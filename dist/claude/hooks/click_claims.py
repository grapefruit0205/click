"""Prose-free capability claim ledger primitives for Click receipts.

The ledger records only digests and lifecycle metadata.  A one-use runner token
digest participates in the claim commitment but is never persisted in the
ledger or exported receipt.  Host mutations use a digest of the stable tool-use
identifier and are labelled separately instead of pretending to be token-bound
runners.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any


CLAIM_LEDGER_VERSION = 1
CAPABILITIES = {
    "browser",
    "evidence-attestation",
    "managed-service-start",
    "managed-service-stop",
    "managed-service-supervisor",
    "mutation",
    "observation",
    "verification",
}
CLAIM_MODES = {"host-tool-use", "one-use-runner"}
RESULT_STATUSES = {"failed", "observed", "passed", "running"}

LEDGER_FIELDS = {"version", "history_complete", "next_sequence", "entries"}
ENTRY_FIELDS = {
    "sequence",
    "capability",
    "claim_mode",
    "request_digest",
    "claim_digest",
    "binding_digest",
    "mutation_revision",
    "claimed_at",
    "completed_at",
    "result",
}
RESULT_FIELDS = {"status", "exit_code"}
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def fresh_state(*, history_complete: bool = True) -> dict[str, Any]:
    return {
        "version": CLAIM_LEDGER_VERSION,
        "history_complete": history_complete,
        "next_sequence": 1,
        "entries": [],
    }


def _is_digest(value: Any) -> bool:
    return bool(isinstance(value, str) and DIGEST_PATTERN.fullmatch(value))


def _is_non_negative_int(value: Any) -> bool:
    return bool(isinstance(value, int) and not isinstance(value, bool) and value >= 0)


def _exact_fields(value: Any, expected: set[str], label: str) -> str:
    if not isinstance(value, dict):
        return f"Click capability claim `{label}` must be an object."
    unknown = sorted(set(value) - expected)
    if unknown:
        rendered = ", ".join(f"`{field}`" for field in unknown)
        return f"Click capability claim `{label}` contains unsupported field(s): {rendered}."
    missing = sorted(expected - set(value))
    if missing:
        rendered = ", ".join(f"`{field}`" for field in missing)
        return f"Click capability claim `{label}` is missing field(s): {rendered}."
    return ""


def _normalize_entry(value: Any, *, expected_sequence: int) -> tuple[dict[str, Any] | None, str]:
    error = _exact_fields(value, ENTRY_FIELDS, "entry")
    if error:
        return None, error
    assert isinstance(value, dict)
    if value.get("sequence") != expected_sequence:
        return None, "Click capability claim sequences must be contiguous and ordered."
    capability = value.get("capability")
    if capability not in CAPABILITIES:
        return None, "Click capability claim names an unsupported capability."
    claim_mode = value.get("claim_mode")
    if claim_mode not in CLAIM_MODES:
        return None, "Click capability claim mode is unsupported."
    request_digest = value.get("request_digest")
    claim_digest = value.get("claim_digest")
    binding_digest = value.get("binding_digest")
    if not _is_digest(request_digest) or not _is_digest(claim_digest):
        return None, "Click capability request and claim digests must be lowercase SHA-256."
    if claim_mode == "one-use-runner" and binding_digest != "":
        return None, "A one-use runner claim must not expose a host binding digest."
    if claim_mode == "host-tool-use" and not _is_digest(binding_digest):
        return None, "A host-tool-use claim requires a tool-use binding digest."

    revision = value.get("mutation_revision")
    claimed_at = value.get("claimed_at")
    completed_at = value.get("completed_at")
    if not _is_non_negative_int(revision):
        return None, "Click capability claim mutation revision is invalid."
    if not _is_non_negative_int(claimed_at) or claimed_at <= 0:
        return None, "Click capability claim timestamp is invalid."
    if not _is_non_negative_int(completed_at):
        return None, "Click capability completion timestamp is invalid."

    result = value.get("result")
    error = _exact_fields(result, RESULT_FIELDS, "result")
    if error:
        return None, error
    assert isinstance(result, dict)
    status = result.get("status")
    exit_code = result.get("exit_code")
    if status not in RESULT_STATUSES:
        return None, "Click capability result status is unsupported."
    if status == "running":
        if completed_at != 0 or exit_code is not None:
            return None, "A running Click capability claim cannot contain a result."
    else:
        if completed_at < claimed_at:
            return None, "A completed Click capability claim has invalid timestamps."
        if status == "observed":
            if claim_mode != "host-tool-use" or exit_code is not None:
                return None, "Only host-tool-use claims may have an observed result."
        elif not isinstance(exit_code, int) or isinstance(exit_code, bool):
            return None, "A runner capability result requires an integer exit code."
        elif (status == "passed") != (exit_code == 0):
            return None, "Click capability result status does not match its exit code."

    return {
        "sequence": expected_sequence,
        "capability": capability,
        "claim_mode": claim_mode,
        "request_digest": request_digest,
        "claim_digest": claim_digest,
        "binding_digest": binding_digest,
        "mutation_revision": revision,
        "claimed_at": claimed_at,
        "completed_at": completed_at,
        "result": {"status": status, "exit_code": exit_code},
    }, ""


def validate_ledger(value: Any) -> tuple[dict[str, Any] | None, str]:
    error = _exact_fields(value, LEDGER_FIELDS, "ledger")
    if error:
        return None, error
    assert isinstance(value, dict)
    if value.get("version") != CLAIM_LEDGER_VERSION:
        return None, f"Click capability ledger version must be {CLAIM_LEDGER_VERSION}."
    history_complete = value.get("history_complete")
    if not isinstance(history_complete, bool):
        return None, "Click capability ledger history flag must be boolean."
    entries = value.get("entries")
    if not isinstance(entries, list):
        return None, "Click capability ledger entries must be a list."
    normalized_entries: list[dict[str, Any]] = []
    seen_claims: set[str] = set()
    for sequence, entry in enumerate(entries, start=1):
        normalized, error = _normalize_entry(entry, expected_sequence=sequence)
        if error:
            return None, error
        assert normalized is not None
        claim_digest = str(normalized["claim_digest"])
        if claim_digest in seen_claims:
            return None, "Click capability claim digests must be unique."
        seen_claims.add(claim_digest)
        normalized_entries.append(normalized)
    if value.get("next_sequence") != len(normalized_entries) + 1:
        return None, "Click capability ledger next sequence is invalid."
    return {
        "version": CLAIM_LEDGER_VERSION,
        "history_complete": history_complete,
        "next_sequence": len(normalized_entries) + 1,
        "entries": normalized_entries,
    }, ""


def host_binding_digest(tool_use_id: str) -> str:
    return hashlib.sha256(tool_use_id.encode()).hexdigest() if tool_use_id else ""


def host_request_digest(event: dict[str, Any]) -> str:
    payload = {
        "tool_name": str(event.get("tool_name", "")),
        "tool_input": event.get("tool_input"),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def record_claim(
    state: dict[str, Any],
    *,
    capability: str,
    claim_mode: str,
    request_digest: str,
    mutation_revision: int,
    claimed_at: int,
    token_digest: str = "",
    binding_digest: str = "",
) -> tuple[str, str]:
    ledger_value = state.get("capability_ledger")
    if ledger_value is None:
        ledger_value = fresh_state(history_complete=False)
    ledger, error = validate_ledger(ledger_value)
    if error:
        return "", error
    assert ledger is not None
    contract_digest = state.get("contract_digest")
    if not _is_digest(contract_digest) or not _is_digest(request_digest):
        return "", "Click capability claim contract or request digest is invalid."
    if capability not in CAPABILITIES or claim_mode not in CLAIM_MODES:
        return "", "Click capability claim type is unsupported."
    if (
        not _is_non_negative_int(mutation_revision)
        or not _is_non_negative_int(claimed_at)
        or claimed_at <= 0
    ):
        return "", "Click capability claim revision or timestamp is invalid."
    if claim_mode == "one-use-runner":
        if not _is_digest(token_digest) or binding_digest:
            return "", "Click one-use runner claim binding is invalid."
    elif token_digest or not _is_digest(binding_digest):
        return "", "Click host-tool-use claim binding is invalid."

    sequence = int(ledger["next_sequence"])
    commitment = {
        "contract_digest": contract_digest,
        "sequence": sequence,
        "capability": capability,
        "claim_mode": claim_mode,
        "request_digest": request_digest,
        "token_digest": token_digest,
        "binding_digest": binding_digest,
        "mutation_revision": mutation_revision,
        "claimed_at": claimed_at,
    }
    canonical = json.dumps(commitment, sort_keys=True, separators=(",", ":"))
    claim_digest = hashlib.sha256(canonical.encode()).hexdigest()
    ledger["entries"].append(
        {
            "sequence": sequence,
            "capability": capability,
            "claim_mode": claim_mode,
            "request_digest": request_digest,
            "claim_digest": claim_digest,
            "binding_digest": binding_digest,
            "mutation_revision": mutation_revision,
            "claimed_at": claimed_at,
            "completed_at": 0,
            "result": {"status": "running", "exit_code": None},
        }
    )
    ledger["next_sequence"] = sequence + 1
    state["capability_ledger"] = ledger
    return claim_digest, ""


def complete_claim(
    state: dict[str, Any],
    *,
    capability: str,
    claim_mode: str,
    request_digest: str,
    mutation_revision: int,
    exit_code: int | None,
    binding_digest: str = "",
    completed_at: int | None = None,
) -> bool:
    ledger, error = validate_ledger(state.get("capability_ledger"))
    if error or ledger is None:
        return False
    for entry in reversed(ledger["entries"]):
        if not (
            entry["capability"] == capability
            and entry["claim_mode"] == claim_mode
            and entry["request_digest"] == request_digest
            and entry["mutation_revision"] == mutation_revision
            and entry["binding_digest"] == binding_digest
            and entry["result"]["status"] == "running"
        ):
            continue
        timestamp = completed_at if completed_at is not None else int(time.time()) or 1
        if not _is_non_negative_int(timestamp):
            return False
        if timestamp < int(entry["claimed_at"]):
            timestamp = int(entry["claimed_at"])
        if exit_code is None:
            if claim_mode != "host-tool-use":
                return False
            status = "observed"
        else:
            if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                return False
            status = "passed" if exit_code == 0 else "failed"
        entry["completed_at"] = timestamp
        entry["result"] = {"status": status, "exit_code": exit_code}
        state["capability_ledger"] = ledger
        return True
    return False


def _settle_verified_host_mutations(
    entries: list[dict[str, Any]], *, through_revision: int
) -> None:
    """Project omitted host completions as observed after later verification.

    Some supported hosts admit a mutation through ``PreToolUse`` but omit the
    matching ``PostToolUse`` event. A later passing one-use verification at the
    same or a newer revision proves that Click moved on to verified workspace
    state, but it does not reveal the mutation tool's exit code. Such claims can
    therefore be settled only as ``observed``—never as ``passed``.
    """
    witnesses = [
        entry
        for entry in entries
        if entry["capability"] == "verification"
        and entry["claim_mode"] == "one-use-runner"
        and entry["result"]["status"] == "passed"
        and entry["mutation_revision"] <= through_revision
    ]
    for entry in entries:
        if not (
            entry["capability"] == "mutation"
            and entry["claim_mode"] == "host-tool-use"
            and entry["result"]["status"] == "running"
            and entry["mutation_revision"] <= through_revision
        ):
            continue
        witness = next(
            (
                candidate
                for candidate in witnesses
                if candidate["sequence"] > entry["sequence"]
                and candidate["mutation_revision"] >= entry["mutation_revision"]
            ),
            None,
        )
        if witness is None:
            continue
        entry["completed_at"] = max(
            int(entry["claimed_at"]), int(witness["completed_at"])
        )
        entry["result"] = {"status": "observed", "exit_code": None}


def receipt_entries(
    state: dict[str, Any], *, settle_through_revision: int | None = None
) -> tuple[list[dict[str, Any]] | None, str]:
    ledger, error = validate_ledger(state.get("capability_ledger"))
    if error:
        return None, error
    assert ledger is not None
    if ledger.get("history_complete") is not True:
        return None, "Click capability history predates receipt tracking and cannot be exported."
    if settle_through_revision is not None:
        if not _is_non_negative_int(settle_through_revision):
            return None, "Click receipt settlement revision is invalid."
        _settle_verified_host_mutations(
            ledger["entries"], through_revision=settle_through_revision
        )
    if any(entry["result"]["status"] == "running" for entry in ledger["entries"]):
        return None, "Click cannot export a receipt while a capability claim is active."
    return list(ledger["entries"]), ""
