#!/usr/bin/env python3
"""Validate runner outcomes and persist evidence and incremental measurements."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import fields
from pathlib import Path
from typing import Any
import hashlib
import hmac
import json
import os
import re
import secrets
import time

if __package__:
    from . import click_import_bootstrap
else:
    import click_import_bootstrap

(_common,) = click_import_bootstrap.load_siblings(__package__, "click_verification_common")
FAILURE_COLLECTION_STATE_FIELD = _common.FAILURE_COLLECTION_STATE_FIELD
_authoritative_current_bindings = _common._authoritative_current_bindings
_clear_dependency_receipt = _common._clear_dependency_receipt
_clear_safe_change_receipt = _common._clear_safe_change_receipt
_dependency_declarations = _common._dependency_declarations
_evidence_is_current = _common._evidence_is_current
_evidence_key = _common._evidence_key
_evidence_keys_for_kind = _common._evidence_keys_for_kind
_evidence_sources = _common._evidence_sources
_failure_collection_result_is_valid = _common._failure_collection_result_is_valid
_fresh_external_evidence_state = _common._fresh_external_evidence_state
_fresh_observation_state = _common._fresh_observation_state
_git_capture = _common._git_capture
_managed_contract_path = _common._managed_contract_path
_state_lock = _common._state_lock
_store_dependency_receipt = _common._store_dependency_receipt
_store_safe_change_receipt = _common._store_safe_change_receipt
_verification_claim_binding = _common._verification_claim_binding
_verification_command_plans = _common._verification_command_plans
_verification_environment = _common._verification_environment
_verification_environment_binding_is_authentic = _common._verification_environment_binding_is_authentic
_verification_environment_from_binding = _common._verification_environment_from_binding
_verification_group_digest = _common._verification_group_digest
_verification_groups = _common._verification_groups
_verification_host_coverage_binding_is_authentic = _common._verification_host_coverage_binding_is_authentic
_write_json = _common._write_json
click_change_policy = _common.click_change_policy
click_claims = _common.click_claims
click_dependency_cache = _common.click_dependency_cache
click_diagnostics = _common.click_diagnostics
click_evidence = _common.click_evidence
click_incremental = _common.click_incremental
click_observer_common = _common.click_observer_common
click_observer_control = _common.click_observer_control
click_observer_runtime = _common.click_observer_runtime
click_shadow_intelligence = _common.click_shadow_intelligence
click_verification_reuse = _common.click_verification_reuse

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
    framework_observer_records: dict[str, dict[str, Any]] | None = None
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
    framework_observer_records: dict[str, dict[str, Any]] | None = None,
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
        if selected_observer_mode in {"authoritative", "auto"}
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
        selected_observer_mode in {"authoritative", "auto", "runtime"}
        and isinstance(authoritative_observations, dict)
        and authoritative_observations
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
            verifier = (click_dependency_cache.conditional_observer().verify
                        if isinstance(envelope, dict) and click_dependency_cache.conditional_dependency_observation_is_valid(envelope.get("observation"))
                        else click_authoritative_observer.verified_observation)
            verified = verifier(
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
    prepared_inputs = verification.pop("running_input_policy_receipts", {})
    prepared_binding = verification.pop("running_input_policy_binding", "")
    valid_input_binding = bool(
        isinstance(prepared_inputs, dict)
        and isinstance(prepared_binding, str)
        and hmac.compare_digest(prepared_binding, hmac.new(
            runner_token.encode(), json.dumps(prepared_inputs, sort_keys=True).encode(),
            hashlib.sha256,
        ).hexdigest())
    )
    safe_change_receipts = {
        key: receipt for key, receipt in safe_change_receipts.items()
        if receipt.get("provider") != click_change_policy.INPUT_PROVIDER_NAME
        or (valid_input_binding and click_change_policy.same_policy_inputs(
            prepared_inputs.get(key), receipt
        ))
    }
    verification["running_evidence_keys"] = []
    verification["running_environment_digests"] = {}
    verification["running_environment_binding"] = []
    verification["running_environment_binding_digest"] = ""
    verification["running_executable_digests"] = {}
    verification["running_executable_component_digests"] = {}
    verification["running_host_coverage"] = {}
    verification["running_host_coverage_digest"] = ""
    invalidated_reuse_keys: set[str] = set()
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
        invalidated_reuse_keys = click_verification_reuse.changed_observed_inputs(
            sources,
            {key for key in argv_keys - running_keys
             if _evidence_is_current(sources.get(key), revision)},
            project=Path(workspace_root or Path.cwd()), runtime=authoritative_runtime,
        )
        for key in invalidated_reuse_keys:
            click_evidence.clear_successor_receipt(sources[key])
            sources[key].update(status="ready", verified_revision=-1, last_exit_code=None)
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
    if framework_observer_records:
        (framework_observer,) = click_import_bootstrap.load_siblings(__package__, "click_framework_observer")
        framework_observer.store(verification, {
            key: value for key, value in framework_observer_records.items()
            if framework_observer.record_valid(value)
            and value["capture"]["binding"]["mutation_revision"] == revision
            and value["capture"]["binding"]["check_digest"] == _verification_group_digest(grouped_checks.get(key, []))
        })
        for key, value in framework_observer_records.items():
            if (key in sources and framework_observer.record_valid(value)
                    and value["capture"]["binding"]["mutation_revision"] == revision
                    and value["capture"]["binding"]["check_digest"] == _verification_group_digest(grouped_checks.get(key, []))):
                # A failed/unsupported capture is not an exact-reuse fallback.
                # Keep this requirement even before a usable learning seed:
                # worker and ignored inputs need a bound observation. Explicit
                # owner input receipts retain their separate admission checks.
                sources[key]["automatic_observation_required"] = True
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
            invalidated_reuse_keys=invalidated_reuse_keys,
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
