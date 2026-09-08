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
import venv
from unittest import mock

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
VITEST_SOURCE = ROOT / "tests" / "fixtures" / "vitest-v5"
VITEST_AVAILABLE = bool(
    shutil.which("node")
    and shutil.which("npx")
    and (VITEST_SOURCE / "node_modules" / "vitest" / "package.json").is_file()
)
JEST_SOURCE = ROOT / "tests" / "fixtures" / "jest-v30"
JEST_AVAILABLE = bool(
    shutil.which("node")
    and shutil.which("npx")
    and (JEST_SOURCE / "node_modules" / "jest" / "package.json").is_file()
)


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


def hardlink_or_copy(source: str, target: str) -> str:
    try:
        os.link(source, target)
        return target
    except OSError:
        return shutil.copy2(source, target)


def vitest_project(root: Path) -> list[str]:
    shutil.copytree(
        VITEST_SOURCE,
        root,
        dirs_exist_ok=True,
        symlinks=True,
        copy_function=hardlink_or_copy,
    )
    git_fixture(root)
    return ["npx", "--no-install", "vitest", "run"]


def jest_project(root: Path) -> list[str]:
    shutil.copytree(
        JEST_SOURCE,
        root,
        dirs_exist_ok=True,
        symlinks=True,
        ignore=shutil.ignore_patterns("node_modules"),
    )
    shutil.copytree(
        JEST_SOURCE / "node_modules",
        root / "node_modules",
        symlinks=True,
        copy_function=hardlink_or_copy,
    )
    git_fixture(root)
    return ["npx", "--no-install", "jest", "--runInBand"]


class CommandParsingTests(unittest.TestCase):
    def test_workspace_snapshot_batches_git_paths_and_preserves_metadata_content(self):
        with tempfile.TemporaryDirectory(prefix="click snapshot ") as directory:
            root = Path(directory)
            git_fixture(root)
            names = ("index", "HEAD", "config", "config.worktree", "packed-refs")
            expected = {}
            for name in names:
                path = Path(os.fsdecode(inventory.git_read(root, ["rev-parse", "--git-path", name])).strip())
                path = path if path.is_absolute() else root / path
                expected[".git/" + name] = inventory.hashlib.sha256(
                    path.read_bytes() if path.exists() else b"").hexdigest()
            with mock.patch.object(inventory, "git_read", wraps=inventory.git_read) as read:
                first = inventory.workspace_snapshot(root, inventory.Limits())
            self.assertEqual(read.call_count, 2)
            self.assertEqual({key: first[key] for key in expected}, expected)
            write(root, "new.txt", "new content")
            self.assertNotEqual(first, inventory.workspace_snapshot(root, inventory.Limits()))

    def test_workspace_snapshot_resolves_project_alias_before_symlink_boundary(self):
        with tempfile.TemporaryDirectory(prefix="click-snapshot-alias-") as directory:
            base = Path(directory)
            physical = base / "physical"
            physical.mkdir()
            write(physical, "value.txt", "value")
            (physical / "linked.txt").symlink_to("value.txt")
            alias = base / "alias"
            try:
                alias.symlink_to(physical, target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("directory symlinks are unavailable on this host")
            git_fixture(physical)

            snapshot = inventory.workspace_snapshot(alias, inventory.Limits())

            self.assertIn("linked.txt", snapshot)

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

    def test_vitest_parser_rejects_network_and_interactive_or_ambiguous_shapes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copy2(VITEST_SOURCE / "package.json", root / "package.json")
            shutil.copy2(
                VITEST_SOURCE / "package-lock.json", root / "package-lock.json"
            )
            write(root, "tests/case.test.js", "// collection fixture\n")
            accepted = inventory.parse_command(
                [
                    "npx",
                    "--no-install",
                    "vitest",
                    "run",
                    "tests/case.test.js",
                ],
                root,
                root,
            )
            self.assertEqual(accepted["selected_file"], "tests/case.test.js")

            rejected = (
                ["npx", "vitest", "run"],
                ["vitest", "run"],
                ["npx", "--no-install", "vitest"],
                ["npx", "--no-install", "vitest", "run", "--watch"],
                ["npx", "--no-install", "vitest", "run", "--update"],
                ["npx", "--no-install", "vitest", "run", "--browser"],
                ["npx", "--no-install", "vitest", "run", "tests"],
            )
            for command in rejected:
                with self.subTest(command=command), self.assertRaises(
                    inventory.AnalysisError
                ):
                    inventory.parse_command(command, root, root)

    def test_vitest_collection_output_and_timeout_fail_closed(self) -> None:
        spec = {
            "adapter": inventory.VITEST_ADAPTER,
            "version_prefix": ["npx", "--no-install", "vitest"],
            "collector_prefix": ["npx", "--no-install", "vitest", "list"],
            "selected_file": "",
        }
        version = b"vitest/5.0.0 linux-x64 node-v22.23.2\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = Path("/external/npx")
            polluted = mock.patch.object(
                inventory,
                "_capture_bounded_command",
                side_effect=[(version, b""), (b"[]", b"warning")],
            )
            with polluted, self.assertRaisesRegex(
                inventory.AnalysisError, "vitest-collection-output-polluted"
            ):
                inventory._vitest_collection(
                    root, root, spec, executable, inventory.Limits()
                )

            invalid = mock.patch.object(
                inventory,
                "_capture_bounded_command",
                side_effect=[(version, b""), (b"not-json", b"")],
            )
            with invalid, self.assertRaisesRegex(
                inventory.AnalysisError, "invalid-collector-result"
            ):
                inventory._vitest_collection(
                    root, root, spec, executable, inventory.Limits()
                )

            timeout = mock.patch.object(
                inventory,
                "_capture_bounded_command",
                side_effect=inventory.AnalysisError("collection-timeout"),
            )
            with timeout, self.assertRaisesRegex(
                inventory.AnalysisError, "collection-timeout"
            ):
                inventory._vitest_collection(
                    root, root, spec, executable, inventory.Limits()
                )

    @unittest.skipUnless(JEST_AVAILABLE, "pinned Jest fixture is unavailable")
    def test_jest_parser_rejects_network_mutating_dynamic_and_ambiguous_shapes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = jest_project(root)
            accepted = inventory.parse_command(
                [*command, "--runTestsByPath", "tests/regex+meta/value.test.js"],
                root,
                root,
            )
            self.assertEqual(
                accepted["selected_file"], "tests/regex+meta/value.test.js"
            )
            rejected = (
                ["npx", "jest", "--runInBand"],
                ["jest", "--runInBand"],
                ["npx", "--no-install", "jest"],
                [*command, "--watch"],
                [*command, "--watchAll"],
                [*command, "--updateSnapshot"],
                [*command, "tests"],
                [*command, "--findRelatedTests", "src/math.cjs"],
            )
            for argv in rejected:
                with self.subTest(argv=argv), self.assertRaises(inventory.AnalysisError):
                    inventory.parse_command(argv, root, root)
            package = json.loads((root / "package.json").read_text())
            package["jest"]["projects"] = ["<rootDir>"]
            (root / "package.json").write_text(json.dumps(package))
            with self.assertRaisesRegex(
                inventory.AnalysisError, "unsupported-jest-multi-project"
            ):
                inventory.parse_command(command, root, root)

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

    @unittest.skipUnless(VITEST_AVAILABLE, "pinned Vitest fixture is unavailable")
    def test_vitest_collection_is_repeatable_and_exact_file_selectors_are_safe(self):
        command = vitest_project(self.root)
        result = self.analyze(command)
        self.assertEqual(result["status"], "analysis-complete", result)
        self.assertEqual(result["adapter"], inventory.VITEST_ADAPTER)
        self.assertEqual(result["collection"]["loader"], "vitest.list-json")
        self.assertEqual(result["runtime"]["framework_version"], "5.0.0")
        self.assertEqual(
            sorted({item["file"] for item in result["inventory"]}),
            [
                "tests/integration/shared.test.js",
                "tests/types/value.test.ts",
                "tests/unit/shared.test.js",
            ],
        )

        child = self.analyze([*command, "tests/integration/shared.test.js"])
        self.assertEqual(child["status"], "analysis-complete", child)
        self.assertEqual(
            {item["file"] for item in child["inventory"]},
            {"tests/integration/shared.test.js"},
        )
        self.assertNotEqual(result["inventory_digest"], child["inventory_digest"])

        nested = self.analyze(
            [*command, "integration/shared.test.js"],
            cwd=self.root / "tests",
        )
        self.assertEqual(nested["status"], "analysis-complete", nested)
        self.assertEqual(
            {item["file"] for item in nested["inventory"]},
            {"tests/integration/shared.test.js"},
        )

        write(self.root, "vitest.config.js", "export default {}\n")
        unsupported = self.analyze(command)
        self.assertEqual(unsupported["status"], "unsupported", unsupported)
        self.assertEqual(unsupported["reasons"], ["unsupported-vitest-config"])

    @unittest.skipUnless(JEST_AVAILABLE, "pinned Jest fixture is unavailable")
    def test_jest_file_inventory_is_repeatable_and_run_by_path_is_exact(self):
        command = jest_project(self.root)
        result = self.analyze(command)
        self.assertEqual(result["status"], "analysis-complete", result)
        self.assertEqual(result["adapter"], inventory.JEST_ADAPTER)
        self.assertEqual(result["collection"]["loader"], "jest.list-tests-json")
        self.assertEqual(result["runtime"]["framework_version"], "30.5.1")
        self.assertEqual(len(result["inventory"]), 5)
        self.assertEqual(
            sorted(item["id"] for item in result["inventory"]),
            [
                "tests/integration/shared.test.js::file",
                "tests/regex+meta/value.test.js::file",
                "tests/snapshot/label.test.js::file",
                "tests/types/value.test.ts::file",
                "tests/unit/shared.test.cjs::file",
            ],
        )
        child = self.analyze(
            [
                *command,
                "--runTestsByPath",
                "tests/regex+meta/value.test.js",
            ]
        )
        self.assertEqual(child["status"], "analysis-complete", child)
        self.assertEqual(
            [item["file"] for item in child["inventory"]],
            ["tests/regex+meta/value.test.js"],
        )
        package = json.loads((self.root / "package.json").read_text())
        package["type"] = "module"
        (self.root / "package.json").write_text(json.dumps(package))
        unsupported = self.analyze(command)
        self.assertEqual(unsupported["status"], "unsupported", unsupported)
        self.assertEqual(unsupported["reasons"], ["unsupported-jest-esm"])

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

    def test_pytest_availability_uses_the_selected_interpreter(self):
        git_fixture(self.root)
        (self.root / "tests").mkdir()
        isolated = Path(self.temp.name) / "python-without-pytest"
        venv.EnvBuilder(with_pip=False).create(isolated)
        executable = isolated / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )

        result = self.analyze(
            [str(executable), "-m", "pytest", "-q", "tests"]
        )

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
