from __future__ import annotations

import ast
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
    click_dependency_trace,
    click_inspection,
    click_process,
)


EVIDENCE_KEY = "a" * 64
CHECK_DIGEST = "b" * 64
BACKEND_DIGEST = "c" * 64


class _CompletedChild:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


class ClickDependencyTraceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()

    def trace_text(self, *lines: str) -> bytes:
        return ("\n".join(lines) + "\n").encode()

    def test_trace_module_is_a_non_authoritative_leaf(self) -> None:
        source = Path(click_dependency_trace.__file__).read_text(encoding="utf-8")
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
            "click_evidence",
            "click_gate",
            "click_receipt",
            "click_state",
            "click_verification",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(forbidden in name.split(".") for name in imported),
                    imported,
                )
        self.assertNotIn("dependency_observation(", source)

    def fake_trace(
        self,
        raw: bytes,
        *,
        exit_code: int = 0,
        capture_limit: int = click_dependency_trace.MAX_RAW_TRACE_BYTES,
        digest_values: tuple[str, str] = (BACKEND_DIGEST, BACKEND_DIGEST),
    ) -> tuple[click_dependency_trace.ShadowExecution, list[list[str]], list[int]]:
        spawned: list[list[str]] = []
        fallback_calls: list[int] = []
        digests = iter(digest_values)

        def spawn(argv: list[str], **_: object) -> _CompletedChild:
            spawned.append(list(argv))
            output_path = argv[argv.index("-o") + 1]
            with Path(output_path).open("wb", buffering=0) as output:
                output.write(raw)
            return _CompletedChild(exit_code)

        def fallback() -> int:
            fallback_calls.append(1)
            return 91

        result = click_dependency_trace.run_command(
            [sys.executable, "-c", "pass"],
            workspace=self.workspace,
            environment={"PATH": os.environ.get("PATH", os.defpath)},
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=4,
            execute_unobserved=fallback,
            resolve_backend=lambda *_args, **_kwargs: ("/usr/bin/strace", ""),
            digest_file=lambda _path: next(digests),
            probe_version=lambda _executable: "6.1",
            spawn_argv=spawn,
            system_name="Linux",
            already_traced=lambda: False,
            capture_limit=capture_limit,
        )
        return result, spawned, fallback_calls

    @unittest.skipUnless(platform.system() == "Linux", "strace parser is Linux-only")
    def test_parser_normalizes_repository_inputs_and_counts_external_paths(self) -> None:
        root = self.workspace.as_posix()
        parsed = click_dependency_trace.parse_strace(
            self.trace_text(
                '100 execve("/usr/bin/python3", ["python3"], 0x0) = 0',
                f'100 openat(AT_FDCWD<{root}>, "src/input.py", '
                f'O_RDONLY|O_CLOEXEC) = 3<{root}/src/input.py>',
                f'100 newfstatat(AT_FDCWD<{root}>, "missing.cfg", 0x0, 0) '
                '= -1 ENOENT (No such file or directory)',
                f'100 openat(AT_FDCWD<{root}>, "pkg", O_RDONLY|O_DIRECTORY) '
                f'= 4<{root}/pkg>',
                f'100 getdents64(4<{root}/pkg>, 0x0, 32768) = 24',
                f'100 openat(AT_FDCWD<{root}>, "private-link", '
                'O_RDONLY) = 5</outside/private-token>',
                '100 clone(child_stack=NULL, flags=SIGCHLD) = 101',
            ),
            workspace=self.workspace,
        )

        self.assertTrue(parsed.root_exec_observed)
        self.assertTrue(parsed.process_tree_complete)
        self.assertEqual(parsed.unresolved_event_count, 0)
        self.assertEqual(parsed.external_input_count, 2)
        self.assertEqual(parsed.child_process_count, 1)
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
                    "operations": ["enumerate", "metadata"],
                },
                {
                    "path": "src/input.py",
                    "kind": "file",
                    "operations": ["read"],
                },
            ),
        )
        absolute_paths = {row["path"] for row in parsed.absolute_inputs}
        self.assertIn(f"{root}/private-link", absolute_paths)
        self.assertIn("/outside/private-token", absolute_paths)

    def test_non_linux_executes_once_without_a_collector(self) -> None:
        calls: list[int] = []
        result = click_dependency_trace.run_command(
            ["check", "--flag"],
            workspace=self.workspace,
            environment={},
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=0,
            execute_unobserved=lambda: calls.append(1) or 7,
            resolve_backend=lambda *_args, **_kwargs: self.fail(
                "non-Linux must not resolve strace"
            ),
            system_name="Darwin",
            macos_privilege_probe=lambda: False,
        )

        self.assertEqual(result.exit_code, 7)
        self.assertEqual(calls, [1])
        self.assertEqual(result.record["status"], "unavailable")
        self.assertIsNone(result.record["backend"])
        self.assertFalse(result.record["authoritative"])
        self.assertFalse(result.record["reuse_authorized"])

    @unittest.skipUnless(platform.system() == "Linux", "strace collector is Linux-only")
    def test_collector_start_failure_falls_back_exactly_once(self) -> None:
        calls: list[int] = []
        trace_paths: list[Path] = []

        def fail_spawn(argv: list[str], **_: object) -> _CompletedChild:
            trace_paths.append(Path(argv[argv.index("-o") + 1]))
            raise OSError("denied")

        result = click_dependency_trace.run_command(
            ["check"],
            workspace=self.workspace,
            environment={},
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=2,
            execute_unobserved=lambda: calls.append(1) or 0,
            resolve_backend=lambda *_args, **_kwargs: ("/usr/bin/strace", ""),
            digest_file=lambda _path: BACKEND_DIGEST,
            probe_version=lambda _executable: "6.1",
            spawn_argv=fail_spawn,
            system_name="Linux",
            already_traced=lambda: False,
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(calls, [1])
        self.assertEqual(result.record["status"], "failed")
        self.assertEqual(result.record["backend"]["digest"], BACKEND_DIGEST)
        self.assertEqual(len(trace_paths), 1)
        self.assertFalse(trace_paths[0].exists())
        self.assertFalse(trace_paths[0].parent.exists())

    def test_denied_backend_probe_leaves_the_real_command_unchanged(self) -> None:
        calls: list[int] = []
        result = click_dependency_trace.run_command(
            ["check"],
            workspace=self.workspace,
            environment={},
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=3,
            execute_unobserved=lambda: calls.append(1) or 4,
            resolve_backend=lambda *_args, **_kwargs: ("/usr/bin/strace", ""),
            digest_file=lambda _path: BACKEND_DIGEST,
            probe_version=lambda _executable: "",
            spawn_argv=lambda *_args, **_kwargs: self.fail(
                "an unavailable backend must not be spawned"
            ),
            system_name="Linux",
            already_traced=lambda: False,
        )

        self.assertEqual(result.exit_code, 4)
        self.assertEqual(calls, [1])
        self.assertEqual(result.record["status"], "unavailable")
        self.assertIsNone(result.record["backend"])

    @unittest.skipUnless(platform.system() == "Linux", "strace collector is Linux-only")
    def test_keyboard_interrupt_terminates_traced_process_group(self) -> None:
        child = mock.Mock(spec=subprocess.Popen)
        child.wait.side_effect = KeyboardInterrupt
        terminated: list[object] = []

        def spawn(argv: list[str], **_: object) -> object:
            output_path = argv[argv.index("-o") + 1]
            with Path(output_path).open("wb", buffering=0) as output:
                output.write(
                    b'100 execve("/usr/bin/python3", ["python3"], 0x0) = 0\n'
                )
            return child

        result = click_dependency_trace.run_command(
            [sys.executable, "-c", "pass"],
            workspace=self.workspace,
            environment={"PATH": os.environ.get("PATH", os.defpath)},
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=4,
            execute_unobserved=lambda: self.fail(
                "an interrupted traced command must not execute again"
            ),
            resolve_backend=lambda *_args, **_kwargs: ("/usr/bin/strace", ""),
            digest_file=lambda _path: BACKEND_DIGEST,
            probe_version=lambda _executable: "6.1",
            spawn_argv=spawn,
            terminate_group=lambda current: terminated.append(current) or 130,
            system_name="Linux",
            already_traced=lambda: False,
        )

        self.assertEqual(result.exit_code, 130)
        self.assertEqual(result.record["status"], "failed")
        self.assertFalse(result.record["reuse_authorized"])
        self.assertEqual(terminated, [child])

    @unittest.skipUnless(platform.system() == "Linux", "strace collector is Linux-only")
    def test_started_collector_never_reruns_when_trace_is_incomplete(self) -> None:
        root = self.workspace.as_posix()
        result, spawned, fallback_calls = self.fake_trace(
            self.trace_text(
                f'100 openat(AT_FDCWD<{root}>, "input.txt", O_RDONLY) '
                f'= 3<{root}/input.txt>'
            )
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(spawned), 1)
        self.assertEqual(fallback_calls, [])
        self.assertEqual(result.record["status"], "failed")

    @unittest.skipUnless(platform.system() == "Linux", "strace collector is Linux-only")
    def test_trace_preserves_target_failure_and_drops_external_path_values(self) -> None:
        root = self.workspace.as_posix()
        result, spawned, fallback_calls = self.fake_trace(
            self.trace_text(
                '100 execve("/usr/bin/python3", ["python3"], 0x0) = 0',
                f'100 openat(AT_FDCWD<{root}>, "input.txt", O_RDONLY) '
                f'= 3<{root}/input.txt>',
                f'100 openat(AT_FDCWD<{root}>, "/outside/private-token", '
                'O_RDONLY) = 4</outside/private-token>',
            ),
            exit_code=7,
        )

        self.assertEqual(result.exit_code, 7)
        self.assertEqual(len(spawned), 1)
        self.assertEqual(fallback_calls, [])
        trace_path = Path(spawned[0][spawned[0].index("-o") + 1])
        self.assertFalse(trace_path.exists())
        self.assertFalse(trace_path.parent.exists())
        self.assertEqual(result.record["status"], "complete")
        self.assertEqual(result.record["external_input_count"], 2)
        self.assertIn(
            {"path": "input.txt", "kind": "file", "operations": ["read"]},
            result.record["inputs"],
        )
        encoded = json.dumps(result.record, sort_keys=True)
        self.assertNotIn("/outside/private-token", encoded)
        self.assertNotIn("0x0", encoded)
        self.assertNotIn("python3\"],", encoded)

    @unittest.skipUnless(platform.system() == "Linux", "strace collector is Linux-only")
    def test_bounded_capture_marks_an_otherwise_complete_trace_partial(self) -> None:
        root_exec = (
            '100 execve("/usr/bin/python3", ["python3"], 0x0) = 0\n'
        ).encode()
        result, _, fallback_calls = self.fake_trace(
            root_exec + b"x" * 512,
            capture_limit=len(root_exec),
        )

        self.assertEqual(fallback_calls, [])
        self.assertEqual(result.record["status"], "partial")
        self.assertGreaterEqual(result.record["unresolved_event_count"], 1)
        self.assertFalse(result.record["process_tree_complete"])

    @unittest.skipUnless(platform.system() == "Linux", "strace collector is Linux-only")
    def test_backend_digest_drift_discards_trace_without_rerunning_target(self) -> None:
        result, spawned, fallback_calls = self.fake_trace(
            self.trace_text(
                '100 execve("/usr/bin/python3", ["python3"], 0x0) = 0'
            ),
            digest_values=(BACKEND_DIGEST, "d" * 64),
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(spawned), 1)
        self.assertEqual(fallback_calls, [])
        self.assertEqual(result.record["status"], "unavailable")
        self.assertIsNone(result.record["backend"])

    def test_combined_record_marks_unexecuted_group_members_partial(self) -> None:
        complete = click_dependency_cache.shadow_observer_record(
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=5,
            backend_name="strace",
            backend_version="6.1",
            backend_digest=BACKEND_DIGEST,
            inputs=[
                {"path": "src/input.py", "kind": "file", "operations": ["read"]}
            ],
            command_duration_ms=10,
            observer_overhead_ms=2,
        )

        combined = click_dependency_trace.combine_records(
            [complete],
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=5,
            unexecuted_checks=1,
        )

        self.assertIsNotNone(combined)
        assert combined is not None
        self.assertEqual(combined["status"], "partial")
        self.assertEqual(combined["unresolved_event_count"], 1)
        self.assertFalse(combined["authoritative"])
        self.assertFalse(combined["reuse_authorized"])

    def test_lifecycle_state_keeps_only_latest_valid_record_per_source(self) -> None:
        first = click_dependency_cache.shadow_observer_record(
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=1,
            backend_name=None,
            status="unavailable",
            process_tree_complete=False,
        )
        second = click_dependency_cache.shadow_observer_record(
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=2,
            backend_name=None,
            status="unavailable",
            process_tree_complete=False,
        )
        verification: dict[str, object] = {}

        self.assertEqual(
            click_dependency_trace.store_records(
                verification, {EVIDENCE_KEY: first, "bad": {}}
            ),
            1,
        )
        self.assertEqual(
            click_dependency_trace.store_records(
                verification, {EVIDENCE_KEY: second}
            ),
            1,
        )
        records = click_dependency_trace.records_from_verification(verification)
        self.assertEqual(records[EVIDENCE_KEY]["binding"]["mutation_revision"], 2)
        self.assertTrue(
            click_dependency_trace.state_is_valid(
                verification[click_dependency_trace.SHADOW_STATE_FIELD]
            )
        )

    def test_advisory_is_concise_and_contains_no_observed_paths(self) -> None:
        record = click_dependency_cache.shadow_observer_record(
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=1,
            backend_name="strace",
            backend_version="6.1",
            backend_digest=BACKEND_DIGEST,
            inputs=[
                {"path": "private/name.py", "kind": "file", "operations": ["read"]}
            ],
        )

        message = click_dependency_trace.advisory(record)

        self.assertIn("inputs=1", message)
        self.assertNotIn("private/name.py", message)

    def test_version_probe_is_bounded_by_timeout(self) -> None:
        completed = mock.Mock(
            returncode=0,
            stdout=b"strace -- version 6.1\n",
            stderr=b"",
        )
        capability = mock.Mock(returncode=0)
        click_dependency_trace.probe_strace_version.cache_clear()
        self.addCleanup(click_dependency_trace.probe_strace_version.cache_clear)
        with mock.patch.object(
            click_dependency_trace.click_process,
            "run_argv",
            side_effect=[completed, capability],
        ) as run:
            version = click_dependency_trace.probe_strace_version("/usr/bin/strace")

        self.assertEqual(version, "6.1")
        self.assertEqual(run.call_count, 2)
        run.assert_any_call(
            ["/usr/bin/strace", "--version"],
            stdout=click_dependency_trace.subprocess.PIPE,
            stderr=click_dependency_trace.subprocess.PIPE,
            timeout=2.0,
        )
        run.assert_any_call(
            [
                "/usr/bin/strace",
                "-D",
                "-f",
                "-qq",
                "-e",
                "trace=none",
                "-o",
                os.devnull,
                "--",
                "/usr/bin/strace",
                "--version",
            ],
            stdout=click_dependency_trace.subprocess.DEVNULL,
            stderr=click_dependency_trace.subprocess.DEVNULL,
            timeout=3.0,
        )

    @unittest.skipUnless(platform.system() == "Linux", "strace backend is Linux-only")
    def test_linux_strace_smoke_reads_input_without_workspace_artifacts(self) -> None:
        if click_dependency_trace._already_traced():
            self.skipTest("nested ptrace is intentionally disabled")
        executable, error = click_inspection.resolve_read_only_executable(
            "strace", workspace=self.workspace
        )
        if error or executable is None:
            self.skipTest("trusted strace is unavailable")
        if not click_dependency_trace.probe_strace_version(executable):
            self.skipTest("strace version probe is unavailable")
        input_path = self.workspace / "input.txt"
        input_path.write_text("observed\n", encoding="utf-8")
        environment = dict(os.environ)
        fallback_calls: list[int] = []

        def fallback() -> int:
            fallback_calls.append(1)
            return int(
                click_process.run_argv(
                    [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; Path('input.txt').read_bytes()",
                    ],
                    cwd=self.workspace,
                    env=environment,
                ).returncode
            )

        result = click_dependency_trace.run_command(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('input.txt').read_bytes()",
            ],
            workspace=self.workspace,
            environment=environment,
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=0,
            execute_unobserved=fallback,
            resolve_backend=click_inspection.resolve_read_only_executable,
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(fallback_calls, [])
        self.assertIn(result.record["status"], {"complete", "partial"})
        self.assertTrue(
            any(item["path"] == "input.txt" for item in result.record["inputs"])
        )
        self.assertEqual(
            sorted(path.name for path in self.workspace.iterdir()), ["input.txt"]
        )


if __name__ == "__main__":
    unittest.main()
