from __future__ import annotations

import _thread
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from click_gate_test_support import (
    CLICK_EVIDENCE,
    CLICK_GATE,
    CLICK_VERIFICATION,
    ClickGateTestCase,
    split_runner_command,
)
from hooks import click_authoritative_observer as authoritative
from hooks import click_dependency_cache as dependency_cache
from hooks import click_observation_inputs as observation_inputs
from hooks import click_observer_runtime as observer_runtime
from hooks import click_test_inventory as inventory
from hooks import click_shard_proposal as proposal


ROOT = Path(__file__).resolve().parents[1]
SUPPORTED = (
    sys.platform in {"linux", "darwin", "win32"}
    and sys.implementation.name == "cpython"
    and sys.version_info[:3] == (3, 12, 3)
)


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(SUPPORTED, "authoritative CPython 3.12.3 native profile")
class AuthoritativeObserverRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build = observer_runtime.prepare(ROOT)
        cls.runtime = observer_runtime.control_state(cls.build)

    # The content-addressed companion is shared with other test processes and
    # live sessions. This class owns its fixtures, not the cached runtime.

    def environment(self) -> dict[str, str]:
        value = {
            key: item
            for key, item in os.environ.items()
            if key not in {
                "LD_PRELOAD",
                "DYLD_FORCE_FLAT_NAMESPACE",
                "DYLD_INSERT_LIBRARIES",
                "CLICK_NATIVE_OBSERVER_BOOTSTRAP",
                "CLICK_NATIVE_OBSERVER_CHANNEL",
                "CLICK_NATIVE_OBSERVER_HANDLE",
                "CLICK_NATIVE_OBSERVER_ORIGINAL_PYTHONPATH",
                "CLICK_NATIVE_OBSERVER_PYTHONPATH_PRESENT",
                "CLICK_NATIVE_OBSERVER_ROOT",
            }
        }
        value.update(PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")
        return value

    def context(self) -> dict[str, object]:
        value: dict[str, object] = {
            field: hashlib.sha256(field.encode()).hexdigest()
            for field in authoritative.FULL_BINDING_FIELDS
        }
        value["mutation_revision"] = 0
        return value

    def run_body(
        self,
        body: str,
        *,
        capture_limit: int = authoritative.MAX_AUTHORITATIVE_CAPTURE_BYTES,
        resolve_backend=None,
        fallback=None,
    ):
        temporary = tempfile.TemporaryDirectory(prefix="click-authoritative-test-")
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name) / "project"
        (project / "tests").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        indented = "".join(f"        {line}\n" for line in body.splitlines())
        (project / "tests" / "test_example.py").write_text(
            "import unittest\n"
            "class Example(unittest.TestCase):\n"
            "    def test_case(self):\n"
            + indented,
            encoding="utf-8",
        )
        argv = [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-q",
        ]
        environment = self.environment()
        fallback = fallback or mock.Mock(
            side_effect=lambda: subprocess.run(
                argv, cwd=project, env=environment, check=False
            ).returncode
        )
        resolve_backend = resolve_backend or (
            lambda name, workspace: (
                str(inventory.trusted_executable(name, workspace)),
                "",
            )
        )
        original_records = observation_inputs.InputSnapshot.records

        def diagnosed_records(snapshot, inputs):
            try:
                return original_records(snapshot, inputs)
            except observation_inputs.InputError as error:
                failures = []
                for path, operations in sorted(inputs.items()):
                    try:
                        original_records(snapshot, {path: operations})
                    except Exception as item_error:  # pragma: no branch - failure aid
                        failures.append(f"{path}: {item_error}")
                        if len(failures) == 8:
                            break
                raise AssertionError(
                    f"authoritative input snapshot failed ({error}); "
                    f"individual failures: {failures}"
                ) from error

        with mock.patch.object(
            observation_inputs.InputSnapshot,
            "records",
            diagnosed_records,
        ):
            result = authoritative.run_command(
                argv,
                workspace=project,
                observation_root=project,
                environment=environment,
                binding_context=self.context(),
                runtime=self.runtime,
                runner_token=self.id(),
                execute_unobserved=fallback,
                resolve_backend=resolve_backend,
                digest_file=file_digest,
                capture_limit=capture_limit,
            )
        return project, result, fallback

    def current_binding(self, observation: dict) -> dict:
        return {
            field: observation["binding"][field]
            for field in dependency_cache.AUTHORITATIVE_CURRENT_BINDING_FIELDS
        }

    def test_real_backend_issues_a_current_content_bound_observation(self) -> None:
        project, result, fallback = self.run_body("self.assertEqual(2 + 2, 4)")
        observation = result.envelope["observation"]
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            observation["status"],
            "complete",
            observation["ineligibility_reasons"],
        )
        self.assertEqual(observation["profile"], self.runtime["profile"])
        self.assertIn("tests/test_example.py", observation["paths"])
        if sys.platform == "linux":
            self.assertGreater(len(observation["inputs"]), 20)
        else:
            self.assertGreater(len(observation["inputs"]), 0)
        fallback.assert_not_called()
        self.assertIsNotNone(
            authoritative.verified_observation(
                result.envelope,
                secret=self.id(),
                expected_binding=self.context(),
            )
        )
        self.assertTrue(
            dependency_cache.authoritative_dependency_observation_matches(
                observation,
                project=project,
                runtime=self.runtime,
                binding=self.current_binding(observation),
            )
        )

    def test_new_import_candidate_and_binding_change_invalidate_reuse(self) -> None:
        project, result, _ = self.run_body(
            "try:\n"
            "    import optional_click_feature\n"
            "except ModuleNotFoundError:\n"
            "    pass\n"
            "self.assertTrue(True)"
        )
        observation = result.envelope["observation"]
        self.assertEqual(
            observation["status"],
            "complete",
            observation["ineligibility_reasons"],
        )
        binding = self.current_binding(observation)
        self.assertTrue(
            dependency_cache.authoritative_dependency_observation_matches(
                observation, project=project, runtime=self.runtime, binding=binding
            )
        )
        (project / "optional_click_feature.py").write_text("VALUE = 1\n")
        self.assertFalse(
            dependency_cache.authoritative_dependency_observation_matches(
                observation, project=project, runtime=self.runtime, binding=binding
            )
        )
        changed_environment = dict(binding)
        changed_environment["environment_digest"] = "f" * 64
        self.assertFalse(
            dependency_cache.authoritative_dependency_observation_matches(
                observation,
                project=project,
                runtime=self.runtime,
                binding=changed_environment,
            )
        )

    def test_time_child_and_event_loss_are_non_reusable_without_a_retry(self) -> None:
        # Followed child processes and threads leave every file input
        # snapshotted, so they downgrade to an explicitly conditional receipt.
        # Time, random, network and capture loss remain non-reusable.
        cases = (
            (
                "import time\nself.assertGreater(time.time(), 0)",
                {"time-random-input"},
                {},
                "failed",
            ),
            (
                "import subprocess, sys\n"
                "subprocess.run([sys.executable, '-c', 'pass'], check=True)",
                {"child-process-unsupported"},
                {},
                "conditional",
            ),
            (
                "import socket\nchannel = socket.socket()\nchannel.close()",
                {"external-or-native-input"},
                {},
                "failed",
            ),
            (
                "import threading\n"
                "worker = threading.Thread(target=lambda: None)\n"
                "worker.start()\nworker.join()",
                {"concurrent-execution-unsupported"},
                {},
                "conditional",
            ),
            (
                "self.assertTrue(True)",
                {"capture-failed", "event-loss", "unresolved-event"},
                {"capture_limit": 1},
                "failed",
            ),
        )
        for body, expected_reasons, options, expected_status in cases:
            with self.subTest(expected_reasons=expected_reasons):
                _, result, fallback = self.run_body(body, **options)
                observation = result.envelope["observation"]
                self.assertEqual(result.exit_code, 0)
                self.assertEqual(observation["status"], expected_status, observation["ineligibility_reasons"])
                if expected_status == "conditional":
                    self.assertTrue(observation["process_tree_complete"])
                    self.assertTrue(observation["inputs"])
                    self.assertTrue(dependency_cache.authoritative_dependency_observation_is_conditional(observation))
                    self.assertFalse(dependency_cache.authoritative_dependency_observation_is_complete(observation))
                    self.assertTrue(dependency_cache.bound_dependency_observation_is_reusable(observation))
                    self.assertEqual(dependency_cache.conditional_authority_source(observation), "conditional-python-observation")
                self.assertTrue(
                    expected_reasons.intersection(
                        observation["ineligibility_reasons"]
                    ),
                    observation["ineligibility_reasons"],
                )
                fallback.assert_not_called()

        timers: list[threading.Timer] = []

        def interrupt_main() -> None:
            if os.name == "nt":
                _thread.interrupt_main()
            else:
                os.kill(os.getpid(), signal.SIGINT)

        def interrupt_started_target() -> None:
            timer = threading.Timer(0.1, interrupt_main)
            timer.start()
            timers.append(timer)

        interrupted_started = time.monotonic()
        with mock.patch.object(
            authoritative.click_process,
            "target_started",
            side_effect=interrupt_started_target,
        ):
            _, interrupted, fallback = self.run_body(
                "import time\ntime.sleep(15)"
            )
        interrupted_elapsed = time.monotonic() - interrupted_started
        for timer in timers:
            timer.join(timeout=1)
        interrupted_observation = interrupted.envelope["observation"]
        self.assertEqual(interrupted.exit_code, 130)
        self.assertEqual(interrupted_observation["status"], "failed")
        fallback.assert_not_called()
        if os.name != "nt":
            self.assertLess(interrupted_elapsed, 10.0)

        _, recovered, fallback = self.run_body("self.assertTrue(True)")
        self.assertEqual(recovered.exit_code, 0)
        self.assertEqual(
            recovered.envelope["observation"]["status"],
            "complete",
            recovered.envelope["observation"]["ineligibility_reasons"],
        )
        fallback.assert_not_called()

    def test_unavailable_backend_executes_the_original_command_once(self) -> None:
        fallback = mock.Mock(return_value=0)
        _, result, fallback = self.run_body(
            "self.assertTrue(True)",
            resolve_backend=lambda name, workspace: (None, "unavailable"),
            fallback=fallback,
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(fallback.call_count, 1)
        self.assertEqual(
            result.envelope["observation"]["ineligibility_reasons"],
            ["backend-unavailable"],
        )

    def test_input_records_cover_content_membership_missing_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="click-input-records-") as raw:
            project = Path(raw) / "project"
            directory = project / "data"
            directory.mkdir(parents=True)
            target = project / "target.txt"
            target.write_text("one\n")
            link = project / "current.txt"
            try:
                link.symlink_to("target.txt")
            except OSError:
                link = None
            missing = project / "optional.txt"

            def receipt(path: Path, operations: set[str]) -> list[dict]:
                snapshot = observation_inputs.InputSnapshot(
                    project,
                    self.runtime["artifact_id"],
                    profile=self.runtime["profile"],
                )
                return snapshot.records({str(path): operations})

            alias = Path(raw) / "project-alias"
            try:
                alias.symlink_to(project, target_is_directory=True)
            except OSError:
                alias = None
            if alias is not None:
                snapshot = observation_inputs.InputSnapshot(
                    project,
                    self.runtime["artifact_id"],
                    profile=self.runtime["profile"],
                )
                aliased = snapshot.records(
                    {
                        str(target): {"metadata"},
                        str(alias / target.name): {"execute"},
                    }
                )
                target_rows = [
                    row
                    for row in aliased
                    if row["root"] == "project" and row["path"] == target.name
                ]
                self.assertEqual(len(target_rows), 1)
                self.assertEqual(
                    target_rows[0]["operations"], ["execute", "metadata"]
                )
                self.assertTrue(observation_inputs.records_valid(aliased))

            content = receipt(target, {"read"})
            self.assertTrue(
                observation_inputs.records_current(
                    project,
                    self.runtime["artifact_id"],
                    content,
                    profile=self.runtime["profile"],
                )
            )
            target.write_text("two\n")
            self.assertFalse(
                observation_inputs.records_current(
                    project,
                    self.runtime["artifact_id"],
                    content,
                    profile=self.runtime["profile"],
                )
            )

            membership = receipt(directory, {"enumerate"})
            created = directory / "created.txt"
            created.write_text("new\n")
            self.assertFalse(
                observation_inputs.records_current(
                    project,
                    self.runtime["artifact_id"],
                    membership,
                    profile=self.runtime["profile"],
                )
            )

            deletion = receipt(directory, {"enumerate"})
            created.unlink()
            self.assertFalse(
                observation_inputs.records_current(
                    project,
                    self.runtime["artifact_id"],
                    deletion,
                    profile=self.runtime["profile"],
                )
            )

            renamed = directory / "before.txt"
            renamed.write_text("rename\n")
            rename_snapshot = receipt(directory, {"enumerate"})
            renamed.rename(directory / "after.txt")
            self.assertFalse(
                observation_inputs.records_current(
                    project,
                    self.runtime["artifact_id"],
                    rename_snapshot,
                    profile=self.runtime["profile"],
                )
            )

            absent = receipt(missing, {"metadata"})
            missing.write_text("appeared\n")
            self.assertFalse(
                observation_inputs.records_current(
                    project,
                    self.runtime["artifact_id"],
                    absent,
                    profile=self.runtime["profile"],
                )
            )

            if link is not None:
                linked = receipt(link, {"read"})
                replacement = project / "replacement.txt"
                replacement.write_text("replacement\n")
                link.unlink()
                link.symlink_to("replacement.txt")
                self.assertFalse(
                    observation_inputs.records_current(
                        project,
                        self.runtime["artifact_id"],
                        linked,
                        profile=self.runtime["profile"],
                    )
                )


@unittest.skipUnless(SUPPORTED, "authoritative CPython 3.12.3 native profile")
class AuthoritativeCrossContractReuseTests(ClickGateTestCase):
    def setUp(self) -> None:
        super().setUp()
        environment = mock.patch.dict(
            os.environ,
            {"PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        environment.start()
        self.addCleanup(environment.stop)

    def contract_for_shards(self, outcome: str) -> dict:
        contract = self.contract()
        contract["outcome"] = outcome
        contract["verification"] = {
            "scale": "focused",
            "evidence": [
                {
                    "id": "E_ALPHA",
                    "kind": "argv",
                    "description": "alpha shard",
                    "dependencies": ["alpha.py", "shared.cfg", "uv.lock"],
                },
                {
                    "id": "E_BETA",
                    "kind": "argv",
                    "description": "beta shard",
                    "dependencies": ["beta.py", "shared.cfg", "uv.lock"],
                },
            ],
            "done_when": [
                {"condition": "alpha passes", "primary_evidence": "E_ALPHA"},
                {"condition": "beta passes", "primary_evidence": "E_BETA"},
            ],
        }
        return contract

    def enable_authoritative(self, turn_id: str) -> dict:
        payload = self.pre_tool(
            "Bash",
            "click-gate observer authoritative",
            turn_id,
            submit_prompt=False,
        )
        assert payload is not None
        result = self.run_rewritten(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text())
        runtime = observer_runtime.state_from_verification(state["verification"])
        self.assertIsNotNone(runtime)
        assert runtime is not None
        # Do not delete a cached companion another process may still be using.
        return state

    def mark_patch(self, path: Path, old: str, new: str, tool_id: str) -> None:
        patch = f"*** Begin Patch\n*** Update File: {path}\n@@\n-{old}\n+{new}\n*** End Patch"
        self.assertIsNone(
            self.pre_tool(
                "apply_patch",
                patch,
                "turn-4",
                submit_prompt=False,
                tool_use_id=tool_id,
            )
        )
        path.write_text(path.read_text().replace(old, new))
        self.tool_hook(
            "post-tool",
            "apply_patch",
            {"patch": patch},
            turn_id="turn-4",
            tool_use_id=tool_id,
        )

    def test_complete_plan_refresh_requalifies_unaffected_automatic_child(self) -> None:
        (self.workspace / ".gitignore").write_text("__pycache__/\n")
        (self.workspace / "app").mkdir()
        (self.workspace / "tests").mkdir()
        (self.workspace / "app/__init__.py").write_text("")
        for name in ("alpha", "beta"):
            (self.workspace / f"app/{name}.py").write_text("VALUE = 1\n")
            (self.workspace / f"tests/test_{name}.py").write_text(
                f"import unittest\nfrom app.{name} import VALUE\n"
                "class Case(unittest.TestCase):\n"
                "    def test_value(self): self.assertGreater(VALUE, 0)\n")
        parent = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]
        self.initialize_git(".gitignore", "app", "tests")
        generated = proposal.propose(self.workspace, parent)
        self.assertTrue(generated["proposal_ready"], generated["reasons"])
        for path, value in generated["proposals"].items():
            target = self.workspace / path
            target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps(value))
        subprocess.run(["git", "add", ".click"], cwd=self.workspace, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Click Tests",
                        "-c", "user.email=click-tests@example.invalid", "commit", "-qm", "accept automatic child plan"],
                       cwd=self.workspace, check=True, capture_output=True)
        self.approve_contract()
        self.enable_authoritative("turn-2")
        first = self.run_rewritten(self.verify_gate([parent]))
        self.assertEqual(first.returncode, 0, first.stderr)
        state_path = next((self.plugin_data / "gate-state").glob("session-contract-*.json"))
        baseline = json.loads(state_path.read_text())
        old_sources = baseline["evidence_state"]["sources"]
        for source in old_sources.values():
            observation = source["verified_dependency_observation"]
            self.assertEqual(observation["status"], "complete", {
                "source": source.get("evidence_id"),
                "observation": {key: observation.get(key) for key in (
                    "ineligibility_reasons", "process_tree_complete", "child_processes", "paths")},
                "runner_stderr": first.stderr[-4000:],
            })

        # A reviewed layout change affects alpha's identity, while beta's
        # command, coverage, shared inputs and observed dependencies stay exact.
        tool_id = "refresh-child-plan"
        self.assertIsNone(self.pre_tool("apply_patch", "*** Begin Patch\n*** End Patch", "turn-2",
                                       submit_prompt=False, tool_use_id=tool_id))
        (self.workspace / "app/alpha.py").write_text("VALUE = 2\n")
        path = self.workspace / ".click/evidence-shards.json"
        manifest = json.loads(path.read_text())
        alpha = next(child for child in manifest["entries"][0]["shards"]
                     if child["covers"] == ["tests/test_alpha.py"])
        alpha["id"] = "alpha-refreshed"
        path.write_text(json.dumps(manifest))
        subprocess.run(["git", "add", "."], cwd=self.workspace, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Click Tests",
                        "-c", "user.email=click-tests@example.invalid", "commit", "-qm", "accept refreshed child plan"],
                       cwd=self.workspace, check=True, capture_output=True)
        self.tool_hook("post-tool", "apply_patch", {"patch": "alpha and plan"},
                       turn_id="turn-2", tool_use_id=tool_id)
        prepared = self.verify_gate([parent])
        planned = json.loads(state_path.read_text())
        decisions = planned["verification"]["incremental_plan"]["decisions"]
        self.assertEqual(sorted(item["decision"] for item in decisions),
                         ["reuse-dependency", "run"], decisions)
        self.assertEqual(self.run_rewritten(prepared).returncode, 0)
        completed = json.loads(state_path.read_text())
        self.assertEqual(len(completed["evidence_state"]["sources"]), 2)
        self.assertEqual(completed["verification"]["status"], "passed")

    def test_separate_contract_reuses_only_the_unaffected_shard(self) -> None:
        (self.workspace / ".gitignore").write_text("__pycache__/\n")
        (self.workspace / "shared.cfg").write_text("mode=stable\n")
        (self.workspace / "uv.lock").write_text("version=1\n")
        (self.workspace / "alpha.py").write_text(
            "import unittest\nVALUE = 1\n"
            "class Alpha(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(VALUE, 1)\n"
        )
        (self.workspace / "beta.py").write_text(
            "import unittest\nVALUE = 1\n"
            "class Beta(unittest.TestCase):\n"
            "    def test_value(self): self.assertGreater(VALUE, 0)\n"
        )
        self.initialize_git(
            ".gitignore", "shared.cfg", "uv.lock", "alpha.py", "beta.py"
        )
        commands = [
            [sys.executable, "-m", "unittest", "alpha.Alpha.test_value"],
            [sys.executable, "-m", "unittest", "beta.Beta.test_value"],
        ]
        evidence_ids = ["E_ALPHA", "E_BETA"]
        self.set_default("guarded", "turn-0")

        contract_a = self.contract_for_shards("establish authoritative shard inputs")
        self.arm_gate("turn-1")
        self.stage_gate(contract_a, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        state_a = self.enable_authoritative("turn-2")
        contract_a_id = state_a["contract_id"]
        first = self.verify_gate(commands, "turn-2", evidence_ids=evidence_ids)
        first_result = self.run_rewritten(first)
        self.assertEqual(first_result.returncode, 0, first_result.stderr)

        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        origin = json.loads(state_path.read_text())
        for evidence_id in evidence_ids:
            source = origin["evidence_state"]["sources"][
                CLICK_EVIDENCE.evidence_key(evidence_id)
            ]
            self.assertEqual(
                source["verified_dependency_observation"]["provider"],
                dependency_cache.AUTHORITATIVE_OBSERVATION_PROVIDER_NAME,
            )
            self.assertEqual(
                source["verified_dependency_observation"]["status"],
                "complete",
                source["verified_dependency_observation"],
            )

        contract_b = self.contract_for_shards(
            "reuse the unaffected shard after a separately approved change"
        )
        self.arm_gate("turn-3")
        self.stage_gate(contract_b, "turn-3")
        staged = json.loads(state_path.read_text())
        self.assertNotEqual(staged["contract_id"], contract_a_id)
        denied = self.pre_tool(
            "Bash",
            "click-gate observer authoritative",
            "turn-3",
            submit_prompt=False,
        )
        assert denied is not None
        self.assertEqual(
            denied["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.arm_gate("turn-4")
        self.pass_gate(turn_id="turn-4")
        state_b = self.enable_authoritative("turn-4")
        self.assertNotEqual(state_b["contract_id"], contract_a_id)

        beta = self.workspace / "beta.py"
        self.mark_patch(beta, "VALUE = 1", "VALUE = 2", "beta-change")
        alpha_key = CLICK_EVIDENCE.evidence_key("E_ALPHA")
        beta_key = CLICK_EVIDENCE.evidence_key("E_BETA")
        origin_alpha_observation = origin["evidence_state"]["sources"][alpha_key][
            "verified_dependency_observation"
        ]
        successor_runtime = observer_runtime.state_from_verification(
            state_b["verification"]
        )
        assert successor_runtime is not None
        stale_rows = [
            row
            for row in origin_alpha_observation["inputs"]
            if not observation_inputs.records_current(
                self.workspace,
                successor_runtime["artifact_id"],
                [row],
                profile=successor_runtime["profile"],
            )
        ]
        self.assertEqual(stale_rows, [], stale_rows)
        partial = self.verify_gate(
            commands, "turn-4", evidence_ids=evidence_ids
        )
        self.assertIn(
            "run-verification",
            split_runner_command(
                partial["hookSpecificOutput"]["updatedInput"]["command"]
            ),
        )
        partial_result = self.run_rewritten(partial)
        self.assertEqual(partial_result.returncode, 0, partial_result.stderr)
        after_partial = json.loads(state_path.read_text())
        plan = after_partial["verification"]["incremental_plan"]
        decisions = {item["source_key"]: item for item in plan["decisions"]}
        self.assertEqual(
            decisions[alpha_key]["decision"], "reuse-dependency", decisions
        )
        self.assertEqual(
            decisions[alpha_key]["reason_code"],
            "successor-evidence-dependencies-unchanged",
        )
        self.assertEqual(decisions[beta_key]["decision"], "run")
        self.assertEqual(
            after_partial["evidence_state"]["sources"][alpha_key][
                "last_successor_origin_contract_id"
            ],
            contract_a_id,
        )

        shared = self.workspace / "shared.cfg"
        self.mark_patch(
            shared, "mode=stable", "mode=changed", "shared-change"
        )
        shared_request = self.verify_gate(
            commands, "turn-4", evidence_ids=evidence_ids
        )
        shared_state = json.loads(state_path.read_text())
        shared_decisions = {
            item["source_key"]: item
            for item in shared_state["verification"]["incremental_plan"]["decisions"]
        }
        self.assertEqual(shared_decisions[alpha_key]["decision"], "run")
        self.assertEqual(shared_decisions[beta_key]["decision"], "run")
        shared_result = self.run_rewritten(shared_request)
        self.assertEqual(shared_result.returncode, 0, shared_result.stderr)
        shared_completed = json.loads(state_path.read_text())
        for key in (alpha_key, beta_key):
            observed = shared_completed["evidence_state"]["sources"][key].get(
                "verified_dependency_observation", {}
            )
            self.assertEqual(
                observed.get("status"), "complete",
                {"source": key, "observation": observed,
                 "runner_stderr": shared_result.stderr},
            )

        lockfile = self.workspace / "uv.lock"
        self.mark_patch(lockfile, "version=1", "version=2", "lockfile-change")
        lockfile_request = self.verify_gate(
            commands, "turn-4", evidence_ids=evidence_ids
        )
        lockfile_state = json.loads(state_path.read_text())
        lockfile_decisions = {
            item["source_key"]: item
            for item in lockfile_state["verification"]["incremental_plan"][
                "decisions"
            ]
        }
        self.assertEqual(lockfile_decisions[alpha_key]["decision"], "run", lockfile_decisions)
        self.assertEqual(lockfile_decisions[beta_key]["decision"], "run", lockfile_decisions)
        lockfile_result = self.run_rewritten(lockfile_request)
        self.assertEqual(lockfile_result.returncode, 0, lockfile_result.stderr)

    def test_shadow_record_and_forged_envelope_are_not_v2_authority(self) -> None:
        shadow = dependency_cache.shadow_observer_record(
            evidence_key="a" * 64,
            check_digest="b" * 64,
            mutation_revision=0,
            backend_name="strace",
            backend_version="6.8",
            backend_digest="c" * 64,
        )
        self.assertFalse(
            dependency_cache.authoritative_dependency_observation_is_valid(shadow)
        )
        forged = {
            "version": 1,
            "observation": {"provider": dependency_cache.AUTHORITATIVE_OBSERVATION_PROVIDER_NAME},
            "attestation": "0" * 64,
        }
        self.assertIsNone(
            authoritative.verified_observation(
                forged,
                secret=self.id(),
                expected_binding={
                    field: (0 if field == "mutation_revision" else "0" * 64)
                    for field in authoritative.FULL_BINDING_FIELDS
                },
            )
        )


if __name__ == "__main__":
    unittest.main()
