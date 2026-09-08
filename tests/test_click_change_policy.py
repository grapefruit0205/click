from __future__ import annotations

import ast
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from hooks import click_change_policy


class ClickChangePolicyValidationTests(unittest.TestCase):
    def snapshot(self, **updates: object) -> dict[str, object]:
        value: dict[str, object] = {
            "version": 1,
            "provider": click_change_policy.SNAPSHOT_PROVIDER_NAME,
            "object_format": "sha1",
            "head": "a" * 40,
            "overrides": [],
        }
        value.update(updates)
        value["digest"] = click_change_policy._digest(value)
        return value

    def test_changed_paths_rejects_malformed_values_without_raising(self) -> None:
        for value in (
            None, True, 1, 1.9, "README.md", {}, ("README.md",),
            [None], [True], [1], [1.9], [float("nan")], [float("inf")],
            ["README.md", 1], [[]], [{}], ["README.md", []],
        ):
            with self.subTest(value=value):
                self.assertFalse(click_change_policy.changed_paths_are_valid(value))

    def test_changed_paths_preserves_order_uniqueness_and_existing_limits(self) -> None:
        for value in ([], ["README.md"], ["README.md", "docs/guide.md"]):
            with self.subTest(value=value):
                self.assertTrue(click_change_policy.changed_paths_are_valid(value))
        for value in (
            ["README.md", "README.md"], ["docs/guide.md", "README.md"],
            ["../README.md"], ["/README.md"], ["README\x00.md"],
        ):
            with self.subTest(value=value):
                self.assertFalse(click_change_policy.changed_paths_are_valid(value))
        maximum = click_change_policy.MAX_CHANGED_PATHS
        paths = [f"docs/{index:05d}.md" for index in range(maximum + 1)]
        self.assertTrue(click_change_policy.changed_paths_are_valid(paths[:maximum]))
        self.assertFalse(click_change_policy.changed_paths_are_valid(paths))
        self.assertTrue(click_change_policy.changed_paths_are_valid(paths[:128], maximum=128))
        self.assertFalse(click_change_policy.changed_paths_are_valid(paths[:129], maximum=128))
        self.assertTrue(click_change_policy.changed_paths_are_valid(["x" * click_change_policy.MAX_PATH_BYTES]))
        self.assertFalse(click_change_policy.changed_paths_are_valid(["x" * (click_change_policy.MAX_PATH_BYTES + 1)]))

    def test_unencodable_paths_fail_closed(self) -> None:
        snapshot = self.snapshot(overrides=[{"path": "README.md", "identity": "missing"}])
        error = UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed")
        with mock.patch.object(click_change_policy.os, "fsencode", side_effect=error):
            self.assertFalse(click_change_policy.changed_paths_are_valid(["README.md"]))
            self.assertFalse(click_change_policy.snapshot_is_valid(snapshot))

    def test_snapshot_requires_integer_schema_version(self) -> None:
        self.assertTrue(click_change_policy.snapshot_is_valid(self.snapshot()))
        self.assertTrue(click_change_policy.snapshot_is_valid(
            self.snapshot(object_format="sha256", head="a" * 64)
        ))
        for version in (True, False, 1.0, 1.9, "1", None, [], {}, float("nan"), float("inf")):
            with self.subTest(version=version):
                self.assertFalse(click_change_policy.snapshot_is_valid(self.snapshot(version=version)))

    def test_snapshot_rejects_malformed_overrides_and_retains_limits(self) -> None:
        for overrides in (
            None, {}, "README.md", [None], [[]], [{}],
            [{"path": [], "identity": "missing"}],
            [{"path": "README.md", "identity": {}}],
            [{"path": "README.md", "identity": "missing"}] * 2,
            [{"path": "z.md", "identity": "missing"}, {"path": "a.md", "identity": "missing"}],
        ):
            with self.subTest(overrides=overrides):
                self.assertFalse(click_change_policy.snapshot_is_valid(self.snapshot(overrides=overrides)))
        maximum = click_change_policy.MAX_DIRTY_PATHS
        overrides = [{"path": f"docs/{index:05d}.md", "identity": "missing"} for index in range(maximum + 1)]
        self.assertTrue(click_change_policy.snapshot_is_valid(self.snapshot(overrides=overrides[:maximum])))
        self.assertFalse(click_change_policy.snapshot_is_valid(self.snapshot(overrides=overrides)))

    def test_receipt_rejects_malformed_patterns_and_digest_values(self) -> None:
        receipt = {
            "provider": click_change_policy.PROVIDER_NAME,
            "config_digest": "a" * 64,
            "entry_digest": "b" * 64,
            "patterns": ["README.md", "docs/**"],
            "baseline": self.snapshot(),
        }
        self.assertTrue(click_change_policy.receipt_is_valid(receipt))
        for patterns in (
            None, {}, "README.md", [], [None], [True], [1], [[]], [{}],
            ["README.md", {}], ["README.md", "README.md"], ["docs/**", "README.md"],
        ):
            with self.subTest(patterns=patterns):
                self.assertFalse(click_change_policy.receipt_is_valid({**receipt, "patterns": patterns}))
        for field in ("config_digest", "entry_digest"):
            for value in (None, True, 1, 1.0, [], {}, "", "A" * 64, "a" * 63, "a" * 65):
                with self.subTest(field=field, value=value):
                    self.assertFalse(click_change_policy.receipt_is_valid({**receipt, field: value}))
        for value in (None, True, 1, [], {}, "", "A" * 64):
            with self.subTest(snapshot_digest=value):
                self.assertFalse(click_change_policy.receipt_is_valid({
                    **receipt, "baseline": {**receipt["baseline"], "digest": value},
                }))


class ClickChangePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "workspace"
        self.root.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=self.root, check=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "unit.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "guide.md").write_text("before\n", encoding="utf-8")
        (self.root / "README.md").write_text("before\n", encoding="utf-8")
        self.argv = ["python3", "-m", "pytest", "tests/unit"]
        self.checks = [
            {
                "evidence_id": "E1",
                "argv": self.argv,
                "class": "targeted",
            }
        ]
        self.write_policy(["README.md", "docs/**"])
        self.commit("baseline")

    def git_capture(self, cwd: Path, arguments: list[str]) -> bytes | None:
        completed = subprocess.run(
            [
                "git",
                "--no-pager",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                *arguments,
            ],
            cwd=cwd,
            capture_output=True,
            check=False,
        )
        return completed.stdout if completed.returncode == 0 else None

    def write_policy(
        self,
        patterns: list[str],
        *,
        argv: list[str] | None = None,
        extra_entries: list[dict[str, object]] | None = None,
    ) -> None:
        target = self.root / click_change_policy.CONFIG_RELATIVE_PATH
        target.parent.mkdir(exist_ok=True)
        entries: list[dict[str, object]] = [
            {
                "checks": [argv or self.argv],
                "reuse_if_only_changed": patterns,
            }
        ]
        entries.extend(extra_entries or [])
        target.write_text(
            json.dumps({"version": 1, "entries": entries}, indent=2) + "\n",
            encoding="utf-8",
        )

    def commit(self, message: str) -> None:
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Click Tests",
                "-c",
                "user.email=click-tests@example.invalid",
                "commit",
                "--quiet",
                "-m",
                message,
            ],
            cwd=self.root,
            check=True,
        )

    def baseline(self) -> dict[str, object]:
        receipts = click_change_policy.receipts_for_groups(
            self.root,
            {"source": self.checks},
            git_capture=self.git_capture,
        )
        self.assertIn("source", receipts)
        return receipts["source"]

    def decide(self, baseline: dict[str, object]) -> dict[str, object]:
        return click_change_policy.decide(
            self.root,
            self.checks,
            baseline,
            git_capture=self.git_capture,
        )

    def test_module_has_no_upward_runtime_dependency(self) -> None:
        source = Path(click_change_policy.__file__).read_text(encoding="utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        for forbidden in (
            "click_gate",
            "click_contract",
            "click_evidence",
            "click_state",
            "click_process",
            "platform_protocol",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(forbidden in name.split(".") for name in imported),
                    imported,
                )

    def test_declared_readme_change_is_reusable(self) -> None:
        baseline = self.baseline()
        (self.root / "README.md").write_text("after\n", encoding="utf-8")

        decision = self.decide(baseline)

        self.assertEqual(decision["status"], "reuse")
        self.assertEqual(decision["reason"], "all-paths-declared-safe")
        self.assertEqual(decision["changed_paths"], ["README.md"])
        self.assertRegex(str(decision["decision_digest"]), r"^[0-9a-f]{64}$")

    def test_nested_docs_add_delete_and_rename_are_reusable(self) -> None:
        baseline = self.baseline()
        (self.root / "docs" / "guide.md").unlink()
        (self.root / "docs" / "new.md").write_text("new\n", encoding="utf-8")

        decision = self.decide(baseline)

        self.assertEqual(decision["status"], "reuse")
        self.assertEqual(
            decision["changed_paths"], ["docs/guide.md", "docs/new.md"]
        )

    def test_code_change_forces_rerun(self) -> None:
        baseline = self.baseline()
        (self.root / "src" / "unit.py").write_text("VALUE = 2\n", encoding="utf-8")

        decision = self.decide(baseline)

        self.assertEqual(decision["status"], "rerun")
        self.assertEqual(decision["reason"], "path-not-declared-safe")
        self.assertEqual(decision["changed_paths"], ["src/unit.py"])

    def test_safe_and_unsafe_changes_together_force_rerun(self) -> None:
        baseline = self.baseline()
        (self.root / "README.md").write_text("after\n", encoding="utf-8")
        (self.root / "src" / "unit.py").write_text("VALUE = 2\n", encoding="utf-8")

        decision = self.decide(baseline)

        self.assertEqual(decision["status"], "rerun")
        self.assertEqual(decision["changed_paths"], ["README.md", "src/unit.py"])

    def test_committed_changes_are_compared_across_heads(self) -> None:
        baseline = self.baseline()
        (self.root / "docs" / "guide.md").write_text("committed\n", encoding="utf-8")
        self.commit("docs")

        decision = self.decide(baseline)

        self.assertEqual(decision["status"], "reuse")
        self.assertEqual(decision["changed_paths"], ["docs/guide.md"])

    def test_baseline_dirty_file_uses_its_effective_content(self) -> None:
        (self.root / "README.md").write_text("verified dirty baseline\n", encoding="utf-8")
        baseline = self.baseline()
        (self.root / "README.md").write_text("later\n", encoding="utf-8")

        decision = self.decide(baseline)

        self.assertEqual(decision["status"], "reuse")
        self.assertEqual(decision["changed_paths"], ["README.md"])

    def test_reverted_content_has_no_net_change(self) -> None:
        baseline = self.baseline()
        original = (self.root / "README.md").read_text(encoding="utf-8")
        (self.root / "README.md").write_text("temporary\n", encoding="utf-8")
        (self.root / "README.md").write_text(original, encoding="utf-8")

        decision = self.decide(baseline)

        self.assertEqual(decision["status"], "reuse")
        self.assertEqual(decision["reason"], "no-net-change")
        self.assertEqual(decision["changed_paths"], [])

    def test_uncommitted_policy_change_is_not_authority(self) -> None:
        baseline = self.baseline()
        self.write_policy(["README.md", "docs/**", "src/**"])
        (self.root / "src" / "unit.py").write_text("VALUE = 2\n", encoding="utf-8")

        decision = self.decide(baseline)

        self.assertEqual(decision["status"], "unknown")
        self.assertEqual(decision["reason"], "preflight-unavailable")

    def test_committed_policy_change_forces_one_rerun(self) -> None:
        baseline = self.baseline()
        self.write_policy(["README.md", "docs/**", "notes/**"])
        self.commit("policy")

        decision = self.decide(baseline)

        self.assertEqual(decision["status"], "rerun")
        self.assertEqual(decision["reason"], "policy-changed")

    def test_policy_cannot_mark_click_authority_files_safe(self) -> None:
        self.write_policy(["**"])
        self.commit("overbroad policy")

        receipts = click_change_policy.receipts_for_groups(
            self.root,
            {"source": self.checks},
            git_capture=self.git_capture,
        )

        self.assertEqual(receipts, {})

    def test_wrong_check_group_has_no_policy_receipt(self) -> None:
        receipts = click_change_policy.receipts_for_groups(
            self.root,
            {
                "source": [
                    {
                        "evidence_id": "E1",
                        "argv": ["python3", "-m", "pytest", "tests/other"],
                        "class": "targeted",
                    }
                ]
            },
            git_capture=self.git_capture,
        )

        self.assertEqual(receipts, {})

    def test_tampered_baseline_fails_closed(self) -> None:
        baseline = self.baseline()
        baseline["baseline"]["head"] = "0" * 40

        decision = self.decide(baseline)

        self.assertEqual(decision["status"], "unknown")

    def bound_decision(self):
        baseline = self.baseline()
        (self.root / "README.md").write_text("after\n", encoding="utf-8")
        git_root_output = self.git_capture(
            self.root, ["rev-parse", "--show-toplevel"]
        )
        self.assertIsNotNone(git_root_output)
        assert git_root_output is not None
        context = {
            "source_key": "1" * 64, "revision": 1,
            "git_root": os.path.normcase(os.fsdecode(git_root_output.strip())),
            "tree_digest": "2" * 64,
        }
        decision = click_change_policy.decide(
            self.root, self.checks, baseline,
            git_capture=self.git_capture, decision_context=context,
        )
        self.assertEqual(decision["status"], "reuse")
        return baseline, decision, context

    def test_bound_decision_matches_exact_check_baseline_and_current_scope(self) -> None:
        from hooks import click_verification_plan

        baseline, decision, context = self.bound_decision()
        digest = click_change_policy.group_digest(self.checks)
        self.assertEqual(digest, click_verification_plan.verification_group_digest(self.checks))
        with mock.patch.object(click_change_policy, "receipts_for_groups", side_effect=AssertionError("matching must not read files")), mock.patch.object(click_change_policy, "_load_policy", side_effect=AssertionError("matching must not reload policy")):
            self.assertTrue(click_change_policy.reuse_decision_matches(
                decision, baseline, check_digest=digest, decision_context=context,
            ))
        changed = {
            "source_key": "3" * 64, "revision": 2,
            "git_root": str(self.root / "other"), "tree_digest": "4" * 64,
        }
        for field, value in changed.items():
            with self.subTest(field=field):
                self.assertFalse(click_change_policy.reuse_decision_matches(
                    decision, baseline, check_digest=digest,
                    decision_context={**context, field: value},
                ))
        self.assertFalse(click_change_policy.reuse_decision_matches(
            decision, baseline, check_digest="5" * 64, decision_context=context,
        ))
        different_baseline = self.baseline()
        self.assertNotEqual(different_baseline, baseline)
        self.assertFalse(click_change_policy.reuse_decision_matches(
            decision, different_baseline, check_digest=digest, decision_context=context,
        ))

    def test_bound_decision_rejects_changed_payload_and_policy(self) -> None:
        baseline, decision, context = self.bound_decision()
        cases = []
        for field, value in (
            ("decision_digest", "f" * 64),
            ("changed_paths", ["src/unit.py"]),
            ("changed_paths", ["docs/guide.md"]),
            ("receipt", baseline),
        ):
            cases.append({**copy.deepcopy(decision), field: value})
        for field, value in (
            ("config_digest", "6" * 64), ("entry_digest", "7" * 64),
            ("patterns", ["README.md", "docs/**", "src/**"]),
        ):
            changed = copy.deepcopy(decision)
            changed["receipt"][field] = value
            cases.append(changed)
        for changed in cases:
            with self.subTest(changed=changed):
                self.assertFalse(click_change_policy.reuse_decision_matches(
                    changed, baseline, check_digest=click_change_policy.group_digest(self.checks),
                    decision_context=context,
                ))
        (self.root / "src/unit.py").write_text("VALUE = 2\n", encoding="utf-8")
        unsafe = click_change_policy.decide(
            self.root, self.checks, baseline,
            git_capture=self.git_capture, decision_context=context,
        )
        self.assertEqual(unsafe["status"], "rerun")
        unsafe["status"] = "reuse"
        self.assertFalse(click_change_policy.reuse_decision_matches(
            unsafe, baseline, check_digest=click_change_policy.group_digest(self.checks),
            decision_context=context,
        ))

    def test_unbound_or_malformed_context_is_not_reuse_authority(self) -> None:
        baseline, decision, context = self.bound_decision()
        unbound = self.decide(baseline)
        self.assertEqual(unbound["status"], "reuse")
        self.assertFalse(click_change_policy.reuse_decision_matches(
            unbound, baseline, check_digest=click_change_policy.group_digest(self.checks),
            decision_context=context,
        ))
        for malformed in (None, {}, [], {**context, "revision": True}, {**context, "revision": "1"}, {**context, "tree_digest": []}):
            with self.subTest(context=malformed):
                self.assertFalse(click_change_policy.reuse_decision_matches(
                    decision, baseline, check_digest=click_change_policy.group_digest(self.checks),
                    decision_context=malformed,
                ))
        for malformed in ({**context, "git_root": str(self.root / "other")}, {**context, "revision": True}):
            with self.subTest(producer_context=malformed):
                rejected = click_change_policy.decide(
                    self.root, self.checks, baseline,
                    git_capture=self.git_capture, decision_context=malformed,
                )
                self.assertEqual(rejected["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
