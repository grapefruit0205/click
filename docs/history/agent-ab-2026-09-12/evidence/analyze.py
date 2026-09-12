"""Aggregate the paired-session A/B record in this directory.

Usage: python3 analyze.py [evidence-dir]

Every number is derived from summary.json (the `claude -p` result events) and
from each session's bash_calls.json (Bash tool calls paired with their outputs).
Arm A test executions: each `Ran N tests in Xs` is one unittest process and a
full discover runs the four modules. Arm B test executions: each
`[Click verification i/n:...]` line is one executed shard child; the request's
test time is Click's parallel wall clock when children overlapped, and a fully
reused request executes nothing.
"""
import json
import re
import statistics
import sys
from pathlib import Path

HERE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
RAN = re.compile(r"Ran (\d+) tests? in ([0-9.]+)s")
CLICK_EXEC = re.compile(r"\[Click verification (\d+)/(\d+):")
PARALLEL = re.compile(r"병렬 실행 벽시계 ([0-9.]+)초")
SINGLE = re.compile(r"이번 테스트 실행: ([0-9.]+)초(?! 합산)")
REUSED = re.compile(r"Click reused (\d+) current")


def session_rows():
    summary = json.loads((HERE / "summary.json").read_text())
    rows = []
    for session in summary["sessions"]:
        calls = json.loads((HERE / session["label"] / "bash_calls.json").read_text())
        executions, test_wall, requests = 0, 0.0, []
        for call in calls:
            out, cmd = call["output"], call["command"]
            if "click-gate" in cmd:
                executed = len(CLICK_EXEC.findall(out))
                walls = PARALLEL.findall(out) or SINGLE.findall(out) or ["0"]
                executions += executed
                test_wall += float(walls[0])
                requests.append({"executed": executed, "wall_s": float(walls[0]), "reused_all": bool(REUSED.search(out))})
            else:
                for tests, seconds in RAN.findall(out):
                    test_wall += float(seconds)
                    executions += 4 if int(tests) >= 19 else 1
                    requests.append({"executed": 4 if int(tests) >= 19 else 1, "wall_s": float(seconds), "reused_all": False})
        usage_total = sum(session[key] or 0 for key in (
            "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        rows.append({
            "label": session["label"], "arm": session["arm_name"], "wall_s": session["wall_s"],
            "test_wall_s": round(test_wall, 1), "executions": executions, "turns": session["num_turns"],
            "cost_usd": session["total_cost_usd"], "output_tokens": session["output_tokens"],
            "cache_read": session["cache_read_input_tokens"], "cache_creation": session["cache_creation_input_tokens"],
            "total_tokens": usage_total, "click_requests": session["click_gate_commands"],
            "final_ok": session["final_suite_ok"], "tests_touched": session["tests_dir_touched"],
            "requests": requests,
        })
    return summary, rows


def main():
    summary, rows = session_rows()
    print(f"model={summary['model']} order={summary['order']} rounds={summary['rounds']}")
    print(f"{'session':14}{'arm':11}{'wall s':>8}{'test s':>8}{'exec':>6}{'turns':>7}{'cost $':>8}{'out tok':>9}{'total tok':>11}{'click':>7}{'ok':>5}")
    for r in rows:
        print(f"{r['label']:14}{r['arm']:11}{r['wall_s']:8.1f}{r['test_wall_s']:8.1f}{r['executions']:6d}{r['turns']:7d}"
              f"{r['cost_usd']:8.3f}{r['output_tokens']:9d}{r['total_tokens']:11d}{r['click_requests']:7d}{str(r['final_ok']):>5}")
    print()
    print("per-request trajectory (executed shards or modules, wall s):")
    for r in rows:
        print(f"  {r['label']}: " + ", ".join(f"{q['executed']}/{q['wall_s']:.0f}s" + ("*" if q["reused_all"] else "") for q in r["requests"]))
    print("  (* = every shard reused, nothing executed)")
    print()
    print(f"{'median (min–max) | mean':34}{'A click-off':>34}{'B click-on':>34}{'median Δ':>10}")
    for key, label in (("wall_s", "session wall time s"), ("test_wall_s", "test wall time s"), ("executions", "test module executions"),
                       ("turns", "turns"), ("cost_usd", "cost USD"), ("output_tokens", "output tokens"),
                       ("cache_read", "cache-read input tokens"), ("cache_creation", "cache-creation input tokens"),
                       ("total_tokens", "total tokens incl. cache")):
        stats = {}
        for arm in ("click-off", "click-on"):
            values = [r[key] for r in rows if r["arm"] == arm]
            stats[arm] = (statistics.median(values), min(values), max(values), statistics.mean(values))
        a, b = stats["click-off"], stats["click-on"]
        delta = (b[0] - a[0]) / a[0] * 100 if a[0] else float("nan")
        fmt = "{:.3f}" if key == "cost_usd" else "{:.0f}"
        cell = lambda s: f"{fmt.format(s[0])} ({fmt.format(s[1])}–{fmt.format(s[2])}) | {fmt.format(s[3])}"
        print(f"{label:34}{cell(a):>34}{cell(b):>34}{delta:>+9.1f}%")
    print()
    print("all final suites pass:", all(r["final_ok"] for r in rows), "| tests/ untouched everywhere:", not any(r["tests_touched"] for r in rows))
    print("click-gate requests per Click-on session:", [r["click_requests"] for r in rows if r["arm"] == "click-on"], "(5 test cycles requested by the task)")


if __name__ == "__main__":
    main()
