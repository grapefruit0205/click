from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest import mock

from hooks import click_incremental as metrics, click_shadow_dashboard


UI_ASSERTIONS = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const escape = s => String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
class Node {
  constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.style={};this.dataset={};this.text='';this.classList={add(){},remove(){},toggle(){}};}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return this.text+this.children.map(n=>n.textContent).join('');}
  set innerHTML(_){throw Error('Unsafe HTML sink used');}
  append(...children){this.children.push(...children);}
  prepend(...children){this.children.unshift(...children);}
  replaceChildren(...children){this.children=children;this.text='';}
  setAttribute(k,v){this.attributes[k]=String(v);}
  get outerHTML(){return '<'+this.tag+Object.entries(this.attributes).map(([k,v])=>' '+k+'="'+escape(v)+'"').join('')+'>'+escape(this.text)+this.children.map(n=>n.outerHTML).join('')+'</'+this.tag+'>';}
}
class Document {
  constructor(){this.nodes={};this.head=new Node('head');this.body=new Node('body');this.documentElement=new Node('html');this.documentElement.append(this.head,this.body);this.implementation={createHTMLDocument:()=>new Document()};}
  getElementById(id){return this.nodes[id]??=new Node(id==='map'?'svg':'div');}
  createElement(tag){assert.notEqual(tag,'script');return new Node(tag);}
  createElementNS(_,tag){return new Node(tag);}
  querySelectorAll(){return [];}
  querySelector(){return new Node('div');}
}
const doc=new Document();
const context={document:doc,location:{hash:'',pathname:'/'},history:{replaceState(){}},URLSearchParams,
  setTimeout(){},setInterval(){},fetch(){throw Error('Unexpected network or verification');}};
const marker='  refresh();\n  setInterval(refresh, 1500);';
assert(input.script.includes(marker));
const expose='  globalThis.api={summarize,readComparison,renderMap,standaloneReport,shareReport,setState(data,batch,summary,measured){snapshot=data;activeBatch=batch;activeSummary=summary;comparison=measured;}};';
vm.runInNewContext(input.script.replace(marker,expose),context);
const api=context.api;
const actual=JSON.parse(JSON.stringify(api.summarize(input.batch)));
for (const [key,value] of Object.entries(actual)) assert.deepEqual(value,input.summary[key],key);
const b={wall_ms:10,status:'passed',executed_source_count:2,reused_source_count:0,not_run_source_count:0};
const i={wall_ms:15,status:'passed',executed_source_count:0,reused_source_count:2,not_run_source_count:0};
const benchmark={version:2,kind:'click-paired-verification-benchmark',engine:{version:'<script>bad</script>',commit:'private-secret'},
  conditions:{iterations:1,warmups:0,workload_rounds:20,runtime_mode:'guarded',scope_equivalence:'same-two-unittest-files',authority:'real-hooks-and-one-use-runner',observer:'off',order:'alternating-pair-order'},
  samples:[{scenario:'unchanged',comparison:'same-shards',iteration:0,warmup:false,order:['baseline','incremental'],baseline:b,incremental:i,raw_argv:['private-secret']}]};
let safe=api.readComparison(benchmark);
assert.equal(safe.samples[0].delta_ms,-5);assert.equal(safe.samples[0].delta_percent,-50);
assert(!JSON.stringify(safe).includes('private-secret'));assert(!JSON.stringify(safe).includes('<script>'));
benchmark.samples[0].baseline.wall_ms=0;assert.equal(api.readComparison(benchmark).samples[0].delta_percent,null);
benchmark.samples.push(benchmark.samples[0]);assert.throws(()=>api.readComparison(benchmark));benchmark.samples.pop();
benchmark.samples[0].incremental.status='failed';assert.equal(api.readComparison(benchmark).samples[0].eligible,false);
const source={id:'source:one',input_count:60,visible_input_count:60};
const map={nodes:[{id:source.id,type:'source',label:'Check',kind:'argv',status:'passed'}],edges:[]};
for(let n=0;n<60;n++){map.nodes.push({id:'input:'+n,type:'input',label:'test-'+n,kind:'file',status:'current-observed'});map.edges.push({source:source.id,target:'input:'+n,operations:['read']});}
api.renderMap({map},source);
assert.equal(doc.getElementById('map').children.filter(n=>n.tag==='g').length,49);
assert.equal(doc.getElementById('mapMeta').textContent,'입력 48개 표시 · 12개 생략');
// Explicitly synthetic sensitive-field fixture, not a usable credential.
const snapshot={generated_at:1000,summary:{shadow:{candidate_count:9}},private_token:'<example-private-value>'};
const batch=JSON.parse(JSON.stringify(input.batch));batch.sources[0].label='</td><script>alert(1)</script>';
batch.sources[0].reuse_origin={kind:'successor-evidence',batch_id:'a'.repeat(32),evidence_session_id:'evs_'+'b'.repeat(32),candidate_digest:'c'.repeat(64),origin_revision:7};
api.setState(snapshot,batch,input.summary,safe);
const report=api.shareReport();assert(!JSON.stringify(report).includes('<example-private-value>'));
assert.equal(report.task,null);assert.equal(report.engine,null);assert.equal(report.accounting,null);assert.equal(report.controls,null);
assert.equal(report.summary.authoritative_reuse_count,input.summary.authoritative_reuse_count);
assert.equal(report.batch.sources[0].reuse_origin.origin_revision,7);
const html=api.standaloneReport(report);assert(!html.includes('<script>'));assert(html.includes('&lt;script&gt;'));
assert(html.includes('이전 배치 aaaaaaaaaaaa에서 재판정'));
assert(!html.includes('src="http'));assert(!html.includes('href="http'));
console.log('dashboard calculation, map limit, comparison validation and safe standalone export passed');
"""


class VerificationEfficiencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = mock.patch.object(metrics.time, "time", return_value=1000)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def decision(self, index: int, selected: str = "run", duration=None):
        baseline = None if duration is None else {
            "duration_ms": duration, "revision": 0, "check_digest": str(index + 5) * 64,
            "observed_at": 900, "batch_id": "b" * 32, "sample_count": 1,
        }
        return metrics.decision(
            source_key=str(index) * 64, decision=selected,
            reason_code="no-passing-evidence" if selected == "run" else "same-revision-receipt-current",
            current_revision=1, previous_revision=0, check_digest=str(index + 5) * 64,
            authority_source="runner" if selected == "run" else "exact-receipt",
            estimated_avoided_ms=0 if selected == "run" else duration, duration_baseline=baseline,
        )

    def verification(self, *items):
        plan = metrics.build_plan(items, current_revision=1)
        state = {}
        metrics.store_plan(state, plan)
        batch = metrics.new_batch(plan, batch_id="a" * 32, revision=1, prepared_ms=3.25)
        self.assertTrue(metrics.store_batch(state, batch))
        return state

    def test_plan_does_not_claim_actual_execution(self):
        plan = metrics.build_plan([self.decision(1), self.decision(2)], current_revision=1)
        self.assertEqual(plan["planned_execution_source_count"], 2)
        self.assertNotIn("executed_source_count", plan)
        state = {}
        metrics.store_plan(state, plan)
        self.assertIsNone(metrics.summary(state)["executed_source_count"])
        self.assertFalse(metrics.record_execution(state, {"1" * 64: 99}))
        self.assertIsNone(metrics.summary(state)["executed_duration_ms"])

    def test_first_failure_leaves_later_source_not_run(self):
        state = self.verification(self.decision(1), self.decision(2))
        immutable_plan = copy.deepcopy(state[metrics.PLAN_FIELD])
        self.assertTrue(metrics.mark_started(state, "1" * 64))
        self.assertEqual(metrics.summary(state)["executed_source_count"], 1)
        self.assertEqual(metrics.summary(state)["completed_source_count"], 0)
        self.assertTrue(metrics.record_execution(
            state, {"1" * 64: 17}, source_results={
                "1" * 64: {"started": True, "completed": True, "status": "failed", "reason_code": "command-failed"}
            }, exit_code=1, runner_duration_ms=25,
        ))
        result = metrics.summary(state)
        self.assertEqual(result["planned_execution_source_count"], 2)
        self.assertEqual(result["executed_source_count"], 1)
        self.assertEqual(result["failed_source_count"], 1)
        self.assertEqual(result["not_run_source_count"], 1)
        self.assertEqual(result["authoritative_reuse_count"], 0)
        self.assertEqual(result["measured_processing_ms"], 28.25)
        self.assertEqual(state[metrics.PLAN_FIELD], immutable_plan)

    def test_all_reuse_retains_processing_time_and_partial_baseline_coverage(self):
        state = self.verification(self.decision(1, "reuse-exact", 18.4), self.decision(2, "reuse-exact"))
        self.assertEqual(metrics.summary(state)["authoritative_reuse_count"], 0)
        self.assertTrue(metrics.finish_reuse(state))
        result = metrics.summary(state)
        self.assertEqual(result["executed_source_count"], 0)
        self.assertEqual(result["executed_duration_ms"], 0)
        self.assertEqual(result["authoritative_reuse_count"], 2)
        self.assertEqual(result["measured_processing_ms"], 3.25)
        self.assertIsNone(result["request_wall_ms"])
        self.assertEqual(result["estimated_avoided_ms"], 18.4)
        self.assertEqual(result["estimated_source_count"], 1)
        self.assertEqual(result["baseline_sample_count"], 1)

    def test_unmeasured_baseline_is_unknown_not_zero(self):
        state = self.verification(self.decision(1, "reuse-exact"))
        metrics.finish_reuse(state)
        self.assertIsNone(metrics.summary(state)["estimated_avoided_ms"])

    def test_rejected_candidate_is_not_applied_reuse(self):
        state = self.verification(self.decision(1, "reuse-exact", 200), self.decision(2))
        self.assertTrue(metrics.reject_batch(state))
        result = metrics.summary(state)
        self.assertEqual(metrics.current_batch(state)["status"], "rejected")
        self.assertEqual(result["executed_source_count"], 0)
        self.assertEqual(result["not_run_source_count"], 2)
        self.assertEqual(result["authoritative_reuse_count"], 0)
        self.assertEqual(result["estimated_avoided_ms"], 0)

    def test_interruption_is_not_success(self):
        state = self.verification(self.decision(1), self.decision(2))
        metrics.record_execution(
            state, {"1" * 64: 5}, exit_code=130, runner_duration_ms=10,
            source_results={"1" * 64: {
                "started": True, "completed": True, "status": "interrupted", "reason_code": "command-interrupted",
            }},
        )
        self.assertEqual(metrics.current_batch(state)["status"], "interrupted")
        result = metrics.summary(state)
        self.assertEqual(result["passed_source_count"], 0)
        self.assertEqual(result["interrupted_source_count"], 1)
        self.assertEqual(result["not_run_source_count"], 1)

    def test_duplicate_results_and_projection_do_not_accumulate(self):
        state = self.verification(self.decision(1, "reuse-exact", 12))
        metrics.finish_reuse(state)
        original = json.dumps(state, sort_keys=True)
        final = metrics.current_batch(state)
        for _ in range(4):
            self.assertTrue(metrics.store_batch(state, final))
            metrics.summary(state)
            metrics.batch_history(state, now=1000)
        self.assertEqual(json.dumps(state, sort_keys=True), original)
        self.assertEqual(len(metrics.batch_history(state, now=1000)), 1)

    def test_crash_unfinished_record_is_not_a_completed_sample(self):
        state = self.verification(self.decision(1))
        metrics.mark_started(state, "1" * 64)
        batch = metrics.current_batch(state)
        self.assertEqual(batch["status"], "running")
        self.assertIsNone(batch["finished_at"])
        self.assertIsNone(metrics.summary(state)["executed_duration_ms"])
        self.assertEqual(metrics.summary(state)["completed_source_count"], 0)
        self.assertEqual(metrics.history_totals(state)["finalized_batch_count"], 0)

    def test_cancel_preserves_unknown_outcome_and_deduplicated_history(self):
        state = self.verification(self.decision(1), self.decision(2, "reuse-exact", 10))
        metrics.mark_started(state, "1" * 64)
        self.assertTrue(metrics.interrupt_batch(state))
        self.assertEqual(metrics.summary(state)["authoritative_reuse_count"], 0)
        self.assertEqual(metrics.summary(state)["not_run_source_count"], 1)
        self.assertEqual(metrics.summary(state)["completed_source_count"], 0)
        copied = {}
        metrics.merge_history(state, copied)
        metrics.merge_history(state, copied)
        self.assertEqual(len(metrics.batch_history(copied)), 1)
        self.assertEqual(metrics.history_totals(copied)["executed_source_count"], 1)

    def test_each_source_completion_is_visible_and_cancel_preserves_it(self):
        state = self.verification(self.decision(1), self.decision(2))
        self.assertTrue(metrics.mark_started(state, "1" * 64))
        self.assertTrue(metrics.mark_completed(
            state,
            "1" * 64,
            status="passed",
            reason="command-passed",
            duration_ms=17.5,
        ))
        self.assertTrue(metrics.mark_started(state, "2" * 64))

        running = metrics.current_batch(state)
        first, second = running["sources"]
        self.assertEqual((first["status"], first["completed"]), ("passed", True))
        self.assertEqual(first["duration_ms"], 17.5)
        self.assertEqual((second["status"], second["completed"]), ("running", False))

        self.assertTrue(metrics.interrupt_batch(state))
        cancelled = metrics.current_batch(state)
        first, second = cancelled["sources"]
        self.assertEqual((first["status"], first["completed"]), ("passed", True))
        self.assertEqual(first["duration_ms"], 17.5)
        self.assertEqual((second["status"], second["completed"]), ("interrupted", False))
        self.assertEqual(metrics.summary(state)["passed_source_count"], 1)

    def test_duplicate_source_completion_is_idempotent_and_conflicts_are_rejected(self):
        state = self.verification(self.decision(1))
        metrics.mark_started(state, "1" * 64)
        arguments = {
            "status": "passed", "reason": "command-passed", "duration_ms": 8.25,
        }
        self.assertTrue(metrics.mark_completed(state, "1" * 64, **arguments))
        original = json.dumps(state, sort_keys=True)
        self.assertTrue(metrics.mark_completed(state, "1" * 64, **arguments))
        self.assertEqual(json.dumps(state, sort_keys=True), original)
        self.assertFalse(metrics.mark_completed(
            state, "1" * 64, status="failed", reason="command-failed", duration_ms=8.25,
        ))

    def test_storage_does_not_keep_mutable_aliases_or_expired_batches(self):
        state = self.verification(self.decision(1))
        batch = metrics.current_batch(state)
        fresh = {}
        metrics.store_batch(fresh, batch)
        batch["sources"][0]["label"] = "tampered"
        self.assertNotEqual(metrics.current_batch(fresh)["sources"][0]["label"], "tampered")
        with mock.patch.object(metrics.time, "time", return_value=1000 + metrics.MAX_HISTORY_AGE_SECONDS + 1):
            self.assertIsNone(metrics.current_batch(fresh))

    def test_replayed_preparation_cannot_erase_a_witnessed_start(self):
        state = self.verification(self.decision(1))
        prepared = metrics.current_batch(state)
        metrics.mark_started(state, "1" * 64)
        self.assertFalse(metrics.store_batch(state, prepared))
        self.assertEqual(metrics.current_batch(state)["status"], "running")
        self.assertEqual(metrics.summary(state)["executed_source_count"], 1)

    def test_preexecution_failure_records_zero_executions_and_time(self):
        state = self.verification(self.decision(1), self.decision(2))
        metrics.record_execution(state, {}, source_results={}, exit_code=2, runner_duration_ms=9)
        self.assertEqual(metrics.current_batch(state)["status"], "rejected")
        self.assertEqual(metrics.summary(state)["executed_source_count"], 0)
        self.assertEqual(metrics.summary(state)["measured_processing_ms"], 12.25)

    def test_history_caps_apply_to_result_records_and_private_fields_are_rejected(self):
        state = self.verification(self.decision(1))
        batch = metrics.current_batch(state)
        bad = copy.deepcopy(batch)
        bad["raw_argv"] = ["secret"]
        self.assertFalse(metrics.batch_is_valid(bad))
        bad = copy.deepcopy(batch)
        bad["sources"][0]["label"] = '<img src=x onerror="alert(1)">'
        self.assertFalse(metrics.batch_is_valid(bad))
        self.assertEqual(metrics.safe_label("/private/token", "묶음"), "묶음")
        records = [dict(batch, batch_id=str(index) * 32, timestamp=timestamp) for index, timestamp in ((1, 985), (2, 995), (3, 998))]
        self.assertEqual(len(metrics.prune_history(records, now=1000, max_events=1)), 1)
        self.assertEqual(len(metrics.prune_history(records, now=1000, max_age_seconds=10)), 2)
        self.assertEqual(metrics.prune_history(records, now=1000, max_bytes=2), [])

    def test_missing_legacy_fields_remain_unknown_and_malformed_history_is_safe(self):
        for state in ({}, {metrics.HISTORY_FIELD: {}}, {metrics.HISTORY_FIELD: "not-a-list"}):
            self.assertIsNone(metrics.current_batch(state))
            self.assertIsNone(metrics.summary(state)["request_wall_ms"])
            self.assertIsNone(metrics.summary(state)["executed_source_count"])

    @unittest.skipUnless(shutil.which("node"), "Node unavailable for JavaScript unit assertions")
    def test_dashboard_functions_match_actual_metrics_and_exports_are_content_safe(self):
        state = self.verification(self.decision(1, "reuse-exact", 12.5), self.decision(2, "reuse-exact"))
        metrics.finish_reuse(state)
        result = subprocess.run(
            [shutil.which("node"), "-e", UI_ASSERTIONS],
            input=json.dumps({"script": click_shadow_dashboard.JS,
                              "batch": metrics.current_batch(state), "summary": metrics.summary(state)}),
            text=True, capture_output=True, check=False, cwd=Path(__file__).parents[1],
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
