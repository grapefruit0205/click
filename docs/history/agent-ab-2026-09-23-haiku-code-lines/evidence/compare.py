"""Compare this record with the 2026-09-23 Haiku record it repeats.

Usage: python3 compare.py [this-evidence-dir [earlier-evidence-dir]]

For each record and arm: median (min–max) of turns, tool calls by type, session
wall time, cost and output tokens, from summary.json and each session's
stream.jsonl. It also counts the calls the earlier record named as the extra
round trips of the Click-on arm: Read calls on files under tests/ and Bash
calls that run `python3 -c`. `test requests` are the transcript-derived rows
of run_ab.py (unittest runs, routed checks and `click-gate verify`); `other
Bash` is every remaining Bash call that is not a probe.
"""
import json
import re
import statistics
import sys
from pathlib import Path

HERE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
EARLIER = (
    Path(sys.argv[2]).resolve() if len(sys.argv) > 2
    else Path(__file__).resolve().parents[2] / "agent-ab-2026-09-23-haiku" / "evidence"
)
PROBE = re.compile(r"\bpython3?\s+-c\b")
TOOLS = ("Bash", "Read", "Edit", "MultiEdit", "Write", "Glob", "Grep")


def events(stream):
    for line in stream.read_text().splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


def tool_uses(stream):
    for event in events(stream):
        message = event.get("message")
        if event.get("type") != "assistant" or not isinstance(message, dict):
            continue
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                yield block.get("name", ""), block.get("input") or {}


def session_row(evidence, session):
    stream = evidence / session["label"] / "stream.jsonl"
    uses = list(tool_uses(stream))
    row = {name: sum(use == name for use, _ in uses) for name in TOOLS}
    row["tool calls"] = len(uses)
    reads = [str(args.get("file_path", "")) for name, args in uses if name == "Read"]
    row["test-file reads"] = sum("/tests/" in path for path in reads)
    row["distinct test files read"] = len({path for path in reads if "/tests/" in path})
    row["source reads"] = sum("/ledger/" in path for path in reads)
    row["python3 -c probes"] = sum(
        name == "Bash" and bool(PROBE.search(str(args.get("command", "")))) for name, args in uses
    )
    row["test requests"] = session["test_requests"]
    row["other Bash"] = row["Bash"] - row["test requests"] - row["python3 -c probes"]
    # The `beta` bug took the extra rounds: a first fix that hands the
    # leftover cent to the last share fails once more.
    row["edits of ledger/beta.py"] = sum(
        name in ("Edit", "MultiEdit", "Write") and str(args.get("file_path", "")).endswith("/ledger/beta.py")
        for name, args in uses
    )
    row["turns"] = session["num_turns"]
    row["session wall s"] = session["wall_s"]
    # Backoff the CLI reported before retrying an API request; part of wall time.
    row["API retry backoff s"] = round(sum(
        event.get("retry_delay_ms", 0) for event in events(stream)
        if event.get("type") == "system" and event.get("subtype") == "api_retry"
    ) / 1000, 1)
    row["test wall s"] = session["test_wall_s"]
    row["cost USD"] = session["total_cost_usd"]
    row["output tokens"] = session["output_tokens"]
    return row


def arms(evidence):
    summary = json.loads((evidence / "summary.json").read_text())
    rows = {"click-off": [], "click-on": []}
    for session in summary["sessions"]:
        rows[session["arm_name"]].append((session["label"], session_row(evidence, session)))
    return summary, rows


def number(value):
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.3f}" if abs(value) < 1 else f"{value:.1f}"
    return f"{value:.0f}"


def cell(values):
    return f"{number(statistics.median(values))} ({number(min(values))}–{number(max(values))})"


def main():
    records = [("earlier", EARLIER), ("this", HERE)]
    loaded = {name: arms(path) for name, path in records}
    for name, (summary, rows) in loaded.items():
        print(f"{name}: {summary['model']} plugin={summary['plugin_digest']} order={summary['order']} {summary['claude_version']}")
    metrics = [key for key in next(iter(loaded["this"][1]["click-on"]))[1]]
    header = f"{'median (min–max)':26}" + "".join(
        f"{name + ' ' + arm:>24}" for name in ("earlier", "this") for arm in ("click-off", "click-on")
    )
    print()
    print(header)
    for metric in metrics:
        line = f"{metric:26}"
        for name in ("earlier", "this"):
            for arm in ("click-off", "click-on"):
                values = [row[metric] for _, row in loaded[name][1][arm] if row[metric] is not None]
                line += f"{cell(values):>24}"
        print(line)
    print()
    print("per session: tool calls (Bash/Read/Edit), test-file reads, probes, beta edits, turns, API retry backoff:")
    for name in ("earlier", "this"):
        for arm in ("click-off", "click-on"):
            print(f"  {name} {arm}:")
            for label, row in loaded[name][1][arm]:
                print(
                    f"    {label}: {row['tool calls']} ({row['Bash']}/{row['Read']}/{row['Edit'] + row['MultiEdit']})"
                    f" · tests read {row['test-file reads']} · probes {row['python3 -c probes']}"
                    f" · beta edits {row['edits of ledger/beta.py']} · turns {row['turns']}"
                    f" · retry backoff {row['API retry backoff s']} s"
                )


if __name__ == "__main__":
    main()
