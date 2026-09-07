"""Content-bound inputs for explicit local authoritative profiles.

External runtime locations are represented by fixed roles and relative names;
raw host paths, file contents and environment values are not persisted. This
module validates snapshots, never approvals or caller claims of completeness.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import site
import stat
import sys
import sysconfig
import tempfile

MAX_INPUTS = 4096
MAX_INDEX_FILES = 100_000
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
PROFILE = "linux-cpython3123-strace68-v1"
DARWIN_PROFILE = "darwin-cpython3123-fsusage-v1"
WINDOWS_PROFILE = "windows-cpython3123-etw-v1"
PROFILES = frozenset({PROFILE, DARWIN_PROFILE, WINDOWS_PROFILE})
DIGEST = re.compile(r"^[0-9a-f]{64}$")
ARTIFACT_ID = re.compile(r"^click-native-observer-[a-zA-Z0-9_-]{1,64}$")


class InputError(ValueError):
    pass


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode()).hexdigest()


def metadata(path: Path):
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    # Reading an input may legitimately refresh atime.  It is not part of the
    # value consumed by the target, so binding it would make the observer
    # invalidate its own otherwise stable snapshot.  Identity, permissions,
    # size and content-changing timestamps remain bound.
    return [getattr(value, name, 0) for name in (
        "st_mode", "st_ino", "st_dev", "st_nlink", "st_uid", "st_gid", "st_size",
        "st_mtime_ns", "st_ctime_ns", "st_rdev")]


def _read_flags() -> int:
    return (
        os.O_RDONLY
        | int(getattr(os, "O_CLOEXEC", 0))
        | int(getattr(os, "O_NOFOLLOW", 0))
        | int(getattr(os, "O_BINARY", 0))
    )


def _lexical_canonical_path(path: Path) -> Path:
    absolute = Path(os.path.normpath(os.path.abspath(path)))
    try:
        # Resolve filesystem aliases in parent components while retaining the
        # final lexical component so symlink identity remains independently
        # observable from the target it resolves to.
        return absolute.parent.resolve(strict=False) / absolute.name
    except (OSError, RuntimeError):
        return absolute


def _path_key(path: Path) -> str:
    return os.path.normcase(str(_lexical_canonical_path(path)))


def relative_name(value) -> bool:
    return bool(isinstance(value, str) and len(value) <= 4096 and "\\" not in value
                and not value.startswith("/") and not any(ord(c) < 32 or ord(c) == 127 for c in value)
                and (value == "" or all(part not in ("", ".", "..") for part in value.split("/"))))


def _profile_platform(profile: str) -> str:
    return {
        PROFILE: "linux",
        DARWIN_PROFILE: "darwin",
        WINDOWS_PROFILE: "win32",
    }.get(profile, "")


def _secure_artifact_directory(artifact: Path) -> bool:
    try:
        info = artifact.lstat()
        if artifact.is_symlink() or not artifact.is_dir():
            return False
        if os.name != "nt" and (
            info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
        ):
            return False
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _portable_runtime_roots(project: Path, artifact: Path) -> dict[str, Path]:
    resolved_project = project.resolve()
    executable = Path(sys.executable).resolve()
    candidates = {
        "project": resolved_project,
        "project-parent": resolved_project.parent,
        "companion": artifact.resolve(),
        "stdlib": Path(sysconfig.get_path("stdlib")).resolve(),
        "platform-stdlib": Path(sysconfig.get_path("platstdlib")).resolve(),
        "site-packages": Path(sysconfig.get_path("purelib")).resolve(),
        "platform-packages": Path(sysconfig.get_path("platlib")).resolve(),
        "user-packages": Path(site.getusersitepackages()).resolve(),
        "binaries": executable.parent,
        "executable-prefix": Path(sys.prefix).resolve(),
        "base-prefix": Path(sys.base_prefix).resolve(),
    }
    environment_prefix = Path(sys.prefix).resolve()
    if environment_prefix != Path(sys.base_prefix).resolve():
        # A virtual environment is part of the interpreter input surface.
        # Index it fully so pyvenv.cfg, launcher symlinks, and installed
        # framework files are all available to the post-execution snapshot.
        candidates["environment-prefix"] = environment_prefix
    if sys.platform == "darwin":
        candidates.update({
            "host-root": Path("/"),
            "system-libraries": Path("/System/Library"),
            "system-frameworks": Path("/System/Library/Frameworks"),
            "local-frameworks": Path("/Library/Frameworks"),
            "usr-libraries": Path("/usr/lib"),
            "system-config": Path("/etc"),
            "private-config": Path("/private/etc"),
            "timezone": Path("/usr/share/zoneinfo"),
        })
    elif sys.platform == "win32":
        system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
        if not system_root:
            raise InputError("windows-system-root-unavailable")
        windows = Path(system_root).resolve()
        base_prefix = Path(sys.base_prefix).resolve()
        python_distribution = (
            base_prefix.parents[1]
            if len(base_prefix.parents) > 1
            else base_prefix.parent
        )
        candidates.update({
            "windows-root": windows,
            "system32": (windows / "System32").resolve(),
            "windows-appcompat": (windows / "AppPatch").resolve(),
            "runtime-dlls": (base_prefix / "DLLs").resolve(),
            "python-build-modules": (
                python_distribution / "Modules"
            ).resolve(),
        })
    return candidates


def runtime_roots(
    project: Path, artifact_id: str, *, profile: str = PROFILE
) -> dict[str, Path]:
    if (
        profile not in PROFILES
        or sys.platform != _profile_platform(profile)
        or sys.implementation.name != "cpython"
        or sys.version_info[:3] != (3, 12, 3)
        or not ARTIFACT_ID.fullmatch(artifact_id)
    ):
        raise InputError("unsupported-input-runtime")
    artifact = Path(tempfile.gettempdir()) / artifact_id
    if not _secure_artifact_directory(artifact):
        raise InputError("native-artifact-unavailable")
    if profile != PROFILE:
        result = _portable_runtime_roots(project, artifact)
        for role, path in result.items():
            if role == "project":
                continue
            if path == result["project"] or result["project"] in path.parents:
                raise InputError("runtime-root-inside-project")
        return result
    multiarch = sysconfig.get_config_var("MULTIARCH")
    if not isinstance(multiarch, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", multiarch):
        raise InputError("unsupported-runtime-library-layout")
    resolved_project = project.resolve()
    python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    executable_directory = Path(sys.executable).resolve().parent
    user_site = Path(site.getusersitepackages())
    result = {
        "project": resolved_project,
        "project-parent": resolved_project.parent,
        "companion": artifact.resolve(),
        "stdlib": Path(sysconfig.get_path("stdlib")).resolve(),
        "distro-stdlib": Path("/usr/lib") / python_version,
        "system-packages": Path("/usr/lib/python3/dist-packages"),
        "local-packages": Path("/usr/local/lib") / python_version / "dist-packages",
        "user-packages": user_site,
        "libraries": (Path("/usr/lib") / multiarch).resolve(),
        "loader-libraries": Path("/lib") / multiarch,
        "gconv": Path("/usr/lib") / multiarch / "gconv",
        "binaries": executable_directory,
        "executable-lib": executable_directory / "lib",
        "executable-modules": executable_directory / "Modules",
        "locale": Path("/usr/lib/locale"),
        "message-locale": Path("/usr/share/locale"),
        "locale-langpack": Path("/usr/share/locale-langpack"),
        "timezone": Path("/usr/share/zoneinfo"),
        "system-config": Path("/etc"),
        "python-config": Path("/etc") / python_version,
        "host-root": Path("/"),
        "usr-root": Path("/usr"),
        "lib-root": Path("/usr/lib"),
        "local-root": Path("/usr/local"),
        "local-lib-root": Path("/usr/local/lib"),
    }
    environment_prefix = Path(sys.prefix).resolve()
    if environment_prefix != Path(sys.base_prefix).resolve():
        result["environment-prefix"] = environment_prefix
    for role, path in result.items():
        if role == "project":
            continue
        if path == result["project"] or result["project"] in path.parents:
            raise InputError("runtime-root-inside-project")
    return result


class InputSnapshot:
    """Take bounded pre-execution metadata, then bind actual consumed inputs.

    Repository content is fingerprinted before execution. Runtime files must
    retain their pre-execution metadata while their content is fingerprinted.
    Any unreadable, special, changing or unindexed input invalidates coverage.
    """

    def __init__(
        self, project: Path, artifact_id: str, *, profile: str = PROFILE
    ):
        self.roots = runtime_roots(project, artifact_id, profile=profile)
        self.artifact_id = artifact_id
        self.profile = profile
        self.before: dict[str, list | None] = {}
        self.enumerated: set[str] = set()
        self.project_content: dict[str, str] = {}
        self.total_bytes = 0
        self._index()

    def _index(self) -> None:
        seen = set()
        for role, root in self.roots.items():
            # Parent locations are needed for path lookup and symlink identity,
            # not as permission to walk the entire host filesystem.
            shallow = role.endswith("-root") or role in (
                "binaries", "system-config", "libraries", "loader-libraries",
                "project-parent", "executable-prefix", "base-prefix",
                "system-libraries", "system-frameworks", "local-frameworks",
                "usr-libraries", "system32",
            )
            depth_limit = {
                # System32 language resources live one directory beneath the
                # main runtime files. AppCompat databases use at most two
                # levels on supported Windows hosts.
                "system32": 1,
                "windows-appcompat": 2,
            }.get(role, 0 if shallow else None)
            pending = [(root, 0)]
            while pending:
                path, depth = pending.pop()
                key = _path_key(path)
                if key in seen:
                    continue
                seen.add(key)
                value = metadata(path)
                self.before[key] = value
                if len(self.before) > MAX_INDEX_FILES:
                    raise InputError("runtime-index-limit")
                if value is None:
                    continue
                mode = value[0]
                if stat.S_ISDIR(mode):
                    try:
                        entries = sorted(path.iterdir())
                    except OSError as error:
                        raise InputError("runtime-index-unreadable") from error
                    # Directory enumeration itself can update atime.
                    self.before[key] = metadata(path)
                    self.enumerated.add(key)
                    for child in entries:
                        if role == "project" and child.name == ".git":
                            continue
                        if depth_limit is not None and depth >= depth_limit:
                            self.before[_path_key(child)] = metadata(child)
                            if len(self.before) > MAX_INDEX_FILES:
                                raise InputError("runtime-index-limit")
                        else:
                            pending.append((child, depth + 1))
                elif stat.S_ISLNK(mode):
                    os.readlink(path)
                    self.before[key] = metadata(path)
                elif stat.S_ISREG(mode) and role == "project":
                    value_digest, _ = self._fingerprint(path, {"read"}, warm=True)
                    self.project_content[key] = value_digest
                    self.before[key] = metadata(path)

    def locator(self, path: Path) -> tuple[str, str]:
        path = _lexical_canonical_path(path)
        candidates = []
        for role, root in self.roots.items():
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                continue
            relative = "" if relative == "." else relative
            if role.endswith("-root") and "/" in relative:
                continue
            if role in ("binaries", "system-config") and "/" in relative:
                continue
            if role == "project" and (relative == ".git" or relative.startswith(".git/")):
                raise InputError("git-internal-input")
            if relative_name(relative):
                candidates.append((len(root.parts), role, relative))
        if not candidates:
            raise InputError("external-input-outside-profile")
        _, role, relative = max(candidates)
        return role, relative

    def _fingerprint(self, path: Path, operations: set[str], *, warm: bool = False) -> tuple[str, str]:
        before = metadata(path)
        if before is None:
            return digest(["missing"]), "missing"
        mode = before[0]
        payload = [before]
        if stat.S_ISLNK(mode):
            payload.append(os.readlink(path))
            kind = "symlink"
        elif stat.S_ISDIR(mode):
            kind = "directory"
            if "enumerate" in operations:
                entries = []
                with os.scandir(path) as iterator:
                    for entry in iterator:
                        info = entry.stat(follow_symlinks=False)
                        entries.append([entry.name, entry.inode(), stat.S_IFMT(info.st_mode)])
                        if len(entries) > MAX_INDEX_FILES:
                            raise InputError("directory-input-limit")
                payload.append(sorted(entries))
        elif stat.S_ISREG(mode):
            kind = "file"
            if "read" in operations or "execute" in operations:
                if before[6] > MAX_FILE_BYTES or self.total_bytes + before[6] > MAX_INPUT_BYTES:
                    raise InputError("input-byte-limit")
                descriptor = os.open(path, _read_flags())
                with os.fdopen(descriptor, "rb") as stream:
                    content = stream.read(MAX_FILE_BYTES + 1)
                if len(content) > MAX_FILE_BYTES:
                    raise InputError("input-byte-limit")
                self.total_bytes += len(content)
                payload.append(hashlib.sha256(content).hexdigest())
        else:
            raise InputError("special-file-input")
        after = metadata(path)
        if before != after:
            raise InputError("input-changed-during-snapshot")
        return digest(payload), kind

    def _indexed_missing(self, path: Path) -> bool:
        candidate = path
        while _path_key(candidate) not in self.before:
            parent = candidate.parent
            if parent == candidate:
                return False
            if _path_key(parent) in self.enumerated:
                return True  # Its first unknown child was absent at preparation.
            candidate = parent
        return self.before[_path_key(candidate)] is None

    def records(self, inputs: dict[str, set[str]]) -> list[dict]:
        # Follow and bind each lexical symlink component as well as the final
        # resolved input. A replacement link must not reuse a target's old pass.
        expanded = {path: set(operations) for path, operations in inputs.items()}
        for raw, operations in list(expanded.items()):
            path = Path(raw)
            for parent in [*path.parents, path]:
                if parent.is_symlink():
                    expanded.setdefault(str(parent), set()).add("metadata")
            resolved = path.resolve(strict=False)
            if resolved != path:
                expanded.setdefault(str(resolved), set()).update(operations)
        if len(expanded) > MAX_INPUTS:
            raise InputError("observation-input-limit")
        canonical: dict[str, tuple[Path, set[str]]] = {}
        for raw, operations in sorted(expanded.items()):
            path = _lexical_canonical_path(Path(raw))
            key = _path_key(path)
            if key not in canonical:
                canonical[key] = (path, set())
            canonical[key][1].update(operations)
        output = []
        self.total_bytes = 0
        for key in sorted(canonical):
            path, operations = canonical[key]
            role, relative = self.locator(path)
            old = self.before.get(key)
            current = metadata(path)
            # Missing paths are valid only under a fully indexed parent.
            if key not in self.before and not self._indexed_missing(path):
                raise InputError("input-was-not-indexed")
            if old != current:
                raise InputError("input-changed-during-execution")
            fingerprint, kind = self._fingerprint(path, operations)
            if role == "project" and kind == "file" and "read" in operations:
                if self.project_content.get(key) != fingerprint:
                    raise InputError("project-input-content-changed")
            output.append({"root": role, "path": relative, "kind": kind,
                           "operations": sorted(operations), "digest": fingerprint})
        return sorted(output, key=lambda item: (item["root"], item["path"]))


def records_valid(records) -> bool:
    if not isinstance(records, list) or not records or len(records) > MAX_INPUTS:
        return False
    keys = []
    for row in records:
        if (not isinstance(row, dict) or set(row) != {"root", "path", "kind", "operations", "digest"}
                or not isinstance(row["root"], str) or not re.fullmatch(r"[a-z-]+", row["root"])
                or not relative_name(row["path"]) or row["kind"] not in ("file", "directory", "missing", "symlink")
                or not isinstance(row["digest"], str) or not DIGEST.fullmatch(row["digest"])
                or not isinstance(row["operations"], list) or not row["operations"]
                or any(op not in ("read", "metadata", "enumerate", "execute") for op in row["operations"])
                or row["operations"] != sorted(set(row["operations"]))):
            return False
        keys.append((row["root"], row["path"]))
    return keys == sorted(set(keys))


def records_current(
    project: Path,
    artifact_id: str,
    records: list[dict],
    *,
    profile: str = PROFILE,
) -> bool:
    if not records_valid(records):
        return False
    try:
        roots = runtime_roots(project, artifact_id, profile=profile)
        reader = object.__new__(InputSnapshot)
        reader.total_bytes = 0
        for row in records:
            root = roots.get(row["root"])
            if root is None:
                return False
            path = root / row["path"]
            if row["root"].endswith("-root") and "/" in row["path"]:
                return False
            fingerprint, kind = reader._fingerprint(path, set(row["operations"]))
            if fingerprint != row["digest"] or kind != row["kind"]:
                return False
        return True
    except (OSError, ValueError, RuntimeError):
        return False
