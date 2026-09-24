from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from ci import click_ci, click_ci_trace


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "ci" / "click_ci.py"


def reduce(lines: str, cwd: str = "/work") -> click_ci_trace.TraceReducer:
    return click_ci_trace.reduce_lines(lines.strip().splitlines(), cwd=Path(cwd))


class TraceReducerTests(unittest.TestCase):
    def test_reads_listings_lookups_and_execs_are_inputs(self) -> None:
        reducer = reduce('''
100 execve("/usr/bin/python3", ["python3", "-m", "unittest"], 0x1 /* 9 vars */) = 0
100 openat(AT_FDCWD</work>, "src/calc.py", O_RDONLY|O_CLOEXEC) = 3</work/src/calc.py>
100 getdents64(3</work/tests>, 0x55 /* 4 entries */, 32768) = 112
100 newfstatat(AT_FDCWD</work>, "missing.py", 0x7ff, 0) = -1 ENOENT (No such file or directory)
100 access("/etc/ld.so.preload", R_OK) = -1 ENOENT (No such file or directory)
100 +++ exited with 0 +++
''')
        paths = reducer.paths
        self.assertEqual(paths["/usr/bin/python3"].first, "input")
        self.assertEqual(paths["/usr/bin/python3"].operations, {"execute"})
        self.assertEqual(paths["/work/src/calc.py"].operations, {"read"})
        self.assertEqual(paths["/work/tests"].operations, {"enumerate"})
        self.assertEqual(paths["/work/missing.py"].first, "missing")
        self.assertEqual(paths["/etc/ld.so.preload"].first, "missing")
        self.assertEqual(reducer.root_exit, 0)
        self.assertEqual(reducer.unresolved, 0)

    def test_interleaved_calls_are_stitched_and_vfork_children_inherit_cwd(self) -> None:
        # With -f a vfork child's execve can appear before the parent learns
        # the child's pid; its relative lookups wait for the parent's cwd.
        reducer = reduce('''
200 chdir("/work/hooks") = 0
200 vfork( <unfinished ...>
201 execve("./run.sh", ["./run.sh"], 0x1 /* 9 vars */ <unfinished ...>
200 <... vfork resumed>) = 201
201 <... execve resumed>) = 0
201 openat(AT_FDCWD</work/hooks>, "data.json", O_RDONLY) = 3</work/hooks/data.json>
200 +++ exited with 3 +++
''')
        self.assertEqual(reducer.paths["/work/hooks/run.sh"].operations, {"execute"})
        self.assertIn("/work/hooks/data.json", reducer.paths)
        self.assertEqual(reducer.root_exit, 3)
        self.assertEqual(reducer.unresolved, 0)
        self.assertEqual(reducer.processes, 2)

    def test_products_are_not_inputs_and_changed_inputs_are_flagged(self) -> None:
        reducer = reduce('''
300 openat(AT_FDCWD</work>, "out.txt", O_WRONLY|O_CREAT|O_TRUNC, 0666) = 3</work/out.txt>
300 openat(AT_FDCWD</work>, "out.txt", O_RDONLY) = 3</work/out.txt>
300 openat(AT_FDCWD</work>, "fixture.txt", O_RDONLY) = 3</work/fixture.txt>
300 unlinkat(AT_FDCWD</work>, "fixture.txt", 0) = 0
300 newfstatat(AT_FDCWD</work>, "later.txt", 0x1, 0) = -1 ENOENT (No such file or directory)
300 openat(AT_FDCWD</work>, "later.txt", O_WRONLY|O_CREAT|O_TRUNC, 0666) = 3</work/later.txt>
300 renameat2(AT_FDCWD</work>, "a.tmp", AT_FDCWD</work>, "b.txt", 0) = 0
300 openat(AT_FDCWD</work>, "index.lock", O_RDWR|O_CREAT|O_EXCL|O_CLOEXEC, 0666) = 3</work/index.lock>
300 openat(AT_FDCWD</work>, "state.db", O_RDWR|O_CREAT|O_CLOEXEC, 0644) = 3</work/state.db>
300 openat(AT_FDCWD</work>, "state.db", O_RDONLY) = 3</work/state.db>
300 openat(AT_FDCWD</work>, "debug.log", O_RDWR|O_APPEND|O_CREAT|O_CLOEXEC, 0666) = 3</work/debug.log>
''')
        paths = reducer.paths
        self.assertEqual(paths["/work/out.txt"].first, "produced")
        self.assertTrue(paths["/work/fixture.txt"].modified_after_input)
        self.assertEqual(paths["/work/later.txt"].first, "missing")
        self.assertEqual(paths["/work/a.tmp"].first, "deleted")
        self.assertEqual(paths["/work/b.txt"].first, "produced")
        # A lock taken with O_EXCL did not exist before; opening for update did.
        self.assertEqual(paths["/work/index.lock"].first, "produced")
        self.assertFalse(paths["/work/index.lock"].modified_after_input)
        # Opened for update with O_CREAT: product or input is settled later.
        self.assertEqual(paths["/work/state.db"].first, "opened")
        self.assertEqual(paths["/work/debug.log"].first, "opened")

    def test_opened_files_are_products_only_when_born_during_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            before = Path(temporary) / "existing.db"
            before.write_text("rows\n", encoding="utf-8")
            if click_ci_trace.birth_time(str(before)) is None:
                self.skipTest("this filesystem does not report birth times")
            time.sleep(0.05)
            started_at = time.time()
            time.sleep(0.05)
            during = Path(temporary) / "debug.log"
            during.write_text("log\n", encoding="utf-8")
            replaced = Path(temporary) / "replaced.log"
            replaced.write_text("new\n", encoding="utf-8")
            paths = {str(p): click_ci_trace.PathState(first="opened") for p in (before, during, replaced)}
            paths[str(replaced)].modified_after_input = True  # renamed over after the open
            paths[str(Path(temporary) / "gone.lock")] = click_ci_trace.PathState(first="opened")
            click_ci_trace.settle_opened(paths, started_at=started_at, finished_at=time.time())
            self.assertEqual(paths[str(during)].first, "produced")
            for fail_closed in (before, replaced, Path(temporary) / "gone.lock"):
                state = paths[str(fail_closed)]
                self.assertEqual((state.first, state.modified_after_input), ("input", True), fail_closed)

    def test_external_network_and_unknown_lines_fail_closed(self) -> None:
        reducer = reduce('''
400 connect(3<socket:[1]>, {sa_family=AF_INET, sin_port=htons(443), sin_addr=inet_addr("140.82.112.3")}, 16) = 0
400 connect(4<socket:[2]>, {sa_family=AF_INET, sin_port=htons(8080), sin_addr=inet_addr("127.0.0.1")}, 16) = 0
400 mount("none", "/mnt", "tmpfs", 0, NULL) = 0
this is not a strace line
400 renameat2(AT_FDCWD</work>, "a", AT_FDCWD</work>, "b", 0 <unfinished ...>
''')
        self.assertEqual(reducer.volatile, ["network:140.82.112.3"])
        self.assertEqual(reducer.unresolved, 3)
        self.assertEqual(reducer.unresolved_examples[1], "this is not a strace line")
        self.assertTrue(reducer.unresolved_examples[2].endswith("[never resumed]"))

    def test_execve_from_a_thread_finishes_under_the_leader_pid(self) -> None:
        # Verbatim shape from strace 6.8 when a Python thread calls os.execv.
        reducer = reduce('''
500 clone3({flags=CLONE_VM|CLONE_FS|CLONE_FILES|CLONE_SIGHAND|CLONE_THREAD, child_tid=0x7f, parent_tid=0x7f, exit_signal=0, stack=0x7f, stack_size=0x7fff00, tls=0x7f} => {parent_tid=[501]}, 88) = 501
501 execve("/bin/true", ["true"], 0x7ffcae303db8 /* 96 vars */ <pid changed to 500 ...>
500 +++ superseded by execve in pid 501 +++
500 <... execve resumed>)            = -1 (errno 18446744073709551595)
500 openat(AT_FDCWD, "after.txt", O_RDONLY) = 3</work/after.txt>
500 +++ exited with 0 +++
''')
        self.assertEqual(reducer.unresolved, 0, reducer.unresolved_examples)
        self.assertEqual(reducer.paths["/bin/true"].operations, {"execute"})
        self.assertIn("/work/after.txt", reducer.paths)
        self.assertEqual(reducer.root_exit, 0)

    def test_killed_processes_and_nested_tracers(self) -> None:
        # Verbatim from strace 6.8: a process SIGKILLed at a syscall stop.
        reducer = reduce('''
600 kill(601, SIGKILL) = 0
601 ???( <unfinished ...>
600 wait4(601,  <unfinished ...>
601 <... ??? resumed>)               = ?
601 +++ killed by SIGKILL +++
600 <... wait4 resumed>[{WIFSIGNALED(s) && WTERMSIG(s) == SIGKILL}], 0, NULL) = 601
602 ptrace(PTRACE_TRACEME) = -1 EPERM (Operation not permitted)
''')
        self.assertEqual(reducer.unresolved, 0, reducer.unresolved_examples)
        self.assertEqual(reducer.volatile, ["nested-ptrace"])

    def test_non_ascii_paths_decode_as_utf8(self) -> None:
        reducer = reduce(r'''
500 openat(AT_FDCWD</work>, "\355\225\234.txt", O_RDONLY) = 3</work/한.txt>
''')
        self.assertIn("/work/한.txt", reducer.paths)


@unittest.skipUnless(sys.platform.startswith("linux"), "Click CI observes Linux runners only")
class RecordDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name).resolve() / "repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src" / "calc.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.repo / "src" / "unused.py").write_text("OTHER = 1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.places = click_ci.Places(repo=str(self.repo), home="/nonexistent-home",
                                      volatile_roots=("/proc", "/tmp/volatile"),
                                      ignores=click_ci.DEFAULT_IGNORES)
        self.environment = {"PATH": "/usr/bin", "PYTHONHASHSEED": "0"}

    def record(self, observation: click_ci_trace.Observation) -> dict:
        return click_ci.build_record(observation, identity={"key": "k"}, places=self.places,
                                     digester=click_ci.Digester(self.places),
                                     environment=self.environment)

    def decide(self, record: dict, environment: dict | None = None) -> click_ci.Decision:
        return click_ci.decide(record, places=self.places, digester=click_ci.Digester(self.places),
                               environment=environment or self.environment)

    def test_decisions_follow_content_listing_and_absence(self) -> None:
        observation = click_ci_trace.Observation(
            exit_code=0,
            paths={
                str(self.repo / "src" / "calc.py"): click_ci_trace.PathState("input", {"read"}),
                str(self.repo / "src"): click_ci_trace.PathState("input", {"enumerate"}),
                str(self.repo / "src" / "absent.py"): click_ci_trace.PathState("missing", {"metadata"}),
                str(self.repo / "src" / "__pycache__" / "calc.pyc"): click_ci_trace.PathState("input", {"read"}),
                "/tmp/volatile/scratch": click_ci_trace.PathState("input", {"read"}),
            },
            volatile_reasons=[], unresolved_lines=0, process_count=1, duration_seconds=0.1)
        record = self.record(observation)
        self.assertEqual(set(record["inputs"]), {"repo:src/calc.py", "repo:src", "repo:src/absent.py"})
        self.assertTrue(self.decide(record).skip)

        (self.repo / "src" / "unused.py").write_text("OTHER = 2\n", encoding="utf-8")
        self.assertTrue(self.decide(record).skip, "an unread file's content is not an input")

        (self.repo / "src" / "calc.py").write_text("VALUE = 2\n", encoding="utf-8")
        decision = self.decide(record)
        self.assertEqual((decision.skip, decision.reason, decision.changed),
                         (False, "inputs-changed", ["repo:src/calc.py"]))
        (self.repo / "src" / "calc.py").write_text("VALUE = 1\n", encoding="utf-8")

        (self.repo / "src" / "absent.py").write_text("", encoding="utf-8")
        decision = self.decide(record)
        self.assertEqual(decision.changed, ["repo:src", "repo:src/absent.py"])
        (self.repo / "src" / "absent.py").unlink()
        self.assertTrue(self.decide(record).skip)

        changed_path = {**self.environment, "PATH": "/opt/other:/usr/bin"}
        decision = self.decide(record, changed_path)
        self.assertEqual((decision.reason, decision.changed), ("environment-changed", ["env:PATH"]))
        unrelated = {**self.environment, "GITHUB_RUN_ID": "99"}
        self.assertTrue(self.decide(record, unrelated).skip)

    def test_run_products_leave_listings_and_changed_inputs_never_skip(self) -> None:
        produced = self.repo / "src" / "generated.txt"
        produced.write_text("made by the run\n", encoding="utf-8")
        observation = click_ci_trace.Observation(
            exit_code=0,
            paths={
                str(self.repo / "src"): click_ci_trace.PathState("input", {"enumerate"}),
                str(produced): click_ci_trace.PathState("produced"),
            },
            volatile_reasons=[], unresolved_lines=0, process_count=1, duration_seconds=0.1)
        record = self.record(observation)
        produced.unlink()
        self.assertTrue(self.decide(record).skip, "the run's own product is not part of the listing")

        changed = click_ci_trace.PathState("input", {"read"}, modified_after_input=True)
        record = self.record(click_ci_trace.Observation(
            exit_code=0, paths={str(self.repo / "src" / "calc.py"): changed},
            volatile_reasons=[], unresolved_lines=0, process_count=1, duration_seconds=0.1))
        decision = self.decide(record)
        self.assertEqual((decision.skip, decision.reason), (False, "volatile"))
        self.assertEqual(decision.changed, ["changed-own-input:repo:src/calc.py"])

        failed = self.record(click_ci_trace.Observation(
            exit_code=1, paths={}, volatile_reasons=[], unresolved_lines=0,
            process_count=1, duration_seconds=0.1))
        self.assertEqual(self.decide(failed).reason, "recorded-run-failed")
        self.assertEqual(self.decide(None).reason, "no-record")

    def test_store_round_trips_compressed_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = click_ci.Store(Path(directory))
            store.save("abc", {"version": 1, "inputs": {"repo:x": "missing"}})
            self.assertEqual(store.load("abc"), {"version": 1, "inputs": {"repo:x": "missing"}})
            with gzip.open(Path(directory) / "abc.json.gz", "rt", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["version"], 1)
            store.remove("abc")
            self.assertIsNone(store.load("abc"))


class ModeTests(unittest.TestCase):
    def test_default_branch_pushes_record_and_everything_else_selects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text(json.dumps({"repository": {"default_branch": "main"}}), encoding="utf-8")
            base = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_PATH": str(event)}
            cases = {
                ("push", "refs/heads/main"): "record",
                ("push", "refs/heads/feature"): "select",
                ("pull_request", "refs/pull/1/merge"): "select",
                ("schedule", "refs/heads/main"): "record",
            }
            for (name, ref), expected in cases.items():
                with self.subTest(event=name, ref=ref):
                    environment = {**base, "GITHUB_EVENT_NAME": name, "GITHUB_REF": ref}
                    self.assertEqual(click_ci.automatic_mode(environment), expected)
        self.assertEqual(click_ci.automatic_mode({}), "select")
        self.assertEqual(click_ci.automatic_mode({"CLICK_CI_MODE": "shadow"}), "shadow")


class SplitTests(unittest.TestCase):
    def test_globs_expand_with_exclusions_and_stay_as_written(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in ("tests/test_a.py", "tests/slow/test_s.py", "tests/helper.py",
                             "web/node_modules/x/a.test.js", "web/app.test.js"):
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_text("", encoding="utf-8")
            self.assertEqual(click_ci.expand_paths(["tests/**/test_*.py", "!tests/slow"], root),
                             ["tests/test_a.py"])
            self.assertEqual(click_ci.expand_paths(["./web/**/*.test.js"], root), ["./web/app.test.js"])

    def test_paths_fill_an_argument_list_or_a_shell_string(self) -> None:
        paths = ["tests/a b.py", "tests/c.py"]
        self.assertEqual(click_ci.substitute(["pytest", "-q", "{paths}"], paths),
                         ["pytest", "-q", "tests/a b.py", "tests/c.py"])
        self.assertEqual(click_ci.substitute(["bash", "-c", "pytest {paths} -x"], paths),
                         ["bash", "-c", "pytest 'tests/a b.py' tests/c.py -x"])

    def test_groups_are_stable_when_a_path_is_added(self) -> None:
        paths = [f"tests/test_{index}.py" for index in range(100)]
        self.assertEqual(click_ci.plan_groups(paths[:3]), [[p] for p in paths[:3]])
        before = click_ci.plan_groups(paths)
        after = click_ci.plan_groups(sorted(paths + ["tests/test_new.py"]))
        self.assertLessEqual(len(before), click_ci.AUTO_GROUPS)  # empty buckets are dropped
        changed = [group for group in after if group not in before]
        self.assertEqual(len(changed), 1)
        self.assertIn("tests/test_new.py", changed[0])


@unittest.skipUnless(sys.platform.startswith("linux") and click_ci_trace.strace_available(),
                     "strace observation runs on Linux with strace installed")
class ObservedCommandTests(unittest.TestCase):
    def test_record_then_select_skips_until_a_read_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repo, store = root / "repo", root / "store"
            (repo / "src").mkdir(parents=True)
            (repo / "docs").mkdir()
            (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            (repo / "docs" / "guide.md").write_text("# guide\n", encoding="utf-8")
            (repo / "test_calc.py").write_text(
                "import sys, unittest\nsys.path.insert(0, 'src')\nimport calc\n"
                "class T(unittest.TestCase):\n    def test_add(self):\n"
                "        self.assertEqual(calc.add(1, 2), 3)\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            command = [sys.executable, "-B", "-m", "unittest", "-q", "test_calc"]
            report = root / "report.jsonl"

            def run(mode: str) -> dict:
                environment = {**os.environ, "CLICK_CI_REPORT": str(report), "PYTHONDONTWRITEBYTECODE": "1"}
                environment.pop("GITHUB_STEP_SUMMARY", None)
                completed = subprocess.run(
                    [sys.executable, str(SCRIPT), "run", "--mode", mode, "--store", str(store), "--", *command],
                    cwd=repo, env=environment, capture_output=True, text=True, check=False)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                return json.loads(report.read_text(encoding="utf-8").splitlines()[-1])

            recorded = run("record")
            self.assertEqual(recorded["action"], "recorded")
            self.assertEqual(run("select")["action"], "skipped")
            (repo / "docs" / "guide.md").write_text("# changed\n", encoding="utf-8")
            self.assertEqual(run("select")["action"], "skipped")
            (repo / "src" / "calc.py").write_text("def add(a, b):\n    return b + a\n", encoding="utf-8")
            ran = run("select")
            self.assertEqual((ran["action"], ran["changed"]), ("ran", ["repo:src/calc.py"]))
            shadow = run("shadow")
            self.assertEqual((shadow["action"], shadow["would_skip"]), ("shadow", False))

    def test_split_command_reruns_only_the_groups_whose_inputs_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repo, store, report = root / "repo", root / "store", root / "report.jsonl"
            (repo / "src").mkdir(parents=True)
            (repo / "tests").mkdir()
            for name in ("alpha", "beta"):
                (repo / "src" / f"{name}.py").write_text("VALUE = 1\n", encoding="utf-8")
                (repo / "tests" / f"test_{name}.py").write_text(
                    f"import sys, unittest\nsys.path.insert(0, 'src')\nimport {name}\n"
                    f"class T(unittest.TestCase):\n    def test_value(self):\n"
                    f"        self.assertEqual({name}.VALUE, 1)\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(repo)], check=True)

            def run(mode: str) -> tuple[dict, str]:
                environment = {**os.environ, "CLICK_CI_REPORT": str(report), "PYTHONDONTWRITEBYTECODE": "1"}
                for name in ("GITHUB_STEP_SUMMARY", "GITHUB_ACTIONS"):
                    environment.pop(name, None)
                completed = subprocess.run(
                    [sys.executable, str(SCRIPT), "run", "--mode", mode, "--store", str(store),
                     "--paths", "tests/test_*.py", "--",
                     sys.executable, "-B", "-m", "unittest", "{paths}"],
                    cwd=repo, env=environment, capture_output=True, text=True, check=False)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                return json.loads(report.read_text(encoding="utf-8").splitlines()[-1]), completed.stderr

            recorded, _ = run("record")
            self.assertEqual((recorded["action"], recorded["groups"]), ("recorded", 2))
            self.assertEqual(run("select")[0]["action"], "skipped")
            (repo / "src" / "beta.py").write_text("VALUE = 1  # touched\n", encoding="utf-8")
            ran, stderr = run("select")
            self.assertEqual((ran["action"], ran["ran_paths"]), ("ran", 1))
            self.assertIn("tests/test_beta.py ← inputs-changed: repo:src/beta.py", ran["detail"])
            self.assertIn("Ran 1 test", stderr)

    def test_a_failure_caused_by_tracing_is_decided_by_an_unobserved_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            store, report = root / "store", root / "report.jsonl"
            # Exits 1 only while traced, like a test that attaches a debugger.
            command = [sys.executable, "-c",
                       "import sys\nstatus = open('/proc/self/status').read()\n"
                       "sys.exit(0 if 'TracerPid:\\t0\\n' in status else 1)"]
            environment = {**os.environ, "CLICK_CI_REPORT": str(report)}
            for name in ("GITHUB_STEP_SUMMARY", "GITHUB_ACTIONS"):
                environment.pop(name, None)
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "run", "--mode", "record", "--store", str(store), "--", *command],
                cwd=root, env=environment, capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("failed only under observation", completed.stderr)
            entry = json.loads(report.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual((entry["observed_exit_code"], entry["exit_code"]), (1, 0))
            self.assertFalse(any(store.glob("*.json.gz")))


if __name__ == "__main__":
    unittest.main()
