#!/usr/bin/env python3
"""Skip a CI command when nothing it was observed reading has changed.

``click-ci run -- <command>`` wraps any test or check command, in any
language, without configuration:

* **record** (default-branch pushes): the command runs under strace. When it
  passes, the files it read, the directories it listed, the programs it
  executed and the paths it looked up and did not find are stored with their
  content digests, together with a fingerprint of the environment variables
  that change how commands run.
* **select** (pull requests): every recorded input is digested again in this
  checkout. When all of them, and the environment fingerprint, are unchanged
  the command is skipped: an execution that read the same bytes takes the same
  path. Anything else runs the command, unobserved.
* **shadow**: decide as select would, run anyway, and report whether a skip
  would have been wrong.

Only a passing, fully explained observation can be reused. A command that
changed a file it had read, connected to a non-loopback address, left
processes running, used ptrace, or produced a trace line the reducer could
not place is recorded as volatile and always runs. A command that fails under
observation runs again unobserved and that result counts, so tracing never
turns a passing build red. Default-branch pushes always run, so a wrong skip
on a pull request surfaces at the latest when it merges.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import datetime as _datetime
import fnmatch
import glob
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, Mapping, Sequence

if __package__:
    from . import click_ci_trace
else:  # executed as a script from the action directory
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import click_ci_trace  # type: ignore[no-redef]


RECORD_VERSION = 1
CONFIG_PATH = Path(".click") / "ci.json"
MAX_REPORTED_CHANGES = 5
# Digests are file reads and sha256, both of which release the GIL. Inputs are
# checked in batches so a changed source file ends the check early.
DIGEST_WORKERS = min(8, os.cpu_count() or 1)
CHECK_BATCH = 256
# Repository files change most often; the toolchain under /usr least.
CHECK_ORDER = {"repo": 0, "home": 1}
# `run: pytest {paths}` with `paths: <globs>` splits one command into groups of
# paths, each recorded and decided on its own. Up to AUTO_GROUPS paths get one
# group each; beyond that, AUTO_GROUPS stable hash buckets keep the number of
# recorded start-ups bounded.
PATHS_TOKEN = "{paths}"
AUTO_GROUPS = 32
PATH_EXCLUDED_PARTS = frozenset({"node_modules", "__pycache__"})

# Tool caches whose contents change how fast a command runs, not what it
# checks. `.click/ci.json` can add patterns; it cannot remove these.
DEFAULT_IGNORES = (
    "__pycache__", "__pycache__/*", "*/__pycache__", "*/__pycache__/*", "*.pyc",
    ".pytest_cache", ".pytest_cache/*", ".mypy_cache", ".mypy_cache/*",
    ".ruff_cache", ".ruff_cache/*", ".coverage", ".coverage.*",
    "node_modules/.cache", "node_modules/.cache/*", ".nyc_output", ".nyc_output/*",
)
IGNORED_NAMES = frozenset({"__pycache__"})

# The same allow-list Click's agent-side receipts bind (hooks/
# click_verification_bindings.py): variables that change how a check runs.
# CI bookkeeping (run ids, refs, tokens) stays out so every run is comparable.
ENVIRONMENT_KEYS = frozenset({
    "PATH", "SHELL", "TZ", "LANG", "LANGUAGE", "CI", "SOURCE_DATE_EPOCH",
    "NO_COLOR", "FORCE_COLOR", "PY_COLORS", "VIRTUAL_ENV", "PYENV_VERSION",
    "PIPENV_ACTIVE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "DOCKER_HOST", "KUBECONFIG", "JAVA_HOME", "ANDROID_HOME", "ImageOS",
})
ENVIRONMENT_PREFIXES = (
    "PYTHON", "LC_", "XDG_", "CONDA_", "PIP_", "UV_", "POETRY_", "PDM_",
    "HATCH_", "TOX_", "PYTEST_", "COVERAGE_", "NODE_", "NPM_CONFIG_", "YARN_",
    "PNPM_", "BUN_", "JEST_", "VITEST", "DENO_", "GO", "CGO_", "CARGO_",
    "RUST", "JAVA_", "JDK_", "GRADLE_", "MAVEN_", "DOTNET_", "NUGET_", "LD_",
    "DYLD_", "CLICOLOR", "GIT_",
)
ENVIRONMENT_EXCLUDED = frozenset({
    "LC_CTYPE", "GIT_EDITOR", "GIT_PAGER", "GIT_TERMINAL_PROMPT", "GIT_ASKPASS",
    "GIT_PREFIX", "GIT_EXEC_PATH", "PYTHONUNBUFFERED", "CLICK_CI_MODE",
    "CLICK_CI_STORE", "CLICK_CI_REPORT",
})


# -- places -----------------------------------------------------------------

@dataclass(frozen=True)
class Places:
    """Where paths live, so records survive a different checkout location."""

    repo: str
    home: str
    volatile_roots: tuple[str, ...]
    ignores: tuple[str, ...]

    @classmethod
    def discover(cls, cwd: Path, *, store: Path | None = None) -> "Places":
        repo = _git(["rev-parse", "--show-toplevel"], cwd) or str(cwd)
        repo = os.path.realpath(repo)
        home = os.path.realpath(os.path.expanduser("~"))
        roots = {"/proc", "/sys", "/dev", "/run", "/var/run", "/tmp", "/var/tmp",
                 os.path.realpath(tempfile.gettempdir())}
        for name in ("RUNNER_TEMP", "TMPDIR", "TEMP", "TMP"):
            value = os.environ.get(name)
            if value:
                roots.add(os.path.realpath(value))
        if store is not None:
            roots.add(os.path.realpath(str(store)))
        ignores = list(DEFAULT_IGNORES)
        config = Path(repo) / CONFIG_PATH
        try:
            extra = json.loads(config.read_text(encoding="utf-8")).get("ignore", [])
        except (OSError, ValueError, AttributeError):
            extra = []
        ignores.extend(item for item in extra if isinstance(item, str) and item)
        return cls(repo=repo, home=home,
                   volatile_roots=tuple(sorted(r for r in roots if r and r != "/")),
                   ignores=tuple(ignores))

    def key(self, absolute: str) -> str | None:
        """The portable name of a path, or None when it is not an input."""
        path = os.path.normpath(absolute)
        # The repository may itself sit under a temporary root (local runs).
        if _within(path, self.repo):
            relative = os.path.relpath(path, self.repo)
            relative = "" if relative == "." else relative.replace(os.sep, "/")
            if relative and self.ignored(relative):
                return None
            return "repo:" + relative
        for root in self.volatile_roots:
            if _within(path, root):
                return None
        if _within(path, self.home):
            return "home:" + os.path.relpath(path, self.home).replace(os.sep, "/")
        return "abs:" + path

    def path(self, key: str) -> str:
        kind, _, rest = key.partition(":")
        if kind == "repo":
            return os.path.join(self.repo, rest) if rest else self.repo
        if kind == "home":
            return os.path.join(self.home, rest)
        return rest

    def ignored(self, relative: str) -> bool:
        parts = relative.split("/")
        if any(part in IGNORED_NAMES for part in parts):
            return True
        return any(fnmatch.fnmatchcase(relative, pattern) for pattern in self.ignores)

    def portable(self, value: str) -> str:
        """Replace this checkout's absolute locations inside a string."""
        for prefix, token in ((self.repo, "$REPO"), (self.home, "$HOME")):
            value = value.replace(prefix, token)
        return value


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _git(arguments: Sequence[str], cwd: Path) -> str:
    try:
        completed = subprocess.run(["git", *arguments], cwd=str(cwd), capture_output=True,
                                   text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


# -- digests ----------------------------------------------------------------

class Digester:
    """Content identity of inputs, cached per real file within one process."""

    def __init__(self, places: Places) -> None:
        self.places = places
        self._content: dict[str, str] = {}

    def listing(self, path: str, *, removed: Iterable[str] = (), added: Iterable[str] = ()) -> str:
        names = set()
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.name in IGNORED_NAMES:
                    continue
                names.add(entry.name)
        names -= set(removed)
        names |= set(added)
        relative_base = self.places.key(path)
        if relative_base and relative_base.startswith("repo:"):
            base = relative_base[5:]
            names = {n for n in names
                     if not self.places.ignored(f"{base}/{n}" if base else n)}
        encoded = json.dumps(sorted(names), ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8", "surrogateescape")).hexdigest()

    def content(self, path: str) -> str:
        real = os.path.realpath(path)
        cached = self._content.get(real)
        if cached is not None:
            return cached
        hasher = hashlib.sha256()
        with open(real, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                hasher.update(chunk)
        value = hasher.hexdigest()
        self._content[real] = value
        return value

    def digest(self, key: str, operations: Iterable[str], *,
               removed: Iterable[str] = (), added: Iterable[str] = ()) -> str:
        path = self.places.path(key)
        try:
            info = os.lstat(path)
        except (FileNotFoundError, NotADirectoryError):
            return "missing"
        except OSError as exc:
            return f"error:{exc.errno}"
        mode = info.st_mode
        try:
            if stat.S_ISLNK(mode):
                return "l:" + self.places.portable(os.readlink(path))
            if stat.S_ISDIR(mode):
                if "enumerate" in operations:
                    return "d:" + self.listing(path, removed=removed, added=added)
                return "d"
            if stat.S_ISREG(mode):
                flag = "x" if mode & 0o111 else "f"
                return f"{flag}:{self.content(path)}"
        except OSError as exc:
            return f"error:{exc.errno}"
        return "o:" + oct(stat.S_IFMT(mode))


# -- environment and identity ------------------------------------------------

def environment_fingerprint(environment: Mapping[str, str], places: Places) -> dict[str, str]:
    selected: dict[str, str] = {}
    for name, value in environment.items():
        upper = name.upper()
        if name in ENVIRONMENT_EXCLUDED or upper in ENVIRONMENT_EXCLUDED:
            continue
        if name in ENVIRONMENT_KEYS or upper in ENVIRONMENT_KEYS or upper.startswith(ENVIRONMENT_PREFIXES):
            portable = places.portable(value)
            selected[name] = hashlib.sha256(portable.encode("utf-8", "surrogateescape")).hexdigest()[:16]
    selected["@system"] = platform.system()
    selected["@machine"] = platform.machine()
    return dict(sorted(selected.items()))


def unit_identity(argv: Sequence[str], cwd: Path, places: Places, label: str | None) -> dict[str, Any]:
    """Name a command by what it runs and where, never by its environment.

    An environment change must read as ``environment-changed`` against the
    same record, not as a new command without one. A matrix that runs the
    same command under several toolchains names each leg with ``--unit``.
    """
    identity = {
        "label": label or "",
        "argv": [places.portable(item) for item in argv],
        "cwd": places.key(os.path.realpath(str(cwd))) or "",
        "system": platform.system(),
        "machine": platform.machine(),
    }
    identity["key"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    return identity


# -- records -----------------------------------------------------------------

@dataclass
class Decision:
    skip: bool
    reason: str
    changed: list[str] = field(default_factory=list)
    checked: int = 0
    recorded_commit: str = ""
    recorded_run: str = ""


def build_record(observation: click_ci_trace.Observation, *, identity: Mapping[str, Any],
                 places: Places, digester: Digester, environment: Mapping[str, str]) -> dict[str, Any]:
    """Reduce an observation to the inputs a later checkout must reproduce."""

    volatile = list(observation.volatile_reasons)
    if observation.unresolved_lines:
        volatile.append(f"trace-unresolved:{observation.unresolved_lines}")
    produced_children: dict[str, set[str]] = {}
    deleted_children: dict[str, set[str]] = {}
    for absolute, state in observation.paths.items():
        parent, name = os.path.split(absolute)
        if state.first == "produced":
            produced_children.setdefault(parent, set()).add(name)
        elif state.first == "deleted":
            deleted_children.setdefault(parent, set()).add(name)
    inputs: dict[str, str] = {}
    pending: list[tuple[str, str, click_ci_trace.PathState]] = []
    for absolute, state in sorted(observation.paths.items()):
        key = places.key(absolute)
        if key is None or state.first == "produced":
            continue
        if state.first == "missing":
            inputs[key] = "missing"
            continue
        if state.first == "deleted":
            inputs[key] = "exists"
            continue
        if state.first == "touched":
            if "read" in state.operations or "execute" in state.operations:
                volatile.append(f"read-after-partial-write:{key}")
            continue
        if state.modified_after_input:
            volatile.append(f"changed-own-input:{key}")
            continue
        inputs[key] = ""  # keeps sorted order; filled below
        pending.append((absolute, key, state))

    def digest(job: tuple[str, str, click_ci_trace.PathState]) -> str:
        absolute, key, state = job
        return digester.digest(key, state.operations,
                               removed=produced_children.get(absolute, ()),
                               added=deleted_children.get(absolute, ()))

    with ThreadPoolExecutor(max_workers=DIGEST_WORKERS) as pool:
        for (_absolute, key, _state), value in zip(pending, pool.map(digest, pending)):
            if value == "missing":
                volatile.append(f"input-vanished:{key}")
                del inputs[key]
            else:
                inputs[key] = value
    return {
        "version": RECORD_VERSION,
        "unit": dict(identity),
        "recorded": {
            "commit": _git(["rev-parse", "HEAD"], Path(places.repo)) or os.environ.get("GITHUB_SHA", ""),
            "run": _run_url(),
            "at": _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds"),
        },
        "exit_code": observation.exit_code,
        "duration_seconds": round(observation.duration_seconds, 3),
        "process_count": observation.process_count,
        "environment": environment_fingerprint(environment, places),
        "volatile": volatile[:50],
        "unresolved_examples": list(observation.unresolved_examples),
        "inputs": inputs,
    }


def decide(record: Mapping[str, Any] | None, *, places: Places, digester: Digester,
           environment: Mapping[str, str]) -> Decision:
    if record is None:
        return Decision(False, "no-record")
    recorded = record.get("recorded", {}) if isinstance(record.get("recorded"), dict) else {}
    base = dict(recorded_commit=str(recorded.get("commit", "")), recorded_run=str(recorded.get("run", "")))
    if record.get("version") != RECORD_VERSION:
        return Decision(False, "record-version", **base)
    if record.get("exit_code") != 0:
        return Decision(False, "recorded-run-failed", **base)
    volatile = record.get("volatile") or []
    if volatile:
        return Decision(False, "volatile", changed=[str(v) for v in volatile[:MAX_REPORTED_CHANGES]], **base)
    current = environment_fingerprint(environment, places)
    previous = record.get("environment") or {}
    changed_environment = sorted(k for k in set(current) | set(previous) if current.get(k) != previous.get(k))
    if changed_environment:
        return Decision(False, "environment-changed",
                        changed=[f"env:{k}" for k in changed_environment[:MAX_REPORTED_CHANGES]], **base)
    inputs = record.get("inputs")
    if not isinstance(inputs, dict) or not inputs:
        return Decision(False, "no-inputs", **base)
    def differs(item: tuple[str, Any]) -> bool:
        key, expected = item
        if expected == "missing":
            actual = "missing" if not os.path.lexists(places.path(key)) else "exists"
        elif expected == "exists":
            actual = "exists" if os.path.lexists(places.path(key)) else "missing"
        else:
            operations = ("enumerate",) if str(expected).startswith("d:") else ()
            actual = digester.digest(key, operations)
        return actual != expected

    ordered = sorted(inputs.items(), key=lambda item: CHECK_ORDER.get(item[0].split(":", 1)[0], 2))
    changed: list[str] = []
    checked = 0
    with ThreadPoolExecutor(max_workers=DIGEST_WORKERS) as pool:
        for start in range(0, len(ordered), CHECK_BATCH):
            batch = ordered[start:start + CHECK_BATCH]
            checked += len(batch)
            changed.extend(key for (key, _), moved in zip(batch, pool.map(differs, batch)) if moved)
            if len(changed) >= MAX_REPORTED_CHANGES:
                break
    changed = changed[:MAX_REPORTED_CHANGES]
    if changed:
        return Decision(False, "inputs-changed", changed=changed, checked=checked, **base)
    return Decision(True, "unchanged", checked=checked, **base)


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json.gz"

    def load(self, key: str) -> dict[str, Any] | None:
        try:
            with gzip.open(self._path(key), "rt", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError, EOFError):
            return None
        return value if isinstance(value, dict) else None

    def save(self, key: str, record: Mapping[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.root, prefix=".record-", delete=False) as handle:
            temporary = Path(handle.name)
        with gzip.open(temporary, "wt", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, self._path(key))

    def remove(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def prune(self, keep: Iterable[str]) -> None:
        """Drop records of groups that no longer exist."""
        wanted = {self._path(key).name for key in keep}
        for path in self.root.glob("*.json.gz"):
            if path.name not in wanted:
                path.unlink(missing_ok=True)


# -- split commands ------------------------------------------------------------

def expand_paths(patterns: Iterable[str], cwd: Path) -> list[str]:
    """Paths matching the globs, as written, relative to cwd; ``!glob`` excludes."""
    included: dict[str, str] = {}
    excluded: set[str] = set()
    for pattern in patterns:
        negate = pattern.startswith("!")
        body = pattern[1:] if negate else pattern
        for match in glob.glob(body, root_dir=str(cwd), recursive=True):
            normal = os.path.normpath(match)
            if negate:
                excluded.add(normal)
            elif not PATH_EXCLUDED_PARTS.intersection(Path(normal).parts):
                included.setdefault(normal, match)
    return sorted(original for normal, original in included.items()
                  if normal not in excluded
                  and not any(str(parent) in excluded for parent in Path(normal).parents))


def substitute(template: Sequence[str], paths: Sequence[str]) -> list[str]:
    """The command with ``{paths}`` replaced: as arguments, or quoted inside a shell string."""
    argv: list[str] = []
    for item in template:
        if item == PATHS_TOKEN:
            argv.extend(paths)
        else:
            argv.append(item.replace(PATHS_TOKEN, shlex.join(paths)))
    return argv


def plan_groups(paths: Sequence[str], count: int = 0) -> list[list[str]]:
    """One group per path, or hash buckets: adding a path changes only its own group."""
    count = count if count > 0 else min(len(paths), AUTO_GROUPS)
    if count >= len(paths):
        return [[path] for path in paths]
    buckets: list[list[str]] = [[] for _ in range(count)]
    for path in paths:
        digest = hashlib.sha256(os.path.normpath(path).encode("utf-8", "surrogateescape")).digest()
        buckets[int.from_bytes(digest[:8], "big") % count].append(path)
    return [sorted(bucket) for bucket in buckets if bucket]


# -- mode selection and reporting -------------------------------------------

def _run_url() -> str:
    server, repository, run = (os.environ.get(n, "") for n in
                               ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repository}/actions/runs/{run}" if server and repository and run else ""


def automatic_mode(environment: Mapping[str, str]) -> str:
    configured = environment.get("CLICK_CI_MODE", "").strip().lower()
    if configured in {"record", "select", "shadow", "off"}:
        return configured
    if environment.get("GITHUB_ACTIONS") != "true":
        return "select"
    event = environment.get("GITHUB_EVENT_NAME", "")
    if event in {"pull_request", "pull_request_target", "merge_group"}:
        return "select"
    default_branch = ""
    try:
        payload = json.loads(Path(environment.get("GITHUB_EVENT_PATH", "")).read_text(encoding="utf-8"))
        default_branch = str(payload.get("repository", {}).get("default_branch", ""))
    except (OSError, ValueError, AttributeError):
        pass
    if default_branch and environment.get("GITHUB_REF") == f"refs/heads/{default_branch}":
        return "record"
    return "select"


def _describe(decision: Decision) -> str:
    if decision.skip:
        commit = decision.recorded_commit[:12] or "the recorded run"
        return f"none of {decision.checked} observed inputs changed since {commit}"
    detail = ", ".join(decision.changed)
    return f"{decision.reason}" + (f": {detail}" if detail else "")


def _report(entry: Mapping[str, Any], *, summary: bool = True) -> None:
    target = os.environ.get("CLICK_CI_REPORT")
    if target:
        try:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            pass
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and step_summary:
        verb = entry["action"]
        line = f"- click-ci **{verb}** `{entry['command']}` — {entry['detail']}\n"
        try:
            with open(step_summary, "a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass


def _say(message: str) -> None:
    sys.stderr.write(f"click-ci: {message}\n")
    sys.stderr.flush()


def _annotate(level: str, message: str) -> None:
    """A GitHub Actions annotation, so the message shows on the run page."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        sys.stdout.write(f"::{level}::{message}\n")
        sys.stdout.flush()
    else:
        _say(message)


def _record_unit(argv: Sequence[str], *, identity: Mapping[str, Any], store: Store, places: Places,
                 environment: Mapping[str, str], strace: str, cwd: Path) -> tuple[int, dict[str, Any]]:
    """Run one unit under observation and keep its record when it passes."""
    command = shlex.join(argv)
    observation = click_ci_trace.observe(argv, cwd=cwd, environment=environment, strace=strace)
    record = build_record(observation, identity=identity, places=places,
                          digester=Digester(places), environment=environment)
    volatile = record["volatile"]
    code = observation.exit_code
    if code == 0:
        store.save(identity["key"], record)
        detail = f"recorded {len(record['inputs'])} inputs in {observation.duration_seconds:.1f}s"
    else:
        store.remove(identity["key"])
        # Tracing must never turn a passing command into a failing one
        # (ptrace users, tight timeouts): the unobserved run decides.
        _say(f"`{command}` exited {code} under observation; running it again unobserved")
        code = subprocess.call(list(argv), cwd=str(cwd))
        detail = f"exited {observation.exit_code} under observation, {code} unobserved; nothing recorded"
        if code == 0:
            _annotate("warning", f"click-ci: `{command}` failed only under observation "
                      f"({', '.join(volatile[:3]) or 'no traced cause'}); nothing was recorded")
    if volatile:
        detail += f"; volatile: {', '.join(volatile[:3])}"
    _say(f"ran `{command}` (record) — {detail}")
    return code, {"detail": detail, "saved": observation.exit_code == 0,
                  "observed_exit_code": observation.exit_code,
                  "volatile": volatile[:MAX_REPORTED_CHANGES],
                  "unresolved_examples": record["unresolved_examples"]}


def run(argv: Sequence[str], *, mode: str, store: Store, label: str | None = None,
        cwd: Path | None = None) -> int:
    cwd = Path(cwd or os.getcwd())
    environment = dict(os.environ)
    places = Places.discover(cwd, store=store.root)
    identity = unit_identity(argv, cwd, places, label)
    command = shlex.join(argv)
    if mode == "off":
        return subprocess.call(list(argv), cwd=str(cwd))
    digester = Digester(places)
    previous = store.load(identity["key"])
    started = time.monotonic()
    decision = decide(previous, places=places, digester=digester, environment=environment)
    decide_seconds = time.monotonic() - started
    entry: dict[str, Any] = {
        "command": command, "unit": identity["key"], "mode": mode,
        "would_skip": decision.skip, "reason": decision.reason, "changed": decision.changed,
        "checked_inputs": decision.checked, "recorded_commit": decision.recorded_commit,
        "recorded_run": decision.recorded_run, "decide_seconds": round(decide_seconds, 3),
    }
    if mode == "select" and decision.skip:
        detail = _describe(decision)
        _say(f"skipped `{command}` — {detail}" + (f" ({decision.recorded_run})" if decision.recorded_run else ""))
        _report({**entry, "action": "skipped", "exit_code": 0, "detail": detail})
        return 0
    if mode == "record":
        strace = click_ci_trace.strace_available()
        if strace is None:
            _say("strace is unavailable: running unobserved and recording nothing")
            code = subprocess.call(list(argv), cwd=str(cwd))
            _report({**entry, "action": "ran", "exit_code": code, "detail": "record without strace"})
            return code
        code, outcome = _record_unit(argv, identity=identity, store=store, places=places,
                                     environment=environment, strace=strace, cwd=cwd)
        _report({**entry, "action": "recorded", "exit_code": code, **outcome,
                 "false_skip": decision.skip and code != 0})
        return code
    code = subprocess.call(list(argv), cwd=str(cwd))
    detail = _describe(decision)
    action = "ran" if mode == "select" else "shadow"
    if mode == "shadow" and decision.skip:
        detail = "would skip: " + detail
    _say(f"ran `{command}` ({mode}) — {detail}")
    _report({**entry, "action": action, "exit_code": code, "detail": detail,
             "false_skip": decision.skip and code != 0})
    return code


def _group_store(template: Sequence[str], cwd: Path, places: Places, store: Store,
                 label: str | None) -> Store:
    return Store(store.root / unit_identity(template, cwd, places, label)["key"])


def _decide_groups(identities: Sequence[Mapping[str, Any]], *, store: Store, places: Places,
                   environment: Mapping[str, str]) -> list[Decision]:
    digester = Digester(places)  # shared: an input read by every group is hashed once
    return [decide(store.load(identity["key"]), places=places, digester=digester, environment=environment)
            for identity in identities]


def _group_name(part: Sequence[str]) -> str:
    return part[0] + (f" +{len(part) - 1}" if len(part) > 1 else "")


def run_split(template: Sequence[str], patterns: Sequence[str], *, mode: str, store: Store,
              groups: int = 0, label: str | None = None, cwd: Path | None = None) -> int:
    """Record and decide each group of paths on its own; run the changed ones together."""
    cwd = Path(cwd or os.getcwd())
    environment = dict(os.environ)
    command = shlex.join(template)
    paths = expand_paths(patterns, cwd)
    if not paths:
        _say(f"no path in {cwd} matches {' '.join(patterns)}")
        return 2
    if mode == "off":
        return subprocess.call(substitute(template, paths), cwd=str(cwd))
    places = Places.discover(cwd, store=store.root)
    store = _group_store(template, cwd, places, store, label)
    parts = plan_groups(paths, groups)
    units = [(part, substitute(template, part)) for part in parts]
    identities = [unit_identity(argv, cwd, places, label) for _, argv in units]
    base: dict[str, Any] = {"command": command, "mode": mode, "groups": len(parts), "paths": len(paths)}

    if mode == "record":
        strace = click_ci_trace.strace_available()
        if strace is None:
            _say("strace is unavailable: running unobserved and recording nothing")
            code = subprocess.call(substitute(template, paths), cwd=str(cwd))
            _report({**base, "action": "ran", "exit_code": code, "detail": "record without strace"})
            return code
        codes: list[int] = []
        saved = 0
        for (part, argv), identity in zip(units, identities):
            code, outcome = _record_unit(argv, identity=identity, store=store, places=places,
                                         environment=environment, strace=strace, cwd=cwd)
            codes.append(code)
            saved += bool(outcome["saved"])
            _report({**base, "action": "group-recorded", "group": part, "unit": identity["key"],
                     "exit_code": code, **outcome}, summary=False)
        store.prune(identity["key"] for identity in identities)
        failed = [_group_name(part) for part, code in zip(parts, codes) if code]
        code = next((code for code in codes if code), 0)
        detail = (f"recorded {saved} of {len(parts)} groups ({len(paths)} paths)"
                  + (f"; failed: {', '.join(failed[:MAX_REPORTED_CHANGES])}" if failed else ""))
        _say(f"`{command}` (record) — {detail}")
        _report({**base, "action": "recorded", "exit_code": code, "detail": detail})
        return code

    started = time.monotonic()
    decisions = _decide_groups(identities, store=store, places=places, environment=environment)
    decide_seconds = round(time.monotonic() - started, 3)
    for (part, _argv), identity, decision in zip(units, identities, decisions):
        _report({**base, "action": "group-decided", "group": part, "unit": identity["key"],
                 "would_skip": decision.skip, "reason": decision.reason, "changed": decision.changed,
                 "checked_inputs": decision.checked, "detail": _describe(decision)}, summary=False)
    run_parts = [part for part, decision in zip(parts, decisions) if not decision.skip]
    skip_parts = [part for part, decision in zip(parts, decisions) if decision.skip]
    run_paths = sorted(path for part in run_parts for path in part)
    skip_paths = sorted(path for part in skip_parts for path in part)
    why = "; ".join(f"{_group_name(part)} ← {_describe(decision)}"
                    for part, decision in zip(parts, decisions) if not decision.skip)
    base.update(decide_seconds=decide_seconds, ran_groups=len(run_parts), ran_paths=len(run_paths))
    counts = f"{len(run_parts)} of {len(parts)} groups ({len(run_paths)} of {len(paths)} paths)"

    if mode == "select":
        if not run_paths:
            detail = f"no observed input of any of {len(parts)} groups ({len(paths)} paths) changed"
            _say(f"skipped `{command}` — {detail}")
            _report({**base, "action": "skipped", "exit_code": 0, "detail": detail})
            return 0
        _say(f"running {counts}: {why[:600]}")
        code = subprocess.call(substitute(template, run_paths), cwd=str(cwd))
        _report({**base, "action": "ran", "exit_code": code, "detail": f"ran {counts}; {why[:600]}"})
        return code

    # shadow: run what select would, then what it would skip, and say whether skipping was wrong.
    code = subprocess.call(substitute(template, run_paths), cwd=str(cwd)) if run_paths else 0
    check = subprocess.call(substitute(template, skip_paths), cwd=str(cwd)) if skip_paths else 0
    detail = f"would run {counts}" + (f"; the skipped paths failed with {check}" if check else "")
    _say(f"`{command}` (shadow) — {detail}")
    _report({**base, "action": "shadow", "exit_code": code or check, "detail": detail,
             "would_skip": not run_paths, "false_skip": check != 0})
    return code or check


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="click-ci", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("mode", help="print the mode `run --mode auto` would use here")
    for name in ("run", "plan", "key", "paths"):
        command = sub.add_parser(name)
        command.add_argument("--store", type=Path,
                             default=Path(os.environ.get("CLICK_CI_STORE") or
                                          Path(tempfile.gettempdir()) / "click-ci"))
        command.add_argument("--unit", help="optional label kept in the unit identity")
        command.add_argument("--paths", action="append", default=[], metavar="GLOBS",
                             help=f"globs (whitespace-separated, !glob excludes) substituted for "
                                  f"{PATHS_TOKEN} in the command; each group of paths is decided alone")
        command.add_argument("--groups", type=int, default=0,
                             help=f"number of path groups (default: one per path up to {AUTO_GROUPS})")
        if name == "run":
            command.add_argument("--mode", default="auto",
                                 choices=["auto", "record", "select", "shadow", "off"])
        command.add_argument("argv", nargs=argparse.REMAINDER)
    options = parser.parse_args(arguments)
    if options.action == "mode":
        print(automatic_mode(os.environ))
        return 0
    patterns = [item for value in options.paths for item in value.split()]
    if options.action == "paths":
        # The expansion alone, shell-quoted: lets a runner without Python-side
        # observation (macOS, Windows) run the same command.
        print(shlex.join(expand_paths(patterns, Path.cwd())))
        return 0
    argv = list(options.argv)
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        parser.error("give the command after `--`")
    if patterns and not any(PATHS_TOKEN in item for item in argv):
        parser.error(f"--paths needs {PATHS_TOKEN} in the command")
    if not patterns and any(PATHS_TOKEN in item for item in argv):
        parser.error(f"the command has {PATHS_TOKEN} but no --paths was given")
    store = Store(options.store)
    if options.action == "key":
        places = Places.discover(Path.cwd(), store=store.root)
        print(unit_identity(argv, Path.cwd(), places, options.unit)["key"])
        return 0
    if options.action == "plan" and patterns:
        cwd = Path.cwd()
        places = Places.discover(cwd, store=store.root)
        parts = plan_groups(expand_paths(patterns, cwd), options.groups)
        identities = [unit_identity(substitute(argv, part), cwd, places, options.unit) for part in parts]
        decisions = _decide_groups(identities, store=_group_store(argv, cwd, places, store, options.unit),
                                   places=places, environment=os.environ)
        print(json.dumps([{"group": part, "skip": d.skip, "reason": d.reason, "changed": d.changed}
                          for part, d in zip(parts, decisions)], indent=2))
        return 0
    if options.action == "plan":
        places = Places.discover(Path.cwd(), store=store.root)
        identity = unit_identity(argv, Path.cwd(), places, options.unit)
        decision = decide(store.load(identity["key"]), places=places, digester=Digester(places),
                          environment=os.environ)
        print(json.dumps({"unit": identity["key"], "skip": decision.skip, "reason": decision.reason,
                          "changed": decision.changed, "checked_inputs": decision.checked,
                          "recorded_commit": decision.recorded_commit}, indent=2))
        return 0
    mode = automatic_mode(os.environ) if options.mode == "auto" else options.mode
    if patterns:
        return run_split(argv, patterns, mode=mode, store=store, groups=options.groups, label=options.unit)
    return run(argv, mode=mode, store=store, label=options.unit)


if __name__ == "__main__":
    raise SystemExit(main())
