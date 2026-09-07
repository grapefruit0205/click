#!/usr/bin/env python3
"""Explicit native CPython observers that can issue signed dependency facts.

The target command runs once.  A failed preparation or incomplete event stream
returns a non-reusable observation and never triggers a second diagnostic run.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
from typing import Any

if __package__:
    from . import (
        click_dependency_cache,
        click_observation_inputs,
        click_observer_linux,
        click_observer_macos,
        click_observer_runtime,
        click_observer_windows,
        click_process,
    )
else:
    import click_dependency_cache
    import click_observation_inputs
    import click_observer_linux
    import click_observer_macos
    import click_observer_runtime
    import click_observer_windows
    import click_process


TRACE_EXPRESSION = "trace=%file,%process,%network,%ipc,getdents64,getrandom"
ENVELOPE_VERSION = 1
ENVELOPE_FIELDS = frozenset({"version", "observation", "attestation"})
NORMAL_NATIVE_EVENTS = frozenset({
    "native-started", "native-profile-ready", "native-finished",
})
NATIVE_REASON_EVENTS = frozenset({
    "descriptor-operation-needs-review", "dynamic-runtime-introspection",
    "external-or-native-input", "inherited-descriptor-input",
    "child-process-unsupported",
    "concurrent-execution-unsupported",
    "native-profile-unavailable", "observer-introspection-or-tampering",
    "time-random-input",
})
FULL_BINDING_FIELDS = click_dependency_cache.AUTHORITATIVE_BINDING_FIELDS - {
    "execution_digest"
}
DIGEST = re.compile(r"^[0-9a-f]{64}$")
NATIVE_MAIN_THREAD = re.compile(r"^native-main-thread:([1-9][0-9]{0,19})$")
MAX_AUTHORITATIVE_CAPTURE_BYTES = click_observer_windows.MAX_RAW_TRACE_BYTES


@dataclass(frozen=True, slots=True)
class AuthoritativeExecution:
    exit_code: int
    envelope: dict[str, Any]


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def _attestation(observation: dict[str, Any], secret: str) -> str:
    return hmac.new(secret.encode(), _canonical(observation), hashlib.sha256).hexdigest()


def signed_envelope(observation: dict[str, Any], secret: str) -> dict[str, Any]:
    return {
        "version": ENVELOPE_VERSION,
        "observation": observation,
        "attestation": _attestation(observation, secret),
    }


def verified_observation(
    value: Any,
    *,
    secret: str,
    expected_binding: dict[str, Any],
) -> dict[str, Any] | None:
    if (
        not isinstance(value, dict)
        or set(value) != ENVELOPE_FIELDS
        or value.get("version") != ENVELOPE_VERSION
        or not isinstance(value.get("attestation"), str)
        or not DIGEST.fullmatch(value["attestation"])
        or not click_dependency_cache.authoritative_dependency_observation_is_valid(
            value.get("observation")
        )
        or not isinstance(expected_binding, dict)
        or set(expected_binding) != FULL_BINDING_FIELDS
    ):
        return None
    observation = value["observation"]
    if not hmac.compare_digest(value["attestation"], _attestation(observation, secret)):
        return None
    binding = observation["binding"]
    if any(binding.get(field) != expected_binding[field] for field in expected_binding):
        return None
    return json.loads(json.dumps(observation))


def _binding(context: dict[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(context, dict)
        or set(context) != FULL_BINDING_FIELDS
        or not isinstance(context.get("mutation_revision"), int)
        or isinstance(context.get("mutation_revision"), bool)
        or context.get("mutation_revision", -1) < 0
        or any(
            not isinstance(context.get(field), str)
            or not DIGEST.fullmatch(context[field])
            for field in FULL_BINDING_FIELDS - {"mutation_revision"}
        )
    ):
        raise ValueError("authoritative observer binding is invalid")
    return {
        **context,
        "execution_digest": hashlib.sha256(os.urandom(32)).hexdigest(),
    }


def _companion(runtime: dict[str, Any]) -> dict[str, str]:
    return {
        field: str(runtime[field])
        for field in click_dependency_cache.AUTHORITATIVE_COMPANION_FIELDS
    }


def _project_paths(records: list[dict[str, Any]]) -> list[str]:
    return sorted({
        row["path"] + "/" if row["kind"] == "directory" else row["path"]
        for row in records
        if row["root"] == "project" and row["path"]
    })


def _observation(
    *,
    binding: dict[str, Any],
    runtime: dict[str, Any],
    reasons: set[str],
    inputs: list[dict[str, Any]] | None = None,
    child_processes: int = 0,
    process_tree_complete: bool = False,
) -> dict[str, Any]:
    records = inputs or []
    return click_dependency_cache.authoritative_dependency_observation(
        status="complete" if not reasons else "failed",
        paths=_project_paths(records),
        child_processes=child_processes,
        process_tree_complete=process_tree_complete,
        backend=json.loads(json.dumps(runtime["backend"])),
        companion=_companion(runtime),
        binding=binding,
        inputs=records,
        ineligibility_reasons=reasons,
        profile=str(runtime["profile"]),
    )


def _supported_command(argv: Sequence[str]) -> bool:
    try:
        return bool(
            len(argv) >= 3
            and Path(argv[0]).resolve(strict=True) == Path(sys.executable).resolve(strict=True)
            and list(argv[1:3]) == ["-m", "unittest"]
            and all(isinstance(item, str) and item and "\x00" not in item for item in argv)
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _read_native_events(descriptor: int) -> tuple[set[str], bool]:
    retained = bytearray()
    truncated = False
    while True:
        try:
            chunk = os.read(descriptor, 4096)
        except BlockingIOError:
            break
        except OSError:
            return set(), True
        if not chunk:
            break
        remaining = max(0, 64 * 1024 - len(retained))
        retained.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    try:
        lines = retained.decode("ascii", errors="strict").splitlines()
    except UnicodeError:
        return set(), True
    return set(lines), truncated or len(lines) != len(set(lines))


def _native_event_reasons(events: set[str], failed: bool) -> set[str]:
    reasons: set[str] = set()
    ordinary_events = {
        event for event in events if NATIVE_MAIN_THREAD.fullmatch(event) is None
    }
    if failed or not NORMAL_NATIVE_EVENTS.issubset(ordinary_events):
        reasons.add("event-loss")
    for event in ordinary_events - NORMAL_NATIVE_EVENTS:
        reasons.add(event if event in NATIVE_REASON_EVENTS else "event-loss")
    return reasons


def _native_main_thread(events: set[str]) -> int | None:
    matches = [
        match
        for event in events
        if (match := NATIVE_MAIN_THREAD.fullmatch(event)) is not None
    ]
    if len(matches) != 1:
        return None
    try:
        value = int(matches[0].group(1))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _windows_native_channel() -> tuple[int, int, int]:
    """Create a bounded inherited Windows pipe for the native companion."""

    if os.name != "nt":
        raise OSError("Windows native channel is unavailable")
    import msvcrt

    read_descriptor, write_descriptor = os.pipe()
    try:
        os.set_inheritable(read_descriptor, False)
        os.set_inheritable(write_descriptor, True)
        os.set_blocking(read_descriptor, False)
        handle = int(msvcrt.get_osfhandle(write_descriptor))
        if handle <= 0:
            raise OSError("invalid Windows native channel handle")
        return read_descriptor, write_descriptor, handle
    except BaseException:
        os.close(read_descriptor)
        os.close(write_descriptor)
        raise


def _close_descriptor(descriptor: int) -> None:
    if descriptor < 0:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


def _snapshot_records(
    snapshot: click_observation_inputs.InputSnapshot,
    absolute_inputs: Sequence[dict[str, Any]],
    *,
    observation_root: Path,
) -> list[dict[str, Any]]:
    inputs: dict[str, set[str]] = {}
    resolved_root = observation_root.resolve()
    runtime_roots = tuple(
        Path(path) for path in getattr(snapshot, "roots", {}).values()
    )
    for row in absolute_inputs:
        if (
            not isinstance(row, dict)
            or set(row) != {"path", "kind", "operations"}
            or row.get("kind") not in {"file", "directory", "missing"}
            or not isinstance(row.get("operations"), list)
        ):
            raise click_observation_inputs.InputError("invalid-native-input")
        observed_path = Path(str(row["path"]))
        operations = set(row["operations"])
        if not operations or not operations <= {"read", "metadata", "enumerate", "execute"}:
            raise click_observation_inputs.InputError("invalid-native-operation")
        if operations == {"metadata"} and (
            observed_path in resolved_root.parents
            or any(observed_path in root.parents for root in runtime_roots)
        ):
            continue
        kind = str(row["kind"])
        if kind == "missing" and observed_path.exists():
            raise click_observation_inputs.InputError("observed-missing-input-now-exists")
        if kind == "directory" and not observed_path.is_dir():
            raise click_observation_inputs.InputError("observed-directory-kind-changed")
        # fs_usage and Kernel-File ETW call ordinary open/read/metadata events
        # "file" events even when the referenced object is a directory.  The
        # pre-execution InputSnapshot already binds the object's actual type
        # and metadata, so let it classify these generic existing inputs.
        inputs.setdefault(str(observed_path), set()).update(operations)
    return snapshot.records(inputs)


def _darwin_command(
    argv: Sequence[str],
    *,
    workspace: Path,
    observation_root: Path,
    environment: Mapping[str, str],
    binding: dict[str, Any],
    runtime: dict[str, Any],
    runner_token: str,
    fallback: Callable[[str], AuthoritativeExecution],
    resolve_backend: Callable[..., tuple[str | None, str]],
    digest_file: Callable[[Path], str],
    spawn_argv: Callable[..., subprocess.Popen[Any]],
    terminate_group: Callable[[subprocess.Popen[Any]], int],
    capture_limit: int,
) -> AuthoritativeExecution:
    injection_keys = (
        "DYLD_INSERT_LIBRARIES",
        "DYLD_FORCE_FLAT_NAMESPACE",
        "CLICK_NATIVE_OBSERVER_BOOTSTRAP",
        "CLICK_NATIVE_OBSERVER_CHANNEL",
        "CLICK_NATIVE_OBSERVER_ORIGINAL_PYTHONPATH",
        "CLICK_NATIVE_OBSERVER_PYTHONPATH_PRESENT",
        "CLICK_NATIVE_OBSERVER_ROOT",
    )
    if any(key in environment for key in injection_keys):
        return fallback("unsupported-runtime")
    artifact = click_observer_runtime.validate(observation_root, runtime)
    if artifact is None:
        return fallback("native-companion-unavailable")
    try:
        backend, error = resolve_backend("fs_usage", workspace=workspace)
    except Exception:
        backend, error = None, "backend resolution failed"
    if (
        error
        or not isinstance(backend, str)
        or not backend
        or not click_observer_macos.native_fs_usage(backend)
    ):
        return fallback("backend-unavailable")
    try:
        if (
            digest_file(Path(backend)) != runtime["backend"]["digest"]
            or click_observer_macos.probe_macos_version()
            != runtime["backend"]["version"]
        ):
            return fallback("backend-changed")
    except Exception:
        return fallback("backend-unavailable")
    location = click_observer_linux._create_trace_fifo(observation_root)
    if location is None:
        return fallback("capture-failed")
    directory, native_fifo = location
    descriptor = -1
    try:
        descriptor = os.open(
            native_fifo, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC
        )
        snapshot = click_observation_inputs.InputSnapshot(
            observation_root,
            str(runtime["artifact_id"]),
            profile=str(runtime["profile"]),
        )
    except (OSError, ValueError, RuntimeError, TypeError):
        if descriptor >= 0:
            os.close(descriptor)
        click_observer_linux._remove_trace_fifo(directory, native_fifo)
        return fallback("input-snapshot-failed")
    original_pythonpath = str(environment.get("PYTHONPATH", ""))
    artifact_directory = str(artifact.parent)
    observed_environment = dict(environment)
    observed_environment.update(
        {
            "CLICK_NATIVE_OBSERVER_BOOTSTRAP": artifact_directory,
            "CLICK_NATIVE_OBSERVER_CHANNEL": str(native_fifo),
            "CLICK_NATIVE_OBSERVER_ORIGINAL_PYTHONPATH": original_pythonpath,
            "CLICK_NATIVE_OBSERVER_PYTHONPATH_PRESENT": (
                "1" if "PYTHONPATH" in environment else "0"
            ),
            "CLICK_NATIVE_OBSERVER_ROOT": str(observation_root),
            "PYTHONPATH": (
                artifact_directory
                + (os.pathsep + original_pythonpath if original_pythonpath else "")
            ),
        }
    )
    try:
        collected = click_observer_macos.collect_command(
            argv,
            workspace=workspace,
            environment=observed_environment,
            executable=backend,
            spawn_argv=spawn_argv,
            terminate_group=terminate_group,
            capture_limit=capture_limit,
            strict_pid_scope=True,
        )
    except Exception:
        # The collector owns a suspended-target transaction and reports
        # ordinary pre-start failures as target_started=False. An exception
        # escaping that boundary is ambiguous, so rerunning could execute the
        # target twice. Preserve the fail-closed one-execution guarantee.
        collected = click_observer_macos.CollectedExecution(
            127,
            b"",
            False,
            True,
            True,
            0,
            0,
            False,
        )
    if not collected.target_started:
        try:
            os.close(descriptor)
        except OSError:
            pass
        click_observer_linux._remove_trace_fifo(directory, native_fifo)
        return fallback("capture-failed")
    reasons: set[str] = set()
    records: list[dict[str, Any]] = []
    try:
        events, native_failed = _read_native_events(descriptor)
        reasons.update(_native_event_reasons(events, native_failed))
        root_thread_id = _native_main_thread(events)
        if root_thread_id is None:
            reasons.add("event-loss")
        parsed = click_observer_macos.parse_fs_usage(
            collected.raw,
            workspace=observation_root,
            truncated=collected.truncated or collected.failed,
            root_execution_bound=True,
            process_scope_complete=collected.process_scope_complete,
            allow_workspace_root=True,
            root_thread_id=root_thread_id,
        )
        if collected.failed or collected.truncated:
            reasons.add("capture-failed")
        if parsed.unresolved_event_count:
            reasons.add("unresolved-event")
        if not parsed.process_tree_complete or not parsed.root_exec_observed:
            reasons.add("process-tree-incomplete")
        if parsed.child_process_count:
            reasons.add("child-process-unsupported")
        records = _snapshot_records(
            snapshot,
            parsed.absolute_inputs,
            observation_root=observation_root,
        )
        if click_observer_runtime.validate(observation_root, runtime) is None:
            reasons.add("native-companion-unavailable")
        if (
            digest_file(Path(backend)) != runtime["backend"]["digest"]
            or click_observer_macos.probe_macos_version()
            != runtime["backend"]["version"]
        ):
            reasons.add("backend-changed")
    except (OSError, ValueError, RuntimeError, TypeError):
        reasons.add("input-snapshot-failed")
        records = []
        parsed = click_observer_macos.ParsedTrace((), 0, 1, 0, False, False)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        click_observer_linux._remove_trace_fifo(directory, native_fifo)
    observation = _observation(
        binding=binding,
        runtime=runtime,
        reasons=reasons,
        inputs=records,
        child_processes=parsed.child_process_count,
        process_tree_complete=parsed.process_tree_complete,
    )
    return AuthoritativeExecution(
        int(collected.exit_code), signed_envelope(observation, runner_token)
    )


def _windows_command(
    argv: Sequence[str],
    *,
    workspace: Path,
    observation_root: Path,
    environment: Mapping[str, str],
    binding: dict[str, Any],
    runtime: dict[str, Any],
    runner_token: str,
    fallback: Callable[[str], AuthoritativeExecution],
    resolve_backend: Callable[..., tuple[str | None, str]],
    digest_file: Callable[[Path], str],
    spawn_argv: Callable[..., subprocess.Popen[Any]],
    terminate_group: Callable[[subprocess.Popen[Any]], int],
    capture_limit: int,
) -> AuthoritativeExecution:
    injection_keys = (
        "CLICK_NATIVE_OBSERVER_BOOTSTRAP",
        "CLICK_NATIVE_OBSERVER_HANDLE",
        "CLICK_NATIVE_OBSERVER_ORIGINAL_PYTHONPATH",
        "CLICK_NATIVE_OBSERVER_PYTHONPATH_PRESENT",
        "CLICK_NATIVE_OBSERVER_ROOT",
    )
    if any(key in environment for key in injection_keys):
        return fallback("unsupported-runtime")
    artifact = click_observer_runtime.validate(observation_root, runtime)
    if artifact is None:
        return fallback("native-companion-unavailable")

    backends: dict[str, str] = {}
    for name in ("logman", "tracerpt"):
        try:
            executable, error = resolve_backend(name, workspace=workspace)
        except Exception:
            executable, error = None, "backend resolution failed"
        if (
            error
            or not isinstance(executable, str)
            or not executable
            or not click_observer_windows.native_windows_tool(
                executable, f"{name}.exe"
            )
        ):
            return fallback("backend-unavailable")
        backends[name] = executable
    try:
        backend_digest = click_observer_windows.combined_backend_digest(
            digest_file(Path(backends["logman"])),
            digest_file(Path(backends["tracerpt"])),
        )
        backend_version = click_observer_windows.probe_windows_version()
        if (
            backend_digest != runtime["backend"]["digest"]
            or backend_version != runtime["backend"]["version"]
        ):
            return fallback("backend-changed")
    except Exception:
        return fallback("backend-unavailable")

    read_descriptor = -1
    write_descriptor = -1
    try:
        snapshot = click_observation_inputs.InputSnapshot(
            observation_root,
            str(runtime["artifact_id"]),
            profile=str(runtime["profile"]),
        )
        read_descriptor, write_descriptor, inherited_handle = (
            _windows_native_channel()
        )
    except (OSError, ValueError, RuntimeError, TypeError):
        _close_descriptor(read_descriptor)
        _close_descriptor(write_descriptor)
        return fallback("input-snapshot-failed")

    original_pythonpath = str(environment.get("PYTHONPATH", ""))
    artifact_directory = str(artifact.parent)
    observed_environment = dict(environment)
    observed_environment.update(
        {
            "CLICK_NATIVE_OBSERVER_BOOTSTRAP": artifact_directory,
            "CLICK_NATIVE_OBSERVER_HANDLE": str(inherited_handle),
            "CLICK_NATIVE_OBSERVER_ORIGINAL_PYTHONPATH": original_pythonpath,
            "CLICK_NATIVE_OBSERVER_PYTHONPATH_PRESENT": (
                "1" if "PYTHONPATH" in environment else "0"
            ),
            "CLICK_NATIVE_OBSERVER_ROOT": str(observation_root),
            "PYTHONPATH": (
                artifact_directory
                + (os.pathsep + original_pythonpath if original_pythonpath else "")
            ),
        }
    )
    spawned_child: subprocess.Popen[Any] | None = None

    def tracked_spawn(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        nonlocal spawned_child
        kwargs["close_fds"] = False
        child = spawn_argv(*args, **kwargs)
        spawned_child = child
        return child

    try:
        collected = click_observer_windows.collect_command(
            argv,
            workspace=workspace,
            environment=observed_environment,
            logman_executable=backends["logman"],
            tracerpt_executable=backends["tracerpt"],
            spawn_argv=tracked_spawn,
            terminate_group=terminate_group,
            capture_limit=capture_limit,
        )
    except Exception:
        exit_code = 127
        if spawned_child is not None:
            try:
                if spawned_child.poll() is None:
                    terminate_group(spawned_child)
                exit_code = int(spawned_child.wait())
            except Exception:
                pass
        collected = click_observer_windows.CollectedExecution(
            exit_code,
            (),
            False,
            True,
            spawned_child is not None,
            getattr(spawned_child, "pid", None),
            0,
            0,
            False,
        )
    _close_descriptor(write_descriptor)
    write_descriptor = -1
    if not collected.target_started:
        _close_descriptor(read_descriptor)
        return fallback("capture-failed")

    reasons: set[str] = set()
    records: list[dict[str, Any]] = []
    try:
        parsed = click_observer_windows.parse_windows_etw(
            collected.raw,
            workspace=observation_root,
            root_pid=int(collected.root_pid or -1),
            truncated=collected.truncated or collected.failed,
            root_execution_bound=True,
            process_scope_complete=collected.process_scope_complete,
            device_paths=click_observer_windows.windows_device_paths(),
            transparent_child_images=(
                str(getattr(sys, "_base_executable", "")),
            )
            if sys.prefix != sys.base_prefix
            and getattr(sys, "_base_executable", "")
            else (),
            allow_workspace_root=True,
        )
        events, native_failed = _read_native_events(read_descriptor)
        reasons.update(_native_event_reasons(events, native_failed))
        if collected.failed or collected.truncated:
            reasons.add("capture-failed")
        if parsed.unresolved_event_count:
            reasons.add("unresolved-event")
        if not parsed.process_tree_complete or not parsed.root_exec_observed:
            reasons.add("process-tree-incomplete")
        if parsed.child_process_count:
            reasons.add("child-process-unsupported")
        records = _snapshot_records(
            snapshot,
            parsed.absolute_inputs,
            observation_root=observation_root,
        )
        if click_observer_runtime.validate(observation_root, runtime) is None:
            reasons.add("native-companion-unavailable")
        final_digest = click_observer_windows.combined_backend_digest(
            digest_file(Path(backends["logman"])),
            digest_file(Path(backends["tracerpt"])),
        )
        if (
            final_digest != backend_digest
            or click_observer_windows.probe_windows_version()
            != backend_version
        ):
            reasons.add("backend-changed")
    except (OSError, ValueError, RuntimeError, TypeError):
        reasons.add("input-snapshot-failed")
        records = []
        parsed = click_observer_windows.ParsedTrace(
            (), 0, 1, 0, False, False
        )
    finally:
        _close_descriptor(read_descriptor)
        _close_descriptor(write_descriptor)

    observation = _observation(
        binding=binding,
        runtime=runtime,
        reasons=reasons,
        inputs=records,
        child_processes=parsed.child_process_count,
        process_tree_complete=parsed.process_tree_complete,
    )
    return AuthoritativeExecution(
        int(collected.exit_code), signed_envelope(observation, runner_token)
    )


def run_command(
    argv: Sequence[str],
    *,
    workspace: Path,
    observation_root: Path,
    environment: Mapping[str, str],
    binding_context: dict[str, Any],
    runtime: dict[str, Any],
    runner_token: str,
    execute_unobserved: Callable[[], int],
    resolve_backend: Callable[..., tuple[str | None, str]],
    digest_file: Callable[[Path], str],
    spawn_argv: Callable[..., subprocess.Popen[Any]] = click_process.spawn_argv,
    terminate_group: Callable[[subprocess.Popen[Any]], int] = click_process.terminate_process_group,
    capture_limit: int = MAX_AUTHORITATIVE_CAPTURE_BYTES,
) -> AuthoritativeExecution:
    """Execute one supported unittest argv and return a runner-token attestation."""
    binding = _binding(binding_context)

    def fallback(reason: str) -> AuthoritativeExecution:
        exit_code = int(execute_unobserved())
        observation = _observation(
            binding=binding, runtime=runtime, reasons={reason},
            process_tree_complete=False,
        )
        return AuthoritativeExecution(exit_code, signed_envelope(observation, runner_token))

    if not _supported_command(argv):
        return fallback("unsupported-command")
    if (
        environment.get("PYTHONHASHSEED") != "0"
        or environment.get("PYTHONDONTWRITEBYTECODE") != "1"
    ):
        return fallback("unsupported-runtime")
    profile = runtime.get("profile") if isinstance(runtime, dict) else None
    if profile == click_observer_runtime.DARWIN_PROFILE:
        return _darwin_command(
            argv,
            workspace=workspace,
            observation_root=observation_root,
            environment=environment,
            binding=binding,
            runtime=runtime,
            runner_token=runner_token,
            fallback=fallback,
            resolve_backend=resolve_backend,
            digest_file=digest_file,
            spawn_argv=spawn_argv,
            terminate_group=terminate_group,
            capture_limit=capture_limit,
        )
    if profile == click_observer_runtime.WINDOWS_PROFILE:
        return _windows_command(
            argv,
            workspace=workspace,
            observation_root=observation_root,
            environment=environment,
            binding=binding,
            runtime=runtime,
            runner_token=runner_token,
            fallback=fallback,
            resolve_backend=resolve_backend,
            digest_file=digest_file,
            spawn_argv=spawn_argv,
            terminate_group=terminate_group,
            capture_limit=capture_limit,
        )
    if profile != click_observer_runtime.PROFILE:
        return fallback("unsupported-runtime")
    if any(key in environment for key in (
        "LD_PRELOAD", "CLICK_NATIVE_OBSERVER_CHANNEL", "CLICK_NATIVE_OBSERVER_ROOT"
    )):
        return fallback("unsupported-runtime")
    artifact = click_observer_runtime.validate(observation_root, runtime)
    if artifact is None:
        return fallback("native-companion-unavailable")
    try:
        backend, error = resolve_backend("strace", workspace=workspace)
    except Exception:
        backend, error = None, "backend resolution failed"
    if error or not isinstance(backend, str) or not backend:
        return fallback("backend-unavailable")
    try:
        if (
            digest_file(Path(backend)) != runtime["backend"]["digest"]
            or click_observer_linux.probe_strace_version(backend)
            != runtime["backend"]["version"]
        ):
            return fallback("backend-changed")
    except Exception:
        return fallback("backend-unavailable")

    trace_location = click_observer_linux._create_trace_fifo(workspace)
    if trace_location is None:
        return fallback("capture-failed")
    trace_directory, trace_fifo = trace_location
    native_fifo = trace_directory / "native-events.pipe"
    native_descriptor = -1
    trace_capture = click_observer_linux._PipeCapture()
    trace_reader: threading.Thread | None = None
    stop_reader = threading.Event()
    child: subprocess.Popen[Any] | None = None
    started = False
    exit_code = 127
    snapshot: click_observation_inputs.InputSnapshot | None = None
    try:
        os.mkfifo(native_fifo, mode=0o600)
        native_descriptor = os.open(native_fifo, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        snapshot = click_observation_inputs.InputSnapshot(
            observation_root, str(runtime["artifact_id"])
        )
        trace_reader = threading.Thread(
            target=click_observer_linux._read_fifo,
            args=(trace_fifo, capture_limit, trace_capture, stop_reader),
            daemon=True,
        )
        trace_reader.start()
        traced = [
            backend, "-D", "-f", "-qq", "-yy", "-s",
            str(click_observer_linux.STRACE_STRING_LIMIT), "-e", TRACE_EXPRESSION,
            "-o", str(trace_fifo),
            "-E", f"LD_PRELOAD={artifact}",
            "-E", f"CLICK_NATIVE_OBSERVER_CHANNEL={native_fifo}",
            "-E", f"CLICK_NATIVE_OBSERVER_ROOT={observation_root}",
            "--", *list(argv),
        ]
        child = spawn_argv(traced, cwd=workspace, env=dict(environment))
        started = True
        click_process.target_started()
        exit_code = int(child.wait())
    except KeyboardInterrupt:
        if child is not None:
            try:
                terminate_group(child)
            except Exception:
                pass
        exit_code = 130
        trace_capture.failed = True
    except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError):
        trace_capture.failed = True
        if child is not None:
            try:
                exit_code = int(child.wait())
            except Exception:
                exit_code = 127
    finally:
        if trace_reader is not None:
            trace_reader.join(timeout=1)
            if trace_reader.is_alive():
                trace_capture.failed = True
                stop_reader.set()
                click_observer_linux._wake_fifo(trace_fifo)
                trace_reader.join(timeout=0.2)

    if not started:
        if native_descriptor >= 0:
            os.close(native_descriptor)
        native_fifo.unlink(missing_ok=True)
        click_observer_linux._remove_trace_fifo(trace_directory, trace_fifo)
        return fallback("capture-failed")

    reasons: set[str] = set()
    records: list[dict[str, Any]] = []
    try:
        parsed = click_observer_linux.parse_strace(
            trace_capture.data,
            workspace=observation_root,
            initial_cwd=workspace,
            truncated=trace_capture.truncated or trace_capture.failed,
            allow_runtime_getrandom=True,
            allow_workspace_root=True,
        )
        events, native_failed = _read_native_events(native_descriptor)
        if native_failed or not NORMAL_NATIVE_EVENTS.issubset(events):
            reasons.add("event-loss")
        for event in events - NORMAL_NATIVE_EVENTS:
            reasons.add(event if event in NATIVE_REASON_EVENTS else "event-loss")
        if parsed.unresolved_event_count:
            reasons.add("unresolved-event")
        if not parsed.process_tree_complete or not parsed.root_exec_observed:
            reasons.add("process-tree-incomplete")
        if parsed.child_process_count:
            reasons.add("child-process-unsupported")
        if snapshot is None:
            reasons.add("input-snapshot-failed")
        else:
            inputs: dict[str, set[str]] = {}
            for row in parsed.absolute_inputs:
                observed_path = Path(str(row["path"]))
                operations = set(row["operations"])
                # Ancestor metadata is part of resolving the already-bound
                # canonical workspace path. Global ancestors such as /tmp are
                # shared and can change for unrelated processes; persisting
                # them would make a complete run immediately non-current.
                if (
                    operations == {"metadata"}
                    and observed_path in observation_root.resolve().parents
                ):
                    continue
                inputs.setdefault(str(observed_path), set()).update(operations)
            records = snapshot.records(inputs)
        if click_observer_runtime.validate(observation_root, runtime) is None:
            reasons.add("native-companion-unavailable")
        if (
            digest_file(Path(backend)) != runtime["backend"]["digest"]
            or click_observer_linux.probe_strace_version(backend)
            != runtime["backend"]["version"]
        ):
            reasons.add("backend-changed")
    except (OSError, ValueError, RuntimeError, TypeError):
        reasons.add("input-snapshot-failed")
        records = []
        parsed = click_observer_linux.ParsedTrace(
            (), 0, 1, 0, False, False
        )
    finally:
        if native_descriptor >= 0:
            try:
                os.close(native_descriptor)
            except OSError:
                pass
        native_fifo.unlink(missing_ok=True)
        click_observer_linux._remove_trace_fifo(trace_directory, trace_fifo)
        trace_capture.data = b""

    observation = _observation(
        binding=binding,
        runtime=runtime,
        reasons=reasons,
        inputs=records,
        child_processes=parsed.child_process_count,
        process_tree_complete=parsed.process_tree_complete,
    )
    return AuthoritativeExecution(
        exit_code=exit_code,
        envelope=signed_envelope(observation, runner_token),
    )
