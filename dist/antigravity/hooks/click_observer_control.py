#!/usr/bin/env python3
"""Explicit lifecycle-local control for Shadow and authoritative observation."""

from __future__ import annotations

import time
from typing import Any


CONTROL_FIELD = "observer_control"
CONTROL_VERSION = 1
MODES = frozenset({"off", "shadow", "authoritative"})
_FIELDS = frozenset({"version", "mode", "updated_at"})


def fresh_state() -> dict[str, Any]:
    return {"version": CONTROL_VERSION, "mode": "off", "updated_at": 0}


def state_is_valid(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and set(value) == _FIELDS
        and value.get("version") == CONTROL_VERSION
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
        raise ValueError("observer mode must be off, shadow, or authoritative")
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
        "reuse_authorized": selected == "authoritative",
    }
