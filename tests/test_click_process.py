from __future__ import annotations

import ast
import ctypes
import io
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from hooks import click_gate, click_process


def _probe_group_termination(case: str) -> dict:
    """Run only in an isolated Linux supervisor so its orphans can be reaped."""
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "could not become child subreaper")
    descendant_code = (
        "import os, signal, sys\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "os.write(int(sys.argv[1]), b'ready')\n"
        "os.close(int(sys.argv[1]))\n"
        "while True: signal.pause()\n"
    )
    parent_code = (
        "import json, os, signal, subprocess, sys\n"
        "read_fd, write_fd = os.pipe()\n"
        "child = subprocess.Popen([sys.executable, '-c', sys.argv[1], str(write_fd)], pass_fds=(write_fd,))\n"
        "os.close(write_fd)\n"
        "assert os.read(read_fd, 5) == b'ready'\n"
        "os.close(read_fd)\n"
        "metadata = {'child_pid': child.pid, 'pgid': os.getpgrp()}\n"
        "if sys.argv[3]:\n"
        "    with open(sys.argv[3], 'w') as output: json.dump(metadata, output)\n"
        "print(json.dumps(metadata), flush=True)\n"
        "if sys.argv[2] == 'live':\n"
        "    while True: signal.pause()\n"
    )

    def child_is_live(pid):
        try:
            status = Path(f"/proc/{pid}/stat").read_text()
            return status.rsplit(")", 1)[1].split()[0] not in {"Z", "X"}
        except FileNotFoundError:
            return False

    spawned = []
    metadata = None
    saved_spawn = click_process.spawn_argv

    def remember_spawn(*args, **kwargs):
        parent = saved_spawn(*args, **kwargs)
        spawned.append(parent)
        if case == "timeout":
            assert select.select([parent.stdout], [], [], 3)[0], "descendant not ready"
            json.loads(parent.stdout.readline())
            assert parent.wait(timeout=3) == 0
        return parent

    with tempfile.TemporaryDirectory(prefix="click-group-test-") as directory:
        ready_path = Path(directory) / "ready.json"
        try:
            if case == "timeout":
                timed_out = False
                with mock.patch.object(click_process, "spawn_argv", side_effect=remember_spawn):
                    try:
                        click_process.run_argv(
                            [sys.executable, "-c", parent_code, descendant_code, "exited", str(ready_path)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=0.5,
                        )
                    except subprocess.TimeoutExpired:
                        timed_out = True
                assert timed_out, "an inherited pipe should keep communicate pending"
                metadata = json.loads(ready_path.read_text())
                parent = spawned[0]
                result = parent.returncode
            else:
                parent = remember_spawn(
                    [sys.executable, "-c", parent_code, descendant_code, case, ""],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                assert select.select([parent.stdout], [], [], 3)[0], "descendant not ready"
                metadata = json.loads(parent.stdout.readline())
                assert child_is_live(metadata["child_pid"])
                assert os.getpgid(metadata["child_pid"]) == parent.pid
                if case == "exited":
                    assert parent.wait(timeout=3) == 0
                else:
                    assert parent.poll() is None
                result = click_process.terminate_process_group(parent, grace_seconds=0.1)
            assert metadata["pgid"] == parent.pid
            try:
                pidfd = os.pidfd_open(metadata["child_pid"])
            except ProcessLookupError:
                pass
            else:
                try:
                    select.select([pidfd], [], [], 1)
                finally:
                    os.close(pidfd)
            return {
                "case": case,
                "result": result,
                "parent_returncode": parent.returncode,
                "child_live_after": child_is_live(metadata["child_pid"]),
            }
        finally:
            if spawned:
                parent = spawned[0]
                try:
                    os.killpg(parent.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                parent.wait(timeout=3)
                if metadata is None and ready_path.exists():
                    metadata = json.loads(ready_path.read_text())
                try:
                    while True:
                        os.waitpid(-1, 0)
                except ChildProcessError:
                    pass
                for stream in (parent.stdout, parent.stderr):
                    if stream is not None:
                        stream.close()


class ClickProcessTests(unittest.TestCase):
    def _assert_real_group_terminated(self, case: str, returncode: int) -> None:
        # A separate supervisor owns subreaper state; it cannot adopt children
        # belonging to this unittest process or other concurrently running work.
        supervisor = subprocess.run(
            [sys.executable, "-B", "-c", (
                "import json; from tests.test_click_process import _probe_group_termination; "
                f"print(json.dumps(_probe_group_termination({case!r})))"
            )],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=12,
        )
        self.assertEqual(supervisor.returncode, 0, supervisor.stderr)
        result = json.loads(supervisor.stdout)
        self.assertEqual(result["parent_returncode"], returncode)
        self.assertEqual(result["result"], returncode)
        self.assertFalse(result["child_live_after"], result)

    @unittest.skipUnless(sys.platform == "linux" and hasattr(os, "pidfd_open"), "real group fixture needs Linux subreaper, pidfd and /proc")
    def test_termination_stops_descendant_after_parent_already_exited(self) -> None:
        self._assert_real_group_terminated("exited", 0)

    @unittest.skipUnless(sys.platform == "linux" and hasattr(os, "pidfd_open"), "real group fixture needs Linux subreaper, pidfd and /proc")
    def test_termination_stops_term_ignoring_descendant_after_parent_exits(self) -> None:
        self._assert_real_group_terminated("live", -signal.SIGTERM)

    @unittest.skipUnless(sys.platform == "linux" and hasattr(os, "pidfd_open"), "real group fixture needs Linux subreaper, pidfd and /proc")
    def test_run_argv_timeout_stops_descendant_holding_exited_parent_pipe(self) -> None:
        self._assert_real_group_terminated("timeout", 0)

    def test_target_start_is_after_spawn_and_not_reported_for_probe_or_spawn_failure(self):
        child = mock.Mock(returncode=0)
        child.communicate.return_value = (b"", b"")
        called = []
        with click_process.observe_target_start(lambda: called.append("started")):
            with mock.patch.object(click_process, "spawn_argv", return_value=child):
                click_process.run_argv(["probe"])
                self.assertEqual(called, [])
                click_process.run_argv(["check"], target=True)
                self.assertEqual(called, ["started"])
            with mock.patch.object(click_process, "spawn_argv", side_effect=OSError("unavailable")):
                with self.assertRaises(OSError):
                    click_process.run_argv(["check"], target=True)
        self.assertEqual(called, ["started"])

    def test_process_module_does_not_depend_on_gate_state_or_evidence(self) -> None:
        source = Path(click_process.__file__).read_text(encoding="utf-8")
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

        for forbidden in ("click_gate", "click_state", "evidence"):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(forbidden in name.split(".") for name in imported),
                    imported,
                )

    def test_gate_does_not_reexport_process_primitives(self) -> None:
        for name in (
            "_copy_limited_output",
            "_isolated_subprocess_kwargs",
            "_terminate_managed_child",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(click_gate, name))

    def test_isolation_kwargs_are_platform_specific(self) -> None:
        with mock.patch.object(click_process.os, "name", "posix"):
            self.assertEqual(
                click_process.isolated_subprocess_kwargs(),
                {"start_new_session": True},
            )
        with (
            mock.patch.object(click_process.os, "name", "nt"),
            mock.patch.object(
                click_process.subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                512,
                create=True,
            ),
        ):
            self.assertEqual(
                click_process.isolated_subprocess_kwargs(),
                {"creationflags": 512},
            )

    def test_run_argv_is_shell_free_and_uses_an_isolated_group(self) -> None:
        child = mock.Mock(spec=subprocess.Popen)
        child.communicate.return_value = (b"captured", None)
        child.returncode = 0
        with mock.patch.object(
            click_process,
            "spawn_argv",
            return_value=child,
        ) as spawn:
            result = click_process.run_argv(
                ("tool", "--flag"),
                cwd=Path("workspace"),
                env={"SAFE": "1"},
                stdout=subprocess.PIPE,
            )

        self.assertEqual(result.args, ["tool", "--flag"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"captured")
        self.assertIsNone(result.stderr)
        spawn.assert_called_once_with(
            ["tool", "--flag"],
            cwd=Path("workspace"),
            env={"SAFE": "1"},
            stdin=None,
            stdout=subprocess.PIPE,
            stderr=None,
        )
        child.communicate.assert_called_once_with(timeout=None)

    def test_run_argv_terminates_process_group_on_keyboard_interrupt(self) -> None:
        child = mock.Mock(spec=subprocess.Popen)
        child.communicate.side_effect = KeyboardInterrupt
        with (
            mock.patch.object(click_process, "spawn_argv", return_value=child),
            mock.patch.object(click_process, "terminate_process_group") as terminate,
        ):
            with self.assertRaises(KeyboardInterrupt):
                click_process.run_argv(["tool", "--flag"])

        terminate.assert_called_once_with(child)

    def test_spawn_argv_is_shell_free_and_uses_an_isolated_group(self) -> None:
        child = mock.Mock(spec=subprocess.Popen)
        with (
            mock.patch.object(
                click_process,
                "isolated_subprocess_kwargs",
                return_value={"creationflags": 512},
            ),
            mock.patch.object(
                click_process.subprocess,
                "Popen",
                return_value=child,
            ) as popen,
        ):
            result = click_process.spawn_argv(
                ("service", "start"),
                cwd="workspace",
                stderr=subprocess.DEVNULL,
                close_fds=False,
            )

        self.assertIs(result, child)
        popen.assert_called_once_with(
            ["service", "start"],
            cwd="workspace",
            env=None,
            stdin=None,
            stdout=None,
            stderr=subprocess.DEVNULL,
            close_fds=False,
            shell=False,
            creationflags=512,
        )

    @unittest.skipIf(os.name == "nt", "POSIX process groups are unavailable")
    def test_terminate_process_group_uses_posix_group_signals(self) -> None:
        child = mock.Mock()
        child.pid = 4242
        child.poll.return_value = None
        child.wait.return_value = 0
        with (
            mock.patch.object(click_process.os, "name", "posix"),
            mock.patch.object(click_process.time, "monotonic", return_value=100.0),
            mock.patch.object(
                click_process.os, "killpg", create=True,
                side_effect=[None, ProcessLookupError],
            ) as killpg,
        ):
            result = click_process.terminate_process_group(
                child, grace_seconds=0.25
            )

        self.assertEqual(result, 0)
        self.assertEqual(killpg.call_args_list, [
            mock.call(4242, signal.SIGTERM), mock.call(4242, 0),
        ])
        child.wait.assert_called_once_with(timeout=0.25)

    @unittest.skipIf(os.name == "nt", "POSIX process groups are unavailable")
    def test_terminate_process_group_escalates_after_timeout(self) -> None:
        child = mock.Mock()
        child.pid = 4242
        child.poll.return_value = None
        child.wait.side_effect = [
            subprocess.TimeoutExpired("service", 0.25),
            -9,
        ]
        with (
            mock.patch.object(click_process.os, "name", "posix"),
            mock.patch.object(click_process.time, "monotonic", return_value=100.0),
            mock.patch.object(click_process.os, "killpg", create=True) as killpg,
        ):
            result = click_process.terminate_process_group(
                child, grace_seconds=0.25
            )

        self.assertEqual(result, -9)
        self.assertEqual(
            killpg.call_args_list,
            [
                mock.call(4242, signal.SIGTERM),
                mock.call(4242, signal.SIGKILL),
            ],
        )
        self.assertEqual(
            child.wait.call_args_list,
            [mock.call(timeout=0.25), mock.call(timeout=0.25)],
        )

    def test_terminate_process_group_uses_windows_control_break(self) -> None:
        child = mock.Mock()
        child.poll.return_value = None
        child.wait.return_value = 0
        with (
            mock.patch.object(click_process.os, "name", "nt"),
            mock.patch.object(
                click_process.signal,
                "CTRL_BREAK_EVENT",
                21,
                create=True,
            ),
        ):
            result = click_process.terminate_process_group(child)

        self.assertEqual(result, 0)
        child.send_signal.assert_called_once_with(21)
        child.terminate.assert_not_called()
        child.kill.assert_not_called()

    def test_windows_termination_falls_back_when_control_break_is_missing(self) -> None:
        child = mock.Mock()
        child.poll.return_value = None
        child.wait.return_value = 0
        with (
            mock.patch.object(click_process.os, "name", "nt"),
            mock.patch.object(
                click_process.signal,
                "CTRL_BREAK_EVENT",
                None,
                create=True,
            ),
        ):
            result = click_process.terminate_process_group(child)

        self.assertEqual(result, 0)
        child.send_signal.assert_not_called()
        child.terminate.assert_called_once_with()
        child.kill.assert_not_called()

    def test_windows_termination_escalates_to_kill_after_timeout(self) -> None:
        child = mock.Mock()
        child.poll.return_value = None
        child.wait.side_effect = [
            subprocess.TimeoutExpired("service", 0.25),
            1,
        ]
        with (
            mock.patch.object(click_process.os, "name", "nt"),
            mock.patch.object(
                click_process.signal,
                "CTRL_BREAK_EVENT",
                21,
                create=True,
            ),
        ):
            result = click_process.terminate_process_group(
                child, grace_seconds=0.25
            )

        self.assertEqual(result, 1)
        child.send_signal.assert_called_once_with(21)
        child.kill.assert_called_once_with()
        self.assertEqual(
            child.wait.call_args_list,
            [mock.call(timeout=0.25), mock.call(timeout=0.25)],
        )

    def test_copy_limited_output_stops_at_the_exact_byte_cap(self) -> None:
        source = io.BytesIO(b"0123456789")
        target = io.BytesIO()

        copied = click_process.copy_limited_output(
            source, target, 6, chunk_size=4
        )

        self.assertEqual(copied, 6)
        self.assertEqual(target.getvalue(), b"012345")
        self.assertEqual(source.read(), b"6789")


if __name__ == "__main__":
    unittest.main()
