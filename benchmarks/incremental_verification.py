#!/usr/bin/env python3
"""Opt-in paired benchmark using real Click hooks, committed policies and runners.

Only the temporary fixture is modified. No receipt, plan decision, dependency
observation or passing ledger is manufactured by this driver.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import platform
import re
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks import click_dashboard_projection, click_incremental, click_receipt  # noqa: E402

GATE = ROOT / "hooks" / "click_gate.py"
SCENARIOS = (
    "first-run",
    "unchanged",
    "docs",
    "partial-reuse",
    "code",
    "environment",
    "first-failure",
)
COMPARISONS = ("same-shards", "parent-suite")
WORKFLOW_CONFIGS = ("baseline", "click-default", "explicit-reuse")
WORKFLOW_STEPS = (
    "first-run",
    "unrelated-code",
    "related-code",
    "all-code",
    "environment",
    "failure",
    "retry",
    "unchanged",
)
WORKFLOW_MEASUREMENTS = ("click", "same-shards", "parent-suite")
DEFAULT_ITERATIONS = 3
DEFAULT_WORKLOAD_ROUNDS = 40_000
MAX_ITERATIONS = 10
MAX_WORKLOAD_ROUNDS = 1_000_000
DEFAULT_REPOSITORY_COMMAND_TIMEOUT_SECONDS = 900.0
MAX_REPOSITORY_COMMAND_TIMEOUT_SECONDS = 1_800.0


def comparison_delta(baseline_ms: float, incremental_ms: float) -> dict[str, float | None]:
    delta = baseline_ms - incremental_ms
    return {"delta_ms": delta, "delta_percent": 100 * delta / baseline_ms if baseline_ms > 0 else None}


def distribution(values: list[float]) -> dict[str, float | None]:
    return {"median": statistics.median(values) if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None}


def _split(command: str) -> list[str]:
    if os.name != "nt":
        return shlex.split(command)
    import ctypes
    shell = ctypes.windll.shell32
    shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    count = ctypes.c_int()
    args = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not args:
        raise RuntimeError("runner-command-invalid")
    try:
        return [args[index] for index in range(count.value)]
    finally:
        kernel = ctypes.windll.kernel32
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(args, ctypes.c_void_p))


def _fixture_runner_argv(command: str) -> list[str]:
    argv = _split(command)
    if len(argv) == 5 and argv[:2] == ["py", "-3"] and argv[3] == "--encoded-runner":
        # Windows py -3 can select a different Python from the benchmark driver.
        # Keep driver, preflight, and runner on one interpreter; preserve the
        # exact encoded capability for the real runner to authenticate.
        return [sys.executable, *argv[2:]]
    return argv


class Fixture:
    """One independent checkout and lifecycle; no shared receipt across roots."""

    def __init__(
        self,
        directory: Path,
        rounds: int,
        mode: str = "evidence",
        *,
        partial_policy: bool = False,
        configuration: str = "legacy",
    ):
        if configuration not in {"legacy", "scoped-inputs", *WORKFLOW_CONFIGS}:
            raise ValueError("invalid-workflow-configuration")
        self.configuration = configuration
        self.rounds = rounds
        self.controls: list[dict[str, Any]] = []
        self.root = directory / "repository"
        self.data = directory / "runtime"
        self.root.mkdir(parents=True)
        (self.root / "tests").mkdir()
        (self.root / ".click").mkdir()
        self.environment = {key: value for key, value in os.environ.items()
                            if key not in {"PLUGIN_DATA", "CLICK_CONFIG_HOME", "PLUGIN_ROOT"}
                            and not key.startswith("GIT_")}
        self.environment.update(PLUGIN_DATA=str(self.data), CLICK_CONFIG_HOME=str(self.data),
                                PYTHONDONTWRITEBYTECODE="1", GIT_CONFIG_GLOBAL=os.devnull,
                                GIT_CONFIG_NOSYSTEM="1")
        self.event = {"session_id": "efficiency-fixture", "turn_id": "measurement",
                      "cwd": str(self.root), "permission_mode": "default"}
        self.sequence = 0
        self.parent = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q", "-f"]
        self.children = [[sys.executable, "-m", "unittest", f"tests.test_{name}", "-q", "-f"]
                         for name in ("alpha", "beta")]
        self._write(".gitignore", "__pycache__/\n*.pyc\n")
        self._write("README.md", "Benchmark fixture\n")
        self._write("docs-beta.md", "Beta-owned documentation\n")
        self._write("implementation.py", f"ROUNDS = {rounds}\nVALUE = 1\n")
        self._write("tests/__init__.py", "")
        for name in ("alpha", "beta"):
            self._write(f"tests/test_{name}.py",
                        "import hashlib\nimport unittest\nfrom implementation import ROUNDS, VALUE\n"
                        "class Check(unittest.TestCase):\n"
                        "    def test_work(self):\n"
                        "        hashlib.pbkdf2_hmac('sha256', b'click', b'fixture', ROUNDS)\n"
                        "        self.assertEqual(VALUE, 1)\n")
            if configuration != "legacy":
                self._write(f"component_{name}.py", "VALUE = 1\n")
                self._write(f"tests/test_{name}.py",
                            "import hashlib\nimport unittest\nfrom implementation import ROUNDS\n"
                            f"from component_{name} import VALUE\n"
                            "class Check(unittest.TestCase):\n"
                            "    def test_work(self):\n"
                            "        hashlib.pbkdf2_hmac('sha256', b'click', b'fixture', ROUNDS)\n"
                            "        self.assertGreater(VALUE, 0)\n")
        shards = {"version": 1, "entries": [{
            "checks": [self.parent], "inventory": ["tests/test*.py"],
            "shards": [{"id": name, "checks": [argv], "covers": [f"tests/test_{name}.py"]}
                       for name, argv in zip(("alpha", "beta"), self.children)]}]}
        policy = {
            "version": 1,
            "entries": [
                {
                    "checks": [argv],
                    "reuse_if_only_changed": [
                        "README.md"
                        if not partial_policy or name == "alpha"
                        else "docs-beta.md"
                    ],
                }
                for name, argv in zip(("alpha", "beta"), self.children)
            ],
        }
        if configuration == "explicit-reuse":
            # Repository-owner policy exists BEFORE the baseline. The test
            # imports only its own component; a sibling code change is allowed.
            for entry, sibling in zip(policy["entries"], ("beta", "alpha")):
                entry["reuse_if_only_changed"] = [f"component_{sibling}.py"]
        if configuration == "scoped-inputs":
            policy["version"] = 2
            for entry, name in zip(policy["entries"], ("alpha", "beta")):
                entry["reuse_if_only_changed"] = ["component_*.py", "implementation.py", "tests/"]
                entry["inputs"] = [
                    f"component_{name}.py", f"tests/test_{name}.py",
                    "tests/__init__.py", "implementation.py",
                ]
        if configuration in {"legacy", "explicit-reuse", "scoped-inputs"}:
            self._write(".click/evidence-shards.json", json.dumps(shards))
            self._write(".click/evidence-reuse.json", json.dumps(policy))
        for args in (["git", "init", "-q"], ["git", "add", "."],
                     ["git", "-c", "user.name=Click Benchmark", "-c",
                      "user.email=benchmark@example.invalid", "-c", "commit.gpgsign=false",
                      "commit", "-q", "-m", "Committed benchmark policy and inventory"]):
            if self._run(args).returncode:
                raise RuntimeError("fixture-git-preparation-failed")
        if configuration == "baseline":
            return  # Ordinary validation: no Hook, policy, or receipt ledger.
        if configuration not in {"legacy", "scoped-inputs"}:
            self.begin_contract("A · 최초 기준 검증")
            self._control("default guarded")  # Isolated fixture configuration only.
            return
        self._hook("prompt-submit", {"hook_event_name": "UserPromptSubmit",
                                    "prompt": "Measure the committed verification fixture in Evidence mode."})
        if mode == "guarded":
            # An isolated integration-test user stages and approves this fixture
            # through the public lifecycle. This is not approval in the user's repo.
            self._hook("prompt-submit", {"hook_event_name": "UserPromptSubmit", "prompt": "@Click\nMeasure only this temporary fixture."})
            self._control("arm")
            contract = {
                "outcome": "measure real verification in the temporary fixture",
                "boundary": {"in_scope": ["temporary fixture checks and fixed changes"], "out_of_scope": ["user repositories"]},
                "must_hold": ["never manufacture passing receipts or skip decisions"],
                "build": {"approach": ["exercise existing hooks and runners"], "semantics": ["preserve verification authority"], "order": ["baseline before change"]},
                "verification": {"scale": "focused", "evidence": [
                    {"id": "E-suite", "kind": "argv", "description": "real fixture checks"},
                    {"id": "E-measure", "kind": "manual", "description": "finish paired measurement after changes"}],
                    "done_when": [{"condition": "fixture checks pass", "primary_evidence": "E-suite"},
                                  {"condition": "paired measurement ends", "primary_evidence": "E-measure"}]},
                "plain_language": "임시 저장소의 실제 검증과 고정된 변경만 측정합니다. 기존 재사용 권한을 유지하며 사용자 저장소를 바꾸거나 영수증을 위조하지 않습니다. 비교 측정이 끝날 때까지 계약을 유지합니다."
            }
            staged = self._control("stage " + shlex.quote(json.dumps(contract)))
            match = re.search(r"ctr_[0-9a-f]{32}", json.dumps(staged))
            if match is None:
                raise RuntimeError("fixture-contract-not-staged")
            self.event["turn_id"] = "measurement-approved"
            self._hook("prompt-submit", {"hook_event_name": "UserPromptSubmit", "prompt": "Approve the staged temporary integration-test contract."})
            self._control("arm")
            approved = self._control("pass " + match.group())
            if approved.get("permissionDecision") == "deny":
                raise RuntimeError("fixture-contract-not-approved")

    def begin_contract(self, name: str) -> str:
        """Stage, demonstrate real denials, then approve in a later fixture turn."""
        self.event["turn_id"] = f"stage-{self.sequence + 1}"
        self._hook("prompt-submit", {"hook_event_name": "UserPromptSubmit",
                                    "prompt": "@Click\nStage the next independent integration-fixture contract."})
        self._control("arm")
        contract = {
            "outcome": name,
            "boundary": {"in_scope": ["독립 fixture의 고정된 코드 변경과 검증"], "out_of_scope": ["개발 세션과 사용자 저장소"]},
            "must_hold": ["별도 승인 전 실행 금지", "성공 후보만 현재 조건으로 재판정"],
            "build": {"approach": ["실제 Hook과 one-use runner"], "semantics": ["권한 비승계"], "order": ["승인 후 변경과 검증"]},
            "verification": {"scale": "focused", "evidence": [{"id": "E-suite", "kind": "argv", "description": "alpha와 beta 전체 동작"}],
                             "done_when": [{"condition": "두 동작의 유효한 검증", "primary_evidence": "E-suite"}]},
            "plain_language": "독립 임시 fixture만 별도 승인 뒤 수정하고 두 동작을 검증합니다. 과거 성공은 후보로만 재판정하며 승인과 실행 권한을 넘기지 않습니다. 개발 세션과 사용자 저장소는 바꾸지 않습니다."
        }
        if self._control("stage " + shlex.quote(json.dumps(contract))).get("permissionDecision") == "deny":
            raise RuntimeError("workflow-contract-not-staged")
        staged = self.state()
        contract_id = staged["contract_id"]
        denied = self._control("mutate " + shlex.quote(json.dumps({"version": 1, "argv": [sys.executable, "not-executed.py"]})))
        same_turn = self._control("pass " + contract_id)
        self.event["turn_id"] = f"approve-{self.sequence + 1}"
        self._hook("prompt-submit", {"hook_event_name": "UserPromptSubmit",
                                    "prompt": "Approve that exact staged contract only in this independent fixture."})
        self._control("arm")
        wrong = self._control("pass ctr_" + ("1" if contract_id != "ctr_" + "1" * 32 else "2") * 32)
        if self._control("pass " + contract_id).get("permissionDecision") == "deny":
            raise RuntimeError("workflow-contract-not-approved")
        approved = self.state()
        control = {"contract_id": contract_id, "preapproval_denied": denied.get("permissionDecision") == "deny",
                   "same_turn_denied": same_turn.get("permissionDecision") == "deny", "wrong_id_denied": wrong.get("permissionDecision") == "deny",
                   "fresh_unapproved_state": staged["status"] == "staged" and not staged.get("approved_turn_id"),
                   "separate_turns": approved["staged_turn_id"] != approved["approved_turn_id"]}
        if not all(value for key, value in control.items() if key != "contract_id"):
            raise RuntimeError("workflow-approval-boundary-failed")
        self.controls.append(control)
        return contract_id

    def workflow_change(self, scenario: str) -> None:
        if scenario in {"first-run", "unchanged"}:
            return
        if scenario == "environment":
            self.environment["TZ"] = "Etc/GMT+7"  # fingerprinted runtime variable
            return
        paths = {"unrelated-code": ("component_beta.py", 2), "related-code": ("component_alpha.py", 2),
                 "failure": ("component_alpha.py", -1), "retry": ("component_alpha.py", 3)}
        if scenario == "all-code":
            path, value = "implementation.py", 2
            contents = f"ROUNDS = {self.rounds}\nVALUE = 1\nREVISION_MARKER = {value}\n"
        else:
            path, value = paths[scenario]
            contents = f"VALUE = {value}\n"
        self.sequence += 1
        event = {"tool_name": "apply_patch", "tool_use_id": f"workflow-change-{self.sequence}",
                 "tool_input": {"patch": f"*** fixture edit {path}: VALUE = {value} ***"}}
        if self.configuration != "baseline":
            admission = self._hook("pre-tool", {**event, "hook_event_name": "PreToolUse"})
            if admission.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
                raise RuntimeError("workflow-mutation-denied")
        self._write(path, contents)
        if self.configuration != "baseline":
            self._hook("post-tool", {**event, "hook_event_name": "PostToolUse", "tool_response": {"success": True, "exit_code": 0}})

    def input_digest(self) -> str:
        digest = hashlib.sha256()
        for path in sorted([*self.root.glob("*.py"), *(self.root / "tests").glob("*.py")]):
            digest.update(path.relative_to(self.root).as_posix().encode() + b"\0" + path.read_bytes())
        return digest.hexdigest()

    def receipt(self) -> dict[str, Any]:
        response = self._control("receipt export")
        command = response.get("updatedInput", {}).get("command")
        if response.get("permissionDecision") == "deny" or not isinstance(command, str):
            raise RuntimeError("workflow-receipt-not-exportable")
        result = self._run(_fixture_runner_argv(command))
        if result.returncode:
            raise RuntimeError("workflow-receipt-export-failed")
        envelope = json.loads(result.stdout)
        _, error = click_receipt.validate_envelope(envelope)
        if error:
            raise RuntimeError("workflow-receipt-integrity-failed: " + error)
        return envelope

    def _control(self, command: str) -> dict[str, Any]:
        self.sequence += 1
        return self._hook("pre-tool", {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                          "tool_use_id": f"control-{self.sequence}",
                          "tool_input": {"command": "click-gate " + command}}).get("hookSpecificOutput", {})

    def _write(self, path: str, text: str) -> None:
        (self.root / path).write_text(text, encoding="utf-8")

    def _run(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.run(argv, cwd=self.root, env=self.environment, capture_output=True,
                              text=True, check=False, **kwargs)

    def _hook(self, action: str, event: dict[str, Any]) -> dict[str, Any]:
        result = self._run([sys.executable, str(GATE), action], input=json.dumps({**self.event, **event}))
        if result.returncode:
            raise RuntimeError("fixture-hook-failed")
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def state(self) -> dict[str, Any]:
        paths = list((self.data / "gate-state").glob("session-contract-*.json"))
        paths = [path for path in paths if not path.name.endswith(".efficiency.json")]
        if len(paths) != 1:
            raise RuntimeError("fixture-state-unavailable")
        return json.loads(paths[0].read_text(encoding="utf-8"))

    def change(self, scenario: str) -> None:
        if scenario in {"first-run", "unchanged"}:
            return
        if scenario == "environment":
            self.environment["TZ"] = "Etc/GMT+7"  # fingerprinted runtime variable
            return
        if scenario == "partial-reuse" and self.state().get("runtime_mode") == "evidence":
            # Exercise the real completed-Evidence -> next-Evidence lifecycle.
            # No receipt, source status, or plan decision is manufactured here.
            self.event["turn_id"] = f"measurement-successor-{self.sequence + 1}"
            self._hook(
                "prompt-submit",
                {
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "Measure a follow-up Evidence task after the baseline.",
                },
            )
        self.sequence += 1
        event = {"tool_name": "apply_patch", "tool_use_id": f"change-{self.sequence}",
                 "tool_input": {"patch": "*** benchmark fixture mutation ***"}}
        admission = self._hook("pre-tool", {**event, "hook_event_name": "PreToolUse"})
        if admission.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
            raise RuntimeError("fixture-mutation-rejected")
        if scenario in {"docs", "partial-reuse"}:
            self._write("README.md", "Documentation changed by the fixture.\n")
        elif scenario == "code":
            with (self.root / "implementation.py").open("a", encoding="utf-8") as handle:
                handle.write("# Code change outside the safe documentation policy.\n")
        elif scenario == "first-failure":
            self._write("tests/test_alpha.py",
                        "import unittest\nclass Check(unittest.TestCase):\n"
                        "    def test_work(self):\n        self.fail('fixture failure')\n")
        self._hook("post-tool", {**event, "hook_event_name": "PostToolUse",
                                "tool_response": {"success": True, "exit_code": 0}})

    def verify(self) -> dict[str, Any]:
        self.sequence += 1
        batch = {"version": 2, "workdir": str(self.root),
                 "checks": [{"evidence_id": "E-suite", "argv": self.parent, "class": "broad"}]}
        event = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                 "tool_use_id": f"verify-{self.sequence}",
                 "tool_input": {"command": "click-gate verify " + shlex.quote(json.dumps(batch))}}
        started = time.perf_counter_ns()
        response = self._hook("pre-tool", event).get("hookSpecificOutput", {})
        if response.get("permissionDecision") == "deny":
            code = 2
        else:
            command = response.get("updatedInput", {}).get("command")
            if not isinstance(command, str):
                raise RuntimeError("fixture-runner-not-issued")
            argv = _fixture_runner_argv(command)
            if argv and argv[0] == "echo":
                code = 0  # Actual preflight has applied reuse; no runner to dispatch.
            else:
                if "--encoded-runner" not in argv and "run-verification" not in argv:
                    raise RuntimeError("fixture-unexpected-runner")
                code = self._run(argv).returncode
        wall_ms = (time.perf_counter_ns() - started) / 1_000_000
        state = self.state()
        measured = click_incremental.current_batch(state.get("verification"))
        if measured is None:
            raise RuntimeError("fixture-measurement-unavailable")
        summary = click_incremental.batch_summary(measured)
        savings = click_incremental.revalidation_savings(measured)
        test_execution_ms = savings["executed_test_execution_ms"]
        non_test_ms = (
            max(0.0, wall_ms - test_execution_ms)
            if isinstance(test_execution_ms, (int, float)) and not isinstance(test_execution_ms, bool)
            else None
        )
        return {"wall_ms": wall_ms, "exit_code": code, "status": measured["status"],
                "executed_source_count": summary["executed_source_count"],
                "reused_source_count": summary["authoritative_reuse_count"],
                "not_run_source_count": summary["not_run_source_count"],
                "estimated_avoided_ms": summary["estimated_avoided_ms"],
                "estimated_source_count": summary["estimated_source_count"],
                "test_execution_ms": test_execution_ms,
                "test_execution_status": savings["executed_test_execution_status"],
                "click_non_test_interval_ms": non_test_ms,
                "click_non_test_interval_status": (
                    "derived-contained-intervals" if non_test_ms is not None else "unmeasured"
                ),
                "request_measurement_scope": "driver-preflight-through-runner-return",
                "test_measurement_scope": "executed-source-command-duration-sum",
                "revalidation_savings": savings,
                "batch": measured, "snapshot": click_dashboard_projection.dashboard_projection(state)}

    def full(self, comparison: str) -> dict[str, Any]:
        commands = self.children if comparison == "same-shards" else [self.parent]
        started = time.perf_counter_ns()
        code, executed = 0, 0
        for argv in commands:
            code = self._run(argv).returncode
            executed += 1
            if code:
                break
        return {"wall_ms": (time.perf_counter_ns() - started) / 1_000_000, "exit_code": code,
                "status": "passed" if code == 0 else "failed",
                "executed_source_count": executed, "reused_source_count": 0,
                "not_run_source_count": len(commands) - executed, "grouping": comparison,
                "request_measurement_scope": (
                    "sequential-shard-command-dispatch-through-return"
                    if comparison == "same-shards"
                    else "parent-command-dispatch-through-return"
                )}


def run_benchmark(*, iterations: int = DEFAULT_ITERATIONS, warmups: int = 1,
                  workload_rounds: int = DEFAULT_WORKLOAD_ROUNDS,
                  mode: str = "evidence",
                  scenarios: tuple[str, ...] = SCENARIOS) -> dict[str, Any]:
    if not 1 <= iterations <= MAX_ITERATIONS or not 0 <= warmups <= MAX_ITERATIONS:
        raise ValueError("invalid-repetition-count")
    if mode not in {"evidence", "guarded"} or not 1 <= workload_rounds <= MAX_WORKLOAD_ROUNDS or not scenarios or set(scenarios) - set(SCENARIOS):
        raise ValueError("invalid-fixture-configuration")
    samples = []
    latest_snapshot = None
    for scenario in scenarios:
        for comparison in COMPARISONS:
            for index in range(warmups + iterations):
                with tempfile.TemporaryDirectory(prefix="click-efficiency-") as directory:
                    arms = {name: Fixture(
                                Path(directory) / name,
                                workload_rounds,
                                mode,
                                partial_policy=scenario == "partial-reuse",
                            )
                            for name in ("baseline", "incremental")}
                    for arm in arms.values():
                        if scenario != "first-run":
                            if arm.verify()["status"] != "passed":
                                raise RuntimeError("fixture-baseline-failed")
                        arm.change(scenario)
                    order = ["baseline", "incremental"] if index % 2 == 0 else ["incremental", "baseline"]
                    results = {}
                    for name in order:
                        results[name] = arms[name].verify() if name == "incremental" else arms[name].full(comparison)
                    latest_snapshot = results["incremental"].pop("snapshot")
                    successful = all(item["status"] == "passed" for item in results.values())
                    samples.append({"scenario": scenario, "comparison": comparison,
                                    "iteration": index, "warmup": index < warmups, "order": order,
                                    "eligible": successful and index >= warmups,
                                    "excluded_reason": "warmup" if index < warmups else "" if successful else "verification-not-passed",
                                    **results, **comparison_delta(results["baseline"]["wall_ms"], results["incremental"]["wall_ms"])})
    summaries = []
    for scenario in scenarios:
        for comparison in COMPARISONS:
            selected = [sample for sample in samples if sample["scenario"] == scenario and
                        sample["comparison"] == comparison and sample["eligible"]]
            summaries.append({"scenario": scenario, "comparison": comparison, "samples": len(selected),
                              "baseline_wall_ms": distribution([item["baseline"]["wall_ms"] for item in selected]),
                              "incremental_wall_ms": distribution([item["incremental"]["wall_ms"] for item in selected]),
                              "delta_ms": distribution([item["delta_ms"] for item in selected]),
                              "delta_percent": distribution([item["delta_percent"] for item in selected if item["delta_percent"] is not None])})
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False)
    version = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    source_hash = hashlib.sha256()
    for path in sorted([*GATE.parent.glob("*.py"), Path(__file__).resolve()]):
        source_hash.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
        source_hash.update(hashlib.sha256(path.read_bytes()).digest())
    return {"version": 2, "kind": "click-paired-verification-benchmark",
            "engine": {"version": version, "commit": commit.stdout.strip() if commit.returncode == 0 else None,
                       "source_digest": source_hash.hexdigest(),
                       "working_tree_modified": bool(dirty.stdout.strip())},
            "environment": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version()},
            "conditions": {"iterations": iterations, "warmups": warmups, "workload_rounds": workload_rounds,
                           "scope": ["alpha", "beta"], "scope_equivalence": "same-two-unittest-files",
                           "cache": "fresh-checkout-per-arm-and-pair; baseline-warmed-except-first-run; OS-cache-not-flushed; bytecode-disabled",
                           "order": "alternating-pair-order", "observer": "off", "runtime_mode": mode,
                           "authority": "real-hooks-and-one-use-runner",
                           "parent_group_count": 1, "shard_group_count": 2},
            "samples": samples, "summaries": summaries, "dashboard_snapshot": latest_snapshot,
            "observer_overhead_ms": 0, "shadow_contradiction_count": 0,
            "limitations": ["temporary-fixture-not-general-product-performance", "no-authoritative-native-dependency-observation",
                            "different-checkout-roots-have-independent-receipts", "OS-cache-and-scheduler-uncontrolled"]}


def _rotated(values: tuple[str, ...], offset: int) -> list[str]:
    position = offset % len(values)
    return list(values[position:] + values[:position])


def _workflow_comparison_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    for sample in samples:
        for configuration in WORKFLOW_CONFIGS[1:]:
            for step in sample["arms"][configuration]["steps"]:
                click = step["validation"]
                for criterion in COMPARISONS:
                    baseline = step["full_checks"][criterion]
                    click_ms = click["test_execution_ms"] if criterion == "same-shards" else click["wall_ms"]
                    click_scope = click["test_measurement_scope"] if criterion == "same-shards" else click["request_measurement_scope"]
                    measured = isinstance(click_ms, (int, float)) and not isinstance(click_ms, bool)
                    statuses_pass = baseline["status"] == click["status"] == "passed"
                    scope_equivalent = not (
                        configuration == "click-default" and criterion == "same-shards"
                    )
                    eligible = bool(not sample["warmup"] and measured and statuses_pass and scope_equivalent)
                    excluded = (
                        "warmup" if sample["warmup"] else
                        "scope-not-equivalent" if not scope_equivalent else
                        "verification-not-passed" if not statuses_pass else
                        "timing-unavailable" if not measured else ""
                    )
                    delta = comparison_delta(baseline["wall_ms"], click_ms) if measured else {
                        "delta_ms": None, "delta_percent": None,
                    }
                    comparisons.append({
                        "configuration": configuration,
                        "scenario": step["scenario"],
                        "comparison": criterion,
                        "iteration": sample["iteration"],
                        "warmup": sample["warmup"],
                        "order": step["measurement_order"],
                        "eligible": eligible,
                        "excluded_reason": excluded,
                        "scope_equivalent": scope_equivalent,
                        "unit": "ms",
                        "baseline": {
                            "duration_ms": baseline["wall_ms"],
                            "status": baseline["status"],
                            "measurement_scope": baseline["request_measurement_scope"],
                        },
                        "click": {
                            "duration_ms": click_ms,
                            "status": click["status"],
                            "measurement_scope": click_scope,
                        },
                        **delta,
                    })
    return comparisons


def _stage_summaries(comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for configuration in WORKFLOW_CONFIGS[1:]:
        for scenario in WORKFLOW_STEPS:
            for criterion in COMPARISONS:
                raw = [item for item in comparisons if item["configuration"] == configuration
                       and item["scenario"] == scenario and item["comparison"] == criterion]
                selected = [item for item in raw if item["eligible"]]
                summaries.append({
                    "configuration": configuration,
                    "scenario": scenario,
                    "comparison": criterion,
                    "raw_samples": len(raw),
                    "eligible_samples": len(selected),
                    "excluded_samples": len(raw) - len(selected),
                    "baseline_duration_ms": distribution([item["baseline"]["duration_ms"] for item in selected]),
                    "click_duration_ms": distribution([item["click"]["duration_ms"] for item in selected]),
                    "delta_ms": distribution([item["delta_ms"] for item in selected]),
                    "delta_percent": distribution([item["delta_percent"] for item in selected
                                                    if item["delta_percent"] is not None]),
                })
    return summaries


def _cumulative_summaries(comparisons: list[dict[str, Any]], iterations: int) -> list[dict[str, Any]]:
    summaries = []
    for configuration in WORKFLOW_CONFIGS[1:]:
        for criterion in COMPARISONS:
            totals = []
            for iteration in range(iterations):
                raw_iteration = iteration + max(item["iteration"] for item in comparisons) + 1 - iterations
                selected = [item for item in comparisons if item["configuration"] == configuration
                            and item["comparison"] == criterion and item["iteration"] == raw_iteration
                            and item["eligible"]]
                if selected:
                    baseline_ms = sum(item["baseline"]["duration_ms"] for item in selected)
                    click_ms = sum(item["click"]["duration_ms"] for item in selected)
                    totals.append({"baseline_ms": baseline_ms, "click_ms": click_ms,
                                   **comparison_delta(baseline_ms, click_ms),
                                   "included_stages": len(selected)})
            summaries.append({
                "configuration": configuration,
                "comparison": criterion,
                "samples": len(totals),
                "included_stage_count": distribution([item["included_stages"] for item in totals]),
                "baseline_duration_ms": distribution([item["baseline_ms"] for item in totals]),
                "click_duration_ms": distribution([item["click_ms"] for item in totals]),
                "delta_ms": distribution([item["delta_ms"] for item in totals]),
                "delta_percent": distribution([item["delta_percent"] for item in totals
                                                if item["delta_percent"] is not None]),
            })
    return summaries


def run_scoped_session_benchmark(*, iterations: int = 1,
                                 workload_rounds: int = DEFAULT_WORKLOAD_ROUNDS) -> dict[str, Any]:
    """Paired Evidence sessions; setup, failed attempts and final audit all count.

    This exercises real hooks and signed runners. It is a verification fixture,
    not a measurement of agent implementation time, host tokens or production.
    """
    if not 1 <= iterations <= MAX_ITERATIONS or not 1 <= workload_rounds <= MAX_WORKLOAD_ROUNDS:
        raise ValueError("invalid-scoped-session-configuration")
    samples = []
    for index in range(iterations):
        arms = {}
        with tempfile.TemporaryDirectory(prefix="click-scoped-session-") as directory:
            for configuration in _rotated(("baseline", "scoped-inputs"), index):
                started = time.perf_counter_ns()
                fixture = Fixture(Path(directory) / configuration, workload_rounds,
                                  configuration=configuration)
                setup_ms = (time.perf_counter_ns() - started) / 1_000_000
                steps = []
                for position, scenario in enumerate(WORKFLOW_STEPS):
                    started = time.perf_counter_ns()
                    if configuration == "scoped-inputs" and position:
                        fixture.event["turn_id"] = f"scoped-step-{position}"
                        fixture._hook("prompt-submit", {
                            "hook_event_name": "UserPromptSubmit",
                            "prompt": "Continue the next fixed Evidence fixture change and verification.",
                        })
                    fixture.workflow_change(scenario)
                    result = fixture.full("parent-suite") if configuration == "baseline" else fixture.verify()
                    wall_ms = (time.perf_counter_ns() - started) / 1_000_000
                    steps.append({
                        "scenario": scenario, "wall_ms": wall_ms,
                        "exit_code": result["exit_code"], "status": result["status"],
                        "executed": result["executed_source_count"],
                        "reused": result["reused_source_count"],
                        "input_digest": fixture.input_digest(),
                    })
                audit = fixture.full("parent-suite")
                arms[configuration] = {
                    "setup_ms": setup_ms, "steps": steps,
                    "final_full_audit": audit,
                    "total_ms": setup_ms + sum(step["wall_ms"] for step in steps) + audit["wall_ms"],
                }
        baseline, incremental = arms["baseline"], arms["scoped-inputs"]
        equivalent = all(
            left["input_digest"] == right["input_digest"]
            and (left["exit_code"] == 0) == (right["exit_code"] == 0)
            for left, right in zip(baseline["steps"], incremental["steps"])
        ) and all(arm["final_full_audit"]["exit_code"] == 0 for arm in arms.values())
        samples.append({
            "iteration": index, "arms": arms, "equivalent": equivalent,
            **(comparison_delta(baseline["total_ms"], incremental["total_ms"])
               if equivalent else {"delta_ms": None, "delta_percent": None}),
        })
    return {
        "version": 1, "kind": "click-scoped-session-benchmark", "unit": "ms",
        "scope": "isolated-verification-workflow-including-setup-failure-and-final-audit",
        "workload_rounds": workload_rounds, "samples": samples,
        "summaries": {"eligible_samples": sum(sample["equivalent"] for sample in samples),
                      "delta_ms": distribution([sample["delta_ms"] for sample in samples if sample["equivalent"]])},
        "limitations": [
            "Owner policy and layout are committed before baseline in an isolated fixture.",
            "Policy preparation and initial baseline count; automatic inventory/bootstrap is not exercised by this fixture.",
            "Fixed CPU workload is synthetic; no extrapolated production or tens-of-minutes claim.",
            "Each Hook is a fresh CLI process; this does not measure the installed resident host worker.",
            "Whole-agent implementation time, user wait time and host token usage are unmeasured.",
            "Negative deltas are retained; only equivalent outcomes qualify.",
        ],
    }


def run_guarded_workflow_benchmark(*, iterations: int = DEFAULT_ITERATIONS, warmups: int = 1,
                                   workload_rounds: int = DEFAULT_WORKLOAD_ROUNDS) -> dict[str, Any]:
    """Measure independent A→B workflows with two same-state counterfactuals.

    Direct full executions are additional audits. They never update Click's
    receipts, grant reuse, or stand in for a human approval decision.
    """
    if not 1 <= iterations <= MAX_ITERATIONS or not 0 <= warmups <= MAX_ITERATIONS or not 1 <= workload_rounds <= MAX_WORKLOAD_ROUNDS:
        raise ValueError("invalid-workflow-configuration")
    samples = []
    latest_snapshot = None
    for index in range(warmups + iterations):
        workflow_order = _rotated(WORKFLOW_CONFIGS, index)
        arms = {}
        with tempfile.TemporaryDirectory(prefix="click-guarded-workflow-") as directory:
            for configuration in workflow_order:
                started = time.perf_counter_ns()
                fixture = Fixture(Path(directory) / configuration, workload_rounds, "guarded", configuration=configuration)
                setup_ms = (time.perf_counter_ns() - started) / 1_000_000
                steps, successor_receipt = [], None
                for position, scenario in enumerate(WORKFLOW_STEPS):
                    started = time.perf_counter_ns()
                    if configuration != "baseline" and scenario in {
                        "unrelated-code", "related-code", "all-code", "environment", "failure",
                    }:
                        fixture.begin_contract(f"{chr(65 + position)} · {scenario}")
                    fixture.workflow_change(scenario)
                    transition_ms = (time.perf_counter_ns() - started) / 1_000_000

                    if configuration == "baseline":
                        measurement_order = _rotated(("parent-suite", "same-shards"), index + position)
                    else:
                        measurement_order = _rotated(
                            WORKFLOW_MEASUREMENTS,
                            index + position + WORKFLOW_CONFIGS.index(configuration),
                        )
                    measured: dict[str, dict[str, Any]] = {}
                    for measurement in measurement_order:
                        measured[measurement] = fixture.verify() if measurement == "click" else fixture.full(measurement)
                    if configuration == "baseline":
                        validation = measured["parent-suite"]
                        full_checks = measured
                    else:
                        validation = measured["click"]
                        snapshot = validation.pop("snapshot", None)
                        if configuration == "explicit-reuse" and scenario == "unrelated-code":
                            latest_snapshot = snapshot
                        full_checks = {name: measured[name] for name in COMPARISONS}

                    expected = "failed" if scenario == "failure" else "passed"
                    audit_matches = all(
                        item["status"] == validation["status"] and item["exit_code"] == validation["exit_code"]
                        for item in full_checks.values()
                    )
                    if not audit_matches or validation["status"] != expected:
                        raise RuntimeError(f"workflow-full-audit-mismatch:{configuration}:{scenario}")
                    steps.append({
                        "scenario": scenario,
                        "contract_id": None if configuration == "baseline" else fixture.state()["contract_id"],
                        "input_digest": fixture.input_digest(),
                        "transition_ms": transition_ms,
                        "measurement_order": measurement_order,
                        "validation": validation,
                        "full_checks": full_checks,
                        "audit": full_checks["parent-suite"],
                        "audit_matches": audit_matches,
                        "additional_full_audit_ms": sum(item["wall_ms"] for item in full_checks.values()),
                    })
                    if configuration != "baseline" and scenario == "unrelated-code":
                        successor_receipt = fixture.receipt()
                final_receipt = None if configuration == "baseline" else fixture.receipt()
                arms[configuration] = {
                    "steps": steps,
                    "controls": fixture.controls,
                    "setup_ms": setup_ms,
                    "validation_wall_ms": sum(step["validation"]["wall_ms"] for step in steps),
                    "transition_ms": sum(step["transition_ms"] for step in steps),
                    "audit_wall_ms": sum(step["additional_full_audit_ms"] for step in steps),
                    "successor_receipt": successor_receipt,
                    "final_receipt": final_receipt,
                    "final_input_digest": fixture.input_digest(),
                }
            for position in range(len(WORKFLOW_STEPS)):
                if len({arm["steps"][position]["input_digest"] for arm in arms.values()}) != 1:
                    raise RuntimeError("workflow-inputs-not-equivalent")
        samples.append({"iteration": index, "warmup": index < warmups,
                        "workflow_order": workflow_order, "arms": arms})

    comparisons = _workflow_comparison_samples(samples)
    stage_summaries = _stage_summaries(comparisons)
    cumulative_summaries = _cumulative_summaries(comparisons, iterations)
    workflow_cost_summaries = []
    selected_samples = [sample for sample in samples if not sample["warmup"]]
    for configuration in WORKFLOW_CONFIGS[1:]:
        workflow_cost_summaries.append({
            "configuration": configuration,
            "samples": len(selected_samples),
            "click_request_ms_including_expected_failure": distribution([
                sample["arms"][configuration]["validation_wall_ms"] for sample in selected_samples
            ]),
            "transition_ms": distribution([
                sample["arms"][configuration]["transition_ms"] for sample in selected_samples
            ]),
            "setup_ms": distribution([
                sample["arms"][configuration]["setup_ms"] for sample in selected_samples
            ]),
            "additional_full_audit_ms": distribution([
                sample["arms"][configuration]["audit_wall_ms"] for sample in selected_samples
            ]),
        })
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False)
    source_hash = hashlib.sha256()
    for path in sorted([*GATE.parent.glob("*.py"), Path(__file__).resolve()]):
        source_hash.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return {
        "version": 4,
        "kind": "click-guarded-workflow-benchmark",
        "source": "isolated-guarded-fixture",
        "unit": "ms",
        "engine": {
            "version": json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"],
            "commit": commit.stdout.strip() if commit.returncode == 0 else None,
            "source_digest": source_hash.hexdigest(),
            "working_tree_modified": bool(dirty.stdout.strip()),
            "assurance": "unsigned-files-at-measurement",
        },
        "environment": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version()},
        "conditions": {
            "iterations": iterations,
            "warmups": warmups,
            "workload_rounds": workload_rounds,
            "configurations": list(WORKFLOW_CONFIGS),
            "steps": list(WORKFLOW_STEPS),
            "comparisons": list(COMPARISONS),
            "runtime_mode": "guarded-explicitly-selected; baseline-without-click",
            "scope_equivalence": "same-two-unittest-files-and-code-at-every-stage",
            "measurement_order": "rotating-within-stage-and-workflow",
            "authority": "real-hooks-and-one-use-runner; distinct-scripted-fixture-approval-turns",
            "observer": "off",
            "cache": "fresh-initial-state-per-configuration-and-repetition; OS-cache-not-flushed; bytecode-disabled",
            "default_configuration": "no-optional-shards-dependencies-or-safe-change-policy; product-default-mode-remains-Evidence",
            "explicit_configuration": "fixed-committed-two-shard-map-and-sibling-code-safe-change-policy-before-A",
            "test_interval": "source-command-dispatch-through-return; sequential-sum-for-executed-sources",
            "click_request_interval": "driver-preflight-through-runner-return",
            "additional_cost": "setup-transition-and-two-same-state-full-executions-reported-separately",
            "failure": "expected-failure-kept-raw-and-in-workflow-cost; excluded-from-success-only-savings-statistics",
        },
        "samples": samples,
        "comparison_samples": comparisons,
        "stage_summaries": stage_summaries,
        "cumulative_summaries": cumulative_summaries,
        "workflow_cost_summaries": workflow_cost_summaries,
        "summaries": cumulative_summaries,
        "repository_reference": None,
        "dashboard_snapshot": latest_snapshot,
        "limitations": [
            "synthetic-workload-fixture-not-universal-safety-or-performance-proof",
            "scripted-fixture-approvals-not-current-session-approval-or-human-decision-time",
            "explicit-policy-is-fixed-owner-authority-not-observed-dependency-discovery",
            "same-state-full-executions-are-additional-audit-cost-and-can-warm-native-caches",
            "OS-cache-and-scheduler-uncontrolled",
            "no-token-fee-or-total-development-time-conversion",
        ],
    }


def _repository_group(
    commands: list[list[str]], *, timeout_seconds: float
) -> dict[str, Any]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.perf_counter_ns()
    code, executed = 0, 0
    for argv in commands:
        try:
            completed = subprocess.run(
                argv,
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            executed += 1
            return {
                "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
                "status": "timeout",
                "exit_code": 124,
                "executed_command_count": executed,
                "not_run_command_count": len(commands) - executed,
            }
        code = completed.returncode
        executed += 1
        if code:
            break
    return {
        "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
        "status": "passed" if code == 0 else "failed",
        "exit_code": code,
        "executed_command_count": executed,
        "not_run_command_count": len(commands) - executed,
    }


def run_repository_bundle_reference(
    *,
    iterations: int = 1,
    warmups: int = 0,
    command_timeout_seconds: float = DEFAULT_REPOSITORY_COMMAND_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Measure the committed repository test bundle without inventing load.

    This is a grouping reference, not a fabricated Click-reuse run. The
    isolated A→B fixture remains the only place where deterministic code
    mutations and approvals are exercised.
    """
    if (
        not 1 <= iterations <= 3
        or not 0 <= warmups <= 2
        or not isinstance(command_timeout_seconds, (int, float))
        or isinstance(command_timeout_seconds, bool)
        or not 1 <= float(command_timeout_seconds) <= MAX_REPOSITORY_COMMAND_TIMEOUT_SECONDS
    ):
        raise ValueError("invalid-repository-repetition-count")
    payload = json.loads((ROOT / ".click" / "evidence-shards.json").read_text(encoding="utf-8"))
    entries = payload.get("entries") if isinstance(payload, dict) and payload.get("version") == 1 else None
    if not isinstance(entries, list) or len(entries) != 1:
        raise RuntimeError("repository-shard-inventory-unavailable")
    entry = entries[0]
    parent_checks = entry.get("checks")
    shards = entry.get("shards")
    inventory = entry.get("inventory")
    if (
        not isinstance(parent_checks, list) or len(parent_checks) != 1
        or not isinstance(shards, list) or not shards
        or not isinstance(inventory, list) or not inventory
    ):
        raise RuntimeError("repository-shard-inventory-invalid")
    parent = parent_checks[0]
    child_commands = []
    shard_ids = []
    for shard in shards:
        checks = shard.get("checks") if isinstance(shard, dict) else None
        shard_id = shard.get("id") if isinstance(shard, dict) else None
        if (not isinstance(shard_id, str) or not shard_id or not isinstance(checks, list)
                or len(checks) != 1):
            raise RuntimeError("repository-shard-inventory-invalid")
        child_commands.append(checks[0])
        shard_ids.append(shard_id)
    all_commands = [parent, *child_commands]
    if any(not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv)
           for argv in all_commands):
        raise RuntimeError("repository-shard-command-invalid")
    scope_digest = hashlib.sha256(json.dumps(
        {"parent": parent, "shards": list(zip(shard_ids, child_commands)), "inventory": inventory},
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    samples = []
    for index in range(warmups + iterations):
        order = _rotated(("same-shards", "parent-suite"), index)
        results = {}
        for measurement in order:
            commands = child_commands if measurement == "same-shards" else [parent]
            results[measurement] = _repository_group(
                commands, timeout_seconds=float(command_timeout_seconds)
            )
        passed = all(item["status"] == "passed" for item in results.values())
        delta = comparison_delta(
            results["parent-suite"]["duration_ms"],
            results["same-shards"]["duration_ms"],
        )
        samples.append({
            "iteration": index,
            "warmup": index < warmups,
            "order": order,
            "eligible": bool(index >= warmups and passed),
            "excluded_reason": "warmup" if index < warmups else "" if passed else "verification-not-passed",
            "same_shards": results["same-shards"],
            "parent_suite": results["parent-suite"],
            **delta,
        })
    selected = [item for item in samples if item["eligible"]]
    return {
        "version": 2,
        "kind": "click-repository-bundle-reference",
        "source": "current-repository-test-bundle",
        "unit": "ms",
        "scope_digest": scope_digest,
        "conditions": {
            "iterations": iterations,
            "warmups": warmups,
            "shard_count": len(child_commands),
            "scope_basis": "committed-evidence-shards-v1-inventory",
            "measurement_order": "alternating-pair-order",
            "cache": "same-working-tree-and-environment; OS-cache-not-flushed; bytecode-disabled",
            "measurement_scope": "driver-command-dispatch-through-return",
            "command_timeout_seconds": float(command_timeout_seconds),
        },
        "samples": samples,
        "summary": {
            "eligible_samples": len(selected),
            "same_shards_duration_ms": distribution([item["same_shards"]["duration_ms"] for item in selected]),
            "parent_suite_duration_ms": distribution([item["parent_suite"]["duration_ms"] for item in selected]),
            "parent_minus_shards_ms": distribution([item["delta_ms"] for item in selected]),
        },
        "limitations": [
            "grouping-reference-only-not-a-click-reuse-counterfactual",
            "current-dirty-working-tree-measured-as-is",
            "OS-cache-and-scheduler-uncontrolled",
        ],
    }


def repository_reference_is_valid(value: Any) -> bool:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "kind", "source", "unit", "scope_digest", "conditions",
                           "samples", "summary", "limitations"}
        or value.get("version") not in {1, 2}
        or value.get("kind") != "click-repository-bundle-reference"
        or value.get("source") != "current-repository-test-bundle"
        or value.get("unit") != "ms"
        or re.fullmatch(r"[0-9a-f]{64}", str(value.get("scope_digest", ""))) is None
    ):
        return False
    conditions = value.get("conditions")
    expected_condition_keys = {
        "iterations", "warmups", "shard_count", "scope_basis", "measurement_order",
        "cache", "measurement_scope",
    }
    if value.get("version") == 2:
        expected_condition_keys.add("command_timeout_seconds")
    if not isinstance(conditions, dict) or set(conditions) != expected_condition_keys:
        return False
    iterations, warmups, shard_count = (
        conditions.get("iterations"), conditions.get("warmups"), conditions.get("shard_count")
    )
    if (
        not all(isinstance(item, int) and not isinstance(item, bool)
                for item in (iterations, warmups, shard_count))
        or not 1 <= iterations <= 3 or not 0 <= warmups <= 2 or shard_count < 1
        or conditions.get("scope_basis") != "committed-evidence-shards-v1-inventory"
        or conditions.get("measurement_order") != "alternating-pair-order"
        or conditions.get("measurement_scope") != "driver-command-dispatch-through-return"
        or conditions.get("cache") != "same-working-tree-and-environment; OS-cache-not-flushed; bytecode-disabled"
        or (
            value.get("version") == 2
            and (
                not isinstance(conditions.get("command_timeout_seconds"), (int, float))
                or isinstance(conditions.get("command_timeout_seconds"), bool)
                or not 1
                <= float(conditions["command_timeout_seconds"])
                <= MAX_REPOSITORY_COMMAND_TIMEOUT_SECONDS
            )
        )
    ):
        return False
    samples = value.get("samples")
    if not isinstance(samples, list) or len(samples) != iterations + warmups:
        return False
    seen = set()
    eligible_count = 0
    for item in samples:
        if not isinstance(item, dict) or set(item) != {
            "iteration", "warmup", "order", "eligible", "excluded_reason", "same_shards",
            "parent_suite", "delta_ms", "delta_percent",
        }:
            return False
        index = item["iteration"]
        if (
            not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(samples)
            or index in seen or item["warmup"] is not (index < warmups)
            or item["order"] not in (["same-shards", "parent-suite"], ["parent-suite", "same-shards"])
        ):
            return False
        seen.add(index)
        arms = []
        for name in ("same_shards", "parent_suite"):
            arm = item[name]
            if not isinstance(arm, dict) or set(arm) != {
                "duration_ms", "status", "exit_code", "executed_command_count", "not_run_command_count",
            }:
                return False
            duration_ms = arm["duration_ms"]
            if (
                not isinstance(duration_ms, (int, float)) or isinstance(duration_ms, bool)
                or not 0 <= duration_ms < float("inf")
                or arm["status"] not in {"passed", "failed", "timeout"}
                or not isinstance(arm["exit_code"], int) or isinstance(arm["exit_code"], bool)
                or not all(isinstance(arm[field], int) and not isinstance(arm[field], bool) and arm[field] >= 0
                           for field in ("executed_command_count", "not_run_command_count"))
                or (arm["status"] == "passed") != (arm["exit_code"] == 0)
            ):
                return False
            arms.append(arm)
        passed = all(arm["status"] == "passed" for arm in arms)
        eligible = bool(index >= warmups and passed)
        expected_reason = "warmup" if index < warmups else "" if passed else "verification-not-passed"
        if item["eligible"] is not eligible or item["excluded_reason"] != expected_reason:
            return False
        eligible_count += eligible
        expected_delta = comparison_delta(
            item["parent_suite"]["duration_ms"], item["same_shards"]["duration_ms"],
        )
        if (
            not isinstance(item["delta_ms"], (int, float)) or isinstance(item["delta_ms"], bool)
            or abs(item["delta_ms"] - expected_delta["delta_ms"]) > 1e-6
            or (
                expected_delta["delta_percent"] is None and item["delta_percent"] is not None
            )
            or (
                expected_delta["delta_percent"] is not None
                and (not isinstance(item["delta_percent"], (int, float))
                     or isinstance(item["delta_percent"], bool)
                     or abs(item["delta_percent"] - expected_delta["delta_percent"]) > 1e-6)
            )
        ):
            return False
    summary = value.get("summary")
    if not isinstance(summary, dict) or set(summary) != {
        "eligible_samples", "same_shards_duration_ms", "parent_suite_duration_ms", "parent_minus_shards_ms",
    } or summary["eligible_samples"] != eligible_count:
        return False
    for name in ("same_shards_duration_ms", "parent_suite_duration_ms", "parent_minus_shards_ms"):
        item = summary[name]
        if not isinstance(item, dict) or set(item) != {"median", "min", "max"} or any(
            number is not None and (not isinstance(number, (int, float)) or isinstance(number, bool)
                                    or not -float("inf") < number < float("inf"))
            for number in item.values()
        ):
            return False
    return isinstance(value.get("limitations"), list) and all(
        isinstance(item, str) for item in value["limitations"]
    )


def workflow_report_is_valid(value: Any) -> bool:
    """Validate the portable v4 report before export or standalone rendering."""
    top_level_fields = {
        "version", "kind", "source", "unit", "engine", "environment", "conditions",
        "samples", "comparison_samples", "stage_summaries", "cumulative_summaries",
        "workflow_cost_summaries", "summaries", "repository_reference",
        "dashboard_snapshot", "limitations",
    }
    if (
        not isinstance(value, dict)
        or set(value) != top_level_fields
        or value.get("version") != 4
        or value.get("kind") != "click-guarded-workflow-benchmark"
        or value.get("source") != "isolated-guarded-fixture"
        or value.get("unit") != "ms"
        or not isinstance(value.get("comparison_samples"), list)
        or len(value["comparison_samples"]) > 640
    ):
        return False
    engine = value.get("engine")
    environment = value.get("environment")
    if (
        not isinstance(engine, dict)
        or set(engine) != {"version", "commit", "source_digest", "working_tree_modified", "assurance"}
        or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[+-][0-9A-Za-z.-]+)?", str(engine.get("version", ""))) is None
        or engine.get("commit") is not None and re.fullmatch(r"[0-9a-f]{40,64}", str(engine["commit"])) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(engine.get("source_digest", ""))) is None
        or not isinstance(engine.get("working_tree_modified"), bool)
        or engine.get("assurance") != "unsigned-files-at-measurement"
        or not isinstance(environment, dict)
        or set(environment) != {"system", "machine", "python"}
        or environment.get("system") not in {"Linux", "Darwin", "Windows"}
        or environment.get("machine") not in {"x86_64", "AMD64", "aarch64", "arm64", "i386", "i686", "x86"}
        or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", str(environment.get("python", ""))) is None
    ):
        return False
    conditions = value.get("conditions")
    expected_condition_fields = {
        "iterations", "warmups", "workload_rounds", "configurations", "steps", "comparisons",
        "runtime_mode", "scope_equivalence", "measurement_order", "authority", "observer",
        "cache", "default_configuration", "explicit_configuration", "test_interval",
        "click_request_interval", "additional_cost", "failure",
    }
    if (
        not isinstance(conditions, dict)
        or set(conditions) != expected_condition_fields
        or conditions.get("configurations") != list(WORKFLOW_CONFIGS)
        or conditions.get("steps") != list(WORKFLOW_STEPS)
        or conditions.get("comparisons") != list(COMPARISONS)
        or conditions.get("scope_equivalence") != "same-two-unittest-files-and-code-at-every-stage"
        or conditions.get("measurement_order") != "rotating-within-stage-and-workflow"
        or conditions.get("runtime_mode") != "guarded-explicitly-selected; baseline-without-click"
        or conditions.get("authority") != "real-hooks-and-one-use-runner; distinct-scripted-fixture-approval-turns"
        or conditions.get("observer") != "off"
        or conditions.get("cache") != "fresh-initial-state-per-configuration-and-repetition; OS-cache-not-flushed; bytecode-disabled"
        or conditions.get("default_configuration") != "no-optional-shards-dependencies-or-safe-change-policy; product-default-mode-remains-Evidence"
        or conditions.get("explicit_configuration") != "fixed-committed-two-shard-map-and-sibling-code-safe-change-policy-before-A"
        or conditions.get("test_interval") != "source-command-dispatch-through-return; sequential-sum-for-executed-sources"
        or conditions.get("click_request_interval") != "driver-preflight-through-runner-return"
        or conditions.get("additional_cost") != "setup-transition-and-two-same-state-full-executions-reported-separately"
        or conditions.get("failure") != "expected-failure-kept-raw-and-in-workflow-cost; excluded-from-success-only-savings-statistics"
    ):
        return False
    count = conditions.get("iterations")
    warmups = conditions.get("warmups")
    rounds = conditions.get("workload_rounds")
    if (
        not all(isinstance(item, int) and not isinstance(item, bool) for item in (count, warmups, rounds))
        or not 1 <= count <= MAX_ITERATIONS or not 0 <= warmups <= MAX_ITERATIONS
        or not 1 <= rounds <= MAX_WORKLOAD_ROUNDS
    ):
        return False
    scopes = {
        "same-shards": (
            "sequential-shard-command-dispatch-through-return",
            "executed-source-command-duration-sum",
        ),
        "parent-suite": (
            "parent-command-dispatch-through-return",
            "driver-preflight-through-runner-return",
        ),
    }
    seen = set()
    for item in value["comparison_samples"]:
        expected_keys = {
            "configuration", "scenario", "comparison", "iteration", "warmup", "order",
            "eligible", "excluded_reason", "scope_equivalent", "unit", "baseline", "click",
            "delta_ms", "delta_percent",
        }
        if not isinstance(item, dict) or set(item) != expected_keys:
            return False
        key = (item["configuration"], item["scenario"], item["comparison"], item["iteration"])
        if (
            key in seen or item["configuration"] not in WORKFLOW_CONFIGS[1:]
            or item["scenario"] not in WORKFLOW_STEPS or item["comparison"] not in COMPARISONS
            or not isinstance(item["iteration"], int) or isinstance(item["iteration"], bool)
            or not 0 <= item["iteration"] < count + warmups
            or item["warmup"] is not (item["iteration"] < warmups)
            or sorted(item["order"]) != sorted(WORKFLOW_MEASUREMENTS)
            or item["unit"] != "ms"
        ):
            return False
        seen.add(key)
        baseline, click = item["baseline"], item["click"]
        if not isinstance(baseline, dict) or not isinstance(click, dict):
            return False
        if set(baseline) != {"duration_ms", "status", "measurement_scope"} or set(click) != {"duration_ms", "status", "measurement_scope"}:
            return False
        if baseline["measurement_scope"] != scopes[item["comparison"]][0] or click["measurement_scope"] != scopes[item["comparison"]][1]:
            return False
        durations = (baseline["duration_ms"], click["duration_ms"])
        if any(not isinstance(number, (int, float)) or isinstance(number, bool) or not 0 <= number < float("inf") for number in durations):
            return False
        if baseline["status"] not in {"passed", "failed"} or click["status"] not in {"passed", "failed"}:
            return False
        expected_scope = not (
            item["configuration"] == "click-default" and item["comparison"] == "same-shards"
        )
        expected_eligible = bool(
            not item["warmup"] and expected_scope and baseline["status"] == click["status"] == "passed"
        )
        expected_reason = (
            "warmup" if item["warmup"] else
            "scope-not-equivalent" if not expected_scope else
            "verification-not-passed" if baseline["status"] != "passed" or click["status"] != "passed"
            else ""
        )
        if (item["scope_equivalent"] is not expected_scope or item["eligible"] is not expected_eligible
                or item["excluded_reason"] != expected_reason):
            return False
        expected_delta = comparison_delta(*durations)
        if not isinstance(item["delta_ms"], (int, float)) or not abs(item["delta_ms"] - expected_delta["delta_ms"]) <= 1e-6:
            return False
        if expected_delta["delta_percent"] is None:
            if item["delta_percent"] is not None:
                return False
        elif not isinstance(item["delta_percent"], (int, float)) or not abs(item["delta_percent"] - expected_delta["delta_percent"]) <= 1e-6:
            return False
    expected_count = (count + warmups) * len(WORKFLOW_CONFIGS[1:]) * len(WORKFLOW_STEPS) * len(COMPARISONS)
    if len(seen) != expected_count:
        return False
    if (
        not isinstance(value.get("samples"), list) or len(value["samples"]) != count + warmups
        or not isinstance(value.get("stage_summaries"), list)
        or len(value["stage_summaries"]) != len(WORKFLOW_CONFIGS[1:]) * len(WORKFLOW_STEPS) * len(COMPARISONS)
        or not isinstance(value.get("cumulative_summaries"), list) or len(value["cumulative_summaries"]) != 4
        or value.get("summaries") != value.get("cumulative_summaries")
        or not isinstance(value.get("workflow_cost_summaries"), list) or len(value["workflow_cost_summaries"]) != 2
        or value.get("dashboard_snapshot") is not None and not isinstance(value["dashboard_snapshot"], dict)
        or not isinstance(value.get("limitations"), list)
        or any(not isinstance(item, str) for item in value["limitations"])
    ):
        return False
    reference = value.get("repository_reference")
    if reference is not None and not repository_reference_is_valid(reference):
        return False
    return True


def workflow_report_html(result: dict[str, Any]) -> str:
    """Small offline v4 report; no scripts, raw commands, or private paths."""
    if not workflow_report_is_valid(result):
        raise ValueError("invalid-workflow-report")
    escape = lambda value: html.escape(str(value), quote=True)
    shown = lambda value: "측정 없음" if value is None else round(value, 2)
    cumulative_rows = []
    for summary in result["cumulative_summaries"]:
        cumulative_rows.append("<tr>" + "".join(f"<td>{escape(value)}</td>" for value in (
            summary["configuration"], summary["comparison"], summary["samples"],
            shown(summary["baseline_duration_ms"]["median"]), shown(summary["click_duration_ms"]["median"]),
            shown(summary["delta_ms"]["median"]), shown(summary["delta_percent"]["median"]),
            shown(summary["included_stage_count"]["median"]))) + "</tr>")
    stage_rows = []
    for summary in result["stage_summaries"]:
        if summary["configuration"] != "explicit-reuse":
            continue
        stage_rows.append("<tr>" + "".join(f"<td>{escape(value)}</td>" for value in (
            summary["scenario"], summary["comparison"], summary["eligible_samples"], summary["excluded_samples"],
            shown(summary["baseline_duration_ms"]["median"]), shown(summary["click_duration_ms"]["median"]),
            shown(summary["delta_ms"]["median"]),
            f'{shown(summary["delta_ms"]["min"])} ~ {shown(summary["delta_ms"]["max"])}')) + "</tr>")
    cost_rows = []
    for summary in result["workflow_cost_summaries"]:
        cost_rows.append("<tr>" + "".join(f"<td>{escape(value)}</td>" for value in (
            summary["configuration"], summary["samples"],
            shown(summary["click_request_ms_including_expected_failure"]["median"]),
            shown(summary["setup_ms"]["median"]), shown(summary["transition_ms"]["median"]),
            shown(summary["additional_full_audit_ms"]["median"]))) + "</tr>")
    last = result["samples"][-1]["arms"]["explicit-reuse"]
    steps = []
    for step in last["steps"]:
        value = step["validation"]
        origins = [item["reuse_origin"]["kind"] for item in value["batch"]["sources"] if item.get("reuse_origin")]
        steps.append("<tr>" + "".join(f"<td>{escape(item)}</td>" for item in (
            step["scenario"], value["status"], value["executed_source_count"], value["reused_source_count"],
            value["not_run_source_count"], shown(value["test_execution_ms"]), shown(value["wall_ms"]),
            shown(value["click_non_test_interval_ms"]), " → ".join(step["measurement_order"]),
            step["audit_matches"], ", ".join(origins))) + "</tr>")
    reference = result.get("repository_reference")
    reference_html = "<p>실행된 실제 저장소 번들 참조가 없습니다.</p>"
    if reference is not None:
        summary = reference["summary"]
        reference_html = (
            "<p>현재 작업 트리의 커밋된 shard inventory가 선언한 실제 테스트 전체를 실행한 그룹화 참조입니다. "
            "Click 재사용 반사실이 아니며 fixture 수치와 합치지 않습니다.</p>"
            "<table><tr><th>성공 표본</th><th>모든 샤드 순차 ms</th><th>기존 parent ms</th><th>parent−shards ms</th></tr><tr>" +
            "".join(f"<td>{escape(value)}</td>" for value in (
                summary["eligible_samples"], shown(summary["same_shards_duration_ms"]["median"]),
                shown(summary["parent_suite_duration_ms"]["median"]),
                shown(summary["parent_minus_shards_ms"]["median"]))) + "</tr></table>"
        )
    return ('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Click · Guarded A→B 실측</title>'
            '<style>body{font:16px/1.6 system-ui;margin:40px auto;max-width:1100px;padding:0 24px;color:#18322d;background:#f6f8f4}table{border-collapse:collapse;width:100%;font-size:14px;background:white}th,td{padding:10px;border-bottom:1px solid #dce3dd;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere}h1{line-height:1.25}section{margin:32px 0}.scroll{overflow:auto}</style>'
            '<h1>재검증 절감, 같은 상태에서 구간별로 확인</h1><p>독립 fixture의 실제 Hook·runner 측정입니다. fixture 승인과 보고서는 현재 세션 권한이 아니며, 양수 절감은 통과 조건이 아닙니다.</p>'
            '<section><h2>성공 단계 누적 비교</h2><p><code>same-shards</code>는 모든 샤드 command 구간과 실제 실행된 source-command 구간을 비교합니다. <code>parent-suite</code>는 기존 parent 명령 전체 구간과 Click 요청 전체 구간을 비교합니다. 서로 다른 두 구간을 한 수치로 섞지 않습니다. 양수는 이 표본에서 단축, 음수는 증가입니다.</p>'
            '<div class="scroll"><table><tr><th>구성</th><th>비교</th><th>반복</th><th>전체 기준 ms</th><th>Click ms</th><th>차이 ms</th><th>차이 %</th><th>포함 단계</th></tr>' + ''.join(cumulative_rows) + '</table></div></section>'
            '<section><h2>명시적 재사용 구성 · 단계별 원시 요약</h2><p>워밍업과 실패 단계는 원시 JSON에 보존하지만 성공 절감 통계에서는 제외합니다. 범위는 표본별 차이의 최솟값~최댓값입니다.</p><div class="scroll"><table><tr><th>단계</th><th>비교</th><th>성공</th><th>제외</th><th>전체 기준 ms</th><th>Click ms</th><th>차이 ms</th><th>차이 범위 ms</th></tr>' + ''.join(stage_rows) + '</table></div></section>'
            '<section><h2>별도 비용</h2><p>실패를 포함한 Click 요청, fixture 준비, 계약·변경 전환, 같은 상태의 추가 전체 실행을 절감치와 분리합니다.</p><div class="scroll"><table><tr><th>구성</th><th>반복</th><th>Click 요청 합계 ms</th><th>준비 ms</th><th>전환 ms</th><th>추가 전체 감사 ms</th></tr>' + ''.join(cost_rows) + '</table></div></section>'
            '<section><h2>마지막 명시적 구성의 실제 흐름</h2><p>A 승인·성공 뒤 B를 별도 승인하고 부분 영향 코드를 바꿨습니다. 이어 단일 샤드 영향, 전체 영향, 환경 변경, 실패, 수정·재시도를 수행했습니다.</p><div class="scroll"><table><tr><th>단계</th><th>결과</th><th>실행</th><th>재사용</th><th>미시작</th><th>테스트 구간 ms</th><th>Click 요청 ms</th><th>요청 내 비테스트 구간 ms</th><th>실행 순서</th><th>대조 일치</th><th>계보</th></tr>' + ''.join(steps) + '</table></div></section>'
            '<section><h2>실제 저장소 테스트 번들 참조</h2>' + reference_html + '</section>'
            '<section><h2>승인 통제 · 출처</h2><p>각 Click 계약에서 승인 전 수정, 같은 턴 승인, 틀린 ID를 실제로 거부한 뒤 다른 fixture 턴에서 정확한 ID로 승인했습니다. 보고서와 추가 전체 실행은 승인·실행·재사용 권한을 만들지 않습니다.</p><pre>' + escape(json.dumps({"schema_version": result["version"], "source": result["source"], "unit": result["unit"], "engine": result["engine"], "environment": result["environment"], "conditions": result["conditions"], "controls": last["controls"], "limitations": result["limitations"]}, ensure_ascii=False, indent=2)) + '</pre></section></html>')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--workload-rounds", type=int, default=DEFAULT_WORKLOAD_ROUNDS)
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    parser.add_argument("--mode", choices=("evidence", "guarded"), default="evidence")
    parser.add_argument("--output", type=Path, help="Write a new local JSON file (never overwrite).")
    parser.add_argument("--guarded-workflow", action="store_true", help="Compare baseline, Guarded defaults, and explicit reuse through completed contracts.")
    parser.add_argument("--scoped-session", action="store_true",
                        help="Paired Evidence file-input policy sessions; use --warmups 0. Includes setup, failures and final full audits.")
    parser.add_argument("--html-output", type=Path, help="New offline three-configuration workflow report (requires --guarded-workflow).")
    parser.add_argument("--repository-bundle", action="store_true",
                        help="Also run one non-warmup reference of the committed repository test bundle.")
    parser.add_argument(
        "--repository-command-timeout",
        type=float,
        default=DEFAULT_REPOSITORY_COMMAND_TIMEOUT_SECONDS,
        help="Per repository parent or shard command timeout in seconds.",
    )
    args = parser.parse_args(argv)
    try:
        if args.scoped_session and (args.guarded_workflow or args.scenario or args.html_output
                                    or args.repository_bundle or args.warmups != 0 or args.mode != "evidence"):
            raise ValueError("scoped-session-requires-fixed-evidence-workflow-and-zero-warmups")
        if (args.html_output and not args.guarded_workflow
                or args.repository_bundle and not args.guarded_workflow
                or args.guarded_workflow and args.scenario):
            raise ValueError("workflow-requires-fixed-scenarios; html-requires-workflow")
        for target in (args.output, args.html_output):
            if target is not None and target.exists():
                raise ValueError("output-already-exists")
        if args.scoped_session:
            result = run_scoped_session_benchmark(iterations=args.iterations, workload_rounds=args.workload_rounds)
        elif args.guarded_workflow:
            result = run_guarded_workflow_benchmark(iterations=args.iterations, warmups=args.warmups, workload_rounds=args.workload_rounds)
            if args.repository_bundle:
                result["repository_reference"] = run_repository_bundle_reference(
                    command_timeout_seconds=args.repository_command_timeout
                )
            if not workflow_report_is_valid(result):
                raise RuntimeError("workflow-report-validation-failed")
        else:
            result = run_benchmark(iterations=args.iterations, warmups=args.warmups,
                                   workload_rounds=args.workload_rounds, scenarios=tuple(args.scenario or SCENARIOS), mode=args.mode)
        # JSON escapes preserve Unicode values on legacy Windows stdout,
        # while explicitly UTF-8 output files remain human-readable.
        encoded = json.dumps(result, ensure_ascii=args.output is None,
                             sort_keys=True, allow_nan=False)
        if args.output:
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
            summary = {"output": str(args.output), "samples": len(result["samples"]), "summaries": result["summaries"]}
            if args.guarded_workflow:
                summary["full_audit_matches"] = sum(step["audit_matches"] for sample in result["samples"] for arm in sample["arms"].values() for step in arm["steps"])
                summary["last_explicit_steps"] = [{"scenario": step["scenario"], "status": step["validation"]["status"],
                    "executed": step["validation"]["executed_source_count"], "reused": step["validation"]["reused_source_count"]}
                    for step in result["samples"][-1]["arms"]["explicit-reuse"]["steps"]]
            print(json.dumps(summary))
        else:
            print(encoded)
        if args.html_output:
            with args.html_output.open("x", encoding="utf-8") as handle:
                handle.write(workflow_report_html(result))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"benchmark failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
