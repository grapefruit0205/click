"""Observed inputs of one command: where they live, their digests, records and decisions.

A record keeps what a passing command read, listed, executed or looked up and
did not find, each with a content digest. Deciding digests those inputs again:
when every one is unchanged, and the environment variables that change how
commands run are unchanged, an execution that read the same bytes takes the
same path, so its result still holds.

Click CI (``ci/click_ci.py``) keeps these records between CI runs; the agent-side
observed check (``click_observed_check.py``) keeps them on the user's machine.
"""

from __future__ import annotations

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
import tempfile
from typing import Any, Iterable, Mapping, Sequence

if __package__:
    from . import click_import_bootstrap
else:  # Executed directly from the bundled hooks directory.
    import click_import_bootstrap

(click_syscall_trace,) = click_import_bootstrap.load_siblings(__package__, "click_syscall_trace")


RECORD_VERSION = 1
CONFIG_PATH = Path(".click") / "ci.json"
MAX_REPORTED_CHANGES = 5
# Digests are file reads and sha256, both of which release the GIL. Inputs are
# checked in batches so a changed source file ends the check early.
DIGEST_WORKERS = min(8, os.cpu_count() or 1)
CHECK_BATCH = 256
# Repository files change most often; the toolchain under /usr least.
CHECK_ORDER = {"repo": 0, "home": 1}
# A record keeps a stat signature only for inputs that last changed this long
# before the observed run started, so a later change within the same timestamp
# tick cannot hide behind an equal signature (git's "racy clean" problem).
# Change times come from a coarse kernel clock, up to a tick behind the wall
# clock: a change this close before the start counts as made during the run.
RACY_WINDOW_NS = 2_000_000_000
LATE_MARGIN_NS = 10_000_000
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
    # Vite and Vitest: the results cache (test order), optimized dependencies
    # and bundled config files.
    "node_modules/.vite", "node_modules/.vite/*", "node_modules/.vitest", "node_modules/.vitest/*",
    "node_modules/.vite-temp", "node_modules/.vite-temp/*",
)
IGNORED_NAMES = frozenset({"__pycache__"})
# Tool logs under the home directory: rotated by the tool on every run, never
# what a test checks (npm deletes its oldest debug log each time it starts).
HOME_IGNORES = (".npm/_logs", ".npm/_logs/*")

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
                 os.path.realpath(tempfile.gettempdir())} if os.name != "nt" else {
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
                   volatile_roots=tuple(sorted(r for r in roots if r and r != os.path.dirname(r))),
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
            relative = os.path.relpath(path, self.home).replace(os.sep, "/")
            if any(fnmatch.fnmatchcase(relative, pattern) for pattern in HOME_IGNORES):
                return None
            return "home:" + relative
        return "abs:" + path

    def path(self, key: str) -> str:
        kind, _, rest = key.partition(":")
        if kind == "repo":
            return os.path.join(self.repo, *rest.split("/")) if rest else self.repo
        if kind == "home":
            return os.path.join(self.home, *rest.split("/"))
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
    # Windows paths compare without case and with either separator.
    path, root = os.path.normcase(path), os.path.normcase(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _git(arguments: Sequence[str], cwd: Path) -> str:
    try:
        # Git writes paths as UTF-8 whatever the console code page is.
        completed = subprocess.run(["git", *arguments], cwd=str(cwd), capture_output=True,
                                   encoding="utf-8", errors="surrogateescape", timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def git_ignored(repo: str, relatives: Iterable[str]) -> set[str]:
    """Which of these untracked repository paths git ignores (none outside a repository)."""
    names = sorted(set(relatives))
    if not names:
        return set()
    try:
        completed = subprocess.run(["git", "check-ignore", "-z", "--stdin"], cwd=repo,
                                   input="\0".join(names).encode("utf-8", "surrogateescape"),
                                   capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return set()
    if completed.returncode not in (0, 1):
        return set()
    return {name.decode("utf-8", "surrogateescape") for name in completed.stdout.split(b"\0") if name}


# -- digests ----------------------------------------------------------------

class Digester:
    """Content identity of inputs, cached per real file within one process."""

    def __init__(self, places: Places) -> None:
        self.places = places
        self._content: dict[str, str] = {}
        # Whether (key, recorded value) still holds, for the decisions that
        # share this digester: split groups mostly read the same files.
        self.verdicts: dict[tuple[str, str], bool] = {}

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


def stat_signature(path: str) -> list[int] | None:
    """Size, modification and change times and inode of a path, without following links."""
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if os.name == "nt":
        # st_ctime is the creation time there; NTFS keeps a change time too.
        changed = _windows_change_time_ns(path)
        if changed is None:
            return None
        return [info.st_size, info.st_mtime_ns, changed, info.st_ino]
    return [info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino]


_WINDOWS_EPOCH_100NS = 116_444_736_000_000_000
_windows_file_api: Any = None


def _windows_change_time_ns(path: str) -> int | None:
    """NTFS ChangeTime (any change to content or metadata), in ns since the Unix epoch."""
    global _windows_file_api
    import ctypes
    from ctypes import wintypes

    if _windows_file_api is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                         wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                          wintypes.DWORD]
        kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        _windows_file_api = kernel32
    kernel32 = _windows_file_api
    read_attributes, share_all, open_existing = 0x80, 0x7, 3
    backup_semantics, open_reparse_point = 0x02000000, 0x00200000
    handle = kernel32.CreateFileW(path, read_attributes, share_all, None, open_existing,
                                  backup_semantics | open_reparse_point, None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        return None
    try:
        # FILE_BASIC_INFO: creation, last access, last write and change times, attributes.
        info = (ctypes.c_longlong * 5)()
        if not kernel32.GetFileInformationByHandleEx(handle, 0, info, ctypes.sizeof(info)):
            return None
        return (info[3] - _WINDOWS_EPOCH_100NS) * 100
    finally:
        kernel32.CloseHandle(handle)


def _privileged(path: str) -> bool:
    """Whether executing ``path`` grants privileges, which a traced run silently loses."""
    if os.name == "nt":
        return False  # no setuid there; nothing traces the run from outside
    try:
        if os.stat(path).st_mode & (stat.S_ISUID | stat.S_ISGID):
            return True
        getxattr = getattr(os, "getxattr", None)
        return bool(getxattr and getxattr(path, "security.capability"))
    except OSError:
        return False


def build_record(observation: click_syscall_trace.Observation, *, identity: Mapping[str, Any],
                 places: Places, digester: Digester, environment: Mapping[str, str],
                 run_url: str = "", started_ns: int | None = None,
                 listings_before: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Reduce an observation to the inputs a later checkout must reproduce.

    With ``started_ns`` (the wall clock when the observed run started) the record
    also lists, under ``late``, inputs that changed while the command ran: their
    digests, taken afterwards, may not be what it read, so such a record must
    not be kept. A file is late when its inode changed after the start. A
    directory the command itself adds entries to changes too, so a listed
    directory found in ``listings_before`` (listing digests taken just before
    the run) is late only when its listing, less what the command produced and
    plus what it deleted, differs from before. Inputs that last changed well
    before the run keep a stat signature, so a later decision on the same
    machine can skip hashing them while the signature is unchanged.
    """

    volatile = list(observation.volatile_reasons)
    if observation.unresolved_lines:
        volatile.append(f"trace-unresolved:{observation.unresolved_lines}")
    produced_children: dict[str, set[str]] = {}
    deleted_children: dict[str, set[str]] = {}
    for absolute, state in observation.paths.items():
        if "execute" in state.operations and state.first != "produced" and _privileged(absolute):
            # Under ptrace setuid bits and file capabilities do not apply, so the
            # traced run is not the run the command makes on its own.
            volatile.append(f"privileged-exec:{places.key(absolute) or absolute}")
        parent, name = os.path.split(absolute)
        if state.first == "produced":
            produced_children.setdefault(parent, set()).add(name)
        elif state.first == "deleted":
            deleted_children.setdefault(parent, set()).add(name)
    # A file git ignores (a tool cache, a build output) that the command rewrites
    # around its reads is its own state inside the checkout too.
    own_state = git_ignored(places.repo, (
        key[len("repo:"):] for key, state in (
            (places.key(absolute), state) for absolute, state in observation.paths.items())
        if key and key.startswith("repo:") and key != "repo:"
        and state.first != "produced" and (state.first == "touched" or state.modified_after_input)))
    inputs: dict[str, str] = {}
    pending: list[tuple[str, str, click_syscall_trace.PathState]] = []
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
        if state.first == "touched" or state.modified_after_input:
            if not key.startswith("repo:") or key[len("repo:"):] in own_state:
                # Outside the checkout, or ignored by git inside it, a file the
                # command itself rewrites around its reads is its own state (cache
                # entries it refreshes, usage counters, logs), not something the
                # check is about.
                continue
            if state.first != "touched":
                volatile.append(f"changed-own-input:{key}")
            elif "read" in state.operations or "execute" in state.operations:
                volatile.append(f"read-after-partial-write:{key}")
            continue
        inputs[key] = ""  # keeps sorted order; filled below
        pending.append((absolute, key, state))

    def digest(job: tuple[str, str, click_syscall_trace.PathState]) -> str:
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
    late: list[str] = []
    stats: dict[str, list[int]] = {}
    if started_ns is not None:
        for key, value in inputs.items():
            if value in ("missing", "exists", "d"):
                continue
            signature = stat_signature(places.path(key))
            if value.startswith("d:") and listings_before is not None and key in listings_before:
                changed = value != "d:" + listings_before[key]
            else:
                changed = signature is None or signature[2] >= started_ns - LATE_MARGIN_NS
            if changed:
                late.append(key)
            elif signature is not None and signature[2] < started_ns - RACY_WINDOW_NS:
                stats[key] = signature
    return {
        "version": RECORD_VERSION,
        "unit": dict(identity),
        **({"late": late[:MAX_REPORTED_CHANGES], "late_count": len(late), "stats": stats}
           if started_ns is not None else {}),
        "recorded": {
            "commit": _git(["rev-parse", "HEAD"], Path(places.repo)) or os.environ.get("GITHUB_SHA", ""),
            "run": run_url,
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
           environment: Mapping[str, str], use_stats: bool = False) -> Decision:
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
    signatures = record.get("stats") if use_stats and isinstance(record.get("stats"), dict) else {}

    def quick(key: str, expected: str) -> bool | None:
        """Whether the input moved, when a lookup answers it without reading contents."""
        known = signatures.get(key)
        if known is not None and stat_signature(places.path(key)) == known:
            return False  # same inode, size and change time as when it was digested
        if expected in ("missing", "exists"):
            return ("exists" if os.path.lexists(places.path(key)) else "missing") != expected
        return None

    def differs(item: tuple[str, str]) -> bool:
        key, expected = item
        operations = ("enumerate",) if expected.startswith("d:") else ()
        return digester.digest(key, operations) != expected

    ordered = sorted(((key, str(value)) for key, value in inputs.items()),
                     key=lambda item: CHECK_ORDER.get(item[0].split(":", 1)[0], 2))
    changed: list[str] = []
    unknown: list[tuple[str, str]] = []
    for item in ordered:
        moved = digester.verdicts.get(item)
        if moved is None:
            moved = quick(*item)
            if moved is not None:
                digester.verdicts[item] = moved
        if moved is None:
            unknown.append(item)
        elif moved:
            changed.append(item[0])
    checked = len(ordered) - len(unknown)
    if unknown and len(changed) < MAX_REPORTED_CHANGES:
        with ThreadPoolExecutor(max_workers=DIGEST_WORKERS) as pool:
            for start in range(0, len(unknown), CHECK_BATCH):
                batch = unknown[start:start + CHECK_BATCH]
                checked += len(batch)
                for item, moved in zip(batch, pool.map(differs, batch)):
                    digester.verdicts[item] = moved
                    if moved:
                        changed.append(item[0])
                if len(changed) >= MAX_REPORTED_CHANGES:
                    break
    position = {key: index for index, (key, _value) in enumerate(ordered)}
    changed = sorted(changed, key=position.__getitem__)[:MAX_REPORTED_CHANGES]
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

    def touch(self, key: str) -> None:
        """Mark a record as used, so age-based pruning keeps it."""
        try:
            os.utime(self._path(key))
        except OSError:
            pass

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
    """Paths matching the globs, as written with ``/``, relative to cwd; ``!glob`` excludes."""
    included: dict[str, str] = {}
    excluded: set[str] = set()
    for pattern in patterns:
        negate = pattern.startswith("!")
        body = pattern[1:] if negate else pattern
        for match in glob.glob(body, root_dir=str(cwd), recursive=True):
            match = match.replace(os.sep, "/")  # Windows: backslashes would be escapes in bash
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
