"""Receipt matching, promotion and explanations without runner or gate access.

Candidates become reusable only after the caller supplies every current binding;
telemetry and explanations cannot manufacture a passing execution receipt.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
from typing import Any

if __package__:
    from . import click_import_bootstrap
else:  # Installed launchers execute hooks directly.
    import click_import_bootstrap

(click_capability, click_change_policy, click_dependency_cache, click_evidence_shards, click_host_coverage, click_incremental,) = click_import_bootstrap.load_siblings(
    __package__, "click_capability", "click_change_policy", "click_dependency_cache", "click_evidence_shards", "click_host_coverage", "click_incremental"
)

def verification_receipt_matches(
    source: dict[str, Any],
    *,
    contract_digest: str,
    revision: int,
    group_digest: str,
    git_root: str,
    tree_digest: str,
    environment_digest: str,
    executable_digest: str,
    host_coverage: dict[str, Any],
) -> bool:
    if not all(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
        for value in (
            contract_digest,
            group_digest,
            tree_digest,
            environment_digest,
            executable_digest,
        )
    ):
        return False
    verified_at = source.get("verified_at", 0)
    if (
        not isinstance(verified_at, int)
        or isinstance(verified_at, bool)
        or verified_at <= 0
    ):
        return False
    return bool(
        source.get("status") == "passed"
        and int(source.get("verified_revision", -1)) == revision
        and source.get("verified_contract_digest") == contract_digest
        and source.get("verified_check_digest") == group_digest
        and source.get("verified_root") == git_root
        and source.get("verified_tree_digest") == tree_digest
        and source.get("verified_environment_digest") == environment_digest
        and source.get("verified_executable_digest") == executable_digest
        and click_host_coverage.receipt_is_current(host_coverage)
        and source.get("verified_host_coverage") == host_coverage
    )


def dependency_declarations(
    sources: dict[str, Any], source_keys: set[str]
) -> dict[str, list[str]]:
    declarations: dict[str, list[str]] = {}
    for source_key in source_keys:
        source = sources.get(source_key)
        patterns = source.get("dependency_patterns", []) if isinstance(source, dict) else []
        if isinstance(patterns, list) and patterns:
            declarations[source_key] = list(patterns)
    return declarations


def dependency_observations(
    sources: dict[str, Any], source_keys: set[str]
) -> dict[str, dict[str, Any]]:
    observations: dict[str, dict[str, Any]] = {}
    for source_key in source_keys:
        source = sources.get(source_key)
        observation = (
            source.get("verified_dependency_observation")
            if isinstance(source, dict)
            else None
        )
        if click_dependency_cache.dependency_observation_is_valid(observation):
            observations[source_key] = {
                **observation,
                "paths": list(observation["paths"]),
            }
    return observations


def binding_path_digest(path: Path) -> str:
    return click_capability.digest({"path": os.path.normcase(str(path.resolve()))})


def authoritative_shard_digest(source: dict[str, Any]) -> str:
    metadata = source.get("shard")
    return click_capability.digest({
        "shard": metadata if click_evidence_shards.source_metadata_is_valid(metadata) else None
    })


def authoritative_current_bindings(
    *,
    sources: dict[str, Any],
    source_keys: set[str],
    group_digests: dict[str, str],
    cwd: Path,
    workspace_root: Path,
    environment_digests: dict[str, str],
    executable_digests: dict[str, str],
    host_coverage_digest: str,
    policy_digests: dict[str, str],
) -> dict[str, dict[str, Any]]:
    bindings: dict[str, dict[str, Any]] = {}
    for source_key in source_keys:
        source = sources.get(source_key)
        policy_digest = policy_digests.get(source_key)
        if (
            not isinstance(source, dict)
            or not isinstance(policy_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", policy_digest) is None
        ):
            continue
        bindings[source_key] = {
            "evidence_key": source_key,
            "check_digest": group_digests[source_key],
            "cwd_digest": binding_path_digest(cwd),
            "workspace_root_digest": binding_path_digest(workspace_root),
            "environment_digest": environment_digests[source_key],
            "executable_digest": executable_digests[source_key],
            "host_coverage_digest": host_coverage_digest,
            "policy_digest": policy_digest,
            "shard_digest": authoritative_shard_digest(source),
        }
    return bindings


def dependency_receipt_is_valid(receipt: Any) -> bool:
    if not isinstance(receipt, dict):
        return False
    provider = receipt.get("provider")
    manifest_digest = receipt.get("manifest_digest")
    entry_digest = receipt.get("entry_digest")
    dependency_digest = receipt.get("dependency_digest")
    observation_digest = receipt.get("observation_digest")
    observation = receipt.get("observation")
    manifest_is_valid = bool(
        isinstance(manifest_digest, str)
        and (
            provider == click_dependency_cache.CONTRACT_PROVIDER_NAME
            and not manifest_digest
            or provider
            in {
                click_dependency_cache.MANIFEST_PROVIDER_NAME,
                click_dependency_cache.COMBINED_PROVIDER_NAME,
            }
            and re.fullmatch(r"[0-9a-f]{64}", manifest_digest)
        )
    )
    return bool(
        provider in click_dependency_cache.PROVIDER_NAMES
        and manifest_is_valid
        and isinstance(entry_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", entry_digest)
        and isinstance(dependency_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", dependency_digest)
        and isinstance(observation_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", observation_digest)
        and click_dependency_cache.dependency_observation_is_valid(observation)
        and observation_digest
        == click_dependency_cache.dependency_observation_digest(observation)
        and click_dependency_cache.receipt_paths_are_valid(
            receipt.get("resolved_paths")
        )
    )


def dependency_receipt_matches(
    source: dict[str, Any],
    receipt: Any,
    *,
    contract_digest: str,
    revision: int,
    group_digest: str,
    git_root: str,
    environment_digest: str,
    executable_digest: str,
    host_coverage: dict[str, Any],
) -> bool:
    if not dependency_receipt_is_valid(receipt):
        return False
    if not click_dependency_cache.dependency_observation_is_complete(
        receipt["observation"]
    ):
        return False
    if not all(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
        for value in (
            contract_digest,
            group_digest,
            environment_digest,
            executable_digest,
        )
    ):
        return False
    verified_revision = source.get("verified_revision", -1)
    verified_at = source.get("verified_at", 0)
    if (
        not isinstance(verified_revision, int)
        or isinstance(verified_revision, bool)
        or verified_revision < 0
        or verified_revision >= revision
        or not isinstance(verified_at, int)
        or isinstance(verified_at, bool)
        or verified_at <= 0
        or not isinstance(source.get("verified_tree_digest"), str)
        or re.fullmatch(
            r"[0-9a-f]{64}", str(source.get("verified_tree_digest", ""))
        )
        is None
    ):
        return False
    return bool(
        source.get("status") == "stale"
        and source.get("verified_contract_digest") == contract_digest
        and source.get("verified_check_digest") == group_digest
        and source.get("verified_root") == git_root
        and source.get("verified_environment_digest") == environment_digest
        and source.get("verified_executable_digest") == executable_digest
        and click_host_coverage.receipt_is_current(host_coverage)
        and source.get("verified_host_coverage") == host_coverage
        and source.get("verified_dependency_provider") == receipt["provider"]
        # The full manifest digest is audit metadata. The normalized relevant
        # entry is the authority boundary, so unrelated settings may change.
        and source.get("verified_dependency_entry_digest")
        == receipt["entry_digest"]
        and source.get("verified_dependency_digest")
        == receipt["dependency_digest"]
        and source.get("verified_dependency_paths")
        == receipt["resolved_paths"]
        and source.get("verified_dependency_observation_digest")
        == receipt["observation_digest"]
        and source.get("verified_dependency_observation")
        == receipt["observation"]
    )


def clear_dependency_receipt(source: dict[str, Any]) -> None:
    source["verified_dependency_provider"] = ""
    source["verified_dependency_manifest_digest"] = ""
    source["verified_dependency_entry_digest"] = ""
    source["verified_dependency_digest"] = ""
    source["verified_dependency_paths"] = []
    source["verified_dependency_observation_digest"] = ""
    source["verified_dependency_observation"] = {}
    source["dependency_reuse_count"] = 0
    source["last_dependency_reused_at"] = 0
    source["last_dependency_reused_from_revision"] = -1


def store_dependency_receipt(
    source: dict[str, Any], receipt: Any
) -> None:
    clear_dependency_receipt(source)
    if not dependency_receipt_is_valid(receipt):
        return
    source["verified_dependency_provider"] = receipt["provider"]
    source["verified_dependency_manifest_digest"] = receipt["manifest_digest"]
    source["verified_dependency_entry_digest"] = receipt["entry_digest"]
    source["verified_dependency_digest"] = receipt["dependency_digest"]
    source["verified_dependency_paths"] = list(receipt["resolved_paths"])
    source["verified_dependency_observation_digest"] = receipt[
        "observation_digest"
    ]
    source["verified_dependency_observation"] = {
        **receipt["observation"],
        "paths": list(receipt["observation"]["paths"]),
    }


def promote_dependency_receipt(
    source: dict[str, Any],
    receipt: dict[str, Any],
    *,
    revision: int,
    tree_digest: str,
) -> None:
    prior_revision = int(source.get("verified_revision", -1))
    source["status"] = "passed"
    source["verified_revision"] = revision
    source["verified_tree_digest"] = tree_digest
    source["verified_dependency_manifest_digest"] = receipt["manifest_digest"]
    source["verified_dependency_entry_digest"] = receipt["entry_digest"]
    source["verified_dependency_digest"] = receipt["dependency_digest"]
    source["verified_dependency_paths"] = list(receipt["resolved_paths"])
    source["verified_dependency_observation_digest"] = receipt[
        "observation_digest"
    ]
    source["verified_dependency_observation"] = {
        **receipt["observation"],
        "paths": list(receipt["observation"]["paths"]),
    }
    source["last_exit_code"] = 0
    source["unchanged_failure_retries"] = 0
    source["dependency_reuse_count"] = int(
        source.get("dependency_reuse_count", 0)
    ) + 1
    source["last_dependency_reused_at"] = int(time.time()) or 1
    source["last_dependency_reused_from_revision"] = prior_revision


def clear_safe_change_receipt(source: dict[str, Any]) -> None:
    source["verified_safe_change_receipt"] = {}
    source["safe_change_reuse_count"] = 0
    source["last_safe_change_reused_at"] = 0
    source["last_safe_change_reused_from_revision"] = -1
    source["last_safe_change_paths"] = []
    source["last_safe_change_path_count"] = 0
    source["last_safe_change_decision_digest"] = ""


def store_safe_change_receipt(source: dict[str, Any], receipt: Any) -> None:
    clear_safe_change_receipt(source)
    if not click_change_policy.receipt_is_valid(receipt):
        return
    source["verified_safe_change_receipt"] = receipt


def safe_change_receipt_matches(
    source: dict[str, Any],
    decision: Any,
    *,
    contract_digest: str,
    revision: int,
    group_digest: str,
    git_root: str,
    environment_digest: str,
    executable_digest: str,
    host_coverage: dict[str, Any],
) -> bool:
    if not isinstance(decision, dict) or decision.get("status") != "reuse":
        return False
    receipt = decision.get("receipt")
    decision_digest = decision.get("decision_digest")
    changed_paths = decision.get("changed_paths")
    if (
        not click_change_policy.receipt_is_valid(receipt)
        or not click_change_policy.changed_paths_are_valid(changed_paths)
        or not isinstance(decision_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", decision_digest) is None
    ):
        return False
    verified_revision = source.get("verified_revision", -1)
    verified_at = source.get("verified_at", 0)
    return bool(
        source.get("status") == "stale"
        and isinstance(verified_revision, int)
        and not isinstance(verified_revision, bool)
        and 0 <= verified_revision < revision
        and isinstance(verified_at, int)
        and not isinstance(verified_at, bool)
        and verified_at > 0
        and source.get("verified_contract_digest") == contract_digest
        and source.get("verified_check_digest") == group_digest
        and source.get("verified_root") == git_root
        and source.get("verified_environment_digest") == environment_digest
        and source.get("verified_executable_digest") == executable_digest
        and click_host_coverage.receipt_is_current(host_coverage)
        and source.get("verified_host_coverage") == host_coverage
        and source.get("verified_safe_change_receipt", {})
        != {}
        and click_change_policy.receipt_is_valid(
            source.get("verified_safe_change_receipt")
        )
    )


def promote_safe_change_receipt(
    source: dict[str, Any],
    decision: dict[str, Any],
    *,
    revision: int,
    tree_digest: str,
) -> None:
    prior_revision = int(source.get("verified_revision", -1))
    changed_paths = list(decision["changed_paths"])
    source["status"] = "passed"
    source["verified_revision"] = revision
    source["verified_tree_digest"] = tree_digest
    source["verified_safe_change_receipt"] = decision["receipt"]
    source["last_exit_code"] = 0
    source["unchanged_failure_retries"] = 0
    source["safe_change_reuse_count"] = int(
        source.get("safe_change_reuse_count", 0)
    ) + 1
    source["last_safe_change_reused_at"] = int(time.time()) or 1
    source["last_safe_change_reused_from_revision"] = prior_revision
    source["last_safe_change_paths"] = changed_paths[:128]
    source["last_safe_change_path_count"] = len(changed_paths)
    source["last_safe_change_decision_digest"] = decision["decision_digest"]


def reuse_binding_reason(
    source: dict[str, Any],
    *,
    contract_digest: str,
    group_digest: str,
    git_root: str,
    tree_digest: str,
    environment_digest: str,
    executable_digest: str,
    host_coverage: dict[str, Any],
    cross_revision: bool = False,
) -> str:
    """Explain a failed reuse binding without changing its authority decision."""
    if source.get("verified_contract_digest") != contract_digest:
        return "contract-binding-changed"
    if source.get("verified_check_digest") != group_digest:
        return "check-binding-changed"
    if (
        source.get("verified_root") != git_root
        or not cross_revision
        and source.get("verified_tree_digest") != tree_digest
    ):
        return "workspace-ambiguous"
    if source.get("verified_executable_digest") != executable_digest:
        return "executable-binding-changed"
    if source.get("verified_environment_digest") != environment_digest:
        return "environment-binding-changed"
    if (
        not click_host_coverage.receipt_is_current(host_coverage)
        or source.get("verified_host_coverage") != host_coverage
    ):
        return "host-coverage-binding-changed"
    return "receipt-invalid"


def successor_binding_reason(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    group_digest: str,
    git_root: str,
    environment_digest: str,
    executable_digest: str,
    host_coverage: dict[str, Any],
) -> str:
    """Validate prior facts against the current lifecycle's bindings."""
    if (
        previous.get("dependency_patterns", []) != current.get("dependency_patterns", [])
        or previous.get("dependency_declaration_digest", "")
        != current.get("dependency_declaration_digest", "")
    ):
        return "contract-binding-changed"
    if previous.get("verified_check_digest") != group_digest:
        return "check-binding-changed"
    if previous.get("verified_root") != git_root:
        return "workspace-ambiguous"
    if previous.get("verified_executable_digest") != executable_digest:
        return "executable-binding-changed"
    if previous.get("verified_environment_digest") != environment_digest:
        return "environment-binding-changed"
    if (
        not click_host_coverage.receipt_is_current(host_coverage)
        or previous.get("verified_host_coverage") != host_coverage
    ):
        return "host-coverage-binding-changed"
    if previous.get("shard") != current.get("shard"):
        return "check-binding-changed"
    verified_at = previous.get("verified_at")
    if (
        previous.get("status") != "passed"
        or previous.get("last_exit_code") != 0
        or not isinstance(verified_at, int)
        or isinstance(verified_at, bool)
        or verified_at <= 0
        or not isinstance(previous.get("verified_tree_digest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", previous["verified_tree_digest"])
        is None
    ):
        return "successor-evidence-integrity-invalid"
    return ""


def requalify_successor_baseline(
    current: dict[str, Any],
    previous: dict[str, Any],
    *,
    contract_digest: str,
    revision: int,
    group_digest: str,
    units: int,
    tree_digest: str,
    environment_digest: str,
    executable_digest: str,
    host_coverage: dict[str, Any],
    exact_tree: bool,
) -> None:
    """Create a new-lifecycle baseline only after explicit binding checks."""
    current_shard = current.get("shard")
    current_patterns = list(current.get("dependency_patterns", []))
    current_declaration = current.get("dependency_declaration_digest", "")
    preserved = json.loads(json.dumps(previous))
    current.clear()
    current.update(preserved)
    if current_shard is None:
        current.pop("shard", None)
    else:
        current["shard"] = current_shard
    current.update(
        dependency_patterns=current_patterns,
        dependency_declaration_digest=current_declaration,
        status="passed" if exact_tree else "stale",
        verified_revision=revision if exact_tree else max(0, revision - 1),
        attempts=0,
        unchanged_failure_retries=0,
        last_exit_code=0,
        last_check_digest=group_digest,
        locked_check_digest=group_digest,
        reserved_units=units,
        reserved_check_digest=group_digest,
        verified_contract_digest=contract_digest,
        verified_check_digest=group_digest,
        verified_units=units,
        verified_tree_digest=tree_digest if exact_tree else previous["verified_tree_digest"],
        verified_environment_digest=environment_digest,
        verified_executable_digest=executable_digest,
        verified_host_coverage=dict(host_coverage),
        dependency_reuse_count=0,
        last_dependency_reused_at=0,
        last_dependency_reused_from_revision=-1,
        safe_change_reuse_count=0,
        last_safe_change_reused_at=0,
        last_safe_change_reused_from_revision=-1,
        last_safe_change_paths=[],
        last_safe_change_path_count=0,
        last_safe_change_decision_digest="",
        successor_reuse_count=0,
        last_successor_reused_at=0,
        last_successor_origin_batch_id="",
        last_successor_origin_evidence_session_id="",
        last_successor_origin_contract_id="",
        last_successor_candidate_digest="",
        last_successor_origin_revision=-1,
        last_successor_mode="",
    )


def mark_successor_reuse(
    source: dict[str, Any], metadata: dict[str, Any], *, mode: str
) -> None:
    source["successor_reuse_count"] = int(
        source.get("successor_reuse_count", 0)
    ) + 1
    source["last_successor_reused_at"] = int(time.time()) or 1
    source["last_successor_origin_batch_id"] = metadata["batch_id"]
    source["last_successor_origin_evidence_session_id"] = metadata.get("evidence_session_id", "")
    source["last_successor_origin_contract_id"] = metadata.get("contract_id", "")
    source["last_successor_candidate_digest"] = metadata["candidate_digest"]
    source["last_successor_origin_revision"] = metadata["origin_revision"]
    source["last_successor_mode"] = mode
    source["verified_at"] = int(time.time()) or 1


def observation_nonreuse_reason(observation: Any) -> str:
    if not click_dependency_cache.dependency_observation_is_valid(observation):
        return "observer-incomplete"
    if observation.get("provider") != (
        click_dependency_cache.AUTHORITATIVE_OBSERVATION_PROVIDER_NAME
    ):
        return "observer-incomplete"
    if observation.get("external_access") is True:
        return "external-input-unmodeled"
    if not click_dependency_cache.dependency_observation_is_complete(observation):
        return "observer-incomplete"
    return "observed-input-changed"


def default_incremental_reason(source: dict[str, Any]) -> tuple[str, bool]:
    """Return a conservative initial run reason and evaluability marker."""
    if source.get("status") == "failed":
        return "previous-verification-failed", False
    if source.get("status") != "stale":
        return "no-passing-evidence", False
    observation = source.get("verified_dependency_observation")
    if observation:
        reason = observation_nonreuse_reason(observation)
        return reason, reason in {"observer-incomplete", "external-input-unmodeled"}
    if click_change_policy.receipt_is_valid(
        source.get("verified_safe_change_receipt")
    ):
        return "safe-change-policy-not-covered", False
    return "policy-unavailable", False


def canonical_incremental_plan(
    sources: dict[str, Any],
    *,
    requested_keys: set[str],
    group_digests: dict[str, str],
    environment_digests: dict[str, str],
    executable_digests: dict[str, str],
    host_coverage_digest: str,
    observer_mode: str,
    revision: int,
    previous_revisions: dict[str, int],
    reused_keys: set[str],
    dependency_reused_keys: set[str],
    safe_change_reused_keys: set[str],
    not_evaluable_keys: set[str],
    reason_codes: dict[str, str],
) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    for source_key in sorted(requested_keys):
        source = sources[source_key]
        if source_key in dependency_reused_keys:
            selected = "reuse-dependency"
            authority = "runtime-dependency-observation"
        elif source_key in safe_change_reused_keys:
            selected = "reuse-safe-change"
            authority = "repository-safe-change-policy"
        elif source_key in reused_keys:
            selected = "reuse-exact"
            authority = "exact-receipt"
        elif source_key in not_evaluable_keys:
            selected = "not-evaluable"
            authority = "none"
        else:
            selected = "run"
            authority = "runner"
        timing_binding = click_incremental.timing_binding_digest(
            source_key=source_key,
            check_digest=group_digests[source_key],
            environment_digest=environment_digests[source_key],
            executable_digest=executable_digests[source_key],
            host_coverage_digest=host_coverage_digest,
            observer_mode=observer_mode,
        )
        baseline = source.get("last_success_duration_baseline")
        if not click_incremental.baseline_is_suitable(
            baseline,
            source_key=source_key,
            check_digest=group_digests[source_key],
            observer_mode=observer_mode,
            timing_binding_digest=timing_binding,
        ):
            baseline = None
        avoided = baseline["duration_ms"] if baseline is not None else None
        decisions.append(
            click_incremental.decision(
                source_key=source_key,
                decision=selected,
                reason_code=reason_codes[source_key],
                current_revision=revision,
                previous_revision=previous_revisions[source_key],
                check_digest=group_digests[source_key],
                authority_source=authority,
                estimated_avoided_ms=avoided if source_key in reused_keys else 0,
                duration_baseline=baseline,
            )
        )
    return click_incremental.build_plan(decisions, current_revision=revision)
