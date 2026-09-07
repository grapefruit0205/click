from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import unittest
from unittest import mock

from hooks import click_gate, click_inspection, click_inspection_policy


class ClickInspectionTests(unittest.TestCase):
    def test_read_only_environment_drops_loader_and_ripgrep_configuration(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "RIPGREP_CONFIG_PATH": "/tmp/untrusted-ripgrep-config",
                "LD_PRELOAD": "/tmp/untrusted-loader.so",
                "CLICK_PRESERVED": "yes",
            },
        ):
            environment = click_inspection.sanitized_read_only_environment()

        self.assertNotIn("RIPGREP_CONFIG_PATH", environment)
        self.assertNotIn("LD_PRELOAD", environment)
        self.assertEqual(environment["CLICK_PRESERVED"], "yes")

    def test_local_execution_argv_does_not_mutate_validated_request(self) -> None:
        request = ["cat", "README.md"]
        prepared = click_inspection.execution_argv(request)
        self.assertEqual(prepared, request)
        self.assertIsNot(prepared, request)
        prepared[0] = "/usr/bin/cat"
        self.assertEqual(request, ["cat", "README.md"])

    def test_inspection_depends_only_on_policy_capability_and_process_leaves(self) -> None:
        source = Path(click_inspection.__file__).read_text(encoding="utf-8")
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
            "click_browser",
            "click_contract",
            "click_evidence",
            "click_gate",
            "click_mutation",
            "click_observation",
            "click_service",
            "click_state",
            "click_verification_policy",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(forbidden in name.split(".") for name in imported),
                    imported,
                )
        self.assertIn("click_capability", imported)
        self.assertIn("click_inspection_policy", imported)
        self.assertIn("click_process", imported)

    def test_gate_does_not_reexport_inspection_helpers(self) -> None:
        aliases = (
            "_validate_inspection_request",
            "_parse_read_only_git_tokens",
            "_build_read_only_git_argv",
            "_is_read_only_tokens",
            "_direct_command_tokens",
            "_inspection_request_from_bash",
            "_workspace_boundary",
            "_resolve_read_only_executable",
            "_execution_argv",
            "_execute_argv_commands",
            "_execute_inspection_commands",
        )
        for name in aliases:
            with self.subTest(name=name):
                self.assertFalse(hasattr(click_gate, name))
        self.assertIs(
            click_gate.INSPECTION_REQUEST_FIELDS,
            click_inspection.REQUEST_FIELDS,
        )
        self.assertEqual(
            click_gate.MAX_INSPECTION_COMMANDS,
            click_inspection.MAX_COMMANDS,
        )

    def test_inspection_validation_preserves_exact_errors_and_scope(self) -> None:
        cases = (
            ("{", "Inspection request must be valid JSON."),
            ("[]", "Inspection request must be a JSON object."),
            (
                json.dumps({"version": 2, "commands": [["cat", "README.md"]]}),
                "Inspection request `version` must be 1.",
            ),
            (
                json.dumps({"version": 1, "commands": [["cat", "README.md"]], "z": 1}),
                "Inspection request contains unsupported field(s): `z`.",
            ),
            (
                json.dumps({"version": 1, "commands": [["python", "tool.py"]]}),
                "Inspection command 1 is not a supported read-only argv operation.",
            ),
        )
        for raw, expected in cases:
            with self.subTest(expected=expected):
                request, broad, error = click_inspection_policy.validate_request(raw)
                self.assertIsNone(request)
                self.assertFalse(broad)
                self.assertEqual(error, expected)

        request, broad, error = click_inspection_policy.validate_request(
            json.dumps({"version": 1, "commands": [["rg", "--files"]]})
        )
        self.assertEqual(error, "")
        self.assertTrue(broad)
        self.assertEqual(request, {"version": 1, "commands": [["rg", "--files"]]})

        request, broad, error = click_inspection_policy.validate_request(
            json.dumps(
                {
                    "version": 1,
                    "commands": [["cat", "README.md"]],
                    "fresh": True,
                }
            )
        )
        self.assertEqual(error, "")
        self.assertFalse(broad)
        self.assertEqual(
            request,
            {
                "version": 1,
                "commands": [["cat", "README.md"]],
                "fresh": True,
            },
        )
        request, broad, error = click_inspection_policy.validate_request(
            json.dumps(
                {
                    "version": 1,
                    "commands": [["cat", "README.md"]],
                    "fresh": "yes",
                }
            )
        )
        self.assertIsNone(request)
        self.assertFalse(broad)
        self.assertEqual(
            error, "Inspection `fresh` must be a boolean when provided."
        )


if __name__ == "__main__":
    unittest.main()
