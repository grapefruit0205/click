#!/usr/bin/env python3
"""Session-scoped resident process for Click lifecycle Hook dispatch."""

from __future__ import annotations

import io
import hmac
import json
import math
import os
from pathlib import Path
import socket
import sys
import threading
import time
from typing import Any

if __package__:
    from . import click_import_bootstrap
else:  # Executed directly from the bundled hooks directory.
    import click_import_bootstrap


(click_hook_transport, click_host_coverage, click_runner_transport) = (
    click_import_bootstrap.load_siblings(
        __package__, "click_hook_transport", "click_host_coverage", "click_runner_transport"
    )
)


MAX_CAPTURE_CHARACTERS = 80_000
REQUEST_WATCHDOG_SECONDS = 6.0
SESSION_END_WATCHDOG_SECONDS = 2.0


class _BoundedText(io.StringIO):
    def __init__(self, limit: int = MAX_CAPTURE_CHARACTERS) -> None:
        super().__init__()
        self.limit = limit
        self.truncated = False

    def write(self, value: str) -> int:
        if not isinstance(value, str):
            raise TypeError("Click Hook output must be text")
        remaining = max(0, self.limit - self.tell())
        if len(value) > remaining:
            self.truncated = True
        if remaining:
            super().write(value[:remaining])
        return len(value)


# Host adapters register their Windows runner renderer when imported: Codex
# rewrites into PowerShell or cmd.exe, Claude Code into Git Bash. Each package
# bundles only its own adapter, so a missing sibling is expected; the renderer
# for the host that sent each event is activated per request.
_HOST_RENDERER_MODULES = ("click_windows", "claude_hook")


def _load_gate() -> Any:
    (click_gate,) = click_import_bootstrap.load_siblings(__package__, "click_gate")
    if os.name == "nt":
        for name in _HOST_RENDERER_MODULES:
            try:
                click_import_bootstrap.load_siblings(__package__, name)
            except ImportError:
                continue
    return click_gate


def _valid_environment(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict) or len(value) > 4096:
        return None
    result: dict[str, str] = {}
    characters = 0
    for key, item in value.items():
        if (
            not isinstance(key, str) or not key or "=" in key or "\x00" in key
            or not isinstance(item, str) or "\x00" in item
        ):
            return None
        characters += len(key) + len(item)
        if characters > click_hook_transport.MAX_EVENT_BYTES:
            return None
        result[key] = item
    return result


def _run_gate(click_gate: Any, request: dict[str, Any]) -> dict[str, Any]:
    mode = request.get("mode")
    event = request.get("event")
    request_id = request.get("request_id")
    environment = _valid_environment(request.get("environment"))
    cwd = request.get("cwd")
    deadline = request.get("deadline")
    if (
        not isinstance(mode, str) or mode not in click_hook_transport.HOOK_MODES
        or not isinstance(event, dict)
        or not isinstance(request_id, str)
        or len(request_id) != 32
        or environment is None
        or not isinstance(cwd, str)
        or not isinstance(deadline, (int, float))
        or not math.isfinite(deadline)
    ):
        return _response(request_id if isinstance(request_id, str) else "", 1, "", "click hook error: invalid resident worker request\n")
    try:
        target_cwd = Path(cwd).resolve(strict=True)
    except (OSError, RuntimeError):
        return _response(request_id, 1, "", "click hook error: invalid resident worker cwd\n")
    if not target_cwd.is_dir():
        return _response(request_id, 1, "", "click hook error: invalid resident worker cwd\n")

    if os.name == "nt":
        click_runner_transport.activate_host_renderer(
            click_host_coverage.host_id_from_event(event)
        )
    input_text = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    output = _BoundedText()
    errors = _BoundedText()
    old_argv = sys.argv
    old_stdin = sys.stdin
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    old_environment = dict(os.environ)
    old_cwd = Path.cwd()
    watchdog_seconds = (
        SESSION_END_WATCHDOG_SECONDS if mode == "session-end" else REQUEST_WATCHDOG_SECONDS
    )
    watchdog_seconds = min(watchdog_seconds, deadline - time.time())
    if watchdog_seconds <= 0:
        return _response(request_id, 1, "", "click hook error: queued Hook deadline expired\n")
    watchdog = threading.Timer(watchdog_seconds, lambda: os._exit(124))
    watchdog.daemon = True
    try:
        os.environ.clear()
        os.environ.update(environment)
        os.chdir(target_cwd)
        sys.argv = [str(Path(__file__).with_name("click_gate.py").resolve()), mode]
        sys.stdin = io.StringIO(input_text)
        sys.stdout = output
        sys.stderr = errors
        watchdog.start()
        returncode = int(click_gate.main())
    except Exception as exc:
        returncode = 1
        errors.write(f"click hook worker error: {exc}\n")
    finally:
        watchdog.cancel()
        if watchdog.ident is not None:
            watchdog.join()
        sys.argv = old_argv
        sys.stdin = old_stdin
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_environment)
    if output.truncated or errors.truncated:
        return _response(
            request_id,
            1,
            "",
            errors.getvalue() + "click hook error: resident worker output limit exceeded\n",
        )
    return _response(request_id, returncode, output.getvalue(), errors.getvalue())


def _response(request_id: str, returncode: int, stdout: str, stderr: str) -> dict[str, Any]:
    return {
        "version": click_hook_transport.PROTOCOL_VERSION,
        "request_id": request_id,
        "worker_pid": os.getpid(),
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def _serve(runtime: Path, state_name: str, identity: str, idle_seconds: int) -> int:
    click_gate = _load_gate()
    token = os.urandom(32).hex()
    state_path: Path | None = None
    stopping = False
    last_request = time.monotonic()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(8)
        server.settimeout(min(1.0, max(0.1, idle_seconds / 4)))
        port = int(server.getsockname()[1])
        state_path = click_hook_transport.publish_state(
            runtime, state_name, identity, token, port
        )
        while not stopping and time.monotonic() - last_request < idle_seconds:
            try:
                connection, _address = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(click_hook_transport.REQUEST_TIMEOUT_SECONDS)
                try:
                    request = click_hook_transport.receive_frame(
                        connection, click_hook_transport.MAX_EVENT_BYTES
                    )
                    if (
                        not isinstance(request, dict)
                        or request.get("version") != click_hook_transport.PROTOCOL_VERSION
                        or not isinstance(request.get("token"), str)
                        or not hmac.compare_digest(request["token"], token)
                    ):
                        continue
                    request_id = request.get("request_id")
                    if not isinstance(request_id, str) or len(request_id) != 32:
                        continue
                    action = request.get("action")
                    if action == "shutdown":
                        response = _response(request_id, 0, "", "")
                        stopping = True
                    elif action == "hook":
                        response = _run_gate(click_gate, request)
                        if request.get("mode") == "session-end":
                            stopping = True
                    else:
                        response = _response(
                            request_id, 1, "", "click hook error: invalid resident worker action\n"
                        )
                    click_hook_transport.send_frame(
                        connection, response, click_hook_transport.MAX_RESPONSE_BYTES
                    )
                    last_request = time.monotonic()
                except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
                    continue
    finally:
        server.close()
        if state_path is not None:
            click_hook_transport.remove_owned_state(state_path, identity)
    return 0


def main() -> int:
    if len(sys.argv) != 6 or sys.argv[1] != "--serve":
        sys.stderr.write(
            "usage: click_hook_worker.py --serve <runtime> <state> <identity> <idle>\n"
        )
        return 2
    runtime = Path(sys.argv[2])
    state_name = sys.argv[3]
    identity = sys.argv[4]
    try:
        idle_seconds = int(sys.argv[5])
    except ValueError:
        return 2
    try:
        resolved_runtime = runtime.resolve(strict=True)
    except (OSError, RuntimeError):
        return 2
    if runtime != resolved_runtime or not runtime.is_dir():
        return 2
    if len(identity) != 64 or any(
        character not in "0123456789abcdef" for character in identity
    ):
        return 2
    if not click_hook_transport.MIN_IDLE_SECONDS <= idle_seconds <= click_hook_transport.MAX_IDLE_SECONDS:
        return 2
    try:
        click_hook_transport._state_path(runtime, state_name)
    except ValueError:
        return 2
    return _serve(runtime, state_name, identity, idle_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
