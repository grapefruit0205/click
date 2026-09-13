"""Windows (ETW) projection for conditional JS observation.

The strace projection in ``click_conditional_observer`` reads a syscall
transcript. On Windows the runner's native backend is the inbox ETW pair
(``logman.exe`` sessions converted by ``tracerpt.exe``), so the same
questions are answered from the Kernel-Process and Kernel-File events:

* which processes ran, and did every one of them start and stop inside the
  capture (``inspect_tree``);
* which files the single observed process consumed after the collector
  acknowledged its inspector session, with the collector's own transport, the
  null device and the runtime's pre-acknowledgement directory probes set
  aside, and every other read bound by content (``project_capture``).

Anything the trace cannot explain fails closed: an unmapped device path
after the acknowledgement, a lost event, a child process, or a runtime the
inspector did not attach to leaves no projection. This module never grants
reuse; it only produces the rows the conditional receipt binds.
"""

from __future__ import annotations

import ntpath
import os
from pathlib import Path, PureWindowsPath

if __package__:
    from . import click_observer_process_tree as processes
    from . import click_observer_windows as windows
else:  # Executed beside the bundled hook modules.
    import click_observer_process_tree as processes
    import click_observer_windows as windows

PROCESS_START = 1
PROCESS_STOP = 2
# Kernel-File ids: Create and CreateNewFile open a path (the latter only when
# the disposition created it), NameCreate names it before the open.
_OPEN_EVENT_IDS = frozenset({10, 12, 30})
_NULL_DEVICES = frozenset({"\\device\\null", "nul", "\\\\.\\nul"})


def _canonical(value: str, device_paths) -> str:
    return windows._canonical_windows_path(value, device_paths=device_paths)


_long_names: dict[str, str] = {}


def long_name(path: str) -> str:
    """Expand an 8.3 short name (``RUNNER~1``) to the name every event agrees on.

    The file provider records the path an open used, short names included,
    while name and query events carry the full name; one spelling per file
    keeps the collector directory filter and the input rows consistent.
    """
    if os.name != "nt" or "~" not in path:
        return path
    cached = _long_names.get(path)
    if cached is not None:
        return cached
    expanded = path
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32_768)
        length = int(ctypes.windll.kernel32.GetLongPathNameW(path, buffer, len(buffer)))
        if 0 < length < len(buffer):
            expanded = buffer.value
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    if len(_long_names) < 4096:
        _long_names[path] = expanded
    return expanded


def inspect_tree(documents, *, root_pid, truncated: bool = False) -> processes.ProcessTree:
    """Lifecycle facts for the root process and its descendants.

    ``complete`` means the root's start and stop were captured, every
    descendant that started also stopped, and the sessions lost nothing.
    Thread lifecycles are not enabled on this backend; Worker isolates are
    counted by the collector's own markers instead.
    """
    events, lost = windows._iter_events(tuple(documents))
    reasons: set[str] = set()
    if truncated or lost:
        reasons.add("event-loss")
    if not isinstance(root_pid, int) or isinstance(root_pid, bool) or root_pid <= 0:
        return processes.ProcessTree(b"", 0, 0, 0, False, ("root-execution-unbound",))
    parents: dict[int, int] = {}
    stopped: set[int] = set()
    for event in events:
        provider, event_id, fields = windows._event_fields(event)
        if provider not in windows._PROCESS_NAMES:
            continue
        pid = windows._first_integer(fields, windows._PID_FIELDS)
        if pid is None or pid <= 0:
            continue
        if event_id == PROCESS_START:
            parent = windows._first_integer(fields, windows._PARENT_PID_FIELDS)
            if parent is None or parent < 0:
                reasons.add("event-loss")
                continue
            if pid in parents:
                reasons.add("ambiguous-process-identity")
            parents[pid] = parent
        elif event_id == PROCESS_STOP:
            stopped.add(pid)
    if root_pid not in parents:
        reasons.add("root-execution-unbound")
    admitted = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in admitted and pid not in admitted:
                admitted.add(pid)
                changed = True
        if len(admitted) > processes.MAX_TASKS:
            reasons.add("process-limit")
            break
    if admitted - stopped:
        reasons.add("process-tree-incomplete")
    return processes.ProcessTree(
        b"", max(0, len(admitted) - 1), 0, len(admitted & stopped), not reasons,
        tuple(sorted(reasons)), tuple(sorted(admitted)),
    )


def external_row_path(path: str) -> str:
    """The receipt's spelling of an absolute Windows input: ``C:/dir/file``."""
    return PureWindowsPath(path).as_posix()


def project_capture(documents, *, project, cwd, directory, root_pid, device_paths=None,
                    truncated: bool = False, diagnostics: dict | None = None):
    """Conditional projection of one observed process's ETW file events.

    Returns ``{"inputs": [...], "external": [...]}`` in the same shape as the
    strace projection (project-relative rows and absolute rows, the latter
    spelled ``C:/...``), or ``None`` when the capture cannot be explained.
    ``diagnostics``, when given, receives why (names of dynamic objects and
    counts only; never file contents).
    """
    del cwd  # ETW paths are absolute; the working directory is bound separately.
    report = diagnostics if diagnostics is not None else {}
    tree = inspect_tree(documents, root_pid=root_pid, truncated=truncated)
    report["tree"] = {"complete": tree.complete, "reasons": list(tree.reasons), "process_ids": list(tree.process_ids)}
    if not tree.complete or len(tree.process_ids) != 1 or not directory:
        return None
    mappings = dict(device_paths or {})
    try:
        # Resolve the real project on the host; a synthetic Windows path in a
        # test on another OS is taken as spelled.
        project_text = str(Path(project).resolve()) if os.name == "nt" else str(project)
        root = long_name(_canonical(project_text, mappings))
        collector = ntpath.normcase(long_name(_canonical(str(directory), mappings)))
    except (OSError, RuntimeError, ValueError):
        report["error"] = "project-or-collector-path"
        return None
    started_marker = ntpath.normcase(ntpath.join(collector, f"started-{root_pid}"))
    root_case = ntpath.normcase(root).rstrip("\\")
    state = {"started": False, "dynamic": False, "created": set(), "unmapped": []}

    def admit(pid, event_id, raw_path, kind, operation) -> bool:
        if pid != root_pid:
            return True  # unreachable once the tree holds one process; the parser scopes pids
        lowered = raw_path.strip().strip('"').replace("/", "\\").lower()
        if windows._DEVICE_PREFIX.sub("", lowered) in _NULL_DEVICES:
            return False  # the null device supplies no value on either side of the boundary
        try:
            canonical = _canonical(raw_path, mappings)
        except ValueError:
            canonical = ""
        if not canonical or not ntpath.splitdrive(canonical)[0]:
            # A device, pipe, console or unmapped-volume object: no drive
            # letter names it. Before the acknowledgement it is the runtime's
            # own; afterwards it is an input the projection cannot bind.
            if state["started"]:
                state["dynamic"] = True
                if len(state["unmapped"]) < 16:
                    state["unmapped"].append(raw_path[:160])
            return False
        case = ntpath.normcase(long_name(canonical))
        if case == started_marker:
            state["started"] = True
            return False
        if case == collector or case.startswith(collector + "\\"):
            return False  # endpoint announcement, acknowledgement, worker markers
        if event_id == 30:
            # The check created this file: an output, never an input to bind.
            state["created"].add(case)
            return False
        if not state["started"]:
            # Metadata probes outside the project before the acknowledgement
            # are the runtime locating itself and its modules (the file
            # provider does not say whether a queried path is a directory, so
            # the strace rule's directory-only probe becomes metadata-only
            # here); reads stay bound inputs on both sides of the boundary.
            outside = case != root_case and not case.startswith(root_case + "\\")
            if operation in {"metadata", "enumerate"} and outside:
                return False
        return True

    parsed = windows.parse_windows_etw(
        tuple(documents), workspace=Path(root), root_pid=root_pid, truncated=truncated,
        root_execution_bound=True, process_scope_complete=True, device_paths=mappings,
        allow_workspace_root=True, event_filter=admit, normalize_path=long_name,
    )
    report.update({
        "started": state["started"], "dynamic": state["dynamic"], "unmapped": list(state["unmapped"]),
        "created": len(state["created"]), "unresolved": parsed.unresolved_event_count,
        "process_tree_complete": parsed.process_tree_complete, "root_exec_observed": parsed.root_exec_observed,
        "inputs": len(parsed.inputs), "absolute_inputs": len(parsed.absolute_inputs),
    })
    if not state["started"] or state["dynamic"]:
        return None
    if parsed.unresolved_event_count or not parsed.process_tree_complete or not parsed.root_exec_observed:
        return None
    created = state["created"]
    inputs = []
    for row in parsed.inputs:
        absolute = ntpath.normcase(ntpath.join(root, row["path"].rstrip("/").replace("/", "\\")))
        if absolute in created:
            continue
        inputs.append(dict(row))
    external = []
    for row in parsed.absolute_inputs:
        case = ntpath.normcase(row["path"])
        if case == root_case or case.startswith(root_case + "\\") or case in created:
            continue
        if row["operations"] == ["metadata"] and case in {ntpath.normcase(parent) for parent in _ancestors(root)}:
            # Path traversal checks of workspace ancestors are the runtime
            # baseline, not a claim about directory timestamps (the file
            # provider does not say the queried path is a directory).
            continue
        external.append({"path": external_row_path(row["path"]), "kind": row["kind"],
                         "operations": list(row["operations"])})
    external.sort(key=lambda row: row["path"])
    return {"inputs": inputs, "external": external}


def _ancestors(path: str) -> list[str]:
    parents = []
    current = ntpath.normpath(path)
    while True:
        parent = ntpath.dirname(current)
        if not parent or parent == current:
            break
        parents.append(parent)
        current = parent
    return parents
