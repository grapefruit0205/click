"""Bounded OS process/worker lifecycle facts; never input-reuse authority.

Reassemble strace's interleaved calls by task id. A lifecycle is complete only
when each observed task has an admitted birth and a terminal event. Runtime
input coverage (including clocks and shared memory) is a separate requirement.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

MAX_TASKS = 4096
MAX_TRACE_BYTES = 16 * 1024 * 1024
PID = re.compile(r"^(?:\[pid\s+(\d+)\]\s+|(\d+)\s+)(.*)$")
CALL = re.compile(r"^(\w+)\((.*)\)\s+=\s+(.+)$")
RESUME = re.compile(r"^<\.\.\. (\w+) resumed>(.*)$")
INTEGER = re.compile(r"^(-?\d+)")


@dataclass(frozen=True)
class ProcessTree:
    trace: bytes
    processes: int
    threads: int
    completed: int
    complete: bool
    reasons: tuple[str, ...]
    process_ids: tuple[int, ...] = ()


def inspect(raw: bytes, *, truncated: bool = False) -> ProcessTree:
    reasons = {"event-loss"} if truncated or len(raw) > MAX_TRACE_BYTES else set()
    try:
        lines = raw[:MAX_TRACE_BYTES].decode("utf-8", errors="strict").splitlines()
    except UnicodeError:
        return ProcessTree(b"", 0, 0, 0, False, ("event-loss",))
    pending = {}
    joined = []
    births = {}
    executed = set()
    seen = set()
    finished = set()
    group_exits = set()
    for raw_line in lines:
        match = PID.match(raw_line.strip())
        pid, line = ((match[1] or match[2]), match[3]) if match else ("root", raw_line.strip())
        if not line:
            continue
        seen.add(pid)
        if len(seen) > MAX_TASKS:
            reasons.add("process-limit")
            break
        if line.endswith("<unfinished ...>"):
            if pid in pending:
                reasons.add("event-loss")
            pending[pid] = (len(joined), line[:-len("<unfinished ...>")])
            joined.append(None)
            continue
        resumed = RESUME.match(line)
        if resumed:
            position, prefix = pending.pop(pid, (-1, ""))
            if not prefix.startswith(resumed[1] + "("):
                reasons.add("event-loss")
                continue
            line = prefix + resumed[2]
        elif pid in pending:
            reasons.add("event-loss")
        normalized = ("" if pid == "root" else pid + " ") + line
        if resumed:
            joined[position] = normalized
        else:
            joined.append(normalized)
        if line.startswith("+++ killed by "):
            finished.add(pid)
            # Termination is a lifecycle fact, not the check's result. Runners
            # can deliberately terminate idle workers after reporting success.
            # This does not replace a missing Inspector completion/flush.
        elif line.startswith("+++ exited with "):
            finished.add(pid)
        call = CALL.match(line)
        if not call:
            continue
        name, arguments, result = call.groups()
        number = INTEGER.match(result)
        returned = int(number[1]) if number else None
        if name in {"clone", "clone3", "fork", "vfork"} and returned is not None and returned > 0:
            child = str(returned)
            if child in births or child == pid:
                reasons.add("ambiguous-process-identity")
            births[child] = (pid, "CLONE_THREAD" in arguments)
            if len(births) >= MAX_TASKS:
                reasons.add("process-limit")
                break
        elif name in {"execve", "execveat"} and returned == 0:
            executed.add(pid)
        elif name in {"exit", "exit_group"}:
            finished.add(pid)
            if name == "exit_group":
                group_exits.add(pid)
    if pending:
        reasons.add("event-loss")
    roots = executed - set(births)
    if len(roots) != 1:
        reasons.add("root-execution-unbound")
    admitted = set(roots)
    for _ in range(len(births) + 1):
        added = {pid for pid, (parent, _) in births.items() if parent in admitted}
        if added <= admitted:
            break
        admitted.update(added)
    def thread_group(pid):
        visited = set()
        while pid in births and births[pid][1] and pid not in visited:
            visited.add(pid)
            pid = births[pid][0]
        return pid
    closed_groups = {thread_group(pid) for pid in group_exits & admitted}
    # exit_group terminates the whole thread group, even when invoked by a
    # worker thread. strace -qq may omit a separate terminal line for an idle
    # thread; a forked child remains a different group and must terminate too.
    finished.update(pid for pid in admitted if thread_group(pid) in closed_groups)
    if (seen | set(births)) - admitted or admitted - finished:
        reasons.add("process-tree-incomplete")
    threads = sum(thread for _, thread in births.values())
    return ProcessTree(
        ("\n".join(line for line in joined if line is not None) + "\n").encode(), len(births) - threads,
        threads, len(finished & admitted), not reasons, tuple(sorted(reasons)),
        tuple(sorted(int(pid) for pid in admitted if pid.isdigit()
                     and not births.get(pid, (None, False))[1])),
    )
