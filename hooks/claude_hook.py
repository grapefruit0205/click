#!/usr/bin/env python3
"""Claude Code Hook entrypoint for Click.

Claude Code consumes the same ``hookSpecificOutput`` wire format that Click
already serializes for Codex, so this adapter only normalizes the inbound
event and the outbound ``updatedInput``:

* the host id is stamped as ``claude`` so receipts bind Claude Code's known
  Hook surface instead of the Codex default;
* Click's per-turn identity comes from Claude Code's ``prompt_id``, which is
  issued once per submitted user prompt and repeated on every Hook event that
  prompt causes;
* native editor and plan tools map onto Click's canonical names, leaving the
  contract state machine in ``click_gate.py`` unchanged;
* Claude Code replaces a tool's whole input with ``updatedInput``, so a
  rewritten command is merged back over the original ``tool_input`` rather
  than dropping the fields Click did not touch.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys

if __package__:
    from . import click_import_bootstrap
else:  # Executed directly from the bundled hooks directory.
    import click_import_bootstrap


(click_hook_transport, click_host_coverage) = click_import_bootstrap.load_siblings(
    __package__, "click_hook_transport", "click_host_coverage"
)


HOST_ID = "claude"
CLAUDE_TOOL_MAP = click_host_coverage.CLAUDE_TOOL_MAP
CLAUDE_MUTATION_TOOLS = click_host_coverage.CLAUDE_MUTATION_TOOL_NAMES
CLAUDE_PLAN_TOOLS = click_host_coverage.CLAUDE_PLAN_TOOL_NAMES
TOOL_EVENT_MODES = frozenset({"pre-tool", "post-tool"})


def configure_storage() -> None:
    """Keep Click state under Claude Code's persistent plugin data directory.

    ``CLAUDE_PLUGIN_DATA`` survives plugin updates and is removed with the
    plugin. Outside a plugin install (for example ``--plugin-dir`` testing)
    Claude Code still sets it; the home fallback only covers a direct call.
    """
    configured = os.environ.get("CLAUDE_PLUGIN_DATA", "").strip()
    root = Path(configured) if configured else Path.home() / ".claude" / "click"
    os.environ.setdefault("PLUGIN_DATA", str(root / "plugin-data"))
    os.environ.setdefault("CLICK_CONFIG_HOME", str(root / "config"))


def normalize_event(event: dict[str, object], mode: str) -> dict[str, object]:
    """Translate one Claude Code Hook payload into Click's canonical event."""
    normalized = dict(event)
    normalized["platform"] = HOST_ID
    prompt_id = event.get("prompt_id")
    if isinstance(prompt_id, str) and prompt_id.strip():
        normalized["turn_id"] = prompt_id.strip()
    else:
        # Without a host-issued prompt identity Click must fail closed on
        # anything that needs turn proof; never trust a caller-supplied value.
        normalized.pop("turn_id", None)
    if mode in TOOL_EVENT_MODES:
        tool_name = str(event.get("tool_name", ""))
        canonical = CLAUDE_TOOL_MAP.get(tool_name)
        if canonical is not None:
            normalized["tool_name"] = canonical
    return normalized


def merge_updated_input(
    stdout: str, original_input: object, mode: str
) -> str:
    """Restore untouched tool fields around Click's rewritten ``updatedInput``."""
    if mode != "pre-tool" or not isinstance(original_input, dict):
        return stdout
    stripped = stdout.strip()
    if not stripped.startswith("{"):
        return stdout
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return stdout
    if not isinstance(payload, dict):
        return stdout
    specific = payload.get("hookSpecificOutput")
    if not isinstance(specific, dict):
        return stdout
    updated = specific.get("updatedInput")
    if not isinstance(updated, dict):
        return stdout
    specific["updatedInput"] = {**original_input, **updated}
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def main() -> int:
    configure_storage()
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode not in click_hook_transport.HOOK_MODES:
        (click_gate,) = click_import_bootstrap.load_siblings(__package__, "click_gate")
        return int(click_gate.main())
    raw = sys.stdin.read(click_hook_transport.MAX_EVENT_BYTES + 1)
    if len(raw.encode("utf-8")) > click_hook_transport.MAX_EVENT_BYTES:
        sys.stderr.write("click hook error: Hook input exceeds its size limit\n")
        return 1
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return _one_shot(mode, raw, None)
    if not isinstance(event, dict):
        return _one_shot(mode, raw, None)
    original_input = event.get("tool_input")
    event = normalize_event(event, mode)
    normalized = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    response = click_hook_transport.invoke(mode, event)
    if response is None:
        return _one_shot(mode, normalized, original_input)
    sys.stdout.write(
        merge_updated_input(str(response["stdout"]), original_input, mode)
    )
    sys.stderr.write(str(response["stderr"]))
    return int(response["returncode"])


def _one_shot(mode: str, raw: str, original_input: object) -> int:
    (click_gate,) = click_import_bootstrap.load_siblings(__package__, "click_gate")
    old_argv = sys.argv
    old_stdin = sys.stdin
    old_stdout = sys.stdout
    captured = io.StringIO()
    try:
        sys.argv = [str(Path(__file__).with_name("click_gate.py").resolve()), mode]
        sys.stdin = io.StringIO(raw)
        sys.stdout = captured
        returncode = int(click_gate.main())
    finally:
        sys.argv = old_argv
        sys.stdin = old_stdin
        sys.stdout = old_stdout
    sys.stdout.write(merge_updated_input(captured.getvalue(), original_input, mode))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
