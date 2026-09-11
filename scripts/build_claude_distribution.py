#!/usr/bin/env python3
"""Build Click's self-contained Claude Code plugin directory."""

from __future__ import annotations

from pathlib import Path
import argparse
import shutil

try:
    from build_antigravity_distribution import (
        ANTIGRAVITY_EXTRA_HOOK_SOURCES,
        CLICK_REFERENCE_FILES as SHARED_REFERENCE_FILES,
        DASHBOARD_ASSETS,
        HOOK_FILES as SHARED_HOOK_FILES,
        _insert_after_frontmatter,
        dashboard_manifest_errors,
    )
except ModuleNotFoundError:
    from scripts.build_antigravity_distribution import (
        ANTIGRAVITY_EXTRA_HOOK_SOURCES,
        CLICK_REFERENCE_FILES as SHARED_REFERENCE_FILES,
        DASHBOARD_ASSETS,
        HOOK_FILES as SHARED_HOOK_FILES,
        _insert_after_frontmatter,
        dashboard_manifest_errors,
    )


ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platforms" / "claude"
DESTINATION = ROOT / "dist" / "claude"

# Claude Code keeps the resident Hook worker that Antigravity omits and drops
# the Antigravity launcher plus the Windows batch bridge, which the Claude Code
# Hook command does not invoke.
CLAUDE_HOOK_EXCLUDES = frozenset(
    {
        "antigravity_gate.py",
        "click_windows.py",
    }
)
CLAUDE_ONLY_HOOK_FILES = (
    "claude_hook.py",
    "click_hook.py",
    "click_hook_transport.py",
    "click_hook_worker.py",
)
HOOK_FILES = tuple(
    sorted(
        (set(SHARED_HOOK_FILES) - CLAUDE_HOOK_EXCLUDES) | set(CLAUDE_ONLY_HOOK_FILES)
    )
)
EXTRA_HOOK_SOURCES = ANTIGRAVITY_EXTRA_HOOK_SOURCES
CLICK_REFERENCE_FILES = tuple(
    name for name in SHARED_REFERENCE_FILES if name != "antigravity.md"
) + ("claude-code.md",)


def hook_manifest_errors(root: Path = ROOT) -> list[str]:
    """Find unclassified runtime sources before a distribution can omit them."""
    hook_root = root / "hooks"
    source_files = {path.name for path in hook_root.glob("*.py")}
    source_files.update(
        name for name in EXTRA_HOOK_SOURCES if (hook_root / name).is_file()
    )
    expected = source_files - CLAUDE_HOOK_EXCLUDES
    declared = set(HOOK_FILES)
    errors: list[str] = []
    missing = sorted(expected - declared)
    extra = sorted(declared - expected)
    absent_exclusions = sorted(CLAUDE_HOOK_EXCLUDES - source_files)
    if missing:
        errors.append("Claude Code hook manifest omits: " + ", ".join(missing))
    if extra:
        errors.append(
            "Claude Code hook manifest has unknown entries: " + ", ".join(extra)
        )
    if absent_exclusions:
        errors.append(
            "Claude Code hook exclusions are stale: " + ", ".join(absent_exclusions)
        )
    return errors


def reference_manifest_errors(root: Path = ROOT) -> list[str]:
    references = root / "skills" / "click" / "references"
    actual = {path.name for path in references.glob("*.md")}
    expected = set(CLICK_REFERENCE_FILES) | {"antigravity.md"}
    errors: list[str] = []
    if actual - expected:
        errors.append(
            "Claude Code reference manifest omits: " + ", ".join(sorted(actual - expected))
        )
    if set(CLICK_REFERENCE_FILES) - actual:
        errors.append(
            "Claude Code reference files missing: "
            + ", ".join(sorted(set(CLICK_REFERENCE_FILES) - actual))
        )
    return errors


RUNTIME_NOTE = """
## Claude Code runtime

This generated Skill uses the shared Click contract semantics with the bundled
Claude Code adapter. Before invoking a Click control command, read
[Claude Code Runtime](references/claude-code.md). Its host notes override the
Codex-specific Hook names, the `plugin://click@click` mention, and the Browser
guidance in the shared references.
""".strip()

FIX_RUNTIME_NOTE = """
## Claude Code runtime

Before invoking Click from this repair flow, read
[Claude Code Runtime](../click/references/claude-code.md); every `click-gate`
action is an ordinary Bash command that the installed Hook rewrites.
""".strip()


def rendered_skill(skill_name: str) -> str:
    source = (ROOT / "skills" / skill_name / "SKILL.md").read_text(encoding="utf-8")
    note = RUNTIME_NOTE if skill_name == "click" else FIX_RUNTIME_NOTE
    return _insert_after_frontmatter(source, note)


def expected_files(root: Path = ROOT) -> dict[Path, bytes]:
    platform = root / "platforms" / "claude"
    expected: dict[Path, bytes] = {}
    expected[Path(".claude-plugin/plugin.json")] = (platform / "plugin.json").read_bytes()
    expected[Path("hooks/hooks.json")] = (platform / "hooks.json").read_bytes()
    expected[Path("README.md")] = (platform / "README.md").read_bytes()
    for name in HOOK_FILES:
        expected[Path("hooks") / name] = (root / "hooks" / name).read_bytes()
    for name in DASHBOARD_ASSETS:
        expected[Path("hooks/dashboard") / name] = (root / "hooks/dashboard" / name).read_bytes()
    for name in ("click", "fix"):
        expected[Path("skills") / name / "SKILL.md"] = rendered_skill(name).encode("utf-8")
    for name in CLICK_REFERENCE_FILES:
        relative = Path("skills/click/references") / name
        expected[relative] = (root / relative).read_bytes()
    return expected


def build(destination: Path = DESTINATION, *, clean: bool = False) -> Path:
    manifest_errors = (
        hook_manifest_errors(ROOT)
        + dashboard_manifest_errors(ROOT)
        + reference_manifest_errors(ROOT)
    )
    if manifest_errors:
        raise ValueError("; ".join(manifest_errors))
    destination = destination.resolve()
    expected_parent = (ROOT / "dist").resolve()
    if destination.parent != expected_parent or destination.name != "claude":
        raise ValueError("Claude Code distribution target must be dist/claude")
    if destination.is_symlink():
        raise ValueError("Claude Code distribution target must not be a symlink")
    if clean and destination.exists():
        shutil.rmtree(destination)
    expected = expected_files(ROOT)
    # Generated output has no independent sources. Remove stale files first,
    # then only write changed bytes so repeated local builds do no extra I/O.
    if destination.exists():
        for path in sorted(destination.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_symlink():
                path.unlink()
            elif path.is_file() and path.relative_to(destination) not in expected:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    for relative, contents in expected.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_bytes() != contents:
            path.write_bytes(contents)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", action="store_true", help="regenerate every file for release validation")
    args = parser.parse_args()
    path = build(clean=args.clean)
    print(f"Built Claude Code plugin at {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
