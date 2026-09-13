"""Real V8 call observations, realm/worker coverage and no invented authority."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from hooks import click_node_observer as observer
from hooks import click_dependency_cache as dependencies
from tests.click_gate_test_support import ClickGateTestCase, CLICK_EVIDENCE, CLICK_OBSERVER_CONTROL


class NodeObservationBoundaryTests(unittest.TestCase):
    def test_missing_engine_coverage_cannot_be_promoted_by_caller_fields(self):
        value = observer.empty("unsupported-runtime")
        self.assertTrue(observer.valid(value))
        self.assertFalse(dependencies.authoritative_dependency_observation_is_complete(value))
        for key in ("inputs_complete", "reuse_authorized", "capture_complete"):
            changed = copy.deepcopy(value); changed[key] = True
            self.assertFalse(observer.valid(changed), key)
        for counts in ({"clock": 1}, {key: True for key in observer.CATEGORIES},
                       {key: observer.MAX_COUNT + 1 for key in observer.CATEGORIES}):
            changed = copy.deepcopy(value); changed["counts"] = counts
            self.assertFalse(observer.valid(changed))

    def test_node_test_scheduler_and_explicit_preload_are_preserved(self):
        for argv, environment in ((["node", "--test", "test.cjs"], {}),
                                   (["node", "test.cjs"], {"NODE_OPTIONS": "--require owner.cjs"}),
                                   (["node", "--require=owner.cjs", "test.cjs"], {})):
            with self.subTest(argv=argv, environment=environment):
                collector = observer.Collector(argv, environment, Path.cwd())
                try:
                    self.assertEqual(collector.environment, environment)
                    self.assertIsNone(collector.child)
                    self.assertTrue(observer.valid(collector.finish()))
                finally:
                    collector.close()


@unittest.skipUnless(sys.platform in observer.PROFILES and shutil.which("node"), "native Node inspector profile for this host")
class RealNodeObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if subprocess.check_output([cls.node, "--version"]).strip().decode() != observer.VERSION:
            raise unittest.SkipTest("versioned Node input profile unavailable")
        cls.companion = observer.click_node_state.prepare(Path(cls.node).resolve(), Path.cwd(), dict(os.environ))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="click-node-case-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def execute(self, code, *arguments):
        script = self.root / "entry.cjs"
        script.write_text(code)
        argv = [self.node, str(script), *arguments]
        collector = observer.Collector(argv, dict(os.environ), self.root)
        self.assertIsNotNone(collector.child, collector.record)
        directory = collector.location.name
        controller = collector.child
        try:
            result = subprocess.run(argv, cwd=self.root, env=collector.environment, capture_output=True, timeout=25)
            self.record = collector.finish()
        finally:
            collector.close()
        self.assertTrue(observer.valid(self.record), self.record)
        self.assertFalse(self.record["inputs_complete"])
        self.assertFalse(self.record["reuse_authorized"])
        self.assertIn("engine-input-coverage-incomplete", self.record["reasons"])
        self.assertIsNotNone(controller.poll(), "observer process was stranded")
        # The controller exits by itself once it has reported; a terminated
        # controller would report a signal exit instead.
        self.assertEqual(controller.returncode, 0, "observer process did not exit after reporting")
        self.assertFalse(Path(directory).exists(), "transient observer directory was retained")
        self.assertNotIn("ws://", json.dumps(self.record))
        self.assertEqual(self.record["installed"], self.record["contexts"], self.record)
        self.assertGreater(self.record["installed"], 0, self.record)
        return result

    def test_saved_native_references_and_original_arguments(self):
        result = self.execute("""
const assert = require('node:assert/strict');
const saved = Date.now; Date.now = () => 7;
assert.equal(Date.now(), 7); assert.ok(saved() > 7);
Math.random(); const a = new Int32Array(new SharedArrayBuffer(4)); Atomics.load(a, 0);
console.log('ONLY-ONCE', process.argv.slice(2).join(','));
""", "literal-argument")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(b"ONLY-ONCE literal-argument"), 1)
        for category in ("clock", "random", "shared-memory"):
            self.assertEqual(self.record["counts"][category], 1, self.record)

    def test_records_consumed_values_without_calling_the_input_again(self):
        result = self.execute("""
const view = new Int32Array(new SharedArrayBuffer(4));
const tick = Date.now(); const random = Math.random();
const old = Atomics.add(view, 0, 7); const current = Atomics.load(view, 0);
console.log('VALUES', JSON.stringify({
  'date-now': String(tick), 'math-random': String(random),
  'atomics-add': String(old), 'atomics-load': String(current)
}));
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        consumed = json.loads(next(line[7:] for line in result.stdout.decode().splitlines() if line.startswith("VALUES ")))
        for source, value in consumed.items():
            expected = hashlib.sha256(json.dumps({"type": "number", "value": value}, separators=(",", ":")).encode()).hexdigest()
            observed = self.record["values"][source]
            self.assertEqual(observed["count"], 1, self.record)
            self.assertEqual(observed["last_value_digest"], expected, (source, observed))
        self.assertEqual(consumed["atomics-add"], "0")
        self.assertEqual(consumed["atomics-load"], "7")
        if shutil.which("c++") and observer.digest_file(Path(self.node)) == observer.click_node_state.NODE_DIGEST:
            self.assertTrue(self.record["state_companion_digest"], self.record)
            for source in ("math-random", "atomics-add", "atomics-load"):
                self.assertEqual(self.record["values"][source]["state_count"], 1, self.record)

    def test_new_realm_is_observed_before_its_first_sensitive_call(self):
        result = self.execute("require('node:vm').runInNewContext(\"if('__click_native_state_bridge_v1' in globalThis)throw Error('bridge leaked');Date.now();Math.random();new SharedArrayBuffer(4)\");")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(self.record["contexts"], 2, self.record)
        for category in ("clock", "random", "shared-memory"):
            self.assertEqual(self.record["counts"][category], 1, self.record)
        if self.record["state_companion_digest"]:
            self.assertEqual(self.record["values"]["math-random"]["state_count"], 1, self.record)
            self.assertNotIn("input-value-setup-incomplete", self.record["reasons"])

    def test_atomic_coercion_and_thrown_identity_are_not_repeated_or_replaced(self):
        result = self.execute("""
const assert = require('node:assert/strict');
const view = new Int32Array(new SharedArrayBuffer(4));
let calls = 0;
const index = {valueOf() { calls++; return 0; }};
assert.equal(Atomics.add(view, index, 7), 0);
assert.equal(calls, 1);
const failure = new Error('exact instance');
const invalid = {valueOf() { calls++; throw failure; }};
assert.throws(() => Atomics.load(view, invalid), error => error === failure);
assert.equal(calls, 2);
for (const method of [Math.random, Date.now, Atomics.load])
  assert.throws(() => new method(), TypeError);
console.log('SEMANTICS-PRESERVED');
""")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count(b"SEMANTICS-PRESERVED"), 1)
        self.assertEqual(self.record["values"]["atomics-add"]["count"], 1, self.record)
        self.assertEqual(self.record["values"]["atomics-load"]["count"], 1, self.record)
        self.assertIn("input-exception-state-incomplete", self.record["reasons"])
        self.assertIn("input-coercion-state-incomplete", self.record["reasons"])
        self.assertNotIn("input-values-unavailable", self.record["reasons"])

    def test_vm_owner_field_is_not_deleted_or_treated_as_native_state(self):
        if not self.companion:
            self.skipTest("exact-ABI native state companion unavailable on this host")
        result = self.execute("""
const vm = require('node:vm');
for (const value of ['owner', {get randomState(){throw Error('owner getter invoked');}}]) {
 const sandbox = {__click_native_state_bridge_v1:value, expected:value};
 vm.runInNewContext("if(__click_native_state_bridge_v1 !== expected)throw Error('owner field changed');Math.random();", sandbox);
}
console.log('OWNER-PRESERVED');
""")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count(b"OWNER-PRESERVED"), 1)
        self.assertIn("input-value-setup-incomplete", self.record["reasons"])

    def test_worker_first_calls_are_captured_and_failure_is_unchanged(self):
        (self.root / "worker.cjs").write_text("Math.random();new SharedArrayBuffer(4);require('node:worker_threads').parentPort.postMessage(42);")
        result = self.execute("const {Worker}=require('node:worker_threads');new Worker('./worker.cjs').on('message',value=>{console.log('ONLY-ONCE',value);process.exitCode=7;});")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout.count(b"ONLY-ONCE 42"), 1)
        self.assertGreaterEqual(self.record["workers"], 1, self.record)
        self.assertEqual(self.record["counts"]["random"], 1, self.record)
        self.assertEqual(self.record["counts"]["shared-memory"], 1, self.record)

    def test_silent_fork_uses_independent_control_channel(self):
        (self.root / "child.cjs").write_text("Math.random();process.send('done');")
        result = self.execute("require('node:child_process').fork('./child.cjs',[],{silent:true}).on('message',()=>console.log('ONLY-ONCE'));")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(b"ONLY-ONCE"), 1)
        self.assertGreaterEqual(self.record["sessions"], 2, self.record)
        self.assertEqual(self.record["counts"]["random"], 1, self.record)

    def test_plain_runtime_values_do_not_acquire_authority(self):
        result = self.execute("if (process.pid <= 0) process.exitCode=1;")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(dependencies.authoritative_dependency_observation_is_complete(self.record))

    def test_nested_node_test_retains_requested_concurrency(self):
        for name, other in (("one", "two"), ("two", "one")):
            (self.root / (name + ".cjs")).write_text(
                "const {test}=require('node:test');const fs=require('node:fs');"
                "test('concurrent',async()=>{"
                f"fs.writeFileSync('{name}.ready','');const end=Date.now()+4000;"
                f"while(!fs.existsSync('{other}.ready')){{if(Date.now()>end)throw Error('test runner serialized');await new Promise(r=>setTimeout(r,10));}}"
                "});")
        result = self.execute("const result=require('node:child_process').spawnSync(process.execPath,['--test','--test-concurrency=2','one.cjs','two.cjs'],{stdio:'inherit'});process.exitCode=result.status;")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(b"forces running at a concurrency of 1", result.stdout + result.stderr)

    def test_bootstrap_timeout_continues_original_check_once(self):
        script = self.root / "entry.cjs"; script.write_text("console.log('ONLY-ONCE')")
        collector = observer.Collector([self.node, str(script)], dict(os.environ), self.root)
        self.assertIsNotNone(collector.child, collector.record)
        try:
            # Simulate a controller lost after preparation, before target startup.
            collector.child.kill(); collector.child.wait(timeout=3)
            result = subprocess.run([self.node, str(script)], env=collector.environment,
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count(b"ONLY-ONCE"), 1)
            self.assertFalse(collector.finish()["capture_complete"])
        finally:
            collector.close()


@unittest.skipUnless(sys.platform == "linux" and shutil.which("node") and shutil.which("npm"), "native Node/npm diagnostic integration")
class NodeDiagnosticHookTests(ClickGateTestCase):
    maxDiff = None
    def test_default_capture_allows_policy_reuse_and_reruns_changed_inputs(self):
        self.default_capture_reuse()

    def test_default_vitest_capture_and_automatic_policy_reuse(self):
        self.default_capture_reuse("vitest-v5")

    def test_default_jest_capture_and_automatic_policy_reuse(self):
        self.default_capture_reuse("jest-v30")

    def default_capture_reuse(self, framework=None):
        if subprocess.check_output(["node", "--version"]).strip().decode() != observer.VERSION:
            self.skipTest("versioned Node input profile unavailable")
        package = {}
        extension = ".cjs"
        runner = "node"
        if framework:
            fixture = Path(__file__).parent / "fixtures" / framework
            if not (fixture / "node_modules").is_dir():
                self.skipTest("pinned framework fixture is not installed")
            shutil.copytree(fixture, self.workspace, dirs_exist_ok=True, symlinks=True)
            package = json.loads((self.workspace / "package.json").read_text())
            extension = ".test.js" if framework == "vitest-v5" else ".test.cjs"
            runner = "vitest run" if framework == "vitest-v5" else "jest --maxWorkers=2 --runTestsByPath"
        package["scripts"] = {"test:" + name: runner + " " + name + extension for name in ("alpha", "beta")}
        (self.workspace / "package.json").write_text(json.dumps(package))
        (self.workspace / ".gitignore").write_text("node_modules/\nextra-input.cfg\n")
        (self.workspace / "shared.cjs").write_text("module.exports=1;\n")
        for name in ("alpha", "beta"):
            code = f"const VALUE=1;require('node:assert').ok(require('./shared.cjs')>0);console.log('ran-{name}',VALUE);\n"
            if framework == "vitest-v5":
                code = f"import {{expect,test}} from 'vitest';import shared from './shared.cjs';const VALUE=1;test('value',()=>{{expect(shared).toBeGreaterThan(0);console.log('ran-{name}',VALUE);}});\n"
            elif framework == "jest-v30":
                code = f"const shared=require('./shared.cjs');const VALUE=1;test('value',()=>{{expect(shared).toBeGreaterThan(0);console.log('ran-{name}',VALUE);}});\n"
            (self.workspace / (name + extension)).write_text(code)
        commands = [["npm", "run", "test:" + name] for name in ("alpha", "beta")]
        if framework:
            commands = [["npx", "--no-install", *runner.split(), name + extension] for name in ("alpha", "beta")]
        (self.workspace / ".click").mkdir()
        (self.workspace / ".click/evidence-reuse.json").write_text(json.dumps({
            "version": 2, "entries": [{
                "checks": [command], "reuse_if_only_changed": ["*.cjs", "*.js"],
                "inputs": [name + extension, "shared.cjs", "package.json", "package-lock.json", "extra-input.cfg"],
            } for name, command in zip(("alpha", "beta"), commands)],
        }))
        self.initialize_git(".")
        keys = [CLICK_EVIDENCE.evidence_key(name) for name in ("ALPHA", "BETA")]

        def run(turn):
            payload = self.verify_gate(commands, turn, evidence_ids=["ALPHA", "BETA"])
            self.assertIn("updatedInput", payload["hookSpecificOutput"], payload)
            result = self.run_rewritten(payload)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            state = json.loads(next((self.plugin_data / "gate-state").glob("session-contract-*.json")).read_text())
            decisions = {row["source_key"]: row["decision"] for row in state["verification"]["incremental_plan"]["decisions"]}
            for name, key in zip(("alpha", "beta"), keys):
                executed = int(decisions[key] in {"run", "not-evaluable"})
                if framework:
                    # Framework reporters may route/suppress successful console
                    # output. The admitted runner emits one command start.
                    self.assertEqual(result.stdout.count(":" + name.upper() + ":broad]"), executed, result.stdout)
                else:
                    self.assertEqual(result.stdout.count("ran-" + name), executed, result.stdout)
            return state, decisions

        first, _ = run("turn-1")
        self.assertEqual(CLICK_OBSERVER_CONTROL.mode(first["verification"]), "auto")
        for key in keys:
            runtime = first["verification"]["framework_observations"][key]["runtime"]
            self.assertGreater(runtime["installed"], 0, runtime)
            self.assertNotIn("not-requested", runtime["reasons"])
            # Actual authority remains in the host-verified owner input receipt.
            source = first["evidence_state"]["sources"][key]
            self.assertEqual(source["verified_safe_change_receipt"]["provider"], "repository-scoped-input-policy-v2")
            self.assertEqual(source["verified_dependency_provider"], "")

        def change(name, old, new, turn):
            path = self.workspace / name
            patch = f"*** Begin Patch\n*** Update File: {path}\n@@\n-{old}\n+{new}\n*** End Patch"
            self.pre_tool("apply_patch", patch, turn, tool_use_id=turn)
            path.write_text(path.read_text().replace(old, new))
            self.tool_hook("post-tool", "apply_patch", {"patch": patch}, turn_id=turn, tool_use_id=turn)

        change("alpha" + extension, "VALUE=1", "VALUE=2", "turn-2")
        _, changed = run("turn-2")
        self.assertEqual(changed, {keys[0]: "run", keys[1]: "reuse-safe-change"}, json.dumps({
            "decisions": changed,
            "plan": json.loads(next((self.plugin_data / "gate-state").glob("session-contract-*.json")).read_text())["verification"]["incremental_plan"],
        }))
        # This bypasses Hook/Git revision reporting. The final receipt boundary
        # must still notice an absent ignored file becoming a real input.
        (self.workspace / "extra-input.cfg").write_text("new")
        _, lock_changed = run("turn-2")
        self.assertTrue(all(value in {"run", "not-evaluable"} for value in lock_changed.values()), lock_changed)
        change("shared.cjs", "exports=1", "exports=2", "turn-3")
        _, shared = run("turn-3")
        self.assertTrue(all(value in {"run", "not-evaluable"} for value in shared.values()), shared)

    def test_explicit_runtime_mode_is_bound_once_and_never_grants_reuse(self):
        if subprocess.check_output(["node", "--version"]).strip().decode() != observer.VERSION:
            self.skipTest("versioned Node input profile unavailable")
        (self.workspace / "package.json").write_text(json.dumps({"scripts": {"test": "node check.cjs"}}))
        code = "const VALUE=1;Date.now();Math.random();new SharedArrayBuffer(4);console.log('ONLY-ONCE',VALUE);"
        (self.workspace / "check.cjs").write_text(code)
        self.initialize_git("package.json", "check.cjs")
        selected = self.run_rewritten(self.pre_tool("Bash", "click-gate observer runtime", "turn-1"))
        self.assertEqual(selected.returncode, 0, selected.stderr)
        argv = ["npm", "test"]
        run = self.run_rewritten(self.verify_gate([argv], "turn-1", evidence_ids=["NODE_RUNTIME"]))
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(run.stdout.count("ONLY-ONCE 1"), 1)
        state_path = next((self.plugin_data / "gate-state").glob("session-contract-*.json"))
        state = json.loads(state_path.read_text())
        self.assertEqual(CLICK_OBSERVER_CONTROL.mode(state["verification"]), "runtime")
        self.assertFalse(CLICK_OBSERVER_CONTROL.projection(state["verification"])["reuse_authorized"])
        key = CLICK_EVIDENCE.evidence_key("NODE_RUNTIME")
        record = state["verification"]["framework_observations"][key]
        self.assertEqual(record["runtime"]["counts"]["random"], 1, record)
        self.assertFalse(record["runtime_inputs_complete"])
        self.assertEqual(state["evidence_state"]["sources"][key]["verified_dependency_provider"], "")
        patch = "*** Begin Patch\n*** Update File: check.cjs\n@@\n-" + code + "\n+" + code.replace("VALUE=1", "VALUE=2") + "\n*** End Patch"
        self.pre_tool("apply_patch", patch, "turn-2", tool_use_id="runtime-change")
        (self.workspace / "check.cjs").write_text(code.replace("VALUE=1", "VALUE=2"))
        self.tool_hook("post-tool", "apply_patch", {"patch": patch}, turn_id="turn-2", tool_use_id="runtime-change")
        run = self.run_rewritten(self.verify_gate([argv], "turn-2", evidence_ids=["NODE_RUNTIME"]))
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(run.stdout.count("ONLY-ONCE 2"), 1)
        self.assertNotIn("[Click framework observer]", run.stdout)


@unittest.skipUnless(sys.platform == "linux" and shutil.which("node") and shutil.which("c++"), "native state reader requires Linux toolchain")
class NativeNodeStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = Path(shutil.which("node")).resolve()
        cls.native = observer.click_node_state.prepare(cls.node, Path.cwd(), dict(os.environ))
        if not cls.native:
            raise unittest.SkipTest("exact-ABI native state profile unavailable")

    def run_native(self, source):
        result = subprocess.run([str(self.node), "--random-seed=12345", "-e", source, str(self.native[0])],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_random_cache_rollover_and_independent_vm_state(self):
        value = self.run_native("""
const state = require(process.argv[1]);
const vm = require('node:vm');
const other = vm.runInNewContext('Math.random');
const initial = state.randomState(other);
const rows = [];
for(let index=0; index<66; index++) { Math.random(); rows.push(state.randomState(Math.random)); }
const unchanged = state.randomState(other);
other(); const changed = state.randomState(other);
console.log(JSON.stringify({initial, unchanged, changed, rows}));
""")
        self.assertEqual(value["initial"], value["unchanged"])
        self.assertEqual(value["initial"].split(":")[1:4], ["0", "0" * 16, "0" * 16])
        self.assertEqual(value["changed"].split(":")[1], "63")
        self.assertEqual([int(row.split(":")[1]) for row in value["rows"]], list(range(63, -1, -1)) + [63, 62])
        # Refilling changes the generator/cache; consuming cached entries does not.
        self.assertEqual(value["rows"][0].split(":")[2:], value["rows"][63].split(":")[2:])
        self.assertNotEqual(value["rows"][63].split(":")[2:], value["rows"][64].split(":")[2:])

    def test_shared_backing_identity_and_actual_bytes_across_worker(self):
        value = self.run_native("""
const state = require(process.argv[1]);
const {Worker} = require('node:worker_threads');
const shared = new SharedArrayBuffer(4), view = new Int32Array(shared);
const before = state.memoryState(shared);
const worker = new Worker(`const {parentPort,workerData}=require('node:worker_threads');
const state=require(workerData.addon),view=new Int32Array(workerData.shared);
const before=state.memoryState(workerData.shared);Atomics.store(view,0,7);
parentPort.postMessage({before,after:state.memoryState(workerData.shared)});`,
{eval:true,workerData:{addon:process.argv[1],shared}});
worker.on('message', message=>console.log(JSON.stringify({before,worker:message,after:state.memoryState(view.buffer)})));
""")
        self.assertEqual(value["before"], value["worker"]["before"])
        self.assertEqual(value["after"], value["worker"]["after"])
        self.assertEqual(value["before"].split(":")[:3], value["after"].split(":")[:3])
        self.assertEqual(value["before"].split(":")[-1], hashlib.sha256(bytes(4)).hexdigest())
        self.assertEqual(value["after"].split(":")[-1], hashlib.sha256((7).to_bytes(4, sys.byteorder)).hexdigest())

    def test_native_reader_rejects_unrecognized_binary_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "node"
            fake.write_text("#!/bin/sh\nexit 99\n")
            fake.chmod(0o700)
            self.assertIsNone(observer.click_node_state.prepare(fake, Path.cwd(), dict(os.environ)))


if __name__ == "__main__":
    unittest.main()
