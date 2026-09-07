from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest import mock

from hooks import click_gate, click_runner_transport, click_windows


ROOT = Path(__file__).resolve().parents[1]


class ClickRunnerTransportTests(unittest.TestCase):
    def test_transport_is_a_leaf_runtime_boundary(self) -> None:
        source = (ROOT / "hooks" / "click_runner_transport.py").read_text(
            encoding="utf-8"
        )

        for forbidden in (
            "click_contract",
            "click_evidence",
            "click_gate",
            "click_state",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(f"import {forbidden}", source)
                self.assertNotIn(f"from {forbidden}", source)

    def test_default_posix_renderer_preserves_argv(self) -> None:
        arguments = ["python3", "click gate.py", "value with spaces", "$literal"]

        with mock.patch.object(click_runner_transport.os, "name", "posix"):
            rendered = click_runner_transport.default_runner_shell_command(arguments)

        self.assertEqual(shlex.split(rendered), arguments)

    def test_host_renderer_installation_is_explicit_and_reversible(self) -> None:
        def sentinel(arguments: list[str]) -> str:
            return "host:" + "|".join(arguments)

        previous = click_runner_transport.install_runner_shell_renderer(sentinel)
        self.addCleanup(click_runner_transport.install_runner_shell_renderer, previous)

        self.assertEqual(
            click_runner_transport.render_runner_shell_command(["one", "two"]),
            "host:one|two",
        )

    def test_windows_bridge_configures_transport_without_gate_monkeypatch(self) -> None:
        source = (ROOT / "hooks" / "click_windows.py").read_text(encoding="utf-8")
        self.assertNotIn("import click_gate", source)

        with (
            mock.patch.object(click_windows.os, "name", "nt"),
            mock.patch.object(
                click_windows.click_runner_transport,
                "install_runner_shell_renderer",
            ) as install,
            mock.patch.object(click_windows.click_hook, "main", return_value=17),
        ):
            self.assertEqual(click_windows.main(), 17)

        install.assert_called_once_with(click_windows._runner_shell_command)

    def test_gate_no_longer_exposes_runner_transport_privates(self) -> None:
        for name in (
            "MAX_RUNNER_TRANSPORT_BYTES",
            "WINDOWS_COMMAND_LINE_LIMIT",
            "_decode_runner_transport",
            "_encode_runner_transport",
            "_runner_shell_command",
            "_windows_launcher_path_is_safe",
            "_windows_shell_quote",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(click_gate, name))

    def test_oversized_json_report_uses_a_bounded_one_shot_file(self) -> None:
        calls: list[list[str]] = []

        def renderer(arguments: list[str]) -> str:
            calls.append(arguments)
            return "exit 2" if arguments[1] == "-c" else "bounded-report"

        with tempfile.TemporaryDirectory() as temporary:
            with (
                mock.patch.dict(os.environ, {"PLUGIN_DATA": temporary}),
                mock.patch.object(
                    click_gate.click_runner_transport,
                    "render_runner_shell_command",
                    side_effect=renderer,
                ),
            ):
                command = click_gate._json_report_command(
                    {"payload": "large-status-report"}
                )
            self.assertEqual(command, "bounded-report")
            self.assertEqual(calls[1][-2], "run-json-report")
            report_path = Path(calls[1][-1])
            self.assertTrue(report_path.is_file())

            stdout = io.StringIO()
            stderr = io.StringIO()
            runner_argv = [calls[1][1], *calls[1][2:]]
            with (
                mock.patch.object(sys, "argv", runner_argv),
                mock.patch.object(sys, "stdout", stdout),
                mock.patch.object(sys, "stderr", stderr),
            ):
                returncode = click_gate.main()

            self.assertEqual(returncode, 0, stderr.getvalue())
            self.assertEqual(
                json.loads(stdout.getvalue()),
                {"payload": "large-status-report"},
            )
            self.assertFalse(report_path.exists())


if __name__ == "__main__":
    unittest.main()
