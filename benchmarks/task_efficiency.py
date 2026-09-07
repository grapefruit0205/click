#!/usr/bin/env python3
"""Whole-task paired evaluation and token-ratio-only public projection.

Raw host usage remains in the input/internal result. Public output is built from
an explicit allowlist and never contains absolute token counts or usage events.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
import time
from typing import Any, Iterable


INTERNAL_VERSION = 1
PUBLIC_VERSION = 1
INTERNAL_KIND = "click-task-efficiency-evaluation"
PUBLIC_KIND = "click-task-efficiency-public"
VARIANTS = frozenset({"N", "B0", "B1", "B2"})
RUN_KINDS = frozenset({"first-use", "prepared-repeat"})
RUNTIME_MODES = frozenset({"evidence", "guarded"})
COMPLETION_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "incomplete"}
)
ACTIVITY_KINDS = frozenset(
    {"implementation", "verification", "click-management", "user-intervention", "unclassified"}
)
INTERVENTION_KINDS = (
    "additional_approval", "question_response", "status_check", "configuration_recovery"
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "baseline_total_tokens", "improved_total_tokens", "saved_tokens",
        "input_tokens", "output_tokens", "cached_input_tokens",
        "reasoning_output_tokens", "usage", "usage_events", "events",
    }
)


def _number(value: Any, *, positive: bool = False) -> float | None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or positive and float(value) <= 0
        or not positive and float(value) < 0
    ):
        return None
    return float(value)


def _integer(value: Any, *, minimum: int = 0) -> int | None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
    ):
        return None
    return value


def _safe_text(value: Any, limit: int = 240) -> str:
    text = "".join(
        character for character in str(value)
        if character in "\t " or ord(character) >= 32
    ).strip()
    return text[:limit]


def _dedupe_usage_events(
    events: Any,
) -> tuple[list[dict[str, Any]] | None, str]:
    if not isinstance(events, list) or not events:
        return None, "usage-events-missing"
    by_id: dict[str, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for raw in events:
        if not isinstance(raw, dict):
            return None, "usage-event-invalid"
        event_id = raw.get("event_id")
        sequence = _integer(raw.get("sequence"))
        input_tokens = _integer(raw.get("input_tokens"))
        output_tokens = _integer(raw.get("output_tokens"))
        cached = _integer(raw.get("cached_input_tokens", 0))
        reasoning = _integer(raw.get("reasoning_output_tokens", 0))
        if (
            not isinstance(event_id, str)
            or _SAFE_ID.fullmatch(event_id) is None
            or sequence is None
            or input_tokens is None
            or output_tokens is None
            or cached is None
            or reasoning is None
            or cached > input_tokens
            or reasoning > output_tokens
        ):
            return None, "usage-event-invalid"
        normalized = {
            "event_id": event_id,
            "response_id": (
                raw.get("response_id")
                if isinstance(raw.get("response_id"), str)
                and _SAFE_ID.fullmatch(raw["response_id"])
                else None
            ),
            "sequence": sequence,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_input_tokens": cached,
            "reasoning_output_tokens": reasoning,
        }
        previous = by_id.get(event_id)
        if previous is not None:
            if previous != normalized:
                return None, "usage-duplicate-conflict"
            continue
        by_id[event_id] = normalized
        ordered.append(normalized)
    ordered.sort(key=lambda event: (event["sequence"], event["event_id"]))
    return ordered, ""


def usage_total(usage: Any) -> tuple[int | None, str, int | None]:
    """Return complete task total and causally identified response count."""

    if not isinstance(usage, dict):
        return None, "usage-missing", None
    if usage.get("version") != 1:
        return None, "usage-version-incompatible", None
    if usage.get("scope_status") != "complete":
        return None, "usage-scope-incomplete", None
    if usage.get("covers_task_boundary") is not True:
        return None, "usage-task-boundary-incomplete", None
    if usage.get("input_semantics") != "includes-cached":
        return None, "usage-input-semantics-unknown", None
    if usage.get("output_semantics") != "includes-reasoning":
        return None, "usage-output-semantics-unknown", None
    semantics = usage.get("counter_semantics")
    if semantics not in {"incremental", "cumulative"}:
        return None, "usage-counter-semantics-unknown", None
    events, error = _dedupe_usage_events(usage.get("events"))
    if error or events is None:
        return None, error, None
    if semantics == "incremental":
        total = sum(event["input_tokens"] + event["output_tokens"] for event in events)
    else:
        previous_input = previous_output = -1
        for event in events:
            if (
                event["input_tokens"] < previous_input
                or event["output_tokens"] < previous_output
            ):
                return None, "usage-cumulative-counter-regressed", None
            previous_input = event["input_tokens"]
            previous_output = event["output_tokens"]
        total = events[-1]["input_tokens"] + events[-1]["output_tokens"]
    response_ids = [event["response_id"] for event in events]
    round_trips = (
        len(set(response_ids))
        if response_ids and all(isinstance(value, str) for value in response_ids)
        else None
    )
    return total, "", round_trips


def _task_elapsed(run: Any) -> tuple[float | None, str]:
    if not isinstance(run, dict):
        return None, "run-missing"
    task = run.get("task")
    if not isinstance(task, dict):
        return None, "task-boundary-missing"
    if task.get("boundary_status") != "complete":
        return None, "task-boundary-incomplete"
    if task.get("completion_status") != "completed":
        return None, f"task-{task.get('completion_status', 'incomplete')}"
    if task.get("acceptance_status") != "passed":
        return None, "acceptance-not-passed"
    started = _number(task.get("started_at_ms"))
    finished = _number(task.get("finished_at_ms"))
    if started is None or finished is None or finished < started:
        return None, "task-boundary-invalid"
    return finished - started, ""


def _equivalent_completion(baseline: Any, improved: Any) -> tuple[bool, str]:
    if not isinstance(baseline, dict) or not isinstance(improved, dict):
        return False, "run-missing"
    base_task = baseline.get("task")
    improved_task = improved.get("task")
    if not isinstance(base_task, dict) or not isinstance(improved_task, dict):
        return False, "task-boundary-missing"
    for task in (base_task, improved_task):
        if task.get("completion_status") != "completed":
            return False, f"task-{task.get('completion_status', 'incomplete')}"
        if task.get("acceptance_status") != "passed":
            return False, "acceptance-not-passed"
    baseline_digest = base_task.get("completion_digest")
    improved_digest = improved_task.get("completion_digest")
    if (
        not isinstance(baseline_digest, str)
        or _DIGEST.fullmatch(baseline_digest) is None
        or baseline_digest != improved_digest
    ):
        return False, "completion-digest-mismatch"
    acceptance = base_task.get("acceptance_version")
    if not isinstance(acceptance, str) or not acceptance or acceptance != improved_task.get(
        "acceptance_version"
    ):
        return False, "acceptance-version-mismatch"
    return True, ""


def _intervention_summary(run: Any) -> dict[str, int | float | None]:
    intervention = run.get("user_intervention") if isinstance(run, dict) else None
    if not isinstance(intervention, dict):
        return {"count": None, "observed_duration_ms": None}
    counts = [_integer(intervention.get(kind)) for kind in INTERVENTION_KINDS]
    duration = _number(intervention.get("observed_duration_ms"))
    return {
        "count": sum(counts) if all(value is not None for value in counts) else None,
        "observed_duration_ms": duration,
    }


def _observed_counts(run: Any) -> dict[str, int | None]:
    observed = run.get("observed") if isinstance(run, dict) else None
    if not isinstance(observed, dict):
        return {
            "tool_calls": None,
            "post_failure_calls_before_mutation": None,
            "repair_cycles": None,
            "model_round_trips": None,
        }
    return {
        key: _integer(observed.get(key))
        for key in (
            "tool_calls", "post_failure_calls_before_mutation",
            "repair_cycles", "model_round_trips",
        )
    }


def _activity_summary(run: Any) -> dict[str, int | None]:
    intervals = run.get("activity_intervals") if isinstance(run, dict) else None
    if not isinstance(intervals, list):
        return {"interval_count": None, "unclassified_interval_count": None}
    valid = [
        item for item in intervals
        if isinstance(item, dict) and item.get("kind") in ACTIVITY_KINDS
    ]
    return {
        "interval_count": len(valid),
        "unclassified_interval_count": sum(
            item.get("kind") == "unclassified" for item in valid
        ),
    }


def evaluate_pair(pair: Any) -> dict[str, Any]:
    """Evaluate one pair without discarding failures or incomplete runs."""

    if not isinstance(pair, dict):
        return {"status": "invalid", "reason": "pair-invalid"}
    identity = {
        "id": _safe_text(pair.get("id"), 96),
        "baseline_variant": pair.get("baseline_variant"),
        "improved_variant": pair.get("improved_variant"),
        "scenario": _safe_text(pair.get("scenario"), 96),
        "run_kind": pair.get("run_kind"),
        "runtime_mode": pair.get("runtime_mode"),
    }
    if (
        not identity["id"]
        or identity["baseline_variant"] not in VARIANTS
        or identity["improved_variant"] not in VARIANTS
        or identity["baseline_variant"] == identity["improved_variant"]
        or not identity["scenario"]
        or identity["run_kind"] not in RUN_KINDS
        or identity["runtime_mode"] not in RUNTIME_MODES
    ):
        return {**identity, "status": "invalid", "reason": "pair-scope-invalid"}
    baseline = pair.get("baseline")
    improved = pair.get("improved")
    equivalent, equivalent_reason = _equivalent_completion(baseline, improved)
    baseline_elapsed, baseline_time_reason = _task_elapsed(baseline)
    improved_elapsed, improved_time_reason = _task_elapsed(improved)
    time_reason = equivalent_reason or baseline_time_reason or improved_time_reason
    time_delta = time_ratio = None
    if equivalent and baseline_elapsed is not None and improved_elapsed is not None:
        if baseline_elapsed > 0:
            time_delta = baseline_elapsed - improved_elapsed
            time_ratio = 1 - improved_elapsed / baseline_elapsed
        else:
            time_reason = "baseline-task-duration-zero"
    baseline_tokens, baseline_usage_reason, baseline_round_trips = usage_total(
        baseline.get("usage") if isinstance(baseline, dict) else None
    )
    improved_tokens, improved_usage_reason, improved_round_trips = usage_total(
        improved.get("usage") if isinstance(improved, dict) else None
    )
    token_reason = equivalent_reason or baseline_usage_reason or improved_usage_reason
    token_ratio = None
    if equivalent and baseline_tokens is not None and improved_tokens is not None:
        if baseline_tokens > 0:
            token_ratio = 1 - improved_tokens / baseline_tokens
        else:
            token_reason = "baseline-token-total-zero"
    base_intervention = _intervention_summary(baseline)
    improved_intervention = _intervention_summary(improved)
    intervention_delta = (
        base_intervention["count"] - improved_intervention["count"]
        if base_intervention["count"] is not None
        and improved_intervention["count"] is not None
        else None
    )
    base_observed = _observed_counts(baseline)
    improved_observed = _observed_counts(improved)
    base_activity = _activity_summary(baseline)
    improved_activity = _activity_summary(improved)
    return {
        **identity,
        "status": "comparable" if equivalent else "incomplete",
        "reason": "" if equivalent else equivalent_reason,
        "equivalent_completion": equivalent,
        "task_completion_time_delta_ms": time_delta,
        "task_completion_time_savings_ratio": time_ratio,
        "task_time_reason": time_reason,
        # Absolute usage remains internal. public_projection never copies it.
        "baseline_total_tokens": baseline_tokens,
        "improved_total_tokens": improved_tokens,
        "token_savings_ratio": token_ratio,
        "token_reason": token_reason,
        "baseline_model_round_trips": baseline_round_trips,
        "improved_model_round_trips": improved_round_trips,
        "baseline_user_intervention": base_intervention,
        "improved_user_intervention": improved_intervention,
        "user_intervention_count_delta": intervention_delta,
        "baseline_observed": base_observed,
        "improved_observed": improved_observed,
        "baseline_activity": base_activity,
        "improved_activity": improved_activity,
    }


def _median(values: Iterable[float]) -> float | None:
    materialized = list(values)
    return statistics.median(materialized) if materialized else None


def _range(values: Iterable[float]) -> dict[str, float] | None:
    materialized = list(values)
    return (
        {"min": min(materialized), "max": max(materialized)}
        if materialized
        else None
    )


def _scope_key(result: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(result["baseline_variant"]),
        str(result["improved_variant"]),
        str(result["scenario"]),
        str(result["run_kind"]),
        str(result["runtime_mode"]),
    )


def _effect_status(ratio: float | None) -> str:
    if ratio is None:
        return "unmeasured"
    if ratio > 0:
        return "faster"
    if ratio < 0:
        return "slower"
    return "unchanged"


def _complete_sum(values: Iterable[int | float | None]) -> int | float | None:
    materialized = list(values)
    if not materialized or any(value is None for value in materialized):
        return None
    return sum(value for value in materialized if value is not None)


def _public_group(
    scope: tuple[str, str, str, str, str], results: list[dict[str, Any]], generated_at: int
) -> dict[str, Any]:
    baseline_variant, improved_variant, scenario, run_kind, runtime_mode = scope
    time_results = [
        result
        for result in results
        if result.get("task_completion_time_savings_ratio") is not None
    ]
    token_results = [
        result for result in results if result.get("token_savings_ratio") is not None
    ]
    time_ratios = [float(result["task_completion_time_savings_ratio"]) for result in time_results]
    time_deltas = [float(result["task_completion_time_delta_ms"]) for result in time_results]
    token_ratios = [float(result["token_savings_ratio"]) for result in token_results]
    baseline_token_sum = sum(int(result["baseline_total_tokens"]) for result in token_results)
    improved_token_sum = sum(int(result["improved_total_tokens"]) for result in token_results)
    aggregate_token_ratio = (
        1 - improved_token_sum / baseline_token_sum
        if token_results and baseline_token_sum > 0
        else None
    )
    task_ratio = _median(time_ratios)
    faster = sum(value > 0 for value in time_ratios)
    unchanged = sum(value == 0 for value in time_ratios)
    slower = sum(value < 0 for value in time_ratios)
    incomplete = sum(result.get("status") != "comparable" for result in results)
    failed = sum(
        str(result.get("reason", "")).startswith("task-failed") for result in results
    )
    cancelled = sum(
        str(result.get("reason", "")).startswith("task-cancelled") for result in results
    )
    intervention_pairs = [
        result for result in results
        if result.get("baseline_user_intervention", {}).get("count") is not None
        and result.get("improved_user_intervention", {}).get("count") is not None
    ]
    observed_pairs = [
        result for result in results
        if isinstance(result.get("baseline_observed"), dict)
        and isinstance(result.get("improved_observed"), dict)
    ]
    activity_pairs = [
        result for result in results
        if isinstance(result.get("baseline_activity"), dict)
        and isinstance(result.get("improved_activity"), dict)
    ]
    scope_payload = json.dumps(scope, separators=(",", ":")).encode()
    task_reason = "" if task_ratio is not None else (
        next((str(result.get("task_time_reason")) for result in results if result.get("task_time_reason")), "task-boundary-unavailable")
    )
    token_reason = "" if aggregate_token_ratio is not None else (
        next((str(result.get("token_reason")) for result in results if result.get("token_reason")), "usage-unavailable")
    )
    return {
        "comparison_ref": hashlib.sha256(scope_payload).hexdigest()[:24],
        "baseline_variant": baseline_variant,
        "improved_variant": improved_variant,
        "comparison_label": f"{baseline_variant}→{improved_variant}",
        "scenario": scenario,
        "run_kind": run_kind,
        "runtime_mode": runtime_mode,
        "measured_at": generated_at,
        "sample_count": len(results),
        "comparable_sample_count": sum(result.get("status") == "comparable" for result in results),
        "incomplete_sample_count": incomplete,
        "failed_sample_count": failed,
        "cancelled_sample_count": cancelled,
        "completion_condition": "same-version acceptance digest matched",
        "task_measurement_status": "measured" if task_ratio is not None else "unmeasured",
        "task_measurement_reason": task_reason,
        "task_completion_time_delta_ms": _median(time_deltas),
        "task_completion_time_savings_ratio": task_ratio,
        "task_effect_status": _effect_status(task_ratio),
        "task_time_ratio_range": _range(time_ratios),
        "faster_sample_count": faster,
        "unchanged_sample_count": unchanged,
        "slower_sample_count": slower,
        "token_measurement_status": "measured" if aggregate_token_ratio is not None else "unmeasured",
        "token_measurement_reason": token_reason,
        "token_savings_ratio": aggregate_token_ratio,
        "token_pair_median_savings_ratio": _median(token_ratios),
        "token_ratio_range": _range(token_ratios),
        "token_aggregation": "ratio-of-complete-pair-totals" if aggregate_token_ratio is not None else "unavailable",
        "baseline_user_intervention_count": (
            sum(int(result["baseline_user_intervention"]["count"]) for result in intervention_pairs)
            if intervention_pairs else None
        ),
        "improved_user_intervention_count": (
            sum(int(result["improved_user_intervention"]["count"]) for result in intervention_pairs)
            if intervention_pairs else None
        ),
        "baseline_user_intervention_observed_duration_ms": _complete_sum(
            result["baseline_user_intervention"]["observed_duration_ms"]
            for result in intervention_pairs
        ),
        "improved_user_intervention_observed_duration_ms": _complete_sum(
            result["improved_user_intervention"]["observed_duration_ms"]
            for result in intervention_pairs
        ),
        "user_intervention_pair_count": len(intervention_pairs),
        "baseline_tool_call_count": _complete_sum(
            result["baseline_observed"]["tool_calls"] for result in observed_pairs
        ),
        "improved_tool_call_count": _complete_sum(
            result["improved_observed"]["tool_calls"] for result in observed_pairs
        ),
        "baseline_post_failure_calls_before_mutation": _complete_sum(
            result["baseline_observed"]["post_failure_calls_before_mutation"]
            for result in observed_pairs
        ),
        "improved_post_failure_calls_before_mutation": _complete_sum(
            result["improved_observed"]["post_failure_calls_before_mutation"]
            for result in observed_pairs
        ),
        "baseline_repair_cycle_count": _complete_sum(
            result["baseline_observed"]["repair_cycles"] for result in observed_pairs
        ),
        "improved_repair_cycle_count": _complete_sum(
            result["improved_observed"]["repair_cycles"] for result in observed_pairs
        ),
        "baseline_model_round_trip_count": _complete_sum(
            result["baseline_model_round_trips"] for result in results
        ),
        "improved_model_round_trip_count": _complete_sum(
            result["improved_model_round_trips"] for result in results
        ),
        "baseline_activity_interval_count": _complete_sum(
            result["baseline_activity"]["interval_count"] for result in activity_pairs
        ),
        "improved_activity_interval_count": _complete_sum(
            result["improved_activity"]["interval_count"] for result in activity_pairs
        ),
        "baseline_unclassified_activity_interval_count": _complete_sum(
            result["baseline_activity"]["unclassified_interval_count"]
            for result in activity_pairs
        ),
        "improved_unclassified_activity_interval_count": _complete_sum(
            result["improved_activity"]["unclassified_interval_count"]
            for result in activity_pairs
        ),
        "observability": {
            "activity_intervals_are_non_additive": True,
            "hidden_reasoning_status": "unknown",
        },
    }


def public_projection(evaluation: Any) -> dict[str, Any]:
    """Project an internal evaluation through a token-absolute-free allowlist."""

    generated_at = int(time.time())
    if not isinstance(evaluation, dict):
        return {
            "kind": PUBLIC_KIND,
            "version": PUBLIC_VERSION,
            "generated_at": generated_at,
            "measurement_status": "unmeasured",
            "measurement_reason": "evaluation-missing",
            "presentations": [],
        }
    candidate_time = _integer(evaluation.get("generated_at"), minimum=1)
    if candidate_time is not None:
        generated_at = candidate_time
    if evaluation.get("kind") != INTERNAL_KIND or evaluation.get("version") != INTERNAL_VERSION:
        return {
            "kind": PUBLIC_KIND,
            "version": PUBLIC_VERSION,
            "generated_at": generated_at,
            "measurement_status": "unmeasured",
            "measurement_reason": "evaluation-version-incompatible",
            "presentations": [],
        }
    pairs = evaluation.get("comparisons")
    if not isinstance(pairs, list):
        pairs = []
    evaluated = [evaluate_pair(pair) for pair in pairs]
    valid_scope = [
        result
        for result in evaluated
        if result.get("baseline_variant") in VARIANTS
        and result.get("improved_variant") in VARIANTS
        and result.get("scenario")
        and result.get("run_kind") in RUN_KINDS
        and result.get("runtime_mode") in RUNTIME_MODES
    ]
    groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for result in valid_scope:
        groups.setdefault(_scope_key(result), []).append(result)
    presentations = [
        _public_group(scope, groups[scope], generated_at) for scope in sorted(groups)
    ]
    measured = any(
        presentation["task_measurement_status"] == "measured"
        or presentation["token_measurement_status"] == "measured"
        for presentation in presentations
    )
    reason = "" if measured else _safe_text(
        evaluation.get("measurement_reason", "host-task-or-usage-boundary-unavailable")
    )
    public = {
        "kind": PUBLIC_KIND,
        "version": PUBLIC_VERSION,
        "generated_at": generated_at,
        "measurement_status": "measured" if measured else "unmeasured",
        "measurement_reason": reason,
        "presentations": presentations,
    }
    if public_has_forbidden_usage(public):
        raise ValueError("public task-efficiency projection exposed raw usage")
    return public


def public_has_forbidden_usage(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _FORBIDDEN_PUBLIC_KEYS:
                return True
            if public_has_forbidden_usage(item):
                return True
    elif isinstance(value, list):
        return any(public_has_forbidden_usage(item) for item in value)
    return False


def adapt_host_events(
    events: Any,
    *,
    task_id: str,
    acceptance_version: str,
    usage_scope_complete: bool,
    input_semantics: str,
    output_semantics: str,
    counter_semantics: str,
) -> dict[str, Any]:
    """Adapt explicit host events without inferring hidden activity or boundaries."""

    materialized = [event for event in events if isinstance(event, dict)] if isinstance(events, list) else []
    relevant = [event for event in materialized if event.get("task_id") == task_id]
    starts = [event for event in relevant if event.get("type") == "task.started"]
    finishes = [event for event in relevant if event.get("type") == "task.finished"]
    start = starts[0] if len(starts) == 1 else None
    finish = finishes[-1] if len(finishes) == 1 else None
    start_ms = _number(start.get("timestamp_ms")) if start else None
    finish_ms = _number(finish.get("timestamp_ms")) if finish else None
    completion_status = (
        finish.get("completion_status")
        if finish and finish.get("completion_status") in COMPLETION_STATUSES
        else "incomplete"
    )
    completion_digest = (
        finish.get("completion_digest")
        if finish and isinstance(finish.get("completion_digest"), str)
        and _DIGEST.fullmatch(finish["completion_digest"])
        else ""
    )
    usage_events = []
    for event in relevant:
        if event.get("type") != "turn.completed" or not isinstance(event.get("usage"), dict):
            continue
        usage = event["usage"]
        usage_events.append(
            {
                "event_id": event.get("event_id"),
                "response_id": event.get("response_id"),
                "sequence": event.get("sequence"),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cached_input_tokens": usage.get("cached_input_tokens", 0),
                "reasoning_output_tokens": usage.get("reasoning_output_tokens", 0),
            }
        )
    tool_ids = {
        event.get("tool_call_id")
        for event in relevant
        if event.get("type") == "tool.completed"
        and isinstance(event.get("tool_call_id"), str)
    }
    failures = sorted(
        _number(event.get("timestamp_ms"))
        for event in relevant
        if event.get("type") == "verification.failed"
        and _number(event.get("timestamp_ms")) is not None
    )
    mutations = sorted(
        _number(event.get("timestamp_ms"))
        for event in relevant
        if event.get("type") == "file.mutated"
        and _number(event.get("timestamp_ms")) is not None
    )
    post_failure_calls = None
    if failures:
        first_failure = failures[0]
        first_mutation = next((value for value in mutations if value >= first_failure), None)
        if first_mutation is not None:
            post_failure_calls = len(
                {
                    event.get("tool_call_id")
                    for event in relevant
                    if event.get("type") == "tool.completed"
                    and isinstance(event.get("tool_call_id"), str)
                    and _number(event.get("timestamp_ms")) is not None
                    and first_failure <= float(event["timestamp_ms"]) < first_mutation
                }
            )
    interventions = {kind: 0 for kind in INTERVENTION_KINDS}
    intervention_duration = 0.0
    intervention_duration_complete = True
    for event in relevant:
        if event.get("type") != "user.intervention":
            continue
        kind = event.get("kind")
        if kind in interventions:
            interventions[kind] += 1
        duration = _number(event.get("observed_duration_ms"))
        if duration is None:
            intervention_duration_complete = False
        else:
            intervention_duration += duration
    activity = []
    for event in relevant:
        if event.get("type") != "activity.interval" or event.get("kind") not in ACTIVITY_KINDS:
            continue
        interval_start = _number(event.get("started_at_ms"))
        interval_end = _number(event.get("finished_at_ms"))
        if interval_start is None or interval_end is None or interval_end < interval_start:
            continue
        activity.append(
            {
                "kind": event["kind"],
                "started_at_ms": interval_start,
                "finished_at_ms": interval_end,
            }
        )
    return {
        "task": {
            "boundary_status": (
                "complete"
                if start_ms is not None and finish_ms is not None and finish_ms >= start_ms
                else "missing"
            ),
            "started_at_ms": start_ms,
            "finished_at_ms": finish_ms,
            "completion_status": completion_status,
            "acceptance_status": (
                "passed" if finish and finish.get("acceptance_status") == "passed" else "unavailable"
            ),
            "completion_digest": completion_digest,
            "acceptance_version": acceptance_version,
        },
        "usage": {
            "version": 1,
            "scope_status": "complete" if usage_scope_complete else "partial",
            "covers_task_boundary": usage_scope_complete,
            "input_semantics": input_semantics,
            "output_semantics": output_semantics,
            "counter_semantics": counter_semantics,
            "events": usage_events,
        },
        "activity_intervals": activity,
        "user_intervention": {
            **interventions,
            "observed_duration_ms": (
                intervention_duration if intervention_duration_complete else None
            ),
        },
        "observed": {
            "tool_calls": len(tool_ids),
            "post_failure_calls_before_mutation": post_failure_calls,
            "repair_cycles": None,
            "model_round_trips": None,
        },
        "hidden_reasoning": "unknown",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a token-ratio-only public Click task evaluation."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--public-output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        internal = json.loads(arguments.input.read_text(encoding="utf-8"))
        public = public_projection(internal)
        arguments.public_output.parent.mkdir(parents=True, exist_ok=True)
        arguments.public_output.write_text(
            json.dumps(public, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"Click task evaluation failed: {exc}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
