"""Pure verification request validation, classification and command grouping.

No state, runner or cache authority is owned here. Caller class labels and legacy
units remain compatible metadata and never widen receipt authority.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
from typing import Any

if __package__:
    from . import click_import_bootstrap
else:  # Installed launchers execute hooks directly.
    import click_import_bootstrap

(click_capability, click_diagnostics, click_evidence, click_inspection, click_verification_adapters, click_verification_inputs, click_verification_meter,) = click_import_bootstrap.load_siblings(
    __package__, "click_capability", "click_diagnostics", "click_evidence", "click_inspection", "click_verification_adapters", "click_verification_inputs", "click_verification_meter"
)

LEGACY_PROTOCOL_VERSION = 2
PROTOCOL_VERSION = 3
SUPPORTED_PROTOCOL_VERSIONS = frozenset({LEGACY_PROTOCOL_VERSION, PROTOCOL_VERSION})
BATCH_FIELDS = {
    "version", "checks", "workdir", "reporting", "failure_collection"
}
LEGACY_CHECK_FIELDS = {"evidence_id", "argv", "class"}
CHECK_FIELDS = LEGACY_CHECK_FIELDS | {"reuse", "inputs", "outputs_required"}
VERIFICATION_CLASSES = click_verification_meter.VERIFICATION_CLASSES
PYTHON_VERIFICATION_MODULES = click_verification_adapters.PYTHON_VERIFICATION_MODULES
PYTHON_VERIFICATION_EXECUTABLES = click_verification_adapters.PYTHON_VERIFICATION_EXECUTABLES
VERSIONED_PYTHON_EXECUTABLE = click_verification_adapters.VERSIONED_PYTHON_EXECUTABLE
DEEP_VERIFICATION_EXECUTABLES = click_verification_adapters.DEEP_VERIFICATION_EXECUTABLES
DEEP_VERIFICATION_MARKERS = click_verification_adapters.DEEP_VERIFICATION_MARKERS
VERIFICATION_EXECUTABLES = click_verification_adapters.VERIFICATION_EXECUTABLES
VERIFICATION_NAME_MARKERS = click_verification_adapters.VERIFICATION_NAME_MARKERS
TEST_TARGET_SUFFIXES = click_verification_adapters.TEST_TARGET_SUFFIXES
TEST_FILTER_OPTIONS = click_verification_adapters.TEST_FILTER_OPTIONS
TEST_OPTIONS_WITH_VALUES = click_verification_adapters.TEST_OPTIONS_WITH_VALUES
NEW_SOURCE_PATH_SEGMENTS = {
    "app", "config", "configs", "lib", "migration", "migrations", "src",
}

VERIFICATION_BATCH_FIELDS = BATCH_FIELDS
VERIFICATION_CHECK_FIELDS = CHECK_FIELDS
VERIFICATION_PROTOCOL_VERSION = PROTOCOL_VERSION
EVIDENCE_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
FAILURE_COLLECTION_VERSION = 1
FAILURE_COLLECTION_MAX_EXTRA_SOURCES = 3
FAILURE_COLLECTION_MAX_EXTRA_FAILURES = 3
FAILURE_COLLECTION_MAX_START_WINDOW_MS = 30_000
FAILURE_COLLECTION_STATE_FIELD = "bounded_failure_collection"
FAILURE_COLLECTION_RESULT_STATUSES = frozenset(
    {"not-triggered", "disabled", "collecting", "completed", "stopped"}
)


def default_failure_collection() -> dict[str, Any]:
    return {
        "version": FAILURE_COLLECTION_VERSION,
        "mode": "off",
        "independent_sources": [],
        "max_extra_sources": 0,
        "max_extra_failures": 0,
        "start_window_ms": 0,
    }


def validate_failure_collection(
    value: Any, checks: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str]:
    if value is None:
        return default_failure_collection(), ""
    fields = {
        "version", "mode", "independent_sources", "max_extra_sources",
        "max_extra_failures", "start_window_ms",
    }
    if not isinstance(value, dict) or set(value) != fields:
        return None, (
            "Verification `failure_collection` must contain only version, mode, "
            "independent_sources, max_extra_sources, max_extra_failures, and "
            "start_window_ms."
        )
    if value.get("version") != FAILURE_COLLECTION_VERSION:
        return None, (
            f"Verification `failure_collection.version` must be "
            f"{FAILURE_COLLECTION_VERSION}."
        )
    mode = value.get("mode")
    independent = value.get("independent_sources")
    extra_sources = value.get("max_extra_sources")
    extra_failures = value.get("max_extra_failures")
    start_window_ms = value.get("start_window_ms")
    if mode not in {"off", "bounded"}:
        return None, "Verification `failure_collection.mode` must be off or bounded."
    if (
        not isinstance(independent, list)
        or any(
            not isinstance(item, str)
            or EVIDENCE_ID_PATTERN.fullmatch(item) is None
            for item in independent
        )
        or len(set(independent)) != len(independent)
    ):
        return None, (
            "Verification `failure_collection.independent_sources` must contain "
            "distinct evidence ids."
        )
    numeric = (extra_sources, extra_failures, start_window_ms)
    if any(not isinstance(item, int) or isinstance(item, bool) for item in numeric):
        return None, "Verification failure-collection budgets must be integers."
    if mode == "off":
        if independent or any(numeric):
            return None, "Disabled failure collection must use empty, zero budgets."
        return default_failure_collection(), ""
    submitted_ids = {
        str(check.get("evidence_id", ""))
        for check in checks
        if isinstance(check, dict)
    }
    if len(independent) < 2 or not set(independent).issubset(submitted_ids):
        return None, (
            "Bounded failure collection requires at least two explicitly submitted "
            "independent evidence ids."
        )
    if not 1 <= extra_sources <= FAILURE_COLLECTION_MAX_EXTRA_SOURCES:
        return None, "Bounded failure collection allows 1..3 extra sources."
    if not 1 <= extra_failures <= FAILURE_COLLECTION_MAX_EXTRA_FAILURES:
        return None, "Bounded failure collection allows 1..3 extra failures."
    if not 1 <= start_window_ms <= FAILURE_COLLECTION_MAX_START_WINDOW_MS:
        return None, "Bounded failure collection start_window_ms must be 1..30000."
    return json.loads(json.dumps(value)), ""


def failure_collection_result_is_valid(value: Any) -> bool:
    fields = {
        "version", "batch_ref", "requested_mode", "status", "first_failure_source_id",
        "admitted_source_ids", "additional_sources_started",
        "additional_failures", "boundary_checks", "boundary_check_ms",
        "stop_reason",
    }
    return bool(
        isinstance(value, dict)
        and set(value) == fields
        and value.get("version") == FAILURE_COLLECTION_VERSION
        and isinstance(value.get("batch_ref"), str)
        and re.fullmatch(r"[0-9a-f]{64}", value["batch_ref"])
        and value.get("requested_mode") in {"off", "bounded"}
        and value.get("status") in FAILURE_COLLECTION_RESULT_STATUSES
        and isinstance(value.get("first_failure_source_id"), str)
        and isinstance(value.get("admitted_source_ids"), list)
        and all(
            isinstance(item, str) and EVIDENCE_ID_PATTERN.fullmatch(item)
            for item in value["admitted_source_ids"]
        )
        and all(
            isinstance(value.get(field), int)
            and not isinstance(value.get(field), bool)
            and value[field] >= 0
            for field in (
                "additional_sources_started", "additional_failures",
                "boundary_checks",
            )
        )
        and isinstance(value.get("boundary_check_ms"), (int, float))
        and not isinstance(value.get("boundary_check_ms"), bool)
        and value["boundary_check_ms"] >= 0
        and isinstance(value.get("stop_reason"), str)
        and 0 < len(value["stop_reason"]) <= 64
    )



def validate_verification_batch(
    raw: str,
    scale: str,
    evidence_sources: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, int, str]:
    try:
        preview = json.loads(raw)
    except json.JSONDecodeError:
        return None, 0, "Verification batch request must be valid JSON."
    if not isinstance(preview, dict):
        return None, 0, "Verification batch request must be a JSON object."
    request_version = preview.get("version")
    if request_version not in SUPPORTED_PROTOCOL_VERSIONS or isinstance(
        request_version, bool
    ):
        return None, 0, "Verification batch request `version` must be 2 or 3."
    value, error = click_capability.decode_request(
        raw, "Verification batch", version=int(request_version)
    )
    if error or value is None:
        return None, 0, error
    if "commands" in value:
        return (
            None,
            0,
            "Click verification uses `checks` with argv arrays and a submitted "
            "`class`; legacy shell-string `commands` are no longer accepted.",
        )
    unknown = sorted(set(value) - VERIFICATION_BATCH_FIELDS)
    if unknown:
        rendered = ", ".join(f"`{field}`" for field in unknown)
        return None, 0, f"Verification batch contains unsupported field(s): {rendered}."
    reporting, reporting_error = click_diagnostics.validate_reporting(
        value.get("reporting")
    )
    if reporting_error:
        return None, 0, reporting_error
    assert reporting is not None
    workdir = value.get("workdir")
    if workdir is not None and (
        not isinstance(workdir, str)
        or not workdir
        or "\x00" in workdir
        or not Path(workdir).is_absolute()
    ):
        return (
            None,
            0,
            "Verification batch `workdir` must be a non-empty absolute path when supplied.",
        )
    checks = value.get("checks")
    if not isinstance(checks, list) or not checks:
        return None, 0, "Verification batch `checks` must be a non-empty list."
    normalized: list[dict[str, Any]] = []
    units = 0
    for index, check in enumerate(checks, start=1):
        if not isinstance(check, dict):
            return None, 0, f"Verification check {index} must be an object."
        allowed_check_fields = (
            LEGACY_CHECK_FIELDS
            if request_version == LEGACY_PROTOCOL_VERSION
            else VERIFICATION_CHECK_FIELDS
        )
        unknown_check = sorted(set(check) - allowed_check_fields)
        if unknown_check:
            rendered = ", ".join(f"`{field}`" for field in unknown_check)
            return None, 0, f"Verification check {index} has unsupported field(s): {rendered}."
        evidence_id = check.get("evidence_id")
        if evidence_sources is not None:
            if not isinstance(evidence_id, str) or not EVIDENCE_ID_PATTERN.fullmatch(
                evidence_id
            ):
                return (
                    None,
                    0,
                    f"Verification check {index} `evidence_id` must name one declared "
                    "argv evidence source.",
                )
            source = evidence_sources.get(click_evidence.evidence_key(evidence_id))
            if not isinstance(source, dict):
                return (
                    None,
                    0,
                    f"Verification check {index} references unknown evidence id "
                    f"`{evidence_id}`.",
                )
            if source.get("kind") != "argv":
                return (
                    None,
                    0,
                    f"Verification check {index} evidence `{evidence_id}` has kind "
                    f"`{source.get('kind')}`, not `argv`.",
                )
        elif evidence_id is not None and (
            not isinstance(evidence_id, str)
            or not EVIDENCE_ID_PATTERN.fullmatch(evidence_id)
        ):
            return None, 0, f"Verification check {index} `evidence_id` is invalid."
        argv, argv_error = click_capability.validate_argv(check.get("argv"), f"Verification check {index}")
        if argv_error:
            return None, 0, argv_error
        assert argv is not None
        read_only = click_inspection.is_read_only_tokens(list(argv))
        minimum_class = (
            "broad" if read_only and click_inspection.is_broad_exploration_tokens(argv) else "targeted"
        ) if read_only else minimum_verification_class(argv)
        if minimum_class is None:
            return (
                None,
                0,
                f"Verification check {index} is neither read-only nor a recognized check.",
            )
        check_class = check.get("class")
        if check_class not in VERIFICATION_CLASSES:
            allowed = ", ".join(VERIFICATION_CLASSES)
            return None, 0, f"Verification check {index} `class` must be one of: {allowed}."
        effective_class = click_verification_meter.effective_class(
            check_class, minimum_class
        )
        assert effective_class is not None
        effective_units = click_verification_meter.class_units(effective_class)
        assert effective_units is not None
        units += effective_units
        normalized_check: dict[str, Any] = {
            "argv": argv,
            "class": effective_class,
        }
        reuse_policy = check.get("reuse", "conditional")
        if reuse_policy not in click_verification_inputs.REUSE_POLICIES:
            return (
                None,
                0,
                f"Verification check {index} `reuse` must be conditional, rerun, or always-run.",
            )
        input_patterns, input_error = click_verification_inputs.normalize_patterns(
            check.get("inputs", [])
        )
        if input_error or input_patterns is None:
            return (
                None,
                0,
                f"Verification check {index} inputs are invalid: {input_error}.",
            )
        outputs_required = check.get("outputs_required", False)
        if not isinstance(outputs_required, bool):
            return (
                None,
                0,
                f"Verification check {index} `outputs_required` must be boolean.",
            )
        if reuse_policy != "conditional":
            normalized_check["reuse"] = reuse_policy
        if input_patterns:
            normalized_check["inputs"] = list(input_patterns)
        if outputs_required:
            normalized_check["outputs_required"] = True
        if isinstance(evidence_id, str):
            normalized_check["evidence_id"] = evidence_id
        normalized.append(normalized_check)
    normalized_batch = {
        "version": VERIFICATION_PROTOCOL_VERSION,
        "checks": normalized,
        "reporting": reporting,
    }
    failure_collection, collection_error = validate_failure_collection(
        value.get("failure_collection"), normalized
    )
    if collection_error:
        return None, 0, collection_error
    assert failure_collection is not None
    normalized_batch["failure_collection"] = failure_collection
    if isinstance(workdir, str):
        normalized_batch["workdir"] = workdir
    return normalized_batch, units, ""


def verification_group_policy(
    checks: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    """Return one conservative policy shared by an evidence check group."""

    values = {
        (
            str(check.get("reuse", "conditional")),
            tuple(check.get("inputs", [])),
            bool(check.get("outputs_required", False)),
        )
        for check in checks
    }
    if len(values) != 1:
        return None, (
            "Checks for one argv evidence id must use the same reuse, inputs, "
            "and outputs_required policy."
        )
    reuse_policy, inputs, outputs_required = next(iter(values))
    return {
        "reuse": reuse_policy,
        "inputs": list(inputs),
        "outputs_required": outputs_required,
    }, ""


def verification_groups(
    batch: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], str]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    completed_groups: set[str] = set()
    active_group = ""
    for index, check in enumerate(batch["checks"], start=1):
        evidence_id = check.get("evidence_id")
        if not isinstance(evidence_id, str) or not EVIDENCE_ID_PATTERN.fullmatch(
            evidence_id
        ):
            return {}, (
                f"Verification check {index} `evidence_id` must name one declared "
                "argv evidence source."
            )
        source_key = click_evidence.evidence_key(evidence_id)
        if source_key != active_group:
            if source_key in completed_groups:
                return {}, (
                    "Checks for one argv evidence id must be adjacent in a verification "
                    "batch so partial failure can be recorded deterministically."
                )
            if active_group:
                completed_groups.add(active_group)
            active_group = source_key
        grouped.setdefault(source_key, []).append(check)
    return grouped, ""


def verification_group_digest(checks: list[dict[str, Any]]) -> str:
    # Receipt identity is the executable request, not Click's compatibility
    # class or legacy unit heuristic.
    payload = [{"argv": check["argv"]} for check in checks]
    return click_capability.digest({"checks": payload})


def verification_command_digest(check: dict[str, Any]) -> str:
    return click_capability.digest({"argv": check["argv"]})


def verification_command_plans(
    batch: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    plans: dict[str, list[dict[str, Any]]] = {}
    source_positions: dict[str, int] = {}
    for position, check in enumerate(batch["checks"], start=1):
        source_key = click_evidence.evidence_key(str(check["evidence_id"]))
        source_position = source_positions.get(source_key, 0) + 1
        source_positions[source_key] = source_position
        plans.setdefault(source_key, []).append(
            {
                "position": position,
                "source_position": source_position,
                "check_digest": verification_command_digest(check),
            }
        )
    return plans


def verification_group_units(checks: list[dict[str, Any]]) -> int:
    units = click_verification_meter.total_units(check["class"] for check in checks)
    assert units is not None
    return units



contains_deep_verification_marker = (
    click_verification_adapters.contains_deep_verification_marker
)
arguments_have_filter = click_verification_adapters.arguments_have_filter
verification_targets = click_verification_adapters.verification_targets
scope_with_kind_floor = click_verification_adapters.scope_with_kind_floor
minimum_test_runner_class = click_verification_adapters.minimum_test_runner_class
minimum_verification_class = click_verification_adapters.minimum_verification_class


def is_recognized_verification_tokens(tokens: list[str]) -> bool:
    return minimum_verification_class(tokens) is not None


def is_recognized_verification_command(command: str) -> bool:
    segments = click_capability.shell_segments(command)
    if segments:
        return any(is_recognized_verification_tokens(segment) for segment in segments)
    try:
        fallback = shlex.split(command, posix=True)
    except ValueError:
        return False
    return is_recognized_verification_tokens(fallback)
