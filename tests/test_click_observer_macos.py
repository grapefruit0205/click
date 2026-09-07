from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from hooks import (
    click_dependency_cache,
    click_inspection,
    click_observer_macos,
    click_process,
)


EVIDENCE_KEY = "a" * 64
CHECK_DIGEST = "b" * 64
BACKEND_DIGEST = "c" * 64


class _FakeProcess:
    def __init__(
        self,
        *,
        pid: int,
        returncode: int,
        running: bool = False,
        stdout: bytes = b"",
        stderr: bytes = b"",
        interrupt: bool = False,
        command_name: str = "tool",
    ) -> None:
        self.pid = pid
        self.command_name = command_name
        self.returncode = None if running else returncode
        self._final_returncode = returncode
        self._running = running
        self._interrupt = interrupt
        self.stdout = BytesIO(stdout)
        self.stderr = BytesIO(stderr)

    def wait(self, timeout: float | None = None) -> int:
        if self._interrupt and timeout is None:
            raise KeyboardInterrupt
        if self._running and timeout is not None:
            raise subprocess.TimeoutExpired("fake", timeout)
        self._running = False
        self.returncode = self._final_returncode
        return self._final_returncode

    def poll(self) -> int | None:
        return None if self._running else self.returncode

    def terminate_for_test(self) -> int:
        self._running = False
        self.returncode = self._final_returncode
        return self._final_returncode


class ClickObserverMacOSTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()

    def trace_text(self, *lines: str) -> bytes:
        return ("\n".join(lines) + "\n").encode()

    def test_parser_normalizes_inputs_and_never_retains_external_paths(self) -> None:
        root = self.workspace.as_posix()
        raw = self.trace_text(
            "12:00:00.000001 execve            /usr/bin/python3 0.000010 Python.20",
            f"12:00:00.000002 open F=3 (R_____) {root}/src/input.py "
            "0.000011 Python.20",
            f"12:00:00.000003 stat64 Err#2 {root}/missing.cfg "
            "0.000012 Python.20",
            f"12:00:00.000004 getdirentries64 {root}/pkg "
            "0.000013 Python.20",
            "12:00:00.000005 posix_spawn /usr/bin/git 0.000014 Python.20",
        )

        parsed = click_observer_macos.parse_fs_usage(
            raw, workspace=self.workspace
        )

        self.assertTrue(parsed.root_exec_observed)
        self.assertFalse(parsed.process_tree_complete)
        self.assertEqual(parsed.child_process_count, 1)
        self.assertEqual(parsed.external_input_count, 2)
        self.assertEqual(parsed.unresolved_event_count, 0)
        self.assertEqual(
            parsed.inputs,
            (
                {
                    "path": "missing.cfg",
                    "kind": "missing",
                    "operations": ["metadata"],
                },
                {
                    "path": "pkg/",
                    "kind": "directory",
                    "operations": ["enumerate"],
                },
                {
                    "path": "src/input.py",
                    "kind": "file",
                    "operations": ["read"],
                },
            ),
        )
        rendered = json.dumps(parsed.inputs)
        self.assertNotIn("/usr/bin", rendered)
        self.assertNotIn(root, rendered)
        self.assertEqual(
            {item["path"] for item in parsed.absolute_inputs},
            {
                "/usr/bin/git",
                "/usr/bin/python3",
                click_observer_macos._canonical_macos_path(f"{root}/missing.cfg"),
                click_observer_macos._canonical_macos_path(f"{root}/pkg"),
                click_observer_macos._canonical_macos_path(f"{root}/src/input.py"),
            },
        )

    def test_parser_marks_truncation_and_missing_exec_incomplete(self) -> None:
        parsed = click_observer_macos.parse_fs_usage(
            self.trace_text(
                f"12:00:00.000002 open F=3 (R_____) "
                f"{self.workspace.as_posix()}/input.txt 0.000011 Python.20"
            ),
            workspace=self.workspace,
            truncated=True,
        )
        self.assertFalse(parsed.root_exec_observed)
        self.assertFalse(parsed.process_tree_complete)
        self.assertEqual(parsed.unresolved_event_count, 2)

    def test_suspended_target_binding_replaces_missing_exec_event(self) -> None:
        parsed = click_observer_macos.parse_fs_usage(
            self.trace_text(
                f"12:00:00.000002 open F=3 (R_____) "
                f"{self.workspace.as_posix()}/input.txt 0.000011 Python.20"
            ),
            workspace=self.workspace,
            root_execution_bound=True,
        )

        self.assertTrue(parsed.root_exec_observed)
        self.assertTrue(parsed.process_tree_complete)
        self.assertEqual(parsed.unresolved_event_count, 0)

        name_filtered = click_observer_macos.parse_fs_usage(
            self.trace_text(
                f"12:00:00.000002 open F=3 (R_____) "
                f"{self.workspace.as_posix()}/input.txt 0.000011 Python.20"
            ),
            workspace=self.workspace,
            root_execution_bound=True,
            process_scope_complete=False,
        )
        self.assertFalse(name_filtered.process_tree_complete)

    def test_authoritative_parser_binds_the_workspace_root(self) -> None:
        raw = self.trace_text(
            "12:00:00.000001 execve /usr/bin/python3 0.000010 Python.20",
            f"12:00:00.000002 getdirentries64 {self.workspace.as_posix()} "
            "0.000011 Python.20",
        )

        shadow = click_observer_macos.parse_fs_usage(
            raw, workspace=self.workspace
        )
        authoritative = click_observer_macos.parse_fs_usage(
            raw, workspace=self.workspace, allow_workspace_root=True
        )

        self.assertEqual(shadow.unresolved_event_count, 1)
        self.assertFalse(shadow.process_tree_complete)
        self.assertEqual(authoritative.unresolved_event_count, 0)
        self.assertTrue(authoritative.process_tree_complete)
        self.assertIn(
            {
                "path": click_observer_macos._canonical_macos_path(
                    self.workspace.as_posix()
                ),
                "kind": "directory",
                "operations": ["enumerate"],
            },
            authoritative.absolute_inputs,
        )

    def test_authoritative_parser_keeps_only_the_bound_main_thread(self) -> None:
        root = self.workspace.as_posix()
        parsed = click_observer_macos.parse_fs_usage(
            self.trace_text(
                f"12:00:00.000001 open F=3 (R_____) {root}/wanted.txt "
                "0.000010 Python.20",
                f"12:00:00.000002 open F=4 (R_____) {root}/other.txt "
                "0.000011 Python.21",
            ),
            workspace=self.workspace,
            root_execution_bound=True,
            root_thread_id=20,
        )

        self.assertEqual(
            parsed.inputs,
            (
                {
                    "path": "wanted.txt",
                    "kind": "file",
                    "operations": ["read"],
                },
            ),
        )
        self.assertEqual(parsed.unresolved_event_count, 0)
        self.assertTrue(parsed.process_tree_complete)

    def test_native_suspended_spawn_rejects_invalid_inputs_before_launch(self) -> None:
        with self.assertRaises(ValueError):
            click_observer_macos._spawn_suspended_macos(
                [], cwd=self.workspace, env={}
            )
        with self.assertRaises(ValueError):
            click_observer_macos._spawn_suspended_macos(
                ["bad\x00command"], cwd=self.workspace, env={}
            )
        with self.assertRaises(ValueError):
            click_observer_macos._spawn_suspended_macos(
                ["tool"], cwd=self.workspace, env={"BAD=KEY": "value"}
            )

    def test_parser_handles_errno_write_only_and_pathless_events_safely(self) -> None:
        root = self.workspace.as_posix()
        parsed = click_observer_macos.parse_fs_usage(
            self.trace_text(
                "12:00:00.000001 execve /usr/bin/python3 "
                "0.000010 Python.20",
                f"12:00:00.000002 open [  2] {root}/missing.txt "
                "0.000011 Python.20",
                f"12:00:00.000003 open F=3 (_WCA__) {root}/output.txt "
                "0.000012 Python.20",
                "12:00:00.000004 read F=3 B=0x10 0.000013 Python.20",
            ),
            workspace=self.workspace,
        )

        self.assertEqual(
            parsed.inputs,
            (
                {
                    "path": "missing.txt",
                    "kind": "missing",
                    "operations": ["read"],
                },
            ),
        )
        self.assertEqual(parsed.unresolved_event_count, 1)
        self.assertFalse(parsed.process_tree_complete)

    def test_parser_ignores_fixed_dyld_startup_housekeeping(self) -> None:
        parsed = click_observer_macos.parse_fs_usage(
            self.trace_text(
                "12:00:00.000001 fsgetpath /usr/lib/dyld "
                "0.000010 Python.20",
                "12:00:00.000002 openat F=4 (R______________) "
                "[3]/../../System/Volumes/Preboot/Cryptexes/OS "
                "0.000011 Python.20",
                "12:00:00.000003 openat F=6 (R______________) "
                "[4]/System/Library/dyld 0.000012 Python.20",
                "12:00:00.000004 stat64 Err#2 "
                "/AppleInternal/XBS/.isChrooted 0.000013 Python.20",
                "12:00:00.000005 open F=3 (R_____) /dev/dtracehelper "
                "0.000014 Python.20",
                "12:00:00.000006 fstatat64 [4]/System/Library/dyld "
                "0.000015 Python.20",
            ),
            workspace=self.workspace,
            root_execution_bound=True,
        )

        self.assertEqual(parsed.inputs, ())
        self.assertEqual(parsed.absolute_inputs, ())
        self.assertEqual(parsed.unresolved_event_count, 0)
        self.assertTrue(parsed.process_tree_complete)

    def test_parser_accepts_absolute_openat_path_after_dirfd_prefix(self) -> None:
        root = self.workspace.as_posix()
        parsed = click_observer_macos.parse_fs_usage(
            self.trace_text(
                "12:00:00.000001 execve /usr/bin/python3 "
                "0.000010 Python.20",
                f"12:00:00.000002 openat F=3 (R_____) [ -2]/{root}/input.txt "
                "0.000011 Python.20",
            ),
            workspace=self.workspace,
        )

        self.assertEqual(
            parsed.inputs,
            (
                {
                    "path": "input.txt",
                    "kind": "file",
                    "operations": ["read"],
                },
            ),
        )
        self.assertEqual(parsed.unresolved_event_count, 0)
        self.assertTrue(parsed.process_tree_complete)
        self.assertIn(
            click_observer_macos._canonical_macos_path(f"{root}/input.txt"),
            {item["path"] for item in parsed.absolute_inputs},
        )
        self.assertEqual(
            click_observer_macos._candidate_path(
                "F=3 (R_____) [ -2]/D:/workspace/input.txt"
            ),
            "D:/workspace/input.txt",
        )

    def test_parser_does_not_guess_relative_openat_path(self) -> None:
        parsed = click_observer_macos.parse_fs_usage(
            self.trace_text(
                "12:00:00.000001 execve /usr/bin/python3 "
                "0.000010 Python.20",
                "12:00:00.000002 openat F=3 (R_____) [ -2]/input.txt "
                "0.000011 Python.20",
            ),
            workspace=self.workspace,
        )

        self.assertEqual(parsed.inputs, ())
        self.assertEqual(parsed.unresolved_event_count, 1)
        self.assertFalse(parsed.process_tree_complete)

    def test_parser_projects_bound_relative_open_paths_as_partial(self) -> None:
        parsed = click_observer_macos.parse_fs_usage(
            self.trace_text(
                "12:00:00.000001 open F=3 (R_____) input.txt "
                "0.000010 Python.20",
                "12:00:00.000002 openat F=4 (R_____) [ -2]/nested.txt "
                "0.000011 Python.20",
                "12:00:00.000003 openat F=5 (R_____) [ 9]/unknown.txt "
                "0.000012 Python.20",
            ),
            workspace=self.workspace,
            root_execution_bound=True,
            process_scope_complete=False,
        )

        self.assertEqual(
            parsed.inputs,
            (
                {
                    "path": "input.txt",
                    "kind": "file",
                    "operations": ["read"],
                },
                {
                    "path": "nested.txt",
                    "kind": "file",
                    "operations": ["read"],
                },
            ),
        )
        self.assertEqual(parsed.unresolved_event_count, 3)
        self.assertFalse(parsed.process_tree_complete)

    def test_authoritative_parser_accepts_cwd_bound_relative_paths(self) -> None:
        parsed = click_observer_macos.parse_fs_usage(
            self.trace_text(
                "12:00:00.000001 open F=3 (R_____) tests/input.py "
                "0.000010 Python.20"
            ),
            workspace=self.workspace,
            root_execution_bound=True,
            relative_cwd_bound=True,
        )

        self.assertEqual(
            parsed.inputs,
            (
                {
                    "path": "tests/input.py",
                    "kind": "file",
                    "operations": ["read"],
                },
            ),
        )
        self.assertEqual(parsed.unresolved_event_count, 0)
        self.assertTrue(parsed.process_tree_complete)

    def test_authoritative_parser_restores_only_known_rootless_paths(self) -> None:
        runtime_directory = tempfile.TemporaryDirectory(
            prefix="click-native-observer-example-"
        )
        self.addCleanup(runtime_directory.cleanup)
        runtime = Path(runtime_directory.name)
        rootless = runtime.as_posix().lstrip("/")
        parsed = click_observer_macos.parse_fs_usage(
            self.trace_text(
                f"12:00:00.000001 open F=3 (R_____) {rootless}/module.so "
                "0.000010 Python.20"
            ),
            workspace=self.workspace,
            root_execution_bound=True,
            relative_cwd_bound=True,
            absolute_roots=(runtime,),
        )

        self.assertEqual(parsed.inputs, ())
        self.assertEqual(parsed.unresolved_event_count, 0)
        self.assertIn(
            {
                "path": click_observer_macos._canonical_macos_path(
                    f"{runtime.as_posix()}/module.so"
                ),
                "kind": "file",
                "operations": ["read"],
            },
            parsed.absolute_inputs,
        )

    def test_parser_normalizes_macos_data_volume_and_private_aliases(self) -> None:
        logical_root = self.workspace.as_posix()
        physical_root = (
            "/System/Volumes/Data" + logical_root
            if logical_root.startswith("/")
            else logical_root
        )
        parsed = click_observer_macos.parse_fs_usage(
            self.trace_text(
                "12:00:00.000001 execve /usr/bin/python3 "
                "0.000010 Python.20",
                f"12:00:00.000002 open F=3 (R_____) "
                f"{physical_root}/input.txt 0.000011 Python.20",
            ),
            workspace=self.workspace,
        )

        self.assertEqual(
            parsed.inputs,
            (
                {
                    "path": "input.txt",
                    "kind": "file",
                    "operations": ["read"],
                },
            ),
        )
        self.assertEqual(parsed.external_input_count, 1)
        self.assertEqual(parsed.unresolved_event_count, 0)

    def test_parser_bounds_aggregate_inputs(self) -> None:
        root = self.workspace.as_posix()
        with mock.patch.object(
            click_dependency_cache, "MAX_SHADOW_OBSERVER_INPUTS", 1
        ):
            parsed = click_observer_macos.parse_fs_usage(
                self.trace_text(
                    "12:00:00.000001 execve /usr/bin/python3 "
                    "0.000010 Python.20",
                    f"12:00:00.000002 open F=3 (R_____) {root}/a.txt "
                    "0.000011 Python.20",
                    f"12:00:00.000003 open F=4 (R_____) {root}/b.txt "
                    "0.000012 Python.20",
                ),
                workspace=self.workspace,
            )

        self.assertEqual(len(parsed.inputs), 1)
        self.assertEqual(parsed.unresolved_event_count, 1)
        self.assertFalse(parsed.process_tree_complete)

    def test_unprivileged_execution_uses_fallback_exactly_once(self) -> None:
        calls: list[int] = []
        result = click_observer_macos.run_command(
            ["check"],
            workspace=self.workspace,
            environment={},
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=1,
            execute_unobserved=lambda: calls.append(1) or 7,
            resolve_backend=lambda *_args, **_kwargs: self.fail(
                "an unprivileged run must not resolve fs_usage"
            ),
            privilege_probe=lambda: False,
            collector=lambda *_args, **_kwargs: self.fail(
                "an unprivileged run must not start collection"
            ),
            system_name="Darwin",
        )
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(calls, [1])
        self.assertEqual(result.record["status"], "unavailable")
        self.assertIsNone(result.record["backend"])

    def test_failure_before_target_release_falls_back_once(self) -> None:
        calls: list[int] = []
        result = click_observer_macos.run_command(
            ["check"],
            workspace=self.workspace,
            environment={},
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=2,
            execute_unobserved=lambda: calls.append(1) or 9,
            resolve_backend=lambda *_args, **_kwargs: ("/usr/bin/fs_usage", ""),
            digest_file=lambda _path: BACKEND_DIGEST,
            native_backend_probe=lambda _executable: True,
            system_version=lambda: "15.0",
            privilege_probe=lambda: True,
            collector=lambda *_args, **_kwargs: click_observer_macos.CollectedExecution(
                127, b"permission denied", False, True, False, 0, 4
            ),
            system_name="Darwin",
        )
        self.assertEqual(result.exit_code, 9)
        self.assertEqual(calls, [1])
        self.assertEqual(result.record["status"], "failed")
        self.assertEqual(result.record["backend"]["name"], "fs_usage")

    def test_failure_after_target_release_never_reruns_target(self) -> None:
        fallback = mock.Mock(return_value=91)
        result = click_observer_macos.run_command(
            ["check"],
            workspace=self.workspace,
            environment={},
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=3,
            execute_unobserved=fallback,
            resolve_backend=lambda *_args, **_kwargs: ("/usr/bin/fs_usage", ""),
            digest_file=lambda _path: BACKEND_DIGEST,
            native_backend_probe=lambda _executable: True,
            system_version=lambda: "15.0",
            privilege_probe=lambda: True,
            collector=lambda *_args, **_kwargs: click_observer_macos.CollectedExecution(
                5, b"malformed trace\n", True, True, True, 12, 3
            ),
            system_name="Darwin",
        )
        self.assertEqual(result.exit_code, 5)
        fallback.assert_not_called()
        self.assertEqual(result.record["status"], "failed")
        self.assertFalse(result.record["process_tree_complete"])

    def test_complete_collection_preserves_exit_and_relative_inputs(self) -> None:
        fallback = mock.Mock(return_value=91)
        root = self.workspace.as_posix()
        raw = self.trace_text(
            "12:00:00.000001 execve /usr/bin/python3 0.000010 Python.20",
            f"12:00:00.000002 open F=3 (R_____) {root}/input.txt "
            "0.000011 Python.20",
        )
        result = click_observer_macos.run_command(
            ["check"],
            workspace=self.workspace,
            environment={},
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=4,
            execute_unobserved=fallback,
            resolve_backend=lambda *_args, **_kwargs: ("/usr/bin/fs_usage", ""),
            digest_file=lambda _path: BACKEND_DIGEST,
            native_backend_probe=lambda _executable: True,
            system_version=lambda: "15.0",
            privilege_probe=lambda: True,
            collector=lambda *_args, **_kwargs: click_observer_macos.CollectedExecution(
                6, raw, False, False, True, 15, 2
            ),
            system_name="Darwin",
        )
        self.assertEqual(result.exit_code, 6)
        fallback.assert_not_called()
        self.assertEqual(result.record["status"], "complete")
        self.assertEqual(
            result.record["inputs"],
            [{"path": "input.txt", "kind": "file", "operations": ["read"]}],
        )
        self.assertFalse(result.record["authoritative"])
        self.assertFalse(result.record["reuse_authorized"])

    def test_backend_identity_change_discards_trace_without_rerun(self) -> None:
        fallback = mock.Mock(return_value=91)
        digests = iter((BACKEND_DIGEST, "d" * 64))
        result = click_observer_macos.run_command(
            ["check"],
            workspace=self.workspace,
            environment={},
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=5,
            execute_unobserved=fallback,
            resolve_backend=lambda *_args, **_kwargs: ("/usr/bin/fs_usage", ""),
            digest_file=lambda _path: next(digests),
            native_backend_probe=lambda _executable: True,
            system_version=lambda: "15.0",
            privilege_probe=lambda: True,
            collector=lambda *_args, **_kwargs: click_observer_macos.CollectedExecution(
                0, b"", False, False, True, 8, 1
            ),
            system_name="Darwin",
        )
        self.assertEqual(result.exit_code, 0)
        fallback.assert_not_called()
        self.assertEqual(result.record["status"], "unavailable")
        self.assertIsNone(result.record["backend"])

    def test_non_native_fs_usage_path_is_never_executed(self) -> None:
        fallback = mock.Mock(return_value=3)
        collector = mock.Mock()
        result = click_observer_macos.run_command(
            ["check"],
            workspace=self.workspace,
            environment={},
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=6,
            execute_unobserved=fallback,
            resolve_backend=lambda *_args, **_kwargs: ("/opt/bin/fs_usage", ""),
            privilege_probe=lambda: True,
            collector=collector,
            system_name="Darwin",
        )
        self.assertEqual(result.exit_code, 3)
        fallback.assert_called_once_with()
        collector.assert_not_called()
        self.assertEqual(result.record["status"], "unavailable")

    def test_collector_suspends_actual_target_before_pid_filter(self) -> None:
        collector_launches: list[list[str]] = []
        collector_environments: list[dict[str, str]] = []
        target_spawns: list[list[str]] = []
        target_environments: list[dict[str, str]] = []
        target = _FakeProcess(
            pid=4321, returncode=4, running=True, command_name="Python"
        )
        collector = _FakeProcess(
            pid=4322,
            returncode=0,
            running=True,
            stdout=b"trace-output",
        )

        def spawn_suspended(argv: list[str], **kwargs: object) -> _FakeProcess:
            target_spawns.append(list(argv))
            target_environments.append(dict(kwargs["env"]))  # type: ignore[arg-type]
            return target

        def spawn_collector(argv: list[str], **kwargs: object) -> _FakeProcess:
            collector_launches.append(list(argv))
            collector_environments.append(dict(kwargs["env"]))  # type: ignore[arg-type]
            return collector

        terminated: list[int] = []
        resumed: list[int] = []
        drained: list[float] = []

        def terminate(child: _FakeProcess) -> int:
            terminated.append(child.pid)
            return child.terminate_for_test()

        result = click_observer_macos.collect_command(
            ["tool", "--flag"],
            workspace=self.workspace,
            environment={
                "PATH": os.environ.get("PATH", os.defpath),
                "CLICK_NATIVE_OBSERVER_BOOTSTRAP": "/tmp/observer",
                "CLICK_NATIVE_OBSERVER_CHANNEL": "/tmp/native.pipe",
                "CLICK_NATIVE_OBSERVER_ORIGINAL_PYTHONPATH": "/tmp/original",
                "CLICK_NATIVE_OBSERVER_PYTHONPATH_PRESENT": "1",
                "CLICK_NATIVE_OBSERVER_ROOT": "/tmp/project",
                "PYTHONPATH": "/tmp/observer:/tmp/original",
            },
            executable="/usr/bin/fs_usage",
            spawn_argv=spawn_collector,
            spawn_suspended=spawn_suspended,
            resume_target=lambda child: resumed.append(child.pid) or True,
            discard_suspended=terminate,
            terminate_group=terminate,
            drain_wait=drained.append,
        )

        self.assertTrue(result.target_started)
        self.assertEqual(result.exit_code, 4)
        self.assertEqual(result.raw, b"trace-output")
        self.assertEqual(target_spawns, [["tool", "--flag"]])
        self.assertEqual(resumed, [4321])
        self.assertEqual(collector_launches[0][-2:], ["4321", "Python"])
        self.assertEqual(
            collector_launches[0][1:-2],
            ["-w", "-f", "pathname", "-f", "exec"],
        )
        self.assertNotIn("sudo", collector_launches[0])
        self.assertIn("CLICK_NATIVE_OBSERVER_BOOTSTRAP", target_environments[0])
        self.assertEqual(
            collector_environments,
            [
                {
                    "PATH": os.environ.get("PATH", os.defpath),
                    "PYTHONPATH": "/tmp/original",
                }
            ],
        )
        self.assertEqual(terminated, [4322])
        self.assertEqual(drained, [click_observer_macos.COLLECTOR_DRAIN_SECONDS])
        self.assertFalse(result.process_scope_complete)

    def test_authoritative_collector_uses_only_the_suspended_target_pid(self) -> None:
        launches: list[list[str]] = []
        target = _FakeProcess(
            pid=4421, returncode=0, running=True, command_name="Python"
        )
        collector = _FakeProcess(
            pid=4422, returncode=0, running=True, stdout=b"trace-output"
        )
        drained: list[float] = []

        result = click_observer_macos.collect_command(
            ["tool"],
            workspace=self.workspace,
            environment={},
            executable="/usr/bin/fs_usage",
            spawn_argv=lambda argv, **_kwargs: (
                launches.append(list(argv)) or collector
            ),
            spawn_suspended=lambda *_args, **_kwargs: target,
            resume_target=lambda _target: True,
            discard_suspended=lambda child: child.terminate_for_test(),
            terminate_group=lambda child: child.terminate_for_test(),
            drain_wait=drained.append,
            strict_pid_scope=True,
        )

        self.assertTrue(result.target_started)
        self.assertTrue(result.process_scope_complete)
        self.assertEqual(launches[0][-2:], ["4421", "Python"])
        self.assertEqual(drained, [click_observer_macos.COLLECTOR_DRAIN_SECONDS])

    def test_collector_interrupt_stops_both_retained_groups(self) -> None:
        target = _FakeProcess(pid=5321, returncode=0, running=True, interrupt=True)
        collector = _FakeProcess(pid=5322, returncode=0, running=True)

        terminated: list[int] = []

        def terminate(child: _FakeProcess) -> int:
            terminated.append(child.pid)
            return child.terminate_for_test()

        result = click_observer_macos.collect_command(
            ["tool"],
            workspace=self.workspace,
            environment={},
            executable="/usr/bin/fs_usage",
            spawn_argv=lambda *_args, **_kwargs: collector,
            spawn_suspended=lambda *_args, **_kwargs: target,
            resume_target=lambda _target: True,
            discard_suspended=terminate,
            terminate_group=terminate,
        )
        self.assertTrue(result.target_started)
        self.assertEqual(result.exit_code, 130)
        self.assertTrue(result.failed)
        self.assertEqual(terminated, [5321, 5322])

    def test_collector_early_exit_after_resume_marks_trace_failed(self) -> None:
        collector = _FakeProcess(
            pid=6322,
            returncode=2,
            running=True,
            stdout=b"partial trace",
        )
        target = _FakeProcess(pid=6321, returncode=0, running=True)
        target_wait = target.wait

        def stop_collector_then_wait(timeout: float | None = None) -> int:
            if timeout is None:
                collector._running = False
                collector.returncode = 2
            return target_wait(timeout)

        target.wait = stop_collector_then_wait  # type: ignore[method-assign]

        result = click_observer_macos.collect_command(
            ["tool"],
            workspace=self.workspace,
            environment={},
            executable="/usr/bin/fs_usage",
            spawn_argv=lambda *_args, **_kwargs: collector,
            spawn_suspended=lambda *_args, **_kwargs: target,
            resume_target=lambda _target: True,
            discard_suspended=lambda child: child.terminate_for_test(),
            terminate_group=lambda child: child.terminate_for_test(),
        )

        self.assertTrue(result.target_started)
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.failed)

    def test_collector_failure_before_resume_discards_without_starting(self) -> None:
        target = _FakeProcess(pid=7321, returncode=-9, running=True)
        collector = _FakeProcess(pid=7322, returncode=2, stdout=b"denied")
        resumed = mock.Mock(return_value=True)
        discarded: list[int] = []

        result = click_observer_macos.collect_command(
            ["tool"],
            workspace=self.workspace,
            environment={},
            executable="/usr/bin/fs_usage",
            spawn_argv=lambda *_args, **_kwargs: collector,
            spawn_suspended=lambda *_args, **_kwargs: target,
            resume_target=resumed,
            discard_suspended=lambda child: (
                discarded.append(child.pid) or child.terminate_for_test()
            ),
        )

        self.assertFalse(result.target_started)
        self.assertTrue(result.failed)
        resumed.assert_not_called()
        self.assertEqual(discarded, [7321])


@unittest.skipUnless(platform.system() == "Darwin", "native smoke is macOS-only")
class MacOSNativeSmokeTests(unittest.TestCase):
    @unittest.skipUnless(
        click_observer_macos.has_privilege(),
        "native fs_usage smoke requires an explicitly privileged test process",
    )
    def test_native_fs_usage_reads_input_without_workspace_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            input_path = workspace / "input.txt"
            input_path.write_text("observed\n", encoding="utf-8")
            executable, error = click_inspection.resolve_read_only_executable(
                "fs_usage", workspace=workspace
            )
            self.assertFalse(error)
            self.assertIsNotNone(executable)
            fallback_calls: list[int] = []
            collected: list[click_observer_macos.CollectedExecution] = []

            def fallback() -> int:
                fallback_calls.append(1)
                return int(
                    click_process.run_argv(
                        [
                            sys.executable,
                            "-I",
                            "-S",
                            "-B",
                            "-c",
                            "from pathlib import Path; import time; "
                            "Path('input.txt').read_bytes(); time.sleep(1)",
                        ],
                        cwd=workspace,
                        env=dict(os.environ),
                    ).returncode
                )

            def collect(*args: object, **kwargs: object):
                execution = click_observer_macos.collect_command(*args, **kwargs)
                collected.append(execution)
                return execution

            result = click_observer_macos.run_command(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    "-c",
                    "from pathlib import Path; import time; "
                    "Path('input.txt').read_bytes(); time.sleep(1)",
                ],
                workspace=workspace,
                environment=dict(os.environ),
                evidence_key=EVIDENCE_KEY,
                check_digest=CHECK_DIGEST,
                mutation_revision=0,
                execute_unobserved=fallback,
                resolve_backend=click_inspection.resolve_read_only_executable,
                collector=collect,
                system_name="Darwin",
            )

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(fallback_calls, [])
            self.assertIn(result.record["status"], {"complete", "partial"})
            self.assertEqual(len(collected), 1)
            trace = collected[0]
            trace_text = trace.raw.decode("utf-8", errors="replace")
            timestamp_lines = [
                line
                for line in trace_text.splitlines()
                if re.match(r"^\s*\d{2}:\d{2}:\d{2}", line)
            ]
            diagnostic = {
                "record": result.record,
                "raw_byte_count": len(trace.raw),
                "timestamp_line_count": len(timestamp_lines),
                "event_match_count": sum(
                    click_observer_macos._EVENT.match(line) is not None
                    for line in timestamp_lines
                ),
                "workspace_mention_count": sum(
                    workspace.as_posix() in line for line in timestamp_lines
                ),
                "input_name_mention_count": sum(
                    "input.txt" in line for line in timestamp_lines
                ),
                "trace_truncated": trace.truncated,
                "collector_failed": trace.failed,
            }
            self.assertTrue(
                any(item["path"] == "input.txt" for item in result.record["inputs"]),
                diagnostic,
            )
            self.assertTrue(
                click_dependency_cache.shadow_observer_record_is_valid(result.record)
            )
            self.assertEqual(
                sorted(path.name for path in workspace.iterdir()), ["input.txt"]
            )


if __name__ == "__main__":
    unittest.main()
