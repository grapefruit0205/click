from __future__ import annotations

import json
import unittest

from hooks import click_incremental
from click_gate_test_support import ClickGateTestCase


class ClickIncrementalPlanTests(unittest.TestCase):
    def test_cancellation_retains_finished_failure_and_never_counts_unstarted_as_run(self) -> None:
        plan = click_incremental.build_plan([
            self.item("a", "run", "observed-input-changed", "runner"),
            self.item("b", "run", "no-passing-evidence", "runner"),
        ], current_revision=12)
        verification = {}
        click_incremental.store_batch(verification, click_incremental.new_batch(plan, batch_id="a" * 32, revision=12, prepared_ms=None))
        click_incremental.mark_started(verification, "a" * 64)
        click_incremental.mark_completed(verification, "a" * 64, status="failed", reason="command-failed", duration_ms=None)
        click_incremental.interrupt_batch(verification)
        before = click_incremental.history_accounting(verification)
        self.assertEqual(before["executed_source_count"], 1)
        self.assertEqual(before["failed_source_count"], 1)
        self.assertEqual(before["not_run_source_count"], 1)
        self.assertEqual(before["reuse_rate"], 0)
        click_incremental.interrupt_batch(verification)
        self.assertEqual(click_incremental.history_accounting(verification), before)
        self.assertIsNone(click_incremental.batch_summary(click_incremental.current_batch(verification))["executed_duration_ms"])
        self.assertIsNone(click_incremental.history_accounting({})["reuse_rate"])

    def test_control_events_deduplicate_observations_without_storing_reason_prose(self) -> None:
        state = {"status": "staged", "approved_turn_id": "", "verification": {"status": "ready"}}
        event = {"session_id": "fixture", "turn_id": "one", "tool_use_id": "tool-one", "tool_input": {"command": "sensitive secret-token /home/private"}}
        self.assertTrue(click_incremental.record_control_event(state, event, "contract-id-mismatch"))
        self.assertFalse(click_incremental.record_control_event(state, event, "contract-lifecycle-rejected"))
        self.assertTrue(click_incremental.record_control_event(state, event, "verification-guidance"))
        values = click_incremental.control_events(state)
        self.assertEqual([value["effect"] for value in values], ["blocked", "advisory"])
        self.assertNotIn("secret-token", json.dumps(values))
        self.assertNotIn("/home/private", json.dumps(values))
        self.assertEqual(state["approved_turn_id"], "")
        self.assertEqual(state["verification"], {"status": "ready"})

    def test_retained_group_rate_exposes_denominator_window_and_missing_duration(self) -> None:
        plan = click_incremental.build_plan([
            self.item("a", "run", "observed-input-changed", "runner"),
            self.item("b", "reuse-exact", "same-revision-receipt-current", "exact-receipt"),
        ], current_revision=12)
        plan["decisions"][1]["duration_baseline"] = None
        verification = {}
        click_incremental.store_batch(verification, click_incremental.new_batch(plan, batch_id="a" * 32, revision=12, prepared_ms=1))
        click_incremental.record_execution(verification, {"a" * 64: 4}, source_results={"a" * 64: {
            "status": "failed", "started": True, "completed": True, "reason_code": "command-failed",
        }}, reused_keys={"b" * 64}, exit_code=1, runner_duration_ms=7)
        report = click_incremental.history_accounting(verification)
        self.assertEqual(report["reuse_numerator"], 1)
        self.assertEqual(report["request_denominator"], 2)
        self.assertEqual(report["reuse_rate"], 0.5)
        self.assertEqual(report["missing_baseline_duration_count"], 1)
        self.assertEqual(report["failed_source_count"], 1)
        self.assertEqual(report["unit"], "verification-group-request")
        self.assertEqual(report["window"], "retained-history")
        self.assertIsNotNone(report["from_timestamp"])
        verification[click_incremental.HISTORY_FIELD] *= 2
        self.assertEqual(click_incremental.history_accounting(verification), report)

    def test_request_timing_is_partial_and_unknown_clock_stays_null(self) -> None:
        plan = click_incremental.build_plan([
            self.item("a", "reuse-exact", "same-revision-receipt-current", "exact-receipt"),
        ], current_revision=12)
        verification = {}
        batch = click_incremental.new_batch(plan, batch_id="a" * 32, revision=12, prepared_ms=1)
        click_incremental.start_request_clock(verification, batch["batch_id"], started_ns=1_000_000, clock_id="fixture-clock")
        click_incremental.complete_request_timing(verification, batch, ended_ns=6_000_000, clock_id="fixture-clock")
        self.assertEqual(batch["request_wall_ms"], 5)
        self.assertEqual(batch["measurement_scope"], "hook-entry-to-result-recording")
        self.assertTrue(click_incremental.batch_is_valid(batch))
        missing = click_incremental.new_batch(plan, batch_id="b" * 32, revision=12, prepared_ms=None)
        click_incremental.complete_request_timing(verification, missing, ended_ns=6_000_000, clock_id="other-clock")
        self.assertIsNone(missing["request_wall_ms"])

    def test_timing_baseline_v2_separates_compatibility_from_reuse_revision(self) -> None:
        source_key = "a" * 64
        check_digest = "f" * 64
        binding = click_incremental.timing_binding_digest(
            source_key=source_key,
            check_digest=check_digest,
            environment_digest="1" * 64,
            executable_digest="2" * 64,
            host_coverage_digest="3" * 64,
            observer_mode="off",
        )
        baseline = click_incremental.build_duration_baseline(
            duration_ms=125.5,
            source_key=source_key,
            revision=7,
            check_digest=check_digest,
            observed_at=10,
            batch_id="b" * 32,
            origin_task={"mode": "guarded", "id": "ctr_" + "c" * 32},
            observer_mode="off",
            timing_binding_digest=binding,
        )

        self.assertTrue(click_incremental.baseline_is_valid(baseline))
        self.assertTrue(click_incremental.timing_baseline_is_valid(baseline))
        self.assertTrue(
            click_incremental.baseline_is_suitable(
                baseline,
                source_key=source_key,
                check_digest=check_digest,
                observer_mode="off",
                timing_binding_digest=binding,
            )
        )
        assert baseline is not None
        baseline["revision"] = 99
        self.assertTrue(
            click_incremental.baseline_is_suitable(
                baseline,
                source_key=source_key,
                check_digest=check_digest,
                observer_mode="off",
                timing_binding_digest=binding,
            )
        )
        self.assertFalse(
            click_incremental.baseline_is_suitable(
                baseline,
                source_key=source_key,
                check_digest=check_digest,
                observer_mode="shadow",
                timing_binding_digest=binding,
            )
        )
        self.assertFalse(
            click_incremental.baseline_is_suitable(
                baseline,
                source_key="b" * 64,
                check_digest=check_digest,
                observer_mode="off",
                timing_binding_digest=binding,
            )
        )

        legacy = {
            "duration_ms": 125.5,
            "revision": 7,
            "check_digest": check_digest,
            "observed_at": 10,
            "batch_id": "b" * 32,
            "sample_count": 1,
        }
        self.assertTrue(click_incremental.baseline_is_valid(legacy))
        self.assertFalse(click_incremental.timing_baseline_is_valid(legacy))
        self.assertFalse(
            click_incremental.baseline_is_suitable(
                legacy,
                source_key=source_key,
                check_digest=check_digest,
                observer_mode="off",
                timing_binding_digest=binding,
            )
        )
        self.assertFalse(
            click_incremental.baseline_is_suitable(
                None,
                source_key=source_key,
                check_digest=check_digest,
                observer_mode="off",
                timing_binding_digest=binding,
            )
        )

    def item(
        self,
        suffix: str,
        selected: str,
        reason: str,
        authority: str,
        *,
        avoided: int = 0,
    ) -> dict[str, object]:
        source_key = suffix * 64
        check_digest = ("f" if suffix != "f" else "e") * 64
        timing_binding = click_incremental.timing_binding_digest(
            source_key=source_key,
            check_digest=check_digest,
            environment_digest="1" * 64,
            executable_digest="2" * 64,
            host_coverage_digest="3" * 64,
            observer_mode="off",
        )
        return click_incremental.decision(
            source_key=source_key,
            decision=selected,
            reason_code=reason,
            current_revision=12,
            previous_revision=11,
            check_digest=check_digest,
            authority_source=authority,
            estimated_avoided_ms=avoided,
            duration_baseline=click_incremental.build_duration_baseline(
                duration_ms=avoided,
                source_key=source_key,
                revision=11,
                check_digest=check_digest,
                observed_at=1,
                batch_id="b" * 32,
                origin_task={"mode": "guarded", "id": "ctr_" + "c" * 32},
                observer_mode="off",
                timing_binding_digest=timing_binding,
            ) if selected in click_incremental.REUSE_DECISIONS else None,
        )

    def test_canonical_plan_drives_only_non_reused_sources_into_runner(self) -> None:
        plan = click_incremental.build_plan(
            [
                self.item(
                    "a",
                    "run",
                    "observed-input-changed",
                    "runner",
                ),
                self.item(
                    "b",
                    "reuse-exact",
                    "same-revision-receipt-current",
                    "exact-receipt",
                    avoided=800,
                ),
                self.item(
                    "c",
                    "reuse-dependency",
                    "observed-dependencies-unchanged",
                    "runtime-dependency-observation",
                    avoided=1_200,
                ),
                self.item(
                    "d",
                    "reuse-safe-change",
                    "safe-change-policy-covered",
                    "repository-safe-change-policy",
                    avoided=400,
                ),
                self.item(
                    "e",
                    "not-evaluable",
                    "observer-incomplete",
                    "none",
                ),
            ],
            current_revision=12,
            planned_at=1,
        )

        self.assertTrue(click_incremental.plan_is_valid(plan))
        self.assertEqual(plan["total_source_count"], 5)
        self.assertEqual(plan["planned_execution_source_count"], 2)
        self.assertEqual(plan["planned_reuse_source_count"], 3)
        self.assertNotIn("executed_source_count", plan)
        self.assertNotIn("executed_duration_ms", plan)
        self.assertEqual(click_incremental.keys_to_execute(plan), {"a" * 64, "e" * 64})

    def test_measured_execution_updates_time_without_changing_decisions(self) -> None:
        plan = click_incremental.build_plan(
            [
                self.item("a", "run", "observed-input-changed", "runner"),
                self.item(
                    "b",
                    "reuse-exact",
                    "same-revision-receipt-current",
                    "exact-receipt",
                    avoided=50,
                ),
            ],
            current_revision=12,
            planned_at=1,
        )
        verification: dict[str, object] = {}
        click_incremental.store_plan(verification, plan)
        click_incremental.store_batch(verification, click_incremental.new_batch(
            plan, batch_id="a" * 32, revision=12, prepared_ms=3,
        ))

        recorded = click_incremental.record_execution(
            verification, {"a" * 64: 125},
            source_results={"a" * 64: {
                "status": "passed", "started": True, "completed": True, "reason_code": "command-passed",
            }}, reused_keys={"b" * 64}, exit_code=0, runner_duration_ms=150,
        )

        self.assertTrue(recorded)
        stored = click_incremental.current_plan(verification)
        assert stored is not None
        self.assertEqual(stored, plan)
        self.assertEqual(
            [item["decision"] for item in stored["decisions"]],
            ["run", "reuse-exact"],
        )
        summary = click_incremental.summary(verification)
        self.assertEqual(summary["executed_source_count"], 1)
        self.assertEqual(summary["authoritative_reuse_count"], 1)
        self.assertEqual(summary["executed_duration_ms"], 125)
        self.assertEqual(summary["estimated_avoided_ms"], 50)
        self.assertNotIn("actual_saved_ms", summary)
        self.assertFalse(any("candidate" in field for field in summary))

    def test_history_drops_oldest_by_age_count_and_size(self) -> None:
        verification: dict[str, object] = {}
        for revision in range(1, 1_006):
            item = click_incremental.decision(
                source_key=f"{revision:064x}",
                decision="run",
                reason_code="no-passing-evidence",
                current_revision=revision,
                previous_revision=revision - 1,
                check_digest="f" * 64,
                authority_source="runner",
            )
            plan = click_incremental.build_plan(
                [item], current_revision=revision, planned_at=revision
            )
            click_incremental.append_plan_history(verification, plan)
        history = click_incremental.current_history(verification)
        self.assertEqual(len(history), click_incremental.MAX_HISTORY_EVENTS)
        self.assertEqual(history[0]["timestamp"], 6)
        self.assertEqual(history[-1]["timestamp"], 1_005)

        recent = click_incremental.prune_history(
            history,
            now=1_005,
            max_events=1_000,
            max_age_seconds=10,
            max_bytes=click_incremental.MAX_HISTORY_BYTES,
        )
        self.assertEqual(recent[0]["timestamp"], 995)
        size_limited = click_incremental.prune_history(
            recent,
            now=1_005,
            max_events=1_000,
            max_age_seconds=10,
            max_bytes=400,
        )
        self.assertLess(len(size_limited), len(recent))
        self.assertEqual(size_limited[-1]["timestamp"], 1_005)

    def test_history_contains_only_bounded_aggregate_fields(self) -> None:
        plan = click_incremental.build_plan(
            [
                self.item(
                    "a",
                    "reuse-safe-change",
                    "safe-change-policy-covered",
                    "repository-safe-change-policy",
                    avoided=40,
                )
            ],
            current_revision=12,
            planned_at=100,
        )
        verification: dict[str, object] = {}
        click_incremental.append_plan_history(verification, plan)
        history = click_incremental.current_history(verification)

        self.assertTrue(click_incremental.history_is_valid(history))
        self.assertEqual(
            set(history[0]),
            {
                "event",
                "source_key",
                "decision",
                "reason",
                "current_revision",
                "previous_revision",
                "estimated_avoided_ms",
                "timestamp",
            },
        )
        encoded = json.dumps(history, sort_keys=True)
        for forbidden in ("argv", "/home/", "prompt", "token", "environment"):
            self.assertNotIn(forbidden, encoded)

    def test_plan_contains_no_argv_paths_or_natural_language_authority(self) -> None:
        plan = click_incremental.build_plan(
            [
                self.item(
                    "a",
                    "reuse-exact",
                    "same-revision-receipt-current",
                    "exact-receipt",
                )
            ],
            current_revision=12,
            planned_at=1,
        )
        encoded = json.dumps(plan, sort_keys=True)

        for forbidden in ("argv", "/home/", "prompt", "token", "src/auth/token.py"):
            self.assertNotIn(forbidden, encoded)

    def test_non_reuse_decision_cannot_claim_avoided_time_or_reuse_authority(self) -> None:
        with self.assertRaises(ValueError):
            click_incremental.decision(
                source_key="a" * 64,
                decision="run",
                reason_code="observed-input-changed",
                current_revision=1,
                previous_revision=0,
                check_digest="b" * 64,
                authority_source="exact-receipt",
                estimated_avoided_ms=1,
            )


class ClickObservedControlTests(ClickGateTestCase):
    hook_in_process = True

    def test_real_hook_records_approval_and_id_denials_without_granting_authority(self) -> None:
        self.set_default("guarded", "turn-0")
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        denied = self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-1")
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.arm_gate("turn-2")
        mismatch = self.pass_gate("ctr_" + "0" * 32, "turn-2")
        self.assertEqual(mismatch["hookSpecificOutput"]["permissionDecision"], "deny")
        state_path = next((self.plugin_data / "gate-state").glob("session-contract-*.json"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "staged")
        self.assertEqual(state["approved_turn_id"], "")
        self.assertEqual({item["code"] for item in click_incremental.control_events(state)}, {"approval-required", "contract-id-mismatch"})


if __name__ == "__main__":
    unittest.main()
