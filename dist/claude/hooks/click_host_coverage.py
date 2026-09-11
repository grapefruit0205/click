"""Deterministic host Hook coverage identities for Click.

The registry describes only tool surfaces that a supported host is known to
dispatch to Click.  It does not claim that the host emits an event for every
possible capability.  Verification receipts bind this explicit, limited
surface so cached evidence cannot silently cross hosts or coverage revisions.
"""

from __future__ import annotations

import hashlib
import json
import re


HOST_COVERAGE_VERSION = 1
KNOWN_SURFACES_ASSURANCE = "known-surfaces-only"
HOST_EVENT_OMISSION_LIMITATION = "host-may-omit-events"

CODEX_DIRECT_EXEC_TOOL_NAMES = frozenset(
    {
        "Bash",
        "exec_command",
        "functions.exec_command",
        "shell_command",
        "functions.shell_command",
        "unified_exec",
        "functions.unified_exec",
    }
)
CODEX_CODE_MODE_TOOL_NAMES = frozenset(
    {
        "exec",
        "functions.exec",
        "code_mode_exec",
    }
)
CODEX_MUTATION_TOOL_NAMES = frozenset(
    {
        "Bash",
        "apply_patch",
        "functions.apply_patch",
        "Edit",
        "Write",
        *CODEX_DIRECT_EXEC_TOOL_NAMES,
        *CODEX_CODE_MODE_TOOL_NAMES,
    }
)
CODEX_PLAN_TOOL_NAMES = frozenset({"update_plan", "functions.update_plan"})
CODEX_BROWSER_TOOL_NAMES = frozenset({"mcp__node_repl__js"})
CODEX_TOOL_MAP = {
    tool_name: "Bash"
    for tool_name in CODEX_DIRECT_EXEC_TOOL_NAMES | CODEX_CODE_MODE_TOOL_NAMES
}
CODEX_TOOL_MAP.update(
    {
        "apply_patch": "apply_patch",
        "functions.apply_patch": "functions.apply_patch",
        "Edit": "Edit",
        "Write": "Write",
        "update_plan": "update_plan",
        "functions.update_plan": "functions.update_plan",
        "mcp__node_repl__js": "mcp__node_repl__js",
    }
)

ANTIGRAVITY_TOOL_MAP = {
    "run_command": "Bash",
    "write_to_file": "Write",
    "replace_file_content": "Edit",
    "multi_replace_file_content": "Edit",
    "update_plan": "update_plan",
    "create_plan": "update_plan",
}
ANTIGRAVITY_MUTATION_TOOL_NAMES = frozenset(
    {
        "run_command",
        "write_to_file",
        "replace_file_content",
        "multi_replace_file_content",
    }
)
ANTIGRAVITY_PLAN_TOOL_NAMES = frozenset({"update_plan", "create_plan"})

# Claude Code dispatches Hook events under its native tool names. Bash, Edit
# and Write already match Click's canonical names; the remaining editors map
# onto Edit because Click only records their mutation boundary, never their
# tool-specific arguments. Plan tools stay advisory-only.
CLAUDE_MUTATION_TOOL_NAMES = frozenset(
    {"Bash", "Edit", "Write", "MultiEdit", "NotebookEdit"}
)
CLAUDE_PLAN_TOOL_NAMES = frozenset({"TodoWrite", "ExitPlanMode"})
CLAUDE_TOOL_MAP = {
    "Bash": "Bash",
    "Edit": "Edit",
    "Write": "Write",
    "MultiEdit": "Edit",
    "NotebookEdit": "Edit",
    "TodoWrite": "update_plan",
    "ExitPlanMode": "update_plan",
}


_HOST_SPECS: dict[str, dict[str, object]] = {
    "codex": {
        "assurance": KNOWN_SURFACES_ASSURANCE,
        "limitations": (HOST_EVENT_OMISSION_LIMITATION,),
        "lifecycle": ("UserPromptSubmit", "SessionEnd"),
        "canonical_tool_map": tuple(sorted(CODEX_TOOL_MAP.items())),
        "pre_tool": {
            "mutation": tuple(sorted(CODEX_MUTATION_TOOL_NAMES)),
            "browser": tuple(sorted(CODEX_BROWSER_TOOL_NAMES)),
            "plan": tuple(sorted(CODEX_PLAN_TOOL_NAMES)),
        },
        "post_tool": {
            "mutation": tuple(sorted(CODEX_MUTATION_TOOL_NAMES)),
            "browser": tuple(sorted(CODEX_BROWSER_TOOL_NAMES)),
        },
    },
    "antigravity": {
        "assurance": KNOWN_SURFACES_ASSURANCE,
        "limitations": (HOST_EVENT_OMISSION_LIMITATION,),
        "lifecycle": ("PreInvocation", "Stop"),
        "canonical_tool_map": tuple(sorted(ANTIGRAVITY_TOOL_MAP.items())),
        "pre_tool": {
            "mutation": tuple(sorted(ANTIGRAVITY_MUTATION_TOOL_NAMES)),
            "browser": (),
            "plan": tuple(sorted(ANTIGRAVITY_PLAN_TOOL_NAMES)),
        },
        "post_tool": {
            "mutation": tuple(sorted(ANTIGRAVITY_MUTATION_TOOL_NAMES)),
            "browser": (),
        },
    },
    "claude": {
        "assurance": KNOWN_SURFACES_ASSURANCE,
        "limitations": (HOST_EVENT_OMISSION_LIMITATION,),
        "lifecycle": ("UserPromptSubmit", "SessionEnd"),
        "canonical_tool_map": tuple(sorted(CLAUDE_TOOL_MAP.items())),
        "pre_tool": {
            "mutation": tuple(sorted(CLAUDE_MUTATION_TOOL_NAMES)),
            "browser": (),
            "plan": tuple(sorted(CLAUDE_PLAN_TOOL_NAMES)),
        },
        "post_tool": {
            "mutation": tuple(sorted(CLAUDE_MUTATION_TOOL_NAMES)),
            "browser": (),
        },
    },
}


def host_id_from_event(event: dict[str, object]) -> str:
    """Resolve Click's canonical host id, defaulting legacy Codex events."""
    value = event.get("platform")
    if value is None or value == "":
        return "codex"
    if not isinstance(value, str):
        return ""
    normalized = value.strip().lower()
    return normalized if normalized in _HOST_SPECS else ""


def spec(host_id: str) -> dict[str, object] | None:
    """Return a detached JSON-compatible copy of one registered host surface."""
    registered = _HOST_SPECS.get(host_id)
    if registered is None:
        return None
    return json.loads(json.dumps(registered, sort_keys=True, separators=(",", ":")))


def coverage_digest(host_id: str) -> str:
    """Fingerprint the exact known surface and its explicit assurance limits."""
    registered = _HOST_SPECS.get(host_id)
    if registered is None:
        return ""
    payload = {
        "version": HOST_COVERAGE_VERSION,
        "host": host_id,
        "spec": registered,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def receipt(host_id: str) -> dict[str, object] | None:
    """Return the compact coverage identity bound to verification evidence."""
    registered = _HOST_SPECS.get(host_id)
    if registered is None:
        return None
    return {
        "version": HOST_COVERAGE_VERSION,
        "host": host_id,
        "assurance": registered["assurance"],
        "digest": coverage_digest(host_id),
    }


def receipt_for_event(event: dict[str, object]) -> dict[str, object] | None:
    host_id = host_id_from_event(event)
    return receipt(host_id) if host_id else None


def receipt_is_valid(value: object) -> bool:
    """Validate a receipt's shape without requiring its digest to be current."""
    if not isinstance(value, dict) or set(value) != {
        "version",
        "host",
        "assurance",
        "digest",
    }:
        return False
    host_id = value.get("host")
    registered = _HOST_SPECS.get(host_id) if isinstance(host_id, str) else None
    return bool(
        value.get("version") == HOST_COVERAGE_VERSION
        and registered is not None
        and value.get("assurance") == registered["assurance"]
        and isinstance(value.get("digest"), str)
        and re.fullmatch(r"[0-9a-f]{64}", str(value.get("digest", "")))
    )


def receipt_is_current(value: object) -> bool:
    """Require a valid receipt to match the installed registry exactly."""
    import secrets

    if not receipt_is_valid(value):
        return False
    expected = receipt(str(value["host"]))
    return bool(
        expected is not None
        and secrets.compare_digest(
            str(value.get("digest", "")), str(expected.get("digest", ""))
        )
    )
