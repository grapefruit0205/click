#!/usr/bin/env python3
"""Authenticated loopback viewer for non-authoritative Shadow telemetry."""

from __future__ import annotations

import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from typing import Any, Callable
from urllib.parse import urlsplit

if __package__:
    from . import (
        click_contract_state,
        click_dashboard_projection,
        click_process,
        click_runtime_state,
        click_state,
    )
else:  # Executed directly from the bundled hooks directory.
    import click_contract_state
    import click_dashboard_projection
    import click_process
    import click_runtime_state
    import click_state


DASHBOARD_FIELD = "shadow_dashboard"
DASHBOARD_STATE_VERSION = 1
DASHBOARD_STATUSES = frozenset(
    {"idle", "starting", "running", "stopping", "stopped", "failed"}
)
DASHBOARD_ACTIONS = frozenset({"start", "stop", "status"})
START_TIMEOUT_SECONDS = 8
STOP_TIMEOUT_SECONDS = 8
MAX_LIFETIME_SECONDS = 2 * 60 * 60
POLL_SECONDS = 0.1

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_INSTANCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_DASHBOARD_FIELDS = frozenset(
    {
        "version",
        "status",
        "instance_id",
        "runner_token_digest",
        "runner_claimed_at",
        "access_token_digest",
        "port",
        "pid",
        "started_at",
        "stop_requested",
        "last_error",
    }
)

RenderCommand = Callable[[list[str]], str]


HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Click Incremental Verification</title>
  <link rel="stylesheet" href="/styles.css">
</head>
<body>
  <a class="skip" href="#main">대시보드로 건너뛰기</a>
  <header>
    <div class="brand"><span class="mark" aria-hidden="true">C</span><div><b>Click</b><span>Incremental Verification</span></div></div>
    <div class="live"><i aria-hidden="true"></i><span id="connection">연결 중</span></div>
  </header>
  <main id="main">
    <section class="hero" aria-labelledby="title">
      <div><p class="eyebrow">승인한 약속 · 확인된 결과</p><h1 id="title">약속한 범위 안에서,<br>유효한 검증을 이어서.</h1><p id="taskline">현재 Click 상태를 불러오는 중…</p><p id="batchHeadline">실제 검증 기록을 기다리고 있습니다.</p></div>
      <div class="notice"><strong id="topComparison">비교 실측 없음</strong><span>과거 실행시간은 비용 회피 추정입니다. 실제 순절감률은 동등한 비교 실측이 있어야 계산합니다.</span></div>
    </section>
    <section class="panel contract-panel"><div class="panel-title"><h2 id="contractName">현재 작업</h2><span id="approvalState" class="pill">승인 상태 확인 중</span></div><div id="contractPromises"></div><details><summary>약속 전체·포함·제외 범위와 유지 조건</summary><div id="allPromises"></div><div id="contractBoundary"></div><p class="muted">로컬 표시용 요약입니다. 전체 약속과 승인 대상은 제시된 계약 원문을 기준으로 합니다. 표시 정보와 viewer 이력은 실행 권한이 아닙니다.</p><code id="contractId"></code></details><p id="controlSummary" class="muted"></p></section>
    <section class="metrics" aria-label="실제 증분 검증 결과">
      <article><span>요청한 검증 묶음</span><strong id="currentChecks">—</strong><small>개별 테스트 케이스 수가 아닙니다</small></article>
      <article><span>실제 실행</span><strong id="executedChecks">—</strong><small id="executionDetail">실제 시작한 검증 묶음</small></article>
      <article><span>실제 재사용</span><strong id="reusedChecks">—</strong><small>권한 규칙을 통과해 적용된 결과</small></article>
      <article><span>재실행 비용 회피 추정</span><strong id="estimatedAvoided">—</strong><small id="estimateCoverage">과거 실행시간 기반 · 실측 절약 아님</small></article>
    </section>
    <section class="metric-split" aria-label="실제 결과와 Shadow 텔레메트리">
      <article class="panel compact"><p class="eyebrow">실측 · 이번 검증</p><h2>측정된 구간과 빠진 구간</h2><div class="statline"><span>Hook 진입 → 결과 기록 · 부분 실측</span><strong id="requestWall">—</strong></div><div class="statline"><span>측정 가능한 처리 구간</span><strong id="processingDuration">—</strong></div><p class="muted" id="timeScope">호스트 요청 전·최종 반환은 계측 범위 밖입니다.</p><div class="statline"><span>검사 실행 구간 (Observer 포함)</span><strong id="executedDuration">—</strong></div><div class="statline"><span>동일 상태 / 관찰 입력 / 안전 변경 재사용</span><strong id="reuseBreakdown">—</strong></div></article>
      <article class="panel compact"><p class="eyebrow">재사용의 출처와 비교 조건</p><h2 id="comparisonStatus">비교 실측 없음</h2><p id="reuseOrigins"></p><p id="reuseRate" class="muted"></p><p id="zeroReuse" class="muted"></p><p class="muted">과거 성공의 실행시간 합은 추정치입니다. 동등한 비교 실측 없이 순절감률·토큰·요금·전체 개발시간으로 환산하지 않습니다.</p></article>
    </section>
    <section class="panel timeline"><div class="panel-title"><div><p class="eyebrow">변경별 타임라인</p><h2>최근 검증 배치</h2></div><button id="latestBatch" type="button">최신 배치</button></div><label for="batchSelect">상세 결과 선택</label> <select id="batchSelect"></select><p id="batchState" class="muted"></p><p id="historyMeta" class="muted"></p></section>
    <section class="workspace">
      <article class="panel checks"><div class="panel-title"><div><p class="eyebrow">검사 목록</p><h2>실행 또는 재사용 결과</h2></div><span id="sourceCount" class="count">0</span></div><div id="sources" class="source-list"></div></article>
    </section>
    <section class="panel explanation" aria-live="polite"><p class="eyebrow">판정 이유</p><h2 id="whyTitle">검사를 선택하세요</h2><p id="whyBody">왜 실행했거나 재사용했는지 사람말로 설명합니다.</p><p id="originName"></p><details><summary>원계약·검사·revision·판정 상세</summary><div id="limits"></div></details></section>
    <details class="panel telemetry"><summary>Evidence Map · Shadow 관찰 상세 · 재사용 권한 없음</summary><p><strong id="observerTitle">Observer 확인 중</strong> · <span id="observerBody">Dashboard와 Observer는 독립적입니다.</span></p><article class="map-panel"><div class="panel-title"><h2>선택한 검사의 관찰된 입력</h2><span id="mapMeta" class="muted"></span></div><div id="emptyMap" class="empty">검사를 선택하면 현재 입력과 이전 baseline의 관계를 보여줍니다.</div><svg id="map" role="img" aria-label="선택한 검사의 Evidence Map"></svg></article><article class="compact shadow-card"><h2>잠재 재사용 관찰</h2><div class="statline"><span>후보 / 확인 / 모순</span><strong id="shadowBreakdown">—</strong></div><div class="statline"><span>잠재 시간 / 관찰기 처리 비용</span><strong id="shadowTiming">—</strong></div><p class="muted" id="tracingSlowdown">추적으로 인한 검사 지연은 별도 측정하지 않았습니다.</p></article></details>
    <section class="panel comparison"><p class="eyebrow">명시적으로 실행한 비교 측정 · 일상 추정치와 별개</p><h2>전체 재실행 기준 vs 증분 실행</h2><p id="comparisonInfo">아직 비교 측정이 없습니다. 아래 명령을 로컬에서 실행한 뒤 JSON을 선택하세요.</p><pre>python3 benchmarks/incremental_verification.py --iterations 3 --warmups 1 --output /tmp/click-comparison.json</pre><label>비교 JSON 선택 <input id="comparisonFile" type="file" accept="application/json,.json"></label><div id="comparisonChart"></div></section>
    <section class="panel exports"><h2>공유 리포트</h2><p class="muted">현재 선택한 배치와 가져온 비교 측정을 내보냅니다. 파일 경로·원시 명령·환경 값·토큰은 제외합니다. 검증은 실행하지 않습니다.</p><button type="button" id="exportJson">JSON 내보내기</button> <button type="button" id="exportHtml">독립형 HTML 내보내기</button><span id="exportStatus" role="status"></span></section>
  </main>
  <footer><span>로컬 전용 · 읽기 전용 · 파일 내용 미노출</span><span id="updated">아직 갱신되지 않음</span></footer>
  <script src="/app.js" defer></script>
</body>
</html>
"""

CSS = """:root{color-scheme:dark;--bg:#0b0d12;--panel:#121722;--line:#253044;--text:#f5f7fb;--muted:#8e9bb0;--green:#73e6a2;--amber:#ffca6a;--red:#ff7b82;--blue:#79b8ff;--violet:#ad8cff;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 12% -8%,#1d2940 0,transparent 32%),var(--bg);color:var(--text);min-height:100vh}.skip{position:absolute;left:-999px}.skip:focus{left:16px;top:12px;z-index:9;background:#fff;color:#000;padding:8px}header,footer{height:72px;padding:0 max(24px,calc((100vw - 1320px)/2));display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line)}footer{height:auto;min-height:56px;border:0;border-top:1px solid var(--line);color:var(--muted);font-size:12px}.brand{display:flex;gap:12px;align-items:center}.brand div{display:grid}.brand span{color:var(--muted);font-size:12px}.mark{display:grid;place-items:center;width:38px;height:38px;border-radius:11px;background:linear-gradient(135deg,var(--violet),var(--blue));color:#080a0e!important;font-weight:900;font-size:20px!important}.live{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:13px}.live i{width:8px;height:8px;border-radius:50%;background:var(--amber);box-shadow:0 0 14px var(--amber)}.live.ok i{background:var(--green);box-shadow:0 0 14px var(--green)}main{max-width:1320px;margin:auto;padding:44px 24px 56px}.hero{display:flex;justify-content:space-between;align-items:end;gap:32px;margin-bottom:28px}.eyebrow{margin:0 0 8px;color:var(--blue);font-size:10px;font-weight:800;letter-spacing:.16em}h1{margin:0;font-size:clamp(34px,5vw,64px);line-height:1;letter-spacing:-.055em}h2{font-size:18px;margin:0}#taskline{color:var(--muted);margin:14px 0 0}.notice{max-width:330px;border:1px solid #4f4128;background:#1c1811;border-radius:14px;padding:14px 16px;display:grid;gap:4px}.notice strong{color:var(--amber)}.notice span{color:#c7b991;font-size:12px;line-height:1.5}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:12px}.metrics article,.panel{background:linear-gradient(180deg,rgba(24,31,45,.96),rgba(15,20,29,.96));border:1px solid var(--line);border-radius:18px}.metrics article{padding:20px;display:grid;gap:8px}.metrics span,.metrics small{color:var(--muted);font-size:11px}.metrics strong{font-size:28px;letter-spacing:-.04em}.workspace{display:grid;grid-template-columns:380px 1fr;gap:12px}.panel{padding:22px}.panel-title{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}.count{display:grid;place-items:center;min-width:28px;height:28px;border-radius:9px;background:#202a3a;color:var(--blue);font-weight:800}.muted{color:var(--muted);font-size:11px}.source-list{display:grid;gap:9px;max-height:490px;overflow:auto}.source{appearance:none;width:100%;text-align:left;color:inherit;background:#0f141e;border:1px solid #263146;border-radius:13px;padding:14px;cursor:pointer}.source:hover,.source:focus-visible,.source.active{border-color:var(--blue);outline:none}.source-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:7px}.source strong{font-size:14px}.pill{font-size:10px;padding:4px 7px;border-radius:999px;background:#202a3a;color:var(--muted)}.pill.reuse-candidate,.pill.confirmed-candidate{color:var(--green);background:#14291e}.pill.rerun-required,.pill.contradicted-candidate{color:var(--red);background:#31191d}.source p{margin:0;color:var(--muted);font-size:11px}.map-panel{min-height:550px;overflow:hidden;position:relative}.empty{height:420px;display:grid;place-items:center;text-align:center;color:var(--muted);padding:40px}svg{width:100%;height:440px;display:none}.edge{stroke:#34435d;stroke-width:1.2}.node text{fill:var(--text);font-size:11px}.node rect{fill:#101722;stroke:#31405a;rx:9}.node.source rect{stroke:var(--blue);fill:#132033}.node.changed rect{stroke:var(--red);fill:#2b171b}.explanation{margin-top:12px;min-height:138px}.explanation p:not(.eyebrow){color:var(--muted);line-height:1.6;margin-bottom:0}.tag{display:inline-block;margin:12px 6px 0 0;padding:5px 8px;border-radius:8px;background:#292319;color:var(--amber);font-size:11px}@media(max-width:900px){.metrics{grid-template-columns:repeat(2,1fr)}.workspace{grid-template-columns:1fr}.hero{align-items:start;flex-direction:column}.notice{max-width:none;width:100%}}@media(max-width:520px){main{padding:30px 14px}.metrics{grid-template-columns:1fr 1fr}.metrics article{padding:15px}.metrics strong{font-size:22px}header,footer{padding-left:14px;padding-right:14px}.panel{padding:16px}}@media(prefers-reduced-motion:no-preference){.live i{animation:pulse 2s infinite}@keyframes pulse{50%{opacity:.45}}}
"""

CSS += """.metric-split{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}.compact{padding:18px 20px}.compact h2{margin-bottom:14px}.statline{display:flex;justify-content:space-between;gap:18px;padding-top:10px;margin-top:10px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}.statline strong{color:var(--text);text-align:right}.shadow-card{border-color:#3d3459}.pill.reused{color:var(--green);background:#14291e}.pill.rerun{color:var(--red);background:#31191d}.pill.pending{color:var(--amber);background:#292319}.source .reason{margin-top:7px;color:#c3ccda}.node.changed rect{stroke:var(--red);fill:#2b171b}.node.baseline-only rect{stroke:var(--amber);fill:#292319}.node.newly-observed rect{stroke:var(--violet);fill:#211a32}@media(max-width:900px){.metric-split{grid-template-columns:1fr}}"""


CSS += """.timeline,.comparison,.exports{margin:12px 0}#batchHeadline{font-size:20px;color:var(--text)}button,select,input{font:inherit}select{max-width:100%;background:#141c29;color:var(--text);padding:10px;border:1px solid var(--line);border-radius:9px}button:not(.source){background:#243650;color:var(--text);border:1px solid #456087;padding:10px 14px;border-radius:9px;cursor:pointer}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--blue);outline-offset:3px}.comparison pre{overflow:auto;background:#0c111a;padding:14px;border-radius:10px;font-size:12px}.comparison-row{border-top:1px solid var(--line);padding:18px 0}.comparison-row h3{font-size:14px}.comparison-bar{min-width:190px;border-radius:5px;margin:8px 0;padding:8px;font-size:12px;background:#204d76;white-space:nowrap}.comparison-bar.incremental{background:#513575}.comparison-row p{font-size:12px;color:var(--muted)}.comparison-row p.slower{color:var(--red)}.exports h2{margin-bottom:10px}.exports span{font-size:12px;margin-left:12px}.timeline label{font-size:12px}#timeScope{line-height:1.6}#batchState{line-height:1.6}#executionDetail{line-height:1.5}.metrics small{line-height:1.5}"""

CSS += """.contract-panel{margin-bottom:16px}.contract-panel p{line-height:1.6}.contract-panel code{overflow-wrap:anywhere;color:var(--muted);font-size:11px}details summary{cursor:pointer;padding:10px 0;color:var(--blue)}details[open] summary{margin-bottom:12px}.workspace{grid-template-columns:1fr}.source-list{grid-template-columns:repeat(2,minmax(0,1fr));max-height:360px}.telemetry{margin-top:12px}.telemetry .map-panel{min-height:0}.contract-panel .pill{white-space:nowrap}#reuseRate,#zeroReuse,#controlSummary{line-height:1.6}@media(max-width:650px){.source-list{grid-template-columns:1fr}.contract-panel .panel-title{align-items:start;gap:12px;flex-direction:column}}"""

CSS += """main{padding-top:24px}.hero{margin-bottom:16px;align-items:center}h1{font-size:clamp(28px,3.1vw,40px);line-height:1.1}#taskline{margin-top:8px}#batchHeadline{margin:8px 0 0;font-size:17px}.contract-panel{padding:16px 20px}.contract-panel .panel-title{margin-bottom:8px}#contractPromises{display:flex;gap:24px}#contractPromises p{flex:1;margin:4px 0;font-size:13px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}#controlSummary{margin:6px 0 0}.metrics article{padding:16px}.notice{padding:12px;max-width:300px}.notice span{font-size:11px}@media(max-width:650px){#contractPromises{display:block}.hero{gap:14px}.notice{box-sizing:border-box}}"""

JS = r"""(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const fmt = ms => !Number.isFinite(ms) ? '측정 정보 없음' : ms < 1000 ? `${ms.toFixed(1)} ms` : `${(ms / 1000).toFixed(2)}초`;
  const count = value => Number.isInteger(value) ? String(value) : '알 수 없음';
  const token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
  history.replaceState(null, '', location.pathname);
  let selected = '';
  let snapshot = null;
  let selectedBatch = '';
  let activeBatch = null;
  let activeSummary = null;
  let comparison = null;
  let lastSnapshotSignature = '';
  const statusText = {
    planned: '실행 예정', 'reuse-pending': '재사용 예정 · 미적용', running: '실행 중',
    passed: '통과', failed: '실패', interrupted: '중단 · 일부 결과 미확정',
    'not-run': '미실행', reused: '재사용 적용', unknown: '측정 정보 없음',
    rejected: '실행 전 거부', incomplete: '미확정', evidence: 'Evidence', staged: '승인 대기', approved: '승인됨', none: '활성 작업 없음'
  };
  const outcomeText = {
    'request-rejected': '요청이 거부되어 시작하지 않았습니다.',
    'runner-admission-rejected': '실행 전 안전 조건을 통과하지 못해 시작하지 않았습니다.',
    'preceding-check-stopped': '앞선 검사 실패나 중단 때문에 시작하지 않았습니다.',
    'workspace-invalidated': '검증 중 코드가 변경되어 결과를 현재 상태에 사용할 수 없습니다.',
    'reservation-expired': '실행이 시작되기 전에 요청이 만료되었습니다.',
    'user-cancelled': '사용자가 취소했습니다. 실행 중이던 검사의 종료 여부는 미확정입니다.',
    'command-error': '실행 경계에서 오류가 발생했습니다. 같은 검사를 다시 실행하지 않았습니다.',
    'command-interrupted': '검사 실행이 중단되었습니다.',
    'outcome-unconfirmed': '종료 결과를 확인하지 못했습니다.'
    , 'not-requested': '아직 요청되지 않은 검증입니다. 승인 후 기준 검증을 실행하세요.'
  };

  const executionLabels = {
    'run': ['재실행', 'rerun'],
    'not-evaluable': ['재실행', 'rerun'],
    'reuse-exact': ['재사용', 'reused'],
    'reuse-dependency': ['재사용', 'reused'],
    'reuse-safe-change': ['재사용', 'reused'],
    'not-planned': ['대기', 'pending']
  };
  const reasonText = {
    'same-revision-receipt-current': '같은 revision의 검사 결과가 현재 작업트리와 정확히 일치해 재사용했습니다.',
    'successor-evidence-current': '이전 작업의 실제 통과 결과를 현재 명령·작업트리·환경·실행 파일·호스트 범위에 다시 결합해 재사용했습니다.',
    'successor-evidence-dependencies-unchanged': '이전 작업의 실제 통과 결과를 가져와, 현재 변경 뒤에도 관찰된 입력이 바뀌지 않았음을 다시 확인해 재사용했습니다.',
    'successor-evidence-safe-change-covered': '이전 작업의 실제 통과 결과를 가져와, 사전에 커밋된 안전 변경 정책이 이번 변경을 허용하는지 다시 확인해 재사용했습니다.',
    'successor-evidence-scope-mismatch': '이전 결과가 현재 호스트 세션과 작업 공간의 후속 작업 범위에 속하지 않아 실제 검사를 실행했습니다.',
    'successor-evidence-integrity-invalid': '이전 실행 사실의 무결성이나 출처를 확인할 수 없어 실제 검사를 실행했습니다.',
    'observed-dependencies-unchanged': '이 검사가 실제로 읽었던 입력이 바뀌지 않아 이전 통과 결과를 재사용했습니다.',
    'safe-change-policy-covered': '저장소 소유자가 미리 허용한 안전 변경 범위 안이라 이전 결과를 재사용했습니다.',
    'no-passing-evidence': '재사용할 수 있는 이전 통과 결과가 없어 실제 검사를 실행했습니다.',
    'previous-verification-failed': '이전 검사가 통과하지 않아 실제 검사를 다시 실행했습니다.',
    'observed-input-changed': '이 검사가 읽었던 입력이 변경되어 실제 검사를 다시 실행했습니다.',
    'check-binding-changed': '검사 명령의 결합 정보가 달라져 실제 검사를 실행했습니다.',
    'contract-binding-changed': '승인 계약의 결합 정보가 달라져 실제 검사를 실행했습니다.',
    'environment-binding-changed': '검사 환경이 달라져 실제 검사를 실행했습니다.',
    'executable-binding-changed': '검사 실행 파일이 달라져 실제 검사를 실행했습니다.',
    'host-coverage-binding-changed': '호스트 Hook 관찰 범위가 달라져 실제 검사를 실행했습니다.',
    'workspace-ambiguous': '현재 작업트리를 확실히 식별할 수 없어 안전하게 실제 검사를 실행했습니다.',
    'mutation-boundary-ambiguous': '변경 전후 경계를 확실히 묶을 수 없어 실제 검사를 실행했습니다.',
    'observer-incomplete': '의존성 관찰이 완전하지 않아 실제 검사를 실행했습니다.',
    'external-input-unmodeled': '저장소 밖 입력이 관찰되어 실제 검사를 실행했습니다.',
    'policy-unavailable': '적용할 수 있는 재사용 정책이 없어 실제 검사를 실행했습니다.',
    'safe-change-policy-not-covered': '변경이 안전 변경 정책 범위를 벗어나 실제 검사를 실행했습니다.',
    'receipt-invalid': '이전 영수증을 현재 상태에 유효하게 결합할 수 없어 실제 검사를 실행했습니다.',
    '': '아직 이 검사에 대한 실행 계획이 없습니다.'
  };
  const inputStatus = {
    'current-observed': '현재 관찰됨',
    'changed': '변경됨',
    'baseline-only': '이전 baseline에만 존재',
    'newly-observed': '현재 새로 관찰됨'
  };

  function reasonFor(source) {
    if (source.execution_reason_code === 'user-cancelled' && source.execution_status === 'not-run') return '실행 전에 취소되어 시작하지 않았습니다. 이전 계약의 승인이나 실행 권한은 이어받지 않습니다.';
    if (outcomeText[source.execution_reason_code]) return outcomeText[source.execution_reason_code];
    if (source.execution_status === 'unknown') return '실제 실행 기록이 없는 이전 데이터입니다. 계획을 실행 실적으로 표시하지 않습니다.';
    const planned = source.execution_status === 'planned' || source.execution_status === 'reuse-pending';
    // Shadow paths are not an explanation of an authoritative dependency decision.
    const text = reasonText[source.reason_code] || '판정 근거 정보가 없습니다.';
    return planned ? `계획: ${text.replaceAll('실행했습니다', '실행할 예정입니다').replaceAll('재사용했습니다', '재사용할 예정입니다')}` : text;
  }

  function explain(source) {
    selected = source.id;
    const label = statusText[source.execution_status] || '측정 정보 없음';
    $('whyTitle').textContent = `${source.label} · ${label}`;
    $('whyBody').textContent = `${reasonFor(source)} ${source.next_action || ''}`;
    $('originName').textContent = source.reuse_origin ? `${source.origin_name || '이전 작업'} → 원본 검사 ${source.origin_check_label || source.label} → 현재 계약에서 재판정` : ['passed','failed','interrupted','reused'].includes(source.execution_status) ? '현재 계약 안에서 관측한 결과입니다.' : '아직 실행·재사용 결과가 없습니다.';
    const tags = [
      `현재 revision ${source.current_revision}`,
      source.previous_revision >= 0 ? `이전 성공 revision ${source.previous_revision}` : '이전 성공 없음',
      `계획 ${executionLabels[source.execution_decision]?.[0] || '없음'} · 실제 ${label}`,
      `실행 구간 ${fmt(source.duration_ms)}`,
      source.duration_baseline ? `과거 표본 ${source.duration_baseline.sample_count}개 · revision ${source.duration_baseline.revision} · ${fmt(source.duration_baseline.duration_ms)}` : '과거 시간 표본 없음',
      source.reuse_origin ? `원본 ${source.reuse_origin.contract_id || source.reuse_origin.evidence_session_id} · 배치 ${source.reuse_origin.batch_id} · 출처 revision ${source.reuse_origin.origin_revision}` : '현재 작업 안의 근거',
      `정확한 검사 결합 ${source.check_digest || source.duration_baseline?.check_digest || '정보 없음'}`,
      `판정 식별자 ${source.reason_code || '없음'}`,
      ...source.shadow_limitations.map(item => `Shadow: ${item}`)
    ];
    $('limits').replaceChildren(...tags.map(text => {
      const element = document.createElement('span');
      element.className = 'tag';
      element.textContent = text;
      return element;
    }));
    document.querySelectorAll('.source').forEach(element => {
      element.classList.toggle('active', element.dataset.id === selected);
    });
    renderMap(batchView(snapshot, activeBatch), source);
  }

  function renderSources(data) {
    const root = $('sources');
    root.replaceChildren();
    data.sources.forEach(source => {
      const button = document.createElement('button');
      button.className = 'source';
      button.dataset.id = source.id;
      button.type = 'button';
      const head = document.createElement('div');
      head.className = 'source-head';
      const name = document.createElement('strong');
      name.textContent = source.label;
      const label = statusText[source.execution_status] || '측정 정보 없음';
      const className = source.execution_status === 'reused' ? 'reused' : ['failed', 'interrupted'].includes(source.execution_status) ? 'rerun' : 'pending';
      const pill = document.createElement('span');
      pill.className = `pill ${className}`;
      pill.textContent = label;
      head.append(name, pill);
      const meta = document.createElement('p');
      meta.textContent = source.execution_status === 'reused'
        ? `실행 생략 · 과거 실행 표본 ${fmt(source.duration_baseline?.duration_ms)}`
        : `계획: ${executionLabels[source.execution_decision]?.[0] || '없음'} · 실행 구간 ${fmt(source.duration_ms)}`;
      const reason = document.createElement('p');
      reason.className = 'reason';
      reason.textContent = reasonFor(source);
      button.append(head, meta, reason);
      button.onclick = () => explain(source);
      root.append(button);
    });
    $('sourceCount').textContent = String(data.sources.length);
    const current = data.sources.find(source => source.id === selected) || data.sources[0];
    if (current) {
      explain(current);
    } else {
      selected = '';
      $('whyTitle').textContent = '아직 계획된 검사가 없습니다';
      $('whyBody').textContent = '검증 계획이 생성되면 실행과 재사용 이유가 여기에 표시됩니다.';
      renderMap(data, null);
    }
  }

  function svgElement(name, attributes = {}) {
    const element = document.createElementNS('http://www.w3.org/2000/svg', name);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
    return element;
  }

  function renderMap(data, selectedSource) {
    const svg = $('map');
    const empty = $('emptyMap');
    svg.replaceChildren();
    if (!selectedSource) {
      svg.style.display = 'none';
      empty.style.display = 'grid';
      $('mapMeta').textContent = '';
      return;
    }
    const sourceNode = data.map.nodes.find(node => node.id === selectedSource.id);
    const selectedEdges = data.map.edges.filter(edge => edge.source === selectedSource.id);
    const targetIds = new Set(selectedEdges.map(edge => edge.target));
    const inputNodes = data.map.nodes.filter(node => targetIds.has(node.id)).slice(0, 48);
    const visibleIds = new Set(inputNodes.map(node => node.id));
    const edges = selectedEdges.filter(edge => visibleIds.has(edge.target));
    if (!sourceNode) {
      svg.style.display = 'none';
      empty.style.display = 'grid';
      empty.textContent = '이 과거 배치의 입력 그래프는 보관하지 않습니다. 최신 배치에서 현재 Evidence Map을 볼 수 있습니다.';
      $('mapMeta').textContent = '';
      return;
    }
    empty.style.display = 'none';
    svg.style.display = 'block';
    const positions = new Map();
    positions.set(sourceNode.id, {x: 20, y: 28, w: 160, h: 48});
    inputNodes.forEach((node, index) => {
      positions.set(node.id, {x: 260 + (index % 2) * 225, y: 12 + Math.floor(index / 2) * 58, w: 195, h: 42});
    });
    const height = Math.max(440, Math.ceil(inputNodes.length / 2) * 58 + 30);
    svg.setAttribute('viewBox', `0 0 700 ${height}`);
    edges.forEach(edge => {
      const from = positions.get(edge.source);
      const to = positions.get(edge.target);
      if (from && to) {
        svg.append(svgElement('line', {class: 'edge', x1: from.x + from.w, y1: from.y + from.h / 2, x2: to.x, y2: to.y + to.h / 2}));
      }
    });
    [sourceNode, ...inputNodes].forEach(node => {
      const position = positions.get(node.id);
      const group = svgElement('g', {class: `node ${node.type} ${node.status}`});
      group.append(svgElement('rect', {x: position.x, y: position.y, width: position.w, height: position.h}));
      const text = svgElement('text', {x: position.x + 10, y: position.y + 18});
      const label = node.label.length > 25 ? `${node.label.slice(0, 22)}…` : node.label;
      text.textContent = label;
      group.append(text);
      if (node.type === 'input') {
        const status = svgElement('text', {x: position.x + 10, y: position.y + 33, class: 'muted'});
        status.textContent = inputStatus[node.status];
        group.append(status);
      }
      svg.append(group);
    });
    const hidden = Math.max(0, selectedSource.input_count - inputNodes.length);
    $('mapMeta').textContent = hidden > 0 ? `입력 ${inputNodes.length}개 표시 · ${hidden}개 생략` : `입력 ${inputNodes.length}개`;
  }

  function summarize(batch) {
    // Counts project witnessed statuses. No client-side reuse decision is made.
    const items = batch.sources;
    const reused = items.filter(item => item.status === 'reused');
    const started = items.filter(item => item.started);
    const baselines = reused.map(item => item.duration_baseline).filter(Boolean);
    const sum = values => values.every(Number.isFinite) ? values.reduce((a,b) => a+b, 0) : null;
    return {
      total_source_count: batch.requested_source_count,
      planned_execution_source_count: items.filter(item => ['run','not-evaluable'].includes(item.decision)).length,
      executed_source_count: started.length, authoritative_reuse_count: reused.length,
      passed_source_count: items.filter(item => item.status === 'passed').length,
      failed_source_count: items.filter(item => item.status === 'failed').length,
      interrupted_source_count: items.filter(item => item.status === 'interrupted').length,
      not_run_source_count: items.filter(item => item.status === 'not-run').length,
      pending_source_count: items.filter(item => ['planned','reuse-pending','running','unknown'].includes(item.status)).length,
      exact_reuse_count: reused.filter(item => item.decision === 'reuse-exact').length,
      dependency_reuse_count: reused.filter(item => item.decision === 'reuse-dependency').length,
      safe_change_reuse_count: reused.filter(item => item.decision === 'reuse-safe-change').length,
      executed_duration_ms: sum(started.map(item => item.duration_ms)),
      request_wall_ms: batch.request_wall_ms,
      measured_processing_ms: batch.runner_duration_ms === null ? batch.prepare_duration_ms : sum([batch.prepare_duration_ms,batch.runner_duration_ms]),
      estimated_avoided_ms: baselines.length || !reused.length ? sum(baselines.map(item => item.duration_ms)) : null,
      estimated_source_count: baselines.length, baseline_sample_count: baselines.length
    };
  }

  function batchView(data, batch) {
    if (!batch) return data;
    const current = batch.batch_id === data.history.current_batch_id;
    return {...data, sources: batch.sources.map(item => {
      const id = `source:${item.source_key.slice(0,16)}`;
      const source = current ? data.sources.find(source => source.id === id) : null;
      const originBatch = item.reuse_origin ? data.batches.find(previous=>previous.task?.id === (item.reuse_origin.contract_id || item.reuse_origin.evidence_session_id)) : null;
      return {...(source || {input_count:0, changed_inputs:[], shadow_limitations:[], observer_status:'unavailable'}),
        id, label:item.label, status:item.status, execution_status:item.status,
        execution_decision:item.decision || 'not-planned', reason_code:item.reason_code,
        execution_reason_code:item.execution_reason_code, current_revision:item.current_revision,
        previous_revision:item.previous_revision, duration_ms:item.duration_ms, duration_baseline:item.duration_baseline,
        authority_source:item.authority_source, reuse_origin:item.reuse_origin, check_digest:item.check_digest,
        origin_name:source?.origin_name || originBatch?.task?.name || '보관 범위 밖의 이전 작업',
        origin_check_label:source?.origin_check_label || originBatch?.sources.find(previous=>previous.source_key===item.source_key)?.label || item.label};
    }), map: current ? data.map : {nodes:[],edges:[]}};
  }

  function renderBatch(data) {
    const batches = data.batches || [];
    if (!batches.some(batch => batch.batch_id === selectedBatch)) selectedBatch = '';
    activeBatch = batches.find(batch => batch.batch_id === (selectedBatch || data.history?.current_batch_id)) || null;
    const select = $('batchSelect');
    select.replaceChildren();
    if (!activeBatch) {const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='현재 계약 · 검증 요청 기록 없음';placeholder.selected=true;select.append(placeholder);}
    [...batches].reverse().forEach(batch => {
      const option = document.createElement('option');
      option.value = batch.batch_id;
      option.textContent = `${batch.task?.name || '이전 검증'} · ${new Date(batch.timestamp*1000).toLocaleString()} · 변경 ${batch.current_revision} · ${statusText[batch.status]}`;
      option.selected = batch.batch_id === activeBatch?.batch_id;
      select.append(option);
    });
    select.disabled = !batches.length;
    $('batchState').textContent = activeBatch ? `${statusText[activeBatch.status]} · ${outcomeText[activeBatch.reason_code] || ''}${['planned','running'].includes(activeBatch.status) ? ' 아직 종료가 확인되지 않았습니다. 연결이 끊겨도 정상 완료로 계산하지 않습니다.' : ''}` : '이전 데이터에 실제 실행 기록이 없으면 계획을 실적으로 계산하지 않습니다.';
    $('historyMeta').textContent = `보관 배치 ${count(data.history?.retained_batch_count)}개 중 ${batches.length}개 표시 · 최대 1,000건 / 7일 / 4 MiB · 종료 미확정 기록은 완료 통계에서 제외`;
    const totals=data.history?.totals;
    if (totals) $('historyMeta').textContent += ` · 종료 기록 ${totals.finalized_batch_count}개: 실제 실행 ${totals.executed_source_count} / 적용 재사용 ${totals.authoritative_reuse_count} / 미실행 ${totals.not_run_source_count}`;
    return batchView(data, activeBatch);
  }

  const scenarios = {'first-run':'첫 실행','unchanged':'변경 없음','docs':'문서 변경','partial-reuse':'일부 실행 + 일부 재사용','code':'코드 변경','environment':'환경 변경','first-failure':'첫 검사 실패'};
  const criteria = {'same-shards':'같은 샤드 전체 실행','parent-suite':'기존 전체 검증 명령'};
  function readComparison(value) {
    if (value?.version !== 2 || value.kind !== 'click-paired-verification-benchmark' || !Array.isArray(value.samples) || value.samples.length > 240) throw Error('지원하지 않는 비교 형식');
    const c = value.conditions;
    const integer = n => Number.isInteger(n) && n >= 0 && n <= 1000000;
    const duration = n => Number.isFinite(n) && n >= 0;
    if (!c || ![c.iterations,c.warmups,c.workload_rounds].every(integer) || !['evidence','guarded'].includes(c.runtime_mode) || c.scope_equivalence !== 'same-two-unittest-files' || c.authority !== 'real-hooks-and-one-use-runner' || c.observer !== 'off' || c.order !== 'alternating-pair-order') throw Error('비교 조건 정보가 없습니다');
    if (c.iterations < 1 || c.iterations > 10 || c.warmups > 10 || c.workload_rounds < 1) throw Error('비교 반복 조건이 잘못되었습니다');
    const seen = new Set();
    const samples = value.samples.map(item => {
      const key = `${item.scenario}:${item.comparison}:${item.iteration}`;
      if (!Object.hasOwn(scenarios,item.scenario) || !Object.hasOwn(criteria,item.comparison) || !integer(item.iteration) || typeof item.warmup !== 'boolean' || seen.has(key)) throw Error('비교 표본이 잘못되었습니다');
      if (item.iteration >= c.iterations+c.warmups || item.warmup !== (item.iteration < c.warmups) || !Array.isArray(item.order) || !['baseline,incremental','incremental,baseline'].includes(item.order.join(','))) throw Error('표본의 실행 순서나 워밍업 조건이 잘못되었습니다');
      seen.add(key);
      const arms = {};
      for (const name of ['baseline','incremental']) {
        const arm = item[name];
        if (!arm || !duration(arm.wall_ms) || !['passed','failed','interrupted','rejected','incomplete'].includes(arm.status) || ![arm.executed_source_count,arm.reused_source_count,arm.not_run_source_count].every(integer)) throw Error('실측 결과 정보가 없습니다');
        arms[name] = {wall_ms:arm.wall_ms, status:arm.status, executed_source_count:arm.executed_source_count, reused_source_count:arm.reused_source_count, not_run_source_count:arm.not_run_source_count};
      }
      const delta = arms.baseline.wall_ms - arms.incremental.wall_ms;
      return {scenario:item.scenario,comparison:item.comparison,iteration:item.iteration,warmup:item.warmup,
        order:item.order?.join(',') === 'baseline,incremental' ? ['baseline','incremental'] : ['incremental','baseline'],
        eligible:!item.warmup && arms.baseline.status === 'passed' && arms.incremental.status === 'passed',
        ...arms, delta_ms:delta, delta_percent:arms.baseline.wall_ms > 0 ? 100*delta/arms.baseline.wall_ms : null};
    });
    const engine = value.engine || {};
    const environment=value.environment || {};
    return {version:1, engine:{version:/^[0-9]+\.[0-9]+\.[0-9]+$/.test(engine.version) ? engine.version : null,commit:/^[0-9a-f]{40,64}$/.test(engine.commit) ? engine.commit : null,source_digest:/^[0-9a-f]{64}$/.test(engine.source_digest) ? engine.source_digest : null,working_tree_modified:engine.working_tree_modified === true},
      environment:{system:['Linux','Darwin','Windows'].includes(environment.system)?environment.system:null,machine:['x86_64','AMD64','aarch64','arm64','i386','i686','x86'].includes(environment.machine)?environment.machine:null,python:/^[0-9]+\.[0-9]+\.[0-9]+$/.test(environment.python)?environment.python:null},
      conditions:{iterations:c.iterations,warmups:c.warmups,workload_rounds:c.workload_rounds,runtime_mode:c.runtime_mode,
        scope:['alpha','beta'],order:'alternating-pair-order',observer:'off',cache:'각 비교 경로에 별도 저장소와 동일 baseline 절차 · OS 캐시 초기화 안 함 · bytecode 비활성'},samples};
  }

  function comparisonRows() {
    if (!comparison) return [];
    const median = values => {const v=[...values].sort((a,b)=>a-b); return v.length ? (v[Math.floor((v.length-1)/2)]+v[Math.floor(v.length/2)])/2 : null;};
    const keys = [...new Set(comparison.samples.map(item => `${item.scenario}:${item.comparison}`))];
    return keys.map(key => {
      const [scenario,criterion] = key.split(':');
      const all = comparison.samples.filter(item => item.scenario === scenario && item.comparison === criterion);
      const samples = all.filter(item => item.eligible);
      const deltas = samples.map(item => item.delta_ms);
      return {label:`${scenarios[scenario]} · ${criteria[criterion]}`, n:samples.length, excluded:all.length-samples.length,
        baseline:median(samples.map(item=>item.baseline.wall_ms)), incremental:median(samples.map(item=>item.incremental.wall_ms)),
        delta:median(deltas), percent:median(samples.map(item=>item.delta_percent).filter(Number.isFinite)),
        min:deltas.length?Math.min(...deltas):null,max:deltas.length?Math.max(...deltas):null};
    });
  }

  function renderComparison() {
    $('comparisonStatus').textContent = comparison ? '별도 비교 실측 있음 · fixture 조건 한정' : '비교 실측 없음';
    $('topComparison').textContent=$('comparisonStatus').textContent;
    const root = $('comparisonChart'); root.replaceChildren();
    if (!comparison) return;
    const c = comparison.conditions;
    $('comparisonInfo').textContent = `가져온 로컬 실측 · 서명 없음 · ${c.runtime_mode} · 반복 ${c.iterations} / 워밍업 ${c.warmups} · 각 경로의 2개 테스트 파일 범위 일치 · ${c.cache}. 아래는 성공 표본의 중앙값이며 음수 차이는 증분 실행이 더 느렸다는 뜻입니다.`;
    comparisonRows().forEach(row => {
      const article = document.createElement('article'); article.className = 'comparison-row';
      const title = document.createElement('h3'); title.textContent = `${row.label} · ${row.n}회 측정 / ${row.excluded}회 제외`; article.append(title);
      if (row.n) {
        const max = Math.max(row.baseline,row.incremental,1);
        [['전체 재실행 기준',row.baseline,'baseline'],['Click 증분 실행',row.incremental,'incremental']].forEach(([label,value,kind]) => {
          const bar = document.createElement('div'); bar.className = `comparison-bar ${kind}`;
          bar.style.width = `${Math.max(2,100*value/max)}%`;
          bar.textContent = `${label}: ${fmt(value)}`; article.append(bar);
        });
        const delta = document.createElement('p'); delta.className = row.delta < 0 ? 'slower' : '';
        delta.textContent = `쌍별 차이 중앙값 ${row.delta.toFixed(1)} ms (${row.percent === null ? '비율 계산 불가' : row.percent.toFixed(1)+'%'}) · 범위 ${row.min.toFixed(1)} ~ ${row.max.toFixed(1)} ms`;
        article.append(delta);
      } else {const text=document.createElement('p');text.textContent='정상 완료 성능 표본 없음 · 실패·중단·워밍업 표본은 JSON에 별도 보관';article.append(text);}
      root.append(article);
    });
  }

  function shareReport() {
    if (!snapshot) throw Error('표시할 실행 기록이 없습니다.');
    return {version:2,kind:'click-verification-efficiency-report',generated_at:snapshot.generated_at,
      projection_version:snapshot.version ?? null,engine:snapshot.engine ?? null,accounting:snapshot.accounting ?? null,controls:snapshot.controls ?? null,
      task:snapshot.task?{runtime_mode:snapshot.task.runtime_mode,contract_id:snapshot.task.contract_id,approval_bound:snapshot.task.approval_bound}:null,
      unit:'verification-group',summary:activeSummary,
      measurement_scope:activeBatch?.measurement_scope || 'unknown',
      batch:activeBatch ? {batch_id:activeBatch.batch_id,current_revision:activeBatch.current_revision,timestamp:activeBatch.timestamp,
        finished_at:activeBatch.finished_at,status:activeBatch.status,reason_code:activeBatch.reason_code,
        task:activeBatch.task?{mode:activeBatch.task.mode,id:activeBatch.task.id}:null,
        sources:activeBatch.sources.map(item=>({label:item.label,source_key:item.source_key,check_digest:item.check_digest,
          decision:item.decision,status:item.status,started:item.started,completed:item.completed,reason_code:item.reason_code,
          execution_reason_code:item.execution_reason_code,authority_source:item.authority_source,
          current_revision:item.current_revision,previous_revision:item.previous_revision,duration_ms:item.duration_ms,
          duration_baseline:item.duration_baseline,reuse_origin:item.reuse_origin,
          estimated_avoided_ms:item.status === 'reused' ? item.duration_baseline?.duration_ms ?? null : 0}))} : null,
      comparison,shadow:snapshot.summary.shadow,
      notes:['전체 사용자 대기시간은 측정하지 않음','Hook 진입부터 결과 기록 또는 준비와 runner 개별 구간만 부분 계측 · 호스트 요청 전·최종 저장·반환 제외',
        '생략 비용은 실제 적용된 재사용의 이전 성공 실행 표본에 기반한 추정','Shadow는 실제 재사용·실측 절약 아님',
        '입력 파일 경로와 원시 명령·환경·토큰은 공유본에 포함하지 않음','비교 fixture 결과를 일반 저장소 성능으로 일반화할 수 없음']};
  }

  function standaloneReport(report) {
    // Build with textContent, never interpolate user-provided HTML or scripts.
    const doc = document.implementation.createHTMLDocument('Click 검증 효율 리포트');
    doc.documentElement.lang = 'ko';
    const meta = doc.createElement('meta'); meta.setAttribute('charset','utf-8');doc.head.prepend(meta);
    const viewport=doc.createElement('meta');viewport.name='viewport';viewport.content='width=device-width, initial-scale=1';doc.head.append(viewport);
    const style=doc.createElement('style');style.textContent='body{font:16px/1.6 system-ui,sans-serif;max-width:1100px;margin:auto;padding:32px;color:#172333}table{border-collapse:collapse;width:100%;margin:16px 0}td,th{border-bottom:1px solid #ccd4df;text-align:left;padding:10px;overflow-wrap:anywhere}pre{white-space:pre-wrap;background:#f0f3f7;padding:16px}h2{margin-top:32px}';doc.head.append(style);
    const add=(tag,text,parent=doc.body)=>{const node=doc.createElement(tag);node.textContent=text;parent.append(node);return node;};
    const s=report.summary;
    add('h1','Click · 검증 효율 리포트');
    add('p',`${count(s.total_source_count)}개 검증 묶음 중 ${count(s.executed_source_count)}개 실제 실행 · ${count(s.authoritative_reuse_count)}개 재사용 · ${count(s.not_run_source_count)}개 미실행`);
    add('p',`Hook 진입 → 결과 기록 부분 실측: ${fmt(s.request_wall_ms)} / 부분 처리 구간: ${fmt(s.measured_processing_ms)} / 검사 실행 구간: ${fmt(s.executed_duration_ms)}`);
    add('p',`재실행 비용 회피 추정: ${fmt(s.estimated_avoided_ms)} · 표본이 있는 ${count(s.estimated_source_count)} / 재사용 ${count(s.authoritative_reuse_count)}개 묶음`);
    add('h2','검증 묶음별 실제 결과');const table=add('table','');
    const heading=add('tr','',table);['이름','계획','실제 결과','시간 / 과거 표본','이유'].forEach(text=>add('th',text,heading));
    report.batch?.sources.forEach(item=>{const tr=add('tr','',table);const origin=item.reuse_origin?` · 이전 배치 ${item.reuse_origin.batch_id.slice(0,12)}에서 재판정`:'';[item.label,executionLabels[item.decision]?.[0]||'없음',statusText[item.status],item.status==='reused'?fmt(item.duration_baseline?.duration_ms)+' (과거 표본)':fmt(item.duration_ms),(outcomeText[item.execution_reason_code]||reasonText[item.reason_code]||'정보 없음')+origin].forEach(text=>add('td',text,tr));});
    add('h2','별도 실측 비교');
    if (!report.comparison) add('p','비교 측정 없음. 일상 추정 비용을 실측한 전체 재실행 시간으로 환산하지 않습니다.');
    else {
      add('p',`반복 ${report.comparison.conditions.iterations} · 워밍업 ${report.comparison.conditions.warmups} · ${report.comparison.conditions.runtime_mode} · ${report.comparison.conditions.cache}`);
      const comparisonTable=add('table','');comparisonRows().forEach(row=>{const tr=add('tr','',comparisonTable);[row.label,`${row.n}회 / 제외 ${row.excluded}회`,fmt(row.baseline),fmt(row.incremental),row.delta===null?'성공 표본 없음':`${row.delta.toFixed(1)} ms · ${row.percent===null?'비율 없음':row.percent.toFixed(1)+'%'} · 범위 ${row.min.toFixed(1)} ~ ${row.max.toFixed(1)} ms`].forEach(text=>add('td',text,tr));});
    }
    add('h2','범위와 주의사항');report.notes.forEach(text=>add('p',text));
    add('h2','기계 판독용 데이터 (서명 없음)');add('pre',JSON.stringify(report,null,2));
    return '<!doctype html>\n'+doc.documentElement.outerHTML;
  }

  function download(content,type,name) {
    const url=URL.createObjectURL(new Blob([content],{type}));const link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  $('comparisonFile').onchange=async event=>{
    try {const file=event.target.files[0];if(!file)return;if(file.size>4*1024*1024)throw Error('비교 파일은 4 MiB 이하만 읽습니다.');comparison=readComparison(JSON.parse(await file.text()));renderComparison();}
    catch(error){comparison=null;$('comparisonChart').replaceChildren();$('comparisonInfo').textContent=`비교 파일을 읽지 못했습니다: ${error.message}`;}
  };
  $('exportJson').onclick=()=>{try{download(JSON.stringify(shareReport(),null,2),'application/json','click-efficiency.json');$('exportStatus').textContent=' JSON 내보내기 완료';}catch(error){$('exportStatus').textContent=error.message;}};
  $('exportHtml').onclick=()=>{try{download(standaloneReport(shareReport()),'text/html','click-efficiency.html');$('exportStatus').textContent=' HTML 내보내기 완료';}catch(error){$('exportStatus').textContent=error.message;}};
  $('batchSelect').onchange=event=>{selectedBatch=event.target.value;render(snapshot);};
  $('latestBatch').onclick=()=>{selectedBatch='';render(snapshot);};

  function render(data) {
    snapshot = data;
    $('connection').textContent = '연결됨';
    document.querySelector('.live').classList.add('ok');
    $('taskline').textContent = data.task.runtime_mode === 'unknown'
      ? '현재 실행 중인 검증이 없습니다. 아래에서 최근 검증 결과를 볼 수 있습니다.'
      : `${data.task.runtime_mode === 'guarded' ? 'Guarded' : 'Evidence'} 모드 · 변경 ${data.task.mutation_revision} · ${statusText[data.task.status] || '상태 확인 중'}`;
    const view = renderBatch(data);
    const incremental = activeBatch ? summarize(activeBatch) : data.summary.incremental;
    activeSummary = incremental;
    const shadow = data.summary.shadow;
    $('contractName').textContent = data.task.name || '현재 작업';
    $('approvalState').textContent = data.task.approval_bound ? '별도 승인됨 · Guarded' : data.task.runtime_mode === 'evidence' ? '호스트 권한 · Click 승인 없음' : data.task.status === 'staged' ? '승인 대기 · Guarded' : data.task.status === 'none' ? '활성 계약 없음 · 이력 전용' : '승인 정보 없음';
    $('contractId').textContent = data.task.contract_id || '승인 계약 ID 없음';
    const paragraphs = values => values.map(text => {const p=document.createElement('p');p.textContent=text;return p;});
    $('contractPromises').replaceChildren(...paragraphs(data.task.promises?.length ? data.task.promises.slice(0,2) : ['표시 가능한 약속 요약이 없습니다. 기존 승인 계약 또는 사용자 요청을 확인하세요.']));
    $('allPromises').replaceChildren(...paragraphs(data.task.promises || []));
    $('contractBoundary').replaceChildren(...paragraphs([
      `포함: ${(data.task.in_scope || []).join(' · ') || '원문 확인'}`,
      `제외: ${(data.task.out_of_scope || []).join(' · ') || '원문 확인'}`,
      `유지 조건: ${(data.task.must_hold || []).join(' · ') || '원문 확인'}`,
    ]));
    const controls=data.controls || [];
    $('controlSummary').textContent = `이 계약의 관측 통제: 차단 ${controls.filter(item=>item.effect==='blocked').length}건 · 비차단 안내 ${controls.filter(item=>item.effect==='advisory').length}건. 의미적 범위 준수나 숨은 추론을 판정한 수치가 아닙니다.`;
    if (data.task.status==='none') $('controlSummary').textContent='활성 계약이 없습니다. 보관된 viewer 이력은 승인이나 실행 권한을 전달하지 않습니다.';
    const reusedItems=(activeBatch?.sources || []).filter(item=>item.status==='reused');
    const prior=reusedItems.filter(item=>item.reuse_origin).length;
    $('reuseOrigins').textContent=`선택한 기록: 현재 작업 안의 재사용 ${reusedItems.length-prior} · 이전 작업에서 재판정 ${prior}`;
    const a=data.accounting;
    $('reuseRate').textContent=a ? `보관된 검증 그룹 요청 기준: ${a.reuse_numerator} / ${a.request_denominator} · ${a.reuse_rate===null?'비율 미측정':(100*a.reuse_rate).toFixed(1)+'%'} · ${a.from_timestamp?new Date(a.from_timestamp*1000).toLocaleString():'시작 기록 없음'} ~ ${a.through_timestamp?new Date(a.through_timestamp*1000).toLocaleString():'종료 기록 없음'}. 실제 재시도는 별도 요청이며 중복 수신·화면 갱신은 추가 집계하지 않습니다.` : '집계 정보 없음';
    $('zeroReuse').textContent=reusedItems.length ? `재사용 중 과거 시간 표본 미측정 ${reusedItems.filter(item=>!item.duration_baseline).length}개` : [...new Set(view.sources.map(source=>source.next_action || reasonFor(source)))].slice(0,3).join(' ');
    $('comparisonStatus').textContent=comparison ? '별도 비교 실측 있음 · fixture 조건 한정' : '비교 실측 없음';
    $('topComparison').textContent=$('comparisonStatus').textContent;
    $('batchHeadline').textContent = incremental.total_source_count === null ? '이번 계약의 검증 요청 기록이 아직 없습니다.' : `${count(incremental.total_source_count)}개 검증 묶음 중 ${count(incremental.executed_source_count)}개 실행 · ${count(incremental.authoritative_reuse_count)}개 재사용`;
    $('currentChecks').textContent = count(incremental.total_source_count);
    $('executedChecks').textContent = count(incremental.executed_source_count);
    $('reusedChecks').textContent = count(incremental.authoritative_reuse_count);
    $('executionDetail').textContent = `통과 ${count(incremental.passed_source_count)} · 실패 ${count(incremental.failed_source_count)} · 중단 ${count(incremental.interrupted_source_count)} · 미실행 ${count(incremental.not_run_source_count)} · 대기/미확정 ${count(incremental.pending_source_count)}`;
    $('requestWall').textContent = fmt(incremental.request_wall_ms);
    $('processingDuration').textContent = fmt(incremental.measured_processing_ms);
    $('timeScope').textContent = activeBatch?.measurement_scope === 'hook-entry-to-result-recording' ? '부분 실측: 같은 호스트의 단조 시계로 Hook 진입부터 결과 기록 직전까지 측정했습니다. Hook 이전 요청 대기·최종 저장·호스트 반환은 제외합니다.' : activeBatch?.measurement_scope === 'prepare-only' ? '부분 계측: 준비·재사용 판정만 포함. 호스트 대기·전달·최종 저장·반환은 제외합니다.' : '부분 계측: 준비 + runner의 개별 경과시간 합계. 호스트 대기·전달·최종 저장·반환은 제외합니다.';
    if (!activeBatch) $('timeScope').textContent='이 계약의 요청-결과 시간은 아직 측정되지 않았습니다.';
    $('estimateCoverage').textContent = `이전 시간 표본이 있는 ${count(incremental.estimated_source_count)} / 재사용 ${count(incremental.authoritative_reuse_count)}개 묶음 · 실측 절약 아님`;
    $('estimatedAvoided').textContent = fmt(incremental.estimated_avoided_ms);
    $('reuseBreakdown').textContent = `${count(incremental.exact_reuse_count)} / ${count(incremental.dependency_reuse_count)} / ${count(incremental.safe_change_reuse_count)}`;
    $('executedDuration').textContent = fmt(incremental.executed_duration_ms);
    $('shadowBreakdown').textContent = `${shadow.candidate_count} / ${shadow.confirmed_candidate_count} / ${shadow.contradiction_count}`;
    $('shadowTiming').textContent = `${fmt(shadow.potential_ms)} / ${fmt(shadow.observer_overhead_ms)}`;
    $('observerTitle').textContent = data.task.observer_enabled ? 'Observer: Shadow 켜짐' : 'Observer: 꺼짐';
    $('observerBody').textContent = data.task.observer_enabled
      ? '예측 정확도를 측정하지만 검사 생략 권한은 만들지 않습니다.'
      : 'Dashboard는 계속 볼 수 있으며 기존 exact·policy reuse는 정상 동작합니다.';
    $('updated').textContent = `${new Date(data.generated_at * 1000).toLocaleTimeString()} 갱신`;
    snapshot = view;
    renderSources(view);
    snapshot = data;
  }

  async function refresh() {
    if (!token) {
      $('connection').textContent = '접근 토큰 없음';
      return;
    }
    try {
      const response = await fetch('/api/v1/snapshot', {
        headers: {Authorization: `Bearer ${token}`},
        cache: 'no-store'
      });
      if (!response.ok) throw new Error(String(response.status));
      const data=await response.json();
      const signature=JSON.stringify({...data,generated_at:null});
      if (signature!==lastSnapshotSignature) {lastSnapshotSignature=signature;render(data);}
      else { $('connection').textContent='연결됨';document.querySelector('.live').classList.add('ok'); }
    } catch (_) {
      $('connection').textContent = '연결 끊김';
      document.querySelector('.live').classList.remove('ok');
    }
  }
  refresh();
  setInterval(refresh, 1500);
})();
"""


def fresh_state() -> dict[str, Any]:
    return {
        "version": DASHBOARD_STATE_VERSION,
        "status": "idle",
        "instance_id": "",
        "runner_token_digest": "",
        "runner_claimed_at": 0,
        "access_token_digest": "",
        "port": 0,
        "pid": 0,
        "started_at": 0,
        "stop_requested": False,
        "last_error": "",
    }


def state_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _DASHBOARD_FIELDS:
        return False
    status = value.get("status")
    instance_id = value.get("instance_id")
    runner_digest = value.get("runner_token_digest")
    access_digest = value.get("access_token_digest")
    integers = [
        value.get("runner_claimed_at"),
        value.get("port"),
        value.get("pid"),
        value.get("started_at"),
    ]
    if (
        value.get("version") != DASHBOARD_STATE_VERSION
        or status not in DASHBOARD_STATUSES
        or not isinstance(instance_id, str)
        or not isinstance(runner_digest, str)
        or not isinstance(access_digest, str)
        or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in integers)
        or not isinstance(value.get("stop_requested"), bool)
        or not isinstance(value.get("last_error"), str)
        or len(value["last_error"]) > 256
    ):
        return False
    if status == "idle":
        return value == fresh_state()
    return bool(
        _INSTANCE.fullmatch(instance_id)
        and _DIGEST.fullmatch(access_digest)
        and (not runner_digest or _DIGEST.fullmatch(runner_digest))
        and 0 <= value["port"] <= 65535
    )


def _dashboard_state(state: Any) -> dict[str, Any]:
    value = state.get(DASHBOARD_FIELD) if isinstance(state, dict) else None
    return dict(value) if state_is_valid(value) else fresh_state()


def _dashboard_path(state_path: Path) -> Path:
    return state_path.with_name(
        "dashboard-" + state_path.name.removeprefix("session-contract-")
    )


def _dashboard_state_for_path(state_path: Path) -> dict[str, Any]:
    sidecar = _read_state(_dashboard_path(state_path))
    if state_is_valid(sidecar):
        return dict(sidecar)
    # Compatibility for a viewer prepared by v0.80 before the sidecar split.
    return _dashboard_state(_read_state(state_path))


def _write_dashboard_state(state_path: Path, dashboard: dict[str, Any]) -> bool:
    if not state_is_valid(dashboard):
        return False
    click_state.write_json(_dashboard_path(state_path), dashboard)
    return True


def _managed_state_path(path: Path) -> bool:
    return click_state.managed_state_path(path, ("session-contract-",))


def _runner_prefix(action: str, runner_script: Path) -> list[str]:
    return [
        sys.executable,
        str(runner_script.resolve()),
        "--state-root",
        str(click_state.state_root().resolve()),
        action,
    ]


def runner_command(
    event: dict[str, Any],
    action: str,
    arguments: list[str],
    *,
    runner_script: Path,
    render_command: RenderCommand,
) -> str:
    return render_command(
        [
            *_runner_prefix(action, runner_script),
            str(click_state.contract_path(event).resolve()),
            *arguments,
        ]
    )


def prepare(
    event: dict[str, Any],
    action: str,
    *,
    runner_script: Path,
    render_command: RenderCommand,
) -> tuple[str, str]:
    if action not in DASHBOARD_ACTIONS:
        return "", "Click dashboard action must be start, stop, or status."
    state = click_contract_state.read_contract_state(event)
    runtime = click_runtime_state.view(state)
    if not runtime.execution_authorized:
        return "", "Start Guarded or Evidence runtime state before opening its dashboard."
    contract_path = click_state.contract_path(event).resolve()
    dashboard = _dashboard_state_for_path(contract_path)
    state_path = str(contract_path)
    if action == "status":
        return runner_command(
            event,
            "run-dashboard-status",
            [],
            runner_script=runner_script,
            render_command=render_command,
        ), ""
    if action == "stop":
        if dashboard["status"] not in {"starting", "running", "stopping"}:
            return runner_command(
                event,
                "run-dashboard-status",
                [],
                runner_script=runner_script,
                render_command=render_command,
            ), ""
        dashboard["status"] = "stopping"
        dashboard["stop_requested"] = True
        _write_dashboard_state(contract_path, dashboard)
        return runner_command(
            event,
            "run-dashboard-stop",
            [dashboard["instance_id"]],
            runner_script=runner_script,
            render_command=render_command,
        ), ""

    if dashboard["status"] in {"starting", "running", "stopping"}:
        return "", "The Click Shadow dashboard is already active. Stop it first."
    instance_id = secrets.token_urlsafe(24)
    runner_token = secrets.token_urlsafe(24)
    access_token = secrets.token_urlsafe(32)
    dashboard = {
        "version": DASHBOARD_STATE_VERSION,
        "status": "starting",
        "instance_id": instance_id,
        "runner_token_digest": hashlib.sha256(runner_token.encode()).hexdigest(),
        "runner_claimed_at": 0,
        "access_token_digest": hashlib.sha256(access_token.encode()).hexdigest(),
        "port": 0,
        "pid": 0,
        "started_at": int(time.time()) or 1,
        "stop_requested": False,
        "last_error": "",
    }
    _write_dashboard_state(contract_path, dashboard)
    return runner_command(
        event,
        "run-dashboard-start",
        [instance_id, runner_token, access_token],
        runner_script=runner_script,
        render_command=render_command,
    ), ""


def request_stop(event: dict[str, Any]) -> bool:
    state_path = click_state.contract_path(event).resolve()
    dashboard = _dashboard_state_for_path(state_path)
    if dashboard["status"] not in {"starting", "running", "stopping"}:
        return False
    dashboard["status"] = "stopping"
    dashboard["stop_requested"] = True
    return _write_dashboard_state(state_path, dashboard)


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _write_dashboard_fields(
    path: Path, instance_id: str, **fields: Any
) -> bool:
    dashboard = _dashboard_state_for_path(path)
    if dashboard.get("instance_id") != instance_id:
        return False
    dashboard.update(fields)
    if not state_is_valid(dashboard):
        return False
    return _write_dashboard_state(path, dashboard)


def _snapshot(path: Path, instance_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    dashboard = _dashboard_state_for_path(path)
    if dashboard.get("instance_id") != instance_id:
        return None, dashboard
    return click_contract_state.read_projection_state_path(path), dashboard


def run_start(
    arguments: list[str],
    *,
    runner_script: Path,
    spawn: Callable[..., subprocess.Popen[Any]] = click_process.spawn_argv,
) -> int:
    if len(arguments) != 4:
        sys.stderr.write("usage: run-dashboard-start <state> <id> <runner-token> <access-token>\n")
        return 2
    state_path = Path(arguments[0])
    instance_id, runner_token, access_token = arguments[1:]
    if not _managed_state_path(state_path):
        sys.stderr.write("Click dashboard runner received an unmanaged state path.\n")
        return 2
    with click_state.state_lock():
        state, dashboard = _snapshot(state_path, instance_id)
        runner_digest = hashlib.sha256(runner_token.encode()).hexdigest()
        access_digest = hashlib.sha256(access_token.encode()).hexdigest()
        if (
            state is None
            or not click_runtime_state.view(state).execution_authorized
            or dashboard.get("status") != "starting"
            or dashboard.get("stop_requested") is True
            or dashboard.get("runner_claimed_at") != 0
            or not hmac.compare_digest(str(dashboard.get("runner_token_digest", "")), runner_digest)
            or not hmac.compare_digest(str(dashboard.get("access_token_digest", "")), access_digest)
            or time.time() - int(dashboard.get("started_at", 0)) > START_TIMEOUT_SECONDS * 2
        ):
            sys.stderr.write("Click dashboard start authorization is stale or invalid.\n")
            return 2
        if not _write_dashboard_fields(
            state_path,
            instance_id,
            runner_claimed_at=int(time.time()) or 1,
            runner_token_digest="",
        ):
            return 2
    try:
        spawn(
            [
                *_runner_prefix("run-dashboard-server", runner_script),
                str(state_path),
                instance_id,
                access_token,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        with click_state.state_lock():
            _write_dashboard_fields(
                state_path,
                instance_id,
                status="failed",
                last_error=str(exc)[:256],
            )
        sys.stderr.write("Click dashboard process could not start.\n")
        return 2
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        with click_state.state_lock():
            _, current = _snapshot(state_path, instance_id)
        if current.get("status") == "running" and int(current.get("port", 0)) > 0:
            sys.stdout.write(
                f"http://127.0.0.1:{current['port']}/#token={access_token}\n"
            )
            return 0
        if current.get("status") == "failed":
            sys.stderr.write("Click dashboard exited during startup.\n")
            return 2
        time.sleep(POLL_SECONDS)
    with click_state.state_lock():
        _write_dashboard_fields(
            state_path,
            instance_id,
            status="stopping",
            stop_requested=True,
            last_error="startup-timeout",
        )
    sys.stderr.write("Click dashboard did not start within its bounded timeout.\n")
    return 2


def run_stop(arguments: list[str]) -> int:
    if len(arguments) != 2:
        sys.stderr.write("usage: run-dashboard-stop <state> <id>\n")
        return 2
    state_path = Path(arguments[0])
    instance_id = arguments[1]
    if not _managed_state_path(state_path):
        return 2
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        with click_state.state_lock():
            _, dashboard = _snapshot(state_path, instance_id)
        if dashboard.get("status") in {"stopped", "failed", "idle"}:
            sys.stdout.write("Click Shadow dashboard stopped\n")
            return 0
        time.sleep(POLL_SECONDS)
    sys.stderr.write("Click Shadow dashboard did not stop within its bounded timeout.\n")
    return 2


def run_status(arguments: list[str]) -> int:
    if len(arguments) != 1:
        sys.stderr.write("usage: run-dashboard-status <state>\n")
        return 2
    state_path = Path(arguments[0])
    if not _managed_state_path(state_path):
        return 2
    with click_state.state_lock():
        dashboard = _dashboard_state_for_path(state_path)
    sys.stdout.write(
        json.dumps(
            {
                "status": dashboard["status"],
                "port": dashboard["port"],
                "started_at": dashboard["started_at"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    return 0


class _DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, state_path: Path, instance_id: str, access_token: str):
        self.state_path = state_path
        self.instance_id = instance_id
        self.access_token = access_token
        super().__init__(("127.0.0.1", 0), _DashboardHandler)


class _DashboardHandler(BaseHTTPRequestHandler):
    server: _DashboardServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'none'",
        )
        self.end_headers()

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self._headers(status, content_type, len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    def _host_is_valid(self) -> bool:
        expected = f"127.0.0.1:{self.server.server_port}"
        return hmac.compare_digest(self.headers.get("Host", ""), expected)

    def _authorized(self) -> bool:
        value = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.access_token}"
        return hmac.compare_digest(value, expected)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._host_is_valid():
            self._send(421, "text/plain; charset=utf-8", b"Misdirected Request\n")
            return
        path = urlsplit(self.path).path
        if path == "/":
            self._send(200, "text/html; charset=utf-8", HTML.encode())
            return
        if path == "/styles.css":
            self._send(200, "text/css; charset=utf-8", CSS.encode())
            return
        if path == "/app.js":
            self._send(200, "text/javascript; charset=utf-8", JS.encode())
            return
        if path == "/api/v1/snapshot":
            if not self._authorized():
                self._send(401, "application/json", b'{"error":"unauthorized"}\n')
                return
            with click_state.state_lock():
                state, dashboard = _snapshot(
                    self.server.state_path, self.server.instance_id
                )
                if (
                    state is None
                    or dashboard.get("status") not in {"running", "stopping"}
                ):
                    self._send(410, "application/json", b'{"error":"stale"}\n')
                    return
                projection = click_dashboard_projection.dashboard_projection(state)
                if not click_dashboard_projection.projection_is_valid(projection):
                    self._send(500, "application/json", b'{"error":"invalid-projection"}\n')
                    return
            body = json.dumps(
                projection,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8") + b"\n"
            self._send(200, "application/json; charset=utf-8", body)
            return
        self._send(404, "text/plain; charset=utf-8", b"Not Found\n")

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.do_GET()

    def _method_not_allowed(self) -> None:
        self._send(405, "text/plain; charset=utf-8", b"Method Not Allowed\n")

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed


def run_server(arguments: list[str]) -> int:
    if len(arguments) != 3:
        return 2
    state_path = Path(arguments[0])
    instance_id, access_token = arguments[1:]
    if not _managed_state_path(state_path):
        return 2
    access_digest = hashlib.sha256(access_token.encode()).hexdigest()
    with click_state.state_lock():
        state, dashboard = _snapshot(state_path, instance_id)
        if (
            state is None
            or not click_runtime_state.view(state).execution_authorized
            or dashboard.get("status") != "starting"
            or dashboard.get("stop_requested") is True
            or not hmac.compare_digest(
                str(dashboard.get("access_token_digest", "")), access_digest
            )
        ):
            return 2
    try:
        server = _DashboardServer(state_path, instance_id, access_token)
    except OSError as exc:
        with click_state.state_lock():
            _write_dashboard_fields(
                state_path,
                instance_id,
                status="failed",
                last_error=str(exc)[:256],
            )
        return 2
    server.timeout = 0.4
    with click_state.state_lock():
        if not _write_dashboard_fields(
            state_path,
            instance_id,
            status="running",
            port=int(server.server_port),
            pid=os.getpid(),
        ):
            server.server_close()
            return 2
    deadline = time.monotonic() + MAX_LIFETIME_SECONDS
    result = 0
    try:
        while time.monotonic() < deadline:
            with click_state.state_lock():
                state, dashboard = _snapshot(state_path, instance_id)
            if (
                dashboard.get("stop_requested") is True
                or dashboard.get("status") != "running"
                or not hmac.compare_digest(
                    str(dashboard.get("access_token_digest", "")), access_digest
                )
            ):
                break
            server.handle_request()
        else:
            result = 124
    finally:
        server.server_close()
        with click_state.state_lock():
            _write_dashboard_fields(
                state_path,
                instance_id,
                status="stopped" if result == 0 else "failed",
                stop_requested=True,
                port=0,
                pid=0,
                last_error="" if result == 0 else "lifetime-expired",
            )
    return result
