#!/usr/bin/env python3
"""Non-authoritative Shadow prediction, evaluation, map, and ROI primitives.

This module consumes only canonical Shadow Observer aggregates and bounded
repository-relative fingerprints.  Its output is telemetry: it never imports
or mutates evidence, approval, reuse, completion, or receipt domains.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
from typing import Any

if __package__:
    from . import click_dependency_cache, click_observer_common
else:  # Executed directly from the bundled hooks directory.
    import click_dependency_cache
    import click_observer_common


SHADOW_INTELLIGENCE_FIELD = "shadow_intelligence"
STATE_VERSION = 1
BASELINE_VERSION = 1
PREDICTION_VERSION = 1
EVALUATION_VERSION = 1
PROJECTION_VERSION = 1
SHADOW_MODE = "shadow"

MAX_STATE_SOURCES = 64
MAX_BASELINE_INPUTS = 1_024
MAX_CHANGED_INPUTS = 64
MAX_DIRECTORY_ENTRIES = 4_096
MAX_DIRECTORY_BYTES = 256 * 1024
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_PROJECTION_INPUTS = 512
MAX_PROJECTION_BYTES = 512 * 1024
MAX_JSON_SAFE_INTEGER = (1 << 53) - 1

DECISIONS = frozenset({"reuse-candidate", "rerun-required", "not-evaluable"})
PREDICTION_REASONS = frozenset(
    {
        "no-baseline",
        "check-binding-changed",
        "environment-binding-changed",
        "executable-binding-changed",
        "host-coverage-binding-changed",
        "input-snapshot-unavailable",
        "observed-input-changed",
        "observed-inputs-unchanged",
    }
)
LIMITATIONS = frozenset({"external-inputs-unmodeled"})
OUTCOMES = frozenset(
    {
        "confirmed-candidate",
        "contradicted-candidate",
        "correct-invalidation",
        "conservative-rerun",
        "not-evaluable",
    }
)
EVALUATION_REASONS = frozenset(
    {
        "candidate-passed",
        "candidate-failed",
        "rerun-failed",
        "rerun-passed",
        "prediction-unavailable",
        "workspace-changed",
        "collector-incomplete",
        "collector-binding-changed",
        "execution-binding-changed",
    }
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_INPUT_KINDS = click_dependency_cache.SHADOW_OBSERVER_INPUT_KINDS
_INPUT_OPERATIONS = click_dependency_cache.SHADOW_OBSERVER_OPERATIONS
_SOURCE_STATUSES = frozenset(
    {"ready", "running", "observed", "passed", "failed", "stale", "unknown"}
)

_BASELINE_BINDING_FIELDS = frozenset(
    {
        "evidence_key",
        "check_digest",
        "mutation_revision",
        "environment_digest",
        "executable_digest",
        "host_coverage_digest",
        "backend_digest",
    }
)
_BASELINE_FIELDS = frozenset(
    {
        "version",
        "mode",
        "binding",
        "observer_digest",
        "inputs",
        "external_input_count",
        "limitations",
        "authoritative",
        "reuse_authorized",
    }
)
_FINGERPRINT_FIELDS = frozenset({"path", "kind", "operations", "identity"})
_PREDICTION_BINDING_FIELDS = frozenset(
    {
        "evidence_key",
        "check_digest",
        "baseline_revision",
        "mutation_revision",
        "environment_digest",
        "executable_digest",
        "host_coverage_digest",
    }
)
_PREDICTION_FIELDS = frozenset(
    {
        "version",
        "mode",
        "binding",
        "decision",
        "reason",
        "changed_inputs",
        "changed_input_count",
        "limitations",
        "baseline_digest",
        "current_input_digest",
        "prepared_at",
        "authoritative",
        "reuse_authorized",
        "prediction_digest",
    }
)
_EVALUATION_FIELDS = frozenset(
    {
        "version",
        "mode",
        "evidence_key",
        "mutation_revision",
        "prediction_digest",
        "outcome",
        "reason",
        "actual_exit_code",
        "workspace_changed",
        "command_duration_ms",
        "observer_overhead_ms",
        "actual_saved_ms",
        "gross_potential_ms",
        "evaluated_at",
        "authoritative",
        "reuse_authorized",
    }
)
_SOURCE_FIELDS = frozenset({"baseline", "prediction", "evaluation"})
_PROJECTION_FIELDS = frozenset(
    {"version", "mode", "generated_at", "task", "summary", "sources", "map"}
)
_PROJECTION_TASK_FIELDS = frozenset(
    {"runtime_mode", "status", "mutation_revision"}
)
_PROJECTION_SUMMARY_FIELDS = frozenset(
    {
        "source_count",
        "candidate_count",
        "evaluated_source_count",
        "contradiction_count",
        "actual_saved_ms",
        "gross_potential_ms",
        "observer_overhead_ms",
        "tracing_slowdown_measured",
    }
)
_PROJECTION_SOURCE_FIELDS = frozenset(
    {
        "id",
        "label",
        "status",
        "observer_status",
        "input_count",
        "visible_input_count",
        "external_input_count",
        "decision",
        "reason",
        "limitations",
        "changed_input_count",
        "outcome",
    }
)
_PROJECTION_MAP_FIELDS = frozenset(
    {
        "nodes",
        "edges",
        "total_input_count",
        "visible_input_count",
        "truncated_input_count",
    }
)
_PROJECTION_NODE_FIELDS = frozenset(
    {"id", "type", "label", "kind", "changed", "status"}
)
_PROJECTION_EDGE_FIELDS = frozenset({"source", "target", "operations"})


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _is_digest(value: Any, *, empty: bool = False) -> bool:
    return bool(
        isinstance(value, str)
        and (empty and value == "" or _DIGEST.fullmatch(value) is not None)
    )


def _is_count(value: Any, *, minimum: int = 0) -> bool:
    return bool(
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= MAX_JSON_SAFE_INTEGER
    )


def _safe_relative_path(value: Any, *, directory: bool = False) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\\" in value
        or len(os.fsencode(value)) > click_dependency_cache.MAX_SHADOW_OBSERVER_PATH_BYTES
    ):
        return False
    candidate = value[:-1] if directory and value.endswith("/") else value
    if not candidate:
        return False
    path = PurePosixPath(candidate)
    return not path.is_absolute() and all(
        part not in {"", ".", ".."} for part in path.parts
    )


def _fingerprint_record_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _FINGERPRINT_FIELDS:
        return False
    path = value.get("path")
    kind = value.get("kind")
    operations = value.get("operations")
    return bool(
        kind in _INPUT_KINDS
        and _safe_relative_path(path, directory=kind == "directory")
        and (kind == "directory") == str(path).endswith("/")
        and isinstance(operations, list)
        and operations == sorted(set(operations))
        and operations
        and all(operation in _INPUT_OPERATIONS for operation in operations)
        and _is_digest(value.get("identity"))
    )


def _inside(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def _stable_lstat(target: Path) -> tuple[os.stat_result | None, str]:
    try:
        before = target.lstat()
    except FileNotFoundError:
        try:
            target.lstat()
        except FileNotFoundError:
            return None, "missing"
        except OSError:
            return None, "error"
        return None, "raced"
    except OSError:
        return None, "error"
    return before, "present"


def _metadata_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(metadata.st_mode),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(getattr(metadata, "st_ino", 0)),
        int(getattr(metadata, "st_dev", 0)),
    )


def _fingerprint_input(
    root: Path, observed: Mapping[str, Any], *, comparison: bool = False
) -> dict[str, Any] | None:
    path = observed.get("path")
    kind = observed.get("kind")
    operations = observed.get("operations")
    if not (
        isinstance(path, str)
        and kind in _INPUT_KINDS
        and isinstance(operations, list)
        and operations == sorted(set(operations))
        and operations
        and all(operation in _INPUT_OPERATIONS for operation in operations)
        and _safe_relative_path(path, directory=kind == "directory")
        and (kind == "directory") == path.endswith("/")
    ):
        return None
    candidate = path[:-1] if kind == "directory" else path
    target = root / candidate
    metadata, state = _stable_lstat(target)
    if kind == "missing":
        if state == "missing":
            identity = _digest({"kind": "missing"})
        elif comparison and metadata is not None and state == "present":
            if stat.S_ISLNK(metadata.st_mode):
                try:
                    resolved = target.resolve(strict=True)
                except (OSError, RuntimeError):
                    return None
                if not _inside(root, resolved):
                    return None
            identity = _digest(
                {"kind": "present", "type": stat.S_IFMT(metadata.st_mode)}
            )
        else:
            return None
        return {
            "path": path,
            "kind": kind,
            "operations": list(operations),
            "identity": identity,
        }
    if metadata is None or state != "present":
        if comparison and state == "missing":
            return {
                "path": path,
                "kind": kind,
                "operations": list(operations),
                "identity": _digest({"kind": "missing"}),
            }
        return None

    hasher = hashlib.sha256()
    hasher.update(kind.encode())
    hasher.update(b"\0")
    hasher.update(str(stat.S_IMODE(metadata.st_mode)).encode())
    before_identity = _metadata_identity(metadata)
    resolved_target: Path | None = None
    resolved_identity: tuple[int, int, int, int, int] | None = None
    if stat.S_ISLNK(metadata.st_mode):
        try:
            link_value = os.readlink(target)
        except OSError:
            return None
        if Path(link_value).is_absolute():
            return None
        try:
            resolved = target.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if not _inside(root, resolved):
            return None
        hasher.update(b"symlink\0")
        hasher.update(os.fsencode(link_value))
        target = resolved
        try:
            metadata = target.stat()
        except OSError:
            return None
        resolved_target = target
        resolved_identity = _metadata_identity(metadata)

    if kind == "directory":
        if not stat.S_ISDIR(metadata.st_mode):
            if comparison:
                return {
                    "path": path,
                    "kind": kind,
                    "operations": list(operations),
                    "identity": _digest(
                        {"kind": "kind-mismatch", "type": stat.S_IFMT(metadata.st_mode)}
                    ),
                }
            return None
        try:
            entries = sorted(
                (os.fsencode(entry.name), int(entry.stat(follow_symlinks=False).st_mode))
                for entry in os.scandir(target)
            )
        except OSError:
            return None
        if len(entries) > MAX_DIRECTORY_ENTRIES:
            return None
        encoded_bytes = sum(len(name) for name, _ in entries)
        if encoded_bytes > MAX_DIRECTORY_BYTES:
            return None
        hasher.update(b"directory\0")
        for name, entry_mode in entries:
            hasher.update(len(name).to_bytes(8, "big"))
            hasher.update(name)
            hasher.update(stat.S_IFMT(entry_mode).to_bytes(8, "big"))
    else:
        if not stat.S_ISREG(metadata.st_mode):
            if comparison:
                return {
                    "path": path,
                    "kind": kind,
                    "operations": list(operations),
                    "identity": _digest(
                        {"kind": "kind-mismatch", "type": stat.S_IFMT(metadata.st_mode)}
                    ),
                }
            return None
        hasher.update(b"file\0")
        try:
            with target.open("rb") as handle:
                while True:
                    chunk = handle.read(128 * 1024)
                    if not chunk:
                        break
                    hasher.update(chunk)
        except OSError:
            return None

    try:
        after = (root / candidate).lstat()
    except OSError:
        return None
    if _metadata_identity(after) != before_identity:
        return None
    if resolved_target is not None and resolved_identity is not None:
        try:
            resolved_after = resolved_target.stat()
        except OSError:
            return None
        if _metadata_identity(resolved_after) != resolved_identity:
            return None
    return {
        "path": path,
        "kind": kind,
        "operations": list(operations),
        "identity": hasher.hexdigest(),
    }


def fingerprint_inputs(
    workspace: Path,
    inputs: Sequence[Mapping[str, Any]],
    *,
    comparison: bool = False,
) -> list[dict[str, Any]] | None:
    if len(inputs) > MAX_BASELINE_INPUTS:
        return None
    try:
        root = workspace.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not root.is_dir():
        return None
    fingerprints: list[dict[str, Any]] = []
    for observed in inputs:
        fingerprint = _fingerprint_input(root, observed, comparison=comparison)
        if fingerprint is None:
            return None
        fingerprints.append(fingerprint)
    fingerprints.sort(key=lambda item: (item["path"], item["kind"]))
    if len({item["path"] for item in fingerprints}) != len(fingerprints):
        return None
    return fingerprints


def baseline_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _BASELINE_FIELDS:
        return False
    binding = value.get("binding")
    inputs = value.get("inputs")
    limitations = value.get("limitations")
    if (
        value.get("version") != BASELINE_VERSION
        or value.get("mode") != SHADOW_MODE
        or value.get("authoritative") is not False
        or value.get("reuse_authorized") is not False
        or not isinstance(binding, dict)
        or set(binding) != _BASELINE_BINDING_FIELDS
        or not _is_digest(binding.get("evidence_key"))
        or not _is_digest(binding.get("check_digest"))
        or not _is_count(binding.get("mutation_revision"))
        or not all(
            _is_digest(binding.get(field))
            for field in (
                "environment_digest",
                "executable_digest",
                "host_coverage_digest",
                "backend_digest",
            )
        )
        or not _is_digest(value.get("observer_digest"))
        or not isinstance(inputs, list)
        or len(inputs) > MAX_BASELINE_INPUTS
        or inputs
        != sorted(inputs, key=lambda item: (str(item.get("path", "")), str(item.get("kind", ""))))
        or len({item.get("path") for item in inputs if isinstance(item, dict)})
        != len(inputs)
        or any(not _fingerprint_record_is_valid(item) for item in inputs)
        or not _is_count(value.get("external_input_count"))
        or not isinstance(limitations, list)
        or limitations != sorted(set(limitations))
        or any(item not in LIMITATIONS for item in limitations)
    ):
        return False
    expected = (
        ["external-inputs-unmodeled"]
        if value["external_input_count"]
        else []
    )
    return limitations == expected


def build_baseline(
    record: Any,
    *,
    workspace: Path,
    environment_digest: str,
    executable_digest: str,
    host_coverage_digest: str,
) -> dict[str, Any] | None:
    if (
        not click_dependency_cache.shadow_observer_record_is_valid(record)
        or record.get("status") != "complete"
        or record.get("process_tree_complete") is not True
        or record.get("unresolved_event_count") != 0
        or not all(
            _is_digest(value)
            for value in (
                environment_digest,
                executable_digest,
                host_coverage_digest,
            )
        )
    ):
        return None
    backend = record.get("backend")
    if not isinstance(backend, dict) or not _is_digest(backend.get("digest")):
        return None
    fingerprints = fingerprint_inputs(workspace, record["inputs"])
    if fingerprints is None:
        return None
    binding = record["binding"]
    baseline = {
        "version": BASELINE_VERSION,
        "mode": SHADOW_MODE,
        "binding": {
            "evidence_key": binding["evidence_key"],
            "check_digest": binding["check_digest"],
            "mutation_revision": binding["mutation_revision"],
            "environment_digest": environment_digest,
            "executable_digest": executable_digest,
            "host_coverage_digest": host_coverage_digest,
            "backend_digest": backend["digest"],
        },
        "observer_digest": click_dependency_cache.shadow_observer_record_digest(record),
        "inputs": fingerprints,
        "external_input_count": record["external_input_count"],
        "limitations": (
            ["external-inputs-unmodeled"]
            if record["external_input_count"]
            else []
        ),
        "authoritative": False,
        "reuse_authorized": False,
    }
    return baseline if baseline_is_valid(baseline) else None


def prediction_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _PREDICTION_FIELDS:
        return False
    binding = value.get("binding")
    changed = value.get("changed_inputs")
    limitations = value.get("limitations")
    if (
        value.get("version") != PREDICTION_VERSION
        or value.get("mode") != SHADOW_MODE
        or value.get("authoritative") is not False
        or value.get("reuse_authorized") is not False
        or not isinstance(binding, dict)
        or set(binding) != _PREDICTION_BINDING_FIELDS
        or not _is_digest(binding.get("evidence_key"))
        or not _is_digest(binding.get("check_digest"))
        or not _is_count(binding.get("baseline_revision"), minimum=-1)
        or not _is_count(binding.get("mutation_revision"))
        or not _is_digest(binding.get("environment_digest"))
        or not _is_digest(binding.get("executable_digest"))
        or not _is_digest(binding.get("host_coverage_digest"))
        or value.get("decision") not in DECISIONS
        or value.get("reason") not in PREDICTION_REASONS
        or not isinstance(changed, list)
        or len(changed) > MAX_CHANGED_INPUTS
        or changed != sorted(set(changed))
        or any(not _safe_relative_path(path, directory=str(path).endswith("/")) for path in changed)
        or not _is_count(value.get("changed_input_count"))
        or value["changed_input_count"] < len(changed)
        or not isinstance(limitations, list)
        or limitations != sorted(set(limitations))
        or any(item not in LIMITATIONS for item in limitations)
        or not _is_digest(value.get("baseline_digest"), empty=True)
        or not _is_digest(value.get("current_input_digest"), empty=True)
        or not _is_count(value.get("prepared_at"), minimum=1)
        or not _is_digest(value.get("prediction_digest"))
    ):
        return False
    unsigned = {key: item for key, item in value.items() if key != "prediction_digest"}
    if value["prediction_digest"] != _digest(unsigned):
        return False
    decision = value["decision"]
    reason = value["reason"]
    if decision == "reuse-candidate":
        return bool(
            reason == "observed-inputs-unchanged"
            and value["baseline_digest"]
            and value["current_input_digest"]
            and value["changed_input_count"] == 0
        )
    if decision == "rerun-required":
        return reason in {
            "check-binding-changed",
            "environment-binding-changed",
            "executable-binding-changed",
            "host-coverage-binding-changed",
            "observed-input-changed",
        }
    return reason in {"no-baseline", "input-snapshot-unavailable"}


def _prediction(
    *,
    evidence_key: str,
    check_digest: str,
    mutation_revision: int,
    baseline_revision: int,
    environment_digest: str,
    executable_digest: str,
    host_coverage_digest: str,
    decision: str,
    reason: str,
    changed_inputs: Sequence[str] = (),
    changed_input_count: int = 0,
    limitations: Sequence[str] = (),
    baseline_digest: str = "",
    current_input_digest: str = "",
    prepared_at: int | None = None,
) -> dict[str, Any]:
    unsigned = {
        "version": PREDICTION_VERSION,
        "mode": SHADOW_MODE,
        "binding": {
            "evidence_key": evidence_key,
            "check_digest": check_digest,
            "baseline_revision": baseline_revision,
            "mutation_revision": mutation_revision,
            "environment_digest": environment_digest,
            "executable_digest": executable_digest,
            "host_coverage_digest": host_coverage_digest,
        },
        "decision": decision,
        "reason": reason,
        "changed_inputs": sorted(set(changed_inputs))[:MAX_CHANGED_INPUTS],
        "changed_input_count": changed_input_count,
        "limitations": sorted(set(limitations)),
        "baseline_digest": baseline_digest,
        "current_input_digest": current_input_digest,
        "prepared_at": max(1, int(time.time()) if prepared_at is None else prepared_at),
        "authoritative": False,
        "reuse_authorized": False,
    }
    result = {**unsigned, "prediction_digest": _digest(unsigned)}
    if not prediction_is_valid(result):
        raise ValueError("invalid Shadow prediction")
    return result


def predict(
    baseline: Any,
    *,
    workspace: Path,
    evidence_key: str,
    check_digest: str,
    mutation_revision: int,
    environment_digest: str,
    executable_digest: str,
    host_coverage_digest: str,
    prepared_at: int | None = None,
) -> dict[str, Any]:
    common = {
        "evidence_key": evidence_key,
        "check_digest": check_digest,
        "mutation_revision": mutation_revision,
        "environment_digest": environment_digest,
        "executable_digest": executable_digest,
        "host_coverage_digest": host_coverage_digest,
        "prepared_at": prepared_at,
    }
    if not baseline_is_valid(baseline):
        return _prediction(
            **common,
            baseline_revision=-1,
            decision="not-evaluable",
            reason="no-baseline",
        )
    binding = baseline["binding"]
    baseline_digest = _digest(baseline)
    base = {
        **common,
        "baseline_revision": binding["mutation_revision"],
        "limitations": baseline["limitations"],
        "baseline_digest": baseline_digest,
    }
    comparisons = (
        ("check_digest", check_digest, "check-binding-changed"),
        ("environment_digest", environment_digest, "environment-binding-changed"),
        ("executable_digest", executable_digest, "executable-binding-changed"),
        ("host_coverage_digest", host_coverage_digest, "host-coverage-binding-changed"),
    )
    for field, current, reason in comparisons:
        if binding.get(field) != current:
            return _prediction(
                **base,
                decision="rerun-required",
                reason=reason,
            )
    current = fingerprint_inputs(workspace, baseline["inputs"], comparison=True)
    if current is None:
        return _prediction(
            **base,
            decision="not-evaluable",
            reason="input-snapshot-unavailable",
        )
    current_digest = _digest({"inputs": current})
    previous_by_path = {item["path"]: item["identity"] for item in baseline["inputs"]}
    changed = [
        item["path"]
        for item in current
        if previous_by_path.get(item["path"]) != item["identity"]
    ]
    return _prediction(
        **base,
        decision="rerun-required" if changed else "reuse-candidate",
        reason="observed-input-changed" if changed else "observed-inputs-unchanged",
        changed_inputs=changed,
        changed_input_count=len(changed),
        current_input_digest=current_digest,
    )


def evaluation_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _EVALUATION_FIELDS:
        return False
    if (
        value.get("version") != EVALUATION_VERSION
        or value.get("mode") != SHADOW_MODE
        or value.get("authoritative") is not False
        or value.get("reuse_authorized") is not False
        or not _is_digest(value.get("evidence_key"))
        or not _is_count(value.get("mutation_revision"))
        or not _is_digest(value.get("prediction_digest"))
        or value.get("outcome") not in OUTCOMES
        or value.get("reason") not in EVALUATION_REASONS
        or not isinstance(value.get("actual_exit_code"), int)
        or isinstance(value.get("actual_exit_code"), bool)
        or not isinstance(value.get("workspace_changed"), bool)
        or not all(
            _is_count(value.get(field))
            for field in (
                "command_duration_ms",
                "observer_overhead_ms",
                "actual_saved_ms",
                "gross_potential_ms",
            )
        )
        or value["observer_overhead_ms"] > value["command_duration_ms"]
        or value["actual_saved_ms"] != 0
        or not _is_count(value.get("evaluated_at"), minimum=1)
    ):
        return False
    expected = {
        "confirmed-candidate": "candidate-passed",
        "contradicted-candidate": "candidate-failed",
        "correct-invalidation": "rerun-failed",
        "conservative-rerun": "rerun-passed",
    }
    if value["outcome"] in expected and value["reason"] != expected[value["outcome"]]:
        return False
    if value["outcome"] == "confirmed-candidate":
        return value["gross_potential_ms"] == value["command_duration_ms"]
    return value["gross_potential_ms"] == 0


def evaluate(
    prediction: Any,
    baseline: Any,
    record: Any,
    *,
    actual_exit_code: int,
    workspace_changed: bool,
    actual_environment_digest: str | None = None,
    actual_executable_digest: str | None = None,
    actual_host_coverage_digest: str | None = None,
    evaluated_at: int | None = None,
) -> dict[str, Any] | None:
    if not prediction_is_valid(prediction):
        return None
    binding = prediction["binding"]
    if not click_dependency_cache.shadow_observer_record_is_valid(record):
        return None
    observer_binding = record["binding"]
    if (
        observer_binding["evidence_key"] != binding["evidence_key"]
        or observer_binding["check_digest"] != binding["check_digest"]
        or observer_binding["mutation_revision"] != binding["mutation_revision"]
    ):
        return None
    reason = "prediction-unavailable"
    outcome = "not-evaluable"
    if workspace_changed:
        reason = "workspace-changed"
    elif record["status"] != "complete" or not record["process_tree_complete"]:
        reason = "collector-incomplete"
    elif baseline_is_valid(baseline) and (
        not isinstance(record.get("backend"), dict)
        or record["backend"].get("digest") != baseline["binding"]["backend_digest"]
    ):
        reason = "collector-binding-changed"
    elif (
        (
            actual_environment_digest is not None
            and actual_environment_digest
            != binding["environment_digest"]
        )
        or (
            actual_executable_digest is not None
            and actual_executable_digest
            != binding["executable_digest"]
        )
        or (
            actual_host_coverage_digest is not None
            and actual_host_coverage_digest
            != binding["host_coverage_digest"]
        )
    ):
        reason = "execution-binding-changed"
    elif prediction["decision"] == "reuse-candidate":
        if actual_exit_code == 0:
            outcome, reason = "confirmed-candidate", "candidate-passed"
        else:
            outcome, reason = "contradicted-candidate", "candidate-failed"
    elif prediction["decision"] == "rerun-required":
        if actual_exit_code == 0:
            outcome, reason = "conservative-rerun", "rerun-passed"
        else:
            outcome, reason = "correct-invalidation", "rerun-failed"
    duration = int(record["command_duration_ms"])
    result = {
        "version": EVALUATION_VERSION,
        "mode": SHADOW_MODE,
        "evidence_key": binding["evidence_key"],
        "mutation_revision": binding["mutation_revision"],
        "prediction_digest": prediction["prediction_digest"],
        "outcome": outcome,
        "reason": reason,
        "actual_exit_code": actual_exit_code,
        "workspace_changed": workspace_changed,
        "command_duration_ms": duration,
        "observer_overhead_ms": int(record["observer_overhead_ms"]),
        "actual_saved_ms": 0,
        "gross_potential_ms": duration if outcome == "confirmed-candidate" else 0,
        "evaluated_at": max(1, int(time.time()) if evaluated_at is None else evaluated_at),
        "authoritative": False,
        "reuse_authorized": False,
    }
    return result if evaluation_is_valid(result) else None


def fresh_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "sources": {}}


def state_is_valid(value: Any) -> bool:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "sources"}
        or value.get("version") != STATE_VERSION
        or not isinstance(value.get("sources"), dict)
        or len(value["sources"]) > MAX_STATE_SOURCES
        or len(_canonical_bytes(value)) > MAX_STATE_BYTES
    ):
        return False
    for key, source in value["sources"].items():
        if (
            not _is_digest(key)
            or not isinstance(source, dict)
            or set(source) != _SOURCE_FIELDS
            or not isinstance(source.get("baseline"), dict)
            or not isinstance(source.get("prediction"), dict)
            or not isinstance(source.get("evaluation"), dict)
            or source["baseline"]
            and (
                not baseline_is_valid(source["baseline"])
                or source["baseline"]["binding"]["evidence_key"] != key
            )
            or source["prediction"]
            and (
                not prediction_is_valid(source["prediction"])
                or source["prediction"]["binding"]["evidence_key"] != key
            )
            or source["evaluation"]
            and (
                not evaluation_is_valid(source["evaluation"])
                or source["evaluation"]["evidence_key"] != key
            )
        ):
            return False
    return True


def _detached_state(verification: Any) -> dict[str, Any]:
    if not isinstance(verification, dict):
        return fresh_state()
    value = verification.get(SHADOW_INTELLIGENCE_FIELD)
    if not state_is_valid(value):
        return fresh_state()
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))


def prepare_predictions(
    verification: dict[str, Any],
    *,
    workspace: Path,
    source_contexts: Mapping[str, Mapping[str, Any]],
    mutation_revision: int,
    prepared_at: int | None = None,
) -> int:
    """Store immutable pre-run predictions without touching authority state."""
    state = _detached_state(verification)
    stored = 0
    for evidence_key, context in sorted(source_contexts.items()):
        if not _is_digest(evidence_key):
            continue
        existing = state["sources"].get(evidence_key)
        if existing is None and len(state["sources"]) >= MAX_STATE_SOURCES:
            continue
        entry = (
            dict(existing)
            if isinstance(existing, dict) and set(existing) == _SOURCE_FIELDS
            else {"baseline": {}, "prediction": {}, "evaluation": {}}
        )
        prediction = predict(
            entry.get("baseline"),
            workspace=workspace,
            evidence_key=evidence_key,
            check_digest=str(context.get("check_digest", "")),
            mutation_revision=mutation_revision,
            environment_digest=str(context.get("environment_digest", "")),
            executable_digest=str(context.get("executable_digest", "")),
            host_coverage_digest=str(context.get("host_coverage_digest", "")),
            prepared_at=prepared_at,
        )
        entry["prediction"] = prediction
        entry["evaluation"] = {}
        state["sources"][evidence_key] = entry
        stored += 1
    if state_is_valid(state):
        verification[SHADOW_INTELLIGENCE_FIELD] = state
        return stored
    return 0


def record_run(
    verification: dict[str, Any],
    *,
    observer_records: Mapping[str, Any],
    baselines: Mapping[str, Any],
    source_exit_codes: Mapping[str, int],
    source_contexts: Mapping[str, Mapping[str, Any]],
    workspace_changed: bool,
    evaluated_at: int | None = None,
) -> int:
    """Evaluate the prepared prediction, then advance only successful baselines."""
    state = _detached_state(verification)
    stored = 0
    for evidence_key, record in sorted(observer_records.items()):
        entry = state["sources"].get(evidence_key)
        if not isinstance(entry, dict) or set(entry) != _SOURCE_FIELDS:
            continue
        exit_code = source_exit_codes.get(evidence_key)
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            continue
        previous_baseline = entry.get("baseline", {})
        context = source_contexts.get(evidence_key)
        context = context if isinstance(context, Mapping) else {}
        result = evaluate(
            entry.get("prediction"),
            previous_baseline,
            record,
            actual_exit_code=exit_code,
            workspace_changed=workspace_changed,
            actual_environment_digest=str(context.get("environment_digest", "")),
            actual_executable_digest=str(context.get("executable_digest", "")),
            actual_host_coverage_digest=str(
                context.get("host_coverage_digest", "")
            ),
            evaluated_at=evaluated_at,
        )
        if result is not None:
            entry["evaluation"] = result
        next_baseline = baselines.get(evidence_key)
        if exit_code == 0 and not workspace_changed and baseline_is_valid(next_baseline):
            entry["baseline"] = next_baseline
        state["sources"][evidence_key] = entry
        stored += 1
    if state_is_valid(state):
        verification[SHADOW_INTELLIGENCE_FIELD] = state
        return stored
    return 0


def _source_status(value: Any) -> str:
    status = value.get("status") if isinstance(value, dict) else "unknown"
    return status if status in _SOURCE_STATUSES else "unknown"


def dashboard_projection(state: Any, *, generated_at: int | None = None) -> dict[str, Any]:
    """Return the only sanitized state shape exposed to the local dashboard."""
    raw_state = state if isinstance(state, dict) else {}
    verification = raw_state.get("verification")
    verification = verification if isinstance(verification, dict) else {}
    evidence_state = raw_state.get("evidence_state")
    evidence_sources = (
        evidence_state.get("sources", {}) if isinstance(evidence_state, dict) else {}
    )
    evidence_sources = evidence_sources if isinstance(evidence_sources, dict) else {}
    observer_records = click_observer_common.records_from_verification(verification)
    intelligence = _detached_state(verification)

    source_keys = sorted(
        set(key for key in evidence_sources if _is_digest(key))
        | set(observer_records)
        | set(intelligence["sources"])
    )[:MAX_STATE_SOURCES]
    sources: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    input_nodes: dict[tuple[str, str], str] = {}
    all_input_nodes: set[tuple[str, str]] = set()
    rendered_inputs = 0
    gross_potential_ms = 0
    observer_overhead_ms = 0
    contradiction_count = 0
    evaluated_count = 0
    candidate_count = 0

    for index, key in enumerate(source_keys, start=1):
        label = f"Check {index}"
        source_id = f"source:{key[:16]}"
        evidence_source = evidence_sources.get(key)
        observer = observer_records.get(key, {})
        intel = intelligence["sources"].get(key, {})
        prediction = intel.get("prediction", {}) if isinstance(intel, dict) else {}
        evaluation = intel.get("evaluation", {}) if isinstance(intel, dict) else {}
        decision = prediction.get("decision", "not-evaluable") if prediction_is_valid(prediction) else "not-evaluable"
        reason = prediction.get("reason", "no-baseline") if prediction_is_valid(prediction) else "no-baseline"
        outcome = evaluation.get("outcome", "not-evaluable") if evaluation_is_valid(evaluation) else "not-evaluable"
        limitations = list(prediction.get("limitations", [])) if prediction_is_valid(prediction) else []
        changed = set(prediction.get("changed_inputs", [])) if prediction_is_valid(prediction) else set()
        record_inputs = observer.get("inputs", []) if click_dependency_cache.shadow_observer_record_is_valid(observer) else []
        source_input_count = 0
        for observed in record_inputs:
            node_key = (observed["path"], observed["kind"])
            all_input_nodes.add(node_key)
            input_id = input_nodes.get(node_key)
            if input_id is None:
                if rendered_inputs >= MAX_PROJECTION_INPUTS:
                    continue
                input_id = f"input:{hashlib.sha256((observed['kind'] + chr(0) + observed['path']).encode()).hexdigest()[:24]}"
                input_nodes[node_key] = input_id
                nodes.append(
                    {
                        "id": input_id,
                        "type": "input",
                        "label": observed["path"],
                        "kind": observed["kind"],
                        "changed": observed["path"] in changed,
                        "status": "changed" if observed["path"] in changed else "observed",
                    }
                )
                rendered_inputs += 1
            edges.append(
                {
                    "source": source_id,
                    "target": input_id,
                    "operations": list(observed["operations"]),
                }
            )
            source_input_count += 1
        observer_status = (
            str(observer.get("status"))
            if click_dependency_cache.shadow_observer_record_is_valid(observer)
            else "unavailable"
        )
        status = _source_status(evidence_source)
        nodes.append(
            {
                "id": source_id,
                "type": "source",
                "label": label,
                "kind": "argv",
                "changed": False,
                "status": status,
            }
        )
        if decision == "reuse-candidate":
            candidate_count += 1
        if evaluation_is_valid(evaluation):
            evaluated_count += 1
            gross_potential_ms += int(evaluation["gross_potential_ms"])
            observer_overhead_ms += int(evaluation["observer_overhead_ms"])
            contradiction_count += int(outcome == "contradicted-candidate")
        sources.append(
            {
                "id": source_id,
                "label": label,
                "status": status,
                "observer_status": observer_status,
                "input_count": len(record_inputs),
                "visible_input_count": source_input_count,
                "external_input_count": int(observer.get("external_input_count", 0)) if click_dependency_cache.shadow_observer_record_is_valid(observer) else 0,
                "decision": decision,
                "reason": reason,
                "limitations": limitations,
                "changed_input_count": int(prediction.get("changed_input_count", 0)) if prediction_is_valid(prediction) else 0,
                "outcome": outcome,
            }
        )

    nodes.sort(key=lambda item: (item["type"], item["label"], item["id"]))
    edges.sort(key=lambda item: (item["source"], item["target"], item["operations"]))
    runtime_mode = raw_state.get("runtime_mode")
    if runtime_mode not in {"evidence", "guarded"}:
        runtime_mode = "unknown"
    revision = verification.get("mutation_revision", 0)
    if not _is_count(revision):
        revision = 0
    total_inputs = len(all_input_nodes)
    projection = {
        "version": PROJECTION_VERSION,
        "mode": SHADOW_MODE,
        "generated_at": max(1, int(time.time()) if generated_at is None else generated_at),
        "task": {
            "runtime_mode": runtime_mode,
            "status": str(raw_state.get("status", "unknown"))[:32],
            "mutation_revision": revision,
        },
        "summary": {
            "source_count": len(sources),
            "candidate_count": candidate_count,
            "evaluated_source_count": evaluated_count,
            "contradiction_count": contradiction_count,
            "actual_saved_ms": 0,
            "gross_potential_ms": min(gross_potential_ms, MAX_JSON_SAFE_INTEGER),
            "observer_overhead_ms": min(observer_overhead_ms, MAX_JSON_SAFE_INTEGER),
            "tracing_slowdown_measured": False,
        },
        "sources": sources,
        "map": {
            "nodes": nodes,
            "edges": edges,
            "total_input_count": total_inputs,
            "visible_input_count": rendered_inputs,
            "truncated_input_count": max(0, total_inputs - rendered_inputs),
        },
    }
    if len(_canonical_bytes(projection)) > MAX_PROJECTION_BYTES:
        projection["map"] = {
            "nodes": [node for node in nodes if node["type"] == "source"],
            "edges": [],
            "total_input_count": total_inputs,
            "visible_input_count": 0,
            "truncated_input_count": total_inputs,
        }
    return projection


def projection_is_valid(value: Any) -> bool:
    if (
        not isinstance(value, dict)
        or set(value) != _PROJECTION_FIELDS
        or value.get("version") != PROJECTION_VERSION
        or value.get("mode") != SHADOW_MODE
        or not _is_count(value.get("generated_at"), minimum=1)
        or len(_canonical_bytes(value)) > MAX_PROJECTION_BYTES
    ):
        return False
    task = value.get("task")
    summary = value.get("summary")
    sources = value.get("sources")
    map_value = value.get("map")
    if (
        not isinstance(task, dict)
        or set(task) != _PROJECTION_TASK_FIELDS
        or task.get("runtime_mode") not in {"evidence", "guarded", "unknown"}
        or not isinstance(task.get("status"), str)
        or len(task["status"]) > 32
        or not _is_count(task.get("mutation_revision"))
        or not isinstance(summary, dict)
        or set(summary) != _PROJECTION_SUMMARY_FIELDS
        or any(
            not _is_count(summary.get(field))
            for field in _PROJECTION_SUMMARY_FIELDS
            if field != "tracing_slowdown_measured"
        )
        or summary.get("actual_saved_ms") != 0
        or summary.get("tracing_slowdown_measured") is not False
        or not isinstance(sources, list)
        or len(sources) > MAX_STATE_SOURCES
        or not isinstance(map_value, dict)
        or set(map_value) != _PROJECTION_MAP_FIELDS
    ):
        return False
    for source in sources:
        if (
            not isinstance(source, dict)
            or set(source) != _PROJECTION_SOURCE_FIELDS
            or not isinstance(source.get("id"), str)
            or not re.fullmatch(r"source:[0-9a-f]{16}", source["id"])
            or not isinstance(source.get("label"), str)
            or len(source["label"]) > 32
            or source.get("status") not in _SOURCE_STATUSES
            or source.get("observer_status")
            not in click_dependency_cache.SHADOW_OBSERVER_STATUSES
            or source.get("decision") not in DECISIONS
            or source.get("reason") not in PREDICTION_REASONS
            or source.get("outcome") not in OUTCOMES
            or not isinstance(source.get("limitations"), list)
            or source["limitations"] != sorted(set(source["limitations"]))
            or any(item not in LIMITATIONS for item in source["limitations"])
            or any(
                not _is_count(source.get(field))
                for field in (
                    "input_count",
                    "visible_input_count",
                    "external_input_count",
                    "changed_input_count",
                )
            )
        ):
            return False
    nodes = map_value.get("nodes")
    edges = map_value.get("edges")
    if (
        not isinstance(nodes, list)
        or len(nodes) > MAX_PROJECTION_INPUTS + MAX_STATE_SOURCES
        or not isinstance(edges, list)
        or len(edges) > MAX_PROJECTION_INPUTS * MAX_STATE_SOURCES
        or any(
            not _is_count(map_value.get(field))
            for field in (
                "total_input_count",
                "visible_input_count",
                "truncated_input_count",
            )
        )
        or map_value["visible_input_count"] > MAX_PROJECTION_INPUTS
        or map_value["visible_input_count"] + map_value["truncated_input_count"]
        != map_value["total_input_count"]
    ):
        return False
    node_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or set(node) != _PROJECTION_NODE_FIELDS:
            return False
        node_id = node.get("id")
        node_type = node.get("type")
        label = node.get("label")
        if (
            not isinstance(node_id, str)
            or node_id in node_ids
            or node_type not in {"source", "input"}
            or not isinstance(label, str)
            or not label
            or not isinstance(node.get("changed"), bool)
        ):
            return False
        node_ids.add(node_id)
        if node_type == "source":
            if (
                re.fullmatch(r"source:[0-9a-f]{16}", node_id) is None
                or node.get("kind") != "argv"
                or node.get("status") not in _SOURCE_STATUSES
            ):
                return False
        elif (
            re.fullmatch(r"input:[0-9a-f]{24}", node_id) is None
            or node.get("kind") not in _INPUT_KINDS
            or not _safe_relative_path(label, directory=node.get("kind") == "directory")
            or node.get("status") not in {"observed", "changed"}
        ):
            return False
    for edge in edges:
        if (
            not isinstance(edge, dict)
            or set(edge) != _PROJECTION_EDGE_FIELDS
            or edge.get("source") not in node_ids
            or edge.get("target") not in node_ids
            or not str(edge.get("source", "")).startswith("source:")
            or not str(edge.get("target", "")).startswith("input:")
            or not isinstance(edge.get("operations"), list)
            or edge["operations"] != sorted(set(edge["operations"]))
            or not edge["operations"]
            or any(operation not in _INPUT_OPERATIONS for operation in edge["operations"])
        ):
            return False
    return summary["source_count"] == len(sources)


def advisory(prediction: Any) -> str:
    if not prediction_is_valid(prediction):
        return "[Click shadow prediction] not-evaluable (invalid prediction)"
    return (
        f"[Click shadow prediction] {prediction['decision']} "
        f"({prediction['reason']}); changed={prediction['changed_input_count']}"
    )
