"""Framework diagnostics and separately attested conditional JS receipts.

OS/V8 records stay diagnostic and never claim input completeness. Eligible
captures can seed before/after snapshots on requested executions; the runner
attests conditional confidence separately. Unsupported attempts remain bounded.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

if __package__:
    from . import click_dependency_trace as trace, click_observer_process_tree as processes
    from . import click_verification_adapters as adapters
    from . import click_dependency_cache as dependencies
    from . import click_node_observer as node_observer
    from . import click_conditional_observer as conditional
else:
    import click_dependency_trace as trace
    import click_observer_process_tree as processes
    import click_verification_adapters as adapters
    import click_dependency_cache as dependencies
    import click_node_observer as node_observer
    import click_conditional_observer as conditional

STATE_FIELD = "framework_observations"
MAX_RECORDS = 256
# The native backend a conditional receipt binds: strace on Linux, the inbox
# ETW controller on Windows (its converter is bound with it).
BACKEND_EXECUTABLE = "logman" if os.name == "nt" else "strace"
FRAMEWORKS = frozenset({"node-test", "node-script", "vitest", "jest", "package-script"})


def framework(argv) -> str | None:
    description = adapters.command_profile(list(argv))
    name = description.get("profile") if description else None
    if name in FRAMEWORKS:
        return name
    # A resolved framework entry point is common after trusted npm resolution.
    if argv and Path(argv[0]).name.lower() in {"node", "node.exe"} and len(argv) > 1:
        entry = str(argv[1]).replace("\\", "/")
        if entry.endswith("/vitest/vitest.mjs"):
            return "vitest"
        if entry.endswith("/jest/bin/jest.js"):
            return "jest"
        if entry.endswith((".js", ".cjs", ".mjs")) and not entry.startswith("-"):
            return "node-script"
    return None


def record_valid(value) -> bool:
    if not isinstance(value, dict) or type(value.get("version")) is not int:
        return False
    fields = {"version", "framework", "capture", "workers", "runtime_inputs_complete", "reuse_authorized", "reason"}
    if value.get("version") in {2, 3, 4}:
        fields.add("runtime")
        if not node_observer.valid(value.get("runtime")):
            return False
    if value.get("version") == 4:
        fields.add("workspace_digest")
        workspace_digest = value.get("workspace_digest")
        if (not isinstance(workspace_digest, str)
                or workspace_digest and not conditional.DIGEST.fullmatch(workspace_digest)):
            return False
    if value.get("version") in {3, 4}:
        fields.add("conditional_capture")
        projection = value.get("conditional_capture")
        if projection is not None:
            try:
                if (not isinstance(projection, dict) or set(projection) != {"inputs", "external"}
                        or not conditional.seed_valid(conditional.seed(projection["inputs"]))
                        or not isinstance(projection["external"], list)
                        or len(projection["external"]) > conditional.MAX_INPUTS):
                    return False
                for row in projection["external"]:
                    if not conditional.external_row_valid(row):
                        return False
            except (KeyError, ValueError, TypeError):
                return False
    return bool(isinstance(value, dict)
                and set(value) == fields
                and value.get("version") in {1, 2, 3, 4} and value.get("framework") in FRAMEWORKS
                and value.get("runtime_inputs_complete") is False and value.get("reuse_authorized") is False
                and value.get("reason") == "runtime-input-completeness-unavailable"
                and dependencies.shadow_observer_record_is_valid(value.get("capture"))
                and isinstance(value.get("workers"), dict)
                and set(value["workers"]) == {"processes", "threads", "completed", "complete"}
                and isinstance(value["workers"]["complete"], bool)
                and all(isinstance(value["workers"][key], int) and not isinstance(value["workers"][key], bool)
                        and 0 <= value["workers"][key] <= processes.MAX_TASKS
                        for key in ("processes", "threads", "completed")))


def store(verification: dict, records: dict) -> None:
    retained = {key: value for key, value in verification.get(STATE_FIELD, {}).items()
                if record_valid(value)} if isinstance(verification.get(STATE_FIELD), dict) else {}
    retained.update({key: value for key, value in records.items()
                     if record_valid(value) and value["capture"]["binding"]["evidence_key"] == key})
    verification[STATE_FIELD] = json.loads(json.dumps(dict(list(retained.items())[-MAX_RECORDS:])))


@dataclass(frozen=True)
class Execution:
    exit_code: int
    record: dict
    envelope: dict | None = None
    refusal: str = ""


def should_collect(previous, check_digest, revision=None, *, recover_missing_projection=False, workspace_digest=""):
    if not record_valid(previous) or previous.get("version") not in {3, 4} or previous["capture"]["binding"]["check_digest"] != check_digest:
        return True
    # Continue learning on requested executions when a usable seed exists.
    # Evidence task revisions restart at zero. Use the already-computed Git
    # content binding for automatic recovery, not a cross-task counter. This
    # digest schedules collection only; it never authorizes result reuse.
    recovering = (recover_missing_projection and previous["runtime"]["sessions"] > 0
                  and isinstance(workspace_digest, str)
                  and conditional.DIGEST.fullmatch(workspace_digest)
                  and workspace_digest != previous.get("workspace_digest"))
    return (conditional.eligible_record(previous) or
            type(revision) is int and revision >= 0
            and bool(recovering or (previous.get("conditional_capture") is not None
                and previous["capture"]["binding"]["mutation_revision"] != revision)))


def run_command(argv, *, runtime_inputs: bool = True, previous=None,
                conditional_context=None, conditional_secret="", workspace_digest="", **kwargs) -> Execution:
    name = framework(argv)
    if name is None:
        raise ValueError("unsupported framework candidate command")
    tree = None
    projection = None
    def observed(raw, **options):
        nonlocal tree, projection
        if os.name == "nt":
            tree = conditional._windows().inspect_tree(
                raw, root_pid=options.get("root_pid", -1), truncated=bool(options.get("truncated", False)))
        else:
            tree = processes.inspect(raw, truncated=bool(options.get("truncated", False)))
        if collector and collector.location:
            projection = conditional.project_capture(raw, project=project, cwd=kwargs["workspace"],
                                                     directory=collector.location.name, **options)
    collector = node_observer.Collector(argv, dict(kwargs["environment"]), Path(kwargs["workspace"])) if runtime_inputs else None
    before = None
    external_before = None
    project = kwargs.get("observation_root") or kwargs["workspace"]
    starting_digest = conditional.source_digest()
    if (conditional_context and conditional_secret and record_valid(previous)
            and previous["capture"]["binding"]["check_digest"] == kwargs["check_digest"]
            and previous["capture"]["binding"]["evidence_key"] == kwargs["evidence_key"]):
        try:
            before = conditional.snapshot(project, previous["conditional_capture"]["inputs"])
            external_before = conditional.external_snapshot(previous["conditional_capture"]["external"])
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            pass
    try:
        result = trace.run_command(argv, **{**kwargs, "environment": collector.environment if collector else kwargs["environment"]}, process_observer=observed)
        runtime = collector.finish(tree) if collector else node_observer.empty("not-requested")
    finally:
        if collector:
            collector.close()
    if isinstance(runtime, dict) and int(runtime.get("workers", 0) or 0) > 0:
        # A Worker isolate's runtime facilities are outside the Inspector
        # session, so the run cannot earn a conditional receipt. Decide that
        # from the Worker count, never from where a probe happened to land.
        projection = None
    value = {
        "version": 4, "framework": name, "capture": result.record, "runtime": runtime,
        "workspace_digest": workspace_digest if isinstance(workspace_digest, str) and conditional.DIGEST.fullmatch(workspace_digest) else "",
        "conditional_capture": projection,
        "workers": {"processes": tree.processes if tree else 0,
                    "threads": tree.threads if tree else 0,
                    "completed": tree.completed if tree else 0,
                    "complete": bool(tree and tree.complete)},
        "runtime_inputs_complete": False, "reuse_authorized": False,
        "reason": "runtime-input-completeness-unavailable",
    }
    envelope = None
    refusal = ""
    if (result.exit_code == 0 and before and conditional_context and collector
            and starting_digest == conditional.source_digest()):
        backend, error = kwargs["resolve_backend"](BACKEND_EXECUTABLE, workspace=Path(kwargs["workspace"]))
        if not error:
            envelope = conditional.issue(value, before=before, project=project,
                                         external_before=external_before,
                                         context=conditional_context, secret=conditional_secret,
                                         node_path=collector.runtime_path, backend_path=backend)
            if envelope is None:
                refusal = conditional.explain_refusal(value, before=before, external_before=external_before,
                                                      project=project, node_path=collector.runtime_path,
                                                      backend_path=backend)
    return Execution(result.exit_code, value, envelope, refusal)
