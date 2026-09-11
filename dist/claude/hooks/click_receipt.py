"""Canonical completion-receipt primitives for Click.

This leaf defines the versioned, content-only receipt envelope used by later
runtime integration.  It accepts already-normalized completion data, validates
it strictly, and produces deterministic JSON bytes and a SHA-256 digest.  It
does not read Click state, grant authority, persist receipts, or sign them.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from typing import Any


LEGACY_RECEIPT_VERSION = 1
RECEIPT_VERSION = 2
SHARD_RECEIPT_VERSION = 3
SUCCESSOR_RECEIPT_VERSION = 4
GUARDED_SUCCESSOR_RECEIPT_VERSION = 5
SUPPORTED_RECEIPT_VERSIONS = {
    LEGACY_RECEIPT_VERSION,
    RECEIPT_VERSION,
    SHARD_RECEIPT_VERSION,
    SUCCESSOR_RECEIPT_VERSION,
    GUARDED_SUCCESSOR_RECEIPT_VERSION,
}
ENVELOPE_VERSION = 1
UNSIGNED_ASSURANCE = "unsigned-integrity-only"
CONTRACT_ID_PATTERN = re.compile(r"^ctr_[0-9a-f]{32}$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_KINDS = ("argv", "browser", "hosted", "manual", "existing")
WORKSPACE_ASSURANCES = ("git-protected-tree", "unavailable")
HOST_ASSURANCE = "known-surfaces-only"
BASE_COVERAGE_EXCLUSIONS = (
    "external-dependencies",
    "external-system-state",
    "git-ignored-content",
    "unmatched-host-paths",
)
UNAVAILABLE_TREE_EXCLUSION = "protected-tree-unavailable"

RECEIPT_V1_FIELDS = {
    "version",
    "contract",
    "execution",
    "capabilities",
    "evidence",
    "coverage",
}
RECEIPT_FIELDS = RECEIPT_V1_FIELDS | {"authority"}
CONTRACT_FIELDS = {"id", "digest", "staged_turn_id", "approved_turn_id"}
AUTHORITY_FIELDS = {
    "mode",
    "approval_bound",
    "execution_authority",
    "intent_digest",
    "intent_turn_id",
    "follow_up_turns",
    "history_complete",
}
FOLLOW_UP_FIELDS = {"turn_id", "digest"}
EXECUTION_FIELDS = {"mutation_revision", "workspace"}
WORKSPACE_FIELDS = {"assurance", "root_digest", "tree_digest"}
CAPABILITY_FIELDS = {
    "sequence",
    "capability",
    "claim_mode",
    "request_digest",
    "claim_digest",
    "binding_digest",
    "mutation_revision",
    "claimed_at",
    "completed_at",
    "result",
}
CAPABILITY_RESULT_FIELDS = {"status", "exit_code"}
CAPABILITIES = {
    "browser",
    "evidence-attestation",
    "managed-service-start",
    "managed-service-stop",
    "managed-service-supervisor",
    "mutation",
    "observation",
    "verification",
}
CLAIM_MODES = {"host-tool-use", "one-use-runner"}
CAPABILITY_RESULT_STATUSES = {"failed", "observed", "passed"}
EVIDENCE_FIELDS = {
    "source_key",
    "kind",
    "verified_revision",
    "check_digest",
    "environment_digest",
    "executable_digest",
    "result",
    "lineage",
}
SHARDED_EVIDENCE_FIELDS = EVIDENCE_FIELDS | {"shard"}
SHARD_FIELDS = {
    "provider",
    "parent_source_key",
    "parent_check_digest",
    "shard_id",
    "shard_count",
    "plan_digest",
    "entry_digest",
    "inventory_digest",
}
SHARD_PROVIDER = "repository-evidence-shards-v1"
SHARD_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
EVIDENCE_RESULT_FIELDS = {"status", "exit_code", "completed_at"}
LINEAGE_FIELDS = {"mode", "from_revision", "dependency_digest"}
SUCCESSOR_LINEAGE_FIELDS = LINEAGE_FIELDS | {
    "origin_batch_id",
    "origin_evidence_session_id",
    "requalification_mode",
}
GUARDED_SUCCESSOR_LINEAGE_FIELDS = (
    SUCCESSOR_LINEAGE_FIELDS - {"origin_evidence_session_id"}
) | {"origin_contract_id"}
COVERAGE_FIELDS = {
    "host_assurance",
    "host_coverage_digest",
    "excluded",
}
ENVELOPE_FIELDS = {"version", "assurance", "receipt", "receipt_digest"}


def _exact_fields(value: Any, expected: set[str], label: str) -> str:
    if not isinstance(value, dict):
        return f"Completion receipt `{label}` must be an object."
    unknown = sorted(set(value) - expected)
    if unknown:
        rendered = ", ".join(f"`{field}`" for field in unknown)
        return f"Completion receipt `{label}` contains unsupported field(s): {rendered}."
    missing = sorted(expected - set(value))
    if missing:
        rendered = ", ".join(f"`{field}`" for field in missing)
        return f"Completion receipt `{label}` is missing field(s): {rendered}."
    return ""


def _is_digest(value: Any) -> bool:
    return bool(isinstance(value, str) and DIGEST_PATTERN.fullmatch(value))


def _is_revision(value: Any) -> bool:
    return bool(isinstance(value, int) and not isinstance(value, bool) and value >= 0)


def _normalize_contract(value: Any) -> tuple[dict[str, Any] | None, str]:
    error = _exact_fields(value, CONTRACT_FIELDS, "contract")
    if error:
        return None, error
    assert isinstance(value, dict)
    contract_id = value.get("id")
    if not isinstance(contract_id, str) or not CONTRACT_ID_PATTERN.fullmatch(contract_id):
        return None, "Completion receipt contract `id` is invalid."
    digest = value.get("digest")
    if not _is_digest(digest):
        return None, "Completion receipt contract `digest` must be lowercase SHA-256."
    staged_turn_id = value.get("staged_turn_id")
    approved_turn_id = value.get("approved_turn_id")
    if not isinstance(staged_turn_id, str) or not staged_turn_id.strip():
        return None, "Completion receipt `staged_turn_id` must be non-empty."
    if not isinstance(approved_turn_id, str) or not approved_turn_id.strip():
        return None, "Completion receipt `approved_turn_id` must be non-empty."
    if staged_turn_id == approved_turn_id:
        return None, "Completion receipt approval must belong to a later distinct turn."
    return {
        "id": contract_id,
        "digest": digest,
        "staged_turn_id": staged_turn_id,
        "approved_turn_id": approved_turn_id,
    }, ""


def _normalize_authority(value: Any) -> tuple[dict[str, Any] | None, str]:
    error = _exact_fields(value, AUTHORITY_FIELDS, "authority")
    if error:
        return None, error
    assert isinstance(value, dict)
    mode = value.get("mode")
    approval_bound = value.get("approval_bound")
    execution_authority = value.get("execution_authority")
    if mode not in {"guarded", "evidence"}:
        return None, "Completion receipt authority mode is unsupported."
    if not isinstance(approval_bound, bool):
        return None, "Completion receipt `approval_bound` must be boolean."
    expected = (
        (True, "click-contract")
        if mode == "guarded"
        else (False, "host")
    )
    if (approval_bound, execution_authority) != expected:
        return None, "Completion receipt authority fields contradict its mode."
    intent_digest = value.get("intent_digest")
    intent_turn_id = value.get("intent_turn_id")
    if not _is_digest(intent_digest):
        return None, "Completion receipt intent digest must be lowercase SHA-256."
    if not isinstance(intent_turn_id, str) or not intent_turn_id.strip():
        return None, "Completion receipt intent turn must be non-empty."
    history_complete = value.get("history_complete")
    if not isinstance(history_complete, bool):
        return None, "Completion receipt history assurance must be boolean."
    follow_up_turns = value.get("follow_up_turns")
    if not isinstance(follow_up_turns, list):
        return None, "Completion receipt follow-up turns must be a list."
    normalized_follow_ups: list[dict[str, str]] = []
    seen_turns: set[str] = set()
    for item in follow_up_turns:
        error = _exact_fields(item, FOLLOW_UP_FIELDS, "authority.follow_up_turn")
        if error:
            return None, error
        assert isinstance(item, dict)
        turn_id = item.get("turn_id")
        digest = item.get("digest")
        if (
            not isinstance(turn_id, str)
            or not turn_id.strip()
            or turn_id == intent_turn_id
            or turn_id in seen_turns
            or not _is_digest(digest)
        ):
            return None, "Completion receipt follow-up turn lineage is invalid."
        seen_turns.add(turn_id)
        normalized_follow_ups.append({"turn_id": turn_id, "digest": str(digest)})
    return {
        "mode": mode,
        "approval_bound": approval_bound,
        "execution_authority": execution_authority,
        "intent_digest": str(intent_digest),
        "intent_turn_id": intent_turn_id,
        "follow_up_turns": normalized_follow_ups,
        "history_complete": history_complete,
    }, ""


def _normalize_workspace(value: Any) -> tuple[dict[str, Any] | None, str]:
    error = _exact_fields(value, WORKSPACE_FIELDS, "execution.workspace")
    if error:
        return None, error
    assert isinstance(value, dict)
    assurance = value.get("assurance")
    if assurance not in WORKSPACE_ASSURANCES:
        return None, "Completion receipt workspace `assurance` is unsupported."
    root_digest = value.get("root_digest")
    tree_digest = value.get("tree_digest")
    if assurance == "git-protected-tree":
        if not _is_digest(root_digest) or not _is_digest(tree_digest):
            return (
                None,
                "A git-protected completion receipt requires root and tree SHA-256 digests.",
            )
    elif root_digest != "" or tree_digest != "":
        return (
            None,
            "An unavailable workspace receipt must not claim root or tree digests.",
        )
    return {
        "assurance": assurance,
        "root_digest": root_digest,
        "tree_digest": tree_digest,
    }, ""


def _normalize_execution(value: Any) -> tuple[dict[str, Any] | None, str]:
    error = _exact_fields(value, EXECUTION_FIELDS, "execution")
    if error:
        return None, error
    assert isinstance(value, dict)
    revision = value.get("mutation_revision")
    if not _is_revision(revision):
        return None, "Completion receipt `mutation_revision` must be non-negative."
    workspace, error = _normalize_workspace(value.get("workspace"))
    if error:
        return None, error
    assert workspace is not None
    return {"mutation_revision": revision, "workspace": workspace}, ""


def _normalize_capability(
    value: Any, *, expected_sequence: int, final_revision: int
) -> tuple[dict[str, Any] | None, str]:
    error = _exact_fields(value, CAPABILITY_FIELDS, "capability")
    if error:
        return None, error
    assert isinstance(value, dict)
    if value.get("sequence") != expected_sequence:
        return None, "Completion receipt capability sequences must be contiguous and ordered."
    capability = value.get("capability")
    if capability not in CAPABILITIES:
        return None, "Completion receipt capability name is unsupported."
    claim_mode = value.get("claim_mode")
    if claim_mode not in CLAIM_MODES:
        return None, "Completion receipt capability claim mode is unsupported."
    request_digest = value.get("request_digest")
    claim_digest = value.get("claim_digest")
    binding_digest = value.get("binding_digest")
    if not _is_digest(request_digest) or not _is_digest(claim_digest):
        return None, "Completion receipt capability digests must be lowercase SHA-256."
    if claim_mode == "one-use-runner" and binding_digest != "":
        return None, "A one-use receipt claim must not expose a host binding digest."
    if claim_mode == "host-tool-use" and not _is_digest(binding_digest):
        return None, "A host-tool-use receipt claim requires a binding digest."

    revision = value.get("mutation_revision")
    claimed_at = value.get("claimed_at")
    completed_at = value.get("completed_at")
    if not _is_revision(revision) or revision > final_revision:
        return None, "Completion receipt capability revision exceeds the final revision."
    if not _is_revision(claimed_at) or claimed_at <= 0:
        return None, "Completion receipt capability claim timestamp is invalid."
    if not _is_revision(completed_at) or completed_at < claimed_at:
        return None, "Completion receipt capability completion timestamp is invalid."

    result = value.get("result")
    error = _exact_fields(result, CAPABILITY_RESULT_FIELDS, "capability.result")
    if error:
        return None, error
    assert isinstance(result, dict)
    result_status = result.get("status")
    exit_code = result.get("exit_code")
    if result_status not in CAPABILITY_RESULT_STATUSES:
        return None, "Completion receipt capability result status is unsupported."
    if result_status == "observed":
        if claim_mode != "host-tool-use" or exit_code is not None:
            return None, "Only host-tool-use receipt claims may have an observed result."
    elif not isinstance(exit_code, int) or isinstance(exit_code, bool):
        return None, "A runner receipt claim requires an integer exit code."
    elif (result_status == "passed") != (exit_code == 0):
        return None, "Completion receipt capability status does not match its exit code."

    return {
        "sequence": expected_sequence,
        "capability": capability,
        "claim_mode": claim_mode,
        "request_digest": request_digest,
        "claim_digest": claim_digest,
        "binding_digest": binding_digest,
        "mutation_revision": revision,
        "claimed_at": claimed_at,
        "completed_at": completed_at,
        "result": {"status": result_status, "exit_code": exit_code},
    }, ""


def _normalize_lineage(
    value: Any, *, kind: str, revision: int, receipt_version: int
) -> tuple[dict[str, Any] | None, str]:
    successor = isinstance(value, dict) and value.get("mode") == "successor-reused"
    guarded = successor and "origin_contract_id" in value
    fields = (
        GUARDED_SUCCESSOR_LINEAGE_FIELDS if guarded
        else SUCCESSOR_LINEAGE_FIELDS if successor else LINEAGE_FIELDS
    )
    error = _exact_fields(value, fields, "evidence.lineage")
    if error:
        return None, error
    assert isinstance(value, dict)
    mode = value.get("mode")
    from_revision = value.get("from_revision")
    dependency_digest = value.get("dependency_digest")
    if not _is_revision(from_revision):
        return None, "Completion receipt evidence lineage revision is invalid."

    expected_modes = {
        "argv": {"executed", "dependency-reused", "successor-reused"},
        "browser": {"browser-observed"},
        "hosted": {"attested"},
        "manual": {"attested"},
        "existing": {"attested"},
    }
    if mode not in expected_modes[kind]:
        return None, f"Completion receipt lineage mode is invalid for `{kind}` evidence."
    if mode == "successor-reused":
        origin_batch_id = value.get("origin_batch_id")
        identity_field = "origin_contract_id" if guarded else "origin_evidence_session_id"
        origin_session_id = value.get(identity_field)
        requalification_mode = value.get("requalification_mode")
        if (
            receipt_version != (
                GUARDED_SUCCESSOR_RECEIPT_VERSION if guarded else SUCCESSOR_RECEIPT_VERSION
            )
            or kind != "argv"
            or not _is_digest(dependency_digest)
            or not isinstance(origin_batch_id, str)
            or re.fullmatch(r"[0-9a-f]{32}", origin_batch_id) is None
            or not isinstance(origin_session_id, str)
            or re.fullmatch(r"ctr_[0-9a-f]{32}" if guarded else r"evs_[0-9a-f]{32}", origin_session_id) is None
            or requalification_mode not in {"exact", "dependency", "safe-change"}
        ):
            return None, "Successor-reused evidence lineage is invalid."
        return {
            "mode": mode,
            "from_revision": from_revision,
            "dependency_digest": dependency_digest,
            "origin_batch_id": origin_batch_id,
            identity_field: origin_session_id,
            "requalification_mode": requalification_mode,
        }, ""
    if mode == "dependency-reused":
        if from_revision >= revision or not _is_digest(dependency_digest):
            return (
                None,
                "Dependency-reused evidence requires an earlier revision and dependency digest.",
            )
    elif from_revision != revision or dependency_digest != "":
        return (
            None,
            "Direct or attested evidence lineage must name the completed revision only.",
        )
    return {
        "mode": mode,
        "from_revision": from_revision,
        "dependency_digest": dependency_digest,
    }, ""


def _normalize_shard(value: Any, *, source_key: str) -> tuple[dict[str, Any] | None, str]:
    if value is None:
        return None, ""
    error = _exact_fields(value, SHARD_FIELDS, "evidence.shard")
    if error:
        return None, error
    assert isinstance(value, dict)
    parent_source_key = value.get("parent_source_key")
    shard_id = value.get("shard_id")
    shard_count = value.get("shard_count")
    if value.get("provider") != SHARD_PROVIDER:
        return None, "Completion receipt evidence shard provider is unsupported."
    if not _is_digest(parent_source_key) or parent_source_key == source_key:
        return None, "Completion receipt evidence shard parent identity is invalid."
    if not isinstance(shard_id, str) or not SHARD_ID_PATTERN.fullmatch(shard_id):
        return None, "Completion receipt evidence shard id is invalid."
    if (
        not isinstance(shard_count, int)
        or isinstance(shard_count, bool)
        or shard_count < 2
        or shard_count > 64
    ):
        return None, "Completion receipt evidence shard count is invalid."
    for field in (
        "parent_check_digest",
        "plan_digest",
        "entry_digest",
        "inventory_digest",
    ):
        if not _is_digest(value.get(field)):
            return None, f"Completion receipt evidence shard `{field}` is invalid."
    return {
        "provider": SHARD_PROVIDER,
        "parent_source_key": parent_source_key,
        "parent_check_digest": value["parent_check_digest"],
        "shard_id": shard_id,
        "shard_count": shard_count,
        "plan_digest": value["plan_digest"],
        "entry_digest": value["entry_digest"],
        "inventory_digest": value["inventory_digest"],
    }, ""


def _normalize_evidence(
    value: Any, *, revision: int, receipt_version: int
) -> tuple[dict[str, Any] | None, str]:
    fields = (
        SHARDED_EVIDENCE_FIELDS
        if receipt_version in {SHARD_RECEIPT_VERSION, SUCCESSOR_RECEIPT_VERSION, GUARDED_SUCCESSOR_RECEIPT_VERSION}
        else EVIDENCE_FIELDS
    )
    error = _exact_fields(value, fields, "evidence source")
    if error:
        return None, error
    assert isinstance(value, dict)
    source_key = value.get("source_key")
    if not _is_digest(source_key):
        return None, "Completion receipt evidence `source_key` must be lowercase SHA-256."
    kind = value.get("kind")
    if kind not in EVIDENCE_KINDS:
        return None, "Completion receipt evidence `kind` is unsupported."
    verified_revision = value.get("verified_revision")
    if verified_revision != revision or not _is_revision(verified_revision):
        return None, "Every completion receipt evidence source must match the final revision."

    check_digest = value.get("check_digest")
    environment_digest = value.get("environment_digest")
    executable_digest = value.get("executable_digest")
    fingerprint_values = (check_digest, environment_digest, executable_digest)
    if kind == "argv":
        if not all(_is_digest(item) for item in fingerprint_values):
            return (
                None,
                "Completed argv evidence requires check, environment, and executable digests.",
            )
    elif any(item != "" for item in fingerprint_values):
        return (
            None,
            "Non-argv evidence must not claim argv check, environment, or executable digests.",
        )

    result = value.get("result")
    error = _exact_fields(result, EVIDENCE_RESULT_FIELDS, "evidence.result")
    if error:
        return None, error
    assert isinstance(result, dict)
    completed_at = result.get("completed_at")
    if (
        result.get("status") != "passed"
        or result.get("exit_code") != 0
        or not _is_revision(completed_at)
        or completed_at <= 0
    ):
        return None, "Completion receipt evidence result must be a timestamped pass."

    lineage, error = _normalize_lineage(
        value.get("lineage"), kind=str(kind), revision=revision,
        receipt_version=receipt_version,
    )
    if error:
        return None, error
    assert lineage is not None
    normalized = {
        "source_key": source_key,
        "kind": kind,
        "verified_revision": verified_revision,
        "check_digest": check_digest,
        "environment_digest": environment_digest,
        "executable_digest": executable_digest,
        "result": {
            "status": "passed",
            "exit_code": 0,
            "completed_at": completed_at,
        },
        "lineage": lineage,
    }
    if receipt_version in {SHARD_RECEIPT_VERSION, SUCCESSOR_RECEIPT_VERSION, GUARDED_SUCCESSOR_RECEIPT_VERSION}:
        if kind != "argv" and value.get("shard") is not None:
            return None, "Only argv evidence may carry shard provenance."
        shard, error = _normalize_shard(value.get("shard"), source_key=source_key)
        if error:
            return None, error
        normalized["shard"] = shard
    return normalized, ""


def _normalize_coverage(
    value: Any, *, workspace_assurance: str
) -> tuple[dict[str, Any] | None, str]:
    error = _exact_fields(value, COVERAGE_FIELDS, "coverage")
    if error:
        return None, error
    assert isinstance(value, dict)
    if value.get("host_assurance") != HOST_ASSURANCE:
        return None, "Completion receipt host assurance must be `known-surfaces-only`."
    host_digest = value.get("host_coverage_digest")
    if not _is_digest(host_digest):
        return None, "Completion receipt host coverage digest must be lowercase SHA-256."
    excluded = value.get("excluded")
    if (
        not isinstance(excluded, list)
        or any(not isinstance(item, str) for item in excluded)
        or len(set(excluded)) != len(excluded)
    ):
        return None, "Completion receipt coverage exclusions must be unique strings."
    expected = set(BASE_COVERAGE_EXCLUSIONS)
    if workspace_assurance == "unavailable":
        expected.add(UNAVAILABLE_TREE_EXCLUSION)
    if set(excluded) != expected:
        return None, "Completion receipt coverage exclusions are incomplete or unsupported."
    return {
        "host_assurance": HOST_ASSURANCE,
        "host_coverage_digest": host_digest,
        "excluded": sorted(expected),
    }, ""


def validate_receipt(value: Any) -> tuple[dict[str, Any] | None, str]:
    """Return a normalized supported completion receipt or a fail-closed error."""
    if not isinstance(value, dict):
        return None, "Completion receipt `root` must be an object."
    version = value.get("version")
    expected_fields = (
        RECEIPT_V1_FIELDS if version == LEGACY_RECEIPT_VERSION else RECEIPT_FIELDS
    )
    error = _exact_fields(value, expected_fields, "root")
    if error:
        return None, error
    if version not in SUPPORTED_RECEIPT_VERSIONS or isinstance(version, bool):
        return None, "Completion receipt `version` is unsupported."
    authority: dict[str, Any] | None = None
    if version in {
        RECEIPT_VERSION,
        SHARD_RECEIPT_VERSION,
        SUCCESSOR_RECEIPT_VERSION,
        GUARDED_SUCCESSOR_RECEIPT_VERSION,
    }:
        authority, error = _normalize_authority(value.get("authority"))
        if error:
            return None, error
        assert authority is not None
        if authority["mode"] == "guarded":
            contract, error = _normalize_contract(value.get("contract"))
            if error:
                return None, error
            assert contract is not None
            if (
                contract["digest"] != authority["intent_digest"]
                or contract["staged_turn_id"] != authority["intent_turn_id"]
            ):
                return None, "Guarded receipt authority does not match its contract."
        else:
            if value.get("contract") is not None:
                return None, "Evidence receipt must not claim an approved contract."
            contract = None
    else:
        contract, error = _normalize_contract(value.get("contract"))
        if error:
            return None, error
    execution, error = _normalize_execution(value.get("execution"))
    if error:
        return None, error
    assert execution is not None
    revision = int(execution["mutation_revision"])

    capabilities = value.get("capabilities")
    if not isinstance(capabilities, list):
        return None, "Completion receipt `capabilities` must be a list."
    normalized_capabilities: list[dict[str, Any]] = []
    seen_claims: set[str] = set()
    for sequence, capability in enumerate(capabilities, start=1):
        normalized, error = _normalize_capability(
            capability,
            expected_sequence=sequence,
            final_revision=revision,
        )
        if error:
            return None, error
        assert normalized is not None
        claim_digest = str(normalized["claim_digest"])
        if claim_digest in seen_claims:
            return None, "Completion receipt capability claim digests must be unique."
        seen_claims.add(claim_digest)
        normalized_capabilities.append(normalized)

    evidence = value.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return None, "Completion receipt `evidence` must be a non-empty list."
    normalized_evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in evidence:
        normalized, error = _normalize_evidence(
            source, revision=revision, receipt_version=int(version)
        )
        if error:
            return None, error
        assert normalized is not None
        source_key = str(normalized["source_key"])
        if source_key in seen:
            return None, "Completion receipt evidence source keys must be unique."
        seen.add(source_key)
        normalized_evidence.append(normalized)
    normalized_evidence.sort(key=lambda source: str(source["source_key"]))
    if version in {SHARD_RECEIPT_VERSION, SUCCESSOR_RECEIPT_VERSION, GUARDED_SUCCESSOR_RECEIPT_VERSION}:
        sharded = [
            source for source in normalized_evidence if source.get("shard") is not None
        ]
        if version == SHARD_RECEIPT_VERSION and not sharded:
            return None, "Receipt v3 requires at least one sharded evidence source."
        groups: dict[str, list[dict[str, Any]]] = {}
        for source in sharded:
            shard = source["shard"]
            assert isinstance(shard, dict)
            groups.setdefault(str(shard["parent_source_key"]), []).append(shard)
        for shards in groups.values():
            first = shards[0]
            shared_fields = (
                "provider",
                "parent_check_digest",
                "shard_count",
                "plan_digest",
                "entry_digest",
                "inventory_digest",
            )
            if (
                len(shards) != first["shard_count"]
                or len({str(shard["shard_id"]) for shard in shards}) != len(shards)
                or any(
                    any(shard[field] != first[field] for field in shared_fields)
                    for shard in shards[1:]
                )
            ):
                return None, "Completion receipt evidence shard set is incomplete."
    if version == SUCCESSOR_RECEIPT_VERSION and not any(
        source["lineage"]["mode"] == "successor-reused"
        for source in normalized_evidence
    ):
        return None, "Receipt v4 requires successor-reused evidence lineage."
    if version == GUARDED_SUCCESSOR_RECEIPT_VERSION and (
        authority is None or authority["mode"] != "guarded"
        or not any("origin_contract_id" in source["lineage"] for source in normalized_evidence)
        or any(source["lineage"].get("origin_contract_id") == contract["id"] for source in normalized_evidence)
    ):
        return None, "Receipt v5 requires separately approved Guarded successor lineage."

    workspace = execution["workspace"]
    assert isinstance(workspace, dict)
    coverage, error = _normalize_coverage(
        value.get("coverage"), workspace_assurance=str(workspace["assurance"])
    )
    if error:
        return None, error
    assert coverage is not None
    normalized_receipt = {
        "version": version,
        "contract": contract,
        "execution": execution,
        "capabilities": normalized_capabilities,
        "evidence": normalized_evidence,
        "coverage": coverage,
    }
    if authority is not None:
        normalized_receipt["authority"] = authority
    return normalized_receipt, ""


def canonical_bytes(value: Any) -> tuple[bytes | None, str]:
    """Return deterministic UTF-8 JSON bytes for a valid completion receipt."""
    normalized, error = validate_receipt(value)
    if error:
        return None, error
    assert normalized is not None
    rendered = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return rendered.encode("utf-8"), ""


def receipt_digest(value: Any) -> tuple[str, str]:
    """Return the SHA-256 digest of the canonical receipt body."""
    canonical, error = canonical_bytes(value)
    if error:
        return "", error
    assert canonical is not None
    return hashlib.sha256(canonical).hexdigest(), ""


def create_envelope(value: Any) -> tuple[dict[str, Any] | None, str]:
    """Wrap one valid receipt in an explicitly unsigned integrity envelope."""
    normalized, error = validate_receipt(value)
    if error:
        return None, error
    assert normalized is not None
    digest, error = receipt_digest(normalized)
    if error:
        return None, error
    return {
        "version": ENVELOPE_VERSION,
        "assurance": UNSIGNED_ASSURANCE,
        "receipt": normalized,
        "receipt_digest": digest,
    }, ""


def validate_envelope(value: Any) -> tuple[dict[str, Any] | None, str]:
    """Validate an unsigned envelope without claiming publisher authenticity."""
    error = _exact_fields(value, ENVELOPE_FIELDS, "envelope")
    if error:
        return None, error
    assert isinstance(value, dict)
    if value.get("version") != ENVELOPE_VERSION:
        return None, f"Completion receipt envelope `version` must be {ENVELOPE_VERSION}."
    if value.get("assurance") != UNSIGNED_ASSURANCE:
        return None, "Completion receipt envelope assurance must be `unsigned-integrity-only`."
    normalized, error = validate_receipt(value.get("receipt"))
    if error:
        return None, error
    assert normalized is not None
    supplied_digest = value.get("receipt_digest")
    if not _is_digest(supplied_digest):
        return None, "Completion receipt envelope digest must be lowercase SHA-256."
    expected_digest, error = receipt_digest(normalized)
    if error:
        return None, error
    if not secrets.compare_digest(str(supplied_digest), expected_digest):
        return None, "Completion receipt envelope digest does not match its canonical body."
    return {
        "version": ENVELOPE_VERSION,
        "assurance": UNSIGNED_ASSURANCE,
        "receipt": normalized,
        "receipt_digest": expected_digest,
    }, ""
