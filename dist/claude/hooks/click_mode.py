#!/usr/bin/env python3
"""Authority-mode preferences and legacy migration for Click.

This leaf owns per-session compatibility modes and the persistent public
Evidence, Guarded, and Off preference.  It depends only on Click's filesystem
state boundary and never imports lifecycle orchestration, the gate facade, or a
host adapter.
"""

from __future__ import annotations

import json
import time
from typing import Any

if __package__:
    from . import click_import_bootstrap
else:  # Imported from a directly executed bundled hook.
    import click_import_bootstrap


(click_state,) = click_import_bootstrap.load_siblings(__package__, "click_state")


PREFERENCE_SCHEMA_VERSION = 2
PUBLIC_DEFAULT_MODES = {"evidence", "guarded", "off"}
LEGACY_DEFAULT_MODE_ALIASES = {"on": "guarded", "manual": "off"}
DEFAULT_MODES = PUBLIC_DEFAULT_MODES | set(LEGACY_DEFAULT_MODE_ALIASES)


def write_mode(event: dict[str, Any], mode: str) -> None:
    click_state.write_json(
        click_state.mode_path(event),
        {"mode": mode, "updated_at": int(time.time())},
    )


def read_mode(event: dict[str, Any]) -> str:
    try:
        value = json.loads(click_state.mode_path(event).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return "adaptive"
    if isinstance(value, dict) and value.get("mode") in {"adaptive", "strict"}:
        return str(value["mode"])
    return "adaptive"


def write_default_mode(mode: str) -> None:
    if mode not in DEFAULT_MODES:
        raise ValueError(f"unsupported Click default mode: {mode}")
    normalized = LEGACY_DEFAULT_MODE_ALIASES.get(mode, mode)
    click_state.write_json(
        click_state.preference_path(),
        {
            "schema_version": PREFERENCE_SCHEMA_VERSION,
            "default_mode": normalized,
            "migration_notice_pending": False,
            "updated_at": int(time.time()),
        },
    )


def read_default_mode() -> str:
    path = click_state.preference_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return "evidence"
    if not isinstance(value, dict):
        return "evidence"
    stored = value.get("default_mode")
    if (
        value.get("schema_version") == PREFERENCE_SCHEMA_VERSION
        and stored in PUBLIC_DEFAULT_MODES
    ):
        return str(stored)
    if isinstance(stored, str) and stored in DEFAULT_MODES:
        # Preserve the user's prior authority choice while upgrading the public
        # names: Always ON becomes Guarded and Manual becomes Off. An already
        # staged or approved contract is stored separately and remains locked.
        migrated_mode = LEGACY_DEFAULT_MODE_ALIASES.get(stored, stored)
        click_state.write_json(
            path,
            {
                "schema_version": PREFERENCE_SCHEMA_VERSION,
                "default_mode": migrated_mode,
                "migrated_from": stored,
                "migration_notice_pending": True,
                "updated_at": int(time.time()),
            },
        )
        return migrated_mode
    return "evidence"


def consume_migration_notice() -> str:
    path = click_state.preference_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ""
    if not isinstance(value, dict) or value.get("migration_notice_pending") is not True:
        return ""
    migrated_from = str(value.get("migrated_from", ""))
    value["migration_notice_pending"] = False
    value["updated_at"] = int(time.time())
    click_state.write_json(path, value)
    return migrated_from
