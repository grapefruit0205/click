from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

from click_gate_test_support import (
    CLICK_EVIDENCE,
    CLICK_VERIFICATION,
    ClickGateTestCase,
)
from hooks import click_dashboard_projection as dashboard
from hooks import click_evidence_shards as evidence_shards
from hooks import click_lifecycle
from hooks import click_sharding_setup as setup
from hooks import click_test_inventory as inventory


ROOT = Path(__file__).resolve().parents[1]
SUPPORTED = (
    inventory.click_collector_runtime.capability().status == "implemented"
    and inventory.click_collector_runtime.cpython_supported(
        sys.implementation.name, sys.version_info[:3]
    )
)
SETTING_FREE_E2E_SUPPORTED = (
    SUPPORTED
    and sys.platform == "linux"
    and sys.implementation.name == "cpython"
    and sys.version_info[:3] == (3, 12, 3)
)
VITEST_SOURCE = ROOT / "tests" / "fixtures" / "vitest-v5"
VITEST_AVAILABLE = bool(
    shutil.which("node")
    and shutil.which("npx")
    and (VITEST_SOURCE / "node_modules" / "vitest" / "package.json").is_file()
)
JEST_SOURCE = ROOT / "tests" / "fixtures" / "jest-v30"
JEST_AVAILABLE = bool(
    shutil.which("node")
    and shutil.which("npx")
    and (JEST_SOURCE / "node_modules" / "jest" / "package.json").is_file()
)


def write(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")


def git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )


def stable_setup_durations(case: unittest.TestCase) -> None:
    """Run real checks but isolate lifecycle assertions from timing noise.

    Parent-relative arithmetic is covered independently with adverse timings.
    These fixture durations must never be published as benchmark measurements.
    """
    execute = setup._run_check
    def run(root, argv):
        result = execute(root, argv)
        parent = getattr(case, "command", [])
        if isinstance(parent, str):
            parent = shlex.split(parent)
        return {**result, "duration_ms": 1000.0 if argv == parent else 100.0}
    patcher = mock.patch.object(setup, "_run_check", side_effect=run)
    patcher.start()
    case.addCleanup(patcher.stop)


class ShardingControlParsingTests(unittest.TestCase):
    def test_public_commands_need_no_json_and_keep_argv_exact(self) -> None:
        action, raw, error = click_lifecycle.control_request(
            "click-gate sharding init -- python3 -m unittest discover -s tests -q"
        )
        self.assertEqual(action, "sharding")
        self.assertEqual(error, "")
        self.assertEqual(
            json.loads(raw),
            {
                "operation": "init",
                "command": [
                    "python3",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-q",
                ],
            },
        )
        for command, operation in (
            ("click-gate sharding init", "init"),
            ("click-gate sharding status", "status"),
            ("click-gate sharding refresh", "refresh"),
        ):
            action, raw, error = click_lifecycle.control_request(command)
            self.assertEqual((action, error), ("sharding", ""))
            self.assertEqual(json.loads(raw), {"operation": operation, "command": []})

    def test_malformed_public_command_is_a_control_error(self) -> None:
        action, _, error = click_lifecycle.control_request(
            "click-gate sharding init python3 -m unittest"
        )
        self.assertEqual(action, "")
        self.assertIn("--", error)


@unittest.skipUnless(SUPPORTED, "setup profile requires CPython 3.10-3.14")
class ShardingSetupStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        stable_setup_durations(self)
        temporary = tempfile.TemporaryDirectory(prefix="click-sharding-setup-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.plugin_data = self.base / "plugin-data"
        environment = mock.patch.dict(
            os.environ,
            {
                "PLUGIN_DATA": str(self.plugin_data),
                "CLICK_CONFIG_HOME": str(self.plugin_data),
                "CLICK_SHARDING_MIN_PARENT_MS": "0",
                "CLICK_SHARDING_MIN_AVOIDABLE_MS": "0",
                "CLICK_SHARDING_MANAGEMENT_RESERVE_MS": "0",
            },
            clear=False,
        )
        environment.start()
        self.addCleanup(environment.stop)
        startup_probe = mock.patch.object(
            setup,
            "_run_startup_probe",
            return_value={"status": "passed", "duration_ms": 0.0, "reason": ""},
        )
        startup_probe.start()
        self.addCleanup(startup_probe.stop)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "click-tests@example.invalid")
        git(self.root, "config", "user.name", "Click Tests")
        write(self.root, ".gitignore", "__pycache__/\n*.pyc\n")
        write(self.root, "app/__init__.py", "")
        write(self.root, "app/value.py", "VALUE = 2\n")
        write(self.root, "tests/test_alpha.py", """
            import unittest
            from app.value import VALUE

            class Alpha(unittest.TestCase):
                def test_alpha(self):
                    self.assertEqual(VALUE, 2)
        """)
        write(self.root, "tests/test_beta.py", """
            import unittest
            from app.value import VALUE

            class Beta(unittest.TestCase):
                def test_beta(self):
                    self.assertEqual(VALUE + 1, 3)
        """)
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "fixture")
        self.command = [
            "python3",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-q",
        ]
        self.analysis_contract = "ctr_" + "1" * 32
        self.application_contract = "ctr_" + "2" * 32

    def application_state(self, digest: str) -> dict[str, object]:
        return {
            "contract_id": self.application_contract,
            "presentation": {
                "must_hold": [f"Apply proposal digest {digest}"],
            },
            "verification": {},
            "evidence_state": {
                "sources": {
                    setup.evidence.evidence_key(setup.BASELINE_EVIDENCE_ID): {
                        "kind": "argv"
                    }
                }
            },
        }

    def commit_policy(self) -> None:
        git(self.root, "add", *setup.POLICY_PATHS)
        git(self.root, "commit", "-qm", "add Click sharding policy")

    def test_proposal_apply_commit_and_bootstrap_are_separate_idempotent_steps(self) -> None:
        initial = setup.status(self.root)
        self.assertEqual(initial["status"], "selection-required")
        self.assertFalse((self.root / ".click").exists())

        proposal = setup.generate(
            self.root, self.command, self.analysis_contract
        )
        self.assertEqual(proposal["status"], "approval-required", proposal)
        digest = proposal["proposal_digest"]
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertFalse((self.root / ".click").exists())
        self.assertFalse(setup.state_path(self.root).is_relative_to(self.root))
        self.assertEqual(
            setup.generate(self.root, self.command, self.analysis_contract)[
                "proposal_digest"
            ],
            digest,
        )

        unbound = {
            "contract_id": self.application_contract,
            "presentation": {"must_hold": ["Apply reviewed settings"]},
        }
        waiting = setup.plan(self.root, "refresh", [], unbound)
        self.assertEqual(waiting["action"], "report")
        self.assertEqual(
            waiting["report"]["reasons"],
            ["proposal-digest-approval-required"],
        )
        missing_baseline = self.application_state(digest)
        missing_baseline.pop("evidence_state")
        waiting = setup.plan(self.root, "refresh", [], missing_baseline)
        self.assertEqual(
            waiting["report"]["reasons"],
            ["baseline-evidence-approval-required"],
        )
        self.assertEqual(
            setup.plan(
                self.root,
                "refresh",
                [],
                self.application_state(digest),
            )["action"],
            "apply",
        )

        index = self.root / git(self.root, "rev-parse", "--git-path", "index").stdout.strip()
        index_before = hashlib.sha256(index.read_bytes()).hexdigest()
        applied = setup.apply(self.root, self.application_contract)
        self.assertEqual(applied["status"], "commit-required")
        self.assertEqual(hashlib.sha256(index.read_bytes()).hexdigest(), index_before)
        if os.name != "nt":
            self.assertEqual(
                stat.S_IMODE((self.root / ".click").stat().st_mode), 0o755
            )
        for relative in setup.POLICY_PATHS:
            self.assertTrue((self.root / relative).is_file())

        self.assertEqual(setup.status(self.root)["status"], "commit-required")
        self.commit_policy()
        self.assertEqual(setup.status(self.root)["status"], "baseline-required")

        bootstrapped = setup.bootstrap(self.root, self.application_contract)
        self.assertEqual(bootstrapped["status"], "baseline-required", bootstrapped)
        metrics = bootstrapped["bootstrap"]
        self.assertEqual(metrics["status"], "passed")
        self.assertTrue(metrics["inventory_equal"])
        self.assertEqual(len(metrics["children"]), 2)
        self.assertEqual(metrics["analysis_workspace_snapshot_scans"], 9)
        self.assertEqual(metrics["avoided_initial_analysis_scans"], 3)
        self.assertEqual(
            metrics["comparison_scope"],
            "first-bootstrap-parent-vs-sequential-children-not-savings",
        )
        projection = setup.dashboard_setup_projection(bootstrapped)
        self.assertEqual(
            projection["comparison_net_ms"], metrics["comparison_net_ms"]
        )
        self.assertFalse(projection["reuse_ready"])

    def test_evidence_bootstrap_defers_children_and_survives_source_only_commit(self) -> None:
        authority = "evs_" + "3" * 32
        generated = setup.generate(self.root, self.command, authority, runtime_mode="evidence")
        self.assertEqual(generated["status"], "application-ready", generated)
        setup.apply(self.root, authority, runtime_mode="evidence")
        self.commit_policy()
        with mock.patch.object(setup, "_run_check", wraps=setup._run_check) as execute:
            report = setup.bootstrap(self.root, authority, runtime_mode="evidence")
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.args[1], self.command)
        self.assertEqual(report["status"], "baseline-required")
        self.assertFalse(report["reuse_ready"])
        self.assertTrue(all(child["status"] == "baseline-pending"
                            for child in report["bootstrap"]["children"]))
        projected = setup.dashboard_setup_projection(report)
        self.assertIsNone(projected["comparison_net_ms"])
        self.assertEqual(projected["comparison_scope"], "parent-bootstrap-children-pending-not-savings")
        write(self.root, "app/value.py", "VALUE = 2\n# source-only revision\n")
        git(self.root, "add", "app/value.py")
        git(self.root, "commit", "-qm", "source-only revision")
        with mock.patch.object(setup, "_run_check", side_effect=AssertionError("status executed")):
            report = setup.status(self.root)
            action = setup.plan(self.root, "refresh", [], {}, runtime_mode="evidence", authority_id=authority)
        self.assertNotIn("bootstrap-validation-required", report["reasons"])
        self.assertEqual(action["action"], "verify")

    def test_user_policy_is_never_overwritten_and_discovery_drift_needs_review(self) -> None:
        proposal = setup.generate(self.root, self.command, self.analysis_contract)
        setup.apply(self.root, self.application_contract)
        self.commit_policy()
        setup.bootstrap(self.root, self.application_contract)
        original = {
            relative: (self.root / relative).read_bytes()
            for relative in setup.POLICY_PATHS
        }

        policy = self.root / setup.POLICY_PATHS[0]
        policy.write_bytes(original[setup.POLICY_PATHS[0]] + b" \n")
        tampered = setup.status(self.root)
        self.assertEqual(tampered["status"], "review-required")
        self.assertEqual(tampered["reasons"], ["policy-worktree-changed"])
        policy.write_bytes(original[setup.POLICY_PATHS[0]])
        self.assertEqual(setup.status(self.root)["status"], "baseline-required")

        write(self.root, "README.md", "ordinary source edit\n")
        self.assertEqual(setup.status(self.root)["status"], "baseline-required")
        write(self.root, "tests/test_gamma.py", """
            import unittest
            class Gamma(unittest.TestCase):
                def test_gamma(self):
                    self.assertTrue(True)
        """)
        drift = setup.status(self.root)
        self.assertEqual(drift["status"], "review-required")
        self.assertEqual(drift["reasons"], ["test-discovery-changed"])
        self.assertEqual(
            setup.plan(
                self.root,
                "refresh",
                [],
                self.application_state(proposal["proposal_digest"]),
            )["action"],
            "generate",
        )
        refreshed = setup.generate(self.root, self.command, self.application_contract)
        self.assertEqual(refreshed["status"], "approval-required")
        self.assertEqual(refreshed["lineage_kind"], "generated-update")
        self.assertTrue(
            all(
                item["action"] == "update-generated"
                for item in refreshed["review"]["files"]
            )
        )
        self.assertTrue(
            any(item["diff_preview"] for item in refreshed["review"]["files"])
        )
        for relative, expected in original.items():
            self.assertEqual((self.root / relative).read_bytes(), expected)

    def test_generated_policy_update_resumes_partial_apply_and_tracks_test_structure(self) -> None:
        setup.generate(self.root, self.command, self.analysis_contract)
        setup.apply(self.root, self.application_contract)
        self.commit_policy()
        setup.bootstrap(self.root, self.application_contract)

        alpha = self.root / "tests/test_alpha.py"
        original_alpha = alpha.read_text(encoding="utf-8")
        alpha.write_text(
            original_alpha.replace(
                "self.assertEqual(VALUE, 2)",
                "self.assertEqual(VALUE, 1 + 1)",
            ),
            encoding="utf-8",
        )
        self.assertEqual(setup.status(self.root)["status"], "baseline-required")

        alpha.write_text(
            alpha.read_text(encoding="utf-8").replace(
                "        self.assertEqual(VALUE, 1 + 1)\n",
                "        self.assertEqual(VALUE, 1 + 1)\n"
                "    def test_alpha_again(self):\n"
                "        self.assertEqual(VALUE, 2)\n",
            ),
            encoding="utf-8",
        )
        changed = setup.status(self.root)
        self.assertEqual(changed["status"], "review-required")
        self.assertEqual(changed["reasons"], ["test-inventory-changed"])

        # Exercise a real policy update. Adding a case alone keeps the exact
        # file children and must no longer widen every child's dependencies.
        write(self.root, "app/extra.py", "EXTRA = 2\n")
        alpha.write_text("from app.extra import EXTRA\n" + alpha.read_text(encoding="utf-8"),
                         encoding="utf-8")
        update_analysis = "ctr_" + "3" * 32
        update_application = "ctr_" + "4" * 32
        proposal = setup.generate(self.root, self.command, update_analysis)
        self.assertEqual(proposal["status"], "approval-required", proposal)
        self.assertEqual(proposal["lineage_kind"], "generated-update")
        self.assertEqual(proposal["review"]["test_count"], 3)
        state = setup._read_state(self.root)
        assert state is not None
        payload = setup._artifact(state)
        first_path = self.root / setup.POLICY_PATHS[0]
        first_path.write_bytes(payload[setup.POLICY_PATHS[0]])

        applied = setup.apply(self.root, update_application)
        self.assertEqual(applied["status"], "commit-required")
        for relative in setup.POLICY_PATHS:
            self.assertEqual((self.root / relative).read_bytes(), payload[relative])
        self.commit_policy()
        bootstrapped = setup.bootstrap(self.root, update_application)
        self.assertEqual(bootstrapped["bootstrap"]["status"], "passed")
        self.assertEqual(
            bootstrapped["bootstrap"]["parent"]["status"], "passed"
        )

        beta = self.root / "tests/test_beta.py"
        moved = self.root / "tests/test_moved.py"
        beta.rename(moved)
        self.assertEqual(
            setup.status(self.root)["reasons"], ["test-discovery-changed"]
        )
        moved.rename(beta)
        self.assertEqual(setup.status(self.root)["status"], "baseline-required")
        beta.unlink()
        self.assertEqual(
            setup.status(self.root)["reasons"], ["test-discovery-changed"]
        )

    def test_added_case_preserves_policy_bytes_and_needs_no_policy_recommit(self) -> None:
        setup.generate(self.root, self.command, self.analysis_contract)
        setup.apply(self.root, self.application_contract)
        self.commit_policy()
        before = {relative: (self.root / relative).read_bytes() for relative in setup.POLICY_PATHS}
        alpha = self.root / "tests/test_alpha.py"
        with alpha.open("a", encoding="utf-8") as stream:
            stream.write("\n    def test_another_case(self):\n        self.assertEqual(VALUE, 2)\n")
        self.assertEqual(setup.status(self.root)["reasons"], ["test-inventory-changed"])
        proposed = setup.generate(self.root, self.command, "ctr_" + "3" * 32)
        self.assertEqual(proposed["status"], "approval-required", proposed)
        self.assertEqual(proposed["review"]["test_count"], 3)
        applied = setup.apply(self.root, "ctr_" + "4" * 32)
        self.assertEqual(applied["status"], "baseline-required", applied)
        self.assertEqual(before, {relative: (self.root / relative).read_bytes() for relative in setup.POLICY_PATHS})
        self.assertEqual(git(self.root, "diff", "HEAD", "--", *setup.POLICY_PATHS).stdout, "")

    def test_short_parent_skips_child_cost_probes_and_keeps_whole_suite(self) -> None:
        proposal = {
            "analysis": {"command": {"argv": self.command}},
            "children": [{"argv": [*self.command, "-k", "one"]}],
        }
        parent = {
            "status": "passed",
            "exit_code": 0,
            "duration_ms": 12.0,
            "reason": "",
        }
        with (
            mock.patch.dict(
                os.environ,
                {"CLICK_SHARDING_MIN_PARENT_MS": "250"},
                clear=False,
            ),
            mock.patch.object(setup.inventory, "workspace_snapshot", return_value={}),
            mock.patch.object(setup, "_run_check", return_value=parent),
            mock.patch.object(setup, "_run_startup_probe") as startup,
        ):
            policy = setup._measure_cost_policy(self.root, proposal)
        self.assertEqual(policy["strategy"], "whole-suite")
        self.assertEqual(policy["reason"], "parent-below-sharding-threshold")
        self.assertEqual(
            policy["measurement_status"], "children-skipped-for-short-parent"
        )
        startup.assert_not_called()

    def test_concurrent_initialization_and_existing_target_fail_closed(self) -> None:
        proposal = setup.generate(self.root, self.command, self.analysis_contract)
        with setup._project_lock(self.root):
            with self.assertRaisesRegex(setup.SetupError, "concurrent-initialization"):
                setup.generate(self.root, self.command, self.analysis_contract)

        state = setup._read_state(self.root)
        assert state is not None
        payload = setup._artifact(state)
        target = self.root / setup.POLICY_PATHS[1]
        target.parent.mkdir(parents=True)
        target.write_text("user configuration\n", encoding="utf-8")
        with self.assertRaisesRegex(setup.SetupError, "policy-worktree-changed"):
            setup.apply(self.root, self.application_contract)
        self.assertEqual(target.read_text(encoding="utf-8"), "user configuration\n")
        self.assertEqual(
            payload[setup.POLICY_PATHS[0]],
            setup._artifact(setup._read_state(self.root))[setup.POLICY_PATHS[0]],
        )

    def test_unsupported_command_and_bootstrap_failure_stay_non_ready(self) -> None:
        unsupported = setup.generate(
            self.root,
            ["python3", "-m", "pytest"],
            self.analysis_contract,
        )
        self.assertEqual(unsupported["status"], "unsupported")
        self.assertFalse(unsupported["sharding_ready"])
        self.assertFalse(unsupported["reuse_ready"])
        self.assertEqual(setup.status(self.root)["status"], "unsupported")

        proposal = setup.generate(
            self.root, self.command, self.application_contract
        )
        setup.apply(self.root, self.analysis_contract)
        self.commit_policy()
        write(self.root, "tests/test_alpha.py", """
            import unittest
            from app.value import VALUE
            class Alpha(unittest.TestCase):
                def test_alpha(self): self.assertEqual(VALUE, 999)
        """)
        failed = setup.bootstrap(self.root, self.analysis_contract)
        self.assertEqual(failed["status"], "blocked", {"proposal": proposal, "failed": failed})
        self.assertEqual(failed["reasons"], ["bootstrap-validation-failed"])
        self.assertFalse(failed["sharding_ready"])
        self.assertFalse(failed["reuse_ready"])

    def test_status_is_read_only_and_does_not_collect_or_execute_project_checks(self) -> None:
        generated = setup.generate(
            self.root, self.command, self.analysis_contract
        )
        with (
            mock.patch.object(
                setup.inventory,
                "collect_once",
                side_effect=AssertionError("status must not collect tests"),
            ),
            mock.patch.object(
                setup,
                "_run_check",
                side_effect=AssertionError("status must not execute checks"),
            ),
        ):
            report = setup.status(self.root)
        self.assertEqual(report["status"], generated["status"])
        self.assertEqual(report["command_status"], "available")
        self.assertEqual(report["inventory_status"], "supported")


@unittest.skipUnless(VITEST_AVAILABLE, "pinned Vitest fixture is unavailable")
class VitestShardingSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="click-vitest-setup-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "project"
        shutil.copytree(VITEST_SOURCE, self.root, symlinks=True)
        shutil.rmtree(self.root / "node_modules" / ".vite", ignore_errors=True)
        self.plugin_data = self.base / "plugin-data"
        environment = mock.patch.dict(
            os.environ,
            {
                "PLUGIN_DATA": str(self.plugin_data),
                "CLICK_CONFIG_HOME": str(self.plugin_data),
                "CLICK_SHARDING_MIN_PARENT_MS": "0",
                "CLICK_SHARDING_MIN_AVOIDABLE_MS": "0",
                "CLICK_SHARDING_MANAGEMENT_RESERVE_MS": "0",
            },
            clear=False,
        )
        environment.start()
        self.addCleanup(environment.stop)
        cost_policy = mock.patch.object(
            setup,
            "_measure_cost_policy",
            return_value={
                "strategy": "sharded",
                "reason": "fixture-selects-sharding",
                "thresholds": {
                    "min_parent_ms": 0.0,
                    "min_avoidable_ms": 0.0,
                    "management_reserve_ms": 0.0,
                },
            },
        )
        cost_policy.start()
        self.addCleanup(cost_policy.stop)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "click-tests@example.invalid")
        git(self.root, "config", "user.name", "Click Tests")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "fixture")
        self.command = ["npx", "--no-install", "vitest", "run"]
        self.authority = "evs_" + "5" * 32

    def test_cost_probe_disables_only_the_derived_vitest_cache(self) -> None:
        before = inventory.workspace_snapshot(self.root, inventory.Limits())
        result = setup._run_check(self.root, self.command)
        after = inventory.workspace_snapshot(self.root, inventory.Limits())

        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(after, before)
        self.assertFalse((self.root / "node_modules" / ".vite").exists())

    def test_init_status_and_refresh_preserve_exact_parent_fallback(self) -> None:
        generated = setup.generate(
            self.root,
            self.command,
            self.authority,
            runtime_mode="evidence",
        )
        self.assertEqual(generated["status"], "application-ready", generated)
        self.assertEqual(generated["adapter"]["id"], inventory.VITEST_ADAPTER)
        applied = setup.apply(
            self.root, self.authority, runtime_mode="evidence"
        )
        self.assertEqual(applied["status"], "commit-required", applied)
        git(self.root, "add", *setup.POLICY_PATHS)
        git(self.root, "commit", "-qm", "add Vitest sharding policy")
        self.assertEqual(setup.status(self.root)["status"], "baseline-required")
        bootstrapped = setup.bootstrap(
            self.root, self.authority, runtime_mode="evidence"
        )
        self.assertEqual(bootstrapped["status"], "baseline-required", bootstrapped)
        self.assertEqual(bootstrapped["bootstrap"]["status"], "passed")
        self.assertTrue(bootstrapped["bootstrap"]["inventory_equal"])
        self.assertEqual(len(bootstrapped["bootstrap"]["children"]), 3)

        body = next((self.root / "tests").rglob("*.test.js"))
        original_body = body.read_bytes()
        body.write_bytes(original_body + b"\n// changed test implementation\n")
        with mock.patch.object(inventory, "analyze", side_effect=AssertionError("status must not collect")):
            unchanged_layout = setup.status(self.root)
        self.assertNotEqual(unchanged_layout["status"], "review-required", unchanged_layout)
        self.assertNotIn("test-inventory-changed", unchanged_layout["reasons"])
        body.write_bytes(original_body)

        lockfile = self.root / "package-lock.json"
        original_lock = lockfile.read_bytes()
        lockfile.write_bytes(original_lock + b"\n")
        lock_changed = setup.status(self.root)
        self.assertEqual(lock_changed["status"], "review-required", lock_changed)
        self.assertEqual(lock_changed["reasons"], ["configuration-changed"])
        lockfile.write_bytes(original_lock)

        write(
            self.root,
            "tests/new.test.js",
            "import { test } from 'vitest'\ntest('new', () => {})\n",
        )
        changed = setup.status(self.root)
        self.assertEqual(changed["status"], "review-required", changed)
        self.assertEqual(changed["reasons"], ["test-discovery-changed"])
        refresh = setup.plan(
            self.root,
            "refresh",
            [],
            {},
            runtime_mode="evidence",
            authority_id=self.authority,
        )
        self.assertEqual(refresh["action"], "generate")
        self.assertEqual(refresh["command"], self.command)

        (self.root / "tests" / "new.test.js").unlink()
        write(self.root, "vitest.config.js", "export default {}\n")
        configuration = setup.status(self.root)
        self.assertEqual(configuration["status"], "review-required", configuration)
        self.assertEqual(configuration["reasons"], ["configuration-changed"])
        refresh = setup.plan(
            self.root,
            "refresh",
            [],
            {},
            runtime_mode="evidence",
            authority_id=self.authority,
        )
        self.assertEqual(refresh["action"], "generate")


@unittest.skipUnless(JEST_AVAILABLE, "pinned Jest fixture is unavailable")
class JestShardingSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="click-jest-setup-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "project"
        shutil.copytree(JEST_SOURCE, self.root, symlinks=True)
        self.plugin_data = self.base / "plugin-data"
        environment = mock.patch.dict(
            os.environ,
            {
                "PLUGIN_DATA": str(self.plugin_data),
                "CLICK_CONFIG_HOME": str(self.plugin_data),
                "CLICK_SHARDING_MIN_PARENT_MS": "0",
                "CLICK_SHARDING_MIN_AVOIDABLE_MS": "0",
                "CLICK_SHARDING_MANAGEMENT_RESERVE_MS": "0",
            },
            clear=False,
        )
        environment.start()
        self.addCleanup(environment.stop)
        cost_policy = mock.patch.object(
            setup,
            "_measure_cost_policy",
            return_value={
                "strategy": "sharded",
                "reason": "fixture-selects-sharding",
                "thresholds": {
                    "min_parent_ms": 0.0,
                    "min_avoidable_ms": 0.0,
                    "management_reserve_ms": 0.0,
                },
            },
        )
        cost_policy.start()
        self.addCleanup(cost_policy.stop)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "click-tests@example.invalid")
        git(self.root, "config", "user.name", "Click Tests")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "fixture")
        self.command = ["npx", "--no-install", "jest", "--runInBand"]
        self.authority = "evs_" + "6" * 32

    def test_init_status_refresh_snapshot_and_exact_children(self) -> None:
        before = inventory.workspace_snapshot(self.root, inventory.Limits())
        self.assertEqual(setup._run_check(self.root, self.command)["status"], "passed")
        self.assertEqual(inventory.workspace_snapshot(self.root, inventory.Limits()), before)

        generated = setup.generate(
            self.root, self.command, self.authority, runtime_mode="evidence"
        )
        self.assertEqual(generated["status"], "application-ready", generated)
        self.assertEqual(generated["adapter"]["id"], inventory.JEST_ADAPTER)
        applied = setup.apply(
            self.root, self.authority, runtime_mode="evidence"
        )
        self.assertEqual(applied["status"], "commit-required", applied)
        git(self.root, "add", *setup.POLICY_PATHS)
        git(self.root, "commit", "-qm", "add Jest sharding policy")
        self.assertEqual(setup.status(self.root)["status"], "baseline-required")
        bootstrapped = setup.bootstrap(
            self.root, self.authority, runtime_mode="evidence"
        )
        self.assertEqual(bootstrapped["bootstrap"]["status"], "passed", bootstrapped)
        self.assertTrue(bootstrapped["bootstrap"]["inventory_equal"])
        self.assertEqual(len(bootstrapped["bootstrap"]["children"]), 5)

        body = next((self.root / "tests").rglob("*.test.js"))
        original_body = body.read_bytes()
        body.write_bytes(original_body + b"\ntest('added case', () => expect(2).toBe(2));\n")
        with mock.patch.object(inventory, "analyze", side_effect=AssertionError("status must not collect")):
            unchanged_layout = setup.status(self.root)
        self.assertNotEqual(unchanged_layout["status"], "review-required", unchanged_layout)
        self.assertNotIn("test-inventory-changed", unchanged_layout["reasons"])
        body.write_bytes(original_body)

        snapshot = next((self.root / "tests").rglob("*.snap"))
        original_snapshot = snapshot.read_bytes()
        snapshot.write_bytes(original_snapshot + b"\n")
        changed = setup.status(self.root)
        self.assertEqual(changed["status"], "review-required", changed)
        self.assertEqual(changed["reasons"], ["configuration-changed"])
        snapshot.write_bytes(original_snapshot)

        write(self.root, "tests/new.test.js", "test('new', () => expect(1).toBe(1));\n")
        discovery = setup.status(self.root)
        self.assertEqual(discovery["status"], "review-required", discovery)
        self.assertEqual(discovery["reasons"], ["test-discovery-changed"])
        refresh = setup.plan(
            self.root,
            "refresh",
            [],
            {},
            runtime_mode="evidence",
            authority_id=self.authority,
        )
        self.assertEqual(refresh["action"], "generate")
        self.assertEqual(refresh["command"], self.command)


@unittest.skipUnless(
    SETTING_FREE_E2E_SUPPORTED,
    "setting-free automatic-sharding E2E requires Linux CPython 3.12.3",
)
class ShardingGateIntegrationTests(ClickGateTestCase):
    """Real child runners with deterministic per-case CPU fixture cost.

    This margin makes lifecycle selection independent of startup noise. These
    synthetic timings are not benchmark evidence or production savings.
    """
    hook_in_process = True

    def setUp(self) -> None:
        super().setUp()
        cost_environment = mock.patch.dict(
            os.environ,
            {
                "CLICK_SHARDING_MIN_PARENT_MS": "0",
                "CLICK_SHARDING_MIN_AVOIDABLE_MS": "0",
                "CLICK_SHARDING_MANAGEMENT_RESERVE_MS": "0",
            },
            clear=False,
        )
        cost_environment.start()
        self.addCleanup(cost_environment.stop)
        startup_probe = mock.patch.object(
            setup,
            "_run_startup_probe",
            return_value={"status": "passed", "duration_ms": 0.0, "reason": ""},
        )
        startup_probe.start()
        self.addCleanup(startup_probe.stop)
        (self.workspace / "verification_fixture.py").unlink()
        git(self.workspace, "init", "-q")
        git(self.workspace, "config", "user.email", "click-tests@example.invalid")
        git(self.workspace, "config", "user.name", "Click Tests")
        write(self.workspace, ".gitignore", "__pycache__/\n*.pyc\n")
        write(self.workspace, "app/__init__.py", "")
        write(self.workspace, "app/settings.py", "SCALE = 3\n")
        write(self.workspace, "app/one.py", """
            from app.settings import SCALE
            def value(): return SCALE
        """)
        write(self.workspace, "app/two.py", """
            from app.settings import SCALE
            def value(): return SCALE * 2
        """)
        write(self.workspace, "tests/test_one.py", """
            import unittest
            from app.one import value
            class One(unittest.TestCase):
                def test_one(self): self.assertEqual(sum(range(50_000_000)), 1_249_999_975_000_000); self.assertEqual(value(), 3)
        """)
        write(self.workspace, "tests/test_two.py", """
            import unittest
            from app.two import value
            class Two(unittest.TestCase):
                def test_two(self): self.assertEqual(sum(range(50_000_000)), 1_249_999_975_000_000); self.assertEqual(value(), 6)
        """)
        git(self.workspace, "add", ".")
        git(self.workspace, "commit", "-qm", "fixture")
        self.command = "python3 -m unittest discover -s tests -q"
        self.change_path = self.workspace / "app/two.py"
        self.change_before = "def value(): return SCALE * 2"
        self.change_after = "def value(): return 2 * SCALE"
        self.expected_shards = 2
        self.expected_tests = 2
        self.expected_reused_tests = 1

    def setup_contract(
        self,
        evidence_id: str,
        kind: str,
        *,
        proposal_digest: str = "",
        outcome: str = "prepare automatic unittest sharding",
    ) -> dict[str, object]:
        contract = self.contract()
        contract["outcome"] = outcome
        contract["boundary"] = {
            "in_scope": [
                "automatic unittest shard setup and validation in this fixture",
                "one approved fixture module change in the successor contract",
            ],
            "out_of_scope": [
                "package installation deployment and the development repository index"
            ],
        }
        contract["must_hold"] = [
            "Preserve user files and the git index",
            "Require a committed policy before baseline validation",
            "Reuse only complete current authoritative observations",
        ]
        if proposal_digest:
            contract["must_hold"].append(
                f"Apply proposal digest {proposal_digest}"
            )
        contract["build"] = {
            "approach": [
                "use the public Click sharding controller and generated policy"
            ],
            "semantics": [
                "run changed shards and reuse only eligible unaffected shards"
            ],
            "order": [
                "approve setup then commit policy then validate then change code"
            ],
        }
        contract["verification"] = {
            "scale": "full",
            "evidence": [
                {
                    "id": evidence_id,
                    "kind": kind,
                    "description": "automatic sharding setup baseline",
                }
            ],
            "done_when": [
                {
                    "condition": "automatic sharding setup is validated",
                    "primary_evidence": evidence_id,
                }
            ],
        }
        contract["plain_language"] = (
            "승인된 자동 샤딩 설정만 준비하고 사용자 파일과 Git 경계를 보존한 뒤 "
            "전체 명령과 생성된 샤드를 검증합니다."
        )
        return contract

    def approve(self, contract: dict[str, object], first: str, second: str) -> None:
        self.arm_gate(first)
        staged = self.stage_gate(contract, first)
        self.assertEqual(
            staged["hookSpecificOutput"]["permissionDecision"], "allow", staged
        )
        self.arm_gate(second)
        passed = self.pass_gate(turn_id=second)
        self.assertEqual(
            passed["hookSpecificOutput"]["permissionDecision"], "allow", passed
        )

    def run_control(self, command: str, turn: str) -> subprocess.CompletedProcess[str]:
        payload = self.pre_tool("Bash", command, turn)
        assert payload is not None
        self.assertEqual(
            payload["hookSpecificOutput"]["permissionDecision"], "allow", payload
        )
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result

    def contract_state(self) -> dict[str, object]:
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        return json.loads(state_path.read_text(encoding="utf-8"))

    def product_patch(self, turn: str) -> None:
        current = self.change_path.read_text(encoding="utf-8")
        self.assertEqual(current.count(self.change_before), 1)
        patch = (
            "*** Begin Patch\n"
            f"*** Update File: {self.change_path}\n"
            "@@\n"
            f"-{self.change_before}\n"
            f"+{self.change_after}\n"
            "*** End Patch"
        )
        tool_id = "phase5-module-change"
        self.assertIsNone(
            self.pre_tool(
                "apply_patch",
                patch,
                turn,
                submit_prompt=False,
                tool_use_id=tool_id,
            )
        )
        self.change_path.write_text(
            current.replace(self.change_before, self.change_after),
            encoding="utf-8",
        )
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": patch},
            turn_id=turn,
            tool_use_id=tool_id,
        )

    def configure_nested_science_project(self) -> None:
        shutil.rmtree(self.workspace / "app")
        shutil.rmtree(self.workspace / "tests")
        write(self.workspace, "science/__init__.py", "")
        write(self.workspace, "science/common.py", "SCALE = 10\nOFFSET = 1\n")
        write(self.workspace, "science/distance.py", """
            from science.common import OFFSET, SCALE
            def convert(value): return value * SCALE + OFFSET
        """)
        write(self.workspace, "science/mass.py", """
            from science.common import OFFSET, SCALE
            def convert(value): return value * SCALE + OFFSET
        """)
        write(self.workspace, "science/time_unit.py", """
            from science.common import OFFSET, SCALE
            def convert(value): return value * SCALE + OFFSET
        """)
        write(self.workspace, "checks/__init__.py", "")
        write(self.workspace, "checks/unit/__init__.py", "")
        write(self.workspace, "checks/unit/case_distance.py", """
            import unittest
            from science.distance import convert
            class DistanceCase(unittest.TestCase):
                def test_distance(self): self.assertEqual(sum(range(50_000_000)), 1_249_999_975_000_000); self.assertEqual(convert(2), 21)
        """)
        write(self.workspace, "checks/unit/case_mass.py", """
            import unittest
            from science.mass import convert
            class MassCase(unittest.TestCase):
                def test_positive_mass(self): self.assertEqual(sum(range(50_000_000)), 1_249_999_975_000_000); self.assertEqual(convert(3), 31)
                def test_zero_mass(self): self.assertEqual(sum(range(50_000_000)), 1_249_999_975_000_000); self.assertEqual(convert(0), 1)
        """)
        write(self.workspace, "checks/unit/case_time.py", """
            import unittest
            from science.time_unit import convert
            class TimeCase(unittest.TestCase):
                def test_time(self): self.assertEqual(sum(range(50_000_000)), 1_249_999_975_000_000); self.assertEqual(convert(4), 41)
        """)
        git(self.workspace, "add", "-A")
        git(self.workspace, "commit", "-qm", "nested science fixture")
        self.command = (
            "python3 -m unittest discover -s checks -t . "
            "-p 'case_*.py' -q"
        )
        self.change_path = self.workspace / "science/mass.py"
        self.change_before = "def convert(value): return value * SCALE + OFFSET"
        self.change_after = "def convert(value): return OFFSET + value * SCALE"
        self.expected_shards = 3
        self.expected_tests = 4
        self.expected_reused_tests = 2

    def reused_test_count(
        self, state: dict[str, object], reused_keys: set[str]
    ) -> int:
        parent_key = CLICK_EVIDENCE.evidence_key(setup.BASELINE_EVIDENCE_ID)
        shard_set = evidence_shards.active_set(state["evidence_state"], parent_key)
        self.assertIsNotNone(shard_set)
        assert shard_set is not None
        shard_ids = {
            child["source_key"]: child["shard_id"]
            for child in shard_set["children"]
        }
        policy = json.loads(
            (self.workspace / ".click/evidence-shards.json").read_text(
                encoding="utf-8"
            )
        )
        entry = policy["entries"][0]
        self.assertEqual(entry["checks"], [shlex.split(self.command)])
        covers = {
            shard["id"]: set(shard["covers"])
            for shard in entry["shards"]
        }
        reused_files = set().union(
            *(covers[shard_ids[key]] for key in reused_keys)
        )
        analyzed = inventory.analyze(
            self.workspace, shlex.split(self.command), cwd=self.workspace
        )
        self.assertEqual(analyzed["status"], "analysis-complete", analyzed)
        self.assertEqual(len(analyzed["inventory"]), self.expected_tests)
        return sum(row["file"] in reused_files for row in analyzed["inventory"])

    def assert_shipped_status_path(self) -> None:
        source = ROOT / "hooks/click_sharding_setup.py"
        shipped = ROOT / "dist/antigravity/hooks/click_sharding_setup.py"
        self.assertEqual(source.read_bytes(), shipped.read_bytes())
        environment = os.environ.copy()
        environment.update(
            PLUGIN_DATA=str(self.plugin_data),
            CLICK_CONFIG_HOME=str(self.plugin_data),
            PYTHONHASHSEED="0",
            PYTHONDONTWRITEBYTECODE="1",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(shipped),
                "status",
                "--project",
                str(self.workspace),
            ],
            cwd=self.workspace,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(json.loads(result.stdout)["status"], "baseline-required")

    def test_evidence_mode_runs_setup_through_baseline_without_guarded_contract(self) -> None:
        self.set_default("evidence", "turn-e0")
        # This scenario owns the Evidence lifecycle, while dedicated cost tests
        # exercise the real startup probe. Keep scheduler noise from selecting
        # the whole-suite branch before the lifecycle assertions begin.
        with mock.patch.object(
            setup,
            "_run_startup_probe",
            return_value={"status": "passed", "duration_ms": 0.0, "reason": ""},
        ):
            generated = self.run_control(
                f"click-gate sharding init -- {self.command}", "turn-e1"
            )
        generated_report = json.loads(generated.stdout)
        self.assertEqual(
            generated_report["status"], "application-ready", generated_report
        )
        self.assertEqual(generated_report["authority_mode"], "evidence")
        self.assertEqual(generated_report["cost_policy"]["strategy"], "sharded")

        applied = self.run_control("click-gate sharding refresh", "turn-e1")
        self.assertEqual(json.loads(applied.stdout)["status"], "commit-required")
        git(self.workspace, "add", *setup.POLICY_PATHS)
        git(self.workspace, "commit", "-qm", "accept Evidence sharding proposal")

        bootstrapped = self.run_control("click-gate sharding refresh", "turn-e1")
        self.assertEqual(
            json.loads(bootstrapped.stdout)["status"], "baseline-required"
        )
        self.run_control("click-gate sharding refresh", "turn-e1")
        final = json.loads(
            self.run_control("click-gate sharding status", "turn-e1").stdout
        )
        self.assertEqual(final["status"], "reuse-ready", final)
        self.assertTrue(final["sharding_ready"])
        self.assertTrue(final["reuse_ready"])
        self.assertEqual(final["reuse_status"], "authoritative-v2")
        self.assertEqual(final["exact_reuse_status"], "available")
        self.assertEqual(final["authoritative_reuse_status"], "available")
        state = self.contract_state()
        batch = CLICK_VERIFICATION.click_incremental.current_batch(
            state["verification"]
        )
        self.assertIsNotNone(batch)
        assert batch is not None
        self.assertEqual(batch["status"], "passed")
        self.assertEqual(len(batch["sources"]), 2)

        write(self.workspace, "app/settings.py", "SCALE = 3\n# source-only commit outside Hook\n")
        git(self.workspace, "add", "app/settings.py")
        git(self.workspace, "commit", "-qm", "source-only edit")
        with mock.patch.dict(os.environ, {
            "PLUGIN_DATA": str(self.plugin_data), "CLICK_CONFIG_HOME": str(self.plugin_data),
        }):
            stale = setup.status(self.workspace, state)
        self.assertEqual(stale["status"], "baseline-required", stale)
        self.assertFalse(stale["reuse_ready"])
        self.run_control("click-gate sharding refresh", "turn-e1")
        refreshed = json.loads(self.run_control("click-gate sharding status", "turn-e1").stdout)
        self.assertEqual(refreshed["status"], "reuse-ready", refreshed)

    def run_setting_free_end_to_end(self) -> dict[str, object]:
        self.assertFalse((self.workspace / ".click").exists())
        self.set_default("guarded", "turn-0")
        status_result = self.run_control("click-gate sharding status", "turn-0")
        self.assertEqual(json.loads(status_result.stdout)["status"], "selection-required")

        denied = self.pre_tool(
            "Bash",
            f"click-gate sharding init -- {self.command}",
            "turn-unapproved",
        )
        assert denied is not None
        self.assertEqual(
            denied["hookSpecificOutput"]["permissionDecision"], "deny"
        )

        self.approve(
            self.setup_contract("E_SETUP", "manual"), "turn-1", "turn-2"
        )
        generated = self.run_control(
            f"click-gate sharding init -- {self.command}", "turn-2"
        )
        generated_report = json.loads(generated.stdout)
        self.assertEqual(generated_report["status"], "approval-required")
        digest = generated_report["proposal_digest"]
        self.complete_evidence("E_SETUP", "turn-2")

        self.approve(
            self.setup_contract(
                setup.BASELINE_EVIDENCE_ID,
                "argv",
                proposal_digest=digest,
                outcome="apply and validate the generated unittest shard policy",
            ),
            "turn-3",
            "turn-4",
        )
        contract_a_id = self.active_contract_id()
        applied = self.run_control("click-gate sharding refresh", "turn-4")
        self.assertEqual(json.loads(applied.stdout)["status"], "commit-required")
        git(self.workspace, "add", *setup.POLICY_PATHS)
        git(self.workspace, "commit", "-qm", "accept Click sharding proposal")

        observer_payload = self.pre_tool(
            "Bash", "click-gate observer authoritative", "turn-4"
        )
        assert observer_payload is not None
        observer_decision = observer_payload["hookSpecificOutput"]
        if (
            observer_decision["permissionDecision"] == "deny"
            and "native CPython 3.12.3 authoritative profile"
            in observer_decision.get("permissionDecisionReason", "")
        ):
            self.skipTest("prepared native authoritative profile required")
        self.assertEqual(
            observer_decision["permissionDecision"], "allow", observer_payload
        )
        observer = self.run_rewritten(observer_payload)
        self.assertEqual(observer.returncode, 0, observer.stderr or observer.stdout)
        self.assertIn("authoritative", observer.stdout)
        bootstrap = self.run_control("click-gate sharding refresh", "turn-4")
        self.assertEqual(
            json.loads(bootstrap.stdout)["status"], "baseline-required"
        )
        self.run_control("click-gate sharding refresh", "turn-4")
        final = self.run_control("click-gate sharding status", "turn-4")
        report = json.loads(final.stdout)
        contract_state = self.contract_state()
        origin_batch = CLICK_VERIFICATION.click_incremental.current_batch(
            contract_state["verification"]
        )
        self.assertIsNotNone(origin_batch)
        assert origin_batch is not None
        self.assertEqual(origin_batch["status"], "passed")
        self.assertEqual(origin_batch["task"]["id"], contract_a_id)
        self.assertEqual(len(origin_batch["sources"]), self.expected_shards)
        origin_candidates = {
            item["source_key"]: {
                "batch_id": origin_batch["batch_id"],
                "execution_status": item["status"],
            }
            for item in origin_batch["sources"]
            if item["status"] in {"passed", "reused"}
            and item["check_digest"]
            == contract_state["evidence_state"]["sources"][item["source_key"]][
                "verified_check_digest"
            ]
        }
        self.assertEqual(len(origin_candidates), self.expected_shards)
        observations = [
            source.get("verified_dependency_observation", {})
            for source in contract_state["evidence_state"]["sources"].values()
        ]
        without_observer = json.loads(json.dumps(contract_state))
        for source in without_observer["evidence_state"]["sources"].values():
            source["verified_dependency_observation"] = {}
            source["verified_dependency_observation_digest"] = ""
        with mock.patch.dict(
            os.environ,
            {
                "PLUGIN_DATA": str(self.plugin_data),
                "CLICK_CONFIG_HOME": str(self.plugin_data),
            },
            clear=False,
        ):
            sharding_only = setup.status(self.workspace, without_observer)
        self.assertEqual(sharding_only["status"], "sharding-ready")
        self.assertTrue(sharding_only["sharding_ready"])
        self.assertTrue(sharding_only["reuse_ready"])
        self.assertEqual(sharding_only["reuse_status"], "exact")
        self.assertEqual(sharding_only["exact_reuse_status"], "available")
        self.assertEqual(sharding_only["policy_reuse_status"], "unavailable")
        self.assertEqual(
            sharding_only["authoritative_reuse_status"], "unavailable"
        )
        self.assertEqual(sharding_only["inventory_status"], "supported")
        self.assertEqual(
            report["status"], "reuse-ready", {"report": report, "observations": observations}
        )
        self.assertTrue(report["sharding_ready"])
        self.assertTrue(report["reuse_ready"])
        self.assertEqual(report["reuse_status"], "authoritative-v2")
        self.assertEqual(report["exact_reuse_status"], "available")
        self.assertEqual(report["authoritative_reuse_status"], "available")

        partial_observer = json.loads(json.dumps(contract_state))
        for source in partial_observer["evidence_state"]["sources"].values():
            if source.get("verified_dependency_observation"):
                source["verified_dependency_observation"] = {}
                source["verified_dependency_observation_digest"] = ""
                break
        with mock.patch.dict(os.environ, {
            "PLUGIN_DATA": str(self.plugin_data),
            "CLICK_CONFIG_HOME": str(self.plugin_data),
        }):
            partial_report = setup.status(self.workspace, partial_observer)
        self.assertEqual(partial_report["status"], "sharding-ready")
        self.assertEqual(partial_report["authoritative_reuse_status"], "partially-available")
        self.assertEqual(partial_report["exact_reuse_status"], "available")

        self.assertEqual(
            contract_state["auto_sharding_setup"]["status"], "reuse-ready"
        )
        self.assertTrue(click_lifecycle.contract_is_completed(contract_state))

        contract_b = self.setup_contract(
            setup.BASELINE_EVIDENCE_ID,
            "argv",
            outcome="revalidate generated shards after one fixture module change",
        )
        self.arm_gate("turn-5")
        staged = self.stage_gate(contract_b, "turn-5")
        self.assertEqual(
            staged["hookSpecificOutput"]["permissionDecision"], "allow", staged
        )
        staged_state = self.contract_state()
        contract_b_id = staged_state["contract_id"]
        self.assertNotEqual(contract_b_id, contract_a_id)
        self.assertTrue(
            staged_state.get("successor_evidence"),
            {
                "origin_completed": click_lifecycle._contract_is_completed(
                    contract_state
                ),
                "origin_sources": len(
                    contract_state["evidence_state"]["sources"]
                ),
                "origin_batches": len(
                    CLICK_VERIFICATION.click_incremental.batch_history(
                        contract_state["verification"]
                    )
                ),
                "origin_candidates": len(origin_candidates),
            },
        )
        self.assertEqual(staged_state["approved_turn_id"], "")
        self.assertEqual(staged_state["verification"]["status"], "ready")
        self.assertNotIn("runner_token", staged_state["verification"])
        self.assertEqual(staged_state["mutation"]["status"], "idle")
        self.assertEqual(staged_state["service"]["status"], "idle")
        self.assertEqual(staged_state["capability_ledger"]["entries"], [])
        baseline_key = CLICK_EVIDENCE.evidence_key(setup.BASELINE_EVIDENCE_ID)
        self.assertEqual(
            staged_state["evidence_state"]["sources"][baseline_key]["status"],
            "ready",
        )
        denied_successor = self.pre_tool(
            "Bash",
            "click-gate sharding refresh",
            "turn-5",
            submit_prompt=False,
        )
        assert denied_successor is not None
        self.assertEqual(
            denied_successor["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )
        self.arm_gate("turn-6")
        passed = self.pass_gate(contract_b_id, "turn-6")
        self.assertEqual(
            passed["hookSpecificOutput"]["permissionDecision"], "allow", passed
        )
        observer_b = self.run_control(
            "click-gate observer authoritative", "turn-6"
        )
        self.assertIn("authoritative", observer_b.stdout)

        self.product_patch("turn-6")
        partial_wall_started = time.perf_counter_ns()
        partial = self.run_control("click-gate sharding refresh", "turn-6")
        partial_external_wall_ms = (
            time.perf_counter_ns() - partial_wall_started
        ) / 1_000_000
        successor_status = self.run_control(
            "click-gate sharding status", "turn-6"
        )
        self.assertEqual(json.loads(successor_status.stdout)["status"], "reuse-ready")
        successor_state = self.contract_state()
        batch = CLICK_VERIFICATION.click_incremental.current_batch(
            successor_state["verification"]
        )
        self.assertIsNotNone(batch)
        assert batch is not None
        self.assertEqual(batch["status"], "passed", batch)
        self.assertEqual(batch["task"]["id"], contract_b_id)
        self.assertEqual(batch["requested_source_count"], self.expected_shards)
        executed = [item for item in batch["sources"] if item["started"]]
        reused = [item for item in batch["sources"] if item["status"] == "reused"]
        self.assertEqual(len(executed), 1, batch)
        self.assertEqual(len(reused), self.expected_shards - 1, batch)
        self.assertTrue(all(item["status"] == "passed" for item in executed))
        for item in reused:
            self.assertEqual(item["decision"], "reuse-dependency", item)
            self.assertEqual(
                item["reason_code"],
                "successor-evidence-dependencies-unchanged",
            )
            self.assertEqual(item["reuse_origin"]["kind"], "successor-contract")
            self.assertEqual(item["reuse_origin"]["contract_id"], contract_a_id)
            self.assertEqual(item["reuse_origin"]["batch_id"], origin_batch["batch_id"])
            baseline = item["duration_baseline"]
            self.assertTrue(
                CLICK_VERIFICATION.click_incremental.timing_baseline_is_valid(
                    baseline
                )
            )
            self.assertEqual(
                baseline["origin_task"],
                {"mode": "guarded", "id": contract_a_id},
            )
            self.assertEqual(baseline["batch_id"], origin_batch["batch_id"])

        savings = CLICK_VERIFICATION.click_incremental.revalidation_savings(batch)
        self.assertTrue(savings["scope_complete"], savings)
        self.assertTrue(savings["timing_complete"], savings)
        self.assertTrue(savings["sequential_comparison_valid"], savings)
        coverage = savings["coverage"]
        self.assertEqual(coverage["actual_executed_source_count"], 1)
        self.assertEqual(
            coverage["actual_reused_source_count"], self.expected_shards - 1
        )
        self.assertEqual(
            coverage["timed_reused_source_count"], self.expected_shards - 1
        )
        self.assertIsNotNone(savings["omitted_test_execution_ms"])
        self.assertGreaterEqual(savings["omitted_test_execution_ms"], 0)
        host = CLICK_VERIFICATION.click_incremental.host_summary(
            successor_state["verification"]
        )
        self.assertIn(host, partial.stdout + partial.stderr)
        projection = dashboard.dashboard_projection(
            successor_state, generated_at=int(time.time())
        )
        self.assertEqual(
            projection["summary"]["revalidation_savings"], savings
        )
        self.assertEqual(
            projection["batch_summaries"][batch["batch_id"]][
                "revalidation_savings"
            ],
            savings,
        )
        self.assertEqual(projection["setup"]["status"], "reuse-ready")
        self.assertTrue(click_lifecycle.contract_is_completed(successor_state))

        reused_keys = {item["source_key"] for item in reused}
        reused_tests = self.reused_test_count(successor_state, reused_keys)
        self.assertEqual(reused_tests, self.expected_reused_tests)

        environment = os.environ.copy()
        environment.update(PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")
        full_started = time.perf_counter_ns()
        full = subprocess.run(
            shlex.split(self.command),
            cwd=self.workspace,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        full_wall_ms = (time.perf_counter_ns() - full_started) / 1_000_000
        self.assertEqual(full.returncode, 0, full.stderr or full.stdout)
        self.assertGreater(full_wall_ms, 0)
        request_wall_ms = batch["request_wall_ms"]
        self.assertIsNotNone(request_wall_ms)
        self.assertGreaterEqual(request_wall_ms, 0)
        net_comparison_ms = full_wall_ms - request_wall_ms
        batch_summary = CLICK_VERIFICATION.click_incremental.batch_summary(batch)
        setup_projection = projection["setup"]

        self.assert_shipped_status_path()
        return {
            "project": self.change_path.parts[-2],
            "parent_argv": shlex.split(self.command),
            "tests": self.expected_tests,
            "shards": self.expected_shards,
            "executed_shards": len(executed),
            "reused_shards": len(reused),
            "full_test_execution_ms": full_wall_ms,
            "partial_test_execution_ms": savings[
                "executed_test_execution_ms"
            ],
            "avoided_test_execution_estimate_ms": savings[
                "omitted_test_execution_ms"
            ],
            "time_baseline_coverage": {
                "timed": coverage["timed_reused_source_count"],
                "reused": coverage["actual_reused_source_count"],
            },
            "test_count_reuse_rate": reused_tests / self.expected_tests,
            "shard_reuse_rate": len(reused) / self.expected_shards,
            "time_reduction_rate": savings["test_execution_reduction_ratio"],
            "click_request_wall_ms": request_wall_ms,
            "partial_external_wall_ms": partial_external_wall_ms,
            "measured_processing_ms": batch_summary[
                "measured_processing_ms"
            ],
            "net_comparison_ms": net_comparison_ms,
            "net_formula": "full_test_execution_ms - click_request_wall_ms",
            "comparison_scope": {
                "full": "same-final-code original parent subprocess wall",
                "partial": savings["aggregation_scope"],
                "request": batch["measurement_scope"],
            },
            "setup": {
                "initial_setup_ms": setup_projection["initial_setup_ms"],
                "observation_ms": setup_projection["observation_ms"],
                "click_processing_ms": setup_projection[
                    "click_processing_ms"
                ],
                "bootstrap_parent_ms": setup_projection[
                    "bootstrap_parent_ms"
                ],
                "bootstrap_shards_ms": setup_projection[
                    "bootstrap_shards_ms"
                ],
                "comparison_net_ms": setup_projection[
                    "comparison_net_ms"
                ],
                "comparison_scope": setup_projection["comparison_scope"],
            },
            "click_management_overhead_ms": None,
            "click_management_overhead_status": "unmeasured",
            "contract_a": contract_a_id,
            "contract_b": contract_b_id,
            "origin_batch": origin_batch["batch_id"],
            "successor_batch": batch["batch_id"],
        }

    def test_library_layout_reuses_one_of_two_generated_shards(self) -> None:
        self.phase5_metrics = self.run_setting_free_end_to_end()

    def test_nested_layout_reuses_two_of_three_generated_shards(self) -> None:
        self.configure_nested_science_project()
        self.phase5_metrics = self.run_setting_free_end_to_end()


if __name__ == "__main__":
    unittest.main()
