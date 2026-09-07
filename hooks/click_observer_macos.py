#!/usr/bin/env python3
"""Native macOS ``fs_usage`` backend for Shadow Observer v1.

The backend never raises privilege.  When the current process is already
privileged, it creates the approved target in macOS's native suspended state,
attaches a PID-filtered system collector, and then resumes that exact process.
Raw output is bounded in memory and discarded after it is normalized into the
existing non-authoritative Observer v1 record.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path, PurePosixPath


from collections.abc import Callable, Mapping, Sequence  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from functools import lru_cache  # noqa: E402
import hashlib  # noqa: E402
import platform  # noqa: E402
import posixpath  # noqa: E402
import re  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from typing import Any, BinaryIO  # noqa: E402

if __package__:
    from . import click_dependency_cache, click_observer_common, click_process
else:  # Executed directly from the bundled hooks directory.
    import click_dependency_cache
    import click_observer_common
    import click_process


MAX_RAW_TRACE_BYTES = 4 * 1024 * 1024
MAX_TRANSIENT_INPUTS = click_dependency_cache.MAX_SHADOW_OBSERVER_INPUTS
# fs_usage publishes no readiness signal. Its startup performs disk-name cache
# discovery and ktrace callback registration before ktrace_start(), so keep the
# suspended target stopped long enough for that bounded initialization.
COLLECTOR_STARTUP_SECONDS = 1.0
NATIVE_FS_USAGE_PATHS = frozenset({"/usr/bin/fs_usage", "/usr/sbin/fs_usage"})
MACOS_DATA_VOLUME_PREFIX = "/System/Volumes/Data"
MACOS_PRIVATE_ALIASES = ("/etc", "/tmp", "/var")
POSIX_SPAWN_SETPGROUP = 0x0002
POSIX_SPAWN_START_SUSPENDED = 0x0080
POSIX_SPAWN_CLOEXEC_DEFAULT = 0x4000

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_PROCESS_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9._+-]{0,63}$")
_EVENT = re.compile(
    r"^\s*\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+"
    r"(?P<operation>\S+)\s+(?P<details>.*?)\s+"
    r"\d+\.\d+(?:\s+[A-Z]+)?\s+\S+\s*$"
)
_ABSOLUTE_PATH = re.compile(
    r"(?<!\S)((?:/|[A-Za-z]:/)(?:[^\x00\r\n])*?)\s*$"
)
_DIRFD_ABSOLUTE_PATH = re.compile(
    r"(?:^|\s)\[\s*-?\d+\s*\]/"
    r"(?P<path>(?:/|[A-Za-z]:/)[^\x00\r\n]*?)\s*$"
)
_AT_FDCWD_RELATIVE_PATH = re.compile(
    r"(?:^|\s)\[\s*-2\s*\]/(?P<path>[^/\x00\r\n][^\x00\r\n]*?)\s*$"
)
_POSIX_DRIVE_PATH = re.compile(r"^[A-Za-z]:/")
_OPEN_FLAGS = re.compile(r"\((?P<flags>[A-Z_]{2,32})\)")
_MISSING_ERRNO = re.compile(r"\[\s*2\s*\]")

_READ_OPERATIONS = frozenset(
    {
        "open",
        "openat",
        "pread",
        "pread_nocancel",
        "read",
        "read_nocancel",
        "readv",
    }
)
_METADATA_OPERATIONS = frozenset(
    {
        "access",
        "access_extended",
        "fstat",
        "getattrlist",
        "lstat",
        "lstat64",
        "readlink",
        "stat",
        "stat64",
    }
)
_DIRECTORY_OPERATIONS = frozenset(
    {"getdirentries", "getdirentries64", "readdir"}
)
_EXEC_OPERATIONS = frozenset({"exec", "execve"})
_CHILD_OPERATIONS = frozenset({"fork", "posix_spawn", "posix_spawnp", "vfork"})
_MISSING_MARKERS = ("ENOENT", "Err#2", "No such file")

FallbackExecutor = click_observer_common.FallbackExecutor
BackendResolver = Callable[..., tuple[str | None, str]]
FileDigester = Callable[[Path], str]
NativeBackendProbe = Callable[[str], bool]
SpawnArgv = Callable[..., subprocess.Popen[Any]]
SpawnSuspended = Callable[..., Any]
ResumeTarget = Callable[[Any], bool]
DiscardTarget = Callable[[Any], int]
TerminateGroup = Callable[[Any], int]


@dataclass(frozen=True, slots=True)
class ParsedTrace:
    inputs: tuple[dict[str, Any], ...]
    external_input_count: int
    unresolved_event_count: int
    child_process_count: int
    process_tree_complete: bool
    root_exec_observed: bool
    # Kept only in memory for the authoritative adapter. Shadow records still
    # persist repository-relative paths and an external count, never host paths.
    absolute_inputs: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class CollectedExecution:
    exit_code: int
    raw: bytes
    truncated: bool
    failed: bool
    target_started: bool
    command_duration_ms: int
    collector_overhead_ms: int
    process_scope_complete: bool = True


@dataclass(slots=True)
class _SuspendedProcess:
    """Small Popen-compatible owner for a suspended posix_spawn child."""

    pid: int
    command_name: str
    returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        try:
            waited, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            self.returncode = 127
            return self.returncode
        if waited == 0:
            return None
        self.returncode = os.waitstatus_to_exitcode(status)
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is not None:
            return self.returncode
        if timeout is None:
            try:
                _, status = os.waitpid(self.pid, 0)
            except ChildProcessError:
                self.returncode = 127
            else:
                self.returncode = os.waitstatus_to_exitcode(status)
            return int(self.returncode)
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() <= deadline:
            result = self.poll()
            if result is not None:
                return result
            time.sleep(0.01)
        raise subprocess.TimeoutExpired(str(self.pid), timeout)


@dataclass(slots=True)
class _BoundedCapture:
    limit: int
    retained: bytearray = field(default_factory=bytearray)
    truncated: bool = False
    failed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def append(self, chunk: bytes) -> None:
        with self.lock:
            remaining = max(0, self.limit - len(self.retained))
            if remaining:
                self.retained.extend(chunk[:remaining])
            if len(chunk) > remaining:
                self.truncated = True

    def bytes(self) -> bytes:
        with self.lock:
            return bytes(self.retained)


ShadowExecution = click_observer_common.ShadowExecution
Collector = Callable[..., CollectedExecution]


def has_privilege() -> bool:
    """Return whether the process already has the privilege fs_usage needs."""

    try:
        getuid = getattr(os, "geteuid")
        return bool(getuid() == 0)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


@lru_cache(maxsize=4)
def probe_macos_version() -> str:
    """Use the bounded host version as the native collector version."""

    try:
        version = platform.mac_ver()[0]
    except Exception:
        return ""
    return version if isinstance(version, str) and _VERSION.fullmatch(version) else ""


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError:
        return ""
    return hasher.hexdigest()


def native_fs_usage(executable: str) -> bool:
    try:
        return str(Path(executable).resolve(strict=True)) in NATIVE_FS_USAGE_PATHS
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


_native_fs_usage = native_fs_usage


def _bounded_add(left: int, right: int) -> int:
    return click_observer_common.bounded_add(left, right)


def _encoded_c_string(value: str) -> bytes:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("invalid native process string")
    return os.fsencode(value)


def _macos_process_name(pid: int) -> str:
    """Read the kernel-owned short process name without invoking a shell."""

    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    libproc.proc_name.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    libproc.proc_name.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(1_024)
    length = int(libproc.proc_name(int(pid), buffer, len(buffer)))
    if length <= 0:
        error = ctypes.get_errno()
        raise OSError(error or 1, os.strerror(error or 1))
    try:
        name = os.fsdecode(buffer.value)
    except (TypeError, UnicodeError):
        name = ""
    if _PROCESS_NAME.fullmatch(name) is None:
        raise ValueError("unsupported native process name")
    return name


def _spawn_suspended_macos(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> _SuspendedProcess:
    """Spawn the actual target suspended, in its own process group."""

    if not argv:
        raise ValueError("empty argv")
    encoded_argv = [_encoded_c_string(value) for value in argv]
    encoded_environment = [
        _encoded_c_string(f"{key}={value}")
        for key, value in sorted(env.items())
        if isinstance(key, str)
        and isinstance(value, str)
        and key
        and "=" not in key
    ]
    if len(encoded_environment) != len(env):
        raise ValueError("invalid native process environment")
    argv_array = (ctypes.c_char_p * (len(encoded_argv) + 1))(
        *encoded_argv, None
    )
    environment_array = (ctypes.c_char_p * (len(encoded_environment) + 1))(
        *encoded_environment, None
    )

    libc = ctypes.CDLL(None, use_errno=True)
    attributes = ctypes.c_void_p()
    actions = ctypes.c_void_p()
    attributes_ready = False
    actions_ready = False
    child_pid = ctypes.c_int()

    libc.posix_spawnattr_init.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    libc.posix_spawnattr_init.restype = ctypes.c_int
    libc.posix_spawnattr_destroy.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    libc.posix_spawnattr_destroy.restype = ctypes.c_int
    libc.posix_spawnattr_setpgroup.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_int,
    ]
    libc.posix_spawnattr_setpgroup.restype = ctypes.c_int
    libc.posix_spawnattr_setflags.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_short,
    ]
    libc.posix_spawnattr_setflags.restype = ctypes.c_int
    libc.posix_spawn_file_actions_init.argtypes = [
        ctypes.POINTER(ctypes.c_void_p)
    ]
    libc.posix_spawn_file_actions_init.restype = ctypes.c_int
    libc.posix_spawn_file_actions_destroy.argtypes = [
        ctypes.POINTER(ctypes.c_void_p)
    ]
    libc.posix_spawn_file_actions_destroy.restype = ctypes.c_int
    add_chdir = getattr(libc, "posix_spawn_file_actions_addchdir_np")
    add_chdir.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
    add_chdir.restype = ctypes.c_int
    add_inherit = getattr(libc, "posix_spawn_file_actions_addinherit_np")
    add_inherit.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
    add_inherit.restype = ctypes.c_int
    libc.posix_spawnp.argtypes = [
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.POINTER(ctypes.c_char_p),
    ]
    libc.posix_spawnp.restype = ctypes.c_int

    try:
        error = int(libc.posix_spawnattr_init(ctypes.byref(attributes)))
        if error:
            raise OSError(error, os.strerror(error))
        attributes_ready = True
        error = int(libc.posix_spawnattr_setpgroup(ctypes.byref(attributes), 0))
        if error:
            raise OSError(error, os.strerror(error))
        flags = (
            POSIX_SPAWN_SETPGROUP
            | POSIX_SPAWN_START_SUSPENDED
            | POSIX_SPAWN_CLOEXEC_DEFAULT
        )
        error = int(
            libc.posix_spawnattr_setflags(ctypes.byref(attributes), flags)
        )
        if error:
            raise OSError(error, os.strerror(error))
        error = int(libc.posix_spawn_file_actions_init(ctypes.byref(actions)))
        if error:
            raise OSError(error, os.strerror(error))
        actions_ready = True
        error = int(
            add_chdir(
                ctypes.byref(actions), _encoded_c_string(os.fspath(cwd))
            )
        )
        if error:
            raise OSError(error, os.strerror(error))
        for descriptor in (0, 1, 2):
            error = int(add_inherit(ctypes.byref(actions), descriptor))
            if error:
                raise OSError(error, os.strerror(error))
        error = int(
            libc.posix_spawnp(
                ctypes.byref(child_pid),
                encoded_argv[0],
                ctypes.byref(actions),
                ctypes.byref(attributes),
                argv_array,
                environment_array,
            )
        )
        if error:
            raise OSError(error, os.strerror(error), argv[0])
        if child_pid.value <= 0:
            raise OSError("posix_spawnp returned an invalid pid")
        target = _SuspendedProcess(pid=int(child_pid.value), command_name="")
        try:
            target.command_name = _macos_process_name(target.pid)
        except Exception:
            _discard_suspended_target(target)
            raise
        return target
    finally:
        if actions_ready:
            libc.posix_spawn_file_actions_destroy(ctypes.byref(actions))
        if attributes_ready:
            libc.posix_spawnattr_destroy(ctypes.byref(attributes))


def _resume_suspended_target(target: Any) -> bool:
    if target.poll() is not None:
        return False
    try:
        os.kill(int(target.pid), signal.SIGCONT)
    except (OSError, TypeError, ValueError):
        return False
    return True


def _discard_suspended_target(target: Any) -> int:
    """Kill a child that has never been resumed, without running its code."""

    if target.poll() is not None:
        return int(target.returncode or 0)
    try:
        os.killpg(int(target.pid), signal.SIGKILL)
        return int(target.wait(timeout=1.0))
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return 1


def _candidate_path(details: str) -> str:
    # Wide fs_usage output prefixes openat-family paths with ``[dirfd]/``.
    # If the reported pathname is absolute, that renders as
    # ``[-2]//private/...``. Strip only the numeric prefix; genuinely relative
    # paths stay unresolved instead of being guessed against the workspace.
    match = _DIRFD_ABSOLUTE_PATH.search(details)
    if match is not None:
        value = match.group("path").strip()
    else:
        match = _ABSOLUTE_PATH.search(details)
        if match is None:
            return ""
        value = match.group(1).strip()
    if " -> " in value or "\x00" in value:
        return ""
    return value


def _bound_relative_candidate(operation_name: str, details: str) -> str:
    """Return a cwd-relative open path only when its shape is unambiguous."""

    if operation_name == "openat":
        match = _AT_FDCWD_RELATIVE_PATH.search(details)
        value = match.group("path").strip() if match is not None else ""
    elif operation_name == "open":
        flags = _OPEN_FLAGS.search(details)
        value = details[flags.end() :].strip() if flags is not None else ""
    else:
        return ""
    if (
        not value
        or value.startswith(("/", "\\"))
        or _POSIX_DRIVE_PATH.match(value) is not None
        or " -> " in value
        or "\x00" in value
    ):
        return ""
    return value


def _canonical_macos_path(path_text: str) -> str:
    """Normalize stable logical/physical aliases emitted by macOS ktrace."""

    normalized = posixpath.normpath(path_text)
    if normalized == MACOS_DATA_VOLUME_PREFIX:
        normalized = "/"
    elif normalized.startswith(MACOS_DATA_VOLUME_PREFIX + "/"):
        normalized = normalized[len(MACOS_DATA_VOLUME_PREFIX) :]
    for alias in MACOS_PRIVATE_ALIASES:
        if normalized == alias or normalized.startswith(alias + "/"):
            normalized = "/private" + normalized
            break
    return normalized


def parse_fs_usage(
    raw: bytes,
    *,
    workspace: Path,
    truncated: bool = False,
    root_execution_bound: bool = False,
    process_scope_complete: bool = True,
) -> ParsedTrace:
    """Parse bounded fs_usage text into content-free repository inputs."""

    try:
        root_text = workspace.resolve(strict=True).as_posix()
    except (OSError, RuntimeError):
        root_text = Path(os.path.abspath(workspace)).as_posix()
    root_text = _canonical_macos_path(root_text)
    root = PurePosixPath(root_text)
    inputs: dict[str, dict[str, Any]] = {}
    absolute_inputs: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    absolute_conflicts: set[str] = set()
    external_digests: set[str] = set()
    unresolved = 1 if truncated else 0
    child_processes = 0
    root_exec_observed = bool(root_execution_bound)

    def add_path(path_text: str, *, kind: str, operation: str) -> None:
        nonlocal unresolved
        try:
            normalized = _canonical_macos_path(path_text)
            if not normalized.startswith("/") and _POSIX_DRIVE_PATH.match(
                normalized
            ) is None:
                raise ValueError("not absolute")
            candidate = PurePosixPath(normalized)
        except (OSError, TypeError, ValueError):
            unresolved = _bounded_add(unresolved, 1)
            return
        absolute_key = candidate.as_posix()
        absolute = absolute_inputs.get(absolute_key)
        if absolute is not None and absolute["kind"] != kind:
            absolute_conflicts.add(absolute_key)
        elif absolute is None:
            if len(absolute_inputs) >= MAX_TRANSIENT_INPUTS:
                unresolved = _bounded_add(unresolved, 1)
            else:
                absolute = {
                    "path": absolute_key,
                    "kind": kind,
                    "operations": [],
                }
                absolute_inputs[absolute_key] = absolute
        if absolute is not None and operation not in absolute["operations"]:
            absolute["operations"].append(operation)
        try:
            relative = candidate.relative_to(root).as_posix()
        except ValueError:
            try:
                digest = hashlib.sha256(os.fsencode(normalized)).hexdigest()
            except (OSError, TypeError, UnicodeError, ValueError):
                unresolved = _bounded_add(unresolved, 1)
                return
            if digest not in external_digests:
                if (
                    len(external_digests)
                    >= click_dependency_cache.MAX_SHADOW_OBSERVER_INPUTS
                ):
                    unresolved = _bounded_add(unresolved, 1)
                else:
                    external_digests.add(digest)
            return
        if not relative or relative == ".":
            unresolved = _bounded_add(unresolved, 1)
            return
        if kind == "directory" and not relative.endswith("/"):
            relative += "/"
        try:
            encoded_relative = relative.encode("utf-8")
        except UnicodeEncodeError:
            unresolved = _bounded_add(unresolved, 1)
            return
        if (
            len(encoded_relative)
            > click_dependency_cache.MAX_SHADOW_OBSERVER_PATH_BYTES
            or "\\" in relative
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in relative
            )
        ):
            unresolved = _bounded_add(unresolved, 1)
            return
        existing = inputs.get(relative)
        if existing is not None and existing["kind"] != kind:
            conflicts.add(relative)
            return
        if existing is None:
            if len(inputs) >= click_dependency_cache.MAX_SHADOW_OBSERVER_INPUTS:
                unresolved = _bounded_add(unresolved, 1)
                return
            existing = {"path": relative, "kind": kind, "operations": []}
            inputs[relative] = existing
        if operation not in existing["operations"]:
            existing["operations"].append(operation)

    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        text = raw.decode("utf-8", errors="replace")
        unresolved = _bounded_add(unresolved, 1)

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("TIMESTAMP", "fs_usage:")):
            continue
        match = _EVENT.match(line)
        if match is None:
            if re.match(r"^\s*\d{2}:\d{2}:\d{2}", line):
                unresolved = _bounded_add(unresolved, 1)
            continue
        operation_name = match.group("operation").rstrip("*").lower()
        details = match.group("details")
        if operation_name in _CHILD_OPERATIONS:
            child_processes = _bounded_add(child_processes, 1)
        if operation_name in _EXEC_OPERATIONS:
            root_exec_observed = True

        observed_operation = ""
        kind = "file"
        ignored_operation = False
        if any(marker in details for marker in _MISSING_MARKERS) or (
            _MISSING_ERRNO.search(details) is not None
        ):
            observed_operation = (
                "read" if operation_name in _READ_OPERATIONS else "metadata"
            )
            kind = "missing"
        elif operation_name in _DIRECTORY_OPERATIONS:
            observed_operation = "enumerate"
            kind = "directory"
        elif operation_name in _EXEC_OPERATIONS or operation_name in _CHILD_OPERATIONS:
            observed_operation = "execute"
        elif operation_name in _METADATA_OPERATIONS:
            observed_operation = "metadata"
        elif operation_name in _READ_OPERATIONS:
            flags = _OPEN_FLAGS.search(details)
            if (
                operation_name in {"open", "openat"}
                and flags is not None
                and "R" not in flags.group("flags")
            ):
                ignored_operation = True
            else:
                observed_operation = "read"

        path_text = _candidate_path(details)
        projected_relative_path = False
        if not path_text and root_execution_bound:
            relative_path = _bound_relative_candidate(operation_name, details)
            if relative_path:
                path_text = posixpath.join(root_text, relative_path)
                projected_relative_path = True
        if observed_operation and path_text:
            add_path(path_text, kind=kind, operation=observed_operation)
            if projected_relative_path:
                # fs_usage may report the first VFS lookup as relative.  The
                # suspended launch binds the initial cwd, so retain the useful
                # repository candidate, but keep the observation partial: the
                # target may chdir and the process-name fallback can include
                # unrelated same-name processes.
                unresolved = _bounded_add(unresolved, 1)
        elif observed_operation:
            unresolved = _bounded_add(unresolved, 1)
        elif path_text and operation_name not in {
            "close",
            "fsync",
            "pwrite",
            "write",
            "write_nocancel",
        } and not ignored_operation:
            unresolved = _bounded_add(unresolved, 1)

    for relative in conflicts:
        inputs.pop(relative, None)
        unresolved = _bounded_add(unresolved, 1)
    for absolute in absolute_conflicts:
        absolute_inputs.pop(absolute, None)
        unresolved = _bounded_add(unresolved, 1)
    if not root_exec_observed:
        unresolved = _bounded_add(unresolved, 1)
    normalized_inputs = tuple(
        {
            "path": item["path"],
            "kind": item["kind"],
            "operations": sorted(item["operations"]),
        }
        for _, item in sorted(inputs.items())
    )
    process_tree_complete = bool(
        root_exec_observed
        and child_processes == 0
        and unresolved == 0
        and not truncated
        and process_scope_complete
    )
    return ParsedTrace(
        inputs=normalized_inputs,
        external_input_count=len(external_digests),
        unresolved_event_count=unresolved,
        child_process_count=child_processes,
        process_tree_complete=process_tree_complete,
        root_exec_observed=root_exec_observed,
        absolute_inputs=tuple(
            {
                "path": item["path"],
                "kind": item["kind"],
                "operations": sorted(item["operations"]),
            }
            for _, item in sorted(absolute_inputs.items())
        ),
    )


def _drain_stream(stream: BinaryIO, capture: _BoundedCapture) -> None:
    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            capture.append(bytes(chunk))
    except (OSError, TypeError, ValueError):
        capture.failed = True
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _stop_collector(
    collector: subprocess.Popen[Any],
    *,
    terminate_group: TerminateGroup,
) -> int:
    """Request fs_usage's graceful flush, then use the shared hard fallback."""

    if collector.poll() is not None:
        return int(collector.returncode or 0)
    if os.name != "nt":
        try:
            os.killpg(collector.pid, signal.SIGINT)
            return int(collector.wait(timeout=1.0))
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            pass
    return int(terminate_group(collector))


def collect_command(
    argv: Sequence[str],
    *,
    workspace: Path,
    environment: Mapping[str, str],
    executable: str,
    spawn_argv: SpawnArgv = click_process.spawn_argv,
    spawn_suspended: SpawnSuspended = _spawn_suspended_macos,
    resume_target: ResumeTarget = _resume_suspended_target,
    discard_suspended: DiscardTarget = _discard_suspended_target,
    terminate_group: TerminateGroup = click_process.terminate_process_group,
    capture_limit: int = MAX_RAW_TRACE_BYTES,
    strict_pid_scope: bool = False,
) -> CollectedExecution:
    """Collect one PID-scoped trace while executing the target at most once."""

    started = time.monotonic()
    bounded_limit = (
        capture_limit
        if isinstance(capture_limit, int)
        and not isinstance(capture_limit, bool)
        and capture_limit > 0
        else MAX_RAW_TRACE_BYTES
    )
    capture = _BoundedCapture(limit=bounded_limit)
    target: Any | None = None
    collector: subprocess.Popen[Any] | None = None
    readers: list[threading.Thread] = []
    target_started = False
    exit_code = 127
    failed = False
    collector_preparation_ms = 0
    collector_cleanup_started = 0.0
    collector_cleanup_ms = 0
    try:
        target = spawn_suspended(
            list(argv),
            cwd=workspace,
            env=dict(environment),
        )
        collector_started = time.monotonic()
        filters = [str(target.pid)]
        if not strict_pid_scope:
            filters.append(str(target.command_name))
        collector_environment = dict(environment)
        for key in (
            "DYLD_INSERT_LIBRARIES",
            "DYLD_FORCE_FLAT_NAMESPACE",
            "CLICK_NATIVE_OBSERVER_CHANNEL",
            "CLICK_NATIVE_OBSERVER_ROOT",
        ):
            collector_environment.pop(key, None)
        collector = spawn_argv(
            [
                executable,
                "-w",
                "-f",
                "pathname",
                "-f",
                "exec",
                *filters,
            ],
            cwd=workspace,
            env=collector_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if collector.stdout is None:
            failed = True
        else:
            reader = threading.Thread(
                target=_drain_stream,
                args=(collector.stdout, capture),
                daemon=True,
            )
            reader.start()
            readers.append(reader)
        try:
            collector.wait(timeout=COLLECTOR_STARTUP_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        else:
            failed = True
            exit_code = int(collector.returncode or 1)
        collector_preparation_ms = max(
            0, int((time.monotonic() - collector_started) * 1000)
        )
        if not failed and not resume_target(target):
            failed = True
        if not failed:
            target_started = True
            click_process.target_started()
            exit_code = int(target.wait())
            if collector.poll() is not None:
                failed = True
    except KeyboardInterrupt:
        failed = True
        exit_code = 130
    except (OSError, RuntimeError, subprocess.SubprocessError, TypeError, ValueError):
        failed = True
        if target_started and target is not None:
            try:
                exit_code = int(target.wait())
            except (OSError, subprocess.SubprocessError, TypeError, ValueError):
                exit_code = 127
    finally:
        collector_cleanup_started = time.monotonic()
        if target is not None and target.poll() is None:
            try:
                if target_started:
                    terminate_group(target)
                else:
                    discard_suspended(target)
            except Exception:
                failed = True
        if collector is not None and collector.poll() is None:
            try:
                _stop_collector(collector, terminate_group=terminate_group)
            except Exception:
                failed = True
        for reader in readers:
            reader.join(timeout=1)
            if reader.is_alive():
                capture.failed = True
        collector_cleanup_ms = max(
            0, int((time.monotonic() - collector_cleanup_started) * 1000)
        )
    duration_ms = (
        max(0, int((time.monotonic() - started) * 1000)) if target_started else 0
    )
    return CollectedExecution(
        exit_code=exit_code,
        raw=capture.bytes(),
        truncated=capture.truncated,
        failed=bool(failed or capture.failed),
        target_started=target_started,
        command_duration_ms=duration_ms,
        collector_overhead_ms=min(
            _bounded_add(collector_preparation_ms, collector_cleanup_ms),
            duration_ms,
        ),
        process_scope_complete=bool(
            strict_pid_scope and target_started and not failed and not capture.truncated
        ),
    )


def run_command(
    argv: Sequence[str],
    *,
    workspace: Path,
    observation_root: Path | None = None,
    environment: Mapping[str, str],
    evidence_key: str,
    check_digest: str,
    mutation_revision: int,
    execute_unobserved: FallbackExecutor,
    resolve_backend: BackendResolver,
    digest_file: FileDigester = _file_digest,
    native_backend_probe: NativeBackendProbe = native_fs_usage,
    system_version: Callable[[], str] = probe_macos_version,
    privilege_probe: Callable[[], bool] = has_privilege,
    collector: Collector = collect_command,
    spawn_argv: SpawnArgv = click_process.spawn_argv,
    terminate_group: TerminateGroup = click_process.terminate_process_group,
    system_name: str | None = None,
    capture_limit: int = MAX_RAW_TRACE_BYTES,
) -> ShadowExecution:
    """Execute one target and attach best-effort native macOS telemetry."""

    try:
        system = platform.system() if system_name is None else system_name
        privileged = bool(privilege_probe()) if system == "Darwin" else False
    except Exception:
        system = ""
        privileged = False
    if system != "Darwin" or not privileged:
        return click_observer_common.run_unobserved(
            execute_unobserved,
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
        )

    preparation_started = time.monotonic()
    try:
        executable, resolve_error = resolve_backend("fs_usage", workspace=workspace)
    except Exception:
        executable, resolve_error = None, "backend resolution failed"
    if (
        resolve_error
        or not isinstance(executable, str)
        or not executable
        or not native_backend_probe(executable)
    ):
        preparation_ms = max(0, int((time.monotonic() - preparation_started) * 1000))
        return click_observer_common.fallback_execution(
            execute_unobserved,
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
            status="unavailable",
            preparation_ms=preparation_ms,
        )
    try:
        digest = digest_file(Path(executable))
        version = system_version()
    except Exception:
        digest = ""
        version = ""
    if (
        not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
        or not isinstance(version, str)
        or _VERSION.fullmatch(version) is None
    ):
        preparation_ms = max(0, int((time.monotonic() - preparation_started) * 1000))
        return click_observer_common.fallback_execution(
            execute_unobserved,
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
            status="unavailable",
            preparation_ms=preparation_ms,
        )
    preparation_ms = max(0, int((time.monotonic() - preparation_started) * 1000))

    try:
        collected = collector(
            argv,
            workspace=workspace,
            environment=environment,
            executable=executable,
            spawn_argv=spawn_argv,
            terminate_group=terminate_group,
            capture_limit=capture_limit,
        )
    except Exception:
        collected = CollectedExecution(127, b"", False, True, False, 0, 0)
    if not collected.target_started:
        return click_observer_common.fallback_execution(
            execute_unobserved,
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
            status="failed",
            backend_name="fs_usage",
            backend_version=version,
            backend_digest=digest,
            preparation_ms=_bounded_add(
                preparation_ms, collected.collector_overhead_ms
            ),
        )

    parsing_started = time.monotonic()
    raw_trace = collected.raw
    try:
        parsed = parse_fs_usage(
            raw_trace,
            workspace=observation_root or workspace,
            truncated=collected.truncated or collected.failed,
            root_execution_bound=collected.target_started,
            process_scope_complete=collected.process_scope_complete,
        )
    except Exception:
        parsed = ParsedTrace((), 0, 1, 0, False, False)
        collected = CollectedExecution(
            collected.exit_code,
            b"",
            collected.truncated,
            True,
            True,
            collected.command_duration_ms,
            collected.collector_overhead_ms,
        )
    finally:
        raw_trace = b""
    parsing_ms = max(0, int((time.monotonic() - parsing_started) * 1000))
    observer_overhead_ms = _bounded_add(
        preparation_ms,
        _bounded_add(collected.collector_overhead_ms, parsing_ms),
    )
    identity_started = time.monotonic()
    try:
        final_digest = digest_file(Path(executable))
    except Exception:
        final_digest = ""
    identity_ms = max(0, int((time.monotonic() - identity_started) * 1000))
    observer_overhead_ms = _bounded_add(observer_overhead_ms, identity_ms)
    if final_digest != digest:
        return ShadowExecution(
            collected.exit_code,
            click_observer_common.record(
                evidence_key=evidence_key,
                check_digest=check_digest,
                mutation_revision=mutation_revision,
                backend_name=None,
                status="unavailable",
                process_tree_complete=False,
                command_duration_ms=collected.command_duration_ms,
                observer_overhead_ms=observer_overhead_ms,
            ),
        )

    status = (
        "failed"
        if collected.failed and not parsed.inputs
        else "partial"
        if collected.failed
        or collected.truncated
        or not parsed.process_tree_complete
        else "complete"
    )
    return ShadowExecution(
        collected.exit_code,
        click_observer_common.record(
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
            backend_name="fs_usage",
            backend_version=version,
            backend_digest=digest,
            inputs=parsed.inputs,
            status=status,
            external_input_count=parsed.external_input_count,
            unresolved_event_count=parsed.unresolved_event_count,
            child_process_count=parsed.child_process_count,
            process_tree_complete=(
                parsed.process_tree_complete and status == "complete"
            ),
            command_duration_ms=collected.command_duration_ms,
            observer_overhead_ms=observer_overhead_ms,
        ),
    )
