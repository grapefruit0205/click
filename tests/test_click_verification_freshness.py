from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

from tests.click_gate_test_support import (
    CLICK_EVIDENCE,
    ClickGateTestCase,
    split_runner_command,
)


class VerificationFreshnessHookTests(ClickGateTestCase):
    def setUp(self) -> None:
        super().setUp()
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n.local-input.txt\ngenerated/\nprofile-*.txt\n",
            encoding="utf-8",
        )
        self.initialize_git(".gitignore", "verification_fixture.py")

    def check(self, **policy: object) -> dict[str, object]:
        return {
            "argv": self.verification_argv(),
            "class": "targeted",
            **policy,
        }

    def state_path(self) -> Path:
        return next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )

    def state(self) -> dict[str, object]:
        return json.loads(self.state_path().read_text(encoding="utf-8"))

    def reason(self) -> str:
        state = self.state()
        verification = state["verification"]
        assert isinstance(verification, dict)
        plan = verification["incremental_plan"]
        assert isinstance(plan, dict)
        decisions = plan["decisions"]
        assert isinstance(decisions, list)
        return str(decisions[0]["reason_code"])

    def assert_runner(self, payload: dict, expected: bool = True) -> None:
        rewritten = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        self.assertEqual("run-verification" in rewritten, expected)

    def request(self, **policy: object) -> dict:
        return self.verify_checks(
            [self.check(**policy)], turn_id="turn-1", version=3
        )

    def test_default_reuse_and_one_time_rerun_execute_on_distinct_paths(self) -> None:
        first = self.request()
        self.assert_runner(first)
        self.assertEqual(self.run_rewritten(first).returncode, 0)

        reused = self.request()
        self.assert_runner(reused, False)
        self.assertEqual(self.reason(), "same-revision-receipt-current")

        rerun = self.request(reuse="rerun")
        self.assert_runner(rerun)
        self.assertEqual(self.reason(), "explicit-rerun-requested")
        self.assertEqual(self.run_rewritten(rerun).returncode, 0)

        reused_again = self.request()
        self.assert_runner(reused_again, False)
        source = self.state()["evidence_state"]["sources"][
            CLICK_EVIDENCE.evidence_key("E1")
        ]
        self.assertEqual(source["reuse_policy"], "conditional")

    def test_always_run_and_required_outputs_cannot_be_relaxed_by_omission(self) -> None:
        first = self.request(reuse="always-run")
        self.assertEqual(self.reason(), "always-run-policy")
        self.assertEqual(self.run_rewritten(first).returncode, 0)

        second = self.request()
        self.assert_runner(second)
        self.assertEqual(self.reason(), "always-run-policy")
        self.assertEqual(self.run_rewritten(second).returncode, 0)

        # A new dynamic evidence id proves required-output semantics without
        # inheriting the always-run owner policy above.
        output_bound = self.verify_checks(
            [
                {
                    "evidence_id": "E_OUTPUT",
                    **self.check(outputs_required=True),
                }
            ],
            turn_id="turn-1",
            version=3,
            bind_default=False,
        )
        self.assert_runner(output_bound)
        self.assertEqual(self.reason(), "required-output-not-guaranteed")
        self.assertEqual(self.run_rewritten(output_bound).returncode, 0)
        output_bound_again = self.verify_checks(
            [{"evidence_id": "E_OUTPUT", **self.check()}],
            turn_id="turn-1",
            version=3,
            bind_default=False,
        )
        self.assert_runner(output_bound_again)
        self.assertEqual(self.reason(), "required-output-not-guaranteed")

    def test_failed_forced_rerun_invalidates_the_previous_pass(self) -> None:
        fixture = self.workspace / "verification_fixture.py"
        fixture.write_text(
            fixture.read_text(encoding="utf-8").replace(
                "import unittest\n", "import os\nimport unittest\n"
            ).replace(
                "    def test_fail(self):\n",
                "    def test_controlled(self):\n"
                "        self.assertNotEqual(os.environ.get('CLICK_TEST_FAIL'), '1')\n\n"
                "    def test_fail(self):\n",
            ),
            encoding="utf-8",
        )
        # Keep the same command identity while the child environment controls
        # whether the second, explicitly forced execution succeeds.
        command = [
            sys.executable,
            "-m",
            "unittest",
            "verification_fixture.VerificationFixture.test_controlled",
        ]
        with mock.patch.dict(os.environ, {"CLICK_TEST_FAIL": "0"}):
            first = self.verify_checks(
                [{"argv": command, "class": "targeted"}],
                turn_id="turn-1",
                version=3,
            )
            self.assertEqual(self.run_rewritten(first).returncode, 0)
            forced = self.verify_checks(
                [{"argv": command, "class": "targeted", "reuse": "rerun"}],
                turn_id="turn-1",
                version=3,
            )
            failed = self.run_rewritten(
                forced, environment_updates={"CLICK_TEST_FAIL": "1"}
            )
        self.assertNotEqual(failed.returncode, 0)
        source = self.state()["evidence_state"]["sources"][
            CLICK_EVIDENCE.evidence_key("E1")
        ]
        self.assertEqual(source["status"], "failed")
        self.assertEqual(source["verified_revision"], -1)
        retry = self.verify_checks(
            [{"argv": command, "class": "targeted"}],
            turn_id="turn-1",
            version=3,
        )
        self.assert_runner(retry)

    def test_ignored_input_content_change_forces_run_and_private_export(self) -> None:
        local_input = self.workspace / ".local-input.txt"
        private_sample = "private-value-that-must-not-be-exported"
        local_input.write_text(private_sample, encoding="utf-8")
        first = self.request(inputs=[".local-input.txt"])
        self.assertEqual(self.reason(), "explicit-input-receipt-missing")
        self.assertEqual(self.run_rewritten(first).returncode, 0)

        export = self.pre_tool(
            "Bash", "click-gate receipt export", "turn-1",
            submit_prompt=False, tool_use_id="private-receipt",
        )
        assert export is not None
        exported = self.run_rewritten(export)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertNotIn(".local-input.txt", exported.stdout)
        self.assertNotIn(private_sample, exported.stdout)

        local_input.write_text("changed-private-value", encoding="utf-8")
        changed = self.request(inputs=[".local-input.txt"])
        self.assert_runner(changed)
        self.assertEqual(self.reason(), "explicit-input-changed")
        self.assertEqual(self.run_rewritten(changed).returncode, 0)

    def test_tracked_explicit_input_change_forces_a_real_run(self) -> None:
        tracked = self.workspace / "tracked-input.txt"
        tracked.write_text("first", encoding="utf-8")
        self.initialize_git("tracked-input.txt")
        first = self.request(inputs=["tracked-input.txt"])
        self.assertEqual(self.run_rewritten(first).returncode, 0)

        tracked.write_text("second", encoding="utf-8")
        changed = self.request(inputs=["tracked-input.txt"])
        self.assert_runner(changed)
        self.assertEqual(self.reason(), "explicit-input-changed")
        self.assertEqual(self.run_rewritten(changed).returncode, 0)

    def test_input_changed_by_the_check_cannot_create_a_reusable_pass(self) -> None:
        local_input = self.workspace / ".local-input.txt"
        local_input.write_text("before", encoding="utf-8")
        fixture = self.workspace / "verification_fixture.py"
        fixture.write_text(
            fixture.read_text(encoding="utf-8")
            + "\nclass InputMutationFixture(unittest.TestCase):\n"
            + "    def test_mutates_input(self):\n"
            + "        from pathlib import Path\n"
            + "        Path('.local-input.txt').write_text('after', encoding='utf-8')\n",
            encoding="utf-8",
        )
        command = [
            sys.executable,
            "-m",
            "unittest",
            "verification_fixture.InputMutationFixture.test_mutates_input",
        ]
        request = self.verify_checks(
            [
                {
                    "argv": command,
                    "class": "targeted",
                    "inputs": [".local-input.txt"],
                }
            ],
            turn_id="turn-1",
            version=3,
        )
        result = self.run_rewritten(request)
        self.assertEqual(result.returncode, 3)
        self.assertIn("explicit verification input changed", result.stderr)
        source = self.state()["evidence_state"]["sources"][
            CLICK_EVIDENCE.evidence_key("E1")
        ]
        self.assertEqual(source["status"], "failed")
        self.assertEqual(source["verified_revision"], -1)

    def test_missing_file_glob_membership_and_profile_expansion_force_runs(self) -> None:
        pattern = "generated/*.json"
        missing = self.request(inputs=[pattern])
        self.assertEqual(self.run_rewritten(missing).returncode, 0)
        generated = self.workspace / "generated"
        generated.mkdir()
        (generated / "one.json").write_text("{}", encoding="utf-8")
        created = self.request(inputs=[pattern])
        self.assert_runner(created)
        self.assertEqual(self.reason(), "explicit-input-changed")
        self.assertEqual(self.run_rewritten(created).returncode, 0)
        (generated / "two.json").write_text("{}", encoding="utf-8")
        membership = self.request(inputs=[pattern])
        self.assert_runner(membership)
        self.assertEqual(self.reason(), "explicit-input-changed")
        self.assertEqual(self.run_rewritten(membership).returncode, 0)

        (self.workspace / "profile-a.txt").write_text("a", encoding="utf-8")
        expanded = self.request(inputs=["profile-a.txt"])
        self.assert_runner(expanded)
        self.assertEqual(self.reason(), "explicit-input-receipt-missing")
        source = self.state()["evidence_state"]["sources"][
            CLICK_EVIDENCE.evidence_key("E1")
        ]
        self.assertEqual(
            source["input_patterns"], ["generated/*.json", "profile-a.txt"]
        )

    def test_explicit_input_condition_survives_successor_requalification(self) -> None:
        (self.workspace / ".local-input.txt").write_text("stable", encoding="utf-8")
        first = self.request(inputs=[".local-input.txt"])
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        previous_session = self.state()["evidence_session_id"]

        self.prompt_submit("후속 입력 검증", "turn-2")
        successor = self.verify_checks(
            [self.check(inputs=[".local-input.txt"])],
            turn_id="turn-2",
            version=3,
        )
        self.assert_runner(successor, False)
        self.assertEqual(self.reason(), "successor-evidence-current")
        self.assertNotEqual(self.state()["evidence_session_id"], previous_session)

    def test_v2_rejects_new_fields(self) -> None:
        legacy = self.verify_checks(
            [self.check(reuse="rerun")], turn_id="turn-1", version=2
        )
        output = legacy["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("unsupported field", output["permissionDecisionReason"])

    def test_one_evidence_group_cannot_mix_reuse_profiles(self) -> None:
        mixed = self.verify_checks(
            [self.check(), self.check(reuse="rerun")],
            turn_id="turn-1",
            version=3,
        )
        output = mixed["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("same reuse", output["permissionDecisionReason"])

    def test_guarded_authority_still_applies_to_v3(self) -> None:
        self.set_default("guarded", "turn-0")
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        denied = self.verify_checks(
            [self.check(reuse="always-run")], turn_id="turn-1", version=3
        )
        self.assertEqual(
            denied["hookSpecificOutput"]["permissionDecision"], "deny"
        )


if __name__ == "__main__":
    unittest.main()
