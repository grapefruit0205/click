"""Paired headless Claude Code sessions: arm A (Click plugin disabled) vs arm B
(Click enabled) on fresh copies of the same fixture with the same task.

Usage: run_ab.py --root DIR --model MODEL --order ABBAABBA [--rounds N] [--max-turns N] [--timeout S]
Each session writes: repo/, runs.log (one line per executed test module),
stream.jsonl (full transcript), result.json (final summary), stderr.log.
A summary.json is rewritten after every session so partial runs are usable.
"""
import argparse, json, os, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROMPT = (
    "The test suite in this repository fails. Run the full suite with "
    "`python3 -m unittest discover -s tests` to see the failures, then fix the bugs in the "
    "`ledger` package one module at a time. Rules: do not modify anything under `tests/`; "
    "after fixing each module, run the full test suite again to confirm the fix; when every "
    "test passes, run the full suite one final time and stop. Work only inside this repository. "
    "Finish with a short list of the files you changed."
)
TOOLS = ["Bash", "Read", "Edit", "MultiEdit", "Write", "Glob", "Grep"]

RAN = re.compile(r"Ran (\d+) tests? in ([0-9.]+)s")
CLICK_EXEC = re.compile(r"\[Click verification (\d+)/(\d+):")
# Click renders its result line in the host locale; v1.1.1 made English the
# default, so both spellings are accepted.
CLICK_TIME = re.compile(r"(?:이번 테스트 실행|this run's test time): ([0-9.]+)\s*(초|s|ms)")
CLICK_REUSED = re.compile(r"Click reused (\d+) current")
FULL_DISCOVER = re.compile(r"python3? -m unittest discover -s tests(?!.*-p)")


def parse_transcript(path):
    """Return the final result event and Bash tool calls paired with their outputs."""
    result, calls, pending = {}, [], {}
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "result":
            result = event
        content = event.get("message", {}).get("content", []) if isinstance(event.get("message"), dict) else []
        for block in content if isinstance(content, list) else []:
            if event.get("type") == "assistant" and block.get("type") == "tool_use" and block.get("name") == "Bash":
                call = {"id": block.get("id"), "command": block.get("input", {}).get("command", ""), "output": ""}
                pending[call["id"]] = call
                calls.append(call)
            if event.get("type") == "user" and block.get("type") == "tool_result" and block.get("tool_use_id") in pending:
                body = block.get("content")
                if isinstance(body, list):
                    body = "\n".join(part.get("text", "") for part in body if isinstance(part, dict))
                pending[block.get("tool_use_id")]["output"] = str(body or "")
    return result, calls


def derive_metrics(calls):
    """Count test executions from the transcript, independently of Click.

    Arm A: every `Ran N tests in Xs` is one unittest process; a full discover
    runs all four modules. Arm B: each `[Click verification i/n:...]` line is
    one executed shard child; `이번 테스트 실행` is Click's measured test time
    (parallel wall clock when children overlapped); a reused request executes 0.
    """
    executions = 0; test_wall = 0.0; full_runs = 0; click_requests = 0; click_reused_requests = 0
    click_result_lines = []
    for call in calls:
        out, cmd = call["output"], call["command"]
        if "click-gate" in cmd:
            click_requests += 1
            executed = len(CLICK_EXEC.findall(out))
            executions += executed
            for value, unit in CLICK_TIME.findall(out):
                test_wall += float(value) / (1000.0 if unit == "ms" else 1.0)
            if CLICK_REUSED.search(out) or executed == 0 and ("재사용" in out or "reused" in out):
                click_reused_requests += 1
            click_result_lines += [l for l in out.splitlines() if l.startswith(("[Click 결과]", "[Click result]", "Click reused"))]
            continue
        for tests, seconds in RAN.findall(out):
            test_wall += float(seconds)
            if FULL_DISCOVER.search(cmd) or int(tests) >= 19:
                executions += 4; full_runs += 1
            else:
                executions += 1
    return {
        "test_module_executions": executions, "test_wall_s": round(test_wall, 1),
        "full_suite_runs_without_click": full_runs,
        "click_gate_commands": click_requests, "click_reused_requests": click_reused_requests,
        "click_result_lines": click_result_lines,
    }



def run_session(index, arm, args):
    label = f"session-{index:02d}-{arm}"
    base = Path(args.root) / label
    base.mkdir(parents=True)
    repo = base / "repo"
    subprocess.run([sys.executable, str(HERE / "make_fixture.py"), str(repo), "--rounds", str(args.rounds)],
                   check=True, stdout=subprocess.DEVNULL)
    env = {k: v for k, v in os.environ.items() if k != "SENTRY-TRACE"}
    argv = ["claude", "-p", PROMPT, "--model", args.model, "--output-format", "stream-json", "--verbose",
            "--max-turns", str(args.max_turns), "--allowedTools", *TOOLS]
    # Both arms disable the unrelated plugin installed on this machine; its bin
    # directory on the Bash tool PATH otherwise differs from the Hook's PATH.
    enabled = {"deploy-on-aws@agent-plugins-for-aws": False}
    if arm == "A" or args.plugin_dir:
        enabled["click@click"] = False
    if arm == "B" and args.plugin_dir:
        argv += ["--plugin-dir", args.plugin_dir]
    argv += ["--settings", json.dumps({"enabledPlugins": enabled})]
    load_before = os.getloadavg()
    started = time.monotonic()
    with open(base / "stream.jsonl", "w") as out, open(base / "stderr.log", "w") as err:
        try:
            proc = subprocess.run(argv, cwd=repo, env=env, stdout=out, stderr=err, timeout=args.timeout, check=False)
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            returncode = "timeout"
    wall = time.monotonic() - started
    result, calls = parse_transcript(base / "stream.jsonl")
    (base / "result.json").write_text(json.dumps(result, indent=1))
    (base / "bash_calls.json").write_text(json.dumps(calls, indent=1, ensure_ascii=False))
    metrics = derive_metrics(calls)
    check_env = env
    check = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=repo, env=check_env,
                           capture_output=True, text=True, timeout=600)
    changed = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    usage = result.get("usage", {})
    return {
        "label": label, "arm": arm, "arm_name": "click-off" if arm == "A" else "click-on",
        "returncode": returncode, "wall_s": round(wall, 1), "load_before": load_before,
        "duration_ms": result.get("duration_ms"), "duration_api_ms": result.get("duration_api_ms"),
        "num_turns": result.get("num_turns"), "total_cost_usd": result.get("total_cost_usd"),
        "stop_reason": result.get("stop_reason"), "is_error": result.get("is_error"),
        "permission_denials": len(result.get("permission_denials", []) or []),
        "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        **metrics,
        "bash_commands": [c["command"] for c in calls],
        "final_suite_ok": check.returncode == 0,
        "final_suite_tail": check.stderr.strip().splitlines()[-1:] ,
        "changed_files": [l[3:] for l in changed.splitlines()],
        "tests_dir_touched": any(l[3:].startswith("tests/") for l in changed.splitlines()),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--order", default="ABBAABBA")
    parser.add_argument("--rounds", type=int, default=300_000)
    parser.add_argument("--max-turns", type=int, default=80)
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--plugin-dir", default="")
    args = parser.parse_args()
    Path(args.root).mkdir(parents=True, exist_ok=True)
    summary = {"model": args.model, "order": args.order, "rounds": args.rounds, "prompt": PROMPT, "sessions": []}
    for index, arm in enumerate(args.order, start=1):
        print(f"[{time.strftime('%H:%M:%S')}] start {index}/{len(args.order)} arm={arm}", flush=True)
        record = run_session(index, arm, args)
        summary["sessions"].append(record)
        (Path(args.root) / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
        print(f"[{time.strftime('%H:%M:%S')}] done  {record['label']}: wall={record['wall_s']}s turns={record['num_turns']} "
              f"cost=${record['total_cost_usd']} module_exec={record['test_module_executions']} test_wall={record['test_wall_s']}s click_gate={record['click_gate_commands']} "
              f"final_ok={record['final_suite_ok']} tests_touched={record['tests_dir_touched']} rc={record['returncode']}", flush=True)


if __name__ == "__main__":
    main()
