"""Bounded, non-executing Python dependency candidates, never reuse authority."""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
import sys

CONFIG_NAMES = frozenset({
    "pyproject.toml", "setup.cfg", "setup.py", "tox.ini", "unittest.cfg",
    "pytest.ini", "conftest.py",
    "requirements.txt", "requirements-dev.txt", "uv.lock", "poetry.lock",
    "Pipfile", "Pipfile.lock", ".python-version",
})
UNKNOWN_IMPORTS = {
    "time": "time-random", "datetime": "time-random", "random": "time-random",
    "secrets": "time-random", "subprocess": "subprocess", "socket": "external-service",
    "http": "external-service", "urllib": "external-service", "requests": "external-service",
    "sqlite3": "database", "psycopg": "database", "pymysql": "database",
    "multiprocessing": "untracked-ipc", "mmap": "untracked-ipc",
    "ctypes": "native-extension", "importlib": "dynamic-import",
}
MAX_REACHABLE_FILES = 512
MAX_SOURCE_BYTES = 2 * 1024 * 1024


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted(node.value)
        return parent + "." + node.attr if parent else ""
    return ""


def analyze_candidates(root: Path, cwd: Path, spec: dict, inventory: list,
                       project_paths: list[str]) -> dict:
    known = {p for p in project_paths if not p.startswith(".git/")}
    modules: dict[str, set[str]] = {}
    search_roots = list(dict.fromkeys(((cwd / spec["top"]).resolve(), cwd, root)))
    for relative in sorted(known):
        path = root / relative
        if path.suffix != ".py" or "__pycache__" in path.parts:
            continue
        for search in search_roots:
            try:
                parts = list(path.relative_to(search).with_suffix("").parts)
            except ValueError:
                continue
            if parts[-1] == "__init__":
                parts.pop()
            if parts and all(part.isidentifier() for part in parts):
                modules.setdefault(".".join(parts), set()).add(relative)
    config = sorted(p for p in known if Path(p).name in CONFIG_NAMES)
    cache: dict[str, dict] = {}
    unknown: set[tuple[str, int, str]] = set()
    runtime_imports: set[str] = set()
    split_risks: set[tuple[str, str]] = set()

    def note(relative: str, node: ast.AST, reason: str) -> None:
        unknown.add((relative, getattr(node, "lineno", 0), reason))

    def resolve_name(name: str) -> set[str]:
        found = set()
        segments = name.split(".")
        for index in range(1, len(segments) + 1):
            prefix = ".".join(segments[:index])
            values = modules.get(prefix, set())
            found.update(values)
        return found

    def inspect_file(relative: str) -> dict:
        if relative in cache:
            return cache[relative]
        output = {"paths": {relative}, "edges": set()}
        cache[relative] = output
        path = root / relative
        try:
            if path.stat().st_size > MAX_SOURCE_BYTES:
                raise ValueError("size")
            tree = ast.parse(path.read_bytes())
        except (OSError, ValueError, SyntaxError):
            unknown.add((relative, 0, "unparsed-source"))
            return output
        aliases: dict[str, str] = {}
        file_modules = sorted(name for name, paths in modules.items() if relative in paths)
        current_module = max(file_modules, key=lambda name: len(name.split(".")), default="")
        package = current_module if path.name == "__init__.py" else current_module.rpartition(".")[0]

        def add_import(name: str, node: ast.AST, *, optional_attribute: bool = False) -> None:
            if not name:
                note(relative, node, "unresolved-import")
                return
            base = name.split(".")[0]
            if base in UNKNOWN_IMPORTS:
                note(relative, node, UNKNOWN_IMPORTS[base])
            targets = resolve_name(name)
            if targets:
                output["paths"].update(targets)
                output["edges"].update(targets)
                if len(modules.get(name, ())) > 1:
                    note(relative, node, "ambiguous-import")
            elif (
                base == "pytest"
                or base in sys.stdlib_module_names
                or base in sys.builtin_module_names
            ):
                runtime_imports.add(name)
            elif not optional_attribute:
                note(relative, node, "external-module")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    aliases[alias.asname or alias.name.split(".")[0]] = (
                        alias.name if alias.asname else alias.name.split(".")[0])
                    add_import(alias.name, node)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = package.split(".") if package else []
                    if node.level > len(parts):
                        note(relative, node, "unresolved-import")
                        base = ""
                    else:
                        base = ".".join(parts[:len(parts) - node.level + 1])
                        if node.module:
                            base = ".".join(filter(None, (base, node.module)))
                else:
                    base = node.module or ""
                add_import(base, node)
                for alias in node.names:
                    if alias.name == "*":
                        note(relative, node, "wildcard-import")
                    else:
                        full = ".".join(filter(None, (base, alias.name)))
                        aliases[alias.asname or alias.name] = full
                        add_import(full, node, optional_attribute=True)

        def qualify(node: ast.AST) -> str:
            name = _dotted(node)
            first, dot, tail = name.partition(".")
            return aliases.get(first, first) + (dot + tail if dot else "")

        def literal_path(node: ast.AST) -> Path | None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return cwd / node.value
            if isinstance(node, ast.Name) and node.id == "__file__":
                return path
            if isinstance(node, ast.Attribute) and node.attr == "parent":
                parent = literal_path(node.value)
                return parent.parent if parent is not None else None
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                left = literal_path(node.left)
                if left is not None and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
                    return left / node.right.value
            if isinstance(node, ast.Call) and qualify(node.func) in ("pathlib.Path", "Path") and len(node.args) == 1:
                return literal_path(node.args[0])
            return None

        for node in ast.walk(tree):
            targets = (node.targets if isinstance(node, ast.Assign) else
                       [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else [])
            for target in targets:
                if isinstance(target, (ast.Attribute, ast.Subscript)):
                    base_target = target.value if isinstance(target, ast.Subscript) else target
                    if _dotted(base_target).split(".")[0] in aliases:
                        split_risks.add((relative, "shared-mutable-state"))
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id == "open":
                note(relative, node, "unresolved-file-access")
            if isinstance(node, ast.Attribute) and qualify(node) in (
                    "sys.argv", "sys.path", "sys.modules", "sys.flags", "os.environ"):
                note(relative, node, "process-environment-introspection")
                split_risks.add((relative, "process-environment-introspection"))
            if isinstance(node, ast.Global):
                split_risks.add((relative, "module-global-state"))
            if not isinstance(node, ast.Call):
                continue
            name = qualify(node.func)
            if not isinstance(node.func, (ast.Name, ast.Attribute)):
                note(relative, node, "dynamic-call")
            base = name.split(".")[0]
            if base in UNKNOWN_IMPORTS:
                note(relative, node, UNKNOWN_IMPORTS[base])
            if name in ("__import__", "importlib.import_module", "eval", "exec", "compile"):
                note(relative, node, "dynamic-import" if "import" in name else "dynamic-code")
                split_risks.add((relative, "dynamic-code"))
            if name in ("os.system", "os.popen", "os.spawnv", "os.execv"):
                note(relative, node, "subprocess")
            if name in ("os.pipe", "os.mkfifo", "os.fork", "os.kill"):
                note(relative, node, "untracked-ipc")
            if name in ("os.urandom", "os.getrandom"):
                note(relative, node, "time-random")
            if name in ("os.getenv", "os.environ.get"):
                note(relative, node, "environment-access")
            file_target = None
            file_access = name in ("open", "builtins.open", "io.open")
            if file_access and node.args:
                file_target = literal_path(node.args[0])
                modes = [node.args[1]] if len(node.args) > 1 else []
                modes.extend(item.value for item in node.keywords if item.arg == "mode")
                if any(not isinstance(mode, ast.Constant) or not isinstance(mode.value, str)
                       or any(flag in mode.value for flag in "wax+") for mode in modes):
                    split_risks.add((relative, "file-write-or-unknown-mode"))
            if isinstance(node.func, ast.Attribute) and node.func.attr in ("write_text", "write_bytes", "unlink", "rename"):
                split_risks.add((relative, "file-mutation"))
            if not file_access and isinstance(node.func, ast.Attribute) and node.func.attr in (
                    "read_text", "read_bytes", "open", "exists", "is_file", "iterdir", "glob"):
                file_access = True
                file_target = literal_path(node.func.value)
            if file_access:
                if file_target is None:
                    note(relative, node, "unresolved-file-access")
                else:
                    try:
                        candidate = file_target.resolve().relative_to(root).as_posix()
                        output["paths"].add(candidate)
                    except (ValueError, OSError, RuntimeError):
                        note(relative, node, "external-file-access")
            # Cross-module state mutation / import-time operations need review.
            if isinstance(node.func, ast.Attribute) and node.func.attr in (
                    "append", "extend", "update", "add", "remove", "pop", "clear"):
                if _dotted(node.func.value).split(".")[0] in aliases:
                    split_risks.add((relative, "shared-mutable-state"))
        for node in tree.body:
            value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
            if isinstance(value, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
                split_risks.add((relative, "mutable-module-value"))
            if isinstance(value, ast.Call):
                split_risks.add((relative, "import-time-initializer"))
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                split_risks.add((relative, "import-time-call"))
        return output

    by_module: dict[str, dict] = {}
    for item in inventory:
        by_module.setdefault(item["module"], {"files": set(), "paths": set(config)})
        by_module[item["module"]]["files"].add(item["file"])
    for name, group in by_module.items():
        pending = list(group["files"])
        visited: set[str] = set()
        while pending:
            relative = pending.pop()
            if relative in visited:
                continue
            if len(visited) >= MAX_REACHABLE_FILES:
                unknown.add((relative, 0, "dependency-analysis-limit"))
                break
            visited.add(relative)
            info = inspect_file(relative)
            group["paths"].update(info["paths"])
            for target in sorted(info["edges"]):
                if target not in visited:
                    pending.append(target)
        group["paths"] = sorted(group["paths"])
        group["files"] = sorted(group["files"])
    counts = Counter(p for group in by_module.values() for p in group["paths"])
    return {
        "candidate_only": True, "authority": False,
        "by_module": dict(sorted(by_module.items())),
        "edges": [{"from": path, "to": target}
                  for path, info in sorted(cache.items()) for target in sorted(info["edges"])],
        "common_paths": sorted(p for p, count in counts.items() if count > 1),
        "config_paths": config,
        "runtime_imports": sorted(runtime_imports),
        "unknown": [{"file": p, "line": line, "reason": reason}
                    for p, line, reason in sorted(unknown)],
        "split_risks": [{"file": p, "reason": reason} for p, reason in sorted(split_risks)],
    }
