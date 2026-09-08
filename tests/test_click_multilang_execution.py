from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import unittest
from unittest import mock

from tests.click_gate_test_support import ClickGateTestCase, split_runner_command


NODE = shutil.which("node")
NPM = shutil.which("npm")
GO = shutil.which("go")
NPX = shutil.which("npx")
VITEST_SOURCE = Path(__file__).resolve().parent / "fixtures" / "vitest-v5"
VITEST_AVAILABLE = bool(
    NODE
    and NPX
    and (VITEST_SOURCE / "node_modules" / "vitest" / "package.json").is_file()
)
JEST_SOURCE = Path(__file__).resolve().parent / "fixtures" / "jest-v30"
JEST_AVAILABLE = bool(
    NODE
    and NPX
    and (JEST_SOURCE / "node_modules" / "jest" / "package.json").is_file()
)


class MultilangExecutionHookTests(ClickGateTestCase):
    def assert_runner(self, payload: dict, expected: bool = True) -> None:
        command = payload["hookSpecificOutput"]["updatedInput"]["command"]
        self.assertEqual("run-verification" in split_runner_command(command), expected)

    def mutate(self, path: Path, content: str, tool_id: str) -> None:
        self.assertIsNone(
            self.pre_tool(
                "apply_patch",
                "*** Begin Patch\n*** End Patch",
                "turn-1",
                submit_prompt=False,
                tool_use_id=tool_id,
            )
        )
        path.write_text(content, encoding="utf-8")
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": f"changed {path.name}"},
            turn_id="turn-1",
            tool_use_id=tool_id,
        )

    def node_project(self) -> tuple[Path, list[str]]:
        project = self.workspace / "프로젝트 space"
        (project / "src").mkdir(parents=True)
        (project / "test").mkdir()
        (project / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        (project / "package.json").write_text(
            json.dumps(
                {
                    "name": "click-node-fixture",
                    "private": True,
                    "type": "module",
                    "scripts": {
                        "test": (
                            "node --test test/math.test.mjs test/common.test.cjs"
                        )
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (project / "package-lock.json").write_text(
            json.dumps(
                {
                    "name": "click-node-fixture",
                    "lockfileVersion": 3,
                    "requires": True,
                    "packages": {"": {"name": "click-node-fixture"}},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (project / "src" / "math.mjs").write_text(
            "export function add(a, b) { return a + b; }\n", encoding="utf-8"
        )
        (project / "test" / "math.test.mjs").write_text(
            "import test from 'node:test';\n"
            "import assert from 'node:assert/strict';\n"
            "import { add } from '../src/math.mjs';\n"
            "test('esm add', () => assert.equal(add(2, 2), 4));\n",
            encoding="utf-8",
        )
        (project / "test" / "common.test.cjs").write_text(
            "const test = require('node:test');\n"
            "const assert = require('node:assert/strict');\n"
            "test('cjs runtime', () => assert.equal(2 + 2, 4));\n",
            encoding="utf-8",
        )
        self.workspace = project
        self.base_event["cwd"] = str(project)
        self.initialize_git(
            ".gitignore", "package.json", "package-lock.json", "src", "test"
        )
        return project, ["node", "--test", "test/math.test.mjs", "test/common.test.cjs"]

    def vitest_project(self) -> tuple[Path, list[str]]:
        project = self.workspace / "vitest project"
        shutil.copytree(VITEST_SOURCE, project, symlinks=True)
        self.workspace = project
        self.base_event["cwd"] = str(project)
        self.initialize_git(
            ".gitignore", "package.json", "package-lock.json", "src", "tests"
        )
        return project, ["npx", "--no-install", "vitest", "run"]

    def jest_project(self) -> tuple[Path, list[str]]:
        project = self.workspace / "jest project"
        shutil.copytree(JEST_SOURCE, project, symlinks=True)
        self.workspace = project
        self.base_event["cwd"] = str(project)
        self.initialize_git(
            ".gitignore", "package.json", "package-lock.json", "src", "tests"
        )
        return project, ["npx", "--no-install", "jest", "--runInBand"]

    @unittest.skipUnless(NODE, "Node is not installed")
    def test_node_test_real_runner_reuse_failure_and_fix(self) -> None:
        project, command = self.node_project()
        first = self.verify_gate([command], "turn-1")
        self.assert_runner(first)
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        reused = self.verify_gate([command], "turn-1")
        self.assert_runner(reused, False)

        source = project / "src" / "math.mjs"
        self.mutate(
            source,
            "export function add(a, b) { return a - b; }\n",
            "node-fail",
        )
        failing = self.verify_gate([command], "turn-1")
        self.assert_runner(failing)
        self.assertNotEqual(self.run_rewritten(failing).returncode, 0)

        self.mutate(
            source,
            "export function add(a, b) { return a + b; }\n",
            "node-fix",
        )
        fixed = self.verify_gate([command], "turn-1")
        self.assert_runner(fixed)
        self.assertEqual(self.run_rewritten(fixed).returncode, 0)

    @unittest.skipUnless(NODE and NPM, "Node and npm are not installed")
    def test_npm_script_binds_node_manifest_lock_and_runs_without_install(self) -> None:
        project, _ = self.node_project()
        command = ["npm", "test", "--silent"]
        first = self.verify_gate([command], "turn-1")
        self.assert_runner(first)
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        reused = self.verify_gate([command], "turn-1")
        self.assert_runner(reused, False)

        package = project / "package.json"
        value = json.loads(package.read_text(encoding="utf-8"))
        value["scripts"]["test"] = value["scripts"]["test"].replace(
            "node --test ", "node --test --test-reporter=tap "
        )
        self.mutate(package, json.dumps(value, indent=2) + "\n", "npm-config")
        changed = self.verify_gate([command], "turn-1")
        self.assert_runner(changed)
        self.assertEqual(self.run_rewritten(changed).returncode, 0)

    @unittest.skipUnless(NODE and NPM, "Node and npm are not installed")
    def test_npm_lifecycle_hook_executes_but_never_reuses_incomplete_identity(self) -> None:
        project, _ = self.node_project()
        package = project / "package.json"
        value = json.loads(package.read_text(encoding="utf-8"))
        value["scripts"]["pretest"] = "node --check test/common.test.cjs"
        package.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        self.initialize_git("package.json")
        command = ["npm", "test", "--silent"]
        first = self.verify_gate([command], "turn-1")
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        second = self.verify_gate([command], "turn-1")
        self.assert_runner(second)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        decision = state["verification"]["incremental_plan"]["decisions"][0]
        self.assertEqual(decision["reason_code"], "runtime-identity-incomplete")

    @unittest.skipUnless(VITEST_AVAILABLE, "pinned Vitest fixture is unavailable")
    def test_vitest_no_install_runner_executes_and_reuses_exact_identity(self) -> None:
        _, command = self.vitest_project()
        first = self.verify_gate([command], "turn-1")
        self.assert_runner(first)
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        reused = self.verify_gate([command], "turn-1")
        self.assert_runner(reused, False)

    @unittest.skipUnless(JEST_AVAILABLE, "pinned Jest fixture is unavailable")
    def test_jest_no_install_runner_executes_and_reuses_exact_identity(self) -> None:
        _, command = self.jest_project()
        first = self.verify_gate([command], "turn-1")
        self.assert_runner(first)
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        reused = self.verify_gate([command], "turn-1")
        self.assert_runner(reused, False)

    @unittest.skipUnless(NODE, "Node is not installed")
    def test_node_check_is_a_distinct_one_shot_validation_profile(self) -> None:
        project = self.workspace
        script = project / "syntax check.js"
        script.write_text("const value = 1;\n", encoding="utf-8")
        self.initialize_git("verification_fixture.py", "syntax check.js")
        command = ["node", "--check", "syntax check.js"]
        first = self.verify_gate([command], "turn-1")
        self.assert_runner(first)
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        reused = self.verify_gate([command], "turn-1")
        self.assert_runner(reused, False)

    @unittest.skipUnless(GO, "Go is not installed")
    def test_go_test_real_runner_reuse_failure_and_fix(self) -> None:
        (self.workspace / "calc").mkdir()
        (self.workspace / ".gitignore").write_text("\n", encoding="utf-8")
        (self.workspace / "go.mod").write_text(
            "module example.invalid/clickfixture\n\ngo 1.22\n", encoding="utf-8"
        )
        implementation = self.workspace / "calc" / "calc.go"
        implementation.write_text(
            "package calc\n\nfunc Add(a, b int) int { return a + b }\n",
            encoding="utf-8",
        )
        (self.workspace / "calc" / "calc_test.go").write_text(
            "package calc\n\nimport \"testing\"\n\n"
            "func TestAdd(t *testing.T) { if Add(2, 2) != 4 { t.Fatal(\"bad add\") } }\n",
            encoding="utf-8",
        )
        self.initialize_git(".gitignore", "go.mod", "calc")
        command = ["go", "test", "./..."]
        with mock.patch.dict(
            os.environ, {"GOTOOLCHAIN": "local", "GOPROXY": "off"}
        ):
            first = self.verify_gate([command], "turn-1")
            self.assert_runner(first)
            self.assertEqual(self.run_rewritten(first).returncode, 0)
            reused = self.verify_gate([command], "turn-1")
            self.assert_runner(reused, False)

            self.mutate(
                implementation,
                "package calc\n\nfunc Add(a, b int) int { return a - b }\n",
                "go-fail",
            )
            failing = self.verify_gate([command], "turn-1")
            self.assert_runner(failing)
            self.assertNotEqual(self.run_rewritten(failing).returncode, 0)

            self.mutate(
                implementation,
                "package calc\n\nfunc Add(a, b int) int { return a + b }\n",
                "go-fix",
            )
            fixed = self.verify_gate([command], "turn-1")
            self.assert_runner(fixed)
            self.assertEqual(self.run_rewritten(fixed).returncode, 0)

    @unittest.skipUnless(NPX, "npx is not installed")
    def test_npx_cannot_download_an_unprovisioned_test_runner(self) -> None:
        self.initialize_git("verification_fixture.py")
        request = self.verify_gate(
            [["npx", "click-missing-test-runner", "--run"]], "turn-1"
        )
        output = request["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("download", output["permissionDecisionReason"])
        self.assertNotIn("updatedInput", output)


if __name__ == "__main__":
    unittest.main()
