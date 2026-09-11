#!/usr/bin/env python3
"""Incremental record schemas and pure validation primitives.

This leaf owns record construction and validation, not live lifecycle state.
It imports no Click runtime module and does not decide whether evidence may be reused.  The verification
runtime supplies decisions only after its existing receipt, dependency, and
safe-change authority checks have completed.  The resulting content-free plan
is the single deterministic source for constructing the runner batch and for
explaining that batch to read-only consumers.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from typing import Any


PLAN_VERSION = 2
PLAN_FIELD = "incremental_plan"
CURRENT_BATCH_FIELD = "incremental_batch_id"
BATCH_EVENT = "verification-batch"
HISTORY_FIELD = "incremental_history"
HISTORY_EVENT = "verification-planned"
MAX_HISTORY_EVENTS = 1_000
MAX_HISTORY_AGE_SECONDS = 7 * 24 * 60 * 60
MAX_HISTORY_BYTES = 4 * 1024 * 1024
PROGRESS_VERSION = 1
PROGRESS_MODE = "verification-progress"
MAX_PROGRESS_CHECKS = 256

DECISIONS = frozenset(
    {"run", "reuse-exact", "reuse-dependency", "reuse-safe-change", "not-evaluable"}
)
REUSE_DECISIONS = frozenset(
    {"reuse-exact", "reuse-dependency", "reuse-safe-change"}
)
AUTHORITY_SOURCES = frozenset(
    {
        "runner",
        "exact-receipt",
        "runtime-dependency-observation",
        "conditional-js-observation",
        "repository-safe-change-policy",
        "none",
    }
)
REASON_CODES = frozenset(
    {
        "same-revision-receipt-current",
        "successor-evidence-current",
        "successor-evidence-dependencies-unchanged",
        "successor-evidence-safe-change-covered",
        "successor-evidence-scope-mismatch",
        "successor-evidence-integrity-invalid",
        "observed-dependencies-unchanged",
        "conditional-observed-inputs-current",
        "safe-change-policy-covered",
        "no-passing-evidence",
        "previous-verification-failed",
        "observed-input-changed",
        "check-binding-changed",
        "contract-binding-changed",
        "environment-binding-changed",
        "executable-binding-changed",
        "host-coverage-binding-changed",
        "workspace-ambiguous",
        "mutation-boundary-ambiguous",
        "observer-incomplete",
        "external-input-unmodeled",
        "policy-unavailable",
        "safe-change-policy-not-covered",
        "explicit-rerun-requested",
        "always-run-policy",
        "required-output-not-guaranteed",
        "explicit-input-receipt-missing",
        "explicit-input-changed",
        "explicit-input-unavailable",
        "runtime-identity-incomplete",
        "receipt-invalid",
    }
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
TIMING_BASELINE_VERSION = 2
TIMING_BINDING_VERSION = 1
TIMING_UNIT = "ms"
TIMING_MEASUREMENT_SCOPE = "source-command-dispatch-through-return"
TIMING_EXECUTION_MODEL = "sequential"
TIMING_OBSERVER_MODES = frozenset({"off", "shadow", "authoritative", "auto", "runtime"})
_LEGACY_BASELINE_FIELDS = frozenset({
    "duration_ms", "revision", "check_digest", "observed_at", "batch_id",
    "sample_count",
})
_TIMING_TASK_FIELDS = frozenset({"mode", "id"})
_TIMING_BASELINE_FIELDS = _LEGACY_BASELINE_FIELDS | {
    "version", "unit", "measurement_scope", "execution_model",
    "observer_mode", "timing_binding_digest", "source_key", "origin_task",
}
_DECISION_FIELDS = frozenset(
    {
        "source_key",
        "decision",
        "reason_code",
        "current_revision",
        "previous_revision",
        "check_digest",
        "authority_source",
        "estimated_avoided_ms",
    }
)
_LEGACY_PLAN_FIELDS = frozenset(
    {
        "version",
        "planned_at",
        "current_revision",
        "total_source_count",
        "executed_source_count",
        "authoritative_reuse_count",
        "exact_reuse_count",
        "dependency_reuse_count",
        "safe_change_reuse_count",
        "estimated_avoided_ms",
        "executed_duration_ms",
        "decisions",
    }
)
_PLAN_FIELDS = frozenset({
    "version", "planned_at", "current_revision", "total_source_count",
    "planned_execution_source_count", "planned_reuse_source_count", "decisions",
})
_HISTORY_FIELDS = frozenset(
    {
        "event",
        "source_key",
        "decision",
        "reason",
        "current_revision",
        "previous_revision",
        "estimated_avoided_ms",
        "timestamp",
    }
)


def _is_integer(value: Any, *, minimum: int = 0) -> bool:
    return bool(
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= minimum
    )


def is_duration(value: Any) -> bool:
    return bool(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and value >= 0
    )


def timing_task_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _TIMING_TASK_FIELDS:
        return False
    mode = value.get("mode")
    identity = value.get("id")
    pattern = (
        r"ctr_[0-9a-f]{32}" if mode == "guarded"
        else r"evs_[0-9a-f]{32}" if mode == "evidence"
        else ""
    )
    return bool(
        pattern
        and isinstance(identity, str)
        and re.fullmatch(pattern, identity)
    )


def _legacy_baseline_is_valid(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and set(value) == _LEGACY_BASELINE_FIELDS
        and is_duration(value.get("duration_ms"))
        and _is_integer(value.get("revision"))
        and isinstance(value.get("check_digest"), str)
        and _DIGEST.fullmatch(value["check_digest"])
        and _is_integer(value.get("observed_at"), minimum=1)
        and isinstance(value.get("batch_id"), str)
        and re.fullmatch(r"[0-9a-f]{32}", value["batch_id"])
        and value.get("sample_count") == 1
        and not isinstance(value.get("sample_count"), bool)
    )


def timing_baseline_is_valid(value: Any) -> bool:
    """Validate a self-describing successful source-duration sample."""
    return bool(
        isinstance(value, dict)
        and set(value) == _TIMING_BASELINE_FIELDS
        and value.get("version") == TIMING_BASELINE_VERSION
        and not isinstance(value.get("version"), bool)
        and value.get("unit") == TIMING_UNIT
        and value.get("measurement_scope") == TIMING_MEASUREMENT_SCOPE
        and value.get("execution_model") == TIMING_EXECUTION_MODEL
        and isinstance(value.get("observer_mode"), str)
        and value["observer_mode"] in TIMING_OBSERVER_MODES
        and isinstance(value.get("timing_binding_digest"), str)
        and _DIGEST.fullmatch(value["timing_binding_digest"])
        and isinstance(value.get("source_key"), str)
        and _DIGEST.fullmatch(value["source_key"])
        and timing_task_is_valid(value.get("origin_task"))
        and _legacy_baseline_is_valid({
            key: value[key] for key in _LEGACY_BASELINE_FIELDS
        })
    )


def baseline_is_valid(value: Any) -> bool:
    """Accept both storage schemas; estimate suitability is checked separately."""
    return _legacy_baseline_is_valid(value) or timing_baseline_is_valid(value)


def timing_binding_digest(
    *,
    source_key: str,
    check_digest: str,
    environment_digest: str,
    executable_digest: str,
    host_coverage_digest: str,
    observer_mode: str,
) -> str:
    """Bind comparable execution conditions without storing their values."""
    digests = (
        source_key,
        check_digest,
        environment_digest,
        executable_digest,
        host_coverage_digest,
    )
    if (
        any(
            not isinstance(item, str) or _DIGEST.fullmatch(item) is None
            for item in digests
        )
        or not isinstance(observer_mode, str)
        or observer_mode not in TIMING_OBSERVER_MODES
    ):
        return ""
    payload = {
        "version": TIMING_BINDING_VERSION,
        "unit": TIMING_UNIT,
        "measurement_scope": TIMING_MEASUREMENT_SCOPE,
        "execution_model": TIMING_EXECUTION_MODEL,
        "observer_mode": observer_mode,
        "source_key": source_key,
        "check_digest": check_digest,
        "environment_digest": environment_digest,
        "executable_digest": executable_digest,
        "host_coverage_digest": host_coverage_digest,
    }
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()


def build_duration_baseline(
    *,
    duration_ms: int | float,
    source_key: str,
    revision: int,
    check_digest: str,
    observed_at: int,
    batch_id: str,
    origin_task: dict[str, str],
    observer_mode: str,
    timing_binding_digest: str,
) -> dict[str, Any] | None:
    value = {
        "version": TIMING_BASELINE_VERSION,
        "duration_ms": duration_ms,
        "unit": TIMING_UNIT,
        "measurement_scope": TIMING_MEASUREMENT_SCOPE,
        "execution_model": TIMING_EXECUTION_MODEL,
        "observer_mode": observer_mode,
        "timing_binding_digest": timing_binding_digest,
        "source_key": source_key,
        "revision": revision,
        "check_digest": check_digest,
        "observed_at": observed_at,
        "batch_id": batch_id,
        "sample_count": 1,
        "origin_task": dict(origin_task) if isinstance(origin_task, dict) else {},
    }
    return value if timing_baseline_is_valid(value) else None


def baseline_is_suitable(
    value: Any,
    *,
    source_key: str,
    check_digest: str,
    observer_mode: str,
    timing_binding_digest: str,
) -> bool:
    """Check estimate compatibility separately from verification authority.

    Origin revision and task identity intentionally do not participate: a
    separately authorized successor may reuse an otherwise compatible sample.
    """
    return bool(
        timing_baseline_is_valid(value)
        and value["source_key"] == source_key
        and value["check_digest"] == check_digest
        and value["observer_mode"] == observer_mode
        and timing_binding_digest
        and value["timing_binding_digest"] == timing_binding_digest
    )


def decision(
    *,
    source_key: str,
    decision: str,
    reason_code: str,
    current_revision: int,
    previous_revision: int,
    check_digest: str,
    authority_source: str,
    estimated_avoided_ms: int | float | None = 0,
    duration_baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one strict, content-free plan decision."""
    value = {
        "source_key": source_key,
        "decision": decision,
        "reason_code": reason_code,
        "current_revision": current_revision,
        "previous_revision": previous_revision,
        "check_digest": check_digest,
        "authority_source": authority_source,
        "estimated_avoided_ms": estimated_avoided_ms,
        "duration_baseline": duration_baseline,
    }
    if not decision_is_valid(value):
        raise ValueError("invalid incremental-verification decision")
    return value


# Batch records are non-authoritative projections of the actual runtime. They
# share the existing age/count/byte budget with legacy planning events.
BATCH_STATUSES = frozenset({"planned", "running", "passed", "failed", "interrupted", "rejected", "incomplete"})
SOURCE_STATUSES = frozenset({"planned", "reuse-pending", "running", "passed", "failed", "interrupted", "not-run", "reused", "unknown"})
COMMAND_STATUSES = frozenset(
    {"planned", "started", "passed", "failed", "interrupted", "not-run", "unknown"}
)
COMMAND_MEASUREMENT_SCOPE = "target-start-through-command-return"
COMMAND_MEASUREMENT_SCOPES = frozenset({"unmeasured", COMMAND_MEASUREMENT_SCOPE})
REUSE_ORIGIN_KINDS = frozenset({"successor-evidence", "successor-contract"})
EXECUTION_REASONS = frozenset({
    "", "batch-finished", "request-rejected", "runner-admission-rejected",
    "plan-not-created", "reuse-applied", "command-started", "command-passed",
    "command-failed", "command-interrupted", "command-error",
    "preceding-check-stopped", "workspace-invalidated", "outcome-unconfirmed",
    "observed-input-changed",
    "reservation-expired",
    "user-cancelled",
    "not-requested",
})
_BATCH_FIELDS = frozenset({
    "event", "version", "batch_id", "timestamp", "finished_at", "current_revision",
    "status", "reason_code", "requested_source_count", "sources",
    "prepare_duration_ms", "runner_duration_ms", "request_wall_ms", "measurement_scope",
})
_SOURCE_FIELDS_V1 = _DECISION_FIELDS | {
    "duration_baseline", "label", "status", "started", "completed",
    "duration_ms", "execution_reason_code",
}
_SOURCE_FIELDS = _SOURCE_FIELDS_V1 | {"reuse_origin"}
_COMMAND_PLAN_FIELDS = frozenset(
    {"position", "source_position", "check_digest"}
)
_COMMAND_FIELDS = _COMMAND_PLAN_FIELDS | {
    "status", "started", "completed", "started_offset_ms",
    "finished_offset_ms", "duration_ms", "exit_code", "reason_code",
    "measurement_scope", "log_ref",
}
_SOURCE_FIELDS_WITH_COMMANDS = _SOURCE_FIELDS | {"commands"}
_REUSE_ORIGIN_FIELDS = frozenset({
    "kind", "batch_id", "evidence_session_id", "candidate_digest",
    "origin_revision",
})


def command_plan_is_valid(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and set(value) == _COMMAND_PLAN_FIELDS
        and _is_integer(value.get("position"), minimum=1)
        and _is_integer(value.get("source_position"), minimum=1)
        and isinstance(value.get("check_digest"), str)
        and _DIGEST.fullmatch(value["check_digest"])
    )


def planned_command_outcome(plan: dict[str, Any]) -> dict[str, Any]:
    if not command_plan_is_valid(plan):
        raise ValueError("invalid verification command plan")
    return {
        **plan,
        "status": "planned",
        "started": False,
        "completed": False,
        "started_offset_ms": None,
        "finished_offset_ms": None,
        "duration_ms": None,
        "exit_code": None,
        "reason_code": "",
        "measurement_scope": "unmeasured",
        "log_ref": None,
    }


def command_outcome_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _COMMAND_FIELDS:
        return False
    if not command_plan_is_valid(
        {key: value.get(key) for key in _COMMAND_PLAN_FIELDS}
    ):
        return False
    status = value.get("status")
    started = value.get("started")
    completed = value.get("completed")
    started_offset = value.get("started_offset_ms")
    finished_offset = value.get("finished_offset_ms")
    duration = value.get("duration_ms")
    exit_code = value.get("exit_code")
    reason = value.get("reason_code")
    scope = value.get("measurement_scope")
    log_ref = value.get("log_ref")
    if (
        status not in COMMAND_STATUSES
        or not isinstance(started, bool)
        or not isinstance(completed, bool)
        or reason not in EXECUTION_REASONS
        or scope not in COMMAND_MEASUREMENT_SCOPES
        or not (
            log_ref is None
            or isinstance(log_ref, str) and _DIGEST.fullmatch(log_ref)
        )
        or not (
            exit_code is None
            or isinstance(exit_code, int) and not isinstance(exit_code, bool)
        )
    ):
        return False
    timed = all(
        is_duration(item) for item in (started_offset, finished_offset, duration)
    )
    unmeasured_timing = all(
        item is None for item in (started_offset, finished_offset, duration)
    )
    if timed and (
        finished_offset < started_offset
        or abs(duration - (finished_offset - started_offset)) > 0.001
    ):
        return False
    started_only = bool(
        is_duration(started_offset)
        and finished_offset is None
        and duration is None
    )
    if (
        any(item is not None for item in (started_offset, finished_offset, duration))
        and not timed
        and not started_only
    ):
        return False
    if status == "planned":
        return bool(
            not started and not completed and not reason and exit_code is None
            and unmeasured_timing and scope == "unmeasured"
        )
    if status == "not-run":
        return bool(
            not started and not completed and reason
            and exit_code is None and unmeasured_timing and scope == "unmeasured"
        )
    if status == "started":
        return bool(
            started and not completed and reason == "command-started"
            and exit_code is None and is_duration(started_offset)
            and finished_offset is None and duration is None
            and scope == COMMAND_MEASUREMENT_SCOPE
        )
    if status == "unknown":
        return bool(
            not completed and reason in {"command-error", "outcome-unconfirmed"}
            and exit_code is None
            and (
                not started and unmeasured_timing and scope == "unmeasured"
                or started and (timed or started_only)
                and scope == COMMAND_MEASUREMENT_SCOPE
            )
        )
    if status == "interrupted" and not completed:
        return bool(
            started and reason in {"command-interrupted", "user-cancelled"}
            and exit_code is None and is_duration(started_offset)
            and finished_offset is None and duration is None
            and scope == COMMAND_MEASUREMENT_SCOPE
        )
    if not (started and completed and timed and scope == COMMAND_MEASUREMENT_SCOPE):
        return False
    if status == "passed":
        return exit_code == 0 and reason == "command-passed"
    if status == "failed":
        return bool(
            exit_code not in {None, 0, 130} and reason == "command-failed"
        )
    return status == "interrupted" and exit_code == 130 and reason == "command-interrupted"


def command_outcomes_match_plans(
    outcomes: Any, plans: Any, *, require_positions: bool = True
) -> bool:
    if (
        not isinstance(outcomes, list)
        or not isinstance(plans, list)
        or len(outcomes) != len(plans)
        or not outcomes
        or not all(command_outcome_is_valid(item) for item in outcomes)
        or not all(command_plan_is_valid(item) for item in plans)
    ):
        return False
    for outcome, plan in zip(outcomes, plans):
        identity_fields = _COMMAND_PLAN_FIELDS if require_positions else {
            "source_position", "check_digest"
        }
        if any(outcome[field] != plan[field] for field in identity_fields):
            return False
    return True


def _folded_command_fields(
    commands: list[dict[str, Any]],
) -> tuple[str, bool, int | None, str]:
    statuses = [str(item["status"]) for item in commands]
    if all(status == "passed" for status in statuses):
        return "passed", True, 0, "command-passed"
    if "interrupted" in statuses:
        selected = next(item for item in commands if item["status"] == "interrupted")
        return (
            "interrupted",
            bool(selected["completed"]),
            selected["exit_code"],
            str(selected["reason_code"]),
        )
    if "failed" in statuses:
        selected = next(item for item in commands if item["status"] == "failed")
        return "failed", True, selected["exit_code"], "command-failed"
    if "unknown" in statuses:
        return "unknown", False, None, "command-error"
    if (
        any(
            item["status"] == "not-run"
            and item["reason_code"] == "user-cancelled"
            for item in commands
        )
        and any(item["started"] for item in commands)
    ):
        return "interrupted", False, None, "user-cancelled"
    if "started" in statuses or "passed" in statuses:
        return "running", False, None, "command-started"
    return "planned", False, None, ""


def source_command_outcome(
    source_result: Any, plans: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Validate and fold one runner source without inferring a success prefix."""
    if not isinstance(source_result, dict) or "commands" not in source_result:
        return None
    if set(source_result) != {
        "started", "completed", "status", "reason_code", "commands"
    }:
        return {
            "valid": False, "started": bool(source_result.get("started")),
            "completed": False, "status": "unknown", "exit_code": None,
        }
    commands = source_result.get("commands")
    if not command_outcomes_match_plans(commands, plans, require_positions=False):
        return {
            "valid": False, "started": bool(source_result.get("started")),
            "completed": False, "status": "unknown", "exit_code": None,
        }
    assert isinstance(commands, list)
    started = any(item["started"] for item in commands)
    status, completed, exit_code, reason = _folded_command_fields(commands)
    valid_aggregate = bool(
        source_result.get("started") is started
        and source_result.get("completed") is completed
        and source_result.get("status") == status
        and source_result.get("reason_code") == reason
    )
    return {
        "valid": valid_aggregate,
        "started": started,
        "completed": completed,
        "status": status if valid_aggregate else "unknown",
        "exit_code": exit_code if valid_aggregate else None,
    }


def new_source_results(
    command_plans: dict[str, list[dict[str, Any]]]
) -> dict[str, dict[str, Any]]:
    if (
        not isinstance(command_plans, dict)
        or not command_plans
        or any(
            not isinstance(source_key, str)
            or _DIGEST.fullmatch(source_key) is None
            or not isinstance(plans, list)
            or not plans
            or not all(command_plan_is_valid(plan) for plan in plans)
            for source_key, plans in command_plans.items()
        )
    ):
        raise ValueError("invalid source command plans")
    return {
        source_key: {
            "started": False,
            "completed": False,
            "status": "planned",
            "reason_code": "",
            "commands": [planned_command_outcome(plan) for plan in plans],
        }
        for source_key, plans in command_plans.items()
    }


def start_source_command(
    source_results: dict[str, dict[str, Any]],
    source_key: str,
    *,
    position: int,
    check_digest: str,
    started_offset_ms: int | float,
) -> bool:
    result = source_results.get(source_key)
    commands = result.get("commands") if isinstance(result, dict) else None
    if not isinstance(commands, list) or not is_duration(started_offset_ms):
        return False
    selected = next(
        (
            command for command in commands
            if command["position"] == position
            and command["check_digest"] == check_digest
        ),
        None,
    )
    if not isinstance(selected, dict) or selected["status"] != "planned":
        return False
    selected.update(
        status="started",
        started=True,
        started_offset_ms=started_offset_ms,
        reason_code="command-started",
        measurement_scope=COMMAND_MEASUREMENT_SCOPE,
    )
    result.update(
        started=True,
        completed=False,
        status="running",
        reason_code="command-started",
    )
    return command_outcome_is_valid(selected)


def complete_source_command(
    source_results: dict[str, dict[str, Any]],
    source_key: str,
    *,
    position: int,
    check_digest: str,
    status: str,
    reason: str,
    finished_offset_ms: int | float | None,
    duration_ms: int | float | None,
    exit_code: int | None,
    log_ref: str | None = None,
) -> bool:
    result = source_results.get(source_key)
    commands = result.get("commands") if isinstance(result, dict) else None
    if not isinstance(commands, list):
        return False
    selected = next(
        (
            command for command in commands
            if command["position"] == position
            and command["check_digest"] == check_digest
        ),
        None,
    )
    if not isinstance(selected, dict) or selected["status"] not in {
        "planned", "started"
    }:
        return False
    candidate = dict(selected)
    if selected["started"]:
        candidate.update(
            status=status,
            completed=status != "unknown",
            finished_offset_ms=finished_offset_ms,
            duration_ms=duration_ms,
            exit_code=exit_code,
            reason_code=reason,
            measurement_scope=COMMAND_MEASUREMENT_SCOPE,
            log_ref=log_ref,
        )
    else:
        candidate.update(
            status="unknown",
            completed=False,
            reason_code="command-error",
            measurement_scope="unmeasured",
        )
    if not command_outcome_is_valid(candidate):
        return False
    selected.clear()
    selected.update(candidate)
    if candidate["status"] != "passed":
        for command in commands:
            if command["status"] == "planned":
                command.update(
                    status="not-run",
                    reason_code="preceding-check-stopped",
                    measurement_scope="unmeasured",
                )
    aggregate_status, completed, _, aggregate_reason = _folded_command_fields(
        commands
    )
    result.update(
        started=any(command["started"] for command in commands),
        completed=completed,
        status=aggregate_status,
        reason_code=aggregate_reason,
    )
    plans = [
        {key: command[key] for key in _COMMAND_PLAN_FIELDS}
        for command in commands
    ]
    folded = source_command_outcome(result, plans)
    return bool(folded and folded["valid"])


SUMMARY_FIELDS = (
    "total_source_count", "planned_execution_source_count", "planned_reuse_source_count",
    "executed_source_count", "completed_source_count", "passed_source_count",
    "failed_source_count", "interrupted_source_count", "not_run_source_count",
    "pending_source_count", "authoritative_reuse_count", "exact_reuse_count",
    "dependency_reuse_count", "safe_change_reuse_count", "executed_duration_ms",
    "request_wall_ms", "measured_processing_ms", "estimated_avoided_ms",
    "estimated_source_count", "baseline_sample_count",
)
CONTROL_CODES = frozenset({
    "approval-required", "contract-id-mismatch", "contract-lifecycle-rejected",
    "verification-request-rejected", "verification-guidance",
})
REVALIDATION_SAVINGS_VERSION = 1
REVALIDATION_SAVINGS_UNIT = "ms"
REVALIDATION_SAVINGS_BASIS = "sequential-source-command-intervals"
REVALIDATION_AGGREGATION_SCOPE = "actual-expanded-verification-batch"
REVALIDATION_METRIC_STATUSES = frozenset(
    {"measured", "estimated", "partial", "unmeasured"}
)
REVALIDATION_REASON_CODES = frozenset(
    {
        "request-not-finalized",
        "request-not-passed",
        "scope-incomplete",
        "executed-duration-missing",
        "reused-duration-sample-missing",
        "reused-duration-sample-incompatible",
        "legacy-timing-context-missing",
        "sequential-comparison-invalid",
        "zero-denominator",
    }
)
REVALIDATION_COVERAGE_FIELDS = frozenset(
    {
        "requested_source_count",
        "actual_executed_source_count",
        "timed_executed_source_count",
        "actual_reused_source_count",
        "timed_reused_source_count",
    }
)
REVALIDATION_SAVINGS_FIELDS = frozenset(
    {
        "version",
        "unit",
        "basis",
        "aggregation_scope",
        "omitted_test_execution_ms",
        "omitted_test_execution_status",
        "executed_test_execution_ms",
        "executed_test_execution_status",
        "full_sequential_test_execution_estimate_ms",
        "full_sequential_test_execution_estimate_status",
        "test_execution_reduction_ratio",
        "test_execution_reduction_status",
        "scope_complete",
        "timing_complete",
        "sequential_comparison_valid",
        "coverage",
        "reason_codes",
    }
)
CONTROL_FIELDS = frozenset({"event_id", "timestamp", "code", "effect"})


def control_events(state: Any) -> list[dict[str, Any]]:
    values = state.get("control_events", []) if isinstance(state, dict) else []
    if not isinstance(values, list):
        return []
    unique = {}
    for value in values[-128:]:
        if (
            isinstance(value, dict) and set(value) == CONTROL_FIELDS
            and isinstance(value.get("event_id"), str) and _DIGEST.fullmatch(value["event_id"])
            and _is_integer(value.get("timestamp"), minimum=1)
            and value.get("code") in CONTROL_CODES
            and value.get("effect") == ("advisory" if value["code"] == "verification-guidance" else "blocked")
        ):
            unique[value["event_id"]] = dict(value)
    return list(unique.values())


def record_control_event(state: dict[str, Any], event: dict[str, Any], code: str) -> bool:
    """Prose-free observation only. Never consulted by any authorization check."""
    if code not in CONTROL_CODES or not event.get("tool_use_id"):
        return False
    identity = hashlib.sha256(_canonical_bytes([
        event.get("session_id"), event.get("turn_id"), event.get("tool_use_id"),
        event.get("tool_input"), "advisory" if code == "verification-guidance" else "blocked",
    ])).hexdigest()
    values = control_events(state)
    if any(value["event_id"] == identity for value in values):
        return False
    values.append({"event_id": identity, "timestamp": int(time.time()) or 1,
                   "code": code, "effect": "advisory" if code == "verification-guidance" else "blocked"})
    state["control_events"] = values[-128:]
    return True


def _clock_id() -> str:
    return time.get_clock_info("monotonic").implementation


def start_request_clock(
    verification: dict[str, Any], batch_id: str, *,
    started_ns: int | None = None, clock_id: str | None = None,
) -> None:
    verification["incremental_request_clock"] = {
        "batch_id": batch_id, "started_ns": time.monotonic_ns() if started_ns is None else started_ns,
        "clock_id": _clock_id() if clock_id is None else clock_id,
    }


def complete_request_timing(
    verification: dict[str, Any], batch: dict[str, Any], *,
    ended_ns: int | None = None, clock_id: str | None = None,
) -> None:
    """Same-host monotonic interval; excludes pre-Hook queue and final return.

    A missing, mismatched or regressed clock stays unknown. Timing is never
    consulted by runner admission, reuse or contract completion.
    """
    clock = verification.get("incremental_request_clock")
    ended = time.monotonic_ns() if ended_ns is None else ended_ns
    if not isinstance(clock, dict):
        return
    started = clock.get("started_ns")
    if (
        clock.get("batch_id") == batch.get("batch_id")
        and clock.get("clock_id") == (_clock_id() if clock_id is None else clock_id)
        and _is_integer(started) and _is_integer(ended)
        and 0 <= ended - started <= 24 * 60 * 60 * 1_000_000_000
    ):
        batch.update(version=max(batch.get("version", 2), 3), request_wall_ms=(ended - started) / 1_000_000,
                     measurement_scope="hook-entry-to-result-recording")


def safe_label(value: Any, fallback: str) -> str:
    # Names come only from committed shard ids or explicitly safe aliases, not argv.
    return value if (
        isinstance(value, str) and len(value) <= 64
        and re.fullmatch(r"[A-Za-z0-9가-힣][A-Za-z0-9가-힣 _.-]*", value)
        and ".." not in value
        and not re.search(r"(?i)(token|secret|password|bearer|api.?key)", value)
    ) else fallback


def safe_display_text(value: Any, fallback: str = "승인 계약 원문에서 확인") -> str:
    """Bounded local display copy, not an approval or a secret-redaction proof."""
    if (
        not isinstance(value, str) or not value or len(value) > 240
        or re.fullmatch(r"[\w가-힣\s.,!?()·→–—-]+", value) is None
        or re.search(r"(?i)(token|secret|password|bearer|api.?key|비밀번호|비밀키)", value)
        or any(char in value for char in "\n\r\t")
    ):
        return fallback
    return value


def sanitize_presentation(value: Any) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    labels = value.get("evidence_labels", {})
    return {
        "name": safe_display_text(value.get("name"), "현재 작업"),
        **{key: [safe_display_text(item) for item in value.get(key, [])[:8]]
           if isinstance(value.get(key), list) else []
           for key in ("promises", "in_scope", "out_of_scope", "must_hold")},
        "evidence_labels": {
            key: safe_label(label, "검증 묶음") for key, label in list(labels.items())[:256]
            if isinstance(key, str) and _DIGEST.fullmatch(key)
        } if isinstance(labels, dict) else {},
    }


def contract_presentation(contract: dict[str, Any]) -> dict[str, Any]:
    verification = contract.get("verification", {})
    boundary = contract.get("boundary", {})
    return sanitize_presentation({
        "name": contract.get("outcome"),
        "promises": [item.get("condition") for item in verification.get("done_when", [])],
        "in_scope": boundary.get("in_scope"), "out_of_scope": boundary.get("out_of_scope"),
        "must_hold": contract.get("must_hold"),
        "evidence_labels": {hashlib.sha256(item["id"].encode()).hexdigest(): safe_label(item.get("description"), safe_label(item["id"], "검증 묶음"))
                            for item in verification.get("evidence", [])},
    })


def batch_task_is_valid(value: Any) -> bool:
    return bool(
        isinstance(value, dict) and set(value) == {"mode", "id", "name"}
        and value.get("mode") in {"guarded", "evidence"}
        and isinstance(value.get("id"), str)
        and re.fullmatch(r"ctr_[0-9a-f]{32}" if value["mode"] == "guarded" else r"evs_[0-9a-f]{32}", value["id"])
        and value.get("name") == safe_display_text(value.get("name"), "")
        and value["name"]
    )


def reuse_origin_is_valid(value: Any) -> bool:
    guarded = isinstance(value, dict) and value.get("kind") == "successor-contract"
    identity_field = "contract_id" if guarded else "evidence_session_id"
    fields = (_REUSE_ORIGIN_FIELDS - {"evidence_session_id"}) | {identity_field}
    return bool(
        isinstance(value, dict)
        and set(value) == fields
        and value.get("kind") in REUSE_ORIGIN_KINDS
        and isinstance(value.get("batch_id"), str)
        and re.fullmatch(r"[0-9a-f]{32}", value["batch_id"])
        and isinstance(value.get(identity_field), str)
        and re.fullmatch(r"ctr_[0-9a-f]{32}" if guarded else r"evs_[0-9a-f]{32}", value[identity_field])
        and isinstance(value.get("candidate_digest"), str)
        and _DIGEST.fullmatch(value["candidate_digest"])
        and _is_integer(value.get("origin_revision"))
    )


def source_result_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) not in {
        _SOURCE_FIELDS_V1, _SOURCE_FIELDS, _SOURCE_FIELDS_WITH_COMMANDS
    }:
        return False
    planned = {key: value[key] for key in _DECISION_FIELDS | {"duration_baseline"}}
    if planned["decision"] is None:
        planned.update(decision="not-evaluable", reason_code="receipt-invalid")
    if not decision_is_valid(planned):
        return False
    reuse_origin = value.get("reuse_origin")
    base_valid = bool(
        value["label"] == safe_label(value["label"], "")
        and value["label"]
        and value["status"] in SOURCE_STATUSES
        and isinstance(value["started"], bool) and isinstance(value["completed"], bool)
        and (not value["completed"] or value["started"])
        and (value["duration_ms"] is None or is_duration(value["duration_ms"]))
        and value["execution_reason_code"] in EXECUTION_REASONS
        and (value["status"] not in {"reused", "not-run", "planned", "reuse-pending"} or not value["started"])
        and (value["status"] != "reused" or value["decision"] in REUSE_DECISIONS)
        and (value["status"] not in {"passed", "failed"} or (value["started"] and value["completed"]))
        and (value["status"] != "running" or (value["started"] and not value["completed"]))
        and (value["started"] or value["duration_ms"] is None)
        and (reuse_origin is None or reuse_origin_is_valid(reuse_origin))
        and (reuse_origin is None or value["decision"] in REUSE_DECISIONS)
    )
    if not base_valid or "commands" not in value:
        return base_valid
    commands = value.get("commands")
    if (
        not isinstance(commands, list)
        or not commands
        or not all(command_outcome_is_valid(item) for item in commands)
        or [item["source_position"] for item in commands]
        != list(range(1, len(commands) + 1))
        or len({item["position"] for item in commands}) != len(commands)
    ):
        return False
    status = value["status"]
    if status in {"planned", "reuse-pending"}:
        return all(item["status"] == "planned" for item in commands)
    if status == "reused":
        return all(
            item["status"] == "not-run"
            and item["reason_code"] == "reuse-applied"
            for item in commands
        )
    if status == "not-run":
        return all(item["status"] == "not-run" for item in commands)
    folded = source_command_outcome(
        {
            "started": value["started"],
            "completed": value["completed"],
            "status": status,
            "reason_code": value["execution_reason_code"],
            "commands": commands,
        },
        [
            {key: item[key] for key in _COMMAND_PLAN_FIELDS}
            for item in commands
        ],
    )
    return bool(folded and folded["valid"])


def batch_is_valid(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    version = value.get("version")
    expected_fields = _BATCH_FIELDS | {"task"} if version in {4, 5} else _BATCH_FIELDS
    if set(value) != expected_fields:
        return False
    items = value.get("sources")
    valid = bool(
        value.get("event") == BATCH_EVENT and version in {1, 2, 3, 4, 5}
        and (version not in {4, 5} or batch_task_is_valid(value.get("task")))
        and not isinstance(value.get("version"), bool)
        and isinstance(value.get("batch_id"), str)
        and re.fullmatch(r"[0-9a-f]{32}", value["batch_id"])
        and _is_integer(value.get("timestamp"), minimum=1)
        and (value.get("finished_at") is None or _is_integer(value["finished_at"], minimum=1))
        and _is_integer(value.get("current_revision"))
        and value.get("status") in BATCH_STATUSES
        and value.get("reason_code") in EXECUTION_REASONS
        and (value.get("requested_source_count") is None or _is_integer(value["requested_source_count"]))
        and isinstance(items, list)
        and all(source_result_is_valid(item) for item in items)
        and all(
            (value["version"] == 1 and "reuse_origin" not in item)
            or (value["version"] in {2, 3, 4} and set(item) == _SOURCE_FIELDS)
            or (value["version"] == 5 and set(item) == _SOURCE_FIELDS_WITH_COMMANDS)
            for item in items
        )
        and len({item["source_key"] for item in items}) == len(items)
        and (value["requested_source_count"] is None or len(items) <= value["requested_source_count"])
        and all(value.get(key) is None or is_duration(value[key]) for key in (
            "prepare_duration_ms", "runner_duration_ms", "request_wall_ms"
        ))
        and (
            value.get("request_wall_ms") is None
            and value.get("measurement_scope") in {"unknown", "prepare-only", "prepare-and-runner-segments"}
            or value["version"] in {3, 4, 5} and is_duration(value.get("request_wall_ms"))
            and value.get("measurement_scope") == "hook-entry-to-result-recording"
        )
    )
    if not valid or version != 5:
        return valid
    positions = [
        command["position"]
        for source in items
        for command in source["commands"]
    ]
    return bool(
        positions
        and len(set(positions)) == len(positions)
        and sorted(positions) == list(range(1, len(positions) + 1))
    )




def decision_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) not in (
        _DECISION_FIELDS, _DECISION_FIELDS | {"duration_baseline"}
    ):
        return False
    selected = value.get("decision")
    authority = value.get("authority_source")
    avoided = value.get("estimated_avoided_ms")
    if (
        not isinstance(value.get("source_key"), str)
        or _DIGEST.fullmatch(value["source_key"]) is None
        or not isinstance(selected, str) or selected not in DECISIONS
        or not isinstance(value.get("reason_code"), str) or value.get("reason_code") not in REASON_CODES
        or not _is_integer(value.get("current_revision"))
        or not _is_integer(value.get("previous_revision"), minimum=-1)
        or not isinstance(value.get("check_digest"), str)
        or _DIGEST.fullmatch(value["check_digest"]) is None
        or not isinstance(authority, str) or authority not in AUTHORITY_SOURCES
        or (avoided is not None and not is_duration(avoided))
        or (value.get("duration_baseline") is not None
            and not baseline_is_valid(value["duration_baseline"]))
    ):
        return False
    expected_authority = {
        "run": "runner",
        "reuse-exact": "exact-receipt",
        "reuse-dependency": "runtime-dependency-observation",
        "reuse-safe-change": "repository-safe-change-policy",
        "not-evaluable": "none",
    }[selected]
    return bool(
        (authority == expected_authority or
         authority == "conditional-js-observation" and selected in REUSE_DECISIONS)
        and (authority != "conditional-js-observation" or value["reason_code"] == "conditional-observed-inputs-current")
        and (value["reason_code"] != "conditional-observed-inputs-current" or authority == "conditional-js-observation")
        and (selected in REUSE_DECISIONS or avoided == 0)
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
