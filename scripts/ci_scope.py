#!/usr/bin/env python3
"""Select CI from the exact checked-out Git comparison; ambiguity runs full CI."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:\+codex\.\d{14})?\Z")
DOC_NAMES = frozenset({"README.md", "README.ko.md", "README.zh-CN.md",
                       "RELEASE_NOTES.md", "COMMUNITY_POSTS.md", "SECURITY.md"})
DOC_SUFFIXES = frozenset({".md", ".txt", ".json", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"})
VERSION_PATHS = frozenset({".codex-plugin/plugin.json", ".agents/plugins/marketplace.json"})


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "--no-pager", *args], cwd=root, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30).stdout


@dataclass(frozen=True)
class Change:
    path: str
    old_mode: str
    new_mode: str
    old_oid: str
    new_oid: str


def changes_between(root: Path, base: str, head: str) -> list[Change]:
    if not OID.fullmatch(base) or not OID.fullmatch(head):
        raise ValueError("invalid-commit")
    raw = git(root, "diff", "--raw", "-z", "--no-abbrev", "--no-renames",
              "--no-ext-diff", "--no-textconv", base, head, "--")
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("comparison-limit")
    fields = raw.split(b"\0")
    if fields.pop() != b"" or len(fields) % 2:
        raise ValueError("invalid-comparison")
    changes = []
    for index in range(0, len(fields), 2):
        modes = fields[index].decode("ascii").split()
        path = fields[index + 1].decode("utf-8")
        if len(modes) != 5 or not modes[0].startswith(":") or modes[4] not in {"A", "D", "M", "T"}:
            raise ValueError("unsupported-change")
        if not path or path.startswith("/") or "\\" in path or any(ord(c) < 32 for c in path):
            raise ValueError("invalid-path")
        if any(part in {".", ".."} for part in path.split("/")):
            raise ValueError("invalid-path")
        changes.append(Change(path, modes[0][1:], modes[1], modes[2], modes[3]))
    return changes


def blob(root: Path, oid: str, limit: int = 256 * 1024) -> bytes:
    if not OID.fullmatch(oid) or int(git(root, "cat-file", "-s", oid)) > limit:
        raise ValueError("blob-limit")
    return git(root, "cat-file", "blob", oid)


def is_document(path: str) -> bool:
    return path in DOC_NAMES or (
        path.startswith("docs/") and PurePosixPath(path).suffix in DOC_SUFFIXES
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate-json-key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("invalid-json-constant")


def version_only(path: str, before: bytes, after: bytes) -> bool:
    """Allow only the release version/ref; permissions and execution stay full."""
    try:
        values = [json.loads(raw, object_pairs_hook=_unique_object,
                             parse_constant=_invalid_constant) for raw in (before, after)]
        for value in values:
            if path == ".codex-plugin/plugin.json":
                version = value.pop("version")
            elif path == ".agents/plugins/marketplace.json":
                plugins = value["plugins"]
                if len(plugins) != 1 or plugins[0]["name"] != "click":
                    return False
                ref = plugins[0]["source"].pop("ref")
                if not isinstance(ref, str) or not ref.startswith("v"):
                    return False
                version = ref[1:]
            else:
                return False
            if not isinstance(version, str) or not VERSION.fullmatch(version):
                return False
        return json.dumps(values[0], sort_keys=True) == json.dumps(values[1], sort_keys=True)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        return False


def classify(root: Path, base: str, head: str) -> dict:
    result = {"scope": "full", "reason": "comparison-unavailable", "base": "", "head": "", "changed_count": 0}
    try:
        if not OID.fullmatch(base) or not OID.fullmatch(head):
            return result
        if git(root, "rev-parse", "HEAD").decode().strip() != head:
            return {**result, "reason": "checkout-mismatch"}
        git(root, "merge-base", "--is-ancestor", base, head)
        changes = changes_between(root, base, head)
        result.update(base=base, head=head, changed_count=len(changes))
        if not changes:
            return {**result, "reason": "empty-comparison"}
        scope = "docs"
        for change in changes:
            if {change.old_mode, change.new_mode} - {"000000", "100644"}:
                return {**result, "reason": "file-type-or-mode-change"}
            if is_document(change.path):
                continue
            if (change.path in VERSION_PATHS and change.old_mode == change.new_mode == "100644"
                    and version_only(change.path, blob(root, change.old_oid), blob(root, change.new_oid))):
                scope = "release-metadata"
                continue
            return {**result, "reason": "runtime-policy-or-unclassified-change"}
        return {**result, "scope": scope, "reason": "bounded-change-set"}
    except (ValueError, UnicodeError, OSError, subprocess.SubprocessError):
        return {**result, "scope": "full", "reason": "comparison-unavailable"}


def plan_event(root: Path, name: str, event: dict, head: str) -> dict:
    forced = {"scope": "full", "reason": "full-event", "base": "", "head": "", "changed_count": 0}
    try:
        if name == "pull_request":
            return classify(root, event["pull_request"]["base"]["sha"], head)
        if name == "push" and event["ref"] == "refs/heads/main" and event["after"] == head:
            return classify(root, event["before"], head)
    except (KeyError, TypeError):
        return {**forced, "reason": "event-unavailable"}
    return forced


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = plan_event(Path.cwd(), args.event_name, json.loads(args.event.read_text()), args.head)
    except (OSError, ValueError, TypeError):
        result = {"scope": "full", "reason": "event-unavailable", "base": "", "head": "", "changed_count": 0}
    with args.output.open("a", encoding="utf-8") as stream:
        for key in ("scope", "base", "head"):
            stream.write(f"{key}={result[key]}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
