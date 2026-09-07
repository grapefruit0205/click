from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from benchmarks import task_efficiency


COMPLETION_DIGEST = "a" * 64


def usage(
    total: int,
    *,
    status: str = "complete",
    semantics: str = "incremental",
    event_id: str = "usage-1",
) -> dict:
    return {
        "version": 1,
        "scope_status": status,
        "covers_task_boundary": status == "complete",
        "input_semantics": "includes-cached",
        "output_semantics": "includes-reasoning",
        "counter_semantics": semantics,
        "events": [
            {
                "event_id": event_id,
                "response_id": "response-1",
                "sequence": 1,
                "input_tokens": total * 3 // 4,
                "output_tokens": total - total * 3 // 4,
                "cached_input_tokens": 0,
                "reasoning_output_tokens": 0,
            }
        ],
    }


def run(
    elapsed_ms: int,
    total_tokens: int,
    *,
    completion_status: str = "completed",
    completion_digest: str = COMPLETION_DIGEST,
    usage_status: str = "complete",
    intervention_count: int = 0,
) -> dict:
    return {
        "task": {
            "boundary_status": "complete",
            "started_at_ms": 1000,
            "finished_at_ms": 1000 + elapsed_ms,
            "completion_status": completion_status,
            "acceptance_status": "passed" if completion_status == "completed" else "unavailable",
            "completion_digest": completion_digest,
            "acceptance_version": "acceptance-v1",
        },
        "usage": usage(total_tokens, status=usage_status),
        "activity_intervals": [],
        "user_intervention": {
            "additional_approval": intervention_count,
            "question_response": 0,
            "status_check": 0,
            "configuration_recovery": 0,
            "observed_duration_ms": intervention_count * 100,
        },
        "observed": {
            "tool_calls": 2,
            "post_failure_calls_before_mutation": 1,
            "repair_cycles": 1,
            "model_round_trips": 1,
        },
    }


def pair(
    baseline_ms: int,
    improved_ms: int,
    baseline_tokens: int,
    improved_tokens: int,
    *,
    pair_id: str = "pair-1",
    baseline_variant: str = "B0",
    improved_variant: str = "B2",
    scenario: str = "independent-source-failures",
    run_kind: str = "prepared-repeat",
    runtime_mode: str = "evidence",
) -> dict:
    return {
        "id": pair_id,
        "baseline_variant": baseline_variant,
        "improved_variant": improved_variant,
        "scenario": scenario,
        "run_kind": run_kind,
        "runtime_mode": runtime_mode,
        "baseline": run(baseline_ms, baseline_tokens, intervention_count=2),
        "improved": run(improved_ms, improved_tokens, intervention_count=1),
    }


def evaluation(comparisons: list[dict]) -> dict:
    return {
        "kind": task_efficiency.INTERNAL_KIND,
        "version": 1,
        "generated_at": 1234,
        "measurement_reason": "",
        "comparisons": comparisons,
    }


class TaskEfficiencyPairTests(unittest.TestCase):
    def test_positive_zero_negative_and_tiny_signs_are_preserved(self) -> None:
        cases = (
            (1000, 800, 1000, 800, 0.2, "faster"),
            (1000, 1000, 1000, 1000, 0.0, "unchanged"),
            (1000, 1200, 1000, 1100, -0.2, "slower"),
            (1_000_000, 999_999, 1_000_000, 999_999, 0.000001, "faster"),
            (1_000_000, 1_000_001, 1_000_000, 1_000_001, -0.000001, "slower"),
        )
        for index, (base_ms, new_ms, base_tokens, new_tokens, expected, status) in enumerate(cases):
            with self.subTest(index=index):
                result = task_efficiency.evaluate_pair(
                    pair(
                        base_ms,
                        new_ms,
                        base_tokens,
                        new_tokens,
                        pair_id=f"pair-{index}",
                    )
                )
                self.assertAlmostEqual(
                    result["task_completion_time_savings_ratio"], expected
                )
                token_expected = 1 - new_tokens / base_tokens
                self.assertAlmostEqual(result["token_savings_ratio"], token_expected)
                public = task_efficiency.public_projection(evaluation([
                    pair(
                        base_ms,
                        new_ms,
                        base_tokens,
                        new_tokens,
                        pair_id=f"pair-{index}",
                    )
                ]))["presentations"][0]
                self.assertEqual(public["task_effect_status"], status)
                self.assertEqual(
                    (public["token_savings_ratio"] > 0)
                    - (public["token_savings_ratio"] < 0),
                    (token_expected > 0) - (token_expected < 0),
                )

    def test_missing_boundaries_usage_and_zero_denominators_are_null(self) -> None:
        candidate = pair(1000, 800, 1000, 800)
        candidate["baseline"]["task"]["boundary_status"] = "missing"
        candidate["improved"]["usage"]["scope_status"] = "partial"
        result = task_efficiency.evaluate_pair(candidate)
        self.assertIsNone(result["task_completion_time_savings_ratio"])
        self.assertIsNone(result["token_savings_ratio"])
        self.assertIn("boundary", result["task_time_reason"])
        self.assertIn("incomplete", result["token_reason"])

        zero = pair(0, 0, 0, 0)
        result = task_efficiency.evaluate_pair(zero)
        self.assertIsNone(result["task_completion_time_savings_ratio"])
        self.assertEqual(result["task_time_reason"], "baseline-task-duration-zero")
        self.assertIsNone(result["token_savings_ratio"])
        self.assertEqual(result["token_reason"], "baseline-token-total-zero")

    def test_completion_quality_mismatch_blocks_both_effect_ratios(self) -> None:
        candidate = pair(1000, 500, 1000, 500)
        candidate["improved"]["task"]["completion_digest"] = "b" * 64
        result = task_efficiency.evaluate_pair(candidate)
        self.assertFalse(result["equivalent_completion"])
        self.assertEqual(result["reason"], "completion-digest-mismatch")
        self.assertIsNone(result["task_completion_time_savings_ratio"])
        self.assertIsNone(result["token_savings_ratio"])


class TaskEfficiencyUsageTests(unittest.TestCase):
    def test_duplicate_delivery_is_deduplicated_and_conflict_is_null(self) -> None:
        candidate = usage(100)
        candidate["events"].append(dict(candidate["events"][0]))
        total, reason, round_trips = task_efficiency.usage_total(candidate)
        self.assertEqual((total, reason, round_trips), (100, "", 1))

        candidate["events"][1]["output_tokens"] += 1
        total, reason, round_trips = task_efficiency.usage_total(candidate)
        self.assertIsNone(total)
        self.assertEqual(reason, "usage-duplicate-conflict")
        self.assertIsNone(round_trips)

    def test_cumulative_snapshots_use_final_monotonic_value(self) -> None:
        candidate = usage(100, semantics="cumulative")
        candidate["events"] = [
            {
                "event_id": "event-1",
                "response_id": "response-1",
                "sequence": 1,
                "input_tokens": 40,
                "output_tokens": 10,
                "cached_input_tokens": 4,
                "reasoning_output_tokens": 2,
            },
            {
                "event_id": "event-2",
                "response_id": "response-2",
                "sequence": 2,
                "input_tokens": 75,
                "output_tokens": 25,
                "cached_input_tokens": 8,
                "reasoning_output_tokens": 5,
            },
        ]
        self.assertEqual(task_efficiency.usage_total(candidate), (100, "", 2))
        candidate["events"][1]["input_tokens"] = 30
        total, reason, _ = task_efficiency.usage_total(candidate)
        self.assertIsNone(total)
        self.assertEqual(reason, "usage-cumulative-counter-regressed")

    def test_cache_and_reasoning_are_validated_but_not_added_twice(self) -> None:
        candidate = usage(100)
        candidate["events"][0]["cached_input_tokens"] = 30
        candidate["events"][0]["reasoning_output_tokens"] = 10
        self.assertEqual(task_efficiency.usage_total(candidate)[0], 100)
        candidate["input_semantics"] = "excludes-cached"
        total, reason, _ = task_efficiency.usage_total(candidate)
        self.assertIsNone(total)
        self.assertEqual(reason, "usage-input-semantics-unknown")


class TaskEfficiencyPublicProjectionTests(unittest.TestCase):
    def test_public_projection_has_no_absolute_token_or_raw_usage_fields(self) -> None:
        raw = evaluation([pair(1000, 800, 1000, 700)])
        result = task_efficiency.evaluate_pair(raw["comparisons"][0])
        self.assertEqual(result["baseline_total_tokens"], 1000)
        self.assertEqual(result["improved_total_tokens"], 700)

        public = task_efficiency.public_projection(raw)
        self.assertFalse(task_efficiency.public_has_forbidden_usage(public))
        rendered = json.dumps(public)
        for forbidden in (
            "baseline_total_tokens", "improved_total_tokens", "saved_tokens",
            "input_tokens", "output_tokens", '"usage"', '"events"',
        ):
            self.assertNotIn(forbidden, rendered)
        presentation = public["presentations"][0]
        self.assertAlmostEqual(presentation["token_savings_ratio"], 0.3)
        self.assertEqual(
            presentation["token_aggregation"], "ratio-of-complete-pair-totals"
        )

    def test_incompatible_or_empty_evaluation_stays_unmeasured(self) -> None:
        incompatible = task_efficiency.public_projection(
            {"kind": task_efficiency.INTERNAL_KIND, "version": 99}
        )
        self.assertEqual(incompatible["measurement_status"], "unmeasured")
        self.assertEqual(
            incompatible["measurement_reason"], "evaluation-version-incompatible"
        )
        empty = task_efficiency.public_projection(
            {
                "kind": task_efficiency.INTERNAL_KIND,
                "version": 1,
                "generated_at": 10,
                "measurement_reason": "host-task-boundaries-unavailable",
                "comparisons": [],
            }
        )
        self.assertEqual(empty["presentations"], [])
        self.assertEqual(empty["measurement_status"], "unmeasured")
        self.assertEqual(
            empty["measurement_reason"], "host-task-boundaries-unavailable"
        )

    def test_scenarios_modes_and_first_use_are_not_merged(self) -> None:
        comparisons = [
            pair(1000, 800, 1000, 800, pair_id="one"),
            pair(
                1000,
                900,
                1000,
                900,
                pair_id="two",
                run_kind="first-use",
            ),
            pair(
                1000,
                700,
                1000,
                700,
                pair_id="three",
                runtime_mode="guarded",
            ),
            pair(
                1000,
                600,
                1000,
                600,
                pair_id="four",
                scenario="common-setup-error",
            ),
        ]
        public = task_efficiency.public_projection(evaluation(comparisons))
        self.assertEqual(len(public["presentations"]), 4)
        self.assertTrue(all(item["sample_count"] == 1 for item in public["presentations"]))

    def test_adverse_and_incomplete_samples_are_retained(self) -> None:
        faster = pair(1000, 800, 1000, 800, pair_id="faster")
        slower = pair(1000, 1200, 1000, 1200, pair_id="slower")
        failed = pair(1000, 700, 1000, 700, pair_id="failed")
        failed["improved"] = run(700, 700, completion_status="failed")
        public = task_efficiency.public_projection(
            evaluation([faster, slower, failed])
        )["presentations"][0]
        self.assertEqual(public["sample_count"], 3)
        self.assertEqual(public["faster_sample_count"], 1)
        self.assertEqual(public["slower_sample_count"], 1)
        self.assertEqual(public["incomplete_sample_count"], 1)
        self.assertEqual(public["failed_sample_count"], 1)


class TaskEfficiencyHostAdapterTests(unittest.TestCase):
    def test_adapter_uses_direct_boundaries_and_preserves_overlapping_activity(self) -> None:
        events = [
            {"type": "task.started", "task_id": "task-1", "timestamp_ms": 100},
            {
                "type": "turn.completed",
                "task_id": "task-1",
                "event_id": "usage-1",
                "response_id": "response-1",
                "sequence": 1,
                "usage": {
                    "input_tokens": 70,
                    "output_tokens": 30,
                    "cached_input_tokens": 20,
                    "reasoning_output_tokens": 5,
                },
            },
            {"type": "tool.completed", "task_id": "task-1", "tool_call_id": "tool-1", "timestamp_ms": 250},
            {"type": "tool.completed", "task_id": "task-1", "tool_call_id": "tool-1", "timestamp_ms": 251},
            {"type": "verification.failed", "task_id": "task-1", "timestamp_ms": 200},
            {"type": "tool.completed", "task_id": "task-1", "tool_call_id": "tool-2", "timestamp_ms": 260},
            {"type": "file.mutated", "task_id": "task-1", "timestamp_ms": 300},
            {"type": "activity.interval", "task_id": "task-1", "kind": "implementation", "started_at_ms": 200, "finished_at_ms": 500},
            {"type": "activity.interval", "task_id": "task-1", "kind": "verification", "started_at_ms": 400, "finished_at_ms": 600},
            {"type": "user.intervention", "task_id": "task-1", "kind": "status_check", "observed_duration_ms": 50},
            {
                "type": "task.finished",
                "task_id": "task-1",
                "timestamp_ms": 1100,
                "completion_status": "completed",
                "acceptance_status": "passed",
                "completion_digest": COMPLETION_DIGEST,
            },
        ]
        adapted = task_efficiency.adapt_host_events(
            events,
            task_id="task-1",
            acceptance_version="acceptance-v1",
            usage_scope_complete=True,
            input_semantics="includes-cached",
            output_semantics="includes-reasoning",
            counter_semantics="incremental",
        )
        self.assertEqual(adapted["task"]["finished_at_ms"] - adapted["task"]["started_at_ms"], 1000)
        self.assertEqual(len(adapted["activity_intervals"]), 2)
        self.assertEqual(adapted["observed"]["tool_calls"], 2)
        self.assertEqual(adapted["observed"]["post_failure_calls_before_mutation"], 2)
        self.assertIsNone(adapted["observed"]["model_round_trips"])
        self.assertEqual(adapted["hidden_reasoning"], "unknown")
        self.assertEqual(task_efficiency.usage_total(adapted["usage"])[0], 100)

    def test_cli_writes_only_public_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "internal.json"
            destination = root / "public.json"
            source.write_text(json.dumps(evaluation([pair(1000, 800, 1000, 800)])), encoding="utf-8")
            self.assertEqual(
                task_efficiency.main(
                    [str(source), "--public-output", str(destination)]
                ),
                0,
            )
            public = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(public["kind"], task_efficiency.PUBLIC_KIND)
            self.assertFalse(task_efficiency.public_has_forbidden_usage(public))


if __name__ == "__main__":
    unittest.main()
