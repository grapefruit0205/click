"""Conditional confidence is explicit and never upgraded to complete authority."""
import copy
import json
import secrets
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from hooks import click_conditional_observer as conditional
from hooks import click_dependency_cache as dependencies
from hooks import click_framework_observer as framework
from hooks import click_node_observer as node
from hooks import click_verification_reuse as reuse
from hooks import click_incremental as incremental
from tests import click_gate_test_support as support


class ConditionalSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'input.txt').write_text('one')
        self.rows = [{'path':'input.txt','kind':'file','operations':['read']},
                     {'path':'optional.cfg','kind':'missing','operations':['metadata']}]

    def test_ignored_missing_and_unrelated_inputs(self):
        before = conditional.snapshot(self.root, self.rows)
        (self.root / 'unrelated.txt').write_text('unrelated')
        self.assertTrue(conditional.current(self.root, before))
        (self.root / 'optional.cfg').write_text('present')
        self.assertFalse(conditional.current(self.root, before))
        (self.root / 'optional.cfg').unlink()
        (self.root / 'input.txt').write_text('two')
        self.assertFalse(conditional.current(self.root, before))

    def test_read_then_overwrite_does_not_become_reusable(self):
        before = conditional.snapshot(self.root, self.rows)
        (self.root / 'input.txt').write_text('changed by test')
        self.assertFalse(conditional.current(self.root, before))

    def test_missing_below_symlink_is_not_a_local_snapshot(self):
        (self.root / 'alias').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            conditional.snapshot(self.root, [{'path':'alias/missing','kind':'missing','operations':['read']}])

    @unittest.skipIf(os.name == "nt", "conditional external receipts require POSIX paths")
    def test_external_alias_and_target_are_both_bound(self):
        (self.root / 'alias').symlink_to(self.root / 'input.txt')
        before = conditional.external_snapshot([{'path':str(self.root / 'alias'),'kind':'file','operations':['read']}])
        self.assertTrue(conditional.external_valid(before))
        (self.root / 'input.txt').write_text('two')
        self.assertNotEqual(conditional.external_snapshot(before), before)

    def test_readonly_opened_directory_is_bound_without_guessing_enumeration(self):
        directory=self.root/'locale'
        directory.mkdir()
        rows=[{'path':str(directory),'kind':'file','operations':['read']}]
        before=conditional.external_snapshot(rows)
        self.assertEqual(conditional.external_snapshot(rows),before)
        (directory/'new').write_text('new')
        self.assertNotEqual(conditional.external_snapshot(rows),before)

    def test_conditional_reason_cannot_masquerade_as_complete_authority(self):
        value=incremental.decision(source_key='a'*64,decision='reuse-dependency',
            reason_code='conditional-observed-inputs-current',current_revision=1,
            previous_revision=0,check_digest='b'*64,authority_source='conditional-js-observation',
            estimated_avoided_ms=None)
        self.assertTrue(incremental.decision_is_valid(value))
        value['authority_source']='runtime-dependency-observation'
        self.assertFalse(incremental.decision_is_valid(value))

    def test_malformed_projection_never_raises_or_becomes_eligible(self):
        for value in (None, {}, {'conditional_capture': {'inputs':None,'external':[]}},
                      {'conditional_capture': {'inputs':[{}],'external':[]}}):
            self.assertFalse(conditional.eligible_record(value))

    @unittest.skipUnless(sys.platform == 'linux', 'the projection reads a Linux strace capture')
    def test_the_allocator_probe_after_the_acknowledgement_still_projects(self):
        directory = self.root / 'observer'
        base = f'100 execve("/usr/bin/node", ["node"], 0x1) = 0\n100 access("{directory}/ready-100", F_OK) = 0\n'
        # glibc reads the overcommit policy on the first large allocation, on
        # either side of the acknowledgement; it is the allocator's probe.
        probe = '100 openat(AT_FDCWD, "/proc/sys/vm/overcommit_memory", O_RDONLY) = 3</proc/sys/vm/overcommit_memory>'
        raw = (base + probe + '\n100 exit_group(0) = ?\n100 +++ exited with 0 +++\n').encode()
        projection = conditional.project_capture(raw, project=self.root, cwd=self.root, directory=directory)
        self.assertIsNotNone(projection)
        self.assertNotIn('overcommit', str(projection))

    def test_projection_keeps_application_proc_read_and_unknown_calls_ineligible(self):
        directory = self.root / 'observer'
        base = f'100 execve("/usr/bin/node", ["node"], 0x1) = 0\n100 access("{directory}/ready-100", F_OK) = 0\n'
        for line in ('100 openat(AT_FDCWD, "/proc/meminfo", O_RDONLY) = 3</proc/meminfo>',
                     '100 mystery_input(1) = 0'):
            raw=(base+line+'\n100 exit_group(0) = ?\n100 +++ exited with 0 +++\n').encode()
            self.assertIsNone(conditional.project_capture(raw, project=self.root, cwd=self.root, directory=directory))


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('node') and shutil.which('strace'), 'Linux Node and strace required')
class RealConditionalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which('node')
        if subprocess.check_output([cls.node,'--version']).strip().decode() != node.VERSION:
            raise unittest.SkipTest('supported Node profile unavailable')

    def setUp(self):
        # Preserve bounded facts at the exact projection rejection, without
        # retaining raw syscall arguments, external paths or environment values.
        self.projection_diagnostics = {}
        project_capture = conditional.project_capture

        def capture(raw, **kwargs):
            def diagnostic(frame, event, result):
                if frame.f_code is project_capture.__code__ and event == "return" and result is None:
                    values = frame.f_locals
                    parsed = values.get("parsed")
                    path = values.get("path")
                    self.projection_diagnostics = {
                        "return_line": frame.f_lineno,
                        "handshake_observed": values.get("started"),
                        "unresolved": getattr(parsed, "unresolved_event_count", None),
                        "process_tree_complete": getattr(parsed, "process_tree_complete", None),
                        "dynamic_input": str(path) if path and Path(path).parts[1:2] in [("proc",), ("sys",), ("dev",)] else None,
                    }
            previous_profile = sys.getprofile()
            sys.setprofile(diagnostic)
            try:
                return project_capture(raw, **kwargs)
            finally:
                sys.setprofile(previous_profile)

        patcher = support.mock.patch.object(conditional, "project_capture", side_effect=capture)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_dynamic_esm_config_and_ignored_inputs_have_separate_fingerprints(self):
        with tempfile.TemporaryDirectory(prefix="click-js-inputs-") as tmp:
            root = Path(tmp)
            files = {
                ".gitignore": "local.cfg\n",
                "project.json": '{"module":"./value.mjs"}',
                "value.mjs": "export const value = 1;\n",
                "local.cfg": "ready",
                "unrelated.txt": "unchanged",
                "entry.mjs": (
                    "import fs from 'node:fs';\n"
                    "const config = JSON.parse(fs.readFileSync('project.json', 'utf8'));\n"
                    "const {value} = await import(config.module);\n"
                    "if(value !== 1 || fs.readFileSync('local.cfg','utf8') !== 'ready') process.exitCode=1;\n"
                ),
            }
            for name, content in files.items():
                (root / name).write_text(content)
            context = {key: "a" * 64 for key in dependencies.AUTHORITATIVE_BINDING_FIELDS if key != "execution_digest"}
            context["mutation_revision"] = 0
            previous = None
            secret = secrets.token_hex(32)
            for _ in range(2):
                result = framework.run_command(
                    [self.node, str(root / "entry.mjs")], workspace=root, observation_root=root,
                    environment=dict(os.environ), evidence_key="a" * 64, check_digest="a" * 64,
                    mutation_revision=0, execute_unobserved=lambda: 99,
                    resolve_backend=lambda name, **kw: (shutil.which(name), ""),
                    digest_file=node.digest_file, capture_output={}, previous=previous,
                    conditional_context=context, conditional_secret=secret,
                )
                self.assertEqual(result.exit_code, 0)
                self.assertTrue(conditional.eligible_record(result.record), (self.projection_diagnostics, result.record))
                previous = result.record
            observed = conditional.verify(result.envelope, secret=secret, expected_binding=context)
            self.assertIsNotNone(observed)
            rows = {row["path"]: row for row in observed["inputs"]}
            (root / "unrelated.txt").write_text("unrelated change")
            self.assertTrue(conditional.current(root, observed["inputs"]))
            for name in ("project.json", "value.mjs", "local.cfg"):
                with self.subTest(input=name):
                    self.assertIn(name, rows)
                    self.assertTrue(conditional.current(root, [rows[name]]))
                    (root / name).write_text(files[name] + "\n")
                    self.assertFalse(conditional.current(root, [rows[name]]))

    def test_real_execution_attestation_and_per_input_invalidation(self):
        with tempfile.TemporaryDirectory(prefix='click-js-conditional-') as tmp:
            root=Path(tmp)
            (root/'input.txt').write_text('ok')
            (root/'check.cjs').write_text("const fs=require('node:fs');if(fs.readFileSync('input.txt','utf8')!=='ok')process.exitCode=1;console.log('RAN-ONCE');\n")
            context={key:'a'*64 for key in dependencies.AUTHORITATIVE_BINDING_FIELDS if key!='execution_digest'}
            context['mutation_revision']=0
            runner_token=secrets.token_hex(32)
            previous=None
            for learning in (True,False):
                output={}
                execution=framework.run_command([self.node,str(root/'check.cjs')],workspace=root,observation_root=root,
                    environment=dict(os.environ),evidence_key='a'*64,check_digest='a'*64,mutation_revision=0,
                    execute_unobserved=lambda:99,resolve_backend=lambda name,**kw:(shutil.which(name),''),
                    digest_file=node.digest_file,capture_output=output,previous=previous,
                    conditional_context=context,conditional_secret=runner_token)
                self.assertEqual(execution.exit_code,0)
                self.assertEqual(output['process'].stdout.data.count(b'RAN-ONCE'),1)
                self.assertTrue(framework.record_valid(execution.record),execution.record)
                self.assertTrue(conditional.eligible_record(execution.record),(self.projection_diagnostics, execution.record))
                # A refused receipt means the two runs' projections differed or
                # issue() declined for another reason; show both sides, so a
                # host-only refusal can be read from the failure alone.
                self.assertEqual(execution.envelope is None,learning,{
                    'diagnostics': self.projection_diagnostics,
                    'learning_capture': previous.get('conditional_capture') if isinstance(previous, dict) else None,
                    'binding_capture': execution.record.get('conditional_capture'),
                    'record': {key: execution.record.get(key) for key in ('capture','runtime','workers')}})
                previous=execution.record
            envelope=execution.envelope
            observation=conditional.verify(envelope,secret=runner_token,expected_binding=context)
            self.assertIsNotNone(observation)
            self.assertFalse(dependencies.authoritative_dependency_observation_is_complete(observation))
            self.assertTrue(dependencies.bound_dependency_observation_is_reusable(observation))
            binding={key:context[key] for key in dependencies.AUTHORITATIVE_CURRENT_BINDING_FIELDS}
            self.assertTrue(conditional.matches(observation,project=root,binding=binding))
            self.assertIsNone(conditional.verify(envelope,secret=secrets.token_hex(32),expected_binding=context))
            for key in ('evidence_key','environment_digest','executable_digest','shard_digest'):
                changed={**context,key:'b'*64}
                self.assertIsNone(conditional.verify(envelope,secret=runner_token,expected_binding=changed))
            forged=copy.deepcopy(envelope);forged['observation']['runtime_inputs_complete']=True
            self.assertIsNone(conditional.verify(forged,secret=runner_token,expected_binding=context))
            (root/'unrelated.txt').write_text('unrelated')
            self.assertTrue(conditional.matches(observation,project=root,binding=binding))
            (root/'input.txt').write_text('changed')
            self.assertFalse(conditional.matches(observation,project=root,binding=binding))
            source={'verified_dependency_observation':observation,'verified_dependency_provider':dependencies.AUTOMATIC_PROVIDER_NAME}
            self.assertEqual(reuse.changed_observed_inputs({'key':source},{'key'},project=root,runtime=None),{'key'})
            dynamic=copy.deepcopy(previous);dynamic['runtime']['counts']['clock']=1;dynamic['runtime']['reasons'].append('clock');dynamic['runtime']['reasons'].sort()
            self.assertFalse(conditional.eligible_record(dynamic))


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('node') and shutil.which('strace'), 'Linux Node and strace required')
class ConditionalHookTests(support.ClickGateTestCase):
    def test_default_hook_learns_then_reuses_and_reruns_changed_child(self):
        if subprocess.check_output(['node','--version']).strip().decode() != node.VERSION:
            self.skipTest('supported Node profile unavailable')
        (self.workspace/'.gitignore').write_text('local.cfg\n')
        (self.workspace/'local.cfg').write_text('ready')
        for name in ('alpha','beta'):
            source="const fs=require('node:fs'); const assert=require('node:assert/strict');\n"
            if name=='alpha': source+="assert.equal(fs.readFileSync('local.cfg','utf8').trim(),'ready');\n"
            source+=f"console.log('ran-{name}');\n"
            (self.workspace/(name+'.cjs')).write_text(source)
        # A tiny deterministic check-runner fixture exercises the existing
        # recognized Jest command boundary; actual Node observation is real.
        runner=self.workspace/'jest'
        runner.write_text('#!/usr/bin/env node\nrequire(require("node:path").resolve(process.argv[2]));\n')
        runner.chmod(0o755)
        self.initialize_git('.gitignore','alpha.cjs','beta.cjs','jest')
        commands=[[str(runner),name+'.cjs'] for name in ('alpha','beta')]
        def run(turn):
            payload=self.verify_gate(commands,turn,evidence_ids=['ALPHA','BETA'])
            self.assertIn("updatedInput",payload["hookSpecificOutput"],payload)
            result=self.run_rewritten(payload)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            state=json.loads(next((self.plugin_data/'gate-state').glob('session-contract-*.json')).read_text())
            return state,result
        run('learn-1')
        state,result=run('learn-2')
        for source in state['evidence_state']['sources'].values():
            self.assertTrue(conditional.valid(source.get('verified_dependency_observation')), result.stdout + result.stderr + '\n' + json.dumps(state['verification'].get('framework_observations')))
        state,result=run('reuse')
        decisions=state['verification']['incremental_plan']['decisions']
        self.assertTrue(all(row['authority_source']=='conditional-js-observation' for row in decisions),decisions)
        self.assertNotIn('ran-alpha',result.stdout)
        self.assertNotIn('ran-beta',result.stdout)
        self.assertIn('입력 완전성 미보증',result.stdout)
        # Git is unchanged; the ignored input's content change must still
        # invalidate only alpha. An equal-content rewrite would not: identity
        # is content, membership and mode, never timestamps or inodes.
        (self.workspace/'local.cfg').write_text('ready')
        self.assertTrue(conditional.current(self.workspace, state['evidence_state']['sources'][support.CLICK_EVIDENCE.evidence_key('ALPHA')]['verified_dependency_observation']['inputs']))
        (self.workspace/'local.cfg').write_text('ready\n')
        state,result=run('ignored-input-change')
        self.assertEqual(result.stdout.count('ran-alpha'),1,result.stdout)
        self.assertNotIn('ran-beta',result.stdout)
        unrelated=self.workspace/'unrelated.md'
        patch=f"*** Begin Patch\n*** Add File: {unrelated}\n+Unrelated note\n*** End Patch"
        self.pre_tool('apply_patch',patch,'unrelated-change',tool_use_id='unrelated-edit')
        unrelated.write_text('Unrelated note\n')
        self.tool_hook('post-tool','apply_patch',{'patch':patch},turn_id='unrelated-change',tool_use_id='unrelated-edit')
        state,result=run('unrelated-change')
        self.assertNotIn('ran-alpha',result.stdout)
        self.assertNotIn('ran-beta',result.stdout)
        decisions=state['verification']['incremental_plan']['decisions']
        self.assertTrue(all(row['decision']=='reuse-dependency' for row in decisions),decisions)
        self.assertTrue(all(row['authority_source']=='conditional-js-observation' for row in decisions),decisions)

        # Environment bindings cover the fingerprinted runtime variables: a
        # changed inherited value invalidates both commands, even when its
        # per-variable reader is unknown.
        with support.mock.patch.dict(os.environ, {"TZ": "Etc/GMT+7"}):
            state, result = run('environment-change')
        self.assertIn('ran-alpha', result.stdout)
        self.assertIn('ran-beta', result.stdout)

    def test_worker_input_runs_and_recovers_after_unsupported_code_is_removed(self):
        if subprocess.check_output(['node', '--version']).strip().decode() != node.VERSION:
            self.skipTest('supported Node profile unavailable')
        (self.workspace / '.gitignore').write_text('worker.cfg\n')
        (self.workspace / 'worker.cfg').write_text('ready')
        worker_source = (
            "const {Worker}=require('node:worker_threads');\n"
            "new Worker('./worker.cjs').on('message',value=>{console.log('ran-alpha',value)});\n"
        )
        plain_source = "console.log('ran-alpha',require('node:fs').readFileSync('worker.cfg','utf8'));\n"
        (self.workspace / 'alpha.cjs').write_text(plain_source)
        (self.workspace / 'worker.cjs').write_text(
            "require('node:worker_threads').parentPort.postMessage(require('node:fs').readFileSync('worker.cfg','utf8'));\n"
        )
        (self.workspace / 'beta.cjs').write_text("console.log('ran-beta');\n")
        runner = self.workspace / 'jest'
        runner.write_text('#!/usr/bin/env node\nrequire(require("node:path").resolve(process.argv[2]));\n')
        runner.chmod(0o755)
        self.initialize_git('.gitignore', 'alpha.cjs', 'worker.cjs', 'beta.cjs', 'jest')
        commands = [[str(runner), name + '.cjs'] for name in ('alpha', 'beta')]
        alpha_key = support.CLICK_EVIDENCE.evidence_key('ALPHA')

        def run(turn):
            payload = self.verify_gate(commands, turn, evidence_ids=['ALPHA', 'BETA'])
            result = self.run_rewritten(payload)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            state = json.loads(next((self.plugin_data / 'gate-state').glob('session-contract-*.json')).read_text())
            return state, result

        def replace_source(before, after, turn):
            target = self.workspace / 'alpha.cjs'
            patch = '\n'.join([
                '*** Begin Patch', f'*** Update File: {target}', '@@',
                *('-' + line for line in before.splitlines()),
                *('+' + line for line in after.splitlines()), '*** End Patch',
            ])
            self.pre_tool('apply_patch', patch, turn, tool_use_id=turn + '-edit')
            target.write_text(after)
            self.tool_hook('post-tool', 'apply_patch', {'patch': patch}, turn_id=turn, tool_use_id=turn + '-edit')

        run('conditional-learn-1')
        learned, _ = run('conditional-learn-2')
        self.assertTrue(conditional.valid(learned['evidence_state']['sources'][alpha_key]['verified_dependency_observation']))
        replace_source(plain_source, worker_source, 'add-worker')
        first, _ = run('worker-capture')
        self.assertTrue(first['evidence_state']['sources'][alpha_key]['automatic_observation_required'])
        (self.workspace / 'worker.cfg').write_text('changed')
        third, result = run('worker-input-changed')
        self.assertIn('ran-alpha changed', result.stdout)
        self.assertNotIn('ran-beta', result.stdout)
        # A repeated unsupported execution does not launch another Inspector.
        self.assertEqual(first['verification']['framework_observations'][alpha_key],
                         third['verification']['framework_observations'][alpha_key])

        replace_source(worker_source, plain_source, 'remove-worker')
        recovered, result = run('recover-learning')
        self.assertIn('ran-alpha changed', result.stdout)
        self.assertTrue(conditional.eligible_record(recovered['verification']['framework_observations'][alpha_key]))
        run('recover-binding')
        reused, result = run('recover-reuse')
        self.assertNotIn('ran-alpha', result.stdout)
        self.assertNotIn('ran-beta', result.stdout)
        self.assertTrue(all(row['decision'].startswith('reuse-')
                            for row in reused['verification']['incremental_plan']['decisions']))
