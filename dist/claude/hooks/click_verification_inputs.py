"""Bounded, private bindings for opt-in local verification inputs.

This leaf never grants reuse. It returns content digests and stable failure
reasons without returning matched paths or file contents to callers.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable

if __package__:
    from . import click_import_bootstrap
else:
    import click_import_bootstrap

(click_dependency_cache,) = click_import_bootstrap.load_siblings(
    __package__, "click_dependency_cache"
)


VERSION = 1
MAX_PATTERNS = 32
MAX_SCAN_ENTRIES = 50_000
MAX_MATCHED_FILES = 4_096
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_PATTERN_BYTES = 4_096
REUSE_POLICIES = frozenset({"conditional", "rerun", "always-run"})
_SENSITIVE_COMPONENT = re.compile(
    r"(?i)(?:^|[._-])(credential|passwd|password|secret|token|api[_-]?key)(?:$|[._-])"
)
_SENSITIVE_NAMES = frozenset(
    {".env", ".netrc", "id_rsa", "id_ed25519", "credentials"}
)


def normalize_patterns(value: Any) -> tuple[tuple[str, ...] | None, str]:
    if not isinstance(value, list):
        return None, "explicit-inputs-must-be-a-list"
    if not value:
        return (), ""
    if len(value) > MAX_PATTERNS:
        return None, "explicit-input-pattern-limit"
    normalized, error = click_dependency_cache.normalize_patterns(value)
    if error or normalized is None:
        return None, "explicit-input-pattern-invalid"
    for pattern in normalized:
        if len(os.fsencode(pattern)) > MAX_PATTERN_BYTES:
            return None, "explicit-input-pattern-limit"
        components = [part for part in pattern.rstrip("/").split("/") if part]
        if any(
            component.lower() in _SENSITIVE_NAMES
            or _SENSITIVE_COMPONENT.search(component) is not None
            for component in components
        ):
            return None, "explicit-input-sensitive-path"
        if components and components[0] == ".git":
            return None, "explicit-input-protected-path"
    return normalized, ""


def _sensitive_relative_path(relative: str) -> bool:
    components = [part for part in relative.split("/") if part]
    return any(
        component.lower() in _SENSITIVE_NAMES
        or _SENSITIVE_COMPONENT.search(component) is not None
        for component in components
    )


def group_patterns(checks: list[dict[str, Any]]) -> tuple[str, ...]:
    values = {
        tuple(check.get("inputs", []))
        for check in checks
        if isinstance(check, dict)
    }
    if len(values) != 1:
        return ()
    selected = next(iter(values), ())
    normalized, error = normalize_patterns(list(selected))
    return () if error or normalized is None else normalized


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    # Reading may update atime; only mutation-relevant identity participates in
    # the before/after race check.
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
    )


def _unavailable(patterns: tuple[str, ...], reason: str) -> dict[str, Any]:
    return {
        "version": VERSION,
        "status": "unavailable",
        "digest": _digest(
            {"version": VERSION, "patterns": patterns, "status": reason}
        ),
        "reason": reason,
        "match_count": 0,
    }


def snapshot(
    root: Path, patterns: tuple[str, ...],
    *, file_content_digest: Callable[[Path], str] | None = None,
) -> dict[str, Any]:
    """Hash matched regular files and the match set under one workdir."""

    if not patterns:
        return {
            "version": VERSION,
            "status": "not-configured",
            "digest": "",
            "reason": "",
            "match_count": 0,
        }
    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return _unavailable(patterns, "explicit-input-root-unavailable")
    if not root.is_dir():
        return _unavailable(patterns, "explicit-input-root-unavailable")

    records: list[tuple[str, int, str]] = []
    scanned = 0
    total_bytes = 0
    try:
        for directory, directories, names in os.walk(root, followlinks=False):
            base = Path(directory)
            directories[:] = sorted(name for name in directories if name != ".git")
            names.sort()
            scanned += len(directories) + len(names)
            if scanned > MAX_SCAN_ENTRIES:
                return _unavailable(patterns, "explicit-input-scan-limit")
            for name in [*directories, *names]:
                target = base / name
                relative = target.relative_to(root).as_posix()
                if not any(
                    click_dependency_cache.path_matches(pattern, relative)
                    for pattern in patterns
                ):
                    continue
                # A broad glob must never turn a low-entropy secret file into a
                # publicly correlatable content digest. The caller can still run
                # the real check, but this input set remains non-reusable.
                if _sensitive_relative_path(relative):
                    return _unavailable(
                        patterns, "explicit-input-sensitive-match"
                    )
                metadata = target.lstat()
                if stat.S_ISDIR(metadata.st_mode):
                    continue
                if stat.S_ISLNK(metadata.st_mode):
                    return _unavailable(patterns, "explicit-input-symlink")
                if not stat.S_ISREG(metadata.st_mode):
                    return _unavailable(patterns, "explicit-input-file-type")
                if metadata.st_size > MAX_FILE_BYTES:
                    return _unavailable(patterns, "explicit-input-file-limit")
                total_bytes += int(metadata.st_size)
                if total_bytes > MAX_TOTAL_BYTES:
                    return _unavailable(patterns, "explicit-input-byte-limit")
                if file_content_digest is None:
                    hasher = hashlib.sha256()
                    with target.open("rb") as stream:
                        while True:
                            chunk = stream.read(128 * 1024)
                            if not chunk:
                                break
                            hasher.update(chunk)
                    content_digest = hasher.hexdigest()
                else:
                    # Only the caller's current binding stage may share content
                    # reads. Membership, limits and file identity stay fresh.
                    content_digest = file_content_digest(target)
                    if not content_digest:
                        return _unavailable(patterns, "explicit-input-unavailable")
                if _file_identity(target.lstat()) != _file_identity(metadata):
                    return _unavailable(patterns, "explicit-input-raced")
                records.append((relative, int(metadata.st_size), content_digest))
                if len(records) > MAX_MATCHED_FILES:
                    return _unavailable(patterns, "explicit-input-match-limit")
    except (OSError, RuntimeError, ValueError):
        return _unavailable(patterns, "explicit-input-unavailable")

    records.sort()
    return {
        "version": VERSION,
        "status": "complete",
        "digest": _digest(
            {"version": VERSION, "patterns": patterns, "records": records}
        ),
        "reason": "",
        "match_count": len(records),
    }


def group_binding(
    checks: list[dict[str, Any]], *, cwd: Path,
    file_content_digest: Callable[[Path], str] | None = None,
) -> dict[str, Any]:
    return snapshot(cwd, group_patterns(checks), file_content_digest=file_content_digest)


def context_digest(
    base_digest: str, checks: list[dict[str, Any]], *, cwd: Path,
    file_content_digest: Callable[[Path], str] | None = None,
) -> tuple[str, dict[str, Any]]:
    binding = group_binding(checks, cwd=cwd, file_content_digest=file_content_digest)
    if binding["status"] == "not-configured":
        return base_digest, binding
    return _digest(
        {
            "version": VERSION,
            "environment_digest": base_digest,
            "explicit_input_digest": binding["digest"],
        }
    ), binding
