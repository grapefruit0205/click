#!/usr/bin/env python3
"""Operating-system-neutral Shadow Observer result and lifecycle helpers.

This module owns the bounded v1 telemetry envelope used by every collector.
It has no backend probing or raw-event parsing and never feeds authority-bearing
dependency observations, evidence reuse, approval, or completion.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import re
import time
from typing import Any

if __package__:
    from . import click_dependency_cache
else:  # Executed beside the bundled hook modules.
    import click_dependency_cache


SHADOW_STATE_VERSION = 1
SHADOW_STATE_FIELD = "shadow_observer"
MAX_SHADOW_STATE_RECORDS = 256

_DIGEST = re.compile(r"^[0-9a-f]{64}$")

FallbackExecutor = Callable[[], int]


@dataclass(frozen=True, slots=True)
class ShadowExecution:
    """One real command result paired with best-effort shadow telemetry."""

    exit_code: int
    record: dict[str, Any]


def bounded_add(left: int, right: int) -> int:
    return min(click_dependency_cache.MAX_JSON_SAFE_INTEGER, left + right)


def record(
    *,
    evidence_key: str,
    check_digest: str,
    mutation_revision: int,
    backend_name: str | None,
    backend_version: str = "",
    backend_digest: str = "",
    inputs: Sequence[dict[str, Any]] = (),
    status: str,
    external_input_count: int = 0,
    unresolved_event_count: int = 0,
    child_process_count: int = 0,
    process_tree_complete: bool,
    command_duration_ms: int,
    observer_overhead_ms: int,
) -> dict[str, Any]:
    """Build a canonical v1 record or fail closed to unavailable telemetry."""

    safe_duration = max(
        0,
        min(command_duration_ms, click_dependency_cache.MAX_JSON_SAFE_INTEGER),
    )
    safe_overhead = max(0, min(observer_overhead_ms, safe_duration))
    try:
        return click_dependency_cache.shadow_observer_record(
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
            backend_name=backend_name,
            backend_version=backend_version,
            backend_digest=backend_digest,
            inputs=inputs,
            status=status,
            external_input_count=external_input_count,
            unresolved_event_count=unresolved_event_count,
            child_process_count=child_process_count,
            process_tree_complete=process_tree_complete,
            command_duration_ms=safe_duration,
            observer_overhead_ms=safe_overhead,
        )
    except (TypeError, ValueError):
        return click_dependency_cache.shadow_observer_record(
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
            backend_name=None,
            status="unavailable",
            process_tree_complete=False,
            command_duration_ms=safe_duration,
            observer_overhead_ms=safe_overhead,
        )


def fallback_execution(
    execute_unobserved: FallbackExecutor,
    *,
    evidence_key: str,
    check_digest: str,
    mutation_revision: int,
    status: str,
    backend_name: str | None = None,
    backend_version: str = "",
    backend_digest: str = "",
    preparation_ms: int = 0,
) -> ShadowExecution:
    """Run the target once through its established unobserved execution path."""

    command_started = time.monotonic()
    exit_code = int(execute_unobserved())
    duration_ms = max(0, int((time.monotonic() - command_started) * 1000))
    return ShadowExecution(
        exit_code=exit_code,
        record=record(
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
            backend_name=backend_name,
            backend_version=backend_version,
            backend_digest=backend_digest,
            status=status,
            process_tree_complete=False,
            command_duration_ms=duration_ms,
            observer_overhead_ms=preparation_ms,
        ),
    )


def run_unobserved(
    execute_unobserved: FallbackExecutor,
    *,
    evidence_key: str,
    check_digest: str,
    mutation_revision: int,
) -> ShadowExecution:
    """Execute through the established path and record unavailable telemetry."""

    return fallback_execution(
        execute_unobserved,
        evidence_key=evidence_key,
        check_digest=check_digest,
        mutation_revision=mutation_revision,
        status="unavailable",
    )


def combine_records(
    records: Sequence[dict[str, Any]],
    *,
    evidence_key: str,
    check_digest: str,
    mutation_revision: int,
    unexecuted_checks: int = 0,
) -> dict[str, Any] | None:
    """Combine per-command records into the latest bounded source record."""

    valid = [
        item
        for item in records
        if click_dependency_cache.shadow_observer_record_is_valid(item)
        and item["binding"]
        == {
            "evidence_key": evidence_key,
            "check_digest": check_digest,
            "mutation_revision": mutation_revision,
        }
    ]
    if len(valid) != len(records) or not valid:
        return None
    backends = {
        tuple(sorted(item["backend"].items()))
        for item in valid
        if isinstance(item.get("backend"), dict)
    }
    if len(backends) > 1:
        return None
    backend = dict(next(iter(backends))) if backends else None
    observed = [item for item in valid if item["status"] != "unavailable"]
    duration = 0
    overhead = 0
    for item in valid:
        duration = bounded_add(duration, int(item["command_duration_ms"]))
        overhead = bounded_add(overhead, int(item["observer_overhead_ms"]))
    if not observed:
        return record(
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
            backend_name=None,
            status="unavailable",
            process_tree_complete=False,
            command_duration_ms=duration,
            observer_overhead_ms=overhead,
        )
    if backend is None:
        return None
    unavailable_count = len(valid) - len(observed)
    status = (
        "failed"
        if any(item["status"] == "failed" for item in observed)
        else "partial"
        if unavailable_count
        or unexecuted_checks
        or any(item["status"] == "partial" for item in observed)
        else "complete"
    )
    unresolved = unexecuted_checks + unavailable_count
    external = 0
    children = 0
    inputs: list[dict[str, Any]] = []
    for item in observed:
        unresolved = bounded_add(unresolved, int(item["unresolved_event_count"]))
        external = bounded_add(external, int(item["external_input_count"]))
        children = bounded_add(children, int(item["child_process_count"]))
        inputs.extend(item["inputs"])
    process_tree_complete = bool(
        status == "complete"
        and all(item["process_tree_complete"] for item in observed)
    )
    return record(
        evidence_key=evidence_key,
        check_digest=check_digest,
        mutation_revision=mutation_revision,
        backend_name=str(backend["name"]),
        backend_version=str(backend["version"]),
        backend_digest=str(backend["digest"]),
        inputs=inputs,
        status=status,
        external_input_count=external,
        unresolved_event_count=unresolved,
        child_process_count=children,
        process_tree_complete=process_tree_complete,
        command_duration_ms=duration,
        observer_overhead_ms=overhead,
    )


def fresh_state() -> dict[str, Any]:
    return {"version": SHADOW_STATE_VERSION, "records": {}}


def state_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"version", "records"}:
        return False
    if value.get("version") != SHADOW_STATE_VERSION:
        return False
    records = value.get("records")
    if not isinstance(records, dict) or len(records) > MAX_SHADOW_STATE_RECORDS:
        return False
    return all(
        isinstance(key, str)
        and _DIGEST.fullmatch(key) is not None
        and click_dependency_cache.shadow_observer_record_is_valid(item)
        and item["binding"]["evidence_key"] == key
        for key, item in records.items()
    )


def records_from_verification(verification: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(verification, dict):
        return {}
    state = verification.get(SHADOW_STATE_FIELD)
    if not state_is_valid(state):
        return {}
    return {key: dict(item) for key, item in state["records"].items()}


def store_records(
    verification: dict[str, Any], records: Mapping[str, Any]
) -> int:
    """Persist only canonical latest records; malformed telemetry is ignored."""

    current = records_from_verification(verification)
    stored = 0
    for key, item in sorted(records.items()):
        if len(current) >= MAX_SHADOW_STATE_RECORDS and key not in current:
            continue
        if (
            not isinstance(key, str)
            or _DIGEST.fullmatch(key) is None
            or not click_dependency_cache.shadow_observer_record_is_valid(item)
            or item["binding"]["evidence_key"] != key
        ):
            continue
        current[key] = item
        stored += 1
    verification[SHADOW_STATE_FIELD] = {
        "version": SHADOW_STATE_VERSION,
        "records": current,
    }
    return stored


def advisory(value: Any) -> str:
    if not click_dependency_cache.shadow_observer_record_is_valid(value):
        return "[Click shadow] failed: canonical record unavailable"
    return (
        f"[Click shadow] {value['status']}: inputs={len(value['inputs'])} "
        f"external={value['external_input_count']} "
        f"unresolved={value['unresolved_event_count']} "
        f"overhead={value['observer_overhead_ms']}ms"
    )
