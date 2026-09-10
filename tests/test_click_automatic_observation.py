"""Automatic capture tested through the real Evidence hook and runner boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from click_gate_test_support import CLICK_EVIDENCE, ClickGateTestCase
from hooks import click_dependency_cache as dependencies
from hooks import click_observer_runtime as observer
from hooks import click_observer_control as control
from hooks import click_verification_bindings as bindings
from hooks import click_incremental as incremental


class AutomaticPreparationTests(unittest.TestCase):
    def test_diagnostics_and_bounded_failure_keep_their_output_capture(self) -> None:
        self.assertTrue(control.batch_supports_capture({}))
        self.assertTrue(control.batch_supports_capture({"reporting": {"format": "actionable"}}))
        self.assertTrue(control.batch_supports_capture({"failure_collection": {"mode": "bounded"}}))

    def test_unavailable_backend_is_attempted_once_without_blocking(self) -> None:
        verification = {}
        groups = {"source": [{"argv": [sys.executable, "-m", "unittest"]}]}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(observer.inventory, "project_root", return_value=Path(directory)), mock.patch.object(observer, "prepare", side_effect=observer.inventory.AnalysisError("backend-missing")) as prepare:
            observer.prepare_automatic(Path(directory), verification, groups)
            observer.prepare_automatic(Path(directory), verification, groups)
        prepare.assert_called_once()
        self.assertEqual(verification["automatic_observer_attempt"], {
            "status": "unavailable", "reason": "backend-missing",
        })
        self.assertNotIn(observer.STATE_FIELD, verification)

    def test_unsupported_commands_do_not_build_or_change_environment(self) -> None:
        verification = {"observer_control": {"version": 1, "mode": "auto", "updated_at": 0}}
        with mock.patch.object(observer, "prepare") as prepare:
            observer.prepare_automatic(Path.cwd(), verification, {"node": [{"argv": ["node", "--test"]}]})
        prepare.assert_not_called()
        self.assertEqual(bindings.observer_environment({"EXAMPLE": "1"}, verification), {"EXAMPLE": "1"})

    def test_auto_preserves_explicit_environment(self) -> None:
        verification = {
            "observer_control": {"version": 1, "mode": "auto", "updated_at": 0},
            "authoritative_observer": {},
        }
        environment = {"PYTHONHASHSEED": "77", "PYTHONDONTWRITEBYTECODE": "0"}
        self.assertEqual(bindings.observer_environment(environment, verification), environment)

    def test_automatic_provider_rejects_unsigned_or_absent_observations(self) -> None:
        with mock.patch.object(dependencies, "_load_repository", return_value=(Path.cwd(), "", {}, {"test.py"})):
            groups = {"source": [{"argv": [sys.executable, "-m", "unittest"]}]}
            policy = dependencies.observation_policy_bindings(Path.cwd(), groups, git_capture=mock.Mock())
            self.assertEqual(policy, {"source": dependencies.automatic_policy_digest(groups["source"])})
            for only in (False, True):
                self.assertEqual(dependencies.receipts_for_groups(
                    Path.cwd(), groups, authoritative_only=only, git_capture=mock.Mock(),
                ), {})

    def test_owner_manifest_is_not_replaced_by_automatic_policy(self) -> None:
        with mock.patch.object(dependencies, "_load_repository", return_value=(Path.cwd(), "a" * 64, {}, {"test.py"})):
            groups = {"source": [{"argv": [sys.executable, "-m", "unittest"]}]}
            self.assertEqual(dependencies.observation_policy_bindings(Path.cwd(), groups, git_capture=mock.Mock()), {})

    def test_owner_reuse_policy_does_not_trigger_automatic_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".click").mkdir()
            (root / ".click/evidence-reuse.json").write_text("{}")
            verification = {}
            with mock.patch.object(observer.inventory, "project_root", return_value=root), mock.patch.object(observer, "prepare") as prepare:
                observer.prepare_automatic(root, verification, {"source": [{"argv": [sys.executable, "-m", "unittest"]}]})
            prepare.assert_not_called()
            self.assertEqual(verification["automatic_observer_attempt"]["reason"], "owner-reuse-policy-selected")


@unittest.skipUnless(
    sys.platform == "linux" and sys.implementation.name == "cpython" and sys.version_info[:3] == (3, 12, 3),
    "real automatic observation requires the validated Linux CPython 3.12.3 profile",
)
class AutomaticEvidenceTests(ClickGateTestCase):
    maxDiff = None
    def read_state(self) -> dict:
        path = next((self.plugin_data / "gate-state").glob("session-contract-*.json"))
        return json.loads(path.read_text())

    @unittest.skipUnless(shutil.which("node"), "Node unavailable")
    def test_node_auto_candidates_are_bound_once_and_never_dependency_authority(self):
        (self.workspace / "test.cjs").write_text("const {test}=require('node:test'); test('one',()=>console.log('ONLY-ONCE'));\n")
        self.initialize_git("test.cjs")
        command = ["node", "--test", "test.cjs"]
        first = self.verify_gate([command], "turn-1", evidence_ids=["NODE"])
        run = self.run_rewritten(first)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(run.stdout.count("ONLY-ONCE"), 1)
        state = self.read_state()
        key = CLICK_EVIDENCE.evidence_key("NODE")
        record = state["verification"]["framework_observations"][key]
        self.assertFalse(record["reuse_authorized"])
        self.assertEqual(state["evidence_state"]["sources"][key]["verified_dependency_provider"], "")
        self.patch_file("test.cjs", "'one'", "'changed'", "turn-2")
        second = self.verify_gate([command], "turn-2", evidence_ids=["NODE"])
        run = self.run_rewritten(second)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(run.stdout.count("ONLY-ONCE"), 1)
        self.assertNotIn("[Click framework observer]", run.stdout)

    def fixture(self, *, child_process: bool = False) -> list[list[str]]:
        (self.workspace / ".gitignore").write_text("__pycache__/\nlocal.cfg\nlocal-data/\n")
        (self.workspace / "local-data").mkdir()
        (self.workspace / "local.cfg").write_text("ready")
        (self.workspace / "shared.cfg").write_text("ready")
        for name in ("alpha", "beta"):
            body = (
                "import unittest\nfrom pathlib import Path\nVALUE = 1\n"
                "class Check(unittest.TestCase):\n"
                "    def test_value(self):\n"
                f"        print('ran-{name}')\n"
                "        self.assertGreater(VALUE, 0)\n"
                "        self.assertTrue(Path('shared.cfg').read_text())\n"
            )
            if name == "alpha":
                body += "        self.assertTrue(Path('local.cfg').read_text())\n"
                body += "        if Path('local-data/optional.cfg').exists():\n            self.assertTrue(Path('local-data/optional.cfg').read_text())\n"
            if name == "beta" and child_process:
                body += "        import subprocess, sys\n        subprocess.run([sys.executable, '-c', 'pass'], check=True)\n"
            (self.workspace / f"{name}.py").write_text(body)
        self.initialize_git(".gitignore", "alpha.py", "beta.py", "shared.cfg")
        return [[sys.executable, "-m", "unittest", f"{name}.Check"] for name in ("alpha", "beta")]

    def run_checks(self, commands: list[list[str]], turn: str) -> dict:
        evidence_ids = [command[-1].split(".")[0].upper() for command in commands]
        payload = self.verify_gate(commands, turn, evidence_ids=evidence_ids)
        self.assertIn("updatedInput", payload["hookSpecificOutput"], payload)
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = self.read_state()
        decisions = self.decisions(state)
        for name in ("alpha", "beta"):
            source_key = CLICK_EVIDENCE.evidence_key(name.upper())
            executed = decisions[source_key] in {"run", "not-evaluable"}
            self.assertEqual(result.stdout.count(f"ran-{name}"), int(executed), result.stdout)
            observation = state["evidence_state"]["sources"][source_key].get("verified_dependency_observation")
            if dependencies.authoritative_dependency_observation_is_complete(observation):
                self.assertEqual(observation["binding"]["evidence_key"], source_key, observation)
                project_inputs = {row["path"] for row in observation["inputs"] if row["root"] == "project"}
                self.assertIn(name + ".py", project_inputs, observation)
                self.assertEqual("local.cfg" in project_inputs, name == "alpha", observation)
        return state

    def patch_file(self, name: str, old: str, new: str, turn: str) -> None:
        path = self.workspace / name
        patch = f"*** Begin Patch\n*** Update File: {path}\n@@\n-{old}\n+{new}\n*** End Patch"
        self.pre_tool("apply_patch", patch, turn, tool_use_id=turn + name)
        path.write_text(path.read_text().replace(old, new))
        self.tool_hook("post-tool", "apply_patch", {"patch": patch}, turn_id=turn, tool_use_id=turn + name)

    def decisions(self, state: dict) -> dict[str, str]:
        return {row["source_key"]: row["decision"] for row in state["verification"]["incremental_plan"]["decisions"]}

    def assert_decisions(self, state: dict, expected: dict[str, str]) -> None:
        # Keep ownership evidence in a failing CI assertion; TemporaryDirectory
        # cleanup otherwise destroys the state needed to diagnose a mismatch.
        details = {}
        for key, source in state["evidence_state"]["sources"].items():
            observation = source.get("verified_dependency_observation") or {}
            binding = observation.get("binding", {})
            details[key] = {
                "provider": source.get("verified_dependency_provider"),
                "required": source.get("automatic_observation_required"),
                "bound_key": binding.get("evidence_key"),
                "project_inputs": [row for row in observation.get("inputs", [])
                                   if row.get("root") == "project"],
            }
        self.assertEqual(self.decisions(state), expected, json.dumps(details, sort_keys=True))

    def test_no_policy_reuses_unaffected_child_and_tracks_ignored_shared_inputs(self) -> None:
        commands = self.fixture()
        initial = self.run_checks(commands, "turn-1")
        alpha, beta = [CLICK_EVIDENCE.evidence_key(name) for name in ("ALPHA", "BETA")]
        self.assertEqual(initial["status"], "evidence")
        self.assertEqual(initial["contract_id"], "")
        for source in initial["evidence_state"]["sources"].values():
            self.assertEqual(source["verified_dependency_provider"], dependencies.AUTOMATIC_PROVIDER_NAME)
            self.assertTrue(dependencies.authoritative_dependency_observation_is_complete(source["verified_dependency_observation"]), source)
            self.assertEqual(source["dependency_patterns"], [])
        self.assertFalse((self.workspace / ".click").exists())

        self.patch_file("alpha.py", "VALUE = 1", "VALUE = 2", "turn-2")
        changed = self.run_checks(commands, "turn-2")
        self.assert_decisions(changed, {alpha: "run", beta: "reuse-dependency"})
        self.assertNotEqual(initial["evidence_session_id"], changed["evidence_session_id"])

        self.patch_file("local.cfg", "ready", "changed", "turn-3")
        ignored = self.run_checks(commands, "turn-3")
        self.assert_decisions(ignored, {alpha: "run", beta: "reuse-exact"})

        # An unreported ignored input can change without changing Git or a Hook
        # revision. The final exact-reuse boundary still catches it.
        (self.workspace / "local-data/optional.cfg").write_text("present")
        appeared = self.run_checks(commands, "turn-3")
        self.assert_decisions(appeared, {alpha: "run", beta: "reuse-exact"})

        self.patch_file("shared.cfg", "ready", "changed", "turn-4")
        shared = self.run_checks(commands, "turn-4")
        self.assert_decisions(shared, {alpha: "run", beta: "run"})
        parent = subprocess.run([sys.executable, "-m", "unittest", "alpha", "beta"], cwd=self.workspace, capture_output=True, text=True)
        self.assertEqual(parent.returncode, 0, parent.stderr)
        self.assertIn("Ran 2 tests", parent.stderr)

    def test_incomplete_worker_does_not_disable_unaffected_child_reuse(self) -> None:
        commands = self.fixture(child_process=True)
        first = self.run_checks(commands, "turn-1")
        alpha, beta = [CLICK_EVIDENCE.evidence_key(name) for name in ("ALPHA", "BETA")]
        self.assertEqual(first["evidence_state"]["sources"][beta]["verified_dependency_provider"], "")
        self.patch_file("beta.py", "VALUE = 1", "VALUE = 2", "turn-2")
        second = self.run_checks(commands, "turn-2")
        self.assertEqual(self.decisions(second), {alpha: "reuse-dependency", beta: "run"})

    def test_request_order_does_not_exchange_child_input_ownership(self) -> None:
        commands = self.fixture()
        beta_path = self.workspace / "beta.py"
        beta_path.write_text(beta_path.read_text() +
            "        self.assertTrue(Path('local-data/beta.cfg').read_text())\n")
        (self.workspace / "local-data/beta.cfg").write_text("ready")
        self.initialize_git("beta.py")
        first = self.run_checks(list(reversed(commands)), "turn-1")
        alpha, beta = [CLICK_EVIDENCE.evidence_key(name) for name in ("ALPHA", "BETA")]
        self.assertEqual(self.decisions(first), {alpha: "run", beta: "run"})
        for key in (alpha, beta):
            source = first["evidence_state"]["sources"][key]
            self.assertTrue(dependencies.authoritative_dependency_observation_is_complete(
                source.get("verified_dependency_observation")), source)
        (self.workspace / "local-data/beta.cfg").write_text("changed")
        changed_beta = self.run_checks(commands, "turn-1")
        self.assert_decisions(changed_beta, {alpha: "reuse-exact", beta: "run"})
        (self.workspace / "local.cfg").write_text("changed")
        changed_alpha = self.run_checks(list(reversed(commands)), "turn-1")
        self.assert_decisions(changed_alpha, {alpha: "run", beta: "reuse-exact"})

    def test_sibling_receipt_cannot_authorize_exact_child_reuse(self) -> None:
        commands = self.fixture()
        state = self.run_checks(commands, "turn-1")
        alpha, beta = [CLICK_EVIDENCE.evidence_key(name) for name in ("ALPHA", "BETA")]
        sources = state["evidence_state"]["sources"]
        for key in (alpha, beta):
            self.assertTrue(dependencies.authoritative_dependency_observation_is_complete(
                sources[key].get("verified_dependency_observation")), sources[key])

        # Model a child-association error in stored input evidence while leaving
        # alpha's exact successful execution bindings intact. Beta's receipt
        # lacks local.cfg, but cannot satisfy alpha's key/check binding.
        for field, value in sources[beta].items():
            if field.startswith("verified_dependency_"):
                sources[alpha][field] = value
        path = next((self.plugin_data / "gate-state").glob("session-contract-*.json"))
        path.write_text(json.dumps(state))
        (self.workspace / "local.cfg").write_text("changed-with-wrong-child-receipt")
        recovered = self.run_checks(commands, "turn-1")
        self.assert_decisions(recovered, {alpha: "run", beta: "reuse-exact"})
        self.assertEqual(recovered["verification"]["status"], "passed")

    def test_running_child_cannot_leave_changed_reused_inputs_current(self) -> None:
        commands = self.fixture()
        beta_path = self.workspace / "beta.py"
        beta_path.write_text(beta_path.read_text() +
            "        if Path('local-data/mutate.flag').exists():\n"
            "            Path('local.cfg').write_text('changed-by-beta')\n")
        self.initialize_git("beta.py")
        self.run_checks(commands, "turn-1")
        alpha, beta = [CLICK_EVIDENCE.evidence_key(name) for name in ("ALPHA", "BETA")]

        # Git and the Hook revision stay unchanged. Only beta initially needs
        # execution, but that execution invalidates alpha's reused input.
        (self.workspace / "local-data/mutate.flag").write_text("yes")
        payload = self.verify_gate(commands, "turn-1", evidence_ids=["ALPHA", "BETA"])
        self.assertEqual(self.decisions(self.read_state()), {alpha: "reuse-exact", beta: "run"})
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("ran-alpha"), 0, result.stdout)
        self.assertEqual(result.stdout.count("ran-beta"), 1, result.stdout)
        state = self.read_state()
        self.assertEqual(state["verification"]["status"], "ready")
        self.assertEqual(state["evidence_state"]["sources"][alpha]["status"], "ready")
        self.assertEqual(state["evidence_state"]["sources"][alpha]["verified_revision"], -1)
        self.assertEqual(state["evidence_state"]["sources"][beta]["status"], "passed")
        measured = incremental.current_batch(state["verification"])
        measured_alpha = next(row for row in measured["sources"] if row["source_key"] == alpha)
        self.assertEqual(measured_alpha["status"], "not-run")
        self.assertEqual(measured_alpha["execution_reason_code"], "observed-input-changed")
        self.assertIn("Inputs of previously reused checks changed", result.stderr)

        # Recovery stays at the affected child; beta's completed execution is
        # retained and neither a parent fallback nor an automatic retry runs.
        recovered = self.run_checks(commands, "turn-1")
        self.assertEqual(self.decisions(recovered), {alpha: "run", beta: "reuse-exact"})
        self.assertEqual(recovered["verification"]["status"], "passed")

    def test_reused_input_change_after_planning_is_caught_at_completion(self) -> None:
        commands = self.fixture()
        self.run_checks(commands, "turn-1")
        alpha, beta = [CLICK_EVIDENCE.evidence_key(name) for name in ("ALPHA", "BETA")]
        self.patch_file("beta.py", "VALUE = 1", "VALUE = 2", "turn-2")
        payload = self.verify_gate(commands, "turn-2", evidence_ids=["ALPHA", "BETA"])
        self.assertEqual(self.decisions(self.read_state()), {alpha: "reuse-dependency", beta: "run"})
        (self.workspace / "local.cfg").write_text("changed-after-plan")
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("ran-alpha"), 0, result.stdout)
        self.assertEqual(result.stdout.count("ran-beta"), 1, result.stdout)
        state = self.read_state()
        self.assertEqual(state["verification"]["status"], "ready")
        self.assertEqual(state["evidence_state"]["sources"][alpha]["status"], "ready")
        self.assertEqual(state["evidence_state"]["sources"][beta]["status"], "passed")

    def test_capture_loss_does_not_downgrade_to_git_only_reuse(self) -> None:
        commands = self.fixture()
        self.run_checks(commands, "turn-1")
        alpha, beta = [CLICK_EVIDENCE.evidence_key(name) for name in ("ALPHA", "BETA")]
        original = "        self.assertGreater(VALUE, 0)"
        with_clock = original + "; __import__('time').time()"
        self.patch_file("alpha.py", original, with_clock, "turn-2")
        incomplete = self.run_checks(commands, "turn-2")
        self.assertEqual(self.decisions(incomplete), {alpha: "run", beta: "reuse-dependency"})
        self.assertEqual(incomplete["evidence_state"]["sources"][alpha]["verified_dependency_provider"], "")

        self.patch_file("local.cfg", "ready", "changed", "turn-3")
        changed = self.run_checks(commands, "turn-3")
        self.assertEqual(self.decisions(changed), {alpha: "run", beta: "reuse-exact"})
        again = self.run_checks(commands, "turn-3")
        self.assertEqual(self.decisions(again), {alpha: "run", beta: "reuse-exact"})

        # Once complete capture is possible again, normal child reuse recovers.
        self.patch_file("alpha.py", with_clock, original, "turn-4")
        self.run_checks(commands, "turn-4")
        restored = self.run_checks(commands, "turn-4")
        self.assertEqual(self.decisions(restored), {alpha: "reuse-exact", beta: "reuse-exact"})

    def test_unavailable_runtime_keeps_the_previous_child_input_requirement(self) -> None:
        commands = self.fixture()
        first = self.run_checks(commands, "turn-1")
        alpha, beta = [CLICK_EVIDENCE.evidence_key(name) for name in ("ALPHA", "BETA")]
        self.patch_file("alpha.py", "VALUE = 1", "VALUE = 2", "turn-2")
        payload = self.verify_gate(commands, "turn-2", evidence_ids=["ALPHA", "BETA"])
        path = next((self.plugin_data / "gate-state").glob("session-contract-*.json"))
        planned = self.read_state()
        # Fault-inject only this fixture's capability. Never remove or change
        # the shared native artifact used by other sessions or test processes.
        planned["verification"][observer.STATE_FIELD]["artifact_digest"] = "0" * 64
        path.write_text(json.dumps(planned))
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        unavailable = self.read_state()
        source = unavailable["evidence_state"]["sources"][alpha]
        self.assertEqual(source["verified_dependency_provider"], "")
        self.assertTrue(source["automatic_observation_required"])
        unavailable["verification"][observer.STATE_FIELD] = first["verification"][observer.STATE_FIELD]
        path.write_text(json.dumps(unavailable))
        (self.workspace / "local.cfg").write_text("changed-while-unobserved")
        recovered = self.run_checks(commands, "turn-2")
        self.assertEqual(self.decisions(recovered)[alpha], "run")
        self.assertEqual(recovered["verification"]["status"], "passed")
        repeated = self.run_checks(commands, "turn-2")
        self.assertEqual(self.decisions(repeated), {alpha: "reuse-exact", beta: "reuse-exact"})

    def test_explicit_python_environment_keeps_original_execution(self) -> None:
        commands = self.fixture()
        with mock.patch.dict(os.environ, {"PYTHONHASHSEED": "77"}):
            first = self.run_checks(commands, "turn-1")
        self.assertEqual(first["verification"]["automatic_observer_attempt"]["reason"], "deterministic-environment-required")
        self.assertNotIn(observer.STATE_FIELD, first["verification"])
        for source in first["evidence_state"]["sources"].values():
            self.assertEqual(source["verified_dependency_provider"], "")

    def test_switching_off_does_not_delete_another_sessions_cached_companion(self) -> None:
        first = self.run_checks(self.fixture(), "turn-1")
        runtime = first["verification"][observer.STATE_FIELD]
        artifact = observer.artifact_path(runtime)
        self.assertTrue(artifact.is_file())
        payload = self.pre_tool("Bash", "click-gate observer off", "turn-2")
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(observer.STATE_FIELD, self.read_state()["verification"])
        self.assertTrue(artifact.is_file())
        self.assertIsNotNone(observer.validate(self.workspace, runtime))


if __name__ == "__main__":
    unittest.main()
