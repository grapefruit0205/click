#!/usr/bin/env python3
"""Authenticated loopback viewer for non-authoritative Shadow telemetry."""

from __future__ import annotations

import hashlib
from functools import lru_cache
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from typing import Any, Callable

if __package__:
    from . import (
        click_contract_state,
        click_process,
        click_runtime_state,
        click_state,
    )
else:  # Executed directly from the bundled hooks directory.
    import click_contract_state
    import click_process
    import click_runtime_state
    import click_state


DASHBOARD_FIELD = "shadow_dashboard"
DASHBOARD_STATE_VERSION = 1
DASHBOARD_STATUSES = frozenset(
    {"idle", "starting", "running", "stopping", "stopped", "failed"}
)
DASHBOARD_ACTIONS = frozenset({"start", "stop", "status"})
START_TIMEOUT_SECONDS = 8
STOP_TIMEOUT_SECONDS = 8
MAX_LIFETIME_SECONDS = 2 * 60 * 60
POLL_SECONDS = 0.1

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_INSTANCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_DASHBOARD_FIELDS = frozenset(
    {
        "version",
        "status",
        "instance_id",
        "runner_token_digest",
        "runner_claimed_at",
        "access_token_digest",
        "port",
        "pid",
        "started_at",
        "stop_requested",
        "last_error",
    }
)

RenderCommand = Callable[[list[str]], str]


_ASSET_ROOT = Path(__file__).resolve().parent / "dashboard"


@lru_cache(maxsize=4)
def _asset_text(name: str) -> str:
    """Load only fixed bundled assets, when a viewer or caller requests them."""
    if name not in {"index.html", "styles.css", "app.js", "locales.js"}:
        raise ValueError("Unknown dashboard asset")
    if name == "locales.js":
        locales = {
            locale: json.loads(
                (_ASSET_ROOT / "locales" / f"{locale}.json").read_text(encoding="utf-8")
            )
            for locale in ("ko", "en", "zh-CN")
        }
        messages = {
            key: [locales["en"][key], locales["zh-CN"][key]]
            for key in locales["ko"]
        }
        return "globalThis.ClickDashboardMessages=" + json.dumps(
            messages, ensure_ascii=False, separators=(",", ":")
        ) + ";\n"
    return (_ASSET_ROOT / name).read_text(encoding="utf-8")


def __getattr__(name: str) -> Any:
    """Retain the Python asset API without loading frontend data on Hook import."""
    if name == "JS":
        return _asset_text("locales.js") + _asset_text("app.js")
    if name in {"HTML", "CSS"}:
        return _asset_text({"HTML": "index.html", "CSS": "styles.css"}[name])
    if name in {"_DashboardServer", "_DashboardHandler", "click_dashboard_projection"}:
        return getattr(_server_module(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _server_module():
    if __package__:
        from . import click_dashboard_server
    else:
        import click_dashboard_server
    return click_dashboard_server




def fresh_state() -> dict[str, Any]:
    return {
        "version": DASHBOARD_STATE_VERSION,
        "status": "idle",
        "instance_id": "",
        "runner_token_digest": "",
        "runner_claimed_at": 0,
        "access_token_digest": "",
        "port": 0,
        "pid": 0,
        "started_at": 0,
        "stop_requested": False,
        "last_error": "",
    }


def state_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _DASHBOARD_FIELDS:
        return False
    status = value.get("status")
    instance_id = value.get("instance_id")
    runner_digest = value.get("runner_token_digest")
    access_digest = value.get("access_token_digest")
    integers = [
        value.get("runner_claimed_at"),
        value.get("port"),
        value.get("pid"),
        value.get("started_at"),
    ]
    if (
        value.get("version") != DASHBOARD_STATE_VERSION
        or status not in DASHBOARD_STATUSES
        or not isinstance(instance_id, str)
        or not isinstance(runner_digest, str)
        or not isinstance(access_digest, str)
        or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in integers)
        or not isinstance(value.get("stop_requested"), bool)
        or not isinstance(value.get("last_error"), str)
        or len(value["last_error"]) > 256
    ):
        return False
    if status == "idle":
        return value == fresh_state()
    return bool(
        _INSTANCE.fullmatch(instance_id)
        and _DIGEST.fullmatch(access_digest)
        and (not runner_digest or _DIGEST.fullmatch(runner_digest))
        and 0 <= value["port"] <= 65535
    )


def _dashboard_state(state: Any) -> dict[str, Any]:
    value = state.get(DASHBOARD_FIELD) if isinstance(state, dict) else None
    return dict(value) if state_is_valid(value) else fresh_state()


def _dashboard_path(state_path: Path) -> Path:
    return state_path.with_name(
        "dashboard-" + state_path.name.removeprefix("session-contract-")
    )


def _dashboard_state_for_path(state_path: Path) -> dict[str, Any]:
    sidecar = _read_state(_dashboard_path(state_path))
    if state_is_valid(sidecar):
        return dict(sidecar)
    # Compatibility for a viewer prepared by v0.80 before the sidecar split.
    return _dashboard_state(_read_state(state_path))


def _write_dashboard_state(state_path: Path, dashboard: dict[str, Any]) -> bool:
    if not state_is_valid(dashboard):
        return False
    click_state.write_json(_dashboard_path(state_path), dashboard)
    return True


def _managed_state_path(path: Path) -> bool:
    return click_state.managed_state_path(path, ("session-contract-",))


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
    action: str,
    arguments: list[str],
    *,
    runner_script: Path,
    render_command: RenderCommand,
) -> str:
    return render_command(
        [
            *_runner_prefix(action, runner_script),
            str(click_state.contract_path(event).resolve()),
            *arguments,
        ]
    )


def prepare(
    event: dict[str, Any],
    action: str,
    *,
    runner_script: Path,
    render_command: RenderCommand,
) -> tuple[str, str]:
    if action not in DASHBOARD_ACTIONS:
        return "", "Click dashboard action must be start, stop, or status."
    state = click_contract_state.read_contract_state(event)
    runtime = click_runtime_state.view(state)
    if not runtime.execution_authorized:
        return "", "Start Guarded or Evidence runtime state before opening its dashboard."
    contract_path = click_state.contract_path(event).resolve()
    dashboard = _dashboard_state_for_path(contract_path)
    state_path = str(contract_path)
    if action == "status":
        return runner_command(
            event,
            "run-dashboard-status",
            [],
            runner_script=runner_script,
            render_command=render_command,
        ), ""
    if action == "stop":
        if dashboard["status"] not in {"starting", "running", "stopping"}:
            return runner_command(
                event,
                "run-dashboard-status",
                [],
                runner_script=runner_script,
                render_command=render_command,
            ), ""
        dashboard["status"] = "stopping"
        dashboard["stop_requested"] = True
        _write_dashboard_state(contract_path, dashboard)
        return runner_command(
            event,
            "run-dashboard-stop",
            [dashboard["instance_id"]],
            runner_script=runner_script,
            render_command=render_command,
        ), ""

    if dashboard["status"] in {"starting", "running", "stopping"}:
        return "", "The Click Shadow dashboard is already active. Stop it first."
    instance_id = secrets.token_urlsafe(24)
    runner_token = secrets.token_urlsafe(24)
    access_token = secrets.token_urlsafe(32)
    dashboard = {
        "version": DASHBOARD_STATE_VERSION,
        "status": "starting",
        "instance_id": instance_id,
        "runner_token_digest": hashlib.sha256(runner_token.encode()).hexdigest(),
        "runner_claimed_at": 0,
        "access_token_digest": hashlib.sha256(access_token.encode()).hexdigest(),
        "port": 0,
        "pid": 0,
        "started_at": int(time.time()) or 1,
        "stop_requested": False,
        "last_error": "",
    }
    _write_dashboard_state(contract_path, dashboard)
    return runner_command(
        event,
        "run-dashboard-start",
        [instance_id, runner_token, access_token],
        runner_script=runner_script,
        render_command=render_command,
    ), ""


def request_stop(event: dict[str, Any]) -> bool:
    state_path = click_state.contract_path(event).resolve()
    dashboard = _dashboard_state_for_path(state_path)
    if dashboard["status"] not in {"starting", "running", "stopping"}:
        return False
    dashboard["status"] = "stopping"
    dashboard["stop_requested"] = True
    return _write_dashboard_state(state_path, dashboard)


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _write_dashboard_fields(
    path: Path, instance_id: str, **fields: Any
) -> bool:
    dashboard = _dashboard_state_for_path(path)
    if dashboard.get("instance_id") != instance_id:
        return False
    dashboard.update(fields)
    if not state_is_valid(dashboard):
        return False
    return _write_dashboard_state(path, dashboard)


def _snapshot(path: Path, instance_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    dashboard = _dashboard_state_for_path(path)
    if dashboard.get("instance_id") != instance_id:
        return None, dashboard
    return click_contract_state.read_projection_state_path(path), dashboard


def run_start(
    arguments: list[str],
    *,
    runner_script: Path,
    spawn: Callable[..., subprocess.Popen[Any]] = click_process.spawn_argv,
) -> int:
    if len(arguments) != 4:
        sys.stderr.write("usage: run-dashboard-start <state> <id> <runner-token> <access-token>\n")
        return 2
    state_path = Path(arguments[0])
    instance_id, runner_token, access_token = arguments[1:]
    if not _managed_state_path(state_path):
        sys.stderr.write("Click dashboard runner received an unmanaged state path.\n")
        return 2
    with click_state.state_lock():
        state, dashboard = _snapshot(state_path, instance_id)
        runner_digest = hashlib.sha256(runner_token.encode()).hexdigest()
        access_digest = hashlib.sha256(access_token.encode()).hexdigest()
        if (
            state is None
            or not click_runtime_state.view(state).execution_authorized
            or dashboard.get("status") != "starting"
            or dashboard.get("stop_requested") is True
            or dashboard.get("runner_claimed_at") != 0
            or not hmac.compare_digest(str(dashboard.get("runner_token_digest", "")), runner_digest)
            or not hmac.compare_digest(str(dashboard.get("access_token_digest", "")), access_digest)
            or time.time() - int(dashboard.get("started_at", 0)) > START_TIMEOUT_SECONDS * 2
        ):
            sys.stderr.write("Click dashboard start authorization is stale or invalid.\n")
            return 2
        if not _write_dashboard_fields(
            state_path,
            instance_id,
            runner_claimed_at=int(time.time()) or 1,
            runner_token_digest="",
        ):
            return 2
    try:
        spawn(
            [
                *_runner_prefix("run-dashboard-server", runner_script),
                str(state_path),
                instance_id,
                access_token,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        with click_state.state_lock():
            _write_dashboard_fields(
                state_path,
                instance_id,
                status="failed",
                last_error=str(exc)[:256],
            )
        sys.stderr.write("Click dashboard process could not start.\n")
        return 2
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        with click_state.state_lock():
            current = _dashboard_state_for_path(state_path)
        if current.get("instance_id") != instance_id:
            break
        if current.get("status") == "running" and int(current.get("port", 0)) > 0:
            sys.stdout.write(
                f"http://127.0.0.1:{current['port']}/#token={access_token}\n"
            )
            return 0
        if current.get("status") == "failed":
            sys.stderr.write("Click dashboard exited during startup.\n")
            return 2
        time.sleep(POLL_SECONDS)
    with click_state.state_lock():
        _write_dashboard_fields(
            state_path,
            instance_id,
            status="stopping",
            stop_requested=True,
            last_error="startup-timeout",
        )
    sys.stderr.write("Click dashboard did not start within its bounded timeout.\n")
    return 2


def run_stop(arguments: list[str]) -> int:
    if len(arguments) != 2:
        sys.stderr.write("usage: run-dashboard-stop <state> <id>\n")
        return 2
    state_path = Path(arguments[0])
    instance_id = arguments[1]
    if not _managed_state_path(state_path):
        return 2
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        with click_state.state_lock():
            dashboard = _dashboard_state_for_path(state_path)
        if dashboard.get("instance_id") != instance_id:
            return 2
        if dashboard.get("status") in {"stopped", "failed", "idle"}:
            sys.stdout.write("Click Shadow dashboard stopped\n")
            return 0
        time.sleep(POLL_SECONDS)
    sys.stderr.write("Click Shadow dashboard did not stop within its bounded timeout.\n")
    return 2


def run_status(arguments: list[str]) -> int:
    if len(arguments) != 1:
        sys.stderr.write("usage: run-dashboard-status <state>\n")
        return 2
    state_path = Path(arguments[0])
    if not _managed_state_path(state_path):
        return 2
    with click_state.state_lock():
        dashboard = _dashboard_state_for_path(state_path)
    sys.stdout.write(
        json.dumps(
            {
                "status": dashboard["status"],
                "port": dashboard["port"],
                "started_at": dashboard["started_at"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    return 0


def run_server(arguments: list[str]) -> int:
    return _server_module().run_server(arguments)
