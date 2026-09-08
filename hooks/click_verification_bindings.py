"""Exact environment, executable and workspace bindings for verification.

This module performs no state writes and never grants receipt reuse. Each caller
chooses a fresh authority boundary before collecting its binding records.
"""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import subprocess
import sys
from typing import Any

if __package__:
    from . import click_import_bootstrap
else:  # Installed launchers execute hooks directly.
    import click_import_bootstrap

(click_capability, click_host_coverage, click_inspection, click_observer_control, click_process, click_runtime_identity, click_verification_inputs,) = click_import_bootstrap.load_siblings(
    __package__, "click_capability", "click_host_coverage", "click_inspection", "click_observer_control", "click_process", "click_runtime_identity", "click_verification_inputs"
)

def hash_file_content(path: Path) -> str:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError:
        return ""
    return hasher.hexdigest()


class FileDigestStage:
    """Hash each unchanged file once within one binding collection stage.

    Never retain an instance across prepare, issuance, claim, or a subsequent
    source boundary. Stat identity avoids reusing a digest after an ordinary
    in-stage replacement; every new authority boundary always hashes afresh.
    Windows ctime is a creation timestamp on supported Python versions, so
    Windows retains a content read for every record, even within one stage.
    """

    def __init__(self, digest_file: Callable[[Path], str] = hash_file_content):
        self._digest_file = digest_file
        self._digests: dict[tuple[Any, ...], str] = {}

    @staticmethod
    def _identity(path: Path) -> tuple[Any, ...]:
        metadata = path.stat()
        return (
            os.path.normcase(str(path)), metadata.st_dev, metadata.st_ino,
            metadata.st_mode, metadata.st_size, metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    def __call__(self, path: Path) -> str:
        try:
            identity = self._identity(path)
            if os.name != "nt" and identity in self._digests:
                return self._digests[identity]
            digest = self._digest_file(path)
            # A write during hashing must not establish a reusable stage entry.
            if not digest or self._identity(path) != identity:
                return ""
        except OSError:
            return ""
        if os.name != "nt":
            self._digests[identity] = digest
        return digest


def verification_environment(*, cwd: Path) -> dict[str, str]:
    # Shell launchers add bookkeeping variables that do not change check
    # semantics and are not stable across the Hook process and its rewritten
    # runner. Keep user/project variables fingerprinted, but canonicalize these
    # launcher-owned values so an unchanged receipt remains portable.
    volatile = {
        "_",
        "__CF_USER_TEXT_ENCODING",
        "CMDCMDLINE",
        "CLICK_CONFIG_HOME",
        "COMMAND_MODE",
        "LC_CTYPE",
        "OLDPWD",
        "PROMPT",
        "PROMPT_COMMAND",
        "PS1",
        "PS2",
        "PLUGIN_DATA",
        "PLUGIN_ROOT",
        "SHLVL",
    }
    environment = {
        str(key): str(value)
        for key, value in os.environ.items()
        if str(key).upper() not in volatile and not str(key).startswith("=")
    }
    environment["PWD"] = str(cwd.resolve())
    return environment


def observer_environment(
    environment: dict[str, str], verification: Any
) -> dict[str, str]:
    """Bind the deterministic Python profile selected by authoritative mode."""
    normalized = dict(environment)
    if click_observer_control.mode(verification) == "authoritative":
        normalized["PYTHONHASHSEED"] = "0"
        normalized["PYTHONDONTWRITEBYTECODE"] = "1"
    return normalized


def verification_environment_key(key: str) -> str:
    return key.upper() if os.name == "nt" else key


def verification_environment_hmac(
    runner_token: str, domain: str, value: str
) -> str:
    secret = hashlib.sha256(runner_token.encode()).digest()
    message = f"click-verification-{domain}\0{value}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def verification_environment_binding(
    environment: dict[str, str], runner_token: str
) -> list[dict[str, str]]:
    records = []
    for key, value in environment.items():
        normalized_key = verification_environment_key(str(key))
        records.append(
            {
                "key_digest": verification_environment_hmac(
                    runner_token, "key", normalized_key
                ),
                "value_digest": verification_environment_hmac(
                    runner_token, "value", f"{normalized_key}\0{value}"
                ),
            }
        )
    return sorted(records, key=lambda item: item["key_digest"])


def verification_environment_binding_digest(
    binding: Any, runner_token: str
) -> str:
    try:
        canonical = json.dumps(
            binding, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError):
        return ""
    return verification_environment_hmac(runner_token, "binding", canonical)


def verification_environment_binding_is_authentic(
    binding: Any, digest: Any, runner_token: str
) -> bool:
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return False
    expected = verification_environment_binding_digest(binding, runner_token)
    return bool(expected and secrets.compare_digest(digest, expected))


def verification_host_coverage_binding_digest(
    coverage: Any, runner_token: str
) -> str:
    if not click_host_coverage.receipt_is_current(coverage):
        return ""
    canonical = json.dumps(coverage, sort_keys=True, separators=(",", ":"))
    return verification_environment_hmac(
        runner_token, "host-coverage", canonical
    )


def verification_host_coverage_binding_is_authentic(
    coverage: Any, digest: Any, runner_token: str
) -> bool:
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return False
    expected = verification_host_coverage_binding_digest(
        coverage, runner_token
    )
    return bool(expected and secrets.compare_digest(digest, expected))


def verification_environment_from_binding(
    binding: Any,
    runner_token: str,
    current_environment: dict[str, str],
) -> tuple[dict[str, str] | None, bool, str]:
    if not isinstance(binding, list) or not binding or len(binding) > 4096:
        return None, False, "Click verification runner environment binding was malformed."
    expected: dict[str, str] = {}
    for record in binding:
        if not isinstance(record, dict) or set(record) != {
            "key_digest",
            "value_digest",
        }:
            return None, False, "Click verification runner environment binding was malformed."
        key_digest = record.get("key_digest")
        value_digest = record.get("value_digest")
        if (
            not isinstance(key_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", key_digest)
            or not isinstance(value_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", value_digest)
            or key_digest in expected
        ):
            return None, False, "Click verification runner environment binding was malformed."
        expected[key_digest] = value_digest

    projected: dict[str, str] = {}
    matched: set[str] = set()
    drifted = False
    for key, value in current_environment.items():
        normalized_key = verification_environment_key(str(key))
        key_digest = verification_environment_hmac(
            runner_token, "key", normalized_key
        )
        expected_value = expected.get(key_digest)
        if expected_value is None:
            continue
        current_value = verification_environment_hmac(
            runner_token, "value", f"{normalized_key}\0{value}"
        )
        if not secrets.compare_digest(expected_value, current_value):
            drifted = True
        projected[str(key)] = str(value)
        matched.add(key_digest)
    if matched != set(expected):
        drifted = True
    return projected, drifted, ""


def executable_search_path(environment: dict[str, str], *, cwd: Path) -> str:
    """Resolve relative PATH entries as the verification child will from its cwd."""
    entries: list[str] = []
    for raw_entry in environment.get("PATH", os.defpath).split(os.pathsep):
        entry = Path(raw_entry) if raw_entry else cwd
        if not entry.is_absolute():
            entry = cwd / entry
        entries.append(str(entry.resolve()))
    return os.pathsep.join(entries)


def verification_executable_records(
    checks: list[dict[str, Any]],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
    file_content_digest: Callable[[Path], str] | None = None,
) -> list[dict[str, Any]] | None:
    effective_environment = environment or verification_environment(cwd=cwd)
    digest_file = file_content_digest or FileDigestStage()
    search_path = executable_search_path(effective_environment, cwd=cwd)
    def resolve_runtime(name: str) -> Path | None:
        resolved = shutil.which(name, path=search_path)
        if not resolved:
            return None
        try:
            candidate = Path(resolved)
            if not candidate.is_absolute():
                candidate = cwd / candidate
            return candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
    executables: list[dict[str, Any]] = []
    for check in checks:
        argv = check.get("argv")
        executable = str(argv[0]) if isinstance(argv, list) and argv else ""
        candidate = Path(executable)
        if executable and (
            candidate.is_absolute() or click_inspection.is_path_qualified_executable(executable)
        ):
            selected = candidate if candidate.is_absolute() else cwd / candidate
            resolved = shutil.which(str(selected))
        else:
            resolved = shutil.which(executable, path=search_path)
        item: dict[str, Any] = {"name": Path(executable).name.lower()}
        if resolved:
            try:
                resolved_candidate = Path(resolved)
                if not resolved_candidate.is_absolute():
                    resolved_candidate = cwd / resolved_candidate
                execution_path = Path(os.path.abspath(resolved_candidate))
                path = execution_path.resolve(strict=True)
                metadata = path.stat()
                item.update(
                    {
                        "selected_path": os.path.normcase(str(execution_path)),
                        "path": os.path.normcase(str(path)),
                        "size": int(metadata.st_size),
                        "mtime_ns": int(metadata.st_mtime_ns),
                        "content_digest": digest_file(path),
                        "_execution_path": str(execution_path),
                    }
                )
                item["runtime_identity"] = click_runtime_identity.collect(
                    list(argv),
                    cwd=cwd,
                    environment=effective_environment,
                    resolve_executable=resolve_runtime,
                    digest_file=digest_file,
                )
            except (OSError, RuntimeError):
                item["path"] = "unresolved"
        else:
            item["path"] = "missing"
        executables.append(item)
    if any(
        not isinstance(item.get("content_digest"), str)
        or not item.get("content_digest")
        for item in executables
    ):
        return None
    return executables


def verification_executable_payload(
    executables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            key: value
            for key, value in executable.items()
            if key != "_execution_path"
        }
        for executable in executables
    ]


def _environment_context(cwd: Path, environment: dict[str, str]) -> dict[str, Any]:
    environment_payload = json.dumps(
        sorted(
            (
                verification_environment_key(str(key)),
                str(value),
            )
            for key, value in environment.items()
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return {
        "cwd": os.path.normcase(str(cwd.resolve())),
        "os_name": os.name,
        "platform": sys.platform,
        "machine": platform.machine(),
        "python": list(sys.version_info[:3]),
        "environment_digest": hashlib.sha256(environment_payload).hexdigest(),
    }


def verification_environment_digest_from_records(
    executables: list[dict[str, Any]],
    *,
    cwd: Path,
    environment: dict[str, str],
) -> str:
    payload = {
        **_environment_context(cwd, environment),
        "executables": verification_executable_payload(executables),
    }
    return click_capability.digest(payload)


def verification_environment_digest(
    checks: list[dict[str, Any]], *, cwd: Path, environment: dict[str, str] | None = None
) -> str:
    effective_environment = environment or verification_environment(cwd=cwd)
    executables = verification_executable_records(
        checks, cwd=cwd, environment=effective_environment
    )
    if executables is None:
        return ""
    base_digest = verification_environment_digest_from_records(
        executables, cwd=cwd, environment=effective_environment
    )
    digest, _ = click_verification_inputs.context_digest(
        base_digest, checks, cwd=cwd
    )
    return digest



def collect_group_bindings(
    groups: dict[str, list[dict[str, Any]]],
    source_keys: set[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    digest_file: Callable[[Path], str] = hash_file_content,
    input_bindings: dict[str, dict[str, Any]] | None = None,
    runtime_bindings: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, str], dict[str, str]] | None:
    """Collect all source fingerprints with one strictly local digest stage."""
    stage = FileDigestStage(digest_file)
    context = _environment_context(cwd, environment)
    environments: dict[str, str] = {}
    executables: dict[str, str] = {}
    for source_key in source_keys:
        records = verification_executable_records(
            groups[source_key], cwd=cwd, environment=environment,
            file_content_digest=stage,
        )
        if records is None:
            return None
        executable_payload = verification_executable_payload(records)
        base_environment_digest = click_capability.digest({
            **context, "executables": executable_payload,
        })
        environments[source_key], input_binding = click_verification_inputs.context_digest(
            base_environment_digest, groups[source_key], cwd=cwd,
            file_content_digest=stage,
        )
        if input_bindings is not None:
            input_bindings[source_key] = input_binding
        if runtime_bindings is not None:
            runtime_bindings[source_key] = click_runtime_identity.group_binding(records)
        executables[source_key] = click_capability.digest(
            {"executables": executable_payload}
        )
    return environments, executables


def git_capture(cwd: Path, arguments: list[str]) -> bytes | None:
    executable, error = click_inspection.resolve_read_only_executable("git", workspace=cwd)
    if error or executable is None:
        return None
    try:
        result = click_process.run_argv(
            [
                executable,
                "--no-pager",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                *arguments,
            ],
            cwd=cwd,
            env=click_inspection.sanitized_git_environment(workspace=cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def hash_workspace_path(hasher: Any, root: Path, relative: str) -> None:
    encoded_path = os.fsencode(relative)
    hasher.update(len(encoded_path).to_bytes(8, "big"))
    hasher.update(encoded_path)
    target = root / relative
    try:
        metadata = target.lstat()
    except OSError:
        hasher.update(b"missing")
        return
    hasher.update(str(metadata.st_mode).encode())
    if target.is_symlink():
        try:
            hasher.update(os.fsencode(os.readlink(target)))
        except OSError:
            hasher.update(b"unreadable-link")
        return
    if not target.is_file():
        hasher.update(b"non-file")
        return
    try:
        with target.open("rb") as handle:
            while True:
                chunk = handle.read(128 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError:
        hasher.update(b"unreadable-file")


def git_workspace_snapshot(
    cwd: Path, protected_untracked: list[str] | None = None
) -> dict[str, Any] | None:
    root_output = git_capture(cwd, ["rev-parse", "--show-toplevel"])
    if root_output is None:
        return None
    root = Path(os.fsdecode(root_output.strip()))
    head_tree = git_capture(root, ["rev-parse", "--verify", "HEAD^{tree}"])
    has_head = head_tree is not None
    if not has_head and git_capture(root, ["rev-parse", "--verify", "HEAD"]) is not None:
        # A present but unreadable tree is an unavailable snapshot, not an
        # unborn repository. Normal committed snapshots need only one probe.
        return None
    diff_commands = (
        [["diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--"]]
        if has_head
        else [
            ["diff", "--binary", "--no-ext-diff", "--no-textconv", "--cached", "--"],
            ["diff", "--binary", "--no-ext-diff", "--no-textconv", "--"],
        ]
    )
    hasher = hashlib.sha256()
    if has_head:
        hasher.update(len(head_tree).to_bytes(8, "big"))
        hasher.update(head_tree)
    for arguments in diff_commands:
        diff = git_capture(root, arguments)
        if diff is None:
            return None
        hasher.update(len(diff).to_bytes(8, "big"))
        hasher.update(diff)

    untracked_output = git_capture(
        root, ["ls-files", "--others", "--exclude-standard", "-z"]
    )
    if untracked_output is None:
        return None
    current_untracked = [
        os.fsdecode(item) for item in untracked_output.split(b"\0") if item
    ]
    if protected_untracked is None:
        protected_untracked = [*current_untracked]
    for relative in sorted(protected_untracked):
        hash_workspace_path(hasher, root, relative)
    return {
        "root": str(root),
        "digest": hasher.hexdigest(),
        "protected_untracked": protected_untracked,
        "current_untracked": current_untracked,
    }
