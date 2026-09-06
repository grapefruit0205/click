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
WORKFLOW_STEPS = ("first-run", "unrelated-code", "related-code", "environment", "failure", "retry", "unchanged")
DEFAULT_ITERATIONS = 3
DEFAULT_WORKLOAD_ROUNDS = 40_000
MAX_ITERATIONS = 10
MAX_WORKLOAD_ROUNDS = 1_000_000


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
        if configuration not in {"legacy", *WORKFLOW_CONFIGS}:
            raise ValueError("invalid-workflow-configuration")
        self.configuration = configuration
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
        if configuration in {"legacy", "explicit-reuse"}:
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
        if configuration != "legacy":
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
            self.environment["CLICK_BENCHMARK_VARIANT"] = "changed"
            return
        paths = {"unrelated-code": ("component_beta.py", 2), "related-code": ("component_alpha.py", 2),
                 "failure": ("component_alpha.py", -1), "retry": ("component_alpha.py", 3)}
        path, value = paths[scenario]
        self.sequence += 1
        event = {"tool_name": "apply_patch", "tool_use_id": f"workflow-change-{self.sequence}",
                 "tool_input": {"patch": f"*** fixture edit {path}: VALUE = {value} ***"}}
        if self.configuration != "baseline":
            admission = self._hook("pre-tool", {**event, "hook_event_name": "PreToolUse"})
            if admission.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
                raise RuntimeError("workflow-mutation-denied")
        self._write(path, f"VALUE = {value}\n")
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
            self.environment["CLICK_BENCHMARK_VARIANT"] = "changed"
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
        return {"wall_ms": wall_ms, "exit_code": code, "status": measured["status"],
                "executed_source_count": summary["executed_source_count"],
                "reused_source_count": summary["authoritative_reuse_count"],
                "not_run_source_count": summary["not_run_source_count"],
                "estimated_avoided_ms": summary["estimated_avoided_ms"],
                "estimated_source_count": summary["estimated_source_count"],
                "request_measurement_scope": "driver-preflight-through-runner-return",
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
                "request_measurement_scope": "driver-command-dispatch-through-return"}


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


def run_guarded_workflow_benchmark(*, iterations: int = DEFAULT_ITERATIONS, warmups: int = 1,
                                   workload_rounds: int = DEFAULT_WORKLOAD_ROUNDS) -> dict[str, Any]:
    """Three independent configurations, each completing A before approving B.

    The baseline never enters Click. Default means Guarded selected explicitly,
    with no optional dependency, reuse, or shard configuration. Native caches
    are not flushed. Full audits are extra work and never manufacture evidence.
    """
    if not 1 <= iterations <= MAX_ITERATIONS or not 0 <= warmups <= MAX_ITERATIONS or not 1 <= workload_rounds <= MAX_WORKLOAD_ROUNDS:
        raise ValueError("invalid-workflow-configuration")
    samples = []
    latest_snapshot = None
    for index in range(warmups + iterations):
        order = list(WORKFLOW_CONFIGS[index % 3:] + WORKFLOW_CONFIGS[:index % 3])
        arms = {}
        with tempfile.TemporaryDirectory(prefix="click-guarded-workflow-") as directory:
            for config in order:
                started = time.perf_counter_ns()
                fixture = Fixture(Path(directory) / config, workload_rounds, "guarded", configuration=config)
                setup_ms = (time.perf_counter_ns() - started) / 1_000_000
                steps, successor_receipt = [], None
                for position, scenario in enumerate(WORKFLOW_STEPS):
                    started = time.perf_counter_ns()
                    if config != "baseline" and scenario in {"unrelated-code", "related-code", "environment", "failure"}:
                        fixture.begin_contract(f"{chr(65 + position)} · {scenario}")
                    fixture.workflow_change(scenario)
                    transition_ms = (time.perf_counter_ns() - started) / 1_000_000
                    result = fixture.full("parent-suite") if config == "baseline" else fixture.verify()
                    snapshot = result.pop("snapshot", None)
                    if config == "explicit-reuse" and scenario == "unrelated-code":
                        latest_snapshot = snapshot
                    # Same-state full validation after EVERY step, including the
                    # expected failure. Its time is explicitly outside primary
                    # request measurements, and it warms native caches uniformly.
                    audit = fixture.full("parent-suite")
                    matches = result["exit_code"] == audit["exit_code"] and result["status"] == audit["status"]
                    expected = "failed" if scenario == "failure" else "passed"
                    if not matches or result["status"] != expected:
                        raise RuntimeError(f"workflow-full-audit-mismatch:{config}:{scenario}")
                    steps.append({"scenario": scenario, "contract_id": None if config == "baseline" else fixture.state()["contract_id"],
                                  "input_digest": fixture.input_digest(), "transition_ms": transition_ms,
                                  "validation": result, "audit": audit, "audit_matches": matches})
                    if config != "baseline" and scenario == "unrelated-code":
                        successor_receipt = fixture.receipt()
                final_receipt = None if config == "baseline" else fixture.receipt()
                arms[config] = {"steps": steps, "controls": fixture.controls, "setup_ms": setup_ms,
                                "validation_wall_ms": sum(step["validation"]["wall_ms"] for step in steps),
                                "transition_ms": sum(step["transition_ms"] for step in steps),
                                "audit_wall_ms": sum(step["audit"]["wall_ms"] for step in steps),
                                "successor_receipt": successor_receipt, "final_receipt": final_receipt,
                                "final_input_digest": fixture.input_digest()}
            for position in range(len(WORKFLOW_STEPS)):
                if len({arm["steps"][position]["input_digest"] for arm in arms.values()}) != 1:
                    raise RuntimeError("workflow-inputs-not-equivalent")
        samples.append({"iteration": index, "warmup": index < warmups, "order": order, "arms": arms})
    summaries = []
    for config in WORKFLOW_CONFIGS[1:]:
        selected = [sample for sample in samples if not sample["warmup"]]
        deltas = [comparison_delta(sample["arms"]["baseline"]["validation_wall_ms"], sample["arms"][config]["validation_wall_ms"]) for sample in selected]
        summaries.append({"configuration": config, "samples": len(selected),
                          "baseline_wall_ms": distribution([sample["arms"]["baseline"]["validation_wall_ms"] for sample in selected]),
                          "validation_wall_ms": distribution([sample["arms"][config]["validation_wall_ms"] for sample in selected]),
                          "delta_ms": distribution([value["delta_ms"] for value in deltas]),
                          "delta_percent": distribution([value["delta_percent"] for value in deltas if value["delta_percent"] is not None]),
                          "transition_ms": distribution([sample["arms"][config]["transition_ms"] for sample in selected]),
                          "setup_ms": distribution([sample["arms"][config]["setup_ms"] for sample in selected]),
                          "extra_audit_ms": distribution([sample["arms"][config]["audit_wall_ms"] for sample in selected])})
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False)
    source_hash = hashlib.sha256()
    for path in sorted([*GATE.parent.glob("*.py"), Path(__file__).resolve()]):
        source_hash.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return {"version": 3, "kind": "click-guarded-workflow-benchmark",
            "engine": {"version": json.loads((ROOT / ".codex-plugin/plugin.json").read_text())["version"],
                       "commit": commit.stdout.strip() if commit.returncode == 0 else None,
                       "source_digest": source_hash.hexdigest(), "working_tree_modified": bool(dirty.stdout.strip()), "assurance": "unsigned-files-at-measurement"},
            "environment": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version()},
            "conditions": {"iterations": iterations, "warmups": warmups, "workload_rounds": workload_rounds,
                           "configurations": list(WORKFLOW_CONFIGS), "runtime_mode": "guarded-explicitly-selected; baseline-without-click",
                           "scope_equivalence": "same-two-unittest-files-and-code-at-every-step", "order": "rotating-three-arm-workflows",
                           "authority": "real-hooks-and-one-use-runner; distinct-scripted-fixture-approval-turns", "observer": "off",
                           "cache": "fresh-checkout-per-configuration-and-repetition; OS-cache-not-flushed; bytecode-disabled; full-audit-after-each-step",
                           "default_configuration": "no-optional-shards-dependencies-or-safe-change-policy; product-default-mode-remains-Evidence",
                           "explicit_configuration": "committed-two-shard-map-and-sibling-code-safe-change-policy-before-baseline",
                           "measurement": "sum-of-seven-driver-validation-requests; Click-includes-hook-and-runner-startup; negative-differences-preserved",
                           "additional_cost": "setup-includes-Git-and-first-scripted-approval; later-approval-and-mutation-transitions-and-audits-separate; receipt-export-and-report-serialization-excluded",
                           "failure": "one-expected-alpha-failure-and-repair-included-in-workflow-totals; final-full-suite-must-pass"},
            "samples": samples, "summaries": summaries, "dashboard_snapshot": latest_snapshot,
            "limitations": ["fixture-only-not-universal-safety-or-performance-proof", "scripted-approvals-not-human-decision-time",
                            "explicit-policy-is-owner-authority-not-observed-dependency-discovery", "known-Hook-surfaces-only; unsigned-receipts",
                            "OS-cache-and-scheduler-uncontrolled", "no-token-fee-or-total-development-time-conversion"]}


def workflow_report_html(result: dict[str, Any]) -> str:
    """Small offline measured report; no scripts, raw commands, or private paths."""
    escape = lambda value: html.escape(str(value), quote=True)
    rows = []
    for summary in result["summaries"]:
        rows.append("<tr>" + "".join(f"<td>{escape(value)}</td>" for value in (
            summary["configuration"], summary["samples"], round(summary["baseline_wall_ms"]["median"], 2),
            round(summary["validation_wall_ms"]["median"], 2), round(summary["delta_ms"]["median"], 2),
            round(summary["delta_percent"]["median"], 2), round(summary["transition_ms"]["median"], 2), round(summary["extra_audit_ms"]["median"], 2))) + "</tr>")
    last = result["samples"][-1]["arms"]["explicit-reuse"]
    steps = []
    for step in last["steps"]:
        value = step["validation"]
        origins = [item["reuse_origin"]["kind"] for item in value["batch"]["sources"] if item.get("reuse_origin")]
        steps.append("<tr>" + "".join(f"<td>{escape(item)}</td>" for item in (step["scenario"], value["status"], value["executed_source_count"], value["reused_source_count"], value["not_run_source_count"], round(value["wall_ms"], 2), step["audit_matches"], ", ".join(origins))) + "</tr>")
    return ('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Click · Guarded A→B 실측</title>'
            '<style>body{font:16px/1.6 system-ui;margin:40px auto;max-width:1100px;padding:0 24px;color:#18322d;background:#f6f8f4}table{border-collapse:collapse;width:100%;font-size:14px;background:white}th,td{padding:10px;border-bottom:1px solid #dce3dd;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere}h1{line-height:1.25}section{margin:32px 0}.scroll{overflow:auto}</style>'
            '<h1>시키지 않은 범위 확장은 억제하고,<br>유효한 검증은 다시 기다리지 않도록.</h1><p>독립 fixture의 실제 Hook·runner 측정입니다. 개발 세션의 승인이나 보편적 안전성 증명이 아닙니다.</p>'
            '<section><h2>같은 작업 · 세 구성</h2><p>기준은 Click 없는 전체 검증입니다. Click 기본은 Guarded를 명시적으로 선택하되 추가 재사용 설정이 없습니다. 명시적 구성은 기준 실행 전에 두 샤드와 무관 코드 변경 정책을 커밋했습니다.</p><p>일곱 검증 요청 합계의 반복 중앙값(ms). 양수 차이는 이 표본에서 빠름, 음수는 느림입니다. 최초 실행·실패·재시도도 포함하며 추가 비용과 사람의 판단 시간은 검증 시간 절감으로 합치지 않습니다.</p>'
            '<div class="scroll"><table><tr><th>구성</th><th>표본</th><th>기준 ms</th><th>Click ms</th><th>차이 ms</th><th>차이 %</th><th>별도 전환 ms</th><th>추가 전체 감사 ms</th></tr>' + ''.join(rows) + '</table></div></section>'
            '<section><h2>마지막 명시적 구성의 실제 흐름</h2><p>A 승인 → 성공 → B 별도 승인·beta 코드 수정 → alpha 재판정 → alpha 수정 시 재실행. 환경 변경은 재실행, 실패한 alpha는 수정 후 재시도했습니다.</p><div class="scroll"><table><tr><th>단계</th><th>결과</th><th>실행 그룹</th><th>재사용 그룹</th><th>미시작</th><th>요청 ms</th><th>전체 대조 일치</th><th>계보</th></tr>' + ''.join(steps) + '</table></div></section>'
            '<section><h2>승인 통제 · 출처</h2><p>각 Click 계약에서 승인 전 수정, 같은 턴 승인, 틀린 ID를 실제로 거부한 뒤 다른 fixture 턴에서 정확한 ID로 승인했습니다. 전체 검증 대조는 모든 구성의 모든 단계에서 수행했습니다. 그룹은 테스트 케이스 수가 아닙니다.</p><pre>' + escape(json.dumps({"engine": result["engine"], "environment": result["environment"], "conditions": result["conditions"], "controls": last["controls"], "limitations": result["limitations"]}, ensure_ascii=False, indent=2)) + '</pre></section></html>')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--workload-rounds", type=int, default=DEFAULT_WORKLOAD_ROUNDS)
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    parser.add_argument("--mode", choices=("evidence", "guarded"), default="evidence")
    parser.add_argument("--output", type=Path, help="Write a new local JSON file (never overwrite).")
    parser.add_argument("--guarded-workflow", action="store_true", help="Compare baseline, Guarded defaults, and explicit reuse through completed contracts.")
    parser.add_argument("--html-output", type=Path, help="New offline three-configuration workflow report (requires --guarded-workflow).")
    args = parser.parse_args(argv)
    try:
        if args.html_output and not args.guarded_workflow or args.guarded_workflow and args.scenario:
            raise ValueError("workflow-requires-fixed-scenarios; html-requires-workflow")
        for target in (args.output, args.html_output):
            if target is not None and target.exists():
                raise ValueError("output-already-exists")
        if args.guarded_workflow:
            result = run_guarded_workflow_benchmark(iterations=args.iterations, warmups=args.warmups, workload_rounds=args.workload_rounds)
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
