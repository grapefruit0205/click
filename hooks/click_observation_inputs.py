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

if __package__:
    from . import click_observer_profiles as profiles
else:
    import click_observer_profiles as profiles

MAX_INPUTS = 4096
MAX_INDEX_FILES = 100_000
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
PROFILE = "linux-cpython3123-strace68-v1"
DARWIN_PROFILE = "darwin-cpython3123-fsusage-v1"
WINDOWS_PROFILE = "windows-cpython3123-etw-v1"
PROFILES = profiles.PROFILES
DIGEST = re.compile(r"^[0-9a-f]{64}$")
ARTIFACT_ID = re.compile(r"^click-native-observer-[a-zA-Z0-9_-]{1,64}$")


class InputError(ValueError):
    pass


# Bump when the identity payload changes so an older receipt can never match
# a digest computed under the new rules; it simply reruns once.
FINGERPRINT_VERSION = 2


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode()).hexdigest()


def metadata(path: Path):
    try:
        value = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
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
    return profiles.PLATFORMS.get(profile, "")


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
        base_prefix = Path(sys.base_prefix).resolve()
        python_framework = (
            base_prefix.parents[1]
            if len(base_prefix.parents) > 1
            else base_prefix.parent
        )
        candidates.update({
            "python-framework": python_framework,
            "library-python-lib": Path("/Library/lib"),
            "host-root": Path("/"),
            "system-libraries": Path("/System/Library"),
            "system-frameworks": Path("/System/Library/Frameworks"),
            "local-frameworks": Path("/Library/Frameworks"),
            "usr-libraries": Path("/usr/lib"),
            "system-config": Path("/etc"),
            "private-config": Path("/private/etc"),
            "timezone": Path("/usr/share/zoneinfo").resolve(),
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
            "windows-system": (windows / "System32").resolve(),
            "windows-appcompat": (windows / "AppPatch").resolve(),
            "runtime-dlls": (base_prefix / "DLLs").resolve(),
            "python-build-modules": (
                python_distribution / "Modules"
            ).resolve(),
        })
    return candidates


def _linux_external_runtime_root(base_prefix: Path) -> Path | None:
    """Return one bounded non-system CPython distribution root.

    GitHub's setup-python layout keeps the executable under a platform child
    such as ``<version>/x64`` while a zip probe and supporting libraries live
    beside it under ``<version>/lib``.  Index that version directory as one
    runtime unit.  Custom interpreters without that layout stay bounded to
    their own prefix, and normal /usr or /usr/local installs need no extra
    root.
    """
    base_prefix = base_prefix.resolve()
    system_prefixes = (Path("/usr"), Path("/usr/local"))
    if any(
        base_prefix == prefix or prefix in base_prefix.parents
        for prefix in system_prefixes
    ):
        return None
    if base_prefix.name in {"x64", "arm64", "x86", "universal2"}:
        version_root = base_prefix.parent
        hosted_cache = version_root.parent.parent
        if (
            version_root.parent.name == "Python"
            and hosted_cache.name == "hostedtoolcache"
            and re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}(?:[-+][a-zA-Z0-9._-]+)?", version_root.name)
        ):
            # setup-python may not create ``<version>/lib`` even though
            # CPython probes it.  Recognize the stable hosted-toolcache
            # structure instead of relying on that optional directory.
            return version_root
        if (version_root / "lib").is_dir():
            return version_root
    return base_prefix


def _linux_hosted_runtime_probe_roots(runtime_root: Path) -> dict[str, Path]:
    """Return only the lib directories CPython probes above setup-python.

    These roots can be absent.  They are intentionally rooted at ``lib`` so
    the generic ``*-root`` lookup rule permits one filename without granting
    recursive access to the hosted tool cache or its installation prefix.
    """
    runtime_root = runtime_root.resolve()
    python_root = runtime_root.parent
    cache_root = python_root.parent
    install_root = cache_root.parent
    if (
        python_root.name != "Python"
        or cache_root.name != "hostedtoolcache"
        or not re.fullmatch(
            r"[0-9]+(?:\.[0-9]+){2}(?:[-+][a-zA-Z0-9._-]+)?",
            runtime_root.name,
        )
    ):
        return {}
    return {
        "runtime-version-lib-root": runtime_root / "lib",
        "runtime-channel-lib-root": python_root / "lib",
        "runtime-cache-lib-root": cache_root / "lib",
        "runtime-prefix-lib-root": install_root / "lib",
    }


def runtime_roots(
    project: Path, artifact_id: str, *, profile: str = PROFILE
) -> dict[str, Path]:
    if (
        profile not in PROFILES
        or sys.platform != _profile_platform(profile)
        or not profiles.supported(profile)
        or not ARTIFACT_ID.fullmatch(artifact_id)
    ):
        raise InputError("unsupported-input-runtime")
    artifact = Path(tempfile.gettempdir()) / artifact_id
    if not _secure_artifact_directory(artifact):
        raise InputError("native-artifact-unavailable")
    if _profile_platform(profile) != "linux":
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
        "ssl-config-root": Path("/etc/ssl"),
        "ssl-runtime-root": Path("/usr/lib/ssl"),
        "crypto-policy-root": Path("/proc/sys/crypto"),
        "terminfo": Path("/usr/share/terminfo"),
        "terminfo-config": Path("/etc/terminfo"),
        "user-config-root": Path.home(),
    }
    environment_prefix = Path(sys.prefix).resolve()
    if environment_prefix != Path(sys.base_prefix).resolve():
        result["environment-prefix"] = environment_prefix
    external_runtime = _linux_external_runtime_root(Path(sys.base_prefix))
    if external_runtime is not None:
        result["external-runtime"] = external_runtime
        result.update(_linux_hosted_runtime_probe_roots(external_runtime))
        # CPython's prefix discovery probes a stdlib archive above custom
        # installations too. Bind those literal files (including absence),
        # without granting a recursive ancestor-library input boundary.
        for index, ancestor in enumerate(executable_directory.parents):
            if index >= 64:
                raise InputError("runtime-ancestor-limit")
            label = "a" * (index // 26 + 1) + chr(ord("a") + index % 26)
            result[f"python-archive-{label}"] = ancestor / "lib" / f"python{sys.version_info.major}{sys.version_info.minor}.zip"
    # pytest searches every ancestor for configuration. Bind those literal
    # lookups, including absent files, without recursively indexing ancestors.
    config_names = {"ini": "pytest.ini", "hidden-ini": ".pytest.ini", "pyproject": "pyproject.toml",
                    "tox": "tox.ini", "setup": "setup.cfg", "conftest": "conftest.py",
                    "toml": "pytest.toml", "hidden-toml": ".pytest.toml", "setup-script": "setup.py"}
    for index, ancestor in enumerate(resolved_project.parents):
        if index >= 64:
            raise InputError("configuration-ancestor-limit")
        label = "a" * (index // 26 + 1) + chr(ord("a") + index % 26)
        for config_role, config in config_names.items():
            result[f"pytest-config-{label}-{config_role}"] = ancestor / config
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
        self,
        project: Path,
        artifact_id: str,
        *,
        profile: str = PROFILE,
        shared: "InputSnapshot | None" = None,
    ):
        self.roots = runtime_roots(project, artifact_id, profile=profile)
        self.artifact_id = artifact_id
        self.profile = profile
        self.before: dict[str, list | None] = {}
        self.enumerated: set[str] = set()
        self.project_content: dict[str, str] = {}
        self.total_bytes = 0
        if (
            shared is not None
            and shared.roots == self.roots
            and shared.profile == profile
        ):
            # The host runtime was indexed once for this process; only the
            # repository, which each check may have changed, is indexed again.
            self.before = dict(shared.before)
            self.enumerated = set(shared.enumerated)
            self._index(roles=frozenset({"project"}))
        else:
            self._index()

    def _index(self, roles: frozenset[str] | None = None) -> None:
        seen = set()
        for role, root in self.roots.items():
            if roles is not None and role not in roles:
                continue
            # Parent locations are needed for path lookup and symlink identity,
            # not as permission to walk the entire host filesystem.
            shallow = role.endswith("-root") or role in (
                "binaries", "system-config", "libraries", "loader-libraries",
                "project-parent", "executable-prefix", "base-prefix",
                "system-libraries", "system-frameworks", "local-frameworks",
                "usr-libraries", "windows-system",
            )
            if (
                _profile_platform(getattr(self, "profile", "")) == "darwin"
                and role == "base-prefix"
            ):
                # Framework builds keep launchers, Resources/Python.app,
                # locale data, and the stdlib beneath this prefix.  Traverse
                # it once; previously visited stdlib/package roots are skipped
                # by the shared ``seen`` set.
                shallow = False
            if (
                role == "executable-prefix"
                and self.roots.get("environment-prefix") == root
            ):
                shallow = False
            depth_limit = {
                # System32 language resources live one directory beneath the
                # main runtime files. AppCompat databases use at most two
                # levels on supported Windows hosts.
                "windows-system": 1,
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
                            self.before[_path_key(child)] = metadata(child)
                            # pytest probes directories for virtual-environment
                            # markers. Bind only those literal metadata lookups;
                            # repository internals remain outside input authority.
                            for relative in ("pyvenv.cfg", "conda-meta", "conda-meta/history"):
                                probe = child / relative
                                self.before[_path_key(probe)] = metadata(probe)
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
            if role == "project" and relative.startswith(".git/") and relative not in {
                ".git/pyvenv.cfg", ".git/conda-meta", ".git/conda-meta/history",
            }:
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
        # Identity is what the target can consume: type and permission bits,
        # content for reads and executions, membership for enumerations, the
        # link text for symlinks, and size for metadata-only lookups. Inode,
        # link count, ownership and timestamps are deliberately excluded so an
        # atomic save, checkout, touch or unrelated sibling change with equal
        # content keeps the receipt. Timestamp-dependent behavior is a
        # documented runtime assumption, not a modeled input.
        payload: list[object] = [FINGERPRINT_VERSION, mode]
        if stat.S_ISLNK(mode):
            payload.append(os.readlink(path))
            kind = "symlink"
        elif stat.S_ISDIR(mode):
            kind = "directory"
            # A directory lookup binds its membership whether or not an
            # enumeration was observed: a stat-only lookup may have consumed
            # the modification time that membership changes would move.
            entries = []
            with os.scandir(path) as iterator:
                for entry in iterator:
                    info = entry.stat(follow_symlinks=False)
                    entries.append([entry.name, stat.S_IFMT(info.st_mode)])
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
                payload.append(before[6])
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
            if role == "project" and (relative == ".git" or relative.startswith(".git/")) and operations != {"metadata"}:
                raise InputError("git-internal-input")
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


# One runner executes every shard of a suite against the same host runtime.
# Indexing the runtime roots (stdlib, site-packages, loader libraries, locale
# data: ~16,000 paths) took about half a second per shard and was identical
# every time. The runtime index is therefore built once per process and each
# snapshot copies it, re-indexing only the repository. A runtime file that
# changes after the shared index was taken is still caught: records() compares
# the pre-execution metadata with the current one and refuses the input.
_SHARED_RUNTIME_INDEX: dict[tuple[str, str, str], InputSnapshot] = {}
# The real class, captured at import: a test that replaces the module's
# InputSnapshot to simulate a failing or interrupted snapshot must see its
# replacement called by the observer, not by the shared-index builder.
_RUNTIME_INDEX_CLASS = InputSnapshot


def shared_runtime_index(
    project: Path, artifact_id: str, *, profile: str = PROFILE
) -> InputSnapshot | None:
    """The host-runtime part of an input index, built once per process.

    Returns None when the runtime cannot be indexed here; the snapshot built
    without it then raises the real reason itself.
    """
    key = (str(project), artifact_id, profile)
    base = _SHARED_RUNTIME_INDEX.get(key)
    if base is None:
        try:
            base = object.__new__(_RUNTIME_INDEX_CLASS)
            base.roots = runtime_roots(project, artifact_id, profile=profile)
        except Exception:  # noqa: BLE001 - the snapshot itself reports the real reason
            return None
        base.artifact_id = artifact_id
        base.profile = profile
        base.before = {}
        base.enumerated = set()
        base.project_content = {}
        base.total_bytes = 0
        try:
            base._index(roles=frozenset(base.roots) - {"project"})
        except Exception:  # noqa: BLE001 - see above; interrupts still propagate
            return None
        _SHARED_RUNTIME_INDEX.clear()
        _SHARED_RUNTIME_INDEX[key] = base
    return base


def clear_shared_runtime_index() -> None:
    _SHARED_RUNTIME_INDEX.clear()


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
