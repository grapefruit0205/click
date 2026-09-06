"""Early Windows bootstrap for the authoritative CPython companion.

The runtime copies this file to its owner-controlled artifact directory as
``sitecustomize.py``. It restores the caller's PYTHONPATH before any project
module runs; authority still requires the native companion and ETW capture.
"""
from __future__ import annotations

import os
import sys


def _normalized(value: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(value)))


def _executable_pth(paths: list[str]) -> bool:
    for entry in paths:
        if not entry or not os.path.isdir(entry):
            continue
        try:
            names = os.listdir(entry)
        except OSError:
            continue
        for name in names:
            if not name.lower().endswith(".pth"):
                continue
            try:
                with open(os.path.join(entry, name), "rb") as stream:
                    raw = stream.read(256 * 1024 + 1)
                if len(raw) > 256 * 1024:
                    return True
                text = raw.decode("utf-8-sig", errors="strict")
            except (OSError, UnicodeError):
                return True
            for line in text.splitlines():
                candidate = line.lstrip()
                if candidate.startswith(("import ", "import\t")):
                    return True
    return False


def _chain_original_sitecustomize(spec: object | None) -> None:
    if spec is None:
        return
    current = sys.modules.pop(__name__, None)
    try:
        __import__(__name__)
    except BaseException:
        if current is not None and __name__ not in sys.modules:
            sys.modules[__name__] = current
        raise


def _activate() -> None:
    expected = os.environ.pop("CLICK_NATIVE_OBSERVER_BOOTSTRAP", "")
    original = os.environ.pop("CLICK_NATIVE_OBSERVER_ORIGINAL_PYTHONPATH", "")
    present = os.environ.pop("CLICK_NATIVE_OBSERVER_PYTHONPATH_PRESENT", "") == "1"
    here = os.path.dirname(os.path.abspath(__file__))
    if not expected or _normalized(expected) != _normalized(here):
        raise RuntimeError("invalid authoritative observer bootstrap")
    companion = __import__("_click_observer_companion")
    sys.path[:] = [
        entry
        for entry in sys.path
        if _normalized(entry or os.getcwd()) != _normalized(here)
    ]
    if present:
        os.environ["PYTHONPATH"] = original
    else:
        os.environ.pop("PYTHONPATH", None)
    from importlib.machinery import PathFinder

    site_spec = PathFinder.find_spec("sitecustomize", sys.path)
    user_spec = PathFinder.find_spec("usercustomize", sys.path)
    if site_spec is not None or user_spec is not None or _executable_pth(sys.path):
        companion.mark_startup_unsafe()
    _chain_original_sitecustomize(site_spec)


_activate()
