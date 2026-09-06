#!/usr/bin/env python3
"""Verification admission, fingerprints, receipts, and runner lifecycle for Click.

This layer binds approved argv evidence to an exact check group, workspace,
environment, executable, host coverage identity, and one-use runner result. It
may depend on lower runtime domains but never imports the gate or host router.
"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any

if __package__:
    from . import (
        click_capability,
        click_change_policy,
        click_claims,
        click_contract_state,
        click_dependency_cache,
        click_dependency_trace,
        click_evidence,
        click_evidence_shards,
        click_host_coverage,
        click_incremental,
        click_inspection,
        click_mutation,
        click_observation,
        click_observer_control,
        click_process,
        click_runtime_state,
        click_shadow_intelligence,
        click_state,
        click_verification_meter,
        click_verification_policy,
    )
else:  # Executed directly from the bundled hooks directory.
    import click_capability
    import click_change_policy
    import click_claims
    import click_contract_state
    import click_dependency_cache
    import click_dependency_trace
    import click_evidence
    import click_evidence_shards
    import click_host_coverage
    import click_incremental
    import click_inspection
    import click_mutation
    import click_observation
    import click_observer_control
    import click_process
    import click_runtime_state
    import click_shadow_intelligence
    import click_state
    import click_verification_meter
    import click_verification_policy


PROTOCOL_VERSION = 2
CONTRACT_STATE_SCHEMA_VERSION = 2
BATCH_FIELDS = {"version", "checks", "workdir"}
CHECK_FIELDS = {"evidence_id", "argv", "class"}
VERIFICATION_CLASSES = click_verification_meter.VERIFICATION_CLASSES
RUNNING_TTL_SECONDS = 60 * 60
VERIFY_RUNNING_TTL_SECONDS = RUNNING_TTL_SECONDS
PYTHON_VERIFICATION_MODULES = {"coverage", "pytest", "unittest"}
DEEP_VERIFICATION_EXECUTABLES = {
    "bandit", "cargo-audit", "cypress", "k6", "locust", "nox", "playwright",
    "semgrep", "snyk", "tox", "trivy",
}
DEEP_VERIFICATION_MARKERS = {
    "audit", "bench", "coverage", "e2e", "end-to-end", "end_to_end",
    "integration", "load-test", "load_test", "security",
}
VERIFICATION_EXECUTABLES = {
    "bandit", "bats", "cargo-audit", "cypress", "jest", "k6", "locust",
    "nox", "playwright", "phpunit", "pytest", "rspec", "semgrep", "snyk",
    "tox", "trivy", "vitest",
}
VERIFICATION_NAME_MARKERS = (
    "audit", "bench", "coverage", "e2e", "integration-test", "integration_test",
    "security", "spec", "test", "validate", "verification", "verify",
)
TEST_TARGET_SUFFIXES = {
    ".go", ".js", ".jsx", ".php", ".py", ".rb", ".rs", ".ts", ".tsx",
}
TEST_FILTER_OPTIONS = {
    "-k", "-m", "-run", "-t", "--filter", "--test-name-pattern",
    "--tests-regex",
}
TEST_OPTIONS_WITH_VALUES = TEST_FILTER_OPTIONS | {
    "-p", "-r", "-s", "--basetemp", "--confcutdir", "--cov", "--cov-report",
    "--deselect", "--ignore", "--junitxml", "--maxfail", "--package",
    "--project", "--rootdir", "--test",
}
NEW_SOURCE_PATH_SEGMENTS = {
    "app", "config", "configs", "lib", "migration", "migrations", "src",
}


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
VERIFICATION_BATCH_FIELDS = BATCH_FIELDS
VERIFICATION_CHECK_FIELDS = CHECK_FIELDS
VERIFICATION_PROTOCOL_VERSION = PROTOCOL_VERSION
EVIDENCE_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")


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
        "running_host_coverage": {},
        "running_host_coverage_digest": "",
        "workspace_changed": False,
        "mutation_boundary": _fresh_mutation_boundary(),
        "started_at": 0,
        click_observer_control.CONTROL_FIELD: (
            click_observer_control.fresh_state()
        ),
        click_dependency_trace.SHADOW_STATE_FIELD: (
            click_dependency_trace.fresh_state()
        ),
        click_shadow_intelligence.SHADOW_INTELLIGENCE_FIELD: (
            click_shadow_intelligence.fresh_state()
        ),
    }


def _validate_verification_batch(
    raw: str,
    scale: str,
    evidence_sources: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, int, str]:
    value, error = _decode_capability_request(
        raw,
        "Verification batch",
        version=VERIFICATION_PROTOCOL_VERSION,
    )
    if error:
        return None, 0, error
    assert value is not None
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
        unknown_check = sorted(set(check) - VERIFICATION_CHECK_FIELDS)
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
            source = evidence_sources.get(_evidence_key(evidence_id))
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
        argv, argv_error = _validate_argv(check.get("argv"), f"Verification check {index}")
        if argv_error:
            return None, 0, argv_error
        assert argv is not None
        read_only = _is_read_only_tokens(list(argv))
        minimum_class = (
            "broad" if read_only and _is_broad_exploration_tokens(argv) else "targeted"
        ) if read_only else _minimum_verification_class(argv)
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
        if isinstance(evidence_id, str):
            normalized_check["evidence_id"] = evidence_id
        normalized.append(normalized_check)
    normalized_batch = {
        "version": VERIFICATION_PROTOCOL_VERSION,
        "checks": normalized,
    }
    if isinstance(workdir, str):
        normalized_batch["workdir"] = workdir
    return normalized_batch, units, ""


def _verification_groups(
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
        source_key = _evidence_key(evidence_id)
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


def _verification_group_digest(checks: list[dict[str, Any]]) -> str:
    # Receipt identity is the executable request, not Click's compatibility
    # class or legacy unit heuristic.
    payload = [{"argv": check["argv"]} for check in checks]
    return _capability_digest({"checks": payload})


def _verification_group_units(checks: list[dict[str, Any]]) -> int:
    units = click_verification_meter.total_units(check["class"] for check in checks)
    assert units is not None
    return units


def _file_content_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError:
        return ""
    return hasher.hexdigest()


def _verification_environment(*, cwd: Path) -> dict[str, str]:
    # Shell launchers add bookkeeping variables that do not change check
    # semantics and are not stable across the Hook process and its rewritten
    # runner. Keep user/project variables fingerprinted, but canonicalize these
    # launcher-owned values so an unchanged receipt remains portable.
    volatile = {
        "_",
        "__CF_USER_TEXT_ENCODING",
        "CMDCMDLINE",
        "CLICK_CONFIG_HOME",
        "COMMAND_MODE",
        "LC_CTYPE",
        "OLDPWD",
        "PROMPT",
        "PROMPT_COMMAND",
        "PS1",
        "PS2",
        "PLUGIN_DATA",
        "PLUGIN_ROOT",
        "SHLVL",
    }
    environment = {
        str(key): str(value)
        for key, value in os.environ.items()
        if str(key).upper() not in volatile and not str(key).startswith("=")
    }
    environment["PWD"] = str(cwd.resolve())
    return environment


def _verification_environment_key(key: str) -> str:
    return key.upper() if os.name == "nt" else key


def _verification_environment_hmac(
    runner_token: str, domain: str, value: str
) -> str:
    secret = hashlib.sha256(runner_token.encode()).digest()
    message = f"click-verification-{domain}\0{value}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _verification_environment_binding(
    environment: dict[str, str], runner_token: str
) -> list[dict[str, str]]:
    records = []
    for key, value in environment.items():
        normalized_key = _verification_environment_key(str(key))
        records.append(
            {
                "key_digest": _verification_environment_hmac(
                    runner_token, "key", normalized_key
                ),
                "value_digest": _verification_environment_hmac(
                    runner_token, "value", f"{normalized_key}\0{value}"
                ),
            }
        )
    return sorted(records, key=lambda item: item["key_digest"])


def _verification_environment_binding_digest(
    binding: Any, runner_token: str
) -> str:
    try:
        canonical = json.dumps(
            binding, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError):
        return ""
    return _verification_environment_hmac(runner_token, "binding", canonical)


def _verification_environment_binding_is_authentic(
    binding: Any, digest: Any, runner_token: str
) -> bool:
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return False
    expected = _verification_environment_binding_digest(binding, runner_token)
    return bool(expected and secrets.compare_digest(digest, expected))


def _verification_host_coverage_binding_digest(
    coverage: Any, runner_token: str
) -> str:
    if not click_host_coverage.receipt_is_current(coverage):
        return ""
    canonical = json.dumps(coverage, sort_keys=True, separators=(",", ":"))
    return _verification_environment_hmac(
        runner_token, "host-coverage", canonical
    )


def _verification_host_coverage_binding_is_authentic(
    coverage: Any, digest: Any, runner_token: str
) -> bool:
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return False
    expected = _verification_host_coverage_binding_digest(
        coverage, runner_token
    )
    return bool(expected and secrets.compare_digest(digest, expected))


def _verification_environment_from_binding(
    binding: Any,
    runner_token: str,
    current_environment: dict[str, str],
) -> tuple[dict[str, str] | None, bool, str]:
    if not isinstance(binding, list) or not binding or len(binding) > 4096:
        return None, False, "Click verification runner environment binding was malformed."
    expected: dict[str, str] = {}
    for record in binding:
        if not isinstance(record, dict) or set(record) != {
            "key_digest",
            "value_digest",
        }:
            return None, False, "Click verification runner environment binding was malformed."
        key_digest = record.get("key_digest")
        value_digest = record.get("value_digest")
        if (
            not isinstance(key_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", key_digest)
            or not isinstance(value_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", value_digest)
            or key_digest in expected
        ):
            return None, False, "Click verification runner environment binding was malformed."
        expected[key_digest] = value_digest

    projected: dict[str, str] = {}
    matched: set[str] = set()
    drifted = False
    for key, value in current_environment.items():
        normalized_key = _verification_environment_key(str(key))
        key_digest = _verification_environment_hmac(
            runner_token, "key", normalized_key
        )
        expected_value = expected.get(key_digest)
        if expected_value is None:
            continue
        current_value = _verification_environment_hmac(
            runner_token, "value", f"{normalized_key}\0{value}"
        )
        if not secrets.compare_digest(expected_value, current_value):
            drifted = True
        projected[str(key)] = str(value)
        matched.add(key_digest)
    if matched != set(expected):
        drifted = True
    return projected, drifted, ""


def _executable_search_path(environment: dict[str, str], *, cwd: Path) -> str:
    """Resolve relative PATH entries as the verification child will from its cwd."""
    entries: list[str] = []
    for raw_entry in environment.get("PATH", os.defpath).split(os.pathsep):
        entry = Path(raw_entry) if raw_entry else cwd
        if not entry.is_absolute():
            entry = cwd / entry
        entries.append(str(entry.resolve()))
    return os.pathsep.join(entries)


def _verification_executable_records(
    checks: list[dict[str, Any]],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
    file_content_digest: Callable[[Path], str] | None = None,
) -> list[dict[str, Any]] | None:
    effective_environment = environment or _verification_environment(cwd=cwd)
    digest_file = file_content_digest or _file_content_digest
    search_path = _executable_search_path(effective_environment, cwd=cwd)
    executables: list[dict[str, Any]] = []
    for check in checks:
        argv = check.get("argv")
        executable = str(argv[0]) if isinstance(argv, list) and argv else ""
        candidate = Path(executable)
        if executable and (
            candidate.is_absolute() or _is_path_qualified_executable(executable)
        ):
            selected = candidate if candidate.is_absolute() else cwd / candidate
            resolved = shutil.which(str(selected))
        else:
            resolved = shutil.which(executable, path=search_path)
        item: dict[str, Any] = {"name": Path(executable).name.lower()}
        if resolved:
            try:
                resolved_candidate = Path(resolved)
                if not resolved_candidate.is_absolute():
                    resolved_candidate = cwd / resolved_candidate
                execution_path = Path(os.path.abspath(resolved_candidate))
                path = execution_path.resolve(strict=True)
                metadata = path.stat()
                item.update(
                    {
                        "selected_path": os.path.normcase(str(execution_path)),
                        "path": os.path.normcase(str(path)),
                        "size": int(metadata.st_size),
                        "mtime_ns": int(metadata.st_mtime_ns),
                        "content_digest": digest_file(path),
                        "_execution_path": str(execution_path),
                    }
                )
            except (OSError, RuntimeError):
                item["path"] = "unresolved"
        else:
            item["path"] = "missing"
        executables.append(item)
    if any(
        not isinstance(item.get("content_digest"), str)
        or not item.get("content_digest")
        for item in executables
    ):
        return None
    return executables


def _verification_executable_payload(
    executables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            key: value
            for key, value in executable.items()
            if key != "_execution_path"
        }
        for executable in executables
    ]


def _verification_environment_digest_from_records(
    executables: list[dict[str, Any]],
    *,
    cwd: Path,
    environment: dict[str, str],
) -> str:
    environment_payload = json.dumps(
        sorted(
            (
                _verification_environment_key(str(key)),
                str(value),
            )
            for key, value in environment.items()
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    payload = {
        "cwd": os.path.normcase(str(cwd.resolve())),
        "os_name": os.name,
        "platform": sys.platform,
        "machine": platform.machine(),
        "python": list(sys.version_info[:3]),
        "executables": _verification_executable_payload(executables),
        "environment_digest": hashlib.sha256(environment_payload).hexdigest(),
    }
    return _capability_digest(payload)


def _verification_environment_digest(
    checks: list[dict[str, Any]], *, cwd: Path, environment: dict[str, str] | None = None
) -> str:
    effective_environment = environment or _verification_environment(cwd=cwd)
    executables = _verification_executable_records(
        checks, cwd=cwd, environment=effective_environment
    )
    if executables is None:
        return ""
    return _verification_environment_digest_from_records(
        executables, cwd=cwd, environment=effective_environment
    )


def _verification_receipt_matches(
    source: dict[str, Any],
    *,
    contract_digest: str,
    revision: int,
    group_digest: str,
    git_root: str,
    tree_digest: str,
    environment_digest: str,
    executable_digest: str,
    host_coverage: dict[str, Any],
) -> bool:
    if not all(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
        for value in (
            contract_digest,
            group_digest,
            tree_digest,
            environment_digest,
            executable_digest,
        )
    ):
        return False
    verified_at = source.get("verified_at", 0)
    if (
        not isinstance(verified_at, int)
        or isinstance(verified_at, bool)
        or verified_at <= 0
    ):
        return False
    return bool(
        source.get("status") == "passed"
        and int(source.get("verified_revision", -1)) == revision
        and source.get("verified_contract_digest") == contract_digest
        and source.get("verified_check_digest") == group_digest
        and source.get("verified_root") == git_root
        and source.get("verified_tree_digest") == tree_digest
        and source.get("verified_environment_digest") == environment_digest
        and source.get("verified_executable_digest") == executable_digest
        and click_host_coverage.receipt_is_current(host_coverage)
        and source.get("verified_host_coverage") == host_coverage
    )


def _dependency_declarations(
    sources: dict[str, Any], source_keys: set[str]
) -> dict[str, list[str]]:
    declarations: dict[str, list[str]] = {}
    for source_key in source_keys:
        source = sources.get(source_key)
        patterns = source.get("dependency_patterns", []) if isinstance(source, dict) else []
        if isinstance(patterns, list) and patterns:
            declarations[source_key] = list(patterns)
    return declarations


def _dependency_observations(
    sources: dict[str, Any], source_keys: set[str]
) -> dict[str, dict[str, Any]]:
    observations: dict[str, dict[str, Any]] = {}
    for source_key in source_keys:
        source = sources.get(source_key)
        observation = (
            source.get("verified_dependency_observation")
            if isinstance(source, dict)
            else None
        )
        if click_dependency_cache.dependency_observation_is_valid(observation):
            observations[source_key] = {
                **observation,
                "paths": list(observation["paths"]),
            }
    return observations


def _dependency_receipt_is_valid(receipt: Any) -> bool:
    if not isinstance(receipt, dict):
        return False
    provider = receipt.get("provider")
    manifest_digest = receipt.get("manifest_digest")
    entry_digest = receipt.get("entry_digest")
    dependency_digest = receipt.get("dependency_digest")
    observation_digest = receipt.get("observation_digest")
    observation = receipt.get("observation")
    manifest_is_valid = bool(
        isinstance(manifest_digest, str)
        and (
            provider == click_dependency_cache.CONTRACT_PROVIDER_NAME
            and not manifest_digest
            or provider
            in {
                click_dependency_cache.MANIFEST_PROVIDER_NAME,
                click_dependency_cache.COMBINED_PROVIDER_NAME,
            }
            and re.fullmatch(r"[0-9a-f]{64}", manifest_digest)
        )
    )
    return bool(
        provider in click_dependency_cache.PROVIDER_NAMES
        and manifest_is_valid
        and isinstance(entry_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", entry_digest)
        and isinstance(dependency_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", dependency_digest)
        and isinstance(observation_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", observation_digest)
        and click_dependency_cache.dependency_observation_is_valid(observation)
        and observation_digest
        == click_dependency_cache.dependency_observation_digest(observation)
        and click_dependency_cache.receipt_paths_are_valid(
            receipt.get("resolved_paths")
        )
    )


def _dependency_receipt_matches(
    source: dict[str, Any],
    receipt: Any,
    *,
    contract_digest: str,
    revision: int,
    group_digest: str,
    git_root: str,
    environment_digest: str,
    executable_digest: str,
    host_coverage: dict[str, Any],
) -> bool:
    if not _dependency_receipt_is_valid(receipt):
        return False
    if not click_dependency_cache.dependency_observation_is_complete(
        receipt["observation"]
    ):
        return False
    if not all(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
        for value in (
            contract_digest,
            group_digest,
            environment_digest,
            executable_digest,
        )
    ):
        return False
    verified_revision = source.get("verified_revision", -1)
    verified_at = source.get("verified_at", 0)
    if (
        not isinstance(verified_revision, int)
        or isinstance(verified_revision, bool)
        or verified_revision < 0
        or verified_revision >= revision
        or not isinstance(verified_at, int)
        or isinstance(verified_at, bool)
        or verified_at <= 0
        or not isinstance(source.get("verified_tree_digest"), str)
        or re.fullmatch(
            r"[0-9a-f]{64}", str(source.get("verified_tree_digest", ""))
        )
        is None
    ):
        return False
    return bool(
        source.get("status") == "stale"
        and source.get("verified_contract_digest") == contract_digest
        and source.get("verified_check_digest") == group_digest
        and source.get("verified_root") == git_root
        and source.get("verified_environment_digest") == environment_digest
        and source.get("verified_executable_digest") == executable_digest
        and click_host_coverage.receipt_is_current(host_coverage)
        and source.get("verified_host_coverage") == host_coverage
        and source.get("verified_dependency_provider") == receipt["provider"]
        # The full manifest digest is audit metadata. The normalized relevant
        # entry is the authority boundary, so unrelated settings may change.
        and source.get("verified_dependency_entry_digest")
        == receipt["entry_digest"]
        and source.get("verified_dependency_digest")
        == receipt["dependency_digest"]
        and source.get("verified_dependency_paths")
        == receipt["resolved_paths"]
        and source.get("verified_dependency_observation_digest")
        == receipt["observation_digest"]
        and source.get("verified_dependency_observation")
        == receipt["observation"]
    )


def _clear_dependency_receipt(source: dict[str, Any]) -> None:
    source["verified_dependency_provider"] = ""
    source["verified_dependency_manifest_digest"] = ""
    source["verified_dependency_entry_digest"] = ""
    source["verified_dependency_digest"] = ""
    source["verified_dependency_paths"] = []
    source["verified_dependency_observation_digest"] = ""
    source["verified_dependency_observation"] = {}
    source["dependency_reuse_count"] = 0
    source["last_dependency_reused_at"] = 0
    source["last_dependency_reused_from_revision"] = -1


def _store_dependency_receipt(
    source: dict[str, Any], receipt: Any
) -> None:
    _clear_dependency_receipt(source)
    if not _dependency_receipt_is_valid(receipt):
        return
    source["verified_dependency_provider"] = receipt["provider"]
    source["verified_dependency_manifest_digest"] = receipt["manifest_digest"]
    source["verified_dependency_entry_digest"] = receipt["entry_digest"]
    source["verified_dependency_digest"] = receipt["dependency_digest"]
    source["verified_dependency_paths"] = list(receipt["resolved_paths"])
    source["verified_dependency_observation_digest"] = receipt[
        "observation_digest"
    ]
    source["verified_dependency_observation"] = {
        **receipt["observation"],
        "paths": list(receipt["observation"]["paths"]),
    }


def _promote_dependency_receipt(
    source: dict[str, Any],
    receipt: dict[str, Any],
    *,
    revision: int,
    tree_digest: str,
) -> None:
    prior_revision = int(source.get("verified_revision", -1))
    source["status"] = "passed"
    source["verified_revision"] = revision
    source["verified_tree_digest"] = tree_digest
    source["verified_dependency_manifest_digest"] = receipt["manifest_digest"]
    source["verified_dependency_entry_digest"] = receipt["entry_digest"]
    source["verified_dependency_digest"] = receipt["dependency_digest"]
    source["verified_dependency_paths"] = list(receipt["resolved_paths"])
    source["verified_dependency_observation_digest"] = receipt[
        "observation_digest"
    ]
    source["verified_dependency_observation"] = {
        **receipt["observation"],
        "paths": list(receipt["observation"]["paths"]),
    }
    source["last_exit_code"] = 0
    source["unchanged_failure_retries"] = 0
    source["dependency_reuse_count"] = int(
        source.get("dependency_reuse_count", 0)
    ) + 1
    source["last_dependency_reused_at"] = int(time.time()) or 1
    source["last_dependency_reused_from_revision"] = prior_revision


def _clear_safe_change_receipt(source: dict[str, Any]) -> None:
    source["verified_safe_change_receipt"] = {}
    source["safe_change_reuse_count"] = 0
    source["last_safe_change_reused_at"] = 0
    source["last_safe_change_reused_from_revision"] = -1
    source["last_safe_change_paths"] = []
    source["last_safe_change_path_count"] = 0
    source["last_safe_change_decision_digest"] = ""


def _store_safe_change_receipt(source: dict[str, Any], receipt: Any) -> None:
    _clear_safe_change_receipt(source)
    if not click_change_policy.receipt_is_valid(receipt):
        return
    source["verified_safe_change_receipt"] = receipt


def _safe_change_receipt_matches(
    source: dict[str, Any],
    decision: Any,
    *,
    contract_digest: str,
    revision: int,
    group_digest: str,
    git_root: str,
    environment_digest: str,
    executable_digest: str,
    host_coverage: dict[str, Any],
) -> bool:
    if not isinstance(decision, dict) or decision.get("status") != "reuse":
        return False
    receipt = decision.get("receipt")
    decision_digest = decision.get("decision_digest")
    changed_paths = decision.get("changed_paths")
    if (
        not click_change_policy.receipt_is_valid(receipt)
        or not click_change_policy.changed_paths_are_valid(changed_paths)
        or not isinstance(decision_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", decision_digest) is None
    ):
        return False
    verified_revision = source.get("verified_revision", -1)
    verified_at = source.get("verified_at", 0)
    return bool(
        source.get("status") == "stale"
        and isinstance(verified_revision, int)
        and not isinstance(verified_revision, bool)
        and 0 <= verified_revision < revision
        and isinstance(verified_at, int)
        and not isinstance(verified_at, bool)
        and verified_at > 0
        and source.get("verified_contract_digest") == contract_digest
        and source.get("verified_check_digest") == group_digest
        and source.get("verified_root") == git_root
        and source.get("verified_environment_digest") == environment_digest
        and source.get("verified_executable_digest") == executable_digest
        and click_host_coverage.receipt_is_current(host_coverage)
        and source.get("verified_host_coverage") == host_coverage
        and source.get("verified_safe_change_receipt", {})
        != {}
        and click_change_policy.receipt_is_valid(
            source.get("verified_safe_change_receipt")
        )
    )


def _promote_safe_change_receipt(
    source: dict[str, Any],
    decision: dict[str, Any],
    *,
    revision: int,
    tree_digest: str,
) -> None:
    prior_revision = int(source.get("verified_revision", -1))
    changed_paths = list(decision["changed_paths"])
    source["status"] = "passed"
    source["verified_revision"] = revision
    source["verified_tree_digest"] = tree_digest
    source["verified_safe_change_receipt"] = decision["receipt"]
    source["last_exit_code"] = 0
    source["unchanged_failure_retries"] = 0
    source["safe_change_reuse_count"] = int(
        source.get("safe_change_reuse_count", 0)
    ) + 1
    source["last_safe_change_reused_at"] = int(time.time()) or 1
    source["last_safe_change_reused_from_revision"] = prior_revision
    source["last_safe_change_paths"] = changed_paths[:128]
    source["last_safe_change_path_count"] = len(changed_paths)
    source["last_safe_change_decision_digest"] = decision["decision_digest"]


def _reuse_binding_reason(
    source: dict[str, Any],
    *,
    contract_digest: str,
    group_digest: str,
    git_root: str,
    tree_digest: str,
    environment_digest: str,
    executable_digest: str,
    host_coverage: dict[str, Any],
    cross_revision: bool = False,
) -> str:
    """Explain a failed reuse binding without changing its authority decision."""
    if source.get("verified_contract_digest") != contract_digest:
        return "contract-binding-changed"
    if source.get("verified_check_digest") != group_digest:
        return "check-binding-changed"
    if (
        source.get("verified_root") != git_root
        or not cross_revision
        and source.get("verified_tree_digest") != tree_digest
    ):
        return "workspace-ambiguous"
    if source.get("verified_executable_digest") != executable_digest:
        return "executable-binding-changed"
    if source.get("verified_environment_digest") != environment_digest:
        return "environment-binding-changed"
    if (
        not click_host_coverage.receipt_is_current(host_coverage)
        or source.get("verified_host_coverage") != host_coverage
    ):
        return "host-coverage-binding-changed"
    return "receipt-invalid"


def _successor_binding_reason(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    group_digest: str,
    git_root: str,
    environment_digest: str,
    executable_digest: str,
    host_coverage: dict[str, Any],
) -> str:
    """Validate prior facts against the current lifecycle's bindings."""
    if (
        previous.get("dependency_patterns", []) != current.get("dependency_patterns", [])
        or previous.get("dependency_declaration_digest", "")
        != current.get("dependency_declaration_digest", "")
    ):
        return "contract-binding-changed"
    if previous.get("verified_check_digest") != group_digest:
        return "check-binding-changed"
    if previous.get("verified_root") != git_root:
        return "workspace-ambiguous"
    if previous.get("verified_executable_digest") != executable_digest:
        return "executable-binding-changed"
    if previous.get("verified_environment_digest") != environment_digest:
        return "environment-binding-changed"
    if (
        not click_host_coverage.receipt_is_current(host_coverage)
        or previous.get("verified_host_coverage") != host_coverage
    ):
        return "host-coverage-binding-changed"
    if previous.get("shard") != current.get("shard"):
        return "check-binding-changed"
    verified_at = previous.get("verified_at")
    if (
        previous.get("status") != "passed"
        or previous.get("last_exit_code") != 0
        or not isinstance(verified_at, int)
        or isinstance(verified_at, bool)
        or verified_at <= 0
        or not isinstance(previous.get("verified_tree_digest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", previous["verified_tree_digest"])
        is None
    ):
        return "successor-evidence-integrity-invalid"
    return ""


def _requalify_successor_baseline(
    current: dict[str, Any],
    previous: dict[str, Any],
    *,
    contract_digest: str,
    revision: int,
    group_digest: str,
    units: int,
    tree_digest: str,
    environment_digest: str,
    executable_digest: str,
    host_coverage: dict[str, Any],
    exact_tree: bool,
) -> None:
    """Create a new-lifecycle baseline only after explicit binding checks."""
    current_shard = current.get("shard")
    current_patterns = list(current.get("dependency_patterns", []))
    current_declaration = current.get("dependency_declaration_digest", "")
    preserved = json.loads(json.dumps(previous))
    current.clear()
    current.update(preserved)
    if current_shard is None:
        current.pop("shard", None)
    else:
        current["shard"] = current_shard
    current.update(
        dependency_patterns=current_patterns,
        dependency_declaration_digest=current_declaration,
        status="passed" if exact_tree else "stale",
        verified_revision=revision if exact_tree else max(0, revision - 1),
        attempts=0,
        unchanged_failure_retries=0,
        last_exit_code=0,
        last_check_digest=group_digest,
        locked_check_digest=group_digest,
        reserved_units=units,
        reserved_check_digest=group_digest,
        verified_contract_digest=contract_digest,
        verified_check_digest=group_digest,
        verified_units=units,
        verified_tree_digest=tree_digest if exact_tree else previous["verified_tree_digest"],
        verified_environment_digest=environment_digest,
        verified_executable_digest=executable_digest,
        verified_host_coverage=dict(host_coverage),
        dependency_reuse_count=0,
        last_dependency_reused_at=0,
        last_dependency_reused_from_revision=-1,
        safe_change_reuse_count=0,
        last_safe_change_reused_at=0,
        last_safe_change_reused_from_revision=-1,
        last_safe_change_paths=[],
        last_safe_change_path_count=0,
        last_safe_change_decision_digest="",
        successor_reuse_count=0,
        last_successor_reused_at=0,
        last_successor_origin_batch_id="",
        last_successor_origin_evidence_session_id="",
        last_successor_origin_contract_id="",
        last_successor_candidate_digest="",
        last_successor_origin_revision=-1,
        last_successor_mode="",
    )


def _mark_successor_reuse(
    source: dict[str, Any], metadata: dict[str, Any], *, mode: str
) -> None:
    source["successor_reuse_count"] = int(
        source.get("successor_reuse_count", 0)
    ) + 1
    source["last_successor_reused_at"] = int(time.time()) or 1
    source["last_successor_origin_batch_id"] = metadata["batch_id"]
    source["last_successor_origin_evidence_session_id"] = metadata.get("evidence_session_id", "")
    source["last_successor_origin_contract_id"] = metadata.get("contract_id", "")
    source["last_successor_candidate_digest"] = metadata["candidate_digest"]
    source["last_successor_origin_revision"] = metadata["origin_revision"]
    source["last_successor_mode"] = mode
    source["verified_at"] = int(time.time()) or 1


def _observation_nonreuse_reason(observation: Any) -> str:
    if not click_dependency_cache.dependency_observation_is_valid(observation):
        return "observer-incomplete"
    if observation.get("external_access") is True:
        return "external-input-unmodeled"
    if not click_dependency_cache.dependency_observation_is_complete(observation):
        return "observer-incomplete"
    return "observed-input-changed"


def _default_incremental_reason(source: dict[str, Any]) -> tuple[str, bool]:
    """Return a conservative initial run reason and evaluability marker."""
    if source.get("status") == "failed":
        return "previous-verification-failed", False
    if source.get("status") != "stale":
        return "no-passing-evidence", False
    observation = source.get("verified_dependency_observation")
    if observation:
        reason = _observation_nonreuse_reason(observation)
        return reason, reason in {"observer-incomplete", "external-input-unmodeled"}
    if click_change_policy.receipt_is_valid(
        source.get("verified_safe_change_receipt")
    ):
        return "safe-change-policy-not-covered", False
    return "policy-unavailable", False


def _canonical_incremental_plan(
    sources: dict[str, Any],
    *,
    requested_keys: set[str],
    group_digests: dict[str, str],
    revision: int,
    previous_revisions: dict[str, int],
    reused_keys: set[str],
    dependency_reused_keys: set[str],
    safe_change_reused_keys: set[str],
    not_evaluable_keys: set[str],
    reason_codes: dict[str, str],
) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    for source_key in sorted(requested_keys):
        source = sources[source_key]
        if source_key in dependency_reused_keys:
            selected = "reuse-dependency"
            authority = "runtime-dependency-observation"
        elif source_key in safe_change_reused_keys:
            selected = "reuse-safe-change"
            authority = "repository-safe-change-policy"
        elif source_key in reused_keys:
            selected = "reuse-exact"
            authority = "exact-receipt"
        elif source_key in not_evaluable_keys:
            selected = "not-evaluable"
            authority = "none"
        else:
            selected = "run"
            authority = "runner"
        baseline = source.get("last_success_duration_baseline")
        if (not click_incremental.baseline_is_valid(baseline)
            or baseline["check_digest"] != group_digests[source_key]):
            baseline = None
        avoided = baseline["duration_ms"] if baseline is not None else None
        decisions.append(
            click_incremental.decision(
                source_key=source_key,
                decision=selected,
                reason_code=reason_codes[source_key],
                current_revision=revision,
                previous_revision=previous_revisions[source_key],
                check_digest=group_digests[source_key],
                authority_source=authority,
                estimated_avoided_ms=avoided if source_key in reused_keys else 0,
                duration_baseline=baseline,
            )
        )
    return click_incremental.build_plan(decisions, current_revision=revision)


def _contains_deep_verification_marker(values: list[str]) -> bool:
    joined = " ".join(values)
    return any(marker in joined for marker in DEEP_VERIFICATION_MARKERS)


def _arguments_have_filter(arguments: list[str]) -> bool:
    return any(
        argument in TEST_FILTER_OPTIONS
        or any(argument.startswith(f"{option}=") for option in TEST_FILTER_OPTIONS)
        for argument in arguments
    )


def _verification_targets(
    arguments: list[str], *, skip_words: set[str] | None = None
) -> list[str]:
    skip_words = skip_words or set()
    targets: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            targets.extend(
                item for item in arguments[index + 1 :] if item not in skip_words
            )
            break
        if argument in TEST_OPTIONS_WITH_VALUES:
            index += 2
            continue
        if argument.startswith("-") or argument in skip_words:
            index += 1
            continue
        targets.append(argument)
        index += 1
    return targets


def _scope_with_kind_floor(scope: str, values: list[str]) -> str:
    if not _contains_deep_verification_marker(values):
        return scope
    return "broad" if scope == "targeted" else "deep"


def _minimum_test_runner_class(runner: str, arguments: list[str]) -> str:
    if runner == "unittest" and "discover" in arguments:
        return _scope_with_kind_floor("broad", [runner, *arguments])
    if _arguments_have_filter(arguments):
        return _scope_with_kind_floor("broad", [runner, *arguments])
    broad_targets = {".", "./", "...", "./...", "all", "test", "tests", "spec"}
    targets = _verification_targets(arguments, skip_words={"run", "exec", "x"})
    scope = "broad"
    if len(targets) == 1:
        target = targets[0]
        normalized = target.rstrip("/\\")
        if normalized not in broad_targets and (
            "::" in target
            or Path(normalized).suffix.lower() in TEST_TARGET_SUFFIXES
            or (runner == "unittest" and "." in normalized)
        ):
            scope = "targeted"
    return _scope_with_kind_floor(scope, [runner, *arguments])


def _minimum_verification_class(
    tokens: list[str], *, wrapper_depth: int = 0
) -> str | None:
    executable, arguments = _command_parts(tokens)
    if not executable:
        return None
    if executable in DEEP_VERIFICATION_EXECUTABLES:
        return "deep"
    if executable in {"python", "python3", "py", "pypy", "pypy3"}:
        if executable == "py" and arguments and re.fullmatch(
            r"-\d+(?:\.\d+)?(?:-\d+)?", arguments[0]
        ):
            arguments = arguments[1:]
        if len(arguments) < 2 or arguments[0] != "-m":
            return None
        module = arguments[1]
        if module not in PYTHON_VERIFICATION_MODULES:
            return None
        if module == "coverage":
            return "deep"
        return _minimum_test_runner_class(module, arguments[2:])
    if executable == "uv":
        if wrapper_depth >= 2 or not arguments or arguments[0] != "run":
            return None
        nested = arguments[1:]
        while nested and nested[0].startswith("-"):
            nested = nested[1:]
        return _minimum_verification_class(nested, wrapper_depth=wrapper_depth + 1)
    if executable in VERIFICATION_EXECUTABLES:
        if executable in {"bats", "jest", "phpunit", "pytest", "rspec", "vitest"}:
            return _minimum_test_runner_class(executable, arguments)
        return "broad"
    if executable == "node":
        if any(
            argument in {"-e", "--eval", "-p", "--print"}
            or argument.startswith(("--eval=", "--print="))
            for argument in arguments
        ):
            return None
        if arguments[:1] == ["--check"]:
            targets = [argument for argument in arguments[1:] if not argument.startswith("-")]
            return (
                "targeted"
                if len(targets) == 1
                and Path(targets[0]).suffix.lower() in {".cjs", ".js", ".mjs"}
                else None
            )
        if "--test" in arguments:
            test_arguments = [argument for argument in arguments if argument != "--test"]
            return _minimum_test_runner_class("node", test_arguments)
        return None
    if executable in {"npm", "pnpm", "yarn", "bun"}:
        meaningful = [item for item in arguments if item not in {"run", "exec", "x"}]
        target = meaningful[0] if meaningful else ""
        if not (
            any(marker in target for marker in VERIFICATION_NAME_MARKERS)
            or target in {"build", "check", "lint", "typecheck", "type-check"}
        ):
            return None
        return "deep" if _contains_deep_verification_marker(meaningful) else "broad"
    if executable in {"npx", "pnpx", "bunx"}:
        target_index = next(
            (index for index, argument in enumerate(arguments) if not argument.startswith("-")),
            -1,
        )
        if target_index < 0:
            return None
        target = arguments[target_index]
        nested_arguments = arguments[target_index + 1 :]
        if target in DEEP_VERIFICATION_EXECUTABLES:
            return "deep"
        if target in {"jest", "pytest", "vitest"}:
            return _minimum_test_runner_class(target, nested_arguments)
        if target in VERIFICATION_EXECUTABLES:
            return "broad"
        if any(marker in target for marker in VERIFICATION_NAME_MARKERS):
            return "deep"
        return None
    if executable == "cargo":
        if not arguments or arguments[0] not in {
            "audit",
            "bench",
            "check",
            "clippy",
            "nextest",
            "test",
        }:
            return None
        if arguments[0] in {"audit", "bench"}:
            return "deep"
        if arguments[0] in {"check", "clippy", "nextest"}:
            return "broad"
        test_targets = [
            argument
            for argument in arguments[1:]
            if not argument.startswith("-") and argument not in {"all", "workspace"}
        ]
        return "targeted" if len(test_targets) == 1 else "broad"
    if executable == "go":
        if not arguments or arguments[0] not in {"test", "vet"}:
            return None
        if arguments[0] == "vet":
            return "broad"
        if _arguments_have_filter(arguments[1:]):
            return "broad"
        targets = [argument for argument in arguments[1:] if not argument.startswith("-")]
        recursive = any(target == "./..." or target.endswith("/...") for target in targets)
        return "targeted" if len(targets) == 1 and not recursive else "broad"
    if executable == "ruff":
        if not arguments or arguments[0] != "check":
            return None
        targets = _verification_targets(arguments[1:])
        return (
            "targeted"
            if len(targets) == 1
            and Path(targets[0].rstrip("/\\")).suffix.lower() in TEST_TARGET_SUFFIXES
            else "broad"
        )
    if executable == "mypy":
        targets = _verification_targets(arguments)
        return (
            "targeted"
            if len(targets) == 1 and Path(targets[0]).suffix.lower() == ".py"
            else "broad"
        )
    if executable == "tsc":
        return "broad" if "--noemit" in arguments else None
    if executable in {"dotnet", "gradle", "gradlew", "gradlew.bat", "mvn", "mvnw", "mvnw.cmd"}:
        if not any(
            any(marker in argument for marker in VERIFICATION_NAME_MARKERS)
            for argument in arguments
        ):
            return None
        if _contains_deep_verification_marker(arguments):
            return "deep"
        return "targeted" if any("filter" in item for item in arguments) else "broad"
    if executable in {"make", "gmake", "cmake", "ctest", "pre-commit"}:
        recognized = executable in {"ctest", "pre-commit"} or any(
            any(marker in argument for marker in VERIFICATION_NAME_MARKERS)
            for argument in arguments
        )
        if not recognized:
            return None
        if _contains_deep_verification_marker(arguments):
            return "deep"
        if executable == "ctest" and any(item in {"-r", "--tests-regex"} for item in arguments):
            return _scope_with_kind_floor("broad", arguments)
        if executable == "pre-commit" and "--files" in arguments:
            file_index = arguments.index("--files") + 1
            files = [item for item in arguments[file_index:] if not item.startswith("-")]
            return "targeted" if len(files) == 1 else "broad"
        return "broad"
    stem = Path(executable).stem.lower()
    if any(marker in stem for marker in VERIFICATION_NAME_MARKERS):
        return "deep"
    return None


def _is_recognized_verification_tokens(tokens: list[str]) -> bool:
    return _minimum_verification_class(tokens) is not None


def _is_recognized_verification_command(command: str) -> bool:
    segments = _shell_segments(command)
    if segments:
        return any(_is_recognized_verification_tokens(segment) for segment in segments)
    try:
        fallback = shlex.split(command, posix=True)
    except ValueError:
        return False
    return _is_recognized_verification_tokens(fallback)


def _git_capture(cwd: Path, arguments: list[str]) -> bytes | None:
    executable, error = _resolve_read_only_executable("git", workspace=cwd)
    if error or executable is None:
        return None
    try:
        result = click_process.run_argv(
            [
                executable,
                "--no-pager",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                *arguments,
            ],
            cwd=cwd,
            env=_sanitized_git_environment(workspace=cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _hash_workspace_path(hasher: Any, root: Path, relative: str) -> None:
    encoded_path = os.fsencode(relative)
    hasher.update(len(encoded_path).to_bytes(8, "big"))
    hasher.update(encoded_path)
    target = root / relative
    try:
        metadata = target.lstat()
    except OSError:
        hasher.update(b"missing")
        return
    hasher.update(str(metadata.st_mode).encode())
    if target.is_symlink():
        try:
            hasher.update(os.fsencode(os.readlink(target)))
        except OSError:
            hasher.update(b"unreadable-link")
        return
    if not target.is_file():
        hasher.update(b"non-file")
        return
    try:
        with target.open("rb") as handle:
            while True:
                chunk = handle.read(128 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError:
        hasher.update(b"unreadable-file")


def _git_workspace_snapshot(
    cwd: Path, protected_untracked: list[str] | None = None
) -> dict[str, Any] | None:
    root_output = _git_capture(cwd, ["rev-parse", "--show-toplevel"])
    if root_output is None:
        return None
    root = Path(os.fsdecode(root_output.strip()))
    has_head = _git_capture(root, ["rev-parse", "--verify", "HEAD"]) is not None
    diff_commands = (
        [["diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--"]]
        if has_head
        else [
            ["diff", "--binary", "--no-ext-diff", "--no-textconv", "--cached", "--"],
            ["diff", "--binary", "--no-ext-diff", "--no-textconv", "--"],
        ]
    )
    hasher = hashlib.sha256()
    if has_head:
        head_tree = _git_capture(root, ["rev-parse", "HEAD^{tree}"])
        if head_tree is None:
            return None
        hasher.update(len(head_tree).to_bytes(8, "big"))
        hasher.update(head_tree)
    for arguments in diff_commands:
        diff = _git_capture(root, arguments)
        if diff is None:
            return None
        hasher.update(len(diff).to_bytes(8, "big"))
        hasher.update(diff)

    untracked_output = _git_capture(
        root, ["ls-files", "--others", "--exclude-standard", "-z"]
    )
    if untracked_output is None:
        return None
    current_untracked = [
        os.fsdecode(item) for item in untracked_output.split(b"\0") if item
    ]
    if protected_untracked is None:
        protected_untracked = [*current_untracked]
    for relative in sorted(protected_untracked):
        _hash_workspace_path(hasher, root, relative)
    return {
        "root": str(root),
        "digest": hasher.hexdigest(),
        "protected_untracked": protected_untracked,
        "current_untracked": current_untracked,
    }


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


def _validated_shard_checks(
    plan: dict[str, Any], *, scale: str
) -> tuple[list[dict[str, Any]] | None, str]:
    candidate = {
        "version": VERIFICATION_PROTOCOL_VERSION,
        "checks": [
            {
                "evidence_id": str(child["evidence_id"]),
                "argv": list(argv),
                "class": "broad",
            }
            for child in plan.get("children", [])
            if isinstance(child, dict)
            for argv in child.get("checks", [])
            if isinstance(argv, list)
        ],
    }
    normalized, _, error = _validate_verification_batch(
        json.dumps(candidate, separators=(",", ":")), scale, None
    )
    if error or normalized is None:
        return None, error or "Evidence Shards child checks are invalid."
    grouped, grouping_error = _verification_groups(normalized)
    if grouping_error:
        return None, grouping_error
    expected = {
        str(child["source_key"]): str(child["check_digest"])
        for child in plan.get("children", [])
        if isinstance(child, dict)
    }
    if set(grouped) != set(expected) or any(
        _verification_group_digest(checks) != expected[source_key]
        for source_key, checks in grouped.items()
    ):
        return None, "Evidence Shards child check identity is inconsistent."
    return list(normalized["checks"]), ""


def _expand_evidence_shards(
    state: dict[str, Any],
    batch: dict[str, Any],
    *,
    scale: str,
    workspace: Path,
    git_capture: Callable[[Path, list[str]], bytes | None],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str], str]:
    """Expand submitted broad parents while retaining their approval identity."""
    evidence_state = state.get("evidence_state")
    sources = evidence_state.get("sources") if isinstance(evidence_state, dict) else None
    if not isinstance(evidence_state, dict) or not isinstance(sources, dict):
        return None, None, [], "Click Evidence Shards registry is unavailable."
    grouped, grouping_error = _verification_groups(batch)
    if grouping_error:
        return None, None, [], grouping_error

    expanded: list[dict[str, Any]] = []
    advisories: list[str] = []
    for parent_source_key, parent_checks in grouped.items():
        evidence_id = str(parent_checks[0].get("evidence_id", "argv"))
        parent_check_digest = _verification_group_digest(parent_checks)
        submitted_source = sources.get(parent_source_key)
        if click_evidence_shards.is_child_source(submitted_source):
            return (
                None,
                None,
                advisories,
                "Evidence shard children are internal. Submit the declared broad "
                "parent evidence id so Click can revalidate the complete plan.",
            )
        active = click_evidence_shards.active_set(
            evidence_state, parent_source_key
        )
        if active is not None and active.get("parent_check_digest") != parent_check_digest:
            return (
                None,
                None,
                advisories,
                "A sharded argv evidence source is locked to a different broad "
                "parent check set. Reuse that set or stage a new contract.",
            )
        if active is None and isinstance(submitted_source, dict):
            incompatible_bindings = (
                (
                    "reserved_check_digest",
                    "An argv evidence source is already reserved to a different exact "
                    "check set for this contract. Reuse that set or stage a new contract.",
                ),
                (
                    "locked_check_digest",
                    "A previously successful argv evidence source is locked to its exact "
                    "check set. Re-run that set after the relevant mutation.",
                ),
                (
                    "last_check_digest",
                    "An argv evidence source changed its check set without an intervening "
                    "mutation. Fix the implementation or reuse the original check set.",
                ),
            )
            for field, message in incompatible_bindings:
                bound = str(submitted_source.get(field, ""))
                if bound and bound != parent_check_digest:
                    return None, None, advisories, message
        eligible = any(
            check.get("class") in {"broad", "deep"} for check in parent_checks
        )
        if not eligible and active is None:
            expanded.extend(parent_checks)
            continue

        decision = click_evidence_shards.resolve_plan(
            workspace,
            parent_checks,
            parent_source_key=parent_source_key,
            git_capture=git_capture,
        )
        plan_current = bool(
            decision.get("status") == "sharded"
            and (
                active is None
                or click_evidence_shards.plan_matches_shard_set(decision, active)
            )
        )
        shard_checks: list[dict[str, Any]] | None = None
        validation_error = ""
        if plan_current:
            shard_checks, validation_error = _validated_shard_checks(
                decision, scale=scale
            )
            plan_current = shard_checks is not None and not validation_error

        if active is not None and not plan_current:
            sources, collapse_error = click_evidence.collapse_shard_plan(
                state, parent_source_key
            )
            if collapse_error or sources is None:
                return None, None, advisories, collapse_error
            evidence_state = state["evidence_state"]
            reason = validation_error or str(
                decision.get("reason", "plan-unavailable")
            )
            advisories.append(
                f"Click Evidence Shards [{evidence_id}]: {reason}; running the "
                "original broad suite."
            )
            expanded.extend(parent_checks)
            continue

        if active is None and plan_current:
            sources, activation_error = click_evidence.activate_shard_plan(
                state, parent_source_key, decision
            )
            if activation_error or sources is None:
                return None, None, advisories, activation_error
            evidence_state = state["evidence_state"]
        if plan_current:
            assert shard_checks is not None
            expanded.extend(shard_checks)
            advisories.append(
                f"Click Evidence Shards [{evidence_id}]: expanded the broad suite "
                f"into {len(decision['children'])} independent shard(s)."
            )
            continue

        reason = validation_error or str(
            decision.get("reason", "plan-unavailable")
        )
        if (
            decision.get("status") == "fallback" or validation_error
        ) and reason not in {"manifest-not-committed", "git-root-unavailable"}:
            advisories.append(
                f"Click Evidence Shards [{evidence_id}]: {reason}; running the "
                "original broad suite."
            )
        expanded.extend(parent_checks)

    expanded_batch: dict[str, Any] = {
        "version": VERIFICATION_PROTOCOL_VERSION,
        "checks": expanded,
    }
    if isinstance(batch.get("workdir"), str):
        expanded_batch["workdir"] = str(batch["workdir"])
    return (
        expanded_batch,
        sources,
        advisories,
        "",
    )


def runner_command(
    event: dict[str, Any],
    batch: dict[str, Any],
    batch_digest: str,
    runner_token: str,
    *,
    runner_script: Path,
    render_command: Callable[[list[str]], str],
) -> str:
    arguments = [
        sys.executable,
        str(runner_script),
        "--state-root",
        str(click_state.state_root().resolve()),
        "run-verification",
        str(click_state.contract_path(event).resolve()),
        batch_digest,
        runner_token,
        click_capability.encode_request(batch),
    ]
    return render_command(arguments)


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


def _prepare_verification(
    event: dict[str, Any], raw: str, *, runner_script: Path,
    render_command: Callable[[list[str]], str],
    git_workspace_snapshot: Callable[..., dict[str, Any] | None] = _git_workspace_snapshot,
    git_capture: Callable[[Path, list[str]], bytes | None] = _git_capture,
) -> tuple[str, str, str]:
    # A preparation hook and its runner are different processes. Measure their
    # local elapsed segments, not a subtraction of cross-process clock origins.
    started = time.perf_counter_ns()
    request_started = time.monotonic_ns()
    trace: dict[str, Any] = {}
    result = _prepare_verification_impl(
        event, raw, runner_script=runner_script, render_command=render_command,
        git_workspace_snapshot=git_workspace_snapshot, git_capture=git_capture,
        measurement=trace,
    )
    elapsed = (time.perf_counter_ns() - started) / 1_000_000
    try:
        state = _read_contract_state(event)
        verification = state.get("verification")
        if not click_runtime_state.view(state).execution_authorized or not isinstance(verification, dict):
            return result
        tool_id = event.get("tool_use_id")
        request_id = (
            hashlib.sha256(json.dumps([
                event.get("session_id"), event.get("turn_id"), tool_id,
                hashlib.sha256(raw.encode()).hexdigest(),
            ], sort_keys=True).encode()).hexdigest()[:32]
            if isinstance(tool_id, str) and tool_id else secrets.token_hex(16)
        )
        previous_id = verification.get(click_incremental.CURRENT_BATCH_FIELD)
        previous_clock = verification.get("incremental_request_clock")
        previous_batch = click_incremental.current_batch(verification)
        batch = click_incremental.new_batch(
            trace.get("plan"), batch_id=request_id,
            revision=int(verification.get("mutation_revision", 0)), prepared_ms=elapsed,
            requested=trace.get("requested"), labels=trace.get("labels"),
            reuse_origins=trace.get("reuse_origins"),
            task={
                "mode": state.get("runtime_mode"),
                "id": state.get("contract_id") if state.get("runtime_mode") == "guarded" else state.get("evidence_session_id"),
                "name": click_incremental.sanitize_presentation(state.get("presentation"))["name"],
            },
        )
        if click_incremental.store_batch(verification, batch):
            click_incremental.start_request_clock(verification, request_id, started_ns=request_started)
            if result[1]:
                click_incremental.record_control_event(state, event, "verification-request-rejected")
                click_incremental.reject_batch(verification)
                if previous_batch and previous_batch["status"] in {"planned", "running"}:
                    verification[click_incremental.CURRENT_BATCH_FIELD] = previous_id
                    if previous_clock is not None:
                        verification["incremental_request_clock"] = previous_clock
            elif trace.get("all_reused"):
                click_incremental.finish_reuse(verification)
            if result[2]:
                click_incremental.record_control_event(state, event, "verification-guidance")
            _save_contract_state(event, state)
            if trace.get("all_reused"):
                result = (result[0], result[1], "\n".join(filter(None, (result[2], click_incremental.host_summary(verification)))))
    except Exception:
        # Measurements cannot admit/reject a command or grant reuse authority.
        pass
    return result


def _prepare_verification_impl(
    event: dict[str, Any],
    raw: str,
    *,
    runner_script: Path,
    render_command: Callable[[list[str]], str],
    git_workspace_snapshot: Callable[..., dict[str, Any] | None] = (
        _git_workspace_snapshot
    ),
    git_capture: Callable[[Path, list[str]], bytes | None] = _git_capture,
    measurement: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    state = _read_contract_state(event)
    runtime = click_runtime_state.view(state)
    if not runtime.execution_authorized:
        return "", "Start Guarded or Evidence runtime state before verification.", ""
    mutation = state.get("mutation")
    if _mutation_is_running(mutation):
        return (
            "",
            "Wait for the structured Click mutation to finish before verification.",
            "",
        )
    if isinstance(mutation, dict) and mutation.get("status") == "running":
        state["mutation"] = _fresh_mutation_state()
    verification = state.get("verification")
    if not isinstance(verification, dict):
        return (
            "",
            "Click verification state is unavailable; stage and approve again.",
            "",
        )
    prior_verification_changed_workspace = (
        verification.get("workspace_changed") is True
    )
    scale = str(verification.get("scale", ""))
    if not click_verification_policy.is_profile(scale):
        return (
            "",
            "Approved Click verification scale is invalid; stage and approve again.",
            "",
        )
    verification_advisories: list[str] = []
    sources = _evidence_sources(state)
    if sources is None:
        return (
            "",
            "This active contract predates evidence-id completion tracking. Cancel it, "
            "stage the proposal again, and obtain fresh approval.",
            "",
        )
    if not sources and runtime.guarded_approved:
        return (
            "",
            "Click evidence state is unavailable or malformed; cancel and restage.",
            "",
        )
    host_coverage = click_host_coverage.receipt_for_event(event)
    if not click_host_coverage.receipt_is_current(host_coverage):
        return (
            "",
            "Click could not establish the current host Hook coverage identity.",
            "",
        )

    observations = state.get("observations")
    if isinstance(observations, dict):
        entries = observations.get("entries")
        if isinstance(entries, dict):
            for entry in entries.values():
                if _observation_is_running(entry):
                    return (
                        "",
                        "Wait for the approved read or search to finish before starting "
                        "the final verification batch.",
                        "",
                    )

    provisional, _, error = _validate_verification_batch(raw, scale, None)
    if error:
        return "", error, ""
    assert provisional is not None
    provisional_groups, grouping_error = _verification_groups(provisional)
    if grouping_error:
        return "", grouping_error, ""
    if measurement is not None:
        measurement["requested"] = [
            {"source_key": key, "check_digest": _verification_group_digest(checks)}
            for key, checks in provisional_groups.items()
        ]
    if runtime.evidence:
        evidence_state = state.get("evidence_state")
        dynamic_ids = [
            str(checks[0].get("evidence_id", ""))
            for source_key, checks in provisional_groups.items()
            if click_evidence_shards.active_set(evidence_state, source_key) is None
        ]
        sources, error = click_evidence.register_runtime_sources(
            state, dynamic_ids, kind="argv"
        )
        if error:
            return "", error, ""
        assert sources is not None
    workspace = _tool_working_directory(event, provisional)
    if not workspace.is_dir():
        return "", "Click verification workdir is not an existing directory.", ""
    provisional["workdir"] = str(workspace)
    expanded, sources, shard_advisories, error = _expand_evidence_shards(
        state,
        provisional,
        scale=scale,
        workspace=workspace,
        git_capture=git_capture,
    )
    if error:
        return "", error, ""
    assert expanded is not None and sources is not None
    verification_advisories.extend(shard_advisories)
    encoded_expanded = json.dumps(expanded, separators=(",", ":"))
    batch, units, error = _validate_verification_batch(
        encoded_expanded, scale, sources
    )
    if error:
        return "", error, ""
    assert batch is not None
    status = str(verification.get("status", "ready"))
    if status == "running":
        claimed_at = verification.get("runner_claimed_at", 0)
        if not isinstance(claimed_at, int) or isinstance(claimed_at, bool):
            return "", "Click verification runner claim state is malformed.", ""
        if claimed_at > 0:
            return "", "The approved Click verification batch is already running.", ""
        started_at = int(verification.get("started_at", 0))
        if started_at and time.time() - started_at <= VERIFY_RUNNING_TTL_SECONDS:
            return "", "The approved Click verification batch is already running.", ""
        status = "failed"
        click_incremental.reject_batch(verification, reason="reservation-expired")
        verification["status"] = status
        verification["last_exit_code"] = 124
        for source_key in verification.get("running_evidence_keys", []):
            source = sources.get(source_key)
            if isinstance(source, dict) and source.get("status") == "running":
                source["status"] = "ready"
                source["last_exit_code"] = None
        verification["running_evidence_keys"] = []
        verification["running_environment_digests"] = {}
        verification["running_environment_binding"] = []
        verification["running_environment_binding_digest"] = ""
        verification["running_executable_digests"] = {}
        verification["running_host_coverage"] = {}
        verification["running_host_coverage_digest"] = ""
        verification["runner_claimed_at"] = 0

    revision = int(verification.get("mutation_revision", 0))
    argv_keys = _evidence_keys_for_kind(sources, "argv")
    grouped_checks, grouping_error = _verification_groups(batch)
    if grouping_error:
        return "", grouping_error, ""
    requested_keys = set(grouped_checks)
    unassigned_keys = requested_keys - argv_keys
    if unassigned_keys:
        return (
            "",
            "A verification batch may contain only declared argv evidence; "
            f"{len(unassigned_keys)} source(s) were unassigned or non-argv.",
            "",
        )

    group_digests: dict[str, str] = {}
    group_units: dict[str, int] = {}
    for source_key, checks in grouped_checks.items():
        group_digest = _verification_group_digest(checks)
        group_digests[source_key] = group_digest
        group_units[source_key] = _verification_group_units(checks)
        source = sources[source_key]
        reserved_digest = str(source.get("reserved_check_digest", ""))
        if reserved_digest and reserved_digest != group_digest:
            return (
                "",
                "An argv evidence source is already reserved to a different exact "
                "check set for this contract. Reuse that set or stage a new contract.",
                "",
            )
        locked_digest = str(source.get("locked_check_digest", ""))
        if locked_digest and locked_digest != group_digest:
            return (
                "",
                "A previously successful argv evidence source is locked to its exact "
                "check set. Re-run that set after the relevant mutation.",
                "",
            )
        last_digest = str(source.get("last_check_digest", ""))
        if last_digest and last_digest != group_digest:
            return (
                "",
                "An argv evidence source changed its check set without an intervening "
                "mutation. Fix the implementation or reuse the original check set.",
                "",
            )

    for source_key, requested_units in group_units.items():
        source = sources[source_key]
        # Retain the derived unit field for state-schema compatibility only.
        # Exact check-group identity comes from reserved_check_digest.
        source["reserved_units"] = requested_units
        if not str(source.get("reserved_check_digest", "")):
            source["reserved_check_digest"] = group_digests[source_key]

    successor_candidates: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    if runtime.evidence or runtime.guarded_approved:
        scope_digest = click_evidence.successor_scope_digest(
            str(click_state.contract_path(event).resolve())
        )
        for source_key in requested_keys:
            # A current-lifecycle execution (especially a failure) supersedes a
            # prior candidate. Never resurrect A over B's observed result.
            if sources[source_key].get("attempts", 0) or sources[source_key].get("verified_contract_digest"):
                continue
            candidate = click_evidence.successor_candidate(
                state,
                source_key,
                expected_contract_schema_version=CONTRACT_STATE_SCHEMA_VERSION,
                scope_digest=scope_digest,
            )
            if candidate is not None:
                successor_candidates[source_key] = candidate

    previous_revisions: dict[str, int] = {}
    reason_codes: dict[str, str] = {}
    not_evaluable_keys: set[str] = set()
    for source_key in requested_keys:
        source = sources[source_key]
        previous_revision = source.get("verified_revision", -1)
        previous_revisions[source_key] = (
            previous_revision
            if isinstance(previous_revision, int)
            and not isinstance(previous_revision, bool)
            and previous_revision >= -1
            else -1
        )
        reason, not_evaluable = _default_incremental_reason(source)
        reason_codes[source_key] = reason
        if not_evaluable:
            not_evaluable_keys.add(source_key)

    prepared_environment = _verification_environment(cwd=workspace)
    current_environment_digests: dict[str, str] = {}
    current_executable_digests: dict[str, str] = {}
    for source_key in requested_keys:
        executable_records = _verification_executable_records(
            grouped_checks[source_key],
            cwd=workspace,
            environment=prepared_environment,
        )
        if executable_records is None:
            return (
                "",
                "Click could not resolve and fingerprint every verification "
                "executable before planning the runner batch.",
                "",
            )
        current_environment_digests[source_key] = (
            _verification_environment_digest_from_records(
                executable_records,
                cwd=workspace,
                environment=prepared_environment,
            )
        )
        current_executable_digests[source_key] = _capability_digest(
            {"executables": _verification_executable_payload(executable_records)}
        )

    current_requested = {
        source_key
        for source_key in requested_keys
        if _evidence_is_current(sources.get(source_key), revision)
    }
    dependency_candidates = {
        source_key
        for source_key in requested_keys
        if isinstance(sources.get(source_key), dict)
        and sources[source_key].get("status") == "stale"
        and isinstance(sources[source_key].get("verified_revision"), int)
        and not isinstance(sources[source_key].get("verified_revision"), bool)
        and 0 <= int(sources[source_key].get("verified_revision", -1)) < revision
        and sources[source_key].get("verified_dependency_provider")
        in click_dependency_cache.PROVIDER_NAMES
    }
    safe_change_candidates = {
        source_key
        for source_key in requested_keys
        if isinstance(sources.get(source_key), dict)
        and sources[source_key].get("status") == "stale"
        and isinstance(sources[source_key].get("verified_revision"), int)
        and not isinstance(sources[source_key].get("verified_revision"), bool)
        and 0 <= int(sources[source_key].get("verified_revision", -1)) < revision
        and click_change_policy.receipt_is_valid(
            sources[source_key].get("verified_safe_change_receipt")
        )
        # A complete observation is stronger evidence than a repository
        # declaration. Never let the declaration override an observed input.
        and not sources[source_key].get("verified_dependency_observation")
    }
    reused_keys: set[str] = set()
    dependency_reused_keys: set[str] = set()
    safe_change_reused_keys: set[str] = set()
    successor_origins: dict[str, dict[str, Any]] = {}
    successor_imported: dict[str, dict[str, Any]] = {}
    if (
        current_requested
        or dependency_candidates
        or safe_change_candidates
        or successor_candidates
    ):
        snapshot = git_workspace_snapshot(workspace)
        if snapshot is None:
            for source_key in (
                current_requested
                | dependency_candidates
                | safe_change_candidates
                | set(successor_candidates)
            ):
                reason_codes[source_key] = "workspace-ambiguous"
                not_evaluable_keys.add(source_key)
            for source_key in current_requested:
                source = sources[source_key]
                source["status"] = "ready"
                source["verified_revision"] = -1
        else:
            contract_digest = str(state.get("contract_digest", ""))
            git_root = os.path.normcase(str(snapshot.get("root", "")))
            tree_digest = str(snapshot.get("digest", ""))
            for source_key, (previous, metadata) in successor_candidates.items():
                source = sources[source_key]
                binding_reason = _successor_binding_reason(
                    previous,
                    source,
                    group_digest=group_digests[source_key],
                    git_root=git_root,
                    environment_digest=current_environment_digests[source_key],
                    executable_digest=current_executable_digests[source_key],
                    host_coverage=host_coverage,
                )
                if binding_reason:
                    reason_codes[source_key] = binding_reason
                    if binding_reason in {
                        "workspace-ambiguous",
                        "successor-evidence-integrity-invalid",
                    }:
                        not_evaluable_keys.add(source_key)
                    continue
                exact_tree = previous.get("verified_tree_digest") == tree_digest
                successor_imported[source_key] = json.loads(json.dumps(source))
                _requalify_successor_baseline(
                    source,
                    previous,
                    contract_digest=contract_digest,
                    revision=revision,
                    group_digest=group_digests[source_key],
                    units=group_units[source_key],
                    tree_digest=tree_digest,
                    environment_digest=current_environment_digests[source_key],
                    executable_digest=current_executable_digests[source_key],
                    host_coverage=host_coverage,
                    exact_tree=exact_tree,
                )
                previous_revisions[source_key] = metadata["origin_revision"]
                successor_origins[source_key] = metadata
                if exact_tree:
                    current_requested.add(source_key)
                elif source.get("verified_dependency_provider") in (
                    click_dependency_cache.PROVIDER_NAMES
                ):
                    dependency_candidates.add(source_key)
                elif (
                    click_change_policy.receipt_is_valid(
                        source.get("verified_safe_change_receipt")
                    )
                    and not source.get("verified_dependency_observation")
                ):
                    safe_change_candidates.add(source_key)
                else:
                    reason, not_evaluable = _default_incremental_reason(source)
                    reason_codes[source_key] = reason
                    if not_evaluable:
                        not_evaluable_keys.add(source_key)
            mutation_boundary = verification.get("mutation_boundary")
            if (dependency_candidates or safe_change_candidates) and not (
                isinstance(mutation_boundary, dict)
                and mutation_boundary.get("status") == "recorded"
                and mutation_boundary.get("lineage_valid") is True
                and mutation_boundary.get("revision") == revision
                and mutation_boundary.get("after_root") == git_root
                and mutation_boundary.get("after_digest") == tree_digest
            ):
                # A missing PostToolUse receipt or any later workspace drift is
                # outside the observable approved mutation boundary. Rerun.
                for source_key in dependency_candidates | safe_change_candidates:
                    reason_codes[source_key] = "mutation-boundary-ambiguous"
                    not_evaluable_keys.add(source_key)
                dependency_candidates = set()
                safe_change_candidates = set()
            tree_changed = any(
                isinstance(sources[source_key].get("verified_root"), str)
                and bool(sources[source_key].get("verified_root"))
                and (
                    sources[source_key].get("verified_root") != git_root
                    or sources[source_key].get("verified_tree_digest") != tree_digest
                )
                for source_key in current_requested
            )
            if tree_changed:
                for source_key in (
                    current_requested | dependency_candidates | safe_change_candidates
                ):
                    reason_codes[source_key] = "workspace-ambiguous"
                    not_evaluable_keys.add(source_key)
                revision += 1
                verification["mutation_revision"] = revision
                verification["status"] = "ready"
                verification["verified_revision"] = -1
                verification["failed_revision"] = -1
                verification["workspace_changed"] = True
                for source in sources.values():
                    if not isinstance(source, dict):
                        continue
                    if source.get("status") in {"passed", "observed"}:
                        source["status"] = "stale"
                    else:
                        source["status"] = "ready"
                    source["verified_revision"] = -1
                    source["unchanged_failure_retries"] = 0
                    source["last_exit_code"] = None
                external = state.get("external_evidence")
                browser_required = bool(
                    isinstance(external, dict)
                    and external.get("browser_required") is True
                )
                browser_source_key = (
                    str(external.get("browser_source_key", ""))
                    if isinstance(external, dict)
                    else ""
                )
                state["external_evidence"] = _fresh_external_evidence_state(
                    required=browser_required,
                    source_key=browser_source_key,
                )
                state["observations"] = _fresh_observation_state()
            else:
                for source_key in current_requested:
                    source = sources[source_key]
                    environment_digest = current_environment_digests[source_key]
                    executable_digest = current_executable_digests[source_key]
                    if _verification_receipt_matches(
                        source,
                        contract_digest=contract_digest,
                        revision=revision,
                        group_digest=group_digests[source_key],
                        git_root=git_root,
                        tree_digest=tree_digest,
                        environment_digest=environment_digest,
                        executable_digest=executable_digest,
                        host_coverage=host_coverage,
                    ):
                        reused_keys.add(source_key)
                        if source_key in successor_origins:
                            _mark_successor_reuse(
                                source, successor_origins[source_key], mode="exact"
                            )
                            reason_codes[source_key] = "successor-evidence-current"
                        else:
                            reason_codes[source_key] = (
                                "same-revision-receipt-current"
                            )
                    else:
                        reason_codes[source_key] = _reuse_binding_reason(
                            source,
                            contract_digest=contract_digest,
                            group_digest=group_digests[source_key],
                            git_root=git_root,
                            tree_digest=tree_digest,
                            environment_digest=environment_digest,
                            executable_digest=executable_digest,
                            host_coverage=host_coverage,
                        )
                        source["status"] = "ready"
                        source["verified_revision"] = -1
                        source["last_exit_code"] = None
                candidate_checks = {
                    source_key: grouped_checks[source_key]
                    for source_key in dependency_candidates
                }
                dependency_receipts = (
                    click_dependency_cache.receipts_for_groups(
                        workspace,
                        candidate_checks,
                        declarations=_dependency_declarations(
                            sources, dependency_candidates
                        ),
                        observations=_dependency_observations(
                            sources, dependency_candidates
                        ),
                        git_capture=git_capture,
                    )
                    if candidate_checks
                    else {}
                )
                for source_key in dependency_candidates:
                    source = sources[source_key]
                    receipt = dependency_receipts.get(source_key)
                    environment_digest = current_environment_digests[source_key]
                    executable_digest = current_executable_digests[source_key]
                    if _dependency_receipt_matches(
                        source,
                        receipt,
                        contract_digest=contract_digest,
                        revision=revision,
                        group_digest=group_digests[source_key],
                        git_root=git_root,
                        environment_digest=environment_digest,
                        executable_digest=executable_digest,
                        host_coverage=host_coverage,
                    ):
                        assert isinstance(receipt, dict)
                        _promote_dependency_receipt(
                            source,
                            receipt,
                            revision=revision,
                            tree_digest=tree_digest,
                        )
                        reused_keys.add(source_key)
                        dependency_reused_keys.add(source_key)
                        if source_key in successor_origins:
                            _mark_successor_reuse(
                                source,
                                successor_origins[source_key],
                                mode="dependency",
                            )
                        not_evaluable_keys.discard(source_key)
                        reason_codes[source_key] = (
                            "successor-evidence-dependencies-unchanged"
                            if source_key in successor_origins
                            else "observed-dependencies-unchanged"
                        )
                    else:
                        binding_reason = _reuse_binding_reason(
                            source,
                            contract_digest=contract_digest,
                            group_digest=group_digests[source_key],
                            git_root=git_root,
                            tree_digest=tree_digest,
                            environment_digest=environment_digest,
                            executable_digest=executable_digest,
                            host_coverage=host_coverage,
                            cross_revision=True,
                        )
                        if binding_reason != "receipt-invalid":
                            reason_codes[source_key] = binding_reason
                            not_evaluable_keys.discard(source_key)
                        else:
                            observation_reason = _observation_nonreuse_reason(
                                source.get("verified_dependency_observation")
                            )
                            reason_codes[source_key] = observation_reason
                            if observation_reason in {
                                "observer-incomplete",
                                "external-input-unmodeled",
                            }:
                                not_evaluable_keys.add(source_key)
                for source_key in safe_change_candidates - reused_keys:
                    source = sources[source_key]
                    decision = click_change_policy.decide(
                        workspace,
                        grouped_checks[source_key],
                        source.get("verified_safe_change_receipt"),
                        git_capture=git_capture,
                    )
                    changed_paths = decision.get("changed_paths", [])
                    evidence_id = str(
                        grouped_checks[source_key][0].get("evidence_id", "argv")
                    )
                    rendered_paths = ", ".join(
                        json.dumps(path, ensure_ascii=True)
                        for path in changed_paths[:8]
                        if isinstance(path, str)
                    )
                    if len(changed_paths) > 8:
                        rendered_paths += f", ... (+{len(changed_paths) - 8})"
                    status = str(decision.get("status", "unknown"))
                    reason = str(decision.get("reason", "preflight-unavailable"))
                    if rendered_paths:
                        detail = f" changed paths: {rendered_paths}."
                    elif status == "unknown":
                        detail = " changed paths unavailable."
                    else:
                        detail = " no net changed paths."
                    preflight_advisory = (
                        f"Click preflight [{evidence_id}]: {status} ({reason});{detail}"
                    )
                    environment_digest = current_environment_digests[source_key]
                    executable_digest = current_executable_digests[source_key]
                    safe_change_matches = _safe_change_receipt_matches(
                        source,
                        decision,
                        contract_digest=contract_digest,
                        revision=revision,
                        group_digest=group_digests[source_key],
                        git_root=git_root,
                        environment_digest=environment_digest,
                        executable_digest=executable_digest,
                        host_coverage=host_coverage,
                    )
                    if safe_change_matches:
                        confirmed_snapshot = git_workspace_snapshot(workspace)
                        if not (
                            isinstance(confirmed_snapshot, dict)
                            and os.path.normcase(
                                str(confirmed_snapshot.get("root", ""))
                            )
                            == git_root
                            and confirmed_snapshot.get("digest") == tree_digest
                        ):
                            verification_advisories.append(
                                f"Click preflight [{evidence_id}]: workspace changed "
                                "during preflight; running the real check."
                            )
                            reason_codes[source_key] = "workspace-ambiguous"
                            not_evaluable_keys.add(source_key)
                            continue
                        verification_advisories.append(preflight_advisory)
                        _promote_safe_change_receipt(
                            source,
                            decision,
                            revision=revision,
                            tree_digest=tree_digest,
                        )
                        reused_keys.add(source_key)
                        safe_change_reused_keys.add(source_key)
                        if source_key in successor_origins:
                            _mark_successor_reuse(
                                source,
                                successor_origins[source_key],
                                mode="safe-change",
                            )
                        not_evaluable_keys.discard(source_key)
                        reason_codes[source_key] = (
                            "successor-evidence-safe-change-covered"
                            if source_key in successor_origins
                            else "safe-change-policy-covered"
                        )
                    else:
                        verification_advisories.append(preflight_advisory)
                        binding_reason = _reuse_binding_reason(
                            source,
                            contract_digest=contract_digest,
                            group_digest=group_digests[source_key],
                            git_root=git_root,
                            tree_digest=tree_digest,
                            environment_digest=environment_digest,
                            executable_digest=executable_digest,
                            host_coverage=host_coverage,
                            cross_revision=True,
                        )
                        if binding_reason != "receipt-invalid":
                            reason_codes[source_key] = binding_reason
                        elif status == "unknown":
                            reason_codes[source_key] = "policy-unavailable"
                            not_evaluable_keys.add(source_key)
                        else:
                            reason_codes[source_key] = (
                                "safe-change-policy-not-covered"
                            )

    for source_key, original in successor_imported.items():
        if source_key not in reused_keys:
            sources[source_key].clear()
            sources[source_key].update(original)

    unresolved_keys = {
        key for key in argv_keys if not _evidence_is_current(sources.get(key), revision)
    }
    try:
        incremental_plan = _canonical_incremental_plan(
            sources,
            requested_keys=requested_keys,
            group_digests=group_digests,
            revision=revision,
            previous_revisions=previous_revisions,
            reused_keys=reused_keys,
            dependency_reused_keys=dependency_reused_keys,
            safe_change_reused_keys=safe_change_reused_keys,
            not_evaluable_keys=not_evaluable_keys,
            reason_codes=reason_codes,
        )
        pending_keys = click_incremental.keys_to_execute(incremental_plan)
    except ValueError:
        return "", "Click could not build a valid incremental verification plan.", ""
    repeated_keys = pending_keys - unresolved_keys
    if repeated_keys:
        return (
            "",
            "A verification batch may contain only unresolved declared argv evidence or "
            "an exact reusable success receipt.",
            "",
        )
    click_incremental.store_plan(verification, incremental_plan)
    if measurement is not None:
        measurement["plan"] = incremental_plan
        measurement["labels"] = {
            **click_incremental.sanitize_presentation(state.get("presentation"))["evidence_labels"],
            **{
            key: str(source["shard"]["shard_id"])
            for key, source in sources.items()
            if isinstance(source, dict)
            and click_evidence_shards.source_metadata_is_valid(source.get("shard"))
            },
        }
        measurement["reuse_origins"] = {
            key: successor_origins[key]
            for key in reused_keys
            if key in successor_origins
        }

    if not pending_keys:
        if measurement is not None:
            measurement["all_reused"] = True
        all_argv_current = bool(argv_keys) and all(
            _evidence_is_current(sources.get(source_key), revision)
            for source_key in argv_keys
        )
        verification["status"] = "passed" if all_argv_current else "ready"
        verification["verified_revision"] = revision if all_argv_current else -1
        verification["failed_revision"] = -1
        verification["last_exit_code"] = 0
        verification["last_units"] = 0
        state["verification"] = verification
        _save_contract_state(event, state)
        exact_reused = len(
            reused_keys - dependency_reused_keys - safe_change_reused_keys
        )
        dependency_reused = len(dependency_reused_keys)
        safe_change_reused = len(safe_change_reused_keys)
        reuse_parts: list[str] = []
        if exact_reused:
            reuse_parts.append(f"{exact_reused} current unchanged-tree")
        if dependency_reused:
            reuse_parts.append(
                f"{dependency_reused} dependency-safe cross-revision"
            )
        if safe_change_reused:
            reuse_parts.append(
                f"{safe_change_reused} repository-declared safe-change cross-revision"
            )
        reuse_message = (
            f"Click reused {' and '.join(reuse_parts)} verification receipt(s)"
        )
        return (
            f"echo {reuse_message}",
            "",
            "\n".join(verification_advisories),
        )

    batch = {
        "version": VERIFICATION_PROTOCOL_VERSION,
        "workdir": str(batch["workdir"]),
        "checks": [
            check
            for check in batch["checks"]
            if _evidence_key(str(check["evidence_id"])) in pending_keys
        ],
    }
    units = sum(group_units[source_key] for source_key in pending_keys)
    requested_keys = pending_keys

    if prior_verification_changed_workspace:
        return (
            "",
            "The previous verification changed protected repository content. Use an "
            "approved code mutation to repair or reconcile the workspace before running "
            "verification again.",
            "",
        )

    retried_failed_sources = 0
    for source_key in requested_keys:
        source = sources[source_key]
        source_status = str(source.get("status", "ready"))
        if source_status == "failed":
            retries = int(source.get("unchanged_failure_retries", 0))
            if retries >= 1:
                retried_failed_sources += 1
            source["unchanged_failure_retries"] = retries + 1

    if retried_failed_sources:
        verification_advisories.append(
            "Click advisory: "
            f"{retried_failed_sources} argv evidence source(s) already failed twice "
            "without a subsequent code mutation. This fresh, separately authorized "
            "retry is allowed, but fix the in-scope cause before repeating it when "
            "practical."
        )

    canonical = json.dumps(batch, sort_keys=True, separators=(",", ":"))
    batch_digest = hashlib.sha256(canonical.encode()).hexdigest()
    runner_token = secrets.token_urlsafe(24)
    running_host_coverage_digest = (
        _verification_host_coverage_binding_digest(host_coverage, runner_token)
    )
    running_environment_binding = _verification_environment_binding(
        prepared_environment, runner_token
    )
    running_environment_binding_digest = (
        _verification_environment_binding_digest(
            running_environment_binding, runner_token
        )
    )
    running_environment_digests: dict[str, str] = {}
    running_executable_digests: dict[str, str] = {}
    for source_key in requested_keys:
        executable_records = _verification_executable_records(
            grouped_checks[source_key],
            cwd=workspace,
            environment=prepared_environment,
        )
        if executable_records is None:
            return (
                "",
                "Click could not resolve and fingerprint every verification "
                "executable before issuing the runner.",
                "",
            )
        running_environment_digests[source_key] = (
            _verification_environment_digest_from_records(
                executable_records,
                cwd=workspace,
                environment=prepared_environment,
            )
        )
        running_executable_digests[source_key] = _capability_digest(
            {
                "executables": _verification_executable_payload(
                    executable_records
                )
            }
        )
    shadow_contexts = {
        source_key: {
            "check_digest": group_digests[source_key],
            "environment_digest": running_environment_digests[source_key],
            "executable_digest": running_executable_digests[source_key],
            "host_coverage_digest": str(host_coverage.get("digest", "")),
        }
        for source_key in requested_keys
    }
    shadow_workspace = workspace
    try:
        shadow_snapshot = git_workspace_snapshot(workspace)
        shadow_root = (
            shadow_snapshot.get("root")
            if isinstance(shadow_snapshot, dict)
            else None
        )
        if isinstance(shadow_root, str) and shadow_root:
            shadow_workspace = Path(shadow_root).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        # A Shadow-only root lookup cannot change verification admission.
        shadow_workspace = workspace
    try:
        click_shadow_intelligence.prepare_predictions(
            verification,
            workspace=shadow_workspace,
            source_contexts=shadow_contexts,
            mutation_revision=revision,
        )
        intelligence = verification.get(
            click_shadow_intelligence.SHADOW_INTELLIGENCE_FIELD, {}
        )
        intelligence_sources = (
            intelligence.get("sources", {})
            if isinstance(intelligence, dict)
            else {}
        )
        for source_key in sorted(requested_keys):
            entry = intelligence_sources.get(source_key)
            prediction = entry.get("prediction") if isinstance(entry, dict) else None
            if (
                click_shadow_intelligence.prediction_is_valid(prediction)
                and prediction["decision"] != "not-evaluable"
            ):
                verification_advisories.append(
                    click_shadow_intelligence.advisory(prediction)
                )
    except Exception:
        # Shadow prediction must never affect verification admission.
        pass
    for source_key in requested_keys:
        group_digest = group_digests[source_key]
        source = sources[source_key]
        source["status"] = "running"
        source["last_check_digest"] = group_digest

    verification.update(
        {
            "status": "running",
            "attempts": int(verification.get("attempts", 0)) + 1,
            "last_units": units,
            "last_batch_digest": batch_digest,
            "runner_token_digest": hashlib.sha256(runner_token.encode()).hexdigest(),
            "runner_claimed_at": 0,
            "running_evidence_keys": sorted(requested_keys),
            "running_environment_digests": running_environment_digests,
            "running_environment_binding": running_environment_binding,
            "running_environment_binding_digest": (
                running_environment_binding_digest
            ),
            "running_executable_digests": running_executable_digests,
            "running_host_coverage": host_coverage,
            "running_host_coverage_digest": running_host_coverage_digest,
            "started_at": int(time.time()),
        }
    )
    state["verification"] = verification
    _save_contract_state(event, state)
    return (
        runner_command(
            event,
            batch,
            batch_digest,
            runner_token,
            runner_script=runner_script,
            render_command=render_command,
        ),
        "",
        "\n".join(verification_advisories),
    )


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
    shadow_observer_records: dict[str, dict[str, Any]] | None = None,
    shadow_intelligence_baselines: dict[str, dict[str, Any]] | None = None,
    shadow_source_exit_codes: dict[str, int] | None = None,
    shadow_execution_contexts: dict[str, dict[str, Any]] | None = None,
    *,
    source_results: dict[str, dict[str, Any]] | None = None,
    runner_started_ns: int | None = None,
    git_capture: Callable[[Path, list[str]], bytes | None] = _git_capture,
) -> bool:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    verification = state.get("verification")
    if not isinstance(verification, dict):
        return False
    if verification.get("status") != "running":
        return False
    if verification.get("last_batch_digest") != batch_digest:
        return False
    token_digest = hashlib.sha256(runner_token.encode()).hexdigest()
    if not secrets.compare_digest(
        str(verification.get("runner_token_digest", "")), token_digest
    ):
        return False
    claimed_at = verification.get("runner_claimed_at", 0)
    if (
        not isinstance(claimed_at, int)
        or isinstance(claimed_at, bool)
        or claimed_at <= 0
    ):
        return False
    running_host_coverage = verification.get("running_host_coverage")
    if not _verification_host_coverage_binding_is_authentic(
        running_host_coverage,
        verification.get("running_host_coverage_digest"),
        runner_token,
    ):
        return False

    revision = int(verification.get("mutation_revision", 0))
    claimed_revision = revision
    verification["runner_token_digest"] = ""
    verification["runner_claimed_at"] = 0
    verification["started_at"] = 0
    verification["last_exit_code"] = exit_code
    verification["workspace_changed"] = workspace_changed
    sources = _evidence_sources(state)
    if sources is None or not sources:
        return False
    running_keys = {
        key
        for key in verification.get("running_evidence_keys", [])
        if isinstance(key, str)
    }
    measured_durations = source_durations_ms or {}
    if (
        not isinstance(measured_durations, dict)
        or any(
            not isinstance(source_key, str)
            or source_key not in running_keys
            or not click_incremental.is_duration(duration)
            for source_key, duration in measured_durations.items()
        )
    ):
        return False
    prepared_environment_digests = verification.get(
        "running_environment_digests"
    )
    prepared_executable_digests = verification.get(
        "running_executable_digests"
    )
    for prepared in (
        prepared_environment_digests,
        prepared_executable_digests,
    ):
        if (
            not isinstance(prepared, dict)
            or set(prepared) != running_keys
            or any(
                not isinstance(value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", value)
                for value in prepared.values()
            )
        ):
            return False
    running_environment_binding = verification.get("running_environment_binding")
    if not _verification_environment_binding_is_authentic(
        running_environment_binding,
        verification.get("running_environment_binding_digest"),
        runner_token,
    ):
        return False
    _, _, binding_error = _verification_environment_from_binding(
        running_environment_binding,
        runner_token,
        _verification_environment(cwd=Path.cwd()),
    )
    if binding_error:
        return False
    if (
        environment_digests is not None
        and environment_digests != prepared_environment_digests
    ):
        return False
    environment_digests = prepared_environment_digests
    checks = batch.get("checks")
    if not isinstance(checks, list):
        return False
    positions: dict[str, list[int]] = {}
    for index, check in enumerate(checks):
        if not isinstance(check, dict) or not isinstance(check.get("evidence_id"), str):
            return False
        source_key = _evidence_key(str(check["evidence_id"]))
        positions.setdefault(source_key, []).append(index)
    if set(positions) != running_keys:
        return False
    grouped_checks, grouping_error = _verification_groups(batch)
    if grouping_error or set(grouped_checks) != running_keys:
        return False
    dependency_receipts = (
        click_dependency_cache.receipts_for_groups(
            Path(workspace_root),
            grouped_checks,
            declarations=_dependency_declarations(sources, running_keys),
            observations=dependency_observations,
            git_capture=git_capture,
        )
        if not workspace_changed and workspace_root and workspace_digest
        else {}
    )
    safe_change_receipts = (
        click_change_policy.receipts_for_groups(
            Path(workspace_root),
            grouped_checks,
            git_capture=git_capture,
        )
        if not workspace_changed and workspace_root and workspace_digest
        else {}
    )
    verification["running_evidence_keys"] = []
    verification["running_environment_digests"] = {}
    verification["running_environment_binding"] = []
    verification["running_environment_binding_digest"] = ""
    verification["running_executable_digests"] = {}
    verification["running_host_coverage"] = {}
    verification["running_host_coverage_digest"] = ""
    if workspace_changed:
        previous_revision = revision
        revision += 1
        verification["mutation_revision"] = revision
        verification["status"] = "failed"
        verification["failed_revision"] = revision
        verification["unchanged_failure_retries"] = 1
        state["observations"] = _fresh_observation_state()
        for source_key, source in sources.items():
            if not isinstance(source, dict):
                continue
            check_positions = positions.get(source_key)
            source_ran = bool(
                check_positions
                and (
                    min(check_positions) < succeeded_count
                    or (exit_code != 0 and min(check_positions) == succeeded_count)
                )
            )
            if source_results is not None:
                source_ran = source_results.get(source_key, {}).get("started") is True
            was_current = _evidence_is_current(source, previous_revision)
            if check_positions and source_ran:
                click_evidence.clear_successor_receipt(source)
                source["status"] = "failed"
                source["attempts"] = int(source.get("attempts", 0)) + 1
                source["unchanged_failure_retries"] = 1
                source["last_exit_code"] = exit_code
            elif check_positions:
                # A preceding check stopped the batch before this source executed.
                source["status"] = "ready"
                source["unchanged_failure_retries"] = 0
                source["last_exit_code"] = None
            else:
                source["status"] = "stale" if was_current else "ready"
                source["unchanged_failure_retries"] = 0
                source["last_exit_code"] = None
            source["verified_revision"] = -1
        external = state.get("external_evidence")
        browser_required = bool(
            isinstance(external, dict) and external.get("browser_required") is True
        )
        browser_source_key = (
            str(external.get("browser_source_key", ""))
            if isinstance(external, dict)
            else ""
        )
        state["external_evidence"] = _fresh_external_evidence_state(
            required=browser_required,
            source_key=browser_source_key,
        )
    else:
        for source_key, check_positions in positions.items():
            source = sources.get(source_key)
            if not isinstance(source, dict):
                return False
            first_position = min(check_positions)
            source_ran = first_position < succeeded_count or (
                exit_code != 0 and first_position == succeeded_count
            )
            if source_results is not None:
                source_ran = source_results.get(source_key, {}).get("started") is True
            if source_ran:
                click_evidence.clear_successor_receipt(source)
                source["attempts"] = int(source.get("attempts", 0)) + 1
            if all(position < succeeded_count for position in check_positions):
                source["status"] = "passed"
                source["verified_revision"] = revision
                source["last_exit_code"] = 0
                source["unchanged_failure_retries"] = 0
                source["locked_check_digest"] = str(
                    source.get("last_check_digest", "")
                )
                # Keep the legacy ledger's integer field compatible. Consumers
                # of precise/unknown timing use the separate, validated baseline.
                source["last_success_duration_ms"] = int(measured_durations.get(source_key) or 0)
                batch_id = verification.get(click_incremental.CURRENT_BATCH_FIELD)
                duration_baseline = {
                    "duration_ms": measured_durations.get(source_key),
                    "revision": revision, "check_digest": source.get("last_check_digest"),
                    "observed_at": int(time.time()), "batch_id": batch_id, "sample_count": 1,
                }
                source["last_success_duration_baseline"] = (
                    duration_baseline if click_incremental.baseline_is_valid(duration_baseline) else None
                )
                environment_digest = str(
                    environment_digests.get(source_key, "")
                )
                check_digest = str(source.get("last_check_digest", ""))
                reserved_units = int(source.get("reserved_units", 0))
                if (
                    workspace_root
                    and workspace_digest
                    and environment_digest
                    and check_digest
                ):
                    source["verified_contract_digest"] = str(
                        state.get("contract_digest", "")
                    )
                    source["verified_check_digest"] = check_digest
                    source["verified_units"] = reserved_units
                    source["verified_root"] = os.path.normcase(workspace_root)
                    source["verified_tree_digest"] = workspace_digest
                    source["verified_environment_digest"] = environment_digest
                    source["verified_executable_digest"] = str(
                        prepared_executable_digests.get(source_key, "")
                    )
                    source["verified_host_coverage"] = dict(
                        running_host_coverage
                    )
                    source["verified_at"] = int(time.time())
                    _store_dependency_receipt(
                        source, dependency_receipts.get(source_key)
                    )
                    _store_safe_change_receipt(
                        source, safe_change_receipts.get(source_key)
                    )
                else:
                    source["verified_contract_digest"] = ""
                    source["verified_check_digest"] = ""
                    source["verified_units"] = 0
                    source["verified_root"] = ""
                    source["verified_tree_digest"] = ""
                    source["verified_environment_digest"] = ""
                    source["verified_executable_digest"] = ""
                    source["verified_host_coverage"] = {}
                    source["verified_at"] = 0
                    _clear_dependency_receipt(source)
                    _clear_safe_change_receipt(source)
            elif source_ran:
                source["status"] = "failed"
                source["verified_revision"] = -1
                source["last_exit_code"] = exit_code
            else:
                source["status"] = "ready"
                source["verified_revision"] = -1
                source["last_exit_code"] = None

        argv_keys = _evidence_keys_for_kind(sources, "argv")
        if argv_keys and all(
            _evidence_is_current(sources.get(source_key), revision)
            for source_key in argv_keys
        ):
            verification["status"] = "passed"
            verification["verified_revision"] = revision
            verification["failed_revision"] = -1
            verification["unchanged_failure_retries"] = 0
            verification["locked_batch_digest"] = batch_digest
        else:
            verification["status"] = "failed" if exit_code != 0 else "ready"
            verification["verified_revision"] = -1
            verification["failed_revision"] = revision if exit_code != 0 else -1
    if shadow_observer_records:
        try:
            click_dependency_trace.store_records(
                verification, shadow_observer_records
            )
        except Exception:
            # Shadow telemetry must never change verification authority or its
            # result, including when a future collector returns malformed data.
            pass
        try:
            click_shadow_intelligence.record_run(
                verification,
                observer_records=shadow_observer_records,
                baselines=shadow_intelligence_baselines or {},
                source_exit_codes=shadow_source_exit_codes or {},
                source_contexts=shadow_execution_contexts or {},
                workspace_changed=workspace_changed,
            )
        except Exception:
            # Analysis is telemetry only and cannot change an evidence result.
            pass
    try:
        measured_batch = click_incremental.current_batch(verification)
        reused_keys = {
            item["source_key"] for item in (measured_batch or {}).get("sources", [])
            if item["decision"] in click_incremental.REUSE_DECISIONS
            and _evidence_is_current(sources.get(item["source_key"]), revision)
        }
        click_incremental.record_execution(
            verification, measured_durations, source_results=source_results,
            reused_keys=reused_keys, exit_code=exit_code,
            workspace_changed=workspace_changed,
            runner_duration_ms=(
                (time.perf_counter_ns() - runner_started_ns) / 1_000_000
                if runner_started_ns is not None else None
            ),
        )
    except Exception:
        # Measurement cannot alter evidence authority or the recorded result.
        pass
    if not click_claims.complete_claim(
        state,
        capability="verification",
        claim_mode="one-use-runner",
        request_digest=batch_digest,
        mutation_revision=claimed_revision,
        exit_code=exit_code,
    ):
        return False
    state["verification"] = verification
    state["updated_at"] = int(time.time())
    _write_json(path, state)
    return True


def _claim_verification_run(
    state_path: Path,
    raw: str,
    batch_digest: str,
    runner_token: str,
    *,
    file_content_digest: Callable[[Path], str] = _file_content_digest,
    git_capture: Callable[[Path, list[str]], bytes | None] = _git_capture,
) -> tuple[dict[str, Any] | None, str]:
    """Atomically bind one runner invocation before any check can execute."""
    if not _managed_contract_path(state_path):
        return None, "Click verification runner received an unmanaged state path."
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None, "Click verification runner could not read its contract state."
    if not click_runtime_state.view(state).execution_authorized:
        return None, "Click verification runner is no longer authorized to execute."
    verification = state.get("verification")
    if not isinstance(verification, dict):
        return None, "Click verification runner could not read its approved scale."
    if verification.get("status") != "running":
        return None, "Click verification runner is no longer authorized to execute."
    if verification.get("last_batch_digest") != batch_digest:
        return None, "Click verification runner batch digest did not match active state."
    token_digest = hashlib.sha256(runner_token.encode()).hexdigest()
    if not secrets.compare_digest(
        str(verification.get("runner_token_digest", "")), token_digest
    ):
        return None, "Click verification runner token did not match active state."
    claimed_at = verification.get("runner_claimed_at", 0)
    if not isinstance(claimed_at, int) or isinstance(claimed_at, bool):
        return None, "Click verification runner claim state is malformed."
    if claimed_at:
        return None, "Click verification runner was already claimed; replay is blocked."
    if not _unclaimed_reservation_is_fresh(
        verification.get("started_at", 0), VERIFY_RUNNING_TTL_SECONDS
    ):
        return None, "Click verification runner authorization expired before execution."
    running_host_coverage = verification.get("running_host_coverage")
    if not _verification_host_coverage_binding_is_authentic(
        running_host_coverage,
        verification.get("running_host_coverage_digest"),
        runner_token,
    ):
        return (
            None,
            "Click verification runner host coverage binding was malformed or changed.",
        )

    scale = str(verification.get("scale", ""))
    if not click_verification_policy.is_profile(scale):
        return None, "Click verification runner could not read its approved scale."
    sources = _evidence_sources(state)
    if sources is None or not sources:
        return None, "Click verification runner could not read its evidence ledger."
    batch, _, error = _validate_verification_batch(raw, scale, sources)
    if error:
        return None, error
    assert batch is not None
    if _capability_digest(batch) != batch_digest:
        return None, "Click verification runner batch digest did not match."
    expected_workdir = batch.get("workdir")
    if not isinstance(expected_workdir, str) or not expected_workdir:
        return None, "Click verification runner workdir binding was missing."
    try:
        actual_workdir = Path.cwd().resolve(strict=True)
        prepared_workdir = Path(expected_workdir).resolve(strict=True)
    except (OSError, RuntimeError):
        return None, "Click verification runner could not resolve its working directory."
    if os.path.normcase(str(actual_workdir)) != os.path.normcase(
        str(prepared_workdir)
    ):
        return (
            None,
            "Click verification runner workdir did not match the prepared capability.",
        )
    running_keys = {
        key
        for key in verification.get("running_evidence_keys", [])
        if isinstance(key, str)
    }
    batch_keys = {
        _evidence_key(str(check["evidence_id"])) for check in batch["checks"]
    }
    if not running_keys or batch_keys != running_keys:
        return None, "Click verification runner evidence binding did not match active state."

    grouped_checks, grouping_error = _verification_groups(batch)
    if grouping_error:
        return None, grouping_error
    shard_error = click_evidence_shards.running_plan_error(
        Path.cwd(),
        state.get("evidence_state"),
        grouped_checks,
        git_capture=git_capture,
    )
    if shard_error:
        return None, shard_error
    prepared_environment_digests = verification.get("running_environment_digests")
    prepared_executable_digests = verification.get("running_executable_digests")
    for prepared in (prepared_environment_digests, prepared_executable_digests):
        if (
            not isinstance(prepared, dict)
            or set(prepared) != running_keys
            or any(
                not isinstance(value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", value)
                for value in prepared.values()
            )
        ):
            return None, "Click verification runner context binding was malformed."
    running_environment_binding = verification.get("running_environment_binding")
    if not _verification_environment_binding_is_authentic(
        running_environment_binding,
        verification.get("running_environment_binding_digest"),
        runner_token,
    ):
        return None, "Click verification runner environment binding was malformed."
    verification_environment, environment_rebound, binding_error = (
        _verification_environment_from_binding(
            running_environment_binding,
            runner_token,
            _verification_environment(cwd=Path.cwd()),
        )
    )
    if binding_error:
        return None, binding_error
    assert verification_environment is not None
    shadow_bindings: dict[str, str] = {}
    for source_key, checks in grouped_checks.items():
        source = sources.get(source_key)
        if not isinstance(source, dict):
            return None, "Click verification runner source reservation is unavailable."
        expected_digest = _verification_group_digest(checks)
        shadow_bindings[source_key] = expected_digest
        if source.get("reserved_check_digest") != expected_digest:
            return None, "Click verification runner source reservation did not match."
        executable_records = _verification_executable_records(
            checks,
            cwd=Path.cwd(),
            environment=verification_environment,
            file_content_digest=file_content_digest,
        )
        if executable_records is None:
            return None, "Click verification executable changed before execution."
        current_executable_digest = _capability_digest(
            {
                "executables": _verification_executable_payload(
                    executable_records
                )
            }
        )
        current_environment_digest = _verification_environment_digest_from_records(
            executable_records,
            cwd=Path.cwd(),
            environment=verification_environment,
        )
        if not secrets.compare_digest(
            str(prepared_executable_digests.get(source_key, "")),
            current_executable_digest,
        ):
            return None, "Click verification executable changed before execution."
        if not secrets.compare_digest(
            str(prepared_environment_digests.get(source_key, "")),
            current_environment_digest,
        ):
            prepared_environment_digests[source_key] = current_environment_digest
            environment_rebound = True
        for check, record in zip(checks, executable_records):
            execution_path = record.get("_execution_path")
            if not isinstance(execution_path, str) or not execution_path:
                return None, "Click verification executable binding was malformed."
            approved_argv = list(check["argv"])
            execution_argv = list(approved_argv)
            execution_argv[0] = execution_path
            # Preserve the approved identity privately while keeping the
            # established claim API's executable-pinned argv behavior.
            check["_click_approved_argv"] = approved_argv
            check["argv"] = execution_argv
    claimed_at = int(time.time()) or 1
    _, claim_error = click_claims.record_claim(
        state,
        capability="verification",
        claim_mode="one-use-runner",
        request_digest=batch_digest,
        token_digest=token_digest,
        mutation_revision=int(verification.get("mutation_revision", 0)),
        claimed_at=claimed_at,
    )
    if claim_error:
        return None, claim_error
    verification["runner_claimed_at"] = claimed_at
    verification["running_environment_digests"] = prepared_environment_digests
    state["verification"] = verification
    state["updated_at"] = int(time.time())
    _write_json(state_path, state)
    batch["_click_verification_environment"] = verification_environment
    batch["_click_verification_environment_rebound"] = environment_rebound
    batch["_click_shadow_bindings"] = shadow_bindings
    batch["_click_shadow_contexts"] = {
        source_key: {
            "environment_digest": str(prepared_environment_digests[source_key]),
            "executable_digest": str(prepared_executable_digests[source_key]),
            "host_coverage_digest": str(running_host_coverage.get("digest", "")),
        }
        for source_key in sorted(running_keys)
    }
    batch["_click_mutation_revision"] = int(
        verification.get("mutation_revision", 0)
    )
    batch["_click_observer_mode"] = click_observer_control.mode(verification)
    return batch, ""


def _release_unclaimed_verification_reservation(
    state_path: Path, batch_digest: str, runner_token: str, *,
    runner_duration_ms: float | None = None,
) -> bool:
    """Release one authenticated reservation when admission failed pre-check."""
    if not _managed_contract_path(state_path):
        return False
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    if not click_runtime_state.view(state).execution_authorized:
        return False
    verification = state.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "running":
        return False
    if verification.get("last_batch_digest") != batch_digest:
        return False
    token_digest = hashlib.sha256(runner_token.encode()).hexdigest()
    if not secrets.compare_digest(
        str(verification.get("runner_token_digest", "")), token_digest
    ):
        return False
    claimed_at = verification.get("runner_claimed_at", 0)
    if (
        not isinstance(claimed_at, int)
        or isinstance(claimed_at, bool)
        or claimed_at != 0
    ):
        return False
    sources = _evidence_sources(state)
    if sources is None or not sources:
        return False
    running_keys = verification.get("running_evidence_keys")
    if not isinstance(running_keys, list) or not running_keys:
        return False
    for source_key in running_keys:
        source = sources.get(source_key) if isinstance(source_key, str) else None
        if not isinstance(source, dict) or source.get("status") != "running":
            return False

    for source_key in running_keys:
        source = sources[source_key]
        source["status"] = "ready"
        source["last_exit_code"] = None
    click_incremental.reject_batch(
        verification, reason="runner-admission-rejected",
        runner_duration_ms=runner_duration_ms,
    )
    verification.update(
        {
            "status": "ready",
            "runner_token_digest": "",
            "runner_claimed_at": 0,
            "running_evidence_keys": [],
            "running_environment_digests": {},
            "running_environment_binding": [],
            "running_environment_binding_digest": "",
            "running_executable_digests": {},
            "running_host_coverage": {},
            "running_host_coverage_digest": "",
            "started_at": 0,
            "last_exit_code": None,
        }
    )
    state["verification"] = verification
    state["updated_at"] = int(time.time())
    _write_json(state_path, state)
    return True


def _record_incremental_start(path: Path, digest: str, token: str, source_key: str) -> None:
    """Best-effort live telemetry, authenticated by the already claimed runner."""
    try:
        with _state_lock():
            if not _managed_contract_path(path):
                return
            state = json.loads(path.read_text(encoding="utf-8"))
            verification = state.get("verification", {})
            if (
                verification.get("status") != "running"
                or verification.get("last_batch_digest") != digest
                or not verification.get("runner_claimed_at")
                or not secrets.compare_digest(
                    str(verification.get("runner_token_digest", "")),
                    hashlib.sha256(token.encode()).hexdigest(),
                )
            ):
                return
            if click_incremental.mark_started(verification, source_key):
                _write_json(path, state)
    except Exception:
        pass  # An unavailable telemetry write cannot execute a second command.


def _record_incremental_completion(
    path: Path,
    digest: str,
    token: str,
    source_key: str,
    *,
    status: str,
    reason: str,
    duration_ms: int | float | None,
    completed: bool = True,
) -> None:
    """Persist one source result while the claimed batch is still active."""
    try:
        with _state_lock():
            if not _managed_contract_path(path):
                return
            state = json.loads(path.read_text(encoding="utf-8"))
            verification = state.get("verification", {})
            if (
                verification.get("status") != "running"
                or verification.get("last_batch_digest") != digest
                or not verification.get("runner_claimed_at")
                or not secrets.compare_digest(
                    str(verification.get("runner_token_digest", "")),
                    hashlib.sha256(token.encode()).hexdigest(),
                )
            ):
                return
            if click_incremental.mark_completed(
                verification,
                source_key,
                status=status,
                reason=reason,
                duration_ms=duration_ms,
                completed=completed,
            ):
                state["updated_at"] = int(time.time())
                _write_json(path, state)
    except Exception:
        # Telemetry cannot repeat a check or change evidence authority.
        pass


def _run_verification(
    arguments: list[str],
    *,
    file_content_digest: Callable[[Path], str] = _file_content_digest,
    git_workspace_snapshot: Callable[..., dict[str, Any] | None] = (
        _git_workspace_snapshot
    ),
    git_metadata_present: Callable[[Path | None], bool] = _git_metadata_present,
    execute_commands: Callable[..., int] = _execute_argv_commands,
    git_capture: Callable[[Path, list[str]], bytes | None] = _git_capture,
    shadow_execute: Callable[..., click_dependency_trace.ShadowExecution]
    | None = None,
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
    verification_environment = batch.pop("_click_verification_environment", None)
    if not isinstance(verification_environment, dict):
        sys.stderr.write(
            "Click verification runner lost its prepared environment binding.\n"
        )
        return 2
    environment_rebound = batch.pop(
        "_click_verification_environment_rebound", False
    )
    shadow_bindings = batch.pop("_click_shadow_bindings", {})
    shadow_contexts = batch.pop("_click_shadow_contexts", {})
    shadow_revision = batch.pop("_click_mutation_revision", -1)
    observer_mode = batch.pop("_click_observer_mode", "off")
    shadow_enabled = observer_mode == "shadow"
    active_shadow_execute = shadow_execute or click_dependency_trace.run_command
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
    source_results: dict[str, dict[str, Any]] = {}
    source_completed_commands: dict[str, int] = {}
    per_source_shadow_records: dict[str, list[dict[str, Any]]] = {}
    source_key = ""
    if not snapshot_failed:
        try:
            for index, check in enumerate(checks, start=1):
                argv = check["argv"]
                rendered = (
                    subprocess.list2cmdline(argv)
                    if os.name == "nt"
                    else shlex.join(argv)
                )
                print(
                    f"[Click verification {index}/{len(checks)}:"
                    f"{check['evidence_id']}:{check['class']}] {rendered}",
                    flush=True,
                )
                source_key = _evidence_key(str(check["evidence_id"]))
                def on_target_start() -> None:
                    if source_key not in source_results:
                        source_results[source_key] = {
                            "started": True, "completed": False, "status": "running",
                            "reason_code": "command-started",
                        }
                        _record_incremental_start(state_path, batch_digest, runner_token, source_key)
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
                command_started = time.perf_counter_ns()
                def execute_current() -> int:
                    if can_record_shadow:
                        execute_unobserved = lambda current=argv: execute_commands(
                            [current], environment=verification_environment
                        )
                        observer_compatible = bool(
                            click_inspection.execution_argv(argv) == argv
                            and not click_inspection.is_git_remote_output_request(argv)
                        )
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
                            shadow_result = click_dependency_trace.run_unobserved(
                                execute_unobserved,
                                evidence_key=source_key,
                                check_digest=check_digest,
                                mutation_revision=shadow_revision,
                            )
                        if click_dependency_cache.shadow_observer_record_is_valid(
                            shadow_result.record
                        ):
                            per_source_shadow_records.setdefault(
                                source_key, []
                            ).append(shadow_result.record)
                        print(
                            click_dependency_trace.advisory(shadow_result.record),
                            flush=True,
                        )
                        return shadow_result.exit_code
                    return execute_commands([argv], environment=verification_environment)
                try:
                    with click_process.observe_target_start(on_target_start):
                        exit_code = execute_current()
                    # Injected test executors own their admission boundary; the
                    # production executor reports after successful Popen/resume.
                    if execute_commands is not _execute_argv_commands and source_key not in source_results:
                        on_target_start()
                finally:
                    elapsed_ms = (time.perf_counter_ns() - command_started) / 1_000_000
                    if source_key in source_results:
                        source_durations_ms[source_key] = source_durations_ms.get(source_key, 0) + elapsed_ms
                source_completed_commands[source_key] = source_completed_commands.get(source_key, 0) + 1
                group_completed = source_completed_commands[source_key] == len(grouped_checks[source_key])
                if source_key in source_results:
                    source_results[source_key].update(
                        completed=exit_code != 0 or group_completed,
                        status="interrupted" if exit_code == 130 else "failed" if exit_code != 0 else "passed" if group_completed else "running",
                        reason_code="command-interrupted" if exit_code == 130 else "command-failed" if exit_code != 0 else "command-passed",
                    )
                    if source_results[source_key]["completed"]:
                        _record_incremental_completion(
                            state_path,
                            batch_digest,
                            runner_token,
                            source_key,
                            status=str(source_results[source_key]["status"]),
                            reason=str(source_results[source_key]["reason_code"]),
                            duration_ms=source_durations_ms.get(source_key),
                        )
                if exit_code != 0:
                    break
                succeeded_count += 1
        except KeyboardInterrupt:
            exit_code = 130
            if source_key in source_results:
                source_results[source_key].update(
                    status="interrupted", completed=True, reason_code="command-interrupted"
                )
                _record_incremental_completion(
                    state_path,
                    batch_digest,
                    runner_token,
                    source_key,
                    status="interrupted",
                    reason="command-interrupted",
                    duration_ms=source_durations_ms.get(source_key),
                )
            sys.stderr.write(
                "[Click] Verification was interrupted. The active check was stopped "
                "and recorded as non-passing.\n"
            )
        except Exception:
            exit_code = 2
            if source_key in source_results:
                source_results[source_key].update(
                    status="unknown", completed=False, reason_code="command-error"
                )
                _record_incremental_completion(
                    state_path,
                    batch_digest,
                    runner_token,
                    source_key,
                    status="unknown",
                    reason="command-error",
                    duration_ms=source_durations_ms.get(source_key),
                    completed=False,
                )
            sys.stderr.write("[Click] The command boundary failed; no check was repeated.\n")

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

    combined_shadow_records: dict[str, dict[str, Any]] = {}
    if shadow_enabled and isinstance(shadow_bindings, dict):
        for source_key, records in per_source_shadow_records.items():
            checks_for_source = grouped_checks.get(source_key, [])
            try:
                combined = click_dependency_trace.combine_records(
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
    for source_key, check_positions in (
        {
            key: [
                index
                for index, check in enumerate(checks)
                if click_evidence.evidence_key(str(check["evidence_id"])) == key
            ]
            for key in combined_shadow_records
        }.items()
    ):
        if check_positions and all(position < succeeded_count for position in check_positions):
            shadow_source_exit_codes[source_key] = 0
        elif check_positions and succeeded_count in check_positions and exit_code != 0:
            shadow_source_exit_codes[source_key] = exit_code

    with _state_lock():
        recorded = _record_verification_result(
            state_path,
            batch,
            batch_digest,
            runner_token,
            exit_code,
            succeeded_count,
            workspace_changed=workspace_changed,
            workspace_root=workspace_root if not workspace_changed else "",
            workspace_digest=workspace_digest if not workspace_changed else "",
            source_durations_ms=source_durations_ms,
            source_results=source_results,
            runner_started_ns=runner_started_ns,
            shadow_observer_records=combined_shadow_records,
            shadow_intelligence_baselines=shadow_intelligence_baselines,
            shadow_source_exit_codes=shadow_source_exit_codes,
            shadow_execution_contexts=(
                shadow_contexts if isinstance(shadow_contexts, dict) else {}
            ),
            git_capture=git_capture,
        )
    if not recorded:
        sys.stderr.write("Click could not record the verification result safely.\n")
        return exit_code or 2
    try:
        result_state = json.loads(state_path.read_text(encoding="utf-8"))
        message = click_incremental.host_summary(result_state.get("verification"))
        if message:
            print(message, flush=True)
    except (OSError, ValueError, TypeError):
        pass  # A display failure cannot change the observed verification result.
    return exit_code


prepare = _prepare_verification
record_result = _record_verification_result
claim_run = _claim_verification_run
release_unclaimed_reservation = _release_unclaimed_verification_reservation
run = _run_verification
