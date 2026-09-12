from __future__ import annotations

from click_gate_test_support import (
    CLICK_CAPABILITY,
    CLICK_GATE,
    CLICK_PROCESS,
    CLICK_SERVICE,
    CLICK_STATE,
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


def validate_service_request(raw: str):
    return CLICK_SERVICE.validate_request(
        raw,
        validate_argv=CLICK_CAPABILITY.validate_argv,
        protocol_version=CLICK_GATE.CAPABILITY_PROTOCOL_VERSION,
    )


class ClickGateServiceTests(ClickGateTestCase):
    def test_managed_service_start_runner_is_one_use_and_preclaimed(self) -> None:
        self.approve_contract()
        request = {
            "version": 1,
            "action": "start",
            "argv": [sys.executable, "-m", "http.server", "0"],
        }
        runner_token = self.id()
        with (
            mock.patch.dict(
                CLICK_GATE.os.environ,
                {"PLUGIN_DATA": str(self.plugin_data)},
            ),
            mock.patch.object(
                CLICK_GATE.secrets,
                "token_urlsafe",
                side_effect=["service-id", runner_token],
            ),
        ):
            _, error = CLICK_GATE._prepare_service(
                self.base_event, json.dumps(request)
            )
        self.assertEqual(error, "")
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        ).resolve()
        normalized, error = validate_service_request(json.dumps(request))
        self.assertEqual(error, "")
        self.assertIsNotNone(normalized)
        assert normalized is not None
        arguments = [
            str(state_path),
            "service-id",
            runner_token,
            str(self.workspace.resolve()),
            CLICK_CAPABILITY.encode_request(normalized),
        ]

        def launch_supervisor(*_args: object, **_kwargs: object) -> mock.Mock:
            with CLICK_STATE.state_lock():
                self.assertTrue(
                    CLICK_SERVICE.record_service_fields(
                        state_path,
                        "service-id",
                        expected_statuses=("launching",),
                        status="running",
                    )
                )
            return mock.Mock()

        with (
            mock.patch.dict(
                CLICK_GATE.os.environ,
                {"PLUGIN_DATA": str(self.plugin_data)},
            ),
            mock.patch.object(
                CLICK_PROCESS,
                "spawn_argv",
                side_effect=launch_supervisor,
            ) as popen,
        ):
            tampered = [*arguments]
            tampered[2] = "wrong-token"
            self.assertEqual(CLICK_GATE._run_service_start(tampered), 2)
            self.assertEqual(CLICK_GATE._run_service_start(arguments), 0)
            self.assertEqual(CLICK_GATE._run_service_start(arguments), 2)
        self.assertEqual(popen.call_count, 1)

    def test_managed_service_rejects_stale_or_future_start_before_launch(self) -> None:
        for label, started_at in (
            (
                "expired",
                int(time.time()) - CLICK_GATE.SERVICE_START_TIMEOUT_SECONDS * 2 - 1,
            ),
            ("future", int(time.time()) + 60),
        ):
            with self.subTest(label=label):
                self.plugin_data = Path(self.temporary.name) / f"service-{label}"
                self.submitted_turns.clear()
                self.approve_contract()
                request = {
                    "version": 1,
                    "action": "start",
                    "argv": [sys.executable, "-m", "http.server", "0"],
                }
                runner_token = f"service-{label}-token"
                with (
                    mock.patch.dict(
                        CLICK_GATE.os.environ,
                        {"PLUGIN_DATA": str(self.plugin_data)},
                    ),
                    mock.patch.object(
                        CLICK_GATE.secrets,
                        "token_urlsafe",
                        side_effect=[f"service-{label}-id", runner_token],
                    ),
                ):
                    CLICK_GATE._prepare_service(self.base_event, json.dumps(request))
                state_path = next(
                    (self.plugin_data / "gate-state").glob("session-contract-*.json")
                ).resolve()
                state = self.read_state(state_path)
                state["service"]["started_at"] = started_at
                state_path.write_text(json.dumps(state), encoding="utf-8")
                normalized, error = validate_service_request(json.dumps(request))
                self.assertEqual(error, "")
                assert normalized is not None
                arguments = [
                    str(state_path),
                    f"service-{label}-id",
                    runner_token,
                    str(self.workspace.resolve()),
                    CLICK_CAPABILITY.encode_request(normalized),
                ]
                with (
                    mock.patch.dict(
                        CLICK_GATE.os.environ,
                        {"PLUGIN_DATA": str(self.plugin_data)},
                    ),
                    mock.patch.object(CLICK_PROCESS, "spawn_argv") as popen,
                ):
                    self.assertEqual(CLICK_GATE._run_service_start(arguments), 2)
                popen.assert_not_called()

    def test_managed_service_supervisor_launch_is_one_use(self) -> None:
        self.approve_contract()
        request = {
            "version": 1,
            "action": "start",
            "argv": [sys.executable, "-m", "http.server", "0"],
        }
        runner_token = self.id()
        with (
            mock.patch.dict(
                CLICK_GATE.os.environ,
                {"PLUGIN_DATA": str(self.plugin_data)},
            ),
            mock.patch.object(
                CLICK_GATE.secrets,
                "token_urlsafe",
                side_effect=["service-id", runner_token],
            ),
        ):
            CLICK_GATE._prepare_service(self.base_event, json.dumps(request))
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        ).resolve()
        normalized, error = validate_service_request(json.dumps(request))
        self.assertEqual(error, "")
        assert normalized is not None
        cwd_raw = str(self.workspace.resolve())
        arguments = [
            str(state_path),
            "service-id",
            runner_token,
            cwd_raw,
            CLICK_CAPABILITY.encode_request(normalized),
        ]
        child = mock.Mock()
        child.pid = 12345
        child.poll.return_value = 0
        with (
            mock.patch.dict(
                CLICK_GATE.os.environ,
                {"PLUGIN_DATA": str(self.plugin_data)},
            ),
            CLICK_STATE.state_lock(),
        ):
            self.assertEqual(
                CLICK_SERVICE.claim_service_runner(
                    state_path,
                    "service-id",
                    normalized,
                    cwd_raw,
                    runner_token,
                    supervisor=False,
                ),
                "",
            )
        with (
            mock.patch.dict(
                CLICK_GATE.os.environ,
                {"PLUGIN_DATA": str(self.plugin_data)},
            ),
            mock.patch.object(
                CLICK_PROCESS, "spawn_argv", return_value=child
            ) as popen,
            mock.patch.object(CLICK_GATE.time, "sleep"),
        ):
            self.assertEqual(CLICK_GATE._run_service_supervisor(arguments), 2)
            self.assertEqual(CLICK_GATE._run_service_supervisor(arguments), 2)
        self.assertEqual(popen.call_count, 1)

    def test_managed_service_rejects_stale_supervisor_before_launch(self) -> None:
        self.approve_contract()
        request = {
            "version": 1,
            "action": "start",
            "argv": [sys.executable, "-m", "http.server", "0"],
        }
        runner_token = self.id()
        with (
            mock.patch.dict(
                CLICK_GATE.os.environ,
                {"PLUGIN_DATA": str(self.plugin_data)},
            ),
            mock.patch.object(
                CLICK_GATE.secrets,
                "token_urlsafe",
                side_effect=["stale-supervisor-id", runner_token],
            ),
        ):
            CLICK_GATE._prepare_service(self.base_event, json.dumps(request))
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        ).resolve()
        normalized, error = validate_service_request(json.dumps(request))
        self.assertEqual(error, "")
        assert normalized is not None
        cwd_raw = str(self.workspace.resolve())
        with (
            mock.patch.dict(
                CLICK_GATE.os.environ,
                {"PLUGIN_DATA": str(self.plugin_data)},
            ),
            CLICK_STATE.state_lock(),
        ):
            self.assertEqual(
                CLICK_SERVICE.claim_service_runner(
                    state_path,
                    "stale-supervisor-id",
                    normalized,
                    cwd_raw,
                    runner_token,
                    supervisor=False,
                ),
                "",
            )
        state = self.read_state(state_path)
        state["service"]["runner_claimed_at"] = (
            int(time.time()) - CLICK_GATE.SERVICE_START_TIMEOUT_SECONDS * 2 - 1
        )
        state_path.write_text(json.dumps(state), encoding="utf-8")
        arguments = [
            str(state_path),
            "stale-supervisor-id",
            runner_token,
            cwd_raw,
            CLICK_CAPABILITY.encode_request(normalized),
        ]
        with (
            mock.patch.dict(
                CLICK_GATE.os.environ,
                {"PLUGIN_DATA": str(self.plugin_data)},
            ),
            mock.patch.object(CLICK_PROCESS, "spawn_argv") as popen,
        ):
            self.assertEqual(CLICK_GATE._run_service_supervisor(arguments), 2)
        popen.assert_not_called()

    def test_managed_service_cancellation_before_claim_prevents_launch(self) -> None:
        self.approve_contract()
        request = {
            "version": 1,
            "action": "start",
            "argv": [sys.executable, "-m", "http.server", "0"],
        }
        runner_token = self.id()
        with (
            mock.patch.dict(
                CLICK_GATE.os.environ,
                {"PLUGIN_DATA": str(self.plugin_data)},
            ),
            mock.patch.object(
                CLICK_GATE.secrets,
                "token_urlsafe",
                side_effect=["service-id", runner_token],
            ),
        ):
            CLICK_GATE._prepare_service(self.base_event, json.dumps(request))
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        ).resolve()
        normalized, error = validate_service_request(json.dumps(request))
        self.assertEqual(error, "")
        assert normalized is not None
        arguments = [
            str(state_path),
            "service-id",
            runner_token,
            str(self.workspace.resolve()),
            CLICK_CAPABILITY.encode_request(normalized),
        ]
        state_path.unlink()
        with (
            mock.patch.dict(
                CLICK_GATE.os.environ,
                {"PLUGIN_DATA": str(self.plugin_data)},
            ),
            mock.patch.object(CLICK_PROCESS, "spawn_argv") as popen,
        ):
            self.assertEqual(CLICK_GATE._run_service_start(arguments), 2)
        popen.assert_not_called()

    @staticmethod
    def read_state(path: Path) -> dict:
        """Read the contract state while its writer may be replacing it.

        A supervisor writes the state through a temporary file and os.replace.
        On Windows the target is briefly unopenable during that replace, so a
        reader outside Click's state lock — which this test deliberately is —
        retries instead of failing.
        """
        deadline = time.monotonic() + 10
        while True:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (PermissionError, json.JSONDecodeError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def test_long_running_local_server_uses_managed_service_lifecycle(self) -> None:
        self.approve_contract()
        argv = [sys.executable, "-m", "http.server", "0", "--bind", "127.0.0.1"]
        mutation = self.mutate_gate(argv)
        self.assertEqual(
            mutation["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn(
            "click-gate service",
            mutation["hookSpecificOutput"]["permissionDecisionReason"],
        )

        start_request = {"version": 1, "action": "start", "argv": argv}
        start = self.pre_tool(
            "Bash",
            f"click-gate service {shlex.quote(json.dumps(start_request))}",
            "turn-2",
        )
        self.assertEqual(start["hookSpecificOutput"]["permissionDecision"], "allow")
        start_result = self.run_rewritten(start)
        self.assertEqual(start_result.returncode, 0, start_result.stderr)

        stop_request = {"version": 1, "action": "stop"}
        stop = self.pre_tool(
            "Bash",
            f"click-gate service {shlex.quote(json.dumps(stop_request))}",
            "turn-2",
        )
        self.assertEqual(stop["hookSpecificOutput"]["permissionDecision"], "allow")
        stop_result = self.run_rewritten(stop)
        self.assertEqual(stop_result.returncode, 0, stop_result.stderr)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = self.read_state(state_path)
        self.assertEqual(state["service"]["status"], "stopped")

        restart = self.pre_tool(
            "Bash",
            f"click-gate service {shlex.quote(json.dumps(start_request))}",
            "turn-2",
        )
        restart_result = self.run_rewritten(restart)
        self.assertEqual(restart_result.returncode, 0, restart_result.stderr)
        end_event = {
            **self.base_event,
            "hook_event_name": "SessionEnd",
        }
        end_result, end_payload = self.run_hook("session-end", end_event)
        self.assertEqual(end_result.returncode, 0, end_result.stderr)
        self.assertIsNone(end_payload)

        def supervisor_claims_finished(snapshot: dict) -> bool:
            claims = [
                entry
                for entry in snapshot["capability_ledger"]["entries"]
                if entry["capability"] == "managed-service-supervisor"
            ]
            return bool(claims) and all(
                entry["result"]["status"] != "running" for entry in claims
            )

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = self.read_state(state_path)
            # The stopped status is persisted before the final claim result.
            # Do not remove the fixture while its supervisor still writes.
            if (
                state["service"]["status"] == "stopped"
                and supervisor_claims_finished(state)
            ):
                break
            time.sleep(0.05)
        self.assertEqual(state["service"]["status"], "stopped")
        self.assertTrue(supervisor_claims_finished(state))

    def test_service_snapshot_retries_windows_sharing_collision(self) -> None:
        self.approve_contract()
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = self.read_state(state_path)
        state["service"] = {
            **CLICK_SERVICE.fresh_state(),
            "status": "stopped",
            "service_id": "service-1",
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        original_read_text = Path.read_text

        def transient_read(path: Path, *args: object, **kwargs: object) -> str:
            if path == state_path and transient_read.attempts == 0:
                transient_read.attempts += 1
                raise PermissionError("sharing violation")
            return original_read_text(path, *args, **kwargs)

        transient_read.attempts = 0
        with (
            mock.patch.dict(
                CLICK_GATE.os.environ,
                {"PLUGIN_DATA": str(self.plugin_data)},
            ),
            mock.patch.object(Path, "read_text", transient_read),
        ):
            snapshot = CLICK_SERVICE.service_snapshot(state_path, "service-1")
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["status"], "stopped")

    def test_service_stop_does_not_treat_unreadable_state_as_stopped(self) -> None:
        with (
            mock.patch.object(
                CLICK_SERVICE,
                "service_snapshot",
                side_effect=[None, {"status": "stopped"}],
            ) as snapshot,
            mock.patch.object(
                CLICK_GATE.click_service,
                "record_service_claim_result",
                return_value=True,
            ),
            mock.patch.object(CLICK_GATE.time, "sleep"),
        ):
            result = CLICK_GATE._run_service_stop(
                [str(self.plugin_data / "state.json"), "service-1"]
            )
        self.assertEqual(result, 0)
        self.assertEqual(snapshot.call_count, 2)
