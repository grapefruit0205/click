from __future__ import annotations

from dataclasses import fields
import hashlib
import inspect
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest import mock

from hooks import click_verification as verification
from hooks import click_verification_bindings as bindings
from hooks import click_verification_inputs as inputs


class VerificationBindingStageTests(unittest.TestCase):
    def test_shared_inputs_keep_per_group_membership_and_fresh_boundary_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            config = cwd / "options.json"
            config.write_text('{"value":1}')
            groups = {str(index): [{"argv": [sys.executable, "-m", "unittest", "one"],
                                   "inputs": ["*.json"]}] for index in range(3)}
            environment = bindings.verification_environment(cwd=cwd)
            with mock.patch.object(bindings, "hash_file_content", wraps=bindings.hash_file_content) as digest:
                first = bindings.collect_group_bindings(groups, set(groups), cwd=cwd,
                                                        environment=environment, digest_file=digest)
                self.assertIsNotNone(first)
                self.assertEqual(digest.call_count, 2 * (len(groups) if os.name == "nt" else 1))
                for key, checks in groups.items():
                    self.assertEqual(first[0][key], bindings.verification_environment_digest(
                        checks, cwd=cwd, environment=environment))
                original = config.stat()
                config.write_text('{"value":2}')
                os.utime(config, ns=(original.st_atime_ns, original.st_mtime_ns))
                second = bindings.collect_group_bindings(groups, set(groups), cwd=cwd,
                                                         environment=environment, digest_file=digest)
            self.assertNotEqual(first[0], second[0])
            stage = bindings.FileDigestStage()
            baseline = inputs.snapshot(cwd, ("*.json",), file_content_digest=stage)
            added = cwd / "new.json"
            added.write_text("{}")
            self.assertNotEqual(baseline["digest"], inputs.snapshot(
                cwd, ("*.json",), file_content_digest=stage)["digest"])
            added.unlink()
            self.assertEqual(baseline, inputs.snapshot(cwd, ("*.json",), file_content_digest=stage))
            config.rename(cwd / "moved.json")
            self.assertNotEqual(baseline["digest"], inputs.snapshot(
                cwd, ("*.json",), file_content_digest=stage)["digest"])

    def test_snapshot_keeps_legacy_digest_and_observes_dirty_index_and_new_files(self):
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            def git(*arguments):
                return subprocess.run(["git", *arguments], cwd=cwd, capture_output=True, check=True)
            git("init", "-q")
            def legacy_snapshot():
                hasher = hashlib.sha256()
                has_head = bindings.git_capture(cwd, ["rev-parse", "--verify", "HEAD"]) is not None
                if has_head:
                    tree = bindings.git_capture(cwd, ["rev-parse", "HEAD^{tree}"])
                    hasher.update(len(tree).to_bytes(8, "big")); hasher.update(tree)
                commands = [["diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--"]] if has_head else [
                    ["diff", "--binary", "--no-ext-diff", "--no-textconv", "--cached", "--"],
                    ["diff", "--binary", "--no-ext-diff", "--no-textconv", "--"],
                ]
                for command in commands:
                    diff = bindings.git_capture(cwd, command)
                    hasher.update(len(diff).to_bytes(8, "big")); hasher.update(diff)
                untracked = bindings.git_capture(cwd, ["ls-files", "--others", "--exclude-standard", "-z"])
                for relative in sorted(os.fsdecode(value) for value in untracked.split(b"\0") if value):
                    bindings.hash_workspace_path(hasher, cwd, relative)
                return hasher.hexdigest()
            self.assertEqual(bindings.git_workspace_snapshot(cwd)["digest"], legacy_snapshot())
            source = cwd / "source.txt"
            source.write_text("original")
            git("add", ".")
            git("-c", "user.name=Click Tests", "-c", "user.email=click-tests@example.invalid", "commit", "-qm", "fixture")
            with mock.patch.object(bindings, "git_capture", wraps=bindings.git_capture) as capture:
                clean = bindings.git_workspace_snapshot(cwd)
            self.assertEqual(capture.call_count, 4)
            self.assertEqual(clean["digest"], legacy_snapshot())
            source.write_text("staged")
            git("add", "source.txt")
            source.write_text("worktree")
            (cwd / "new.txt").write_text("untracked")
            dirty = bindings.git_workspace_snapshot(cwd)
            self.assertEqual(dirty["digest"], legacy_snapshot())
            self.assertNotEqual(dirty["digest"], clean["digest"])

    def test_unreadable_head_tree_does_not_become_an_unborn_snapshot(self):
        def capture(cwd, arguments):
            if arguments == ["rev-parse", "--show-toplevel"]:
                return os.fsencode(str(cwd)) + b"\n"
            if arguments == ["rev-parse", "--verify", "HEAD^{tree}"]:
                return None
            if arguments == ["rev-parse", "--verify", "HEAD"]:
                return b"a" * 40 + b"\n"
            raise AssertionError("A malformed repository must not reach diff execution")
        with mock.patch.object(bindings, "git_capture", side_effect=capture):
            self.assertIsNone(bindings.git_workspace_snapshot(Path.cwd()))

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

    def test_the_search_path_binds_the_same_identity_in_hook_and_runner(self):
        own = sorted(bindings._own_command_directories())[0]
        plugin_bin = str(Path(own))
        entries = ["/usr/local/bin", "/usr/bin", "/home/user/.local/bin"]

        def spelled(*values: str) -> str:
            # Windows entries are re-spelled to one canonical form (see
            # test_windows_search_path_spellings_share_one_identity).
            if os.name == "nt":
                return os.pathsep.join(os.path.normcase(os.path.normpath(value)) for value in values)
            return os.pathsep.join(values)

        # The Hook process sees a repeated entry; the tool call that runs the
        # verification sees Click's own command directory appended instead.
        hook = os.pathsep.join([*entries, "/usr/bin"])
        runner = os.pathsep.join([*entries, plugin_bin])
        self.assertNotEqual(hook, runner)
        self.assertEqual(
            bindings.normalized_search_path(hook), bindings.normalized_search_path(runner)
        )
        self.assertEqual(bindings.normalized_search_path(hook), spelled(*entries))
        # Order and every distinct directory are preserved: a repeat can never
        # win over its first occurrence, so dropping it changes no resolution.
        self.assertEqual(
            bindings.normalized_search_path(os.pathsep.join(["/b", "/a", "/b"])),
            spelled("/b", "/a"),
        )
        # Only the search path is normalized; other values stay verbatim.
        fingerprint = bindings.environment_fingerprint(
            {"PATH": hook, "TZ": "UTC" + os.pathsep + "UTC"}
        )
        self.assertEqual(fingerprint["PATH"], spelled(*entries))
        self.assertEqual(fingerprint["TZ"], "UTC" + os.pathsep + "UTC")
        # A real directory difference still changes the identity.
        self.assertNotEqual(
            bindings.normalized_search_path(hook),
            bindings.normalized_search_path(os.pathsep.join([*entries, "/opt/tools/bin"])),
        )

    def test_other_installed_plugins_offered_by_the_host_do_not_split_the_identity(self):
        # Claude Code appends every installed plugin's bin directory to the
        # tool call's search path. The `plugins/cache/<marketplace>/<plugin>/
        # <version>/bin` shape identifies them wherever Click itself was loaded
        # from, so a sibling plugin cannot make each request rebind.
        cache = Path("/home/user/.claude/plugins/cache")
        sibling = cache / "agent-plugins-for-aws" / "deploy-on-aws" / "1.3.0" / "bin"
        codex_sibling = Path("/home/user/.codex/plugins/cache/personal/agy-runner/0.2.0/bin")
        entries = ["/usr/local/bin", "/usr/bin"]
        hook = os.pathsep.join(entries)
        runner = os.pathsep.join([*entries, str(sibling), str(codex_sibling)])
        self.assertEqual(bindings.normalized_search_path(runner), bindings.normalized_search_path(hook))
        # Only that exact shape is host-owned; anything else stays part of
        # the identity, including deeper or shallower paths under a cache.
        for kept in (str(cache / "other" / "bin"), str(cache / "a" / "b" / "c" / "lib"),
                     str(cache / "a" / "b" / "c" / "bin" / "extra"), "/home/user/tools/v1/bin",
                     "/opt/plugins/a/b/c/bin"):
            with self.subTest(kept=kept):
                self.assertNotEqual(
                    bindings.normalized_search_path(os.pathsep.join([*entries, kept])),
                    bindings.normalized_search_path(hook),
                )

    def test_environment_fingerprint_is_an_allowlist_with_owner_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".git").mkdir()
            base = {
                "PATH": "/usr/bin",
                "PYTHONPATH": "/lib",
                "LC_ALL": "C.UTF-8",
                "TZ": "UTC",
                "GOFLAGS": "-mod=vendor",
                "DATABASE_URL": "postgres://one",
                "SENTRY-TRACE": "abc",
                "SHLVL": "3",
                "CLAUDE_CODE_SESSION_ID": "s1",
                "VSCODE_PID": "42",
                "GIT_EDITOR": "vim",
            }
            with mock.patch.dict(os.environ, base, clear=True):
                execution = bindings.verification_environment(cwd=root)
            # The child still receives project variables; launcher bookkeeping
            # and non-identifier names are dropped from execution as before.
            self.assertEqual(execution["DATABASE_URL"], "postgres://one")
            self.assertEqual(execution["PWD"], str(root))
            self.assertNotIn("SHLVL", execution)
            self.assertNotIn("SENTRY-TRACE", execution)
            first = bindings.environment_fingerprint(execution)
            self.assertEqual(
                set(first),
                {"PATH", "PYTHONPATH", "LC_ALL", "TZ", "GOFLAGS", "PWD"},
            )
            noisy = {**base, "DATABASE_URL": "postgres://two", "SENTRY-TRACE": "xyz",
                     "SHLVL": "4", "CLAUDE_CODE_SESSION_ID": "s2", "NEW_IDE_VARIABLE": "1"}
            with mock.patch.dict(os.environ, noisy, clear=True):
                self.assertEqual(
                    bindings.environment_fingerprint(bindings.verification_environment(cwd=root)), first
                )
            for key in ("SENTRY-TRACE", "SHLVL", "CLAUDE_CODE_SESSION_ID", "GIT_EDITOR", "DATABASE_URL"):
                self.assertFalse(bindings.environment_key_is_fingerprinted(key), key)
            for key in ("PATH", "PYTHONHASHSEED", "NODE_OPTIONS", "LC_MESSAGES", "CARGO_HOME", "TZ"):
                self.assertTrue(bindings.environment_key_is_fingerprinted(key), key)

            policy = root / ".click" / "environment.json"
            policy.parent.mkdir()
            policy.write_text(
                '{"version": 1, "fingerprint": ["DATABASE_URL", "APP_*"]}',
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {**base, "APP_MODE": "test"}, clear=True):
                extended = bindings.environment_fingerprint(bindings.verification_environment(cwd=root))
            self.assertEqual(extended["DATABASE_URL"], "postgres://one")
            self.assertEqual(extended["APP_MODE"], "test")
            with mock.patch.dict(os.environ, {**base, "APP_MODE": "test", "DATABASE_URL": "postgres://two"}, clear=True):
                self.assertNotEqual(
                    bindings.environment_fingerprint(bindings.verification_environment(cwd=root)), extended
                )
            nested = root / "src"
            nested.mkdir()
            with mock.patch.dict(os.environ, {**base, "APP_MODE": "test"}, clear=True):
                self.assertIn(
                    "APP_MODE",
                    bindings.environment_fingerprint(bindings.verification_environment(cwd=nested)),
                )

            for malformed in (
                '{"version": 2, "fingerprint": ["DATABASE_URL"]}',
                '{"version": 1, "fingerprint": ["bad name"]}',
                '{"version": 1, "fingerprint": "DATABASE_URL"}',
                '{"version": 1, "fingerprint": ["DATABASE_URL"], "extra": true}',
                "not json",
            ):
                policy.write_text(malformed, encoding="utf-8")
                with mock.patch.dict(os.environ, base, clear=True):
                    self.assertEqual(
                        bindings.environment_fingerprint(bindings.verification_environment(cwd=root)),
                        first,
                        malformed,
                    )

    @unittest.skipUnless(os.name == "nt", "Windows search-path spelling")
    def test_windows_search_path_spellings_share_one_identity(self):
        # Git Bash hands `bash -c python` the raw Windows entries and
        # `bash -c 'sh …'` the re-spelled ones; both name the same directories.
        raw = ";".join(
            [
                "C:\\Program Files\\dotnet\\",
                "C:\\\\ghcup\\bin",
                "c:\\tools\\php",
                "C:\\Windows\\System32\\OpenSSH\\",
            ]
        )
        respelled = ";".join(
            [
                "C:\\Program Files\\dotnet",
                "C:\\ghcup\\bin",
                "C:\\tools\\php",
                "C:\\Windows\\System32\\OpenSSH",
            ]
        )
        self.assertEqual(
            bindings.normalized_search_path(raw), bindings.normalized_search_path(respelled)
        )
        self.assertEqual(bindings.normalized_search_path(raw).count(";"), 3)
        self.assertNotEqual(
            bindings.normalized_search_path(raw),
            bindings.normalized_search_path(raw + ";C:\\extra"),
        )

    def test_environment_binding_covers_only_the_fingerprint_subset(self):
        execution = {"PATH": "/usr/bin", "PWD": "/work", "DATABASE_URL": "one", "TZ": "UTC"}
        binding = bindings.verification_environment_binding(execution, "t" * 32)
        self.assertEqual(len(binding), 3)
        changed_noise = {**execution, "DATABASE_URL": "two", "RUNNER_ONLY": "x"}
        projected, drifted, error = bindings.verification_environment_from_binding(
            binding, "t" * 32, changed_noise
        )
        self.assertEqual((projected, drifted, error), (changed_noise, False, ""))
        changed_bound = {**execution, "TZ": "Etc/GMT+7"}
        projected, drifted, error = bindings.verification_environment_from_binding(
            binding, "t" * 32, changed_bound
        )
        self.assertEqual((projected, drifted, error), (changed_bound, True, ""))
        missing_bound = {"PATH": "/usr/bin", "PWD": "/work", "DATABASE_URL": "one"}
        _, drifted, _ = bindings.verification_environment_from_binding(binding, "t" * 32, missing_bound)
        self.assertTrue(drifted)

        # The drift report names what moved, never the values.
        drift: dict[str, object] = {}
        bindings.verification_environment_from_binding(binding, "t" * 32, changed_noise, drift=drift)
        self.assertEqual(drift, {"changed": [], "absent": 0})
        bindings.verification_environment_from_binding(
            binding, "t" * 32, {**changed_bound, "LANG": "C.UTF-8"}, drift=drift
        )
        self.assertEqual(drift, {"changed": ["LANG", "TZ"], "absent": 0})
        bindings.verification_environment_from_binding(binding, "t" * 32, missing_bound, drift=drift)
        self.assertEqual(drift, {"changed": [], "absent": 1})
        self.assertNotIn("Etc/GMT+7", repr(drift))

    def test_executable_payload_uses_content_instead_of_volatile_mtime(self):
        baseline = {
            "name": "npx.cmd",
            "selected_path": "c:/node/npx.cmd",
            "path": "c:/node/npx.cmd",
            "size": 12,
            "mtime_ns": 1,
            "content_digest": "a" * 64,
            "runtime_identity": {
                "status": "complete",
                "digest": "b" * 64,
                "reason_codes": [],
            },
            "_execution_path": "C:/node/npx.cmd",
        }
        touched = {**baseline, "mtime_ns": 2}

        self.assertEqual(
            bindings.verification_executable_payload([baseline]),
            bindings.verification_executable_payload([touched]),
        )
        changed = {**baseline, "content_digest": "c" * 64}
        self.assertNotEqual(
            bindings.verification_executable_payload([baseline]),
            bindings.verification_executable_payload([changed]),
        )

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

    def test_windows_metadata_only_transition_gets_one_stable_content_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "verifier"
            executable.write_bytes(b"stable")
            stage = bindings.FileDigestStage()
            before = ("verifier", 1)
            after = ("verifier", 2)
            with (
                mock.patch.object(bindings.os, "name", "nt"),
                mock.patch.object(
                    stage, "_identity", side_effect=[before, after, after]
                ),
                mock.patch.object(
                    stage, "_digest_file", wraps=bindings.hash_file_content
                ) as digest,
            ):
                self.assertEqual(
                    stage(executable), hashlib.sha256(b"stable").hexdigest()
                )
            self.assertEqual(digest.call_count, 2)

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
            self.assertEqual(len(calls), 3 if os.name == "nt" else 2)

    def test_typed_result_keeps_every_legacy_record_field_and_avoids_copying(self):
        signature = inspect.signature(verification.record_result)
        expected = set(signature.parameters) - {
            "path", "batch", "batch_digest", "runner_token", "git_capture",
        }
        self.assertEqual({item.name for item in fields(verification.VerificationRunResult)}, expected)
        records = [{"message": "bounded diagnostic"}]
        result = verification.VerificationRunResult(1, 0, diagnostic_records=records)
        with mock.patch.object(verification._results, "_record_verification_result", return_value=False) as record:
            self.assertFalse(verification.record_outcome(Path("state"), {}, "digest", "token", result))
        self.assertIs(record.call_args.kwargs["diagnostic_records"], records)
        self.assertEqual(record.call_args.kwargs["exit_code"], 1)


if __name__ == "__main__":
    unittest.main()


class GitResolutionPassTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name) / "workspace"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)

    def test_git_is_resolved_once_per_workspace_inside_a_pass(self) -> None:
        inspection = bindings.click_inspection
        with mock.patch.object(
            inspection, "resolve_read_only_executable", wraps=inspection.resolve_read_only_executable
        ) as resolved:
            with bindings.binding_pass():
                for _ in range(3):
                    output = bindings.git_capture(self.workspace, ["rev-parse", "--show-toplevel"])
                    self.assertEqual(Path(output.decode().strip()).resolve(), self.workspace.resolve())
            self.assertEqual(resolved.call_count, 1)
            # Git itself still ran every time: the answer reflects the current tree.
            (self.workspace / "note.txt").write_text("x", encoding="utf-8")
            with bindings.binding_pass():
                status = bindings.git_capture(self.workspace, ["status", "--porcelain"])
            self.assertIn(b"note.txt", status)

    def test_without_a_pass_every_call_resolves_again(self) -> None:
        inspection = bindings.click_inspection
        with mock.patch.object(
            inspection, "resolve_read_only_executable", wraps=inspection.resolve_read_only_executable
        ) as resolved:
            for _ in range(2):
                bindings.git_capture(self.workspace, ["rev-parse", "--show-toplevel"])
            self.assertEqual(resolved.call_count, 2)

    def test_nested_passes_share_one_memo_and_a_new_path_misses(self) -> None:
        inspection = bindings.click_inspection
        with mock.patch.object(
            inspection, "resolve_read_only_executable", wraps=inspection.resolve_read_only_executable
        ) as resolved:
            with bindings.binding_pass():
                bindings.git_capture(self.workspace, ["rev-parse", "--show-toplevel"])
                with bindings.binding_pass():
                    bindings.git_capture(self.workspace, ["rev-parse", "--show-toplevel"])
                self.assertEqual(resolved.call_count, 1)
                with mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "") + os.pathsep + str(self.workspace)}):
                    bindings.git_capture(self.workspace, ["rev-parse", "--show-toplevel"])
                self.assertEqual(resolved.call_count, 2)
            self.assertIsNone(bindings._binding_pass)
