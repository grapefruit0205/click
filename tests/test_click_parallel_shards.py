"""Shard children of one committed plan execute concurrently, recorded in order."""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path

from unittest import mock

from click_gate_test_support import ClickGateTestCase

def _load_runner():
    import importlib
    return importlib.import_module("hooks.click_verification_runner")


class ParallelBlockPlanningTests(unittest.TestCase):
    def test_only_contiguous_single_command_children_of_one_parent_form_a_block(self) -> None:
        runner = _load_runner()
        checks = [
            {"evidence_id": "PLAIN"},
            {"evidence_id": "S" + "1" * 31}, {"evidence_id": "S" + "2" * 31}, {"evidence_id": "S" + "3" * 31},
            {"evidence_id": "OTHER"},
            {"evidence_id": "S" + "4" * 31}, {"evidence_id": "S" + "5" * 31},
            {"evidence_id": "S" + "6" * 31},
        ]
        key = runner._evidence_key
        groups = {key("S" + "1" * 31): "p1", key("S" + "2" * 31): "p1", key("S" + "3" * 31): "p1",
                  key("S" + "4" * 31): "p2", key("S" + "5" * 31): "p2", key("S" + "6" * 31): "p3"}
        grouped = {key(str(check["evidence_id"])): [check] for check in checks}
        self.assertEqual(runner._parallel_blocks(checks, groups, grouped), {2: 4, 6: 7})
        # A source with two commands keeps its order and breaks the block.
        grouped[key("S" + "2" * 31)] = [checks[2], dict(checks[2])]
        self.assertEqual(runner._parallel_blocks(checks, groups, grouped), {6: 7})
        self.assertEqual(runner._parallel_blocks(checks, {}, grouped), {})

    def test_worker_count_comes_from_the_environment_or_the_cores(self) -> None:
        runner = _load_runner()
        with mock.patch.dict(os.environ, {"CLICK_VERIFICATION_WORKERS": "3"}):
            self.assertEqual(runner._verification_workers(), 3)
        with mock.patch.dict(os.environ, {"CLICK_VERIFICATION_WORKERS": "0"}):
            self.assertGreaterEqual(runner._verification_workers(), 1)
        with mock.patch.dict(os.environ, {"CLICK_VERIFICATION_WORKERS": "nope"}):
            self.assertLessEqual(runner._verification_workers(), 8)


class ParallelShardExecutionTests(ClickGateTestCase):
    SLEEP_SECONDS = 3.0

    def shard_fixture(self, names: tuple[str, ...], *, failing: tuple[str, ...] = ()) -> list[str]:
        (self.workspace / ".gitignore").write_text("__pycache__/\n*.pyc\nmarkers/\n", encoding="utf-8")
        (self.workspace / "tests").mkdir()
        (self.workspace / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.workspace / "markers").mkdir()
        for name in names:
            verdict = "self.fail('shard broken')" if name in failing else "self.assertTrue(True)"
            (self.workspace / "tests" / f"test_{name}.py").write_text(
                "import pathlib, time, unittest\n"
                f"MARKERS = pathlib.Path(__file__).resolve().parents[1] / 'markers'\n"
                "class Check(unittest.TestCase):\n"
                "    def test_it(self):\n"
                f"        (MARKERS / '{name}.start').write_text(repr(time.time()))\n"
                f"        time.sleep({self.SLEEP_SECONDS!r})\n"
                f"        (MARKERS / '{name}.end').write_text(repr(time.time()))\n"
                f"        {verdict}\n",
                encoding="utf-8",
            )
        parent = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
        (self.workspace / ".click").mkdir()
        (self.workspace / ".click" / "evidence-shards.json").write_text(json.dumps({
            "version": 1,
            "entries": [{
                "checks": [parent], "inventory": ["tests/test*.py"],
                "shards": [{"id": name, "checks": [[sys.executable, "-m", "unittest", f"tests.test_{name}"]],
                            "covers": [f"tests/test_{name}.py"]} for name in names],
            }],
        }), encoding="utf-8")
        self.initialize_git(".gitignore", ".click/evidence-shards.json", "tests/__init__.py",
                            *(f"tests/test_{name}.py" for name in names))
        return parent

    def stamps(self, names: tuple[str, ...], suffix: str) -> dict[str, float]:
        return {name: float((self.workspace / "markers" / f"{name}.{suffix}").read_text()) for name in names}

    def run_parallel(self, commands, evidence_ids, workers: int, turn: str = "turn-1"):
        # Actionable reporting: raw output is teed live and therefore keeps
        # submission order; the concurrent path is for the bounded format.
        payload = self.verify_gate(commands, turn, evidence_ids=evidence_ids, raw_output=False)
        return self.run_rewritten(payload, {"CLICK_VERIFICATION_WORKERS": str(workers)})

    @unittest.skipUnless(sys.platform == "linux", "concurrent shard execution is enabled on Linux hosts")
    def test_children_of_one_plan_overlap_and_are_reported_in_order(self) -> None:
        names = ("alpha", "beta", "gamma")
        parent = self.shard_fixture(names)
        started = time.perf_counter()
        result = self.run_parallel([parent], ["E1"], workers=3)
        elapsed = time.perf_counter() - started
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        starts, ends = self.stamps(names, "start"), self.stamps(names, "end")
        # Every child was running at the same moment: the last one to start
        # began before the first one finished.
        self.assertLess(max(starts.values()), min(ends.values()), (starts, ends, output))
        self.assertLess(elapsed, 3 * self.SLEEP_SECONDS, output)
        headers = [output.index(f"[Click verification {index}/3:E1[{name}]:") for index, name in enumerate(names, start=1)]
        self.assertEqual(headers, sorted(headers), output)
        self.assertEqual(output.count("passed."), 3, output)
        summary = next(line for line in output.splitlines() if "[Click 결과]" in line)
        self.assertIn("병렬 실행 벽시계", summary)
        # The recorded receipts are as usable as sequential ones.
        again = self.run_parallel([parent], ["E1"], workers=3, turn="turn-2")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertNotIn("[Click verification", again.stdout, again.stdout)

    def test_a_single_worker_keeps_the_children_sequential(self) -> None:
        names = ("alpha", "beta")
        parent = self.shard_fixture(names)
        result = self.run_parallel([parent], ["E1"], workers=1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        starts, ends = self.stamps(names, "start"), self.stamps(names, "end")
        ordered = sorted(names, key=starts.get)
        self.assertGreaterEqual(starts[ordered[1]], ends[ordered[0]], (starts, ends))
        summary = next(line for line in (result.stdout + result.stderr).splitlines() if "[Click 결과]" in line)
        self.assertNotIn("병렬", summary)

    @unittest.skipUnless(sys.platform == "linux", "concurrent shard execution is enabled on Linux hosts")
    def test_a_failing_child_lets_running_siblings_finish_and_stops_later_checks(self) -> None:
        names = ("alpha", "beta", "gamma")
        parent = self.shard_fixture(names, failing=("beta",))
        (self.workspace / "plain_test.py").write_text(
            "import pathlib, unittest\n"
            "class Plain(unittest.TestCase):\n"
            "    def test_it(self):\n"
            "        (pathlib.Path(__file__).resolve().parent / 'markers' / 'plain.ran').write_text('1')\n",
            encoding="utf-8",
        )
        self.initialize_git("plain_test.py")
        plain = [sys.executable, "-m", "unittest", "plain_test"]
        result = self.run_parallel([parent, plain], ["E1", "PLAIN"], workers=3)
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        # Siblings already dispatched with the failing child are recorded.
        for name in names:
            self.assertTrue((self.workspace / "markers" / f"{name}.end").exists(), (name, output))
            self.assertIn(f":E1[{name}]:", output)
        # Fail-fast still holds at the group boundary: the later check never ran.
        self.assertNotIn(":PLAIN:", output)
        self.assertFalse((self.workspace / "markers" / "plain.ran").exists(), output)


if __name__ == "__main__":
    unittest.main()
