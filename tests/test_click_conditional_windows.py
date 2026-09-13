"""The ETW projection for conditional JS observation, on synthetic captures.

The Kernel-Process and Kernel-File XML that ``tracerpt.exe`` produces is
platform-neutral text, so the lifecycle and projection rules are exercised
here on every OS; the real capture runs in ``RealWindowsConditionalTests``
on a Windows host with Node 22.23.2.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from hooks import click_conditional_observer as conditional
from hooks import click_conditional_windows as windows_projection
from hooks import click_node_observer as node
from hooks import click_observer_windows

PROCESS = click_observer_windows.PROCESS_PROVIDER
FILE = click_observer_windows.FILE_PROVIDER
ROOT_PID = 4100
PROJECT = "C:\\work\\app"
COLLECTOR = "C:\\Users\\dev\\AppData\\Local\\Temp\\click-node-inputs-abc"
DEVICE_PATHS = {"\\device\\harddiskvolume3": "C:", "\\device\\harddiskvolume4": "D:", "\\device\\harddiskvolume5": "E:"}
_VOLUMES = {drive: volume for volume, drive in DEVICE_PATHS.items()}


def _event(provider, event_id, pid, data):
    rendered = "".join(f'<Data Name="{key}">{value}</Data>' for key, value in data.items())
    return (
        "<Event><System>"
        f'<Provider Name="{provider}"/><EventID>{event_id}</EventID>'
        f'<Execution ProcessID="{pid}" ThreadID="1"/>'
        f"</System><EventData>{rendered}</EventData></Event>"
    )


def _document(*events, lost=0):
    lost_element = f"<EventsLost>{lost}</EventsLost>" if lost else ""
    return f"<Events>{lost_element}{''.join(events)}</Events>".encode()


def start(pid, parent, image="\\Device\\HarddiskVolume3\\Program Files\\nodejs\\node.exe"):
    return _event(PROCESS, 1, pid, {"ProcessID": pid, "ParentProcessID": parent, "ImageName": image})


def stop(pid):
    return _event(PROCESS, 2, pid, {"ProcessID": pid, "ExitCode": 0})


def create(pid, path, event_id=12):
    return _event(FILE, event_id, pid, {"FileName": path, "FileObject": f"0x{abs(hash(path)) % 0xFFFF:04X}"})


def read(pid, path):
    return _event(FILE, 15, pid, {"FileName": path})


def query(pid, path):
    return _event(FILE, 22, pid, {"FileName": path})


def enumerate(pid, path):
    return _event(FILE, 20, pid, {"FileName": path})


def device(path):
    """Spell a drive path the way the file provider reports it."""
    volume = _VOLUMES.get(path[:2].upper(), "\\device\\harddiskvolume3")
    return "\\Device\\HarddiskVolume" + volume[-1] + path[2:]


class WindowsProcessTreeTests(unittest.TestCase):
    def test_root_and_descendants_must_start_and_stop_inside_the_capture(self):
        complete = _document(start(ROOT_PID, 4000), stop(ROOT_PID))
        tree = windows_projection.inspect_tree((complete, _document()), root_pid=ROOT_PID)
        self.assertTrue(tree.complete, tree.reasons)
        self.assertEqual((tree.processes, tree.threads, tree.completed, tree.process_ids), (0, 0, 1, (ROOT_PID,)))

        child_alive = _document(start(ROOT_PID, 4000), start(4200, ROOT_PID), stop(ROOT_PID))
        tree = windows_projection.inspect_tree((child_alive,), root_pid=ROOT_PID)
        self.assertFalse(tree.complete)
        self.assertIn("process-tree-incomplete", tree.reasons)
        self.assertEqual((tree.processes, tree.process_ids), (1, (ROOT_PID, 4200)))

        child_done = _document(start(ROOT_PID, 4000), start(4200, ROOT_PID), stop(4200), stop(ROOT_PID))
        tree = windows_projection.inspect_tree((child_done,), root_pid=ROOT_PID)
        self.assertTrue(tree.complete, tree.reasons)
        self.assertEqual(tree.completed, 2)

        # An unrelated process is neither admitted nor required to stop.
        stranger = _document(start(ROOT_PID, 4000), start(9000, 1), stop(ROOT_PID))
        self.assertTrue(windows_projection.inspect_tree((stranger,), root_pid=ROOT_PID).complete)

    def test_loss_and_an_unobserved_root_fail_closed(self):
        lost = _document(start(ROOT_PID, 4000), stop(ROOT_PID), lost=1)
        self.assertIn("event-loss", windows_projection.inspect_tree((lost,), root_pid=ROOT_PID).reasons)
        truncated = windows_projection.inspect_tree((_document(start(ROOT_PID, 4000), stop(ROOT_PID)),), root_pid=ROOT_PID, truncated=True)
        self.assertIn("event-loss", truncated.reasons)
        unbound = windows_projection.inspect_tree((_document(stop(ROOT_PID)),), root_pid=ROOT_PID)
        self.assertIn("root-execution-unbound", unbound.reasons)
        for bad in (0, -1, True, None):
            self.assertFalse(windows_projection.inspect_tree((_document(),), root_pid=bad).complete)
        duplicate = _document(start(ROOT_PID, 4000), start(ROOT_PID, 4000), stop(ROOT_PID))
        self.assertIn("ambiguous-process-identity", windows_projection.inspect_tree((duplicate,), root_pid=ROOT_PID).reasons)


class WindowsProjectionTests(unittest.TestCase):
    """Projection rules on a synthetic capture.

    On Windows the projection reads presence from the host to bind absent
    probes as ``missing``, so the synthetic project and its externals are
    real files there; elsewhere the spelled paths are enough.
    """

    def setUp(self):
        if os.name == "nt":
            self.temporary = tempfile.TemporaryDirectory(prefix="click-etw-")
            self.addCleanup(self.temporary.cleanup)
            base = Path(self.temporary.name).resolve()
            self.project = str(base / "app")
            for relative in ("entry.cjs", "config.json", "fixtures/one.txt", "src/main.js"):
                target = Path(self.project) / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("x", encoding="utf-8")
            (base / "tool.json").write_text("{}", encoding="utf-8")
            self.tool = str(base / "tool.json")
            self.runtime = "C:\\Windows\\System32\\kernel32.dll"
            self.ancestor = str(base)
            self.absent = str(base / "not-there.dll")
        else:
            self.project = PROJECT
            self.tool = "C:\\Users\\dev\\.config\\tool.json"
            self.runtime = "C:\\Program Files\\nodejs\\node.exe"
            self.ancestor = "C:\\work"
            self.absent = "C:\\Program Files\\nodejs\\not-there.dll"

    def project_rows(self, *file_events, process_events=None, root_pid=ROOT_PID, truncated=False):
        process_document = _document(*(process_events or (start(ROOT_PID, 4000), stop(ROOT_PID))))
        file_document = _document(*file_events)
        return windows_projection.project_capture(
            (process_document, file_document), project=self.project, cwd=self.project, directory=COLLECTOR,
            root_pid=root_pid, device_paths=DEVICE_PATHS, truncated=truncated,
        )

    project_capture = project_rows

    def bootstrap(self):
        return [
            read(ROOT_PID, device(self.runtime)),
            query(ROOT_PID, device("C:\\Users\\dev\\AppData\\Roaming\\npm")),
            enumerate(ROOT_PID, device("C:\\Program Files\\nodejs\\node_modules")),
            read(ROOT_PID, device("C:\\Windows\\System32\\ntdll.dll")),
            create(ROOT_PID, device(COLLECTOR + f"\\endpoint-{ROOT_PID}.json")),
            create(ROOT_PID, device(COLLECTOR + f"\\endpoint-{ROOT_PID}.json"), event_id=30),
            query(ROOT_PID, device(COLLECTOR + f"\\ready-{ROOT_PID}")),
            create(ROOT_PID, device(COLLECTOR + f"\\started-{ROOT_PID}")),
            create(ROOT_PID, device(COLLECTOR + f"\\started-{ROOT_PID}"), event_id=30),
        ]

    def test_reads_after_the_acknowledgement_are_bound_and_the_runtime_probes_are_not(self):
        projection = self.project_rows(
            *self.bootstrap(),
            read(ROOT_PID, device(self.project + "\\entry.cjs")),
            query(ROOT_PID, device(self.project + "\\config.json")),
            enumerate(ROOT_PID, device(self.project + "\\fixtures")),
            query(ROOT_PID, device(self.ancestor)),
            read(ROOT_PID, device("C:\\Windows\\System32\\bcrypt.dll")),
            query(ROOT_PID, device(self.tool)),
            read(ROOT_PID, device(self.absent)),
            read(ROOT_PID, "\\Device\\Null"),
        )
        self.assertIsNotNone(projection)
        self.assertEqual(
            projection["inputs"],
            [
                {"path": "config.json", "kind": "file", "operations": ["metadata"]},
                {"path": "entry.cjs", "kind": "file", "operations": ["read"]},
                {"path": "fixtures/", "kind": "directory", "operations": ["enumerate"]},
            ],
        )
        spell = windows_projection.external_row_path
        expected = sorted(
            [
                # Bootstrap reads stay bound by content; the runtime's own
                # metadata probes before the acknowledgement do not.
                {"path": spell(self.runtime), "kind": "file", "operations": ["read"]},
                {"path": spell(self.tool), "kind": "file", "operations": ["metadata"]},
                {"path": "C:/Windows/System32/bcrypt.dll", "kind": "file", "operations": ["read"]},
                {"path": "C:/Windows/System32/ntdll.dll", "kind": "file", "operations": ["read"]},
                # A probed path that is not there binds as one that must stay absent.
                {"path": spell(self.absent), "kind": "missing" if os.name == "nt" else "file", "operations": ["read"]},
            ],
            key=lambda row: row["path"],
        )
        self.assertEqual(projection["external"], expected)
        for row in projection["external"]:
            self.assertTrue(conditional.external_row_valid(row) if os.name == "nt" else row["path"][1] == ":", row)
        self.assertNotIn(spell(self.ancestor), [row["path"] for row in projection["external"]])

    def test_the_collectors_transport_and_created_outputs_are_not_inputs(self):
        projection = self.project_rows(
            *self.bootstrap(),
            create(ROOT_PID, device(COLLECTOR + f"\\worker-{ROOT_PID}-1")),
            create(ROOT_PID, device(self.project + "\\out\\report.json")),
            create(ROOT_PID, device(self.project + "\\out\\report.json"), event_id=30),
            read(ROOT_PID, device(self.project + "\\src\\main.js")),
        )
        self.assertEqual([row["path"] for row in projection["inputs"]], ["src/main.js"])
        self.assertEqual(sorted(row["path"] for row in projection["external"]),
                         sorted([windows_projection.external_row_path(self.runtime), "C:/Windows/System32/ntdll.dll"]))

    def test_dynamic_objects_after_the_acknowledgement_leave_no_projection(self):
        self.assertIsNone(self.project_rows(*self.bootstrap(), create(ROOT_PID, "\\Device\\NamedPipe\\uv\\pipe-1")))
        self.assertIsNone(self.project_rows(*self.bootstrap(), read(ROOT_PID, "\\Device\\ConDrv\\Console")))
        # Before the acknowledgement the same objects are the runtime's own.
        self.assertIsNotNone(self.project_rows(create(ROOT_PID, "\\Device\\ConDrv\\Console"), *self.bootstrap()))

    def test_missing_acknowledgement_loss_or_children_leave_no_projection(self):
        events = self.bootstrap()
        without_marker = [event for event in events if "started-" not in event]
        self.assertIsNone(self.project_rows(*without_marker, read(ROOT_PID, device(self.project + "\\entry.cjs"))))
        self.assertIsNone(self.project_rows(*events, truncated=True))
        self.assertIsNone(self.project_rows(
            *events, process_events=(start(ROOT_PID, 4000), start(4200, ROOT_PID), stop(4200), stop(ROOT_PID))))
        self.assertIsNone(self.project_rows(*events, root_pid=-1))

    def test_an_object_the_session_never_saw_opened_is_the_stdio_transport(self):
        # libuv queries the inherited stdout pipe; the session saw no Create
        # for it, so the event carries no path. It is set aside, not counted.
        unkeyed = _event(FILE, 22, ROOT_PID, {"FileObject": "0xFFFF0001"})
        projection = self.project_rows(*self.bootstrap(), unkeyed, read(ROOT_PID, device(self.project + "\\entry.cjs")))
        self.assertIsNotNone(projection)
        self.assertEqual([row["path"] for row in projection["inputs"]], ["entry.cjs"])

    def test_unmapped_volume_paths_after_the_boundary_are_unresolved(self):
        # A read from a volume the device map does not know cannot be bound.
        projection = self.project_rows(*self.bootstrap(), read(ROOT_PID, "\\Device\\HarddiskVolume9\\data\\input.bin"))
        self.assertIsNone(projection)


class WindowsRowSpellingTests(unittest.TestCase):
    def test_external_rows_are_spelled_with_forward_slashes_and_a_drive(self):
        self.assertEqual(windows_projection.external_row_path("C:\\Windows\\System32\\ntdll.dll"),
                         "C:/Windows/System32/ntdll.dll")
        row = {"path": "C:/Windows/System32/ntdll.dll", "kind": "file", "operations": ["read"]}
        posix_row = {"path": "/usr/lib/libc.so.6", "kind": "file", "operations": ["read"]}
        if os.name == "nt":
            self.assertTrue(conditional.external_row_valid(row))
            self.assertFalse(conditional.external_row_valid(posix_row))
            for bad in ("C:\\Windows\\x", "C://Windows/x", "Windows/x", "C:/Windows/../x", "C:/"):
                self.assertFalse(conditional.external_row_valid({**row, "path": bad}), bad)
        else:
            self.assertTrue(conditional.external_row_valid(posix_row))
            self.assertFalse(conditional.external_row_valid(row))

    @unittest.skipUnless(os.name == "nt", "Windows external snapshots")
    def test_external_snapshot_binds_windows_paths_by_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "input.txt"
            target.write_text("one", encoding="utf-8")
            row = {"path": windows_projection.external_row_path(str(target.resolve())), "kind": "file", "operations": ["read"]}
            first = conditional.external_snapshot([row])
            self.assertTrue(conditional.external_valid(first))
            self.assertEqual(conditional.external_snapshot([row]), first)
            target.write_text("two", encoding="utf-8")
            self.assertNotEqual(conditional.external_snapshot([row]), first)
            with self.assertRaises(ValueError):
                conditional.external_snapshot([{**row, "path": "\\\\.\\pipe\\x"}])


@unittest.skipUnless(sys.platform == "win32" and shutil.which("node"), "native Windows Node observation")
class RealWindowsConditionalTests(unittest.TestCase):
    """The real ETW capture with the real inspector collector on one Node run."""

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if subprocess.check_output([cls.node, "--version"]).strip().decode() != node.VERSION:
            raise unittest.SkipTest("versioned Node input profile unavailable")

    def test_a_node_script_projects_its_inputs_and_reuses_them(self):
        from hooks import click_dependency_trace as trace
        from hooks import click_framework_observer as framework
        from hooks import click_inspection
        from hooks import click_process

        with tempfile.TemporaryDirectory(prefix="click-win-conditional-") as temporary:
            root = Path(temporary).resolve()
            (root / "input.txt").write_text("ok", encoding="utf-8")
            (root / "check.cjs").write_text(
                "const fs=require('node:fs');"
                "if(fs.readFileSync('input.txt','utf8')!=='ok')process.exitCode=1;"
                "console.log('RAN-ONCE');\n", encoding="utf-8")
            argv = [self.node, str(root / "check.cjs")]
            environment = dict(os.environ)

            def fallback():
                return int(click_process.run_argv(argv, cwd=root, env=environment).returncode)

            # First, the raw capture: the same collector and backend the
            # framework observer uses, with the documents kept for diagnosis.
            debug_path = Path(temporary) / "observer-debug.json"
            collector = node.Collector(argv, {**environment, "CLICK_NODE_OBSERVER_DEBUG": str(debug_path)}, root)
            self.assertIsNotNone(collector.child, collector.record)
            directory = collector.location.name
            captured = {}

            def observed(raw, **options):
                captured["documents"] = raw
                captured["options"] = options

            try:
                shadow = trace.run_command(
                    argv, workspace=root, observation_root=root, environment=collector.environment,
                    evidence_key="e" * 64, check_digest="c" * 64, mutation_revision=0,
                    execute_unobserved=fallback, resolve_backend=click_inspection.resolve_read_only_executable,
                    digest_file=node.digest_file, capture_output={}, process_observer=observed,
                )
                tree = windows_projection.inspect_tree(
                    captured.get("documents", ()), root_pid=captured.get("options", {}).get("root_pid", -1),
                    truncated=bool(captured.get("options", {}).get("truncated", False)))
                # Project while the collector directory still exists: its
                # 8.3 spelling is expanded through the file system.
                diagnostics = {}
                projection = windows_projection.project_capture(
                    captured.get("documents", ()), project=root, cwd=root, directory=directory,
                    root_pid=captured.get("options", {}).get("root_pid", -1),
                    device_paths=captured.get("options", {}).get("device_paths"),
                    truncated=bool(captured.get("options", {}).get("truncated", False)), diagnostics=diagnostics,
                ) if "documents" in captured else None
                runtime = collector.finish(tree)
            finally:
                collector.close()
            self.assertEqual(shadow.exit_code, 0, shadow.record)
            self.assertIn("documents", captured, "the Windows backend did not hand over its capture")
            try:
                observer_debug = json.loads(debug_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                observer_debug = None
            summary = {"shadow": {k: shadow.record.get(k) for k in ("status", "ineligibility_reasons", "unresolved_event_count", "process_tree_complete", "child_process_count")},
                       "runtime": {k: runtime.get(k) for k in ("status", "reasons", "capture_complete", "sessions", "contexts", "installed", "completed", "workers", "counts")},
                       "values": {k: v["count"] for k, v in runtime.get("values", {}).items() if v.get("count")},
                       "observer_debug": observer_debug, "diagnostics": diagnostics, "projection": projection}
            print("CLICK-WINDOWS-CONDITIONAL " + json.dumps(summary, ensure_ascii=True)[:20000])
            lost = set(diagnostics.get("tree", {}).get("reasons", [])) & {"event-loss", "process-tree-incomplete"}
            if lost:
                self.skipTest(f"native backend lost events on this host: {sorted(lost)}")
            self.assertTrue(runtime["capture_complete"], json.dumps(runtime))
            self.assertIsNotNone(projection, json.dumps(summary)[:6000])
            rows = {row["path"]: row for row in projection["inputs"]}
            for name in ("input.txt", "check.cjs"):
                self.assertIn(name, rows, projection["inputs"])
                self.assertEqual(rows[name]["kind"], "file")
                self.assertIn("read", rows[name]["operations"])
            self.assertTrue(all(conditional.external_row_valid(row) for row in projection["external"]), projection["external"])
            self.assertFalse([row for row in projection["external"] if "click-node-inputs" in row["path"]], projection["external"])

            # Then the framework observer end to end: learn, bind, invalidate.
            def run(previous=None, context=None):
                return framework.run_command(
                    argv, workspace=root, observation_root=root, environment=environment,
                    evidence_key="e" * 64, check_digest="c" * 64, mutation_revision=0,
                    execute_unobserved=fallback, resolve_backend=click_inspection.resolve_read_only_executable,
                    digest_file=node.digest_file, capture_output={}, previous=previous,
                    conditional_context=context, conditional_secret="s" * 32,
                )

            learning = run()
            record = learning.record
            self.assertEqual(learning.exit_code, 0, json.dumps(record)[:2000])
            self.assertIsNotNone(record["conditional_capture"], json.dumps(record)[:3000])
            self.assertTrue(conditional.eligible_record(record), json.dumps(record)[:3000])
            from hooks import click_dependency_cache as dependencies

            context = {key: "a" * 64 for key in dependencies.AUTHORITATIVE_BINDING_FIELDS if key != "execution_digest"}
            context["mutation_revision"] = 0
            binding = run(previous=record, context=context)
            self.assertEqual(binding.exit_code, 0)
            self.assertIsNotNone(binding.envelope, binding.refusal or json.dumps(binding.record)[:3000])
            observation = conditional.verify(binding.envelope, secret="s" * 32, expected_binding=context)
            self.assertIsNotNone(observation)
            current_binding = {key: context[key] for key in dependencies.AUTHORITATIVE_CURRENT_BINDING_FIELDS}
            self.assertTrue(conditional.matches(observation, project=root, binding=current_binding))
            (root / "input.txt").write_text("changed", encoding="utf-8")
            self.assertFalse(conditional.current(root, observation["inputs"]))


@unittest.skipUnless(sys.platform == "win32" and shutil.which("node"), "native Windows Node observation")
class RealWindowsRandomStateTests(unittest.TestCase):
    """With the native state reader built on Windows, consumed random state binds."""

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if subprocess.check_output([cls.node, "--version"]).strip().decode() != node.VERSION:
            raise unittest.SkipTest("versioned Node input profile unavailable")
        cls.companion = node.click_node_state.prepare(Path(cls.node).resolve(), Path.cwd(), dict(os.environ))
        if not cls.companion:
            raise unittest.SkipTest("exact-ABI native state companion unavailable (MSVC developer prompt and Node headers required)")

    def test_math_random_state_is_recorded_and_the_run_stays_eligible(self):
        with tempfile.TemporaryDirectory(prefix="click-win-random-") as temporary:
            root = Path(temporary).resolve()
            (root / "check.cjs").write_text(
                "const view=new Int32Array(new SharedArrayBuffer(4));"
                "const r=Math.random();Atomics.add(view,0,7);"
                "console.log('RANDOM', r>=0 && r<1, Atomics.load(view,0));\n", encoding="utf-8")
            argv = [self.node, str(root / "check.cjs")]
            collector = node.Collector(argv, dict(os.environ), root)
            self.assertIsNotNone(collector.child, collector.record)
            self.assertIsNotNone(collector.state_companion, "companion not attached")
            try:
                result = subprocess.run(argv, cwd=root, env=collector.environment, capture_output=True, timeout=60)
                record = collector.finish()
            finally:
                collector.close()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b"RANDOM true 7", result.stdout)
            self.assertTrue(record["state_companion_digest"], record)
            self.assertEqual(record["values"]["math-random"]["count"], 1, record)
            self.assertEqual(record["values"]["math-random"]["state_count"], 1, record)
            self.assertEqual(record["values"]["atomics-add"]["state_count"], 1, record)
            self.assertNotIn("input-native-state-unavailable", record["reasons"])
            self.assertNotIn("input-values-unavailable", record["reasons"])


if __name__ == "__main__":
    unittest.main()
