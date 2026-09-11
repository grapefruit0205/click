#!/usr/bin/env python3
"""Load Click runtime siblings in package and direct-script contexts.

Hook entrypoints are imported as ``hooks.<module>`` by tests and integrations,
but installed launchers also execute their files directly.  Keep that import
choice in one stdlib-only leaf so entrypoints do not duplicate every sibling
import in two branches.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType


def load_siblings(package: str | None, *names: str) -> tuple[ModuleType, ...]:
    """Return named sibling modules for a package import or direct execution."""

    package_name = package if isinstance(package, str) and package else ""
    modules: list[ModuleType] = []
    for name in names:
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError("Click sibling module names must be identifiers.")
        module = (
            import_module(f".{name}", package_name)
            if package_name
            else import_module(name)
        )
        modules.append(module)
    return tuple(modules)
