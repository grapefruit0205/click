"""Automatic shard plans: Click collects a supported suite and shards it itself.

A committed ``.click/evidence-shards.json`` remains the reviewed, shared
authority. When a broad parent check has no committed plan, Evidence mode
asks the runner's own collection (``click_shard_proposal.propose``) for a
plan and keeps the resulting policy bytes in Click's state, per repository
root and parent check set. The plan is validated exactly like a committed
one on every request: the entry's inventory patterns are matched against the
repository's current file inventory, and every discovered file must be
covered exactly once, so a new or removed test file invalidates the stored
plan and the next preparation regenerates it. Nothing is written into the
repository; ``click-gate sharding init`` still produces the reviewable file.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

if __package__:
    from . import click_import_bootstrap
else:
    import click_import_bootstrap

(click_state,) = click_import_bootstrap.load_siblings(__package__, "click_state")
CONFIG_RELATIVE_PATH = ".click/evidence-shards.json"
MAX_CONFIG_BYTES = 256 * 1024


def _evidence_shards():
    # Loaded on use: click_evidence_shards consults this module while loading
    # committed plans, so neither module may import the other at import time.
    (module,) = click_import_bootstrap.load_siblings(__package__, "click_evidence_shards")
    return module


def _collection():
    # Loaded on use: the proposal generator imports the verification package,
    # which reaches the preparation module that calls into this one.
    return click_import_bootstrap.load_siblings(__package__, "click_test_inventory", "click_shard_proposal")

STORE_VERSION = 1
STORE_DIRECTORY = "automatic-shards"
MAX_PLANS = 16
MAX_STORE_BYTES = 4 * 1024 * 1024
BUDGET_VARIABLE = "CLICK_AUTOMATIC_SHARDS_BUDGET_SECONDS"
DEFAULT_BUDGET_SECONDS = 20.0
QUIET_REASONS = frozenset({"unsupported-command", "project-boundary", "unsupported-runtime"})


def _store_root() -> Path:
    return click_state.state_root().parent / STORE_DIRECTORY


def store_path(root: Path) -> Path:
    canonical = os.path.normcase(str(Path(root).resolve(strict=False)))
    return _store_root() / f"{hashlib.sha256(canonical.encode('utf-8', 'surrogateescape')).hexdigest()}.json"


def _read_store(root: Path) -> dict[str, Any]:
    try:
        path = store_path(root)
        if path.stat().st_size > MAX_STORE_BYTES:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if (
        not isinstance(value, dict)
        or value.get("version") != STORE_VERSION
        or not isinstance(value.get("plans"), dict)
    ):
        return {}
    plans = {
        key: plan for key, plan in value["plans"].items()
        if isinstance(key, str) and isinstance(plan, dict)
        and isinstance(plan.get("policy"), str) and isinstance(plan.get("argv"), list)
    }
    return plans


def _write_store(root: Path, plans: dict[str, Any]) -> None:
    path = store_path(root)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    ordered = sorted(plans.items(), key=lambda item: int(item[1].get("generated_at", 0)))
    payload = {"version": STORE_VERSION, "root": str(root), "plans": dict(ordered[-MAX_PLANS:])}
    click_state.write_json(path, payload)


def policies(root: Path) -> list[bytes]:
    """Every stored automatic policy for this repository root, as manifest bytes."""
    result: list[bytes] = []
    for plan in _read_store(root).values():
        try:
            result.append(plan["policy"].encode("utf-8"))
        except (AttributeError, UnicodeEncodeError):
            continue
    return result


def forget(root: Path, parent_check_digest: str) -> None:
    """Drop a stored plan that no longer validates against the repository."""
    plans = _read_store(root)
    if parent_check_digest in plans:
        del plans[parent_check_digest]
        try:
            _write_store(root, plans)
        except OSError:
            pass


def budget_seconds() -> float:
    raw = os.environ.get(BUDGET_VARIABLE, "").strip()
    try:
        value = float(raw) if raw else DEFAULT_BUDGET_SECONDS
    except ValueError:
        value = DEFAULT_BUDGET_SECONDS
    return value if 1.0 <= value <= 300.0 else DEFAULT_BUDGET_SECONDS


def generate(root: Path, cwd: Path, parent_checks: list[dict[str, Any]]) -> tuple[bytes | None, str]:
    """Collect a supported single-argv parent and store its shard plan.

    Returns the manifest bytes and an empty reason, or None and the reason
    the runner's collection could not produce a plan. Reasons in
    QUIET_REASONS describe commands that are not test runners at all.
    """
    if len(parent_checks) != 1 or not isinstance(parent_checks[0].get("argv"), list):
        return None, "parent-not-single-command"
    argv = [str(item) for item in parent_checks[0]["argv"]]
    click_test_inventory, click_shard_proposal = _collection()
    try:
        root = Path(root).resolve(strict=True)
        cwd = Path(cwd).resolve(strict=True)
        click_test_inventory.parse_command(argv, root, cwd)
    except click_test_inventory.AnalysisError as error:
        return None, str(error) or "unsupported-command"
    except (OSError, RuntimeError, ValueError):
        return None, "project-boundary"
    started = time.monotonic()
    limits = click_test_inventory.Limits(timeout=min(30.0, budget_seconds()))
    try:
        proposal = click_shard_proposal.propose(root, argv, cwd=cwd, limits=limits)
    except Exception:  # noqa: BLE001 - collection is best effort; the parent runs unsharded
        return None, "proposal-unavailable"
    if not proposal.get("proposal_ready"):
        reasons = [str(item) for item in proposal.get("reasons", [])]
        return None, ",".join(reasons) or str(proposal.get("status") or "proposal-unavailable")
    manifest = (proposal.get("proposals") or {}).get(CONFIG_RELATIVE_PATH)
    if not isinstance(manifest, dict):
        return None, "proposal-artifact-invalid"
    shards = [shard for entry in manifest.get("entries", []) for shard in entry.get("shards", [])]
    if len(shards) < 2:
        return None, "single-shard-suite"
    policy = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if len(policy.encode("utf-8")) > MAX_CONFIG_BYTES:
        return None, "proposal-size-limit"
    plans = _read_store(root)
    key = _evidence_shards().group_digest([argv])
    previous = plans.get(key, {})
    plans[key] = {
        "argv": argv,
        "policy": policy,
        "shard_count": len(shards),
        "inventory_digest": str(proposal.get("inventory_digest", "")),
        "generated_at": int(time.time()),
        "generation": int(previous.get("generation", 0)) + 1,
        "analysis_ms": round((time.monotonic() - started) * 1000.0, 1),
    }
    try:
        _write_store(root, plans)
    except OSError:
        return None, "store-unavailable"
    return policy.encode("utf-8"), ""
