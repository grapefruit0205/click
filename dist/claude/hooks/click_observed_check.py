"""Checks reused by what they observably read, for any command, observed in the background.

In Evidence mode on a Linux host with strace, the hook rewrites
``click-gate verify -- <command>`` to ``run`` here; on Windows it does so for
commands that run Node, which are observed from inside their Node processes
(``click_runtime_trace``) since nothing traces system calls there without
administrator rights. Nothing about the project
is recognized or configured: the command runs as given, and a record of the
files, directory listings, programs and failed lookups a passing run touched
(``click_input_records``) decides the next request. When none of them changed,
the command does not run again.

The run the agent waits for is never traced while a record can be refreshed
later, because tracing slows import-heavy suites about twice. When it passes, a
detached worker runs the command once more under strace at the lowest CPU and
I/O priority while the agent reads the result. That record is kept only when
the traced run passed too and none of its inputs changed while it ran, so an
edit made in the meantime cannot hide behind it. A new foreground check stops a
worker that is still running, so two copies of one suite never share ports or
fixture files, and hands the worker's unfinished units to the next one. A
command that reached the network or a socket is never run a second time in the
background.

``--paths GLOBS`` with ``{paths}`` in the command splits it into groups of
paths, each recorded and decided on its own; only the changed groups run,
together in one command. A split is trusted only after every group passed when
run alone and the groups together read every repository input the whole
command read; until then the whole command runs.

Records stay on the user's machine, per checkout, under the plugin data
directory; nothing is written to the project.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import datetime as _datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, Mapping, Sequence

try:
    import fcntl
except ImportError:  # Windows: msvcrt locks instead.
    fcntl = None  # type: ignore[assignment]

if __package__:
    from . import click_import_bootstrap
else:  # Executed directly from the bundled hooks directory.
    import click_import_bootstrap

(click_input_records, click_runner_transport, click_runtime_trace, click_status_summary,
 click_syscall_trace) = click_import_bootstrap.load_siblings(
    __package__, "click_input_records", "click_runner_transport", "click_runtime_trace",
    "click_status_summary", "click_syscall_trace"
)
WINDOWS = os.name == "nt"
records = click_input_records

# `CLICK_OBSERVED_CHECK=off` keeps `click-gate verify` on the runtime profiles.
ROUTE_VARIABLE = "CLICK_OBSERVED_CHECK"
# `background` (default) traces a passing run again in a detached worker;
# `inline` does that before returning (slower, deterministic); `foreground`
# traces the run itself; `off` only runs. A checkout whose workers keep being
# ended from outside (a sandbox that kills what a command leaves behind) falls
# back from `background` to `foreground` for a day.
MODE_VARIABLE = "CLICK_OBSERVATION"
MODES = ("background", "inline", "foreground", "off")
OFF_VALUES = frozenset({"0", "off", "false", "no"})
STORE_NAME = "observed-checks"
UNUSABLE_FILE = "strace-unusable.json"
WORKER_FILE = "worker.json"
LOCK_FILE = "worker.lock"
REPORT_FILE = "report.json"
NOTES_FILE = "notes.json"
SPLIT_FILE = "split.json"
# The repository files the whole command of a split read when last observed
# passing: what its groups must cover to be trusted.
FULL_READS_FILE = "full-reads.json"
DEATHS_FILE = "worker-deaths.json"
# Running these again in the background would repeat their external effects.
# A privileged program behaves differently under a tracer, every time.
STRUCTURAL_VOLATILITY = ("network:", "socket:", "nested-ptrace", "privileged-exec:")
RETRY_VOLATILE_SECONDS = 24 * 60 * 60
RETRY_STRUCTURAL_SECONDS = 7 * 24 * 60 * 60
FAILURE_LIMIT = 2
TIMEOUT_FLOOR_SECONDS = 300
TIMEOUT_FACTOR = 6
LOCK_WAIT_SECONDS = 5
STOP_WAIT_SECONDS = 2
SNAPSHOT_DIRECTORY_LIMIT = 20_000
SNAPSHOT_SKIPPED = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__"})
# Records unused this long are deleted, checked at most once a day.
RECORD_TTL_SECONDS = 30 * 24 * 60 * 60
PRUNE_FILE = "pruned.json"
SHOWN_COMMAND_CHARS = 160
SHOWN_KEYS = 3


# -- host routing (called by the hook) -----------------------------------------

def store_root(environment: Mapping[str, str] | None = None) -> Path:
    source = os.environ if environment is None else environment
    configured = source.get("PLUGIN_DATA")
    base = Path(configured) if configured else Path(tempfile.gettempdir()) / "click-plugin-data"
    return base / STORE_NAME


def _switch(name: str, environment: Mapping[str, str]) -> str:
    return str(environment.get(name, "")).strip().lower()


def available(environment: Mapping[str, str] | None = None, argv: Sequence[str] | None = None) -> bool:
    """Whether this host can observe ``argv``, so `click-gate verify` comes here.

    Linux with strace observes any command. Windows observes commands that run
    Node; the others keep the runtime profiles, which know their runners.
    """
    source = os.environ if environment is None else environment
    if _switch(ROUTE_VARIABLE, source) in OFF_VALUES:
        return False
    if WINDOWS:
        if argv is None or shutil.which("node", path=source.get("PATH")) is None:
            return False
        if not click_runtime_trace.node_command(argv, source):
            return False
    elif not sys.platform.startswith("linux") or fcntl is None or shutil.which("strace") is None:
        return False
    marker = _read_json(store_root(source) / UNUSABLE_FILE)
    return not marker or time.time() - float(marker.get("at", 0)) >= RETRY_VOLATILE_SECONDS


# `click-gate verify -- <text>`: the text after `--`, past any `--paths` values.
_VERIFY_TEXT = re.compile(
    r"^\s*click-gate\s+verify\s+(?:--paths\s+(?:'[^']*'|\"[^\"]*\"|\S+)\s+)*--\s+(.*?)\s*$", re.S
)
# Characters a POSIX shell would act on; quoting alone needs no shell.
_SHELL_CHARACTERS = frozenset("|&;<>()$`*?[~\n")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SHELL_PREFIX = ("bash", "-o", "pipefail", "-c")


def check_argv(command: str, argv: Sequence[str]) -> list[str]:
    """What to run for ``click-gate verify -- <text>``.

    Plain words run as the argv the hook parsed. Text a shell would act on
    (pipes, `&&`, redirections, globs, variables, a leading assignment) runs
    as written through bash, with ``pipefail`` so ``check | tail`` still fails
    when the check fails: a record must never call a failing check passed.
    """
    match = _VERIFY_TEXT.match(command)
    text = match.group(1) if match else ""
    body = text.replace(records.PATHS_TOKEN, "")
    if text and (_SHELL_CHARACTERS.intersection(body) or (argv and _ASSIGNMENT.match(argv[0]))):
        return [*SHELL_PREFIX, text]
    return list(argv)


def runner_command(argv: Sequence[str], patterns: Sequence[str],
                   environment: Mapping[str, str] | None = None) -> str:
    arguments = [sys.executable, str(Path(__file__).resolve()), "run",
                 "--store", str(store_root(environment))]
    for pattern in patterns:
        arguments += ["--paths", pattern]
    # The host's shell renders it (Git Bash, PowerShell or cmd.exe on Windows).
    return click_runner_transport.render_runner_shell_command([*arguments, "--", *argv])


# -- small helpers --------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(temporary, path)


def _shown(argv: Sequence[str]) -> str:
    if tuple(argv[:len(SHELL_PREFIX)]) == SHELL_PREFIX and len(argv) == len(SHELL_PREFIX) + 1:
        text = argv[-1]
    else:
        text = shlex.join(argv).replace(shlex.quote(records.PATHS_TOKEN), records.PATHS_TOKEN)
    return text if len(text) <= SHOWN_COMMAND_CHARS else text[:SHOWN_COMMAND_CHARS - 1] + "…"


def _keys(keys: Iterable[str]) -> str:
    listed = [key.removeprefix("repo:") or "." for key in keys]
    shown = ", ".join(listed[:SHOWN_KEYS])
    return shown + (", …" if len(listed) > SHOWN_KEYS else "")


def _age(record: Mapping[str, Any] | None) -> float:
    recorded = (record or {}).get("recorded")
    at = recorded.get("at", "") if isinstance(recorded, dict) else ""
    try:
        return time.time() - _datetime.datetime.fromisoformat(str(at)).timestamp()
    except ValueError:
        return float("inf")


def _alive(pid: int) -> bool:
    if WINDOWS:
        return _windows().process_alive(pid)
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return fields.rpartition(")")[2].split()[:1] != ["Z"]


def _runs(pid: int, marker: bytes) -> bool:
    if WINDOWS:
        command_line = _windows().describe(pid).command_line or ""
        return marker.decode() in command_line
    try:
        return marker in Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False


def _windows() -> Any:
    (module,) = click_import_bootstrap.load_siblings(__package__, "click_windows_job")
    return module


def _tracer() -> str | None:
    """What observes a run on this host: strace's path, ``node`` on Windows, or None."""
    if WINDOWS:
        return "node" if shutil.which("node") else None
    return click_syscall_trace.strace_available()


def _trace(argv: Sequence[str], *, cwd: Path, places: Any, tracer: str, background: bool = False,
           quiet: bool = False, timeout: float | None = None, started: Any = None) -> Any:
    """Run ``argv`` once, observed, and return its Observation."""
    output = subprocess.DEVNULL if quiet else None
    if WINDOWS:
        return click_runtime_trace.observe(argv, cwd=cwd, repo=places.repo, stdout=output, stderr=output,
                                           timeout_seconds=timeout, on_start=started, idle=background)
    return click_syscall_trace.observe(argv, cwd=cwd, strace=tracer, stdout=output, stderr=output,
                                       timeout_seconds=timeout, on_start=started)


# -- units ----------------------------------------------------------------------

@dataclass
class Checkout:
    cwd: Path
    places: Any
    store: Path  # every checkout's records
    root: Path  # this checkout's records
    environment: dict[str, str]
    locale: str

    @classmethod
    def open(cls, store: Path, cwd: Path, environment: Mapping[str, str]) -> "Checkout":
        places = records.Places.discover(cwd, store=store)
        name = hashlib.sha256(places.repo.encode("utf-8", "surrogateescape")).hexdigest()[:16]
        return cls(cwd, places, store, store / name, dict(environment),
                   click_status_summary.resolve_locale(environment))

    def msg(self, key: str, *values: object) -> str:
        return click_status_summary.message(key, self.locale, *values, max_value_chars=None)

    def decide(self, unit: "Unit", digester: Any = None) -> Any:
        return records.decide(unit.load(), places=self.places,
                              digester=digester or records.Digester(self.places),
                              environment=self.environment, use_stats=True)


@dataclass
class Unit:
    argv: list[str]
    identity: dict[str, Any]
    store_dir: Path
    paths: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return str(self.identity["key"])

    def load(self) -> dict[str, Any] | None:
        return records.Store(self.store_dir).load(self.key)

    def spec(self) -> dict[str, Any]:
        return {"argv": self.argv, "identity": self.identity, "store": str(self.store_dir)}


def _unit(argv: Sequence[str], checkout: Checkout, store_dir: Path, paths: Sequence[str] = ()) -> Unit:
    identity = records.unit_identity(argv, checkout.cwd, checkout.places, None)
    return Unit(list(argv), identity, store_dir, list(paths))


def _plan(record: Mapping[str, Any] | None, note: Mapping[str, Any] | None, *,
          split: bool) -> tuple[str, str]:
    """How to observe a unit that ran: ``background``, ``foreground`` or ``none`` (and why)."""
    volatile = [str(item) for item in (record or {}).get("volatile") or []]
    structural = [item for item in volatile if item.startswith(STRUCTURAL_VOLATILITY)]
    if structural:
        # Tracing the run the agent waits for repeats nothing; do that rarely.
        if not split and _age(record) > RETRY_STRUCTURAL_SECONDS:
            return "foreground", ""
        return "none", ", ".join(structural[:SHOWN_KEYS])
    if volatile and _age(record) < RETRY_VOLATILE_SECONDS:
        return "none", ", ".join(volatile[:SHOWN_KEYS])
    retry = RETRY_STRUCTURAL_SECONDS if (note or {}).get("structural") else RETRY_VOLATILE_SECONDS
    if (note and int(note.get("failures", 0)) >= FAILURE_LIMIT
            and time.time() - float(note.get("at", 0)) < retry):
        return "none", str(note.get("why", ""))
    return "background", ""


def _why(checkout: Checkout, decision: Any) -> str:
    if decision.reason == "no-record":
        return checkout.msg("기록 없음")
    if decision.reason == "inputs-changed":
        return checkout.msg("입력 변경: {0}", _keys(decision.changed))
    if decision.reason == "environment-changed":
        return checkout.msg("환경 변수 변경: {0}", _keys(item.removeprefix("env:") for item in decision.changed))
    if decision.reason == "volatile":
        return checkout.msg("재사용할 수 없는 동작: {0}", _keys(decision.changed))
    return checkout.msg("기록을 쓸 수 없음({0})", decision.reason)


def _call(argv: Sequence[str], checkout: Checkout) -> int:
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        return subprocess.call(click_runtime_trace.launchable(argv), cwd=str(checkout.cwd))
    except FileNotFoundError:
        print(f"{argv[0]}: command not found", file=sys.stderr)
        return 127
    except PermissionError:
        print(f"{argv[0]}: permission denied", file=sys.stderr)
        return 126


# -- the background worker's lifecycle ------------------------------------------

def _stop_worker(checkout: Checkout) -> dict[str, Any]:
    """Stop a worker still observing this checkout; return what it had left to do."""
    path = checkout.root / WORKER_FILE
    state = _read_json(path)
    if not state:
        return {}
    pid = int(state.get("pid") or 0)
    group = int(state.get("group") or 0)
    worker = pid > 0 and _alive(pid) and _runs(pid, b"click_observed_check")
    if not worker:
        # Gone without removing its state: something outside ended it.
        deaths = _read_json(checkout.root / DEATHS_FILE)
        _write_json(checkout.root / DEATHS_FILE, {"count": int(deaths.get("count", 0)) + 1, "at": time.time()})
    if worker and WINDOWS:
        # Its job object ends the observed tree when the worker's handle closes.
        _windows().terminate(pid)
    elif worker:
        # The worker stops its traced tree and removes its trace files itself.
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        deadline = time.monotonic() + STOP_WAIT_SECONDS
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
    if not WINDOWS and group > 0 and _alive(group) and _runs(group, b"strace"):
        try:
            os.killpg(group, signal.SIGKILL)
        except OSError:
            pass
    if worker and not WINDOWS and _alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    path.unlink(missing_ok=True)
    pending = state.get("pending")
    return pending if isinstance(pending, dict) else {}


def _merge(job: dict[str, Any], carried: Mapping[str, Any], ran: set[str]) -> dict[str, Any]:
    """Append a stopped worker's units that the foreground run did not supersede."""
    seen = {spec["identity"]["key"] for spec in job["units"]} | ran
    for spec in carried.get("units") or []:
        key = spec.get("identity", {}).get("key")
        if key and key not in seen:
            job["units"].append({**spec, "carried": True})
            seen.add(key)
    if not job.get("validate") and carried.get("validate"):
        job["validate"] = carried["validate"]
    return job


def _schedule(checkout: Checkout, mode: str, units: Sequence[Unit], *, seconds: float,
              carried: Mapping[str, Any], ran: set[str], validate: Mapping[str, Any] | None = None) -> str:
    """Observe ``units`` (and anything carried over) after the foreground run; return a report line."""
    job = _merge({
        "version": 1, "cwd": str(checkout.cwd), "store": str(checkout.store), "root": str(checkout.root),
        "timeout": max(TIMEOUT_FLOOR_SECONDS, TIMEOUT_FACTOR * seconds),
        "units": [unit.spec() for unit in units], "validate": dict(validate) if validate else None,
    }, carried, ran)
    if mode == "foreground":  # nothing may outlive the command: observe only what just ran
        job["units"] = [unit.spec() for unit in units]
        job["validate"] = dict(validate) if validate else None
    if not job["units"]:
        return ""
    if mode in ("inline", "foreground"):
        return _describe_report(checkout, work(job), verbose=True)
    checkout.root.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=checkout.root, prefix="job-", suffix=".json")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(job, handle)
    try:
        process = _detach([sys.executable, str(Path(__file__).resolve()), "observe", "--job", name],
                          cwd=checkout.cwd, log=checkout.root / "worker.log")
    except OSError:
        Path(name).unlink(missing_ok=True)
        return ""
    # Named before it starts, so a check issued right after this one stops it too.
    _write_json(checkout.root / WORKER_FILE, {
        "pid": process.pid, "group": 0,
        "pending": {"units": job["units"], "validate": job["validate"]},
    })
    return ""


def _detach(argv: Sequence[str], *, cwd: Path, log: Path) -> subprocess.Popen:
    """Start a worker that outlives the tool call."""
    with open(log, "wb") as handle:
        # No descriptor of the tool call survives in the worker: the host
        # waits for the command's output pipes to close.
        options: dict[str, Any] = dict(cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=handle, close_fds=True)
        if not WINDOWS:
            return subprocess.Popen(list(argv), start_new_session=True, **options)
        job = _windows()
        flags = job.DETACHED_PROCESS | job.CREATE_NEW_PROCESS_GROUP
        try:
            # Out of the host's job, which may end everything a command leaves behind.
            return subprocess.Popen(list(argv), creationflags=flags | job.CREATE_BREAKAWAY_FROM_JOB, **options)
        except OSError:
            return subprocess.Popen(list(argv), creationflags=flags, **options)


def _lower_priority() -> None:
    if WINDOWS:
        _windows().lower_priority()
        return
    try:
        os.nice(19)
    except OSError:
        pass
    ionice = shutil.which("ionice")
    if ionice:
        try:
            subprocess.run([ionice, "-c", "3", "-p", str(os.getpid())], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            pass


def _lock(root: Path) -> Any:
    root.mkdir(parents=True, exist_ok=True)
    handle = open(root / LOCK_FILE, "a+")
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return handle
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                return None
            time.sleep(0.05)


def _snapshot(places: Any) -> dict[str, str]:
    """Listing digests of the checkout's directories, taken before a traced run."""
    digester = records.Digester(places)
    listings: dict[str, str] = {}
    stack = [places.repo]
    while stack and len(listings) < SNAPSHOT_DIRECTORY_LIMIT:
        directory = stack.pop()
        key = places.key(directory)
        if key is None:
            continue
        try:
            listings[key] = digester.listing(directory)
            with os.scandir(directory) as entries:
                stack.extend(entry.path for entry in entries
                             if entry.name not in SNAPSHOT_SKIPPED and entry.is_dir(follow_symlinks=False))
        except OSError:
            continue
    return listings


def work(job: Mapping[str, Any]) -> dict[str, Any]:
    """Trace each unit of a job once and keep the records that hold; return a report."""
    root = Path(job["root"])
    cwd = Path(job["cwd"])
    report: dict[str, Any] = {"at": time.time(), "entries": []}
    lock = _lock(root)
    if lock is None:
        report["busy"] = True
        return report
    pending = {"units": list(job["units"]), "validate": job.get("validate")}
    state = {"pid": os.getpid(), "group": 0, "pending": pending}
    try:
        tracer = _tracer()
        if tracer is None:
            _write_json(Path(job["store"]) / UNUSABLE_FILE, {"at": time.time()})
            report["unusable"] = True
            return report
        places = records.Places.discover(cwd, store=Path(job["store"]))
        _prune(Path(job["store"]))
        outcomes: dict[str, str] = {}
        reads: dict[str, list[str]] = {}
        validate = job.get("validate") or {}
        full_key = validate.get("full", {}).get("identity", {}).get("key") if validate else None
        for spec in job["units"]:
            _write_json(root / WORKER_FILE, state)

            def started(group: int) -> None:
                _write_json(root / WORKER_FILE, {**state, "group": group})

            entry = _observe(spec, cwd=cwd, places=places, tracer=tracer,
                             timeout=float(job["timeout"]), started=started)
            key = spec["identity"]["key"]
            unit_reads = entry.pop("_reads", None)
            if unit_reads is not None:
                reads[key] = unit_reads
                if key == full_key and validate.get("full_reads"):
                    _write_json(Path(validate["full_reads"]), {"key": key, "reads": unit_reads, "at": time.time()})
            report["entries"].append(entry)
            outcomes[key] = entry["result"]
            pending["units"] = pending["units"][1:]
        if validate:
            verdict = _validate(validate, outcomes, reads, places)
            if verdict:
                report["split"] = verdict
    finally:
        if _read_json(root / WORKER_FILE).get("pid") == os.getpid():
            (root / WORKER_FILE).unlink(missing_ok=True)
        lock.close()
    return report


def _observe(spec: Mapping[str, Any], *, cwd: Path, places: Any, tracer: str, timeout: float,
             started: Any) -> dict[str, Any]:
    argv = list(spec["argv"])
    identity = dict(spec["identity"])
    key = str(identity["key"])
    store = records.Store(Path(spec["store"]))
    shown = _shown(argv)
    environment = dict(os.environ)
    current = records.decide(store.load(key), places=places, digester=records.Digester(places),
                             environment=environment, use_stats=True)
    if current.skip:
        return {"command": shown, "result": "current"}
    before = _snapshot(places)
    started_ns = time.time_ns()
    observation = _trace(argv, cwd=cwd, places=places, tracer=tracer, background=True, quiet=True,
                         timeout=timeout, started=started)
    record = records.build_record(observation, identity=identity, places=places,
                                  digester=records.Digester(places), environment=environment,
                                  started_ns=started_ns, listings_before=before)
    notes_path = store.root / NOTES_FILE
    notes = _read_json(notes_path)
    if observation.exit_code != 0:
        structural = [item for item in record["volatile"] if str(item).startswith(STRUCTURAL_VOLATILITY)]
        if structural:  # it will never pass traced: stop trying for a while
            notes[key] = {"failures": FAILURE_LIMIT, "at": time.time(), "structural": True,
                          "why": _keys(structural)}
            _write_json(notes_path, notes)
        elif record.get("late_count") or spec.get("carried"):
            # An input changed while it ran, or since the run it repeats passed
            # (a carried unit): the failure may be the edit's, not the command's.
            return {"command": shown, "result": "inconclusive", "exit_code": observation.exit_code,
                    "changed": (record.get("late") or [])[:SHOWN_KEYS]}
        else:
            note = notes.get(key) if isinstance(notes.get(key), dict) else {}
            notes[key] = {"failures": int(note.get("failures", 0)) + 1, "at": time.time(),
                          "why": f"exit {observation.exit_code} under observation"}
            _write_json(notes_path, notes)
        return {"command": shown, "result": "failed", "exit_code": observation.exit_code,
                "volatile": record["volatile"][:SHOWN_KEYS]}
    # Which repository files a passing run read holds even when their contents
    # moved meanwhile; a split's verdict needs only that.
    reads = sorted(item for item in record["inputs"] if item.startswith("repo:"))
    if record.get("late_count"):
        return {"command": shown, "result": "dropped", "changed": record["late"][:SHOWN_KEYS], "_reads": reads}
    if notes.pop(key, None) is not None:
        _write_json(notes_path, notes)
    record["observed"] = "background"
    store.save(key, record)
    return {"command": shown, "result": "recorded", "inputs": len(record["inputs"]),
            "volatile": record["volatile"][:SHOWN_KEYS],
            "seconds": round(observation.duration_seconds, 1), "_reads": reads}


def _read_by(key: str, store: Path, reads: Mapping[str, Sequence[str]]) -> set[str] | None:
    """The repository files a passing run of ``key`` read: from this job, else from its record."""
    if key in reads:
        return set(reads[key])
    record = records.Store(store).load(key)
    if record is None or record.get("exit_code") != 0:
        return None
    return {item for item in record.get("inputs") or {} if item.startswith("repo:")}


def _validate(spec: Mapping[str, Any], outcomes: Mapping[str, str], reads: Mapping[str, Sequence[str]],
              places: Any) -> dict[str, Any] | None:
    """Trust a split once its groups, each run alone, passed and read what the whole command read.

    A run whose record was dropped because an input changed while it ran still
    shows which files it reads, so edits made meanwhile do not hold the verdict
    back. The whole command's reads are kept between jobs, so it is traced once.
    """
    full_key = spec["full"]["identity"]["key"]
    failed = [key for key in spec["groups"] if outcomes.get(key) == "failed"]
    verdict: dict[str, Any]
    if failed:
        verdict = {"status": "rejected", "at": time.time(),
                   "why": f"{len(failed)} group(s) failed when run alone"}
    else:
        groups = [_read_by(key, Path(spec["groups_store"]), reads) for key in spec["groups"]]
        full = _read_by(full_key, Path(spec["full"]["store"]), reads)
        if full is None and spec.get("full_reads"):
            saved = _read_json(Path(spec["full_reads"]))
            if saved.get("key") == full_key and isinstance(saved.get("reads"), list):
                full = set(saved["reads"])
        if full is None or any(group is None for group in groups):
            return None  # not every run has been seen passing yet: judge on a later pass
        covered: set[str] = set().union(*groups)
        uncovered = sorted(key for key in full - covered if os.path.lexists(places.path(key)))
        # Dependencies and caches git ignores are the runner's own files; which
        # of them it loads can depend on how many files it was given.
        ignored = records.git_ignored(places.repo, (key[len("repo:"):] for key in uncovered))
        uncovered = [key for key in uncovered if key[len("repo:"):] not in ignored]
        verdict = ({"status": "rejected", "at": time.time(),
                    "why": "only the whole command read " + _keys(uncovered)}
                   if uncovered else {"status": "trusted", "at": time.time(), "groups": len(groups)})
    _write_json(Path(spec["meta"]), verdict)
    return verdict


def _describe_report(checkout: Checkout, report: Mapping[str, Any], *, verbose: bool = False) -> str:
    """One line on what the last observation found, when that changes what the agent should expect."""
    items: list[str] = []
    if report.get("unusable"):
        items.append(checkout.msg("strace를 쓸 수 없어 기록하지 못함"))
    for entry in report.get("entries") or []:
        result = entry.get("result")
        if result == "recorded" and verbose:
            items.append(checkout.msg("{0} 입력 {1}개 기록", entry["command"], entry.get("inputs", 0)))
        elif result == "dropped":
            items.append(checkout.msg("{0} 관찰 중 입력이 바뀌어 기록하지 않음({1})",
                                      entry["command"], _keys(entry.get("changed") or [])))
        elif result == "failed":
            items.append(checkout.msg("{0} 관찰 실행만 실패함(종료 코드 {1})",
                                      entry["command"], entry.get("exit_code")))
        elif result == "inconclusive" and verbose:
            items.append(checkout.msg("{0} 관찰 실행이 실패했지만 그사이 입력이 바뀌어 판정하지 않음",
                                      entry["command"]))
    split = report.get("split")
    if isinstance(split, dict) and split.get("status") == "trusted":
        items.append(checkout.msg("분할 확인됨: 다음부터 바뀐 그룹만 실행"))
    elif isinstance(split, dict) and split.get("status") == "rejected":
        items.append(checkout.msg("분할 거부됨: {0}", split.get("why", "")))
    if report.get("stopped"):
        items.append(checkout.msg("이전 백그라운드 관찰을 멈추고 남은 작업을 넘겨받음"))
    if report.get("fallback"):
        items.append(checkout.msg("백그라운드 관찰이 밖에서 끊겨 하루 동안 실행 자체를 관찰함"))
    return checkout.msg("[Click 관찰] {0}", "; ".join(items)) if items else ""


def _prune(store: Path) -> None:
    """Delete records no check used for RECORD_TTL_SECONDS, across checkouts."""
    marker = store / PRUNE_FILE
    if time.time() - float(_read_json(marker).get("at", 0)) < 24 * 60 * 60:
        return
    _write_json(marker, {"at": time.time()})
    cutoff = time.time() - RECORD_TTL_SECONDS
    for path in store.glob("*/**/*.json.gz"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue
    for checkout in store.iterdir():
        if checkout.is_dir() and not any(checkout.glob("**/*.json.gz")) and not (checkout / WORKER_FILE).exists():
            shutil.rmtree(checkout, ignore_errors=True)


def _take_report(checkout: Checkout) -> dict[str, Any]:
    path = checkout.root / REPORT_FILE
    report = _read_json(path)
    path.unlink(missing_ok=True)
    if report:  # a worker finished: background observation works here
        (checkout.root / DEATHS_FILE).unlink(missing_ok=True)
    return report


def _effective_mode(checkout: Checkout, mode: str) -> str:
    if mode != "background":
        return mode
    deaths = _read_json(checkout.root / DEATHS_FILE)
    if (int(deaths.get("count", 0)) >= FAILURE_LIMIT
            and time.time() - float(deaths.get("at", 0)) < RETRY_VOLATILE_SECONDS):
        return "foreground"
    return mode


# -- the foreground -------------------------------------------------------------

def _observe_foreground(unit: Unit, checkout: Checkout) -> tuple[int, str]:
    """Trace the run the agent waits for; return its exit code and the result tail."""
    tracer = _tracer()
    if tracer is None:
        _write_json(checkout.store / UNUSABLE_FILE, {"at": time.time()})
        code = _call(unit.argv, checkout)
        return code, checkout.msg("strace를 쓸 수 없어 기록하지 못함")
    before = _snapshot(checkout.places)
    sys.stdout.flush()
    sys.stderr.flush()
    started_ns = time.time_ns()
    observation = _trace(unit.argv, cwd=checkout.cwd, places=checkout.places, tracer=tracer)
    record = records.build_record(observation, identity=unit.identity, places=checkout.places,
                                  digester=records.Digester(checkout.places),
                                  environment=checkout.environment, started_ns=started_ns,
                                  listings_before=before)
    code = observation.exit_code
    structural = [item for item in record["volatile"] if str(item).startswith(STRUCTURAL_VOLATILITY)]
    if code != 0 and structural:
        # Tracing itself may have failed it (a privileged program, a debugger):
        # the untraced run decides, and tracing is not tried again for a while.
        notes_path = Path(unit.store_dir) / NOTES_FILE
        notes = _read_json(notes_path)
        notes[unit.key] = {"failures": FAILURE_LIMIT, "at": time.time(), "structural": True,
                           "why": _keys(structural)}
        _write_json(notes_path, notes)
        code = _call(unit.argv, checkout)
        return code, checkout.msg("{0} 관찰 실행만 실패함(종료 코드 {1})", _shown(unit.argv), observation.exit_code)
    if code != 0:
        return code, checkout.msg("실패함 — 기록하지 않음")
    if record.get("late_count"):
        return code, checkout.msg("{0} 관찰 중 입력이 바뀌어 기록하지 않음({1})",
                                  _shown(unit.argv), _keys(record["late"]))
    record["observed"] = "foreground"
    records.Store(unit.store_dir).save(unit.key, record)
    return code, checkout.msg("{0} 입력 {1}개 기록", _shown(unit.argv), len(record["inputs"]))


def _after_pass(checkout: Checkout, mode: str, observe: Sequence[Unit], *, seconds: float,
                carried: Mapping[str, Any], ran: set[str], why_not: str,
                validate: Mapping[str, Any] | None = None) -> tuple[str, str]:
    """The result tail and observation line after a passing foreground run."""
    line = _schedule(checkout, mode, observe, seconds=seconds, carried=carried, ran=ran, validate=validate)
    if observe and mode == "background":
        return checkout.msg("통과함 — 다음 확인에 쓰도록 백그라운드에서 관찰하며 다시 실행함"), line
    if observe:
        return checkout.msg("통과함"), line
    if why_not:
        return checkout.msg("통과함 — 기록하지 않음: {0}", why_not), line
    return checkout.msg("통과함"), line


def run_single(argv: Sequence[str], checkout: Checkout, mode: str) -> int:
    unit = _unit(argv, checkout, checkout.root / "units")
    report = _take_report(checkout)
    record = unit.load()
    decision = checkout.decide(unit) if mode != "off" else None
    if decision is not None and decision.skip:
        records.Store(unit.store_dir).touch(unit.key)
        _print(checkout, report, checkout.msg(
            "[Click 결과] 재사용: {0} — 마지막 통과 뒤로 관찰된 입력 {1}개가 모두 그대로라 실행하지 않음",
            _shown(argv), decision.checked))
        return 0
    carried = _stop_worker(checkout)
    report["stopped"] = bool(carried.get("units"))
    mode = _effective_mode(checkout, mode)
    report["fallback"] = mode == "foreground"
    notes = _read_json(Path(unit.store_dir) / NOTES_FILE)
    plan, why_not = _plan(record, notes.get(unit.key), split=False) if mode != "off" else ("none", "")
    if plan == "background" and mode == "foreground":
        plan = "foreground"
    why = _why(checkout, decision) if decision is not None else checkout.msg("관찰 꺼짐")
    line = ""
    if plan == "foreground":
        code, tail = _observe_foreground(unit, checkout)
        line = _schedule(checkout, mode, [], seconds=0, carried=carried, ran={unit.key})
    else:
        started = time.monotonic()
        code = _call(argv, checkout)
        seconds = time.monotonic() - started
        if code == 0 and mode != "off":
            tail, line = _after_pass(checkout, mode, [unit] if plan == "background" else [], seconds=seconds,
                                     carried=carried, ran={unit.key}, why_not=why_not)
        else:
            tail = checkout.msg("실패함 — 기록하지 않음") if code else checkout.msg("통과함")
            if mode != "off":
                line = _schedule(checkout, mode, [], seconds=seconds, carried=carried, ran={unit.key})
    _print(checkout, report, checkout.msg("[Click 결과] 실행: {0} → 종료 코드 {1}; 이유: {2}; {3}",
                                          _shown(argv), code, why, tail), line)
    return code


def run_split(template: Sequence[str], patterns: Sequence[str], checkout: Checkout, mode: str) -> int:
    paths = records.expand_paths(patterns, checkout.cwd)
    if not paths:
        print(checkout.msg("[Click 결과] {0}에 맞는 경로가 {1}에 없음", " ".join(patterns), checkout.cwd))
        return 2
    if mode == "off":
        return run_single(records.substitute(template, paths), checkout, mode)
    template_key = records.unit_identity(template, checkout.cwd, checkout.places, "split")["key"]
    split_dir = checkout.root / "splits" / template_key
    meta = _read_json(split_dir / SPLIT_FILE)
    full = _unit(records.substitute(template, paths), checkout, checkout.root / "units", paths)
    parts = records.plan_groups(paths)
    groups = [_unit(records.substitute(template, part), checkout, split_dir / "groups", part) for part in parts]
    report = _take_report(checkout)
    shown = _shown(template)
    if meta.get("status") != "trusted":
        record = full.load()
        decision = checkout.decide(full)
        if decision.skip:
            records.Store(full.store_dir).touch(full.key)
            _print(checkout, report, checkout.msg(
                "[Click 결과] 재사용: {0} — 마지막 통과 뒤로 관찰된 입력 {1}개가 모두 그대로라 실행하지 않음",
                shown, decision.checked))
            return 0
        carried = _stop_worker(checkout)
        report["stopped"] = bool(carried.get("units"))
        mode = _effective_mode(checkout, mode)
        report["fallback"] = mode == "foreground"
        rejected = meta.get("status") == "rejected"
        state = (checkout.msg("분할 거부됨({0}) — 전체 명령 실행", meta.get("why", "")) if rejected
                 else checkout.msg("분할 확인 전 — 전체 명령 실행"))
        started = time.monotonic()
        code = _call(full.argv, checkout)
        seconds = time.monotonic() - started
        line = ""
        if code == 0:
            notes = _read_json(checkout.root / "units" / NOTES_FILE)
            plan, why_not = _plan(record, notes.get(full.key), split=True)
            observe: list[Unit] = []
            validate = None
            if plan == "background" and rejected and time.time() - float(meta.get("at", 0)) < RETRY_VOLATILE_SECONDS:
                observe = [full]
            elif plan == "background":
                # Groups first: their records outlast the next edit, and the split
                # needs them. The whole command is traced only while what it
                # reads is unknown; its record is not needed once the split holds.
                known = _read_json(split_dir / FULL_READS_FILE).get("key") == full.key
                observe = [*groups, *([] if known else [full])]
                validate = {"meta": str(split_dir / SPLIT_FILE), "full": full.spec(),
                            "full_reads": str(split_dir / FULL_READS_FILE),
                            "groups_store": str(split_dir / "groups"), "groups": [g.key for g in groups]}
            tail, line = _after_pass(checkout, mode, observe, seconds=seconds, carried=carried,
                                     ran={full.key}, why_not=why_not, validate=validate)
        else:
            tail = checkout.msg("실패함 — 기록하지 않음")
            line = _schedule(checkout, mode, [], seconds=seconds, carried=carried, ran={full.key})
        _print(checkout, report, checkout.msg("[Click 결과] 실행: {0} → 종료 코드 {1}; 이유: {2}; {3}",
                                              _shown(full.argv), code,
                                              f"{state}; {_why(checkout, decision)}", tail), line)
        return code

    digester = records.Digester(checkout.places)  # an input every group read is hashed once
    decisions = [checkout.decide(group, digester) for group in groups]
    changed = [(group, decision) for group, decision in zip(groups, decisions) if not decision.skip]
    if not changed:
        for group in groups:
            records.Store(group.store_dir).touch(group.key)
        _print(checkout, report, checkout.msg(
            "[Click 결과] 재사용: {0} — {1}개 그룹({2}개 경로) 모두 마지막 통과 뒤로 관찰된 입력이 그대로라 실행하지 않음",
            shown, len(groups), len(paths)))
        return 0
    carried = _stop_worker(checkout)
    report["stopped"] = bool(carried.get("units"))
    mode = _effective_mode(checkout, mode)
    report["fallback"] = mode == "foreground"
    for group, decision in zip(groups, decisions):
        if decision.skip:
            records.Store(group.store_dir).touch(group.key)
    run_paths = sorted(path for group, _ in changed for path in group.paths)
    why = "; ".join(f"{_keys(group.paths)} ← {_why(checkout, decision)}" for group, decision in changed[:SHOWN_KEYS])
    if len(changed) > SHOWN_KEYS:
        why += "; …"
    started = time.monotonic()
    code = _call(records.substitute(template, run_paths), checkout)
    seconds = time.monotonic() - started
    ran = {group.key for group, _ in changed}
    if code == 0:
        notes = _read_json(split_dir / "groups" / NOTES_FILE)
        observe: list[Unit] = []
        skipped: list[str] = []
        for group, _decision in changed:
            plan, why_not = _plan(group.load(), notes.get(group.key), split=True)
            if plan == "background":
                observe.append(group)
            elif why_not:
                skipped.append(why_not)
        tail, line = _after_pass(checkout, mode, observe, seconds=seconds, carried=carried, ran=ran,
                                 why_not=", ".join(skipped))
    else:
        tail = checkout.msg("실패함 — 기록하지 않음")
        line = _schedule(checkout, mode, [], seconds=seconds, carried=carried, ran=ran)
    _print(checkout, report, checkout.msg(
        "[Click 결과] 실행: {0}개 그룹 중 {1}개({2}/{3}개 경로) → 종료 코드 {4}; 이유: {5}; 나머지 {6}개 그룹은 입력이 그대로라 재사용; {7}",
        len(groups), len(changed), len(run_paths), len(paths), code, why, len(groups) - len(changed), tail), line)
    return code


def _print(checkout: Checkout, report: Mapping[str, Any], result: str, line: str = "") -> None:
    earlier = _describe_report(checkout, report)
    sys.stdout.flush()
    for text in (earlier, line, result):
        if text:
            print(text, flush=True)


# -- entry point ----------------------------------------------------------------

def _stopped(_signal: int, _frame: Any) -> None:
    raise SystemExit(128 + signal.SIGTERM)  # unwinds through observe(), which kills its tree


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="click-observed-check", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="decide, run and schedule observation of one check")
    run.add_argument("--store", type=Path, default=None)
    run.add_argument("--paths", action="append", default=[], metavar="GLOBS",
                     help=f"globs (whitespace-separated, !glob excludes) for {records.PATHS_TOKEN}")
    run.add_argument("argv", nargs=argparse.REMAINDER)
    observe = sub.add_parser("observe", help="(internal) trace the units of a job file")
    observe.add_argument("--job", type=Path, required=True)
    # Lines carry Korean and "→", which a console code page such as cp1252
    # cannot encode; they are written as UTF-8 like the runner's.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    raw = list(sys.argv[1:] if arguments is None else arguments)
    if raw[:1] == ["--encoded-runner"]:
        # A Windows host renders the runner with its arguments encoded.
        decoded, error = click_runner_transport.decode_runner_transport(raw[1] if len(raw) == 2 else "")
        if error or decoded is None:
            print(error or "Click runner transport was malformed.", file=sys.stderr)
            return 2
        raw = decoded
    options = parser.parse_args(raw)
    if options.action == "observe":
        job = _read_json(options.job)
        options.job.unlink(missing_ok=True)
        if not job:
            return 2
        _lower_priority()
        signal.signal(signal.SIGTERM, _stopped)
        report = work(job)
        if not report.get("busy"):
            _write_json(Path(job["root"]) / REPORT_FILE, report)
        return 0
    argv = list(options.argv)
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        parser.error("give the command after `--`")
    patterns = [item for value in options.paths for item in value.split()]
    has_token = any(records.PATHS_TOKEN in item for item in argv)
    if patterns and not has_token:
        parser.error(f"--paths needs {records.PATHS_TOKEN} in the command")
    if has_token and not patterns:
        parser.error(f"the command has {records.PATHS_TOKEN} but no --paths was given")
    environment = dict(os.environ)
    mode = _switch(MODE_VARIABLE, environment)
    mode = "off" if mode in OFF_VALUES else mode if mode in MODES else "background"
    checkout = Checkout.open(options.store or store_root(environment), Path.cwd(), environment)
    if patterns:
        return run_split(argv, patterns, checkout, mode)
    return run_single(argv, checkout, mode)


if __name__ == "__main__":
    raise SystemExit(main())
