from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from hooks import click_hook_transport, click_hook_worker


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "click_hook.py"


def _event(
    mode: str, workspace: Path, session_id: str, *, tool_use_id: str = "tool-1"
) -> dict[str, object]:
    event: dict[str, object] = {
        "session_id": session_id,
        "turn_id": "turn-1",
        "cwd": str(workspace),
        "model": "test-model",
        "permission_mode": "default",
    }
    if mode == "prompt-submit":
        event.update(
            {"hook_event_name": "UserPromptSubmit", "prompt": "Explain Click status."}
        )
    elif mode == "pre-tool":
        event.update(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "exec_command",
                "tool_use_id": tool_use_id,
                "tool_input": {"command": "click-gate default status"},
            }
        )
    elif mode == "session-end":
        event["hook_event_name"] = "SessionEnd"
    return event


class ClickHookWorkerTests(unittest.TestCase):
    def test_worker_uses_exact_environment_without_affecting_verification_bindings(self) -> None:
        expected_environment = {"PATH": os.environ.get("PATH", ""), "TEST_VALUE": "fresh"}
        observed = []
        gate = mock.Mock(__file__=str(ROOT / "hooks" / "click_gate.py"))
        def dispatch():
            observed.append((dict(os.environ), Path.cwd()))
            return 0
        gate.main.side_effect = dispatch
        request = {
            "mode": "pre-tool", "event": {}, "request_id": "a" * 32,
            "environment": expected_environment, "cwd": str(ROOT),
            "deadline": time.time() + 5,
        }
        previous = dict(os.environ)
        response = click_hook_worker._run_gate(gate, request)
        self.assertEqual(response["returncode"], 0, response)
        self.assertEqual(observed, [(expected_environment, ROOT)])
        self.assertEqual(dict(os.environ), previous)
        request["deadline"] = time.time() - 1
        expired = click_hook_worker._run_gate(gate, request)
        self.assertEqual(expired["returncode"], 1)
        self.assertEqual(gate.main.call_count, 1)

    def test_worker_exits_after_idle_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary).resolve()
            state_name = "hook-" + "a" * 64 + "-" + "b" * 64 + ".json"
            process = subprocess.Popen(
                [sys.executable, "-B", "-c",
                 "import sys; from pathlib import Path; from hooks import click_hook_worker as w; "
                 "raise SystemExit(w._serve(Path(sys.argv[1]), sys.argv[2], 'b'*64, 0.2))",
                 str(runtime), state_name],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                _stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertFalse((runtime / state_name).exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    def test_uncertain_delivery_is_blocked_without_replay(self) -> None:
        state = {
            "token": "a" * 64,
            "host": "127.0.0.1",
            "port": 12345,
            "pid": 321,
        }
        event: dict[str, object] = {
            "session_id": "uncertain-delivery",
            "cwd": str(ROOT),
        }
        with (
            mock.patch.object(
                click_hook_transport,
                "_context",
                return_value=(ROOT, "hook-" + "a" * 64 + "-" + "b" * 64 + ".json", "b" * 64, str(ROOT)),
            ),
            mock.patch.object(click_hook_transport, "_read_state", return_value=state),
            mock.patch.object(
                click_hook_transport, "_request", return_value=(None, True)
            ),
            mock.patch.object(click_hook_transport, "_start_worker") as start_worker,
        ):
            response = click_hook_transport.invoke("pre-tool", event)

        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["returncode"], 1)
        self.assertIn("not replayed", response["stderr"])
        start_worker.assert_not_called()

    def test_concurrent_clients_share_one_startup_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            workspace = base / "workspace"
            workspace.mkdir()
            plugin_data = base / "plugin-data"
            environment = os.environ.copy()
            environment.update(
                {
                    "PLUGIN_DATA": str(plugin_data),
                    "CLICK_CONFIG_HOME": str(base / "config"),
                    "CLICK_HOOK_WORKER_IDLE_SECONDS": "30",
                }
            )
            session_id = "concurrent-worker-test"
            processes = [
                subprocess.Popen(
                    [sys.executable, "-B", str(HOOK), "pre-tool"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=workspace,
                    env=environment,
                )
                for _index in range(4)
            ]
            def communicate(item):
                index, process = item
                stdout, stderr = process.communicate(
                    json.dumps(
                        _event(
                            "pre-tool",
                            workspace,
                            session_id,
                            tool_use_id=f"tool-{index}",
                        )
                    ),
                    timeout=10,
                )
                self.assertEqual(process.returncode, 0, stderr)
                self.assertTrue(stdout)
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(communicate, enumerate(processes)))

            state_path, state = self._worker_state(plugin_data)
            self.assertGreater(int(state["pid"]), 0)
            ended = self._run(
                "session-end",
                _event("session-end", workspace, session_id),
                workspace,
                environment,
            )
            self.assertEqual(ended.returncode, 0, ended.stderr)
            deadline = time.monotonic() + 2
            while state_path.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(state_path.exists())

    def test_separate_hook_clients_reuse_one_worker_and_session_end_stops_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            workspace = base / "workspace"
            workspace.mkdir()
            plugin_data = base / "plugin-data"
            environment = os.environ.copy()
            environment.update(
                {
                    "PLUGIN_DATA": str(plugin_data),
                    "CLICK_CONFIG_HOME": str(base / "config"),
                    "CLICK_HOOK_WORKER_IDLE_SECONDS": "30",
                }
            )
            session_id = "resident-worker-test"

            first = self._run(
                "prompt-submit",
                _event("prompt-submit", workspace, session_id),
                workspace,
                environment,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            state_path, first_state = self._worker_state(plugin_data)

            second = self._run(
                "pre-tool",
                _event("pre-tool", workspace, session_id),
                workspace,
                environment,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            payload = json.loads(second.stdout)
            self.assertIn(
                "Click default mode:",
                payload["hookSpecificOutput"]["updatedInput"]["command"],
            )
            self.assertEqual(
                self._worker_state(plugin_data)[1]["pid"], first_state["pid"]
            )

            ended = self._run(
                "session-end",
                _event("session-end", workspace, session_id),
                workspace,
                environment,
            )
            self.assertEqual(ended.returncode, 0, ended.stderr)
            deadline = time.monotonic() + 2
            while state_path.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(state_path.exists())

    def test_explicit_disable_uses_one_shot_gate_without_worker_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            workspace = base / "workspace"
            workspace.mkdir()
            plugin_data = base / "plugin-data"
            environment = os.environ.copy()
            environment.update(
                {
                    "PLUGIN_DATA": str(plugin_data),
                    "CLICK_CONFIG_HOME": str(base / "config"),
                    "CLICK_HOOK_WORKER": "0",
                }
            )
            result = self._run(
                "pre-tool",
                _event("pre-tool", workspace, "one-shot-test"),
                workspace,
                environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((plugin_data / "hook-runtime-v1").exists())

    @staticmethod
    def _run(
        mode: str,
        event: dict[str, object],
        workspace: Path,
        environment: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(HOOK), mode],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            cwd=workspace,
            env=environment,
            check=False,
            timeout=10,
        )

    @staticmethod
    def _worker_state(plugin_data: Path) -> tuple[Path, dict[str, object]]:
        states = list((plugin_data / "hook-runtime-v1").glob("hook-*.json"))
        if len(states) != 1:
            raise AssertionError(f"expected one worker state, found {states}")
        return states[0], json.loads(states[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
