from __future__ import annotations

import json

import ast
from pathlib import Path
import unittest

from hooks import (
    click_contract_state,
    click_gate,
    click_lifecycle,
    click_mode,
    click_prompt,
)


class ClickLifecycleTests(unittest.TestCase):
    def test_lifecycle_has_no_gate_or_host_adapter_dependency(self) -> None:
        source = Path(click_lifecycle.__file__).read_text(encoding="utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imported.add(module)
                imported.update(
                    f"{module}.{alias.name}".strip(".") for alias in node.names
                )
        for forbidden in (
            "click_gate",
            "click_host_coverage",
            "platform_protocol",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(forbidden in name.split(".") for name in imported),
                    imported,
                )
        for required in (
            "click_browser",
            "click_capability",
            "click_claims",
            "click_contract",
            "click_contract_state",
            "click_evidence",
            "click_mode",
            "click_mutation",
            "click_observation",
            "click_prompt",
            "click_runtime_state",
            "click_service",
            "click_state",
            "click_verification",
        ):
            with self.subTest(required=required):
                self.assertIn(required, imported)

    def test_lifecycle_delegates_mode_storage_and_migration_to_the_leaf(self) -> None:
        source = Path(click_lifecycle.__file__).read_text(encoding="utf-8")
        for extracted in (
            "def _write_mode(",
            "def _read_mode(",
            "def _write_default_mode(",
            "def _read_default_mode(",
            "def _consume_migration_notice(",
        ):
            with self.subTest(extracted=extracted):
                self.assertNotIn(extracted, source)

        self.assertIs(click_lifecycle.write_mode, click_mode.write_mode)
        self.assertIs(click_lifecycle.read_mode, click_mode.read_mode)
        self.assertIs(
            click_lifecycle.write_default_mode,
            click_mode.write_default_mode,
        )
        self.assertIs(
            click_lifecycle.read_default_mode,
            click_mode.read_default_mode,
        )
        self.assertIs(
            click_lifecycle.consume_migration_notice,
            click_mode.consume_migration_notice,
        )

    def test_lifecycle_delegates_prompt_state_to_the_leaf(self) -> None:
        source = Path(click_lifecycle.__file__).read_text(encoding="utf-8")
        for extracted in (
            "def _prompt_digest(",
            "def _append_follow_up(",
            "def _prompt_authorization(",
            "def _record_user_prompt(",
            "def _read_user_prompt_state(",
            "def _read_user_prompt_turn(",
            "def _consume_user_authorization(",
            "def _active_prompt_turn_error(",
        ):
            with self.subTest(extracted=extracted):
                self.assertNotIn(extracted, source)

        aliases = {
            "prompt_authorization": click_prompt.prompt_authorization,
            "record_user_prompt": click_prompt.record_user_prompt,
            "read_user_prompt_state": click_prompt.read_user_prompt_state,
            "read_user_prompt_turn": click_prompt.read_user_prompt_turn,
            "consume_user_authorization": click_prompt.consume_user_authorization,
            "active_prompt_turn_error": click_prompt.active_prompt_turn_error,
        }
        for name, expected in aliases.items():
            with self.subTest(name=name):
                self.assertIs(getattr(click_lifecycle, name), expected)
        self.assertIs(
            click_lifecycle.CLICK_AUTHORIZATION_PATTERNS,
            click_prompt.CLICK_AUTHORIZATION_PATTERNS,
        )

    def test_lifecycle_delegates_contract_storage_to_the_leaf(self) -> None:
        source = Path(click_lifecycle.__file__).read_text(encoding="utf-8")
        for extracted in (
            "def _read_contract_state(",
            "def _save_contract_state(",
            "def _clear_contract_state(",
        ):
            with self.subTest(extracted=extracted):
                self.assertNotIn(extracted, source)

        self.assertIs(
            click_lifecycle.read_contract_state,
            click_contract_state.read_contract_state,
        )
        self.assertIs(
            click_lifecycle.save_contract_state,
            click_contract_state.save_contract_state,
        )
        self.assertIs(
            click_lifecycle.clear_contract_state,
            click_contract_state.clear_contract_state,
        )

    def test_gate_does_not_reexport_lifecycle_helpers(self) -> None:
        aliases = (
            "_write_state",
            "_read_state",
            "_write_mode",
            "_read_mode",
            "_write_default_mode",
            "_read_default_mode",
            "_evidence_sources",
            "_write_contract_state",
            "_contract_id_from_state",
            "_read_contract_state",
            "_clear_contract_state",
            "_save_contract_state",
            "_prompt_authorization",
            "_record_user_prompt",
            "_read_user_prompt_state",
            "_read_user_prompt_turn",
            "_consume_user_authorization",
            "_active_prompt_turn_error",
            "_contract_is_completed",
            "_approved_contract_is_active",
            "_session_contract_is_active",
            "_prune_state",
            "_validate_evidence_result",
            "_control_request",
        )
        for name in aliases:
            with self.subTest(name=name):
                self.assertFalse(hasattr(click_gate, name))
        self.assertIs(
            click_gate.CONTRACT_ID_PATTERN,
            click_lifecycle.CONTRACT_ID_PATTERN,
        )
        self.assertEqual(
            click_gate.CONTRACT_STATE_SCHEMA_VERSION,
            click_lifecycle.CONTRACT_STATE_SCHEMA_VERSION,
        )

    def test_control_and_authorization_error_text_stay_in_lifecycle(self) -> None:
        self.assertEqual(
            click_lifecycle.control_request("click-gate arm"),
            ("arm", "", ""),
        )
        self.assertEqual(
            click_lifecycle.control_request("click-gate receipt export"),
            ("receipt-export", "", ""),
        )
        self.assertEqual(
            click_lifecycle.control_request(
                "click-gate receipt verify 'completion receipt.json'"
            ),
            ("receipt-verify", "completion receipt.json", ""),
        )
        self.assertEqual(
            click_lifecycle.control_request(
                r"click-gate receipt verify C:\temp\completion-receipt.json"
            ),
            ("receipt-verify", r"C:\temp\completion-receipt.json", ""),
        )
        # The argv form names one check after its exact command: the same
        # command yields the same evidence id, the class is the command's own
        # minimum, and the JSON envelope stays available for batches.
        action, value, error = click_lifecycle.control_request(
            "click-gate verify -- python3 -m unittest discover -s tests"
        )
        self.assertEqual((action, error), ("verify", ""))
        request = json.loads(value)
        self.assertEqual(request["version"], 2)
        self.assertNotIn("workdir", request)
        [check] = request["checks"]
        self.assertEqual(check["argv"], ["python3", "-m", "unittest", "discover", "-s", "tests"])
        self.assertEqual(check["class"], "broad")
        self.assertRegex(check["evidence_id"], r"^E_[0-9a-f]{12}$")
        self.assertEqual(
            json.loads(click_lifecycle.control_request(
                "click-gate verify -- python3 -m unittest discover -s tests")[1]),
            request,
        )
        [targeted] = json.loads(click_lifecycle.control_request(
            "click-gate verify -- python3 -m pytest tests/test_alpha.py -q")[1])["checks"]
        self.assertEqual(targeted["class"], "targeted")
        self.assertNotEqual(targeted["evidence_id"], check["evidence_id"])
        self.assertEqual(
            click_lifecycle.control_request("click-gate verify --")[0::2],
            ("", "Use `click-gate verify -- <check argv>`; the `--` keeps the checked command explicit."),
        )
        self.assertEqual(
            click_lifecycle.control_request("click-gate verify '{\"version\":2,\"checks\":[]}'"),
            ("verify", '{"version":2,"checks":[]}', ""),
        )
        self.assertEqual(
            click_lifecycle.control_request("click-gate stage 'unterminated"),
            (
                None,
                "",
                "Malformed click-gate command: No closing quotation.",
            ),
        )
        self.assertEqual(
            click_lifecycle.prompt_authorization("@Click bypass\ncontinue"),
            "bypass",
        )
        self.assertEqual(
            click_lifecycle.prompt_authorization("please @Click bypass"),
            "",
        )

    def test_gate_retains_event_and_runner_routing(self) -> None:
        source = Path(click_gate.__file__).read_text(encoding="utf-8")
        for required in (
            "def _handle_pre_tool(",
            "def _handle_post_tool(",
            "def _handle_prompt_submit(",
            "def _handle_session_end(",
            "def _runner_arguments(",
            "def main(",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)
        for extracted in (
            "def _write_state(",
            "def _write_contract_state(",
            "def _contract_is_completed(",
            "def _control_request(",
        ):
            with self.subTest(extracted=extracted):
                self.assertNotIn(extracted, source)


if __name__ == "__main__":
    unittest.main()
