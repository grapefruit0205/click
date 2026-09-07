from __future__ import annotations

import ast
from pathlib import Path
import time
import unittest

from hooks import click_gate, click_observation


class ClickObservationTests(unittest.TestCase):
    def test_observation_depends_only_on_approved_runtime_leaves(self) -> None:
        source = Path(click_observation.__file__).read_text(encoding="utf-8")
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
            "click_host_coverage",
            "click_mutation",
            "click_service",
            "click_verification_meter",
            "click_verification_policy",
            "platform_protocol",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(forbidden in name.split(".") for name in imported),
                    imported,
                )
        for required in (
            "click_capability",
            "click_claims",
            "click_contract_state",
            "click_inspection",
            "click_observation_cache",
            "click_process",
            "click_state",
        ):
            with self.subTest(required=required):
                self.assertIn(required, imported)

    def test_gate_does_not_reexport_observation_helpers(self) -> None:
        aliases = (
            "_fresh_observation_state",
            "_unclaimed_reservation_is_fresh",
            "_observation_is_running",
            "_write_review_state",
            "_read_review_state",
            "_save_review_state",
            "_clear_review_state",
            "_managed_observation_path",
            "_record_observation_result",
        )
        for name in aliases:
            with self.subTest(name=name):
                self.assertFalse(hasattr(click_gate, name))
        self.assertEqual(
            click_gate.OBSERVATION_RESERVATION_TTL_SECONDS,
            click_observation.RESERVATION_TTL_SECONDS,
        )
        self.assertEqual(
            click_gate.MAX_OBSERVATION_OUTPUT_BYTES,
            click_observation.MAX_OUTPUT_BYTES,
        )

    def test_observation_running_semantics_preserve_claim_and_expiry(self) -> None:
        now = int(time.time())
        self.assertTrue(
            click_observation.is_running(
                {"status": "running", "runner_claimed_at": now, "started_at": 0}
            )
        )
        self.assertTrue(
            click_observation.is_running(
                {"status": "running", "runner_claimed_at": 0, "started_at": now}
            )
        )
        self.assertFalse(
            click_observation.is_running(
                {
                    "status": "running",
                    "runner_claimed_at": 0,
                    "started_at": now - click_observation.RESERVATION_TTL_SECONDS - 1,
                }
            )
        )
        self.assertFalse(click_observation.is_running({"status": "success"}))


if __name__ == "__main__":
    unittest.main()
