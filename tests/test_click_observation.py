from __future__ import annotations

import ast
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

from hooks import click_capability, click_gate, click_observation, click_state


class ClickObservationTests(unittest.TestCase):
    def test_observation_depends_only_on_approved_runtime_leaves(self) -> None:
        source = Path(click_observation.__file__).read_text(encoding="utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imported.add(module)
                imported.update(
                    f"{module}.{alias.name}".strip(".") for alias in node.names
                )
        for forbidden in (
            "click_browser",
            "click_contract",
            "click_evidence",
            "click_gate",
            "click_host_coverage",
            "click_mutation",
            "click_service",
            "click_verification_meter",
            "click_verification_policy",
            "platform_protocol",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(forbidden in name.split(".") for name in imported),
                    imported,
                )
        for required in (
            "click_capability",
            "click_claims",
            "click_contract_state",
            "click_inspection",
            "click_observation_cache",
            "click_process",
            "click_state",
        ):
            with self.subTest(required=required):
                self.assertIn(required, imported)

    def test_gate_does_not_reexport_observation_helpers(self) -> None:
        aliases = (
            "_fresh_observation_state",
            "_unclaimed_reservation_is_fresh",
            "_observation_is_running",
            "_write_review_state",
            "_read_review_state",
            "_save_review_state",
            "_clear_review_state",
            "_managed_observation_path",
            "_record_observation_result",
        )
        for name in aliases:
            with self.subTest(name=name):
                self.assertFalse(hasattr(click_gate, name))
        self.assertEqual(
            click_gate.OBSERVATION_RESERVATION_TTL_SECONDS,
            click_observation.RESERVATION_TTL_SECONDS,
        )
        self.assertEqual(
            click_gate.MAX_OBSERVATION_OUTPUT_BYTES,
            click_observation.MAX_OUTPUT_BYTES,
        )

    def test_observation_running_semantics_preserve_claim_and_expiry(self) -> None:
        now = int(time.time())
        self.assertTrue(
            click_observation.is_running(
                {"status": "running", "runner_claimed_at": now, "started_at": 0}
            )
        )
        self.assertTrue(
            click_observation.is_running(
                {"status": "running", "runner_claimed_at": 0, "started_at": now}
            )
        )
        self.assertFalse(
            click_observation.is_running(
                {
                    "status": "running",
                    "runner_claimed_at": 0,
                    "started_at": now - click_observation.RESERVATION_TTL_SECONDS - 1,
                }
            )
        )
        self.assertFalse(click_observation.is_running({"status": "success"}))


class ClickObservationStorageRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = mock.patch.dict(os.environ, {"PLUGIN_DATA": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.event = {"session_id": "observation-recovery", "cwd": str(self.root)}
        self.path = click_state.contract_path(self.event)
        self.request = {"version": 1, "commands": [["cat", "README.md"]]}
        self.digest = click_capability.digest(self.request)
        self.output = mock.Mock(buffer=io.BytesIO())
        self.errors = mock.Mock(buffer=io.BytesIO())
        click_state.write_json(self.path, {
            "status": "evidence",
            "contract_digest": "a" * 64,
            "verification": {"mutation_revision": 0},
            "observations": click_observation.fresh_state(),
        })

    def prepare(self):
        return click_observation.prepare(
            self.event, self.request, False,
            mutation_is_running=lambda _: False,
            fresh_mutation_state=lambda: {},
            runner_script=Path(click_gate.__file__),
            render_command=json.dumps,
        )

    def reserve(self):
        command, error, _ = self.prepare()
        self.assertEqual(error, "")
        return json.loads(command)[-4:]

    def claim(self, arguments):
        with click_state.state_lock():
            request, error = click_observation.claim_run(
                self.path, json.dumps(self.request), arguments[1], arguments[2]
            )
        self.assertEqual(error, "")
        self.assertEqual(request, self.request)

    def read_state(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def errors_text(self):
        return "".join(call.args[0] for call in self.errors.write.call_args_list)

    def test_claim_storage_errors_execute_nothing_and_never_create_success(self):
        arguments = self.reserve()
        runner = mock.Mock()
        for failure in (OSError(errno.EROFS, "read-only filesystem"),
                        PermissionError(errno.EACCES, "permission denied")):
            for operation in ("state_lock", "write_json"):
                with self.subTest(failure=failure, operation=operation), \
                     mock.patch.object(click_state, operation, side_effect=failure), \
                     mock.patch.object(sys, "stderr", self.errors):
                    result = click_observation.run(arguments, run_inspection_request=runner)
                self.assertEqual(result, 2)
                entry = self.read_state()["observations"]["entries"][self.digest]
                self.assertEqual(entry["runner_claimed_at"], 0)
                self.assertIsNone(entry["last_exit_code"])
        runner.assert_not_called()
        self.assertIn("No read was executed", self.errors_text())

    def test_result_storage_failure_preserves_output_without_success_receipt(self):
        arguments = self.reserve()
        self.claim(arguments)

        def execute(commands, stdout, stderr):
            self.assertEqual(commands, self.request["commands"])
            stdout.write(b"original read output\n")
            return 0

        executor = mock.Mock(side_effect=execute)
        for failure in (OSError(errno.EROFS, "read-only filesystem"),
                        PermissionError(errno.EACCES, "permission denied")):
            with self.subTest(failure=failure), \
                 mock.patch.object(click_observation.click_observation_cache, "load", return_value=(None, None)), \
                 mock.patch.object(click_state, "write_json", side_effect=failure), \
                 mock.patch.object(sys, "stdout", self.output), \
                 mock.patch.object(sys, "stderr", self.errors):
                result = click_observation.run_request(
                    self.request, (self.path, self.digest, arguments[2]),
                    execute_commands=executor,
                )
            self.assertEqual(result, 2)
        self.assertEqual(executor.call_count, 2)  # Each request executed exactly once.
        self.assertEqual(self.output.buffer.getvalue(), b"original read output\n" * 2)
        state = self.read_state()
        self.assertEqual(state["observations"]["entries"][self.digest]["status"], "running")
        self.assertEqual(state["capability_ledger"]["entries"][0]["result"]["status"], "running")
        self.assertIn("no successful receipt", self.errors_text())

    def test_record_binding_rejection_preserves_output_and_returns_failure(self):
        arguments = self.reserve()
        self.claim(arguments)

        def execute(commands, stdout, stderr):
            stdout.write(b"read once\n")
            return 0

        with mock.patch.object(click_observation.click_observation_cache, "load", return_value=(None, None)), \
             mock.patch.object(click_observation, "record_result", return_value=False), \
             mock.patch.object(sys, "stdout", self.output), \
             mock.patch.object(sys, "stderr", self.errors):
            result = click_observation.run_request(
                self.request, (self.path, self.digest, arguments[2]), execute_commands=execute
            )
        self.assertEqual(result, 2)
        self.assertEqual(self.output.buffer.getvalue(), b"read once\n")

    def test_execution_and_failure_storage_errors_do_not_raise_twice(self):
        arguments = self.reserve()
        self.claim(arguments)
        executor = mock.Mock(side_effect=OSError(errno.ENOENT, "missing executable"))
        with mock.patch.object(click_observation.click_observation_cache, "load", return_value=(None, None)), \
             mock.patch.object(click_state, "state_lock", side_effect=OSError(errno.EROFS, "read-only filesystem")), \
             mock.patch.object(sys, "stderr", self.errors):
            result = click_observation.run_request(
                self.request, (self.path, self.digest, arguments[2]), execute_commands=executor
            )
        self.assertEqual(result, 127)
        executor.assert_called_once()
        self.assertIn("failure storage is unavailable", self.errors_text())
        self.assertIn("missing executable", self.errors_text())

    def test_fresh_unclaimed_and_live_claims_still_block_duplicate_requests(self):
        arguments = self.reserve()
        self.assertIn("already active", self.prepare()[1])
        self.claim(arguments)
        self.assertIn("already active", self.prepare()[1])
        request, error = click_observation.claim_run(
            self.path, json.dumps(self.request), self.digest, arguments[2]
        )
        self.assertIsNone(request)
        self.assertIn("replay is blocked", error)

    def test_exited_wrapper_without_terminal_result_keeps_claim_active(self):
        arguments = self.reserve()
        # A real separate runner claims its token and exits before recording
        # any target result, as a startup or result-storage failure would.
        code = (
            "import sys; from pathlib import Path; "
            "from hooks import click_observation as o, click_state as s\n"
            "with s.state_lock():\n"
            " request, error = o.claim_run(Path(sys.argv[1]), *sys.argv[2:])\n"
            "if error: raise RuntimeError(error)\n"
        )
        child = subprocess.run(
            [sys.executable, "-B", "-c", code, str(self.path),
             json.dumps(self.request), self.digest, arguments[2]],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        command, error, _ = self.prepare()
        self.assertEqual(command, "")
        self.assertIn("already active", error)
        request, error = click_observation.claim_run(
            self.path, json.dumps(self.request), self.digest, arguments[2]
        )
        self.assertIsNone(request)
        self.assertIn("replay is blocked", error)
        claims = self.read_state()["capability_ledger"]["entries"]
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["result"], {"status": "running", "exit_code": None})

    def test_process_identity_and_elapsed_time_cannot_release_claim(self):
        arguments = self.reserve()
        self.claim(arguments)
        state = self.read_state()
        entry = state["observations"]["entries"][self.digest]
        entry["started_at"] = 1
        entry["runner_claimed_at"] = 1
        for pid in (None, 0, -1, True, "123", 2**128):
            with self.subTest(pid=pid):
                entry["runner_pid"] = pid
                click_state.write_json(self.path, state)
                self.assertIn("already active", self.prepare()[1])

    def test_corrupt_abandoned_claim_is_not_replaced(self):
        arguments = self.reserve()
        self.claim(arguments)
        state = self.read_state()
        state["capability_ledger"] = {}
        click_state.write_json(self.path, state)
        command, error, _ = self.prepare()
        self.assertEqual(command, "")
        self.assertIn("already active", error)
        self.assertEqual(self.read_state()["observations"]["entries"][self.digest]["runner_token_digest"],
                         state["observations"]["entries"][self.digest]["runner_token_digest"])

    @unittest.skipUnless(sys.platform == "linux", "Linux subreaper reaps the isolated orphan")
    def test_orphaned_read_child_keeps_claim_active(self):
        self.assert_read_child_interruption(hard_kill=True)

    @unittest.skipUnless(sys.platform == "linux", "Linux subreaper checks child cleanup")
    def test_sigterm_read_child_records_interruption_and_releases_claim(self):
        self.assert_read_child_interruption(hard_kill=False)

    def assert_read_child_interruption(self, *, hard_kill):
        fifo = self.root / "blocking-read"
        os.mkfifo(fifo)
        self.request = {"version": 1, "commands": [["cat", str(fifo)]]}
        self.digest = click_capability.digest(self.request)
        arguments = self.reserve()
        child_marker = self.root / "read-child.pid"
        # The isolated harness becomes a subreaper so it can waitpid() the
        # orphan after terminating it. No child or zombie is left in the host.
        wrapper_code = textwrap.dedent("""
            import json, sys
            from pathlib import Path
            from hooks import click_observation as o, click_inspection as i, click_process as p
            original_spawn = p.spawn_argv
            def spawn(*args, **kwargs):
                child = original_spawn(*args, **kwargs)
                communicate = child.communicate
                def waiting(*args, **kwargs):
                    Path(sys.argv[1]).write_text(str(child.pid), encoding="utf-8")
                    return communicate(*args, **kwargs)
                child.communicate = waiting
                return child
            p.spawn_argv = spawn
            def execute(request, state_result):
                return o.run_request(request, state_result, execute_commands=i.execute_commands)
            raise SystemExit(o.run(json.loads(sys.argv[2]), run_inspection_request=execute))
        """)
        harness_code = textwrap.dedent("""
            import ctypes, json, os, signal, subprocess, sys, time
            from pathlib import Path
            from hooks import click_observation as o, click_gate as g
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(36, 1, 0, 0, 0):
                raise OSError(ctypes.get_errno(), "could not enable test subreaper")
            arguments, event, request, marker_name, wrapper_code, hard_kill = json.loads(sys.argv[1])
            marker = Path(marker_name)
            child_pid = None
            wrapper = subprocess.Popen(
                [sys.executable, "-B", "-c", wrapper_code, marker_name, json.dumps(arguments)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    if wrapper.poll() is not None:
                        raise AssertionError("read wrapper exited before launching its child")
                    time.sleep(0.01)
                child_pid = int(marker.read_text(encoding="utf-8"))
                wrapper.kill() if hard_kill else wrapper.terminate()
                wrapper.wait(timeout=5)
                state = json.loads(Path(arguments[0]).read_text(encoding="utf-8"))
                entry = state["observations"]["entries"][arguments[1]]
                if hard_kill:
                    assert os.waitpid(child_pid, os.WNOHANG) == (0, 0), "read child must still be alive"
                    assert o.is_running(entry), "dead wrapper PID does not prove its read child stopped"
                else:
                    assert wrapper.returncode == 130, "interruption must be recorded as failure"
                    try:
                        os.waitpid(child_pid, os.WNOHANG)
                    except ChildProcessError:
                        pass
                    else:
                        raise AssertionError("wrapper must reap its child before recording interruption")
                    assert not o.is_running(entry)
                    assert state["capability_ledger"]["entries"][0]["result"]["exit_code"] == 130
                    marker.unlink()
                    child_pid = None
                command, error, _ = o.prepare(
                    event, request, False, mutation_is_running=lambda _: False,
                    fresh_mutation_state=lambda: {}, runner_script=Path(g.__file__),
                    render_command=json.dumps,
                )
                if hard_kill:
                    assert command == "" and "already active" in error, "fresh token must remain blocked"
                else:
                    assert "already active" not in error, "completed interruption must release the active claim"
                assert len(state["capability_ledger"]["entries"]) == 1
            finally:
                if wrapper.poll() is None:
                    wrapper.kill()
                    wrapper.wait(timeout=5)
                if child_pid is None and marker.exists():
                    child_pid = int(marker.read_text(encoding="utf-8"))
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        os.waitpid(child_pid, 0)
                    except ChildProcessError:
                        pass
        """)
        result = subprocess.run(
            [sys.executable, "-B", "-c", harness_code,
             json.dumps([arguments, self.event, self.request, str(child_marker), wrapper_code, hard_kill])],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
