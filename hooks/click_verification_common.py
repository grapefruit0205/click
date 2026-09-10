#!/usr/bin/env python3
"""Shared verification bindings and state primitives. No preparation or execution dependencies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import time
from typing import Any

if __package__:
    from . import click_import_bootstrap
else:  # Installed launchers execute hooks directly.
    import click_import_bootstrap

(
    click_capability,
    click_change_policy,
    click_claims,
    click_contract_state,
    click_dependency_cache,
    click_diagnostics,
    click_evidence,
    click_evidence_shards,
    click_host_coverage,
    click_incremental,
    click_inspection,
    click_mutation,
    click_observation,
    click_observer_control,
    click_observer_runtime,
    click_process,
    click_runtime_state,
    click_shadow_intelligence,
    click_state,
    click_verification_policy,
    click_verification_bindings,
    click_verification_inputs,
    click_verification_plan,
    click_verification_reuse,
    click_observer_common,
) = click_import_bootstrap.load_siblings(
    __package__,
    "click_capability",
    "click_change_policy",
    "click_claims",
    "click_contract_state",
    "click_dependency_cache",
    "click_diagnostics",
    "click_evidence",
    "click_evidence_shards",
    "click_host_coverage",
    "click_incremental",
    "click_inspection",
    "click_mutation",
    "click_observation",
    "click_observer_control",
    "click_observer_runtime",
    "click_process",
    "click_runtime_state",
    "click_shadow_intelligence",
    "click_state",
    "click_verification_policy",
    "click_verification_bindings",
    "click_verification_inputs",
    "click_verification_plan",
    "click_verification_reuse",
    "click_observer_common",
)
CONTRACT_STATE_SCHEMA_VERSION = 2
RUNNING_TTL_SECONDS = 60 * 60
VERIFY_RUNNING_TTL_SECONDS = RUNNING_TTL_SECONDS
PROTOCOL_VERSION = click_verification_plan.PROTOCOL_VERSION
BATCH_FIELDS = click_verification_plan.BATCH_FIELDS
CHECK_FIELDS = click_verification_plan.CHECK_FIELDS
VERIFICATION_CLASSES = click_verification_plan.VERIFICATION_CLASSES
PYTHON_VERIFICATION_MODULES = click_verification_plan.PYTHON_VERIFICATION_MODULES
PYTHON_VERIFICATION_EXECUTABLES = click_verification_plan.PYTHON_VERIFICATION_EXECUTABLES
VERSIONED_PYTHON_EXECUTABLE = click_verification_plan.VERSIONED_PYTHON_EXECUTABLE
DEEP_VERIFICATION_EXECUTABLES = click_verification_plan.DEEP_VERIFICATION_EXECUTABLES
DEEP_VERIFICATION_MARKERS = click_verification_plan.DEEP_VERIFICATION_MARKERS
VERIFICATION_EXECUTABLES = click_verification_plan.VERIFICATION_EXECUTABLES
VERIFICATION_NAME_MARKERS = click_verification_plan.VERIFICATION_NAME_MARKERS
TEST_TARGET_SUFFIXES = click_verification_plan.TEST_TARGET_SUFFIXES
TEST_FILTER_OPTIONS = click_verification_plan.TEST_FILTER_OPTIONS
TEST_OPTIONS_WITH_VALUES = click_verification_plan.TEST_OPTIONS_WITH_VALUES
NEW_SOURCE_PATH_SEGMENTS = click_verification_plan.NEW_SOURCE_PATH_SEGMENTS
VERIFICATION_BATCH_FIELDS = click_verification_plan.VERIFICATION_BATCH_FIELDS
VERIFICATION_CHECK_FIELDS = click_verification_plan.VERIFICATION_CHECK_FIELDS
VERIFICATION_PROTOCOL_VERSION = click_verification_plan.VERIFICATION_PROTOCOL_VERSION
EVIDENCE_ID_PATTERN = click_verification_plan.EVIDENCE_ID_PATTERN
FAILURE_COLLECTION_VERSION = click_verification_plan.FAILURE_COLLECTION_VERSION
FAILURE_COLLECTION_MAX_EXTRA_SOURCES = click_verification_plan.FAILURE_COLLECTION_MAX_EXTRA_SOURCES
FAILURE_COLLECTION_MAX_EXTRA_FAILURES = click_verification_plan.FAILURE_COLLECTION_MAX_EXTRA_FAILURES
FAILURE_COLLECTION_MAX_START_WINDOW_MS = click_verification_plan.FAILURE_COLLECTION_MAX_START_WINDOW_MS
FAILURE_COLLECTION_STATE_FIELD = click_verification_plan.FAILURE_COLLECTION_STATE_FIELD
FAILURE_COLLECTION_RESULT_STATUSES = click_verification_plan.FAILURE_COLLECTION_RESULT_STATUSES
_fresh_mutation_boundary = click_mutation.fresh_boundary
_decode_capability_request = click_capability.decode_request
_validate_argv = click_capability.validate_argv
_capability_digest = click_capability.digest
_command_parts = click_capability.command_parts
_shell_segments = click_capability.shell_segments
_evidence_key = click_evidence.evidence_key
_evidence_is_current = click_evidence.is_current
_evidence_keys_for_kind = click_evidence.keys_for_kind
_is_read_only_tokens = click_inspection.is_read_only_tokens
_is_broad_exploration_tokens = click_inspection.is_broad_exploration_tokens
_is_path_qualified_executable = click_inspection.is_path_qualified_executable
_resolve_read_only_executable = click_inspection.resolve_read_only_executable
_sanitized_git_environment = click_inspection.sanitized_git_environment
_default_failure_collection = click_verification_plan.default_failure_collection
_validate_failure_collection = click_verification_plan.validate_failure_collection
_failure_collection_result_is_valid = click_verification_plan.failure_collection_result_is_valid


def _fresh_verification_state(contract: dict[str, Any]) -> dict[str, Any]:
    scale = str(contract["verification"]["scale"])
    legacy_unit_limit = click_verification_policy.approved_unit_limit(scale)
    assert legacy_unit_limit is not None
    return {
        "scale": scale,
        # Compatibility state field only. Runtime authority and advice do not
        # depend on this legacy plugin-authored number.
        "unit_limit": legacy_unit_limit,
        "status": "ready",
        "mutation_revision": 0,
        "verified_revision": -1,
        "failed_revision": -1,
        "attempts": 0,
        "unchanged_failure_retries": 0,
        "last_units": 0,
        "last_exit_code": None,
        "last_batch_digest": "",
        "locked_batch_digest": "",
        "runner_token_digest": "",
        "runner_claimed_at": 0,
        "running_evidence_keys": [],
        "running_environment_digests": {},
        "running_environment_binding": [],
        "running_environment_binding_digest": "",
        "running_executable_digests": {},
        "running_executable_component_digests": {},
        "running_host_coverage": {},
        "running_host_coverage_digest": "",
        "running_input_policy_receipts": {},
        "running_input_policy_binding": "",
        "workspace_changed": False,
        "mutation_boundary": _fresh_mutation_boundary(),
        "started_at": 0,
        click_observer_control.CONTROL_FIELD: (
            click_observer_control.fresh_state()
        ),
        click_observer_common.SHADOW_STATE_FIELD: (
            click_observer_common.fresh_state()
        ),
        click_shadow_intelligence.SHADOW_INTELLIGENCE_FIELD: (
            click_shadow_intelligence.fresh_state()
        ),
    }


_validate_verification_batch = click_verification_plan.validate_verification_batch
_verification_groups = click_verification_plan.verification_groups
_verification_group_policy = click_verification_plan.verification_group_policy
_verification_group_digest = click_verification_plan.verification_group_digest
_verification_command_digest = click_verification_plan.verification_command_digest
_verification_command_plans = click_verification_plan.verification_command_plans
_verification_group_units = click_verification_plan.verification_group_units
_file_content_digest = click_verification_bindings.hash_file_content
_verification_environment = click_verification_bindings.verification_environment
_observer_environment = click_verification_bindings.observer_environment
_verification_environment_key = click_verification_bindings.verification_environment_key
_verification_environment_hmac = click_verification_bindings.verification_environment_hmac
_verification_environment_binding = click_verification_bindings.verification_environment_binding
_verification_environment_binding_digest = click_verification_bindings.verification_environment_binding_digest
_verification_environment_binding_is_authentic = click_verification_bindings.verification_environment_binding_is_authentic
_verification_host_coverage_binding_digest = click_verification_bindings.verification_host_coverage_binding_digest
_verification_host_coverage_binding_is_authentic = click_verification_bindings.verification_host_coverage_binding_is_authentic
_verification_environment_from_binding = click_verification_bindings.verification_environment_from_binding
_executable_search_path = click_verification_bindings.executable_search_path
_verification_executable_records = click_verification_bindings.verification_executable_records
_verification_executable_payload = click_verification_bindings.verification_executable_payload
_verification_environment_digest_from_records = click_verification_bindings.verification_environment_digest_from_records
_verification_environment_digest = click_verification_bindings.verification_environment_digest
_verification_receipt_matches = click_verification_reuse.verification_receipt_matches
_dependency_declarations = click_verification_reuse.dependency_declarations
_dependency_observations = click_verification_reuse.dependency_observations
_binding_path_digest = click_verification_reuse.binding_path_digest
_authoritative_shard_digest = click_verification_reuse.authoritative_shard_digest
_authoritative_current_bindings = click_verification_reuse.authoritative_current_bindings
_dependency_receipt_is_valid = click_verification_reuse.dependency_receipt_is_valid
_dependency_receipt_matches = click_verification_reuse.dependency_receipt_matches
_clear_dependency_receipt = click_verification_reuse.clear_dependency_receipt
_store_dependency_receipt = click_verification_reuse.store_dependency_receipt
_promote_dependency_receipt = click_verification_reuse.promote_dependency_receipt
_clear_safe_change_receipt = click_verification_reuse.clear_safe_change_receipt
_store_safe_change_receipt = click_verification_reuse.store_safe_change_receipt
_safe_change_receipt_matches = click_verification_reuse.safe_change_receipt_matches
_promote_safe_change_receipt = click_verification_reuse.promote_safe_change_receipt
_reuse_binding_reason = click_verification_reuse.reuse_binding_reason
_successor_binding_reason = click_verification_reuse.successor_binding_reason
_requalify_successor_baseline = click_verification_reuse.requalify_successor_baseline
_mark_successor_reuse = click_verification_reuse.mark_successor_reuse
_observation_nonreuse_reason = click_verification_reuse.observation_nonreuse_reason
_default_incremental_reason = click_verification_reuse.default_incremental_reason
_canonical_incremental_plan = click_verification_reuse.canonical_incremental_plan
_contains_deep_verification_marker = click_verification_plan.contains_deep_verification_marker
_arguments_have_filter = click_verification_plan.arguments_have_filter
_verification_targets = click_verification_plan.verification_targets
_scope_with_kind_floor = click_verification_plan.scope_with_kind_floor
_minimum_test_runner_class = click_verification_plan.minimum_test_runner_class
_minimum_verification_class = click_verification_plan.minimum_verification_class
_is_recognized_verification_tokens = click_verification_plan.is_recognized_verification_tokens
_is_recognized_verification_command = click_verification_plan.is_recognized_verification_command
_git_capture = click_verification_bindings.git_capture
_hash_workspace_path = click_verification_bindings.hash_workspace_path
_git_workspace_snapshot = click_verification_bindings.git_workspace_snapshot


def _new_untracked_is_suspicious(relative: str) -> bool:
    parts = [part.lower() for part in Path(relative).parts if part not in {"", "."}]
    if not parts:
        return False
    if parts[0] in NEW_SOURCE_PATH_SEGMENTS:
        return True
    if any(
        part in {"config", "configs", "migration", "migrations", "src"}
        for part in parts
    ):
        return True
    for index, part in enumerate(parts):
        if part not in {"app", "lib"}:
            continue
        if index >= 2 and parts[index - 2] in {
            "apps",
            "modules",
            "packages",
            "services",
        }:
            return True
    if any(
        parts[index : index + 2] == ["db", "migrate"]
        for index in range(max(0, len(parts) - 1))
    ):
        return True
    if len(parts) == 1:
        name = parts[0]
        suffix = Path(name).suffix.lower()
        if suffix in {
            ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js",
            ".jsx", ".php", ".py", ".rb", ".rs", ".ts", ".tsx",
        }:
            return True
        if name in {
            "cargo.toml", "compose.yaml", "compose.yml", "docker-compose.yaml",
            "docker-compose.yml", "dockerfile", "go.mod", "package-lock.json",
            "package.json", "pnpm-lock.yaml", "pyproject.toml", "requirements.txt",
            "yarn.lock",
        }:
            return True
    return False


fresh_state = _fresh_verification_state
validate_batch = _validate_verification_batch
verification_groups = _verification_groups
group_digest = _verification_group_digest
group_units = _verification_group_units
file_content_digest = _file_content_digest
environment = _verification_environment
environment_key = _verification_environment_key
environment_binding = _verification_environment_binding
environment_binding_digest = _verification_environment_binding_digest
environment_binding_is_authentic = _verification_environment_binding_is_authentic
host_coverage_binding_digest = _verification_host_coverage_binding_digest
host_coverage_binding_is_authentic = _verification_host_coverage_binding_is_authentic
environment_from_binding = _verification_environment_from_binding
executable_records = _verification_executable_records
executable_payload = _verification_executable_payload
environment_digest_from_records = _verification_environment_digest_from_records
environment_digest = _verification_environment_digest
receipt_matches = _verification_receipt_matches
dependency_declarations = _dependency_declarations
dependency_observations = _dependency_observations
dependency_receipt_is_valid = _dependency_receipt_is_valid
dependency_receipt_matches = _dependency_receipt_matches
clear_dependency_receipt = _clear_dependency_receipt
store_dependency_receipt = _store_dependency_receipt
promote_dependency_receipt = _promote_dependency_receipt
clear_safe_change_receipt = _clear_safe_change_receipt
store_safe_change_receipt = _store_safe_change_receipt
safe_change_receipt_matches = _safe_change_receipt_matches
promote_safe_change_receipt = _promote_safe_change_receipt
minimum_class = _minimum_verification_class
is_recognized_tokens = _is_recognized_verification_tokens
is_recognized_command = _is_recognized_verification_command
git_capture = _git_capture
hash_workspace_path = _hash_workspace_path
git_workspace_snapshot = _git_workspace_snapshot
new_untracked_is_suspicious = _new_untracked_is_suspicious
_read_contract_state = click_contract_state.read_contract_state
_save_contract_state = click_contract_state.save_contract_state


def _evidence_sources(state: dict[str, Any]) -> dict[str, Any] | None:
    return click_evidence.sources_from_state(
        state,
        expected_contract_schema_version=CONTRACT_STATE_SCHEMA_VERSION,
    )


_fresh_external_evidence_state = click_evidence.fresh_external_state
_fresh_mutation_state = click_mutation.fresh_state
_mutation_is_running = click_mutation.is_running
_fresh_observation_state = click_observation.fresh_state
_observation_is_running = click_observation.is_running
_unclaimed_reservation_is_fresh = click_observation.unclaimed_reservation_is_fresh
_write_json = click_state.write_json
_state_lock = click_state.state_lock
_decode_encoded_request = click_capability.decode_encoded_request
_execute_argv_commands = click_inspection.execute_argv_commands
_git_metadata_present = click_inspection.git_metadata_present


def _managed_contract_path(path: Path) -> bool:
    return click_state.managed_state_path(path, ("session-contract-",))




def _tool_working_directory(
    event: dict[str, Any], batch: dict[str, Any] | None = None
) -> Path:
    event_cwd = Path(str(event.get("cwd", "")))
    requested = batch.get("workdir") if isinstance(batch, dict) else None
    tool_input = event.get("tool_input")
    if not isinstance(requested, str) or not requested:
        requested = tool_input.get("workdir") if isinstance(tool_input, dict) else None
    if not isinstance(requested, str) or not requested:
        return event_cwd.resolve()
    workdir = Path(requested)
    if not workdir.is_absolute():
        workdir = event_cwd / workdir
    return workdir.resolve()




def _verification_claim_binding(state: Any) -> dict[str, Any] | None:
    """Capture only the live authority facts needed by a claimed invocation."""
    if not isinstance(state, dict) or not click_runtime_state.view(state).execution_authorized:
        return None
    verification = state.get("verification")
    contract_digest = state.get("contract_digest")
    if (
        not isinstance(verification, dict)
        or not click_evidence.revision_is_valid(verification.get("mutation_revision"))
        or not isinstance(contract_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", contract_digest) is None
    ):
        return None
    identity_fields = (
        ("contract_id", "staged_turn_id", "approved_turn_id")
        if state["status"] == "approved" else ("evidence_session_id",)
    )
    return {
        "status": state["status"],
        "contract_digest": contract_digest,
        "mutation_revision": verification["mutation_revision"],
        **{field: state.get(field) for field in identity_fields},
    }


