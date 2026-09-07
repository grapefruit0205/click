from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

from hooks import (
    click_dependency_cache,
    click_dependency_trace,
    click_gate,
    click_host_coverage,
    click_verification,
)


class ClickVerificationTests(unittest.TestCase):
    def test_tool_working_directory_prefers_explicit_absolute_or_relative_path(
        self,
    ) -> None:
        event_cwd = (Path.cwd() / "outer-workspace").resolve()
        absolute = (Path.cwd() / "nested-repository").resolve()
        batch_absolute = (Path.cwd() / "batch-repository").resolve()

        self.assertEqual(
            click_verification._tool_working_directory(
                {"cwd": str(event_cwd), "tool_input": {"workdir": str(absolute)}},
                {"workdir": str(batch_absolute)},
            ),
            batch_absolute,
        )

        self.assertEqual(
            click_verification._tool_working_directory(
                {"cwd": str(event_cwd), "tool_input": {"workdir": str(absolute)}}
            ),
            absolute,
        )
        self.assertEqual(
            click_verification._tool_working_directory(
                {"cwd": str(event_cwd), "tool_input": {"workdir": "repository"}}
            ),
            (event_cwd / "repository").resolve(),
        )
        self.assertEqual(
            click_verification._tool_working_directory({"cwd": str(event_cwd)}),
            event_cwd,
        )

    def _dependency_receipt(
        self, observation: dict[str, object]
    ) -> dict[str, object]:
        return {
            "provider": click_dependency_cache.CONTRACT_PROVIDER_NAME,
            "manifest_digest": "",
            "entry_digest": "1" * 64,
            "dependency_digest": "2" * 64,
            "resolved_paths": ["src/unit.py"],
            "observation_digest": (
                click_dependency_cache.dependency_observation_digest(observation)
            ),
            "observation": observation,
        }

    def _stale_dependency_source(
        self, receipt: dict[str, object], host_coverage: dict[str, object]
    ) -> dict[str, object]:
        source: dict[str, object] = {
            "status": "stale",
            "verified_revision": 0,
            "verified_at": 1,
            "verified_contract_digest": "3" * 64,
            "verified_check_digest": "4" * 64,
            "verified_root": "/workspace",
            "verified_tree_digest": "5" * 64,
            "verified_environment_digest": "6" * 64,
            "verified_executable_digest": "7" * 64,
            "verified_host_coverage": host_coverage,
        }
        click_verification.store_dependency_receipt(source, receipt)
        return source

    def test_verification_runtime_has_no_gate_host_router_or_service_dependency(self) -> None:
        source = Path(click_verification.__file__).read_text(encoding="utf-8")
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
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "load_siblings"
            ):
                imported.update(
                    argument.value for argument in node.args[1:]
                    if isinstance(argument, ast.Constant)
                    and isinstance(argument.value, str)
                )
        for forbidden in (
            "click_browser",
            "click_contract",
            "click_gate",
            "click_service",
            "platform_protocol",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(forbidden in name.split(".") for name in imported),
                    imported,
                )
        for required in (
            "click_capability",
            "click_change_policy",
            "click_claims",
            "click_contract_state",
            "click_dependency_cache",
            "click_dependency_trace",
            "click_evidence",
            "click_evidence_shards",
            "click_host_coverage",
            "click_incremental",
            "click_inspection",
            "click_mutation",
            "click_observation",
            "click_process",
            "click_runtime_state",
            "click_state",
            "click_verification_policy",
            "click_verification_bindings",
            "click_verification_plan",
            "click_verification_reuse",
        ):
            with self.subTest(required=required):
                self.assertIn(required, imported)

    def test_gate_does_not_reexport_verification_helpers(self) -> None:
        aliases = (
            "_fresh_verification_state",
            "_validate_verification_batch",
            "_verification_groups",
            "_verification_group_digest",
            "_file_content_digest",
            "_verification_environment",
            "_verification_environment_binding",
            "_verification_executable_records",
            "_verification_environment_digest",
            "_verification_receipt_matches",
            "_dependency_receipt_matches",
            "_minimum_verification_class",
            "_git_workspace_snapshot",
            "_new_untracked_is_suspicious",
        )
        for name in aliases:
            with self.subTest(name=name):
                self.assertFalse(hasattr(click_gate, name))
        self.assertEqual(
            click_gate.VERIFICATION_PROTOCOL_VERSION,
            click_verification.PROTOCOL_VERSION,
        )
        self.assertEqual(
            click_gate.VERIFY_RUNNING_TTL_SECONDS,
            click_verification.RUNNING_TTL_SECONDS,
        )

    def test_verification_entrypoints_and_exact_validation_stay_in_domain(self) -> None:
        source = Path(click_verification.__file__).read_text(encoding="utf-8")
        for required in (
            "def _prepare_verification(",
            "def _claim_verification_run(",
            "def _record_verification_result(",
            "def _release_unclaimed_verification_reservation(",
            "def _run_verification(",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)

        batch, units, error = click_verification.validate_batch(
            json.dumps(
                {
                    "version": 2,
                    "checks": [
                        {
                            "argv": ["python3", "-m", "unittest", "tests.test_one"],
                            "class": "targeted",
                        }
                    ],
                }
            ),
            "focused",
        )
        self.assertEqual(error, "")
        self.assertEqual(units, 1)
        self.assertEqual(batch["checks"][0]["class"], "targeted")

        absolute_workdir = str((Path.cwd() / "repository").resolve())
        batch, _, error = click_verification.validate_batch(
            json.dumps(
                {
                    "version": 2,
                    "workdir": absolute_workdir,
                    "checks": [
                        {
                            "argv": ["python3", "-m", "unittest", "tests.test_one"],
                            "class": "targeted",
                        }
                    ],
                }
            ),
            "focused",
        )
        self.assertEqual(error, "")
        self.assertEqual(batch["workdir"], absolute_workdir)

        rejected, _, error = click_verification.validate_batch(
            json.dumps(
                {
                    "version": 2,
                    "workdir": "relative/repository",
                    "checks": [
                        {
                            "argv": ["python3", "-m", "unittest", "tests.test_one"],
                            "class": "targeted",
                        }
                    ],
                }
            ),
            "focused",
        )
        self.assertIsNone(rejected)
        self.assertIn("non-empty absolute path", error)

        rejected, _, error = click_verification.validate_batch(
            json.dumps({"version": 2, "commands": ["pytest"]}),
            "focused",
        )
        self.assertIsNone(rejected)
        self.assertEqual(
            error,
            "Click verification uses `checks` with argv arrays and a submitted "
            "`class`; legacy shell-string `commands` are no longer accepted.",
        )

    def test_cross_revision_reuse_requires_complete_runtime_observation(self) -> None:
        host_coverage = click_host_coverage.receipt("codex")
        observations = {
            "complete": click_dependency_cache.dependency_observation(
                ["src/unit.py"]
            ),
            "trace-failed": click_dependency_cache.dependency_observation(
                ["src/unit.py"], status="failed", process_tree_complete=False
            ),
            "external": click_dependency_cache.dependency_observation(
                ["src/unit.py"], external_access=True
            ),
            "unfollowed-child": click_dependency_cache.dependency_observation(
                ["src/unit.py"],
                child_processes=1,
                process_tree_complete=False,
            ),
        }

        for label, observation in observations.items():
            with self.subTest(label=label):
                receipt = self._dependency_receipt(observation)
                source = self._stale_dependency_source(receipt, host_coverage)
                matched = click_verification.dependency_receipt_matches(
                    source,
                    receipt,
                    contract_digest="3" * 64,
                    revision=1,
                    group_digest="4" * 64,
                    git_root="/workspace",
                    environment_digest="6" * 64,
                    executable_digest="7" * 64,
                    host_coverage=host_coverage,
                )
                self.assertEqual(matched, label == "complete")

    def test_legacy_receipt_without_observation_fails_closed(self) -> None:
        receipt = self._dependency_receipt(
            click_dependency_cache.dependency_observation(["src/unit.py"])
        )
        receipt.pop("observation")
        receipt.pop("observation_digest")

        self.assertFalse(click_verification.dependency_receipt_is_valid(receipt))


if __name__ == "__main__":
    unittest.main()
