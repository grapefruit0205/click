from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hooks import (
    click_dashboard_projection,
    click_dependency_cache,
    click_dependency_trace,
    click_incremental,
    click_observer_control,
    click_reuse_readiness,
    click_shadow_intelligence,
)


KEY_RUN = "1" * 64
KEY_DEPENDENCY = "2" * 64
KEY_POLICY = "3" * 64
CHECK_RUN = "4" * 64
CHECK_DEPENDENCY = "5" * 64
CHECK_POLICY = "6" * 64
ENVIRONMENT = "7" * 64
EXECUTABLE = "8" * 64
HOST_COVERAGE = "9" * 64
BACKEND = "a" * 64


class ClickDashboardProjectionTests(unittest.TestCase):
    def test_readiness_is_bounded_read_only_and_never_grants_authority(self) -> None:
        state = {"verification": {
            "observer_control": {"version": 1, "mode": "authoritative", "updated_at": 1},
            "automatic_observer_attempt": {"status": "unavailable", "reason": "compiler-secret=/private/token"},
        }}
        before = copy.deepcopy(state)
        view = click_reuse_readiness.projection(state)
        self.assertEqual(state, before)
        self.assertTrue(click_reuse_readiness.is_valid(view))
        self.assertFalse(view["reuse_authorized"])
        self.assertFalse(click_observer_control.projection(state["verification"])["reuse_authorized"])
        self.assertEqual(view["preparation"], {"reason": "prerequisite-missing", "action": "check-prerequisites"})
        self.assertNotIn("secret", json.dumps(view))
        for field, parent in (("mode", view), ("reason", view["preparation"]), ("action", view["preparation"])):
            old = parent[field]
            for invalid in ([], {}, True, None):
                parent[field] = invalid
                self.assertFalse(click_reuse_readiness.is_valid(view))
            parent[field] = old
        state["verification"]["observer_control"]["mode"] = []
        self.assertEqual(click_reuse_readiness.projection(state)["mode"], "off")

    def test_readiness_ignores_malformed_decisions_and_preserves_conditional_label(self) -> None:
        entry = click_incremental.decision(
            source_key=KEY_RUN, decision="reuse-dependency",
            reason_code="conditional-observed-inputs-current", current_revision=1,
            previous_revision=0, check_digest=CHECK_RUN,
            authority_source="conditional-js-observation",
        )
        state = {"verification": {click_incremental.PLAN_FIELD: {"decisions": [entry]}}}
        view = click_reuse_readiness.projection(state)
        self.assertEqual(view["checks"][0]["reason"], "conditional")
        for field in ("decision", "reason_code", "authority_source"):
            broken = dict(entry, **{field: []})
            state["verification"][click_incremental.PLAN_FIELD]["decisions"] = [broken]
            self.assertEqual(click_reuse_readiness.projection(state)["checks"], [])
        state["verification"][click_incremental.PLAN_FIELD]["decisions"] = [entry] * 500
        self.assertEqual(len(click_reuse_readiness.projection(state)["checks"]), 1)

    def test_engine_identity_preserves_python_file_byte_provenance(self) -> None:
        hooks = self.workspace / "hooks"
        hooks.mkdir()
        sources = {"a.py": b"a\r\n", "z.py": b"z\n"}
        for name, content in sources.items():
            (hooks / name).write_bytes(content)
        expected = hashlib.sha256()
        for name, content in sorted(sources.items()):
            expected.update(name.encode() + b"\0" + hashlib.sha256(content).digest())
        with mock.patch.object(click_dashboard_projection, "__file__", str(hooks / "a.py")):
            identity = click_dashboard_projection.engine_identity()
            self.assertEqual(identity["hook_files_digest"], expected.hexdigest())
            self.assertEqual(identity["assurance"], "unsigned-files-at-snapshot")
            (hooks / "a.py").write_bytes(b"a\n")
            self.assertNotEqual(click_dashboard_projection.engine_identity()["hook_files_digest"], identity["hook_files_digest"])

    def test_engine_identity_binds_frontend_bytes_and_relative_asset_paths(self) -> None:
        hooks = self.workspace / "hooks"
        hooks.mkdir()
        (hooks / "runtime.py").write_bytes(b"runtime\n")
        assets = [
            "dashboard/index.html", "dashboard/styles.css", "dashboard/app.js",
            "dashboard/locales/ko.json", "dashboard/locales/en.json", "dashboard/locales/zh-CN.json",
        ]
        for name in reversed(assets):
            path = hooks / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"first")
        with mock.patch.object(click_dashboard_projection, "__file__", str(hooks / "runtime.py")):
            baseline = click_dashboard_projection.engine_identity()["hook_files_digest"]
            self.assertIsNotNone(baseline)
            self.assertEqual(click_dashboard_projection.engine_identity()["hook_files_digest"], baseline)
            for name in assets:
                with self.subTest(asset=name):
                    path = hooks / name
                    original_stat = path.stat()
                    path.write_bytes(b"other")
                    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
                    self.assertNotEqual(click_dashboard_projection.engine_identity()["hook_files_digest"], baseline)
                    path.write_bytes(b"first")
                    self.assertEqual(click_dashboard_projection.engine_identity()["hook_files_digest"], baseline)
            locale = hooks / "dashboard/locales/en.json"
            renamed = hooks / "dashboard/en.json"
            locale.rename(renamed)
            self.assertNotEqual(click_dashboard_projection.engine_identity()["hook_files_digest"], baseline)

    def test_projection_v6_through_v9_remain_readable_and_new_fields_are_validated(self) -> None:
        value = click_dashboard_projection.dashboard_projection({})
        self.assertTrue(click_dashboard_projection.projection_is_valid(value))
        v10 = copy.deepcopy(value)
        v10["version"] = 10
        v10.pop("reuse_reasons")
        self.assertTrue(click_dashboard_projection.projection_is_valid(v10))
        v9 = copy.deepcopy(v10)
        v9["version"] = 9
        v9.pop("readiness")
        self.assertTrue(click_dashboard_projection.projection_is_valid(v9))
        invalid = copy.deepcopy(value)
        invalid["readiness"]["reuse_authorized"] = True
        self.assertFalse(click_dashboard_projection.projection_is_valid(invalid))
        reasons = value["reuse_reasons"]
        self.assertEqual(reasons["request_count"], 0)
        self.assertEqual(reasons["run_reasons"], [])
        self.assertEqual(set(reasons["decisions"]), set(click_incremental.DECISIONS))
        unbalanced = copy.deepcopy(value)
        unbalanced["reuse_reasons"]["request_count"] = 3
        self.assertFalse(click_dashboard_projection.projection_is_valid(unbalanced))
        unsorted_reasons = copy.deepcopy(value)
        unsorted_reasons["reuse_reasons"]["decisions"]["run"] = 2
        unsorted_reasons["reuse_reasons"]["request_count"] = 2
        unsorted_reasons["reuse_reasons"]["run_reasons"] = [
            {"reason_code": "a-reason", "count": 1},
            {"reason_code": "b-reason", "count": 2},
        ]
        self.assertFalse(click_dashboard_projection.projection_is_valid(unsorted_reasons))
        unsorted_reasons["reuse_reasons"]["run_reasons"].reverse()
        self.assertFalse(click_dashboard_projection.projection_is_valid(unsorted_reasons))
        # Reason counts may never exceed the retained non-reuse decisions.
        unsorted_reasons["reuse_reasons"]["decisions"]["run"] = 3
        unsorted_reasons["reuse_reasons"]["request_count"] = 3
        self.assertTrue(click_dashboard_projection.projection_is_valid(unsorted_reasons))
        readiness_fields = {
            "command_status",
            "inventory_status",
            "exact_reuse_status",
            "policy_reuse_status",
            "authoritative_reuse_status",
            "next_action_code",
        }
        v8 = copy.deepcopy(value)
        v8["version"] = 8
        v8.pop("readiness")
        v8.pop("reuse_reasons")
        for field in readiness_fields:
            v8["setup"].pop(field)
        self.assertTrue(click_dashboard_projection.projection_is_valid(v8))
        v7 = copy.deepcopy(value)
        v7["version"] = 7
        v7.pop("readiness")
        v7.pop("reuse_reasons")
        v7.pop("task_efficiency")
        for field in readiness_fields:
            v7["setup"].pop(field)
        self.assertTrue(click_dashboard_projection.projection_is_valid(v7))
        previous = copy.deepcopy(value)
        previous["version"] = 6
        previous.pop("readiness")
        previous.pop("reuse_reasons")
        previous.pop("retained_impact")
        previous.pop("task_efficiency")
        for field in readiness_fields:
            previous["setup"].pop(field)
        self.assertTrue(click_dashboard_projection.projection_is_valid(previous))
        value["retained_impact"]["reused_group_request_count"] = 999
        self.assertFalse(click_dashboard_projection.projection_is_valid(value))

    def test_display_copy_rejects_paths_secrets_and_html_without_authority(self) -> None:
        state = {"status": "staged", "runtime_mode": "guarded", "presentation": {
            "name": "password abc", "promises": ["/home/private/repo", "<script>alert(1)</script>"],
            "evidence_labels": {KEY_RUN: "secret token"},
        }}
        projection = click_dashboard_projection.dashboard_projection(state)
        rendered = json.dumps(projection)
        for forbidden in ("password abc", "/home/private", "<script>", "secret token"):
            self.assertNotIn(forbidden, rendered)
        self.assertFalse(projection["task"]["approval_bound"])
        self.assertTrue(click_dashboard_projection.projection_is_valid(projection))

    def test_first_guarded_contract_shows_promise_approval_and_unstarted_sources(self) -> None:
        state = {
            "runtime_mode": "guarded", "status": "staged", "contract_id": "ctr_" + "a" * 32,
            "staged_turn_id": "one", "approved_turn_id": "",
            "presentation": {"name": "새 계약 검증", "promises": ["관련 검증을 통과한다"],
                             "in_scope": ["검증 경로"], "out_of_scope": ["배포"], "must_hold": ["별도 승인 유지"],
                             "evidence_labels": {KEY_RUN: "핵심 동작"}},
            "verification": {"mutation_revision": 0},
            "evidence_state": {"sources": {KEY_RUN: {"kind": "argv", "status": "ready"}}},
        }
        projection = click_dashboard_projection.dashboard_projection(state)
        self.assertEqual(projection["task"]["name"], "새 계약 검증")
        self.assertEqual(projection["task"]["promises"], ["관련 검증을 통과한다"])
        self.assertFalse(projection["task"]["approval_bound"])
        self.assertEqual(projection["sources"][0]["execution_status"], "not-run")
        self.assertIn("검증", projection["sources"][0]["next_action"])
        self.assertIsNone(projection["accounting"]["reuse_rate"])
        self.assertTrue(click_dashboard_projection.projection_is_valid(projection))
        state.update(status="approved", approved_turn_id="two")
        self.assertTrue(click_dashboard_projection.dashboard_projection(state)["task"]["approval_bound"])

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        (self.workspace / "src").mkdir()
        for relative, content in (
            ("src/old.py", "old\n"),
            ("src/shared.py", "before\n"),
            ("src/stable.py", "stable\n"),
        ):
            (self.workspace / relative).write_text(content, encoding="utf-8")

    def record(
        self,
        *,
        key: str,
        check: str,
        revision: int,
        inputs: list[dict[str, object]],
        duration_ms: int,
    ) -> dict[str, object]:
        return click_dependency_cache.shadow_observer_record(
            evidence_key=key,
            check_digest=check,
            mutation_revision=revision,
            backend_name="test-observer",
            backend_version="1",
            backend_digest=BACKEND,
            inputs=inputs,
            command_duration_ms=duration_ms,
            observer_overhead_ms=10,
        )

    def baseline(self, record: dict[str, object]) -> dict[str, object]:
        result = click_shadow_intelligence.build_baseline(
            record,
            workspace=self.workspace,
            environment_digest=ENVIRONMENT,
            executable_digest=EXECUTABLE,
            host_coverage_digest=HOST_COVERAGE,
        )
        self.assertIsNotNone(result)
        assert result is not None
        return result

    def timing_baseline(
        self, *, key: str, check: str, duration_ms: int
    ) -> dict[str, object]:
        binding = click_incremental.timing_binding_digest(
            source_key=key,
            check_digest=check,
            environment_digest=ENVIRONMENT,
            executable_digest=EXECUTABLE,
            host_coverage_digest=HOST_COVERAGE,
            observer_mode="shadow",
        )
        result = click_incremental.build_duration_baseline(
            duration_ms=duration_ms,
            source_key=key,
            revision=1,
            check_digest=check,
            observed_at=1,
            batch_id="b" * 32,
            origin_task={"mode": "guarded", "id": "ctr_" + "d" * 32},
            observer_mode="shadow",
            timing_binding_digest=binding,
        )
        self.assertIsNotNone(result)
        assert result is not None
        return result

    def prediction(
        self,
        baseline: dict[str, object],
        *,
        key: str,
        check: str,
    ) -> dict[str, object]:
        return click_shadow_intelligence.predict(
            baseline,
            workspace=self.workspace,
            evidence_key=key,
            check_digest=check,
            mutation_revision=2,
            environment_digest=ENVIRONMENT,
            executable_digest=EXECUTABLE,
            host_coverage_digest=HOST_COVERAGE,
            prepared_at=20,
        )

    def state(self) -> dict[str, object]:
        changed_previous = self.record(
            key=KEY_RUN,
            check=CHECK_RUN,
            revision=1,
            inputs=[
                {"path": "src/old.py", "kind": "file", "operations": ["read"]},
                {
                    "path": "src/shared.py",
                    "kind": "file",
                    "operations": ["read"],
                },
            ],
            duration_ms=700,
        )
        stable_previous = self.record(
            key=KEY_DEPENDENCY,
            check=CHECK_DEPENDENCY,
            revision=1,
            inputs=[
                {
                    "path": "src/stable.py",
                    "kind": "file",
                    "operations": ["metadata", "read"],
                }
            ],
            duration_ms=900,
        )
        changed_baseline = self.baseline(changed_previous)
        stable_baseline = self.baseline(stable_previous)

        (self.workspace / "src/old.py").unlink()
        (self.workspace / "src/shared.py").write_text("after\n", encoding="utf-8")
        (self.workspace / "src/new.py").write_text("new\n", encoding="utf-8")
        changed_prediction = self.prediction(
            changed_baseline, key=KEY_RUN, check=CHECK_RUN
        )
        stable_prediction = self.prediction(
            stable_baseline, key=KEY_DEPENDENCY, check=CHECK_DEPENDENCY
        )
        changed_current = self.record(
            key=KEY_RUN,
            check=CHECK_RUN,
            revision=2,
            inputs=[
                {
                    "path": "src/shared.py",
                    "kind": "file",
                    "operations": ["read"],
                },
                {"path": "src/new.py", "kind": "file", "operations": ["read"]},
            ],
            duration_ms=650,
        )
        stable_current = self.record(
            key=KEY_DEPENDENCY,
            check=CHECK_DEPENDENCY,
            revision=2,
            inputs=[
                {
                    "path": "src/stable.py",
                    "kind": "file",
                    "operations": ["metadata", "read"],
                }
            ],
            duration_ms=900,
        )
        changed_evaluation = click_shadow_intelligence.evaluate(
            changed_prediction,
            changed_baseline,
            changed_current,
            actual_exit_code=0,
            workspace_changed=False,
            evaluated_at=30,
        )
        stable_evaluation = click_shadow_intelligence.evaluate(
            stable_prediction,
            stable_baseline,
            stable_current,
            actual_exit_code=0,
            workspace_changed=False,
            evaluated_at=30,
        )
        self.assertIsNotNone(changed_evaluation)
        self.assertIsNotNone(stable_evaluation)

        decisions = [
            click_incremental.decision(
                source_key=KEY_RUN,
                decision="run",
                reason_code="observed-input-changed",
                current_revision=2,
                previous_revision=1,
                check_digest=CHECK_RUN,
                authority_source="runner",
            ),
            click_incremental.decision(
                source_key=KEY_DEPENDENCY,
                decision="reuse-dependency",
                reason_code="observed-dependencies-unchanged",
                current_revision=2,
                previous_revision=1,
                check_digest=CHECK_DEPENDENCY,
                authority_source="runtime-dependency-observation",
                estimated_avoided_ms=900,
                duration_baseline=self.timing_baseline(
                    key=KEY_DEPENDENCY,
                    check=CHECK_DEPENDENCY,
                    duration_ms=900,
                ),
            ),
            click_incremental.decision(
                source_key=KEY_POLICY,
                decision="reuse-safe-change",
                reason_code="safe-change-policy-covered",
                current_revision=2,
                previous_revision=1,
                check_digest=CHECK_POLICY,
                authority_source="repository-safe-change-policy",
                estimated_avoided_ms=1800,
                duration_baseline=self.timing_baseline(
                    key=KEY_POLICY,
                    check=CHECK_POLICY,
                    duration_ms=1800,
                ),
            ),
        ]
        verification: dict[str, object] = {"mutation_revision": 2}
        plan = click_incremental.build_plan(decisions, current_revision=2, planned_at=10)
        click_incremental.store_plan(verification, plan)
        click_incremental.store_batch(verification, click_incremental.new_batch(
            plan, batch_id="a" * 32, revision=2, prepared_ms=12,
        ))
        self.assertTrue(click_incremental.record_execution(
            verification, {KEY_RUN: 650}, source_results={KEY_RUN: {
                "status": "passed", "started": True, "completed": True, "reason_code": "command-passed",
            }}, reused_keys={KEY_DEPENDENCY, KEY_POLICY}, exit_code=0, runner_duration_ms=670,
        ))
        click_dependency_trace.store_records(
            verification,
            {KEY_RUN: changed_current, KEY_DEPENDENCY: stable_current},
        )
        verification[click_shadow_intelligence.SHADOW_INTELLIGENCE_FIELD] = {
            "version": click_shadow_intelligence.STATE_VERSION,
            "sources": {
                KEY_RUN: {
                    "baseline": changed_baseline,
                    "prediction": changed_prediction,
                    "evaluation": changed_evaluation,
                },
                KEY_DEPENDENCY: {
                    "baseline": stable_baseline,
                    "prediction": stable_prediction,
                    "evaluation": stable_evaluation,
                },
            },
        }
        click_observer_control.set_mode(verification, "shadow", updated_at=40)
        self.assertTrue(
            click_shadow_intelligence.state_is_valid(
                verification[click_shadow_intelligence.SHADOW_INTELLIGENCE_FIELD]
            )
        )
        return {
            "status": "approved",
            "runtime_mode": "guarded",
            "verification": verification,
            "evidence_state": {
                "sources": {
                    KEY_RUN: {"status": "passed"},
                    KEY_DEPENDENCY: {"status": "passed"},
                    KEY_POLICY: {"status": "passed"},
                }
            },
            "workspace": str(self.workspace),
            "prompt": "TOP-SECRET-PROMPT",
            "access_token": "TOP-SECRET-TOKEN",
        }

    def test_projection_separates_authoritative_roi_from_shadow_telemetry(self) -> None:
        projection = click_dashboard_projection.dashboard_projection(
            self.state(), generated_at=50
        )

        self.assertTrue(click_dashboard_projection.projection_is_valid(projection))
        self.assertEqual(projection["version"], 11)
        self.assertEqual(
            projection["task_efficiency"]["measurement_status"], "unmeasured"
        )
        self.assertEqual(projection["task_efficiency"]["presentations"], [])
        self.assertEqual(projection["setup"]["status"], "unconfigured")
        incremental = projection["summary"]["incremental"]
        self.assertEqual(incremental["total_source_count"], 3)
        self.assertEqual(incremental["current_source_count"], 3)
        self.assertEqual(incremental["executed_source_count"], 1)
        self.assertEqual(incremental["authoritative_reuse_count"], 2)
        self.assertEqual(incremental["dependency_reuse_count"], 1)
        self.assertEqual(incremental["safe_change_reuse_count"], 1)
        self.assertEqual(incremental["executed_duration_ms"], 650)
        self.assertEqual(incremental["estimated_avoided_ms"], 2700)
        savings = projection["summary"]["revalidation_savings"]
        self.assertEqual(savings["omitted_test_execution_ms"], 2700)
        self.assertEqual(savings["executed_test_execution_ms"], 650)
        self.assertEqual(
            savings["full_sequential_test_execution_estimate_ms"], 3350
        )
        self.assertEqual(
            savings["test_execution_reduction_ratio"], 2700 / 3350
        )
        current_id = projection["history"]["current_batch_id"]
        self.assertEqual(
            projection["batch_summaries"][current_id][
                "revalidation_savings"
            ],
            savings,
        )
        self.assertEqual(
            projection["batch_summaries"][current_id]["incremental"],
            {
                key: incremental[key]
                for key in click_incremental.SUMMARY_FIELDS
            },
        )

        shadow = projection["summary"]["shadow"]
        self.assertEqual(shadow["candidate_count"], 1)
        self.assertEqual(shadow["confirmed_candidate_count"], 1)
        self.assertEqual(shadow["contradiction_count"], 0)
        self.assertEqual(shadow["potential_ms"], 900)
        self.assertEqual(shadow["observer_overhead_ms"], 20)
        self.assertFalse(shadow["tracing_slowdown_measured"])
        self.assertEqual(projection["task"]["observer_mode"], "shadow")
        self.assertTrue(projection["task"]["observer_enabled"])

        run_source = next(
            source
            for source in projection["sources"]
            if source["id"] == f"source:{KEY_RUN[:16]}"
        )
        self.assertEqual(run_source["execution_decision"], "run")
        self.assertEqual(run_source["reason_code"], "observed-input-changed")
        self.assertEqual(run_source["authority_source"], "runner")
        self.assertEqual(run_source["shadow_decision"], "rerun-required")
        self.assertEqual(run_source["shadow_outcome"], "conservative-rerun")

    def test_selected_source_map_retains_baseline_and_current_input_states(self) -> None:
        projection = click_dashboard_projection.dashboard_projection(
            self.state(), generated_at=50
        )
        run_id = f"source:{KEY_RUN[:16]}"
        targets = {
            edge["target"]
            for edge in projection["map"]["edges"]
            if edge["source"] == run_id
        }
        statuses = {
            node["label"]: node["status"]
            for node in projection["map"]["nodes"]
            if node["id"] in targets
        }

        self.assertEqual(statuses["src/old.py"], "baseline-only")
        self.assertEqual(statuses["src/shared.py"], "changed")
        self.assertEqual(statuses["src/new.py"], "newly-observed")
        self.assertIn(
            "current-observed",
            {node["status"] for node in projection["map"]["nodes"]},
        )

    def test_source_nodes_survive_input_slicing(self) -> None:
        with mock.patch.object(click_dashboard_projection, "MAX_INPUTS", 0):
            projection = click_dashboard_projection.dashboard_projection(
                self.state(), generated_at=50
            )
            self.assertTrue(click_dashboard_projection.projection_is_valid(projection))

        source_nodes = [
            node for node in projection["map"]["nodes"] if node["type"] == "source"
        ]
        self.assertEqual(len(source_nodes), 3)
        self.assertEqual(projection["map"]["visible_input_count"], 0)
        self.assertGreater(projection["map"]["truncated_input_count"], 0)

    def test_projection_is_content_free_and_rejects_tampering(self) -> None:
        projection = click_dashboard_projection.dashboard_projection(
            self.state(), generated_at=50
        )
        encoded = json.dumps(projection, ensure_ascii=False, sort_keys=True)

        self.assertNotIn(str(self.workspace), encoded)
        self.assertNotIn("TOP-SECRET-PROMPT", encoded)
        self.assertNotIn("TOP-SECRET-TOKEN", encoded)
        self.assertNotIn("actual_saved_ms", encoded)
        self.assertNotIn("raw_argv", encoded)

        projection["prompt"] = "leak"
        self.assertFalse(click_dashboard_projection.projection_is_valid(projection))

    def test_invalid_status_is_not_reflected_into_the_dashboard(self) -> None:
        state = self.state()
        state["status"] = "PRIVATE STATUS WITH SPACES"

        projection = click_dashboard_projection.dashboard_projection(
            state, generated_at=50
        )

        self.assertEqual(projection["task"]["status"], "unknown")
        self.assertTrue(click_dashboard_projection.projection_is_valid(projection))

    def test_projection_keeps_requests_separate_and_deduplicates_redelivery(self) -> None:
        state = self.state()
        verification = state["verification"]
        first = click_incremental.current_batch(verification)
        assert first is not None
        first.update(
            version=4,
            task={
                "mode": "guarded",
                "id": "ctr_" + "1" * 32,
                "name": "첫 계약",
            },
        )
        second = copy.deepcopy(first)
        second.update(
            batch_id="c" * 32,
            timestamp=first["timestamp"] + 1,
            finished_at=first["finished_at"] + 1,
            task={
                "mode": "guarded",
                "id": "ctr_" + "2" * 32,
                "name": "재시도 계약",
            },
        )
        executed = next(item for item in second["sources"] if item["started"])
        executed["duration_ms"] = 325
        self.assertTrue(click_incremental.batch_is_valid(first))
        self.assertTrue(click_incremental.batch_is_valid(second))

        parent = click_incremental.build_plan(
            [
                click_incremental.decision(
                    source_key="f" * 64,
                    decision="run",
                    reason_code="no-passing-evidence",
                    current_revision=2,
                    previous_revision=1,
                    check_digest="e" * 64,
                    authority_source="runner",
                )
            ],
            current_revision=2,
            planned_at=1,
        )
        parent_history: dict[str, object] = {}
        click_incremental.append_plan_history(parent_history, parent)
        verification[click_incremental.HISTORY_FIELD] = [
            *parent_history[click_incremental.HISTORY_FIELD],
            first,
            copy.deepcopy(first),
            second,
        ]
        verification[click_incremental.CURRENT_BATCH_FIELD] = second["batch_id"]

        projection = click_dashboard_projection.dashboard_projection(
            state, generated_at=second["finished_at"] + 1
        )
        self.assertTrue(click_dashboard_projection.projection_is_valid(projection))
        self.assertEqual(
            set(projection["batch_summaries"]),
            {first["batch_id"], second["batch_id"]},
        )
        self.assertEqual(len(projection["batches"]), 2)
        self.assertEqual(
            [batch["task"]["id"] for batch in projection["batches"]],
            ["ctr_" + "1" * 32, "ctr_" + "2" * 32],
        )
        self.assertEqual(
            projection["batch_summaries"][first["batch_id"]]["incremental"][
                "total_source_count"
            ],
            3,
        )
        self.assertEqual(
            projection["batch_summaries"][second["batch_id"]][
                "revalidation_savings"
            ]["executed_test_execution_ms"],
            325,
        )
        self.assertEqual(
            projection["summary"]["revalidation_savings"],
            projection["batch_summaries"][second["batch_id"]][
                "revalidation_savings"
            ],
        )

    def test_projection_validator_keeps_v4_read_compatibility(self) -> None:
        projection = click_dashboard_projection.dashboard_projection(
            self.state(), generated_at=50
        )
        legacy = copy.deepcopy(projection)
        legacy["version"] = 4
        legacy.pop("readiness")
        legacy.pop("reuse_reasons")
        legacy.pop("batch_summaries")
        legacy.pop("setup")
        legacy.pop("retained_impact")
        legacy.pop("task_efficiency")
        legacy["summary"].pop("revalidation_savings")

        self.assertTrue(click_dashboard_projection.projection_is_valid(legacy))

        v5 = copy.deepcopy(projection)
        v5["version"] = 5
        v5.pop("readiness")
        v5.pop("reuse_reasons")
        v5.pop("setup")
        v5.pop("retained_impact")
        v5.pop("task_efficiency")
        self.assertTrue(click_dashboard_projection.projection_is_valid(v5))

    def test_setup_projection_preserves_measured_loss(self) -> None:
        state = self.state()
        state["auto_sharding_setup"] = {
            "version": 1,
            "status": "baseline-required",
            "sharding_ready": False,
            "reuse_ready": False,
            "reuse_status": "unavailable",
            "initial_setup_ms": 41.5,
            "observation_ms": None,
            "click_processing_ms": 3.5,
            "bootstrap_parent_ms": 10.0,
            "bootstrap_shards_ms": 17.0,
            "comparison_net_ms": -7.0,
            "comparison_scope": "first-bootstrap-parent-vs-sequential-children-not-savings",
        }

        projection = click_dashboard_projection.dashboard_projection(
            state, generated_at=50
        )

        self.assertTrue(click_dashboard_projection.projection_is_valid(projection))
        self.assertEqual(projection["setup"]["comparison_net_ms"], -7.0)
        self.assertEqual(projection["setup"]["command_status"], "unavailable")
        self.assertEqual(
            projection["setup"]["exact_reuse_status"], "unavailable"
        )
        tampered = copy.deepcopy(projection)
        tampered["setup"]["comparison_net_ms"] = float("nan")
        self.assertFalse(click_dashboard_projection.projection_is_valid(tampered))


if __name__ == "__main__":
    unittest.main()
