from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest import mock

from tests.click_gate_test_support import (
    CLICK_EVIDENCE,
    ClickGateTestCase,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "mixed-project"
AVAILABLE = bool(
    shutil.which("node")
    and shutil.which("npx")
    and shutil.which("go")
    and (SOURCE / "node_modules" / "vitest" / "package.json").is_file()
)


@unittest.skipUnless(AVAILABLE, "mixed fixture requires pinned Vitest, Node, and Go")
class MixedProjectReuseTests(ClickGateTestCase):
    hook_in_process = True

    def setUp(self) -> None:
        # This suite asserts per-check owner-policy decisions; automatic
        # sharding of the vitest parent would replace it with shard children.
        environment = mock.patch.dict(os.environ, {"CLICK_AUTOMATIC_SHARDS": "off"})
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()
        project = self.workspace / "mixed project"
        shutil.copytree(SOURCE, project, symlinks=True)
        for generated in project.rglob("__pycache__"):
            shutil.rmtree(generated)
        self.workspace = project
        self.base_event["cwd"] = str(project)
        self.commands = {
            "frontend": ["npx", "--no-install", "vitest", "run"],
            "backend": [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "backend/tests",
                "-q",
            ],
            "go": ["go", "test", "./go/..."],
            "docs": ["node", "--test", "docs/link.check.mjs"],
        }

    def checks(self) -> list[dict[str, object]]:
        return [
            {
                "evidence_id": "E_FRONTEND",
                "argv": self.commands["frontend"],
                "class": "broad",
                "inputs": ["frontend/**", "tests/frontend/**", "shared/**", "package.json", "package-lock.json"],
            },
            {
                "evidence_id": "E_BACKEND",
                "argv": self.commands["backend"],
                "class": "broad",
                "inputs": ["backend/**", "shared/**"],
            },
            {
                "evidence_id": "E_GO",
                "argv": self.commands["go"],
                "class": "broad",
                "inputs": ["go/**", "go.mod", "shared/**"],
            },
            {
                "evidence_id": "E_DOCS",
                "argv": self.commands["docs"],
                "class": "targeted",
                "inputs": ["docs/**", "assets/**"],
            },
        ]

    def initialize(self, *, policy: bool) -> None:
        if policy:
            target = self.workspace / ".click" / "evidence-reuse.json"
            target.parent.mkdir()
            safe = {
                "frontend": ["backend/**", "go/**", "docs/**", "assets/**"],
                "backend": ["frontend/**", "tests/frontend/**", "go/**", "docs/**", "assets/**", "package*.json"],
                "go": ["frontend/**", "tests/frontend/**", "backend/**", "docs/**", "assets/**", "package*.json"],
                "docs": ["frontend/**", "tests/frontend/**", "backend/**", "go/**", "shared/**", "go.mod", "package*.json"],
            }
            target.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": [
                            {
                                "checks": [self.commands[name]],
                                "reuse_if_only_changed": patterns,
                            }
                            for name, patterns in safe.items()
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        self.initialize_git(".")

    def state(self) -> dict[str, object]:
        path = next((self.plugin_data / "gate-state").glob("session-contract-*.json"))
        return json.loads(path.read_text(encoding="utf-8"))

    def sources(self) -> dict[str, dict[str, object]]:
        sources = self.state()["evidence_state"]["sources"]
        return {
            name: sources[CLICK_EVIDENCE.evidence_key(evidence_id)]
            for name, evidence_id in {
                "frontend": "E_FRONTEND",
                "backend": "E_BACKEND",
                "go": "E_GO",
                "docs": "E_DOCS",
            }.items()
        }

    def decisions(self) -> dict[str, str]:
        plan = self.state()["verification"]["incremental_plan"]
        by_key = {
            item["source_key"]: item["decision"] for item in plan["decisions"]
        }
        return {
            name: by_key[CLICK_EVIDENCE.evidence_key(evidence_id)]
            for name, evidence_id in {
                "frontend": "E_FRONTEND",
                "backend": "E_BACKEND",
                "go": "E_GO",
                "docs": "E_DOCS",
            }.items()
        }

    def run_batch(self) -> int:
        with mock.patch.dict(
            os.environ,
            {
                "GOTOOLCHAIN": "local",
                "GOPROXY": "off",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            clear=False,
        ):
            request = self.verify_checks(
                self.checks(),
                turn_id="turn-1",
                bind_default=False,
                version=3,
            )
            self.last_result = self.run_rewritten(request)
            return self.last_result.returncode

    def mutate(self, relative: str, content: str, tool_id: str) -> None:
        self.assertIsNone(
            self.pre_tool(
                "apply_patch",
                "*** Begin Patch\n*** End Patch",
                "turn-1",
                submit_prompt=False,
                tool_use_id=tool_id,
            )
        )
        target = self.workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": relative},
            turn_id="turn-1",
            tool_use_id=tool_id,
        )

    def delete(self, relative: str, tool_id: str) -> None:
        self.assertIsNone(
            self.pre_tool(
                "apply_patch",
                "*** Begin Patch\n*** End Patch",
                "turn-1",
                submit_prompt=False,
                tool_use_id=tool_id,
            )
        )
        (self.workspace / relative).unlink()
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": relative},
            turn_id="turn-1",
            tool_use_id=tool_id,
        )

    def test_without_owner_policy_frontend_change_reruns_every_check(self) -> None:
        self.initialize(policy=False)
        self.assertEqual(self.run_batch(), 0)
        self.mutate(
            "frontend/value.js",
            "export const expectedSchemaVersion = 1; // local refactor\n",
            "frontend-no-policy",
        )
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual(
            self.decisions(),
            {"frontend": "run", "backend": "run", "go": "run", "docs": "run"},
        )

    def test_policy_reuses_only_unaffected_checks_and_repairs_failures(self) -> None:
        self.initialize(policy=True)
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual(
            self.decisions(),
            {
                "frontend": "reuse-exact",
                "backend": "reuse-exact",
                "go": "reuse-exact",
                "docs": "reuse-exact",
            },
        )

        self.mutate(
            "frontend/value.js",
            "export const expectedSchemaVersion = 1; // equivalent refactor\n",
            "frontend-policy",
        )
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual(
            self.decisions(),
            {
                "frontend": "run",
                "backend": "reuse-safe-change",
                "go": "reuse-safe-change",
                "docs": "reuse-safe-change",
            },
        )

        backend = (self.workspace / "backend/tests/test_schema.py").read_text(
            encoding="utf-8"
        )
        self.mutate(
            "backend/tests/test_schema.py",
            backend + "\n# backend-only refactor\n",
            "backend-policy",
        )
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual(
            self.decisions(),
            {
                "frontend": "reuse-safe-change",
                "backend": "run",
                "go": "reuse-safe-change",
                "docs": "reuse-safe-change",
            },
        )

        lockfile = (self.workspace / "package-lock.json").read_text(encoding="utf-8")
        self.mutate("package-lock.json", lockfile + "\n", "lockfile-change")
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual(
            self.decisions(),
            {
                "frontend": "run",
                "backend": "reuse-safe-change",
                "go": "reuse-safe-change",
                # Node configuration identity includes the package lock, so the
                # docs runner is conservatively refreshed as well.
                "docs": "run",
            },
        )

        self.mutate(
            "tests/frontend/added.test.js",
            "import { test, expect } from 'vitest';\n"
            "test('new member', () => expect(2 + 2).toBe(4));\n",
            "frontend-test-add",
        )
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual(self.decisions()["frontend"], "run")

        self.mutate(
            "tests/frontend/dynamic.test.js",
            "import { test, expect } from 'vitest';\n"
            "test('dynamic import remains parent-bound', async () => {\n"
            "  const value = await import('../../frontend/value.js');\n"
            "  expect(value.expectedSchemaVersion).toBe(1);\n"
            "});\n",
            "frontend-dynamic-add",
        )
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual(self.decisions()["frontend"], "run")

        self.mutate(
            "shared/api.json",
            '{"title":"Click mixed fixture","version":2}\n',
            "shared-break",
        )
        self.assertNotEqual(self.run_batch(), 0)
        self.assertEqual(
            self.decisions(),
            {
                "frontend": "run",
                "backend": "run",
                "go": "run",
                "docs": "reuse-safe-change",
            },
        )

        self.mutate(
            "shared/api.json",
            '{"title":"Click mixed fixture","version":1}\n',
            "shared-repair",
        )
        self.assertEqual(self.run_batch(), 0)

        self.mutate("assets/extra.txt", "new asset membership\n", "asset-add")
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual(self.decisions()["docs"], "run")

        self.delete("assets/logo.txt", "asset-delete")
        self.assertNotEqual(self.run_batch(), 0)
        self.assertEqual(
            self.decisions(),
            {
                "frontend": "reuse-safe-change",
                "backend": "reuse-safe-change",
                "go": "reuse-safe-change",
                "docs": "run",
            },
        )
        self.mutate("assets/logo.txt", "click fixture asset\n", "asset-repair")
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual(self.decisions()["docs"], "run")


if __name__ == "__main__":
    unittest.main()
