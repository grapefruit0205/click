#!/usr/bin/env python3
"""Non-authoritative verification-profile names for Click.

This leaf module owns the qualitative profile names. The legacy numeric table
and accessor remain only for state and direct-caller compatibility; Click does
not use those numbers as authority, advice, receipt evidence, or a claim about
cost. A future numeric budget requires an explicit user-owned policy field.
"""

from __future__ import annotations


VERIFICATION_SCALES = ("quick", "focused", "full")
VERIFICATION_UNIT_LIMITS = {"quick": 1, "focused": 4, "full": 10}


def is_profile(value: object) -> bool:
    """Return whether *value* names one supported qualitative profile."""
    return isinstance(value, str) and value in VERIFICATION_SCALES


def approved_unit_limit(scale: object) -> int | None:
    """Return the legacy value retained for compatibility, never authority."""
    if not isinstance(scale, str):
        return None
    return VERIFICATION_UNIT_LIMITS.get(scale)
