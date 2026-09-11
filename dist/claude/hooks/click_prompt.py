#!/usr/bin/env python3
"""Prompt lineage and per-turn authorization state for Click.

This leaf owns prompt digests, exact first-line bypass/cancel recognition,
one-use authorization consumption, active-turn validation, and follow-up turn
lineage.  It depends only on Click's filesystem state boundary and never
imports lifecycle orchestration, the gate facade, or a host adapter.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

if __package__:
    from . import click_import_bootstrap
else:  # Imported from a directly executed bundled hook.
    import click_import_bootstrap


(click_state,) = click_import_bootstrap.load_siblings(__package__, "click_state")


CLICK_AUTHORIZATION_PATTERNS = (
    re.compile(r"(?i:@click)[ \t]+(?P<action>(?i:bypass|cancel))"),
    re.compile(
        r"\[(?i:@click)\]\(plugin://click@click\)[ \t]+"
        r"(?P<action>(?i:bypass|cancel))"
    ),
)


def prompt_digest(value: Any) -> str:
    text = value if isinstance(value, str) else ""
    return hashlib.sha256(text.encode()).hexdigest()


def prompt_authorization(prompt: Any) -> str:
    if not isinstance(prompt, str) or not prompt:
        return ""
    first_line = prompt.splitlines()[0].strip() if prompt.splitlines() else ""
    for pattern in CLICK_AUTHORIZATION_PATTERNS:
        match = pattern.fullmatch(first_line)
        if match:
            return match.group("action").lower()
    return ""


def record_user_prompt(event: dict[str, Any]) -> str:
    turn_id = str(event.get("turn_id", ""))
    if not turn_id:
        raise ValueError("Click requires the host turn_id on UserPromptSubmit")
    authorization = prompt_authorization(event.get("prompt", ""))
    click_state.write_json(
        click_state.prompt_path(event),
        {
            "turn_id": turn_id,
            "authorization": authorization,
            "prompt_digest": prompt_digest(event.get("prompt", "")),
            "updated_at": int(time.time()),
        },
    )
    return authorization


def read_user_prompt_state(event: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(click_state.prompt_path(event).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def read_user_prompt_turn(event: dict[str, Any]) -> str:
    return str(read_user_prompt_state(event).get("turn_id", ""))


def consume_user_authorization(event: dict[str, Any], expected: str) -> str:
    turn_id = str(event.get("turn_id", ""))
    if not turn_id:
        return f"Click {expected} requires a current host turn_id."
    state = read_user_prompt_state(event)
    if str(state.get("turn_id", "")) != turn_id:
        return (
            f"Click {expected} requires a recognized first-line Click directive "
            f"(`@Click {expected}`) in this user turn."
        )
    if state.get("authorization") != expected:
        return (
            f"Click {expected} requires a recognized first-line Click directive "
            f"(`@Click {expected}`) in this user turn."
        )
    state["authorization"] = ""
    state["updated_at"] = int(time.time())
    click_state.write_json(click_state.prompt_path(event), state)
    return ""


def active_prompt_turn_error(event: dict[str, Any]) -> str:
    turn_id = str(event.get("turn_id", ""))
    if not turn_id:
        return "Click cannot prove approval because this tool call has no host turn_id."
    if read_user_prompt_turn(event) != turn_id:
        return (
            "Click can stage or approve a contract only in a turn that began with a "
            "UserPromptSubmit event. Ask the user to respond, then retry in that turn."
        )
    return ""


def append_follow_up(event: dict[str, Any], state: dict[str, Any]) -> bool:
    prompt = read_user_prompt_state(event)
    turn_id = str(prompt.get("turn_id", ""))
    digest = str(prompt.get("prompt_digest", ""))
    if (
        not turn_id
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or turn_id
        in {
            str(state.get("intent_turn_id", "")),
            str(state.get("staged_turn_id", "")),
            str(state.get("approved_turn_id", "")),
        }
    ):
        return False
    entries = state.get("follow_up_turns")
    if not isinstance(entries, list):
        entries = []
    if any(
        isinstance(item, dict) and item.get("turn_id") == turn_id
        for item in entries
    ):
        return False
    entries.append({"turn_id": turn_id, "digest": digest})
    state["follow_up_turns"] = entries
    return True
