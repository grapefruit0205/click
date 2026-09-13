from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

from scripts.run_ci_tests import isolated_temp, partition
from scripts import ci_scope, check_doc_links


ROOT = Path(__file__).resolve().parents[1]


class RepositoryShardInventoryTests(unittest.TestCase):
    def test_ci_partition_temp_root_is_isolated_and_inherited_by_children(self) -> None:
        previous = tempfile.gettempdir()
        with isolated_temp():
            root = tempfile.gettempdir()
            self.assertNotEqual(root, previous)
            child = subprocess.run(
                [sys.executable, "-c", "import tempfile; print(tempfile.gettempdir())"],
                text=True, capture_output=True, check=True,
            )
            self.assertEqual(child.stdout.strip(), root)
        self.assertEqual(tempfile.gettempdir(), previous)
        self.assertFalse(Path(root).exists())

    def test_ci_partition_covers_every_test_once_and_keeps_class_fixtures_together(self) -> None:
        class Slow(unittest.TestCase):
            def test_a(self):
                pass

            def test_b(self):
                pass

        class Fast(unittest.TestCase):
            def test_c(self):
                pass

        tests = [Slow("test_a"), Slow("test_b"), Fast("test_c")]
        durations = {test.id(): 5.0 if isinstance(test, Slow) else 1.0 for test in tests}
        buckets = partition(tests, 2, durations)
        self.assertEqual(sorted(test.id() for bucket in buckets for test in bucket), sorted(test.id() for test in tests))
        self.assertEqual(len({i for i, bucket in enumerate(buckets) for test in bucket if isinstance(test, Slow)}), 1)
        self.assertEqual(sorted(len(bucket) for bucket in buckets), [1, 2])
        self.assertEqual(buckets, partition(tests, 2, durations))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            partition([tests[0], tests[0]], 2, durations)

    def test_every_parent_discovery_file_has_one_shard_owner(self) -> None:
        policy = json.loads(
            (ROOT / ".click/evidence-shards.json").read_text(encoding="utf-8")
        )
        self.assertEqual(policy.get("version"), 1)
        entries = policy.get("entries")
        self.assertIsInstance(entries, list)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        patterns = entry["inventory"]
        discovered = sorted(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "tests").rglob("*.py")
            if any(
                fnmatch.fnmatchcase(path.relative_to(ROOT).as_posix(), pattern)
                for pattern in patterns
            )
        )
        covered = [
            path
            for shard in entry["shards"]
            for path in shard["covers"]
        ]
        self.assertEqual(sorted(covered), discovered)
        self.assertEqual(len(covered), len(set(covered)))


class CIScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="click-ci-scope-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "CI test")
        self.git("config", "user.email", "ci@example.invalid")
        self.write("README.md", "[Guide](docs/guide.md)\n")
        self.write("docs/guide.md", "[Source](../hooks/runtime.py)\n")
        self.write("hooks/runtime.py", "VALUE = 1\n")
        self.manifest = {"name": "click", "version": "1.0.0", "hooks": "hooks/", "enabled": 1}
        self.write(".codex-plugin/plugin.json", json.dumps(self.manifest))
        self.write(".agents/plugins/marketplace.json", json.dumps({"plugins": [
            {"name": "click", "source": {"url": "https://example.invalid/click.git", "ref": "v1.0.0"}}
        ]}))
        self.base = self.commit()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True,
                              text=True, check=True).stdout.strip()

    def write(self, name, text):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture change")
        return self.git("rev-parse", "HEAD")

    def test_docs_and_exact_release_values_use_reduced_scope(self):
        self.write("docs/guide.md", "Updated guide\n")
        self.assertEqual(ci_scope.classify(self.root, self.base, self.commit())["scope"], "docs")
        self.manifest["version"] = "1.0.1"
        self.write(".codex-plugin/plugin.json", json.dumps(self.manifest))
        self.write(".agents/plugins/marketplace.json", json.dumps({"plugins": [
            {"name": "click", "source": {"url": "https://example.invalid/click.git", "ref": "v1.0.1"}}
        ]}))
        self.assertEqual(ci_scope.classify(self.root, self.base, self.commit())["scope"], "release-metadata")

    def test_manifest_behavior_and_json_type_changes_run_full(self):
        before = json.dumps(self.manifest).encode()
        for changed in ({**self.manifest, "hooks": "other/"},
                        {**self.manifest, "enabled": True},
                        {**self.manifest, "version": "latest"}):
            self.assertFalse(ci_scope.version_only(".codex-plugin/plugin.json", before, json.dumps(changed).encode()))
        for invalid in (b'{"version":"1.0.1","version":"1.0.2"}', b'{"version":"1.0.1","x":NaN}'):
            self.assertFalse(ci_scope.version_only(".codex-plugin/plugin.json", invalid, invalid))
        self.write(".codex-plugin/plugin.json", json.dumps({**self.manifest, "hooks": "other/"}))
        self.assertEqual(ci_scope.classify(self.root, self.base, self.commit())["scope"], "full")

    def test_entire_push_range_catches_engine_change_before_latest_docs_commit(self):
        self.write("hooks/runtime.py", "VALUE = 2\n")
        middle = self.commit()
        self.write("README.md", "Documentation follow-up\n")
        head = self.commit()
        self.assertEqual(ci_scope.classify(self.root, middle, head)["scope"], "docs")
        event = {"ref": "refs/heads/main", "before": self.base, "after": head, "commits": []}
        self.assertEqual(ci_scope.plan_event(self.root, "push", event, head)["scope"], "full")

    def test_pull_request_compares_base_to_actual_merge_checkout(self):
        self.git("checkout", "-qb", "topic")
        self.write("docs/guide.md", "Topic docs\n")
        topic = self.commit()
        self.git("checkout", "main")
        self.write("hooks/runtime.py", "VALUE = 2\n")
        base = self.commit()
        self.git("merge", "--no-ff", "--no-edit", "topic")
        merged = self.git("rev-parse", "HEAD")
        event = {"pull_request": {"base": {"sha": base}, "head": {"sha": topic}}}
        self.assertEqual(ci_scope.plan_event(self.root, "pull_request", event, merged)["scope"], "docs")
        self.assertEqual(ci_scope.plan_event(self.root, "pull_request", event, topic)["scope"], "full")

    def test_renaming_runtime_into_docs_still_runs_full(self):
        self.git("mv", "hooks/runtime.py", "docs/runtime.md")
        self.assertEqual(ci_scope.classify(self.root, self.base, self.commit())["scope"], "full")

    def test_mode_changes_unknown_paths_and_policy_docs_run_full(self):
        self.git("update-index", "--chmod=+x", "docs/guide.md")
        self.git("commit", "-qm", "mode change")
        self.assertEqual(ci_scope.classify(self.root, self.base, self.git("rev-parse", "HEAD"))["scope"], "full")
        for path in (".click/evidence-reuse.json", ".github/workflows/ci.yml", "skills/click/SKILL.md",
                     "GUARD_CLASSIFICATION.md", "docs/example.py", "unknown.data"):
            with self.subTest(path=path):
                self.assertFalse(ci_scope.is_document(path))

    def test_missing_comparison_manual_and_release_events_run_full(self):
        for base in ("0" * 40, "f" * 40, "bad\nscope=docs", ""):
            self.assertEqual(ci_scope.classify(self.root, base, self.base)["scope"], "full")
        for name, event in (("workflow_dispatch", {}), ("release", {}),
                            ("push", {"ref": "refs/tags/v1.0.0", "before": self.base, "after": self.base}),
                            ("pull_request", {})):
            self.assertEqual(ci_scope.plan_event(self.root, name, event, self.base)["scope"], "full")
        self.assertEqual(ci_scope.classify(self.root, self.base, self.base)["scope"], "full")

    def test_cli_handles_missing_event_without_omitting_matrix_output(self):
        output = self.root / "output"
        result = subprocess.run([sys.executable, str(ROOT / "scripts/ci_scope.py"),
                                 "--event", str(self.root / "missing.json"), "--event-name", "push",
                                 "--head", self.base, "--output", str(output)],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_text(), "scope=full\nbase=\nhead=\n")
        output.unlink()
        self.write("docs/guide.md", "Changed documentation\n")
        head = self.commit()
        event = self.root / "event.json"
        event.write_text(json.dumps({"ref": "refs/heads/main", "before": self.base, "after": head}))
        output.write_text("")
        result = subprocess.run([sys.executable, str(ROOT / "scripts/ci_scope.py"),
                                 "--event", str(event), "--event-name", "push",
                                 "--head", head, "--output", str(output)],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_text(), f"scope=docs\nbase={self.base}\nhead={head}\n")

    def test_links_detect_new_breakage_and_target_deletion(self):
        self.assertEqual(check_doc_links.check(self.root, self.base), [])
        self.write("docs/guide.md", "[Source](../hooks/runtime.py)\n[Missing](absent.md)\n")
        head = self.commit()
        self.assertEqual(check_doc_links.check(self.root, head, self.base),
                         ["docs/guide.md: missing local target docs/absent.md"])
        self.write("docs/guide.md", "[Source](../hooks/runtime.py)\n")
        (self.root / "hooks/runtime.py").unlink()
        self.assertEqual(check_doc_links.check(self.root, self.commit(), self.base),
                         ["docs/guide.md: missing local target hooks/runtime.py"])

    def test_links_ignore_unchanged_legacy_breakage_and_code_examples(self):
        self.write("docs/guide.md", "[Old](old-missing.md)\n")
        old = self.commit()
        self.write("README.md", "[Guide][guide]\n[guide]: docs/guide.md\n"
                   "```md\n[example](absent.md)\n```\n`[example](also-absent.md)`\n"
                   '<img src="https://example.invalid/logo.png">\n')
        self.assertEqual(check_doc_links.check(self.root, self.commit(), old), [])
        self.assertEqual(check_doc_links.links('[x](<file with spaces.md>)\n![img](a(b).png)\n'),
                         {"file with spaces.md", "a(b).png"})


class CIWorkflowGateTests(unittest.TestCase):
    def test_required_names_and_every_matrix_depend_on_the_scope(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        jobs = dict(re.findall(r"(?ms)^  ([a-z-]+):\n(.*?)(?=^  [a-z-]+:|\Z)", workflow.split("jobs:\n", 1)[1]))
        matrix_jobs = set(jobs) - {"changes", "repository-checks", "deterministic-tests"}
        self.assertEqual(len(matrix_jobs), 12)
        self.assertIn("framework-observation", matrix_jobs)
        for name in matrix_jobs:
            self.assertIn("needs: changes", jobs[name])
            self.assertIn("needs.changes.result != 'success'", jobs[name])
            self.assertIn("needs.changes.outputs.scope != 'docs'", jobs[name])
            self.assertIn("needs.changes.outputs.scope != 'release-metadata'", jobs[name])
            self.assertIn(f"- {name}\n", jobs["deterministic-tests"])
        self.assertIn("if: always()", jobs["deterministic-tests"])
        self.assertIn("os: [ubuntu-latest, macos-latest, windows-latest]", jobs["deterministic-tests"])
        self.assertNotIn("paths-ignore:", workflow)
        self.assertIn("tags: ['v*']", workflow)

    @unittest.skipUnless(os.name == "nt" or shutil.which("bash"), "required-check gate uses bash")
    def test_actual_required_gate_rejects_failures_cancellation_and_missing_jobs(self):
        bash = shutil.which("bash")
        if os.name == "nt":
            # GitHub's `shell: bash` uses Git Bash; System32/bash.exe may be
            # the WSL launcher and cannot run this native Windows fixture.
            git = Path(shutil.which("git") or "")
            bash = next((str(path) for path in (
                git.parent / "bash.exe", git.parent.parent / "bin/bash.exe",
            ) if path.is_file()), None)
        self.assertIsNotNone(bash, "The CI required-check fixture needs Git Bash on Windows")
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        block = workflow.split("  deterministic-tests:\n", 1)[1].split("\n  native-authoritative:", 1)[0]
        script = textwrap.dedent(block.rsplit("        run: |\n", 1)[1])
        temporary = tempfile.TemporaryDirectory(prefix="click-ci-gate-")
        self.addCleanup(temporary.cleanup)
        script_path = Path(temporary.name) / "required-check.sh"
        script_path.write_text(script, encoding="utf-8", newline="\n")
        count = len(re.findall(r"needs\.([a-z-]+)\.result", block.split("MATRIX_RESULTS: >-", 1)[1].split("run: |", 1)[0]))
        cases = [("full", "success", "success", ["success"] * count, True),
                 ("docs", "success", "success", ["skipped"] * count, True),
                 ("release-metadata", "success", "success", ["skipped"] * count, True),
                 ("full", "success", "success", ["success"] * (count - 1) + ["failure"], False),
                 ("full", "success", "success", ["success"] * (count - 1) + ["skipped"], False),
                 ("docs", "failure", "success", ["skipped"] * count, False),
                 ("docs", "success", "cancelled", ["skipped"] * count, False),
                 ("full", "success", "success", ["success"] * (count - 1), False),
                 ("unknown", "success", "success", ["skipped"] * count, False)]
        for scope, plan, repository, results, passed in cases:
            with self.subTest(scope=scope, plan=plan, repository=repository, results=results):
                env = {**os.environ, "CI_SCOPE": scope, "PLAN_RESULT": plan,
                       "REPOSITORY_RESULT": repository, "MATRIX_RESULTS": " ".join(results)}
                result = subprocess.run([bash, "--noprofile", "--norc", "-e", "-o", "pipefail", script_path.as_posix()], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, passed,
                                 {"bash": bash, "exit_code": result.returncode,
                                  "stdout": result.stdout, "stderr": result.stderr})


if __name__ == "__main__":
    unittest.main()
