"""Browser evidence admission and result-receipt transitions for Click.

This module owns observable Browser authorization: the assigned evidence source,
serial tool_use_id binding, bounded receipt history, current revision, and
PostToolUse result accounting. Workflow suggestions remain in the independent
``click_browser_advisory`` leaf and cannot grant or deny Browser authority.
"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import time
from typing import Any

if __package__:
    from . import (
        click_browser_advisory,
        click_claims,
        click_contract_state,
        click_evidence,
        click_state,
    )
else:  # Executed directly from the bundled hooks directory.
    import click_browser_advisory
    import click_claims
    import click_contract_state
    import click_evidence
    import click_state


MAX_UNIQUE_INPUTS = 256
RUNNING_TTL_SECONDS = 40


ContractCompleted = Callable[[dict[str, Any]], bool]
MutationRunning = Callable[[Any], bool]


_read_contract_state = click_contract_state.read_contract_state


def _read_turn_state(event: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(click_state.state_path(event).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"status": "idle"}
    return value if isinstance(value, dict) else {"status": "idle"}


_save_contract_state = click_contract_state.save_contract_state


def _sources(
    state: dict[str, Any], *, expected_contract_schema_version: int
) -> dict[str, Any] | None:
    return click_evidence.sources_from_state(
        state,
        expected_contract_schema_version=expected_contract_schema_version,
    )


def input_error(tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return "Browser evidence requires an object tool input."
    return ""


def running_expires_at(tool_input: Any, started_at: float) -> float:
    declared_seconds = (
        click_browser_advisory.longest_declared_runtime_ms(tool_input) / 1000.0
    )
    return started_at + max(RUNNING_TTL_SECONDS, declared_seconds + 10.0)


def running_entry_is_active(entry: Any, now: float) -> bool:
    if isinstance(entry, (int, float)) and not isinstance(entry, bool):
        return now - float(entry) <= RUNNING_TTL_SECONDS
    if not isinstance(entry, dict):
        return False
    expires_at = entry.get("expires_at")
    if isinstance(expires_at, (int, float)) and not isinstance(expires_at, bool):
        return now <= float(expires_at)
    started_at = entry.get("started_at")
    return bool(
        isinstance(started_at, (int, float))
        and not isinstance(started_at, bool)
        and now - float(started_at) <= RUNNING_TTL_SECONDS
    )


def _capability_digest(request: dict[str, Any]) -> str:
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def attempt_digest(tool_input: Any) -> str:
    if isinstance(tool_input, dict) and isinstance(tool_input.get("code"), str):
        code = str(tool_input["code"]).replace("\r\n", "\n").strip()
        return _capability_digest({"code": code})
    if isinstance(tool_input, dict):
        semantic = {
            key: value
            for key, value in tool_input.items()
            if key not in {"_meta", "annotations", "timeout", "timeout_ms"}
        }
        return _capability_digest({"tool_input": semantic})
    return _capability_digest({"tool_input": tool_input})


def response_failed(response: Any) -> bool:
    if not isinstance(response, dict):
        return True
    if response.get("isError") is True or response.get("is_error") is True:
        return True
    if "status" in response:
        status = str(response.get("status", "")).lower()
        return status not in {
            "complete",
            "completed",
            "ok",
            "pass",
            "passed",
            "success",
            "succeeded",
        }

    # MCP responses may omit a status while still returning structured content.
    # Empty containers, empty strings, and null acknowledgements prove nothing.
    def meaningful(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (bytes, bytearray)):
            return bool(value)
        if isinstance(value, dict):
            metadata_keys = {
                "_meta",
                "annotations",
                "mimeType",
                "mime_type",
                "role",
                "type",
            }
            return any(
                meaningful(item)
                for key, item in value.items()
                if key not in metadata_keys
            )
        if isinstance(value, (list, tuple, set)):
            return any(meaningful(item) for item in value)
        return True

    return not any(
        meaningful(response.get(key)) for key in {"content", "output", "result"}
    )


def prepare(
    event: dict[str, Any],
    *,
    expected_contract_schema_version: int,
    contract_is_completed: ContractCompleted,
    mutation_is_running: MutationRunning,
) -> tuple[bool, str, str]:
    state = _read_contract_state(event)
    if state.get("status") not in {"staged", "approved"}:
        return False, "", ""
    if state.get("status") != "approved":
        return (
            True,
            "Approve the staged Click contract before collecting browser evidence.",
            "",
        )
    if contract_is_completed(state):
        if _read_turn_state(event).get("status") != "passed":
            return False, "", ""
        return (
            True,
            "The approved Click contract is complete. Reuse its evidence instead of "
            "starting a shadow browser verification session.",
            "",
        )
    external = state.get("external_evidence")
    if not isinstance(external, dict) or external.get("browser_required") is not True:
        return (
            True,
            "Browser work has no referenced verification evidence source with kind "
            "`browser` in this contract. Use the cheaper assigned source instead of "
            "adding shadow verification.",
            "",
        )
    sources = _sources(
        state,
        expected_contract_schema_version=expected_contract_schema_version,
    )
    if sources is None:
        return (
            True,
            "This active contract predates evidence-id completion tracking. Cancel it, "
            "stage the proposal again, and obtain fresh approval.",
            "",
        )
    source_key = str(external.get("browser_source_key", ""))
    source = sources.get(source_key) if sources else None
    if not isinstance(source, dict) or source.get("kind") != "browser":
        return True, "Click Browser evidence state is unavailable or malformed.", ""
    mutation = state.get("mutation")
    if mutation_is_running(mutation):
        return (
            True,
            "Wait for the structured mutation to finish before browser evidence.",
            "",
        )
    verification = state.get("verification")
    if isinstance(verification, dict) and verification.get("status") == "running":
        return (
            True,
            "Wait for the final argv verification batch before browser evidence.",
            "",
        )
    revision = (
        int(verification.get("mutation_revision", 0))
        if isinstance(verification, dict)
        else 0
    )
    if click_evidence.is_current(source, revision):
        return (
            True,
            "The assigned Browser evidence already completed for the current revision. "
            "Reuse it instead of replaying the session.",
            "",
        )
    running = external.get("browser_running")
    if isinstance(running, dict) and running:
        already_observed = bool(
            external.get("browser_status") == "observed"
            or source.get("status") == "observed"
        )
        now = time.time()
        if any(
            running_entry_is_active(running_entry, now)
            for running_entry in running.values()
        ):
            return (
                True,
                "One browser evidence call is already running; keep the session serial.",
                "",
            )
        ledger, ledger_error = click_claims.validate_ledger(
            state.get("capability_ledger")
        )
        if ledger_error or ledger is None:
            return (
                True,
                "Click could not safely close the expired Browser capability claim.",
                "",
            )
        expired_claims: list[tuple[str, str, int]] = []
        for tool_use_id, running_entry in running.items():
            binding_digest = click_claims.host_binding_digest(str(tool_use_id))
            matches = [
                entry
                for entry in ledger["entries"]
                if entry["capability"] == "browser"
                and entry["claim_mode"] == "host-tool-use"
                and entry["binding_digest"] == binding_digest
                and entry["result"]["status"] == "running"
            ]
            if len(matches) != 1:
                return (
                    True,
                    "Click could not safely match the expired Browser capability claim.",
                    "",
                )
            request_digest = str(matches[0]["request_digest"])
            stored_digest = (
                str(running_entry.get("attempt_digest", ""))
                if isinstance(running_entry, dict)
                else ""
            )
            if stored_digest and stored_digest != request_digest:
                return (
                    True,
                    "Click expired Browser state did not match its capability claim.",
                    "",
                )
            expired_claims.append(
                (
                    request_digest,
                    binding_digest,
                    int(matches[0]["mutation_revision"]),
                )
            )
        if not all(
            click_claims.complete_claim(
                state,
                capability="browser",
                claim_mode="host-tool-use",
                request_digest=request_digest,
                binding_digest=binding_digest,
                mutation_revision=claimed_revision,
                exit_code=124,
            )
            for request_digest, binding_digest, claimed_revision in expired_claims
        ):
            return (
                True,
                "Click could not safely record the expired Browser capability claim.",
                "",
            )
        attempts = external.get("browser_attempts")
        if not isinstance(attempts, dict):
            attempts = {}
        for running_entry in running.values():
            if not isinstance(running_entry, dict):
                continue
            digest = str(running_entry.get("attempt_digest", ""))
            attempt = attempts.get(digest)
            if isinstance(attempt, dict) and attempt.get("status") == "running":
                attempt["status"] = "failed"
                attempt["failed_attempts"] = int(
                    attempt.get("failed_attempts", 0)
                ) + 1
        external["browser_running"] = {}
        external["browser_attempts"] = attempts
        external["browser_status"] = "observed" if already_observed else "failed"
        external["last_browser_error"] = "post-tool-timeout"
        if not already_observed:
            source["status"] = "failed"
            source["verified_revision"] = -1
            source["last_exit_code"] = 124
        state["external_evidence"] = external
        _save_contract_state(event, state)
    tool_input_error = input_error(event.get("tool_input"))
    if tool_input_error:
        return True, tool_input_error, ""
    advisories = list(
        click_browser_advisory.input_advisories(event.get("tool_input"))
    )
    tool_use_id = str(event.get("tool_use_id", ""))
    if not tool_use_id:
        return (
            True,
            "Browser evidence requires a stable tool_use_id for PostToolUse accounting.",
            "",
        )
    digest = attempt_digest(event.get("tool_input"))
    attempts = external.get("browser_attempts")
    if not isinstance(attempts, dict):
        attempts = {}
    prior_attempt = attempts.get(digest)
    repeat_advisory = click_browser_advisory.repeat_advisory(prior_attempt)
    if repeat_advisory:
        advisories.append(repeat_advisory)
    unchanged_retries = 0
    if isinstance(prior_attempt, dict):
        prior_status = str(prior_attempt.get("status", ""))
        unchanged_retries = int(prior_attempt.get("unchanged_retries", 0))
        if prior_status in {"failed", "incomplete"}:
            unchanged_retries += 1
    previous_successes = (
        int(prior_attempt.get("successful_attempts", 0))
        if isinstance(prior_attempt, dict)
        else 0
    )
    if (
        isinstance(prior_attempt, dict)
        and prior_attempt.get("status") == "success"
        and previous_successes == 0
    ):
        previous_successes = 1
    previous_failures = (
        int(prior_attempt.get("failed_attempts", 0))
        if isinstance(prior_attempt, dict)
        else 0
    )
    if isinstance(prior_attempt, dict):
        attempts.pop(digest, None)
    compacted = False
    while len(attempts) >= MAX_UNIQUE_INPUTS:
        attempts.pop(next(iter(attempts)))
        compacted = True
    if compacted:
        advisories.append(
            "Click advisory: older Browser attempt guidance was compacted to keep "
            "receipt state bounded. This call remains tracked, and the current source "
            "and revision receipt are unchanged."
        )
    already_observed = bool(
        external.get("browser_status") == "observed"
        or source.get("status") == "observed"
    )
    attempts[digest] = {
        "status": "running",
        "attempts": int(prior_attempt.get("attempts", 0)) + 1
        if isinstance(prior_attempt, dict)
        else 1,
        "unchanged_retries": unchanged_retries,
        "successful_attempts": previous_successes,
        "failed_attempts": previous_failures,
    }
    calls = int(external.get("browser_calls", 0))
    external["browser_calls"] = calls + 1
    external["browser_status"] = "observed" if already_observed else "running"
    started_at = time.time()
    external["browser_running"] = {
        tool_use_id: {
            "started_at": started_at,
            "expires_at": running_expires_at(event.get("tool_input"), started_at),
            "attempt_digest": digest,
        }
    }
    external["browser_attempts"] = attempts
    external["last_browser_error"] = ""
    if not already_observed:
        source["status"] = "running"
    _, claim_error = click_claims.record_claim(
        state,
        capability="browser",
        claim_mode="host-tool-use",
        request_digest=digest,
        binding_digest=click_claims.host_binding_digest(tool_use_id),
        mutation_revision=revision,
        claimed_at=int(started_at) or 1,
    )
    if claim_error:
        return True, claim_error, ""
    state["external_evidence"] = external
    _save_contract_state(event, state)
    return True, "", "\n".join(advisories)


def record_result(
    event: dict[str, Any], *, expected_contract_schema_version: int
) -> None:
    state = _read_contract_state(event)
    if state.get("status") != "approved":
        return
    external = state.get("external_evidence")
    if not isinstance(external, dict):
        return
    running = external.get("browser_running")
    tool_use_id = str(event.get("tool_use_id", ""))
    if not isinstance(running, dict) or tool_use_id not in running:
        return
    running_entry = running.pop(tool_use_id)
    if isinstance(running_entry, dict):
        started_at = float(running_entry.get("started_at", 0.0))
        digest = str(running_entry.get("attempt_digest", ""))
    else:
        started_at = float(running_entry)
        digest = attempt_digest(event.get("tool_input"))
    duration = max(0.0, time.time() - started_at)
    total = float(external.get("browser_seconds", 0.0)) + duration
    external["browser_seconds"] = round(total, 3)
    external["browser_running"] = running
    sources = _sources(
        state,
        expected_contract_schema_version=expected_contract_schema_version,
    )
    source_key = str(external.get("browser_source_key", ""))
    source = sources.get(source_key) if sources else None
    verification = state.get("verification")
    revision = (
        int(verification.get("mutation_revision", 0))
        if isinstance(verification, dict)
        else 0
    )
    attempts = external.get("browser_attempts")
    if not isinstance(attempts, dict):
        attempts = {}
    attempt = attempts.get(digest)
    if not isinstance(attempt, dict):
        attempt = {
            "attempts": 1,
            "unchanged_retries": 0,
            "successful_attempts": 0,
            "failed_attempts": 0,
        }
        attempts[digest] = attempt
    if response_failed(event.get("tool_response")):
        exit_code = 1
        attempt["status"] = "failed"
        attempt["failed_attempts"] = int(attempt.get("failed_attempts", 0)) + 1
        already_observed = bool(
            external.get("browser_status") == "observed"
            or isinstance(source, dict)
            and source.get("status") == "observed"
        )
        external["browser_status"] = "observed" if already_observed else "failed"
        external["last_browser_error"] = "tool-error"
        if isinstance(source, dict) and not already_observed:
            source["status"] = "failed"
            source["verified_revision"] = -1
            source["last_exit_code"] = 1
    else:
        exit_code = 0
        attempt["status"] = "success"
        attempt["successful_attempts"] = int(
            attempt.get("successful_attempts", 0)
        ) + 1
        external["browser_status"] = "observed"
        external["last_browser_error"] = ""
        if isinstance(source, dict):
            source["status"] = "observed"
            source["verified_revision"] = revision
            source["last_exit_code"] = 0
    if not click_claims.complete_claim(
        state,
        capability="browser",
        claim_mode="host-tool-use",
        request_digest=digest,
        binding_digest=click_claims.host_binding_digest(tool_use_id),
        mutation_revision=revision,
        exit_code=exit_code,
    ):
        return
    external["browser_attempts"] = attempts
    state["external_evidence"] = external
    _save_contract_state(event, state)


def finalize_evidence(
    state: dict[str, Any],
    *,
    evidence_id: str,
    source_key: str,
    source: dict[str, Any],
    revision: int,
) -> str:
    external = state.get("external_evidence")
    browser_running = (
        external.get("browser_running") if isinstance(external, dict) else None
    )
    if isinstance(browser_running, dict) and browser_running:
        return "Wait for the running Browser interaction before finalizing evidence."
    if (
        not isinstance(external, dict)
        or external.get("browser_source_key") != source_key
        or external.get("browser_status") != "observed"
        or source.get("status") != "observed"
        or int(source.get("verified_revision", -1)) != revision
    ):
        return (
            f"Browser evidence `{evidence_id}` can complete only after a successful "
            "current-revision Browser call in its metered session."
        )
    external["browser_status"] = "passed"
    state["external_evidence"] = external
    return ""
