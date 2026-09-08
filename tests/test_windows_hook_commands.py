from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
HOOK_CONFIG = ROOT / "hooks" / "hooks.json"
WINDOWS_MODES = {
    "UserPromptSubmit": "prompt-submit",
    "PreToolUse": "pre-tool",
    "PostToolUse": "post-tool",
    "SessionEnd": "session-end",
}
WINDOWS_RUNNER_PREFIX = "powershell.exe -NoProfile -NonInteractive -Command "
DESKTOP_EXEC_NAMES = {
    "Bash",
    "shell_command",
    "functions.shell_command",
    "exec_command",
    "functions.exec_command",
    "unified_exec",
    "functions.unified_exec",
    "exec",
    "functions.exec",
    "code_mode_exec",
}


def hook_config() -> dict[str, object]:
    return json.loads(HOOK_CONFIG.read_text(encoding="utf-8"))


def windows_commands() -> dict[str, str]:
    config = hook_config()
    hooks = config["hooks"]
    assert isinstance(hooks, dict)
    return {
        event_name: hooks[event_name][0]["hooks"][0]["commandWindows"]
        for event_name in WINDOWS_MODES
    }


def synthetic_event(
    event_name: str,
    workspace: Path,
    suffix: str,
    *,
    tool_name: str = "Bash",
) -> dict[str, object]:
    event: dict[str, object] = {
        "session_id": f"windows-hook-{suffix}",
        "turn_id": f"windows-turn-{suffix}",
        "cwd": str(workspace),
        "model": "test-model",
        "permission_mode": "default",
        "hook_event_name": event_name,
    }
    if event_name == "UserPromptSubmit":
        event["prompt"] = "Explain the current Click mode."
    elif event_name == "PreToolUse":
        event.update(
            {
                "tool_name": tool_name,
                "tool_use_id": f"windows-tool-{suffix}",
                "tool_input": {"command": "click-gate default status"},
            }
        )
    elif event_name == "PostToolUse":
        event.update(
            {
                "tool_name": "mcp__node_repl__js",
                "tool_use_id": f"windows-tool-{suffix}",
                "tool_input": {"code": "return true;"},
                "tool_response": {"content": []},
            }
        )
    return event


def rendered_windows_command(command: str, plugin_root: Path) -> str:
    return command.replace("${PLUGIN_ROOT}", str(plugin_root))


class WindowsHookCommandTests(unittest.TestCase):
    def test_commands_use_direct_batch_launcher_and_desktop_matchers(self) -> None:
        config = hook_config()
        hooks = config["hooks"]
        assert isinstance(hooks, dict)
        commands = windows_commands()
        self.assertEqual(set(commands), set(WINDOWS_MODES))
        for event_name, mode in WINDOWS_MODES.items():
            with self.subTest(event=event_name):
                self.assertEqual(
                    commands[event_name],
                    f'"${{PLUGIN_ROOT}}\\hooks\\click_windows.cmd" {mode}',
                )
                self.assertNotIn("powershell", commands[event_name].lower())
                self.assertNotIn("py -3", commands[event_name].lower())
                command = hooks[event_name][0]["hooks"][0]["command"]
                self.assertIn("click_hook.py", command)

        matchers = [
            re.compile(entry["matcher"])
            for entry in hooks["PreToolUse"]
        ]
        for name in DESKTOP_EXEC_NAMES | {
            "apply_patch",
            "functions.apply_patch",
            "update_plan",
            "functions.update_plan",
            "mcp__node_repl__js",
        }:
            with self.subTest(matcher=name):
                self.assertTrue(
                    any(compiled.fullmatch(name) for compiled in matchers),
                    name,
                )
        desktop_handler = hooks["PreToolUse"][1]["hooks"][0]
        self.assertIn("click_hook.py", desktop_handler["command"])

        launcher = (ROOT / "hooks" / "click_windows.cmd").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('-c "import sys', launcher)
        self.assertEqual(launcher.count('click_windows.py" %*'), 3)

    @unittest.skipUnless(os.name == "nt", "Windows cmd integration test")
    def test_hooks_execute_via_cmd_and_exec_aliases_rewrite(self) -> None:
        command_prompt = shutil.which("cmd.exe")
        self.assertIsNotNone(command_prompt, "cmd.exe is required on Windows")
        powershell = shutil.which("pwsh")
        self.assertIsNotNone(powershell, "pwsh is required on the Windows CI runner")
        commands = windows_commands()

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for root_name in ("click-normal", "Click Plugin Root With Spaces"):
                with self.subTest(plugin_root=root_name):
                    plugin_root = base / root_name
                    shutil.copytree(
                        ROOT / "hooks",
                        plugin_root / "hooks",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                    )
                    plugin_data = plugin_root / "plugin data"
                    workspace = plugin_root / "workspace"
                    workspace.mkdir()
                    environment = os.environ.copy()
                    environment.update(
                        {
                            "PLUGIN_ROOT": str(plugin_root),
                            "PLUGIN_DATA": str(plugin_data),
                            "CLICK_CONFIG_HOME": str(plugin_data),
                        }
                    )

                    def run_hook(event_name: str, event: dict[str, object]) -> subprocess.CompletedProcess[str]:
                        rendered = rendered_windows_command(commands[event_name], plugin_root)
                        self.assertNotIn("PLUGIN_ROOT", rendered)
                        return subprocess.run(
                            rendered,
                            shell=True,
                            executable=command_prompt,
                            input=json.dumps(event) + "\n",
                            capture_output=True,
                            text=True,
                            cwd=workspace,
                            env=environment,
                            check=False,
                        )

                    prompt_result = run_hook(
                        "UserPromptSubmit",
                        synthetic_event("UserPromptSubmit", workspace, root_name),
                    )
                    self.assertEqual(
                        prompt_result.returncode,
                        0,
                        f"prompt hook failed:\n{prompt_result.stderr}\n{prompt_result.stdout}",
                    )
                    prompt_payload = json.loads(prompt_result.stdout)
                    self.assertTrue(
                        prompt_payload["hookSpecificOutput"]["additionalContext"]
                    )

                    for alias in ("Bash", "exec_command", "functions.exec_command", "shell_command"):
                        with self.subTest(plugin_root=root_name, alias=alias):
                            event = synthetic_event(
                                "PreToolUse",
                                workspace,
                                f"{root_name}-{alias}".replace(" ", "-"),
                                tool_name=alias,
                            )
                            result = run_hook("PreToolUse", event)
                            self.assertEqual(
                                result.returncode,
                                0,
                                f"{alias} hook failed:\n{result.stderr}\n{result.stdout}",
                            )
                            payload = json.loads(result.stdout)
                            updated = payload["hookSpecificOutput"]["updatedInput"]
                            self.assertIn("Click default mode:", updated["command"])

                    probe = workspace / "runner probe.txt"
                    probe.write_text("portable Windows runner\n", encoding="utf-8")
                    request = {
                        "version": 1,
                        "commands": [["Get-Content", "-Raw", probe.name]],
                    }
                    inspect_event = synthetic_event(
                        "PreToolUse",
                        workspace,
                        f"{root_name}-inspect".replace(" ", "-"),
                        tool_name="functions.exec_command",
                    )
                    inspect_event["tool_input"] = {
                        "command": (
                            "click-gate inspect '"
                            + json.dumps(request, separators=(",", ":"))
                            + "'"
                        )
                    }
                    hook_result = run_hook("PreToolUse", inspect_event)
                    self.assertEqual(
                        hook_result.returncode,
                        0,
                        f"inspect hook failed:\n{hook_result.stderr}\n{hook_result.stdout}",
                    )
                    payload = json.loads(hook_result.stdout)
                    runner = payload["hookSpecificOutput"]["updatedInput"]["command"]
                    self.assertTrue(runner.startswith(WINDOWS_RUNNER_PREFIX), runner)
                    self.assertNotIn("py -3", runner.lower())

                    for shell_name in ("PowerShell", "cmd.exe"):
                        with self.subTest(plugin_root=root_name, runner_shell=shell_name):
                            if shell_name == "PowerShell":
                                invocation: str | list[str] = [
                                    powershell,
                                    "-NoProfile",
                                    "-NonInteractive",
                                    "-Command",
                                    runner,
                                ]
                                shell_options: dict[str, object] = {}
                            else:
                                invocation = runner
                                shell_options = {
                                    "shell": True,
                                    "executable": command_prompt,
                                }
                            executed = subprocess.run(
                                invocation,
                                capture_output=True,
                                text=True,
                                cwd=workspace,
                                env=environment,
                                check=False,
                                **shell_options,
                            )
                            self.assertEqual(
                                executed.returncode,
                                0,
                                f"{shell_name} runner failed:\n{executed.stderr}\n{executed.stdout}",
                            )
                            self.assertEqual(executed.stdout, "portable Windows runner\n")

    @unittest.skipUnless(os.name == "nt", "Windows Python launcher fallback test")
    def test_broken_py_launcher_falls_back_to_python(self) -> None:
        command_prompt = shutil.which("cmd.exe")
        self.assertIsNotNone(command_prompt, "cmd.exe is required on Windows")
        real_python = shutil.which("python")
        self.assertIsNotNone(real_python, "python.exe is required on Windows")
        commands = windows_commands()

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            plugin_root = base / "Click Fallback Root With Spaces"
            shutil.copytree(
                ROOT / "hooks",
                plugin_root / "hooks",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            workspace = plugin_root / "workspace"
            workspace.mkdir()
            plugin_data = plugin_root / "plugin data"
            fake_bin = base / "broken py launcher"
            fake_bin.mkdir()
            launcher_log = base / "launcher.log"
            (fake_bin / "py.cmd").write_text(
                "@echo off\r\n"
                ">>\"%CLICK_TEST_LAUNCHER_LOG%\" echo py\r\n"
                ">&2 echo No installed Python found!\r\n"
                "exit /b 103\r\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "PLUGIN_ROOT": str(plugin_root),
                    "PLUGIN_DATA": str(plugin_data),
                    "CLICK_CONFIG_HOME": str(plugin_data),
                    "CLICK_TEST_LAUNCHER_LOG": str(launcher_log),
                    "PATH": str(fake_bin) + os.pathsep + environment.get("PATH", ""),
                }
            )
            event = synthetic_event(
                "PreToolUse", workspace, "fallback", tool_name="exec_command"
            )
            rendered = rendered_windows_command(commands["PreToolUse"], plugin_root)
            result = subprocess.run(
                rendered,
                shell=True,
                executable=command_prompt,
                input=json.dumps(event) + "\n",
                capture_output=True,
                text=True,
                cwd=workspace,
                env=environment,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"fallback hook failed:\n{result.stderr}\n{result.stdout}",
            )
            self.assertIn("No installed Python found", result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn(
                "Click default mode:",
                payload["hookSpecificOutput"]["updatedInput"]["command"],
            )
            self.assertEqual(
                launcher_log.read_text(encoding="utf-8").splitlines(), ["py"]
            )


if __name__ == "__main__":
    unittest.main()
