#!/usr/bin/env python3
"""Observe one command from inside its Node processes, where no system call tracer runs.

On Windows nothing like strace watches a process tree without administrator
rights. Instead ``click_input_observer.cjs`` is preloaded into every Node
process and worker thread the command starts (``NODE_OPTIONS=--require``): it
logs what each of them read, listed, looked up and did not find, and wrote.
The same ``Observation`` as the system call tracer's comes out, so records and
decisions do not know which one made it.

Code that is not Node is not seen from the inside. Every process the command
starts is accounted for (a Windows job object names them all): one that is not
an observed Node process must be a launcher whose own reads follow from its
command line (``cmd.exe`` running a script, a shell, a console host), or the
observation is volatile and its command is never skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import IO, Any, Callable, Iterable, Mapping, Sequence

if __package__:
    from . import click_import_bootstrap
else:  # Executed directly from the bundled hooks directory.
    import click_import_bootstrap

(click_syscall_trace,) = click_import_bootstrap.load_siblings(__package__, "click_syscall_trace")

PathState = click_syscall_trace.PathState
Observation = click_syscall_trace.Observation

PRELOAD = Path(__file__).resolve().with_name("click_input_observer.cjs")
LOG_VARIABLE = "CLICK_INPUT_LOG"
LINGER_SECONDS = 5.0
MAX_UNRESOLVED_EXAMPLES = 5

_EVENTS = {"I": "input", "M": "missing", "P": "produced", "T": "touched", "D": "deleted"}

# Native addons whose own file reads are module resolution and configuration:
# where it looks for package.json and tsconfig.json, which it reads. Their
# lookups are not seen, so the configuration files around everything the
# command read, and the installed dependency state, stand in for them. Any
# other native addon may read anything: its observation is volatile.
RESOLVER_ADDONS = (
    "@rolldown/binding-", "rolldown", "@oxc-resolver/binding-", "oxc-resolver",
    "@oxc-transform/binding-", "oxc-transform", "@oxc-parser/binding-", "oxc-parser",
    "@oxc-minify/binding-", "oxc-minify", "@rollup/rollup-", "@swc/core-", "@swc/core",
    "@swc/html-", "lightningcss-", "lightningcss", "@esbuild/",
)
CONFIG_NAMES = ("package.json", "tsconfig.json", "jsconfig.json", ".swcrc")
# What an install leaves behind: any change to installed dependencies moves one.
INSTALL_STATE = (
    "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb",
    "node_modules/.package-lock.json", "node_modules/.modules.yaml", "node_modules/.yarn-state.yml",
    "node_modules/.yarn-integrity",
)
MAX_EXTENDS_DEPTH = 16


@dataclass
class NodeProcess:
    pid: int
    ppid: int
    thread: int
    executable: str
    argv: list[str]
    cwd: str
    hooks: bool


@dataclass
class RuntimeLogs:
    paths: dict[str, PathState]
    processes: list[NodeProcess]
    unaccounted: list[str] = field(default_factory=list)
    unreadable: int = 0
    # Services outside the command it reached: `network:<host>`, `socket:<path>`.
    services: list[str] = field(default_factory=list)


def _loopback(host: str) -> bool:
    name = host.strip("[]").lower()
    return name in {"", "localhost", "::1", "0.0.0.0", "::"} or name.startswith("127.") or name.endswith(".localhost")


def node_options(existing: str, preload: Path = PRELOAD) -> str:
    """NODE_OPTIONS with the observer preloaded, quoted the way Node parses it."""
    # Node reads backslashes inside quotes as escapes; forward slashes name
    # the same file on Windows.
    name = preload.as_posix().replace('"', '\\"')
    option = f'--require "{name}"'
    return f"{existing} {option}".strip() if existing else option


def _events(directory: Path) -> tuple[list[tuple[int, str, str, str]], list[NodeProcess], int]:
    events: list[tuple[int, str, str, str]] = []
    processes: list[NodeProcess] = []
    unreadable = 0
    for log in sorted(directory.glob("*.log")):
        try:
            text = log.read_text(encoding="utf-8", errors="surrogateescape")
        except OSError:
            unreadable += 1
            continue
        lines = text.split("\n")
        if not lines or not lines[0].startswith("{"):
            unreadable += 1
            continue
        try:
            header = json.loads(lines[0])
            processes.append(NodeProcess(
                pid=int(header["pid"]), ppid=int(header.get("ppid") or 0), thread=int(header.get("thread") or 0),
                executable=str(header.get("exec") or ""), argv=[str(item) for item in header.get("argv") or []],
                cwd=str(header.get("cwd") or ""), hooks=bool(header.get("hooks")),
            ))
        except (ValueError, KeyError, TypeError):
            unreadable += 1
            continue
        for line in lines[1:]:
            if not line:
                continue  # the last line ends with a newline, or a killed process cut it
            parts = line.split("\t", 3)
            if len(parts) != 4:
                unreadable += 1
                continue
            clock, event, operation, encoded = parts
            try:
                events.append((int(clock), event, operation, json.loads(encoded)))
            except ValueError:
                unreadable += 1
    events.sort(key=lambda item: item[0])  # hrtime is one monotonic clock for the whole machine
    return events, processes, unreadable


def reduce_logs(directory: Path) -> RuntimeLogs:
    """Fold the logs of every observed process, in time order, into per-path states."""
    events, processes, unreadable = _events(directory)
    paths: dict[str, PathState] = {}
    unaccounted: list[str] = []
    bound: set[str] = set()
    connected: list[str] = []
    services: list[str] = []
    for process in processes:
        if process.thread == 0 and process.executable:
            click_syscall_trace.mark_path(paths, os.path.normpath(process.executable), "input", "execute")
    for _clock, event, operation, target in events:
        if not isinstance(target, str) or not target:
            unreadable += 1
            continue
        path = os.path.normpath(target)
        if event in _EVENTS:
            click_syscall_trace.mark_path(paths, path, _EVENTS[event], operation)
        elif event == "O":
            if path in paths:
                # Seen before it was opened for update: an input it changed.
                click_syscall_trace.mark_path(paths, path, "input", "read").modified_after_input = True
            else:
                click_syscall_trace.mark_path(paths, path, "opened")
        elif event == "U":
            click_syscall_trace.mark_path(paths, path, "input", operation or "read").modified_after_input = True
        elif event == "X":
            unaccounted.append(f"{operation}:{target}")
        elif event == "B":
            bound.add(target)
            if not target.startswith("\\\\"):
                click_syscall_trace.mark_path(paths, path, "produced")
        elif event == "S":
            connected.append(target)
        elif event == "N":
            host, _, port = target.rpartition(":")
            if not _loopback(host) or port == "53":
                services.append(f"network:{host}")
        else:
            unreadable += 1
    for target in connected:
        state = paths.get(os.path.normpath(target))
        if target not in bound and not (state is not None and state.first == "produced"):
            services.append(f"socket:{target}")
    for process in processes:
        if not process.hooks:
            unaccounted.append(f"module-hooks:{process.executable}")
    return RuntimeLogs(paths=paths, processes=processes, unaccounted=unaccounted, unreadable=unreadable,
                       services=list(dict.fromkeys(services)))


def observing_environment(environment: Mapping[str, str] | None, directory: Path) -> dict[str, str]:
    base = dict(os.environ if environment is None else environment)
    base["NODE_OPTIONS"] = node_options(base.get("NODE_OPTIONS", ""))
    base[LOG_VARIABLE] = str(directory)
    return base


def _package_of(path: str) -> str:
    """The package a file under node_modules belongs to (``@scope/name`` or ``name``)."""
    parts = path.replace("\\", "/").split("/")
    if "node_modules" not in parts:
        return ""
    index = len(parts) - 1 - parts[::-1].index("node_modules")
    rest = parts[index + 1:]
    if not rest:
        return ""
    return "/".join(rest[:2]) if rest[0].startswith("@") and len(rest) > 1 else rest[0]


def native_addons(paths: Mapping[str, PathState]) -> list[str]:
    """Packages of the native addons (``.node`` files) the command loaded."""
    return sorted({_package_of(path) or os.path.basename(path)
                   for path, state in paths.items()
                   if path.endswith(".node") and state.first == "input" and "read" in state.operations})


def resolver_addon(package: str) -> bool:
    return any(package == name or (name.endswith(("-", "/")) and package.startswith(name))
               for name in RESOLVER_ADDONS)


def _strip_json_comments(text: str) -> str:
    """tsconfig.json is JSON with comments and trailing commas."""
    out: list[str] = []
    index = 0
    quoted = False
    while index < len(text):
        character = text[index]
        if quoted:
            out.append(character)
            if character == "\\" and index + 1 < len(text):
                out.append(text[index + 1])
                index += 1
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
            out.append(character)
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end < 0 else end
            continue
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
            continue
        else:
            out.append(character)
        index += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _extends(config: str) -> list[str]:
    try:
        with open(config, encoding="utf-8") as handle:
            value = json.loads(_strip_json_comments(handle.read()))
    except (OSError, ValueError):
        return []
    named = value.get("extends") if isinstance(value, dict) else None
    items = named if isinstance(named, list) else [named] if isinstance(named, str) else []
    found: list[str] = []
    base = os.path.dirname(config)
    for item in items:
        if not isinstance(item, str) or not item:
            continue
        if item.startswith((".", "/")) or os.path.isabs(item):
            candidate = os.path.normpath(os.path.join(base, item))
            found.append(candidate if candidate.endswith(".json") or os.path.isfile(candidate) else candidate + ".json")
            continue
        # A package: node_modules/<name>[/<file>], looked up from here upward.
        directory = base
        while True:
            target = os.path.join(directory, "node_modules", *item.split("/"))
            if os.path.isdir(target):
                found.append(os.path.join(target, "tsconfig.json"))
                break
            if os.path.isfile(target) or os.path.isfile(target + ".json"):
                found.append(target if os.path.isfile(target) else target + ".json")
                break
            parent = os.path.dirname(directory)
            if parent == directory:
                break
            directory = parent
    return found


def add_resolution_context(paths: dict[str, PathState], repo: str) -> None:
    """Record, as inputs, what a native resolver would have looked at around what was read.

    For every checkout file the command read (outside node_modules), each
    directory from its own up to the checkout root: its package.json,
    tsconfig.json, jsconfig.json and .swcrc, found or missing, and the configs
    a tsconfig extends. Plus the lockfiles and install markers of the checkout.
    """
    root = os.path.normpath(repo)
    prefix = root.rstrip(os.sep) + os.sep
    directories: set[str] = set()
    for path, state in list(paths.items()):
        if state.first != "input" or not path.startswith(prefix):
            continue
        if "node_modules" in path[len(prefix):].replace("\\", "/").split("/"):
            continue
        directory = os.path.dirname(path) if "enumerate" not in state.operations else path
        while directory.startswith(prefix) or directory == root:
            if directory in directories:
                break
            directories.add(directory)
            if directory == root:
                break
            directory = os.path.dirname(directory)
    configs: list[str] = []
    for directory in sorted(directories):
        for name in CONFIG_NAMES:
            configs.append(os.path.join(directory, name))
    for name in INSTALL_STATE:
        configs.append(os.path.join(root, *name.split("/")))
    followed: set[str] = set()
    queue = list(configs)
    depth = 0
    while queue and depth <= MAX_EXTENDS_DEPTH:
        following: list[str] = []
        for config in queue:
            if config in followed:
                continue
            followed.add(config)
            if config in paths and paths[config].first != "input":
                continue  # the command itself made or removed it
            exists = os.path.isfile(config)
            click_syscall_trace.mark_path(paths, config, "input" if exists else "missing", "read")
            extended = config not in configs  # reached through another config's "extends"
            if exists and (extended or os.path.basename(config).startswith(("tsconfig", "jsconfig"))):
                following.extend(_extends(config))
        queue = following
        depth += 1


# -- launching and accounting ---------------------------------------------------

# Programs a command runs around its Node processes whose own reads follow
# from their command lines: shells running a script, console hosts, and the
# text tools of Git for Windows that npm's shell shims and pipes use.
SHELLS = frozenset({"bash.exe", "sh.exe", "dash.exe"})
# Tools that read no file at all, whatever their arguments.
QUIET_TOOLS = frozenset({
    "dirname", "basename", "uname", "cygpath", "echo", "printf", "expr", "true", "false", "test", "[",
    "sleep", "env", "date", "tty", "id", "whoami", "hostname", "nproc", "seq", "yes",
})
# Tools that read the files their operands name (stdin otherwise).
READING_TOOLS = frozenset({
    "sed", "head", "tail", "grep", "egrep", "fgrep", "cat", "cut", "sort", "uniq", "wc", "tr", "tee",
    "awk", "gawk", "column", "fold", "nl", "paste", "rev", "tac", "less", "more",
})
NODE_PROGRAMS = frozenset({"node", "npm", "npx", "pnpm", "pnpx", "yarn", "yarnpkg", "corepack"})


def _stem(path: str) -> str:
    name = ntpath.basename(path).lower()  # either separator
    for suffix in (".exe", ".cmd", ".bat", ".ps1", ".js", ".mjs", ".cjs"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def windows_bash(environment: Mapping[str, str] | None = None) -> str | None:
    """Git for Windows' bash, never the WSL launcher in System32."""
    source = os.environ if environment is None else environment
    system_root = os.path.normcase(source.get("SystemRoot", source.get("SYSTEMROOT", r"C:\Windows")))
    found = shutil.which("bash", path=source.get("PATH"))
    if found and not os.path.normcase(found).startswith(system_root):
        return found
    for base in (source.get("ProgramFiles"), source.get("ProgramW6432"), source.get("LOCALAPPDATA")):
        if not base:
            continue
        for relative in (("Git", "bin", "bash.exe"), ("Programs", "Git", "bin", "bash.exe")):
            candidate = os.path.join(base, *relative)
            if os.path.isfile(candidate):
                return candidate
    return None


def launchable(argv: Sequence[str], environment: Mapping[str, str] | None = None) -> list[str]:
    """argv with its program resolved the way a shell would, for CreateProcess on Windows."""
    if os.name != "nt" or not argv:
        return list(argv)
    source = os.environ if environment is None else environment
    program = argv[0]
    if program == "bash":
        found = windows_bash(source)
    elif os.path.dirname(program):
        found = shutil.which(program, path=os.path.dirname(program) or None) or program
    else:
        found = shutil.which(program, path=source.get("PATH"))
    return [found or program, *argv[1:]]


def node_command(argv: Sequence[str], environment: Mapping[str, str] | None = None) -> bool:
    """Whether a check's program is Node or a Node launcher, so its inputs can be observed here."""
    words = list(argv)
    if len(words) == 5 and words[:4] == ["bash", "-o", "pipefail", "-c"]:
        try:
            import shlex
            words = shlex.split(words[4], posix=True)
        except ValueError:
            return False
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words = words[1:]
    if not words:
        return False
    source = os.environ if environment is None else environment
    program = words[0]
    if _stem(program) in NODE_PROGRAMS:
        return True
    found = shutil.which(program, path=source.get("PATH")) if not os.path.dirname(program) else program
    if not found:
        return False
    parts = [part.lower() for part in os.path.normpath(found).split(os.sep)]
    if len(parts) >= 3 and parts[-3:-1] == ["node_modules", ".bin"]:
        return True
    return os.path.isfile(os.path.join(os.path.dirname(found), "node.exe" if os.name == "nt" else "node"))


def _cmd_script(arguments: Sequence[str], cwd: str, path_value: str) -> tuple[str | None, str]:
    """What ``cmd.exe /c <command>`` runs: the resolved script or program, and its first word."""
    lowered = [item.lower() for item in arguments]
    try:
        index = next(position for position, item in enumerate(lowered) if item in ("/c", "/k"))
    except StopIteration:
        return None, ""
    command = " ".join(arguments[index + 1:]).strip()
    if command.startswith('"') and command.count('"') >= 2:
        first = command[1:].split('"', 1)[0]
    else:
        first = command.split()[0] if command.split() else ""
    first = first.strip('"')
    if not first:
        return None, ""
    if os.path.dirname(first):
        candidate = first if os.path.isabs(first) else os.path.join(cwd, first)
        return shutil.which(candidate) or (candidate if os.path.isfile(candidate) else None), first
    return shutil.which(first, path=os.pathsep.join([cwd, path_value])), first


def classify(members: Sequence[Any], observed: set[int], *, cwd: str,
             path_value: str) -> tuple[list[str], list[str], bool]:
    """Which processes of the job the observation does not cover, and what launchers read.

    Returns the volatility reasons, the files launchers read (scripts, the
    operands of text tools) and whether an esbuild service ran (a resolver
    whose lookups the resolution context stands in for).
    """
    reasons: list[str] = []
    reads: list[str] = []
    esbuild = False
    system_root = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows"))
    for member in members:
        if member.pid in observed:
            continue
        if not member.image:
            reasons.append(f"unidentified-process:{member.pid}")
            continue
        name = ntpath.basename(member.image).lower()
        stem = _stem(member.image)
        arguments = split_arguments(member.command_line or "")
        operands = [item for item in arguments[1:] if not item.startswith("-")]
        if name == "conhost.exe":
            continue
        if name == "cmd.exe":
            script, first = _cmd_script(arguments, cwd, path_value)
            if script is None:
                reasons.append(f"unobserved-program:cmd.exe {first}".rstrip())
                continue
            parts = [part.lower() for part in os.path.normpath(script).split(os.sep)]
            shim = parts[-3:-1] == ["node_modules", ".bin"] or os.path.isfile(
                os.path.join(os.path.dirname(script), "node.exe"))
            if script.lower().endswith((".cmd", ".bat")) and not shim:
                reasons.append(f"unobserved-program:cmd.exe {first}")
                continue
            reads.append(script)
            continue
        if name in SHELLS and not os.path.normcase(member.image).startswith(system_root):
            reads.extend(os.path.join(cwd, item) for item in operands if os.path.isfile(os.path.join(cwd, item)))
            continue
        if stem in QUIET_TOOLS:
            continue
        if stem in READING_TOOLS:
            directories = [item for item in operands if os.path.isdir(os.path.join(cwd, item))]
            if directories:
                reasons.append(f"unobserved-program:{stem} {directories[0]}")
                continue
            reads.extend(os.path.join(cwd, item) for item in operands if os.path.isfile(os.path.join(cwd, item)))
            continue
        if stem == "esbuild" and any(item.startswith("--service") for item in arguments):
            esbuild = True
            continue
        if stem == "node":
            reasons.append(f"unobserved-node:{member.pid}")
            continue
        reasons.append(f"unobserved-program:{name}")
    return reasons, reads, esbuild


def split_arguments(command_line: str) -> list[str]:
    if os.name == "nt":
        return _windows_job().split_command_line(command_line)
    import shlex
    try:
        return shlex.split(command_line)
    except ValueError:
        return command_line.split()


def _windows_job() -> Any:
    (module,) = click_import_bootstrap.load_siblings(__package__, "click_windows_job")
    return module


def observe(
    argv: Sequence[str],
    *,
    cwd: Path,
    repo: str,
    environment: Mapping[str, str] | None = None,
    stdout: int | IO[Any] | None = None,
    stderr: int | IO[Any] | None = None,
    timeout_seconds: float | None = None,
    on_start: Callable[[int], None] | None = None,
    idle: bool = False,
) -> Observation:
    """Run ``argv`` once with the Node observer preloaded and reduce what it touched.

    On Windows the command runs in a job object that names every process it
    starts; elsewhere (tests) only its Node processes are known.
    """
    directory = Path(tempfile.mkdtemp(prefix="click-node-"))
    environment_used = observing_environment(environment, directory)
    started = time.monotonic()
    started_at = time.time()
    reasons: list[str] = []
    job = _windows_job().Job(idle=idle) if os.name == "nt" else None
    try:
        command = launchable(argv, environment_used)
        if job is not None:
            process = job.start(command, cwd=str(cwd), environment=environment_used, stdout=stdout, stderr=stderr)
        else:
            process = subprocess.Popen(command, cwd=str(cwd), env=environment_used, stdout=stdout, stderr=stderr,
                                       start_new_session=True)
        if on_start is not None:
            on_start(process.pid)
        try:
            returncode = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            reasons.append("timed-out")
            _end(process, job)
            returncode = process.wait()
        except BaseException:
            _end(process, job)
            process.wait()
            raise
        if not _drained(process, job):
            reasons.append("processes-outlived-command")
            _end(process, job)
        logs = reduce_logs(directory)
        members = job.processes() if job is not None else []
        if job is not None and job.total() > len(members):
            reasons.append(f"unidentified-process:{job.total() - len(members)}")
    finally:
        if job is not None:
            job.close()
        shutil.rmtree(directory, ignore_errors=True)
    finished_at = time.time()
    paths = _long_names(logs.paths) if os.name == "nt" else logs.paths
    observed = {process.pid for process in logs.processes}
    esbuild = False
    if job is not None:
        found, reads, esbuild = classify(members, observed, cwd=str(cwd),
                                         path_value=environment_used.get("PATH", ""))
        reasons.extend(found)
        for path in reads:
            click_syscall_trace.mark_path(paths, os.path.normpath(path), "input", "read")
    if not logs.processes:
        reasons.append("no-node-process")
    reasons.extend(logs.services)
    addons = native_addons(paths)
    reasons.extend(f"native-addon:{name}" for name in addons if not resolver_addon(name))
    if esbuild or any(resolver_addon(name) for name in addons):
        add_resolution_context(paths, repo)
    reasons.extend(f"unobserved-call:{item}"[:200] for item in logs.unaccounted[:MAX_UNRESOLVED_EXAMPLES])
    click_syscall_trace.settle_opened(paths, started_at=started_at, finished_at=finished_at)
    return Observation(
        exit_code=(returncode or -9) if "timed-out" in reasons else returncode,
        paths=paths,
        volatile_reasons=reasons,
        unresolved_lines=logs.unreadable,
        process_count=len(members) if job is not None else len({p.pid for p in logs.processes}),
        duration_seconds=time.monotonic() - started,
    )


def _long_names(paths: dict[str, PathState]) -> dict[str, PathState]:
    """Paths spelled with 8.3 short names (``RUNNER~1``) under their long names, as the checkout is named."""
    resolved: dict[str, str] = {}
    result: dict[str, PathState] = {}
    for path, state in paths.items():
        if "~" in path:
            directory, name = os.path.split(path)
            if directory not in resolved:
                resolved[directory] = os.path.realpath(directory)
            path = os.path.join(resolved[directory], name)
        existing = result.get(path)
        if existing is None:
            result[path] = state
        else:  # the same file under both names: keep the earlier state, add what the other saw
            existing.operations |= state.operations
            existing.modified_after_input = existing.modified_after_input or state.modified_after_input
    return result


def _end(process: subprocess.Popen, job: Any) -> None:
    if job is not None:
        job.terminate()
        return
    import signal
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass


def _drained(process: subprocess.Popen, job: Any) -> bool:
    """Whether every process the command started has ended (after a short grace period)."""
    if job is not None:
        return job.empty.wait(LINGER_SECONDS) or job.active() == 0
    deadline = time.monotonic() + LINGER_SECONDS
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except OSError:
            return True
        time.sleep(0.05)
    return False
