from __future__ import annotations

import unittest

from hooks import click_gate, click_host_router


class ClickHostRouterTests(unittest.TestCase):
    def test_dispatch_uses_the_named_handler_for_each_host_event(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        def handler(name: str):
            return lambda event: calls.append((name, event))

        router = click_host_router.HostRouter(
            click_host_router.HostHandlers(
                pre_tool=handler("pre-tool"),
                post_tool=handler("post-tool"),
                prompt_submit=handler("prompt-submit"),
                session_end=handler("session-end"),
                session_start=handler("session-start"),
            ),
            set_output_adapter=lambda adapter: adapter,
            set_output_sink=lambda sink: sink,
        )

        actions = ["pre-tool", "post-tool", "prompt-submit", "session-end", "session-start"]
        for action in actions:
            router.dispatch(action, {"action": action})

        self.assertEqual([name for name, _ in calls], actions)

    def test_capture_scopes_adapter_and_sink_then_restores_both(self) -> None:
        active: dict[str, object] = {
            "adapter": "original-adapter",
            "sink": lambda _payload: None,
        }

        def set_adapter(value: object) -> object:
            previous = active["adapter"]
            active["adapter"] = value
            return previous

        def set_sink(value):
            previous = active["sink"]
            active["sink"] = value
            return previous

        def pre_tool(event: dict[str, object]) -> None:
            active["sink"]({"adapter": active["adapter"], **event})

        router = click_host_router.HostRouter(
            click_host_router.HostHandlers(
                pre_tool=pre_tool,
                post_tool=lambda _event: None,
                prompt_submit=lambda _event: None,
                session_end=lambda _event: None,
            ),
            set_output_adapter=set_adapter,
            set_output_sink=set_sink,
        )

        result = router.capture(
            "pre-tool", {"event": "captured"}, output_adapter="temporary"
        )

        self.assertEqual(result, {"adapter": "temporary", "event": "captured"})
        self.assertEqual(active["adapter"], "original-adapter")
        self.assertTrue(callable(active["sink"]))

    def test_gate_exposes_one_stable_host_router(self) -> None:
        self.assertIs(click_gate.host_router(), click_gate.host_router())

    def test_unknown_host_action_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported Click host action"):
            click_gate.host_router().dispatch("unknown", {})


if __name__ == "__main__":
    unittest.main()
