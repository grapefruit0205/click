"""A supported suite without a committed plan is sharded by Click itself."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from unittest import mock

from click_gate_test_support import ClickGateTestCase
from hooks import click_automatic_shards as automatic

COLLECTION = sys.implementation.name == "cpython" and (3, 10) <= sys.version_info[:2] <= (3, 14)
try:
    import pytest as _pytest  # noqa: F401
    PYTEST = True
except ImportError:
    PYTEST = False


@unittest.skipUnless(COLLECTION, "CPython 3.10-3.14 collection adapter")
class AutomaticShardTests(ClickGateTestCase):
    def suite(self, names=("alpha", "beta", "gamma")) -> list[str]:
        (self.workspace / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
        (self.workspace / "tests").mkdir(exist_ok=True)
        (self.workspace / "tests" / "__init__.py").write_text("", encoding="utf-8")
        for name in names:
            self.write_module(name)
        self.initialize_git(".gitignore", "tests")
        return [sys.executable, "-m", "unittest", "discover", "-s", "tests"]

    def write_module(self, name: str, verdict: str = "self.assertTrue(True)") -> None:
        (self.workspace / "tests" / f"test_{name}.py").write_text(
            "import unittest\nclass Check(unittest.TestCase):\n"
            f"    def test_it(self):\n        print('ran-{name}')\n        {verdict}\n",
            encoding="utf-8",
        )

    def verify(self, parent, turn: str, *, evidence_ids=("E1",)):
        payload = self.verify_gate([parent], turn, evidence_ids=list(evidence_ids))
        result = self.run_rewritten(payload)
        return payload, result, result.stdout + result.stderr

    def hooked_change(self, path: Path, content: str, *, turn: str, tool_id: str) -> None:
        patch = f"*** Begin Patch\n*** Update File: {path}\n@@\n+{content}\n*** End Patch"
        self.assertIsNone(self.pre_tool("apply_patch", patch, turn, tool_use_id=tool_id))
        path.write_text(content, encoding="utf-8")
        self.tool_hook("post-tool", "apply_patch", {"patch": patch}, turn_id=turn, tool_use_id=tool_id)

    def store(self) -> Path:
        # The hooks ran with PLUGIN_DATA pointing at the fixture's data directory.
        with mock.patch.dict("os.environ", {"PLUGIN_DATA": str(self.plugin_data)}):
            return automatic.store_path(self.workspace)

    @staticmethod
    def headers(output: str) -> list[str]:
        return [line for line in output.splitlines() if line.startswith("[Click verification ")]

    def test_a_supported_suite_without_a_plan_is_sharded_automatically(self) -> None:
        parent = self.suite()
        self.assertFalse((self.workspace / ".click").exists())
        payload, result, output = self.verify(parent, "turn-1")
        self.assertEqual(result.returncode, 0, output)
        self.assertEqual(len(self.headers(output)), 3, output)
        for name in ("alpha", "beta", "gamma"):
            self.assertIn(f"ran-{name}", output)
        self.assertIn("generated a 3-shard plan automatically", json.dumps(payload, ensure_ascii=False))
        # The plan lives in Click's state, not in the repository.
        self.assertFalse((self.workspace / ".click").exists())
        self.assertTrue(self.store().exists())
        stored = json.loads(self.store().read_text(encoding="utf-8"))
        self.assertEqual(len(stored["plans"]), 1)
        self.assertEqual(next(iter(stored["plans"].values()))["shard_count"], 3)

    def test_the_plan_is_kept_and_regenerated_when_the_inventory_changes(self) -> None:
        parent = self.suite()
        _, first, output = self.verify(parent, "turn-1")
        self.assertEqual(first.returncode, 0, output)
        self.hooked_change(
            self.workspace / "tests" / "test_beta.py",
            "import unittest\nclass Check(unittest.TestCase):\n"
            "    def test_it(self):\n        print('ran-beta')\n        self.assertEqual(1 + 1, 2)\n",
            turn="turn-2", tool_id="edit-beta",
        )
        payload, second, output = self.verify(parent, "turn-2")
        self.assertEqual(second.returncode, 0, output)
        # Same inventory: the stored plan is used again, not regenerated.
        self.assertNotIn("automatically", json.dumps(payload, ensure_ascii=False))
        self.assertEqual(len(self.headers(output)), 3, output)
        # A new test module is outside the stored plan's inventory: the plan
        # is dropped and regenerated with four shards.
        self.hooked_change(
            self.workspace / "tests" / "test_delta.py",
            "import unittest\nclass Check(unittest.TestCase):\n"
            "    def test_it(self):\n        print('ran-delta')\n        self.assertTrue(True)\n",
            turn="turn-3", tool_id="add-delta",
        )
        payload, third, output = self.verify(parent, "turn-3")
        self.assertEqual(third.returncode, 0, output)
        self.assertIn("generated a 4-shard plan automatically", json.dumps(payload, ensure_ascii=False))
        self.assertIn("ran-delta", output)

    @unittest.skipUnless(PYTEST, "pytest is not installed for this interpreter")
    def test_pytest_file_shards_reuse_unchanged_siblings_after_a_hooked_edit(self) -> None:
        # pytest children are exact file targets, so a sibling's observed
        # inputs do not include the edited module; unittest discovery stats
        # every file in the start directory and re-runs all of its shards.
        (self.workspace / ".gitignore").write_text("__pycache__/\n*.pyc\n.pytest_cache/\n", encoding="utf-8")
        (self.workspace / "tests").mkdir(exist_ok=True)
        for name in ("alpha", "beta", "gamma"):
            (self.workspace / "tests" / f"test_{name}.py").write_text(
                f"def test_it():\n    print('ran-{name}')\n    assert True\n", encoding="utf-8",
            )
        self.initialize_git(".gitignore", "tests")
        parent = [sys.executable, "-m", "pytest", "-q", "tests"]
        payload, first, output = self.verify(parent, "turn-1")
        self.assertEqual(first.returncode, 0, output)
        self.assertIn("generated a 3-shard plan automatically", json.dumps(payload, ensure_ascii=False))
        self.hooked_change(
            self.workspace / "tests" / "test_beta.py",
            "def test_it():\n    print('ran-beta')\n    assert 1 + 1 == 2\n",
            turn="turn-2", tool_id="edit-beta",
        )
        _, second, output = self.verify(parent, "turn-2")
        self.assertEqual(second.returncode, 0, output)
        headers = self.headers(output)
        self.assertEqual(len(headers), 1, output)
        self.assertIn("tests/test_beta.py", headers[0])

    def test_a_committed_manifest_takes_precedence(self) -> None:
        parent = self.suite()
        (self.workspace / ".click").mkdir()
        (self.workspace / ".click" / "evidence-shards.json").write_text(json.dumps({
            "version": 1,
            "entries": [{
                "checks": [parent], "inventory": ["tests/test*.py"],
                "shards": [{"id": name, "checks": [[sys.executable, "-m", "unittest", f"tests.test_{name}"]],
                            "covers": [f"tests/test_{name}.py"]} for name in ("alpha", "beta", "gamma")],
            }],
        }), encoding="utf-8")
        self.initialize_git(".click/evidence-shards.json")
        payload, result, output = self.verify(parent, "turn-1")
        self.assertEqual(result.returncode, 0, output)
        self.assertNotIn("automatically", json.dumps(payload, ensure_ascii=False))
        self.assertIn(":E1[alpha]:", output)
        self.assertFalse(self.store().exists())

    def test_a_runner_form_click_cannot_collect_is_left_alone(self) -> None:
        self.suite()
        (self.workspace / "plain_test.py").write_text(
            "import unittest\nclass Plain(unittest.TestCase):\n    def test_it(self):\n        print('ran-plain')\n",
            encoding="utf-8",
        )
        self.initialize_git("plain_test.py")
        # A single-module target is not a discovery command the collector
        # understands; it runs as submitted, without any sharding advisory.
        plain = [sys.executable, "-m", "unittest", "plain_test"]
        payload = self.verify_checks([{"argv": plain, "class": "broad"}], "turn-1")
        result = self.run_rewritten(payload)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertEqual(len(self.headers(output)), 1, output)
        self.assertIn("[Click diagnostic] E1 passed.", output)
        self.assertNotIn("Evidence Shards", json.dumps(payload, ensure_ascii=False))
        self.assertFalse(self.store().exists())

    def test_the_switch_turns_automatic_plans_off(self) -> None:
        parent = self.suite()
        with mock.patch.dict("os.environ", {"CLICK_AUTOMATIC_SHARDS": "off"}):
            payload, result, output = self.verify(parent, "turn-1")
        self.assertEqual(result.returncode, 0, output)
        self.assertEqual(len(self.headers(output)), 1, output)
        self.assertNotIn("Evidence Shards", json.dumps(payload, ensure_ascii=False))
        self.assertFalse(self.store().exists())

    def test_the_store_ignores_garbage_and_forgets_plans(self) -> None:
        with mock.patch.dict("os.environ", {"PLUGIN_DATA": str(self.plugin_data)}):
            path = automatic.store_path(self.workspace)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(automatic.policies(self.workspace), [])
            path.write_text(json.dumps({"version": 1, "plans": {"k": {"argv": ["x"], "policy": "{}"}}}), encoding="utf-8")
            self.assertEqual(automatic.policies(self.workspace), [b"{}"])
            automatic.forget(self.workspace, "k")
            self.assertEqual(automatic.policies(self.workspace), [])


if __name__ == "__main__":
    unittest.main()
