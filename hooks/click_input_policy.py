"""Bounded fingerprints for owner-declared repository file inputs.

This is not dependency discovery or runtime observation. An owner must declare
the complete file-content boundary before the baseline in the reuse policy.
Ignored files are included; symlinks, special files and races fail closed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

if __package__:
    from . import click_dependency_cache as dependencies
else:
    import click_dependency_cache as dependencies

MAX_FILES = 50_000
MAX_BYTES = 128 * 1024 * 1024


def _prefix(pattern: str) -> str:
    parts = []
    for part in pattern.rstrip("/").split("/"):
        if "*" in part:
            break
        parts.append(part)
    return "/".join(parts)


def snapshot(root: Path, patterns: list[str]) -> str | None:
    """Hash matching content and membership, including absent literal inputs."""
    normalized, error = dependencies.normalize_patterns(patterns)
    if error or normalized is None:
        return None
    root = root.resolve()
    rows: dict[str, Any] = {}
    visited: set[str] = set()
    consumed = 0
    scanned = 0

    def inspect(path: Path, *, literal: bool = False) -> bool:
        nonlocal consumed, scanned
        relative = path.relative_to(root).as_posix()
        if relative in visited:
            return True
        visited.add(relative)
        scanned += 1
        if scanned > MAX_FILES:
            return False
        # Reject a symlink in any lexical ancestor as well as the leaf.
        for parent in (path, *path.parents):
            if parent == root:
                break
            if parent.is_symlink():
                return False
        try:
            before = path.lstat()
        except FileNotFoundError:
            if literal:
                rows[relative] = ["missing"]
            return True
        mode = stat.S_IMODE(before.st_mode)
        matches = literal or any(
            dependencies.path_matches(pattern, relative)
            or dependencies.path_matches(pattern, relative + "/")
            for pattern in normalized
        )
        if stat.S_ISDIR(before.st_mode):
            if literal:
                return False
            if matches:
                rows[relative + "/"] = ["directory", mode]
            return True
        if not stat.S_ISREG(before.st_mode):
            return False
        if not matches:
            return True
        consumed += before.st_size
        if consumed > MAX_BYTES:
            return False
        hasher = hashlib.sha256()
        count = 0
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                return False
            while chunk := stream.read(128 * 1024):
                count += len(chunk)
                if count > before.st_size:
                    return False
                hasher.update(chunk)
            after = os.fstat(stream.fileno())
        current = path.lstat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")
        if count != before.st_size or any(
            getattr(before, key) != getattr(after, key)
            or getattr(before, key) != getattr(current, key) for key in fields
        ):
            return False
        rows[relative] = ["file", mode, hasher.hexdigest()]
        return True

    try:
        for pattern in normalized:
            prefix = _prefix(pattern)
            if not prefix or prefix == ".git" or prefix.startswith(".git/"):
                # An unbounded repository root is not a useful closed boundary.
                return None
            target = root / prefix
            expanding = "*" in pattern or pattern.endswith("/")
            if not inspect(target, literal=not expanding):
                return None
            if not expanding or not target.exists():
                continue
            if not target.is_dir():
                return None
            def onerror(error: OSError) -> None:
                raise error
            for directory, dirs, files in os.walk(target, followlinks=False, onerror=onerror):
                dirs.sort()
                for name in [*dirs, *sorted(files)]:
                    if not inspect(Path(directory) / name):
                        return None
        payload = {"patterns": list(normalized), "inputs": sorted(rows.items())}
        return hashlib.sha256(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    except (OSError, ValueError, RuntimeError):
        return None
