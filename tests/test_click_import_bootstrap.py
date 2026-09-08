from __future__ import annotations

import ast
import importlib
import os
from pathlib import Path
import subprocess
import sys
import unittest

from hooks import click_import_bootstrap


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
ENTRYPOINTS = (
    "click_gate.py",
    "antigravity_gate.py",
    "click_hook.py",
    "click_hook_worker.py",
    "click_windows.py",
)


class ClickImportBootstrapTests(unittest.TestCase):
    def test_gate_startup_does_not_load_optional_viewer_or_native_collectors(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", "-c",
             "import sys; import hooks.click_gate; "
             "names=('click_dashboard_server','click_dashboard_projection',"
             "'click_sharding_setup','click_observer_linux','click_observer_macos',"
             "'click_observer_windows','click_receipt_runtime'); "
             "assert not [n for n in names if 'hooks.'+n in sys.modules]"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_hook_client_does_not_import_the_heavy_gate(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                "import sys; import hooks.click_hook; "
                "assert 'hooks.click_gate' not in sys.modules",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_load_siblings_uses_the_active_package_context(self) -> None:
        (state_module,) = click_import_bootstrap.load_siblings(
            "hooks", "click_state"
        )

        self.assertIs(state_module, importlib.import_module("hooks.click_state"))

    def test_load_siblings_rejects_non_sibling_module_paths(self) -> None:
        for name in ("", ".click_state", "hooks.click_state", "../click_state"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    click_import_bootstrap.load_siblings("hooks", name)

    def test_entrypoints_only_branch_to_import_the_shared_bootstrap(self) -> None:
        for name in ENTRYPOINTS:
            with self.subTest(entrypoint=name):
                tree = ast.parse((HOOKS / name).read_text(encoding="utf-8"))
                branches = [
                    node
                    for node in tree.body
                    if isinstance(node, ast.If)
                    and isinstance(node.test, ast.Name)
                    and node.test.id == "__package__"
                ]
                self.assertEqual(len(branches), 1)
                branch = branches[0]
                self.assertEqual(len(branch.body), 1)
                self.assertEqual(len(branch.orelse), 1)
                package_import = branch.body[0]
                direct_import = branch.orelse[0]
                self.assertIsInstance(package_import, ast.ImportFrom)
                self.assertEqual(
                    [alias.name for alias in package_import.names],
                    ["click_import_bootstrap"],
                )
                self.assertIsInstance(direct_import, ast.Import)
                self.assertEqual(
                    [alias.name for alias in direct_import.names],
                    ["click_import_bootstrap"],
                )

    def test_entrypoints_import_in_package_and_direct_script_contexts(self) -> None:
        for name in ENTRYPOINTS:
            module_name = Path(name).stem
            with self.subTest(context="package", entrypoint=name):
                module = importlib.import_module(f"hooks.{module_name}")
                self.assertEqual(module.__package__, "hooks")

        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONNOUSERSITE": "1",
                "PYTHONPATH": str(HOOKS),
            }
        )
        module_names = ", ".join(Path(name).stem for name in ENTRYPOINTS)
        result = subprocess.run(
            [sys.executable, "-c", f"import {module_names}; print('ok')"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
