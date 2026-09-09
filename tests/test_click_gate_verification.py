from __future__ import annotations

import copy

from contextlib import ExitStack

from click_gate_test_support import (
    CLICK_CAPABILITY,
    CLICK_EVIDENCE,
    CLICK_GATE,
    CLICK_INSPECTION,
    CLICK_PROCESS,
    CLICK_STATE,
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


class VerificationLifecycleBoundaryTests(ClickGateTestCase):
    """Inject post-admission faults through existing internal call boundaries."""

    hook_in_process = True

    def _runner_context(self) -> ExitStack:
        context = ExitStack()
        context.enter_context(mock.patch.dict(os.environ, {
            "PLUGIN_DATA": str(self.plugin_data), "CLICK_CONFIG_HOME": str(self.plugin_data),
        }))
        context.enter_context(mock.patch.object(CLICK_VERIFICATION.Path, "cwd", return_value=self.workspace))
        context.enter_context(mock.patch.object(sys, "path", [str(Path(CLICK_VERIFICATION.__file__).parent), *sys.path]))
        return context

    def _prepared(self, mode: str = "guarded") -> list[str]:
        self.plugin_data = Path(self.temporary.name) / f"plugin-{getattr(self, '_sequence', 0)}"
        self._sequence = getattr(self, "_sequence", 0) + 1
        self.submitted_turns.clear()
        if mode == "guarded":
            self.approve_contract()
        else:
            self.prompt_submit("Verify the current task", "turn-2")
        self._payload = self.verify_gate([self.verification_argv()])
        return split_runner_command(self._payload["hookSpecificOutput"]["updatedInput"]["command"])

    def _run(self, tokens: list[str], execute: object) -> int:
        with self._runner_context():
            return CLICK_VERIFICATION._run_verification(
                tokens[5:], execute_commands=execute,
                git_workspace_snapshot=lambda *_args, **_kwargs: None,
                git_metadata_present=lambda _path: False,
            )

    def _change_state(self, tokens: list[str], field: str, value: object) -> None:
        path = Path(tokens[5])
        state = json.loads(path.read_text(encoding="utf-8"))
        target = state["verification"] if field == "mutation_revision" else state
        target[field] = value
        path.write_text(json.dumps(state), encoding="utf-8")

    def _assert_no_success(self, tokens: list[str]) -> None:
        state = json.loads(Path(tokens[5]).read_text(encoding="utf-8"))
        self.assertNotEqual(state["verification"]["status"], "passed")
        for source in state["evidence_state"]["sources"].values():
            self.assertNotEqual(source["status"], "passed")
            self.assertEqual(source["verified_revision"], -1)

    def test_normal_claim_and_record_preserve_both_authority_modes(self) -> None:
        for mode in ("evidence", "guarded"):
            with self.subTest(mode=mode):
                tokens = self._prepared(mode)
                execute = mock.Mock(return_value=0)
                self.assertEqual(self._run(tokens, execute), 0)
                execute.assert_called_once()
                state = json.loads(Path(tokens[5]).read_text(encoding="utf-8"))
                self.assertEqual(state["verification"]["status"], "passed")
                self.assertEqual(state["verification"]["runner_token_digest"], "")

    def test_malformed_revision_after_prepare_cannot_claim_or_execute(self) -> None:
        for mode in ("evidence", "guarded"):
            for value in (True, False, 0.0, "0", -1, None, [], {}):
                with self.subTest(mode=mode, revision=value):
                    tokens = self._prepared(mode)
                    self._change_state(tokens, "mutation_revision", value)
                    execute = mock.Mock(return_value=0)
                    self.assertEqual(self._run(tokens, execute), 2)
                    execute.assert_not_called()
                    self._assert_no_success(tokens)

    def test_malformed_revision_after_claim_cannot_record_success(self) -> None:
        for mode in ("evidence", "guarded"):
            for value in (True, False, 0.0, "0", -1, None, [], {}):
                with self.subTest(mode=mode, revision=value):
                    tokens = self._prepared(mode)
                    def execute(*_args: object, **_kwargs: object) -> int:
                        self._change_state(tokens, "mutation_revision", value)
                        return 0
                    self.assertNotEqual(self._run(tokens, execute), 0)
                    self._assert_no_success(tokens)

    def test_authority_changed_after_prepare_cannot_execute(self) -> None:
        for mode in ("evidence", "guarded"):
            for status in ("staged", "off"):
                with self.subTest(mode=mode, status=status):
                    tokens = self._prepared(mode)
                    self._change_state(tokens, "status", status)
                    execute = mock.Mock(return_value=0)
                    self.assertEqual(self._run(tokens, execute), 2)
                    execute.assert_not_called()
                    self._assert_no_success(tokens)

    def test_authority_changed_after_claim_cannot_record_success(self) -> None:
        for mode in ("evidence", "guarded"):
            changes = [("status", "staged"), ("status", "off"),
                       ("status", "approved" if mode == "evidence" else "evidence"),
                       ("contract_digest", "f" * 64), ("mutation_revision", 1)]
            for field, value in changes:
                with self.subTest(mode=mode, field=field, value=value):
                    tokens = self._prepared(mode)
                    def execute(*_args: object, **_kwargs: object) -> int:
                        self._change_state(tokens, field, value)
                        return 0
                    self.assertNotEqual(self._run(tokens, execute), 0)
                    self._assert_no_success(tokens)

    def test_task_identity_changed_after_claim_cannot_record_success(self) -> None:
        for mode, changes in (
            ("evidence", (("evidence_session_id", "evs_" + "f" * 32),)),
            ("guarded", (("contract_id", "ctr_" + "f" * 32),
                         ("staged_turn_id", "replacement-stage"),
                         ("approved_turn_id", "replacement-approval"))),
        ):
            for field, value in changes:
                with self.subTest(mode=mode, field=field):
                    tokens = self._prepared(mode)
                    def execute(*_args: object, **_kwargs: object) -> int:
                        self._change_state(tokens, field, value)
                        return 0
                    self.assertNotEqual(self._run(tokens, execute), 0)
                    self._assert_no_success(tokens)

    def test_collection_boundary_rechecks_authority_after_snapshot(self) -> None:
        changes = (("status", "off"), ("status", "evidence"),
                   ("mutation_revision", True), ("mutation_revision", 1),
                   ("contract_digest", "f" * 64),
                   ("contract_id", "ctr_" + "f" * 32),
                   ("staged_turn_id", "replacement-stage"),
                   ("approved_turn_id", "replacement-approval"))
        for when, field, value in (
            (when, field, value) for when in ("before", "during") for field, value in changes
        ):
            with self.subTest(when=when, field=field, value=value):
                tokens = self._prepared()
                raw, error = CLICK_CAPABILITY.decode_encoded_request(tokens[8], "verification")
                self.assertEqual(error, "")
                with self._runner_context():
                    with CLICK_STATE.state_lock():
                        batch, error = CLICK_VERIFICATION._claim_verification_run(Path(tokens[5]), raw, tokens[6], tokens[7])
                    self.assertEqual(error, "")
                    assert batch is not None
                    grouped, error = CLICK_VERIFICATION._verification_groups(batch)
                    self.assertEqual(error, "")
                    before = {"root": str(self.workspace), "digest": "1" * 64, "protected_untracked": []}
                    def snapshot(*_args: object, **_kwargs: object) -> dict:
                        if when == "during":
                            self._change_state(tokens, field, value)
                        return before
                    if when == "before":
                        self._change_state(tokens, field, value)
                    reason, _ = CLICK_VERIFICATION._collection_boundary_check(
                        Path(tokens[5]), tokens[6], tokens[7], next_source_key=next(iter(grouped)),
                        expected_claim_binding=batch.get("_click_claim_binding"),
                        grouped_checks=grouped, before=before,
                        verification_environment=batch["_click_verification_environment"],
                        file_content_digest=CLICK_VERIFICATION._file_content_digest,
                        git_workspace_snapshot=snapshot,
                    )
                    self.assertEqual(reason, "claim-or-cancellation-changed")

    def test_claim_atomic_write_failure_executes_nothing_and_can_retry(self) -> None:
        tokens = self._prepared()
        path = Path(tokens[5])
        before = path.read_bytes()
        execute = mock.Mock(return_value=0)
        replace = CLICK_STATE.os.replace
        def fail_replace(source: object, destination: object) -> None:
            if Path(destination) == path:
                raise OSError("injected primary state replace failure")
            replace(source, destination)
        with mock.patch.object(CLICK_STATE.os, "replace", side_effect=fail_replace):
            with self.assertRaises(OSError):
                self._run(tokens, execute)
        execute.assert_not_called()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self._run(tokens, execute), 0)
        execute.assert_called_once()

    def test_claim_recovery_write_failure_preserves_consumed_claim(self) -> None:
        tokens = self._prepared()
        path = Path(tokens[5])
        recovery = CLICK_STATE._recovery_snapshot_path(path)
        execute = mock.Mock(return_value=0)
        replace = CLICK_STATE.os.replace
        def fail_replace(source: object, destination: object) -> None:
            if Path(destination) == recovery:
                raise OSError("injected recovery replace failure")
            replace(source, destination)
        with mock.patch.object(CLICK_STATE.os, "replace", side_effect=fail_replace):
            with self.assertRaises(OSError):
                self._run(tokens, execute)
        execute.assert_not_called()
        self._assert_no_success(tokens)
        state = json.loads(path.read_text(encoding="utf-8"))
        self.assertGreater(state["verification"]["runner_claimed_at"], 0)
        self.assertEqual(self._run(tokens, execute), 2)
        execute.assert_not_called()

    def test_result_atomic_write_failure_cannot_publish_success_or_replay(self) -> None:
        tokens = self._prepared()
        path = Path(tokens[5])
        execute = mock.Mock(return_value=0)
        replace = CLICK_STATE.os.replace
        def fail_replace(source: object, destination: object) -> None:
            if Path(destination) == path:
                payload = json.loads(Path(source).read_text(encoding="utf-8"))
                if payload["verification"]["status"] == "passed":
                    raise OSError("injected result replace failure")
            replace(source, destination)
        with mock.patch.object(CLICK_STATE.os, "replace", side_effect=fail_replace):
            with self.assertRaises(OSError):
                self._run(tokens, execute)
        execute.assert_called_once()
        self._assert_no_success(tokens)
        self.assertEqual(self._run(tokens, execute), 2)
        execute.assert_called_once()


class ClickGateVerificationTests(ClickGateTestCase):
    def test_prediction_root_lookup_does_not_take_an_authority_snapshot(self) -> None:
        (self.workspace / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.approve_contract()
        self.hook_in_process = True
        with (
            mock.patch.object(
                CLICK_VERIFICATION, "git_workspace_snapshot",
                side_effect=AssertionError("a first plan needs no reuse snapshot"),
            ) as snapshot,
            mock.patch.object(
                CLICK_VERIFICATION, "git_capture", wraps=CLICK_VERIFICATION.git_capture,
            ) as capture,
        ):
            payload = self.verify_gate([self.verification_argv()])
        snapshot.assert_not_called()
        self.assertTrue(any(
            call.args[1] == ["rev-parse", "--show-toplevel"]
            for call in capture.call_args_list
        ))
        self.assertIn("updatedInput", payload["hookSpecificOutput"])
        self.assertEqual(self.run_rewritten(payload).returncode, 0)

    def test_evidence_status_distinguishes_execution_reuse_and_invalidation(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.prompt_submit("Evidence 검증 상태를 확인해줘", "turn-1")
        command = self.verification_argv()

        first = self.verify_gate([command], "turn-1")
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        first_status = self.pre_tool(
            "Bash",
            "click-gate status",
            "turn-1",
            submit_prompt=False,
            tool_use_id="status-after-first",
        )
        assert first_status is not None
        first_status_result = self.run_rewritten(first_status)
        self.assertEqual(
            first_status_result.returncode, 0, first_status_result.stderr
        )
        first_report = json.loads(first_status_result.stdout)
        self.assertEqual(first_report["task"]["runtime_mode"], "evidence")
        self.assertEqual(first_report["task"]["execution_authority"], "host")
        self.assertFalse(first_report["task"]["approval_bound"])
        self.assertEqual(first_report["summary"]["valid_check_count"], 1)
        self.assertEqual(first_report["summary"]["remaining_check_count"], 0)
        self.assertEqual(first_report["summary"]["actual_execution_count"], 1)
        self.assertEqual(first_report["summary"]["reused_check_count"], 0)
        self.assertEqual(first_report["checks"][0]["execution_status"], "executed")
        self.assertEqual(first_report["checks"][0]["outcome_status"], "passed")
        self.assertGreater(first_report["batch"]["executed_duration_ms"], 0)
        self.assertGreater(first_report["batch"]["request_wall_ms"], 0)

        reused = self.verify_gate([command], "turn-1")
        self.assertNotIn(
            "run-verification",
            split_runner_command(
                reused["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        reused_status = self.pre_tool(
            "Bash",
            "click-gate status",
            "turn-1",
            submit_prompt=False,
            tool_use_id="status-after-reuse",
        )
        assert reused_status is not None
        reused_status_result = self.run_rewritten(reused_status)
        self.assertEqual(
            reused_status_result.returncode, 0, reused_status_result.stderr
        )
        reused_report = json.loads(reused_status_result.stdout)
        self.assertEqual(reused_report["summary"]["actual_execution_count"], 0)
        self.assertEqual(reused_report["summary"]["reused_check_count"], 1)
        self.assertEqual(reused_report["checks"][0]["execution_status"], "reused")
        self.assertEqual(reused_report["checks"][0]["decision"], "reuse-exact")
        self.assertEqual(reused_report["batch"]["executed_duration_ms"], 0)
        self.assertGreater(reused_report["batch"]["request_wall_ms"], 0)
        self.assertGreater(reused_report["batch"]["estimated_avoided_ms"], 0)

        mutation_id = "status-mutation"
        self.assertIsNone(
            self.pre_tool(
                "apply_patch",
                "*** Begin Patch\n*** End Patch",
                "turn-1",
                submit_prompt=False,
                tool_use_id=mutation_id,
            )
        )
        fixture = self.workspace / "verification_fixture.py"
        fixture.write_text(
            fixture.read_text(encoding="utf-8") + "\n# invalidate the receipt\n",
            encoding="utf-8",
        )
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": "verification fixture changed"},
            turn_id="turn-1",
            tool_use_id=mutation_id,
        )
        stale_status = self.pre_tool(
            "Bash",
            "click-gate status",
            "turn-1",
            submit_prompt=False,
            tool_use_id="status-after-mutation",
        )
        assert stale_status is not None
        stale_status_result = self.run_rewritten(stale_status)
        self.assertEqual(
            stale_status_result.returncode, 0, stale_status_result.stderr
        )
        stale_report = json.loads(stale_status_result.stdout)
        self.assertEqual(stale_report["summary"]["valid_check_count"], 0)
        self.assertEqual(stale_report["summary"]["invalidated_check_count"], 1)
        self.assertEqual(stale_report["summary"]["remaining_check_count"], 1)
        self.assertEqual(stale_report["checks"][0]["current_state"], "invalidated")

        rerun = self.verify_gate([command], "turn-1")
        self.assertIn(
            "run-verification",
            split_runner_command(
                rerun["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        self.assertEqual(self.run_rewritten(rerun).returncode, 0)

    def test_evidence_status_reports_failed_and_unstarted_checks(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.prompt_submit("실패 뒤 남은 검증을 확인해줘", "turn-1")
        request = self.verify_gate(
            [self.verification_argv(1), self.verification_argv()],
            "turn-1",
            evidence_ids=["E1", "E2"],
        )
        self.assertNotEqual(self.run_rewritten(request).returncode, 0)

        status = self.pre_tool(
            "Bash",
            "click-gate status",
            "turn-1",
            submit_prompt=False,
            tool_use_id="status-after-failure",
        )
        assert status is not None
        status_result = self.run_rewritten(status)
        self.assertEqual(status_result.returncode, 0, status_result.stderr)
        report = json.loads(status_result.stdout)
        self.assertEqual(report["summary"]["remaining_check_count"], 2)
        self.assertEqual(report["summary"]["actual_execution_count"], 1)
        self.assertEqual(report["summary"]["not_run_check_count"], 1)
        self.assertEqual(
            {item["execution_status"] for item in report["checks"]},
            {"executed", "not-run"},
        )

    def test_guarded_successor_requires_new_approval_and_exports_contract_origin(self) -> None:
        (self.workspace / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.set_default("guarded", "turn-0")
        self.approve_contract()
        command = self.verification_argv()
        first = self.verify_gate([command], "turn-2")
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        state_path = next((self.plugin_data / "gate-state").glob("session-contract-*.json"))
        previous = json.loads(state_path.read_text(encoding="utf-8"))

        replacement = self.contract()
        replacement["outcome"] = "verify the separately approved follow-up"
        self.arm_gate("turn-3")
        staged = self.stage_gate(replacement, "turn-3")
        self.assertEqual(staged["hookSpecificOutput"]["permissionDecision"], "allow")
        current = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertNotEqual(current["contract_id"], previous["contract_id"])
        self.assertEqual(current["approved_turn_id"], "")
        self.assertEqual(current["verification"]["status"], "ready")
        self.assertNotIn("runner_token", current["verification"])
        self.assertEqual(next(iter(current["evidence_state"]["sources"].values()))["status"], "ready")
        self.assertTrue(current.get("successor_evidence"))
        denied = self.verify_gate([command], "turn-3")
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.arm_gate("turn-4")
        self.pass_gate(current["contract_id"], "turn-4")
        reused = self.verify_gate([command], "turn-4")
        self.assertNotIn("run-verification", split_runner_command(reused["hookSpecificOutput"]["updatedInput"]["command"]))
        exported = self.run_rewritten(self.pre_tool("Bash", "click-gate receipt export", "turn-4", submit_prompt=False))
        self.assertEqual(exported.returncode, 0, exported.stderr)
        body = json.loads(exported.stdout)["receipt"]
        self.assertEqual(body["version"], 5)
        self.assertEqual(body["contract"]["id"], current["contract_id"])
        self.assertEqual(body["contract"]["approved_turn_id"], "turn-4")
        self.assertEqual(body["evidence"][0]["lineage"]["origin_contract_id"], previous["contract_id"])
        self.assertEqual(body["evidence"][0]["lineage"]["requalification_mode"], "exact")

    def test_guarded_successor_partial_code_reuse_new_check_and_relevant_rerun(self) -> None:
        (self.workspace / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        beta = self.workspace / "beta.py"
        beta.write_text("import unittest\nVALUE = 1\nclass Beta(unittest.TestCase):\n    def test_value(self): self.assertGreater(VALUE, 0)\n", encoding="utf-8")
        command = self.verification_argv()
        beta_command = [sys.executable, "-m", "unittest", "beta"]
        policy = self.workspace / ".click" / "evidence-reuse.json"
        policy.parent.mkdir()
        policy.write_text(json.dumps({"version": 1, "entries": [{"checks": [command], "reuse_if_only_changed": ["beta.py"]}]}), encoding="utf-8")
        self.initialize_git(".gitignore", "verification_fixture.py", "beta.py", ".click/evidence-reuse.json")
        self.set_default("guarded", "turn-0")
        contract = self.contract()
        contract["verification"]["evidence"].append({"id": "E2", "kind": "argv", "description": "beta code behavior"})
        contract["verification"]["done_when"].append({"condition": "beta passes", "primary_evidence": "E2"})
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        first = self.verify_gate([command, beta_command], "turn-2", evidence_ids=["E1", "E2"])
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        origin_id = self.active_contract_id()
        contract["outcome"] = "check the next beta code change and a new validation"
        contract["verification"]["evidence"].extend([
            {"id": "E3", "kind": "argv", "description": "new validation"},
            {"id": "E-hold", "kind": "manual", "description": "finish after relevant-input scenario"},
        ])
        contract["verification"]["done_when"].extend([
            {"condition": "new validation passes", "primary_evidence": "E3"},
            {"condition": "relevant input scenario checked", "primary_evidence": "E-hold"},
        ])
        self.arm_gate("turn-3")
        self.stage_gate(contract, "turn-3")
        self.arm_gate("turn-4")
        self.pass_gate(turn_id="turn-4")
        self.assertIsNone(self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-4"))
        beta.write_text(beta.read_text(encoding="utf-8").replace("VALUE = 1", "VALUE = 2"), encoding="utf-8")
        self.tool_hook("post-tool", "apply_patch", {"patch": "beta code"}, turn_id="turn-4", tool_use_id="tool-1")
        request = self.verify_gate([command, beta_command, [*command, "-v"]], "turn-4", evidence_ids=["E1", "E2", "E3"])
        result = self.run_rewritten(request)
        self.assertEqual(result.returncode, 0, result.stderr)
        state_path = next((self.plugin_data / "gate-state").glob("session-contract-*.json"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
        batch = CLICK_VERIFICATION.click_incremental.current_batch(state["verification"])
        by_key = {source["source_key"]: source for source in batch["sources"]}
        alpha = by_key[CLICK_EVIDENCE.evidence_key("E1")]
        self.assertEqual(alpha["status"], "reused")
        self.assertEqual(alpha["decision"], "reuse-safe-change")
        self.assertEqual(alpha["reuse_origin"]["contract_id"], origin_id)
        for source_id in ("E2", "E3"):
            self.assertEqual(by_key[CLICK_EVIDENCE.evidence_key(source_id)]["status"], "passed")
        unknown = self.verify_gate([command], "turn-4", evidence_ids=["E_UNAPPROVED"])
        self.assertEqual(unknown["hookSpecificOutput"]["permissionDecision"], "deny")

        self.assertIsNone(self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-4"))
        fixture = self.workspace / "verification_fixture.py"
        fixture.write_text(fixture.read_text(encoding="utf-8") + "\n# related code changed\n", encoding="utf-8")
        self.tool_hook("post-tool", "apply_patch", {"patch": "related code"}, turn_id="turn-4", tool_use_id="tool-1")
        rerun = self.verify_gate([command], "turn-4")
        self.assertIn("run-verification", split_runner_command(rerun["hookSpecificOutput"]["updatedInput"]["command"]))
        self.assertEqual(self.run_rewritten(rerun).returncode, 0)
        source = json.loads(state_path.read_text(encoding="utf-8"))["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
        self.assertEqual(source["successor_reuse_count"], 0)
        self.assertEqual(source["last_successor_origin_contract_id"], "")

    def test_guarded_successor_shards_keep_original_timing_sample_across_reuse(self) -> None:
        parent = self.install_evidence_shard_fixture(safe_readme_reuse=True)
        self.set_default("guarded", "turn-0")
        self.approve_contract()

        first = self.verify_gate([parent], "turn-2")
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        origin_state = json.loads(state_path.read_text(encoding="utf-8"))
        origin_contract_id = origin_state["contract_id"]
        origin_batch = CLICK_VERIFICATION.click_incremental.current_batch(
            origin_state["verification"]
        )
        assert origin_batch is not None
        origin_baselines = {
            key: source["last_success_duration_baseline"]
            for key, source in origin_state["evidence_state"]["sources"].items()
        }
        self.assertEqual(len(origin_baselines), 2)
        for source_key, baseline in origin_baselines.items():
            origin_source = origin_state["evidence_state"]["sources"][source_key]
            self.assertTrue(
                CLICK_VERIFICATION.click_incremental.timing_baseline_is_valid(
                    baseline
                )
            )
            self.assertEqual(baseline["unit"], "ms")
            self.assertEqual(
                baseline["measurement_scope"],
                "source-command-dispatch-through-return",
            )
            self.assertEqual(baseline["source_key"], source_key)
            self.assertEqual(baseline["batch_id"], origin_batch["batch_id"])
            self.assertEqual(
                baseline["origin_task"],
                {"mode": "guarded", "id": origin_contract_id},
            )
            self.assertEqual(baseline["sample_count"], 1)
            self.assertEqual(
                baseline["timing_binding_digest"],
                CLICK_VERIFICATION.click_incremental.timing_binding_digest(
                    source_key=source_key,
                    check_digest=origin_source["verified_check_digest"],
                    environment_digest=origin_source[
                        "verified_environment_digest"
                    ],
                    executable_digest=origin_source[
                        "verified_executable_digest"
                    ],
                    host_coverage_digest=origin_source[
                        "verified_host_coverage"
                    ]["digest"],
                    observer_mode="off",
                ),
            )

        successor_contract = self.contract()
        successor_contract["outcome"] = "verify shard reuse after a safe code change"
        self.arm_gate("turn-3")
        self.stage_gate(successor_contract, "turn-3")
        staged = json.loads(state_path.read_text(encoding="utf-8"))
        successor_contract_id = staged["contract_id"]
        self.assertEqual(staged["approved_turn_id"], "")
        denied = self.verify_gate([parent], "turn-3")
        self.assertEqual(
            denied["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.arm_gate("turn-4")
        self.pass_gate(successor_contract_id, "turn-4")
        self.assertIsNone(
            self.pre_tool(
                "apply_patch",
                "*** Begin Patch\n*** End Patch",
                "turn-4",
                tool_use_id="phase1-readme",
            )
        )
        (self.workspace / "README.md").write_text("after\n", encoding="utf-8")
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": "readme safe change"},
            turn_id="turn-4",
            tool_use_id="phase1-readme",
        )

        reused = self.verify_gate([parent], "turn-4")
        self.assertNotIn(
            "run-verification",
            split_runner_command(
                reused["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        successor_state = json.loads(state_path.read_text(encoding="utf-8"))
        successor_batch = CLICK_VERIFICATION.click_incremental.current_batch(
            successor_state["verification"]
        )
        assert successor_batch is not None
        self.assertNotEqual(successor_batch["batch_id"], origin_batch["batch_id"])
        self.assertEqual(successor_batch["task"]["id"], successor_contract_id)
        for result in successor_batch["sources"]:
            source_key = result["source_key"]
            self.assertEqual(result["status"], "reused")
            self.assertEqual(result["decision"], "reuse-safe-change")
            self.assertEqual(result["duration_baseline"], origin_baselines[source_key])
            self.assertEqual(result["reuse_origin"]["contract_id"], origin_contract_id)
            self.assertEqual(
                successor_state["evidence_state"]["sources"][source_key][
                    "last_success_duration_baseline"
                ],
                origin_baselines[source_key],
            )

        repeated_contract = self.contract()
        repeated_contract["outcome"] = "verify the same shards in another approved task"
        self.arm_gate("turn-5")
        self.stage_gate(repeated_contract, "turn-5")
        repeated_staged = json.loads(state_path.read_text(encoding="utf-8"))
        repeated_contract_id = repeated_staged["contract_id"]
        self.arm_gate("turn-6")
        self.pass_gate(repeated_contract_id, "turn-6")
        repeated = self.verify_gate([parent], "turn-6")
        self.assertNotIn(
            "run-verification",
            split_runner_command(
                repeated["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        repeated_state = json.loads(state_path.read_text(encoding="utf-8"))
        repeated_batch = CLICK_VERIFICATION.click_incremental.current_batch(
            repeated_state["verification"]
        )
        assert repeated_batch is not None
        self.assertEqual(repeated_batch["task"]["id"], repeated_contract_id)
        for result in repeated_batch["sources"]:
            source_key = result["source_key"]
            baseline = result["duration_baseline"]
            self.assertEqual(result["status"], "reused")
            self.assertEqual(result["decision"], "reuse-exact")
            self.assertEqual(result["reuse_origin"]["contract_id"], successor_contract_id)
            self.assertEqual(baseline, origin_baselines[source_key])
            self.assertEqual(baseline["sample_count"], 1)
            self.assertEqual(
                baseline["observed_at"],
                origin_baselines[source_key]["observed_at"],
            )

    def test_successor_reuse_keeps_authority_when_timing_basis_is_unusable(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.set_default("guarded", "turn-0")
        commands = [
            self.verification_argv(),
            [
                sys.executable,
                "-m",
                "unittest",
                "-v",
                "verification_fixture.VerificationFixture.test_pass",
            ],
            [
                sys.executable,
                "-m",
                "unittest",
                "-b",
                "verification_fixture.VerificationFixture.test_pass",
            ],
        ]
        contract = self.contract()
        for evidence_id in ("E2", "E3"):
            contract["verification"]["evidence"].append(
                {
                    "id": evidence_id,
                    "kind": "argv",
                    "description": f"timing compatibility fixture {evidence_id}",
                }
            )
            contract["verification"]["done_when"].append(
                {
                    "condition": f"{evidence_id} fixture passes",
                    "primary_evidence": evidence_id,
                }
            )
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        first = self.verify_gate(
            commands, "turn-2", evidence_ids=["E1", "E2", "E3"]
        )
        self.assertEqual(self.run_rewritten(first).returncode, 0)

        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        origin = json.loads(state_path.read_text(encoding="utf-8"))
        sources = origin["evidence_state"]["sources"]
        keys = [CLICK_EVIDENCE.evidence_key(item) for item in ("E1", "E2", "E3")]
        sources[keys[0]].pop("last_success_duration_baseline", None)
        legacy_source = sources[keys[1]]
        legacy_baseline = legacy_source["last_success_duration_baseline"]
        legacy_source["last_success_duration_baseline"] = {
            field: legacy_baseline[field]
            for field in (
                "duration_ms",
                "revision",
                "check_digest",
                "observed_at",
                "batch_id",
                "sample_count",
            )
        }
        incompatible_source = sources[keys[2]]
        incompatible = incompatible_source["last_success_duration_baseline"]
        incompatible["observer_mode"] = "shadow"
        incompatible["timing_binding_digest"] = (
            CLICK_VERIFICATION.click_incremental.timing_binding_digest(
                source_key=keys[2],
                check_digest=incompatible["check_digest"],
                environment_digest=incompatible_source[
                    "verified_environment_digest"
                ],
                executable_digest=incompatible_source[
                    "verified_executable_digest"
                ],
                host_coverage_digest=incompatible_source[
                    "verified_host_coverage"
                ]["digest"],
                observer_mode="shadow",
            )
        )
        state_path.write_text(json.dumps(origin), encoding="utf-8")

        successor = json.loads(json.dumps(contract))
        successor["outcome"] = "reuse valid receipts without inventing timing"
        self.arm_gate("turn-3")
        self.stage_gate(successor, "turn-3")
        self.arm_gate("turn-4")
        self.pass_gate(turn_id="turn-4")
        reused = self.verify_gate(
            commands, "turn-4", evidence_ids=["E1", "E2", "E3"]
        )
        self.assertNotIn(
            "run-verification",
            split_runner_command(
                reused["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        current = json.loads(state_path.read_text(encoding="utf-8"))
        plan = current["verification"]["incremental_plan"]
        self.assertEqual(plan["planned_reuse_source_count"], 3)
        for decision in plan["decisions"]:
            self.assertEqual(decision["decision"], "reuse-exact")
            self.assertIsNone(decision["duration_baseline"])
            self.assertIsNone(decision["estimated_avoided_ms"])
        batch = CLICK_VERIFICATION.click_incremental.current_batch(
            current["verification"]
        )
        assert batch is not None
        self.assertTrue(all(item["status"] == "reused" for item in batch["sources"]))
        self.assertIsNone(
            CLICK_VERIFICATION.click_incremental.batch_summary(batch)[
                "estimated_avoided_ms"
            ]
        )
        current_sources = current["evidence_state"]["sources"]
        self.assertIsNone(current_sources[keys[0]].get("last_success_duration_baseline"))
        self.assertTrue(
            CLICK_VERIFICATION.click_incremental.baseline_is_valid(
                current_sources[keys[1]]["last_success_duration_baseline"]
            )
        )
        self.assertFalse(
            CLICK_VERIFICATION.click_incremental.timing_baseline_is_valid(
                current_sources[keys[1]]["last_success_duration_baseline"]
            )
        )
        self.assertEqual(
            current_sources[keys[2]]["last_success_duration_baseline"][
                "observer_mode"
            ],
            "shadow",
        )

    def test_guarded_successor_does_not_inherit_a_dependency_declaration(self) -> None:
        (self.workspace / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.set_default("guarded", "turn-0")
        contract = self.contract()
        contract["verification"]["evidence"][0]["dependencies"] = ["verification_fixture.py"]
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        command = self.verification_argv()
        self.assertEqual(self.run_rewritten(self.verify_gate([command], "turn-2")).returncode, 0)
        self.arm_gate("turn-3")
        self.stage_gate(self.contract(), "turn-3")
        self.arm_gate("turn-4")
        self.pass_gate(turn_id="turn-4")
        rerun = self.verify_gate([command], "turn-4")
        self.assertIn("run-verification", split_runner_command(rerun["hookSpecificOutput"]["updatedInput"]["command"]))
        self.assertEqual(self.run_rewritten(rerun).returncode, 0)
        state_path = next((self.plugin_data / "gate-state").glob("session-contract-*.json"))
        source = json.loads(state_path.read_text(encoding="utf-8"))["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
        self.assertEqual(source["dependency_patterns"], [])
        self.assertEqual(source["successor_reuse_count"], 0)

    def install_complete_dependency_observation(
        self,
        command: list[str],
        *,
        paths: list[str] | None = None,
    ) -> None:
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        source_key = CLICK_EVIDENCE.evidence_key("E1")
        sources = state["evidence_state"]["sources"]
        grouped_checks = {
            source_key: [
                {
                    "evidence_id": "E1",
                    "argv": command,
                    "class": "targeted",
                }
            ]
        }
        observation = (
            CLICK_VERIFICATION.click_dependency_cache.dependency_observation(
                paths or ["verification_fixture.py"]
            )
        )
        receipts = CLICK_VERIFICATION.click_dependency_cache.receipts_for_groups(
            self.workspace,
            grouped_checks,
            declarations=CLICK_VERIFICATION.dependency_declarations(
                sources, {source_key}
            ),
            observations={source_key: observation},
            git_capture=CLICK_VERIFICATION.git_capture,
        )
        CLICK_VERIFICATION.store_dependency_receipt(
            sources[source_key], receipts[source_key]
        )
        state_path.write_text(json.dumps(state), encoding="utf-8")

    def test_evidence_mode_dynamically_registers_and_exports_host_authority(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.prompt_submit("검증 가능한 작은 변경", "turn-1")

        allowed = self.verify_gate(
            [self.verification_argv()],
            turn_id="turn-1",
            evidence_ids=["E_RUNTIME"],
        )
        self.assertEqual(
            allowed["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        completed = self.run_rewritten(allowed)
        self.assertEqual(completed.returncode, 0, completed.stderr)

        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "evidence")
        self.assertEqual(state["evidence_state"]["source_count"], 1)
        source = next(iter(state["evidence_state"]["sources"].values()))
        self.assertEqual(source["status"], "passed")
        self.assertEqual(source["dependency_patterns"], [])

        receipt = self.pre_tool(
            "Bash", "click-gate receipt export", "turn-1", submit_prompt=False
        )
        self.assertIsNotNone(receipt)
        exported = self.run_rewritten(receipt)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        envelope = json.loads(exported.stdout)
        self.assertIsNone(envelope["receipt"]["contract"])
        self.assertEqual(
            envelope["receipt"]["authority"]["execution_authority"], "host"
        )
        self.assertFalse(
            envelope["receipt"]["authority"]["approval_bound"]
        )

    def test_follow_up_evidence_requalifies_exact_success_and_records_origin(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        command = self.verification_argv()
        self.prompt_submit("첫 Evidence 작업", "turn-1")
        first = self.verify_gate([command], "turn-1")
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        previous = json.loads(state_path.read_text(encoding="utf-8"))
        previous_session = previous["evidence_session_id"]
        previous_batch = previous["verification"]["incremental_batch_id"]

        self.prompt_submit("후속 Evidence 작업", "turn-2")
        successor = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertNotEqual(successor["evidence_session_id"], previous_session)
        self.assertNotEqual(successor["intent_digest"], previous["intent_digest"])
        self.assertNotIn("runner_token", successor["verification"])

        reused = self.verify_gate([command], "turn-2")
        rewritten = split_runner_command(
            reused["hookSpecificOutput"]["updatedInput"]["command"]
        )
        self.assertNotIn("run-verification", rewritten)
        current = json.loads(state_path.read_text(encoding="utf-8"))
        plan = current["verification"]["incremental_plan"]
        self.assertEqual(plan["decisions"][0]["decision"], "reuse-exact")
        self.assertEqual(
            plan["decisions"][0]["reason_code"], "successor-evidence-current"
        )
        batch = CLICK_VERIFICATION.click_incremental.current_batch(
            current["verification"]
        )
        assert batch is not None
        self.assertEqual(batch["sources"][0]["status"], "reused")
        self.assertEqual(
            batch["sources"][0]["reuse_origin"]["batch_id"], previous_batch
        )
        self.assertEqual(
            batch["sources"][0]["reuse_origin"]["evidence_session_id"],
            previous_session,
        )

        receipt = self.pre_tool(
            "Bash", "click-gate receipt export", "turn-2", submit_prompt=False
        )
        assert receipt is not None
        exported = self.run_rewritten(receipt)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        body = json.loads(exported.stdout)["receipt"]
        self.assertEqual(body["version"], 4)
        lineage = body["evidence"][0]["lineage"]
        self.assertEqual(lineage["mode"], "successor-reused")
        self.assertEqual(lineage["origin_batch_id"], previous_batch)
        self.assertEqual(lineage["origin_evidence_session_id"], previous_session)
        self.assertEqual(lineage["requalification_mode"], "exact")

    def test_follow_up_evidence_does_not_reuse_same_revision_for_changed_tree(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        command = self.verification_argv()
        self.prompt_submit("첫 Evidence 작업", "turn-1")
        first = self.verify_gate([command], "turn-1")
        self.assertEqual(self.run_rewritten(first).returncode, 0)

        self.prompt_submit("후속 Evidence 작업", "turn-2")
        with (self.workspace / "verification_fixture.py").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write("\n# unobserved workspace drift\n")
        repeated = self.verify_gate([command], "turn-2")
        rewritten = split_runner_command(
            repeated["hookSpecificOutput"]["updatedInput"]["command"]
        )
        self.assertIn("run-verification", rewritten)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        decision = state["verification"]["incremental_plan"]["decisions"][0]
        self.assertIn(decision["decision"], {"run", "not-evaluable"})
        batch = CLICK_VERIFICATION.click_incremental.current_batch(
            state["verification"]
        )
        assert batch is not None
        self.assertIsNone(batch["sources"][0]["reuse_origin"])

    def test_follow_up_evidence_rechecks_command_binding(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        command = self.verification_argv()
        self.prompt_submit("첫 Evidence 작업", "turn-1")
        first = self.verify_gate([command], "turn-1")
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        self.prompt_submit("다른 검사 요구", "turn-2")

        changed = [*command, "-v"]
        repeated = self.verify_gate([changed], "turn-2")
        self.assertIn(
            "run-verification",
            split_runner_command(
                repeated["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        decision = state["verification"]["incremental_plan"]["decisions"][0]
        self.assertEqual(decision["reason_code"], "check-binding-changed")

    def test_follow_up_evidence_rechecks_environment_binding(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        command = self.verification_argv()
        self.prompt_submit("첫 Evidence 작업", "turn-1")
        first = self.verify_gate([command], "turn-1")
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        self.prompt_submit("환경이 달라진 후속 작업", "turn-2")

        with mock.patch.dict(os.environ, {"CLICK_SUCCESSOR_VARIANT": "changed"}):
            repeated = self.verify_gate([command], "turn-2")
        self.assertIn(
            "run-verification",
            split_runner_command(
                repeated["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        decision = state["verification"]["incremental_plan"]["decisions"][0]
        self.assertEqual(decision["reason_code"], "environment-binding-changed")

    def test_verification_batch_has_no_fixed_check_count_cap(self) -> None:
        checks = [
            {
                "argv": ["git", "diff", "--check"],
                "class": "targeted",
            }
            for _ in range(9)
        ]

        batch, _, error = CLICK_VERIFICATION.validate_batch(
            json.dumps({"version": 2, "checks": checks}),
            "focused",
        )

        self.assertEqual(error, "")
        self.assertIsNotNone(batch)
        assert batch is not None
        self.assertEqual(len(batch["checks"]), 9)

    def test_verification_batch_has_no_arbitrary_character_cap(self) -> None:
        raw = json.dumps(
            {
                "version": 2,
                "checks": [
                    {
                        "argv": [
                            "git",
                            "diff",
                            "--check",
                            "--",
                            "x" * 6_500,
                        ],
                        "class": "targeted",
                    }
                ],
            }
        )
        self.assertGreater(len(raw), 6_000)

        batch, _, error = CLICK_VERIFICATION.validate_batch(raw, "focused")

        self.assertEqual(error, "")
        self.assertIsNotNone(batch)

    def test_quick_profile_does_not_control_verification_authority(self) -> None:
        contract = self.contract()
        contract["verification"]["scale"] = "quick"
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")

        command = self.verification_argv()
        allowed = self.verify_gate([command, command])
        self.assertEqual(
            allowed["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertNotIn("additionalContext", allowed["hookSpecificOutput"])
        completed = self.run_rewritten(allowed)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_split_argv_batches_bind_exact_groups_without_a_profile_ceiling(self) -> None:
        (self.workspace / "empty_tests").mkdir()
        (self.workspace / "empty_tests" / "test_pass.py").write_text(
            "import unittest\n\n"
            "class PassingTest(unittest.TestCase):\n"
            "    def test_pass(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        contract = self.contract()
        contract["verification"]["evidence"].append(
            {"id": "E2", "kind": "argv", "description": "second broad check"}
        )
        contract["verification"]["done_when"].append(
            {"condition": "second broad check passes", "primary_evidence": "E2"}
        )
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        broad_argv = [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "empty_tests",
        ]

        first = self.verify_gate([broad_argv], evidence_ids=["E1"])
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        second = self.verify_gate([broad_argv], evidence_ids=["E2"])
        self.assertEqual(
            second["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertNotIn("additionalContext", second["hookSpecificOutput"])
        self.assertEqual(self.run_rewritten(second).returncode, 0)

        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        sources = state["evidence_state"]["sources"]
        for evidence_id in ("E1", "E2"):
            source = sources[CLICK_EVIDENCE.evidence_key(evidence_id)]
            self.assertEqual(source["status"], "passed")
            self.assertRegex(source["reserved_check_digest"], r"^[0-9a-f]{64}$")

    def test_legacy_class_normalization_is_deterministic_but_non_authoritative(self) -> None:
        for command in (
            "pytest tests",
            "python3 -m pytest tests",
            "vitest run",
            "cargo nextest run",
            "cargo test --all",
            "go test ./...",
            "go test ./internal/...",
        ):
            with self.subTest(command=command):
                batch, units, error = CLICK_VERIFICATION.validate_batch(
                    json.dumps(
                        {
                            "version": 2,
                            "checks": [
                                {
                                    "argv": shlex.split(command),
                                    "class": "targeted",
                                }
                            ],
                        }
                    ),
                    "quick",
                )
                self.assertEqual(error, "")
                self.assertEqual(units, 3)
                self.assertEqual(batch["checks"][0]["class"], "broad")

        deep_batch, deep_units, error = CLICK_VERIFICATION.validate_batch(
            json.dumps(
                {
                    "version": 2,
                    "checks": [
                        {
                            "argv": ["npx", "playwright", "test"],
                            "class": "broad",
                        }
                    ],
                }
            ),
            "quick",
        )
        self.assertEqual(error, "")
        self.assertEqual(deep_units, 5)
        self.assertEqual(deep_batch["checks"][0]["class"], "deep")

    def test_hook_raises_underdeclared_verification_to_its_minimum_class(self) -> None:
        contract = self.contract()
        contract["verification"]["scale"] = "quick"
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")

        for argv in (
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            ["pytest", "-k", "not definitely_missing", "tests"],
            ["pytest", "tests/test_01.py", "tests/test_02.py"],
            [
                "pytest",
                "tests/integration/test_cancel.py::test_duplicate_cancel",
            ],
        ):
            with self.subTest(argv=argv):
                batch, units, error = CLICK_VERIFICATION.validate_batch(
                    json.dumps(
                        {
                            "version": 2,
                            "checks": [{"argv": argv, "class": "targeted"}],
                        }
                    ),
                    "quick",
                )
                self.assertEqual(error, "")
                self.assertEqual(units, 3)
                self.assertEqual(batch["checks"][0]["class"], "broad")

        allowed = self.verify_checks(
            [
                {
                    "argv": [
                        sys.executable,
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        "tests",
                    ],
                    "class": "targeted",
                }
            ]
        )
        self.assertEqual(
            allowed["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertNotIn("additionalContext", allowed["hookSpecificOutput"])

    def test_deep_class_normalization_does_not_create_authority(self) -> None:
        self.approve_contract()
        payload = self.verify_checks(
            [
                {
                    "argv": [
                        sys.executable,
                        "-m",
                        "coverage",
                        "run",
                        "-m",
                        "unittest",
                        "verification_fixture.VerificationFixture.test_pass",
                    ],
                    "class": "broad",
                }
            ]
        )
        self.assertEqual(
            payload["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertNotIn("additionalContext", payload["hookSpecificOutput"])

    def test_verification_classifier_uses_scope_before_kind_markers(self) -> None:
        cases = {
            ("pytest", "-k", "not definitely_missing", "tests"): "broad",
            ("pytest", "-m", "not slow", "tests"): "broad",
            ("pytest", "tests/test_01.py", "tests/test_02.py"): "broad",
            (
                "pytest",
                "tests/integration/test_cancel.py::test_duplicate_cancel",
            ): "broad",
            (
                "pytest",
                "tests/test_security_utils.py::test_parse_header",
            ): "broad",
            ("pytest", "tests/integration"): "deep",
            ("go", "test", "./pkg1", "./pkg2"): "broad",
            ("go", "test", "-run", ".*", "./pkg1"): "broad",
            ("ctest", "-R", ".*"): "broad",
            ("pre-commit", "run", "--files", "a.py", "b.py"): "broad",
            ("pre-commit", "run", "--files", "a.py"): "targeted",
        }
        for argv, expected in cases.items():
            with self.subTest(argv=argv):
                self.assertEqual(
                    CLICK_VERIFICATION.minimum_class(list(argv)), expected
                )

    def test_verification_classifier_supports_common_build_and_check_forms(self) -> None:
        cases = {
            ("py", "-3", "-m", "unittest", "pkg.Test.test_one"): "targeted",
            ("python3.10", "-m", "unittest", "pkg.Test.test_one"): "targeted",
            ("python3.13.exe", "-m", "pytest", "tests/test_one.py"): "targeted",
            ("/opt/python/bin/python3.14", "-m", "pytest", "tests"): "broad",
            ("uv", "run", "pytest", "tests/test_one.py"): "targeted",
            ("npm", "run", "lint"): "broad",
            ("npm", "run", "build"): "broad",
            ("ruff", "check", "."): "broad",
            ("ruff", "check", "src/one.py"): "targeted",
            ("mypy", "src"): "broad",
            ("mypy", "src/one.py"): "targeted",
            ("tsc", "--noEmit"): "broad",
            ("cargo", "check"): "broad",
            ("cargo", "clippy"): "broad",
            ("go", "vet", "./..."): "broad",
            ("node", "--check", "src/one.js"): "targeted",
            ("node", "--test", "tests/one.test.js"): "targeted",
            ("node", "--test"): "broad",
        }
        for argv, expected in cases.items():
            with self.subTest(argv=argv):
                self.assertEqual(
                    CLICK_VERIFICATION.minimum_class(list(argv)), expected
                )
        self.assertIsNone(
            CLICK_VERIFICATION.minimum_class(
                ["node", "--eval", "process.exit(0)"]
            )
        )
        self.assertIsNone(
            CLICK_VERIFICATION.minimum_class(
                ["python3.13-wrapper", "-m", "unittest", "tests"]
            )
        )

    def test_inline_and_direct_python_programs_are_not_verification(self) -> None:
        self.approve_contract()
        for argv in (
            [sys.executable, "-c", "raise SystemExit(0)"],
            [sys.executable, "verify_project.py"],
        ):
            with self.subTest(argv=argv):
                payload = self.verify_checks(
                    [{"argv": argv, "class": "targeted"}]
                )
                output = payload["hookSpecificOutput"]
                self.assertEqual(output["permissionDecision"], "deny")
                self.assertIn("neither read-only nor a recognized check", output["permissionDecisionReason"])

    def test_unknown_verification_wrapper_is_normalized_but_must_resolve(self) -> None:
        self.approve_contract()
        batch, units, error = CLICK_VERIFICATION.validate_batch(
            json.dumps(
                {
                    "version": 2,
                    "checks": [
                        {"argv": ["project-test"], "class": "targeted"}
                    ],
                }
            ),
            "focused",
        )
        self.assertEqual(error, "")
        self.assertEqual(units, 5)
        self.assertEqual(batch["checks"][0]["class"], "deep")

        payload = self.verify_checks(
            [{"argv": ["project-test"], "class": "targeted"}]
        )
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(
            "could not resolve and fingerprint",
            output["permissionDecisionReason"],
        )

    def test_verification_batch_rejects_legacy_shell_strings(self) -> None:
        self.approve_contract()
        payload = self.legacy_verify_gate(
            ["pytest tests/unit && pytest tests/integration"]
        )
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(
            "legacy shell-string",
            payload["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_successful_final_batch_is_not_repeated_until_code_changes(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        contract = self.contract()
        contract["verification"]["scale"] = "quick"
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        command = self.verification_argv()

        first = self.verify_gate([command])
        completed = self.run_rewritten(first)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        state_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (self.plugin_data / "gate-state").glob("*.json")
        )
        self.assertNotIn("raise SystemExit", state_text)
        repeated = self.verify_gate([command])
        self.assertEqual(
            repeated["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertIn(
            "reused 1 current unchanged-tree",
            repeated["hookSpecificOutput"]["updatedInput"]["command"],
        )
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        plan = state["verification"]["incremental_plan"]
        self.assertEqual(plan["planned_execution_source_count"], 0)
        actual = CLICK_VERIFICATION.click_incremental.summary(state["verification"])
        self.assertEqual(actual["executed_source_count"], 0)
        self.assertEqual(actual["exact_reuse_count"], 1)
        self.assertGreater(actual["estimated_avoided_ms"], 0)
        self.assertGreater(actual["measured_processing_ms"], 0)
        self.assertEqual(actual["executed_duration_ms"], 0)
        self.assertEqual(plan["decisions"][0]["decision"], "reuse-exact")
        self.assertEqual(
            plan["decisions"][0]["reason_code"],
            "same-revision-receipt-current",
        )
        history = state["verification"]["incremental_history"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[-1]["sources"][0]["decision"], "reuse-exact")
        self.assertEqual(history[-1]["sources"][0]["status"], "reused")
        source = state["evidence_state"]["sources"][
            CLICK_EVIDENCE.evidence_key("E1")
        ]
        self.assertGreater(source["last_success_duration_ms"], 0)

        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )
        stale_retry = self.verify_gate([command])
        self.assertEqual(
            stale_retry["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertIn(
            "run-verification",
            split_runner_command(
                stale_retry["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )

    def test_caller_installed_v1_receipt_does_not_reuse_across_revision(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        (self.workspace / "notes.md").write_text("before\n", encoding="utf-8")
        self.initialize_git(".gitignore", "verification_fixture.py", "notes.md")
        contract = self.contract()
        contract["verification"]["evidence"][0]["dependencies"] = [
            "verification_fixture.py"
        ]
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        command = self.verification_argv()

        first = self.verify_gate([command])
        completed = self.run_rewritten(first)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.install_complete_dependency_observation(command)
        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )
        (self.workspace / "notes.md").write_text("after\n", encoding="utf-8")
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": "notes"},
            tool_use_id="tool-1",
        )

        repeated = self.verify_gate([command])

        self.assertIn(
            "run-verification",
            split_runner_command(
                repeated["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        self.assertEqual(self.run_rewritten(repeated).returncode, 0)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        source = state["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
        self.assertEqual(state["verification"]["mutation_revision"], 1)
        self.assertEqual(source["status"], "passed")
        self.assertEqual(source["verified_revision"], 1)
        self.assertEqual(source["attempts"], 2)
        self.assertEqual(source["dependency_reuse_count"], 0)
        self.assertEqual(source["last_dependency_reused_from_revision"], -1)
        plan = state["verification"]["incremental_plan"]
        self.assertEqual(CLICK_VERIFICATION.click_incremental.summary(state["verification"])["dependency_reuse_count"], 0)
        self.assertEqual(plan["decisions"][0]["decision"], "not-evaluable")
        self.assertEqual(
            plan["decisions"][0]["reason_code"],
            "observer-incomplete",
        )

    def test_committed_safe_change_policy_reuses_without_runtime_observer(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        (self.workspace / "README.md").write_text("before\n", encoding="utf-8")
        policy = self.workspace / ".click" / "evidence-reuse.json"
        policy.parent.mkdir()
        command = self.verification_argv()
        policy.write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": [
                        {
                            "checks": [command],
                            "reuse_if_only_changed": ["README.md"],
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.initialize_git(
            ".gitignore",
            "verification_fixture.py",
            "README.md",
            ".click/evidence-reuse.json",
        )
        self.approve_contract()

        first = self.verify_gate([command])
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        baseline_state = json.loads(state_path.read_text(encoding="utf-8"))
        source_key = CLICK_EVIDENCE.evidence_key("E1")
        baseline_source = baseline_state["evidence_state"]["sources"][source_key]
        self.assertEqual(baseline_source["verified_dependency_observation"], {})
        self.assertEqual(
            baseline_source["verified_safe_change_receipt"]["provider"],
            "repository-safe-change-policy-v1",
        )

        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )
        (self.workspace / "README.md").write_text("after\n", encoding="utf-8")
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": "README"},
            tool_use_id="tool-1",
        )

        reused = self.verify_gate([command])

        self.assertEqual(reused["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertIn(
            "repository-declared safe-change cross-revision",
            reused["hookSpecificOutput"]["updatedInput"]["command"],
        )
        self.assertIn("README.md", json.dumps(reused))
        state = json.loads(state_path.read_text(encoding="utf-8"))
        source = state["evidence_state"]["sources"][source_key]
        self.assertEqual(source["status"], "passed")
        self.assertEqual(source["verified_revision"], 1)
        self.assertEqual(source["attempts"], 1)
        self.assertEqual(source["safe_change_reuse_count"], 1)
        self.assertEqual(source["last_safe_change_reused_from_revision"], 0)
        self.assertEqual(source["last_safe_change_paths"], ["README.md"])
        plan = state["verification"]["incremental_plan"]
        self.assertEqual(CLICK_VERIFICATION.click_incremental.summary(state["verification"])["safe_change_reuse_count"], 1)
        self.assertEqual(plan["decisions"][0]["decision"], "reuse-safe-change")
        self.assertEqual(
            plan["decisions"][0]["reason_code"], "safe-change-policy-covered"
        )

        receipt = self.pre_tool(
            "Bash", "click-gate receipt export", "turn-2", submit_prompt=False
        )
        self.assertIsNotNone(receipt)
        exported = self.run_rewritten(receipt)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        envelope = json.loads(exported.stdout)
        evidence = envelope["receipt"]["evidence"]
        self.assertEqual(evidence[0]["lineage"]["mode"], "dependency-reused")
        self.assertEqual(evidence[0]["lineage"]["from_revision"], 0)

    def test_safe_change_policy_reruns_when_code_is_also_changed(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        (self.workspace / "README.md").write_text("before\n", encoding="utf-8")
        policy = self.workspace / ".click" / "evidence-reuse.json"
        policy.parent.mkdir()
        command = self.verification_argv()
        policy.write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": [
                        {
                            "checks": [command],
                            "reuse_if_only_changed": ["README.md"],
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.initialize_git(
            ".gitignore",
            "verification_fixture.py",
            "README.md",
            ".click/evidence-reuse.json",
        )
        self.approve_contract()
        first = self.verify_gate([command])
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )
        (self.workspace / "README.md").write_text("after\n", encoding="utf-8")
        with (self.workspace / "verification_fixture.py").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write("\n# changed\n")
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": "mixed"},
            tool_use_id="tool-1",
        )

        repeated = self.verify_gate([command])

        self.assertIn(
            "run-verification",
            split_runner_command(
                repeated["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        decision = state["verification"]["incremental_plan"]["decisions"][0]
        self.assertEqual(decision["decision"], "run")
        self.assertEqual(
            decision["reason_code"], "safe-change-policy-not-covered"
        )
        rendered = json.dumps(repeated)
        self.assertIn("path-not-declared-safe", rendered)
        self.assertIn("verification_fixture.py", rendered)

    def test_v1_observation_cannot_activate_safe_change_policy(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        (self.workspace / "README.md").write_text("before\n", encoding="utf-8")
        policy = self.workspace / ".click" / "evidence-reuse.json"
        policy.parent.mkdir()
        command = self.verification_argv()
        policy.write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": [
                        {
                            "checks": [command],
                            "reuse_if_only_changed": ["README.md"],
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.initialize_git(
            ".gitignore",
            "verification_fixture.py",
            "README.md",
            ".click/evidence-reuse.json",
        )
        contract = self.contract()
        contract["verification"]["evidence"][0]["dependencies"] = [
            "verification_fixture.py",
            "README.md",
        ]
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        first = self.verify_gate([command])
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        self.install_complete_dependency_observation(
            command, paths=["verification_fixture.py", "README.md"]
        )
        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )
        (self.workspace / "README.md").write_text("after\n", encoding="utf-8")
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": "README"},
            tool_use_id="tool-1",
        )

        repeated = self.verify_gate([command])

        self.assertIn(
            "run-verification",
            split_runner_command(
                repeated["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        decision = state["verification"]["incremental_plan"]["decisions"][0]
        self.assertEqual(decision["decision"], "not-evaluable")
        self.assertEqual(decision["reason_code"], "observer-incomplete")
        self.assertNotIn("repository-declared safe-change", json.dumps(repeated))

    def test_unavailable_runtime_observation_reruns_after_any_revision(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        (self.workspace / "notes.md").write_text("before\n", encoding="utf-8")
        self.initialize_git(".gitignore", "verification_fixture.py", "notes.md")
        contract = self.contract()
        contract["verification"]["evidence"][0]["dependencies"] = [
            "verification_fixture.py"
        ]
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        command = self.verification_argv()
        first = self.verify_gate([command])
        self.assertEqual(self.run_rewritten(first).returncode, 0)

        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        source = state["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
        self.assertEqual(
            source["verified_dependency_observation"]["status"], "unavailable"
        )

        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )
        (self.workspace / "notes.md").write_text("after\n", encoding="utf-8")
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": "notes"},
            tool_use_id="tool-1",
        )

        repeated = self.verify_gate([command])

        self.assertIn(
            "run-verification",
            split_runner_command(
                repeated["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        decision = state["verification"]["incremental_plan"]["decisions"][0]
        self.assertEqual(decision["decision"], "not-evaluable")
        self.assertEqual(decision["reason_code"], "observer-incomplete")

    def test_incomplete_observation_cannot_fall_back_to_safe_change_skip(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        (self.workspace / "README.md").write_text("before\n", encoding="utf-8")
        command = self.verification_argv()
        policy = self.workspace / ".click" / "evidence-reuse.json"
        policy.parent.mkdir()
        policy.write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": [
                        {
                            "checks": [command],
                            "reuse_if_only_changed": ["README.md"],
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.initialize_git(
            ".gitignore",
            "verification_fixture.py",
            "README.md",
            ".click/evidence-reuse.json",
        )
        contract = self.contract()
        contract["verification"]["evidence"][0]["dependencies"] = [
            "verification_fixture.py"
        ]
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        first = self.verify_gate([command])
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )
        (self.workspace / "README.md").write_text("after\n", encoding="utf-8")
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": "README"},
            tool_use_id="tool-1",
        )

        repeated = self.verify_gate([command])

        rendered = split_runner_command(
            repeated["hookSpecificOutput"]["updatedInput"]["command"]
        )
        self.assertIn("run-verification", rendered)
        self.assertNotIn("repository-declared safe-change", json.dumps(repeated))
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        decision = state["verification"]["incremental_plan"]["decisions"][0]
        self.assertEqual(decision["decision"], "not-evaluable")
        self.assertEqual(decision["reason_code"], "observer-incomplete")

    def _prepare_approved_dependency_receipt(self) -> list[str]:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        (self.workspace / "notes.md").write_text("before\n", encoding="utf-8")
        self.initialize_git(".gitignore", "verification_fixture.py", "notes.md")
        contract = self.contract()
        contract["verification"]["evidence"][0]["dependencies"] = [
            "verification_fixture.py"
        ]
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        command = self.verification_argv()
        first = self.verify_gate([command])
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        self.install_complete_dependency_observation(command)
        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )
        return command

    def test_dependency_change_reruns_cross_revision_check(self) -> None:
        command = self._prepare_approved_dependency_receipt()
        with (self.workspace / "verification_fixture.py").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write("\n# dependency changed\n")
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": "dependency"},
            tool_use_id="tool-1",
        )

        repeated = self.verify_gate([command])

        self.assertIn(
            "run-verification",
            split_runner_command(
                repeated["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        decision = state["verification"]["incremental_plan"]["decisions"][0]
        self.assertEqual(decision["decision"], "not-evaluable")
        self.assertEqual(decision["reason_code"], "observer-incomplete")

    def test_environment_change_reruns_cross_revision_check(self) -> None:
        command = self._prepare_approved_dependency_receipt()
        (self.workspace / "notes.md").write_text("after\n", encoding="utf-8")
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": "notes"},
            tool_use_id="tool-1",
        )

        with mock.patch.dict(
            os.environ, {"CLICK_DEPENDENCY_TEST_ENV": "changed"}
        ):
            repeated = self.verify_gate([command])

        self.assertIn(
            "run-verification",
            split_runner_command(
                repeated["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        decision = state["verification"]["incremental_plan"]["decisions"][0]
        self.assertEqual(decision["decision"], "run")
        self.assertEqual(decision["reason_code"], "environment-binding-changed")

    def test_change_after_approved_mutation_receipt_disables_dependency_reuse(
        self,
    ) -> None:
        command = self._prepare_approved_dependency_receipt()
        (self.workspace / "notes.md").write_text("approved change\n", encoding="utf-8")
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": "notes"},
            tool_use_id="tool-1",
        )
        (self.workspace / "notes.md").write_text(
            "changed after PostToolUse\n", encoding="utf-8"
        )

        repeated = self.verify_gate([command])

        self.assertIn(
            "run-verification",
            split_runner_command(
                repeated["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )

    def test_unrelated_manifest_change_cannot_make_v1_receipt_authoritative(
        self,
    ) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        (self.workspace / "notes.md").write_text("notes\n", encoding="utf-8")
        (self.workspace / "other.md").write_text("other\n", encoding="utf-8")
        manifest_path = self.workspace / ".click" / "evidence-dependencies.json"
        manifest_path.parent.mkdir()
        command = self.verification_argv()

        def write_manifest(unrelated_path: str) -> None:
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": [
                            {
                                "checks": [command],
                                "paths": ["verification_fixture.py"],
                            },
                            {
                                "checks": [["python3", "-m", "pytest", "docs"]],
                                "paths": [unrelated_path],
                            },
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        write_manifest("notes.md")
        self.initialize_git(
            ".gitignore",
            "verification_fixture.py",
            "notes.md",
            "other.md",
            ".click/evidence-dependencies.json",
        )
        self.approve_contract()
        first = self.verify_gate([command])
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        self.install_complete_dependency_observation(command)
        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )
        write_manifest("other.md")
        subprocess.run(
            ["git", "add", ".click/evidence-dependencies.json"],
            cwd=self.workspace,
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Click Tests",
                "-c",
                "user.email=click-tests@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "unrelated dependency mapping",
            ],
            cwd=self.workspace,
            check=True,
        )
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": "manifest"},
            tool_use_id="tool-1",
        )

        repeated = self.verify_gate([command])

        self.assertIn(
            "run-verification",
            split_runner_command(
                repeated["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        decision = state["verification"]["incremental_plan"]["decisions"][0]
        self.assertEqual(decision["decision"], "not-evaluable")
        self.assertEqual(decision["reason_code"], "observer-incomplete")

    def test_legacy_class_change_does_not_invalidate_exact_argv_receipt(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.approve_contract()
        argv = self.verification_argv()

        first = self.verify_checks([{"argv": argv, "class": "targeted"}])
        completed = self.run_rewritten(first)
        self.assertEqual(completed.returncode, 0, completed.stderr)

        repeated = self.verify_checks([{"argv": argv, "class": "deep"}])
        self.assertEqual(
            repeated["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertIn(
            "reused 1 current unchanged-tree",
            repeated["hookSpecificOutput"]["updatedInput"]["command"],
        )

    def test_runner_only_environment_noise_does_not_invalidate_receipt(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.approve_contract()
        command = self.verification_argv()
        first = self.verify_gate([command])
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        prepared = json.loads(state_path.read_text(encoding="utf-8"))
        source_key = CLICK_EVIDENCE.evidence_key("E1")
        prepared_digest = prepared["verification"][
            "running_environment_digests"
        ][source_key]

        completed = self.run_rewritten(
            first,
            {"CLICK_RUNNER_ONLY_NOISE": "launcher-added"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        recorded = json.loads(state_path.read_text(encoding="utf-8"))
        source = recorded["evidence_state"]["sources"][source_key]
        self.assertEqual(source["verified_environment_digest"], prepared_digest)

        repeated = self.verify_gate([command])
        self.assertEqual(
            repeated["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertIn(
            "reused 1 current unchanged-tree",
            repeated["hookSpecificOutput"]["updatedInput"]["command"],
        )

    def test_prepared_environment_value_change_is_rebound_before_execution(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.approve_contract()
        with mock.patch.dict(
            os.environ, {"CLICK_TEST_ENVIRONMENT": "prepared-value"}
        ):
            first = self.verify_gate([self.verification_argv()])
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        prepared = json.loads(state_path.read_text(encoding="utf-8"))
        source_key = CLICK_EVIDENCE.evidence_key("E1")
        prepared_digest = prepared["verification"][
            "running_environment_digests"
        ][source_key]

        rewritten = first["hookSpecificOutput"]["updatedInput"]["command"]
        environment = os.environ.copy()
        environment["PLUGIN_DATA"] = str(self.plugin_data)
        environment["CLICK_CONFIG_HOME"] = str(self.plugin_data)
        environment["CLICK_TEST_ENVIRONMENT"] = "changed-before-runner"
        completed = subprocess.run(
            rewritten,
            shell=True,
            cwd=self.workspace,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "rebound to the current canonical environment", completed.stdout
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        source = state["evidence_state"]["sources"][source_key]
        self.assertEqual(state["verification"]["runner_claimed_at"], 0)
        self.assertEqual(source["status"], "passed")
        self.assertNotEqual(source["verified_environment_digest"], prepared_digest)
        serialized = state_path.read_text(encoding="utf-8")
        self.assertNotIn("CLICK_TEST_ENVIRONMENT", serialized)
        self.assertNotIn("prepared-value", serialized)

    def test_current_receipt_reruns_when_the_git_tree_changes_out_of_band(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.approve_contract()
        command = self.verification_argv()
        first = self.verify_gate([command])
        self.assertEqual(self.run_rewritten(first).returncode, 0)

        with (self.workspace / "verification_fixture.py").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write("\n# changed outside the matched mutation tools\n")

        repeated = self.verify_gate([command])
        self.assertEqual(
            repeated["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertIn(
            "run-verification",
            split_runner_command(
                repeated["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["verification"]["mutation_revision"], 1)

    def test_current_receipt_reruns_when_the_execution_environment_changes(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.approve_contract()
        command = self.verification_argv()
        first = self.verify_gate([command])
        self.assertEqual(self.run_rewritten(first).returncode, 0)

        with mock.patch.dict(os.environ, {"CLICK_TEST_ENVIRONMENT": "changed"}):
            repeated = self.verify_gate([command])
        self.assertEqual(
            repeated["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertIn(
            "run-verification",
            split_runner_command(
                repeated["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )

    def test_verification_environment_ignores_shell_bookkeeping(self) -> None:
        stable = {
            "PATH": os.environ.get("PATH", os.defpath),
            "CLICK_TEST_ENVIRONMENT": "stable",
        }
        with mock.patch.object(CLICK_GATE.os, "environ", stable):
            expected = CLICK_VERIFICATION.environment(cwd=self.workspace)
        noisy = {
            **stable,
            "_": "launcher",
            "__CF_USER_TEXT_ENCODING": "0x1F5:0x0:0x0",
            "CMDCMDLINE": "cmd.exe /c runner",
            "COMMAND_MODE": "unix2003",
            "LC_CTYPE": "UTF-8",
            "PLUGIN_ROOT": "/host/plugin/cache/click",
            "PROMPT": "$P$G",
            "SHLVL": "2",
            "=C:": "C:\\runner",
        }
        with mock.patch.object(CLICK_GATE.os, "environ", noisy):
            actual = CLICK_VERIFICATION.environment(cwd=self.workspace)

        self.assertEqual(actual, expected)
        self.assertEqual(actual["CLICK_TEST_ENVIRONMENT"], "stable")

    def test_windows_environment_binding_is_case_insensitive(self) -> None:
        reservation_nonce = "runner-nonce"
        executables = [
            {
                "name": "python.exe",
                "path": "C:\\Python\\python.exe",
                "size": 1,
                "mtime_ns": 1,
                "content_digest": "1" * 64,
            }
        ]
        with mock.patch.object(CLICK_GATE.os, "name", "nt"):
            binding = CLICK_VERIFICATION.environment_binding(
                {"Path": "C:\\Python", "Click_Test": "stable"}, reservation_nonce
            )
            projected, drifted, error = (
                CLICK_VERIFICATION.environment_from_binding(
                    binding,
                    reservation_nonce,
                    {
                        "PATH": "C:\\Python",
                        "CLICK_TEST": "stable",
                        "RUNNER_ONLY": "ignored",
                    },
                )
            )
            first_digest = CLICK_VERIFICATION.environment_digest_from_records(
                executables,
                cwd=self.workspace,
                environment={"Path": "C:\\Python", "Click_Test": "stable"},
            )
            second_digest = CLICK_VERIFICATION.environment_digest_from_records(
                executables,
                cwd=self.workspace,
                environment={"PATH": "C:\\Python", "CLICK_TEST": "stable"},
            )

        self.assertEqual(error, "")
        self.assertFalse(drifted)
        self.assertEqual(
            projected, {"PATH": "C:\\Python", "CLICK_TEST": "stable"}
        )
        self.assertEqual(first_digest, second_digest)

    def test_verification_environment_binding_recovers_missing_prepared_key(
        self,
    ) -> None:
        runner_token = "set-at-runtime"
        binding = CLICK_VERIFICATION.environment_binding(
            {"PATH": "/usr/bin", "HOOK_ONLY": "prepared"}, runner_token
        )

        projected, drifted, error = (
            CLICK_VERIFICATION.environment_from_binding(
                binding,
                runner_token,
                {"PATH": "/usr/bin", "RUNNER_ONLY": "ignored"},
            )
        )

        self.assertEqual(error, "")
        self.assertTrue(drifted)
        self.assertEqual(projected, {"PATH": "/usr/bin"})

    def test_receipt_fingerprint_resolves_relative_path_from_runner_cwd(self) -> None:
        tools = self.workspace / "tools"
        tools.mkdir()
        executable_name = "receipt-check.exe" if os.name == "nt" else "receipt-check"
        executable = tools / executable_name
        executable.write_bytes(b"receipt-check executable\n")
        executable.chmod(0o755)
        environment = os.environ.copy()
        environment["PATH"] = "tools"

        digest = CLICK_VERIFICATION.environment_digest(
            [{"argv": [executable_name], "class": "targeted"}],
            cwd=self.workspace,
            environment=environment,
        )

        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_relative_verifier_selection_uses_explicit_workspace(self) -> None:
        executable = self.workspace / "gradlew"
        executable.write_bytes(b"verification launcher\n")
        executable.chmod(0o755)
        relative = ".\\gradlew" if os.name == "nt" else "./gradlew"
        with mock.patch.object(CLICK_GATE.shutil, "which", return_value=relative):
            records = CLICK_VERIFICATION.executable_records(
                [{"argv": [relative], "class": "targeted"}],
                cwd=self.workspace,
                environment={"PATH": os.environ.get("PATH", os.defpath)},
            )

        self.assertIsNotNone(records)
        assert records is not None
        self.assertEqual(records[0]["_execution_path"], str(executable.absolute()))

    @unittest.skipIf(os.name == "nt", "symlink launcher semantics are POSIX-specific")
    def test_verification_pins_selected_symlink_launcher_not_its_target(self) -> None:
        tools = self.workspace / "tools"
        tools.mkdir()
        launcher = tools / "python3.13"
        launcher.symlink_to(Path(sys.executable).resolve())
        self.approve_contract()
        argv = [
            launcher.name,
            "-m",
            "unittest",
            "verification_fixture.VerificationFixture.test_pass",
        ]
        with mock.patch.dict(os.environ, {"PATH": str(tools)}):
            payload = self.verify_gate([argv])
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        raw, error = CLICK_CAPABILITY.decode_encoded_request(tokens[8], "verification")
        self.assertEqual(error, "")
        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
            "PATH": str(tools),
        }
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(CLICK_GATE.Path, "cwd", return_value=self.workspace),
        ):
            batch, claim_error = CLICK_GATE._claim_verification_run(
                Path(tokens[5]), raw, tokens[6], tokens[7]
            )
        self.assertEqual(claim_error, "")
        self.assertIsNotNone(batch)
        assert batch is not None
        self.assertTrue(
            os.path.samefile(batch["checks"][0]["argv"][0], launcher)
        )
        self.assertNotEqual(
            batch["checks"][0]["argv"][0], str(Path(sys.executable).resolve())
        )

    def test_rewritten_verification_runner_is_claimed_before_execution(self) -> None:
        self.approve_contract()
        payload = self.verify_gate([self.verification_argv()])
        command = payload["hookSpecificOutput"]["updatedInput"]["command"]
        tokens = split_runner_command(command)
        self.assertEqual(tokens[2], "--state-root")
        self.assertEqual(
            Path(tokens[3]).resolve(),
            (self.plugin_data / "gate-state").resolve(),
        )
        self.assertEqual(tokens[4], "run-verification")

        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(CLICK_GATE.Path, "cwd", return_value=self.workspace),
            mock.patch.object(CLICK_VERIFICATION, "git_workspace_snapshot", return_value=None),
            mock.patch.object(CLICK_INSPECTION, "git_metadata_present", return_value=False),
            mock.patch.object(
                CLICK_INSPECTION, "execute_argv_commands", return_value=0
            ) as execute,
        ):
            self.assertEqual(CLICK_GATE._run_verification(tokens[5:]), 0)
            self.assertEqual(CLICK_GATE._run_verification(tokens[5:]), 2)
        self.assertEqual(execute.call_count, 1)

    def test_runner_persists_each_group_before_starting_the_next_group(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.prompt_submit("두 검증 묶음 실행", "turn-1")
        command = self.verification_argv()
        payload = self.verify_checks(
            [
                {"evidence_id": "E_ALPHA", "argv": command, "class": "targeted"},
                {"evidence_id": "E_BETA", "argv": command, "class": "targeted"},
            ],
            turn_id="turn-1",
            bind_default=False,
        )
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        state_path = Path(tokens[5])
        alpha_key = CLICK_EVIDENCE.evidence_key("E_ALPHA")
        beta_key = CLICK_EVIDENCE.evidence_key("E_BETA")
        calls = 0

        def execute(
            _commands: list[list[str]], **_kwargs: object
        ) -> int:
            nonlocal calls
            calls += 1
            if calls == 2:
                live = json.loads(state_path.read_text(encoding="utf-8"))
                batch = CLICK_VERIFICATION.click_incremental.current_batch(
                    live["verification"]
                )
                assert batch is not None
                by_key = {item["source_key"]: item for item in batch["sources"]}
                self.assertEqual(by_key[alpha_key]["status"], "passed")
                self.assertTrue(by_key[alpha_key]["completed"])
                self.assertIsNotNone(by_key[alpha_key]["duration_ms"])
                self.assertEqual(by_key[beta_key]["status"], "planned")
            return 0

        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(
                CLICK_VERIFICATION.Path, "cwd", return_value=self.workspace
            ),
        ):
            result = CLICK_VERIFICATION.run(
                tokens[5:],
                file_content_digest=CLICK_VERIFICATION.file_content_digest,
                git_workspace_snapshot=CLICK_VERIFICATION.git_workspace_snapshot,
                git_metadata_present=CLICK_INSPECTION.git_metadata_present,
                execute_commands=execute,
                git_capture=CLICK_VERIFICATION.git_capture,
            )
        self.assertEqual(result, 0)
        self.assertEqual(calls, 2)
        completed = json.loads(state_path.read_text(encoding="utf-8"))
        batch = CLICK_VERIFICATION.click_incremental.current_batch(
            completed["verification"]
        )
        assert batch is not None
        self.assertEqual(
            {item["status"] for item in batch["sources"]}, {"passed"}
        )
        self.assertEqual(batch["version"], 5)
        self.assertTrue(
            all(
                command["status"] == "passed"
                for source in batch["sources"]
                for command in source["commands"]
            )
        )

    def test_runner_records_exact_commands_and_stops_after_the_first_failure(
        self,
    ) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.prompt_submit("한 묶음의 fail-fast 결과 기록", "turn-1")
        payload = self.verify_gate(
            [
                self.verification_argv(),
                self.verification_argv(1),
                self.verification_argv(),
            ],
            "turn-1",
            evidence_ids=["E_CHAIN", "E_CHAIN", "E_CHAIN"],
        )
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        execute = mock.Mock(side_effect=[0, 7, 0])
        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(
                CLICK_VERIFICATION.Path, "cwd", return_value=self.workspace
            ),
        ):
            result = CLICK_VERIFICATION.run(
                tokens[5:],
                file_content_digest=CLICK_VERIFICATION.file_content_digest,
                git_workspace_snapshot=CLICK_VERIFICATION.git_workspace_snapshot,
                git_metadata_present=CLICK_INSPECTION.git_metadata_present,
                execute_commands=execute,
                git_capture=CLICK_VERIFICATION.git_capture,
            )

        self.assertEqual(result, 7)
        self.assertEqual(execute.call_count, 2)
        state_path = Path(tokens[5])
        state = json.loads(state_path.read_text(encoding="utf-8"))
        batch = CLICK_VERIFICATION.click_incremental.current_batch(
            state["verification"]
        )
        assert batch is not None
        self.assertEqual(batch["status"], "failed")
        self.assertEqual(batch["version"], 5)
        source = batch["sources"][0]
        self.assertEqual(source["status"], "failed")
        self.assertEqual(
            [item["status"] for item in source["commands"]],
            ["passed", "failed", "not-run"],
        )
        self.assertEqual(
            [item["exit_code"] for item in source["commands"]],
            [0, 7, None],
        )
        evidence = state["evidence_state"]["sources"][
            CLICK_EVIDENCE.evidence_key("E_CHAIN")
        ]
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["last_exit_code"], 7)

    def test_synthetic_noncontiguous_results_cannot_create_false_receipts(
        self,
    ) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.prompt_submit("비연속 결과 기록 fixture", "turn-1")
        payload = self.verify_gate(
            [self.verification_argv()] * 3,
            "turn-1",
            evidence_ids=["E_ALPHA", "E_BETA", "E_MISSING"],
        )
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        state_path = Path(tokens[5])
        raw, decode_error = CLICK_CAPABILITY.decode_encoded_request(
            tokens[8], "verification"
        )
        self.assertEqual(decode_error, "")
        alpha_key = CLICK_EVIDENCE.evidence_key("E_ALPHA")
        beta_key = CLICK_EVIDENCE.evidence_key("E_BETA")
        missing_key = CLICK_EVIDENCE.evidence_key("E_MISSING")
        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(CLICK_GATE.Path, "cwd", return_value=self.workspace),
        ):
            with CLICK_STATE.state_lock():
                batch, claim_error = CLICK_GATE._claim_verification_run(
                    state_path, raw, tokens[6], tokens[7]
                )
            self.assertEqual(claim_error, "")
            self.assertIsNotNone(batch)
            assert batch is not None
            command_plans = batch.pop("_click_command_plans")
            source_results = CLICK_VERIFICATION.click_incremental.new_source_results(
                command_plans
            )
            for source_key, status, exit_code, offset in (
                (alpha_key, "failed", 7, 1),
                (beta_key, "passed", 0, 3),
            ):
                command_plan = command_plans[source_key][0]
                self.assertTrue(
                    CLICK_VERIFICATION.click_incremental.start_source_command(
                        source_results,
                        source_key,
                        position=command_plan["position"],
                        check_digest=command_plan["check_digest"],
                        started_offset_ms=offset,
                    )
                )
                self.assertTrue(
                    CLICK_VERIFICATION.click_incremental.complete_source_command(
                        source_results,
                        source_key,
                        position=command_plan["position"],
                        check_digest=command_plan["check_digest"],
                        status=status,
                        reason=(
                            "command-passed" if status == "passed"
                            else "command-failed"
                        ),
                        finished_offset_ms=offset + 1,
                        duration_ms=1,
                        exit_code=exit_code,
                    )
                )
            # The third source deliberately has no result at all. This fixture
            # models out-of-order/corrupt input; the real runner remains fail-fast.
            source_results.pop(missing_key)
            # Match the runner: discard execution helpers while retaining the
            # original claim context required at the result-recording boundary.
            for key in list(batch):
                if key.startswith("_click_") and key != "_click_claim_binding":
                    batch.pop(key)
            for check in batch["checks"]:
                approved_argv = check.pop("_click_approved_argv", None)
                if approved_argv:
                    check["argv"] = approved_argv
            snapshot = CLICK_VERIFICATION.git_workspace_snapshot(self.workspace)
            assert snapshot is not None
            with CLICK_STATE.state_lock():
                recorded = CLICK_VERIFICATION.record_result(
                    state_path,
                    batch,
                    tokens[6],
                    tokens[7],
                    # A legacy prefix count and last exit both claim success.
                    # Exact v5 command facts must still win.
                    0,
                    3,
                    workspace_changed=False,
                    workspace_root=str(snapshot["root"]),
                    workspace_digest=str(snapshot["digest"]),
                    source_durations_ms={alpha_key: 1, beta_key: 1},
                    source_results=source_results,
                    runner_started_ns=time.perf_counter_ns(),
                    observer_mode="off",
                )

        self.assertTrue(recorded)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        sources = state["evidence_state"]["sources"]
        self.assertEqual(sources[alpha_key]["status"], "failed")
        self.assertEqual(sources[alpha_key]["last_exit_code"], 7)
        self.assertEqual(sources[beta_key]["status"], "passed")
        self.assertEqual(sources[missing_key]["status"], "ready")
        self.assertEqual(state["verification"]["status"], "failed")
        batch = CLICK_VERIFICATION.click_incremental.current_batch(
            state["verification"]
        )
        assert batch is not None
        by_key = {item["source_key"]: item for item in batch["sources"]}
        self.assertEqual(by_key[alpha_key]["status"], "failed")
        self.assertEqual(by_key[beta_key]["status"], "passed")
        self.assertEqual(by_key[missing_key]["status"], "not-run")

    def test_late_completion_from_cancelled_batch_cannot_overwrite_successor_batch(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        command = self.verification_argv()
        self.prompt_submit("취소될 첫 작업", "turn-1")
        first = self.verify_gate([command], "turn-1")
        old_tokens = split_runner_command(
            first["hookSpecificOutput"]["updatedInput"]["command"]
        )
        state_path = Path(old_tokens[5])
        raw, error = CLICK_CAPABILITY.decode_encoded_request(
            old_tokens[8], "verification"
        )
        self.assertEqual(error, "")
        source_key = CLICK_EVIDENCE.evidence_key("E1")
        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(CLICK_GATE.Path, "cwd", return_value=self.workspace),
        ):
            with CLICK_STATE.state_lock():
                batch, claim_error = CLICK_GATE._claim_verification_run(
                    state_path, raw, old_tokens[6], old_tokens[7]
                )
            self.assertEqual(claim_error, "")
            self.assertIsNotNone(batch)
            assert batch is not None
            command_plan = batch["_click_command_plans"][source_key][0]
            CLICK_VERIFICATION._record_incremental_start(
                state_path,
                old_tokens[6],
                old_tokens[7],
                source_key,
                position=command_plan["position"],
                check_digest=command_plan["check_digest"],
                started_offset_ms=0,
            )
            CLICK_GATE.click_contract_state.clear_contract_state(
                {**self.base_event, "turn_id": "turn-1"}
            )

        self.prompt_submit("취소 뒤 새 작업", "turn-2")
        second = self.verify_gate([command], "turn-2")
        new_tokens = split_runner_command(
            second["hookSpecificOutput"]["updatedInput"]["command"]
        )
        self.assertEqual(Path(new_tokens[5]), state_path)
        before = state_path.read_bytes()

        with mock.patch.dict(os.environ, environment):
            CLICK_VERIFICATION._record_incremental_completion(
                state_path,
                old_tokens[6],
                old_tokens[7],
                source_key,
                position=command_plan["position"],
                check_digest=command_plan["check_digest"],
                status="passed",
                reason="command-passed",
                finished_offset_ms=9.5,
                duration_ms=9.5,
                source_duration_ms=9.5,
                exit_code=0,
            )

        self.assertEqual(state_path.read_bytes(), before)
        current = json.loads(before)
        self.assertEqual(
            current["verification"]["last_batch_digest"], new_tokens[6]
        )
        current_batch = CLICK_VERIFICATION.click_incremental.current_batch(
            current["verification"]
        )
        assert current_batch is not None
        self.assertEqual(current_batch["sources"][0]["status"], "planned")

    def test_verification_runner_rejects_prepared_workdir_mismatch(self) -> None:
        self.approve_contract()
        payload = self.verify_gate([self.verification_argv()])
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(
                CLICK_GATE.Path, "cwd", return_value=self.workspace.parent
            ),
            mock.patch.object(
                CLICK_INSPECTION, "execute_argv_commands", return_value=0
            ) as execute,
        ):
            self.assertEqual(CLICK_GATE._run_verification(tokens[5:]), 2)
        execute.assert_not_called()

        state_path = Path(tokens[5])
        state = json.loads(state_path.read_text(encoding="utf-8"))
        verification = state["verification"]
        self.assertEqual(verification["status"], "ready")
        self.assertEqual(verification["runner_claimed_at"], 0)
        self.assertEqual(verification["runner_token_digest"], "")

    def test_keyboard_interrupt_records_failure_and_releases_runner(self) -> None:
        self.approve_contract()
        self.pre_tool(
            "Bash", "click-gate observer shadow", "turn-2", submit_prompt=False
        )
        payload = self.verify_gate([self.verification_argv()])
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )

        def interrupt(*_args: object, **_kwargs: object) -> object:
            raise KeyboardInterrupt

        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(
                CLICK_VERIFICATION.Path, "cwd", return_value=self.workspace
            ),
        ):
            self.assertEqual(
                CLICK_VERIFICATION._run_verification(
                    tokens[5:],
                    git_workspace_snapshot=lambda *_args, **_kwargs: None,
                    git_metadata_present=lambda _path: False,
                    shadow_execute=interrupt,
                ),
                130,
            )

        state_path = Path(tokens[5])
        state = json.loads(state_path.read_text(encoding="utf-8"))
        verification = state["verification"]
        self.assertEqual(verification["status"], "failed")
        self.assertEqual(verification["last_exit_code"], 130)
        self.assertEqual(verification["runner_claimed_at"], 0)
        self.assertEqual(verification["runner_token_digest"], "")
        batch = CLICK_VERIFICATION.click_incremental.current_batch(verification)
        assert batch is not None
        self.assertEqual(batch["status"], "interrupted")
        self.assertEqual(batch["sources"][0]["status"], "unknown")
        self.assertIsNone(batch["sources"][0]["duration_ms"])
        self.assertEqual(
            [
                command["status"]
                for command in batch["sources"][0]["commands"]
            ],
            ["unknown"],
        )
        interrupted_source = state["evidence_state"]["sources"][
            CLICK_EVIDENCE.evidence_key("E1")
        ]
        self.assertFalse(
            CLICK_VERIFICATION.click_incremental.timing_baseline_is_valid(
                interrupted_source.get("last_success_duration_baseline")
            )
        )

    def test_shadow_observer_is_off_by_default(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.approve_contract()
        payload = self.verify_gate([self.verification_argv()])
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        execute = mock.Mock(return_value=0)
        shadow = mock.Mock()
        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(
                CLICK_VERIFICATION.Path, "cwd", return_value=self.workspace
            ),
        ):
            result = CLICK_VERIFICATION.run(
                tokens[5:],
                file_content_digest=CLICK_VERIFICATION.file_content_digest,
                git_workspace_snapshot=CLICK_VERIFICATION.git_workspace_snapshot,
                git_metadata_present=CLICK_INSPECTION.git_metadata_present,
                execute_commands=execute,
                git_capture=CLICK_VERIFICATION.git_capture,
                shadow_execute=shadow,
            )

        self.assertEqual(result, 0)
        execute.assert_called_once()
        shadow.assert_not_called()
        state = json.loads(Path(tokens[5]).read_text(encoding="utf-8"))
        self.assertEqual(state["verification"]["shadow_observer"]["records"], {})

    def test_shadow_observer_records_without_affecting_evidence_or_receipt(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.approve_contract()
        self.pre_tool(
            "Bash", "click-gate observer shadow", "turn-2", submit_prompt=False
        )
        payload = self.verify_gate([self.verification_argv()])
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        source_key = CLICK_EVIDENCE.evidence_key("E1")
        execute = mock.Mock(return_value=0)

        def shadow(argv: list[str], **kwargs: object) -> object:
            self.assertEqual(argv[1:3], ["-m", "unittest"])
            execute_unobserved = kwargs["execute_unobserved"]
            assert callable(execute_unobserved)
            exit_code = execute_unobserved()
            record = CLICK_VERIFICATION.click_dependency_cache.shadow_observer_record(
                evidence_key=str(kwargs["evidence_key"]),
                check_digest=str(kwargs["check_digest"]),
                mutation_revision=int(kwargs["mutation_revision"]),
                backend_name="strace",
                backend_version="6.1",
                backend_digest="c" * 64,
                inputs=[
                    {
                        "path": "shadow-only-input.py",
                        "kind": "file",
                        "operations": ["read"],
                    }
                ],
                command_duration_ms=3,
                observer_overhead_ms=1,
            )
            return CLICK_VERIFICATION.click_observer_common.ShadowExecution(
                exit_code=exit_code,
                record=record,
            )

        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(
                CLICK_VERIFICATION.Path, "cwd", return_value=self.workspace
            ),
        ):
            result = CLICK_VERIFICATION.run(
                tokens[5:],
                file_content_digest=CLICK_VERIFICATION.file_content_digest,
                git_workspace_snapshot=CLICK_VERIFICATION.git_workspace_snapshot,
                git_metadata_present=CLICK_INSPECTION.git_metadata_present,
                execute_commands=execute,
                git_capture=CLICK_VERIFICATION.git_capture,
                shadow_execute=shadow,
            )

        self.assertEqual(result, 0)
        execute.assert_called_once()
        state_path = Path(tokens[5])
        state = json.loads(state_path.read_text(encoding="utf-8"))
        source = state["evidence_state"]["sources"][source_key]
        self.assertEqual(source["status"], "passed")
        shadow_state = state["verification"]["shadow_observer"]
        self.assertEqual(shadow_state["version"], 1)
        record = shadow_state["records"][source_key]
        self.assertEqual(
            record["binding"]["check_digest"], source["locked_check_digest"]
        )
        self.assertFalse(record["authoritative"])
        self.assertFalse(record["reuse_authorized"])

        receipt_payload = self.pre_tool(
            "Bash", "click-gate receipt export", "turn-2", submit_prompt=False
        )
        assert receipt_payload is not None
        exported = self.run_rewritten(receipt_payload)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertNotIn("shadow_observer", exported.stdout)
        self.assertNotIn("shadow-only-input.py", exported.stdout)

    def test_shadow_prediction_is_fixed_before_rerun_and_evaluated_afterward(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        (self.workspace / "notes.md").write_text("before\n", encoding="utf-8")
        self.initialize_git(".gitignore", "verification_fixture.py", "notes.md")
        self.approve_contract()
        self.pre_tool(
            "Bash", "click-gate observer shadow", "turn-2", submit_prompt=False
        )
        source_key = CLICK_EVIDENCE.evidence_key("E1")
        execute = mock.Mock(return_value=0)

        def run_once(expected_decision: str) -> tuple[Path, dict[str, object]]:
            payload = self.verify_gate([self.verification_argv()])
            tokens = split_runner_command(
                payload["hookSpecificOutput"]["updatedInput"]["command"]
            )
            action_index = tokens.index("run-verification")
            arguments = tokens[action_index + 1 :]
            state_path = Path(arguments[0])

            def shadow(argv: list[str], **kwargs: object) -> object:
                prepared = json.loads(state_path.read_text(encoding="utf-8"))
                entry = prepared["verification"]["shadow_intelligence"]["sources"][
                    source_key
                ]
                self.assertEqual(entry["prediction"]["decision"], expected_decision)
                self.assertEqual(entry["evaluation"], {})
                execute_unobserved = kwargs["execute_unobserved"]
                assert callable(execute_unobserved)
                exit_code = execute_unobserved()
                record = CLICK_VERIFICATION.click_dependency_cache.shadow_observer_record(
                    evidence_key=str(kwargs["evidence_key"]),
                    check_digest=str(kwargs["check_digest"]),
                    mutation_revision=int(kwargs["mutation_revision"]),
                    backend_name="strace",
                    backend_version="6.1",
                    backend_digest="c" * 64,
                    inputs=[
                        {
                            "path": "verification_fixture.py",
                            "kind": "file",
                            "operations": ["read"],
                        }
                    ],
                    command_duration_ms=25,
                    observer_overhead_ms=2,
                )
                return CLICK_VERIFICATION.click_observer_common.ShadowExecution(
                    exit_code=exit_code,
                    record=record,
                )

            environment = {
                "PLUGIN_DATA": str(self.plugin_data),
                "CLICK_CONFIG_HOME": str(self.plugin_data),
            }
            with (
                mock.patch.dict(os.environ, environment),
                mock.patch.object(
                    CLICK_VERIFICATION.Path, "cwd", return_value=self.workspace
                ),
            ):
                result = CLICK_VERIFICATION.run(
                    arguments,
                    file_content_digest=CLICK_VERIFICATION.file_content_digest,
                    git_workspace_snapshot=CLICK_VERIFICATION.git_workspace_snapshot,
                    git_metadata_present=CLICK_INSPECTION.git_metadata_present,
                    execute_commands=execute,
                    git_capture=CLICK_VERIFICATION.git_capture,
                    shadow_execute=shadow,
                )
            self.assertEqual(result, 0)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            return state_path, state

        state_path, first_state = run_once("not-evaluable")
        first_entry = first_state["verification"]["shadow_intelligence"]["sources"][
            source_key
        ]
        self.assertTrue(
            CLICK_VERIFICATION.click_shadow_intelligence.baseline_is_valid(
                first_entry["baseline"]
            )
        )
        self.assertEqual(first_entry["evaluation"]["outcome"], "not-evaluable")

        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )
        (self.workspace / "notes.md").write_text("after\n", encoding="utf-8")
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": "notes"},
            tool_use_id="tool-1",
        )

        _, second_state = run_once("reuse-candidate")
        second_entry = second_state["verification"]["shadow_intelligence"]["sources"][
            source_key
        ]
        self.assertEqual(second_entry["evaluation"]["outcome"], "confirmed-candidate")
        self.assertEqual(second_entry["evaluation"]["actual_saved_ms"], 0)
        self.assertEqual(second_entry["evaluation"]["gross_potential_ms"], 25)
        self.assertEqual(execute.call_count, 2)

        receipt_payload = self.pre_tool(
            "Bash", "click-gate receipt export", "turn-2", submit_prompt=False
        )
        assert receipt_payload is not None
        exported = self.run_rewritten(receipt_payload)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertNotIn("shadow_intelligence", exported.stdout)
        self.assertNotIn("shadow_dashboard", exported.stdout)
        self.assertNotIn("verification_fixture.py", exported.stdout)

    def test_record_result_ignores_supplied_dependency_observation(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        contract = self.contract()
        contract["verification"]["evidence"][0]["dependencies"] = [
            "verification_fixture.py"
        ]
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        payload = self.verify_gate([self.verification_argv()])
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        state_path = Path(tokens[5])
        raw, error = CLICK_CAPABILITY.decode_encoded_request(
            tokens[8], "verification"
        )
        self.assertEqual(error, "")
        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }
        with (
            mock.patch.dict(CLICK_GATE.os.environ, environment),
            mock.patch.object(CLICK_GATE.Path, "cwd", return_value=self.workspace),
        ):
            with CLICK_STATE.state_lock():
                batch, claim_error = CLICK_GATE._claim_verification_run(
                    state_path, raw, tokens[6], tokens[7]
                )
            self.assertEqual(claim_error, "")
            assert batch is not None
            batch.pop("_click_verification_environment")
            batch.pop("_click_verification_environment_rebound")
            command_plans = batch.pop("_click_command_plans")
            snapshot = CLICK_VERIFICATION.git_workspace_snapshot(self.workspace)
            assert snapshot is not None
            source_key = CLICK_EVIDENCE.evidence_key("E1")
            source_results = CLICK_VERIFICATION.click_incremental.new_source_results(
                command_plans
            )
            command_plan = command_plans[source_key][0]
            self.assertTrue(
                CLICK_VERIFICATION.click_incremental.start_source_command(
                    source_results,
                    source_key,
                    position=command_plan["position"],
                    check_digest=command_plan["check_digest"],
                    started_offset_ms=1,
                )
            )
            self.assertTrue(
                CLICK_VERIFICATION.click_incremental.complete_source_command(
                    source_results,
                    source_key,
                    position=command_plan["position"],
                    check_digest=command_plan["check_digest"],
                    status="passed",
                    reason="command-passed",
                    finished_offset_ms=2,
                    duration_ms=1,
                    exit_code=0,
                )
            )
            observation = (
                CLICK_VERIFICATION.click_dependency_cache.dependency_observation(
                    ["verification_fixture.py"]
                )
            )
            with CLICK_STATE.state_lock():
                recorded = CLICK_GATE._record_verification_result(
                    state_path,
                    batch,
                    tokens[6],
                    tokens[7],
                    0,
                    1,
                    workspace_root=str(snapshot["root"]),
                    workspace_digest=str(snapshot["digest"]),
                    dependency_observations={source_key: observation},
                    source_durations_ms={source_key: 1},
                    source_results=source_results,
                )

        self.assertTrue(recorded)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        source = state["evidence_state"]["sources"][source_key]
        self.assertNotEqual(source["verified_dependency_observation"], observation)
        self.assertEqual(
            source["verified_dependency_observation"]["status"], "unavailable"
        )
        self.assertRegex(
            source["verified_dependency_observation_digest"], r"^[0-9a-f]{64}$"
        )

    def test_verification_runner_rejects_executable_change_before_execution(self) -> None:
        self.approve_contract()
        payload = self.verify_gate([self.verification_argv()])
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(CLICK_GATE.Path, "cwd", return_value=self.workspace),
            mock.patch.object(CLICK_VERIFICATION, "file_content_digest", return_value="0" * 64),
            mock.patch.object(
                CLICK_INSPECTION, "execute_argv_commands", return_value=0
            ) as execute,
        ):
            self.assertEqual(CLICK_GATE._run_verification(tokens[5:]), 2)
        execute.assert_not_called()

        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["verification"]["status"], "ready")
        self.assertEqual(state["verification"]["runner_claimed_at"], 0)
        self.assertEqual(state["verification"]["runner_token_digest"], "")
        source = state["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
        self.assertEqual(source["status"], "ready")

    def test_verification_environment_mismatch_rebinds_before_execution(
        self,
    ) -> None:
        self.approve_contract()
        payload = self.verify_gate([self.verification_argv()])
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        state_path = Path(tokens[5])
        with (
            mock.patch.dict(
                CLICK_GATE.os.environ,
                {
                    "PLUGIN_DATA": str(self.plugin_data),
                    "CLICK_CONFIG_HOME": str(self.plugin_data),
                    "HOME": str(self.workspace / "changed-home"),
                },
            ),
            mock.patch.object(
                CLICK_INSPECTION, "execute_argv_commands", return_value=0
            ) as execute,
            mock.patch.object(CLICK_GATE.Path, "cwd", return_value=self.workspace),
            mock.patch.object(CLICK_VERIFICATION, "git_workspace_snapshot", return_value=None),
            mock.patch.object(CLICK_INSPECTION, "git_metadata_present", return_value=False),
        ):
            self.assertEqual(CLICK_GATE._run_verification(tokens[5:]), 0)
        execute.assert_called_once()

        state = json.loads(state_path.read_text(encoding="utf-8"))
        verification = state["verification"]
        self.assertEqual(verification["status"], "passed")
        self.assertEqual(verification["runner_token_digest"], "")
        self.assertEqual(verification["runner_claimed_at"], 0)
        self.assertEqual(verification["running_evidence_keys"], [])
        source = state["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
        self.assertEqual(source["status"], "passed")
        self.assertEqual(source["unchanged_failure_retries"], 0)

    def test_verification_runner_rejects_tampered_environment_binding(self) -> None:
        self.approve_contract()
        payload = self.verify_gate([self.verification_argv()])
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["verification"]["running_environment_binding"].pop()
        state_path.write_text(json.dumps(state), encoding="utf-8")

        completed = self.run_rewritten(payload)

        self.assertEqual(completed.returncode, 2)
        self.assertIn("environment binding was malformed", completed.stderr)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        verification = state["verification"]
        self.assertEqual(verification["status"], "ready")
        self.assertEqual(verification["runner_claimed_at"], 0)
        self.assertEqual(verification["runner_token_digest"], "")
        source = state["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
        self.assertEqual(source["status"], "ready")

    def test_tampered_verification_token_does_not_release_reservation(self) -> None:
        self.approve_contract()
        payload = self.verify_gate([self.verification_argv()])
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        state_path = Path(tokens[5])
        tokens[7] = "tampered-runner-token"
        with (
            mock.patch.dict(
                CLICK_GATE.os.environ,
                {
                    "PLUGIN_DATA": str(self.plugin_data),
                    "CLICK_CONFIG_HOME": str(self.plugin_data),
                },
            ),
            mock.patch.object(
                CLICK_INSPECTION, "execute_argv_commands", return_value=0
            ) as execute,
        ):
            self.assertEqual(CLICK_GATE._run_verification(tokens[5:]), 2)
        execute.assert_not_called()

        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["verification"]["status"], "running")
        self.assertEqual(state["verification"]["runner_claimed_at"], 0)
        self.assertNotEqual(state["verification"]["runner_token_digest"], "")

    def test_claimed_verification_replay_does_not_release_active_runner(self) -> None:
        self.approve_contract()
        payload = self.verify_gate([self.verification_argv()])
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        state_path = Path(tokens[5])
        raw, error = CLICK_CAPABILITY.decode_encoded_request(tokens[8], "verification")
        self.assertEqual(error, "")
        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }
        with (
            mock.patch.dict(CLICK_GATE.os.environ, environment),
            mock.patch.object(CLICK_GATE.Path, "cwd", return_value=self.workspace),
        ):
            with CLICK_STATE.state_lock():
                batch, claim_error = CLICK_GATE._claim_verification_run(
                    state_path, raw, tokens[6], tokens[7]
                )
            self.assertEqual(claim_error, "")
            self.assertIsNotNone(batch)
            self.assertEqual(CLICK_GATE._run_verification(tokens[5:]), 2)

        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["verification"]["status"], "running")
        self.assertGreater(state["verification"]["runner_claimed_at"], 0)

    def test_verification_result_rejects_lost_context_bindings(self) -> None:
        for field in (
            "running_environment_digests",
            "running_environment_binding_digest",
            "running_executable_digests",
        ):
            with self.subTest(field=field):
                self.plugin_data = Path(self.temporary.name) / f"plugin-{field}"
                self.submitted_turns.clear()
                self.approve_contract()
                payload = self.verify_gate([self.verification_argv()])
                tokens = split_runner_command(
                    payload["hookSpecificOutput"]["updatedInput"]["command"]
                )
                raw, error = CLICK_CAPABILITY.decode_encoded_request(
                    tokens[8], "verification"
                )
                self.assertEqual(error, "")
                environment = {
                    "PLUGIN_DATA": str(self.plugin_data),
                    "CLICK_CONFIG_HOME": str(self.plugin_data),
                }
                with (
                    mock.patch.dict(CLICK_GATE.os.environ, environment),
                    mock.patch.object(
                        CLICK_GATE.Path, "cwd", return_value=self.workspace
                    ),
                ):
                    batch, claim_error = CLICK_GATE._claim_verification_run(
                        Path(tokens[5]), raw, tokens[6], tokens[7]
                    )
                self.assertEqual(claim_error, "")
                self.assertIsNotNone(batch)
                assert batch is not None
                batch.pop("_click_verification_environment")

                state_path = Path(tokens[5])
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["verification"][field] = {}
                state_path.write_text(json.dumps(state), encoding="utf-8")
                with (
                    mock.patch.dict(CLICK_GATE.os.environ, environment),
                    mock.patch.object(
                        CLICK_GATE.Path, "cwd", return_value=self.workspace
                    ),
                ):
                    recorded = CLICK_GATE._record_verification_result(
                        state_path,
                        batch,
                        tokens[6],
                        tokens[7],
                        0,
                        1,
                        workspace_root=str(self.workspace),
                        workspace_digest="1" * 64,
                    )
                self.assertFalse(recorded)

    def test_verification_runner_rejects_tampered_source_reservation(self) -> None:
        self.approve_contract()
        payload = self.verify_gate([self.verification_argv()])
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        state_path = Path(tokens[5])
        state = json.loads(state_path.read_text(encoding="utf-8"))
        source = state["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
        source["reserved_check_digest"] = "0" * 64
        state_path.write_text(json.dumps(state), encoding="utf-8")

        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }
        with (
            mock.patch.dict(CLICK_GATE.os.environ, environment),
            mock.patch.object(CLICK_GATE.Path, "cwd", return_value=self.workspace),
            mock.patch.object(CLICK_INSPECTION, "execute_argv_commands") as execute,
        ):
            self.assertEqual(CLICK_GATE._run_verification(tokens[5:]), 2)
        execute.assert_not_called()

    def test_source_reservation_survives_mutation_and_prevents_check_swapping(self) -> None:
        self.approve_contract()
        first = self.verify_gate([self.verification_argv(1)])
        self.assertEqual(self.run_rewritten(first).returncode, 1)
        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )

        changed = self.verify_gate([self.verification_argv()])
        self.assertEqual(
            changed["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn(
            "reserved to a different exact check set",
            changed["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_rewritten_verification_uses_bound_root_not_ambient_plugin_data(self) -> None:
        self.approve_contract()
        payload = self.verify_gate([self.verification_argv()])
        command = payload["hookSpecificOutput"]["updatedInput"]["command"]
        environment = os.environ.copy()
        environment.pop("PLUGIN_DATA", None)
        environment.pop("CLICK_CONFIG_HOME", None)
        first = subprocess.run(
            command,
            shell=True,
            cwd=self.workspace,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        replay = subprocess.run(
            command,
            shell=True,
            cwd=self.workspace,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(replay.returncode, 2)
        self.assertIn("no longer authorized", replay.stderr)

    def test_verification_runner_rejects_stale_or_future_reservation_before_execution(self) -> None:
        for label, started_at in (
            (
                "expired",
                int(time.time()) - CLICK_GATE.VERIFY_RUNNING_TTL_SECONDS - 1,
            ),
            ("future", int(time.time()) + 60),
        ):
            with self.subTest(label=label):
                self.plugin_data = Path(self.temporary.name) / f"plugin-{label}"
                self.submitted_turns.clear()
                self.approve_contract()
                payload = self.verify_gate([self.verification_argv()])
                tokens = split_runner_command(
                    payload["hookSpecificOutput"]["updatedInput"]["command"]
                )
                state_path = Path(tokens[5])
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["verification"]["started_at"] = started_at
                state_path.write_text(json.dumps(state), encoding="utf-8")
                with (
                    mock.patch.dict(
                        CLICK_GATE.os.environ,
                        {"PLUGIN_DATA": str(self.plugin_data)},
                    ),
                    mock.patch.object(
                        CLICK_INSPECTION, "execute_argv_commands"
                    ) as execute,
                ):
                    self.assertEqual(CLICK_GATE._run_verification(tokens[5:]), 2)
                execute.assert_not_called()

    def test_verification_fails_closed_when_git_snapshot_cannot_be_established(self) -> None:
        self.approve_contract()
        payload = self.verify_gate([self.verification_argv()])
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }
        with (
            mock.patch.dict(CLICK_GATE.os.environ, environment),
            mock.patch.object(CLICK_GATE.Path, "cwd", return_value=self.workspace),
            mock.patch.object(CLICK_VERIFICATION, "git_workspace_snapshot", return_value=None),
            mock.patch.object(CLICK_INSPECTION, "git_metadata_present", return_value=True),
            mock.patch.object(CLICK_INSPECTION, "execute_argv_commands") as execute,
        ):
            self.assertEqual(CLICK_GATE._run_verification(tokens[5:]), 2)
        execute.assert_not_called()

    def test_claimed_verification_never_auto_expires_while_result_is_unknown(self) -> None:
        self.approve_contract()
        first = self.verify_gate([self.verification_argv()])
        self.assertEqual(first["hookSpecificOutput"]["permissionDecision"], "allow")
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["verification"]["runner_claimed_at"] = 1
        state["verification"]["started_at"] = 1
        state_path.write_text(json.dumps(state), encoding="utf-8")

        repeated = self.verify_gate([self.verification_argv()])
        self.assertEqual(
            repeated["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn(
            "already running",
            repeated["hookSpecificOutput"]["permissionDecisionReason"],
        )
        current = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(current["verification"]["status"], "running")
        self.assertEqual(current["verification"]["runner_claimed_at"], 1)

    def test_stateful_runner_prefix_rejects_non_gate_state_root(self) -> None:
        self.approve_contract()
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        ).resolve()
        arguments, error = CLICK_GATE._runner_arguments(
            [
                "--state-root",
                str(self.plugin_data.resolve()),
                "run-verification",
                str(state_path),
            ]
        )
        self.assertEqual(arguments, [])
        self.assertIn("invalid", error)

    def test_stateful_runner_requires_one_canonical_bound_state_root(self) -> None:
        self.approve_contract()
        state_root = (self.plugin_data / "gate-state").resolve()
        state_path = next(state_root.glob("session-contract-*.json")).resolve()

        for action in CLICK_GATE.STATEFUL_RUNNER_ACTIONS:
            with self.subTest(action=action):
                with mock.patch.dict(
                    CLICK_GATE.os.environ,
                    {"PLUGIN_DATA": str(Path(self.temporary.name) / "wrong-root")},
                ):
                    arguments, error = CLICK_GATE._runner_arguments(
                        ["--state-root", str(state_root), action, str(state_path)]
                    )
                    self.assertEqual(error, "")
                    self.assertEqual(arguments[:2], [action, str(state_path)])
                    self.assertEqual(
                        CLICK_GATE.os.environ["PLUGIN_DATA"],
                        str(state_root.parent),
                    )

        bare, error = CLICK_GATE._runner_arguments(
            ["run-mutation", str(state_path)]
        )
        self.assertEqual(bare, [])
        self.assertIn("requires", error)

        relative, error = CLICK_GATE._runner_arguments(
            ["--state-root", "gate-state", "run-mutation", str(state_path)]
        )
        self.assertEqual(relative, [])
        self.assertIn("invalid", error)

        missing_root = Path(self.temporary.name) / "missing" / "gate-state"
        missing, error = CLICK_GATE._runner_arguments(
            [
                "--state-root",
                str(missing_root),
                "run-mutation",
                str(state_path),
            ]
        )
        self.assertEqual(missing, [])
        self.assertIn("could not be resolved", error)

        other_root = Path(self.temporary.name) / "other" / "gate-state"
        other_root.mkdir(parents=True)
        mismatched, error = CLICK_GATE._runner_arguments(
            [
                "--state-root",
                str(other_root.resolve()),
                "run-mutation",
                str(state_path),
            ]
        )
        self.assertEqual(mismatched, [])
        self.assertIn("does not match", error)

        alias = Path(self.temporary.name) / "gate-state-alias"
        try:
            alias.symlink_to(state_root, target_is_directory=True)
        except OSError:
            alias = None
        if alias is not None:
            symlinked, error = CLICK_GATE._runner_arguments(
                ["--state-root", str(alias), "run-mutation", str(state_path)]
            )
            self.assertEqual(symlinked, [])
            self.assertIn("invalid", error)

        state_alias = Path(self.temporary.name) / "session-contract-alias.json"
        try:
            state_alias.symlink_to(state_path)
        except OSError:
            state_alias = None
        if state_alias is not None:
            aliased_state, error = CLICK_GATE._runner_arguments(
                [
                    "--state-root",
                    str(state_root),
                    "run-mutation",
                    str(state_alias),
                ]
            )
            self.assertEqual(aliased_state, [])
            self.assertIn("does not match", error)

    def test_verification_that_changes_repository_content_cannot_pass(self) -> None:
        (self.workspace / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        (self.workspace / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.workspace / "mutating_test.py").write_text(
            "import unittest\n"
            "from pathlib import Path\n\n"
            "class MutatingTest(unittest.TestCase):\n"
            "    def test_mutates_source(self):\n"
            "        Path('app.py').write_text('VALUE = 2\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        self.initialize_git(".gitignore", "app.py", "mutating_test.py")

        contract = self.contract()
        contract["verification"]["scale"] = "quick"
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        payload = self.verify_checks(
            [
                {
                    "argv": [
                        sys.executable,
                        "-m",
                        "unittest",
                        "mutating_test.MutatingTest.test_mutates_source",
                    ],
                    "class": "targeted",
                }
            ]
        )
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("changed protected repository content", result.stderr)

        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        verification = state["verification"]
        self.assertEqual(verification["status"], "failed")
        self.assertTrue(verification["workspace_changed"])
        self.assertEqual(verification["mutation_revision"], 1)
        self.assertNotEqual(
            verification["verified_revision"], verification["mutation_revision"]
        )

        retry = self.verify_checks(
            [
                {
                    "argv": [
                        sys.executable,
                        "-m",
                        "unittest",
                        "mutating_test.MutatingTest.test_mutates_source",
                    ],
                    "class": "targeted",
                }
            ]
        )
        self.assertEqual(retry["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("code mutation", retry["hookSpecificOutput"]["permissionDecisionReason"])

    def test_workspace_changing_failure_does_not_mark_unrun_evidence_executed(self) -> None:
        (self.workspace / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        (self.workspace / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.workspace / "mutating_failure_test.py").write_text(
            "import unittest\n"
            "from pathlib import Path\n\n"
            "class MutatingFailureTest(unittest.TestCase):\n"
            "    def test_mutates_then_fails(self):\n"
            "        Path('app.py').write_text('VALUE = 2\\n', encoding='utf-8')\n"
            "        self.fail('expected failure after mutation')\n",
            encoding="utf-8",
        )
        self.initialize_git(".gitignore", "app.py", "mutating_failure_test.py")

        contract = self.contract()
        contract["verification"]["evidence"].append(
            {"id": "E2", "kind": "argv", "description": "unrun compatibility check"}
        )
        contract["verification"]["done_when"].append(
            {"condition": "compatibility remains", "primary_evidence": "E2"}
        )
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")

        payload = self.verify_gate(
            [
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "mutating_failure_test.MutatingFailureTest.test_mutates_then_fails",
                ],
                self.verification_argv(),
            ],
            evidence_ids=["E1", "E2"],
        )
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 1, result.stderr)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        sources = state["evidence_state"]["sources"]
        executed = sources[CLICK_EVIDENCE.evidence_key("E1")]
        unrun = sources[CLICK_EVIDENCE.evidence_key("E2")]
        self.assertEqual((executed["status"], executed["attempts"]), ("failed", 1))
        self.assertEqual((unrun["status"], unrun["attempts"]), ("ready", 0))
        self.assertEqual(unrun["unchanged_failure_retries"], 0)

    def test_verification_protects_preexisting_untracked_content(self) -> None:
        (self.workspace / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        (self.workspace / "local-settings.txt").write_text("safe\n", encoding="utf-8")
        (self.workspace / "untracked_mutating_test.py").write_text(
            "import unittest\n"
            "from pathlib import Path\n\n"
            "class UntrackedMutatingTest(unittest.TestCase):\n"
            "    def test_mutates_existing_file(self):\n"
            "        Path('local-settings.txt').write_text('changed\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        self.initialize_git(".gitignore", "untracked_mutating_test.py")
        contract = self.contract()
        contract["verification"]["scale"] = "quick"
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")

        payload = self.verify_checks(
            [
                {
                    "argv": [
                        sys.executable,
                        "-m",
                        "unittest",
                        (
                            "untracked_mutating_test.UntrackedMutatingTest."
                            "test_mutates_existing_file"
                        ),
                    ],
                    "class": "targeted",
                }
            ]
        )
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("changed protected repository content", result.stderr)

    def test_verification_detects_content_committed_during_the_batch(self) -> None:
        (self.workspace / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        (self.workspace / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.workspace / "committing_test.py").write_text(
            "import subprocess\n"
            "import unittest\n"
            "from pathlib import Path\n\n"
            "class CommittingTest(unittest.TestCase):\n"
            "    def test_commits_source_change(self):\n"
            "        Path('app.py').write_text('VALUE = 2\\n', encoding='utf-8')\n"
            "        subprocess.run(['git', 'add', 'app.py'], check=True)\n"
            "        subprocess.run([\n"
            "            'git', '-c', 'user.name=Click Tests', '-c',\n"
            "            'user.email=click-tests@example.invalid', 'commit', '--quiet',\n"
            "            '-m', 'verification mutation'\n"
            "        ], check=True)\n",
            encoding="utf-8",
        )
        self.initialize_git(".gitignore", "app.py", "committing_test.py")
        contract = self.contract()
        contract["verification"]["scale"] = "quick"
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")

        payload = self.verify_checks(
            [
                {
                    "argv": [
                        sys.executable,
                        "-m",
                        "unittest",
                        "committing_test.CommittingTest.test_commits_source_change",
                    ],
                    "class": "targeted",
                }
            ]
        )
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("changed protected repository content", result.stderr)
        clean_diff = subprocess.run(
            ["git", "diff", "--quiet"], cwd=self.workspace, check=False
        )
        self.assertEqual(clean_diff.returncode, 0)

    def test_new_untracked_verification_artifact_fails_stale(self) -> None:
        (self.workspace / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        (self.workspace / "artifact_test.py").write_text(
            "import unittest\n"
            "from pathlib import Path\n\n"
            "class ArtifactTest(unittest.TestCase):\n"
            "    def test_writes_disposable_report(self):\n"
            "        Path('new-report.tmp').write_text('result\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        self.initialize_git(".gitignore", "artifact_test.py")
        contract = self.contract()
        contract["verification"]["scale"] = "quick"
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")

        payload = self.verify_checks(
            [
                {
                    "argv": [
                        sys.executable,
                        "-m",
                        "unittest",
                        "artifact_test.ArtifactTest.test_writes_disposable_report",
                    ],
                    "class": "targeted",
                }
            ]
        )
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("new non-ignored untracked path", result.stderr)
        self.assertTrue((self.workspace / "new-report.tmp").exists())
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["verification"]["status"], "failed")
        self.assertTrue(state["verification"]["workspace_changed"])
        self.assertEqual(state["verification"]["mutation_revision"], 1)

    def test_new_source_path_created_during_verification_fails_stale(self) -> None:
        self.assertTrue(CLICK_VERIFICATION.new_untracked_is_suspicious("src/new_feature.py"))
        self.assertTrue(CLICK_VERIFICATION.new_untracked_is_suspicious("config/policy.json"))
        self.assertTrue(CLICK_VERIFICATION.new_untracked_is_suspicious("migration/001.sql"))
        self.assertTrue(
            CLICK_VERIFICATION.new_untracked_is_suspicious("packages/api/lib/new_rule.py")
        )
        self.assertFalse(CLICK_VERIFICATION.new_untracked_is_suspicious("new-report.tmp"))
        self.assertFalse(
            CLICK_VERIFICATION.new_untracked_is_suspicious("reports/app/output.txt")
        )
        (self.workspace / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        (self.workspace / "source_creating_test.py").write_text(
            "import unittest\n"
            "from pathlib import Path\n\n"
            "class SourceCreatingTest(unittest.TestCase):\n"
            "    def test_creates_source(self):\n"
            "        Path('src').mkdir(exist_ok=True)\n"
            "        Path('src/new_feature.py').write_text('VALUE = 1\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        self.initialize_git(".gitignore", "source_creating_test.py")
        contract = self.contract()
        contract["verification"]["scale"] = "quick"
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")

        payload = self.verify_checks(
            [
                {
                    "argv": [
                        sys.executable,
                        "-m",
                        "unittest",
                        "source_creating_test.SourceCreatingTest.test_creates_source",
                    ],
                    "class": "targeted",
                }
            ]
        )
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("new non-ignored untracked path", result.stderr)
        self.assertIn("classification is informational", result.stderr)

    def test_running_batch_blocks_parallel_mutation_and_verification(self) -> None:
        contract = self.contract()
        contract["verification"]["scale"] = "quick"
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        self.verify_gate([self.verification_argv()])

        for tool_name, command in (
            ("apply_patch", "*** Begin Patch\n*** End Patch"),
            ("Bash", "pytest tests/test_inventory.py"),
        ):
            with self.subTest(tool_name=tool_name):
                payload = self.pre_tool(tool_name, command, "turn-2")
                self.assertEqual(
                    payload["hookSpecificOutput"]["permissionDecision"], "deny"
                )
                self.assertIn(
                    "verification batch is running",
                    payload["hookSpecificOutput"]["permissionDecisionReason"],
                )

    def test_unclaimed_expired_verification_does_not_consume_source_attempt(self) -> None:
        self.approve_contract()
        self.verify_gate([self.verification_argv()])
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["verification"]["started_at"] = (
            int(time.time()) - CLICK_GATE.VERIFY_RUNNING_TTL_SECONDS - 1
        )
        self.assertEqual(state["verification"]["runner_claimed_at"], 0)
        original_source = state["evidence_state"]["sources"][
            CLICK_EVIDENCE.evidence_key("E1")
        ]
        reserved_digest = original_source["reserved_check_digest"]
        reserved_units = original_source["reserved_units"]
        original_binding = state["verification"]["running_environment_binding"]
        original_token_digest = state["verification"]["runner_token_digest"]
        state_path.write_text(json.dumps(state), encoding="utf-8")

        retry = self.verify_gate([self.verification_argv()])
        self.assertEqual(retry["hookSpecificOutput"]["permissionDecision"], "allow")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        source = state["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
        self.assertEqual(source["status"], "running")
        self.assertEqual(source["attempts"], 0)
        self.assertEqual(source["unchanged_failure_retries"], 0)
        self.assertEqual(source["reserved_check_digest"], reserved_digest)
        self.assertEqual(source["reserved_units"], reserved_units)
        verification = state["verification"]
        source_key = CLICK_EVIDENCE.evidence_key("E1")
        for field in (
            "running_environment_digests",
            "running_executable_digests",
        ):
            self.assertEqual(set(verification[field]), {source_key})
            self.assertRegex(verification[field][source_key], r"^[0-9a-f]{64}$")
        self.assertNotEqual(
            verification["running_environment_binding"], original_binding
        )
        self.assertNotEqual(verification["runner_token_digest"], original_token_digest)

    def test_failed_batch_advises_after_unchanged_retry(self) -> None:
        contract = self.contract()
        contract["verification"]["scale"] = "quick"
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        command = self.verification_argv(exit_code=1)

        first = self.verify_gate([command])
        self.assertEqual(self.run_rewritten(first).returncode, 1)
        transient_retry = self.verify_gate([command])
        self.assertEqual(self.run_rewritten(transient_retry).returncode, 1)
        blocked = self.verify_gate([command])
        self.assert_verification_advisory(
            blocked, "already failed twice"
        )
        self.assertEqual(self.run_rewritten(blocked).returncode, 1)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        failed_source = json.loads(state_path.read_text(encoding="utf-8"))[
            "evidence_state"
        ]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
        self.assertFalse(
            CLICK_VERIFICATION.click_incremental.timing_baseline_is_valid(
                failed_source.get("last_success_duration_baseline")
            )
        )

        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )
        after_fix = self.verify_gate([command])
        self.assertEqual(after_fix["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_broad_checks_must_use_the_receipt_bound_runner_after_approval(self) -> None:
        self.approve_contract()
        for command in (
            "python3 -m unittest discover -s tests",
            "pytest tests",
            "python3 -m pytest tests",
            "vitest run",
            "pytest tests/unit && pytest tests/integration",
            "pytest tests/unit > verification.txt",
            "npm test -- --runInBand",
        ):
            with self.subTest(command=command):
                payload = self.pre_tool("Bash", command, "turn-2")
                self.assertEqual(
                    payload["hookSpecificOutput"]["permissionDecision"], "deny"
                )
                self.assertIn(
                    "click-gate verify",
                    payload["hookSpecificOutput"]["permissionDecisionReason"],
                )
        targeted = self.pre_tool("Bash", "pytest tests/test_inventory.py", "turn-2")
        self.assertEqual(
            targeted["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_uninvoked_verification_remains_fail_open(self) -> None:
        self.set_default("manual")
        self.assertIsNone(
            self.pre_tool("Bash", "python3 -m unittest discover -s tests")
        )

    def test_verification_root_main_py_fails_stale(self) -> None:
        self.assertTrue(CLICK_VERIFICATION.new_untracked_is_suspicious("main.py"))
        self.assert_verification_new_path_behavior("main.py", suspicious=True)

    def test_verification_root_package_json_fails_stale(self) -> None:
        self.assertTrue(CLICK_VERIFICATION.new_untracked_is_suspicious("package.json"))
        self.assert_verification_new_path_behavior("package.json", suspicious=True)

    def test_verification_root_dockerfile_fails_stale(self) -> None:
        self.assertTrue(CLICK_VERIFICATION.new_untracked_is_suspicious("Dockerfile"))
        self.assert_verification_new_path_behavior("Dockerfile", suspicious=True)

    def test_verification_generic_report_fails_stale(self) -> None:
        self.assertFalse(CLICK_VERIFICATION.new_untracked_is_suspicious("generic-report.txt"))
        self.assert_verification_new_path_behavior("generic-report.txt")

    def test_verification_ignored_artifact_does_not_change_snapshot(self) -> None:
        self.assert_verification_new_path_behavior(
            "ignored-artifact.tmp", ignored=True
        )

    def test_success_receipt_binds_the_current_host_coverage_identity(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.approve_contract()

        first = self.verify_gate([self.verification_argv()])
        completed = self.run_rewritten(first)
        self.assertEqual(completed.returncode, 0, completed.stderr)

        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        source = state["evidence_state"]["sources"][
            CLICK_EVIDENCE.evidence_key("E1")
        ]
        self.assertEqual(
            source["verified_host_coverage"],
            CLICK_GATE.click_host_coverage.receipt("codex"),
        )
        self.assertEqual(state["verification"]["running_host_coverage"], {})

    def test_current_receipt_is_not_reused_across_host_coverage_identities(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.approve_contract()
        command = self.verification_argv()
        first = self.verify_gate([command])
        self.assertEqual(self.run_rewritten(first).returncode, 0)

        self.base_event["platform"] = "antigravity"
        repeated = self.verify_gate([command])

        self.assertEqual(
            repeated["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertIn(
            "run-verification",
            split_runner_command(
                repeated["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            state["verification"]["running_host_coverage"],
            CLICK_GATE.click_host_coverage.receipt("antigravity"),
        )
        self.assertRegex(
            state["verification"]["running_host_coverage_digest"],
            r"^[0-9a-f]{64}$",
        )

    def test_runner_rejects_tampered_host_coverage_binding_before_execution(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.approve_contract()
        prepared = self.verify_gate([self.verification_argv()])
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["verification"]["running_host_coverage"] = (
            CLICK_GATE.click_host_coverage.receipt("antigravity")
        )
        state_path.write_text(json.dumps(state), encoding="utf-8")

        blocked = self.run_rewritten(prepared)

        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("host coverage binding", blocked.stderr)
        released = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(released["verification"]["status"], "ready")
        self.assertEqual(released["verification"]["running_host_coverage"], {})
        self.assertEqual(
            released["verification"]["running_host_coverage_digest"], ""
        )

    def install_evidence_shard_fixture(
        self,
        *,
        transient_failure: bool = False,
        safe_readme_reuse: bool = False,
        dependency_observation: bool = False,
    ) -> list[str]:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n.shard-attempt\n", encoding="utf-8"
        )
        (self.workspace / "README.md").write_text("before\n", encoding="utf-8")
        (self.workspace / "a_shard.py").write_text(
            "import unittest\n\n"
            "class AlphaShard(unittest.TestCase):\n"
            "    def test_pass(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        beta_body = (
            "from pathlib import Path\n"
            "import unittest\n\n"
            "class BetaShard(unittest.TestCase):\n"
            "    def test_pass(self):\n"
            "        marker = Path('.shard-attempt')\n"
            "        if not marker.exists():\n"
            "            marker.write_text('seen\\n', encoding='utf-8')\n"
            "            self.fail('transient shard failure')\n"
            if transient_failure
            else
            "import unittest\n\n"
            "class BetaShard(unittest.TestCase):\n"
            "    def test_pass(self):\n"
            "        self.assertTrue(True)\n"
        )
        (self.workspace / "b_shard.py").write_text(beta_body, encoding="utf-8")
        parent = [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            ".",
            "-p",
            "*_shard.py",
            "-q",
        ]
        alpha = [
            sys.executable,
            "-m",
            "unittest",
            "a_shard.AlphaShard.test_pass",
        ]
        beta = [
            sys.executable,
            "-m",
            "unittest",
            "b_shard.BetaShard.test_pass",
        ]
        shard_map = self.workspace / ".click" / "evidence-shards.json"
        shard_map.parent.mkdir()
        shard_map.write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": [
                        {
                            "checks": [parent],
                            "inventory": ["*_shard.py"],
                            "shards": [
                                {
                                    "id": "alpha",
                                    "checks": [alpha],
                                    "covers": ["a_shard.py"],
                                },
                                {
                                    "id": "beta",
                                    "checks": [beta],
                                    "covers": ["b_shard.py"],
                                },
                            ],
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        tracked = [
            ".gitignore",
            "README.md",
            "a_shard.py",
            "b_shard.py",
            ".click/evidence-shards.json",
        ]
        if safe_readme_reuse:
            policy = self.workspace / ".click" / "evidence-reuse.json"
            policy.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": [
                            {
                                "checks": [alpha],
                                "reuse_if_only_changed": ["README.md"],
                            },
                            {
                                "checks": [beta],
                                "reuse_if_only_changed": ["README.md"],
                            },
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            tracked.append(".click/evidence-reuse.json")
        if dependency_observation:
            dependency_map = self.workspace / ".click" / "evidence-dependencies.json"
            dependency_map.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": [
                            {"checks": [alpha], "paths": ["*_shard.py"]},
                            {"checks": [beta], "paths": ["*_shard.py"]},
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            tracked.append(".click/evidence-dependencies.json")
        self.initialize_git(*tracked)
        return parent

    def decoded_verification_batch(self, payload: dict) -> dict:
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        raw, error = CLICK_CAPABILITY.decode_encoded_request(
            tokens[8], "verification"
        )
        self.assertEqual(error, "")
        assert raw is not None
        return json.loads(raw)

    def test_explicit_batch_workdir_expands_shards_from_nested_repository(
        self,
    ) -> None:
        parent = self.install_evidence_shard_fixture()
        self.base_event["cwd"] = str(self.workspace.parent)
        self.approve_contract()
        batch = {
            "version": 2,
            "workdir": str(self.workspace),
            "checks": [
                {"evidence_id": "E1", "argv": parent, "class": "broad"}
            ],
        }
        command = f"click-gate verify {shlex.quote(json.dumps(batch))}"

        prepared = self.tool_hook(
            "pre-tool",
            "Bash",
            {"command": command},
            turn_id="turn-2",
        )

        self.assertIsNotNone(prepared)
        assert prepared is not None
        expanded = self.decoded_verification_batch(prepared)
        self.assertEqual(expanded["workdir"], str(self.workspace.resolve()))
        self.assertEqual(len(expanded["checks"]), 2)
        self.assertTrue(
            all(
                check["evidence_id"].startswith("S")
                for check in expanded["checks"]
            )
        )
        self.assertIn(
            "expanded the broad suite into 2 independent shard(s)",
            prepared["hookSpecificOutput"]["additionalContext"],
        )
        completed = self.run_rewritten(prepared)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_evidence_shards_retain_passed_sibling_and_retry_only_unresolved(self) -> None:
        parent = self.install_evidence_shard_fixture(transient_failure=True)
        self.approve_contract()

        prepared = self.verify_gate([parent])
        first_batch = self.decoded_verification_batch(prepared)
        self.assertEqual(len(first_batch["checks"]), 2)
        self.assertTrue(
            all(check["evidence_id"].startswith("S") for check in first_batch["checks"])
        )
        first = self.run_rewritten(prepared)
        self.assertEqual(first.returncode, 1, first.stderr)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        partial = json.loads(state_path.read_text(encoding="utf-8"))
        statuses = sorted(
            source["status"]
            for source in partial["evidence_state"]["sources"].values()
        )
        self.assertEqual(statuses, ["failed", "passed"])

        retry = self.verify_gate([parent])
        retry_batch = self.decoded_verification_batch(retry)
        self.assertEqual(len(retry_batch["checks"]), 1)
        second = self.run_rewritten(retry)
        self.assertEqual(second.returncode, 0, second.stderr)
        completed = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(completed["verification"]["status"], "passed")
        self.assertTrue(
            all(
                source["status"] == "passed"
                for source in completed["evidence_state"]["sources"].values()
            )
        )

        receipt_payload = self.pre_tool(
            "Bash", "click-gate receipt export", "turn-2", submit_prompt=False
        )
        assert receipt_payload is not None
        exported = self.run_rewritten(receipt_payload)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        receipt = json.loads(exported.stdout)["receipt"]
        self.assertEqual(receipt["version"], 3)
        self.assertEqual(
            sorted(source["shard"]["shard_id"] for source in receipt["evidence"]),
            ["alpha", "beta"],
        )
        self.assertTrue(
            all(source["shard"]["shard_count"] == 2 for source in receipt["evidence"])
        )

    def test_shard_map_alone_never_authorizes_cross_revision_skip(self) -> None:
        parent = self.install_evidence_shard_fixture()
        self.approve_contract()
        first = self.verify_gate([parent])
        self.assertEqual(self.run_rewritten(first).returncode, 0)

        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )
        (self.workspace / "README.md").write_text("after\n", encoding="utf-8")
        self.tool_hook(
            "post-tool", "apply_patch", {"patch": "readme"}, tool_use_id="tool-1"
        )

        rerun = self.verify_gate([parent])
        self.assertEqual(len(self.decoded_verification_batch(rerun)["checks"]), 2)

    def test_caller_installed_v1_shard_observations_rerun_every_child(self) -> None:
        parent = self.install_evidence_shard_fixture(dependency_observation=True)
        self.approve_contract()

        prepared = self.verify_gate([parent])
        first_batch = self.decoded_verification_batch(prepared)
        self.assertEqual(self.run_rewritten(prepared).returncode, 0)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        sources = state["evidence_state"]["sources"]
        grouped: dict[str, list[dict]] = {}
        observations: dict[str, dict] = {}
        for check in first_batch["checks"]:
            source_key = CLICK_EVIDENCE.evidence_key(check["evidence_id"])
            grouped[source_key] = [check]
            shard_id = sources[source_key]["shard"]["shard_id"]
            observations[source_key] = (
                CLICK_VERIFICATION.click_dependency_cache.dependency_observation(
                    [f"{shard_id[0]}_shard.py"]
                )
            )
        receipts = CLICK_VERIFICATION.click_dependency_cache.receipts_for_groups(
            self.workspace,
            grouped,
            declarations=CLICK_VERIFICATION.dependency_declarations(
                sources, set(grouped)
            ),
            observations=observations,
            git_capture=CLICK_VERIFICATION.git_capture,
        )
        for source_key, receipt in receipts.items():
            CLICK_VERIFICATION.store_dependency_receipt(
                sources[source_key], receipt
            )
        state_path.write_text(json.dumps(state), encoding="utf-8")

        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )
        with (self.workspace / "a_shard.py").open("a", encoding="utf-8") as handle:
            handle.write("\n# alpha changed\n")
        self.tool_hook(
            "post-tool", "apply_patch", {"patch": "alpha"}, tool_use_id="tool-1"
        )

        incremental = self.verify_gate([parent])

        batch = self.decoded_verification_batch(incremental)
        planned = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            len(batch["checks"]),
            2,
            json.dumps(planned["verification"]["incremental_plan"], indent=2),
        )
        plan = planned["verification"]["incremental_plan"]
        self.assertEqual(plan["planned_execution_source_count"], 2)
        self.assertEqual(plan["planned_reuse_source_count"], 0)
        self.assertEqual(CLICK_VERIFICATION.click_incremental.summary(planned["verification"])["executed_source_count"], 0)
        self.assertEqual(
            sorted(item["decision"] for item in plan["decisions"]),
            ["not-evaluable", "not-evaluable"],
        )

    def test_each_shard_can_use_existing_committed_safe_change_authority(self) -> None:
        parent = self.install_evidence_shard_fixture(safe_readme_reuse=True)
        self.approve_contract()
        first = self.verify_gate([parent])
        self.assertEqual(self.run_rewritten(first).returncode, 0)

        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )
        (self.workspace / "README.md").write_text("after\n", encoding="utf-8")
        self.tool_hook(
            "post-tool", "apply_patch", {"patch": "readme"}, tool_use_id="tool-1"
        )

        reused = self.verify_gate([parent])
        command = reused["hookSpecificOutput"]["updatedInput"]["command"]
        self.assertIn("repository-declared safe-change cross-revision", command)
        self.assertNotIn("run-verification", command)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertTrue(
            all(
                source["safe_change_reuse_count"] == 1
                for source in state["evidence_state"]["sources"].values()
            )
        )

    def test_edited_shard_map_runs_original_parent_suite(self) -> None:
        parent = self.install_evidence_shard_fixture()
        shard_map = self.workspace / ".click" / "evidence-shards.json"
        shard_map.write_text(
            shard_map.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        self.approve_contract()

        prepared = self.verify_gate([parent])
        batch = self.decoded_verification_batch(prepared)
        self.assertEqual(len(batch["checks"]), 1)
        self.assertEqual(batch["checks"][0]["evidence_id"], "E1")
        self.assertEqual(batch["checks"][0]["argv"], parent)
        self.assertIn(
            "manifest-working-copy-mismatch",
            prepared["hookSpecificOutput"]["additionalContext"],
        )
        self.assertEqual(self.run_rewritten(prepared).returncode, 0)

    def test_refreshed_complete_plan_runs_children_without_promoting_stale_facts(self) -> None:
        parent = self.install_evidence_shard_fixture(safe_readme_reuse=True)
        self.approve_contract()
        first = self.verify_gate([parent])
        old_ids = {check["evidence_id"] for check in self.decoded_verification_batch(first)["checks"]}
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        self.assertIsNone(self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2"))
        (self.workspace / "c_shard.py").write_text(
            "import unittest\nclass Gamma(unittest.TestCase):\n"
            "    def test_pass(self): self.assertTrue(True)\n", encoding="utf-8")
        target = self.workspace / ".click/evidence-shards.json"
        manifest = json.loads(target.read_text())
        manifest["entries"][0]["shards"].append({
            "id": "gamma", "covers": ["c_shard.py"],
            "checks": [[sys.executable, "-m", "unittest", "c_shard.Gamma.test_pass"]],
        })
        target.write_text(json.dumps(manifest), encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.workspace, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Click Tests",
                        "-c", "user.email=click-tests@example.invalid", "commit", "-qm", "refresh complete child plan"],
                       cwd=self.workspace, check=True, capture_output=True)
        self.tool_hook("post-tool", "apply_patch", {"patch": "new child"}, tool_use_id="tool-1")
        prepared = self.verify_gate([parent])
        checks = self.decoded_verification_batch(prepared)["checks"]
        self.assertEqual(len(checks), 3)
        self.assertTrue(old_ids.issubset({check["evidence_id"] for check in checks}))
        self.assertTrue(all(check["argv"] != parent for check in checks))
        self.assertEqual(self.run_rewritten(prepared).returncode, 0)
        state_path = next((self.plugin_data / "gate-state").glob("session-contract-*.json"))
        state = json.loads(state_path.read_text())
        self.assertEqual(sorted(source["attempts"] for source in state["evidence_state"]["sources"].values()), [1, 2, 2])
        self.assertEqual(state["verification"]["incremental_plan"]["planned_reuse_source_count"], 0)
        exported = self.pre_tool("Bash", "click-gate receipt export", "turn-2", submit_prompt=False)
        result = self.run_rewritten(exported)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)["receipt"]
        self.assertEqual(len(receipt["evidence"]), 3)
        self.assertTrue(all(item["shard"]["shard_count"] == 3 for item in receipt["evidence"]))

    def test_multiple_edits_preserve_safe_change_candidates_per_child(self) -> None:
        parent = self.install_evidence_shard_fixture(safe_readme_reuse=True)
        self.set_default("evidence", "turn-0")
        self.prompt_submit("Verify the current project with Evidence.", "turn-1")
        self.assertEqual(self.run_rewritten(self.verify_gate([parent], "turn-1")).returncode, 0)
        for index in range(2):
            tool_id = f"readme-{index}"
            self.assertIsNone(self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-1",
                                           tool_use_id=tool_id))
            (self.workspace / "README.md").write_text(f"after {index}\n", encoding="utf-8")
            self.tool_hook("post-tool", "apply_patch", {"patch": "readme"},
                           turn_id="turn-1", tool_use_id=tool_id)
        prepared = self.verify_gate([parent], "turn-1")
        self.assertNotIn("run-verification", prepared["hookSpecificOutput"]["updatedInput"]["command"])
        state_path = next((self.plugin_data / "gate-state").glob("session-contract-*.json"))
        state = json.loads(state_path.read_text())
        self.assertEqual(state["verification"]["incremental_plan"]["planned_reuse_source_count"], 2)


class ReviewHardeningGateTests(ClickGateTestCase):
    def _passing_state(self, mode: str, *, safe_change: bool = False):
        (self.workspace / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        files = [".gitignore", "verification_fixture.py"]
        if safe_change:
            (self.workspace / "README.md").write_text("before\n", encoding="utf-8")
            policy = self.workspace / ".click" / "evidence-reuse.json"
            policy.parent.mkdir()
            policy.write_text(json.dumps({"version": 1, "entries": [{
                "checks": [self.verification_argv()], "reuse_if_only_changed": ["README.md"],
            }]}), encoding="utf-8")
            files.extend(["README.md", ".click/evidence-reuse.json"])
        self.initialize_git(*files)
        if mode == "guarded":
            self.approve_contract()
            turn = "turn-2"
        else:
            self.prompt_submit("Verify the current work with Evidence.", "turn-1")
            turn = "turn-1"
        argv = self.verification_argv()
        first = self.run_rewritten(self.verify_gate([argv], turn))
        self.assertEqual(first.returncode, 0, first.stderr)
        path = next((self.plugin_data / "gate-state").glob("session-contract-*.json"))
        baseline = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(baseline["verification"]["mutation_revision"], 0)
        return path, baseline, argv, turn

    def _assert_malformed_revision_denied(self, mode: str, field: str):
        path, baseline, argv, turn = self._passing_state(mode)
        key = next(iter(baseline["evidence_state"]["sources"]))
        values = [False, True, 0.0, 0.9, 1.0, 1.9, "0", None, [], {},
                  float("nan"), float("inf"), -1, -2]
        for index, value in enumerate([*values, "missing"]):
            with self.subTest(mode=mode, field=field, value=value):
                modified = copy.deepcopy(baseline)
                target = (modified["evidence_state"]["sources"][key]
                          if field == "verified_revision" else modified["verification"])
                if value == "missing":
                    del target[field]
                else:
                    target[field] = value
                raw = json.dumps(modified)
                path.write_text(raw, encoding="utf-8")
                request = {"version": 2, "checks": [
                    {"evidence_id": "E1", "argv": argv, "class": "targeted"},
                ]}
                result, payload = self.run_hook("pre-tool", {
                    **self.base_event, "turn_id": turn,
                    "hook_event_name": "PreToolUse", "tool_name": "Bash",
                    "tool_use_id": f"malformed-{field}-{index}",
                    "tool_input": {"command": "click-gate verify " + shlex.quote(json.dumps(request))},
                })
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsNotNone(payload)
                output = payload["hookSpecificOutput"]
                self.assertEqual(output.get("permissionDecision"), "deny", payload)
                self.assertNotIn("updatedInput", output)
                # Rejection telemetry may change; evidence and its revision may not
                # be repaired into a fresh success. JSON comparison retains NaN.
                after = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    json.dumps(after["evidence_state"], sort_keys=True),
                    json.dumps(modified["evidence_state"], sort_keys=True),
                )
                self.assertEqual(
                    json.dumps(after["verification"].get("mutation_revision", "missing")),
                    json.dumps(modified["verification"].get("mutation_revision", "missing")),
                )

    def test_evidence_rejects_malformed_success_revision_from_state(self):
        self._assert_malformed_revision_denied("evidence", "verified_revision")

    def test_guarded_rejects_malformed_success_revision_from_state(self):
        self._assert_malformed_revision_denied("guarded", "verified_revision")

    def test_evidence_rejects_malformed_current_revision_from_state(self):
        self._assert_malformed_revision_denied("evidence", "mutation_revision")

    def test_guarded_rejects_malformed_current_revision_from_state(self):
        self._assert_malformed_revision_denied("guarded", "mutation_revision")

    def _safe_change_state(self, mode: str):
        path, baseline, argv, turn = self._passing_state(mode, safe_change=True)
        self.assertIsNone(self.pre_tool(
            "apply_patch", "*** Begin Patch\n*** End Patch", turn,
            submit_prompt=False, tool_use_id="change-readme",
        ))
        (self.workspace / "README.md").write_text("after\n", encoding="utf-8")
        self.tool_hook("post-tool", "apply_patch", {"patch": "README"},
                       turn_id=turn, tool_use_id="change-readme")
        return path, baseline, argv, turn

    def _assert_changed_decision_runs(self, mode: str):
        path, baseline, argv, turn = self._safe_change_state(mode)
        self.hook_in_process = True
        decide = CLICK_VERIFICATION.click_change_policy.decide

        def changed_decision(*args, **kwargs):
            decision = decide(*args, **kwargs)
            self.assertEqual(decision["status"], "reuse")
            return {**decision, "decision_digest": "0" * 64}

        with mock.patch.object(CLICK_VERIFICATION.click_change_policy, "decide", side_effect=changed_decision):
            payload = self.verify_gate([argv], turn)
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "allow", payload)
        self.assertIn("run-verification", split_runner_command(output["updatedInput"]["command"]))
        pending = json.loads(path.read_text(encoding="utf-8"))
        key = CLICK_EVIDENCE.evidence_key("E1")
        self.assertNotEqual(pending["evidence_state"]["sources"][key]["status"], "passed")
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        source = json.loads(path.read_text(encoding="utf-8"))["evidence_state"]["sources"][key]
        self.assertEqual(source["status"], "passed")
        self.assertEqual(source["attempts"], pending["evidence_state"]["sources"][key]["attempts"] + 1)
        self.assertEqual(source["safe_change_reuse_count"], 0)

    def test_evidence_changed_decision_runs_real_check(self):
        self._assert_changed_decision_runs("evidence")

    def test_guarded_changed_decision_runs_real_check(self):
        self._assert_changed_decision_runs("guarded")

    def _assert_successor_fact_boundary(self, mode: str):
        if mode == "guarded":
            self.set_default("guarded", "turn-0")
        path, previous, argv, turn = self._passing_state(mode)
        key = CLICK_EVIDENCE.evidence_key("E1")
        previous_source = previous["evidence_state"]["sources"][key]
        previous_source["verified_at"] = 1234567890
        previous_source["verified_future_extension"] = {"nested": ["previous"]}
        previous_source["future_execution_state"] = {"active": True}
        path.write_text(json.dumps(previous), encoding="utf-8")

        if mode == "guarded":
            replacement = self.contract()
            replacement["outcome"] = "Verify an independently approved successor"
            self.arm_gate("turn-3")
            staged = self.stage_gate(replacement, "turn-3")
            self.assertEqual(staged["hookSpecificOutput"]["permissionDecision"], "allow")
            staged_state = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotEqual(staged_state["contract_id"], previous["contract_id"])
            self.assertEqual(staged_state["approved_turn_id"], "")
            denied = self.verify_gate([argv], "turn-3")
            self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
            self.arm_gate("turn-4")
            self.pass_gate(staged_state["contract_id"], "turn-4")
            turn = "turn-4"
        else:
            self.prompt_submit("Follow-up Evidence task", "turn-2")
            turn = "turn-2"

        reused = self.verify_gate([argv], turn)
        self.assertNotIn("run-verification", split_runner_command(
            reused["hookSpecificOutput"]["updatedInput"]["command"],
        ))
        current = json.loads(path.read_text(encoding="utf-8"))
        source = current["evidence_state"]["sources"][key]
        self.assertEqual(source["status"], "passed")
        self.assertEqual(source["attempts"], 0)
        self.assertEqual(source["verified_at"], previous_source["verified_at"])
        self.assertGreater(source["last_successor_reused_at"], source["verified_at"])
        self.assertNotIn("verified_future_extension", source)
        self.assertNotIn("future_execution_state", source)
        self.assertNotIn("runner_token", current["verification"])
        if mode == "guarded":
            self.assertEqual(current["approved_turn_id"], "turn-4")
            self.assertEqual(source["last_successor_origin_contract_id"], previous["contract_id"])
        else:
            self.assertEqual(source["last_successor_origin_evidence_session_id"],
                             previous["evidence_session_id"])

    def test_evidence_successor_copies_facts_and_preserves_execution_time(self):
        self._assert_successor_fact_boundary("evidence")

    def test_guarded_successor_copies_facts_after_independent_approval(self):
        self._assert_successor_fact_boundary("guarded")

    def _assert_late_workspace_drift(self, mode: str):
        self.hook_in_process = True
        inputs = self.workspace / "inputs"
        inputs.mkdir()
        original = {"value.txt": "good", "shared.json": "good", "deps.lock": "good"}
        for name, content in original.items():
            (inputs / name).write_text(content, encoding="utf-8")
        fixture = self.workspace / "verification_fixture.py"
        fixture.write_text(
            "import unittest\nfrom pathlib import Path\n"
            "class VerificationFixture(unittest.TestCase):\n"
            "    def test_pass(self):\n"
            "        for path in Path('inputs').iterdir():\n"
            "            self.assertNotEqual(path.read_text(), 'bad')\n",
            encoding="utf-8",
        )
        path, baseline, argv, turn = self._passing_state(mode)
        matcher = CLICK_VERIFICATION._verification_receipt_matches
        actions = {
            "modify": lambda: (inputs / "value.txt").write_text("bad"),
            "create": lambda: (inputs / "new.txt").write_text("bad"),
            "delete": lambda: (inputs / "value.txt").unlink(),
            "rename": lambda: (inputs / "value.txt").rename(inputs / "renamed.txt"),
            "shared-config": lambda: (inputs / "shared.json").write_text("bad"),
            "lockfile": lambda: (inputs / "deps.lock").write_text("bad"),
        }
        for name, action in actions.items():
            with self.subTest(mode=mode, change=name):
                for item in inputs.iterdir():
                    item.unlink()
                for filename, content in original.items():
                    (inputs / filename).write_text(content, encoding="utf-8")
                path.write_text(json.dumps(baseline), encoding="utf-8")

                def match_then_change(*args, **kwargs):
                    matched = matcher(*args, **kwargs)
                    self.assertTrue(matched)
                    action()
                    return matched

                with mock.patch.object(CLICK_VERIFICATION, "_verification_receipt_matches",
                                       side_effect=match_then_change) as matched:
                    payload = self.verify_gate([argv], turn)
                self.assertEqual(matched.call_count, 1)
                self.assertIn("run-verification", split_runner_command(
                    payload["hookSpecificOutput"]["updatedInput"]["command"],
                ))
                pending = json.loads(path.read_text(encoding="utf-8"))
                source = pending["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
                self.assertNotEqual(source["status"], "passed")
                result = self.run_rewritten(payload)
                # The original full command on the exact same final fixture is
                # an experiment control, not an extra production verification.
                audit = subprocess.run(argv, cwd=self.workspace, capture_output=True,
                                       text=True, check=False)
                self.assertEqual(result.returncode == 0, audit.returncode == 0,
                                 (result.stderr, audit.stderr))
                expected_success = name in {"delete", "rename"}
                self.assertEqual(result.returncode == 0, expected_success)
                final_source = json.loads(path.read_text(encoding="utf-8"))["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
                self.assertEqual(final_source["status"], "passed" if expected_success else "failed")
                self.assertEqual(final_source["attempts"], source["attempts"] + 1)

    def test_evidence_rechecks_workspace_after_reuse_decision(self):
        self._assert_late_workspace_drift("evidence")

    def test_guarded_rechecks_workspace_after_reuse_decision(self):
        self._assert_late_workspace_drift("guarded")

    def _assert_late_environment_drift(self, mode: str):
        self.hook_in_process = True
        path, baseline, argv, turn = self._passing_state(mode)
        matcher = CLICK_VERIFICATION._verification_receipt_matches
        with mock.patch.dict(os.environ):
            def match_then_change(*args, **kwargs):
                matched = matcher(*args, **kwargs)
                self.assertTrue(matched)
                os.environ["CLICK_HARDENING_INPUT"] = "changed"
                return matched

            with mock.patch.object(CLICK_VERIFICATION, "_verification_receipt_matches",
                                   side_effect=match_then_change):
                payload = self.verify_gate([argv], turn)
            self.assertIn("updatedInput", payload["hookSpecificOutput"], payload)
            self.assertIn("run-verification", split_runner_command(
                payload["hookSpecificOutput"]["updatedInput"]["command"],
            ))
            result = self.run_rewritten(payload, {"CLICK_HARDENING_INPUT": "changed"})
            self.assertEqual(result.returncode, 0, result.stderr)
        source = json.loads(path.read_text(encoding="utf-8"))["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
        self.assertEqual(source["attempts"], 2)
        self.assertNotEqual(source["verified_environment_digest"], baseline["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]["verified_environment_digest"])

    def test_evidence_rechecks_environment_after_reuse_decision(self):
        self._assert_late_environment_drift("evidence")

    def test_guarded_rechecks_environment_after_reuse_decision(self):
        self._assert_late_environment_drift("guarded")

    def test_reuse_rechecks_replaced_symlink_target(self):
        if os.name == "nt":
            self.skipTest("requires native symlink creation privileges; covered here on POSIX")
        self.hook_in_process = True
        (self.workspace / "good.txt").write_text("good", encoding="utf-8")
        (self.workspace / "bad.txt").write_text("bad", encoding="utf-8")
        link = self.workspace / "current.txt"
        link.symlink_to("good.txt")
        fixture = self.workspace / "verification_fixture.py"
        fixture.write_text(
            "import unittest\nfrom pathlib import Path\n"
            "class VerificationFixture(unittest.TestCase):\n"
            "    def test_pass(self):\n"
            "        self.assertEqual(Path('current.txt').read_text(), 'good')\n",
            encoding="utf-8",
        )
        path, baseline, argv, turn = self._passing_state("evidence")
        matcher = CLICK_VERIFICATION._verification_receipt_matches

        def replace_target(*args, **kwargs):
            result = matcher(*args, **kwargs)
            self.assertTrue(result)
            link.unlink()
            link.symlink_to("bad.txt")
            return result

        with mock.patch.object(CLICK_VERIFICATION, "_verification_receipt_matches",
                               side_effect=replace_target):
            payload = self.verify_gate([argv], turn)
        self.assertIn("run-verification", split_runner_command(payload["hookSpecificOutput"]["updatedInput"]["command"]))
        self.assertNotEqual(self.run_rewritten(payload).returncode, 0)
        self.assertNotEqual(subprocess.run(argv, cwd=self.workspace, capture_output=True).returncode, 0)
        source = json.loads(path.read_text(encoding="utf-8"))["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
        self.assertEqual(source["status"], "failed")

    def test_reuse_rechecks_executable_content_outside_workspace(self):
        if os.name == "nt":
            self.skipTest("POSIX executable fixture; native Windows executable replacement is untested")
        self.hook_in_process = True
        executable = self.workspace.parent / "pytest"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o700)
        self.initialize_git("verification_fixture.py")
        self.prompt_submit("Verify the executable fixture", "turn-1")
        argv = [str(executable)]
        self.assertEqual(self.run_rewritten(self.verify_gate([argv], "turn-1")).returncode, 0)
        matcher = CLICK_VERIFICATION._verification_receipt_matches

        def replace_executable(*args, **kwargs):
            result = matcher(*args, **kwargs)
            self.assertTrue(result)
            executable.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            return result

        with mock.patch.object(CLICK_VERIFICATION, "_verification_receipt_matches",
                               side_effect=replace_executable):
            payload = self.verify_gate([argv], "turn-1")
        self.assertIn("updatedInput", payload["hookSpecificOutput"], payload)
        self.assertIn("run-verification", split_runner_command(payload["hookSpecificOutput"]["updatedInput"]["command"]))
        self.assertNotEqual(self.run_rewritten(payload).returncode, 0)
        self.assertNotEqual(subprocess.run(argv, cwd=self.workspace, capture_output=True).returncode, 0)

    def test_drift_after_safe_change_promotion_rolls_back_reuse(self):
        self.hook_in_process = True
        path, baseline, argv, turn = self._safe_change_state("evidence")
        promote = CLICK_VERIFICATION._promote_safe_change_receipt

        def promote_then_change(*args, **kwargs):
            promote(*args, **kwargs)
            fixture = self.workspace / "verification_fixture.py"
            fixture.write_text(fixture.read_text().replace("self.assertTrue(True)", "self.fail('late drift')"))

        with mock.patch.object(CLICK_VERIFICATION, "_promote_safe_change_receipt",
                               side_effect=promote_then_change) as promoted:
            payload = self.verify_gate([argv], turn)
        self.assertEqual(promoted.call_count, 1)
        self.assertIn("run-verification", split_runner_command(payload["hookSpecificOutput"]["updatedInput"]["command"]))
        source = json.loads(path.read_text(encoding="utf-8"))["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
        self.assertNotEqual(source["status"], "passed")
        self.assertEqual(source["safe_change_reuse_count"], 0)
        self.assertEqual(source["last_safe_change_decision_digest"], "")
        self.assertNotEqual(self.run_rewritten(payload).returncode, 0)
        self.assertNotEqual(subprocess.run(argv, cwd=self.workspace, capture_output=True).returncode, 0)
