"""Approved mutation admission, revision invalidation, and runner receipts.

The module binds one direct argv mutation to the approved contract state before
execution and records the exact claimed result afterward. It may depend on
state and evidence leaves, but host routing, Browser policy, services,
observation execution, and verification strategy stay outside this boundary.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
from typing import Any

if __package__:
    from . import (
        click_claims,
        click_contract_state,
        click_dependency_cache,
        click_evidence,
        click_runtime_state,
        click_state,
    )
else:  # Executed directly from the bundled hooks directory.
    import click_claims
    import click_contract_state
    import click_dependency_cache
    import click_evidence
    import click_runtime_state
    import click_state


REQUEST_FIELDS = {"version", "argv"}
RUNNING_TTL_SECONDS = 10 * 60


ValidateArgv = Callable[[Any, str], tuple[list[str] | None, str]]
ManagedServicePredicate = Callable[[list[str]], bool]
WorkspaceSnapshot = Callable[[Path], dict[str, Any] | None]
ObservationIsRunning = Callable[[Any], bool]
RenderRunnerCommand = Callable[[list[str]], str]
ExecuteCommands = Callable[[list[list[str]]], int]


def fresh_boundary() -> dict[str, Any]:
    return {
        "revision": 0,
        "tool_use_id": "",
        "claim_mode": "",
        "claim_request_digest": "",
        "claim_binding_digest": "",
        "status": "none",
        "lineage_valid": False,
        "before_root": "",
        "before_digest": "",
        "after_root": "",
        "after_digest": "",
    }


def fresh_state() -> dict[str, Any]:
    return {
        "status": "idle",
        "request_digest": "",
        "runner_token_digest": "",
        "runner_claimed_at": 0,
        "started_at": 0,
        "last_exit_code": None,
    }


def is_running(mutation: Any) -> bool:
    if not isinstance(mutation, dict) or mutation.get("status") != "running":
        return False
    # Expiry cannot prove that a claimed child has stopped. Keep a claimed
    # mutation active until it records a result or the user explicitly cancels.
    if mutation.get("runner_claimed_at"):
        return True
    started_at = int(mutation.get("started_at", 0))
    return bool(
        started_at and time.time() - started_at <= RUNNING_TTL_SECONDS
    )


def _decode_request(
    raw: str, *, protocol_version: int
) -> tuple[dict[str, Any] | None, str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None, "Mutation request must be valid JSON."
    if not isinstance(value, dict):
        return None, "Mutation request must be a JSON object."
    if value.get("version") != protocol_version:
        return None, f"Mutation request `version` must be {protocol_version}."
    return value, ""


def validate_request(
    raw: str,
    *,
    validate_argv: ValidateArgv,
    looks_like_managed_service: ManagedServicePredicate,
    protocol_version: int,
) -> tuple[dict[str, Any] | None, str]:
    value, error = _decode_request(raw, protocol_version=protocol_version)
    if error:
        return None, error
    assert value is not None
    unknown = sorted(set(value) - REQUEST_FIELDS)
    if unknown:
        rendered = ", ".join(f"`{field}`" for field in unknown)
        return None, f"Mutation request contains unsupported field(s): {rendered}."
    argv, argv_error = validate_argv(value.get("argv"), "Mutation")
    if argv_error:
        return None, argv_error
    assert argv is not None
    if looks_like_managed_service(argv):
        return (
            None,
            "Long-running local servers must use `click-gate service` so Click owns "
            "the exact child lifecycle and cannot strand a foreground mutation.",
        )
    return {"version": protocol_version, "argv": argv}, ""


_read_contract_state = click_contract_state.read_contract_state
_save_contract_state = click_contract_state.save_contract_state


def _sources(
    state: dict[str, Any], *, expected_contract_schema_version: int
) -> dict[str, Any] | None:
    return click_evidence.sources_from_state(
        state,
        expected_contract_schema_version=expected_contract_schema_version,
    )


def mark_contract_mutated(
    event: dict[str, Any],
    *,
    expected_contract_schema_version: int,
    observation_is_running: ObservationIsRunning,
    workspace_snapshot: WorkspaceSnapshot,
    host_tool_use: bool = True,
) -> str:
    state = _read_contract_state(event)
    runtime = click_runtime_state.view(state)
    if not runtime.execution_authorized:
        return ""
    mutation = state.get("mutation")
    if is_running(mutation):
        return "Click blocked a second mutation while a structured mutation is running."
    if isinstance(mutation, dict) and mutation.get("status") == "running":
        state["mutation"] = fresh_state()
    verification = state.get("verification")
    if not isinstance(verification, dict):
        return "Click verification state is unavailable; stage and approve the contract again."
    if verification.get("status") == "running":
        return "Click blocked this mutation while the final verification batch is running."
    sources = _sources(
        state,
        expected_contract_schema_version=expected_contract_schema_version,
    )
    if sources is None:
        return (
            "This active contract predates evidence-id completion tracking. Cancel it, "
            "stage the proposal again, and obtain fresh approval before mutation."
        )
    if not sources and runtime.guarded_approved:
        return (
            "Click evidence state is unavailable or malformed; cancel and stage the "
            "contract again before changing the implementation."
        )

    observations = state.get("observations")
    if isinstance(observations, dict):
        entries = observations.get("entries")
        if isinstance(entries, dict):
            for entry in entries.values():
                if observation_is_running(entry):
                    return (
                        "Click blocked this mutation while an approved read or search is "
                        "running. Wait for that evidence before changing the implementation."
                    )

    tool_use_id = str(event.get("tool_use_id", ""))
    if host_tool_use and not tool_use_id:
        return "Click requires a stable host tool_use_id for a direct mutation claim."
    previous_revision = int(verification.get("mutation_revision", 0))
    workspace = Path(str(event.get("cwd", ""))).resolve()
    snapshot = workspace_snapshot(workspace)
    snapshot_root = (
        os.path.normcase(str(snapshot.get("root", "")))
        if isinstance(snapshot, dict)
        else ""
    )
    snapshot_digest = (
        str(snapshot.get("digest", "")) if isinstance(snapshot, dict) else ""
    )
    reusable_sources = [
        source
        for source in sources.values()
        if isinstance(source, dict)
        and source.get("verified_dependency_provider")
        in click_dependency_cache.PROVIDER_NAMES
        and isinstance(source.get("verified_revision"), int)
        and not isinstance(source.get("verified_revision"), bool)
        and int(source.get("verified_revision", -1)) >= 0
    ]
    current_receipts = [
        source
        for source in reusable_sources
        if source.get("status") == "passed"
        and int(source.get("verified_revision", -1)) == previous_revision
    ]
    prior_boundary = verification.get("mutation_boundary")
    if current_receipts:
        lineage_valid = bool(
            snapshot_root
            and snapshot_digest
            and all(
                source.get("verified_root") == snapshot_root
                and source.get("verified_tree_digest") == snapshot_digest
                for source in current_receipts
            )
        )
    elif reusable_sources:
        lineage_valid = bool(
            isinstance(prior_boundary, dict)
            and prior_boundary.get("status") == "recorded"
            and prior_boundary.get("lineage_valid") is True
            and prior_boundary.get("revision") == previous_revision
            and prior_boundary.get("after_root") == snapshot_root
            and prior_boundary.get("after_digest") == snapshot_digest
        )
    else:
        lineage_valid = bool(snapshot_root and snapshot_digest)

    revision = previous_revision + 1
    verification["mutation_revision"] = revision
    claim_request_digest = (
        click_claims.host_request_digest(event) if host_tool_use else ""
    )
    claim_binding_digest = (
        click_claims.host_binding_digest(tool_use_id) if host_tool_use else ""
    )
    verification["mutation_boundary"] = {
        "revision": revision,
        "tool_use_id": tool_use_id,
        "claim_mode": "host-tool-use" if host_tool_use else "",
        "claim_request_digest": claim_request_digest,
        "claim_binding_digest": claim_binding_digest,
        "status": (
            "running"
            if tool_use_id and lineage_valid and snapshot_root and snapshot_digest
            else "invalid"
        ),
        "lineage_valid": lineage_valid,
        "before_root": snapshot_root,
        "before_digest": snapshot_digest,
        "after_root": "",
        "after_digest": "",
    }
    if verification.get("status") == "passed":
        verification["status"] = "stale"
    elif verification.get("status") == "failed":
        verification["status"] = "ready"
        verification["failed_revision"] = -1
        verification["unchanged_failure_retries"] = 0
        verification["workspace_changed"] = False
    state["verification"] = verification
    for source in sources.values():
        if not isinstance(source, dict):
            continue
        # Several edits before verification still share the last successful
        # baseline. Retain it as stale, never as a current passing result.
        has_baseline = source.get("status") in {"passed", "stale"} and (
            click_evidence.revision_is_valid(source.get("verified_revision"))
        )
        source["status"] = "stale" if has_baseline else "ready"
        source["unchanged_failure_retries"] = 0
        source["last_exit_code"] = None
        if not source.get("locked_check_digest"):
            source["last_check_digest"] = ""
    external = state.get("external_evidence")
    browser_required = bool(
        isinstance(external, dict) and external.get("browser_required") is True
    )
    browser_source_key = (
        str(external.get("browser_source_key", ""))
        if isinstance(external, dict)
        else ""
    )
    if not browser_source_key and isinstance(external, dict):
        legacy_source_id = str(external.get("browser_source_id", ""))
        browser_source_key = (
            click_evidence.evidence_key(legacy_source_id) if legacy_source_id else ""
        )
    state["external_evidence"] = click_evidence.fresh_external_state(
        required=browser_required,
        source_key=browser_source_key,
    )
    state["observations"] = {"entries": {}}
    if host_tool_use:
        _, claim_error = click_claims.record_claim(
            state,
            capability="mutation",
            claim_mode="host-tool-use",
            request_digest=claim_request_digest,
            binding_digest=claim_binding_digest,
            mutation_revision=revision,
            claimed_at=int(time.time()) or 1,
        )
        if claim_error:
            return claim_error
    _save_contract_state(event, state)
    return ""


def _capability_digest(request: dict[str, Any]) -> str:
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _encoded_request(request: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
    ).decode()


def _decode_encoded_request(encoded: str) -> tuple[str, str]:
    try:
        return base64.urlsafe_b64decode(encoded.encode()).decode(), ""
    except (ValueError, UnicodeDecodeError):
        return "", "Click mutation runner received an invalid request."


def _runner_prefix(action: str, runner_script: Path) -> list[str]:
    return [
        sys.executable,
        str(runner_script.resolve()),
        "--state-root",
        str(click_state.state_root().resolve()),
        action,
    ]


def runner_command(
    event: dict[str, Any],
    request: dict[str, Any],
    request_digest: str,
    runner_token: str,
    *,
    runner_script: Path,
    render_command: RenderRunnerCommand,
) -> str:
    arguments = [
        *_runner_prefix("run-mutation", runner_script),
        str(click_state.contract_path(event).resolve()),
        request_digest,
        runner_token,
        _encoded_request(request),
    ]
    return render_command(arguments)


def prepare(
    event: dict[str, Any],
    raw: str,
    *,
    validate_argv: ValidateArgv,
    looks_like_managed_service: ManagedServicePredicate,
    protocol_version: int,
    expected_contract_schema_version: int,
    observation_is_running: ObservationIsRunning,
    workspace_snapshot: WorkspaceSnapshot,
    runner_script: Path,
    render_command: RenderRunnerCommand,
) -> tuple[str, str]:
    state = _read_contract_state(event)
    if not click_runtime_state.view(state).execution_authorized:
        return "", "Start Guarded or Evidence runtime state before mutation."
    request, error = validate_request(
        raw,
        validate_argv=validate_argv,
        looks_like_managed_service=looks_like_managed_service,
        protocol_version=protocol_version,
    )
    if error:
        return "", error
    assert request is not None
    mutation_error = mark_contract_mutated(
        event,
        expected_contract_schema_version=expected_contract_schema_version,
        observation_is_running=observation_is_running,
        workspace_snapshot=workspace_snapshot,
        host_tool_use=False,
    )
    if mutation_error:
        return "", mutation_error

    state = _read_contract_state(event)
    request_digest = _capability_digest(request)
    runner_token = secrets.token_urlsafe(24)
    state["mutation"] = {
        "status": "running",
        "request_digest": request_digest,
        "runner_token_digest": hashlib.sha256(runner_token.encode()).hexdigest(),
        "runner_claimed_at": 0,
        "started_at": int(time.time()),
        "last_exit_code": None,
    }
    _save_contract_state(event, state)
    return (
        runner_command(
            event,
            request,
            request_digest,
            runner_token,
            runner_script=runner_script,
            render_command=render_command,
        ),
        "",
    )


def _managed_contract_path(path: Path) -> bool:
    return click_state.managed_state_path(path, ("session-contract-",))


def _reservation_is_fresh(value: Any, ttl_seconds: int) -> bool:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return False
    age = time.time() - value
    return 0 <= age <= ttl_seconds


def claim_run(
    path: Path,
    raw: str,
    request_digest: str,
    runner_token: str,
    *,
    validate_argv: ValidateArgv,
    looks_like_managed_service: ManagedServicePredicate,
    protocol_version: int,
) -> tuple[dict[str, Any] | None, str]:
    """Atomically authorize one mutation runner before any side effect."""
    if not _managed_contract_path(path):
        return None, "Click mutation runner received an unmanaged state path."
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None, "Click mutation runner could not read its contract state."
    if not click_runtime_state.view(state).execution_authorized:
        return None, "Click mutation runner is no longer authorized to execute."
    mutation = state.get("mutation")
    if not isinstance(mutation, dict) or mutation.get("status") != "running":
        return None, "Click mutation runner is no longer authorized to execute."
    if mutation.get("request_digest") != request_digest:
        return None, "Click mutation runner request digest did not match active state."
    token_digest = hashlib.sha256(runner_token.encode()).hexdigest()
    if not secrets.compare_digest(
        str(mutation.get("runner_token_digest", "")), token_digest
    ):
        return None, "Click mutation runner token did not match active state."
    claimed_at = mutation.get("runner_claimed_at", 0)
    if not isinstance(claimed_at, int) or isinstance(claimed_at, bool):
        return None, "Click mutation runner claim state is malformed."
    if claimed_at:
        return None, "Click mutation runner was already claimed; replay is blocked."
    if not _reservation_is_fresh(
        mutation.get("started_at", 0), RUNNING_TTL_SECONDS
    ):
        return None, "Click mutation runner authorization expired before execution."

    request, error = validate_request(
        raw,
        validate_argv=validate_argv,
        looks_like_managed_service=looks_like_managed_service,
        protocol_version=protocol_version,
    )
    if error:
        return None, error
    assert request is not None
    if _capability_digest(request) != request_digest:
        return None, "Click mutation runner request digest did not match."

    verification = state.get("verification")
    mutation_revision = (
        int(verification.get("mutation_revision", 0))
        if isinstance(verification, dict)
        else 0
    )
    claimed_at = int(time.time()) or 1
    _, claim_error = click_claims.record_claim(
        state,
        capability="mutation",
        claim_mode="one-use-runner",
        request_digest=request_digest,
        token_digest=token_digest,
        mutation_revision=mutation_revision,
        claimed_at=claimed_at,
    )
    if claim_error:
        return None, claim_error
    mutation["runner_claimed_at"] = claimed_at
    state["mutation"] = mutation
    state["updated_at"] = int(time.time())
    click_state.write_json(path, state)
    return request, ""


def record_result(
    path: Path, request_digest: str, runner_token: str, exit_code: int
) -> bool:
    if not _managed_contract_path(path):
        return False
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    if not click_runtime_state.view(state).execution_authorized:
        return False
    mutation = state.get("mutation")
    if not isinstance(mutation, dict) or mutation.get("status") != "running":
        return False
    if mutation.get("request_digest") != request_digest:
        return False
    token_digest = hashlib.sha256(runner_token.encode()).hexdigest()
    if not secrets.compare_digest(
        str(mutation.get("runner_token_digest", "")), token_digest
    ):
        return False
    claimed_at = mutation.get("runner_claimed_at", 0)
    if (
        not isinstance(claimed_at, int)
        or isinstance(claimed_at, bool)
        or not claimed_at
    ):
        return False
    verification = state.get("verification")
    mutation_revision = (
        int(verification.get("mutation_revision", 0))
        if isinstance(verification, dict)
        else 0
    )
    if not click_claims.complete_claim(
        state,
        capability="mutation",
        claim_mode="one-use-runner",
        request_digest=request_digest,
        mutation_revision=mutation_revision,
        exit_code=exit_code,
    ):
        return False
    mutation.update(
        {
            "status": "passed" if exit_code == 0 else "failed",
            "runner_token_digest": "",
            "runner_claimed_at": 0,
            "started_at": 0,
            "last_exit_code": exit_code,
        }
    )
    state["mutation"] = mutation
    state["updated_at"] = int(time.time())
    click_state.write_json(path, state)
    return True


def run(
    arguments: list[str],
    *,
    validate_argv: ValidateArgv,
    looks_like_managed_service: ManagedServicePredicate,
    protocol_version: int,
    execute_commands: ExecuteCommands,
) -> int:
    if len(arguments) != 4:
        sys.stderr.write(
            "usage: click_gate.py run-mutation <state> <digest> <token> <request>\n"
        )
        return 2
    state_path = Path(arguments[0])
    request_digest, runner_token, encoded = arguments[1:]
    raw, error = _decode_encoded_request(encoded)
    if error:
        sys.stderr.write(f"{error}\n")
        return 2
    with click_state.state_lock():
        request, error = claim_run(
            state_path,
            raw,
            request_digest,
            runner_token,
            validate_argv=validate_argv,
            looks_like_managed_service=looks_like_managed_service,
            protocol_version=protocol_version,
        )
    if error:
        sys.stderr.write(f"{error}\n")
        return 2
    assert request is not None
    exit_code = execute_commands([request["argv"]])
    with click_state.state_lock():
        recorded = record_result(
            state_path, request_digest, runner_token, exit_code
        )
    if not recorded:
        sys.stderr.write("Click could not record the mutation result safely.\n")
        return exit_code or 2
    return exit_code


def record_boundary(
    event: dict[str, Any], *, workspace_snapshot: WorkspaceSnapshot
) -> None:
    state = _read_contract_state(event)
    if not click_runtime_state.view(state).execution_authorized:
        return
    verification = state.get("verification")
    if not isinstance(verification, dict):
        return
    boundary = verification.get("mutation_boundary")
    if (
        not isinstance(boundary, dict)
        or boundary.get("status") != "running"
        or boundary.get("tool_use_id") != str(event.get("tool_use_id", ""))
        or boundary.get("revision") != verification.get("mutation_revision")
    ):
        return
    snapshot = workspace_snapshot(Path(str(event.get("cwd", ""))).resolve())
    if snapshot is None:
        boundary["status"] = "invalid"
        boundary["lineage_valid"] = False
    else:
        boundary["status"] = "recorded"
        boundary["after_root"] = os.path.normcase(str(snapshot.get("root", "")))
        boundary["after_digest"] = str(snapshot.get("digest", ""))
    if boundary.get("claim_mode") == "host-tool-use":
        if not click_claims.complete_claim(
            state,
            capability="mutation",
            claim_mode="host-tool-use",
            request_digest=str(boundary.get("claim_request_digest", "")),
            binding_digest=str(boundary.get("claim_binding_digest", "")),
            mutation_revision=int(verification.get("mutation_revision", 0)),
            exit_code=None,
        ):
            boundary["status"] = "invalid"
            boundary["lineage_valid"] = False
    verification["mutation_boundary"] = boundary
    state["verification"] = verification
    _save_contract_state(event, state)
