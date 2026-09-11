#!/usr/bin/env python3
"""Canonical incremental-verification plan records.

This module does not decide whether evidence may be reused.  The verification
runtime supplies decisions only after its existing receipt, dependency, and
safe-change authority checks have completed.  The resulting content-free plan
is the single deterministic source for constructing the runner batch and for
explaining that batch to read-only consumers.
"""

from __future__ import annotations

import json
import math
import re
import time
from typing import Any, Iterable


# Preserve the existing import surface while record schemas live in a leaf.
if __package__:
    from .click_incremental_records import (
        PLAN_VERSION, PLAN_FIELD, CURRENT_BATCH_FIELD,
        BATCH_EVENT, HISTORY_FIELD, HISTORY_EVENT,
        MAX_HISTORY_EVENTS, MAX_HISTORY_AGE_SECONDS, MAX_HISTORY_BYTES,
        PROGRESS_VERSION, PROGRESS_MODE, MAX_PROGRESS_CHECKS,
        DECISIONS, REUSE_DECISIONS, AUTHORITY_SOURCES, CONDITIONAL_AUTHORITY_SOURCES,
        REASON_CODES, _DIGEST, TIMING_BASELINE_VERSION,
        TIMING_BINDING_VERSION, TIMING_UNIT, TIMING_MEASUREMENT_SCOPE,
        TIMING_EXECUTION_MODEL, TIMING_OBSERVER_MODES, _LEGACY_BASELINE_FIELDS,
        _TIMING_TASK_FIELDS, _TIMING_BASELINE_FIELDS, _DECISION_FIELDS,
        _LEGACY_PLAN_FIELDS, _PLAN_FIELDS, _HISTORY_FIELDS,
        _is_integer, is_duration, timing_task_is_valid,
        _legacy_baseline_is_valid, timing_baseline_is_valid, baseline_is_valid,
        timing_binding_digest, build_duration_baseline, baseline_is_suitable,
        decision, BATCH_STATUSES, SOURCE_STATUSES,
        COMMAND_STATUSES, COMMAND_MEASUREMENT_SCOPE, COMMAND_MEASUREMENT_SCOPES,
        REUSE_ORIGIN_KINDS, EXECUTION_REASONS, _BATCH_FIELDS,
        _SOURCE_FIELDS_V1, _SOURCE_FIELDS, _COMMAND_PLAN_FIELDS,
        _COMMAND_FIELDS, _SOURCE_FIELDS_WITH_COMMANDS, _REUSE_ORIGIN_FIELDS,
        command_plan_is_valid, planned_command_outcome, command_outcome_is_valid,
        command_outcomes_match_plans, _folded_command_fields, source_command_outcome,
        new_source_results, start_source_command, complete_source_command,
        SUMMARY_FIELDS, CONTROL_CODES, REVALIDATION_SAVINGS_VERSION,
        REVALIDATION_SAVINGS_UNIT, REVALIDATION_SAVINGS_BASIS, REVALIDATION_AGGREGATION_SCOPE,
        REVALIDATION_METRIC_STATUSES, REVALIDATION_REASON_CODES, REVALIDATION_COVERAGE_FIELDS,
        REVALIDATION_SAVINGS_FIELDS, CONTROL_FIELDS, control_events,
        record_control_event, _clock_id, start_request_clock,
        complete_request_timing, safe_label, safe_display_text,
        sanitize_presentation, contract_presentation, batch_task_is_valid,
        reuse_origin_is_valid, source_result_is_valid, batch_is_valid,
        decision_is_valid, _canonical_bytes,
    )
else:
    from click_incremental_records import (
        PLAN_VERSION, PLAN_FIELD, CURRENT_BATCH_FIELD,
        BATCH_EVENT, HISTORY_FIELD, HISTORY_EVENT,
        MAX_HISTORY_EVENTS, MAX_HISTORY_AGE_SECONDS, MAX_HISTORY_BYTES,
        PROGRESS_VERSION, PROGRESS_MODE, MAX_PROGRESS_CHECKS,
        DECISIONS, REUSE_DECISIONS, AUTHORITY_SOURCES, CONDITIONAL_AUTHORITY_SOURCES,
        REASON_CODES, _DIGEST, TIMING_BASELINE_VERSION,
        TIMING_BINDING_VERSION, TIMING_UNIT, TIMING_MEASUREMENT_SCOPE,
        TIMING_EXECUTION_MODEL, TIMING_OBSERVER_MODES, _LEGACY_BASELINE_FIELDS,
        _TIMING_TASK_FIELDS, _TIMING_BASELINE_FIELDS, _DECISION_FIELDS,
        _LEGACY_PLAN_FIELDS, _PLAN_FIELDS, _HISTORY_FIELDS,
        _is_integer, is_duration, timing_task_is_valid,
        _legacy_baseline_is_valid, timing_baseline_is_valid, baseline_is_valid,
        timing_binding_digest, build_duration_baseline, baseline_is_suitable,
        decision, BATCH_STATUSES, SOURCE_STATUSES,
        COMMAND_STATUSES, COMMAND_MEASUREMENT_SCOPE, COMMAND_MEASUREMENT_SCOPES,
        REUSE_ORIGIN_KINDS, EXECUTION_REASONS, _BATCH_FIELDS,
        _SOURCE_FIELDS_V1, _SOURCE_FIELDS, _COMMAND_PLAN_FIELDS,
        _COMMAND_FIELDS, _SOURCE_FIELDS_WITH_COMMANDS, _REUSE_ORIGIN_FIELDS,
        command_plan_is_valid, planned_command_outcome, command_outcome_is_valid,
        command_outcomes_match_plans, _folded_command_fields, source_command_outcome,
        new_source_results, start_source_command, complete_source_command,
        SUMMARY_FIELDS, CONTROL_CODES, REVALIDATION_SAVINGS_VERSION,
        REVALIDATION_SAVINGS_UNIT, REVALIDATION_SAVINGS_BASIS, REVALIDATION_AGGREGATION_SCOPE,
        REVALIDATION_METRIC_STATUSES, REVALIDATION_REASON_CODES, REVALIDATION_COVERAGE_FIELDS,
        REVALIDATION_SAVINGS_FIELDS, CONTROL_FIELDS, control_events,
        record_control_event, _clock_id, start_request_clock,
        complete_request_timing, safe_label, safe_display_text,
        sanitize_presentation, contract_presentation, batch_task_is_valid,
        reuse_origin_is_valid, source_result_is_valid, batch_is_valid,
        decision_is_valid, _canonical_bytes,
    )


def new_batch(
    plan: dict[str, Any] | None, *, batch_id: str, revision: int,
    prepared_ms: float | None, requested: list[dict[str, str]] | None = None,
    labels: dict[str, str] | None = None,
    reuse_origins: dict[str, dict[str, Any]] | None = None,
    command_plans: dict[str, list[dict[str, Any]]] | None = None,
    task: dict[str, Any] | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    timestamp = int(time.time()) if now is None else now
    items = plan["decisions"] if plan_is_valid(plan) else [
        {
            "source_key": item["source_key"], "check_digest": item["check_digest"],
            "decision": None, "reason_code": "receipt-invalid",
            "current_revision": revision, "previous_revision": -1,
            "authority_source": "none", "estimated_avoided_ms": 0, "duration_baseline": None,
        }
        for item in requested or []
    ]
    item_keys = {item["source_key"] for item in items}
    if command_plans is not None:
        if (
            set(command_plans) != item_keys
            or not all(
                isinstance(commands, list)
                and commands
                and all(command_plan_is_valid(command) for command in commands)
                and [command["source_position"] for command in commands]
                == list(range(1, len(commands) + 1))
                for commands in command_plans.values()
            )
            or sorted(
                command["position"]
                for commands in command_plans.values()
                for command in commands
            )
            != list(
                range(
                    1,
                    1 + sum(len(commands) for commands in command_plans.values()),
                )
            )
            or not batch_task_is_valid(task)
        ):
            raise ValueError("invalid verification command plans")
    batch = {
        "event": BATCH_EVENT, "version": 2, "batch_id": batch_id,
        "timestamp": timestamp, "finished_at": None, "current_revision": revision,
        "status": "planned", "reason_code": "", "requested_source_count": len(items) if plan or requested is not None else None,
        "sources": [],
        "prepare_duration_ms": prepared_ms, "runner_duration_ms": None,
        # Host queue/handoff/return is not measured by these separate processes.
        "request_wall_ms": None, "measurement_scope": "prepare-only",
    }
    if command_plans is not None:
        batch.update(version=5, task=dict(task))
    elif batch_task_is_valid(task):
        batch.update(version=4, task=dict(task))
    for index, item in enumerate(items, start=1):
        source = dict(item)
        source.setdefault("duration_baseline", None)
        source.update(
            label=safe_label((labels or {}).get(item["source_key"]), f"검증 묶음 {index}"),
            status="reuse-pending" if item["decision"] in REUSE_DECISIONS else "planned",
            started=False, completed=False, duration_ms=None, execution_reason_code="",
            reuse_origin=json.loads(json.dumps((reuse_origins or {}).get(item["source_key"])))
            if reuse_origin_is_valid((reuse_origins or {}).get(item["source_key"]))
            else None,
        )
        if command_plans is not None:
            source["commands"] = [
                planned_command_outcome(command)
                for command in command_plans[item["source_key"]]
            ]
        batch["sources"].append(source)
    if not batch_is_valid(batch):
        raise ValueError("invalid verification batch measurement")
    return batch


def store_batch(verification: dict[str, Any], batch: dict[str, Any]) -> bool:
    if not batch_is_valid(batch):
        return False
    existing = verification.get(HISTORY_FIELD, [])
    existing = existing if isinstance(existing, list) else []
    previous = next((
        item for item in existing
        if isinstance(item, dict) and item.get("batch_id") == batch["batch_id"]
        and batch_is_valid(item)
    ), None)
    if previous and previous["status"] not in {"planned", "running"}:
        return previous == batch
    if previous and batch["status"] == "planned":
        # Redelivered preparation cannot roll back a runner's witnessed start.
        return False
    retained = [
        item for item in existing
        if not isinstance(item, dict) or item.get("batch_id") != batch["batch_id"]
    ]
    verification[HISTORY_FIELD] = prune_history(
        [*retained, batch], now=int(time.time())
    )
    verification[CURRENT_BATCH_FIELD] = batch["batch_id"]
    return True


def current_batch(verification: Any) -> dict[str, Any] | None:
    if not isinstance(verification, dict):
        return None
    selected = verification.get(CURRENT_BATCH_FIELD)
    history = verification.get(HISTORY_FIELD)
    if not isinstance(history, list):
        return None
    for item in reversed(prune_history(history, now=int(time.time()))):
        if isinstance(item, dict) and item.get("batch_id") == selected and batch_is_valid(item):
            return item  # prune_history already detached it from stored state.
    return None


def current_command_plans(
    verification: Any, source_keys: Iterable[str] | None = None
) -> dict[str, list[dict[str, Any]]] | None:
    """Return content-free command identities from the current v5 batch."""
    batch = current_batch(verification)
    if batch is None or batch.get("version") != 5:
        return None
    selected = set(source_keys) if source_keys is not None else {
        item["source_key"] for item in batch["sources"]
    }
    result = {
        item["source_key"]: [
            {key: command[key] for key in _COMMAND_PLAN_FIELDS}
            for command in item["commands"]
        ]
        for item in batch["sources"]
        if item["source_key"] in selected
    }
    return result if set(result) == selected else None


def _mark_unstarted_commands(source: dict[str, Any], reason: str) -> None:
    commands = source.get("commands")
    if not isinstance(commands, list):
        return
    for command in commands:
        if command.get("status") == "planned":
            command.update(
                status="not-run",
                reason_code=reason,
                measurement_scope="unmeasured",
            )


def _mark_source_outcome_unknown(source: dict[str, Any]) -> bool:
    """Preserve witnessed commands and fail closed at the first missing result."""
    commands = source.get("commands")
    if not isinstance(commands, list):
        return False
    _mark_unstarted_commands(source, "outcome-unconfirmed")
    selected = next(
        (command for command in commands if command["status"] == "started"),
        None,
    )
    if selected is None:
        selected = next(
            (command for command in commands if command["status"] == "not-run"),
            None,
        )
    if selected is None:
        return False
    selected.update(
        status="unknown",
        completed=False,
        exit_code=None,
        reason_code="outcome-unconfirmed",
    )
    source.update(
        status="unknown",
        started=any(command["started"] for command in commands),
        completed=False,
        execution_reason_code="command-error",
        duration_ms=None,
    )
    return source_result_is_valid(source)


def mark_command_started(
    verification: dict[str, Any],
    source_key: str,
    *,
    position: int,
    check_digest: str,
    started_offset_ms: int | float,
) -> bool:
    batch = current_batch(verification)
    if (
        batch is None
        or batch.get("version") != 5
        or batch["status"] not in {"planned", "running"}
        or not is_duration(started_offset_ms)
    ):
        return False
    for source in batch["sources"]:
        if source["source_key"] != source_key:
            continue
        for command in source["commands"]:
            if (
                command["position"] != position
                or command["check_digest"] != check_digest
                or command["status"] != "planned"
            ):
                continue
            command.update(
                status="started",
                started=True,
                started_offset_ms=started_offset_ms,
                reason_code="command-started",
                measurement_scope=COMMAND_MEASUREMENT_SCOPE,
            )
            source.update(
                status="running",
                started=True,
                completed=False,
                execution_reason_code="command-started",
            )
            batch["status"] = "running"
            return store_batch(verification, batch)
    return False


def mark_command_completed(
    verification: dict[str, Any],
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
    log_ref: str | None = None,
) -> bool:
    batch = current_batch(verification)
    if (
        batch is None
        or batch.get("version") != 5
        or batch["status"] not in {"planned", "running"}
        or status not in {"passed", "failed", "interrupted", "unknown"}
        or source_duration_ms is not None and not is_duration(source_duration_ms)
    ):
        return False
    for source in batch["sources"]:
        if source["source_key"] != source_key:
            continue
        selected = next(
            (
                command
                for command in source["commands"]
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
                exit_code=None,
                reason_code="command-error",
                measurement_scope="unmeasured",
            )
        if not command_outcome_is_valid(candidate):
            return False
        selected.clear()
        selected.update(candidate)
        if candidate["status"] != "passed":
            _mark_unstarted_commands(source, "preceding-check-stopped")
        aggregate_status, completed, _, aggregate_reason = _folded_command_fields(
            source["commands"]
        )
        source.update(
            status=aggregate_status,
            started=any(command["started"] for command in source["commands"]),
            completed=completed,
            duration_ms=source_duration_ms if completed else None,
            execution_reason_code=aggregate_reason,
        )
        if not source_result_is_valid(source):
            return False
        batch["status"] = "running"
        return store_batch(verification, batch)
    return False


def batch_history(verification: Any, *, now: int | None = None) -> list[dict[str, Any]]:
    if not isinstance(verification, dict):
        return []
    events = verification.get(HISTORY_FIELD, [])
    if not isinstance(events, list):
        return []
    records = prune_history(events, now=int(time.time()) if now is None else now)
    # Repeated deliveries of the same batch never become new performance samples.
    unique = {item["batch_id"]: item for item in records if item.get("event") == BATCH_EVENT}
    return list(unique.values())  # Each retained record is already detached.


def reject_batch(
    verification: dict[str, Any], *, reason: str = "request-rejected",
    runner_duration_ms: float | None = None,
) -> bool:
    batch = current_batch(verification)
    if batch is None or batch["status"] not in {"planned", "running"}:
        return False
    batch.update(status="rejected", reason_code=reason, finished_at=int(time.time()))
    if runner_duration_ms is not None:
        batch.update(runner_duration_ms=runner_duration_ms, measurement_scope="prepare-and-runner-segments")
    for item in batch["sources"]:
        item.update(status="not-run", execution_reason_code=reason)
        _mark_unstarted_commands(item, reason)
    complete_request_timing(verification, batch)
    return store_batch(verification, batch)


def finish_reuse(verification: dict[str, Any]) -> bool:
    batch = current_batch(verification)
    if batch is None or any(item["decision"] not in REUSE_DECISIONS for item in batch["sources"]):
        return False
    batch.update(status="passed", reason_code="batch-finished", finished_at=int(time.time()))
    for item in batch["sources"]:
        item.update(status="reused", execution_reason_code="reuse-applied")
        _mark_unstarted_commands(item, "reuse-applied")
    complete_request_timing(verification, batch)
    return store_batch(verification, batch)


def mark_started(verification: dict[str, Any], source_key: str) -> bool:
    batch = current_batch(verification)
    if batch is None or batch["status"] not in {"planned", "running"}:
        return False
    for item in batch["sources"]:
        if item["source_key"] == source_key and item["decision"] not in REUSE_DECISIONS:
            if "commands" in item:
                return False
            item.update(status="running", started=True, execution_reason_code="command-started")
            batch["status"] = "running"
            return store_batch(verification, batch)
    return False


def mark_completed(
    verification: dict[str, Any], source_key: str, *, status: str,
    reason: str, duration_ms: int | float | None, completed: bool = True,
) -> bool:
    """Persist one witnessed source outcome before the next source starts."""
    if status not in {"passed", "failed", "interrupted", "unknown"}:
        return False
    if reason not in EXECUTION_REASONS or reason == "":
        return False
    if duration_ms is not None and not is_duration(duration_ms):
        return False
    batch = current_batch(verification)
    if batch is None or batch["status"] not in {"planned", "running"}:
        return False
    for item in batch["sources"]:
        if item["source_key"] != source_key or not item["started"]:
            continue
        if "commands" in item:
            return False
        if item["completed"]:
            return bool(
                item["status"] == status
                and item["execution_reason_code"] == reason
                and item["duration_ms"] == duration_ms
                and completed
            )
        item.update(
            status=status,
            completed=completed,
            duration_ms=duration_ms,
            execution_reason_code=reason,
        )
        batch["status"] = "running"
        return store_batch(verification, batch)
    return False


def interrupt_batch(verification: dict[str, Any]) -> bool:
    """Cancellation is witnessed; an active child's termination is not assumed."""
    batch = current_batch(verification)
    if batch is None or batch["status"] not in {"planned", "running"}:
        return False
    batch.update(status="interrupted", reason_code="user-cancelled", finished_at=int(time.time()))
    for item in batch["sources"]:
        if item["completed"]:
            # Cancellation revokes current authority, not the already witnessed
            # fact that this source finished before the cancellation boundary.
            continue
        commands = item.get("commands")
        if isinstance(commands, list):
            for command in commands:
                if command["status"] == "started":
                    command.update(
                        status="interrupted",
                        completed=False,
                        reason_code="user-cancelled",
                    )
                elif command["status"] == "planned":
                    command.update(
                        status="not-run",
                        reason_code="user-cancelled",
                        measurement_scope="unmeasured",
                    )
            if item["started"]:
                status, completed, _, reason = _folded_command_fields(commands)
                item.update(
                    status=status,
                    execution_reason_code=reason,
                    completed=completed,
                )
                continue
        item.update(
            status="interrupted" if item["started"] else "not-run",
            execution_reason_code="user-cancelled", completed=False,
        )
    complete_request_timing(verification, batch)
    return store_batch(verification, batch)


def merge_history(previous: Any, current: dict[str, Any]) -> None:
    """Carry only validated measurements across lifecycle resets, never receipts."""
    old = batch_history(previous)
    new = batch_history(current)
    records = {item["batch_id"]: item for item in [*old, *new]}
    current[HISTORY_FIELD] = prune_history(records.values(), now=int(time.time()))
    if CURRENT_BATCH_FIELD not in current and records:
        current[CURRENT_BATCH_FIELD] = max(records.values(), key=lambda item: item["timestamp"])["batch_id"]


def history_totals(verification: Any) -> dict[str, int]:
    batches = batch_history(verification)
    summaries = {item["batch_id"]: batch_summary(item) for item in batches}
    return _history_totals(batches, summaries)


def _history_totals(batches: list[dict], summaries: dict[str, dict]) -> dict[str, int]:
    finalized = [summaries[item["batch_id"]] for item in batches
                 if item["status"] not in {"planned", "running", "incomplete"}]
    return {"finalized_batch_count": len(finalized), **{
        key: sum(item[key] for item in finalized) for key in (
            "executed_source_count", "authoritative_reuse_count", "not_run_source_count"
        )}}


def history_accounting(verification: Any) -> dict[str, Any]:
    """Count actual group requests, including distinct retries, not test cases."""
    batches = batch_history(verification)
    return _history_accounting(batches, [batch_summary(batch) for batch in batches])


def _history_accounting(batches: list[dict], summaries: list[dict]) -> dict[str, Any]:
    unknown_requests = sum(batch["requested_source_count"] is None for batch in batches)
    denominator = sum(batch["requested_source_count"] or 0 for batch in batches)
    numerator = sum(item["authoritative_reuse_count"] for item in summaries)
    return {
        "unit": "verification-group-request", "window": "retained-history",
        "from_timestamp": min((batch["timestamp"] for batch in batches), default=None),
        "through_timestamp": max((batch["finished_at"] or batch["timestamp"] for batch in batches), default=None),
        "max_age_seconds": MAX_HISTORY_AGE_SECONDS, "max_events": MAX_HISTORY_EVENTS,
        "batch_count": len(batches), "unknown_request_batch_count": unknown_requests,
        "request_denominator": denominator, "reuse_numerator": numerator,
        "reuse_rate": numerator / denominator if denominator and not unknown_requests else None,
        "missing_baseline_duration_count": sum(item["authoritative_reuse_count"] - item["estimated_source_count"] for item in summaries),
        **{key: sum(item[key] for item in summaries) for key in (
            "executed_source_count", "passed_source_count", "failed_source_count",
            "interrupted_source_count", "not_run_source_count", "pending_source_count",
        )},
    }


def history_projection(verification: Any, *, now: int | None = None) -> dict[str, Any]:
    """Derive one detached display view; never retain it across state changes."""
    batches = batch_history(verification, now=now)
    selected = verification.get(CURRENT_BATCH_FIELD) if isinstance(verification, dict) else None
    current = next((item for item in reversed(batches) if item["batch_id"] == selected), None)
    summaries = {item["batch_id"]: batch_summary(item) for item in batches}
    return {
        "batches": batches,
        "current": current,
        "summaries": summaries,
        "accounting": _history_accounting(batches, list(summaries.values())),
        "totals": _history_totals(batches, summaries),
    }


def accounting_is_valid(value: Any) -> bool:
    example = history_accounting({})
    if not isinstance(value, dict) or set(value) != set(example):
        return False
    if value["unit"] != example["unit"] or value["window"] != example["window"]:
        return False
    for key, item in value.items():
        if key in {"unit", "window", "reuse_rate"}:
            continue
        if key in {"from_timestamp", "through_timestamp"} and item is None:
            continue
        if not _is_integer(item):
            return False
    denominator, numerator = value["request_denominator"], value["reuse_numerator"]
    return bool(numerator <= denominator and value["reuse_rate"] == (
        numerator / denominator if denominator and not value["unknown_request_batch_count"] else None
    ))


def batch_summary(batch: dict[str, Any]) -> dict[str, Any]:
    if not batch_is_valid(batch):
        raise ValueError("invalid verification batch")
    items = batch["sources"]
    started = [item for item in items if item["started"]]
    reused = [item for item in items if item["status"] == "reused"]
    baselines = [
        item["duration_baseline"]
        for item in reused
        if (
            timing_baseline_is_valid(item.get("duration_baseline"))
            and item["duration_baseline"]["source_key"] == item["source_key"]
            and item["duration_baseline"]["check_digest"]
            == item["check_digest"]
        )
    ]
    planned_runs = sum(item["decision"] in DECISIONS - REUSE_DECISIONS for item in items)
    times = [item["duration_ms"] for item in started]
    prep, runner = batch["prepare_duration_ms"], batch["runner_duration_ms"]
    measured = prep if runner is None else (
        prep + runner if prep is not None and runner is not None else None
    )
    return {
        "total_source_count": batch["requested_source_count"],
        "planned_execution_source_count": planned_runs,
        "planned_reuse_source_count": sum(item["decision"] in REUSE_DECISIONS for item in items),
        "executed_source_count": len(started),
        "completed_source_count": sum(item["completed"] for item in items),
        "passed_source_count": sum(item["status"] == "passed" for item in items),
        "failed_source_count": sum(item["status"] == "failed" for item in items),
        "interrupted_source_count": sum(item["status"] == "interrupted" for item in items),
        "not_run_source_count": sum(item["status"] == "not-run" for item in items),
        "pending_source_count": sum(item["status"] in {"planned", "running", "reuse-pending", "unknown"} for item in items),
        "authoritative_reuse_count": len(reused),
        "exact_reuse_count": sum(item["decision"] == "reuse-exact" for item in reused),
        "dependency_reuse_count": sum(item["decision"] == "reuse-dependency" for item in reused),
        "safe_change_reuse_count": sum(item["decision"] == "reuse-safe-change" for item in reused),
        "executed_duration_ms": sum(times) if all(is_duration(item) for item in times) else None,
        "request_wall_ms": batch["request_wall_ms"],
        "measured_processing_ms": measured,
        "estimated_avoided_ms": sum(item["duration_ms"] for item in baselines) if baselines or not reused else None,
        "estimated_source_count": len(baselines),
        "baseline_sample_count": sum(item["sample_count"] for item in baselines),
    }


def unmeasured_revalidation_savings(
    *, requested_source_count: int | None = None,
) -> dict[str, Any]:
    """Return the canonical no-actual-batch projection.

    A plan may establish the requested count, but it never establishes actual
    execution, reuse, or timing counts.  Those values therefore stay null
    until a concrete batch exists.
    """
    requested = (
        requested_source_count
        if _is_integer(requested_source_count)
        else None
    )
    value = {
        "version": REVALIDATION_SAVINGS_VERSION,
        "unit": REVALIDATION_SAVINGS_UNIT,
        "basis": REVALIDATION_SAVINGS_BASIS,
        "aggregation_scope": REVALIDATION_AGGREGATION_SCOPE,
        "omitted_test_execution_ms": None,
        "omitted_test_execution_status": "unmeasured",
        "executed_test_execution_ms": None,
        "executed_test_execution_status": "unmeasured",
        "full_sequential_test_execution_estimate_ms": None,
        "full_sequential_test_execution_estimate_status": "unmeasured",
        "test_execution_reduction_ratio": None,
        "test_execution_reduction_status": "unmeasured",
        "scope_complete": False,
        "timing_complete": False,
        "sequential_comparison_valid": False,
        "coverage": {
            "requested_source_count": requested,
            "actual_executed_source_count": None,
            "timed_executed_source_count": None,
            "actual_reused_source_count": None,
            "timed_reused_source_count": None,
        },
        "reason_codes": ["request-not-finalized", "scope-incomplete"],
    }
    if not revalidation_savings_is_valid(value):
        raise ValueError("invalid unmeasured revalidation-savings projection")
    return value


def _compatible_reused_timing_baseline(
    source: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    baseline = source.get("duration_baseline")
    if baseline is None:
        return None, "reused-duration-sample-missing"
    if _legacy_baseline_is_valid(baseline):
        return None, "legacy-timing-context-missing"
    if (
        not timing_baseline_is_valid(baseline)
        or baseline["source_key"] != source["source_key"]
        or baseline["check_digest"] != source["check_digest"]
    ):
        return None, "reused-duration-sample-incompatible"
    return baseline, None


def revalidation_savings(batch: Any) -> dict[str, Any]:
    """Derive A/E/F/R once from one actual verification batch.

    The result is explanatory only.  It cannot grant reuse, execution,
    approval, or completion authority and is never written back into a
    receipt.  One batch is one aggregation scope, so parent plans, refreshes,
    and other requests cannot be folded into its values.
    """
    if batch is None:
        return unmeasured_revalidation_savings()
    if not batch_is_valid(batch):
        raise ValueError("invalid verification batch")

    sources = batch["sources"]
    executed = [source for source in sources if source["started"]]
    reused = [source for source in sources if source["status"] == "reused"]
    timed_executed = [
        source for source in executed if is_duration(source.get("duration_ms"))
    ]
    timed_reused: list[dict[str, Any]] = []
    reasons: set[str] = set()
    observer_modes: set[str] = set()
    for source in reused:
        baseline, reason = _compatible_reused_timing_baseline(source)
        if baseline is None:
            if reason is not None:
                reasons.add(reason)
            continue
        timed_reused.append(baseline)
        observer_modes.add(baseline["observer_mode"])

    request_finalized = bool(
        batch["finished_at"] is not None
        and batch["status"] not in {"planned", "running", "incomplete"}
    )
    if not request_finalized:
        reasons.add("request-not-finalized")
    if batch["status"] != "passed":
        reasons.add("request-not-passed")

    requested_count = batch["requested_source_count"]
    source_scope_complete = bool(
        requested_count is not None
        and requested_count == len(sources)
        and all(
            (
                source["status"] == "passed"
                and source["decision"] not in REUSE_DECISIONS
                and source["started"]
                and source["completed"]
            )
            or (
                source["status"] == "reused"
                and source["decision"] in REUSE_DECISIONS
                and not source["started"]
                and source["execution_reason_code"] == "reuse-applied"
            )
            for source in sources
        )
    )
    scope_complete = bool(request_finalized and batch["status"] == "passed" and source_scope_complete)
    if not scope_complete:
        reasons.add("scope-incomplete")

    if len(timed_executed) != len(executed):
        reasons.add("executed-duration-missing")
    timing_complete = bool(
        len(timed_executed) == len(executed)
        and len(timed_reused) == len(reused)
    )
    sequential_comparison_valid = bool(
        timing_complete and len(observer_modes) <= 1
    )
    if not sequential_comparison_valid:
        reasons.add("sequential-comparison-invalid")

    omitted_subtotal = sum(
        baseline["duration_ms"] for baseline in timed_reused
    )
    if scope_complete and len(timed_reused) == len(reused):
        omitted_value: int | float | None = omitted_subtotal
        omitted_status = "estimated"
    elif timed_reused:
        omitted_value = omitted_subtotal
        omitted_status = "partial"
    else:
        omitted_value = None
        omitted_status = "unmeasured"

    executed_subtotal = sum(
        source["duration_ms"] for source in timed_executed
    )
    if scope_complete and len(timed_executed) == len(executed):
        executed_value: int | float | None = executed_subtotal
        executed_status = "measured"
    elif timed_executed:
        executed_value = executed_subtotal
        executed_status = "partial"
    else:
        executed_value = None
        executed_status = "unmeasured"

    comparison_complete = bool(
        scope_complete and timing_complete and sequential_comparison_valid
    )
    if comparison_complete:
        assert omitted_value is not None and executed_value is not None
        full_value: int | float | None = omitted_value + executed_value
        full_status = "estimated"
    else:
        full_value = None
        full_status = "unmeasured"

    if full_value is not None and full_value > 0:
        assert omitted_value is not None
        ratio: float | None = omitted_value / full_value
        ratio_status = "estimated"
    else:
        ratio = None
        ratio_status = "unmeasured"
        if full_value == 0:
            reasons.add("zero-denominator")

    value = {
        "version": REVALIDATION_SAVINGS_VERSION,
        "unit": REVALIDATION_SAVINGS_UNIT,
        "basis": REVALIDATION_SAVINGS_BASIS,
        "aggregation_scope": REVALIDATION_AGGREGATION_SCOPE,
        "omitted_test_execution_ms": omitted_value,
        "omitted_test_execution_status": omitted_status,
        "executed_test_execution_ms": executed_value,
        "executed_test_execution_status": executed_status,
        "full_sequential_test_execution_estimate_ms": full_value,
        "full_sequential_test_execution_estimate_status": full_status,
        "test_execution_reduction_ratio": ratio,
        "test_execution_reduction_status": ratio_status,
        "scope_complete": scope_complete,
        "timing_complete": timing_complete,
        "sequential_comparison_valid": sequential_comparison_valid,
        "coverage": {
            "requested_source_count": requested_count,
            "actual_executed_source_count": len(executed),
            "timed_executed_source_count": len(timed_executed),
            "actual_reused_source_count": len(reused),
            "timed_reused_source_count": len(timed_reused),
        },
        "reason_codes": sorted(reasons),
    }
    if not revalidation_savings_is_valid(value):
        raise ValueError("invalid revalidation-savings projection")
    return value


def retained_impact(batches: list[dict[str, Any]]) -> dict[str, Any]:
    """Read-only totals for retained, successfully completed group requests."""
    unique = {batch["batch_id"]: batch for batch in batches if batch_is_valid(batch)}
    completed = []
    for batch in unique.values():
        savings = revalidation_savings(batch)
        if savings["scope_complete"]:
            completed.append((batch, savings))
    reused = sum(item["coverage"]["actual_reused_source_count"] for _, item in completed)
    timed = sum(item["coverage"]["timed_reused_source_count"] for _, item in completed)
    return {
        "unit": "verification-group-request", "window": "retained-completed-history",
        "completed_request_count": len(completed),
        "reused_group_request_count": reused,
        "timed_reused_group_count": timed,
        "missing_timing_group_count": reused - timed,
        "avoided_execution_ms": (
            sum(item["omitted_test_execution_ms"] or 0 for _, item in completed)
            if completed and (timed or not reused) else None
        ),
        "timing_status": "unmeasured" if not completed or reused and not timed else "partial" if timed < reused else "estimated",
        "from_timestamp": min((batch["timestamp"] for batch, _ in completed), default=None),
        "through_timestamp": max((batch["finished_at"] for batch, _ in completed), default=None),
        "max_age_seconds": MAX_HISTORY_AGE_SECONDS, "max_events": MAX_HISTORY_EVENTS,
    }


RETAINED_REASON_LIMIT = 32
_REASON_CODE_TEXT = re.compile(r"^[a-z0-9-]{1,64}$")


def retained_reuse_reasons(batches: list[dict[str, Any]]) -> dict[str, Any]:
    """Read-only distribution of retained per-group plan decisions and reasons.

    This explains where reuse was lost across the retained history. It reports
    ledger facts only and grants nothing: a frequent reason is a place to look,
    not an instruction to weaken a binding.
    """
    unique = {batch["batch_id"]: batch for batch in batches if batch_is_valid(batch)}
    decisions = {decision: 0 for decision in sorted(DECISIONS)}
    reasons: dict[str, int] = {}
    request_count = 0
    for batch in unique.values():
        for source in batch["sources"]:
            decision = source.get("decision")
            reason = source.get("reason_code")
            if decision not in decisions or not isinstance(reason, str) or _REASON_CODE_TEXT.fullmatch(reason) is None:
                continue
            request_count += 1
            decisions[decision] += 1
            if decision not in REUSE_DECISIONS:
                reasons[reason] = reasons.get(reason, 0) + 1
    ranked = sorted(reasons.items(), key=lambda item: (-item[1], item[0]))[:RETAINED_REASON_LIMIT]
    return {
        "unit": "verification-group-request", "window": "retained-history",
        "request_count": request_count,
        "decisions": decisions,
        "run_reasons": [{"reason_code": code, "count": count} for code, count in ranked],
        "max_age_seconds": MAX_HISTORY_AGE_SECONDS, "max_events": MAX_HISTORY_EVENTS,
    }


def retained_reuse_reasons_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != set(retained_reuse_reasons([])):
        return False
    if value["unit"] != "verification-group-request" or value["window"] != "retained-history":
        return False
    decisions = value.get("decisions")
    if (
        not isinstance(decisions, dict) or set(decisions) != set(DECISIONS)
        or any(not _is_integer(count) for count in decisions.values())
        or not _is_integer(value.get("request_count"))
        or sum(decisions.values()) != value["request_count"]
    ):
        return False
    reasons = value.get("run_reasons")
    if (
        not isinstance(reasons, list) or len(reasons) > RETAINED_REASON_LIMIT
        or any(
            not isinstance(row, dict) or set(row) != {"reason_code", "count"}
            or not isinstance(row["reason_code"], str) or _REASON_CODE_TEXT.fullmatch(row["reason_code"]) is None
            or not _is_integer(row["count"], minimum=1)
            for row in reasons
        )
        or [row["reason_code"] for row in reasons] != [
            row["reason_code"] for row in sorted(reasons, key=lambda row: (-row["count"], row["reason_code"]))
        ]
        or len({row["reason_code"] for row in reasons}) != len(reasons)
        or sum(row["count"] for row in reasons) > sum(
            count for decision, count in decisions.items() if decision not in REUSE_DECISIONS
        )
    ):
        return False
    return value["max_age_seconds"] == MAX_HISTORY_AGE_SECONDS and value["max_events"] == MAX_HISTORY_EVENTS


def retained_impact_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != set(retained_impact([])):
        return False
    if value["unit"] != "verification-group-request" or value["window"] != "retained-completed-history":
        return False
    counts = ("completed_request_count", "reused_group_request_count", "timed_reused_group_count", "missing_timing_group_count", "max_age_seconds", "max_events")
    if any(not _is_integer(value[key]) for key in counts):
        return False
    if value["reused_group_request_count"] != value["timed_reused_group_count"] + value["missing_timing_group_count"]:
        return False
    expected_status = ("unmeasured" if not value["completed_request_count"] or value["reused_group_request_count"] and not value["timed_reused_group_count"] else "partial" if value["missing_timing_group_count"] else "estimated")
    if value["timing_status"] != expected_status:
        return False
    duration = value["avoided_execution_ms"]
    if (duration is None) != (expected_status == "unmeasured") or duration is not None and not is_duration(duration):
        return False
    if value["completed_request_count"] and not value["reused_group_request_count"] and duration != 0:
        return False
    start, end = value["from_timestamp"], value["through_timestamp"]
    return bool(
        value["max_age_seconds"] == MAX_HISTORY_AGE_SECONDS and value["max_events"] == MAX_HISTORY_EVENTS
        and (not value["completed_request_count"] and start is None and end is None and not value["reused_group_request_count"]
             or value["completed_request_count"] and _is_integer(start, minimum=1) and _is_integer(end, minimum=1) and start <= end)
    )


def revalidation_savings_is_valid(value: Any) -> bool:
    if (
        not isinstance(value, dict)
        or set(value) != REVALIDATION_SAVINGS_FIELDS
        or value.get("version") != REVALIDATION_SAVINGS_VERSION
        or isinstance(value.get("version"), bool)
        or value.get("unit") != REVALIDATION_SAVINGS_UNIT
        or value.get("basis") != REVALIDATION_SAVINGS_BASIS
        or value.get("aggregation_scope") != REVALIDATION_AGGREGATION_SCOPE
        or not all(
            isinstance(value.get(field), bool)
            for field in (
                "scope_complete",
                "timing_complete",
                "sequential_comparison_valid",
            )
        )
    ):
        return False
    coverage = value.get("coverage")
    if (
        not isinstance(coverage, dict)
        or set(coverage) != REVALIDATION_COVERAGE_FIELDS
        or any(
            item is not None and not _is_integer(item)
            for item in coverage.values()
        )
    ):
        return False
    reasons = value.get("reason_codes")
    if (
        not isinstance(reasons, list)
        or reasons != sorted(set(reasons))
        or any(reason not in REVALIDATION_REASON_CODES for reason in reasons)
    ):
        return False

    metric_pairs = (
        (
            value.get("omitted_test_execution_ms"),
            value.get("omitted_test_execution_status"),
            {"estimated", "partial", "unmeasured"},
        ),
        (
            value.get("executed_test_execution_ms"),
            value.get("executed_test_execution_status"),
            {"measured", "partial", "unmeasured"},
        ),
        (
            value.get("full_sequential_test_execution_estimate_ms"),
            value.get("full_sequential_test_execution_estimate_status"),
            {"estimated", "unmeasured"},
        ),
    )
    for metric, status, allowed in metric_pairs:
        if (
            status not in allowed
            or status not in REVALIDATION_METRIC_STATUSES
            or (status == "unmeasured") != (metric is None)
            or (metric is not None and not is_duration(metric))
        ):
            return False
    ratio = value.get("test_execution_reduction_ratio")
    ratio_status = value.get("test_execution_reduction_status")
    if (
        ratio_status not in {"estimated", "unmeasured"}
        or (ratio_status == "unmeasured") != (ratio is None)
        or (
            ratio is not None
            and (
                not isinstance(ratio, (int, float))
                or isinstance(ratio, bool)
                or not math.isfinite(ratio)
                or not 0 <= ratio <= 1
            )
        )
    ):
        return False

    requested = coverage["requested_source_count"]
    actual_executed = coverage["actual_executed_source_count"]
    timed_executed = coverage["timed_executed_source_count"]
    actual_reused = coverage["actual_reused_source_count"]
    timed_reused = coverage["timed_reused_source_count"]
    actual_counts = (actual_executed, timed_executed, actual_reused, timed_reused)
    if any(item is None for item in actual_counts):
        return bool(
            all(item is None for item in actual_counts)
            and not value["scope_complete"]
            and not value["timing_complete"]
            and not value["sequential_comparison_valid"]
            and all(metric is None for metric, _, _ in metric_pairs)
            and ratio is None
            and "request-not-finalized" in reasons
            and "scope-incomplete" in reasons
        )
    assert all(isinstance(item, int) for item in actual_counts)
    if (
        timed_executed > actual_executed
        or timed_reused > actual_reused
        or (
            requested is not None
            and actual_executed + actual_reused > requested
        )
        or value["timing_complete"]
        != (
            timed_executed == actual_executed
            and timed_reused == actual_reused
        )
    ):
        return False

    omitted = value["omitted_test_execution_ms"]
    executed_value = value["executed_test_execution_ms"]
    full = value["full_sequential_test_execution_estimate_ms"]
    comparison_complete = bool(
        value["scope_complete"]
        and value["timing_complete"]
        and value["sequential_comparison_valid"]
    )
    if value["scope_complete"] and (
        requested is None or actual_executed + actual_reused != requested
    ):
        return False
    if comparison_complete:
        if (
            omitted is None
            or executed_value is None
            or full != omitted + executed_value
            or value["full_sequential_test_execution_estimate_status"]
            != "estimated"
        ):
            return False
    elif full is not None:
        return False
    if full is not None and full > 0:
        if (
            ratio is None
            or omitted is None
            or ratio_status != "estimated"
            or not math.isclose(ratio, omitted / full, rel_tol=1e-12)
        ):
            return False
    elif ratio is not None:
        return False
    return bool((full != 0) == ("zero-denominator" not in reasons))


OUTPUT_RECORD_VERSION = 1
# Rough conversion used only for the disclosed token estimate; the byte count
# is the measured quantity.
OUTPUT_BYTES_PER_TOKEN_ESTIMATE = 4


def build_output_record(
    diagnostic_records: Any,
    *,
    source_key: str,
    check_digest: str,
    reporting: Any,
) -> dict[str, Any] | None:
    """Sum the bounded output one passing source produced in this batch."""
    if not isinstance(diagnostic_records, list) or not _DIGEST.fullmatch(str(check_digest)):
        return None
    total = 0
    truncated = False
    matched = 0
    for record in diagnostic_records:
        if not isinstance(record, dict) or record.get("source_key") != source_key:
            continue
        capture = record.get("capture")
        if not isinstance(capture, dict):
            continue
        stdout_bytes = capture.get("stdout_bytes")
        stderr_bytes = capture.get("stderr_bytes")
        if not _is_integer(stdout_bytes) or not _is_integer(stderr_bytes):
            continue
        matched += 1
        total += stdout_bytes + stderr_bytes
        truncated = truncated or capture.get("truncated") is True or capture.get("status") != "complete"
    if not matched:
        return None
    output_format = reporting.get("format") if isinstance(reporting, dict) else None
    return {
        "version": OUTPUT_RECORD_VERSION,
        "check_digest": str(check_digest),
        "bytes": total,
        "lower_bound": truncated,
        "format": output_format if output_format in {"raw", "actionable"} else "raw",
    }


def output_record_is_valid(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and set(value) == {"version", "check_digest", "bytes", "lower_bound", "format"}
        and value.get("version") == OUTPUT_RECORD_VERSION
        and isinstance(value.get("check_digest"), str)
        and _DIGEST.fullmatch(value["check_digest"])
        and _is_integer(value.get("bytes"))
        and isinstance(value.get("lower_bound"), bool)
        and value.get("format") in {"raw", "actionable"}
    )


def avoided_output(batch: Any, sources: Any) -> dict[str, Any]:
    """Estimate the output that reused checks did not produce for the host.

    Each reused source contributes the bounded output of its last recorded
    pass of the same check. The figure describes host reading avoided by a
    reuse that already happened; it grants nothing and is never a savings claim
    for wall-clock time or model cost beyond the disclosed byte count.
    """
    unmeasured = {
        "bytes": None, "estimated_tokens": None, "status": "unmeasured",
        "reused_source_count": 0, "measured_source_count": 0, "lower_bound": False,
        "bytes_per_token": OUTPUT_BYTES_PER_TOKEN_ESTIMATE,
    }
    if not isinstance(batch, dict) or not isinstance(sources, dict) or not batch_is_valid(batch):
        return unmeasured
    reused = [item for item in batch["sources"] if item["status"] == "reused"]
    if not reused:
        return unmeasured
    total = 0
    measured = 0
    lower_bound = False
    for item in reused:
        source = sources.get(item["source_key"])
        record = source.get("last_success_output") if isinstance(source, dict) else None
        if not output_record_is_valid(record) or record["check_digest"] != item["check_digest"]:
            continue
        measured += 1
        total += record["bytes"]
        lower_bound = lower_bound or record["lower_bound"]
    if not measured:
        return {**unmeasured, "reused_source_count": len(reused)}
    return {
        "bytes": total,
        "estimated_tokens": total // OUTPUT_BYTES_PER_TOKEN_ESTIMATE,
        "status": "estimated" if measured == len(reused) else "partial",
        "reused_source_count": len(reused),
        "measured_source_count": measured,
        "lower_bound": lower_bound,
        "bytes_per_token": OUTPUT_BYTES_PER_TOKEN_ESTIMATE,
    }


def avoided_output_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != set(avoided_output(None, None)):
        return False
    if value["bytes_per_token"] != OUTPUT_BYTES_PER_TOKEN_ESTIMATE or value["status"] not in {"unmeasured", "partial", "estimated"}:
        return False
    if not _is_integer(value["reused_source_count"]) or not _is_integer(value["measured_source_count"]):
        return False
    if value["measured_source_count"] > value["reused_source_count"] or not isinstance(value["lower_bound"], bool):
        return False
    if value["status"] == "unmeasured":
        return value["bytes"] is None and value["estimated_tokens"] is None and value["measured_source_count"] == 0
    return bool(
        _is_integer(value["bytes"]) and value["estimated_tokens"] == value["bytes"] // OUTPUT_BYTES_PER_TOKEN_ESTIMATE
        and value["measured_source_count"] > 0
        and (value["status"] == "estimated") == (value["measured_source_count"] == value["reused_source_count"])
    )


def _host_output(value: dict[str, Any]) -> str:
    if value["status"] == "unmeasured":
        return ""
    kilobytes = value["bytes"] / 1024
    size = f"{kilobytes:.1f}".rstrip("0").rstrip(".") + " KB" if value["bytes"] >= 1024 else f"{value['bytes']} B"
    qualifier = "이상 " if value["lower_bound"] else ""
    partial = "" if value["status"] == "estimated" else f" · 표본 {value['measured_source_count']}/{value['reused_source_count']}"
    return (
        f"; 재사용으로 다시 읽지 않은 출력: {qualifier}{size} (약 {value['estimated_tokens']:,} 토큰, 추정{partial})"
    )


def _host_duration(value: Any, *, estimated: bool = False) -> str:
    if value is None or not is_duration(value):
        return "측정 정보 없음"
    numeric = float(value)
    if numeric < 1000:
        rendered = f"{numeric:.2f}".rstrip("0").rstrip(".") + " ms"
    elif numeric < 60_000:
        rendered = f"{numeric / 1000:.2f}".rstrip("0").rstrip(".") + "초"
    else:
        rounded_seconds = round(numeric / 1000)
        minutes, seconds = divmod(rounded_seconds, 60)
        rendered = f"{minutes}분 {seconds}초" if seconds else f"{minutes}분"
    return f"약 {rendered}" if estimated else rendered


def host_summary(verification: Any, sources: Any = None) -> str:
    batch = current_batch(verification)
    if batch is None:
        return ""
    output = _host_output(avoided_output(batch, sources)) if isinstance(sources, dict) else ""
    summary = batch_summary(batch)
    savings = revalidation_savings(batch)
    prior = sum(item["status"] == "reused" and item.get("reuse_origin") is not None for item in batch["sources"])
    omitted = savings["omitted_test_execution_ms"]
    executed = savings["executed_test_execution_ms"]
    full = savings["full_sequential_test_execution_estimate_ms"]
    ratio = savings["test_execution_reduction_ratio"]

    requested = summary["total_source_count"]
    executed_count = summary["executed_source_count"]
    reused_count = summary["authoritative_reuse_count"]
    timed_reused = savings["coverage"]["timed_reused_source_count"]
    actual_reused = savings["coverage"]["actual_reused_source_count"]
    complete = bool(batch["status"] == "passed" and savings["scope_complete"])
    if not complete:
        headline = (
            f"{requested}개 샤드 요청 미완료 · 실제 실행 {executed_count}개 · "
            f"재사용 적용 {reused_count}개"
        )
        omitted_text = "요청 미완료"
    elif reused_count == 0:
        headline = f"{requested}개 샤드 모두 실제 실행 · 실제 재사용 없음"
        omitted_text = "0 ms · 실제 재사용 없음"
    elif savings["omitted_test_execution_status"] == "estimated":
        omitted_text = _host_duration(omitted, estimated=True)
        if executed_count == 0:
            headline = (
                f"{requested}개 중 실제 실행 0개 · {reused_count}개 모두 재사용으로 "
                f"{omitted_text}의 테스트 재실행 생략〔추정〕"
            )
        else:
            headline = (
                f"{requested}개 중 {executed_count}개만 실행 · {reused_count}개 재사용으로 "
                f"{omitted_text}의 테스트 재실행 생략〔추정〕"
            )
    elif savings["omitted_test_execution_status"] == "partial":
        omitted_text = f"부분 추정 합계 약 {_host_duration(omitted)}〔추정〕"
        headline = (
            f"{requested}개 중 {executed_count}개 실행 · {reused_count}개 재사용 · "
            f"시간 표본 {timed_reused}/{actual_reused}개 · {omitted_text}"
        )
    else:
        omitted_text = "측정 정보 없음"
        headline = (
            f"{requested}개 중 {executed_count}개 실행 · {reused_count}개 재사용 · "
            "생략 시간은 미측정"
        )

    full_text = _host_duration(full, estimated=True)
    executed_text = _host_duration(executed)
    reduction_text = (
        "측정 정보 없음"
        if ratio is None
        else f"약 {100 * ratio:.2f}".rstrip("0").rstrip(".") + "%"
    )
    conditional_count = sum(item["status"] == "reused" and item.get("authority_source") in CONDITIONAL_AUTHORITY_SOURCES
                            for item in batch["sources"])
    limitation = (f" 조건부 재사용 {conditional_count}개: 관찰 범위 기반 / 입력 완전성 미보증."
                  if conditional_count else "")
    return (
        f"[Click 결과] {headline}; "
        f"재사용으로 생략한 테스트 실행시간: {omitted_text}; "
        f"동일 샤드 전체 순차 실행 예상: {full_text}; "
        f"이번 테스트 실행: {executed_text}; "
        f"테스트 실행시간 감소: {reduction_text}; "
        f"시간 근거 커버리지 {timed_reused}/{actual_reused} · "
        f"이전 계약 재판정 {prior}개; "
        "과거 성공 실행 기록 기반 추정 / 동일 샤드 순차 기준 / Click 관리비용 제외."
        + limitation
    ) + output





def build_plan(
    decisions: Iterable[dict[str, Any]],
    *,
    current_revision: int,
    planned_at: int | None = None,
) -> dict[str, Any]:
    """Build a canonical plan from already-authorized source decisions."""
    normalized = sorted(
        (dict(item) for item in decisions), key=lambda item: item.get("source_key", "")
    )
    if (
        not normalized
        or not _is_integer(current_revision)
        or any(not decision_is_valid(item) for item in normalized)
        or any(item["current_revision"] != current_revision for item in normalized)
        or len({item["source_key"] for item in normalized}) != len(normalized)
    ):
        raise ValueError("invalid incremental-verification plan input")
    counts = {
        selected: sum(item["decision"] == selected for item in normalized)
        for selected in DECISIONS
    }
    timestamp = int(time.time()) if planned_at is None else planned_at
    plan = {
        "version": PLAN_VERSION,
        "planned_at": timestamp,
        "current_revision": current_revision,
        "total_source_count": len(normalized),
        "planned_execution_source_count": counts["run"] + counts["not-evaluable"],
        "planned_reuse_source_count": sum(
            counts[selected] for selected in REUSE_DECISIONS
        ),
        "decisions": normalized,
    }
    if not plan_is_valid(plan):
        raise ValueError("invalid incremental-verification plan")
    return plan


def _legacy_plan_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _LEGACY_PLAN_FIELDS:
        return False
    decisions = value.get("decisions")
    if (
        value.get("version") != 1
        or not _is_integer(value.get("planned_at"), minimum=1)
        or not _is_integer(value.get("current_revision"))
        or not isinstance(decisions, list)
        or not decisions
        or any(not decision_is_valid(item) for item in decisions)
        or any(not _is_integer(item.get("estimated_avoided_ms")) for item in decisions)
        or decisions != sorted(decisions, key=lambda item: item["source_key"])
        or len({item["source_key"] for item in decisions}) != len(decisions)
        or any(
            item["current_revision"] != value["current_revision"]
            for item in decisions
        )
    ):
        return False
    counts = {
        selected: sum(item["decision"] == selected for item in decisions)
        for selected in DECISIONS
    }
    return bool(
        value.get("total_source_count") == len(decisions)
        and value.get("executed_source_count")
        == counts["run"] + counts["not-evaluable"]
        and value.get("authoritative_reuse_count")
        == sum(counts[selected] for selected in REUSE_DECISIONS)
        and value.get("exact_reuse_count") == counts["reuse-exact"]
        and value.get("dependency_reuse_count") == counts["reuse-dependency"]
        and value.get("safe_change_reuse_count") == counts["reuse-safe-change"]
        and value.get("estimated_avoided_ms")
        == sum(
            item["estimated_avoided_ms"]
            for item in decisions
            if item["decision"] in REUSE_DECISIONS
        )
        and all(
            _is_integer(value.get(field))
            for field in (
                "total_source_count",
                "executed_source_count",
                "authoritative_reuse_count",
                "exact_reuse_count",
                "dependency_reuse_count",
                "safe_change_reuse_count",
                "estimated_avoided_ms",
                "executed_duration_ms",
            )
        )
    )


def plan_is_valid(value: Any) -> bool:
    if isinstance(value, dict) and value.get("version") == 1:
        return _legacy_plan_is_valid(value)
    if not isinstance(value, dict) or set(value) != _PLAN_FIELDS:
        return False
    items = value.get("decisions")
    if (
        value.get("version") != PLAN_VERSION
        or not _is_integer(value.get("planned_at"), minimum=1)
        or not _is_integer(value.get("current_revision"))
        or not isinstance(items, list) or not items
        or any(not decision_is_valid(item) for item in items)
        or items != sorted(items, key=lambda item: item["source_key"])
        or len({item["source_key"] for item in items}) != len(items)
        or any(item["current_revision"] != value["current_revision"] for item in items)
    ):
        return False
    runs = sum(item["decision"] not in REUSE_DECISIONS for item in items)
    return bool(
        value.get("total_source_count") == len(items)
        and value.get("planned_execution_source_count") == runs
        and value.get("planned_reuse_source_count") == len(items) - runs
        and all(_is_integer(value.get(key)) for key in (
            "total_source_count", "planned_execution_source_count", "planned_reuse_source_count"
        ))
    )


def keys_to_execute(plan: Any) -> set[str]:
    """Return the sources retained in the real runner batch."""
    if not plan_is_valid(plan):
        raise ValueError("invalid incremental-verification plan")
    return {
        item["source_key"]
        for item in plan["decisions"]
        if item["decision"] not in REUSE_DECISIONS
    }


def store_plan(verification: dict[str, Any], plan: dict[str, Any]) -> None:
    if not plan_is_valid(plan):
        raise ValueError("invalid incremental-verification plan")
    # JSON round-tripping prevents callers from retaining mutable aliases.
    verification[PLAN_FIELD] = json.loads(
        json.dumps(plan, sort_keys=True, separators=(",", ":"))
    )


def record_execution(
    verification: dict[str, Any], source_durations_ms: dict[str, Any], *,
    source_results: dict[str, dict[str, Any]] | None = None,
    reused_keys: Iterable[str] = (), exit_code: int | None = None,
    invalidated_reuse_keys: Iterable[str] = (),
    runner_duration_ms: float | None = None, workspace_changed: bool = False,
) -> bool:
    """Record witnessed outcomes, never derive executions from a plan."""
    batch = current_batch(verification)
    if batch is None or source_results is None or exit_code is None:
        return False
    if batch["status"] not in {"planned", "running"}:
        return True  # A delivered final result is idempotent.
    any_started = any(
        isinstance(item, dict) and item.get("started") is True
        for item in source_results.values()
    )
    if not any_started and exit_code != 0:
        admission_only = batch.get("version") != 5
        if batch.get("version") == 5:
            admission_only = True
            for source in batch["sources"]:
                plans = [
                    {field: command[field] for field in _COMMAND_PLAN_FIELDS}
                    for command in source["commands"]
                ]
                result = source_results.get(source["source_key"])
                folded = source_command_outcome(result, plans)
                commands = (
                    result.get("commands") if isinstance(result, dict) else None
                )
                if not (
                    folded is not None
                    and folded["valid"]
                    and folded["status"] == "planned"
                    and command_outcomes_match_plans(
                        commands, plans, require_positions=True
                    )
                ):
                    admission_only = False
                    break
        if admission_only:
            return reject_batch(
                verification,
                reason="runner-admission-rejected",
                runner_duration_ms=runner_duration_ms,
            )
    reused = set(reused_keys) if not workspace_changed else set()
    invalidated_reuse = set(invalidated_reuse_keys)
    reused.difference_update(invalidated_reuse)
    for source in batch["sources"]:
        key = source["source_key"]
        result = source_results.get(key)
        if source["completed"]:
            # A source-level completion was already persisted under the claimed
            # runner. The final batch fold must not roll that fact backward.
            if key in source_durations_ms and is_duration(source_durations_ms[key]):
                source["duration_ms"] = source_durations_ms[key]
            if batch.get("version") == 5 and isinstance(result, dict):
                plans = [
                    {field: command[field] for field in _COMMAND_PLAN_FIELDS}
                    for command in source["commands"]
                ]
                commands = result.get("commands")
                if command_outcomes_match_plans(
                    commands, plans, require_positions=True
                ):
                    source["commands"] = json.loads(json.dumps(commands))
            continue
        if result is not None:
            if batch.get("version") == 5:
                plans = [
                    {field: command[field] for field in _COMMAND_PLAN_FIELDS}
                    for command in source["commands"]
                ]
                commands = result.get("commands") if isinstance(result, dict) else None
                folded = source_command_outcome(result, plans)
                if (
                    folded is not None
                    and folded["valid"]
                    and command_outcomes_match_plans(
                        commands, plans, require_positions=True
                    )
                ):
                    if folded["status"] == "planned":
                        source.update(
                            status="not-run",
                            started=False,
                            completed=False,
                            execution_reason_code=(
                                "workspace-invalidated" if workspace_changed
                                else "preceding-check-stopped"
                            ),
                            duration_ms=None,
                        )
                        _mark_unstarted_commands(
                            source, source["execution_reason_code"]
                        )
                    else:
                        source.update(
                            status=folded["status"],
                            started=folded["started"],
                            completed=folded["completed"],
                            execution_reason_code=result["reason_code"],
                            duration_ms=(
                                source_durations_ms.get(key)
                                if folded["started"] else None
                            ),
                            commands=json.loads(json.dumps(commands)),
                        )
                else:
                    _mark_source_outcome_unknown(source)
            else:
                source.update({
                    "status": result["status"], "started": result["started"],
                    "completed": result["completed"],
                    "execution_reason_code": result["reason_code"],
                    "duration_ms": source_durations_ms.get(key),
                })
        elif (
            batch.get("version") == 5
            and any(command["started"] for command in source["commands"])
        ):
            _mark_source_outcome_unknown(source)
        elif key in reused and source["decision"] in REUSE_DECISIONS:
            source.update(status="reused", execution_reason_code="reuse-applied")
            _mark_unstarted_commands(source, "reuse-applied")
        else:
            reason = (
                "workspace-invalidated" if workspace_changed
                else "observed-input-changed" if key in invalidated_reuse
                else "preceding-check-stopped"
            )
            source.update(status="not-run", execution_reason_code=reason)
            _mark_unstarted_commands(source, reason)
    source_statuses = {source["status"] for source in batch["sources"]}
    batch["status"] = (
        "interrupted"
        if exit_code == 130 or "interrupted" in source_statuses
        else "passed"
        if exit_code == 0 and source_statuses <= {"passed", "reused"}
        else "failed"
    )
    batch["reason_code"] = "workspace-invalidated" if workspace_changed else "batch-finished"
    batch["finished_at"] = int(time.time())
    batch["runner_duration_ms"] = runner_duration_ms
    batch["measurement_scope"] = "prepare-and-runner-segments"
    complete_request_timing(verification, batch)
    return store_batch(verification, batch)


def _history_event_is_valid(value: Any) -> bool:
    if isinstance(value, dict) and value.get("event") == BATCH_EVENT:
        return batch_is_valid(value)
    return bool(
        isinstance(value, dict)
        and set(value) == _HISTORY_FIELDS
        and value.get("event") == HISTORY_EVENT
        and isinstance(value.get("source_key"), str)
        and _DIGEST.fullmatch(value["source_key"]) is not None
        and value.get("decision") in DECISIONS
        and value.get("reason") in REASON_CODES
        and _is_integer(value.get("current_revision"))
        and _is_integer(value.get("previous_revision"), minimum=-1)
        and (value.get("estimated_avoided_ms") is None or is_duration(value["estimated_avoided_ms"]))
        and _is_integer(value.get("timestamp"), minimum=1)
    )


def history_is_valid(value: Any) -> bool:
    return bool(
        isinstance(value, list)
        and len(value) <= MAX_HISTORY_EVENTS
        and all(_history_event_is_valid(event) for event in value)
        and value == sorted(value, key=lambda event: event["timestamp"])
        and len(_canonical_bytes(value)) <= MAX_HISTORY_BYTES
    )




def prune_history(
    events: Iterable[dict[str, Any]],
    *,
    now: int,
    max_events: int = MAX_HISTORY_EVENTS,
    max_age_seconds: int = MAX_HISTORY_AGE_SECONDS,
    max_bytes: int = MAX_HISTORY_BYTES,
) -> list[dict[str, Any]]:
    """Apply age, count, and encoded-size caps by dropping oldest events."""
    if not all(
        _is_integer(value, minimum=1)
        for value in (now, max_events, max_age_seconds, max_bytes)
    ):
        raise ValueError("invalid incremental history bounds")
    cutoff = max(1, now - max_age_seconds)
    retained = sorted(
        (event for event in events
         if _history_event_is_valid(event) and event["timestamp"] >= cutoff),
        key=lambda event: event["timestamp"],
    )[-max_events:]
    # JSON list size is brackets + records + separating commas. Encode each
    # retained candidate once, then discard oldest records in linear time.
    # Key order does not affect encoded size; preserving it also keeps the
    # existing detached-copy behavior for callers that mutate returned records.
    encoded = [json.dumps(event, separators=(",", ":"), ensure_ascii=True).encode()
               for event in retained]
    size = 2 + sum(map(len, encoded)) + max(0, len(encoded) - 1)
    start = 0
    while start < len(encoded) and size > max_bytes:
        size -= len(encoded[start]) + (1 if start + 1 < len(encoded) else 0)
        start += 1
    return [json.loads(record) for record in encoded[start:]]


def append_plan_history(
    verification: dict[str, Any], plan: dict[str, Any]
) -> None:
    """Persist bounded, content-free planning events for one canonical plan."""
    if not plan_is_valid(plan):
        raise ValueError("invalid incremental-verification plan")
    existing = verification.get(HISTORY_FIELD, [])
    if not isinstance(existing, list):
        existing = []
    timestamp = plan["planned_at"]
    additions = [
        {
            "event": HISTORY_EVENT,
            "source_key": item["source_key"],
            "decision": item["decision"],
            "reason": item["reason_code"],
            "current_revision": item["current_revision"],
            "previous_revision": item["previous_revision"],
            "estimated_avoided_ms": item["estimated_avoided_ms"],
            "timestamp": timestamp,
        }
        for item in plan["decisions"]
    ]
    verification[HISTORY_FIELD] = prune_history(
        [*existing, *additions], now=timestamp
    )


def current_history(verification: Any) -> list[dict[str, Any]]:
    value = verification.get(HISTORY_FIELD) if isinstance(verification, dict) else None
    if not history_is_valid(value):
        return []
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))


def current_plan(verification: Any) -> dict[str, Any] | None:
    value = verification.get(PLAN_FIELD) if isinstance(verification, dict) else None
    return (
        json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))
        if plan_is_valid(value)
        else None
    )


def summary(verification: Any) -> dict[str, Any]:
    """Project actual outcomes only; old planning-only records have no actuals."""
    batch = current_batch(verification)
    if batch is not None:
        return batch_summary(batch)
    plan = current_plan(verification)
    value = {key: None for key in SUMMARY_FIELDS}
    value["total_source_count"] = plan["total_source_count"] if plan else None
    value["planned_execution_source_count"] = len(keys_to_execute(plan)) if plan else None
    value["planned_reuse_source_count"] = len(plan["decisions"]) - len(keys_to_execute(plan)) if plan else None
    return value


def progress_projection(
    state: Any,
    evidence_sources: Any,
    *,
    successor_candidates: Any = None,
    generated_at: int | None = None,
) -> dict[str, Any]:
    """Return a compact, read-only view of current verification progress.

    The caller supplies the already-validated active registry and optional
    successor candidates.  This view reports ledger and batch facts only; it
    cannot make a receipt reusable, complete a task, or transfer runner or
    approval authority.
    """
    raw_state = state if isinstance(state, dict) else {}
    verification = raw_state.get("verification")
    verification = verification if isinstance(verification, dict) else {}
    active_sources = evidence_sources if isinstance(evidence_sources, dict) else {}
    candidate_sources = (
        successor_candidates if isinstance(successor_candidates, dict) else {}
    )
    revision = verification.get("mutation_revision", 0)
    if not _is_integer(revision):
        revision = 0

    runtime_mode = raw_state.get("runtime_mode")
    if runtime_mode not in {"evidence", "guarded"}:
        runtime_mode = "unknown"
    runtime_status = raw_state.get("status")
    if not isinstance(runtime_status, str) or re.fullmatch(
        r"[a-z0-9-]{1,32}", runtime_status
    ) is None:
        runtime_status = "unknown"
    approval_bound = bool(
        runtime_mode == "guarded"
        and runtime_status == "approved"
        and raw_state.get("approved_turn_id")
        and raw_state.get("approved_turn_id") != raw_state.get("staged_turn_id")
    )
    execution_authority = (
        "host"
        if runtime_mode == "evidence"
        else "approved-contract"
        if approval_bound
        else "none"
    )

    presentation = sanitize_presentation(raw_state.get("presentation"))
    candidate_batch = current_batch(verification)
    task_identity = str(
        raw_state.get(
            "contract_id" if runtime_mode == "guarded" else "evidence_session_id",
            "",
        )
    )
    batch_task = (
        candidate_batch.get("task") if isinstance(candidate_batch, dict) else None
    )
    batch = (
        candidate_batch
        if isinstance(candidate_batch, dict)
        and candidate_batch.get("current_revision") == revision
        and (
            not isinstance(batch_task, dict)
            or batch_task.get("id") == task_identity
        )
        else None
    )
    actual = {
        item["source_key"]: item
        for item in (batch or {}).get("sources", [])
        if isinstance(item, dict) and isinstance(item.get("source_key"), str)
    }
    checks: list[dict[str, Any]] = []

    active_valid_sources = [
        (key, source)
        for key, source in active_sources.items()
        if isinstance(key, str)
        and _DIGEST.fullmatch(key)
        and isinstance(source, dict)
        and source.get("kind") in {"argv", "browser", "hosted", "manual", "existing"}
        and source.get("status")
        in {"ready", "running", "observed", "passed", "failed", "stale"}
    ]
    active_keys = {key for key, _ in active_valid_sources}
    candidate_valid_sources = [
        (key, source)
        for key, source in candidate_sources.items()
        if key not in active_keys
        and isinstance(key, str)
        and _DIGEST.fullmatch(key)
        and isinstance(source, dict)
        and source.get("kind") in {"argv", "browser", "hosted", "manual", "existing"}
        and source.get("status") == "passed"
        and isinstance(source.get("verified_revision"), int)
        and not isinstance(source.get("verified_revision"), bool)
        and source.get("verified_revision", -1) >= 0
    ]
    source_origins = {
        **{key: "successor-candidate" for key, _ in candidate_valid_sources},
        **{key: "active" for key, _ in active_valid_sources},
    }
    valid_sources = [*active_valid_sources, *candidate_valid_sources]
    source_keys = {key for key, _ in valid_sources}
    valid_count = sum(
        source.get("status") == "passed"
        and source.get("verified_revision") == revision
        for _, source in active_valid_sources
    )
    invalidated_count = sum(
        source_origins[key] == "successor-candidate"
        or source.get("status") == "stale"
        or (
            isinstance(source.get("verified_revision"), int)
            and not isinstance(source.get("verified_revision"), bool)
            and 0 <= source["verified_revision"] < revision
        )
        for key, source in valid_sources
    )
    batch_results = [
        result for key, result in actual.items() if key in source_keys
    ]
    actual_execution_count = sum(
        result.get("started") is True
        and result.get("status") != "reused"
        for result in batch_results
    )
    reused_count = sum(
        result.get("status") == "reused" for result in batch_results
    )
    not_run_count = sum(
        result.get("status") == "not-run" for result in batch_results
    )
    pending_count = sum(
        result.get("status")
        in {"planned", "running", "reuse-pending", "unknown"}
        and result.get("started") is not True
        for result in batch_results
    )
    not_requested_count = len(source_keys - set(actual))
    for index, (key, source) in enumerate(
        sorted(valid_sources, key=lambda item: item[0])[:MAX_PROGRESS_CHECKS],
        start=1,
    ):
        current = bool(
            source_origins[key] == "active"
            and source.get("status") == "passed"
            and source.get("verified_revision") == revision
        )
        if current:
            current_state = "valid"
        elif source_origins[key] == "successor-candidate" or source.get("status") == "stale" or (
            isinstance(source.get("verified_revision"), int)
            and not isinstance(source.get("verified_revision"), bool)
            and 0 <= source["verified_revision"] < revision
        ):
            current_state = "invalidated"
        else:
            current_state = "remaining"

        result = actual.get(key)
        if result is None:
            execution_status = "not-requested"
            outcome_status = str(source.get("status", "unknown"))
            decision = "not-planned"
            reason_code = (
                "successor-requalification-required"
                if source_origins[key] == "successor-candidate"
                else "mutation-invalidated"
                if current_state == "invalidated"
                else "current"
                if current_state == "valid"
                else "not-verified"
            )
            authority_source = "none"
            reuse_origin = ""
        else:
            outcome_status = str(result.get("status", "unknown"))
            if outcome_status == "reused":
                execution_status = "reused"
            elif result.get("started") is True:
                execution_status = "executed"
            elif outcome_status == "not-run":
                execution_status = "not-run"
            else:
                execution_status = "pending"
            decision = str(result.get("decision", "not-planned"))
            reason_code = str(
                result.get("execution_reason_code")
                or result.get("reason_code")
                or "outcome-unconfirmed"
            )
            authority_source = str(result.get("authority_source", "none"))
            origin = result.get("reuse_origin")
            reuse_origin = (
                str(origin.get("kind", "")) if isinstance(origin, dict) else ""
            )

        default_label = f"{source.get('kind', 'verification')} 확인 {index}"
        label = (
            safe_label(result.get("label"), default_label)
            if isinstance(result, dict)
            else presentation["evidence_labels"].get(key, default_label)
        )
        checks.append(
            {
                "id": f"source:{key[:16]}",
                "label": label,
                "kind": str(source.get("kind")),
                "source_origin": source_origins[key],
                "current_state": current_state,
                "ledger_status": str(source.get("status")),
                "execution_status": execution_status,
                "outcome_status": outcome_status,
                "decision": decision,
                "reason_code": reason_code,
                "authority_source": authority_source,
                "reuse_origin": reuse_origin,
            }
        )

    registered_count = len(active_valid_sources)
    candidate_count = len(candidate_valid_sources)
    tracked_count = len(valid_sources)
    remaining_count = tracked_count - valid_count
    verification_completion = (
        "no-checks"
        if tracked_count == 0
        else "complete"
        if remaining_count == 0
        else "remaining"
    )
    batch_metrics = batch_summary(batch) if batch is not None else None
    return {
        "version": PROGRESS_VERSION,
        "mode": PROGRESS_MODE,
        "generated_at": max(
            1, int(time.time()) if generated_at is None else generated_at
        ),
        "task": {
            "runtime_mode": runtime_mode,
            "status": runtime_status,
            "mutation_revision": revision,
            "verification_status": str(verification.get("status", "unavailable")),
            "execution_authority": execution_authority,
            "approval_bound": approval_bound,
            "verification_completion": verification_completion,
        },
        "summary": {
            "registered_check_count": registered_count,
            "candidate_check_count": candidate_count,
            "tracked_check_count": tracked_count,
            "visible_check_count": len(checks),
            "truncated_check_count": max(0, tracked_count - len(checks)),
            "valid_check_count": valid_count,
            "invalidated_check_count": invalidated_count,
            "remaining_check_count": remaining_count,
            "actual_execution_count": actual_execution_count,
            "reused_check_count": reused_count,
            "not_run_check_count": not_run_count,
            "pending_check_count": pending_count,
            "not_requested_check_count": not_requested_count,
        },
        "batch": ({
            "status": str(batch["status"]),
            "current_revision": int(batch["current_revision"]),
            "requested_check_count": batch["requested_source_count"],
            **{
                key: batch_metrics[key]
                for key in (
                    "executed_duration_ms",
                    "request_wall_ms",
                    "measured_processing_ms",
                    "estimated_avoided_ms",
                )
            },
            "avoided_output": avoided_output(batch, active_sources),
        } if batch is not None else None),
        "checks": checks,
    }
