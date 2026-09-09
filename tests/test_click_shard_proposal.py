from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hooks import click_auto_sharding as cli
from hooks import click_shard_proposal as proposal
from hooks import click_test_inventory as inventory
try:
    from .test_click_auto_sharding import (
        PYTEST_AVAILABLE,
        JEST_AVAILABLE,
        ROOT,
        SUPPORTED,
        VITEST_AVAILABLE,
        library_project,
        nested_project,
        pytest_project,
        vitest_project,
        jest_project,
        write,
    )
except ImportError:  # unittest discovery with tests/ as the import root.
    from test_click_auto_sharding import (
        PYTEST_AVAILABLE,
        JEST_AVAILABLE,
        ROOT,
        SUPPORTED,
        VITEST_AVAILABLE,
        library_project,
        nested_project,
        pytest_project,
        vitest_project,
        jest_project,
        write,
    )


@unittest.skipUnless(SUPPORTED, "CPython 3.10-3.14 collection adapter")
class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="click-proposal-project ")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def generate(self, command, **kwargs):
        return proposal.propose(self.root, command, **kwargs)

    def assert_ready(self, value, count):
        self.assertEqual(value["status"], "proposal-ready", value["reasons"])
        self.assertTrue(value["proposal_ready"])
        self.assertFalse(value["reuse_ready"])
        self.assertFalse(value["authority"])
        timing = value["timing_samples"]
        self.assertEqual(
            timing["scope"], "proposal-analysis-wall-not-test-savings"
        )
        self.assertGreaterEqual(timing["parent_analysis_ms"], 0)
        self.assertEqual(
            timing["avoided_initial_child_scans"], len(value["children"])
        )
        self.assertEqual(
            timing["workspace_snapshot_scans"],
            6 + 3 * len(value["children"]),
        )
        self.assertEqual(value["equivalence"]["parent_count"], count)
        self.assertFalse(value["equivalence"]["semantic_equivalence_proven"])
        self.assertEqual(Counter(item["id"] for item in value["analysis"]["inventory"]),
                         Counter(item["id"] for child in value["children"] for item in child["inventory"]))
        self.assertFalse((self.root / ".click").exists())
        self.assertEqual(value["review_state"], "pending")

    def test_two_independent_projects_generate_without_json(self):
        command = library_project(self.root)
        before = inventory.workspace_snapshot(self.root, inventory.Limits())
        value = self.generate(command)
        self.assert_ready(value, 3)
        self.assertEqual(inventory.workspace_snapshot(self.root, inventory.Limits()), before)
        for entry in value["proposals"][".click/evidence-dependencies.json"]["entries"]:
            self.assertIn("retail/constants.py", entry["paths"])
            self.assertIn("pyproject.toml", entry["paths"])
        with tempfile.TemporaryDirectory(prefix="click-independent-science ") as tmp:
            root = Path(tmp)
            command = nested_project(root)
            second = proposal.propose(root, command)
            self.assert_ready(second, 2)
            self.assertTrue(all("-k" in child["argv"] and "works" in child["argv"]
                                for child in second["children"]))

    def test_regeneration_and_source_line_changes_keep_layout(self):
        command = library_project(self.root)
        first = self.generate(command)
        self.assert_ready(first, 3)
        repeated = self.generate(command)
        self.assert_ready(repeated, 3)
        self.assertEqual(
            {key: value for key, value in repeated.items() if key != "timing_samples"},
            {key: value for key, value in first.items() if key != "timing_samples"},
        )
        write(self.root, "retail/constants.py", "RATE = 3\n")
        second = self.generate(command)
        self.assert_ready(second, 3)
        self.assertNotEqual(first["workspace_digest"], second["workspace_digest"])
        self.assertEqual(first["proposals"], second["proposals"])

    def test_added_deleted_tests_require_new_inventory_and_exact_coverage(self):
        command = library_project(self.root)
        first = self.generate(command)
        write(self.root, "tests/test_added.py", """
            import unittest
            class Extra(unittest.TestCase):
                def test_added(self): pass
        """)
        second = self.generate(command)
        self.assert_ready(second, 4)
        self.assertNotEqual(first["inventory_digest"], second["inventory_digest"])
        first_ids = {row["id"] for row in first["layout"]}
        self.assertTrue(first_ids.issubset({row["id"] for row in second["layout"]}))
        (self.root / "tests/test_quantity.py").unlink()
        third = self.generate(command)
        self.assert_ready(third, 3)
        entry = third["proposals"][".click/evidence-shards.json"]["entries"][0]
        self.assertNotIn("tests/test_quantity.py", entry["inventory"])

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is unavailable")
    def test_pytest_nested_modules_generate_exact_children_and_regenerate(self):
        command = pytest_project(self.root)
        first = self.generate(command)
        self.assert_ready(first, 2)
        self.assertEqual(first["adapter"], inventory.PYTEST_ADAPTER)
        child_argv = [child["argv"] for child in first["children"]]
        self.assertIn(
            [sys.executable, "-m", "pytest", "-q", "tests/nested/test_beta.py"],
            child_argv,
        )
        self.assertIn(
            [sys.executable, "-m", "pytest", "-q", "tests/test_alpha.py"],
            child_argv,
        )
        write(self.root, "tests/test_added.py", "def test_added():\n    assert True\n")
        second = self.generate(command)
        self.assert_ready(second, 3)
        self.assertNotEqual(first["inventory_digest"], second["inventory_digest"])

    @unittest.skipUnless(VITEST_AVAILABLE, "pinned Vitest fixture is unavailable")
    def test_vitest_same_basenames_generate_exact_file_children(self) -> None:
        command = vitest_project(self.root)
        value = self.generate(command)
        self.assert_ready(value, 3)
        self.assertEqual(value["adapter"], inventory.VITEST_ADAPTER)
        self.assertEqual(
            [child["argv"] for child in value["children"]],
            [
                [*command, "tests/integration/shared.test.js"],
                [*command, "tests/types/value.test.ts"],
                [*command, "tests/unit/shared.test.js"],
            ],
        )
        self.assertEqual(
            [row["files"] for row in value["layout"]],
            [
                ["tests/integration/shared.test.js"],
                ["tests/types/value.test.ts"],
                ["tests/unit/shared.test.js"],
            ],
        )
        dependencies = value["proposals"][
            ".click/evidence-dependencies.json"
        ]["entries"]
        self.assertTrue(all("**" in entry["paths"] for entry in dependencies))

    @unittest.skipUnless(JEST_AVAILABLE, "pinned Jest fixture is unavailable")
    def test_jest_children_use_exact_run_tests_by_path_selectors(self) -> None:
        command = jest_project(self.root)
        value = self.generate(command)
        self.assert_ready(value, 5)
        self.assertEqual(value["adapter"], inventory.JEST_ADAPTER)
        argv = [child["argv"] for child in value["children"]]
        self.assertIn(
            [*command, "--runTestsByPath", "tests/integration/shared.test.js"],
            argv,
        )
        self.assertIn(
            [*command, "--runTestsByPath", "tests/unit/shared.test.cjs"],
            argv,
        )
        self.assertIn(
            [*command, "--runTestsByPath", "tests/regex+meta/value.test.js"],
            argv,
        )
        self.assertEqual(
            {item for row in value["layout"] for item in row["files"]},
            {child["inventory"][0]["file"] for child in value["children"]},
        )

    def test_pytest_child_command_keeps_the_full_nested_module_path(self):
        parent = {
            "adapter": inventory.PYTEST_ADAPTER,
            "command": {
                "cwd": ".",
                "runner_prefix": ["py", "-3.13", "-m", "pytest"],
                "child_options": ["-q", "--tb=short"],
            },
        }
        self.assertEqual(
            proposal.child_command(parent, "tests/nested/test_beta.py"),
            [
                "py", "-3.13", "-m", "pytest", "-q", "--tb=short",
                "tests/nested/test_beta.py",
            ],
        )

    def test_missing_duplicate_and_invalid_child_are_not_proposals(self):
        command = library_project(self.root)
        original = proposal.child_command
        variants = (
            lambda parent, name: original(parent, "test_invoice.py"),
            lambda parent, name: original(parent, "absent.py"),
            lambda parent, name: ["sh", "-c", "true"],
        )
        for changed in variants:
            with self.subTest(changed=changed), patch.object(proposal, "child_command", side_effect=changed):
                value = self.generate(command)
                self.assertFalse(value["proposal_ready"])
                self.assertFalse(value["proposals"])
        parent = {"inventory": [{"id": "one", "module": "a"}, {"id": "two", "module": "b"}]}
        with self.assertRaisesRegex(inventory.AnalysisError, "child-inventory-mismatch"):
            proposal.equivalence(parent, [{"inventory": parent["inventory"][:1]}])

    def test_whole_module_and_class_fixtures_preserved(self):
        command = library_project(self.root)
        write(self.root, "tests/test_invoice.py", """
            import unittest
            def setUpModule(): pass
            class Invoice(unittest.TestCase):
                @classmethod
                def setUpClass(cls): cls.value = 1
                def test_one(self): self.assertEqual(self.value, 1)
                def test_two(self): self.assertEqual(self.value, 1)
        """)
        value = self.generate(command)
        self.assert_ready(value, 3)
        matching = [child for child in value["children"]
                    if any(test["module"] == "test_invoice" for test in child["inventory"])]
        self.assertEqual(len(matching), 1)
        self.assertEqual(len(matching[0]["inventory"]), 2)

    def test_shared_fixture_and_global_state_require_review(self):
        command = nested_project(self.root)
        write(self.root, "checks/__init__.py", "def setUpModule(): pass\n")
        value = self.generate(command)
        self.assertEqual(value["reasons"], ["shared-module-fixture-needs-review"])
        self.assertFalse(value["proposal_ready"])
        write(self.root, "checks/__init__.py", "")
        write(self.root, "science/units/length.py", "STATE = []\ndef convert(value):\n    return value * 10\n")
        value = self.generate(command)
        self.assertEqual(value["reasons"], ["split-independence-needs-review"])
        self.assertFalse(value["proposals"])

    def test_same_basename_groups_modules_and_nonroot_cwd(self):
        command = nested_project(self.root)
        (self.root / "checks/nested/case_mass.py").rename(self.root / "checks/nested/case_distance.py")
        value = self.generate(command)
        self.assertEqual(value["reasons"], ["no-useful-module-split"])
        write(self.root, "checks/case_other.py", """
            import unittest
            class Other(unittest.TestCase):
                def test_works(self): pass
        """)
        value = self.generate(command)
        self.assert_ready(value, 3)
        self.assertEqual(sorted(len(row["files"]) for row in value["layout"]), [1, 2])
        nested_cwd = self.root / "checks"
        # Explicit import root retains science imports from the repository root.
        command = ["python3", "-m", "unittest", "discover", "-s", ".", "-t", "..",
                   "-p", "case_*.py", "-k", "works", "-q"]
        value = self.generate(command, cwd=nested_cwd)
        self.assert_ready(value, 3)
        self.assertTrue(all(child["cwd"] == "checks" for child in value["children"]))

    def test_existing_configs_preserved_and_dependency_scope_not_narrowed(self):
        command = library_project(self.root)
        old = {"version": 1, "entries": [{"checks": [command], "paths": ["retail/", "data/**"]}]}
        for name, value in (("evidence-dependencies.json", json.dumps(old)),
                            ("evidence-shards.json", "user edited shard settings\n"),
                            ("evidence-reuse.json", "user reuse settings\n")):
            write(self.root, ".click/" + name, value)
        before = inventory.workspace_snapshot(self.root, inventory.Limits())
        value = self.generate(command)
        self.assertEqual(value["status"], "review-required", value["reasons"])
        self.assertTrue(value["proposal_ready"])
        self.assertEqual(before, inventory.workspace_snapshot(self.root, inventory.Limits()))
        entries = value["proposals"][".click/evidence-dependencies.json"]["entries"]
        self.assertIn(old["entries"][0], entries)
        self.assertTrue(all({"retail/", "data/**"}.issubset(entry["paths"]) for entry in entries))
        self.assertNotIn(".click/evidence-reuse.json", value["proposals"])
        write(self.root, ".click/evidence-dependencies.json", "invalid json\n")
        value = self.generate(command)
        self.assertEqual(value["reasons"], ["existing-dependencies-invalid"])
        self.assertFalse(value["proposal_ready"])

    def test_unsupported_or_empty_split_and_bounds(self):
        command = library_project(self.root)
        (self.root / "tests/test_quantity.py").unlink()
        self.assertEqual(self.generate(command)["reasons"], ["no-useful-module-split"])
        write(self.root, "tests/test_empty.py", "")
        self.assertEqual(self.generate(command)["reasons"], ["child-collection-not-supported"])
        write(self.root, "tests/test_empty.py", "import unittest\nclass Empty(unittest.TestCase):\n    def test_one(self): pass\n")
        with patch.object(proposal, "MAX_SHARDS", 1):
            self.assertEqual(self.generate(command)["reasons"], ["shard-count-limit"])
        with patch.object(proposal, "MAX_CHILD_ARGUMENTS", 5):
            self.assertEqual(self.generate(command)["reasons"], ["child-command-limit"])
        write(self.root, "tests/no_package/test_invisible.py", "raise RuntimeError('never imported')\n")
        self.assertEqual(self.generate(command)["reasons"], ["undiscovered-file-matches-policy-inventory"])

    def test_refresh_keeps_parent_and_exact_child_dependencies_separate(self):
        command = library_project(self.root)
        first = self.generate(command)
        entries = first["proposals"][".click/evidence-dependencies.json"]["entries"]
        first_checks = entries[0]["checks"]
        entries[0]["paths"].append("invoice-data/**")
        parent = {"checks": [command], "paths": ["shared-data/**"]}
        unrelated = {"checks": [["python3", "-m", "unittest", "other"]],
                     "paths": ["unrelated-project/**"]}
        write(self.root, ".click/evidence-dependencies.json", json.dumps(
            {"version": 1, "entries": [*entries, parent, unrelated]}))
        refreshed = self.generate(command)
        self.assertTrue(refreshed["proposal_ready"], refreshed["reasons"])
        actual = refreshed["proposals"][".click/evidence-dependencies.json"]["entries"]
        self.assertIn(parent, actual)
        self.assertIn(unrelated, actual)
        for entry in actual:
            if entry["checks"] in [parent["checks"], unrelated["checks"]]:
                continue
            self.assertIn("shared-data/**", entry["paths"])
            self.assertNotIn("unrelated-project/**", entry["paths"])
            self.assertEqual("invoice-data/**" in entry["paths"],
                             entry["checks"] == first_checks)
            prior = next(old for old in entries if old["checks"] == entry["checks"])
            self.assertTrue(set(prior["paths"]).issubset(entry["paths"]))

        # Persisting and refreshing again must not spread a sibling's inputs.
        write(self.root, ".click/evidence-dependencies.json", json.dumps(
            {"version": 1, "entries": actual}))
        repeated = self.generate(command)
        self.assertEqual(actual, repeated["proposals"][".click/evidence-dependencies.json"]["entries"])

    def test_discovery_imports_contribute_dependencies(self):
        command = library_project(self.root)
        write(self.root, "retail/test_helpers.py", """
            import unittest
            class Imported(unittest.TestCase):
                def test_case(self): pass
        """)
        write(self.root, "tests/test_invoice.py", "from retail.test_helpers import Imported\nfrom retail.prices import total\n")
        value = self.generate(command)
        self.assert_ready(value, 2)
        invoice = next(row for row in value["layout"] if "tests/test_invoice.py" in row["files"])
        self.assertIn("retail/prices.py", invoice["dependency_candidates"])
        self.assertIn("retail/test_helpers.py", invoice["dependency_candidates"])

    def test_private_artifacts_and_shipped_propose_entrypoint(self):
        command = nested_project(self.root)
        value = self.generate(command)
        self.assert_ready(value, 2)
        directory = cli.save_proposal(value, self.root)
        try:
            self.assertFalse(inventory.inside(self.root, directory))
            if os.name != "nt":
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual(json.loads((directory / ".click/evidence-shards.json").read_text()),
                             value["proposals"][".click/evidence-shards.json"])
            self.assertFalse(json.loads((directory / "proposal.json").read_text())["authority"])
        finally:
            shutil.rmtree(directory)
        result = subprocess.run([sys.executable, "-B", str(ROOT / "dist/antigravity/hooks/click_auto_sharding.py"),
                                 "propose", "--project", str(self.root), "--", *command],
                                cwd=self.root, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = json.loads(result.stdout)
        directory = Path(output["artifact_directory"])
        try:
            self.assertEqual(output["status"], "proposal-ready")
            self.assertTrue((directory / ".click/evidence-dependencies.json").is_file())
            self.assertFalse((self.root / ".click").exists())
        finally:
            shutil.rmtree(directory)
