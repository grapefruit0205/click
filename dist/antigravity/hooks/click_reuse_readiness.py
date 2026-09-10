"""Bounded, content-free explanations; never an execution or reuse authority.

This view reads recorded state only. It does not probe tools, collect inputs,
prepare runtimes, validate current files or write lifecycle state. Every actual
reuse request still goes through the verification engine's current bindings.
"""
from __future__ import annotations

import re
from typing import Any

if __package__:
    from . import click_observer_control as control
    from . import click_observer_profiles as profiles
    from . import click_incremental_records as records
else:
    import click_observer_control as control
    import click_observer_profiles as profiles
    import click_incremental_records as records

VERSION = 1
ASSURANCE = "recorded-status-not-reuse-authority"
MAX_CHECKS = 256
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
# Codes, not arbitrary collector error text, cross the dashboard boundary.
REASONS = frozenset({
    "not-attempted", "prepared", "disabled", "diagnostics-only",
    "owner-policy-selected", "environment-required", "runtime-unsupported",
    "prerequisite-missing", "permission-required", "capture-unavailable",
    "check-required", "inputs-changed", "binding-changed", "conditional",
    "recorded-reuse", "output-required", "learning", "dynamic-input",
})
ACTIONS = frozenset({
    "run-verification", "keep-owner-policy", "check-environment",
    "check-supported-runtime", "check-prerequisites", "check-permissions",
    "retry-observer-auto", "keep-current-mode", "review-conditional-limits",
    "rerun-affected-check", "inspect-verification-status",
})


def preparation(verification: dict) -> dict:
    mode = control.mode(verification)
    if mode == "off":
        reason, action = "disabled", "keep-current-mode"
    elif mode in {"shadow", "runtime"}:
        reason, action = "diagnostics-only", "keep-current-mode"
    else:
        attempt = verification.get("automatic_observer_attempt")
        attempt = attempt if isinstance(attempt, dict) else {}
        raw = attempt.get("reason", "")
        raw = raw[:256] if isinstance(raw, str) else ""
        if attempt.get("status") == "available" or profiles.prepared_state_is_valid(verification.get("authoritative_observer")):
            reason, action = "prepared", "run-verification"
        elif raw == "owner-reuse-policy-selected":
            reason, action = "owner-policy-selected", "keep-owner-policy"
        elif raw == "deterministic-environment-required":
            reason, action = "environment-required", "check-environment"
        elif raw in {"unsupported-native-runtime", "runtime-mismatch"}:
            reason, action = "runtime-unsupported", "check-supported-runtime"
        elif "privilege" in raw or "permission" in raw:
            reason, action = "permission-required", "check-permissions"
        elif any(term in raw for term in ("backend", "compiler", "header", "executable", "library")):
            reason, action = "prerequisite-missing", "check-prerequisites"
        elif attempt.get("status") == "unavailable":
            reason, action = "capture-unavailable", "retry-observer-auto"
        else:
            reason, action = "not-attempted", "run-verification"
    return {"reason": reason, "action": action}


def decision_help(reason: str) -> tuple[str, str]:
    if reason == "conditional-observed-inputs-current":
        return "conditional", "review-conditional-limits"
    if reason in {"observed-input-changed", "explicit-input-changed"}:
        return "inputs-changed", "rerun-affected-check"
    if "binding" in reason or reason == "runtime-identity-incomplete":
        return "binding-changed", "rerun-affected-check"
    if reason == "required-output-not-guaranteed":
        return "output-required", "run-verification"
    if reason in {"observer-incomplete", "external-input-unmodeled", "explicit-input-unavailable"}:
        return "capture-unavailable", "run-verification"
    if reason in {"same-revision-receipt-current", "successor-evidence-current", "observed-dependencies-unchanged",
                  "safe-change-policy-covered", "successor-evidence-dependencies-unchanged", "successor-evidence-safe-change-covered"}:
        return "recorded-reuse", "inspect-verification-status"
    return "check-required", "run-verification"


def projection(state: Any) -> dict:
    state = state if isinstance(state, dict) else {}
    verification = state.get("verification")
    verification = verification if isinstance(verification, dict) else {}
    plan = verification.get(records.PLAN_FIELD)
    decisions = plan.get("decisions") if isinstance(plan, dict) else None
    decisions = decisions if isinstance(decisions, list) else []
    checks = []
    seen = set()
    for decision in decisions[:MAX_CHECKS]:
        try:
            valid = records.decision_is_valid(decision)
        except (TypeError, ValueError):
            valid = False  # Malformed nested legacy data is not display input.
        if not valid:
            continue
        key = decision["source_key"]
        if key in seen:
            continue
        seen.add(key)
        reason, action = decision_help(decision["reason_code"])
        checks.append({"id": key, "decision": decision["decision"], "reason": reason, "action": action})
    return {
        "version": VERSION, "assurance": ASSURANCE, "reuse_authorized": False,
        "mode": control.mode(verification), "preparation": preparation(verification),
        "checks": checks,
    }


def is_valid(value: Any) -> bool:
    if (not isinstance(value, dict) or set(value) != {"version", "assurance", "reuse_authorized", "mode", "preparation", "checks"}
            or type(value.get("version")) is not int or value["version"] != VERSION
            or value.get("assurance") != ASSURANCE or value.get("reuse_authorized") is not False
            or not isinstance(value.get("mode"), str) or value.get("mode") not in control.MODES):
        return False
    prep = value.get("preparation")
    checks = value.get("checks")
    if (not isinstance(prep, dict) or set(prep) != {"reason", "action"}
            or not isinstance(prep.get("reason"), str) or prep.get("reason") not in REASONS or not isinstance(prep.get("action"), str) or prep.get("action") not in ACTIONS
            or not isinstance(checks, list) or len(checks) > MAX_CHECKS):
        return False
    seen = set()
    for check in checks:
        if (not isinstance(check, dict) or set(check) != {"id", "decision", "reason", "action"}
                or not isinstance(check.get("id"), str) or not _DIGEST.fullmatch(check["id"])
                or check["id"] in seen or not isinstance(check.get("decision"), str) or check.get("decision") not in records.DECISIONS
                or not isinstance(check.get("reason"), str) or check.get("reason") not in REASONS or not isinstance(check.get("action"), str) or check.get("action") not in ACTIONS):
            return False
        seen.add(check["id"])
    return True
