"""Paired headless Claude Code sessions with Haiku 4.5: arm A (Click disabled) vs
arm B (Click with Evidence auto-routing, loaded from a pinned build directory).

Usage: run_ab.py --root DIR --plugin-dir PINNED_BUILD [--model M] [--order ABBAABBA]
                 [--rounds N] [--max-turns N] [--timeout S]

Same fixture, task prompt and tools as docs/history/agent-ab-2026-09-12. Two
differences, both forced by what is measured:

* A routed check is the model's own command (`python3 -m unittest discover -s
  tests`) whose output is Click's, so a call counts as Click-handled by its
  output (`[Click verification i/n:...]`, a `[Click result]` line, a reuse
  notice), not by `click-gate` appearing in the command.
* The child sessions get the environment without this parent session's own
  `CLAUDE_*` variables (effort, session id, messaging socket), and with
  `DEEPSEEK_POLICY=off` so the user-level deepseek-gate hook stays inert in both
  arms.

Each session writes: repo/, stream.jsonl, result.json, bash_calls.json,
stderr.log. summary.json is rewritten after every session.
"""
import argparse, hashlib, json, os, re, subprocess, sys, time
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
CLICK_RESULT = re.compile(r"^\[Click (?:result|결과)\]", re.M)
CLICK_REUSED = re.compile(r"Click reused (\d+) current")
# Click's `[Click result]` line renders durations as "608.25 ms", "14.2초"/"14.2s"
# or "1분 5초"/"1m 5s"; the parallel wall clock is what the host waited.
CLICK_PARALLEL = re.compile(r"병렬 실행 벽시계 ([^;]+);|· ([^;·]+) wall clock with parallel shards")
CLICK_SINGLE = re.compile(r"(?:이번 테스트 실행|this run's test time): ([^;]+);")
DURATION = re.compile(r"^(?:([0-9.]+) ms|([0-9.]+)(?:초|s)|(\d+)(?:분|m)(?: (\d+)(?:초|s))?)$")


def click_seconds(text):
    """Seconds in one rendered Click duration, or None ("측정 정보 없음")."""
    match = DURATION.match(text.strip().removesuffix(" 합산").removesuffix(" summed"))
    if not match:
        return None
    ms, seconds, minutes, rest = match.groups()
    if ms is not None:
        return float(ms) / 1000
    if seconds is not None:
        return float(seconds)
    return int(minutes) * 60 + int(rest or 0)


def child_environment():
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("CLAUDE_") and key != "SENTRY-TRACE"
    }
    env["DEEPSEEK_POLICY"] = "off"
    return env


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


def click_handled(output):
    return bool(CLICK_EXEC.search(output) or CLICK_RESULT.search(output) or CLICK_REUSED.search(output))


def request_rows(calls):
    """One row per test request: executed modules (or shards) and its test wall time."""
    rows = []
    for call in calls:
        out, cmd = call["output"], call["command"]
        if click_handled(out):
            executed = len(CLICK_EXEC.findall(out))
            walls = [a or b for a, b in CLICK_PARALLEL.findall(out)] or CLICK_SINGLE.findall(out)
            wall = click_seconds(walls[0]) if walls else None
            rows.append({
                "via": "click-gate" if "click-gate" in cmd else "routed",
                "executed": executed, "wall_s": wall or 0.0,
                "reused_all": executed == 0, "command": cmd,
            })
            continue
        for tests, seconds in RAN.findall(out):
            rows.append({
                "via": "direct", "executed": 4 if int(tests) >= 19 else 1,
                "wall_s": float(seconds), "reused_all": False, "command": cmd,
            })
    return rows


def run_session(index, arm, args):
    label = f"session-{index:02d}-{arm}"
    base = Path(args.root) / label
    base.mkdir(parents=True)
    repo = base / "repo"
    subprocess.run([sys.executable, str(HERE / "make_fixture.py"), str(repo), "--rounds", str(args.rounds)],
                   check=True, stdout=subprocess.DEVNULL)
    env = child_environment()
    argv = ["claude", "-p", PROMPT, "--model", args.model, "--output-format", "stream-json", "--verbose",
            "--max-turns", str(args.max_turns), "--allowedTools", *TOOLS]
    # The installed Click stays disabled in both arms; arm B loads the pinned
    # build. The unrelated deploy-on-aws plugin is disabled as in the 09-12 record.
    enabled = {"deploy-on-aws@agent-plugins-for-aws": False, "click@click": False}
    argv += ["--settings", json.dumps({"enabledPlugins": enabled})]
    if arm == "B":
        argv += ["--plugin-dir", args.plugin_dir]
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
    rows = request_rows(calls)
    check = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=repo, env=env,
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
        "test_requests": len(rows),
        "test_module_executions": sum(row["executed"] for row in rows),
        "test_wall_s": round(sum(row["wall_s"] for row in rows), 1),
        "requests_via": {via: sum(row["via"] == via for row in rows) for via in ("direct", "routed", "click-gate")},
        "requests": [{key: row[key] for key in ("via", "executed", "wall_s", "reused_all")} for row in rows],
        "bash_commands": [c["command"] for c in calls],
        "final_suite_ok": check.returncode == 0,
        "final_suite_tail": check.stderr.strip().splitlines()[-1:],
        "changed_files": [l[3:] for l in changed.splitlines()],
        "tests_dir_touched": any(l[3:].startswith("tests/") for l in changed.splitlines()),
    }


def plugin_digest(path):
    digest = hashlib.sha256()
    for file in sorted(Path(path).rglob("*")):
        if file.is_file() and "__pycache__" not in file.parts:
            digest.update(str(file.relative_to(path)).encode() + b"\0" + file.read_bytes())
    return digest.hexdigest()[:16]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--plugin-dir", required=True)
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    parser.add_argument("--order", default="ABBAABBA")
    parser.add_argument("--rounds", type=int, default=300_000)
    parser.add_argument("--max-turns", type=int, default=80)
    parser.add_argument("--timeout", type=int, default=2400)
    args = parser.parse_args()
    Path(args.root).mkdir(parents=True, exist_ok=True)
    summary = {
        "model": args.model, "order": args.order, "rounds": args.rounds, "prompt": PROMPT,
        "plugin_dir": args.plugin_dir, "plugin_digest": plugin_digest(args.plugin_dir),
        "claude_version": subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip(),
        "sessions": [],
    }
    for index, arm in enumerate(args.order, start=1):
        print(f"[{time.strftime('%H:%M:%S')}] start {index}/{len(args.order)} arm={arm}", flush=True)
        record = run_session(index, arm, args)
        summary["sessions"].append(record)
        (Path(args.root) / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
        print(f"[{time.strftime('%H:%M:%S')}] done  {record['label']}: wall={record['wall_s']}s turns={record['num_turns']} "
              f"cost=${record['total_cost_usd']} exec={record['test_module_executions']} test_wall={record['test_wall_s']}s "
              f"via={record['requests_via']} final_ok={record['final_suite_ok']} tests_touched={record['tests_dir_touched']} "
              f"rc={record['returncode']}", flush=True)


if __name__ == "__main__":
    main()
