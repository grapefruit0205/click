from __future__ import annotations

import copy
from pathlib import Path
import unittest

from hooks import click_receipt


def _digest(character: str) -> str:
    return character * 64


def _argv_source(source_key: str = _digest("1")) -> dict[str, object]:
    return {
        "source_key": source_key,
        "kind": "argv",
        "verified_revision": 2,
        "check_digest": _digest("3"),
        "environment_digest": _digest("4"),
        "executable_digest": _digest("5"),
        "result": {"status": "passed", "exit_code": 0, "completed_at": 12},
        "lineage": {
            "mode": "executed",
            "from_revision": 2,
            "dependency_digest": "",
        },
    }


def _browser_source() -> dict[str, object]:
    return {
        "source_key": _digest("2"),
        "kind": "browser",
        "verified_revision": 2,
        "check_digest": "",
        "environment_digest": "",
        "executable_digest": "",
        "result": {"status": "passed", "exit_code": 0, "completed_at": 11},
        "lineage": {
            "mode": "browser-observed",
            "from_revision": 2,
            "dependency_digest": "",
        },
    }


def _valid_receipt() -> dict[str, object]:
    return {
        "version": 1,
        "contract": {
            "id": "ctr_0123456789abcdef0123456789abcdef",
            "digest": _digest("a"),
            "staged_turn_id": "turn-stage",
            "approved_turn_id": "turn-approve",
        },
        "execution": {
            "mutation_revision": 2,
            "workspace": {
                "assurance": "git-protected-tree",
                "root_digest": _digest("b"),
                "tree_digest": _digest("c"),
            },
        },
        "capabilities": [
            {
                "sequence": 1,
                "capability": "mutation",
                "claim_mode": "host-tool-use",
                "request_digest": _digest("6"),
                "claim_digest": _digest("7"),
                "binding_digest": _digest("8"),
                "mutation_revision": 1,
                "claimed_at": 8,
                "completed_at": 9,
                "result": {"status": "observed", "exit_code": None},
            },
            {
                "sequence": 2,
                "capability": "verification",
                "claim_mode": "one-use-runner",
                "request_digest": _digest("9"),
                "claim_digest": _digest("0"),
                "binding_digest": "",
                "mutation_revision": 2,
                "claimed_at": 10,
                "completed_at": 12,
                "result": {"status": "passed", "exit_code": 0},
            },
        ],
        "evidence": [_browser_source(), _argv_source()],
        "coverage": {
            "host_assurance": "known-surfaces-only",
            "host_coverage_digest": _digest("d"),
            "excluded": [
                "unmatched-host-paths",
                "git-ignored-content",
                "external-system-state",
                "external-dependencies",
            ],
        },
    }


def _valid_sharded_receipt() -> dict[str, object]:
    receipt = _valid_receipt()
    receipt["version"] = 3
    receipt["authority"] = {
        "mode": "guarded",
        "approval_bound": True,
        "execution_authority": "click-contract",
        "intent_digest": _digest("a"),
        "intent_turn_id": "turn-stage",
        "follow_up_turns": [],
        "history_complete": True,
    }
    shared = {
        "provider": "repository-evidence-shards-v1",
        "parent_source_key": _digest("f"),
        "parent_check_digest": _digest("6"),
        "shard_count": 2,
        "plan_digest": _digest("7"),
        "entry_digest": _digest("8"),
        "inventory_digest": _digest("9"),
    }
    alpha = _argv_source(_digest("1"))
    alpha["shard"] = {**shared, "shard_id": "alpha"}
    beta = _argv_source(_digest("2"))
    beta["shard"] = {**shared, "shard_id": "beta"}
    receipt["evidence"] = [beta, alpha]
    return receipt


class ClickReceiptTests(unittest.TestCase):
    def test_antigravity_distribution_contains_the_exact_receipt_module(self) -> None:
        root = Path(__file__).resolve().parents[1]

        self.assertEqual(
            (root / "hooks" / "click_receipt.py").read_bytes(),
            (root / "dist" / "antigravity" / "hooks" / "click_receipt.py").read_bytes(),
        )

    def test_semantically_identical_input_has_one_canonical_digest(self) -> None:
        first = _valid_receipt()
        second = {key: first[key] for key in reversed(list(first))}
        second["evidence"] = list(reversed(copy.deepcopy(first["evidence"])))
        second["coverage"] = {
            key: first["coverage"][key]  # type: ignore[index]
            for key in reversed(list(first["coverage"]))  # type: ignore[arg-type]
        }

        first_bytes, first_error = click_receipt.canonical_bytes(first)
        second_bytes, second_error = click_receipt.canonical_bytes(second)
        first_digest, first_digest_error = click_receipt.receipt_digest(first)
        second_digest, second_digest_error = click_receipt.receipt_digest(second)

        self.assertEqual(first_error, "")
        self.assertEqual(second_error, "")
        self.assertEqual(first_digest_error, "")
        self.assertEqual(second_digest_error, "")
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(first_digest, second_digest)
        self.assertRegex(first_digest, r"^[0-9a-f]{64}$")

    def test_normalization_orders_evidence_and_coverage_exclusions(self) -> None:
        normalized, error = click_receipt.validate_receipt(_valid_receipt())

        self.assertEqual(error, "")
        assert normalized is not None
        self.assertEqual(
            [source["source_key"] for source in normalized["evidence"]],
            [_digest("1"), _digest("2")],
        )
        self.assertEqual(
            normalized["coverage"]["excluded"],
            sorted(click_receipt.BASE_COVERAGE_EXCLUSIONS),
        )

    def test_v3_binds_one_complete_shard_set_and_v2_remains_strict(self) -> None:
        receipt = _valid_sharded_receipt()
        normalized, error = click_receipt.validate_receipt(receipt)

        self.assertEqual(error, "")
        assert normalized is not None
        self.assertEqual(normalized["version"], 3)
        self.assertEqual(
            [source["shard"]["shard_id"] for source in normalized["evidence"]],
            ["alpha", "beta"],
        )

        incomplete = copy.deepcopy(receipt)
        incomplete["evidence"].pop()  # type: ignore[union-attr]
        rejected, error = click_receipt.validate_receipt(incomplete)
        self.assertIsNone(rejected)
        self.assertIn("incomplete", error)

        mismatched = copy.deepcopy(receipt)
        mismatched["evidence"][0]["shard"]["plan_digest"] = _digest("0")  # type: ignore[index]
        rejected, error = click_receipt.validate_receipt(mismatched)
        self.assertIsNone(rejected)
        self.assertIn("incomplete", error)

        legacy_shape = _valid_receipt()
        legacy_shape["evidence"][1]["shard"] = None  # type: ignore[index]
        rejected, error = click_receipt.validate_receipt(legacy_shape)
        self.assertIsNone(rejected)
        self.assertIn("unsupported field", error)

    def test_v4_binds_successor_origin_and_rejects_downgraded_lineage(self) -> None:
        receipt = _valid_sharded_receipt()
        receipt["version"] = 4
        source = receipt["evidence"][0]  # type: ignore[index]
        source["lineage"] = {
            "mode": "successor-reused",
            "from_revision": 9,
            "dependency_digest": _digest("e"),
            "origin_batch_id": "a" * 32,
            "origin_evidence_session_id": "evs_" + "b" * 32,
            "requalification_mode": "safe-change",
        }

        normalized, error = click_receipt.validate_receipt(receipt)

        self.assertEqual(error, "")
        assert normalized is not None
        lineage = next(
            item["lineage"]
            for item in normalized["evidence"]
            if item["lineage"]["mode"] == "successor-reused"
        )
        self.assertEqual(lineage["mode"], "successor-reused")
        self.assertEqual(lineage["from_revision"], 9)

        missing_origin = copy.deepcopy(receipt)
        del missing_origin["evidence"][0]["lineage"]["origin_batch_id"]  # type: ignore[index]
        rejected, error = click_receipt.validate_receipt(missing_origin)
        self.assertIsNone(rejected)
        self.assertIn("missing field", error)

        downgraded = copy.deepcopy(receipt)
        downgraded["version"] = 3
        rejected, error = click_receipt.validate_receipt(downgraded)
        self.assertIsNone(rejected)
        self.assertIn("Successor-reused", error)

    def test_v5_requires_guarded_separate_contract_and_preserves_shard_integrity(self) -> None:
        receipt = _valid_sharded_receipt()
        receipt["version"] = 5
        receipt["evidence"][0]["lineage"] = {
            "mode": "successor-reused", "from_revision": 9,
            "dependency_digest": _digest("e"), "origin_batch_id": "a" * 32,
            "origin_contract_id": "ctr_" + "b" * 32,
            "requalification_mode": "dependency",
        }
        normalized, error = click_receipt.validate_receipt(receipt)
        self.assertEqual(error, "")
        self.assertIsNotNone(normalized)
        for case in ("downgrade", "self-origin", "evidence-authority", "missing-shard", "mixed-identity"):
            with self.subTest(case=case):
                bad = copy.deepcopy(receipt)
                if case == "downgrade":
                    bad["version"] = 4
                elif case == "self-origin":
                    bad["evidence"][0]["lineage"]["origin_contract_id"] = bad["contract"]["id"]
                elif case == "evidence-authority":
                    bad["contract"] = None
                    bad["authority"].update(mode="evidence", approval_bound=False, execution_authority="host")
                elif case == "missing-shard":
                    bad["evidence"].pop()
                else:
                    bad["evidence"][0]["lineage"]["origin_evidence_session_id"] = "evs_" + "c" * 32
                self.assertIsNone(click_receipt.validate_receipt(bad)[0])

    def test_unknown_sensitive_or_self_referential_fields_fail_closed(self) -> None:
        for path, field in (
            ((), "runner_token"),
            (("contract",), "plain_language"),
            (("evidence", 0), "receipt_digest"),
        ):
            receipt = _valid_receipt()
            target: object = receipt
            for component in path:
                target = target[component]  # type: ignore[index]
            assert isinstance(target, dict)
            target[field] = "secret-or-self-reference"
            with self.subTest(path=path, field=field):
                normalized, error = click_receipt.validate_receipt(receipt)
                self.assertIsNone(normalized)
                self.assertIn("unsupported field", error)

    def test_contract_identity_and_turn_separation_are_strict(self) -> None:
        cases = []
        invalid_id = _valid_receipt()
        invalid_id["contract"]["id"] = "ctr_bad"  # type: ignore[index]
        cases.append(invalid_id)
        invalid_digest = _valid_receipt()
        invalid_digest["contract"]["digest"] = _digest("A")  # type: ignore[index]
        cases.append(invalid_digest)
        same_turn = _valid_receipt()
        same_turn["contract"]["approved_turn_id"] = "turn-stage"  # type: ignore[index]
        cases.append(same_turn)

        for receipt in cases:
            with self.subTest(receipt=receipt["contract"]):
                normalized, error = click_receipt.validate_receipt(receipt)
                self.assertIsNone(normalized)
                self.assertTrue(error)

    def test_every_evidence_source_must_match_the_final_revision(self) -> None:
        receipt = _valid_receipt()
        receipt["evidence"][0]["verified_revision"] = 1  # type: ignore[index]

        normalized, error = click_receipt.validate_receipt(receipt)

        self.assertIsNone(normalized)
        self.assertIn("final revision", error)

    def test_argv_evidence_requires_all_fingerprints(self) -> None:
        for field in ("check_digest", "environment_digest", "executable_digest"):
            receipt = _valid_receipt()
            source = receipt["evidence"][1]  # type: ignore[index]
            source[field] = ""
            with self.subTest(field=field):
                normalized, error = click_receipt.validate_receipt(receipt)
                self.assertIsNone(normalized)
                self.assertIn("Completed argv evidence requires", error)

    def test_dependency_reuse_requires_earlier_revision_and_digest(self) -> None:
        receipt = _valid_receipt()
        source = receipt["evidence"][1]  # type: ignore[index]
        source["lineage"] = {
            "mode": "dependency-reused",
            "from_revision": 1,
            "dependency_digest": _digest("e"),
        }
        normalized, error = click_receipt.validate_receipt(receipt)
        self.assertEqual(error, "")
        self.assertIsNotNone(normalized)

        source["lineage"]["from_revision"] = 2
        normalized, error = click_receipt.validate_receipt(receipt)
        self.assertIsNone(normalized)
        self.assertIn("earlier revision", error)

    def test_non_argv_evidence_cannot_claim_argv_fingerprints(self) -> None:
        receipt = _valid_receipt()
        receipt["evidence"][0]["check_digest"] = _digest("f")  # type: ignore[index]

        normalized, error = click_receipt.validate_receipt(receipt)

        self.assertIsNone(normalized)
        self.assertIn("Non-argv evidence", error)

    def test_unavailable_workspace_is_explicit_and_cannot_claim_tree_digests(self) -> None:
        receipt = _valid_receipt()
        workspace = receipt["execution"]["workspace"]  # type: ignore[index]
        workspace.update(
            {"assurance": "unavailable", "root_digest": "", "tree_digest": ""}
        )
        receipt["coverage"]["excluded"].append(  # type: ignore[index]
            "protected-tree-unavailable"
        )

        normalized, error = click_receipt.validate_receipt(receipt)

        self.assertEqual(error, "")
        self.assertIsNotNone(normalized)
        workspace["tree_digest"] = _digest("c")
        normalized, error = click_receipt.validate_receipt(receipt)
        self.assertIsNone(normalized)
        self.assertIn("must not claim", error)

    def test_duplicate_evidence_source_keys_fail_closed(self) -> None:
        receipt = _valid_receipt()
        receipt["evidence"].append(_argv_source())  # type: ignore[union-attr]

        normalized, error = click_receipt.validate_receipt(receipt)

        self.assertIsNone(normalized)
        self.assertIn("must be unique", error)

    def test_unsigned_envelope_detects_body_or_digest_mismatch(self) -> None:
        envelope, error = click_receipt.create_envelope(_valid_receipt())

        self.assertEqual(error, "")
        assert envelope is not None
        self.assertEqual(envelope["assurance"], "unsigned-integrity-only")
        normalized, error = click_receipt.validate_envelope(envelope)
        self.assertEqual(error, "")
        self.assertIsNotNone(normalized)

        changed = copy.deepcopy(envelope)
        changed["receipt"]["execution"]["mutation_revision"] = 3
        normalized, error = click_receipt.validate_envelope(changed)
        self.assertIsNone(normalized)
        self.assertTrue(error)

    def test_capability_claims_are_ordered_completed_and_secret_free(self) -> None:
        receipt = _valid_receipt()
        receipt["capabilities"][1]["sequence"] = 3  # type: ignore[index]
        normalized, error = click_receipt.validate_receipt(receipt)
        self.assertIsNone(normalized)
        self.assertIn("contiguous", error)

        receipt = _valid_receipt()
        receipt["capabilities"][1]["runner_token_digest"] = _digest("f")  # type: ignore[index]
        normalized, error = click_receipt.validate_receipt(receipt)
        self.assertIsNone(normalized)
        self.assertIn("unsupported field", error)


if __name__ == "__main__":
    unittest.main()
