from __future__ import annotations

import json
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
                duration_baseline={"duration_ms": 900, "revision": 1, "check_digest": CHECK_DEPENDENCY, "observed_at": 1, "batch_id": "b" * 32, "sample_count": 1},
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
                duration_baseline={"duration_ms": 1800, "revision": 1, "check_digest": CHECK_POLICY, "observed_at": 1, "batch_id": "b" * 32, "sample_count": 1},
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
        incremental = projection["summary"]["incremental"]
        self.assertEqual(incremental["total_source_count"], 3)
        self.assertEqual(incremental["current_source_count"], 3)
        self.assertEqual(incremental["executed_source_count"], 1)
        self.assertEqual(incremental["authoritative_reuse_count"], 2)
        self.assertEqual(incremental["dependency_reuse_count"], 1)
        self.assertEqual(incremental["safe_change_reuse_count"], 1)
        self.assertEqual(incremental["executed_duration_ms"], 650)
        self.assertEqual(incremental["estimated_avoided_ms"], 2700)

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


if __name__ == "__main__":
    unittest.main()
