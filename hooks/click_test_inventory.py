"""Authorized bootstrap analysis of real unittest and pytest inventories.

All results are candidate-only. This module never changes Click authority,
policy, verification receipts or the user's Git index.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

if __package__:
    from . import click_import_bootstrap
else:
    import click_import_bootstrap

(click_dependency_candidates, click_collector_runtime) = click_import_bootstrap.load_siblings(
    __package__, "click_dependency_candidates", "click_collector_runtime"
)

UNITTEST_ADAPTER = "cpython-unittest-v1"
PYTEST_ADAPTER = "cpython-pytest-v1"
ADAPTER = UNITTEST_ADAPTER
SUPPORTED_VERSION = tuple(sys.version_info[:3])
SUPPORTED_CPYTHON_MIN = click_collector_runtime.MIN_CPYTHON
SUPPORTED_CPYTHON_MAX = click_collector_runtime.MAX_CPYTHON


class AnalysisError(ValueError):
    """A stable, content-free bootstrap failure reason."""


@dataclass(frozen=True)
class Limits:
    files: int = 50_000
    snapshot_bytes: int = 64 * 1024 * 1024
    output_bytes: int = 256 * 1024
    result_bytes: int = 1024 * 1024
    timeout: float = 30.0
    modules: int = 128


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode()).hexdigest()


def inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def trusted_executable(
    name: str, root: Path, *, preserve_launcher: bool = False
) -> Path:
    if not isinstance(name, str) or not name:
        raise AnalysisError("interpreter-unavailable")
    if os.path.isabs(name):
        selected = Path(name)
    else:
        if "/" in name or "\\" in name:
            raise AnalysisError("repository-executable")
        entries = []
        for entry in os.get_exec_path():
            path = Path(entry)
            if path.is_absolute() and not inside(root, path.resolve()):
                entries.append(entry)
        found = shutil.which(name, path=os.pathsep.join(entries))
        if not found:
            raise AnalysisError("interpreter-unavailable")
        selected = Path(found)
    launch_path = selected.absolute()
    resolved = launch_path.resolve(strict=True)
    if inside(root, launch_path) or inside(root, resolved):
        raise AnalysisError("repository-executable")
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise AnalysisError("interpreter-unavailable")
    # CPython uses an adjacent pyvenv.cfg to activate a virtual environment.
    # Collection therefore preserves its selected launcher, while backend and
    # compiler discovery retain their canonical resolved-path identity.
    return launch_path if preserve_launcher else resolved


def git_read(root: Path, args: list[str], *, optional: bool = False) -> bytes:
    git = trusted_executable("git", root)
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    result = subprocess.run(
        [str(git), "--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false", *args],
        cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=10, check=False,
    )
    if len(result.stdout) > 4 * 1024 * 1024:
        raise AnalysisError("git-output-limit")
    if result.returncode and not optional:
        raise AnalysisError("git-unavailable")
    return result.stdout if result.returncode == 0 else b""


def project_root(project: Path) -> Path:
    root = project.resolve(strict=True)
    # Do not borrow a containing project's Git identity for a nested non-Git app.
    if not root.is_dir() or not (root / ".git").exists():
        raise AnalysisError("non-git-project")
    observed = git_read(root, ["rev-parse", "--show-toplevel"]).decode().strip()
    if Path(observed).resolve() != root:
        raise AnalysisError("project-boundary")
    return root


def _relative(cwd: Path, root: Path, value: str) -> str:
    if not value or "\x00" in value:
        raise AnalysisError("invalid-discovery-path")
    if os.name == "nt":
        value = value.replace("\\", os.sep)
    elif "\\" in value:
        raise AnalysisError("invalid-discovery-path")
    path = (cwd / value).resolve(strict=True)
    if not path.is_dir() or not inside(root, path):
        raise AnalysisError("project-boundary")
    return os.path.relpath(path, cwd).replace(os.sep, "/")


def _pytest_target(cwd: Path, root: Path, value: str) -> str:
    if not value or "\x00" in value:
        raise AnalysisError("invalid-discovery-path")
    if os.name == "nt":
        value = value.replace("\\", os.sep)
    elif "\\" in value:
        raise AnalysisError("invalid-discovery-path")
    path = (cwd / value).resolve(strict=True)
    if not inside(root, path):
        raise AnalysisError("project-boundary")
    if not path.is_dir() and (not path.is_file() or path.suffix != ".py"):
        raise AnalysisError("unsupported-pytest-target")
    return os.path.relpath(path, cwd).replace(os.sep, "/")


def _python_module_command(argv: list[str]) -> tuple[str, int, list[str]]:
    executable = Path(argv[0]).name.lower()
    if re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", executable):
        index = 1
    elif executable in {"py", "py.exe"}:
        index = 1
        if index < len(argv) and re.fullmatch(r"-3(?:\.\d+)?", argv[index]):
            index += 1
    else:
        raise AnalysisError("unsupported-command")
    if index + 1 >= len(argv) or argv[index] != "-m":
        raise AnalysisError("unsupported-command")
    return argv[index + 1], index + 2, list(argv[: index + 2])


def _parse_unittest_command(
    argv: list[str], root: Path, cwd: Path, index: int, prefix: list[str]
) -> dict:
    if index >= len(argv) or argv[index] != "discover":
        raise AnalysisError("unsupported-command")
    if (not isinstance(argv, list) or not argv or len(argv) > 128
            or any(not isinstance(a, str) or not a or len(a) > 4096
                   or any(ord(c) < 32 for c in a) for a in argv)):
        raise AnalysisError("unsupported-command")
    values: dict[str, str] = {}
    filters: list[str] = []
    verbosity = ""
    positionals: list[str] = []
    names = {"-s": "start", "--start-directory": "start",
             "-t": "top", "--top-level-directory": "top",
             "-p": "pattern", "--pattern": "pattern", "-k": "filter"}
    tokens = argv[index + 1 :]
    cursor = 0
    while cursor < len(tokens):
        token = tokens[cursor]
        cursor += 1
        option, equal, attached = token.partition("=")
        if option in names:
            if equal:
                value = attached
            elif cursor < len(tokens):
                value = tokens[cursor]
                cursor += 1
            else:
                raise AnalysisError("missing-option-value")
            if not value or value.startswith("-"):
                raise AnalysisError("invalid-option-value")
            key = names[option]
            if key == "filter":
                filters.append(value)
            elif key in values:
                raise AnalysisError("ambiguous-option")
            else:
                values[key] = value
        elif token in ("-q", "--quiet", "-v", "--verbose"):
            selected = "-q" if token in ("-q", "--quiet") else "-v"
            if verbosity and verbosity != selected:
                raise AnalysisError("ambiguous-option")
            verbosity = selected
        elif token.startswith("-"):
            raise AnalysisError("unsupported-option")
        else:
            positionals.append(token)
    if len(positionals) > 3:
        raise AnalysisError("unsupported-command")
    for key, value in zip(("start", "pattern", "top"), positionals):
        if key in values:
            raise AnalysisError("ambiguous-option")
        values[key] = value
    start = _relative(cwd, root, values.get("start", "."))
    top = _relative(cwd, root, values.get("top", start))
    if not inside((cwd / top).resolve(), (cwd / start).resolve()):
        raise AnalysisError("invalid-top-level")
    pattern = values.get("pattern", "test*.py")
    if "/" in pattern or "\\" in pattern or len(pattern) > 256:
        raise AnalysisError("unsupported-pattern")
    return {
        "adapter": UNITTEST_ADAPTER,
        "runner_prefix": [*prefix, "discover"],
        "start": start,
        "top": top,
        "pattern": pattern,
        "patterns": [pattern],
        "filters": filters,
        "verbosity": verbosity,
        "child_options": [],
        "arguments": list(tokens),
    }


def _parse_pytest_command(
    argv: list[str], root: Path, cwd: Path, index: int, prefix: list[str]
) -> dict:
    tokens = argv[index:]
    flags = {
        "-q", "--quiet", "-v", "--verbose", "--strict-markers",
        "--strict-config", "--disable-warnings",
    }
    value_options = {"-k", "-m", "--maxfail", "--tb"}
    options: list[str] = []
    filters: list[str] = []
    positionals: list[str] = []
    cursor = 0
    while cursor < len(tokens):
        token = tokens[cursor]
        if token in flags:
            options.append(token)
            cursor += 1
            continue
        if token in value_options:
            if cursor + 1 >= len(tokens) or not tokens[cursor + 1]:
                raise AnalysisError("missing-option-value")
            value = tokens[cursor + 1]
            if value.startswith("-"):
                raise AnalysisError("invalid-option-value")
            options.extend((token, value))
            if token in {"-k", "-m"}:
                filters.append(value)
            cursor += 2
            continue
        if token.startswith(("--maxfail=", "--tb=")):
            if not token.partition("=")[2]:
                raise AnalysisError("invalid-option-value")
            options.append(token)
            cursor += 1
            continue
        if token.startswith("-") or "::" in token:
            raise AnalysisError("unsupported-option")
        positionals.append(token)
        cursor += 1
    if len(positionals) > 1:
        raise AnalysisError("unsupported-command")
    start = _pytest_target(cwd, root, positionals[0] if positionals else ".")
    return {
        "adapter": PYTEST_ADAPTER,
        "runner_prefix": prefix,
        "start": start,
        "top": ".",
        "pattern": "test_*.py",
        "patterns": ["test_*.py", "*_test.py"],
        "filters": filters,
        "verbosity": "-v" if any(item in {"-v", "--verbose"} for item in options) else "-q" if any(item in {"-q", "--quiet"} for item in options) else "",
        "child_options": options,
        "arguments": list(tokens),
    }


def parse_command(argv: list[str], root: Path, cwd: Path) -> dict:
    if (
        not isinstance(argv, list)
        or not argv
        or len(argv) > 128
        or any(
            not isinstance(argument, str)
            or not argument
            or len(argument) > 4096
            or any(ord(character) < 32 for character in argument)
            for argument in argv
        )
    ):
        raise AnalysisError("unsupported-command")
    module, index, prefix = _python_module_command(argv)
    if module == "unittest":
        return _parse_unittest_command(argv, root, cwd, index, prefix)
    if module == "pytest":
        return _parse_pytest_command(argv, root, cwd, index, prefix)
    raise AnalysisError("unsupported-command")


def workspace_snapshot(root: Path, limits: Limits) -> dict[str, str]:
    result: dict[str, str] = {}
    total = 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        base = Path(directory)
        dirs.sort()
        names.sort()
        if base == root and ".git" in dirs:
            dirs.remove(".git")
        for name in list(dirs):
            path = base / name
            if path.is_symlink():
                dirs.remove(name)
                names.append(name)
            elif name == ".git":
                raise AnalysisError("nested-git-project")
        for name in sorted(names):
            path = base / name
            if path == root / ".git":
                continue
            metadata = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(metadata.st_mode):
                target = path.resolve(strict=True)
                if not inside(root, target):
                    raise AnalysisError("external-symlink")
                content = ("symlink:" + os.readlink(path)).encode()
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_size + total > limits.snapshot_bytes:
                    raise AnalysisError("snapshot-byte-limit")
                with path.open("rb") as stream:
                    content = stream.read(limits.snapshot_bytes - total + 1)
                if len(content) + total > limits.snapshot_bytes:
                    raise AnalysisError("snapshot-byte-limit")
            else:
                raise AnalysisError("unsupported-file-type")
            total += len(content)
            result[relative] = digest([stat.S_IMODE(metadata.st_mode),
                                      hashlib.sha256(content).hexdigest()])
            if len(result) > limits.files:
                raise AnalysisError("snapshot-file-limit")
    # Protect index and HEAD independently while excluding object-store churn.
    for name in ("index", "HEAD", "config", "config.worktree", "packed-refs"):
        path = Path(git_read(root, ["rev-parse", "--git-path", name]).decode().strip())
        path = path if path.is_absolute() else root / path
        content = path.read_bytes() if path.exists() else b""
        result[".git/" + name] = hashlib.sha256(content).hexdigest()
    result[".git/commit"] = hashlib.sha256(
        git_read(root, ["rev-parse", "--verify", "HEAD"], optional=True)
    ).hexdigest()
    return result


def run_bounded_command(
    argv: list[str], cwd: Path, environment: dict, limits: Limits
) -> None:
    """Run one collector/setup command through the shared bounded supervisor."""
    try:
        click_collector_runtime.supervise(
            argv,
            cwd,
            environment,
            timeout=limits.timeout,
            output_bytes=limits.output_bytes,
        )
    except click_collector_runtime.CollectorRuntimeError as exc:
        raise AnalysisError(str(exc)) from exc


# Compatibility for releases that imported the prior private helper.
_supervised = run_bounded_command


def collect_once(root: Path, cwd: Path, spec: dict, executable: Path,
                 limits: Limits) -> dict:
    if click_collector_runtime.capability().status != "implemented":
        raise AnalysisError("unsupported-platform")
    with tempfile.TemporaryDirectory(prefix="click-collector-") as temp:
        directory = Path(temp)
        if inside(root, directory.resolve()):
            raise AnalysisError("temporary-directory-inside-project")
        request, response = directory / "request.json", directory / "result.json"
        request.write_text(json.dumps(spec))
        blocked_environment = {
            "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONBREAKPOINT",
            "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "LD_PRELOAD", "LD_LIBRARY_PATH",
            "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
        }
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() not in blocked_environment
        }
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONHASHSEED"] = "0"
        if spec.get("adapter") == PYTEST_ADAPTER:
            environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        worker_name = (
            "click_pytest_collector.py"
            if spec.get("adapter") == PYTEST_ADAPTER
            else "click_unittest_collector.py"
        )
        worker = Path(__file__).with_name(worker_name)
        run_bounded_command(
            [str(executable), "-B", str(worker), str(request), str(root),
             str(cwd), str(response)],
            cwd,
            environment,
            limits,
        )
        try:
            metadata = response.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limits.result_bytes:
                raise AnalysisError("invalid-collector-result")
            descriptor = os.open(
                response, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or (metadata.st_dev, metadata.st_ino)
                    != (opened.st_dev, opened.st_ino)
                ):
                    raise AnalysisError("invalid-collector-result")
                encoded = stream.read(limits.result_bytes + 1)
            if len(encoded) > limits.result_bytes:
                raise AnalysisError("result-size-limit")
            result = json.loads(encoded)
        except (OSError, ValueError) as exc:
            raise AnalysisError("invalid-collector-result") from exc
        expected = {"version", "runtime", "tests", "reasons", "modules", "module_files", "fixtures",
                    "id_signature"}
        if (not isinstance(result, dict) or set(result) != expected or result["version"] != 1
                or not isinstance(result["tests"], list) or len(result["tests"]) > 10_000
                or not isinstance(result["reasons"], list)
                or any(not isinstance(x, str) or not re.fullmatch(r"[a-z-]+", x)
                       for x in result["reasons"])):
            raise AnalysisError("invalid-collector-result")
        runtime = result["runtime"]
        if (not isinstance(runtime, dict) or set(runtime) != {
                    "implementation", "version", "framework", "framework_version"
                }
                or not isinstance(runtime["implementation"], str)
                or not re.fullmatch(r"[a-z]+", runtime["implementation"])
                or not isinstance(runtime["version"], list) or len(runtime["version"]) != 3
                or any(type(v) is not int or v < 0 for v in runtime["version"])
                or runtime["framework"] not in {"unittest", "pytest"}
                or not isinstance(runtime["framework_version"], str)
                or not re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", runtime["framework_version"])):
            raise AnalysisError("invalid-collector-result")
        if (
            (spec.get("adapter") == UNITTEST_ADAPTER and runtime["framework"] != "unittest")
            or (spec.get("adapter") == PYTEST_ADAPTER and runtime["framework"] != "pytest")
        ):
            raise AnalysisError("invalid-collector-result")
        if not click_collector_runtime.cpython_supported(
            runtime["implementation"], runtime["version"]
        ):
            raise AnalysisError("unsupported-runtime")
        for key in ("modules", "fixtures"):
            values = result[key]
            if (not isinstance(values, list) or len(values) > 50_000
                    or any(not isinstance(v, str) or len(v) > 1024
                           or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", v)
                           for v in values)):
                raise AnalysisError("invalid-collector-result")
        if (not isinstance(result["id_signature"], str)
                or result["id_signature"] and not re.fullmatch(r"[0-9a-f]{64}", result["id_signature"])):
            raise AnalysisError("invalid-collector-result")
        if not isinstance(result["module_files"], list) or len(result["module_files"]) > 50_000:
            raise AnalysisError("invalid-collector-result")
        for module in result["module_files"]:
            if (not isinstance(module, dict) or set(module) != {"module", "file"}
                    or module["module"] not in result["modules"]
                    or not isinstance(module["file"], str)
                    or not inside(root, (root / module["file"]).resolve())
                    or (root / module["file"]).resolve().relative_to(root).as_posix() != module["file"]):
                raise AnalysisError("invalid-collector-result")
        for test in result["tests"]:
            if (not isinstance(test, dict)
                    or set(test) != {"id", "module", "class", "method", "file"}
                    or any(not isinstance(v, str) or len(v) > 4096 for v in test.values())
                    or not inside(root, (root / test["file"]).resolve())
                    or (root / test["file"]).resolve().relative_to(root).as_posix() != test["file"]
                    or (
                        spec.get("adapter") == UNITTEST_ADAPTER
                        and test["id"] != ".".join(
                            (test["module"], test["class"], test["method"])
                        )
                    )
                    or (
                        spec.get("adapter") == PYTEST_ADAPTER
                        and not test["id"].startswith(test["file"] + "::")
                    )):
                raise AnalysisError("invalid-collector-result")
        ids = [test["id"] for test in result["tests"]]
        if len(ids) != len(set(ids)):
            result["reasons"] = sorted(set(result["reasons"]) | {"duplicate-id"})
        if not ids and not result["reasons"]:
            result["reasons"] = ["zero-tests"]
        return result


def analyze(project: Path, argv: list[str] | None, *, cwd: Path | None = None,
            limits: Limits | None = None,
            _baseline_snapshot: dict[str, str] | None = None) -> dict:
    limits = limits or Limits()
    result = {"version": 1, "kind": "test-analysis", "adapter": ADAPTER,
              "status": "blocked", "reasons": [], "candidate_only": True,
              "authority": False, "reuse_ready": False, "inventory": [],
              "dependencies": {}, "runtime": None}
    if not argv:
        result.update(status="selection-required", reasons=["explicit-command-required"],
                      command_candidates=[
                          ["python3", "-m", "unittest", "discover"],
                          ["python3", "-m", "pytest"],
                      ],
                      candidate_reason="A supported test command requires explicit selection.")
        return result
    before = None
    root = None
    try:
        root = project_root(Path(project))
        execution_cwd = (cwd or root).resolve(strict=True)
        if not inside(root, execution_cwd):
            raise AnalysisError("project-boundary")
        spec = parse_command(argv, root, execution_cwd)
        result["adapter"] = spec["adapter"]
        executable = trusted_executable(argv[0], root, preserve_launcher=True)
        executable_digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        if _baseline_snapshot is None:
            before = workspace_snapshot(root, limits)
        else:
            if (
                not isinstance(_baseline_snapshot, dict)
                or len(_baseline_snapshot) > limits.files + 6
                or any(
                    not isinstance(path, str)
                    or not isinstance(value, str)
                    or not value
                    for path, value in _baseline_snapshot.items()
                )
            ):
                raise AnalysisError("invalid-baseline-snapshot")
            before = dict(_baseline_snapshot)
        result.update(project_identity=digest([str(root), str(execution_cwd)]),
                      workspace_digest=digest(before),
                      command={"argv": list(argv), "cwd": execution_cwd.relative_to(root).as_posix(),
                               **spec})
        first = collect_once(root, execution_cwd, spec, executable, limits)
        result.update(inventory=first["tests"], runtime={
            **first["runtime"], "executable_digest": executable_digest},
            collection={"modules": first["modules"], "module_files": first["module_files"],
                        "fixtures": first["fixtures"],
                        "loader": (
                            "pytest.collect-only"
                            if spec["adapter"] == PYTEST_ADAPTER
                            else "unittest.TestLoader"
                        ), "conditions": spec},
            reasons=first["reasons"])
        if workspace_snapshot(root, limits) != before:
            raise AnalysisError("collection-mutated-project")
        second = collect_once(root, execution_cwd, spec, executable, limits)
        if first != second:
            result["reasons"] = sorted(set(result["reasons"]) | {"unstable-test-id"})
        if workspace_snapshot(root, limits) != before:
            raise AnalysisError("collection-mutated-project")
        if hashlib.sha256(executable.read_bytes()).hexdigest() != executable_digest:
            raise AnalysisError("interpreter-changed")
        modules = {item["module"] for item in result["inventory"]}
        if len(modules) > limits.modules:
            raise AnalysisError("module-count-limit")
        result["inventory_digest"] = digest(result["inventory"])
        result["dependencies"] = click_dependency_candidates.analyze_candidates(
            root, execution_cwd, spec,
            [*result["inventory"], *first["module_files"]], list(before)
        )
        if workspace_snapshot(root, limits) != before:
            raise AnalysisError("analysis-mutated-project")
        if not result["reasons"]:
            result["status"] = "analysis-complete"
        elif set(result["reasons"]) <= {
                "load-tests", "custom-loader", "custom-test-id", "dynamic-test",
                "unsupported-runtime", "unsupported-test-source",
                "pytest-unavailable", "unsupported-pytest-pattern",
                "unsupported-pytest-version"}:
            result["status"] = "unsupported"
    except (AnalysisError, OSError, ValueError, subprocess.SubprocessError, RuntimeError) as exc:
        reason = str(exc) if isinstance(exc, AnalysisError) else "analysis-unavailable"
        result["reasons"] = sorted(set(result["reasons"]) | {reason})
        if reason.startswith(("unsupported-", "non-git", "project-boundary",
                              "repository-executable", "external-symlink")):
            result["status"] = "unsupported"
        # A failed/killed collector may still have changed files; report both.
        if before is not None and root is not None:
            try:
                if workspace_snapshot(root, limits) != before:
                    result["reasons"] = sorted(set(result["reasons"]) |
                                               {"collection-mutated-project"})
                    result["status"] = "blocked"
            except Exception:
                result["reasons"] = sorted(set(result["reasons"]) |
                                           {"post-collection-snapshot-unavailable"})
    return result
