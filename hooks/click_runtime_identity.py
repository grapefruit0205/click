"""Content-bound runtime identities for execute-level verification adapters.

The collector reads only bounded local metadata and files. It never installs,
downloads, imports, or executes a project. Returned bindings contain digests
and stable reasons, not paths, config contents, or environment values.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
from typing import Any, Callable

if __package__:
    from . import click_import_bootstrap
else:
    import click_import_bootstrap

(click_capability, click_verification_adapters,) = click_import_bootstrap.load_siblings(
    __package__, "click_capability", "click_verification_adapters"
)


VERSION = 1
MAX_CONFIG_BYTES = 8 * 1024 * 1024
MAX_RUNTIME_BYTES = 512 * 1024 * 1024
MAX_CONFIG_FILES = 64
MAX_RUNTIME_FILES = 50_000
RUNTIME_TREE_CACHE_DIRECTORIES = frozenset({".cache", ".vite", ".vitest"})
STATUSES = frozenset({"complete", "incomplete", "unsafe"})


def _path_key(root: Path, path: Path) -> str:
    try:
        value = path.relative_to(root).as_posix()
    except ValueError:
        value = path.name
    return click_capability.digest({"path": value})


def _file_record(
    root: Path,
    path: Path,
    role: str,
    digest_file: Callable[[Path], str],
    *,
    max_bytes: int = MAX_CONFIG_BYTES,
) -> tuple[dict[str, Any], str]:
    record: dict[str, Any] = {
        "role": role,
        "path_key": _path_key(root, path),
        "status": "missing",
        "size": 0,
        "content_digest": "",
    }
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return record, ""
    except OSError:
        record["status"] = "unavailable"
        return record, "runtime-config-unavailable"
    if path.is_symlink():
        record["status"] = "symlink"
        return record, "runtime-config-symlink"
    if not path.is_file():
        record["status"] = "non-file"
        return record, "runtime-config-non-file"
    if metadata.st_size > max_bytes:
        record["status"] = "oversized"
        return record, "runtime-config-file-limit"
    digest = digest_file(path)
    if not digest:
        record["status"] = "unavailable"
        return record, "runtime-config-unavailable"
    record.update(
        status="present",
        size=int(metadata.st_size),
        content_digest=digest,
    )
    return record, ""


def _runtime_record(
    root: Path,
    name: str,
    resolve_executable: Callable[[str], Path | None],
    digest_file: Callable[[Path], str],
) -> tuple[dict[str, Any], str]:
    path = resolve_executable(name)
    if path is None:
        return {
            "role": f"runtime:{name}",
            "path_key": "",
            "status": "missing",
            "size": 0,
            "content_digest": "",
        }, "runtime-executable-missing"
    return _file_record(
        root,
        path,
        f"runtime:{name}",
        digest_file,
        max_bytes=MAX_RUNTIME_BYTES,
    )


def _directory_record(
    root: Path,
    path: Path,
    role: str,
    digest_file: Callable[[Path], str],
) -> tuple[dict[str, Any], str]:
    record: dict[str, Any] = {
        "role": role,
        "path_key": _path_key(root, path),
        "status": "missing",
        "size": 0,
        "content_digest": "",
    }
    try:
        boundary = path.resolve(strict=True)
        if path.is_symlink() or not path.is_dir():
            return record, "runtime-directory-unavailable"
        entries: list[tuple[str, str, str]] = []
        total = 0
        for directory, directories, names in os.walk(path, followlinks=False):
            base = Path(directory)
            directories.sort()
            names.sort()
            if base == path:
                directories[:] = [
                    name
                    for name in directories
                    if name not in RUNTIME_TREE_CACHE_DIRECTORIES
                ]
            for name in list(directories):
                candidate = base / name
                if candidate.is_symlink():
                    target = candidate.resolve(strict=True)
                    target.relative_to(boundary)
                    directories.remove(name)
                    names.append(name)
            for name in sorted(names):
                candidate = base / name
                relative = candidate.relative_to(path).as_posix()
                metadata = candidate.lstat()
                if candidate.is_symlink():
                    target = candidate.resolve(strict=True)
                    target.relative_to(boundary)
                    content_digest = click_capability.digest(
                        {"symlink": os.readlink(candidate)}
                    )
                    kind = "symlink"
                    size = len(os.fsencode(os.readlink(candidate)))
                elif candidate.is_file():
                    content_digest = digest_file(candidate)
                    if not content_digest:
                        return record, "runtime-directory-unavailable"
                    kind = "file"
                    size = int(metadata.st_size)
                else:
                    return record, "runtime-directory-unavailable"
                total += size
                entries.append((relative, kind, content_digest))
                if total > MAX_RUNTIME_BYTES or len(entries) > MAX_RUNTIME_FILES:
                    return record, "runtime-directory-limit"
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return record, "runtime-directory-unavailable"
    record.update(
        status="present",
        size=total,
        content_digest=click_capability.digest({"entries": entries}),
    )
    return record, ""


def _config_records(
    root: Path,
    names: tuple[str, ...],
    digest_file: Callable[[Path], str],
) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    reasons: list[str] = []
    for name in names[:MAX_CONFIG_FILES]:
        record, reason = _file_record(root, root / name, f"config:{name}", digest_file)
        records.append(record)
        if reason:
            reasons.append(reason)
    return records, reasons


def _top_level_project_records(
    root: Path,
    suffixes: tuple[str, ...],
    digest_file: Callable[[Path], str],
) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        candidates = sorted(
            path
            for path in root.iterdir()
            if path.is_file() and path.suffix.lower() in suffixes
        )[:MAX_CONFIG_FILES]
    except OSError:
        return [], ["runtime-project-inventory-unavailable"]
    records: list[dict[str, Any]] = []
    reasons: list[str] = []
    for path in candidates:
        record, reason = _file_record(root, path, "project", digest_file)
        records.append(record)
        if reason:
            reasons.append(reason)
    return records, reasons


def _package_script(
    root: Path, arguments: list[str]
) -> tuple[str, str, list[str]]:
    package_path = root / "package.json"
    try:
        metadata = package_path.lstat()
        if package_path.is_symlink() or not package_path.is_file():
            return "", "", ["package-manifest-unavailable"]
        if metadata.st_size > MAX_CONFIG_BYTES:
            return "", "", ["package-manifest-file-limit"]
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return "", "", ["package-manifest-unavailable"]
    meaningful = [value for value in arguments if value not in {"run", "exec", "x"}]
    script_name = meaningful[0] if meaningful else ""
    scripts = package.get("scripts") if isinstance(package, dict) else None
    script = scripts.get(script_name) if isinstance(scripts, dict) else None
    if not isinstance(script, str) or not script:
        return script_name, "", ["package-script-unavailable"]
    reasons = [
        "package-lifecycle-script-present"
        for lifecycle in (f"pre{script_name}", f"post{script_name}")
        if isinstance(scripts.get(lifecycle), str) and scripts[lifecycle]
    ]
    return script_name, script, reasons


def _simple_script_runtime(script: str) -> str:
    try:
        parts = shlex.split(script, posix=os.name != "nt")
    except ValueError:
        return ""
    if not parts or any(token in script for token in ("&&", "||", ";", "\n")):
        return ""
    return Path(parts[0]).name.lower()


def _npm_installed_versions(path: Path) -> dict[str, str] | None:
    try:
        metadata = path.lstat()
        if path.is_symlink() or not path.is_file():
            return None
        if metadata.st_size > MAX_CONFIG_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    packages = value.get("packages") if isinstance(value, dict) else None
    if not isinstance(packages, dict):
        return None
    return {
        str(name): str(record.get("version", ""))
        for name, record in packages.items()
        if name and isinstance(name, str) and isinstance(record, dict)
    }


def _local_package_runner_record(
    root: Path,
    local: Path,
    target: str,
    digest_file: Callable[[Path], str],
) -> tuple[list[dict[str, Any]], str]:
    """Bind an installed package launcher without trusting an external symlink."""

    records: list[dict[str, Any]] = []
    package_name = re.fullmatch(
        r"(?:[a-z0-9][a-z0-9._-]*|@[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*)",
        target,
    )
    if package_name is None or len(target) > 214:
        return records, "package-runner-target-unbound"
    try:
        package = root / "node_modules" / target / "package.json"
        package_record, package_reason = _file_record(
            root, package, "package-runner-manifest", digest_file
        )
        records.append(package_record)
        if package_reason or package_record["status"] != "present":
            return records, "package-runner-target-unbound"
        value = json.loads(package.read_text(encoding="utf-8"))
        declared_bin = value.get("bin") if isinstance(value, dict) else None
        if isinstance(declared_bin, dict):
            declared_bin = declared_bin.get(Path(target).name)
        if not isinstance(declared_bin, str) or not declared_bin:
            return records, "package-runner-target-unbound"
        package_root = package.parent.resolve(strict=True)
        target_path = (package.parent / declared_bin).resolve(strict=True)
        target_path.relative_to(package_root)
        target_record, target_reason = _file_record(
            root, target_path, "package-runner-entry", digest_file
        )
        records.append(target_record)
        if target_reason or target_record["status"] != "present":
            return records, "package-runner-target-unbound"
        metadata = local.lstat()
        if local.is_symlink():
            linked = local.resolve(strict=True)
            linked.relative_to((root / "node_modules").resolve(strict=True))
            if linked != target_path:
                return records, "package-runner-target-unbound"
        elif not local.is_file() or metadata.st_size > MAX_CONFIG_BYTES:
            return records, "package-runner-target-unbound"
        else:
            launcher, launcher_reason = _file_record(
                root, local, "package-runner-launcher", digest_file
            )
            records.append(launcher)
            if launcher_reason or launcher["status"] != "present":
                return records, "package-runner-target-unbound"
    except (FileNotFoundError, OSError, RuntimeError, ValueError, UnicodeError, json.JSONDecodeError):
        return records, "package-runner-target-unbound"
    return records, ""


def _node_identity(
    executable: str,
    arguments: list[str],
    *,
    root: Path,
    resolve_executable: Callable[[str], Path | None],
    digest_file: Callable[[Path], str],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    records, reasons = _config_records(
        root,
        (
            "package.json", "package-lock.json", "npm-shrinkwrap.json",
            "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb",
            "node_modules/.package-lock.json", ".nvmrc", ".node-version",
            "tsconfig.json", "jsconfig.json",
        ),
        digest_file,
    )
    unsafe: list[str] = []
    # Direct `node` already has a content-bound primary executable record.
    # Package and JS-tool launchers still need the secondary Node runtime.
    if executable != "node":
        runtime, runtime_reason = _runtime_record(
            root, "node", resolve_executable, digest_file
        )
        records.append(runtime)
        if runtime_reason:
            reasons.append(runtime_reason)

    if executable in {"npm", "pnpm", "yarn", "bun"}:
        _, script, script_reasons = _package_script(root, arguments)
        reasons.extend(script_reasons)
        script_runtime = _simple_script_runtime(script)
        if script_runtime and script_runtime != "node":
            local = root / "node_modules" / ".bin" / script_runtime
            if os.name == "nt" and not local.exists():
                local = local.with_suffix(".cmd")
            local_record, local_reason = _file_record(
                root, local, "package-script-runtime", digest_file
            )
            records.append(local_record)
            if local_reason or local_record["status"] != "present":
                reasons.append("package-script-runtime-unbound")
        elif script_runtime != "node":
            reasons.append("package-script-runtime-unbound")
        node_modules = root / "node_modules"
        if node_modules.is_dir() and not (node_modules / ".package-lock.json").is_file():
            reasons.append("node-install-state-unbound")
        elif node_modules.is_dir():
            declared = _npm_installed_versions(root / "package-lock.json")
            installed = _npm_installed_versions(
                node_modules / ".package-lock.json"
            )
            if declared is None or installed is None:
                reasons.append("node-lock-install-state-invalid")
            elif declared != installed:
                reasons.append("node-lock-install-mismatch")
        if executable in {"pnpm", "yarn", "bun"}:
            reasons.append("package-runner-chain-unbound")

    if executable in {"npx", "pnpx", "bunx"}:
        target = next((item for item in arguments if not item.startswith("-")), "")
        local = root / "node_modules" / ".bin" / Path(target).name
        if os.name == "nt" and not local.exists():
            local = local.with_suffix(".cmd")
        local_records, local_reason = _local_package_runner_record(
            root, local, target, digest_file
        )
        records.extend(local_records)
        install_tree, install_tree_reason = _directory_record(
            root,
            root / "node_modules",
            "package-runner-install-tree",
            digest_file,
        )
        records.append(install_tree)
        if "--no-install" not in arguments:
            unsafe.append("package-runner-download-unapproved")
        elif local_reason or install_tree_reason:
            unsafe.append("package-runner-download-unapproved")
    return records, reasons, unsafe


def _go_identity(
    *,
    root: Path,
    environment: dict[str, str],
    digest_file: Callable[[Path], str],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    records, reasons = _config_records(
        root, ("go.mod", "go.sum", "go.work", "go.work.sum", ".go-version"),
        digest_file,
    )
    unsafe: list[str] = []
    toolchain_mode = environment.get("GOTOOLCHAIN", "")
    if toolchain_mode != "local":
        unsafe.append("go-toolchain-download-unapproved")
    if environment.get("GOPROXY", "").lower() != "off":
        unsafe.append("go-module-download-unapproved")
    return records, reasons, unsafe


def _cargo_identity(
    arguments: list[str],
    *,
    root: Path,
    environment: dict[str, str],
    resolve_executable: Callable[[str], Path | None],
    digest_file: Callable[[Path], str],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    records, reasons = _config_records(
        root,
        (
            "Cargo.toml", "Cargo.lock", "rust-toolchain", "rust-toolchain.toml",
            ".cargo/config", ".cargo/config.toml",
        ),
        digest_file,
    )
    rustc, rustc_reason = _runtime_record(root, "rustc", resolve_executable, digest_file)
    records.append(rustc)
    if rustc_reason:
        reasons.append(rustc_reason)
    unsafe: list[str] = []
    if any(
        path.exists() or path.is_symlink()
        for path in (root / "rust-toolchain", root / "rust-toolchain.toml")
    ):
        unsafe.append("rust-toolchain-download-unapproved")
    offline_environment = environment.get("CARGO_NET_OFFLINE", "").lower()
    if "--offline" not in arguments and offline_environment not in {"1", "true"}:
        unsafe.append("cargo-registry-download-unapproved")
    return records, reasons, unsafe


def collect(
    argv: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    resolve_executable: Callable[[str], Path | None],
    digest_file: Callable[[Path], str],
) -> dict[str, Any]:
    """Return one content-free runtime binding for a recognized command."""

    profile = click_verification_adapters.command_profile(argv)
    if profile is None:
        return {
            "version": VERSION,
            "adapter_id": "legacy",
            "status": "complete",
            "digest": click_capability.digest({"version": VERSION, "adapter": "legacy"}),
            "reason_codes": [],
        }
    adapter_id = str(profile["adapter_id"])
    executable, arguments = click_capability.command_parts(argv)
    records: list[dict[str, Any]] = []
    reasons: list[str] = []
    unsafe: list[str] = []

    if adapter_id in {
        "node-test-v1", "node-check-v1", "javascript-package-script-v1",
        "vitest-v1", "jest-v1", "typescript-v1",
    } or executable in {"npx", "pnpx", "bunx"} or (
        adapter_id == "content-validation-v1"
        and executable in {"npm", "pnpm", "yarn", "bun"}
    ):
        records, reasons, unsafe = _node_identity(
            executable,
            arguments,
            root=cwd,
            resolve_executable=resolve_executable,
            digest_file=digest_file,
        )
    elif adapter_id == "go-v1":
        records, reasons, unsafe = _go_identity(
            root=cwd, environment=environment, digest_file=digest_file
        )
    elif adapter_id == "cargo-v1":
        records, reasons, unsafe = _cargo_identity(
            arguments,
            root=cwd,
            environment=environment,
            resolve_executable=resolve_executable,
            digest_file=digest_file,
        )
    elif adapter_id == "jvm-v1":
        records, reasons = _config_records(
            cwd,
            (
                "build.gradle", "build.gradle.kts", "settings.gradle",
                "settings.gradle.kts", "pom.xml", "gradle.properties",
                "gradle/wrapper/gradle-wrapper.jar",
                "gradle/wrapper/gradle-wrapper.properties",
                ".mvn/wrapper/maven-wrapper.jar",
                ".mvn/wrapper/maven-wrapper.properties",
            ),
            digest_file,
        )
        java, java_reason = _runtime_record(
            cwd, "java", resolve_executable, digest_file
        )
        records.append(java)
        if java_reason:
            reasons.append(java_reason)
        if executable in {"gradlew", "gradlew.bat", "mvnw", "mvnw.cmd"}:
            unsafe.append("wrapper-download-unapproved")
        elif executable == "gradle" and "--offline" not in arguments:
            unsafe.append("gradle-dependency-download-unapproved")
        elif executable == "mvn" and not {"-o", "--offline"}.intersection(arguments):
            unsafe.append("maven-dependency-download-unapproved")
    elif adapter_id == "dotnet-v1":
        records, reasons = _config_records(
            cwd,
            (
                "global.json", "NuGet.config", "Directory.Build.props",
                "Directory.Build.targets", "Directory.Packages.props",
            ),
            digest_file,
        )
        projects, project_reasons = _top_level_project_records(
            cwd, (".csproj", ".fsproj", ".vbproj", ".sln"), digest_file
        )
        records.extend(projects)
        reasons.extend(project_reasons)
        if "--no-restore" not in arguments:
            unsafe.append("dotnet-restore-unapproved")
    elif adapter_id == "native-build-v1":
        records, reasons = _config_records(
            cwd,
            (
                "CMakeLists.txt", "CMakePresets.json", "CMakeUserPresets.json",
                "CMakeCache.txt", "CTestTestfile.cmake", "Makefile",
            ),
            digest_file,
        )
    elif adapter_id == "content-validation-v1":
        records, reasons = _config_records(
            cwd,
            (
                "package.json", "package-lock.json", "pyproject.toml",
                ".yamllint", ".yamllint.yaml", ".yamllint.yml",
                ".markdownlint.json", ".markdownlint.jsonc",
                ".markdownlint.yaml", ".markdownlint.yml", ".sqlfluff",
            ),
            digest_file,
        )
        if executable == "check-jsonschema":
            schema_values = [
                item.split("=", 1)[1]
                for item in arguments
                if item.startswith("--schemafile=")
            ]
            for index, item in enumerate(arguments[:-1]):
                if item == "--schemafile":
                    schema_values.append(arguments[index + 1])
            if any(value.startswith(("http://", "https://")) for value in schema_values):
                unsafe.append("external-schema-fetch-unapproved")
            else:
                unsafe.append("schema-reference-network-unbounded")

    reasons = sorted(set(reasons))
    unsafe = sorted(set(unsafe))
    status = "unsafe" if unsafe else "incomplete" if reasons else "complete"
    payload = {
        "version": VERSION,
        "adapter_id": adapter_id,
        "adapter_version": int(profile["adapter_version"]),
        "profile": str(profile["profile"]),
        "records": records,
        "reason_codes": reasons,
        "unsafe_reasons": unsafe,
    }
    return {
        "version": VERSION,
        "adapter_id": adapter_id,
        "status": status,
        "digest": click_capability.digest(payload),
        "reason_codes": unsafe or reasons,
        "component_digests": {
            f"{index}:{record.get('role', 'unknown')}": click_capability.digest(
                {"record": record}
            )
            for index, record in enumerate(records)
        },
    }


def group_binding(records: list[dict[str, Any]]) -> dict[str, Any]:
    identities = [record.get("runtime_identity") for record in records]
    if any(not isinstance(identity, dict) for identity in identities):
        return {
            "status": "incomplete",
            "digest": "",
            "reason_codes": ["runtime-identity-missing"],
        }
    statuses = {str(identity.get("status", "incomplete")) for identity in identities}
    status = "unsafe" if "unsafe" in statuses else "incomplete" if "incomplete" in statuses else "complete"
    reasons = sorted({
        str(reason)
        for identity in identities
        for reason in identity.get("reason_codes", [])
        if isinstance(reason, str) and reason
    })
    return {
        "status": status,
        "digest": click_capability.digest(
            {"version": VERSION, "identities": identities}
        ),
        "reason_codes": reasons,
    }
