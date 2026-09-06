#!/usr/bin/env python3
"""Canonical incremental-verification plan records.

This module does not decide whether evidence may be reused.  The verification
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
from typing import Any, Iterable


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
        "receipt-invalid",
    }
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
TIMING_BASELINE_VERSION = 2
TIMING_BINDING_VERSION = 1
TIMING_UNIT = "ms"
TIMING_MEASUREMENT_SCOPE = "source-command-dispatch-through-return"
TIMING_EXECUTION_MODEL = "sequential"
TIMING_OBSERVER_MODES = frozenset({"off", "shadow", "authoritative"})
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
REUSE_ORIGIN_KINDS = frozenset({"successor-evidence", "successor-contract"})
EXECUTION_REASONS = frozenset({
    "", "batch-finished", "request-rejected", "runner-admission-rejected",
    "plan-not-created", "reuse-applied", "command-started", "command-passed",
    "command-failed", "command-interrupted", "command-error",
    "preceding-check-stopped", "workspace-invalidated", "outcome-unconfirmed",
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
_REUSE_ORIGIN_FIELDS = frozenset({
    "kind", "batch_id", "evidence_session_id", "candidate_digest",
    "origin_revision",
})
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
        _SOURCE_FIELDS_V1, _SOURCE_FIELDS
    }:
        return False
    planned = {key: value[key] for key in _DECISION_FIELDS | {"duration_baseline"}}
    if planned["decision"] is None:
        planned.update(decision="not-evaluable", reason_code="receipt-invalid")
    if not decision_is_valid(planned):
        return False
    reuse_origin = value.get("reuse_origin")
    return bool(
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


def batch_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != (_BATCH_FIELDS | {"task"} if value.get("version") == 4 else _BATCH_FIELDS):
        return False
    items = value.get("sources")
    return bool(
        value.get("event") == BATCH_EVENT and value.get("version") in {1, 2, 3, 4}
        and (value["version"] != 4 or batch_task_is_valid(value.get("task")))
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
            or (value["version"] in {2, 3, 4} and "reuse_origin" in item)
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
            or value["version"] in {3, 4} and is_duration(value.get("request_wall_ms"))
            and value.get("measurement_scope") == "hook-entry-to-result-recording"
        )
    )


def new_batch(
    plan: dict[str, Any] | None, *, batch_id: str, revision: int,
    prepared_ms: float | None, requested: list[dict[str, str]] | None = None,
    labels: dict[str, str] | None = None,
    reuse_origins: dict[str, dict[str, Any]] | None = None,
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
    batch = {
        "event": BATCH_EVENT, "version": 2, "batch_id": batch_id,
        "timestamp": timestamp, "finished_at": None, "current_revision": revision,
        "status": "planned", "reason_code": "", "requested_source_count": len(items) if plan or requested is not None else None,
        "sources": [],
        "prepare_duration_ms": prepared_ms, "runner_duration_ms": None,
        # Host queue/handoff/return is not measured by these separate processes.
        "request_wall_ms": None, "measurement_scope": "prepare-only",
    }
    if batch_task_is_valid(task):
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
            return json.loads(json.dumps(item))
    return None


def batch_history(verification: Any, *, now: int | None = None) -> list[dict[str, Any]]:
    if not isinstance(verification, dict):
        return []
    events = verification.get(HISTORY_FIELD, [])
    if not isinstance(events, list):
        return []
    records = prune_history(events, now=int(time.time()) if now is None else now)
    # Repeated deliveries of the same batch never become new performance samples.
    unique = {item["batch_id"]: item for item in records if item.get("event") == BATCH_EVENT}
    return json.loads(json.dumps(list(unique.values())))


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
    complete_request_timing(verification, batch)
    return store_batch(verification, batch)


def finish_reuse(verification: dict[str, Any]) -> bool:
    batch = current_batch(verification)
    if batch is None or any(item["decision"] not in REUSE_DECISIONS for item in batch["sources"]):
        return False
    batch.update(status="passed", reason_code="batch-finished", finished_at=int(time.time()))
    for item in batch["sources"]:
        item.update(status="reused", execution_reason_code="reuse-applied")
    complete_request_timing(verification, batch)
    return store_batch(verification, batch)


def mark_started(verification: dict[str, Any], source_key: str) -> bool:
    batch = current_batch(verification)
    if batch is None or batch["status"] not in {"planned", "running"}:
        return False
    for item in batch["sources"]:
        if item["source_key"] == source_key and item["decision"] not in REUSE_DECISIONS:
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
    batches = [item for item in batch_history(verification)
               if item["status"] not in {"planned", "running", "incomplete"}]
    summaries = [batch_summary(item) for item in batches]
    return {"finalized_batch_count": len(batches), **{
        key: sum(item[key] for item in summaries) for key in (
            "executed_source_count", "authoritative_reuse_count", "not_run_source_count"
        )}}


def history_accounting(verification: Any) -> dict[str, Any]:
    """Count actual group requests, including distinct retries, not test cases."""
    batches = batch_history(verification)
    summaries = [batch_summary(batch) for batch in batches]
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
    scope_complete = bool(batch["status"] == "passed" and source_scope_complete)
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


def host_summary(verification: Any) -> str:
    batch = current_batch(verification)
    if batch is None:
        return ""
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
        omitted_text = f"확인된 표본 합계 ≥ {_host_duration(omitted)}"
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
    return (
        f"[Click 결과] {headline}; "
        f"재사용으로 생략한 테스트 실행시간: {omitted_text}; "
        f"동일 샤드 전체 순차 실행 예상: {full_text}; "
        f"이번 테스트 실행: {executed_text}; "
        f"테스트 실행시간 감소: {reduction_text}; "
        f"시간 근거 커버리지 {timed_reused}/{actual_reused} · "
        f"이전 계약 재판정 {prior}개; "
        "과거 성공 실행 기록 기반 추정 / 동일 샤드 순차 기준 / Click 관리비용 제외."
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
        or selected not in DECISIONS
        or value.get("reason_code") not in REASON_CODES
        or not _is_integer(value.get("current_revision"))
        or not _is_integer(value.get("previous_revision"), minimum=-1)
        or not isinstance(value.get("check_digest"), str)
        or _DIGEST.fullmatch(value["check_digest"]) is None
        or authority not in AUTHORITY_SOURCES
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
        authority == expected_authority
        and (selected in REUSE_DECISIONS or avoided == 0)
    )


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
    runner_duration_ms: float | None = None, workspace_changed: bool = False,
) -> bool:
    """Record witnessed outcomes, never derive executions from a plan."""
    batch = current_batch(verification)
    if batch is None or source_results is None or exit_code is None:
        return False
    if batch["status"] not in {"planned", "running"}:
        return True  # A delivered final result is idempotent.
    if not any(item.get("started") for item in source_results.values()) and exit_code != 0:
        return reject_batch(verification, reason="runner-admission-rejected", runner_duration_ms=runner_duration_ms)
    reused = set(reused_keys) if not workspace_changed else set()
    for source in batch["sources"]:
        key = source["source_key"]
        result = source_results.get(key)
        if source["completed"]:
            # A source-level completion was already persisted under the claimed
            # runner. The final batch fold must not roll that fact backward.
            continue
        if result is not None:
            source.update({
                "status": result["status"], "started": result["started"],
                "completed": result["completed"],
                "execution_reason_code": result["reason_code"],
                "duration_ms": source_durations_ms.get(key),
            })
        elif key in reused and source["decision"] in REUSE_DECISIONS:
            source.update(status="reused", execution_reason_code="reuse-applied")
        else:
            source.update(status="not-run", execution_reason_code=(
                "workspace-invalidated" if workspace_changed else "preceding-check-stopped"
            ))
    batch["status"] = "interrupted" if exit_code == 130 else "passed" if exit_code == 0 else "failed"
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


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


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
        (
            json.loads(json.dumps(event))
            for event in events
            if _history_event_is_valid(event) and event["timestamp"] >= cutoff
        ),
        key=lambda event: event["timestamp"],
    )[-max_events:]
    while retained and len(_canonical_bytes(retained)) > max_bytes:
        retained.pop(0)
    return retained


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
        } if batch is not None else None),
        "checks": checks,
    }
