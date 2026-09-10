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

(click_capability, click_change_policy, click_dependency_cache, click_evidence, click_evidence_shards, click_host_coverage, click_incremental,) = click_import_bootstrap.load_siblings(
    __package__, "click_capability", "click_change_policy", "click_dependency_cache", "click_evidence", "click_evidence_shards", "click_host_coverage", "click_incremental"
)

def automatic_observation_required(source: dict[str, Any]) -> bool:
    return bool(
        source.get("automatic_observation_required", False)
        or source.get("verified_dependency_provider") == click_dependency_cache.AUTOMATIC_PROVIDER_NAME
    )


def automatic_observation_missing(source: dict[str, Any]) -> bool:
    receipt = source.get("verified_safe_change_receipt", {})
    return bool(
        automatic_observation_required(source)
        and not click_dependency_cache.bound_dependency_observation_is_reusable(
            source.get("verified_dependency_observation")
        )
        # A current, explicit owner input policy has its own admission checks.
        # A path-only safe-change policy cannot replace lost input collection.
        and not (click_change_policy.receipt_is_valid(receipt)
                 and receipt.get("provider") == click_change_policy.INPUT_PROVIDER_NAME)
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
    if (
        not isinstance(source, dict)
        or not click_evidence.revision_is_valid(revision)
        or not click_evidence.revision_is_valid(source.get("verified_revision"))
    ):
        return False
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
        and not automatic_observation_missing(source)
        and source["verified_revision"] == revision
        and source.get("verified_contract_digest") == contract_digest
        and source.get("verified_check_digest") == group_digest
        and source.get("verified_root") == git_root
        and source.get("verified_tree_digest") == tree_digest
        and source.get("verified_environment_digest") == environment_digest
        and source.get("verified_executable_digest") == executable_digest
        and click_host_coverage.receipt_is_current(host_coverage)
        and source.get("verified_host_coverage") == host_coverage
        and (
            not click_change_policy.requires_input_receipt(Path(git_root), group_digest)
            or (
                source.get("verified_safe_change_receipt", {}).get("provider")
                == click_change_policy.INPUT_PROVIDER_NAME
                and click_change_policy.inputs_are_current(
                    Path(git_root), source["verified_safe_change_receipt"]
                )
            )
        )
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


def changed_observed_inputs(
    sources: dict[str, Any], source_keys: set[str], *,
    project: Path, runtime: dict[str, Any] | None,
) -> set[str]:
    """Invalidate admitted receipts after sibling execution; never grant reuse.

    Admission already checked each source's current command and bindings. This
    second boundary checks its captured inputs and runtime again, because an
    executed sibling can change ignored data without changing the Git tree.
    """
    changed = set()
    for source_key in source_keys:
        observation = sources[source_key].get("verified_dependency_observation")
        if not click_dependency_cache.bound_dependency_observation_is_reusable(observation):
            continue
        if not click_dependency_cache.bound_dependency_observation_matches(
            observation, project=project, runtime=runtime,
            binding={field: observation["binding"][field]
                     for field in click_dependency_cache.AUTHORITATIVE_CURRENT_BINDING_FIELDS},
        ):
            changed.add(source_key)
    return changed


def binding_path_digest(path: Path) -> str:
    return click_capability.digest({"path": os.path.normcase(str(path.resolve()))})


def authoritative_shard_digest(source: dict[str, Any]) -> str:
    metadata = source.get("shard")
    return click_capability.digest({
        "shard": click_evidence_shards.reuse_binding(metadata)
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
    if not isinstance(provider, str):
        return False
    manifest_digest = receipt.get("manifest_digest")
    entry_digest = receipt.get("entry_digest")
    dependency_digest = receipt.get("dependency_digest")
    observation_digest = receipt.get("observation_digest")
    observation = receipt.get("observation")
    manifest_is_valid = bool(
        isinstance(manifest_digest, str)
        and (
            provider in {click_dependency_cache.CONTRACT_PROVIDER_NAME, click_dependency_cache.AUTOMATIC_PROVIDER_NAME}
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
        and (
            provider != click_dependency_cache.AUTOMATIC_PROVIDER_NAME
            or click_dependency_cache.bound_dependency_observation_is_reusable(observation)
        )
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
    if not isinstance(source, dict) or not click_evidence.revision_is_valid(revision):
        return False
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
        not click_evidence.revision_is_valid(verified_revision)
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
    # Capture loss must not silently weaken a prior automatic input boundary
    # into a Git-only success receipt, including across successor lifecycles.
    source["automatic_observation_required"] = automatic_observation_required(source)
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
    source["automatic_observation_required"] = automatic_observation_required(source)
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


def _promotion_revision_and_count(
    source: dict[str, Any], *, revision: int, tree_digest: str, counter: str
) -> tuple[int, int]:
    """Check mutation preconditions after the caller matched all reuse bindings."""
    prior_revision = source.get("verified_revision")
    count = source.get(counter, 0)
    if (
        source.get("status") != "stale"
        or not click_evidence.revision_is_valid(prior_revision)
        or not click_evidence.revision_is_valid(revision)
        or prior_revision >= revision
        or not isinstance(tree_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", tree_digest) is None
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
    ):
        raise ValueError("Receipt promotion requires valid revisions, tree, and reuse count.")
    return prior_revision, count


def promote_dependency_receipt(
    source: dict[str, Any],
    receipt: dict[str, Any],
    *,
    revision: int,
    tree_digest: str,
) -> None:
    """Apply a matched dependency receipt; reject malformed inputs before mutation."""
    if not isinstance(source, dict) or not dependency_receipt_is_valid(receipt):
        raise ValueError("Receipt promotion requires a valid source and dependency receipt.")
    prior_revision, reuse_count = _promotion_revision_and_count(
        source, revision=revision, tree_digest=tree_digest, counter="dependency_reuse_count"
    )
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
    source["dependency_reuse_count"] = reuse_count + 1
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
    decision_context: dict[str, Any] | None = None,
) -> bool:
    if (
        not isinstance(source, dict)
        or not click_evidence.revision_is_valid(revision)
        or not click_change_policy.reuse_decision_matches(
            decision, source.get("verified_safe_change_receipt"),
            check_digest=group_digest, decision_context=decision_context,
        )
    ):
        return False
    assert isinstance(decision_context, dict)
    if decision_context["revision"] != revision or decision_context["git_root"] != git_root:
        return False
    verified_revision = source.get("verified_revision", -1)
    verified_at = source.get("verified_at", 0)
    return bool(
        source.get("status") == "stale"
        and not automatic_observation_missing(source)
        and click_evidence.revision_is_valid(verified_revision)
        and verified_revision < revision
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
    )


def promote_safe_change_receipt(
    source: dict[str, Any],
    decision: dict[str, Any],
    *,
    revision: int,
    tree_digest: str,
    decision_context: dict[str, Any] | None = None,
) -> None:
    """Apply a matched safe-change decision only after validating mutation inputs."""
    if (
        not isinstance(source, dict)
        or not isinstance(decision_context, dict)
        or decision_context.get("tree_digest") != tree_digest
        or not safe_change_receipt_matches(
            source, decision, revision=revision,
            contract_digest=source.get("verified_contract_digest", ""),
            group_digest=source.get("verified_check_digest", ""),
            git_root=source.get("verified_root", ""),
            environment_digest=source.get("verified_environment_digest", ""),
            executable_digest=source.get("verified_executable_digest", ""),
            host_coverage=source.get("verified_host_coverage", {}),
            decision_context=decision_context,
        )
    ):
        raise ValueError("Receipt promotion requires a valid source and safe-change decision.")
    prior_revision, reuse_count = _promotion_revision_and_count(
        source, revision=revision, tree_digest=tree_digest, counter="safe_change_reuse_count"
    )
    changed_paths = list(decision["changed_paths"])
    source["status"] = "passed"
    source["verified_revision"] = revision
    source["verified_tree_digest"] = tree_digest
    source["verified_safe_change_receipt"] = decision["receipt"]
    source["last_exit_code"] = 0
    source["unchanged_failure_retries"] = 0
    source["safe_change_reuse_count"] = reuse_count + 1
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
    if automatic_observation_missing(source):
        return "observer-incomplete"
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
    if any(
        source.get("shard") is not None
        and not click_evidence_shards.source_metadata_is_valid(source["shard"])
        for source in (previous, current)
    ) or click_evidence_shards.reuse_binding(previous.get("shard")) != (
        click_evidence_shards.reuse_binding(current.get("shard"))
    ):
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
    if (
        not isinstance(current, dict)
        or not isinstance(previous, dict)
        or not click_evidence.revision_is_valid(revision)
        or not click_evidence.revision_is_valid(previous.get("verified_revision"))
    ):
        raise ValueError("Successor requalification requires valid evidence revisions.")
    if (
        current.get("kind") != "argv"
        or previous.get("kind") != "argv"
        or current.get("status") != "ready"
        or current.get("attempts") != 0
        or current.get("verified_contract_digest")
        or not isinstance(exact_tree, bool)
        or not isinstance(units, int)
        or isinstance(units, bool)
        or units < 0
        or not isinstance(previous.get("verified_root"), str)
        or not previous["verified_root"]
        or not all(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
            for value in (contract_digest, group_digest, tree_digest, environment_digest, executable_digest)
        )
        or successor_binding_reason(
            previous, current, group_digest=group_digest,
            git_root=previous["verified_root"], environment_digest=environment_digest,
            executable_digest=executable_digest, host_coverage=host_coverage,
        )
    ):
        raise ValueError("Successor requalification requires passing facts and current bindings.")
    candidate = click_evidence.fresh_successor_source(current, previous)
    candidate.update(
        status="passed" if exact_tree else "stale",
        verified_revision=revision if exact_tree else max(0, revision - 1),
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
    )
    current.clear()
    current.update(candidate)


def mark_successor_reuse(
    source: dict[str, Any], metadata: dict[str, Any], *, mode: str
) -> None:
    count = source.get("successor_reuse_count", 0)
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError("Successor reuse requires a valid reuse count.")
    source["successor_reuse_count"] = count + 1
    source["last_successor_reused_at"] = int(time.time()) or 1
    source["last_successor_origin_batch_id"] = metadata["batch_id"]
    source["last_successor_origin_evidence_session_id"] = metadata.get("evidence_session_id", "")
    source["last_successor_origin_contract_id"] = metadata.get("contract_id", "")
    source["last_successor_candidate_digest"] = metadata["candidate_digest"]
    source["last_successor_origin_revision"] = metadata["origin_revision"]
    source["last_successor_mode"] = mode


def observation_nonreuse_reason(observation: Any) -> str:
    if click_dependency_cache.conditional_dependency_observation_is_valid(observation):
        return "observed-input-changed"
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
        conditional = (source_key in reused_keys and
                       click_dependency_cache.conditional_dependency_observation_is_valid(
                           source.get("verified_dependency_observation")))
        if conditional:
            authority = "conditional-js-observation"
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
                reason_code="conditional-observed-inputs-current" if conditional else reason_codes[source_key],
                current_revision=revision,
                previous_revision=previous_revisions[source_key],
                check_digest=group_digests[source_key],
                authority_source=authority,
                estimated_avoided_ms=avoided if source_key in reused_keys else 0,
                duration_baseline=baseline,
            )
        )
    return click_incremental.build_plan(decisions, current_revision=revision)
