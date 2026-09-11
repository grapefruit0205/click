#!/usr/bin/env python3
"""Execution Contract schema constants and pure validation for Click.

This module deliberately knows nothing about hook events, persisted state,
process execution, platform adapters, or lifecycle transitions.  It validates
one raw contract payload and returns the normalized JSON object or the exact
compatibility error consumed by the gate.
"""

from __future__ import annotations

import json
import re
from typing import Any

if __package__:
    from . import (
        click_dependency_cache,
        click_verification_meter,
        click_verification_policy,
    )
    from .click_evidence import EVIDENCE_KINDS
else:  # Executed directly from the bundled hooks directory.
    import click_dependency_cache
    import click_verification_meter
    import click_verification_policy
    from click_evidence import EVIDENCE_KINDS


STRING_FIELDS = ("outcome", "plain_language")
OBJECT_FIELDS = ("boundary", "build", "verification")
CONTRACT_FIELDS = set(STRING_FIELDS) | set(OBJECT_FIELDS) | {"must_hold"}
BOUNDARY_FIELDS = {"in_scope", "out_of_scope"}
BUILD_FIELDS = {"approach", "semantics", "order"}
VERIFICATION_FIELDS = {"scale", "evidence", "done_when", "intermediate_gate"}
EVIDENCE_SOURCE_FIELDS = {"id", "kind", "description", "dependencies"}
DONE_WHEN_FIELDS = {"condition", "primary_evidence"}
EVIDENCE_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
# Compatibility aliases retained for direct callers. Qualitative profile names
# and legacy unit arithmetic live below contract schema validation.
VERIFICATION_SCALES = click_verification_policy.VERIFICATION_SCALES
VERIFICATION_UNIT_LIMITS = click_verification_policy.VERIFICATION_UNIT_LIMITS
VERIFICATION_CLASSES = click_verification_meter.VERIFICATION_CLASSES


def human_view(contract: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic plain-language approval projection.

    The structured contract remains canonical and digest-bound. The staging
    Hook exposes only its exact easy explanation by default so technical fields
    stay behind an explicit request for the original contract.
    """
    return {"plain_language": str(contract.get("plain_language", ""))}


def render_human_view(contract_id: str, contract: dict[str, Any]) -> str:
    """Render the exact staged projection for Hook-provided approval context."""
    view = human_view(contract)
    lines = [
        "Click Guarded contract staged. Present the exact Hook-generated easy "
        "contract below once as the default approval body; do not independently "
        "summarize, expand, or repeat it.",
        f"CLICK_CONTRACT_ID={contract_id}",
        "",
        "Plain-language contract",
        str(view["plain_language"]),
        "",
        "Keep the canonical JSON hidden unless the user asks to see the original "
        "contract. Viewing it does not approve, change, or restage this contract; "
        "show the exact staged object with the same contract_id.",
        "End with one compact question in the user's language equivalent to: "
        '"The contract above is explained in plain language. Do you approve it as '
        'written, or would you like to see the original contract first?"',
        "Make approve, request changes, cancel, and view the original contract "
        "available responses, then stop without mutating files.",
    ]
    return "\n".join(lines)


def validate_contract(raw: str) -> tuple[dict[str, Any] | None, str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None, "Execution Contract must be valid JSON."
    if not isinstance(value, dict):
        return None, "Execution Contract must be a JSON object."

    unknown_fields = sorted(set(value) - CONTRACT_FIELDS)
    if unknown_fields:
        rendered = ", ".join(f"`{field}`" for field in unknown_fields)
        return (
            None,
            f"Execution Contract contains unsupported top-level field(s): {rendered}.",
        )

    for field in STRING_FIELDS:
        text = value.get(field)
        if not isinstance(text, str) or not text.strip():
            return None, f"Execution Contract field `{field}` must be a non-empty string."

    must_hold = value.get("must_hold")
    if not isinstance(must_hold, list) or not must_hold:
        return None, "Execution Contract field `must_hold` must be a non-empty list."
    if any(not isinstance(item, str) or not item.strip() for item in must_hold):
        return None, "Every `must_hold` item must be a non-empty string."

    boundary = value.get("boundary")
    if not isinstance(boundary, dict):
        return None, "Execution Contract field `boundary` must be an object."
    unknown_boundary_fields = sorted(set(boundary) - BOUNDARY_FIELDS)
    if unknown_boundary_fields:
        rendered = ", ".join(f"`{field}`" for field in unknown_boundary_fields)
        return None, f"Execution Contract boundary contains unsupported field(s): {rendered}."
    in_scope = boundary.get("in_scope")
    if not isinstance(in_scope, list) or not in_scope:
        return None, "Boundary `in_scope` must be a non-empty list."
    if any(not isinstance(item, str) or not item.strip() for item in in_scope):
        return None, "Every boundary `in_scope` item must be a non-empty string."
    out_of_scope = boundary.get("out_of_scope")
    if not isinstance(out_of_scope, list):
        return None, "Boundary `out_of_scope` must be a list."
    if any(not isinstance(item, str) or not item.strip() for item in out_of_scope):
        return None, "Every boundary `out_of_scope` item must be a non-empty string."

    build = value.get("build")
    if not isinstance(build, dict):
        return None, "Execution Contract field `build` must be an object."
    unknown_build_fields = sorted(set(build) - BUILD_FIELDS)
    if unknown_build_fields:
        rendered = ", ".join(f"`{field}`" for field in unknown_build_fields)
        return None, f"Execution Contract build contains unsupported field(s): {rendered}."
    approach = build.get("approach")
    if not isinstance(approach, list) or not approach:
        return None, "Build `approach` must be a non-empty list."
    if any(not isinstance(item, str) or not item.strip() for item in approach):
        return None, "Every build `approach` item must be a non-empty string."
    for field in ("semantics", "order"):
        if field not in build:
            continue
        items = build[field]
        if not isinstance(items, list) or not items:
            return None, f"Optional build `{field}` must be omitted or a non-empty list."
        if any(not isinstance(item, str) or not item.strip() for item in items):
            return None, f"Every build `{field}` item must be a non-empty string."

    verification = value.get("verification")
    if not isinstance(verification, dict):
        return None, "Execution Contract field `verification` must be an object."
    unknown_verification_fields = sorted(set(verification) - VERIFICATION_FIELDS)
    if unknown_verification_fields:
        rendered = ", ".join(
            f"`{field}`" for field in unknown_verification_fields
        )
        return (
            None,
            f"Execution Contract verification contains unsupported field(s): {rendered}.",
        )
    scale = verification.get("scale")
    if not click_verification_policy.is_profile(scale):
        allowed = ", ".join(VERIFICATION_SCALES)
        return None, f"Verification `scale` must be one of: {allowed}."

    evidence = verification.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return None, "Verification `evidence` must be a non-empty list."
    evidence_ids: set[str] = set()
    browser_source_ids: list[str] = []
    for index, source in enumerate(evidence):
        label = f"Verification evidence item {index + 1}"
        if not isinstance(source, dict):
            return None, f"{label} must be an object."
        unknown_source_fields = sorted(set(source) - EVIDENCE_SOURCE_FIELDS)
        if unknown_source_fields:
            rendered = ", ".join(
                f"`{field}`" for field in unknown_source_fields
            )
            return None, f"{label} contains unsupported field(s): {rendered}."
        source_id = source.get("id")
        if not isinstance(source_id, str) or not EVIDENCE_ID_PATTERN.fullmatch(
            source_id
        ):
            return (
                None,
                f"{label} `id` must start with a letter and contain at most 32 "
                "letters, digits, underscores, or hyphens.",
            )
        if source_id in evidence_ids:
            return None, f"Verification evidence id `{source_id}` must be unique."
        evidence_ids.add(source_id)
        kind = source.get("kind")
        if kind not in EVIDENCE_KINDS:
            allowed = ", ".join(EVIDENCE_KINDS)
            return None, f"Evidence `{source_id}` kind must be one of: {allowed}."
        description = source.get("description")
        if not isinstance(description, str) or not description.strip():
            return None, f"Evidence `{source_id}` description must be non-empty."
        if "dependencies" in source:
            if kind != "argv":
                return (
                    None,
                    f"Evidence `{source_id}` may declare `dependencies` only when its "
                    "kind is `argv`.",
                )
            _, dependency_error = click_dependency_cache.normalize_patterns(
                source.get("dependencies")
            )
            if dependency_error:
                return (
                    None,
                    f"Evidence `{source_id}` dependencies {dependency_error}.",
                )
        if kind == "browser":
            browser_source_ids.append(source_id)
    if len(browser_source_ids) > 1:
        return (
            None,
            "Verification may assign at most one Browser evidence source; reuse its id "
            "across every condition covered by the representative session.",
        )
    done_when = verification.get("done_when")
    if not isinstance(done_when, list) or not done_when:
        return None, "Verification `done_when` must be a non-empty list."
    used_evidence_ids: set[str] = set()
    for index, item in enumerate(done_when):
        label = f"Verification done_when item {index + 1}"
        if not isinstance(item, dict):
            return (
                None,
                f"{label} must be an object with `condition` and `primary_evidence`; "
                "inline evidence strings are no longer accepted.",
            )
        unknown_condition_fields = sorted(set(item) - DONE_WHEN_FIELDS)
        if unknown_condition_fields:
            rendered = ", ".join(
                f"`{field}`" for field in unknown_condition_fields
            )
            return None, f"{label} contains unsupported field(s): {rendered}."
        condition = item.get("condition")
        if not isinstance(condition, str) or not condition.strip():
            return None, f"{label} `condition` must be a non-empty string."
        primary_evidence = item.get("primary_evidence")
        if not isinstance(primary_evidence, str) or not primary_evidence.strip():
            return None, f"{label} `primary_evidence` must be one evidence id."
        if primary_evidence not in evidence_ids:
            return (
                None,
                f"{label} references unknown evidence id `{primary_evidence}`.",
            )
        used_evidence_ids.add(primary_evidence)
    unused_evidence_ids = sorted(evidence_ids - used_evidence_ids)
    if unused_evidence_ids:
        rendered = ", ".join(f"`{source_id}`" for source_id in unused_evidence_ids)
        return (
            None,
            f"Verification evidence source(s) {rendered} are unused; remove them or "
            "reference each one from `done_when`.",
        )
    if "intermediate_gate" in verification:
        intermediate_gate = verification["intermediate_gate"]
        if not isinstance(intermediate_gate, str) or not intermediate_gate.strip():
            return None, "Optional verification `intermediate_gate` must be omitted or non-empty."

    return value, ""
