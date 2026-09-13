#!/usr/bin/env python3
"""Validate and consume one-use runner claims, and recheck execution boundaries."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
import hashlib
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
VERIFY_RUNNING_TTL_SECONDS = _common.VERIFY_RUNNING_TTL_SECONDS
_authoritative_current_bindings = _common._authoritative_current_bindings
_capability_digest = _common._capability_digest
_dependency_declarations = _common._dependency_declarations
_evidence_key = _common._evidence_key
_evidence_sources = _common._evidence_sources
_file_content_digest = _common._file_content_digest
_git_capture = _common._git_capture
_managed_contract_path = _common._managed_contract_path
_observer_environment = _common._observer_environment
_state_lock = _common._state_lock
_unclaimed_reservation_is_fresh = _common._unclaimed_reservation_is_fresh
_validate_verification_batch = _common._validate_verification_batch
_verification_claim_binding = _common._verification_claim_binding
_verification_command_plans = _common._verification_command_plans
_verification_environment = _common._verification_environment
_verification_environment_binding_is_authentic = _common._verification_environment_binding_is_authentic
_verification_environment_digest_from_records = _common._verification_environment_digest_from_records
_verification_environment_from_binding = _common._verification_environment_from_binding
_verification_executable_payload = _common._verification_executable_payload
_verification_executable_records = _common._verification_executable_records
_verification_group_digest = _common._verification_group_digest
_verification_groups = _common._verification_groups
_verification_host_coverage_binding_is_authentic = _common._verification_host_coverage_binding_is_authentic
_write_json = _common._write_json
click_claims = _common.click_claims
click_dependency_cache = _common.click_dependency_cache
click_evidence = _common.click_evidence
click_evidence_shards = _common.click_evidence_shards
click_incremental = _common.click_incremental
click_observer_control = _common.click_observer_control
click_observer_runtime = _common.click_observer_runtime
click_runtime_state = _common.click_runtime_state
click_verification_bindings = _common.click_verification_bindings
click_verification_inputs = _common.click_verification_inputs
click_verification_policy = _common.click_verification_policy

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
            or not {"selection", "content", "runtime"}.issubset(components)
            or any(
                not isinstance(name, str)
                or not re.fullmatch(r"[a-z0-9:._/-]{1,160}", name)
                for name in components
            )
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
    environment_drift: dict[str, Any] = {}
    verification_environment, environment_rebound, binding_error = (
        _verification_environment_from_binding(
            running_environment_binding,
            runner_token,
            current_environment,
            drift=environment_drift,
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
                for name in sorted(
                    set(prepared_components) | set(current_components)
                )
                if not secrets.compare_digest(
                    str(prepared_components.get(name, "")),
                    str(current_components.get(name, "")),
                )
            ]
            detailed_runtime_components = [
                name for name in changed_components if name.startswith("runtime:")
            ]
            if detailed_runtime_components:
                changed_components = detailed_runtime_components
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
    batch["_click_verification_environment_drift"] = environment_drift
    batch["_click_command_plans"] = runtime_command_plans
    labels = verification.get("source_labels")
    batch["_click_source_labels"] = (
        {str(key): str(value) for key, value in labels.items()} if isinstance(labels, dict) else {}
    )
    groups = verification.get("parallel_groups")
    batch["_click_parallel_groups"] = (
        {str(key): str(value) for key, value in groups.items()} if isinstance(groups, dict) else {}
    )
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
        if click_observer_control.captures_inputs(verification)
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
    batch["_click_framework_previous"] = verification.get("framework_observations", {})
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
            "running_input_policy_receipts": {},
            "running_input_policy_binding": "",
            "started_at": 0,
            "last_exit_code": None,
        }
    )
    state["verification"] = verification
    state["updated_at"] = int(time.time())
    _write_json(state_path, state)
    return True


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
