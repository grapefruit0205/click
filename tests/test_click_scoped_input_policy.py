"""Owner-policy input scopes never grant runtime observation authority."""
import copy
import json
import os
import unittest
from unittest import mock

from hooks import click_change_policy as policy
from hooks import click_input_policy as inputs
from tests import test_click_change_policy as policy_fixture


class ScopedInputPolicyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = policy_fixture.ClickChangePolicyTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        (self.root / "src/other.py").write_text("OTHER = 1\n")
        (self.root / ".gitignore").write_text("data/\n")
        (self.root / "data").mkdir()
        (self.root / "data/value.txt").write_text("first")
        value = {"version": 2, "entries": [{
            "checks": [self.fixture.argv],
            "reuse_if_only_changed": ["docs/**", "src/**"],
            "inputs": ["src/unit.py", "data/", "package-lock.json"],
        }]}
        (self.root / policy.CONFIG_RELATIVE_PATH).write_text(json.dumps(value))
        self.fixture.commit("owner declares scoped inputs before baseline")
        self.baseline = self.fixture.baseline()

    def test_unrelated_source_edit_reuses_but_own_input_edit_runs(self):
        (self.root / "src/other.py").write_text("OTHER = 2\n")
        self.assertEqual(self.fixture.decide(self.baseline)["status"], "reuse")
        (self.root / "src/unit.py").write_text("VALUE = 2\n")
        result = self.fixture.decide(self.baseline)
        self.assertEqual((result["status"], result["reason"]), ("rerun", "declared-input-changed"))

    def test_ignored_data_and_new_negative_input_invalidate(self):
        (self.root / "data/value.txt").write_text("second")
        self.assertEqual(self.fixture.decide(self.baseline)["reason"], "declared-input-changed")
        (self.root / "data/value.txt").write_text("first")
        (self.root / "package-lock.json").write_text("{}")
        self.assertEqual(self.fixture.decide(self.baseline)["reason"], "declared-input-changed")

    def test_missing_glob_member_and_directory_membership_invalidate(self):
        (self.root / "data/new.txt").write_text("new")
        self.assertFalse(policy.inputs_are_current(self.root, self.baseline))
        self.assertEqual(self.fixture.decide(self.baseline)["status"], "rerun")

    def test_policy_widening_after_baseline_cannot_authorize_reuse(self):
        path = self.root / policy.CONFIG_RELATIVE_PATH
        value = json.loads(path.read_text())
        value["entries"][0]["inputs"] = ["data/"]
        path.write_text(json.dumps(value))
        self.assertEqual(self.fixture.decide(self.baseline)["status"], "unknown")
        self.fixture.commit("changed owner policy needs new baseline")
        self.assertEqual(self.fixture.decide(self.baseline)["reason"], "policy-changed")

    def test_scoped_receipt_is_distinct_and_cannot_be_downgraded(self):
        self.assertEqual(self.baseline["provider"], policy.INPUT_PROVIDER_NAME)
        self.assertTrue(policy.receipt_is_valid(self.baseline))
        forged = copy.deepcopy(self.baseline)
        forged["provider"] = policy.PROVIDER_NAME
        self.assertFalse(policy.receipt_is_valid(forged))
        forged = copy.deepcopy(self.baseline)
        forged["inputs_digest"] = ""
        self.assertFalse(policy.receipt_is_valid(forged))

    def test_file_and_resource_boundaries_fail_closed(self):
        with mock.patch.object(inputs, "MAX_BYTES", 1):
            self.assertIsNone(inputs.snapshot(self.root, ["data/"]))
        self.assertIsNone(inputs.snapshot(self.root, ["**"]))
        self.assertIsNone(inputs.snapshot(self.root, ["src"]))
        try:
            os.symlink(self.root / "src", self.root / "linked", target_is_directory=True)
        except (OSError, NotImplementedError):
            return
        self.assertIsNone(inputs.snapshot(self.root, ["linked/"]))

    def test_decision_context_cannot_be_substituted(self):
        (self.root / "src/other.py").write_text("OTHER = 2\n")
        context = {"source_key": "a" * 64, "revision": 2,
                   "git_root": str(self.root), "tree_digest": "b" * 64}
        decision = policy.decide(
            self.root, self.fixture.checks, self.baseline,
            git_capture=self.fixture.git_capture, decision_context=context,
        )
        self.assertTrue(policy.reuse_decision_matches(
            decision, self.baseline, check_digest=policy.group_digest(self.fixture.checks),
            decision_context=context,
        ))
        self.assertFalse(policy.reuse_decision_matches(
            decision, self.baseline, check_digest=policy.group_digest(self.fixture.checks),
            decision_context={**context, "revision": 3},
        ))

    def test_shared_preflight_reuses_git_diff_only_within_one_request(self):
        path = self.root / policy.CONFIG_RELATIVE_PATH
        value = json.loads(path.read_text())
        second = copy.deepcopy(self.fixture.checks)
        second[0]["argv"].append("-q")
        value["entries"].append({**value["entries"][0], "checks": [second[0]["argv"]]})
        path.write_text(json.dumps(value))
        self.fixture.commit("second precommitted input group")
        groups = {"a" * 64: self.fixture.checks, "b" * 64: second}
        baselines = policy.receipts_for_groups(self.root, groups, git_capture=self.fixture.git_capture)
        (self.root / "src/other.py").write_text("OTHER = 2\n")
        contexts = {key: {"source_key": key, "revision": 2,
                         "git_root": str(self.root), "tree_digest": "c" * 64} for key in groups}
        with mock.patch.object(policy, "_changed_paths", wraps=policy._changed_paths) as diff:
            decisions = policy.decide_groups(self.root, groups, baselines,
                git_capture=self.fixture.git_capture, decision_contexts=contexts)
        self.assertEqual(diff.call_count, 1)
        self.assertTrue(all(item["status"] == "reuse" for item in decisions.values()))
        (self.root / "src/unit.py").write_text("VALUE = 3\n")
        decisions = policy.decide_groups(self.root, groups, baselines,
            git_capture=self.fixture.git_capture, decision_contexts=contexts)
        self.assertTrue(all(item["reason"] == "declared-input-changed" for item in decisions.values()))
