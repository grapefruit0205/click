#!/usr/bin/env python3
"""Filesystem identity, persistence, locking, and runner-state recovery for Click.

The normal state files remain the source of truth.  A short-lived recovery mirror
is written only while an approved stateful runner has an outstanding one-use
authorization.  A runner may restore that exact state only when its bound path,
request/service binding, and runner token match the mirror.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import time
import sys
from typing import Any, Iterator
import zlib


STATE_LOCK_TIMEOUT_SECONDS = 5
STATE_LOCK_STALE_SECONDS = 30
RUNNER_TRANSPORT_MAX_BYTES = 24_000
RECOVERY_SCHEMA_VERSION = 1
RECOVERY_DIR_NAME = "gate-recovery"
RECOVERY_SNAPSHOT_MAX_AGE_SECONDS = 24 * 60 * 60
MUTATION_RESERVATION_SECONDS = 10 * 60
VERIFICATION_RESERVATION_SECONDS = 60 * 60
SERVICE_RESERVATION_SECONDS = 16
RECOVERABLE_RUNNER_ACTIONS = {
    "run-mutation",
    "run-service-start",
    "run-service-supervisor",
    "run-verification",
}


def state_root() -> Path:
    configured = os.environ.get("PLUGIN_DATA")
    if configured:
        return Path(configured) / "gate-state"
    return Path(tempfile.gettempdir()) / "click-plugin-data" / "gate-state"


def preference_path() -> Path:
    configured = os.environ.get("CLICK_CONFIG_HOME")
    if configured:
        return Path(configured) / "preferences.json"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "Click" / "preferences.json"
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "click" / "preferences.json"
    return Path.home() / ".config" / "click" / "preferences.json"


def identity_path(event: dict[str, Any], scope: str) -> Path:
    identity = {
        "session_id": str(event.get("session_id", "")),
        "cwd": str(event.get("cwd", "")),
    }
    if scope in {"turn", "review"}:
        identity["turn_id"] = str(event.get("turn_id", ""))
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    name = f"{scope}-{hashlib.sha256(encoded).hexdigest()}.json"
    return state_root() / name


def _lexical_absolute(path: Path) -> Path | None:
    if not path.is_absolute():
        return None
    lexical = Path(os.path.abspath(path))
    return lexical if path == lexical else None


def _canonical_recovery_state_path(path: Path) -> Path:
    """Canonicalize existing aliases without requiring the state file to exist."""
    lexical = _lexical_absolute(path) or Path(os.path.abspath(path))
    try:
        return lexical.resolve(strict=False)
    except (OSError, RuntimeError):
        return lexical


def _recovery_root_for_state(path: Path) -> Path:
    return path.parent.parent / RECOVERY_DIR_NAME


def _recovery_snapshot_path(path: Path) -> Path:
    canonical = _canonical_recovery_state_path(path)
    key = hashlib.sha256(os.path.normcase(str(canonical)).encode()).hexdigest()
    return _recovery_root_for_state(canonical) / f"{key}.json"


def _remove_recovery_snapshot(path: Path) -> None:
    try:
        _recovery_snapshot_path(path).unlink(missing_ok=True)
    except OSError:
        pass


_PathBase = type(Path())


class _ContractStatePath(_PathBase):
    """Path whose explicit unlink also revokes an outstanding recovery mirror."""

    def unlink(self, missing_ok: bool = False) -> None:
        if self.parent.name == "gate-state" and self.name.startswith("session-contract-"):
            _remove_recovery_snapshot(self)
        super().unlink(missing_ok=missing_ok)


def state_path(event: dict[str, Any]) -> Path:
    return identity_path(event, "turn")


def mode_path(event: dict[str, Any]) -> Path:
    return identity_path(event, "session")


def contract_path(event: dict[str, Any]) -> Path:
    return _ContractStatePath(str(identity_path(event, "session-contract")))


def prompt_path(event: dict[str, Any]) -> Path:
    return identity_path(event, "session-prompt")


def review_path(event: dict[str, Any]) -> Path:
    return identity_path(event, "review")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".gate-",
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        temporary = Path(handle.name)
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _approved_state_has_recoverable_runner(payload: dict[str, Any]) -> bool:
    if payload.get("status") not in {"approved", "evidence"}:
        return False
    mutation = payload.get("mutation")
    if (
        isinstance(mutation, dict)
        and mutation.get("status") == "running"
        and _valid_digest(mutation.get("request_digest"))
        and _valid_digest(mutation.get("runner_token_digest"))
    ):
        return True
    verification = payload.get("verification")
    if (
        isinstance(verification, dict)
        and verification.get("status") == "running"
        and _valid_digest(verification.get("last_batch_digest"))
        and _valid_digest(verification.get("runner_token_digest"))
    ):
        return True
    service = payload.get("service")
    return bool(
        isinstance(service, dict)
        and service.get("status") in {"starting", "launching"}
        and isinstance(service.get("service_id"), str)
        and bool(service.get("service_id"))
        and _valid_digest(service.get("runner_token_digest"))
    )


def _prune_recovery_snapshots(root: Path) -> None:
    try:
        candidates = list(root.glob("*.json"))
    except OSError:
        return
    now = time.time()
    for candidate in candidates:
        try:
            if now - candidate.stat().st_mtime <= RECOVERY_SNAPSHOT_MAX_AGE_SECONDS:
                continue
            candidate.unlink()
        except OSError:
            pass


def _sync_recovery_snapshot(path: Path, payload: dict[str, Any]) -> None:
    if not (
        path.parent.name == "gate-state"
        and path.name.startswith("session-contract-")
        and path.suffix == ".json"
    ):
        return
    canonical_path = _canonical_recovery_state_path(path)
    recovery_path = _recovery_snapshot_path(canonical_path)
    if not _approved_state_has_recoverable_runner(payload):
        _remove_recovery_snapshot(canonical_path)
        return
    snapshot = {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "state_path": str(canonical_path),
        "state": payload,
        "updated_at": int(time.time()),
    }
    _prune_recovery_snapshots(recovery_path.parent)
    _atomic_write_json(recovery_path, snapshot)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_json(path, payload)
    _sync_recovery_snapshot(path, payload)


def _decode_encoded_runner(arguments: list[str]) -> list[str] | None:
    if not arguments or arguments[0] != "--encoded-runner":
        return arguments
    if len(arguments) != 2:
        return None
    try:
        compressed = base64.b64decode(
            arguments[1].encode(), altchars=b"-_", validate=True
        )
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(compressed, RUNNER_TRANSPORT_MAX_BYTES + 1)
        if (
            len(raw) > RUNNER_TRANSPORT_MAX_BYTES
            or not decompressor.eof
            or decompressor.unconsumed_tail
            or decompressor.unused_data
        ):
            return None
        value = json.loads(raw.decode())
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, zlib.error):
        return None
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or "\x00" in item for item in value)
    ):
        return None
    return value


def _fresh_unclaimed(started_at: Any, ttl: int) -> bool:
    if not isinstance(started_at, int) or isinstance(started_at, bool) or started_at <= 0:
        return False
    age = time.time() - started_at
    return 0 <= age <= ttl


def _runner_binding_matches(
    state: dict[str, Any], action: str, arguments: list[str]
) -> bool:
    if state.get("status") not in {"approved", "evidence"}:
        return False
    try:
        if action == "run-mutation":
            if len(arguments) != 7:
                return False
            request_digest, runner_token = arguments[4], arguments[5]
            mutation = state.get("mutation")
            if not isinstance(mutation, dict) or mutation.get("status") != "running":
                return False
            if mutation.get("request_digest") != request_digest:
                return False
            token_digest = hashlib.sha256(runner_token.encode()).hexdigest()
            if not hmac.compare_digest(
                str(mutation.get("runner_token_digest", "")), token_digest
            ):
                return False
            claimed_at = mutation.get("runner_claimed_at", 0)
            return bool(
                isinstance(claimed_at, int)
                and not isinstance(claimed_at, bool)
                and (
                    claimed_at > 0
                    or _fresh_unclaimed(
                        mutation.get("started_at", 0), MUTATION_RESERVATION_SECONDS
                    )
                )
            )

        if action == "run-verification":
            if len(arguments) != 7:
                return False
            batch_digest, runner_token = arguments[4], arguments[5]
            verification = state.get("verification")
            if not isinstance(verification, dict) or verification.get("status") != "running":
                return False
            if verification.get("last_batch_digest") != batch_digest:
                return False
            token_digest = hashlib.sha256(runner_token.encode()).hexdigest()
            if not hmac.compare_digest(
                str(verification.get("runner_token_digest", "")), token_digest
            ):
                return False
            claimed_at = verification.get("runner_claimed_at", 0)
            return bool(
                isinstance(claimed_at, int)
                and not isinstance(claimed_at, bool)
                and (
                    claimed_at > 0
                    or _fresh_unclaimed(
                        verification.get("started_at", 0),
                        VERIFICATION_RESERVATION_SECONDS,
                    )
                )
            )

        if action in {"run-service-start", "run-service-supervisor"}:
            if len(arguments) != 8:
                return False
            service_id, runner_token = arguments[4], arguments[5]
            service = state.get("service")
            if not isinstance(service, dict) or service.get("service_id") != service_id:
                return False
            if service.get("status") not in {"starting", "launching"}:
                return False
            token_digest = hashlib.sha256(runner_token.encode()).hexdigest()
            if not hmac.compare_digest(
                str(service.get("runner_token_digest", "")), token_digest
            ):
                return False
            runner_claimed_at = service.get("runner_claimed_at", 0)
            supervisor_claimed_at = service.get("supervisor_claimed_at", 0)
            if any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in (runner_claimed_at, supervisor_claimed_at)
            ):
                return False
            if action == "run-service-start":
                return bool(
                    service.get("status") == "starting"
                    and runner_claimed_at == 0
                    and supervisor_claimed_at == 0
                    and _fresh_unclaimed(
                        service.get("started_at", 0), SERVICE_RESERVATION_SECONDS
                    )
                )
            return bool(
                service.get("status") == "launching"
                and runner_claimed_at > 0
                and (
                    supervisor_claimed_at > 0
                    or _fresh_unclaimed(
                        runner_claimed_at, SERVICE_RESERVATION_SECONDS
                    )
                )
            )
    except (IndexError, TypeError, ValueError):
        return False
    return False


def _runner_recovery_binding() -> tuple[Path, Path, str, list[str]] | None:
    arguments = _decode_encoded_runner(list(sys.argv[1:]))
    if arguments is None or len(arguments) < 4 or arguments[0] != "--state-root":
        return None
    action = arguments[2]
    if action not in RECOVERABLE_RUNNER_ACTIONS:
        return None
    root = Path(arguments[1])
    state = Path(arguments[3])
    root_lexical = _lexical_absolute(root)
    state_lexical = _lexical_absolute(state)
    if root_lexical is None or state_lexical is None:
        return None
    if root_lexical.name != "gate-state" or state_lexical.parent != root_lexical:
        return None
    if not state_lexical.name.startswith("session-contract-") or state_lexical.suffix != ".json":
        return None
    try:
        if root_lexical.resolve(strict=False) != root_lexical:
            return None
        if state_lexical.resolve(strict=False) != state_lexical:
            return None
    except (OSError, RuntimeError):
        return None
    return root_lexical, state_lexical, action, arguments


def _exclusive_restore_json(path: Path, payload: dict[str, Any]) -> bool:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return True
    except OSError:
        return False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
    except OSError:
        try:
            path.unlink()
        except OSError:
            pass
        return False
    return True


def _restore_runner_state_if_authorized(expected_path: Path | None = None) -> bool:
    binding = _runner_recovery_binding()
    if binding is None:
        return False
    root, state_path_value, action, arguments = binding
    if expected_path is not None:
        expected = _canonical_recovery_state_path(expected_path)
        if expected != _canonical_recovery_state_path(state_path_value):
            return False
    if state_path_value.exists():
        return True
    canonical_state_path = _canonical_recovery_state_path(state_path_value)
    recovery_path = _recovery_snapshot_path(canonical_state_path)
    try:
        if recovery_path.is_symlink():
            return False
        resolved_recovery_root = recovery_path.parent.resolve(strict=True)
        if resolved_recovery_root != recovery_path.parent:
            return False
        snapshot = json.loads(recovery_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, RuntimeError):
        return False
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != RECOVERY_SCHEMA_VERSION:
        return False
    if snapshot.get("state_path") != str(canonical_state_path):
        return False
    payload = snapshot.get("state")
    if not isinstance(payload, dict) or not _runner_binding_matches(payload, action, arguments):
        return False
    updated_at = snapshot.get("updated_at", 0)
    if (
        not isinstance(updated_at, int)
        or isinstance(updated_at, bool)
        or updated_at <= 0
        or time.time() - updated_at > RECOVERY_SNAPSHOT_MAX_AGE_SECONDS
    ):
        _remove_recovery_snapshot(canonical_state_path)
        return False
    try:
        root.mkdir(mode=0o700, parents=False, exist_ok=True)
        if root.resolve(strict=True) != root or root.is_symlink():
            return False
    except (OSError, RuntimeError):
        return False
    return _exclusive_restore_json(state_path_value, payload)


def managed_state_path(path: Path, prefixes: tuple[str, ...]) -> bool:
    """Return whether *path* is one canonical managed state file."""
    try:
        if not path.is_absolute():
            return False
        if not path.exists():
            _restore_runner_state_if_authorized(path)
        resolved_path = path.resolve(strict=True)
        lexical_path = Path(os.path.abspath(path))
        resolved_root = state_root().resolve(strict=True)
        return (
            path == lexical_path
            and not path.is_symlink()
            and resolved_path.parent == resolved_root
            and resolved_path.name.startswith(prefixes)
            and resolved_path.suffix == ".json"
        )
    except (OSError, RuntimeError):
        return False


@contextmanager
def state_lock() -> Iterator[None]:
    root = state_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = root / ".state.lock"
    deadline = time.monotonic() + STATE_LOCK_TIMEOUT_SECONDS
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.write(descriptor, str(os.getpid()).encode())
        except (FileExistsError, PermissionError) as exc:
            # Windows can report an O_EXCL collision as EACCES while another
            # process still owns the lock file. Treat that specific shape as
            # contention and retry instead of failing the observation runner.
            if (
                isinstance(exc, PermissionError)
                and os.name != "nt"
                and not lock_path.exists()
            ):
                raise
            try:
                stale = time.time() - lock_path.stat().st_mtime > STATE_LOCK_STALE_SECONDS
            except OSError:
                stale = False
            if stale:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                else:
                    continue
            if time.monotonic() >= deadline:
                raise OSError("timed out waiting for Click state lock")
            time.sleep(0.025)
    try:
        yield
    finally:
        try:
            os.close(descriptor)
        finally:
            try:
                lock_path.unlink()
            except OSError:
                pass


# Stateful runners import this module before the gate entrypoint validates the
# explicit --state-root binding. Restore only a token-bound approved snapshot so
# Path.resolve(strict=True) can reach the existing claim logic.
_restore_runner_state_if_authorized()
