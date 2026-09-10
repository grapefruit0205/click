#!/usr/bin/env python3
"""Sanitized read model for Click's Incremental Verification Dashboard.

This module composes the authoritative canonical execution plan with separate
Shadow telemetry.  It is a read-only projection: it cannot create reuse
authority, change evidence, or reinterpret a Shadow prediction as a skip.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import time
from typing import Any

if __package__:
    from . import (
        click_dependency_cache,
        click_reuse_readiness,
        click_observer_common,
        click_incremental,
        click_observer_control,
        click_sharding_setup,
        click_shadow_intelligence,
    )
else:  # Executed beside the bundled hook modules.
    import click_dependency_cache
    import click_reuse_readiness
    import click_observer_common
    import click_incremental
    import click_observer_control
    import click_sharding_setup
    import click_shadow_intelligence


PROJECTION_VERSION = 10
LEGACY_PROJECTION_VERSION = 4
LEGACY_PROJECTION_VERSIONS = frozenset({4, 5, 6, 7, 8, 9})
PROJECTION_MODE = "incremental-verification"
MAX_SOURCES = click_shadow_intelligence.MAX_STATE_SOURCES
MAX_INPUTS = click_shadow_intelligence.MAX_PROJECTION_INPUTS
MAX_CHANGED_INPUTS = 8
MAX_BYTES = click_shadow_intelligence.MAX_PROJECTION_BYTES
MAX_JSON_SAFE_INTEGER = click_shadow_intelligence.MAX_JSON_SAFE_INTEGER
MAX_RECENT_BATCHES = 30

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_STATUS = re.compile(r"^[a-z0-9-]{1,32}$")
_SOURCE_STATUSES = frozenset(
    {"ready", "running", "observed", "passed", "failed", "stale", "unknown"}
)
_EXECUTION_DECISIONS = frozenset({*click_incremental.DECISIONS, "not-planned"})
_INPUT_STATUSES = frozenset(
    {"current-observed", "changed", "baseline-only", "newly-observed"}
)

_V4_FIELDS = frozenset(
    {"version", "mode", "generated_at", "task", "summary", "sources", "map", "batches", "history", "accounting", "controls", "engine"}
)
_V5_FIELDS = _V4_FIELDS | {"batch_summaries"}
_V6_FIELDS = _V5_FIELDS | {"setup"}
_V7_FIELDS = _V6_FIELDS | {"retained_impact"}
_V8_FIELDS = _V7_FIELDS | {"task_efficiency"}
_V9_FIELDS = _V8_FIELDS
_FIELDS = _V9_FIELDS | {"readiness"}
_TASK_EFFICIENCY_FIELDS = frozenset(
    {"kind", "version", "generated_at", "measurement_status", "measurement_reason", "presentations"}
)
_TASK_FIELDS = frozenset(
    {
        "runtime_mode",
        "status",
        "mutation_revision",
        "observer_mode",
        "observer_enabled",
        "name", "promises", "in_scope", "out_of_scope", "must_hold", "contract_id", "approval_bound",
    }
)
_LEGACY_SUMMARY_FIELDS = frozenset({"incremental", "shadow"})
_SUMMARY_FIELDS = _LEGACY_SUMMARY_FIELDS | {"revalidation_savings"}
_BATCH_SUMMARY_FIELDS = frozenset({"incremental", "revalidation_savings"})
_LEGACY_SETUP_FIELDS = frozenset(
    {
        "version",
        "status",
        "sharding_ready",
        "reuse_ready",
        "reuse_status",
        "initial_setup_ms",
        "observation_ms",
        "click_processing_ms",
        "bootstrap_parent_ms",
        "bootstrap_shards_ms",
        "comparison_net_ms",
        "comparison_scope",
    }
)
_SETUP_FIELDS = _LEGACY_SETUP_FIELDS | frozenset(
    {
        "command_status",
        "inventory_status",
        "exact_reuse_status",
        "policy_reuse_status",
        "authoritative_reuse_status",
        "next_action_code",
    }
)
_INCREMENTAL_FIELDS = frozenset(click_incremental.SUMMARY_FIELDS) | {"current_source_count"}
_TIME_FIELDS = frozenset({"executed_duration_ms", "estimated_avoided_ms", "request_wall_ms", "measured_processing_ms"})
_SHADOW_FIELDS = frozenset(
    {
        "candidate_count",
        "evaluated_source_count",
        "confirmed_candidate_count",
        "contradiction_count",
        "observer_overhead_ms",
        "potential_ms",
        "tracing_slowdown_measured",
    }
)
_SOURCE_FIELDS = frozenset(
    {
        "id",
        "label",
        "status",
        "execution_decision",
        "reason_code",
        "authority_source",
        "current_revision",
        "previous_revision",
        "estimated_avoided_ms",
        "observer_status",
        "input_count",
        "visible_input_count",
        "external_input_count",
        "changed_input_count",
        "changed_inputs",
        "shadow_decision",
        "shadow_reason",
        "shadow_limitations",
        "shadow_outcome",
        "execution_status", "execution_reason_code", "duration_ms", "duration_baseline",
        "reuse_origin",
        "check_digest", "origin_name", "origin_check_label", "next_action",
    }
)
_MAP_FIELDS = frozenset(
    {
        "nodes",
        "edges",
        "total_input_count",
        "visible_input_count",
        "truncated_input_count",
    }
)
_NODE_FIELDS = frozenset({"id", "type", "label", "kind", "status"})
_EDGE_FIELDS = frozenset({"source", "target", "operations"})


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _is_count(value: Any, *, minimum: int = 0) -> bool:
    return bool(
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= MAX_JSON_SAFE_INTEGER
    )


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _safe_relative_path(value: Any, *, directory: bool = False) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\\" in value
        or len(os.fsencode(value))
        > click_dependency_cache.MAX_SHADOW_OBSERVER_PATH_BYTES
    ):
        return False
    candidate = value[:-1] if directory and value.endswith("/") else value
    if not candidate:
        return False
    path = PurePosixPath(candidate)
    return not path.is_absolute() and all(
        part not in {"", ".", ".."} for part in path.parts
    )


def _source_status(value: Any) -> str:
    status = value.get("status") if isinstance(value, dict) else "unknown"
    return status if status in _SOURCE_STATUSES else "unknown"


def engine_identity() -> dict[str, Any]:
    """File provenance only, never a signed or independently trusted identity."""
    result = {"version": None, "hook_files_digest": None, "assurance": "unsigned-files-at-snapshot"}
    root = Path(__file__).resolve().parents[1]
    try:
        version = json.loads((root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version")
        if isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+(?:\+codex\.\d{14})?", version):
            result["version"] = version
    except (OSError, ValueError, TypeError):
        pass
    try:
        digest = hashlib.sha256()
        hook_root = root / "hooks"
        paths = list(hook_root.glob("*.py"))
        paths.extend(path for path in (hook_root / "dashboard").rglob("*") if path.is_file())
        if len(paths) > 256:
            return result
        for path in sorted(paths, key=lambda item: item.relative_to(hook_root).as_posix()):
            content = path.read_bytes()
            if len(content) > 2 * 1024 * 1024:
                return result
            relative = path.relative_to(hook_root).as_posix()
            # Top-level Python names retain their existing byte-hash binding;
            # nested frontend names also bind the exact bundled asset path.
            digest.update(relative.encode("utf-8") + b"\0" + hashlib.sha256(content).digest())
        result["hook_files_digest"] = digest.hexdigest()
    except OSError:
        pass
    return result


def _next_action(reason: str, status: str) -> str:
    if status == "passed":
        return "현재 코드에서 직접 통과했습니다. 다음 관련 변경 전에는 재실행이 필요하지 않습니다."
    if status == "reused":
        return "현재 재판정을 통과한 결과입니다. 관련 입력이 바뀌면 다시 검증하세요."
    if status in {"failed", "interrupted"}:
        return "실패 또는 중단 원인을 확인한 뒤 승인 범위에서 같은 검증을 다시 실행하세요."
    if reason == "environment-binding-changed":
        return "현재 환경에서 실제 검증을 실행해 새 기준 결과를 만드세요."
    if reason in {"observed-input-changed", "safe-change-policy-not-covered"}:
        return "관련 입력이 변경됐습니다. 현재 코드로 실제 검증을 실행하세요."
    if reason in {"observer-incomplete", "policy-unavailable", "mutation-boundary-ambiguous", "workspace-ambiguous"}:
        return "재사용 근거가 부족합니다. 정책을 완화하지 말고 현재 상태를 실제 검증하세요."
    return "승인된 검증을 먼저 실행해 현재 상태의 성공 기준을 만드세요."


def _intelligence_state(verification: dict[str, Any]) -> dict[str, Any]:
    value = verification.get(click_shadow_intelligence.SHADOW_INTELLIGENCE_FIELD)
    if not click_shadow_intelligence.state_is_valid(value):
        return click_shadow_intelligence.fresh_state()
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _input_id(source_key: str, path: str, kind: str) -> str:
    identity = hashlib.sha256(
        f"{source_key}\0{kind}\0{path}".encode("utf-8")
    ).hexdigest()[:24]
    return f"input:{identity}"


def _shadow_metrics(intelligence: dict[str, Any]) -> dict[str, Any]:
    candidate_count = 0
    evaluated_count = 0
    confirmed_count = 0
    contradiction_count = 0
    potential_ms = 0
    overhead_ms = 0
    for entry in intelligence["sources"].values():
        prediction = entry.get("prediction", {})
        evaluation = entry.get("evaluation", {})
        if click_shadow_intelligence.prediction_is_valid(prediction):
            candidate_count += int(prediction["decision"] == "reuse-candidate")
        if click_shadow_intelligence.evaluation_is_valid(evaluation):
            evaluated_count += 1
            confirmed_count += int(
                evaluation["outcome"] == "confirmed-candidate"
            )
            contradiction_count += int(
                evaluation["outcome"] == "contradicted-candidate"
            )
            potential_ms = min(
                MAX_JSON_SAFE_INTEGER,
                potential_ms + int(evaluation["gross_potential_ms"]),
            )
            overhead_ms = min(
                MAX_JSON_SAFE_INTEGER,
                overhead_ms + int(evaluation["observer_overhead_ms"]),
            )
    return {
        "candidate_count": candidate_count,
        "evaluated_source_count": evaluated_count,
        "confirmed_candidate_count": confirmed_count,
        "contradiction_count": contradiction_count,
        "observer_overhead_ms": overhead_ms,
        "potential_ms": potential_ms,
        "tracing_slowdown_measured": False,
    }


def dashboard_projection(
    state: Any, *, generated_at: int | None = None
) -> dict[str, Any]:
    """Build the only state shape exposed to the local dashboard server."""

    raw_state = state if isinstance(state, dict) else {}
    presentation = click_incremental.sanitize_presentation(raw_state.get("presentation"))
    verification = raw_state.get("verification")
    verification = verification if isinstance(verification, dict) else {}
    evidence_state = raw_state.get("evidence_state")
    evidence_sources = (
        evidence_state.get("sources", {}) if isinstance(evidence_state, dict) else {}
    )
    evidence_sources = evidence_sources if isinstance(evidence_sources, dict) else {}
    observer_records = click_observer_common.records_from_verification(verification)
    intelligence = _intelligence_state(verification)
    plan = click_incremental.current_plan(verification)
    decisions = {
        item["source_key"]: item for item in plan["decisions"]
    } if plan is not None else {}
    history_view = click_incremental.history_projection(verification, now=generated_at)
    batch = history_view["current"]
    actual = {item["source_key"]: item for item in (batch or {}).get("sources", [])}
    history = history_view["batches"]

    source_keys = sorted(
        {key for key, source in evidence_sources.items() if _is_digest(key)
         and isinstance(source, dict) and source.get("kind", "argv") == "argv"}
        | set(observer_records)
        | set(intelligence["sources"])
        | set(decisions)
        | set(actual)
    )[:MAX_SOURCES]
    sources: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    total_inputs = 0
    visible_inputs = 0

    for index, key in enumerate(source_keys, start=1):
        source_id = f"source:{key[:16]}"
        label = actual[key]["label"] if key in actual else presentation["evidence_labels"].get(key, f"검증 묶음 {index}")
        status = _source_status(evidence_sources.get(key))
        nodes.append(
            {
                "id": source_id,
                "type": "source",
                "label": label,
                "kind": "argv",
                "status": status,
            }
        )

        decision = decisions.get(key)
        if decision is None:
            execution_decision = "not-planned"
            reason_code = ""
            authority_source = "none"
            raw_revision = verification.get("mutation_revision", 0)
            current_revision = raw_revision if _is_count(raw_revision) else 0
            previous_revision = -1
            estimated_avoided_ms = None
        else:
            execution_decision = decision["decision"]
            reason_code = decision["reason_code"]
            authority_source = decision["authority_source"]
            current_revision = int(decision["current_revision"])
            previous_revision = int(decision["previous_revision"])
            estimated_avoided_ms = None
        result = actual.get(key, {})
        execution_status = result.get("status", "not-run" if status == "ready" else "unknown")
        origin = result.get("reuse_origin")
        origin_contract = origin.get("contract_id", "") if isinstance(origin, dict) else ""
        presentation_history = raw_state.get("presentation_history", {})
        origin_presentation = click_incremental.sanitize_presentation(
            presentation_history.get(origin_contract) if isinstance(presentation_history, dict) else None
        )
        duration_baseline = result.get("duration_baseline")
        if (
            result.get("status") == "reused"
            and click_incremental.timing_baseline_is_valid(duration_baseline)
            and duration_baseline["source_key"] == key
            and duration_baseline["check_digest"]
            == result.get("check_digest")
        ):
            estimated_avoided_ms = duration_baseline["duration_ms"]

        observer = observer_records.get(key, {})
        valid_observer = click_dependency_cache.shadow_observer_record_is_valid(
            observer
        )
        current_inputs = observer["inputs"] if valid_observer else []
        observer_status = str(observer["status"]) if valid_observer else "unavailable"
        external_count = int(observer["external_input_count"]) if valid_observer else 0

        entry = intelligence["sources"].get(key, {})
        baseline = entry.get("baseline", {}) if isinstance(entry, dict) else {}
        prediction = entry.get("prediction", {}) if isinstance(entry, dict) else {}
        evaluation = entry.get("evaluation", {}) if isinstance(entry, dict) else {}
        valid_baseline = click_shadow_intelligence.baseline_is_valid(baseline)
        valid_prediction = click_shadow_intelligence.prediction_is_valid(prediction)
        valid_evaluation = click_shadow_intelligence.evaluation_is_valid(evaluation)
        baseline_inputs = baseline["inputs"] if valid_baseline else []
        changed = set(prediction["changed_inputs"]) if valid_prediction else set()
        changed_inputs = sorted(changed)[:MAX_CHANGED_INPUTS]

        baseline_by_key = {
            (item["path"], item["kind"]): item for item in baseline_inputs
        }
        current_by_key = {
            (item["path"], item["kind"]): item for item in current_inputs
        }
        input_keys = sorted(set(baseline_by_key) | set(current_by_key))
        total_inputs += len(input_keys)
        source_visible = 0
        for path, kind in input_keys:
            if visible_inputs >= MAX_INPUTS:
                continue
            previous = baseline_by_key.get((path, kind))
            current = current_by_key.get((path, kind))
            if previous is None:
                input_status = "newly-observed"
            elif current is None:
                input_status = "baseline-only"
            elif path in changed:
                input_status = "changed"
            else:
                input_status = "current-observed"
            operations = list(
                (current if current is not None else previous)["operations"]
            )
            input_id = _input_id(key, path, kind)
            nodes.append(
                {
                    "id": input_id,
                    "type": "input",
                    "label": path,
                    "kind": kind,
                    "status": input_status,
                }
            )
            edges.append(
                {
                    "source": source_id,
                    "target": input_id,
                    "operations": operations,
                }
            )
            visible_inputs += 1
            source_visible += 1

        sources.append(
            {
                "id": source_id,
                "label": label,
                "status": status,
                "execution_decision": execution_decision,
                "reason_code": reason_code,
                "authority_source": authority_source,
                "current_revision": current_revision,
                "previous_revision": previous_revision,
                "estimated_avoided_ms": estimated_avoided_ms,
                "execution_status": execution_status,
                "execution_reason_code": result.get("execution_reason_code", "not-requested" if status == "ready" else "outcome-unconfirmed"),
                "duration_ms": result.get("duration_ms"),
                "duration_baseline": duration_baseline,
                "reuse_origin": result.get("reuse_origin"),
                "check_digest": decision["check_digest"] if decision else "",
                "origin_name": origin_presentation["name"] if origin_contract else "이전 Evidence 작업" if origin else "현재 계약",
                "origin_check_label": origin_presentation["evidence_labels"].get(key, label),
                "next_action": _next_action(reason_code, execution_status),
                "observer_status": observer_status,
                "input_count": len(input_keys),
                "visible_input_count": source_visible,
                "external_input_count": external_count,
                "changed_input_count": (
                    int(prediction["changed_input_count"])
                    if valid_prediction
                    else 0
                ),
                "changed_inputs": changed_inputs,
                "shadow_decision": (
                    str(prediction["decision"])
                    if valid_prediction
                    else "not-evaluable"
                ),
                "shadow_reason": (
                    str(prediction["reason"])
                    if valid_prediction
                    else "no-baseline"
                ),
                "shadow_limitations": (
                    list(prediction["limitations"]) if valid_prediction else []
                ),
                "shadow_outcome": (
                    str(evaluation["outcome"])
                    if valid_evaluation
                    else "not-evaluable"
                ),
            }
        )

    incremental = (
        dict(history_view["summaries"][batch["batch_id"]])
        if batch is not None else click_incremental.summary(verification)
    )
    plan_keys = set(decisions)
    incremental["current_source_count"] = sum(
        _source_status(evidence_sources.get(key)) == "passed" for key in plan_keys
    )
    visible_batches = history[-MAX_RECENT_BATCHES:]
    batch_summaries = {
        item["batch_id"]: {
            "incremental": history_view["summaries"][item["batch_id"]],
            "revalidation_savings": click_incremental.revalidation_savings(item),
        }
        for item in visible_batches
    }
    current_savings = (
        click_incremental.revalidation_savings(batch)
        if batch is not None
        else click_incremental.unmeasured_revalidation_savings(
            requested_source_count=(
                plan["total_source_count"] if plan is not None else None
            )
        )
    )
    observer_control = click_observer_control.projection(verification)
    runtime_mode = raw_state.get("runtime_mode")
    if runtime_mode not in {"evidence", "guarded"}:
        runtime_mode = "unknown"
    revision = verification.get("mutation_revision", 0)
    if not _is_count(revision):
        revision = 0
    raw_status = raw_state.get("status")
    status = (
        raw_status
        if isinstance(raw_status, str) and _STATUS.fullmatch(raw_status)
        else "unknown"
    )
    projection_generated_at = max(
        1, int(time.time()) if generated_at is None else generated_at
    )
    projection = {
        "version": PROJECTION_VERSION,
        "mode": PROJECTION_MODE,
        "readiness": click_reuse_readiness.projection(raw_state),
        "generated_at": projection_generated_at,
        "task": {
            "runtime_mode": runtime_mode,
            "status": status,
            "mutation_revision": revision,
            "observer_mode": observer_control["mode"],
            "observer_enabled": observer_control["enabled"],
            **{key: presentation[key] for key in ("name", "promises", "in_scope", "out_of_scope", "must_hold")},
            "contract_id": raw_state.get("contract_id") if re.fullmatch(r"ctr_[0-9a-f]{32}", str(raw_state.get("contract_id", ""))) else "",
            "approval_bound": bool(runtime_mode == "guarded" and status == "approved"
                                   and raw_state.get("approved_turn_id")
                                   and raw_state.get("approved_turn_id") != raw_state.get("staged_turn_id")),
        },
        "accounting": history_view["accounting"],
        "retained_impact": click_incremental.retained_impact(history),
        "controls": click_incremental.control_events(raw_state),
        "engine": engine_identity(),
        "summary": {
            "incremental": incremental,
            "revalidation_savings": current_savings,
            "shadow": _shadow_metrics(intelligence),
        },
        "sources": sources,
        "batches": visible_batches,
        "batch_summaries": batch_summaries,
        "setup": click_sharding_setup.dashboard_setup_projection(
            raw_state.get("auto_sharding_setup")
        ),
        "task_efficiency": {
            "kind": "click-task-efficiency-public",
            "version": 1,
            "generated_at": projection_generated_at,
            "measurement_status": "unmeasured",
            "measurement_reason": "host-task-and-usage-boundaries-unavailable",
            "presentations": [],
        },
        "history": {
            "totals": history_view["totals"],
            "retained_batch_count": len(history),
            "visible_batch_count": min(len(history), MAX_RECENT_BATCHES),
            "current_batch_id": batch["batch_id"] if batch is not None else None,
        },
        "map": {
            "nodes": nodes,
            "edges": edges,
            "total_input_count": total_inputs,
            "visible_input_count": visible_inputs,
            "truncated_input_count": max(0, total_inputs - visible_inputs),
        },
    }
    while projection["batches"] and len(_canonical_bytes(projection)) > MAX_BYTES:
        removed = projection["batches"].pop(0)
        projection["batch_summaries"].pop(removed["batch_id"], None)
    projection["history"]["visible_batch_count"] = len(projection["batches"])
    if len(_canonical_bytes(projection)) > MAX_BYTES:
        source_ids = {source["id"] for source in sources}
        projection["map"] = {
            "nodes": [node for node in nodes if node["id"] in source_ids],
            "edges": [],
            "total_input_count": total_inputs,
            "visible_input_count": 0,
            "truncated_input_count": total_inputs,
        }
        for source in projection["sources"]:
            source["visible_input_count"] = 0
            source["changed_inputs"] = []
    return projection


def projection_is_valid(value: Any) -> bool:
    version = value.get("version") if isinstance(value, dict) else None
    legacy = version == 4
    expected_fields = (
        _V4_FIELDS if version == 4 else _V5_FIELDS if version == 5
        else _V6_FIELDS if version == 6 else _V7_FIELDS if version == 7
        else _V9_FIELDS if version in {8, 9} else _FIELDS
    )
    if (
        not isinstance(value, dict)
        or set(value) != expected_fields
        or version not in {*LEGACY_PROJECTION_VERSIONS, PROJECTION_VERSION}
        or value.get("mode") != PROJECTION_MODE
        or not _is_count(value.get("generated_at"), minimum=1)
        or len(_canonical_bytes(value)) > MAX_BYTES
    ):
        return False
    if version == PROJECTION_VERSION and not click_reuse_readiness.is_valid(value.get("readiness")):
        return False
    task = value.get("task")
    engine = value.get("engine")
    if (
        not isinstance(engine, dict) or set(engine) != {"version", "hook_files_digest", "assurance"}
        or engine["assurance"] != "unsigned-files-at-snapshot"
        or engine["version"] is not None and (not isinstance(engine["version"], str) or re.fullmatch(r"\d+\.\d+\.\d+(?:\+codex\.\d{14})?", engine["version"]) is None)
        or engine["hook_files_digest"] is not None and not _is_digest(engine["hook_files_digest"])
    ):
        return False
    summary = value.get("summary")
    sources = value.get("sources")
    map_value = value.get("map")
    if (
        not isinstance(task, dict)
        or set(task) != _TASK_FIELDS
        or task.get("runtime_mode") not in {"evidence", "guarded", "unknown"}
        or not isinstance(task.get("status"), str)
        or _STATUS.fullmatch(task["status"]) is None
        or not _is_count(task.get("mutation_revision"))
        or task.get("observer_mode") not in click_observer_control.MODES
        or task.get("observer_enabled")
        != (task.get("observer_mode") != "off")
        or task.get("name") != click_incremental.safe_display_text(task.get("name"), "")
        or not isinstance(task.get("approval_bound"), bool)
        or not isinstance(task.get("contract_id"), str)
        or task["contract_id"] and re.fullmatch(r"ctr_[0-9a-f]{32}", task["contract_id"]) is None
        or any(not isinstance(task.get(key), list) or len(task[key]) > 8
               or any(item != click_incremental.safe_display_text(item, "") for item in task[key])
               for key in ("promises", "in_scope", "out_of_scope", "must_hold"))
        or not click_incremental.accounting_is_valid(value.get("accounting"))
        or not isinstance(value.get("controls"), list)
        or value["controls"] != click_incremental.control_events({"control_events": value["controls"]})
        or not isinstance(summary, dict)
        or set(summary) != (
            _LEGACY_SUMMARY_FIELDS if legacy else _SUMMARY_FIELDS
        )
        or not isinstance(sources, list)
        or len(sources) > MAX_SOURCES
        or not isinstance(map_value, dict)
        or set(map_value) != _MAP_FIELDS
    ):
        return False
    incremental = summary.get("incremental")
    revalidation = summary.get("revalidation_savings")
    shadow = summary.get("shadow")
    if (
        not isinstance(incremental, dict)
        or set(incremental) != _INCREMENTAL_FIELDS
        or any(
            incremental.get(field) is not None and not (
                click_incremental.is_duration(incremental[field]) if field in _TIME_FIELDS
                else _is_count(incremental[field])
            ) for field in _INCREMENTAL_FIELDS
        )
        or not isinstance(shadow, dict)
        or set(shadow) != _SHADOW_FIELDS
        or any(
            not _is_count(shadow.get(field))
            for field in _SHADOW_FIELDS
            if field != "tracing_slowdown_measured"
        )
        or shadow.get("tracing_slowdown_measured") is not False
        or shadow["confirmed_candidate_count"] > shadow["evaluated_source_count"]
        or shadow["contradiction_count"] > shadow["evaluated_source_count"]
        or (
            not legacy
            and not click_incremental.revalidation_savings_is_valid(
                revalidation
            )
        )
    ):
        return False
    reused_counts = [incremental[key] for key in ("authoritative_reuse_count", "exact_reuse_count", "dependency_reuse_count", "safe_change_reuse_count")]
    if all(item is not None for item in reused_counts) and reused_counts[0] != sum(reused_counts[1:]):
        return False
    batches, history = value.get("batches"), value.get("history")
    if (
        not isinstance(batches, list) or len(batches) > MAX_RECENT_BATCHES
        or any(not click_incremental.batch_is_valid(item) for item in batches)
        or len({item["batch_id"] for item in batches}) != len(batches)
        or not isinstance(history, dict)
        or set(history) != {"retained_batch_count", "visible_batch_count", "current_batch_id", "totals"}
        or not isinstance(history.get("totals"), dict)
        or set(history["totals"]) != {"finalized_batch_count", "executed_source_count", "authoritative_reuse_count", "not_run_source_count"}
        or any(not _is_count(item) for item in history["totals"].values())
        or not _is_count(history.get("retained_batch_count"))
        or history.get("visible_batch_count") != len(batches)
        or history["retained_batch_count"] < len(batches)
        or (history.get("current_batch_id") is not None and (
            not isinstance(history["current_batch_id"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", history["current_batch_id"])
        ))
    ):
        return False
    if not legacy:
        batch_summaries = value.get("batch_summaries")
        batches_by_id = {item["batch_id"]: item for item in batches}
        if (
            not isinstance(batch_summaries, dict)
            or set(batch_summaries) != set(batches_by_id)
        ):
            return False
        for batch_id, batch in batches_by_id.items():
            projected = batch_summaries.get(batch_id)
            if (
                not isinstance(projected, dict)
                or set(projected) != _BATCH_SUMMARY_FIELDS
                or projected.get("incremental")
                != click_incremental.batch_summary(batch)
                or projected.get("revalidation_savings")
                != click_incremental.revalidation_savings(batch)
            ):
                return False
        current_id = history.get("current_batch_id")
        if current_id in batch_summaries:
            selected = batch_summaries[current_id]
            if revalidation != selected["revalidation_savings"]:
                return False
            for field in click_incremental.SUMMARY_FIELDS:
                if incremental[field] != selected["incremental"][field]:
                    return False

    if version in {7, 8, 9, PROJECTION_VERSION} and not click_incremental.retained_impact_is_valid(value.get("retained_impact")):
        return False
    if version in {7, 8, 9, PROJECTION_VERSION} and (
        value["retained_impact"]["completed_request_count"] > history["retained_batch_count"]
        or value["retained_impact"]["reused_group_request_count"] > value["accounting"]["reuse_numerator"]
    ):
        return False
    if version in {6, 7, 8, 9, PROJECTION_VERSION}:
        setup = value.get("setup")
        if (
            not isinstance(setup, dict)
            or set(setup)
            != (_SETUP_FIELDS if version >= 9 else _LEGACY_SETUP_FIELDS)
            or setup.get("version") != click_sharding_setup.VERSION
            or not isinstance(setup.get("status"), str)
            or re.fullmatch(r"[a-z0-9-]{1,64}", setup["status"]) is None
            or not isinstance(setup.get("sharding_ready"), bool)
            or not isinstance(setup.get("reuse_ready"), bool)
            or not isinstance(setup.get("reuse_status"), str)
            or re.fullmatch(r"[a-z0-9-]{1,64}", setup["reuse_status"]) is None
            or not isinstance(setup.get("comparison_scope"), str)
            or len(setup["comparison_scope"]) > 96
            or any(
                setup.get(field) is not None
                and not click_incremental.is_duration(setup[field])
                for field in (
                    "initial_setup_ms",
                    "observation_ms",
                    "click_processing_ms",
                    "bootstrap_parent_ms",
                    "bootstrap_shards_ms",
                )
            )
            or (
                setup.get("comparison_net_ms") is not None
                and (
                    not isinstance(setup["comparison_net_ms"], (int, float))
                    or isinstance(setup["comparison_net_ms"], bool)
                    or not math.isfinite(setup["comparison_net_ms"])
                )
            )
        ):
            return False

    if version in {8, 9, PROJECTION_VERSION}:
        task_efficiency = value.get("task_efficiency")
        if (
            not isinstance(task_efficiency, dict)
            or set(task_efficiency) != _TASK_EFFICIENCY_FIELDS
            or task_efficiency.get("kind") != "click-task-efficiency-public"
            or task_efficiency.get("version") != 1
            or task_efficiency.get("generated_at") != value.get("generated_at")
            or task_efficiency.get("measurement_status") != "unmeasured"
            or task_efficiency.get("measurement_reason")
            != "host-task-and-usage-boundaries-unavailable"
            or task_efficiency.get("presentations") != []
        ):
            return False

    source_ids: set[str] = set()
    for source in sources:
        if (
            not isinstance(source, dict)
            or set(source) != _SOURCE_FIELDS
            or not isinstance(source.get("id"), str)
            or re.fullmatch(r"source:[0-9a-f]{16}", source["id"]) is None
            or source["id"] in source_ids
            or not isinstance(source.get("label"), str)
            or not source["label"]
            or source["label"] != click_incremental.safe_label(source["label"], "")
            or source.get("check_digest") != "" and not _is_digest(source.get("check_digest"))
            or any(source.get(key) != click_incremental.safe_display_text(source.get(key), "")
                   for key in ("origin_name", "origin_check_label", "next_action"))
            or source.get("status") not in _SOURCE_STATUSES
            or source.get("execution_decision") not in _EXECUTION_DECISIONS
            or source.get("reason_code")
            not in {*click_incremental.REASON_CODES, ""}
            or source.get("authority_source")
            not in click_incremental.AUTHORITY_SOURCES
            or not _is_count(source.get("current_revision"))
            or not _is_count(source.get("previous_revision"), minimum=-1)
            or (source.get("estimated_avoided_ms") is not None and not click_incremental.is_duration(source["estimated_avoided_ms"]))
            or source.get("execution_status") not in click_incremental.SOURCE_STATUSES
            or source.get("execution_reason_code") not in click_incremental.EXECUTION_REASONS
            or (source.get("duration_ms") is not None and not click_incremental.is_duration(source["duration_ms"]))
            or (source.get("duration_baseline") is not None and not click_incremental.baseline_is_valid(source["duration_baseline"]))
            or (
                source.get("reuse_origin") is not None
                and not click_incremental.reuse_origin_is_valid(
                    source["reuse_origin"]
                )
            )
            or source.get("observer_status")
            not in click_dependency_cache.SHADOW_OBSERVER_STATUSES
            or any(
                not _is_count(source.get(field))
                for field in (
                    "input_count",
                    "visible_input_count",
                    "external_input_count",
                    "changed_input_count",
                )
            )
            or source["visible_input_count"] > source["input_count"]
            or not isinstance(source.get("changed_inputs"), list)
            or source["changed_inputs"] != sorted(set(source["changed_inputs"]))
            or len(source["changed_inputs"]) > MAX_CHANGED_INPUTS
            or len(source["changed_inputs"]) > source["changed_input_count"]
            or any(
                not _safe_relative_path(path, directory=str(path).endswith("/"))
                for path in source["changed_inputs"]
            )
            or source.get("shadow_decision")
            not in click_shadow_intelligence.DECISIONS
            or source.get("shadow_reason")
            not in click_shadow_intelligence.PREDICTION_REASONS
            or not isinstance(source.get("shadow_limitations"), list)
            or source["shadow_limitations"]
            != sorted(set(source["shadow_limitations"]))
            or any(
                item not in click_shadow_intelligence.LIMITATIONS
                for item in source["shadow_limitations"]
            )
            or source.get("shadow_outcome")
            not in click_shadow_intelligence.OUTCOMES
        ):
            return False
        if source["execution_decision"] == "not-planned":
            if source["reason_code"] or source["authority_source"] != "none":
                return False
        elif not source["reason_code"]:
            return False
        source_ids.add(source["id"])

    nodes = map_value.get("nodes")
    edges = map_value.get("edges")
    if (
        not isinstance(nodes, list)
        or len(nodes) > MAX_INPUTS + MAX_SOURCES
        or not isinstance(edges, list)
        or len(edges) > MAX_INPUTS
        or any(
            not _is_count(map_value.get(field))
            for field in (
                "total_input_count",
                "visible_input_count",
                "truncated_input_count",
            )
        )
        or map_value["visible_input_count"] > MAX_INPUTS
        or map_value["visible_input_count"] + map_value["truncated_input_count"]
        != map_value["total_input_count"]
    ):
        return False
    node_ids: set[str] = set()
    visible_source_nodes: set[str] = set()
    visible_input_nodes = 0
    for node in nodes:
        if not isinstance(node, dict) or set(node) != _NODE_FIELDS:
            return False
        node_id = node.get("id")
        node_type = node.get("type")
        label = node.get("label")
        if (
            not isinstance(node_id, str)
            or node_id in node_ids
            or node_type not in {"source", "input"}
            or not isinstance(label, str)
            or not label
        ):
            return False
        if node_type == "source":
            if (
                node_id not in source_ids
                or node.get("kind") != "argv"
                or node.get("status") not in _SOURCE_STATUSES
            ):
                return False
            visible_source_nodes.add(node_id)
        else:
            if (
                re.fullmatch(r"input:[0-9a-f]{24}", node_id) is None
                or node.get("kind")
                not in click_dependency_cache.SHADOW_OBSERVER_INPUT_KINDS
                or not _safe_relative_path(
                    label, directory=node.get("kind") == "directory"
                )
                or node.get("status") not in _INPUT_STATUSES
            ):
                return False
            visible_input_nodes += 1
        node_ids.add(node_id)
    if visible_source_nodes != source_ids:
        return False
    for edge in edges:
        if (
            not isinstance(edge, dict)
            or set(edge) != _EDGE_FIELDS
            or edge.get("source") not in source_ids
            or edge.get("target") not in node_ids
            or not str(edge.get("target", "")).startswith("input:")
            or not isinstance(edge.get("operations"), list)
            or edge["operations"] != sorted(set(edge["operations"]))
            or not edge["operations"]
            or any(
                operation not in click_dependency_cache.SHADOW_OBSERVER_OPERATIONS
                for operation in edge["operations"]
            )
        ):
            return False
    return bool(
        visible_input_nodes == map_value["visible_input_count"] == len(edges)
    )


__all__ = ["dashboard_projection", "projection_is_valid"]
