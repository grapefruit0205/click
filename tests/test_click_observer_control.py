from __future__ import annotations

import json

from click_gate_test_support import (
    CLICK_LIFECYCLE,
    CLICK_OBSERVER_CONTROL,
    CLICK_SHADOW_DASHBOARD,
    ClickGateTestCase,
    unittest,
)


class ClickObserverControlTests(ClickGateTestCase):
    def test_fresh_state_is_strictly_off_and_non_authoritative(self) -> None:
        state = CLICK_OBSERVER_CONTROL.fresh_state()
        self.assertTrue(CLICK_OBSERVER_CONTROL.state_is_valid(state))
        self.assertEqual(CLICK_OBSERVER_CONTROL.mode({}), "off")
        self.assertEqual(
            CLICK_OBSERVER_CONTROL.projection(
                {CLICK_OBSERVER_CONTROL.CONTROL_FIELD: state}
            ),
            {
                "mode": "off",
                "enabled": False,
                "authoritative": False,
                "reuse_authorized": False,
            },
        )

    def test_control_parser_accepts_only_public_observer_actions(self) -> None:
        for action in ("off", "shadow", "authoritative", "status"):
            self.assertEqual(
                CLICK_LIFECYCLE.control_request(f"click-gate observer {action}"),
                ("observer", action, ""),
            )
        parsed = CLICK_LIFECYCLE.control_request("click-gate observer automatic")
        self.assertEqual(parsed[0], "")
        self.assertIn("observer off|shadow|authoritative|status", parsed[2])

    def test_status_without_runtime_reports_safe_default(self) -> None:
        payload = self.pre_tool("Bash", "click-gate observer status")
        self.assertIsNotNone(payload)
        assert payload is not None
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("observer mode: off", result.stdout)
        self.assertIn("reuse disabled", result.stdout)
        self.assertIn("base reuse implemented", result.stdout)
        self.assertIn("static configuration implemented", result.stdout)

    def test_setting_preserves_revision_evidence_and_dashboard(self) -> None:
        self.approve_contract()
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        ).resolve()
        before = json.loads(state_path.read_text(encoding="utf-8"))

        payload = self.pre_tool(
            "Bash", "click-gate observer shadow", "turn-2", submit_prompt=False
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 0, result.stderr)

        after = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            CLICK_OBSERVER_CONTROL.mode(after["verification"]), "shadow"
        )
        self.assertEqual(
            after["verification"]["mutation_revision"],
            before["verification"]["mutation_revision"],
        )
        self.assertEqual(after["evidence_state"], before["evidence_state"])
        self.assertEqual(
            after[CLICK_SHADOW_DASHBOARD.DASHBOARD_FIELD],
            before[CLICK_SHADOW_DASHBOARD.DASHBOARD_FIELD],
        )

    def test_setting_requires_an_active_runtime(self) -> None:
        self.set_default("off")
        payload = self.pre_tool(
            "Bash", "click-gate observer shadow", submit_prompt=False
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("Guarded or Evidence", output["permissionDecisionReason"])

    def test_setting_is_blocked_while_verification_is_running(self) -> None:
        self.approve_contract()
        self.verify_gate([self.verification_argv()])
        payload = self.pre_tool(
            "Bash", "click-gate observer shadow", "turn-2", submit_prompt=False
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("active verification batch", output["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
