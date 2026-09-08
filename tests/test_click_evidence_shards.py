from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from hooks import click_evidence, click_evidence_shards


class ClickEvidenceShardsTests(unittest.TestCase):
    def test_antigravity_distribution_contains_the_exact_shard_module(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            (root / "hooks" / "click_evidence_shards.py").read_bytes(),
            (
                root
                / "dist"
                / "antigravity"
                / "hooks"
                / "click_evidence_shards.py"
            ).read_bytes(),
        )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "workspace"
        self.root.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=self.root, check=True)
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_alpha.py").write_text("# alpha\n", encoding="utf-8")
        (tests / "test_beta.py").write_text("# beta\n", encoding="utf-8")
        self.parent_argv = [
            "python3",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-q",
        ]
        self.write_manifest()
        self.commit()

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

    def commit(self) -> None:
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
                "fixture",
            ],
            cwd=self.root,
            check=True,
        )

    def manifest(self) -> dict[str, object]:
        return {
            "version": 1,
            "entries": [
                {
                    "checks": [self.parent_argv],
                    "inventory": ["tests/test_*.py"],
                    "shards": [
                        {
                            "id": "alpha",
                            "checks": [
                                ["python3", "-m", "unittest", "tests.test_alpha", "-q"]
                            ],
                            "covers": ["tests/test_alpha.py"],
                        },
                        {
                            "id": "beta",
                            "checks": [
                                ["python3", "-m", "unittest", "tests.test_beta", "-q"]
                            ],
                            "covers": ["tests/test_beta.py"],
                        },
                    ],
                }
            ],
        }

    def write_manifest(self, value: dict[str, object] | None = None) -> None:
        target = self.root / click_evidence_shards.CONFIG_RELATIVE_PATH
        target.parent.mkdir(exist_ok=True)
        target.write_text(
            json.dumps(value or self.manifest(), indent=2) + "\n",
            encoding="utf-8",
        )

    def parent_checks(self) -> list[dict[str, object]]:
        return [
            {"evidence_id": "E1", "argv": self.parent_argv, "class": "broad"}
        ]

    def resolve(self) -> dict[str, object]:
        return click_evidence_shards.resolve_plan(
            self.root,
            self.parent_checks(),
            parent_source_key=click_evidence.evidence_key("E1"),
            git_capture=self.git_capture,
        )

    def test_committed_exact_map_resolves_stable_complete_children(self) -> None:
        first = self.resolve()
        second = self.resolve()

        self.assertEqual(first, second)
        self.assertEqual(first["status"], "sharded")
        children = first["children"]
        self.assertEqual([child["shard_id"] for child in children], ["alpha", "beta"])
        self.assertEqual(len({child["source_key"] for child in children}), 2)
        self.assertRegex(first["plan_digest"], r"^[0-9a-f]{64}$")

    def test_edited_map_and_inventory_drift_fall_back_to_parent(self) -> None:
        target = self.root / click_evidence_shards.CONFIG_RELATIVE_PATH
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        edited = self.resolve()
        self.assertEqual(edited["status"], "fallback")
        self.assertEqual(edited["reason"], "manifest-working-copy-mismatch")

        subprocess.run(
            ["git", "restore", click_evidence_shards.CONFIG_RELATIVE_PATH],
            cwd=self.root,
            check=True,
        )
        (self.root / "tests" / "test_gamma.py").write_text("# gamma\n", encoding="utf-8")
        drifted = self.resolve()
        self.assertEqual(drifted["status"], "fallback")
        self.assertEqual(drifted["reason"], "inventory-not-covered-exactly-once")

    def test_default_unittest_discovery_cannot_be_narrower_than_inventory(
        self,
    ) -> None:
        # unittest discover defaults to test*.py, not test_*.py.  An untracked
        # file is executable by the parent runner and therefore must prevent
        # decomposition when the committed shard inventory omits it.
        (self.root / "tests" / "testfoo.py").write_text(
            "# discovered by the parent only\n", encoding="utf-8"
        )

        plan = self.resolve()

        self.assertEqual(plan["status"], "fallback")
        self.assertEqual(plan["reason"], "inventory-narrower-than-parent-discovery")

    def test_explicit_unittest_pattern_bounds_parent_discovery(self) -> None:
        (self.root / "tests" / "testfoo.py").write_text(
            "# outside the explicit parent pattern\n", encoding="utf-8"
        )
        self.parent_argv = [
            "python3",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_*.py",
            "-q",
        ]
        self.write_manifest()
        self.commit()

        plan = self.resolve()

        self.assertEqual(plan["status"], "sharded")

    def test_pytest_default_discovery_is_exact_and_new_file_falls_back(self) -> None:
        self.parent_argv = ["python3", "-m", "pytest", "-q", "tests"]
        value = self.manifest()
        value["entries"][0]["shards"] = [
            {
                "id": "alpha",
                "checks": [["python3", "-m", "pytest", "-q", "tests/test_alpha.py"]],
                "covers": ["tests/test_alpha.py"],
            },
            {
                "id": "beta",
                "checks": [["python3", "-m", "pytest", "-q", "tests/test_beta.py"]],
                "covers": ["tests/test_beta.py"],
            },
        ]
        self.write_manifest(value)
        self.commit()
        self.assertEqual(self.resolve()["status"], "sharded")

        (self.root / "tests" / "new_test.py").write_text(
            "# discovered by pytest\n", encoding="utf-8"
        )
        changed = self.resolve()
        self.assertEqual(changed["status"], "fallback")
        self.assertEqual(changed["reason"], "inventory-narrower-than-parent-discovery")

    def test_vitest_new_file_and_ambiguous_command_fall_back_to_parent(self) -> None:
        self.parent_argv = ["npx", "--no-install", "vitest", "run"]
        value = self.manifest()
        value["entries"][0]["inventory"] = [
            "tests/alpha.test.js",
            "tests/nested/alpha.test.js",
        ]
        value["entries"][0]["shards"] = [
            {
                "id": "alpha",
                "checks": [[*self.parent_argv, "tests/alpha.test.js"]],
                "covers": ["tests/alpha.test.js"],
            },
            {
                "id": "nested-alpha",
                "checks": [[*self.parent_argv, "tests/nested/alpha.test.js"]],
                "covers": ["tests/nested/alpha.test.js"],
            },
        ]
        (self.root / "tests" / "test_alpha.py").unlink()
        (self.root / "tests" / "test_beta.py").unlink()
        (self.root / "tests" / "nested").mkdir()
        (self.root / "tests" / "alpha.test.js").write_text("// alpha\n")
        (self.root / "tests" / "nested" / "alpha.test.js").write_text(
            "// nested alpha\n"
        )
        self.write_manifest(value)
        self.commit()

        self.assertEqual(self.resolve()["status"], "sharded")
        (self.root / "tests" / "new.test.ts").write_text("// new\n")
        changed = self.resolve()
        self.assertEqual(changed["status"], "fallback")
        self.assertEqual(
            changed["reason"], "inventory-narrower-than-parent-discovery"
        )

        self.parent_argv = [
            "npx", "--no-install", "vitest", "run", "tests", "nested"
        ]
        (self.root / "tests" / "new.test.ts").unlink()
        value["entries"][0]["checks"] = [self.parent_argv]
        self.write_manifest(value)
        self.commit()
        ambiguous = self.resolve()
        self.assertEqual(ambiguous["status"], "fallback")
        self.assertEqual(
            ambiguous["reason"], "parent-discovery-arguments-unsupported"
        )

    def test_jest_new_file_and_ambiguous_options_fall_back_to_parent(self) -> None:
        self.parent_argv = ["npx", "--no-install", "jest", "--runInBand"]
        value = self.manifest()
        value["entries"][0]["inventory"] = [
            "tests/alpha.test.js",
            "tests/regex+meta/alpha.test.js",
        ]
        value["entries"][0]["shards"] = [
            {
                "id": "alpha",
                "checks": [[
                    *self.parent_argv,
                    "--runTestsByPath",
                    "tests/alpha.test.js",
                ]],
                "covers": ["tests/alpha.test.js"],
            },
            {
                "id": "regex-alpha",
                "checks": [[
                    *self.parent_argv,
                    "--runTestsByPath",
                    "tests/regex+meta/alpha.test.js",
                ]],
                "covers": ["tests/regex+meta/alpha.test.js"],
            },
        ]
        (self.root / "tests" / "test_alpha.py").unlink()
        (self.root / "tests" / "test_beta.py").unlink()
        (self.root / "tests" / "regex+meta").mkdir()
        (self.root / "tests" / "alpha.test.js").write_text("// alpha\n")
        (self.root / "tests" / "regex+meta" / "alpha.test.js").write_text(
            "// exact metacharacter path\n"
        )
        self.write_manifest(value)
        self.commit()
        self.assertEqual(self.resolve()["status"], "sharded")

        (self.root / "tests" / "new.test.ts").write_text("// new\n")
        changed = self.resolve()
        self.assertEqual(changed["status"], "fallback")
        self.assertEqual(
            changed["reason"], "inventory-narrower-than-parent-discovery"
        )

        (self.root / "tests" / "new.test.ts").unlink()
        self.parent_argv = [
            "npx", "--no-install", "jest", "--runInBand", "--watchAll"
        ]
        value["entries"][0]["checks"] = [self.parent_argv]
        self.write_manifest(value)
        self.commit()
        ambiguous = self.resolve()
        self.assertEqual(ambiguous["status"], "fallback")
        self.assertEqual(
            ambiguous["reason"], "parent-discovery-arguments-unsupported"
        )

    def test_ignored_parent_discovery_member_also_forces_fallback(self) -> None:
        (self.root / ".gitignore").write_text(
            "tests/testignored.py\n", encoding="utf-8"
        )
        self.commit()
        (self.root / "tests" / "testignored.py").write_text(
            "# ignored by Git but executable by unittest\n", encoding="utf-8"
        )

        plan = self.resolve()

        self.assertEqual(plan["status"], "fallback")
        self.assertEqual(plan["reason"], "inventory-narrower-than-parent-discovery")

    def test_overlap_or_unsupported_child_is_not_decomposition_authority(self) -> None:
        value = self.manifest()
        shards = value["entries"][0]["shards"]
        shards[0]["covers"] = ["tests/test_*.py"]
        self.write_manifest(value)
        self.commit()

        overlap = self.resolve()
        self.assertEqual(overlap["status"], "fallback")
        self.assertEqual(overlap["reason"], "inventory-not-covered-exactly-once")

    def test_activation_uses_regular_sources_and_tampering_fails_closed(self) -> None:
        plan = self.resolve()
        contract = {
            "verification": {
                "evidence": [
                    {
                        "id": "E1",
                        "kind": "argv",
                        "description": "suite",
                        "dependencies": ["src/"],
                    }
                ]
            }
        }
        state = {
            "state_schema_version": 2,
            "status": "approved",
            "evidence_state": click_evidence.fresh_state(contract),
        }
        parent_key = click_evidence.evidence_key("E1")

        sources, error = click_evidence.activate_shard_plan(state, parent_key, plan)

        self.assertEqual(error, "")
        assert sources is not None
        self.assertNotIn(parent_key, sources)
        self.assertEqual(len(sources), 2)
        self.assertTrue(
            all(click_evidence_shards.source_metadata_is_valid(source["shard"])
                for source in sources.values())
        )
        self.assertEqual(
            click_evidence.sources_from_state(
                state, expected_contract_schema_version=2
            ),
            sources,
        )

        tampered = copy.deepcopy(state)
        next(iter(tampered["evidence_state"]["sources"].values()))[
            "reserved_check_digest"
        ] = hashlib.sha256(b"wrong").hexdigest()
        self.assertEqual(
            click_evidence.sources_from_state(
                tampered, expected_contract_schema_version=2
            ),
            {},
        )

        restored, error = click_evidence.collapse_shard_plan(state, parent_key)
        self.assertEqual(error, "")
        assert restored is not None
        self.assertEqual(set(restored), {parent_key})
        self.assertEqual(restored[parent_key]["status"], "ready")

    def test_runner_revalidates_committed_plan_before_execution(self) -> None:
        plan = self.resolve()
        contract = {
            "verification": {
                "evidence": [{"id": "E1", "kind": "argv", "description": "suite"}]
            }
        }
        state = {
            "state_schema_version": 2,
            "status": "approved",
            "evidence_state": click_evidence.fresh_state(contract),
        }
        parent_key = click_evidence.evidence_key("E1")
        sources, error = click_evidence.activate_shard_plan(state, parent_key, plan)
        self.assertEqual(error, "")
        assert sources is not None
        grouped = {
            child["source_key"]: [
                {
                    "evidence_id": child["evidence_id"],
                    "argv": child["checks"][0],
                    "class": "broad",
                }
            ]
            for child in plan["children"]
        }
        self.assertEqual(
            click_evidence_shards.running_plan_error(
                self.root,
                state["evidence_state"],
                grouped,
                git_capture=self.git_capture,
            ),
            "",
        )

        target = self.root / click_evidence_shards.CONFIG_RELATIVE_PATH
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertIn(
            "authority changed",
            click_evidence_shards.running_plan_error(
                self.root,
                state["evidence_state"],
                grouped,
                git_capture=self.git_capture,
            ),
        )


if __name__ == "__main__":
    unittest.main()
