from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import unittest

from hooks import (
    click_dependency_cache,
    click_evidence,
    click_gate,
    click_host_coverage,
    click_lifecycle,
)


class ClickEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = {
            "verification": {
                "evidence": [
                    {"id": "E1", "kind": "argv", "description": "tests pass"},
                    {
                        "id": "E-browser",
                        "kind": "browser",
                        "description": "render is usable",
                    },
                ]
            }
        }

    def test_evidence_module_has_no_gate_state_or_process_dependency(self) -> None:
        source = Path(click_evidence.__file__).read_text(encoding="utf-8")
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

        for forbidden in ("click_gate", "click_state", "click_process"):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(forbidden in name.split(".") for name in imported),
                    imported,
                )

    def test_gate_does_not_reexport_evidence_primitives(self) -> None:
        for name in (
            "_evidence_key",
            "_evidence_registry_digest",
            "_fresh_evidence_state",
            "_evidence_is_current",
            "_evidence_keys_for_kind",
            "_browser_evidence_source_id",
            "_browser_evidence_required",
            "_fresh_external_evidence_state",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(click_gate, name))

    def test_fresh_state_hashes_ids_and_omits_descriptions(self) -> None:
        ledger = click_evidence.fresh_state(self.contract)
        serialized = json.dumps(ledger, sort_keys=True)

        self.assertEqual(ledger["version"], 1)
        self.assertEqual(ledger["source_count"], 2)
        self.assertNotIn("E-browser", serialized)
        self.assertNotIn("tests pass", serialized)
        self.assertNotIn("render is usable", serialized)
        self.assertEqual(
            set(ledger["sources"]),
            {
                click_evidence.evidence_key("E1"),
                click_evidence.evidence_key("E-browser"),
            },
        )
        self.assertEqual(
            ledger["registry_digest"],
            click_evidence.registry_digest(ledger["sources"]),
        )
        argv_source = ledger["sources"][click_evidence.evidence_key("E1")]
        self.assertEqual(argv_source["reserved_units"], 0)
        self.assertEqual(argv_source["reserved_check_digest"], "")
        self.assertEqual(argv_source["verified_tree_digest"], "")
        self.assertEqual(argv_source["verified_host_coverage"], {})

    def test_browser_registry_and_external_state_remain_content_free(self) -> None:
        self.assertEqual(
            click_evidence.browser_source_id(self.contract), "E-browser"
        )
        self.assertTrue(click_evidence.browser_required(self.contract))
        external = click_evidence.fresh_external_state(self.contract)

        self.assertTrue(external["browser_required"])
        self.assertEqual(external["browser_status"], "ready")
        self.assertEqual(
            external["browser_source_key"],
            click_evidence.evidence_key("E-browser"),
        )
        self.assertNotIn("E-browser", json.dumps(external, sort_keys=True))

    def test_evidence_runtime_registers_ids_without_dependency_authority(self) -> None:
        state = {
            "status": "evidence",
            "evidence_state": click_evidence.fresh_state(
                {"verification": {"evidence": []}}
            ),
        }

        sources, error = click_evidence.register_runtime_sources(
            state, ["E_TESTS"], kind="argv"
        )

        self.assertEqual(error, "")
        assert sources is not None
        source = sources[click_evidence.evidence_key("E_TESTS")]
        self.assertEqual(source["kind"], "argv")
        self.assertEqual(source["dependency_patterns"], [])
        self.assertEqual(source["dependency_declaration_digest"], "")
        self.assertEqual(state["evidence_state"]["source_count"], 1)

    def test_dependency_declaration_is_prose_free_and_integrity_checked(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["verification"]["evidence"][0]["dependencies"] = [
            "tests/",
            "src/**/*.py",
        ]
        ledger = click_evidence.fresh_state(contract)
        source = ledger["sources"][click_evidence.evidence_key("E1")]

        self.assertEqual(
            source["dependency_patterns"], ["src/**/*.py", "tests/"]
        )
        self.assertRegex(source["dependency_declaration_digest"], r"^[0-9a-f]{64}$")
        self.assertNotIn("tests pass", json.dumps(ledger, sort_keys=True))

        state = {"state_schema_version": 2, "evidence_state": ledger}
        source["dependency_patterns"] = ["src/other.py"]
        self.assertEqual(
            click_evidence.sources_from_state(
                state, expected_contract_schema_version=2
            ),
            {},
        )

    def test_observed_dependency_receipt_is_integrity_checked(self) -> None:
        ledger = click_evidence.fresh_state(self.contract)
        source = ledger["sources"][click_evidence.evidence_key("E1")]
        observation = click_dependency_cache.dependency_observation(
            ["src/unit.py"]
        )
        source.update(
            {
                "verified_dependency_provider": (
                    click_dependency_cache.CONTRACT_PROVIDER_NAME
                ),
                "verified_dependency_entry_digest": "1" * 64,
                "verified_dependency_digest": "2" * 64,
                "verified_dependency_paths": ["src/unit.py"],
                "verified_dependency_observation_digest": (
                    click_dependency_cache.dependency_observation_digest(
                        observation
                    )
                ),
                "verified_dependency_observation": observation,
            }
        )
        state = {"state_schema_version": 2, "evidence_state": ledger}
        self.assertIsNotNone(
            click_evidence.sources_from_state(
                state, expected_contract_schema_version=2
            )
        )

        source["verified_dependency_observation"]["paths"] = ["src/other.py"]
        self.assertEqual(
            click_evidence.sources_from_state(
                state, expected_contract_schema_version=2
            ),
            {},
        )

    def test_sources_from_state_preserves_legacy_and_malformed_distinction(self) -> None:
        self.assertIsNone(
            click_evidence.sources_from_state(
                {}, expected_contract_schema_version=2
            )
        )
        self.assertEqual(
            click_evidence.sources_from_state(
                {"state_schema_version": 2},
                expected_contract_schema_version=2,
            ),
            {},
        )
        self.assertEqual(
            click_evidence.sources_from_state(
                {
                    "state_schema_version": 3,
                    "evidence_state": click_evidence.fresh_state(self.contract),
                },
                expected_contract_schema_version=2,
            ),
            {},
        )

    def test_sources_from_state_rejects_registry_tampering(self) -> None:
        valid = {
            "state_schema_version": 2,
            "evidence_state": click_evidence.fresh_state(self.contract),
        }
        sources = click_evidence.sources_from_state(
            valid, expected_contract_schema_version=2
        )
        self.assertIsInstance(sources, dict)
        self.assertEqual(len(sources or {}), 2)

        wrong_count = copy.deepcopy(valid)
        wrong_count["evidence_state"]["source_count"] = 1
        wrong_digest = copy.deepcopy(valid)
        wrong_digest["evidence_state"]["registry_digest"] = "0" * 64
        wrong_kind = copy.deepcopy(valid)
        first = next(iter(wrong_kind["evidence_state"]["sources"].values()))
        first["kind"] = "unknown"

        wrong_reservation = copy.deepcopy(valid)
        reservation_source = next(
            iter(wrong_reservation["evidence_state"]["sources"].values())
        )
        reservation_source["reserved_units"] = "cheap"

        units_without_digest = copy.deepcopy(valid)
        reservation_source = next(
            iter(units_without_digest["evidence_state"]["sources"].values())
        )
        reservation_source["reserved_units"] = 1

        digest_without_units = copy.deepcopy(valid)
        reservation_source = next(
            iter(digest_without_units["evidence_state"]["sources"].values())
        )
        reservation_source["reserved_check_digest"] = "a" * 64

        for state in (
            wrong_count,
            wrong_digest,
            wrong_kind,
            wrong_reservation,
        ):
            with self.subTest(state=state):
                self.assertEqual(
                    click_evidence.sources_from_state(
                        state, expected_contract_schema_version=2
                    ),
                    {},
                )

        # Legacy unit values no longer establish or invalidate exact receipt
        # identity; only the check digest does.
        for state in (units_without_digest, digest_without_units):
            with self.subTest(compatibility_state=state):
                self.assertIsInstance(
                    click_evidence.sources_from_state(
                        state, expected_contract_schema_version=2
                    ),
                    dict,
                )

    def test_host_coverage_receipt_shape_is_integrity_checked(self) -> None:
        valid = {
            "state_schema_version": 2,
            "evidence_state": click_evidence.fresh_state(self.contract),
        }
        source = next(iter(valid["evidence_state"]["sources"].values()))
        source["verified_host_coverage"] = click_host_coverage.receipt("codex")
        self.assertIsInstance(
            click_evidence.sources_from_state(
                valid, expected_contract_schema_version=2
            ),
            dict,
        )

        malformed = copy.deepcopy(valid)
        malformed_source = next(
            iter(malformed["evidence_state"]["sources"].values())
        )
        malformed_source["verified_host_coverage"].pop("assurance")
        self.assertEqual(
            click_evidence.sources_from_state(
                malformed, expected_contract_schema_version=2
            ),
            {},
        )

        legacy = copy.deepcopy(valid)
        legacy_source = next(iter(legacy["evidence_state"]["sources"].values()))
        legacy_source.pop("verified_host_coverage")
        self.assertIsInstance(
            click_evidence.sources_from_state(
                legacy, expected_contract_schema_version=2
            ),
            dict,
        )

    def test_current_revision_and_kind_queries_are_pure(self) -> None:
        ledger = click_evidence.fresh_state(self.contract)
        sources = ledger["sources"]
        argv_key = click_evidence.evidence_key("E1")
        browser_key = click_evidence.evidence_key("E-browser")
        sources[argv_key]["status"] = "passed"
        sources[argv_key]["verified_revision"] = 4

        self.assertTrue(click_evidence.is_current(sources[argv_key], 4))
        self.assertFalse(click_evidence.is_current(sources[argv_key], 5))
        self.assertEqual(click_evidence.keys_for_kind(sources, "argv"), {argv_key})
        self.assertEqual(
            click_evidence.keys_for_kind(sources, "browser"), {browser_key}
        )

    def test_successful_revisions_are_strict_nonnegative_integers(self) -> None:
        for revision in (0, 1, 19):
            with self.subTest(revision=revision):
                source = {"status": "passed", "verified_revision": revision}
                self.assertTrue(click_evidence.revision_is_valid(revision))
                self.assertTrue(click_evidence.is_current(source, revision))
                self.assertFalse(click_evidence.is_current(source, revision + 1))
        for value in (-1, -2, True, False, 0.0, 1.0, 1.9, "0", "1", None, [], {}, float("nan"), float("inf")):
            with self.subTest(value=value):
                self.assertFalse(click_evidence.revision_is_valid(value))
                self.assertFalse(click_evidence.is_current({"status": "passed", "verified_revision": value}, 0))
                self.assertFalse(click_evidence.is_current({"status": "passed", "verified_revision": 0}, value))
        self.assertFalse(click_evidence.is_current({"status": "passed"}, 0))
        self.assertFalse(click_evidence.is_current({"status": "ready", "verified_revision": -1}, -1))

    def test_ledger_revision_validation_preserves_unverified_sentinel(self) -> None:
        valid = {"evidence_state": click_evidence.fresh_state(self.contract)}
        key = click_evidence.evidence_key("E1")
        self.assertTrue(click_evidence.sources_from_state(valid, expected_contract_schema_version=2))
        for status, revision, accepted in (
            ("ready", -1, True), ("running", -1, True), ("failed", -1, True),
            ("passed", 0, True), ("passed", 1, True),
            ("passed", -1, False), ("ready", -2, False),
            *[("passed", value, False) for value in (True, False, 0.0, 1.0, 1.9, "0", "1", None, [], {}, float("nan"), float("inf"))],
        ):
            with self.subTest(status=status, revision=revision):
                state = copy.deepcopy(valid)
                state["evidence_state"]["sources"][key].update(status=status, verified_revision=revision)
                self.assertEqual(bool(click_evidence.sources_from_state(state, expected_contract_schema_version=2)), accepted)
        missing = copy.deepcopy(valid)
        missing["evidence_state"]["sources"][key].pop("verified_revision")
        self.assertEqual(click_evidence.sources_from_state(missing, expected_contract_schema_version=2), {})

    def test_dynamic_registration_rejects_malformed_existing_ledger_without_mutation(self) -> None:
        valid = {"status": "evidence", "evidence_state": click_evidence.fresh_state(self.contract)}
        key = click_evidence.evidence_key("E1")
        for field, value in (
            *[("verified_revision", value) for value in (-1, True, False, 0.0, 1.9, "0", None, [], {}, float("nan"), float("inf"))],
            ("status", []), ("status", {}), ("attempts", "0"),
        ):
            with self.subTest(field=field, value=value):
                state = copy.deepcopy(valid)
                source = state["evidence_state"]["sources"][key]
                source.update(status="passed", verified_revision=0)
                source[field] = value
                before = json.dumps(state, sort_keys=True)
                sources, error = click_evidence.register_runtime_sources(state, ["E1", "E_NEW"])
                self.assertIsNone(sources)
                self.assertIn("malformed", error)
                self.assertEqual(json.dumps(state, sort_keys=True), before)
        for field, value in (("source_count", 99), ("registry_digest", "0" * 64)):
            state = copy.deepcopy(valid)
            state["evidence_state"][field] = value
            before = copy.deepcopy(state)
            sources, error = click_evidence.register_runtime_sources(state, ["E_NEW"])
            self.assertIsNone(sources)
            self.assertIn("malformed", error)
            self.assertEqual(state, before)

    def test_successor_initializer_preserves_current_declaration_and_normal_defaults(self) -> None:
        key = click_evidence.evidence_key("E1")
        current_contract = copy.deepcopy(self.contract)
        current_contract["verification"]["evidence"][0]["dependencies"] = ["current/"]
        current = click_evidence.fresh_state(current_contract)["sources"][key]
        previous = click_evidence.fresh_state(self.contract)["sources"][key]
        previous.update(status="passed", verified_revision=7, verified_at=123,
                        attempts=8, reserved_units=99, reserved_check_digest="a" * 64,
                        locked_check_digest="b" * 64, verified_contract_digest="c" * 64)
        previous["future_extension"] = object()
        previous["verified_future_authority"] = True
        # This helper constructs candidates, not authority. The public
        # requalification boundary separately checks matching kind/declarations.
        current["kind"] = "hosted"
        candidate = click_evidence.fresh_successor_source(current, previous)
        self.assertEqual(candidate["kind"], "hosted")
        self.assertEqual(candidate["dependency_patterns"], ["current/"])
        self.assertEqual(candidate["dependency_declaration_digest"], current["dependency_declaration_digest"])
        self.assertEqual(candidate["status"], "ready")
        self.assertEqual(candidate["verified_revision"], -1)
        self.assertEqual(candidate["verified_at"], 123)
        for field in ("attempts", "unchanged_failure_retries", "reserved_units", "successor_reuse_count"):
            self.assertEqual(candidate[field], 0)
        for field in ("reserved_check_digest", "locked_check_digest", "verified_contract_digest"):
            self.assertEqual(candidate[field], "")
        self.assertNotIn("future_extension", candidate)
        self.assertNotIn("verified_future_authority", candidate)
        self.assertEqual(previous["attempts"], 8)
        self.assertEqual(current["status"], "ready")

    def test_successor_initializer_copies_only_explicit_nested_fact_and_measurement_fields(self) -> None:
        key = click_evidence.evidence_key("E1")
        current = click_evidence.fresh_state(self.contract)["sources"][key]
        previous = copy.deepcopy(current)
        previous["verified_dependency_observation"] = {"paths": ["old.py"], "nested": {"value": 1}}
        previous["last_success_duration_baseline"] = {"origin_task": {"mode": "evidence", "id": "original"}}
        previous["last_success_duration_ms"] = 42
        before = copy.deepcopy(previous)
        candidate = click_evidence.fresh_successor_source(current, previous)
        self.assertEqual(candidate["last_success_duration_ms"], 42)
        self.assertEqual(candidate["last_success_duration_baseline"], before["last_success_duration_baseline"])
        candidate["verified_dependency_observation"]["nested"]["value"] = 2
        candidate["last_success_duration_baseline"]["origin_task"]["id"] = "changed"
        self.assertEqual(previous, before)

    def test_lifecycle_sources_helper_passes_the_contract_schema_version(self) -> None:
        state = {
            "state_schema_version": click_lifecycle.CONTRACT_STATE_SCHEMA_VERSION,
            "evidence_state": click_evidence.fresh_state(self.contract),
        }
        self.assertEqual(
            click_lifecycle.evidence_sources(state),
            state["evidence_state"]["sources"],
        )


if __name__ == "__main__":
    unittest.main()
