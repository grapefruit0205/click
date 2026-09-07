from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from hooks import click_authoritative_observer as authoritative
from hooks import click_observer_runtime as runtime
from hooks import click_test_inventory as inventory

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED = sys.platform == "linux" and sys.implementation.name == "cpython" and sys.version_info[:3] == (3, 12, 3)


class _PortableSnapshot:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def records(self, inputs):
        self.inputs = inputs
        return [
            {
                "root": "project",
                "path": "input.txt",
                "kind": "file",
                "operations": ["read"],
                "digest": "f" * 64,
            }
        ]


class PortableAuthoritativeAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="click-portable-authority-")
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name).resolve()
        self.input = self.project / "input.txt"
        self.input.write_text("value\n", encoding="utf-8")
        self.runtime = {
            "version": 1,
            "profile": runtime.DARWIN_PROFILE,
            "artifact_id": "click-native-observer-portable",
            "artifact_digest": "a" * 64,
            "source_digest": "b" * 64,
            "compiler_digest": "c" * 64,
            "backend": {
                "name": "fs_usage",
                "version": "15.0",
                "digest": "d" * 64,
            },
        }
        self.context = {
            field: "e" * 64 for field in authoritative.FULL_BINDING_FIELDS
        }
        self.context["mutation_revision"] = 1

    def parsed(self):
        return authoritative.click_observer_macos.ParsedTrace(
            inputs=(
                {"path": "input.txt", "kind": "file", "operations": ["read"]},
            ),
            external_input_count=0,
            unresolved_event_count=0,
            child_process_count=0,
            process_tree_complete=True,
            root_exec_observed=True,
            absolute_inputs=(
                {
                    "path": str(self.input),
                    "kind": "file",
                    "operations": ["read"],
                },
            ),
        )

    def windows_runtime(self):
        value = json.loads(json.dumps(self.runtime))
        value["profile"] = runtime.WINDOWS_PROFILE
        value["backend"] = {
            "name": "windows-etw",
            "version": "10.0.26100",
            "digest": authoritative.click_observer_windows.combined_backend_digest(
                "1" * 64, "2" * 64
            ),
        }
        return value

    def windows_parsed(self):
        return authoritative.click_observer_windows.ParsedTrace(
            inputs=(
                {"path": "input.txt", "kind": "file", "operations": ["read"]},
            ),
            external_input_count=0,
            unresolved_event_count=0,
            child_process_count=0,
            process_tree_complete=True,
            root_exec_observed=True,
            absolute_inputs=(
                {
                    "path": str(self.input),
                    "kind": "file",
                    "operations": ["read"],
                },
            ),
        )

    def test_native_file_event_uses_the_snapshotted_object_kind(self) -> None:
        directory = self.project / "package"
        directory.mkdir()
        snapshot = _PortableSnapshot()

        authoritative._snapshot_records(
            snapshot,
            [
                {
                    "path": str(directory),
                    "kind": "file",
                    "operations": ["metadata"],
                }
            ],
            observation_root=self.project,
        )

        self.assertEqual(snapshot.inputs, {str(directory): {"metadata"}})

    def run_windows(self, *, native_events=None, collector_effect=None):
        fallback = mock.Mock(return_value=91)
        collected = authoritative.click_observer_windows.CollectedExecution(
            0, (b"process", b"file"), False, False, True, 42, 2, 1, True
        )
        collector = mock.Mock(
            return_value=collected,
            side_effect=collector_effect,
        )

        def channel():
            events = set(
                authoritative.NORMAL_NATIVE_EVENTS
                if native_events is None
                else native_events
            )
            event_path = self.project / "native-events.log"
            event_path.write_bytes(
                "".join(f"{event}\n" for event in sorted(events)).encode("ascii")
            )
            read_descriptor = os.open(
                event_path, os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
            )
            write_descriptor = os.open(os.devnull, os.O_WRONLY)
            return read_descriptor, write_descriptor, 731

        def digest(path):
            return "1" * 64 if "logman" in str(path).lower() else "2" * 64

        spawn = mock.Mock()
        child = mock.Mock(pid=42)
        child.poll.return_value = 0
        child.wait.return_value = 0
        spawn.return_value = child
        windows_runtime = self.windows_runtime()
        with (
            mock.patch.object(
                runtime,
                "validate",
                return_value=Path("/tmp/click-native-observer-portable/_click_observer_companion.pyd"),
            ),
            mock.patch.object(
                authoritative.click_observer_windows,
                "native_windows_tool",
                return_value=True,
            ),
            mock.patch.object(
                authoritative.click_observer_windows,
                "probe_windows_version",
                return_value="10.0.26100",
            ),
            mock.patch.object(
                authoritative.click_observer_windows,
                "collect_command",
                collector,
            ),
            mock.patch.object(
                authoritative.click_observer_windows,
                "parse_windows_etw",
                return_value=self.windows_parsed(),
            ),
            mock.patch.object(
                authoritative.click_observer_windows,
                "windows_device_paths",
                return_value={},
            ),
            mock.patch.object(
                authoritative.click_observation_inputs,
                "InputSnapshot",
                _PortableSnapshot,
            ),
            mock.patch.object(authoritative, "_windows_native_channel", channel),
        ):
            result = authoritative.run_command(
                [sys.executable, "-m", "unittest", "-q"],
                workspace=self.project,
                observation_root=self.project,
                environment={
                    "PYTHONHASHSEED": "0",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": "C:\\existing",
                },
                binding_context=self.context,
                runtime=windows_runtime,
                runner_token=self.id(),
                execute_unobserved=fallback,
                resolve_backend=lambda name, **_kwargs: (
                    f"C:\\Windows\\System32\\{name}.exe",
                    "",
                ),
                digest_file=digest,
                spawn_argv=spawn,
            )
        return result, fallback, collector, spawn

    def run_darwin(self, *, native_events=None, collector_effect=None):
        fallback = mock.Mock(return_value=91)
        collected = authoritative.click_observer_macos.CollectedExecution(
            0, b"trace", False, False, True, 12, 2, True
        )
        collector = mock.Mock(
            return_value=collected,
            side_effect=collector_effect,
        )
        with (
            mock.patch.object(
                runtime,
                "validate",
                return_value=Path(
                    "/tmp/click-native-observer-portable/_click_observer_companion.so"
                ),
            ),
            mock.patch.object(authoritative.click_observer_macos, "native_fs_usage", return_value=True),
            mock.patch.object(authoritative.click_observer_macos, "probe_macos_version", return_value="15.0"),
            mock.patch.object(
                authoritative.click_observer_macos,
                "collect_command",
                collector,
            ),
            mock.patch.object(authoritative.click_observer_macos, "parse_fs_usage", return_value=self.parsed()),
            mock.patch.object(authoritative.click_observation_inputs, "InputSnapshot", _PortableSnapshot),
            mock.patch.object(
                authoritative,
                "_read_native_events",
                return_value=(
                    set(
                        authoritative.NORMAL_NATIVE_EVENTS
                        if native_events is None
                        else native_events
                    ),
                    False,
                ),
            ),
        ):
            result = authoritative.run_command(
                [sys.executable, "-m", "unittest", "-q"],
                workspace=self.project,
                observation_root=self.project,
                environment={"PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"},
                binding_context=self.context,
                runtime=self.runtime,
                runner_token=self.id(),
                execute_unobserved=fallback,
                resolve_backend=lambda *_args, **_kwargs: ("/usr/bin/fs_usage", ""),
                digest_file=lambda _path: "d" * 64,
            )
        return result, fallback, collector

    @unittest.skipIf(os.name == "nt", "Darwin FIFO adapter requires POSIX")
    def test_darwin_adapter_issues_a_profile_bound_signed_observation(self) -> None:
        result, fallback, collector = self.run_darwin()
        observation = result.envelope["observation"]
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(observation["status"], "complete")
        self.assertEqual(observation["profile"], runtime.DARWIN_PROFILE)
        self.assertEqual(observation["backend"], self.runtime["backend"])
        self.assertEqual(observation["paths"], ["input.txt"])
        fallback.assert_not_called()
        observed_environment = collector.call_args.kwargs["environment"]
        self.assertEqual(
            observed_environment["CLICK_NATIVE_OBSERVER_BOOTSTRAP"],
            "/tmp/click-native-observer-portable",
        )
        self.assertTrue(
            observed_environment["PYTHONPATH"].startswith(
                "/tmp/click-native-observer-portable"
            )
        )
        self.assertNotIn("DYLD_INSERT_LIBRARIES", observed_environment)
        self.assertIsNotNone(
            authoritative.verified_observation(
                result.envelope,
                secret=self.id(),
                expected_binding=self.context,
            )
        )

    @unittest.skipIf(os.name == "nt", "Darwin FIFO adapter requires POSIX")
    def test_darwin_native_reason_is_non_reusable_without_rerunning(self) -> None:
        result, fallback, _ = self.run_darwin(
            native_events={
                *authoritative.NORMAL_NATIVE_EVENTS,
                "time-random-input",
            }
        )
        observation = result.envelope["observation"]
        self.assertEqual(observation["status"], "failed")
        self.assertEqual(observation["ineligibility_reasons"], ["time-random-input"])
        fallback.assert_not_called()

    @unittest.skipIf(os.name == "nt", "Darwin FIFO adapter requires POSIX")
    def test_darwin_ambiguous_collector_exception_never_reruns_target(self) -> None:
        result, fallback, _ = self.run_darwin(
            collector_effect=RuntimeError("collector boundary failed")
        )
        self.assertEqual(result.exit_code, 127)
        self.assertEqual(result.envelope["observation"]["status"], "failed")
        self.assertIn(
            "capture-failed",
            result.envelope["observation"]["ineligibility_reasons"],
        )
        fallback.assert_not_called()

    def test_profile_backend_pairs_are_strict(self) -> None:
        result, _, _ = self.run_darwin()
        changed = json.loads(json.dumps(result.envelope["observation"]))
        changed["backend"]["name"] = "strace"
        self.assertFalse(
            authoritative.click_dependency_cache.authoritative_dependency_observation_is_valid(
                changed
            )
        )

    def test_windows_adapter_issues_a_profile_bound_signed_observation(self) -> None:
        result, fallback, collector, _ = self.run_windows()
        observation = result.envelope["observation"]
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(observation["status"], "complete")
        self.assertEqual(observation["profile"], runtime.WINDOWS_PROFILE)
        self.assertEqual(observation["backend"], self.windows_runtime()["backend"])
        self.assertEqual(observation["paths"], ["input.txt"])
        fallback.assert_not_called()
        observed_environment = collector.call_args.kwargs["environment"]
        self.assertEqual(observed_environment["CLICK_NATIVE_OBSERVER_HANDLE"], "731")
        self.assertEqual(
            observed_environment["CLICK_NATIVE_OBSERVER_ORIGINAL_PYTHONPATH"],
            "C:\\existing",
        )
        self.assertTrue(
            observed_environment["PYTHONPATH"].startswith(
                "/tmp/click-native-observer-portable"
            )
        )
        self.assertIsNotNone(
            authoritative.verified_observation(
                result.envelope,
                secret=self.id(),
                expected_binding=self.context,
            )
        )

    def test_windows_native_reason_is_non_reusable_without_rerunning(self) -> None:
        result, fallback, _, _ = self.run_windows(
            native_events={
                *authoritative.NORMAL_NATIVE_EVENTS,
                "time-random-input",
            }
        )
        self.assertEqual(result.envelope["observation"]["status"], "failed")
        self.assertEqual(
            result.envelope["observation"]["ineligibility_reasons"],
            ["time-random-input"],
        )
        fallback.assert_not_called()

    def test_windows_missing_native_events_fail_closed_without_rerunning(self) -> None:
        result, fallback, _, _ = self.run_windows(native_events=set())
        self.assertEqual(result.envelope["observation"]["status"], "failed")
        self.assertIn(
            "event-loss",
            result.envelope["observation"]["ineligibility_reasons"],
        )
        fallback.assert_not_called()

    def test_windows_complete_observation_uses_profile_currentness(self) -> None:
        result, _, _, _ = self.run_windows()
        observation = result.envelope["observation"]
        current_binding = {
            field: observation["binding"][field]
            for field in authoritative.click_dependency_cache.AUTHORITATIVE_CURRENT_BINDING_FIELDS
        }
        with mock.patch.object(
            authoritative.click_observation_inputs,
            "records_current",
            return_value=True,
        ) as records_current:
            self.assertTrue(
                authoritative.click_dependency_cache.authoritative_dependency_observation_matches(
                    observation,
                    project=self.project,
                    runtime=self.windows_runtime(),
                    binding=current_binding,
                )
            )
        self.assertEqual(
            records_current.call_args.kwargs["profile"], runtime.WINDOWS_PROFILE
        )

    def test_windows_collector_exception_after_spawn_never_reruns_target(self) -> None:
        def crash_after_spawn(_argv, **kwargs):
            kwargs["spawn_argv"](
                [sys.executable, "-m", "unittest", "-q"],
                cwd=self.project,
                env={},
            )
            raise RuntimeError("collector crashed after target spawn")

        result, fallback, _, spawn = self.run_windows(
            collector_effect=crash_after_spawn
        )
        self.assertEqual(result.envelope["observation"]["status"], "failed")
        self.assertIn(
            "capture-failed",
            result.envelope["observation"]["ineligibility_reasons"],
        )
        fallback.assert_not_called()
        self.assertFalse(spawn.call_args.kwargs["close_fds"])


class PortableObserverRuntimeContractTests(unittest.TestCase):
    def state(self, profile: str, name: str, version: str) -> dict:
        return {
            "version": 1,
            "profile": profile,
            "artifact_id": "click-native-observer-portable",
            "artifact_digest": "a" * 64,
            "source_digest": "b" * 64,
            "compiler_digest": "c" * 64,
            "backend": {
                "name": name,
                "version": version,
                "digest": "d" * 64,
            },
        }

    def test_runtime_state_accepts_only_the_profile_backend_pair(self) -> None:
        states = (
            self.state(runtime.PROFILE, "strace", "6.8"),
            self.state(runtime.DARWIN_PROFILE, "fs_usage", "15.0"),
            self.state(runtime.WINDOWS_PROFILE, "windows-etw", "10.0.26100"),
        )
        for state in states:
            with self.subTest(profile=state["profile"]):
                self.assertTrue(runtime.state_is_valid(state))
        changed = json.loads(json.dumps(states[-1]))
        changed["backend"]["name"] = "fs_usage"
        self.assertFalse(runtime.state_is_valid(changed))
        changed = json.loads(json.dumps(states[1]))
        changed["backend"]["version"] = "bad version"
        self.assertFalse(runtime.state_is_valid(changed))

    def test_portable_build_commands_select_native_artifact_shapes(self) -> None:
        darwin_artifact = Path("/tmp/_click_observer_companion.so")
        darwin = runtime._build_command(
            runtime.DARWIN_PROFILE,
            compiler=Path("/usr/bin/cc"),
            include=Path("/headers"),
            source=Path("/source.c"),
            artifact=darwin_artifact,
        )
        self.assertIn("-bundle", darwin)
        self.assertIn("-Wl,-undefined,dynamic_lookup", darwin)
        self.assertEqual(darwin[-1], str(darwin_artifact))

        with tempfile.TemporaryDirectory(prefix="click-win-build-shape-") as raw:
            prefix = Path(raw)
            library = prefix / "libs" / "python312.lib"
            library.parent.mkdir()
            library.write_bytes(b"placeholder")
            with mock.patch.object(runtime.sys, "base_prefix", str(prefix)):
                windows_artifact = Path("C:/tmp/_click_observer_companion.pyd")
                windows = runtime._build_command(
                    runtime.WINDOWS_PROFILE,
                    compiler=Path("C:/tools/cl.exe"),
                    include=Path("C:/Python/include"),
                    source=Path("C:/source.c"),
                    artifact=windows_artifact,
                )
        self.assertIn("/LD", windows)
        self.assertIn("/W4", windows)
        self.assertNotIn("/WX", windows)
        self.assertIn(f"/Fe:{windows_artifact}", windows)
        self.assertEqual(windows[-1], str(library))
        with tempfile.TemporaryDirectory(prefix="click-portable-venv-roots-") as raw:
            directory = Path(raw)
            project = directory / "project"
            artifact = directory / "artifact"
            environment = directory / "venv"
            project.mkdir()
            artifact.mkdir()
            environment.mkdir()
            with (
                mock.patch.object(
                    authoritative.click_observation_inputs.sys,
                    "prefix",
                    str(environment),
                ),
                mock.patch.object(
                    authoritative.click_observation_inputs.sys,
                    "base_prefix",
                    str(directory / "base"),
                ),
                mock.patch.object(
                    authoritative.click_observation_inputs.sys,
                    "platform",
                    "darwin",
                ),
                mock.patch.object(
                    authoritative.click_observation_inputs.sysconfig,
                    "get_path",
                    return_value=str(directory / "runtime"),
                ),
                mock.patch.object(
                    authoritative.click_observation_inputs.site,
                    "getusersitepackages",
                    return_value=str(directory / "user-packages"),
                ),
            ):
                roots = authoritative.click_observation_inputs._portable_runtime_roots(
                    project, artifact
                )
            self.assertEqual(
                roots["environment-prefix"], environment.resolve()
            )
            self.assertEqual(roots["host-root"], Path("/"))

    def test_portable_source_identity_binds_the_bootstrap_and_profile(self) -> None:
        native_only = runtime._source_digest(runtime.PROFILE)
        darwin = runtime._source_digest(runtime.DARWIN_PROFILE)
        windows = runtime._source_digest(runtime.WINDOWS_PROFILE)
        self.assertRegex(native_only, r"^[0-9a-f]{64}$")
        self.assertRegex(darwin, r"^[0-9a-f]{64}$")
        self.assertRegex(windows, r"^[0-9a-f]{64}$")
        self.assertEqual(len({native_only, darwin, windows}), 3)

    def test_input_snapshot_read_flags_are_portable(self) -> None:
        module = authoritative.click_observation_inputs
        expected = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
        with (
            mock.patch.object(module.os, "O_CLOEXEC", 0, create=True),
            mock.patch.object(module.os, "O_NOFOLLOW", 0, create=True),
        ):
            self.assertEqual(module._read_flags(), expected)

    def test_input_snapshot_keys_use_platform_case_normalization(self) -> None:
        module = authoritative.click_observation_inputs
        value = Path("/TMP/Runtime.py")
        absolute = Path(os.path.normpath(os.path.abspath(value)))
        expected = str(absolute.parent.resolve(strict=False) / absolute.name).lower()
        with mock.patch.object(
            module.os.path,
            "normcase",
            side_effect=lambda value: value.lower(),
        ):
            self.assertEqual(module._path_key(value), expected)

    def test_windows_bootstrap_restores_and_chains_existing_customization(self) -> None:
        with tempfile.TemporaryDirectory(prefix="click-bootstrap-") as raw:
            root = Path(raw)
            artifact = root / "artifact"
            original = root / "original"
            artifact.mkdir()
            original.mkdir()
            marker = root / "marker.txt"
            shutil.copy2(
                ROOT / "hooks" / "click_observer_bootstrap.py",
                artifact / "sitecustomize.py",
            )
            (artifact / "_click_observer_companion.py").write_text(
                "import os\n"
                "marker = os.environ['CLICK_BOOTSTRAP_TEST_MARKER']\n"
                "with open(marker, 'a', encoding='utf-8') as stream:\n"
                "    stream.write('companion\\n')\n"
                "def mark_startup_unsafe():\n"
                "    with open(marker, 'a', encoding='utf-8') as stream:\n"
                "        stream.write('unsafe\\n')\n",
                encoding="utf-8",
            )
            (original / "sitecustomize.py").write_text(
                "import os\n"
                "with open(os.environ['CLICK_BOOTSTRAP_TEST_MARKER'], 'a', encoding='utf-8') as stream:\n"
                "    stream.write('original\\n')\n",
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment.update(
                {
                    "CLICK_BOOTSTRAP_TEST_MARKER": str(marker),
                    "CLICK_NATIVE_OBSERVER_BOOTSTRAP": str(artifact),
                    "CLICK_NATIVE_OBSERVER_ORIGINAL_PYTHONPATH": str(original),
                    "CLICK_NATIVE_OBSERVER_PYTHONPATH_PRESENT": "1",
                    "CLICK_NATIVE_OBSERVER_ROOT": str(root),
                    "PYTHONNOUSERSITE": "1",
                    "PYTHONPATH": os.pathsep.join((str(artifact), str(original))),
                }
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import os; print(os.environ.get('PYTHONPATH', 'missing'))",
                ],
                env=environment,
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), str(original))
            self.assertEqual(
                marker.read_text(encoding="utf-8").splitlines(),
                ["companion", "unsafe", "original"],
            )


@unittest.skipUnless(SUPPORTED, "initial Linux CPython 3.12.3 native profile")
class NativeObservationCompanionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = runtime.prepare(ROOT)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(Path(cls.build["artifact"]).parent)

    def run_observed(self, body: str, *, expected=0):
        with tempfile.TemporaryDirectory(prefix="click-native-proof-") as tmp:
            directory = Path(tmp)
            project = directory / "project"
            project.mkdir()
            test = project / "test_example.py"
            test.write_text("import unittest\nclass Example(unittest.TestCase):\n    def test_case(self):\n" +
                            "".join("        " + line + "\n" for line in body.splitlines()))
            channel = directory / "events"
            os.mkfifo(channel, 0o600)
            descriptor = os.open(channel, os.O_RDWR | os.O_NONBLOCK)
            try:
                environment = {key: value for key, value in os.environ.items()
                               if key not in ("LD_PRELOAD", "CLICK_NATIVE_OBSERVER_CHANNEL")}
                environment.update(PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")
                backend = inventory.trusted_executable("strace", project)
                command = [str(backend), "-f", "-qq", "-e", "trace=%file,%process,%clock,getrandom",
                           "-o", os.devnull, "-E", "LD_PRELOAD=" + self.build["artifact"],
                           "-E", "CLICK_NATIVE_OBSERVER_CHANNEL=" + str(channel),
                           "-E", "CLICK_NATIVE_OBSERVER_ROOT=" + str(project),
                           "--", sys.executable, "-m", "unittest", "discover", "-s", ".", "-q"]
                result = subprocess.run(command, cwd=project, env=environment, capture_output=True,
                                        text=True, timeout=30)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                messages = os.read(descriptor, 8192).decode("ascii").splitlines()
                self.assertIn("native-started", messages)
                self.assertIn("native-profile-ready", messages)
                self.assertIn("native-finished", messages)
                self.assertEqual(len(messages), len(set(messages)))
                return messages
            finally:
                os.close(descriptor)

    def test_real_native_companion_preserves_the_original_unittest_run(self):
        messages = self.run_observed("self.assertEqual(2 + 2, 4)")
        self.assertEqual(set(messages), {"native-started", "native-profile-ready", "native-finished"})
        self.assertFalse(self.build["authority"])

    def test_vdso_time_api_is_detected_even_without_a_clock_syscall(self):
        messages = self.run_observed("import time\nself.assertGreater(time.time(), 0)")
        self.assertIn("time-random-input", messages)

    def test_profile_removal_is_a_sticky_incompleteness_reason(self):
        messages = self.run_observed("import sys\nsys.setprofile(None)\nself.assertTrue(True)")
        self.assertIn("observer-introspection-or-tampering", messages)

    def test_native_memory_access_is_incomplete_without_changing_test_result(self):
        messages = self.run_observed("import ctypes\nself.assertTrue(ctypes.CDLL(None))")
        self.assertIn("external-or-native-input", messages)

    def test_observer_does_not_turn_a_test_failure_into_a_success(self):
        self.run_observed("self.fail('intentional fixture failure')", expected=1)

    def test_test_result_timing_introspection_requires_review(self):
        messages = self.run_observed("self.assertIsNotNone(self._outcome.result)")
        self.assertIn("dynamic-runtime-introspection", messages)

    def test_nested_runner_timing_is_not_the_outer_framework_reporting(self):
        messages = self.run_observed("unittest.TextTestRunner().run(unittest.TestSuite())")
        self.assertIn("time-random-input", messages)

    def test_new_python_thread_makes_the_observation_incomplete(self):
        messages = self.run_observed(
            "import threading\n"
            "worker = threading.Thread(target=lambda: None)\n"
            "worker.start()\n"
            "worker.join()"
        )
        self.assertIn("concurrent-execution-unsupported", messages)
