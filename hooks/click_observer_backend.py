#!/usr/bin/env python3
"""Cross-platform backend selection contract for Shadow Observer collectors."""

from __future__ import annotations

from dataclasses import dataclass
import os
import platform
from typing import Any, Protocol

if __package__:
    from .click_observer_common import ShadowExecution
else:  # Executed beside the bundled hook modules.
    from click_observer_common import ShadowExecution


CAPABILITY_STATES = frozenset(
    {"available", "degraded", "permission-required", "unavailable"}
)


class ObserverBackend(Protocol):
    """Structural interface implemented by an operating-system collector."""

    def __call__(self, *args: Any, **kwargs: Any) -> ShadowExecution: ...


@dataclass(frozen=True, slots=True)
class BackendCapability:
    """Bounded implementation selection, separate from persisted v1 status."""

    system: str
    backend_name: str | None
    status: str
    reason: str

    def __post_init__(self) -> None:
        if self.status not in CAPABILITY_STATES:
            raise ValueError("unsupported Observer backend capability state")
        if self.status == "available" and not self.backend_name:
            raise ValueError("an available Observer backend must have a name")
        if self.status == "unavailable" and self.backend_name is not None:
            raise ValueError("an unavailable Observer backend cannot claim a name")


def _macos_privileged() -> bool:
    try:
        return bool(getattr(os, "geteuid")() == 0)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def select_backend(
    system_name: str, *, macos_privileged: bool | None = None
) -> BackendCapability:
    """Select an implemented adapter without probing privileged OS facilities.

    Implemented adapters still perform their own trusted-executable and runtime
    probes. macOS selection never elevates privilege: it exposes the native
    collector only when the current process is already privileged.
    """

    system = system_name if isinstance(system_name, str) else ""
    if system == "Linux":
        return BackendCapability(
            system="Linux",
            backend_name="strace",
            status="available",
            reason="runtime-probe-required",
        )
    if system == "Darwin":
        privileged = (
            _macos_privileged()
            if macos_privileged is None
            else bool(macos_privileged)
        )
        if privileged:
            return BackendCapability(
                system="Darwin",
                backend_name="fs_usage",
                status="available",
                reason="runtime-probe-required",
            )
        return BackendCapability(
            system="Darwin",
            backend_name="fs_usage",
            status="permission-required",
            reason="root-privilege-required",
        )
    if system == "Windows":
        return BackendCapability(
            system="Windows",
            backend_name="windows-etw",
            status="available",
            reason="runtime-probe-required",
        )
    return BackendCapability(
        system=system[:64],
        backend_name=None,
        status="unavailable",
        reason="unsupported-operating-system",
    )


def support_report(
    system_name: str | None = None, *, macos_privileged: bool | None = None
) -> dict[str, Any]:
    """Report implementation tiers without promoting Shadow data to authority."""
    try:
        system = platform.system() if system_name is None else system_name
    except Exception:
        system = ""
    capability = select_backend(system, macos_privileged=macos_privileged)
    common = "implemented" if system in {"Linux", "Darwin", "Windows"} else "unsupported"
    if system in {"Linux", "Darwin", "Windows"}:
        authoritative = {
            "status": "profile-implemented",
            "reason": (
                "separate-runtime-and-backend-probe-required"
                if system == "Linux"
                else "native-runtime-backend-and-real-host-validation-required"
            ),
        }
    else:
        authoritative = {
            "status": "unsupported",
            "reason": "unsupported-operating-system",
        }
    return {
        "version": 1,
        "system": system[:64],
        "base_reuse": {"status": common, "reason": "common-state-engine"},
        "static_configuration": {
            "status": common,
            "reason": "repository-policy-engine",
        },
        "shadow_observer": {
            "status": capability.status,
            "backend": capability.backend_name,
            "reason": capability.reason,
        },
        "authoritative_observer": authoritative,
    }
