from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest import mock

from hooks import click_incremental as metrics, click_shadow_dashboard


UI_HARNESS = r"""
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
  getAttribute(k){return this.attributes[k] ?? null;}
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
const saved={value:null};
const storage={getItem:()=>saved.value,setItem:(key,value)=>{assert.equal(key,'click.dashboard.language');saved.value=value;}};
context.localStorage=storage;
const staticNodes=(input.static_labels??[]).map(attributes=>{const node=new Node('span');Object.entries(attributes).forEach(([key,value])=>node.setAttribute(key,value));return node;});
doc.querySelectorAll=selector=>selector.startsWith('[data-i18n')?staticNodes.filter(node=>selector.slice(1,-1) in node.attributes):[];
function loadDashboard(target){
  target.module={exports:{}};
  vm.runInNewContext(input.script,target);
  target.api=target.module.exports;
  return target.api;
}
const api=loadDashboard(context);
"""


UI_ASSERTIONS = UI_HARNESS + r"""
const b={wall_ms:10,status:'passed',executed_source_count:2,reused_source_count:0,not_run_source_count:0};
const i={wall_ms:15,status:'passed',executed_source_count:0,reused_source_count:2,not_run_source_count:0};
const benchmark={version:2,kind:'click-paired-verification-benchmark',engine:{version:'<script>bad</script>',commit:'private-secret'},
  conditions:{iterations:1,warmups:0,workload_rounds:20,runtime_mode:'guarded',scope_equivalence:'same-two-unittest-files',authority:'real-hooks-and-one-use-runner',observer:'off',order:'alternating-pair-order'},
  samples:[{scenario:'unchanged',comparison:'same-shards',iteration:0,warmup:false,order:['baseline','incremental'],baseline:b,incremental:i,raw_argv:['private-secret']}]};
let safe=api.readComparison(benchmark);
assert.equal(safe.samples[0].delta_ms,-5);assert.equal(safe.samples[0].delta_percent,-50);
assert(!JSON.stringify(safe).includes('private-secret'));assert(!JSON.stringify(safe).includes('<script>'));
const workflowStages=['first-run','unrelated-code','related-code','all-code','environment','failure','retry','unchanged'];
const workflowSamples=[];
for (const configuration of ['click-default','explicit-reuse']) for (const scenario of workflowStages) for (const comparison of ['same-shards','parent-suite']) {
  const failed=scenario==='failure';const baselineMs=comparison==='same-shards'?12:10;const clickMs=comparison==='same-shards'?6:8;const delta=baselineMs-clickMs;
  const scopeEquivalent=!(configuration==='click-default'&&comparison==='same-shards');const eligible=!failed&&scopeEquivalent;
  workflowSamples.push({configuration,scenario,comparison,iteration:0,warmup:false,order:['click','same-shards','parent-suite'],eligible,
    excluded_reason:!scopeEquivalent?'scope-not-equivalent':failed?'verification-not-passed':'',scope_equivalent:scopeEquivalent,unit:'ms',
    baseline:{duration_ms:baselineMs,status:failed?'failed':'passed',measurement_scope:comparison==='same-shards'?'sequential-shard-command-dispatch-through-return':'parent-command-dispatch-through-return'},
    click:{duration_ms:clickMs,status:failed?'failed':'passed',measurement_scope:comparison==='same-shards'?'executed-source-command-duration-sum':'driver-preflight-through-runner-return'},
    delta_ms:delta,delta_percent:100*delta/baselineMs});
}
const workflow={version:4,kind:'click-guarded-workflow-benchmark',source:'isolated-guarded-fixture',unit:'ms',
  engine:{version:'0.82.0+codex.20260906090212',commit:'a'.repeat(40),source_digest:'b'.repeat(64),working_tree_modified:true},environment:{system:'Linux',machine:'x86_64',python:'3.13.0'},
  conditions:{iterations:1,warmups:0,workload_rounds:20,configurations:['baseline','click-default','explicit-reuse'],steps:workflowStages,comparisons:['same-shards','parent-suite'],
    runtime_mode:'guarded-explicitly-selected; baseline-without-click',scope_equivalence:'same-two-unittest-files-and-code-at-every-stage',measurement_order:'rotating-within-stage-and-workflow',
    authority:'real-hooks-and-one-use-runner; distinct-scripted-fixture-approval-turns',observer:'off',cache:'fresh-initial-state-per-configuration-and-repetition; OS-cache-not-flushed; bytecode-disabled',
    default_configuration:'no-optional-shards-dependencies-or-safe-change-policy; product-default-mode-remains-Evidence',explicit_configuration:'fixed-committed-two-shard-map-and-sibling-code-safe-change-policy-before-A',
    test_interval:'source-command-dispatch-through-return; sequential-sum-for-executed-sources',click_request_interval:'driver-preflight-through-runner-return',additional_cost:'setup-transition-and-two-same-state-full-executions-reported-separately',failure:'expected-failure-kept-raw-and-in-workflow-cost; excluded-from-success-only-savings-statistics'},
  samples:[{iteration:0,warmup:false,arms:Object.fromEntries(['baseline','click-default','explicit-reuse'].map(key=>[key,{setup_ms:1,transition_ms:2,audit_wall_ms:3,validation_wall_ms:4}]))}],comparison_samples:workflowSamples,stage_summaries:Array(32).fill({}),cumulative_summaries:Array(4).fill({}),workflow_cost_summaries:Array(2).fill({}),summaries:Array(4).fill({}),
  repository_reference:{version:1,kind:'click-repository-bundle-reference',source:'current-repository-test-bundle',unit:'ms',scope_digest:'c'.repeat(64),
    conditions:{iterations:1,warmups:0,shard_count:6,scope_basis:'committed-evidence-shards-v1-inventory',measurement_order:'alternating-pair-order',cache:'same-working-tree-and-environment; OS-cache-not-flushed; bytecode-disabled',measurement_scope:'driver-command-dispatch-through-return'},
    samples:[{iteration:0,warmup:false,order:['same-shards','parent-suite'],eligible:true,excluded_reason:'',same_shards:{duration_ms:10,status:'passed',exit_code:0,executed_command_count:6,not_run_command_count:0},parent_suite:{duration_ms:11,status:'passed',exit_code:0,executed_command_count:1,not_run_command_count:0},delta_ms:1,delta_percent:100/11}],
    summary:{eligible_samples:1,same_shards_duration_ms:{median:10,min:10,max:10},parent_suite_duration_ms:{median:11,min:11,max:11},parent_minus_shards_ms:{median:1,min:1,max:1}},limitations:['reference-only']},dashboard_snapshot:null,limitations:[]};
const workflowSafe=api.readComparison(workflow);
assert.equal(workflowSafe.version,2);assert.equal(workflowSafe.engine.version,'0.82.0+codex.20260906090212');assert.equal(workflowSafe.samples.length,32);
assert.equal(workflowSafe.repository_reference.conditions.shard_count,6);
assert.equal(workflowSafe.cost_samples.length,3);assert.equal(workflowSafe.cost_samples[0].additional_full_audit_ms,3);
assert.equal(workflowSafe.samples[0].excluded_reason,'scope-not-equivalent');
assert.equal(workflowSafe.samples.filter(item=>!item.eligible).length,11);assert(!JSON.stringify(workflowSafe).includes('dashboard_snapshot'));
for (const mutate of [copy=>copy.unit='s',copy=>copy.source='claimed-production',copy=>copy.comparison_samples[0].unit='s',copy=>copy.comparison_samples[0].baseline.measurement_scope='unknown',copy=>copy.comparison_samples[0].delta_ms=999,copy=>copy.repository_reference.samples[0].delta_ms=999]) {
  const changed=JSON.parse(JSON.stringify(workflow));mutate(changed);assert.throws(()=>api.readComparison(changed));
}
benchmark.samples[0].baseline.wall_ms=0;assert.equal(api.readComparison(benchmark).samples[0].delta_percent,null);
benchmark.samples.push(benchmark.samples[0]);assert.throws(()=>api.readComparison(benchmark));benchmark.samples.pop();
benchmark.samples[0].incremental.status='failed';assert.equal(api.readComparison(benchmark).samples[0].eligible,false);
for (const [name,expected] of Object.entries({first:'first-run',running:'running',allReuse:'all-reuse',noReuse:'no-reuse',failed:'failed',cancelled:'cancelled',partial:'partial-timing'})) {
  const item=input.cases[name];
  const view=api.outcomePresentation(item.batch,item.summary,item.savings);
  assert.equal(view.state,expected,name);
  api.renderOutcome(item.batch,item.summary,item.savings);
  if (['first-run','running','failed','cancelled','partial-timing'].includes(expected)) {
    assert.equal(doc.getElementById('executionComparison').hidden,true,name+' must not fake comparison bars');
    assert.equal(doc.getElementById('reductionRate').textContent,'측정 정보 없음');
  }
}
api.renderOutcome(input.cases.allReuse.batch,input.cases.allReuse.summary,input.cases.allReuse.savings);
assert.equal(doc.getElementById('executionComparison').hidden,false);
assert.equal(doc.getElementById('fullBar').style.width,'100%');
assert.equal(doc.getElementById('executedBar').style.width,'0%');
assert.equal(doc.getElementById('reductionRate').textContent,'약 100% [추정]');
api.renderOutcome(input.cases.noReuse.batch,input.cases.noReuse.summary,input.cases.noReuse.savings);
assert.equal(doc.getElementById('estimatedAvoided').textContent,'0 ms');
assert.equal(doc.getElementById('executedBar').style.width,'100%');
assert.equal(doc.getElementById('reductionRate').textContent,'약 0% [추정]');
api.renderOutcome(input.cases.partial.batch,input.cases.partial.summary,input.cases.partial.savings);
assert.equal(doc.getElementById('estimatedAvoided').textContent,'2 / 2개');
assert.match(doc.getElementById('estimateCoverage').textContent,/부분 추정.*\[추정\]/);
assert(!doc.getElementById('estimateCoverage').textContent.includes('≥'));
assert.equal(doc.getElementById('timingCoverage').textContent,'1/2');
assert.equal(doc.getElementById('baselineBlocks').children.length,2);
assert.equal(doc.getElementById('actualBlocks').children.length,2);
assert(doc.getElementById('actualBlocks').children.every(item=>item.className.includes('reused')));
assert.match(doc.getElementById('resultCoverage').textContent,/2\/2 결과 확보/);
const untimed=JSON.parse(JSON.stringify(input.cases.partial));
untimed.savings.omitted_test_execution_ms=null;untimed.savings.omitted_test_execution_status='unmeasured';
untimed.savings.coverage.timed_reused_source_count=0;
api.renderOutcome(untimed.batch,untimed.summary,untimed.savings);
assert.equal(doc.getElementById('estimatedAvoided').textContent,'2 / 2개');
assert.equal(doc.getElementById('executionComparison').hidden,true);
assert(!api.summaryCopy(untimed.batch,untimed.summary,untimed.savings).includes('피한 테스트 재실행 비용'));
assert(!api.summaryCopy(input.cases.failed.batch,input.cases.failed.summary,input.cases.failed.savings).includes('결과 확보'));
assert(api.summaryCopy(input.cases.failed.batch,input.cases.failed.summary,input.cases.failed.savings).startsWith('검증 실패'));
const differentMedians={samples:[{scenario:'unchanged',comparison:'parent-suite',eligible:true,baseline:{wall_ms:1},incremental:{wall_ms:0},delta_ms:1,delta_percent:100},{scenario:'unchanged',comparison:'parent-suite',eligible:true,baseline:{wall_ms:10},incremental:{wall_ms:9},delta_ms:1,delta_percent:10},{scenario:'unchanged',comparison:'parent-suite',eligible:true,baseline:{wall_ms:11},incremental:{wall_ms:1},delta_ms:10,delta_percent:1000/11}]};
const row=api.comparisonRows(differentMedians)[0];assert.equal(row.delta,1);assert.equal(row.baseline-row.incremental,9);
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
api.setState({...snapshot,history:{current_batch_id:batch.batch_id},batches:[batch],sources:[],map:{nodes:[],edges:[]}},batch,input.summary,input.savings,safe);
const projectedSource={id:'source:one',label:'safe shard',execution_status:'reused',execution_decision:'reuse-exact',
  reason_code:'successor-evidence-current',current_revision:8,previous_revision:7,duration_ms:null,
  duration_baseline:batch.sources[0].duration_baseline,reuse_origin:batch.sources[0].reuse_origin,
  origin_name:'이전 작업',origin_check_label:'원본 샤드',check_digest:'d'.repeat(64),shadow_limitations:[],next_action:''};
api.explain(projectedSource);
assert.match(doc.getElementById('originName').textContent,/이전 계약/);
assert.match(doc.getElementById('lineageSummary').textContent,/이전 작업 → 원본 성공 실행 → 현재 적용/);
assert.equal(doc.getElementById('lineageSteps').children.length,3);
assert(!doc.getElementById('lineageSteps').textContent.includes('evs_'));
assert(doc.getElementById('limits').textContent.includes('evs_'));
api.renderComparison();
assert.equal(doc.getElementById('waitIncreaseNotice').hidden,false);
assert.match(doc.getElementById('pairedNet').textContent,/증가/);
const report=api.shareReport();assert(!JSON.stringify(report).includes('<example-private-value>'));
assert.equal(report.task,null);assert.equal(report.engine,null);assert.equal(report.accounting,null);assert.equal(report.controls,null);
assert.equal(report.version,5);assert.deepEqual(report.revalidation_savings,input.savings);
assert.equal(report.labels.omitted,'절감 시간');
assert.equal(report.measurement.click_management_overhead_ms,null);
assert.equal(report.measurement.live_net_time_saving_reason,'counterfactual-not-measured');
assert.equal(report.summary.authoritative_reuse_count,input.summary.authoritative_reuse_count);
assert.equal('reuse_origin' in report.batch.sources[0],false);
assert.equal(report.batch.sources[0].label,'검증 묶음 1');
assert.equal(report.summary_copy,api.summaryCopy(batch,input.summary,input.savings));
const html=api.standaloneReport(report);assert(!html.includes('<script>'));assert(!html.includes('alert(1)'));
assert(!html.includes('evs_'));assert(!html.includes('ctr_'));
assert(html.includes('Click 전체 관리비용: 측정 정보 없음'));
assert(!html.includes('src="http'));assert(!html.includes('href="http'));
api.setState({...snapshot,task:{runtime_mode:'guarded',contract_id:'ctr_'+'f'.repeat(32),approval_bound:true},history:{current_batch_id:null},controls:[{code:'approval-required'}]},batch,input.summary,input.savings,safe);
const historical=api.shareReport();assert.equal(historical.selection_scope,'historical-batch');assert.equal(historical.task,null);assert.equal(historical.controls,null);assert.equal(historical.shadow,null);
api.render({generated_at:1000,task:{runtime_mode:'evidence',name:'새 작업',status:'evidence',mutation_revision:2},history:{current_batch_id:null,retained_batch_count:1},batches:[batch],summary:{incremental:input.cases.first.summary,revalidation_savings:input.cases.first.savings,shadow:{}},sources:[],map:{nodes:[],edges:[]}});
assert.equal(doc.getElementById('estimatedAvoided').textContent,'기준 실행 대기');assert.equal(doc.getElementById('resultCoverage').textContent,'');assert.equal(doc.getElementById('actualBlocks').children.length,0);
console.log('dashboard states, common metrics, map limit, paired comparison and safe exports passed');
"""


class VerificationEfficiencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = mock.patch.object(metrics.time, "time", return_value=1000)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def decision(self, index: int, selected: str = "run", duration=None):
        source_key = str(index) * 64
        check_digest = str(index + 5) * 64
        binding = metrics.timing_binding_digest(
            source_key=source_key,
            check_digest=check_digest,
            environment_digest="a" * 64,
            executable_digest="b" * 64,
            host_coverage_digest="c" * 64,
            observer_mode="off",
        )
        baseline = None if duration is None else metrics.build_duration_baseline(
            duration_ms=duration,
            source_key=source_key,
            revision=0,
            check_digest=check_digest,
            observed_at=900,
            batch_id="b" * 32,
            origin_task={"mode": "guarded", "id": "ctr_" + "d" * 32},
            observer_mode="off",
            timing_binding_digest=binding,
        )
        return metrics.decision(
            source_key=source_key, decision=selected,
            reason_code="no-passing-evidence" if selected == "run" else "same-revision-receipt-current",
            current_revision=1, previous_revision=0, check_digest=check_digest,
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

    def canonical_decision(
        self,
        suffix: str,
        selected: str = "run",
        duration: float | None = None,
    ):
        source_key = suffix * 64
        check_digest = f"{int(suffix, 16) + 32:064x}"
        binding = metrics.timing_binding_digest(
            source_key=source_key,
            check_digest=check_digest,
            environment_digest="a" * 64,
            executable_digest="b" * 64,
            host_coverage_digest="c" * 64,
            observer_mode="off",
        )
        baseline = None
        if duration is not None:
            baseline = metrics.build_duration_baseline(
                duration_ms=duration,
                source_key=source_key,
                revision=0,
                check_digest=check_digest,
                observed_at=900,
                batch_id="b" * 32,
                origin_task={
                    "mode": "guarded",
                    "id": "ctr_" + "d" * 32,
                },
                observer_mode="off",
                timing_binding_digest=binding,
            )
        return metrics.decision(
            source_key=source_key,
            decision=selected,
            reason_code=(
                "same-revision-receipt-current"
                if selected == "reuse-exact"
                else "no-passing-evidence"
            ),
            current_revision=1,
            previous_revision=0,
            check_digest=check_digest,
            authority_source=(
                "exact-receipt" if selected == "reuse-exact" else "runner"
            ),
            estimated_avoided_ms=(duration if selected == "reuse-exact" else 0),
            duration_baseline=baseline,
        )

    def completed_canonical_batch(
        self,
        decisions,
        execution_durations: dict[str, float],
        *,
        batch_id: str = "e" * 32,
    ):
        plan = metrics.build_plan(decisions, current_revision=1)
        state = {}
        metrics.store_plan(state, plan)
        metrics.store_batch(
            state,
            metrics.new_batch(
                plan,
                batch_id=batch_id,
                revision=1,
                prepared_ms=1,
            ),
        )
        if not execution_durations:
            self.assertTrue(metrics.finish_reuse(state))
            return state
        source_results = {
            key: {
                "started": True,
                "completed": True,
                "status": "passed",
                "reason_code": "command-passed",
            }
            for key in execution_durations
        }
        reused_keys = {
            item["source_key"]
            for item in decisions
            if item["decision"] in metrics.REUSE_DECISIONS
        }
        self.assertTrue(
            metrics.record_execution(
                state,
                execution_durations,
                source_results=source_results,
                reused_keys=reused_keys,
                exit_code=0,
                runner_duration_ms=sum(execution_durations.values()),
            )
        )
        return state

    def test_revalidation_savings_reference_equation_and_time_ratio(self):
        decisions = [
            self.canonical_decision(suffix, "reuse-exact", 50_000)
            for suffix in "12345678"
        ] + [
            self.canonical_decision("9"),
            self.canonical_decision("a"),
        ]
        state = self.completed_canonical_batch(
            decisions,
            {"9" * 64: 60_000, "a" * 64: 40_000},
        )
        savings = metrics.revalidation_savings(metrics.current_batch(state))

        self.assertTrue(metrics.revalidation_savings_is_valid(savings))
        self.assertEqual(savings["omitted_test_execution_ms"], 400_000)
        self.assertEqual(savings["executed_test_execution_ms"], 100_000)
        self.assertEqual(
            savings["full_sequential_test_execution_estimate_ms"], 500_000
        )
        self.assertEqual(savings["test_execution_reduction_ratio"], 0.8)
        self.assertEqual(
            savings["coverage"],
            {
                "requested_source_count": 10,
                "actual_executed_source_count": 2,
                "timed_executed_source_count": 2,
                "actual_reused_source_count": 8,
                "timed_reused_source_count": 8,
            },
        )
        self.assertEqual(savings["reason_codes"], [])
        host = metrics.host_summary(state)
        self.assertIn(
            "10개 중 2개만 실행 · 8개 재사용으로 약 6분 40초의 "
            "테스트 재실행 생략〔추정〕",
            host,
        )
        self.assertIn("동일 샤드 전체 순차 실행 예상: 약 8분 20초", host)
        self.assertIn("이번 테스트 실행: 1분 40초", host)
        self.assertIn("테스트 실행시간 감소: 약 80%", host)
        self.assertIn("시간 근거 커버리지 8/8", host)
        self.assertIn("Click 관리비용 제외", host)

        short_reuse = [
            self.canonical_decision(suffix, "reuse-exact", 1_000)
            for suffix in "12345678"
        ] + [self.canonical_decision("9"), self.canonical_decision("a")]
        different = self.completed_canonical_batch(
            short_reuse,
            {"9" * 64: 46_000, "a" * 64: 46_000},
            batch_id="f" * 32,
        )
        different_savings = metrics.revalidation_savings(
            metrics.current_batch(different)
        )
        self.assertEqual(
            different_savings["coverage"]["actual_reused_source_count"] / 10,
            0.8,
        )
        self.assertEqual(
            different_savings["test_execution_reduction_ratio"], 0.08
        )

    def test_revalidation_savings_all_execute_all_reuse_and_zero_denominator(self):
        all_execute = self.completed_canonical_batch(
            [self.canonical_decision("1"), self.canonical_decision("2")],
            {"1" * 64: 40, "2" * 64: 60},
        )
        executed = metrics.revalidation_savings(
            metrics.current_batch(all_execute)
        )
        self.assertEqual(executed["omitted_test_execution_ms"], 0)
        self.assertEqual(executed["executed_test_execution_ms"], 100)
        self.assertEqual(
            executed["full_sequential_test_execution_estimate_ms"], 100
        )
        self.assertEqual(executed["test_execution_reduction_ratio"], 0)

        all_reuse = self.completed_canonical_batch(
            [
                self.canonical_decision("1", "reuse-exact", 40),
                self.canonical_decision("2", "reuse-exact", 60),
            ],
            {},
        )
        reused = metrics.revalidation_savings(metrics.current_batch(all_reuse))
        self.assertEqual(reused["executed_test_execution_ms"], 0)
        self.assertEqual(reused["omitted_test_execution_ms"], 100)
        self.assertEqual(reused["test_execution_reduction_ratio"], 1)

        zero = self.completed_canonical_batch(
            [self.canonical_decision("1")], {"1" * 64: 0}
        )
        zero_savings = metrics.revalidation_savings(metrics.current_batch(zero))
        self.assertEqual(
            zero_savings["full_sequential_test_execution_estimate_ms"], 0
        )
        self.assertIsNone(zero_savings["test_execution_reduction_ratio"])
        self.assertIn("zero-denominator", zero_savings["reason_codes"])

    def test_revalidation_savings_partial_missing_legacy_and_failed_requests(self):
        missing = self.completed_canonical_batch(
            [
                self.canonical_decision("1", "reuse-exact", 50),
                self.canonical_decision("2", "reuse-exact"),
            ],
            {},
        )
        partial = metrics.revalidation_savings(metrics.current_batch(missing))
        self.assertEqual(partial["omitted_test_execution_ms"], 50)
        self.assertEqual(partial["omitted_test_execution_status"], "partial")
        self.assertEqual(partial["executed_test_execution_ms"], 0)
        self.assertIsNone(
            partial["full_sequential_test_execution_estimate_ms"]
        )
        self.assertIsNone(partial["test_execution_reduction_ratio"])
        self.assertIn(
            "reused-duration-sample-missing", partial["reason_codes"]
        )
        partial_host = metrics.host_summary(missing)
        self.assertIn("시간 표본 1/2개 · 부분 추정 합계 약 50 ms〔추정〕", partial_host)
        self.assertIn("테스트 실행시간 감소: 측정 정보 없음", partial_host)

        legacy_state = self.completed_canonical_batch(
            [self.canonical_decision("1", "reuse-exact", 50)], {},
            batch_id="1" * 32,
        )
        legacy_batch = metrics.current_batch(legacy_state)
        baseline = legacy_batch["sources"][0]["duration_baseline"]
        legacy_batch["sources"][0]["duration_baseline"] = {
            key: baseline[key]
            for key in (
                "duration_ms",
                "revision",
                "check_digest",
                "observed_at",
                "batch_id",
                "sample_count",
            )
        }
        self.assertTrue(metrics.batch_is_valid(legacy_batch))
        legacy = metrics.revalidation_savings(legacy_batch)
        self.assertIsNone(legacy["omitted_test_execution_ms"])
        self.assertIn("legacy-timing-context-missing", legacy["reason_codes"])

        incompatible_batch = copy.deepcopy(
            metrics.current_batch(
                self.completed_canonical_batch(
                    [self.canonical_decision("1", "reuse-exact", 50)],
                    {},
                    batch_id="2" * 32,
                )
            )
        )
        incompatible_batch["sources"][0]["duration_baseline"][
            "source_key"
        ] = "2" * 64
        self.assertTrue(metrics.batch_is_valid(incompatible_batch))
        incompatible = metrics.revalidation_savings(incompatible_batch)
        self.assertIsNone(incompatible["omitted_test_execution_ms"])
        self.assertIn(
            "reused-duration-sample-incompatible",
            incompatible["reason_codes"],
        )

        failed_state = self.verification(self.decision(1), self.decision(2))
        self.assertTrue(
            metrics.record_execution(
                failed_state,
                {"1" * 64: 25},
                source_results={
                    "1" * 64: {
                        "started": True,
                        "completed": True,
                        "status": "failed",
                        "reason_code": "command-failed",
                    }
                },
                exit_code=1,
                runner_duration_ms=25,
            )
        )
        failed = metrics.revalidation_savings(
            metrics.current_batch(failed_state)
        )
        self.assertEqual(failed["executed_test_execution_ms"], 25)
        self.assertEqual(failed["executed_test_execution_status"], "partial")
        self.assertIsNone(failed["full_sequential_test_execution_estimate_ms"])
        self.assertIn("request-not-passed", failed["reason_codes"])
        self.assertIn("scope-incomplete", failed["reason_codes"])
        self.assertIn("2개 샤드 요청 미완료", metrics.host_summary(failed_state))

    def test_revalidation_savings_keeps_retries_and_parent_plan_out_of_batch(self):
        parent = metrics.build_plan(
            [self.canonical_decision("f")], current_revision=1, planned_at=900
        )
        failed_plan = metrics.build_plan(
            [self.canonical_decision("1")], current_revision=1
        )
        first = {}
        metrics.store_plan(first, failed_plan)
        metrics.store_batch(
            first,
            metrics.new_batch(
                failed_plan,
                batch_id="1" * 32,
                revision=1,
                prepared_ms=1,
            ),
        )
        self.assertTrue(
            metrics.record_execution(
                first,
                {"1" * 64: 10},
                source_results={
                    "1" * 64: {
                        "started": True,
                        "completed": True,
                        "status": "failed",
                        "reason_code": "command-failed",
                    }
                },
                exit_code=1,
                runner_duration_ms=10,
            )
        )
        retry = self.completed_canonical_batch(
            [self.canonical_decision("1")],
            {"1" * 64: 8},
            batch_id="2" * 32,
        )
        combined = {}
        metrics.append_plan_history(combined, parent)
        metrics.merge_history(first, combined)
        metrics.merge_history(retry, combined)
        batches = metrics.batch_history(combined, now=1000)
        by_id = {item["batch_id"]: item for item in batches}
        self.assertEqual(set(by_id), {"1" * 32, "2" * 32})
        self.assertEqual(
            {
                metrics.revalidation_savings(item)["coverage"][
                    "requested_source_count"
                ]
                for item in batches
            },
            {1},
        )
        self.assertIsNone(
            metrics.revalidation_savings(by_id["1" * 32])[
                "full_sequential_test_execution_estimate_ms"
            ]
        )
        self.assertEqual(
            metrics.revalidation_savings(by_id["2" * 32])[
                "full_sequential_test_execution_estimate_ms"
            ],
            8,
        )
        before = json.dumps(combined, sort_keys=True)
        self.assertTrue(metrics.store_batch(combined, by_id["1" * 32]))
        self.assertEqual(json.dumps(combined, sort_keys=True), before)
        self.assertEqual(len(metrics.batch_history(combined, now=1000)), 2)

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
        savings = metrics.revalidation_savings(metrics.current_batch(state))
        self.assertFalse(savings["scope_complete"])
        self.assertIsNone(
            savings["full_sequential_test_execution_estimate_ms"]
        )
        self.assertIsNone(savings["test_execution_reduction_ratio"])
        self.assertIn("request-not-passed", savings["reason_codes"])
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

    def test_completed_history_impact_deduplicates_and_excludes_unfinished_requests(self):
        first = self.completed_canonical_batch(
            [self.canonical_decision("1", "reuse-exact", 50), self.canonical_decision("2", "reuse-exact")], {},
        )
        batch = metrics.current_batch(first)
        retry = copy.deepcopy(batch)
        retry["batch_id"] = "f" * 32
        failed = copy.deepcopy(batch)
        failed.update(batch_id="c" * 32, status="failed")
        unfinished = copy.deepcopy(batch)
        unfinished.update(batch_id="d" * 32, finished_at=None)
        self.assertFalse(metrics.revalidation_savings(unfinished)["scope_complete"])
        total = metrics.retained_impact([batch, batch, retry, failed, unfinished])
        self.assertTrue(metrics.retained_impact_is_valid(total))
        self.assertEqual(total["completed_request_count"], 2)
        self.assertEqual(total["reused_group_request_count"], 4)
        self.assertEqual(total["timed_reused_group_count"], 2)
        self.assertEqual(total["missing_timing_group_count"], 2)
        self.assertEqual(total["avoided_execution_ms"], 100)
        self.assertEqual(total["timing_status"], "partial")
        corrupt = {**total, "avoided_execution_ms": None}
        self.assertFalse(metrics.retained_impact_is_valid(corrupt))
        empty = metrics.retained_impact([])
        self.assertTrue(metrics.retained_impact_is_valid(empty))
        self.assertIsNone(empty["avoided_execution_ms"])

    def test_mixed_observer_samples_hide_time_ratio_without_changing_reuse(self):
        state = self.completed_canonical_batch(
            [self.canonical_decision("1", "reuse-exact", 40), self.canonical_decision("2", "reuse-exact", 60)], {},
        )
        batch = metrics.current_batch(state)
        batch["sources"][1]["duration_baseline"]["observer_mode"] = "shadow"
        before = copy.deepcopy(batch)
        savings = metrics.revalidation_savings(batch)
        self.assertEqual(savings["coverage"]["actual_reused_source_count"], 2)
        self.assertIsNone(savings["full_sequential_test_execution_estimate_ms"])
        self.assertIsNone(savings["test_execution_reduction_ratio"])
        self.assertEqual(batch, before)

    @unittest.skipUnless(shutil.which("node"), "Node unavailable for JavaScript unit assertions")
    def test_dashboard_functions_match_actual_metrics_and_exports_are_content_safe(self):
        partial = self.verification(
            self.decision(1, "reuse-exact", 12.5),
            self.decision(2, "reuse-exact"),
        )
        self.assertTrue(metrics.finish_reuse(partial))

        running = self.verification(self.decision(1), self.decision(2))
        self.assertTrue(metrics.mark_started(running, "1" * 64))

        all_reuse = self.verification(
            self.decision(1, "reuse-exact", 12.5),
            self.decision(2, "reuse-exact", 7.5),
        )
        self.assertTrue(metrics.finish_reuse(all_reuse))

        no_reuse = self.verification(self.decision(1), self.decision(2))
        self.assertTrue(
            metrics.record_execution(
                no_reuse,
                {"1" * 64: 30, "2" * 64: 20},
                source_results={
                    "1" * 64: {
                        "started": True,
                        "completed": True,
                        "status": "passed",
                        "reason_code": "command-passed",
                    },
                    "2" * 64: {
                        "started": True,
                        "completed": True,
                        "status": "passed",
                        "reason_code": "command-passed",
                    },
                },
                exit_code=0,
                runner_duration_ms=50,
            )
        )

        failed = self.verification(self.decision(1), self.decision(2))
        self.assertTrue(
            metrics.record_execution(
                failed,
                {"1" * 64: 15},
                source_results={
                    "1" * 64: {
                        "started": True,
                        "completed": True,
                        "status": "failed",
                        "reason_code": "command-failed",
                    }
                },
                exit_code=1,
                runner_duration_ms=15,
            )
        )

        cancelled = self.verification(self.decision(1), self.decision(2))
        self.assertTrue(metrics.mark_started(cancelled, "1" * 64))
        self.assertTrue(metrics.interrupt_batch(cancelled))

        def ui_case(state):
            batch = metrics.current_batch(state)
            return {
                "batch": batch,
                "summary": metrics.summary(state),
                "savings": metrics.revalidation_savings(batch),
            }

        cases = {
            "first": {
                "batch": None,
                "summary": metrics.summary({}),
                "savings": metrics.unmeasured_revalidation_savings(),
            },
            "running": ui_case(running),
            "allReuse": ui_case(all_reuse),
            "noReuse": ui_case(no_reuse),
            "failed": ui_case(failed),
            "cancelled": ui_case(cancelled),
            "partial": ui_case(partial),
        }
        result = subprocess.run(
            [shutil.which("node"), "-e", UI_ASSERTIONS],
            input=json.dumps({"script": click_shadow_dashboard.JS,
                              "batch": metrics.current_batch(partial),
                              "summary": metrics.summary(partial),
                              "savings": metrics.revalidation_savings(
                                  metrics.current_batch(partial)
                              ),
                              "cases": cases}),
            text=True, capture_output=True, check=False, cwd=Path(__file__).parents[1],
        )
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(shutil.which("node"), "Node unavailable for dashboard language assertions")
class DashboardLanguageTests(unittest.TestCase):
    def run_language_script(self, assertions):
        from html.parser import HTMLParser
        from hooks import click_dashboard_projection

        class StaticLabels(HTMLParser):
            def __init__(self):
                super().__init__()
                self.labels = []

            def handle_starttag(self, tag, attrs):
                values = {key: value for key, value in attrs if key.startswith("data-i18n")}
                if values:
                    self.labels.append(values)

        labels = StaticLabels()
        labels.feed(click_shadow_dashboard.HTML)
        fixture = VerificationEfficiencyTests()
        state = fixture.completed_canonical_batch(
            [fixture.canonical_decision("1"), fixture.canonical_decision("2", "reuse-exact", 90000)],
            {"1" * 64: 30000},
            batch_id="d" * 32,
        )
        current_batch = metrics.current_batch(state)
        current_batch["version"] = 4
        current_batch["task"] = {"mode": "evidence", "id": "evs_" + "a" * 32, "name": "사용자 작업명"}
        state[metrics.HISTORY_FIELD] = [current_batch]
        data = click_dashboard_projection.dashboard_projection({
            "status": "evidence", "runtime_mode": "evidence", "evidence_session_id": "evs_" + "a" * 32,
            "verification": state, "presentation": {"name": "사용자 작업명"},
        })
        batch = data["batches"][-1]
        batch["sources"][0]["label"] = "사용자 검증명"
        previous = copy.deepcopy(batch)
        previous["batch_id"] = "e" * 32
        previous["task"]["name"] = "이전 사용자 작업명"
        data["batches"].insert(0, previous)
        data["batch_summaries"][previous["batch_id"]] = copy.deepcopy(data["batch_summaries"][batch["batch_id"]])
        data["history"]["retained_batch_count"] = 2

        result = subprocess.run(
            [shutil.which("node"), "-e", UI_HARNESS + assertions],
            input=json.dumps({"script": click_shadow_dashboard.JS, "projection": data, "static_labels": labels.labels}),
            text=True, capture_output=True, check=False, cwd=Path(__file__).parents[1],
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unchanged_snapshot_ignores_only_projection_timestamps(self):
        self.run_language_script(r'''
const original=JSON.stringify(input.projection);
assert.equal(api.acceptSnapshot(input.projection),true);
const renderedRows=doc.getElementById('sources').children;
const later=JSON.parse(original);
later.generated_at+=2;later.task_efficiency.generated_at+=2;
assert.equal(api.acceptSnapshot(later),false);
assert.equal(doc.getElementById('sources').children,renderedRows);
assert.equal(JSON.stringify(input.projection),original);
later.task.name='A changed task';
assert.equal(api.acceptSnapshot(later),true);
assert(doc.getElementById('contractName').textContent.includes('A changed task'));
const measured=JSON.parse(original);
measured.task_efficiency.presentations=[{measured_at:100}];
const updated=JSON.parse(JSON.stringify(measured));
updated.task_efficiency.presentations[0].measured_at=101;
assert.notEqual(api.snapshotSignature(measured),api.snapshotSignature(updated));
''')

    def test_language_switch_preserves_selection_metrics_and_export_locale(self):
        self.run_language_script(r'''
const original=JSON.stringify(input.projection);
api.render(input.projection);
doc.getElementById('batchSelect').onchange({target:{value:'e'.repeat(32)}});
doc.getElementById('filterReused').onclick();
const korean=api.shareReport();
assert.equal(api.setLanguage('en'),'en');
assert.equal(doc.documentElement.attributes.lang,'en');
assert.equal(doc.getElementById('estimatedAvoided').textContent,'About 1m 30s');
assert.equal(doc.getElementById('batchHeadline').textContent,'Only 1 of 2 groups rerun · Reused: 1');
assert.equal(doc.getElementById('sources').children.length,1);
assert.equal(doc.getElementById('filterReused').attributes['aria-pressed'],'true');
assert(doc.getElementById('selectionLabel').textContent.includes('Historical batch'));
const english=api.shareReport();
assert.equal(english.locale,'en');assert.equal(english.selection_scope,'historical-batch');
assert.deepEqual(english.summary,korean.summary);assert.deepEqual(english.revalidation_savings,korean.revalidation_savings);
assert.equal(english.batch.sources[0].label,'사용자 검증명');
assert(english.summary_copy.includes('[Estimated]'));
assert.equal(api.setLanguage('zh-CN'),'zh-CN');
assert.equal(doc.getElementById('estimatedAvoided').textContent,'约1分30秒');
assert.equal(doc.getElementById('batchHeadline').textContent,'全部 2 个中仅重跑 1 个 · 复用 1 个结果');
assert.equal(doc.getElementById('filterReused').attributes['aria-pressed'],'true');
const chinese=api.shareReport();assert.equal(chinese.locale,'zh-CN');assert(chinese.summary_copy.includes('[估算]'));
assert.deepEqual(chinese.summary,english.summary);assert.deepEqual(chinese.revalidation_savings,english.revalidation_savings);
assert(!/[가-힣]/u.test(chinese.summary_copy));assert(!/[가-힣]/u.test(english.summary_copy));
const htmlEn=api.standaloneReport(english),htmlZh=api.standaloneReport(chinese);
assert(htmlEn.includes('lang="en"'));assert(htmlEn.includes('Verification Efficiency Report'));
assert(htmlEn.includes(english.display.omitted_text));assert(htmlZh.includes('lang="zh-CN"'));assert(htmlZh.includes('验证效率报告'));
assert.equal(api.getLocale(),'zh-CN');
assert.equal(english.labels.omitted,'Time saved');
assert.equal(api.msg`이전 작업: ${'{0}<img onerror=alert(1)>'}`,'之前任务：{0}<img onerror=alert(1)>');
assert.equal(JSON.stringify(input.projection),original);
api.setLanguage('ko');assert.equal(api.shareReport().summary_copy,korean.summary_copy);
''')

    def test_reuse_table_opens_evidence_and_preserves_history_navigation(self):
        self.run_language_script(r'''
api.render(input.projection);api.setLanguage('en');
doc.getElementById('filterReused').onclick();
let rows=doc.getElementById('sources').children;
assert.equal(rows.length,1);assert.equal(rows[0].tag,'tr');
assert(rows[0].children[3].textContent.includes('Reuse applied'));
const details=doc.getElementById('explanationSection');let scrolled=false;
details.scrollIntoView=()=>{scrolled=true;};
rows[0].children[0].children[0].onclick();
assert.equal(details.open,true);assert.equal(scrolled,true);
assert(doc.getElementById('whyBody').textContent.length>0);
doc.getElementById('filterExecuted').onclick();
rows=doc.getElementById('sources').children;
assert.equal(rows.length,1);assert(rows[0].children[3].textContent.includes('Passed'));
doc.getElementById('batchFlow').children[1].children[0].onclick();
assert.equal(api.shareReport().selection_scope,'historical-batch');
assert(doc.getElementById('selectionLabel').textContent.includes('Historical batch'));
assert.equal(doc.getElementById('filterExecuted').attributes['aria-pressed'],'true');
doc.getElementById('latestBatch').onclick();
assert.equal(api.shareReport().selection_scope,'current-task');
assert.equal(doc.getElementById('verifiedChecks').textContent,'2/2');
''')

    def test_language_preference_survives_reload_and_storage_failure(self):
        self.run_language_script(r'''
function reload(localStorage) {
  const next={...context,document:new Document(),localStorage};
  loadDashboard(next);return next;
}
api.setLanguage('en');assert.equal(saved.value,'en');
let next=reload(storage);assert.equal(next.api.getLocale(),'en');assert.equal(next.document.documentElement.attributes.lang,'en');
next.api.setLanguage('zh-CN');assert.equal(saved.value,'zh-CN');
next=reload(storage);assert.equal(next.api.getLocale(),'zh-CN');
saved.value='unsupported';next=reload(storage);assert.equal(next.api.getLocale(),'ko');
const blocked={getItem(){throw Error('storage unavailable');},setItem(){throw Error('storage unavailable');}};
next=reload(blocked);assert.equal(next.api.getLocale(),'ko');assert.equal(next.api.setLanguage('en'),'en');
assert.equal(next.document.documentElement.attributes.lang,'en');
assert.equal(next.api.setLanguage('<script>'),'ko');
''')

    def test_translation_coverage_and_status_distinctions(self):
        self.run_language_script(r'''
const slots=value=>[...value.matchAll(/\{\d+\}/g)].map(match=>match[0]).sort();
for(const [key,pair] of Object.entries(api.MESSAGES)){
  assert.equal(pair.length,2);
  for(const value of pair){assert(value.length);assert(!/[가-힣]/u.test(value));assert.deepEqual([...new Set(slots(value))],[...new Set(slots(key))]);}
}
for(const attributes of input.static_labels)for(const key of Object.values(attributes))assert(key in api.MESSAGES,key);
const batch=input.projection.batches.at(-1),totals=input.projection.batch_summaries[batch.batch_id];
for(const language of ['en','zh-CN']){
  api.setLanguage(language);api.applyStaticLanguage();
  assert(staticNodes.every(node=>!/[가-힣]/u.test(node.textContent)));
  for(const attributes of input.static_labels)for(const [name,key] of Object.entries(attributes)){
    if(name!=='data-i18n')assert(!/[가-힣]/u.test(api.msg(key)));
  }
  for(const state of ['running','failed','interrupted','rejected','incomplete']){
    const view=api.outcomePresentation({...batch,status:state},totals.incremental,totals.revalidation_savings);
    assert.equal(view.complete,false);assert.equal(view.comparisonReady,false);assert.equal(view.resultText,'');
    assert(!/[가-힣]/u.test(JSON.stringify(view)));
  }
  const partial={...totals.revalidation_savings,omitted_test_execution_status:'partial',full_sequential_test_execution_estimate_ms:null,full_sequential_test_execution_estimate_status:'unmeasured',test_execution_reduction_ratio:null};
  const view=api.outcomePresentation(batch,totals.incremental,partial);
  assert.equal(view.comparisonReady,false);assert(!/[가-힣]/u.test(JSON.stringify(view)));
  assert(!api.summaryCopy({...batch,status:'failed'},totals.incremental,totals.revalidation_savings).includes(language==='en'?'Results available':'已取得'));
}
''')

    def test_public_task_efficiency_cards_preserve_sign_scope_and_privacy(self):
        self.run_language_script(r'''
function taskPresentation(ref,timeRatio,tokenRatio,overrides={}) {
  const taskMeasured=timeRatio!==null,tokenMeasured=tokenRatio!==null;
  return {comparison_ref:ref.repeat(24),baseline_variant:'B0',improved_variant:'B2',comparison_label:'B0→B2',scenario:'failure-repair',run_kind:'prepared-repeat',runtime_mode:'evidence',measured_at:1788784525,
    sample_count:1,comparable_sample_count:1,incomplete_sample_count:0,failed_sample_count:0,cancelled_sample_count:0,completion_condition:'same-version acceptance digest matched',
    task_measurement_status:taskMeasured?'measured':'unmeasured',task_measurement_reason:taskMeasured?'':'task-boundary-missing',task_completion_time_delta_ms:taskMeasured?100:null,task_completion_time_savings_ratio:timeRatio,task_effect_status:!taskMeasured?'unmeasured':timeRatio>0?'faster':timeRatio<0?'slower':'unchanged',task_time_ratio_range:taskMeasured?{min:timeRatio,max:timeRatio}:null,
    faster_sample_count:timeRatio>0?1:0,unchanged_sample_count:timeRatio===0?1:0,slower_sample_count:timeRatio<0?1:0,
    token_measurement_status:tokenMeasured?'measured':'unmeasured',token_measurement_reason:tokenMeasured?'':'usage-scope-incomplete',token_savings_ratio:tokenRatio,token_pair_median_savings_ratio:tokenRatio,token_ratio_range:tokenMeasured?{min:tokenRatio,max:tokenRatio}:null,token_aggregation:tokenMeasured?'ratio-of-complete-pair-totals':'unavailable',
    baseline_user_intervention_count:1,improved_user_intervention_count:2,user_intervention_pair_count:1,observability:{activity_intervals_are_non_additive:true,hidden_reasoning_status:'unknown'},...overrides};
}
const wrap=(...presentations)=>({kind:'click-task-efficiency-public',version:1,generated_at:1788784525,measurement_status:presentations.some(p=>p.task_measurement_status==='measured'||p.token_measurement_status==='measured')?'measured':'unmeasured',measurement_reason:presentations.length?'':'host-task-and-usage-boundaries-unavailable',presentations});
const batch=input.projection.batches.at(-1),totals=input.projection.batch_summaries[batch.batch_id];
for(const [ref,timeRatio,tokenRatio,taskText,tokenText] of [['a',.1,.125,'10% 빨라짐','12.5% 감소'],['b',0,0,'0% · 변화 없음','0% · 변화 없음'],['c',-.05,-.05,'5% 느려짐','5% 증가'],['d',.000001,.000001,'<0.01% 빨라짐','<0.01% 감소'],['e',-.000001,-.000001,'<0.01% 느려짐','<0.01% 증가']]){
  const parsed=api.readTaskEfficiency(wrap(taskPresentation(ref,timeRatio,tokenRatio)));
  api.setState(input.projection,batch,totals.incremental,totals.revalidation_savings,null,parsed);api.renderTaskEfficiency();
  assert.equal(doc.getElementById('taskEffectValue').textContent,taskText);assert.equal(doc.getElementById('tokenSavings').textContent,tokenText);
  assert.equal(doc.getElementById('tokenMeasuredBadge').hidden,false);assert(doc.getElementById('taskEffectScope').textContent.includes('B0→B2'));
  assert.equal(doc.getElementById('tokenSavings').dataset.state,tokenRatio<0?'increased':'measured');
}
const unavailable=api.readTaskEfficiency(wrap(taskPresentation('f',null,null,{comparable_sample_count:0,incomplete_sample_count:1,failed_sample_count:1,task_effect_status:'unmeasured'})));
api.setState(input.projection,batch,totals.incremental,totals.revalidation_savings,null,unavailable);api.renderTaskEfficiency();
assert.equal(doc.getElementById('taskEffectValue').textContent,'미측정');assert.equal(doc.getElementById('tokenSavings').textContent,'미측정');assert(doc.getElementById('taskEffectAdverse').textContent.includes('실패 1개'));
const realistic=wrap(taskPresentation('1',.1,.1));
const parsedTime=api.readTaskEfficiency(realistic);assert.equal(parsedTime.generated_at,1788784525);assert.equal(parsedTime.presentations[0].measured_at,1788784525);
for(const stamp of [-1,1.5,null,'1788784525',253402300800]){
  const invalid=JSON.parse(JSON.stringify(realistic));invalid.generated_at=stamp;assert.throws(()=>api.readTaskEfficiency(invalid));
  invalid.generated_at=realistic.generated_at;invalid.presentations[0].measured_at=stamp;assert.throws(()=>api.readTaskEfficiency(invalid));
}
const oversized=JSON.parse(JSON.stringify(realistic));oversized.presentations[0].sample_count=1788784525;assert.throws(()=>api.readTaskEfficiency(oversized));
const raw=wrap(taskPresentation('1',.1,.1));raw.presentations[0].input_tokens=123;assert.throws(()=>api.readTaskEfficiency(raw));
const two=api.readTaskEfficiency(wrap(taskPresentation('2',.1,.1),taskPresentation('3',-.1,-.1,{scenario:'code-change'})));
api.setState(input.projection,batch,totals.incremental,totals.revalidation_savings,null,two);api.renderTaskEfficiency();
assert.equal(doc.getElementById('taskEffectValue').textContent,'미측정');assert.equal(doc.getElementById('taskEvaluationLabel').hidden,false);
doc.getElementById('taskEvaluationSelect').onchange({target:{value:'2'.repeat(24)}});assert.equal(doc.getElementById('taskEffectValue').textContent,'10% 빨라짐');
const report=api.shareReport(),serialized=JSON.stringify(report);for(const key of ['input_tokens','output_tokens','cached_input_tokens','reasoning_output_tokens','baseline_total_tokens','improved_total_tokens'])assert(!serialized.includes(key));
assert.equal(report.task_efficiency.presentation.comparison_ref,'2'.repeat(24));assert(!serialized.includes('task-evaluation-private.json'));
const html=api.standaloneReport(report);assert(html.includes('토큰 절감률'));assert(html.includes('전체 작업 효과'));assert(!html.includes('input_tokens'));
for(const language of ['en','zh-CN']){api.setLanguage(language);assert(!/[가-힣]/u.test(doc.getElementById('taskEffect').textContent));assert(!/[가-힣]/u.test(doc.getElementById('tokenStatus').textContent));}
''')

    def test_imported_comparison_switches_language_without_changing_samples(self):
        self.run_language_script(r'''
const measured={version:2,kind:'click-paired-verification-benchmark',conditions:{iterations:1,warmups:0,workload_rounds:20,runtime_mode:'guarded',scope_equivalence:'same-two-unittest-files',authority:'real-hooks-and-one-use-runner',observer:'off',order:'alternating-pair-order'},samples:[{scenario:'unchanged',comparison:'same-shards',iteration:0,warmup:false,order:['baseline','incremental'],baseline:{wall_ms:10,status:'passed',executed_source_count:2,reused_source_count:0,not_run_source_count:0},incremental:{wall_ms:15,status:'passed',executed_source_count:0,reused_source_count:2,not_run_source_count:0}}]};
const parsed=api.readComparison(measured),original=JSON.stringify(parsed);
const batch=input.projection.batches.at(-1),totals=input.projection.batch_summaries[batch.batch_id];
api.setState(input.projection,batch,totals.incremental,totals.revalidation_savings,parsed);
for(const language of ['en','zh-CN']){
  api.setLanguage(language);
  assert(!/[가-힣]/u.test(doc.getElementById('comparisonInfo').textContent));
  assert(!/[가-힣]/u.test(doc.getElementById('comparisonChart').textContent));
  assert.equal(doc.getElementById('waitIncreaseNotice').hidden,false);
  const report=api.shareReport();assert(!/[가-힣]/u.test(report.comparison.conditions.cache));
  assert.equal(JSON.stringify(report.comparison.samples),JSON.stringify(parsed.samples));
  assert.equal(report.comparison.samples[0].delta_ms,-5);
  assert.equal(JSON.stringify(parsed),original);
  const html=api.standaloneReport(report);
  assert(html.includes(language==='en'?'Separate repository':'独立仓库'));
}
''')


class DashboardImpactRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node unavailable for export assertions")
    def test_real_evidence_flow_projects_and_exports_actual_partial_reuse(self):
        import tempfile
        from benchmarks.incremental_verification import Fixture
        from hooks import click_dashboard_projection

        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), 20, mode="evidence", partial_policy=True)
            baseline = fixture.verify()
            self.assertEqual(baseline["status"], "passed")
            fixture.change("partial-reuse")
            result = fixture.verify()
            self.assertEqual((result["executed_source_count"], result["reused_source_count"]), (1, 1))
            state = fixture.state()
            before = copy.deepcopy(state)
            data = click_dashboard_projection.dashboard_projection(state)
            self.assertTrue(click_dashboard_projection.projection_is_valid(data))
            self.assertEqual(state, before)
            batch = data["batches"][-1]
            summary = data["batch_summaries"][batch["batch_id"]]
            self.assertEqual(summary["incremental"]["total_source_count"], 2)
            savings = summary["revalidation_savings"]
            self.assertEqual(savings["coverage"]["actual_reused_source_count"], 1)
            self.assertEqual(savings["full_sequential_test_execution_estimate_ms"], savings["executed_test_execution_ms"] + savings["omitted_test_execution_ms"])
            script = UI_HARNESS + r'''
api.setState(input.projection,input.batch,input.summary,input.savings,null);
const report=api.shareReport();
assert.equal(report.summary.executed_source_count,1);assert.equal(report.summary.authoritative_reuse_count,1);
assert.equal(report.display.comparison_ready,true);
assert(report.batch.sources.some(item=>item.status==='reused'));assert(report.batch.sources.every(item=>!('reuse_origin' in item)));
const html=api.standaloneReport(report);
assert(html.includes(report.display.full_text));assert(html.includes(report.display.executed_text));
assert(!html.includes('src="http'));assert(!html.includes('<script'));
console.log(JSON.stringify({report,html}));
'''
            exported = subprocess.run(
                [shutil.which("node"), "-e", script],
                input=json.dumps({"script": click_shadow_dashboard.JS, "projection": data, "batch": batch, "summary": summary["incremental"], "savings": savings}),
                text=True, encoding="utf-8", errors="replace",
                capture_output=True, check=False,
            )
            self.assertEqual(exported.returncode, 0, exported.stderr)
            output = json.loads(exported.stdout)
            artifact = Path(tempfile.mkdtemp(prefix="click-impact-e2e-"))
            (artifact / "projection.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (artifact / "report.json").write_text(
                json.dumps(output["report"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (artifact / "report.html").write_text(output["html"], encoding="utf-8")
            print(f"Real Hook/runner dashboard/export evidence: {artifact}")


if __name__ == "__main__":
    unittest.main()
