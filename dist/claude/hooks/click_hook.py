#!/usr/bin/env python3
"""Codex Hook entrypoint that normalizes equivalent tool names for Click.

Codex clients do not always expose shell execution under the historical `Bash`
name. This adapter maps direct shell/exec surfaces onto Click's canonical Bash
path while leaving the core contract state machine unchanged.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
import sys

if __package__:
    from . import click_import_bootstrap
else:  # Executed directly from the bundled hooks directory.
    import click_import_bootstrap


(click_hook_transport, click_host_coverage) = click_import_bootstrap.load_siblings(
    __package__, "click_hook_transport", "click_host_coverage"
)


DIRECT_EXEC_TOOL_NAMES = click_host_coverage.CODEX_DIRECT_EXEC_TOOL_NAMES

# Current Codex Code Mode may not emit Hook events at all for this surface.
# When an event is available, map it onto the conservative command path so an
# active Click workflow fails closed instead of silently bypassing the gate.
CODE_MODE_TOOL_NAMES = click_host_coverage.CODEX_CODE_MODE_TOOL_NAMES


def normalize_event(event: dict[str, object], mode: str) -> dict[str, object]:
    if mode != "pre-tool":
        return event
    tool_name = str(event.get("tool_name", ""))
    if tool_name not in DIRECT_EXEC_TOOL_NAMES | CODE_MODE_TOOL_NAMES:
        return event
    normalized = dict(event)
    normalized["tool_name"] = click_host_coverage.CODEX_TOOL_MAP[tool_name]
    return normalized


def route_stdin_event(mode: str) -> None:
    if mode not in {"pre-tool", "post-tool", "prompt-submit", "session-end"}:
        return
    raw = sys.stdin.read()
    if not raw:
        sys.stdin = io.StringIO(raw)
        return
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        sys.stdin = io.StringIO(raw)
        return
    if isinstance(event, dict):
        event = normalize_event(event, mode)
        raw = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    sys.stdin = io.StringIO(raw)


def main() -> int:
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
        return _one_shot(mode, raw)
    if not isinstance(event, dict):
        return _one_shot(mode, raw)
    event = normalize_event(event, mode)
    normalized = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    response = click_hook_transport.invoke(mode, event)
    if response is None:
        return _one_shot(mode, normalized)
    sys.stdout.write(response["stdout"])
    sys.stderr.write(response["stderr"])
    return int(response["returncode"])


def _one_shot(mode: str, raw: str) -> int:
    (click_gate,) = click_import_bootstrap.load_siblings(__package__, "click_gate")
    old_argv = sys.argv
    old_stdin = sys.stdin
    try:
        sys.argv = [str(Path(__file__).with_name("click_gate.py").resolve()), mode]
        sys.stdin = io.StringIO(raw)
        return int(click_gate.main())
    finally:
        sys.argv = old_argv
        sys.stdin = old_stdin


if __name__ == "__main__":
    raise SystemExit(main())
