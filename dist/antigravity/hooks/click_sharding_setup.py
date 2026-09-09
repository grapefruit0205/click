"""Stateful bootstrap for supported Python, Vitest, and Jest sharding.

The public gate exposes ``sharding init|status|refresh``.  This module owns the
project-local setup state outside the project, proposal application without
index mutation, commit confirmation, and the candidate parent/child bootstrap
run.  It never creates Click verification evidence; only the ordinary one-use
verification runner can do that.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
import difflib
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Iterator

try:  # Use the native nonblocking file lock on each supported platform.
    import fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised on Windows hosts.
    fcntl = None  # type: ignore[assignment]
try:
    import msvcrt  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised on POSIX hosts.
    msvcrt = None  # type: ignore[assignment]

if __package__:
    from . import click_import_bootstrap
else:
    import click_import_bootstrap

(
    auto_sharding,
    change_policy,
    dependency_cache,
    evidence,
    evidence_shards,
    observer_control,
    state_runtime,
    inventory,
    verification_adapters,
) = click_import_bootstrap.load_siblings(
    __package__,
    "click_auto_sharding",
    "click_change_policy",
    "click_dependency_cache",
    "click_evidence",
    "click_evidence_shards",
    "click_observer_control",
    "click_state",
    "click_test_inventory",
    "click_verification_adapters",
)


VERSION = 1
BASELINE_EVIDENCE_ID = "E_AUTO_SHARDING_BASELINE"
POLICY_PATHS = (
    ".click/evidence-shards.json",
    ".click/evidence-dependencies.json",
)
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_REVIEW_PREVIEW_BYTES = 16 * 1024
CONTRACT_ID = re.compile(r"^ctr_[0-9a-f]{32}$")
AUTHORITY_ID = re.compile(r"^(?:ctr|evs)_[0-9a-f]{32}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
RUNTIME_MODES = frozenset({"guarded", "evidence"})
_PROCESS_LOCK_GUARD = threading.Lock()
_PROCESS_LOCKS: set[str] = set()
DEFAULT_MIN_PARENT_MS = 250.0
DEFAULT_MIN_AVOIDABLE_MS = 100.0
DEFAULT_MANAGEMENT_RESERVE_MS = 25.0
COST_ENVIRONMENT = {
    "min_parent_ms": "CLICK_SHARDING_MIN_PARENT_MS",
    "min_avoidable_ms": "CLICK_SHARDING_MIN_AVOIDABLE_MS",
    "management_reserve_ms": "CLICK_SHARDING_MANAGEMENT_RESERVE_MS",
}
PERSISTED_STATUSES = frozenset(
    {
        "application-ready",
        "approval-required",
        "review-required",
        "whole-suite-preferred",
        "unsupported",
        "blocked",
        "commit-required",
        "baseline-required",
    }
)
LEGACY_STATE_FIELDS = frozenset(
    {
        "version",
        "project_root",
        "project_key",
        "status",
        "reasons",
        "parent_argv",
        "cwd",
        "analysis_contract_id",
        "application_contract_id",
        "artifact_directory",
        "artifact_digest",
        "workspace_digest",
        "applied_workspace_digest",
        "inventory_digest",
        "runtime",
        "discovery_files",
        "policy_files",
        "review",
        "bootstrap",
        "generation",
        "created_at",
        "updated_at",
    }
)
STATE_FIELDS = LEGACY_STATE_FIELDS | {
    "authority_mode",
    "test_structure_digest",
    "cost_policy",
    "lineage",
}


class SetupError(ValueError):
    """A stable setup failure that never carries user file contents."""


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def _project_key(root: Path) -> str:
    canonical = root.resolve(strict=True)
    return hashlib.sha256(os.path.normcase(str(canonical)).encode()).hexdigest()


def _setup_root() -> Path:
    return state_runtime.state_root().parent / "sharding-setup"


def _secure_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        raise SetupError("setup-storage-unavailable")
    path.chmod(0o700)


def _restrict_file_permissions(descriptor: int, path: str | Path) -> None:
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, 0o600)
    else:  # Windows exposes path-based permission compatibility only.
        Path(path).chmod(0o600)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def state_path(project: Path) -> Path:
    root = inventory.project_root(project)
    return _setup_root() / f"{_project_key(root)}.json"


@contextmanager
def _project_lock(root: Path) -> Iterator[None]:
    root = root.resolve(strict=True)
    directory = _setup_root()
    _secure_directory(directory)
    project_key = _project_key(root)
    lock_path = directory / f"{project_key}.lock"
    flags = os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    with _PROCESS_LOCK_GUARD:
        if project_key in _PROCESS_LOCKS:
            raise SetupError("concurrent-initialization")
        _PROCESS_LOCKS.add(project_key)
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise SetupError("setup-lock-unavailable") from exc
        if fcntl is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SetupError("concurrent-initialization") from exc
        elif msvcrt is not None:  # pragma: no cover - Windows-only branch.
            try:
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise SetupError("concurrent-initialization") from exc
        else:
            raise SetupError("setup-lock-unavailable")
        try:
            yield
        finally:
            if msvcrt is not None and fcntl is None:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with _PROCESS_LOCK_GUARD:
            _PROCESS_LOCKS.discard(project_key)


def _state_is_valid(value: Any, root: Path) -> bool:
    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    if (
        not isinstance(value, dict)
        or frozenset(value) not in {STATE_FIELDS, LEGACY_STATE_FIELDS}
        or value.get("version") != VERSION
        or value.get("project_root") != str(root)
        or value.get("project_key") != _project_key(root)
        or value.get("status") not in PERSISTED_STATUSES
        or not isinstance(value.get("reasons"), list)
        or any(
            not isinstance(reason, str) or not re.fullmatch(r"[a-z0-9-]+", reason)
            for reason in value["reasons"]
        )
        or not isinstance(value.get("parent_argv"), list)
        or any(not isinstance(item, str) or not item for item in value["parent_argv"])
        or value.get("cwd") != "."
        or not isinstance(value.get("analysis_contract_id"), str)
        or value["analysis_contract_id"]
        and AUTHORITY_ID.fullmatch(value["analysis_contract_id"]) is None
        or not isinstance(value.get("application_contract_id"), str)
        or value["application_contract_id"]
        and AUTHORITY_ID.fullmatch(value["application_contract_id"]) is None
        or not isinstance(value.get("generation"), int)
        or isinstance(value.get("generation"), bool)
        or value["generation"] < 1
        or any(
            not isinstance(value.get(field), int)
            or isinstance(value.get(field), bool)
            or value[field] <= 0
            for field in ("created_at", "updated_at")
        )
        or len(_canonical(value)) > MAX_STATE_BYTES
    ):
        return False
    if frozenset(value) == STATE_FIELDS:
        if (
            value.get("authority_mode") not in RUNTIME_MODES
            or not isinstance(value.get("test_structure_digest"), str)
            or value["test_structure_digest"]
            and DIGEST.fullmatch(value["test_structure_digest"]) is None
            or not isinstance(value.get("cost_policy"), dict)
            or not isinstance(value.get("lineage"), dict)
        ):
            return False
    for field in (
        "artifact_digest",
        "workspace_digest",
        "applied_workspace_digest",
        "inventory_digest",
    ):
        item = value.get(field)
        if not isinstance(item, str) or item and DIGEST.fullmatch(item) is None:
            return False
    if not isinstance(value.get("artifact_directory"), str):
        return False
    if not isinstance(value.get("runtime"), dict):
        return False
    if (
        not isinstance(value.get("discovery_files"), list)
        or value["discovery_files"] != sorted(set(value["discovery_files"]))
        or any(not isinstance(item, str) or not item for item in value["discovery_files"])
    ):
        return False
    policies = value.get("policy_files")
    if not isinstance(policies, dict) or set(policies) not in (set(), set(POLICY_PATHS)):
        return False
    for relative, metadata in policies.items():
        if (
            relative not in POLICY_PATHS
            or not isinstance(metadata, dict)
            or set(metadata) != {"digest", "bytes", "existed_at_analysis"}
            or not isinstance(metadata.get("digest"), str)
            or DIGEST.fullmatch(metadata["digest"]) is None
            or not isinstance(metadata.get("bytes"), int)
            or isinstance(metadata.get("bytes"), bool)
            or metadata["bytes"] <= 0
            or not isinstance(metadata.get("existed_at_analysis"), bool)
        ):
            return False
    return isinstance(value.get("review"), dict) and isinstance(
        value.get("bootstrap"), dict
    )


def _upgrade_state(value: dict[str, Any]) -> dict[str, Any]:
    if frozenset(value) == STATE_FIELDS:
        return value
    upgraded = dict(value)
    upgraded.update(
        authority_mode="guarded",
        test_structure_digest="",
        cost_policy={},
        lineage={},
    )
    return upgraded


def _read_state(root: Path) -> dict[str, Any] | None:
    path = _setup_root() / f"{_project_key(root)}.json"
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise SetupError("setup-state-invalid")
        if metadata.st_size > MAX_STATE_BYTES:
            raise SetupError("setup-state-invalid")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(MAX_STATE_BYTES + 1)
        value = json.loads(raw)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        raise SetupError("setup-state-invalid") from exc
    if not _state_is_valid(value, root):
        raise SetupError("setup-state-invalid")
    return _upgrade_state(value)


def _write_state(root: Path, value: dict[str, Any]) -> None:
    now = int(time.time()) or 1
    value["updated_at"] = now
    if not _state_is_valid(value, root):
        raise SetupError("setup-state-invalid")
    directory = _setup_root()
    _secure_directory(directory)
    target = directory / f"{_project_key(root)}.json"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=directory
    )
    try:
        _restrict_file_permissions(descriptor, temporary)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(_canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        _fsync_directory(directory)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        Path(temporary).unlink(missing_ok=True)
        raise


def _artifact_payload(proposal: dict[str, Any]) -> dict[str, bytes]:
    metadata = {
        key: item
        for key, item in proposal.items()
        if key not in {"analysis", "proposals", "timing_samples"}
    }
    payload = {
        "analysis.json": json.dumps(
            proposal["analysis"], indent=2, sort_keys=True
        ).encode()
        + b"\n",
        "proposal.json": json.dumps(metadata, indent=2, sort_keys=True).encode()
        + b"\n",
    }
    for relative in POLICY_PATHS:
        value = proposal.get("proposals", {}).get(relative)
        if value is not None:
            payload[relative] = json.dumps(value, indent=2, sort_keys=True).encode() + b"\n"
    return payload


def _artifact_digest(payload: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(payload):
        digest.update(name.encode() + b"\0" + hashlib.sha256(payload[name]).digest())
    return digest.hexdigest()


def _persist_artifact(
    root: Path, proposal: dict[str, Any]
) -> tuple[Path, str, dict[str, dict[str, Any]]]:
    payload = _artifact_payload(proposal)
    if not all(relative in payload for relative in POLICY_PATHS):
        raise SetupError("proposal-artifact-incomplete")
    if sum(len(content) for content in payload.values()) > MAX_ARTIFACT_BYTES:
        raise SetupError("proposal-artifact-limit")
    digest = _artifact_digest(payload)
    base = _setup_root() / "artifacts" / _project_key(root)
    _secure_directory(base)
    target = base / digest
    if target.exists():
        if _read_artifact_directory(target, digest) != payload:
            raise SetupError("proposal-artifact-invalid")
    else:
        temporary = Path(tempfile.mkdtemp(prefix=".proposal-", dir=base))
        temporary.chmod(0o700)
        try:
            for relative, content in payload.items():
                path = temporary / relative
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with path.open("xb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                path.chmod(0o600)
            os.replace(temporary, target)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    policies = {
        relative: {
            "digest": _digest_bytes(payload[relative]),
            "bytes": len(payload[relative]),
            "existed_at_analysis": bool(
                (root / relative).exists() or (root / relative).is_symlink()
            ),
        }
        for relative in POLICY_PATHS
    }
    return target, digest, policies


def _read_artifact_directory(path: Path, expected_digest: str) -> dict[str, bytes]:
    base = (_setup_root() / "artifacts").resolve()
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(base)
        metadata = resolved.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise SetupError("proposal-artifact-invalid")
        payload: dict[str, bytes] = {}
        total = 0
        for relative in ("analysis.json", "proposal.json", *POLICY_PATHS):
            target = resolved / relative
            info = target.lstat()
            total += info.st_size
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISREG(info.st_mode)
                or info.st_size > MAX_ARTIFACT_BYTES
                or total > MAX_ARTIFACT_BYTES
            ):
                raise SetupError("proposal-artifact-invalid")
            with target.open("rb") as stream:
                content = stream.read(MAX_ARTIFACT_BYTES - (total - info.st_size) + 1)
            if len(content) != info.st_size or total > MAX_ARTIFACT_BYTES:
                raise SetupError("proposal-artifact-invalid")
            payload[relative] = content
    except (OSError, ValueError) as exc:
        raise SetupError("proposal-artifact-invalid") from exc
    if _artifact_digest(payload) != expected_digest:
        raise SetupError("proposal-artifact-invalid")
    return payload


def _artifact(state: dict[str, Any]) -> dict[str, bytes]:
    directory = Path(state["artifact_directory"])
    return _read_artifact_directory(directory, state["artifact_digest"])


def _json_artifact(payload: dict[str, bytes], name: str) -> dict[str, Any]:
    try:
        value = json.loads(payload[name])
    except (KeyError, ValueError, UnicodeError) as exc:
        raise SetupError("proposal-artifact-invalid") from exc
    if not isinstance(value, dict):
        raise SetupError("proposal-artifact-invalid")
    return value


def _test_structure_digest(
    root: Path, files: list[str], *, framework: str = ""
) -> str:
    """Fingerprint discovery structure for the adapter's supported test files."""
    if framework in {"vitest", "jest"}:
        # These bounded profiles collect files, not test-case IDs. Exact file
        # selectors run every case in a file, including newly added cases.
        # Body bytes remain verification inputs, not shard-layout authority.
        return inventory.digest({"selection": "exact-files-v1", "framework": framework,
                                 "files": files})
    records: list[dict[str, Any]] = []
    total = 0
    for relative in files:
        path = root / relative
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise SetupError("test-structure-unavailable") from exc
        total += len(content)
        if len(content) > 4 * 1024 * 1024 or total > 16 * 1024 * 1024:
            raise SetupError("test-structure-limit")
        if path.suffix.lower() != ".py":
            records.append(
                {
                    "file": relative,
                    "content_digest": _digest_bytes(content),
                }
            )
            continue
        try:
            tree = ast.parse(content, filename=relative)
        except (SyntaxError, ValueError) as exc:
            raise SetupError("test-structure-unavailable") from exc
        imports: list[str] = []
        classes: list[dict[str, Any]] = []
        load_tests: list[str] = []
        functions: list[dict[str, Any]] = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.append(ast.dump(node, include_attributes=False))
            elif isinstance(node, ast.ClassDef):
                methods = sorted(
                    (
                        child.name,
                        [
                            ast.dump(decorator, include_attributes=False)
                            for decorator in child.decorator_list
                        ],
                    )
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name.startswith("test")
                )
                classes.append(
                    {
                        "name": node.name,
                        "bases": [
                            ast.dump(base, include_attributes=False)
                            for base in node.bases
                        ],
                        "methods": methods,
                    }
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "load_tests":
                    load_tests.append(ast.dump(node.args, include_attributes=False))
                elif node.name.startswith("test"):
                    functions.append(
                        {
                            "name": node.name,
                            "decorators": [
                                ast.dump(decorator, include_attributes=False)
                                for decorator in node.decorator_list
                            ],
                        }
                    )
        records.append(
            {
                "file": relative,
                "imports": imports,
                "classes": classes,
                "functions": functions,
                "load_tests": load_tests,
            }
        )
    return inventory.digest(records)


def _generated_lineage(
    root: Path,
    previous: dict[str, Any] | None,
    proposal: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, bytes] | None]:
    existing = {
        item.get("path")
        for item in proposal.get("existing_configuration", [])
        if isinstance(item, dict)
    }
    if not existing:
        return {
            "kind": "create",
            "previous_artifact_digest": "",
            "previous_policy_digests": {},
        }, None
    if previous is None or not previous.get("application_contract_id"):
        return {
            "kind": "user-owned",
            "previous_artifact_digest": "",
            "previous_policy_digests": {},
        }, None
    try:
        old_payload = _artifact(previous)
        commit, _ = _policy_commit(root, previous, old_payload)
        if not commit or existing != set(POLICY_PATHS):
            raise SetupError("generated-lineage-unavailable")
        digests: dict[str, str] = {}
        for relative in POLICY_PATHS:
            current = (root / relative).read_bytes()
            if current != old_payload[relative]:
                raise SetupError("generated-lineage-unavailable")
            digests[relative] = _digest_bytes(current)
    except (OSError, SetupError, KeyError):
        return {
            "kind": "user-owned",
            "previous_artifact_digest": "",
            "previous_policy_digests": {},
        }, None
    return {
        "kind": "generated-update",
        "previous_artifact_digest": str(previous.get("artifact_digest", "")),
        "previous_policy_digests": digests,
    }, old_payload


def _diff_preview(previous: bytes | None, current: bytes) -> tuple[str, bool]:
    if previous is None:
        return "", False
    diff = "\n".join(
        difflib.unified_diff(
            previous.decode("utf-8", errors="replace").splitlines(),
            current.decode("utf-8", errors="replace").splitlines(),
            fromfile="committed-generated",
            tofile="proposed",
            lineterm="",
        )
    )
    encoded = diff.encode("utf-8")
    truncated = len(encoded) > MAX_REVIEW_PREVIEW_BYTES
    if truncated:
        encoded = encoded[:MAX_REVIEW_PREVIEW_BYTES]
        diff = encoded.decode("utf-8", errors="ignore")
    return diff, truncated


def _review(
    proposal: dict[str, Any],
    payload: dict[str, bytes],
    policies: dict[str, dict[str, Any]],
    elapsed_ms: float,
    lineage: dict[str, Any],
    previous_payload: dict[str, bytes] | None,
    authority_mode: str,
) -> dict[str, Any]:
    files = []
    for relative in POLICY_PATHS:
        content = payload[relative]
        preview = content[:MAX_REVIEW_PREVIEW_BYTES].decode("utf-8", errors="replace")
        diff_preview, diff_truncated = _diff_preview(
            previous_payload.get(relative) if previous_payload is not None else None,
            content,
        )
        action = (
            "update-generated"
            if lineage.get("kind") == "generated-update"
            else "review-existing"
            if policies[relative]["existed_at_analysis"]
            else "create"
        )
        files.append(
            {
                "path": relative,
                "action": action,
                "digest": policies[relative]["digest"],
                "previous_digest": lineage.get("previous_policy_digests", {}).get(
                    relative, ""
                ),
                "bytes": len(content),
                "preview": preview,
                "preview_truncated": len(content) > MAX_REVIEW_PREVIEW_BYTES,
                "diff_preview": diff_preview,
                "diff_preview_truncated": diff_truncated,
            }
        )
    return {
        "test_count": len(proposal["analysis"].get("inventory", [])),
        "shard_count": len(proposal.get("children", [])),
        "analysis_ms": max(0.0, elapsed_ms),
        "proposal_analysis": proposal.get("timing_samples", {}),
        "files": files,
        "constraints": [
            (
                "candidate-only-until-next-evidence-refresh"
                if authority_mode == "evidence"
                else "candidate-only-until-separate-approval"
            ),
            "user-owned-configuration-never-overwritten",
            "generated-updates-require-exact-digest-lineage",
            "git-index-commit-push-untouched",
            "commit-and-baseline-required-before-reuse",
            "first-baseline-cost-is-not-savings",
        ],
    }


def _new_state(
    root: Path,
    proposal: dict[str, Any],
    authority_id: str,
    authority_mode: str,
    previous: dict[str, Any] | None,
    elapsed_ms: float,
    cost_policy: dict[str, Any],
) -> dict[str, Any]:
    lineage, previous_payload = _generated_lineage(root, previous, proposal)
    artifact_path, artifact_digest, policies = _persist_artifact(root, proposal)
    payload = _read_artifact_directory(artifact_path, artifact_digest)
    analysis = proposal["analysis"]
    shards_value = _json_artifact(payload, POLICY_PATHS[0])
    entries = shards_value.get("entries", [])
    discovery = (
        entries[0].get("inventory", [])
        if len(entries) == 1 and isinstance(entries[0], dict)
        else []
    )
    now = int(time.time()) or 1
    has_existing = any(item["existed_at_analysis"] for item in policies.values())
    user_owned = has_existing and lineage["kind"] != "generated-update"
    if cost_policy.get("strategy") == "whole-suite":
        status = "whole-suite-preferred"
    elif user_owned or proposal["status"] not in {"proposal-ready", "review-required"}:
        status = "review-required"
    elif authority_mode == "evidence":
        status = "application-ready"
    else:
        status = "approval-required"
    reasons = list(proposal.get("reasons", []))
    if lineage["kind"] == "generated-update":
        reasons = [
            reason
            for reason in reasons
            if reason != "existing-configuration-needs-review"
        ]
    if user_owned and "existing-configuration-needs-review" not in reasons:
        reasons.append("existing-configuration-needs-review")
    if status == "whole-suite-preferred":
        reasons = [str(cost_policy.get("reason", "whole-suite-lower-cost"))]
    return {
        "version": VERSION,
        "project_root": str(root),
        "project_key": _project_key(root),
        "status": status,
        "reasons": sorted(set(reasons)),
        "parent_argv": list(analysis["command"]["argv"]),
        "cwd": ".",
        "analysis_contract_id": authority_id,
        "application_contract_id": "",
        "authority_mode": authority_mode,
        "artifact_directory": str(artifact_path),
        "artifact_digest": artifact_digest,
        "workspace_digest": _snapshot_without_policy(root),
        "applied_workspace_digest": "",
        "inventory_digest": str(proposal["inventory_digest"]),
        "runtime": dict(analysis["runtime"]),
        "discovery_files": sorted(discovery),
        "test_structure_digest": _test_structure_digest(
            root, sorted(discovery), framework=analysis["runtime"].get("framework", "")
        ),
        "policy_files": policies,
        "review": _review(
            proposal,
            payload,
            policies,
            elapsed_ms,
            lineage,
            previous_payload,
            authority_mode,
        ),
        "cost_policy": cost_policy,
        "lineage": lineage,
        "bootstrap": {},
        "generation": int(previous.get("generation", 0)) + 1 if previous else 1,
        "created_at": int(previous.get("created_at", now)) if previous else now,
        "updated_at": now,
    }


def _base_report(root: Path, status: str, reasons: list[str]) -> dict[str, Any]:
    return {
        "version": VERSION,
        "project": str(root),
        "status": status,
        "reasons": sorted(set(reasons)),
        "candidate_only": True,
        "setup_authority": False,
        "sharding_ready": False,
        "reuse_ready": False,
        "reuse_status": "unavailable",
        "command_status": "unavailable",
        "inventory_status": "unsupported",
        "exact_reuse_status": "unavailable",
        "policy_reuse_status": "unavailable",
        "authoritative_reuse_status": "unavailable",
        "next_action_code": "select-supported-command",
        "adapter": None,
        "capabilities": {
            name: {
                "implementation": "unsupported",
                "environment_test": "not-recorded",
            }
            for name in verification_adapters.CAPABILITY_NAMES
        },
    }


def _report_from_state(
    root: Path, state: dict[str, Any], status: str | None = None, reasons: list[str] | None = None
) -> dict[str, Any]:
    report = _base_report(root, status or state["status"], reasons or state["reasons"])
    report.update(
        parent_argv=list(state["parent_argv"]),
        baseline_evidence_id=BASELINE_EVIDENCE_ID,
        proposal_digest=state["artifact_digest"],
        policy_files=[
            {
                "path": path,
                "digest": state["policy_files"][path]["digest"],
                "bytes": state["policy_files"][path]["bytes"],
            }
            for path in sorted(state["policy_files"])
        ],
        review=state["review"],
        bootstrap=state["bootstrap"],
        cost_policy=state.get("cost_policy", {}),
        authority_mode=state.get("authority_mode", "guarded"),
        lineage_kind=state.get("lineage", {}).get("kind", "unknown"),
        generation=state["generation"],
    )
    profile = verification_adapters.command_profile(state["parent_argv"])
    if profile is not None:
        report["adapter"] = {
            "id": profile["adapter_id"],
            "version": profile["adapter_version"],
            "profile": profile["profile"],
        }
        report["capabilities"] = profile["capabilities"]
        report["command_status"] = "available"
        report["exact_reuse_status"] = "baseline-required"
        report["inventory_status"] = (
            "supported"
            if profile["capabilities"]["inventory"]["implementation"]
            != "unsupported"
            else "unsupported"
        )
        report["next_action_code"] = {
            "commit-required": "commit-generated-policy",
            "baseline-required": "run-baseline",
            "review-required": "refresh-review",
            "approval-required": "approve-guarded-setup",
            "application-ready": "apply-reviewed-setup",
            "whole-suite-preferred": "keep-parent-verification",
            "unsupported": "select-supported-command",
            "blocked": "repair-setup",
        }.get(report["status"], "run-baseline")
    return report


def selection_report(project: Path) -> dict[str, Any]:
    try:
        root = inventory.project_root(project)
    except (inventory.AnalysisError, OSError, ValueError):
        root = Path(project).resolve()
        return _base_report(root, "unsupported", ["non-git-project"])
    report = _base_report(root, "selection-required", ["explicit-command-required"])
    report.update(
        command_example=["python3", "-m", "unittest", "discover", "-s", "tests", "-q"],
        next_action="Run click-gate sharding init -- followed by the exact supported unittest argv in Evidence mode or an approved Guarded setup contract.",
    )
    return report


def _index_digest(root: Path) -> str:
    raw = inventory.git_read(root, ["rev-parse", "--git-path", "index"])
    path = Path(os.fsdecode(raw.strip()))
    if not path.is_absolute():
        path = root / path
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        content = b""
    return _digest_bytes(content)


def _policy_commit(
    root: Path, state: dict[str, Any], payload: dict[str, bytes]
) -> tuple[str, str]:
    for relative in POLICY_PATHS:
        target = root / relative
        try:
            metadata = target.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                return "", "policy-worktree-changed"
            current = target.read_bytes()
        except OSError:
            return "", "policy-commit-required"
        expected = payload[relative]
        if current != expected:
            return "", "policy-worktree-changed"
        if inventory.git_read(root, ["show", f"HEAD:{relative}"], optional=True) != expected:
            return "", "policy-commit-required"
        if inventory.git_read(root, ["show", f":{relative}"], optional=True) != expected:
            return "", "policy-index-differs-from-head"
    commit = inventory.git_read(root, ["rev-parse", "--verify", "HEAD"]).decode().strip()
    return (commit, "") if re.fullmatch(r"[0-9a-f]{40,64}", commit) else ("", "git-unavailable")


def _current_discovery_files(root: Path, state: dict[str, Any]) -> list[str]:
    analysis = _json_artifact(_artifact(state), "analysis.json")
    command = analysis.get("command", {})
    start = command.get("start")
    patterns = command.get("patterns")
    if patterns is None and isinstance(command.get("pattern"), str):
        patterns = [command["pattern"]]
    if (
        not isinstance(start, str)
        or not isinstance(patterns, list)
        or not patterns
        or any(not isinstance(pattern, str) or not pattern for pattern in patterns)
    ):
        raise SetupError("proposal-artifact-invalid")
    start_path = (root / start).resolve(strict=True)
    if not inventory.inside(root, start_path):
        raise SetupError("project-boundary")
    files: list[str] = []
    excluded = set(command.get("exclude_directories", []))
    for directory, directories, names in os.walk(start_path, followlinks=False):
        directories[:] = sorted(
            name
            for name in directories
            if not (Path(directory) / name).is_symlink()
            and name != ".git"
            and name not in excluded
        )
        for name in sorted(names):
            path = Path(directory) / name
            if (
                path.is_file()
                and not path.is_symlink()
                and any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)
            ):
                files.append(path.relative_to(root).as_posix())
    return sorted(files)


def _condition_reasons(root: Path, state: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    try:
        current_files = _current_discovery_files(root, state)
        if current_files != state["discovery_files"]:
            reasons.append("test-discovery-changed")
        elif state.get("test_structure_digest") and _test_structure_digest(
            root, current_files, framework=state["runtime"].get("framework", "")
        ) != state.get("test_structure_digest"):
            reasons.append("test-inventory-changed")
        executable = inventory.trusted_executable(state["parent_argv"][0], root)
        if _digest_bytes(executable.read_bytes()) != state["runtime"].get("executable_digest"):
            reasons.append("runner-changed")
        if (
            state["runtime"].get("adapter_digest")
            and inventory.adapter_implementation_digest()
            != state["runtime"].get("adapter_digest")
        ):
            reasons.append("adapter-changed")
        if state["runtime"].get("framework") in {"vitest", "jest"}:
            node = inventory.trusted_executable("node", root)
            if (
                _digest_bytes(node.read_bytes())
                != state["runtime"].get("node_executable_digest")
            ):
                reasons.append("runtime-changed")
            if (
                (
                    inventory.vitest_configuration_digest(root)
                    if state["runtime"].get("framework") == "vitest"
                    else inventory.jest_configuration_digest(root)
                )
                != state["runtime"].get("project_configuration_digest")
            ):
                reasons.append("configuration-changed")
        expected_thresholds = state.get("cost_policy", {}).get("thresholds")
        if isinstance(expected_thresholds, dict) and expected_thresholds != _cost_thresholds():
            reasons.append("cost-policy-changed")
        payload = _artifact(state)
        if state["application_contract_id"]:
            for relative in POLICY_PATHS:
                target = root / relative
                if not target.is_file() or target.is_symlink() or target.read_bytes() != payload[relative]:
                    reasons.append("policy-worktree-changed")
                    break
    except (OSError, ValueError, SetupError, inventory.AnalysisError):
        reasons.append("setup-revalidation-unavailable")
    return sorted(set(reasons))


def _baseline_status(root: Path, setup: dict[str, Any], contract_state: Any) -> dict[str, Any]:
    report = _report_from_state(root, setup, "baseline-required", ["baseline-required"])
    if not isinstance(contract_state, dict):
        return report
    verification = contract_state.get("verification")
    evidence_state = contract_state.get("evidence_state")
    sources = evidence_state.get("sources") if isinstance(evidence_state, dict) else None
    if not isinstance(verification, dict) or not isinstance(sources, dict):
        return report
    parent_key = evidence.evidence_key(BASELINE_EVIDENCE_ID)
    shard_set = evidence_shards.active_set(evidence_state, parent_key)
    if shard_set is None:
        report["reasons"] = ["baseline-evidence-id-not-declared"]
        return report
    expected_parent = evidence_shards.group_digest(
        [{"argv": setup["parent_argv"]}]
    )
    if shard_set.get("parent_check_digest") != expected_parent:
        report["reasons"] = ["baseline-check-changed"]
        return report
    revision = verification.get("mutation_revision")
    children = shard_set.get("children", [])
    child_sources = [
        sources.get(child.get("source_key"))
        for child in children
        if isinstance(child, dict)
    ]
    if (
        not children
        or len(child_sources) != len(children)
        or any(not isinstance(source, dict) for source in child_sources)
    ):
        return report
    if any(
        source.get("status") == "failed" or source.get("last_exit_code") not in (None, 0)
        for source in child_sources
        if isinstance(source, dict)
    ):
        report.update(status="blocked", reasons=["baseline-validation-failed"])
        return report
    current = all(
        source.get("status") == "passed"
        and source.get("verified_revision") == revision
        for source in child_sources
        if isinstance(source, dict)
    )
    if not current:
        return report
    report.update(
        status="sharding-ready",
        reasons=["authoritative-observation-optional"],
        candidate_only=False,
        sharding_ready=True,
        reuse_ready=True,
        reuse_status="exact",
        exact_reuse_status="available",
        next_action_code="reuse-unchanged-or-run-changed",
    )
    safe_receipts = [
        source.get("verified_safe_change_receipt")
        for source in child_sources
        if isinstance(source, dict)
    ]
    valid_safe_receipts = sum(
        change_policy.receipt_is_valid(item) for item in safe_receipts
    )
    if valid_safe_receipts:
        report["policy_reuse_status"] = (
            "available"
            if valid_safe_receipts == len(child_sources)
            else "partially-available"
        )
        report["reuse_status"] = (
            "exact-and-policy"
            if valid_safe_receipts == len(child_sources)
            else "exact-and-partial-policy"
        )
    observations = [source.get("verified_dependency_observation") for source in child_sources]
    if all(
        dependency_cache.authoritative_dependency_observation_is_complete(item)
        for item in observations
    ):
        report.update(
            status="reuse-ready",
            reasons=[],
            reuse_ready=True,
            reuse_status="authoritative-v2",
            authoritative_reuse_status="available",
            next_action_code="reuse-by-current-authority",
            setup_authority=False,
        )
    return report


def status(project: Path, contract_state: Any = None) -> dict[str, Any]:
    try:
        root = inventory.project_root(project)
        setup = _read_state(root)
    except (SetupError, inventory.AnalysisError, OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, (SetupError, inventory.AnalysisError)) else "setup-status-unavailable"
        return _base_report(Path(project).resolve(), "blocked", [reason])
    if setup is None:
        return selection_report(root)
    # Unsupported/blocked proposal attempts deliberately have no artifact.
    # Preserve their stable diagnostic instead of treating that absence as
    # later setup drift.
    if not setup["artifact_directory"]:
        return _report_from_state(root, setup)
    drift = _condition_reasons(root, setup)
    if drift:
        report = _report_from_state(root, setup, "review-required", drift)
        report["next_action"] = "Run refresh in Evidence mode or an approved Guarded contract to create a new review-only proposal; user-owned policy will not be overwritten."
        return report
    if setup["status"] in {
        "application-ready",
        "approval-required",
        "review-required",
        "whole-suite-preferred",
        "unsupported",
        "blocked",
    }:
        report = _report_from_state(root, setup)
        if setup["status"] == "approval-required":
            report["next_action"] = "Approve a new Guarded application contract that identifies this proposal digest, then run click-gate sharding refresh."
        elif setup["status"] == "application-ready":
            report["next_action"] = "Review the proposed files and diff, then run click-gate sharding refresh in Evidence mode to apply only the recorded bytes."
        elif setup["status"] == "whole-suite-preferred":
            report["next_action"] = "Keep using the original parent verification. Click will reconsider after test discovery, command, runner, or cost-policy changes."
        return report
    payload = _artifact(setup)
    commit, commit_reason = _policy_commit(root, setup, payload)
    if not commit:
        report = _report_from_state(root, setup, "commit-required", [commit_reason])
        report["next_action"] = {
            "commit_exact_files": list(POLICY_PATHS),
            "then": "Run click-gate sharding refresh; Click will verify HEAD, index, and worktree content.",
        }
        return report
    if setup["bootstrap"].get("status") != "passed" or setup["bootstrap"].get("commit") != commit:
        report = _report_from_state(root, setup, "baseline-required", ["bootstrap-validation-required"])
        report["sharding_ready"] = False
        report["next_action"] = "Run click-gate sharding refresh in the approved application contract to compare the parent and generated children."
        return report
    return _baseline_status(root, setup, contract_state)


def plan(
    project: Path,
    operation: str,
    command: list[str],
    contract_state: Any,
    *,
    runtime_mode: str = "guarded",
    authority_id: str = "",
) -> dict[str, Any]:
    root = inventory.project_root(project)
    current = status(root, contract_state)
    setup = _read_state(root)
    if operation == "status":
        return {"action": "report", "report": current}
    if operation == "init" and not command:
        return {"action": "report", "report": current}
    if runtime_mode not in RUNTIME_MODES:
        raise SetupError("sharding-runtime-unavailable")
    if not authority_id and isinstance(contract_state, dict):
        authority_id = str(
            contract_state.get(
                "evidence_session_id" if runtime_mode == "evidence" else "contract_id",
                "",
            )
        )
    if operation == "init":
        if setup is not None and command == setup["parent_argv"]:
            return {"action": "report", "report": current}
        return {"action": "generate", "command": command, "refresh": setup is not None}
    if operation != "refresh" or command:
        raise SetupError("invalid-sharding-control")
    if setup is None:
        return {"action": "report", "report": current}
    if current["status"] == "review-required" and set(current["reasons"]) & {
        "test-discovery-changed",
        "test-inventory-changed",
        "runner-changed",
        "adapter-changed",
        "runtime-changed",
        "configuration-changed",
        "cost-policy-changed",
    }:
        return {"action": "generate", "command": setup["parent_argv"], "refresh": True}
    if setup["status"] == "application-ready":
        if runtime_mode == "evidence" and setup.get("authority_mode") == "evidence":
            return {"action": "apply"}
        waiting = dict(current)
        waiting["reasons"] = ["evidence-authority-required"]
        return {"action": "report", "report": waiting}
    if setup["status"] == "approval-required":
        presentation = (
            contract_state.get("presentation", {})
            if isinstance(contract_state, dict)
            else {}
        )
        proposal_bound = setup["artifact_digest"] in json.dumps(
            presentation, sort_keys=True, ensure_ascii=True
        )
        evidence_state = (
            contract_state.get("evidence_state", {})
            if isinstance(contract_state, dict)
            else {}
        )
        sources = (
            evidence_state.get("sources", {})
            if isinstance(evidence_state, dict)
            else {}
        )
        baseline_source = (
            sources.get(evidence.evidence_key(BASELINE_EVIDENCE_ID))
            if isinstance(sources, dict)
            else None
        )
        baseline_declared = bool(
            isinstance(baseline_source, dict)
            and baseline_source.get("kind") == "argv"
        )
        if (
            CONTRACT_ID.fullmatch(authority_id)
            and authority_id != setup["analysis_contract_id"]
            and proposal_bound
            and baseline_declared
        ):
            return {"action": "apply"}
        waiting = dict(current)
        waiting["status"] = "approval-required"
        waiting["reasons"] = [
            "baseline-evidence-approval-required"
            if proposal_bound and not baseline_declared
            else "proposal-digest-approval-required"
            if authority_id != setup["analysis_contract_id"]
            else "separate-application-approval-required"
        ]
        return {"action": "report", "report": waiting}
    if current["status"] == "commit-required":
        return {"action": "report", "report": current}
    if current["status"] == "baseline-required":
        if setup["bootstrap"].get("status") == "passed":
            return {"action": "verify", "command": setup["parent_argv"]}
        return {"action": "bootstrap"}
    if (
        current["status"] == "sharding-ready"
        and isinstance(contract_state, dict)
        and observer_control.mode(contract_state.get("verification")) == "authoritative"
    ):
        return {"action": "bootstrap"}
    return {"action": "report", "report": current}


def generate(
    project: Path,
    command: list[str],
    authority_id: str,
    *,
    runtime_mode: str = "guarded",
) -> dict[str, Any]:
    root = inventory.project_root(project)
    expected_prefix = "ctr_" if runtime_mode == "guarded" else "evs_"
    if (
        runtime_mode not in RUNTIME_MODES
        or AUTHORITY_ID.fullmatch(authority_id) is None
        or not authority_id.startswith(expected_prefix)
    ):
        raise SetupError("setup-authority-required")
    with _project_lock(root):
        previous = _read_state(root)
        if previous is not None and command == previous["parent_argv"]:
            current = status(root)
            if current["status"] != "review-required":
                return current
        started = time.perf_counter_ns()
        proposal = auto_sharding.click_shard_proposal.propose(root, command, cwd=root)
        if not proposal.get("proposal_ready"):
            elapsed = (time.perf_counter_ns() - started) / 1_000_000
            now = int(time.time()) or 1
            blocked = {
                "version": VERSION,
                "project_root": str(root),
                "project_key": _project_key(root),
                "status": proposal.get("status") if proposal.get("status") in {"unsupported", "review-required"} else "blocked",
                "reasons": sorted(set(str(item) for item in proposal.get("reasons", ["proposal-unavailable"]))),
                "parent_argv": list(command),
                "cwd": ".",
                "analysis_contract_id": authority_id,
                "application_contract_id": "",
                "authority_mode": runtime_mode,
                "artifact_directory": "",
                "artifact_digest": "",
                "workspace_digest": "",
                "applied_workspace_digest": "",
                "inventory_digest": "",
                "runtime": {},
                "discovery_files": [],
                "test_structure_digest": "",
                "policy_files": {},
                "review": {"analysis_ms": max(0.0, elapsed)},
                "cost_policy": {},
                "lineage": {},
                "bootstrap": {},
                "generation": int(previous.get("generation", 0)) + 1 if previous else 1,
                "created_at": int(previous.get("created_at", now)) if previous else now,
                "updated_at": now,
            }
            _write_state(root, blocked)
            return _report_from_state(root, blocked)
        cost_policy = _measure_cost_policy(root, proposal)
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        setup = _new_state(
            root,
            proposal,
            authority_id,
            runtime_mode,
            previous,
            elapsed,
            cost_policy,
        )
        _write_state(root, setup)
        report = _report_from_state(root, setup)
        report["proposal_digest"] = setup["artifact_digest"]
        if setup["status"] == "application-ready":
            report["next_action"] = "Review the exact diff, then run click-gate sharding refresh in Evidence mode."
        elif setup["status"] == "approval-required":
            report["next_action"] = "Review the exact diff, then approve a separate Guarded application contract bound to this proposal digest."
        elif setup["status"] == "whole-suite-preferred":
            report["next_action"] = "Keep the original parent verification; the measured cost rule did not select sharding."
        return report


def _snapshot_without_policy(root: Path) -> str:
    snapshot = inventory.workspace_snapshot(root, inventory.Limits())
    for relative in POLICY_PATHS:
        snapshot.pop(relative, None)
    return inventory.digest(snapshot)


def _write_policy_no_replace(target: Path, content: bytes) -> None:
    if target.exists() or target.is_symlink():
        if target.is_file() and not target.is_symlink() and target.read_bytes() == content:
            return
        raise SetupError("policy-worktree-changed")
    directory = target.parent
    try:
        directory.mkdir(mode=0o755, exist_ok=True)
        metadata = directory.lstat()
    except OSError as exc:
        raise SetupError("policy-directory-unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SetupError("policy-directory-unavailable")
    descriptor, temporary = tempfile.mkstemp(prefix=".click-proposal-", dir=directory)
    try:
        _restrict_file_permissions(descriptor, temporary)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if not target.is_file() or target.is_symlink() or target.read_bytes() != content:
                raise SetupError("policy-worktree-changed")
        Path(temporary).unlink(missing_ok=True)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        Path(temporary).unlink(missing_ok=True)
        raise


def _write_policy_replace_exact(
    target: Path, content: bytes, expected_digest: str
) -> None:
    try:
        metadata = target.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise SetupError("policy-worktree-changed")
        current = target.read_bytes()
    except OSError as exc:
        raise SetupError("policy-worktree-changed") from exc
    if current == content:
        return
    if _digest_bytes(current) != expected_digest:
        raise SetupError("policy-worktree-changed")
    descriptor, temporary = tempfile.mkstemp(prefix=".click-update-", dir=target.parent)
    try:
        _restrict_file_permissions(descriptor, temporary)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        Path(temporary).unlink(missing_ok=True)
        raise


def apply(
    project: Path,
    authority_id: str,
    *,
    runtime_mode: str = "guarded",
) -> dict[str, Any]:
    root = inventory.project_root(project)
    expected_prefix = "ctr_" if runtime_mode == "guarded" else "evs_"
    if (
        runtime_mode not in RUNTIME_MODES
        or AUTHORITY_ID.fullmatch(authority_id) is None
        or not authority_id.startswith(expected_prefix)
    ):
        raise SetupError("setup-authority-required")
    with _project_lock(root):
        setup = _read_state(root)
        expected_status = (
            "application-ready" if runtime_mode == "evidence" else "approval-required"
        )
        if (
            setup is None
            or setup["status"] != expected_status
            or setup.get("authority_mode") != runtime_mode
        ):
            raise SetupError("proposal-approval-state-required")
        if runtime_mode == "guarded" and setup["analysis_contract_id"] == authority_id:
            raise SetupError("separate-application-approval-required")
        lineage = setup.get("lineage", {})
        if lineage.get("kind") == "user-owned":
            raise SetupError("existing-configuration-needs-review")
        payload = _artifact(setup)
        current_without_policy = _snapshot_without_policy(root)
        if current_without_policy != setup["workspace_digest"]:
            raise SetupError("project-changed-since-proposal")
        index_before = _index_digest(root)
        for relative in POLICY_PATHS:
            if lineage.get("kind") == "generated-update":
                previous_digest = lineage.get("previous_policy_digests", {}).get(
                    relative, ""
                )
                if DIGEST.fullmatch(str(previous_digest)) is None:
                    raise SetupError("generated-lineage-unavailable")
                _write_policy_replace_exact(
                    root / relative, payload[relative], str(previous_digest)
                )
            else:
                _write_policy_no_replace(root / relative, payload[relative])
        if _index_digest(root) != index_before:
            raise SetupError("git-index-changed-during-apply")
        setup["status"] = "commit-required"
        setup["reasons"] = ["policy-commit-required"]
        setup["application_contract_id"] = authority_id
        setup["applied_workspace_digest"] = inventory.digest(
            inventory.workspace_snapshot(root, inventory.Limits())
        )
        _write_state(root, setup)
        return status(root)


def _run_check(root: Path, argv: list[str]) -> dict[str, Any]:
    inventory.parse_command(argv, root, root)
    executable = inventory.trusted_executable(argv[0], root)
    arguments = list(argv[1:])
    profile = verification_adapters.command_profile(argv)
    if (
        profile is not None
        and profile.get("adapter_id") in {
            verification_adapters.VITEST_ADAPTER,
            verification_adapters.JEST_ADAPTER,
        }
    ):
        # Node test runners may write derived result caches during one-shot runs.
        # Setup probes disable those caches so proposal and baseline validation
        # remain read-only while preserving selected tests and assertions.
        arguments.append("--no-cache")
    command = [str(executable), *arguments]
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.perf_counter_ns()
    try:
        inventory.run_bounded_command(
            command,
            root,
            environment,
            inventory.Limits(timeout=120.0),
        )
        return {
            "status": "passed",
            "exit_code": 0,
            "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
            "reason": "",
        }
    except inventory.AnalysisError as exc:
        return {
            "status": "failed",
            "exit_code": 1,
            "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
            "reason": str(exc),
        }


def _cost_thresholds() -> dict[str, float]:
    defaults = {
        "min_parent_ms": DEFAULT_MIN_PARENT_MS,
        "min_avoidable_ms": DEFAULT_MIN_AVOIDABLE_MS,
        "management_reserve_ms": DEFAULT_MANAGEMENT_RESERVE_MS,
    }
    values: dict[str, float] = {}
    for key, default in defaults.items():
        raw = os.environ.get(COST_ENVIRONMENT[key], "")
        try:
            parsed = float(raw) if raw else default
        except ValueError:
            parsed = default
        values[key] = (
            parsed
            if math.isfinite(parsed) and 0.0 <= parsed <= 600_000.0
            else default
        )
    return values


def _run_startup_probe(root: Path, argv: list[str]) -> dict[str, Any]:
    executable = inventory.trusted_executable(argv[0], root)
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.perf_counter_ns()
    try:
        profile = verification_adapters.command_profile(argv)
        if profile is not None and profile.get("adapter_id") in {
            verification_adapters.VITEST_ADAPTER,
            verification_adapters.JEST_ADAPTER,
        }:
            target_index = next(
                (
                    index
                    for index, argument in enumerate(argv[1:], 1)
                    if not argument.startswith("-")
                ),
                -1,
            )
            if target_index < 0:
                raise inventory.AnalysisError("unsupported-node-test-command")
            command = [str(executable), *argv[1 : target_index + 1], "--version"]
        else:
            command = [str(executable), "-I", "-B", "-c", "pass"]
        inventory.run_bounded_command(
            command,
            root,
            environment,
            inventory.Limits(timeout=30.0),
        )
        status_value, reason = "passed", ""
    except (inventory.AnalysisError, OSError, subprocess.SubprocessError) as exc:
        status_value = "failed"
        reason = str(exc) if isinstance(exc, inventory.AnalysisError) else "startup-probe-failed"
    return {
        "status": status_value,
        "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
        "reason": reason,
    }


def _measure_cost_policy(root: Path, proposal: dict[str, Any]) -> dict[str, Any]:
    thresholds = _cost_thresholds()
    before = inventory.workspace_snapshot(root, inventory.Limits())
    parent = _run_check(root, proposal["analysis"]["command"]["argv"])
    if inventory.workspace_snapshot(root, inventory.Limits()) != before:
        raise SetupError("cost-probe-mutated-project")
    result: dict[str, Any] = {
        "strategy": "whole-suite",
        "reason": "parent-validation-failed",
        "thresholds": thresholds,
        "measurement_scope": "setup-probe-not-savings",
        "parent": parent,
        "startup": None,
        "children": [],
        "sequential_children_ms": None,
        "estimated_eligible_reuse_ms": None,
        "estimated_reusable_shards": 0,
        "estimate_assumption": "one-worst-duration-shard-changes",
    }
    if parent.get("status") != "passed":
        return result
    parent_ms = float(parent["duration_ms"])
    if parent_ms < thresholds["min_parent_ms"]:
        result["reason"] = "parent-below-sharding-threshold"
        result["measurement_status"] = "children-skipped-for-short-parent"
        return result
    startup = _run_startup_probe(root, proposal["analysis"]["command"]["argv"])
    if inventory.workspace_snapshot(root, inventory.Limits()) != before:
        raise SetupError("cost-probe-mutated-project")
    result["startup"] = startup
    if startup.get("status") != "passed":
        result["reason"] = "startup-probe-failed"
        result["measurement_status"] = "incomplete"
        return result
    children: list[dict[str, Any]] = []
    for child in proposal.get("children", []):
        argv = child.get("argv") if isinstance(child, dict) else None
        if not isinstance(argv, list):
            raise SetupError("proposal-artifact-invalid")
        child_result = _run_check(root, argv)
        children.append(child_result)
        if inventory.workspace_snapshot(root, inventory.Limits()) != before:
            raise SetupError("cost-probe-mutated-project")
    result["children"] = children
    if not children or any(item.get("status") != "passed" for item in children):
        result["reason"] = "child-validation-failed"
        result["measurement_status"] = "incomplete"
        return result
    durations = [float(item["duration_ms"]) for item in children]
    sequential = sum(durations)
    eligible = max(0.0, sequential - max(durations))
    required = max(
        thresholds["min_avoidable_ms"],
        float(startup["duration_ms"]) + thresholds["management_reserve_ms"],
    )
    result.update(
        sequential_children_ms=sequential,
        estimated_eligible_reuse_ms=eligible,
        estimated_reusable_shards=max(0, len(children) - 1),
        required_avoidable_ms=required,
        measurement_status="measured",
    )
    if eligible >= required:
        result.update(strategy="sharded", reason="measured-reuse-exceeds-cost-floor")
    else:
        result["reason"] = "estimated-reuse-below-cost-floor"
    return result


def bootstrap(
    project: Path,
    authority_id: str,
    *,
    runtime_mode: str = "guarded",
) -> dict[str, Any]:
    root = inventory.project_root(project)
    expected_prefix = "ctr_" if runtime_mode == "guarded" else "evs_"
    if (
        runtime_mode not in RUNTIME_MODES
        or AUTHORITY_ID.fullmatch(authority_id) is None
        or not authority_id.startswith(expected_prefix)
    ):
        raise SetupError("setup-authority-required")
    with _project_lock(root):
        setup = _read_state(root)
        if setup is None or not setup["application_contract_id"]:
            raise SetupError("applied-policy-required")
        payload = _artifact(setup)
        commit, reason = _policy_commit(root, setup, payload)
        if not commit:
            raise SetupError(reason)
        if _condition_reasons(root, setup):
            raise SetupError("setup-conditions-changed")
        analysis_record = _json_artifact(payload, "analysis.json")
        proposal_record = _json_artifact(payload, "proposal.json")
        before = inventory.workspace_snapshot(root, inventory.Limits())
        started = time.perf_counter_ns()
        analysis_started = time.perf_counter_ns()
        parent = inventory.analyze(
            root,
            setup["parent_argv"],
            cwd=root,
            _baseline_snapshot=before,
        )
        child_analyses = []
        for child in proposal_record.get("children", []):
            if not isinstance(child, dict) or not isinstance(child.get("argv"), list):
                raise SetupError("proposal-artifact-invalid")
            child_analyses.append(
                inventory.analyze(
                    root,
                    child["argv"],
                    cwd=root,
                    _baseline_snapshot=before,
                )
            )
        analysis_ms = (time.perf_counter_ns() - analysis_started) / 1_000_000
        inventories_match = bool(
            parent.get("status") == "analysis-complete"
            and parent.get("inventory_digest") == setup["inventory_digest"]
            and parent.get("runtime") == analysis_record.get("runtime")
            and len(child_analyses) == len(proposal_record.get("children", []))
            and all(
                current.get("status") == "analysis-complete"
                and current.get("inventory_digest") == expected.get("inventory_digest")
                for current, expected in zip(
                    child_analyses, proposal_record.get("children", [])
                )
            )
        )
        parent_result = _run_check(root, setup["parent_argv"])
        if inventory.workspace_snapshot(root, inventory.Limits()) != before:
            raise SetupError("baseline-mutated-project")
        child_results = []
        for child in proposal_record.get("children", []):
            child_results.append(_run_check(root, child["argv"]))
            if inventory.workspace_snapshot(root, inventory.Limits()) != before:
                raise SetupError("baseline-mutated-project")
        total_ms = (time.perf_counter_ns() - started) / 1_000_000
        sharded_ms = sum(float(item["duration_ms"]) for item in child_results)
        passed = bool(
            inventories_match
            and parent_result["status"] == "passed"
            and child_results
            and all(item["status"] == "passed" for item in child_results)
        )
        setup["bootstrap"] = {
            "status": "passed" if passed else "failed",
            "commit": commit,
            "inventory_equal": inventories_match,
            "parent": parent_result,
            "children": child_results,
            "analysis_ms": analysis_ms,
            "analysis_workspace_snapshot_scans": 3 * (1 + len(child_analyses)),
            "avoided_initial_analysis_scans": 1 + len(child_analyses),
            "full_validation_ms": float(parent_result["duration_ms"]),
            "sharded_validation_ms": sharded_ms,
            "click_processing_ms": max(
                0.0,
                total_ms - float(parent_result["duration_ms"]) - sharded_ms,
            ),
            "initial_setup_ms": total_ms + float(setup["review"].get("analysis_ms", 0.0)),
            "comparison_net_ms": float(parent_result["duration_ms"]) - sharded_ms,
            "comparison_scope": "first-bootstrap-parent-vs-sequential-children-not-savings",
        }
        setup["status"] = "baseline-required" if passed else "blocked"
        setup["reasons"] = ["baseline-required" if passed else "bootstrap-validation-failed"]
        _write_state(root, setup)
        return status(root)


def dashboard_setup_projection(report: Any) -> dict[str, Any]:
    value = report if isinstance(report, dict) else {}
    projected_durations = {
        "initial_setup_ms",
        "observation_ms",
        "click_processing_ms",
        "bootstrap_parent_ms",
        "bootstrap_shards_ms",
        "comparison_net_ms",
    }
    projected_statuses = {
        "command_status": "unavailable",
        "inventory_status": "unsupported",
        "exact_reuse_status": "unavailable",
        "policy_reuse_status": "unavailable",
        "authoritative_reuse_status": "unavailable",
        "next_action_code": "select-supported-command",
    }

    def number(item: Any, *, signed: bool = False) -> float | None:
        if (
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not math.isfinite(float(item))
            or (not signed and float(item) < 0)
        ):
            return None
        return float(item)

    def token(item: Any, fallback: str) -> str:
        return (
            item
            if isinstance(item, str) and re.fullmatch(r"[a-z0-9-]{1,64}", item)
            else fallback
        )

    def scope(item: Any) -> str:
        return (
            item
            if item
            in {
                "first-bootstrap-parent-vs-sequential-children-not-savings",
                "unmeasured",
            }
            else "unmeasured"
        )

    # Contract state stores this sanitized form. Accepting it again keeps
    # repeated status/dashboard synchronization idempotent.
    if value.get("version") == VERSION and projected_durations.issubset(value):
        status_value = value.get("status")
        return {
            "version": VERSION,
            "status": token(status_value, "unconfigured"),
            "sharding_ready": value.get("sharding_ready") is True,
            "reuse_ready": value.get("reuse_ready") is True,
            "reuse_status": token(value.get("reuse_status"), "unavailable"),
            **{
                field: token(value.get(field), fallback)
                for field, fallback in projected_statuses.items()
            },
            "initial_setup_ms": number(value.get("initial_setup_ms")),
            "observation_ms": number(value.get("observation_ms")),
            "click_processing_ms": number(value.get("click_processing_ms")),
            "bootstrap_parent_ms": number(value.get("bootstrap_parent_ms")),
            "bootstrap_shards_ms": number(value.get("bootstrap_shards_ms")),
            "comparison_net_ms": number(value.get("comparison_net_ms"), signed=True),
            "comparison_scope": scope(value.get("comparison_scope")),
        }
    bootstrap_value = value.get("bootstrap")
    bootstrap_value = bootstrap_value if isinstance(bootstrap_value, dict) else {}
    status_value = value.get("status")
    return {
        "version": VERSION,
        "status": token(status_value, "unconfigured"),
        "sharding_ready": value.get("sharding_ready") is True,
        "reuse_ready": value.get("reuse_ready") is True,
        "reuse_status": token(value.get("reuse_status"), "unavailable"),
        **{
            field: token(value.get(field), fallback)
            for field, fallback in projected_statuses.items()
        },
        "initial_setup_ms": number(bootstrap_value.get("initial_setup_ms")),
        "observation_ms": None,
        "click_processing_ms": number(bootstrap_value.get("click_processing_ms")),
        "bootstrap_parent_ms": number(bootstrap_value.get("full_validation_ms")),
        "bootstrap_shards_ms": number(bootstrap_value.get("sharded_validation_ms")),
        "comparison_net_ms": number(
            bootstrap_value.get("comparison_net_ms"), signed=True
        ),
        "comparison_scope": scope(bootstrap_value.get("comparison_scope")),
    }


def _print(value: dict[str, Any]) -> None:
    print(json.dumps(value, sort_keys=True, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("operation", choices=("generate", "apply", "bootstrap", "status"))
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--authority-id", default="")
    parser.add_argument("--runtime-mode", choices=tuple(sorted(RUNTIME_MODES)), default="guarded")
    # Retain the private pre-Phase-3 runner flag for resumable local state.
    parser.add_argument("--contract-id", default="")
    arguments = list(sys.argv[1:] if argv is None else argv)
    separator = arguments.index("--") if "--" in arguments else len(arguments)
    options = parser.parse_args(arguments[:separator])
    command = arguments[separator + 1 :]
    authority_id = options.authority_id or options.contract_id
    try:
        if options.operation == "generate":
            if not command:
                raise SetupError("explicit-command-required")
            result = generate(
                options.project,
                command,
                authority_id,
                runtime_mode=options.runtime_mode,
            )
        elif options.operation == "apply":
            result = apply(
                options.project,
                authority_id,
                runtime_mode=options.runtime_mode,
            )
        elif options.operation == "bootstrap":
            result = bootstrap(
                options.project,
                authority_id,
                runtime_mode=options.runtime_mode,
            )
        else:
            result = status(options.project)
    except (SetupError, inventory.AnalysisError, OSError, ValueError, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc, (SetupError, inventory.AnalysisError)) else "setup-operation-failed"
        _print(_base_report(Path(options.project).resolve(), "blocked", [reason]))
        return 2
    _print(result)
    return 0 if result["status"] not in {"blocked", "unsupported", "review-required"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
