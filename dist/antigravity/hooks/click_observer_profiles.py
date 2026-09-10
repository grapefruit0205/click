"""Versioned native input profiles, independent of command/framework detection.

Unknown runtimes remain ordinary executions. A profile is an eligibility
boundary, never an observation or a reusable result.
"""
from __future__ import annotations

import sys

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
    return version[:2] == (3, 12) and len(version) == 3 and 3 <= version[2] <= 14


def current() -> str:
    profile = LEGACY.get(sys.platform, "") if sys.version_info[:3] == (3, 12, 3) else CPYTHON312.get(sys.platform, "")
    return profile if supported(profile) else ""
