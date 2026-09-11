#!/usr/bin/env python3
"""Pure read-only argv admission and classification policy for Click.

This leaf validates structured inspection requests, recognizes the bounded
local, Git, and SSH read surfaces, normalizes direct Bash and Windows command
lines, and classifies broad repository inventory. It never executes a process,
reads Click runtime state, or imports lifecycle, gate, or host adapters.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
from typing import Any

if __package__:
    from . import click_capability
else:  # Imported from a directly executed bundled hook.
    import click_capability


REQUEST_FIELDS = {"version", "commands", "fresh"}
MAX_COMMANDS = 8
READ_ONLY_COMMANDS = {
    "basename",
    "cat",
    "cmp",
    "cut",
    "diff",
    "dirname",
    "du",
    "file",
    "find",
    "grep",
    "get-content",
    "head",
    "ls",
    "pdfinfo",
    "pdftotext",
    "pwd",
    "readlink",
    "realpath",
    "rg",
    "sed",
    "sort",
    "stat",
    "tail",
    "test",
    "tree",
    "tr",
    "true",
    "type",
    "wc",
    "where",
    "which",
}
READ_ONLY_GIT_SUBCOMMANDS = {
    "check-ignore",
    "describe",
    "diff",
    "for-each-ref",
    "log",
    "ls-files",
    "ls-tree",
    "merge-base",
    "name-rev",
    "remote",
    "rev-parse",
    "show",
    "status",
}
GIT_DIFF_RENDERING_SUBCOMMANDS = {"diff", "log", "show"}
GIT_GLOBAL_ALLOWED_PREFIXES = ("--git-dir=", "--work-tree=")
GIT_GLOBAL_REJECTED_OPTIONS = {"-p", "--paginate", "-c", "--config-env"}
GIT_READ_ONLY_EXACT_OPTIONS = {
    "check-ignore": {
        "-q", "--quiet", "-v", "--verbose", "--stdin", "-z", "--no-index",
        "--non-matching",
    },
    "describe": {
        "--always", "--tags", "--all", "--long", "--exact-match", "--contains",
        "--debug", "--first-parent", "--broken",
    },
    "diff": {
        "--cached", "--staged", "--check", "--quiet", "--exit-code", "--stat",
        "--shortstat", "--numstat", "--name-only", "--name-status", "--summary",
        "--binary", "--patch", "--no-patch", "--raw", "--minimal", "--patience",
        "--histogram", "--no-color", "--relative", "--ignore-space-at-eol",
        "--ignore-all-space", "--ignore-space-change", "--ignore-blank-lines", "-w",
        "-b", "--no-ext-diff", "--no-textconv",
    },
    "for-each-ref": {"--ignore-case", "--omit-empty"},
    "log": {
        "--oneline", "--no-decorate", "--decorate", "--stat", "--shortstat",
        "--numstat", "--name-only", "--name-status", "--summary", "--no-merges",
        "--merges", "--first-parent", "--all", "--branches", "--tags", "--remotes",
        "--reflog", "--reverse", "--topo-order", "--date-order", "--author-date-order",
        "--parents", "--children", "--boundary", "--simplify-by-decoration",
        "--full-history", "--simplify-merges", "--ancestry-path", "--follow",
        "--no-patch", "--patch", "--abbrev-commit", "--no-color", "--no-ext-diff",
        "--no-textconv",
    },
    "ls-files": {
        "--cached", "--deleted", "--modified", "--others", "--ignored", "--stage",
        "--unmerged", "--killed", "--directory", "--no-empty-directory", "--eol",
        "--deduplicate", "--sparse", "--debug", "--exclude-standard", "--error-unmatch",
        "-c", "-d", "-m", "-o", "-i", "-s", "-u", "-k", "-t", "-v", "-f", "-z",
    },
    "ls-tree": {
        "-d", "-r", "-t", "-l", "--long", "-z", "--name-only", "--name-status",
        "--object-only", "--full-name", "--full-tree",
    },
    "merge-base": {"--all", "--octopus", "--independent", "--is-ancestor", "--fork-point"},
    "name-rev": {"--tags", "--all", "--stdin", "--name-only", "--no-undefined", "--always"},
    "remote": {"--all", "--push"},
    "rev-parse": {
        "--verify", "--short", "--abbrev-ref", "--symbolic-full-name", "--show-toplevel",
        "--show-prefix", "--show-cdup", "--git-dir", "--is-inside-work-tree",
        "--is-bare-repository", "--show-object-format", "--sq", "--revs-only",
        "--no-revs", "--flags", "--no-flags", "--quiet", "-q",
    },
    "show": {
        "--stat", "--shortstat", "--numstat", "--name-only", "--name-status", "--summary",
        "--binary", "--patch", "--no-patch", "--raw", "--minimal", "--patience",
        "--histogram", "--no-color", "--relative", "--ignore-space-at-eol",
        "--ignore-all-space", "--ignore-space-change", "--ignore-blank-lines", "-w", "-b",
        "--no-ext-diff", "--no-textconv", "--oneline", "--abbrev-commit",
    },
    "status": {
        "--short", "--porcelain", "--branch", "--show-stash", "--long",
        "--ignored", "--no-renames", "-s", "-b", "-sb",
    },
}
GIT_READ_ONLY_OPTION_PREFIXES = {
    "check-ignore": ("--exclude-standard",),
    "describe": ("--abbrev=", "--candidates=", "--match=", "--exclude="),
    "diff": (
        "--stat=", "--relative=", "--unified=", "--word-diff=", "--word-diff-regex=",
        "--src-prefix=", "--dst-prefix=", "--line-prefix=", "--ignore-submodules=",
        "--submodule=", "--diff-filter=",
    ),
    "for-each-ref": (
        "--sort=", "--count=", "--points-at=", "--merged=", "--no-merged=",
        "--contains=", "--no-contains=",
    ),
    "log": (
        "--date=", "--since=", "--after=", "--until=", "--before=", "--author=",
        "--committer=", "--grep=", "--max-count=", "--skip=", "--abbrev=",
        "--decorate=", "--stat=", "--relative=", "--unified=", "--word-diff=",
        "--word-diff-regex=", "--src-prefix=", "--dst-prefix=", "--line-prefix=",
        "--ignore-submodules=", "--submodule=", "--diff-filter=",
    ),
    "ls-files": (
        "--exclude=", "--exclude-from=", "--exclude-per-directory=",
        "--with-tree=", "--abbrev=",
    ),
    "ls-tree": ("--abbrev=",),
    "name-rev": ("--refs=", "--exclude="),
    "rev-parse": ("--short=", "--abbrev-ref=", "--path-format=", "--disambiguate="),
    "show": (
        "--date=", "--stat=", "--relative=", "--unified=", "--word-diff=",
        "--word-diff-regex=", "--src-prefix=", "--dst-prefix=", "--line-prefix=",
        "--ignore-submodules=", "--submodule=", "--diff-filter=",
    ),
    "status": ("--porcelain=", "--ignored=", "--find-renames="),
}
SED_READ_SCRIPT = re.compile(
    r"^\s*(?:\d+|\$)(?:\s*,\s*(?:\d+|\$))?\s*[pq]\s*$"
)
RG_OPTIONS_WITH_VALUES = {
    "-g",
    "--glob",
    "--iglob",
    "--ignore-file",
    "--max-depth",
    "--path-separator",
    "--sort",
    "--sortr",
    "-t",
    "--type",
    "-T",
    "--type-not",
}
SSH_TARGET = re.compile(r"^[A-Za-z0-9_.-]+(?:@[A-Za-z0-9_.-]+)?$")
SSH_READ_ONLY_GIT_SUBCOMMANDS = {"merge-base", "remote", "rev-parse", "status"}
GIT_REMOTE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def validate_request(
    raw: str, *, protocol_version: int = click_capability.PROTOCOL_VERSION
) -> tuple[dict[str, Any] | None, bool, str]:
    value, error = click_capability.decode_request(
        raw, "Inspection", version=protocol_version
    )
    if error:
        return None, False, error
    assert value is not None
    unknown = sorted(set(value) - REQUEST_FIELDS)
    if unknown:
        rendered = ", ".join(f"`{field}`" for field in unknown)
        return None, False, f"Inspection request contains unsupported field(s): {rendered}."
    fresh = value.get("fresh", False)
    if not isinstance(fresh, bool):
        return None, False, "Inspection `fresh` must be a boolean when provided."
    commands = value.get("commands")
    if not isinstance(commands, list) or not commands:
        return None, False, "Inspection `commands` must be a non-empty argv-list list."
    if len(commands) > MAX_COMMANDS:
        return None, False, f"Inspection may contain at most {MAX_COMMANDS} commands."
    normalized: list[list[str]] = []
    broad = False
    for index, raw_argv in enumerate(commands, start=1):
        argv, argv_error = click_capability.validate_argv(
            raw_argv, f"Inspection command {index}"
        )
        if argv_error:
            return None, False, argv_error
        assert argv is not None
        if not is_read_only_tokens(list(argv)):
            return (
                None,
                False,
                f"Inspection command {index} is not a supported read-only argv operation.",
            )
        broad = broad or is_broad_exploration_tokens(argv)
        normalized.append(argv)
    request = {"version": protocol_version, "commands": normalized}
    if fresh:
        request["fresh"] = True
    return request, broad, ""


def git_option_allowed(subcommand: str, token: str) -> bool:
    if token in GIT_READ_ONLY_EXACT_OPTIONS.get(subcommand, set()):
        return True
    if any(
        token.startswith(prefix)
        for prefix in GIT_READ_ONLY_OPTION_PREFIXES.get(subcommand, ())
    ):
        return True
    if subcommand in GIT_DIFF_RENDERING_SUBCOMMANDS and re.fullmatch(r"-U\d+", token):
        return True
    if subcommand == "log" and re.fullmatch(r"-\d+", token):
        return True
    return False

def is_read_only_git_remote_arguments(arguments: list[str]) -> bool:
    if not arguments or arguments[0] != "get-url":
        return False
    remote_names = [
        argument
        for argument in arguments[1:]
        if argument not in {"--", "--all", "--push"}
    ]
    return len(remote_names) == 1 and GIT_REMOTE_NAME.fullmatch(remote_names[0]) is not None


def parse_read_only_git_tokens(
    tokens: list[str],
) -> tuple[list[str], str, list[str]] | None:
    if not tokens or Path(tokens[0]).name.lower() not in {"git", "git.exe"}:
        return None
    global_arguments: list[str] = []
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-C":
            if index + 1 >= len(tokens):
                return None
            global_arguments.extend([token, tokens[index + 1]])
            index += 2
            continue
        if token.startswith(GIT_GLOBAL_ALLOWED_PREFIXES):
            global_arguments.append(token)
            index += 1
            continue
        if token in {"--no-pager", "--no-optional-locks"}:
            index += 1
            continue
        if (
            token in GIT_GLOBAL_REJECTED_OPTIONS
            or token.startswith("--config-env=")
            or (token.startswith("-c") and token != "-C")
        ):
            return None
        if token.startswith("-"):
            return None
        subcommand = token
        break
    else:
        return None

    if subcommand not in READ_ONLY_GIT_SUBCOMMANDS:
        return None
    arguments = tokens[index + 1 :]
    options_finished = False
    for argument in arguments:
        if options_finished:
            continue
        if argument == "--":
            options_finished = True
            continue
        if argument.startswith("-") and not git_option_allowed(subcommand, argument):
            return None
    if subcommand == "remote" and not is_read_only_git_remote_arguments(arguments):
        return None
    return global_arguments, subcommand, arguments


def git_subcommand(tokens: list[str]) -> str:
    parsed = parse_read_only_git_tokens(tokens)
    return parsed[1] if parsed is not None else ""


def build_read_only_git_argv(tokens: list[str]) -> tuple[list[str] | None, str]:
    parsed = parse_read_only_git_tokens(tokens)
    if parsed is None:
        return None, "Git argv is outside Click's supported read-only option policy."
    global_arguments, subcommand, arguments = parsed
    forced = (
        ["--no-ext-diff", "--no-textconv"]
        if subcommand in GIT_DIFF_RENDERING_SUBCOMMANDS
        else []
    )
    safe_config = [
        "-c",
        "core.fsmonitor=false",
        "-c",
        "diff.external=",
        "-c",
        "log.showSignature=false",
        "-c",
        "format.pretty=medium",
    ]
    return [
        "git",
        "--no-pager",
        "--no-optional-locks",
        *safe_config,
        *global_arguments,
        subcommand,
        *forced,
        *arguments,
    ], ""


def is_read_only_sed(tokens: list[str]) -> bool:
    index = 1
    quiet = False
    script = ""
    while index < len(tokens) and not script:
        token = tokens[index]
        if token in {"-n", "--quiet", "--silent"}:
            quiet = True
        elif token in {"-e", "--expression"}:
            index += 1
            if index >= len(tokens):
                return False
            script = tokens[index]
        elif token.startswith("-e") and len(token) > 2:
            script = token[2:]
        elif token.startswith("-"):
            return False
        else:
            script = token
        index += 1
    if not quiet or not script or not SED_READ_SCRIPT.fullmatch(script):
        return False
    if index < len(tokens) and tokens[index] == "--":
        index += 1
    return index < len(tokens) and all(not token.startswith("-") for token in tokens[index:])


def get_content_paths(tokens: list[str]) -> list[str] | None:
    if not tokens or Path(tokens[0]).name.lower() != "get-content":
        return None
    paths: list[str] = []
    index = 1
    while index < len(tokens):
        argument = tokens[index]
        lowered = argument.lower()
        if lowered == "-raw":
            index += 1
            continue
        if lowered in {"-path", "-literalpath"}:
            if index + 1 >= len(tokens):
                return None
            paths.append(tokens[index + 1])
            index += 2
            continue
        if argument.startswith("-"):
            return None
        paths.append(argument)
        index += 1
    return paths or None


def is_read_only_pdfinfo(tokens: list[str]) -> bool:
    """Accept metadata output only; pdfinfo has no output-file operand."""
    return bool(
        len(tokens) >= 2
        and Path(tokens[0]).name.lower() == "pdfinfo"
        and any(argument and not argument.startswith("-") for argument in tokens[1:])
    )


def is_stdout_only_pdftotext(tokens: list[str]) -> bool:
    """Require the explicit stdout operand so the default .txt write is impossible."""
    return bool(
        len(tokens) >= 3
        and Path(tokens[0]).name.lower() == "pdftotext"
        and tokens[-1] == "-"
        and any(argument and not argument.startswith("-") for argument in tokens[1:-1])
    )


def structured_ssh_parts(tokens: list[str]) -> tuple[str, list[str]] | None:
    if len(tokens) < 4 or Path(tokens[0]).name.lower() not in {"ssh", "ssh.exe"}:
        return None
    target = tokens[1]
    remote_argv = tokens[2:]
    if target.startswith("-") or not SSH_TARGET.fullmatch(target):
        return None
    if remote_argv[0] != "git":
        return None
    parsed = parse_read_only_git_tokens(remote_argv)
    if parsed is None or parsed[1] not in SSH_READ_ONLY_GIT_SUBCOMMANDS:
        return None
    if parsed[1] == "rev-parse":
        positional = [
            argument
            for argument in parsed[2]
            if argument != "--" and not argument.startswith("-")
        ]
        if positional != ["HEAD"]:
            return None
    return target, remote_argv


def is_path_qualified_executable(value: str) -> bool:
    return "/" in value or "\\" in value or bool(re.match(r"^[A-Za-z]:", value))


def is_local_read_only_tokens(tokens: list[str]) -> bool:
    if not tokens:
        return False
    if click_capability.ENVIRONMENT_ASSIGNMENT.match(tokens[0]):
        return False
    if is_path_qualified_executable(tokens[0]):
        return False
    executable = tokens[0].lower()
    if executable in {"git", "git.exe"}:
        return parse_read_only_git_tokens(tokens) is not None
    if executable not in READ_ONLY_COMMANDS:
        return False
    if executable == "get-content":
        return get_content_paths(tokens) is not None
    if executable == "pdfinfo":
        return is_read_only_pdfinfo(tokens)
    if executable == "pdftotext":
        return is_stdout_only_pdftotext(tokens)
    if executable == "sed":
        return is_read_only_sed(tokens)
    if executable == "file" and any(token in {"-C", "--compile"} for token in tokens[1:]):
        return False
    if executable == "find" and any(
        token in {
            "-delete", "-exec", "-execdir", "-fls", "-fprint", "-fprint0",
            "-fprintf", "-ok", "-okdir",
        }
        for token in tokens[1:]
    ):
        return False
    if executable == "rg" and any(
        token == "--pre" or token.startswith("--pre=") for token in tokens[1:]
    ):
        return False
    if executable in {"diff", "sort", "tree"} and any(
        token == "-o" or token.startswith("-o") or token.startswith("--output")
        for token in tokens[1:]
    ):
        return False
    if executable == "sort" and any(
        token.startswith("--compress-program") for token in tokens[1:]
    ):
        return False
    return True


def is_read_only_tokens(tokens: list[str]) -> bool:
    if not tokens:
        return False
    if is_path_qualified_executable(tokens[0]):
        return False
    if tokens[0].lower() in {"ssh", "ssh.exe"}:
        return structured_ssh_parts(tokens) is not None
    return is_local_read_only_tokens(tokens)


def direct_command_tokens(
    command: str, *, windows: bool | None = None
) -> tuple[list[str] | None, str]:
    windows_tokens = os.name == "nt" if windows is None else windows
    try:
        lexer = shlex.shlex(
            command,
            posix=not windows_tokens,
            punctuation_chars="".join(sorted(click_capability.SHELL_CONTROL_PUNCTUATION)),
        )
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return None, ""
    if not windows_tokens:
        return tokens, ""
    normalized_tokens: list[str] = []
    for token in tokens:
        if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
            token = token[1:-1]
        if '"' in token or "'" in token:
            return (
                None,
                "Click could not safely normalize this Windows command line. "
                "Use `click-gate inspect` with explicit argv JSON.",
            )
        normalized_tokens.append(token)
    return normalized_tokens, ""


def request_from_bash(
    command: str,
    *,
    windows: bool | None = None,
    protocol_version: int = click_capability.PROTOCOL_VERSION,
) -> tuple[dict[str, Any] | None, bool, str]:
    if not command.strip() or "\n" in command or "\r" in command or "`" in command:
        return None, False, ""
    tokens, token_error = direct_command_tokens(command, windows=windows)
    if token_error:
        return None, False, token_error
    if tokens is None:
        return None, False, ""
    commands: list[list[str]] = [[]]
    for token in tokens:
        if token == "&&":
            if not commands[-1]:
                return None, False, ""
            commands.append([])
            continue
        if token == "|":
            return (
                None,
                False,
                "Click structured inspection does not execute pipelines. Pass direct argv "
                "commands or narrow the read instead.",
            )
        if token and set(token).issubset(click_capability.SHELL_CONTROL_PUNCTUATION):
            return None, False, ""
        commands[-1].append(token)
    if not commands[-1]:
        return None, False, ""
    raw = json.dumps(
        {"version": protocol_version, "commands": commands},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    request, broad, error = validate_request(raw, protocol_version=protocol_version)
    if error and "not a supported read-only argv operation" in error:
        return None, False, ""
    return request, broad, error


def is_read_only_bash(command: str) -> bool:
    request, _, _ = request_from_bash(command)
    return request is not None


def targets_repository_root(targets: list[str]) -> bool:
    if not targets:
        return True
    return any(target.rstrip("/\\") in {"", ".", ".."} for target in targets)


def is_broad_exploration_tokens(tokens: list[str]) -> bool:
    executable, arguments = click_capability.command_parts(tokens)
    if executable == "rg" and "--files" in arguments:
        targets = click_capability.positional_arguments(arguments, RG_OPTIONS_WITH_VALUES)
        return targets_repository_root(targets)
    if executable == "find":
        targets = click_capability.positional_arguments(arguments)
        return targets_repository_root(targets[:1])
    if executable == "tree":
        targets = click_capability.positional_arguments(arguments)
        return targets_repository_root(targets)
    if executable == "ls":
        recursive = any(argument in {"-r", "--recursive"} for argument in arguments)
        if not recursive:
            return False
        targets = click_capability.positional_arguments(arguments)
        return targets_repository_root(targets)
    if executable == "git":
        subcommand = git_subcommand(tokens)
        if subcommand == "ls-files":
            index = tokens.index(subcommand)
            targets = click_capability.positional_arguments(
                [item.lower() for item in tokens[index + 1 :]]
            )
            return targets_repository_root(targets)
        if subcommand == "ls-tree":
            index = tokens.index(subcommand)
            remainder = [item.lower() for item in tokens[index + 1 :]]
            if "--" not in remainder:
                return True
            targets = remainder[remainder.index("--") + 1 :]
            return targets_repository_root(targets)
    return False
