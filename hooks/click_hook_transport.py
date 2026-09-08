#!/usr/bin/env python3
"""Small authenticated transport for the session-scoped Click Hook worker.

Only lifecycle Hook events use this transport.  Verification, mutation,
inspection, dashboard, and sharding runners continue to execute in fresh
processes so their claims and environment bindings remain one-shot boundaries.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import time


PROTOCOL_VERSION = 1
HOOK_MODES = frozenset({"pre-tool", "post-tool", "prompt-submit", "session-end"})
MAX_EVENT_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_STATE_BYTES = 4096
MAX_WORKER_STATES = 32
STARTUP_TIMEOUT_SECONDS = 2.0
CONNECT_TIMEOUT_SECONDS = 0.4
REQUEST_TIMEOUT_SECONDS = 6.2
SESSION_END_TIMEOUT_SECONDS = 2.2
DEFAULT_IDLE_SECONDS = 300
MIN_IDLE_SECONDS = 30
MAX_IDLE_SECONDS = 3600
RUNTIME_DIRECTORY_NAME = "hook-runtime-v1"

_STATE_KEYS = frozenset(
    {"version", "identity", "token", "host", "port", "pid", "created_at"}
)
_CONTEXT_ENVIRONMENT_KEYS = (
    "APPDATA",
    "CLICK_CONFIG_HOME",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PLUGIN_DATA",
    "PYTHONHOME",
    "PYTHONPATH",
    "SystemRoot",
    "WINDIR",
    "XDG_CONFIG_HOME",
)


def _digest_json(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _random_token() -> str:
    return os.urandom(32).hex()


def _runtime_directory() -> Path | None:
    configured = os.environ.get("PLUGIN_DATA")
    if configured:
        base = Path(configured)
    else:
        import tempfile

        base = Path(tempfile.gettempdir()) / "click-plugin-data"
    path = base / RUNTIME_DIRECTORY_NAME
    try:
        if path.exists() or path.is_symlink():
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                return None
        else:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            return None
        if os.name != "nt":
            os.chmod(path, 0o700)
            if stat.S_IMODE(path.stat().st_mode) & 0o077:
                return None
        return path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None


def _plugin_identity() -> str | None:
    """Retire loaded code when installed source bytes change, even with old mtimes."""

    root = Path(__file__).resolve().parent.parent
    hooks = root / "hooks"
    records: list[tuple[str, str]] = []
    remaining_bytes = 8 * 1024 * 1024
    try:
        paths = sorted([*hooks.glob("*.py"), *hooks.glob("*.c")])
        if len(paths) > 256:
            return None
        for path in paths:
            metadata = path.stat()
            if not stat.S_ISREG(metadata.st_mode):
                return None
            with path.open("rb") as handle:
                payload = handle.read(remaining_bytes + 1)
            remaining_bytes -= len(payload)
            if remaining_bytes < 0:
                return None
            records.append((path.name, hashlib.sha256(payload).hexdigest()))
        fixed_files: list[tuple[str, str]] = []
        for relative in (".codex-plugin/plugin.json", "hooks/hooks.json"):
            path = root / relative
            with path.open("rb") as handle:
                payload = handle.read(remaining_bytes + 1)
            remaining_bytes -= len(payload)
            if remaining_bytes < 0:
                return None
            fixed_files.append((relative, hashlib.sha256(payload).hexdigest()))
    except (OSError, RuntimeError):
        return None
    return _digest_json(
        {"root": str(root), "hooks": records, "fixed_files": fixed_files}
    )


def _canonical_cwd(event: dict[str, object]) -> str | None:
    raw = event.get("cwd")
    candidate = Path(raw) if isinstance(raw, str) and raw else Path.cwd()
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return str(resolved) if resolved.is_dir() else None


def _context(event: dict[str, object]) -> tuple[Path, str, str, str] | None:
    session_id = event.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    runtime = _runtime_directory()
    plugin = _plugin_identity()
    cwd = _canonical_cwd(event)
    if runtime is None or plugin is None or cwd is None:
        return None
    environment = {
        key: os.environ.get(key, "") for key in _CONTEXT_ENVIRONMENT_KEYS
    }
    try:
        interpreter = str(Path(sys.executable).resolve(strict=True))
    except (OSError, RuntimeError):
        return None
    identity = _digest_json(
        {
            "plugin": plugin,
            "python": interpreter,
            "python_version": list(sys.version_info[:3]),
            "cwd": cwd,
            "environment": environment,
        }
    )
    session_key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    state_name = f"hook-{session_key}-{identity}.json"
    return runtime, state_name, identity, cwd


def _state_path(runtime: Path, state_name: str) -> Path:
    stem = state_name[:-5] if state_name.endswith(".json") else ""
    parts = stem.split("-")
    if (
        len(parts) != 3
        or parts[0] != "hook"
        or any(len(value) != 64 for value in parts[1:])
        or any(
            character not in "0123456789abcdef"
            for value in parts[1:]
            for character in value
        )
    ):
        raise ValueError("invalid Click Hook worker state name")
    return runtime / state_name


def _read_state(runtime: Path, state_name: str, identity: str) -> dict[str, object] | None:
    try:
        path = _state_path(runtime, state_name)
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > MAX_STATE_BYTES
            or (os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077)
        ):
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or set(value) != _STATE_KEYS:
        return None
    if value.get("version") != PROTOCOL_VERSION or value.get("identity") != identity:
        return None
    token = value.get("token")
    host = value.get("host")
    port = value.get("port")
    pid = value.get("pid")
    if (
        not isinstance(token, str)
        or len(token) != 64
        or any(character not in "0123456789abcdef" for character in token)
        or host != "127.0.0.1"
        or not isinstance(port, int)
        or not 0 < port < 65536
        or not isinstance(pid, int)
        or pid <= 0
    ):
        return None
    return value


def _unlink_state(runtime: Path, state_name: str) -> None:
    try:
        path = _state_path(runtime, state_name)
        if not path.is_symlink():
            path.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def _encoded_frame(value: object, limit: int) -> bytes:
    payload = json.dumps(
        value, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    if len(payload) > limit:
        raise ValueError("Click Hook worker frame exceeds its size limit")
    return struct.pack("!I", len(payload)) + payload


def _receive_exact(connection: socket.socket, size: int) -> bytes:
    result = bytearray()
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise ConnectionError("Click Hook worker connection closed")
        result.extend(chunk)
        remaining -= len(chunk)
    return bytes(result)


def receive_frame(connection: socket.socket, limit: int) -> object:
    header = _receive_exact(connection, 4)
    (size,) = struct.unpack("!I", header)
    if size > limit:
        raise ValueError("Click Hook worker frame exceeds its size limit")
    return json.loads(_receive_exact(connection, size).decode("utf-8"))


def send_frame(connection: socket.socket, value: object, limit: int) -> None:
    connection.sendall(_encoded_frame(value, limit))


def _request(
    state: dict[str, object], request: dict[str, object], timeout: float
) -> tuple[dict[str, object] | None, bool]:
    """Return (response, uncertain_delivery)."""

    sent = 0
    try:
        encoded = _encoded_frame(request, MAX_EVENT_BYTES)
        with socket.create_connection(
            (state["host"], state["port"]), timeout=CONNECT_TIMEOUT_SECONDS
        ) as connection:
            connection.settimeout(timeout)
            view = memoryview(encoded)
            while sent < len(encoded):
                written = connection.send(view[sent:])
                if written <= 0:
                    raise ConnectionError("Click Hook worker request send failed")
                sent += written
            response = receive_frame(connection, MAX_RESPONSE_BYTES)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
        return None, sent > 0
    if not isinstance(response, dict):
        return None, True
    return response, False


def _idle_seconds() -> int:
    raw = os.environ.get("CLICK_HOOK_WORKER_IDLE_SECONDS", "")
    try:
        value = int(raw) if raw else DEFAULT_IDLE_SECONDS
    except ValueError:
        value = DEFAULT_IDLE_SECONDS
    return max(MIN_IDLE_SECONDS, min(MAX_IDLE_SECONDS, value))


def _acquire_start_lock(path: Path) -> int | None:
    try:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            if time.time() - path.stat().st_mtime > STARTUP_TIMEOUT_SECONDS * 2:
                path.unlink()
                return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except (FileNotFoundError, OSError):
            pass
    except OSError:
        pass
    return None


def _start_worker(
    runtime: Path, state_name: str, identity: str
) -> dict[str, object] | None:
    lock_path = runtime / ".start"
    lock_fd = _acquire_start_lock(lock_path)
    owns_lock = lock_fd is not None
    process = None
    try:
        if lock_fd is not None:
            os.write(lock_fd, str(os.getpid()).encode("ascii"))
            os.close(lock_fd)
            lock_fd = None
            # Another client may have published after our initial lookup.
            existing = _read_state(runtime, state_name, identity)
            if existing is not None:
                return existing
            if sum(1 for _ in runtime.glob("hook-*.json")) >= MAX_WORKER_STATES:
                return None
            import subprocess

            worker = Path(__file__).with_name("click_hook_worker.py")
            creation_flags = 0
            popen_options: dict[str, object] = {"start_new_session": True}
            if os.name == "nt":
                creation_flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                creation_flags |= int(getattr(subprocess, "DETACHED_PROCESS", 0))
                popen_options = {"creationflags": creation_flags}
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(worker),
                    "--serve",
                    str(runtime),
                    state_name,
                    identity,
                    str(_idle_seconds()),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                **popen_options,
            )
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            state = _read_state(runtime, state_name, identity)
            if state is not None:
                return state
            if process is not None and process.poll() is not None:
                break
            time.sleep(0.02)
    except (OSError, ValueError):
        pass
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        if owns_lock and lock_path.exists() and not lock_path.is_symlink():
            try:
                lock_path.unlink()
            except OSError:
                pass
    if process is not None and process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
    return None


def _retire_previous_workers(runtime: Path, state_name: str, identity: str) -> None:
    session_prefix = "-".join(state_name.split("-", 2)[:2]) + "-"
    try:
        candidates = sorted(runtime.glob(f"{session_prefix}*.json"))[:MAX_WORKER_STATES]
    except OSError:
        return
    for path in candidates:
        if path.name == state_name or path.is_symlink():
            continue
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_STATE_BYTES:
                continue
            raw_state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw_state, dict) or raw_state.get("identity") == identity:
                continue
            previous_identity = raw_state.get("identity")
            if not isinstance(previous_identity, str):
                continue
            state = _read_state(runtime, path.name, previous_identity)
            if state is None:
                continue
            request_id = _random_token()[:32]
            request = {
                "version": PROTOCOL_VERSION,
                "token": state.get("token"),
                "request_id": request_id,
                "action": "shutdown",
            }
            response, uncertain = _request(state, request, CONNECT_TIMEOUT_SECONDS)
            if response is not None or not uncertain:
                _unlink_state(runtime, path.name)
        except (OSError, ValueError, json.JSONDecodeError):
            continue


def _validated_response(
    response: dict[str, object], request_id: str, worker_pid: int
) -> dict[str, object] | None:
    if (
        response.get("version") != PROTOCOL_VERSION
        or response.get("request_id") != request_id
        or response.get("worker_pid") != worker_pid
        or not isinstance(response.get("returncode"), int)
        or not isinstance(response.get("stdout"), str)
        or not isinstance(response.get("stderr"), str)
    ):
        return None
    return response


def invoke(mode: str, event: dict[str, object]) -> dict[str, object] | None:
    """Use the resident worker, or return None for a safe one-shot fallback.

    Once any request byte has been sent, failure returns a blocking response and
    is never replayed through the one-shot gate.
    """

    if mode not in HOOK_MODES:
        return None
    if os.environ.get("CLICK_HOOK_WORKER", "").strip().lower() in {
        "0", "false", "no", "off",
    }:
        return None
    budget = SESSION_END_TIMEOUT_SECONDS if mode == "session-end" else REQUEST_TIMEOUT_SECONDS
    deadline = time.time() + budget
    context = _context(event)
    if context is None:
        return None
    runtime, state_name, identity, cwd = context
    state = _read_state(runtime, state_name, identity)
    if state is None:
        _retire_previous_workers(runtime, state_name, identity)
        # SessionEnd needs no new resident process just to shut it down.
        if mode == "session-end":
            return None
        state = _start_worker(runtime, state_name, identity)
        if state is None:
            return None

    request_id = _random_token()[:32]
    request = {
        "version": PROTOCOL_VERSION,
        "token": state["token"],
        "request_id": request_id,
        "action": "hook",
        "mode": mode,
        "event": event,
        "cwd": cwd,
        "environment": dict(os.environ),
        "deadline": deadline - 0.15,
    }
    timeout = max(0.05, deadline - time.time())
    response, uncertain = _request(state, request, timeout)
    if response is not None:
        validated = _validated_response(response, request_id, state["pid"])
        if validated is not None:
            return validated
        uncertain = True
    if uncertain:
        return {
            "version": PROTOCOL_VERSION,
            "request_id": request_id,
            "worker_pid": state["pid"],
            "returncode": 1,
            "stdout": "",
            "stderr": (
                "click hook error: resident worker response was lost; "
                "the event was not replayed\n"
            ),
        }

    current = _read_state(runtime, state_name, identity)
    if current is not None and current.get("token") == state["token"]:
        _unlink_state(runtime, state_name)
    return None


def publish_state(
    runtime: Path, state_name: str, identity: str, token: str, port: int
) -> Path:
    path = _state_path(runtime, state_name)
    temporary = runtime / f".{state_name}.{os.getpid()}.tmp"
    value = {
        "version": PROTOCOL_VERSION,
        "identity": identity,
        "token": token,
        "host": "127.0.0.1",
        "port": port,
        "pid": os.getpid(),
        "created_at": int(time.time()),
    }
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return path


def remove_owned_state(path: Path, identity: str) -> None:
    try:
        if path.is_symlink():
            return
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            isinstance(value, dict)
            and value.get("identity") == identity
            and value.get("pid") == os.getpid()
        ):
            path.unlink(missing_ok=True)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
