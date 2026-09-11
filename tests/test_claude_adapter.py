from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from hooks import claude_hook, click_host_coverage, click_runner_transport


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "hooks" / "claude_hook.py"
PLATFORM = ROOT / "platforms" / "claude"
PROMPT_ID = "550e8400-e29b-41d4-a716-446655440000"
LATER_PROMPT_ID = "550e8400-e29b-41d4-a716-446655440001"


def _runner_tail(command: str) -> list[str]:
    """Return the runner arguments after the interpreter on either host OS.

    POSIX renders ``<python> -c <script> <payload>``; Windows renders the bare
    ``py -3`` launcher with an encoded transport for everything after it.
    """
    if os.name == "nt":
        from hooks import antigravity_gate

        argv = antigravity_gate._command_argv(command)
        if argv[:2] != ["py", "-3"] or argv[3] != "--encoded-runner":
            raise AssertionError(command)
        decoded, error = click_runner_transport.decode_runner_transport(argv[4])
        if error or decoded is None:
            raise AssertionError(error or command)
        return [argv[2], *decoded]
    argv = shlex.split(command)
    if argv[0] != sys.executable:
        raise AssertionError(command)
    return argv[1:]


def _matcher_names(config: dict, event_name: str) -> set[str]:
    names: set[str] = set()
    for entry in config["hooks"][event_name]:
        names.update(entry["matcher"].split("|"))
    return names


class ClaudeHookNormalizationTests(unittest.TestCase):
    def test_prompt_id_becomes_the_turn_identity_and_host_is_stamped(self) -> None:
        event = {
            "session_id": "abc123",
            "prompt_id": PROMPT_ID,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "@Click bypass",
            "turn_id": "spoofed",
            "platform": "codex",
        }
        normalized = claude_hook.normalize_event(event, "prompt-submit")
        self.assertEqual(normalized["platform"], "claude")
        self.assertEqual(normalized["turn_id"], PROMPT_ID)
        self.assertEqual(normalized["prompt"], "@Click bypass")
        self.assertEqual(event["turn_id"], "spoofed")

    def test_missing_prompt_id_never_trusts_a_caller_supplied_turn(self) -> None:
        for missing in ({}, {"prompt_id": ""}, {"prompt_id": "   "}, {"prompt_id": 7}):
            with self.subTest(missing=missing):
                event = {"session_id": "abc123", "turn_id": "spoofed", **missing}
                normalized = claude_hook.normalize_event(event, "pre-tool")
                self.assertNotIn("turn_id", normalized)
                self.assertEqual(normalized["platform"], "claude")

    def test_native_tools_map_onto_the_registered_canonical_names(self) -> None:
        expected = dict(click_host_coverage.spec("claude")["canonical_tool_map"])
        for tool_name, canonical in expected.items():
            for mode in ("pre-tool", "post-tool"):
                with self.subTest(tool_name=tool_name, mode=mode):
                    event = {"tool_name": tool_name, "tool_input": {"x": 1}}
                    normalized = claude_hook.normalize_event(event, mode)
                    self.assertEqual(normalized["tool_name"], canonical)
                    self.assertEqual(normalized["tool_input"], {"x": 1})
        unrelated = {"tool_name": "mcp__server__tool", "tool_input": {}}
        self.assertEqual(
            claude_hook.normalize_event(unrelated, "pre-tool")["tool_name"],
            "mcp__server__tool",
        )
        prompt = {"tool_name": "MultiEdit"}
        self.assertEqual(
            claude_hook.normalize_event(prompt, "prompt-submit")["tool_name"],
            "MultiEdit",
        )

    def test_rewritten_command_is_merged_over_the_original_tool_input(self) -> None:
        original = {
            "command": "click-gate status",
            "description": "Show Click status",
            "timeout": 120000,
            "run_in_background": False,
        }
        stdout = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": {"command": "/usr/bin/python3 runner"},
                }
            }
        )
        merged = json.loads(claude_hook.merge_updated_input(stdout, original, "pre-tool"))
        self.assertEqual(
            merged["hookSpecificOutput"]["updatedInput"],
            {**original, "command": "/usr/bin/python3 runner"},
        )
        self.assertEqual(merged["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_outputs_without_updated_input_pass_through_unchanged(self) -> None:
        deny = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "no",
                }
            }
        )
        context = json.dumps(
            {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "x"}}
        )
        for stdout, original, mode in (
            (deny, {"command": "x"}, "pre-tool"),
            (context, None, "prompt-submit"),
            ("", {"command": "x"}, "pre-tool"),
            ("not json", {"command": "x"}, "pre-tool"),
            ("[1]", {"command": "x"}, "pre-tool"),
            (deny, "not-a-dict", "pre-tool"),
        ):
            with self.subTest(stdout=stdout[:12], mode=mode):
                self.assertEqual(claude_hook.merge_updated_input(stdout, original, mode), stdout)
        with_input = json.dumps(
            {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedInput": {"command": "y"}}}
        )
        self.assertEqual(
            claude_hook.merge_updated_input(with_input, {"command": "x"}, "post-tool"),
            with_input,
        )

    def test_storage_follows_the_persistent_plugin_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            saved = {
                key: os.environ.pop(key, None)
                for key in ("CLAUDE_PLUGIN_DATA", "PLUGIN_DATA", "CLICK_CONFIG_HOME")
            }
            try:
                os.environ["CLAUDE_PLUGIN_DATA"] = temporary
                claude_hook.configure_storage()
                self.assertEqual(os.environ["PLUGIN_DATA"], str(Path(temporary) / "plugin-data"))
                self.assertEqual(os.environ["CLICK_CONFIG_HOME"], str(Path(temporary) / "config"))
                os.environ["CLAUDE_PLUGIN_DATA"] = "/elsewhere"
                claude_hook.configure_storage()
                self.assertEqual(os.environ["PLUGIN_DATA"], str(Path(temporary) / "plugin-data"))
                del os.environ["PLUGIN_DATA"]
                del os.environ["CLICK_CONFIG_HOME"]
                del os.environ["CLAUDE_PLUGIN_DATA"]
                claude_hook.configure_storage()
                self.assertEqual(
                    os.environ["PLUGIN_DATA"],
                    str(Path.home() / ".claude" / "click" / "plugin-data"),
                )
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_adapter_derives_its_tool_identity_from_the_registry(self) -> None:
        self.assertIs(claude_hook.CLAUDE_TOOL_MAP, click_host_coverage.CLAUDE_TOOL_MAP)
        self.assertIs(
            claude_hook.CLAUDE_MUTATION_TOOLS,
            click_host_coverage.CLAUDE_MUTATION_TOOL_NAMES,
        )
        self.assertIs(
            claude_hook.CLAUDE_PLAN_TOOLS, click_host_coverage.CLAUDE_PLAN_TOOL_NAMES
        )
        for tool_name in click_host_coverage.CLAUDE_MUTATION_TOOL_NAMES:
            self.assertIn(
                click_host_coverage.CLAUDE_TOOL_MAP[tool_name], {"Bash", "Edit", "Write"}
            )
        for tool_name in click_host_coverage.CLAUDE_PLAN_TOOL_NAMES:
            self.assertEqual(click_host_coverage.CLAUDE_TOOL_MAP[tool_name], "update_plan")


class ClaudePlatformManifestTests(unittest.TestCase):
    def test_hooks_json_registers_exactly_the_known_surface(self) -> None:
        config = json.loads((PLATFORM / "hooks.json").read_text(encoding="utf-8"))
        spec = click_host_coverage.spec("claude")
        assert spec is not None
        self.assertEqual(
            set(config["hooks"]), set(spec["lifecycle"]) | {"PreToolUse", "PostToolUse"}
        )
        mutation = set(spec["pre_tool"]["mutation"])
        self.assertEqual(
            _matcher_names(config, "PreToolUse"), mutation | set(spec["pre_tool"]["plan"])
        )
        self.assertEqual(_matcher_names(config, "PostToolUse"), mutation)
        self.assertEqual(set(spec["post_tool"]["mutation"]), mutation)
        self.assertEqual(spec["pre_tool"]["browser"], [])
        for event_name, entries in config["hooks"].items():
            for entry in entries:
                for hook in entry["hooks"]:
                    with self.subTest(event=event_name):
                        self.assertEqual(hook["type"], "command")
                        self.assertIn('"${CLAUDE_PLUGIN_ROOT}/hooks/claude_hook.py"', hook["command"])
                        self.assertLessEqual(hook["timeout"], 7)
                        self.assertNotIn("commandWindows", hook)

    def test_plugin_manifest_matches_the_codex_release(self) -> None:
        manifest = json.loads((PLATFORM / "plugin.json").read_text(encoding="utf-8"))
        codex = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "click")
        self.assertEqual(manifest["version"], codex["version"])
        self.assertEqual(manifest["license"], "MIT")
        self.assertNotIn("hooks", manifest)
        self.assertNotIn("skills", manifest)
        marketplace = json.loads(
            (ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        entry = marketplace["plugins"][0]
        self.assertEqual(marketplace["name"], "click")
        self.assertEqual(entry["name"], "click")
        self.assertEqual(entry["source"]["source"], "git-subdir")
        self.assertEqual(entry["source"]["path"], "dist/claude")
        self.assertEqual(entry["source"]["ref"], f"v{codex['version']}")


class ClaudeHookProcessTests(unittest.TestCase):
    """Drive the entrypoint with the payload shapes Claude Code documents."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        (self.workspace / "calc.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.plugin_data = root / "plugin-data"
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PLUGIN_DATA", "CLICK_CONFIG_HOME"}
        }
        self.environment["CLAUDE_PLUGIN_DATA"] = str(self.plugin_data)
        self.environment["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
        self.environment["CLICK_HOOK_WORKER"] = "0"
        self.session_id = "claude-session-1"

    def event(self, hook_event_name: str, prompt_id: str | None = PROMPT_ID, **extra: object) -> dict:
        payload = {
            "session_id": self.session_id,
            "transcript_path": str(Path(self.temporary.name) / "transcript.jsonl"),
            "cwd": str(self.workspace),
            "permission_mode": "default",
            "hook_event_name": hook_event_name,
            **extra,
        }
        if prompt_id is not None:
            payload["prompt_id"] = prompt_id
        return payload

    def hook(self, mode: str, event: dict) -> tuple[int, dict, str]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), mode],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            env=self.environment,
            cwd=str(self.workspace),
            check=False,
        )
        payload = json.loads(result.stdout) if result.stdout else {}
        return result.returncode, payload, result.stderr

    def test_evidence_context_and_rewritten_commands_keep_claude_input_fields(self) -> None:
        code, payload, stderr = self.hook(
            "prompt-submit", self.event("UserPromptSubmit", prompt="Run the tests.")
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("Click Evidence mode is enabled", payload["hookSpecificOutput"]["additionalContext"])

        tool_input = {
            "command": "click-gate status",
            "description": "Show Click status",
            "timeout": 120000,
        }
        code, payload, stderr = self.hook(
            "pre-tool",
            self.event("PreToolUse", tool_name="Bash", tool_input=tool_input, tool_use_id="toolu_1"),
        )
        self.assertEqual((code, stderr), (0, ""))
        specific = payload["hookSpecificOutput"]
        self.assertEqual(specific["permissionDecision"], "allow")
        self.assertEqual(specific["updatedInput"]["description"], "Show Click status")
        self.assertEqual(specific["updatedInput"]["timeout"], 120000)
        self.assertNotEqual(specific["updatedInput"]["command"], "click-gate status")
        tail = _runner_tail(specific["updatedInput"]["command"])
        self.assertEqual(tail[0], "-c")
        self.assertEqual(json.loads(tail[-1])["task"]["runtime_mode"], "evidence")

        code, payload, stderr = self.hook(
            "pre-tool",
            self.event(
                "PreToolUse",
                tool_name="Bash",
                tool_input={"command": "python3 -m pytest -q", "description": "Run tests"},
                tool_use_id="toolu_2",
            ),
        )
        self.assertEqual((code, payload, stderr), (0, {}, ""))
        self.assertTrue((self.plugin_data / "plugin-data" / "gate-state").is_dir())

    def test_native_editors_record_mutation_boundaries(self) -> None:
        self.hook("prompt-submit", self.event("UserPromptSubmit", prompt="Edit calc."))
        for tool_name in ("Write", "MultiEdit", "NotebookEdit"):
            with self.subTest(tool_name=tool_name):
                tool_input = {"file_path": str(self.workspace / "calc.py")}
                code, payload, stderr = self.hook(
                    "pre-tool",
                    self.event("PreToolUse", tool_name=tool_name, tool_input=tool_input, tool_use_id="t"),
                )
                self.assertEqual((code, payload, stderr), (0, {}, ""))
                code, payload, stderr = self.hook(
                    "post-tool",
                    self.event(
                        "PostToolUse",
                        tool_name=tool_name,
                        tool_input=tool_input,
                        tool_response={"filePath": tool_input["file_path"], "type": "update"},
                        tool_use_id="t",
                    ),
                )
                self.assertEqual((code, payload, stderr), (0, {}, ""))
        code, payload, _ = self.hook(
            "pre-tool",
            self.event("PreToolUse", tool_name="Bash", tool_input={"command": "click-gate status"}, tool_use_id="s"),
        )
        self.assertEqual(code, 0)
        status = json.loads(_runner_tail(payload["hookSpecificOutput"]["updatedInput"]["command"])[-1])
        self.assertEqual(status["task"]["mutation_revision"], 3)
        self.assertEqual(status["task"]["runtime_mode"], "evidence")

    def test_bypass_is_bound_to_the_prompt_id_that_authorized_it(self) -> None:
        code, payload, _ = self.hook(
            "prompt-submit", self.event("UserPromptSubmit", prompt="@Click bypass\ncontinue")
        )
        self.assertEqual(code, 0)
        bypass = {"command": "click-gate bypass"}
        code, payload, _ = self.hook(
            "pre-tool",
            self.event("PreToolUse", prompt_id=LATER_PROMPT_ID, tool_name="Bash", tool_input=bypass, tool_use_id="a"),
        )
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")
        code, payload, _ = self.hook(
            "pre-tool",
            self.event("PreToolUse", prompt_id=None, tool_name="Bash", tool_input=bypass, tool_use_id="b"),
        )
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("host turn_id", payload["hookSpecificOutput"]["permissionDecisionReason"])
        code, payload, _ = self.hook(
            "pre-tool",
            self.event("PreToolUse", tool_name="Bash", tool_input=bypass, tool_use_id="c"),
        )
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertEqual(
            payload["hookSpecificOutput"]["updatedInput"],
            {"command": "echo Click bypassed for this turn"},
        )
        code, payload, _ = self.hook(
            "pre-tool",
            self.event("PreToolUse", tool_name="Bash", tool_input=bypass, tool_use_id="d"),
        )
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_plan_tools_and_session_end_stay_silent_in_evidence_mode(self) -> None:
        self.hook("prompt-submit", self.event("UserPromptSubmit", prompt="Plan it."))
        for tool_name in ("TodoWrite", "ExitPlanMode"):
            with self.subTest(tool_name=tool_name):
                code, payload, stderr = self.hook(
                    "pre-tool",
                    self.event("PreToolUse", tool_name=tool_name, tool_input={}, tool_use_id="p"),
                )
                self.assertEqual((code, payload, stderr), (0, {}, ""))
        code, payload, stderr = self.hook("session-end", self.event("SessionEnd", reason="other"))
        self.assertEqual((code, payload, stderr), (0, {}, ""))

    def test_malformed_and_oversized_input_never_grant_a_rewrite(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "pre-tool"],
            input="not json",
            capture_output=True,
            text=True,
            env=self.environment,
            cwd=str(self.workspace),
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("click hook error", result.stderr)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "unsupported-mode"],
            input="{}",
            capture_output=True,
            text=True,
            env=self.environment,
            cwd=str(self.workspace),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
