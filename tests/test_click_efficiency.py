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
const expose='  globalThis.api={readComparison,renderMap,renderComparison,outcomePresentation,renderOutcome,explain,standaloneReport,shareReport,setState(data,batch,summary,savings,measured){snapshot=data;activeBatch=batch;activeSummary=summary;activeSavings=savings;comparison=measured;}};';
vm.runInNewContext(input.script.replace(marker,expose),context);
const api=context.api;
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
  samples:[{}],comparison_samples:workflowSamples,stage_summaries:Array(32).fill({}),cumulative_summaries:Array(4).fill({}),workflow_cost_summaries:Array(2).fill({}),summaries:Array(4).fill({}),
  repository_reference:{version:1,kind:'click-repository-bundle-reference',source:'current-repository-test-bundle',unit:'ms',scope_digest:'c'.repeat(64),
    conditions:{iterations:1,warmups:0,shard_count:6,scope_basis:'committed-evidence-shards-v1-inventory',measurement_order:'alternating-pair-order',cache:'same-working-tree-and-environment; OS-cache-not-flushed; bytecode-disabled',measurement_scope:'driver-command-dispatch-through-return'},
    samples:[{iteration:0,warmup:false,order:['same-shards','parent-suite'],eligible:true,excluded_reason:'',same_shards:{duration_ms:10,status:'passed',exit_code:0,executed_command_count:6,not_run_command_count:0},parent_suite:{duration_ms:11,status:'passed',exit_code:0,executed_command_count:1,not_run_command_count:0},delta_ms:1,delta_percent:100/11}],
    summary:{eligible_samples:1,same_shards_duration_ms:{median:10,min:10,max:10},parent_suite_duration_ms:{median:11,min:11,max:11},parent_minus_shards_ms:{median:1,min:1,max:1}},limitations:['reference-only']},dashboard_snapshot:null,limitations:[]};
const workflowSafe=api.readComparison(workflow);
assert.equal(workflowSafe.version,2);assert.equal(workflowSafe.engine.version,'0.82.0+codex.20260906090212');assert.equal(workflowSafe.samples.length,32);
assert.equal(workflowSafe.repository_reference.conditions.shard_count,6);
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
assert.equal(doc.getElementById('reductionRate').textContent,'약 100%');
api.renderOutcome(input.cases.noReuse.batch,input.cases.noReuse.summary,input.cases.noReuse.savings);
assert.equal(doc.getElementById('estimatedAvoided').textContent,'0 ms');
assert.equal(doc.getElementById('executedBar').style.width,'100%');
assert.equal(doc.getElementById('reductionRate').textContent,'약 0%');
api.renderOutcome(input.cases.partial.batch,input.cases.partial.summary,input.cases.partial.savings);
assert.match(doc.getElementById('estimatedAvoided').textContent,/^≥ /);
assert.equal(doc.getElementById('timingCoverage').textContent,'1/2');
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
assert.equal(report.version,3);assert.deepEqual(report.revalidation_savings,input.savings);
assert.equal(report.labels.omitted,'재사용으로 생략한 테스트 실행시간');
assert.equal(report.measurement.click_management_overhead_ms,null);
assert.equal(report.measurement.live_net_time_saving_reason,'counterfactual-not-measured');
assert.equal(report.summary.authoritative_reuse_count,input.summary.authoritative_reuse_count);
assert.equal(report.batch.sources[0].reuse_origin.origin_revision,7);
const html=api.standaloneReport(report);assert(!html.includes('<script>'));assert(html.includes('&lt;script&gt;'));
assert(html.includes('이전 계약에서 재판정'));
assert(html.includes('Click 전체 관리비용: 측정 정보 없음'));
assert(!html.includes('src="http'));assert(!html.includes('href="http'));
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
        self.assertIn("시간 표본 1/2개 · 확인된 표본 합계 ≥ 50 ms", partial_host)
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


if __name__ == "__main__":
    unittest.main()
