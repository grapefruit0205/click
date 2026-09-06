#!/usr/bin/env python3
"""Private pytest collection worker for the bounded automatic-sharding adapter."""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import sys
from typing import Any

if __package__:
    from . import click_collector_runtime
else:
    import click_collector_runtime


MAX_TESTS = 10_000
MAX_RESULT_BYTES = 1024 * 1024
DEFAULT_PATTERNS = ("test_*.py", "*_test.py")


class RecordingPlugin:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.tests: list[dict[str, str]] = []
        self.modules: set[str] = set()
        self.module_files: set[tuple[str, str]] = set()
        self.fixtures: set[str] = set()
        self.reasons: set[str] = set()

    def pytest_collection_finish(self, session: Any) -> None:
        try:
            patterns = tuple(session.config.getini("python_files"))
        except Exception:
            patterns = ()
        if patterns != DEFAULT_PATTERNS:
            self.reasons.add("unsupported-pytest-pattern")
        for item in list(getattr(session, "items", ())):
            if len(self.tests) >= MAX_TESTS:
                self.reasons.add("test-count-limit")
                break
            nodeid = getattr(item, "nodeid", "")
            path_value = getattr(item, "path", None)
            if path_value is None:
                path_value = getattr(item, "fspath", None)
            try:
                path = Path(str(path_value)).resolve(strict=True)
                relative = path.relative_to(self.root).as_posix()
            except (OSError, RuntimeError, TypeError, ValueError):
                self.reasons.add("test-outside-project")
                continue
            _prefix, separator, selection = str(nodeid).partition("::")
            if (
                not isinstance(nodeid, str)
                or not nodeid
                or len(nodeid) > 4096
                or any(ord(character) < 32 or ord(character) == 127 for character in nodeid)
                or path.suffix != ".py"
                or not separator
                or not selection
            ):
                self.reasons.add("invalid-test-id")
                continue
            canonical_id = relative + "::" + selection
            module_name = str(getattr(getattr(item, "module", None), "__name__", ""))
            if not module_name:
                module_name = relative[:-3].replace("/", ".")
            class_name = str(getattr(getattr(item, "cls", None), "__qualname__", ""))
            method_name = str(
                getattr(item, "originalname", "") or getattr(item, "name", "")
            )
            self.modules.add(module_name)
            self.module_files.add((module_name, relative))
            self.tests.append(
                {
                    "id": canonical_id,
                    "module": module_name,
                    "class": class_name,
                    "method": method_name,
                    "file": relative,
                }
            )
            fixture_info = getattr(item, "_fixtureinfo", None)
            definitions = getattr(fixture_info, "name2fixturedefs", {})
            if not isinstance(definitions, dict):
                continue
            for name, candidates in definitions.items():
                if not candidates:
                    continue
                definition = candidates[-1]
                function = getattr(definition, "func", None)
                try:
                    source = Path(inspect.getsourcefile(function)).resolve(strict=True)
                    source_relative = source.relative_to(self.root).as_posix()
                except (OSError, RuntimeError, TypeError, ValueError):
                    continue
                scope = str(getattr(definition, "scope", "function"))
                material = f"{source_relative}:{scope}:{name}".encode()
                self.fixtures.add("fixture_" + hashlib.sha256(material).hexdigest()[:32])


def collect(arguments: list[str], root: Path, cwd: Path) -> dict[str, Any]:
    runtime = {
        "implementation": sys.implementation.name,
        "version": list(sys.version_info[:3]),
        "framework": "pytest",
        "framework_version": "unavailable",
    }
    result: dict[str, Any] = {
        "version": 1,
        "runtime": runtime,
        "tests": [],
        "reasons": [],
        "modules": [],
        "module_files": [],
        "fixtures": [],
        "id_signature": "",
    }
    if not click_collector_runtime.cpython_supported(
        runtime["implementation"], runtime["version"]
    ):
        result["reasons"] = ["unsupported-runtime"]
        return result
    try:
        import pytest
    except (ImportError, RuntimeError):
        result["reasons"] = ["pytest-unavailable"]
        return result
    version = str(getattr(pytest, "__version__", ""))
    if not version or len(version) > 64 or not all(
        character.isalnum() or character in ". _+-" for character in version
    ):
        result["reasons"] = ["unsupported-pytest-version"]
        return result
    runtime["framework_version"] = version.replace(" ", "_")
    plugin = RecordingPlugin(root)
    original_path = list(sys.path)
    try:
        # Match `python -m pytest`: the selected working directory is the
        # first import root even though this bounded worker lives elsewhere.
        sys.path.insert(0, str(cwd))
        exit_code = int(
            pytest.main(
                [*arguments, "-p", "no:cacheprovider", "--collect-only"],
                plugins=[plugin],
            )
        )
    except (Exception, SystemExit):
        exit_code = 2
    finally:
        sys.path[:] = original_path
    if exit_code not in {0, 5}:
        plugin.reasons.add("collection-error")
    if not plugin.tests and not plugin.reasons:
        plugin.reasons.add("zero-tests")
    ids = [item["id"] for item in plugin.tests]
    if len(ids) != len(set(ids)):
        plugin.reasons.add("duplicate-id")
    result.update(
        tests=plugin.tests,
        reasons=sorted(plugin.reasons),
        modules=sorted(plugin.modules),
        module_files=[
            {"module": module, "file": path}
            for module, path in sorted(plugin.module_files)
        ],
        fixtures=sorted(plugin.fixtures),
        id_signature=hashlib.sha256(
            json.dumps(ids, ensure_ascii=True).encode()
        ).hexdigest(),
    )
    return result


def main() -> int:
    if len(sys.argv) != 5:
        return 2
    request_path, root_path, cwd_path, result_path = map(Path, sys.argv[1:])
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        arguments = request.get("arguments")
        if not isinstance(arguments, list) or any(
            not isinstance(item, str) for item in arguments
        ):
            return 2
        result = collect(
            arguments,
            root_path.resolve(strict=True),
            cwd_path.resolve(strict=True),
        )
        encoded = json.dumps(result, sort_keys=True, ensure_ascii=True).encode()
        if len(encoded) > MAX_RESULT_BYTES:
            result.update(
                tests=[],
                reasons=["result-size-limit"],
                modules=[],
                module_files=[],
                fixtures=[],
                id_signature="",
            )
            encoded = json.dumps(result, sort_keys=True).encode()
        with result_path.open("xb") as stream:
            stream.write(encoded)
        return 0
    except (Exception, SystemExit):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
