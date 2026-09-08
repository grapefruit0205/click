from __future__ import annotations

from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
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
                    {"payload": "large-status-report-검증-상태"}
                )
            self.assertEqual(command, "bounded-report")
            self.assertEqual(calls[1][-2], "run-json-report")
            report_path = Path(calls[1][-1])
            self.assertTrue(report_path.is_file())

            stdout_bytes = io.BytesIO()
            stdout = io.TextIOWrapper(stdout_bytes, encoding="cp1252")
            stderr = io.StringIO()
            runner_argv = [calls[1][1], *calls[1][2:]]
            with (
                mock.patch.object(sys, "argv", runner_argv),
                mock.patch.object(sys, "stdout", stdout),
                mock.patch.object(sys, "stderr", stderr),
            ):
                returncode = click_gate.main()

            stdout.flush()
            self.assertEqual(returncode, 0, stderr.getvalue())
            self.assertEqual(
                json.loads(stdout_bytes.getvalue().decode("cp1252")),
                {"payload": "large-status-report-검증-상태"},
            )
            self.assertFalse(report_path.exists())

    def test_inline_json_report_is_ascii_safe_for_windows_consoles(self) -> None:
        calls: list[list[str]] = []

        def renderer(arguments: list[str]) -> str:
            calls.append(arguments)
            return "inline-report"

        with mock.patch.object(
            click_gate.click_runner_transport,
            "render_runner_shell_command",
            side_effect=renderer,
        ):
            command = click_gate._json_report_command({"payload": "검증 상태"})

        self.assertEqual(command, "inline-report")
        encoded_report = calls[0][-1]
        self.assertTrue(encoded_report.isascii())
        self.assertEqual(json.loads(encoded_report), {"payload": "검증 상태"})


class ClickJsonReportCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="click-json-cleanup-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "gate-state"
        self.root.mkdir()
        self.now = time.time()
        self.report_paths: list[Path] = []
        self.scandir = os.scandir
        self.visited: list[str] = []
        self.metadata: list[str] = []
        for patcher in (
            mock.patch.dict(os.environ, {"PLUGIN_DATA": temporary.name}),
            mock.patch.object(click_gate.time, "time", return_value=self.now),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _report(self, number: int, age: float = 3601) -> Path:
        path = self.root / f"{click_gate.JSON_REPORT_FILE_PREFIX}{number:032x}.json"
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (self.now - age, self.now - age))
        return path

    def _fallback(self) -> str:
        def renderer(arguments: list[str]) -> str:
            if arguments[1] == "-c":
                return "exit 2"
            self.report_paths.append(Path(arguments[-1]))
            return "bounded-report"

        with mock.patch.object(click_gate.click_runner_transport, "render_runner_shell_command", side_effect=renderer):
            return click_gate._json_report_command({"payload": "검증 상태"})

    def _ordered_scandir(self, names: list[str], *, limit: int | None = None, before_stat=None):
        owner = self

        class Entry:
            def __init__(self, entry):
                self.original = entry
                self.name, self.path = entry.name, entry.path

            def __getattr__(self, key):
                return getattr(self.original, key)

            def stat(self, *, follow_symlinks=True):
                owner.assertFalse(follow_symlinks)
                owner.metadata.append(self.name)
                if before_stat is not None:
                    result = before_stat(self.original)
                    if result is not None:
                        return result
                return self.original.stat(follow_symlinks=follow_symlinks)

        @contextmanager
        def scan(path):
            with owner.scandir(path) as entries:
                by_name = {entry.name: entry for entry in entries}
            ordered = [name for name in names if name in by_name]
            ordered.extend(name for name in by_name if name not in names)

            def iterate():
                for index, name in enumerate(ordered):
                    if limit is not None and index >= limit:
                        raise AssertionError("cleanup eagerly consumed beyond its deletion budget")
                    owner.visited.append(name)
                    yield Entry(by_name[name])

            yield iterate()

        return mock.patch.object(click_gate.os, "scandir", side_effect=scan)

    def test_inline_and_oversized_untransportable_reports_do_not_scan_or_write(self) -> None:
        with (
            mock.patch.object(click_gate.os, "scandir", side_effect=AssertionError("unexpected scan")),
            mock.patch.object(click_gate.click_state, "write_json", side_effect=AssertionError("unexpected write")),
            mock.patch.object(click_gate.click_runner_transport, "render_runner_shell_command", return_value="inline-report"),
        ):
            self.assertEqual(click_gate._json_report_command({"payload": "small"}), "inline-report")
        with (
            mock.patch.object(click_gate.os, "scandir", side_effect=AssertionError("unexpected scan")),
            mock.patch.object(click_gate.click_state, "write_json", side_effect=AssertionError("unexpected write")),
            mock.patch.object(click_gate.click_runner_transport, "render_runner_shell_command", return_value="exit 2"),
        ):
            self.assertEqual(click_gate._json_report_command({"payload": "x" * click_gate.JSON_REPORT_MAX_BYTES}), "exit 2")

    def test_empty_directory_preserves_new_report(self) -> None:
        self.assertEqual(self._fallback(), "bounded-report")
        self.assertEqual(
            [path.resolve() for path in self.root.iterdir()],
            [path.resolve() for path in self.report_paths],
        )

    def test_exact_writer_names_and_strict_ttl_preserve_other_state(self) -> None:
        stale, boundary, fresh = self._report(1), self._report(2, 3600), self._report(3, 3599)
        other_names = ["contract-state.json", ".click-json-report-legacy.json",
                       ".click-json-report-" + "A" * 32 + ".json",
                       ".click-json-report-" + "a" * 31 + ".json",
                       ".click-json-report-" + "a" * 32 + ".json.bak"]
        other = []
        for name in other_names:
            path = self.root / name
            path.write_text("{}", encoding="utf-8")
            os.utime(path, (self.now - 7200, self.now - 7200))
            other.append(path)
        directory = self.root / f"{click_gate.JSON_REPORT_FILE_PREFIX}{4:032x}.json"
        directory.mkdir()
        os.utime(directory, (self.now - 7200, self.now - 7200))
        self.assertEqual(self._fallback(), "bounded-report")
        self.assertFalse(stale.exists())
        self.assertTrue(boundary.is_file())
        self.assertTrue(fresh.is_file())
        self.assertTrue(all(path.is_file() for path in other))
        self.assertTrue(directory.is_dir())
        self.assertTrue(self.report_paths[-1].is_file())

    def test_fresh_prefix_does_not_starve_expired_suffix_across_calls(self) -> None:
        fresh = [self._report(index, 0) for index in range(128)]
        stale = [self._report(index) for index in range(128, 428)]
        names = [path.name for path in fresh + stale]
        remaining = []
        with self._ordered_scandir(names):
            for _ in range(3):
                self.assertEqual(self._fallback(), "bounded-report")
                remaining.append(sum(path.exists() for path in stale))
        self.assertEqual(remaining, [172, 44, 0])
        self.assertTrue(all(path.is_file() for path in fresh + self.report_paths))

    def test_streaming_stops_after_128_successful_deletions(self) -> None:
        reports = [self._report(index) for index in range(129)]
        with self._ordered_scandir([path.name for path in reports], limit=128):
            self.assertEqual(self._fallback(), "bounded-report")
        self.assertEqual(len(self.visited), 128)
        self.assertEqual(len(self.metadata), 128)
        self.assertEqual(sum(path.exists() for path in reports), 1)
        self.assertTrue(self.report_paths[-1].is_file())

    def test_many_nonmatching_entries_need_no_metadata_and_fresh_reports_are_preserved(self) -> None:
        other = [self.root / f"contract-{index}.json" for index in range(2048)]
        for path in other:
            path.write_text("{}", encoding="utf-8")
        fresh = [self._report(index, 0) for index in range(1024)]
        stale = self._report(1024)
        names = [path.name for path in other + fresh + [stale]]
        with self._ordered_scandir(names):
            self.assertEqual(self._fallback(), "bounded-report")
        self.assertEqual(len(self.visited), 3074)  # Existing files plus the new report.
        self.assertEqual(set(self.metadata), {path.name for path in fresh + [stale]})
        self.assertFalse(stale.exists())
        self.assertTrue(all(path.is_file() for path in other + fresh + self.report_paths))

    def test_symlinks_are_not_followed_or_removed(self) -> None:
        target = self.root.parent / "external.json"
        target.write_text("{}", encoding="utf-8")
        os.utime(target, (self.now - 7200, self.now - 7200))
        links = [self.root / f"{click_gate.JSON_REPORT_FILE_PREFIX}{index:032x}.json" for index in range(3)]
        try:
            links[0].symlink_to(target)
            links[1].symlink_to(self.root.parent, target_is_directory=True)
            links[2].symlink_to(self.root.parent / "missing.json")
        except OSError as error:
            self.skipTest(f"native symlink creation unavailable: {error}")
        with self._ordered_scandir([path.name for path in links]):
            self.assertEqual(self._fallback(), "bounded-report")
        self.assertTrue(all(path.is_symlink() for path in links))
        self.assertTrue(target.is_file())
        self.assertTrue(self.report_paths[-1].is_file())

    def test_concurrent_disappearance_and_stat_error_are_best_effort(self) -> None:
        disappeared, unavailable, stale = self._report(1), self._report(2), self._report(3)

        def before_stat(entry):
            if entry.name == disappeared.name:
                disappeared.unlink()
            if entry.name == unavailable.name:
                raise OSError("injected metadata failure")

        with self._ordered_scandir([disappeared.name, unavailable.name, stale.name], before_stat=before_stat):
            self.assertEqual(self._fallback(), "bounded-report")
        self.assertFalse(disappeared.exists())
        self.assertTrue(unavailable.is_file())
        self.assertFalse(stale.exists())
        self.assertTrue(self.report_paths[-1].is_file())

    def test_permission_failure_does_not_consume_deletion_budget(self) -> None:
        refused = self._report(0)
        stale = [self._report(index) for index in range(1, 130)]
        original = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == refused:
                raise PermissionError("injected open-file deletion refusal")
            return original(path, *args, **kwargs)

        with self._ordered_scandir([path.name for path in [refused] + stale]), mock.patch.object(Path, "unlink", unlink):
            self.assertEqual(self._fallback(), "bounded-report")
        self.assertTrue(refused.is_file())
        self.assertEqual(sum(path.exists() for path in stale), 1)
        self.assertEqual(len(self.visited), 129)
        self.assertTrue(self.report_paths[-1].is_file())

    def test_directory_open_and_iteration_errors_preserve_new_report(self) -> None:
        with mock.patch.object(click_gate.os, "scandir", side_effect=PermissionError("injected scan refusal")):
            self.assertEqual(self._fallback(), "bounded-report")

        @contextmanager
        def broken_iterator(path):
            def entries():
                raise OSError("injected enumeration failure")
                yield
            yield entries()

        with mock.patch.object(click_gate.os, "scandir", side_effect=broken_iterator):
            self.assertEqual(self._fallback(), "bounded-report")
        self.assertTrue(all(path.is_file() for path in self.report_paths))

    def test_path_replacement_with_directory_does_not_change_report_result(self) -> None:
        candidate = self._report(1)

        def before_stat(entry):
            if entry.name == candidate.name:
                metadata = entry.stat(follow_symlinks=False)
                candidate.unlink()
                candidate.mkdir()
                return metadata

        with self._ordered_scandir([candidate.name], before_stat=before_stat):
            self.assertEqual(self._fallback(), "bounded-report")
        self.assertTrue(candidate.is_dir())
        self.assertTrue(self.report_paths[-1].is_file())

    def test_own_new_report_is_skipped_even_if_its_timestamp_looks_expired(self) -> None:
        original = click_gate.click_state.write_json

        def write_old(path, report):
            original(path, report)
            os.utime(path, (self.now - 7200, self.now - 7200))

        with mock.patch.object(click_gate.click_state, "write_json", side_effect=write_old), self._ordered_scandir([]):
            self.assertEqual(self._fallback(), "bounded-report")
        self.assertEqual(self.metadata, [])
        self.assertTrue(self.report_paths[-1].is_file())

    def test_report_write_error_propagates_and_runner_render_error_removes_new_file(self) -> None:
        with mock.patch.object(click_gate.click_state, "write_json", side_effect=OSError("report write failed")):
            with self.assertRaisesRegex(OSError, "report write failed"):
                self._fallback()
        with mock.patch.object(click_gate.click_runner_transport, "render_runner_shell_command", return_value="exit 2"):
            self.assertEqual(click_gate._json_report_command({"payload": "small"}), "exit 2")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_legacy_prefix_report_remains_readable_once(self) -> None:
        legacy = self.root / ".click-json-report-legacy.json"
        legacy.write_text('{"legacy":true}', encoding="utf-8")
        os.utime(legacy, (self.now - 7200, self.now - 7200))
        self.assertEqual(self._fallback(), "bounded-report")
        self.assertTrue(legacy.is_file())
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", stderr):
            self.assertEqual(click_gate._run_json_report([str(legacy.resolve())]), 0)
            self.assertEqual(click_gate._run_json_report([str(legacy)]), 2)
        self.assertEqual(json.loads(stdout.getvalue()), {"legacy": True})
        self.assertFalse(legacy.exists())
        self.assertTrue(self.report_paths[-1].is_file())


if __name__ == "__main__":
    unittest.main()
