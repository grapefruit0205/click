"""Explicit preparation of the native CPython observation companion.

Preparation is an authorized implementation/build operation, never an implicit
installation hook or a verification-time compiler invocation. Its artifact is
an input to later observation, not an observation or reuse authorization.
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
STATE_FIELD = "authoritative_observer"
STATE_VERSION = 1
SUPPORTED_STRACE_VERSION = "6.8"
DIGEST = re.compile(r"^[0-9a-f]{64}$")
ARTIFACT_ID = re.compile(r"^click-native-observer-[a-zA-Z0-9_-]{1,64}$")
STATE_FIELDS = frozenset({
    "version", "profile", "artifact_id", "artifact_digest", "source_digest",
    "compiler_digest", "backend",
})
BACKEND_FIELDS = frozenset({"name", "version", "digest"})
PROFILE_BACKENDS = {
    PROFILE: ("strace", SUPPORTED_STRACE_VERSION),
    DARWIN_PROFILE: ("fs_usage", None),
    WINDOWS_PROFILE: ("windows-etw", None),
}
ARTIFACT_NAMES = {
    PROFILE: "monitor.so",
    DARWIN_PROFILE: "_click_observer_companion.so",
    WINDOWS_PROFILE: "_click_observer_companion.pyd",
}
BOOTSTRAP_PROFILES = frozenset({DARWIN_PROFILE, WINDOWS_PROFILE})


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
    return {
        "linux": PROFILE,
        "darwin": DARWIN_PROFILE,
        "win32": WINDOWS_PROFILE,
    }.get(sys.platform, "")


def _backend_identity(project: Path, profile: str) -> tuple[object, dict]:
    if profile == PROFILE:
        return _strace_identity(project)
    if profile == DARWIN_PROFILE:
        return _darwin_identity(project)
    if profile == WINDOWS_PROFILE:
        return _windows_identity(project)
    raise inventory.AnalysisError("unsupported-native-runtime")


def _compiler_identity(project: Path, profile: str) -> tuple[Path, str]:
    name = "cl" if profile == WINDOWS_PROFILE else "cc"
    compiler = inventory.trusted_executable(name, project)
    digest = _digest_file(compiler)
    if profile == WINDOWS_PROFILE:
        linker = inventory.trusted_executable("link", project)
        digest = hashlib.sha256(
            f"cl:{digest}\nlink:{_digest_file(linker)}\n".encode("ascii")
        ).hexdigest()
    return compiler, digest


def _source_digest(profile: str) -> str:
    native = _digest_file(Path(__file__).with_name("click_observer_native.c"))
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
    if profile != WINDOWS_PROFILE:
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
    if profile == PROFILE:
        return [
            str(compiler), "-std=c11", "-shared", "-fPIC", "-O2", "-Wall",
            "-Wextra", "-Werror", "-Wl,--build-id=none", "-I", str(include),
            str(source), "-o", str(artifact), "-ldl",
        ]
    if profile == DARWIN_PROFILE:
        return [
            str(compiler), "-std=c11", "-bundle", "-fPIC", "-O2", "-Wall",
            "-Wextra", "-Werror", "-Wl,-undefined,dynamic_lookup", "-I",
            str(include), str(source), "-o", str(artifact),
        ]
    if profile == WINDOWS_PROFILE:
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


def state_is_valid(value) -> bool:
    backend = value.get("backend") if isinstance(value, dict) else None
    profile = value.get("profile") if isinstance(value, dict) else None
    expected_backend = PROFILE_BACKENDS.get(profile)
    return bool(
        isinstance(value, dict)
        and set(value) == STATE_FIELDS
        and value.get("version") == STATE_VERSION
        and profile in PROFILES
        and isinstance(value.get("artifact_id"), str)
        and ARTIFACT_ID.fullmatch(value["artifact_id"])
        and all(
            isinstance(value.get(field), str) and DIGEST.fullmatch(value[field])
            for field in ("artifact_digest", "source_digest", "compiler_digest")
        )
        and isinstance(backend, dict)
        and set(backend) == BACKEND_FIELDS
        and expected_backend is not None
        and backend.get("name") == expected_backend[0]
        and (
            expected_backend[1] is None
            or backend.get("version") == expected_backend[1]
        )
        and isinstance(backend.get("version"), str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", backend["version"])
        is not None
        and isinstance(backend.get("digest"), str)
        and DIGEST.fullmatch(backend["digest"])
    )


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
            value["profile"] != _profile_for_current_runtime()
            or sys.implementation.name != "cpython"
            or sys.version_info[:3] != (3, 12, 3)
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


def discard(value: object) -> None:
    if not state_is_valid(value):
        return
    assert isinstance(value, dict)
    directory = artifact_path(value).parent
    try:
        if directory.parent == Path(tempfile.gettempdir()).resolve() and ARTIFACT_ID.fullmatch(directory.name):
            for child in directory.iterdir():
                child.unlink(missing_ok=True)
            directory.rmdir()
    except OSError:
        pass


def prepare(project: Path, *, profile: str | None = None) -> dict:
    root = inventory.project_root(project)
    selected = _profile_for_current_runtime() if profile is None else profile
    if (
        selected not in PROFILES
        or selected != _profile_for_current_runtime()
        or sys.implementation.name != "cpython"
        or sys.version_info[:3] != (3, 12, 3)
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
    bootstrap = directory / "sitecustomize.py"
    build_record = directory / "build.json"
    try:
        directory.mkdir(mode=0o700)
        if os.name != "nt":
            directory.chmod(0o700)
    except FileExistsError:
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
    command = _build_command(
        selected,
        compiler=compiler,
        include=include,
        source=source,
        artifact=artifact,
    )
    environment = _build_environment(root, selected)
    try:
        if selected in BOOTSTRAP_PROFILES:
            bootstrap.write_bytes(
                Path(__file__).with_name("click_observer_bootstrap.py").read_bytes()
            )
        completed = subprocess.run(command, cwd=directory, env=environment, capture_output=True,
                                   timeout=30, check=False)
        if completed.returncode or not artifact.is_file():
            raise inventory.AnalysisError("native-build-failed")
        _, current_compiler_digest = _compiler_identity(root, selected)
        if (
            _source_digest(selected) != source_digest
            or current_compiler_digest != compiler_digest
        ):
            raise inventory.AnalysisError("native-build-input-changed")
        if selected in BOOTSTRAP_PROFILES:
            for child in directory.iterdir():
                if child not in {artifact, bootstrap}:
                    child.unlink(missing_ok=True)
        result = {"version": 1, "profile": selected, "artifact": str(artifact),
                  "artifact_digest": _digest_file(artifact),
                  "source_digest": source_digest,
                  "compiler_digest": compiler_digest,
                  "backend": backend,
                  "candidate_only": True, "authority": False}
        build_record.write_text(json.dumps(result, sort_keys=True) + "\n")
        return result
    except BaseException:
        try:
            for child in directory.iterdir():
                child.unlink(missing_ok=True)
            directory.rmdir()
        except OSError:
            pass
        raise


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


if __name__ == "__main__":
    raise SystemExit(main())
