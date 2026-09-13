from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from benchmarks import task_efficiency
from benchmarks import token_ab


ROOT = Path(__file__).parents[1]


def _transcript(path: Path, responses, commands, *, duplicate: bool = False) -> None:
    """Write a stream-json transcript in the shape the CLI emits."""
    rows: list[dict] = [{"type": "system", "subtype": "init", "model": "test-model"}]
    for index, (fresh, cache_write, cache_read, output, thinking) in enumerate(responses):
        message = {
            "type": "assistant",
            "message": {
                "id": f"msg_{index}",
                "content": (
                    [{"type": "tool_use", "name": "Bash", "input": {"command": commands[index]}}]
                    if index < len(commands)
                    else [{"type": "text", "text": "done"}]
                ),
                "usage": {
                    "input_tokens": fresh,
                    "cache_creation_input_tokens": cache_write,
                    "cache_read_input_tokens": cache_read,
                    "output_tokens": output,
                    "output_tokens_details": {"thinking_tokens": thinking},
                },
            },
        }
        rows.append(message)
        if duplicate:
            rows.append(json.loads(json.dumps(message)))
        if index < len(commands):
            rows.append({"type": "user", "message": {"content": [
                {"type": "tool_result", "content": "[Click result] 1 of 2 executed · 1 reused"}]}})
    rows.append({"type": "result", "subtype": "success", "is_error": False, "num_turns": len(responses),
                 "duration_ms": 1000, "total_cost_usd": 0.5, "session_id": "session-1",
                 "usage": {"input_tokens": 7, "cache_creation_input_tokens": 11,
                           "cache_read_input_tokens": 13, "output_tokens": 17,
                           "output_tokens_details": {"thinking_tokens": 3}},
                 "modelUsage": {}})
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


class TokenAbFixtureTests(unittest.TestCase):
    def test_acceptance_digest_is_equal_for_equal_end_states_only(self) -> None:
        steps = token_ab.STEPS["first-use"]
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "a", Path(directory) / "b"
            for root in (first, second):
                token_ab.build_fixture(root, rounds=200)
            unchanged = token_ab.audit(first, steps)
            # The suite passes before the task, but the requested steps are not done.
            self.assertTrue(unchanged["suite_passed"])
            self.assertFalse(unchanged["passed"])
            for root in (first, second):
                token_ab.apply_steps(root, steps)
            completed = token_ab.audit(first, steps)
            self.assertTrue(completed["passed"])
            self.assertEqual(completed["completion_digest"], token_ab.audit(second, steps)["completion_digest"])
            # A workspace that stops one step short is a different completion.
            third = Path(directory) / "c"
            token_ab.build_fixture(third, rounds=200)
            token_ab.apply_steps(third, steps[:-1])
            partial = token_ab.audit(third, steps)
            self.assertFalse(partial["passed"])
            self.assertNotEqual(partial["completion_digest"], completed["completion_digest"])

    def test_shard_map_covers_every_test_module_of_the_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repository"
            token_ab.build_fixture(root, rounds=200)
            entry = json.loads((root / ".click" / "evidence-shards.json").read_text(encoding="utf-8"))["entries"][0]
            self.assertEqual(entry["checks"], [token_ab.SUITE])
            self.assertEqual([shard["covers"] for shard in entry["shards"]],
                             [[f"tests/test_{name}.py"] for name in token_ab.MODULES])
            self.assertTrue((root / ".git").is_dir())


class TokenAbTranscriptTests(unittest.TestCase):
    def test_the_task_prompt_forbids_batching_and_requires_each_step_verified(self) -> None:
        prompt = token_ab.task_prompt(token_ab.STEPS["first-use"])
        self.assertIn("Never combine two steps", prompt)
        self.assertIn("run the project's full test suite", prompt)
        self.assertNotIn("click-gate", prompt)
        directed = token_ab.task_prompt(token_ab.STEPS["first-use"], directed=True)
        # The directive is one identical sentence for both arms; only the
        # baseline session lacks the command it names.
        self.assertIn("click-gate verify", directed)
        self.assertEqual(directed.replace(token_ab.CHECK_DIRECTIVE + "\n", ""), prompt)

    def test_usage_and_tool_calls_are_counted_once_per_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            responses = [(100, 2000, 0, 300, 50), (50, 0, 9000, 400, 60)]
            commands = ["python3 -m unittest discover -s tests -v", "click-gate verify '{\"version\":2}'"]
            _transcript(path, responses, commands, duplicate=True)
            parsed = token_ab.parse_transcript(path)
            self.assertEqual(parsed["responses"], 2)
            self.assertEqual(parsed["tool_calls"], 2)
            self.assertEqual(parsed["raw_test_runs"], 1)
            self.assertEqual(parsed["click_verify_calls"], 1)
            self.assertEqual(len(parsed["click_result_lines"]), 2)
            # The evaluation uses the host's own task total, not the streamed
            # per-block snapshots, whose output counter is taken mid-response.
            self.assertEqual(len(parsed["events"]), 1)
            total = parsed["events"][0]
            self.assertEqual(parsed["totals"]["input_total"], 7 + 11 + 13)
            self.assertEqual(total["input_tokens"], parsed["totals"]["input_total"])
            self.assertEqual(total["output_tokens"], 17)
            self.assertEqual(total["cached_input_tokens"], 13)
            self.assertLessEqual(total["reasoning_output_tokens"], total["output_tokens"])
            # The streamed input counters are kept for analysis and do sum.
            self.assertEqual(parsed["streamed_input_tokens"], 100 + 2000 + 50 + 9000)

    def test_pair_evaluation_reports_the_measured_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steps = token_ab.STEPS["first-use"]
            workspace = root / "repository"
            token_ab.build_fixture(workspace, rounds=200)
            token_ab.apply_steps(workspace, steps)
            accepted = token_ab.audit(workspace, steps)
            runs = {}
            for arm, responses in (
                ("baseline", [(100, 2000, 0, 300, 50), (50, 0, 9000, 400, 60)]),
                ("improved", [(100, 2000, 0, 300, 50), (50, 0, 4000, 400, 60)]),
            ):
                path = root / f"{arm}.jsonl"
                _transcript(path, responses, ["python3 -m unittest discover -s tests"])
                runs[arm] = token_ab.run_record(
                    arm=arm, model="test-model", accepted=accepted,
                    session={"exit_code": 0, "timed_out": False, "started_at_ms": 1000,
                             "finished_at_ms": 61000, "transcript": str(path)},
                    parsed=token_ab.parse_transcript(path))
                self.assertEqual(runs[arm]["task"]["completion_status"], "completed")
                self.assertEqual(runs[arm]["task"]["acceptance_status"], "passed")
            pair = {"id": "unit", "baseline_variant": "N", "improved_variant": "B2",
                    "scenario": token_ab.SCENARIO, "run_kind": "first-use", "runtime_mode": "evidence",
                    "baseline": runs["baseline"], "improved": runs["improved"]}
            result = task_efficiency.evaluate_pair(pair)
            self.assertEqual(result["status"], "comparable")
            # Both synthetic sessions report the same host total, so the ratio
            # is zero: the streamed snapshots never reach the evaluation.
            self.assertEqual(result["token_savings_ratio"], 0.0)
            public = task_efficiency.public_projection(token_ab.evaluation([pair], {"scenario": token_ab.SCENARIO}))
            self.assertEqual(public["measurement_status"], "measured")
            self.assertNotIn("baseline_total_tokens", json.dumps(public))

    def test_an_unfinished_session_is_kept_as_an_incomparable_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steps = token_ab.STEPS["first-use"]
            workspace = root / "repository"
            token_ab.build_fixture(workspace, rounds=200)
            accepted = token_ab.audit(workspace, steps)  # steps were never applied
            path = root / "timeout.jsonl"
            _transcript(path, [(100, 0, 0, 50, 0)], [])
            record = token_ab.run_record(
                arm="improved", model="test-model", accepted=accepted,
                session={"exit_code": 143, "timed_out": True, "started_at_ms": 1000,
                         "finished_at_ms": 2000, "transcript": str(path)},
                parsed=token_ab.parse_transcript(path))
            self.assertEqual(record["task"]["completion_status"], "cancelled")
            self.assertEqual(record["task"]["acceptance_status"], "failed")
            result = task_efficiency.evaluate_pair(
                {"id": "unit", "baseline_variant": "N", "improved_variant": "B2",
                 "scenario": token_ab.SCENARIO, "run_kind": "first-use", "runtime_mode": "evidence",
                 "baseline": record, "improved": record})
            self.assertEqual(result["status"], "incomplete")
            self.assertIsNone(result["token_savings_ratio"])

    def test_sessions_disable_every_globally_enabled_plugin(self) -> None:
        settings = token_ab.user_plugin_settings()
        self.assertTrue(all(value is False for value in settings["enabledPlugins"].values()))
        argv = token_ab.session_command("claude", "task", model="m", budget=1.5,
                                        plugin_dir=Path("/plugin"), settings=settings)
        self.assertEqual(argv[:3], ["claude", "-p", "task"])
        for flag in ("--no-session-persistence", "--dangerously-skip-permissions", "--max-budget-usd"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--plugin-dir") + 1], str(Path("/plugin")))
        self.assertNotIn("--plugin-dir", token_ab.session_command(
            "claude", "task", model="m", budget=1.5, plugin_dir=None, settings=settings))
        environment = token_ab.session_environment(Path("/data"))
        self.assertEqual(environment["PLUGIN_DATA"], str(Path("/data") / "plugin-data"))
        self.assertNotIn("CLAUDECODE", environment)

    def test_module_runs_as_a_script_without_arguments_failing_closed(self) -> None:
        result = subprocess.run([sys.executable, str(ROOT / "benchmarks" / "token_ab.py")],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--output", result.stderr)


if __name__ == "__main__":
    unittest.main()
