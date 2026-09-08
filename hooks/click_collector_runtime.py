#!/usr/bin/env python3
"""Cross-platform bounded process adapter for test collection and setup probes."""

from __future__ import annotations

from dataclasses import dataclass
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from . import click_process
else:
    import click_process


SUPPORTED_SYSTEMS = frozenset({"Linux", "Darwin", "Windows"})
MIN_CPYTHON = (3, 10, 0)
MAX_CPYTHON = (3, 15, 0)


class CollectorRuntimeError(ValueError):
    """A stable process/runtime failure without captured project output."""


@dataclass(frozen=True, slots=True)
class RuntimeCapability:
    system: str
    process_adapter: str
    status: str
    reason: str


def capability(system_name: str | None = None) -> RuntimeCapability:
    try:
        system = platform.system() if system_name is None else system_name
    except Exception:
        system = ""
    if system not in SUPPORTED_SYSTEMS:
        return RuntimeCapability(system[:64], "unavailable", "unsupported", "unsupported-platform")
    adapter = "windows-threaded-pipes-v1" if system == "Windows" else "posix-threaded-pipes-v1"
    return RuntimeCapability(system, adapter, "implemented", "runtime-validation-required")


def cpython_supported(implementation: str, version: Sequence[int]) -> bool:
    try:
        normalized = tuple(int(item) for item in version[:3])
    except (TypeError, ValueError):
        return False
    return bool(
        implementation == "cpython"
        and len(normalized) == 3
        and MIN_CPYTHON <= normalized < MAX_CPYTHON
    )


def supervise(
    argv: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
    *,
    timeout: float,
    output_bytes: int,
    capture_output: bool = False,
) -> tuple[bytes, bytes] | None:
    """Run one shell-free child while draining both pipes within a hard bound."""
    if (
        not argv
        or timeout <= 0
        or output_bytes <= 0
        or capability().status != "implemented"
    ):
        raise CollectorRuntimeError("unsupported-platform")
    try:
        process = click_process.spawn_argv(
            list(argv),
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CollectorRuntimeError("collector-start-failed") from exc

    lock = threading.Lock()
    consumed = 0
    overflow = threading.Event()
    reader_failed = threading.Event()
    captured = [bytearray(), bytearray()]

    def drain(pipe: Any, index: int) -> None:
        nonlocal consumed
        try:
            while True:
                chunk = pipe.read(16_384)
                if not chunk:
                    return
                with lock:
                    consumed += len(chunk)
                    if consumed > output_bytes:
                        overflow.set()
                        return
                    if capture_output:
                        captured[index].extend(chunk)
        except (OSError, ValueError):
            reader_failed.set()

    readers = [
        threading.Thread(target=drain, args=(pipe, index), daemon=True)
        for index, pipe in enumerate((process.stdout, process.stderr))
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout
    reason = ""
    try:
        while process.poll() is None:
            if overflow.is_set():
                reason = "collection-output-limit"
                break
            if reader_failed.is_set():
                reason = "collector-output-failed"
                break
            if time.monotonic() >= deadline:
                reason = "collection-timeout"
                break
            time.sleep(min(0.02, max(0.001, deadline - time.monotonic())))
        if reason:
            click_process.terminate_process_group(process)
        else:
            remaining = max(0.001, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                reason = "collection-timeout"
                click_process.terminate_process_group(process)
    except BaseException:
        click_process.terminate_process_group(process)
        raise
    finally:
        for reader in readers:
            reader.join(timeout=1)
        for pipe in (process.stdout, process.stderr):
            try:
                pipe.close()
            except (AttributeError, OSError):
                pass
    if not reason and overflow.is_set():
        reason = "collection-output-limit"
    if not reason and reader_failed.is_set():
        reason = "collector-output-failed"
    if reason:
        raise CollectorRuntimeError(reason)
    if process.returncode:
        raise CollectorRuntimeError("collector-failed")
    return (bytes(captured[0]), bytes(captured[1])) if capture_output else None
