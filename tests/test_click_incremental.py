from __future__ import annotations

import json
import unittest
from unittest import mock

from hooks import click_incremental
from click_gate_test_support import ClickGateTestCase


class ClickIncrementalPlanTests(unittest.TestCase):
    def test_history_size_boundary_preserves_order_and_detachment(self) -> None:
        plan = click_incremental.build_plan([
            self.item("a", "run", "no-passing-evidence", "runner"),
        ], current_revision=12, planned_at=100)
        batch = click_incremental.new_batch(plan, batch_id="a" * 32, revision=12, prepared_ms=1)
        batches = []
        for number in range(8):
            item = json.loads(json.dumps(batch))
            item.update(batch_id=f"{number:032x}", timestamp=100 + number)
            batches.append(item)
        encoded_size = len(click_incremental._canonical_bytes(batches[-3:]))
        for limit, expected in ((1, []), (encoded_size - 1, batches[-2:]),
                                (encoded_size, batches[-3:]), (encoded_size + 1, batches[-3:])):
            with self.subTest(limit=limit):
                result = click_incremental.prune_history(
                    reversed(batches), now=107, max_bytes=limit,
                )
                self.assertEqual(result, expected)
                if result:
                    result[-1]["sources"][0]["label"] = "detached"
                    self.assertNotEqual(batches[-1]["sources"][0]["label"], "detached")

    def test_retained_reuse_reasons_count_decisions_and_rank_rerun_reasons(self) -> None:
        plan = click_incremental.build_plan([
            self.item("a", "run", "observed-input-changed", "runner"),
            self.item("b", "run", "environment-binding-changed", "runner"),
            self.item("c", "reuse-exact", "same-revision-receipt-current", "exact-receipt"),
        ], current_revision=12)
        first = click_incremental.new_batch(plan, batch_id="a" * 32, revision=12, prepared_ms=1)
        second = json.loads(json.dumps(first))
        second.update(batch_id="b" * 32, timestamp=first["timestamp"] + 1)
        second["sources"][1]["reason_code"] = "observed-input-changed"
        duplicate = json.loads(json.dumps(first))

        reasons = click_incremental.retained_reuse_reasons([first, second, duplicate, {"not": "a batch"}])

        self.assertTrue(click_incremental.retained_reuse_reasons_is_valid(reasons))
        self.assertEqual(reasons["request_count"], 6)
        self.assertEqual(reasons["decisions"]["run"], 4)
        self.assertEqual(reasons["decisions"]["reuse-exact"], 2)
        self.assertEqual(reasons["run_reasons"], [
            {"reason_code": "observed-input-changed", "count": 3},
            {"reason_code": "environment-binding-changed", "count": 1},
        ])
        empty = click_incremental.retained_reuse_reasons([])
        self.assertTrue(click_incremental.retained_reuse_reasons_is_valid(empty))
        self.assertEqual((empty["request_count"], empty["run_reasons"]), (0, []))
        self.assertFalse(click_incremental.retained_reuse_reasons_is_valid({**reasons, "request_count": 5}))
        self.assertFalse(click_incremental.retained_reuse_reasons_is_valid({**reasons, "run_reasons": list(reversed(reasons["run_reasons"]))}))
        self.assertFalse(click_incremental.retained_reuse_reasons_is_valid({**reasons, "window": "current"}))

    def test_history_projection_uses_one_retention_window_without_sharing_state(self) -> None:
        plan = click_incremental.build_plan([
            self.item("a", "run", "no-passing-evidence", "runner"),
        ], current_revision=12)
        verification = {}
        click_incremental.store_batch(verification, click_incremental.new_batch(
            plan, batch_id="a" * 32, revision=12, prepared_ms=1,
        ))
        click_incremental.mark_started(verification, "a" * 64)
        click_incremental.mark_completed(verification, "a" * 64, status="passed",
                                         reason="command-passed", duration_ms=2)
        before = json.dumps(verification)
        expected = (click_incremental.current_batch(verification),
                    click_incremental.history_accounting(verification),
                    click_incremental.history_totals(verification))
        with mock.patch.object(click_incremental, "prune_history", wraps=click_incremental.prune_history) as prune:
            view = click_incremental.history_projection(verification)
        self.assertEqual(prune.call_count, 1)
        self.assertEqual((view["current"], view["accounting"], view["totals"]), expected)
        view["current"]["sources"][0]["label"] = "display only"
        self.assertEqual(json.dumps(verification), before)

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

    @staticmethod
    def command(
        position: int, source_position: int, digest_marker: str
    ) -> dict[str, object]:
        return {
            "position": position,
            "source_position": source_position,
            "check_digest": digest_marker * 64,
        }

    @staticmethod
    def completed_command(
        plan: dict[str, object],
        *,
        status: str,
        exit_code: int,
        started_offset_ms: float,
        finished_offset_ms: float,
    ) -> dict[str, object]:
        outcome = click_incremental.planned_command_outcome(plan)
        outcome.update(
            status=status,
            started=True,
            completed=True,
            started_offset_ms=started_offset_ms,
            finished_offset_ms=finished_offset_ms,
            duration_ms=finished_offset_ms - started_offset_ms,
            exit_code=exit_code,
            reason_code=(
                "command-passed" if status == "passed"
                else "command-interrupted" if status == "interrupted"
                else "command-failed"
            ),
            measurement_scope=click_incremental.COMMAND_MEASUREMENT_SCOPE,
        )
        return outcome

    def test_v5_command_outcomes_keep_an_earlier_failure_when_another_source_passes(
        self,
    ) -> None:
        alpha_key = "a" * 64
        beta_key = "b" * 64
        command_plans = {
            alpha_key: [self.command(1, 1, "1"), self.command(2, 2, "2")],
            beta_key: [self.command(3, 1, "3")],
        }
        plan = click_incremental.build_plan(
            [
                self.item("a", "run", "observed-input-changed", "runner"),
                self.item("b", "run", "no-passing-evidence", "runner"),
            ],
            current_revision=12,
        )
        task = {
            "mode": "evidence",
            "id": "evs_" + "c" * 32,
            "name": "command outcome fixture",
        }
        verification: dict[str, object] = {}
        batch = click_incremental.new_batch(
            plan,
            batch_id="d" * 32,
            revision=12,
            prepared_ms=1,
            command_plans=command_plans,
            task=task,
        )
        self.assertEqual(batch["version"], 5)
        self.assertEqual(batch["task"], task)
        self.assertTrue(click_incremental.store_batch(verification, batch))

        results = click_incremental.new_source_results(command_plans)
        self.assertTrue(
            click_incremental.start_source_command(
                results,
                alpha_key,
                position=1,
                check_digest="1" * 64,
                started_offset_ms=1,
            )
        )
        self.assertTrue(
            click_incremental.complete_source_command(
                results,
                alpha_key,
                position=1,
                check_digest="1" * 64,
                status="passed",
                reason="command-passed",
                finished_offset_ms=3,
                duration_ms=2,
                exit_code=0,
            )
        )
        self.assertTrue(
            click_incremental.start_source_command(
                results,
                alpha_key,
                position=2,
                check_digest="2" * 64,
                started_offset_ms=4,
            )
        )
        self.assertTrue(
            click_incremental.complete_source_command(
                results,
                alpha_key,
                position=2,
                check_digest="2" * 64,
                status="failed",
                reason="command-failed",
                finished_offset_ms=8,
                duration_ms=4,
                exit_code=7,
            )
        )
        self.assertFalse(
            click_incremental.complete_source_command(
                results,
                alpha_key,
                position=2,
                check_digest="2" * 64,
                status="passed",
                reason="command-passed",
                finished_offset_ms=9,
                duration_ms=5,
                exit_code=0,
            )
        )
        self.assertTrue(
            click_incremental.start_source_command(
                results,
                beta_key,
                position=3,
                check_digest="3" * 64,
                started_offset_ms=9,
            )
        )
        self.assertTrue(
            click_incremental.complete_source_command(
                results,
                beta_key,
                position=3,
                check_digest="3" * 64,
                status="passed",
                reason="command-passed",
                finished_offset_ms=12,
                duration_ms=3,
                exit_code=0,
            )
        )

        alpha = click_incremental.source_command_outcome(
            results[alpha_key], command_plans[alpha_key]
        )
        beta = click_incremental.source_command_outcome(
            results[beta_key], command_plans[beta_key]
        )
        self.assertEqual(alpha, {
            "valid": True,
            "started": True,
            "completed": True,
            "status": "failed",
            "exit_code": 7,
        })
        self.assertEqual(beta["status"], "passed")
        self.assertTrue(
            click_incremental.record_execution(
                verification,
                {alpha_key: 7, beta_key: 3},
                source_results=results,
                reused_keys=set(),
                # This deliberately models a misleading aggregate last result.
                exit_code=0,
                runner_duration_ms=12,
            )
        )
        recorded = click_incremental.current_batch(verification)
        assert recorded is not None
        by_key = {item["source_key"]: item for item in recorded["sources"]}
        self.assertEqual(recorded["status"], "failed")
        self.assertEqual(by_key[alpha_key]["status"], "failed")
        self.assertEqual(by_key[alpha_key]["commands"][1]["exit_code"], 7)
        self.assertEqual(by_key[beta_key]["status"], "passed")

    def test_command_outcome_fold_never_reconstructs_a_success_prefix(self) -> None:
        plans = [
            self.command(1, 1, "1"),
            self.command(2, 2, "2"),
            self.command(3, 3, "3"),
        ]
        commands = [
            self.completed_command(
                plans[0], status="passed", exit_code=0,
                started_offset_ms=1, finished_offset_ms=2,
            ),
            self.completed_command(
                plans[1], status="failed", exit_code=9,
                started_offset_ms=3, finished_offset_ms=4,
            ),
            self.completed_command(
                plans[2], status="passed", exit_code=0,
                started_offset_ms=5, finished_offset_ms=6,
            ),
        ]
        outcome = click_incremental.source_command_outcome(
            {
                "started": True,
                "completed": True,
                "status": "failed",
                "reason_code": "command-failed",
                "commands": commands,
            },
            plans,
        )
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["exit_code"], 9)
        self.assertTrue(outcome["valid"])

        partial = click_incremental.new_source_results({"a" * 64: plans[:2]})
        self.assertTrue(
            click_incremental.start_source_command(
                partial, "a" * 64, position=1,
                check_digest="1" * 64, started_offset_ms=1,
            )
        )
        self.assertTrue(
            click_incremental.complete_source_command(
                partial, "a" * 64, position=1,
                check_digest="1" * 64, status="passed",
                reason="command-passed", finished_offset_ms=2,
                duration_ms=1, exit_code=0,
            )
        )
        incomplete = click_incremental.source_command_outcome(
            partial["a" * 64], plans[:2]
        )
        self.assertTrue(incomplete["valid"])
        self.assertEqual(incomplete["status"], "running")

        missing = json.loads(json.dumps(partial["a" * 64]))
        missing["commands"].pop()
        self.assertFalse(
            click_incremental.source_command_outcome(missing, plans[:2])["valid"]
        )
        duplicate = json.loads(json.dumps(partial["a" * 64]))
        duplicate["commands"][1] = dict(duplicate["commands"][0])
        self.assertFalse(
            click_incremental.source_command_outcome(duplicate, plans[:2])["valid"]
        )
        rebound = json.loads(json.dumps(partial["a" * 64]))
        rebound["commands"][0]["check_digest"] = "f" * 64
        self.assertFalse(
            click_incremental.source_command_outcome(rebound, plans[:2])["valid"]
        )

    def test_command_outcomes_record_interruption_and_unknown_fail_closed(self) -> None:
        source_key = "a" * 64
        plans = {source_key: [self.command(1, 1, "1"), self.command(2, 2, "2")]}
        interrupted = click_incremental.new_source_results(plans)
        self.assertTrue(
            click_incremental.start_source_command(
                interrupted, source_key, position=1,
                check_digest="1" * 64, started_offset_ms=1,
            )
        )
        self.assertTrue(
            click_incremental.complete_source_command(
                interrupted, source_key, position=1,
                check_digest="1" * 64, status="interrupted",
                reason="command-interrupted", finished_offset_ms=2,
                duration_ms=1, exit_code=130,
            )
        )
        result = click_incremental.source_command_outcome(
            interrupted[source_key], plans[source_key]
        )
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["exit_code"], 130)
        self.assertEqual(
            [item["status"] for item in interrupted[source_key]["commands"]],
            ["interrupted", "not-run"],
        )

        unknown = click_incremental.new_source_results(plans)
        self.assertTrue(
            click_incremental.complete_source_command(
                unknown, source_key, position=1,
                check_digest="1" * 64, status="unknown",
                reason="command-error", finished_offset_ms=None,
                duration_ms=None, exit_code=None,
            )
        )
        result = click_incremental.source_command_outcome(
            unknown[source_key], plans[source_key]
        )
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["completed"])
        self.assertFalse(result["started"])

        malformed_planned = click_incremental.planned_command_outcome(
            plans[source_key][0]
        )
        malformed_planned["started_offset_ms"] = 1
        self.assertFalse(
            click_incremental.command_outcome_is_valid(malformed_planned)
        )

    def test_v5_cancellation_between_commands_and_missing_completion_are_explicit(
        self,
    ) -> None:
        source_key = "a" * 64
        command_plans = {
            source_key: [self.command(1, 1, "1"), self.command(2, 2, "2")]
        }
        plan = click_incremental.build_plan(
            [self.item("a", "run", "observed-input-changed", "runner")],
            current_revision=12,
        )
        task = {
            "mode": "evidence",
            "id": "evs_" + "c" * 32,
            "name": "interruption fixture",
        }

        cancelled: dict[str, object] = {}
        self.assertTrue(
            click_incremental.store_batch(
                cancelled,
                click_incremental.new_batch(
                    plan,
                    batch_id="5" * 32,
                    revision=12,
                    prepared_ms=1,
                    command_plans=command_plans,
                    task=task,
                ),
            )
        )
        self.assertTrue(
            click_incremental.mark_command_started(
                cancelled,
                source_key,
                position=1,
                check_digest="1" * 64,
                started_offset_ms=1,
            )
        )
        self.assertTrue(
            click_incremental.mark_command_completed(
                cancelled,
                source_key,
                position=1,
                check_digest="1" * 64,
                status="passed",
                reason="command-passed",
                finished_offset_ms=2,
                duration_ms=1,
                source_duration_ms=1,
                exit_code=0,
            )
        )
        self.assertTrue(click_incremental.interrupt_batch(cancelled))
        batch = click_incremental.current_batch(cancelled)
        assert batch is not None
        self.assertTrue(click_incremental.batch_is_valid(batch))
        self.assertEqual(batch["status"], "interrupted")
        source = batch["sources"][0]
        self.assertEqual(source["status"], "interrupted")
        self.assertEqual(source["execution_reason_code"], "user-cancelled")
        self.assertEqual(
            [item["status"] for item in source["commands"]],
            ["passed", "not-run"],
        )

        missing: dict[str, object] = {}
        self.assertTrue(
            click_incremental.store_batch(
                missing,
                click_incremental.new_batch(
                    plan,
                    batch_id="6" * 32,
                    revision=12,
                    prepared_ms=1,
                    command_plans=command_plans,
                    task=task,
                ),
            )
        )
        self.assertTrue(
            click_incremental.mark_command_started(
                missing,
                source_key,
                position=1,
                check_digest="1" * 64,
                started_offset_ms=1,
            )
        )
        self.assertTrue(
            click_incremental.record_execution(
                missing,
                {},
                source_results={},
                reused_keys=set(),
                exit_code=0,
                runner_duration_ms=2,
            )
        )
        batch = click_incremental.current_batch(missing)
        assert batch is not None
        self.assertTrue(click_incremental.batch_is_valid(batch))
        self.assertEqual(batch["status"], "failed")
        source = batch["sources"][0]
        self.assertEqual(source["status"], "unknown")
        self.assertTrue(source["started"])
        self.assertEqual(
            [item["status"] for item in source["commands"]],
            ["unknown", "not-run"],
        )

    def test_batch_versions_one_through_four_remain_readable_without_command_facts(
        self,
    ) -> None:
        plan = click_incremental.build_plan(
            [self.item("a", "run", "observed-input-changed", "runner")],
            current_revision=12,
        )
        v2 = click_incremental.new_batch(
            plan, batch_id="1" * 32, revision=12, prepared_ms=1
        )
        v3 = json.loads(json.dumps(v2))
        v3["version"] = 3
        v4 = click_incremental.new_batch(
            plan,
            batch_id="4" * 32,
            revision=12,
            prepared_ms=1,
            task={
                "mode": "evidence",
                "id": "evs_" + "c" * 32,
                "name": "legacy batch fixture",
            },
        )
        v1 = json.loads(json.dumps(v2))
        v1["version"] = 1
        for source in v1["sources"]:
            source.pop("reuse_origin")

        for batch in (v1, v2, v3, v4):
            self.assertTrue(click_incremental.batch_is_valid(batch))
            self.assertNotIn("commands", batch["sources"][0])
        verification = {
            click_incremental.HISTORY_FIELD: [v4],
            click_incremental.CURRENT_BATCH_FIELD: v4["batch_id"],
        }
        self.assertIsNone(click_incremental.current_command_plans(verification))
        self.assertIsNone(
            click_incremental.source_command_outcome(
                {
                    "started": True,
                    "completed": False,
                    "status": "running",
                    "reason_code": "command-started",
                },
                [self.command(1, 1, "1")],
            )
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
