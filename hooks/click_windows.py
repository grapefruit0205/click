#!/usr/bin/env python3
"""Windows launcher bridge for Click.

The batch launcher selects an available Python 3 interpreter. This bridge keeps
that exact interpreter for rewritten Click runners so a missing or broken
`py -3` launcher cannot reappear after the Hook itself starts successfully.
"""

from __future__ import annotations

import os
from pathlib import Path

if __package__:
    from . import click_import_bootstrap
else:  # Executed directly from the bundled hooks directory.
    import click_import_bootstrap


(click_hook, click_runner_transport) = click_import_bootstrap.load_siblings(
    __package__, "click_hook", "click_runner_transport"
)


def _powershell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _runner_shell_command(arguments: list[str]) -> str:
    if os.name != "nt":
        return click_runner_transport.default_runner_shell_command(arguments)
    if len(arguments) < 3:
        return "exit 2"
    try:
        interpreter = Path(arguments[0]).resolve(strict=True)
        script_path = Path(arguments[1]).resolve(strict=True)
    except (OSError, RuntimeError):
        return "exit 2"
    if not interpreter.is_file() or not script_path.is_file():
        return "exit 2"
    if not all(
        click_runner_transport.windows_launcher_path_is_safe(value)
        for value in (str(interpreter), str(script_path))
    ):
        return "exit 2"

    encoded = click_runner_transport.encode_runner_transport(arguments[2:])
    script = " ".join(
        (
            "&",
            _powershell_single_quote(str(interpreter)),
            _powershell_single_quote(str(script_path)),
            "--encoded-runner",
            _powershell_single_quote(encoded),
        )
    )
    command = (
        "powershell.exe -NoProfile -NonInteractive -Command "
        + click_runner_transport.windows_shell_quote(script)
    )
    if len(command) > click_runner_transport.WINDOWS_COMMAND_LINE_LIMIT:
        return "exit 2"
    return command


click_runner_transport.register_host_renderer("codex", _runner_shell_command)


def main() -> int:
    if os.name == "nt":
        click_runner_transport.install_runner_shell_renderer(_runner_shell_command)
    return click_hook.main()


if __name__ == "__main__":
    raise SystemExit(main())
