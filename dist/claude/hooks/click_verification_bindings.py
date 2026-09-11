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
    Windows retains a content read for every record, even within one stage. A
    Windows metadata-only transition gets one stable-content retry; a content
    transition or a second metadata transition still fails closed.
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
            if not digest:
                return ""
            after = self._identity(path)
            if after != identity:
                if os.name != "nt":
                    return ""
                retry_digest = self._digest_file(path)
                if (
                    not retry_digest
                    or retry_digest != digest
                    or self._identity(path) != after
                ):
                    return ""
                digest = retry_digest
                identity = after
        except OSError:
            return ""
        if os.name != "nt":
            self._digests[identity] = digest
        return digest


# The verification environment binds the variables that change how a check
# runs: interpreter and toolchain configuration, locale, time zone, module and
# package resolution, proxies and certificate bundles. Everything else that a
# host, IDE, agent or shell adds is bookkeeping: it differs between the Hook
# process and its rewritten runner and would otherwise turn every session into
# a new environment. Owners extend the fingerprint for project variables with
# `.click/environment.json`; an extension can only add reruns, never reuse.
ENVIRONMENT_FINGERPRINT_KEYS = frozenset({
    "PATH", "PATHEXT", "PWD", "HOME", "USERPROFILE", "SHELL", "COMSPEC",
    "SYSTEMROOT", "SYSTEMDRIVE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
    "TMPDIR", "TMP", "TEMP", "TZ", "LANG", "LANGUAGE", "CI",
    "SOURCE_DATE_EPOCH", "NO_COLOR", "FORCE_COLOR", "PY_COLORS", "VIRTUAL_ENV",
    "PYENV_VERSION", "PIPENV_ACTIVE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "ALL_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE", "DOCKER_HOST", "KUBECONFIG", "JAVA_HOME", "ANDROID_HOME",
})
ENVIRONMENT_FINGERPRINT_PREFIXES = (
    "PYTHON", "LC_", "XDG_", "CONDA_", "PIP_", "UV_", "POETRY_", "PDM_",
    "HATCH_", "TOX_", "PYTEST_", "COVERAGE_", "NODE_", "NPM_CONFIG_", "YARN_",
    "PNPM_", "BUN_", "JEST_", "VITEST", "DENO_", "GO", "CGO_", "CARGO_",
    "RUST", "JAVA_", "JDK_", "GRADLE_", "MAVEN_", "DOTNET_", "NUGET_", "LD_",
    "DYLD_", "CLICOLOR", "GIT_",
)
# Launcher-owned values that would otherwise match an accepted prefix.
ENVIRONMENT_VOLATILE_KEYS = frozenset({
    "LC_CTYPE", "GIT_EDITOR", "GIT_PAGER", "GIT_TERMINAL_PROMPT", "GIT_ASKPASS",
    "GIT_PREFIX", "GIT_EXEC_PATH", "PYTHONUNBUFFERED",
})
ENVIRONMENT_POLICY_PATH = Path(".click") / "environment.json"
ENVIRONMENT_POLICY_VERSION = 1
MAX_ENVIRONMENT_POLICY_BYTES = 16 * 1024
MAX_ENVIRONMENT_POLICY_PATTERNS = 256
_ENVIRONMENT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}\*?$")
_ENVIRONMENT_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _environment_policy_patterns(cwd: Path) -> tuple[set[str], set[str]]:
    """Return (exact, prefixes) that an owner declared for this workspace.

    The file is read from the working tree because it can only widen what is
    fingerprinted. A missing, oversized or malformed file adds nothing.
    """
    root = cwd
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".git").exists():
            root = candidate
            break
    path = root / ENVIRONMENT_POLICY_PATH
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ENVIRONMENT_POLICY_BYTES:
            return set(), set()
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set(), set()
    patterns = value.get("fingerprint") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "fingerprint"}
        or value.get("version") != ENVIRONMENT_POLICY_VERSION
        or not isinstance(patterns, list)
        or len(patterns) > MAX_ENVIRONMENT_POLICY_PATTERNS
        or any(not isinstance(item, str) or _ENVIRONMENT_PATTERN.fullmatch(item) is None for item in patterns)
    ):
        return set(), set()
    exact = {item.upper() for item in patterns if not item.endswith("*")}
    prefixes = {item[:-1].upper() for item in patterns if item.endswith("*")}
    return exact, prefixes


def environment_key_is_fingerprinted(key: str, *, exact: set[str] = frozenset(), prefixes: set[str] = frozenset()) -> bool:
    if _ENVIRONMENT_IDENTIFIER.fullmatch(key) is None:
        # A shell drops names that are not identifiers before the runner
        # starts; binding them could never be reproduced by the runner.
        return False
    upper = key.upper()
    if upper in ENVIRONMENT_VOLATILE_KEYS:
        return False
    return bool(
        upper in ENVIRONMENT_FINGERPRINT_KEYS
        or upper in exact
        or upper.startswith(ENVIRONMENT_FINGERPRINT_PREFIXES)
        or any(upper.startswith(prefix) for prefix in prefixes)
    )


# Shell launchers add bookkeeping variables that do not change check
# semantics and are not stable across the Hook process and its rewritten
# runner. The execution environment handed to the child keeps every other
# variable; only the fingerprint subset above binds the receipt.
ENVIRONMENT_LAUNCHER_KEYS = frozenset({
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
})


def verification_environment(*, cwd: Path) -> dict[str, str]:
    """Return the environment the verification child runs with."""
    environment = {
        str(key): str(value)
        for key, value in os.environ.items()
        if str(key).upper() not in ENVIRONMENT_LAUNCHER_KEYS
        and _ENVIRONMENT_IDENTIFIER.fullmatch(str(key)) is not None
    }
    environment["PWD"] = str(cwd.resolve())
    return environment


def environment_fingerprint(environment: dict[str, str], *, cwd: Path | None = None) -> dict[str, str]:
    """Return the subset of an execution environment that binds a receipt."""
    exact: set[str] = set()
    prefixes: set[str] = set()
    try:
        if cwd is None and environment.get("PWD"):
            cwd = Path(str(environment["PWD"]))
        if cwd is not None:
            exact, prefixes = _environment_policy_patterns(cwd.resolve())
    except (OSError, RuntimeError, ValueError, NotImplementedError):
        exact, prefixes = set(), set()
    return {
        str(key): str(value)
        for key, value in environment.items()
        if environment_key_is_fingerprinted(str(key), exact=exact, prefixes=prefixes)
    }


def observer_environment(
    environment: dict[str, str], verification: Any
) -> dict[str, str]:
    """Bind capture defaults without overriding explicit settings in auto mode."""
    normalized = dict(environment)
    selected = click_observer_control.mode(verification)
    if selected == "authoritative":
        normalized["PYTHONHASHSEED"] = "0"
        normalized["PYTHONDONTWRITEBYTECODE"] = "1"
    elif selected == "auto" and isinstance(verification.get("authoritative_observer"), dict):
        normalized.setdefault("PYTHONHASHSEED", "0")
        normalized.setdefault("PYTHONDONTWRITEBYTECODE", "1")
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
    for key, value in environment_fingerprint(environment).items():
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

    # The child keeps the full execution environment. Only the fingerprint
    # subset is compared with the prepared binding; a changed or missing
    # fingerprinted value rebinds the receipt to what actually runs.
    matched: set[str] = set()
    drifted = False
    for key, value in environment_fingerprint(current_environment).items():
        normalized_key = verification_environment_key(str(key))
        key_digest = verification_environment_hmac(
            runner_token, "key", normalized_key
        )
        expected_value = expected.get(key_digest)
        if expected_value is None:
            drifted = True
            continue
        current_value = verification_environment_hmac(
            runner_token, "value", f"{normalized_key}\0{value}"
        )
        if not secrets.compare_digest(expected_value, current_value):
            drifted = True
        matched.add(key_digest)
    if matched != set(expected):
        drifted = True
    return dict(current_environment), drifted, ""


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
            # Content, size, selected path, and the runtime/install-tree
            # identity already bind execution semantics. Windows can expose
            # launcher timestamp churn across short-lived Hook and runner
            # processes even when those content bindings are unchanged.
            if key not in {"_execution_path", "mtime_ns"}
        }
        for executable in executables
    ]


def verification_executable_component_digests(
    executables: list[dict[str, Any]],
) -> dict[str, str]:
    """Return content-free diagnostics for executable binding drift."""

    components = {
        "selection": [
            {
                key: executable.get(key)
                for key in ("name", "selected_path", "path")
            }
            for executable in executables
        ],
        "content": [
            {
                key: executable.get(key)
                for key in ("size", "content_digest")
            }
            for executable in executables
        ],
        "runtime": [
            executable.get("runtime_identity")
            for executable in executables
        ],
    }
    for executable_index, executable in enumerate(executables):
        runtime_identity = executable.get("runtime_identity")
        runtime_components = (
            runtime_identity.get("component_digests")
            if isinstance(runtime_identity, dict)
            else None
        )
        if not isinstance(runtime_components, dict):
            continue
        for role, digest in runtime_components.items():
            if isinstance(role, str) and isinstance(digest, str):
                components[f"runtime:{executable_index}:{role}"] = [digest]
    return {
        name: click_capability.digest({"executables": payload})
        for name, payload in components.items()
    }


def _environment_context(cwd: Path, environment: dict[str, str]) -> dict[str, Any]:
    environment_payload = json.dumps(
        sorted(
            (
                verification_environment_key(str(key)),
                str(value),
            )
            for key, value in environment_fingerprint(environment, cwd=cwd).items()
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
    executable_component_bindings: dict[str, dict[str, str]] | None = None,
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
        if executable_component_bindings is not None:
            executable_component_bindings[source_key] = (
                verification_executable_component_digests(records)
            )
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
