"""Shared shell-free capability request validation for Click.

This leaf owns only protocol decoding and direct argv normalization. Domain
modules may depend on it, but it must not import state, runners, host adapters,
or any higher-level Click policy.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
from typing import Any


PROTOCOL_VERSION = 1
MAX_ARGV_ITEMS = 128
SHELL_EXECUTABLES = {
    "bash",
    "cmd",
    "cmd.exe",
    "dash",
    "fish",
    "ksh",
    "powershell",
    "powershell.exe",
    "pwsh",
    "sh",
    "zsh",
}
PROCESS_CONTROL_EXECUTABLES = {
    "kill",
    "kill.exe",
    "killall",
    "pkill",
    "pskill",
    "pskill.exe",
    "skill",
    "stop-process",
    "taskkill",
    "taskkill.exe",
    "tskill",
    "tskill.exe",
    "xkill",
}
SHELL_CONTROL_PUNCTUATION = set("();<>|&")
ENVIRONMENT_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def decode_request(
    raw: str,
    label: str,
    *,
    version: int = PROTOCOL_VERSION,
) -> tuple[dict[str, Any] | None, str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None, f"{label} request must be valid JSON."
    if not isinstance(value, dict):
        return None, f"{label} request must be a JSON object."
    if value.get("version") != version:
        return None, f"{label} request `version` must be {version}."
    return value, ""


def encode_request(request: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
    ).decode()


def decode_encoded_request(encoded: str, label: str) -> tuple[str, str]:
    try:
        return base64.urlsafe_b64decode(encoded.encode()).decode(), ""
    except (ValueError, UnicodeDecodeError):
        return "", f"Click {label} runner received an invalid request."


def digest(request: dict[str, Any]) -> str:
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def policy_executable_name(value: str) -> str:
    """Normalize Win32 executable aliases before policy comparisons."""
    executable = value.replace("\\", "/").rsplit("/", 1)[-1].lower()
    executable = executable.rstrip(" .")
    if executable.endswith(".exe"):
        executable = executable[:-4].rstrip(" .")
    return executable


def validate_argv(value: Any, label: str) -> tuple[list[str] | None, str]:
    if not isinstance(value, list) or not value:
        return None, f"{label} `argv` must be a non-empty string list."
    if len(value) > MAX_ARGV_ITEMS:
        return None, f"{label} `argv` may contain at most {MAX_ARGV_ITEMS} items."
    if any(
        not isinstance(item, str) or not item or "\x00" in item for item in value
    ):
        return None, f"Every {label} `argv` item must be a non-empty NUL-free string."
    argv = list(value)
    if ENVIRONMENT_ASSIGNMENT.match(argv[0]):
        return (
            None,
            f"{label} cannot use a NAME=value environment prefix. Pass direct argv; "
            "a future protocol may add an explicit environment field.",
        )
    executable = policy_executable_name(argv[0])
    if executable in SHELL_EXECUTABLES:
        return (
            None,
            f"{label} cannot invoke a shell interpreter. Pass the executable and each "
            "argument directly instead of using `-c` or `-Command`.",
        )
    if executable in PROCESS_CONTROL_EXECUTABLES:
        return (
            None,
            f"{label} cannot invoke the process-control executable `{executable}`. "
            "Use a target-specific lifecycle command that cannot terminate Codex or "
            "unrelated processes.",
        )
    return argv, ""


def shell_segments(command: str) -> list[list[str]] | None:
    if "\n" in command or "\r" in command or "`" in command:
        return None
    try:
        lexer = shlex.shlex(
            command,
            posix=True,
            punctuation_chars="".join(sorted(SHELL_CONTROL_PUNCTUATION)),
        )
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return None

    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in {"&&", "|"}:
            if not segments[-1]:
                return None
            segments.append([])
            continue
        if token and set(token).issubset(SHELL_CONTROL_PUNCTUATION):
            return None
        segments[-1].append(token)
    if not segments[-1]:
        return None
    return segments


def command_parts(tokens: list[str]) -> tuple[str, list[str]]:
    remaining = list(tokens)
    while remaining and "=" in remaining[0] and not remaining[0].startswith(("=", "-")):
        name, _, _ = remaining[0].partition("=")
        if not name.replace("_", "a").isalnum():
            break
        remaining.pop(0)
    if not remaining:
        return "", []
    executable = policy_executable_name(remaining[0])
    return executable, [item.lower() for item in remaining[1:]]


def positional_arguments(
    arguments: list[str], options_with_values: set[str] | None = None
) -> list[str]:
    value_options = options_with_values or set()
    positions: list[str] = []
    skip_value = False
    options_finished = False
    for argument in arguments:
        lowered = argument.lower()
        if skip_value:
            skip_value = False
            continue
        if not options_finished and lowered == "--":
            options_finished = True
            continue
        if not options_finished and lowered in value_options:
            skip_value = True
            continue
        if not options_finished and any(
            lowered.startswith(f"{option}=") for option in value_options
        ):
            continue
        if not options_finished and lowered.startswith("-"):
            continue
        positions.append(lowered)
    return positions
