#!/usr/bin/env python3
"""Native Windows ETW backend for Shadow Observer v1.

The adapter uses only Windows inbox tools: ``logman.exe`` controls two bounded
ETW sessions and ``tracerpt.exe`` converts their private ETL files to XML.  The
approved target starts exactly once after both sessions are live.  Collection
failure after that point can only downgrade telemetry; it never retries the
target.

Raw ETL and XML files live in a private temporary directory outside the
workspace and are removed before this module returns.  Only bounded XML bytes
cross the collector boundary, and the parser persists repository-relative
aggregates rather than raw events or external paths.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import ctypes
from dataclasses import dataclass
import hashlib
import ntpath
import os
from pathlib import Path
import platform
import re
import secrets
import subprocess
import tempfile
import time
from typing import Any
import xml.etree.ElementTree as ElementTree

if __package__:
    from . import click_dependency_cache, click_observer_common, click_process
else:  # Executed beside the bundled hook modules.
    import click_dependency_cache
    import click_observer_common
    import click_process


BACKEND_NAME = "windows-etw"
PROCESS_PROVIDER = "Microsoft-Windows-Kernel-Process"
FILE_PROVIDER = "Microsoft-Windows-Kernel-File"
PROCESS_PROVIDER_GUID = "22fb2cd6-0e7b-422b-a0c7-2fad1fd0e716"
FILE_PROVIDER_GUID = "edd08927-9cc4-4e65-b970-c2560fb5c289"
PROCESS_KEYWORDS = "0x10"
# Filename, FileIO, OpenD, Create, and Read.  Write/delete-only keywords are
# deliberately excluded because Observer records inputs, not generated output.
FILE_KEYWORDS = "0x1f0"
TRACE_LEVEL = "0xff"
MAX_ETL_MIB = 8
MAX_RAW_TRACE_BYTES = 16 * 1024 * 1024
MAX_TRANSIENT_INPUTS = click_dependency_cache.MAX_SHADOW_OBSERVER_INPUTS
MAX_XML_EVENTS = 200_000
CONTROL_TIMEOUT_SECONDS = 30.0
TARGET_WAIT_POLL_SECONDS = 0.1
# ``logman start -ets`` returns after admitting the session, but the kernel
# providers may still need a short interval before their first events are
# observable.  Without this barrier, a fast target can start and read its
# inputs before the process and file providers are ready.
SESSION_READY_DELAY_SECONDS = 0.25

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_INTEGER = re.compile(r"^(?:0[xX][0-9a-fA-F]+|[0-9][0-9,]*)$")
_DEVICE_PREFIX = re.compile(r"^(?:\\\\\?\\|\\\?\?\\)")

_PROCESS_NAMES = frozenset(
    {PROCESS_PROVIDER.lower(), PROCESS_PROVIDER_GUID}
)
_FILE_NAMES = frozenset({FILE_PROVIDER.lower(), FILE_PROVIDER_GUID})
_PATH_FIELDS = (
    "openpath",
    "filename",
    "filepath",
    "filepathname",
    "objectname",
    "path",
)
_FILE_KEY_FIELDS = ("filekey", "fileobject")
_PID_FIELDS = ("processid", "issuingprocessid", "targetprocessid")
_PARENT_PID_FIELDS = ("parentprocessid", "parentid")
_IMAGE_FIELDS = (
    "imagefilename",
    "imagepath",
    "imagename",
    "processname",
    "commandline",
)
_READ_EVENT_IDS = frozenset({12, 15})
_DIRECTORY_EVENT_IDS = frozenset({20, 25})
_METADATA_EVENT_IDS = frozenset({10, 22, 23, 32, 34})
_IGNORED_FILE_EVENT_IDS = frozenset({11, 13, 14, 16, 17, 19, 21, 24, 26, 27, 28, 29, 30})

FallbackExecutor = click_observer_common.FallbackExecutor
BackendResolver = Callable[..., tuple[str | None, str]]
FileDigester = Callable[[Path], str]
NativeBackendProbe = Callable[[str, str], bool]
SpawnArgv = Callable[..., subprocess.Popen[Any]]
TerminateGroup = Callable[[subprocess.Popen[Any]], int]
ControlRunner = Callable[..., subprocess.CompletedProcess[Any]]
DeviceMapProvider = Callable[[], Mapping[str, str]]
SessionWait = Callable[[float], None]
ShadowExecution = click_observer_common.ShadowExecution


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
    raw: tuple[bytes, ...]
    truncated: bool
    failed: bool
    target_started: bool
    root_pid: int | None
    command_duration_ms: int
    collector_overhead_ms: int
    process_scope_complete: bool = True


def _bounded_add(left: int, right: int) -> int:
    return click_observer_common.bounded_add(left, right)


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


def combined_backend_digest(logman_digest: str, tracerpt_digest: str) -> str:
    if _DIGEST.fullmatch(logman_digest) is None or _DIGEST.fullmatch(
        tracerpt_digest
    ) is None:
        return ""
    return hashlib.sha256(
        f"logman:{logman_digest}\ntracerpt:{tracerpt_digest}\n".encode("ascii")
    ).hexdigest()


_combined_digest = combined_backend_digest


def probe_windows_version() -> str:
    """Return a bounded native platform version for the collector identity."""

    try:
        version = platform.version()
    except Exception:
        return ""
    return version if isinstance(version, str) and _VERSION.fullmatch(version) else ""


def native_windows_tool(executable: str, expected_name: str) -> bool:
    """Accept only the named inbox executable under the Windows directory."""

    if not isinstance(executable, str) or not isinstance(expected_name, str):
        return False
    expected = expected_name.lower()
    if expected not in {"logman.exe", "tracerpt.exe"}:
        return False
    try:
        candidate = Path(executable).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    if candidate.name.lower() != expected or not candidate.is_file():
        return False
    windows_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not windows_root:
        return False
    try:
        root = Path(windows_root).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    allowed = {
        os.path.normcase(str(root / "System32" / expected_name)),
        os.path.normcase(str(root / "Sysnative" / expected_name)),
    }
    return os.path.normcase(str(candidate)) in allowed


_native_windows_tool = native_windows_tool


def windows_device_paths() -> Mapping[str, str]:
    """Map native ``\\Device`` volume prefixes to DOS drives when available."""

    if os.name != "nt":
        return {}
    try:
        kernel32 = ctypes.windll.kernel32
        length = int(kernel32.GetLogicalDriveStringsW(0, None))
        if length <= 0 or length > 32_768:
            return {}
        drive_buffer = ctypes.create_unicode_buffer(length + 1)
        if not kernel32.GetLogicalDriveStringsW(length, drive_buffer):
            return {}
        mappings: dict[str, str] = {}
        for drive in drive_buffer[:length].split("\x00"):
            if len(drive) < 2 or drive[1] != ":":
                continue
            target_buffer = ctypes.create_unicode_buffer(32_768)
            if not kernel32.QueryDosDeviceW(
                drive[:2], target_buffer, len(target_buffer)
            ):
                continue
            target = target_buffer.value
            if target:
                mappings[ntpath.normcase(ntpath.normpath(target))] = drive[:2]
        return mappings
    except (AttributeError, OSError, TypeError, ValueError):
        return {}


_windows_device_paths = windows_device_paths


def _canonical_windows_path(
    value: str, *, device_paths: Mapping[str, str]
) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("invalid Windows path")
    path = value.strip().strip('"')
    path = path.replace("/", "\\")
    path = _DEVICE_PREFIX.sub("", path)
    normalized = ntpath.normpath(path)
    normalized_case = ntpath.normcase(normalized)
    for device, drive in sorted(
        device_paths.items(), key=lambda item: len(item[0]), reverse=True
    ):
        device_normalized = ntpath.normcase(ntpath.normpath(device))
        if normalized_case == device_normalized:
            normalized = drive + "\\"
            break
        if normalized_case.startswith(device_normalized + "\\"):
            normalized = drive + normalized[len(device_normalized) :]
            break
    if not ntpath.isabs(normalized):
        raise ValueError("ETW path is not absolute")
    return ntpath.normpath(normalized)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _event_fields(event: ElementTree.Element) -> tuple[str, int | None, dict[str, str]]:
    provider = ""
    event_id: int | None = None
    fields: dict[str, str] = {}
    for element in event.iter():
        name = _local_name(element.tag)
        if name == "provider":
            for key in ("Name", "Guid"):
                value = element.attrib.get(key) or element.attrib.get(key.lower())
                if value and not provider:
                    provider = value.strip().strip("{}").lower()
        elif name == "eventid" and element.text:
            event_id = _parse_integer(element.text)
        elif name == "execution":
            for key, value in element.attrib.items():
                fields.setdefault(f"execution.{key.lower()}", value.strip())
        elif name == "data":
            key = element.attrib.get("Name") or element.attrib.get("name")
            if key and element.text:
                fields.setdefault(key.strip().lower(), element.text.strip())
        elif element.text and not list(element):
            text = element.text.strip()
            if text:
                fields.setdefault(name, text)
        for key, value in element.attrib.items():
            fields.setdefault(f"{name}.{key.lower()}", value.strip())
    return provider, event_id, fields


def _parse_integer(value: str | None) -> int | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if _INTEGER.fullmatch(candidate) is None:
        return None
    try:
        return int(candidate.replace(",", ""), 0)
    except ValueError:
        return None


def _first_integer(fields: Mapping[str, str], names: Sequence[str]) -> int | None:
    for name in names:
        parsed = _parse_integer(fields.get(name))
        if parsed is not None:
            return parsed
    return None


def _first_text(fields: Mapping[str, str], names: Sequence[str]) -> str:
    for name in names:
        value = fields.get(name)
        if isinstance(value, str) and value:
            return value
    return ""


def _event_pid(fields: Mapping[str, str]) -> int | None:
    return _first_integer(fields, (*_PID_FIELDS, "execution.processid", "pid"))


def _iter_events(raw_documents: Sequence[bytes]) -> tuple[list[ElementTree.Element], int]:
    events: list[ElementTree.Element] = []
    unresolved = 0
    for raw in raw_documents:
        if not isinstance(raw, bytes) or not raw:
            unresolved = _bounded_add(unresolved, 1)
            continue
        try:
            root = ElementTree.fromstring(raw)
        except (ElementTree.ParseError, ValueError):
            unresolved = _bounded_add(unresolved, 1)
            continue
        for element in root.iter():
            name = _local_name(element.tag)
            if name == "event":
                if len(events) >= MAX_XML_EVENTS:
                    unresolved = _bounded_add(unresolved, 1)
                    break
                events.append(element)
            elif name in {"eventslost", "bufferslost", "logbufferslost"}:
                lost = _parse_integer(element.text)
                if lost:
                    unresolved = _bounded_add(unresolved, lost)
    return events, unresolved


def parse_windows_etw(
    raw: Sequence[bytes] | bytes,
    *,
    workspace: Path | str,
    root_pid: int,
    truncated: bool = False,
    root_execution_bound: bool = False,
    process_scope_complete: bool = True,
    device_paths: Mapping[str, str] | None = None,
    transparent_child_images: Sequence[str] = (),
) -> ParsedTrace:
    """Normalize bounded ETW XML into content-free repository inputs."""

    documents = (raw,) if isinstance(raw, bytes) else tuple(raw)
    events, unresolved = _iter_events(documents)
    if truncated:
        unresolved = _bounded_add(unresolved, 1)
    if not isinstance(root_pid, int) or isinstance(root_pid, bool) or root_pid <= 0:
        unresolved = _bounded_add(unresolved, 1)
        root_pid = -1
    mappings = dict(device_paths or {})
    transparent_images: set[str] = set()
    for image in transparent_child_images:
        try:
            transparent_images.add(
                ntpath.normcase(
                    _canonical_windows_path(image, device_paths=mappings)
                )
            )
        except (TypeError, ValueError):
            unresolved = _bounded_add(unresolved, 1)
    root_text = str(workspace)
    try:
        root = _canonical_windows_path(root_text, device_paths=mappings)
    except ValueError:
        root = ntpath.normpath(root_text.replace("/", "\\"))
    root_case = ntpath.normcase(root).rstrip("\\")

    parent_by_pid: dict[int, int] = {}
    image_by_pid: dict[int, str] = {}
    process_start_pids: set[int] = set()
    file_events: list[tuple[int | None, int | None, dict[str, str], str]] = []
    file_keys: dict[str, str] = {}

    for event in events:
        provider, event_id, fields = _event_fields(event)
        if provider in _PROCESS_NAMES:
            if event_id != 1:
                continue
            pid = _first_integer(fields, _PID_FIELDS)
            parent = _first_integer(fields, _PARENT_PID_FIELDS)
            if pid is None or parent is None or pid <= 0 or parent < 0:
                unresolved = _bounded_add(unresolved, 1)
                continue
            parent_by_pid[pid] = parent
            image = _first_text(fields, _IMAGE_FIELDS)
            if image:
                try:
                    image_by_pid[pid] = ntpath.normcase(
                        _canonical_windows_path(image, device_paths=mappings)
                    )
                except ValueError:
                    pass
            process_start_pids.add(pid)
        elif provider in _FILE_NAMES:
            pid = _event_pid(fields)
            path = _first_text(fields, _PATH_FIELDS)
            key = _first_text(fields, _FILE_KEY_FIELDS).lower()
            if event_id == 10 and path and key:
                file_keys[key] = path
            if not path and key:
                path = file_keys.get(key, "")
            file_events.append((pid, event_id, fields, path))

    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parent_by_pid.items():
            if parent in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    child_process_count = sum(
        pid != root_pid and image_by_pid.get(pid) not in transparent_images
        for pid in descendants
    )
    process_root_observed = root_pid in process_start_pids
    root_exec_observed = bool(root_execution_bound or process_root_observed)

    inputs: dict[str, dict[str, Any]] = {}
    absolute_inputs: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    absolute_conflicts: set[str] = set()
    external_digests: set[str] = set()

    def add_path(path_text: str, *, kind: str, operation: str) -> None:
        nonlocal unresolved
        try:
            normalized = _canonical_windows_path(
                path_text, device_paths=mappings
            )
        except ValueError:
            unresolved = _bounded_add(unresolved, 1)
            return
        normalized_case = ntpath.normcase(normalized)
        absolute = absolute_inputs.get(normalized_case)
        if absolute is not None and absolute["kind"] != kind:
            if {absolute["kind"], kind} <= {"file", "directory"}:
                absolute["kind"] = "directory"
            else:
                absolute_conflicts.add(normalized_case)
        elif absolute is None:
            if len(absolute_inputs) >= MAX_TRANSIENT_INPUTS:
                unresolved = _bounded_add(unresolved, 1)
            else:
                absolute = {
                    "path": normalized,
                    "kind": kind,
                    "operations": [],
                }
                absolute_inputs[normalized_case] = absolute
        if absolute is not None and operation not in absolute["operations"]:
            absolute["operations"].append(operation)
        prefix = root_case + "\\"
        if normalized_case == root_case:
            unresolved = _bounded_add(unresolved, 1)
            return
        if not normalized_case.startswith(prefix):
            try:
                digest = hashlib.sha256(normalized_case.encode("utf-8")).hexdigest()
            except UnicodeEncodeError:
                unresolved = _bounded_add(unresolved, 1)
                return
            if digest not in external_digests:
                if len(external_digests) >= click_dependency_cache.MAX_SHADOW_OBSERVER_INPUTS:
                    unresolved = _bounded_add(unresolved, 1)
                else:
                    external_digests.add(digest)
            return
        relative = normalized[len(root.rstrip("\\")) + 1 :].replace("\\", "/")
        if not relative or relative in {".", ".."} or relative.startswith("../"):
            unresolved = _bounded_add(unresolved, 1)
            return
        relative_key = relative.rstrip("/")
        try:
            encoded = relative.encode("utf-8")
        except UnicodeEncodeError:
            unresolved = _bounded_add(unresolved, 1)
            return
        if (
            len(encoded) > click_dependency_cache.MAX_SHADOW_OBSERVER_PATH_BYTES
            or "\\" in relative
            or any(ord(character) < 32 or ord(character) == 127 for character in relative)
        ):
            unresolved = _bounded_add(unresolved, 1)
            return
        existing = inputs.get(relative_key)
        if existing is not None and existing["kind"] != kind:
            if {existing["kind"], kind} <= {"file", "directory"}:
                existing["kind"] = "directory"
                existing["path"] = relative_key + "/"
            else:
                conflicts.add(relative_key)
                return
        if existing is None:
            if len(inputs) >= click_dependency_cache.MAX_SHADOW_OBSERVER_INPUTS:
                unresolved = _bounded_add(unresolved, 1)
                return
            existing = {
                "path": relative_key + "/" if kind == "directory" else relative_key,
                "kind": kind,
                "operations": [],
            }
            inputs[relative_key] = existing
        if operation not in existing["operations"]:
            existing["operations"].append(operation)

    for pid, event_id, fields, bound_path in file_events:
        if pid is None:
            if bound_path:
                try:
                    candidate = _canonical_windows_path(
                        bound_path, device_paths=mappings
                    )
                except ValueError:
                    pass
                else:
                    candidate_case = ntpath.normcase(candidate)
                    if candidate_case.startswith(root_case + "\\"):
                        unresolved = _bounded_add(unresolved, 1)
            continue
        if pid not in descendants:
            continue
        path = bound_path or _first_text(fields, _PATH_FIELDS)
        if not path:
            key = _first_text(fields, _FILE_KEY_FIELDS).lower()
            path = file_keys.get(key, "")
        if event_id in _DIRECTORY_EVENT_IDS:
            operation, kind = "enumerate", "directory"
        elif event_id in _READ_EVENT_IDS:
            operation, kind = "read", "file"
        elif event_id in _METADATA_EVENT_IDS:
            operation, kind = "metadata", "file"
        elif event_id in _IGNORED_FILE_EVENT_IDS:
            continue
        else:
            unresolved = _bounded_add(unresolved, 1)
            continue
        if not path:
            unresolved = _bounded_add(unresolved, 1)
            continue
        add_path(path, kind=kind, operation=operation)

    for relative in conflicts:
        inputs.pop(relative, None)
        unresolved = _bounded_add(unresolved, 1)
    for absolute in absolute_conflicts:
        absolute_inputs.pop(absolute, None)
        unresolved = _bounded_add(unresolved, 1)
    if not root_exec_observed:
        unresolved = _bounded_add(unresolved, 1)
    if root_execution_bound and not process_root_observed:
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
        and process_root_observed
        and unresolved == 0
        and not truncated
        and process_scope_complete
    )
    return ParsedTrace(
        inputs=normalized_inputs,
        external_input_count=len(external_digests),
        unresolved_event_count=unresolved,
        child_process_count=child_process_count,
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


def _control(
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    runner: ControlRunner,
) -> subprocess.CompletedProcess[Any]:
    return runner(
        list(argv),
        cwd=cwd,
        env=dict(environment),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=CONTROL_TIMEOUT_SECONDS,
    )


def _trace_path(directory: Path, stem: str) -> Path | None:
    candidates = sorted(
        directory.glob(f"{stem}*.etl"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )
    return candidates[-1] if candidates else None


def _read_bounded(path: Path, limit: int) -> tuple[bytes, bool]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        return b"", True
    return raw[:limit], bool(size > limit or len(raw) > limit)


def _wait_for_target(target: subprocess.Popen[Any]) -> int:
    """Wait in bounded intervals so Windows can deliver KeyboardInterrupt."""
    while True:
        try:
            return int(target.wait(timeout=TARGET_WAIT_POLL_SECONDS))
        except subprocess.TimeoutExpired:
            continue


def collect_command(
    argv: Sequence[str],
    *,
    workspace: Path,
    environment: Mapping[str, str],
    logman_executable: str,
    tracerpt_executable: str,
    run_control: ControlRunner = click_process.run_argv,
    spawn_argv: SpawnArgv = click_process.spawn_argv,
    terminate_group: TerminateGroup = click_process.terminate_process_group,
    wait_for_sessions: SessionWait = time.sleep,
    capture_limit: int = MAX_RAW_TRACE_BYTES,
) -> CollectedExecution:
    """Collect process and file ETW while executing the target at most once."""

    started = time.monotonic()
    bounded_limit = (
        capture_limit
        if isinstance(capture_limit, int)
        and not isinstance(capture_limit, bool)
        and capture_limit > 0
        else MAX_RAW_TRACE_BYTES
    )
    target: subprocess.Popen[Any] | None = None
    target_started = False
    root_pid: int | None = None
    exit_code = 127
    failed = False
    truncated = False
    raw_documents: list[bytes] = []
    sessions: list[str] = []
    preparation_ms = 0
    cleanup_ms = 0
    nonce = secrets.token_hex(8)
    try:
        with tempfile.TemporaryDirectory(prefix="click-shadow-windows-") as temporary:
            trace_root = Path(temporary).resolve()
            definitions = (
                (f"ClickShadowProcess-{os.getpid()}-{nonce}", "process", PROCESS_PROVIDER, PROCESS_KEYWORDS),
                (f"ClickShadowFile-{os.getpid()}-{nonce}", "file", FILE_PROVIDER, FILE_KEYWORDS),
            )
            preparation_started = time.monotonic()
            try:
                for session, stem, provider, keywords in definitions:
                    result = _control(
                        [
                            logman_executable,
                            "start",
                            session,
                            "-ets",
                            "-o",
                            str(trace_root / f"{stem}.etl"),
                            "-f",
                            "bincirc",
                            "-max",
                            str(MAX_ETL_MIB),
                            "-nb",
                            "16",
                            "64",
                            "-bs",
                            "64",
                            "-p",
                            provider,
                            keywords,
                            TRACE_LEVEL,
                        ],
                        cwd=trace_root,
                        environment=environment,
                        runner=run_control,
                    )
                    if int(result.returncode) != 0:
                        failed = True
                        break
                    sessions.append(session)
                preparation_ms = max(
                    0, int((time.monotonic() - preparation_started) * 1000)
                )
                if not failed:
                    wait_for_sessions(SESSION_READY_DELAY_SECONDS)
                    preparation_ms = max(
                        0, int((time.monotonic() - preparation_started) * 1000)
                    )
                    target = spawn_argv(
                        list(argv), cwd=workspace, env=dict(environment)
                    )
                    root_pid = int(target.pid)
                    target_started = True
                    click_process.target_started()
                    exit_code = _wait_for_target(target)
            except KeyboardInterrupt:
                failed = True
                exit_code = 130
            except (
                OSError,
                RuntimeError,
                subprocess.SubprocessError,
                TypeError,
                ValueError,
            ):
                failed = True
            finally:
                if not target_started:
                    preparation_ms = max(
                        preparation_ms,
                        max(
                            0,
                            int((time.monotonic() - preparation_started) * 1000),
                        ),
                    )
                cleanup_started = time.monotonic()
                if target is not None and target.poll() is None:
                    try:
                        terminate_group(target)
                    except Exception:
                        pass
                    failed = True
                for session in reversed(sessions):
                    try:
                        stopped = _control(
                            [logman_executable, "stop", "-ets", session],
                            cwd=trace_root,
                            environment=environment,
                            runner=run_control,
                        )
                        if int(stopped.returncode) != 0:
                            failed = True
                    except KeyboardInterrupt:
                        failed = True
                        exit_code = 130
                    except Exception:
                        failed = True
                cleanup_ms = max(
                    0, int((time.monotonic() - cleanup_started) * 1000)
                )
            if target_started:
                for _session, stem, _provider, _keywords in definitions:
                    etl = _trace_path(trace_root, stem)
                    if etl is None:
                        failed = True
                        continue
                    xml_path = trace_root / f"{stem}.xml"
                    try:
                        converted = _control(
                            [
                                tracerpt_executable,
                                str(etl),
                                "-o",
                                str(xml_path),
                                "-of",
                                "XML",
                                "-lr",
                                "-y",
                            ],
                            cwd=trace_root,
                            environment=environment,
                            runner=run_control,
                        )
                    except Exception:
                        failed = True
                        continue
                    if int(converted.returncode) != 0:
                        failed = True
                        continue
                    raw, was_truncated = _read_bounded(xml_path, bounded_limit)
                    if not raw:
                        failed = True
                    raw_documents.append(raw)
                    truncated = bool(truncated or was_truncated)
    except KeyboardInterrupt:
        failed = True
        exit_code = 130
        if target is not None and target.poll() is None:
            terminate_group(target)
    except (OSError, RuntimeError, subprocess.SubprocessError, TypeError, ValueError):
        failed = True
        if target_started and target is not None:
            try:
                if target.poll() is None:
                    exit_code = int(target.wait())
            except (OSError, subprocess.SubprocessError, TypeError, ValueError):
                exit_code = 127
    duration_ms = (
        max(0, int((time.monotonic() - started) * 1000)) if target_started else 0
    )
    overhead_ms = min(
        _bounded_add(preparation_ms, cleanup_ms), duration_ms
    )
    process_scope_complete = bool(
        target_started
        and not failed
        and not truncated
        and len(raw_documents) == 2
    )
    return CollectedExecution(
        exit_code=exit_code,
        raw=tuple(raw_documents),
        truncated=truncated,
        failed=failed,
        target_started=target_started,
        root_pid=root_pid,
        command_duration_ms=duration_ms,
        collector_overhead_ms=overhead_ms,
        process_scope_complete=process_scope_complete,
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
    native_backend_probe: NativeBackendProbe = native_windows_tool,
    system_version: Callable[[], str] = probe_windows_version,
    device_map_provider: DeviceMapProvider = windows_device_paths,
    collector: Callable[..., CollectedExecution] = collect_command,
    run_control: ControlRunner = click_process.run_argv,
    spawn_argv: SpawnArgv = click_process.spawn_argv,
    terminate_group: TerminateGroup = click_process.terminate_process_group,
    system_name: str | None = None,
    capture_limit: int = MAX_RAW_TRACE_BYTES,
) -> ShadowExecution:
    """Execute one target and attach best-effort native Windows telemetry."""

    try:
        system = platform.system() if system_name is None else system_name
    except Exception:
        system = ""
    if system != "Windows":
        return click_observer_common.run_unobserved(
            execute_unobserved,
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
        )

    preparation_started = time.monotonic()
    resolved: dict[str, str] = {}
    for name in ("logman", "tracerpt"):
        try:
            executable, error = resolve_backend(name, workspace=workspace)
        except Exception:
            executable, error = None, "backend resolution failed"
        if (
            error
            or not isinstance(executable, str)
            or not executable
            or not native_backend_probe(executable, f"{name}.exe")
        ):
            preparation_ms = max(
                0, int((time.monotonic() - preparation_started) * 1000)
            )
            return click_observer_common.fallback_execution(
                execute_unobserved,
                evidence_key=evidence_key,
                check_digest=check_digest,
                mutation_revision=mutation_revision,
                status="unavailable",
                preparation_ms=preparation_ms,
            )
        resolved[name] = executable
    try:
        logman_digest = digest_file(Path(resolved["logman"]))
        tracerpt_digest = digest_file(Path(resolved["tracerpt"]))
        digest = combined_backend_digest(logman_digest, tracerpt_digest)
        version = system_version()
    except Exception:
        logman_digest = tracerpt_digest = digest = version = ""
    if _DIGEST.fullmatch(digest) is None or _VERSION.fullmatch(version) is None:
        preparation_ms = max(
            0, int((time.monotonic() - preparation_started) * 1000)
        )
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
            logman_executable=resolved["logman"],
            tracerpt_executable=resolved["tracerpt"],
            run_control=run_control,
            spawn_argv=spawn_argv,
            terminate_group=terminate_group,
            capture_limit=capture_limit,
        )
    except Exception:
        collected = CollectedExecution(
            127, (), False, True, False, None, 0, 0, False
        )
    if not collected.target_started:
        return click_observer_common.fallback_execution(
            execute_unobserved,
            evidence_key=evidence_key,
            check_digest=check_digest,
            mutation_revision=mutation_revision,
            status="failed",
            backend_name=BACKEND_NAME,
            backend_version=version,
            backend_digest=digest,
            preparation_ms=_bounded_add(
                preparation_ms, collected.collector_overhead_ms
            ),
        )

    parsing_started = time.monotonic()
    raw_documents = collected.raw
    try:
        mappings = device_map_provider()
        parsed = parse_windows_etw(
            raw_documents,
            workspace=observation_root or workspace,
            root_pid=int(collected.root_pid or -1),
            truncated=collected.truncated or collected.failed,
            root_execution_bound=collected.target_started,
            process_scope_complete=collected.process_scope_complete,
            device_paths=mappings,
        )
    except Exception:
        parsed = ParsedTrace((), 0, 1, 0, False, False)
        collected = CollectedExecution(
            collected.exit_code,
            (),
            collected.truncated,
            True,
            True,
            collected.root_pid,
            collected.command_duration_ms,
            collected.collector_overhead_ms,
            False,
        )
    finally:
        raw_documents = ()
    parsing_ms = max(0, int((time.monotonic() - parsing_started) * 1000))
    observer_overhead_ms = _bounded_add(
        preparation_ms,
        _bounded_add(collected.collector_overhead_ms, parsing_ms),
    )
    identity_started = time.monotonic()
    try:
        final_digest = combined_backend_digest(
            digest_file(Path(resolved["logman"])),
            digest_file(Path(resolved["tracerpt"])),
        )
    except Exception:
        final_digest = ""
    observer_overhead_ms = _bounded_add(
        observer_overhead_ms,
        max(0, int((time.monotonic() - identity_started) * 1000)),
    )
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
            backend_name=BACKEND_NAME,
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


__all__ = [
    "BACKEND_NAME",
    "CollectedExecution",
    "ParsedTrace",
    "combined_backend_digest",
    "collect_command",
    "native_windows_tool",
    "parse_windows_etw",
    "probe_windows_version",
    "run_command",
    "windows_device_paths",
]
