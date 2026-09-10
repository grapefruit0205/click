"""Candidate collectors preserve real Node/framework execution and deny authority."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from hooks import click_framework_observer as observer
from hooks import click_dependency_cache as cache
from hooks import click_observer_process_tree as processes
from hooks import click_observer_runtime as runtime


class ProcessTreeTests(unittest.TestCase):
    def test_group_exit_closes_idle_threads_but_not_forked_children(self):
        prefix = (
            b'10 execve("/node", [], []) = 0\n'
            b'10 clone(flags=CLONE_THREAD) = 11\n'
            b'11 clone(flags=CLONE_THREAD) = 12\n'
        )
        for terminating in (b'10', b'11'):
            with self.subTest(terminating=terminating):
                tree = processes.inspect(prefix + terminating + b' exit_group(0) = ?\n')
                self.assertTrue(tree.complete, tree.reasons)
                self.assertEqual(tree.completed, 3)
                self.assertEqual(tree.process_ids, (10,))
        tree = processes.inspect(prefix + b'10 clone(flags=SIGCHLD) = 13\n10 exit_group(0) = ?\n')
        self.assertFalse(tree.complete)
        self.assertIn("process-tree-incomplete", tree.reasons)

    def test_thread_exit_does_not_close_other_threads(self):
        tree = processes.inspect(
            b'10 execve("/node", [], []) = 0\n'
            b'10 clone(flags=CLONE_THREAD) = 11\n'
            b'11 exit(0) = ?\n'
        )
        self.assertFalse(tree.complete)
        self.assertEqual(tree.completed, 1)

    def test_observed_worker_signal_is_coverage_not_a_passing_result(self):
        tree = processes.inspect(
            b'10 execve("/node", [], []) = 0\n'
            b'10 clone(flags=SIGCHLD) = 11\n'
            b'11 +++ killed by SIGTERM +++\n'
            b'10 exit_group(0) = ?\n'
        )
        self.assertTrue(tree.complete, tree.reasons)
        self.assertEqual((tree.processes, tree.completed), (1, 2))
        self.assertFalse(cache.authoritative_dependency_observation_is_complete(tree))

    def test_interleaved_worker_calls_have_bound_birth_and_completion(self):
        value = processes.inspect(b'10 execve("/node", [], []) = 0\n10 clone(child_stack=NULL, flags=CLONE_THREAD <unfinished ...>\n11 exit(0) = ?\n10 <... clone resumed>) = 11\n10 exit_group(0) = ?\n')
        self.assertTrue(value.complete, value.reasons)
        self.assertEqual((value.processes, value.threads, value.completed), (0, 1, 2))
        self.assertNotIn(b"unfinished", value.trace)

    def test_missing_worker_end_unknown_pid_and_lost_resume_are_incomplete(self):
        prefix = b'10 execve("/node", [], []) = 0\n'
        for tail in (b'10 clone(flags=0) = 11\n10 exit_group(0) = ?\n',
                     b'99 exit(0) = ?\n10 exit_group(0) = ?\n',
                     b'10 <... openat resumed>) = 3\n10 exit_group(0) = ?\n'):
            with self.subTest(tail=tail):
                self.assertFalse(processes.inspect(prefix + tail).complete)

    def test_transport_completeness_never_grants_input_authority(self):
        for value in ({"process_tree_complete": True}, {"workers": {"complete": True}}, {"status": "complete"}):
            self.assertFalse(cache.authoritative_dependency_observation_is_complete(value))


@unittest.skipUnless(shutil.which("node"), "Node is unavailable")
class FrameworkCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="click-framework-candidates-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.capture = {}

    def execute(self, argv):
        def fallback():
            completed = observer.trace.click_process.run_argv_captured(argv, cwd=self.root)
            self.capture.update(status="complete", process=completed)
            return completed.returncode
        result = observer.run_command(
            argv, workspace=self.root, observation_root=self.root, environment=dict(os.environ),
            runtime_inputs=True,
            evidence_key="a" * 64, check_digest="b" * 64, mutation_revision=1,
            execute_unobserved=fallback, capture_output=self.capture,
            resolve_backend=lambda name, **kwargs: (shutil.which(name), ""),
            digest_file=runtime._digest_file,
        )
        self.assertTrue(observer.record_valid(result.record), result.record)
        self.assertFalse(result.record["reuse_authorized"])
        self.assertFalse(cache.authoritative_dependency_observation_is_complete(result.record))
        return result

    def test_node_worker_preserves_output_exit_and_input_candidates(self):
        (self.root / "data.json").write_text('{"value": 42}')
        (self.root / "worker.cjs").write_text("const {parentPort}=require('node:worker_threads'); const fs=require('node:fs'); parentPort.postMessage(JSON.parse(fs.readFileSync('data.json')).value);\n")
        (self.root / "test.cjs").write_text("const {test}=require('node:test'); const assert=require('node:assert/strict'); const {Worker}=require('node:worker_threads'); test('worker',()=>new Promise((resolve,reject)=>{const w=new Worker('./worker.cjs'); w.once('message', v=>{assert.equal(v,42);console.log('ONLY-ONCE');resolve()});w.on('error',reject)}));\n")
        result = self.execute([shutil.which("node"), "--test", "test.cjs"])
        self.assertEqual(result.exit_code, 0, self.capture)
        self.assertEqual(self.capture["process"].stdout.data.count(b"ONLY-ONCE"), 1)
        if result.record["capture"]["backend"]["name"] == "strace":
            paths = {item["path"] for item in result.record["capture"]["inputs"]}
            self.assertIn("data.json", paths)
            self.assertGreater(result.record["workers"]["threads"], 0)

    def test_framework_failure_is_executed_once_and_reported(self):
        (self.root / "package.json").write_text(json.dumps({"scripts": {"test": "node fail.cjs"}}))
        (self.root / "fail.cjs").write_text("console.error('ONLY-ONCE'); process.exitCode=7;\n")
        if not shutil.which("npm"):
            self.skipTest("npm unavailable")
        result = self.execute([shutil.which("npm"), "test", "--", "unchanged-argument"])
        self.assertEqual(result.exit_code, 7, self.capture)
        self.assertEqual(self.capture["process"].stderr.data.count(b"ONLY-ONCE"), 1)

    def framework_fixture(self, name, entry, arguments):
        source = Path(__file__).parent / "fixtures" / name
        if not (source / "node_modules" / entry).is_file():
            self.skipTest("pinned framework fixture is not installed")
        shutil.copytree(source, self.root, dirs_exist_ok=True, symlinks=True)
        return self.execute([shutil.which("node"), str(self.root / "node_modules" / entry), *arguments])

    def test_vitest_real_workers_collect_candidates_without_changing_pool(self):
        result = self.framework_fixture("vitest-v5", "vitest/vitest.mjs", ["run", "tests/unit/shared.test.js"])
        self.assertEqual(result.exit_code, 0, self.capture)
        self.assertEqual(result.record["framework"], "vitest")
        if result.record["capture"]["backend"]["name"] == "strace":
            paths = {item["path"] for item in result.record["capture"]["inputs"]}
            self.assertIn("tests/unit/shared.test.js", paths)
            self.assertIn("src/math.js", paths)

    def test_jest_real_workers_preserve_setup_transform_and_teardown(self):
        result = self.framework_fixture("jest-v30", "jest/bin/jest.js", ["--maxWorkers=2", "--runTestsByPath", "tests/unit/shared.test.cjs", "tests/types/value.test.ts"])
        self.assertEqual(result.exit_code, 0, self.capture)
        self.assertEqual(result.record["framework"], "jest")
        if result.record["capture"]["backend"]["name"] == "strace":
            paths = {item["path"] for item in result.record["capture"]["inputs"]}
            self.assertIn("tests/setup.cjs", paths)
            self.assertIn("tests/global-teardown.cjs", paths)
