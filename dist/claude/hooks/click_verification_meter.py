#!/usr/bin/env python3
"""Deterministic verification-unit metering for Click.

This leaf module preserves deterministic class normalization and unit arithmetic
for state and direct-caller compatibility. Those values are not runtime advice,
receipt evidence, or facts about cost, quality, or evidence strength. This
module does not choose evidence or a verification scale, grant or deny
authority, inspect argv, read contract state, or execute a check.
"""

from __future__ import annotations

from collections.abc import Iterable


VERIFICATION_CLASSES = {"targeted": 1, "broad": 3, "deep": 5}


def class_units(check_class: object) -> int | None:
    """Return the units for one recognized class."""
    if not isinstance(check_class, str):
        return None
    return VERIFICATION_CLASSES.get(check_class)


def effective_class(submitted_class: object, minimum_class: object) -> str | None:
    """Raise an underdeclared class to the deterministic runtime minimum."""
    submitted_units = class_units(submitted_class)
    minimum_units = class_units(minimum_class)
    if submitted_units is None or minimum_units is None:
        return None
    return (
        str(minimum_class)
        if submitted_units < minimum_units
        else str(submitted_class)
    )


def total_units(classes: Iterable[object]) -> int | None:
    """Return the sum for recognized classes, failing closed on an invalid class."""
    total = 0
    for check_class in classes:
        units = class_units(check_class)
        if units is None:
            return None
        total += units
    return total
