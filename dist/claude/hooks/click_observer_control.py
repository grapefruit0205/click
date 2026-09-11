#!/usr/bin/env python3
"""Lifecycle-local selection of automatic, Shadow and explicit observation."""

from __future__ import annotations

import time
from typing import Any


CONTROL_FIELD = "observer_control"
CONTROL_VERSION = 1
MODES = frozenset({"off", "shadow", "authoritative", "auto", "runtime"})
_FIELDS = frozenset({"version", "mode", "updated_at"})


def fresh_state() -> dict[str, Any]:
    return {"version": CONTROL_VERSION, "mode": "off", "updated_at": 0}


def state_is_valid(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and set(value) == _FIELDS
        and type(value.get("version")) is int
        and value.get("version") == CONTROL_VERSION
        and isinstance(value.get("mode"), str)
        and value.get("mode") in MODES
        and isinstance(value.get("updated_at"), int)
        and not isinstance(value.get("updated_at"), bool)
        and value.get("updated_at", -1) >= 0
    )


def mode(verification: Any) -> str:
    """Return ``off`` for missing or malformed pre-control lifecycle state."""
    value = verification.get(CONTROL_FIELD) if isinstance(verification, dict) else None
    return str(value["mode"]) if state_is_valid(value) else "off"


def set_mode(
    verification: dict[str, Any], selected: str, *, updated_at: int | None = None
) -> None:
    if selected not in MODES:
        raise ValueError("observer mode must be off, shadow, authoritative, auto, or runtime")
    timestamp = int(time.time()) if updated_at is None else updated_at
    value = {"version": CONTROL_VERSION, "mode": selected, "updated_at": timestamp}
    if not state_is_valid(value):
        raise ValueError("observer mode timestamp is invalid")
    verification[CONTROL_FIELD] = value


def projection(verification: Any) -> dict[str, Any]:
    selected = mode(verification)
    return {
        "mode": selected,
        "enabled": selected != "off",
        "authoritative": selected == "authoritative",
        "reuse_authorized": False,  # Mode selection never supplies a receipt.
    }


def captures_inputs(verification: Any) -> bool:
    """Selection permits capture, never reuse without a verified receipt."""
    return mode(verification) in {"authoritative", "auto"}


def batch_supports_capture(batch: dict[str, Any]) -> bool:
    """Output retention and input observation share one target execution."""
    return True


def description(verification: Any) -> str:
    selected = mode(verification)
    if selected == "runtime":
        return "runtime input diagnostics, never automatic reuse authority"
    if selected == "authoritative":
        return "prepared profile, reuse requires a complete bound observation"
    if selected == "auto":
        attempt = verification.get("automatic_observer_attempt", {})
        if isinstance(attempt, dict) and attempt.get("status") == "unavailable":
            return "capture unavailable, normal verification remains available"
        return "automatic capture for supported checks, reuse requires complete bound inputs or an explicitly conditional JS receipt"
    return "input capture disabled, existing receipt and policy reuse remain available"


def carry_selection(previous: Any, current: dict[str, Any]) -> None:
    """Retain explicit off and cached capability across completed Evidence turns."""
    if not isinstance(previous, dict):
        return
    value = previous.get(CONTROL_FIELD)
    if not state_is_valid(value):
        return
    if value["mode"] == "off" and value["updated_at"] == 0:
        return  # Migrate the old implicit Evidence default, not an explicit off.
    current[CONTROL_FIELD] = dict(value)
    if captures_inputs(previous) or mode(previous) == "runtime":
        # These are capability candidates only; the runner revalidates them.
        for key in ("authoritative_observer", "automatic_observer_attempt", "framework_observations"):
            if isinstance(previous.get(key), dict):
                current[key] = dict(previous[key])
        context = previous.get("automatic_observer_context")
        if isinstance(context, str) and len(context) == 64:
            current["automatic_observer_context"] = context
