"""Local preparation of the native CPython observation companion.

Automatic preparation retries after capability changes and caches the
artifact outside the project. It installs nothing and never blocks the original
check on failure. The artifact is an input, not evidence or reuse authorization.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import sysconfig
import tempfile

if __package__:
    from . import click_import_bootstrap
else:
    import click_import_bootstrap

(inventory, observation_inputs) = (
    click_import_bootstrap.load_siblings(
        __package__,
        "click_test_inventory",
        "click_observation_inputs",
    )
)

PROFILE = observation_inputs.PROFILE
DARWIN_PROFILE = observation_inputs.DARWIN_PROFILE
WINDOWS_PROFILE = observation_inputs.WINDOWS_PROFILE
PROFILES = observation_inputs.PROFILES
profiles = observation_inputs.profiles
STATE_FIELD = "authoritative_observer"
STATE_VERSION = profiles.STATE_VERSION
SUPPORTED_STRACE_VERSION = "6.8"
DIGEST = profiles.DIGEST
ARTIFACT_ID = profiles.ARTIFACT_ID
STATE_FIELDS = profiles.STATE_FIELDS
BACKEND_FIELDS = profiles.BACKEND_FIELDS
PROFILE_BACKENDS = profiles.BACKENDS
ARTIFACT_NAMES = {profile: {"linux": "monitor.so", "darwin": "_click_observer_companion.so",
                           "win32": "_click_observer_companion.pyd"}[platform]
                  for profile, platform in profiles.PLATFORMS.items()}
BOOTSTRAP_PROFILES = frozenset(profile for profile, platform in profiles.PLATFORMS.items() if platform != "linux")


def _digest_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _strace_identity(project: Path) -> tuple[Path, dict]:
    executable = inventory.trusted_executable("strace", project)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("LD_") and key not in (
            "GCONV_PATH", "PYTHONHOME", "PYTHONPATH",
        )
    }
    completed = subprocess.run(
        [str(executable), "--version"], capture_output=True, timeout=3,
        check=False, env=environment,
    )
    output = bytes(completed.stdout or b"") + bytes(completed.stderr or b"")
    match = re.search(rb"\bstrace\s+--\s+version\s+([A-Za-z0-9._+-]+)", output)
    if completed.returncode or match is None:
        raise inventory.AnalysisError("strace-version-unavailable")
    version = match.group(1).decode("ascii", errors="strict")
    if version != SUPPORTED_STRACE_VERSION:
        raise inventory.AnalysisError("unsupported-strace-version")
    capability = subprocess.run(
        [str(executable), "-D", "-f", "-qq", "-e", "trace=none", "-o", os.devnull,
        "--", str(executable), "--version"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, check=False,
        env=environment,
    )
    if capability.returncode:
        raise inventory.AnalysisError("strace-capability-unavailable")
    return executable, {
        "name": "strace", "version": version, "digest": _digest_file(executable)
    }


def _darwin_identity(project: Path) -> tuple[Path, dict]:
    (observer_macos,) = click_import_bootstrap.load_siblings(
        __package__, "click_observer_macos"
    )
    if sys.platform != "darwin" or not observer_macos.has_privilege():
        raise inventory.AnalysisError("fs-usage-privilege-unavailable")
    executable = inventory.trusted_executable("fs_usage", project)
    if not observer_macos.native_fs_usage(str(executable)):
        raise inventory.AnalysisError("fs-usage-backend-unavailable")
    version = observer_macos.probe_macos_version()
    if not version:
        raise inventory.AnalysisError("macos-version-unavailable")
    return executable, {
        "name": "fs_usage",
        "version": version,
        "digest": _digest_file(executable),
    }


def _windows_identity(project: Path) -> tuple[tuple[Path, Path], dict]:
    if sys.platform != "win32":
        raise inventory.AnalysisError("windows-etw-backend-unavailable")
    (observer_windows,) = click_import_bootstrap.load_siblings(
        __package__, "click_observer_windows"
    )
    logman = inventory.trusted_executable("logman", project)
    tracerpt = inventory.trusted_executable("tracerpt", project)
    if (
        not observer_windows.native_windows_tool(str(logman), "logman.exe")
        or not observer_windows.native_windows_tool(str(tracerpt), "tracerpt.exe")
    ):
        raise inventory.AnalysisError("windows-etw-backend-unavailable")
    version = observer_windows.probe_windows_version()
    digest = observer_windows.combined_backend_digest(
        _digest_file(logman), _digest_file(tracerpt)
    )
    if not version or not digest:
        raise inventory.AnalysisError("windows-etw-identity-unavailable")
    return (logman, tracerpt), {
        "name": "windows-etw",
        "version": version,
        "digest": digest,
    }


def _profile_for_current_runtime() -> str:
    return profiles.current()


def _backend_identity(project: Path, profile: str) -> tuple[object, dict]:
    if profiles.PLATFORMS.get(profile) == "linux":
        return _strace_identity(project)
    if profiles.PLATFORMS.get(profile) == "darwin":
        return _darwin_identity(project)
    if profiles.PLATFORMS.get(profile) == "win32":
        return _windows_identity(project)
    raise inventory.AnalysisError("unsupported-native-runtime")


def _compiler_identity(project: Path, profile: str) -> tuple[Path, str]:
    name = "cl" if profiles.PLATFORMS.get(profile) == "win32" else "cc"
    compiler = inventory.trusted_executable(name, project)
    digest = _digest_file(compiler)
    if profiles.PLATFORMS.get(profile) == "win32":
        linker = inventory.trusted_executable("link", project)
        digest = hashlib.sha256(
            f"cl:{digest}\nlink:{_digest_file(linker)}\n".encode("ascii")
        ).hexdigest()
    return compiler, digest


NATIVE_RULE_FILES = (
    "click_observer_profiles.py", "click_observation_inputs.py",
    "click_observer_process_tree.py", "click_authoritative_observer.py",
    "click_observer_linux.py", "click_observer_macos.py", "click_observer_windows.py",
    "click_observer_runtime.py", "click_dependency_cache.py", "click_process.py",
)


def _source_digest(profile: str) -> str:
    native = _digest_file(Path(__file__).with_name("click_observer_native.c"))
    # A compiler can produce an identical artifact against incompatible header
    # or interpreter versions. Bind those inputs before cache lookup and reuse.
    include = Path(sysconfig.get_path("include"))
    headers = [(str(path.relative_to(include)), _digest_file(path))
               for path in sorted(include.rglob("*.h")) if path.is_file()]
    native = hashlib.sha256(json.dumps({
        "native": native, "rules": {
            name: _digest_file(Path(__file__).with_name(name)) for name in NATIVE_RULE_FILES
        },
        "python": sys.version, "soabi": sysconfig.get_config_var("SOABI"),
        "executable": _digest_file(Path(sys.executable).resolve()), "headers": headers,
    }, sort_keys=True).encode()).hexdigest()
    if profile not in BOOTSTRAP_PROFILES:
        return native
    bootstrap = _digest_file(
        Path(__file__).with_name("click_observer_bootstrap.py")
    )
    return hashlib.sha256(
        f"profile:{profile}\nnative:{native}\nbootstrap:{bootstrap}\n".encode("ascii")
    ).hexdigest()


def _build_environment(root: Path, profile: str) -> dict[str, str]:
    rejected = {
        "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "COMPILER_PATH",
        "GCC_EXEC_PREFIX", "DEPENDENCIES_OUTPUT", "GCONV_PATH",
    }
    if profiles.PLATFORMS.get(profile) != "win32":
        rejected.add("LIBRARY_PATH")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LD_", "DYLD_")) and key not in rejected
    }
    environment["PATH"] = os.pathsep.join(
        entry
        for entry in os.get_exec_path(environment)
        if Path(entry).is_absolute()
        and not inventory.inside(root, Path(entry).resolve())
    )
    return environment


def _build_command(
    profile: str,
    *,
    compiler: Path,
    include: Path,
    source: Path,
    artifact: Path,
) -> list[str]:
    if profiles.PLATFORMS.get(profile) == "linux":
        return [
            str(compiler), "-std=c11", "-shared", "-fPIC", "-O2", "-Wall",
            "-Wextra", "-Werror", "-Wl,--build-id=none", "-I", str(include),
            str(source), "-o", str(artifact), "-ldl",
        ]
    if profiles.PLATFORMS.get(profile) == "darwin":
        return [
            str(compiler), "-std=c11", "-bundle", "-fPIC", "-O2", "-Wall",
            "-Wextra", "-Werror", "-Wl,-undefined,dynamic_lookup", "-I",
            str(include), str(source), "-o", str(artifact),
        ]
    if profiles.PLATFORMS.get(profile) == "win32":
        library = Path(sys.base_prefix) / "libs"
        python_library = library / "python312.lib"
        if not python_library.is_file():
            raise inventory.AnalysisError("native-build-library-unavailable")
        return [
            str(compiler), "/nologo", "/LD", "/O2", "/W4",
            f"/I{include}", str(source), f"/Fe:{artifact}", "/link",
            f"/LIBPATH:{library}", str(python_library),
        ]
    raise inventory.AnalysisError("unsupported-native-runtime")


state_is_valid = profiles.prepared_state_is_valid

def control_state(build: dict) -> dict:
    artifact = Path(str(build.get("artifact", "")))
    value = {
        "version": STATE_VERSION,
        "profile": build.get("profile"),
        "artifact_id": artifact.parent.name,
        "artifact_digest": build.get("artifact_digest"),
        "source_digest": build.get("source_digest"),
        "compiler_digest": build.get("compiler_digest"),
        "backend": build.get("backend"),
    }
    if not state_is_valid(value):
        raise inventory.AnalysisError("native-runtime-state-invalid")
    return value


def state_from_verification(verification: object) -> dict | None:
    value = verification.get(STATE_FIELD) if isinstance(verification, dict) else None
    return json.loads(json.dumps(value)) if state_is_valid(value) else None


def artifact_path(value: dict) -> Path:
    if not state_is_valid(value):
        raise inventory.AnalysisError("native-runtime-state-invalid")
    return (
        Path(tempfile.gettempdir())
        / value["artifact_id"]
        / ARTIFACT_NAMES[value["profile"]]
    )


def validate(project: Path, value: object) -> Path | None:
    if not state_is_valid(value):
        return None
    assert isinstance(value, dict)
    try:
        root = inventory.project_root(project)
        if (
            not profiles.supported(value["profile"])
        ):
            return None
        artifact = artifact_path(value)
        directory = artifact.parent
        info = directory.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or (
                os.name != "nt"
                and (
                    info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o700
                )
            )
            or inventory.inside(root, directory.resolve())
        ):
            return None
        artifact_info = artifact.lstat()
        if (
            stat.S_ISLNK(artifact_info.st_mode)
            or not stat.S_ISREG(artifact_info.st_mode)
            or (os.name != "nt" and artifact_info.st_uid != os.getuid())
            or _digest_file(artifact) != value["artifact_digest"]
        ):
            return None
        _, compiler_digest = _compiler_identity(root, str(value["profile"]))
        if (_source_digest(str(value["profile"])) != value["source_digest"]
                or compiler_digest != value["compiler_digest"]):
            return None
        if value["profile"] in BOOTSTRAP_PROFILES:
            bootstrap = directory / "sitecustomize.py"
            source_bootstrap = Path(__file__).with_name(
                "click_observer_bootstrap.py"
            )
            if (
                bootstrap.is_symlink()
                or not bootstrap.is_file()
                or _digest_file(bootstrap) != _digest_file(source_bootstrap)
            ):
                return None
        _, backend = _backend_identity(root, str(value["profile"]))
        if backend != value["backend"]:
            return None
        return artifact
    except (OSError, ValueError, subprocess.SubprocessError, UnicodeError):
        return None


def _cached_build(directory: Path, selected: str, source_digest: str,
                  compiler_digest: str, backend: dict) -> dict:
    artifact = directory / ARTIFACT_NAMES[selected]
    bootstrap = directory / "sitecustomize.py"
    build_record = directory / "build.json"
    try:
        info = directory.lstat()
        cached = json.loads(build_record.read_text(encoding="utf-8"))
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or (
                os.name != "nt"
                and (
                    info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o700
                )
            )
            or not artifact.is_file()
            or artifact.is_symlink()
            or cached.get("artifact") != str(artifact)
            or cached.get("profile") != selected
            or cached.get("source_digest") != source_digest
            or cached.get("compiler_digest") != compiler_digest
            or cached.get("backend") != backend
            or cached.get("artifact_digest") != _digest_file(artifact)
            or (
                selected in BOOTSTRAP_PROFILES
                and (
                    not bootstrap.is_file()
                    or bootstrap.is_symlink()
                    or _digest_file(bootstrap)
                    != _digest_file(
                        Path(__file__).with_name("click_observer_bootstrap.py")
                    )
                )
            )
            or cached.get("candidate_only") is not True
            or cached.get("authority") is not False
        ):
            raise inventory.AnalysisError("native-build-cache-invalid")
        return cached
    except (OSError, ValueError, json.JSONDecodeError) as error:
        if isinstance(error, inventory.AnalysisError):
            raise
        raise inventory.AnalysisError("native-build-cache-invalid") from error


def prepare(project: Path, *, profile: str | None = None) -> dict:
    root = inventory.project_root(project)
    selected = _profile_for_current_runtime() if profile is None else profile
    if (
        selected not in PROFILES
        or selected != _profile_for_current_runtime()
        or not profiles.supported(selected)
    ):
        raise inventory.AnalysisError("unsupported-native-runtime")
    source = Path(__file__).with_name("click_observer_native.c")
    include = Path(sysconfig.get_path("include")).resolve()
    if inventory.inside(root, include) or not (include / "Python.h").is_file():
        raise inventory.AnalysisError("native-build-headers-unavailable")
    compiler, compiler_digest = _compiler_identity(root, selected)
    _, backend = _backend_identity(root, selected)
    source_digest = _source_digest(selected)
    cache_key = hashlib.sha256(json.dumps({
        "profile": selected,
        "source_digest": source_digest,
        "compiler_digest": compiler_digest,
        "backend": backend,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]
    directory = Path(tempfile.gettempdir()) / f"click-native-observer-{cache_key}"
    if inventory.inside(root, directory.resolve()):
        raise inventory.AnalysisError("temporary-directory-inside-project")
    artifact = directory / ARTIFACT_NAMES[selected]
    if directory.exists() or directory.is_symlink():
        return _cached_build(directory, selected, source_digest, compiler_digest, backend)
    # Only publish a fully built directory. Concurrent builders own independent
    # staging paths; a failed build can never clean up another process's cache.
    with tempfile.TemporaryDirectory(prefix=directory.name + "-building-",
                                     dir=directory.parent) as temporary:
        staging = Path(temporary)
        candidate = staging / ARTIFACT_NAMES[selected]
        candidate_bootstrap = staging / "sitecustomize.py"
        command = _build_command(selected, compiler=compiler, include=include,
                                 source=source, artifact=candidate)
        environment = _build_environment(root, selected)
        if selected in BOOTSTRAP_PROFILES:
            candidate_bootstrap.write_bytes(
                Path(__file__).with_name("click_observer_bootstrap.py").read_bytes()
            )
        completed = subprocess.run(command, cwd=staging, env=environment, capture_output=True,
                                   timeout=30, check=False)
        if completed.returncode or not candidate.is_file():
            raise inventory.AnalysisError("native-build-failed")
        _, current_compiler_digest = _compiler_identity(root, selected)
        if _source_digest(selected) != source_digest or current_compiler_digest != compiler_digest:
            raise inventory.AnalysisError("native-build-input-changed")
        if selected in BOOTSTRAP_PROFILES:
            for child in staging.iterdir():
                if child not in {candidate, candidate_bootstrap}:
                    child.unlink(missing_ok=True)
        result = {"version": 1, "profile": selected, "artifact": str(artifact),
                  "artifact_digest": _digest_file(candidate),
                  "source_digest": source_digest,
                  "compiler_digest": compiler_digest,
                  "backend": backend,
                  "candidate_only": True, "authority": False}
        (staging / "build.json").write_text(json.dumps(result, sort_keys=True) + "\n")
        try:
            staging.rename(directory)
        except OSError:
            # A concurrent winner publishes a nonempty directory atomically.
            # Validate its identities instead of replacing or deleting it.
            if not directory.exists() and not directory.is_symlink():
                raise
        return _cached_build(directory, selected, source_digest, compiler_digest, backend)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("operation", choices=("prepare",))
    parser.add_argument("--project", type=Path, required=True)
    options = parser.parse_args()
    try:
        print(json.dumps(prepare(options.project), sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        reason = str(error) if isinstance(error, inventory.AnalysisError) else "native-build-unavailable"
        print(json.dumps({"status": "unavailable", "reason": reason, "authority": False}))
        return 2


def _automatic_context(project: Path, verification: dict, candidates: list) -> str:
    """Cheap retry scheduling identity, never a passing-input fingerprint.

    No tool runs here. Metadata changes may request a new preparation; every
    artifact, executable and input still undergoes the normal content checks.
    Only the digest is stored, never environment values or host paths.
    """
    def identity(path):
        try:
            path = Path(path).resolve(strict=True)
            info = path.stat()
            return [str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        except (OSError, ValueError, TypeError):
            return None
    tools = {}
    for name in {"linux": ("cc", "strace"), "darwin": ("cc", "fs_usage"),
                 "win32": ("cl", "link", "logman", "tracerpt")}.get(sys.platform, ()):
        try:
            tools[name] = identity(inventory.trusted_executable(name, project))
        except (OSError, ValueError, inventory.AnalysisError):
            tools[name] = None
    runtime = state_from_verification(verification)
    include = sysconfig.get_path("include")
    value = {
        "python": identity(sys.executable), "version": sys.version,
        "profile": profiles.current(), "tools": tools,
        "collector_rules": {name: identity(Path(__file__).with_name(name)) for name in
                            (*NATIVE_RULE_FILES, "click_observer_native.c", "click_observer_bootstrap.py")},
        "headers": [identity(Path(include) / name) for name in ("Python.h", "pyconfig.h")]
                   if isinstance(include, str) else None,
        "library": identity(Path(sys.base_prefix) / "libs" / "python312.lib") if sys.platform == "win32" else None,
        "commands": [argv[:3] for argv in candidates],
        "environment": {key: os.environ.get(key) for key in
                        ("PATH", "PYTHONHASHSEED", "PYTHONDONTWRITEBYTECODE", "INCLUDE", "LIB", "LIBPATH")},
        "privilege": getattr(os, "geteuid", lambda: None)(),
        "policy": identity(project / ".click" / "evidence-reuse.json"),
        "artifact": identity(artifact_path(runtime)) if runtime else None,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def prepare_automatic(project: Path, verification: dict, groups: dict) -> None:
    """Retry preparation when capabilities change; failure preserves verification.

    This state is only a local capability result. Every execution/reuse still
    validates the artifact, backend, runtime and complete signed input records.
    An explicit observer auto control clears the attempt for a provisioning retry.
    """
    candidates = [checks[0].get("argv", []) for checks in groups.values() if len(checks) == 1]
    if not any(
        len(argv) >= 3 and argv[1] == "-m" and argv[2] in {"unittest", "pytest"}
        for argv in candidates
    ):
        return
    try:
        project = inventory.project_root(project)
        context = _automatic_context(project, verification, candidates)
        if verification.get("automatic_observer_context") == context and (
            state_from_verification(verification) is not None
            or verification.get("automatic_observer_attempt")
        ):
            return
        attempt_context = _automatic_context(
            project, {**verification, STATE_FIELD: None}, candidates
        )
    except Exception:
        verification["automatic_observer_attempt"] = {
            "status": "unavailable", "reason": "native-preparation-unavailable",
        }
        return
    verification["automatic_observer_context"] = attempt_context
    verification.pop(STATE_FIELD, None)
    if any(
        os.environ.get(key, default) != default
        for key, default in (("PYTHONHASHSEED", "0"), ("PYTHONDONTWRITEBYTECODE", "1"))
    ):
        verification["automatic_observer_attempt"] = {
            "status": "unavailable", "reason": "deterministic-environment-required",
        }
        return
    try:
        root = project
        if (root / ".click" / "evidence-reuse.json").exists():
            # An owner already selected a reuse route. Do not silently replace
            # its scoped baseline with coarser automatic directory observations.
            # This disables optional capture only; policy validity is still
            # checked by the existing reuse engine. Explicit capture is allowed.
            verification["automatic_observer_attempt"] = {
                "status": "unavailable", "reason": "owner-reuse-policy-selected",
            }
            return
        executable = Path(sys.executable).resolve(strict=True)
        if not any(
            len(argv) >= 3 and argv[1] == "-m" and argv[2] in {"unittest", "pytest"}
            and inventory.trusted_executable(argv[0], root).resolve(strict=True) == executable
            for argv in candidates
        ):
            verification["automatic_observer_attempt"] = {
                "status": "unavailable", "reason": "runtime-mismatch",
            }
            return
        value = control_state(prepare(project))
        if validate(project, value) is None:
            raise inventory.AnalysisError("native-companion-unavailable")
        verification[STATE_FIELD] = value
        verification["automatic_observer_attempt"] = {"status": "available", "reason": ""}
        verification["automatic_observer_context"] = _automatic_context(project, verification, candidates)
    except Exception as error:
        verification["automatic_observer_attempt"] = {
            "status": "unavailable", "reason": str(error) if isinstance(error, inventory.AnalysisError)
            else "native-preparation-unavailable",
        }


if __name__ == "__main__":
    raise SystemExit(main())
