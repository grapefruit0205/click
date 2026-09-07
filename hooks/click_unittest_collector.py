"""Private, bounded-data unittest collection worker; never runs test methods.

The parent launches this only through an authorized bootstrap execution. Project
imports are arbitrary code; process supervision is a guardrail, not a sandbox.
This protocol produces candidates, never verification or reuse evidence.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path
import re
import sys
import unittest

if __package__:
    from . import click_collector_runtime
else:
    import click_collector_runtime

MAX_TESTS = 10_000
MAX_RESULT_BYTES = 1024 * 1024
SUPPORTED_VERSION = tuple(sys.version_info[:3])


def collect(spec: dict, root: Path, cwd: Path) -> dict:
    runtime = {"implementation": sys.implementation.name,
               "version": list(sys.version_info[:3]),
               "framework": "unittest",
               "framework_version": ".".join(map(str, sys.version_info[:3]))}
    result = {"version": 1, "runtime": runtime, "tests": [], "reasons": [],
              "modules": [], "module_files": [], "fixtures": [], "id_signature": ""}
    if not click_collector_runtime.cpython_supported(
        runtime["implementation"], runtime["version"]
    ):
        result["reasons"] = ["unsupported-runtime"]
        return result
    reasons: set[str] = set()
    modules: set[str] = set()
    module_files: set[tuple[str, str]] = set()
    fixtures: set[str] = set()
    observed_ids: list[str] = []
    original_loader = unittest.TestLoader
    original_suite = unittest.TestSuite
    original_case = unittest.TestCase
    original_id = unittest.TestCase.id
    original_methods = {name: getattr(original_loader, name) for name in
                        ("discover", "loadTestsFromModule", "loadTestsFromTestCase",
                         "getTestCaseNames", "loadTestsFromName")}

    class RecordingLoader(original_loader):
        def loadTestsFromModule(self, module, *args, **kwargs):
            name = getattr(module, "__name__", "")
            if isinstance(name, str):
                modules.add(name)
                try:
                    source = Path(inspect.getsourcefile(module)).resolve(strict=True)
                    module_files.add((name, source.relative_to(root).as_posix()))
                except (TypeError, ValueError, OSError, RuntimeError):
                    reasons.add("test-outside-project")
            if hasattr(module, "load_tests"):
                reasons.add("load-tests")
            if any(hasattr(module, item) for item in ("setUpModule", "tearDownModule")):
                fixtures.add(name)
            return super().loadTestsFromModule(module, *args, **kwargs)

    # Script mode inserts the Click hooks directory at sys.path[0]. Replace it
    # with the parent '-m unittest' cwd; discovery itself inserts its top-level.
    sys.path[0] = str(cwd)
    sys.argv = [str(Path(unittest.__file__).with_name("__main__.py")),
                "discover", *spec.get("arguments", [])]
    loader = RecordingLoader()
    filters = spec["filters"]
    loader.testNamePatterns = [p if "*" in p else "*" + p + "*" for p in filters] or None
    try:
        suite = loader.discover(str(cwd / spec["start"]), spec["pattern"],
                                str(cwd / spec["top"]))
    except (Exception, SystemExit):
        reasons.add("collection-error")
        suite = original_suite()
    if loader.errors:
        reasons.add("import-error")
    if (unittest.TestLoader is not original_loader or unittest.TestSuite is not original_suite
            or any(getattr(original_loader, n) is not fn for n, fn in original_methods.items())
            or loader.suiteClass is not original_suite):
        reasons.add("custom-loader")
    parsed: dict[Path, ast.AST] = {}
    visited: set[int] = set()

    def walk(value):
        if type(value) is original_suite:
            if id(value) in visited:
                reasons.add("cyclic-suite")
                return
            visited.add(id(value))
            for item in value:
                yield from walk(item)
        elif isinstance(value, original_case):
            yield value
        else:
            reasons.add("custom-loader")

    for test in walk(suite):
        if len(result["tests"]) >= MAX_TESTS:
            reasons.add("test-count-limit")
            break
        cls = type(test)
        if cls.__module__ == "unittest.loader" and cls.__name__ == "_FailedTest":
            reasons.add("import-error")
            continue
        method = getattr(test, "_testMethodName", "")
        canonical_id = original_id(test)
        try:
            observed = test.id()
        except Exception:
            observed = ""
            reasons.add("custom-test-id")
        observed_ids.append(hashlib.sha256(str(observed).encode()).hexdigest())
        if observed != canonical_id or cls.id is not original_id:
            reasons.add("custom-test-id")
        if (not isinstance(method, str) or not method.isidentifier()
                or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", canonical_id)
                or len(canonical_id) > 1024):
            reasons.add("invalid-test-id")
            continue
        module = sys.modules.get(cls.__module__)
        filename = inspect.getsourcefile(module) if module is not None else None
        try:
            path = Path(filename).resolve(strict=True)
            relative = path.relative_to(root).as_posix()
        except (TypeError, ValueError, OSError, RuntimeError):
            reasons.add("test-outside-project")
            continue
        if path.suffix != ".py":
            reasons.add("unsupported-test-source")
            continue
        # Inherited statically declared methods are supported; generated method
        # attachment, generated classes and local classes are not silently accepted.
        owner = next((base for base in cls.__mro__ if method in vars(base)), cls)
        try:
            owner_path = Path(inspect.getsourcefile(owner)).resolve(strict=True)
            owner_path.relative_to(root)
            if owner_path not in parsed:
                parsed[owner_path] = ast.parse(owner_path.read_bytes())
            static_method = any(
                isinstance(node, ast.ClassDef) and node.name == owner.__name__
                and any(isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and member.name == method for member in node.body)
                for node in ast.walk(parsed[owner_path])
            )
            if not static_method or "<locals>" in cls.__qualname__:
                reasons.add("dynamic-test")
        except (OSError, ValueError, TypeError, SyntaxError):
            reasons.add("dynamic-test")
        result["tests"].append({"id": canonical_id, "module": cls.__module__,
                                "class": cls.__qualname__, "method": method,
                                "file": relative})
    ids = [item["id"] for item in result["tests"]]
    if len(ids) != len(set(ids)):
        reasons.add("duplicate-id")
    if not ids and not reasons:
        reasons.add("zero-tests")
    result["reasons"] = sorted(reasons)
    result["modules"] = sorted(modules)
    result["module_files"] = [{"module": name, "file": path}
                              for name, path in sorted(module_files)]
    result["fixtures"] = sorted(fixtures)
    result["id_signature"] = hashlib.sha256(json.dumps(observed_ids).encode()).hexdigest()
    return result


def main() -> int:
    if len(sys.argv) != 5:
        return 2
    spec_path, root, cwd, result_path = map(Path, sys.argv[1:])
    try:
        spec = json.loads(spec_path.read_text())
        result = collect(spec, root.resolve(strict=True), cwd.resolve(strict=True))
        encoded = json.dumps(result, sort_keys=True, ensure_ascii=True).encode()
        if len(encoded) > MAX_RESULT_BYTES:
            result = {"version": 1, "runtime": result["runtime"], "tests": [],
                      "reasons": ["result-size-limit"], "modules": [], "module_files": [],
                      "fixtures": [], "id_signature": ""}
            encoded = json.dumps(result).encode()
        with result_path.open("xb") as stream:
            stream.write(encoded)
        return 0
    except (Exception, SystemExit):
        # No project exception text/tracebacks or output are persisted.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
