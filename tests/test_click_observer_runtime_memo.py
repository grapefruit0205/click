"""Runtime identity work is memoized per process while file identities hold."""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from hooks import click_observer_runtime as runtime
from hooks import click_test_inventory as inventory


class RuntimeIdentityMemoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        runtime._IDENTITY_MEMO.clear()
        self.addCleanup(runtime._IDENTITY_MEMO.clear)

    def _rewrite(self, path: Path, content: bytes) -> None:
        # A rewrite changes size or mtime_ns (and usually the inode), so the
        # memo key differs even on filesystems with coarse timestamps.
        path.write_bytes(content)
        stamp = time.time_ns() + 2_000_000_000
        os.utime(path, ns=(stamp, stamp))

    def test_a_file_is_hashed_once_until_its_identity_changes(self) -> None:
        target = self.root / "artifact.so"
        target.write_bytes(b"first")
        original = Path.open
        with mock.patch.object(Path, "open", autospec=True, side_effect=original) as opened:
            first = runtime._digest_file(target)
            second = runtime._digest_file(target)
            self.assertEqual(first, second)
            self.assertEqual(opened.call_count, 1)
            self._rewrite(target, b"second")
            opened.reset_mock()  # the rewrite itself opened the file
            third = runtime._digest_file(target)
            self.assertNotEqual(first, third)
            self.assertEqual(opened.call_count, 1)

    def test_the_tracer_is_probed_once_per_executable_identity(self) -> None:
        tracer = self.root / "strace"
        tracer.write_bytes(b"#!/bin/sh\n")
        tracer.chmod(0o755)
        completed = subprocess.CompletedProcess([], 0, stdout=b"strace -- version 6.8\n", stderr=b"")
        with mock.patch.object(inventory, "trusted_executable", return_value=tracer), \
             mock.patch.object(runtime.subprocess, "run", return_value=completed) as probes:
            executable, identity = runtime._strace_identity(self.root)
            self.assertEqual(executable, tracer)
            self.assertEqual(identity["version"], "6.8")
            self.assertEqual(probes.call_count, 2)  # --version and the -f capability probe
            _, again = runtime._strace_identity(self.root)
            self.assertEqual(again, identity)
            self.assertEqual(probes.call_count, 2)
            # Returned copies do not alias the memo.
            again["version"] = "tampered"
            self.assertEqual(runtime._strace_identity(self.root)[1]["version"], "6.8")
            self._rewrite(tracer, b"#!/bin/sh\nexit 0\n")
            runtime._strace_identity(self.root)
            self.assertEqual(probes.call_count, 4)

    def test_a_failed_probe_is_not_remembered(self) -> None:
        tracer = self.root / "strace"
        tracer.write_bytes(b"#!/bin/sh\n")
        failing = subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"")
        with mock.patch.object(inventory, "trusted_executable", return_value=tracer), \
             mock.patch.object(runtime.subprocess, "run", return_value=failing) as probes:
            with self.assertRaises(inventory.AnalysisError):
                runtime._strace_identity(self.root)
            with self.assertRaises(inventory.AnalysisError):
                runtime._strace_identity(self.root)
            self.assertEqual(probes.call_count, 2)

    def test_the_project_root_is_resolved_once_per_process(self) -> None:
        project = self.root / "project"
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        with mock.patch.object(inventory, "project_root", wraps=inventory.project_root) as resolved:
            first = runtime._project_root(project)
            second = runtime._project_root(project)
            self.assertEqual(first, second)
            self.assertEqual(first, project.resolve())
            self.assertEqual(resolved.call_count, 1)

    def test_the_memo_stays_bounded(self) -> None:
        with mock.patch.object(runtime, "_IDENTITY_MEMO_LIMIT", 3):
            for index in range(5):
                runtime._remember(("probe", index), index)
            self.assertLessEqual(len(runtime._IDENTITY_MEMO), 3)


if __name__ == "__main__":
    unittest.main()
