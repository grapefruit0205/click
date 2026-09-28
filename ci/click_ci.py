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
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence

if __package__:
    from hooks import click_input_records, click_syscall_trace
else:  # executed as a script from the action directory: the shared engine ships in hooks/
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
    import click_input_records  # type: ignore[no-redef]
    import click_syscall_trace  # type: ignore[no-redef]

click_ci_trace = click_syscall_trace
from_records = click_input_records
build_record = from_records.build_record
(RECORD_VERSION, CONFIG_PATH, MAX_REPORTED_CHANGES, PATHS_TOKEN, AUTO_GROUPS, DEFAULT_IGNORES,
 Places, Digester, Decision, Store, environment_fingerprint, unit_identity, decide, expand_paths,
 substitute, plan_groups, _git) = (
    from_records.RECORD_VERSION, from_records.CONFIG_PATH, from_records.MAX_REPORTED_CHANGES,
    from_records.PATHS_TOKEN, from_records.AUTO_GROUPS, from_records.DEFAULT_IGNORES,
    from_records.Places, from_records.Digester, from_records.Decision, from_records.Store,
    from_records.environment_fingerprint, from_records.unit_identity, from_records.decide,
    from_records.expand_paths, from_records.substitute, from_records.plan_groups, from_records._git)


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
    record = from_records.build_record(observation, identity=identity, places=places,
                                       digester=Digester(places), environment=environment,
                                       run_url=_run_url())
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
