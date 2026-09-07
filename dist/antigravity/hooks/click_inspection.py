"""Hardened inspection execution and compatibility facade for Click.

Pure argv admission and broad-read classification live in
``click_inspection_policy``. This module retains executable trust boundaries,
shell-free execution, Git and SSH runtime hardening, and output redaction while
re-exporting the established inspection surface. Observation receipts and
contract state remain outside this boundary.
"""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit

if __package__:
    from . import click_capability, click_inspection_policy, click_process
else:  # Executed directly from the bundled hooks directory.
    import click_capability
    import click_inspection_policy
    import click_process


REQUEST_FIELDS = click_inspection_policy.REQUEST_FIELDS
MAX_COMMANDS = click_inspection_policy.MAX_COMMANDS
READ_ONLY_COMMANDS = click_inspection_policy.READ_ONLY_COMMANDS
READ_ONLY_GIT_SUBCOMMANDS = click_inspection_policy.READ_ONLY_GIT_SUBCOMMANDS
GIT_DIFF_RENDERING_SUBCOMMANDS = (
    click_inspection_policy.GIT_DIFF_RENDERING_SUBCOMMANDS
)
GIT_GLOBAL_ALLOWED_PREFIXES = click_inspection_policy.GIT_GLOBAL_ALLOWED_PREFIXES
GIT_GLOBAL_REJECTED_OPTIONS = click_inspection_policy.GIT_GLOBAL_REJECTED_OPTIONS
GIT_READ_ONLY_EXACT_OPTIONS = click_inspection_policy.GIT_READ_ONLY_EXACT_OPTIONS
GIT_READ_ONLY_OPTION_PREFIXES = click_inspection_policy.GIT_READ_ONLY_OPTION_PREFIXES
SED_READ_SCRIPT = click_inspection_policy.SED_READ_SCRIPT
RG_OPTIONS_WITH_VALUES = click_inspection_policy.RG_OPTIONS_WITH_VALUES
SSH_TARGET = click_inspection_policy.SSH_TARGET
SSH_READ_ONLY_GIT_SUBCOMMANDS = (
    click_inspection_policy.SSH_READ_ONLY_GIT_SUBCOMMANDS
)
GIT_REMOTE_NAME = click_inspection_policy.GIT_REMOTE_NAME

validate_request = click_inspection_policy.validate_request
git_option_allowed = click_inspection_policy.git_option_allowed
is_read_only_git_remote_arguments = (
    click_inspection_policy.is_read_only_git_remote_arguments
)
parse_read_only_git_tokens = click_inspection_policy.parse_read_only_git_tokens
git_subcommand = click_inspection_policy.git_subcommand
build_read_only_git_argv = click_inspection_policy.build_read_only_git_argv
is_read_only_sed = click_inspection_policy.is_read_only_sed
get_content_paths = click_inspection_policy.get_content_paths
is_read_only_pdfinfo = click_inspection_policy.is_read_only_pdfinfo
is_stdout_only_pdftotext = click_inspection_policy.is_stdout_only_pdftotext
structured_ssh_parts = click_inspection_policy.structured_ssh_parts
is_path_qualified_executable = click_inspection_policy.is_path_qualified_executable
is_local_read_only_tokens = click_inspection_policy.is_local_read_only_tokens
is_read_only_tokens = click_inspection_policy.is_read_only_tokens
direct_command_tokens = click_inspection_policy.direct_command_tokens
request_from_bash = click_inspection_policy.request_from_bash
is_read_only_bash = click_inspection_policy.is_read_only_bash
targets_repository_root = click_inspection_policy.targets_repository_root
is_broad_exploration_tokens = (
    click_inspection_policy.is_broad_exploration_tokens
)


RunRequest = Callable[[dict[str, Any]], int]
RenderRunnerCommand = Callable[[list[str]], str]


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        pass
    try:
        root_stat = root.stat()
        current = path if path.is_dir() else path.parent
        for candidate in (current, *current.parents):
            if os.path.samestat(candidate.stat(), root_stat):
                return True
    except OSError:
        pass
    return False


def valid_git_worktree_marker(marker: Path) -> bool:
    """Recognize real Git metadata, not an unrelated empty ancestor named .git."""
    try:
        if marker.is_dir():
            return (marker / "HEAD").is_file() and (
                (marker / "objects").is_dir() or (marker / "commondir").is_file()
            )
        if not marker.is_file():
            return False
        first_line = marker.read_text(encoding="utf-8", errors="strict").splitlines()[0]
        if not first_line.lower().startswith("gitdir:"):
            return False
        target = Path(first_line.split(":", 1)[1].strip())
        if not target.is_absolute():
            target = marker.parent / target
        target = target.resolve(strict=True)
        return (target / "HEAD").is_file() and (
            (target / "objects").is_dir() or (target / "commondir").is_file()
        )
    except (IndexError, OSError, RuntimeError, UnicodeError):
        return False


def workspace_boundary(workspace: Path | None = None) -> Path:
    candidate = workspace or Path.cwd()
    try:
        current = candidate.resolve()
    except (OSError, RuntimeError):
        current = Path(os.path.abspath(candidate))
    for possible in (current, *current.parents):
        if valid_git_worktree_marker(possible / ".git"):
            return possible
    return current


def git_metadata_present(workspace: Path | None = None) -> bool:
    root = workspace_boundary(workspace)
    return valid_git_worktree_marker(root / ".git")


def unsafe_inherited_environment_key(key: str) -> bool:
    upper = key.upper()
    return upper.startswith(("LD_", "DYLD_")) or upper in {"GCONV_PATH", "LOCPATH"}


def sanitized_executable_path(
    source: str | None = None, *, workspace: Path | None = None
) -> str:
    root = workspace_boundary(workspace)
    value = os.environ.get("PATH", "") if source is None else source
    entries: list[str] = []
    seen: set[str] = set()
    for raw_entry in value.split(os.pathsep):
        normalized_entry = raw_entry.strip()
        if (
            len(normalized_entry) >= 2
            and normalized_entry[0] == normalized_entry[-1]
            and normalized_entry[0] in {'"', "'"}
        ):
            normalized_entry = normalized_entry[1:-1]
        normalized_entry = os.path.expandvars(normalized_entry)
        if not normalized_entry or not os.path.isabs(normalized_entry):
            continue
        lexical = Path(os.path.abspath(normalized_entry))
        if path_is_within(lexical, root):
            continue
        try:
            resolved = Path(normalized_entry).resolve()
        except (OSError, RuntimeError):
            continue
        if path_is_within(resolved, root):
            continue
        rendered = str(resolved)
        key = os.path.normcase(rendered)
        if key in seen:
            continue
        seen.add(key)
        entries.append(rendered)
    return os.pathsep.join(entries)


def resolve_read_only_executable(
    executable: str, *, workspace: Path | None = None
) -> tuple[str | None, str]:
    if is_path_qualified_executable(executable):
        return None, "read-only executables must use an unqualified trusted name"
    root = workspace_boundary(workspace)
    inherited = shutil.which(executable)
    if inherited is not None:
        inherited_lexical = Path(os.path.abspath(inherited))
        if path_is_within(inherited_lexical, root):
            return None, "the inherited executable path is inside the workspace"
        try:
            inherited_path = Path(inherited).resolve(strict=True)
        except (OSError, RuntimeError):
            return None, "the inherited executable path could not be resolved safely"
        if path_is_within(inherited_path, root):
            return None, "the inherited executable resolves inside the workspace"
    sanitized_path = sanitized_executable_path(workspace=root)
    resolved = shutil.which(executable, path=sanitized_path)
    if resolved is None:
        return None, "the executable was not found on Click's sanitized PATH"
    resolved_lexical = Path(os.path.abspath(resolved))
    if path_is_within(resolved_lexical, root):
        return None, "the executable path is inside the workspace"
    try:
        resolved_path = Path(resolved).resolve(strict=True)
    except (OSError, RuntimeError):
        return None, "the executable path could not be resolved safely"
    if path_is_within(resolved_path, root):
        return None, "the executable resolves inside the workspace"
    if not resolved_path.is_file():
        return None, "the executable does not resolve to a regular file"
    return str(resolved_path), ""


def sanitized_read_only_environment(*, workspace: Path | None = None) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"PATH", "RIPGREP_CONFIG_PATH"}
        and not unsafe_inherited_environment_key(key)
    }
    environment["PATH"] = sanitized_executable_path(workspace=workspace)
    return environment


def sanitized_git_environment(
    source: dict[str, str] | None = None, *, workspace: Path | None = None
) -> dict[str, str]:
    inherited = os.environ if source is None else source
    environment = {
        key: value
        for key, value in inherited.items()
        if not key.upper().startswith("GIT_")
        and key.upper() != "PATH"
        and not unsafe_inherited_environment_key(key)
    }
    environment["PATH"] = sanitized_executable_path(
        inherited.get("PATH", ""), workspace=workspace
    )
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    return environment


def execution_argv(argv: list[str]) -> list[str]:
    parts = structured_ssh_parts(argv)
    if parts is None:
        # Runtime hardening replaces argv[0] with a trusted absolute path.
        # Keep the validated request immutable so receipts and cache bindings
        # continue to describe the command the caller actually requested.
        return list(argv)
    target, remote_argv = parts
    safe_git_argv, error = build_read_only_git_argv(remote_argv)
    if error or safe_git_argv is None:
        return argv
    return [
        argv[0], "-n", "-F", "none",
        "-o", "BatchMode=yes",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "PasswordAuthentication=no",
        "-o", "NumberOfPasswordPrompts=0",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UpdateHostKeys=no",
        "-o", "ConnectTimeout=10",
        "-o", "ConnectionAttempts=1",
        "-o", "ServerAliveInterval=5",
        "-o", "ServerAliveCountMax=1",
        "-o", "ClearAllForwardings=yes",
        "-o", "ForwardAgent=no",
        "-o", "PermitLocalCommand=no",
        "-o", "RemoteCommand=none",
        "-o", "RequestTTY=no",
        target,
        shlex.join(safe_git_argv),
    ]


def is_git_remote_output_request(argv: list[str]) -> bool:
    parts = structured_ssh_parts(argv)
    git_argv = parts[1] if parts is not None else argv
    return git_subcommand(git_argv) == "remote"


def redact_git_remote_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        if "://" not in value:
            return value
        scheme, remainder = value.split("://", 1)
        remainder = remainder.rsplit("@", 1)[-1]
        return f"{scheme}://{remainder.split('?', 1)[0].split('#', 1)[0]}"
    if parsed.scheme and parsed.netloc:
        netloc = parsed.netloc.rsplit("@", 1)[-1]
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    if re.fullmatch(r"[^/@\s]+@[^/\s:]+:.+", value):
        return value.rsplit("@", 1)[-1].split("?", 1)[0].split("#", 1)[0]
    return value


def redact_git_remote_output(data: bytes) -> bytes:
    lines = []
    for line in data.decode("utf-8", errors="replace").splitlines(keepends=True):
        value = line.rstrip("\r\n")
        lines.append(redact_git_remote_url(value) + line[len(value) :])
    return "".join(lines).encode()


def write_runner_stream(handle: Any | None, data: bytes, *, error: bool = False) -> None:
    if handle is not None:
        try:
            handle.write(data)
        except TypeError:
            handle.write(data.decode("utf-8", errors="replace"))
        return
    stream = sys.stderr if error else sys.stdout
    target = getattr(stream, "buffer", stream)
    try:
        target.write(data)
    except TypeError:
        target.write(data.decode("utf-8", errors="replace"))
    target.flush()


class _RunnerStreamTarget:
    """Binary writer that preserves the runner's existing output adapters."""

    def __init__(self, handle: Any | None, *, error: bool) -> None:
        self.handle = handle
        self.error = error

    def write(self, data: bytes) -> int:
        write_runner_stream(self.handle, data, error=self.error)
        return len(data)

    def flush(self) -> None:
        if self.handle is not None:
            try:
                self.handle.flush()
            except (AttributeError, OSError, ValueError):
                pass


def execute_argv_commands(
    commands: list[list[str]],
    stdout_file: Any | None = None,
    stderr_file: Any | None = None,
    *,
    trusted_read_only: bool = False,
    workspace: Path | None = None,
    environment: dict[str, str] | None = None,
    capture_output: dict[str, Any] | None = None,
    capture_limit_bytes: int = 64 * 1024,
    capture_tee: bool = True,
) -> int:
    exit_code = 0
    for argv in commands:
        try:
            redact = is_git_remote_output_request(argv)
            prepared = execution_argv(argv)
            if trusted_read_only:
                executable, error = resolve_read_only_executable(argv[0], workspace=workspace)
                if error or executable is None:
                    write_runner_stream(
                        stderr_file,
                        (
                            "Click rejected the read-only executable at execution time: "
                            f"{error}.\n"
                        ).encode(),
                        error=True,
                    )
                    return 2
                prepared[0] = executable
            effective_environment = (
                sanitized_read_only_environment(workspace=workspace)
                if trusted_read_only
                else environment
            )
            if capture_output is not None and not redact:
                captured = click_process.run_argv_captured(
                    prepared,
                    target=True,
                    stdout_target=(
                        _RunnerStreamTarget(stdout_file, error=False)
                        if capture_tee else None
                    ),
                    stderr_target=(
                        _RunnerStreamTarget(stderr_file, error=True)
                        if capture_tee else None
                    ),
                    capture_limit_bytes=capture_limit_bytes,
                    env=effective_environment,
                )
                capture_output.clear()
                capture_output.update(status="complete", process=captured)
                exit_code = int(captured.returncode)
                result = None
            else:
                if capture_output is not None:
                    capture_output.clear()
                    capture_output.update(
                        status="unsupported", reason="redacted-command-output"
                    )
                result = click_process.run_argv(
                    prepared,
                    target=True,
                    stdout=subprocess.PIPE if redact else stdout_file,
                    stderr=subprocess.PIPE if redact else stderr_file,
                    env=effective_environment,
                )
            if redact:
                assert result is not None
                write_runner_stream(stdout_file, redact_git_remote_output(result.stdout or b""))
                write_runner_stream(
                    stderr_file,
                    redact_git_remote_output(result.stderr or b""),
                    error=True,
                )
            if result is not None:
                exit_code = int(result.returncode)
        except OSError as exc:
            message = f"Click could not start `{argv[0]}`: {exc}\n"
            if stderr_file is None:
                sys.stderr.write(message)
            else:
                stderr_file.write(message.encode())
            exit_code = 127
            if capture_output is not None:
                capture_output.clear()
                capture_output.update(status="missing", reason="process-start-failed")
        if exit_code != 0:
            break
    return exit_code


def execute_native_get_content(
    argv: list[str], stdout_file: Any | None, stderr_file: Any | None
) -> int | None:
    if Path(argv[0]).name.lower() != "get-content":
        return None
    paths = get_content_paths(argv)
    if paths is None:
        write_runner_stream(
            stderr_file,
            b"Click Get-Content inspection supports only positional paths, "
            b"-Path, -LiteralPath, and -Raw.\n",
            error=True,
        )
        return 2
    try:
        for path in paths:
            write_runner_stream(stdout_file, Path(path).read_bytes())
    except OSError as exc:
        write_runner_stream(
            stderr_file, f"Click could not read {path}: {exc}\n".encode(), error=True
        )
        return 1
    return 0


def execute_read_only_git(
    argv: list[str],
    stdout_file: Any | None,
    stderr_file: Any | None,
    *,
    workspace: Path | None = None,
) -> int:
    safe_argv, error = build_read_only_git_argv(argv)
    if error or safe_argv is None:
        write_runner_stream(
            stderr_file,
            f"Click rejected Git inspection at execution time: {error}\n".encode(),
            error=True,
        )
        return 2
    executable, executable_error = resolve_read_only_executable(argv[0], workspace=workspace)
    if executable_error or executable is None:
        write_runner_stream(
            stderr_file,
            (
                "Click rejected the Git executable at execution time: "
                f"{executable_error}.\n"
            ).encode(),
            error=True,
        )
        return 2
    safe_argv[0] = executable
    try:
        redact = is_git_remote_output_request(argv)
        result = click_process.run_argv(
            safe_argv,
            stdout=subprocess.PIPE if redact else stdout_file,
            stderr=subprocess.PIPE if redact else stderr_file,
            env=sanitized_git_environment(workspace=workspace),
        )
        if redact:
            write_runner_stream(stdout_file, redact_git_remote_output(result.stdout or b""))
            write_runner_stream(
                stderr_file,
                redact_git_remote_output(result.stderr or b""),
                error=True,
            )
        return int(result.returncode)
    except OSError as exc:
        write_runner_stream(
            stderr_file, f"Click could not start `git`: {exc}\n".encode(), error=True
        )
        return 127


def execute_commands(
    commands: list[list[str]],
    stdout_file: Any | None = None,
    stderr_file: Any | None = None,
    *,
    workspace: Path | None = None,
) -> int:
    for argv in commands:
        native_result = execute_native_get_content(argv, stdout_file, stderr_file)
        if native_result is not None:
            if native_result != 0:
                return native_result
            continue
        if argv[0].lower() in {"git", "git.exe"}:
            exit_code = execute_read_only_git(
                argv, stdout_file, stderr_file, workspace=workspace
            )
        else:
            exit_code = execute_argv_commands(
                [argv],
                stdout_file,
                stderr_file,
                trusted_read_only=True,
                workspace=workspace,
            )
        if exit_code != 0:
            return exit_code
    return 0


def runner_command(
    request: dict[str, Any],
    *,
    runner_script: Path,
    render_command: RenderRunnerCommand,
) -> str:
    return render_command(
        [
            sys.executable,
            str(runner_script),
            "run-inspection-once",
            click_capability.encode_request(request),
        ]
    )


def run_once(
    arguments: list[str],
    *,
    run_request: RunRequest,
    protocol_version: int = click_capability.PROTOCOL_VERSION,
) -> int:
    if len(arguments) != 1:
        sys.stderr.write("usage: click_gate.py run-inspection-once <request>\n")
        return 2
    raw, error = click_capability.decode_encoded_request(arguments[0], "inspection")
    if error:
        sys.stderr.write(f"{error}\n")
        return 2
    request, _, error = validate_request(raw, protocol_version=protocol_version)
    if error:
        sys.stderr.write(f"{error}\n")
        return 2
    assert request is not None
    return run_request(request)
