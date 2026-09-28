"""Observed checks: any command, reused by its observed inputs, observed in the background."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from hooks import click_input_records as records
from hooks import click_lifecycle
from hooks import click_observed_check as observed
from hooks import click_runner_transport
from hooks import click_syscall_trace
from tests.click_gate_test_support import CLICK_GATE, ClickGateTestCase, split_runner_command

ENGINE = Path(observed.__file__).resolve()
STRACE = sys.platform.startswith("linux") and click_syscall_trace.strace_available() is not None
# Where Node commands are observed: strace on Linux, the Node observer on Windows.
NODE_OBSERVED = bool(shutil.which("node")) and (STRACE or os.name == "nt")


def _runner_arguments(command: str) -> list[str]:
    """The argv a rendered runner command starts, with a Windows transport decoded."""
    argv = split_runner_command(command)
    if "--encoded-runner" in argv:
        index = argv.index("--encoded-runner")
        decoded, error = click_runner_transport.decode_runner_transport(argv[index + 1])
        assert decoded is not None, error
        argv = [*argv[:index], *decoded]
    return argv


def _node_runs_typescript() -> bool:
    """Node 22.18+ runs .ts files by stripping their types."""
    if not shutil.which("node"):
        return False
    completed = subprocess.run(["node", "-p", "process.features.typescript"], capture_output=True,
                               text=True, check=False)
    return completed.stdout.strip() in {"strip", "transform"}


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)


class _Fixture(unittest.TestCase):
    project_name = "project"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.project = base / self.project_name
        self.project.mkdir()
        self.store = base / "store"
        _git_init(self.project)
        self.environment = {**os.environ, "CLICK_LANGUAGE": "en", "CLICK_OBSERVATION": "inline"}
        self.environment.pop("PYTHONPATH", None)

    def write(self, name: str, text: str) -> Path:
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def check(self, *argv: str, paths: str = "", mode: str = "inline",
              environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(ENGINE), "run", "--store", str(self.store)]
        if paths:
            command += ["--paths", paths]
        return subprocess.run([*command, "--", *argv], cwd=self.project, capture_output=True, text=True,
                              env={**self.environment, "CLICK_OBSERVATION": mode, **(environment or {})},
                              timeout=300, check=False)

    def result(self, completed: subprocess.CompletedProcess[str]) -> str:
        lines = [line for line in completed.stdout.splitlines() if line.startswith("[Click result]")]
        self.assertEqual(len(lines), 1, completed.stdout + completed.stderr)
        return lines[0]

    def checkout_root(self) -> Path:
        (root,) = [path for path in self.store.iterdir() if path.is_dir()]
        return root

    def wait_for(self, condition, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                self.fail("timed out waiting for the background worker")
            time.sleep(0.05)

    def wait_for_worker(self) -> dict:
        root = lambda: next((p for p in self.store.iterdir() if p.is_dir()), None) if self.store.exists() else None
        self.wait_for(lambda: root() is not None and (root() / observed.REPORT_FILE).exists())
        return json.loads((root() / observed.REPORT_FILE).read_text(encoding="utf-8"))


# -- parsing and routing ---------------------------------------------------------

class VerifyRequestTests(unittest.TestCase):
    def test_argv_and_paths_forms(self) -> None:
        parse = click_lifecycle.verify_argv_request
        self.assertEqual(parse("click-gate verify -- pytest -q"), (["pytest", "-q"], []))
        self.assertEqual(parse("click-gate verify --paths 'a/*.py !a/x.py' --paths b -- pytest {paths}"),
                         (["pytest", "{paths}"], ["a/*.py", "!a/x.py", "b"]))
        for command in ("click-gate verify --", "click-gate verify '{}'", "click-gate verify --paths x pytest",
                        "pytest -q", "click-gate verify --paths", "click-gate verify --paths x",
                        "click-gate verify --paths x --"):
            with self.subTest(command=command):
                self.assertIsNone(parse(command))

    def test_paths_form_needs_the_token(self) -> None:
        action, _value, error = click_lifecycle.control_request("click-gate verify --paths 'a/*' -- pytest -q")
        self.assertEqual(action, "")
        self.assertIn("{paths}", error)
        action, value, error = click_lifecycle.control_request("click-gate verify --paths 'a/*' -- pytest {paths}")
        self.assertEqual((action, error), ("verify", ""))
        self.assertEqual(json.loads(value)["checks"][0]["argv"], ["pytest", "{paths}"])

    def test_shell_text_runs_through_bash_with_pipefail(self) -> None:
        def run(command: str) -> list[str]:
            request = click_lifecycle.verify_argv_request(command)
            assert request is not None
            return observed.check_argv(command, request[0])

        self.assertEqual(run("click-gate verify -- python3 -m pytest -q 'tests/a b.py'"),
                         ["python3", "-m", "pytest", "-q", "tests/a b.py"])
        self.assertEqual(run("click-gate verify --paths 'x/*' -- pytest {paths}"), ["pytest", "{paths}"])
        for text in ("pytest -q 2>&1 | tail -5", "cd sub && make test", "pytest tests/test_*.py",
                     "FOO=1 pytest", "pytest $ARGS"):
            with self.subTest(text=text):
                self.assertEqual(run(f"click-gate verify -- {text}"), ["bash", "-o", "pipefail", "-c", text])
        self.assertEqual(run("click-gate verify --paths 'a/*.py b' -- pytest {paths} | tail -3"),
                         ["bash", "-o", "pipefail", "-c", "pytest {paths} | tail -3"])
        self.assertEqual(observed._shown(["bash", "-o", "pipefail", "-c", "pytest | tail"]), "pytest | tail")

    def test_runner_command_names_the_store_and_globs(self) -> None:
        environment = {"PLUGIN_DATA": "/data"}
        argv = _runner_arguments(observed.runner_command(["pytest", "{paths}"], ["tests/*.py"], environment))
        start = argv.index(str(ENGINE))
        self.assertEqual(argv[start:], [str(ENGINE), "run", "--store", str(observed.store_root(environment)),
                                        "--paths", "tests/*.py", "--", "pytest", "{paths}"])

    def test_an_encoded_runner_reaches_the_check_it_names(self) -> None:
        encoded = click_runner_transport.encode_runner_transport(["run", "--", "node", "--test"])
        with mock.patch.object(observed, "run_single", return_value=0) as run:
            self.assertEqual(observed.main(["--encoded-runner", encoded]), 0)
        self.assertEqual(run.call_args.args[0], ["node", "--test"])
        self.assertEqual(observed.main(["--encoded-runner", "not base64!"]), 2)

    def test_windows_observes_only_commands_that_run_node(self) -> None:
        environment = {"PLUGIN_DATA": tempfile.mkdtemp(), "PATH": os.environ.get("PATH", "")}
        self.addCleanup(shutil.rmtree, environment["PLUGIN_DATA"], True)
        if os.name != "nt" or not shutil.which("node"):
            self.skipTest("routing by program is Windows'")
        self.assertTrue(observed.available(environment, ["npx", "vitest", "run"]))
        self.assertTrue(observed.available(environment, [*observed.SHELL_PREFIX, "npm test 2>&1 | tail -5"]))
        self.assertFalse(observed.available(environment, [sys.executable, "-m", "unittest"]))
        self.assertFalse(observed.available(environment))

    def test_route_can_be_switched_off_and_remembers_an_unusable_strace(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            environment = {"PLUGIN_DATA": data, "PATH": os.environ.get("PATH", "")}
            self.assertFalse(observed.available({**environment, observed.ROUTE_VARIABLE: "off"}))
            if not (sys.platform.startswith("linux") and shutil.which("strace")):
                return
            self.assertTrue(observed.available(environment))
            observed._write_json(observed.store_root(environment) / observed.UNUSABLE_FILE, {"at": time.time()})
            self.assertFalse(observed.available(environment))
            observed._write_json(observed.store_root(environment) / observed.UNUSABLE_FILE,
                                 {"at": time.time() - observed.RETRY_VOLATILE_SECONDS - 1})
            self.assertTrue(observed.available(environment))


class GateRoutingTests(ClickGateTestCase):
    # The gate under test loads its own copy of the engine module.
    engine = CLICK_GATE.click_observed_check

    def test_evidence_verify_goes_to_the_observed_check_where_strace_is(self) -> None:
        self.prompt_submit("run the tests", "turn-1")
        with mock.patch.object(self.engine, "available", return_value=True):
            payload = self.pre_tool_in_process("click-gate verify -- python3 -m unittest -q verification_fixture")
        argv = _runner_arguments(payload["hookSpecificOutput"]["updatedInput"]["command"])
        start = argv.index(str(Path(self.engine.__file__).resolve()))
        self.assertEqual(argv[start + 1:start + 4], ["run", "--store", str(self.plugin_data / "observed-checks")])
        self.assertEqual(argv[start + 4:], ["--", "python3", "-m", "unittest", "-q", "verification_fixture"])

    def test_guarded_or_unobservable_hosts_keep_the_receipt_runner(self) -> None:
        self.prompt_submit("run the tests", "turn-1")
        with mock.patch.object(self.engine, "available", return_value=False):
            payload = self.pre_tool_in_process("click-gate verify -- python3 -m unittest -q verification_fixture")
        self.assertIn("run-verification", split_runner_command(payload["hookSpecificOutput"]["updatedInput"]["command"]))

    def test_without_observation_verify_keeps_the_runtime_profiles_and_expands_paths(self) -> None:
        (self.workspace / "tests").mkdir()
        for name in ("test_a.py", "test_b.py"):
            (self.workspace / "tests" / name).write_text("", encoding="utf-8")
        self.prompt_submit("run the tests", "turn-1")
        with mock.patch.object(self.engine, "available", return_value=False):
            payload = self.pre_tool_in_process(
                "click-gate verify --paths 'tests/test_*.py' -- python3 -m unittest {paths}")
        argv = split_runner_command(payload["hookSpecificOutput"]["updatedInput"]["command"])
        self.assertIn("run-verification", argv)
        batch = json.loads(self.decode_batch(argv[-1]))
        self.assertEqual(batch["checks"][0]["argv"],
                         ["python3", "-m", "unittest", "tests/test_a.py", "tests/test_b.py"])

    def pre_tool_in_process(self, command: str) -> dict:
        self.hook_in_process = True
        payload = self.pre_tool("Bash", command, "turn-1", submit_prompt=False)
        assert payload is not None
        self.assertEqual(payload["hookSpecificOutput"].get("permissionDecision"), "allow", payload)
        return payload

    def decode_batch(self, encoded: str) -> str:
        decoded, error = CLICK_GATE.click_capability.decode_encoded_request(encoded, "batch")
        self.assertEqual(error, "")
        return decoded


# -- policy pieces -------------------------------------------------------------------

def _record(volatile=(), age: float = 0) -> dict:
    moment = time.time() - age
    import datetime
    at = datetime.datetime.fromtimestamp(moment, datetime.timezone.utc).isoformat(timespec="seconds")
    return {"volatile": list(volatile), "recorded": {"at": at}}


class PlanTests(unittest.TestCase):
    def test_background_unless_the_command_reached_out_or_keeps_failing(self) -> None:
        plan = observed._plan
        self.assertEqual(plan(None, None, split=False), ("background", ""))
        self.assertEqual(plan(_record(), None, split=True), ("background", ""))
        network = _record(["network:192.0.2.1"])
        self.assertEqual(plan(network, None, split=False), ("none", "network:192.0.2.1"))
        old_network = _record(["network:192.0.2.1"], age=observed.RETRY_STRUCTURAL_SECONDS + 60)
        self.assertEqual(plan(old_network, None, split=False)[0], "foreground")
        self.assertEqual(plan(old_network, None, split=True)[0], "none")
        self.assertEqual(plan(_record(["changed-own-input:repo:x"]), None, split=False)[0], "none")
        self.assertEqual(plan(_record(["changed-own-input:repo:x"], age=observed.RETRY_VOLATILE_SECONDS + 60),
                              None, split=False)[0], "background")
        failing = {"failures": observed.FAILURE_LIMIT, "at": time.time(), "why": "exit 1 under observation"}
        self.assertEqual(plan(_record(), failing, split=False), ("none", "exit 1 under observation"))
        self.assertEqual(plan(_record(), {**failing, "failures": 1}, split=False)[0], "background")

    def test_carried_units_follow_new_ones_unless_the_run_superseded_them(self) -> None:
        unit = lambda key: {"argv": [key], "identity": {"key": key}, "store": "/s"}
        job = observed._merge({"units": [unit("a")], "validate": None},
                              {"units": [unit("a"), unit("b"), unit("c")], "validate": {"meta": "m"}}, {"c"})
        self.assertEqual([spec["identity"]["key"] for spec in job["units"]], ["a", "b"])
        self.assertTrue(job["units"][1]["carried"])
        self.assertEqual(job["validate"], {"meta": "m"})


class PruneTests(unittest.TestCase):
    def test_unused_records_and_emptied_checkouts_go_at_most_once_a_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            old = records.Store(store / "a" / "units")
            old.save("k", {"x": 1})
            fresh = records.Store(store / "b" / "splits" / "t" / "groups")
            fresh.save("k", {"x": 1})
            past = time.time() - observed.RECORD_TTL_SECONDS - 10
            os.utime(old.root / "k.json.gz", (past, past))
            observed._prune(store)
            self.assertFalse((store / "a").exists())
            self.assertTrue((fresh.root / "k.json.gz").exists())
            os.utime(fresh.root / "k.json.gz", (past, past))
            observed._prune(store)
            self.assertTrue((fresh.root / "k.json.gz").exists())
            fresh.touch("k")
            self.assertGreater((fresh.root / "k.json.gz").stat().st_mtime, past + 60)


class RecordFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "repo"
        (self.root / "dir").mkdir(parents=True)
        (self.root / "dir" / "a.txt").write_text("a", encoding="utf-8")
        self.places = records.Places(repo=str(self.root), home="/nonexistent-home", volatile_roots=(),
                                     ignores=records.DEFAULT_IGNORES)

    def build(self, started_ns: int, listings_before=None, produced: str = "") -> dict:
        paths = {
            str(self.root / "dir" / "a.txt"): click_syscall_trace.PathState("input", {"read"}),
            str(self.root / "dir"): click_syscall_trace.PathState("input", {"enumerate"}),
        }
        if produced:
            paths[str(self.root / "dir" / produced)] = click_syscall_trace.PathState("produced", {"write"})
        observation = click_syscall_trace.Observation(0, paths, [], 0, 1, 0.1)
        return records.build_record(observation, identity={"key": "k"}, places=self.places,
                                    digester=records.Digester(self.places), environment={},
                                    started_ns=started_ns, listings_before=listings_before)

    def test_inputs_changed_while_the_command_ran_are_late(self) -> None:
        self.assertEqual(self.build(time.time_ns() + 10**9)["late"], [])
        self.assertEqual(self.build(time.time_ns() - 10**9)["late"], ["repo:dir", "repo:dir/a.txt"])

    def test_a_listing_the_command_changed_itself_is_judged_against_the_listing_before(self) -> None:
        time.sleep(0.05)
        started = time.time_ns()
        before = {"repo:dir": records.Digester(self.places).listing(str(self.root / "dir"))}
        time.sleep(0.02)
        (self.root / "dir" / "out.txt").write_text("", encoding="utf-8")  # produced by the command
        record = self.build(started, before, produced="out.txt")
        self.assertEqual(record["late"], [])
        (self.root / "dir" / "new.txt").write_text("", encoding="utf-8")  # added by someone else
        record = self.build(started, before, produced="out.txt")
        self.assertIn("repo:dir", record["late"])

    def test_state_the_command_rewrites_outside_the_checkout_is_neither_input_nor_volatile(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            counter = Path(home) / "counter"
            counter.write_text("1", encoding="utf-8")
            places = records.Places(repo=str(self.root), home=home, volatile_roots=(),
                                    ignores=records.DEFAULT_IGNORES)
            observation = click_syscall_trace.Observation(0, {
                str(counter): click_syscall_trace.PathState("input", {"read"}, modified_after_input=True),
                str(self.root / "dir" / "a.txt"): click_syscall_trace.PathState(
                    "input", {"read"}, modified_after_input=True),
            }, [], 0, 1, 0.1)
            record = records.build_record(observation, identity={"key": "k"}, places=places,
                                          digester=records.Digester(places), environment={})
            self.assertEqual(record["inputs"], {})
            self.assertEqual(record["volatile"], ["changed-own-input:repo:dir/a.txt"])

    def test_a_git_ignored_cache_the_command_rewrites_is_its_own_state(self) -> None:
        # Vitest reads its results cache and writes it back; a tracked file it
        # rewrites (a snapshot) still makes the run volatile.
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("cache/\n", encoding="utf-8")
        (self.root / "cache").mkdir()
        (self.root / "cache" / "results.json").write_text("{}", encoding="utf-8")
        observation = click_syscall_trace.Observation(0, {
            str(self.root / "cache" / "results.json"): click_syscall_trace.PathState(
                "input", {"read"}, modified_after_input=True),
            str(self.root / "dir" / "a.txt"): click_syscall_trace.PathState(
                "input", {"read"}, modified_after_input=True),
        }, [], 0, 1, 0.1)
        record = records.build_record(observation, identity={"key": "k"}, places=self.places,
                                      digester=records.Digester(self.places), environment={})
        self.assertEqual(record["inputs"], {})
        self.assertEqual(record["volatile"], ["changed-own-input:repo:dir/a.txt"])

    @unittest.skipUnless(hasattr(os, "getxattr"), "reads file capabilities")
    def test_a_privileged_program_makes_the_record_volatile(self) -> None:
        program = self.root / "suid"
        program.write_text("", encoding="utf-8")
        program.chmod(0o4755)
        observation = click_syscall_trace.Observation(
            0, {str(program): click_syscall_trace.PathState("input", {"execute"})}, [], 0, 1, 0.1)
        record = records.build_record(observation, identity={"key": "k"}, places=self.places,
                                      digester=records.Digester(self.places), environment={})
        self.assertEqual(record["volatile"], ["privileged-exec:repo:suid"])

    def test_stat_signatures_skip_hashing_only_while_they_match(self) -> None:
        path = self.root / "dir" / "a.txt"
        record = {"version": records.RECORD_VERSION, "exit_code": 0, "volatile": [],
                  "environment": records.environment_fingerprint({}, self.places),
                  "inputs": {"repo:dir/a.txt": "f:" + "0" * 64},
                  "stats": {"repo:dir/a.txt": records.stat_signature(str(path))}}
        decision = records.decide(record, places=self.places, digester=records.Digester(self.places),
                                  environment={}, use_stats=True)
        self.assertTrue(decision.skip)  # the wrong digest is never compared
        time.sleep(0.01)
        path.write_text("b", encoding="utf-8")
        decision = records.decide(record, places=self.places, digester=records.Digester(self.places),
                                  environment={}, use_stats=True)
        self.assertEqual(decision.reason, "inputs-changed")

    def test_decisions_sharing_a_digester_check_each_input_value_once(self) -> None:
        digester = records.Digester(self.places)
        current = digester.digest("repo:dir/a.txt", ("read",))

        def record(value: str) -> dict:
            return {"version": records.RECORD_VERSION, "exit_code": 0, "volatile": [],
                    "environment": records.environment_fingerprint({}, self.places),
                    "inputs": {"repo:dir/a.txt": value, "repo:gone.txt": "missing"}}

        with mock.patch.object(records.Digester, "digest", wraps=digester.digest) as digest:
            decide = lambda value: records.decide(record(value), places=self.places, digester=digester,
                                                  environment={})
            self.assertTrue(decide(current).skip)
            self.assertEqual(decide("f:" + "0" * 64).changed, ["repo:dir/a.txt"])
            self.assertTrue(decide(current).skip)
        # Asked once per recorded value; the third decision reads nothing.
        self.assertEqual(digest.call_count, 2)


class SplitVerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        for name in ("a", "b", "all.txt"):
            (self.repo / name).write_text(name, encoding="utf-8")
        self.places = records.Places(repo=str(self.repo), home=str(self.base / "home"),
                                     volatile_roots=(), ignores=())
        self.units = records.Store(self.base / "units")
        self.groups = records.Store(self.base / "groups")
        self.spec = {"meta": str(self.base / "split.json"),
                     "full": {"argv": [], "identity": {"key": "full"}, "store": str(self.units.root)},
                     "full_reads": str(self.base / observed.FULL_READS_FILE),
                     "groups_store": str(self.groups.root), "groups": ["g1", "g2"]}

    def save(self, store: records.Store, key: str, *inputs: str) -> None:
        store.save(key, {"exit_code": 0, "inputs": {name: "f:x" for name in inputs}})

    def validate(self, outcomes: dict[str, str], reads: dict[str, list[str]] | None = None) -> dict | None:
        return observed._validate(self.spec, outcomes, reads or {}, self.places)

    def test_trusted_when_the_groups_read_everything_the_whole_command_read(self) -> None:
        self.save(self.units, "full", "repo:a", "repo:b", "abs:/usr/lib/x")
        self.save(self.groups, "g1", "repo:a")
        self.save(self.groups, "g2", "repo:b")
        verdict = self.validate({"full": "recorded", "g1": "recorded", "g2": "current"})
        self.assertEqual(verdict["status"], "trusted")

    def test_rejected_when_only_the_whole_command_read_an_input(self) -> None:
        self.save(self.units, "full", "repo:a", "repo:b", "repo:all.txt")
        self.save(self.groups, "g1", "repo:a")
        self.save(self.groups, "g2", "repo:b")
        verdict = self.validate({"full": "recorded", "g1": "recorded", "g2": "recorded"})
        self.assertEqual(verdict, {**verdict, "status": "rejected", "why": "only the whole command read all.txt"})

    def test_a_group_failing_alone_rejects_and_an_unseen_run_defers(self) -> None:
        self.assertEqual(self.validate({"g1": "failed"})["status"], "rejected")
        self.assertIsNone(self.validate({"g1": "inconclusive", "g2": "recorded"}, {"g2": ["repo:b"]}))
        self.save(self.groups, "g1", "repo:a")
        self.save(self.groups, "g2", "repo:b")
        self.assertIsNone(self.validate({"g1": "current", "g2": "current"}))  # the whole command unseen

    def test_runs_dropped_for_a_moved_input_still_show_what_they_read(self) -> None:
        # An edit during every traced run: no record is kept, yet the split is judged.
        verdict = self.validate({"g1": "dropped", "g2": "dropped", "full": "dropped"},
                                {"g1": ["repo:a"], "g2": ["repo:b"], "full": ["repo:a", "repo:b"]})
        self.assertEqual(verdict["status"], "trusted")

    def test_the_whole_command_reads_kept_from_an_earlier_job_count(self) -> None:
        observed._write_json(Path(self.spec["full_reads"]), {"key": "full", "reads": ["repo:a", "repo:all.txt"]})
        verdict = self.validate({"g1": "recorded", "g2": "recorded"}, {"g1": ["repo:a"], "g2": ["repo:b"]})
        self.assertEqual(verdict["why"], "only the whole command read all.txt")
        observed._write_json(Path(self.spec["full_reads"]), {"key": "other", "reads": []})
        self.assertIsNone(self.validate({"g1": "recorded", "g2": "recorded"}, {"g1": ["repo:a"], "g2": ["repo:b"]}))

    def test_ignored_or_deleted_files_only_the_whole_command_read_do_not_reject(self) -> None:
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / ".gitignore").write_text("node_modules\n", encoding="utf-8")
        (self.repo / "node_modules").mkdir()
        (self.repo / "node_modules" / "pool.js").write_text("", encoding="utf-8")
        verdict = self.validate({"g1": "recorded", "g2": "recorded", "full": "recorded"}, {
            "g1": ["repo:a"], "g2": ["repo:b"],
            "full": ["repo:a", "repo:b", "repo:node_modules/pool.js", "repo:removed.ts"]})
        self.assertEqual(verdict["status"], "trusted")


class StopWorkerTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux"), "reads /proc")
    def test_a_running_worker_is_stopped_and_its_pending_units_returned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                                       "click_observed_check"], start_new_session=True)
            self.addCleanup(worker.wait)
            self.addCleanup(worker.kill)
            pending = {"units": [{"argv": ["x"], "identity": {"key": "k"}, "store": "/s"}], "validate": None}
            observed._write_json(root / observed.WORKER_FILE, {"pid": worker.pid, "group": 0, "pending": pending})
            checkout = observed.Checkout(root, None, root, root, {}, "en")
            self.assertEqual(observed._stop_worker(checkout), pending)
            self.assertEqual(worker.wait(timeout=5), -15)
            self.assertFalse((root / observed.WORKER_FILE).exists())
            self.assertEqual(observed._stop_worker(checkout), {})
            stubborn = subprocess.Popen([sys.executable, "-c", "import signal, time; "
                                         "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
                                         "click_observed_check"], start_new_session=True)
            self.addCleanup(stubborn.wait)
            self.addCleanup(stubborn.kill)
            time.sleep(0.3)  # let it ignore SIGTERM first
            observed._write_json(root / observed.WORKER_FILE, {"pid": stubborn.pid, "group": 0})
            observed._stop_worker(checkout)
            self.assertEqual(stubborn.wait(timeout=5), -9)

    @unittest.skipUnless(sys.platform.startswith("linux"), "reads /proc")
    def test_an_unrelated_process_with_a_recycled_pid_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            self.addCleanup(other.wait)
            self.addCleanup(other.kill)
            observed._write_json(root / observed.WORKER_FILE, {"pid": other.pid, "group": other.pid})
            observed._stop_worker(observed.Checkout(root, None, root, root, {}, "en"))
            self.assertIsNone(other.poll())


# -- real commands under strace -------------------------------------------------------

@unittest.skipUnless(STRACE, "needs Linux with a working strace")
class PythonCheckTests(_Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.write("src/__init__.py", "")
        self.write("src/calc.py", "def add(a, b):\n    return a + b\n")
        self.write("src/text.py", "def shout(s):\n    return s.upper()\n")
        self.write("tests/__init__.py", "")
        self.write("tests/test_calc.py", "import unittest\nfrom src.calc import add\n"
                   "class T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
        self.write("tests/test_text.py", "import unittest\nfrom src.text import shout\n"
                   "class T(unittest.TestCase):\n    def test_shout(self):\n        self.assertEqual(shout('a'), 'A')\n")
        # Discovery stats every entry of the directories it walks, so an
        # unrelated file only stays unrelated outside them.
        self.write("docs/notes.md", "unrelated\n")

    def test_whole_command_is_reused_until_an_observed_input_changes(self) -> None:
        argv = (sys.executable, "-m", "unittest", "-q")
        first = self.check(*argv)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("why: no record yet; passed", self.result(first))
        self.assertIn("[Click observation]", first.stdout)
        self.write("docs/notes.md", "still unrelated\n")
        second = self.check(*argv)
        self.assertIn("[Click result] reused:", self.result(second))
        self.assertNotIn("Ran 2 tests", second.stderr)
        self.write("src/text.py", "def shout(s):\n    return s.lower()  # broken\n")
        third = self.check(*argv)
        self.assertEqual(third.returncode, 1)
        self.assertIn("inputs changed: src/text.py", self.result(third))
        self.assertIn("failed, so nothing is recorded", self.result(third))
        # The passing record still describes the passing contents.
        self.write("src/text.py", "def shout(s):\n    return s.upper()\n")
        self.assertIn("[Click result] reused:", self.result(self.check(*argv)))

    def test_split_is_trusted_after_validation_and_then_runs_only_changed_groups(self) -> None:
        argv = (sys.executable, "-m", "unittest", "-q", "{paths}")
        first = self.check(*argv, paths="tests/test_*.py")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("split not validated yet, so the whole command ran", self.result(first))
        self.assertIn("split validated", first.stdout)
        self.write("src/text.py", "def shout(s):\n    return s.upper() + ''\n")
        second = self.check(*argv, paths="tests/test_*.py")
        self.assertIn("ran 1 of 2 groups (1 of 2 paths)", self.result(second))
        self.assertIn("tests/test_text.py ← inputs changed: src/text.py", self.result(second))
        self.assertIn("Ran 1 test", second.stderr)
        self.assertIn("reused:", self.result(self.check(*argv, paths="tests/test_*.py")))
        self.write("tests/test_new.py", "import unittest\nclass T(unittest.TestCase):\n"
                   "    def test_new(self):\n        pass\n")
        # Python's importer lists the package directory, so a new module in it
        # reruns every group that imported from there.
        added = self.check(*argv, paths="tests/test_*.py")
        self.assertIn("ran 3 of 3 groups (3 of 3 paths)", self.result(added))
        self.assertIn("tests/test_new.py ← no record yet", self.result(added))
        self.assertIn("tests/test_calc.py ← inputs changed: tests;", self.result(added))

    def _pause_when_traced(self) -> None:
        # Imported by test_calc only: its traced runs pause, so an edit lands meanwhile.
        self.write("src/pause.py", "import time\nif 'TracerPid:\\t0' not in open('/proc/self/status').read():\n"
                   "    time.sleep(0.8)\n")

    def _edit_while_the_first_group_is_traced(self, name: str, text: str) -> dict:
        argv = (sys.executable, "-m", "unittest", "-q", "{paths}")
        first = self.check(*argv, paths="tests/test_*.py", mode="background")
        self.assertIn("split not validated yet", self.result(first))
        root = self.checkout_root()
        self.wait_for(lambda: observed._read_json(root / observed.WORKER_FILE).get("group"))
        time.sleep(0.3)
        self.write(name, text)
        return self.wait_for_worker()

    def test_an_edit_while_the_split_is_traced_does_not_hold_its_verdict_back(self) -> None:
        self._pause_when_traced()
        self.write("src/calc.py", "import src.pause\ndef add(a, b):\n    return a + b\n")
        report = self._edit_while_the_first_group_is_traced(
            "src/calc.py", "import src.pause\ndef add(a, b):\n    return (a + b)\n")
        # Groups first, then the whole command; the dropped group's reads still count.
        self.assertEqual([entry["result"] for entry in report["entries"]], ["dropped", "recorded", "recorded"])
        self.assertTrue(report["entries"][0]["command"].endswith("tests/test_calc.py"))
        self.assertEqual(report["split"]["status"], "trusted")
        second = self.check(sys.executable, "-m", "unittest", "-q", "{paths}", paths="tests/test_*.py")
        self.assertIn("ran 1 of 2 groups (1 of 2 paths)", self.result(second))
        self.assertIn("tests/test_calc.py ← no record yet", self.result(second))

    def test_a_failure_caused_by_an_edit_while_traced_neither_rejects_nor_counts(self) -> None:
        self._pause_when_traced()
        self.write("data.txt", "ok")
        self.write("tests/test_calc.py", "import unittest\nimport src.pause\n"
                   "class T(unittest.TestCase):\n    def test_data(self):\n"
                   "        self.assertEqual(open('data.txt').read(), 'ok')\n")
        report = self._edit_while_the_first_group_is_traced("data.txt", "bad")
        self.assertEqual(report["entries"][0]["result"], "inconclusive")
        # The whole command, traced after the edit, fails for real: no verdict yet.
        self.assertEqual(report["entries"][-1]["result"], "failed")
        self.assertNotIn("split", report)
        root = self.checkout_root()
        self.assertEqual(list((root / "splits").glob("*/split.json")), [])
        for notes in (root / "splits").glob("*/groups/" + observed.NOTES_FILE):
            self.assertEqual(observed._read_json(notes), {}, notes)

    def test_split_is_rejected_when_only_the_whole_command_reads_an_input(self) -> None:
        self.write("combined.txt", "x")
        self.write("check.py", "import sys\nfor p in sys.argv[1:]:\n    open(p).read()\n"
                   "if len(sys.argv) > 2:\n    open('combined.txt').read()\n")
        argv = (sys.executable, "check.py", "{paths}")
        first = self.check(*argv, paths="tests/test_*.py")
        self.assertIn("split rejected: only the whole command read combined.txt", first.stdout)
        self.write("src/text.py", "changed = True\n")  # read by no run: still reused as a whole
        self.assertIn("reused:", self.result(self.check(*argv, paths="tests/test_*.py")))
        self.write("combined.txt", "y")
        again = self.check(*argv, paths="tests/test_*.py")
        self.assertIn("split rejected (only the whole command read combined.txt), so the whole command ran",
                      self.result(again))

    @unittest.skipUnless(shutil.which("snap") and os.path.exists("/snap/bin/go"), "needs a snap-launched tool")
    def test_a_privileged_launcher_is_not_traced_again(self) -> None:
        first = self.check("/snap/bin/go", "version")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("failed only under observation", first.stdout)
        second = self.check("/snap/bin/go", "version")
        self.assertIn("privileged-exec:abs:/usr/lib/snapd/snap-confine", self.result(second))
        self.assertIn("passed; not recorded:", self.result(second))
        self.assertNotIn("[Click observation]", second.stdout)

    def test_a_failing_check_piped_into_tail_is_not_recorded_as_passing(self) -> None:
        text = f"{sys.executable} -m unittest -q 2>&1 | tail -3"
        # A different size: Python trusts a same-size .pyc written in the same second.
        self.write("src/text.py", "def shout(s):\n    return s.lower()  # broken\n")
        failed = self.check("bash", "-o", "pipefail", "-c", text)
        self.assertEqual(failed.returncode, 1)
        self.assertIn(f"[Click result] ran: {text} → exit 1", self.result(failed))
        self.write("src/text.py", "def shout(s):\n    return s.upper()\n")
        self.assertEqual(self.check("bash", "-o", "pipefail", "-c", text).returncode, 0)
        self.assertIn("reused:", self.result(self.check("bash", "-o", "pipefail", "-c", text)))

    def test_network_use_is_recorded_once_and_never_repeated_in_the_background(self) -> None:
        self.write("net.py", "import socket\ns = socket.socket()\ns.setblocking(False)\n"
                   "try:\n    s.connect(('192.0.2.1', 9))\nexcept OSError:\n    pass\n")
        first = self.check(sys.executable, "net.py")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = self.check(sys.executable, "net.py")
        self.assertIn("not reusable: network:192.0.2.1", self.result(second))
        self.assertIn("passed; not recorded: network:192.0.2.1", self.result(second))
        self.assertNotIn("[Click observation]", second.stdout)

    def test_background_worker_records_after_the_check_returns(self) -> None:
        argv = (sys.executable, "-m", "unittest", "-q")
        started = time.monotonic()
        first = self.check(*argv, mode="background")
        self.assertIn("running it again under observation in the background", self.result(first))
        report = self.wait_for_worker()
        self.assertEqual([entry["result"] for entry in report["entries"]], ["recorded"])
        self.assertLess(time.monotonic() - started, 60)
        self.assertIn("reused:", self.result(self.check(*argv, mode="background")))

    def test_an_edit_while_the_worker_traces_drops_its_record(self) -> None:
        self.write("slow_check.py", "import time\nopen('src/calc.py').read()\ntime.sleep(1.5)\n")
        argv = (sys.executable, "slow_check.py")
        self.check(*argv, mode="background")
        root = self.checkout_root()
        self.wait_for(lambda: observed._read_json(root / observed.WORKER_FILE).get("group"))
        time.sleep(0.3)
        self.write("src/calc.py", "def add(a, b):\n    return b + a\n")
        report = self.wait_for_worker()
        self.assertEqual(report["entries"][0]["result"], "dropped")
        self.assertEqual(report["entries"][0]["changed"], ["repo:src/calc.py"])
        after = self.check(*argv, mode="off")
        self.assertIn("not recorded: inputs changed while it was observed (src/calc.py)", after.stdout)
        self.assertIn("observation is off", self.result(after))
        self.assertIn("no record yet", self.result(self.check(*argv)))

    def test_workers_ended_from_outside_switch_the_checkout_to_tracing_the_run_itself(self) -> None:
        dead = subprocess.Popen(["true"])
        dead.wait()
        root = observed.Checkout.open(self.store, self.project, {}).root
        for attempt in range(observed.FAILURE_LIMIT):
            observed._write_json(root / observed.WORKER_FILE, {"pid": dead.pid, "group": 0})
            self.check(sys.executable, "-c", f"print({attempt})", mode="off")
        argv = (sys.executable, "-m", "unittest", "-q")
        traced = self.check(*argv, mode="background")
        self.assertIn("for a day the run itself is observed", traced.stdout)
        self.assertRegex(self.result(traced), r"→ exit 0; why: no record yet; .*unittest -q: recorded \d+ inputs$")
        self.assertIn("Ran 2 tests", traced.stderr)
        self.assertFalse((root / observed.WORKER_FILE).exists())
        self.assertIn("reused:", self.result(self.check(*argv, mode="background")))

    def test_the_next_check_stops_a_running_worker_and_takes_over_its_units(self) -> None:
        # Slow only when traced, so the foreground run returns at once.
        self.write("slow_check.py", "import time\nstatus = open('/proc/self/status').read()\n"
                   "if 'TracerPid:\\t0' not in status:\n    time.sleep(20)\n")
        self.check(sys.executable, "slow_check.py", mode="background")
        root = self.checkout_root()
        self.wait_for(lambda: observed._read_json(root / observed.WORKER_FILE).get("group"))
        quick = self.check(sys.executable, "-c", "pass", mode="background")
        self.assertIn("stopped the previous background observation", quick.stdout)
        pending = observed._read_json(root / observed.WORKER_FILE)["pending"]
        self.assertEqual([spec["argv"][1] for spec in pending["units"]], ["-c", "slow_check.py"])
        self.assertTrue(pending["units"][1]["carried"])
        observed._stop_worker(observed.Checkout(self.project, None, self.store, root, {}, "en"))


@unittest.skipUnless(NODE_OBSERVED, "needs node, and strace outside Windows")
class NodeCheckTests(_Fixture):
    def test_node_test_runner(self) -> None:
        self.write("lib.js", "exports.twice = (x) => x * 2;\n")
        self.write("other.js", "exports.unused = 1;\n")
        self.write("lib.test.js", "const test = require('node:test');\nconst assert = require('node:assert');\n"
                   "const { twice } = require('./lib');\ntest('twice', () => assert.strictEqual(twice(2), 4));\n")
        argv = ("node", "--test", "lib.test.js")
        first = self.check(*argv)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.write("other.js", "exports.unused = 2;\n")
        self.assertIn("reused:", self.result(self.check(*argv)))
        self.write("lib.js", "exports.twice = (x) => x + x;\n")
        self.assertIn("inputs changed: lib.js", self.result(self.check(*argv)))

    def test_observed_in_a_detached_background_worker(self) -> None:
        self.write("lib.js", "exports.twice = (x) => x * 2;\n")
        self.write("lib.test.js", "const test = require('node:test');\nconst assert = require('node:assert');\n"
                   "const { twice } = require('./lib');\ntest('twice', () => assert.strictEqual(twice(2), 4));\n")
        argv = ("node", "--test", "lib.test.js")
        first = self.check(*argv, mode="background")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        report = self.wait_for_worker()
        self.assertEqual([entry["result"] for entry in report["entries"]], ["recorded"], report)
        self.assertIn("reused:", self.result(self.check(*argv, mode="background")))


@unittest.skipUnless(NODE_OBSERVED and _node_runs_typescript(),
                     "needs a Node that runs TypeScript, and strace outside Windows")
class TypeScriptCheckTests(_Fixture):
    # strace escapes the UTF-8 bytes of this name in every descriptor path.
    project_name = "타입스크립트 프로젝트"

    def setUp(self) -> None:
        super().setUp()
        self.write("package.json", '{ "type": "module" }\n')
        self.write("src/shared/format.ts", "export function pad(value: string, width: number): string {\n"
                   "  return value.padStart(width);\n}\n")
        self.write("src/math.ts", "export const add = (a: number, b: number): number => a + b;\n")
        self.write("src/text.ts", 'import { pad } from "./shared/format.ts";\n'
                   "export const right = (value: string): string => pad(value, 4);\n")
        header = 'import { test } from "node:test";\nimport assert from "node:assert/strict";\n'
        self.write("test/math.test.ts", header + 'import { add } from "../src/math.ts";\n'
                   'test("add", () => assert.equal(add(2, 3), 5));\n')
        self.write("test/text.test.ts", header + 'import { right } from "../src/text.ts";\n'
                   'test("right", () => assert.equal(right("ab"), "  ab"));\n')
        # A server and client in one test talk over a socket the run binds itself.
        self.write("test/socket.test.ts", header + 'import net from "node:net";\nimport os from "node:os";\n'
                   'import path from "node:path";\n'
                   'test("echo", async () => {\n'
                   '  const where = process.platform === "win32"\n'
                   r'    ? `\\\\.\\pipe\\click-ts-${process.pid}`' '\n'
                   '    : path.join(os.tmpdir(), `click-ts-${process.pid}.sock`);\n'
                   '  const server = net.createServer((socket) => socket.pipe(socket));\n'
                   '  await new Promise<void>((done) => server.listen(where, done));\n'
                   '  const reply = await new Promise<string>((done) => {\n'
                   '    const client = net.connect(where, () => client.end("hi"));\n'
                   '    client.on("data", (data) => done(String(data)));\n'
                   '  });\n'
                   '  server.close();\n'
                   '  assert.equal(reply, "hi");\n'
                   '});\n')
        # Listed relative to the working directory, whose name strace escapes.
        self.write("test/fixtures.test.ts", header + 'import { readdirSync } from "node:fs";\n'
                   'test("fixtures", () => assert.deepEqual(readdirSync("test/fixtures"), ["a.json"]));\n')
        self.write("test/fixtures/a.json", "{}\n")
        self.write("docs/notes.md", "unrelated\n")

    def test_node_runs_typescript_tests_split_by_file(self) -> None:
        argv = ("node", "--test", "{paths}")
        first = self.check(*argv, paths="test/*.test.ts")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("split validated", first.stdout)
        self.assertNotIn("volatile", first.stdout)
        self.write("docs/notes.md", "still unrelated\n")
        self.assertIn("reused:", self.result(self.check(*argv, paths="test/*.test.ts")))
        self.write("src/shared/format.ts", "export function pad(value: string, width: number): string {\n"
                   "  return value.padStart(width, ' ');\n}\n")
        changed = self.check(*argv, paths="test/*.test.ts")
        self.assertIn("ran 1 of 4 groups (1 of 4 paths)", self.result(changed))
        self.assertIn("test/text.test.ts ← inputs changed: src/shared/format.ts", self.result(changed))
        self.write("test/fixtures/b.json", "{}\n")
        listed = self.check(*argv, paths="test/*.test.ts")
        self.assertEqual(listed.returncode, 1)
        self.assertIn("ran 1 of 4 groups (1 of 4 paths)", self.result(listed))
        self.assertIn("test/fixtures.test.ts ← inputs changed: test/fixtures", self.result(listed))


@unittest.skipUnless(STRACE and shutil.which("go"), "needs strace and go")
class GoCheckTests(_Fixture):
    def test_go_test(self) -> None:
        go = shutil.which("go")
        if go and go.startswith("/snap/bin/"):
            # snap's launcher needs file capabilities, which a traced run loses.
            go = "/snap/go/current/bin/go" if os.access("/snap/go/current/bin/go", os.X_OK) else None
        if go is None:
            self.skipTest("go only as a snap without a direct binary")
        cache = tempfile.mkdtemp(prefix="click-go-cache-")
        self.addCleanup(shutil.rmtree, cache, True)
        self.environment.update(GOCACHE=cache, GOFLAGS="-count=1", GOTOOLCHAIN="local", GOPROXY="off")
        self.write("go.mod", "module example.com/calc\n\ngo 1.21\n")
        self.write("calc.go", "package calc\n\nfunc Add(a, b int) int { return a + b }\n")
        self.write("calc_test.go", "package calc\n\nimport \"testing\"\n\n"
                   "func TestAdd(t *testing.T) { if Add(2, 3) != 5 { t.Fatal() } }\n")
        # The go command stats every file of a package directory it loads, so
        # an unrelated file only stays unrelated outside one.
        self.write("docs/notes.md", "docs\n")
        argv = (go, "test", "./...")
        first = self.check(*argv)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.write("docs/notes.md", "more docs\n")
        second = self.check(*argv)
        self.assertIn("reused:", self.result(second), second.stdout)
        self.write("calc.go", "package calc\n\nfunc Add(a, b int) int { return b + a }\n")
        self.assertIn("inputs changed: calc.go", self.result(self.check(*argv)))


@unittest.skipUnless(STRACE and shutil.which("make"), "needs strace and make")
class MakeCheckTests(_Fixture):
    def test_make_target_running_a_shell_script(self) -> None:
        self.write("Makefile", "check:\n\t@sh check.sh\n")
        self.write("check.sh", "grep -q ready data.txt\n")
        self.write("data.txt", "ready\n")
        self.write("unrelated.txt", "a\n")
        argv = ("make", "-s", "check")
        self.assertEqual(self.check(*argv).returncode, 0)
        self.write("unrelated.txt", "b\n")
        self.assertIn("reused:", self.result(self.check(*argv)))
        self.write("data.txt", "not yet\n")
        failed = self.check(*argv)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("inputs changed: data.txt", self.result(failed))


if __name__ == "__main__":
    unittest.main()
