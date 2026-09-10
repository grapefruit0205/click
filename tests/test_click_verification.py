from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import unittest
from unittest import mock

from hooks import (
    click_change_policy,
    click_dependency_cache,
    click_dependency_trace,
    click_evidence,
    click_evidence_shards,
    click_gate,
    click_host_coverage,
    click_verification,
    click_verification_reuse,
)


class ClickVerificationTests(unittest.TestCase):
    def test_lifecycle_modules_have_one_way_dependencies(self) -> None:
        root = Path(click_verification.__file__).parent
        allowed = {
            "common": set(), "prepare": {"common"}, "claims": {"common"},
            "results": {"common"}, "runner": {"common", "claims", "results"},
        }
        lifecycle = {"click_verification_" + name for name in allowed}
        for name, dependencies in allowed.items():
            source = (root / ("click_verification_" + name + ".py")).read_text(encoding="utf-8")
            imported = {
                argument.value
                for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "load_siblings"
                for argument in node.args[1:]
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
            }
            with self.subTest(domain=name):
                self.assertEqual(imported & lifecycle, {
                    "click_verification_" + dependency for dependency in dependencies
                })
                self.assertNotIn("click_verification", imported)

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
        root = Path(click_verification.__file__).parent
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in root.glob("click_verification*.py")
        )
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
        for name, owner in {
            "prepare": "prepare", "claim_run": "claims",
            "record_result": "results", "release_unclaimed_reservation": "claims",
            "run": "runner",
        }.items():
            with self.subTest(entrypoint=name):
                self.assertEqual(
                    getattr(click_verification, name).__module__,
                    "hooks.click_verification_" + owner,
                )

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

    def _reuse_validation_fixture(self):
        host = click_host_coverage.receipt("codex")
        receipt = self._dependency_receipt(
            click_dependency_cache.dependency_observation(["src/unit.py"])
        )
        source = self._stale_dependency_source(receipt, host)
        snapshot = {
            "version": 1, "provider": click_change_policy.SNAPSHOT_PROVIDER_NAME,
            "object_format": "sha1", "head": "a" * 40, "overrides": [],
        }
        snapshot["digest"] = click_change_policy._digest(snapshot)
        safe_receipt = {
            "provider": click_change_policy.PROVIDER_NAME,
            "config_digest": "a" * 64, "entry_digest": "b" * 64,
            "patterns": ["README.md"], "baseline": snapshot,
        }
        source["verified_safe_change_receipt"] = safe_receipt
        decision = self._safe_decision(source, self._safe_context(1))
        bindings = {
            "contract_digest": "3" * 64, "group_digest": "4" * 64,
            "git_root": "/workspace", "environment_digest": "6" * 64,
            "executable_digest": "7" * 64, "host_coverage": host,
        }
        return source, receipt, decision, bindings

    def _safe_context(self, revision):
        return {
            "source_key": "9" * 64, "revision": revision,
            "git_root": "/workspace", "tree_digest": "8" * 64,
        }

    def _safe_decision(self, source, context):
        receipt = copy.deepcopy(source["verified_safe_change_receipt"])
        return {
            "status": "reuse", "reason": "no-net-change", "receipt": receipt,
            "changed_paths": [],
            "decision_digest": click_change_policy._digest(click_change_policy._decision_payload(
                source["verified_safe_change_receipt"], receipt, [],
                source["verified_check_digest"], context,
            )),
        }

    def test_reuse_matchers_require_strict_current_and_successful_revisions(self) -> None:
        source, receipt, decision, bindings = self._reuse_validation_fixture()
        def safe_matches(item, revision):
            context = self._safe_context(revision)
            return click_verification.safe_change_receipt_matches(
                item, self._safe_decision(source, context), revision=revision,
                decision_context=context, **bindings,
            )
        matchers = (
            ("exact", "passed", lambda item, revision: click_verification.receipt_matches(item, revision=revision, tree_digest="5" * 64, **bindings)),
            ("dependency", "stale", lambda item, revision: click_verification.dependency_receipt_matches(item, receipt, revision=revision, **bindings)),
            ("safe-change", "stale", safe_matches),
        )
        invalid = (-1, True, False, 0.0, 1.0, 1.9, "0", "1", None, [], {}, float("nan"), float("inf"))
        for name, status, matches in matchers:
            for prior, revision in ((0, 0), (0, 1), (1, 1), (1, 2), (2, 1)):
                with self.subTest(matcher=name, prior=prior, revision=revision):
                    candidate = {**source, "status": status, "verified_revision": prior}
                    self.assertEqual(matches(candidate, revision), prior == revision if name == "exact" else prior < revision)
            for value in invalid:
                with self.subTest(matcher=name, malformed=value):
                    candidate = {**source, "status": status, "verified_revision": value}
                    self.assertFalse(matches(candidate, 1))
                    candidate["verified_revision"] = 0
                    self.assertFalse(matches(candidate, value))
            missing = {**source, "status": status}
            missing.pop("verified_revision")
            self.assertFalse(matches(missing, 1))
            for malformed in (None, [], {}):
                self.assertFalse(matches(malformed, 1))

    def test_receipt_promotions_validate_before_changing_sources(self) -> None:
        source, receipt, decision, _ = self._reuse_validation_fixture()
        promotions = (
            ("dependency_reuse_count", receipt, click_verification.promote_dependency_receipt),
            ("safe_change_reuse_count", decision, lambda *args, **kwargs: click_verification.promote_safe_change_receipt(*args, decision_context=self._safe_context(1), **kwargs)),
        )
        invalid = (-1, True, False, 0.0, 1.0, 1.9, "0", "1", None, [], {}, float("nan"), float("inf"))
        for counter, payload, promote in promotions:
            for field in ("current_revision", "verified_revision", counter):
                for value in invalid:
                    with self.subTest(counter=counter, field=field, value=value):
                        candidate = copy.deepcopy(source)
                        revision = value if field == "current_revision" else 1
                        if field != "current_revision":
                            candidate[field] = value
                        before = json.dumps(candidate, sort_keys=True)
                        with self.assertRaises(ValueError):
                            promote(candidate, payload, revision=revision, tree_digest="8" * 64)
                        self.assertEqual(json.dumps(candidate, sort_keys=True), before)
            for revision in (0,):
                candidate = copy.deepcopy(source)
                before = copy.deepcopy(candidate)
                with self.assertRaises(ValueError):
                    promote(candidate, payload, revision=revision, tree_digest="8" * 64)
                self.assertEqual(candidate, before)
            for malformed in (None, {}, {"provider": []}):
                candidate = copy.deepcopy(source)
                before = copy.deepcopy(candidate)
                with self.assertRaises(ValueError):
                    promote(candidate, malformed, revision=1, tree_digest="8" * 64)
                self.assertEqual(candidate, before)
            candidate = copy.deepcopy(source)
            promote(candidate, payload, revision=1, tree_digest="8" * 64)
            self.assertEqual(candidate["status"], "passed")
            self.assertEqual(candidate["verified_revision"], 1)
            self.assertEqual(candidate[counter], 1)
            self.assertEqual(candidate["verified_at"], source["verified_at"])

    def test_safe_change_decision_cannot_move_to_another_source_or_scope(self) -> None:
        source, _, decision, bindings = self._reuse_validation_fixture()
        context = self._safe_context(1)
        self.assertTrue(click_verification.safe_change_receipt_matches(
            source, decision, revision=1, decision_context=context, **bindings,
        ))
        changed_source = copy.deepcopy(source)
        changed_source["verified_check_digest"] = "a" * 64
        other_baseline = copy.deepcopy(source)
        snapshot = other_baseline["verified_safe_change_receipt"]["baseline"]
        snapshot["head"] = "b" * 40
        snapshot["digest"] = click_change_policy._digest({key: value for key, value in snapshot.items() if key != "digest"})
        for candidate, expected, scope, revision in (
            (changed_source, {**bindings, "group_digest": "a" * 64}, context, 1),
            (other_baseline, bindings, context, 1),
            ({**source, "verified_root": "/elsewhere"}, {**bindings, "git_root": "/elsewhere"}, {**context, "git_root": "/elsewhere"}, 1),
            (source, bindings, {**context, "source_key": "b" * 64}, 1),
            (source, bindings, {**context, "tree_digest": "c" * 64}, 1),
            (source, bindings, {**context, "revision": 2}, 2),
            (source, bindings, context, 2),
            (source, {**bindings, "environment_digest": "d" * 64}, context, 1),
            (source, {**bindings, "executable_digest": "e" * 64}, context, 1),
        ):
            with self.subTest(expected=expected, scope=scope, revision=revision):
                before = copy.deepcopy(candidate)
                self.assertFalse(click_verification.safe_change_receipt_matches(
                    candidate, decision, revision=revision, decision_context=scope, **expected,
                ))
                self.assertEqual(candidate, before)

    def test_safe_change_promotion_revalidates_decision_before_mutation(self) -> None:
        source, _, decision, bindings = self._reuse_validation_fixture()
        context = self._safe_context(1)
        variants = [
            {**decision, "decision_digest": "f" * 64},
            {**decision, "changed_paths": ["README.md"]},
            {**decision, "receipt": {**decision["receipt"], "config_digest": "a1" * 32}},
        ]
        for value in variants:
            with self.subTest(decision=value):
                candidate = copy.deepcopy(source)
                before = copy.deepcopy(candidate)
                self.assertFalse(click_verification.safe_change_receipt_matches(
                    candidate, value, revision=1, decision_context=context, **bindings,
                ))
                with self.assertRaises(ValueError):
                    click_verification.promote_safe_change_receipt(
                        candidate, value, revision=1, tree_digest="8" * 64,
                        decision_context=context,
                    )
                self.assertEqual(candidate, before)
        for scope in (None, {**context, "revision": 2}, {**context, "tree_digest": "b" * 64}):
            with self.subTest(context=scope):
                candidate = copy.deepcopy(source)
                before = copy.deepcopy(candidate)
                with self.assertRaises(ValueError):
                    click_verification.promote_safe_change_receipt(
                        candidate, decision, revision=1, tree_digest="8" * 64,
                        decision_context=scope,
                    )
                self.assertEqual(candidate, before)

    def test_successor_revision_and_counter_preconditions_reject_without_mutation(self) -> None:
        source, _, _, bindings = self._reuse_validation_fixture()
        previous = {**source, "status": "passed"}
        arguments = {
            key: value for key, value in bindings.items() if key != "git_root"
        }
        arguments.update(revision=0, units=1, tree_digest="5" * 64, exact_tree=True)
        for value in (-1, True, False, 0.0, 1.9, "0", None, [], {}, float("nan"), float("inf")):
            with self.subTest(value=value):
                current = {"status": "ready"}
                with self.assertRaises(ValueError):
                    click_verification_reuse.requalify_successor_baseline(current, previous, **{**arguments, "revision": value})
                self.assertEqual(current, {"status": "ready"})
                current = {"successor_reuse_count": value}
                before = json.dumps(current)
                with self.assertRaises(ValueError):
                    click_verification_reuse.mark_successor_reuse(current, {}, mode="exact")
                self.assertEqual(json.dumps(current), before)

    def _successor_source_fixture(self):
        contract = {"verification": {"evidence": [
            {"id": "E1", "kind": "argv", "dependencies": ["tests/"]}
        ]}}
        current = click_evidence.fresh_state(contract)["sources"][click_evidence.evidence_key("E1")]
        previous = copy.deepcopy(current)
        facts, _, _, bindings = self._reuse_validation_fixture()
        previous.update(facts)
        previous.update(status="passed", last_exit_code=0, attempts=7, verified_at=123,
                        last_success_duration_ms=42)
        shard = {
            "provider": click_evidence_shards.PROVIDER_NAME,
            "parent_source_key": "a" * 64, "parent_check_digest": "b" * 64,
            "shard_id": "unit", "shard_count": 2, "plan_digest": "c" * 64,
            "entry_digest": "d" * 64, "inventory_digest": "e" * 64,
            "check_digest": bindings["group_digest"],
        }
        current["shard"] = copy.deepcopy(shard)
        previous["shard"] = copy.deepcopy(shard)
        arguments = {key: value for key, value in bindings.items() if key != "git_root"}
        arguments.update(contract_digest="8" * 64, revision=1, units=1,
                         tree_digest="5" * 64, exact_tree=True)
        return current, previous, arguments

    def test_successor_copies_only_allowed_facts_into_current_defaults(self) -> None:
        current, previous, arguments = self._successor_source_fixture()
        previous.update(future_field={"claim": "untrusted future extension"},
                        verified_future_field=True, runner_token="not an actual source field")
        original = copy.deepcopy(previous)
        current_shard = current["shard"]
        current_patterns = list(current["dependency_patterns"])
        click_verification_reuse.requalify_successor_baseline(current, previous, **arguments)
        for unknown in ("future_field", "verified_future_field", "runner_token"):
            self.assertNotIn(unknown, current)
        self.assertEqual(previous, original)
        self.assertEqual(current["kind"], "argv")
        self.assertEqual(current["dependency_patterns"], current_patterns)
        self.assertEqual(current["shard"], current_shard)
        self.assertIsNot(current["shard"], current_shard)
        self.assertIsNot(current["shard"], previous["shard"])
        for key in ("attempts", "unchanged_failure_retries", "dependency_reuse_count",
                    "safe_change_reuse_count", "successor_reuse_count"):
            self.assertEqual(current[key], 0)
        self.assertEqual(current["verified_contract_digest"], arguments["contract_digest"])
        self.assertEqual(current["verified_revision"], 1)
        self.assertEqual(current["reserved_check_digest"], arguments["group_digest"])
        self.assertEqual(current["verified_at"], 123)
        self.assertEqual(current["last_success_duration_ms"], 42)
        self.assertEqual(current["verified_dependency_manifest_digest"], previous["verified_dependency_manifest_digest"])
        current["verified_dependency_observation"]["paths"].append("other.py")
        current["verified_safe_change_receipt"]["baseline"]["overrides"].append({"path": "README.md", "identity": "missing"})
        self.assertEqual(previous, original)

    def test_successor_requalification_failure_leaves_both_sources_unchanged(self) -> None:
        for field, value in (("status", "failed"), ("status", "running"),
                             ("status", "ready"), ("last_exit_code", 1),
                             ("verified_check_digest", "9" * 64),
                             ("verified_at", 0), ("kind", "manual")):
            with self.subTest(field=field, value=value):
                current, previous, arguments = self._successor_source_fixture()
                previous[field] = value
                before_current, before_previous = copy.deepcopy(current), copy.deepcopy(previous)
                with self.assertRaises(ValueError):
                    click_verification_reuse.requalify_successor_baseline(current, previous, **arguments)
                self.assertEqual(current, before_current)
                self.assertEqual(previous, before_previous)
        current, previous, arguments = self._successor_source_fixture()
        current["dependency_patterns"] = ["other/"]
        current["dependency_declaration_digest"] = click_dependency_cache.patterns_digest(("other/",))
        before = copy.deepcopy(current)
        with self.assertRaises(ValueError):
            click_verification_reuse.requalify_successor_baseline(current, previous, **arguments)
        self.assertEqual(current, before)

    def test_successor_reuse_preserves_execution_timestamp(self) -> None:
        current, previous, arguments = self._successor_source_fixture()
        click_verification_reuse.requalify_successor_baseline(current, previous, **arguments)
        metadata = {"batch_id": "a" * 32, "evidence_session_id": "evs_" + "b" * 32,
                    "candidate_digest": "c" * 64, "origin_revision": 0}
        with mock.patch.object(click_verification_reuse.time, "time", return_value=999):
            click_verification_reuse.mark_successor_reuse(current, metadata, mode="exact")
        self.assertEqual(current["verified_at"], 123)
        self.assertEqual(current["last_successor_reused_at"], 999)


if __name__ == "__main__":
    unittest.main()
