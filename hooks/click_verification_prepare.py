#!/usr/bin/env python3
"""Admit verification requests and choose child execution or evidence reuse."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
import hashlib
import hmac
import json
import os
import secrets
import sys
import time

if __package__:
    from . import click_import_bootstrap
else:
    import click_import_bootstrap

(_common,) = click_import_bootstrap.load_siblings(__package__, "click_verification_common")
CONTRACT_STATE_SCHEMA_VERSION = _common.CONTRACT_STATE_SCHEMA_VERSION
VERIFICATION_PROTOCOL_VERSION = _common.VERIFICATION_PROTOCOL_VERSION
VERIFY_RUNNING_TTL_SECONDS = _common.VERIFY_RUNNING_TTL_SECONDS
_authoritative_current_bindings = _common._authoritative_current_bindings
_canonical_incremental_plan = _common._canonical_incremental_plan
click_diagnostics = _common.click_diagnostics
_default_failure_collection = _common._default_failure_collection
_default_incremental_reason = _common._default_incremental_reason
_dependency_declarations = _common._dependency_declarations
_dependency_observations = _common._dependency_observations
_dependency_receipt_matches = _common._dependency_receipt_matches
_evidence_is_current = _common._evidence_is_current
_evidence_key = _common._evidence_key
_evidence_keys_for_kind = _common._evidence_keys_for_kind
_evidence_sources = _common._evidence_sources
_fresh_external_evidence_state = _common._fresh_external_evidence_state
_fresh_mutation_state = _common._fresh_mutation_state
_fresh_observation_state = _common._fresh_observation_state
_git_capture = _common._git_capture
_git_workspace_snapshot = _common._git_workspace_snapshot
_mark_successor_reuse = _common._mark_successor_reuse
_mutation_is_running = _common._mutation_is_running
_observation_is_running = _common._observation_is_running
_observation_nonreuse_reason = _common._observation_nonreuse_reason
_observer_environment = _common._observer_environment
_promote_dependency_receipt = _common._promote_dependency_receipt
_promote_safe_change_receipt = _common._promote_safe_change_receipt
_read_contract_state = _common._read_contract_state
_requalify_successor_baseline = _common._requalify_successor_baseline
_reuse_binding_reason = _common._reuse_binding_reason
_safe_change_receipt_matches = _common._safe_change_receipt_matches
_save_contract_state = _common._save_contract_state
_successor_binding_reason = _common._successor_binding_reason
_tool_working_directory = _common._tool_working_directory
_validate_verification_batch = _common._validate_verification_batch
_verification_command_plans = _common._verification_command_plans
_verification_environment = _common._verification_environment
_verification_environment_binding = _common._verification_environment_binding
_verification_environment_binding_digest = _common._verification_environment_binding_digest
_verification_group_digest = _common._verification_group_digest
_verification_group_policy = _common._verification_group_policy
_verification_group_units = _common._verification_group_units
_verification_groups = _common._verification_groups
_verification_host_coverage_binding_digest = _common._verification_host_coverage_binding_digest
_verification_receipt_matches = _common._verification_receipt_matches
click_capability = _common.click_capability
click_change_policy = _common.click_change_policy
click_dependency_cache = _common.click_dependency_cache
click_evidence = _common.click_evidence
click_evidence_shards = _common.click_evidence_shards
click_host_coverage = _common.click_host_coverage
click_incremental = _common.click_incremental
click_observer_control = _common.click_observer_control
click_observer_runtime = _common.click_observer_runtime
click_runtime_state = _common.click_runtime_state
click_shadow_intelligence = _common.click_shadow_intelligence
click_state = _common.click_state
click_verification_bindings = _common.click_verification_bindings
click_verification_inputs = _common.click_verification_inputs
click_verification_policy = _common.click_verification_policy
click_verification_reuse = _common.click_verification_reuse

def _set_group_policy(
    checks: list[dict[str, Any]], policy: dict[str, Any]
) -> None:
    """Apply one normalized policy without changing executable identity."""
    for check in checks:
        check.pop("reuse", None)
        check.pop("inputs", None)
        check.pop("outputs_required", None)
        if policy["reuse"] != "conditional":
            check["reuse"] = policy["reuse"]
        if policy["inputs"]:
            check["inputs"] = list(policy["inputs"])
        if policy["outputs_required"]:
            check["outputs_required"] = True


def _merge_source_policy(
    source: dict[str, Any], requested: dict[str, Any]
) -> dict[str, Any]:
    """Persist owner restrictions and return this request's effective policy."""
    stored_reuse = str(source.get("reuse_policy", "conditional"))
    requested_reuse = str(requested.get("reuse", "conditional"))
    persistent_reuse = (
        "always-run"
        if "always-run" in {stored_reuse, requested_reuse}
        else "conditional"
    )
    effective_reuse = (
        "always-run" if persistent_reuse == "always-run" else requested_reuse
    )
    stored_inputs, _ = click_verification_inputs.normalize_patterns(
        source.get("input_patterns", [])
    )
    requested_inputs, _ = click_verification_inputs.normalize_patterns(
        requested.get("inputs", [])
    )
    combined_inputs, combined_error = click_verification_inputs.normalize_patterns(
        sorted(set(stored_inputs or ()) | set(requested_inputs or ()))
    )
    if combined_error or combined_inputs is None:
        raise ValueError("verification input policy could not be normalized")
    if list(combined_inputs) != list(stored_inputs or ()):
        source["verified_input_digest"] = ""
    outputs_required = bool(
        source.get("outputs_required", False)
        or requested.get("outputs_required", False)
    )
    source["reuse_policy"] = persistent_reuse
    source["input_patterns"] = list(combined_inputs)
    source["outputs_required"] = outputs_required
    source.setdefault("verified_input_digest", "")
    return {
        "reuse": effective_reuse,
        "inputs": list(combined_inputs),
        "outputs_required": outputs_required,
    }


def _validated_shard_checks(
    plan: dict[str, Any], *, scale: str
) -> tuple[list[dict[str, Any]] | None, str]:
    candidate = {
        "version": VERIFICATION_PROTOCOL_VERSION,
        "checks": [
            {
                "evidence_id": str(child["evidence_id"]),
                "argv": list(argv),
                "class": "broad",
            }
            for child in plan.get("children", [])
            if isinstance(child, dict)
            for argv in child.get("checks", [])
            if isinstance(argv, list)
        ],
    }
    normalized, _, error = _validate_verification_batch(
        json.dumps(candidate, separators=(",", ":")), scale, None
    )
    if error or normalized is None:
        return None, error or "Evidence Shards child checks are invalid."
    grouped, grouping_error = _verification_groups(normalized)
    if grouping_error:
        return None, grouping_error
    expected = {
        str(child["source_key"]): str(child["check_digest"])
        for child in plan.get("children", [])
        if isinstance(child, dict)
    }
    if set(grouped) != set(expected) or any(
        _verification_group_digest(checks) != expected[source_key]
        for source_key, checks in grouped.items()
    ):
        return None, "Evidence Shards child check identity is inconsistent."
    return list(normalized["checks"]), ""


def _expand_evidence_shards(
    state: dict[str, Any],
    batch: dict[str, Any],
    *,
    scale: str,
    workspace: Path,
    git_capture: Callable[[Path, list[str]], bytes | None],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str], str]:
    """Expand submitted broad parents while retaining their approval identity."""
    evidence_state = state.get("evidence_state")
    sources = evidence_state.get("sources") if isinstance(evidence_state, dict) else None
    if not isinstance(evidence_state, dict) or not isinstance(sources, dict):
        return None, None, [], "Click Evidence Shards registry is unavailable."
    grouped, grouping_error = _verification_groups(batch)
    if grouping_error:
        return None, None, [], grouping_error

    expanded: list[dict[str, Any]] = []
    advisories: list[str] = []
    # Presentation only: a shard child is executed under a synthetic id that
    # the caller never chose. Its label names the caller's own evidence id and
    # the committed shard id, so the host recognizes what ran. Identity,
    # receipts and check digests are untouched by it.
    source_labels: dict[str, str] = {}
    for parent_source_key, parent_checks in grouped.items():
        evidence_id = str(parent_checks[0].get("evidence_id", "argv"))
        parent_check_digest = _verification_group_digest(parent_checks)
        submitted_source = sources.get(parent_source_key)
        if click_evidence_shards.is_child_source(submitted_source):
            return (
                None,
                None,
                advisories,
                "Evidence shard children are internal. Submit the declared broad "
                "parent evidence id so Click can revalidate the complete plan.",
            )
        active = click_evidence_shards.active_set(
            evidence_state, parent_source_key
        )
        if active is not None and active.get("parent_check_digest") != parent_check_digest:
            return (
                None,
                None,
                advisories,
                "A sharded argv evidence source is locked to a different broad "
                "parent check set. Reuse that set or stage a new contract.",
            )
        if active is None and isinstance(submitted_source, dict):
            incompatible_bindings = (
                (
                    "reserved_check_digest",
                    "An argv evidence source is already reserved to a different exact "
                    "check set for this contract. Reuse that set or stage a new contract.",
                ),
                (
                    "locked_check_digest",
                    "A previously successful argv evidence source is locked to its exact "
                    "check set. Re-run that set after the relevant mutation.",
                ),
                (
                    "last_check_digest",
                    "An argv evidence source changed its check set without an intervening "
                    "mutation. Fix the implementation or reuse the original check set.",
                ),
            )
            for field, message in incompatible_bindings:
                bound = str(submitted_source.get(field, ""))
                if bound and bound != parent_check_digest:
                    return None, None, advisories, message, {}
        eligible = any(
            check.get("class") in {"broad", "deep"} for check in parent_checks
        )
        if not eligible and active is None:
            expanded.extend(parent_checks)
            continue

        decision = click_evidence_shards.resolve_plan(
            workspace,
            parent_checks,
            parent_source_key=parent_source_key,
            git_capture=git_capture,
        )
        plan_current = decision.get("status") == "sharded"
        shard_checks: list[dict[str, Any]] | None = None
        validation_error = ""
        if plan_current:
            shard_checks, validation_error = _validated_shard_checks(
                decision, scale=scale
            )
            plan_current = shard_checks is not None and not validation_error
            if plan_current:
                parent_policy, validation_error = _verification_group_policy(
                    parent_checks
                )
                plan_current = parent_policy is not None and not validation_error
                if plan_current:
                    assert shard_checks is not None and parent_policy is not None
                    _set_group_policy(shard_checks, parent_policy)

        if active is not None and not plan_current:
            sources, collapse_error = click_evidence.collapse_shard_plan(
                state, parent_source_key
            )
            if collapse_error or sources is None:
                return None, None, advisories, collapse_error, {}
            evidence_state = state["evidence_state"]
            reason = validation_error or str(
                decision.get("reason", "plan-unavailable")
            )
            advisories.append(
                f"Click Evidence Shards [{evidence_id}]: {reason}; running the "
                "original broad suite."
            )
            expanded.extend(parent_checks)
            continue

        if active is None and plan_current:
            sources, activation_error = click_evidence.activate_shard_plan(
                state, parent_source_key, decision
            )
            if activation_error or sources is None:
                return None, None, advisories, activation_error, {}
            evidence_state = state["evidence_state"]
        elif active is not None and plan_current and not (
            click_evidence_shards.plan_matches_shard_set(decision, active)
        ):
            sources, refresh_error = click_evidence.refresh_shard_plan(
                state, parent_source_key, decision
            )
            if refresh_error or sources is None:
                return None, None, advisories, refresh_error, {}
            evidence_state = state["evidence_state"]
            advisories.append(
                f"Click Evidence Shards [{evidence_id}]: complete committed plan "
                "refreshed; requalifying each child's evidence."
            )
        if plan_current:
            assert shard_checks is not None
            expanded.extend(shard_checks)
            for child in decision.get("children", []):
                if isinstance(child, dict) and isinstance(child.get("source_key"), str):
                    source_labels[child["source_key"]] = f"{evidence_id}[{child.get('shard_id', '')}]"
            advisories.append(
                f"Click Evidence Shards [{evidence_id}]: expanded the broad suite "
                f"into {len(decision['children'])} independent shard(s)."
            )
            continue

        reason = validation_error or str(
            decision.get("reason", "plan-unavailable")
        )
        if (
            decision.get("status") == "fallback" or validation_error
        ) and reason not in {"manifest-not-committed", "git-root-unavailable"}:
            advisories.append(
                f"Click Evidence Shards [{evidence_id}]: {reason}; running the "
                "original broad suite."
            )
        expanded.extend(parent_checks)

    failure_collection = batch["failure_collection"]
    expanded_ids = {str(check.get("evidence_id", "")) for check in expanded}
    if (
        failure_collection.get("mode") == "bounded"
        and not set(failure_collection["independent_sources"]).issubset(expanded_ids)
    ):
        # Internal shard children are never inferred to be the independent
        # sources selected by the user for this request.
        failure_collection = _default_failure_collection()
        advisories.append(
            "Click bounded failure collection stayed off because automatic "
            "sharding changed the submitted source identities; shard independence "
            "was not inferred."
        )
    expanded_batch: dict[str, Any] = {
        "version": VERIFICATION_PROTOCOL_VERSION,
        "checks": expanded,
        "reporting": batch["reporting"],
        "failure_collection": failure_collection,
    }
    if isinstance(batch.get("workdir"), str):
        expanded_batch["workdir"] = str(batch["workdir"])
    return (
        expanded_batch,
        sources,
        advisories,
        "",
        source_labels,
    )


def runner_command(
    event: dict[str, Any],
    batch: dict[str, Any],
    batch_digest: str,
    runner_token: str,
    *,
    runner_script: Path,
    render_command: Callable[[list[str]], str],
) -> str:
    arguments = [
        sys.executable,
        str(runner_script),
        "--state-root",
        str(click_state.state_root().resolve()),
        "run-verification",
        str(click_state.contract_path(event).resolve()),
        batch_digest,
        runner_token,
        click_capability.encode_request(batch),
    ]
    return render_command(arguments)


def _prepare_verification(
    event: dict[str, Any], raw: str, *, runner_script: Path,
    render_command: Callable[[list[str]], str],
    git_workspace_snapshot: Callable[..., dict[str, Any] | None] = _git_workspace_snapshot,
    git_capture: Callable[[Path, list[str]], bytes | None] = _git_capture,
) -> tuple[str, str, str]:
    # A preparation hook and its runner are different processes. Measure their
    # local elapsed segments, not a subtraction of cross-process clock origins.
    started = time.perf_counter_ns()
    request_started = time.monotonic_ns()
    trace: dict[str, Any] = {}
    with click_verification_bindings.binding_pass():
        result = _prepare_verification_impl(
            event, raw, runner_script=runner_script, render_command=render_command,
            git_workspace_snapshot=git_workspace_snapshot, git_capture=git_capture,
            measurement=trace,
        )
    elapsed = (time.perf_counter_ns() - started) / 1_000_000
    try:
        state = _read_contract_state(event)
        verification = state.get("verification")
        if (
            not click_runtime_state.view(state).execution_authorized
            or not isinstance(verification, dict)
            or not click_evidence.revision_is_valid(verification.get("mutation_revision"))
        ):
            return result
        tool_id = event.get("tool_use_id")
        request_id = (
            hashlib.sha256(json.dumps([
                event.get("session_id"), event.get("turn_id"), tool_id,
                hashlib.sha256(raw.encode()).hexdigest(),
            ], sort_keys=True).encode()).hexdigest()[:32]
            if isinstance(tool_id, str) and tool_id else secrets.token_hex(16)
        )
        previous_id = verification.get(click_incremental.CURRENT_BATCH_FIELD)
        previous_clock = verification.get("incremental_request_clock")
        previous_batch = click_incremental.current_batch(verification)
        batch = click_incremental.new_batch(
            trace.get("plan"), batch_id=request_id,
            revision=verification["mutation_revision"], prepared_ms=elapsed,
            requested=trace.get("requested"), labels=trace.get("labels"),
            reuse_origins=trace.get("reuse_origins"),
            command_plans=trace.get("command_plans"),
            task={
                "mode": state.get("runtime_mode"),
                "id": state.get("contract_id") if state.get("runtime_mode") == "guarded" else state.get("evidence_session_id"),
                "name": click_incremental.sanitize_presentation(state.get("presentation"))["name"],
            },
        )
        if click_incremental.store_batch(verification, batch):
            click_incremental.start_request_clock(verification, request_id, started_ns=request_started)
            if result[1]:
                click_incremental.record_control_event(state, event, "verification-request-rejected")
                click_incremental.reject_batch(verification)
                if previous_batch and previous_batch["status"] in {"planned", "running"}:
                    verification[click_incremental.CURRENT_BATCH_FIELD] = previous_id
                    if previous_clock is not None:
                        verification["incremental_request_clock"] = previous_clock
            elif trace.get("all_reused"):
                click_incremental.finish_reuse(verification)
            if result[2]:
                click_incremental.record_control_event(state, event, "verification-guidance")
            _save_contract_state(event, state)
            if trace.get("all_reused"):
                result = (result[0], result[1], "\n".join(filter(None, (result[2], click_incremental.host_summary(verification, _evidence_sources(state))))))
    except Exception:
        # Measurements cannot admit/reject a command or grant reuse authority.
        pass
    return result


def _invalidate_workspace_evidence(
    state: dict[str, Any], verification: dict[str, Any],
    sources: dict[str, Any], revision: int,
) -> int:
    """Invalidate receipts after observed out-of-band workspace drift."""
    revision += 1
    verification["mutation_revision"] = revision
    verification["status"] = "ready"
    verification["verified_revision"] = -1
    verification["failed_revision"] = -1
    verification["workspace_changed"] = True
    for source in sources.values():
        if not isinstance(source, dict):
            continue
        if source.get("status") in {"passed", "observed"}:
            source["status"] = "stale"
        else:
            source["status"] = "ready"
        source["verified_revision"] = -1
        source["unchanged_failure_retries"] = 0
        source["last_exit_code"] = None
    external = state.get("external_evidence")
    browser_required = bool(
        isinstance(external, dict)
        and external.get("browser_required") is True
    )
    browser_source_key = (
        str(external.get("browser_source_key", ""))
        if isinstance(external, dict)
        else ""
    )
    state["external_evidence"] = _fresh_external_evidence_state(
        required=browser_required,
        source_key=browser_source_key,
    )
    state["observations"] = _fresh_observation_state()
    return revision


def _prepare_verification_impl(
    event: dict[str, Any],
    raw: str,
    *,
    runner_script: Path,
    render_command: Callable[[list[str]], str],
    git_workspace_snapshot: Callable[..., dict[str, Any] | None] = (
        _git_workspace_snapshot
    ),
    git_capture: Callable[[Path, list[str]], bytes | None] = _git_capture,
    measurement: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    state = _read_contract_state(event)
    runtime = click_runtime_state.view(state)
    if not runtime.execution_authorized:
        return "", "Start Guarded or Evidence runtime state before verification.", ""
    mutation = state.get("mutation")
    if _mutation_is_running(mutation):
        return (
            "",
            "Wait for the structured Click mutation to finish before verification.",
            "",
        )
    if isinstance(mutation, dict) and mutation.get("status") == "running":
        state["mutation"] = _fresh_mutation_state()
    verification = state.get("verification")
    if not isinstance(verification, dict):
        return (
            "",
            "Click verification state is unavailable; stage and approve again.",
            "",
        )
    revision = verification.get("mutation_revision")
    if not click_evidence.revision_is_valid(revision):
        return "", "Click verification mutation revision is malformed.", ""
    prior_verification_changed_workspace = (
        verification.get("workspace_changed") is True
    )
    scale = str(verification.get("scale", ""))
    if not click_verification_policy.is_profile(scale):
        return (
            "",
            "Approved Click verification scale is invalid; stage and approve again.",
            "",
        )
    verification_advisories: list[str] = []
    sources = _evidence_sources(state)
    if sources is None:
        return (
            "",
            "This active contract predates evidence-id completion tracking. Cancel it, "
            "stage the proposal again, and obtain fresh approval.",
            "",
        )
    if not sources and runtime.guarded_approved:
        return (
            "",
            "Click evidence state is unavailable or malformed; cancel and restage.",
            "",
        )
    host_coverage = click_host_coverage.receipt_for_event(event)
    if not click_host_coverage.receipt_is_current(host_coverage):
        return (
            "",
            "Click could not establish the current host Hook coverage identity.",
            "",
        )

    observations = state.get("observations")
    if isinstance(observations, dict):
        entries = observations.get("entries")
        if isinstance(entries, dict):
            for entry in entries.values():
                if _observation_is_running(entry):
                    return (
                        "",
                        "Wait for the approved read or search to finish before starting "
                        "the final verification batch.",
                        "",
                    )

    provisional, _, error = _validate_verification_batch(raw, scale, None)
    if error:
        return "", error, ""
    assert provisional is not None
    provisional_groups, grouping_error = _verification_groups(provisional)
    if grouping_error:
        return "", grouping_error, ""
    if measurement is not None:
        measurement["requested"] = [
            {"source_key": key, "check_digest": _verification_group_digest(checks)}
            for key, checks in provisional_groups.items()
        ]
    if runtime.evidence:
        evidence_state = state.get("evidence_state")
        dynamic_ids = [
            str(checks[0].get("evidence_id", ""))
            for source_key, checks in provisional_groups.items()
            if click_evidence_shards.active_set(evidence_state, source_key) is None
        ]
        sources, error = click_evidence.register_runtime_sources(
            state, dynamic_ids, kind="argv"
        )
        if error:
            return "", error, ""
        assert sources is not None
    workspace = _tool_working_directory(event, provisional)
    if not workspace.is_dir():
        return "", "Click verification workdir is not an existing directory.", ""
    provisional["workdir"] = str(workspace)
    expanded, sources, shard_advisories, error, source_labels = _expand_evidence_shards(
        state,
        provisional,
        scale=scale,
        workspace=workspace,
        git_capture=git_capture,
    )
    if error:
        return "", error, ""
    assert expanded is not None and sources is not None
    verification_advisories.extend(shard_advisories)
    encoded_expanded = json.dumps(expanded, separators=(",", ":"))
    batch, units, error = _validate_verification_batch(
        encoded_expanded, scale, sources
    )
    if error:
        return "", error, ""
    if (
        runtime.evidence
        and batch is not None
        and click_diagnostics.reporting_was_omitted(raw)
        and all(
            click_diagnostics.supports_actionable(check.get("argv"))
            for check in batch.get("checks", [])
        )
    ):
        # Evidence keeps the host's context small: supported Python runners
        # report a bounded failure summary by default. Raw output remains one
        # explicit `reporting.format` away, and Guarded keeps the raw default.
        batch["reporting"] = click_diagnostics.actionable_reporting(batch["reporting"])
    assert batch is not None
    status = str(verification.get("status", "ready"))
    if status == "running":
        claimed_at = verification.get("runner_claimed_at", 0)
        if not isinstance(claimed_at, int) or isinstance(claimed_at, bool):
            return "", "Click verification runner claim state is malformed.", ""
        if claimed_at > 0:
            return "", "The approved Click verification batch is already running.", ""
        started_at = int(verification.get("started_at", 0))
        if started_at and time.time() - started_at <= VERIFY_RUNNING_TTL_SECONDS:
            return "", "The approved Click verification batch is already running.", ""
        status = "failed"
        click_incremental.reject_batch(verification, reason="reservation-expired")
        verification["status"] = status
        verification["last_exit_code"] = 124
        for source_key in verification.get("running_evidence_keys", []):
            source = sources.get(source_key)
            if isinstance(source, dict) and source.get("status") == "running":
                source["status"] = "ready"
                source["last_exit_code"] = None
        verification["running_evidence_keys"] = []
        verification["running_environment_digests"] = {}
        verification["running_environment_binding"] = []
        verification["running_environment_binding_digest"] = ""
        verification["running_executable_digests"] = {}
        verification["running_executable_component_digests"] = {}
        verification["running_host_coverage"] = {}
        verification["running_host_coverage_digest"] = ""
        verification["running_input_policy_receipts"] = {}
        verification["running_input_policy_binding"] = ""
        verification["runner_claimed_at"] = 0

    argv_keys = _evidence_keys_for_kind(sources, "argv")
    grouped_checks, grouping_error = _verification_groups(batch)
    if grouping_error:
        return "", grouping_error, ""
    requested_keys = set(grouped_checks)
    if measurement is not None:
        measurement["command_plans"] = _verification_command_plans(batch)
    unassigned_keys = requested_keys - argv_keys
    if unassigned_keys:
        return (
            "",
            "A verification batch may contain only declared argv evidence; "
            f"{len(unassigned_keys)} source(s) were unassigned or non-argv.",
            "",
        )

    requested_policies: dict[str, dict[str, Any]] = {}
    for source_key, checks in grouped_checks.items():
        policy, policy_error = _verification_group_policy(checks)
        if policy_error or policy is None:
            return "", policy_error or "Verification reuse policy is malformed.", ""
        requested_policies[source_key] = policy

    group_digests: dict[str, str] = {}
    group_units: dict[str, int] = {}
    for source_key, checks in grouped_checks.items():
        group_digest = _verification_group_digest(checks)
        group_digests[source_key] = group_digest
        group_units[source_key] = _verification_group_units(checks)
        source = sources[source_key]
        reserved_digest = str(source.get("reserved_check_digest", ""))
        if reserved_digest and reserved_digest != group_digest:
            return (
                "",
                "An argv evidence source is already reserved to a different exact "
                "check set for this contract. Reuse that set or stage a new contract.",
                "",
            )
        locked_digest = str(source.get("locked_check_digest", ""))
        if locked_digest and locked_digest != group_digest:
            return (
                "",
                "A previously successful argv evidence source is locked to its exact "
                "check set. Re-run that set after the relevant mutation.",
                "",
            )
        last_digest = str(source.get("last_check_digest", ""))
        if last_digest and last_digest != group_digest:
            return (
                "",
                "An argv evidence source changed its check set without an intervening "
                "mutation. Fix the implementation or reuse the original check set.",
                "",
            )

    for source_key, requested_units in group_units.items():
        source = sources[source_key]
        # Retain the derived unit field for state-schema compatibility only.
        # Exact check-group identity comes from reserved_check_digest.
        source["reserved_units"] = requested_units
        if not str(source.get("reserved_check_digest", "")):
            source["reserved_check_digest"] = group_digests[source_key]

    successor_candidates: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    if runtime.evidence or runtime.guarded_approved:
        scope_digest = click_evidence.successor_scope_digest(
            str(click_state.contract_path(event).resolve())
        )
        for source_key in requested_keys:
            # A current-lifecycle execution (especially a failure) supersedes a
            # prior candidate. Never resurrect A over B's observed result.
            if sources[source_key].get("attempts", 0) or sources[source_key].get("verified_contract_digest"):
                continue
            candidate = click_evidence.successor_candidate(
                state,
                source_key,
                expected_contract_schema_version=CONTRACT_STATE_SCHEMA_VERSION,
                scope_digest=scope_digest,
            )
            if candidate is not None:
                successor_candidates[source_key] = candidate
                previous, _ = candidate
                sources[source_key]["automatic_observation_required"] = (
                    click_verification_reuse.automatic_observation_required(previous)
                )
                _merge_source_policy(
                    sources[source_key],
                    {
                        "reuse": previous.get("reuse_policy", "conditional"),
                        "inputs": previous.get("input_patterns", []),
                        "outputs_required": previous.get("outputs_required", False),
                    },
                )
                if (
                    sources[source_key].get("input_patterns")
                    == previous.get("input_patterns", [])
                ):
                    sources[source_key]["verified_input_digest"] = str(
                        previous.get("verified_input_digest", "")
                    )

    effective_policies: dict[str, dict[str, Any]] = {}
    try:
        for source_key, requested_policy in requested_policies.items():
            effective = _merge_source_policy(
                sources[source_key], requested_policy
            )
            effective_policies[source_key] = effective
            _set_group_policy(grouped_checks[source_key], effective)
    except ValueError:
        return "", "Click could not normalize the verification reuse policy.", ""

    previous_revisions: dict[str, int] = {}
    reason_codes: dict[str, str] = {}
    not_evaluable_keys: set[str] = set()
    for source_key in requested_keys:
        source = sources[source_key]
        previous_revision = source.get("verified_revision", -1)
        previous_revisions[source_key] = (
            previous_revision
            if isinstance(previous_revision, int)
            and not isinstance(previous_revision, bool)
            and previous_revision >= -1
            else -1
        )
        reason, not_evaluable = _default_incremental_reason(source)
        reason_codes[source_key] = reason
        if not_evaluable:
            not_evaluable_keys.add(source_key)

    if (
        click_observer_control.mode(verification) == "auto"
        and click_observer_control.batch_supports_capture(batch)
    ):
        click_observer_runtime.prepare_automatic(workspace, verification, grouped_checks)

    prepared_environment = _observer_environment(
        _verification_environment(cwd=workspace), verification
    )
    current_input_bindings: dict[str, dict[str, Any]] = {}
    current_runtime_bindings: dict[str, dict[str, Any]] = {}
    current_bindings = click_verification_bindings.collect_group_bindings(
        grouped_checks, requested_keys, cwd=workspace, environment=prepared_environment,
        input_bindings=current_input_bindings,
        runtime_bindings=current_runtime_bindings,
    )
    if current_bindings is None:
        return (
            "",
            "Click could not resolve and fingerprint every verification "
            "executable before planning the runner batch.",
            "",
        )
    current_environment_digests, current_executable_digests = current_bindings
    unsafe_runtime = {
        source_key: binding
        for source_key, binding in current_runtime_bindings.items()
        if binding.get("status") == "unsafe"
    }
    if unsafe_runtime:
        reasons = sorted({
            str(reason)
            for binding in unsafe_runtime.values()
            for reason in binding.get("reason_codes", [])
            if isinstance(reason, str) and reason
        })
        detail = ", ".join(reasons) or "runtime-download-unapproved"
        return (
            "",
            "Click blocked a verification launcher that could download an "
            f"unprovisioned runtime or package ({detail}).",
            "",
        )

    force_run_reasons: dict[str, str] = {}
    for source_key, policy in effective_policies.items():
        source = sources[source_key]
        binding = current_input_bindings.get(source_key, {})
        if policy["reuse"] == "always-run":
            force_run_reasons[source_key] = "always-run-policy"
        elif policy["reuse"] == "rerun":
            force_run_reasons[source_key] = "explicit-rerun-requested"
        elif policy["outputs_required"]:
            force_run_reasons[source_key] = "required-output-not-guaranteed"
        elif policy["inputs"] and binding.get("status") != "complete":
            force_run_reasons[source_key] = "explicit-input-unavailable"
            not_evaluable_keys.add(source_key)
        elif policy["inputs"] and not source.get("verified_input_digest"):
            force_run_reasons[source_key] = "explicit-input-receipt-missing"
        elif policy["inputs"] and not secrets.compare_digest(
            str(source.get("verified_input_digest", "")),
            str(binding.get("digest", "")),
        ):
            force_run_reasons[source_key] = "explicit-input-changed"
        elif current_runtime_bindings.get(source_key, {}).get("status") != "complete":
            force_run_reasons[source_key] = "runtime-identity-incomplete"
            not_evaluable_keys.add(source_key)
    reason_codes.update(force_run_reasons)

    current_requested = {
        source_key
        for source_key in requested_keys
        if _evidence_is_current(sources.get(source_key), revision)
    }
    dependency_candidates = {
        source_key
        for source_key in requested_keys
        if isinstance(sources.get(source_key), dict)
        and sources[source_key].get("status") == "stale"
        and isinstance(sources[source_key].get("verified_revision"), int)
        and not isinstance(sources[source_key].get("verified_revision"), bool)
        and 0 <= int(sources[source_key].get("verified_revision", -1)) < revision
        and sources[source_key].get("verified_dependency_provider")
        in click_dependency_cache.PROVIDER_NAMES
    }
    safe_change_candidates = {
        source_key
        for source_key in requested_keys
        if isinstance(sources.get(source_key), dict)
        and sources[source_key].get("status") == "stale"
        and isinstance(sources[source_key].get("verified_revision"), int)
        and not isinstance(sources[source_key].get("verified_revision"), bool)
        and 0 <= int(sources[source_key].get("verified_revision", -1)) < revision
        and click_change_policy.receipt_is_valid(
            sources[source_key].get("verified_safe_change_receipt")
        )
        # A complete observation is stronger evidence than a repository
        # declaration. Never let the declaration override an observed input.
        and not sources[source_key].get("verified_dependency_observation")
    }
    forced_keys = set(force_run_reasons)
    current_requested.difference_update(forced_keys)
    dependency_candidates.difference_update(forced_keys)
    safe_change_candidates.difference_update(forced_keys)
    for source_key in forced_keys:
        successor_candidates.pop(source_key, None)
    reused_keys: set[str] = set()
    dependency_reused_keys: set[str] = set()
    safe_change_reused_keys: set[str] = set()
    successor_origins: dict[str, dict[str, Any]] = {}
    successor_imported: dict[str, dict[str, Any]] = {}
    reuse_rollbacks: dict[str, dict[str, Any]] = {}
    if (
        current_requested
        or dependency_candidates
        or safe_change_candidates
        or successor_candidates
    ):
        snapshot = git_workspace_snapshot(workspace)
        if snapshot is None:
            for source_key in (
                current_requested
                | dependency_candidates
                | safe_change_candidates
                | set(successor_candidates)
            ):
                reason_codes[source_key] = "workspace-ambiguous"
                not_evaluable_keys.add(source_key)
            for source_key in current_requested:
                source = sources[source_key]
                source["status"] = "ready"
                source["verified_revision"] = -1
        else:
            contract_digest = str(state.get("contract_digest", ""))
            git_root = os.path.normcase(str(snapshot.get("root", "")))
            tree_digest = str(snapshot.get("digest", ""))
            for source_key, (previous, metadata) in successor_candidates.items():
                source = sources[source_key]
                binding_reason = _successor_binding_reason(
                    previous,
                    source,
                    group_digest=group_digests[source_key],
                    git_root=git_root,
                    environment_digest=current_environment_digests[source_key],
                    executable_digest=current_executable_digests[source_key],
                    host_coverage=host_coverage,
                )
                if binding_reason:
                    reason_codes[source_key] = binding_reason
                    if binding_reason in {
                        "workspace-ambiguous",
                        "successor-evidence-integrity-invalid",
                    }:
                        not_evaluable_keys.add(source_key)
                    continue
                exact_tree = previous.get("verified_tree_digest") == tree_digest
                successor_imported[source_key] = json.loads(json.dumps(source))
                _requalify_successor_baseline(
                    source,
                    previous,
                    contract_digest=contract_digest,
                    revision=revision,
                    group_digest=group_digests[source_key],
                    units=group_units[source_key],
                    tree_digest=tree_digest,
                    environment_digest=current_environment_digests[source_key],
                    executable_digest=current_executable_digests[source_key],
                    host_coverage=host_coverage,
                    exact_tree=exact_tree,
                )
                previous_revisions[source_key] = metadata["origin_revision"]
                successor_origins[source_key] = metadata
                if exact_tree:
                    current_requested.add(source_key)
                elif source.get("verified_dependency_provider") in (
                    click_dependency_cache.PROVIDER_NAMES
                ):
                    dependency_candidates.add(source_key)
                elif (
                    click_change_policy.receipt_is_valid(
                        source.get("verified_safe_change_receipt")
                    )
                    and not source.get("verified_dependency_observation")
                ):
                    safe_change_candidates.add(source_key)
                else:
                    reason, not_evaluable = _default_incremental_reason(source)
                    reason_codes[source_key] = reason
                    if not_evaluable:
                        not_evaluable_keys.add(source_key)
            mutation_boundary = verification.get("mutation_boundary")
            if (dependency_candidates or safe_change_candidates) and not (
                isinstance(mutation_boundary, dict)
                and mutation_boundary.get("status") == "recorded"
                and mutation_boundary.get("lineage_valid") is True
                and mutation_boundary.get("revision") == revision
                and mutation_boundary.get("after_root") == git_root
                and mutation_boundary.get("after_digest") == tree_digest
            ):
                # A missing PostToolUse receipt or any later workspace drift is
                # outside the observable approved mutation boundary. Rerun.
                for source_key in dependency_candidates | safe_change_candidates:
                    reason_codes[source_key] = "mutation-boundary-ambiguous"
                    not_evaluable_keys.add(source_key)
                dependency_candidates = set()
                safe_change_candidates = set()
            tree_changed = any(
                isinstance(sources[source_key].get("verified_root"), str)
                and bool(sources[source_key].get("verified_root"))
                and (
                    sources[source_key].get("verified_root") != git_root
                    or sources[source_key].get("verified_tree_digest") != tree_digest
                )
                for source_key in current_requested
            )
            if tree_changed:
                for source_key in (
                    current_requested | dependency_candidates | safe_change_candidates
                ):
                    reason_codes[source_key] = "workspace-ambiguous"
                    not_evaluable_keys.add(source_key)
                revision = _invalidate_workspace_evidence(
                    state, verification, sources, revision,
                )
            else:
                for source_key in current_requested:
                    source = sources[source_key]
                    environment_digest = current_environment_digests[source_key]
                    executable_digest = current_executable_digests[source_key]
                    if _verification_receipt_matches(
                        source,
                        contract_digest=contract_digest,
                        revision=revision,
                        group_digest=group_digests[source_key],
                        git_root=git_root,
                        tree_digest=tree_digest,
                        environment_digest=environment_digest,
                        executable_digest=executable_digest,
                        host_coverage=host_coverage,
                    ):
                        reused_keys.add(source_key)
                        if source_key in successor_origins:
                            _mark_successor_reuse(
                                source, successor_origins[source_key], mode="exact"
                            )
                            reason_codes[source_key] = "successor-evidence-current"
                        else:
                            reason_codes[source_key] = (
                                "same-revision-receipt-current"
                            )
                    else:
                        reason_codes[source_key] = _reuse_binding_reason(
                            source,
                            contract_digest=contract_digest,
                            group_digest=group_digests[source_key],
                            git_root=git_root,
                            tree_digest=tree_digest,
                            environment_digest=environment_digest,
                            executable_digest=executable_digest,
                            host_coverage=host_coverage,
                        )
                        source["status"] = "ready"
                        source["verified_revision"] = -1
                        source["last_exit_code"] = None
                candidate_checks = {
                    source_key: grouped_checks[source_key]
                    for source_key in dependency_candidates
                }
                authoritative_runtime = (
                    click_observer_runtime.state_from_verification(verification)
                    if click_observer_control.captures_inputs(verification)
                    else None
                )
                if (
                    authoritative_runtime is not None
                    and click_observer_runtime.validate(
                        workspace, authoritative_runtime
                    ) is None
                ):
                    authoritative_runtime = None
                policy_digests = click_dependency_cache.observation_policy_bindings(
                    workspace,
                    candidate_checks,
                    declarations=_dependency_declarations(
                        sources, dependency_candidates
                    ),
                    git_capture=git_capture,
                ) if candidate_checks else {}
                authoritative_bindings = _authoritative_current_bindings(
                    sources=sources,
                    source_keys=dependency_candidates,
                    group_digests=group_digests,
                    cwd=workspace,
                    workspace_root=Path(git_root),
                    environment_digests=current_environment_digests,
                    executable_digests=current_executable_digests,
                    host_coverage_digest=str(host_coverage.get("digest", "")),
                    policy_digests=policy_digests,
                ) if candidate_checks else {}
                dependency_receipts = (
                    click_dependency_cache.receipts_for_groups(
                        workspace,
                        candidate_checks,
                        declarations=_dependency_declarations(
                            sources, dependency_candidates
                        ),
                        observations=_dependency_observations(
                            sources, dependency_candidates
                        ),
                        authoritative_only=True,
                        authoritative_runtime=authoritative_runtime,
                        authoritative_bindings=authoritative_bindings,
                        git_capture=git_capture,
                    )
                    if candidate_checks
                    else {}
                )
                for source_key in dependency_candidates:
                    source = sources[source_key]
                    receipt = dependency_receipts.get(source_key)
                    environment_digest = current_environment_digests[source_key]
                    executable_digest = current_executable_digests[source_key]
                    if _dependency_receipt_matches(
                        source,
                        receipt,
                        contract_digest=contract_digest,
                        revision=revision,
                        group_digest=group_digests[source_key],
                        git_root=git_root,
                        environment_digest=environment_digest,
                        executable_digest=executable_digest,
                        host_coverage=host_coverage,
                    ):
                        assert isinstance(receipt, dict)
                        reuse_rollbacks[source_key] = json.loads(json.dumps(source))
                        _promote_dependency_receipt(
                            source,
                            receipt,
                            revision=revision,
                            tree_digest=tree_digest,
                        )
                        reused_keys.add(source_key)
                        dependency_reused_keys.add(source_key)
                        if source_key in successor_origins:
                            _mark_successor_reuse(
                                source,
                                successor_origins[source_key],
                                mode="dependency",
                            )
                        not_evaluable_keys.discard(source_key)
                        reason_codes[source_key] = (
                            "successor-evidence-dependencies-unchanged"
                            if source_key in successor_origins
                            else "observed-dependencies-unchanged"
                        )
                    else:
                        binding_reason = _reuse_binding_reason(
                            source,
                            contract_digest=contract_digest,
                            group_digest=group_digests[source_key],
                            git_root=git_root,
                            tree_digest=tree_digest,
                            environment_digest=environment_digest,
                            executable_digest=executable_digest,
                            host_coverage=host_coverage,
                            cross_revision=True,
                        )
                        if binding_reason != "receipt-invalid":
                            reason_codes[source_key] = binding_reason
                            not_evaluable_keys.discard(source_key)
                        else:
                            observation_reason = _observation_nonreuse_reason(
                                source.get("verified_dependency_observation")
                            )
                            reason_codes[source_key] = observation_reason
                            if observation_reason in {
                                "observer-incomplete",
                                "external-input-unmodeled",
                            }:
                                not_evaluable_keys.add(source_key)
                policy_keys = safe_change_candidates - reused_keys
                policy_contexts = {
                    key: {"source_key": key, "revision": revision,
                          "git_root": git_root, "tree_digest": tree_digest}
                    for key in policy_keys
                }
                policy_decisions = click_change_policy.decide_groups(
                    workspace, {key: grouped_checks[key] for key in policy_keys},
                    {key: sources[key].get("verified_safe_change_receipt") for key in policy_keys},
                    git_capture=git_capture, decision_contexts=policy_contexts,
                ) if policy_keys else {}
                for source_key in policy_keys:
                    source = sources[source_key]
                    decision_context = {
                        "source_key": source_key,
                        "revision": revision,
                        "git_root": git_root,
                        "tree_digest": tree_digest,
                    }
                    decision = policy_decisions[source_key]
                    changed_paths = decision.get("changed_paths", [])
                    evidence_id = str(
                        grouped_checks[source_key][0].get("evidence_id", "argv")
                    )
                    rendered_paths = ", ".join(
                        json.dumps(path, ensure_ascii=True)
                        for path in changed_paths[:8]
                        if isinstance(path, str)
                    )
                    if len(changed_paths) > 8:
                        rendered_paths += f", ... (+{len(changed_paths) - 8})"
                    status = str(decision.get("status", "unknown"))
                    reason = str(decision.get("reason", "preflight-unavailable"))
                    if rendered_paths:
                        detail = f" changed paths: {rendered_paths}."
                    elif status == "unknown":
                        detail = " changed paths unavailable."
                    else:
                        detail = " no net changed paths."
                    preflight_advisory = (
                        f"Click preflight [{evidence_id}]: {status} ({reason});{detail}"
                    )
                    environment_digest = current_environment_digests[source_key]
                    executable_digest = current_executable_digests[source_key]
                    safe_change_matches = _safe_change_receipt_matches(
                        source,
                        decision,
                        contract_digest=contract_digest,
                        revision=revision,
                        group_digest=group_digests[source_key],
                        git_root=git_root,
                        environment_digest=environment_digest,
                        executable_digest=executable_digest,
                        host_coverage=host_coverage,
                        decision_context=decision_context,
                    )
                    if safe_change_matches:
                        confirmed_snapshot = git_workspace_snapshot(workspace)
                        if not (
                            isinstance(confirmed_snapshot, dict)
                            and os.path.normcase(
                                str(confirmed_snapshot.get("root", ""))
                            )
                            == git_root
                            and confirmed_snapshot.get("digest") == tree_digest
                        ):
                            verification_advisories.append(
                                f"Click preflight [{evidence_id}]: workspace changed "
                                "during preflight; running the real check."
                            )
                            reason_codes[source_key] = "workspace-ambiguous"
                            not_evaluable_keys.add(source_key)
                            continue
                        verification_advisories.append(preflight_advisory)
                        reuse_rollbacks[source_key] = json.loads(json.dumps(source))
                        _promote_safe_change_receipt(
                            source,
                            decision,
                            revision=revision,
                            tree_digest=tree_digest,
                            decision_context=decision_context,
                        )
                        reused_keys.add(source_key)
                        safe_change_reused_keys.add(source_key)
                        if source_key in successor_origins:
                            _mark_successor_reuse(
                                source,
                                successor_origins[source_key],
                                mode="safe-change",
                            )
                        not_evaluable_keys.discard(source_key)
                        reason_codes[source_key] = (
                            "successor-evidence-safe-change-covered"
                            if source_key in successor_origins
                            else "safe-change-policy-covered"
                        )
                    else:
                        verification_advisories.append(preflight_advisory)
                        binding_reason = _reuse_binding_reason(
                            source,
                            contract_digest=contract_digest,
                            group_digest=group_digests[source_key],
                            git_root=git_root,
                            tree_digest=tree_digest,
                            environment_digest=environment_digest,
                            executable_digest=executable_digest,
                            host_coverage=host_coverage,
                            cross_revision=True,
                        )
                        if binding_reason != "receipt-invalid":
                            reason_codes[source_key] = binding_reason
                        elif status == "unknown":
                            reason_codes[source_key] = "policy-unavailable"
                            not_evaluable_keys.add(source_key)
                        else:
                            reason_codes[source_key] = (
                                "safe-change-policy-not-covered"
                            )

    if reused_keys:
        # A successful decision is provisional until this final common boundary.
        # Fresh collections never reuse executable hashes from the first stage.
        confirmed_snapshot = git_workspace_snapshot(workspace)
        workspace_drift = not (
            isinstance(confirmed_snapshot, dict)
            and os.path.normcase(str(confirmed_snapshot.get("root", ""))) == git_root
            and confirmed_snapshot.get("digest") == tree_digest
        )
        confirmed_environment = _observer_environment(
            _verification_environment(cwd=workspace), verification,
        )
        confirmed_bindings = click_verification_bindings.collect_group_bindings(
            grouped_checks, reused_keys, cwd=workspace,
            environment=confirmed_environment,
        )
        if confirmed_bindings is None:
            return "", "Click could not resolve verification executables before confirming reuse.", ""
        confirmed_environment_digests, confirmed_executable_digests = confirmed_bindings
        # Git equality does not imply equality of ignored files, missing-path
        # lookups or runtime inputs. Recheck observed inputs for exact reuse too,
        # including successor receipts, at the final common admission boundary.
        observed_keys = {
            key for key in reused_keys
            if click_dependency_cache.bound_dependency_observation_is_reusable(
                sources[key].get("verified_dependency_observation")
            )
        }
        observed_checks = {key: grouped_checks[key] for key in observed_keys}
        observed_runtime = click_observer_runtime.state_from_verification(verification)
        if observed_keys and click_observer_runtime.validate(workspace, observed_runtime) is None:
            observed_runtime = None
        observed_policy = click_dependency_cache.observation_policy_bindings(
            workspace, observed_checks,
            declarations=_dependency_declarations(sources, observed_keys),
            git_capture=git_capture,
        ) if observed_keys else {}
        observed_bindings = _authoritative_current_bindings(
            sources=sources, source_keys=observed_keys,
            group_digests=group_digests, cwd=workspace, workspace_root=Path(git_root),
            environment_digests=confirmed_environment_digests,
            executable_digests=confirmed_executable_digests,
            host_coverage_digest=str(host_coverage.get("digest", "")),
            policy_digests=observed_policy,
        ) if observed_keys else {}
        observed_receipts = click_dependency_cache.receipts_for_groups(
            workspace, observed_checks,
            declarations=_dependency_declarations(sources, observed_keys),
            observations=_dependency_observations(sources, observed_keys),
            authoritative_only=True, authoritative_runtime=observed_runtime,
            authoritative_bindings=observed_bindings, git_capture=git_capture,
        ) if observed_keys else {}
        observed_drift = {
            key for key in observed_keys
            if observed_receipts.get(key, {}).get("dependency_digest")
            != sources[key].get("verified_dependency_digest")
        }
        invalidated = {
            key for key in reused_keys
            if workspace_drift
            or key in observed_drift
            or confirmed_environment_digests[key] != current_environment_digests[key]
            or confirmed_executable_digests[key] != current_executable_digests[key]
            or (
                sources[key].get("verified_safe_change_receipt", {}).get("provider")
                == click_change_policy.INPUT_PROVIDER_NAME
                and not click_change_policy.inputs_are_current(
                    Path(git_root), sources[key]["verified_safe_change_receipt"]
                )
            )
        }
        for key in invalidated:
            original = successor_imported.get(key, reuse_rollbacks.get(key))
            if original is not None:
                sources[key].clear()
                sources[key].update(original)
            sources[key].update(status="ready", verified_revision=-1, last_exit_code=None)
            reason_codes[key] = (
                "workspace-ambiguous" if workspace_drift
                else "environment-binding-changed"
                if confirmed_environment_digests[key] != current_environment_digests[key]
                else "executable-binding-changed"
                if confirmed_executable_digests[key] != current_executable_digests[key]
                else "observed-input-changed"
            )
            if workspace_drift:
                not_evaluable_keys.add(key)
        reused_keys.difference_update(invalidated)
        dependency_reused_keys.difference_update(invalidated)
        safe_change_reused_keys.difference_update(invalidated)
        if workspace_drift:
            revision = _invalidate_workspace_evidence(state, verification, sources, revision)
        if invalidated:
            prepared_environment = confirmed_environment
            current_environment_digests.update(confirmed_environment_digests)
            current_executable_digests.update(confirmed_executable_digests)
            verification_advisories.append(
                "Click detected changed inputs while confirming reuse; running affected checks."
            )

    for source_key, original in successor_imported.items():
        if source_key not in reused_keys:
            sources[source_key].clear()
            sources[source_key].update(original)

    unresolved_keys = {
        key for key in argv_keys if not _evidence_is_current(sources.get(key), revision)
    }
    # A fresh/always/output-bound request intentionally supersedes an otherwise
    # current receipt. Treat it as unresolved for this one runner reservation.
    unresolved_keys.update(forced_keys)
    try:
        incremental_plan = _canonical_incremental_plan(
            sources,
            requested_keys=requested_keys,
            group_digests=group_digests,
            environment_digests=current_environment_digests,
            executable_digests=current_executable_digests,
            host_coverage_digest=str(host_coverage.get("digest", "")),
            observer_mode=click_observer_control.mode(verification),
            revision=revision,
            previous_revisions=previous_revisions,
            reused_keys=reused_keys,
            dependency_reused_keys=dependency_reused_keys,
            safe_change_reused_keys=safe_change_reused_keys,
            not_evaluable_keys=not_evaluable_keys,
            reason_codes=reason_codes,
        )
        pending_keys = click_incremental.keys_to_execute(incremental_plan)
    except ValueError:
        return "", "Click could not build a valid incremental verification plan.", ""
    repeated_keys = pending_keys - unresolved_keys
    if repeated_keys:
        return (
            "",
            "A verification batch may contain only unresolved declared argv evidence or "
            "an exact reusable success receipt.",
            "",
        )
    click_incremental.store_plan(verification, incremental_plan)
    verification["source_labels"] = {
        key: label for key, label in source_labels.items() if key in requested_keys
    }
    # Children expanded from one committed shard plan are independent by the
    # plan's own declaration, so the runner may execute them concurrently.
    verification["parallel_groups"] = {
        key: str(source["shard"]["parent_source_key"])
        for key, source in sources.items()
        if key in requested_keys
        and isinstance(source, dict)
        and click_evidence_shards.source_metadata_is_valid(source.get("shard"))
    }
    if measurement is not None:
        measurement["plan"] = incremental_plan
        measurement["labels"] = {
            **click_incremental.sanitize_presentation(state.get("presentation"))["evidence_labels"],
            **{
            key: str(source["shard"]["shard_id"])
            for key, source in sources.items()
            if isinstance(source, dict)
            and click_evidence_shards.source_metadata_is_valid(source.get("shard"))
            },
        }
        measurement["reuse_origins"] = {
            key: successor_origins[key]
            for key in reused_keys
            if key in successor_origins
        }

    if not pending_keys:
        if measurement is not None:
            measurement["all_reused"] = True
        all_argv_current = bool(argv_keys) and all(
            _evidence_is_current(sources.get(source_key), revision)
            for source_key in argv_keys
        )
        verification["status"] = "passed" if all_argv_current else "ready"
        verification["verified_revision"] = revision if all_argv_current else -1
        verification["failed_revision"] = -1
        verification["last_exit_code"] = 0
        verification["last_units"] = 0
        state["verification"] = verification
        _save_contract_state(event, state)
        exact_reused = len(
            reused_keys - dependency_reused_keys - safe_change_reused_keys
        )
        dependency_reused = len(dependency_reused_keys)
        safe_change_reused = len(safe_change_reused_keys)
        reuse_parts: list[str] = []
        if exact_reused:
            reuse_parts.append(f"{exact_reused} current unchanged-tree")
        if dependency_reused:
            reuse_parts.append(
                f"{dependency_reused} dependency-safe cross-revision"
            )
        if safe_change_reused:
            reuse_parts.append(
                f"{safe_change_reused} repository-declared safe-change cross-revision"
            )
        reuse_message = (
            f"Click reused {' and '.join(reuse_parts)} verification receipts"
        )
        conditional_count = sum(
            click_dependency_cache.conditional_observation_is_valid(
                sources[key].get("verified_dependency_observation")) for key in reused_keys
        )
        if conditional_count:
            reuse_message += (f" - 조건부 재사용 {conditional_count}개"
                              " - 관찰 범위 기반 / 입력 완전성 미보증")
        return (
            f"echo {reuse_message}",
            "",
            "\n".join(verification_advisories),
        )

    batch = {
        "version": VERIFICATION_PROTOCOL_VERSION,
        "workdir": str(batch["workdir"]),
        "reporting": batch["reporting"],
        "failure_collection": batch["failure_collection"],
        "checks": [
            check
            for check in batch["checks"]
            if _evidence_key(str(check["evidence_id"])) in pending_keys
        ],
    }
    units = sum(group_units[source_key] for source_key in pending_keys)
    requested_keys = pending_keys

    if prior_verification_changed_workspace:
        return (
            "",
            "The previous verification changed protected repository content. Use an "
            "approved code mutation to repair or reconcile the workspace before running "
            "verification again.",
            "",
        )

    retried_failed_sources = 0
    for source_key in requested_keys:
        source = sources[source_key]
        source_status = str(source.get("status", "ready"))
        if source_status == "failed":
            retries = int(source.get("unchanged_failure_retries", 0))
            if retries >= 1:
                retried_failed_sources += 1
            source["unchanged_failure_retries"] = retries + 1

    if retried_failed_sources:
        verification_advisories.append(
            "Click advisory: "
            f"{retried_failed_sources} argv evidence source(s) already failed twice "
            "without a subsequent code mutation. This fresh, separately authorized "
            "retry is allowed, but fix the in-scope cause before repeating it when "
            "practical."
        )

    canonical = json.dumps(batch, sort_keys=True, separators=(",", ":"))
    batch_digest = hashlib.sha256(canonical.encode()).hexdigest()
    runner_token = secrets.token_urlsafe(24)
    running_host_coverage_digest = (
        _verification_host_coverage_binding_digest(host_coverage, runner_token)
    )
    running_environment_binding = _verification_environment_binding(
        prepared_environment, runner_token
    )
    running_environment_binding_digest = (
        _verification_environment_binding_digest(
            running_environment_binding, runner_token
        )
    )
    running_executable_component_digests: dict[str, dict[str, str]] = {}
    running_bindings = click_verification_bindings.collect_group_bindings(
        grouped_checks, requested_keys, cwd=workspace, environment=prepared_environment,
        executable_component_bindings=running_executable_component_digests,
    )
    if running_bindings is None:
        return (
            "",
            "Click could not resolve and fingerprint every verification "
            "executable before issuing the runner.",
            "",
        )
    running_environment_digests, running_executable_digests = running_bindings
    shadow_contexts = {
        source_key: {
            "check_digest": group_digests[source_key],
            "environment_digest": running_environment_digests[source_key],
            "executable_digest": running_executable_digests[source_key],
            "host_coverage_digest": str(host_coverage.get("digest", "")),
        }
        for source_key in requested_keys
    }
    shadow_workspace = workspace
    try:
        # Prediction needs only the root, never another full authority snapshot.
        root_output = git_capture(workspace, ["rev-parse", "--show-toplevel"])
        shadow_root = os.fsdecode(root_output.strip()) if root_output else None
        if isinstance(shadow_root, str) and shadow_root:
            shadow_workspace = Path(shadow_root).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        # A Shadow-only root lookup cannot change verification admission.
        shadow_workspace = workspace
    try:
        click_shadow_intelligence.prepare_predictions(
            verification,
            workspace=shadow_workspace,
            source_contexts=shadow_contexts,
            mutation_revision=revision,
        )
        intelligence = verification.get(
            click_shadow_intelligence.SHADOW_INTELLIGENCE_FIELD, {}
        )
        intelligence_sources = (
            intelligence.get("sources", {})
            if isinstance(intelligence, dict)
            else {}
        )
        for source_key in sorted(requested_keys):
            entry = intelligence_sources.get(source_key)
            prediction = entry.get("prediction") if isinstance(entry, dict) else None
            if (
                click_shadow_intelligence.prediction_is_valid(prediction)
                and prediction["decision"] != "not-evaluable"
            ):
                verification_advisories.append(
                    click_shadow_intelligence.advisory(prediction)
                )
    except Exception:
        # Shadow prediction must never affect verification admission.
        pass
    for source_key in requested_keys:
        group_digest = group_digests[source_key]
        source = sources[source_key]
        source["status"] = "running"
        source["last_check_digest"] = group_digest

    input_policy_receipts = {
        key: receipt for key, receipt in click_change_policy.receipts_for_groups(
            workspace, {key: grouped_checks[key] for key in requested_keys},
            git_capture=git_capture, scoped_only=True,
        ).items() if receipt.get("provider") == click_change_policy.INPUT_PROVIDER_NAME
    }
    verification.update(
        {
            "status": "running",
            "attempts": int(verification.get("attempts", 0)) + 1,
            "last_units": units,
            "last_batch_digest": batch_digest,
            "runner_token_digest": hashlib.sha256(runner_token.encode()).hexdigest(),
            "runner_claimed_at": 0,
            "running_evidence_keys": sorted(requested_keys),
            "running_environment_digests": running_environment_digests,
            "running_environment_binding": running_environment_binding,
            "running_environment_binding_digest": (
                running_environment_binding_digest
            ),
            "running_executable_digests": running_executable_digests,
            "running_executable_component_digests": (
                running_executable_component_digests
            ),
            "running_host_coverage": host_coverage,
            "running_host_coverage_digest": running_host_coverage_digest,
            "running_input_policy_receipts": input_policy_receipts,
            "running_input_policy_binding": hmac.new(
                runner_token.encode(), json.dumps(input_policy_receipts, sort_keys=True).encode(),
                hashlib.sha256,
            ).hexdigest(),
            "started_at": int(time.time()),
        }
    )
    state["verification"] = verification
    _save_contract_state(event, state)
    return (
        runner_command(
            event,
            batch,
            batch_digest,
            runner_token,
            runner_script=runner_script,
            render_command=render_command,
        ),
        "",
        "\n".join(verification_advisories),
    )
