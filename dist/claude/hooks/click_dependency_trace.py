#!/usr/bin/env python3
"""Compatibility facade and cross-platform selector for Shadow Observer v1.

Callers keep the established ``click_dependency_trace`` surface while common
record handling, backend selection, and Linux collection have independent
owners. Unsupported platforms execute the real command once without a
collector and record honest unavailable telemetry.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
import platform
import subprocess
from typing import Any

if __package__:
    from . import (
        click_observer_backend,
        click_observer_common,
        click_observer_linux,
        click_observer_macos,
        click_observer_windows,
    )
else:  # Executed directly from the bundled hooks directory.
    import click_observer_backend
    import click_observer_common
    import click_observer_linux
    import click_observer_macos
    import click_observer_windows


SHADOW_STATE_VERSION = click_observer_common.SHADOW_STATE_VERSION
SHADOW_STATE_FIELD = click_observer_common.SHADOW_STATE_FIELD
MAX_SHADOW_STATE_RECORDS = click_observer_common.MAX_SHADOW_STATE_RECORDS
MAX_RAW_TRACE_BYTES = click_observer_linux.MAX_RAW_TRACE_BYTES
MAX_BACKEND_VERSION_BYTES = click_observer_linux.MAX_BACKEND_VERSION_BYTES
STRACE_STRING_LIMIT = click_observer_linux.STRACE_STRING_LIMIT
STRACE_TRACE_EXPRESSION = click_observer_linux.STRACE_TRACE_EXPRESSION

BackendCapability = click_observer_backend.BackendCapability
ObserverBackend = click_observer_backend.ObserverBackend
ShadowExecution = click_observer_common.ShadowExecution
ParsedTrace = click_observer_linux.ParsedTrace

FallbackExecutor = click_observer_common.FallbackExecutor
BackendResolver = click_observer_linux.BackendResolver
FileDigester = click_observer_linux.FileDigester
BackendProbe = click_observer_linux.BackendProbe
SpawnArgv = click_observer_linux.SpawnArgv
MacOSCollector = click_observer_macos.Collector
WindowsCollector = Callable[..., click_observer_windows.CollectedExecution]

select_backend = click_observer_backend.select_backend
platform_support = click_observer_backend.support_report
run_unobserved = click_observer_common.run_unobserved
combine_records = click_observer_common.combine_records
fresh_state = click_observer_common.fresh_state
state_is_valid = click_observer_common.state_is_valid
records_from_verification = click_observer_common.records_from_verification
store_records = click_observer_common.store_records
advisory = click_observer_common.advisory

# Keep the intentionally tested Linux compatibility helpers on the old facade.
probe_strace_version = click_observer_linux.probe_strace_version
parse_strace = click_observer_linux.parse_strace
_already_traced = click_observer_linux._already_traced
_file_digest = click_observer_linux._file_digest
parse_fs_usage = click_observer_macos.parse_fs_usage
probe_macos_version = click_observer_macos.probe_macos_version
macos_has_privilege = click_observer_macos.has_privilege
parse_windows_etw = click_observer_windows.parse_windows_etw
probe_windows_version = click_observer_windows.probe_windows_version
click_process = click_observer_linux.click_process


@click_process.termination_as_interrupt()
def run_command(argv: Sequence[str], *, capture_output: dict | None = None,
                capture_limit_bytes: int = 65536, capture_tee: bool = False, **kwargs) -> ShadowExecution:
    if capture_output is None:
        return _run_command(argv, **kwargs)
    output = click_process.OutputCapture(limit=capture_limit_bytes, tee=capture_tee)
    spawner = kwargs.pop("spawn_argv", click_process.spawn_argv)
    collector = kwargs.pop("macos_collector", click_observer_macos.collect_command)
    result = None
    try:
        result = _run_command(
            argv, **kwargs,
            spawn_argv=lambda argv, **options: output.spawn(spawner, argv, **options),
            macos_collector=lambda *args, **options: collector(*args, **options,
                spawn_suspended=lambda argv, **values: output.spawn(click_observer_macos._spawn_suspended_macos, argv, **values)),
        )
        return result
    finally:
        captured = output.finish(argv, result.exit_code if result else 130)
        if captured is not None:
            capture_output.update(status="complete", process=captured)


def _run_command(
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
    spawn_argv: SpawnArgv = click_observer_linux.click_process.spawn_argv,
    terminate_group: Callable[[subprocess.Popen[Any]], int] = (
        click_observer_linux.click_process.terminate_process_group
    ),
    system_name: str | None = None,
    already_traced: Callable[[], bool] = _already_traced,
    macos_privilege_probe: Callable[[], bool] = macos_has_privilege,
    macos_collector: MacOSCollector = click_observer_macos.collect_command,
    windows_collector: WindowsCollector = click_observer_windows.collect_command,
    capture_limit: int = MAX_RAW_TRACE_BYTES,
    process_observer: Callable[..., None] | None = None,
) -> ShadowExecution:
    """Select one backend and execute the target exactly once."""

    try:
        system = platform.system() if system_name is None else system_name
        macos_privileged = (
            bool(macos_privilege_probe()) if system == "Darwin" else None
        )
        capability = select_backend(
            system, macos_privileged=macos_privileged
        )
    except Exception:
        capability = BackendCapability(
            system="",
            backend_name=None,
            status="unavailable",
            reason="capability-detection-failed",
        )
    if capability.status != "available":
        return run_unobserved(
            execute_unobserved,
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
        )
    if capability.backend_name == "strace":
        return click_observer_linux.run_command(
            argv,
            workspace=workspace,
            observation_root=observation_root,
            environment=environment,
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
            execute_unobserved=execute_unobserved,
            resolve_backend=resolve_backend,
            digest_file=digest_file,
            probe_version=probe_version,
            spawn_argv=spawn_argv,
            terminate_group=terminate_group,
            system_name=system,
            already_traced=already_traced,
            capture_limit=capture_limit,
            process_observer=process_observer,
        )
    if capability.backend_name == "fs_usage":
        return click_observer_macos.run_command(
            argv,
            workspace=workspace,
            observation_root=observation_root,
            environment=environment,
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
            execute_unobserved=execute_unobserved,
            resolve_backend=resolve_backend,
            digest_file=digest_file,
            privilege_probe=macos_privilege_probe,
            collector=macos_collector,
            spawn_argv=spawn_argv,
            terminate_group=terminate_group,
            system_name=system,
            capture_limit=capture_limit,
        )
    if capability.backend_name == "windows-etw":
        return click_observer_windows.run_command(
            argv,
            workspace=workspace,
            observation_root=observation_root,
            environment=environment,
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
            execute_unobserved=execute_unobserved,
            resolve_backend=resolve_backend,
            digest_file=digest_file,
            collector=windows_collector,
            spawn_argv=spawn_argv,
            terminate_group=terminate_group,
            system_name=system,
            capture_limit=capture_limit,
            process_observer=process_observer,
        )
    return run_unobserved(
        execute_unobserved,
        evidence_key=evidence_key,
        check_digest=check_digest,
        mutation_revision=mutation_revision,
    )
