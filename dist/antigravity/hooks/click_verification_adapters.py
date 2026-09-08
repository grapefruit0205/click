"""Static verification-adapter contracts and command routing.

This leaf describes capabilities and normalizes command semantics.  It does not
load third-party plugins, inspect a project, run a command, create evidence, or
change approval state.  Capability reports are candidate metadata only.
"""

from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any

if __package__:
    from . import click_import_bootstrap
else:
    import click_import_bootstrap

(click_capability,) = click_import_bootstrap.load_siblings(
    __package__, "click_capability"
)


CONTRACT_VERSION = 1
CAPABILITY_NAMES = (
    "execute",
    "inventory",
    "split",
    "dependency_candidates",
    "runtime_observation",
)
IMPLEMENTATION_STATUSES = frozenset(
    {"implemented", "profile-limited", "unsupported"}
)

PYTHON_VERIFICATION_MODULES = {"coverage", "pytest", "unittest"}
PYTHON_VERIFICATION_EXECUTABLES = {"python", "python3", "py", "pypy", "pypy3"}
VERSIONED_PYTHON_EXECUTABLE = re.compile(r"^python3[.][0-9]+$")
DEEP_VERIFICATION_EXECUTABLES = {
    "bandit", "cargo-audit", "cypress", "k6", "locust", "nox", "playwright",
    "semgrep", "snyk", "tox", "trivy",
}
DEEP_VERIFICATION_MARKERS = {
    "audit", "bench", "coverage", "e2e", "end-to-end", "end_to_end",
    "integration", "load-test", "load_test", "security",
}
VERIFICATION_EXECUTABLES = {
    "bandit", "bats", "cargo-audit", "cypress", "jest", "k6", "locust",
    "nox", "playwright", "phpunit", "pytest", "rspec", "semgrep", "snyk",
    "tox", "trivy", "vitest",
}
VERIFICATION_NAME_MARKERS = (
    "audit", "bench", "coverage", "e2e", "integration-test", "integration_test",
    "security", "spec", "test", "validate", "verification", "verify",
)
CONTENT_VALIDATION_EXECUTABLES = {
    "check-jsonschema", "identify", "jq", "markdownlint", "sqlfluff",
    "xmllint", "yamllint",
}
TEST_TARGET_SUFFIXES = {
    ".go", ".js", ".jsx", ".php", ".py", ".rb", ".rs", ".ts", ".tsx",
}
TEST_FILTER_OPTIONS = {
    "-k", "-m", "-run", "-t", "--filter", "--test-name-pattern",
    "--tests-regex",
}
TEST_OPTIONS_WITH_VALUES = TEST_FILTER_OPTIONS | {
    "-p", "-r", "-s", "--basetemp", "--confcutdir", "--cov", "--cov-report",
    "--deselect", "--ignore", "--junitxml", "--maxfail", "--package",
    "--project", "--rootdir", "--test",
}

UNITTEST_ADAPTER = "cpython-unittest-v1"
PYTEST_ADAPTER = "cpython-pytest-v1"
VITEST_ADAPTER = "vitest-v1"
JEST_ADAPTER = "jest-v1"
VITEST_TEST_SUFFIXES = tuple(
    f".{kind}.{extension}"
    for kind in ("test", "spec")
    for extension in ("js", "jsx", "ts", "tsx", "mjs", "mts", "cjs", "cts")
)
VITEST_EXCLUDED_DIRECTORIES = frozenset(
    {".git", ".idea", ".cache", ".output", ".vite", ".vitest", "coverage", "dist", "node_modules"}
)
JEST_TEST_SUFFIXES = VITEST_TEST_SUFFIXES
JEST_EXCLUDED_DIRECTORIES = VITEST_EXCLUDED_DIRECTORIES


@dataclass(frozen=True)
class AdapterDescriptor:
    identifier: str
    version: int
    profiles: tuple[str, ...]
    execute: str = "implemented"
    inventory: str = "unsupported"
    split: str = "unsupported"
    dependency_candidates: str = "unsupported"
    runtime_observation: str = "unsupported"

    def public(self) -> dict[str, Any]:
        capabilities = {
            name: {
                "implementation": getattr(self, name),
                "environment_test": "not-recorded",
            }
            for name in CAPABILITY_NAMES
        }
        return {
            "version": CONTRACT_VERSION,
            "id": self.identifier,
            "adapter_version": self.version,
            "profiles": list(self.profiles),
            "capabilities": capabilities,
            "candidate_only": True,
            "authority": False,
        }


def _descriptor(
    identifier: str,
    *profiles: str,
    inventory: str = "unsupported",
    split: str = "unsupported",
    dependency_candidates: str = "unsupported",
    runtime_observation: str = "unsupported",
) -> AdapterDescriptor:
    return AdapterDescriptor(
        identifier=identifier,
        version=1,
        profiles=tuple(profiles),
        inventory=inventory,
        split=split,
        dependency_candidates=dependency_candidates,
        runtime_observation=runtime_observation,
    )


_DESCRIPTORS = (
    _descriptor(
        UNITTEST_ADAPTER,
        "unittest-discover",
        inventory="implemented",
        split="implemented",
        dependency_candidates="implemented",
        runtime_observation="profile-limited",
    ),
    _descriptor(
        PYTEST_ADAPTER,
        "pytest",
        inventory="implemented",
        split="implemented",
        dependency_candidates="implemented",
        runtime_observation="profile-limited",
    ),
    _descriptor("python-coverage-v1", "coverage", runtime_observation="profile-limited"),
    _descriptor("node-test-v1", "node-test"),
    _descriptor("node-check-v1", "node-check"),
    _descriptor("javascript-package-script-v1", "package-script"),
    _descriptor(
        VITEST_ADAPTER,
        "vitest",
        inventory="profile-limited",
        split="profile-limited",
        dependency_candidates="profile-limited",
    ),
    _descriptor(
        JEST_ADAPTER,
        "jest",
        inventory="profile-limited",
        split="profile-limited",
        dependency_candidates="profile-limited",
    ),
    _descriptor("generic-test-runner-v1", "test-runner"),
    _descriptor("security-verification-v1", "security-or-load"),
    _descriptor("cargo-v1", "cargo"),
    _descriptor("go-v1", "go"),
    _descriptor("python-static-v1", "ruff", "mypy"),
    _descriptor("typescript-v1", "tsc"),
    _descriptor("dotnet-v1", "dotnet"),
    _descriptor("jvm-v1", "gradle", "maven"),
    _descriptor("native-build-v1", "make", "cmake", "ctest"),
    _descriptor(
        "content-validation-v1",
        "json", "yaml", "markdown", "sql", "asset", "package-validation",
    ),
    _descriptor("pre-commit-v1", "pre-commit"),
    _descriptor("generic-named-verification-v1", "name-marker"),
)
DESCRIPTORS = {item.identifier: item for item in _DESCRIPTORS}


def descriptor(identifier: str) -> AdapterDescriptor | None:
    return DESCRIPTORS.get(identifier)


def capability_report(identifier: str) -> dict[str, Any] | None:
    selected = descriptor(identifier)
    return selected.public() if selected is not None else None


def supports(identifier: str, capability: str) -> bool:
    selected = descriptor(identifier)
    return bool(
        selected is not None
        and capability in CAPABILITY_NAMES
        and getattr(selected, capability) != "unsupported"
    )


def contains_deep_verification_marker(values: list[str]) -> bool:
    joined = " ".join(values)
    return any(marker in joined for marker in DEEP_VERIFICATION_MARKERS)


def arguments_have_filter(arguments: list[str]) -> bool:
    return any(
        argument in TEST_FILTER_OPTIONS
        or any(argument.startswith(f"{option}=") for option in TEST_FILTER_OPTIONS)
        for argument in arguments
    )


def verification_targets(
    arguments: list[str], *, skip_words: set[str] | None = None
) -> list[str]:
    skipped = skip_words or set()
    targets: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            targets.extend(
                item for item in arguments[index + 1 :] if item not in skipped
            )
            break
        if argument in TEST_OPTIONS_WITH_VALUES:
            index += 2
            continue
        if argument.startswith("-") or argument in skipped:
            index += 1
            continue
        targets.append(argument)
        index += 1
    return targets


def scope_with_kind_floor(scope: str, values: list[str]) -> str:
    if not contains_deep_verification_marker(values):
        return scope
    return "broad" if scope == "targeted" else "deep"


def minimum_test_runner_class(runner: str, arguments: list[str]) -> str:
    if runner == "unittest" and "discover" in arguments:
        return scope_with_kind_floor("broad", [runner, *arguments])
    if arguments_have_filter(arguments):
        return scope_with_kind_floor("broad", [runner, *arguments])
    broad_targets = {".", "./", "...", "./...", "all", "test", "tests", "spec"}
    targets = verification_targets(arguments, skip_words={"run", "exec", "x"})
    scope = "broad"
    if len(targets) == 1:
        target = targets[0]
        normalized = target.rstrip("/\\")
        if normalized not in broad_targets and (
            "::" in target
            or Path(normalized).suffix.lower() in TEST_TARGET_SUFFIXES
            or (runner == "unittest" and "." in normalized)
        ):
            scope = "targeted"
    return scope_with_kind_floor(scope, [runner, *arguments])


def _match(identifier: str, profile: str, minimum_class: str) -> dict[str, Any]:
    selected = DESCRIPTORS[identifier]
    return {
        "version": CONTRACT_VERSION,
        "adapter_id": selected.identifier,
        "adapter_version": selected.version,
        "profile": profile,
        "minimum_class": minimum_class,
        "capabilities": selected.public()["capabilities"],
        "candidate_only": True,
        "authority": False,
    }


def command_profile(
    tokens: list[str], *, wrapper_depth: int = 0
) -> dict[str, Any] | None:
    """Return static command metadata without probing any executable or project."""

    executable, arguments = click_capability.command_parts(tokens)
    if not executable:
        return None
    if executable in DEEP_VERIFICATION_EXECUTABLES:
        return _match("security-verification-v1", executable, "deep")
    if (
        executable in PYTHON_VERIFICATION_EXECUTABLES
        or VERSIONED_PYTHON_EXECUTABLE.fullmatch(executable)
    ):
        if executable == "py" and arguments and re.fullmatch(
            r"-\d+(?:\.\d+)?(?:-\d+)?", arguments[0]
        ):
            arguments = arguments[1:]
        if len(arguments) < 2 or arguments[0] != "-m":
            return None
        module = arguments[1]
        if module not in PYTHON_VERIFICATION_MODULES:
            return None
        if module == "coverage":
            return _match("python-coverage-v1", module, "deep")
        identifier = UNITTEST_ADAPTER if module == "unittest" else PYTEST_ADAPTER
        return _match(
            identifier,
            "unittest-discover" if module == "unittest" else "pytest",
            minimum_test_runner_class(module, arguments[2:]),
        )
    if executable == "uv":
        if wrapper_depth >= 2 or not arguments or arguments[0] != "run":
            return None
        nested = arguments[1:]
        while nested and nested[0].startswith("-"):
            nested = nested[1:]
        return command_profile(nested, wrapper_depth=wrapper_depth + 1)
    if executable in VERIFICATION_EXECUTABLES:
        if executable in {"bats", "jest", "phpunit", "pytest", "rspec", "vitest"}:
            identifier = {
                "jest": JEST_ADAPTER,
                "vitest": "vitest-v1",
            }.get(executable, "generic-test-runner-v1")
            return _match(
                identifier,
                executable,
                minimum_test_runner_class(executable, arguments),
            )
        return _match("security-verification-v1", executable, "broad")
    if executable == "node":
        if any(
            argument in {"-e", "--eval", "-p", "--print"}
            or argument.startswith(("--eval=", "--print="))
            for argument in arguments
        ):
            return None
        if arguments[:1] == ["--check"]:
            targets = [argument for argument in arguments[1:] if not argument.startswith("-")]
            if (
                len(targets) == 1
                and Path(targets[0]).suffix.lower() in {".cjs", ".js", ".mjs"}
            ):
                return _match("node-check-v1", "node-check", "targeted")
            return None
        if "--test" in arguments:
            test_arguments = [argument for argument in arguments if argument != "--test"]
            return _match(
                "node-test-v1",
                "node-test",
                minimum_test_runner_class("node", test_arguments),
            )
        return None
    if executable in {"npm", "pnpm", "yarn", "bun"}:
        meaningful = [item for item in arguments if item not in {"run", "exec", "x"}]
        target = meaningful[0] if meaningful else ""
        if not (
            any(marker in target for marker in VERIFICATION_NAME_MARKERS)
            or target in {"build", "check", "lint", "typecheck", "type-check"}
        ):
            return None
        minimum_class = (
            "deep" if contains_deep_verification_marker(meaningful) else "broad"
        )
        if target.startswith(("validate", "verify")):
            return _match(
                "content-validation-v1", "package-validation", minimum_class
            )
        return _match("javascript-package-script-v1", "package-script", minimum_class)
    if executable in {"npx", "pnpx", "bunx"}:
        target_index = next(
            (index for index, argument in enumerate(arguments) if not argument.startswith("-")),
            -1,
        )
        if target_index < 0:
            return None
        target = arguments[target_index]
        nested_arguments = arguments[target_index + 1 :]
        if target in DEEP_VERIFICATION_EXECUTABLES:
            return _match("security-verification-v1", target, "deep")
        if target in {"jest", "pytest", "vitest"}:
            identifier = {
                "jest": JEST_ADAPTER,
                "vitest": "vitest-v1",
            }.get(target, "generic-test-runner-v1")
            return _match(
                identifier,
                target,
                minimum_test_runner_class(target, nested_arguments),
            )
        if target in VERIFICATION_EXECUTABLES:
            return _match("generic-test-runner-v1", target, "broad")
        if any(marker in target for marker in VERIFICATION_NAME_MARKERS):
            return _match("generic-named-verification-v1", "name-marker", "deep")
        return None
    if executable == "cargo":
        if not arguments or arguments[0] not in {
            "audit", "bench", "check", "clippy", "nextest", "test",
        }:
            return None
        if arguments[0] in {"audit", "bench"}:
            minimum_class = "deep"
        elif arguments[0] in {"check", "clippy", "nextest"}:
            minimum_class = "broad"
        else:
            targets = [
                argument
                for argument in arguments[1:]
                if not argument.startswith("-") and argument not in {"all", "workspace"}
            ]
            minimum_class = "targeted" if len(targets) == 1 else "broad"
        return _match("cargo-v1", "cargo", minimum_class)
    if executable == "go":
        if not arguments or arguments[0] not in {"test", "vet"}:
            return None
        if arguments[0] == "vet" or arguments_have_filter(arguments[1:]):
            minimum_class = "broad"
        else:
            targets = [argument for argument in arguments[1:] if not argument.startswith("-")]
            recursive = any(target == "./..." or target.endswith("/...") for target in targets)
            minimum_class = "targeted" if len(targets) == 1 and not recursive else "broad"
        return _match("go-v1", "go", minimum_class)
    if executable == "ruff":
        if not arguments or arguments[0] != "check":
            return None
        targets = verification_targets(arguments[1:])
        minimum_class = (
            "targeted"
            if len(targets) == 1
            and Path(targets[0].rstrip("/\\")).suffix.lower() in TEST_TARGET_SUFFIXES
            else "broad"
        )
        return _match("python-static-v1", "ruff", minimum_class)
    if executable == "mypy":
        targets = verification_targets(arguments)
        minimum_class = (
            "targeted"
            if len(targets) == 1 and Path(targets[0]).suffix.lower() == ".py"
            else "broad"
        )
        return _match("python-static-v1", "mypy", minimum_class)
    if executable == "tsc":
        return _match("typescript-v1", "tsc", "broad") if "--noemit" in arguments else None
    if executable == "jq":
        if len(arguments) == 2 and arguments[0] == "empty" and not arguments[1].startswith("-"):
            return _match("content-validation-v1", "json", "targeted")
        return None
    if executable == "check-jsonschema":
        has_schema = any(
            item == "--schemafile" or item.startswith("--schemafile=")
            for item in arguments
        )
        has_target = any(not item.startswith("-") for item in arguments)
        return (
            _match("content-validation-v1", "yaml", "broad")
            if has_schema and has_target else None
        )
    if executable == "yamllint":
        targets = verification_targets(arguments)
        return (
            _match("content-validation-v1", "yaml", "broad")
            if targets else None
        )
    if executable == "markdownlint":
        targets = verification_targets(arguments)
        return (
            _match("content-validation-v1", "markdown", "broad")
            if targets and "--fix" not in arguments else None
        )
    if executable == "sqlfluff":
        return (
            _match("content-validation-v1", "sql", "broad")
            if arguments[:1] == ["lint"] else None
        )
    if executable == "xmllint":
        forbidden = {"--format", "--output", "--shell"}
        targets = verification_targets(arguments)
        return (
            _match("content-validation-v1", "asset", "broad")
            if {"--noout", "--nonet"}.issubset(arguments)
            and not forbidden.intersection(arguments)
            and targets else None
        )
    if executable == "identify":
        targets = verification_targets(arguments)
        return (
            _match("content-validation-v1", "asset", "broad")
            if "-ping" in arguments and "-write" not in arguments and targets
            else None
        )
    if executable in {"dotnet", "gradle", "gradlew", "gradlew.bat", "mvn", "mvnw", "mvnw.cmd"}:
        if not any(
            any(marker in argument for marker in VERIFICATION_NAME_MARKERS)
            for argument in arguments
        ):
            return None
        minimum_class = (
            "deep"
            if contains_deep_verification_marker(arguments)
            else "targeted" if any("filter" in item for item in arguments) else "broad"
        )
        identifier = "dotnet-v1" if executable == "dotnet" else "jvm-v1"
        return _match(identifier, "dotnet" if executable == "dotnet" else "jvm", minimum_class)
    if executable in {"make", "gmake", "cmake", "ctest", "pre-commit"}:
        recognized = executable in {"ctest", "pre-commit"} or any(
            any(marker in argument for marker in VERIFICATION_NAME_MARKERS)
            for argument in arguments
        )
        if not recognized:
            return None
        if contains_deep_verification_marker(arguments):
            minimum_class = "deep"
        elif executable == "ctest" and any(
            item in {"-r", "--tests-regex"} for item in arguments
        ):
            minimum_class = scope_with_kind_floor("broad", arguments)
        elif executable == "pre-commit" and "--files" in arguments:
            file_index = arguments.index("--files") + 1
            files = [item for item in arguments[file_index:] if not item.startswith("-")]
            minimum_class = "targeted" if len(files) == 1 else "broad"
        else:
            minimum_class = "broad"
        identifier = "pre-commit-v1" if executable == "pre-commit" else "native-build-v1"
        return _match(identifier, executable, minimum_class)
    stem = Path(executable).stem.lower()
    if any(marker in stem for marker in VERIFICATION_NAME_MARKERS):
        return _match("generic-named-verification-v1", "name-marker", "deep")
    return None


def minimum_verification_class(
    tokens: list[str], *, wrapper_depth: int = 0
) -> str | None:
    match = command_profile(tokens, wrapper_depth=wrapper_depth)
    return str(match["minimum_class"]) if match is not None else None


def execution_model(
    *,
    adapter_id: str,
    profile: str,
    original_argv: list[str],
    cwd: str,
    execution_identity: dict[str, Any],
    inventory_units: list[dict[str, str]],
    selector: dict[str, Any],
    policy_digest: str = "",
    context_digest: str = "",
    unknown_reasons: list[str] | None = None,
) -> dict[str, Any]:
    """Build the non-authoritative common model used by inventory adapters."""

    selected = descriptor(adapter_id)
    if selected is None or profile not in selected.profiles:
        raise ValueError("unknown-adapter-profile")
    allowed_unit_fields = {"kind", "id", "file", "selector"}
    normalized_units: list[dict[str, str]] = []
    for unit in inventory_units:
        if (
            not isinstance(unit, dict)
            or not set(unit).issubset(allowed_unit_fields)
            or unit.get("kind") not in {"project", "file", "test"}
            or any(not isinstance(value, str) or not value for value in unit.values())
        ):
            raise ValueError("invalid-inventory-unit")
        normalized_units.append(dict(unit))
    reasons = sorted(set(unknown_reasons or []))
    if any(not isinstance(reason, str) or not reason for reason in reasons):
        raise ValueError("invalid-unknown-reason")
    for value in (policy_digest, context_digest):
        if value and re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("invalid-model-digest")
    return {
        "version": CONTRACT_VERSION,
        "adapter": {"id": selected.identifier, "version": selected.version},
        "profile": profile,
        "original_argv": list(original_argv),
        "cwd": cwd,
        "execution_identity": dict(execution_identity),
        "inventory_units": normalized_units,
        "selector": dict(selector),
        "policy_digest": policy_digest,
        "context_digest": context_digest,
        "unknown_reasons": reasons,
        "capabilities": selected.public()["capabilities"],
        "candidate_only": True,
        "authority": False,
    }


def split_child_command(parent: dict[str, Any], filename: str) -> list[str] | None:
    """Return an exact file child command for a supported adapter profile."""

    adapter_id = parent.get("adapter")
    command = parent.get("command")
    if not isinstance(command, dict) or not supports(str(adapter_id), "split"):
        return None
    if adapter_id == PYTEST_ADAPTER:
        import posixpath

        relative = posixpath.relpath(filename, command.get("cwd", "."))
        return [*command["runner_prefix"], *command["child_options"], relative]
    if adapter_id == UNITTEST_ADAPTER:
        argv = [
            *command["runner_prefix"],
            "-s", command["start"],
            "-t", command["top"],
            "-p", filename,
        ]
        for pattern in command["filters"]:
            argv.extend(("-k", pattern))
        if command["verbosity"]:
            argv.append(command["verbosity"])
        return argv
    if adapter_id == VITEST_ADAPTER:
        import posixpath

        relative = posixpath.relpath(filename, command.get("cwd", "."))
        return [*command["runner_prefix"], *command["child_options"], relative]
    if adapter_id == JEST_ADAPTER:
        import posixpath

        relative = posixpath.relpath(filename, command.get("cwd", "."))
        return [
            *command["runner_prefix"],
            *command["child_options"],
            "--runTestsByPath",
            relative,
        ]
    return None


def is_vitest_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(
        path.endswith(VITEST_TEST_SUFFIXES)
        and not VITEST_EXCLUDED_DIRECTORIES.intersection(parts)
    )


def is_jest_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(
        path.endswith(JEST_TEST_SUFFIXES)
        and not JEST_EXCLUDED_DIRECTORIES.intersection(parts)
    )


def discovery_files(
    parent: dict[str, Any],
    snapshot_paths: list[str],
    *,
    root: Path,
    cwd: Path,
) -> list[str] | None:
    """Return the bounded file inventory for adapters with automatic discovery."""

    adapter_id = str(parent.get("adapter", ""))
    if adapter_id not in {
        UNITTEST_ADAPTER, PYTEST_ADAPTER, VITEST_ADAPTER, JEST_ADAPTER
    }:
        return None
    command = parent.get("command")
    if not isinstance(command, dict):
        return None
    start = (cwd / str(command.get("start", "."))).resolve()
    patterns = command.get("patterns", [command.get("pattern", "")])
    if not isinstance(patterns, list) or any(
        not isinstance(pattern, str) or not pattern for pattern in patterns
    ):
        return None
    if adapter_id == VITEST_ADAPTER:
        return sorted(
            path
            for path in snapshot_paths
            if not path.startswith(".git/")
            and _path_inside(cwd, root / path)
            and is_vitest_test_path(path)
        )
    if adapter_id == JEST_ADAPTER:
        return sorted(
            path
            for path in snapshot_paths
            if not path.startswith(".git/")
            and _path_inside(cwd, root / path)
            and is_jest_test_path(path)
        )
    return sorted(
        path
        for path in snapshot_paths
        if not path.startswith(".git/")
        and _path_inside(start, root / path)
        and path.endswith(".py")
        and any(fnmatch.fnmatchcase(Path(path).name, pattern) for pattern in patterns)
    )


def _path_inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def split_group_key(adapter_id: str, path: str) -> str | None:
    if adapter_id in {PYTEST_ADAPTER, VITEST_ADAPTER, JEST_ADAPTER}:
        return path
    if adapter_id == UNITTEST_ADAPTER:
        return Path(path).name
    return None


def inventory_owner(adapter_id: str, item: dict[str, Any]) -> str | None:
    if adapter_id in {UNITTEST_ADAPTER, PYTEST_ADAPTER}:
        module = item.get("module")
        return module if isinstance(module, str) and module else None
    file_value = item.get("file")
    return file_value if isinstance(file_value, str) and file_value else None


def inventory_labels(
    adapter_id: str, items: list[dict[str, Any]]
) -> list[str] | None:
    labels = {inventory_owner(adapter_id, item) for item in items}
    if None in labels:
        return None
    return sorted(str(label) for label in labels)


def discovered_files(parent: dict[str, Any]) -> set[str] | None:
    adapter_id = str(parent.get("adapter", ""))
    collection = parent.get("collection")
    if adapter_id not in {
        UNITTEST_ADAPTER, PYTEST_ADAPTER, VITEST_ADAPTER, JEST_ADAPTER
    } or not isinstance(
        collection, dict
    ):
        return None
    module_files = collection.get("module_files")
    if not isinstance(module_files, list):
        return None
    values = {
        item.get("file")
        for item in module_files
        if isinstance(item, dict) and isinstance(item.get("file"), str)
    }
    return {str(value) for value in values if value}


def dependency_paths(candidate: dict[str, Any]) -> set[str] | None:
    groups = candidate.get("by_module")
    if not isinstance(groups, dict):
        return None
    values: set[str] = set()
    for group in groups.values():
        if not isinstance(group, dict) or not isinstance(group.get("paths"), list):
            return None
        if any(not isinstance(path, str) or not path for path in group["paths"]):
            return None
        values.update(group["paths"])
    return values


def _python_discovery_spec(
    argv: list[str], adapter_id: str
) -> tuple[tuple[str, tuple[str, ...]] | None, str]:
    executable = Path(argv[0]).name.lower()
    index = 1
    if executable in {"py", "py.exe"} and index < len(argv) and re.fullmatch(
        r"-\d+(?:\.\d+)?(?:-\d+)?", argv[index]
    ):
        index += 1
    allowed = {
        "python", "python.exe", "python3", "python3.exe", "pypy", "pypy.exe",
        "pypy3", "pypy3.exe", "py", "py.exe",
    }
    if executable not in allowed:
        return None, ""
    if adapter_id == UNITTEST_ADAPTER:
        if argv[index : index + 3] != ["-m", "unittest", "discover"]:
            return None, ""
        arguments = argv[index + 3 :]
        start: str | None = None
        pattern: str | None = None
        positionals: list[str] = []
        cursor = 0
        while cursor < len(arguments):
            argument = arguments[cursor]
            if argument in {"-s", "--start-directory", "-p", "--pattern"}:
                if cursor + 1 >= len(arguments):
                    return None, "parent-discovery-arguments-unsupported"
                value = arguments[cursor + 1]
                if argument in {"-s", "--start-directory"}:
                    if start is not None:
                        return None, "parent-discovery-arguments-unsupported"
                    start = value
                else:
                    if pattern is not None:
                        return None, "parent-discovery-arguments-unsupported"
                    pattern = value
                cursor += 2
                continue
            if argument.startswith("--start-directory="):
                if start is not None:
                    return None, "parent-discovery-arguments-unsupported"
                start = argument.split("=", 1)[1]
            elif argument.startswith("--pattern="):
                if pattern is not None:
                    return None, "parent-discovery-arguments-unsupported"
                pattern = argument.split("=", 1)[1]
            elif argument in {"-t", "--top-level-directory", "-k"}:
                if cursor + 1 >= len(arguments):
                    return None, "parent-discovery-arguments-unsupported"
                cursor += 2
                continue
            elif argument.startswith("-"):
                pass
            else:
                positionals.append(argument)
            cursor += 1
        if len(positionals) > 3:
            return None, "parent-discovery-arguments-unsupported"
        if start is None and positionals:
            start = positionals[0]
        if pattern is None and len(positionals) > 1:
            pattern = positionals[1]
        start = start or "."
        patterns = (pattern or "test*.py",)
    elif adapter_id == PYTEST_ADAPTER:
        if argv[index : index + 2] != ["-m", "pytest"]:
            return None, ""
        arguments = argv[index + 2 :]
        flags = {
            "-q", "--quiet", "-v", "--verbose", "--strict-markers",
            "--strict-config", "--disable-warnings",
        }
        value_options = {"-k", "-m", "--maxfail", "--tb"}
        positionals = []
        cursor = 0
        while cursor < len(arguments):
            value = arguments[cursor]
            if value in flags:
                cursor += 1
                continue
            if value in value_options:
                if cursor + 1 >= len(arguments) or not arguments[cursor + 1]:
                    return None, "parent-discovery-arguments-unsupported"
                cursor += 2
                continue
            if value.startswith(("--maxfail=", "--tb=")) and value.partition("=")[2]:
                cursor += 1
                continue
            if value.startswith("-") or "::" in value:
                return None, "parent-discovery-arguments-unsupported"
            positionals.append(value)
            cursor += 1
        if len(positionals) > 1:
            return None, "parent-discovery-arguments-unsupported"
        start = positionals[0] if positionals else "."
        patterns = ("test_*.py", "*_test.py")
    else:
        return None, ""
    if os.name == "nt":
        start = start.replace("\\", "/")
    if (
        not start
        or "\x00" in start
        or "\\" in start
        or PurePosixPath(start).is_absolute()
        or any(part in {"", ".."} for part in PurePosixPath(start).parts)
        or any(
            not pattern
            or "\x00" in pattern
            or "/" in pattern
            or "\\" in pattern
            for pattern in patterns
        )
    ):
        return None, "parent-discovery-arguments-unsupported"
    return (start, patterns), ""


def parent_discovery_paths(
    parent_checks: list[list[str]],
    repository_paths: list[str],
    *,
    working_prefix: str,
) -> tuple[set[str] | None, str]:
    """Validate automatic discovery without constraining owner-declared shards."""

    if len(parent_checks) != 1 or not parent_checks[0]:
        return None, ""
    profile = command_profile(parent_checks[0])
    if profile is None or not supports(str(profile["adapter_id"]), "inventory"):
        return None, ""
    adapter_id = str(profile["adapter_id"])
    if adapter_id in {VITEST_ADAPTER, JEST_ADAPTER}:
        executable = click_capability.policy_executable_name(parent_checks[0][0])
        arguments = list(parent_checks[0][1:])
        if executable not in {"npx", "pnpx"}:
            return None, "parent-discovery-arguments-unsupported"
        target_index = next(
            (index for index, argument in enumerate(arguments) if not argument.startswith("-")),
            -1,
        )
        if adapter_id == VITEST_ADAPTER and (
            target_index < 0
            or arguments[:target_index] != ["--no-install"]
            or [item.lower() for item in arguments[target_index : target_index + 2]]
            != ["vitest", "run"]
            or len(arguments[target_index + 2 :]) > 1
            or any(argument.startswith("-") for argument in arguments[target_index + 2 :])
        ):
            return None, "parent-discovery-arguments-unsupported"
        if adapter_id == JEST_ADAPTER:
            trailing = arguments[target_index + 1 :] if target_index >= 0 else []
            if (
                target_index < 0
                or arguments[:target_index] != ["--no-install"]
                or arguments[target_index].lower() != "jest"
                or not trailing
                or trailing[0] != "--runInBand"
                or trailing[1:2] not in ([], ["--runTestsByPath"])
                or (len(trailing) not in {1, 3})
                or (len(trailing) == 3 and (
                    trailing[1] != "--runTestsByPath"
                    or not trailing[2]
                    or trailing[2].startswith("-")
                ))
            ):
                return None, "parent-discovery-arguments-unsupported"
        prefix = f"{working_prefix}/" if working_prefix else ""
        discovered = {
            relative
            for relative in repository_paths
            if (not prefix or relative.startswith(prefix))
            and (
                is_vitest_test_path(relative[len(prefix) :] if prefix else relative)
                if adapter_id == VITEST_ADAPTER
                else is_jest_test_path(relative[len(prefix) :] if prefix else relative)
            )
        }
        filters = (
            arguments[target_index + 2 :]
            if adapter_id == VITEST_ADAPTER
            else arguments[target_index + 3 :]
        )
        if filters:
            selected = filters[-1].replace("\\", "/")
            discovered = {
                relative
                for relative in discovered
                if (
                    selected == (relative[len(prefix) :] if prefix else relative)
                    if adapter_id == JEST_ADAPTER
                    else selected in (relative[len(prefix) :] if prefix else relative)
                )
            }
        return discovered, ""
    spec, error = _python_discovery_spec(parent_checks[0], adapter_id)
    if error or spec is None:
        return None, error
    start, patterns = spec
    start_parts = [] if start == "." else list(PurePosixPath(start).parts)
    prefix_parts = [] if not working_prefix else list(PurePosixPath(working_prefix).parts)
    discovery_root = PurePosixPath(*prefix_parts, *start_parts).as_posix()
    if discovery_root == ".":
        discovery_root = ""
    prefix = f"{discovery_root}/" if discovery_root else ""
    return {
        relative
        for relative in repository_paths
        if (not prefix or relative.startswith(prefix))
        and relative.endswith(".py")
        and any(
            fnmatch.fnmatchcase(PurePosixPath(relative).name, pattern)
            for pattern in patterns
        )
    }, ""
