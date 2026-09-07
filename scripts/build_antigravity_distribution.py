#!/usr/bin/env python3
"""Build Click's self-contained Google Antigravity plugin directory."""

from __future__ import annotations

from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platforms" / "antigravity"
DESTINATION = ROOT / "dist" / "antigravity"

HOOK_FILES = (
    "__init__.py",
    "click_auto_sharding.py",
    "click_shard_proposal.py",
    "click_sharding_setup.py",
    "click_authoritative_observer.py",
    "click_observation_inputs.py",
    "click_observation_cache.py",
    "click_observer_runtime.py",
    "click_observer_bootstrap.py",
    "click_observer_native.c",
    "click_collector_runtime.py",
    "click_test_inventory.py",
    "click_unittest_collector.py",
    "click_pytest_collector.py",
    "click_dependency_candidates.py",
    "click_browser.py",
    "click_browser_advisory.py",
    "click_capability.py",
    "click_claims.py",
    "click_verification_policy.py",
    "click_verification_meter.py",
    "click_contract.py",
    "click_contract_state.py",
    "click_dashboard_projection.py",
    "click_change_policy.py",
    "click_dependency_cache.py",
    "click_dependency_trace.py",
    "click_observer_backend.py",
    "click_observer_common.py",
    "click_observer_control.py",
    "click_observer_linux.py",
    "click_observer_macos.py",
    "click_observer_windows.py",
    "click_evidence.py",
    "click_evidence_shards.py",
    "click_receipt.py",
    "click_receipt_runtime.py",
    "click_host_coverage.py",
    "click_incremental.py",
    "click_host_router.py",
    "click_import_bootstrap.py",
    "click_inspection.py",
    "click_inspection_policy.py",
    "click_lifecycle.py",
    "click_mode.py",
    "click_mutation.py",
    "click_observation.py",
    "click_process.py",
    "click_prompt.py",
    "click_runner_transport.py",
    "click_runtime_state.py",
    "click_service.py",
    "click_shadow_dashboard.py",
    "click_shadow_intelligence.py",
    "click_state.py",
    "click_verification.py",
    "click_gate.py",
    "platform_protocol.py",
    "antigravity_gate.py",
)
ANTIGRAVITY_HOOK_EXCLUDES = frozenset({"click_hook.py", "click_windows.py"})
ANTIGRAVITY_EXTRA_HOOK_SOURCES = frozenset({"click_observer_native.c"})
CLICK_REFERENCE_FILES = (
    "modes.md",
    "translation-guide.md",
    "directive-format.md",
    "anti-loop-policy.md",
    "verification-profiles.md",
    "capability-protocol.md",
    "authoritative-observer-v2.md",
    "automatic-sharding-setup.md",
    "observer-v1.md",
    "shadow-intelligence-v1.md",
    "evidence-shards-v1.md",
    "antigravity.md",
)


def hook_manifest_errors(root: Path = ROOT) -> list[str]:
    """Find unclassified runtime sources before a distribution can omit them."""
    hook_root = root / "hooks"
    source_files = {path.name for path in hook_root.glob("*.py")}
    source_files.update(
        name
        for name in ANTIGRAVITY_EXTRA_HOOK_SOURCES
        if (hook_root / name).is_file()
    )
    expected = source_files - ANTIGRAVITY_HOOK_EXCLUDES
    declared = set(HOOK_FILES)
    errors: list[str] = []
    if len(declared) != len(HOOK_FILES):
        errors.append("Antigravity hook manifest contains duplicate entries")
    missing = sorted(expected - declared)
    extra = sorted(declared - expected)
    absent_exclusions = sorted(ANTIGRAVITY_HOOK_EXCLUDES - source_files)
    if missing:
        errors.append("Antigravity hook manifest omits: " + ", ".join(missing))
    if extra:
        errors.append(
            "Antigravity hook manifest has unknown entries: " + ", ".join(extra)
        )
    if absent_exclusions:
        errors.append(
            "Antigravity hook exclusions are stale: "
            + ", ".join(absent_exclusions)
        )
    return errors

RUNTIME_NOTE = """
## Google Antigravity runtime

This generated Skill uses the shared Click contract semantics with the bundled
Antigravity adapter. Before invoking a Click control command, read
[Google Antigravity Runtime](references/antigravity.md). Its launcher replaces
the bare `click-gate` executable, and its documented host limits override Codex-
specific Hook names or Browser guidance in the shared references.
""".strip()

FIX_RUNTIME_NOTE = """
## Google Antigravity runtime

Before invoking Click from this repair flow, read
[Google Antigravity Runtime](../click/references/antigravity.md) and use its
bundled launcher in place of the bare `click-gate` executable.
""".strip()


def _insert_after_frontmatter(source: str, note: str) -> str:
    marker = "\n---\n"
    index = source.find(marker, 4)
    if index < 0:
        raise ValueError("Skill source has no closing frontmatter fence")
    end = index + len(marker)
    return source[:end] + "\n" + note + "\n\n" + source[end:].lstrip("\n")


def rendered_skill(skill_name: str) -> str:
    source = (ROOT / "skills" / skill_name / "SKILL.md").read_text(encoding="utf-8")
    note = RUNTIME_NOTE if skill_name == "click" else FIX_RUNTIME_NOTE
    return _insert_after_frontmatter(source, note)


def build(destination: Path = DESTINATION) -> Path:
    manifest_errors = hook_manifest_errors(ROOT)
    if manifest_errors:
        raise ValueError("; ".join(manifest_errors))
    destination = destination.resolve()
    expected_parent = (ROOT / "dist").resolve()
    if destination.parent != expected_parent or destination.name != "antigravity":
        raise ValueError("Antigravity distribution target must be dist/antigravity")
    if destination.exists():
        shutil.rmtree(destination)
    (destination / "hooks").mkdir(parents=True)
    (destination / "skills" / "click" / "references").mkdir(parents=True)
    (destination / "skills" / "fix").mkdir(parents=True)

    for name in ("plugin.json", "hooks.json", "README.md"):
        shutil.copy2(PLATFORM / name, destination / name)
    for name in HOOK_FILES:
        shutil.copy2(ROOT / "hooks" / name, destination / "hooks" / name)
    (destination / "skills" / "click" / "SKILL.md").write_text(
        rendered_skill("click"), encoding="utf-8"
    )
    (destination / "skills" / "fix" / "SKILL.md").write_text(
        rendered_skill("fix"), encoding="utf-8"
    )
    for name in CLICK_REFERENCE_FILES:
        shutil.copy2(
            ROOT / "skills" / "click" / "references" / name,
            destination / "skills" / "click" / "references" / name,
        )
    return destination


def main() -> int:
    path = build()
    print(f"Built Antigravity plugin at {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
