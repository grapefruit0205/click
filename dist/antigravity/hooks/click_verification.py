#!/usr/bin/env python3
"""Verification admission, fingerprints, receipts, and runner lifecycle for Click.

This layer binds approved argv evidence to an exact check group, workspace,
environment, executable, host coverage identity, and one-use runner result. It
may depend on lower runtime domains but never imports the gate or host router.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import time
from typing import Any

if __package__:
    from . import click_import_bootstrap
else:  # Installed launchers execute hooks directly.
    import click_import_bootstrap

(
    click_capability,
    click_change_policy,
    click_claims,
    click_contract_state,
    click_dependency_cache,
    click_diagnostics,
    click_evidence,
    click_evidence_shards,
    click_host_coverage,
    click_incremental,
    click_inspection,
    click_mutation,
    click_observation,
    click_observer_control,
    click_observer_runtime,
    click_process,
    click_runtime_state,
    click_shadow_intelligence,
    click_state,
    click_verification_policy,
    click_verification_bindings,
    click_verification_inputs,
    click_verification_plan,
    click_verification_reuse,
    click_observer_common,
) = click_import_bootstrap.load_siblings(
    __package__,
    "click_capability",
    "click_change_policy",
    "click_claims",
    "click_contract_state",
    "click_dependency_cache",
    "click_diagnostics",
    "click_evidence",
    "click_evidence_shards",
    "click_host_coverage",
    "click_incremental",
    "click_inspection",
    "click_mutation",
    "click_observation",
    "click_observer_control",
    "click_observer_runtime",
    "click_process",
    "click_runtime_state",
    "click_shadow_intelligence",
    "click_state",
    "click_verification_policy",
    "click_verification_bindings",
    "click_verification_inputs",
    "click_verification_plan",
    "click_verification_reuse",
    "click_observer_common",
)


CONTRACT_STATE_SCHEMA_VERSION = 2
RUNNING_TTL_SECONDS = 60 * 60
VERIFY_RUNNING_TTL_SECONDS = RUNNING_TTL_SECONDS
PROTOCOL_VERSION = click_verification_plan.PROTOCOL_VERSION
BATCH_FIELDS = click_verification_plan.BATCH_FIELDS
CHECK_FIELDS = click_verification_plan.CHECK_FIELDS
VERIFICATION_CLASSES = click_verification_plan.VERIFICATION_CLASSES
PYTHON_VERIFICATION_MODULES = click_verification_plan.PYTHON_VERIFICATION_MODULES
PYTHON_VERIFICATION_EXECUTABLES = click_verification_plan.PYTHON_VERIFICATION_EXECUTABLES
VERSIONED_PYTHON_EXECUTABLE = click_verification_plan.VERSIONED_PYTHON_EXECUTABLE
DEEP_VERIFICATION_EXECUTABLES = click_verification_plan.DEEP_VERIFICATION_EXECUTABLES
DEEP_VERIFICATION_MARKERS = click_verification_plan.DEEP_VERIFICATION_MARKERS
VERIFICATION_EXECUTABLES = click_verification_plan.VERIFICATION_EXECUTABLES
VERIFICATION_NAME_MARKERS = click_verification_plan.VERIFICATION_NAME_MARKERS
TEST_TARGET_SUFFIXES = click_verification_plan.TEST_TARGET_SUFFIXES
TEST_FILTER_OPTIONS = click_verification_plan.TEST_FILTER_OPTIONS
TEST_OPTIONS_WITH_VALUES = click_verification_plan.TEST_OPTIONS_WITH_VALUES
NEW_SOURCE_PATH_SEGMENTS = click_verification_plan.NEW_SOURCE_PATH_SEGMENTS
VERIFICATION_BATCH_FIELDS = click_verification_plan.VERIFICATION_BATCH_FIELDS
VERIFICATION_CHECK_FIELDS = click_verification_plan.VERIFICATION_CHECK_FIELDS
VERIFICATION_PROTOCOL_VERSION = click_verification_plan.VERIFICATION_PROTOCOL_VERSION
EVIDENCE_ID_PATTERN = click_verification_plan.EVIDENCE_ID_PATTERN
FAILURE_COLLECTION_VERSION = click_verification_plan.FAILURE_COLLECTION_VERSION
FAILURE_COLLECTION_MAX_EXTRA_SOURCES = click_verification_plan.FAILURE_COLLECTION_MAX_EXTRA_SOURCES
FAILURE_COLLECTION_MAX_EXTRA_FAILURES = click_verification_plan.FAILURE_COLLECTION_MAX_EXTRA_FAILURES
FAILURE_COLLECTION_MAX_START_WINDOW_MS = click_verification_plan.FAILURE_COLLECTION_MAX_START_WINDOW_MS
FAILURE_COLLECTION_STATE_FIELD = click_verification_plan.FAILURE_COLLECTION_STATE_FIELD
FAILURE_COLLECTION_RESULT_STATUSES = click_verification_plan.FAILURE_COLLECTION_RESULT_STATUSES


_fresh_mutation_boundary = click_mutation.fresh_boundary
_decode_capability_request = click_capability.decode_request
_validate_argv = click_capability.validate_argv
_capability_digest = click_capability.digest
_command_parts = click_capability.command_parts
_shell_segments = click_capability.shell_segments
_evidence_key = click_evidence.evidence_key
_evidence_is_current = click_evidence.is_current
_evidence_keys_for_kind = click_evidence.keys_for_kind
_is_read_only_tokens = click_inspection.is_read_only_tokens
_is_broad_exploration_tokens = click_inspection.is_broad_exploration_tokens
_is_path_qualified_executable = click_inspection.is_path_qualified_executable
_resolve_read_only_executable = click_inspection.resolve_read_only_executable
_sanitized_git_environment = click_inspection.sanitized_git_environment


_default_failure_collection = click_verification_plan.default_failure_collection
_validate_failure_collection = click_verification_plan.validate_failure_collection
_failure_collection_result_is_valid = click_verification_plan.failure_collection_result_is_valid


def _fresh_verification_state(contract: dict[str, Any]) -> dict[str, Any]:
    scale = str(contract["verification"]["scale"])
    legacy_unit_limit = click_verification_policy.approved_unit_limit(scale)
    assert legacy_unit_limit is not None
    return {
        "scale": scale,
        # Compatibility state field only. Runtime authority and advice do not
        # depend on this legacy plugin-authored number.
        "unit_limit": legacy_unit_limit,
        "status": "ready",
        "mutation_revision": 0,
        "verified_revision": -1,
        "failed_revision": -1,
        "attempts": 0,
        "unchanged_failure_retries": 0,
        "last_units": 0,
        "last_exit_code": None,
        "last_batch_digest": "",
        "locked_batch_digest": "",
        "runner_token_digest": "",
        "runner_claimed_at": 0,
        "running_evidence_keys": [],
        "running_environment_digests": {},
        "running_environment_binding": [],
        "running_environment_binding_digest": "",
        "running_executable_digests": {},
        "running_executable_component_digests": {},
        "running_host_coverage": {},
        "running_host_coverage_digest": "",
        "workspace_changed": False,
        "mutation_boundary": _fresh_mutation_boundary(),
        "started_at": 0,
        click_observer_control.CONTROL_FIELD: (
            click_observer_control.fresh_state()
        ),
        click_observer_common.SHADOW_STATE_FIELD: (
            click_observer_common.fresh_state()
        ),
        click_shadow_intelligence.SHADOW_INTELLIGENCE_FIELD: (
            click_shadow_intelligence.fresh_state()
        ),
    }


_validate_verification_batch = click_verification_plan.validate_verification_batch
_verification_groups = click_verification_plan.verification_groups
_verification_group_policy = click_verification_plan.verification_group_policy
_verification_group_digest = click_verification_plan.verification_group_digest
_verification_command_digest = click_verification_plan.verification_command_digest
_verification_command_plans = click_verification_plan.verification_command_plans
_verification_group_units = click_verification_plan.verification_group_units


_file_content_digest = click_verification_bindings.hash_file_content
_verification_environment = click_verification_bindings.verification_environment
_observer_environment = click_verification_bindings.observer_environment
_verification_environment_key = click_verification_bindings.verification_environment_key
_verification_environment_hmac = click_verification_bindings.verification_environment_hmac
_verification_environment_binding = click_verification_bindings.verification_environment_binding
_verification_environment_binding_digest = click_verification_bindings.verification_environment_binding_digest
_verification_environment_binding_is_authentic = click_verification_bindings.verification_environment_binding_is_authentic
_verification_host_coverage_binding_digest = click_verification_bindings.verification_host_coverage_binding_digest
_verification_host_coverage_binding_is_authentic = click_verification_bindings.verification_host_coverage_binding_is_authentic
_verification_environment_from_binding = click_verification_bindings.verification_environment_from_binding
_executable_search_path = click_verification_bindings.executable_search_path
_verification_executable_records = click_verification_bindings.verification_executable_records
_verification_executable_payload = click_verification_bindings.verification_executable_payload
_verification_environment_digest_from_records = click_verification_bindings.verification_environment_digest_from_records
_verification_environment_digest = click_verification_bindings.verification_environment_digest


_verification_receipt_matches = click_verification_reuse.verification_receipt_matches
_dependency_declarations = click_verification_reuse.dependency_declarations
_dependency_observations = click_verification_reuse.dependency_observations
_binding_path_digest = click_verification_reuse.binding_path_digest
_authoritative_shard_digest = click_verification_reuse.authoritative_shard_digest
_authoritative_current_bindings = click_verification_reuse.authoritative_current_bindings
_dependency_receipt_is_valid = click_verification_reuse.dependency_receipt_is_valid
_dependency_receipt_matches = click_verification_reuse.dependency_receipt_matches
_clear_dependency_receipt = click_verification_reuse.clear_dependency_receipt
_store_dependency_receipt = click_verification_reuse.store_dependency_receipt
_promote_dependency_receipt = click_verification_reuse.promote_dependency_receipt
_clear_safe_change_receipt = click_verification_reuse.clear_safe_change_receipt
_store_safe_change_receipt = click_verification_reuse.store_safe_change_receipt
_safe_change_receipt_matches = click_verification_reuse.safe_change_receipt_matches
_promote_safe_change_receipt = click_verification_reuse.promote_safe_change_receipt
_reuse_binding_reason = click_verification_reuse.reuse_binding_reason
_successor_binding_reason = click_verification_reuse.successor_binding_reason
_requalify_successor_baseline = click_verification_reuse.requalify_successor_baseline
_mark_successor_reuse = click_verification_reuse.mark_successor_reuse
_observation_nonreuse_reason = click_verification_reuse.observation_nonreuse_reason
_default_incremental_reason = click_verification_reuse.default_incremental_reason
_canonical_incremental_plan = click_verification_reuse.canonical_incremental_plan


_contains_deep_verification_marker = click_verification_plan.contains_deep_verification_marker
_arguments_have_filter = click_verification_plan.arguments_have_filter
_verification_targets = click_verification_plan.verification_targets
_scope_with_kind_floor = click_verification_plan.scope_with_kind_floor
_minimum_test_runner_class = click_verification_plan.minimum_test_runner_class
_minimum_verification_class = click_verification_plan.minimum_verification_class
_is_recognized_verification_tokens = click_verification_plan.is_recognized_verification_tokens
_is_recognized_verification_command = click_verification_plan.is_recognized_verification_command


_git_capture = click_verification_bindings.git_capture
_hash_workspace_path = click_verification_bindings.hash_workspace_path
_git_workspace_snapshot = click_verification_bindings.git_workspace_snapshot


def _new_untracked_is_suspicious(relative: str) -> bool:
    parts = [part.lower() for part in Path(relative).parts if part not in {"", "."}]
    if not parts:
        return False
    if parts[0] in NEW_SOURCE_PATH_SEGMENTS:
        return True
    if any(
        part in {"config", "configs", "migration", "migrations", "src"}
        for part in parts
    ):
        return True
    for index, part in enumerate(parts):
        if part not in {"app", "lib"}:
            continue
        if index >= 2 and parts[index - 2] in {
            "apps",
            "modules",
            "packages",
            "services",
        }:
            return True
    if any(
        parts[index : index + 2] == ["db", "migrate"]
        for index in range(max(0, len(parts) - 1))
    ):
        return True
    if len(parts) == 1:
        name = parts[0]
        suffix = Path(name).suffix.lower()
        if suffix in {
            ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js",
            ".jsx", ".php", ".py", ".rb", ".rs", ".ts", ".tsx",
        }:
            return True
        if name in {
            "cargo.toml", "compose.yaml", "compose.yml", "docker-compose.yaml",
            "docker-compose.yml", "dockerfile", "go.mod", "package-lock.json",
            "package.json", "pnpm-lock.yaml", "pyproject.toml", "requirements.txt",
            "yarn.lock",
        }:
            return True
    return False




fresh_state = _fresh_verification_state
validate_batch = _validate_verification_batch
verification_groups = _verification_groups
group_digest = _verification_group_digest
group_units = _verification_group_units
file_content_digest = _file_content_digest
environment = _verification_environment
environment_key = _verification_environment_key
environment_binding = _verification_environment_binding
environment_binding_digest = _verification_environment_binding_digest
environment_binding_is_authentic = _verification_environment_binding_is_authentic
host_coverage_binding_digest = _verification_host_coverage_binding_digest
host_coverage_binding_is_authentic = _verification_host_coverage_binding_is_authentic
environment_from_binding = _verification_environment_from_binding
executable_records = _verification_executable_records
executable_payload = _verification_executable_payload
environment_digest_from_records = _verification_environment_digest_from_records
environment_digest = _verification_environment_digest
receipt_matches = _verification_receipt_matches
dependency_declarations = _dependency_declarations
dependency_observations = _dependency_observations
dependency_receipt_is_valid = _dependency_receipt_is_valid
dependency_receipt_matches = _dependency_receipt_matches
clear_dependency_receipt = _clear_dependency_receipt
store_dependency_receipt = _store_dependency_receipt
promote_dependency_receipt = _promote_dependency_receipt
clear_safe_change_receipt = _clear_safe_change_receipt
store_safe_change_receipt = _store_safe_change_receipt
safe_change_receipt_matches = _safe_change_receipt_matches
promote_safe_change_receipt = _promote_safe_change_receipt
minimum_class = _minimum_verification_class
is_recognized_tokens = _is_recognized_verification_tokens
is_recognized_command = _is_recognized_verification_command
git_capture = _git_capture
hash_workspace_path = _hash_workspace_path
git_workspace_snapshot = _git_workspace_snapshot
new_untracked_is_suspicious = _new_untracked_is_suspicious



_read_contract_state = click_contract_state.read_contract_state
_save_contract_state = click_contract_state.save_contract_state


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


def _evidence_sources(state: dict[str, Any]) -> dict[str, Any] | None:
    return click_evidence.sources_from_state(
        state,
        expected_contract_schema_version=CONTRACT_STATE_SCHEMA_VERSION,
    )


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
                    return None, None, advisories, message
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
        plan_current = bool(
            decision.get("status") == "sharded"
            and (
                active is None
                or click_evidence_shards.plan_matches_shard_set(decision, active)
            )
        )
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
                return None, None, advisories, collapse_error
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
                return None, None, advisories, activation_error
            evidence_state = state["evidence_state"]
        if plan_current:
            assert shard_checks is not None
            expanded.extend(shard_checks)
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


_fresh_external_evidence_state = click_evidence.fresh_external_state
_fresh_mutation_state = click_mutation.fresh_state
_mutation_is_running = click_mutation.is_running
_fresh_observation_state = click_observation.fresh_state
_observation_is_running = click_observation.is_running
_unclaimed_reservation_is_fresh = click_observation.unclaimed_reservation_is_fresh
_write_json = click_state.write_json
_state_lock = click_state.state_lock
_decode_encoded_request = click_capability.decode_encoded_request
_execute_argv_commands = click_inspection.execute_argv_commands
_git_metadata_present = click_inspection.git_metadata_present


def _managed_contract_path(path: Path) -> bool:
    return click_state.managed_state_path(path, ("session-contract-",))


def _tool_working_directory(
    event: dict[str, Any], batch: dict[str, Any] | None = None
) -> Path:
    event_cwd = Path(str(event.get("cwd", "")))
    requested = batch.get("workdir") if isinstance(batch, dict) else None
    tool_input = event.get("tool_input")
    if not isinstance(requested, str) or not requested:
        requested = tool_input.get("workdir") if isinstance(tool_input, dict) else None
    if not isinstance(requested, str) or not requested:
        return event_cwd.resolve()
    workdir = Path(requested)
    if not workdir.is_absolute():
        workdir = event_cwd / workdir
    return workdir.resolve()


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
                result = (result[0], result[1], "\n".join(filter(None, (result[2], click_incremental.host_summary(verification)))))
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
    expanded, sources, shard_advisories, error = _expand_evidence_shards(
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
                    if click_observer_control.mode(verification) == "authoritative"
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
                for source_key in safe_change_candidates - reused_keys:
                    source = sources[source_key]
                    decision_context = {
                        "source_key": source_key,
                        "revision": revision,
                        "git_root": git_root,
                        "tree_digest": tree_digest,
                    }
                    decision = click_change_policy.decide(
                        workspace,
                        grouped_checks[source_key],
                        source.get("verified_safe_change_receipt"),
                        git_capture=git_capture,
                        decision_context=decision_context,
                    )
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
        invalidated = {
            key for key in reused_keys
            if workspace_drift
            or confirmed_environment_digests[key] != current_environment_digests[key]
            or confirmed_executable_digests[key] != current_executable_digests[key]
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


@dataclass(frozen=True)
class VerificationRunResult:
    """One runner's observed result; bindings are still checked when recorded."""

    exit_code: int
    succeeded_count: int
    workspace_changed: bool = False
    workspace_root: str = ""
    workspace_digest: str = ""
    environment_digests: dict[str, str] | None = None
    explicit_input_digests: dict[str, str] | None = None
    source_durations_ms: dict[str, int | float] | None = None
    dependency_observations: dict[str, dict[str, Any]] | None = None
    authoritative_observations: dict[str, dict[str, Any]] | None = None
    shadow_observer_records: dict[str, dict[str, Any]] | None = None
    shadow_intelligence_baselines: dict[str, dict[str, Any]] | None = None
    shadow_source_exit_codes: dict[str, int] | None = None
    shadow_execution_contexts: dict[str, dict[str, Any]] | None = None
    observer_mode: str | None = None
    diagnostic_records: list[dict[str, Any]] | None = None
    reporting: dict[str, Any] | None = None
    collection_result: dict[str, Any] | None = None
    source_results: dict[str, dict[str, Any]] | None = None
    runner_started_ns: int | None = None


def record_outcome(
    path: Path,
    batch: dict[str, Any],
    batch_digest: str,
    runner_token: str,
    result: VerificationRunResult,
    *,
    git_capture: Callable[[Path, list[str]], bytes | None] = _git_capture,
) -> bool:
    """Typed handoff that retains the established receipt validation boundary."""
    # Keep large diagnostic and observation mappings by reference. dataclasses'
    # asdict would recursively copy them on every result handoff.
    return _record_verification_result(
        path, batch, batch_digest, runner_token,
        **{field.name: getattr(result, field.name) for field in fields(result)},
        git_capture=git_capture,
    )


def _verification_claim_binding(state: Any) -> dict[str, Any] | None:
    """Capture only the live authority facts needed by a claimed invocation."""
    if not isinstance(state, dict) or not click_runtime_state.view(state).execution_authorized:
        return None
    verification = state.get("verification")
    contract_digest = state.get("contract_digest")
    if (
        not isinstance(verification, dict)
        or not click_evidence.revision_is_valid(verification.get("mutation_revision"))
        or not isinstance(contract_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", contract_digest) is None
    ):
        return None
    identity_fields = (
        ("contract_id", "staged_turn_id", "approved_turn_id")
        if state["status"] == "approved" else ("evidence_session_id",)
    )
    return {
        "status": state["status"],
        "contract_digest": contract_digest,
        "mutation_revision": verification["mutation_revision"],
        **{field: state.get(field) for field in identity_fields},
    }


def _record_verification_result(
    path: Path,
    batch: dict[str, Any],
    batch_digest: str,
    runner_token: str,
    exit_code: int,
    succeeded_count: int,
    workspace_changed: bool = False,
    workspace_root: str = "",
    workspace_digest: str = "",
    environment_digests: dict[str, str] | None = None,
    explicit_input_digests: dict[str, str] | None = None,
    source_durations_ms: dict[str, int | float] | None = None,
    dependency_observations: dict[str, dict[str, Any]] | None = None,
    authoritative_observations: dict[str, dict[str, Any]] | None = None,
    shadow_observer_records: dict[str, dict[str, Any]] | None = None,
    shadow_intelligence_baselines: dict[str, dict[str, Any]] | None = None,
    shadow_source_exit_codes: dict[str, int] | None = None,
    shadow_execution_contexts: dict[str, dict[str, Any]] | None = None,
    observer_mode: str | None = None,
    diagnostic_records: list[dict[str, Any]] | None = None,
    reporting: dict[str, Any] | None = None,
    collection_result: dict[str, Any] | None = None,
    *,
    source_results: dict[str, dict[str, Any]] | None = None,
    runner_started_ns: int | None = None,
    git_capture: Callable[[Path, list[str]], bytes | None] = _git_capture,
) -> bool:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    claim_binding = _verification_claim_binding(state)
    if claim_binding is None or batch.get("_click_claim_binding") != claim_binding:
        return False
    verification = state.get("verification")
    if not isinstance(verification, dict):
        return False
    if verification.get("status") != "running":
        return False
    if verification.get("last_batch_digest") != batch_digest:
        return False
    token_digest = hashlib.sha256(runner_token.encode()).hexdigest()
    if not secrets.compare_digest(
        str(verification.get("runner_token_digest", "")), token_digest
    ):
        return False
    claimed_at = verification.get("runner_claimed_at", 0)
    if (
        not isinstance(claimed_at, int)
        or isinstance(claimed_at, bool)
        or claimed_at <= 0
    ):
        return False
    running_host_coverage = verification.get("running_host_coverage")
    if not _verification_host_coverage_binding_is_authentic(
        running_host_coverage,
        verification.get("running_host_coverage_digest"),
        runner_token,
    ):
        return False

    revision = claim_binding["mutation_revision"]
    claimed_revision = revision
    verification["runner_token_digest"] = ""
    verification["runner_claimed_at"] = 0
    verification["started_at"] = 0
    verification["last_exit_code"] = exit_code
    verification["workspace_changed"] = workspace_changed
    sources = _evidence_sources(state)
    if sources is None or not sources:
        return False
    running_keys = {
        key
        for key in verification.get("running_evidence_keys", [])
        if isinstance(key, str)
    }
    measured_durations = source_durations_ms or {}
    if (
        not isinstance(measured_durations, dict)
        or any(
            not isinstance(source_key, str)
            or source_key not in running_keys
            or not click_incremental.is_duration(duration)
            for source_key, duration in measured_durations.items()
        )
    ):
        return False
    selected_observer_mode = (
        click_observer_control.mode(verification)
        if observer_mode is None
        else observer_mode
    )
    measured_batch = click_incremental.current_batch(verification)
    measured_task = (
        measured_batch.get("task")
        if isinstance(measured_batch, dict)
        else None
    )
    origin_task = (
        {"mode": measured_task["mode"], "id": measured_task["id"]}
        if click_incremental.batch_task_is_valid(measured_task)
        else {}
    )
    origin_batch_id = (
        str(measured_batch.get("batch_id", ""))
        if isinstance(measured_batch, dict)
        else ""
    )
    prepared_environment_digests = verification.get(
        "running_environment_digests"
    )
    prepared_executable_digests = verification.get(
        "running_executable_digests"
    )
    for prepared in (
        prepared_environment_digests,
        prepared_executable_digests,
    ):
        if (
            not isinstance(prepared, dict)
            or set(prepared) != running_keys
            or any(
                not isinstance(value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", value)
                for value in prepared.values()
            )
        ):
            return False
    running_environment_binding = verification.get("running_environment_binding")
    if not _verification_environment_binding_is_authentic(
        running_environment_binding,
        verification.get("running_environment_binding_digest"),
        runner_token,
    ):
        return False
    _, _, binding_error = _verification_environment_from_binding(
        running_environment_binding,
        runner_token,
        _verification_environment(cwd=Path.cwd()),
    )
    if binding_error:
        return False
    if (
        environment_digests is not None
        and environment_digests != prepared_environment_digests
    ):
        return False
    environment_digests = prepared_environment_digests
    checks = batch.get("checks")
    if not isinstance(checks, list):
        return False
    positions: dict[str, list[int]] = {}
    for index, check in enumerate(checks):
        if not isinstance(check, dict) or not isinstance(check.get("evidence_id"), str):
            return False
        source_key = _evidence_key(str(check["evidence_id"]))
        positions.setdefault(source_key, []).append(index)
    if set(positions) != running_keys:
        return False
    grouped_checks, grouping_error = _verification_groups(batch)
    if grouping_error or set(grouped_checks) != running_keys:
        return False
    observed_input_digests = (
        {source_key: "" for source_key in running_keys}
        if explicit_input_digests is None
        else explicit_input_digests
    )
    if (
        not isinstance(observed_input_digests, dict)
        or set(observed_input_digests) != running_keys
        or any(
            not isinstance(value, str)
            or value and re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in observed_input_digests.values()
        )
    ):
        return False
    command_plans_by_source = _verification_command_plans(batch)
    precise_required = click_incremental.current_command_plans(
        verification, running_keys
    ) is not None
    precise_outcomes: dict[str, dict[str, Any]] = {}
    for source_key in running_keys:
        source_result = (
            source_results.get(source_key)
            if isinstance(source_results, dict) else None
        )
        outcome = click_incremental.source_command_outcome(
            source_result, command_plans_by_source[source_key]
        )
        if outcome is not None:
            precise_outcomes[source_key] = outcome
        elif precise_required:
            # A v5 batch cannot fall back to the legacy contiguous-success
            # counter when its required command record is absent.
            precise_outcomes[source_key] = {
                "valid": False,
                "started": bool(
                    isinstance(source_result, dict)
                    and source_result.get("started") is True
                ),
                "completed": False,
                "status": "unknown",
                "exit_code": None,
            }
    authoritative_runtime = (
        click_observer_runtime.state_from_verification(verification)
        if selected_observer_mode == "authoritative"
        else None
    )
    if (
        authoritative_runtime is not None
        and click_observer_runtime.validate(
            Path(workspace_root or Path.cwd()), authoritative_runtime
        ) is None
    ):
        authoritative_runtime = None
    policy_digests = (
        click_dependency_cache.observation_policy_bindings(
            Path(workspace_root),
            grouped_checks,
            declarations=_dependency_declarations(sources, running_keys),
            git_capture=git_capture,
        )
        if workspace_root and not workspace_changed else {}
    )
    current_bindings = (
        _authoritative_current_bindings(
            sources=sources,
            source_keys=running_keys,
            group_digests={
                key: _verification_group_digest(checks)
                for key, checks in grouped_checks.items()
            },
            cwd=Path.cwd(),
            workspace_root=Path(workspace_root),
            environment_digests=environment_digests,
            executable_digests=prepared_executable_digests,
            host_coverage_digest=str(running_host_coverage.get("digest", "")),
            policy_digests=policy_digests,
        )
        if workspace_root and not workspace_changed else {}
    )
    trusted_observations: dict[str, dict[str, Any]] = {}
    if (
        authoritative_runtime is not None
        and isinstance(authoritative_observations, dict)
        and workspace_digest
        and re.fullmatch(r"[0-9a-f]{64}", workspace_digest)
    ):
        (click_authoritative_observer,) = click_import_bootstrap.load_siblings(
            __package__, "click_authoritative_observer"
        )
        for source_key, current_binding in current_bindings.items():
            envelope = authoritative_observations.get(source_key)
            if envelope is None:
                continue
            verified = click_authoritative_observer.verified_observation(
                envelope,
                secret=runner_token,
                expected_binding={
                    **current_binding,
                    "mutation_revision": claimed_revision,
                    "workspace_tree_digest": workspace_digest,
                    "contract_digest": str(state.get("contract_digest", "")),
                },
            )
            if verified is not None:
                trusted_observations[source_key] = verified
    dependency_receipts = (
        click_dependency_cache.receipts_for_groups(
            Path(workspace_root),
            grouped_checks,
            declarations=_dependency_declarations(sources, running_keys),
            # Legacy/caller-provided observation JSON is intentionally ignored.
            # Only a runner-token attestation from this invocation reaches the
            # authoritative receipt builder.
            observations=trusted_observations,
            authoritative_only=True,
            authoritative_runtime=authoritative_runtime,
            authoritative_bindings=current_bindings,
            git_capture=git_capture,
        )
        if not workspace_changed and workspace_root and workspace_digest
        else {}
    )
    safe_change_receipts = (
        click_change_policy.receipts_for_groups(
            Path(workspace_root),
            grouped_checks,
            git_capture=git_capture,
        )
        if not workspace_changed and workspace_root and workspace_digest
        else {}
    )
    verification["running_evidence_keys"] = []
    verification["running_environment_digests"] = {}
    verification["running_environment_binding"] = []
    verification["running_environment_binding_digest"] = ""
    verification["running_executable_digests"] = {}
    verification["running_executable_component_digests"] = {}
    verification["running_host_coverage"] = {}
    verification["running_host_coverage_digest"] = ""
    if workspace_changed:
        previous_revision = revision
        revision += 1
        verification["mutation_revision"] = revision
        verification["status"] = "failed"
        verification["failed_revision"] = revision
        verification["unchanged_failure_retries"] = 1
        state["observations"] = _fresh_observation_state()
        for source_key, source in sources.items():
            if not isinstance(source, dict):
                continue
            check_positions = positions.get(source_key)
            source_ran = bool(
                check_positions
                and (
                    min(check_positions) < succeeded_count
                    or (exit_code != 0 and min(check_positions) == succeeded_count)
                )
            )
            precise = precise_outcomes.get(source_key)
            if precise is not None:
                source_ran = bool(precise["started"])
            elif source_results is not None:
                source_ran = source_results.get(source_key, {}).get("started") is True
            was_current = _evidence_is_current(source, previous_revision)
            if check_positions and source_ran:
                click_evidence.clear_successor_receipt(source)
                source["status"] = "failed"
                source["attempts"] = int(source.get("attempts", 0)) + 1
                source["unchanged_failure_retries"] = 1
                source["last_exit_code"] = (
                    precise["exit_code"]
                    if precise is not None and precise["valid"]
                    else exit_code
                )
            elif check_positions:
                # A preceding check stopped the batch before this source executed.
                source["status"] = "ready"
                source["unchanged_failure_retries"] = 0
                source["last_exit_code"] = None
            else:
                source["status"] = "stale" if was_current else "ready"
                source["unchanged_failure_retries"] = 0
                source["last_exit_code"] = None
            source["verified_revision"] = -1
        external = state.get("external_evidence")
        browser_required = bool(
            isinstance(external, dict) and external.get("browser_required") is True
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
    else:
        for source_key, check_positions in positions.items():
            source = sources.get(source_key)
            if not isinstance(source, dict):
                return False
            first_position = min(check_positions)
            source_ran = first_position < succeeded_count or (
                exit_code != 0 and first_position == succeeded_count
            )
            precise = precise_outcomes.get(source_key)
            if precise is not None:
                source_ran = bool(precise["started"])
            elif source_results is not None:
                source_ran = source_results.get(source_key, {}).get("started") is True
            if source_ran:
                click_evidence.clear_successor_receipt(source)
                source["attempts"] = int(source.get("attempts", 0)) + 1
            source_passed = (
                bool(precise["valid"] and precise["status"] == "passed")
                if precise is not None
                else all(position < succeeded_count for position in check_positions)
            )
            if source_passed:
                source["status"] = "passed"
                source["verified_revision"] = revision
                source["last_exit_code"] = 0
                source["unchanged_failure_retries"] = 0
                source["locked_check_digest"] = str(
                    source.get("last_check_digest", "")
                )
                # Keep the legacy ledger's integer field compatible. Consumers
                # of precise/unknown timing use the separate, validated baseline.
                source["last_success_duration_ms"] = int(measured_durations.get(source_key) or 0)
                source["last_success_duration_baseline"] = None
                environment_digest = str(
                    environment_digests.get(source_key, "")
                )
                check_digest = str(source.get("last_check_digest", ""))
                reserved_units = int(source.get("reserved_units", 0))
                if (
                    workspace_root
                    and workspace_digest
                    and environment_digest
                    and check_digest
                ):
                    source["verified_contract_digest"] = str(
                        state.get("contract_digest", "")
                    )
                    source["verified_check_digest"] = check_digest
                    source["verified_units"] = reserved_units
                    source["verified_root"] = os.path.normcase(workspace_root)
                    source["verified_tree_digest"] = workspace_digest
                    source["verified_environment_digest"] = environment_digest
                    source["verified_input_digest"] = str(
                        observed_input_digests.get(source_key, "")
                    )
                    source["verified_executable_digest"] = str(
                        prepared_executable_digests.get(source_key, "")
                    )
                    source["verified_host_coverage"] = dict(
                        running_host_coverage
                    )
                    observed_at = int(time.time()) or 1
                    source["verified_at"] = observed_at
                    timing_binding = click_incremental.timing_binding_digest(
                        source_key=source_key,
                        check_digest=check_digest,
                        environment_digest=environment_digest,
                        executable_digest=str(
                            prepared_executable_digests.get(source_key, "")
                        ),
                        host_coverage_digest=str(
                            running_host_coverage.get("digest", "")
                        ),
                        observer_mode=selected_observer_mode,
                    )
                    source["last_success_duration_baseline"] = (
                        click_incremental.build_duration_baseline(
                            duration_ms=measured_durations.get(source_key),
                            source_key=source_key,
                            revision=revision,
                            check_digest=check_digest,
                            observed_at=observed_at,
                            batch_id=origin_batch_id,
                            origin_task=origin_task,
                            observer_mode=selected_observer_mode,
                            timing_binding_digest=timing_binding,
                        )
                    )
                    _store_dependency_receipt(
                        source, dependency_receipts.get(source_key)
                    )
                    _store_safe_change_receipt(
                        source, safe_change_receipts.get(source_key)
                    )
                else:
                    source["verified_contract_digest"] = ""
                    source["verified_check_digest"] = ""
                    source["verified_units"] = 0
                    source["verified_root"] = ""
                    source["verified_tree_digest"] = ""
                    source["verified_environment_digest"] = ""
                    source["verified_input_digest"] = ""
                    source["verified_executable_digest"] = ""
                    source["verified_host_coverage"] = {}
                    source["verified_at"] = 0
                    _clear_dependency_receipt(source)
                    _clear_safe_change_receipt(source)
            elif source_ran:
                source["status"] = "failed"
                source["verified_revision"] = -1
                source["last_exit_code"] = (
                    precise["exit_code"]
                    if precise is not None and precise["valid"]
                    else exit_code
                )
            else:
                source["status"] = "ready"
                source["verified_revision"] = -1
                source["last_exit_code"] = None

        argv_keys = _evidence_keys_for_kind(sources, "argv")
        if argv_keys and all(
            _evidence_is_current(sources.get(source_key), revision)
            for source_key in argv_keys
        ):
            verification["status"] = "passed"
            verification["verified_revision"] = revision
            verification["failed_revision"] = -1
            verification["unchanged_failure_retries"] = 0
            verification["locked_batch_digest"] = batch_digest
        else:
            precise_failure = any(
                outcome is not None
                and (
                    not outcome["valid"]
                    or outcome["started"] and outcome["status"] != "passed"
                )
                for outcome in precise_outcomes.values()
            )
            verification["status"] = (
                "failed" if exit_code != 0 or precise_failure else "ready"
            )
            verification["verified_revision"] = -1
            verification["failed_revision"] = (
                revision if exit_code != 0 or precise_failure else -1
            )
    if shadow_observer_records:
        try:
            click_observer_common.store_records(
                verification, shadow_observer_records
            )
        except Exception:
            # Shadow telemetry must never change verification authority or its
            # result, including when a future collector returns malformed data.
            pass
        try:
            click_shadow_intelligence.record_run(
                verification,
                observer_records=shadow_observer_records,
                baselines=shadow_intelligence_baselines or {},
                source_exit_codes=shadow_source_exit_codes or {},
                source_contexts=shadow_execution_contexts or {},
                workspace_changed=workspace_changed,
            )
        except Exception:
            # Analysis is telemetry only and cannot change an evidence result.
            pass
    try:
        if _failure_collection_result_is_valid(collection_result):
            verification[FAILURE_COLLECTION_STATE_FIELD] = json.loads(
                json.dumps(collection_result)
            )
        if isinstance(diagnostic_records, list) and isinstance(reporting, dict):
            for diagnostic_record in diagnostic_records:
                if isinstance(diagnostic_record, dict):
                    click_diagnostics.store_record(
                        verification, diagnostic_record, reporting
                    )
        measured_batch = click_incremental.current_batch(verification)
        reused_keys = {
            item["source_key"] for item in (measured_batch or {}).get("sources", [])
            if item["decision"] in click_incremental.REUSE_DECISIONS
            and _evidence_is_current(sources.get(item["source_key"]), revision)
        }
        click_incremental.record_execution(
            verification, measured_durations, source_results=source_results,
            reused_keys=reused_keys, exit_code=exit_code,
            workspace_changed=workspace_changed,
            runner_duration_ms=(
                (time.perf_counter_ns() - runner_started_ns) / 1_000_000
                if runner_started_ns is not None else None
            ),
        )
    except Exception:
        # Measurement cannot alter evidence authority or the recorded result.
        pass
    if not click_claims.complete_claim(
        state,
        capability="verification",
        claim_mode="one-use-runner",
        request_digest=batch_digest,
        mutation_revision=claimed_revision,
        exit_code=exit_code,
    ):
        return False
    state["verification"] = verification
    state["updated_at"] = int(time.time())
    _write_json(path, state)
    return True


def _claim_verification_run(
    state_path: Path,
    raw: str,
    batch_digest: str,
    runner_token: str,
    *,
    file_content_digest: Callable[[Path], str] = _file_content_digest,
    git_capture: Callable[[Path, list[str]], bytes | None] = _git_capture,
) -> tuple[dict[str, Any] | None, str]:
    """Atomically bind one runner invocation before any check can execute."""
    if not _managed_contract_path(state_path):
        return None, "Click verification runner received an unmanaged state path."
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None, "Click verification runner could not read its contract state."
    if not click_runtime_state.view(state).execution_authorized:
        return None, "Click verification runner is no longer authorized to execute."
    claim_binding = _verification_claim_binding(state)
    if claim_binding is None:
        return None, "Click verification runner authority or revision is malformed."
    verification = state.get("verification")
    if not isinstance(verification, dict):
        return None, "Click verification runner could not read its approved scale."
    if verification.get("status") != "running":
        return None, "Click verification runner is no longer authorized to execute."
    if verification.get("last_batch_digest") != batch_digest:
        return None, "Click verification runner batch digest did not match active state."
    token_digest = hashlib.sha256(runner_token.encode()).hexdigest()
    if not secrets.compare_digest(
        str(verification.get("runner_token_digest", "")), token_digest
    ):
        return None, "Click verification runner token did not match active state."
    claimed_at = verification.get("runner_claimed_at", 0)
    if not isinstance(claimed_at, int) or isinstance(claimed_at, bool):
        return None, "Click verification runner claim state is malformed."
    if claimed_at:
        return None, "Click verification runner was already claimed; replay is blocked."
    if not _unclaimed_reservation_is_fresh(
        verification.get("started_at", 0), VERIFY_RUNNING_TTL_SECONDS
    ):
        return None, "Click verification runner authorization expired before execution."
    running_host_coverage = verification.get("running_host_coverage")
    if not _verification_host_coverage_binding_is_authentic(
        running_host_coverage,
        verification.get("running_host_coverage_digest"),
        runner_token,
    ):
        return (
            None,
            "Click verification runner host coverage binding was malformed or changed.",
        )

    scale = str(verification.get("scale", ""))
    if not click_verification_policy.is_profile(scale):
        return None, "Click verification runner could not read its approved scale."
    sources = _evidence_sources(state)
    if sources is None or not sources:
        return None, "Click verification runner could not read its evidence ledger."
    batch, _, error = _validate_verification_batch(raw, scale, sources)
    if error:
        return None, error
    assert batch is not None
    if _capability_digest(batch) != batch_digest:
        return None, "Click verification runner batch digest did not match."
    expected_workdir = batch.get("workdir")
    if not isinstance(expected_workdir, str) or not expected_workdir:
        return None, "Click verification runner workdir binding was missing."
    try:
        actual_workdir = Path.cwd().resolve(strict=True)
        prepared_workdir = Path(expected_workdir).resolve(strict=True)
    except (OSError, RuntimeError):
        return None, "Click verification runner could not resolve its working directory."
    if os.path.normcase(str(actual_workdir)) != os.path.normcase(
        str(prepared_workdir)
    ):
        return (
            None,
            "Click verification runner workdir did not match the prepared capability.",
        )
    running_keys = {
        key
        for key in verification.get("running_evidence_keys", [])
        if isinstance(key, str)
    }
    batch_keys = {
        _evidence_key(str(check["evidence_id"])) for check in batch["checks"]
    }
    if not running_keys or batch_keys != running_keys:
        return None, "Click verification runner evidence binding did not match active state."

    grouped_checks, grouping_error = _verification_groups(batch)
    if grouping_error:
        return None, grouping_error
    shard_error = click_evidence_shards.running_plan_error(
        Path.cwd(),
        state.get("evidence_state"),
        grouped_checks,
        git_capture=git_capture,
    )
    if shard_error:
        return None, shard_error
    runtime_command_plans = _verification_command_plans(batch)
    persisted_command_plans = click_incremental.current_command_plans(
        verification, running_keys
    )
    if persisted_command_plans is not None:
        for source_key, runtime_plans in runtime_command_plans.items():
            persisted_plans = persisted_command_plans.get(source_key)
            if (
                not isinstance(persisted_plans, list)
                or len(persisted_plans) != len(runtime_plans)
                or any(
                    persisted["source_position"] != runtime["source_position"]
                    or persisted["check_digest"] != runtime["check_digest"]
                    for persisted, runtime in zip(persisted_plans, runtime_plans)
                )
            ):
                return None, "Click verification command outcome binding did not match."
        runtime_command_plans = persisted_command_plans
    prepared_environment_digests = verification.get("running_environment_digests")
    prepared_executable_digests = verification.get("running_executable_digests")
    prepared_executable_components = verification.get(
        "running_executable_component_digests"
    )
    for prepared in (prepared_environment_digests, prepared_executable_digests):
        if (
            not isinstance(prepared, dict)
            or set(prepared) != running_keys
            or any(
                not isinstance(value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", value)
                for value in prepared.values()
            )
        ):
            return None, "Click verification runner context binding was malformed."
    if (
        not isinstance(prepared_executable_components, dict)
        or set(prepared_executable_components) != running_keys
        or any(
            not isinstance(components, dict)
            or set(components) != {"selection", "content", "runtime"}
            or any(
                not isinstance(value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", value)
                for value in components.values()
            )
            for components in prepared_executable_components.values()
        )
    ):
        return None, "Click verification runner context binding was malformed."
    running_environment_binding = verification.get("running_environment_binding")
    if not _verification_environment_binding_is_authentic(
        running_environment_binding,
        verification.get("running_environment_binding_digest"),
        runner_token,
    ):
        return None, "Click verification runner environment binding was malformed."
    current_environment = _observer_environment(
        _verification_environment(cwd=Path.cwd()), verification
    )
    verification_environment, environment_rebound, binding_error = (
        _verification_environment_from_binding(
            running_environment_binding,
            runner_token,
            current_environment,
        )
    )
    if binding_error:
        return None, binding_error
    assert verification_environment is not None
    policy_digests = click_dependency_cache.observation_policy_bindings(
        Path.cwd(),
        grouped_checks,
        declarations=_dependency_declarations(sources, running_keys),
        git_capture=git_capture,
    )
    shadow_bindings: dict[str, str] = {}
    claim_input_bindings: dict[str, dict[str, Any]] = {}
    claim_file_digests = click_verification_bindings.FileDigestStage(file_content_digest)
    for source_key, checks in grouped_checks.items():
        source = sources.get(source_key)
        if not isinstance(source, dict):
            return None, "Click verification runner source reservation is unavailable."
        expected_digest = _verification_group_digest(checks)
        shadow_bindings[source_key] = expected_digest
        if source.get("reserved_check_digest") != expected_digest:
            return None, "Click verification runner source reservation did not match."
        executable_records = _verification_executable_records(
            checks,
            cwd=Path.cwd(),
            environment=verification_environment,
            file_content_digest=claim_file_digests,
        )
        if executable_records is None:
            return None, "Click verification executable fingerprint was unstable before execution."
        current_executable_digest = _capability_digest(
            {
                "executables": _verification_executable_payload(
                    executable_records
                )
            }
        )
        base_environment_digest = _verification_environment_digest_from_records(
            executable_records,
            cwd=Path.cwd(),
            environment=verification_environment,
        )
        current_environment_digest, input_binding = (
            click_verification_inputs.context_digest(
                base_environment_digest, checks, cwd=Path.cwd()
            )
        )
        claim_input_bindings[source_key] = input_binding
        if not secrets.compare_digest(
            str(prepared_executable_digests.get(source_key, "")),
            current_executable_digest,
        ):
            current_components = (
                click_verification_bindings.verification_executable_component_digests(
                    executable_records
                )
            )
            prepared_components = prepared_executable_components[source_key]
            changed_components = [
                name
                for name in ("selection", "content", "runtime")
                if not secrets.compare_digest(
                    str(prepared_components.get(name, "")),
                    str(current_components.get(name, "")),
                )
            ]
            changed = ", ".join(changed_components) or "aggregate"
            return None, (
                "Click verification executable changed before execution "
                f"({changed} binding)."
            )
        if not secrets.compare_digest(
            str(prepared_environment_digests.get(source_key, "")),
            current_environment_digest,
        ):
            prepared_environment_digests[source_key] = current_environment_digest
            environment_rebound = True
        for check, record in zip(checks, executable_records):
            execution_path = record.get("_execution_path")
            if not isinstance(execution_path, str) or not execution_path:
                return None, "Click verification executable binding was malformed."
            approved_argv = list(check["argv"])
            execution_argv = list(approved_argv)
            execution_argv[0] = execution_path
            # Preserve the approved identity privately while keeping the
            # established claim API's executable-pinned argv behavior.
            check["_click_approved_argv"] = approved_argv
            check["argv"] = execution_argv
    claimed_at = int(time.time()) or 1
    _, claim_error = click_claims.record_claim(
        state,
        capability="verification",
        claim_mode="one-use-runner",
        request_digest=batch_digest,
        token_digest=token_digest,
        mutation_revision=claim_binding["mutation_revision"],
        claimed_at=claimed_at,
    )
    if claim_error:
        return None, claim_error
    verification["runner_claimed_at"] = claimed_at
    verification["running_environment_digests"] = prepared_environment_digests
    state["verification"] = verification
    state["updated_at"] = int(time.time())
    _write_json(state_path, state)
    # This stays inside this invocation, outside the persisted receipt schema.
    batch["_click_claim_binding"] = claim_binding
    batch["_click_verification_environment"] = verification_environment
    batch["_click_verification_environment_rebound"] = environment_rebound
    batch["_click_command_plans"] = runtime_command_plans
    measured_batch = click_incremental.current_batch(verification)
    batch["_click_incremental_batch_id"] = (
        str(measured_batch.get("batch_id", ""))
        if isinstance(measured_batch, dict)
        else ""
    )
    batch["_click_shadow_bindings"] = shadow_bindings
    batch["_click_explicit_input_bindings"] = claim_input_bindings
    batch["_click_shadow_contexts"] = {
        source_key: {
            "environment_digest": str(prepared_environment_digests[source_key]),
            "executable_digest": str(prepared_executable_digests[source_key]),
            "host_coverage_digest": str(running_host_coverage.get("digest", "")),
        }
        for source_key in sorted(running_keys)
    }
    authoritative_runtime = (
        click_observer_runtime.state_from_verification(verification)
        if click_observer_control.mode(verification) == "authoritative"
        else None
    )
    if (
        authoritative_runtime is not None
        and click_observer_runtime.validate(Path.cwd(), authoritative_runtime) is None
    ):
        authoritative_runtime = None
    root_output = git_capture(Path.cwd(), ["rev-parse", "--show-toplevel"])
    try:
        authoritative_root = (
            Path(os.fsdecode(root_output.strip())).resolve(strict=True)
            if root_output is not None else None
        )
    except (OSError, RuntimeError):
        authoritative_root = None
    authoritative_current = (
        _authoritative_current_bindings(
            sources=sources,
            source_keys=running_keys,
            group_digests=shadow_bindings,
            cwd=Path.cwd(),
            workspace_root=authoritative_root,
            environment_digests=prepared_environment_digests,
            executable_digests=prepared_executable_digests,
            host_coverage_digest=str(running_host_coverage.get("digest", "")),
            policy_digests=policy_digests,
        )
        if authoritative_root is not None else {}
    )
    batch["_click_authoritative_runtime"] = authoritative_runtime
    batch["_click_authoritative_contexts"] = {
        source_key: {
            **authoritative_current[source_key],
            "mutation_revision": claim_binding["mutation_revision"],
            "contract_digest": str(state.get("contract_digest", "")),
        }
        for source_key in sorted(authoritative_current)
    }
    batch["_click_mutation_revision"] = claim_binding["mutation_revision"]
    batch["_click_observer_mode"] = click_observer_control.mode(verification)
    return batch, ""


def _release_unclaimed_verification_reservation(
    state_path: Path, batch_digest: str, runner_token: str, *,
    runner_duration_ms: float | None = None,
) -> bool:
    """Release one authenticated reservation when admission failed pre-check."""
    if not _managed_contract_path(state_path):
        return False
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    if _verification_claim_binding(state) is None:
        return False
    verification = state.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "running":
        return False
    if verification.get("last_batch_digest") != batch_digest:
        return False
    token_digest = hashlib.sha256(runner_token.encode()).hexdigest()
    if not secrets.compare_digest(
        str(verification.get("runner_token_digest", "")), token_digest
    ):
        return False
    claimed_at = verification.get("runner_claimed_at", 0)
    if (
        not isinstance(claimed_at, int)
        or isinstance(claimed_at, bool)
        or claimed_at != 0
    ):
        return False
    sources = _evidence_sources(state)
    if sources is None or not sources:
        return False
    running_keys = verification.get("running_evidence_keys")
    if not isinstance(running_keys, list) or not running_keys:
        return False
    for source_key in running_keys:
        source = sources.get(source_key) if isinstance(source_key, str) else None
        if not isinstance(source, dict) or source.get("status") != "running":
            return False

    for source_key in running_keys:
        source = sources[source_key]
        source["status"] = "ready"
        source["last_exit_code"] = None
    click_incremental.reject_batch(
        verification, reason="runner-admission-rejected",
        runner_duration_ms=runner_duration_ms,
    )
    verification.update(
        {
            "status": "ready",
            "runner_token_digest": "",
            "runner_claimed_at": 0,
            "running_evidence_keys": [],
            "running_environment_digests": {},
            "running_environment_binding": [],
            "running_environment_binding_digest": "",
            "running_executable_digests": {},
            "running_executable_component_digests": {},
            "running_host_coverage": {},
            "running_host_coverage_digest": "",
            "started_at": 0,
            "last_exit_code": None,
        }
    )
    state["verification"] = verification
    state["updated_at"] = int(time.time())
    _write_json(state_path, state)
    return True


def _record_incremental_start(
    path: Path,
    digest: str,
    token: str,
    source_key: str,
    *,
    position: int,
    check_digest: str,
    started_offset_ms: int | float,
) -> None:
    """Best-effort live telemetry, authenticated by the already claimed runner."""
    try:
        with _state_lock():
            if not _managed_contract_path(path):
                return
            state = json.loads(path.read_text(encoding="utf-8"))
            verification = state.get("verification", {})
            if (
                verification.get("status") != "running"
                or verification.get("last_batch_digest") != digest
                or not verification.get("runner_claimed_at")
                or not secrets.compare_digest(
                    str(verification.get("runner_token_digest", "")),
                    hashlib.sha256(token.encode()).hexdigest(),
                )
            ):
                return
            if click_incremental.mark_command_started(
                verification,
                source_key,
                position=position,
                check_digest=check_digest,
                started_offset_ms=started_offset_ms,
            ):
                _write_json(path, state)
    except Exception:
        pass  # An unavailable telemetry write cannot execute a second command.


def _record_incremental_completion(
    path: Path,
    digest: str,
    token: str,
    source_key: str,
    *,
    position: int,
    check_digest: str,
    status: str,
    reason: str,
    finished_offset_ms: int | float | None,
    duration_ms: int | float | None,
    source_duration_ms: int | float | None,
    exit_code: int | None,
    diagnostic_record: dict[str, Any] | None = None,
    reporting: dict[str, Any] | None = None,
) -> None:
    """Persist one command result while the claimed batch is still active."""
    try:
        with _state_lock():
            if not _managed_contract_path(path):
                return
            state = json.loads(path.read_text(encoding="utf-8"))
            verification = state.get("verification", {})
            if (
                verification.get("status") != "running"
                or verification.get("last_batch_digest") != digest
                or not verification.get("runner_claimed_at")
                or not secrets.compare_digest(
                    str(verification.get("runner_token_digest", "")),
                    hashlib.sha256(token.encode()).hexdigest(),
                )
            ):
                return
            if click_incremental.mark_command_completed(
                verification,
                source_key,
                position=position,
                check_digest=check_digest,
                status=status,
                reason=reason,
                finished_offset_ms=finished_offset_ms,
                duration_ms=duration_ms,
                source_duration_ms=source_duration_ms,
                exit_code=exit_code,
                log_ref=(
                    diagnostic_record.get("log_ref")
                    if isinstance(diagnostic_record, dict)
                    else None
                ),
            ):
                if isinstance(diagnostic_record, dict) and isinstance(reporting, dict):
                    click_diagnostics.store_record(
                        verification, diagnostic_record, reporting
                    )
                state["updated_at"] = int(time.time())
                _write_json(path, state)
    except Exception:
        # Telemetry cannot repeat a check or change evidence authority.
        pass


def _collection_boundary_check(
    state_path: Path,
    batch_digest: str,
    runner_token: str,
    *,
    expected_claim_binding: dict[str, Any] | None,
    next_source_key: str,
    grouped_checks: dict[str, list[dict[str, Any]]],
    before: dict[str, Any] | None,
    verification_environment: dict[str, str],
    file_content_digest: Callable[[Path], str],
    git_workspace_snapshot: Callable[..., dict[str, Any] | None],
) -> tuple[str, float]:
    """Recheck cancellation, claim, workspace, environment, and executable."""

    started_ns = time.perf_counter_ns()
    try:
        with _state_lock():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        verification = state.get("verification")
        claim_binding = _verification_claim_binding(state)
        if (
            claim_binding is None
            or claim_binding != expected_claim_binding
            or not isinstance(verification, dict)
            or verification.get("status") != "running"
            or not click_evidence.revision_is_valid(verification.get("runner_claimed_at"))
            or verification["runner_claimed_at"] <= 0
            or verification.get("last_batch_digest") != batch_digest
            or not secrets.compare_digest(
                str(verification.get("runner_token_digest", "")),
                hashlib.sha256(runner_token.encode()).hexdigest(),
            )
        ):
            return "claim-or-cancellation-changed", (
                time.perf_counter_ns() - started_ns
            ) / 1_000_000
        checks = grouped_checks.get(next_source_key)
        expected_executables = verification.get("running_executable_digests")
        expected_environments = verification.get("running_environment_digests")
        if (
            not isinstance(checks, list)
            or not checks
            or not isinstance(expected_executables, dict)
            or not isinstance(expected_environments, dict)
        ):
            return "boundary-binding-unavailable", (
                time.perf_counter_ns() - started_ns
            ) / 1_000_000
        if before is not None:
            current = git_workspace_snapshot(
                Path.cwd(), list(before.get("protected_untracked", []))
            )
            if (
                not isinstance(current, dict)
                or current.get("root") != before.get("root")
                or current.get("digest") != before.get("digest")
            ):
                return "workspace-drift", (
                    time.perf_counter_ns() - started_ns
                ) / 1_000_000
        executable_records = _verification_executable_records(
            checks,
            cwd=Path.cwd(),
            environment=verification_environment,
            file_content_digest=file_content_digest,
        )
        if executable_records is None:
            return "executable-drift", (
                time.perf_counter_ns() - started_ns
            ) / 1_000_000
        executable_digest = _capability_digest(
            {"executables": _verification_executable_payload(executable_records)}
        )
        base_environment_digest = _verification_environment_digest_from_records(
            executable_records,
            cwd=Path.cwd(),
            environment=verification_environment,
        )
        environment_digest, _ = click_verification_inputs.context_digest(
            base_environment_digest, checks, cwd=Path.cwd()
        )
        if not secrets.compare_digest(
            str(expected_executables.get(next_source_key, "")), executable_digest
        ):
            return "executable-drift", (
                time.perf_counter_ns() - started_ns
            ) / 1_000_000
        if not secrets.compare_digest(
            str(expected_environments.get(next_source_key, "")), environment_digest
        ):
            return "environment-drift", (
                time.perf_counter_ns() - started_ns
            ) / 1_000_000
        with _state_lock():
            current_state = json.loads(state_path.read_text(encoding="utf-8"))
        current_verification = current_state.get("verification")
        if (
            _verification_claim_binding(current_state) != claim_binding
            or not isinstance(current_verification, dict)
            or current_verification.get("status") != "running"
            or not click_evidence.revision_is_valid(current_verification.get("runner_claimed_at"))
            or current_verification.get("runner_claimed_at") != verification["runner_claimed_at"]
            or current_verification.get("last_batch_digest") != batch_digest
            or current_verification.get("runner_token_digest")
            != verification.get("runner_token_digest")
        ):
            return "claim-or-cancellation-changed", (
                time.perf_counter_ns() - started_ns
            ) / 1_000_000
        return "", (time.perf_counter_ns() - started_ns) / 1_000_000
    except Exception:
        return "boundary-check-failed", (
            time.perf_counter_ns() - started_ns
        ) / 1_000_000


def _run_verification(
    arguments: list[str],
    *,
    file_content_digest: Callable[[Path], str] = _file_content_digest,
    git_workspace_snapshot: Callable[..., dict[str, Any] | None] = (
        _git_workspace_snapshot
    ),
    git_metadata_present: Callable[[Path | None], bool] = _git_metadata_present,
    execute_commands: Callable[..., int] = _execute_argv_commands,
    git_capture: Callable[[Path, list[str]], bytes | None] = _git_capture,
    shadow_execute: Callable[..., click_observer_common.ShadowExecution]
    | None = None,
    authoritative_execute: Callable[..., Any] | None = None,
) -> int:
    if len(arguments) != 4:
        sys.stderr.write(
            "usage: click_gate.py run-verification <state> <digest> <token> <batch>\n"
        )
        return 2
    runner_started_ns = time.perf_counter_ns()
    state_path = Path(arguments[0])
    batch_digest, runner_token, encoded = arguments[1:]
    raw, error = _decode_encoded_request(encoded, "verification")
    if error:
        sys.stderr.write(f"{error}\n")
        return 2
    with _state_lock():
        batch, error = _claim_verification_run(
            state_path,
            raw,
            batch_digest,
            runner_token,
            file_content_digest=file_content_digest,
            git_capture=git_capture,
        )
        if error:
            _release_unclaimed_verification_reservation(
                state_path, batch_digest, runner_token,
                runner_duration_ms=(time.perf_counter_ns() - runner_started_ns) / 1_000_000,
            )
    if error:
        sys.stderr.write(f"{error}\n")
        return 2
    assert batch is not None
    checks = batch["checks"]
    grouped_checks, grouping_error = _verification_groups(batch)
    if grouping_error:
        sys.stderr.write(f"{grouping_error}\n")
        return 2
    command_plans = batch.pop("_click_command_plans", None)
    if not isinstance(command_plans, dict):
        sys.stderr.write("Click verification runner lost its command outcome binding.\n")
        return 2
    incremental_batch_id = batch.pop("_click_incremental_batch_id", "")
    if not isinstance(incremental_batch_id, str):
        incremental_batch_id = ""
    reporting, reporting_error = click_diagnostics.validate_reporting(
        batch.get("reporting")
    )
    if reporting_error or reporting is None:
        sys.stderr.write(
            f"{reporting_error or 'Click verification reporting policy was invalid.'}\n"
        )
        return 2
    failure_collection, collection_error = _validate_failure_collection(
        batch.get("failure_collection"), checks
    )
    if collection_error or failure_collection is None:
        sys.stderr.write(
            f"{collection_error or 'Click failure collection policy was invalid.'}\n"
        )
        return 2
    verification_environment = batch.pop("_click_verification_environment", None)
    if not isinstance(verification_environment, dict):
        sys.stderr.write(
            "Click verification runner lost its prepared environment binding.\n"
        )
        return 2
    environment_rebound = batch.pop(
        "_click_verification_environment_rebound", False
    )
    claim_input_bindings = batch.pop("_click_explicit_input_bindings", None)
    if (
        not isinstance(claim_input_bindings, dict)
        or set(claim_input_bindings) != set(grouped_checks)
    ):
        sys.stderr.write(
            "Click verification runner lost its explicit input binding.\n"
        )
        return 2
    shadow_bindings = batch.pop("_click_shadow_bindings", {})
    shadow_contexts = batch.pop("_click_shadow_contexts", {})
    authoritative_runtime = batch.pop("_click_authoritative_runtime", None)
    authoritative_contexts = batch.pop("_click_authoritative_contexts", {})
    shadow_revision = batch.pop("_click_mutation_revision", -1)
    observer_mode = batch.pop("_click_observer_mode", "off")
    shadow_enabled = observer_mode == "shadow"
    authoritative_enabled = observer_mode == "authoritative"
    active_shadow_execute = shadow_execute
    if shadow_enabled and active_shadow_execute is None:
        (click_dependency_trace,) = click_import_bootstrap.load_siblings(
            __package__, "click_dependency_trace"
        )
        active_shadow_execute = click_dependency_trace.run_command
    active_authoritative_execute = authoritative_execute
    if authoritative_enabled and active_authoritative_execute is None:
        (click_authoritative_observer,) = click_import_bootstrap.load_siblings(
            __package__, "click_authoritative_observer"
        )
        active_authoritative_execute = click_authoritative_observer.run_command
    if environment_rebound:
        print(
            "[Click] Verification runner environment changed after preparation; "
            "rebound to the current canonical environment.",
            flush=True,
        )
    before = git_workspace_snapshot(Path.cwd())
    shadow_workspace = Path.cwd()
    if isinstance(before, dict):
        before_root = before.get("root")
        if isinstance(before_root, str) and before_root:
            try:
                shadow_workspace = Path(before_root).resolve(strict=True)
            except (OSError, RuntimeError):
                shadow_workspace = Path.cwd()
    snapshot_failed = before is None and git_metadata_present(Path.cwd())
    if snapshot_failed:
        sys.stderr.write(
            "[Click] Verification could not establish a protected Git workspace "
            "snapshot. No check was executed.\n"
        )

    exit_code = 2 if snapshot_failed else 0
    succeeded_count = 0
    source_durations_ms: dict[str, float] = {}
    source_results = click_incremental.new_source_results(command_plans)
    diagnostic_records: list[dict[str, Any]] = []
    task_ref = hashlib.sha256(
        (str(state_path.resolve(strict=False)) + ":" + str(shadow_revision)).encode()
    ).hexdigest()
    source_completed_commands: dict[str, int] = {}
    per_source_shadow_records: dict[str, list[dict[str, Any]]] = {}
    authoritative_envelopes: dict[str, dict[str, Any]] = {}
    source_key = ""
    command_plan: dict[str, Any] | None = None
    command_actual_started_ns: int | None = None
    overall_exit_code = 0
    collection_active = False
    collection_first_failure_ns: int | None = None
    collection_failed_source_key = ""
    collection_admitted_source_keys: set[str] = set()
    collection_result: dict[str, Any] = {
        "version": FAILURE_COLLECTION_VERSION,
        "batch_ref": batch_digest,
        "requested_mode": failure_collection["mode"],
        "status": "not-triggered",
        "first_failure_source_id": "",
        "admitted_source_ids": [],
        "additional_sources_started": 0,
        "additional_failures": 0,
        "boundary_checks": 0,
        "boundary_check_ms": 0.0,
        "stop_reason": "no-failure",
    }
    if snapshot_failed:
        collection_result.update(
            status="stopped", stop_reason="runner-admission-failed"
        )
    if not snapshot_failed:
        try:
            for index, check in enumerate(checks, start=1):
                candidate_source_key = _evidence_key(str(check["evidence_id"]))
                if collection_active and candidate_source_key == collection_failed_source_key:
                    # complete_source_command already marked the remaining
                    # commands in this same source as not-run.
                    continue
                if (
                    collection_active
                    and candidate_source_key not in collection_admitted_source_keys
                ):
                    elapsed_since_failure_ms = (
                        (time.perf_counter_ns() - collection_first_failure_ns)
                        / 1_000_000
                        if collection_first_failure_ns is not None
                        else float("inf")
                    )
                    next_evidence_id = str(check["evidence_id"])
                    if next_evidence_id not in failure_collection["independent_sources"]:
                        collection_result.update(
                            status="stopped",
                            stop_reason="source-not-explicitly-independent",
                        )
                        break
                    if (
                        collection_result["additional_sources_started"]
                        >= failure_collection["max_extra_sources"]
                    ):
                        collection_result.update(
                            status="stopped", stop_reason="source-budget-exhausted"
                        )
                        break
                    if elapsed_since_failure_ms > failure_collection["start_window_ms"]:
                        collection_result.update(
                            status="stopped", stop_reason="start-window-expired"
                        )
                        break
                    boundary_reason, boundary_ms = _collection_boundary_check(
                        state_path,
                        batch_digest,
                        runner_token,
                        expected_claim_binding=batch.get("_click_claim_binding"),
                        next_source_key=candidate_source_key,
                        grouped_checks=grouped_checks,
                        before=before,
                        verification_environment=verification_environment,
                        file_content_digest=file_content_digest,
                        git_workspace_snapshot=git_workspace_snapshot,
                    )
                    collection_result["boundary_checks"] += 1
                    collection_result["boundary_check_ms"] = round(
                        float(collection_result["boundary_check_ms"]) + boundary_ms,
                        3,
                    )
                    if boundary_reason:
                        collection_result.update(
                            status="stopped", stop_reason=boundary_reason
                        )
                        break
                    collection_admitted_source_keys.add(candidate_source_key)
                    collection_result["additional_sources_started"] += 1
                    collection_result["admitted_source_ids"].append(
                        next_evidence_id
                    )
                    collection_failed_source_key = ""
                    print(
                        "[Click] Bounded failure collection admitted explicitly "
                        f"independent source {next_evidence_id}.",
                        flush=True,
                    )
                command_plan = None
                command_actual_started_ns = None
                command_finished_ns = None
                diagnostic_record: dict[str, Any] | None = None
                capture_box: dict[str, Any] = {}
                argv = check["argv"]
                rendered = (
                    subprocess.list2cmdline(argv)
                    if os.name == "nt"
                    else shlex.join(argv)
                )
                print(
                    f"[Click verification {index}/{len(checks)}:"
                    f"{check['evidence_id']}:{check['class']}] {rendered}",
                    flush=True,
                )
                source_key = _evidence_key(str(check["evidence_id"]))
                source_position = source_completed_commands.get(source_key, 0) + 1
                plans_for_source = command_plans.get(source_key)
                if (
                    not isinstance(plans_for_source, list)
                    or source_position > len(plans_for_source)
                ):
                    raise RuntimeError("command outcome binding unavailable")
                command_plan = plans_for_source[source_position - 1]
                command_check_digest = str(command_plan["check_digest"])
                def on_target_start() -> None:
                    nonlocal command_actual_started_ns
                    if command_actual_started_ns is not None:
                        return
                    command_actual_started_ns = time.perf_counter_ns()
                    started_offset_ms = (
                        command_actual_started_ns - runner_started_ns
                    ) / 1_000_000
                    if click_incremental.start_source_command(
                        source_results,
                        source_key,
                        position=int(command_plan["position"]),
                        check_digest=command_check_digest,
                        started_offset_ms=started_offset_ms,
                    ):
                        _record_incremental_start(
                            state_path,
                            batch_digest,
                            runner_token,
                            source_key,
                            position=int(command_plan["position"]),
                            check_digest=command_check_digest,
                            started_offset_ms=started_offset_ms,
                        )
                check_digest = (
                    shadow_bindings.get(source_key)
                    if isinstance(shadow_bindings, dict)
                    else None
                )
                can_record_shadow = bool(
                    shadow_enabled
                    and isinstance(check_digest, str)
                    and re.fullmatch(r"[0-9a-f]{64}", check_digest)
                    and isinstance(shadow_revision, int)
                    and not isinstance(shadow_revision, bool)
                    and shadow_revision >= 0
                )
                authoritative_context = (
                    authoritative_contexts.get(source_key)
                    if isinstance(authoritative_contexts, dict)
                    else None
                )
                can_record_authoritative = bool(
                    authoritative_enabled
                    and isinstance(authoritative_runtime, dict)
                    and isinstance(authoritative_context, dict)
                    and len(grouped_checks.get(source_key, [])) == 1
                    and isinstance(before, dict)
                    and isinstance(before.get("digest"), str)
                    and re.fullmatch(r"[0-9a-f]{64}", before["digest"])
                )
                command_dispatch_started_ns = time.perf_counter_ns()
                def execute_current() -> int:
                    def execute_unobserved(current: list[str] = argv) -> int:
                        if execute_commands is _execute_argv_commands:
                            return execute_commands(
                                [current],
                                environment=verification_environment,
                                capture_output=capture_box,
                                capture_limit_bytes=int(reporting["max_bytes"]),
                                capture_tee=reporting["format"] == "raw",
                            )
                        return execute_commands(
                            [current], environment=verification_environment
                        )
                    observer_compatible = bool(
                        click_inspection.execution_argv(argv) == argv
                        and not click_inspection.is_git_remote_output_request(argv)
                    )
                    if can_record_authoritative and observer_compatible:
                        assert isinstance(authoritative_context, dict)
                        authoritative_result = active_authoritative_execute(
                            argv,
                            workspace=Path.cwd(),
                            observation_root=shadow_workspace,
                            environment=verification_environment,
                            binding_context={
                                **authoritative_context,
                                "workspace_tree_digest": str(before["digest"]),
                            },
                            runtime=authoritative_runtime,
                            runner_token=runner_token,
                            execute_unobserved=execute_unobserved,
                            resolve_backend=_resolve_read_only_executable,
                            digest_file=file_content_digest,
                        )
                        authoritative_envelopes[source_key] = (
                            authoritative_result.envelope
                        )
                        observation = authoritative_result.envelope.get(
                            "observation", {}
                        )
                        reasons = observation.get("ineligibility_reasons", [])
                        if observation.get("status") == "complete":
                            print(
                                "[Click authoritative observer] complete bound input snapshot",
                                flush=True,
                            )
                        else:
                            print(
                                "[Click authoritative observer] reuse unavailable: "
                                + ", ".join(str(reason) for reason in reasons),
                                flush=True,
                            )
                        return authoritative_result.exit_code
                    if can_record_shadow:
                        if observer_compatible:
                            shadow_result = active_shadow_execute(
                                argv,
                                workspace=Path.cwd(),
                                observation_root=shadow_workspace,
                                environment=verification_environment,
                                evidence_key=source_key,
                                check_digest=check_digest,
                                mutation_revision=shadow_revision,
                                execute_unobserved=execute_unobserved,
                                resolve_backend=_resolve_read_only_executable,
                                digest_file=file_content_digest,
                            )
                        else:
                            shadow_result = click_observer_common.run_unobserved(
                                execute_unobserved,
                                evidence_key=source_key,
                                check_digest=check_digest,
                                mutation_revision=shadow_revision,
                            )
                        if click_dependency_cache.shadow_observer_record_is_valid(
                            shadow_result.record
                        ):
                            per_source_shadow_records.setdefault(
                                source_key, []
                            ).append(shadow_result.record)
                        print(
                            click_observer_common.advisory(shadow_result.record),
                            flush=True,
                        )
                        return shadow_result.exit_code
                    return execute_unobserved()
                try:
                    with click_process.observe_target_start(on_target_start):
                        exit_code = execute_current()
                    # Injected test executors own their admission boundary; the
                    # production executor reports after successful Popen/resume.
                    if (
                        execute_commands is not _execute_argv_commands
                        and command_actual_started_ns is None
                    ):
                        on_target_start()
                finally:
                    command_finished_ns = time.perf_counter_ns()
                    elapsed_ms = (
                        command_finished_ns - command_dispatch_started_ns
                    ) / 1_000_000
                    source_durations_ms[source_key] = (
                        source_durations_ms.get(source_key, 0) + elapsed_ms
                    )
                try:
                    diagnostic_record = click_diagnostics.build_record(
                        capture_box,
                        argv=(
                            check.get("_click_approved_argv")
                            if isinstance(check.get("_click_approved_argv"), list)
                            else argv
                        ),
                        workspace=shadow_workspace,
                        state_path=state_path,
                        batch_ref=batch_digest,
                        batch_id=incremental_batch_id,
                        task_ref=task_ref,
                        revision=int(shadow_revision),
                        evidence_id=str(check["evidence_id"]),
                        source_key=source_key,
                        command_position=int(command_plan["position"]),
                        check_digest=command_check_digest,
                        exit_code=int(exit_code),
                        reporting=reporting,
                    )
                    diagnostic_records.append(diagnostic_record)
                    if reporting["format"] == "actionable":
                        print(
                            click_diagnostics.render_actionable(diagnostic_record),
                            flush=True,
                        )
                except Exception:
                    diagnostic_record = None
                    if reporting["format"] == "actionable":
                        print(
                            "[Click diagnostic] Structured details were unavailable; "
                            "the check was not repeated.",
                            flush=True,
                        )
                source_completed_commands[source_key] = source_completed_commands.get(source_key, 0) + 1
                command_status = (
                    "interrupted" if exit_code == 130
                    else "failed" if exit_code != 0
                    else "passed"
                )
                command_reason = (
                    "command-interrupted" if exit_code == 130
                    else "command-failed" if exit_code != 0
                    else "command-passed"
                )
                finished_offset_ms = (
                    (command_finished_ns - runner_started_ns) / 1_000_000
                    if command_finished_ns is not None
                    and command_actual_started_ns is not None
                    else None
                )
                command_duration_ms = (
                    (command_finished_ns - command_actual_started_ns) / 1_000_000
                    if command_finished_ns is not None
                    and command_actual_started_ns is not None
                    else None
                )
                click_incremental.complete_source_command(
                    source_results,
                    source_key,
                    position=int(command_plan["position"]),
                    check_digest=command_check_digest,
                    status=command_status,
                    reason=command_reason,
                    finished_offset_ms=finished_offset_ms,
                    duration_ms=command_duration_ms,
                    exit_code=exit_code,
                    log_ref=(
                        diagnostic_record.get("log_ref")
                        if isinstance(diagnostic_record, dict)
                        else None
                    ),
                )
                _record_incremental_completion(
                    state_path,
                    batch_digest,
                    runner_token,
                    source_key,
                    position=int(command_plan["position"]),
                    check_digest=command_check_digest,
                    status=command_status,
                    reason=command_reason,
                    finished_offset_ms=finished_offset_ms,
                    duration_ms=command_duration_ms,
                    source_duration_ms=source_durations_ms.get(source_key),
                    exit_code=exit_code,
                    diagnostic_record=diagnostic_record,
                    reporting=reporting,
                )
                if exit_code != 0:
                    if overall_exit_code == 0:
                        overall_exit_code = int(exit_code)
                    failure_kind = (
                        str(diagnostic_record.get("failure_kind", "unknown"))
                        if isinstance(diagnostic_record, dict)
                        else "unknown"
                    )
                    if collection_active:
                        collection_result["additional_failures"] += 1
                        collection_failed_source_key = source_key
                        if exit_code == 130:
                            collection_result.update(
                                status="stopped", stop_reason="command-interrupted"
                            )
                            break
                        if failure_kind != "test-failure":
                            collection_result.update(
                                status="stopped",
                                stop_reason="unsupported-failure-profile",
                            )
                            break
                        if (
                            collection_result["additional_failures"]
                            >= failure_collection["max_extra_failures"]
                        ):
                            collection_result.update(
                                status="stopped",
                                stop_reason="failure-budget-exhausted",
                            )
                            break
                        continue
                    if failure_collection["mode"] != "bounded":
                        collection_result.update(
                            status="disabled", stop_reason="fail-fast-default"
                        )
                        break
                    if (
                        str(check["evidence_id"])
                        not in failure_collection["independent_sources"]
                    ):
                        collection_result.update(
                            status="stopped",
                            stop_reason="source-not-explicitly-independent",
                        )
                        break
                    if exit_code == 130:
                        collection_result.update(
                            status="stopped", stop_reason="command-interrupted"
                        )
                        break
                    if failure_kind != "test-failure":
                        collection_result.update(
                            status="stopped",
                            stop_reason="unsupported-failure-profile",
                        )
                        break
                    collection_active = True
                    collection_first_failure_ns = command_finished_ns
                    collection_failed_source_key = source_key
                    collection_admitted_source_keys.add(source_key)
                    collection_result.update(
                        status="collecting",
                        first_failure_source_id=str(check["evidence_id"]),
                        admitted_source_ids=[str(check["evidence_id"])],
                        stop_reason="collection-active",
                    )
                    continue
                if overall_exit_code == 0:
                    succeeded_count += 1
            if collection_active and collection_result["status"] == "collecting":
                collection_result.update(
                    status="completed", stop_reason="batch-exhausted"
                )
            if overall_exit_code != 0:
                exit_code = overall_exit_code
        except KeyboardInterrupt:
            exit_code = 130
            collection_result.update(
                status="stopped", stop_reason="runner-interrupted"
            )
            if source_key in source_results and command_plan is not None:
                command_finished_ns = time.perf_counter_ns()
                finished_offset_ms = (
                    (command_finished_ns - runner_started_ns) / 1_000_000
                    if command_actual_started_ns is not None else None
                )
                command_duration_ms = (
                    (command_finished_ns - command_actual_started_ns) / 1_000_000
                    if command_actual_started_ns is not None else None
                )
                click_incremental.complete_source_command(
                    source_results,
                    source_key,
                    position=int(command_plan["position"]),
                    check_digest=str(command_plan["check_digest"]),
                    status="interrupted",
                    reason="command-interrupted",
                    finished_offset_ms=finished_offset_ms,
                    duration_ms=command_duration_ms,
                    exit_code=130,
                )
                _record_incremental_completion(
                    state_path,
                    batch_digest,
                    runner_token,
                    source_key,
                    position=int(command_plan["position"]),
                    check_digest=str(command_plan["check_digest"]),
                    status="interrupted",
                    reason="command-interrupted",
                    finished_offset_ms=finished_offset_ms,
                    duration_ms=command_duration_ms,
                    source_duration_ms=source_durations_ms.get(source_key),
                    exit_code=130,
                )
            sys.stderr.write(
                "[Click] Verification was interrupted. The active check was stopped "
                "and recorded as non-passing.\n"
            )
        except Exception:
            exit_code = 2
            collection_result.update(
                status="stopped", stop_reason="runner-error"
            )
            if source_key in source_results and command_plan is not None:
                command_finished_ns = time.perf_counter_ns()
                finished_offset_ms = (
                    (command_finished_ns - runner_started_ns) / 1_000_000
                    if command_actual_started_ns is not None else None
                )
                command_duration_ms = (
                    (command_finished_ns - command_actual_started_ns) / 1_000_000
                    if command_actual_started_ns is not None else None
                )
                click_incremental.complete_source_command(
                    source_results,
                    source_key,
                    position=int(command_plan["position"]),
                    check_digest=str(command_plan["check_digest"]),
                    status="unknown",
                    reason="command-error",
                    finished_offset_ms=finished_offset_ms,
                    duration_ms=command_duration_ms,
                    exit_code=None,
                )
                _record_incremental_completion(
                    state_path,
                    batch_digest,
                    runner_token,
                    source_key,
                    position=int(command_plan["position"]),
                    check_digest=str(command_plan["check_digest"]),
                    status="unknown",
                    reason="command-error",
                    finished_offset_ms=finished_offset_ms,
                    duration_ms=command_duration_ms,
                    source_duration_ms=source_durations_ms.get(source_key),
                    exit_code=None,
                )
            sys.stderr.write("[Click] The command boundary failed; no check was repeated.\n")

    for check in checks:
        approved_argv = check.pop("_click_approved_argv", None)
        if isinstance(approved_argv, list) and approved_argv:
            check["argv"] = approved_argv

    workspace_changed = False
    workspace_root = ""
    workspace_digest = ""
    if before is not None:
        after = git_workspace_snapshot(
            Path.cwd(), list(before["protected_untracked"])
        )
        new_untracked: list[str] = []
        if after is not None:
            workspace_root = str(after.get("root", ""))
            workspace_digest = str(after.get("digest", ""))
            new_untracked = sorted(
                set(after["current_untracked"]) - set(before["current_untracked"])
            )
            if new_untracked:
                rendered_paths = ", ".join(new_untracked[:8])
                if len(new_untracked) > 8:
                    rendered_paths += f", and {len(new_untracked) - 8} more"
                sys.stderr.write(
                    "[Click] Verification created new non-ignored untracked path(s): "
                    f"{rendered_paths}. Review them before keeping the result.\n"
                )
        suspicious_new = [
            path for path in new_untracked if _new_untracked_is_suspicious(path)
        ]
        workspace_changed = (
            after is None
            or after["digest"] != before["digest"]
            or bool(new_untracked)
        )
        if workspace_changed:
            if suspicious_new:
                sys.stderr.write(
                    "[Click] A new path looks like source, configuration, or migration "
                    "content; this classification is informational because every new "
                    "non-ignored path already makes verification stale.\n"
                )
            sys.stderr.write(
                "[Click] Verification changed protected repository content. "
                "The batch is stale; perform or restore that change through the approved "
                "mutation path before verifying again.\n"
            )
            if exit_code == 0:
                exit_code = 3

    final_input_digests: dict[str, str] = {}
    explicit_input_changed = False
    for input_source_key, input_checks in grouped_checks.items():
        final_binding = click_verification_inputs.group_binding(
            input_checks, cwd=Path.cwd()
        )
        expected_binding = claim_input_bindings.get(input_source_key)
        if not isinstance(expected_binding, dict) or any(
            final_binding.get(field) != expected_binding.get(field)
            for field in ("version", "status", "digest", "reason", "match_count")
        ):
            explicit_input_changed = True
        final_input_digests[input_source_key] = (
            str(final_binding.get("digest", ""))
            if final_binding.get("status") == "complete"
            else ""
        )
    if explicit_input_changed:
        workspace_changed = True
        sys.stderr.write(
            "[Click] An explicit verification input changed during execution. "
            "The batch is stale and no reusable PASS will be recorded.\n"
        )
        if exit_code == 0:
            exit_code = 3

    combined_shadow_records: dict[str, dict[str, Any]] = {}
    if shadow_enabled and isinstance(shadow_bindings, dict):
        for source_key, records in per_source_shadow_records.items():
            checks_for_source = grouped_checks.get(source_key, [])
            try:
                combined = click_observer_common.combine_records(
                    records,
                    evidence_key=source_key,
                    check_digest=str(shadow_bindings.get(source_key, "")),
                    mutation_revision=shadow_revision,
                    unexecuted_checks=max(0, len(checks_for_source) - len(records)),
                )
            except Exception:
                combined = None
            if combined is not None:
                combined_shadow_records[source_key] = combined

    shadow_intelligence_baselines: dict[str, dict[str, Any]] = {}
    if (
        not workspace_changed
        and workspace_root
        and isinstance(shadow_contexts, dict)
    ):
        for source_key, record in combined_shadow_records.items():
            context = shadow_contexts.get(source_key)
            if not isinstance(context, dict):
                continue
            try:
                baseline = click_shadow_intelligence.build_baseline(
                    record,
                    workspace=shadow_workspace,
                    environment_digest=str(context.get("environment_digest", "")),
                    executable_digest=str(context.get("executable_digest", "")),
                    host_coverage_digest=str(context.get("host_coverage_digest", "")),
                )
            except Exception:
                baseline = None
            if baseline is not None:
                shadow_intelligence_baselines[source_key] = baseline

    shadow_source_exit_codes: dict[str, int] = {}
    for source_key in combined_shadow_records:
        precise = click_incremental.source_command_outcome(
            source_results.get(source_key), command_plans.get(source_key, [])
        )
        if precise is not None and precise["valid"] and precise["status"] == "passed":
            shadow_source_exit_codes[source_key] = 0
        elif (
            precise is not None
            and precise["valid"]
            and isinstance(precise["exit_code"], int)
        ):
            shadow_source_exit_codes[source_key] = int(precise["exit_code"])

    with _state_lock():
        recorded = record_outcome(
            state_path,
            batch,
            batch_digest,
            runner_token,
            VerificationRunResult(
                exit_code,
                succeeded_count,
                workspace_changed=workspace_changed,
                workspace_root=workspace_root if not workspace_changed else "",
                workspace_digest=workspace_digest if not workspace_changed else "",
                explicit_input_digests=final_input_digests,
                source_durations_ms=source_durations_ms,
                source_results=source_results,
                runner_started_ns=runner_started_ns,
                shadow_observer_records=combined_shadow_records,
                authoritative_observations=authoritative_envelopes,
                shadow_intelligence_baselines=shadow_intelligence_baselines,
                shadow_source_exit_codes=shadow_source_exit_codes,
                shadow_execution_contexts=(
                    shadow_contexts if isinstance(shadow_contexts, dict) else {}
                ),
                observer_mode=observer_mode,
                diagnostic_records=diagnostic_records,
                reporting=reporting,
                collection_result=collection_result,
            ),
            git_capture=git_capture,
        )
    if not recorded:
        sys.stderr.write("Click could not record the verification result safely.\n")
        return exit_code or 2
    try:
        result_state = json.loads(state_path.read_text(encoding="utf-8"))
        message = click_incremental.host_summary(result_state.get("verification"))
        if message:
            print(message, flush=True)
    except (OSError, ValueError, TypeError):
        pass  # A display failure cannot change the observed verification result.
    return exit_code


prepare = _prepare_verification
record_result = _record_verification_result
claim_run = _claim_verification_run
release_unclaimed_reservation = _release_unclaimed_verification_reservation
run = _run_verification
