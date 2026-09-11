"""Committed broad-suite decomposition with fail-closed shard provenance.

The repository manifest authorizes only an exact parent-to-children
decomposition.  It never authorizes cross-revision reuse: child evidence keeps
using Click's existing dependency-observation and safe-change policy paths.
Missing, edited, malformed, incomplete, or racing manifests return a fallback
decision so the caller runs the original parent suite.
"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

if __package__:
    from . import click_import_bootstrap
else:
    import click_import_bootstrap

(click_dependency_cache, click_verification_adapters) = (
    click_import_bootstrap.load_siblings(
        __package__, "click_dependency_cache", "click_verification_adapters"
    )
)


CONFIG_RELATIVE_PATH = ".click/evidence-shards.json"
CONFIG_VERSION = 1
PROVIDER_NAME = "repository-evidence-shards-v1"
STATE_VERSION = 1
MAX_CONFIG_BYTES = 256 * 1024
MAX_ENTRIES = 128
MAX_SHARDS_PER_ENTRY = 64
MAX_CHECKS_PER_GROUP = 64
MAX_ARGV_ITEMS = 128
MAX_ARGUMENT_BYTES = 16 * 1024
MAX_REPOSITORY_PATHS = 50_000
MAX_PATH_BYTES = 4_096

ENTRY_FIELDS = frozenset({"checks", "inventory", "shards"})
SHARD_FIELDS = frozenset({"id", "checks", "covers"})
SOURCE_METADATA_FIELDS = frozenset(
    {
        "provider",
        "parent_source_key",
        "parent_check_digest",
        "shard_id",
        "shard_count",
        "plan_digest",
        "entry_digest",
        "inventory_digest",
        "check_digest",
    }
)
SHARD_SET_FIELDS = frozenset(
    {
        "version",
        "provider",
        "parent_source_key",
        "parent_check_digest",
        "plan_digest",
        "entry_digest",
        "inventory_digest",
        "dependency_patterns",
        "dependency_declaration_digest",
        "children",
    }
)
CHILD_FIELDS = frozenset(
    {"evidence_id", "source_key", "shard_id", "check_digest"}
)
SHARD_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SYNTHETIC_EVIDENCE_ID_PATTERN = re.compile(r"^S[0-9a-f]{31}$")

GitCapture = Callable[[Path, list[str]], bytes | None]


def _digest(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _canonical_config_bytes(value: bytes) -> bytes:
    return value.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _is_digest(value: Any) -> bool:
    return bool(isinstance(value, str) and DIGEST_PATTERN.fullmatch(value))


def _safe_relative_path(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and value
        and "\x00" not in value
        and "\\" not in value
        and len(os.fsencode(value)) <= MAX_PATH_BYTES
        and click_dependency_cache.observation_paths_are_valid([value])
    )


def _normalize_argv_group(value: Any) -> tuple[list[list[str]] | None, str]:
    if not isinstance(value, list) or not value or len(value) > MAX_CHECKS_PER_GROUP:
        return None, "checks must be a bounded non-empty argv group"
    normalized: list[list[str]] = []
    for argv in value:
        if (
            not isinstance(argv, list)
            or not argv
            or len(argv) > MAX_ARGV_ITEMS
            or any(
                not isinstance(argument, str)
                or not argument
                or "\x00" in argument
                or len(os.fsencode(argument)) > MAX_ARGUMENT_BYTES
                for argument in argv
            )
        ):
            return None, "every shard check must be a bounded direct argv array"
        normalized.append(list(argv))
    return normalized, ""


def group_digest(checks: Any) -> str:
    """Return Click's executable-only digest for a normalized check group."""
    if not isinstance(checks, list) or not checks:
        return ""
    argv_group: list[list[str]] = []
    for check in checks:
        argv = check.get("argv") if isinstance(check, dict) else None
        normalized, error = _normalize_argv_group([argv])
        if error or normalized is None:
            return ""
        argv_group.append(normalized[0])
    return _digest({"checks": [{"argv": argv} for argv in argv_group]})


def _manifest_group_digest(value: Any) -> str:
    """Compatibility wrapper around the canonical dependency-policy digest."""
    normalized, error = _normalize_argv_group(value)
    if error or normalized is None:
        return ""
    return click_dependency_cache.manifest_group_digest(normalized)


def _source_key(evidence_id: str) -> str:
    return hashlib.sha256(evidence_id.encode()).hexdigest()


def _synthetic_evidence_id(
    parent_source_key: str, entry_digest: str, shard_id: str
) -> str:
    material = f"{parent_source_key}:{entry_digest}:{shard_id}"
    return "S" + hashlib.sha256(material.encode()).hexdigest()[:31]


def _policy_file_matches(root: Path, committed: bytes) -> bool:
    target = root / CONFIG_RELATIVE_PATH
    try:
        metadata = target.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_CONFIG_BYTES:
            return False
        current = target.read_bytes()
    except OSError:
        return False
    return _canonical_config_bytes(current) == _canonical_config_bytes(committed)


def _decode_repository_paths(output: bytes) -> list[str] | None:
    values = [os.fsdecode(item) for item in output.split(b"\0") if item]
    if (
        len(values) > MAX_REPOSITORY_PATHS
        or len(set(values)) != len(values)
        or any(not _safe_relative_path(value) for value in values)
    ):
        return None
    return sorted(values)


def _matched_paths(patterns: tuple[str, ...], paths: list[str]) -> list[str]:
    return [
        relative
        for relative in paths
        if any(
            click_dependency_cache.path_matches(pattern, relative)
            for pattern in patterns
        )
    ]


def _parent_discovery_paths(
    parent_checks: list[list[str]],
    repository_paths: list[str],
    *,
    working_prefix: str,
) -> tuple[set[str] | None, str]:
    return click_verification_adapters.parent_discovery_paths(
        parent_checks, repository_paths, working_prefix=working_prefix
    )


def _normalize_entry(
    raw: Any,
    repository_paths: list[str],
    *,
    working_prefix: str,
    parent_digests: set[str],
) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(raw, dict) or set(raw) != ENTRY_FIELDS:
        return None, "entry-fields-invalid"
    parent_checks, error = _normalize_argv_group(raw.get("checks"))
    parent_digest = _manifest_group_digest(raw.get("checks"))
    if error or parent_checks is None or not parent_digest:
        return None, "parent-checks-invalid"
    if parent_digest in parent_digests:
        return None, "duplicate-parent-checks"
    parent_digests.add(parent_digest)

    inventory_patterns, error = click_dependency_cache.normalize_patterns(
        raw.get("inventory")
    )
    if error or inventory_patterns is None:
        return None, "inventory-patterns-invalid"
    inventory_paths = _matched_paths(inventory_patterns, repository_paths)
    if not inventory_paths or CONFIG_RELATIVE_PATH in inventory_paths:
        return None, "inventory-empty-or-protected"
    parent_discovery, discovery_error = _parent_discovery_paths(
        parent_checks,
        repository_paths,
        working_prefix=working_prefix,
    )
    if discovery_error:
        return None, discovery_error
    if parent_discovery is not None and not parent_discovery.issubset(
        set(inventory_paths)
    ):
        return None, "inventory-narrower-than-parent-discovery"

    raw_shards = raw.get("shards")
    if (
        not isinstance(raw_shards, list)
        or len(raw_shards) < 2
        or len(raw_shards) > MAX_SHARDS_PER_ENTRY
    ):
        return None, "shards-invalid"
    normalized_shards: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_check_digests: set[str] = set()
    coverage_counts = {relative: 0 for relative in inventory_paths}
    inventory_set = set(inventory_paths)
    for raw_shard in raw_shards:
        if not isinstance(raw_shard, dict) or set(raw_shard) != SHARD_FIELDS:
            return None, "shard-fields-invalid"
        shard_id = raw_shard.get("id")
        if (
            not isinstance(shard_id, str)
            or not SHARD_ID_PATTERN.fullmatch(shard_id)
            or shard_id in seen_ids
        ):
            return None, "shard-id-invalid"
        checks, check_error = _normalize_argv_group(raw_shard.get("checks"))
        check_digest = _manifest_group_digest(raw_shard.get("checks"))
        covers, covers_error = click_dependency_cache.normalize_patterns(
            raw_shard.get("covers")
        )
        if (
            check_error
            or checks is None
            or not check_digest
            or check_digest in seen_check_digests
            or covers_error
            or covers is None
        ):
            return None, "shard-definition-invalid"
        covered = _matched_paths(covers, repository_paths)
        if not covered or any(relative not in inventory_set for relative in covered):
            return None, "shard-coverage-outside-inventory"
        for relative in covered:
            coverage_counts[relative] += 1
        seen_ids.add(shard_id)
        seen_check_digests.add(check_digest)
        normalized_shards.append(
            {
                "id": shard_id,
                "checks": checks,
                "covers": list(covers),
                "check_digest": check_digest,
                "binding_digest": _digest({
                    "parent_check_digest": parent_digest,
                    "id": shard_id,
                    "checks": checks,
                    "covers": list(covers),
                    "paths": covered,
                }),
            }
        )
    if any(count != 1 for count in coverage_counts.values()):
        return None, "inventory-not-covered-exactly-once"

    normalized_shards.sort(key=lambda shard: str(shard["id"]))
    inventory_digest = _digest({"paths": inventory_paths})
    entry_payload = {
        "checks": parent_checks,
        "inventory": list(inventory_patterns),
        "shards": [
            {
                "id": shard["id"],
                "checks": shard["checks"],
                "covers": shard["covers"],
            }
            for shard in normalized_shards
        ],
    }
    entry_digest = _digest(entry_payload)
    return {
        "parent_check_digest": parent_digest,
        "entry_digest": entry_digest,
        "inventory_digest": inventory_digest,
        "shards": normalized_shards,
    }, ""


def _load_entries(
    cwd: Path, git_capture: GitCapture
) -> tuple[dict[str, dict[str, Any]] | None, str]:
    root_output = git_capture(cwd, ["rev-parse", "--show-toplevel"])
    if root_output is None:
        return None, "git-root-unavailable"
    root = Path(os.fsdecode(root_output.strip()))
    try:
        resolved_root = root.resolve(strict=True)
        resolved_cwd = cwd.resolve(strict=True)
        working_relative = resolved_cwd.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        return None, "git-workdir-unavailable"
    working_prefix = "" if working_relative == Path(".") else working_relative.as_posix()
    head_output = git_capture(root, ["rev-parse", "--verify", "HEAD"])
    if head_output is None:
        return None, "head-unavailable"
    head = os.fsdecode(head_output.strip())
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head) is None:
        return None, "head-invalid"
    committed = git_capture(root, ["show", f"{head}:{CONFIG_RELATIVE_PATH}"])
    if committed is None:
        return None, "manifest-not-committed"
    if len(committed) > MAX_CONFIG_BYTES or not _policy_file_matches(root, committed):
        return None, "manifest-working-copy-mismatch"
    listed = git_capture(
        root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    )
    ignored = git_capture(
        root, ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"]
    )
    if listed is None or ignored is None:
        return None, "inventory-unavailable"
    visible_paths = _decode_repository_paths(listed)
    ignored_paths = _decode_repository_paths(ignored)
    if visible_paths is None or ignored_paths is None:
        return None, "inventory-invalid-or-too-large"
    repository_paths = sorted(set(visible_paths) | set(ignored_paths))
    if len(repository_paths) > MAX_REPOSITORY_PATHS:
        return None, "inventory-invalid-or-too-large"

    canonical = _canonical_config_bytes(committed)
    try:
        value = json.loads(canonical.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "manifest-json-invalid"
    if not isinstance(value, dict) or set(value) != {"version", "entries"}:
        return None, "manifest-fields-invalid"
    if value.get("version") != CONFIG_VERSION:
        return None, "manifest-version-unsupported"
    raw_entries = value.get("entries")
    if (
        not isinstance(raw_entries, list)
        or not raw_entries
        or len(raw_entries) > MAX_ENTRIES
    ):
        return None, "manifest-entries-invalid"

    entries: dict[str, dict[str, Any]] = {}
    parent_digests: set[str] = set()
    for raw_entry in raw_entries:
        entry, error = _normalize_entry(
            raw_entry,
            repository_paths,
            working_prefix=working_prefix,
            parent_digests=parent_digests,
        )
        if error or entry is None:
            return None, error or "entry-invalid"
        entries[str(entry["parent_check_digest"])] = entry

    # Recheck both mutable inputs after parsing so a racing edit cannot become
    # decomposition authority for the prepared batch.
    listed_again = git_capture(
        root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    )
    ignored_again = git_capture(
        root, ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"]
    )
    if (
        listed_again != listed
        or ignored_again != ignored
        or not _policy_file_matches(root, committed)
    ):
        return None, "manifest-or-inventory-raced"
    return entries, ""


def resolve_plan(
    cwd: Path,
    parent_checks: list[dict[str, Any]],
    *,
    parent_source_key: str,
    git_capture: GitCapture,
) -> dict[str, Any]:
    """Resolve one exact parent group to children, or request parent fallback."""
    parent_digest = group_digest(parent_checks)
    if not _is_digest(parent_source_key) or not parent_digest:
        return {"status": "fallback", "reason": "parent-binding-invalid"}
    entries, error = _load_entries(cwd, git_capture)
    if entries is None:
        return {"status": "fallback", "reason": error or "manifest-unavailable"}
    entry = entries.get(parent_digest)
    if entry is None:
        return {"status": "unsharded", "reason": "parent-not-declared"}

    return _plan_from_entry(
        entry,
        parent_source_key=parent_source_key,
        parent_check_digest=parent_digest,
    )


def _plan_from_entry(
    entry: dict[str, Any], *, parent_source_key: str, parent_check_digest: str
) -> dict[str, Any]:
    children: list[dict[str, Any]] = []
    for shard in entry["shards"]:
        shard_id = str(shard["id"])
        evidence_id = _synthetic_evidence_id(
            parent_source_key, str(shard["binding_digest"]), shard_id
        )
        children.append(
            {
                "evidence_id": evidence_id,
                "source_key": _source_key(evidence_id),
                "shard_id": shard_id,
                "check_digest": str(shard["check_digest"]),
                "binding_digest": str(shard["binding_digest"]),
                "checks": [list(argv) for argv in shard["checks"]],
            }
        )
    plan_payload = {
        "provider": PROVIDER_NAME,
        "parent_source_key": parent_source_key,
        "parent_check_digest": parent_check_digest,
        "entry_digest": entry["entry_digest"],
        "inventory_digest": entry["inventory_digest"],
        "children": [
            {
                "source_key": child["source_key"],
                "shard_id": child["shard_id"],
                "check_digest": child["check_digest"],
            }
            for child in children
        ],
    }
    plan_digest = _digest(plan_payload)
    return {
        "status": "sharded",
        "reason": "matched",
        "provider": PROVIDER_NAME,
        "parent_source_key": parent_source_key,
        "parent_check_digest": parent_check_digest,
        "plan_digest": plan_digest,
        "entry_digest": entry["entry_digest"],
        "inventory_digest": entry["inventory_digest"],
        "children": children,
    }


def resolve_active_plan(
    cwd: Path, shard_set: dict[str, Any], *, git_capture: GitCapture
) -> dict[str, Any]:
    """Re-resolve a persisted shard set without storing the raw parent argv."""
    if not isinstance(shard_set, dict):
        return {"status": "fallback", "reason": "shard-state-invalid"}
    parent_source_key = shard_set.get("parent_source_key")
    parent_check_digest = shard_set.get("parent_check_digest")
    if not _is_digest(parent_source_key) or not _is_digest(parent_check_digest):
        return {"status": "fallback", "reason": "shard-state-invalid"}
    entries, error = _load_entries(cwd, git_capture)
    if entries is None:
        return {"status": "fallback", "reason": error or "manifest-unavailable"}
    entry = entries.get(str(parent_check_digest))
    if entry is None:
        return {"status": "fallback", "reason": "parent-not-declared"}
    return _plan_from_entry(
        entry,
        parent_source_key=str(parent_source_key),
        parent_check_digest=str(parent_check_digest),
    )


def running_plan_error(
    cwd: Path,
    evidence_state: Any,
    grouped_checks: dict[str, list[dict[str, Any]]],
    *,
    git_capture: GitCapture,
) -> str:
    """Revalidate active shard authority immediately before child execution."""
    if not isinstance(evidence_state, dict):
        return "Click Evidence Shards state is unavailable."
    shard_sets = evidence_state.get("shard_sets", {})
    if not isinstance(shard_sets, dict):
        return "Click Evidence Shards state is malformed."
    running_keys = set(grouped_checks)
    covered: set[str] = set()
    for shard_set in shard_sets.values():
        if not isinstance(shard_set, dict):
            return "Click Evidence Shards state is malformed."
        children = shard_set.get("children")
        if not isinstance(children, list):
            return "Click Evidence Shards state is malformed."
        child_by_key = {
            str(child.get("source_key", "")): child
            for child in children
            if isinstance(child, dict)
        }
        relevant = running_keys & set(child_by_key)
        if not relevant:
            continue
        current = resolve_active_plan(cwd, shard_set, git_capture=git_capture)
        if current.get("status") != "sharded" or not plan_matches_shard_set(
            current, shard_set
        ):
            return (
                "Click Evidence Shards authority changed before execution; "
                "no child check was run."
            )
        for source_key in relevant:
            child = child_by_key[source_key]
            if group_digest(grouped_checks[source_key]) != child.get("check_digest"):
                return "Click Evidence Shards child check binding changed before execution."
        covered.update(relevant)
    sources = evidence_state.get("sources", {})
    if not isinstance(sources, dict):
        return "Click Evidence Shards child registry is unavailable."
    if any(
        source_key not in covered and is_child_source(sources.get(source_key))
        for source_key in running_keys
    ):
        return "Click Evidence Shards child registry is incomplete."
    return ""


def source_metadata(plan: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": PROVIDER_NAME,
        "parent_source_key": str(plan["parent_source_key"]),
        "parent_check_digest": str(plan["parent_check_digest"]),
        "shard_id": str(child["shard_id"]),
        "shard_count": len(plan["children"]),
        "plan_digest": str(plan["plan_digest"]),
        "entry_digest": str(plan["entry_digest"]),
        "inventory_digest": str(plan["inventory_digest"]),
        "check_digest": str(child["check_digest"]),
        **({"binding_digest": child["binding_digest"]} if "binding_digest" in child else {}),
    }


def source_metadata_is_valid(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and set(value) in (SOURCE_METADATA_FIELDS, SOURCE_METADATA_FIELDS | {"binding_digest"})
        and ("binding_digest" not in value or _is_digest(value["binding_digest"]))
        and value.get("provider") == PROVIDER_NAME
        and _is_digest(value.get("parent_source_key"))
        and _is_digest(value.get("parent_check_digest"))
        and isinstance(value.get("shard_id"), str)
        and SHARD_ID_PATTERN.fullmatch(str(value.get("shard_id")))
        and isinstance(value.get("shard_count"), int)
        and not isinstance(value.get("shard_count"), bool)
        and 2 <= int(value.get("shard_count", 0)) <= MAX_SHARDS_PER_ENTRY
        and all(
            _is_digest(value.get(field))
            for field in (
                "plan_digest",
                "entry_digest",
                "inventory_digest",
                "check_digest",
            )
        )
    )


def reuse_binding(value: Any) -> dict[str, Any] | None:
    """Bind a child's inputs, leaving full-plan admission to the runner.

    Legacy evidence retains its original whole-plan binding and cannot be
    migrated by inventing a narrower identity after execution.
    """
    if not source_metadata_is_valid(value):
        return None
    if "binding_digest" not in value:
        return dict(value)
    return {key: value[key] for key in (
        "provider", "parent_source_key", "parent_check_digest", "shard_id",
        "check_digest", "binding_digest",
    )}


def _child_record(child: dict[str, Any]) -> dict[str, Any]:
    return {key: child[key] for key in CHILD_FIELDS | {"binding_digest"} if key in child}


def shard_set_for_plan(
    plan: dict[str, Any],
    *,
    dependency_patterns: list[str],
    dependency_declaration_digest: str,
) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "provider": PROVIDER_NAME,
        "parent_source_key": str(plan["parent_source_key"]),
        "parent_check_digest": str(plan["parent_check_digest"]),
        "plan_digest": str(plan["plan_digest"]),
        "entry_digest": str(plan["entry_digest"]),
        "inventory_digest": str(plan["inventory_digest"]),
        "dependency_patterns": list(dependency_patterns),
        "dependency_declaration_digest": dependency_declaration_digest,
        "children": [
            _child_record(child)
            for child in plan["children"]
        ],
    }


def plan_matches_shard_set(plan: Any, shard_set: Any) -> bool:
    if not isinstance(plan, dict) or not isinstance(shard_set, dict):
        return False
    return all(
        plan.get(field) == shard_set.get(field)
        for field in (
            "provider",
            "parent_source_key",
            "parent_check_digest",
            "plan_digest",
            "entry_digest",
            "inventory_digest",
        )
    ) and [
        _child_record(child)
        for child in plan.get("children", [])
        if isinstance(child, dict)
    ] == shard_set.get("children")


def state_is_valid(evidence_state: Any, sources: Any) -> bool:
    """Validate active parent/child registry mappings without accepting prose."""
    if not isinstance(evidence_state, dict) or not isinstance(sources, dict):
        return False
    shard_sets = evidence_state.get("shard_sets", {})
    if not isinstance(shard_sets, dict):
        return False
    child_keys: set[str] = set()
    for parent_key, shard_set in shard_sets.items():
        if (
            not _is_digest(parent_key)
            or not isinstance(shard_set, dict)
            or set(shard_set) != SHARD_SET_FIELDS
            or shard_set.get("version") != STATE_VERSION
            or shard_set.get("provider") != PROVIDER_NAME
            or shard_set.get("parent_source_key") != parent_key
            or parent_key in sources
            or not all(
                _is_digest(shard_set.get(field))
                for field in (
                    "parent_check_digest",
                    "plan_digest",
                    "entry_digest",
                    "inventory_digest",
                )
            )
        ):
            return False
        patterns = shard_set.get("dependency_patterns")
        declaration_digest = shard_set.get("dependency_declaration_digest")
        if not isinstance(patterns, list) or not isinstance(declaration_digest, str):
            return False
        if patterns:
            normalized, error = click_dependency_cache.normalize_patterns(patterns)
            if (
                error
                or normalized is None
                or list(normalized) != patterns
                or click_dependency_cache.patterns_digest(normalized)
                != declaration_digest
            ):
                return False
        elif declaration_digest:
            return False
        children = shard_set.get("children")
        if not isinstance(children, list) or not children:
            return False
        seen_ids: set[str] = set()
        seen_shard_ids: set[str] = set()
        for child in children:
            if not isinstance(child, dict) or set(child) not in (
                CHILD_FIELDS, CHILD_FIELDS | {"binding_digest"}
            ):
                return False
            evidence_id = child.get("evidence_id")
            source_key = child.get("source_key")
            shard_id = child.get("shard_id")
            check_digest = child.get("check_digest")
            if (
                not isinstance(evidence_id, str)
                or not SYNTHETIC_EVIDENCE_ID_PATTERN.fullmatch(evidence_id)
                or not _is_digest(source_key)
                or source_key != _source_key(evidence_id)
                or not isinstance(shard_id, str)
                or not SHARD_ID_PATTERN.fullmatch(shard_id)
                or not _is_digest(check_digest)
                or "binding_digest" in child and (
                    not _is_digest(child["binding_digest"])
                    or evidence_id != _synthetic_evidence_id(
                        parent_key, child["binding_digest"], shard_id
                    )
                )
                or evidence_id in seen_ids
                or shard_id in seen_shard_ids
                or source_key in child_keys
            ):
                return False
            source = sources.get(source_key)
            metadata = source.get("shard") if isinstance(source, dict) else None
            expected = {
                "provider": PROVIDER_NAME,
                "parent_source_key": parent_key,
                "parent_check_digest": shard_set["parent_check_digest"],
                "shard_id": shard_id,
                "shard_count": len(children),
                "plan_digest": shard_set["plan_digest"],
                "entry_digest": shard_set["entry_digest"],
                "inventory_digest": shard_set["inventory_digest"],
                "check_digest": check_digest,
                **({"binding_digest": child["binding_digest"]} if "binding_digest" in child else {}),
            }
            if (
                not source_metadata_is_valid(metadata)
                or metadata != expected
                or source.get("kind") != "argv"
                or any(
                    source.get(field, "") != ""
                    and source.get(field, "") != check_digest
                    for field in (
                        "reserved_check_digest",
                        "last_check_digest",
                        "locked_check_digest",
                        "verified_check_digest",
                    )
                )
            ):
                return False
            seen_ids.add(evidence_id)
            seen_shard_ids.add(shard_id)
            child_keys.add(str(source_key))
    for source_key, source in sources.items():
        if isinstance(source, dict) and "shard" in source:
            if (
                source_key not in child_keys
                or not source_metadata_is_valid(source.get("shard"))
            ):
                return False
    return True


def active_set(evidence_state: Any, parent_source_key: str) -> dict[str, Any] | None:
    shard_sets = evidence_state.get("shard_sets") if isinstance(evidence_state, dict) else None
    value = shard_sets.get(parent_source_key) if isinstance(shard_sets, dict) else None
    return value if isinstance(value, dict) else None


def is_child_source(source: Any) -> bool:
    return bool(
        isinstance(source, dict)
        and source_metadata_is_valid(source.get("shard"))
    )
