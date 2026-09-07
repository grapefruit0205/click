"""Observation reservations, one-use claims, and read-result receipts for Click.

The module binds an approved or review-state read request to one runner token,
records bounded output metadata, and blocks replay. Read-only command policy and
execution live in click_inspection; filesystem identity lives in click_state.
"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import re
import secrets
import sys
import tempfile
import time
from typing import Any

if __package__:
    from . import (
        click_capability,
        click_claims,
        click_contract_state,
        click_inspection,
        click_observation_cache,
        click_process,
        click_state,
    )
else:  # Executed directly from the bundled hooks directory.
    import click_capability
    import click_claims
    import click_contract_state
    import click_inspection
    import click_observation_cache
    import click_process
    import click_state


MAX_OUTPUT_BYTES = 48_000
MAX_ENTRIES = 64
RESERVATION_TTL_SECONDS = 30


MutationIsRunning = Callable[[Any], bool]
FreshMutationState = Callable[[], dict[str, Any]]
RenderRunnerCommand = Callable[[list[str]], str]
ExecuteInspectionCommands = Callable[..., int]
RunInspectionRequest = Callable[[dict[str, Any], tuple[Path, str, str] | None], int]


def fresh_state() -> dict[str, Any]:
    return {"entries": {}}


def _nonnegative_count(value: Any) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


def unclaimed_reservation_is_fresh(value: Any, ttl_seconds: int) -> bool:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return False
    age = time.time() - value
    return 0 <= age <= ttl_seconds


def is_running(entry: Any) -> bool:
    if not isinstance(entry, dict) or entry.get("status") != "running":
        return False
    claimed_at = entry.get("runner_claimed_at", 0)
    if not isinstance(claimed_at, int) or isinstance(claimed_at, bool):
        return True
    if claimed_at > 0:
        # The wrapper can exit while its isolated read child remains alive.
        # Neither its PID nor elapsed time proves that the command stopped.
        return True
    started_at = entry.get("started_at", 0)
    if not isinstance(started_at, int) or isinstance(started_at, bool):
        return True
    if started_at <= 0 or time.time() < started_at:
        return True
    return unclaimed_reservation_is_fresh(started_at, RESERVATION_TTL_SECONDS)


def write_review_state(event: dict[str, Any]) -> None:
    click_state.write_json(
        click_state.review_path(event),
        {
            "status": "review",
            "observations": fresh_state(),
            "updated_at": int(time.time()),
        },
    )


def read_review_state(event: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(click_state.review_path(event).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"status": "none"}
    return value if isinstance(value, dict) else {"status": "none"}


def save_review_state(event: dict[str, Any], state: dict[str, Any]) -> None:
    state["updated_at"] = int(time.time())
    click_state.write_json(click_state.review_path(event), state)


def clear_review_state(event: dict[str, Any]) -> None:
    try:
        click_state.review_path(event).unlink()
    except OSError:
        pass


_read_contract_state = click_contract_state.read_contract_state
_save_contract_state = click_contract_state.save_contract_state


def runner_command(
    state_path: Path,
    request: dict[str, Any],
    request_digest: str,
    runner_token: str,
    *,
    runner_script: Path,
    render_command: RenderRunnerCommand,
) -> str:
    arguments = [
        sys.executable,
        str(runner_script),
        "--state-root",
        str(click_state.state_root().resolve()),
        "run-observation",
        str(state_path.resolve()),
        request_digest,
        runner_token,
        click_capability.encode_request(request),
    ]
    return render_command(arguments)


def prepare(
    event: dict[str, Any],
    request: dict[str, Any],
    broad_inventory: bool,
    *,
    review: bool = False,
    mutation_is_running: MutationIsRunning,
    fresh_mutation_state: FreshMutationState,
    runner_script: Path,
    render_command: RenderRunnerCommand,
) -> tuple[str, str, str]:
    state_path = (
        click_state.review_path(event) if review else click_state.contract_path(event)
    )
    if review:
        state = read_review_state(event)
        if state.get("status") != "review":
            return "", "Click review state is unavailable; activate review mode again.", ""
        revision = 0
    else:
        state = _read_contract_state(event)
        if state.get("status") not in {"approved", "evidence"}:
            return "", "Click observation runtime state is unavailable.", ""
        mutation = state.get("mutation")
        if mutation_is_running(mutation):
            return "", "Wait for the structured Click mutation to finish before inspection.", ""
        if isinstance(mutation, dict) and mutation.get("status") == "running":
            state["mutation"] = fresh_mutation_state()
        verification = state.get("verification")
        if not isinstance(verification, dict):
            return "", "Click verification state is unavailable; approve the contract again.", ""
        if verification.get("status") == "running":
            return "", "The final Click verification batch is already running.", ""
        revision = int(verification.get("mutation_revision", 0))

    observations = state.get("observations")
    if not isinstance(observations, dict):
        observations = fresh_state()
    entries = observations.get("entries")
    if not isinstance(entries, dict):
        entries = {}

    digest = click_capability.digest(request)
    advisories: list[str] = []
    if broad_inventory:
        prior_broad_success = False
        prior_broad_running = False
        for existing_digest, existing in entries.items():
            if not (
                existing_digest != digest
                and isinstance(existing, dict)
                and existing.get("broad_inventory") is True
                and int(existing.get("revision", -1)) == revision
            ):
                continue
            existing_status = str(existing.get("status", ""))
            if existing_status in {"success", "reused"}:
                prior_broad_success = True
            elif existing_status == "running" and is_running(existing):
                prior_broad_running = True
        if prior_broad_success:
            advisories.append(
                "Click advisory: a repository-wide inventory already completed for this "
                "revision. This additional broad inventory is allowed through the same "
                "read-only runner, but reuse existing results or narrow the query when "
                "practical."
            )
        elif prior_broad_running:
            advisories.append(
                "Click advisory: another repository-wide inventory is already running for "
                "this revision. This distinct broad inventory is allowed through the same "
                "read-only runner, but waiting or narrowing avoids redundant work."
            )

    prior = entries.get(digest)
    unchanged_retries = 0
    if isinstance(prior, dict) and int(prior.get("revision", -1)) == revision:
        status = str(prior.get("status", ""))
        unchanged_retries = int(prior.get("unchanged_retries", 0))
        if status == "success":
            advisories.append(
                "Click advisory: this identical read or search already succeeded for the "
                "current revision. A fresh, separately authorized one-use runner is "
                "allowed, but reuse the existing result or narrow the query when practical."
            )
        if status == "running":
            if is_running(prior):
                return (
                    "",
                    "An exact observation runner for this request is already active. Wait "
                    "for it to record a result before issuing a fresh authorization.",
                    "",
                )
            status = "failed"
        if status in {"failed", "incomplete"}:
            if unchanged_retries >= 1:
                advisories.append(
                    "Click advisory: this identical read or search already failed or "
                    "produced incomplete output twice for the current revision. A fresh, "
                    "separately authorized retry is allowed, but repair, narrow, or change "
                    "the request when practical."
                )
            unchanged_retries += 1

    runner_token = secrets.token_urlsafe(24)
    entries[digest] = {
        "revision": revision,
        "status": "running",
        "attempts": int(prior.get("attempts", 0)) + 1 if isinstance(prior, dict) else 1,
        "unchanged_retries": unchanged_retries,
        "runner_token_digest": hashlib.sha256(runner_token.encode()).hexdigest(),
        "runner_claimed_at": 0,
        "started_at": int(time.time()),
        "last_exit_code": None,
        "output_bytes": 0,
        "broad_inventory": broad_inventory,
        "actual_process_executed": None,
        "cache_key": "",
        "cache_status": "pending",
        "cache_reuse_count": (
            _nonnegative_count(prior.get("cache_reuse_count", 0))
            if isinstance(prior, dict)
            and int(prior.get("revision", -1)) == revision
            else 0
        ),
        "cache_notice_shown": bool(
            prior.get("cache_notice_shown", False)
            if isinstance(prior, dict)
            and int(prior.get("revision", -1)) == revision
            else False
        ),
    }
    while len(entries) > MAX_ENTRIES:
        entries.pop(next(iter(entries)))
    observations["entries"] = entries
    state["observations"] = observations
    if review:
        save_review_state(event, state)
    else:
        _save_contract_state(event, state)
    return (
        runner_command(
            state_path,
            request,
            digest,
            runner_token,
            runner_script=runner_script,
            render_command=render_command,
        ),
        "",
        "\n".join(advisories),
    )


def managed_path(path: Path) -> bool:
    return click_state.managed_state_path(path, ("session-contract-", "review-"))


def claim_run(
    path: Path,
    raw: str,
    command_digest: str,
    runner_token: str,
    *,
    protocol_version: int = click_capability.PROTOCOL_VERSION,
) -> tuple[dict[str, Any] | None, str]:
    """Atomically authorize one observation runner before any read executes."""
    if not managed_path(path):
        return None, "Click observation runner received an unmanaged state path."
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None, "Click observation runner could not read its managed state."
    status = state.get("status")
    if status not in {"approved", "evidence", "review"}:
        return None, "Click observation runner is no longer authorized to execute."
    observations = state.get("observations")
    if not isinstance(observations, dict):
        return None, "Click observation state is unavailable or malformed."
    entries = observations.get("entries")
    if not isinstance(entries, dict):
        return None, "Click observation state is unavailable or malformed."
    entry = entries.get(command_digest)
    if not isinstance(entry, dict) or entry.get("status") != "running":
        return None, "Click observation runner is no longer authorized to execute."

    expected_revision = 0
    if status in {"approved", "evidence"}:
        verification = state.get("verification")
        if not isinstance(verification, dict):
            return None, "Click observation revision state is unavailable."
        mutation_revision = verification.get("mutation_revision", 0)
        if not isinstance(mutation_revision, int) or isinstance(mutation_revision, bool):
            return None, "Click observation revision state is malformed."
        expected_revision = mutation_revision
    if entry.get("revision") != expected_revision:
        return None, "Click observation runner revision is stale."

    token_digest = hashlib.sha256(runner_token.encode()).hexdigest()
    if not secrets.compare_digest(str(entry.get("runner_token_digest", "")), token_digest):
        return None, "Click observation runner token did not match active state."
    claimed_at = entry.get("runner_claimed_at", 0)
    if not isinstance(claimed_at, int) or isinstance(claimed_at, bool):
        return None, "Click observation runner claim state is malformed."
    if claimed_at:
        return None, "Click observation runner was already claimed; replay is blocked."
    if not unclaimed_reservation_is_fresh(entry.get("started_at", 0), RESERVATION_TTL_SECONDS):
        return None, "Click observation runner authorization expired before execution."

    request, _, error = click_inspection.validate_request(
        raw, protocol_version=protocol_version
    )
    if error:
        return None, error
    assert request is not None
    if click_capability.digest(request) != command_digest:
        return None, "Click observation runner request digest did not match."

    claimed_at = int(time.time()) or 1
    if status in {"approved", "evidence"}:
        _, claim_error = click_claims.record_claim(
            state,
            capability="observation",
            claim_mode="one-use-runner",
            request_digest=command_digest,
            token_digest=token_digest,
            mutation_revision=expected_revision,
            claimed_at=claimed_at,
        )
        if claim_error:
            return None, claim_error
    entry["runner_claimed_at"] = claimed_at
    entries[command_digest] = entry
    observations["entries"] = entries
    state["observations"] = observations
    state["updated_at"] = int(time.time())
    click_state.write_json(path, state)
    return request, ""


def record_result(
    path: Path,
    command_digest: str,
    runner_token: str,
    exit_code: int,
    output_bytes: int,
    incomplete: bool,
    *,
    cache_reused: bool = False,
    cache_key: str = "",
    cache_notice_shown: bool = False,
    cache_status: str = "",
) -> bool:
    if not managed_path(path):
        return False
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    if state.get("status") not in {"approved", "evidence", "review"}:
        return False
    observations = state.get("observations")
    if not isinstance(observations, dict):
        return False
    entries = observations.get("entries")
    if not isinstance(entries, dict):
        return False
    entry = entries.get(command_digest)
    if not isinstance(entry, dict) or entry.get("status") != "running":
        return False
    token_digest = hashlib.sha256(runner_token.encode()).hexdigest()
    if not secrets.compare_digest(str(entry.get("runner_token_digest", "")), token_digest):
        return False
    claimed_at = entry.get("runner_claimed_at", 0)
    if (
        not isinstance(claimed_at, int)
        or isinstance(claimed_at, bool)
        or claimed_at <= 0
    ):
        return False

    status = state.get("status")
    if status in {"approved", "evidence"}:
        verification = state.get("verification")
        if not isinstance(verification, dict) or not click_claims.complete_claim(
            state,
            capability="observation",
            claim_mode="one-use-runner",
            request_digest=command_digest,
            mutation_revision=int(verification.get("mutation_revision", 0)),
            exit_code=exit_code,
        ):
            return False
    entry["runner_token_digest"] = ""
    entry["runner_claimed_at"] = 0
    entry["started_at"] = 0
    entry["last_exit_code"] = exit_code
    entry["output_bytes"] = output_bytes
    if exit_code != 0:
        entry["status"] = "failed"
    elif incomplete:
        entry["status"] = "incomplete"
    elif cache_reused:
        entry["status"] = "reused"
    else:
        entry["status"] = "success"
    previous_reuse_count = entry.get("cache_reuse_count", 0)
    if not isinstance(previous_reuse_count, int) or isinstance(
        previous_reuse_count, bool
    ) or previous_reuse_count < 0:
        previous_reuse_count = 0
    entry["actual_process_executed"] = not cache_reused
    entry["cache_key"] = (
        cache_key
        if isinstance(cache_key, str)
        and re.fullmatch(r"[0-9a-f]{64}", cache_key)
        else ""
    )
    entry["cache_reuse_count"] = previous_reuse_count + int(cache_reused)
    entry["cache_notice_shown"] = bool(
        entry.get("cache_notice_shown") or cache_notice_shown
    )
    entry["cache_status"] = (
        cache_status
        if isinstance(cache_status, str)
        and re.fullmatch(r"[a-z-]{1,32}", cache_status)
        else "unavailable"
    )
    entries[command_digest] = entry
    observations["entries"] = entries
    state["observations"] = observations
    state["updated_at"] = int(time.time())
    click_state.write_json(path, state)
    return True


def cache_notice_needed(path: Path, command_digest: str) -> bool:
    """Return whether this revision has not yet shown its cache-hit notice."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    observations = state.get("observations")
    entries = observations.get("entries") if isinstance(observations, dict) else None
    entry = entries.get(command_digest) if isinstance(entries, dict) else None
    return bool(
        isinstance(entry, dict)
        and entry.get("status") == "running"
        and entry.get("cache_notice_shown") is not True
    )


def run_request(
    request: dict[str, Any],
    state_result: tuple[Path, str, str] | None = None,
    *,
    execute_commands: ExecuteInspectionCommands,
) -> int:
    commands = request["commands"]
    recorded_result = False
    receipt_unavailable = False
    try:
        descriptor: dict[str, Any] | None = None
        cache_hit: dict[str, Any] | None = None
        if state_result is not None:
            try:
                descriptor, cache_hit = click_observation_cache.load(
                    request, Path.cwd()
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                descriptor, cache_hit = None, None
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            cache_reused = cache_hit is not None
            if cache_hit is not None:
                stdout_file.write(cache_hit["stdout_data"])
                stderr_file.write(cache_hit["stderr_data"])
                exit_code = 0
            else:
                exit_code = execute_commands(commands, stdout_file, stderr_file)
            output_bytes = stdout_file.tell() + stderr_file.tell()
            incomplete = output_bytes > MAX_OUTPUT_BYTES
            cache_key = ""
            cache_status = "reused" if cache_reused else "ineligible"
            if (
                not cache_reused
                and exit_code == 0
                and not incomplete
                and descriptor is not None
            ):
                stdout_file.seek(0)
                stderr_file.seek(0)
                stdout_data = stdout_file.read()
                stderr_data = stderr_file.read()
                try:
                    cache_key, cache_status = click_observation_cache.store_with_status(
                        request,
                        Path.cwd(),
                        descriptor,
                        stdout_data,
                        stderr_data,
                    )
                except (OSError, RuntimeError, TypeError, ValueError):
                    cache_key = ""
                    cache_status = "storage-error"
            elif cache_hit is not None:
                cache_key = str(cache_hit.get("key", ""))

            show_cache_notice = False
            if state_result is not None:
                state_path, request_digest, runner_token = state_result
                try:
                    with click_state.state_lock():
                        show_cache_notice = bool(
                            cache_reused
                            and cache_notice_needed(state_path, request_digest)
                        )
                        recorded = record_result(
                            state_path,
                            request_digest,
                            runner_token,
                            exit_code,
                            output_bytes,
                            incomplete,
                            cache_reused=cache_reused,
                            cache_key=cache_key,
                            cache_notice_shown=show_cache_notice,
                            cache_status=cache_status,
                        )
                except OSError as exc:
                    recorded = False
                    sys.stderr.write(f"Click observation result storage is unavailable: {exc}\n")
                if not recorded:
                    receipt_unavailable = True
                    sys.stderr.write(
                        "Click could not record the observation result safely. Output "
                        "is preserved, but no successful receipt is available.\n"
                    )
                recorded_result = recorded

            stdout_file.seek(0)
            stderr_file.seek(0)
            if cache_hit is not None and show_cache_notice:
                sys.stderr.write(click_observation_cache.notice(cache_hit))
            remaining = MAX_OUTPUT_BYTES
            if exit_code == 0:
                remaining -= click_process.copy_limited_output(
                    stdout_file, sys.stdout.buffer, remaining
                )
                click_process.copy_limited_output(stderr_file, sys.stderr.buffer, remaining)
            else:
                remaining -= click_process.copy_limited_output(
                    stderr_file, sys.stderr.buffer, remaining
                )
                click_process.copy_limited_output(stdout_file, sys.stdout.buffer, remaining)
            if incomplete:
                sys.stderr.write(
                    "\n[Click] Read/search output exceeded 48,000 bytes. Narrow or "
                    "paginate the next command; one unchanged retry is available.\n"
                )
    except OSError as exc:
        if state_result is not None and not recorded_result:
            state_path, request_digest, runner_token = state_result
            try:
                with click_state.state_lock():
                    recorded = record_result(
                        state_path, request_digest, runner_token, 127, 0, False
                    )
            except OSError as storage_error:
                recorded = False
                sys.stderr.write(
                    f"Click observation failure storage is unavailable: {storage_error}\n"
                )
            if not recorded:
                sys.stderr.write("Click could not record the observation failure safely.\n")
        sys.stderr.write(f"Click observation runner failed: {exc}\n")
        return 127
    return (exit_code or 2) if receipt_unavailable else exit_code


def run(
    arguments: list[str],
    *,
    run_inspection_request: RunInspectionRequest,
    protocol_version: int = click_capability.PROTOCOL_VERSION,
) -> int:
    if len(arguments) != 4:
        sys.stderr.write(
            "usage: click_gate.py run-observation <state> <digest> <token> <request>\n"
        )
        return 2
    state_path = Path(arguments[0])
    request_digest, runner_token, encoded = arguments[1:]
    raw, error = click_capability.decode_encoded_request(encoded, "observation")
    if error:
        sys.stderr.write(f"{error}\n")
        return 2
    try:
        with click_state.state_lock():
            request, error = claim_run(
                state_path,
                raw,
                request_digest,
                runner_token,
                protocol_version=protocol_version,
            )
    except OSError as exc:
        # The host may place the runner in a stricter filesystem sandbox than
        # the preparation hook. Never execute using an unpersisted claim.
        sys.stderr.write(
            f"Click observation claim storage is unavailable: {exc}. "
            "No read was executed and no receipt was recorded.\n"
        )
        return 2
    if error:
        sys.stderr.write(f"{error}\n")
        return 2
    assert request is not None
    return run_inspection_request(
        request, (state_path, request_digest, runner_token)
    )
