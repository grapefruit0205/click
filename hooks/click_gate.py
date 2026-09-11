#!/usr/bin/env python3
"""A local Evidence/Guarded runtime for structured capabilities and receipts.

Evidence is the approval-free default and records host-authorized intent,
mutations, verification, and cache lineage. Guarded binds supported mutations to
one approved contract; Off remains fail-open unless Click is explicitly armed.
The hook does not judge architecture, search strategy, or verification
sufficiency. Structured argv requests execute without a shell.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
from typing import Any

if __package__:
    from . import click_import_bootstrap
else:  # Executed directly from the bundled hooks directory.
    import click_import_bootstrap


(
    click_browser,
    click_browser_advisory,
    click_capability,
    click_contract,
    click_contract_state,
    click_diagnostics,
    click_evidence,
    click_host_coverage,
    click_host_router,
    click_incremental,
    click_inspection,
    click_lifecycle,
    click_mutation,
    click_observation,
    click_observer_control,
    click_process,
    click_prompt,
    click_runner_transport,
    click_service,
    click_shadow_dashboard,
    click_shadow_intelligence,
    click_state,
    click_verification,
    click_verification_policy,
    platform_protocol,
) = click_import_bootstrap.load_siblings(
    __package__,
    "click_browser",
    "click_browser_advisory",
    "click_capability",
    "click_contract",
    "click_contract_state",
    "click_diagnostics",
    "click_evidence",
    "click_host_coverage",
    "click_host_router",
    "click_incremental",
    "click_inspection",
    "click_lifecycle",
    "click_mutation",
    "click_observation",
    "click_observer_control",
    "click_process",
    "click_prompt",
    "click_runner_transport",
    "click_service",
    "click_shadow_dashboard",
    "click_shadow_intelligence",
    "click_state",
    "click_verification",
    "click_verification_policy",
    "platform_protocol",
)

# Compatibility alias for direct callers and the deterministic suite. Contract
# schema validation now lives in the one-way click_contract boundary.
_validate_contract = click_contract.validate_contract


JSON_REPORT_FILE_PREFIX = ".click-json-report-"
JSON_REPORT_MAX_BYTES = 2 * 1024 * 1024
JSON_REPORT_MAX_AGE_SECONDS = 60 * 60
JSON_REPORT_MAX_CLEANUP_DELETIONS = 128
_JSON_REPORT_FILENAME = re.compile(
    re.escape(JSON_REPORT_FILE_PREFIX) + r"[0-9a-f]{32}\.json"
)


STATE_LOCK_STALE_SECONDS = click_state.STATE_LOCK_STALE_SECONDS
STATE_LOCK_TIMEOUT_SECONDS = click_state.STATE_LOCK_TIMEOUT_SECONDS
CONTROL_COMMAND = click_lifecycle.CONTROL_COMMAND
CLICK_AUTHORIZATION_PATTERNS = click_prompt.CLICK_AUTHORIZATION_PATTERNS
STRING_FIELDS = click_contract.STRING_FIELDS
OBJECT_FIELDS = click_contract.OBJECT_FIELDS
CONTRACT_FIELDS = click_contract.CONTRACT_FIELDS
BOUNDARY_FIELDS = click_contract.BOUNDARY_FIELDS
BUILD_FIELDS = click_contract.BUILD_FIELDS
VERIFICATION_FIELDS = click_contract.VERIFICATION_FIELDS
EVIDENCE_SOURCE_FIELDS = click_contract.EVIDENCE_SOURCE_FIELDS
DONE_WHEN_FIELDS = click_contract.DONE_WHEN_FIELDS
EVIDENCE_KINDS = click_evidence.EVIDENCE_KINDS
EVIDENCE_STATUSES = click_evidence.EVIDENCE_STATUSES
EVIDENCE_ID_PATTERN = click_contract.EVIDENCE_ID_PATTERN
CONTRACT_ID_PATTERN = click_lifecycle.CONTRACT_ID_PATTERN
VERIFICATION_SCALES = click_verification_policy.VERIFICATION_SCALES
VERIFICATION_UNIT_LIMITS = click_verification_policy.VERIFICATION_UNIT_LIMITS
CAPABILITY_PROTOCOL_VERSION = click_capability.PROTOCOL_VERSION
VERIFICATION_PROTOCOL_VERSION = click_verification.PROTOCOL_VERSION
CONTRACT_STATE_SCHEMA_VERSION = click_lifecycle.CONTRACT_STATE_SCHEMA_VERSION
INSPECTION_REQUEST_FIELDS = click_inspection.REQUEST_FIELDS
MUTATION_REQUEST_FIELDS = click_mutation.REQUEST_FIELDS
SERVICE_REQUEST_FIELDS = click_service.SERVICE_REQUEST_FIELDS
VERIFICATION_BATCH_FIELDS = click_verification.BATCH_FIELDS
VERIFICATION_CHECK_FIELDS = click_verification.CHECK_FIELDS
EVIDENCE_RESULT_FIELDS = click_lifecycle.EVIDENCE_RESULT_FIELDS
VERIFICATION_CLASSES = click_verification.VERIFICATION_CLASSES
PYTHON_VERIFICATION_MODULES = click_verification.PYTHON_VERIFICATION_MODULES
DEEP_VERIFICATION_EXECUTABLES = click_verification.DEEP_VERIFICATION_EXECUTABLES
DEEP_VERIFICATION_MARKERS = click_verification.DEEP_VERIFICATION_MARKERS
MAX_INSPECTION_COMMANDS = click_inspection.MAX_COMMANDS
MAX_ARGV_ITEMS = click_capability.MAX_ARGV_ITEMS
MAX_OBSERVATION_OUTPUT_BYTES = click_observation.MAX_OUTPUT_BYTES
MAX_OBSERVATION_ENTRIES = click_observation.MAX_ENTRIES
MAX_BROWSER_UNIQUE_INPUTS = click_browser.MAX_UNIQUE_INPUTS
# Compatibility names for callers that previously treated these advisory
# thresholds as hard maxima.
MAX_BROWSER_TOOL_TIMEOUT_MS = (
    click_browser_advisory.RECOMMENDED_BROWSER_TOOL_TIMEOUT_MS
)
MAX_BROWSER_WAIT_MS = click_browser_advisory.RECOMMENDED_BROWSER_WAIT_MS
BROWSER_RUNNING_TTL_SECONDS = click_browser.RUNNING_TTL_SECONDS
OBSERVATION_RESERVATION_TTL_SECONDS = click_observation.RESERVATION_TTL_SECONDS
MUTATION_RUNNING_TTL_SECONDS = click_mutation.RUNNING_TTL_SECONDS
VERIFY_RUNNING_TTL_SECONDS = click_verification.RUNNING_TTL_SECONDS
EPHEMERAL_STATE_TTL_SECONDS = click_lifecycle.EPHEMERAL_STATE_TTL_SECONDS
COMPLETED_CONTRACT_TTL_SECONDS = click_lifecycle.COMPLETED_CONTRACT_TTL_SECONDS
DEFAULT_MODES = click_lifecycle.DEFAULT_MODES
SHELL_EXECUTABLES = click_capability.SHELL_EXECUTABLES
PROCESS_CONTROL_EXECUTABLES = click_capability.PROCESS_CONTROL_EXECUTABLES

BROWSER_TOOL_NAMES = click_host_coverage.CODEX_BROWSER_TOOL_NAMES
BROWSER_WAIT_PATTERNS = click_browser_advisory.BROWSER_WAIT_PATTERNS
MANAGED_SERVICE_EXECUTABLES = click_service.MANAGED_SERVICE_EXECUTABLES
MANAGED_SERVICE_ACTIONS = click_service.MANAGED_SERVICE_ACTIONS
MANAGED_SERVICE_SCRIPT_MARKERS = click_service.MANAGED_SERVICE_SCRIPT_MARKERS
SERVICE_START_TIMEOUT_SECONDS = click_service.SERVICE_START_TIMEOUT_SECONDS
SERVICE_STOP_TIMEOUT_SECONDS = click_service.SERVICE_STOP_TIMEOUT_SECONDS
MANAGED_SERVICE_MAX_SECONDS = click_service.MANAGED_SERVICE_MAX_SECONDS

VERIFICATION_EXECUTABLES = click_verification.VERIFICATION_EXECUTABLES
VERIFICATION_NAME_MARKERS = click_verification.VERIFICATION_NAME_MARKERS
TEST_TARGET_SUFFIXES = click_verification.TEST_TARGET_SUFFIXES
TEST_FILTER_OPTIONS = click_verification.TEST_FILTER_OPTIONS
TEST_OPTIONS_WITH_VALUES = click_verification.TEST_OPTIONS_WITH_VALUES
NEW_SOURCE_PATH_SEGMENTS = click_verification.NEW_SOURCE_PATH_SEGMENTS
READ_ONLY_COMMANDS = click_inspection.READ_ONLY_COMMANDS
READ_ONLY_GIT_SUBCOMMANDS = click_inspection.READ_ONLY_GIT_SUBCOMMANDS
GIT_DIFF_RENDERING_SUBCOMMANDS = click_inspection.GIT_DIFF_RENDERING_SUBCOMMANDS
GIT_GLOBAL_ALLOWED_PREFIXES = click_inspection.GIT_GLOBAL_ALLOWED_PREFIXES
GIT_GLOBAL_REJECTED_OPTIONS = click_inspection.GIT_GLOBAL_REJECTED_OPTIONS
GIT_READ_ONLY_EXACT_OPTIONS = click_inspection.GIT_READ_ONLY_EXACT_OPTIONS
GIT_READ_ONLY_OPTION_PREFIXES = click_inspection.GIT_READ_ONLY_OPTION_PREFIXES
SHELL_CONTROL_PUNCTUATION = click_capability.SHELL_CONTROL_PUNCTUATION
SED_READ_SCRIPT = click_inspection.SED_READ_SCRIPT


_OUTPUT_ADAPTER: platform_protocol.HookOutputAdapter = (
    platform_protocol.CodexOutputAdapter()
)


def _write_stdout_payload(payload: dict[str, Any]) -> None:
    # Hook stdout may use a legacy Windows code page. JSON escapes preserve the
    # exact Unicode value after parsing without requiring a UTF-8 console.
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


_OUTPUT_SINK: click_host_router.OutputSink = _write_stdout_payload


def _set_output_adapter(
    adapter: platform_protocol.HookOutputAdapter,
) -> platform_protocol.HookOutputAdapter:
    """Select a host serializer and return the previous serializer."""
    global _OUTPUT_ADAPTER
    previous = _OUTPUT_ADAPTER
    _OUTPUT_ADAPTER = adapter
    return previous


def _set_output_sink(
    sink: click_host_router.OutputSink,
) -> click_host_router.OutputSink:
    """Select a host output sink and return the previous sink."""
    global _OUTPUT_SINK
    previous = _OUTPUT_SINK
    _OUTPUT_SINK = sink
    return previous

RG_OPTIONS_WITH_VALUES = click_inspection.RG_OPTIONS_WITH_VALUES
ENVIRONMENT_ASSIGNMENT = click_capability.ENVIRONMENT_ASSIGNMENT
SSH_TARGET = click_inspection.SSH_TARGET
SSH_READ_ONLY_GIT_SUBCOMMANDS = click_inspection.SSH_READ_ONLY_GIT_SUBCOMMANDS
GIT_REMOTE_NAME = click_inspection.GIT_REMOTE_NAME


def _emit(payload: dict[str, Any]) -> None:
    _OUTPUT_SINK(payload)


def _deny(reason: str, *, event: dict[str, Any] | None = None, code: str = "") -> None:
    if event is not None:
        click_lifecycle.record_control_event(event, code)
    _emit(_OUTPUT_ADAPTER.deny(reason))


def _advise(value: str) -> None:
    _emit(_OUTPUT_ADAPTER.advisory(value))


def _allow_rewritten(command: str) -> None:
    _emit(_OUTPUT_ADAPTER.allow(command))


def _allow_rewritten_with_advisory(command: str, value: str) -> None:
    _emit(_OUTPUT_ADAPTER.allow_with_advisory(command, value))


def _read_event() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"invalid hook input: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("hook input must be a JSON object")
    return value


def _unrecorded_evidence_read(event: dict[str, Any], error: OSError) -> bool:
    """Keep an ordinary host read available when Evidence storage is unwritable.

    A positive Evidence session is required. Explicit capabilities, Guarded,
    review, unknown state and active runner conflicts never use this path.
    The stateless runner retains trusted executable and read-only argv checks,
    but creates no observation, cache hit or successful evidence receipt.
    """
    if error.errno not in {errno.EROFS, errno.EACCES, errno.EPERM, errno.ENOSPC, errno.EDQUOT}:
        return False
    if event.get("tool_name") != "Bash":
        return False
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    request, _, parse_error = click_inspection.request_from_bash(str(tool_input.get("command", "")))
    if request is None or parse_error:
        return False
    try:
        state = json.loads(click_state.contract_path(event).read_text(encoding="utf-8"))
        if (
            not isinstance(state, dict)
            or state.get("status") != "evidence"
            or state.get("runtime_mode") != "evidence"
            or state.get("state_schema_version") != CONTRACT_STATE_SCHEMA_VERSION
            or state.get("contract_id")
            or state.get("approved_turn_id")
            or not re.fullmatch(r"evs_[0-9a-f]{32}", str(state.get("evidence_session_id", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(state.get("intent_digest", "")))
            or state.get("intent_digest") != state.get("contract_digest")
        ):
            return False
        ledger = state.get("evidence_state")
        if (
            not isinstance(ledger, dict)
            or ledger.get("version") != click_evidence.EVIDENCE_STATE_VERSION
            or not isinstance(ledger.get("sources"), dict)
            or ledger.get("source_count") != len(ledger["sources"])
            or ledger.get("registry_digest") != click_evidence.registry_digest(ledger["sources"])
            or click_evidence.sources_from_state(state, expected_contract_schema_version=CONTRACT_STATE_SCHEMA_VERSION) != ledger["sources"]
        ):
            return False
        for path, field, allowed in (
            (click_state.state_path(event), "status", {"idle", "bypassed"}),
            (click_state.mode_path(event), "mode", {"adaptive"}),
            (click_state.preference_path(), "default_mode", {"evidence", "off", "manual"}),
        ):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            if not isinstance(value, dict) or value.get(field) not in allowed:
                return False
        verification = state.get("verification")
        if not isinstance(verification, dict) or verification.get("status") == "running":
            return False
        mutation = state.get("mutation")
        if not isinstance(mutation, dict) or click_mutation.is_running(mutation):
            return False
        entries = state.get("observations", {}).get("entries", {})
        if not isinstance(entries, dict) or any(click_observation.is_running(entry) for entry in entries.values()):
            return False
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    _allow_rewritten_with_advisory(
        _inspection_once_runner_command(request),
        "Click Evidence storage is unavailable. This host-authorized read uses "
        "the validated read-only runner without recording or reusing a receipt; "
        "verification evidence remains unavailable.",
    )
    return True


def _mark_contract_mutated(
    event: dict[str, Any], *, host_tool_use: bool = True
) -> str:
    return click_mutation.mark_contract_mutated(
        event,
        expected_contract_schema_version=CONTRACT_STATE_SCHEMA_VERSION,
        observation_is_running=click_observation.is_running,
        workspace_snapshot=click_verification.git_workspace_snapshot,
        host_tool_use=host_tool_use,
    )


def _stateful_runner_prefix(action: str) -> list[str]:
    """Bind a rewritten runner to the state root selected by the Hook process."""
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--state-root",
        str(click_state.state_root().resolve()),
        action,
    ]


def _observation_runner_command(
    state_path: Path, request: dict[str, Any], request_digest: str, runner_token: str
) -> str:
    return click_observation.runner_command(
        state_path,
        request,
        request_digest,
        runner_token,
        runner_script=Path(__file__).resolve(),
        render_command=click_runner_transport.render_runner_shell_command,
    )


def _prepare_observation(
    event: dict[str, Any],
    request: dict[str, Any],
    broad_inventory: bool,
    *,
    review: bool = False,
) -> tuple[str, str, str]:
    return click_observation.prepare(
        event,
        request,
        broad_inventory,
        review=review,
        mutation_is_running=click_mutation.is_running,
        fresh_mutation_state=click_mutation.fresh_state,
        runner_script=Path(__file__).resolve(),
        render_command=click_runner_transport.render_runner_shell_command,
    )


def _inspection_once_runner_command(request: dict[str, Any]) -> str:
    return click_inspection.runner_command(
        request,
        runner_script=Path(__file__).resolve(),
        render_command=click_runner_transport.render_runner_shell_command,
    )


def _mutation_runner_command(
    event: dict[str, Any], request: dict[str, Any], request_digest: str, runner_token: str
) -> str:
    return click_mutation.runner_command(
        event,
        request,
        request_digest,
        runner_token,
        runner_script=Path(__file__).resolve(),
        render_command=click_runner_transport.render_runner_shell_command,
    )


def _prepare_mutation(event: dict[str, Any], raw: str) -> tuple[str, str]:
    return click_mutation.prepare(
        event,
        raw,
        validate_argv=click_capability.validate_argv,
        looks_like_managed_service=click_service.looks_like_managed_service,
        protocol_version=CAPABILITY_PROTOCOL_VERSION,
        expected_contract_schema_version=CONTRACT_STATE_SCHEMA_VERSION,
        observation_is_running=click_observation.is_running,
        workspace_snapshot=click_verification.git_workspace_snapshot,
        runner_script=Path(__file__).resolve(),
        render_command=click_runner_transport.render_runner_shell_command,
    )


def _service_runner_command(
    event: dict[str, Any],
    request: dict[str, Any],
    service_id: str,
    runner_token: str = "",
) -> str:
    return click_service.service_runner_command(
        event,
        request,
        service_id,
        runner_token=runner_token,
        runner_script=Path(__file__).resolve(),
        render_command=click_runner_transport.render_runner_shell_command,
    )


def _prepare_service(event: dict[str, Any], raw: str) -> tuple[str, str]:
    return click_service.prepare_service(
        event,
        raw,
        validate_argv=click_capability.validate_argv,
        protocol_version=CAPABILITY_PROTOCOL_VERSION,
        mark_contract_mutated=lambda value: _mark_contract_mutated(
            value, host_tool_use=False
        ),
        runner_script=Path(__file__).resolve(),
        render_command=click_runner_transport.render_runner_shell_command,
    )


def _verification_runner_command(
    event: dict[str, Any], batch: dict[str, Any], batch_digest: str, runner_token: str
) -> str:
    return click_verification.runner_command(
        event,
        batch,
        batch_digest,
        runner_token,
        runner_script=Path(__file__).resolve(),
        render_command=click_runner_transport.render_runner_shell_command,
    )


def _tool_working_directory(event: dict[str, Any]) -> Path:
    event_cwd = Path(str(event.get("cwd", "")))
    tool_input = event.get("tool_input")
    requested = tool_input.get("workdir") if isinstance(tool_input, dict) else None
    if not isinstance(requested, str) or not requested:
        return event_cwd.resolve()
    workdir = Path(requested)
    if not workdir.is_absolute():
        workdir = event_cwd / workdir
    return workdir.resolve()


def _tool_working_directory_is_explicit(event: dict[str, Any]) -> bool:
    tool_input = event.get("tool_input")
    requested = tool_input.get("workdir") if isinstance(tool_input, dict) else None
    return isinstance(requested, str) and bool(requested)


def _receipt_export_runner_command(event: dict[str, Any]) -> str:
    host_id = click_host_coverage.host_id_from_event(event)
    return click_runner_transport.render_runner_shell_command(
        [
            *_stateful_runner_prefix("run-receipt-export"),
            str(click_state.contract_path(event).resolve()),
            str(_tool_working_directory(event)),
            host_id,
            (
                "explicit"
                if _tool_working_directory_is_explicit(event)
                else "ambient"
            ),
        ]
    )


def _receipt_verify_runner_command(path: str) -> str:
    return click_runner_transport.render_runner_shell_command(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "run-receipt-verify",
            path,
        ]
    )


def _record_evidence_completion(event: dict[str, Any], raw: str) -> tuple[str, str]:
    return click_lifecycle.record_evidence_completion(event, raw)


def _prepare_verification(
    event: dict[str, Any], raw: str
) -> tuple[str, str, str]:
    return click_verification.prepare(
        event,
        raw,
        runner_script=Path(__file__).resolve(),
        render_command=click_runner_transport.render_runner_shell_command,
        git_workspace_snapshot=click_verification.git_workspace_snapshot,
        git_capture=click_verification.git_capture,
    )


def _prune_json_reports(root: Path, report_path: Path) -> None:
    # Stream the exceptional file-fallback cleanup without retaining a directory
    # list. Fresh files do not consume the deletion budget or starve older files.
    # Enumeration and metadata work can still be O(N); only deletions are capped.
    deleted = 0
    now = time.time()
    try:
        with os.scandir(root) as candidates:
            for candidate in candidates:
                if (
                    candidate.name == report_path.name
                    or _JSON_REPORT_FILENAME.fullmatch(candidate.name) is None
                ):
                    continue
                try:
                    metadata = candidate.stat(follow_symlinks=False)
                    if (
                        stat.S_ISREG(metadata.st_mode)
                        and now - metadata.st_mtime > JSON_REPORT_MAX_AGE_SECONDS
                    ):
                        Path(candidate.path).unlink()
                        deleted += 1
                        if deleted >= JSON_REPORT_MAX_CLEANUP_DELETIONS:
                            break
                except OSError:
                    pass
    except OSError:
        pass


# Each summary line is one argv item, so the transcript shows a few short
# localized lines instead of the full JSON report. UTF-8 output keeps the
# dashboard languages intact on hosts whose console code page cannot encode them.
_STATUS_SUMMARY_SCRIPT = (
    'import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace"); '
    'sys.stdout.write("\\n".join(sys.argv[1:]) + "\\n")'
)
JSON_REPORT_SUMMARY_FLAG = "summary"


def _status_summary_lines(report: dict[str, Any]) -> list[str]:
    (click_status_summary,) = click_import_bootstrap.load_siblings(
        __package__, "click_status_summary"
    )
    return click_status_summary.render_lines(report)


def _inline_report_command(report: dict[str, Any], summary: bool) -> str:
    if summary:
        arguments = [sys.executable, "-c", _STATUS_SUMMARY_SCRIPT, *_status_summary_lines(report)]
    else:
        arguments = [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write(sys.argv[1] + '\\n')",
            json.dumps(report, sort_keys=True, ensure_ascii=True),
        ]
    return click_runner_transport.render_runner_shell_command(arguments)


def _json_report_command(report: dict[str, Any], *, summary: bool = False) -> str:
    """Rewrite to a command that prints the report, or its short summary."""
    inline = _inline_report_command(report, summary)
    if inline != "exit 2":
        return inline

    encoded = json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > JSON_REPORT_MAX_BYTES:
        return inline
    root = click_state.state_root()
    report_path = root / f"{JSON_REPORT_FILE_PREFIX}{secrets.token_hex(16)}.json"
    click_state.write_json(report_path, report)
    _prune_json_reports(root, report_path)
    bounded = click_runner_transport.render_runner_shell_command(
        [
            *_stateful_runner_prefix("run-json-report"),
            str(report_path.resolve()),
            *([JSON_REPORT_SUMMARY_FLAG] if summary else []),
        ]
    )
    if bounded == "exit 2":
        report_path.unlink(missing_ok=True)
    return bounded


def _verification_progress_report(event: dict[str, Any]) -> dict[str, Any]:
    state = click_contract_state.read_contract_state(event)
    sources = click_evidence.sources_from_state(
        state,
        expected_contract_schema_version=CONTRACT_STATE_SCHEMA_VERSION,
    )
    candidates: dict[str, Any] = {}
    successor = state.get(click_evidence.SUCCESSOR_EVIDENCE_FIELD)
    scope_digest = click_evidence.successor_scope_digest(
        str(click_state.contract_path(event).resolve())
    )
    if click_evidence.successor_evidence_is_valid(
        successor,
        expected_contract_schema_version=CONTRACT_STATE_SCHEMA_VERSION,
        scope_digest=scope_digest,
    ):
        candidate_state = {
            "state_schema_version": CONTRACT_STATE_SCHEMA_VERSION,
            "evidence_state": successor["evidence_state"],
        }
        candidate_registry = click_evidence.sources_from_state(
            candidate_state,
            expected_contract_schema_version=CONTRACT_STATE_SCHEMA_VERSION,
        )
        origins = successor.get("origins", {})
        if isinstance(candidate_registry, dict) and isinstance(origins, dict):
            candidates = {
                key: source
                for key, source in candidate_registry.items()
                if key in origins
            }
    (readiness,) = click_import_bootstrap.load_siblings(__package__, "click_reuse_readiness")
    return {"readiness": readiness.projection(state), **click_diagnostics.enrich_progress(
        click_incremental.progress_projection(
            state, sources, successor_candidates=candidates
        ),
        state,
    )}


def _save_sharding_projection(
    event: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    if state.get("status") == "none":
        return
    (click_sharding_setup,) = click_import_bootstrap.load_siblings(
        __package__, "click_sharding_setup"
    )
    state["auto_sharding_setup"] = click_sharding_setup.dashboard_setup_projection(
        report
    )
    click_contract_state.save_contract_state(event, state)


def _prepare_sharding_control(
    event: dict[str, Any], raw: str
) -> tuple[str, str, str]:
    (click_sharding_setup,) = click_import_bootstrap.load_siblings(
        __package__, "click_sharding_setup"
    )
    try:
        request = json.loads(raw)
        if (
            not isinstance(request, dict)
            or set(request) != {"operation", "command"}
            or request.get("operation") not in {"init", "status", "refresh"}
            or not isinstance(request.get("command"), list)
            or any(not isinstance(item, str) or not item for item in request["command"])
        ):
            raise click_sharding_setup.SetupError("invalid-sharding-control")
        project = _tool_working_directory(event)
        state = click_contract_state.read_contract_state(event)
        guarded_active = click_lifecycle.approved_contract_is_active(state)
        evidence_active = bool(
            state.get("status") == "evidence"
            and state.get("runtime_mode") == "evidence"
            and isinstance(state.get("evidence_session_id"), str)
        )
        runtime_mode = (
            "guarded" if guarded_active else "evidence" if evidence_active else ""
        )
        authority_id = str(
            state.get(
                "contract_id" if runtime_mode == "guarded" else "evidence_session_id",
                "",
            )
        )
        planned = click_sharding_setup.plan(
            project,
            request["operation"],
            request["command"],
            state,
            runtime_mode=runtime_mode,
            authority_id=authority_id,
        )
    except (
        click_sharding_setup.SetupError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        reason = str(exc) if str(exc) else "setup-status-unavailable"
        return "", reason, ""

    report = planned.get("report")
    if isinstance(report, dict):
        _save_sharding_projection(event, state, report)
    action = planned.get("action")
    if action == "report":
        assert isinstance(report, dict)
        return _json_report_command(report), "", ""

    if runtime_mode not in {"guarded", "evidence"}:
        return "", (
            "Start Evidence mode or an approved Guarded contract before Click "
            "sharding analysis, application, or bootstrap."
        ), "approval-required"
    script = str(Path(click_sharding_setup.__file__).resolve())
    if action in {"generate", "apply", "bootstrap"}:
        argv = [
            sys.executable,
            script,
            action,
            "--project",
            str(project),
            "--authority-id",
            authority_id,
            "--runtime-mode",
            runtime_mode,
        ]
        if action == "generate":
            argv.extend(["--", *planned["command"]])
        rewritten, error = _prepare_mutation(
            event,
            json.dumps({"version": 1, "argv": argv}, sort_keys=True),
        )
        return rewritten, error, ""
    if action == "verify":
        batch = {
            "version": 2,
            "workdir": str(project),
            "checks": [
                {
                    "evidence_id": click_sharding_setup.BASELINE_EVIDENCE_ID,
                    "argv": planned["command"],
                    "class": "broad",
                }
            ],
        }
        return _prepare_verification(event, json.dumps(batch, sort_keys=True))
    return "", "invalid-sharding-state", ""



def _is_plan_tool(tool_name: str) -> bool:
    normalized = tool_name.lower().replace("::", "__").replace(".", "__")
    return normalized.split("__")[-1] == "update_plan"


def _prepare_browser_evidence(event: dict[str, Any]) -> tuple[bool, str, str]:
    return click_browser.prepare(
        event,
        expected_contract_schema_version=CONTRACT_STATE_SCHEMA_VERSION,
        contract_is_completed=click_lifecycle.contract_is_completed,
        mutation_is_running=click_mutation.is_running,
    )


def _record_mutation_boundary(event: dict[str, Any]) -> None:
    click_mutation.record_boundary(
        event,
        workspace_snapshot=click_verification.git_workspace_snapshot,
    )


def _handle_post_tool(event: dict[str, Any]) -> None:
    if str(event.get("tool_name", "")) not in BROWSER_TOOL_NAMES:
        _record_mutation_boundary(event)
        return
    click_browser.record_result(
        event,
        expected_contract_schema_version=CONTRACT_STATE_SCHEMA_VERSION,
    )


def _handle_prompt_submit(event: dict[str, Any]) -> None:
    _emit(_OUTPUT_ADAPTER.context(click_lifecycle.prompt_context(event)))


def _handle_session_end(event: dict[str, Any]) -> None:
    click_service.request_stop(event)
    click_shadow_dashboard.request_stop(event)


def _handle_pre_tool(event: dict[str, Any]) -> None:
    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input")
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""

    if tool_name in BROWSER_TOOL_NAMES:
        handled, browser_error, browser_advisory = _prepare_browser_evidence(event)
        if handled and browser_error:
            _deny(browser_error)
        elif handled and browser_advisory:
            _advise(browser_advisory)
        return

    if tool_name == "Bash":
        action, value, control_error = click_lifecycle.control_request(str(command))
        if action is not None:
            if control_error:
                _deny(control_error)
                return
            if action == "arm":
                click_lifecycle.prune_state()
                click_observation.clear_review_state(event)
                click_lifecycle.write_state(event, "armed")
                _allow_rewritten("echo Click mutation gate armed")
                return
            if action == "bypass":
                click_lifecycle.prune_state()
                authorization_error = click_prompt.consume_user_authorization(event, "bypass")
                if authorization_error:
                    _deny(authorization_error)
                    return
                click_lifecycle.write_state(event, "bypassed")
                click_observation.clear_review_state(event)
                _allow_rewritten("echo Click bypassed for this turn")
                return
            if action == "cancel":
                click_lifecycle.prune_state()
                authorization_error = click_prompt.consume_user_authorization(event, "cancel")
                if authorization_error:
                    _deny(authorization_error)
                    return
                click_service.request_stop(event)
                click_contract_state.clear_contract_state(event, lock_already_held=True)
                click_observation.clear_review_state(event)
                click_lifecycle.write_state(event, "idle")
                _allow_rewritten("echo Click active contract cancelled")
                return
            if action == "review":
                click_lifecycle.prune_state()
                current_status = click_lifecycle.read_state(event).get("status")
                if current_status in {"armed", "staged", "passed"}:
                    _deny(
                        "Click cannot enter read-only review mode while a build contract "
                        "is active in this turn. Finish or explicitly bypass that workflow."
                    )
                    return
                click_observation.write_review_state(event)
                click_lifecycle.write_state(event, "review")
                _allow_rewritten("echo Click read-only review guard armed")
                return
            if action == "default":
                click_lifecycle.prune_state()
                if value == "status":
                    current = click_lifecycle.read_default_mode()
                    _allow_rewritten(f"echo Click default mode: {current}")
                    return
                click_lifecycle.write_default_mode(value)
                normalized = click_lifecycle.LEGACY_DEFAULT_MODE_ALIASES.get(
                    value, value
                )
                runtime_state = click_contract_state.read_contract_state(event)
                if (
                    normalized != "evidence"
                    and runtime_state.get("status") == "evidence"
                ):
                    click_contract_state.clear_contract_state(
                        event, lock_already_held=True
                    )
                    click_lifecycle.write_state(event, "idle")
                label = {
                    "evidence": "Evidence",
                    "guarded": "Guarded",
                    "off": "Off",
                }[normalized]
                _allow_rewritten(f"echo Click default mode set to {label}")
                return
            if action == "mode":
                click_lifecycle.prune_state()
                click_lifecycle.write_mode(event, value)
                if value == "adaptive":
                    click_lifecycle.write_state(event, "idle")
                _allow_rewritten(f"echo Click mode set to {value}")
                return
            if action == "status":
                report = _verification_progress_report(event)
                _allow_rewritten(
                    _json_report_command(report, summary=value != "json")
                )
                return
            if action == "sharding":
                rewritten, setup_error, detail = _prepare_sharding_control(
                    event, value
                )
                if setup_error:
                    _deny(setup_error, event=event, code=detail)
                elif detail:
                    _allow_rewritten_with_advisory(rewritten, detail)
                else:
                    _allow_rewritten(rewritten)
                return
            if action == "observer":
                (click_observer_backend, click_observer_runtime) = (
                    click_import_bootstrap.load_siblings(
                        __package__, "click_observer_backend", "click_observer_runtime"
                    )
                )
                runtime_state = click_contract_state.read_contract_state(event)
                verification = runtime_state.get("verification")
                if value == "status":
                    selected = click_observer_control.mode(verification)
                    support = click_observer_backend.support_report()
                    detail = click_observer_control.description(verification)
                    tiers = (
                        f"platform {support['system']}: "
                        f"base reuse {support['base_reuse']['status']}, "
                        f"static configuration {support['static_configuration']['status']}, "
                        f"Shadow {support['shadow_observer']['status']}, "
                        "authoritative "
                        f"{support['authoritative_observer']['status']}"
                    )
                    (readiness,) = click_import_bootstrap.load_siblings(__package__, "click_reuse_readiness")
                    _allow_rewritten(_json_report_command({
                        "message": f"Click observer mode: {selected} - {detail} - {tiers}",
                        "readiness": readiness.projection(runtime_state),
                    }))
                    return
                current_status = click_lifecycle.read_state(event).get("status")
                evidence_active = runtime_state.get("status") == "evidence"
                approved_active = click_lifecycle.approved_contract_is_active(
                    runtime_state
                )
                if (
                    current_status != "passed"
                    and click_lifecycle.read_mode(event) != "strict"
                    and not evidence_active
                    and not approved_active
                ):
                    _deny(
                        "Start Guarded or Evidence runtime state before changing its "
                        "Observer mode."
                    )
                    return
                if not isinstance(verification, dict):
                    _deny("Click verification state is unavailable.")
                    return
                if verification.get("status") == "running":
                    _deny(
                        "Wait for the active verification batch before changing its "
                        "Observer mode."
                    )
                    return
                if value in {"authoritative", "auto"} and not (approved_active or evidence_active):
                    _deny(
                        "Input observation requires an active Evidence or approved Guarded runtime."
                    )
                    return
                verification.pop("automatic_observer_attempt", None)
                if value == "runtime":
                    verification.pop("framework_observations", None)
                if value == "authoritative":
                    try:
                        build = click_observer_runtime.prepare(
                            Path(str(event.get("cwd", ""))).resolve(strict=True)
                        )
                        prepared_runtime = click_observer_runtime.control_state(build)
                        if click_observer_runtime.validate(
                            Path(str(event.get("cwd", ""))), prepared_runtime
                        ) is None:
                            raise ValueError("prepared runtime did not validate")
                    except Exception:
                        _deny(
                            "The supported native CPython 3.12 authoritative profile "
                            "for this platform could not be prepared."
                        )
                        return
                    verification[click_observer_runtime.STATE_FIELD] = prepared_runtime
                else:
                    verification.pop(click_observer_runtime.STATE_FIELD, None)
                click_observer_control.set_mode(verification, value)
                runtime_state["verification"] = verification
                click_contract_state.save_contract_state(event, runtime_state)
                # Prepared artifacts are content-addressed and shared by local
                # sessions. Switching this session off must not delete a
                # companion another session is using.
                detail = click_observer_control.description(verification)
                _allow_rewritten(
                    f"echo Click observer mode set to {value} - {detail}"
                )
                return
            if action == "receipt-export":
                state = click_contract_state.read_contract_state(event)
                if not click_lifecycle.contract_is_completed(state):
                    _deny(
                        "Click receipt export requires every declared evidence source "
                        "to be current and every managed service to be stopped."
                    )
                    return
                _allow_rewritten(_receipt_export_runner_command(event))
                return
            if action == "receipt-verify":
                _allow_rewritten(_receipt_verify_runner_command(value))
                return
            if action == "evidence":
                current_status = click_lifecycle.read_state(event).get("status")
                runtime_state = click_contract_state.read_contract_state(event)
                evidence_active = runtime_state.get("status") == "evidence"
                if (
                    current_status != "passed"
                    and click_lifecycle.read_mode(event) != "strict"
                    and not evidence_active
                ):
                    _deny(
                        "Pass the approved Click execution contract before recording "
                        "its declared completion evidence."
                    )
                    return
                rewritten, evidence_error = _record_evidence_completion(event, value)
                if evidence_error:
                    _deny(evidence_error)
                    return
                _allow_rewritten(rewritten)
                return
            if action == "inspect":
                request, broad_inventory, inspection_error = (
                    click_inspection.validate_request(value)
                )
                if inspection_error:
                    _deny(inspection_error)
                    return
                assert request is not None
                current_status = click_lifecycle.read_state(event).get("status")
                runtime_state = click_contract_state.read_contract_state(event)
                approved_session_active = click_lifecycle.approved_contract_is_active(runtime_state)
                evidence_active = runtime_state.get("status") == "evidence"
                if current_status == "review":
                    (
                        rewritten,
                        inspection_error,
                        inspection_advisory,
                    ) = _prepare_observation(
                        event, request, broad_inventory, review=True
                    )
                    if inspection_error:
                        _deny(inspection_error)
                        return
                elif current_status == "passed" or approved_session_active or evidence_active:
                    (
                        rewritten,
                        inspection_error,
                        inspection_advisory,
                    ) = _prepare_observation(
                        event, request, broad_inventory
                    )
                    if inspection_error:
                        _deny(inspection_error)
                        return
                else:
                    rewritten = _inspection_once_runner_command(request)
                    inspection_advisory = ""
                if inspection_advisory:
                    _allow_rewritten_with_advisory(rewritten, inspection_advisory)
                else:
                    _allow_rewritten(rewritten)
                return
            if action == "mutate":
                current_status = click_lifecycle.read_state(event).get("status")
                evidence_active = click_contract_state.read_contract_state(event).get("status") == "evidence"
                if (
                    current_status != "passed"
                    and click_lifecycle.read_mode(event) != "strict"
                    and not evidence_active
                ):
                    _deny(
                        "Pass the approved Click execution contract in the current turn "
                        "before starting a structured mutation.",
                        event=event, code="approval-required",
                    )
                    return
                rewritten, mutation_error = _prepare_mutation(event, value)
                if mutation_error:
                    _deny(mutation_error)
                    return
                _allow_rewritten(rewritten)
                return
            if action == "service":
                current_status = click_lifecycle.read_state(event).get("status")
                evidence_active = click_contract_state.read_contract_state(event).get("status") == "evidence"
                if (
                    current_status != "passed"
                    and click_lifecycle.read_mode(event) != "strict"
                    and not evidence_active
                ):
                    _deny(
                        "Pass the approved Click execution contract in the current turn "
                        "before managing its local development service.",
                        event=event, code="approval-required",
                    )
                    return
                rewritten, service_error = _prepare_service(event, value)
                if service_error:
                    _deny(service_error)
                    return
                _allow_rewritten(rewritten)
                return
            if action == "dashboard":
                (click_sharding_setup,) = click_import_bootstrap.load_siblings(
                    __package__, "click_sharding_setup"
                )
                current_status = click_lifecycle.read_state(event).get("status")
                runtime_state = click_contract_state.read_contract_state(event)
                try:
                    setup_report = click_sharding_setup.status(
                        _tool_working_directory(event), runtime_state
                    )
                    _save_sharding_projection(event, runtime_state, setup_report)
                except (OSError, TypeError, ValueError):
                    pass
                evidence_active = runtime_state.get("status") == "evidence"
                approved_active = click_lifecycle.approved_contract_is_active(
                    runtime_state
                )
                if (
                    current_status != "passed"
                    and click_lifecycle.read_mode(event) != "strict"
                    and not evidence_active
                    and not approved_active
                ):
                    _deny(
                        "Start Guarded or Evidence runtime state before opening its "
                        "Shadow dashboard."
                    )
                    return
                rewritten, dashboard_error = click_shadow_dashboard.prepare(
                    event,
                    value,
                    runner_script=Path(__file__).resolve(),
                    render_command=(
                        click_runner_transport.render_runner_shell_command
                    ),
                )
                if dashboard_error:
                    _deny(dashboard_error)
                    return
                _allow_rewritten(rewritten)
                return
            if action == "verify":
                current_status = click_lifecycle.read_state(event).get("status")
                evidence_active = click_contract_state.read_contract_state(event).get("status") == "evidence"
                if (
                    current_status != "passed"
                    and click_lifecycle.read_mode(event) != "strict"
                    and not evidence_active
                ):
                    _deny(
                        "Pass the approved Click execution contract in the current turn "
                        "before starting its final verification batch.",
                        event=event, code="approval-required",
                    )
                    return
                (
                    rewritten,
                    verification_error,
                    verification_advisory,
                ) = _prepare_verification(event, value)
                if verification_error:
                    _deny(verification_error)
                    return
                if verification_advisory:
                    _allow_rewritten_with_advisory(
                        rewritten, verification_advisory
                    )
                else:
                    _allow_rewritten(rewritten)
                return
            if action in {"stage", "pass"}:
                if action == "stage":
                    rewritten, lifecycle_error = click_lifecycle.stage_contract(
                        event, value
                    )
                else:
                    rewritten, lifecycle_error = click_lifecycle.pass_contract(
                        event, value
                    )
                if lifecycle_error:
                    _deny(lifecycle_error, event=event, code="contract-lifecycle-rejected")
                    return
                if action == "stage":
                    contract, projection_error = click_contract.validate_contract(value)
                    contract_id = click_lifecycle.contract_id_from_state(click_contract_state.read_contract_state(event))
                    if projection_error or contract is None or not contract_id:
                        _deny(
                            projection_error
                            or "Click could not render the staged approval projection."
                        )
                        return
                    _allow_rewritten_with_advisory(
                        rewritten,
                        click_contract.render_human_view(contract_id, contract),
                    )
                else:
                    _allow_rewritten(rewritten)
                return

    status = click_lifecycle.read_state(event).get("status")
    if status == "bypassed":
        return
    if _is_plan_tool(tool_name):
        contract_state = click_contract_state.read_contract_state(event)
        session_contract_active = click_lifecycle.session_contract_is_active(contract_state)
        if status in {"armed", "staged", "passed"} or session_contract_active:
            _advise(
                "Click advisory: the Click contract workflow remains authoritative. Use the "
                "host plan only to track progress; it cannot authorize mutation; replace a "
                "staged or approved contract; widen the approved outcome, boundary, must-hold "
                "conditions, or evidence commitments; or satisfy evidence. Obtain new user "
                "approval before changing those commitments."
            )
        elif status == "review":
            _advise(
                "Click advisory: this remains a read-only review. A host plan may organize "
                "findings but does not authorize mutation; obtain a new approved contract "
                "before changing files."
            )
        return

    inspection_request: dict[str, Any] | None = None
    broad_inventory = False
    inspection_parse_error = ""
    if tool_name == "Bash":
        inspection_request, broad_inventory, inspection_parse_error = (
            click_inspection.request_from_bash(str(command))
        )
    if tool_name == "Bash" and inspection_request is not None:
        runtime_state = click_contract_state.read_contract_state(event)
        approved_session_active = click_lifecycle.approved_contract_is_active(runtime_state)
        evidence_active = runtime_state.get("status") == "evidence"
        if status == "passed" or approved_session_active or evidence_active:
            (
                rewritten,
                observation_error,
                observation_advisory,
            ) = _prepare_observation(
                event, inspection_request, broad_inventory
            )
            if observation_error:
                _deny(observation_error)
                return
            if observation_advisory:
                _allow_rewritten_with_advisory(rewritten, observation_advisory)
            else:
                _allow_rewritten(rewritten)
        elif status == "review":
            (
                rewritten,
                observation_error,
                observation_advisory,
            ) = _prepare_observation(
                event, inspection_request, broad_inventory, review=True
            )
            if observation_error:
                _deny(observation_error)
                return
            if observation_advisory:
                _allow_rewritten_with_advisory(rewritten, observation_advisory)
            else:
                _allow_rewritten(rewritten)
        else:
            _allow_rewritten(_inspection_once_runner_command(inspection_request))
        return

    if tool_name == "Bash" and status in {"passed", "review"}:
        if inspection_parse_error:
            _deny(inspection_parse_error)
            return
        if status == "review":
            _deny(
                "Click review accepts only structured read-only argv operations. Use "
                "`click-gate inspect '<Inspection JSON>'`; mutation remains blocked during "
                "review, while host plan tools remain non-authoritative advisory."
            )
            return
        contract_state = click_contract_state.read_contract_state(event)
        verification = contract_state.get("verification")
        if isinstance(verification, dict) and verification.get("status") == "running":
            _deny(
                "The final Click verification batch is running. Wait for it to finish "
                "before starting another command or mutating the implementation."
            )
            return
        if click_verification.is_recognized_command(str(command)):
            _deny(
                "Click final checks use protocol-v2 `click-gate verify` with evidence-bound "
                "argv `checks` and an explicit targeted, broad, or deep class."
            )
            return
        _deny(
            "Click does not guess whether this Bash command mutates the workspace. Use "
            "`click-gate inspect` for read-only argv operations or `click-gate mutate` "
            "for an approved implementation command."
        )
        return

    if status == "review":
        _deny(
            "Click review mode is read-only. Report the review findings without changing "
            "the project. If the user asks for a fix, leave review mode and use a compact "
            "Click build contract, or bypass Click for that turn when the user requests it."
        )
        return

    if status in {"passed", "bypassed"}:
        if status == "passed":
            contract_state = click_contract_state.read_contract_state(event)
            verification = contract_state.get("verification")
            verification_status = (
                str(verification.get("status", ""))
                if isinstance(verification, dict)
                else ""
            )
            if verification_status == "running":
                _deny(
                    "The final Click verification batch is running. Wait for it to finish "
                    "before starting another command or mutating the implementation."
                )
                return
            mutation_error = _mark_contract_mutated(event)
            if mutation_error:
                _deny(mutation_error)
        return

    default_mode = click_lifecycle.read_default_mode()
    runtime_state = click_contract_state.read_contract_state(event)
    session_contract_active = click_lifecycle.session_contract_is_active(runtime_state)
    if (
        default_mode == "evidence"
        and not session_contract_active
        and status not in {"armed", "staged"}
        and click_lifecycle.read_mode(event) != "strict"
    ):
        runtime_state, recovered = click_lifecycle.ensure_evidence_state(event)
        verification = runtime_state.get("verification")
        if isinstance(verification, dict) and verification.get("status") == "running":
            _deny(
                "Click blocked mutation while its exact Evidence verification runner is "
                "active. Wait for that bound result before changing the revision."
            )
            return
        mutation_error = _mark_contract_mutated(event)
        if mutation_error:
            # Evidence is observability, not execution authority. A recovery problem may
            # lower receipt assurance but must not masquerade as a host permission denial.
            _advise(
                "Click Evidence advisory: this host mutation remains authorized by the "
                f"host, but Click could not bind its receipt ({mutation_error})."
            )
        elif recovered:
            _advise(
                "Click Evidence advisory: a new lower-assurance session was created; "
                "history before recovery is excluded from its receipt."
            )
        return

    if (
        session_contract_active
        or status in {"armed", "staged"}
        or click_lifecycle.read_mode(event) == "strict"
        or default_mode == "guarded"
    ):
        _deny(
            "Click blocked this mutation because the active execution contract has "
            "not been staged, explained plainly, explicitly approved, and matched for the "
            "current turn. Complete outcome, boundary.in_scope, boundary.out_of_scope, "
            "must_hold, build.approach, verification.scale, verification.evidence, "
            "verification.done_when, and plain_language; add build.semantics, build.order, "
            "or an intermediate gate only "
            "when the work materially requires them; "
            "stage the JSON once, show its exact easy explanation once with the emitted "
            "contract_id, keep the original technical contract hidden unless requested, "
            "and offer approve, request changes, cancel, or view-original choices; "
            "obtain approval, arm the later approval turn, then pass only that exact id. "
            "Do not resend the JSON. In Guarded mode, arm is optional because the "
            "persistent preference already activates the gate. If the user does not want "
            "Click for this turn, run "
            "`click-gate bypass` only after the current user turn begins with a recognized "
            "first-line `@Click bypass` directive or trusted Click autocomplete mention. "
            "Use the corresponding `@Click cancel` form plus `click-gate cancel` to discard "
            "an active contract instead of bypassing it.",
            event=event, code="approval-required",
        )


_HOST_ROUTER = click_host_router.HostRouter(
    click_host_router.HostHandlers(
        pre_tool=_handle_pre_tool,
        post_tool=_handle_post_tool,
        prompt_submit=_handle_prompt_submit,
        session_end=_handle_session_end,
    ),
    set_output_adapter=_set_output_adapter,
    set_output_sink=_set_output_sink,
)


def host_router() -> click_host_router.HostRouter:
    """Return the supported routing interface used by bundled host adapters."""
    return _HOST_ROUTER


def _record_verification_result(
    path: Path,
    batch: dict[str, Any],
    batch_digest: str,
    runner_token: str,
    exit_code: int,
    succeeded_count: int,
    workspace_changed: bool = False,
    workspace_root: str = "",
    workspace_digest: str = "",
    environment_digests: dict[str, str] | None = None,
    source_durations_ms: dict[str, int | float] | None = None,
    dependency_observations: dict[str, dict[str, Any]] | None = None,
    authoritative_observations: dict[str, dict[str, Any]] | None = None,
    shadow_observer_records: dict[str, dict[str, Any]] | None = None,
    shadow_intelligence_baselines: dict[str, dict[str, Any]] | None = None,
    shadow_source_exit_codes: dict[str, int] | None = None,
    shadow_execution_contexts: dict[str, dict[str, Any]] | None = None,
    observer_mode: str | None = None,
    *,
    source_results: dict[str, dict[str, Any]] | None = None,
    runner_started_ns: int | None = None,
) -> bool:
    return click_verification.record_result(
        path,
        batch,
        batch_digest,
        runner_token,
        exit_code,
        succeeded_count,
        workspace_changed=workspace_changed,
        workspace_root=workspace_root,
        workspace_digest=workspace_digest,
        environment_digests=environment_digests,
        source_durations_ms=source_durations_ms,
        dependency_observations=dependency_observations,
        authoritative_observations=authoritative_observations,
        shadow_observer_records=shadow_observer_records,
        shadow_intelligence_baselines=shadow_intelligence_baselines,
        shadow_source_exit_codes=shadow_source_exit_codes,
        shadow_execution_contexts=shadow_execution_contexts,
        observer_mode=observer_mode,
        source_results=source_results,
        runner_started_ns=runner_started_ns,
        git_capture=click_verification.git_capture,
    )


def _claim_observation_run(
    path: Path, raw: str, command_digest: str, runner_token: str
) -> tuple[dict[str, Any] | None, str]:
    return click_observation.claim_run(
        path,
        raw,
        command_digest,
        runner_token,
        protocol_version=CAPABILITY_PROTOCOL_VERSION,
    )


def _run_inspection_request(
    request: dict[str, Any], state_result: tuple[Path, str, str] | None = None
) -> int:
    return click_observation.run_request(
        request,
        state_result,
        execute_commands=click_inspection.execute_commands,
    )


def _run_inspection_once(arguments: list[str]) -> int:
    return click_inspection.run_once(
        arguments,
        run_request=_run_inspection_request,
        protocol_version=CAPABILITY_PROTOCOL_VERSION,
    )


def _run_observation(arguments: list[str]) -> int:
    return click_observation.run(
        arguments,
        run_inspection_request=_run_inspection_request,
        protocol_version=CAPABILITY_PROTOCOL_VERSION,
    )


def _claim_mutation_run(
    path: Path, raw: str, request_digest: str, runner_token: str
) -> tuple[dict[str, Any] | None, str]:
    return click_mutation.claim_run(
        path,
        raw,
        request_digest,
        runner_token,
        validate_argv=click_capability.validate_argv,
        looks_like_managed_service=click_service.looks_like_managed_service,
        protocol_version=CAPABILITY_PROTOCOL_VERSION,
    )


def _run_mutation(arguments: list[str]) -> int:
    return click_mutation.run(
        arguments,
        validate_argv=click_capability.validate_argv,
        looks_like_managed_service=click_service.looks_like_managed_service,
        protocol_version=CAPABILITY_PROTOCOL_VERSION,
        execute_commands=click_inspection.execute_argv_commands,
    )


def _managed_contract_path(path: Path) -> bool:
    return click_state.managed_state_path(path, ("session-contract-",))


def _run_service_supervisor(arguments: list[str]) -> int:
    return click_service.run_service_supervisor(
        arguments,
        validate_argv=click_capability.validate_argv,
        protocol_version=CAPABILITY_PROTOCOL_VERSION,
        execution_argv=click_inspection.execution_argv,
    )


def _run_service_start(arguments: list[str]) -> int:
    return click_service.run_service_start(
        arguments,
        validate_argv=click_capability.validate_argv,
        protocol_version=CAPABILITY_PROTOCOL_VERSION,
        runner_script=Path(__file__).resolve(),
    )


def _run_service_stop(arguments: list[str]) -> int:
    return click_service.run_service_stop(
        arguments,
        snapshot_reader=click_service.service_snapshot,
    )


def _run_dashboard_start(arguments: list[str]) -> int:
    return click_shadow_dashboard.run_start(
        arguments,
        runner_script=Path(__file__).resolve(),
    )


def _run_dashboard_stop(arguments: list[str]) -> int:
    return click_shadow_dashboard.run_stop(arguments)


def _run_dashboard_status(arguments: list[str]) -> int:
    return click_shadow_dashboard.run_status(arguments)


def _run_dashboard_server(arguments: list[str]) -> int:
    return click_shadow_dashboard.run_server(arguments)




def _claim_verification_run(
    state_path: Path,
    raw: str,
    batch_digest: str,
    runner_token: str,
) -> tuple[dict[str, Any] | None, str]:
    return click_verification.claim_run(
        state_path,
        raw,
        batch_digest,
        runner_token,
        file_content_digest=click_verification.file_content_digest,
    )


def _release_unclaimed_verification_reservation(
    state_path: Path, batch_digest: str, runner_token: str
) -> bool:
    return click_verification.release_unclaimed_reservation(
        state_path, batch_digest, runner_token
    )


def _run_verification(arguments: list[str]) -> int:
    return click_verification.run(
        arguments,
        file_content_digest=click_verification.file_content_digest,
        git_workspace_snapshot=click_verification.git_workspace_snapshot,
        git_metadata_present=click_inspection.git_metadata_present,
        execute_commands=click_inspection.execute_argv_commands,
        git_capture=click_verification.git_capture,
    )


def _run_receipt_export(arguments: list[str]) -> int:
    (click_receipt_runtime,) = click_import_bootstrap.load_siblings(
        __package__, "click_receipt_runtime"
    )
    if len(arguments) not in {3, 4}:
        sys.stderr.write(
            "usage: click_gate.py run-receipt-export "
            "<state> <cwd> <host> [explicit|ambient]\n"
        )
        return 2
    state_path = Path(arguments[0])
    workspace = Path(arguments[1])
    host_id = arguments[2]
    workspace_source = arguments[3] if len(arguments) == 4 else "explicit"
    if workspace_source not in {"explicit", "ambient"}:
        sys.stderr.write("Click receipt export received an invalid workspace source.\n")
        return 2
    if not _managed_contract_path(state_path) or not workspace.is_absolute():
        sys.stderr.write("Click receipt export received an invalid state or workspace.\n")
        return 2
    try:
        workspace = workspace.resolve(strict=True)
    except (OSError, RuntimeError):
        sys.stderr.write("Click receipt export workspace could not be resolved.\n")
        return 2
    with click_state.state_lock():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            sys.stderr.write("Click receipt export could not read contract state.\n")
            return 2
        if not isinstance(state, dict) or not click_lifecycle.contract_is_completed(state):
            sys.stderr.write("Click receipt export requires a completed contract.\n")
            return 2
        if workspace_source == "ambient":
            verified_workspace = click_receipt_runtime.verified_workspace_root(
                state,
                expected_contract_schema_version=CONTRACT_STATE_SCHEMA_VERSION,
            )
            if verified_workspace is not None:
                workspace = verified_workspace
        envelope, error = click_receipt_runtime.build_envelope(
            state,
            workspace_snapshot=click_verification.git_workspace_snapshot(workspace),
            host_coverage=click_host_coverage.receipt(host_id),
            expected_contract_schema_version=CONTRACT_STATE_SCHEMA_VERSION,
        )
    if error:
        sys.stderr.write(f"{error}\n")
        return 2
    assert envelope is not None
    sys.stdout.write(click_receipt_runtime.render_envelope(envelope) + "\n")
    return 0


def _run_receipt_verify(arguments: list[str]) -> int:
    (click_receipt_runtime,) = click_import_bootstrap.load_siblings(
        __package__, "click_receipt_runtime"
    )
    if len(arguments) != 1:
        sys.stderr.write("usage: click_gate.py run-receipt-verify <path>\n")
        return 2
    report, error = click_receipt_runtime.verify_file(Path(arguments[0]))
    if error:
        sys.stderr.write(f"{error}\n")
        return 2
    assert report is not None
    sys.stdout.write(
        json.dumps(
            report,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


def _run_json_report(arguments: list[str]) -> int:
    summary = arguments[1:] == [JSON_REPORT_SUMMARY_FLAG]
    if not arguments or (len(arguments) != 1 and not summary):
        sys.stderr.write("Click JSON report runner arguments were invalid.\n")
        return 2
    path = Path(arguments[0])
    try:
        root = click_state.state_root().resolve(strict=True)
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
        if (
            path != resolved
            or resolved.parent != root
            or not resolved.name.startswith(JSON_REPORT_FILE_PREFIX)
            or resolved.suffix != ".json"
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > JSON_REPORT_MAX_BYTES
        ):
            raise ValueError("invalid report path")
        flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
        flags |= int(getattr(os, "O_NOFOLLOW", 0))
        descriptor = os.open(resolved, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > JSON_REPORT_MAX_BYTES:
                raise ValueError("invalid report file")
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                descriptor = -1
                raw = stream.read(JSON_REPORT_MAX_BYTES + 1)
            if len(raw) > JSON_REPORT_MAX_BYTES:
                raise ValueError("report too large")
            report = json.loads(raw)
            if not isinstance(report, dict):
                raise ValueError("invalid report")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            resolved.unlink(missing_ok=True)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        sys.stderr.write("Click JSON report was unavailable.\n")
        return 2
    if summary:
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.write("\n".join(_status_summary_lines(report)) + "\n")
        return 0
    sys.stdout.write(json.dumps(report, sort_keys=True, ensure_ascii=True) + "\n")
    return 0


STATEFUL_RUNNER_ACTIONS = {
    "run-observation",
    "run-mutation",
    "run-receipt-export",
    "run-service-start",
    "run-service-stop",
    "run-service-supervisor",
    "run-dashboard-start",
    "run-dashboard-stop",
    "run-dashboard-status",
    "run-dashboard-server",
    "run-json-report",
    "run-verification",
}


def _runner_arguments(arguments: list[str]) -> tuple[list[str], str]:
    """Adopt only an explicit absolute gate-state root for internal runners."""
    if not arguments:
        return arguments, ""
    if arguments[0] in STATEFUL_RUNNER_ACTIONS:
        return [], "Click stateful runner requires an explicit state-root binding."
    if arguments[0] != "--state-root":
        return arguments, ""
    if len(arguments) < 4 or arguments[2] not in STATEFUL_RUNNER_ACTIONS:
        return [], "Click runner state-root binding is malformed."
    state_root = Path(arguments[1])
    if not state_root.is_absolute():
        return [], "Click runner state-root binding is invalid."
    try:
        resolved = state_root.resolve(strict=True)
    except (OSError, RuntimeError):
        return [], "Click runner state-root binding could not be resolved."
    if resolved.name != "gate-state" or state_root != resolved:
        return [], "Click runner state-root binding is invalid."
    state_path = Path(arguments[3])
    if not state_path.is_absolute():
        return [], "Click runner state path is invalid."
    try:
        resolved_state = state_path.resolve(strict=True)
    except (OSError, RuntimeError):
        return [], "Click runner state path could not be resolved."
    if state_path != resolved_state or resolved_state.parent != resolved:
        return [], "Click runner state path does not match its bound state root."
    os.environ["PLUGIN_DATA"] = str(resolved.parent)
    return arguments[2:], ""


def main() -> int:
    arguments = sys.argv[1:]
    if arguments and arguments[0] == "--encoded-runner":
        if len(arguments) != 2:
            sys.stderr.write("Click runner transport was malformed.\n")
            return 2
        decoded, transport_error = click_runner_transport.decode_runner_transport(
            arguments[1]
        )
        if transport_error or decoded is None:
            sys.stderr.write(f"{transport_error}\n")
            return 2
        arguments = decoded
    arguments, runner_error = _runner_arguments(arguments)
    if runner_error:
        sys.stderr.write(f"{runner_error}\n")
        return 2
    if arguments and arguments[0] == "run-inspection-once":
        return _run_inspection_once(arguments[1:])
    if arguments and arguments[0] == "run-observation":
        return _run_observation(arguments[1:])
    if arguments and arguments[0] == "run-mutation":
        return _run_mutation(arguments[1:])
    if arguments and arguments[0] == "run-service-start":
        return _run_service_start(arguments[1:])
    if arguments and arguments[0] == "run-service-stop":
        return _run_service_stop(arguments[1:])
    if arguments and arguments[0] == "run-service-supervisor":
        return _run_service_supervisor(arguments[1:])
    if arguments and arguments[0] == "run-dashboard-start":
        return _run_dashboard_start(arguments[1:])
    if arguments and arguments[0] == "run-dashboard-stop":
        return _run_dashboard_stop(arguments[1:])
    if arguments and arguments[0] == "run-dashboard-status":
        return _run_dashboard_status(arguments[1:])
    if arguments and arguments[0] == "run-dashboard-server":
        return _run_dashboard_server(arguments[1:])
    if arguments and arguments[0] == "run-json-report":
        return _run_json_report(arguments[1:])
    if arguments and arguments[0] == "run-verification":
        return _run_verification(arguments[1:])
    if arguments and arguments[0] == "run-receipt-export":
        return _run_receipt_export(arguments[1:])
    if arguments and arguments[0] == "run-receipt-verify":
        return _run_receipt_verify(arguments[1:])
    if len(arguments) != 1 or arguments[0] not in {
        "post-tool",
        "pre-tool",
        "prompt-submit",
        "session-end",
    }:
        sys.stderr.write(
            "usage: click_gate.py pre-tool|post-tool|prompt-submit|session-end\n"
        )
        return 1
    event: dict[str, Any] | None = None
    try:
        event = _read_event()
        with click_state.state_lock():
            _HOST_ROUTER.dispatch(arguments[0], event)
    except OSError as exc:
        if arguments[0] == "pre-tool" and event is not None and _unrecorded_evidence_read(event, exc):
            return 0
        sys.stderr.write(f"click hook error: {exc}\n")
        return 1
    except ValueError as exc:
        sys.stderr.write(f"click hook error: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
