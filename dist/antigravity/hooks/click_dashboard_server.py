#!/usr/bin/env python3
"""HTTP serving for the local Click dashboard, loaded only when needed."""

from __future__ import annotations

import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from socketserver import TCPServer
import time
from urllib.parse import urlsplit

if __package__:
    from . import click_shadow_dashboard as runtime
    from . import click_dashboard_projection, click_runtime_state, click_state
else:
    import click_shadow_dashboard as runtime
    import click_dashboard_projection
    import click_runtime_state
    import click_state


class _DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, state_path: Path, instance_id: str, access_token: str):
        self.state_path = state_path
        self.instance_id = instance_id
        self.access_token = access_token
        super().__init__(("127.0.0.1", 0), _DashboardHandler)

    def server_bind(self) -> None:
        # HTTPServer resolves the bound address with getfqdn(). This local-only
        # viewer uses its numeric address, so DNS must not delay startup.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class _DashboardHandler(BaseHTTPRequestHandler):
    server: _DashboardServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'none'",
        )
        self.end_headers()

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self._headers(status, content_type, len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    def _host_is_valid(self) -> bool:
        expected = f"127.0.0.1:{self.server.server_port}"
        return hmac.compare_digest(self.headers.get("Host", ""), expected)

    def _authorized(self) -> bool:
        value = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.access_token}"
        return hmac.compare_digest(value, expected)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._host_is_valid():
            self._send(421, "text/plain; charset=utf-8", b"Misdirected Request\n")
            return
        path = urlsplit(self.path).path
        asset = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/locales.js": ("locales.js", "text/javascript; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }.get(path)
        if asset is not None:
            self._send(200, asset[1], runtime._asset_text(asset[0]).encode("utf-8"))
            return
        if path == "/api/v1/snapshot":
            if not self._authorized():
                self._send(401, "application/json", b'{"error":"unauthorized"}\n')
                return
            with click_state.state_lock():
                state, dashboard = runtime._snapshot(
                    self.server.state_path, self.server.instance_id
                )
            # runtime._snapshot returns a fresh JSON object. Projection work and socket
            # writes must not hold the lock needed by Hook state mutations.
            if (
                state is None
                or dashboard.get("status") not in {"running", "stopping"}
            ):
                self._send(410, "application/json", b'{"error":"stale"}\n')
                return
            projection = click_dashboard_projection.dashboard_projection(state)
            if not click_dashboard_projection.projection_is_valid(projection):
                self._send(500, "application/json", b'{"error":"invalid-projection"}\n')
                return
            body = json.dumps(
                projection,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8") + b"\n"
            self._send(200, "application/json; charset=utf-8", body)
            return
        self._send(404, "text/plain; charset=utf-8", b"Not Found\n")

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.do_GET()

    def _method_not_allowed(self) -> None:
        self._send(405, "text/plain; charset=utf-8", b"Method Not Allowed\n")

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed


def run_server(arguments: list[str]) -> int:
    if len(arguments) != 3:
        return 2
    state_path = Path(arguments[0])
    instance_id, access_token = arguments[1:]
    if not runtime._managed_state_path(state_path):
        return 2
    access_digest = hashlib.sha256(access_token.encode()).hexdigest()
    with click_state.state_lock():
        state, dashboard = runtime._snapshot(state_path, instance_id)
        if (
            state is None
            or not click_runtime_state.view(state).execution_authorized
            or dashboard.get("status") != "starting"
            or dashboard.get("stop_requested") is True
            or not hmac.compare_digest(
                str(dashboard.get("access_token_digest", "")), access_digest
            )
        ):
            return 2
    try:
        server = _DashboardServer(state_path, instance_id, access_token)
    except OSError as exc:
        with click_state.state_lock():
            runtime._write_dashboard_fields(
                state_path,
                instance_id,
                status="failed",
                last_error=str(exc)[:256],
            )
        return 2
    server.timeout = 0.4
    with click_state.state_lock():
        if not runtime._write_dashboard_fields(
            state_path,
            instance_id,
            status="running",
            port=int(server.server_port),
            pid=os.getpid(),
        ):
            server.server_close()
            return 2
    deadline = time.monotonic() + runtime.MAX_LIFETIME_SECONDS
    result = 0
    try:
        while time.monotonic() < deadline:
            with click_state.state_lock():
                dashboard = runtime._dashboard_state_for_path(state_path)
            if (
                dashboard.get("instance_id") != instance_id
                or dashboard.get("stop_requested") is True
                or dashboard.get("status") != "running"
                or not hmac.compare_digest(
                    str(dashboard.get("access_token_digest", "")), access_digest
                )
            ):
                break
            server.handle_request()
        else:
            result = 124
    finally:
        server.server_close()
        with click_state.state_lock():
            runtime._write_dashboard_fields(
                state_path,
                instance_id,
                status="stopped" if result == 0 else "failed",
                stop_requested=True,
                port=0,
                pid=0,
                last_error="" if result == 0 else "lifetime-expired",
            )
    return result
