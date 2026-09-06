from __future__ import annotations

from click_gate_test_support import (
    CLICK_EVIDENCE,
    CLICK_GATE,
    CLICK_LIFECYCLE,
    CLICK_PROCESS,
    CLICK_PROMPT,
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


class ClickGateLifecycleTests(ClickGateTestCase):
    # These tests exercise the lifecycle state machine.  The executable and
    # encoded-runner boundaries remain covered by the dedicated gate/transport
    # suites, so avoid paying for a fresh Python import on every hook event.
    hook_in_process = True

    def test_manual_default_persists_and_keeps_uninvoked_mutations_fail_open(self) -> None:
        setting = self.set_default("manual")
        self.assertEqual(
            setting["hookSpecificOutput"]["updatedInput"]["command"],
            "echo Click default mode set to Off",
        )
        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch")
        )
        self.assertIsNone(
            self.pre_tool("Bash", "python3 update_schema.py", turn_id="turn-2")
        )

    def test_manual_staged_contract_blocks_next_turn_mutation(self) -> None:
        self.set_default("manual", "turn-0")
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")

        payload = self.pre_tool(
            "apply_patch", "*** Begin Patch\n*** End Patch", turn_id="turn-2"
        )
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(
            "active execution contract",
            payload["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_manual_incomplete_approved_contract_blocks_later_turn_mutation(self) -> None:
        self.set_default("manual", "turn-0")
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")

        payload = self.pre_tool(
            "Bash", "python3 update_schema.py", turn_id="turn-3"
        )
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(
            "active execution contract",
            payload["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_incomplete_approved_contract_resume_reuses_the_same_id(self) -> None:
        self.set_default("manual", "turn-0")
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        contract_id = self.active_contract_id()
        self.arm_gate("turn-2")
        self.pass_gate(contract_id, "turn-2")

        context = self.prompt_submit("계속 진행해줘", "turn-3")["hookSpecificOutput"][
            "additionalContext"
        ]
        self.assertIn(f"incomplete approved contract_id is `{contract_id}`", context)
        self.arm_gate("turn-3")
        resumed = self.pass_gate(contract_id, "turn-3")
        self.assertEqual(
            resumed["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["contract_id"], contract_id)
        self.assertEqual(state["approved_turn_id"], "turn-2")

    def test_incomplete_approved_contract_advises_on_parallel_root_inventory(self) -> None:
        self.initialize_git("verification_fixture.py")
        self.set_default("manual", "turn-0")
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")

        first = self.pre_tool("Bash", "git ls-files", turn_id="turn-3")
        self.assertEqual(first["hookSpecificOutput"]["permissionDecision"], "allow")
        parallel = self.pre_tool(
            "Bash", "git ls-files --cached", turn_id="turn-3"
        )
        self.assert_observation_advisory(parallel, "already running")

        identical_running = self.pre_tool(
            "Bash", "git ls-files", turn_id="turn-3"
        )
        self.assertEqual(
            identical_running["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn(
            "exact observation runner for this request is already active",
            identical_running["hookSpecificOutput"]["permissionDecisionReason"],
        )
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        self.assertEqual(self.run_rewritten(parallel).returncode, 0)
        repeated = self.pre_tool("Bash", "git ls-files", turn_id="turn-3")
        self.assert_observation_advisory(
            repeated, "identical read or search already succeeded"
        )
        self.assertEqual(self.run_rewritten(repeated).returncode, 0)

    def test_incomplete_approved_contract_tracks_later_turn_direct_reads(self) -> None:
        (self.workspace / "README.md").write_text("hello\n", encoding="utf-8")
        self.approve_contract()
        command = self.read_file_command("README.md")

        first = self.pre_tool("Bash", command, turn_id="turn-3")
        self.assertEqual(first["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        repeated = self.pre_tool("Bash", command, turn_id="turn-3")
        self.assert_observation_advisory(
            repeated, "identical read or search already succeeded"
        )
        self.assertEqual(self.run_rewritten(repeated).returncode, 0)

    def test_incomplete_approved_contract_tracks_later_turn_structured_reads(self) -> None:
        (self.workspace / "README.md").write_text("hello\n", encoding="utf-8")
        self.approve_contract()
        commands = [["Get-Content", "-Raw", "README.md"]]

        first = self.inspect_gate(commands, turn_id="turn-3")
        self.assertEqual(first["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        repeated = self.inspect_gate(commands, turn_id="turn-3")
        self.assert_observation_advisory(
            repeated, "identical read or search already succeeded"
        )
        self.assertEqual(self.run_rewritten(repeated).returncode, 0)

    def test_always_on_default_persists_and_gates_later_mutations(self) -> None:
        setting = self.set_default("on")
        self.assertEqual(
            setting["hookSpecificOutput"]["updatedInput"]["command"],
            "echo Click default mode set to Guarded",
        )
        payload = self.pre_tool(
            "apply_patch", "*** Begin Patch\n*** End Patch", turn_id="turn-2"
        )
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(
            "active execution contract",
            payload["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_default_preference_stays_outside_the_target_repository(self) -> None:
        self.set_default("on")
        preference = self.plugin_data / "preferences.json"
        self.assertTrue(preference.is_file())
        self.assertFalse((self.workspace / "preferences.json").exists())
        stored = json.loads(preference.read_text(encoding="utf-8"))
        self.assertEqual(stored["default_mode"], "guarded")
        self.assertEqual(stored["schema_version"], 2)
        self.assertFalse(stored["migration_notice_pending"])

    def test_prompt_context_reflects_persistent_default(self) -> None:
        evidence = self.prompt_submit()["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Evidence mode is enabled", evidence)
        self.assertIn("host remains the execution authority", evidence)

        self.set_default("on")
        guarded = self.prompt_submit()["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Guarded mode is enabled", guarded)
        self.assertIn("exact digest-bound easy contract once", guarded)
        self.assertIn("view-original choices", guarded)

        self.set_default("manual")
        off = self.prompt_submit()["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Off mode is enabled", off)
        self.assertIn("explicitly selects", off)

    def test_uninvoked_plan_and_exploration_remain_fail_open(self) -> None:
        self.assertIsNone(self.pre_tool("update_plan", ""))
        inspection = self.pre_tool("Bash", "rg --files")
        self.assertEqual(
            inspection["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertIn(
            "run-observation",
            split_runner_command(
                inspection["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )

    def test_legacy_preferences_preserve_authority_choice_during_migration(self) -> None:
        preference = self.plugin_data / "preferences.json"
        preference.parent.mkdir(parents=True, exist_ok=True)
        for legacy, expected in (("on", "guarded"), ("manual", "off")):
            with self.subTest(legacy=legacy):
                preference.write_text(
                    json.dumps({"default_mode": legacy, "updated_at": 1}),
                    encoding="utf-8",
                )
                context = self.prompt_submit(
                    "새 작업", f"turn-migrate-{legacy}"
                )["hookSpecificOutput"]["additionalContext"]
                self.assertIn("migrated", context)
                stored = json.loads(preference.read_text(encoding="utf-8"))
                self.assertEqual(stored["default_mode"], expected)
                self.assertEqual(stored["migrated_from"], legacy)
                self.assertFalse(stored["migration_notice_pending"])
                later = self.prompt_submit(
                    "계속", f"turn-later-{legacy}"
                )["hookSpecificOutput"]["additionalContext"]
                self.assertNotIn("migrated the previous", later)

    def test_guarded_follow_up_turn_is_digest_bound_without_reapproval(self) -> None:
        self.approve_contract()
        context = self.prompt_submit("표시 문자열만 빼줘", "turn-3")[
            "hookSpecificOutput"
        ]["additionalContext"]
        self.assertIn("incomplete approved contract_id", context)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["follow_up_turns"][0]["turn_id"], "turn-3")
        self.assertRegex(state["follow_up_turns"][0]["digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(state["approved_turn_id"], "turn-2")

    def test_guarded_follow_up_resumes_and_mutates_without_new_approval(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        contract = self.contract()
        contract["verification"]["evidence"].append(
            {
                "id": "E-manual",
                "kind": "manual",
                "description": "final operator review",
            }
        )
        contract["verification"]["done_when"].append(
            {
                "condition": "the final display is acceptable",
                "primary_evidence": "E-manual",
            }
        )
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        contract_id = self.active_contract_id()
        self.arm_gate("turn-2")
        self.pass_gate(contract_id, "turn-2")
        verification = self.verify_gate([self.verification_argv()], "turn-2")
        result = self.run_rewritten(verification)
        self.assertEqual(result.returncode, 0, result.stderr)

        context = self.prompt_submit("표시 문자열만 빼줘", "turn-3")[
            "hookSpecificOutput"
        ]["additionalContext"]
        self.assertIn(f"incomplete approved contract_id is `{contract_id}`", context)
        self.arm_gate("turn-3")
        resumed = self.pass_gate(contract_id, "turn-3")
        self.assertEqual(
            resumed["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        mutation = self.mutate_gate(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('generated.txt').write_text('updated')",
            ],
            "turn-3",
        )
        mutated = self.run_rewritten(mutation)
        self.assertEqual(mutated.returncode, 0, mutated.stderr)

        state_paths = list(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        self.assertEqual(len(state_paths), 1)
        state = json.loads(state_paths[0].read_text(encoding="utf-8"))
        source = state["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
        self.assertEqual(state["contract_id"], contract_id)
        self.assertEqual(state["approved_turn_id"], "turn-2")
        self.assertEqual(state["follow_up_turns"][0]["turn_id"], "turn-3")
        self.assertEqual(state["verification"]["mutation_revision"], 1)
        self.assertEqual(state["verification"]["status"], "stale")
        self.assertEqual(source["status"], "stale")

    def test_legacy_migration_does_not_unlock_an_active_guarded_contract(self) -> None:
        self.set_default("guarded", "turn-0")
        self.stage_gate(turn_id="turn-1")
        preference = self.plugin_data / "preferences.json"
        preference.write_text(
            json.dumps({"default_mode": "manual", "updated_at": 1}),
            encoding="utf-8",
        )

        context = self.prompt_submit("다른 것도 바꿔줘", "turn-2")[
            "hookSpecificOutput"
        ]["additionalContext"]
        self.assertIn("active staged contract_id", context)
        self.assertIn("migrated", context)
        stored = json.loads(preference.read_text(encoding="utf-8"))
        self.assertEqual(stored["default_mode"], "off")
        blocked = self.pre_tool(
            "apply_patch",
            "*** Begin Patch\n*** End Patch",
            "turn-2",
            submit_prompt=False,
        )
        self.assertEqual(
            blocked["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_armed_gate_denies_apply_patch_before_contract(self) -> None:
        self.arm_gate()
        payload = self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch")
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("execution contract", output["permissionDecisionReason"])
        self.assertIn("bypass", output["permissionDecisionReason"])

    def test_armed_gate_denies_mutating_bash_before_contract(self) -> None:
        self.arm_gate()
        payload = self.pre_tool("Bash", "python3 update_schema.py")
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_bypass_honors_user_opt_out_after_arm(self) -> None:
        self.arm_gate()
        payload = self.bypass_gate()
        self.assertEqual(
            payload["hookSpecificOutput"]["updatedInput"]["command"],
            "echo Click bypassed for this turn",
        )
        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch")
        )

    def test_strict_mode_is_session_wide_and_can_return_to_adaptive(self) -> None:
        self.set_mode("strict")
        payload = self.pre_tool(
            "apply_patch", "*** Begin Patch\n*** End Patch", turn_id="turn-2"
        )
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")
        self.set_mode("adaptive", turn_id="turn-2")
        self.set_default("manual", turn_id="turn-2")
        self.assertIsNone(
            self.pre_tool(
                "apply_patch", "*** Begin Patch\n*** End Patch", turn_id="turn-2"
            )
        )

    def test_pass_requires_a_staged_contract(self) -> None:
        self.arm_gate("turn-2")
        payload = self.pass_gate("ctr_" + ("0" * 32), turn_id="turn-2")
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("No staged", output["permissionDecisionReason"])

    def test_pass_accepts_only_a_well_formed_contract_id(self) -> None:
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        self.arm_gate("turn-2")

        malformed = self.pre_tool("Bash", "click-gate pass contract-123", "turn-2")
        self.assertEqual(
            malformed["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn(
            "exactly 32 lowercase hexadecimal",
            malformed["hookSpecificOutput"]["permissionDecisionReason"],
        )

        legacy = self.pre_tool(
            "Bash",
            f"click-gate pass {shlex.quote(json.dumps(self.contract()))}",
            "turn-2",
        )
        self.assertEqual(legacy["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(
            "contract_id", legacy["hookSpecificOutput"]["permissionDecisionReason"]
        )
        self.assertIn(
            "not the Execution Contract JSON",
            legacy["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_prompt_context_exposes_only_the_active_contract_id(self) -> None:
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        contract_id = self.active_contract_id()

        context = self.prompt_submit("승인합니다", "turn-2")["hookSpecificOutput"][
            "additionalContext"
        ]
        self.assertIn(f"active staged contract_id is `{contract_id}`", context)
        self.assertIn(f"click-gate pass {contract_id}", context)
        self.assertIn("never resend the contract JSON", context)
        self.assertNotIn("inventory", context)

    def test_prompt_context_migrates_pre_ledger_staged_contract(self) -> None:
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        del state["evidence_state"]
        del state["state_schema_version"]
        state_path.write_text(json.dumps(state), encoding="utf-8")

        context = self.prompt_submit("승인합니다", "turn-2")["hookSpecificOutput"][
            "additionalContext"
        ]
        self.assertIn("predates evidence-id completion tracking", context)
        self.assertIn("click-gate cancel", context)
        self.assertNotIn("click-gate pass", context)

    def test_prompt_context_migrates_pre_ledger_approved_contract(self) -> None:
        self.approve_contract()
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        del state["evidence_state"]
        del state["state_schema_version"]
        state_path.write_text(json.dumps(state), encoding="utf-8")

        context = self.prompt_submit("계속해줘", "turn-3")["hookSpecificOutput"][
            "additionalContext"
        ]
        self.assertIn("predates evidence-id completion tracking", context)
        self.assertIn("click-gate cancel", context)
        self.assertNotIn("click-gate pass", context)

    def test_pre_id_staged_state_gets_a_digest_derived_compatibility_id(self) -> None:
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        del state["contract_id"]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        compatibility_id = f"ctr_{state['contract_digest'][:32]}"

        context = self.prompt_submit("승인합니다", "turn-2")["hookSpecificOutput"][
            "additionalContext"
        ]
        self.assertIn(compatibility_id, context)
        self.arm_gate("turn-2")
        passed = self.pass_gate(compatibility_id, "turn-2")
        self.assertEqual(passed["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_incomplete_pre_ledger_contract_requires_cancel_and_restage(self) -> None:
        self.approve_contract()
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        del state["evidence_state"]
        del state["state_schema_version"]
        state_path.write_text(json.dumps(state), encoding="utf-8")

        verification = self.verify_gate([self.verification_argv()])
        self.assertEqual(
            verification["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn(
            "predates evidence-id completion tracking",
            verification["hookSpecificOutput"]["permissionDecisionReason"],
        )
        mutation = self.pre_tool(
            "apply_patch", "*** Begin Patch\n*** End Patch", "turn-2"
        )
        self.assertEqual(
            mutation["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_pre_ledger_staged_contract_is_rejected_at_pass(self) -> None:
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        contract_id = self.active_contract_id()
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        del state["evidence_state"]
        del state["state_schema_version"]
        state_path.write_text(json.dumps(state), encoding="utf-8")

        self.arm_gate("turn-2")
        denied = self.pass_gate(contract_id, "turn-2")
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(
            "predates evidence-id completion tracking",
            denied["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_current_staged_contract_missing_ledger_is_rejected_at_pass(self) -> None:
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        contract_id = self.active_contract_id()
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        del state["evidence_state"]
        state_path.write_text(json.dumps(state), encoding="utf-8")

        self.arm_gate("turn-2")
        denied = self.pass_gate(contract_id, "turn-2")
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(
            "unavailable or malformed",
            denied["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_completed_pre_ledger_contract_keeps_rollover_compatibility(self) -> None:
        self.approve_contract()
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        del state["evidence_state"]
        del state["state_schema_version"]
        state["verification"]["status"] = "passed"
        state["verification"]["verified_revision"] = state["verification"][
            "mutation_revision"
        ]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.assertTrue(CLICK_LIFECYCLE.contract_is_completed(state))

        replacement = self.contract()
        replacement["outcome"] = "send a purchasing summary"
        self.arm_gate("turn-3")
        staged = self.stage_gate(replacement, "turn-3")
        self.assertEqual(staged["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_current_schema_missing_ledger_fails_closed_even_if_global_passed(self) -> None:
        self.approve_contract()
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            state["state_schema_version"], CLICK_GATE.CONTRACT_STATE_SCHEMA_VERSION
        )
        del state["evidence_state"]
        state["verification"]["status"] = "passed"
        state["verification"]["verified_revision"] = state["verification"][
            "mutation_revision"
        ]
        state_path.write_text(json.dumps(state), encoding="utf-8")

        self.assertFalse(CLICK_LIFECYCLE.contract_is_completed(state))
        replacement = self.contract()
        replacement["outcome"] = "send a purchasing summary"
        self.arm_gate("turn-3")
        staged = self.stage_gate(replacement, "turn-3")
        self.assertEqual(staged["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_unknown_state_schema_fails_closed_with_valid_ledger(self) -> None:
        self.approve_contract()
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        source = state["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
        source["status"] = "passed"
        source["verified_revision"] = state["verification"]["mutation_revision"]
        state["state_schema_version"] = 999
        state_path.write_text(json.dumps(state), encoding="utf-8")

        self.assertFalse(CLICK_LIFECYCLE.contract_is_completed(state))
        replacement = self.contract()
        replacement["outcome"] = "send a purchasing summary"
        self.arm_gate("turn-3")
        staged = self.stage_gate(replacement, "turn-3")
        self.assertEqual(staged["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_malformed_current_evidence_ledger_fails_closed(self) -> None:
        self.approve_contract()
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["evidence_state"] = {"version": 1, "sources": {}}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.assertFalse(CLICK_LIFECYCLE.contract_is_completed(state))

        mutation = self.pre_tool(
            "apply_patch", "*** Begin Patch\n*** End Patch", "turn-2"
        )
        self.assertEqual(
            mutation["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn(
            "malformed", mutation["hookSpecificOutput"]["permissionDecisionReason"]
        )

    def test_partial_evidence_ledger_entry_loss_fails_closed(self) -> None:
        contract = self.contract()
        contract["verification"]["evidence"].append(
            {"id": "E-manual", "kind": "manual", "description": "operator check"}
        )
        contract["verification"]["done_when"].append(
            {"condition": "operator view is correct", "primary_evidence": "E-manual"}
        )
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        del state["evidence_state"]["sources"][
            CLICK_EVIDENCE.evidence_key("E-manual")
        ]
        remaining = state["evidence_state"]["sources"][CLICK_EVIDENCE.evidence_key("E1")]
        remaining["status"] = "passed"
        remaining["verified_revision"] = state["verification"]["mutation_revision"]
        state_path.write_text(json.dumps(state), encoding="utf-8")

        self.assertFalse(CLICK_LIFECYCLE.contract_is_completed(state))
        verification = self.verify_gate([self.verification_argv()])
        self.assertEqual(
            verification["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn(
            "malformed", verification["hookSpecificOutput"]["permissionDecisionReason"]
        )

    def test_malformed_stored_id_does_not_fall_back_to_the_digest(self) -> None:
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        compatibility_id = f"ctr_{state['contract_digest'][:32]}"
        state["contract_id"] = "broken"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        context = self.prompt_submit("승인합니다", "turn-2")["hookSpecificOutput"][
            "additionalContext"
        ]
        self.assertNotIn(compatibility_id, context)
        self.arm_gate("turn-2")
        denied = self.pass_gate(compatibility_id, "turn-2")
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(
            "no recoverable contract_id",
            denied["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_pass_rejects_corrupted_staged_digest(self) -> None:
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        contract_id = self.active_contract_id()
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["contract_digest"] = "broken"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        context = self.prompt_submit("승인합니다", "turn-2")["hookSpecificOutput"][
            "additionalContext"
        ]
        self.assertNotIn(contract_id, context)

        self.arm_gate("turn-2")
        denied = self.pass_gate(contract_id, "turn-2")
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(
            "digest is unavailable or invalid",
            denied["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_stage_requires_explicit_arm(self) -> None:
        payload = self.stage_gate(turn_id="turn-1")
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("Arm Click", output["permissionDecisionReason"])

    def test_pass_rejects_an_id_different_from_the_staged_contract(self) -> None:
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        self.arm_gate("turn-2")
        payload = self.pass_gate("ctr_" + ("f" * 32), "turn-2")
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("contract_id differs", output["permissionDecisionReason"])

    def test_contract_id_cannot_cross_session_or_working_directory(self) -> None:
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        contract_id = self.active_contract_id()
        original_event = self.base_event

        for identity_change, turn_id in (
            ({"session_id": "session-2"}, "turn-other-session"),
            ({"cwd": str(self.workspace / "other")}, "turn-other-cwd"),
        ):
            with self.subTest(identity_change=identity_change):
                self.base_event = {**original_event, **identity_change}
                self.arm_gate(turn_id)
                denied = self.pass_gate(contract_id, turn_id)
                self.assertEqual(
                    denied["hookSpecificOutput"]["permissionDecision"], "deny"
                )
                self.assertIn(
                    "No staged",
                    denied["hookSpecificOutput"]["permissionDecisionReason"],
                )
        self.base_event = original_event

    def test_contract_can_be_replaced_only_after_a_new_user_turn(self) -> None:
        original = self.contract()
        self.arm_gate("turn-1")
        self.stage_gate(original, "turn-1")
        revised = self.contract()
        revised["build"]["approach"] = [
            *revised["build"]["approach"],
            "update approved API documentation",
        ]
        same_turn = self.stage_gate(revised, "turn-1")
        self.assertEqual(
            same_turn["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn(
            "already staged", same_turn["hookSpecificOutput"]["permissionDecisionReason"]
        )

        self.arm_gate("turn-2")
        replacement = self.stage_gate(revised, "turn-2")
        self.assertEqual(
            replacement["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        same_turn_pass = self.pass_gate(turn_id="turn-2")
        self.assertEqual(
            same_turn_pass["hookSpecificOutput"]["permissionDecision"], "deny"
        )

        self.arm_gate("turn-3")
        payload = self.pass_gate(turn_id="turn-3")
        self.assertEqual(
            payload["hookSpecificOutput"]["updatedInput"]["command"],
            "echo Click mutation gate passed",
        )

    def test_identical_staged_contract_cannot_be_restaged_in_a_later_turn(self) -> None:
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        self.arm_gate("turn-2")

        repeated = self.stage_gate(turn_id="turn-2")
        self.assertEqual(
            repeated["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn(
            "identical Click execution contract is already staged",
            repeated["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_always_on_cannot_stage_and_pass_in_the_same_user_turn(self) -> None:
        self.set_default("on", "turn-1")
        staged = self.stage_gate(turn_id="turn-1")
        self.assertEqual(staged["hookSpecificOutput"]["permissionDecision"], "allow")

        same_turn = self.pass_gate(turn_id="turn-1")
        self.assertEqual(
            same_turn["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn(
            "separate user response",
            same_turn["hookSpecificOutput"]["permissionDecisionReason"],
        )

        later_turn = self.pass_gate(turn_id="turn-2")
        self.assertEqual(
            later_turn["hookSpecificOutput"]["permissionDecision"], "allow"
        )

    def test_stage_and_pass_require_a_user_prompt_for_their_exact_turn(self) -> None:
        self.set_default("on", "turn-1")
        stage_command = f"click-gate stage {shlex.quote(json.dumps(self.contract()))}"
        missing_stage_prompt = self.pre_tool(
            "Bash", stage_command, "turn-2", submit_prompt=False
        )
        self.assertEqual(
            missing_stage_prompt["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn(
            "UserPromptSubmit",
            missing_stage_prompt["hookSpecificOutput"]["permissionDecisionReason"],
        )

        self.stage_gate(turn_id="turn-2")
        pass_command = f"click-gate pass {self.active_contract_id()}"
        missing_pass_prompt = self.pre_tool(
            "Bash", pass_command, "turn-3", submit_prompt=False
        )
        self.assertEqual(
            missing_pass_prompt["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn(
            "UserPromptSubmit",
            missing_pass_prompt["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_contract_records_staging_and_approval_turns(self) -> None:
        self.approve_contract()
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["staged_turn_id"], "turn-1")
        self.assertEqual(state["approved_turn_id"], "turn-2")

    def test_completed_contract_cannot_be_passed_again(self) -> None:
        self.set_default("guarded", "turn-0")
        self.approve_contract()
        verification = self.verify_gate([self.verification_argv()])
        result = self.run_rewritten(verification)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.arm_gate("turn-3")
        replay = self.pass_gate(turn_id="turn-3")
        self.assertEqual(replay["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(
            "already completed",
            replay["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_approved_contract_cannot_be_replaced_mid_run(self) -> None:
        original = self.contract()
        self.arm_gate("turn-1")
        self.stage_gate(original, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")

        replacement = self.contract()
        replacement["build"]["approach"] = [
            *replacement["build"]["approach"],
            "rewrite unrelated API",
        ]
        payload = self.stage_gate(replacement, "turn-2")
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("one approved contract", output["permissionDecisionReason"])

    def test_failed_contract_cannot_be_replaced(self) -> None:
        self.approve_contract()
        failed = self.verify_gate([self.verification_argv(1)])
        result = self.run_rewritten(failed)
        self.assertEqual(result.returncode, 1)

        replacement = self.contract()
        replacement["outcome"] = "send a purchasing summary"
        self.arm_gate("turn-3")
        payload = self.stage_gate(replacement, "turn-3")
        self.assertEqual(
            payload["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn(
            "every declared source",
            payload["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_stale_completed_contract_cannot_be_replaced(self) -> None:
        self.approve_contract()
        passed = self.verify_gate([self.verification_argv()])
        result = self.run_rewritten(passed)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )

        replacement = self.contract()
        replacement["outcome"] = "send a purchasing summary"
        self.arm_gate("turn-3")
        payload = self.stage_gate(replacement, "turn-3")
        self.assertEqual(
            payload["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_completed_contract_allows_fresh_next_contract_state(self) -> None:
        self.approve_contract()
        completed_id = self.active_contract_id()
        passed = self.verify_gate([self.verification_argv()])
        result = self.run_rewritten(passed)
        self.assertEqual(result.returncode, 0, result.stderr)

        replacement = self.contract()
        replacement["outcome"] = "send a purchasing summary"
        replacement["verification"]["scale"] = "quick"
        self.arm_gate("turn-3")
        payload = self.stage_gate(replacement, "turn-3")
        self.assertEqual(
            payload["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "staged")
        self.assertRegex(state["contract_id"], r"^ctr_[0-9a-f]{32}$")
        self.assertNotEqual(state["contract_id"], completed_id)
        self.assertEqual(state["verification"]["status"], "ready")
        self.assertEqual(state["verification"]["scale"], "quick")
        self.assertEqual(state["verification"]["mutation_revision"], 0)
        self.assertEqual(state["observations"], {"entries": {}})
        self.assertEqual(state["mutation"]["status"], "idle")

    def test_completed_guarded_contract_rolls_into_default_evidence(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.approve_contract()
        completed_id = self.active_contract_id()
        passed = self.verify_gate([self.verification_argv()])
        result = self.run_rewritten(passed)
        self.assertEqual(result.returncode, 0, result.stderr)

        context = self.prompt_submit("start a new task", "turn-3")[
            "hookSpecificOutput"
        ]["additionalContext"]
        self.assertIn("Evidence mode is enabled", context)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "evidence")
        self.assertEqual(state["runtime_mode"], "evidence")
        self.assertEqual(state["contract_id"], "")
        self.assertNotEqual(state["contract_id"], completed_id)
        self.assertEqual(state["verification"]["mutation_revision"], 0)

    def test_approved_contract_cannot_be_restaged_unchanged(self) -> None:
        self.approve_contract()
        payload = self.stage_gate(turn_id="turn-2")
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("Do not restage", output["permissionDecisionReason"])

    def test_revised_stage_invalidates_the_previous_contract_id(self) -> None:
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        previous_id = self.active_contract_id()
        changed = self.contract()
        changed["verification"]["scale"] = "full"
        self.arm_gate("turn-2")
        replacement = self.stage_gate(changed, "turn-2")
        self.assertEqual(
            replacement["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        current_id = self.active_contract_id()
        self.assertNotEqual(previous_id, current_id)
        self.arm_gate("turn-3")
        payload = self.pass_gate(previous_id, "turn-3")
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("contract_id differs", output["permissionDecisionReason"])
        accepted = self.pass_gate(current_id, "turn-3")
        self.assertEqual(
            accepted["hookSpecificOutput"]["permissionDecision"], "allow"
        )

    def test_bypass_preserves_the_staged_contract_for_later_turn(self) -> None:
        self.set_default("manual", "turn-0")
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        bypassed = self.bypass_gate("turn-1")
        self.assertEqual(
            bypassed["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertIsNone(
            self.pre_tool(
                "apply_patch",
                "*** Begin Patch\n*** End Patch",
                turn_id="turn-1",
                submit_prompt=False,
            )
        )
        blocked = self.pre_tool(
            "apply_patch", "*** Begin Patch\n*** End Patch", turn_id="turn-2"
        )
        self.assertEqual(blocked["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(
            "active execution contract",
            blocked["hookSpecificOutput"]["permissionDecisionReason"],
        )
        self.arm_gate("turn-2")
        payload = self.pass_gate(turn_id="turn-2")
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_valid_contract_is_recorded_and_control_command_is_rewritten(self) -> None:
        self.arm_gate("turn-1")
        staged = self.stage_gate(turn_id="turn-1")
        contract_id = self.active_contract_id()
        self.assertEqual(
            staged["hookSpecificOutput"]["updatedInput"]["command"],
            f"echo CLICK_CONTRACT_ID={contract_id}",
        )
        projection = staged["hookSpecificOutput"]["additionalContext"]
        self.assertIn(f"CLICK_CONTRACT_ID={contract_id}", projection)
        self.assertIn(
            "Plain-language contract\n" + self.contract()["plain_language"],
            projection,
        )
        self.assertEqual(projection.count(self.contract()["plain_language"]), 1)
        self.assertNotIn(self.contract()["outcome"], projection)
        self.assertNotIn(self.contract()["build"]["approach"][0], projection)
        self.assertNotIn("Scale: focused", projection)
        self.assertIn("unless the user asks to see the original contract", projection)
        self.assertNotIn("inventory", contract_id)
        self.arm_gate("turn-2")
        payload = self.pass_gate(turn_id="turn-2")
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "allow")
        self.assertEqual(
            output["updatedInput"]["command"], "echo Click mutation gate passed"
        )
        self.assertIsNone(
            self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2")
        )
        mutation = self.mutate_gate(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('generated.txt').write_text('ok')",
            ],
            "turn-2",
        )
        self.assertEqual(self.run_rewritten(mutation).returncode, 0)
        self.assertEqual(
            (self.workspace / "generated.txt").read_text(encoding="utf-8"), "ok"
        )

    def test_state_retains_bounded_display_but_not_full_contract_or_read_content(self) -> None:
        self.approve_contract()
        (self.workspace / "private-marker.txt").write_text(
            "private marker\n", encoding="utf-8"
        )
        observation = self.pre_tool(
            "Bash", self.read_file_command("private-marker.txt"), "turn-2"
        )
        self.assertEqual(self.run_rewritten(observation).returncode, 0)
        state_text = "\n".join(
            path.read_text()
            for path in (self.plugin_data / "gate-state").glob("*.json")
        )
        self.assertIn("contract_digest", state_text)
        self.assertRegex(state_text, r'"contract_id":"ctr_[0-9a-f]{32}"')
        self.assertIn('"scale":"focused"', state_text)
        self.assertIn('"unit_limit":4', state_text)
        self.assertNotIn("inventory write path", state_text)
        # The result-first local viewer deliberately retains a bounded display
        # copy. It is not the canonical contract and never grants authority.
        state = json.loads(next((self.plugin_data / "gate-state").glob("session-contract-*.json")).read_text())
        self.assertIn("threshold crossing", json.dumps(state["presentation"]))
        self.assertEqual(set(state["presentation"]), {
            "name", "promises", "in_scope", "out_of_scope", "must_hold", "evidence_labels"
        })
        self.assertNotIn("existing notification mechanism", state_text)
        self.assertNotIn("재고가 임계값", state_text)
        self.assertNotIn('"E1"', state_text)
        self.assertIn(CLICK_EVIDENCE.evidence_key("E1"), state_text)
        self.assertNotIn("private-marker.txt", state_text)
        self.assertNotIn("private marker", state_text)

    def test_structured_mutation_state_does_not_store_command_plaintext(self) -> None:
        self.approve_contract()
        private_body = "mutation-private-marker"
        payload = self.mutate_gate(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    f"Path('generated.txt').write_text('{private_body}')"
                ),
            ],
            "turn-2",
        )
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        state_text = "\n".join(
            path.read_text()
            for path in (self.plugin_data / "gate-state").glob("*.json")
        )
        self.assertIn("request_digest", state_text)
        self.assertNotIn(private_body, state_text)
        self.assertNotIn("generated.txt", state_text)

    def test_gate_pass_does_not_leak_into_a_new_turn(self) -> None:
        self.set_mode("strict")
        self.stage_gate(turn_id="turn-1")
        self.pass_gate(turn_id="turn-2")
        payload = self.pre_tool(
            "apply_patch",
            "*** Begin Patch\n*** End Patch",
            turn_id="turn-3",
        )
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_bypass_requires_exact_same_turn_one_use_authorization(self) -> None:
        self.set_default("on", "turn-0")
        denied = self.pre_tool("Bash", "click-gate bypass", "turn-1")
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

        self.prompt_submit("@Click bypass extra", "turn-2")
        malformed = self.pre_tool(
            "Bash", "click-gate bypass", "turn-2", submit_prompt=False
        )
        self.assertEqual(malformed["hookSpecificOutput"]["permissionDecision"], "deny")

        self.prompt_submit(
            "[@Click](plugin://click@click) BYPASS\nDo this turn without Click.",
            "turn-3",
        )
        authorized = self.pre_tool(
            "Bash", "click-gate bypass", "turn-3", submit_prompt=False
        )
        self.assertEqual(authorized["hookSpecificOutput"]["permissionDecision"], "allow")
        reused = self.pre_tool(
            "Bash", "click-gate bypass", "turn-3", submit_prompt=False
        )
        self.assertEqual(reused["hookSpecificOutput"]["permissionDecision"], "deny")

        self.prompt_submit("@Click bypass", "turn-4")
        later = self.pre_tool(
            "Bash", "click-gate bypass", "turn-5", submit_prompt=False
        )
        self.assertEqual(later["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_cancel_requires_authorization_and_clears_contract_once(self) -> None:
        self.set_default("manual", "turn-0")
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")

        denied = self.pre_tool("Bash", "click-gate cancel", "turn-2")
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        contract_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        self.assertTrue(contract_path.exists())

        self.prompt_submit("@click cancel", "turn-3")
        cancelled = self.pre_tool(
            "Bash", "click-gate cancel", "turn-3", submit_prompt=False
        )
        self.assertEqual(cancelled["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertFalse(contract_path.exists())
        reused = self.pre_tool(
            "Bash", "click-gate cancel", "turn-3", submit_prompt=False
        )
        self.assertEqual(reused["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIsNone(
            self.pre_tool(
                "apply_patch", "*** Begin Patch\n*** End Patch", turn_id="turn-4"
            )
        )

    def test_prompt_authorization_accepts_only_allowlisted_first_line_forms(self) -> None:
        accepted = {
            "@Click bypass": "bypass",
            "@click BYPASS": "bypass",
            "  [@Click](plugin://click@click) bypass  \nContinue with the task.": "bypass",
            "[@click](plugin://click@click) CANCEL\nContinue with the task.": "cancel",
        }
        for prompt, expected in accepted.items():
            with self.subTest(prompt=prompt):
                self.assertEqual(CLICK_PROMPT.prompt_authorization(prompt), expected)

        rejected = (
            "@Click bypass extra",
            "[@Click](plugin://click@click) bypass,",
            "[@Click](plugin://other@click) bypass",
            "[@Other](plugin://click@click) bypass",
            "[@Click](plugin://click@CLICK) bypass",
            "Please use @Click bypass",
            "`@Click bypass`",
            "Continue with the task.\n@Click bypass",
        )
        for prompt in rejected:
            with self.subTest(prompt=prompt):
                self.assertEqual(CLICK_PROMPT.prompt_authorization(prompt), "")

    def test_manual_incomplete_contract_survives_eight_day_cleanup(self) -> None:
        self.set_default("manual", "turn-0")
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        contract_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        old = time.time() - 8 * 24 * 60 * 60
        os.utime(contract_path, (old, old))

        self.prompt_submit("continue the approved work", "turn-3")
        self.assertTrue(contract_path.exists())
        blocked = self.pre_tool(
            "Bash", "python3 update_schema.py", "turn-3", submit_prompt=False
        )
        self.assertEqual(blocked["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(
            "active execution contract",
            blocked["hookSpecificOutput"]["permissionDecisionReason"],
        )
