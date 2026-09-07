from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys
import unittest

from hooks import click_diagnostics, click_verification
from click_gate_test_support import ClickGateTestCase


class FailureCollectionPolicyTests(unittest.TestCase):
    def checks(self) -> list[dict]:
        return [
            {
                "evidence_id": evidence_id,
                "argv": ["git", "diff", "--check"],
                "class": "targeted",
            }
            for evidence_id in ("E1", "E2")
        ]

    def test_absent_policy_is_fail_fast_off(self) -> None:
        batch, _, error = click_verification.validate_batch(
            json.dumps({"version": 2, "checks": self.checks()}), "focused"
        )
        self.assertEqual(error, "")
        assert batch is not None
        self.assertEqual(
            batch["failure_collection"],
            {
                "version": 1,
                "mode": "off",
                "independent_sources": [],
                "max_extra_sources": 0,
                "max_extra_failures": 0,
                "start_window_ms": 0,
            },
        )

    def test_bounded_policy_requires_explicit_sources_and_hard_caps(self) -> None:
        base = {
            "version": 1,
            "mode": "bounded",
            "independent_sources": ["E1", "E2"],
            "max_extra_sources": 1,
            "max_extra_failures": 1,
            "start_window_ms": 30_000,
        }
        valid, _, error = click_verification.validate_batch(
            json.dumps(
                {"version": 2, "checks": self.checks(), "failure_collection": base}
            ),
            "focused",
        )
        self.assertEqual(error, "")
        self.assertIsNotNone(valid)
        for changed, expected in (
            ({**base, "independent_sources": ["E1", "missing"]}, "explicitly submitted"),
            ({**base, "max_extra_sources": 4}, "1..3 extra sources"),
            ({**base, "max_extra_failures": 4}, "1..3 extra failures"),
            ({**base, "start_window_ms": 30_001}, "1..30000"),
        ):
            with self.subTest(changed=changed):
                rejected, _, error = click_verification.validate_batch(
                    json.dumps(
                        {
                            "version": 2,
                            "checks": self.checks(),
                            "failure_collection": changed,
                        }
                    ),
                    "focused",
                )
                self.assertIsNone(rejected)
                self.assertIn(expected, error)


class ClickFailureCollectionIntegrationTests(ClickGateTestCase):
    def prepare_git(self, *extra: str) -> None:
        (self.workspace / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        self.initialize_git(".gitignore", "verification_fixture.py", *extra)
        self.prompt_submit("명시한 독립 검증 실패를 제한적으로 수집해줘", "turn-1")

    def policy(
        self,
        source_ids: list[str],
        *,
        extra_sources: int = 3,
        extra_failures: int = 3,
        window_ms: int = 30_000,
    ) -> dict:
        return {
            "version": 1,
            "mode": "bounded",
            "independent_sources": source_ids,
            "max_extra_sources": extra_sources,
            "max_extra_failures": extra_failures,
            "start_window_ms": window_ms,
        }

    def run_request(
        self,
        checks: list[dict],
        *,
        policy: dict | None,
        request_id: str,
    ):
        reporting = click_diagnostics.default_reporting()
        reporting["format"] = "actionable"
        request = {"version": 2, "reporting": reporting, "checks": checks}
        if policy is not None:
            request["failure_collection"] = policy
        payload = self.pre_tool(
            "Bash",
            f"click-gate verify {shlex.quote(json.dumps(request))}",
            "turn-1",
            submit_prompt=False,
            tool_use_id=request_id,
        )
        assert payload is not None
        completed = self.run_rewritten(payload)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        return completed, state

    def check(self, evidence_id: str, exit_code: int) -> dict:
        return {
            "evidence_id": evidence_id,
            "argv": self.verification_argv(exit_code),
            "class": "targeted",
        }

    def latest_sources(self, state: dict) -> list[dict]:
        return state["verification"]["incremental_history"][-1]["sources"]

    def test_default_remains_fail_fast(self) -> None:
        self.prepare_git()
        completed, state = self.run_request(
            [self.check("E1", 1), self.check("E2", 0)],
            policy=None,
            request_id="default-fail-fast",
        )
        self.assertEqual(completed.returncode, 1)
        collection = state["verification"][
            click_verification.FAILURE_COLLECTION_STATE_FIELD
        ]
        self.assertEqual(collection["requested_mode"], "off")
        self.assertEqual(collection["status"], "disabled")
        self.assertEqual(collection["stop_reason"], "fail-fast-default")
        self.assertEqual(
            sum(source["started"] for source in self.latest_sources(state)), 1
        )

    def test_a_fail_b_fail_c_success_are_all_recorded_when_explicit(self) -> None:
        self.prepare_git()
        completed, state = self.run_request(
            [
                self.check("E1", 1),
                self.check("E2", 1),
                self.check("E3", 0),
            ],
            policy=self.policy(
                ["E1", "E2", "E3"], extra_sources=2, extra_failures=2
            ),
            request_id="three-independent-sources",
        )
        self.assertEqual(completed.returncode, 1)
        collection = state["verification"][
            click_verification.FAILURE_COLLECTION_STATE_FIELD
        ]
        self.assertEqual(collection["status"], "completed")
        self.assertEqual(collection["additional_sources_started"], 2)
        self.assertEqual(collection["additional_failures"], 1)
        self.assertEqual(collection["boundary_checks"], 2)
        self.assertEqual(collection["admitted_source_ids"], ["E1", "E2", "E3"])
        sources = self.latest_sources(state)
        self.assertEqual(sum(source["started"] for source in sources), 3)
        self.assertEqual(
            sorted(source["status"] for source in sources),
            ["failed", "failed", "passed"],
        )

    def test_same_source_stops_after_its_first_failure(self) -> None:
        self.prepare_git()
        completed, state = self.run_request(
            [
                self.check("E1", 1),
                self.check("E1", 0),
                self.check("E2", 0),
            ],
            policy=self.policy(["E1", "E2"], extra_sources=1),
            request_id="same-source-stop",
        )
        self.assertEqual(completed.returncode, 1)
        sources = self.latest_sources(state)
        e1 = next(source for source in sources if len(source["commands"]) == 2)
        self.assertEqual(
            [command["status"] for command in e1["commands"]],
            ["failed", "not-run"],
        )
        self.assertEqual(sum(source["started"] for source in sources), 2)

    def test_common_runtime_error_stops_collection_conservatively(self) -> None:
        fixture = self.workspace / "verification_fixture.py"
        fixture.write_text(
            fixture.read_text(encoding="utf-8")
            + "\n    def test_error(self):\n        raise RuntimeError('shared setup failed')\n",
            encoding="utf-8",
        )
        self.prepare_git()
        error_check = {
            "evidence_id": "E1",
            "argv": [
                sys.executable,
                "-m",
                "unittest",
                "verification_fixture.VerificationFixture.test_error",
            ],
            "class": "targeted",
        }
        completed, state = self.run_request(
            [error_check, self.check("E2", 0)],
            policy=self.policy(["E1", "E2"], extra_sources=1),
            request_id="unsupported-error",
        )
        self.assertEqual(completed.returncode, 1)
        collection = state["verification"][
            click_verification.FAILURE_COLLECTION_STATE_FIELD
        ]
        self.assertEqual(collection["status"], "stopped")
        self.assertEqual(collection["stop_reason"], "unsupported-failure-profile")
        self.assertEqual(sum(source["started"] for source in self.latest_sources(state)), 1)

    def test_source_budget_stops_before_unadmitted_source(self) -> None:
        self.prepare_git()
        completed, state = self.run_request(
            [
                self.check("E1", 1),
                self.check("E2", 0),
                self.check("E3", 0),
            ],
            policy=self.policy(["E1", "E2", "E3"], extra_sources=1),
            request_id="source-budget",
        )
        self.assertEqual(completed.returncode, 1)
        collection = state["verification"][
            click_verification.FAILURE_COLLECTION_STATE_FIELD
        ]
        self.assertEqual(collection["stop_reason"], "source-budget-exhausted")
        self.assertEqual(sum(source["started"] for source in self.latest_sources(state)), 2)

    def test_workspace_drift_at_source_boundary_stops_before_next_source(self) -> None:
        target = self.workspace / "tracked.txt"
        target.write_text("before\n", encoding="utf-8")
        drift_test = self.workspace / "drift_test.py"
        drift_test.write_text(
            "import unittest\nfrom pathlib import Path\n\n"
            "class DriftTest(unittest.TestCase):\n"
            "    def test_fail_after_drift(self):\n"
            "        Path('tracked.txt').write_text('after\\n')\n"
            "        self.fail('drift failure')\n",
            encoding="utf-8",
        )
        self.prepare_git("tracked.txt", "drift_test.py")
        drift_check = {
            "evidence_id": "E1",
            "argv": [
                sys.executable,
                "-m",
                "unittest",
                "drift_test.DriftTest.test_fail_after_drift",
            ],
            "class": "targeted",
        }
        completed, state = self.run_request(
            [drift_check, self.check("E2", 0)],
            policy=self.policy(["E1", "E2"], extra_sources=1),
            request_id="workspace-drift",
        )
        self.assertNotEqual(completed.returncode, 0)
        collection = state["verification"][
            click_verification.FAILURE_COLLECTION_STATE_FIELD
        ]
        self.assertEqual(collection["stop_reason"], "workspace-drift")
        self.assertEqual(sum(source["started"] for source in self.latest_sources(state)), 1)

    def test_failure_diagnosis_fix_requalification_and_reuse_use_real_runner(self) -> None:
        self.prepare_git()
        checks = [self.check("E1", 1), self.check("E2", 1)]
        policy = self.policy(["E1", "E2"], extra_sources=1, extra_failures=1)
        failed, state = self.run_request(
            checks, policy=policy, request_id="repair-cycle-failures"
        )
        self.assertEqual(failed.returncode, 1)
        records = state["verification"][click_diagnostics.STATE_FIELD]["records"]
        self.assertEqual(len(records), 2)
        self.assertTrue(all(record["failures"] for record in records))
        self.assertTrue(all(record["log_ref"] for record in records))

        mutation_id = "repair-cycle-fix"
        self.assertIsNone(
            self.pre_tool(
                "apply_patch",
                "*** Begin Patch\n*** End Patch",
                "turn-1",
                submit_prompt=False,
                tool_use_id=mutation_id,
            )
        )
        fixture = self.workspace / "verification_fixture.py"
        fixture.write_text(
            fixture.read_text(encoding="utf-8").replace(
                "self.fail('expected verification failure')", "self.assertTrue(True)"
            ),
            encoding="utf-8",
        )
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": "replace the failing fixture assertion"},
            turn_id="turn-1",
            tool_use_id=mutation_id,
        )

        requalified, state = self.run_request(
            checks, policy=policy, request_id="repair-cycle-requalification"
        )
        self.assertEqual(requalified.returncode, 0, requalified.stderr)
        self.assertEqual(
            [source["status"] for source in self.latest_sources(state)],
            ["passed", "passed"],
        )

        reporting = click_diagnostics.default_reporting()
        reporting["format"] = "actionable"
        request = {
            "version": 2,
            "reporting": reporting,
            "failure_collection": policy,
            "checks": checks,
        }
        reuse = self.pre_tool(
            "Bash",
            f"click-gate verify {shlex.quote(json.dumps(request))}",
            "turn-1",
            submit_prompt=False,
            tool_use_id="repair-cycle-reuse",
        )
        assert reuse is not None
        rewritten = reuse["hookSpecificOutput"]["updatedInput"]["command"]
        self.assertNotIn("run-verification", rewritten)
        reused = self.run_rewritten(reuse)
        self.assertEqual(reused.returncode, 0, f"{reused.stderr}\n{rewritten}")
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        final_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [source["status"] for source in self.latest_sources(final_state)],
            ["reused", "reused"],
        )


if __name__ == "__main__":
    unittest.main()
