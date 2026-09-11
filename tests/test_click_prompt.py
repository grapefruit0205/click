from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hooks import click_gate, click_lifecycle, click_prompt, click_state


class ClickPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.plugin_data = Path(self.temporary.name) / "plugin-data"
        self.environment = mock.patch.dict(
            os.environ,
            {"PLUGIN_DATA": str(self.plugin_data)},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.event = {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(Path(self.temporary.name) / "workspace"),
        }

    def test_prompt_leaf_has_only_the_state_boundary_below_it(self) -> None:
        source = Path(click_prompt.__file__).read_text(encoding="utf-8")
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
            "click_lifecycle",
            "click_host_coverage",
            "click_host_router",
            "click_contract",
            "click_evidence",
            "click_mutation",
            "click_observation",
            "click_runtime_state",
            "click_service",
            "click_verification",
            "platform_protocol",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(forbidden in name.split(".") for name in imported),
                    imported,
                )
        self.assertIn(
            'click_import_bootstrap.load_siblings(__package__, "click_state")',
            source,
        )

    def test_first_line_authorization_forms_remain_exact(self) -> None:
        accepted = {
            "@Click bypass": "bypass",
            "@click BYPASS": "bypass",
            "  [@Click](plugin://click@click) bypass  \nContinue": "bypass",
            "[@click](plugin://click@click) CANCEL\nContinue": "cancel",
        }
        for prompt, expected in accepted.items():
            with self.subTest(prompt=prompt):
                self.assertEqual(click_prompt.prompt_authorization(prompt), expected)

        for prompt in (
            "@Click bypass extra",
            "[@Click](plugin://other@click) bypass",
            "Please use @Click bypass",
            "Continue\n@Click cancel",
            "`@Click bypass`",
            None,
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(click_prompt.prompt_authorization(prompt), "")

    def test_prompt_state_and_authorization_consumption_are_unchanged(self) -> None:
        prompt = "@Click bypass\nContinue"
        event = {**self.event, "prompt": prompt}
        self.assertEqual(click_prompt.record_user_prompt(event), "bypass")
        state = click_prompt.read_user_prompt_state(event)
        self.assertEqual(state["turn_id"], "turn-1")
        self.assertEqual(state["authorization"], "bypass")
        self.assertEqual(
            state["prompt_digest"],
            hashlib.sha256(prompt.encode()).hexdigest(),
        )
        self.assertEqual(click_prompt.read_user_prompt_turn(event), "turn-1")

        self.assertEqual(click_prompt.consume_user_authorization(event, "bypass"), "")
        self.assertEqual(
            click_prompt.read_user_prompt_state(event)["authorization"],
            "",
        )
        self.assertEqual(
            click_prompt.consume_user_authorization(event, "bypass"),
            "Click bypass requires a recognized first-line Click directive "
            "(`@Click bypass`) in this user turn.",
        )

    def test_active_turn_errors_and_follow_up_lineage_remain_exact(self) -> None:
        self.assertEqual(
            click_prompt.active_prompt_turn_error({}),
            "Click cannot prove approval because this tool call has no host turn_id.",
        )
        self.assertEqual(
            click_prompt.active_prompt_turn_error(self.event),
            "Click can stage or approve a contract only in a turn that began with a "
            "UserPromptSubmit event. Ask the user to respond, then retry in that turn.",
        )

        event = {**self.event, "turn_id": "turn-3", "prompt": "표시 문자열만 빼줘"}
        click_prompt.record_user_prompt(event)
        state = {
            "intent_turn_id": "turn-1",
            "staged_turn_id": "turn-1",
            "approved_turn_id": "turn-2",
            "follow_up_turns": [],
        }
        self.assertTrue(click_prompt.append_follow_up(event, state))
        self.assertEqual(state["follow_up_turns"][0]["turn_id"], "turn-3")
        self.assertRegex(state["follow_up_turns"][0]["digest"], r"^[0-9a-f]{64}$")
        self.assertFalse(click_prompt.append_follow_up(event, state))

    def test_gate_does_not_reexport_prompt_helpers(self) -> None:
        aliases = (
            "_prompt_authorization",
            "_record_user_prompt",
            "_read_user_prompt_state",
            "_read_user_prompt_turn",
            "_consume_user_authorization",
            "_active_prompt_turn_error",
        )
        for gate_name in aliases:
            with self.subTest(gate_name=gate_name):
                self.assertFalse(hasattr(click_gate, gate_name))
        self.assertIs(
            click_lifecycle.CLICK_AUTHORIZATION_PATTERNS,
            click_prompt.CLICK_AUTHORIZATION_PATTERNS,
        )

        prompt_path = click_state.prompt_path(self.event)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text("not-json", encoding="utf-8")
        self.assertEqual(click_prompt.read_user_prompt_state(self.event), {})


if __name__ == "__main__":
    unittest.main()
