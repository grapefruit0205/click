#!/usr/bin/env python3
"""Contract-state persistence shared by Click runtime domains.

This leaf owns only the canonical contract JSON read, atomic save, and clear
operations.  Contract construction and lifecycle transitions remain in their
own domains.
"""

from __future__ import annotations

import json
import time
from typing import Any

if __package__:
    from . import click_import_bootstrap
else:  # Imported from a directly executed bundled hook.
    import click_import_bootstrap


(click_state, click_incremental) = click_import_bootstrap.load_siblings(
    __package__, "click_state", "click_incremental"
)


EMPTY_CONTRACT_STATE = {"status": "none", "contract_digest": ""}


def history_path_for_contract(path):
    return path.with_name("efficiency-history-" + path.name.removeprefix("session-contract-"))


def _history_path(event: dict[str, Any]):
    return history_path_for_contract(click_state.contract_path(event))


def read_projection_state_path(path) -> dict[str, Any]:
    """Read current state or an authority-free history-only dashboard state."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        value = None
    if isinstance(value, dict):
        return value
    empty = dict(EMPTY_CONTRACT_STATE)
    try:
        archived = json.loads(
            history_path_for_contract(path).read_text(encoding="utf-8")
        )
        verification: dict[str, Any] = {}
        click_incremental.merge_history(archived, verification)
        empty["verification"] = verification
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return empty


def read_contract_state(event: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(
            click_state.contract_path(event).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        empty = dict(EMPTY_CONTRACT_STATE)
        try:
            archived = json.loads(_history_path(event).read_text(encoding="utf-8"))
            verification: dict[str, Any] = {}
            click_incremental.merge_history(archived, verification)
            empty["verification"] = verification
        except (OSError, ValueError, TypeError):
            pass
        return empty
    return value if isinstance(value, dict) else dict(EMPTY_CONTRACT_STATE)


def save_contract_state(event: dict[str, Any], state: dict[str, Any]) -> None:
    verification = state.get("verification")
    if isinstance(verification, dict):
        # Measurements survive a new intent, but carry no evidence authority.
        previous = read_contract_state(event).get("verification")
        click_incremental.merge_history(previous, verification)
    state["updated_at"] = int(time.time())
    click_state.write_json(click_state.contract_path(event), state)


def _clear_contract_state_locked(event: dict[str, Any]) -> None:
    try:
        previous = read_contract_state(event).get("verification")
        if isinstance(previous, dict):
            click_incremental.interrupt_batch(previous)
            archived: dict[str, Any] = {}
            click_incremental.merge_history(previous, archived)
            if archived.get(click_incremental.HISTORY_FIELD):
                click_state.write_json(_history_path(event), archived)
    except (OSError, ValueError, TypeError):
        pass  # An unavailable telemetry archive cannot obstruct cancellation.
    try:
        click_state.contract_path(event).unlink()
    except OSError:
        pass


def clear_contract_state(
    event: dict[str, Any], *, lock_already_held: bool = False
) -> None:
    """Archive measurements and revoke authority without a lost-update window.

    Hook dispatch already owns the global state lock.  Direct callers, including
    tests and future maintenance commands, acquire it here instead.  Keeping the
    two cases explicit avoids attempting to recursively acquire the file lock.
    """
    if lock_already_held:
        _clear_contract_state_locked(event)
        return
    with click_state.state_lock():
        _clear_contract_state_locked(event)
