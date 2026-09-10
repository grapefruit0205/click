"""Actual framework processes: one execution, diagnostics, and bound inputs."""
from __future__ import annotations

import importlib.util
import json
import secrets
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from hooks import click_authoritative_observer as observer
from hooks import click_observer_runtime as runtime
from hooks import click_observation_inputs as inputs
from tests.click_gate_test_support import ClickGateTestCase, CLICK_EVIDENCE


class ProfileAndCaptureTests(unittest.TestCase):
    def test_profiles_bound_patch_family_platform_and_implementation(self):
        profile = runtime.profiles.CPYTHON312["linux"]
        for version in ((3, 12, 3), (3, 12, 14)):
            self.assertTrue(runtime.profiles.supported(profile, version=version, platform="linux", implementation="cpython"))
        for version in ((3, 11, 9), (3, 12, 2), (3, 12, 15), (3, 13, 0)):
            self.assertFalse(runtime.profiles.supported(profile, version=version, platform="linux", implementation="cpython"))
        self.assertFalse(runtime.profiles.supported(profile, version=(3, 12, 3), platform="win32", implementation="cpython"))
        self.assertFalse(runtime.profiles.supported(profile, version=(3, 12, 3), platform="linux", implementation="pypy"))
        self.assertFalse(runtime.profiles.supported(runtime.PROFILE, version=(3, 12, 14), platform="linux", implementation="cpython"))

    def test_output_reader_preparation_failure_never_admits_target(self):
        output = observer.click_process.OutputCapture()
        spawn = mock.Mock()
        with mock.patch.object(observer.click_process.threading.Thread, "start", side_effect=RuntimeError("reader unavailable")):
            with self.assertRaises(RuntimeError):
                output.spawn(spawn, ["not-started"])
        spawn.assert_not_called()
        self.assertFalse(output.started)


@unittest.skipUnless(sys.platform == "linux" and runtime.profiles.current(), "native Linux Python profile")
class FrameworkObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = runtime.control_state(runtime.prepare(Path(__file__).resolve().parents[1]))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="click-framework-inputs-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.runner_token = secrets.token_hex(32)
        self.context = {key: "a" * 64 for key in observer.FULL_BINDING_FIELDS}
        self.context["mutation_revision"] = 1

    def execute(self, module, *arguments):
        argv = [sys.executable, "-m", module, *arguments]
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0")
        self.capture = {}
        def fallback():
            raise AssertionError("supported fixture unexpectedly ran without observation")
        result = observer.run_command(
            argv, workspace=self.root, observation_root=self.root, environment=environment,
            binding_context=self.context, runtime=self.runtime, runner_token=self.runner_token,
            execute_unobserved=fallback, capture_output=self.capture,
            capture_limit_bytes=4096, resolve_backend=lambda name, **kwargs: (str(runtime.inventory.trusted_executable(name, self.root)), ""),
            digest_file=runtime._digest_file,
        )
        self.assertEqual(result.exit_code, self.capture["process"].returncode)
        self.observation = observer.verified_observation(result.envelope, secret=self.runner_token, expected_binding=self.context)
        self.assertIsNotNone(self.observation)
        return result

    def test_unittest_output_and_input_share_one_execution(self):
        (self.root / "test_case.py").write_text("import unittest\nclass Case(unittest.TestCase):\n def test_one(self):\n  print('ONLY-ONCE')\n  self.assertTrue(True)\n")
        result = self.execute("unittest", "test_case", "-q")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.capture["process"].stdout.data.count(b"ONLY-ONCE"), 1)
        self.assertEqual(self.observation["status"], "complete", self.observation["ineligibility_reasons"])
        self.assertTrue(inputs.records_current(self.root, self.runtime["artifact_id"], self.observation["inputs"], profile=self.runtime["profile"]))

    def test_failure_and_large_output_are_retained_without_repeating(self):
        (self.root / "test_case.py").write_text("import unittest\nclass Case(unittest.TestCase):\n def test_one(self):\n  print('ONLY-ONCE')\n  print('x'*100000)\n  self.fail('original failure')\n")
        result = self.execute("unittest", "test_case", "-q")
        self.assertEqual(result.exit_code, 1)
        captured = self.capture["process"]
        self.assertEqual(captured.stdout.data.count(b"ONLY-ONCE"), 1)
        self.assertTrue(captured.stdout.truncated)
        self.assertLessEqual(len(captured.stdout.data), 4096)
        self.assertIn(b"original failure", captured.stderr.data)

    def test_cancellation_during_snapshot_never_starts_fallback(self):
        with mock.patch.object(inputs, "InputSnapshot", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.execute("unittest", "test_case", "-q")
        self.assertNotIn("process", self.capture)

    def test_sigterm_retains_output_and_stops_admitted_target(self):
        (self.root / "test_case.py").write_text(
            "import os, time, unittest\nfrom pathlib import Path\n"
            "class Case(unittest.TestCase):\n def test_wait(self):\n"
            "  print('ONLY-ONCE', flush=True)\n"
            "  Path('ready.pid').write_text(str(os.getpid()))\n  time.sleep(60)\n")
        script = (
            "import json, os, secrets, sys\nfrom pathlib import Path\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
            "from hooks import click_authoritative_observer as observer\n"
            "from hooks import click_observer_runtime as runtime\n"
            "root=Path(sys.argv[1]); capture={}\n"
            "def fallback():\n raise AssertionError('unexpected fallback')\n"
            f"result=observer.run_command([sys.executable, '-m', 'unittest', 'test_case', '-q'], workspace=root, observation_root=root, environment=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0'), binding_context={self.context!r}, runtime={self.runtime!r}, runner_token=secrets.token_hex(32), execute_unobserved=fallback, capture_output=capture, resolve_backend=lambda name, **kw: (str(runtime.inventory.trusted_executable(name, root)), ''), digest_file=runtime._digest_file)\n"
            "print(json.dumps({'exit': result.exit_code, 'output': capture['process'].stdout.data.decode()}))\n")
        harness = subprocess.Popen([sys.executable, "-c", script, str(self.root)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        pid_file = self.root / "ready.pid"
        target_pid = None
        try:
            deadline = time.monotonic() + 20
            while not pid_file.exists() and harness.poll() is None and time.monotonic() < deadline:
                time.sleep(0.025)
            self.assertTrue(pid_file.exists(), "target never became ready")
            target_pid = int(pid_file.read_text())
            harness.terminate()
            stdout, stderr = harness.communicate(timeout=10)
            self.assertEqual(harness.returncode, 0, stderr)
            result = json.loads(stdout)
            self.assertEqual(result["exit"], 130)
            self.assertEqual(result["output"].count("ONLY-ONCE"), 1)
            with self.assertRaises(ProcessLookupError):
                os.kill(target_pid, 0)
            target_pid = None
        finally:
            if harness.poll() is None:
                observer.click_process.terminate_process_group(harness)
                harness.communicate(timeout=5)
            if target_pid is not None:
                try:
                    os.kill(target_pid, 9)
                except ProcessLookupError:
                    pass

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is not installed")
    def test_pytest_default_capture_keeps_original_failure_diagnostics(self):
        (self.root / "test_case.py").write_text("def test_one():\n print('ONLY-ONCE')\n assert False, 'original failure'\n")
        result = self.execute("pytest", "test_case.py", "-q")
        self.assertEqual(result.exit_code, 1)
        captured = self.capture["process"].stdout.data
        self.assertIn(b"original failure", captured)
        self.assertIn(b"1 failed", captured)
        # Cache/output side effects can invalidate input completeness. The
        # actual failure and diagnostics still come from the original run.
        self.assertEqual(self.observation["status"], "failed")

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is not installed")
    def test_pytest_clock_consumption_cannot_issue_reuse_authority(self):
        (self.root / "test_case.py").write_text("import time\ndef test_one():\n assert time.time() > 0\n")
        self.execute("pytest", "test_case.py", "-q", "-s", "-p", "no:cacheprovider")
        self.assertIn("time-random-input", self.observation["ineligibility_reasons"])

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is not installed")
    def test_pytest_fixture_inputs_and_output(self):
        (self.root / "fixture.txt").write_text("ready")
        (self.root / "conftest.py").write_text("import pytest\nfrom pathlib import Path\n@pytest.fixture\ndef value():\n return Path('fixture.txt').read_text()\n")
        (self.root / "test_case.py").write_text("def test_one(value):\n print('ONLY-ONCE')\n assert value == 'ready'\n")
        result = self.execute("pytest", "test_case.py", "-q", "-s", "-p", "no:cacheprovider")
        self.assertEqual(result.exit_code, 0, self.capture["process"])
        self.assertEqual(self.capture["process"].stdout.data.count(b"ONLY-ONCE"), 1)
        self.assertEqual(self.observation["status"], "complete", self.observation["ineligibility_reasons"])
        self.assertIn("fixture.txt", self.observation["paths"])
        (self.root / "fixture.txt").write_text("changed")
        self.assertFalse(inputs.records_current(self.root, self.runtime["artifact_id"], self.observation["inputs"], profile=self.runtime["profile"]))


@unittest.skipUnless(sys.platform == "linux" and runtime.profiles.current() and importlib.util.find_spec("pytest"), "native pytest profile unavailable")
class PytestEvidenceReuseTests(ClickGateTestCase):
    def test_real_claimed_pytest_sources_requalify_independently(self):
        (self.workspace / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
        for name in ("alpha", "beta"):
            (self.workspace / name).mkdir()
            (self.workspace / name / "test_case.py").write_text("VALUE = 1\ndef test_value():\n assert VALUE > 0\n")
        self.initialize_git(".gitignore", "alpha", "beta")
        commands = [[sys.executable, "-m", "pytest", "-s", "-q", "-p", "no:cacheprovider", name + "/test_case.py"] for name in ("alpha", "beta")]
        def execute(turn):
            payload = self.verify_gate(commands, turn, evidence_ids=["ALPHA", "BETA"])
            result = self.run_rewritten(payload)
            self.last_output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            state_file = next((self.plugin_data / "gate-state").glob("session-contract-*.json"))
            return json.loads(state_file.read_text())
        first = execute("turn-1")
        for source in first["evidence_state"]["sources"].values():
            self.assertEqual(source["verified_dependency_provider"], "runtime-observed-inputs-v1", self.last_output)
        patch = "*** Begin Patch\n*** Update File: alpha/test_case.py\n@@\n-VALUE = 1\n+VALUE = 2\n*** End Patch"
        self.pre_tool("apply_patch", patch, "turn-2", tool_use_id="pytest-change")
        (self.workspace / "alpha/test_case.py").write_text("VALUE = 2\ndef test_value():\n assert VALUE > 0\n")
        self.tool_hook("post-tool", "apply_patch", {"patch": patch}, turn_id="turn-2", tool_use_id="pytest-change")
        second = execute("turn-2")
        decisions = {row["source_key"]: row["decision"] for row in second["verification"]["incremental_plan"]["decisions"]}
        self.assertEqual(decisions[CLICK_EVIDENCE.evidence_key("ALPHA")], "run")
        beta = first["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("BETA")]
        self.assertEqual(decisions[CLICK_EVIDENCE.evidence_key("BETA")], "reuse-dependency", beta["verified_dependency_paths"])
