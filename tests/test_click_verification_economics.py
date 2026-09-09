"""Parent-relative economics; timings are deterministic inputs, not benchmarks."""
from pathlib import Path
import os
import unittest
from unittest import mock

from hooks import click_sharding_setup as setup


class ShardingEconomicsTests(unittest.TestCase):
    def policy(self, parent, children, *, startup=100.0, reserve=25.0):
        proposal = {
            "analysis": {"command": {"argv": ["python3", "-m", "unittest"]}},
            "children": [{"argv": ["python3", "-m", "unittest", str(i)]}
                         for i in range(len(children))],
        }
        def sample(ms):
            return {"status": "passed", "exit_code": 0, "duration_ms": ms, "reason": ""}
        with (
            mock.patch.object(setup, "_cost_thresholds", return_value={
                "min_parent_ms": 250.0, "min_avoidable_ms": 100.0,
                "management_reserve_ms": reserve,
            }),
            mock.patch.object(setup.inventory, "workspace_snapshot", return_value={}),
            mock.patch.object(setup, "_run_check", side_effect=[
                sample(ms) for ms in [parent, *children]
            ]),
            mock.patch.object(setup, "_run_startup_probe", return_value=sample(startup)),
        ):
            return setup._measure_cost_policy(Path("."), proposal)

    def test_expensive_children_cannot_manufacture_savings(self):
        result = self.policy(500.0, [700.0, 700.0, 700.0])
        self.assertEqual(result["strategy"], "whole-suite")
        self.assertEqual(result["reason"], "selected-children-not-cheaper-than-parent")
        self.assertEqual(result["estimated_net_per_repeat_ms"], -225.0)
        self.assertIsNone(result["estimated_probe_break_even_repeats"])

    def test_profitable_repeat_reports_setup_payback_separately(self):
        result = self.policy(480_000.0, [90_000.0] * 5)
        self.assertEqual(result["strategy"], "sharded")
        self.assertEqual(result["estimated_selected_execution_ms"], 90_000.0)
        self.assertEqual(result["estimated_net_per_repeat_ms"], 389_975.0)
        self.assertEqual(result["estimated_probe_break_even_repeats"], 3)
        self.assertEqual(result["measurement_scope"], "setup-probe-not-savings")

    def test_management_cost_can_consume_the_entire_gain(self):
        result = self.policy(500.0, [480.0, 480.0])
        self.assertEqual(result["strategy"], "whole-suite")

    def test_startup_is_not_charged_twice_to_selected_execution(self):
        result = self.policy(1000.0, [600.0, 600.0], startup=500.0)
        self.assertEqual(result["strategy"], "sharded")
        self.assertEqual(result["estimated_net_per_repeat_ms"], 375.0)


class ExecutionBudgetTests(unittest.TestCase):
    def test_execution_has_its_own_long_budget(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(setup._execution_budget()["timeout_seconds"], 1800.0)
            self.assertEqual(setup.inventory.Limits().timeout, 30.0)

    def test_budget_is_bounded_and_rejects_non_finite_values(self):
        for value in ("nan", "inf", "-1", "0", "7201", "invalid"):
            with self.subTest(value=value), mock.patch.dict(
                os.environ, {"CLICK_SHARDING_EXECUTION_TIMEOUT_SECONDS": value}
            ):
                with self.assertRaisesRegex(setup.SetupError, "invalid-execution-budget"):
                    setup._execution_budget()
        with mock.patch.dict(os.environ, {"CLICK_SHARDING_EXECUTION_TIMEOUT_SECONDS": "600"}):
            self.assertEqual(setup._execution_budget()["timeout_seconds"], 600)


if __name__ == "__main__":
    unittest.main()
