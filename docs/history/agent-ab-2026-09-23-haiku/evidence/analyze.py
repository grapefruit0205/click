"""Aggregate the paired Haiku 4.5 A/B record in this directory.

Usage: python3 analyze.py [evidence-dir]

Every number comes from summary.json (the `claude -p` result events and the
per-request rows run_ab.py derived from each session's transcript). A request
is `direct` (the host ran unittest: a full discover is four module executions),
`routed` (the model's own command, run by Click through auto-routing) or
`click-gate` (the model typed the Click form); for the last two, executions are
Click's `[Click verification i/n:...]` lines and the wall time is Click's
parallel wall clock.
"""
import collections
import json
import statistics
import sys
from pathlib import Path

HERE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
METRICS = (
    ("wall_s", "session wall time s", "{:.0f}"),
    ("test_wall_s", "test wall time s", "{:.0f}"),
    ("test_module_executions", "test module executions", "{:.0f}"),
    ("test_requests", "test requests", "{:.0f}"),
    ("num_turns", "turns", "{:.0f}"),
    ("total_cost_usd", "cost USD", "{:.3f}"),
    ("output_tokens", "output tokens", "{:.0f}"),
    ("cache_read_input_tokens", "cache-read input tokens", "{:.0f}"),
)


def tool_calls(stream):
    """Tool uses by name in one session's transcript."""
    tally = collections.Counter()
    for line in stream.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        message = event.get("message")
        if event.get("type") != "assistant" or not isinstance(message, dict):
            continue
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tally[block.get("name", "")] += 1
    return tally


def main():
    summary = json.loads((HERE / "summary.json").read_text())
    sessions = summary["sessions"]
    print(f"model={summary['model']} order={summary['order']} rounds={summary['rounds']} "
          f"plugin={summary['plugin_digest']} {summary['claude_version']}")
    print(f"{'session':14}{'arm':11}{'wall s':>8}{'test s':>8}{'exec':>6}{'turns':>7}{'cost $':>8}"
          f"{'direct':>8}{'routed':>8}{'gate':>6}{'ok':>6}")
    for s in sessions:
        via = s["requests_via"]
        print(f"{s['label']:14}{s['arm_name']:11}{s['wall_s']:8.1f}{s['test_wall_s']:8.1f}"
              f"{s['test_module_executions']:6d}{s['num_turns']:7d}{s['total_cost_usd']:8.3f}"
              f"{via['direct']:8d}{via['routed']:8d}{via['click-gate']:6d}{str(s['final_suite_ok']):>6}")
    print()
    print("per-request trajectory (executed modules or shards / wall s; r = routed, g = click-gate, * = all reused):")
    for s in sessions:
        cells = []
        for q in s["requests"]:
            tag = {"routed": "r", "click-gate": "g"}.get(q["via"], "")
            cells.append(f"{q['executed']}/{q['wall_s']:.0f}s{tag}" + ("*" if q["reused_all"] else ""))
        print(f"  {s['label']}: " + ", ".join(cells))
    print()
    print(f"{'median (min–max) | mean':30}{'A click-off':>32}{'B click-on':>32}{'median Δ':>10}")
    for key, label, fmt in METRICS:
        stats = {}
        for arm in ("click-off", "click-on"):
            values = [s[key] for s in sessions if s["arm_name"] == arm and s[key] is not None]
            stats[arm] = (statistics.median(values), min(values), max(values), statistics.mean(values))
        a, b = stats["click-off"], stats["click-on"]
        delta = (b[0] - a[0]) / a[0] * 100 if a[0] else float("nan")
        cell = lambda v: f"{fmt.format(v[0])} ({fmt.format(v[1])}–{fmt.format(v[2])}) | {fmt.format(v[3])}"
        print(f"{label:30}{cell(a):>32}{cell(b):>32}{delta:>+9.1f}%")
    print()
    print("tool calls per session (from each session's stream.jsonl):")
    tallies = {s["label"]: tool_calls(HERE / s["label"] / "stream.jsonl") for s in sessions}
    names = sorted({name for tally in tallies.values() for name in tally})
    print(f"{'session':14}{'arm':11}{'total':>7}{'tests':>7}{'other Bash':>11}" + "".join(f"{n:>11}" for n in names if n != "Bash"))
    for s in sessions:
        tally = tallies[s["label"]]
        other_bash = tally.get("Bash", 0) - s["test_requests"]
        print(f"{s['label']:14}{s['arm_name']:11}{sum(tally.values()):7d}{s['test_requests']:7d}{other_bash:11d}"
              + "".join(f"{tally.get(n, 0):11d}" for n in names if n != "Bash"))
    for arm in ("click-off", "click-on"):
        totals = [sum(tallies[s["label"]].values()) for s in sessions if s["arm_name"] == arm]
        print(f"  {arm}: total tool calls median {statistics.median(totals):.1f} (min {min(totals)}–max {max(totals)})")
    print()
    print("all final suites pass:", all(s["final_suite_ok"] for s in sessions),
          "| tests/ untouched everywhere:", not any(s["tests_dir_touched"] for s in sessions))
    click_on = [s for s in sessions if s["arm_name"] == "click-on"]
    print("Click-on requests handled by Click (routed + click-gate) / all test requests:",
          [f"{s['requests_via']['routed'] + s['requests_via']['click-gate']}/{s['test_requests']}" for s in click_on])


if __name__ == "__main__":
    main()
