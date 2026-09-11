#!/usr/bin/env python3
"""Bounded runner argv transport and host shell rendering.

This leaf module knows how to serialize runner argv and render the one command
that launches Click again. It deliberately knows nothing about contracts,
state, evidence, or host event routing. Host adapters may install a process-local
renderer without reaching into ``click_gate`` private globals.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
import json
import os
import shlex
import zlib


MAX_RUNNER_TRANSPORT_BYTES = 24_000
WINDOWS_COMMAND_LINE_LIMIT = 8_191

RunnerShellRenderer = Callable[[list[str]], str]


def windows_shell_quote(argument: str) -> str:
    """Quote one argv item for cmd.exe while preserving CRT argv decoding."""
    if any(character in argument for character in ("\0", "\r", "\n")):
        raise ValueError("Click runner arguments cannot contain control characters.")
    result = ['"']
    backslashes = 0
    for character in argument:
        if character == "\\":
            backslashes += 1
            continue
        if character == '"':
            result.append("\\" * (backslashes * 2 + 1))
            result.append('"')
            backslashes = 0
            continue
        if backslashes:
            result.append("\\" * backslashes)
            backslashes = 0
        result.append(character)
    if backslashes:
        result.append("\\" * (backslashes * 2))
    result.append('"')
    return "".join(result)


def encode_runner_transport(arguments: list[str]) -> str:
    raw = json.dumps(arguments, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(zlib.compress(raw, level=9)).decode()


def decode_runner_transport(encoded: str) -> tuple[list[str] | None, str]:
    try:
        compressed = base64.b64decode(
            encoded.encode(), altchars=b"-_", validate=True
        )
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(compressed, MAX_RUNNER_TRANSPORT_BYTES + 1)
        if (
            len(raw) > MAX_RUNNER_TRANSPORT_BYTES
            or not decompressor.eof
            or decompressor.unconsumed_tail
            or decompressor.unused_data
        ):
            return None, "Click runner transport exceeded its bounded payload."
        value = json.loads(raw.decode())
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, zlib.error):
        return None, "Click runner transport was malformed."
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or "\x00" in item for item in value)
    ):
        return None, "Click runner transport did not contain a valid argv list."
    return value, ""


def windows_launcher_path_is_safe(value: str) -> bool:
    # Arguments after the launcher are encoded. These characters remain unsafe
    # in the two launcher paths because cmd.exe and PowerShell expand them even
    # inside double quotes under some configurations.
    return not any(character in value for character in ("%", "!", "$", "`"))


def default_runner_shell_command(arguments: list[str]) -> str:
    if os.name == "nt":
        if len(arguments) < 3 or not all(
            windows_launcher_path_is_safe(argument) for argument in arguments[:2]
        ):
            return "exit 2"
        transported = [
            arguments[1],
            "--encoded-runner",
            encode_runner_transport(arguments[2:]),
        ]
        # hooks.json already requires the Windows py launcher. Reuse its bare
        # command form here so the rewritten runner is valid in both cmd.exe
        # and PowerShell; a quoted executable path in command position is only
        # an expression in PowerShell unless prefixed with its call operator.
        command = "py -3 " + " ".join(
            windows_shell_quote(argument) for argument in transported
        )
        if len(command) > WINDOWS_COMMAND_LINE_LIMIT:
            return "exit 2"
        return command
    return shlex.join(arguments)


_runner_shell_renderer: RunnerShellRenderer = default_runner_shell_command


def install_runner_shell_renderer(
    renderer: RunnerShellRenderer,
) -> RunnerShellRenderer:
    """Install one host renderer for this Hook process and return its predecessor."""
    if not callable(renderer):
        raise TypeError("Click runner shell renderer must be callable.")
    global _runner_shell_renderer
    previous = _runner_shell_renderer
    _runner_shell_renderer = renderer
    return previous


def render_runner_shell_command(arguments: list[str]) -> str:
    return _runner_shell_renderer(arguments)
