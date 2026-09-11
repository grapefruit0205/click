#!/usr/bin/env python3
"""Bounded, content-bound cache for a narrow set of local inspections.

Cached output is heuristic workflow data, never verification evidence.  Every
lookup recomputes the request, executable, environment, cwd, and input
fingerprints.  Unsupported or ambiguous requests return ``None`` so the normal
read-only runner executes without depending on this module.
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
from typing import Any

if __package__:
    from . import click_capability, click_inspection, click_state
else:  # Executed beside the bundled hook modules.
    import click_capability
    import click_inspection
    import click_state


CACHE_VERSION = 1
CACHE_DIR_NAME = "observation-cache-v1"
CACHE_TTL_SECONDS = 24 * 60 * 60
MAX_CACHE_ENTRIES = 128
MAX_CACHE_BYTES = 8 * 1024 * 1024
MAX_RESULT_BYTES = 48_000
MAX_INPUT_FILES = 4_096
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOOL_BYTES = 64 * 1024 * 1024
MAX_SOURCE_REFS = 8

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE_REF = re.compile(r"^[^\x00-\x1f\x7f]{1,240}$")
_ENVIRONMENT_KEYS = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NO_COLOR",
    "TERM",
    "COLORTERM",
)
_RG_VALUE_OPTIONS = {
    "-g",
    "--glob",
    "--iglob",
    "--max-depth",
    "--path-separator",
    "--sort",
    "--sortr",
    "-t",
    "--type",
    "-T",
    "--type-not",
    "-A",
    "--after-context",
    "-B",
    "--before-context",
    "-C",
    "--context",
    "-m",
    "--max-count",
    "--max-filesize",
    "--encoding",
    "--engine",
    "--color",
    "--colors",
    "--replace",
    "--threads",
}
_RG_FLAGS = {
    "-0",
    "--null",
    "--null-data",
    "-a",
    "--text",
    "--binary",
    "--byte-offset",
    "--column",
    "-c",
    "--count",
    "--count-matches",
    "--crlf",
    "-F",
    "--fixed-strings",
    "-H",
    "--with-filename",
    "-h",
    "--no-filename",
    "--heading",
    "--no-heading",
    "-i",
    "--ignore-case",
    "--json",
    "-l",
    "--files-with-matches",
    "--files-without-match",
    "--line-buffered",
    "-n",
    "--line-number",
    "-N",
    "--no-line-number",
    "--multiline",
    "-U",
    "--multiline-dotall",
    "--one-file-system",
    "-P",
    "--pcre2",
    "--no-pcre2-unicode",
    "-s",
    "--case-sensitive",
    "-S",
    "--smart-case",
    "--trim",
    "-v",
    "--invert-match",
    "-w",
    "--word-regexp",
    "-x",
    "--line-regexp",
}
_RG_DIRECTORY_UNSAFE_FLAGS = {
    "-L",
    "--follow",
    "-u",
    "-uu",
    "-uuu",
    "--hidden",
    "--no-ignore",
    "--no-ignore-dot",
    "--no-ignore-global",
    "--no-ignore-parent",
    "--no-ignore-vcs",
}
_IGNORE_FILE_NAMES = (".gitignore", ".ignore", ".rgignore")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _workspace(value: Path) -> Path | None:
    try:
        resolved = value.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return resolved if resolved.is_dir() else None


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_target(workspace: Path, raw: str) -> Path | None:
    if not isinstance(raw, str) or not raw or "\x00" in raw or raw == "-":
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    lexical = Path(os.path.abspath(candidate))
    try:
        resolved = lexical.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if resolved != lexical or not _within(resolved, workspace):
        return None
    return resolved


def _file_digest(path: Path, *, maximum: int) -> tuple[str, int] | None:
    try:
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            return None
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > maximum:
                    return None
                digest.update(block)
        after = path.stat()
    except OSError:
        return None
    identity = (
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
    )
    if identity != (
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
    ):
        return None
    return digest.hexdigest(), total


def _source_ref(path: Path, workspace: Path, *, directory: bool) -> str | None:
    try:
        rendered = path.relative_to(workspace).as_posix()
    except ValueError:
        return None
    if directory:
        rendered = rendered.rstrip("/") + "/"
    return rendered if _SAFE_REF.fullmatch(rendered) else None


def _snapshot_targets(
    workspace: Path, targets: list[Path]
) -> tuple[list[list[Any]], list[str], int] | None:
    records: list[list[Any]] = []
    refs: list[str] = []
    total_bytes = 0
    seen: set[Path] = set()
    pending = list(targets)
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        if len(seen) > MAX_INPUT_FILES:
            return None
        try:
            value = path.lstat()
        except OSError:
            return None
        relative = path.relative_to(workspace).as_posix()
        mode = stat.S_IMODE(value.st_mode)
        if stat.S_ISREG(value.st_mode):
            result = _file_digest(path, maximum=MAX_FILE_BYTES)
            if result is None:
                return None
            content_digest, size = result
            total_bytes += size
            if total_bytes > MAX_INPUT_BYTES:
                return None
            records.append([relative, "file", mode, size, content_digest])
        elif stat.S_ISDIR(value.st_mode):
            records.append([relative, "directory", mode, 0, ""])
            try:
                children = sorted(path.iterdir(), key=lambda item: item.name)
            except OSError:
                return None
            for child in reversed(children):
                if child.name == ".git" and child.is_dir():
                    continue
                if not _within(child, workspace):
                    return None
                pending.append(child)
        elif stat.S_ISLNK(value.st_mode):
            try:
                target = os.readlink(path)
            except OSError:
                return None
            records.append([relative, "symlink", mode, len(target), hashlib.sha256(target.encode()).hexdigest()])
        else:
            return None
    for target in targets:
        reference = _source_ref(target, workspace, directory=target.is_dir())
        if reference is None:
            return None
        refs.append(reference)
    return sorted(records), refs[:MAX_SOURCE_REFS], total_bytes


def _ignore_context_records(
    workspace: Path, targets: list[Path]
) -> tuple[list[list[Any]], int] | None:
    candidates: set[Path] = set()
    for target in targets:
        if not target.is_dir():
            continue
        current = target
        while _within(current, workspace):
            candidates.update(current / name for name in _IGNORE_FILE_NAMES)
            if current == workspace:
                break
            current = current.parent
    candidates.add(workspace / ".git" / "info" / "exclude")
    records: list[list[Any]] = []
    total_bytes = 0
    for path in sorted(candidates):
        relative = path.relative_to(workspace).as_posix()
        try:
            value = path.lstat()
        except FileNotFoundError:
            records.append([relative, "missing", 0, 0, ""])
            continue
        except OSError:
            return None
        if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
            return None
        result = _file_digest(path, maximum=1024 * 1024)
        if result is None:
            return None
        digest, size = result
        total_bytes += size
        records.append(
            [relative, "ignore-file", stat.S_IMODE(value.st_mode), size, digest]
        )
    return records, total_bytes


def _cat_targets(argv: list[str]) -> list[str] | None:
    arguments = argv[1:]
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    if not arguments or any(value == "-" or value.startswith("-") for value in arguments):
        return None
    return arguments


def _sed_targets(argv: list[str]) -> list[str] | None:
    if not click_inspection.is_read_only_sed(argv):
        return None
    index = 1
    script_found = False
    while index < len(argv) and not script_found:
        value = argv[index]
        if value in {"-n", "--quiet", "--silent"}:
            index += 1
            continue
        if value in {"-e", "--expression"}:
            index += 2
            script_found = True
            break
        if value.startswith("-e") and len(value) > 2:
            index += 1
            script_found = True
            break
        script_found = True
        index += 1
    if index < len(argv) and argv[index] == "--":
        index += 1
    targets = argv[index:]
    return targets if targets else None


def _rg_targets(argv: list[str]) -> tuple[list[str], set[str]] | None:
    positional: list[str] = []
    flags: set[str] = set()
    explicit_pattern = False
    index = 1
    while index < len(argv):
        value = argv[index]
        if value == "--":
            positional.extend(argv[index + 1 :])
            break
        if value in {"-e", "--regexp"}:
            if index + 1 >= len(argv):
                return None
            explicit_pattern = True
            index += 2
            continue
        if value.startswith("--regexp="):
            explicit_pattern = True
            index += 1
            continue
        if value in {"-f", "--file", "--ignore-file"} or value.startswith(
            ("--file=", "--ignore-file=")
        ):
            return None
        if value in _RG_VALUE_OPTIONS:
            if index + 1 >= len(argv):
                return None
            index += 2
            continue
        matched_value_option = next(
            (
                option
                for option in _RG_VALUE_OPTIONS
                if option.startswith("--") and value.startswith(option + "=")
            ),
            None,
        )
        if matched_value_option is not None:
            index += 1
            continue
        if value in _RG_FLAGS or value in _RG_DIRECTORY_UNSAFE_FLAGS:
            flags.add(value)
            index += 1
            continue
        if value.startswith("-"):
            return None
        positional.append(value)
        index += 1
    targets = positional if explicit_pattern else positional[1:]
    if not explicit_pattern and not positional:
        return None
    if not targets or any(value == "-" for value in targets):
        return None
    return targets, flags


def _request_targets(request: dict[str, Any]) -> tuple[str, list[str], set[str]] | None:
    commands = request.get("commands")
    if not isinstance(commands, list) or len(commands) != 1:
        return None
    argv = commands[0]
    if not isinstance(argv, list) or not argv or any(not isinstance(item, str) for item in argv):
        return None
    executable = Path(argv[0]).name.lower()
    if executable == "cat":
        targets = _cat_targets(argv)
        return ("cat", targets, set()) if targets is not None else None
    if executable == "sed":
        targets = _sed_targets(argv)
        return ("sed", targets, set()) if targets is not None else None
    if executable == "rg":
        parsed = _rg_targets(argv)
        return ("rg", parsed[0], parsed[1]) if parsed is not None else None
    return None


def _tool_fingerprint(executable: str, workspace: Path) -> dict[str, Any] | None:
    resolved, error = click_inspection.resolve_read_only_executable(
        executable, workspace=workspace
    )
    if error or resolved is None:
        return None
    path = Path(resolved)
    result = _file_digest(path, maximum=MAX_TOOL_BYTES)
    if result is None:
        return None
    digest, size = result
    return {
        "path_digest": hashlib.sha256(os.path.normcase(str(path)).encode()).hexdigest(),
        "content_digest": digest,
        "size": size,
    }


def _environment_fingerprint() -> dict[str, Any]:
    values = {
        key: hashlib.sha256(os.environ.get(key, "").encode()).hexdigest()
        for key in _ENVIRONMENT_KEYS
    }
    return {"values": values}


def describe(request: dict[str, Any], workspace: Path) -> dict[str, Any] | None:
    """Describe one cacheable request without storing its query or contents."""
    root = _workspace(workspace)
    parsed = _request_targets(request)
    if root is None or parsed is None:
        return None
    kind, raw_targets, flags = parsed
    targets: list[Path] = []
    for raw in raw_targets:
        target = _resolve_target(root, raw)
        if target is None:
            return None
        targets.append(target)
    if kind in {"cat", "sed"} and any(not target.is_file() for target in targets):
        return None
    if kind == "rg" and any(target.is_dir() for target in targets):
        if flags & _RG_DIRECTORY_UNSAFE_FLAGS:
            return None
    snapshot = _snapshot_targets(root, targets)
    tool = _tool_fingerprint(str(request["commands"][0][0]), root)
    environment = _environment_fingerprint()
    if snapshot is None or tool is None or environment is None:
        return None
    records, source_refs, input_bytes = snapshot
    if kind == "rg" and any(target.is_dir() for target in targets):
        ignore_context = _ignore_context_records(root, targets)
        if ignore_context is None:
            return None
        ignore_records, _ = ignore_context
        by_path = {row[0]: row for row in records}
        by_path.update({row[0]: row for row in ignore_records})
        records = [by_path[key] for key in sorted(by_path)]
        input_bytes = sum(
            row[3] for row in records if row[1] in {"file", "ignore-file"}
        )
        if len(records) > MAX_INPUT_FILES or input_bytes > MAX_INPUT_BYTES:
            return None
    normalized_request = {
        "version": request.get("version"),
        "commands": request.get("commands"),
    }
    binding = {
        "version": CACHE_VERSION,
        "kind": kind,
        "request_digest": click_capability.digest(normalized_request),
        "cwd_digest": hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest(),
        "tool": tool,
        "environment": environment,
        "inputs": records,
    }
    key = _digest(binding)
    return {
        "version": CACHE_VERSION,
        "key": key,
        "kind": kind,
        "request_digest": binding["request_digest"],
        "input_digest": _digest(records),
        "binding_digest": key,
        "source_refs": source_refs,
        "source_count": len(records),
        "input_bytes": input_bytes,
    }


def _cache_root(*, create: bool) -> Path | None:
    root = click_state.state_root().parent / CACHE_DIR_NAME
    try:
        if root.is_symlink():
            return None
        if create:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name != "nt":
                root.chmod(0o700)
        value = root.stat()
        if not stat.S_ISDIR(value.st_mode):
            return None
        if os.name != "nt" and (
            value.st_uid != os.getuid() or stat.S_IMODE(value.st_mode) != 0o700
        ):
            return None
        return root.resolve(strict=True)
    except OSError:
        return None


def _entry_digest(value: dict[str, Any]) -> str:
    return _digest({key: item for key, item in value.items() if key != "digest"})


def _decode_entry(value: Any, *, expected_key: str, now: int) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("version") != CACHE_VERSION:
        return None
    if value.get("key") != expected_key or not _DIGEST.fullmatch(expected_key):
        return None
    created_at = value.get("created_at")
    if (
        not isinstance(created_at, int)
        or isinstance(created_at, bool)
        or created_at <= 0
        or now < created_at
        or now - created_at > CACHE_TTL_SECONDS
        or value.get("digest") != _entry_digest(value)
    ):
        return None
    try:
        stdout = base64.b64decode(value["stdout"], validate=True)
        stderr = base64.b64decode(value["stderr"], validate=True)
    except (KeyError, TypeError, ValueError):
        return None
    if (
        len(stdout) + len(stderr) > MAX_RESULT_BYTES
        or value.get("stdout_bytes") != len(stdout)
        or value.get("stderr_bytes") != len(stderr)
        or value.get("line_count") != stdout.count(b"\n")
        or not isinstance(value.get("source_refs"), list)
        or len(value["source_refs"]) > MAX_SOURCE_REFS
        or any(not isinstance(item, str) or _SAFE_REF.fullmatch(item) is None for item in value["source_refs"])
    ):
        return None
    return {**value, "stdout_data": stdout, "stderr_data": stderr}


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def load_descriptor(descriptor: dict[str, Any], *, now: int | None = None) -> dict[str, Any] | None:
    key = descriptor.get("key") if isinstance(descriptor, dict) else None
    if not isinstance(key, str) or _DIGEST.fullmatch(key) is None:
        return None
    root = _cache_root(create=False)
    if root is None:
        return None
    path = root / f"{key}.json"
    try:
        if path.is_symlink() or path.stat().st_size > MAX_RESULT_BYTES * 2 + 16_384:
            _remove(path)
            return None
        if os.name != "nt":
            value = path.stat()
            if value.st_uid != os.getuid() or stat.S_IMODE(value.st_mode) != 0o600:
                _remove(path)
                return None
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        _remove(path)
        return None
    decoded = _decode_entry(
        stored, expected_key=key, now=int(time.time()) if now is None else now
    )
    if decoded is None:
        _remove(path)
    return decoded


def load(request: dict[str, Any], workspace: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    descriptor = describe(request, workspace)
    if descriptor is None or request.get("fresh") is True:
        return descriptor, None
    hit = load_descriptor(descriptor)
    if hit is None:
        return descriptor, None
    revalidated = describe(request, workspace)
    if revalidated is None or revalidated.get("key") != descriptor.get("key"):
        return revalidated, None
    return revalidated, hit


def _prune(root: Path, *, now: int) -> None:
    try:
        entries = [
            path
            for path in root.iterdir()
            if path.is_file() and not path.is_symlink() and _DIGEST.fullmatch(path.stem)
            and path.suffix == ".json"
        ]
    except OSError:
        return
    retained: list[tuple[float, Path, int]] = []
    for path in entries:
        try:
            value = path.stat()
        except OSError:
            continue
        if now - int(value.st_mtime) > CACHE_TTL_SECONDS:
            _remove(path)
            continue
        retained.append((value.st_mtime, path, value.st_size))
    retained.sort(reverse=True)
    total = 0
    for index, (_, path, size) in enumerate(retained):
        total += size
        if index >= MAX_CACHE_ENTRIES or total > MAX_CACHE_BYTES:
            _remove(path)


def store_with_status(
    request: dict[str, Any],
    workspace: Path,
    descriptor: dict[str, Any] | None,
    stdout: bytes,
    stderr: bytes,
    *,
    now: int | None = None,
) -> tuple[str, str]:
    if descriptor is None:
        return "", "ineligible"
    if len(stdout) + len(stderr) > MAX_RESULT_BYTES:
        return "", "output-too-large"
    current = describe(request, workspace)
    if current is None:
        return "", "inputs-unavailable"
    if current.get("key") != descriptor.get("key"):
        return "", "inputs-changed"
    key = str(descriptor.get("key", ""))
    if _DIGEST.fullmatch(key) is None:
        return "", "invalid-key"
    timestamp = int(time.time()) if now is None else now
    entry = {
        "version": CACHE_VERSION,
        "key": key,
        "kind": descriptor["kind"],
        "created_at": max(1, timestamp),
        "request_digest": descriptor["request_digest"],
        "input_digest": descriptor["input_digest"],
        "binding_digest": descriptor["binding_digest"],
        "source_refs": descriptor["source_refs"],
        "source_count": descriptor["source_count"],
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "line_count": stdout.count(b"\n"),
        "stdout": base64.b64encode(stdout).decode("ascii"),
        "stderr": base64.b64encode(stderr).decode("ascii"),
    }
    entry["digest"] = _entry_digest(entry)
    encoded = json.dumps(entry, ensure_ascii=True, separators=(",", ":"))
    root = _cache_root(create=True)
    if root is None or len(encoded.encode("utf-8")) > MAX_RESULT_BYTES * 2 + 16_384:
        return "", "storage-unavailable"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=root, prefix=".observation-", delete=False
        ) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.chmod(0o600)
        os.replace(temporary, root / f"{key}.json")
        _prune(root, now=max(1, timestamp))
        return key, "stored"
    except OSError:
        if temporary is not None:
            _remove(temporary)
        return "", "storage-error"


def store(
    request: dict[str, Any],
    workspace: Path,
    descriptor: dict[str, Any] | None,
    stdout: bytes,
    stderr: bytes,
    *,
    now: int | None = None,
) -> str:
    return store_with_status(
        request,
        workspace,
        descriptor,
        stdout,
        stderr,
        now=now,
    )[0]


def notice(hit: dict[str, Any]) -> str:
    refs = hit.get("source_refs", [])
    rendered_refs = ", ".join(refs) if refs else "bound source set"
    return (
        f"[Click cache] reused a complete {hit.get('kind', 'inspection')} result; "
        f"no read/search child process ran. Sources: {rendered_refs}; "
        f"stdout {hit.get('stdout_bytes', 0)} bytes / {hit.get('line_count', 0)} lines.\n"
    )
