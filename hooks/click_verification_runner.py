#!/usr/bin/env python3
"""Execute admitted verification commands and hand typed outcomes to the recorder."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import pickle
import signal
import time

if __package__:
    from . import click_import_bootstrap
else:
    import click_import_bootstrap

(_common,) = click_import_bootstrap.load_siblings(__package__, "click_verification_common")
FAILURE_COLLECTION_VERSION = _common.FAILURE_COLLECTION_VERSION
_decode_encoded_request = _common._decode_encoded_request
_evidence_key = _common._evidence_key
_execute_argv_commands = _common._execute_argv_commands
_file_content_digest = _common._file_content_digest
_git_capture = _common._git_capture
_git_metadata_present = _common._git_metadata_present
_git_workspace_snapshot = _common._git_workspace_snapshot
_new_untracked_is_suspicious = _common._new_untracked_is_suspicious
_resolve_read_only_executable = _common._resolve_read_only_executable
_state_lock = _common._state_lock
_validate_failure_collection = _common._validate_failure_collection
_verification_groups = _common._verification_groups
click_dependency_cache = _common.click_dependency_cache
click_diagnostics = _common.click_diagnostics
click_incremental = _common.click_incremental
click_inspection = _common.click_inspection
click_observer_common = _common.click_observer_common
click_observer_control = _common.click_observer_control
click_process = _common.click_process
click_shadow_intelligence = _common.click_shadow_intelligence
click_verification_inputs = _common.click_verification_inputs
click_verification_bindings = _common.click_verification_bindings

(_results,) = click_import_bootstrap.load_siblings(__package__, "click_verification_results")
VerificationRunResult = _results.VerificationRunResult
_record_incremental_completion = _results._record_incremental_completion
_record_incremental_start = _results._record_incremental_start
record_outcome = _results.record_outcome

(_claims,) = click_import_bootstrap.load_siblings(__package__, "click_verification_claims")
_claim_verification_run = _claims._claim_verification_run
_collection_boundary_check = _claims._collection_boundary_check
_release_unclaimed_verification_reservation = _claims._release_unclaimed_verification_reservation


class _CheckExecution:
    """What one command's execution produced, on the inline path or a worker."""

    __slots__ = (
        "exit_code", "capture_box", "lines", "authoritative_envelope",
        "framework_record", "shadow_record", "dispatch_started_ns",
        "actual_started_ns", "finished_ns", "error",
    )

    def __init__(self) -> None:
        self.exit_code = 0
        self.capture_box: dict[str, Any] = {}
        self.lines: list[str] = []
        self.authoritative_envelope: dict[str, Any] | None = None
        self.framework_record: dict[str, Any] | None = None
        self.shadow_record: dict[str, Any] | None = None
        self.dispatch_started_ns = time.perf_counter_ns()
        self.actual_started_ns: int | None = None
        self.finished_ns: int | None = None
        self.error: BaseException | None = None

    @property
    def elapsed_ms(self) -> float:
        finished = self.finished_ns if self.finished_ns is not None else time.perf_counter_ns()
        return (finished - self.dispatch_started_ns) / 1_000_000


def _verification_workers() -> int:
    """Concurrent shard executions per runner: CLICK_VERIFICATION_WORKERS, else cores, at most 8."""
    raw = os.environ.get("CLICK_VERIFICATION_WORKERS", "").strip()
    if raw:
        try:
            requested = int(raw)
        except ValueError:
            requested = 0
        if requested >= 1:
            return requested
    return max(1, min(8, os.cpu_count() or 1))


def _parallel_blocks(
    checks: list[dict[str, Any]],
    parallel_groups: dict[str, Any],
    grouped_checks: dict[str, list[dict[str, Any]]],
) -> dict[int, int]:
    """Map the first 1-based index of each contiguous shard group to its last.

    Only children expanded from one committed shard plan share a group, and a
    member must be its source's only command so its plan is fixed before it
    starts. Everything else keeps submission order.
    """
    blocks: dict[int, int] = {}
    first: int | None = None
    active: str | None = None
    for index, check in enumerate(checks, start=1):
        source_key = _evidence_key(str(check["evidence_id"]))
        group = parallel_groups.get(source_key)
        eligible = (
            isinstance(group, str) and bool(group)
            and len(grouped_checks.get(source_key, [])) == 1
        )
        if eligible and group == active and first is not None:
            blocks[first] = index
            continue
        first, active = (index, group) if eligible else (None, None)
    return blocks


def _run_verification(arguments: list[str], **options: Any) -> int:
    """One binding pass per runner: git resolution is computed once per workspace."""
    with click_verification_bindings.binding_pass():
        return _run_verification_impl(arguments, **options)


def _run_verification_impl(
    arguments: list[str],
    *,
    file_content_digest: Callable[[Path], str] = _file_content_digest,
    git_workspace_snapshot: Callable[..., dict[str, Any] | None] = (
        _git_workspace_snapshot
    ),
    git_metadata_present: Callable[[Path | None], bool] = _git_metadata_present,
    execute_commands: Callable[..., int] = _execute_argv_commands,
    git_capture: Callable[[Path, list[str]], bytes | None] = _git_capture,
    shadow_execute: Callable[..., click_observer_common.ShadowExecution]
    | None = None,
    authoritative_execute: Callable[..., Any] | None = None,
) -> int:
    if len(arguments) != 4:
        sys.stderr.write(
            "usage: click_gate.py run-verification <state> <digest> <token> <batch>\n"
        )
        return 2
    runner_started_ns = time.perf_counter_ns()
    state_path = Path(arguments[0])
    batch_digest, runner_token, encoded = arguments[1:]
    raw, error = _decode_encoded_request(encoded, "verification")
    if error:
        sys.stderr.write(f"{error}\n")
        return 2
    with _state_lock():
        batch, error = _claim_verification_run(
            state_path,
            raw,
            batch_digest,
            runner_token,
            file_content_digest=file_content_digest,
            git_capture=git_capture,
        )
        if error:
            _release_unclaimed_verification_reservation(
                state_path, batch_digest, runner_token,
                runner_duration_ms=(time.perf_counter_ns() - runner_started_ns) / 1_000_000,
            )
    if error:
        sys.stderr.write(f"{error}\n")
        return 2
    assert batch is not None
    checks = batch["checks"]
    grouped_checks, grouping_error = _verification_groups(batch)
    if grouping_error:
        sys.stderr.write(f"{grouping_error}\n")
        return 2
    command_plans = batch.pop("_click_command_plans", None)
    source_labels = batch.pop("_click_source_labels", {})
    if not isinstance(source_labels, dict):
        source_labels = {}
    parallel_groups = batch.pop("_click_parallel_groups", {})
    if not isinstance(parallel_groups, dict):
        parallel_groups = {}
    if not isinstance(command_plans, dict):
        sys.stderr.write("Click verification runner lost its command outcome binding.\n")
        return 2
    incremental_batch_id = batch.pop("_click_incremental_batch_id", "")
    if not isinstance(incremental_batch_id, str):
        incremental_batch_id = ""
    reporting, reporting_error = click_diagnostics.validate_reporting(
        batch.get("reporting")
    )
    if reporting_error or reporting is None:
        sys.stderr.write(
            f"{reporting_error or 'Click verification reporting policy was invalid.'}\n"
        )
        return 2
    failure_collection, collection_error = _validate_failure_collection(
        batch.get("failure_collection"), checks
    )
    if collection_error or failure_collection is None:
        sys.stderr.write(
            f"{collection_error or 'Click failure collection policy was invalid.'}\n"
        )
        return 2
    verification_environment = batch.pop("_click_verification_environment", None)
    if not isinstance(verification_environment, dict):
        sys.stderr.write(
            "Click verification runner lost its prepared environment binding.\n"
        )
        return 2
    environment_rebound = batch.pop(
        "_click_verification_environment_rebound", False
    )
    claim_input_bindings = batch.pop("_click_explicit_input_bindings", None)
    if (
        not isinstance(claim_input_bindings, dict)
        or set(claim_input_bindings) != set(grouped_checks)
    ):
        sys.stderr.write(
            "Click verification runner lost its explicit input binding.\n"
        )
        return 2
    shadow_bindings = batch.pop("_click_shadow_bindings", {})
    shadow_contexts = batch.pop("_click_shadow_contexts", {})
    authoritative_runtime = batch.pop("_click_authoritative_runtime", None)
    authoritative_contexts = batch.pop("_click_authoritative_contexts", {})
    shadow_revision = batch.pop("_click_mutation_revision", -1)
    observer_mode = batch.pop("_click_observer_mode", "off")
    framework_previous = batch.pop("_click_framework_previous", {})
    framework_observer = None
    if observer_mode in {"auto", "runtime"} and execute_commands is _execute_argv_commands and any(
        Path(check["argv"][0]).name.lower() in {"node", "node.exe", "npm", "npm.cmd", "npx", "npx.cmd", "pnpm", "yarn", "vitest", "jest"}
        for check in checks
    ):
        (framework_observer,) = click_import_bootstrap.load_siblings(__package__, "click_framework_observer")
    shadow_enabled = observer_mode == "shadow"
    authoritative_enabled = bool(
        observer_mode in {"authoritative", "auto"}
        and isinstance(authoritative_runtime, dict)
        and click_observer_control.batch_supports_capture(batch)
        and (execute_commands is _execute_argv_commands or authoritative_execute is not None)
    )
    active_shadow_execute = shadow_execute
    if shadow_enabled and active_shadow_execute is None:
        (click_dependency_trace,) = click_import_bootstrap.load_siblings(
            __package__, "click_dependency_trace"
        )
        active_shadow_execute = click_dependency_trace.run_command
    active_authoritative_execute = authoritative_execute
    if authoritative_enabled and active_authoritative_execute is None:
        (click_authoritative_observer,) = click_import_bootstrap.load_siblings(
            __package__, "click_authoritative_observer"
        )
        active_authoritative_execute = click_authoritative_observer.run_command
    if environment_rebound:
        print(
            "[Click] Verification runner environment changed after preparation; "
            "rebound to the current canonical environment.",
            flush=True,
        )
    before = git_workspace_snapshot(Path.cwd())
    shadow_workspace = Path.cwd()
    if isinstance(before, dict):
        before_root = before.get("root")
        if isinstance(before_root, str) and before_root:
            try:
                shadow_workspace = Path(before_root).resolve(strict=True)
            except (OSError, RuntimeError):
                shadow_workspace = Path.cwd()
    snapshot_failed = before is None and git_metadata_present(Path.cwd())
    if snapshot_failed:
        sys.stderr.write(
            "[Click] Verification could not establish a protected Git workspace "
            "snapshot. No check was executed.\n"
        )

    exit_code = 2 if snapshot_failed else 0
    succeeded_count = 0
    source_durations_ms: dict[str, float] = {}
    source_results = click_incremental.new_source_results(command_plans)
    diagnostic_records: list[dict[str, Any]] = []
    task_ref = hashlib.sha256(
        (str(state_path.resolve(strict=False)) + ":" + str(shadow_revision)).encode()
    ).hexdigest()
    source_completed_commands: dict[str, int] = {}
    per_source_shadow_records: dict[str, list[dict[str, Any]]] = {}
    authoritative_envelopes: dict[str, dict[str, Any]] = {}
    framework_records: dict[str, dict[str, Any]] = {}
    source_key = ""
    command_plan: dict[str, Any] | None = None
    command_actual_started_ns: int | None = None
    overall_exit_code = 0
    collection_active = False
    collection_first_failure_ns: int | None = None
    collection_failed_source_key = ""
    collection_admitted_source_keys: set[str] = set()
    collection_result: dict[str, Any] = {
        "version": FAILURE_COLLECTION_VERSION,
        "batch_ref": batch_digest,
        "requested_mode": failure_collection["mode"],
        "status": "not-triggered",
        "first_failure_source_id": "",
        "admitted_source_ids": [],
        "additional_sources_started": 0,
        "additional_failures": 0,
        "boundary_checks": 0,
        "boundary_check_ms": 0.0,
        "stop_reason": "no-failure",
    }
    if snapshot_failed:
        collection_result.update(
            status="stopped", stop_reason="runner-admission-failed"
        )
    def execute_check(
        check: dict[str, Any],
        source_key: str,
        command_plan: dict[str, Any],
        command_check_digest: str,
    ) -> _CheckExecution:
        """Run one admitted command and return everything the recorder needs.

        The same function serves the inline path and a forked worker: it
        never prints and returns what it produced, so shard children can
        execute concurrently and still be recorded in submission order.
        """
        execution = _CheckExecution()
        argv = check["argv"]
        capture_box = execution.capture_box
        emit = execution.lines.append

        def on_target_start() -> None:
            if execution.actual_started_ns is not None:
                return
            execution.actual_started_ns = time.perf_counter_ns()
            started_offset_ms = (
                execution.actual_started_ns - runner_started_ns
            ) / 1_000_000
            # In a forked worker this updates the worker's own copy of the
            # in-memory results; the consumer replays the start from the
            # returned timestamp. The state-file telemetry is shared either way.
            if click_incremental.start_source_command(
                source_results,
                source_key,
                position=int(command_plan["position"]),
                check_digest=command_check_digest,
                started_offset_ms=started_offset_ms,
            ):
                _record_incremental_start(
                    state_path,
                    batch_digest,
                    runner_token,
                    source_key,
                    position=int(command_plan["position"]),
                    check_digest=command_check_digest,
                    started_offset_ms=started_offset_ms,
                )

        check_digest = (
            shadow_bindings.get(source_key)
            if isinstance(shadow_bindings, dict)
            else None
        )
        can_record_shadow = bool(
            shadow_enabled
            and isinstance(check_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", check_digest)
            and isinstance(shadow_revision, int)
            and not isinstance(shadow_revision, bool)
            and shadow_revision >= 0
        )
        authoritative_context = (
            authoritative_contexts.get(source_key)
            if isinstance(authoritative_contexts, dict)
            else None
        )
        can_record_authoritative = bool(
            authoritative_enabled
            and isinstance(authoritative_runtime, dict)
            and isinstance(authoritative_context, dict)
            and len(grouped_checks.get(source_key, [])) == 1
            and isinstance(before, dict)
            and isinstance(before.get("digest"), str)
            and re.fullmatch(r"[0-9a-f]{64}", before["digest"])
        )
        def execute_current() -> int:
            def execute_unobserved(current: list[str] = argv) -> int:
                if execute_commands is _execute_argv_commands:
                    return execute_commands(
                        [current],
                        environment=verification_environment,
                        capture_output=capture_box,
                        capture_limit_bytes=int(reporting["max_bytes"]),
                        capture_tee=reporting["format"] == "raw",
                    )
                return execute_commands(
                    [current], environment=verification_environment
                )
            observer_compatible = bool(
                click_inspection.execution_argv(argv) == argv
                and not click_inspection.is_git_remote_output_request(argv)
            )
            framework_name = framework_observer.framework(argv) if framework_observer else None
            previous = framework_previous.get(source_key) if isinstance(framework_previous, dict) else None
            if (framework_name and observer_compatible
                    and len(grouped_checks.get(source_key, [])) == 1
                    and isinstance(check_digest, str)
                    and framework_observer.should_collect(
                        previous, check_digest, shadow_revision,
                        recover_missing_projection=observer_mode == "auto",
                        workspace_digest=before.get("digest", "") if isinstance(before, dict) else "",
                    )):

                candidate = framework_observer.run_command(
                    argv, workspace=Path.cwd(), observation_root=shadow_workspace,
                    environment=verification_environment, evidence_key=source_key,
                    check_digest=check_digest, mutation_revision=shadow_revision,
                    execute_unobserved=execute_unobserved,
                    resolve_backend=_resolve_read_only_executable, digest_file=file_content_digest,
                    capture_output=capture_box, capture_limit_bytes=int(reporting["max_bytes"]),
                    capture_tee=reporting["format"] == "raw",
                    runtime_inputs=True,
                    previous=previous,
                    workspace_digest=before.get("digest", "") if isinstance(before, dict) else "",
                    conditional_context={**authoritative_context, "workspace_tree_digest": before["digest"]}
                        if isinstance(authoritative_context, dict) and isinstance(before, dict) else None,
                    conditional_secret=runner_token,
                )
                if candidate.envelope is not None:
                    execution.authoritative_envelope = candidate.envelope
                execution.framework_record = candidate.record
                runtime = candidate.record.get("runtime", {})
                detected = [key for key, count in runtime.get("counts", {}).items() if count]
                emit("[Click framework observer] " +
                      ("runtime inputs detected: " + ", ".join(sorted(detected)) if detected else "runtime inputs: " + str(runtime.get("status", "unavailable"))) +
                      ("; conditional reuse eligible: observed inputs only, completeness unproven"
                       if candidate.envelope else "; no conditional receipt: "
                       + (getattr(candidate, "refusal", "") or "capture incomplete, dynamic, or learning baseline")))
                return candidate.exit_code
            if can_record_authoritative and observer_compatible:
                assert isinstance(authoritative_context, dict)
                authoritative_result = active_authoritative_execute(
                    argv,
                    workspace=Path.cwd(),
                    observation_root=shadow_workspace,
                    environment=verification_environment,
                    binding_context={
                        **authoritative_context,
                        "workspace_tree_digest": str(before["digest"]),
                    },
                    runtime=authoritative_runtime,
                    runner_token=runner_token,
                    execute_unobserved=execute_unobserved,
                    resolve_backend=_resolve_read_only_executable,
                    digest_file=file_content_digest,
                    **({
                        "capture_output": capture_box,
                        "capture_limit_bytes": int(reporting["max_bytes"]),
                        "capture_tee": reporting["format"] == "raw",
                    } if authoritative_execute is None else {}),
                )
                execution.authoritative_envelope = authoritative_result.envelope
                observation = authoritative_result.envelope.get(
                    "observation", {}
                )
                reasons = observation.get("ineligibility_reasons", [])
                if observation.get("status") == "complete":
                    emit(
                        "[Click authoritative observer] complete bound input snapshot",
                    )
                elif observation.get("status") == "conditional":
                    emit(
                        "[Click authoritative observer] conditional bound input snapshot: "
                        + ", ".join(str(reason) for reason in reasons)
                        + "; reuse only while every observed input is unchanged, "
                        "completeness unproven",
                    )
                else:
                    emit(
                        "[Click authoritative observer] reuse unavailable: "
                        + ", ".join(str(reason) for reason in reasons),
                    )
                return authoritative_result.exit_code
            if can_record_shadow:
                if observer_compatible:
                    shadow_result = active_shadow_execute(
                        argv,
                        workspace=Path.cwd(),
                        observation_root=shadow_workspace,
                        environment=verification_environment,
                        evidence_key=source_key,
                        check_digest=check_digest,
                        mutation_revision=shadow_revision,
                        execute_unobserved=execute_unobserved,
                        resolve_backend=_resolve_read_only_executable,
                        digest_file=file_content_digest,
                    )
                else:
                    shadow_result = click_observer_common.run_unobserved(
                        execute_unobserved,
                        evidence_key=source_key,
                        check_digest=check_digest,
                        mutation_revision=shadow_revision,
                    )
                if click_dependency_cache.shadow_observer_record_is_valid(
                    shadow_result.record
                ):
                    execution.shadow_record = shadow_result.record
                emit(
                    click_observer_common.advisory(shadow_result.record),
                )
                return shadow_result.exit_code
            return execute_unobserved()

        try:
            try:
                with click_process.observe_target_start(on_target_start):
                    execution.exit_code = execute_current()
                # Injected test executors own their admission boundary; the
                # production executor reports after successful Popen/resume.
                if (
                    execute_commands is not _execute_argv_commands
                    and execution.actual_started_ns is None
                ):
                    on_target_start()
            finally:
                execution.finished_ns = time.perf_counter_ns()
        except BaseException as error:  # re-raised by the consumer, in order
            execution.error = error
        return execution

    worker_limit = _verification_workers()
    parallel_blocks: dict[int, int] = {}
    if (
        worker_limit > 1
        and sys.platform == "linux"
        and hasattr(os, "fork")
        and reporting["format"] != "raw"
        and failure_collection["mode"] != "bounded"
        and execute_commands is _execute_argv_commands
        and framework_observer is None
        and not shadow_enabled
    ):
        parallel_blocks = _parallel_blocks(checks, parallel_groups, grouped_checks)
    # Shard children run in forked workers: the observer's snapshot work is
    # CPU-bound Python, so threads would only serialize it on the GIL. Each
    # worker returns its _CheckExecution over a pipe; the loop below consumes
    # them in submission order, so output and receipts match a sequential run.
    in_flight: dict[int, tuple[int, int]] = {}
    pending: list[int] = []
    stop_after_block = False

    def member_binding(position: int) -> tuple[str, dict[str, Any]]:
        member = checks[position - 1]
        member_key = _evidence_key(str(member["evidence_id"]))
        plans = command_plans.get(member_key)
        if not isinstance(plans, list) or len(plans) != 1:
            raise RuntimeError("command outcome binding unavailable")
        return member_key, plans[0]

    def start_member(position: int) -> None:
        member = checks[position - 1]
        member_key, plan = member_binding(position)
        read_end, write_end = os.pipe()
        sys.stdout.flush()
        sys.stderr.flush()
        pid = os.fork()
        if pid == 0:  # worker
            status = 0
            try:
                os.close(read_end)
                execution = execute_check(
                    member, member_key, plan, str(plan["check_digest"])
                )
                if execution.error is not None:
                    try:
                        pickle.dumps(execution.error)
                    except Exception:
                        execution.error = RuntimeError(
                            f"{type(execution.error).__name__}: {execution.error}"
                        )
                view = memoryview(
                    pickle.dumps(execution, protocol=pickle.HIGHEST_PROTOCOL)
                )
                while view:
                    view = view[os.write(write_end, view):]
            except BaseException:
                status = 1
            finally:
                try:
                    os.close(write_end)
                finally:
                    os._exit(status)
        os.close(write_end)
        in_flight[position] = (pid, read_end)

    def pump() -> None:
        while pending and len(in_flight) < worker_limit:
            start_member(pending.pop(0))

    def collect(position: int) -> _CheckExecution:
        pid, read_end = in_flight.pop(position)
        chunks: list[bytes] = []
        try:
            with click_process.termination_as_interrupt():
                while True:
                    chunk = os.read(read_end, 1 << 16)
                    if not chunk:
                        break
                    chunks.append(chunk)
                _, wait_status = os.waitpid(pid, 0)
        finally:
            os.close(read_end)
        if not stop_after_block:
            pump()
        payload = b"".join(chunks)
        if not payload:
            raise RuntimeError(
                f"parallel shard worker exited without a result (status {wait_status})"
            )
        execution = pickle.loads(payload)
        if not isinstance(execution, _CheckExecution):
            raise RuntimeError("parallel shard worker returned an unexpected result")
        if execution.actual_started_ns is not None:
            # The worker wrote the state-file telemetry itself; replay the
            # start into this process's results so completion finds it.
            member_key, plan = member_binding(position)
            click_incremental.start_source_command(
                source_results,
                member_key,
                position=int(plan["position"]),
                check_digest=str(plan["check_digest"]),
                started_offset_ms=(execution.actual_started_ns - runner_started_ns) / 1_000_000,
            )
        return execution

    def abandon_parallel_work() -> None:
        pending.clear()
        for pid, read_end in in_flight.values():
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                os.close(read_end)
            except OSError:
                pass
        for pid, _ in in_flight.values():
            try:
                os.waitpid(pid, 0)
            except OSError:
                pass
        in_flight.clear()

    if not snapshot_failed:
        try:
            for index, check in enumerate(checks, start=1):
                if stop_after_block and index not in in_flight:
                    break
                candidate_source_key = _evidence_key(str(check["evidence_id"]))
                if collection_active and candidate_source_key == collection_failed_source_key:
                    # complete_source_command already marked the remaining
                    # commands in this same source as not-run.
                    continue
                if (
                    collection_active
                    and candidate_source_key not in collection_admitted_source_keys
                ):
                    elapsed_since_failure_ms = (
                        (time.perf_counter_ns() - collection_first_failure_ns)
                        / 1_000_000
                        if collection_first_failure_ns is not None
                        else float("inf")
                    )
                    next_evidence_id = str(check["evidence_id"])
                    if next_evidence_id not in failure_collection["independent_sources"]:
                        collection_result.update(
                            status="stopped",
                            stop_reason="source-not-explicitly-independent",
                        )
                        break
                    if (
                        collection_result["additional_sources_started"]
                        >= failure_collection["max_extra_sources"]
                    ):
                        collection_result.update(
                            status="stopped", stop_reason="source-budget-exhausted"
                        )
                        break
                    if elapsed_since_failure_ms > failure_collection["start_window_ms"]:
                        collection_result.update(
                            status="stopped", stop_reason="start-window-expired"
                        )
                        break
                    boundary_reason, boundary_ms = _collection_boundary_check(
                        state_path,
                        batch_digest,
                        runner_token,
                        expected_claim_binding=batch.get("_click_claim_binding"),
                        next_source_key=candidate_source_key,
                        grouped_checks=grouped_checks,
                        before=before,
                        verification_environment=verification_environment,
                        file_content_digest=file_content_digest,
                        git_workspace_snapshot=git_workspace_snapshot,
                    )
                    collection_result["boundary_checks"] += 1
                    collection_result["boundary_check_ms"] = round(
                        float(collection_result["boundary_check_ms"]) + boundary_ms,
                        3,
                    )
                    if boundary_reason:
                        collection_result.update(
                            status="stopped", stop_reason=boundary_reason
                        )
                        break
                    collection_admitted_source_keys.add(candidate_source_key)
                    collection_result["additional_sources_started"] += 1
                    collection_result["admitted_source_ids"].append(
                        next_evidence_id
                    )
                    collection_failed_source_key = ""
                    print(
                        "[Click] Bounded failure collection admitted explicitly "
                        f"independent source {next_evidence_id}.",
                        flush=True,
                    )
                command_plan = None
                command_actual_started_ns = None
                command_finished_ns = None
                diagnostic_record: dict[str, Any] | None = None
                argv = check["argv"]
                rendered = (
                    subprocess.list2cmdline(argv)
                    if os.name == "nt"
                    else shlex.join(argv)
                )
                source_key = _evidence_key(str(check["evidence_id"]))
                # The caller's own id, plus the committed shard id for a shard
                # child; the synthetic child id is never shown to the host.
                label = source_labels.get(source_key)
                if not isinstance(label, str) or not (0 < len(label) <= 80) or not label.isprintable():
                    label = str(check["evidence_id"])
                print(
                    f"[Click verification {index}/{len(checks)}:"
                    f"{label}:{check['class']}] {rendered}",
                    flush=True,
                )
                source_position = source_completed_commands.get(source_key, 0) + 1
                plans_for_source = command_plans.get(source_key)
                if (
                    not isinstance(plans_for_source, list)
                    or source_position > len(plans_for_source)
                ):
                    raise RuntimeError("command outcome binding unavailable")
                command_plan = plans_for_source[source_position - 1]
                command_check_digest = str(command_plan["check_digest"])
                block_end = parallel_blocks.get(index)
                if block_end is not None and index not in in_flight:
                    pending.extend(range(index, block_end + 1))
                    pump()
                if index in in_flight:
                    execution = collect(index)
                else:
                    execution = execute_check(
                        check, source_key, command_plan, command_check_digest
                    )
                for line in execution.lines:
                    print(line, flush=True)
                command_actual_started_ns = execution.actual_started_ns
                command_finished_ns = execution.finished_ns
                source_durations_ms[source_key] = (
                    source_durations_ms.get(source_key, 0) + execution.elapsed_ms
                )
                if execution.error is not None:
                    raise execution.error
                exit_code = execution.exit_code
                capture_box = execution.capture_box
                if execution.authoritative_envelope is not None:
                    authoritative_envelopes[source_key] = execution.authoritative_envelope
                if execution.framework_record is not None:
                    framework_records[source_key] = execution.framework_record
                if execution.shadow_record is not None:
                    per_source_shadow_records.setdefault(source_key, []).append(
                        execution.shadow_record
                    )
                try:
                    diagnostic_record = click_diagnostics.build_record(
                        capture_box,
                        argv=(
                            check.get("_click_approved_argv")
                            if isinstance(check.get("_click_approved_argv"), list)
                            else argv
                        ),
                        workspace=shadow_workspace,
                        state_path=state_path,
                        batch_ref=batch_digest,
                        batch_id=incremental_batch_id,
                        task_ref=task_ref,
                        revision=int(shadow_revision),
                        evidence_id=label,
                        source_key=source_key,
                        command_position=int(command_plan["position"]),
                        check_digest=command_check_digest,
                        exit_code=int(exit_code),
                        reporting=reporting,
                    )
                    diagnostic_records.append(diagnostic_record)
                    if reporting["format"] == "actionable":
                        print(
                            click_diagnostics.render_actionable(diagnostic_record),
                            flush=True,
                        )
                except Exception:
                    diagnostic_record = None
                    if reporting["format"] == "actionable":
                        print(
                            "[Click diagnostic] Structured details were unavailable; "
                            "the check was not repeated.",
                            flush=True,
                        )
                source_completed_commands[source_key] = source_completed_commands.get(source_key, 0) + 1
                command_status = (
                    "interrupted" if exit_code == 130
                    else "failed" if exit_code != 0
                    else "passed"
                )
                command_reason = (
                    "command-interrupted" if exit_code == 130
                    else "command-failed" if exit_code != 0
                    else "command-passed"
                )
                finished_offset_ms = (
                    (command_finished_ns - runner_started_ns) / 1_000_000
                    if command_finished_ns is not None
                    and command_actual_started_ns is not None
                    else None
                )
                command_duration_ms = (
                    (command_finished_ns - command_actual_started_ns) / 1_000_000
                    if command_finished_ns is not None
                    and command_actual_started_ns is not None
                    else None
                )
                click_incremental.complete_source_command(
                    source_results,
                    source_key,
                    position=int(command_plan["position"]),
                    check_digest=command_check_digest,
                    status=command_status,
                    reason=command_reason,
                    finished_offset_ms=finished_offset_ms,
                    duration_ms=command_duration_ms,
                    exit_code=exit_code,
                    log_ref=(
                        diagnostic_record.get("log_ref")
                        if isinstance(diagnostic_record, dict)
                        else None
                    ),
                )
                _record_incremental_completion(
                    state_path,
                    batch_digest,
                    runner_token,
                    source_key,
                    position=int(command_plan["position"]),
                    check_digest=command_check_digest,
                    status=command_status,
                    reason=command_reason,
                    finished_offset_ms=finished_offset_ms,
                    duration_ms=command_duration_ms,
                    source_duration_ms=source_durations_ms.get(source_key),
                    exit_code=exit_code,
                    diagnostic_record=diagnostic_record,
                    reporting=reporting,
                )
                if exit_code != 0:
                    if overall_exit_code == 0:
                        overall_exit_code = int(exit_code)
                    failure_kind = (
                        str(diagnostic_record.get("failure_kind", "unknown"))
                        if isinstance(diagnostic_record, dict)
                        else "unknown"
                    )
                    if collection_active:
                        collection_result["additional_failures"] += 1
                        collection_failed_source_key = source_key
                        if exit_code == 130:
                            collection_result.update(
                                status="stopped", stop_reason="command-interrupted"
                            )
                            break
                        if failure_kind != "test-failure":
                            collection_result.update(
                                status="stopped",
                                stop_reason="unsupported-failure-profile",
                            )
                            break
                        if (
                            collection_result["additional_failures"]
                            >= failure_collection["max_extra_failures"]
                        ):
                            collection_result.update(
                                status="stopped",
                                stop_reason="failure-budget-exhausted",
                            )
                            break
                        continue
                    if failure_collection["mode"] != "bounded":
                        collection_result.update(
                            status="disabled", stop_reason="fail-fast-default"
                        )
                        if in_flight:
                            # Siblings already running in this shard group are
                            # recorded when they finish; nothing new starts.
                            stop_after_block = True
                            pending.clear()
                            continue
                        break
                    if (
                        str(check["evidence_id"])
                        not in failure_collection["independent_sources"]
                    ):
                        collection_result.update(
                            status="stopped",
                            stop_reason="source-not-explicitly-independent",
                        )
                        break
                    if exit_code == 130:
                        collection_result.update(
                            status="stopped", stop_reason="command-interrupted"
                        )
                        break
                    if failure_kind != "test-failure":
                        collection_result.update(
                            status="stopped",
                            stop_reason="unsupported-failure-profile",
                        )
                        break
                    collection_active = True
                    collection_first_failure_ns = command_finished_ns
                    collection_failed_source_key = source_key
                    collection_admitted_source_keys.add(source_key)
                    collection_result.update(
                        status="collecting",
                        first_failure_source_id=str(check["evidence_id"]),
                        admitted_source_ids=[str(check["evidence_id"])],
                        stop_reason="collection-active",
                    )
                    continue
                if overall_exit_code == 0:
                    succeeded_count += 1
            if collection_active and collection_result["status"] == "collecting":
                collection_result.update(
                    status="completed", stop_reason="batch-exhausted"
                )
            if overall_exit_code != 0:
                exit_code = overall_exit_code
        except KeyboardInterrupt:
            exit_code = 130
            abandon_parallel_work()
            collection_result.update(
                status="stopped", stop_reason="runner-interrupted"
            )
            if source_key in source_results and command_plan is not None:
                command_finished_ns = time.perf_counter_ns()
                finished_offset_ms = (
                    (command_finished_ns - runner_started_ns) / 1_000_000
                    if command_actual_started_ns is not None else None
                )
                command_duration_ms = (
                    (command_finished_ns - command_actual_started_ns) / 1_000_000
                    if command_actual_started_ns is not None else None
                )
                click_incremental.complete_source_command(
                    source_results,
                    source_key,
                    position=int(command_plan["position"]),
                    check_digest=str(command_plan["check_digest"]),
                    status="interrupted",
                    reason="command-interrupted",
                    finished_offset_ms=finished_offset_ms,
                    duration_ms=command_duration_ms,
                    exit_code=130,
                )
                _record_incremental_completion(
                    state_path,
                    batch_digest,
                    runner_token,
                    source_key,
                    position=int(command_plan["position"]),
                    check_digest=str(command_plan["check_digest"]),
                    status="interrupted",
                    reason="command-interrupted",
                    finished_offset_ms=finished_offset_ms,
                    duration_ms=command_duration_ms,
                    source_duration_ms=source_durations_ms.get(source_key),
                    exit_code=130,
                )
            sys.stderr.write(
                "[Click] Verification was interrupted. The active check was stopped "
                "and recorded as non-passing.\n"
            )
        except Exception:
            exit_code = 2
            abandon_parallel_work()
            collection_result.update(
                status="stopped", stop_reason="runner-error"
            )
            if source_key in source_results and command_plan is not None:
                command_finished_ns = time.perf_counter_ns()
                finished_offset_ms = (
                    (command_finished_ns - runner_started_ns) / 1_000_000
                    if command_actual_started_ns is not None else None
                )
                command_duration_ms = (
                    (command_finished_ns - command_actual_started_ns) / 1_000_000
                    if command_actual_started_ns is not None else None
                )
                click_incremental.complete_source_command(
                    source_results,
                    source_key,
                    position=int(command_plan["position"]),
                    check_digest=str(command_plan["check_digest"]),
                    status="unknown",
                    reason="command-error",
                    finished_offset_ms=finished_offset_ms,
                    duration_ms=command_duration_ms,
                    exit_code=None,
                )
                _record_incremental_completion(
                    state_path,
                    batch_digest,
                    runner_token,
                    source_key,
                    position=int(command_plan["position"]),
                    check_digest=str(command_plan["check_digest"]),
                    status="unknown",
                    reason="command-error",
                    finished_offset_ms=finished_offset_ms,
                    duration_ms=command_duration_ms,
                    source_duration_ms=source_durations_ms.get(source_key),
                    exit_code=None,
                )
            sys.stderr.write("[Click] The command boundary failed; no check was repeated.\n")

    if in_flight or pending:
        abandon_parallel_work()

    for check in checks:
        approved_argv = check.pop("_click_approved_argv", None)
        if isinstance(approved_argv, list) and approved_argv:
            check["argv"] = approved_argv

    workspace_changed = False
    workspace_root = ""
    workspace_digest = ""
    if before is not None:
        after = git_workspace_snapshot(
            Path.cwd(), list(before["protected_untracked"])
        )
        new_untracked: list[str] = []
        if after is not None:
            workspace_root = str(after.get("root", ""))
            workspace_digest = str(after.get("digest", ""))
            new_untracked = sorted(
                set(after["current_untracked"]) - set(before["current_untracked"])
            )
            if new_untracked:
                rendered_paths = ", ".join(new_untracked[:8])
                if len(new_untracked) > 8:
                    rendered_paths += f", and {len(new_untracked) - 8} more"
                sys.stderr.write(
                    "[Click] Verification created new non-ignored untracked path(s): "
                    f"{rendered_paths}. Review them before keeping the result.\n"
                )
        suspicious_new = [
            path for path in new_untracked if _new_untracked_is_suspicious(path)
        ]
        workspace_changed = (
            after is None
            or after["digest"] != before["digest"]
            or bool(new_untracked)
        )
        if workspace_changed:
            if suspicious_new:
                sys.stderr.write(
                    "[Click] A new path looks like source, configuration, or migration "
                    "content; this classification is informational because every new "
                    "non-ignored path already makes verification stale.\n"
                )
            sys.stderr.write(
                "[Click] Verification changed protected repository content. "
                "The batch is stale; perform or restore that change through the approved "
                "mutation path before verifying again.\n"
            )
            if exit_code == 0:
                exit_code = 3

    final_input_digests: dict[str, str] = {}
    explicit_input_changed = False
    for input_source_key, input_checks in grouped_checks.items():
        final_binding = click_verification_inputs.group_binding(
            input_checks, cwd=Path.cwd()
        )
        expected_binding = claim_input_bindings.get(input_source_key)
        if not isinstance(expected_binding, dict) or any(
            final_binding.get(field) != expected_binding.get(field)
            for field in ("version", "status", "digest", "reason", "match_count")
        ):
            explicit_input_changed = True
        final_input_digests[input_source_key] = (
            str(final_binding.get("digest", ""))
            if final_binding.get("status") == "complete"
            else ""
        )
    if explicit_input_changed:
        workspace_changed = True
        sys.stderr.write(
            "[Click] An explicit verification input changed during execution. "
            "The batch is stale and no reusable PASS will be recorded.\n"
        )
        if exit_code == 0:
            exit_code = 3

    combined_shadow_records: dict[str, dict[str, Any]] = {}
    if shadow_enabled and isinstance(shadow_bindings, dict):
        for source_key, records in per_source_shadow_records.items():
            checks_for_source = grouped_checks.get(source_key, [])
            try:
                combined = click_observer_common.combine_records(
                    records,
                    evidence_key=source_key,
                    check_digest=str(shadow_bindings.get(source_key, "")),
                    mutation_revision=shadow_revision,
                    unexecuted_checks=max(0, len(checks_for_source) - len(records)),
                )
            except Exception:
                combined = None
            if combined is not None:
                combined_shadow_records[source_key] = combined

    shadow_intelligence_baselines: dict[str, dict[str, Any]] = {}
    if (
        not workspace_changed
        and workspace_root
        and isinstance(shadow_contexts, dict)
    ):
        for source_key, record in combined_shadow_records.items():
            context = shadow_contexts.get(source_key)
            if not isinstance(context, dict):
                continue
            try:
                baseline = click_shadow_intelligence.build_baseline(
                    record,
                    workspace=shadow_workspace,
                    environment_digest=str(context.get("environment_digest", "")),
                    executable_digest=str(context.get("executable_digest", "")),
                    host_coverage_digest=str(context.get("host_coverage_digest", "")),
                )
            except Exception:
                baseline = None
            if baseline is not None:
                shadow_intelligence_baselines[source_key] = baseline

    shadow_source_exit_codes: dict[str, int] = {}
    for source_key in combined_shadow_records:
        precise = click_incremental.source_command_outcome(
            source_results.get(source_key), command_plans.get(source_key, [])
        )
        if precise is not None and precise["valid"] and precise["status"] == "passed":
            shadow_source_exit_codes[source_key] = 0
        elif (
            precise is not None
            and precise["valid"]
            and isinstance(precise["exit_code"], int)
        ):
            shadow_source_exit_codes[source_key] = int(precise["exit_code"])

    with _state_lock():
        recorded = record_outcome(
            state_path,
            batch,
            batch_digest,
            runner_token,
            VerificationRunResult(
                exit_code,
                succeeded_count,
                workspace_changed=workspace_changed,
                workspace_root=workspace_root if not workspace_changed else "",
                workspace_digest=workspace_digest if not workspace_changed else "",
                explicit_input_digests=final_input_digests,
                source_durations_ms=source_durations_ms,
                source_results=source_results,
                runner_started_ns=runner_started_ns,
                shadow_observer_records=combined_shadow_records,
                authoritative_observations=authoritative_envelopes,
                framework_observer_records=framework_records,
                shadow_intelligence_baselines=shadow_intelligence_baselines,
                shadow_source_exit_codes=shadow_source_exit_codes,
                shadow_execution_contexts=(
                    shadow_contexts if isinstance(shadow_contexts, dict) else {}
                ),
                observer_mode=observer_mode,
                diagnostic_records=diagnostic_records,
                reporting=reporting,
                collection_result=collection_result,
            ),
            git_capture=git_capture,
        )
    if not recorded:
        sys.stderr.write("Click could not record the verification result safely.\n")
        return exit_code or 2
    try:
        result_state = json.loads(state_path.read_text(encoding="utf-8"))
        measured_batch = click_incremental.current_batch(result_state.get("verification"))
        if any(source.get("execution_reason_code") == "observed-input-changed"
               for source in (measured_batch or {}).get("sources", [])):
            sys.stderr.write(
                "[Click] Inputs of previously reused checks changed during execution. "
                "Those checks require verification again; their reuse was not counted.\n"
            )
        message = click_incremental.host_summary(
            result_state.get("verification"),
            (result_state.get("evidence_state") or {}).get("sources"),
        )
        if message:
            print(message, flush=True)
    except (OSError, ValueError, TypeError):
        pass  # A display failure cannot change the observed verification result.
    return exit_code
