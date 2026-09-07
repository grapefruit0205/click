from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

from hooks import click_auto_sharding as cli
from hooks import click_test_inventory as inventory

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED = (
    inventory.click_collector_runtime.capability().status == "implemented"
    and inventory.click_collector_runtime.cpython_supported(
        sys.implementation.name, sys.version_info[:3]
    )
)
PYTEST_AVAILABLE = importlib.util.find_spec("pytest") is not None


def write(root: Path, name: str, content: str) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(textwrap.dedent(content).lstrip("\n"))


def git_fixture(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def library_project(root: Path) -> list[str]:
    git_fixture(root)
    write(root, "retail/__init__.py", "")
    write(root, "retail/prices.py", """
        from retail.constants import RATE
        def total(value):
            return value * RATE
    """)
    write(root, "retail/constants.py", "RATE = 2\n")
    write(root, "pyproject.toml", "[project]\nname = 'sample-retail'\n")
    write(root, "tests/test_invoice.py", """
        import unittest
        from retail.prices import total
        class Invoice(unittest.TestCase):
            def test_total(self):
                raise AssertionError("collection must not run this test")
            def test_empty(self):
                self.assertEqual(total(0), 0)
    """)
    write(root, "tests/test_quantity.py", """
        import unittest
        from retail.constants import RATE
        class Quantity(unittest.TestCase):
            def test_units(self):
                self.assertEqual(RATE, 2)
    """)
    return [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]


def nested_project(root: Path) -> list[str]:
    git_fixture(root)
    for path in ("science/__init__.py", "science/units/__init__.py",
                 "checks/__init__.py", "checks/nested/__init__.py"):
        write(root, path, "")
    write(root, "science/units/length.py", "def convert(value):\n    return value * 10\n")
    write(root, "checks/case_distance.py", """
        import unittest
        from science.units.length import convert
        class Distance(unittest.TestCase):
            def test_works(self):
                self.assertEqual(convert(2), 20)
            def test_filtered_out(self):
                pass
    """)
    write(root, "checks/nested/case_mass.py", """
        import unittest
        from science.units.length import convert
        class Mass(unittest.TestCase):
            def test_works(self):
                self.assertEqual(convert(3), 30)
    """)
    return [sys.executable, "-m", "unittest", "discover", "-s", "checks", "-t", ".",
            "-p", "case_*.py", "-k", "works", "-q"]


def pytest_project(root: Path) -> list[str]:
    git_fixture(root)
    write(root, "store/__init__.py", "")
    write(root, "store/prices.py", "def total(value):\n    return value * 2\n")
    write(root, "pyproject.toml", "[project]\nname = 'pytest-store'\n")
    write(root, "tests/test_alpha.py", """
        from store.prices import total
        def test_alpha():
            assert total(2) == 4
    """)
    write(root, "tests/nested/test_beta.py", """
        from store.prices import total
        def test_beta():
            assert total(3) == 6
    """)
    return [sys.executable, "-m", "pytest", "-q", "tests"]


class CommandParsingTests(unittest.TestCase):
    def test_missing_command_requires_selection_without_importing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "test_bad.py", "raise RuntimeError('must not import')\n")
            result = inventory.analyze(root, [])
            self.assertEqual(result["status"], "selection-required")
            self.assertFalse(result["reuse_ready"])

    def test_rejects_shell_and_unsupported_flags_without_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_fixture(root)
            for command in (
                "python3 -m unittest discover",
                ["sh", "-c", "python3 -m unittest discover"],
                ["python3", "-m", "pytest", "--collect-only"],
                ["python3", "-m", "pytest", "tests", "other-tests"],
                ["python3", "-m", "unittest", "discover", "-f"],
                ["python3", "-m", "unittest", "discover", "--random-option"],
            ):
                with self.subTest(command=command):
                    result = inventory.analyze(root, command)
                    self.assertEqual(result["status"], "unsupported")
                    self.assertEqual(result["inventory"], [])

    def test_no_parent_repository_borrowing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_fixture(root)
            nested = root / "another app"
            nested.mkdir()
            result = inventory.analyze(nested, ["python3", "-m", "unittest", "discover"])
            self.assertIn("non-git-project", result["reasons"])

    def test_path_and_top_level_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            root.mkdir()
            git_fixture(root)
            result = inventory.analyze(root, ["python3", "-m", "unittest", "discover",
                                               "-s", ".."])
            self.assertIn("project-boundary", result["reasons"])

    def test_repository_interpreter_shadow_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_fixture(root)
            local = root / "python3"
            local.write_text("#!/bin/sh\nexit 0\n")
            local.chmod(0o755)
            result = inventory.analyze(root, [str(local), "-m", "unittest", "discover"])
            self.assertIn("repository-executable", result["reasons"])
            with tempfile.TemporaryDirectory() as external:
                launcher = Path(external) / "python3"
                launcher.symlink_to(Path(sys.executable).resolve())
                selected = inventory.trusted_executable(
                    str(launcher), root, preserve_launcher=True
                )
                self.assertEqual(selected, launcher.absolute())
                self.assertEqual(selected.resolve(), Path(sys.executable).resolve())

    def test_positional_discovery_and_repeated_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            spec = inventory.parse_command(
                ["python3", "-m", "unittest", "discover", "tests", "case*.py", ".",
                 "-k", "one", "-k", "*two*", "-v"], root, root)
            self.assertEqual(
                spec,
                {
                    "adapter": inventory.UNITTEST_ADAPTER,
                    "runner_prefix": ["python3", "-m", "unittest", "discover"],
                    "start": "tests",
                    "top": ".",
                    "pattern": "case*.py",
                    "patterns": ["case*.py"],
                    "filters": ["one", "*two*"],
                    "verbosity": "-v",
                    "child_options": [],
                    "arguments": [
                        "tests", "case*.py", ".", "-k", "one", "-k", "*two*", "-v"
                    ],
                },
            )

    def test_pytest_parser_preserves_narrow_options_and_windows_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            spec = inventory.parse_command(
                ["py", "-3.13", "-m", "pytest", "-q", "--tb=short", "tests"],
                root,
                root,
            )
            self.assertEqual(spec["adapter"], inventory.PYTEST_ADAPTER)
            self.assertEqual(spec["runner_prefix"], ["py", "-3.13", "-m", "pytest"])
            self.assertEqual(spec["start"], "tests")
            self.assertEqual(spec["patterns"], ["test_*.py", "*_test.py"])
            self.assertEqual(spec["child_options"], ["-q", "--tb=short"])
            nested = root / "tests" / "nested"
            nested.mkdir()
            target = nested / "test_case.py"
            target.write_text("def test_case(): pass\n", encoding="utf-8")
            child = inventory.parse_command(
                ["python3", "-m", "pytest", "tests/nested/test_case.py"],
                root,
                root,
            )
            self.assertEqual(child["start"], "tests/nested/test_case.py")


@unittest.skipUnless(SUPPORTED, "positive collection profile requires CPython 3.10-3.14")
class RealCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="click-analysis-test-")
        self.root = Path(self.temp.name) / "source project with spaces"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def analyze(self, command, **kwargs):
        return inventory.analyze(self.root, command, **kwargs)

    def test_library_inventory_is_real_and_transitive(self):
        command = library_project(self.root)
        result = self.analyze(command)
        self.assertEqual(result["status"], "analysis-complete", result)
        self.assertEqual([item["id"] for item in result["inventory"]], [
            "test_invoice.Invoice.test_empty", "test_invoice.Invoice.test_total",
            "test_quantity.Quantity.test_units"])
        self.assertEqual(result["runtime"]["version"], list(sys.version_info[:3]))
        self.assertEqual(result["runtime"]["framework"], "unittest")
        deps = result["dependencies"]
        self.assertIn("retail/prices.py", deps["by_module"]["test_invoice"]["paths"])
        self.assertIn("retail/constants.py", deps["by_module"]["test_invoice"]["paths"])
        self.assertIn("pyproject.toml", deps["common_paths"])
        self.assertNotIn("tests/test_click_auto_sharding.py",
                         {item["file"] for item in result["inventory"]})
        self.assertFalse(result["authority"])
        self.assertFalse(result["reuse_ready"])
        self.assertFalse((self.root / ".click").exists())
        self.assertEqual(result["inventory_digest"], self.analyze(command)["inventory_digest"])

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is unavailable")
    def test_pytest_collection_is_real_repeatable_and_never_runs_tests(self):
        command = pytest_project(self.root)
        result = self.analyze(command)
        self.assertEqual(result["status"], "analysis-complete", result)
        self.assertEqual(result["adapter"], inventory.PYTEST_ADAPTER)
        self.assertEqual(result["collection"]["loader"], "pytest.collect-only")
        self.assertEqual(
            sorted(item["id"] for item in result["inventory"]),
            ["tests/nested/test_beta.py::test_beta", "tests/test_alpha.py::test_alpha"],
        )
        self.assertEqual(result["inventory_digest"], self.analyze(command)["inventory_digest"])
        self.assertFalse(result["authority"])
        self.assertFalse(result["reuse_ready"])

    @unittest.skipIf(PYTEST_AVAILABLE, "local pytest integration is covered above")
    def test_missing_pytest_is_an_explicit_safe_unsupported_result(self):
        git_fixture(self.root)
        (self.root / "tests").mkdir()
        result = self.analyze(["python3", "-m", "pytest", "-q", "tests"])
        self.assertEqual(result["status"], "unsupported", result)
        self.assertEqual(result["reasons"], ["pytest-unavailable"])
        self.assertEqual(result["inventory"], [])
        self.assertFalse(result["authority"])
        self.assertFalse(result["reuse_ready"])

    def test_second_independent_layout_nested_and_filter(self):
        result = self.analyze(nested_project(self.root))
        self.assertEqual(result["status"], "analysis-complete", result)
        self.assertEqual([x["id"] for x in result["inventory"]], [
            "checks.case_distance.Distance.test_works",
            "checks.nested.case_mass.Mass.test_works"])
        self.assertEqual(result["command"]["top"], ".")
        self.assertEqual(result["command"]["filters"], ["works"])
        self.assertIn("science/units/length.py", result["dependencies"]["common_paths"])

    def test_unknowns_and_literal_data_are_preserved(self):
        command = library_project(self.root)
        write(self.root, "retail/prices.py", """
            import time
            import random
            import importlib
            import subprocess
            import socket
            import sqlite3
            import multiprocessing
            from pathlib import Path
            def total(value):
                Path("fixtures/rates.json").read_text()
                open("data.txt").read()
                open(value).read()
                return importlib.import_module(value)
        """)
        write(self.root, "fixtures/rates.json", "{}\n")
        result = self.analyze(command)
        self.assertEqual(result["status"], "analysis-complete", result)
        reasons = {item["reason"] for item in result["dependencies"]["unknown"]}
        self.assertTrue({"time-random", "dynamic-import", "subprocess",
                         "external-service", "database", "untracked-ipc", "unresolved-file-access"} <= reasons)
        self.assertIn("fixtures/rates.json",
                      result["dependencies"]["by_module"]["test_invoice"]["paths"])
        self.assertFalse(result["dependencies"]["authority"])

    def test_import_error_is_not_success_and_does_not_leak_exception(self):
        command = library_project(self.root)
        write(self.root, "tests/test_invoice.py", "raise ValueError('SECRET-COLLECTION-TEXT')\n")
        result = self.analyze(command)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("import-error", result["reasons"])
        self.assertNotIn("SECRET-COLLECTION-TEXT", json.dumps(result))

    def test_zero_tests(self):
        git_fixture(self.root)
        write(self.root, "test_empty.py", "VALUE = 1\n")
        result = self.analyze(["python3", "-m", "unittest", "discover"])
        self.assertIn("zero-tests", result["reasons"])
        self.assertEqual(result["status"], "blocked")

    def test_empty_discovered_module_is_recorded_and_parent_arguments_are_preserved(self):
        command = library_project(self.root)
        write(self.root, "tests/test_empty.py", "VALUE = 1\n")
        write(self.root, "tests/test_argv.py", """
            import sys
            import unittest
            if sys.argv[1:] != ["discover", "-s", "tests", "-q"]:
                raise RuntimeError("wrong parent arguments")
            class Arguments(unittest.TestCase):
                def test_observed(self):
                    pass
        """)
        result = self.analyze(command)
        self.assertEqual(result["status"], "analysis-complete", result)
        self.assertIn({"module": "test_empty", "file": "tests/test_empty.py"},
                      result["collection"]["module_files"])
        self.assertIn("process-environment-introspection",
                      {item["reason"] for item in result["dependencies"]["unknown"]})

    def test_load_tests_and_duplicate_ids(self):
        command = library_project(self.root)
        write(self.root, "tests/test_invoice.py", """
            import unittest
            class Invoice(unittest.TestCase):
                def test_once(self):
                    pass
            def load_tests(loader, suite, pattern):
                test = Invoice("test_once")
                return unittest.TestSuite([test, test])
        """)
        result = self.analyze(command)
        self.assertIn("load-tests", result["reasons"])
        self.assertIn("duplicate-id", result["reasons"])
        self.assertNotEqual(result["status"], "analysis-complete")

    def test_custom_loader(self):
        command = library_project(self.root)
        write(self.root, "tests/test_invoice.py", """
            import unittest
            original = unittest.TestLoader.getTestCaseNames
            def different(self, test_class):
                return original(self, test_class)
            unittest.TestLoader.getTestCaseNames = different
            class Invoice(unittest.TestCase):
                def test_one(self):
                    pass
        """)
        result = self.analyze(command)
        self.assertIn("custom-loader", result["reasons"])
        self.assertNotEqual(result["status"], "analysis-complete")

    def test_dynamic_and_unstable_test_ids(self):
        command = library_project(self.root)
        write(self.root, "tests/test_invoice.py", """
            import unittest
            import uuid
            class Invoice(unittest.TestCase):
                def test_one(self):
                    pass
            Invoice.__name__ = Invoice.__qualname__ = "Generated" + uuid.uuid4().hex
        """)
        result = self.analyze(command)
        self.assertIn("dynamic-test", result["reasons"])
        self.assertIn("unstable-test-id", result["reasons"])
        self.assertNotEqual(result["status"], "analysis-complete")

    def test_custom_id(self):
        command = library_project(self.root)
        write(self.root, "tests/test_invoice.py", """
            import unittest
            class Invoice(unittest.TestCase):
                def id(self):
                    return "SECRET-ID-CONTENT"
                def test_one(self):
                    pass
        """)
        result = self.analyze(command)
        self.assertIn("custom-test-id", result["reasons"])
        self.assertNotIn("SECRET-ID-CONTENT", json.dumps(result))

    def test_collection_mutation_detected_including_ignored_files_no_revert(self):
        command = library_project(self.root)
        write(self.root, ".gitignore", "*.log\n")
        write(self.root, "scratch.log", "original\n")
        write(self.root, "tests/test_invoice.py", """
            from pathlib import Path
            Path("scratch.log").write_text("changed")
            import unittest
            class Invoice(unittest.TestCase):
                def test_one(self):
                    pass
        """)
        result = self.analyze(command)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("collection-mutated-project", result["reasons"])
        self.assertEqual((self.root / "scratch.log").read_text(), "changed")

    def test_timeout_and_excessive_output_are_failures(self):
        command = library_project(self.root)
        write(self.root, "tests/test_invoice.py", "import time\ntime.sleep(5)\n")
        result = self.analyze(command, limits=inventory.Limits(timeout=0.15))
        self.assertIn("collection-timeout", result["reasons"])
        write(self.root, "tests/test_invoice.py", "print('private-output' * 10000)\n")
        result = self.analyze(command, limits=inventory.Limits(output_bytes=1000))
        self.assertIn("collection-output-limit", result["reasons"])
        self.assertNotIn("private-output", json.dumps(result))

    def test_external_symlink_is_explicitly_unsupported(self):
        command = library_project(self.root)
        (self.root / "external").symlink_to(Path(sys.executable).resolve())
        result = self.analyze(command)
        self.assertIn("external-symlink", result["reasons"])

    def test_cli_writes_candidate_artifact_outside_project(self):
        command = nested_project(self.root)
        result = subprocess.run(
            [sys.executable, str(ROOT / "hooks/click_auto_sharding.py"), "analyze",
             "--project", str(self.root), "--", *command],
            cwd=ROOT, text=True, capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        output = json.loads(result.stdout)
        directory = Path(output["artifact_directory"])
        try:
            self.assertFalse(inventory.inside(self.root, directory))
            data = json.loads((directory / "analysis.json").read_text())
            self.assertEqual(data["status"], "analysis-complete")
            self.assertFalse(data["authority"])
            self.assertFalse(data["reuse_ready"])
        finally:
            shutil.rmtree(directory)

    def test_shipped_entrypoint_collects_target_not_click(self):
        command = nested_project(self.root)
        result = subprocess.run(
            [sys.executable, str(ROOT / "dist/antigravity/hooks/click_auto_sharding.py"),
             "analyze", "--project", str(self.root), "--", *command],
            cwd=self.root, text=True, capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        output = json.loads(result.stdout)
        directory = Path(output["artifact_directory"])
        try:
            data = json.loads((directory / "analysis.json").read_text())
            self.assertEqual(len(data["inventory"]), 2)
            self.assertTrue(all(test["file"].startswith("checks/")
                                for test in data["inventory"]))
        finally:
            shutil.rmtree(directory)


if __name__ == "__main__":
    unittest.main()
