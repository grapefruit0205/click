#!/usr/bin/env python3
"""Managed local-service admission, state, and runner lifecycle for Click.

This module owns only the recognizable development-service capability. It may
depend on the state and shell-free process leaves, but it must not import the
gate, contract lifecycle, evidence, observation, Browser, or verification
layers. The caller supplies the one cross-domain transition that marks an
approved contract mutated before a service starts.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from typing import Any

if __package__:
    from . import click_claims, click_contract_state, click_process, click_state
else:  # Executed from the bundled hooks directory.
    import click_claims
    import click_contract_state
    import click_process
    import click_state


SERVICE_REQUEST_FIELDS = {"version", "action", "argv"}
MANAGED_SERVICE_EXECUTABLES = {
    "flask",
    "gunicorn",
    "http-server",
    "next",
    "serve",
    "uvicorn",
    "vite",
    "webpack-dev-server",
}
MANAGED_SERVICE_ACTIONS = {"start", "stop"}
MANAGED_SERVICE_SCRIPT_MARKERS = {
    "dev",
    "preview",
    "runserver",
    "serve",
    "start",
}
SERVICE_START_TIMEOUT_SECONDS = 8
SERVICE_STOP_TIMEOUT_SECONDS = 8
MANAGED_SERVICE_MAX_SECONDS = 2 * 60 * 60


ValidateArgv = Callable[[Any, str], tuple[list[str] | None, str]]
MarkContractMutated = Callable[[dict[str, Any]], str]
RenderRunnerCommand = Callable[[list[str]], str]
ExecutionArgv = Callable[[list[str]], list[str]]
SnapshotReader = Callable[[Path, str], dict[str, Any] | None]


def fresh_state() -> dict[str, Any]:
    return {
        "status": "idle",
        "service_id": "",
        "request_digest": "",
        "runner_token_digest": "",
        "runner_claimed_at": 0,
        "supervisor_claimed_at": 0,
        "stop_requested": False,
        "supervisor_pid": 0,
        "child_pid": 0,
        "started_at": 0,
        "last_exit_code": None,
        "stop_claim_request_digest": "",
        "stop_claim_binding_digest": "",
    }


def looks_like_managed_service(argv: list[str]) -> bool:
    executable = Path(argv[0]).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    arguments = [argument.lower() for argument in argv[1:]]
    if executable in MANAGED_SERVICE_EXECUTABLES:
        return True
    if executable == "py" or re.fullmatch(r"(?:python|pypy)\d*(?:\.\d+)?", executable):
        if executable == "py" and arguments and re.fullmatch(
            r"-\d+(?:\.\d+)?(?:-\d+)?", arguments[0]
        ):
            arguments = arguments[1:]
        if len(arguments) >= 2 and arguments[:2] == ["-m", "http.server"]:
            return True
        return any(marker in arguments for marker in {"runserver"})
    if executable in {"npm", "pnpm", "yarn", "bun"}:
        meaningful = {
            argument
            for argument in arguments
            if argument not in {"run", "exec", "x", "--"}
            and not argument.startswith("-")
        }
        return bool(meaningful & MANAGED_SERVICE_SCRIPT_MARKERS)
    if executable in {"npx", "pnpx", "bunx"}:
        return any(
            Path(argument).name.lower() in MANAGED_SERVICE_EXECUTABLES
            for argument in arguments
            if not argument.startswith("-")
        )
    return any(marker in arguments for marker in {"runserver"})


def _decode_request(
    raw: str, *, protocol_version: int
) -> tuple[dict[str, Any] | None, str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None, "Managed service request must be valid JSON."
    if not isinstance(value, dict):
        return None, "Managed service request must be a JSON object."
    if value.get("version") != protocol_version:
        return (
            None,
            f"Managed service request `version` must be {protocol_version}.",
        )
    return value, ""


def validate_request(
    raw: str,
    *,
    validate_argv: ValidateArgv,
    protocol_version: int,
) -> tuple[dict[str, Any] | None, str]:
    value, error = _decode_request(raw, protocol_version=protocol_version)
    if error:
        return None, error
    assert value is not None
    unknown = sorted(set(value) - SERVICE_REQUEST_FIELDS)
    if unknown:
        rendered = ", ".join(f"`{field}`" for field in unknown)
        return None, f"Managed service request contains unsupported field(s): {rendered}."
    action = value.get("action")
    if action not in MANAGED_SERVICE_ACTIONS:
        allowed = ", ".join(sorted(MANAGED_SERVICE_ACTIONS))
        return None, f"Managed service `action` must be one of: {allowed}."
    if action == "stop":
        if "argv" in value:
            return None, "Managed service stop must omit `argv`."
        return {"version": protocol_version, "action": "stop"}, ""
    argv, argv_error = validate_argv(value.get("argv"), "Managed service")
    if argv_error:
        return None, argv_error
    assert argv is not None
    if not looks_like_managed_service(argv):
        return (
            None,
            "Managed service start accepts a recognizable local development server, "
            "not an arbitrary detached command.",
        )
    return {
        "version": protocol_version,
        "action": "start",
        "argv": argv,
    }, ""


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
        return "", "Click managed service runner received an invalid request."


_read_contract_state = click_contract_state.read_contract_state
_save_contract_state = click_contract_state.save_contract_state


def _runner_prefix(action: str, runner_script: Path) -> list[str]:
    return [
        sys.executable,
        str(runner_script.resolve()),
        "--state-root",
        str(click_state.state_root().resolve()),
        action,
    ]


def service_runner_command(
    event: dict[str, Any],
    request: dict[str, Any],
    service_id: str,
    *,
    runner_script: Path,
    render_command: RenderRunnerCommand,
    runner_token: str = "",
) -> str:
    arguments = [
        *_runner_prefix(
            "run-service-start" if request["action"] == "start" else "run-service-stop",
            runner_script,
        ),
        str(click_state.contract_path(event).resolve()),
        service_id,
    ]
    if request["action"] == "start":
        arguments.extend(
            [
                runner_token,
                str(Path(str(event.get("cwd", ""))).resolve()),
                _encoded_request(request),
            ]
        )
    return render_command(arguments)


def request_stop(event: dict[str, Any]) -> bool:
    state = _read_contract_state(event)
    service = state.get("service")
    if not isinstance(service, dict) or service.get("status") not in {
        "starting",
        "launching",
        "running",
        "stopping",
    }:
        return False
    service["status"] = "stopping"
    service["stop_requested"] = True
    state["service"] = service
    _save_contract_state(event, state)
    return True


def prepare_service(
    event: dict[str, Any],
    raw: str,
    *,
    validate_argv: ValidateArgv,
    protocol_version: int,
    mark_contract_mutated: MarkContractMutated,
    runner_script: Path,
    render_command: RenderRunnerCommand,
) -> tuple[str, str]:
    request, error = validate_request(
        raw,
        validate_argv=validate_argv,
        protocol_version=protocol_version,
    )
    if error:
        return "", error
    assert request is not None
    state = _read_contract_state(event)
    if state.get("status") not in {"approved", "evidence"}:
        return "", "Start Guarded or Evidence runtime state before managing a service."
    service = state.get("service")
    if not isinstance(service, dict):
        service = fresh_state()
    if request["action"] == "stop":
        if service.get("status") not in {
            "starting",
            "launching",
            "running",
            "stopping",
        }:
            return "echo Click managed service already stopped", ""
        service["status"] = "stopping"
        service["stop_requested"] = True
        verification = state.get("verification")
        tool_use_id = str(event.get("tool_use_id", ""))
        if not isinstance(verification, dict) or not tool_use_id:
            return "", "Click managed service stop requires a stable host tool-use claim."
        stop_request_digest = _capability_digest(
            {"action": "stop", "service_id": str(service["service_id"])}
        )
        stop_binding_digest = click_claims.host_binding_digest(tool_use_id)
        _, claim_error = click_claims.record_claim(
            state,
            capability="managed-service-stop",
            claim_mode="host-tool-use",
            request_digest=stop_request_digest,
            binding_digest=stop_binding_digest,
            mutation_revision=int(verification.get("mutation_revision", 0)),
            claimed_at=int(time.time()) or 1,
        )
        if claim_error:
            return "", claim_error
        service["stop_claim_request_digest"] = stop_request_digest
        service["stop_claim_binding_digest"] = stop_binding_digest
        state["service"] = service
        _save_contract_state(event, state)
        return (
            service_runner_command(
                event,
                request,
                str(service["service_id"]),
                runner_script=runner_script,
                render_command=render_command,
            ),
            "",
        )

    if service.get("status") in {"starting", "launching", "running", "stopping"}:
        started_at = int(service.get("started_at", 0))
        if not (
            service.get("status") == "starting"
            and started_at
            and time.time() - started_at > SERVICE_START_TIMEOUT_SECONDS * 2
        ):
            return "", "One Click-managed local service is already active. Stop it first."
    mutation_error = mark_contract_mutated(event)
    if mutation_error:
        return "", mutation_error
    state = _read_contract_state(event)
    service_id = secrets.token_urlsafe(24)
    runner_token = secrets.token_urlsafe(24)
    cwd_raw = str(Path(str(event.get("cwd", ""))).resolve())
    request_digest = _capability_digest({"request": request, "cwd": cwd_raw})
    state["service"] = {
        "status": "starting",
        "service_id": service_id,
        "request_digest": request_digest,
        "runner_token_digest": hashlib.sha256(runner_token.encode()).hexdigest(),
        "runner_claimed_at": 0,
        "supervisor_claimed_at": 0,
        "stop_requested": False,
        "supervisor_pid": 0,
        "child_pid": 0,
        "started_at": int(time.time()),
        "last_exit_code": None,
        "stop_claim_request_digest": "",
        "stop_claim_binding_digest": "",
    }
    _save_contract_state(event, state)
    return (
        service_runner_command(
            event,
            request,
            service_id,
            runner_token=runner_token,
            runner_script=runner_script,
            render_command=render_command,
        ),
        "",
    )


def _managed_contract_path(path: Path) -> bool:
    return click_state.managed_state_path(path, ("session-contract-",))


def service_snapshot(path: Path, service_id: str) -> dict[str, Any] | None:
    if not _managed_contract_path(path):
        return None
    state: Any = None
    for attempt in range(5):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            break
        except PermissionError:
            # Windows may briefly deny a reader while another process replaces
            # the state file. Do not mistake that sharing collision for a
            # missing or stopped managed service.
            if attempt == 4:
                return None
            time.sleep(0.02)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
    if not isinstance(state, dict) or state.get("status") not in {"approved", "evidence"}:
        return None
    service = state.get("service")
    if not isinstance(service, dict) or service.get("service_id") != service_id:
        return None
    return dict(service)


def record_service_fields(
    path: Path,
    service_id: str,
    *,
    expected_statuses: tuple[str, ...] | None = None,
    **fields: Any,
) -> bool:
    if not _managed_contract_path(path):
        return False
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    if not isinstance(state, dict) or state.get("status") not in {"approved", "evidence"}:
        return False
    service = state.get("service")
    if not isinstance(service, dict) or service.get("service_id") != service_id:
        return False
    if expected_statuses is not None and service.get("status") not in expected_statuses:
        return False
    service.update(fields)
    state["service"] = service
    state["updated_at"] = int(time.time())
    click_state.write_json(path, state)
    return True


def claim_service_runner(
    path: Path,
    service_id: str,
    request: dict[str, Any],
    cwd_raw: str,
    runner_token: str,
    *,
    supervisor: bool,
) -> str:
    if not _managed_contract_path(path):
        return "Click managed service runner received an unmanaged state path."
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return "Click managed service runner could not read its contract state."
    if not isinstance(state, dict) or state.get("status") not in {"approved", "evidence"}:
        return "Click managed service runner is no longer authorized to execute."
    service = state.get("service")
    expected_status = "launching" if supervisor else "starting"
    if (
        not isinstance(service, dict)
        or service.get("service_id") != service_id
        or service.get("status") != expected_status
        or service.get("stop_requested") is True
    ):
        return "Click managed service runner is no longer authorized to execute."
    request_digest = _capability_digest({"request": request, "cwd": cwd_raw})
    if service.get("request_digest") != request_digest:
        return "Click managed service runner request digest did not match active state."
    token_digest = hashlib.sha256(runner_token.encode()).hexdigest()
    if not secrets.compare_digest(
        str(service.get("runner_token_digest", "")), token_digest
    ):
        return "Click managed service runner token did not match active state."
    runner_claimed_at = service.get("runner_claimed_at", 0)
    supervisor_claimed_at = service.get("supervisor_claimed_at", 0)
    for claimed_at in (runner_claimed_at, supervisor_claimed_at):
        if not isinstance(claimed_at, int) or isinstance(claimed_at, bool):
            return "Click managed service runner claim state is malformed."
    if supervisor:
        if runner_claimed_at <= 0 or supervisor_claimed_at > 0:
            return "Click managed service supervisor was already claimed or not launched."
        if not _unclaimed_reservation_is_fresh(
            runner_claimed_at, SERVICE_START_TIMEOUT_SECONDS * 2
        ):
            return "Click managed service supervisor authorization expired before launch."
        claimed_at = int(time.time()) or 1
        service["supervisor_claimed_at"] = claimed_at
        capability = "managed-service-supervisor"
    else:
        if runner_claimed_at > 0 or supervisor_claimed_at > 0:
            return "Click managed service start runner was already claimed."
        if not _unclaimed_reservation_is_fresh(
            service.get("started_at", 0), SERVICE_START_TIMEOUT_SECONDS * 2
        ):
            return "Click managed service start authorization expired before launch."
        claimed_at = int(time.time()) or 1
        service["runner_claimed_at"] = claimed_at
        service["status"] = "launching"
        capability = "managed-service-start"
    verification = state.get("verification")
    if not isinstance(verification, dict):
        return "Click managed service claim revision state is unavailable."
    _, claim_error = click_claims.record_claim(
        state,
        capability=capability,
        claim_mode="one-use-runner",
        request_digest=request_digest,
        token_digest=token_digest,
        mutation_revision=int(verification.get("mutation_revision", 0)),
        claimed_at=claimed_at,
    )
    if claim_error:
        return claim_error
    state["service"] = service
    state["updated_at"] = int(time.time())
    click_state.write_json(path, state)
    return ""


def record_service_claim_result(
    path: Path,
    service_id: str,
    *,
    capability: str,
    exit_code: int | None,
) -> bool:
    if not _managed_contract_path(path):
        return False
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    if not isinstance(state, dict) or state.get("status") not in {"approved", "evidence"}:
        return False
    service = state.get("service")
    verification = state.get("verification")
    if (
        not isinstance(service, dict)
        or service.get("service_id") != service_id
        or not isinstance(verification, dict)
    ):
        return False
    claim_mode = "one-use-runner"
    request_digest = str(service.get("request_digest", ""))
    binding_digest = ""
    if capability == "managed-service-stop":
        claim_mode = "host-tool-use"
        request_digest = str(service.get("stop_claim_request_digest", ""))
        binding_digest = str(service.get("stop_claim_binding_digest", ""))
    if not click_claims.complete_claim(
        state,
        capability=capability,
        claim_mode=claim_mode,
        request_digest=request_digest,
        binding_digest=binding_digest,
        mutation_revision=int(verification.get("mutation_revision", 0)),
        exit_code=exit_code,
    ):
        return False
    if capability == "managed-service-stop":
        service["stop_claim_request_digest"] = ""
        service["stop_claim_binding_digest"] = ""
        state["service"] = service
    state["updated_at"] = int(time.time())
    click_state.write_json(path, state)
    return True


def _unclaimed_reservation_is_fresh(value: Any, ttl_seconds: int) -> bool:
    return bool(
        isinstance(value, int)
        and not isinstance(value, bool)
        and value > 0
        and 0 <= time.time() - value <= ttl_seconds
    )


def run_service_supervisor(
    arguments: list[str],
    *,
    validate_argv: ValidateArgv,
    protocol_version: int,
    execution_argv: ExecutionArgv,
) -> int:
    if len(arguments) != 5:
        return 2
    state_path = Path(arguments[0])
    service_id, runner_token, cwd_raw, encoded = arguments[1:]
    raw, error = _decode_encoded_request(encoded)
    if error:
        return 2
    request, error = validate_request(
        raw,
        validate_argv=validate_argv,
        protocol_version=protocol_version,
    )
    if error or request is None or request.get("action") != "start":
        return 2
    with click_state.state_lock():
        claim_error = claim_service_runner(
            state_path,
            service_id,
            request,
            cwd_raw,
            runner_token,
            supervisor=True,
        )
    if claim_error:
        return 2
    cwd = Path(cwd_raw)
    if not cwd.is_dir():
        with click_state.state_lock():
            record_service_fields(
                state_path,
                service_id,
                expected_statuses=("launching",),
                status="failed",
                last_exit_code=2,
            )
            record_service_claim_result(
                state_path,
                service_id,
                capability="managed-service-supervisor",
                exit_code=2,
            )
        return 2
    try:
        child = click_process.spawn_argv(
            execution_argv(request["argv"]),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        with click_state.state_lock():
            record_service_fields(
                state_path,
                service_id,
                expected_statuses=("launching",),
                status="failed",
                last_exit_code=127,
                stop_requested=False,
            )
            record_service_claim_result(
                state_path,
                service_id,
                capability="managed-service-supervisor",
                exit_code=127,
            )
        return 127

    time.sleep(0.2)
    early_exit = child.poll()
    with click_state.state_lock():
        recorded = record_service_fields(
            state_path,
            service_id,
            expected_statuses=("launching",),
            status="failed" if early_exit is not None else "running",
            supervisor_pid=os.getpid(),
            child_pid=child.pid,
            last_exit_code=int(early_exit) if early_exit is not None else None,
        )
        if early_exit is not None or not recorded:
            record_service_claim_result(
                state_path,
                service_id,
                capability="managed-service-supervisor",
                exit_code=int(early_exit or 2),
            )
    if early_exit is not None or not recorded:
        if early_exit is None:
            click_process.terminate_process_group(child)
        return int(early_exit or 2)

    started = time.monotonic()
    stop_requested = False
    while True:
        exit_code = child.poll()
        if exit_code is not None:
            break
        snapshot = service_snapshot(state_path, service_id)
        if snapshot is None or snapshot.get("stop_requested") is True:
            stop_requested = True
            exit_code = click_process.terminate_process_group(child)
            break
        if time.monotonic() - started >= MANAGED_SERVICE_MAX_SECONDS:
            stop_requested = True
            exit_code = click_process.terminate_process_group(child)
            break
        time.sleep(0.2)

    with click_state.state_lock():
        record_service_fields(
            state_path,
            service_id,
            expected_statuses=("running", "stopping", "launching"),
            status="stopped" if stop_requested else "failed",
            stop_requested=False,
            child_pid=0,
            supervisor_pid=0,
            last_exit_code=int(exit_code or 0),
        )
        record_service_claim_result(
            state_path,
            service_id,
            capability="managed-service-supervisor",
            exit_code=int(exit_code or 0),
        )
    return int(exit_code or 0)


def run_service_start(
    arguments: list[str],
    *,
    validate_argv: ValidateArgv,
    protocol_version: int,
    runner_script: Path,
) -> int:
    if len(arguments) != 5:
        sys.stderr.write(
            "usage: click_gate.py run-service-start "
            "<state> <id> <token> <cwd> <request>\n"
        )
        return 2
    state_path = Path(arguments[0])
    service_id, runner_token, cwd_raw, encoded = arguments[1:]
    raw, error = _decode_encoded_request(encoded)
    if error:
        sys.stderr.write(f"{error}\n")
        return 2
    request, error = validate_request(
        raw,
        validate_argv=validate_argv,
        protocol_version=protocol_version,
    )
    if error or request is None or request.get("action") != "start":
        sys.stderr.write(f"{error or 'Managed service start request is invalid.'}\n")
        return 2
    with click_state.state_lock():
        claim_error = claim_service_runner(
            state_path,
            service_id,
            request,
            cwd_raw,
            runner_token,
            supervisor=False,
        )
    if claim_error:
        sys.stderr.write(f"{claim_error}\n")
        return 2
    supervisor = [
        *_runner_prefix("run-service-supervisor", runner_script),
        str(state_path.resolve()),
        service_id,
        runner_token,
        cwd_raw,
        encoded,
    ]
    try:
        click_process.spawn_argv(
            supervisor,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:
        with click_state.state_lock():
            record_service_fields(
                state_path,
                service_id,
                expected_statuses=("launching",),
                status="failed",
                last_exit_code=127,
            )
            record_service_claim_result(
                state_path,
                service_id,
                capability="managed-service-start",
                exit_code=127,
            )
        sys.stderr.write(f"Click could not start the managed service supervisor: {exc}\n")
        return 127
    deadline = time.monotonic() + SERVICE_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        snapshot = service_snapshot(state_path, service_id)
        if snapshot is None:
            with click_state.state_lock():
                record_service_claim_result(
                    state_path,
                    service_id,
                    capability="managed-service-start",
                    exit_code=2,
                )
            return 2
        if snapshot.get("status") == "running":
            with click_state.state_lock():
                if not record_service_claim_result(
                    state_path,
                    service_id,
                    capability="managed-service-start",
                    exit_code=0,
                ):
                    return 2
            sys.stdout.write("Click managed service started\n")
            return 0
        if snapshot.get("status") == "failed":
            exit_code = int(snapshot.get("last_exit_code") or 2)
            with click_state.state_lock():
                record_service_claim_result(
                    state_path,
                    service_id,
                    capability="managed-service-start",
                    exit_code=exit_code,
                )
            sys.stderr.write("Click managed service exited during startup.\n")
            return exit_code
        if snapshot.get("status") in {"stopping", "stopped"}:
            with click_state.state_lock():
                record_service_claim_result(
                    state_path,
                    service_id,
                    capability="managed-service-start",
                    exit_code=2,
                )
            return 2
        time.sleep(0.05)
    with click_state.state_lock():
        record_service_fields(
            state_path,
            service_id,
            expected_statuses=("launching", "starting"),
            status="stopping",
            stop_requested=True,
        )
        record_service_claim_result(
            state_path,
            service_id,
            capability="managed-service-start",
            exit_code=2,
        )
    sys.stderr.write("Click managed service did not start within its bounded timeout.\n")
    return 2


def run_service_stop(
    arguments: list[str], *, snapshot_reader: SnapshotReader | None = None
) -> int:
    if len(arguments) != 2:
        sys.stderr.write("usage: click_gate.py run-service-stop <state> <id>\n")
        return 2
    state_path = Path(arguments[0])
    service_id = arguments[1]
    reader = snapshot_reader or service_snapshot
    deadline = time.monotonic() + SERVICE_STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        snapshot = reader(state_path, service_id)
        if snapshot is not None and snapshot.get("status") in {
            "failed",
            "idle",
            "stopped",
        }:
            with click_state.state_lock():
                if not record_service_claim_result(
                    state_path,
                    service_id,
                    capability="managed-service-stop",
                    exit_code=0,
                ):
                    return 2
            sys.stdout.write("Click managed service stopped\n")
            return 0
        time.sleep(0.05)
    with click_state.state_lock():
        record_service_claim_result(
            state_path,
            service_id,
            capability="managed-service-stop",
            exit_code=2,
        )
    sys.stderr.write("Click managed service did not stop within its bounded timeout.\n")
    return 2
