from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import re
import unittest

from hooks import antigravity_gate, click_gate, click_hook, click_host_coverage


ROOT = Path(__file__).parents[1]


def _matchers(config: dict, event_name: str) -> list[re.Pattern[str]]:
    hooks = config.get("hooks", config)
    return [re.compile(entry["matcher"]) for entry in hooks[event_name]]


def _configured_names(matchers: list[re.Pattern[str]]) -> set[str]:
    names: set[str] = set()
    for matcher in matchers:
        pattern = matcher.pattern.removeprefix("^").removesuffix("$")
        if pattern.startswith("(") and pattern.endswith(")"):
            pattern = pattern[1:-1]
        names.update(part.replace(r"\.", ".") for part in pattern.split("|"))
    return names


class ClickHostCoverageTests(unittest.TestCase):
    def test_registry_is_a_stdlib_only_leaf(self) -> None:
        source = Path(click_host_coverage.__file__).read_text(encoding="utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")

        self.assertFalse(
            any(name.startswith("hooks") or "click_" in name for name in imported),
            imported,
        )

    def test_receipts_are_deterministic_host_specific_and_tamper_evident(self) -> None:
        codex = click_host_coverage.receipt("codex")
        antigravity = click_host_coverage.receipt("antigravity")

        self.assertEqual(codex, click_host_coverage.receipt("codex"))
        self.assertNotEqual(codex, antigravity)
        self.assertTrue(click_host_coverage.receipt_is_current(codex))
        self.assertTrue(click_host_coverage.receipt_is_current(antigravity))
        assert codex is not None
        tampered = copy.deepcopy(codex)
        tampered["digest"] = "0" * 64
        self.assertTrue(click_host_coverage.receipt_is_valid(tampered))
        self.assertFalse(click_host_coverage.receipt_is_current(tampered))
        self.assertIsNone(click_host_coverage.receipt("unknown"))
        self.assertIsNone(
            click_host_coverage.receipt_for_event({"platform": "unknown"})
        )

    def test_legacy_events_default_to_codex_but_explicit_hosts_do_not_fall_back(self) -> None:
        self.assertEqual(click_host_coverage.host_id_from_event({}), "codex")
        self.assertEqual(
            click_host_coverage.host_id_from_event({"platform": "Antigravity"}),
            "antigravity",
        )
        self.assertEqual(
            click_host_coverage.host_id_from_event({"platform": 1}), ""
        )

    def test_codex_known_mutation_and_browser_surfaces_have_pre_post_symmetry(self) -> None:
        config = json.loads(
            (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )
        pre = _matchers(config, "PreToolUse")
        post = _matchers(config, "PostToolUse")
        spec = click_host_coverage.spec("codex")
        assert spec is not None

        self.assertEqual(
            set(spec["lifecycle"]), {"UserPromptSubmit", "SessionEnd"}
        )
        self.assertEqual(
            set(config["hooks"]),
            set(spec["lifecycle"]) | {"PreToolUse", "PostToolUse"},
        )

        paired = set(spec["pre_tool"]["mutation"]) | set(
            spec["pre_tool"]["browser"]
        )
        expected_pre = paired | set(spec["pre_tool"]["plan"])
        self.assertEqual(_configured_names(pre), expected_pre)
        self.assertEqual(_configured_names(post), paired)
        self.assertEqual(
            set(dict(spec["canonical_tool_map"])), expected_pre
        )
        self.assertEqual(
            paired,
            set(spec["post_tool"]["mutation"])
            | set(spec["post_tool"]["browser"]),
        )
        for tool_name in paired:
            with self.subTest(tool_name=tool_name):
                self.assertTrue(any(m.fullmatch(tool_name) for m in pre))
                self.assertTrue(any(m.fullmatch(tool_name) for m in post))
        for tool_name in spec["pre_tool"]["plan"]:
            with self.subTest(plan_tool=tool_name):
                self.assertTrue(any(m.fullmatch(tool_name) for m in pre))
                self.assertFalse(any(m.fullmatch(tool_name) for m in post))

    def test_antigravity_known_mutation_surfaces_have_pre_post_symmetry(self) -> None:
        config = json.loads(
            (ROOT / "platforms" / "antigravity" / "hooks.json").read_text(
                encoding="utf-8"
            )
        )["click-tools"]
        pre = _matchers(config, "PreToolUse")
        post = _matchers(config, "PostToolUse")
        spec = click_host_coverage.spec("antigravity")
        assert spec is not None

        context = json.loads(
            (ROOT / "platforms" / "antigravity" / "hooks.json").read_text(
                encoding="utf-8"
            )
        )["click-context"]
        self.assertEqual(set(spec["lifecycle"]), {"PreInvocation", "Stop"})
        self.assertEqual(set(spec["lifecycle"]), set(context))

        self.assertEqual(
            set(spec["pre_tool"]["mutation"]),
            set(spec["post_tool"]["mutation"]),
        )
        expected_pre = set(spec["pre_tool"]["mutation"]) | set(
            spec["pre_tool"]["plan"]
        )
        self.assertEqual(_configured_names(pre), expected_pre)
        self.assertEqual(
            _configured_names(post), set(spec["post_tool"]["mutation"])
        )
        self.assertEqual(
            set(dict(spec["canonical_tool_map"])), expected_pre
        )
        for tool_name in spec["pre_tool"]["mutation"]:
            with self.subTest(tool_name=tool_name):
                self.assertTrue(any(m.fullmatch(tool_name) for m in pre))
                self.assertTrue(any(m.fullmatch(tool_name) for m in post))
        for tool_name in spec["pre_tool"]["plan"]:
            with self.subTest(plan_tool=tool_name):
                self.assertTrue(any(m.fullmatch(tool_name) for m in pre))
                self.assertFalse(any(m.fullmatch(tool_name) for m in post))

    def test_adapters_derive_their_tool_identity_from_the_registry(self) -> None:
        self.assertIs(
            click_gate.BROWSER_TOOL_NAMES,
            click_host_coverage.CODEX_BROWSER_TOOL_NAMES,
        )
        self.assertIs(
            click_hook.DIRECT_EXEC_TOOL_NAMES,
            click_host_coverage.CODEX_DIRECT_EXEC_TOOL_NAMES,
        )
        self.assertIs(
            click_hook.CODE_MODE_TOOL_NAMES,
            click_host_coverage.CODEX_CODE_MODE_TOOL_NAMES,
        )
        for tool_name in (
            click_hook.DIRECT_EXEC_TOOL_NAMES | click_hook.CODE_MODE_TOOL_NAMES
        ):
            self.assertEqual(
                click_host_coverage.CODEX_TOOL_MAP[tool_name], "Bash"
            )
        self.assertIs(
            antigravity_gate.ANTIGRAVITY_TOOL_MAP,
            click_host_coverage.ANTIGRAVITY_TOOL_MAP,
        )
        self.assertIs(
            antigravity_gate.ANTIGRAVITY_MUTATION_TOOLS,
            click_host_coverage.ANTIGRAVITY_MUTATION_TOOL_NAMES,
        )


if __name__ == "__main__":
    unittest.main()
