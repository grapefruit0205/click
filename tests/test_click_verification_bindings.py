from __future__ import annotations

from dataclasses import fields
import hashlib
import inspect
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from hooks import click_verification as verification
from hooks import click_verification_bindings as bindings


class VerificationBindingStageTests(unittest.TestCase):
    def test_shared_executable_is_hashed_once_per_stage_and_fresh_next_stage(self):
        groups = {
            "argv:E_ONE": [{"argv": [sys.executable, "-m", "unittest", "one"]}],
            "argv:E_TWO": [{"argv": [sys.executable, "-m", "unittest", "two"]}],
        }
        cwd = Path.cwd()
        environment = bindings.verification_environment(cwd=cwd)
        with mock.patch.object(
            bindings, "hash_file_content", wraps=bindings.hash_file_content
        ) as digest:
            first = bindings.collect_group_bindings(
                groups, set(groups), cwd=cwd, environment=environment,
                digest_file=digest,
            )
            self.assertIsNotNone(first)
            per_stage = len(groups) if os.name == "nt" else 1
            self.assertEqual(digest.call_count, per_stage)
            second = bindings.collect_group_bindings(
                groups, set(groups), cwd=cwd, environment=environment,
                digest_file=digest,
            )
            self.assertEqual(first, second)
            self.assertEqual(digest.call_count, 2 * per_stage)

    def test_windows_equal_metadata_cannot_hide_an_executable_content_change(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "verifier"
            executable.write_bytes(b"first")
            stage = bindings.FileDigestStage()
            # On Windows creation time survives an in-place, same-size edit;
            # mtime can also be restored. Model that unchanged stat identity.
            identity = stage._identity(executable)
            with mock.patch.object(bindings.os, "name", "nt"), mock.patch.object(stage, "_identity", return_value=identity):
                first = stage(executable)
                executable.write_bytes(b"other")
                self.assertNotEqual(first, stage(executable))
                self.assertEqual(stage(executable), hashlib.sha256(b"other").hexdigest())

    def test_in_stage_replacement_invalidates_the_file_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "verifier"
            executable.write_bytes(b"first")
            stage = bindings.FileDigestStage()
            first = stage(executable)
            previous = executable.stat()
            executable.write_bytes(b"replacement")
            os.utime(executable, ns=(previous.st_atime_ns, previous.st_mtime_ns))
            self.assertNotEqual(first, stage(executable))
            self.assertEqual(stage(executable), hashlib.sha256(b"replacement").hexdigest())

    def test_write_during_hashing_does_not_establish_a_stage_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "verifier"
            executable.write_bytes(b"first")
            calls = []

            def digest(path):
                calls.append(path)
                value = bindings.hash_file_content(path)
                if len(calls) == 1:
                    path.write_bytes(b"replacement")
                return value

            stage = bindings.FileDigestStage(digest)
            self.assertEqual(stage(executable), "")
            self.assertEqual(stage(executable), hashlib.sha256(b"replacement").hexdigest())
            self.assertEqual(len(calls), 2)

    def test_typed_result_keeps_every_legacy_record_field_and_avoids_copying(self):
        signature = inspect.signature(verification.record_result)
        expected = set(signature.parameters) - {
            "path", "batch", "batch_digest", "runner_token", "git_capture",
        }
        self.assertEqual({item.name for item in fields(verification.VerificationRunResult)}, expected)
        records = [{"message": "bounded diagnostic"}]
        result = verification.VerificationRunResult(1, 0, diagnostic_records=records)
        with mock.patch.object(verification, "_record_verification_result", return_value=False) as record:
            self.assertFalse(verification.record_outcome(Path("state"), {}, "digest", "token", result))
        self.assertIs(record.call_args.kwargs["diagnostic_records"], records)
        self.assertEqual(record.call_args.kwargs["exit_code"], 1)


if __name__ == "__main__":
    unittest.main()
