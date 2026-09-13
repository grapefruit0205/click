from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


SCRIPT = Path(
    os.environ.get(
        "CLICK_GATE_UNDER_TEST",
        Path(__file__).parents[1] / "hooks" / "click_gate.py",
    )
)
HOOK_CONFIG = Path(
    os.environ.get(
        "CLICK_HOOK_CONFIG_UNDER_TEST",
        Path(__file__).parents[1] / "hooks" / "hooks.json",
    )
)
CLICK_GATE_SPEC = importlib.util.spec_from_file_location("click_gate_under_test", SCRIPT)
assert CLICK_GATE_SPEC is not None and CLICK_GATE_SPEC.loader is not None
CLICK_GATE = importlib.util.module_from_spec(CLICK_GATE_SPEC)
sys.path.insert(0, str(SCRIPT.parent.resolve()))
try:
    CLICK_GATE_SPEC.loader.exec_module(CLICK_GATE)
finally:
    sys.path.pop(0)

CLICK_PROCESS = CLICK_GATE.click_process
CLICK_BROWSER = CLICK_GATE.click_browser
CLICK_CAPABILITY = CLICK_GATE.click_capability
CLICK_EVIDENCE = CLICK_GATE.click_evidence
CLICK_INSPECTION = CLICK_GATE.click_inspection
CLICK_LIFECYCLE = CLICK_GATE.click_lifecycle
CLICK_MUTATION = CLICK_GATE.click_mutation
CLICK_OBSERVER_CONTROL = CLICK_GATE.click_observer_control
CLICK_PROMPT = CLICK_GATE.click_prompt
CLICK_SERVICE = CLICK_GATE.click_service
CLICK_SHADOW_DASHBOARD = CLICK_GATE.click_shadow_dashboard
CLICK_SHADOW_INTELLIGENCE = CLICK_GATE.click_shadow_intelligence
CLICK_STATE = CLICK_GATE.click_state
CLICK_VERIFICATION = CLICK_GATE.click_verification


def mark_git_boundary(root: Path) -> None:
    marker = root / ".git"
    marker.mkdir(parents=True)
    (marker / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (marker / "objects").mkdir()


def split_runner_command(command: str) -> list[str]:
    if os.name != "nt":
        return shlex.split(command, posix=True)
    import ctypes

    argument_count = ctypes.c_int()
    shell32 = ctypes.windll.shell32
    shell32.CommandLineToArgvW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argv = shell32.CommandLineToArgvW(
        command, ctypes.byref(argument_count)
    )
    if not argv:
        raise OSError("CommandLineToArgvW failed")
    try:
        parsed = [argv[index] for index in range(argument_count.value)]
    finally:
        kernel32 = ctypes.windll.kernel32
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))
    encoded_index = 3 if parsed[:2] == ["py", "-3"] else 2
    if (
        len(parsed) == encoded_index + 2
        and parsed[encoded_index] == "--encoded-runner"
    ):
        decoded, error = CLICK_GATE.click_runner_transport.decode_runner_transport(
            parsed[encoded_index + 1]
        )
        if error or decoded is None:
            raise ValueError(error or "invalid runner transport")
        script_index = encoded_index - 1
        return [parsed[0], parsed[script_index], *decoded]
    return parsed


class ClickGateTestCase(unittest.TestCase):
    # Most suites retain the executable boundary.  Large state-machine suites
    # may opt into the equivalent in-process entry point and leave dedicated
    # transport tests to cover stdin/stdout and interpreter startup.
    hook_in_process = False

    def _remove_temporary(self) -> None:
        """Remove the fixture; on Windows a child that just stopped may still
        hold the workspace as its working directory for a moment, which locks
        the directory against removal. Retry briefly instead of failing the
        test after its assertions already passed."""
        deadline = time.monotonic() + 10
        while True:
            try:
                self.temporary.cleanup()
                return
            except PermissionError:
                if os.name != "nt" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)

    def setUp(self) -> None:
        # Hook and runner output is rendered in the dashboard locale; pin it so
        # assertions read the same text on a Korean desktop and a C.UTF-8 runner.
        language = mock.patch.dict(os.environ, {"CLICK_LANGUAGE": "en"})
        language.start()
        self.addCleanup(language.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._remove_temporary)
        self.plugin_data = Path(self.temporary.name) / "plugin-data"
        self.workspace = Path(self.temporary.name) / "workspace"
        self.workspace.mkdir()
        self.submitted_turns: set[str] = set()
        (self.workspace / "verification_fixture.py").write_text(
            "import unittest\n\n"
            "class VerificationFixture(unittest.TestCase):\n"
            "    def test_pass(self):\n"
            "        self.assertTrue(True)\n\n"
            "    def test_fail(self):\n"
            "        self.fail('expected verification failure')\n",
            encoding="utf-8",
        )
        self.base_event = {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(self.workspace),
            "model": "test-model",
            "permission_mode": "default",
        }

    def run_hook(
        self, mode: str, event: dict
    ) -> tuple[subprocess.CompletedProcess[str], dict | None]:
        if self.hook_in_process:
            standard_input = io.StringIO(json.dumps(event))
            standard_output = io.StringIO()
            standard_error = io.StringIO()
            environment = {
                "PLUGIN_DATA": str(self.plugin_data),
                "CLICK_CONFIG_HOME": str(self.plugin_data),
            }
            with (
                mock.patch.dict(os.environ, environment, clear=False),
                mock.patch.object(sys, "argv", [str(SCRIPT), mode]),
                mock.patch.object(sys, "stdin", standard_input),
                mock.patch.object(sys, "stdout", standard_output),
                mock.patch.object(sys, "stderr", standard_error),
                mock.patch.object(sys, "path", [str(SCRIPT.parent.resolve()), *sys.path]),
            ):
                returncode = CLICK_GATE.main()
            stdout = standard_output.getvalue()
            result = subprocess.CompletedProcess(
                [sys.executable, str(SCRIPT), mode],
                returncode,
                stdout,
                standard_error.getvalue(),
            )
            payload = json.loads(stdout) if stdout else None
            return result, payload
        environment = os.environ.copy()
        environment["PLUGIN_DATA"] = str(self.plugin_data)
        environment["CLICK_CONFIG_HOME"] = str(self.plugin_data)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), mode],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        payload = json.loads(result.stdout) if result.stdout else None
        return result, payload

    def pre_tool(
        self,
        tool_name: str,
        command: str,
        turn_id: str = "turn-1",
        *,
        submit_prompt: bool = True,
        tool_use_id: str = "tool-1",
    ) -> dict | None:
        if submit_prompt and turn_id not in self.submitted_turns:
            self.prompt_submit("test user request", turn_id)
        event = {
            **self.base_event,
            "turn_id": turn_id,
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_use_id": tool_use_id,
            "tool_input": {"command": command},
        }
        result, payload = self.run_hook("pre-tool", event)
        self.assertEqual(result.returncode, 0, result.stderr)
        return payload

    def assert_plan_advisory(
        self, payload: dict | None, expected_context: str
    ) -> None:
        self.assertIsNotNone(payload)
        assert payload is not None
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertNotIn("permissionDecision", output)
        self.assertNotIn("permissionDecisionReason", output)
        self.assertNotIn("updatedInput", output)
        self.assertIn(expected_context, output["additionalContext"])

    def assert_runner_advisory(
        self,
        payload: dict | None,
        expected_context: str,
        runner_action: str,
    ) -> None:
        self.assertIsNotNone(payload)
        assert payload is not None
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertEqual(output["permissionDecision"], "allow")
        self.assertNotIn("permissionDecisionReason", output)
        self.assertIn(
            runner_action,
            split_runner_command(output["updatedInput"]["command"]),
        )
        self.assertIn("Click advisory", output["additionalContext"])
        self.assertIn(expected_context, output["additionalContext"])

    def assert_observation_advisory(
        self, payload: dict | None, expected_context: str
    ) -> None:
        self.assert_runner_advisory(
            payload, expected_context, "run-observation"
        )

    def assert_verification_advisory(
        self, payload: dict | None, expected_context: str
    ) -> None:
        self.assert_runner_advisory(
            payload, expected_context, "run-verification"
        )

    def assert_browser_advisory(
        self, payload: dict | None, expected_context: str
    ) -> None:
        self.assertIsNotNone(payload)
        assert payload is not None
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertNotIn("permissionDecision", output)
        self.assertNotIn("permissionDecisionReason", output)
        self.assertNotIn("updatedInput", output)
        self.assertIn("Click advisory", output["additionalContext"])
        self.assertIn(expected_context, output["additionalContext"])

    def tool_hook(
        self,
        mode: str,
        tool_name: str,
        tool_input: dict[str, object],
        *,
        turn_id: str = "turn-2",
        tool_use_id: str = "browser-tool-1",
        tool_response: dict[str, object] | None = None,
    ) -> dict | None:
        event = {
            **self.base_event,
            "turn_id": turn_id,
            "hook_event_name": "PreToolUse" if mode == "pre-tool" else "PostToolUse",
            "tool_name": tool_name,
            "tool_use_id": tool_use_id,
            "tool_input": tool_input,
        }
        if tool_response is not None:
            event["tool_response"] = tool_response
        result, payload = self.run_hook(mode, event)
        self.assertEqual(result.returncode, 0, result.stderr)
        return payload

    def prompt_submit(
        self, prompt: str = "review this code", turn_id: str = "turn-1"
    ) -> dict:
        event = {
            **self.base_event,
            "turn_id": turn_id,
            "hook_event_name": "UserPromptSubmit",
            "prompt": prompt,
        }
        result, payload = self.run_hook("prompt-submit", event)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNotNone(payload)
        self.submitted_turns.add(turn_id)
        return payload

    def contract(self) -> dict:
        return {
            "outcome": "send one alert when inventory crosses below its threshold",
            "boundary": {
                "in_scope": ["inventory threshold transition and notification path"],
                "out_of_scope": ["unrelated inventory and purchasing behavior"],
            },
            "must_hold": [
                "send at most one alert per threshold crossing",
                "preserve the existing inventory write behavior",
            ],
            "build": {
                "approach": [
                    "extend the existing threshold transition and notification path"
                ],
                "semantics": [
                    "deduplicate notification intent at the inventory write boundary"
                ],
                "order": ["record the inventory transition before dispatching the alert"],
            },
            "verification": {
                "scale": "focused",
                "evidence": [
                    {
                        "id": "E1",
                        "kind": "argv",
                        "description": "focused concurrent threshold tests",
                    }
                ],
                "done_when": [
                    {
                        "condition": "one alert is sent per threshold crossing",
                        "primary_evidence": "E1",
                    }
                ],
            },
            "plain_language": (
                "재고가 임계값 아래로 내려갈 때 같은 상황에서는 알림을 한 번만 보내고, "
                "승인된 순서대로 현재 알림 경로를 수정하고 검증합니다."
            ),
        }

    def stage_gate(
        self, contract: dict | None = None, turn_id: str = "turn-1"
    ) -> dict:
        value = contract or self.contract()
        command = f"click-gate stage {shlex.quote(json.dumps(value))}"
        payload = self.pre_tool("Bash", command, turn_id)
        self.assertIsNotNone(payload)
        return payload

    def active_contract_id(self) -> str:
        state_paths = list(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        if not state_paths:
            return "ctr_" + ("0" * 32)
        state = json.loads(state_paths[0].read_text(encoding="utf-8"))
        contract_id = CLICK_LIFECYCLE.contract_id_from_state(state)
        if not contract_id:
            return "ctr_" + ("0" * 32)
        self.assertRegex(contract_id, r"^ctr_[0-9a-f]{32}$")
        return contract_id

    def pass_gate(
        self, contract_id: str | None = None, turn_id: str = "turn-2"
    ) -> dict:
        if contract_id is not None and not isinstance(contract_id, str):
            raise TypeError("pass_gate accepts only a contract_id string")
        contract_id = contract_id or self.active_contract_id()
        command = f"click-gate pass {contract_id}"
        payload = self.pre_tool("Bash", command, turn_id)
        self.assertIsNotNone(payload)
        return payload

    def approve_contract(self) -> None:
        self.arm_gate("turn-1")
        self.stage_gate(turn_id="turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")

    def arm_gate(self, turn_id: str = "turn-1") -> dict:
        payload = self.pre_tool("Bash", "click-gate arm", turn_id)
        self.assertIsNotNone(payload)
        return payload

    def bypass_gate(self, turn_id: str = "turn-1") -> dict:
        self.prompt_submit("@Click bypass", turn_id)
        payload = self.pre_tool(
            "Bash", "click-gate bypass", turn_id, submit_prompt=False
        )
        self.assertIsNotNone(payload)
        return payload

    def cancel_gate(self, turn_id: str = "turn-1") -> dict:
        self.prompt_submit("@Click cancel", turn_id)
        payload = self.pre_tool(
            "Bash", "click-gate cancel", turn_id, submit_prompt=False
        )
        self.assertIsNotNone(payload)
        return payload

    def set_mode(self, mode: str, turn_id: str = "turn-1") -> dict:
        payload = self.pre_tool(
            "Bash", f"click-gate mode {mode}", turn_id
        )
        self.assertIsNotNone(payload)
        return payload

    def set_default(self, mode: str, turn_id: str = "turn-1") -> dict:
        payload = self.pre_tool(
            "Bash", f"click-gate default {mode}", turn_id
        )
        self.assertIsNotNone(payload)
        return payload

    def start_review(self, turn_id: str = "turn-1") -> dict:
        payload = self.pre_tool("Bash", "click-gate review", turn_id)
        self.assertIsNotNone(payload)
        return payload

    def verification_argv(self, exit_code: int = 0) -> list[str]:
        test_name = "test_pass" if exit_code == 0 else "test_fail"
        return [
            sys.executable,
            "-m",
            "unittest",
            f"verification_fixture.VerificationFixture.{test_name}",
        ]

    def initialize_git(self, *tracked_paths: str) -> None:
        initialized = subprocess.run(
            ["git", "init", "--quiet"],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        subprocess.run(
            ["git", "add", *tracked_paths],
            cwd=self.workspace,
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Click Tests",
                "-c",
                "user.email=click-tests@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "fixture",
            ],
            cwd=self.workspace,
            check=True,
        )

    def read_file_command(self, path: str, fail_hard: bool = False) -> str:
        if os.name == "nt":
            return f"Get-Content -Raw {path}"
        return f"sed -n '1,99999p' {path}"

    def inspect_gate(
        self, commands: list[list[str]], turn_id: str = "turn-1"
    ) -> dict:
        request = {"version": 1, "commands": commands}
        command = f"click-gate inspect {shlex.quote(json.dumps(request))}"
        payload = self.pre_tool("Bash", command, turn_id)
        self.assertIsNotNone(payload)
        return payload

    def mutate_gate(self, argv: list[str], turn_id: str = "turn-2") -> dict:
        request = {"version": 1, "argv": argv}
        command = f"click-gate mutate {shlex.quote(json.dumps(request))}"
        payload = self.pre_tool("Bash", command, turn_id)
        self.assertIsNotNone(payload)
        return payload

    def verification_class(self, command: str) -> str:
        lowered = command.lower()
        if any(marker in lowered for marker in ("playwright", "coverage", "audit")):
            return "deep"
        if any(
            marker in lowered
            for marker in (
                "unittest discover",
                "pytest tests",
                "vitest run",
                "npm test",
            )
        ):
            return "broad"
        return "targeted"

    def verify_gate(
        self,
        commands: list[str | list[str]],
        turn_id: str = "turn-2",
        evidence_ids: list[str] | None = None,
        raw_output: bool = True,
    ) -> dict:
        """Submit checks. Fixtures observe child output markers, so the request
        asks for raw reporting explicitly; Evidence's actionable default is
        covered by tests that build their own request without `reporting`."""
        checks = []
        for index, value in enumerate(commands):
            if isinstance(value, list):
                argv = value
                rendered = " ".join(value)
            else:
                argv = shlex.split(value, posix=True)
                rendered = value
            checks.append(
                {
                    "evidence_id": evidence_ids[index] if evidence_ids else "E1",
                    "argv": argv,
                    "class": self.verification_class(rendered),
                }
            )
        batch: dict[str, object] = {"version": 2, "checks": checks}
        if raw_output:
            batch["reporting"] = CLICK_GATE.click_diagnostics.default_reporting()
        command = f"click-gate verify {shlex.quote(json.dumps(batch))}"
        self.verification_request_sequence = getattr(self, "verification_request_sequence", 0) + 1
        payload = self.pre_tool("Bash", command, turn_id, tool_use_id=f"verification-{self.verification_request_sequence}")
        self.assertIsNotNone(payload)
        return payload

    def verify_checks(
        self,
        checks: list[dict[str, object]],
        turn_id: str = "turn-2",
        *,
        bind_default: bool = True,
        version: int = 2,
    ) -> dict:
        normalized = [
            ({"evidence_id": "E1", **check} if bind_default else dict(check))
            for check in checks
        ]
        batch = {"version": version, "checks": normalized}
        command = f"click-gate verify {shlex.quote(json.dumps(batch))}"
        self.verification_request_sequence = getattr(self, "verification_request_sequence", 0) + 1
        payload = self.pre_tool("Bash", command, turn_id, tool_use_id=f"verification-{self.verification_request_sequence}")
        self.assertIsNotNone(payload)
        return payload

    def complete_evidence(
        self, evidence_id: str, turn_id: str = "turn-2"
    ) -> dict:
        request = {"version": 1, "evidence_id": evidence_id}
        command = f"click-gate evidence {shlex.quote(json.dumps(request))}"
        payload = self.pre_tool("Bash", command, turn_id)
        self.assertIsNotNone(payload)
        return payload

    def legacy_verify_gate(self, commands: list[str], turn_id: str = "turn-2") -> dict:
        batch = {"version": 2, "commands": commands}
        command = f"click-gate verify {shlex.quote(json.dumps(batch))}"
        payload = self.pre_tool("Bash", command, turn_id)
        self.assertIsNotNone(payload)
        return payload

    def rewritten_invocation(self, payload: dict) -> tuple[str | list[str], bool]:
        command = payload["hookSpecificOutput"]["updatedInput"]["command"]
        invocation: str | list[str] = command
        use_shell = True
        if os.name == "nt" and command.startswith("py -3 "):
            # Runner shell compatibility has dedicated PowerShell/cmd.exe
            # integration coverage. Keep semantic unit tests on the same
            # interpreter that prepared their environment fingerprints.
            invocation = split_runner_command(command)
            invocation = [sys.executable, *invocation[1:]]
            use_shell = False
        return invocation, use_shell

    def run_rewritten(
        self,
        payload: dict,
        environment_updates: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        invocation, use_shell = self.rewritten_invocation(payload)
        environment = os.environ.copy()
        environment["PLUGIN_DATA"] = str(self.plugin_data)
        environment["CLICK_CONFIG_HOME"] = str(self.plugin_data)
        if environment_updates:
            environment.update(environment_updates)
        return subprocess.run(
            invocation,
            shell=use_shell,
            cwd=self.workspace,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def observation_runner_arguments(self, payload: dict) -> list[str]:
        command = payload["hookSpecificOutput"]["updatedInput"]["command"]
        runner = split_runner_command(command)
        action_index = runner.index("run-observation")
        return runner[action_index + 1 :]

    def assert_verification_new_path_behavior(
        self,
        relative: str,
        *,
        ignored: bool = False,
        suspicious: bool = False,
    ) -> None:
        ignore_lines = ["__pycache__/"]
        if ignored:
            ignore_lines.append(relative)
        (self.workspace / ".gitignore").write_text(
            "\n".join(ignore_lines) + "\n", encoding="utf-8"
        )
        escaped = repr(relative)
        (self.workspace / "new_path_test.py").write_text(
            "import unittest\n"
            "from pathlib import Path\n\n"
            "class NewPathTest(unittest.TestCase):\n"
            "    def test_writes_path(self):\n"
            f"        target = Path({escaped})\n"
            "        target.parent.mkdir(parents=True, exist_ok=True)\n"
            "        target.write_text('generated\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        self.initialize_git(".gitignore", "new_path_test.py")
        contract = self.contract()
        contract["verification"]["scale"] = "quick"
        self.arm_gate("turn-1")
        self.stage_gate(contract, "turn-1")
        self.arm_gate("turn-2")
        self.pass_gate(turn_id="turn-2")
        payload = self.verify_checks(
            [
                {
                    "argv": [
                        sys.executable,
                        "-m",
                        "unittest",
                        "new_path_test.NewPathTest.test_writes_path",
                    ],
                    "class": "targeted",
                }
            ]
        )
        result = self.run_rewritten(payload)
        if ignored:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("new non-ignored untracked path", result.stderr)
            return
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("new non-ignored untracked path", result.stderr)
        self.assertIn("batch is stale", result.stderr)
        if suspicious:
            self.assertIn("classification is informational", result.stderr)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["verification"]["status"], "failed")
        self.assertTrue(state["verification"]["workspace_changed"])
        self.assertEqual(state["verification"]["mutation_revision"], 1)
