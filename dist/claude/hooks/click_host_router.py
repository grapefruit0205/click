#!/usr/bin/env python3
"""Named host-routing boundary for Click Hook adapters.

The router owns no contract, state, evidence, or capability behavior. It binds
the four canonical Hook events to their runtime handlers and provides a scoped
output-capture mechanism for adapters such as Google Antigravity. This keeps
host adapters from reaching through ``click_gate`` private globals.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


Event = dict[str, Any]
EventHandler = Callable[[Event], None]
OutputAdapterSetter = Callable[[Any], Any]
OutputSink = Callable[[dict[str, Any]], None]
OutputSinkSetter = Callable[[OutputSink], OutputSink]


@dataclass(frozen=True)
class HostHandlers:
    pre_tool: EventHandler
    post_tool: EventHandler
    prompt_submit: EventHandler
    session_end: EventHandler


class HostRouter:
    """Dispatch canonical host events through one explicit adapter boundary."""

    def __init__(
        self,
        handlers: HostHandlers,
        *,
        set_output_adapter: OutputAdapterSetter,
        set_output_sink: OutputSinkSetter,
    ) -> None:
        self._handlers = handlers
        self._set_output_adapter = set_output_adapter
        self._set_output_sink = set_output_sink

    def dispatch(self, action: str, event: Event) -> None:
        handlers = {
            "pre-tool": self._handlers.pre_tool,
            "post-tool": self._handlers.post_tool,
            "prompt-submit": self._handlers.prompt_submit,
            "session-end": self._handlers.session_end,
        }
        try:
            handler = handlers[action]
        except KeyError as exc:
            raise ValueError(f"unsupported Click host action: {action}") from exc
        handler(event)

    def capture(
        self, action: str, event: Event, *, output_adapter: Any
    ) -> dict[str, Any]:
        """Run one event with a temporary serializer and in-memory output sink."""

        outputs: list[dict[str, Any]] = []
        previous_adapter = self._set_output_adapter(output_adapter)
        previous_sink = self._set_output_sink(outputs.append)
        try:
            self.dispatch(action, event)
        finally:
            self._set_output_sink(previous_sink)
            self._set_output_adapter(previous_adapter)
        return outputs[-1] if outputs else {}
