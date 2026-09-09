#!/usr/bin/env python3
"""Check local file targets in maintained Markdown, without network requests.

Inline links/images, reference definitions and HTML href/src are supported.
Code examples, external URLs and heading anchors are not link-validation input.
Existing broken targets are grandfathered only if unchanged from the base.
"""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
import posixpath
import re
from urllib.parse import unquote, urlsplit

try:
    from ci_scope import OID, blob, git
except ModuleNotFoundError:
    from scripts.ci_scope import OID, blob, git


class _HTMLLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.links.update(value for key, value in attrs if key in {"href", "src"} and value)


def links(text: str) -> set[str]:
    lines = []
    fence = ""
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            value = marker[1]
            if not fence:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = ""
            continue
        if not fence:
            lines.append(line)
    content = re.sub(r"(`+).*?\1", "", "\n".join(lines), flags=re.DOTALL)
    destination = r"(<[^>\n]+>|(?:\\.|[^\s()]+|\([^()]*\))+)"
    found = re.findall(r"\[[^\]\n]*\]\(\s*" + destination, content)
    found += re.findall(r"(?m)^ {0,3}\[[^\]\n]+\]:\s*" + destination, content)
    parser = _HTMLLinks()
    parser.feed(content)
    return {re.sub(r"\\([\\() ])", r"\1", value.strip("<>")) for value in found} | parser.links


def tree(root: Path, revision: str) -> dict[str, tuple[str, str]]:
    if not OID.fullmatch(revision):
        raise ValueError("invalid documentation revision")
    records = {}
    for record in git(root, "ls-tree", "-r", "-z", revision).split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        if kind == "blob":
            records[name.decode("utf-8")] = (mode, oid)
    return records


def maintained(path: str) -> bool:
    return path.endswith(".md") and (
        "/" not in path or path.startswith(("skills/", "platforms/"))
        or path.startswith("docs/") and not path.startswith("docs/history/")
    )


def targets(root: Path, records: dict[str, tuple[str, str]]) -> dict[str, set[str]]:
    result = {}
    for path, (mode, oid) in records.items():
        if not maintained(path):
            continue
        if mode != "100644":
            raise ValueError(f"documentation must be a regular file: {path}")
        values = set()
        for url in links(blob(root, oid, 4 * 1024 * 1024).decode("utf-8")):
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            destination = unquote(parsed.path)
            values.add(posixpath.normpath(posixpath.join(posixpath.dirname(path), destination)))
        result[path] = values
    return result


def check(root: Path, head: str, base: str = "") -> list[str]:
    current = tree(root, head)
    previous = tree(root, base) if base else {}
    current_links = targets(root, current)
    previous_links = targets(root, previous)

    def exists(path: str, records: dict) -> bool:
        return path in records or any(name.startswith(path.rstrip("/") + "/") for name in records)

    return sorted(
        f"{document}: missing local target {target}"
        for document, values in current_links.items() for target in values
        if not exists(target, current)
        and not (target in previous_links.get(document, set()) and not exists(target, previous))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    args = parser.parse_args()
    root = Path.cwd()
    head = args.head or git(root, "rev-parse", "HEAD").decode().strip()
    errors = check(root, head, args.base)
    for error in errors:
        print(error)
    if not errors:
        print("Maintained Markdown local file targets passed (external URLs and anchors excluded).")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
