"""Observing Node commands from inside, where no system call tracer runs (Windows)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from hooks import click_runtime_trace as runtime
from hooks import click_syscall_trace

NODE = shutil.which("node")


def _states(observation, root: Path) -> dict[str, tuple[str, set[str]]]:
    found = {}
    real = os.path.normcase(os.path.realpath(root))
    for path, state in observation.paths.items():
        normal = os.path.normcase(os.path.realpath(path) if "~" in path else path)
        if normal == real or normal.startswith(real + os.sep):
            relative = os.path.relpath(normal, real).replace(os.sep, "/")
            found[relative] = (state.first, state.operations)
    return found


@unittest.skipUnless(NODE, "needs node")
class ObserverTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(os.path.realpath(temporary.name))  # long names, as records spell them

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def observe(self, *argv: str):
        return runtime.observe(list(argv), cwd=self.root, repo=str(self.root),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout_seconds=120)

    def test_every_way_node_reads_writes_and_looks_up_files_is_seen(self) -> None:
        for name in ("data", "promised", "streamed", "worker", "child"):
            self.write(f"{name}.txt", name)
        self.write("lib/index.js", "module.exports = 1;\n")
        self.write("esm.mjs", "export default 2;\n")
        self.write("listed/a", "")
        self.write("worker.js", "require('node:fs').readFileSync('worker.txt');\n")
        self.write("child.js", "require('node:fs').readFileSync('child.txt');\n")
        self.write("main.js", "\n".join([
            "const fs = require('node:fs');",
            "const { Worker } = require('node:worker_threads');",
            "const { execFileSync } = require('node:child_process');",
            "fs.readFileSync('data.txt', 'utf8');",
            "fs.existsSync('absent.txt');",
            "fs.readdirSync('listed');",
            "fs.writeFileSync('out.txt', 'x');",
            "require('./lib');",
            "(async () => {",
            "  await fs.promises.readFile('promised.txt');",
            "  await new Promise((done) => fs.createReadStream('streamed.txt').on('close', done).resume());",
            "  await import('./esm.mjs');",
            "  await new Promise((done, fail) => new Worker('./worker.js').on('exit', done).on('error', fail));",
            "  execFileSync(process.execPath, ['child.js']);",
            "})();",
        ]))
        observation = self.observe("node", "main.js")
        self.assertEqual(observation.exit_code, 0)
        self.assertEqual(observation.volatile_reasons, [])
        self.assertEqual(observation.unresolved_lines, 0)
        states = _states(observation, self.root)
        for name in ("data.txt", "promised.txt", "streamed.txt", "worker.txt", "child.txt",
                     "lib/index.js", "esm.mjs", "main.js", "worker.js", "child.js"):
            with self.subTest(name=name):
                self.assertEqual(states[name][0], "input")
                self.assertIn("read", states[name][1])
        self.assertEqual(states["absent.txt"][0], "missing")
        self.assertIn("enumerate", states["listed"][1])
        self.assertEqual(states["out.txt"][0], "produced")
        # require('./lib') tries lib.js, lib.json and lib/ next to the caller.
        self.assertIn("enumerate", states["."][1])
        self.assertIn(os.path.normcase(os.path.realpath(NODE)),
                      {os.path.normcase(os.path.realpath(path)) for path, state in observation.paths.items()
                       if "execute" in state.operations})

    def test_what_a_killed_process_read_before_it_died_is_kept(self) -> None:
        self.write("read-first.txt", "x")
        self.write("main.js", "require('node:fs').readFileSync('read-first.txt');\n"
                              "process.kill(process.pid, 'SIGKILL');\n")
        observation = self.observe("node", "main.js")
        self.assertNotEqual(observation.exit_code, 0)
        self.assertEqual(_states(observation, self.root)["read-first.txt"][0], "input")

    def test_services_outside_the_command_make_it_volatile_its_own_do_not(self) -> None:
        pipe = "\\\\.\\pipe\\click-own" if os.name == "nt" else str(self.root / "own.sock")
        self.write("main.js", "\n".join([
            "const net = require('node:net');",
            f"const where = {json.dumps(pipe)};",
            "const server = net.createServer((socket) => socket.end());",
            "server.listen(where, () => {",
            "  const client = net.connect(where, () => {});",
            "  client.on('close', () => server.close());",
            "  client.resume();",
            "});",
        ]))
        self.assertEqual(self.observe("node", "main.js").volatile_reasons, [])
        # A lookup of a host outside, answered or not, is a service the check reached.
        self.write("remote.js", "require('node:dns').lookup('example.invalid', () => {});\n")
        self.assertEqual(self.observe("node", "remote.js").volatile_reasons, ["network:example.invalid"])

    def test_a_file_read_then_rewritten_is_an_input_the_command_changed(self) -> None:
        self.write("state.json", "{}")
        self.write("main.js", "const fs = require('node:fs');\n"
                              "fs.writeFileSync('state.json', fs.readFileSync('state.json'));\n")
        observation = self.observe("node", "main.js")
        state = observation.paths[next(path for path in observation.paths if path.endswith("state.json"))]
        self.assertEqual(state.first, "input")
        self.assertTrue(state.modified_after_input)


class ReductionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.logs = Path(temporary.name)
        self.base = os.path.abspath(os.sep + "work")

    def log(self, pid: int, events: list[tuple[int, str, str, str]], *, hooks: bool = True) -> None:
        header = {"pid": pid, "ppid": 1, "thread": 0, "exec": os.path.join(self.base, "node"), "argv": [],
                  "cwd": self.base, "hooks": hooks, "at": "0"}
        lines = [json.dumps(header)] + [f"{clock}\t{event}\t{operation}\t{json.dumps(path)}"
                                        for clock, event, operation, path in events]
        (self.logs / f"{pid}-0.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_events_of_all_processes_fold_in_time_order(self) -> None:
        made = os.path.join(self.base, "made.txt")
        read = os.path.join(self.base, "read.txt")
        # One process writes a file another reads later: a product, not an input.
        self.log(10, [(5, "P", "", made), (1, "I", "read", read)])
        self.log(11, [(7, "I", "read", made), (3, "U", "read", read)])
        reduced = runtime.reduce_logs(self.logs)
        self.assertEqual(reduced.paths[made].first, "produced")
        self.assertEqual(reduced.paths[read].first, "input")
        self.assertTrue(reduced.paths[read].modified_after_input)
        self.assertEqual({process.pid for process in reduced.processes}, {10, 11})

    def test_opened_for_update_is_settled_later_unless_it_was_seen_before(self) -> None:
        fresh = os.path.join(self.base, "fresh.log")
        seen = os.path.join(self.base, "seen.db")
        self.log(10, [(1, "I", "metadata", seen), (2, "O", "", seen), (3, "O", "", fresh)])
        reduced = runtime.reduce_logs(self.logs)
        self.assertEqual(reduced.paths[fresh].first, "opened")
        self.assertTrue(reduced.paths[seen].modified_after_input)

    def test_unaccounted_calls_and_missing_module_hooks_are_reported(self) -> None:
        self.log(10, [(1, "X", "cp", os.path.join(self.base, "tree"))], hooks=False)
        reduced = runtime.reduce_logs(self.logs)
        self.assertEqual(len(reduced.unaccounted), 2)
        self.assertTrue(reduced.unaccounted[0].startswith("cp:"))
        self.assertTrue(reduced.unaccounted[1].startswith("module-hooks:"))

    def test_a_cut_last_line_is_unreadable_not_fatal(self) -> None:
        self.log(10, [(1, "I", "read", os.path.join(self.base, "a"))])
        with open(self.logs / "10-0.log", "a", encoding="utf-8") as handle:
            handle.write("2\tI\tre")
        reduced = runtime.reduce_logs(self.logs)
        self.assertEqual(reduced.unreadable, 1)
        self.assertIn(os.path.join(self.base, "a"), reduced.paths)

    def test_node_options_keep_what_was_there_and_quote_the_preload(self) -> None:
        preload = Path(tempfile.gettempdir()) / "with space" / "observer.cjs"
        options = runtime.node_options("--max-old-space-size=4096", preload)
        self.assertTrue(options.startswith("--max-old-space-size=4096 --require \""))
        self.assertIn(preload.as_posix(), options)
        self.assertNotIn("\\", options)


class ResolutionContextTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = os.path.realpath(temporary.name)

    def write(self, name: str, text: str = "{}") -> str:
        path = os.path.join(self.repo, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_configs_around_what_was_read_and_what_they_extend_become_inputs(self) -> None:
        source = self.write("packages/a/src/x.ts", "")
        self.write("packages/a/package.json")
        self.write("packages/a/tsconfig.json", '{\n  // comment\n  "extends": "../../tsconfig.base.json",\n}\n')
        self.write("tsconfig.base.json", '{ "extends": "@tsconfig/strict/tsconfig.json" }')
        self.write("node_modules/@tsconfig/strict/tsconfig.json")
        self.write("package-lock.json")
        paths = {source: click_syscall_trace.PathState("input", {"read"})}
        runtime.add_resolution_context(paths, self.repo)
        state = lambda name: paths[os.path.join(self.repo, *name.split("/"))].first
        self.assertEqual(state("packages/a/package.json"), "input")
        self.assertEqual(state("packages/a/tsconfig.json"), "input")
        self.assertEqual(state("tsconfig.base.json"), "input")
        self.assertEqual(state("node_modules/@tsconfig/strict/tsconfig.json"), "input")
        self.assertEqual(state("packages/a/src/tsconfig.json"), "missing")
        self.assertEqual(state("packages/package.json"), "missing")
        self.assertEqual(state("package-lock.json"), "input")
        self.assertEqual(state("node_modules/.package-lock.json"), "missing")

    def test_files_the_command_made_stay_its_products(self) -> None:
        source = self.write("src/x.ts", "")
        made = self.write("src/tsconfig.json")
        paths = {source: click_syscall_trace.PathState("input", {"read"}),
                 made: click_syscall_trace.PathState("produced")}
        runtime.add_resolution_context(paths, self.repo)
        self.assertEqual(paths[made].first, "produced")

    def test_native_addons_are_named_by_package_and_resolvers_are_known(self) -> None:
        paths = {
            os.path.join(self.repo, "node_modules", "@rolldown", "binding-win32-x64-msvc", "x.node"):
                click_syscall_trace.PathState("input", {"read"}),
            os.path.join(self.repo, "node_modules", "better-sqlite3", "build", "sqlite.node"):
                click_syscall_trace.PathState("input", {"read"}),
        }
        addons = runtime.native_addons(paths)
        self.assertEqual(addons, ["@rolldown/binding-win32-x64-msvc", "better-sqlite3"])
        self.assertEqual([runtime.resolver_addon(name) for name in addons], [True, False])


class ClassifyTests(unittest.TestCase):
    def member(self, pid: int, image: str | None, command_line: str = "") -> object:
        return type("Member", (), {"pid": pid, "image": image, "command_line": command_line})()

    def test_observed_node_processes_and_quiet_launchers_are_covered(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            members = [
                self.member(1, r"C:\Program Files\nodejs\node.exe", "node main.js"),
                self.member(2, r"C:\Windows\System32\conhost.exe", "conhost.exe 0xffffffff"),
                self.member(3, r"C:\Program Files\Git\usr\bin\dirname.exe", "dirname /c/x/y"),
                self.member(4, r"C:\Program Files\Git\usr\bin\sed.exe", "sed -e s,a,b,g"),
                self.member(5, r"C:\repo\node_modules\@esbuild\win32-x64\esbuild.exe", "esbuild --service=0.25 --ping"),
            ]
            reasons, reads, esbuild = runtime.classify(members, {1}, cwd=cwd, path_value="")
        self.assertEqual(reasons, [])
        self.assertEqual(reads, [])
        self.assertTrue(esbuild)

    def test_text_tools_read_the_files_they_name(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            Path(cwd, "data.txt").write_text("x", encoding="utf-8")
            Path(cwd, "sub").mkdir()
            reasons, reads, _ = runtime.classify(
                [self.member(2, r"C:\Program Files\Git\usr\bin\grep.exe", "grep -q ready data.txt")],
                set(), cwd=cwd, path_value="")
            self.assertEqual((reasons, reads), ([], [os.path.join(cwd, "data.txt")]))
            reasons, _, _ = runtime.classify(
                [self.member(2, r"C:\Program Files\Git\usr\bin\grep.exe", "grep -r ready sub")],
                set(), cwd=cwd, path_value="")
            self.assertEqual(reasons, ["unobserved-program:grep sub"])

    def test_other_programs_and_unobserved_node_make_it_volatile(self) -> None:
        members = [
            self.member(2, r"C:\Program Files\Git\cmd\git.exe", "git status"),
            self.member(3, r"C:\Program Files\nodejs\node.exe", "node --no-node-options x.js"),
            self.member(4, None),
            self.member(5, r"C:\Windows\System32\bash.exe", "bash -c ls"),
        ]
        reasons, _, _ = runtime.classify(members, set(), cwd=tempfile.gettempdir(), path_value="")
        self.assertEqual(reasons, ["unobserved-program:git.exe", "unobserved-node:3", "unidentified-process:4",
                                   "unobserved-program:bash.exe"])

    @unittest.skipUnless(os.name == "nt", "cmd.exe resolves its command through PATHEXT")
    def test_cmd_runs_npm_shims_but_not_other_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            shim = Path(cwd, "node_modules", ".bin", "vitest.cmd")
            shim.parent.mkdir(parents=True)
            shim.write_text("@ECHO off\r\n", encoding="utf-8")
            Path(cwd, "build.bat").write_text("@type secret.txt\r\n", encoding="utf-8")
            path_value = str(shim.parent)
            cmd = r"C:\Windows\System32\cmd.exe"
            reasons, reads, _ = runtime.classify(
                [self.member(2, cmd, 'C:\\Windows\\system32\\cmd.exe /d /s /c "vitest run"')], set(),
                cwd=cwd, path_value=path_value)
            self.assertEqual(reasons, [])
            self.assertEqual([os.path.normcase(item) for item in reads], [os.path.normcase(str(shim))])
            reasons, _, _ = runtime.classify(
                [self.member(2, cmd, 'cmd.exe /c build.bat')], set(), cwd=cwd, path_value=path_value)
            self.assertEqual(reasons, ["unobserved-program:cmd.exe build.bat"])


@unittest.skipUnless(os.name == "nt" and NODE, "needs Windows and node")
class WindowsJobTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(os.path.realpath(temporary.name))  # long names, as records spell them

    def observe(self, *argv: str):
        return runtime.observe(list(argv), cwd=self.root, repo=str(self.root),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout_seconds=120)

    def test_node_started_through_cmd_is_covered(self) -> None:
        (self.root / "data.txt").write_text("x", encoding="utf-8")
        # A script, not `node -e "..."`: cmd.exe does not unescape \" the way node's argv parser does.
        (self.root / "read.js").write_text("require('fs').readFileSync('data.txt');\n", encoding="utf-8")
        observation = self.observe("cmd", "/d", "/c", "node", "read.js")
        self.assertEqual(observation.exit_code, 0)
        self.assertEqual(observation.volatile_reasons, [])
        states = _states(observation, self.root)
        self.assertEqual(states.get("data.txt", ("",))[0], "input", sorted(states))
        self.assertGreaterEqual(observation.process_count, 2)

    def test_a_program_that_is_not_node_makes_the_observation_volatile(self) -> None:
        (self.root / "data.txt").write_text("ready", encoding="utf-8")
        observation = self.observe(
            "node", "-e", "require('child_process').execFileSync('findstr', ['ready', 'data.txt'])")
        self.assertEqual(observation.exit_code, 0)
        self.assertIn("unobserved-program:findstr.exe", observation.volatile_reasons)

    def test_a_timeout_ends_every_process_of_the_job(self) -> None:
        observation = runtime.observe(
            ["node", "-e", "require('child_process').spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)']);"
                           "setInterval(() => {}, 1000)"],
            cwd=self.root, repo=str(self.root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout_seconds=3)
        self.assertIn("timed-out", observation.volatile_reasons)
        self.assertNotEqual(observation.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
