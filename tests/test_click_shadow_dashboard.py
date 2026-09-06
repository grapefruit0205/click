from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request

from click_gate_test_support import (
    CLICK_GATE,
    CLICK_LIFECYCLE,
    CLICK_SHADOW_DASHBOARD,
    CLICK_STATE,
    ClickGateTestCase,
    mock,
    os,
    split_runner_command,
    unittest,
)


class ClickShadowDashboardTests(ClickGateTestCase):
    def test_dashboard_javascript_parses_and_keeps_contract_prose_out_of_share_report(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node syntax check unavailable; browser verification remains separate")
        result = subprocess.run(
            [node, "--check"],
            input=CLICK_SHADOW_DASHBOARD.JS,
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        share = CLICK_SHADOW_DASHBOARD.JS.split("function shareReport()", 1)[1].split("function standaloneReport", 1)[0]
        self.assertNotIn("snapshot.task.promises", share)
        self.assertNotIn("snapshot.task.name", share)
        self.assertIn("projection_version", share)
        self.assertIn("reuse_origin", share)
        self.assertIn("revalidation_savings", share)
        self.assertIn("click_management_overhead_ms:null", share)
        self.assertIn("live_net_time_saving_reason:'counterfactual-not-measured'", share)
        self.assertNotIn("function summarize(batch)", CLICK_SHADOW_DASHBOARD.JS)
        self.assertIn("data.batch_summaries", CLICK_SHADOW_DASHBOARD.JS)
        self.assertIn("function outcomePresentation", CLICK_SHADOW_DASHBOARD.JS)
        self.assertNotIn("Math.max(2,100*value/max)", CLICK_SHADOW_DASHBOARD.JS)
        self.assertNotIn("innerHTML", CLICK_SHADOW_DASHBOARD.JS)

        html = CLICK_SHADOW_DASHBOARD.HTML
        ordered_ids = [
            'id="estimatedAvoided"',
            'id="executionComparison"',
            'id="sources"',
            'id="measurementDetails"',
        ]
        positions = [html.index(item) for item in ordered_ids]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("재사용으로 생략한 테스트 실행시간", html)
        self.assertIn("과거 성공 실행 기록 기반 추정", html)
        self.assertIn("동일 샤드 순차 기준", html)
        self.assertIn("Click 관리비용 제외", html)
        self.assertIn("총 검증 대기는 증가했습니다. 상세 보기", html)
        self.assertIn('id="setupInitial"', html)
        self.assertIn('id="setupObservation"', html)
        self.assertIn('id="setupProcessing"', html)
        self.assertIn('id="setupNet"', html)
        self.assertIn("signedDuration(setup.comparison_net_ms)", CLICK_SHADOW_DASHBOARD.JS)
        self.assertIn("Observer: authoritative", CLICK_SHADOW_DASHBOARD.JS)

    def test_guarded_contract_transition_keeps_viewer_history_without_authority(self) -> None:
        self.set_default("guarded", "turn-0")
        self.approve_contract()
        first_id = self.active_contract_id()
        self.assertEqual(self.run_rewritten(self.verify_gate([self.verification_argv()])).returncode, 0)
        self.arm_gate("turn-3")
        contract = self.contract()
        contract["outcome"] = "다음 계약의 검증"
        staged = self.stage_gate(contract, "turn-3")
        self.assertEqual(staged["hookSpecificOutput"]["permissionDecision"], "allow")
        state_path = next((self.plugin_data / "gate-state").glob("session-contract-*.json"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
        projection = CLICK_SHADOW_DASHBOARD.click_dashboard_projection.dashboard_projection(state)
        self.assertFalse(projection["task"]["approval_bound"])
        self.assertEqual(projection["task"]["name"], "다음 계약의 검증")
        self.assertNotEqual(projection["task"]["contract_id"], first_id)
        self.assertIsNone(projection["history"]["current_batch_id"])
        self.assertEqual(projection["batches"][-1]["task"]["id"], first_id)
        self.assertEqual(projection["sources"][0]["execution_status"], "not-run")
        self.assertEqual(state["verification"]["status"], "ready")
        self.assertEqual(state["approved_turn_id"], "")

    def test_control_parser_accepts_only_explicit_dashboard_actions(self) -> None:
        for action in ("start", "stop", "status"):
            self.assertEqual(
                CLICK_LIFECYCLE.control_request(f"click-gate dashboard {action}"),
                ("dashboard", action, ""),
            )
        parsed = CLICK_LIFECYCLE.control_request("click-gate dashboard erase")
        self.assertEqual(parsed[0], "")
        self.assertIn("dashboard start|stop|status", parsed[2])

    def test_prepare_start_persists_only_token_digests(self) -> None:
        self.approve_contract()
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        ).resolve()
        before = json.loads(state_path.read_text(encoding="utf-8"))
        payload = self.pre_tool(
            "Bash", "click-gate dashboard start", "turn-2", submit_prompt=False
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        action_index = tokens.index("run-dashboard-start")
        instance_id, runner_token, access_token = tokens[action_index + 2 :]
        self.assertEqual(Path(tokens[action_index + 1]), state_path)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        dashboard_path = CLICK_SHADOW_DASHBOARD._dashboard_path(state_path)
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

        self.assertEqual(dashboard["instance_id"], instance_id)
        self.assertEqual(
            dashboard["runner_token_digest"],
            hashlib.sha256(runner_token.encode()).hexdigest(),
        )
        self.assertEqual(
            dashboard["access_token_digest"],
            hashlib.sha256(access_token.encode()).hexdigest(),
        )
        encoded = state_path.read_text(encoding="utf-8")
        self.assertNotIn(runner_token, encoded)
        self.assertNotIn(access_token, encoded)
        self.assertEqual(
            state["verification"]["mutation_revision"],
            before["verification"]["mutation_revision"],
        )
        self.assertEqual(state["evidence_state"], before["evidence_state"])

    def test_session_end_requests_dashboard_cleanup_without_changing_evidence(self) -> None:
        self.approve_contract()
        self.pre_tool(
            "Bash", "click-gate dashboard start", "turn-2", submit_prompt=False
        )
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        ).resolve()
        before = json.loads(state_path.read_text(encoding="utf-8"))
        event = {
            **self.base_event,
            "turn_id": "turn-2",
            "hook_event_name": "SessionEnd",
        }

        result, _ = self.run_hook("session-end", event)

        self.assertEqual(result.returncode, 0, result.stderr)
        after = json.loads(state_path.read_text(encoding="utf-8"))
        dashboard = json.loads(
            CLICK_SHADOW_DASHBOARD._dashboard_path(state_path).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(dashboard["status"], "stopping")
        self.assertTrue(dashboard["stop_requested"])
        self.assertEqual(
            after["verification"]["mutation_revision"],
            before["verification"]["mutation_revision"],
        )
        self.assertEqual(after["evidence_state"], before["evidence_state"])

    def test_start_runner_is_one_use_and_reports_fragment_token(self) -> None:
        self.approve_contract()
        payload = self.pre_tool(
            "Bash", "click-gate dashboard start", "turn-2", submit_prompt=False
        )
        assert payload is not None
        tokens = split_runner_command(
            payload["hookSpecificOutput"]["updatedInput"]["command"]
        )
        action_index = tokens.index("run-dashboard-start")
        arguments = tokens[action_index + 1 :]
        state_path = Path(arguments[0])
        instance_id = arguments[1]

        def mark_running(*_args: object, **_kwargs: object) -> mock.Mock:
            with CLICK_STATE.state_lock():
                self.assertTrue(
                    CLICK_SHADOW_DASHBOARD._write_dashboard_fields(
                        state_path,
                        instance_id,
                        status="running",
                        port=43219,
                        pid=123,
                    )
                )
            return mock.Mock()

        with (
            mock.patch.dict(
                os.environ,
                {
                    "PLUGIN_DATA": str(self.plugin_data),
                    "CLICK_CONFIG_HOME": str(self.plugin_data),
                },
            ),
            mock.patch.object(
                CLICK_SHADOW_DASHBOARD.click_process,
                "spawn_argv",
                side_effect=mark_running,
            ),
            mock.patch.object(CLICK_SHADOW_DASHBOARD.sys, "stdout") as stdout,
        ):
            self.assertEqual(
                CLICK_SHADOW_DASHBOARD.run_start(
                    arguments,
                    runner_script=CLICK_GATE.Path(CLICK_GATE.__file__).resolve(),
                    spawn=CLICK_SHADOW_DASHBOARD.click_process.spawn_argv,
                ),
                0,
            )
            self.assertEqual(
                CLICK_SHADOW_DASHBOARD.run_start(
                    arguments,
                    runner_script=CLICK_GATE.Path(CLICK_GATE.__file__).resolve(),
                    spawn=CLICK_SHADOW_DASHBOARD.click_process.spawn_argv,
                ),
                2,
            )
        rendered = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertIn("http://127.0.0.1:43219/#token=", rendered)

    def test_server_is_loopback_read_only_authenticated_and_content_free(self) -> None:
        self.approve_contract()
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        ).resolve()
        instance_id = "dashboard-instance-12345"
        access_token = hashlib.sha256(instance_id.encode()).hexdigest()
        with CLICK_STATE.state_lock():
            CLICK_STATE.write_json(
                CLICK_SHADOW_DASHBOARD._dashboard_path(state_path),
                {
                "version": 1,
                "status": "starting",
                "instance_id": instance_id,
                "runner_token_digest": "",
                "runner_claimed_at": 1,
                "access_token_digest": hashlib.sha256(
                    access_token.encode()
                ).hexdigest(),
                "port": 0,
                "pid": 0,
                "started_at": int(time.time()),
                "stop_requested": False,
                "last_error": "",
                },
            )

        results: list[int] = []
        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }
        environment_ready = threading.Event()

        def serve() -> None:
            with mock.patch.dict(os.environ, environment):
                environment_ready.set()
                results.append(
                    CLICK_SHADOW_DASHBOARD.run_server(
                        [str(state_path), instance_id, access_token]
                    )
                )

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.assertTrue(environment_ready.wait(timeout=2))
        port = 0
        for _ in range(500):
            with CLICK_STATE.state_lock():
                dashboard = json.loads(
                    CLICK_SHADOW_DASHBOARD._dashboard_path(state_path).read_text(
                        encoding="utf-8"
                    )
                )
            if dashboard["status"] == "running":
                port = int(dashboard["port"])
                break
            time.sleep(0.02)
        self.assertGreater(port, 0)
        base = f"http://127.0.0.1:{port}"

        with urllib.request.urlopen(base + "/", timeout=2) as response:
            html = response.read().decode()
            self.assertEqual(response.status, 200)
            self.assertIn("약속한 범위 안에서", html)
            self.assertIn('id="approvalState"', html)
            self.assertIn('id="contractPromises"', html)
            self.assertIn('<details class="telemetry">', html)
            self.assertIn("실제 재사용", html)
            self.assertIn("재사용으로 생략한 테스트 실행시간", html)
            self.assertIn("독립형 HTML 내보내기", html)
            self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))

        with self.assertRaises(urllib.error.HTTPError) as unauthorized:
            urllib.request.urlopen(base + "/api/v1/snapshot", timeout=2)
        self.assertEqual(unauthorized.exception.code, 401)

        request = urllib.request.Request(
            base + "/api/v1/snapshot",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            body = response.read().decode()
            payload = json.loads(body)
            self.assertEqual(response.status, 200)
            self.assertIsNone(payload["summary"]["incremental"]["total_source_count"])
            self.assertIsNone(payload["summary"]["incremental"]["executed_source_count"])
            self.assertEqual(payload["summary"]["shadow"]["candidate_count"], 0)
            self.assertNotIn("actual_saved_ms", body)
            self.assertNotIn(str(self.workspace), body)
            self.assertEqual(payload["task"]["contract_id"], self.active_contract_id())
            self.assertNotIn("runner_token", body)
            self.assertNotIn(access_token, body)
            self.assertNotIn("raw_argv", body)

        wrong_host = urllib.request.Request(
            base + "/", headers={"Host": "attacker.invalid"}
        )
        with self.assertRaises(urllib.error.HTTPError) as misdirected:
            urllib.request.urlopen(wrong_host, timeout=2)
        self.assertEqual(misdirected.exception.code, 421)

        post = urllib.request.Request(base + "/api/v1/snapshot", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as method:
            urllib.request.urlopen(post, timeout=2)
        self.assertEqual(method.exception.code, 405)

        with CLICK_STATE.state_lock():
            dashboard_path = CLICK_SHADOW_DASHBOARD._dashboard_path(state_path)
            dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
            dashboard["status"] = "stopping"
            dashboard["stop_requested"] = True
            CLICK_STATE.write_json(dashboard_path, dashboard)
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [0])

    def test_viewer_survives_next_evidence_task_and_cancel_as_history_only(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "__pycache__/\n", encoding="utf-8"
        )
        self.initialize_git(".gitignore", "verification_fixture.py")
        self.prompt_submit("첫 Evidence 작업", "turn-1")
        start = self.pre_tool(
            "Bash", "click-gate dashboard start", "turn-1", submit_prompt=False
        )
        assert start is not None
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        ).resolve()
        dashboard_path = CLICK_SHADOW_DASHBOARD._dashboard_path(state_path)
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
        instance_id = dashboard["instance_id"]
        dashboard.update(
            status="running", runner_token_digest="", runner_claimed_at=1,
            port=43219, pid=123,
        )
        CLICK_STATE.write_json(dashboard_path, dashboard)

        first = self.verify_gate([self.verification_argv()], "turn-1")
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        previous = json.loads(state_path.read_text(encoding="utf-8"))
        previous_session = previous["evidence_session_id"]
        previous_batch = previous["verification"]["incremental_batch_id"]

        self.prompt_submit("다음 Evidence 작업", "turn-2")
        current = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertNotEqual(current["evidence_session_id"], previous_session)
        self.assertEqual(
            json.loads(dashboard_path.read_text(encoding="utf-8"))["instance_id"],
            instance_id,
        )
        projected_state, active = CLICK_SHADOW_DASHBOARD._snapshot(
            state_path, instance_id
        )
        assert projected_state is not None
        self.assertEqual(active["status"], "running")
        self.assertEqual(
            projected_state["verification"]["incremental_batch_id"],
            previous_batch,
        )

        environment = {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }
        with mock.patch.dict(os.environ, environment):
            CLICK_GATE.click_contract_state.clear_contract_state(
                {**self.base_event, "turn_id": "turn-2"}
            )
        history_only, still_active = CLICK_SHADOW_DASHBOARD._snapshot(
            state_path, instance_id
        )
        assert history_only is not None
        self.assertEqual(history_only["status"], "none")
        self.assertEqual(still_active["status"], "running")
        projection = (
            CLICK_SHADOW_DASHBOARD.click_dashboard_projection.dashboard_projection(
                history_only
            )
        )
        self.assertTrue(
            CLICK_SHADOW_DASHBOARD.click_dashboard_projection.projection_is_valid(
                projection
            )
        )
        self.assertEqual(projection["task"]["runtime_mode"], "unknown")
        self.assertGreaterEqual(projection["history"]["retained_batch_count"], 1)

        other_event = {
            **self.base_event,
            "session_id": "session-2",
            "turn_id": "turn-1",
        }
        with mock.patch.dict(os.environ, environment):
            other_path = CLICK_STATE.contract_path(other_event).resolve()
        foreign_state, foreign_dashboard = CLICK_SHADOW_DASHBOARD._snapshot(
            other_path, instance_id
        )
        self.assertIsNone(foreign_state)
        self.assertEqual(foreign_dashboard, CLICK_SHADOW_DASHBOARD.fresh_state())


if __name__ == "__main__":
    unittest.main()
