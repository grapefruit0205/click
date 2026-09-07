#!/usr/bin/env python3
"""Bounded, advisory verification diagnostics and local retained output.

This module never decides whether a check passed, whether evidence is reusable,
or whether a task is complete. It parses output from the original execution and
stores bounded local detail under the existing plugin data directory.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Any, Sequence


REPORTING_VERSION = 1
RESULT_VERSION = 1
ACTIONABLE_REPORT_VERSION = 1
STATE_FIELD = "verification_diagnostics"
REPORTING_FORMATS = frozenset({"raw", "actionable"})
DEFAULT_CAPTURE_BYTES = 64 * 1024
MIN_CAPTURE_BYTES = 4 * 1024
MAX_CAPTURE_BYTES = 128 * 1024
MAX_RECORDS = 32
MAX_STATE_BYTES = 512 * 1024
MAX_FAILURES_PER_COMMAND = 12
MAX_MESSAGE_CHARS = 480
MAX_TEST_ID_CHARS = 240
MAX_STACK_FRAMES = 8
LOG_DIRECTORY = "verification-diagnostics"
LOG_MAX_AGE_SECONDS = 24 * 60 * 60
LOG_MAX_FILES = 128
LOG_MAX_TOTAL_BYTES = 8 * 1024 * 1024
LOG_MAX_FILE_BYTES = 512 * 1024
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret|credential)"
    r"(\s*[:=]\s*)(?:['\"]?)[^\s,;]+"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}")
_UNITTEST_HEADER = re.compile(r"^(FAIL|ERROR):\s+(.+?)(?:\s+\(([^)]+)\))?$")
_TRACEBACK_FRAME = re.compile(
    r'^\s*File\s+["\'](?P<file>.*?)["\'],\s+line\s+(?P<line>[0-9]+)(?:,\s+in\s+(?P<func>.+))?$'
)
_PYTEST_FAILURE = re.compile(
    r"^FAILED\s+(?P<test>\S+?)(?:\s+-\s+(?P<message>.*))?$"
)
_PYTEST_FRAME = re.compile(
    r"^(?P<file>[^:\r\n]+[.]py):(?P<line>[0-9]+):(?:\s+in\s+(?P<func>.*))?$"
)
_SENSITIVE_PARTS = frozenset(
    {
        ".env", ".git", ".ssh", "credentials", "credential", "secrets",
        "secret", "private", "id_rsa", "id_ed25519", "tokens", "token",
    }
)


def default_reporting() -> dict[str, Any]:
    return {
        "version": REPORTING_VERSION,
        "format": "raw",
        "max_bytes": DEFAULT_CAPTURE_BYTES,
        "context": {
            "enabled": False,
            "max_files": 2,
            "max_lines": 120,
        },
    }


def validate_reporting(value: Any) -> tuple[dict[str, Any] | None, str]:
    """Validate the versioned presentation/capture policy.

    The absent policy preserves the established raw streaming behavior.
    """

    if value is None:
        return default_reporting(), ""
    if not isinstance(value, dict) or set(value) != {
        "version", "format", "max_bytes", "context"
    }:
        return None, (
            "Verification `reporting` must contain only version, format, "
            "max_bytes, and context."
        )
    if value.get("version") != REPORTING_VERSION:
        return None, f"Verification `reporting.version` must be {REPORTING_VERSION}."
    output_format = value.get("format")
    if output_format not in REPORTING_FORMATS:
        return None, "Verification `reporting.format` must be raw or actionable."
    max_bytes = value.get("max_bytes")
    if (
        not isinstance(max_bytes, int)
        or isinstance(max_bytes, bool)
        or not MIN_CAPTURE_BYTES <= max_bytes <= MAX_CAPTURE_BYTES
    ):
        return None, (
            f"Verification `reporting.max_bytes` must be {MIN_CAPTURE_BYTES}.."
            f"{MAX_CAPTURE_BYTES}."
        )
    context = value.get("context")
    if not isinstance(context, dict) or set(context) != {
        "enabled", "max_files", "max_lines"
    }:
        return None, (
            "Verification `reporting.context` must contain enabled, max_files, "
            "and max_lines."
        )
    if not isinstance(context.get("enabled"), bool):
        return None, "Verification `reporting.context.enabled` must be boolean."
    max_files = context.get("max_files")
    max_lines = context.get("max_lines")
    if (
        not isinstance(max_files, int)
        or isinstance(max_files, bool)
        or not 0 <= max_files <= 2
        or not isinstance(max_lines, int)
        or isinstance(max_lines, bool)
        or not 0 <= max_lines <= 120
    ):
        return None, (
            "Verification diagnostic context is limited to 2 files and 120 lines."
        )
    if context["enabled"] and (max_files == 0 or max_lines == 0):
        return None, "Enabled verification context requires nonzero file and line bounds."
    return json.loads(json.dumps(value)), ""


def _clean(value: Any, limit: int = MAX_MESSAGE_CHARS) -> str:
    text = _ANSI.sub("", str(value))
    text = _BEARER_VALUE.sub("Bearer <redacted>", text)
    text = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>", text
    )
    text = "".join(
        character
        for character in text
        if character in "\t " or ord(character) >= 32
    ).strip()
    if len(text) > limit:
        return text[: max(0, limit - 1)] + "…"
    return text


def _framework(argv: Sequence[str]) -> str:
    parts = [str(value) for value in argv]
    # Path.name follows the current host's path syntax. Normalizing both
    # separators keeps diagnostics deterministic when a Windows command is
    # inspected by another host, and removing .exe covers native Windows argv.
    lowered = [
        value.replace("\\", "/").rsplit("/", 1)[-1].lower()
        for value in parts
    ]
    executables = [
        value[:-4] if value.endswith(".exe") else value for value in lowered
    ]
    for index, value in enumerate(executables[:-1]):
        if value in {"python", "python3", "py", "pypy", "pypy3"} or value.startswith(
            "python3."
        ):
            module_index = index + 1
            if value == "py" and module_index < len(parts):
                launcher_selector = parts[module_index].lower()
                if re.fullmatch(r"-\d+(?:\.\d+)?(?:-\d+)?", launcher_selector):
                    module_index += 1
            module = [item.lower() for item in parts[module_index : module_index + 2]]
            if module == ["-m", "unittest"]:
                return "python-unittest"
            if module == ["-m", "pytest"]:
                return "pytest"
    if executables and executables[0] in {"pytest", "py.test"}:
        return "pytest"
    return "unsupported"


def _safe_file(value: str, workspace: Path) -> str:
    cleaned = _clean(value, 512)
    if not cleaned or "\x00" in cleaned:
        return ""
    candidate = Path(cleaned)
    try:
        root = workspace.resolve(strict=True)
        lexical = candidate if candidate.is_absolute() else root / candidate
        resolved = lexical.resolve(strict=False)
        relative = resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return ""
    lowered = {part.lower() for part in relative.parts}
    if lowered & _SENSITIVE_PARTS or any(
        part.lower().endswith((".pem", ".key", ".p12", ".pfx"))
        for part in relative.parts
    ):
        return ""
    try:
        if lexical.exists() and lexical.is_symlink():
            return ""
    except OSError:
        return ""
    return relative.as_posix()


def _frames(lines: list[str], workspace: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in lines:
        match = _TRACEBACK_FRAME.match(line) or _PYTEST_FRAME.match(line)
        if match is None:
            continue
        safe = _safe_file(match.group("file"), workspace)
        if not safe:
            continue
        frame = {"file": safe, "line": int(match.group("line"))}
        function = _clean(match.groupdict().get("func") or "", 120)
        if function:
            frame["function"] = function
        if frame not in result:
            result.append(frame)
        if len(result) >= MAX_STACK_FRAMES:
            break
    return result


def _error_message(lines: list[str]) -> tuple[str, str]:
    for line in reversed(lines):
        candidate = _clean(line)
        if not candidate or set(candidate) <= {"-", "=", "_"}:
            continue
        if candidate.startswith(("Traceback ", "File ", "Ran ", "FAILED ")):
            continue
        if ":" in candidate:
            kind, message = candidate.split(":", 1)
            kind = _clean(kind, 80)
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)?", kind):
                return kind, _clean(message) or kind
        return "AssertionError", candidate
    return "AssertionError", "failure details unavailable"


def _unittest_failures(text: str, workspace: Path) -> list[dict[str, Any]]:
    lines = text.splitlines()
    headers = [index for index, line in enumerate(lines) if _UNITTEST_HEADER.match(line)]
    failures: list[dict[str, Any]] = []
    for offset, index in enumerate(headers[:MAX_FAILURES_PER_COMMAND]):
        header = _UNITTEST_HEADER.match(lines[index])
        assert header is not None
        end = headers[offset + 1] if offset + 1 < len(headers) else len(lines)
        section = lines[index + 1 : end]
        frames = _frames(section, workspace)
        error_type, message = _error_message(section)
        status = header.group(1)
        test_id = _clean(header.group(3) or header.group(2), MAX_TEST_ID_CHARS)
        failure: dict[str, Any] = {
            "test_id": test_id or "unknown-test",
            "error_type": error_type if status == "FAIL" else status,
            "message": message,
            "failure_kind": "test-failure" if status == "FAIL" else "test-error",
            "stack": frames,
        }
        if frames:
            failure.update(file=frames[-1]["file"], line=frames[-1]["line"])
        else:
            failure.update(file="", line=None)
        failures.append(failure)
    return failures


def _pytest_failures(text: str, workspace: Path) -> list[dict[str, Any]]:
    lines = text.splitlines()
    frames = _frames(lines, workspace)
    failures: list[dict[str, Any]] = []
    for line in lines:
        match = _PYTEST_FAILURE.match(line)
        if match is None:
            continue
        message = _clean(match.group("message") or "pytest test failed")
        error_type = "AssertionError" if "assert" in message.lower() else "TestFailure"
        failure: dict[str, Any] = {
            "test_id": _clean(match.group("test"), MAX_TEST_ID_CHARS),
            "error_type": error_type,
            "message": message,
            "failure_kind": "test-failure",
            "stack": frames[-MAX_STACK_FRAMES:],
            "file": "",
            "line": None,
        }
        if frames:
            failure.update(file=frames[-1]["file"], line=frames[-1]["line"])
        failures.append(failure)
        if len(failures) >= MAX_FAILURES_PER_COMMAND:
            break
    return failures


def _capture_value(capture: Any, name: str, default: Any = None) -> Any:
    return getattr(capture, name, default)


def _stream_value(process: Any, name: str) -> tuple[bytes, int, bool, bool]:
    stream = getattr(process, name, None)
    data = _capture_value(stream, "data", b"")
    if not isinstance(data, bytes):
        data = b""
    total = _capture_value(stream, "total_bytes", len(data))
    if not isinstance(total, int) or isinstance(total, bool) or total < len(data):
        total = len(data)
    return (
        data,
        total,
        bool(_capture_value(stream, "truncated", False)),
        bool(_capture_value(stream, "reader_error", False)),
    )


def _diagnostic_root(state_path: Path) -> Path:
    return state_path.parent.parent / LOG_DIRECTORY


def _prune_logs(root: Path, *, keep: Path | None = None, now: int | None = None) -> None:
    current = int(time.time()) if now is None else now
    try:
        candidates = []
        for path in root.glob("*.json"):
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                continue
            if current - int(metadata.st_mtime) > LOG_MAX_AGE_SECONDS and path != keep:
                path.unlink(missing_ok=True)
                continue
            candidates.append((metadata.st_mtime, metadata.st_size, path))
    except OSError:
        return
    candidates.sort(key=lambda item: item[0], reverse=True)
    total = 0
    retained = 0
    for _, size, path in candidates:
        if path == keep:
            total += size
            retained += 1
            continue
        if retained >= LOG_MAX_FILES or total + size > LOG_MAX_TOTAL_BYTES:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        total += size
        retained += 1


def _store_capture(
    state_path: Path,
    process: Any,
    *,
    batch_ref: str,
    source_key: str,
    command_position: int,
    recorded_at: int,
) -> tuple[str | None, int, str]:
    stdout, stdout_total, stdout_truncated, stdout_error = _stream_value(
        process, "stdout"
    )
    stderr, stderr_total, stderr_truncated, stderr_error = _stream_value(
        process, "stderr"
    )
    payload = {
        "version": RESULT_VERSION,
        "recorded_at": recorded_at,
        "batch_ref": batch_ref,
        "source_ref": source_key,
        "command_position": command_position,
        "stdout": {
            "encoding": "base64",
            "data": base64.b64encode(stdout).decode("ascii"),
            "total_bytes": stdout_total,
            "truncated": stdout_truncated,
            "reader_error": stdout_error,
        },
        "stderr": {
            "encoding": "base64",
            "data": base64.b64encode(stderr).decode("ascii"),
            "total_bytes": stderr_total,
            "truncated": stderr_truncated,
            "reader_error": stderr_error,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > LOG_MAX_FILE_BYTES:
        return None, 0, "storage-limit"
    log_ref = hashlib.sha256(encoded).hexdigest()
    root = _diagnostic_root(state_path)
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
        resolved_root = root.resolve(strict=True)
        if root.is_symlink() or resolved_root != root.resolve(strict=False):
            return None, 0, "unsafe-storage-root"
        destination = resolved_root / f"{log_ref}.json"
        if not destination.exists():
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=resolved_root, prefix=".diagnostic-", delete=False
            ) as handle:
                handle.write(encoded)
                temporary = Path(handle.name)
            temporary.chmod(0o600)
            os.replace(temporary, destination)
        destination.chmod(0o600)
        _prune_logs(resolved_root, keep=destination, now=recorded_at)
        return log_ref, int(destination.stat().st_size), "stored"
    except OSError:
        return None, 0, "storage-unavailable"


def build_record(
    capture_box: Any,
    *,
    argv: Sequence[str],
    workspace: Path,
    state_path: Path,
    batch_ref: str,
    batch_id: str,
    task_ref: str,
    revision: int,
    evidence_id: str,
    source_key: str,
    command_position: int,
    check_digest: str,
    exit_code: int,
    reporting: dict[str, Any],
    generated_at: int | None = None,
) -> dict[str, Any]:
    started_ns = time.perf_counter_ns()
    now = max(1, int(time.time()) if generated_at is None else generated_at)
    process = (
        capture_box.get("process")
        if isinstance(capture_box, dict) and capture_box.get("status") == "complete"
        else None
    )
    framework = _framework(argv)
    stdout = stderr = b""
    stdout_total = stderr_total = 0
    truncated = reader_error = False
    log_ref: str | None = None
    storage_bytes = 0
    storage_status = "not-stored"
    if process is not None:
        stdout, stdout_total, stdout_truncated, stdout_error = _stream_value(
            process, "stdout"
        )
        stderr, stderr_total, stderr_truncated, stderr_error = _stream_value(
            process, "stderr"
        )
        truncated = stdout_truncated or stderr_truncated
        reader_error = stdout_error or stderr_error
        log_ref, storage_bytes, storage_status = _store_capture(
            state_path,
            process,
            batch_ref=batch_ref,
            source_key=source_key,
            command_position=command_position,
            recorded_at=now,
        )
    combined_bytes = stdout + (b"\n" if stdout and stderr else b"") + stderr
    decoded = combined_bytes.decode("utf-8", errors="replace")
    replacements = decoded.count("\ufffd")
    failures: list[dict[str, Any]] = []
    if framework == "python-unittest":
        failures = _unittest_failures(decoded, workspace)
    elif framework == "pytest":
        failures = _pytest_failures(decoded, workspace)
    if framework == "unsupported":
        parser_status = "unsupported"
    elif exit_code != 0 and not failures:
        parser_status = "unrecognized-failure-output"
    else:
        parser_status = "supported"
    capture_status = (
        "missing"
        if process is None
        else "partial"
        if truncated or reader_error
        else "complete"
    )
    context = reporting.get("context", {})
    inspect_suggestions = [
        {"file": failure["file"], "line": failure["line"]}
        for failure in failures
        if failure.get("file") and isinstance(failure.get("line"), int)
    ][: int(context.get("max_files", 0))]
    context_status = (
        "not-requested"
        if context.get("enabled") is not True
        else "separate-read-authority-required"
    )
    processing_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
    record: dict[str, Any] = {
        "version": RESULT_VERSION,
        "recorded_at": now,
        "task_ref": task_ref,
        "batch_ref": batch_ref,
        "batch_id": batch_id,
        "revision": revision,
        "source_id": evidence_id,
        "source_key": source_key,
        "command_position": command_position,
        "check_digest": check_digest,
        "exit_code": exit_code,
        "outcome": "passed" if exit_code == 0 else "failed",
        "parser": {
            "name": framework,
            "version": 1,
            "status": parser_status,
            "encoding_replacements": replacements,
        },
        "capture": {
            "status": capture_status,
            "stdout_bytes": stdout_total,
            "stderr_bytes": stderr_total,
            "truncated": truncated,
            "reader_error": reader_error,
        },
        "failures": failures,
        "failure_kind": (
            failures[0]["failure_kind"]
            if failures
            else "none"
            if exit_code == 0
            else "unknown"
        ),
        "log_ref": log_ref,
        "storage": {
            "status": storage_status,
            "bytes": storage_bytes,
            "ttl_seconds": LOG_MAX_AGE_SECONDS,
        },
        "context": {
            "status": context_status,
            "max_files": int(context.get("max_files", 0)),
            "max_lines": int(context.get("max_lines", 0)),
            "inspect_suggestions": inspect_suggestions,
        },
        "generation": {
            "processing_ms": round(processing_ms, 3),
            "input_bytes": len(combined_bytes),
            "output_bytes": 0,
            "storage_bytes": storage_bytes,
        },
    }
    record["generation"]["output_bytes"] = len(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    )
    return record


def _record_is_valid(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and value.get("version") == RESULT_VERSION
        and isinstance(value.get("recorded_at"), int)
        and isinstance(value.get("revision"), int)
        and isinstance(value.get("command_position"), int)
        and isinstance(value.get("batch_ref"), str)
        and _DIGEST.fullmatch(value["batch_ref"])
        and isinstance(value.get("source_key"), str)
        and _DIGEST.fullmatch(value["source_key"])
        and isinstance(value.get("check_digest"), str)
        and _DIGEST.fullmatch(value["check_digest"])
        and isinstance(value.get("failures"), list)
        and (
            value.get("log_ref") is None
            or isinstance(value.get("log_ref"), str)
            and _DIGEST.fullmatch(value["log_ref"])
        )
    )


def store_record(
    verification: dict[str, Any],
    record: dict[str, Any],
    reporting: dict[str, Any],
) -> bool:
    if not isinstance(verification, dict) or not _record_is_valid(record):
        return False
    validated_reporting, error = validate_reporting(reporting)
    if error or validated_reporting is None:
        return False
    existing = verification.get(STATE_FIELD)
    records = (
        existing.get("records", [])
        if isinstance(existing, dict) and existing.get("version") == RESULT_VERSION
        else []
    )
    records = [item for item in records if _record_is_valid(item)]
    key = (
        record["batch_ref"], record["source_key"], record["command_position"]
    )
    records = [
        item
        for item in records
        if (item["batch_ref"], item["source_key"], item["command_position"]) != key
    ]
    records.append(json.loads(json.dumps(record)))
    records = records[-MAX_RECORDS:]
    container = {
        "version": RESULT_VERSION,
        "reporting": validated_reporting,
        "records": records,
    }
    while records and len(
        json.dumps(container, sort_keys=True, separators=(",", ":")).encode()
    ) > MAX_STATE_BYTES:
        records.pop(0)
    verification[STATE_FIELD] = container
    return True


def latest_records(verification: Any, batch_ref: str) -> list[dict[str, Any]]:
    if not isinstance(verification, dict) or not _DIGEST.fullmatch(batch_ref or ""):
        return []
    container = verification.get(STATE_FIELD)
    if not isinstance(container, dict) or container.get("version") != RESULT_VERSION:
        return []
    return [
        json.loads(json.dumps(record))
        for record in container.get("records", [])
        if _record_is_valid(record) and record["batch_ref"] == batch_ref
    ]


def render_actionable(record: dict[str, Any]) -> str:
    """Render either a short success or bounded failure diagnosis."""

    source = _clean(record.get("source_id", "verification"), 48)
    if record.get("outcome") == "passed":
        return f"[Click diagnostic] {source} passed."
    failures = record.get("failures") if isinstance(record.get("failures"), list) else []
    if not failures:
        return (
            f"[Click diagnostic] {source} failed; structured details were unavailable. "
            f"local log ref {record.get('log_ref') or 'unavailable'}."
        )
    lines = [f"[Click diagnostic] {source} failed ({len(failures)} parsed failure(s))."]
    for failure in failures[:MAX_FAILURES_PER_COMMAND]:
        location = ""
        if failure.get("file") and isinstance(failure.get("line"), int):
            location = f" at {failure['file']}:{failure['line']}"
        lines.append(
            f"- {failure.get('test_id', 'unknown-test')}{location}: "
            f"{failure.get('error_type', 'Failure')}: {failure.get('message', '')}"
        )
    lines.append(f"Local retained log ref: {record.get('log_ref') or 'unavailable'}")
    return "\n".join(lines)


def _blockers(state: dict[str, Any], progress: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    summary = progress.get("summary", {})
    if int(summary.get("remaining_check_count", 0) or 0) > 0:
        result.append({"kind": "verification", "reason": "registered-checks-remaining"})
    if any(check.get("current_state") == "invalidated" for check in progress.get("checks", [])):
        result.append({"kind": "stale", "reason": "registered-check-invalidated"})
    external = state.get("external_evidence")
    if isinstance(external, dict) and external.get("browser_required") is True:
        status = str(external.get("status", "required"))
        if status not in {"passed", "complete", "observed"}:
            result.append({"kind": "browser", "reason": "browser-evidence-required"})
    service = state.get("service")
    if isinstance(service, dict):
        status = str(service.get("status", ""))
        if status in {"starting", "launching", "running", "stopping"}:
            result.append({"kind": "service", "reason": f"service-{status}"})
    for record in records:
        capture = record.get("capture", {})
        if capture.get("status") == "missing":
            result.append({"kind": "diagnostic", "reason": "captured-output-missing"})
        elif capture.get("truncated") is True:
            result.append({"kind": "diagnostic", "reason": "retained-output-truncated"})
    unique: list[dict[str, str]] = []
    for item in result:
        if item not in unique:
            unique.append(item)
    return unique[:16]


def enrich_progress(progress: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Attach a versioned read-only actionable report to progress facts."""

    if not isinstance(progress, dict):
        return progress
    verification = state.get("verification") if isinstance(state, dict) else None
    verification = verification if isinstance(verification, dict) else {}
    batch_ref = str(verification.get("last_batch_digest", ""))
    records = latest_records(verification, batch_ref)
    failures = [
        {
            "source_id": record.get("source_id", ""),
            "test_id": failure.get("test_id", ""),
            "error_type": failure.get("error_type", ""),
            "message": failure.get("message", ""),
            "file": failure.get("file", ""),
            "line": failure.get("line"),
            "stack": failure.get("stack", []),
            "failure_kind": failure.get("failure_kind", "unknown"),
            "log_ref": record.get("log_ref"),
        }
        for record in records
        for failure in record.get("failures", [])
    ][:32]
    summary = progress.get("summary", {})
    remaining = [
        {
            "source_id": check.get("id", ""),
            "state": check.get("current_state", "remaining"),
            "reason": check.get("reason_code", "not-verified"),
        }
        for check in progress.get("checks", [])
        if check.get("current_state") != "valid"
    ]
    blockers = _blockers(state, progress, records)
    latest_failure = failures[0] if failures else None
    if latest_failure is not None:
        next_action: dict[str, Any] = {
            "kind": "fix-observed-failure",
            "source_id": latest_failure["source_id"],
            "test_id": latest_failure["test_id"],
            "file": latest_failure["file"],
            "line": latest_failure["line"],
            "parent_request_ref": batch_ref or None,
            "local_log_ref": latest_failure["log_ref"],
        }
    elif remaining:
        next_action = {
            "kind": "complete-remaining-verification",
            "source_id": remaining[0]["source_id"],
            "parent_request_ref": batch_ref or None,
            "local_log_ref": None,
        }
    else:
        next_action = {
            "kind": "review-completion-conditions",
            "parent_request_ref": batch_ref or None,
            "local_log_ref": None,
        }
    reporting = default_reporting()
    container = verification.get(STATE_FIELD)
    if isinstance(container, dict):
        candidate, error = validate_reporting(container.get("reporting"))
        if not error and candidate is not None:
            reporting = candidate
    generation = {
        "processing_ms": round(
            sum(float(record.get("generation", {}).get("processing_ms", 0)) for record in records),
            3,
        ),
        "input_bytes": sum(
            int(record.get("generation", {}).get("input_bytes", 0)) for record in records
        ),
        "output_bytes": sum(
            int(record.get("generation", {}).get("output_bytes", 0)) for record in records
        ),
        "storage_bytes": sum(
            int(record.get("generation", {}).get("storage_bytes", 0)) for record in records
        ),
    }
    collection = verification.get("bounded_failure_collection")
    collection_projection: dict[str, Any] | None = None
    if (
        isinstance(collection, dict)
        and collection.get("version") == 1
        and collection.get("batch_ref") == batch_ref
    ):
        collection_projection = {
            key: collection.get(key)
            for key in (
                "version", "requested_mode", "status",
                "first_failure_source_id", "admitted_source_ids",
                "additional_sources_started", "additional_failures",
                "boundary_checks", "boundary_check_ms", "stop_reason",
            )
        }
    progress["actionable_report"] = {
        "version": ACTIONABLE_REPORT_VERSION,
        "task": {
            "task_ref": records[-1].get("task_ref", "") if records else "",
            "revision": progress.get("task", {}).get("mutation_revision", 0),
            "registered_checks_current": (
                int(summary.get("remaining_check_count", 0) or 0) == 0
                and int(summary.get("tracked_check_count", 0) or 0) > 0
            ),
            "whole_task_correctness": "not-established",
            "whole_task_reason": "verification-evidence-only",
        },
        "batch": {
            "request_ref": batch_ref or None,
            "batch_id": records[-1].get("batch_id", "") if records else "",
            "record_count": len(records),
            "executed_source_count": int(summary.get("actual_execution_count", 0) or 0),
            "reused_source_count": int(summary.get("reused_check_count", 0) or 0),
            "remaining_source_count": int(summary.get("remaining_check_count", 0) or 0),
        },
        "reporting": reporting,
        "failures": failures,
        "remaining_checks": remaining,
        "blockers": blockers,
        "next_action": next_action,
        "observable_follow_up": {
            "status": "unavailable",
            "reason": "host-tool-and-file-mutation-events-not-bound",
        },
        "failure_collection": collection_projection,
        "generation": generation,
        "authority": "advisory-only",
    }
    return progress


def read_local_log(plugin_data: Path, log_ref: str) -> dict[str, Any] | None:
    """Read one opaque local log reference without accepting an arbitrary path."""

    if not isinstance(log_ref, str) or _DIGEST.fullmatch(log_ref) is None:
        return None
    path = plugin_data / LOG_DIRECTORY / f"{log_ref}.json"
    try:
        root = (plugin_data / LOG_DIRECTORY).resolve(strict=True)
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
        if (
            path.is_symlink()
            or resolved.parent != root
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > LOG_MAX_FILE_BYTES
        ):
            return None
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) and value.get("version") == RESULT_VERSION else None
