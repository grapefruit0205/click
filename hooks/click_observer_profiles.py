"""Versioned native input profiles, independent of command/framework detection.

Unknown runtimes remain ordinary executions. A profile is an eligibility
boundary, never an observation or a reusable result.
"""
from __future__ import annotations

import re
import sys

MIN_NATIVE_CPYTHON = (3, 12, 3)
MAX_NATIVE_CPYTHON = (3, 12, 14)

LEGACY = {
    "linux": "linux-cpython3123-strace68-v1",
    "darwin": "darwin-cpython3123-fsusage-v1",
    "win32": "windows-cpython3123-etw-v1",
}
CPYTHON312 = {
    "linux": "linux-cpython312-strace68-v2",
    "darwin": "darwin-cpython312-fsusage-v2",
    "win32": "windows-cpython312-etw-v2",
}
PLATFORMS = {value: key for mapping in (LEGACY, CPYTHON312) for key, value in mapping.items()}
PROFILES = frozenset(PLATFORMS)
BACKENDS = {profile: {"linux": ("strace", "6.8"), "darwin": ("fs_usage", None),
                      "win32": ("windows-etw", None)}[platform]
            for profile, platform in PLATFORMS.items()}


def supported(profile: str, *, version=None, platform=None, implementation=None) -> bool:
    version = tuple(sys.version_info[:3] if version is None else version)
    platform = sys.platform if platform is None else platform
    implementation = sys.implementation.name if implementation is None else implementation
    if implementation != "cpython" or PLATFORMS.get(profile) != platform:
        return False
    if profile in LEGACY.values():
        return version == (3, 12, 3)
    # Bound the tested ABI family; a new minor or newer patch needs an explicit
    # compatibility update. The actual binary, headers and SOABI bind artifacts.
    return len(version) == 3 and MIN_NATIVE_CPYTHON <= version <= MAX_NATIVE_CPYTHON


def current() -> str:
    profile = LEGACY.get(sys.platform, "") if sys.version_info[:3] == (3, 12, 3) else CPYTHON312.get(sys.platform, "")
    return profile if supported(profile) else ""


# Prepared-record shape is read without importing compilers or runtime probes.
STATE_VERSION = 1
DIGEST = re.compile(r"^[0-9a-f]{64}$")
ARTIFACT_ID = re.compile(r"^click-native-observer-[a-zA-Z0-9_-]{1,64}$")
STATE_FIELDS = frozenset({
    "version", "profile", "artifact_id", "artifact_digest", "source_digest",
    "compiler_digest", "backend",
})
BACKEND_FIELDS = frozenset({"name", "version", "digest"})


def prepared_state_is_valid(value) -> bool:
    backend = value.get("backend") if isinstance(value, dict) else None
    profile = value.get("profile") if isinstance(value, dict) else None
    expected_backend = BACKENDS.get(profile) if isinstance(profile, str) else None
    return bool(
        isinstance(value, dict)
        and set(value) == STATE_FIELDS
        and type(value.get("version")) is int
        and value.get("version") == STATE_VERSION
        and isinstance(profile, str)
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
