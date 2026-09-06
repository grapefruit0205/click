from __future__ import annotations

from click_gate_test_support import (
    CLICK_CAPABILITY,
    CLICK_GATE,
    CLICK_INSPECTION,
    CLICK_PROCESS,
    CLICK_VERIFICATION,
    HOOK_CONFIG,
    Path,
    ClickGateTestCase,
    json,
    mark_git_boundary,
    mock,
    os,
    shlex,
    split_runner_command,
    subprocess,
    sys,
    tempfile,
    time,
    unittest,
)


CLICK_RUNNER_TRANSPORT = CLICK_GATE.click_runner_transport


def spawned_child(
    *,
    returncode: int = 0,
    stdout: bytes | None = None,
    stderr: bytes | None = None,
) -> mock.Mock:
    child = mock.Mock(spec=subprocess.Popen)
    child.returncode = returncode
    child.communicate.return_value = (stdout, stderr)
    return child


class ClickGateInspectionTests(ClickGateTestCase):
    def observation_entry(self, request: dict[str, object]) -> dict[str, object]:
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        return state["observations"]["entries"][CLICK_CAPABILITY.digest(request)]

    def test_supported_cat_cache_reuses_once_and_fresh_bypasses_it(self) -> None:
        target = self.workspace / "notes.txt"
        target.write_text("alpha\nbeta\n", encoding="utf-8")
        request = {"version": 1, "commands": [["cat", "notes.txt"]]}

        first = self.pre_tool(
            "Bash", "cat notes.txt", "turn-1", tool_use_id="cat-first"
        )
        assert first is not None
        first_result = self.run_rewritten(first)
        self.assertEqual(first_result.returncode, 0, first_result.stderr)
        self.assertEqual(first_result.stdout, "alpha\nbeta\n")
        first_entry = self.observation_entry(request)
        self.assertEqual(first_entry["status"], "success")
        self.assertTrue(first_entry["actual_process_executed"])
        self.assertEqual(first_entry["cache_status"], "stored", first_entry)
        self.assertRegex(first_entry["cache_key"], r"^[0-9a-f]{64}$")

        second = self.pre_tool(
            "Bash", "cat notes.txt", "turn-1", tool_use_id="cat-second"
        )
        self.assert_observation_advisory(second, "already succeeded")
        second_result = self.run_rewritten(second)
        self.assertEqual(second_result.returncode, 0, second_result.stderr)
        self.assertEqual(second_result.stdout, first_result.stdout)
        self.assertIn("no read/search child process ran", second_result.stderr)
        self.assertIn("Sources: notes.txt", second_result.stderr)
        self.assertIn("stdout 11 bytes / 2 lines", second_result.stderr)
        second_entry = self.observation_entry(request)
        self.assertEqual(second_entry["status"], "reused")
        self.assertFalse(second_entry["actual_process_executed"])
        self.assertEqual(second_entry["cache_reuse_count"], 1)
        self.assertTrue(second_entry["cache_notice_shown"])
        self.assertEqual(second_entry["output_bytes"], first_entry["output_bytes"])

        third = self.pre_tool(
            "Bash", "cat notes.txt", "turn-1", tool_use_id="cat-third"
        )
        assert third is not None
        third_result = self.run_rewritten(third)
        self.assertEqual(third_result.returncode, 0, third_result.stderr)
        self.assertEqual(third_result.stdout, first_result.stdout)
        self.assertNotIn("[Click cache]", third_result.stderr)
        self.assertEqual(self.observation_entry(request)["cache_reuse_count"], 2)

        fresh_request = {**request, "fresh": True}
        command = f"click-gate inspect {shlex.quote(json.dumps(fresh_request))}"
        fresh = self.pre_tool(
            "Bash", command, "turn-1", tool_use_id="cat-fresh"
        )
        assert fresh is not None
        fresh_result = self.run_rewritten(fresh)
        self.assertEqual(fresh_result.returncode, 0, fresh_result.stderr)
        self.assertEqual(fresh_result.stdout, first_result.stdout)
        fresh_entry = self.observation_entry(fresh_request)
        self.assertEqual(fresh_entry["status"], "success")
        self.assertTrue(fresh_entry["actual_process_executed"])

        target.write_text("alpha\nchanged\n", encoding="utf-8")
        changed = self.pre_tool(
            "Bash", "cat notes.txt", "turn-1", tool_use_id="cat-changed"
        )
        assert changed is not None
        changed_result = self.run_rewritten(changed)
        self.assertEqual(changed_result.stdout, "alpha\nchanged\n")
        self.assertNotIn("[Click cache]", changed_result.stderr)
        self.assertTrue(self.observation_entry(request)["actual_process_executed"])

    def test_rg_directory_cache_invalidates_inventory_changes(self) -> None:
        sources = self.workspace / "src"
        sources.mkdir()
        first_path = sources / "a.txt"
        first_path.write_text("needle one\n", encoding="utf-8")
        request = {"version": 1, "commands": [["rg", "-n", "needle", "src"]]}

        first = self.inspect_gate(request["commands"], "turn-1")
        first_result = self.run_rewritten(first)
        self.assertEqual(first_result.returncode, 0, first_result.stderr)
        repeated = self.inspect_gate(request["commands"], "turn-1")
        repeated_result = self.run_rewritten(repeated)
        self.assertIn("[Click cache]", repeated_result.stderr)
        self.assertFalse(self.observation_entry(request)["actual_process_executed"])

        second_path = sources / "b.txt"
        second_path.write_text("needle two\n", encoding="utf-8")
        added = self.inspect_gate(request["commands"], "turn-1")
        added_result = self.run_rewritten(added)
        self.assertIn("a.txt", added_result.stdout)
        self.assertIn("b.txt", added_result.stdout)
        self.assertNotIn("[Click cache]", added_result.stderr)
        self.assertTrue(self.observation_entry(request)["actual_process_executed"])

        first_path.rename(sources / "renamed.txt")
        renamed = self.inspect_gate(request["commands"], "turn-1")
        renamed_result = self.run_rewritten(renamed)
        self.assertIn("renamed.txt", renamed_result.stdout)
        self.assertNotIn("a.txt", renamed_result.stdout)
        self.assertTrue(self.observation_entry(request)["actual_process_executed"])

        second_path.unlink()
        deleted = self.inspect_gate(request["commands"], "turn-1")
        deleted_result = self.run_rewritten(deleted)
        self.assertNotIn("b.txt", deleted_result.stdout)
        self.assertTrue(self.observation_entry(request)["actual_process_executed"])

    def test_supported_sed_range_reuses_only_the_same_range(self) -> None:
        target = self.workspace / "lines.txt"
        target.write_text("one\ntwo\nthree\n", encoding="utf-8")
        first_request = {
            "version": 1,
            "commands": [["sed", "-n", "1,2p", "lines.txt"]],
        }
        first = self.inspect_gate(first_request["commands"], "turn-1")
        first_result = self.run_rewritten(first)
        self.assertEqual(first_result.stdout, "one\ntwo\n")
        repeated = self.inspect_gate(first_request["commands"], "turn-1")
        repeated_result = self.run_rewritten(repeated)
        self.assertEqual(repeated_result.stdout, first_result.stdout)
        self.assertIn("[Click cache]", repeated_result.stderr)
        self.assertFalse(
            self.observation_entry(first_request)["actual_process_executed"]
        )

        other_request = {
            "version": 1,
            "commands": [["sed", "-n", "2,3p", "lines.txt"]],
        }
        other = self.inspect_gate(other_request["commands"], "turn-1")
        other_result = self.run_rewritten(other)
        self.assertEqual(other_result.stdout, "two\nthree\n")
        self.assertNotIn("[Click cache]", other_result.stderr)
        self.assertTrue(
            self.observation_entry(other_request)["actual_process_executed"]
        )

    def test_incomplete_and_corrupt_cache_fall_back_to_real_read(self) -> None:
        small = self.workspace / "small.txt"
        small.write_text("cached\n", encoding="utf-8")
        request = {"version": 1, "commands": [["cat", "small.txt"]]}
        first = self.inspect_gate(request["commands"], "turn-1")
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        cache_key = self.observation_entry(request)["cache_key"]
        cache_path = (
            self.plugin_data
            / "observation-cache-v1"
            / f"{cache_key}.json"
        )
        cache_path.write_text("{broken", encoding="utf-8")
        recovered = self.inspect_gate(request["commands"], "turn-1")
        recovered_result = self.run_rewritten(recovered)
        self.assertEqual(recovered_result.stdout, "cached\n")
        self.assertNotIn("[Click cache]", recovered_result.stderr)
        recovered_entry = self.observation_entry(request)
        self.assertEqual(recovered_entry["status"], "success")
        self.assertTrue(recovered_entry["actual_process_executed"])

        large = self.workspace / "large.txt"
        large.write_text("x" * 50_000, encoding="utf-8")
        large_request = {"version": 1, "commands": [["cat", "large.txt"]]}
        for index in range(2):
            payload = self.inspect_gate(large_request["commands"], "turn-1")
            result = self.run_rewritten(payload)
            self.assertEqual(result.returncode, 0)
            self.assertIn("output exceeded 48,000 bytes", result.stderr)
            entry = self.observation_entry(large_request)
            self.assertEqual(entry["status"], "incomplete")
            self.assertTrue(entry["actual_process_executed"])
            self.assertEqual(entry["cache_key"], "")

        no_match = self.workspace / "no-match.txt"
        no_match.write_text("haystack\n", encoding="utf-8")
        failed_request = {
            "version": 1,
            "commands": [["rg", "needle", "no-match.txt"]],
        }
        for index in range(2):
            payload = self.inspect_gate(failed_request["commands"], "turn-1")
            result = self.run_rewritten(payload)
            self.assertEqual(result.returncode, 1)
            entry = self.observation_entry(failed_request)
            self.assertEqual(entry["status"], "failed")
            self.assertTrue(entry["actual_process_executed"])
            self.assertEqual(entry["cache_key"], "")

    def test_hook_payload_writer_is_safe_for_legacy_windows_code_pages(self) -> None:
        stdout = mock.Mock()
        with mock.patch.object(CLICK_GATE.sys, "stdout", stdout):
            CLICK_GATE._write_stdout_payload({"contract": "쉬운 계약문"})

        rendered = stdout.write.call_args.args[0]
        self.assertTrue(rendered.isascii())
        self.assertEqual(json.loads(rendered), {"contract": "쉬운 계약문"})

    def test_hook_config_loads_mode_for_each_prompt(self) -> None:
        config = json.loads(HOOK_CONFIG.read_text(encoding="utf-8"))
        hooks = config["hooks"]
        self.assertNotIn("SessionStart", hooks)
        self.assertIn("UserPromptSubmit", hooks)
        prompt_handler = hooks["UserPromptSubmit"][0]["hooks"][0]
        self.assertTrue(
            prompt_handler["command"].endswith('click_gate.py" prompt-submit')
        )
        self.assertEqual(
            hooks["PreToolUse"][0]["matcher"],
            "^(Bash|apply_patch|Edit|Write|update_plan|functions\\.update_plan|mcp__node_repl__js)$",
        )
        pre_tool_handler = hooks["PreToolUse"][0]["hooks"][0]
        self.assertTrue(pre_tool_handler["command"].endswith('click_gate.py\" pre-tool'))
        self.assertEqual(prompt_handler["timeout"], 7)
        self.assertEqual(pre_tool_handler["timeout"], 7)
        self.assertEqual(
            hooks["PostToolUse"][0]["matcher"], "^mcp__node_repl__js$"
        )
        post_tool_handler = hooks["PostToolUse"][0]["hooks"][0]
        self.assertTrue(
            post_tool_handler["command"].endswith('click_gate.py" post-tool')
        )
        self.assertEqual(post_tool_handler["timeout"], 7)
        session_end_handler = hooks["SessionEnd"][0]["hooks"][0]
        self.assertTrue(
            session_end_handler["command"].endswith('click_gate.py" session-end')
        )
        self.assertEqual(session_end_handler["timeout"], 3)

    def test_uninvoked_hook_starts_without_state(self) -> None:
        self.assertFalse((self.plugin_data / "gate-state").exists())

    def test_read_only_bash_is_rewritten_before_gate(self) -> None:
        for command in (
            "rg --files",
            "Get-Content README.md",
            "sed -n '1,240p' README.md",
        ):
            with self.subTest(command=command):
                payload = self.pre_tool("Bash", command)
                self.assertEqual(
                    payload["hookSpecificOutput"]["permissionDecision"], "allow"
                )
                self.assertIn(
                    "run-observation",
                    split_runner_command(
                        payload["hookSpecificOutput"]["updatedInput"]["command"]
                    ),
                )

        initialized = subprocess.run(
            ["git", "init", "--quiet"],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        git_read = self.pre_tool("Bash", "git status --short")
        self.assertEqual(
            git_read["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertIn(
            "run-observation",
            split_runner_command(
                git_read["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        self.assertEqual(self.run_rewritten(git_read).returncode, 0)

        mixed_read = self.pre_tool(
            "Bash", "sed -n '1,20p' README.md && git status --short"
        )
        self.assertEqual(
            mixed_read["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertIn(
            "run-observation",
            split_runner_command(
                mixed_read["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )

        piped = self.pre_tool("Bash", "rg --files | sort")
        if piped is not None:
            self.assertNotIn(
                "permissionDecision", piped["hookSpecificOutput"]
            )
        self.arm_gate()
        piped_after_arm = self.pre_tool("Bash", "git status --short | head -20")
        self.assertEqual(
            piped_after_arm["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_unsupported_powershell_cmdlets_are_not_read_only_capabilities(self) -> None:
        for command in (
            "Get-ChildItem",
            "Get-Command",
            "Get-Item",
            "Get-Location",
            "Measure-Object",
            "Resolve-Path",
            "Select-String",
            "Test-Path",
        ):
            with self.subTest(command=command):
                self.assertFalse(CLICK_INSPECTION.is_read_only_tokens([command]))

    def test_evidence_default_allows_and_records_first_mutation(self) -> None:
        payload = self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch")
        self.assertIsNone(payload)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "evidence")
        self.assertEqual(state["runtime_mode"], "evidence")
        self.assertEqual(state["verification"]["mutation_revision"], 1)

    def test_pdf_tools_admit_only_side_effect_free_forms(self) -> None:
        allowed = (
            ["pdfinfo", "feedback.pdf"],
            ["pdftotext", "-layout", "feedback.pdf", "-"],
        )
        denied = (
            ["pdftotext", "feedback.pdf"],
            ["pdftotext", "feedback.pdf", "feedback.txt"],
            ["pdftoppm", "feedback.pdf", "page"],
        )
        for argv in allowed:
            with self.subTest(allowed=argv):
                self.assertTrue(CLICK_INSPECTION.is_read_only_tokens(argv))
        for argv in denied:
            with self.subTest(denied=argv):
                self.assertFalse(CLICK_INSPECTION.is_read_only_tokens(argv))

    def test_structured_inspection_runs_shell_free_and_advises_repeat(self) -> None:
        self.approve_contract()
        initialized = subprocess.run(
            ["git", "init", "--quiet"],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        first = self.inspect_gate([["git", "status", "--short"]], "turn-2")
        self.assertEqual(first["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        repeated = self.inspect_gate([["git", "status", "--short"]], "turn-2")
        self.assert_observation_advisory(
            repeated, "identical read or search already succeeded"
        )
        self.assertEqual(self.run_rewritten(repeated).returncode, 0)

    def test_structured_broad_inspection_allows_advised_cross_digest_repeat(
        self,
    ) -> None:
        self.initialize_git("verification_fixture.py")
        self.approve_contract()

        first = self.inspect_gate([["git", "ls-files"]], "turn-2")
        self.assertEqual(first["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertNotIn("additionalContext", first["hookSpecificOutput"])
        self.assertEqual(self.run_rewritten(first).returncode, 0)

        cross_digest = self.inspect_gate(
            [["git", "ls-files", "--cached"]], "turn-2"
        )
        self.assert_observation_advisory(cross_digest, "already completed")
        self.assertEqual(self.run_rewritten(cross_digest).returncode, 0)

        identical = self.inspect_gate([["git", "ls-files"]], "turn-2")
        self.assert_observation_advisory(
            identical, "identical read or search already succeeded"
        )
        self.assertEqual(self.run_rewritten(identical).returncode, 0)

    def test_broad_inventory_advisory_is_independent_of_model_identity(self) -> None:
        self.initialize_git("verification_fixture.py")
        self.approve_contract()

        first = self.pre_tool("Bash", "git ls-files", "turn-2")
        self.assertEqual(self.run_rewritten(first).returncode, 0)

        self.base_event["model"] = "different-frontier-model"
        second = self.pre_tool("Bash", "git ls-files --cached", "turn-2")
        self.assert_observation_advisory(second, "already completed")
        self.assertEqual(self.run_rewritten(second).returncode, 0)

    def test_logical_repeat_advisory_is_independent_of_model_identity(self) -> None:
        (self.workspace / "model-neutral.txt").write_text(
            "same request\n", encoding="utf-8"
        )
        self.approve_contract()
        command = self.read_file_command("model-neutral.txt")

        first = self.pre_tool("Bash", command, "turn-2")
        self.assertEqual(self.run_rewritten(first).returncode, 0)

        self.base_event["model"] = "another-model-family"
        repeated = self.pre_tool("Bash", command, "turn-2")
        self.assert_observation_advisory(
            repeated, "identical read or search already succeeded"
        )
        self.assertEqual(self.run_rewritten(repeated).returncode, 0)

    def test_structured_inspection_rejects_shells_and_write_options(self) -> None:
        for commands in (
            [["bash", "-c", "cat README.md"]],
            [["find", ".", "-delete"]],
            [["sort", "README.md", "-o", "sorted.txt"]],
        ):
            with self.subTest(commands=commands):
                denied = self.inspect_gate(commands)
                self.assertEqual(
                    denied["hookSpecificOutput"]["permissionDecision"], "deny"
                )

    def test_read_only_capability_rejects_path_qualified_executables(self) -> None:
        for executable in (
            "./cat",
            r".\cat",
            r"C:cat.exe",
            r"C:\tools\cat.exe",
            "C:/tools/cat.exe",
            r"\\server\share\cat.exe",
        ):
            with self.subTest(executable=executable):
                self.assertTrue(
                    CLICK_INSPECTION.is_path_qualified_executable(executable)
                )
        for argv in (
            ["./cat", "README.md"],
            ["../cat", "README.md"],
            ["/tmp/cat", "README.md"],
            ["/usr/bin/cat", "README.md"],
            [r".\cat", "README.md"],
            [r"C:cat.exe", "README.md"],
            [r"C:\tools\cat.exe", "README.md"],
            ["C:/tools/cat.exe", "README.md"],
            [r"\\server\share\cat.exe", "README.md"],
            ["./git", "status", "--short"],
        ):
            with self.subTest(argv=argv):
                self.assertFalse(CLICK_INSPECTION.is_read_only_tokens(argv))

    def test_windows_direct_command_tokenization_preserves_paths(self) -> None:
        cases = {
            r"Get-Content -LiteralPath C:\repo\README.md": [
                "Get-Content",
                "-LiteralPath",
                r"C:\repo\README.md",
            ],
            r'Get-Content -LiteralPath "C:\Program Files\README.md"': [
                "Get-Content",
                "-LiteralPath",
                r"C:\Program Files\README.md",
            ],
            r'Get-Content -LiteralPath "\\server\share\README.md"': [
                "Get-Content",
                "-LiteralPath",
                r"\\server\share\README.md",
            ],
            r"Get-Content README.md && rg needle src": [
                "Get-Content",
                "README.md",
                "&&",
                "rg",
                "needle",
                "src",
            ],
            r"Get-Content README.md | rg needle": [
                "Get-Content",
                "README.md",
                "|",
                "rg",
                "needle",
            ],
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                tokens, error = CLICK_INSPECTION.direct_command_tokens(
                    command, windows=True
                )
                self.assertEqual(error, "")
                self.assertEqual(tokens, expected)

        request, broad, error = CLICK_INSPECTION.request_from_bash(
            r'Get-Content -LiteralPath "C:\Program Files\README.md"',
            windows=True,
        )
        self.assertEqual(error, "")
        self.assertFalse(broad)
        self.assertEqual(
            request["commands"][0],
            ["Get-Content", "-LiteralPath", r"C:\Program Files\README.md"],
        )
        self.assertEqual(
            CLICK_RUNNER_TRANSPORT.windows_shell_quote(
                r"C:\plugin&data\gate-state"
            ),
            r'"C:\plugin&data\gate-state"',
        )

    def test_windows_runner_transport_hides_shell_expansion_tokens(self) -> None:
        arguments = [
            r"C:\Program Files\Python\python.exe",
            r"C:\Users\safe user\.codex\click_gate.py",
            "--state-root",
            r"C:\work\%PATH%!CLICK!\gate-state",
            "run-mutation",
            r"C:\work\%PATH%!CLICK!\gate-state\session-contract-1.json",
            "digest",
            "token",
            "encoded-request",
        ]
        with mock.patch.object(CLICK_RUNNER_TRANSPORT.os, "name", "nt"):
            command = CLICK_RUNNER_TRANSPORT.default_runner_shell_command(arguments)
        self.assertTrue(command.startswith('py -3 "C:\\Users\\safe user'))
        self.assertNotIn(arguments[0], command)
        self.assertIn('"--encoded-runner"', command)
        self.assertNotIn("%PATH%", command)
        self.assertNotIn("!CLICK!", command)
        encoded = command.rsplit('"', 2)[1]
        decoded, error = CLICK_RUNNER_TRANSPORT.decode_runner_transport(encoded)
        self.assertEqual(error, "")
        self.assertEqual(decoded, arguments[2:])

    def test_windows_runner_refuses_expandable_launcher_paths(self) -> None:
        with mock.patch.object(CLICK_RUNNER_TRANSPORT.os, "name", "nt"):
            for launcher in (
                r"C:\%PATH%\python.exe",
                r"C:\!CLICK!\python.exe",
                r"C:\$profile\python.exe",
                r"C:\`profile\python.exe",
            ):
                with self.subTest(launcher=launcher):
                    self.assertEqual(
                        CLICK_RUNNER_TRANSPORT.default_runner_shell_command(
                            [launcher, r"C:\click_gate.py", "run-inspection-once", "x"]
                        ),
                        "exit 2",
                    )

    def test_runner_transport_rejects_malformed_or_oversized_payloads(self) -> None:
        for encoded in (
            "not-base64%",
            CLICK_RUNNER_TRANSPORT.encode_runner_transport([]),
        ):
            with self.subTest(encoded=encoded):
                decoded, error = CLICK_RUNNER_TRANSPORT.decode_runner_transport(encoded)
                self.assertIsNone(decoded)
                self.assertTrue(error)

        bomb = CLICK_RUNNER_TRANSPORT.base64.urlsafe_b64encode(
            CLICK_RUNNER_TRANSPORT.zlib.compress(b'"' + b"x" * 30_000 + b'"')
        ).decode()
        decoded, error = CLICK_RUNNER_TRANSPORT.decode_runner_transport(bomb)
        self.assertIsNone(decoded)
        self.assertIn("bounded payload", error)

    def test_inspection_never_executes_a_path_qualified_read_only_name(self) -> None:
        with mock.patch.object(CLICK_PROCESS, "spawn_argv") as spawn:
            exit_code = CLICK_INSPECTION.execute_commands(
                [["./cat", "README.md"]], workspace=self.workspace
            )
        self.assertEqual(exit_code, 2)
        spawn.assert_not_called()

    def test_inspection_rejects_a_workspace_path_shadow(self) -> None:
        mark_git_boundary(self.workspace)
        fake = self.workspace / ("cat.exe" if os.name == "nt" else "cat")
        fake.write_text("not a real reader\n", encoding="utf-8")
        with (
            mock.patch("shutil.which", return_value=str(fake)),
            mock.patch.object(CLICK_PROCESS, "spawn_argv") as spawn,
        ):
            exit_code = CLICK_INSPECTION.execute_commands(
                [["cat", "README.md"]], workspace=self.workspace
            )
        self.assertEqual(exit_code, 2)
        spawn.assert_not_called()

    def test_inspection_rewrites_a_trusted_bare_executable(self) -> None:
        mark_git_boundary(self.workspace)
        trusted_root = Path(self.temporary.name) / "trusted-bin"
        trusted_root.mkdir()
        trusted = trusted_root / ("cat.exe" if os.name == "nt" else "cat")
        trusted.write_text("trusted fixture\n", encoding="utf-8")
        child = spawned_child()
        with (
            mock.patch.dict(
                CLICK_GATE.os.environ,
                {
                    "LD_PRELOAD": "/tmp/inject.so",
                    "DYLD_INSERT_LIBRARIES": "/tmp/inject.dylib",
                    "GCONV_PATH": "/tmp/gconv",
                    "LOCPATH": "/tmp/locale",
                },
            ),
            mock.patch("shutil.which", return_value=str(trusted)) as which,
            mock.patch.object(
                CLICK_PROCESS, "spawn_argv", return_value=child
            ) as spawn,
        ):
            exit_code = CLICK_INSPECTION.execute_commands(
                [["cat", "README.md"]], workspace=self.workspace
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(spawn.call_args.args[0][0], str(trusted.resolve()))
        self.assertEqual(which.call_count, 2)
        sanitized_path = which.call_args_list[1].kwargs["path"]
        self.assertNotIn(str(self.workspace), sanitized_path)
        self.assertEqual(spawn.call_args.kwargs["env"]["PATH"], sanitized_path)
        for key in (
            "LD_PRELOAD",
            "DYLD_INSERT_LIBRARIES",
            "GCONV_PATH",
            "LOCPATH",
        ):
            self.assertNotIn(key, spawn.call_args.kwargs["env"])

    def test_inspection_rejects_a_symlink_into_the_workspace(self) -> None:
        mark_git_boundary(self.workspace)
        target = self.workspace / ("cat.exe" if os.name == "nt" else "cat")
        target.write_text("workspace target\n", encoding="utf-8")
        link_root = Path(self.temporary.name) / "linked-bin"
        link_root.mkdir()
        link = link_root / target.name
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with (
            mock.patch("shutil.which", return_value=str(link)),
            mock.patch.object(CLICK_PROCESS, "spawn_argv") as spawn,
        ):
            exit_code = CLICK_INSPECTION.execute_commands(
                [["cat", "README.md"]], workspace=self.workspace
            )
        self.assertEqual(exit_code, 2)
        spawn.assert_not_called()

    def test_inspection_rejects_a_workspace_symlink_to_a_trusted_binary(self) -> None:
        mark_git_boundary(self.workspace)
        trusted_root = Path(self.temporary.name) / "trusted-target"
        trusted_root.mkdir()
        target = trusted_root / ("cat.exe" if os.name == "nt" else "cat")
        target.write_text("trusted target\n", encoding="utf-8")
        link = self.workspace / target.name
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with (
            mock.patch("shutil.which", return_value=str(link)),
            mock.patch.object(CLICK_PROCESS, "spawn_argv") as spawn,
        ):
            exit_code = CLICK_INSPECTION.execute_commands(
                [["cat", "README.md"]], workspace=self.workspace
            )
        self.assertEqual(exit_code, 2)
        spawn.assert_not_called()

    def test_sanitized_path_drops_relative_and_workspace_entries(self) -> None:
        mark_git_boundary(self.workspace)
        outside = Path(self.temporary.name) / "outside-bin"
        outside.mkdir()
        workspace_bin = self.workspace / "bin"
        workspace_bin.mkdir()
        workspace_link = self.workspace / "linked-bin"
        try:
            workspace_link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        source = os.pathsep.join(
            ["", ".", "relative-bin", str(workspace_bin), str(workspace_link), str(outside)]
        )
        sanitized = CLICK_INSPECTION.sanitized_executable_path(
            source, workspace=self.workspace
        ).split(os.pathsep)
        self.assertEqual(sanitized, [str(outside.resolve())])

    def test_read_only_resolution_excludes_the_containing_repository(self) -> None:
        repository = self.workspace / "repository"
        repository.mkdir()
        mark_git_boundary(repository)
        nested = repository / "packages" / "app"
        nested.mkdir(parents=True)
        sibling_bin = repository / "tools"
        sibling_bin.mkdir()
        fake = sibling_bin / ("cat.exe" if os.name == "nt" else "cat")
        fake.write_text("repository-owned reader\n", encoding="utf-8")

        with mock.patch("shutil.which", return_value=str(fake)):
            executable, error = CLICK_INSPECTION.resolve_read_only_executable(
                "cat", workspace=nested
            )

        self.assertIsNone(executable)
        self.assertIn("workspace", error)

    def test_workspace_boundary_ignores_an_invalid_git_named_ancestor(self) -> None:
        ancestor = Path(self.temporary.name) / "invalid-ancestor"
        (ancestor / ".git").mkdir(parents=True)
        nested = ancestor / "nested" / "workspace"
        nested.mkdir(parents=True)
        self.assertEqual(CLICK_INSPECTION.workspace_boundary(nested), nested.resolve())
        self.assertFalse(CLICK_INSPECTION.git_metadata_present(nested))

    def test_sanitized_path_fails_closed_on_a_symlink_loop(self) -> None:
        mark_git_boundary(self.workspace)
        first = Path(self.temporary.name) / "loop-a"
        second = Path(self.temporary.name) / "loop-b"
        try:
            first.symlink_to(second)
            second.symlink_to(first)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        sanitized = CLICK_INSPECTION.sanitized_executable_path(
            str(first), workspace=self.workspace
        )
        self.assertEqual(sanitized, "")

    def test_workspace_containment_uses_filesystem_identity_for_aliases(self) -> None:
        alias = Path(self.temporary.name) / "workspace-alias"
        try:
            alias.symlink_to(self.workspace, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        self.assertTrue(CLICK_INSPECTION.path_is_within(alias, self.workspace))

    def test_read_only_child_environments_strip_loader_injection(self) -> None:
        mark_git_boundary(self.workspace)
        source = {
            "PATH": os.pathsep.join([str(self.workspace), "/usr/bin"]),
            "HOME": str(Path(self.temporary.name) / "home"),
            "LD_PRELOAD": "/tmp/inject.so",
            "DYLD_INSERT_LIBRARIES": "/tmp/inject.dylib",
            "GCONV_PATH": "/tmp/gconv",
            "LOCPATH": "/tmp/locale",
            "GIT_EXTERNAL_DIFF": "/tmp/helper",
        }
        with mock.patch.dict(CLICK_GATE.os.environ, source, clear=True):
            read_environment = CLICK_INSPECTION.sanitized_read_only_environment(
                workspace=self.workspace
            )
        git_environment = CLICK_INSPECTION.sanitized_git_environment(
            source, workspace=self.workspace
        )

        for environment in (read_environment, git_environment):
            self.assertEqual(environment["HOME"], source["HOME"])
            for key in (
                "LD_PRELOAD",
                "DYLD_INSERT_LIBRARIES",
                "GCONV_PATH",
                "LOCPATH",
            ):
                self.assertNotIn(key, environment)
            self.assertNotIn(str(self.workspace), environment["PATH"])
        self.assertNotIn("GIT_EXTERNAL_DIFF", git_environment)

    def test_git_and_ssh_inspection_execute_verified_absolute_binaries(self) -> None:
        mark_git_boundary(self.workspace)
        trusted_root = Path(self.temporary.name) / "trusted-tools"
        trusted_root.mkdir()
        names = {
            "git": trusted_root / ("git.exe" if os.name == "nt" else "git"),
            "ssh": trusted_root / ("ssh.exe" if os.name == "nt" else "ssh"),
        }
        for path in names.values():
            path.write_text("trusted fixture\n", encoding="utf-8")

        def resolved(name: str, path: str | None = None) -> str | None:
            return str(names[name.lower().removesuffix(".exe")])

        child = spawned_child()
        with (
            mock.patch("shutil.which", side_effect=resolved),
            mock.patch.object(
                CLICK_PROCESS, "spawn_argv", return_value=child
            ) as spawn,
        ):
            self.assertEqual(
                CLICK_INSPECTION.execute_commands(
                    [["git", "status", "--short"]], workspace=self.workspace
                ),
                0,
            )
            self.assertEqual(
                CLICK_INSPECTION.execute_commands(
                    [["ssh", "example-host", "git", "status", "--short"]],
                    workspace=self.workspace,
                ),
                0,
            )
        self.assertEqual(
            spawn.call_args_list[0].args[0][0], str(names["git"].resolve())
        )
        self.assertEqual(
            spawn.call_args_list[1].args[0][0], str(names["ssh"].resolve())
        )

    def test_structured_ssh_inspection_reuses_bounded_git_policy(self) -> None:
        allowed = (
            ["ssh", "example-host", "git", "status", "--short"],
            ["ssh.exe", "example-host", "git", "status", "--short"],
            ["ssh", "user@example-host", "git", "rev-parse", "HEAD"],
            ["ssh", "example-host", "git", "merge-base", "HEAD", "origin/main"],
            ["ssh", "example-host", "git", "remote", "get-url", "origin"],
            [
                "ssh",
                "example-host",
                "git",
                "remote",
                "get-url",
                "--all",
                "origin",
            ],
        )
        denied = (
            ["ssh", "-o", "ProxyCommand=helper", "example-host", "git", "status"],
            ["/tmp/ssh", "example-host", "git", "status"],
            ["ssh", "example-host", "git status"],
            ["ssh", "example-host", "hostname"],
            ["ssh", "example-host", "docker", "ps"],
            ["ssh", "example-host", "systemctl", "status", "service"],
            ["ssh", "example-host", "nvidia-smi", "-L"],
            ["ssh", "example-host", "sudo", "-n", "true"],
            ["ssh", "example-host", "bash", "-c", "git status"],
            ["ssh", "example-host", "ssh", "other-host", "git", "status"],
            ["ssh", "example-host", "git", "fetch"],
            ["ssh", "example-host", "git", "log", "-1"],
            ["ssh", "example-host", "git", "rev-parse", "origin/main"],
            ["ssh", "example-host", "git", "remote", "-v"],
            ["ssh", "example-host", "git", "remote", "set-url", "origin", "x"],
        )

        for argv in allowed:
            with self.subTest(allowed=argv):
                self.assertTrue(CLICK_INSPECTION.is_read_only_tokens(argv))
        for argv in denied:
            with self.subTest(denied=argv):
                self.assertFalse(CLICK_INSPECTION.is_read_only_tokens(argv))
                payload = self.inspect_gate([argv])
                self.assertEqual(
                    payload["hookSpecificOutput"]["permissionDecision"], "deny"
                )

    def test_direct_structured_ssh_read_becomes_an_observation(self) -> None:
        self.approve_contract()
        command = "ssh example-host git status --short"
        request, broad, error = CLICK_INSPECTION.request_from_bash(command)
        self.assertEqual(error, "")
        self.assertFalse(broad)
        self.assertEqual(
            request,
            {
                "version": 1,
                "commands": [
                    ["ssh", "example-host", "git", "status", "--short"]
                ],
            },
        )
        payload = self.pre_tool("Bash", command, "turn-2")
        rewritten = payload["hookSpecificOutput"]["updatedInput"]["command"]
        self.assertIn("run-observation", split_runner_command(rewritten))

        piped = self.pre_tool(
            "Bash",
            "ssh example-host git status --short | head -1",
            "turn-2",
        )
        self.assertEqual(
            piped["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_structured_ssh_execution_preserves_remote_argv_literals(self) -> None:
        remote_argv = [
            "git",
            "merge-base",
            "HEAD",
            "literal|value;still-one-argument",
        ]
        argv = ["ssh", "example-host", *remote_argv]
        prepared = CLICK_INSPECTION.execution_argv(argv)
        safe_git_argv, error = CLICK_INSPECTION.build_read_only_git_argv(remote_argv)
        self.assertEqual(error, "")
        self.assertEqual(prepared[:4], ["ssh", "-n", "-F", "none"])
        self.assertIn("BatchMode=yes", prepared)
        self.assertIn("StrictHostKeyChecking=yes", prepared)
        self.assertIn("ConnectTimeout=10", prepared)
        self.assertIn("ConnectionAttempts=1", prepared)
        self.assertIn("ServerAliveInterval=5", prepared)
        self.assertIn("ServerAliveCountMax=1", prepared)
        self.assertIn("NumberOfPasswordPrompts=0", prepared)
        self.assertIn("UpdateHostKeys=no", prepared)
        self.assertIn("PermitLocalCommand=no", prepared)
        self.assertIn("ClearAllForwardings=yes", prepared)
        self.assertEqual(prepared[-2], "example-host")
        self.assertEqual(shlex.split(prepared[-1], posix=True), safe_git_argv)

        child = spawned_child()
        with mock.patch.object(
            CLICK_PROCESS, "spawn_argv", return_value=child
        ) as spawn:
            self.assertEqual(CLICK_INSPECTION.execute_argv_commands([argv]), 0)
        self.assertEqual(spawn.call_args.args[0], prepared)
        child.communicate.assert_called_once_with(timeout=None)

        pinned_ssh = (
            r"C:\trusted\ssh.exe" if os.name == "nt" else "/trusted/bin/ssh"
        )
        pinned_argv = [pinned_ssh, "example-host", *remote_argv]
        pinned = CLICK_INSPECTION.execution_argv(pinned_argv)
        self.assertEqual(pinned[0], pinned_ssh)
        self.assertIn("BatchMode=yes", pinned)
        self.assertIsNotNone(CLICK_INSPECTION.structured_ssh_parts(pinned_argv))

    def test_structured_ssh_execution_keeps_mutations_explicit(self) -> None:
        remote_argv = ["python3", "tool.py", "--value", "literal|value"]
        argv = ["ssh", "example-host", *remote_argv]
        self.assertEqual(CLICK_INSPECTION.execution_argv(argv), argv)
        self.assertFalse(
            CLICK_INSPECTION.is_read_only_tokens(argv)
        )

        unsupported = ["ssh", "-p", "2222", "example-host", "git", "status"]
        self.assertEqual(CLICK_INSPECTION.execution_argv(unsupported), unsupported)

    def test_structured_ssh_read_is_valid_targeted_verification(self) -> None:
        self.approve_contract()
        payload = self.verify_checks(
            [
                {
                    "argv": ["ssh", "example-host", "git", "status", "--short"],
                    "class": "targeted",
                }
            ]
        )
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "allow")

    def test_git_remote_output_redacts_credentials_and_query_tokens(self) -> None:
        output = CLICK_INSPECTION.redact_git_remote_output(
            b"https://user:token@example.com/repo.git?access_token=secret\n"
            b"ssh://user:password@example.com/repo.git#secret\n"
            b"token@example.com:owner/repo.git\n"
        )
        self.assertEqual(
            output,
            b"https://example.com/repo.git\n"
            b"ssh://example.com/repo.git\n"
            b"example.com:owner/repo.git\n",
        )
        self.assertNotIn(b"token", output)
        self.assertNotIn(b"password", output)
        self.assertNotIn(b"secret", output)

    def test_structured_ssh_remote_url_is_redacted_before_output(self) -> None:
        pinned_ssh = (
            r"C:\trusted\ssh.exe" if os.name == "nt" else "/trusted/bin/ssh"
        )
        for executable in ("ssh", pinned_ssh):
            with self.subTest(executable=executable):
                argv = [
                    executable,
                    "example-host",
                    "git",
                    "remote",
                    "get-url",
                    "origin",
                ]
                with (
                    tempfile.TemporaryFile() as stdout_file,
                    tempfile.TemporaryFile() as stderr_file,
                ):
                    child = spawned_child(
                        stdout=b"https://user:secret@example.com/repo.git\n",
                        stderr=b"",
                    )
                    with mock.patch.object(
                        CLICK_PROCESS, "spawn_argv", return_value=child
                    ):
                        self.assertEqual(
                            CLICK_INSPECTION.execute_argv_commands(
                                [argv], stdout_file, stderr_file
                            ),
                            0,
                        )
                    stdout_file.seek(0)
                    self.assertEqual(
                        stdout_file.read(), b"https://example.com/repo.git\n"
                    )

    def test_structured_inspection_emulates_get_content_cross_platform(self) -> None:
        (self.workspace / "native.txt").write_text(
            "portable inspection\n", encoding="utf-8"
        )
        payload = self.inspect_gate(
            [["Get-Content", "-Raw", "native.txt"]]
        )
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "portable inspection\n")

        for option in ("-Path", "-LiteralPath"):
            with self.subTest(option=option):
                option_payload = self.inspect_gate(
                    [["Get-Content", "-Raw", option, "native.txt"]]
                )
                option_result = self.run_rewritten(option_payload)
                self.assertEqual(option_result.returncode, 0, option_result.stderr)
                self.assertEqual(option_result.stdout, "portable inspection\n")

    def test_get_content_rejects_options_the_native_runner_does_not_implement(self) -> None:
        for option, value in (
            ("-Tail", "10"),
            ("-TotalCount", "10"),
            ("-Encoding", "utf8"),
            ("-ErrorAction", "Stop"),
        ):
            with self.subTest(option=option):
                denied = self.inspect_gate(
                    [["Get-Content", option, value, "native.txt"]]
                )
                self.assertEqual(
                    denied["hookSpecificOutput"]["permissionDecision"], "deny"
                )

    def test_structured_capabilities_reject_environment_assignment_prefixes(self) -> None:
        for label, argv in (
            ("inspection", ["LC_ALL=C", "sort", "README.md"]),
            ("mutation", ["CI=1", "npm", "run", "build"]),
            ("verification", ["CI=1", "npm", "test"]),
        ):
            with self.subTest(label=label):
                normalized, error = CLICK_CAPABILITY.validate_argv(argv, label.title())
                self.assertIsNone(normalized)
                self.assertIn("NAME=value", error)

    def test_structured_capabilities_reject_process_control_executables(self) -> None:
        for argv in (
            ["kill", "1234"],
            ["/usr/bin/PKILL", "-f", "codex"],
            ["killall", "node"],
            ["pskill.exe", "codex.exe"],
            [r"C:\Windows\System32\taskkill.exe", "/IM", "codex.exe"],
            ["tskill.exe", "1234"],
            ["Stop-Process", "-Id", "1234"],
        ):
            with self.subTest(argv=argv):
                normalized, error = CLICK_CAPABILITY.validate_argv(argv, "Mutation")
                self.assertIsNone(normalized)
                self.assertIn("process-control executable", error)

        normalized, error = CLICK_CAPABILITY.validate_argv(
            ["kill-switch-check", "--help"], "Mutation"
        )
        self.assertEqual(normalized, ["kill-switch-check", "--help"])
        self.assertEqual(error, "")

    def test_subprocess_isolation_kwargs_are_platform_specific(self) -> None:
        with mock.patch.object(CLICK_PROCESS.os, "name", "posix"):
            self.assertEqual(
                CLICK_PROCESS.isolated_subprocess_kwargs(),
                {"start_new_session": True},
            )
        with (
            mock.patch.object(CLICK_PROCESS.os, "name", "nt"),
            mock.patch.object(
                CLICK_PROCESS.subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                512,
                create=True,
            ),
        ):
            self.assertEqual(
                CLICK_PROCESS.isolated_subprocess_kwargs(),
                {"creationflags": 512},
            )

    def test_all_click_subprocesses_use_isolated_process_groups(self) -> None:
        isolated = {"start_new_session": True}
        child = spawned_child(stdout=b"captured\n")
        with (
            mock.patch.object(
                CLICK_PROCESS,
                "isolated_subprocess_kwargs",
                return_value=isolated,
            ) as isolation,
            mock.patch.object(
                CLICK_PROCESS.subprocess, "Popen", return_value=child
            ) as popen,
        ):
            self.assertEqual(
                CLICK_INSPECTION.execute_argv_commands([["echo", "ok"]]), 0
            )
            self.assertEqual(
                CLICK_INSPECTION.execute_read_only_git(
                    ["git", "status", "--short"], None, None
                ),
                0,
            )
            self.assertEqual(
                CLICK_VERIFICATION.git_capture(self.workspace, ["status", "--short"]),
                b"captured\n",
            )

        self.assertEqual(isolation.call_count, 3)
        self.assertEqual(len(popen.call_args_list), 3)
        for call in popen.call_args_list:
            with self.subTest(argv=call.args[0]):
                self.assertTrue(call.kwargs["start_new_session"])
                self.assertFalse(call.kwargs["shell"])

    def test_direct_read_sequence_is_rewritten_as_shell_free_inspection(self) -> None:
        self.approve_contract()
        (self.workspace / "first.txt").write_text("first\n", encoding="utf-8")
        (self.workspace / "second.txt").write_text("second\n", encoding="utf-8")
        if os.name == "nt":
            command = "Get-Content -Raw first.txt && Get-Content -Raw second.txt"
        else:
            command = "sed -n '1,5p' first.txt && sed -n '1,5p' second.txt"
        payload = self.pre_tool("Bash", command, "turn-2")
        rewritten = payload["hookSpecificOutput"]["updatedInput"]["command"]
        self.assertIn("run-observation", split_runner_command(rewritten))
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("first", result.stdout)
        self.assertIn("second", result.stdout)

    def test_structured_requests_require_protocol_version_one(self) -> None:
        request = {"version": 2, "commands": [["git", "status", "--short"]]}
        command = f"click-gate inspect {shlex.quote(json.dumps(request))}"
        denied = self.pre_tool("Bash", command)
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("version", denied["hookSpecificOutput"]["permissionDecisionReason"])

    def test_inspection_request_retains_the_eight_command_operational_cap(self) -> None:
        request, broad, error = CLICK_INSPECTION.validate_request(
            json.dumps(
                {
                    "version": 1,
                    "commands": [["git", "status", "--short"] for _ in range(9)],
                }
            )
        )

        self.assertIsNone(request)
        self.assertFalse(broad)
        self.assertEqual(error, "Inspection may contain at most 8 commands.")

    def test_shell_chaining_is_not_treated_as_read_only(self) -> None:
        self.arm_gate()
        for command in (
            "rg --files && python3 update_schema.py",
            "rg --files | tee captured.txt",
            "rg --files & python3 update_schema.py",
            "rg pattern <(python3 generate_input.py)",
        ):
            with self.subTest(command=command):
                payload = self.pre_tool("Bash", command)
                self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_write_capable_options_are_not_treated_as_read_only(self) -> None:
        self.arm_gate()
        for command in (
            "sed -i s/old/new/ src/app.py",
            "sed -n '1,20w captured.txt' src/app.py",
            "sed -n '1e touch captured.txt' src/app.py",
            "find . -fprint output.txt",
            "find . -fprint0 output.txt",
            "sort input.txt --output=result.txt",
            "sort input.txt --compress-program=update_schema.py",
            "diff old new --output=changes.txt",
            "tree -o inventory.txt",
            "rg --pre update_schema.py pattern .",
            "git diff --output=changes.txt",
            "file --compile custom.magic",
        ):
            with self.subTest(command=command):
                payload = self.pre_tool("Bash", command)
                self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_git_read_only_policy_rejects_pager_config_and_removed_subcommands(self) -> None:
        commands = (
            ["git", "-p", "status"],
            ["git", "--paginate", "status"],
            ["git", "-c", "core.pager=cat", "status"],
            ["git", "--config-env=core.pager=PAGER", "status"],
            ["git", "grep", "-Oless", "needle", "."],
            ["git", "grep", "--open-files-in-pager=less", "needle", "."],
            ["git", "cat-file", "--filters", "HEAD:README.md"],
            ["git", "cat-file", "--textconv", "HEAD:README.md"],
            ["git", "show", "--textconv", "HEAD"],
            ["git", "log", "--format=%G?", "-1"],
            ["git", "log", "--pretty=format:%H", "-1"],
            ["git", "show", "--format=%H", "HEAD"],
            ["git", "show", "--show-signature", "HEAD"],
            ["git", "for-each-ref", "--format=%(objectname)", "refs/heads"],
            ["git", "ls-files", "--format=%(objectname)"],
            ["git", "ls-tree", "--format=%(objectname)", "HEAD"],
            ["git", "status", "--verbose"],
            ["git", "status", "-v"],
            ["git", "status", "-vv"],
            ["git", "remote"],
            ["git", "remote", "-v"],
            ["git", "remote", "set-url", "origin", "https://example.com/repo.git"],
            ["git", "remote", "add", "backup", "https://example.com/repo.git"],
        )
        for argv in commands:
            with self.subTest(argv=argv):
                denied = self.inspect_gate([argv])
                self.assertEqual(
                    denied["hookSpecificOutput"]["permissionDecision"], "deny"
                )

    def test_git_read_only_policy_uses_hardened_executor_shape(self) -> None:
        for argv in (
            ["git", "status", "--short"],
            ["git", "diff", "--check"],
            ["git", "log", "--oneline"],
            ["git", "merge-base", "HEAD", "origin/main"],
            ["git", "remote", "get-url", "origin"],
        ):
            with self.subTest(argv=argv):
                request, _, error = CLICK_INSPECTION.validate_request(
                    json.dumps({"version": 1, "commands": [argv]})
                )
                self.assertEqual(error, "")
                self.assertIsNotNone(request)
        safe, error = CLICK_INSPECTION.build_read_only_git_argv(
            ["git", "diff", "--check"]
        )
        self.assertEqual(error, "")
        self.assertIsNotNone(safe)
        assert safe is not None
        self.assertIn("--no-pager", safe)
        self.assertIn("--no-optional-locks", safe)
        self.assertIn("core.fsmonitor=false", safe)
        self.assertIn("diff.external=", safe)
        self.assertIn("log.showSignature=false", safe)
        self.assertIn("format.pretty=medium", safe)
        self.assertIn("--no-ext-diff", safe)
        self.assertIn("--no-textconv", safe)

        environment = CLICK_INSPECTION.sanitized_git_environment(
            {
                "PATH": os.environ.get("PATH", ""),
                "GIT_PAGER": "evil-pager",
                "GIT_EXTERNAL_DIFF": "evil-diff",
                "GIT_CONFIG_COUNT": "1",
            }
        )
        self.assertNotIn("GIT_PAGER", environment)
        self.assertNotIn("GIT_EXTERNAL_DIFF", environment)
        self.assertNotIn("GIT_CONFIG_COUNT", environment)
        self.assertEqual(environment["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], os.devnull)

        with mock.patch.dict(
            os.environ,
            {"RIPGREP_CONFIG_PATH": str(self.workspace / "unsafe-rg-config")},
        ):
            read_environment = CLICK_INSPECTION.sanitized_read_only_environment(
                workspace=self.workspace
            )
        self.assertNotIn("RIPGREP_CONFIG_PATH", read_environment)
