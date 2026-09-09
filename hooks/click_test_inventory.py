"""Authorized bootstrap analysis of supported Python and Node test inventories.

All results are candidate-only. This module never changes Click authority,
policy, verification receipts or the user's Git index.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

if __package__:
    from . import click_import_bootstrap
else:
    import click_import_bootstrap

(click_collector_runtime, click_verification_adapters) = click_import_bootstrap.load_siblings(
    __package__, "click_collector_runtime", "click_verification_adapters"
)

UNITTEST_ADAPTER = click_verification_adapters.UNITTEST_ADAPTER
PYTEST_ADAPTER = click_verification_adapters.PYTEST_ADAPTER
VITEST_ADAPTER = click_verification_adapters.VITEST_ADAPTER
JEST_ADAPTER = click_verification_adapters.JEST_ADAPTER
ADAPTER = UNITTEST_ADAPTER
SUPPORTED_VERSION = tuple(sys.version_info[:3])
SUPPORTED_CPYTHON_MIN = click_collector_runtime.MIN_CPYTHON
SUPPORTED_CPYTHON_MAX = click_collector_runtime.MAX_CPYTHON


class AnalysisError(ValueError):
    """A stable, content-free bootstrap failure reason."""


@dataclass(frozen=True)
class Limits:
    files: int = 50_000
    snapshot_bytes: int = 128 * 1024 * 1024
    output_bytes: int = 256 * 1024
    result_bytes: int = 1024 * 1024
    timeout: float = 30.0
    modules: int = 2048


VITEST_VERSION = "5.0.0"
SNAPSHOT_DERIVED_ROOTS = frozenset({"node_modules"})
VITEST_CONFIG_NAMES = (
    "vitest.config.js", "vitest.config.mjs", "vitest.config.cjs",
    "vitest.config.ts", "vitest.config.mts", "vitest.config.cts",
    "vite.config.js", "vite.config.mjs", "vite.config.cjs",
    "vite.config.ts", "vite.config.mts", "vite.config.cts",
    "vitest.workspace.js", "vitest.workspace.mjs", "vitest.workspace.cjs",
    "vitest.workspace.ts", "vitest.workspace.mts", "vitest.workspace.cts",
)
VITEST_IDENTITY_NAMES = (
    "package.json", "package-lock.json", "npm-shrinkwrap.json",
    "node_modules/.package-lock.json", ".nvmrc", ".node-version",
    "tsconfig.json", "jsconfig.json", "node_modules/vitest/package.json",
    "node_modules/vitest/vitest.mjs", *VITEST_CONFIG_NAMES,
)
JEST_VERSION = "30.5.1"
JEST_CLI_VERSION = "30.5.0"
JEST_CONFIG_NAMES = (
    "jest.config.js", "jest.config.mjs", "jest.config.cjs", "jest.config.ts",
    "jest.config.json",
)
JEST_STATIC_CONFIG_FIELDS = frozenset({
    "globalSetup", "globalTeardown", "rootDir", "setupFiles",
    "setupFilesAfterEnv", "testEnvironment", "transform",
})
JEST_IDENTITY_NAMES = (
    "package.json", "package-lock.json", "npm-shrinkwrap.json",
    "node_modules/.package-lock.json", ".nvmrc", ".node-version",
    "tsconfig.json", "jsconfig.json", "babel.config.js", "babel.config.cjs",
    "babel.config.json", "node_modules/jest/package.json",
    "node_modules/jest/bin/jest.js", *JEST_CONFIG_NAMES,
)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode()).hexdigest()


def inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def trusted_executable(
    name: str, root: Path, *, preserve_launcher: bool = False
) -> Path:
    if not isinstance(name, str) or not name:
        raise AnalysisError("interpreter-unavailable")
    if os.path.isabs(name):
        selected = Path(name)
    else:
        if "/" in name or "\\" in name:
            raise AnalysisError("repository-executable")
        entries = []
        for entry in os.get_exec_path():
            path = Path(entry)
            if path.is_absolute() and not inside(root, path.resolve()):
                entries.append(entry)
        found = shutil.which(name, path=os.pathsep.join(entries))
        if not found:
            raise AnalysisError("interpreter-unavailable")
        selected = Path(found)
    launch_path = selected.absolute()
    resolved = launch_path.resolve(strict=True)
    if inside(root, launch_path) or inside(root, resolved):
        raise AnalysisError("repository-executable")
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise AnalysisError("interpreter-unavailable")
    # CPython uses an adjacent pyvenv.cfg to activate a virtual environment.
    # Collection therefore preserves its selected launcher, while backend and
    # compiler discovery retain their canonical resolved-path identity.
    return launch_path if preserve_launcher else resolved


def git_read(root: Path, args: list[str], *, optional: bool = False) -> bytes:
    git = trusted_executable("git", root)
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    result = subprocess.run(
        [str(git), "--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false", *args],
        cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=10, check=False,
    )
    if len(result.stdout) > 4 * 1024 * 1024:
        raise AnalysisError("git-output-limit")
    if result.returncode and not optional:
        raise AnalysisError("git-unavailable")
    return result.stdout if result.returncode == 0 else b""


def project_root(project: Path) -> Path:
    root = project.resolve(strict=True)
    # Do not borrow a containing project's Git identity for a nested non-Git app.
    if not root.is_dir() or not (root / ".git").exists():
        raise AnalysisError("non-git-project")
    observed = git_read(root, ["rev-parse", "--show-toplevel"]).decode().strip()
    if Path(observed).resolve() != root:
        raise AnalysisError("project-boundary")
    return root


def _relative(cwd: Path, root: Path, value: str) -> str:
    if not value or "\x00" in value:
        raise AnalysisError("invalid-discovery-path")
    if os.name == "nt":
        value = value.replace("\\", os.sep)
    elif "\\" in value:
        raise AnalysisError("invalid-discovery-path")
    path = (cwd / value).resolve(strict=True)
    if not path.is_dir() or not inside(root, path):
        raise AnalysisError("project-boundary")
    return os.path.relpath(path, cwd).replace(os.sep, "/")


def _pytest_target(cwd: Path, root: Path, value: str) -> str:
    if not value or "\x00" in value:
        raise AnalysisError("invalid-discovery-path")
    if os.name == "nt":
        value = value.replace("\\", os.sep)
    elif "\\" in value:
        raise AnalysisError("invalid-discovery-path")
    path = (cwd / value).resolve(strict=True)
    if not inside(root, path):
        raise AnalysisError("project-boundary")
    if not path.is_dir() and (not path.is_file() or path.suffix != ".py"):
        raise AnalysisError("unsupported-pytest-target")
    return os.path.relpath(path, cwd).replace(os.sep, "/")


def _vitest_target(cwd: Path, root: Path, value: str) -> str:
    if not value or "\x00" in value or (os.name != "nt" and "\\" in value):
        raise AnalysisError("invalid-discovery-path")
    normalized = value.replace("\\", os.sep) if os.name == "nt" else value
    try:
        path = (cwd / normalized).resolve(strict=True)
    except (OSError, RuntimeError):
        raise AnalysisError("invalid-discovery-path") from None
    if (
        not inside(root, path)
        or not path.is_file()
        or path.is_symlink()
        or not click_verification_adapters.is_vitest_test_path(
            path.relative_to(root).as_posix()
        )
    ):
        raise AnalysisError("unsupported-vitest-target")
    return os.path.relpath(path, cwd).replace(os.sep, "/")


def _jest_target(cwd: Path, root: Path, value: str) -> str:
    if not value or "\x00" in value or (os.name != "nt" and "\\" in value):
        raise AnalysisError("invalid-discovery-path")
    normalized = value.replace("\\", os.sep) if os.name == "nt" else value
    try:
        path = (cwd / normalized).resolve(strict=True)
    except (OSError, RuntimeError):
        raise AnalysisError("invalid-discovery-path") from None
    if (
        not inside(root, path)
        or not path.is_file()
        or path.is_symlink()
        or not click_verification_adapters.is_jest_test_path(
            path.relative_to(root).as_posix()
        )
    ):
        raise AnalysisError("unsupported-jest-target")
    return os.path.relpath(path, cwd).replace(os.sep, "/")


def _load_json_file(path: Path, reason: str) -> dict:
    try:
        metadata = path.lstat()
        if path.is_symlink() or not path.is_file() or metadata.st_size > 8 * 1024 * 1024:
            raise AnalysisError(reason)
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisError(reason) from exc
    if not isinstance(value, dict):
        raise AnalysisError(reason)
    return value


def _vitest_project_is_supported(root: Path) -> None:
    if any((root / name).exists() or (root / name).is_symlink() for name in VITEST_CONFIG_NAMES):
        raise AnalysisError("unsupported-vitest-config")
    package = _load_json_file(root / "package.json", "unsupported-vitest-project")
    if "vitest" in package:
        raise AnalysisError("unsupported-vitest-config")
    dependencies = {}
    for field in ("dependencies", "devDependencies", "peerDependencies"):
        value = package.get(field)
        if isinstance(value, dict):
            dependencies.update(value)
    if dependencies.get("vitest") != VITEST_VERSION:
        raise AnalysisError("unsupported-vitest-version")
    lock = _load_json_file(root / "package-lock.json", "unsupported-vitest-lock")
    packages = lock.get("packages")
    installed = packages.get("node_modules/vitest") if isinstance(packages, dict) else None
    if not isinstance(installed, dict) or installed.get("version") != VITEST_VERSION:
        raise AnalysisError("unsupported-vitest-version")


def _jest_config_paths(root: Path, configuration: dict) -> list[str]:
    paths: list[str] = []
    for field in ("setupFiles", "setupFilesAfterEnv"):
        values = configuration.get(field, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise AnalysisError("unsupported-jest-config")
        paths.extend(values)
    for field in ("globalSetup", "globalTeardown"):
        value = configuration.get(field)
        if value is not None:
            if not isinstance(value, str):
                raise AnalysisError("unsupported-jest-config")
            paths.append(value)
    normalized: list[str] = []
    for value in paths:
        if not value.startswith("<rootDir>/"):
            raise AnalysisError("unsupported-jest-config")
        relative = value[len("<rootDir>/") :]
        candidate = (root / relative).resolve(strict=True)
        if (
            not inside(root, candidate)
            or not candidate.is_file()
            or candidate.is_symlink()
        ):
            raise AnalysisError("unsupported-jest-config")
        normalized.append(candidate.relative_to(root).as_posix())
    return sorted(set(normalized))


def _jest_project_is_supported(root: Path) -> tuple[dict, list[str]]:
    if any((root / name).exists() or (root / name).is_symlink() for name in JEST_CONFIG_NAMES):
        raise AnalysisError("unsupported-jest-dynamic-config")
    package = _load_json_file(root / "package.json", "unsupported-jest-project")
    if package.get("type") == "module":
        raise AnalysisError("unsupported-jest-esm")
    configuration = package.get("jest", {})
    if not isinstance(configuration, dict):
        raise AnalysisError("unsupported-jest-config")
    unknown = set(configuration) - JEST_STATIC_CONFIG_FIELDS
    if unknown:
        if unknown.intersection({"projects", "displayName"}):
            raise AnalysisError("unsupported-jest-multi-project")
        if unknown.intersection({"testMatch", "testRegex"}):
            raise AnalysisError("unsupported-jest-discovery-config")
        raise AnalysisError("unsupported-jest-config")
    if configuration.get("rootDir", ".") != ".":
        raise AnalysisError("unsupported-jest-root")
    if configuration.get("testEnvironment", "node") not in {"node", "jest-environment-node"}:
        raise AnalysisError("unsupported-jest-environment")
    transform = configuration.get("transform", {})
    if transform:
        expected = ["babel-jest", {"presets": ["@babel/preset-typescript"]}]
        if transform != {r"^.+\.tsx?$": expected}:
            raise AnalysisError("unsupported-jest-transform")
    referenced = _jest_config_paths(root, configuration)
    dependencies: dict[str, str] = {}
    for field in ("dependencies", "devDependencies", "peerDependencies"):
        value = package.get(field)
        if isinstance(value, dict):
            dependencies.update({str(key): str(item) for key, item in value.items()})
    if dependencies.get("jest") != JEST_VERSION:
        raise AnalysisError("unsupported-jest-version")
    if transform and (
        dependencies.get("babel-jest") != JEST_VERSION
        or dependencies.get("@babel/core") != "7.29.7"
        or dependencies.get("@babel/preset-typescript") != "7.29.7"
    ):
        raise AnalysisError("unsupported-jest-transform-version")
    lock = _load_json_file(root / "package-lock.json", "unsupported-jest-lock")
    packages = lock.get("packages")
    installed = packages.get("node_modules/jest") if isinstance(packages, dict) else None
    if not isinstance(installed, dict) or installed.get("version") != JEST_VERSION:
        raise AnalysisError("unsupported-jest-version")
    return configuration, referenced


def _parse_vitest_command(argv: list[str], root: Path, cwd: Path) -> dict:
    executable = click_verification_adapters.click_capability.policy_executable_name(argv[0])
    if executable not in {"npx", "pnpx"}:
        raise AnalysisError("unsupported-vitest-launcher")
    arguments = argv[1:]
    target_index = next(
        (index for index, argument in enumerate(arguments) if not argument.startswith("-")),
        -1,
    )
    if (
        target_index < 0
        or arguments[:target_index] != ["--no-install"]
        or arguments[target_index : target_index + 2] != ["vitest", "run"]
    ):
        raise AnalysisError("unsupported-vitest-command")
    trailing = arguments[target_index + 2 :]
    if len(trailing) > 48 or len(set(trailing)) != len(trailing) or any(value.startswith("-") for value in trailing):
        raise AnalysisError("unsupported-vitest-option")
    _vitest_project_is_supported(root)
    selected_files = [_vitest_target(cwd, root, value) for value in trailing]
    selected = selected_files[0] if len(selected_files) == 1 else ""
    runner_prefix = list(argv[: target_index + 3])
    return {
        "adapter": VITEST_ADAPTER,
        "runner_prefix": runner_prefix,
        "collector_prefix": [*argv[: target_index + 2], "list"],
        "version_prefix": list(argv[: target_index + 2]),
        "start": ".",
        "top": ".",
        "pattern": "*.{test,spec}.{js,jsx,ts,tsx,mjs,mts,cjs,cts}",
        "patterns": [f"*{suffix}" for suffix in click_verification_adapters.VITEST_TEST_SUFFIXES],
        "filters": [],
        "verbosity": "",
        "child_options": [],
        "arguments": list(arguments),
        "selected_file": selected,
        "selected_files": selected_files,
        "exclude_directories": sorted(
            click_verification_adapters.VITEST_EXCLUDED_DIRECTORIES
        ),
    }


def _parse_jest_command(argv: list[str], root: Path, cwd: Path) -> dict:
    executable = click_verification_adapters.click_capability.policy_executable_name(argv[0])
    if executable not in {"npx", "pnpx"}:
        raise AnalysisError("unsupported-jest-launcher")
    arguments = argv[1:]
    target_index = next(
        (index for index, argument in enumerate(arguments) if not argument.startswith("-")),
        -1,
    )
    if (
        target_index < 0
        or arguments[:target_index] != ["--no-install"]
        or arguments[target_index] != "jest"
    ):
        raise AnalysisError("unsupported-jest-command")
    trailing = arguments[target_index + 1 :]
    selected = ""
    selected_files = []
    if trailing == ["--runInBand"]:
        pass
    elif (
        3 <= len(trailing) <= 50
        and trailing[:2] == ["--runInBand", "--runTestsByPath"]
    ):
        if len(set(trailing[2:])) != len(trailing[2:]):
            raise AnalysisError("unsupported-jest-option")
        selected_files = [_jest_target(cwd, root, value) for value in trailing[2:]]
        selected = selected_files[0] if len(selected_files) == 1 else ""
    else:
        raise AnalysisError("unsupported-jest-option")
    _, referenced = _jest_project_is_supported(root)
    return {
        "adapter": JEST_ADAPTER,
        "runner_prefix": [*argv[: target_index + 2], "--runInBand"],
        "collector_prefix": [*argv[: target_index + 2], "--runInBand", "--listTests", "--json"],
        "version_prefix": list(argv[: target_index + 2]),
        "start": ".",
        "top": ".",
        "pattern": "*.{test,spec}.{js,jsx,ts,tsx,mjs,mts,cjs,cts}",
        "patterns": [f"*{suffix}" for suffix in click_verification_adapters.JEST_TEST_SUFFIXES],
        "filters": [],
        "verbosity": "",
        "child_options": [],
        "arguments": list(arguments),
        "selected_file": selected,
        "selected_files": selected_files,
        "config_input_paths": referenced,
        "exclude_directories": sorted(click_verification_adapters.JEST_EXCLUDED_DIRECTORIES),
    }


def _python_module_command(argv: list[str]) -> tuple[str, int, list[str]]:
    executable = Path(argv[0]).name.lower()
    if re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", executable):
        index = 1
    elif executable in {"py", "py.exe"}:
        index = 1
        if index < len(argv) and re.fullmatch(r"-3(?:\.\d+)?", argv[index]):
            index += 1
    else:
        raise AnalysisError("unsupported-command")
    if index + 1 >= len(argv) or argv[index] != "-m":
        raise AnalysisError("unsupported-command")
    return argv[index + 1], index + 2, list(argv[: index + 2])


def _parse_unittest_command(
    argv: list[str], root: Path, cwd: Path, index: int, prefix: list[str]
) -> dict:
    if index >= len(argv) or argv[index] != "discover":
        raise AnalysisError("unsupported-command")
    if (not isinstance(argv, list) or not argv or len(argv) > 128
            or any(not isinstance(a, str) or not a or len(a) > 4096
                   or any(ord(c) < 32 for c in a) for a in argv)):
        raise AnalysisError("unsupported-command")
    values: dict[str, str] = {}
    filters: list[str] = []
    verbosity = ""
    positionals: list[str] = []
    names = {"-s": "start", "--start-directory": "start",
             "-t": "top", "--top-level-directory": "top",
             "-p": "pattern", "--pattern": "pattern", "-k": "filter"}
    tokens = argv[index + 1 :]
    cursor = 0
    while cursor < len(tokens):
        token = tokens[cursor]
        cursor += 1
        option, equal, attached = token.partition("=")
        if option in names:
            if equal:
                value = attached
            elif cursor < len(tokens):
                value = tokens[cursor]
                cursor += 1
            else:
                raise AnalysisError("missing-option-value")
            if not value or value.startswith("-"):
                raise AnalysisError("invalid-option-value")
            key = names[option]
            if key == "filter":
                filters.append(value)
            elif key in values:
                raise AnalysisError("ambiguous-option")
            else:
                values[key] = value
        elif token in ("-q", "--quiet", "-v", "--verbose"):
            selected = "-q" if token in ("-q", "--quiet") else "-v"
            if verbosity and verbosity != selected:
                raise AnalysisError("ambiguous-option")
            verbosity = selected
        elif token.startswith("-"):
            raise AnalysisError("unsupported-option")
        else:
            positionals.append(token)
    if len(positionals) > 3:
        raise AnalysisError("unsupported-command")
    for key, value in zip(("start", "pattern", "top"), positionals):
        if key in values:
            raise AnalysisError("ambiguous-option")
        values[key] = value
    start = _relative(cwd, root, values.get("start", "."))
    top = _relative(cwd, root, values.get("top", start))
    if not inside((cwd / top).resolve(), (cwd / start).resolve()):
        raise AnalysisError("invalid-top-level")
    pattern = values.get("pattern", "test*.py")
    if "/" in pattern or "\\" in pattern or len(pattern) > 256:
        raise AnalysisError("unsupported-pattern")
    return {
        "adapter": UNITTEST_ADAPTER,
        "runner_prefix": [*prefix, "discover"],
        "start": start,
        "top": top,
        "pattern": pattern,
        "patterns": [pattern],
        "filters": filters,
        "verbosity": verbosity,
        "child_options": [],
        "arguments": list(tokens),
    }


def _parse_pytest_command(
    argv: list[str], root: Path, cwd: Path, index: int, prefix: list[str]
) -> dict:
    tokens = argv[index:]
    flags = {
        "-q", "--quiet", "-v", "--verbose", "--strict-markers",
        "--strict-config", "--disable-warnings",
    }
    value_options = {"-k", "-m", "--maxfail", "--tb"}
    options: list[str] = []
    filters: list[str] = []
    positionals: list[str] = []
    cursor = 0
    while cursor < len(tokens):
        token = tokens[cursor]
        if token in flags:
            options.append(token)
            cursor += 1
            continue
        if token in value_options:
            if cursor + 1 >= len(tokens) or not tokens[cursor + 1]:
                raise AnalysisError("missing-option-value")
            value = tokens[cursor + 1]
            if value.startswith("-"):
                raise AnalysisError("invalid-option-value")
            options.extend((token, value))
            if token in {"-k", "-m"}:
                filters.append(value)
            cursor += 2
            continue
        if token.startswith(("--maxfail=", "--tb=")):
            if not token.partition("=")[2]:
                raise AnalysisError("invalid-option-value")
            options.append(token)
            cursor += 1
            continue
        if token.startswith("-") or "::" in token:
            raise AnalysisError("unsupported-option")
        positionals.append(token)
        cursor += 1
    if len(positionals) > 1:
        raise AnalysisError("unsupported-command")
    start = _pytest_target(cwd, root, positionals[0] if positionals else ".")
    return {
        "adapter": PYTEST_ADAPTER,
        "runner_prefix": prefix,
        "start": start,
        "top": ".",
        "pattern": "test_*.py",
        "patterns": ["test_*.py", "*_test.py"],
        "filters": filters,
        "verbosity": "-v" if any(item in {"-v", "--verbose"} for item in options) else "-q" if any(item in {"-q", "--quiet"} for item in options) else "",
        "child_options": options,
        "arguments": list(tokens),
    }


def parse_command(argv: list[str], root: Path, cwd: Path) -> dict:
    if (
        not isinstance(argv, list)
        or not argv
        or len(argv) > 128
        or any(
            not isinstance(argument, str)
            or not argument
            or len(argument) > 4096
            or any(ord(character) < 32 for character in argument)
            for argument in argv
        )
    ):
        raise AnalysisError("unsupported-command")
    try:
        root = root.resolve(strict=True)
        cwd = cwd.resolve(strict=True)
    except (OSError, RuntimeError):
        raise AnalysisError("project-boundary") from None
    if not inside(root, cwd):
        raise AnalysisError("project-boundary")
    profile = click_verification_adapters.command_profile(argv)
    if (
        profile is None
        or not click_verification_adapters.supports(
            str(profile.get("adapter_id", "")), "inventory"
        )
    ):
        raise AnalysisError("unsupported-command")
    if profile.get("adapter_id") == VITEST_ADAPTER:
        return _parse_vitest_command(argv, root, cwd)
    if profile.get("adapter_id") == JEST_ADAPTER:
        return _parse_jest_command(argv, root, cwd)
    module, index, prefix = _python_module_command(argv)
    if module == "unittest":
        return _parse_unittest_command(argv, root, cwd, index, prefix)
    if module == "pytest":
        return _parse_pytest_command(argv, root, cwd, index, prefix)
    raise AnalysisError("unsupported-command")


def workspace_snapshot(root: Path, limits: Limits) -> dict[str, str]:
    try:
        # macOS exposes temporary directories through both /var and
        # /private/var.  Compare symlink targets against the physical project
        # root so an internal link does not become an apparent escape merely
        # because the caller used the public alias.
        root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise AnalysisError("project-boundary") from None
    result: dict[str, str] = {}
    total = 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        base = Path(directory)
        dirs.sort()
        names.sort()
        if base == root and ".git" in dirs:
            dirs.remove(".git")
        for name in list(dirs):
            path = base / name
            if path.is_symlink():
                dirs.remove(name)
                names.append(name)
            elif base == root and name in SNAPSHOT_DERIVED_ROOTS:
                # Package-runner execution binds the complete installed tree
                # through click_runtime_identity.  Avoid walking the same
                # dependency tree during every source/inventory snapshot.
                dirs.remove(name)
            elif name == ".git":
                raise AnalysisError("nested-git-project")
        for name in sorted(names):
            path = base / name
            if path == root / ".git":
                continue
            metadata = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(metadata.st_mode):
                target = path.resolve(strict=True)
                if not inside(root, target):
                    raise AnalysisError("external-symlink")
                content = ("symlink:" + os.readlink(path)).encode()
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_size + total > limits.snapshot_bytes:
                    raise AnalysisError("snapshot-byte-limit")
                with path.open("rb") as stream:
                    content = stream.read(limits.snapshot_bytes - total + 1)
                if len(content) + total > limits.snapshot_bytes:
                    raise AnalysisError("snapshot-byte-limit")
            else:
                raise AnalysisError("unsupported-file-type")
            total += len(content)
            result[relative] = digest([stat.S_IMODE(metadata.st_mode),
                                      hashlib.sha256(content).hexdigest()])
            if len(result) > limits.files:
                raise AnalysisError("snapshot-file-limit")
    # Protect index and HEAD independently while excluding object-store churn.
    names = ("index", "HEAD", "config", "config.worktree", "packed-refs")
    paths = git_read(root, [
        "rev-parse", *(argument for name in names for argument in ("--git-path", name))
    ]).splitlines()
    if len(paths) != len(names):
        # Unusual worktree paths may contain newlines. Preserve the original
        # single-path interpretation rather than guessing how to split them.
        paths = [git_read(root, ["rev-parse", "--git-path", name]).rstrip(b"\r\n")
                 for name in names]
    for name, raw_path in zip(names, paths):
        path = Path(os.fsdecode(raw_path))
        path = path if path.is_absolute() else root / path
        content = path.read_bytes() if path.exists() else b""
        result[".git/" + name] = hashlib.sha256(content).hexdigest()
    result[".git/commit"] = hashlib.sha256(
        git_read(root, ["rev-parse", "--verify", "HEAD"], optional=True)
    ).hexdigest()
    return result


def run_bounded_command(
    argv: list[str], cwd: Path, environment: dict, limits: Limits
) -> None:
    """Run one collector/setup command through the shared bounded supervisor."""
    try:
        click_collector_runtime.supervise(
            argv,
            cwd,
            environment,
            timeout=limits.timeout,
            output_bytes=limits.output_bytes,
        )
    except click_collector_runtime.CollectorRuntimeError as exc:
        raise AnalysisError(str(exc)) from exc


def _capture_bounded_command(
    argv: list[str], cwd: Path, environment: dict, limits: Limits
) -> tuple[bytes, bytes]:
    try:
        captured = click_collector_runtime.supervise(
            argv,
            cwd,
            environment,
            timeout=limits.timeout,
            output_bytes=limits.output_bytes,
            capture_output=True,
        )
    except click_collector_runtime.CollectorRuntimeError as exc:
        raise AnalysisError(str(exc)) from exc
    if captured is None:
        raise AnalysisError("collector-output-failed")
    return captured


# Compatibility for releases that imported the prior private helper.
_supervised = run_bounded_command


def _vitest_environment() -> dict[str, str]:
    blocked = {
        "NODE_OPTIONS", "NODE_PATH", "NPM_CONFIG_PREFIX", "npm_config_prefix",
        "VITEST", "VITEST_MODE",
    }
    environment = {
        key: value for key, value in os.environ.items() if key not in blocked
    }
    environment.update(
        CI="1",
        NO_UPDATE_NOTIFIER="1",
        npm_config_audit="false",
        npm_config_fund="false",
        npm_config_offline="true",
        npm_config_update_notifier="false",
    )
    return environment


def _jest_environment() -> dict[str, str]:
    environment = _vitest_environment()
    environment.pop("JEST_WORKER_ID", None)
    return environment


def _vitest_collection(
    root: Path, cwd: Path, spec: dict, executable: Path, limits: Limits
) -> dict:
    environment = _vitest_environment()
    version_argv = [str(executable), *spec["version_prefix"][1:], "--version"]
    version_output, version_error = _capture_bounded_command(
        version_argv, cwd, environment, limits
    )
    if version_error.strip():
        raise AnalysisError("vitest-version-output-polluted")
    match = re.fullmatch(
        rb"vitest/([0-9]+[.][0-9]+[.][0-9]+) [^\s]+ node-v([0-9]+)[.]([0-9]+)[.]([0-9]+)\s*",
        version_output,
    )
    if match is None or match.group(1).decode() != VITEST_VERSION:
        raise AnalysisError("unsupported-vitest-version")
    selected = spec.get("selected_files", [spec["selected_file"]] if spec.get("selected_file") else [])
    collect_argv = [
        str(executable), *spec["collector_prefix"][1:], *selected, "--json", "--run"
    ]
    output, error_output = _capture_bounded_command(
        collect_argv, cwd, environment, limits
    )
    if error_output.strip():
        raise AnalysisError("vitest-collection-output-polluted")
    try:
        values = json.loads(output)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisError("invalid-collector-result") from exc
    if not isinstance(values, list) or len(values) > 10_000:
        raise AnalysisError("invalid-collector-result")
    occurrences: dict[tuple[str, str], int] = {}
    tests: list[dict[str, str]] = []
    files: set[str] = set()
    for value in values:
        if not isinstance(value, dict) or set(value) != {"name", "file", "location"}:
            raise AnalysisError("invalid-collector-result")
        name = value.get("name")
        location = value.get("location")
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 4096
            or any(ord(character) < 32 for character in name)
            or not isinstance(location, dict)
            or set(location) != {"line", "column"}
            or any(type(location.get(key)) is not int or location[key] < 1 for key in ("line", "column"))
        ):
            raise AnalysisError("invalid-collector-result")
        try:
            absolute = Path(value.get("file", "")).resolve(strict=True)
            relative = absolute.relative_to(root).as_posix()
        except (OSError, RuntimeError, ValueError, TypeError):
            raise AnalysisError("project-boundary") from None
        if (
            absolute.is_symlink()
            or not absolute.is_file()
            or not click_verification_adapters.is_vitest_test_path(relative)
        ):
            raise AnalysisError("unsupported-test-source")
        key = (relative, name)
        occurrences[key] = occurrences.get(key, 0) + 1
        identifier = f"{relative}::{name}#{occurrences[key]}"
        tests.append(
            {
                "id": identifier,
                "module": relative,
                "class": "",
                "method": name,
                "file": relative,
            }
        )
        files.add(relative)
    tests.sort(key=lambda item: item["id"])
    if selected:
        selected_relative = {
            (cwd / path).resolve(strict=True).relative_to(root).as_posix()
            for path in selected
        }
        if files != selected_relative:
            raise AnalysisError("vitest-selector-not-exact")
    reasons = [] if tests else ["zero-tests"]
    return {
        "version": 1,
        "runtime": {
            "implementation": "node",
            "version": [int(match.group(index)) for index in (2, 3, 4)],
            "framework": "vitest",
            "framework_version": VITEST_VERSION,
        },
        "tests": tests,
        "reasons": reasons,
        "modules": sorted(files),
        "module_files": [
            {"module": relative, "file": relative} for relative in sorted(files)
        ],
        "fixtures": [],
        "id_signature": digest([item["id"] for item in tests]) if tests else "",
    }


def _jest_collection(
    root: Path, cwd: Path, spec: dict, executable: Path, limits: Limits
) -> dict:
    environment = _jest_environment()
    version_argv = [str(executable), *spec["version_prefix"][1:], "--version"]
    version_output, version_error = _capture_bounded_command(
        version_argv, cwd, environment, limits
    )
    if version_error.strip():
        raise AnalysisError("jest-version-output-polluted")
    match = re.fullmatch(rb"([0-9]+)[.]([0-9]+)[.]([0-9]+)\s*", version_output)
    if match is None or ".".join(part.decode() for part in match.groups()) != JEST_CLI_VERSION:
        raise AnalysisError("unsupported-jest-version")
    node = trusted_executable("node", root)
    node_output, node_error = _capture_bounded_command(
        [str(node), "--version"], cwd, environment, limits
    )
    node_match = re.fullmatch(rb"v([0-9]+)[.]([0-9]+)[.]([0-9]+)\s*", node_output)
    if node_error.strip() or node_match is None:
        raise AnalysisError("unsupported-node-version")
    collect_argv = [str(executable), *spec["collector_prefix"][1:]]
    selected_files = spec.get("selected_files", [spec["selected_file"]] if spec.get("selected_file") else [])
    if selected_files:
        collect_argv.extend(("--runTestsByPath", *selected_files))
    output, error_output = _capture_bounded_command(
        collect_argv, cwd, environment, limits
    )
    if error_output.strip():
        raise AnalysisError("jest-collection-output-polluted")
    try:
        values = json.loads(output)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisError("invalid-collector-result") from exc
    if not isinstance(values, list) or len(values) > 10_000:
        raise AnalysisError("invalid-collector-result")
    files: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value:
            raise AnalysisError("invalid-collector-result")
        try:
            absolute = Path(value).resolve(strict=True)
            relative = absolute.relative_to(root).as_posix()
        except (OSError, RuntimeError, ValueError):
            raise AnalysisError("project-boundary") from None
        if (
            absolute.is_symlink()
            or not absolute.is_file()
            or not click_verification_adapters.is_jest_test_path(relative)
        ):
            raise AnalysisError("unsupported-test-source")
        files.append(relative)
    files = sorted(files)
    if len(files) != len(set(files)):
        raise AnalysisError("duplicate-id")
    if selected_files:
        selected = sorted((cwd / path).resolve(strict=True).relative_to(root).as_posix()
                          for path in selected_files)
        if files != selected:
            raise AnalysisError("jest-selector-not-exact")
    tests = [
        {
            "id": f"{relative}::file",
            "module": relative,
            "class": "",
            "method": "",
            "file": relative,
        }
        for relative in files
    ]
    return {
        "version": 1,
        "runtime": {
            "implementation": "node",
            "version": [int(value) for value in node_match.groups()],
            "framework": "jest",
            "framework_version": JEST_VERSION,
        },
        "tests": tests,
        "reasons": [] if tests else ["zero-tests"],
        "modules": files,
        "module_files": [
            {"module": relative, "file": relative} for relative in files
        ],
        "fixtures": [],
        "id_signature": digest([item["id"] for item in tests]) if tests else "",
    }


def collect_once(root: Path, cwd: Path, spec: dict, executable: Path,
                 limits: Limits) -> dict:
    adapter_id = str(spec.get("adapter", ""))
    if not click_verification_adapters.supports(adapter_id, "inventory"):
        raise AnalysisError("unsupported-adapter-capability")
    if click_collector_runtime.capability().status != "implemented":
        raise AnalysisError("unsupported-platform")
    if adapter_id == VITEST_ADAPTER:
        return _vitest_collection(root, cwd, spec, executable, limits)
    if adapter_id == JEST_ADAPTER:
        return _jest_collection(root, cwd, spec, executable, limits)
    with tempfile.TemporaryDirectory(prefix="click-collector-") as temp:
        directory = Path(temp)
        if inside(root, directory.resolve()):
            raise AnalysisError("temporary-directory-inside-project")
        request, response = directory / "request.json", directory / "result.json"
        request.write_text(json.dumps(spec))
        blocked_environment = {
            "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONBREAKPOINT",
            "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "LD_PRELOAD", "LD_LIBRARY_PATH",
            "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
        }
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() not in blocked_environment
        }
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONHASHSEED"] = "0"
        if spec.get("adapter") == PYTEST_ADAPTER:
            environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        worker_name = (
            "click_pytest_collector.py"
            if spec.get("adapter") == PYTEST_ADAPTER
            else "click_unittest_collector.py"
        )
        worker = Path(__file__).with_name(worker_name)
        run_bounded_command(
            [str(executable), "-B", str(worker), str(request), str(root),
             str(cwd), str(response)],
            cwd,
            environment,
            limits,
        )
        try:
            metadata = response.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limits.result_bytes:
                raise AnalysisError("invalid-collector-result")
            descriptor = os.open(
                response, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or (metadata.st_dev, metadata.st_ino)
                    != (opened.st_dev, opened.st_ino)
                ):
                    raise AnalysisError("invalid-collector-result")
                encoded = stream.read(limits.result_bytes + 1)
            if len(encoded) > limits.result_bytes:
                raise AnalysisError("result-size-limit")
            result = json.loads(encoded)
        except (OSError, ValueError) as exc:
            raise AnalysisError("invalid-collector-result") from exc
        expected = {"version", "runtime", "tests", "reasons", "modules", "module_files", "fixtures",
                    "id_signature"}
        if (not isinstance(result, dict) or set(result) != expected or result["version"] != 1
                or not isinstance(result["tests"], list) or len(result["tests"]) > 10_000
                or not isinstance(result["reasons"], list)
                or any(not isinstance(x, str) or not re.fullmatch(r"[a-z-]+", x)
                       for x in result["reasons"])):
            raise AnalysisError("invalid-collector-result")
        runtime = result["runtime"]
        if (not isinstance(runtime, dict) or set(runtime) != {
                    "implementation", "version", "framework", "framework_version"
                }
                or not isinstance(runtime["implementation"], str)
                or not re.fullmatch(r"[a-z]+", runtime["implementation"])
                or not isinstance(runtime["version"], list) or len(runtime["version"]) != 3
                or any(type(v) is not int or v < 0 for v in runtime["version"])
                or runtime["framework"] not in {"unittest", "pytest", "vitest"}
                or not isinstance(runtime["framework_version"], str)
                or not re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", runtime["framework_version"])):
            raise AnalysisError("invalid-collector-result")
        if (
            (spec.get("adapter") == UNITTEST_ADAPTER and runtime["framework"] != "unittest")
            or (spec.get("adapter") == PYTEST_ADAPTER and runtime["framework"] != "pytest")
            or (spec.get("adapter") == VITEST_ADAPTER and (
                runtime["framework"] != "vitest"
                or runtime["implementation"] != "node"
                or runtime["framework_version"] != VITEST_VERSION
            ))
        ):
            raise AnalysisError("invalid-collector-result")
        if spec.get("adapter") in {UNITTEST_ADAPTER, PYTEST_ADAPTER} and not click_collector_runtime.cpython_supported(
            runtime["implementation"], runtime["version"]
        ):
            raise AnalysisError("unsupported-runtime")
        for key in ("modules", "fixtures"):
            values = result[key]
            if (not isinstance(values, list) or len(values) > 50_000
                    or any(not isinstance(v, str) or len(v) > 1024 for v in values)
                    or (
                        spec.get("adapter") in {UNITTEST_ADAPTER, PYTEST_ADAPTER}
                        and any(not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", v) for v in values)
                    )
                    or (
                        spec.get("adapter") == VITEST_ADAPTER
                        and key == "modules"
                        and any(
                            not inside(root, (root / v).resolve())
                            or (root / v).resolve().relative_to(root).as_posix() != v
                            for v in values
                        )
                    )):
                raise AnalysisError("invalid-collector-result")
        if (not isinstance(result["id_signature"], str)
                or result["id_signature"] and not re.fullmatch(r"[0-9a-f]{64}", result["id_signature"])):
            raise AnalysisError("invalid-collector-result")
        if not isinstance(result["module_files"], list) or len(result["module_files"]) > 50_000:
            raise AnalysisError("invalid-collector-result")
        for module in result["module_files"]:
            if (not isinstance(module, dict) or set(module) != {"module", "file"}
                    or module["module"] not in result["modules"]
                    or not isinstance(module["file"], str)
                    or not inside(root, (root / module["file"]).resolve())
                    or (root / module["file"]).resolve().relative_to(root).as_posix() != module["file"]):
                raise AnalysisError("invalid-collector-result")
        for test in result["tests"]:
            if (not isinstance(test, dict)
                    or set(test) != {"id", "module", "class", "method", "file"}
                    or any(not isinstance(v, str) or len(v) > 4096 for v in test.values())
                    or not inside(root, (root / test["file"]).resolve())
                    or (root / test["file"]).resolve().relative_to(root).as_posix() != test["file"]
                    or (
                        spec.get("adapter") == UNITTEST_ADAPTER
                        and test["id"] != ".".join(
                            (test["module"], test["class"], test["method"])
                        )
                    )
                    or (
                        spec.get("adapter") == PYTEST_ADAPTER
                        and not test["id"].startswith(test["file"] + "::")
                    )):
                raise AnalysisError("invalid-collector-result")
        ids = [test["id"] for test in result["tests"]]
        if len(ids) != len(set(ids)):
            result["reasons"] = sorted(set(result["reasons"]) | {"duplicate-id"})
        if not ids and not result["reasons"]:
            result["reasons"] = ["zero-tests"]
        return result


def adapter_implementation_digest() -> str:
    hasher = hashlib.sha256()
    for module in (Path(__file__), Path(click_verification_adapters.__file__)):
        content = module.read_bytes()
        hasher.update(len(content).to_bytes(8, "big"))
        hasher.update(content)
    return hasher.hexdigest()


def vitest_configuration_digest(root: Path) -> str:
    records: list[tuple[str, str]] = []
    total = 0
    for name in VITEST_IDENTITY_NAMES:
        path = root / name
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            records.append((name, "missing"))
            continue
        if path.is_symlink() or not path.is_file() or metadata.st_size > 8 * 1024 * 1024:
            raise AnalysisError("vitest-identity-unavailable")
        total += int(metadata.st_size)
        if total > 32 * 1024 * 1024:
            raise AnalysisError("vitest-identity-limit")
        records.append((name, hashlib.sha256(path.read_bytes()).hexdigest()))
    return digest(records)


def jest_configuration_digest(root: Path) -> str:
    configuration, referenced = _jest_project_is_supported(root)
    names = list(JEST_IDENTITY_NAMES) + referenced
    snapshots = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.snap")
        if path.is_file()
        and not path.is_symlink()
        and "node_modules" not in path.relative_to(root).parts
    )
    names.extend(snapshots)
    records: list[tuple[str, str]] = [("jest-static-config", digest(configuration))]
    total = 0
    for name in sorted(set(names)):
        path = root / name
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            records.append((name, "missing"))
            continue
        if path.is_symlink() or not path.is_file() or metadata.st_size > 8 * 1024 * 1024:
            raise AnalysisError("jest-identity-unavailable")
        total += int(metadata.st_size)
        if total > 32 * 1024 * 1024:
            raise AnalysisError("jest-identity-limit")
        records.append((name, hashlib.sha256(path.read_bytes()).hexdigest()))
    return digest(records)


def _vitest_dependency_candidates(inventory_items: list[dict]) -> dict:
    owners = sorted({str(item["file"]) for item in inventory_items})
    by_module = {
        owner: {"files": [owner], "paths": ["**"]} for owner in owners
    }
    return {
        "candidate_only": True,
        "authority": False,
        "by_module": by_module,
        "edges": [],
        "common_paths": ["**"] if len(owners) > 1 else [],
        "config_paths": list(VITEST_IDENTITY_NAMES),
        "runtime_imports": ["vitest"],
        "unknown": [],
        "split_risks": [],
    }


def _jest_dependency_candidates(inventory_items: list[dict]) -> dict:
    owners = sorted({str(item["file"]) for item in inventory_items})
    return {
        "candidate_only": True,
        "authority": False,
        "by_module": {
            owner: {"files": [owner], "paths": ["**"]} for owner in owners
        },
        "edges": [],
        "common_paths": ["**"] if len(owners) > 1 else [],
        "config_paths": list(JEST_IDENTITY_NAMES),
        "runtime_imports": ["jest"],
        "unknown": [],
        "split_risks": [],
    }


def analyze(project: Path, argv: list[str] | None, *, cwd: Path | None = None,
            limits: Limits | None = None,
            _baseline_snapshot: dict[str, str] | None = None) -> dict:
    limits = limits or Limits()
    result = {"version": 1, "kind": "test-analysis", "adapter": ADAPTER,
              "status": "blocked", "reasons": [], "candidate_only": True,
              "authority": False, "reuse_ready": False, "inventory": [],
              "dependencies": {}, "runtime": None}
    if not argv:
        result.update(status="selection-required", reasons=["explicit-command-required"],
                      command_candidates=[
                          ["python3", "-m", "unittest", "discover"],
                          ["python3", "-m", "pytest"],
                          ["npx", "--no-install", "vitest", "run"],
                          ["npx", "--no-install", "jest", "--runInBand"],
                      ],
                      candidate_reason="A supported test command requires explicit selection.")
        return result
    before = None
    root = None
    try:
        root = project_root(Path(project))
        execution_cwd = (cwd or root).resolve(strict=True)
        if not inside(root, execution_cwd):
            raise AnalysisError("project-boundary")
        spec = parse_command(argv, root, execution_cwd)
        result["adapter"] = spec["adapter"]
        executable = trusted_executable(argv[0], root, preserve_launcher=True)
        executable_digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        if _baseline_snapshot is None:
            before = workspace_snapshot(root, limits)
        else:
            if (
                not isinstance(_baseline_snapshot, dict)
                or len(_baseline_snapshot) > limits.files + 6
                or any(
                    not isinstance(path, str)
                    or not isinstance(value, str)
                    or not value
                    for path, value in _baseline_snapshot.items()
                )
            ):
                raise AnalysisError("invalid-baseline-snapshot")
            before = dict(_baseline_snapshot)
        result.update(project_identity=digest([str(root), str(execution_cwd)]),
                      workspace_digest=digest(before),
                      command={"argv": list(argv), "cwd": execution_cwd.relative_to(root).as_posix(),
                               **spec})
        first = collect_once(root, execution_cwd, spec, executable, limits)
        runtime = {
            **first["runtime"],
            "executable_digest": executable_digest,
            "adapter_digest": adapter_implementation_digest(),
        }
        if spec["adapter"] in {VITEST_ADAPTER, JEST_ADAPTER}:
            node = trusted_executable("node", root)
            runtime.update(
                node_executable_digest=hashlib.sha256(node.read_bytes()).hexdigest(),
                project_configuration_digest=(
                    vitest_configuration_digest(root)
                    if spec["adapter"] == VITEST_ADAPTER
                    else jest_configuration_digest(root)
                ),
            )
        result.update(inventory=first["tests"], runtime=runtime,
            collection={"modules": first["modules"], "module_files": first["module_files"],
                        "fixtures": first["fixtures"],
                        "loader": (
                            "pytest.collect-only"
                            if spec["adapter"] == PYTEST_ADAPTER
                            else "vitest.list-json"
                            if spec["adapter"] == VITEST_ADAPTER
                            else "jest.list-tests-json"
                            if spec["adapter"] == JEST_ADAPTER
                            else "unittest.TestLoader"
                        ), "conditions": spec},
            reasons=first["reasons"])
        if workspace_snapshot(root, limits) != before:
            raise AnalysisError("collection-mutated-project")
        second = collect_once(root, execution_cwd, spec, executable, limits)
        if first != second:
            result["reasons"] = sorted(set(result["reasons"]) | {"unstable-test-id"})
        if workspace_snapshot(root, limits) != before:
            raise AnalysisError("collection-mutated-project")
        if hashlib.sha256(executable.read_bytes()).hexdigest() != executable_digest:
            raise AnalysisError("interpreter-changed")
        if adapter_implementation_digest() != runtime["adapter_digest"]:
            raise AnalysisError("adapter-changed")
        if spec["adapter"] in {VITEST_ADAPTER, JEST_ADAPTER}:
            node = trusted_executable("node", root)
            if (
                hashlib.sha256(node.read_bytes()).hexdigest()
                != runtime["node_executable_digest"]
            ):
                raise AnalysisError("runtime-changed")
            if (
                (
                    vitest_configuration_digest(root)
                    if spec["adapter"] == VITEST_ADAPTER
                    else jest_configuration_digest(root)
                )
                != runtime["project_configuration_digest"]
            ):
                raise AnalysisError("configuration-changed")
        modules = {item["module"] for item in result["inventory"]}
        if len(modules) > limits.modules:
            raise AnalysisError("module-count-limit")
        result["inventory_digest"] = digest(result["inventory"])
        if not click_verification_adapters.supports(
            str(spec["adapter"]), "dependency_candidates"
        ):
            raise AnalysisError("unsupported-adapter-capability")
        if spec["adapter"] == VITEST_ADAPTER:
            result["dependencies"] = _vitest_dependency_candidates(
                result["inventory"]
            )
        elif spec["adapter"] == JEST_ADAPTER:
            result["dependencies"] = _jest_dependency_candidates(
                result["inventory"]
            )
        else:
            (click_dependency_candidates,) = click_import_bootstrap.load_siblings(
                __package__, "click_dependency_candidates"
            )
            result["dependencies"] = click_dependency_candidates.analyze_candidates(
                root,
                execution_cwd,
                spec,
                [*result["inventory"], *first["module_files"]],
                list(before),
            )
        adapter_profile = click_verification_adapters.command_profile(argv)
        if adapter_profile is None:
            raise AnalysisError("unsupported-command")
        result["adapter_contract"] = click_verification_adapters.execution_model(
            adapter_id=str(spec["adapter"]),
            profile=str(adapter_profile["profile"]),
            original_argv=list(argv),
            cwd=execution_cwd.relative_to(root).as_posix(),
            execution_identity={
                "executable_digest": executable_digest,
                "runtime": dict(result["runtime"]),
            },
            inventory_units=[
                {
                    "kind": "test",
                    "id": str(item["id"]),
                    "file": str(item["file"]),
                    "selector": str(item["id"]),
                }
                for item in result["inventory"]
            ],
            selector={
                "start": spec.get("start", "."),
                "patterns": list(spec.get("patterns", [])),
                "filters": list(spec.get("filters", [])),
            },
            context_digest=digest(before),
            unknown_reasons=list(result["reasons"]),
        )
        if workspace_snapshot(root, limits) != before:
            raise AnalysisError("analysis-mutated-project")
        if not result["reasons"]:
            result["status"] = "analysis-complete"
        elif set(result["reasons"]) <= {
                "load-tests", "custom-loader", "custom-test-id", "dynamic-test",
                "unsupported-runtime", "unsupported-test-source",
                "pytest-unavailable", "unsupported-pytest-pattern",
                "unsupported-pytest-version"}:
            result["status"] = "unsupported"
    except (AnalysisError, OSError, ValueError, subprocess.SubprocessError, RuntimeError) as exc:
        reason = str(exc) if isinstance(exc, AnalysisError) else "analysis-unavailable"
        result["reasons"] = sorted(set(result["reasons"]) | {reason})
        if reason.startswith(("unsupported-", "non-git", "project-boundary",
                              "repository-executable", "external-symlink")):
            result["status"] = "unsupported"
        # A failed/killed collector may still have changed files; report both.
        if before is not None and root is not None:
            try:
                if workspace_snapshot(root, limits) != before:
                    result["reasons"] = sorted(set(result["reasons"]) |
                                               {"collection-mutated-project"})
                    result["status"] = "blocked"
            except Exception:
                result["reasons"] = sorted(set(result["reasons"]) |
                                           {"post-collection-snapshot-unavailable"})
    return result
