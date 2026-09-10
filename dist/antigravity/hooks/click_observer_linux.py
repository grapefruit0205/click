#!/usr/bin/env python3
"""Linux strace backend for non-authoritative argv observation.

This backend executes one already-authorized command and returns its exit
status beside bounded Shadow Observer v1 telemetry. Selection and lifecycle
state live outside this operating-system-specific module.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import platform
import re
import select
import subprocess
import tempfile
import threading
import time
from typing import Any

if __package__:
    from . import click_dependency_cache, click_observer_common, click_process
else:  # Executed directly from the bundled hooks directory.
    import click_dependency_cache
    import click_observer_common
    import click_process


MAX_RAW_TRACE_BYTES = 4 * 1024 * 1024
MAX_BACKEND_VERSION_BYTES = 4_096
STRACE_STRING_LIMIT = click_dependency_cache.MAX_SHADOW_OBSERVER_PATH_BYTES
STRACE_TRACE_EXPRESSION = "trace=%file,%process,getdents64"

_PID_BRACKET = re.compile(r"^\[pid\s+(\d+)\]\s+")
_PID_PLAIN = re.compile(r"^(\d+)\s+")
_CALL = re.compile(r"^([A-Za-z0-9_]+)\((.*)\)\s+=\s+(.+)$")
_QUOTED = re.compile(r'"(?:\\.|[^"\\])*"')
_FD_PATH = re.compile(r"(?:-?\d+|AT_FDCWD)<([^<>]+)>")
_VERSION = re.compile(r"\bstrace\s+--\s+version\s+([A-Za-z0-9][A-Za-z0-9._+-]{0,63})")
_RETURN_INTEGER = re.compile(r"^(-?\d+)")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")

_EXEC_CALLS = frozenset({"execve", "execveat"})
_OPEN_CALLS = frozenset({"open", "openat", "openat2"})
_METADATA_CALLS = frozenset(
    {
        "access",
        "faccessat",
        "faccessat2",
        "lstat",
        "newfstatat",
        "readlink",
        "readlinkat",
        "stat",
        "statx",
    }
)
_DIRECTORY_CALLS = frozenset({"getdents64"})
_CHDIR_CALLS = frozenset({"chdir", "fchdir"})
_CHILD_CALLS = frozenset({"clone", "clone3", "fork", "vfork"})
_IGNORED_PROCESS_CALLS = frozenset(
    {
        "exit",
        "exit_group",
        "getpgrp",
        "getpgid",
        "getsid",
        "kill",
        "pidfd_open",
        "pidfd_send_signal",
        "prctl",
        "setpgid",
        "setsid",
        "tgkill",
        "tkill",
        "wait4",
        "waitid",
        "waitpid",
    }
)


FallbackExecutor = click_observer_common.FallbackExecutor
BackendResolver = Callable[..., tuple[str | None, str]]
FileDigester = Callable[[Path], str]
BackendProbe = Callable[[str], str]
SpawnArgv = Callable[..., subprocess.Popen[Any]]


@dataclass(frozen=True, slots=True)
class ParsedTrace:
    inputs: tuple[dict[str, Any], ...]
    external_input_count: int
    unresolved_event_count: int
    child_process_count: int
    process_tree_complete: bool
    root_exec_observed: bool
    # Absolute names exist only in the in-memory collector result.  Shadow
    # records continue to persist repository-relative, content-free inputs.
    absolute_inputs: tuple[dict[str, Any], ...] = ()


ShadowExecution = click_observer_common.ShadowExecution


@dataclass(slots=True)
class _PipeCapture:
    data: bytes = b""
    truncated: bool = False
    failed: bool = False


_bounded_add = click_observer_common.bounded_add


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


@lru_cache(maxsize=8)
def probe_strace_version(executable: str) -> str:
    """Return a bounded strace version, or an empty value on probe failure."""
    try:
        result = click_process.run_argv(
            [executable, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    output = bytes(result.stdout or b"") + bytes(result.stderr or b"")
    if result.returncode != 0 or len(output) > MAX_BACKEND_VERSION_BYTES:
        return ""
    match = _VERSION.search(output.decode("utf-8", errors="replace"))
    if match is None:
        return ""
    try:
        capability = click_process.run_argv(
            [
                executable,
                "-D",
                "-f",
                "-qq",
                "-e",
                "trace=none",
                "-o",
                os.devnull,
                "--",
                executable,
                "--version",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3.0,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return match.group(1) if capability.returncode == 0 else ""


def _already_traced() -> bool:
    try:
        for line in Path("/proc/self/status").read_text(
            encoding="utf-8", errors="strict"
        ).splitlines():
            if line.startswith("TracerPid:"):
                return int(line.split(":", 1)[1].strip()) != 0
    except (OSError, UnicodeError, ValueError):
        return False
    return False


def _read_pipe(
    read_fd: int,
    limit: int,
    capture: _PipeCapture,
    stop: threading.Event,
) -> None:
    retained = bytearray()
    try:
        while True:
            if stop.is_set():
                capture.failed = True
                break
            ready, _, _ = select.select([read_fd], [], [], 0.05)
            if not ready:
                if stop.is_set():
                    capture.failed = True
                    break
                continue
            chunk = os.read(read_fd, 64 * 1024)
            if not chunk:
                break
            remaining = max(0, limit - len(retained))
            if remaining:
                retained.extend(chunk[:remaining])
            if len(chunk) > remaining:
                capture.truncated = True
    except OSError:
        capture.failed = True
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass
    capture.data = bytes(retained)


def _read_fifo(
    path: Path,
    limit: int,
    capture: _PipeCapture,
    stop: threading.Event,
) -> None:
    try:
        read_fd = os.open(path, os.O_RDONLY)
    except OSError:
        capture.failed = True
        return
    _read_pipe(read_fd, limit, capture, stop)


def _wake_fifo(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


def _create_trace_fifo(workspace: Path) -> tuple[Path, Path] | None:
    try:
        workspace_root = workspace.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    for raw_root in ("/tmp", "/var/tmp"):
        directory: Path | None = None
        fifo: Path | None = None
        try:
            temporary_root = Path(raw_root).resolve(strict=True)
            if not temporary_root.is_dir():
                continue
            try:
                temporary_root.relative_to(workspace_root)
            except ValueError:
                pass
            else:
                continue
            directory = Path(
                tempfile.mkdtemp(prefix="click-shadow-", dir=temporary_root)
            )
            try:
                directory.relative_to(workspace_root)
            except ValueError:
                pass
            else:
                directory.rmdir()
                continue
            directory.chmod(0o700)
            fifo = directory / "trace.pipe"
            os.mkfifo(fifo, mode=0o600)
            return directory, fifo
        except OSError:
            _remove_trace_fifo(directory, fifo)
            continue
    return None


def _remove_trace_fifo(directory: Path | None, fifo: Path | None) -> None:
    if fifo is not None:
        try:
            fifo.unlink(missing_ok=True)
        except OSError:
            pass
    if directory is not None:
        try:
            directory.rmdir()
        except OSError:
            pass


def _strip_pid(line: str) -> tuple[str, str]:
    for pattern in (_PID_BRACKET, _PID_PLAIN):
        match = pattern.match(line)
        if match is not None:
            return match.group(1), line[match.end() :]
    return "root", line


def _decoded_path(arguments: str) -> tuple[str | None, int, bool]:
    match = _QUOTED.search(arguments)
    if match is None:
        return None, -1, False
    truncated = arguments[match.end() :].lstrip().startswith("...")
    try:
        value = ast.literal_eval(match.group(0))
    except (SyntaxError, ValueError):
        return None, -1, truncated
    if not isinstance(value, str) or "\x00" in value:
        return None, -1, truncated
    return value, match.start(), truncated


def _fd_path(arguments: str, *, before: int | None = None) -> str:
    prefix = arguments if before is None else arguments[:before]
    matches = list(_FD_PATH.finditer(prefix))
    return matches[-1].group(1) if matches else ""


def _absolute_observed_path(
    raw_path: str,
    *,
    arguments: str,
    path_offset: int,
    cwd: Path,
) -> Path | None:
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        annotated = _fd_path(arguments, before=path_offset)
        base = Path(annotated) if annotated.startswith("/") else cwd
        candidate = base / candidate
    try:
        return Path(os.path.normpath(os.path.abspath(candidate)))
    except (OSError, ValueError):
        return None


def _return_integer(result: str) -> int | None:
    match = _RETURN_INTEGER.match(result)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def parse_strace(
    raw: bytes,
    *,
    workspace: Path,
    initial_cwd: Path | None = None,
    truncated: bool = False,
    allow_runtime_getrandom: bool = False,
    allow_workspace_root: bool = False,
    allow_python_seed: bool = False,
    require_process_lifecycle: bool = False,
) -> ParsedTrace:
    """Parse bounded strace text into repository-relative aggregate inputs."""
    try:
        root = workspace.resolve(strict=True)
    except (OSError, RuntimeError):
        root = Path(os.path.abspath(workspace))
    initial = initial_cwd or root
    try:
        initial = Path(os.path.normpath(os.path.abspath(initial)))
    except (OSError, ValueError):
        initial = root

    if require_process_lifecycle:
        if __package__:
            from . import click_observer_process_tree
        else:
            import click_observer_process_tree
        tree = click_observer_process_tree.inspect(raw, truncated=truncated)
        raw = tree.trace
        truncated = truncated or not tree.complete
    cwd_by_pid: dict[str, Path] = {"root": initial}
    inputs: dict[str, dict[str, Any]] = {}
    absolute_inputs: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    absolute_conflicts: set[str] = set()
    external_paths: set[str] = set()
    unresolved = 1 if truncated else 0
    child_processes = 0
    root_exec_observed = False
    seed_seen = False

    def add_path(
        path: Path | None,
        *,
        kind: str,
        operation: str,
        shadow_projection: bool = True,
    ) -> None:
        nonlocal unresolved
        if path is None:
            unresolved = _bounded_add(unresolved, 1)
            return
        absolute = os.path.normcase(str(path))
        absolute_existing = absolute_inputs.get(absolute)
        if absolute_existing is not None and absolute_existing["kind"] != kind:
            absolute_inputs.pop(absolute, None)
            absolute_conflicts.add(absolute)
            unresolved = _bounded_add(unresolved, 1)
        elif absolute not in absolute_conflicts:
            if absolute_existing is None:
                if len(absolute_inputs) >= click_dependency_cache.MAX_SHADOW_OBSERVER_INPUTS:
                    unresolved = _bounded_add(unresolved, 1)
                else:
                    absolute_inputs[absolute] = {
                        "path": str(path), "kind": kind, "operations": {operation}
                    }
            else:
                absolute_existing["operations"].add(operation)
        if not shadow_projection:
            return
        try:
            relative_path = path.relative_to(root)
        except ValueError:
            external = os.path.normcase(str(path))
            if external not in external_paths:
                if (
                    len(external_paths)
                    >= click_dependency_cache.MAX_SHADOW_OBSERVER_INPUTS
                ):
                    unresolved = _bounded_add(unresolved, 1)
                else:
                    external_paths.add(external)
            return
        relative = relative_path.as_posix()
        if relative in {"", "."}:
            if not allow_workspace_root:
                unresolved = _bounded_add(unresolved, 1)
            return
        if kind == "directory":
            relative = f"{relative.rstrip('/')}/"
        try:
            encoded_relative = relative.encode("utf-8")
        except UnicodeEncodeError:
            unresolved = _bounded_add(unresolved, 1)
            return
        if (
            len(encoded_relative) > click_dependency_cache.MAX_SHADOW_OBSERVER_PATH_BYTES
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
            inputs.pop(relative, None)
            conflicts.add(relative)
            unresolved = _bounded_add(unresolved, 1)
            return
        if relative in conflicts:
            return
        if existing is None:
            if len(inputs) >= click_dependency_cache.MAX_SHADOW_OBSERVER_INPUTS:
                unresolved = _bounded_add(unresolved, 1)
                return
            inputs[relative] = {
                "path": relative,
                "kind": kind,
                "operations": {operation},
            }
        else:
            existing["operations"].add(operation)

    for raw_line in raw.decode("utf-8", errors="replace").splitlines():
        pid, line = _strip_pid(raw_line.strip())
        if not line or line.startswith(("--- ", "+++ ", "strace:")):
            continue
        if "<unfinished ...>" in line or line.startswith("<..."):
            unresolved = _bounded_add(unresolved, 1)
            continue
        match = _CALL.match(line)
        if match is None:
            unresolved = _bounded_add(unresolved, 1)
            continue
        call, arguments, result = match.groups()
        cwd = cwd_by_pid.get(pid, initial)
        returned = _return_integer(result)

        if call in _CHILD_CALLS:
            if returned is not None and returned > 0:
                child_processes = _bounded_add(child_processes, 1)
                if (
                    len(cwd_by_pid)
                    <= click_dependency_cache.MAX_SHADOW_OBSERVER_INPUTS
                ):
                    cwd_by_pid[str(returned)] = cwd
                else:
                    unresolved = _bounded_add(unresolved, 1)
            continue
        if call in _IGNORED_PROCESS_CALLS:
            continue
        if call == "getcwd" and returned is not None and returned >= 0:
            # The runner separately binds the canonical cwd.
            continue
        if (
            call == "getrandom" and allow_python_seed and not seed_seen
            and returned == 2496 and re.search(r",\s*2496,\s*GRND_NONBLOCK\s*$", arguments)
        ):
            seed_seen = True
            continue
        if (
            call == "getrandom"
            and allow_runtime_getrandom
            and returned == 8
            and re.search(r",\s*8,\s*GRND_NONBLOCK\s*$", arguments)
        ):
            # glibc obtains an internal startup canary this way even when
            # CPython hash randomization is explicitly disabled. Project-level
            # random APIs remain covered by the native profile and every other
            # getrandom shape remains unresolved.
            continue
        if call == "chdir":
            path_text, offset, shortened = _decoded_path(arguments)
            if shortened or path_text is None or returned != 0:
                if shortened or path_text is None:
                    unresolved = _bounded_add(unresolved, 1)
                continue
            changed = _absolute_observed_path(
                path_text,
                arguments=arguments,
                path_offset=offset,
                cwd=cwd,
            )
            if changed is None:
                unresolved = _bounded_add(unresolved, 1)
            else:
                cwd_by_pid[pid] = changed
            continue
        if call == "fchdir":
            annotated = _fd_path(arguments)
            if returned == 0 and annotated.startswith("/"):
                cwd_by_pid[pid] = Path(os.path.normpath(annotated))
            elif returned == 0:
                unresolved = _bounded_add(unresolved, 1)
            continue
        if call in _DIRECTORY_CALLS:
            annotated = _fd_path(arguments)
            if returned is not None and returned >= 0 and annotated.startswith("/"):
                add_path(
                    Path(os.path.normpath(annotated)),
                    kind="directory",
                    operation="enumerate",
                )
            elif returned is None:
                unresolved = _bounded_add(unresolved, 1)
            continue
        if call not in _EXEC_CALLS | _OPEN_CALLS | _METADATA_CALLS:
            unresolved = _bounded_add(unresolved, 1)
            continue

        path_text, offset, shortened = _decoded_path(arguments)
        if shortened or path_text is None:
            unresolved = _bounded_add(unresolved, 1)
            continue
        observed = _absolute_observed_path(
            path_text,
            arguments=arguments,
            path_offset=offset,
            cwd=cwd,
        )
        if path_text == "" and "AT_EMPTY_PATH" in arguments:
            # statx/newfstatat may inspect a descriptor instead of a pathname.
            # Bind its kernel-reported target, never the caller's cwd.
            annotated = _fd_path(arguments, before=offset)
            observed = Path(os.path.normpath(annotated)) if annotated.startswith("/") else None
        missing = result.startswith("-1 ENOENT")
        if call in _EXEC_CALLS:
            if returned == 0:
                root_exec_observed = True
                add_path(observed, kind="file", operation="execute")
            elif not missing:
                unresolved = _bounded_add(unresolved, 1)
            else:
                add_path(observed, kind="missing", operation="execute")
            continue
        if missing:
            operation = "read" if call in _OPEN_CALLS else "metadata"
            add_path(observed, kind="missing", operation=operation)
            continue
        if returned is None or returned < 0:
            continue
        if call in _OPEN_CALLS:
            if "O_WRONLY" in arguments and "O_RDWR" not in arguments:
                continue
            resolved_descriptor = _fd_path(result)
            lexical_observed = observed
            if resolved_descriptor.startswith("/"):
                observed = Path(os.path.normpath(resolved_descriptor))
            directory = "O_DIRECTORY" in arguments
            # Preserve a lexical symlink or alias as well as the kernel's
            # resolved descriptor annotation.  The authoritative snapshot
            # binds both; Shadow still receives only its normal projection.
            if lexical_observed is not None and lexical_observed != observed:
                add_path(
                    lexical_observed,
                    kind="directory" if directory else "file",
                    operation="metadata" if "O_PATH" in arguments or directory else "read",
                    shadow_projection=False,
                )
            add_path(
                observed,
                kind="directory" if directory else "file",
                operation="metadata" if "O_PATH" in arguments or directory else "read",
            )
            continue
        directory = "S_IFDIR" in arguments
        add_path(
            observed,
            kind="directory" if directory else "file",
            operation="metadata",
        )

    normalized_inputs = tuple(
        {
            "path": relative,
            "kind": inputs[relative]["kind"],
            "operations": sorted(inputs[relative]["operations"]),
        }
        for relative in sorted(inputs)
    )
    normalized_absolute_inputs = tuple(
        {
            "path": absolute_inputs[key]["path"],
            "kind": absolute_inputs[key]["kind"],
            "operations": sorted(absolute_inputs[key]["operations"]),
        }
        for key in sorted(absolute_inputs)
    )
    process_tree_complete = not truncated and unresolved == 0
    return ParsedTrace(
        inputs=normalized_inputs,
        external_input_count=min(
            len(external_paths), click_dependency_cache.MAX_JSON_SAFE_INTEGER
        ),
        unresolved_event_count=unresolved,
        child_process_count=child_processes,
        process_tree_complete=process_tree_complete,
        root_exec_observed=root_exec_observed,
        absolute_inputs=normalized_absolute_inputs,
    )


_record = click_observer_common.record
_fallback_execution = click_observer_common.fallback_execution


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
    probe_version: BackendProbe = probe_strace_version,
    spawn_argv: SpawnArgv = click_process.spawn_argv,
    terminate_group: Callable[[subprocess.Popen[Any]], int] = (
        click_process.terminate_process_group
    ),
    system_name: str | None = None,
    already_traced: Callable[[], bool] = _already_traced,
    capture_limit: int = MAX_RAW_TRACE_BYTES,
    process_observer: Callable[..., None] | None = None,
) -> ShadowExecution:
    """Execute one command exactly once and collect best-effort Linux trace data."""
    try:
        system = platform.system() if system_name is None else system_name
        nested_trace = already_traced() if system == "Linux" else False
    except Exception:
        system = ""
        nested_trace = False
    if system != "Linux" or nested_trace:
        return _fallback_execution(
            execute_unobserved,
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
            status="unavailable",
        )

    preparation_started = time.monotonic()
    try:
        executable, resolve_error = resolve_backend("strace", workspace=workspace)
    except Exception:
        executable, resolve_error = None, "backend resolution failed"
    if resolve_error or not isinstance(executable, str) or not executable:
        preparation_ms = max(0, int((time.monotonic() - preparation_started) * 1000))
        return _fallback_execution(
            execute_unobserved,
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
            status="unavailable",
            preparation_ms=preparation_ms,
        )
    try:
        digest = digest_file(Path(executable))
        version = probe_version(executable)
    except Exception:
        digest = ""
        version = ""
    if (
        not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
        or not isinstance(version, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", version) is None
    ):
        preparation_ms = max(0, int((time.monotonic() - preparation_started) * 1000))
        return _fallback_execution(
            execute_unobserved,
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
            status="unavailable",
            preparation_ms=preparation_ms,
        )
    preparation_ms = max(0, int((time.monotonic() - preparation_started) * 1000))

    capture = _PipeCapture()
    trace_directory: Path | None = None
    trace_fifo: Path | None = None
    child: subprocess.Popen[Any] | None = None
    reader: threading.Thread | None = None
    stop_reader = threading.Event()
    command_started = time.monotonic()
    try:
        location = _create_trace_fifo(workspace)
        if location is None:
            raise OSError("private external trace FIFO is unavailable")
        trace_directory, trace_fifo = location
        bounded_capture_limit = (
            capture_limit
            if isinstance(capture_limit, int)
            and not isinstance(capture_limit, bool)
            and capture_limit > 0
            else MAX_RAW_TRACE_BYTES
        )
        reader = threading.Thread(
            target=_read_fifo,
            args=(trace_fifo, bounded_capture_limit, capture, stop_reader),
            daemon=True,
        )
        reader.start()
        traced_argv = [
            executable,
            "-D",
            "-f",
            "-qq",
            "-yy",
            "-s",
            str(STRACE_STRING_LIMIT),
            "-e",
            STRACE_TRACE_EXPRESSION,
            "-o",
            str(trace_fifo),
            "--",
            *list(argv),
        ]
        child = spawn_argv(
            traced_argv,
            cwd=workspace,
            env=dict(environment),
        )
        click_process.target_started()
        exit_code = int(child.wait())
    except KeyboardInterrupt:
        if child is not None:
            try:
                terminate_group(child)
            except Exception:
                pass
        exit_code = 130
        capture.failed = True
    except (OSError, subprocess.SubprocessError, RuntimeError, TypeError, ValueError):
        if child is None:
            if reader is not None:
                stop_reader.set()
                if trace_fifo is not None:
                    _wake_fifo(trace_fifo)
                reader.join(timeout=1)
            preparation_ms = _bounded_add(
                preparation_ms,
                max(0, int((time.monotonic() - command_started) * 1000)),
            )
            return _fallback_execution(
                execute_unobserved,
                evidence_key=evidence_key,
                check_digest=check_digest,
                mutation_revision=mutation_revision,
                status="failed",
                backend_name="strace",
                backend_version=version,
                backend_digest=digest,
                preparation_ms=preparation_ms,
            )
        try:
            exit_code = int(child.wait())
        except (
            OSError,
            subprocess.SubprocessError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            exit_code = 127
        capture.failed = True
    finally:
        if reader is not None:
            reader.join(timeout=1)
            if reader.is_alive():
                capture.failed = True
                stop_reader.set()
                if trace_fifo is not None:
                    _wake_fifo(trace_fifo)
                reader.join(timeout=0.2)
        _remove_trace_fifo(trace_directory, trace_fifo)

    command_duration_ms = max(0, int((time.monotonic() - command_started) * 1000))
    parsing_started = time.monotonic()
    if process_observer is not None:
        try:
            process_observer(capture.data, truncated=capture.truncated or capture.failed)
        except Exception:
            pass  # Candidate analysis cannot change or repeat target execution.
    raw_trace = capture.data
    capture.data = b""
    try:
        parsed = parse_strace(
            raw_trace,
            workspace=observation_root or workspace,
            initial_cwd=workspace,
            truncated=capture.truncated or capture.failed,
            allow_workspace_root=process_observer is not None,
            require_process_lifecycle=process_observer is not None,
        )
    except Exception:
        capture.failed = True
        parsed = ParsedTrace(
            inputs=(),
            external_input_count=0,
            unresolved_event_count=1,
            child_process_count=0,
            process_tree_complete=False,
            root_exec_observed=False,
        )
    finally:
        raw_trace = b""
    parsing_ms = max(0, int((time.monotonic() - parsing_started) * 1000))
    observer_overhead_ms = _bounded_add(preparation_ms, parsing_ms)

    identity_started = time.monotonic()
    try:
        final_digest = digest_file(Path(executable))
    except Exception:
        final_digest = ""
    identity_ms = max(0, int((time.monotonic() - identity_started) * 1000))
    observer_overhead_ms = _bounded_add(observer_overhead_ms, identity_ms)
    if final_digest != digest:
        return ShadowExecution(
            exit_code=exit_code,
            record=_record(
                evidence_key=evidence_key,
                check_digest=check_digest,
                mutation_revision=mutation_revision,
                backend_name=None,
                status="unavailable",
                process_tree_complete=False,
                command_duration_ms=command_duration_ms,
                observer_overhead_ms=observer_overhead_ms,
            ),
        )

    status = (
        "failed"
        if capture.failed or not parsed.root_exec_observed
        else "partial"
        if capture.truncated or not parsed.process_tree_complete
        else "complete"
    )
    record = _record(
        evidence_key=evidence_key,
        check_digest=check_digest,
        mutation_revision=mutation_revision,
        backend_name="strace",
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
        command_duration_ms=command_duration_ms,
        observer_overhead_ms=observer_overhead_ms,
    )
    return ShadowExecution(exit_code=exit_code, record=record)
