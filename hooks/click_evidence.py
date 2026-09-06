"""Prose-free evidence registry and ledger primitives for Click.

This module owns deterministic evidence identifiers, initial ledger creation,
ledger-shape validation, and current-revision lookup helpers. It deliberately
does not decide contract completion, verification profiles, Browser policy, or
when a source may transition between states; those decisions remain in the
gate that calls these mechanics.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from typing import Any

if __package__:
    from . import (
        click_change_policy,
        click_dependency_cache,
        click_evidence_shards,
        click_host_coverage,
    )
else:  # Executed directly from the bundled hooks directory.
    import click_change_policy
    import click_dependency_cache
    import click_evidence_shards
    import click_host_coverage


EVIDENCE_KINDS = ("argv", "browser", "hosted", "manual", "existing")
EVIDENCE_STATUSES = {"ready", "running", "observed", "passed", "failed", "stale"}
EVIDENCE_STATE_VERSION = 1
SUCCESSOR_EVIDENCE_FIELD = "successor_evidence"
SUCCESSOR_EVIDENCE_VERSION = 1
GUARDED_SUCCESSOR_VERSION = 2
MAX_SUCCESSOR_EVIDENCE_BYTES = 2 * 1024 * 1024
MAX_SUCCESSOR_SOURCES = 256
_SUCCESSOR_FIELDS = frozenset({
    "version", "scope_digest", "origin_evidence_session_id",
    "origin_intent_digest", "origin_revision", "origin_registry_digest",
    "captured_at", "evidence_state", "origins", "digest",
})
_SUCCESSOR_ORIGIN_FIELDS = frozenset({"batch_id", "execution_status"})
_GUARDED_SUCCESSOR_FIELDS = (
    _SUCCESSOR_FIELDS - {"origin_evidence_session_id"}
) | {"origin_contract_id"}


def evidence_key(evidence_id: str) -> str:
    """Return the prose-free key persisted for one approved evidence id."""
    return hashlib.sha256(evidence_id.encode()).hexdigest()


def registry_digest(sources: dict[str, Any]) -> str:
    """Bind the persisted source keys and kinds without storing contract prose."""
    registry = sorted(
        (key, str(source.get("kind", "")))
        for key, source in sources.items()
        if isinstance(key, str) and isinstance(source, dict)
    )
    payload = json.dumps(registry, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _fresh_source(kind: str, dependency_patterns: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "kind": kind,
        "dependency_patterns": list(dependency_patterns),
        "dependency_declaration_digest": (
            click_dependency_cache.patterns_digest(dependency_patterns)
            if dependency_patterns
            else ""
        ),
        "status": "ready",
        "verified_revision": -1,
        "attempts": 0,
        "unchanged_failure_retries": 0,
        "last_exit_code": None,
        "last_check_digest": "",
        "locked_check_digest": "",
        "reserved_units": 0,
        "reserved_check_digest": "",
        "verified_contract_digest": "",
        "verified_check_digest": "",
        "verified_units": 0,
        "verified_root": "",
        "verified_tree_digest": "",
        "verified_environment_digest": "",
        "verified_executable_digest": "",
        "verified_host_coverage": {},
        "verified_at": 0,
        "last_success_duration_ms": 0,
        "verified_dependency_provider": "",
        "verified_dependency_manifest_digest": "",
        "verified_dependency_entry_digest": "",
        "verified_dependency_digest": "",
        "verified_dependency_paths": [],
        "verified_dependency_observation_digest": "",
        "verified_dependency_observation": {},
        "dependency_reuse_count": 0,
        "last_dependency_reused_at": 0,
        "last_dependency_reused_from_revision": -1,
        "verified_safe_change_receipt": {},
        "safe_change_reuse_count": 0,
        "last_safe_change_reused_at": 0,
        "last_safe_change_reused_from_revision": -1,
        "last_safe_change_paths": [],
        "last_safe_change_path_count": 0,
        "last_safe_change_decision_digest": "",
        "successor_reuse_count": 0,
        "last_successor_reused_at": 0,
        "last_successor_origin_batch_id": "",
        "last_successor_origin_evidence_session_id": "",
        "last_successor_origin_contract_id": "",
        "last_successor_candidate_digest": "",
        "last_successor_origin_revision": -1,
        "last_successor_mode": "",
    }


def fresh_state(contract: dict[str, Any]) -> dict[str, Any]:
    """Create a prose-free evidence ledger from a validated contract."""
    verification = contract.get("verification")
    declared = verification.get("evidence") if isinstance(verification, dict) else []
    sources: dict[str, Any] = {}
    if isinstance(declared, list):
        for source in declared:
            if not isinstance(source, dict):
                continue
            source_id = source.get("id")
            kind = source.get("kind")
            if not isinstance(source_id, str) or kind not in EVIDENCE_KINDS:
                continue
            declared_patterns = source.get("dependencies", [])
            normalized_patterns, dependency_error = (
                click_dependency_cache.normalize_patterns(declared_patterns)
                if declared_patterns
                else ((), "")
            )
            if dependency_error or normalized_patterns is None:
                normalized_patterns = ()
            sources[evidence_key(source_id)] = _fresh_source(
                kind, normalized_patterns
            )
    return {
        "version": EVIDENCE_STATE_VERSION,
        "source_count": len(sources),
        "registry_digest": registry_digest(sources),
        "sources": sources,
        "shard_sets": {},
    }


def fresh_successor_evidence() -> dict[str, Any]:
    return {}


def clear_successor_receipt(source: dict[str, Any]) -> None:
    """A real current-lifecycle execution replaces previously reused lineage."""
    source.update({
        key: value for key, value in _fresh_source("argv").items()
        if key == "successor_reuse_count" or key.startswith("last_successor_")
    })


def successor_scope_digest(identity: str) -> str:
    return hashlib.sha256(identity.encode()).hexdigest()


def _successor_digest(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "digest"}
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def successor_evidence_is_valid(
    value: Any,
    *,
    expected_contract_schema_version: int,
    scope_digest: str,
) -> bool:
    guarded = isinstance(value, dict) and value.get("version") == GUARDED_SUCCESSOR_VERSION
    identity_field = "origin_contract_id" if guarded else "origin_evidence_session_id"
    identity_pattern = r"ctr_[0-9a-f]{32}" if guarded else r"evs_[0-9a-f]{32}"
    if (
        not isinstance(value, dict)
        or set(value) != (_GUARDED_SUCCESSOR_FIELDS if guarded else _SUCCESSOR_FIELDS)
        or value.get("version") not in {SUCCESSOR_EVIDENCE_VERSION, GUARDED_SUCCESSOR_VERSION}
        or isinstance(value.get("version"), bool)
        or value.get("scope_digest") != scope_digest
        or not isinstance(scope_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", scope_digest) is None
        or not isinstance(value.get(identity_field), str)
        or re.fullmatch(identity_pattern, value[identity_field])
        is None
        or not isinstance(value.get("origin_intent_digest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", value["origin_intent_digest"]) is None
        or not isinstance(value.get("origin_revision"), int)
        or isinstance(value.get("origin_revision"), bool)
        or value["origin_revision"] < 0
        or not isinstance(value.get("origin_registry_digest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", value["origin_registry_digest"]) is None
        or not isinstance(value.get("captured_at"), int)
        or isinstance(value.get("captured_at"), bool)
        or value["captured_at"] <= 0
        or not isinstance(value.get("origins"), dict)
        or not isinstance(value.get("evidence_state"), dict)
        or not isinstance(value.get("digest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", value["digest"]) is None
        or not secrets.compare_digest(value["digest"], _successor_digest(value))
        or len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        > MAX_SUCCESSOR_EVIDENCE_BYTES
    ):
        return False
    synthetic = {
        "state_schema_version": expected_contract_schema_version,
        "evidence_state": value["evidence_state"],
    }
    sources = sources_from_state(
        synthetic,
        expected_contract_schema_version=expected_contract_schema_version,
    )
    if (
        sources is None
        or not sources
        or len(sources) > MAX_SUCCESSOR_SOURCES
        or registry_digest(sources) != value["origin_registry_digest"]
        or len(value["origins"]) > len(sources)
    ):
        return False
    for key, origin in value["origins"].items():
        source = sources.get(key) if isinstance(key, str) else None
        if (
            not isinstance(origin, dict)
            or set(origin) != _SUCCESSOR_ORIGIN_FIELDS
            or not isinstance(origin.get("batch_id"), str)
            or re.fullmatch(r"[0-9a-f]{32}", origin["batch_id"]) is None
            or origin.get("execution_status") not in {"passed", "reused"}
            or not is_current(source, value["origin_revision"])
            or source.get("kind") != "argv"
            or source.get("verified_contract_digest")
            != value["origin_intent_digest"]
        ):
            return False
    return bool(value["origins"])


def carry_successor_evidence(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    origins: dict[str, dict[str, str]],
    scope_digest: str,
    expected_contract_schema_version: int,
    now: int | None = None,
) -> bool:
    """Carry completed facts, never approval, claims, runner or completion state.

    The lifecycle caller must establish completion before calling this helper.
    Cross-mode carry is deliberately unsupported.
    """
    sources = sources_from_state(
        previous,
        expected_contract_schema_version=expected_contract_schema_version,
    )
    evidence_state = previous.get("evidence_state")
    verification = previous.get("verification")
    revision = (
        verification.get("mutation_revision")
        if isinstance(verification, dict)
        else None
    )
    guarded = previous.get("runtime_mode") == "guarded"
    identity_field = "origin_contract_id" if guarded else "origin_evidence_session_id"
    session_id = previous.get("contract_id" if guarded else "evidence_session_id")
    intent_digest = previous.get("intent_digest")
    if (
        current.get("runtime_mode") != previous.get("runtime_mode")
        or guarded and (
            previous.get("status") != "approved"
            or not previous.get("approved_turn_id")
            or previous.get("approved_turn_id") == previous.get("staged_turn_id")
        )
        or not sources
        or len(sources) > MAX_SUCCESSOR_SOURCES
        or not isinstance(evidence_state, dict)
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
        or not isinstance(session_id, str)
        or re.fullmatch(r"ctr_[0-9a-f]{32}" if guarded else r"evs_[0-9a-f]{32}", session_id) is None
        or not isinstance(intent_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", intent_digest) is None
    ):
        return False
    eligible = {
        key: dict(origin)
        for key, origin in origins.items()
        if key in sources
        and isinstance(origin, dict)
        and set(origin) == _SUCCESSOR_ORIGIN_FIELDS
        and is_current(sources[key], revision)
    }
    if not eligible:
        return False
    value = {
        "version": GUARDED_SUCCESSOR_VERSION if guarded else SUCCESSOR_EVIDENCE_VERSION,
        "scope_digest": scope_digest,
        identity_field: session_id,
        "origin_intent_digest": intent_digest,
        "origin_revision": revision,
        "origin_registry_digest": registry_digest(sources),
        "captured_at": int(time.time()) if now is None else now,
        "evidence_state": json.loads(json.dumps(evidence_state)),
        "origins": eligible,
        "digest": "",
    }
    value["digest"] = _successor_digest(value)
    if not successor_evidence_is_valid(
        value,
        expected_contract_schema_version=expected_contract_schema_version,
        scope_digest=scope_digest,
    ):
        return False
    current[SUCCESSOR_EVIDENCE_FIELD] = value
    return True


def successor_candidate(
    state: dict[str, Any],
    source_key: str,
    *,
    expected_contract_schema_version: int,
    scope_digest: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    value = state.get(SUCCESSOR_EVIDENCE_FIELD)
    if not successor_evidence_is_valid(
        value,
        expected_contract_schema_version=expected_contract_schema_version,
        scope_digest=scope_digest,
    ):
        return None
    assert isinstance(value, dict)
    guarded = value["version"] == GUARDED_SUCCESSOR_VERSION
    if guarded and not (
        state.get("runtime_mode") == "guarded"
        and state.get("status") == "approved"
        and state.get("approved_turn_id")
        and state.get("approved_turn_id") != state.get("staged_turn_id")
        and state.get("contract_id") != value["origin_contract_id"]
    ):
        return None
    if not guarded and state.get("runtime_mode") != "evidence":
        return None
    origin = value["origins"].get(source_key)
    source = value["evidence_state"]["sources"].get(source_key)
    if not isinstance(origin, dict) or not isinstance(source, dict):
        return None
    metadata = {
        "kind": "successor-contract" if guarded else "successor-evidence",
        "batch_id": origin["batch_id"],
        ("contract_id" if guarded else "evidence_session_id"): value[
            "origin_contract_id" if guarded else "origin_evidence_session_id"
        ],
        "candidate_digest": value["digest"],
        "origin_revision": value["origin_revision"],
    }
    return json.loads(json.dumps(source)), metadata


def _refresh_registry(evidence_state: dict[str, Any], sources: dict[str, Any]) -> None:
    evidence_state["sources"] = sources
    evidence_state["source_count"] = len(sources)
    evidence_state["registry_digest"] = registry_digest(sources)


def activate_shard_plan(
    state: dict[str, Any], parent_source_key: str, plan: dict[str, Any]
) -> tuple[dict[str, Any] | None, str]:
    """Replace one declared parent source with stable internal shard sources."""
    evidence_state = state.get("evidence_state")
    if not isinstance(evidence_state, dict):
        return None, "Click Evidence Shards registry is unavailable."
    sources = evidence_state.get("sources")
    if not isinstance(sources, dict):
        return None, "Click Evidence Shards registry is unavailable."
    existing_set = click_evidence_shards.active_set(
        evidence_state, parent_source_key
    )
    if existing_set is not None:
        if click_evidence_shards.plan_matches_shard_set(plan, existing_set):
            return sources, ""
        return None, "The active Evidence Shards plan changed unexpectedly."
    parent = sources.get(parent_source_key)
    if not isinstance(parent, dict) or parent.get("kind") != "argv":
        return None, "The broad parent evidence source is unavailable."
    if click_evidence_shards.is_child_source(parent):
        return None, "A shard child cannot become a broad parent source."
    patterns = parent.get("dependency_patterns", [])
    declaration_digest = parent.get("dependency_declaration_digest", "")
    if not isinstance(patterns, list) or not isinstance(declaration_digest, str):
        return None, "The broad parent dependency declaration is malformed."
    shard_set = click_evidence_shards.shard_set_for_plan(
        plan,
        dependency_patterns=patterns,
        dependency_declaration_digest=declaration_digest,
    )
    children = plan.get("children")
    if not isinstance(children, list) or not children:
        return None, "The Evidence Shards plan has no child checks."
    child_keys = {
        str(child.get("source_key", ""))
        for child in children
        if isinstance(child, dict)
    }
    if len(child_keys) != len(children) or any(key in sources for key in child_keys):
        return None, "The Evidence Shards child identity collided with active evidence."

    sources.pop(parent_source_key)
    for child in children:
        assert isinstance(child, dict)
        source = _fresh_source("argv", tuple(patterns))
        source["shard"] = click_evidence_shards.source_metadata(plan, child)
        sources[str(child["source_key"])] = source
    shard_sets = evidence_state.setdefault("shard_sets", {})
    if not isinstance(shard_sets, dict):
        return None, "Click Evidence Shards registry is malformed."
    shard_sets[parent_source_key] = shard_set
    _refresh_registry(evidence_state, sources)
    state["evidence_state"] = evidence_state
    return sources, ""


def collapse_shard_plan(
    state: dict[str, Any], parent_source_key: str
) -> tuple[dict[str, Any] | None, str]:
    """Discard shard-only results and restore a fresh parent for full fallback."""
    evidence_state = state.get("evidence_state")
    if not isinstance(evidence_state, dict):
        return None, "Click Evidence Shards registry is unavailable."
    sources = evidence_state.get("sources")
    shard_sets = evidence_state.get("shard_sets", {})
    if not isinstance(sources, dict) or not isinstance(shard_sets, dict):
        return None, "Click Evidence Shards registry is malformed."
    shard_set = shard_sets.get(parent_source_key)
    if not isinstance(shard_set, dict):
        return sources, ""
    children = shard_set.get("children")
    patterns = shard_set.get("dependency_patterns")
    if not isinstance(children, list) or not isinstance(patterns, list):
        return None, "Click Evidence Shards fallback state is malformed."
    for child in children:
        if not isinstance(child, dict) or not isinstance(child.get("source_key"), str):
            return None, "Click Evidence Shards fallback state is malformed."
        sources.pop(str(child["source_key"]), None)
    sources[parent_source_key] = _fresh_source("argv", tuple(patterns))
    shard_sets.pop(parent_source_key, None)
    _refresh_registry(evidence_state, sources)
    state["evidence_state"] = evidence_state
    return sources, ""


def register_runtime_sources(
    state: dict[str, Any], source_ids: list[str], *, kind: str = "argv"
) -> tuple[dict[str, Any] | None, str]:
    """Register execution-selected Evidence-mode sources without prose authority."""
    if state.get("status") != "evidence" or kind not in EVIDENCE_KINDS:
        return None, "Dynamic evidence registration requires Evidence mode."
    evidence_state = state.get("evidence_state")
    if not isinstance(evidence_state, dict):
        return None, "Click Evidence registry is unavailable."
    sources = evidence_state.get("sources")
    if not isinstance(sources, dict):
        return None, "Click Evidence registry is unavailable."
    for source_id in source_ids:
        if not isinstance(source_id, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]{0,31}", source_id
        ):
            return None, "Dynamic evidence id is invalid."
        key = evidence_key(source_id)
        existing = sources.get(key)
        if isinstance(existing, dict):
            if existing.get("kind") != kind:
                return None, "Dynamic evidence id is already registered with another kind."
            continue
        # Evidence mode has no approval-bound declaration. Cross-revision reuse
        # may therefore come only from an exact committed repository manifest.
        sources[key] = _fresh_source(kind)
    evidence_state["sources"] = sources
    evidence_state["source_count"] = len(sources)
    evidence_state["registry_digest"] = registry_digest(sources)
    state["evidence_state"] = evidence_state
    return sources, ""


def _dependency_fields_are_valid(source: dict[str, Any]) -> bool:
    patterns = source.get("dependency_patterns", [])
    declaration_digest = source.get("dependency_declaration_digest", "")
    if not isinstance(patterns, list) or not isinstance(declaration_digest, str):
        return False
    if patterns:
        normalized, error = click_dependency_cache.normalize_patterns(patterns)
        if (
            error
            or normalized is None
            or list(normalized) != patterns
            or declaration_digest
            != click_dependency_cache.patterns_digest(normalized)
        ):
            return False
    elif declaration_digest:
        return False

    provider = source.get("verified_dependency_provider", "")
    manifest_digest = source.get("verified_dependency_manifest_digest", "")
    entry_digest = source.get("verified_dependency_entry_digest", "")
    dependency_digest = source.get("verified_dependency_digest", "")
    paths = source.get("verified_dependency_paths", [])
    observation_digest = source.get(
        "verified_dependency_observation_digest", ""
    )
    observation = source.get("verified_dependency_observation", {})
    if not all(
        isinstance(value, str)
        for value in (
            provider,
            manifest_digest,
            entry_digest,
            dependency_digest,
            observation_digest,
        )
    ) or not isinstance(paths, list) or not isinstance(observation, dict):
        return False
    if provider:
        if (
            provider not in click_dependency_cache.PROVIDER_NAMES
            or re.fullmatch(r"[0-9a-f]{64}", entry_digest) is None
            or re.fullmatch(r"[0-9a-f]{64}", dependency_digest) is None
            or manifest_digest
            and re.fullmatch(r"[0-9a-f]{64}", manifest_digest) is None
            or not click_dependency_cache.receipt_paths_are_valid(paths)
            or observation
            and (
                not click_dependency_cache.dependency_observation_is_valid(
                    observation
                )
                or observation_digest
                != click_dependency_cache.dependency_observation_digest(
                    observation
                )
            )
            or not observation
            and observation_digest
        ):
            return False
        if provider == click_dependency_cache.CONTRACT_PROVIDER_NAME:
            if manifest_digest:
                return False
        elif re.fullmatch(r"[0-9a-f]{64}", manifest_digest) is None:
            return False
    elif any(
        (
            manifest_digest,
            entry_digest,
            dependency_digest,
            paths,
            observation_digest,
            observation,
        )
    ):
        return False
    reuse_count = source.get("dependency_reuse_count", 0)
    reused_at = source.get("last_dependency_reused_at", 0)
    reused_from = source.get("last_dependency_reused_from_revision", -1)
    for value in (reuse_count, reused_at, reused_from):
        if not isinstance(value, int) or isinstance(value, bool):
            return False
    if reuse_count < 0 or reused_at < 0 or reused_from < -1:
        return False
    if not provider:
        return reuse_count == 0 and reused_at == 0 and reused_from == -1
    return bool(
        reuse_count == 0
        and reused_at == 0
        and reused_from == -1
        or reuse_count > 0
        and reused_at > 0
        and reused_from >= 0
    )


def _host_coverage_field_is_valid(source: dict[str, Any]) -> bool:
    coverage = source.get("verified_host_coverage", {})
    return bool(
        isinstance(coverage, dict)
        and (not coverage or click_host_coverage.receipt_is_valid(coverage))
    )


def _safe_change_fields_are_valid(source: dict[str, Any]) -> bool:
    receipt = source.get("verified_safe_change_receipt", {})
    reuse_count = source.get("safe_change_reuse_count", 0)
    reused_at = source.get("last_safe_change_reused_at", 0)
    reused_from = source.get("last_safe_change_reused_from_revision", -1)
    paths = source.get("last_safe_change_paths", [])
    path_count = source.get("last_safe_change_path_count", 0)
    decision_digest = source.get("last_safe_change_decision_digest", "")
    if not isinstance(receipt, dict) or not isinstance(paths, list):
        return False
    if any(
        not isinstance(value, int) or isinstance(value, bool)
        for value in (reuse_count, reused_at, reused_from, path_count)
    ):
        return False
    if (
        reuse_count < 0
        or reused_at < 0
        or reused_from < -1
        or path_count < 0
        or not isinstance(decision_digest, str)
        or not click_change_policy.changed_paths_are_valid(paths, maximum=128)
        or path_count < len(paths)
    ):
        return False
    if not receipt:
        return bool(
            reuse_count == 0
            and reused_at == 0
            and reused_from == -1
            and not paths
            and path_count == 0
            and not decision_digest
        )
    if not click_change_policy.receipt_is_valid(receipt):
        return False
    return bool(
        reuse_count == 0
        and reused_at == 0
        and reused_from == -1
        and not paths
        and path_count == 0
        and not decision_digest
        or reuse_count > 0
        and reused_at > 0
        and reused_from >= 0
        and re.fullmatch(r"[0-9a-f]{64}", decision_digest) is not None
    )


def _successor_fields_are_valid(source: dict[str, Any]) -> bool:
    count = source.get("successor_reuse_count", 0)
    reused_at = source.get("last_successor_reused_at", 0)
    origin_revision = source.get("last_successor_origin_revision", -1)
    batch_id = source.get("last_successor_origin_batch_id", "")
    session_id = source.get("last_successor_origin_evidence_session_id", "")
    contract_id = source.get("last_successor_origin_contract_id", "")
    candidate_digest = source.get("last_successor_candidate_digest", "")
    mode = source.get("last_successor_mode", "")
    if any(
        not isinstance(value, int) or isinstance(value, bool)
        for value in (count, reused_at, origin_revision)
    ):
        return False
    if count < 0 or reused_at < 0 or origin_revision < -1:
        return False
    if not all(isinstance(value, str) for value in (
        batch_id, session_id, contract_id, candidate_digest, mode
    )):
        return False
    if count == 0:
        return bool(
            reused_at == 0 and origin_revision == -1 and not batch_id
            and not session_id and not contract_id and not candidate_digest and not mode
        )
    return bool(
        reused_at > 0
        and origin_revision >= 0
        and re.fullmatch(r"[0-9a-f]{32}", batch_id)
        and (
            not contract_id and re.fullmatch(r"evs_[0-9a-f]{32}", session_id)
            or not session_id and re.fullmatch(r"ctr_[0-9a-f]{32}", contract_id)
        )
        and re.fullmatch(r"[0-9a-f]{64}", candidate_digest)
        and mode in {"exact", "dependency", "safe-change"}
    )


def sources_from_state(
    state: dict[str, Any],
    *,
    expected_contract_schema_version: int,
) -> dict[str, Any] | None:
    """Return a valid ledger, `{}` for malformed state, or `None` for legacy state."""
    if (
        "state_schema_version" in state
        and state.get("state_schema_version") != expected_contract_schema_version
    ):
        return {}
    if "evidence_state" not in state:
        if "state_schema_version" in state:
            return {}
        return None
    evidence_state = state.get("evidence_state")
    if (
        not isinstance(evidence_state, dict)
        or evidence_state.get("version") != EVIDENCE_STATE_VERSION
    ):
        return {}
    sources = evidence_state.get("sources")
    if not isinstance(sources, dict):
        return {}
    for key, source in sources.items():
        reserved_units = source.get("reserved_units", 0) if isinstance(source, dict) else 0
        reserved_digest = (
            source.get("reserved_check_digest", "")
            if isinstance(source, dict)
            else ""
        )
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[0-9a-f]{64}", key)
            or not isinstance(source, dict)
            or source.get("kind") not in EVIDENCE_KINDS
            or source.get("status") not in EVIDENCE_STATUSES
            or not isinstance(source.get("verified_revision"), int)
            or isinstance(source.get("verified_revision"), bool)
            or not isinstance(source.get("attempts"), int)
            or isinstance(source.get("attempts"), bool)
            or not isinstance(source.get("unchanged_failure_retries"), int)
            or isinstance(source.get("unchanged_failure_retries"), bool)
            or source.get("attempts", -1) < 0
            or source.get("unchanged_failure_retries", -1) < 0
            or source.get("last_exit_code") is not None
            and (
                not isinstance(source.get("last_exit_code"), int)
                or isinstance(source.get("last_exit_code"), bool)
            )
            or not isinstance(source.get("last_check_digest"), str)
            or not isinstance(source.get("locked_check_digest"), str)
            or not isinstance(source.get("verified_executable_digest", ""), str)
            or not isinstance(source.get("last_success_duration_ms", 0), int)
            or isinstance(source.get("last_success_duration_ms", 0), bool)
            or source.get("last_success_duration_ms", 0) < 0
            or source.get("verified_executable_digest", "")
            and re.fullmatch(
                r"[0-9a-f]{64}",
                str(source.get("verified_executable_digest", "")),
            )
            is None
            or not _dependency_fields_are_valid(source)
            or not _safe_change_fields_are_valid(source)
            or not _successor_fields_are_valid(source)
            or not _host_coverage_field_is_valid(source)
            or (
                "reserved_units" in source
                and (
                    not isinstance(source.get("reserved_units"), int)
                    or isinstance(source.get("reserved_units"), bool)
                    or source.get("reserved_units", -1) < 0
                )
            )
            or (
                "reserved_check_digest" in source
                and (
                    not isinstance(source.get("reserved_check_digest"), str)
                    or source.get("reserved_check_digest")
                    and not re.fullmatch(
                        r"[0-9a-f]{64}", source.get("reserved_check_digest", "")
                    )
                )
            )
        ):
            return {}
    source_count = evidence_state.get("source_count")
    stored_digest = evidence_state.get("registry_digest")
    if (
        not isinstance(source_count, int)
        or isinstance(source_count, bool)
        or source_count != len(sources)
        or not isinstance(stored_digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", stored_digest)
        or not secrets.compare_digest(stored_digest, registry_digest(sources))
        or not click_evidence_shards.state_is_valid(evidence_state, sources)
    ):
        return {}
    return sources


def is_current(source: Any, revision: int) -> bool:
    """Return whether one source passed for the exact current mutation revision."""
    return bool(
        isinstance(source, dict)
        and source.get("status") == "passed"
        and int(source.get("verified_revision", -1)) == revision
    )


def keys_for_kind(sources: dict[str, Any], kind: str) -> set[str]:
    """Return the persisted keys registered for one evidence kind."""
    return {
        key
        for key, source in sources.items()
        if isinstance(source, dict) and source.get("kind") == kind
    }


def browser_source_id(contract: dict[str, Any]) -> str:
    """Return the one Browser evidence id from a validated contract, if any."""
    verification = contract.get("verification")
    evidence = verification.get("evidence") if isinstance(verification, dict) else []
    if not isinstance(evidence, list):
        return ""
    for source in evidence:
        if isinstance(source, dict) and source.get("kind") == "browser":
            source_id = source.get("id")
            return source_id if isinstance(source_id, str) else ""
    return ""


def browser_required(contract: dict[str, Any]) -> bool:
    """Return whether the validated registry assigns a Browser source."""
    return bool(browser_source_id(contract))


def fresh_external_state(
    contract: dict[str, Any] | None = None,
    *,
    required: bool | None = None,
    source_key: str | None = None,
) -> dict[str, Any]:
    """Create the prose-free Browser evidence session state."""
    source_id = browser_source_id(contract or {})
    browser_source_key = (
        evidence_key(source_id)
        if source_key is None and source_id
        else (source_key or "")
    )
    browser_is_required = (
        bool(browser_source_key) if required is None else required
    )
    return {
        "browser_required": browser_is_required,
        "browser_source_key": browser_source_key,
        "browser_status": "ready" if browser_is_required else "not-required",
        "browser_calls": 0,
        "browser_seconds": 0.0,
        "browser_running": {},
        "browser_attempts": {},
        "last_browser_error": "",
    }
