"""Dependency snapshots for opt-in cross-revision evidence reuse.

Dependencies may be declared in the approved contract, in a committed
repository manifest, or in both. Patterns use a small deterministic grammar:
``*`` matches inside one path segment, ``**`` as a complete segment crosses
directories, and a trailing slash names a directory prefix. Every unsafe or
ambiguous input fails closed and simply causes the verification check to run.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import fnmatch
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

if __package__:
    from . import click_observation_inputs
else:
    import click_observation_inputs


CONFIG_RELATIVE_PATH = ".click/evidence-dependencies.json"
CONFIG_VERSION = 1
CONTRACT_PROVIDER_NAME = "approved-contract-v1"
MANIFEST_PROVIDER_NAME = "repository-manifest-v1"
COMBINED_PROVIDER_NAME = "approved-contract+repository-manifest-v1"
AUTOMATIC_PROVIDER_NAME = "runtime-observed-inputs-v1"
PROVIDER_NAMES = frozenset(
    {
        CONTRACT_PROVIDER_NAME,
        MANIFEST_PROVIDER_NAME,
        COMBINED_PROVIDER_NAME,
        AUTOMATIC_PROVIDER_NAME,
    }
)
OBSERVATION_PROVIDER_NAME = "runtime-dependency-observation-v1"
OBSERVATION_STATUSES = frozenset({"complete", "failed", "unavailable"})
OBSERVATION_FIELDS = frozenset(
    {
        "provider",
        "status",
        "paths",
        "external_access",
        "child_processes",
        "process_tree_complete",
    }
)
AUTHORITATIVE_OBSERVATION_PROVIDER_NAME = "runtime-dependency-observation-v2"
AUTHORITATIVE_OBSERVATION_PROFILE = click_observation_inputs.PROFILE
AUTHORITATIVE_OBSERVATION_PROFILES = click_observation_inputs.PROFILES
AUTHORITATIVE_PROFILE_BACKENDS = click_observation_inputs.profiles.BACKENDS
AUTHORITATIVE_OBSERVATION_FIELDS = frozenset({
    "provider", "status", "profile", "paths", "external_access",
    "child_processes", "process_tree_complete", "backend", "companion",
    "binding", "inputs", "ineligibility_reasons",
})
AUTHORITATIVE_BINDING_FIELDS = frozenset({
    "evidence_key", "check_digest", "mutation_revision", "cwd_digest",
    "workspace_root_digest", "workspace_tree_digest", "environment_digest",
    "executable_digest", "host_coverage_digest", "policy_digest",
    "shard_digest", "contract_digest", "execution_digest",
})
AUTHORITATIVE_CURRENT_BINDING_FIELDS = frozenset({
    "evidence_key", "check_digest", "cwd_digest", "workspace_root_digest",
    "environment_digest", "executable_digest", "host_coverage_digest",
    "policy_digest", "shard_digest",
})
AUTHORITATIVE_BACKEND_FIELDS = frozenset({"name", "version", "digest"})
AUTHORITATIVE_COMPANION_FIELDS = frozenset({
    "artifact_digest", "source_digest", "compiler_digest",
})
AUTHORITATIVE_INELIGIBILITY_REASONS = frozenset({
    "backend-changed", "backend-unavailable", "capture-failed",
    "child-process-unsupported", "descriptor-operation-needs-review",
    "concurrent-execution-unsupported",
    "dynamic-runtime-introspection", "event-loss", "external-or-native-input",
    "inherited-descriptor-input", "input-snapshot-failed",
    "native-companion-unavailable", "native-profile-unavailable",
    "observer-introspection-or-tampering", "process-tree-incomplete",
    "time-random-input", "unresolved-event", "unsupported-command",
    "unsupported-runtime",
})
# Dynamic behavior the native profile observed but cannot prove equivalent
# across runs. File inputs of the whole followed process tree were still
# snapshotted, so the observation can back an explicitly conditional receipt:
# reuse only while every observed input is unchanged, disclosed as unproven.
AUTHORITATIVE_CONDITIONAL_REASONS = frozenset({
    "child-process-unsupported",
    "concurrent-execution-unsupported",
    "dynamic-runtime-introspection",
})
AUTHORITATIVE_OBSERVATION_STATUSES = frozenset({"complete", "conditional", "failed", "unavailable"})
MAX_CONFIG_BYTES = 256 * 1024

SHADOW_OBSERVER_SCHEMA_VERSION = 1
SHADOW_OBSERVER_MODE = "shadow"
SHADOW_OBSERVER_STATUSES = frozenset(
    {"complete", "partial", "failed", "unavailable"}
)
SHADOW_OBSERVER_INPUT_KINDS = frozenset({"directory", "file", "missing"})
SHADOW_OBSERVER_OPERATIONS = frozenset(
    {"enumerate", "execute", "metadata", "read"}
)
SHADOW_OBSERVER_REASONS = frozenset(
    {
        "collector-failed",
        "collector-unavailable",
        "external-input",
        "observation-partial",
        "process-tree-incomplete",
        "shadow-mode",
        "unresolved-event",
    }
)
SHADOW_OBSERVER_FIELDS = frozenset(
    {
        "version",
        "mode",
        "status",
        "binding",
        "backend",
        "inputs",
        "external_input_count",
        "unresolved_event_count",
        "child_process_count",
        "process_tree_complete",
        "command_duration_ms",
        "observer_overhead_ms",
        "authoritative",
        "reuse_authorized",
        "ineligibility_reasons",
    }
)
SHADOW_OBSERVER_BINDING_FIELDS = frozenset(
    {"evidence_key", "check_digest", "mutation_revision"}
)
SHADOW_OBSERVER_BACKEND_FIELDS = frozenset({"name", "version", "digest"})
SHADOW_OBSERVER_INPUT_FIELDS = frozenset({"path", "kind", "operations"})
MAX_SHADOW_OBSERVER_BYTES = 256 * 1024
MAX_SHADOW_OBSERVER_INPUTS = 4_096
MAX_SHADOW_OBSERVER_PATH_BYTES = 4_096
MAX_SHADOW_OBSERVER_TEXT_CHARS = 256
MAX_JSON_SAFE_INTEGER = (1 << 53) - 1

_SHA256_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SHADOW_BACKEND_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SHADOW_BACKEND_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")

GitCapture = Callable[[Path, list[str]], bytes | None]


def _digest(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _canonical_config_bytes(value: bytes) -> bytes:
    """Make a committed text manifest stable across Git checkout EOL modes."""
    return value.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _group_digest(checks: list[dict[str, Any]]) -> str:
    payload: list[dict[str, list[str]]] = []
    for check in checks:
        argv = check.get("argv") if isinstance(check, dict) else None
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(item, str) or not item for item in argv)
        ):
            return ""
        payload.append({"argv": list(argv)})
    return _digest({"checks": payload}) if payload else ""


def manifest_group_digest(value: Any) -> str:
    """Return the canonical digest for one repository manifest argv group."""
    if not isinstance(value, list) or not value:
        return ""
    checks: list[dict[str, Any]] = []
    for argv in value:
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(item, str) or not item for item in argv)
        ):
            return ""
        checks.append({"argv": list(argv)})
    return _group_digest(checks)


# Compatibility for releases that imported the prior private helper.
_manifest_group_digest = manifest_group_digest


def _valid_pattern(pattern: Any) -> bool:
    if (
        not isinstance(pattern, str)
        or not pattern
        or "\x00" in pattern
        or "\\" in pattern
        or pattern.startswith(("/", "!", "./", "../"))
        or any(character in pattern for character in "?[]")
    ):
        return False
    candidate = pattern[:-1] if pattern.endswith("/") else pattern
    if not candidate or candidate in {".", ".."}:
        return False
    parts = candidate.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return all("**" not in part or part == "**" for part in parts)


def normalize_patterns(value: Any) -> tuple[tuple[str, ...] | None, str]:
    """Validate and normalize one approved dependency declaration."""
    if not isinstance(value, list) or not value:
        return None, "must be a non-empty list of repository-relative patterns"
    if any(not _valid_pattern(pattern) for pattern in value):
        return (
            None,
            "must use deterministic repository-relative patterns; `*` stays in one "
            "path segment, `**` must be a complete segment, and `?`, character "
            "classes, absolute paths, traversal, and backslashes are not accepted",
        )
    if len(set(value)) != len(value):
        return None, "must not contain duplicate patterns"
    return tuple(sorted(value)), ""


def patterns_digest(patterns: Iterable[str]) -> str:
    normalized, error = normalize_patterns(list(patterns))
    return "" if error or normalized is None else _digest({"patterns": normalized})


def _segment_matches(pattern: str, value: str) -> bool:
    return fnmatch.fnmatchcase(value, pattern)


def _glob_matches(
    pattern_parts: tuple[str, ...], path_parts: tuple[str, ...]
) -> bool:
    @lru_cache(maxsize=None)
    def match(pattern_index: int, path_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        head = pattern_parts[pattern_index]
        if head == "**":
            return match(pattern_index + 1, path_index) or bool(
                path_index < len(path_parts)
                and match(pattern_index, path_index + 1)
            )
        return bool(
            path_index < len(path_parts)
            and _segment_matches(head, path_parts[path_index])
            and match(pattern_index + 1, path_index + 1)
        )

    return match(0, 0)


def _matches(pattern: str, relative: str) -> bool:
    if pattern.endswith("/"):
        return relative.startswith(pattern)
    return _glob_matches(tuple(pattern.split("/")), tuple(relative.split("/")))


def path_matches(pattern: str, relative: str) -> bool:
    """Match one already-normalized repository pattern against one path."""
    return bool(
        _valid_pattern(pattern)
        and _safe_relative_path(relative)
        and _matches(pattern, relative)
    )


def _pattern_expands_repository_members(pattern: str) -> bool:
    """Return whether a manifest pattern names a set rather than one path.

    A complete runtime observation can safely refine these conservative
    discovery envelopes to the members that the check actually touched.
    Concrete contract and manifest paths remain hard dependencies.
    """
    return pattern.endswith("/") or "*" in pattern


def _safe_relative_path(relative: str, *, directory: bool = False) -> bool:
    candidate = relative[:-1] if directory and relative.endswith("/") else relative
    if not candidate or "\x00" in candidate or "\\" in candidate:
        return False
    path = PurePosixPath(candidate)
    return not path.is_absolute() and all(
        part not in {".", "..", ""} for part in path.parts
    )


def receipt_paths_are_valid(value: Any) -> bool:
    return bool(
        isinstance(value, list)
        and value
        and value == sorted(set(value))
        and all(
            isinstance(relative, str)
            and _safe_relative_path(relative, directory=relative.endswith("/"))
            for relative in value
        )
    )


def observation_paths_are_valid(value: Any) -> bool:
    """Return whether observed paths are canonical repository-relative inputs."""
    return bool(
        isinstance(value, list)
        and value == sorted(set(value))
        and all(
            isinstance(relative, str)
            and _safe_relative_path(relative, directory=relative.endswith("/"))
            for relative in value
        )
    )


def dependency_observation(
    paths: Iterable[str] = (),
    *,
    status: str = "complete",
    external_access: bool = False,
    child_processes: int = 0,
    process_tree_complete: bool = True,
) -> dict[str, Any]:
    """Build one content-free runtime dependency observation receipt.

    The observer reports repository-relative paths only. External input is
    represented as a boolean because absolute host paths must not leak into
    persisted Click state. A completed check may still have an incomplete
    observation; callers must use :func:`dependency_observation_is_complete`
    before reusing evidence.
    """
    if isinstance(paths, (str, bytes)):
        raise ValueError("runtime dependency paths must be an iterable of paths")
    try:
        raw_paths = list(paths)
        normalized_paths = sorted(set(raw_paths))
    except (TypeError, ValueError) as error:
        raise ValueError("invalid runtime dependency observation paths") from error
    receipt = {
        "provider": OBSERVATION_PROVIDER_NAME,
        "status": status,
        "paths": normalized_paths,
        "external_access": external_access,
        "child_processes": child_processes,
        "process_tree_complete": process_tree_complete,
    }
    if not dependency_observation_is_valid(receipt):
        raise ValueError("invalid runtime dependency observation")
    return receipt


def unavailable_dependency_observation(*, failed: bool = False) -> dict[str, Any]:
    """Return an explicit fail-closed observation for an absent/failed tracer."""
    return dependency_observation(
        status="failed" if failed else "unavailable",
        process_tree_complete=False,
    )


def dependency_observation_is_valid(value: Any) -> bool:
    if conditional_dependency_observation_is_valid(value):
        return True
    if authoritative_dependency_observation_is_valid(value):
        return True
    if not isinstance(value, dict) or set(value) != OBSERVATION_FIELDS:
        return False
    child_processes = value.get("child_processes")
    return bool(
        value.get("provider") == OBSERVATION_PROVIDER_NAME
        and value.get("status") in OBSERVATION_STATUSES
        and observation_paths_are_valid(value.get("paths"))
        and isinstance(value.get("external_access"), bool)
        and isinstance(child_processes, int)
        and not isinstance(child_processes, bool)
        and child_processes >= 0
        and isinstance(value.get("process_tree_complete"), bool)
    )


def dependency_observation_is_complete(value: Any) -> bool:
    """Whether observation supports reuse at its declared confidence level."""
    if conditional_dependency_observation_is_valid(value):
        return True
    if authoritative_dependency_observation_is_valid(value):
        return (
            authoritative_dependency_observation_is_complete(value)
            or authoritative_dependency_observation_is_conditional(value)
        )
    return bool(
        dependency_observation_is_valid(value)
        and value.get("status") == "complete"
        and value.get("external_access") is False
        and value.get("process_tree_complete") is True
    )


def dependency_observation_digest(value: Any) -> str:
    return _digest(value) if dependency_observation_is_valid(value) else ""


def combine_dependency_observations(values: Iterable[Any]) -> dict[str, Any]:
    """Union per-check observations for one evidence source."""
    observations = list(values)
    if not observations:
        return unavailable_dependency_observation()
    # V2 observations bind one original execution and are never merged from
    # caller-provided JSON.  The supported authoritative profile uses exactly
    # one command per evidence source.
    if any(
        not dependency_observation_is_valid(value)
        or value.get("provider") != OBSERVATION_PROVIDER_NAME
        for value in observations
    ):
        return unavailable_dependency_observation(failed=True)
    statuses = {str(value["status"]) for value in observations}
    status = (
        "complete"
        if statuses == {"complete"}
        else "failed"
        if "failed" in statuses
        else "unavailable"
    )
    return dependency_observation(
        {
            relative
            for value in observations
            for relative in value["paths"]
        },
        status=status,
        external_access=any(value["external_access"] for value in observations),
        child_processes=sum(value["child_processes"] for value in observations),
        process_tree_complete=all(
            value["process_tree_complete"] for value in observations
        ),
    )


def _authoritative_digest(value: Any) -> bool:
    return bool(isinstance(value, str) and _SHA256_DIGEST.fullmatch(value))


def authoritative_dependency_observation(
    *,
    status: str,
    paths: Iterable[str],
    child_processes: int,
    process_tree_complete: bool,
    backend: dict[str, Any] | None,
    companion: dict[str, Any] | None,
    binding: dict[str, Any],
    inputs: list[dict[str, Any]],
    ineligibility_reasons: Iterable[str] = (),
    profile: str = AUTHORITATIVE_OBSERVATION_PROFILE,
) -> dict[str, Any]:
    value = {
        "provider": AUTHORITATIVE_OBSERVATION_PROVIDER_NAME,
        "status": status,
        "profile": profile,
        "paths": sorted(set(paths)),
        "external_access": False,
        "child_processes": child_processes,
        "process_tree_complete": process_tree_complete,
        "backend": backend,
        "companion": companion,
        "binding": binding,
        "inputs": inputs,
        "ineligibility_reasons": sorted(set(ineligibility_reasons)),
    }
    if not authoritative_dependency_observation_is_valid(value):
        raise ValueError("invalid authoritative dependency observation")
    return value


def authoritative_dependency_observation_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != AUTHORITATIVE_OBSERVATION_FIELDS:
        return False
    if (
        value.get("provider") != AUTHORITATIVE_OBSERVATION_PROVIDER_NAME
        or value.get("profile") not in AUTHORITATIVE_OBSERVATION_PROFILES
        or value.get("status") not in AUTHORITATIVE_OBSERVATION_STATUSES
        or not observation_paths_are_valid(value.get("paths"))
        or value.get("external_access") is not False
        or not isinstance(value.get("child_processes"), int)
        or isinstance(value.get("child_processes"), bool)
        or value.get("child_processes", -1) < 0
        or not isinstance(value.get("process_tree_complete"), bool)
    ):
        return False
    backend = value.get("backend")
    companion = value.get("companion")
    expected_backend = AUTHORITATIVE_PROFILE_BACKENDS.get(value.get("profile"))
    if (
        expected_backend is None
        or
        not isinstance(backend, dict)
        or set(backend) != AUTHORITATIVE_BACKEND_FIELDS
        or backend.get("name") != expected_backend[0]
        or (
            expected_backend[1] is not None
            and backend.get("version") != expected_backend[1]
        )
        or not isinstance(backend.get("version"), str)
        or _SHADOW_BACKEND_VERSION.fullmatch(backend["version"]) is None
        or not _authoritative_digest(backend.get("digest"))
        or not isinstance(companion, dict)
        or set(companion) != AUTHORITATIVE_COMPANION_FIELDS
        or any(not _authoritative_digest(companion.get(field)) for field in companion)
    ):
        return False
    binding = value.get("binding")
    if (
        not isinstance(binding, dict)
        or set(binding) != AUTHORITATIVE_BINDING_FIELDS
        or not isinstance(binding.get("mutation_revision"), int)
        or isinstance(binding.get("mutation_revision"), bool)
        or binding.get("mutation_revision", -1) < 0
        or any(
            not _authoritative_digest(binding.get(field))
            for field in AUTHORITATIVE_BINDING_FIELDS - {"mutation_revision"}
        )
    ):
        return False
    reasons = value.get("ineligibility_reasons")
    if (
        not isinstance(reasons, list)
        or reasons != sorted(set(reasons))
        or any(reason not in AUTHORITATIVE_INELIGIBILITY_REASONS for reason in reasons)
        or not isinstance(value.get("inputs"), list)
    ):
        return False
    complete = value.get("status") == "complete"
    conditional = value.get("status") == "conditional"
    if complete != (not reasons):
        return False
    if conditional and (
        not reasons
        or any(reason not in AUTHORITATIVE_CONDITIONAL_REASONS for reason in reasons)
    ):
        return False
    if complete and (
        value.get("process_tree_complete") is not True
        or value.get("child_processes") != 0
        or not click_observation_inputs.records_valid(value.get("inputs"))
    ):
        return False
    if conditional and (
        value.get("process_tree_complete") is not True
        or not click_observation_inputs.records_valid(value.get("inputs"))
    ):
        return False
    if not complete and not conditional and value.get("inputs") and not click_observation_inputs.records_valid(value["inputs"]):
        return False
    projected = []
    for row in value.get("inputs", []):
        if row.get("root") != "project" or not row.get("path"):
            continue
        projected.append(
            row["path"] + "/" if row.get("kind") == "directory" else row["path"]
        )
    return value.get("paths") == sorted(set(projected))


def authoritative_dependency_observation_is_complete(value: Any) -> bool:
    return bool(
        authoritative_dependency_observation_is_valid(value)
        and value.get("status") == "complete"
        and value.get("process_tree_complete") is True
        and value.get("child_processes") == 0
        and not value.get("ineligibility_reasons")
    )


def authoritative_dependency_observation_is_conditional(value: Any) -> bool:
    """A native observation whose only gaps are disclosed dynamic categories."""
    return bool(
        authoritative_dependency_observation_is_valid(value)
        and value.get("status") == "conditional"
    )


def conditional_observation_is_valid(value: Any) -> bool:
    """Either conditional provider: reuse is explicitly limited to observed inputs."""
    return bool(
        conditional_dependency_observation_is_valid(value)
        or authoritative_dependency_observation_is_conditional(value)
    )


def conditional_authority_source(value: Any) -> str:
    if conditional_dependency_observation_is_valid(value):
        return "conditional-js-observation"
    if authoritative_dependency_observation_is_conditional(value):
        return "conditional-python-observation"
    return ""


def conditional_observer():
    if __package__:
        from . import click_conditional_observer
    else:
        import click_conditional_observer
    return click_conditional_observer


def conditional_dependency_observation_is_valid(value: Any) -> bool:
    return bool(isinstance(value, dict)
                and value.get("provider") == "conditional-js-observation-v1"
                and conditional_observer().valid(value))


def bound_dependency_observation_is_reusable(value: Any) -> bool:
    return (authoritative_dependency_observation_is_complete(value)
            or authoritative_dependency_observation_is_conditional(value)
            or conditional_dependency_observation_is_valid(value))


def bound_dependency_observation_matches(value: Any, **kwargs) -> bool:
    if conditional_dependency_observation_is_valid(value):
        return conditional_observer().matches(value, **kwargs)
    return authoritative_dependency_observation_matches(value, **kwargs)


def authoritative_dependency_observation_matches(
    value: Any,
    *,
    project: Path,
    runtime: Any,
    binding: Any,
) -> bool:
    if (
        not authoritative_dependency_observation_is_valid(value)
        or not isinstance(runtime, dict)
        or set(runtime) != {
            "version", "profile", "artifact_id", "artifact_digest", "source_digest",
            "compiler_digest", "backend",
        }
        or runtime.get("version") != 1
        or runtime.get("profile") not in AUTHORITATIVE_OBSERVATION_PROFILES
        or not isinstance(binding, dict)
        or set(binding) != AUTHORITATIVE_CURRENT_BINDING_FIELDS
        or any(not _authoritative_digest(binding.get(field)) for field in binding)
    ):
        return False
    expected_companion = {
        field: runtime.get(field)
        for field in AUTHORITATIVE_COMPANION_FIELDS
    }
    if (
        value.get("profile") != runtime.get("profile")
        or value.get("backend") != runtime.get("backend")
        or value.get("companion") != expected_companion
    ):
        return False
    observed_binding = value["binding"]
    if any(observed_binding.get(field) != binding[field] for field in binding):
        return False
    if (
        authoritative_dependency_observation_is_complete(value)
        or authoritative_dependency_observation_is_conditional(value)
    ):
        artifact_id = runtime.get("artifact_id")
        if not isinstance(artifact_id, str):
            return False
        return click_observation_inputs.records_current(
            project,
            artifact_id,
            value["inputs"],
            profile=str(runtime["profile"]),
        )
    return True


def _shadow_text(
    value: Any,
    *,
    field: str,
    pattern: re.Pattern[str] | None = None,
) -> tuple[str | None, str]:
    if not isinstance(value, str) or not value:
        return None, f"{field} must be a non-empty string"
    if len(value) > MAX_SHADOW_OBSERVER_TEXT_CHARS:
        return None, f"{field} exceeds the character limit"
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None, f"{field} must not contain control characters"
    if pattern is not None and pattern.fullmatch(value) is None:
        return None, f"{field} has an invalid format"
    return value, ""


def _shadow_count(value: Any, *, field: str) -> tuple[int | None, str]:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > MAX_JSON_SAFE_INTEGER
    ):
        return None, f"{field} must be a non-negative JSON-safe integer"
    return value, ""


def _normalize_shadow_binding(value: Any) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(value, dict) or set(value) != SHADOW_OBSERVER_BINDING_FIELDS:
        return None, "binding must contain only the Observer v1 binding fields"
    evidence_key, error = _shadow_text(
        value.get("evidence_key"),
        field="binding.evidence_key",
        pattern=_SHA256_DIGEST,
    )
    if error:
        return None, error
    check_digest, error = _shadow_text(
        value.get("check_digest"), field="binding.check_digest", pattern=_SHA256_DIGEST
    )
    if error:
        return None, error
    mutation_revision, error = _shadow_count(
        value.get("mutation_revision"), field="binding.mutation_revision"
    )
    if error:
        return None, error
    return {
        "evidence_key": evidence_key,
        "check_digest": check_digest,
        "mutation_revision": mutation_revision,
    }, ""


def _normalize_shadow_backend(
    value: Any, *, status: str
) -> tuple[dict[str, Any] | None, str]:
    if status == "unavailable":
        if value is not None:
            return None, "backend must be null when observation is unavailable"
        return None, ""
    if not isinstance(value, dict) or set(value) != SHADOW_OBSERVER_BACKEND_FIELDS:
        return None, "backend must contain only the Observer v1 backend fields"
    name, error = _shadow_text(
        value.get("name"), field="backend.name", pattern=_SHADOW_BACKEND_NAME
    )
    if error:
        return None, error
    version, error = _shadow_text(
        value.get("version"),
        field="backend.version",
        pattern=_SHADOW_BACKEND_VERSION,
    )
    if error:
        return None, error
    digest, error = _shadow_text(
        value.get("digest"), field="backend.digest", pattern=_SHA256_DIGEST
    )
    if error:
        return None, error
    return {"name": name, "version": version, "digest": digest}, ""


def _shadow_path_is_canonical(path: str, *, kind: str) -> bool:
    directory_marker = path.endswith("/")
    candidate = path[:-1] if directory_marker else path
    if any(ord(character) < 32 or ord(character) == 127 for character in path):
        return False
    try:
        encoded = path.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if len(encoded) > MAX_SHADOW_OBSERVER_PATH_BYTES:
        return False
    if not _safe_relative_path(path, directory=directory_marker):
        return False
    if PurePosixPath(candidate).as_posix() != candidate:
        return False
    if kind == "directory" and not directory_marker:
        return False
    if kind == "file" and directory_marker:
        return False
    return True


def _normalize_shadow_inputs(value: Any) -> tuple[list[dict[str, Any]] | None, str]:
    if not isinstance(value, list):
        return None, "inputs must be a list"
    if len(value) > MAX_SHADOW_OBSERVER_INPUTS:
        return None, "inputs exceed the entry limit"
    merged: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != SHADOW_OBSERVER_INPUT_FIELDS:
            return None, "each input must contain only the Observer v1 input fields"
        path = item.get("path")
        kind = item.get("kind")
        operations = item.get("operations")
        if not isinstance(path, str) or not path:
            return None, "input.path must be a non-empty string"
        if not isinstance(kind, str) or kind not in SHADOW_OBSERVER_INPUT_KINDS:
            return None, "input.kind is not supported"
        if not _shadow_path_is_canonical(path, kind=kind):
            return None, "input.path must be a canonical repository-relative path"
        if (
            not isinstance(operations, list)
            or not operations
            or len(operations) > len(SHADOW_OBSERVER_OPERATIONS)
            or any(
                not isinstance(operation, str)
                or operation not in SHADOW_OBSERVER_OPERATIONS
                for operation in operations
            )
        ):
            return (
                None,
                "input.operations must be a non-empty list of supported operations",
            )
        existing = merged.get(path)
        if existing is not None and existing["kind"] != kind:
            return None, "one input path must not have conflicting kinds"
        if existing is None:
            merged[path] = {
                "path": path,
                "kind": kind,
                "operations": set(operations),
            }
        else:
            existing["operations"].update(operations)
    return [
        {
            "path": path,
            "kind": merged[path]["kind"],
            "operations": sorted(merged[path]["operations"]),
        }
        for path in sorted(merged)
    ], ""


def _shadow_ineligibility_reasons(
    *,
    status: str,
    external_input_count: int,
    unresolved_event_count: int,
    process_tree_complete: bool,
) -> list[str]:
    reasons = {"shadow-mode"}
    if status == "failed":
        reasons.add("collector-failed")
    elif status == "unavailable":
        reasons.add("collector-unavailable")
    elif status == "partial":
        reasons.add("observation-partial")
    if external_input_count:
        reasons.add("external-input")
    if unresolved_event_count:
        reasons.add("unresolved-event")
    if not process_tree_complete:
        reasons.add("process-tree-incomplete")
    return sorted(reasons)


def normalize_shadow_observer_record(
    value: Any,
) -> tuple[dict[str, Any] | None, str]:
    """Validate and canonicalize one content-free Shadow Observer v1 record.

    This pure contract does not feed verification, evidence reuse, approval, or
    completion. Strict consumers should additionally require
    :func:`shadow_observer_record_is_valid` so non-canonical input fails closed.
    """
    if not isinstance(value, dict) or set(value) != SHADOW_OBSERVER_FIELDS:
        return None, "record must contain only the Observer v1 fields"
    if value.get("version") != SHADOW_OBSERVER_SCHEMA_VERSION:
        return None, "unsupported Observer schema version"
    if value.get("mode") != SHADOW_OBSERVER_MODE:
        return None, "Observer v1 records must use shadow mode"
    status = value.get("status")
    if not isinstance(status, str) or status not in SHADOW_OBSERVER_STATUSES:
        return None, "unsupported Observer status"

    binding, error = _normalize_shadow_binding(value.get("binding"))
    if error:
        return None, error
    backend, error = _normalize_shadow_backend(value.get("backend"), status=status)
    if error:
        return None, error
    inputs, error = _normalize_shadow_inputs(value.get("inputs"))
    if error:
        return None, error

    counts: dict[str, int] = {}
    for field in (
        "external_input_count",
        "unresolved_event_count",
        "child_process_count",
        "command_duration_ms",
        "observer_overhead_ms",
    ):
        count, error = _shadow_count(value.get(field), field=field)
        if error or count is None:
            return None, error
        counts[field] = count
    process_tree_complete = value.get("process_tree_complete")
    if not isinstance(process_tree_complete, bool):
        return None, "process_tree_complete must be a boolean"
    if value.get("authoritative") is not False:
        return None, "Shadow Observer records must never be authoritative"
    if value.get("reuse_authorized") is not False:
        return None, "Shadow Observer records must never authorize reuse"
    reasons = value.get("ineligibility_reasons")
    if (
        not isinstance(reasons, list)
        or len(reasons) > len(SHADOW_OBSERVER_REASONS)
        or any(
            not isinstance(reason, str) or reason not in SHADOW_OBSERVER_REASONS
            for reason in reasons
        )
    ):
        return None, "ineligibility_reasons contains an unsupported value"
    if counts["observer_overhead_ms"] > counts["command_duration_ms"]:
        return None, "observer overhead must not exceed command duration"
    if status == "complete" and (
        not process_tree_complete or counts["unresolved_event_count"]
    ):
        return None, "complete observation requires complete process coverage"
    if status == "partial" and (
        process_tree_complete and not counts["unresolved_event_count"]
    ):
        return None, "partial observation must identify incomplete coverage"
    if status == "failed" and process_tree_complete:
        return None, "failed observation cannot claim complete process coverage"
    if status == "unavailable" and (
        inputs
        or counts["external_input_count"]
        or counts["unresolved_event_count"]
        or counts["child_process_count"]
        or process_tree_complete
    ):
        return None, "unavailable observation cannot claim collected events"

    normalized_reasons = _shadow_ineligibility_reasons(
        status=status,
        external_input_count=counts["external_input_count"],
        unresolved_event_count=counts["unresolved_event_count"],
        process_tree_complete=process_tree_complete,
    )
    normalized = {
        "version": SHADOW_OBSERVER_SCHEMA_VERSION,
        "mode": SHADOW_OBSERVER_MODE,
        "status": status,
        "binding": binding,
        "backend": backend,
        "inputs": inputs,
        "external_input_count": counts["external_input_count"],
        "unresolved_event_count": counts["unresolved_event_count"],
        "child_process_count": counts["child_process_count"],
        "process_tree_complete": process_tree_complete,
        "command_duration_ms": counts["command_duration_ms"],
        "observer_overhead_ms": counts["observer_overhead_ms"],
        "authoritative": False,
        "reuse_authorized": False,
        "ineligibility_reasons": normalized_reasons,
    }
    encoded = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if len(encoded) > MAX_SHADOW_OBSERVER_BYTES:
        return None, "record exceeds the serialized byte limit"
    return normalized, ""


def shadow_observer_record(
    *,
    evidence_key: str,
    check_digest: str,
    mutation_revision: int,
    backend_name: str | None,
    backend_version: str = "",
    backend_digest: str = "",
    inputs: Iterable[dict[str, Any]] = (),
    status: str = "complete",
    external_input_count: int = 0,
    unresolved_event_count: int = 0,
    child_process_count: int = 0,
    process_tree_complete: bool = True,
    command_duration_ms: int = 0,
    observer_overhead_ms: int = 0,
) -> dict[str, Any]:
    """Build a canonical, permanently non-authoritative shadow record."""
    if isinstance(inputs, (str, bytes)):
        raise ValueError("Observer inputs must be an iterable of input records")
    try:
        raw_inputs = list(inputs)
    except TypeError as error:
        raise ValueError(
            "Observer inputs must be an iterable of input records"
        ) from error
    raw = {
        "version": SHADOW_OBSERVER_SCHEMA_VERSION,
        "mode": SHADOW_OBSERVER_MODE,
        "status": status,
        "binding": {
            "evidence_key": evidence_key,
            "check_digest": check_digest,
            "mutation_revision": mutation_revision,
        },
        "backend": (
            None
            if backend_name is None
            else {
                "name": backend_name,
                "version": backend_version,
                "digest": backend_digest,
            }
        ),
        "inputs": raw_inputs,
        "external_input_count": external_input_count,
        "unresolved_event_count": unresolved_event_count,
        "child_process_count": child_process_count,
        "process_tree_complete": process_tree_complete,
        "command_duration_ms": command_duration_ms,
        "observer_overhead_ms": observer_overhead_ms,
        "authoritative": False,
        "reuse_authorized": False,
        "ineligibility_reasons": [],
    }
    normalized, error = normalize_shadow_observer_record(raw)
    if error or normalized is None:
        raise ValueError(error or "invalid Shadow Observer record")
    return normalized


def shadow_observer_record_is_valid(value: Any) -> bool:
    normalized, error = normalize_shadow_observer_record(value)
    return not error and normalized == value


def shadow_observer_record_digest(value: Any) -> str:
    return _digest(value) if shadow_observer_record_is_valid(value) else ""


def _inside(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def _dependency_closure(
    root: Path,
    matched: set[str],
    repository_paths: set[str],
) -> set[str] | None:
    """Add safe in-repository symlink targets to the dependency set."""
    root = root.resolve()
    closure = set(matched)
    pending = list(matched)
    while pending:
        relative = pending.pop()
        directory_marker = relative.endswith("/")
        target = root / (relative[:-1] if directory_marker else relative)
        try:
            metadata = target.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return None
        if not stat.S_ISLNK(metadata.st_mode):
            continue
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
        resolved_relative = resolved.relative_to(root).as_posix()
        if resolved.is_file():
            additions = {resolved_relative}
        elif resolved.is_dir():
            prefix = f"{resolved_relative}/" if resolved_relative != "." else ""
            additions = {
                *(
                    {f"{resolved_relative}/"}
                    if resolved_relative != "."
                    else set()
                ),
                *{
                    candidate
                    for candidate in repository_paths
                    if not prefix or candidate.startswith(prefix)
                },
            }
        else:
            return None
        for addition in additions - closure:
            if not _safe_relative_path(
                addition, directory=addition.endswith("/")
            ):
                return None
            closure.add(addition)
            pending.append(addition)
    return closure


def _hash_path(hasher: Any, root: Path, relative: str) -> bool:
    directory_marker = relative.endswith("/")
    candidate = relative[:-1] if directory_marker else relative
    encoded = os.fsencode(relative)
    hasher.update(len(encoded).to_bytes(8, "big"))
    hasher.update(encoded)
    target = root / candidate
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        hasher.update(b"missing")
        return True
    except OSError:
        return False
    hasher.update(str(stat.S_IMODE(metadata.st_mode)).encode())
    if stat.S_ISLNK(metadata.st_mode):
        try:
            link_value = os.readlink(target)
        except OSError:
            return False
        if Path(link_value).is_absolute():
            return False
        hasher.update(b"symlink\0")
        hasher.update(os.fsencode(link_value))
        return True
    if stat.S_ISDIR(metadata.st_mode) and directory_marker:
        hasher.update(b"directory\0")
        try:
            entries = sorted(os.fsencode(entry.name) for entry in target.iterdir())
        except OSError:
            return False
        hasher.update(len(entries).to_bytes(8, "big"))
        for name in entries:
            hasher.update(len(name).to_bytes(8, "big"))
            hasher.update(name)
        return True
    if not stat.S_ISREG(metadata.st_mode):
        return False
    hasher.update(b"file\0")
    try:
        with target.open("rb") as handle:
            while True:
                chunk = handle.read(128 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError:
        return False
    return True


def _load_repository(
    cwd: Path,
    git_capture: GitCapture,
) -> tuple[Path, str, dict[str, tuple[str, ...]], set[str]] | None:
    root_output = git_capture(cwd, ["rev-parse", "--show-toplevel"])
    if root_output is None:
        return None
    root = Path(os.fsdecode(root_output.strip()))
    listed = git_capture(
        root,
        ["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
    )
    if listed is None:
        return None
    repository_paths = {os.fsdecode(item) for item in listed.split(b"\0") if item}
    if any(not _safe_relative_path(relative) for relative in repository_paths):
        return None

    committed = git_capture(root, ["show", f"HEAD:{CONFIG_RELATIVE_PATH}"])
    if committed is None:
        # An uncommitted manifest is never dependency authority. Contract-only
        # receipts remain available only when no working-tree manifest exists.
        try:
            (root / CONFIG_RELATIVE_PATH).lstat()
        except FileNotFoundError:
            return root, "", {}, repository_paths
        except OSError:
            return None
        return None
    if len(committed) > MAX_CONFIG_BYTES:
        return None
    # HEAD is the authority boundary. A malformed, deleted, or otherwise
    # modified working-tree copy cannot narrow or replace the committed map.
    # If a verification command reads that copy, a complete runtime observer
    # records the path and its content change still invalidates the receipt.
    canonical_raw = _canonical_config_bytes(committed)
    try:
        value = json.loads(canonical_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or set(value) != {"version", "entries"}:
        return None
    if value.get("version") != CONFIG_VERSION:
        return None
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        return None

    entries: dict[str, tuple[str, ...]] = {}
    for entry in raw_entries:
        if not isinstance(entry, dict) or set(entry) != {"checks", "paths"}:
            return None
        group_digest = manifest_group_digest(entry.get("checks"))
        paths, error = normalize_patterns(entry.get("paths"))
        if not group_digest or group_digest in entries or error or paths is None:
            return None
        entries[group_digest] = paths
    return (
        root,
        hashlib.sha256(canonical_raw).hexdigest(),
        entries,
        repository_paths,
    )


def automatic_policy_digest(checks: list[dict[str, Any]]) -> str:
    """Built-in capture rules, never an inferred owner dependency declaration."""
    return _digest({"provider": AUTOMATIC_PROVIDER_NAME, "checks": _group_digest(checks)})


def observation_policy_bindings(
    cwd: Path,
    grouped_checks: dict[str, list[dict[str, Any]]],
    *,
    declarations: dict[str, list[str] | tuple[str, ...]] | None = None,
    git_capture: GitCapture,
) -> dict[str, str]:
    """Bind the exact check group to its approved/committed dependency policy."""
    loaded = _load_repository(cwd, git_capture)
    if loaded is None:
        return {}
    _, manifest_digest, entries, _ = loaded
    approved = declarations or {}
    output: dict[str, str] = {}
    for source_key, checks in grouped_checks.items():
        if not isinstance(source_key, str):
            continue
        manifest_patterns = entries.get(_group_digest(checks), ())
        raw_declared = approved.get(source_key, ())
        if raw_declared:
            declared_patterns, error = normalize_patterns(list(raw_declared))
            if error or declared_patterns is None:
                continue
        else:
            declared_patterns = ()
        if not declared_patterns and not manifest_patterns:
            if not manifest_digest:
                output[source_key] = automatic_policy_digest(checks)
            continue
        output[source_key] = _digest({
            "checks": [check["argv"] for check in checks],
            "contract_paths": list(declared_patterns),
            "manifest_paths": list(manifest_patterns),
        })
    return output


def receipts_for_groups(
    cwd: Path,
    grouped_checks: dict[str, list[dict[str, Any]]],
    *,
    declarations: dict[str, list[str] | tuple[str, ...]] | None = None,
    observations: dict[str, dict[str, Any]] | None = None,
    authoritative_only: bool = False,
    authoritative_runtime: dict[str, Any] | None = None,
    authoritative_bindings: dict[str, dict[str, Any]] | None = None,
    git_capture: GitCapture,
) -> dict[str, dict[str, Any]]:
    """Return manifest-plus-observation receipts, or `{}` on ambiguity."""
    loaded = _load_repository(cwd, git_capture)
    if loaded is None:
        return {}
    root, manifest_digest, entries, repository_paths = loaded
    approved = declarations or {}
    observed = observations or {}
    receipts: dict[str, dict[str, Any]] = {}
    for source_key, checks in grouped_checks.items():
        if not isinstance(source_key, str):
            continue
        group_digest = _group_digest(checks)
        manifest_patterns = entries.get(group_digest, ())
        declared_value = approved.get(source_key, ())
        if declared_value:
            declared_patterns, declaration_error = normalize_patterns(
                list(declared_value)
            )
            if declaration_error or declared_patterns is None:
                continue
        else:
            declared_patterns = ()
        automatic = not declared_patterns and not manifest_patterns
        if automatic and manifest_digest:
            continue
        if automatic:
            provider = AUTOMATIC_PROVIDER_NAME
        elif declared_patterns and manifest_patterns:
            provider = COMBINED_PROVIDER_NAME
        elif declared_patterns:
            provider = CONTRACT_PROVIDER_NAME
        else:
            provider = MANIFEST_PROVIDER_NAME
        entry_payload = {
            "checks": [check["argv"] for check in checks],
            "contract_paths": list(declared_patterns),
            "manifest_paths": list(manifest_patterns),
        }
        entry_digest = automatic_policy_digest(checks) if automatic else _digest(entry_payload)
        raw_observation = observed.get(source_key)
        if raw_observation is None:
            observation = unavailable_dependency_observation()
        elif authoritative_only:
            binding = (authoritative_bindings or {}).get(source_key)
            if (
                isinstance(binding, dict)
                and binding.get("policy_digest") == entry_digest
                and bound_dependency_observation_matches(
                    raw_observation,
                    project=root,
                    runtime=authoritative_runtime,
                    binding=binding,
                )
            ):
                observation = json.loads(json.dumps(raw_observation))
            else:
                observation = unavailable_dependency_observation(failed=True)
        elif dependency_observation_is_valid(raw_observation):
            observation = json.loads(json.dumps(raw_observation))
        else:
            observation = unavailable_dependency_observation(failed=True)
        if automatic and (not authoritative_only or not bound_dependency_observation_is_reusable(observation)):
            continue
        # Approval-bound contract paths are always hard dependencies. With a
        # complete runtime observation, expanding repository-manifest patterns
        # act as conservative discovery envelopes; the observation identifies
        # the members actually consumed. Literal manifest paths remain hard
        # inputs so known implicit dependencies are never silently discarded.
        required_manifest_patterns = (
            tuple(
                pattern
                for pattern in manifest_patterns
                if not _pattern_expands_repository_members(pattern)
            )
            if dependency_observation_is_complete(observation)
            else manifest_patterns
        )
        effective_patterns = tuple(
            sorted(set(declared_patterns) | set(required_manifest_patterns))
        )
        matched: set[str] = set()
        valid = True
        for pattern in effective_patterns:
            pattern_matches = {
                relative
                for relative in repository_paths
                if _matches(pattern, relative)
            }
            if not pattern_matches:
                valid = False
                break
            matched.update(pattern_matches)
        if not valid or effective_patterns and not matched:
            continue
        declared_closure = _dependency_closure(root, matched, repository_paths)
        if declared_closure is None:
            continue
        observed_closure = _dependency_closure(
            root, set(observation["paths"]), repository_paths
        )
        if observed_closure is None:
            continue
        resolved_paths = sorted(declared_closure | observed_closure)
        if not resolved_paths:
            continue
        hasher = hashlib.sha256()
        hasher.update(provider.encode())
        hasher.update(entry_digest.encode())
        observation_digest = dependency_observation_digest(observation)
        hasher.update(observation_digest.encode())
        for relative in resolved_paths:
            if not _hash_path(hasher, root, relative):
                valid = False
                break
        if not valid:
            continue
        receipts[source_key] = {
            "provider": provider,
            # The full manifest digest is audit metadata. Matching deliberately
            # uses the relevant normalized entry digest, so unrelated entries
            # may change without invalidating this receipt.
            "manifest_digest": manifest_digest if manifest_patterns else "",
            "entry_digest": entry_digest,
            "dependency_digest": hasher.hexdigest(),
            "resolved_paths": resolved_paths,
            "observation_digest": observation_digest,
            "observation": observation,
        }
    return receipts
