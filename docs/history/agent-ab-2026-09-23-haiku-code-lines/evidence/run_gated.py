"""Run the 2026-09-23 paired Haiku design one session at a time, each only once
the rest of the machine is idle, and record the CPU other processes used.

Usage: run_gated.py --root DIR --plugin-dir PINNED_BUILD [--order ABBAABBA]
                    [--idle-cores N] [--max-wait S] [run_ab.py options]

Every session is started by `run_session` of
../../agent-ab-2026-09-23-haiku/evidence/run_ab.py, unchanged: same fixture,
prompt, tools, child environment and transcript parsing, and summary.json has
the same fields. This driver adds two things, because other Claude Code
sessions on the measurement machine run test suites of their own:

* Before each session it waits until processes outside this driver's process
  tree have used less than --idle-cores CPU cores on average (and never more
  than twice that) over a 60 s window, for at most --max-wait seconds.
* During each session it samples every 5 s the CPU cores used by the machine
  and by the session's own process tree (reaped children included), and
  stores the difference, `other_cores`, under the session's `machine` key.

Linux only (/proc).
"""
import argparse
import importlib.util
import json
import os
import statistics
import subprocess
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN_AB = HERE.parents[1] / "agent-ab-2026-09-23-haiku" / "evidence" / "run_ab.py"
_spec = importlib.util.spec_from_file_location("run_ab", RUN_AB)
run_ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_ab)

TICKS = os.sysconf("SC_CLK_TCK")
SAMPLE_S = 5
WINDOW_S = 60


def machine_ticks():
    """Busy CPU ticks of the whole machine (user+nice+system+irq+softirq+steal)."""
    user, nice, system, _idle, _iowait, irq, softirq, steal = (
        int(value) for value in Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:9]
    )
    return user + nice + system + irq + softirq + steal


def tree_ticks(root):
    """CPU ticks used by `root` and its live descendants, with reaped children."""
    processes = {}
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            raw = Path(entry.path, "stat").read_text()
        except OSError:
            continue
        fields = raw[raw.rfind(")") + 2 :].split()
        processes[int(entry.name)] = (int(fields[1]), sum(int(value) for value in fields[11:15]))
    children = {}
    for pid, (parent, _) in processes.items():
        children.setdefault(parent, []).append(pid)
    total, pending = 0, [root]
    while pending:
        pid = pending.pop()
        total += processes.get(pid, (0, 0))[1]
        pending.extend(children.get(pid, ()))
    return total


class Meter:
    """Cores used by the machine and by this driver's tree since the last read."""

    def __init__(self):
        self.last = (time.monotonic(), machine_ticks(), tree_ticks(os.getpid()))

    def read(self):
        now = (time.monotonic(), machine_ticks(), tree_ticks(os.getpid()))
        seconds = max(now[0] - self.last[0], 1e-6)
        machine = (now[1] - self.last[1]) / TICKS / seconds
        own = (now[2] - self.last[2]) / TICKS / seconds
        self.last = now
        return machine, own, max(0.0, machine - own)


def wait_idle(idle_cores, max_wait):
    meter, window, started = Meter(), [], time.monotonic()
    while True:
        time.sleep(SAMPLE_S)
        window = (window + [meter.read()[2]])[-(WINDOW_S // SAMPLE_S) :]
        waited = time.monotonic() - started
        full = len(window) == WINDOW_S // SAMPLE_S
        if full and statistics.mean(window) < idle_cores and max(window) < 2 * idle_cores:
            return {"waited_s": round(waited), "other_cores_mean": round(statistics.mean(window), 2), "timed_out": False}
        if waited > max_wait:
            return {"waited_s": round(waited), "other_cores_mean": round(statistics.mean(window), 2), "timed_out": True}


class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.meter, self.stop, self.samples = Meter(), threading.Event(), []

    def run(self):
        while not self.stop.wait(SAMPLE_S):
            self.samples.append(tuple(round(value, 2) for value in self.meter.read()))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--plugin-dir", required=True)
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    parser.add_argument("--order", default="ABBAABBA")
    parser.add_argument("--rounds", type=int, default=300_000)
    parser.add_argument("--max-turns", type=int, default=80)
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--idle-cores", type=float, default=1.0)
    parser.add_argument("--max-wait", type=int, default=3600)
    args = parser.parse_args()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    summary = {
        "model": args.model, "order": args.order, "rounds": args.rounds, "prompt": run_ab.PROMPT,
        "plugin_dir": args.plugin_dir, "plugin_digest": run_ab.plugin_digest(args.plugin_dir),
        "claude_version": subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip(),
        "driver": {"script": "run_gated.py", "idle_cores": args.idle_cores, "max_wait_s": args.max_wait,
                   "sample_s": SAMPLE_S, "window_s": WINDOW_S},
        "sessions": [],
    }
    for index, arm in enumerate(args.order, start=1):
        print(f"[{time.strftime('%H:%M:%S')}] waiting for idle before {index}/{len(args.order)} arm={arm}", flush=True)
        gate = wait_idle(args.idle_cores, args.max_wait)
        print(f"[{time.strftime('%H:%M:%S')}] start {index}/{len(args.order)} arm={arm} gate={gate}", flush=True)
        sampler = Sampler()
        sampler.start()
        record = run_ab.run_session(index, arm, args)
        sampler.stop.set()
        sampler.join()
        other = [sample[2] for sample in sampler.samples]
        record["machine"] = {
            "idle_gate": gate,
            "other_cores_mean": round(statistics.mean(other), 2) if other else None,
            "other_cores_max": max(other, default=None),
            "samples": len(other),
            "samples_other_over_1_core": sum(value > 1 for value in other),
            "load_after": os.getloadavg(),
            "cores_machine_session_other": sampler.samples,
        }
        summary["sessions"].append(record)
        (root / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
        print(f"[{time.strftime('%H:%M:%S')}] done  {record['label']}: wall={record['wall_s']}s turns={record['num_turns']} "
              f"cost=${record['total_cost_usd']} exec={record['test_module_executions']} test_wall={record['test_wall_s']}s "
              f"via={record['requests_via']} final_ok={record['final_suite_ok']} tests_touched={record['tests_dir_touched']} "
              f"rc={record['returncode']} other_cores_mean={record['machine']['other_cores_mean']}", flush=True)


if __name__ == "__main__":
    main()
