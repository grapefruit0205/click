from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts.validate_distribution import (
    _release_notes_error,
    _release_version,
    validate,
)
from scripts import build_antigravity_distribution as distribution_builder
from scripts import build_claude_distribution as claude_builder
from scripts.build_antigravity_distribution import hook_manifest_errors, dashboard_manifest_errors


ROOT = Path(__file__).parents[1]


class DistributionValidationTests(unittest.TestCase):
    def test_incremental_build_preserves_unchanged_files_and_removes_stale_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            for name in ("hooks", "skills", "platforms"):
                shutil.copytree(ROOT / name, source / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            destination = source / "dist" / "antigravity"
            with mock.patch.object(distribution_builder, "ROOT", source), mock.patch.object(distribution_builder, "PLATFORM", source / "platforms/antigravity"):
                distribution_builder.build(destination)
                sample = destination / "hooks/dashboard/app.js"
                old_time = sample.stat().st_mtime_ns
                stale = destination / "hooks/stale.py"
                stale.write_text("stale", encoding="utf-8")
                sample_source = source / "hooks/dashboard/styles.css"
                sample_source.write_text(sample_source.read_text(encoding="utf-8") + "\n/* changed */\n", encoding="utf-8")
                distribution_builder.build(destination)
                self.assertEqual(sample.stat().st_mtime_ns, old_time)
                self.assertFalse(stale.exists())
                self.assertEqual((destination / "hooks/dashboard/styles.css").read_bytes(), sample_source.read_bytes())
                (source / "hooks/dashboard/unclassified.js").write_text("unknown", encoding="utf-8")
                self.assertIn("unclassified.js", " ".join(dashboard_manifest_errors(source)))

    def test_public_distribution_is_self_consistent(self) -> None:
        self.assertEqual(validate(ROOT), [])

    def test_new_unclassified_hook_source_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "source"
            (copied / "hooks").mkdir(parents=True)
            for source in (ROOT / "hooks").glob("*.py"):
                shutil.copy2(source, copied / "hooks" / source.name)
            shutil.copy2(
                ROOT / "hooks" / "click_observer_native.c",
                copied / "hooks" / "click_observer_native.c",
            )
            (copied / "hooks" / "click_new_runtime.py").write_text(
                "VALUE = 1\n", encoding="utf-8"
            )
            errors = hook_manifest_errors(copied)
            self.assertEqual(len(errors), 1)
            self.assertIn("click_new_runtime.py", errors[0])

    def test_installed_codex_cache_version_keeps_release_metadata_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            installed = Path(temporary) / "click"
            shutil.copytree(
                ROOT,
                installed,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
            )
            manifest_path = installed / ".codex-plugin" / "plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["version"] = (
                f"{manifest['version']}+codex.20260905161627"
            )
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )

            self.assertEqual(validate(installed), [])

    def test_only_codex_timestamp_build_metadata_is_accepted(self) -> None:
        self.assertEqual(_release_version("0.81.1"), "0.81.1")
        self.assertEqual(
            _release_version("0.81.1+codex.20260905161627"), "0.81.1"
        )
        for invalid in (
            "0.81.1+codex.latest",
            "0.81.1+codex.20260905",
            "0.81.1+build.1",
            "0.81.1-rc.1",
        ):
            with self.subTest(invalid=invalid):
                self.assertEqual(_release_version(invalid), "")

    def test_source_and_antigravity_gate_load_only_sibling_runtime_modules(self) -> None:
        hook_directories = (
            ROOT / "hooks",
            ROOT / "dist" / "antigravity" / "hooks",
            ROOT / "dist" / "claude" / "hooks",
        )
        with tempfile.TemporaryDirectory() as temporary:
            isolated = Path(temporary)
            event = {
                "session_id": "distribution-smoke",
                "turn_id": "turn-1",
                "cwd": str(isolated),
                "prompt": "inspect the project",
            }
            for index, hook_directory in enumerate(hook_directories):
                with self.subTest(hook_directory=hook_directory):
                    environment = os.environ.copy()
                    environment.update(
                        {
                            "PLUGIN_DATA": str(isolated / f"plugin-data-{index}"),
                            "CLICK_CONFIG_HOME": str(isolated / f"config-{index}"),
                            "PYTHONNOUSERSITE": "1",
                            "PYTHONPATH": str(hook_directory.resolve()),
                        }
                    )
                    result = subprocess.run(
                        [
                            sys.executable,
                            str((hook_directory / "click_gate.py").resolve()),
                            "prompt-submit",
                        ],
                        input=json.dumps(event),
                        cwd=isolated,
                        env=environment,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIsInstance(json.loads(result.stdout), dict)

    def test_claude_build_is_incremental_and_reports_unclassified_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            for name in ("hooks", "skills", "platforms"):
                shutil.copytree(ROOT / name, source / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            destination = source / "dist" / "claude"
            with mock.patch.object(claude_builder, "ROOT", source), mock.patch.object(claude_builder, "PLATFORM", source / "platforms/claude"):
                claude_builder.build(destination)
                manifest = json.loads((destination / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["name"], "click")
                self.assertTrue((destination / "hooks/claude_hook.py").is_file())
                self.assertTrue((destination / "hooks/click_hook_worker.py").is_file())
                self.assertFalse((destination / "hooks/antigravity_gate.py").exists())
                self.assertFalse((destination / "hooks/click_windows.py").exists())
                self.assertFalse((destination / "hooks/click_windows.cmd").exists())
                self.assertFalse((destination / "skills/click/agents").exists())
                self.assertTrue((destination / "skills/click/references/claude-code.md").is_file())
                self.assertFalse((destination / "skills/click/references/antigravity.md").exists())
                skill = (destination / "skills/click/SKILL.md").read_text(encoding="utf-8")
                self.assertIn("## Claude Code runtime", skill)
                self.assertTrue(skill.startswith("---\nname: click\n"))
                sample = destination / "hooks/click_gate.py"
                old_time = sample.stat().st_mtime_ns
                stale = destination / "hooks/stale.py"
                stale.write_text("stale", encoding="utf-8")
                claude_builder.build(destination)
                self.assertEqual(sample.stat().st_mtime_ns, old_time)
                self.assertFalse(stale.exists())
                (source / "hooks" / "click_new_runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
                self.assertIn("click_new_runtime.py", " ".join(claude_builder.hook_manifest_errors(source)))
                (source / "skills/click/references/extra.md").write_text("# extra\n", encoding="utf-8")
                self.assertIn("extra.md", " ".join(claude_builder.reference_manifest_errors(source)))

    def test_claude_hook_manifest_never_overlaps_its_exclusions(self) -> None:
        self.assertEqual(
            set(claude_builder.HOOK_FILES) & claude_builder.CLAUDE_HOOK_EXCLUDES, set()
        )
        self.assertEqual(claude_builder.hook_manifest_errors(ROOT), [])
        self.assertEqual(claude_builder.reference_manifest_errors(ROOT), [])
        self.assertIn("claude_hook.py", claude_builder.HOOK_FILES)
        self.assertNotIn("antigravity.md", claude_builder.CLICK_REFERENCE_FILES)
        self.assertIn("claude-code.md", claude_builder.CLICK_REFERENCE_FILES)

    def test_stale_claude_distribution_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            installed = Path(temporary) / "click"
            shutil.copytree(
                ROOT,
                installed,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
            )
            hook = installed / "dist" / "claude" / "hooks" / "claude_hook.py"
            hook.write_text(hook.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
            (installed / "dist" / "claude" / "hooks" / "orphan.py").write_text("X = 1\n", encoding="utf-8")
            errors = validate(installed)
            self.assertIn("Claude Code distribution is stale: hooks/claude_hook.py", errors)
            self.assertIn("Claude Code distribution has an unclassified file: hooks/orphan.py", errors)

    def test_release_notes_allow_only_the_explicit_next_minor_candidate(self) -> None:
        stable = "## v0.24.1 — 2026-08-30\n"
        self.assertEqual(_release_notes_error(stable, "0.24.1"), "")
        current = "## Unreleased v0.25 candidate — evidence\n\n## v0.24.1\n"
        self.assertEqual(_release_notes_error(current, "0.24.1"), "")
        for invalid in (
            "## Unreleased — evidence\n\n## v0.24.1\n",
            "## Unreleased v0.26 candidate\n\n## v0.24.1\n",
            "## Unreleased v0.25 candidate\n## Unreleased v0.25 candidate — two\n## v0.24.1\n",
        ):
            with self.subTest(invalid=invalid):
                self.assertIn("next-minor candidate", _release_notes_error(invalid, "0.24.1"))


if __name__ == "__main__":
    unittest.main()
