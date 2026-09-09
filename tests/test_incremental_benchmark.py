from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import shutil
import sys
import unittest
import tempfile
from unittest import mock

from benchmarks.incremental_verification import Fixture, _fixture_runner_argv, comparison_delta, distribution, main
from benchmarks import incremental_verification as benchmark
from hooks import click_incremental


ROOT = Path(__file__).parents[1]
BENCHMARK = ROOT / "benchmarks" / "incremental_verification.py"


class ScopedSessionBenchmarkTests(unittest.TestCase):
    def test_real_scoped_sessions_preserve_failures_and_count_all_costs(self):
        result = benchmark.run_scoped_session_benchmark(iterations=1, workload_rounds=20)
        self.assertEqual(result["summaries"]["eligible_samples"], 1)
        sample = result["samples"][0]
        for arm in sample["arms"].values():
            self.assertEqual(arm["total_ms"], arm["setup_ms"]
                             + sum(step["wall_ms"] for step in arm["steps"])
                             + arm["final_full_audit"]["wall_ms"])
            self.assertEqual(arm["final_full_audit"]["status"], "passed")
        steps = {step["scenario"]: step for step in sample["arms"]["scoped-inputs"]["steps"]}
        self.assertEqual((steps["unrelated-code"]["executed"], steps["unrelated-code"]["reused"]), (1, 1))
        self.assertEqual((steps["related-code"]["executed"], steps["related-code"]["reused"]), (1, 1))
        self.assertEqual(steps["all-code"]["executed"], 2)
        self.assertEqual(steps["environment"]["executed"], 2)
        self.assertEqual(steps["failure"]["status"], "failed")
        self.assertEqual(steps["retry"]["status"], "passed")
        self.assertEqual(steps["unchanged"]["reused"], 2)
        self.assertNotIn("runner_token", json.dumps(result))


class IncrementalVerificationBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_result = benchmark.run_guarded_workflow_benchmark(
            iterations=1, warmups=0, workload_rounds=20,
        )

    @unittest.skipUnless(shutil.which("node"), "Node unavailable for dashboard importer")
    def test_actual_workflow_output_imports_with_costs_and_all_paired_samples(self):
        from tests.test_click_efficiency import UI_ASSERTIONS
        from hooks import click_shadow_dashboard
        script = UI_ASSERTIONS.split("const b={wall_ms", 1)[0] + r'''
const result=api.readComparison(input.workflow);
assert.equal(result.samples.length,input.workflow.comparison_samples.length);
assert.equal(result.cost_samples.length,3);
assert(result.samples.some(item=>item.excluded_reason==='verification-not-passed'));
assert(result.samples.some(item=>item.excluded_reason==='scope-not-equivalent'));
assert(result.cost_samples.every(item=>Number.isFinite(item.setup_ms)&&Number.isFinite(item.additional_full_audit_ms)));
console.log('Actual driver v4 imports without fixture authority or lost samples.');
'''
        checked = subprocess.run(
            [shutil.which("node"), "-e", script],
            input=json.dumps({"script": click_shadow_dashboard.JS, "workflow": self.workflow_result}),
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        artifact = Path(tempfile.mkdtemp(prefix="click-impact-benchmark-")) / "workflow.json"
        artifact.write_text(
            json.dumps(self.workflow_result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Actual benchmark/importer evidence: {artifact}")

    def test_guarded_workflow_compares_three_configs_and_audits_real_successor_reuse(self):
        result = json.loads(json.dumps(self.workflow_result))
        self.assertEqual(result["kind"], "click-guarded-workflow-benchmark")
        self.assertEqual(result["version"], 4)
        self.assertTrue(benchmark.workflow_report_is_valid(result))
        sample = result["samples"][0]
        self.assertEqual(set(sample["arms"]), {"baseline", "click-default", "explicit-reuse"})
        digests = [arm["final_input_digest"] for arm in sample["arms"].values()]
        self.assertEqual(len(set(digests)), 1)
        for arm in sample["arms"].values():
            self.assertTrue(all(step["audit_matches"] for step in arm["steps"]))
            steps = {step["scenario"]: step for step in arm["steps"]}
            self.assertEqual(steps["failure"]["validation"]["status"], "failed")
            self.assertEqual(steps["retry"]["validation"]["status"], "passed")
            self.assertEqual(steps["unchanged"]["audit"]["status"], "passed")
            self.assertTrue(all(set(step["full_checks"]) == {"same-shards", "parent-suite"}
                                for step in arm["steps"]))
        explicit = sample["arms"]["explicit-reuse"]
        steps = {step["scenario"]: step for step in explicit["steps"]}
        partial = steps["unrelated-code"]["validation"]
        self.assertEqual((partial["executed_source_count"], partial["reused_source_count"]), (1, 1))
        reused = next(item for item in partial["batch"]["sources"] if item["status"] == "reused")
        self.assertEqual(reused["reuse_origin"]["kind"], "successor-contract")
        self.assertNotEqual(steps["first-run"]["contract_id"], steps["unrelated-code"]["contract_id"])
        self.assertEqual(steps["related-code"]["validation"]["executed_source_count"], 1)
        self.assertEqual(steps["all-code"]["validation"]["executed_source_count"], 2)
        self.assertEqual(steps["environment"]["validation"]["executed_source_count"], 2)
        failed = next(item for item in steps["failure"]["validation"]["batch"]["sources"] if item["status"] == "failed")
        self.assertIsNone(failed["reuse_origin"])
        self.assertEqual(explicit["successor_receipt"]["receipt"]["version"], 5)
        self.assertTrue(all(item["preapproval_denied"] and item["wrong_id_denied"] and item["separate_turns"] for item in explicit["controls"]))
        default_steps = sample["arms"]["click-default"]["steps"]
        self.assertEqual(default_steps[1]["validation"]["reused_source_count"], 0)
        comparisons = result["comparison_samples"]
        self.assertEqual(len(comparisons), 2 * len(benchmark.WORKFLOW_STEPS) * 2)
        partial_pairs = [item for item in comparisons if item["configuration"] == "explicit-reuse"
                         and item["scenario"] == "unrelated-code"]
        self.assertEqual({item["comparison"] for item in partial_pairs}, {"same-shards", "parent-suite"})
        self.assertTrue(all(item["eligible"] for item in partial_pairs))
        self.assertEqual(
            next(item for item in partial_pairs if item["comparison"] == "same-shards")["click"]["measurement_scope"],
            "executed-source-command-duration-sum",
        )
        self.assertEqual(
            next(item for item in partial_pairs if item["comparison"] == "parent-suite")["click"]["measurement_scope"],
            "driver-preflight-through-runner-return",
        )
        failures = [item for item in comparisons if item["scenario"] == "failure"]
        self.assertTrue(failures)
        self.assertTrue(all(not item["eligible"] for item in failures))
        self.assertTrue(all(item["excluded_reason"] == "verification-not-passed"
                            for item in failures if item["scope_equivalent"]))
        default_same = [item for item in comparisons if item["configuration"] == "click-default"
                        and item["comparison"] == "same-shards"]
        self.assertTrue(all(not item["scope_equivalent"] and item["excluded_reason"] == "scope-not-equivalent"
                            for item in default_same if not item["warmup"]))
        self.assertGreater(steps["unrelated-code"]["validation"]["click_non_test_interval_ms"], 0)
        self.assertNotIn("runner_token", json.dumps(result))
        report = benchmark.workflow_report_html(result)
        self.assertIn("<html lang=\"ko\">", report)
        self.assertIn("음수", report)
        self.assertNotIn("<script", report)
        self.assertNotIn("PLUGIN_DATA", report)
        self.assertNotIn("raw_argv", report)

    def test_v4_report_validation_rejects_units_scopes_sources_and_tampered_math(self):
        result = json.loads(json.dumps(self.workflow_result))
        for path, bad in (
            (("unit",), "seconds"),
            (("source",), "claimed-production"),
            (("comparison_samples", 0, "unit"), "s"),
            (("comparison_samples", 0, "baseline", "measurement_scope"), "unknown"),
            (("comparison_samples", 0, "delta_ms"), 999999),
        ):
            changed = json.loads(json.dumps(result))
            target = changed
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = bad
            self.assertFalse(benchmark.workflow_report_is_valid(changed), path)

    def test_measurement_order_crosses_positions_and_repository_reference_uses_real_inventory(self):
        orders = {tuple(benchmark._rotated(benchmark.WORKFLOW_MEASUREMENTS, index)) for index in range(3)}
        self.assertEqual(len(orders), 3)
        passed = {"duration_ms": 10.0, "status": "passed", "exit_code": 0,
                  "executed_command_count": 1, "not_run_command_count": 0}
        with mock.patch.object(benchmark, "_repository_group", side_effect=[passed, passed, passed, passed]):
            reference = benchmark.run_repository_bundle_reference(iterations=2, warmups=0)
        self.assertEqual(reference["source"], "current-repository-test-bundle")
        self.assertEqual(reference["unit"], "ms")
        self.assertTrue(benchmark.repository_reference_is_valid(reference))
        self.assertEqual([item["order"] for item in reference["samples"]],
                         [["same-shards", "parent-suite"], ["parent-suite", "same-shards"]])
        tampered = json.loads(json.dumps(reference))
        tampered["samples"][0]["delta_ms"] = 999
        self.assertFalse(benchmark.repository_reference_is_valid(tampered))
        result = json.loads(json.dumps(self.workflow_result))
        result["repository_reference"] = reference
        self.assertTrue(benchmark.workflow_report_is_valid(result))
        self.assertIn("실제 저장소 테스트 번들 참조", benchmark.workflow_report_html(result))

    def test_repository_group_timeout_is_bounded_and_not_eligible(self):
        with mock.patch.object(
            benchmark.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["check"], timeout=1),
        ):
            result = benchmark._repository_group(
                [["check"], ["later"]], timeout_seconds=1
            )
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["exit_code"], 124)
        self.assertEqual(result["executed_command_count"], 1)
        self.assertEqual(result["not_run_command_count"], 1)

    def test_windows_fixture_runner_keeps_the_preflight_interpreter_and_capability(self) -> None:
        argv = ["py", "-3", str(BENCHMARK), "--encoded-runner", "transport-fixture"]
        with mock.patch("benchmarks.incremental_verification._split", return_value=argv):
            self.assertEqual(_fixture_runner_argv("rendered runner"), [sys.executable, *argv[2:]])
        self.assertEqual(argv[:2], ["py", "-3"])
        direct = [sys.executable, str(BENCHMARK), "run-verification", "transport-fixture"]
        with mock.patch("benchmarks.incremental_verification._split", return_value=direct):
            self.assertEqual(_fixture_runner_argv("direct runner"), direct)

    def test_stdout_json_round_trips_with_a_legacy_windows_encoding(self) -> None:
        payload = {"label": "검증 묶음"}
        raw = io.BytesIO()
        with (
            io.TextIOWrapper(raw, encoding="cp1252") as output,
            mock.patch("benchmarks.incremental_verification.run_benchmark", return_value=payload),
            mock.patch("benchmarks.incremental_verification.sys.stdout", output),
        ):
            self.assertEqual(main(["--iterations", "1", "--warmups", "0"]), 0)
            output.flush()
            self.assertEqual(json.loads(raw.getvalue().decode("ascii")), payload)

    def test_fixture_emits_measured_and_estimated_values_separately(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(BENCHMARK),
                "--iterations",
                "1",
                "--warmups", "0",
                "--mode", "guarded",
                "--scenario", "unchanged",
                "--scenario", "docs",
                "--workload-rounds",
                "2000",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["version"], 2)
        self.assertEqual(payload["conditions"]["authority"], "real-hooks-and-one-use-runner")
        self.assertEqual({sample["comparison"] for sample in payload["samples"]}, {"same-shards", "parent-suite"})
        for sample in payload["samples"]:
            measured = sample["incremental"]
            diagnostic = json.dumps({
                "scenario": sample["scenario"], "comparison": sample["comparison"],
                "sources": [{key: item.get(key) for key in
                             ("decision", "reason_code", "status", "execution_reason_code")}
                            for item in measured["batch"]["sources"]],
            }, ensure_ascii=True, sort_keys=True)
            self.assertEqual(measured["executed_source_count"], 0, diagnostic)
            self.assertEqual(measured["reused_source_count"], 2)
            self.assertGreater(measured["wall_ms"], 0)
            self.assertGreater(measured["estimated_avoided_ms"], 0)
            self.assertTrue(click_incremental.batch_is_valid(measured["batch"]))
            expected = "reuse-exact" if sample["scenario"] == "unchanged" else "reuse-safe-change"
            self.assertEqual({item["decision"] for item in measured["batch"]["sources"]}, {expected})
            self.assertAlmostEqual(sample["delta_ms"], sample["baseline"]["wall_ms"] - measured["wall_ms"])
        self.assertEqual(payload["observer_overhead_ms"], 0)
        self.assertEqual(payload["shadow_contradiction_count"], 0)
        self.assertNotIn("actual_saved", result.stdout)
        self.assertNotIn("runner_token", result.stdout)
        self.assertNotIn("raw_argv", result.stdout)

    def test_first_run_failure_and_evidence_session_boundary_are_honest(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), 20)
            first = fixture.verify()
            self.assertEqual(first["executed_source_count"], 2)
            self.assertEqual(first["reused_source_count"], 0)
            fixture.change("first-failure")
            failed = fixture.verify()
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["executed_source_count"], 1)
            self.assertEqual(failed["not_run_source_count"], 1)
            self.assertEqual(failed["reused_source_count"], 0)
            self.assertEqual(failed["estimated_avoided_ms"], 0)

    def test_partial_reuse_uses_real_successor_evidence_and_runs_the_other_shard(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(
                Path(directory), 20, mode="evidence", partial_policy=True
            )
            baseline = fixture.verify()
            self.assertEqual(baseline["status"], "passed")
            origin_session = fixture.state()["evidence_session_id"]

            fixture.change("partial-reuse")
            current_session = fixture.state()["evidence_session_id"]
            self.assertNotEqual(current_session, origin_session)
            result = fixture.verify()

            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["executed_source_count"], 1)
            self.assertEqual(result["reused_source_count"], 1)
            self.assertEqual(result["not_run_source_count"], 0)
            decisions = {
                item["label"]: (
                    item["decision"], item["status"], item["reason_code"]
                )
                for item in result["batch"]["sources"]
            }
            self.assertEqual(
                decisions["alpha"],
                (
                    "reuse-safe-change",
                    "reused",
                    "successor-evidence-safe-change-covered",
                ),
            )
            self.assertEqual(decisions["beta"][0:2], ("run", "passed"))
            alpha = next(
                item for item in result["batch"]["sources"]
                if item["label"] == "alpha"
            )
            self.assertEqual(
                alpha["reuse_origin"]["evidence_session_id"], origin_session
            )
            beta = next(
                item for item in result["batch"]["sources"]
                if item["label"] == "beta"
            )
            self.assertIsNone(beta["reuse_origin"])

    def test_negative_delta_zero_baseline_and_variation(self):
        self.assertEqual(comparison_delta(10, 15), {"delta_ms": -5, "delta_percent": -50})
        self.assertEqual(comparison_delta(0, 2), {"delta_ms": -2, "delta_percent": None})
        self.assertEqual(distribution([-10, 0, 5]), {"median": 0, "min": -10, "max": 5})
        self.assertIsNone(distribution([])["median"])


if __name__ == "__main__":
    unittest.main()
