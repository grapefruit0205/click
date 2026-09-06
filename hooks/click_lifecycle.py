#!/usr/bin/env python3
"""Approval, contract, and completion lifecycle for Click.

This is the top runtime domain beneath host routing. It may coordinate the
lower contract, evidence, observation, mutation, service, verification, and
state domains, but it never imports a host adapter or ``click_gate``.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shlex
import time
from typing import Any

if __package__:
    from . import (
        click_browser,
        click_capability,
        click_claims,
        click_contract,
        click_contract_state,
        click_evidence,
        click_incremental,
        click_mode,
        click_mutation,
        click_observation,
        click_prompt,
        click_runtime_state,
        click_service,
        click_shadow_dashboard,
        click_state,
        click_verification,
    )
else:  # Executed directly from the bundled hooks directory.
    import click_browser
    import click_capability
    import click_claims
    import click_contract
    import click_contract_state
    import click_evidence
    import click_incremental
    import click_mode
    import click_mutation
    import click_observation
    import click_prompt
    import click_runtime_state
    import click_service
    import click_shadow_dashboard
    import click_state
    import click_verification


CONTROL_COMMAND = "click-gate"
CLICK_AUTHORIZATION_PATTERNS = click_prompt.CLICK_AUTHORIZATION_PATTERNS
CONTRACT_ID_PATTERN = re.compile(r"^ctr_[0-9a-f]{32}$")
CONTRACT_STATE_SCHEMA_VERSION = 2
EVIDENCE_RESULT_FIELDS = {"version", "evidence_id"}
PREFERENCE_SCHEMA_VERSION = click_mode.PREFERENCE_SCHEMA_VERSION
PUBLIC_DEFAULT_MODES = click_mode.PUBLIC_DEFAULT_MODES
LEGACY_DEFAULT_MODE_ALIASES = click_mode.LEGACY_DEFAULT_MODE_ALIASES
DEFAULT_MODES = click_mode.DEFAULT_MODES
EPHEMERAL_STATE_TTL_SECONDS = 7 * 24 * 60 * 60
COMPLETED_CONTRACT_TTL_SECONDS = 30 * 24 * 60 * 60

_prompt_digest = click_prompt.prompt_digest
_append_follow_up = click_prompt.append_follow_up
_prompt_authorization = click_prompt.prompt_authorization
_record_user_prompt = click_prompt.record_user_prompt
_read_user_prompt_state = click_prompt.read_user_prompt_state
_read_user_prompt_turn = click_prompt.read_user_prompt_turn
_consume_user_authorization = click_prompt.consume_user_authorization
_active_prompt_turn_error = click_prompt.active_prompt_turn_error
_read_contract_state = click_contract_state.read_contract_state
_clear_contract_state = click_contract_state.clear_contract_state
_save_contract_state = click_contract_state.save_contract_state


def _write_state(
    event: dict[str, Any], status: str, contract_digest: str = ""
) -> None:
    payload = {
        "status": status,
        "contract_digest": contract_digest,
        "updated_at": int(time.time()),
    }
    click_state.write_json(click_state.state_path(event), payload)


def record_control_event(event: dict[str, Any], code: str) -> None:
    """Called within the host's state lock; failures cannot affect admission."""
    try:
        state = _read_contract_state(event)
        if state and click_incremental.record_control_event(state, event, code):
            _save_contract_state(event, state)
    except Exception:
        pass


def _read_state(event: dict[str, Any]) -> dict[str, Any]:
    path = click_state.state_path(event)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"status": "idle"}
    return value if isinstance(value, dict) else {"status": "idle"}


def _evidence_sources(state: dict[str, Any]) -> dict[str, Any] | None:
    """Return the v1 prose-free evidence ledger, or None for legacy state."""
    return click_evidence.sources_from_state(
        state,
        expected_contract_schema_version=CONTRACT_STATE_SCHEMA_VERSION,
    )


def _write_contract_state(
    event: dict[str, Any], status: str, digest: str, contract: dict[str, Any]
) -> str:
    contract_id = f"ctr_{secrets.token_hex(16)}"
    previous = _read_contract_state(event)
    current = {
            "state_schema_version": CONTRACT_STATE_SCHEMA_VERSION,
            "status": status,
            "contract_digest": digest,
            "contract_id": contract_id,
            "staged_turn_id": str(event.get("turn_id", "")),
            "approved_turn_id": "",
            "runtime_mode": "guarded",
            "presentation": click_incremental.contract_presentation(contract),
            "intent_digest": digest,
            "intent_turn_id": str(event.get("turn_id", "")),
            "follow_up_turns": [],
            "history_complete": True,
            "capability_ledger": click_claims.fresh_state(),
            "verification": click_verification.fresh_state(contract),
            "evidence_state": click_evidence.fresh_state(contract),
            "external_evidence": click_evidence.fresh_external_state(contract),
            "observations": click_observation.fresh_state(),
            "mutation": click_mutation.fresh_state(),
            "service": click_service.fresh_state(),
            click_shadow_dashboard.DASHBOARD_FIELD: (
                click_shadow_dashboard.fresh_state()
            ),
            "updated_at": int(time.time()),
        }
    if (
        click_runtime_state.view(previous).guarded_approved
        and _contract_is_completed(previous)
    ):
        _carry_completed_candidates(event, previous, current)
    # Viewer history is deliberately separate from the fresh authority ledger.
    presentations = previous.get("presentation_history", {})
    presentations = dict(presentations) if isinstance(presentations, dict) else {}
    if CONTRACT_ID_PATTERN.fullmatch(str(previous.get("contract_id", ""))):
        presentations[previous["contract_id"]] = click_incremental.sanitize_presentation(previous.get("presentation"))
    current["presentation_history"] = dict(list(presentations.items())[-8:])
    current["verification"][click_incremental.CURRENT_BATCH_FIELD] = None
    _save_contract_state(event, current)
    return contract_id


def _carry_completed_candidates(
    event: dict[str, Any], previous: dict[str, Any], current: dict[str, Any]
) -> None:
    verification = previous.get("verification", {})
    revision = verification.get("mutation_revision", 0)
    _, claim_error = click_claims.receipt_entries(previous, settle_through_revision=revision)
    if claim_error:
        return
    sources = _evidence_sources(previous) or {}
    origins: dict[str, dict[str, str]] = {}
    for batch in click_incremental.batch_history(previous.get("verification")):
        for source in batch["sources"]:
            fact = sources.get(source["source_key"], {})
            if (
                source["status"] in {"passed", "reused"}
                and batch["current_revision"] == revision
                and source["check_digest"] == fact.get("verified_check_digest")
            ):
                origins[source["source_key"]] = {
                    "batch_id": batch["batch_id"],
                    "execution_status": source["status"],
                }
    click_evidence.carry_successor_evidence(
        previous, current, origins=origins,
        scope_digest=click_evidence.successor_scope_digest(
            str(click_state.contract_path(event).resolve())
        ),
        expected_contract_schema_version=CONTRACT_STATE_SCHEMA_VERSION,
    )


def _fresh_evidence_state(
    event: dict[str, Any], *, history_complete: bool = True
) -> dict[str, Any]:
    prompt = _read_user_prompt_state(event)
    turn_id = str(prompt.get("turn_id", "")) or str(event.get("turn_id", ""))
    digest = str(prompt.get("prompt_digest", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        digest = _prompt_digest(event.get("prompt", ""))
    virtual_contract = {
        "verification": {"scale": "focused", "evidence": []}
    }
    return {
        "state_schema_version": CONTRACT_STATE_SCHEMA_VERSION,
        "status": "evidence",
        "runtime_mode": "evidence",
        "evidence_session_id": f"evs_{secrets.token_hex(16)}",
        # Existing receipt, claim, and cache primitives bind this digest. It is
        # an intent digest in Evidence mode, never an approved contract digest.
        "contract_digest": digest,
        "contract_id": "",
        "staged_turn_id": "",
        "approved_turn_id": "",
        "intent_digest": digest,
        "intent_turn_id": turn_id,
        "follow_up_turns": [],
        "history_complete": history_complete,
        "capability_ledger": click_claims.fresh_state(),
        "verification": click_verification.fresh_state(virtual_contract),
        "evidence_state": click_evidence.fresh_state(virtual_contract),
        click_evidence.SUCCESSOR_EVIDENCE_FIELD: (
            click_evidence.fresh_successor_evidence()
        ),
        "external_evidence": click_evidence.fresh_external_state(),
        "observations": click_observation.fresh_state(),
        "mutation": click_mutation.fresh_state(),
        "service": click_service.fresh_state(),
        click_shadow_dashboard.DASHBOARD_FIELD: (
            click_shadow_dashboard.fresh_state()
        ),
        "updated_at": int(time.time()),
    }


def _evidence_state_is_usable(state: dict[str, Any]) -> bool:
    runtime = click_runtime_state.view(state)
    return bool(
        runtime.evidence
        and runtime.runtime_mode == "evidence"
        and runtime.state_schema_version == CONTRACT_STATE_SCHEMA_VERSION
        and re.fullmatch(r"[0-9a-f]{64}", runtime.intent_digest)
        and isinstance(state.get("verification"), dict)
        and _evidence_sources(state) is not None
    )


def _ensure_evidence_state(event: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    state = _read_contract_state(event)
    runtime = click_runtime_state.view(state)
    if runtime.staged or (
        runtime.guarded_approved and not _contract_is_completed(state)
    ):
        return state, False
    recovered = runtime.evidence and not _evidence_state_is_usable(state)
    completed_evidence = bool(
        runtime.evidence
        and _evidence_state_is_usable(state)
        and _contract_is_completed(state)
    )
    if not _evidence_state_is_usable(state) or completed_evidence:
        previous = state
        state = _fresh_evidence_state(event, history_complete=not recovered)
        if completed_evidence:
            _carry_completed_candidates(event, previous, state)
        _save_contract_state(event, state)
        return state, recovered
    if _append_follow_up(event, state):
        _save_contract_state(event, state)
    return state, False


def _contract_id_from_state(state: dict[str, Any]) -> str:
    runtime = click_runtime_state.view(state)
    digest = runtime.contract_digest
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        return ""
    contract_id = runtime.contract_id
    if runtime.contains("contract_id"):
        return (
            contract_id
            if CONTRACT_ID_PATTERN.fullmatch(contract_id)
            else ""
        )
    # Compatibility only for a staged or incomplete state created before ids existed.
    return f"ctr_{digest[:32]}"


def _contract_is_completed(state: dict[str, Any]) -> bool:
    if not click_runtime_state.view(state).execution_authorized:
        return False
    verification = state.get("verification")
    if not isinstance(verification, dict):
        return False
    revision = int(verification.get("mutation_revision", 0))
    sources = _evidence_sources(state)
    if sources is None:
        # Compatibility for an active contract staged before the evidence ledger existed.
        local_verification_passed = bool(
            verification.get("status") == "passed"
            and int(verification.get("verified_revision", -1)) == revision
        )
        if not local_verification_passed:
            return False
        external = state.get("external_evidence")
        if isinstance(external, dict) and external.get("browser_required") is True:
            if external.get("browser_status") != "passed":
                return False
    elif not sources or any(
        not click_evidence.is_current(source, revision)
        for source in sources.values()
    ):
        return False
    service = state.get("service")
    if isinstance(service, dict) and service.get("status") in {
        "starting",
        "launching",
        "running",
        "stopping",
    }:
        return False
    return True


def _approved_contract_is_active(state: dict[str, Any]) -> bool:
    return bool(
        click_runtime_state.view(state).guarded_approved
        and not _contract_is_completed(state)
    )


def _session_contract_is_active(state: dict[str, Any]) -> bool:
    return bool(
        click_runtime_state.view(state).staged
        or _approved_contract_is_active(state)
    )


def _prune_state() -> None:
    root = click_state.state_root()
    if not root.exists():
        return
    now = time.time()
    for candidate in root.glob("*.json"):
        try:
            age = now - candidate.stat().st_mtime
        except (OSError, RuntimeError):
            continue
        ttl = EPHEMERAL_STATE_TTL_SECONDS
        if candidate.name.startswith("session-contract-"):
            try:
                value = json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                value = {}
            if isinstance(value, dict) and _session_contract_is_active(value):
                continue
            if isinstance(value, dict) and _contract_is_completed(value):
                ttl = COMPLETED_CONTRACT_TTL_SECONDS
        if age <= ttl:
            continue
        try:
            candidate.unlink()
        except OSError:
            continue


def _validate_evidence_result(raw: str) -> tuple[str, str]:
    value, error = click_capability.decode_request(raw, "Evidence completion")
    if error:
        return "", error
    assert value is not None
    unknown = sorted(set(value) - EVIDENCE_RESULT_FIELDS)
    if unknown:
        rendered = ", ".join(f"`{field}`" for field in unknown)
        return "", f"Evidence completion contains unsupported field(s): {rendered}."
    evidence_id = value.get("evidence_id")
    if not isinstance(evidence_id, str) or not click_contract.EVIDENCE_ID_PATTERN.fullmatch(
        evidence_id
    ):
        return "", "Evidence completion `evidence_id` must name one declared source."
    return evidence_id, ""


def _control_request(command: str) -> tuple[str | None, str, str]:
    stripped = command.strip()
    receipt_verify_prefix = f"{CONTROL_COMMAND} receipt verify"
    if stripped.startswith(receipt_verify_prefix):
        remainder = stripped[len(receipt_verify_prefix) :]
        if remainder and remainder[0].isspace():
            raw_path = remainder.strip()
            if raw_path and not any(character.isspace() for character in raw_path):
                # Preserve unquoted Windows drive and UNC separators. POSIX shlex
                # treats their backslashes as escapes even when Click is running
                # under cmd or PowerShell.
                return "receipt-verify", raw_path, ""
            if raw_path:
                try:
                    path_tokens = shlex.split(raw_path, posix=True)
                except ValueError as exc:
                    return None, "", f"Malformed {CONTROL_COMMAND} command: {exc}."
                if len(path_tokens) == 1:
                    return "receipt-verify", path_tokens[0], ""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as exc:
        return None, "", f"Malformed {CONTROL_COMMAND} command: {exc}."
    if not tokens or tokens[0] != CONTROL_COMMAND:
        return None, "", ""
    if len(tokens) == 2 and tokens[1] in {"arm", "bypass", "cancel", "review"}:
        return tokens[1], "", ""
    if len(tokens) == 3 and tokens[1] == "default" and tokens[2] in {
        "evidence",
        "guarded",
        "off",
        "on",
        "manual",
        "status",
    }:
        return "default", tokens[2], ""
    if len(tokens) == 3 and tokens[1] == "mode" and tokens[2] in {
        "adaptive",
        "strict",
    }:
        return "mode", tokens[2], ""
    if len(tokens) == 3 and tokens[1:3] == ["receipt", "export"]:
        return "receipt-export", "", ""
    if len(tokens) == 3 and tokens[1] == "observer" and tokens[2] in {
        "off",
        "shadow",
        "status",
    }:
        return "observer", tokens[2], ""
    if len(tokens) == 3 and tokens[1] == "dashboard" and tokens[2] in {
        "start",
        "stop",
        "status",
    }:
        return "dashboard", tokens[2], ""
    if len(tokens) == 4 and tokens[1:3] == ["receipt", "verify"]:
        return "receipt-verify", tokens[3], ""
    if len(tokens) == 3 and tokens[1] in {
        "evidence",
        "inspect",
        "mutate",
        "service",
        "stage",
        "pass",
        "verify",
    }:
        return tokens[1], tokens[2], ""
    return (
        "",
        "",
        f"Use `{CONTROL_COMMAND} arm`, `{CONTROL_COMMAND} stage '<Execution Contract "
        f"JSON>'`, `{CONTROL_COMMAND} pass <contract_id>`, "
        f"`{CONTROL_COMMAND} inspect '<Inspection JSON>'`, "
        f"`{CONTROL_COMMAND} mutate '<Mutation JSON>'`, "
        f"`{CONTROL_COMMAND} service '<Managed Service JSON>'`, "
        f"`{CONTROL_COMMAND} observer off|shadow|status`, "
        f"`{CONTROL_COMMAND} dashboard start|stop|status`, "
        f"`{CONTROL_COMMAND} evidence '<Evidence Completion JSON>'`, "
        f"`{CONTROL_COMMAND} verify '<Verification Batch JSON>'`, "
        f"`{CONTROL_COMMAND} receipt export`, "
        f"`{CONTROL_COMMAND} receipt verify <path>`, "
        f"`{CONTROL_COMMAND} review`, `{CONTROL_COMMAND} bypass`, "
        f"`{CONTROL_COMMAND} cancel`, "
        f"`{CONTROL_COMMAND} default evidence|guarded|off|status` "
        f"(legacy aliases: on|manual), or "
        f"`{CONTROL_COMMAND} mode adaptive|strict`."
    )


def prompt_context(event: dict[str, Any]) -> str:
    _prune_state()
    authorization = _record_user_prompt(event)
    default_mode = click_mode.read_default_mode()
    migrated_from = click_mode.consume_migration_notice()
    contract_state = _read_contract_state(event)
    active_guarded = _session_contract_is_active(contract_state)
    recovered_evidence = False
    if default_mode == "evidence" and not active_guarded:
        contract_state, recovered_evidence = _ensure_evidence_state(event)
    elif (
        click_runtime_state.view(contract_state).guarded_approved
        and _append_follow_up(event, contract_state)
    ):
        _save_contract_state(event, contract_state)

    if default_mode == "guarded" or active_guarded:
        context = (
            "Click Guarded mode is enabled. For software creation, modification, deletion, "
            "or repair, compile the compact Click contract, explain it plainly, ask once, "
            "and do not pass or mutate until a later UserPromptSubmit turn approves the "
            "staged contract_id. Questions, "
            "explanations, and simple read-only inspection do not need a contract. For a "
            "read-only code review, run `click-gate review` before shell reads/searches; "
            "do not stage a build contract, reuse exact successful evidence, and prefer "
            "focused follow-up after broad repository context. During review or approved "
            "implementation use versioned `click-gate inspect`, `click-gate mutate`, and "
            "`click-gate verify` version-2 evidence-bound argv requests when direct Bash "
            "intent is ambiguous; use `click-gate evidence` to finalize an observed "
            "Browser source or attest a collected hosted, manual, or existing source; use "
            "`click-gate service` start/stop for a recognizable long-running local server. "
            "Browser MCP work requires one referenced verification evidence source with "
            "kind `browser`; calls remain serial and receipt-bound while repeat and timing "
            "guidance is advisory. Use "
            "`click-gate bypass` only when the user explicitly opts out for the current turn. "
            "Present the exact digest-bound easy contract once as the default approval "
            "body. Keep the original canonical JSON hidden unless the user asks for it, "
            "and treat viewing it as neither approval nor a replacement stage. Offer "
            "approve, request changes, cancel, and view-original choices. An in-scope "
            "detail or narrowing instruction continues under the same contract and is recorded "
            "as a follow-up turn; require a new contract only when outcome, boundary, must-hold "
            "behavior, or verification commitment changes."
        )
    elif default_mode == "off":
        context = (
            "Click Off mode is enabled. Apply the Guarded contract workflow only when "
            "the user explicitly selects @Click or $click. Ordinary software work and "
            "code review remain fail-open unless explicitly activated. Once activated, a "
            "staged or incomplete approved session contract remains mutation-locked across "
            "later turns. Stage the contract JSON once, then pass only its emitted "
            "contract_id after a later UserPromptSubmit turn. Approved Browser evidence is "
            "metered and long-running "
            "local servers use `click-gate service` start/stop."
        )
    else:
        context = (
            "Click Evidence mode is enabled. Do not ask for a Click approval contract for "
            "ordinary software work. The host remains the execution authority; Click records "
            "intent lineage, host-observed mutation revisions, exact verification receipts, "
            "and cache lineage without claiming auto-approval. Prefer structured `click-gate "
            "inspect`, `click-gate verify`, and managed service capabilities when their exact "
            "receipts are useful, but never block ordinary host work merely because Evidence "
            "state is missing or recoverable. Use @Click or $click to opt one task into "
            "Guarded approval, or `click-gate default guarded` for a persistent choice."
        )
    if migrated_from:
        migrated_label = {
            "evidence": "Evidence",
            "guarded": "Guarded",
            "off": "Off",
        }.get(default_mode, default_mode)
        context += (
            f" Click migrated the previous `{migrated_from}` preference to "
            f"{migrated_label} once, preserving the prior authority choice. No past "
            "approval was recreated. Any already active Guarded contract remains locked "
            "until completion or explicit cancel."
        )
    if recovered_evidence:
        context += (
            " Click recovered malformed Evidence state by starting a new lower-assurance "
            "session. Earlier unobserved history is excluded from its receipt; ordinary host "
            "work remains available."
        )
    contract_id = _contract_id_from_state(contract_state)
    contract_status = click_runtime_state.view(contract_state).status
    contract_completed = _contract_is_completed(contract_state)
    contract_sources = (
        _evidence_sources(contract_state)
        if contract_status in {"staged", "approved"}
        else {}
    )
    if (
        contract_status in {"staged", "approved"}
        and not contract_completed
        and contract_sources is None
    ):
        context += (
            " The active contract predates evidence-id completion tracking and cannot "
            "be resumed safely. Do not pass it. Ask the user to start a turn with "
            "`@Click cancel`, run `click-gate cancel`, then stage and approve a fresh "
            "contract."
        )
    elif (
        contract_status in {"staged", "approved"}
        and not contract_completed
        and not contract_sources
    ):
        context += (
            " The active contract evidence state is unavailable or malformed. Do not "
            "pass it. Ask the user to start a turn with `@Click cancel`, run "
            "`click-gate cancel`, then stage and approve a fresh contract."
        )
    elif contract_status == "staged" and contract_id:
        context += (
            f" The active staged contract_id is `{contract_id}`. Treat that id as the "
            "approval target. If and only if this user response explicitly approves the "
            f"shown proposal, pass it with `click-gate pass {contract_id}`; never resend "
            "the contract JSON."
        )
    elif _approved_contract_is_active(contract_state) and contract_id:
        context += (
            f" The incomplete approved contract_id is `{contract_id}`. To resume its "
            f"implementation in this turn, use `click-gate pass {contract_id}` after "
            "passing the same id; do not restage or resend the JSON."
        )
    if authorization:
        context += (
            f" The user's exact first-line `@Click {authorization}` directive authorizes "
            f"one `click-gate {authorization}` in this turn only. Do not reuse that "
            "authorization in another tool call or later turn."
        )
    return context


def stage_contract(event: dict[str, Any], raw: str) -> tuple[str, str]:
    contract, validation_error = click_contract.validate_contract(raw)
    if validation_error:
        return "", validation_error
    assert contract is not None
    canonical = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    _prune_state()

    current_status = _read_state(event).get("status")
    strict = click_mode.read_mode(event) == "strict"
    guarded = click_mode.read_default_mode() == "guarded"
    prompt_turn_error = _active_prompt_turn_error(event)
    if prompt_turn_error:
        return "", prompt_turn_error
    current_turn_id = str(event.get("turn_id", ""))
    if (
        current_status not in {"armed", "staged", "passed"}
        and not strict
        and not guarded
    ):
        return "", "Arm Click before staging the execution contract for approval."
    existing_contract = _read_contract_state(event)
    if (
        existing_contract.get("status") == "staged"
        and existing_contract.get("contract_digest") == digest
    ):
        existing_id = _contract_id_from_state(existing_contract)
        return (
            "",
            "The identical Click execution contract is already staged. "
            f"Its contract_id is `{existing_id}`; pass that id after the "
            "user's approval instead of staging it again.",
        )
    if (
        existing_contract.get("status") == "staged"
        and existing_contract.get("staged_turn_id") == current_turn_id
    ):
        return (
            "",
            "Click already staged a contract in this user turn. Show that "
            "exact proposal and wait; a revised contract may be staged only "
            "after the user's next response.",
        )
    if (
        existing_contract.get("status") == "approved"
        and not _contract_is_completed(existing_contract)
    ):
        return (
            "",
            "Click is already executing one approved contract. Do not restage, "
            "replan, or replace it mid-run. Finish every declared source for "
            "its current revision before staging the next contract. If the "
            "approved outcome or authority is no longer sufficient, stop and "
            "report the blocker.",
        )
    contract_id = _write_contract_state(event, "staged", digest, contract)
    _write_state(event, "staged", digest)
    return f"echo CLICK_CONTRACT_ID={contract_id}", ""


def pass_contract(event: dict[str, Any], contract_id: str) -> tuple[str, str]:
    if not CONTRACT_ID_PATTERN.fullmatch(contract_id):
        if contract_id.lstrip().startswith("{"):
            return (
                "",
                "Click pass accepts the staged `contract_id`, not the Execution "
                "Contract JSON. Use `click-gate pass ctr_<32 hex characters>` "
                "after the later approval response.",
            )
        return (
            "",
            "Click `contract_id` must use `ctr_` followed by exactly 32 "
            "lowercase hexadecimal characters.",
        )
    _prune_state()

    current_status = _read_state(event).get("status")
    strict = click_mode.read_mode(event) == "strict"
    guarded = click_mode.read_default_mode() == "guarded"
    prompt_turn_error = _active_prompt_turn_error(event)
    if prompt_turn_error:
        return "", prompt_turn_error
    current_turn_id = str(event.get("turn_id", ""))
    if current_status != "armed" and not strict and not guarded:
        return (
            "",
            "Arm Click in the current turn before passing the approved "
            "execution contract.",
        )
    staged = _read_contract_state(event)
    if staged.get("status") not in {"staged", "approved"}:
        return "", "No staged Click execution contract is available for approval."
    if staged.get("status") == "staged":
        staged_turn_id = str(staged.get("staged_turn_id", ""))
        if not staged_turn_id or staged_turn_id == current_turn_id:
            return (
                "",
                "Click requires one separate user response after the contract is "
                "staged. Show the proposal now and pass it only from the next "
                "UserPromptSubmit turn.",
            )
    elif _contract_is_completed(staged):
        return (
            "",
            "This Click contract already completed its current-revision evidence. Stage a "
            "fresh contract and obtain a new user response before another mutation.",
        )
    staged_sources = _evidence_sources(staged)
    if staged_sources is None:
        return (
            "",
            "This staged Click contract predates evidence-id completion "
            "tracking. Cancel it, stage the proposal again, and obtain fresh "
            "approval instead of passing an unrecoverable contract.",
        )
    if not staged_sources:
        return (
            "",
            "The staged Click evidence state is unavailable or malformed. "
            "Cancel it, stage the proposal again, and obtain fresh approval.",
        )
    staged_digest = staged.get("contract_digest")
    if not isinstance(staged_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", staged_digest
    ):
        return (
            "",
            "The staged Click contract digest is unavailable or invalid. Cancel "
            "it explicitly, then stage and show the contract again.",
        )
    expected_id = _contract_id_from_state(staged)
    if not expected_id:
        return (
            "",
            "The staged Click contract has no recoverable contract_id. Cancel "
            "it explicitly, then stage and show the contract again.",
        )
    if contract_id != expected_id:
        record_control_event(event, "contract-id-mismatch")
        return (
            "",
            "The contract_id differs from the proposal staged for user approval. "
            "Pass the exact id emitted by stage, or replace the proposal before "
            "approval and show the new easy contract again.",
        )
    if staged.get("status") == "staged":
        staged["approved_turn_id"] = current_turn_id
    staged["status"] = "approved"
    staged["contract_id"] = expected_id
    _save_contract_state(event, staged)
    _write_state(event, "passed", staged_digest)
    return "echo Click mutation gate passed", ""


def record_evidence_completion(
    event: dict[str, Any], raw: str
) -> tuple[str, str]:
    evidence_id, error = _validate_evidence_result(raw)
    if error:
        return "", error
    state = _read_contract_state(event)
    if not click_runtime_state.view(state).execution_authorized:
        return "", "Start Guarded or Evidence runtime state before recording evidence."
    verification = state.get("verification")
    if not isinstance(verification, dict):
        return "", "Click verification state is unavailable; stage and approve again."
    if verification.get("status") == "running":
        return "", "Wait for the final argv verification batch before recording evidence."
    mutation = state.get("mutation")
    if click_mutation.is_running(mutation):
        return "", "Wait for the structured Click mutation before recording evidence."

    sources = _evidence_sources(state)
    if sources is None:
        return (
            "",
            "This active contract predates evidence-id completion tracking. Cancel it, "
            "stage the proposal again, and obtain fresh approval.",
        )
    if not sources:
        return "", "Click evidence state is unavailable or malformed; cancel and restage."
    source_key = click_evidence.evidence_key(evidence_id)
    source = sources.get(source_key)
    if not isinstance(source, dict):
        return "", f"Evidence completion references unknown id `{evidence_id}`."
    kind = str(source.get("kind", ""))
    if kind == "argv":
        return (
            "",
            f"Evidence `{evidence_id}` has kind `argv`; execute it through "
            "`click-gate verify` instead of attesting it.",
        )

    revision = int(verification.get("mutation_revision", 0))
    if click_evidence.is_current(source, revision):
        return (
            "",
            f"Evidence `{evidence_id}` already completed for the current revision; "
            "reuse it instead of recording it twice.",
        )
    if kind == "browser":
        browser_error = click_browser.finalize_evidence(
            state,
            evidence_id=evidence_id,
            source_key=source_key,
            source=source,
            revision=revision,
        )
        if browser_error:
            return "", browser_error
    elif kind not in {"hosted", "manual", "existing"}:
        return "", f"Evidence `{evidence_id}` has unsupported completion kind `{kind}`."

    source["status"] = "passed"
    source["verified_revision"] = revision
    source["attempts"] = int(source.get("attempts", 0)) + 1
    source["last_exit_code"] = 0
    source["verified_at"] = int(time.time()) or 1
    tool_use_id = str(event.get("tool_use_id", ""))
    if not tool_use_id:
        return "", "Click evidence completion requires a stable host tool_use_id."
    request_digest = click_claims.host_request_digest(event)
    binding_digest = click_claims.host_binding_digest(tool_use_id)
    _, claim_error = click_claims.record_claim(
        state,
        capability="evidence-attestation",
        claim_mode="host-tool-use",
        request_digest=request_digest,
        binding_digest=binding_digest,
        mutation_revision=revision,
        claimed_at=int(time.time()) or 1,
    )
    if claim_error or not click_claims.complete_claim(
        state,
        capability="evidence-attestation",
        claim_mode="host-tool-use",
        request_digest=request_digest,
        binding_digest=binding_digest,
        mutation_revision=revision,
        exit_code=0,
    ):
        return "", claim_error or "Click could not complete its evidence claim safely."
    _save_contract_state(event, state)
    return f"echo Click evidence {evidence_id} completed for revision {revision}", ""


write_state = _write_state
read_state = _read_state
write_mode = click_mode.write_mode
read_mode = click_mode.read_mode
write_default_mode = click_mode.write_default_mode
read_default_mode = click_mode.read_default_mode
consume_migration_notice = click_mode.consume_migration_notice
evidence_sources = _evidence_sources
write_contract_state = _write_contract_state
contract_id_from_state = _contract_id_from_state
read_contract_state = click_contract_state.read_contract_state
clear_contract_state = click_contract_state.clear_contract_state
save_contract_state = click_contract_state.save_contract_state
prompt_authorization = click_prompt.prompt_authorization
record_user_prompt = click_prompt.record_user_prompt
read_user_prompt_state = click_prompt.read_user_prompt_state
read_user_prompt_turn = click_prompt.read_user_prompt_turn
consume_user_authorization = click_prompt.consume_user_authorization
active_prompt_turn_error = click_prompt.active_prompt_turn_error
contract_is_completed = _contract_is_completed
approved_contract_is_active = _approved_contract_is_active
session_contract_is_active = _session_contract_is_active
ensure_evidence_state = _ensure_evidence_state
prune_state = _prune_state
validate_evidence_result = _validate_evidence_result
control_request = _control_request
