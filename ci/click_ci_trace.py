#!/usr/bin/env python3
"""Observe one CI command with strace and reduce the trace to its inputs.

Click's agent-side observer bounds its trace for a single hook-driven check.
A CI test step reads far more, so this collector streams strace's output from
a FIFO, keeps no size limit, and stitches the ``<unfinished ...>`` and
``<... resumed>`` halves that ``-f`` produces when processes run concurrently.

The result keeps, per absolute path, the state the command found before it
touched the path: what it read, listed, executed or looked up and did not
find. Paths the command created itself are its products, not its inputs. A
pre-existing input the command later changed, an external network connection,
or a line the parser cannot place makes the observation *volatile*: its
command is never skipped.
"""

from __future__ import annotations

import ast
import ctypes
from dataclasses import dataclass, field
import os
import posixpath
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import tempfile
import threading
import time
from typing import Iterable, Sequence


TRACE_EXPRESSION = "trace=%file,%process,getdents64,getdents,connect,ptrace"
STRING_LIMIT = 4096
LINGER_SECONDS = 5.0

_PID = re.compile(r"^(\d+)\s+")
_CALL = re.compile(r"^([A-Za-z0-9_]+)\((.*)\)\s+=\s+(.+)$")
_QUOTED = re.compile(r'"(?:\\.|[^"\\])*"(\.\.\.)?')
_FD_ANNOTATION = re.compile(r"(?:-?\d+|AT_FDCWD)<([^<>]*)>")
_RESULT_PATH = re.compile(r"^-?\d+<([^<>]*)>")
_RESULT_INT = re.compile(r"^(-?\d+)")
_EXIT = re.compile(r"^\+\+\+ exited with (\d+) \+\+\+$")
_KILLED = re.compile(r"^\+\+\+ killed by (SIG[A-Z0-9]+)")
_UNFINISHED = " <unfinished ...>"
_RESUMED = re.compile(r"^<\.\.\. ([A-Za-z0-9_]+|\?\?\?) resumed>(.*)$")
# A process killed at a syscall stop: strace could not read which call it was
# entering, and the call never ran (a tracee woken by SIGKILL exits at once).
_DYING = re.compile(r"^\?\?\?\(.*\)\s+=\s+\?$")
# A thread other than the leader called execve: the kernel gives it the
# leader's pid and strace finishes the call under that pid.
_PID_CHANGED = re.compile(r"^(.*) <pid changed to (\d+) \.\.\.>$")
_HANDOFF_RESULT = re.compile(r"=\s*-1 \(errno \d+\)\s*$")
MAX_UNRESOLVED_EXAMPLES = 5
_INET = re.compile(r"sa_family=AF_INET6?,.*?(?:inet_addr\(\"([^\"]+)\"\)|inet_pton\(AF_INET6, \"([^\"]+)\")")
_PORT = re.compile(r"htons\((\d+)\)")
_UNIX = re.compile(r'sa_family=AF_UNIX, sun_path=(@?)"')

_EXEC = frozenset({"execve", "execveat"})
_OPEN = frozenset({"open", "openat", "openat2", "creat"})
_METADATA = frozenset({
    "access", "faccessat", "faccessat2", "lstat", "stat", "newfstatat", "statx",
    "readlink", "readlinkat", "getxattr", "lgetxattr", "listxattr", "llistxattr",
})
_LIST = frozenset({"getdents64", "getdents"})
_CREATE = frozenset({"mkdir", "mkdirat", "mknod", "mknodat"})
_DELETE = frozenset({"unlink", "unlinkat", "rmdir"})
_RENAME = frozenset({"rename", "renameat", "renameat2"})
_LINK = frozenset({"link", "linkat"})
_SYMLINK = frozenset({"symlink", "symlinkat"})
_MODIFY = frozenset({
    "truncate", "chmod", "fchmodat", "fchmodat2", "chown", "lchown", "fchownat",
    "utime", "utimes", "utimensat", "futimesat", "setxattr", "lsetxattr",
    "removexattr", "lremovexattr",
})
_CHILD = frozenset({"clone", "clone3", "fork", "vfork"})
# Calls in the traced classes that name no input and change nothing the
# command later reads.
_IGNORED = frozenset({
    "getcwd", "statfs", "fstatfs", "inotify_add_watch", "fanotify_mark",
    "exit", "exit_group", "wait4", "waitid", "waitpid", "kill", "tgkill", "tkill",
    "pidfd_open", "pidfd_send_signal", "pidfd_getfd", "rt_sigqueueinfo",
    "rt_tgsigqueueinfo", "setpgid", "setsid", "getpgid", "getsid", "getpgrp",
    "prctl", "arch_prctl", "unshare", "setns", "seccomp", "personality",
})
_WRITE_FLAGS = ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND")


@dataclass
class PathState:
    """What the command did to one path, in trace order."""

    first: str  # input | missing | produced | touched | deleted | opened
    operations: set[str] = field(default_factory=set)
    modified_after_input: bool = False


@dataclass
class Observation:
    exit_code: int
    paths: dict[str, PathState]
    volatile_reasons: list[str]
    unresolved_lines: int
    process_count: int
    duration_seconds: float
    unresolved_examples: list[str] = field(default_factory=list)

    @property
    def volatile(self) -> bool:
        return bool(self.volatile_reasons) or self.unresolved_lines > 0


def _decode(literal: str) -> str | None:
    """Decode strace's C string: escapes are bytes, the path is UTF-8."""
    try:
        value = ast.literal_eval("b" + literal)
    except (SyntaxError, ValueError):
        try:
            value = ast.literal_eval(literal)
        except (SyntaxError, ValueError):
            return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", "surrogateescape")
    if not isinstance(value, str) or "\x00" in value:
        return None
    return value


def _strings(arguments: str) -> list[tuple[str | None, str | None]]:
    """Each quoted path argument with the directory annotation before it."""

    found: list[tuple[str | None, str | None]] = []
    previous = 0
    for match in _QUOTED.finditer(arguments):
        between = arguments[previous:match.start()]
        annotations = _FD_ANNOTATION.findall(between)
        base = annotations[-1] if annotations else None
        if match.group(1):  # strace shortened the string: the path is unknown
            found.append((None, base))
        else:
            found.append((_decode(match.group(0)), base))
        previous = match.end()
    return found


class TraceReducer:
    """Fold strace lines into per-path states."""

    def __init__(self, initial_cwd: Path) -> None:
        self.cwd: dict[str, str] = {}
        self.root_pid: str | None = None
        # Traces always carry POSIX paths, whatever the host running the reducer.
        self.initial_cwd = posixpath.normpath(Path(initial_cwd).as_posix())
        self.pending: dict[str, str] = {}
        self.waiting: dict[str, list[tuple[str, str, str]]] = {}
        self.orphans: dict[str, list[str]] = {}
        self.paths: dict[str, PathState] = {}
        self.volatile: list[str] = []
        self.unresolved = 0
        self.unresolved_examples: list[str] = []
        self.handoffs: set[str] = set()
        self._line = ""
        self.processes = 0
        self.root_exit: int | None = None
        self.root_exited = threading.Event()
        self.local_sockets: set[str] = set()

    # -- line handling -----------------------------------------------------
    def unplaced(self, text: str | None = None) -> None:
        """Count a line the reducer cannot account for, keeping a few."""
        self.unresolved += 1
        if len(self.unresolved_examples) < MAX_UNRESOLVED_EXAMPLES:
            self.unresolved_examples.append((self._line if text is None else text)[:300])

    def feed(self, raw_line: str) -> None:
        line = raw_line.rstrip("\n")
        self._line = line
        match = _PID.match(line)
        if match is None:
            if line.strip():
                self.unplaced()
            return
        pid, body = match.group(1), line[match.end():]
        if self.root_pid is None:
            self.root_pid = pid
            self.cwd[pid] = self.initial_cwd
            self.processes = 1
        if body.startswith("+++ "):
            if pid == self.root_pid:
                exited = _EXIT.match(body)
                killed = _KILLED.match(body)
                if exited:
                    self.root_exit = int(exited.group(1))
                    self.root_exited.set()
                elif killed:
                    number = getattr(signal, killed.group(1), None)
                    self.root_exit = 128 + int(number) if number else 1
                    self.root_exited.set()
                # "superseded by execve" is not an exit: the process goes on.
            return
        if body.startswith("--- "):
            return
        handoff = _PID_CHANGED.match(body)
        if handoff:
            leader = handoff.group(2)
            if leader in self.pending:
                self.unplaced(f"{leader} {self.pending[leader]} [thread replaced by execve]")
            self.pending[leader] = handoff.group(1)
            self.handoffs.add(leader)
            if pid in self.cwd:
                self.cwd.setdefault(leader, self.cwd[pid])
            return
        if body.endswith(_UNFINISHED):
            self.pending[pid] = body[: -len(_UNFINISHED)]
            return
        resumed = _RESUMED.match(body)
        if resumed:
            head = self.pending.pop(pid, None)
            if head is None:
                self.unplaced()
                return
            body = head + resumed.group(2)
            if pid in self.handoffs:
                self.handoffs.discard(pid)
                # strace cannot read the result across the pid change; the
                # kernel changes the pid only after the point of no return.
                body = _HANDOFF_RESULT.sub("= 0", body)
        if _DYING.match(body):
            return
        call = _CALL.match(body)
        if call is None:
            self.unplaced()
            return
        name, arguments, result = call.groups()
        if pid not in self.cwd and self._needs_cwd(name, arguments):
            self.waiting.setdefault(pid, []).append((name, arguments, result))
            return
        self._event(pid, name, arguments, result)

    def _needs_cwd(self, name: str, arguments: str) -> bool:
        if name in _IGNORED or name in _CHILD:
            return False
        for text, base in _strings(arguments):
            if text is not None and not text.startswith("/") and base is None:
                return True
        return name == "chdir"

    def _adopt(self, pid: str, cwd: str) -> None:
        if pid in self.cwd:
            return
        self.cwd[pid] = cwd
        # Children cloned before this process's cwd was known inherit the
        # cwd it had then, which is this one unless a waiting chdir moves it.
        children = self.orphans.pop(pid, [])
        for name, arguments, result in self.waiting.pop(pid, []):
            self._event(pid, name, arguments, result)
        for child in children:
            self._adopt(child, cwd)

    def finish(self) -> None:
        for pid, waiting in self.waiting.items():
            for name, arguments, result in waiting:
                self.unplaced(f"{pid} {name}({arguments}) = {result} [cwd unknown]")
        self.waiting.clear()
        for pid, head in self.pending.items():
            self.unplaced(f"{pid} {head} [never resumed]")
        self.pending.clear()

    # -- path helpers ------------------------------------------------------
    def _resolve(self, pid: str, text: str | None, base: str | None) -> str | None:
        if text is None or text == "":
            return None
        if text.startswith("/"):
            return posixpath.normpath(text)
        origin = base if base and base.startswith("/") else self.cwd.get(pid)
        if origin is None:
            return None
        return posixpath.normpath(posixpath.join(origin, text))

    def _learn_cwd(self, pid: str, arguments: str) -> None:
        if pid in self.cwd:
            return
        match = re.search(r"AT_FDCWD<([^<>]*)>", arguments)
        if match and match.group(1).startswith("/"):
            self._adopt(pid, posixpath.normpath(match.group(1)))

    def _mark(self, path: str | None, kind: str, operation: str = "") -> None:
        if path is None:
            self.unplaced()
            return
        state = self.paths.get(path)
        if state is None:
            state = PathState(first=kind)
            self.paths[path] = state
        elif kind in {"produced", "touched", "deleted"} and state.first in {"input"}:
            state.modified_after_input = True
        elif kind in {"produced", "deleted"} and state.first == "opened":
            # Replaced or removed: its birth time no longer tells whether the
            # command created it.
            state.modified_after_input = True
        if operation:
            state.operations.add(operation)

    # -- syscall semantics --------------------------------------------------
    def _event(self, pid: str, name: str, arguments: str, result: str) -> None:
        self._learn_cwd(pid, arguments)
        returned_match = _RESULT_INT.match(result)
        returned = int(returned_match.group(1)) if returned_match else None
        failed = returned is not None and returned < 0
        missing = result.startswith("-1 ENOENT") or result.startswith("-1 ENOTDIR")

        if name in _CHILD:
            if returned is not None and returned > 0:
                child = str(returned)
                self.processes += 1
                parent_cwd = self.cwd.get(pid)
                if parent_cwd is not None:
                    self._adopt(child, parent_cwd)
                else:
                    self.orphans.setdefault(pid, []).append(child)
            return
        if name in _IGNORED:
            return
        if name == "chdir":
            (text, base), = _strings(arguments)[:1] or [(None, None)]
            if returned == 0:
                target = self._resolve(pid, text, base)
                if target is None:
                    self.unplaced()
                else:
                    self._mark(target, "input", "metadata")
                    self.cwd[pid] = target
            return
        if name == "fchdir":
            annotation = _FD_ANNOTATION.search(arguments)
            if returned == 0:
                if annotation and annotation.group(1).startswith("/"):
                    self.cwd[pid] = posixpath.normpath(annotation.group(1))
                else:
                    self.unplaced()
            return
        if name in _LIST:
            annotation = _FD_ANNOTATION.search(arguments)
            if returned is not None and returned >= 0:
                if annotation and annotation.group(1).startswith("/"):
                    self._mark(posixpath.normpath(annotation.group(1)), "input", "enumerate")
                else:
                    self.unplaced()
            return
        if name == "connect":
            self._connect(arguments, failed)
            return
        if name == "ptrace":
            # A debugger or tracer inside the command cannot attach while the
            # command itself is traced, so the run may not show its behaviour.
            if "nested-ptrace" not in self.volatile:
                self.volatile.append("nested-ptrace")
            return

        strings = _strings(arguments)
        if name in _EXEC:
            if name == "execveat" or not strings:
                self.unplaced()
                return
            text, base = strings[0]
            path = self._resolve(pid, text, base)
            if missing:
                self._mark(path, "missing", "execute")
            elif returned == 0:
                self._mark(path, "input", "execute")
            elif failed:
                self._mark(path, "input", "metadata")
            return
        if name in _OPEN:
            if not strings:
                # openat on an empty path with a descriptor base is fd-relative.
                if name == "openat" and "AT_EMPTY_PATH" in arguments:
                    return
                self.unplaced()
                return
            text, base = strings[0]
            path = self._resolve(pid, text, base)
            writes = name == "creat" or any(flag in arguments for flag in _WRITE_FLAGS)
            creates = name == "creat" or "O_CREAT" in arguments
            truncates = name == "creat" or "O_TRUNC" in arguments
            # O_CREAT|O_EXCL succeeds only on a path that did not exist: a lock
            # or a fresh file, never a read of something already there.
            exclusive = creates and "O_EXCL" in arguments
            reads = name != "creat" and ("O_RDWR" in arguments or "O_WRONLY" not in arguments)
            directory = "O_DIRECTORY" in arguments or "O_PATH" in arguments
            if missing:
                self._mark(path, "missing", "read")
                return
            if failed:
                self._mark(path, "input", "metadata")
                return
            resolved = _RESULT_PATH.match(result)
            target = posixpath.normpath(resolved.group(1)) if resolved and resolved.group(1).startswith("/") else path
            if target != path and path is not None:
                self._mark(path, "input", "metadata")
            if not writes:
                self._mark(target, "input", "metadata" if directory else "read")
            elif creates and (truncates or exclusive or not reads):
                self._mark(target, "produced")
            elif reads and creates and target is not None and target not in self.paths:
                # Opened for update, created when absent (logs, databases):
                # whether it existed is settled after the run by birth time.
                self._mark(target, "opened")
            elif reads:
                self._mark(target, "input", "read")
                state = self.paths.get(target or "")
                if state is not None:
                    state.modified_after_input = True
            else:
                self._mark(target, "touched")
            return
        if name in _METADATA:
            if not strings:
                if "AT_EMPTY_PATH" in arguments:
                    return
                self.unplaced()
                return
            text, base = strings[0]
            if text == "" and "AT_EMPTY_PATH" in arguments:
                return
            path = self._resolve(pid, text, base)
            self._mark(path, "missing" if missing else "input", "metadata")
            return
        if name in _CREATE:
            if not strings:
                self.unplaced()
                return
            text, base = strings[0]
            if returned == 0:
                self._mark(self._resolve(pid, text, base), "produced")
            elif result.startswith("-1 EEXIST"):
                self._mark(self._resolve(pid, text, base), "input", "metadata")
            elif missing:
                self._mark(self._resolve(pid, text, base), "missing", "metadata")
            return
        if name in _DELETE:
            if not strings:
                self.unplaced()
                return
            text, base = strings[0]
            path = self._resolve(pid, text, base)
            if returned == 0:
                self._mark(path, "deleted")
            elif missing:
                self._mark(path, "missing", "metadata")
            return
        if name in _RENAME or name in _LINK:
            if len(strings) < 2:
                self.unplaced()
                return
            (old_text, old_base), (new_text, new_base) = strings[0], strings[1]
            old = self._resolve(pid, old_text, old_base)
            new = self._resolve(pid, new_text, new_base)
            if returned == 0:
                if name in _RENAME:
                    self._mark(old, "deleted")
                else:
                    self._mark(old, "input", "metadata")
                self._mark(new, "produced")
            elif missing:
                self._mark(old, "missing", "metadata")
            return
        if name in _SYMLINK:
            if len(strings) < 2:
                self.unplaced()
                return
            new_text, new_base = strings[1]
            if returned == 0:
                self._mark(self._resolve(pid, new_text, new_base), "produced")
            return
        if name in _MODIFY:
            if not strings:
                return  # descriptor-based (e.g. futimens through utimensat)
            text, base = strings[0]
            if text == "" or text is None and "AT_EMPTY_PATH" in arguments:
                return
            path = self._resolve(pid, text, base)
            if returned == 0:
                self._mark(path, "touched")
            elif missing:
                self._mark(path, "missing", "metadata")
            return
        # A traced call this reducer does not model: fail closed.
        self.unplaced()

    def _connect(self, arguments: str, failed: bool) -> None:
        inet = _INET.search(arguments)
        if inet:
            address = inet.group(1) or inet.group(2) or ""
            port = _PORT.search(arguments)
            loopback = address.startswith("127.") or address in {"::1", "0.0.0.0", "::"}
            if not loopback or (port and port.group(1) == "53"):
                reason = f"network:{address}"
                if reason not in self.volatile:
                    self.volatile.append(reason)
            return
        unix = _UNIX.search(arguments)
        if unix:
            if unix.group(1):
                reason = "socket:abstract"
            else:
                strings = _strings(arguments[unix.start():])
                path = strings[0][0] if strings else None
                if path is not None and self.paths.get(posixpath.normpath(path), PathState("")).first == "produced":
                    return
                reason = f"socket:{path}"
            if not failed and reason not in self.volatile:
                self.volatile.append(reason)


def strace_available() -> str | None:
    executable = shutil.which("strace")
    if executable is None:
        return None
    try:
        probe = subprocess.run(
            [executable, "-f", "-qq", "-e", "trace=none", "-o", os.devnull, "--", "true"],
            capture_output=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return executable if probe.returncode == 0 else None


def _seccomp_supported(executable: str) -> bool:
    try:
        probe = subprocess.run(
            [executable, "--seccomp-bpf", "-f", "-qq", "-e", TRACE_EXPRESSION,
             "-o", os.devnull, "--", "true"],
            capture_output=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def observe(
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
    strace: str | None = None,
    seccomp: bool | None = None,
) -> Observation:
    """Run ``argv`` once under strace and reduce what it touched."""

    executable = strace or strace_available()
    if executable is None:
        raise RuntimeError("strace is not available")
    use_seccomp = _seccomp_supported(executable) if seccomp is None else seccomp
    directory = Path(tempfile.mkdtemp(prefix="click-ci-trace-"))
    fifo = directory / "trace"
    os.mkfifo(fifo, 0o600)
    reducer = TraceReducer(cwd)
    reader_done = threading.Event()
    reader_error: list[BaseException] = []

    def read() -> None:
        try:
            with open(fifo, "r", encoding="utf-8", errors="surrogateescape") as handle:
                for line in handle:
                    reducer.feed(line)
        except BaseException as exc:  # pragma: no cover - surfaced below
            reader_error.append(exc)
        finally:
            reader_done.set()

    command = [executable, "-f", "-qq", "-y", "-s", str(STRING_LIMIT),
               "-e", TRACE_EXPRESSION, "-o", str(fifo)]
    if use_seccomp:
        command.insert(1, "--seccomp-bpf")
    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    started = time.monotonic()
    started_at = time.time()
    process = subprocess.Popen([*command, "--", *argv], cwd=str(cwd), env=environment,
                               start_new_session=True)
    lingering = False
    try:
        while True:
            try:
                returncode = process.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if reducer.root_exited.is_set():
                    try:
                        returncode = process.wait(timeout=LINGER_SECONDS)
                        break
                    except subprocess.TimeoutExpired:
                        lingering = True
                        os.killpg(process.pid, signal.SIGKILL)
                        returncode = process.wait()
                        break
    except KeyboardInterrupt:
        os.killpg(process.pid, signal.SIGINT)
        process.wait()
        raise
    finally:
        if not reader_done.wait(timeout=2):
            # strace never opened the FIFO (or is flushing): unblock the reader.
            try:
                descriptor = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                os.close(descriptor)
            except OSError:
                pass
        if not reader_done.wait(timeout=30):
            reducer.unplaced("trace reader did not finish")
        thread.join(timeout=1)
        shutil.rmtree(directory, ignore_errors=True)
    if reader_error:
        raise RuntimeError(f"trace reader failed: {reader_error[0]}")
    reducer.finish()
    settle_opened(reducer.paths, started_at=started_at, finished_at=time.time())
    exit_code = reducer.root_exit if reducer.root_exit is not None else returncode
    reasons = list(reducer.volatile)
    if lingering:
        reasons.append("processes-outlived-command")
    if reducer.root_pid is None:
        reasons.append("empty-trace")
    return Observation(
        exit_code=exit_code,
        paths=reducer.paths,
        volatile_reasons=reasons,
        unresolved_lines=reducer.unresolved,
        unresolved_examples=list(reducer.unresolved_examples),
        process_count=reducer.processes,
        duration_seconds=time.monotonic() - started,
    )


_STATX_BTIME = 0x800
_AT_FDCWD = -100
_AT_SYMLINK_NOFOLLOW = 0x100
_STATX_SIZE = 256
_STATX_BTIME_OFFSET = 80  # struct statx: stx_btime follows stx_atime


def _statx():
    try:
        function = ctypes.CDLL(None, use_errno=True).statx
    except (OSError, AttributeError, TypeError):  # no libc statx (Windows, macOS)
        return None
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
    function.restype = ctypes.c_int
    return function


def birth_time(path: str) -> float | None:
    """When the file was created, or None when the filesystem does not say."""
    statx = _statx()
    if statx is None:
        return None
    buffer = ctypes.create_string_buffer(_STATX_SIZE)
    if statx(_AT_FDCWD, os.fsencode(path), _AT_SYMLINK_NOFOLLOW, _STATX_BTIME, buffer) != 0:
        return None
    (mask,) = struct.unpack_from("=I", buffer, 0)
    if not mask & _STATX_BTIME:
        return None
    seconds, nanoseconds = struct.unpack_from("=qI", buffer, _STATX_BTIME_OFFSET)
    return seconds + nanoseconds / 1e9


def settle_opened(paths: dict[str, PathState], *, started_at: float, finished_at: float) -> None:
    """Decide files opened for update with O_CREAT: product or changed input.

    A file born during the run is the command's product. Anything else,
    including an unknown birth time or one outside the run (a filesystem with
    another clock), is treated as an input the command changed: fail closed.
    """
    for path, state in paths.items():
        if state.first != "opened":
            continue
        born = None if state.modified_after_input else birth_time(path)
        if born is not None and started_at <= born <= finished_at + 1.0:
            state.first = "produced"
        else:
            state.first = "input"
            state.modified_after_input = True


def reduce_lines(lines: Iterable[str], *, cwd: Path) -> TraceReducer:
    """Test helper: fold prepared strace lines without running anything."""

    reducer = TraceReducer(cwd)
    for line in lines:
        reducer.feed(line)
    reducer.finish()
    return reducer
