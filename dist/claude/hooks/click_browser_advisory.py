#!/usr/bin/env python3
"""Non-authoritative Browser workflow guidance for Click.

This leaf module recognizes observable repetition and long timed interactions
only to produce advisory text. It does not grant or deny Browser authority,
bind host calls to receipts, read or mutate Click state, or mark evidence
complete.
"""

from __future__ import annotations

import math
import re
from typing import Any


RECOMMENDED_BROWSER_TOOL_TIMEOUT_MS = 30_000
RECOMMENDED_BROWSER_WAIT_MS = 5_000
BROWSER_WAIT_PATTERNS = (
    re.compile(r"(?i:waitForTimeout)\s*\(\s*(\d+)"),
    re.compile(r"(?i:setTimeout)\s*\([^,]{0,240},\s*(\d+)"),
)


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if not math.isfinite(float(value)) or value < 0:
        return 0
    return int(value)


def _declared_waits_ms(code: str) -> tuple[float, ...]:
    waits: list[float] = []
    for pattern in BROWSER_WAIT_PATTERNS:
        for match in pattern.finditer(code):
            try:
                wait_ms = float(match.group(1))
            except (OverflowError, ValueError):
                continue
            if math.isfinite(wait_ms):
                waits.append(wait_ms)
    return tuple(waits)


def longest_declared_runtime_ms(tool_input: Any) -> float:
    """Return the longest observable timeout or explicit wait, if any."""
    if not isinstance(tool_input, dict):
        return 0.0
    candidates: list[float] = []
    timeout_ms = tool_input.get("timeout_ms")
    if (
        isinstance(timeout_ms, (int, float))
        and not isinstance(timeout_ms, bool)
        and math.isfinite(float(timeout_ms))
        and timeout_ms > 0
    ):
        candidates.append(float(timeout_ms))
    code = tool_input.get("code")
    if isinstance(code, str):
        candidates.extend(_declared_waits_ms(code))
    return max(candidates, default=0.0)


def input_advisories(tool_input: Any) -> tuple[str, ...]:
    """Return non-blocking guidance for observable Browser timing choices."""
    if not isinstance(tool_input, dict):
        return ()
    advisories: list[str] = []
    timeout_ms = tool_input.get("timeout_ms")
    if (
        isinstance(timeout_ms, (int, float))
        and not isinstance(timeout_ms, bool)
        and math.isfinite(float(timeout_ms))
        and timeout_ms > RECOMMENDED_BROWSER_TOOL_TIMEOUT_MS
    ):
        advisories.append(
            "Click advisory: this Browser call requests a timeout above 30 seconds. "
            "It remains allowed and receipt-bound; prefer deterministic state or one "
            "representative interaction when practical."
        )
    code = tool_input.get("code")
    if isinstance(code, str) and any(
        wait_ms > RECOMMENDED_BROWSER_WAIT_MS
        for wait_ms in _declared_waits_ms(code)
    ):
        advisories.append(
            "Click advisory: this Browser interaction includes an explicit wait above "
            "five seconds. It remains allowed and receipt-bound; prefer deterministic "
            "state when practical."
        )
    return tuple(advisories)


def repeat_advisory(prior_attempt: Any) -> str:
    """Return non-blocking guidance for a normalized input seen before."""
    if not isinstance(prior_attempt, dict):
        return ""
    status = str(prior_attempt.get("status", ""))
    successes = _count(prior_attempt.get("successful_attempts", 0))
    if status == "success" and successes == 0:
        successes = 1
    if successes:
        return (
            "Click advisory: this normalized Browser interaction already succeeded for "
            "the current revision. A fresh tool invocation remains allowed and receives "
            "its own tool_use_id-bound result record; reuse or finalize the assigned "
            "source when practical."
        )

    failures = _count(prior_attempt.get("failed_attempts", 0))
    if status in {"failed", "incomplete"} and failures == 0:
        failures = max(1, _count(prior_attempt.get("attempts", 0)))
    if failures >= 2:
        return (
            "Click advisory: this normalized Browser interaction already failed or "
            "produced incomplete evidence twice for the current revision. A fresh "
            "tool invocation remains allowed and receipt-bound; repair or change the "
            "input when practical."
        )
    return ""
