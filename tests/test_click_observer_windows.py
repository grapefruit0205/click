from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from hooks import (
    click_dependency_cache,
    click_inspection,
    click_observer_windows,
    click_process,
)


EVIDENCE_KEY = "a" * 64
CHECK_DIGEST = "b" * 64
BACKEND_DIGEST = "c" * 64
PROCESS_PROVIDER = click_observer_windows.PROCESS_PROVIDER
FILE_PROVIDER = click_observer_windows.FILE_PROVIDER


def _event(
    provider: str,
    event_id: int,
    *,
    execution_pid: int,
    data: dict[str, object],
) -> str:
    rendered = "".join(
        f'<Data Name="{key}">{value}</Data>' for key, value in data.items()
    )
    return (
        "<Event><System>"
        f'<Provider Name="{provider}"/><EventID>{event_id}</EventID>'
        f'<Execution ProcessID="{execution_pid}" ThreadID="1"/>'
        "</System><EventData>"
        f"{rendered}</EventData></Event>"
    )


def _document(*events: str, lost: int = 0) -> bytes:
    lost_element = f"<EventsLost>{lost}</EventsLost>" if lost else ""
    return f"<Events>{lost_element}{''.join(events)}</Events>".encode()


class _FakeTarget:
    def __init__(self, *, pid: int = 100, returncode: int = 0) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._final_returncode = returncode
        self.wait_calls = 0

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        self.returncode = self._final_returncode
        return self._final_returncode

    def poll(self) -> int | None:
        return self.returncode


class ClickObserverWindowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace_text = r"C:\work\click"

    def test_parser_scopes_inputs_to_root_and_descendants(self) -> None:
        raw = _document(
            _event(
                PROCESS_PROVIDER,
                1,
                execution_pid=4,
                data={"ProcessID": 100, "ParentProcessID": 50},
            ),
            _event(
                PROCESS_PROVIDER,
                1,
                execution_pid=4,
                data={"ProcessID": 200, "ParentProcessID": 100},
            ),
            _event(
                FILE_PROVIDER,
                12,
                execution_pid=100,
                data={"FileName": r"C:\work\click\input.txt"},
            ),
            _event(
                FILE_PROVIDER,
                20,
                execution_pid=200,
                data={"FileName": r"C:\work\click\pkg"},
            ),
            _event(
                FILE_PROVIDER,
                12,
                execution_pid=999,
                data={"FileName": r"C:\work\click\unrelated.txt"},
            ),
            _event(
                FILE_PROVIDER,
                12,
                execution_pid=100,
                data={"FileName": r"C:\Windows\System32\kernel32.dll"},
            ),
        )

        parsed = click_observer_windows.parse_windows_etw(
            raw,
            workspace=self.workspace_text,
            root_pid=100,
            root_execution_bound=True,
        )

        self.assertTrue(parsed.root_exec_observed)
        self.assertTrue(parsed.process_tree_complete)
        self.assertEqual(parsed.child_process_count, 1)
        self.assertEqual(parsed.external_input_count, 1)
        self.assertEqual(parsed.unresolved_event_count, 0)
        self.assertEqual(
            parsed.inputs,
            (
                {
                    "path": "input.txt",
                    "kind": "file",
                    "operations": ["read"],
                },
                {
                    "path": "pkg/",
                    "kind": "directory",
                    "operations": ["enumerate"],
                },
            ),
        )
        rendered = json.dumps(parsed.inputs)
        self.assertNotIn("C:\\Windows", rendered)
        self.assertNotIn(self.workspace_text, rendered)
        self.assertNotIn("unrelated", rendered)
        self.assertEqual(
            {item["path"] for item in parsed.absolute_inputs},
            {
                r"C:\Windows\System32\kernel32.dll",
                r"C:\work\click\input.txt",
                r"C:\work\click\pkg",
            },
        )

    def test_parser_uses_file_key_mapping_and_native_device_paths(self) -> None:
        raw = _document(
            _event(
                PROCESS_PROVIDER,
                1,
                execution_pid=4,
                data={"ProcessID": 100, "ParentProcessID": 50},
            ),
            _event(
                FILE_PROVIDER,
                10,
                execution_pid=100,
                data={
                    "FileKey": "0x123",
                    "FileName": r"\Device\HarddiskVolume4\work\click\bound.txt",
                },
            ),
            _event(
                FILE_PROVIDER,
                15,
                execution_pid=100,
                data={"FileKey": "0x123"},
            ),
        )

        parsed = click_observer_windows.parse_windows_etw(
            raw,
            workspace=self.workspace_text,
            root_pid=100,
            root_execution_bound=True,
            device_paths={r"\Device\HarddiskVolume4": "C:"},
        )

        self.assertEqual(
            parsed.inputs,
            (
                {
                    "path": "bound.txt",
                    "kind": "file",
                    "operations": ["metadata", "read"],
                },
            ),
        )
        self.assertTrue(parsed.process_tree_complete)
        self.assertEqual(
            parsed.absolute_inputs,
            (
                {
                    "path": r"C:\work\click\bound.txt",
                    "kind": "file",
                    "operations": ["metadata", "read"],
                },
            ),
        )

    def test_parser_marks_loss_truncation_and_unknown_events_incomplete(self) -> None:
        raw = _document(
            _event(
                PROCESS_PROVIDER,
                1,
                execution_pid=4,
                data={"ProcessID": 100, "ParentProcessID": 50},
            ),
            _event(
                FILE_PROVIDER,
                99,
                execution_pid=100,
                data={"FileName": r"C:\work\click\unknown.txt"},
            ),
            lost=2,
        )
        parsed = click_observer_windows.parse_windows_etw(
            raw,
            workspace=self.workspace_text,
            root_pid=100,
            root_execution_bound=True,
            truncated=True,
        )
        self.assertEqual(parsed.unresolved_event_count, 4)
        self.assertFalse(parsed.process_tree_complete)

    def test_parser_bounds_inputs_without_retaining_overflow_paths(self) -> None:
        raw = _document(
            _event(
                PROCESS_PROVIDER,
                1,
                execution_pid=4,
                data={"ProcessID": 100, "ParentProcessID": 50},
            ),
            _event(
                FILE_PROVIDER,
                12,
                execution_pid=100,
                data={"FileName": r"C:\work\click\a.txt"},
            ),
            _event(
                FILE_PROVIDER,
                12,
                execution_pid=100,
                data={"FileName": r"C:\work\click\b.txt"},
            ),
        )
        with mock.patch.object(
            click_dependency_cache, "MAX_SHADOW_OBSERVER_INPUTS", 1
        ):
            parsed = click_observer_windows.parse_windows_etw(
                raw,
                workspace=self.workspace_text,
                root_pid=100,
                root_execution_bound=True,
            )
        self.assertEqual(len(parsed.inputs), 1)
        self.assertEqual(parsed.unresolved_event_count, 1)
        self.assertFalse(parsed.process_tree_complete)

    def test_collection_starts_both_sessions_then_runs_target_once(self) -> None:
        target = _FakeTarget()
        calls: list[list[str]] = []
        lifecycle: list[str] = []
        temporary_roots: list[Path] = []

        process_xml = _document(
            _event(
                PROCESS_PROVIDER,
                1,
                execution_pid=4,
                data={"ProcessID": 100, "ParentProcessID": 50},
            )
        )
        file_xml = _document(
            _event(
                FILE_PROVIDER,
                12,
                execution_pid=100,
                data={"FileName": r"C:\work\click\input.txt"},
            )
        )

        def control(argv: list[str], **kwargs: object):
            calls.append(list(argv))
            cwd = Path(kwargs["cwd"])
            temporary_roots.append(cwd)
            if argv[1] == "start":
                lifecycle.append("start")
                output = Path(argv[argv.index("-o") + 1])
                output.with_name(output.stem + "_000001.etl").write_bytes(b"etl")
            elif Path(argv[0]).name.lower().startswith("tracerpt"):
                output = Path(argv[argv.index("-o") + 1])
                output.write_bytes(process_xml if "process" in output.name else file_xml)
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        spawned: list[list[str]] = []
        result = click_observer_windows.collect_command(
            ["tool", "--flag"],
            workspace=Path.cwd(),
            environment={},
            logman_executable="logman.exe",
            tracerpt_executable="tracerpt.exe",
            run_control=control,
            spawn_argv=lambda argv, **_kwargs: (
                lifecycle.append("spawn") or spawned.append(list(argv)) or target
            ),
            wait_for_sessions=lambda seconds: lifecycle.append(f"wait:{seconds}"),
        )

        self.assertTrue(result.target_started)
        self.assertEqual(result.root_pid, 100)
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.failed)
        self.assertTrue(result.process_scope_complete)
        self.assertEqual(spawned, [["tool", "--flag"]])
        self.assertEqual(target.wait_calls, 1)
        self.assertEqual(
            lifecycle,
            [
                "start",
                "start",
                f"wait:{click_observer_windows.SESSION_READY_DELAY_SECONDS}",
                "spawn",
            ],
        )
        self.assertEqual(sum(call[1] == "start" for call in calls), 2)
        self.assertEqual(
            lifecycle[:4],
            [
                "start",
                "start",
                f"wait:{click_observer_windows.SESSION_READY_DELAY_SECONDS}",
                "spawn",
            ],
        )
        self.assertEqual(sum(call[1] == "stop" for call in calls), 2)
        self.assertEqual(
            sum(Path(call[0]).name.lower().startswith("tracerpt") for call in calls),
            2,
        )
        self.assertEqual(len(result.raw), 2)
        self.assertTrue(temporary_roots)
        self.assertFalse(temporary_roots[0].exists())

    def test_collection_failure_before_target_does_not_start_it(self) -> None:
        spawned = mock.Mock()

        def denied(argv: list[str], **_kwargs: object):
            return subprocess.CompletedProcess(argv, 5, b"denied", b"")

        result = click_observer_windows.collect_command(
            ["tool"],
            workspace=Path.cwd(),
            environment={},
            logman_executable="logman.exe",
            tracerpt_executable="tracerpt.exe",
            run_control=denied,
            spawn_argv=spawned,
        )
        self.assertFalse(result.target_started)
        self.assertTrue(result.failed)
        spawned.assert_not_called()

    def test_target_spawn_failure_stops_both_started_sessions(self) -> None:
        calls: list[list[str]] = []

        def control(argv: list[str], **_kwargs: object):
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        result = click_observer_windows.collect_command(
            ["tool"],
            workspace=Path.cwd(),
            environment={},
            logman_executable="logman.exe",
            tracerpt_executable="tracerpt.exe",
            run_control=control,
            spawn_argv=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("spawn failed")
            ),
        )

        self.assertFalse(result.target_started)
        self.assertTrue(result.failed)
        self.assertEqual(sum(call[1] == "start" for call in calls), 2)
        self.assertEqual(sum(call[1] == "stop" for call in calls), 2)

        class InterruptingTarget(_FakeTarget):
            def __init__(self) -> None:
                super().__init__()
                self.wait_timeouts: list[float | None] = []

            def wait(self, timeout: float | None = None) -> int:
                self.wait_calls += 1
                self.wait_timeouts.append(timeout)
                if self.wait_calls == 1:
                    raise subprocess.TimeoutExpired("target", timeout)
                raise KeyboardInterrupt

        target = InterruptingTarget()
        interrupted_calls: list[list[str]] = []
        terminated: list[int] = []

        def interrupt_control(argv: list[str], **_kwargs: object):
            interrupted_calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        def terminate(child: _FakeTarget) -> int:
            terminated.append(child.pid)
            child.returncode = 130
            return 130

        interrupted = click_observer_windows.collect_command(
            ["tool"],
            workspace=Path.cwd(),
            environment={},
            logman_executable="logman.exe",
            tracerpt_executable="tracerpt.exe",
            run_control=interrupt_control,
            spawn_argv=lambda *_args, **_kwargs: target,
            terminate_group=terminate,
            wait_for_sessions=lambda _seconds: None,
        )

        self.assertTrue(interrupted.target_started)
        self.assertEqual(interrupted.exit_code, 130)
        self.assertTrue(interrupted.failed)
        self.assertEqual(terminated, [100])
        self.assertEqual(
            target.wait_timeouts,
            [
                click_observer_windows.TARGET_WAIT_POLL_SECONDS,
                click_observer_windows.TARGET_WAIT_POLL_SECONDS,
            ],
        )
        self.assertEqual(
            sum(call[1] == "stop" for call in interrupted_calls), 2
        )

    def test_unavailable_tools_use_fallback_exactly_once(self) -> None:
        fallback = mock.Mock(return_value=7)
        result = click_observer_windows.run_command(
            ["tool"],
            workspace=Path.cwd(),
            environment={},
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=1,
            execute_unobserved=fallback,
            resolve_backend=lambda *_args, **_kwargs: (None, "missing"),
            system_name="Windows",
        )
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(result.record["status"], "unavailable")
        fallback.assert_called_once_with()

    def test_post_start_failure_never_reruns_target(self) -> None:
        fallback = mock.Mock(return_value=99)
        collected = click_observer_windows.CollectedExecution(
            exit_code=5,
            raw=(b"<broken",),
            truncated=False,
            failed=True,
            target_started=True,
            root_pid=100,
            command_duration_ms=10,
            collector_overhead_ms=1,
            process_scope_complete=False,
        )
        result = click_observer_windows.run_command(
            ["tool"],
            workspace=Path.cwd(),
            environment={},
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=2,
            execute_unobserved=fallback,
            resolve_backend=lambda name, **_kwargs: (f"C:/{name}.exe", ""),
            digest_file=lambda _path: BACKEND_DIGEST,
            native_backend_probe=lambda *_args: True,
            system_version=lambda: "10.0.26100",
            collector=lambda *_args, **_kwargs: collected,
            device_map_provider=lambda: {},
            system_name="Windows",
        )
        self.assertEqual(result.exit_code, 5)
        self.assertEqual(result.record["status"], "failed")
        self.assertFalse(result.record["process_tree_complete"])
        self.assertFalse(result.record["authoritative"])
        self.assertFalse(result.record["reuse_authorized"])
        fallback.assert_not_called()


@unittest.skipUnless(platform.system() == "Windows", "native smoke is Windows-only")
class WindowsNativeSmokeTests(unittest.TestCase):
    def test_native_etw_observes_child_input_without_workspace_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            (workspace / "input.txt").write_text("observed\n", encoding="utf-8")
            command = [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                "import subprocess,sys,time; "
                "subprocess.run([sys.executable,'-I','-S','-B','-c',"
                "\"open('input.txt','rb').read()\"],check=True); "
                "open('input.txt','rb').read(); time.sleep(0.25)",
            ]
            fallback_calls: list[int] = []

            def fallback() -> int:
                fallback_calls.append(1)
                return int(
                    click_process.run_argv(
                        command, cwd=workspace, env=dict(os.environ)
                    ).returncode
                )

            result = click_observer_windows.run_command(
                command,
                workspace=workspace,
                environment=dict(os.environ),
                evidence_key=EVIDENCE_KEY,
                check_digest=CHECK_DIGEST,
                mutation_revision=0,
                execute_unobserved=fallback,
                resolve_backend=click_inspection.resolve_read_only_executable,
                system_name="Windows",
            )

            self.assertEqual(result.exit_code, 0, result.record)
            self.assertEqual(fallback_calls, [], result.record)
            self.assertEqual(result.record["backend"]["name"], "windows-etw")
            self.assertIn(result.record["status"], {"complete", "partial"})
            self.assertGreaterEqual(result.record["child_process_count"], 1)
            self.assertTrue(
                any(
                    item["path"].lower() == "input.txt"
                    for item in result.record["inputs"]
                ),
                result.record,
            )
            self.assertFalse(result.record["authoritative"])
            self.assertFalse(result.record["reuse_authorized"])
            self.assertTrue(
                click_dependency_cache.shadow_observer_record_is_valid(
                    result.record
                )
            )
            self.assertEqual(
                sorted(path.name for path in workspace.iterdir()), ["input.txt"]
            )


if __name__ == "__main__":
    unittest.main()
