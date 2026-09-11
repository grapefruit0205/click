"""Passing receipts outlive the host session: a new session starts from the repository's archive."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from click_gate_test_support import ClickGateTestCase


class WorkspaceReceiptTests(ClickGateTestCase):
    def fixture(self, workspace: Path | None = None) -> list[str]:
        workspace = workspace or self.workspace
        (workspace / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
        (workspace / "tests").mkdir()
        (workspace / "tests" / "__init__.py").write_text("", encoding="utf-8")
        for name in ("alpha", "beta"):
            (workspace / "tests" / f"test_{name}.py").write_text(
                "import unittest\nclass Check(unittest.TestCase):\n"
                f"    def test_it(self):\n        print('ran-{name}')\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
        parent = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
        (workspace / ".click").mkdir()
        (workspace / ".click" / "evidence-shards.json").write_text(json.dumps({
            "version": 1,
            "entries": [{
                "checks": [parent], "inventory": ["tests/test*.py"],
                "shards": [{"id": name, "checks": [[sys.executable, "-m", "unittest", f"tests.test_{name}"]],
                            "covers": [f"tests/test_{name}.py"]} for name in ("alpha", "beta")],
            }],
        }), encoding="utf-8")
        return parent

    def setUp(self) -> None:
        super().setUp()
        self.original_event = dict(self.base_event)

    def verify(self, parent: list[str], turn: str):
        payload = self.verify_gate([parent], turn, evidence_ids=["E1"])
        result = self.run_rewritten(payload)
        return payload, result, result.stdout + result.stderr

    def end_session(self) -> None:
        result, payload = self.run_hook("session-end", {**self.base_event, "hook_event_name": "SessionEnd"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(payload)

    def hooked_edit(self, path: Path, old: str, new: str, *, turn: str) -> None:
        patch = f"*** Begin Patch\n*** Update File: {path}\n@@\n-{old}\n+{new}\n*** End Patch"
        tool_id = f"edit-{path.name}"
        self.assertIsNone(self.pre_tool("apply_patch", patch, turn, tool_use_id=tool_id))
        path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
        self.tool_hook("post-tool", "apply_patch", {"patch": patch}, turn_id=turn, tool_use_id=tool_id)

    def as_session(self, session_id: str, *, cwd: Path | None = None) -> None:
        self.base_event = {**self.original_event, "session_id": session_id}
        if cwd is not None:
            self.base_event["cwd"] = str(cwd)
            self.workspace = cwd
        self.submitted_turns = set()

    def test_a_new_session_reuses_the_last_sessions_passing_receipts(self) -> None:
        parent = self.fixture()
        self.initialize_git(".gitignore", ".click/evidence-shards.json", "tests/__init__.py",
                            "tests/test_alpha.py", "tests/test_beta.py")
        _, first, output = self.verify(parent, "turn-1")
        self.assertEqual(first.returncode, 0, output)
        self.assertIn("ran-alpha", output)
        self.assertIn("ran-beta", output)
        self.end_session()

        self.as_session("session-2")
        payload, second, output = self.verify(parent, "turn-1")
        self.assertEqual(second.returncode, 0, output)
        # Both shards were requalified from the archive: nothing executed.
        self.assertNotIn("ran-alpha", output)
        self.assertNotIn("ran-beta", output)
        self.assertNotIn("[Click verification", output)

    def test_a_changed_shard_runs_while_its_unchanged_sibling_is_reused(self) -> None:
        parent = self.fixture()
        self.initialize_git(".gitignore", ".click/evidence-shards.json", "tests/__init__.py",
                            "tests/test_alpha.py", "tests/test_beta.py")
        _, first, output = self.verify(parent, "turn-1")
        self.assertEqual(first.returncode, 0, output)
        self.end_session()

        self.as_session("session-2")
        # The edit goes through the host's tool hooks, as an agent's edit
        # does, so Click knows the mutation boundary of the new session.
        self.hooked_edit(self.workspace / "tests" / "test_beta.py",
                         "self.assertTrue(True)", "self.assertEqual(1 + 1, 2)", turn="turn-1")
        _, second, output = self.verify(parent, "turn-1")
        self.assertEqual(second.returncode, 0, output)
        self.assertIn("ran-beta", output)
        self.assertNotIn("ran-alpha", output)

    def test_an_edit_made_outside_the_hooks_reruns_everything(self) -> None:
        parent = self.fixture()
        self.initialize_git(".gitignore", ".click/evidence-shards.json", "tests/__init__.py",
                            "tests/test_alpha.py", "tests/test_beta.py")
        _, first, output = self.verify(parent, "turn-1")
        self.assertEqual(first.returncode, 0, output)
        self.end_session()

        self.as_session("session-2")
        path = self.workspace / "tests" / "test_beta.py"
        path.write_text(path.read_text(encoding="utf-8").replace("assertTrue(True)", "assertEqual(1 + 1, 2)"), encoding="utf-8")
        _, second, output = self.verify(parent, "turn-1")
        self.assertEqual(second.returncode, 0, output)
        # Drift Click did not observe makes the mutation boundary ambiguous;
        # the archived facts are not trusted for input-based reuse.
        self.assertIn("ran-alpha", output)
        self.assertIn("ran-beta", output)

    def test_facts_stay_with_their_repository(self) -> None:
        parent = self.fixture()
        self.initialize_git(".gitignore", ".click/evidence-shards.json", "tests/__init__.py",
                            "tests/test_alpha.py", "tests/test_beta.py")
        _, first, output = self.verify(parent, "turn-1")
        self.assertEqual(first.returncode, 0, output)
        self.end_session()

        other = self.workspace.parent / "other-workspace"
        shutil.copytree(self.workspace, other, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        self.as_session("session-2", cwd=other)
        self.initialize_git(".gitignore", ".click/evidence-shards.json", "tests/__init__.py",
                            "tests/test_alpha.py", "tests/test_beta.py")
        _, second, output = self.verify(parent, "turn-1")
        self.assertEqual(second.returncode, 0, output)
        self.assertIn("ran-alpha", output)
        self.assertIn("ran-beta", output)

    def test_a_check_that_failed_last_is_not_archived_but_its_passing_sibling_is(self) -> None:
        parent = self.fixture()
        self.initialize_git(".gitignore", ".click/evidence-shards.json", "tests/__init__.py",
                            "tests/test_alpha.py", "tests/test_beta.py")
        _, first, output = self.verify(parent, "turn-1")
        self.assertEqual(first.returncode, 0, output)
        (self.workspace / "tests" / "test_beta.py").write_text(
            "import unittest\nclass Check(unittest.TestCase):\n"
            "    def test_it(self):\n        print('ran-beta')\n        self.fail('broken')\n",
            encoding="utf-8",
        )
        _, failed, output = self.verify(parent, "turn-1")
        self.assertNotEqual(failed.returncode, 0, output)
        self.end_session()

        self.as_session("session-2")
        _, second, output = self.verify(parent, "turn-1")
        self.assertNotEqual(second.returncode, 0, output)
        self.assertIn("ran-beta", output)
        self.assertNotIn("ran-alpha", output)


if __name__ == "__main__":
    unittest.main()
