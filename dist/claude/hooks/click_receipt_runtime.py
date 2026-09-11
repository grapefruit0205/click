"""Completion-state assembly and offline receipt-envelope verification."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

if __package__:
    from . import (
        click_claims,
        click_evidence,
        click_evidence_shards,
        click_host_coverage,
        click_mutation,
        click_observation,
        click_receipt,
    )
else:  # Executed directly from the bundled hooks directory.
    import click_claims
    import click_evidence
    import click_evidence_shards
    import click_host_coverage
    import click_mutation
    import click_observation
    import click_receipt


MAX_RECEIPT_FILE_BYTES = 1_000_000
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ACTIVE_SERVICE_STATUSES = {"starting", "launching", "running", "stopping"}


def _is_digest(value: Any) -> bool:
    return bool(isinstance(value, str) and DIGEST_PATTERN.fullmatch(value))


def _active_runner_error(state: dict[str, Any]) -> str:
    mutation = state.get("mutation")
    if isinstance(mutation, dict) and mutation.get("status") == "running":
        return "Click cannot export a receipt while a mutation runner is active."
    verification = state.get("verification")
    if isinstance(verification, dict) and verification.get("status") == "running":
        return "Click cannot export a receipt while verification is active."
    observations = state.get("observations")
    entries = observations.get("entries") if isinstance(observations, dict) else None
    if isinstance(entries, dict) and any(
        click_observation.is_running(entry) for entry in entries.values()
    ):
        return "Click cannot export a receipt while an observation runner is active."
    service = state.get("service")
    if isinstance(service, dict) and service.get("status") in ACTIVE_SERVICE_STATUSES:
        return "Click cannot export a receipt while a managed service is active."
    return ""


def _workspace_receipt(
    snapshot: dict[str, Any] | None,
) -> tuple[dict[str, str], str, str]:
    if snapshot is None:
        return {
            "assurance": "unavailable",
            "root_digest": "",
            "tree_digest": "",
        }, "", ""
    root = snapshot.get("root")
    tree_digest = snapshot.get("digest")
    if not isinstance(root, str) or not root or not _is_digest(tree_digest):
        return {}, "", "Click could not establish a valid final workspace receipt."
    normalized_root = os.path.normcase(str(Path(root).resolve()))
    root_digest = hashlib.sha256(normalized_root.encode()).hexdigest()
    return {
        "assurance": "git-protected-tree",
        "root_digest": root_digest,
        "tree_digest": str(tree_digest),
    }, normalized_root, ""


def verified_workspace_root(
    state: dict[str, Any], *, expected_contract_schema_version: int
) -> Path | None:
    """Return the sole canonical Git root bound by all current argv evidence."""

    verification = state.get("verification")
    if not isinstance(verification, dict):
        return None
    revision = verification.get("mutation_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        return None
    sources = click_evidence.sources_from_state(
        state,
        expected_contract_schema_version=expected_contract_schema_version,
    )
    if sources is None or not sources:
        return None

    roots: dict[str, Path] = {}
    for source in sources.values():
        if not isinstance(source, dict) or not click_evidence.is_current(
            source, revision
        ):
            return None
        if source.get("kind") != "argv":
            continue
        stored_root = source.get("verified_root")
        if not isinstance(stored_root, str) or not stored_root:
            return None
        candidate = Path(stored_root)
        if not candidate.is_absolute():
            return None
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if not resolved.is_dir():
            return None
        normalized = os.path.normcase(str(resolved))
        if os.path.normcase(stored_root) != normalized:
            return None
        roots[normalized] = resolved
    if len(roots) != 1:
        return None
    return next(iter(roots.values()))


def _evidence_receipts(
    sources: dict[str, Any],
    *,
    revision: int,
    workspace_root: str,
    workspace_digest: str,
    host_coverage: dict[str, Any],
    receipt_version: int,
) -> tuple[list[dict[str, Any]] | None, str]:
    receipts: list[dict[str, Any]] = []
    for source_key, source in sorted(sources.items()):
        if not isinstance(source, dict) or not click_evidence.is_current(source, revision):
            return None, "Every declared evidence source must be current before receipt export."
        kind = str(source.get("kind", ""))
        completed_at = source.get("verified_at", 0)
        if (
            source.get("last_exit_code") != 0
            or not isinstance(completed_at, int)
            or isinstance(completed_at, bool)
            or completed_at <= 0
        ):
            return None, "Every receipt evidence source must have a timestamped passing result."

        check_digest = ""
        environment_digest = ""
        executable_digest = ""
        if kind == "argv":
            check_digest = str(source.get("verified_check_digest", ""))
            environment_digest = str(source.get("verified_environment_digest", ""))
            executable_digest = str(source.get("verified_executable_digest", ""))
            if not all(
                _is_digest(value)
                for value in (check_digest, environment_digest, executable_digest)
            ):
                return None, "Completed argv evidence is missing a bound runtime fingerprint."
            if (
                not workspace_root
                or os.path.normcase(str(source.get("verified_root", "")))
                != workspace_root
                or source.get("verified_tree_digest") != workspace_digest
            ):
                return None, "The final workspace drifted after argv evidence completed."
            if source.get("verified_host_coverage") != host_coverage:
                return None, "The host coverage identity changed after argv evidence completed."

        if kind == "argv" and int(source.get("successor_reuse_count", 0)) > 0:
            guarded_origin = source.get("last_successor_origin_contract_id", "")
            lineage = {
                "mode": "successor-reused",
                "from_revision": int(
                    source.get("last_successor_origin_revision", -1)
                ),
                # This digest binds the carried candidate, including its
                # authoritative source facts and origin mapping.
                "dependency_digest": str(
                    source.get("last_successor_candidate_digest", "")
                ),
                "origin_batch_id": str(
                    source.get("last_successor_origin_batch_id", "")
                ),
                ("origin_contract_id" if guarded_origin else "origin_evidence_session_id"): str(
                    guarded_origin or source.get("last_successor_origin_evidence_session_id", "")
                ),
                "requalification_mode": str(
                    source.get("last_successor_mode", "")
                ),
            }
        elif kind == "argv" and int(source.get("safe_change_reuse_count", 0)) > 0:
            lineage = {
                "mode": "dependency-reused",
                "from_revision": int(
                    source.get("last_safe_change_reused_from_revision", -1)
                ),
                "dependency_digest": str(
                    source.get("last_safe_change_decision_digest", "")
                ),
            }
        elif kind == "argv" and int(source.get("dependency_reuse_count", 0)) > 0:
            lineage = {
                "mode": "dependency-reused",
                "from_revision": int(
                    source.get("last_dependency_reused_from_revision", -1)
                ),
                "dependency_digest": str(
                    source.get("verified_dependency_digest", "")
                ),
            }
        else:
            modes = {
                "argv": "executed",
                "browser": "browser-observed",
                "hosted": "attested",
                "manual": "attested",
                "existing": "attested",
            }
            if kind not in modes:
                return None, "Receipt evidence contains an unsupported kind."
            lineage = {
                "mode": modes[kind],
                "from_revision": revision,
                "dependency_digest": "",
            }
        receipt_source = {
                "source_key": source_key,
                "kind": kind,
                "verified_revision": revision,
                "check_digest": check_digest,
                "environment_digest": environment_digest,
                "executable_digest": executable_digest,
                "result": {
                    "status": "passed",
                    "exit_code": 0,
                    "completed_at": completed_at,
                },
                "lineage": lineage,
            }
        if receipt_version in {
            click_receipt.SHARD_RECEIPT_VERSION,
            click_receipt.SUCCESSOR_RECEIPT_VERSION,
            click_receipt.GUARDED_SUCCESSOR_RECEIPT_VERSION,
        }:
            metadata = source.get("shard")
            receipt_source["shard"] = (
                {
                    field: metadata[field]
                    for field in click_receipt.SHARD_FIELDS
                }
                if click_evidence_shards.source_metadata_is_valid(metadata)
                else None
            )
        receipts.append(receipt_source)
    if not receipts:
        return None, "Click cannot export a receipt without declared completion evidence."
    return receipts, ""


def build_envelope(
    state: dict[str, Any],
    *,
    workspace_snapshot: dict[str, Any] | None,
    host_coverage: dict[str, Any] | None,
    expected_contract_schema_version: int,
) -> tuple[dict[str, Any] | None, str]:
    """Build one receipt from completed state and a just-captured workspace."""
    status = state.get("status")
    if status not in {"approved", "evidence"}:
        return None, "Click receipt export requires completed Guarded or Evidence state."
    active_error = _active_runner_error(state)
    if active_error:
        return None, active_error
    verification = state.get("verification")
    if not isinstance(verification, dict):
        return None, "Click receipt export could not read verification state."
    revision = verification.get("mutation_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        return None, "Click receipt export found an invalid mutation revision."
    sources = click_evidence.sources_from_state(
        state,
        expected_contract_schema_version=expected_contract_schema_version,
    )
    if sources is None or not sources:
        return None, "Click receipt export requires the current evidence ledger."
    if any(not click_evidence.is_current(source, revision) for source in sources.values()):
        return None, "Every declared evidence source must be current before receipt export."
    if not click_host_coverage.receipt_is_current(host_coverage):
        return None, "Click receipt export found an unavailable or changed host coverage identity."
    assert isinstance(host_coverage, dict)

    workspace, normalized_root, error = _workspace_receipt(workspace_snapshot)
    if error:
        return None, error
    has_successor = any(
        isinstance(source, dict)
        and int(source.get("successor_reuse_count", 0)) > 0
        for source in sources.values()
    )
    receipt_version = (
        click_receipt.GUARDED_SUCCESSOR_RECEIPT_VERSION
        if has_successor and any(source.get("last_successor_origin_contract_id") for source in sources.values())
        else click_receipt.SUCCESSOR_RECEIPT_VERSION
        if has_successor
        else click_receipt.SHARD_RECEIPT_VERSION
        if any(
            click_evidence_shards.source_metadata_is_valid(source.get("shard"))
            for source in sources.values()
            if isinstance(source, dict)
        )
        else click_receipt.RECEIPT_VERSION
    )
    evidence, error = _evidence_receipts(
        sources,
        revision=revision,
        workspace_root=normalized_root,
        workspace_digest=str(workspace.get("tree_digest", "")),
        host_coverage=host_coverage,
        receipt_version=receipt_version,
    )
    if error:
        return None, error
    assert evidence is not None
    capabilities, error = click_claims.receipt_entries(
        state, settle_through_revision=revision
    )
    if error:
        return None, error
    assert capabilities is not None

    excluded = list(click_receipt.BASE_COVERAGE_EXCLUSIONS)
    if workspace["assurance"] == "unavailable":
        excluded.append(click_receipt.UNAVAILABLE_TREE_EXCLUSION)
    guarded = status == "approved"
    contract = (
        {
            "id": state.get("contract_id"),
            "digest": state.get("contract_digest"),
            "staged_turn_id": state.get("staged_turn_id"),
            "approved_turn_id": state.get("approved_turn_id"),
        }
        if guarded
        else None
    )
    intent_digest = state.get("intent_digest", state.get("contract_digest"))
    intent_turn_id = state.get("intent_turn_id", state.get("staged_turn_id"))
    body = {
        "version": receipt_version,
        "authority": {
            "mode": "guarded" if guarded else "evidence",
            "approval_bound": guarded,
            "execution_authority": "click-contract" if guarded else "host",
            "intent_digest": intent_digest,
            "intent_turn_id": intent_turn_id,
            "follow_up_turns": state.get("follow_up_turns", []),
            "history_complete": state.get("history_complete", True),
        },
        "contract": contract,
        "execution": {
            "mutation_revision": revision,
            "workspace": workspace,
        },
        "capabilities": capabilities,
        "evidence": evidence,
        "coverage": {
            "host_assurance": host_coverage.get("assurance"),
            "host_coverage_digest": host_coverage.get("digest"),
            "excluded": excluded,
        },
    }
    return click_receipt.create_envelope(body)


def render_envelope(envelope: dict[str, Any]) -> str:
    return json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def verify_file(path: Path) -> tuple[dict[str, Any] | None, str]:
    """Verify one local unsigned receipt envelope without network or Click state."""
    try:
        if path.is_symlink() or not path.is_file():
            return None, "Completion receipt path must name one regular non-symlink file."
        if path.stat().st_size > MAX_RECEIPT_FILE_BYTES:
            return None, "Completion receipt file exceeds the bounded parser size."
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw)
    except UnicodeDecodeError:
        return None, "Completion receipt file must be UTF-8 JSON."
    except json.JSONDecodeError:
        return None, "Completion receipt file must contain valid JSON."
    except OSError as exc:
        return None, f"Completion receipt file could not be read: {exc}."
    normalized, error = click_receipt.validate_envelope(value)
    if error:
        return None, error
    assert normalized is not None
    return {
        "status": "valid",
        "assurance": click_receipt.UNSIGNED_ASSURANCE,
        "receipt_digest": normalized["receipt_digest"],
        "version": click_receipt.ENVELOPE_VERSION,
    }, ""
