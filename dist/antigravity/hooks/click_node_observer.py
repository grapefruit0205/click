"""Bounded V8 input-call observations from a separate inspector controller.

This detects runtime input use, not an unconditional reason to forbid reuse.
Inspector call coverage alone
does not prove all V8/native/host inputs complete: plain data properties, native
memory and unobserved process paths still require stronger engine integration.
Consequently this module cannot mint an authoritative dependency receipt.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading

if __package__:
    from . import click_process
    from . import click_node_state
else:
    import click_process
    import click_node_state

PROFILE = "linux-node22232-v8-inspector-v1"
VERSION = "v22.23.2"
MAX_COUNT = 200000
CATEGORIES = frozenset({"clock", "random", "shared-memory", "native-escape", "inspector-access"})
VALUE_SOURCES = frozenset({"date-now", "math-random", *("atomics-" + name for name in (
    "add", "and", "compareExchange", "exchange", "load", "notify", "or", "store", "sub", "wait", "xor"))})
REASONS = frozenset({
    "not-requested",
    "unsupported-runtime", "explicit-node-options", "observer-preparation-failed",
    "observer-result-unavailable", "observer-result-invalid", "observer-source-changed",
    "event-limit", "session-limit", "worker-start-unobserved", "worker-transport-lost",
    "worker-setup-failed", "invalid-worker-message", "worker-completion-unobserved",
    "primitive-unavailable", "context-setup-failed", "session-setup-incomplete",
    "invalid-endpoint", "duplicate-endpoint", "invalid-protocol-message",
    "session-disconnected", "transport-failed", "session-setup-failed",
    "endpoint-limit", "endpoint-channel-failed", "endpoint-truncated",
    "no-runtime-session", "session-completion-unobserved", "process-tree-incomplete",
    "unobserved-process", "engine-input-coverage-incomplete",
    "context-start-unobserved",
    "input-value-limit", "input-value-setup-incomplete", "input-values-unavailable", "input-source-replaced",
    "input-exception-state-incomplete", "input-coercion-state-incomplete", "input-native-state-unavailable",
    "input-random-state-discontinuity",
    *CATEGORIES,
})
SOURCES = ("click_node_observer.py", "click_node_controller.cjs", "click_node_bootstrap.mjs", "click_node_value_probe.js", "click_node_state.py", "click_node_state.cc")
FIELDS = frozenset({"version", "profile", "status", "counts", "sessions", "contexts",
                    "installed", "completed", "workers", "capture_complete", "inputs_complete",
                    "reuse_authorized", "reasons", "runtime_digest", "collector_digest", "values", "state_companion_digest"})


def digest_file(path: Path) -> str:
    return click_node_state.digest(path)


def source_digest() -> str:
    return hashlib.sha256(json.dumps([(name, digest_file(Path(__file__).with_name(name)))
                                      for name in SOURCES], separators=(",", ":")).encode()).hexdigest()


def empty(reason: str) -> dict:
    return {"version": 2, "profile": PROFILE, "status": "unavailable",
            "counts": {name: 0 for name in sorted(CATEGORIES)},
            "values": {name: {"count": 0, "digest": "", "last_value_digest": "", "state_count": 0, "last_state_digest": ""} for name in sorted(VALUE_SOURCES)},
            "sessions": 0, "contexts": 0, "installed": 0, "completed": 0, "workers": 0,
            "capture_complete": False, "inputs_complete": False, "reuse_authorized": False,
            "reasons": sorted({reason, "engine-input-coverage-incomplete"}),
            "runtime_digest": "", "collector_digest": "", "state_companion_digest": ""}


def valid(value) -> bool:
    if not isinstance(value, dict) or set(value) != FIELDS:
        return False
    if (type(value["version"]) is not int or value["version"] != 2 or value["profile"] != PROFILE or
            value["status"] not in {"observed", "partial", "unavailable"} or
            value["inputs_complete"] is not False or value["reuse_authorized"] is not False or
            not isinstance(value["capture_complete"], bool)):
        return False
    if not isinstance(value["counts"], dict) or set(value["counts"]) != CATEGORIES:
        return False
    if not isinstance(value["values"], dict) or set(value["values"]) != VALUE_SOURCES:
        return False
    for row in value["values"].values():
        if (not isinstance(row, dict) or set(row) != {"count", "digest", "last_value_digest", "state_count", "last_state_digest"}
                or type(row["count"]) is not int or not 0 <= row["count"] <= MAX_COUNT
                or type(row["state_count"]) is not int or not 0 <= row["state_count"] <= row["count"]
                or not isinstance(row["last_state_digest"], str)
                or not re.fullmatch(r"[0-9a-f]{64}" if row["state_count"] else r"", row["last_state_digest"])
                or any(not isinstance(row[key], str) or not re.fullmatch(r"[0-9a-f]{64}" if row["count"] else r"", row[key])
                       for key in ("digest", "last_value_digest"))):
            return False
    if sum(row["count"] for row in value["values"].values()) > MAX_COUNT:
        return False
    if any(type(count) is not int or not 0 <= count <= MAX_COUNT
           for count in [*value["counts"].values(), *(value[key] for key in ("sessions", "contexts", "installed", "completed", "workers"))]):
        return False
    if not isinstance(value["reasons"], list) or any(type(reason) is not str for reason in value["reasons"]):
        return False
    if value["reasons"] != sorted(set(value["reasons"])) or not set(value["reasons"]) <= REASONS:
        return False
    if "engine-input-coverage-incomplete" not in value["reasons"]:
        return False
    if any(not isinstance(value[key], str) or not re.fullmatch(r"(?:[0-9a-f]{64})?", value[key])
           for key in ("runtime_digest", "collector_digest", "state_companion_digest")):
        return False
    if value["capture_complete"]:
        if (value["status"] != "observed" or not value["sessions"] or
                value["completed"] != value["sessions"] or value["contexts"] != value["installed"] or
                set(value["reasons"]) - CATEGORIES - {"engine-input-coverage-incomplete"}):
            return False
    return True


class Collector:
    def __init__(self, argv, environment: dict, workspace: Path):
        self.environment = dict(environment)
        self.record = empty("unsupported-runtime")
        self.child = None
        self.location = None
        self.descriptor = None
        self.messages = queue.Queue(maxsize=8)
        self.process_ids = set()
        self.runtime_path = None
        self.state_companion = None
        if sys.platform != "linux" or not argv:
            return
        # Node's test runner reduces concurrency when an inspector is active.
        # Preserve that command's scheduler and retain its ordinary OS capture.
        if any(arg == "--test" or str(arg).startswith("--test=") for arg in argv[1:]):
            return
        # Keep existing inspector/preload/loader authority and semantics intact.
        if environment.get("NODE_OPTIONS") or environment.get("CLICK_NODE_OBSERVER_DIRECTORY") or any(str(arg).startswith(("--inspect", "--require", "--import", "--loader", "--experimental-loader", "-r")) for arg in argv[1:]):
            self.record = empty("explicit-node-options")
            return
        try:
            node = Path(argv[0]) if Path(argv[0]).name in {"node", "node.exe"} else Path(shutil.which("node", path=environment.get("PATH")) or "")
            node = node.resolve(strict=True)
            root = workspace.resolve()
            if root == node or root in node.parents:
                return
            probe = subprocess.run([str(node), "--version"], env=environment, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, timeout=3, check=False)
            if probe.returncode or probe.stdout.strip().decode("ascii") != VERSION:
                return
            self.runtime_path = node
            self.runtime_digest = digest_file(node)
            self.collector_digest = source_digest()
            self.state_companion = click_node_state.prepare(node, root, environment)
            self.location = tempfile.TemporaryDirectory(prefix="click-node-inputs-")
            directory = Path(self.location.name)
            os.chmod(directory, 0o700)
            fifo = directory / "endpoints.pipe"
            os.mkfifo(fifo, 0o600)
            # Keep a reader and writer open across short-lived forked processes.
            self.descriptor = os.open(fifo, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
            child_environment = {key: value for key, value in environment.items()
                                 if key not in {"NODE_OPTIONS", "CLICK_NODE_OBSERVER_DIRECTORY"}}
            self.child = click_process.spawn_argv([str(node), str(Path(__file__).with_name("click_node_controller.cjs")), str(directory),
                                                   str(self.state_companion[0]) if self.state_companion else ""],
                                                  cwd=directory, env=child_environment, stdin=subprocess.PIPE,
                                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.reader.start()
            if self.messages.get(timeout=5) != {"kind": "ready"}:
                raise ValueError("observer not ready")
            self.environment["CLICK_NODE_OBSERVER_DIRECTORY"] = str(directory)
            # Even --inspect-publish-uid enables Node's inspector scheduler
            # branch before preloads run. Inherited by a hidden `node --test`,
            # it forces concurrency=1 despite the bootstrap's scheduler guard.
            self.environment["NODE_OPTIONS"] = "--import=" + Path(__file__).with_name("click_node_bootstrap.mjs").resolve().as_uri()
            self.record = empty("observer-result-unavailable")
        except (OSError, ValueError, subprocess.SubprocessError, queue.Empty):
            self.record = empty("observer-preparation-failed")
            self.close()
            self.environment = dict(environment)

    def _read(self):
        stream = self.child.stdout
        try:
            while line := stream.readline(65537):
                if len(line) > 65536:
                    break
                self.messages.put_nowait(json.loads(line))
        except (OSError, ValueError, queue.Full):
            pass

    def finish(self, tree=None) -> dict:
        if self.child is None:
            return self.record
        try:
            self.child.stdin.write(b"stop\n")
            self.child.stdin.flush()
            value = self.messages.get(timeout=8)
            if not isinstance(value, dict) or value.pop("kind", None) != "result":
                raise ValueError("invalid result")
            if set(value) != {"counts", "sessions", "contexts", "installed", "completed", "workers", "process_ids", "reasons", "values"}:
                raise ValueError("invalid fields")
            self.process_ids = set(value.pop("process_ids"))
            if any(type(pid) is not int or pid <= 0 for pid in self.process_ids):
                raise ValueError("invalid process identity")
            reasons = set(value["reasons"])
            if tree is None or not tree.complete:
                reasons.add("process-tree-incomplete")
            elif self.process_ids != set(tree.process_ids):
                reasons.add("unobserved-process")
            complete = not reasons
            reasons.update(name for name, count in value["counts"].items() if count)
            reasons.add("engine-input-coverage-incomplete")
            value.update(version=2, profile=PROFILE, status="observed" if complete else "partial",
                         capture_complete=complete, inputs_complete=False, reuse_authorized=False,
                         reasons=sorted(reasons), runtime_digest=self.runtime_digest, collector_digest=self.collector_digest,
                         state_companion_digest=self.state_companion[1] if self.state_companion else "")
            if (digest_file(self.runtime_path) != self.runtime_digest or source_digest() != self.collector_digest
                    or self.state_companion and digest_file(self.state_companion[0]) != self.state_companion[1]):
                value.update(status="partial", capture_complete=False, reasons=sorted(reasons | {"observer-source-changed"}))
            if not valid(value):
                raise ValueError("invalid runtime observation")
            self.record = value
        except (OSError, ValueError, TypeError, queue.Empty):
            self.record = empty("observer-result-invalid")
        finally:
            self.close()
        return self.record

    def close(self):
        if self.child is not None:
            try:
                try:
                    self.child.stdin.close()
                except OSError:
                    pass
                if self.child.poll() is None:
                    self.child.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                click_process.terminate_process_group(self.child, grace_seconds=0.2)
            self.child.stdout.close()
            self.child = None
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None
        if self.location is not None:
            self.location.cleanup()
            self.location = None
