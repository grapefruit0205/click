"""The Claude Code Hook launcher and the Windows (Git Bash) host path.

Claude Code runs a shell-form hook command through ``sh -c`` on macOS and
Linux and through Git Bash on Windows. ``hooks/claude_hook.sh`` selects the
Python 3 interpreter there, and ``claude_hook.py`` renders rewritten commands
for Git Bash on Windows. The interpreter selection is exercised on every OS
with fake interpreters on a private PATH; the Git Bash end-to-end path runs
only on Windows, where the CI runner ships Git for Windows.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from hooks import claude_hook, click_runner_transport
from scripts import build_claude_distribution


ROOT = Path(__file__).parents[1]
LAUNCHER = ROOT / "hooks" / "claude_hook.sh"
SH = shutil.which("sh") or "sh"
HOOKS_JSON = ROOT / "platforms" / "claude" / "hooks.json"
PROMPT_ID = "550e8400-e29b-41d4-a716-446655440000"
COREUTILS = ("uname", "tr", "dirname", "cat")


def hook_command(mode: str) -> str:
    config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    event_name = {
        "prompt-submit": "UserPromptSubmit",
        "pre-tool": "PreToolUse",
        "post-tool": "PostToolUse",
        "session-end": "SessionEnd",
    }[mode]
    return config["hooks"][event_name][0]["hooks"][0]["command"]


def git_bash() -> str | None:
    """Git for Windows' bash.exe, which Claude Code uses for shell-form hooks."""
    if os.name != "nt":
        return None
    found = shutil.which("bash")
    if found and "Git" in found:
        return found
    for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        base = os.environ.get(variable)
        if base:
            candidate = Path(base) / "Git" / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    return found


def write_package(destination: Path) -> Path:
    """Materialize the exact Claude Code package the marketplace installs."""
    for relative, contents in build_claude_distribution.expected_files(ROOT).items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    return destination


class LauncherScriptTests(unittest.TestCase):
    def test_launcher_is_posix_sh_with_lf_endings_and_ships_in_the_package(self) -> None:
        raw = LAUNCHER.read_bytes()
        self.assertTrue(raw.startswith(b"#!/bin/sh\n"))
        self.assertNotIn(b"\r", raw)
        text = raw.decode("utf-8")
        for name in ("py", "python", "python3", "-X utf8", "WindowsApps", "103"):
            self.assertIn(name, text)
        self.assertIn("claude_hook.sh", build_claude_distribution.HOOK_FILES)
        self.assertIn(
            Path("hooks/claude_hook.sh"), build_claude_distribution.expected_files(ROOT)
        )
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.sh text eol=lf", attributes)
        for mode in ("prompt-submit", "pre-tool", "post-tool", "session-end"):
            self.assertEqual(
                hook_command(mode), f'sh "${{CLAUDE_PLUGIN_ROOT}}/hooks/claude_hook.sh" {mode}'
            )

    @unittest.skipIf(os.name == "nt", "POSIX sh syntax check")
    def test_launcher_parses_under_sh(self) -> None:
        result = subprocess.run(["sh", "-n", str(LAUNCHER)], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stderr), (0, ""))

    @unittest.skipIf(os.name == "nt", "Claude Code runs shell-form hooks through sh -c here")
    def test_shell_form_command_runs_the_packaged_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            plugin_root = write_package(base / "click plugin")
            workspace = base / "workspace"
            workspace.mkdir()
            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in {"PLUGIN_DATA", "CLICK_CONFIG_HOME"}
            }
            environment.update(
                {
                    "CLAUDE_PLUGIN_ROOT": str(plugin_root),
                    "CLAUDE_PLUGIN_DATA": str(base / "data"),
                    "CLICK_HOOK_WORKER": "0",
                    "CLICK_LANGUAGE": "en",
                }
            )
            command = hook_command("prompt-submit").replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root))
            event = {
                "session_id": "claude-launcher",
                "transcript_path": str(base / "transcript.jsonl"),
                "cwd": str(workspace),
                "permission_mode": "default",
                "hook_event_name": "UserPromptSubmit",
                "prompt_id": PROMPT_ID,
                "prompt": "테스트를 실행해줘",
            }
            result = subprocess.run(
                ["sh", "-c", command],
                input=json.dumps(event, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=str(workspace),
                env=environment,
                check=False,
            )
            self.assertEqual(result.returncode, 0, f"{result.stderr}\n{result.stdout}")
            payload = json.loads(result.stdout)
            context = payload["hookSpecificOutput"]["additionalContext"]
            self.assertIn("Click Evidence mode is enabled", context)
            self.assertIn("`[Click result]`", context)


@unittest.skipIf(os.name == "nt", "fake interpreters on a private PATH need POSIX sh")
class LauncherInterpreterSelectionTests(unittest.TestCase):
    """Drive the selection order with fake interpreters and a fake uname."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.tools = self.base / "tools"
        self.tools.mkdir()
        for name in COREUTILS:
            real = shutil.which(name)
            self.assertIsNotNone(real, name)
            os.symlink(real, self.tools / name)
        self.record = self.base / "record.txt"
        self.plugin = self.base / "plugin root with spaces"
        (self.plugin / "hooks").mkdir(parents=True)
        shutil.copy(LAUNCHER, self.plugin / "hooks" / "claude_hook.sh")
        (self.plugin / "hooks" / "claude_hook.py").write_text("", encoding="utf-8")

    def fake(self, directory: Path, name: str, body: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def recorder(self, label: str) -> str:
        return f'printf \'%s\\n\' "{label}" "$@" > "{self.record}"\ncat > /dev/null\nexit 0\n'

    def run_launcher(self, *directories: Path, uname: str | None = None) -> subprocess.CompletedProcess[str]:
        path_entries = [str(directory) for directory in directories]
        if uname is not None:
            fake_uname = self.base / "uname-override"
            self.fake(fake_uname, "uname", f"printf '%s\\n' '{uname}'\n")
            path_entries.insert(0, str(fake_uname))
        path_entries.append(str(self.tools))
        return subprocess.run(
            [SH, str(self.plugin / "hooks" / "claude_hook.sh"), "pre-tool"],
            input='{"hook_event_name":"PreToolUse"}',
            capture_output=True,
            text=True,
            env={"PATH": os.pathsep.join(path_entries), "LC_ALL": "C"},
            check=False,
        )

    def recorded(self) -> list[str]:
        return self.record.read_text(encoding="utf-8").splitlines()

    def test_posix_prefers_python3_then_python_in_utf8_mode(self) -> None:
        both = self.base / "both"
        self.fake(both, "python3", self.recorder("python3"))
        self.fake(both, "python", self.recorder("python"))
        self.fake(both, "py", self.recorder("py"))
        result = self.run_launcher(both)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        adapter = str(self.plugin / "hooks" / "claude_hook.py")
        self.assertEqual(self.recorded(), ["python3", "-X", "utf8", adapter, "pre-tool"])

        only_python = self.base / "only-python"
        self.fake(only_python, "python", self.recorder("python"))
        self.fake(only_python, "py", self.recorder("py"))
        result = self.run_launcher(only_python)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.recorded()[0], "python")

    def test_windows_prefers_the_py_launcher_and_skips_the_store_stub(self) -> None:
        windows = self.base / "windows"
        self.fake(windows, "py", self.recorder("py"))
        self.fake(windows, "python", self.recorder("python"))
        self.fake(windows, "python3", self.recorder("python3"))
        result = self.run_launcher(windows, uname="MINGW64_NT-10.0-22631")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        adapter = str(self.plugin / "hooks" / "claude_hook.py")
        self.assertEqual(self.recorded(), ["py", "-3", "-X", "utf8", adapter, "pre-tool"])

        # The py launcher exits 103 when no Python 3 is registered with it and
        # never reads stdin, so the next candidate still receives the event.
        no_python3 = self.base / "py-without-python3"
        self.fake(no_python3, "py", "exit 103\n")
        self.fake(no_python3, "python", self.recorder("python"))
        result = self.run_launcher(no_python3, uname="MSYS_NT-10.0")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.recorded()[0], "python")

        # The Microsoft Store alias stub under WindowsApps only offers to
        # install Python; a candidate there must prove it can run `import sys`.
        store = self.base / "Microsoft" / "WindowsApps"
        self.fake(store, "python", "echo 'Python was not found' >&2\nexit 9009\n")
        self.fake(store, "python3", self.recorder("python3-store"))
        result = self.run_launcher(store, uname="MINGW64_NT-10.0-22631")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.recorded()[0], "python3-store")

    def test_missing_interpreter_is_reported_without_running_anything(self) -> None:
        result = self.run_launcher(self.base / "empty")
        self.assertEqual(result.returncode, 127)
        self.assertIn("click hook error: Click requires Python 3", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.record.exists())

    def test_launcher_normalizes_a_backslash_plugin_root(self) -> None:
        # Claude Code substitutes ${CLAUDE_PLUGIN_ROOT} with backslashes on
        # Windows, so $0 arrives as `C:\...\click/hooks/claude_hook.sh`. The
        # launcher converts the separators before locating the adapter next to
        # itself. A POSIX directory whose name contains a literal backslash
        # reproduces that spelling without Windows.
        both = self.base / "both"
        self.fake(both, "python3", self.recorder("python3"))
        spelled_hooks = self.base / "plugin\\hooks"
        spelled_hooks.mkdir()
        shutil.copy(LAUNCHER, spelled_hooks / "claude_hook.sh")
        normalized_hooks = self.base / "plugin" / "hooks"
        normalized_hooks.mkdir(parents=True)
        (normalized_hooks / "claude_hook.py").write_text("", encoding="utf-8")
        result = subprocess.run(
            [SH, str(spelled_hooks / "claude_hook.sh"), "post-tool"],
            input="{}",
            capture_output=True,
            text=True,
            env={"PATH": os.pathsep.join([str(both), str(self.tools)]), "LC_ALL": "C"},
            check=False,
        )
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.recorded()[3], str(normalized_hooks / "claude_hook.py"))


class ClaudeRunnerRenderingTests(unittest.TestCase):
    def test_claude_renderer_is_registered_and_activated_by_host_id(self) -> None:
        previous = click_runner_transport.install_runner_shell_renderer(
            click_runner_transport.default_runner_shell_command
        )
        try:
            self.assertIs(
                click_runner_transport.activate_host_renderer("claude"),
                claude_hook.runner_shell_command,
            )
            self.assertIs(
                click_runner_transport._runner_shell_renderer, claude_hook.runner_shell_command
            )
            self.assertIs(
                click_runner_transport.activate_host_renderer("unknown-host"),
                click_runner_transport.default_runner_shell_command,
            )
        finally:
            click_runner_transport.install_runner_shell_renderer(previous)
        with self.assertRaises(TypeError):
            click_runner_transport.register_host_renderer("", claude_hook.runner_shell_command)

    @unittest.skipIf(os.name == "nt", "POSIX rendering")
    def test_posix_rendering_is_the_shared_default(self) -> None:
        arguments = [sys.executable, "-c", "import sys; print(sys.argv)", "a b", "c'd"]
        self.assertEqual(
            claude_hook.runner_shell_command(arguments),
            click_runner_transport.default_runner_shell_command(arguments),
        )

    @unittest.skipUnless(os.name == "nt", "Git Bash rendering")
    def test_windows_rendering_targets_git_bash(self) -> None:
        script = ROOT / "hooks" / "click_gate.py"
        command = claude_hook.runner_shell_command(
            [sys.executable, str(script), "run-json-report", "C:\\state root\\report.json", "summary"]
        )
        import shlex

        argv = shlex.split(command)
        self.assertEqual(len(argv), 4)
        self.assertEqual(argv[0], str(Path(sys.executable).resolve()).replace("\\", "/"))
        self.assertEqual(argv[1], str(script.resolve()).replace("\\", "/"))
        self.assertNotIn("\\", argv[0] + argv[1])
        self.assertEqual(argv[2], "--encoded-runner")
        decoded, error = click_runner_transport.decode_runner_transport(argv[3])
        self.assertEqual(error, "")
        self.assertEqual(decoded, ["run-json-report", "C:\\state root\\report.json", "summary"])
        # Inline `-c` payloads have no script file to resolve, so the report
        # falls back to its bounded file form, as it does for Codex on Windows.
        self.assertEqual(claude_hook.runner_shell_command([sys.executable, "-c", "print(1)"]), "exit 2")
        self.assertEqual(claude_hook.runner_shell_command([sys.executable, str(script)]), "exit 2")
        self.assertEqual(
            claude_hook.runner_shell_command([sys.executable, str(script / "missing.py"), "x"]),
            "exit 2",
        )


@unittest.skipUnless(os.name == "nt", "Claude Code on Windows runs hooks and the Bash tool in Git Bash")
class GitBashIntegrationTests(unittest.TestCase):
    """The installed package driven through Git Bash the way Claude Code does it."""

    def setUp(self) -> None:
        self.bash = git_bash()
        self.assertIsNotNone(self.bash, "Git for Windows bash.exe is required on the Windows runner")
        # The resident worker may still be exiting when the directory goes.
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def environment(self, plugin_root: Path, plugin_data: Path, worker: str) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PLUGIN_DATA", "CLICK_CONFIG_HOME", "CLAUDE_PLUGIN_DATA", "CLICK_LANGUAGE"}
        }
        # A default Git for Windows install puts only Git\cmd on the Windows
        # PATH; `sh` must still resolve inside bash.exe from the MSYS runtime.
        environment["PATH"] = os.pathsep.join(
            entry
            for entry in environment.get("PATH", "").split(os.pathsep)
            if not (
                "git" in entry.lower()
                and any(entry.lower().rstrip("\\").endswith(tail) for tail in ("\\bin", "\\usr\\bin", "\\mingw64\\bin"))
            )
        )
        environment.update(
            {
                "CLAUDE_PLUGIN_ROOT": str(plugin_root),
                "CLAUDE_PLUGIN_DATA": str(plugin_data),
                "CLICK_HOOK_WORKER": worker,
                "CLICK_LANGUAGE": "en",
                # Pin the py launcher to this interpreter's version so the
                # hook, the runner and the check agree; a version the launcher
                # does not know exits 103 and falls through to `python`.
                "PY_PYTHON": f"{sys.version_info.major}.{sys.version_info.minor}",
                "PY_PYTHON3": f"{sys.version_info.major}.{sys.version_info.minor}",
            }
        )
        return environment

    def bash_run(self, command: str, *, stdin: str, cwd: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.bash, "-c", command],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(cwd),
            env=environment,
            check=False,
            timeout=120,
        )

    def stop_session(self, workspace: Path, environment: dict[str, str], spelled_root: str, root_name: str) -> None:
        command = hook_command("session-end").replace("${CLAUDE_PLUGIN_ROOT}", spelled_root)
        event = json.dumps(
            {
                "session_id": f"claude-windows-{root_name}".replace(" ", "-"),
                "transcript_path": str(self.base / "transcript.jsonl"),
                "cwd": str(workspace),
                "permission_mode": "default",
                "hook_event_name": "SessionEnd",
                "prompt_id": PROMPT_ID,
                "reason": "other",
            }
        )
        try:
            self.bash_run(command, stdin=event, cwd=workspace, environment=environment)
        except (OSError, subprocess.SubprocessError):
            pass
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                shutil.rmtree(workspace)
                return
            except OSError:
                time.sleep(0.25)

    def test_hooks_and_rewritten_commands_run_through_git_bash(self) -> None:
        for root_name, worker in (("click", "1"), ("Click Plugin Root With Spaces", "0")):
            with self.subTest(plugin_root=root_name, worker=worker):
                plugin_root = write_package(self.base / root_name)
                plugin_data = self.base / f"{root_name} data"
                workspace = self.base / f"{root_name} workspace"
                workspace.mkdir()
                subprocess.run(["git", "init", "-q", str(workspace)], check=True)
                # A real project ignores bytecode; Click reports any untracked
                # path a check leaves behind, and unittest writes __pycache__.
                (workspace / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
                (workspace / "calc.py").write_text("VALUE = 1\n", encoding="utf-8")
                (workspace / "test_calc.py").write_text(
                    "import unittest\nimport calc\n\n\nclass CalcTests(unittest.TestCase):\n"
                    "    def test_value(self):\n        self.assertEqual(calc.VALUE, 1)\n",
                    encoding="utf-8",
                )
                environment = self.environment(plugin_root, plugin_data, worker)
                # Claude Code substitutes the placeholder with the native
                # (backslash) path before Git Bash sees the command.
                spelled_root = str(plugin_root)
                self.assertIn("\\", spelled_root)
                # The resident worker keeps the workspace as its cwd until the
                # session ends; stop it even when an assertion fails so the
                # temporary directory can be removed.
                self.addCleanup(self.stop_session, workspace, environment, spelled_root, root_name)

                def hook(mode: str, event: dict[str, object]) -> dict[str, object]:
                    command = hook_command(mode).replace("${CLAUDE_PLUGIN_ROOT}", spelled_root)
                    result = self.bash_run(command, stdin=json.dumps(event), cwd=workspace, environment=environment)
                    self.assertEqual(result.returncode, 0, f"{mode} hook failed:\n{result.stderr}\n{result.stdout}")
                    self.assertNotIn("click hook error", result.stderr)
                    return json.loads(result.stdout) if result.stdout.strip() else {}

                def event(hook_event_name: str, **extra: object) -> dict[str, object]:
                    return {
                        "session_id": f"claude-windows-{root_name}".replace(" ", "-"),
                        "transcript_path": str(self.base / "transcript.jsonl"),
                        "cwd": str(workspace),
                        "permission_mode": "default",
                        "hook_event_name": hook_event_name,
                        "prompt_id": PROMPT_ID,
                        **extra,
                    }

                prompt = hook("prompt-submit", event("UserPromptSubmit", prompt="테스트를 실행해줘 (run the tests)"))
                context = prompt["hookSpecificOutput"]["additionalContext"]
                self.assertIn("Click Evidence mode is enabled", context)
                self.assertIn("`[Click result]`", context)

                status = hook(
                    "pre-tool",
                    event("PreToolUse", tool_name="Bash", tool_use_id="toolu_status",
                          tool_input={"command": "click-gate status --json", "description": "status"}),
                )
                rewritten = status["hookSpecificOutput"]["updatedInput"]["command"]
                self.assertEqual(status["hookSpecificOutput"]["permissionDecision"], "allow")
                self.assertEqual(status["hookSpecificOutput"]["updatedInput"]["description"], "status")
                self.assertNotIn("py -3", rewritten)
                self.assertNotIn("powershell", rewritten.lower())
                self.assertIn("--encoded-runner", rewritten)
                executed = self.bash_run(rewritten, stdin="", cwd=workspace, environment=environment)
                self.assertEqual(executed.returncode, 0, executed.stderr)
                self.assertEqual(json.loads(executed.stdout)["task"]["runtime_mode"], "evidence")

                check = "python -m unittest -q test_calc"
                verify = hook(
                    "pre-tool",
                    event("PreToolUse", tool_name="Bash", tool_use_id="toolu_verify",
                          tool_input={"command": f"click-gate verify -- {check}"}),
                )
                rewritten = verify["hookSpecificOutput"]["updatedInput"]["command"]
                self.assertIn("--encoded-runner", rewritten)
                executed = self.bash_run(rewritten, stdin="", cwd=workspace, environment=environment)
                self.assertEqual(executed.returncode, 0, f"{executed.stderr}\n{executed.stdout}")
                # Actionable reporting summarizes a passing check instead of
                # relaying its output; the diagnostic line is the evidence.
                self.assertIn("[Click verification 1/1:", executed.stdout + executed.stderr)
                self.assertIn("passed.", executed.stdout)
                self.assertNotIn("environment changed after preparation", executed.stdout)
                hook("post-tool", event("PostToolUse", tool_name="Bash", tool_use_id="toolu_verify",
                                        tool_input={"command": f"click-gate verify -- {check}"},
                                        tool_response={"stdout": executed.stdout, "stderr": executed.stderr}))

                summary = hook(
                    "pre-tool",
                    event("PreToolUse", tool_name="Bash", tool_use_id="toolu_summary",
                          tool_input={"command": "click-gate status"}),
                )
                executed = self.bash_run(
                    summary["hookSpecificOutput"]["updatedInput"]["command"], stdin="", cwd=workspace, environment=environment
                )
                self.assertEqual(executed.returncode, 0, executed.stderr)
                self.assertTrue(executed.stdout.startswith("Executed 1 "), executed.stdout)

                # The same check resubmitted with nothing changed is reused:
                # the Hook-side and runner-side environment fingerprints agree
                # under Git Bash, so the receipt is current.
                again = hook(
                    "pre-tool",
                    event("PreToolUse", tool_name="Bash", tool_use_id="toolu_verify_again",
                          tool_input={"command": f"click-gate verify -- {check}"}),
                )
                rewritten = again["hookSpecificOutput"]["updatedInput"]["command"]
                executed = self.bash_run(rewritten, stdin="", cwd=workspace, environment=environment)
                self.assertEqual(executed.returncode, 0, f"{executed.stderr}\n{executed.stdout}")
                self.assertIn("Click reused 1 current", executed.stdout, f"{executed.stderr}\n{executed.stdout}")
                self.assertNotIn("[Click verification 1/1:", executed.stdout + executed.stderr)


if __name__ == "__main__":
    unittest.main()
