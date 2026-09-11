#!/usr/bin/env python3
"""Measure real host token usage for one fixed task with and without Click.

The same multi-step task runs in two headless Claude Code sessions on two
copies of one synthetic repository: ``baseline`` (variant ``N``, no Click) and
``improved`` (variant ``B2``, the Click plugin loaded from a build directory).
Every session's ``stream-json`` transcript is kept, the host's own usage
records become the ``click-task-efficiency-evaluation`` v1 input read by
``benchmarks/task_efficiency.py``, and that adapter produces the public
ratio-only projection the dashboard imports.

Two run kinds are measured in order. ``first-use`` starts from a pristine copy
with no Click state. ``prepared-repeat`` continues in the same copy, with the
same arm's Click data directory, on five further steps: this is where reuse
can happen. Nothing here decides or estimates reuse; the numbers are the
host's usage counters and the audit of the finished workspace.

The measured task edits one component and its test per step and requires the
full suite to pass after each step. The suite is a sharded broad check
(``.click/evidence-shards.json`` is committed), so with Click an unchanged
module's shard can be reused while the changed one reruns.

Costs are the caller's: sessions are billed to the account the ``claude`` CLI
is logged into. Use ``--budget`` (per session, USD) to cap each session.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks import task_efficiency  # noqa: E402

SCENARIO = "limit-steps-six-module-sharded-suite"
ACCEPTANCE_VERSION = "limit-steps-v1"
MODULES = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")
INITIAL_LIMITS = {"alpha": 10, "beta": 20, "gamma": 30, "delta": 40, "epsilon": 50, "zeta": 60}
# (module, old, new) — five steps per run kind, applied in this order.
STEPS: dict[str, list[tuple[str, int, int]]] = {
    "first-use": [("alpha", 10, 12), ("beta", 20, 23), ("gamma", 30, 31), ("delta", 40, 44), ("epsilon", 50, 55)],
    "prepared-repeat": [("zeta", 60, 63), ("alpha", 12, 14), ("beta", 23, 25), ("gamma", 31, 35), ("delta", 44, 46)],
}
RUN_KINDS = ("first-use", "prepared-repeat")
ARMS = ("baseline", "improved")
VARIANT = {"baseline": "N", "improved": "B2"}
SUITE = ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_BUDGET_USD = 6.0
DEFAULT_ROUNDS = 60000
DEFAULT_SESSION_TIMEOUT = 1800
_LIMIT = re.compile(r"^LIMIT = (\d+)$", re.MULTILINE)
_CLICK_RESULT = re.compile(r"\[Click 결과\][^\n]*")
_TEST_COMMAND = re.compile(r"(?:python3?|py)\s+-m\s+(?:unittest|pytest)\b|\bpytest\b")


# --------------------------------------------------------------------------
# Fixture
# --------------------------------------------------------------------------

def _component_source(name: str, limit: int) -> str:
    return (
        f'"""Component {name}."""\n\n'
        f"LIMIT = {limit}\n\n\n"
        "def describe() -> str:\n"
        f'    return f"{name}:{{LIMIT}}"\n\n\n'
        "def window(count: int) -> list[int]:\n"
        '    """Return the first ``count`` values below LIMIT."""\n'
        "    return [value for value in range(min(count, LIMIT))]\n"
    )


def _test_source(name: str, limit: int) -> str:
    return (
        "import sys\n"
        "import unittest\n\n"
        "from implementation import ROUNDS, checksum\n"
        f"from component_{name} import LIMIT, describe, window\n\n\n"
        "class Check(unittest.TestCase):\n"
        "    def test_limit(self):\n"
        f"        self.assertEqual(LIMIT, {limit})\n\n"
        "    def test_describe(self):\n"
        f'        self.assertEqual(describe(), "{name}:{limit}")\n\n'
        "    def test_window(self):\n"
        "        self.assertEqual(window(3), [0, 1, 2])\n"
        "        self.assertEqual(len(window(LIMIT + 5)), LIMIT)\n\n"
        "    def test_checksum(self):\n"
        f'        digest = checksum(b"{name}", ROUNDS)\n'
        f'        sys.stderr.write(f"[{name}] checksum {{digest[:16]}} over {{ROUNDS}} rounds\\n")\n'
        "        self.assertEqual(len(digest), 64)\n\n"
        "    def test_report(self):\n"
        "        for index in range(8):\n"
        f'            sys.stderr.write(f"[{name}] case {{index}}: window={{window(index)[:3]}} limit={{LIMIT}}\\n")\n'
        "        self.assertTrue(all(value < LIMIT for value in window(LIMIT)))\n"
    )


def build_fixture(root: Path, *, rounds: int = DEFAULT_ROUNDS) -> None:
    """Write and commit the synthetic repository at ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / ".click").mkdir(exist_ok=True)
    command = " ".join(SUITE)
    (root / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    (root / "README.md").write_text(
        "# Component fixture\n\n"
        "Six small components, each with its own test module under `tests/`.\n\n"
        f"Run the full test suite with:\n\n```\n{command}\n```\n\n"
        "The full suite must pass after every change.\n",
        encoding="utf-8",
    )
    (root / "CLAUDE.md").write_text(
        f"Run the full test suite with `{command}` after every change and make sure it passes.\n",
        encoding="utf-8",
    )
    (root / "implementation.py").write_text(
        "import hashlib\n\n"
        f"ROUNDS = {rounds}\n\n\n"
        "def checksum(seed: bytes, rounds: int) -> str:\n"
        '    return hashlib.pbkdf2_hmac("sha256", seed, b"click-token-ab", rounds).hex()\n',
        encoding="utf-8",
    )
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    for name in MODULES:
        (root / f"component_{name}.py").write_text(_component_source(name, INITIAL_LIMITS[name]), encoding="utf-8")
        (root / "tests" / f"test_{name}.py").write_text(_test_source(name, INITIAL_LIMITS[name]), encoding="utf-8")
    shards = {"version": 1, "entries": [{
        "checks": [SUITE], "inventory": ["tests/test*.py"],
        "shards": [{"id": name, "checks": [["python3", "-m", "unittest", f"tests.test_{name}", "-v"]],
                    "covers": [f"tests/test_{name}.py"]} for name in MODULES]}]}
    (root / ".click" / "evidence-shards.json").write_text(json.dumps(shards, indent=2) + "\n", encoding="utf-8")
    git = ["git", "-c", "user.name=Click Benchmark", "-c", "user.email=benchmark@example.invalid",
           "-c", "commit.gpgsign=false"]
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "Fixture with committed shard map"]):
        subprocess.run([*git, *args], cwd=root, env=env, check=True, capture_output=True)


def apply_steps(root: Path, steps: list[tuple[str, int, int]]) -> None:
    """Apply the canonical edits of ``steps`` without a model (reset or reference)."""
    for name, old, new in steps:
        component = root / f"component_{name}.py"
        component.write_text(component.read_text(encoding="utf-8").replace(f"LIMIT = {old}\n", f"LIMIT = {new}\n"), encoding="utf-8")
        test = root / "tests" / f"test_{name}.py"
        test.write_text(
            test.read_text(encoding="utf-8")
            .replace(f"assertEqual(LIMIT, {old})", f"assertEqual(LIMIT, {new})")
            .replace(f'"{name}:{old}"', f'"{name}:{new}"'),
            encoding="utf-8",
        )


# Without this the model applies every edit in one shell command and verifies
# once, which is a workload with nothing to reuse. The rule is identical in
# both arms, so it changes what is measured, not who wins.
STEP_RULES = (
    "Rules you must follow:",
    "- Do one step at a time. Never combine two steps into one command or one message.",
    "- Make every source edit with the file-editing tool, not with a shell command,"
    " and never combine an edit and a test run in one command.",
    "- After finishing a step, run the project's full test suite (README.md names the command)"
    " and report its result line before you start the next step.",
    "- Fix any failure you caused before moving on.",
    "- Do not ask questions and do not commit.",
)
# Appended to both arms when comparing "Click used" against "no Click": the
# baseline session has no such command and runs the suite directly.
CHECK_DIRECTIVE = (
    "- If a `click-gate` command is available in this session, submit the test suite through"
    " `click-gate verify` instead of running it directly; otherwise run the test command itself."
)


def task_prompt(steps: list[tuple[str, int, int]], *, directed: bool = False) -> str:
    rules = list(STEP_RULES)
    if directed:
        rules.append(CHECK_DIRECTIVE)
    lines = ["Work in this repository (the current directory). Complete the steps below in order.",
             "", *rules, ""]
    for index, (name, old, new) in enumerate(steps, 1):
        lines.append(
            f"Step {index}: In component_{name}.py change LIMIT from {old} to {new}, "
            f"and update tests/test_{name}.py so it expects {new}."
        )
    lines += ["", 'When every step is done, reply with exactly one line per step: "step N: passed" or "step N: failed".']
    return "\n".join(lines)


def audit(root: Path, steps: list[tuple[str, int, int]]) -> dict[str, Any]:
    """Fixed acceptance: every LIMIT and test literal is as requested and the suite passes."""
    results = []
    for name, _old, new in steps:
        component = (root / f"component_{name}.py").read_text(encoding="utf-8")
        match = _LIMIT.search(component)
        test = (root / "tests" / f"test_{name}.py").read_text(encoding="utf-8")
        results.append({
            "module": name, "expected": new,
            "limit_ok": bool(match) and int(match.group(1)) == new,
            "test_ok": f"assertEqual(LIMIT, {new})" in test and f'"{name}:{new}"' in test,
        })
    run = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"], cwd=root,
                         env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True)
    suite_passed = run.returncode == 0
    passed = suite_passed and all(row["limit_ok"] and row["test_ok"] for row in results)
    projection = {"acceptance_version": ACCEPTANCE_VERSION, "suite_passed": suite_passed,
                  "steps": [{"module": r["module"], "expected": r["expected"], "ok": r["limit_ok"] and r["test_ok"]} for r in results]}
    return {
        "passed": passed, "suite_passed": suite_passed, "steps": results,
        "completion_digest": hashlib.sha256(json.dumps(projection, sort_keys=True).encode()).hexdigest(),
        "suite_output_tail": (run.stdout + run.stderr)[-600:],
    }


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------

def user_plugin_settings() -> dict[str, Any]:
    """Settings that disable every globally enabled plugin for both arms."""
    path = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "settings.json"
    try:
        enabled = json.loads(path.read_text(encoding="utf-8")).get("enabledPlugins") or {}
    except (OSError, ValueError, AttributeError):
        enabled = {}
    return {"enabledPlugins": {key: False for key in enabled if isinstance(key, str)}}


def session_environment(data_dir: Path) -> dict[str, str]:
    """A child CLI environment: no nested-session markers, isolated Click state."""
    environment = {
        key: value for key, value in os.environ.items()
        if not (key.startswith("CLAUDE") or key in {"ANTHROPIC_BASE_URL", "PLUGIN_DATA", "CLICK_CONFIG_HOME"})
        or key == "CLAUDE_CONFIG_DIR"
    }
    environment["PLUGIN_DATA"] = str(data_dir / "plugin-data")
    environment["CLICK_CONFIG_HOME"] = str(data_dir / "config")
    return environment


def session_command(claude_bin: str, prompt: str, *, model: str, budget: float, plugin_dir: Path | None,
                    settings: dict[str, Any]) -> list[str]:
    argv = [claude_bin, "-p", prompt, "--model", model, "--max-budget-usd", f"{budget:g}",
            "--output-format", "stream-json", "--verbose", "--dangerously-skip-permissions",
            "--no-session-persistence", "--settings", json.dumps(settings, separators=(",", ":"))]
    if plugin_dir is not None:
        argv += ["--plugin-dir", str(plugin_dir)]
    return argv


def run_session(argv: list[str], *, cwd: Path, environment: dict[str, str], transcript: Path,
                timeout: float) -> dict[str, Any]:
    """Run one headless session; the task boundary is launch to exit."""
    transcript.parent.mkdir(parents=True, exist_ok=True)
    stderr_path = transcript.with_suffix(".stderr")
    started = int(time.time() * 1000)
    timed_out = False
    with transcript.open("wb") as out, stderr_path.open("wb") as err:
        process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            exit_code = process.wait()
            timed_out = True
    finished = int(time.time() * 1000)
    return {"exit_code": exit_code, "timed_out": timed_out, "started_at_ms": started, "finished_at_ms": finished,
            "transcript": str(transcript)}


# --------------------------------------------------------------------------
# Transcript parsing
# --------------------------------------------------------------------------

def _tool_result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    return ""


def parse_transcript(path: Path) -> dict[str, Any]:
    """Host usage for one session plus its tool activity.

    A response is streamed as one assistant event per content block, all
    sharing an id. Their input counters are the response's own and sum to the
    session total, but their output counter is a snapshot taken before the
    response finished. The session's authoritative total is the one the host
    reports at the end, so that is what the evaluation uses; the per-response
    input numbers are kept for analysis only.
    """
    responses: dict[str, dict[str, int]] = {}
    order: list[str] = []
    seen_tools: set[str] = set()
    commands: list[str] = []
    tool_calls = 0
    click_results: list[str] = []
    final: dict[str, Any] | None = None
    init: dict[str, Any] | None = None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "system" and event.get("subtype") == "init":
                init = event
            elif kind == "assistant":
                message = event.get("message") or {}
                identifier = message.get("id")
                usage = message.get("usage") or {}
                if isinstance(identifier, str) and task_efficiency._SAFE_ID.fullmatch(identifier):
                    details = usage.get("output_tokens_details") or {}
                    current = {
                        "fresh": int(usage.get("input_tokens") or 0),
                        "cache_write": int(usage.get("cache_creation_input_tokens") or 0),
                        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
                        "output": int(usage.get("output_tokens") or 0),
                        "reasoning": int(details.get("thinking_tokens") or 0) if isinstance(details, dict) else 0,
                    }
                    if identifier not in responses:
                        order.append(identifier)
                        responses[identifier] = current
                    else:  # the same response streams once per content block
                        responses[identifier] = {key: max(value, current[key]) for key, value in responses[identifier].items()}
                for block in message.get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    # Blocks repeat with their response; each call has one id.
                    call_id = str(block.get("id") or "")
                    if call_id and call_id in seen_tools:
                        continue
                    if call_id:
                        seen_tools.add(call_id)
                    tool_calls += 1
                    if block.get("name") == "Bash":
                        commands.append(str((block.get("input") or {}).get("command", "")))
            elif kind == "user":
                for block in (event.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        click_results += _CLICK_RESULT.findall(_tool_result_text(block))
            elif kind == "result":
                final = event
    reported = (final or {}).get("usage") if isinstance((final or {}).get("usage"), dict) else {}
    details = reported.get("output_tokens_details")
    totals = {
        "fresh": int(reported.get("input_tokens") or 0),
        "cache_write": int(reported.get("cache_creation_input_tokens") or 0),
        "cache_read": int(reported.get("cache_read_input_tokens") or 0),
        "output": int(reported.get("output_tokens") or 0),
        "reasoning": int((details or {}).get("thinking_tokens") or 0) if isinstance(details, dict) else 0,
    }
    totals["input_total"] = totals["fresh"] + totals["cache_write"] + totals["cache_read"]
    totals["total"] = totals["input_total"] + totals["output"]
    events = []
    if final is not None and totals["total"] > 0:
        events.append({
            "event_id": "task-total", "response_id": str((final or {}).get("session_id") or "task"),
            "sequence": 0,
            "input_tokens": totals["input_total"], "output_tokens": totals["output"],
            "cached_input_tokens": totals["cache_read"],
            "reasoning_output_tokens": min(totals["reasoning"], totals["output"]),
        })
    streamed_input = sum(usage["fresh"] + usage["cache_write"] + usage["cache_read"]
                         for usage in responses.values())
    verify_calls = sum("click-gate verify" in command for command in commands)
    return {
        "events": events, "totals": totals, "responses": len(order), "tool_calls": tool_calls,
        "streamed_input_tokens": streamed_input,
        "bash_commands": len(commands),
        "raw_test_runs": sum(bool(_TEST_COMMAND.search(command)) and "click-gate" not in command for command in commands),
        "click_gate_calls": sum("click-gate" in command for command in commands),
        "click_verify_calls": verify_calls,
        "click_result_lines": click_results,
        "result": {key: final.get(key) for key in ("subtype", "is_error", "num_turns", "duration_ms", "duration_api_ms",
                                                    "total_cost_usd", "stop_reason", "terminal_reason", "usage",
                                                    "modelUsage", "permission_denials", "result")} if final else None,
        "init": {key: init.get(key) for key in ("model", "claude_code_version", "plugins", "skills", "permissionMode")} if init else None,
    }


# --------------------------------------------------------------------------
# Evaluation records
# --------------------------------------------------------------------------

def run_record(*, arm: str, model: str, session: dict[str, Any], parsed: dict[str, Any], accepted: dict[str, Any]) -> dict[str, Any]:
    final = parsed.get("result") or {}
    if session["timed_out"]:
        completion = "cancelled"
    elif final and final.get("subtype") == "success" and not final.get("is_error"):
        completion = "completed"
    elif final:
        completion = "failed"
    else:
        completion = "incomplete"
    if completion == "completed" and not accepted["passed"]:
        completion = "failed"
    return {
        "arm": arm, "variant": VARIANT[arm], "model": model,
        "task": {
            "boundary_status": "complete" if final else "incomplete",
            "completion_status": completion,
            "acceptance_status": "passed" if accepted["passed"] else "failed",
            "started_at_ms": session["started_at_ms"], "finished_at_ms": session["finished_at_ms"],
            "completion_digest": accepted["completion_digest"], "acceptance_version": ACCEPTANCE_VERSION,
        },
        "usage": {
            "version": 1, "scope_status": "complete" if final else "incomplete", "covers_task_boundary": bool(final),
            "input_semantics": "includes-cached", "output_semantics": "includes-reasoning",
            "counter_semantics": "incremental", "events": parsed["events"],
        },
        "observed": {"tool_calls": parsed["tool_calls"], "post_failure_calls_before_mutation": None,
                     "repair_cycles": None, "model_round_trips": parsed["responses"]},
        "user_intervention": {kind: 0 for kind in task_efficiency.INTERVENTION_KINDS} | {"observed_duration_ms": 0},
        "host_result": {**final, "exit_code": session["exit_code"], "timed_out": session["timed_out"]},
        "usage_totals": parsed["totals"],
        "activity": {key: parsed[key] for key in ("bash_commands", "raw_test_runs", "click_gate_calls", "click_verify_calls", "click_result_lines")},
        "acceptance": {key: accepted[key] for key in ("passed", "suite_passed", "steps")},
        "transcript": session["transcript"],
    }


def evaluation(pairs: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    return {"kind": task_efficiency.INTERNAL_KIND, "version": task_efficiency.INTERNAL_VERSION,
            "generated_at": int(time.time()), "comparisons": pairs, "evaluation_context": context}


def summary_lines(internal: dict[str, Any]) -> list[str]:
    lines = []
    for pair in internal["comparisons"]:
        result = task_efficiency.evaluate_pair(pair)
        lines.append(f"## {pair['id']} ({pair['run_kind']}) — {result['status']}"
                     + (f" ({result['reason']})" if result.get("reason") else ""))
        lines.append("| arm | input total | fresh | cache write | cache read | output | total | responses | tool calls | test runs | click verify | cost USD | wall s |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for arm in ("baseline", "improved"):
            run = pair[arm]
            totals, activity, host = run["usage_totals"], run["activity"], run["host_result"]
            wall = (run["task"]["finished_at_ms"] - run["task"]["started_at_ms"]) / 1000
            lines.append(f"| {arm} ({run['variant']}) | {totals['input_total']:,} | {totals['fresh']:,} | {totals['cache_write']:,} | "
                         f"{totals['cache_read']:,} | {totals['output']:,} | {totals['total']:,} | {run['observed']['model_round_trips']} | "
                         f"{run['observed']['tool_calls']} | {activity['raw_test_runs']} | {activity['click_verify_calls']} | "
                         f"{host.get('total_cost_usd') if host.get('total_cost_usd') is not None else '-'} | {wall:.0f} |")
        ratio = result.get("token_savings_ratio")
        time_ratio = result.get("task_completion_time_savings_ratio")
        lines.append(f"token_savings_ratio: {ratio:+.4f} ({ratio * 100:+.1f}%)" if ratio is not None else f"token_savings_ratio: unmeasured ({result.get('token_reason')})")
        lines.append(f"task_completion_time_savings_ratio: {time_ratio:+.4f}" if time_ratio is not None else f"task time: unmeasured ({result.get('task_time_reason')})")
        for arm in ("baseline", "improved"):
            for line in pair[arm]["activity"]["click_result_lines"]:
                lines.append(f"  {arm}: {line}")
        lines.append("")
    return lines


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def run(args: argparse.Namespace) -> Path:
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    plugin_dir = Path(args.plugin_dir).resolve()
    if not (plugin_dir / ".claude-plugin" / "plugin.json").is_file():
        raise SystemExit(f"not a Claude Code plugin directory: {plugin_dir}")
    settings = user_plugin_settings()
    context = {
        "scenario": SCENARIO, "model": args.model, "budget_usd_per_session": args.budget, "rounds": args.rounds,
        "claude_bin": args.claude_bin, "plugin_dir": str(plugin_dir), "repetitions": args.repeat,
        "plugin_version": json.loads((plugin_dir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version"),
        "directed": bool(args.directed),
        "prompt_digests": {kind: hashlib.sha256(task_prompt(STEPS[kind], directed=args.directed).encode()).hexdigest()
                           for kind in RUN_KINDS},
        "disabled_plugins": sorted(settings["enabledPlugins"]),
    }
    pairs: list[dict[str, Any]] = []
    for repetition in range(1, args.repeat + 1):
        workspaces: dict[str, Path] = {}
        data_dirs: dict[str, Path] = {}
        for arm in ARMS:
            workspaces[arm] = output / f"rep{repetition}" / arm / "repository"
            data_dirs[arm] = output / f"rep{repetition}" / arm / "click-data"
            build_fixture(workspaces[arm], rounds=args.rounds)
        for kind in RUN_KINDS:
            if kind not in args.run_kinds:
                continue
            prompt = task_prompt(STEPS[kind], directed=args.directed)
            records: dict[str, dict[str, Any]] = {}
            for arm in ARMS:
                if arm not in args.arms:
                    continue
                transcript = output / f"rep{repetition}" / arm / f"{kind}.jsonl"
                argv = session_command(args.claude_bin, prompt, model=args.model, budget=args.budget,
                                       plugin_dir=plugin_dir if arm == "improved" else None, settings=settings)
                print(f"[{time.strftime('%H:%M:%S')}] rep{repetition} {kind} {arm}: session start", flush=True)
                session = run_session(argv, cwd=workspaces[arm], environment=session_environment(data_dirs[arm]),
                                      transcript=transcript, timeout=args.session_timeout)
                parsed = parse_transcript(transcript)
                accepted = audit(workspaces[arm], STEPS[kind])
                records[arm] = run_record(arm=arm, model=args.model, session=session, parsed=parsed, accepted=accepted)
                totals = parsed["totals"]
                print(f"[{time.strftime('%H:%M:%S')}] rep{repetition} {kind} {arm}: exit={session['exit_code']} "
                      f"accepted={accepted['passed']} responses={parsed['responses']} tokens={totals['total']:,} "
                      f"cost={(parsed.get('result') or {}).get('total_cost_usd')}", flush=True)
                if not accepted["passed"]:
                    # The next run kind must start from the same code in both arms.
                    apply_steps(workspaces[arm], [(name, _current_limit(workspaces[arm], name), new) for name, _old, new in STEPS[kind]])
            if all(arm in records for arm in ARMS):
                pairs.append({"id": f"rep{repetition}-{kind}" + ("-directed" if args.directed else ""), "baseline_variant": "N", "improved_variant": "B2",
                              "scenario": SCENARIO, "run_kind": kind, "runtime_mode": "evidence",
                              "baseline": records["baseline"], "improved": records["improved"]})
            for arm, record in records.items():
                (output / f"rep{repetition}" / arm / f"{kind}-run.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    internal = evaluation(pairs, context)
    (output / "internal.json").write_text(json.dumps(internal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    public = task_efficiency.public_projection(internal)
    (output / "public.json").write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = summary_lines(internal)
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return output


def _current_limit(root: Path, name: str) -> int:
    match = _LIMIT.search((root / f"component_{name}.py").read_text(encoding="utf-8"))
    return int(match.group(1)) if match else INITIAL_LIMITS[name]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", required=True, help="directory for workspaces, transcripts and results")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD, help="USD cap per session")
    parser.add_argument("--plugin-dir", default=str(ROOT / "dist" / "claude"))
    parser.add_argument("--claude-bin", default=os.environ.get("CLAUDE_CODE_EXECPATH") or "claude")
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS, help="pbkdf2 rounds per checksum test")
    parser.add_argument("--repeat", type=int, default=1, help="independent repetitions of the whole pair set")
    parser.add_argument("--arms", nargs="+", choices=ARMS, default=list(ARMS))
    parser.add_argument("--run-kinds", nargs="+", choices=RUN_KINDS, default=list(RUN_KINDS))
    parser.add_argument("--session-timeout", type=float, default=DEFAULT_SESSION_TIMEOUT, help="seconds per session")
    parser.add_argument("--directed", action="store_true",
                        help="add the same check directive to both arms, so the comparison is "
                             "'checks through click-gate' against 'checks run directly'")
    args = parser.parse_args(argv)
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
