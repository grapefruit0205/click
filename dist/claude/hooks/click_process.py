#!/usr/bin/env python3
"""Shell-free operating-system process mechanics for Click runners.

This module deliberately knows nothing about contracts, capabilities, trusted
executables, Git/SSH policy, state claims, or evidence. Callers must complete
those decisions before passing an argv sequence here.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
import signal
import subprocess
import threading
import time
from typing import Any, BinaryIO, Mapping, Sequence


_target_start = ContextVar("click_target_start", default=None)


_TRUNCATION_MARKER = b"\n[Click retained output truncated]\n"


@dataclass(frozen=True)
class CapturedStream:
    """One bounded retained stream plus its exact observed byte count."""

    data: bytes
    total_bytes: int
    truncated: bool
    reader_error: bool = False


@dataclass(frozen=True)
class CapturedProcess:
    """Process result whose streams were drained once and retained by bounds."""

    args: list[str]
    returncode: int
    stdout: CapturedStream
    stderr: CapturedStream


class _BoundedStream:
    def __init__(self, limit: int) -> None:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1024:
            raise ValueError("capture limit must be an integer of at least 1024 bytes")
        payload_limit = max(0, limit - len(_TRUNCATION_MARKER))
        self._head_limit = payload_limit // 2
        self._tail_limit = payload_limit - self._head_limit
        self._head = bytearray()
        self._tail = bytearray()
        self.total_bytes = 0
        self.reader_error = False

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.total_bytes += len(chunk)
        remaining = chunk
        if len(self._head) < self._head_limit:
            take = min(self._head_limit - len(self._head), len(remaining))
            self._head.extend(remaining[:take])
            remaining = remaining[take:]
        if remaining and self._tail_limit:
            self._tail.extend(remaining)
            if len(self._tail) > self._tail_limit:
                del self._tail[: len(self._tail) - self._tail_limit]

    def result(self) -> CapturedStream:
        retained = bytes(self._head + self._tail)
        truncated = self.total_bytes > len(retained)
        data = (
            bytes(self._head) + _TRUNCATION_MARKER + bytes(self._tail)
            if truncated
            else retained
        )
        return CapturedStream(
            data=data,
            total_bytes=self.total_bytes,
            truncated=truncated,
            reader_error=self.reader_error,
        )


class OutputCapture:
    """Bounded output for an externally managed, one-time target execution.

    Collectors own admission, waiting and cancellation. This object only owns
    output pipes; collector diagnostics must never be passed through it.
    """

    def __init__(self, *, limit: int = 65536, tee: bool = False) -> None:
        import sys
        self.streams = [_BoundedStream(limit), _BoundedStream(limit)]
        self.targets = [getattr(sys.stdout, "buffer", None), getattr(sys.stderr, "buffer", None)] if tee else [None, None]
        self.readers: list[threading.Thread] = []
        self.started = False

    def spawn(self, spawner, argv, **kwargs):
        # Explicit pipes belong to the collector, not the requested check.
        if "stdout" in kwargs or "stderr" in kwargs:
            return spawner(argv, **kwargs)
        if self.started:
            raise RuntimeError("target output capture already admitted")
        pipes = []
        try:
            for _ in range(2):
                read_fd, write_fd = os.pipe()
                pipes.append((os.fdopen(read_fd, "rb", buffering=0), os.fdopen(write_fd, "wb", buffering=0)))
            for index, (source, sink) in enumerate(pipes):
                reader = threading.Thread(target=_drain_stream, args=(source, self.streams[index], self.targets[index]), daemon=True)
                self.readers.append(reader)
                reader.start()
            # Reader preparation can fail. Finish it before admitting the
            # target so callers may safely use their pre-start fallback.
            child = spawner(argv, **kwargs, stdout=pipes[0][1], stderr=pipes[1][1])
            self.started = True
            for _, sink in pipes:
                try:
                    sink.close()
                except OSError:
                    pass  # A launched target must never be reported unstarted.
            return child
        except BaseException:
            for source, sink in pipes:
                source.close()
                sink.close()
            raise

    def finish(self, argv, returncode: int) -> CapturedProcess | None:
        if not self.started:
            return None
        for index, reader in enumerate(self.readers):
            reader.join(timeout=3)
            if reader.is_alive():
                self.streams[index].reader_error = True
        return CapturedProcess(list(argv), int(returncode), *(stream.result() for stream in self.streams))


def _drain_stream(
    source: BinaryIO,
    retained: _BoundedStream,
    target: BinaryIO | None,
) -> None:
    try:
        while True:
            chunk = source.read(16_384)
            if not chunk:
                break
            retained.feed(chunk)
            if target is not None:
                try:
                    target.write(chunk)
                    target.flush()
                except (BrokenPipeError, OSError, ValueError):
                    target = None
    except (OSError, ValueError):
        retained.reader_error = True
    finally:
        try:
            source.close()
        except (OSError, ValueError):
            pass


@contextmanager
def observe_target_start(callback):
    """Observe target admission only, never collector/probe subprocesses."""
    token = _target_start.set(callback)
    try:
        yield
    finally:
        _target_start.reset(token)


def target_started() -> None:
    callback = _target_start.get()
    if callback is not None:
        try:
            callback()
        except Exception:
            pass  # Measurement cannot fail or repeat an authorized command.


def isolated_subprocess_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


@contextmanager
def termination_as_interrupt():
    """Let runner cleanup record SIGTERM as interruption, not a stranded claim."""
    active = threading.current_thread() is threading.main_thread()
    previous = signal.getsignal(signal.SIGTERM) if active else None
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    if active:
        signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        if active:
            signal.signal(signal.SIGTERM, previous)


def run_argv(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    stdin: Any | None = None,
    stdout: Any | None = None,
    stderr: Any | None = None,
    timeout: float | None = None,
    target: bool = False,
) -> subprocess.CompletedProcess[Any]:
    """Run one already-authorized argv without a shell in an isolated group."""
    command = list(argv)
    child = spawn_argv(
        command,
        cwd=cwd,
        env=env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )
    if target:
        target_started()
    try:
        with termination_as_interrupt():
            captured_stdout, captured_stderr = child.communicate(timeout=timeout)
    except BaseException:
        terminate_process_group(child)
        raise
    return subprocess.CompletedProcess(
        command,
        int(child.returncode),
        captured_stdout,
        captured_stderr,
    )


def run_argv_captured(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    stdin: Any | None = None,
    stdout_target: BinaryIO | None = None,
    stderr_target: BinaryIO | None = None,
    capture_limit_bytes: int = 64 * 1024,
    timeout: float | None = None,
    target: bool = False,
) -> CapturedProcess:
    """Run once, drain both pipes concurrently, and retain bounded output.

    Full output is optionally forwarded while the child runs. Retention never
    controls the child's pipe, so a long stream cannot deadlock or cause a
    verification command to be repeated.
    """

    command = list(argv)
    child = spawn_argv(
        command,
        cwd=cwd,
        env=env,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if target:
        target_started()
    if child.stdout is None or child.stderr is None:
        terminate_process_group(child)
        raise OSError("captured process pipes were unavailable")
    captured_stdout = _BoundedStream(capture_limit_bytes)
    captured_stderr = _BoundedStream(capture_limit_bytes)
    threads = [
        threading.Thread(
            target=_drain_stream,
            args=(child.stdout, captured_stdout, stdout_target),
            daemon=True,
        ),
        threading.Thread(
            target=_drain_stream,
            args=(child.stderr, captured_stderr, stderr_target),
            daemon=True,
        ),
    ]
    for reader in threads:
        reader.start()
    try:
        with termination_as_interrupt():
            child.wait(timeout=timeout)
    except BaseException:
        terminate_process_group(child)
        for reader in threads:
            reader.join(timeout=3)
        raise
    for reader in threads:
        reader.join(timeout=3)
    if threads[0].is_alive():
        captured_stdout.reader_error = True
    if threads[1].is_alive():
        captured_stderr.reader_error = True
    return CapturedProcess(
        args=command,
        returncode=int(child.returncode),
        stdout=captured_stdout.result(),
        stderr=captured_stderr.result(),
    )


def spawn_argv(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    stdin: Any | None = None,
    stdout: Any | None = None,
    stderr: Any | None = None,
    close_fds: bool = True,
) -> subprocess.Popen[Any]:
    """Spawn one already-authorized argv without a shell in an isolated group."""
    return subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        close_fds=close_fds,
        shell=False,
        **isolated_subprocess_kwargs(),
    )


def terminate_process_group(
    child: subprocess.Popen[Any], *, grace_seconds: float = 3.0
) -> int:
    """Stop an isolated child group, escalating once when graceful stop fails."""
    if os.name != "nt":
        # An exited group leader does not imply its descendants have stopped.
        # Keep the original isolated group id and give the whole group the
        # remaining grace period, even when waiting for the leader succeeds.
        deadline = time.monotonic() + grace_seconds
        try:
            os.killpg(child.pid, signal.SIGTERM)
            returncode = child.wait(timeout=max(0.0, deadline - time.monotonic()))
            while True:
                try:
                    os.killpg(child.pid, 0)
                except ProcessLookupError:
                    return int(returncode)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.02, remaining))
        except ProcessLookupError:
            try:
                return int(child.wait(timeout=grace_seconds))
            except (OSError, subprocess.TimeoutExpired):
                return 1
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            return 1
        try:
            return int(child.wait(timeout=grace_seconds))
        except (OSError, subprocess.TimeoutExpired):
            return 1

    if child.poll() is not None:
        return int(child.returncode or 0)
    try:
        if os.name == "nt":
            ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
            if ctrl_break is not None:
                child.send_signal(ctrl_break)
            else:
                child.terminate()
        else:
            os.killpg(child.pid, signal.SIGTERM)
        return int(child.wait(timeout=grace_seconds))
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == "nt":
                child.kill()
            else:
                os.killpg(child.pid, signal.SIGKILL)
            return int(child.wait(timeout=grace_seconds))
        except (OSError, subprocess.TimeoutExpired):
            return 1


def copy_limited_output(
    source: Any,
    target: Any,
    remaining: int,
    *,
    chunk_size: int = 16_384,
) -> int:
    """Copy at most *remaining* bytes and return the number actually copied."""
    copied = 0
    while copied < remaining:
        chunk = source.read(min(chunk_size, remaining - copied))
        if not chunk:
            break
        target.write(chunk)
        target.flush()
        copied += len(chunk)
    return copied
