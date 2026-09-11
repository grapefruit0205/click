from __future__ import annotations

import json
import sys
import unittest

from click_gate_test_support import ClickGateTestCase


class LegibleVerificationOutputTests(ClickGateTestCase):
    def test_shard_children_are_named_by_the_callers_id_and_shard(self) -> None:
        # Bytecode caches are not repository content. Outside the native
        # observer profile the interpreter writes them, and Click correctly
        # refuses a verification that changed protected content.
        (self.workspace / ".gitignore").write_text("__pycache__/\n*.pyc\n")
        (self.workspace / "tests").mkdir()
        (self.workspace / "tests" / "__init__.py").write_text("")
        for name in ("alpha", "beta"):
            (self.workspace / "tests" / f"test_{name}.py").write_text(
                "import unittest\nclass Check(unittest.TestCase):\n"
                f"    def test_it(self):\n        print('ran-{name}')\n        self.assertTrue(True)\n"
            )
        (self.workspace / "plain_test.py").write_text(
            "import unittest\nclass Plain(unittest.TestCase):\n    def test_it(self):\n        self.assertTrue(True)\n"
        )
        parent = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
        (self.workspace / ".click").mkdir()
        (self.workspace / ".click" / "evidence-shards.json").write_text(json.dumps({
            "version": 1,
            "entries": [{
                "checks": [parent], "inventory": ["tests/test*.py"],
                "shards": [{"id": name, "checks": [[sys.executable, "-m", "unittest", f"tests.test_{name}"]],
                            "covers": [f"tests/test_{name}.py"]} for name in ("alpha", "beta")],
            }],
        }))
        self.initialize_git(".gitignore", ".click/evidence-shards.json", "tests/__init__.py",
                            "tests/test_alpha.py", "tests/test_beta.py", "plain_test.py")
        plain = [sys.executable, "-m", "unittest", "plain_test"]
        payload = self.verify_gate([parent, plain], "turn-1", evidence_ids=["SUITE", "PLAIN"])
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        # Each shard child runs under a synthetic id the caller never chose;
        # the host is shown the caller's id and the committed shard id instead.
        self.assertIn(":SUITE[alpha]:broad]", output)
        self.assertIn(":SUITE[beta]:broad]", output)
        self.assertNotRegex(output, r"\[Click verification \d+/\d+:S[0-9a-f]{31}:")
        # A check that was not sharded keeps exactly the id the caller gave.
        self.assertIn(":PLAIN:", output)
        state = json.loads(next((self.plugin_data / "gate-state").glob("session-contract-*.json")).read_text())
        labels = state["verification"]["source_labels"]
        self.assertEqual(sorted(labels.values()), ["SUITE[alpha]", "SUITE[beta]"])
        # Presentation never changes identity: every stored key is a source digest.
        self.assertTrue(all(len(key) == 64 for key in labels))


if __name__ == "__main__":
    unittest.main()
