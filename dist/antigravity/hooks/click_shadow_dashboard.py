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
    <section class="panel task-summary" aria-label="현재 작업과 승인 상태">
      <div><p class="eyebrow">현재 작업 · 약속한 범위 안에서</p><h2 id="contractName">현재 작업</h2><p id="taskline">현재 Click 상태를 불러오는 중…</p></div>
      <span id="approvalState" class="pill">승인 상태 확인 중</span>
      <details><summary>승인 약속과 범위</summary><div id="contractPromises"></div><div id="allPromises"></div><div id="contractBoundary"></div><p class="muted">이 Viewer와 보관 이력은 승인·실행·재사용 권한을 만들거나 승계하지 않습니다.</p><code id="contractId"></code><p id="controlSummary" class="muted"></p></details>
    </section>

    <section class="panel savings-hero" aria-labelledby="title">
      <div class="hero-copy">
        <p class="eyebrow">이번 재검증</p>
        <h1 id="title">재사용으로 생략한 테스트 실행시간</h1>
        <strong id="estimatedAvoided" class="hero-value">—</strong>
        <p id="estimateCoverage" class="hero-status">과거 성공 실행의 시간 근거를 확인하는 중입니다.</p>
        <p id="batchHeadline" class="batch-headline">실제 검증 기록을 기다리고 있습니다.</p>
        <div class="basis" aria-label="추정 기준"><span>과거 성공 실행 기록 기반 추정</span><span>동일 샤드 순차 기준</span><span>Click 관리비용 제외</span></div>
        <p class="truth-note">테스트 실행 구간의 추정입니다. 전체 요청 대기시간이나 관리비용을 차감한 순절감이 아닙니다.</p>
      </div>
      <aside id="waitIncreaseNotice" class="wait-notice" hidden><a href="#measurementDetails">총 검증 대기는 증가했습니다. 상세 보기</a><span>가져온 동등 비교 실측의 중앙값 기준</span></aside>
    </section>

    <section class="panel execution-overview" aria-labelledby="executionTitle">
      <div class="panel-title"><div><p class="eyebrow">전체 대비 부분 실행</p><h2 id="executionTitle">같은 샤드를 모두 순차 실행했을 때와 비교</h2></div><span id="comparisonState" class="pill">판정 중</span></div>
      <div class="metrics" aria-label="실제 증분 검증 결과">
        <article><span>전체 검증 샤드</span><strong id="currentChecks">—</strong><small>개별 테스트 케이스 수가 아닙니다</small></article>
        <article><span>이번에 실제 실행</span><strong id="executedChecks">—</strong><small id="executionDetail">실제 시작한 샤드</small></article>
        <article><span>실제 재사용 적용</span><strong id="reusedChecks">—</strong><small>기존 권한 판정을 통과한 샤드</small></article>
        <article><span>시간 근거 커버리지</span><strong id="timingCoverage">—</strong><small>시간 표본이 적합한 재사용 샤드</small></article>
      </div>
      <div id="executionComparison" class="execution-comparison" hidden>
        <div class="comparison-values"><p><span>동일 샤드 전체 실행 예상</span><strong id="fullEstimate">—</strong></p><p><span>이번 테스트 실행</span><strong id="executedDuration">—</strong></p><p><span>테스트 실행시간 감소</span><strong id="reductionRate">—</strong></p></div>
        <div class="axis" aria-label="동일 시간 축 비교">
          <div><span>전체 순차 실행 예상</span><div class="track"><i id="fullBar" class="bar full"></i></div></div>
          <div><span>이번 테스트 실행</span><div class="track"><i id="executedBar" class="bar executed"></i></div></div>
        </div>
      </div>
      <p id="comparisonGuidance" class="state-guidance">완료된 검증의 시간 근거를 기다리고 있습니다.</p>
    </section>

    <section class="panel timeline"><div class="panel-title"><div><p class="eyebrow">재사용 근거 · 배치 선택</p><h2>최근 검증 배치</h2></div><button id="latestBatch" type="button">최신 배치</button></div><label for="batchSelect">상세 결과 선택</label> <select id="batchSelect"></select><p id="batchState" class="muted"></p><p id="historyMeta" class="muted"></p></section>
    <section class="workspace">
      <article class="panel checks"><div class="panel-title"><div><p class="eyebrow">샤드별 근거</p><h2>실행·재사용 결과와 이유</h2></div><span id="sourceCount" class="count">0</span></div><p id="reuseOrigins" class="muted"></p><div id="sources" class="source-list"></div></article>
    </section>
    <section class="panel explanation" aria-live="polite"><p class="eyebrow">선택한 샤드</p><h2 id="whyTitle">샤드를 선택하세요</h2><p id="whyBody">실제 판정 결과와 시간 근거를 설명합니다.</p><p id="originName"></p><details class="lineage"><summary id="lineageSummary">실행·재사용 계보</summary><ol id="lineageSteps"></ol></details><details><summary>원시 ID·revision·측정 결합 상세</summary><div id="limits"></div></details></section>

    <details id="measurementDetails" class="panel measurement-details">
      <summary><span><b>측정 상세</b><small>요청·처리 구간, 관리비용, 비교 실측, 원시 조건</small></span></summary>
      <div class="metric-split">
        <article class="compact"><p class="eyebrow">현재 요청에서 관측한 구간</p><div class="statline"><span>Hook 진입 → 결과 기록 · 부분 요청시간</span><strong id="requestWall">—</strong></div><div class="statline"><span>현재 측정 가능한 처리 구간</span><strong id="processingDuration">—</strong></div><div class="statline"><span>이번 source-command 실행 구간</span><strong id="detailExecutedDuration">—</strong></div><div class="statline"><span>동일 샤드 전체 순차 실행 추정</span><strong id="detailFullEstimate">—</strong></div><p class="muted" id="timeScope">호스트 요청 전·최종 반환은 계측 범위 밖입니다.</p></article>
        <article class="compact"><p class="eyebrow">관리비용과 순 요청시간</p><div class="statline"><span>Click 전체 관리비용</span><strong id="managementOverhead">측정 정보 없음</strong></div><p class="muted">포함 관계가 있는 요청시간과 검사시간을 빼서 관리비용을 만들지 않습니다.</p><div class="statline"><span>비교 환경의 순 요청 시간 차이</span><strong id="pairedNet">측정 정보 없음</strong></div><p id="comparisonStatus" class="muted">동등한 paired 비교 실측이 없습니다.</p><div class="statline"><span>Observer 후보 / 확인 / 모순</span><strong id="shadowBreakdown">—</strong></div><div class="statline"><span>잠재 시간 / Observer 보조 처리시간</span><strong id="shadowTiming">—</strong></div><p class="muted" id="tracingSlowdown">추적으로 인한 검사 지연은 별도 측정하지 않았습니다.</p></article>
        <article class="compact setup-costs"><p class="eyebrow">최초 설정과 기준 실행</p><div class="statline"><span>설정 상태</span><strong id="setupStatus">미설정</strong></div><div class="statline"><span>초기 설정 비용</span><strong id="setupInitial">측정 정보 없음</strong></div><div class="statline"><span>Observer 비용</span><strong id="setupObservation">측정 정보 없음</strong></div><div class="statline"><span>Click 처리</span><strong id="setupProcessing">측정 정보 없음</strong></div><div class="statline"><span>parent 전체 실행 / 순차 shards</span><strong id="setupRuns">측정 정보 없음</strong></div><div class="statline"><span>parent − 순차 shards</span><strong id="setupNet">측정 정보 없음</strong></div><p id="setupScope" class="muted">첫 기준 실행은 절감 시간으로 집계하지 않습니다.</p></article>
      </div>
      <details class="telemetry"><summary>Evidence Map · Shadow 관찰 상세 · 재사용 권한 없음</summary><p><strong id="observerTitle">Observer 확인 중</strong> · <span id="observerBody">Dashboard와 Observer는 독립적입니다.</span></p><article class="map-panel"><div class="panel-title"><h2>선택한 샤드의 관찰된 입력</h2><span id="mapMeta" class="muted"></span></div><div id="emptyMap" class="empty">샤드를 선택하면 현재 입력과 이전 baseline의 관계를 보여줍니다.</div><svg id="map" role="img" aria-label="선택한 샤드의 Evidence Map"></svg></article></details>
      <section class="comparison"><p class="eyebrow">명시적으로 실행한 paired 비교 · 일상 추정치와 별개</p><h2>전체 재실행 기준 vs Click 부분 검증</h2><p id="comparisonInfo">아직 비교 측정이 없습니다. 아래 명령으로 별도 측정한 JSON만 가져올 수 있습니다. 가져온 결과는 승인·재사용 권한이 아닙니다.</p><pre>python3 benchmarks/incremental_verification.py --guarded-workflow --iterations 3 --warmups 1 --output /tmp/click-comparison.json --html-output /tmp/click-comparison.html</pre><label>비교 JSON 선택 <input id="comparisonFile" type="file" accept="application/json,.json"></label><div id="comparisonChart"></div></section>
      <details><summary>원시 측정 조건과 상태 사유</summary><div id="rawConditions"></div><p id="reuseRate" class="muted"></p><p id="zeroReuse" class="muted"></p><p class="muted">비교 실측이 없는 live 요청은 순절감을 계산하지 않습니다. 가져온 보고서와 Viewer는 실행 권한을 만들지 않습니다.</p></details>
    </details>
    <section class="panel exports"><h2>공유 리포트</h2><p class="muted">현재 선택한 배치와 가져온 비교 측정을 내보냅니다. 파일 경로·원시 명령·환경 값·토큰은 제외합니다. 검증은 실행하지 않습니다.</p><button type="button" id="exportJson">JSON 내보내기</button> <button type="button" id="exportHtml">독립형 HTML 내보내기</button><span id="exportStatus" role="status"></span></section>
  </main>
  <footer><span>로컬 전용 · 읽기 전용 · 파일 내용 미노출</span><span id="updated">아직 갱신되지 않음</span></footer>
  <script src="/app.js" defer></script>
</body>
</html>
"""

CSS = """:root{color-scheme:dark;--bg:#0b0d12;--panel:#121722;--line:#253044;--text:#f5f7fb;--muted:#93a0b4;--green:#73e6a2;--amber:#ffca6a;--red:#ff7b82;--blue:#79b8ff;--violet:#ad8cff;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
*{box-sizing:border-box}[hidden]{display:none!important}html{scroll-behavior:smooth}body{margin:0;background:radial-gradient(circle at 12% -8%,#1d2940 0,transparent 32%),var(--bg);color:var(--text);min-height:100vh}.skip{position:absolute;left:-999px}.skip:focus{left:16px;top:12px;z-index:9;background:#fff;color:#000;padding:8px}header,footer{height:72px;padding:0 max(24px,calc((100vw - 1180px)/2));display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line)}footer{height:auto;min-height:56px;border:0;border-top:1px solid var(--line);color:var(--muted);font-size:12px}.brand{display:flex;gap:12px;align-items:center}.brand div{display:grid}.brand span{color:var(--muted);font-size:12px}.mark{display:grid;place-items:center;width:38px;height:38px;border-radius:11px;background:linear-gradient(135deg,var(--violet),var(--blue));color:#080a0e!important;font-weight:900;font-size:20px!important}.live{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:13px}.live i{width:8px;height:8px;border-radius:50%;background:var(--amber);box-shadow:0 0 14px var(--amber)}.live.ok i{background:var(--green);box-shadow:0 0 14px var(--green)}main{max-width:1180px;margin:auto;padding:24px 24px 56px}.panel,.metrics article{background:linear-gradient(180deg,rgba(24,31,45,.97),rgba(15,20,29,.97));border:1px solid var(--line);border-radius:18px}.panel{padding:22px}.eyebrow{margin:0 0 8px;color:var(--blue);font-size:10px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}h1,h2,p{overflow-wrap:anywhere}h1{margin:0;font-size:clamp(24px,3vw,38px);line-height:1.08;letter-spacing:-.04em}h2{font-size:18px;margin:0}.muted{color:var(--muted);font-size:11px;line-height:1.6}.pill{display:inline-block;font-size:10px;padding:5px 8px;border-radius:999px;background:#202a3a;color:var(--muted);white-space:nowrap}.pill.reused{color:var(--green);background:#14291e}.pill.rerun{color:var(--red);background:#31191d}.pill.pending{color:var(--amber);background:#292319}.panel-title{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:18px}.task-summary{display:grid;grid-template-columns:1fr auto;align-items:start;padding:16px 20px;margin-bottom:12px}.task-summary #taskline{color:var(--muted);margin:6px 0 0;font-size:12px}.task-summary details{grid-column:1/-1}.task-summary code{overflow-wrap:anywhere;color:var(--muted);font-size:11px}#contractPromises{display:flex;gap:24px}#contractPromises p{flex:1;margin:4px 0;font-size:13px}#allPromises p,#contractBoundary p{font-size:12px;line-height:1.6}.savings-hero{display:grid;grid-template-columns:minmax(0,1fr) 270px;gap:28px;align-items:center;border-color:#355274;background:radial-gradient(circle at 85% 0,#1f3150 0,transparent 38%),linear-gradient(145deg,#162235,#0f141d);padding:30px;margin-bottom:12px}.hero-value{display:block;margin:14px 0 8px;color:var(--green);font-size:clamp(48px,8vw,92px);line-height:.95;letter-spacing:-.07em}.hero-status{color:#c9d3e2;margin:0;font-size:14px}.batch-headline{font-size:20px;margin:22px 0 12px}.basis{display:flex;flex-wrap:wrap;gap:8px}.basis span{padding:7px 9px;border:1px solid #314564;border-radius:999px;color:#bdcbe0;font-size:11px}.truth-note{max-width:760px;margin:12px 0 0;color:var(--muted);font-size:11px;line-height:1.6}.wait-notice{border:1px solid #6b482f;background:#281b17;border-radius:14px;padding:16px;display:grid;gap:6px}.wait-notice a{color:var(--red);font-weight:800;text-decoration-thickness:1px}.wait-notice span{color:#d3b1a2;font-size:11px}.execution-overview{margin-bottom:12px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.metrics article{padding:16px;display:grid;gap:7px;background:#0f151f}.metrics span,.metrics small{color:var(--muted);font-size:11px;line-height:1.5}.metrics strong{font-size:27px;letter-spacing:-.04em}.execution-comparison{margin-top:18px;padding-top:18px;border-top:1px solid var(--line)}.comparison-values{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.comparison-values p{display:grid;gap:6px;margin:0}.comparison-values span{color:var(--muted);font-size:11px}.comparison-values strong{font-size:22px}.axis{margin-top:18px;display:grid;gap:12px}.axis>div{display:grid;grid-template-columns:150px 1fr;gap:14px;align-items:center}.axis span{color:var(--muted);font-size:11px}.track{height:18px;border:1px solid #2e3b50;border-radius:999px;background:#0c1119;overflow:hidden}.bar{display:block;height:100%;border-radius:999px}.bar.full{background:linear-gradient(90deg,#355d88,#78b7f7)}.bar.executed{background:linear-gradient(90deg,#69469a,#ad8cff)}.state-guidance{margin:16px 0 0;padding:11px 13px;border-radius:10px;background:#101621;color:#c2ccdb;font-size:12px;line-height:1.6}.timeline,.exports{margin:12px 0}.timeline label{font-size:12px}.workspace{display:grid;grid-template-columns:1fr;gap:12px}.count{display:grid;place-items:center;min-width:28px;height:28px;border-radius:9px;background:#202a3a;color:var(--blue);font-weight:800}.source-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;max-height:430px;overflow:auto}.source{appearance:none;width:100%;text-align:left;color:inherit;background:#0f141e;border:1px solid #263146;border-radius:13px;padding:14px;cursor:pointer}.source:hover,.source:focus-visible,.source.active{border-color:var(--blue);outline:none}.source-head{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:7px}.source strong{font-size:14px}.source p{margin:0;color:var(--muted);font-size:11px;line-height:1.5}.source .reason{margin-top:7px;color:#c3ccda}.explanation{margin-top:12px;min-height:138px}.explanation p:not(.eyebrow){color:var(--muted);line-height:1.6;margin-bottom:0}.lineage ol,.lineage{color:var(--muted)}#lineageSteps{margin:0;padding-left:23px}#lineageSteps li{padding:5px 0;color:#c5cfdd;font-size:12px}.tag{display:inline-block;margin:8px 6px 0 0;padding:5px 8px;border-radius:8px;background:#292319;color:var(--amber);font-size:11px}.measurement-details{margin:12px 0}.measurement-details>summary{font-size:18px}.measurement-details>summary span{display:inline-grid;gap:4px}.measurement-details>summary small{color:var(--muted);font-size:11px;font-weight:400}.measurement-details[open]>summary{margin-bottom:18px}.metric-split{display:grid;grid-template-columns:1fr 1fr;gap:12px}.compact{padding:4px 12px 12px}.statline{display:flex;justify-content:space-between;gap:18px;padding-top:10px;margin-top:10px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}.statline strong{color:var(--text);text-align:right}.telemetry{margin-top:12px;border-top:1px solid var(--line)}.map-panel{min-height:0;overflow:hidden;position:relative}.empty{height:300px;display:grid;place-items:center;text-align:center;color:var(--muted);padding:40px}svg{width:100%;height:440px;display:none}.edge{stroke:#34435d;stroke-width:1.2}.node text{fill:var(--text);font-size:11px}.node rect{fill:#101722;stroke:#31405a;rx:9}.node.source rect{stroke:var(--blue);fill:#132033}.node.changed rect{stroke:var(--red);fill:#2b171b}.node.baseline-only rect{stroke:var(--amber);fill:#292319}.node.newly-observed rect{stroke:var(--violet);fill:#211a32}.comparison{margin-top:18px;padding-top:18px;border-top:1px solid var(--line)}.comparison pre{overflow:auto;background:#0c111a;padding:14px;border-radius:10px;font-size:12px}.comparison-row{border-top:1px solid var(--line);padding:18px 0}.comparison-row h3{font-size:14px}.comparison-bar{border-radius:5px;margin:8px 0;padding:8px;font-size:12px;background:#204d76;white-space:nowrap}.comparison-bar.incremental{background:#513575}.comparison-row p{font-size:12px;color:var(--muted)}.comparison-row p.slower{color:var(--red)}details summary{cursor:pointer;padding:10px 0;color:var(--blue)}details[open] summary{margin-bottom:10px}button,select,input{font:inherit}select{max-width:100%;background:#141c29;color:var(--text);padding:10px;border:1px solid var(--line);border-radius:9px}button:not(.source){background:#243650;color:var(--text);border:1px solid #456087;padding:10px 14px;border-radius:9px;cursor:pointer}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--blue);outline-offset:3px}.exports h2{margin-bottom:10px}.exports span{font-size:12px;margin-left:12px}#timeScope,#batchState,#executionDetail,#reuseRate,#zeroReuse,#controlSummary{line-height:1.6}
.setup-costs{grid-column:1/-1}
@media(max-width:900px){.savings-hero{grid-template-columns:1fr}.wait-notice{width:100%}.metrics{grid-template-columns:repeat(2,1fr)}.metric-split{grid-template-columns:1fr}.comparison-values{grid-template-columns:1fr 1fr}.source-list{grid-template-columns:1fr}}@media(max-width:560px){main{padding:20px 14px 42px}header,footer{padding-left:14px;padding-right:14px}.panel{padding:16px}.task-summary{grid-template-columns:1fr;gap:10px}.task-summary details{grid-column:1}.savings-hero{padding:22px}.hero-value{font-size:clamp(42px,16vw,68px)}.metrics{grid-template-columns:1fr 1fr}.metrics strong{font-size:22px}.comparison-values{grid-template-columns:1fr}.axis>div{grid-template-columns:1fr;gap:5px}#contractPromises{display:block}}@media(prefers-reduced-motion:no-preference){.live i{animation:pulse 2s infinite}@keyframes pulse{50%{opacity:.45}}}
"""

JS = r"""(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const OUTPUT_LABELS = Object.freeze({
    omitted:'재사용으로 생략한 테스트 실행시간',
    full:'동일 샤드 전체 순차 실행 예상',
    executed:'이번 테스트 실행',
    reduction:'테스트 실행시간 감소',
    basis:['과거 성공 실행 기록 기반 추정','동일 샤드 순차 기준','Click 관리비용 제외']
  });
  const decimal = value => Number(value.toFixed(2)).toLocaleString('ko-KR');
  const fmt = ms => {
    if (!Number.isFinite(ms)) return '측정 정보 없음';
    if (ms < 1000) return `${decimal(ms)} ms`;
    if (ms < 60000) return `${decimal(ms / 1000)}초`;
    const rounded = Math.round(ms / 1000);
    const minutes = Math.floor(rounded / 60);
    const seconds = rounded % 60;
    return seconds ? `${minutes}분 ${seconds}초` : `${minutes}분`;
  };
  const estimatedDuration = ms => Number.isFinite(ms) ? `약 ${fmt(ms)}` : '측정 정보 없음';
  const signedDuration = ms => Number.isFinite(ms) ? `${ms >= 0 ? '+' : '−'}${fmt(Math.abs(ms))}` : '측정 정보 없음';
  const percent = ratio => Number.isFinite(ratio) ? `약 ${decimal(100 * ratio)}%` : '측정 정보 없음';
  const count = value => Number.isInteger(value) ? String(value) : '알 수 없음';
  const token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
  history.replaceState(null, '', location.pathname);
  let selected = '';
  let snapshot = null;
  let selectedBatch = '';
  let activeBatch = null;
  let activeSummary = null;
  let activeSavings = null;
  let comparison = null;
  let lastSnapshotSignature = '';
  const statusText = {
    planned: '실행 예정', 'reuse-pending': '재사용 예정 · 미적용', running: '실행 중',
    passed: '통과', failed: '실패', interrupted: '중단 · 일부 결과 미확정',
    'not-run': '미실행', reused: '재사용 적용', unknown: '측정 정보 없음',
    rejected: '실행 전 거부', incomplete: '미확정', evidence: 'Evidence', staged: '승인 대기', approved: '승인됨', none: '활성 작업 없음'
  };
  const setupStatusText = {
    unconfigured:'미설정', 'selection-required':'명령 선택 필요',
    'approval-required':'승인 대기', 'review-required':'검토 필요',
    'commit-required':'커밋 필요', 'baseline-required':'기준 실행 필요',
    'sharding-ready':'샤딩 준비됨 · 재사용 불가',
    'reuse-ready':'샤딩·재사용 준비됨', unsupported:'미지원', blocked:'차단됨'
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
  const savingsReasonText = {
    'request-not-finalized':'검증 요청의 종료가 아직 확인되지 않았습니다.',
    'request-not-passed':'검증 요청이 정상 완료되지 않았습니다.',
    'scope-incomplete':'요청한 모든 샤드의 최종 상태가 확정되지 않았습니다.',
    'executed-duration-missing':'실제로 시작한 샤드 중 실행시간이 없는 항목이 있습니다.',
    'reused-duration-sample-missing':'재사용된 샤드 중 과거 성공 실행시간이 없는 항목이 있습니다.',
    'legacy-timing-context-missing':'구형 시간 기록에는 현재 추정에 필요한 측정 조건이 없습니다.',
    'reused-duration-sample-incompatible':'과거 시간 기록의 샤드·검사·측정 조건이 현재 추정과 맞지 않습니다.',
    'sequential-comparison-invalid':'같은 샤드의 순차 실행으로 비교할 시간 조건이 완전하지 않습니다.',
    'zero-denominator':'전체 실행 예상시간이 0이라 감소율을 계산하지 않습니다.'
  };
  const inputStatus = {
    'current-observed': '현재 관찰됨',
    'changed': '변경됨',
    'baseline-only': '이전 baseline에만 존재',
    'newly-observed': '현재 새로 관찰됨'
  };

  function outcomePresentation(batch, summary = {}, savings = {}) {
    const total = summary.total_source_count;
    const executed = summary.executed_source_count;
    const reused = summary.authoritative_reuse_count;
    const timedReused = savings.coverage?.timed_reused_source_count;
    const actualReused = savings.coverage?.actual_reused_source_count;
    const complete = Boolean(batch && batch.status === 'passed' && savings.scope_complete === true);
    const reasons = Array.isArray(savings.reason_codes) ? savings.reason_codes : [];
    const reason = reasons.map(code => savingsReasonText[code]).filter(Boolean).join(' ');
    let state = 'first-run';
    let heroValue = '측정 정보 없음';
    let heroStatus = '첫 성공 실행 전에는 재사용 시간 추정이 없습니다.';
    let summaryText = '첫 검증 실행을 완료하면 실행과 재사용 결과를 비교할 수 있습니다.';
    let guidance = summaryText;

    if (batch) {
      if (batch.status === 'planned') {
        state = 'first-run';
        heroValue = '요청 미완료';
        heroStatus = '검증이 준비됐지만 아직 어떤 샤드도 실행·재사용되지 않았습니다.';
        summaryText = '첫 실행 준비 중 · 계획은 실제 실행이나 재사용 실적으로 계산하지 않습니다.';
      } else if (batch.status === 'running') {
        state = 'running';
        heroValue = '요청 미완료';
        heroStatus = '검증이 진행 중입니다. 종료 전 부분 값은 대표 절감치로 표시하지 않습니다.';
        summaryText = `${count(total)}개 샤드의 검증 진행 중 · 완료된 구간은 측정 상세에서만 확인할 수 있습니다.`;
      } else if (batch.status === 'interrupted') {
        state = 'cancelled';
        heroValue = '요청 미완료';
        heroStatus = '검증이 취소되거나 중단되어 전체 대비 감소율을 만들지 않습니다.';
        summaryText = `${count(total)}개 샤드 요청이 중단됨 · 완료·미확정·미실행 상태를 분리해 보존합니다.`;
      } else if (['failed','rejected','incomplete'].includes(batch.status)) {
        state = 'failed';
        heroValue = '요청 미완료';
        heroStatus = batch.status === 'failed' ? '검증 실패로 전체 대비 감소율을 만들지 않습니다.' : '실행 전 거부 또는 미확정 요청은 절감 실적으로 계산하지 않습니다.';
        summaryText = `${count(total)}개 샤드 요청이 정상 완료되지 않음 · 부분 관측값은 측정 상세에서만 확인할 수 있습니다.`;
      } else if (complete && reused === 0) {
        state = 'no-reuse';
        heroValue = '0 ms';
        heroStatus = '실제 재사용 없음 · 생략한 샤드가 없습니다.';
        summaryText = `${count(total)}개 샤드 모두 실제 실행 · 재사용으로 생략한 테스트 실행 없음`;
      } else if (complete && savings.omitted_test_execution_status === 'estimated') {
        state = executed === 0 ? 'all-reuse' : 'partial-execution';
        heroValue = estimatedDuration(savings.omitted_test_execution_ms);
        heroStatus = `${count(timedReused)}/${count(actualReused)}개 재사용 샤드의 적합한 과거 성공 표본 · 추정`;
        summaryText = executed === 0
          ? `${count(total)}개 중 실제 실행 0개 · ${count(reused)}개 모두 재사용으로 ${heroValue}의 테스트 재실행 생략〔추정〕`
          : `${count(total)}개 중 ${count(executed)}개만 실행 · ${count(reused)}개 재사용으로 ${heroValue}의 테스트 재실행 생략〔추정〕`;
      } else if (complete && savings.omitted_test_execution_status === 'partial') {
        state = 'partial-timing';
        heroValue = Number.isFinite(savings.omitted_test_execution_ms) ? `≥ ${fmt(savings.omitted_test_execution_ms)}` : '측정 정보 없음';
        heroStatus = `확인 가능한 재사용 샤드의 합계 · 시간 표본 ${count(timedReused)}/${count(actualReused)}개`;
        summaryText = `${count(total)}개 중 ${count(executed)}개 실행 · ${count(reused)}개 재사용 · 확인된 ${count(timedReused)}/${count(actualReused)}개 표본 합계 ${heroValue}`;
      } else if (complete) {
        state = 'unmeasured';
        heroValue = '측정 정보 없음';
        heroStatus = reason || '재사용은 적용됐지만 적합한 과거 실행시간 근거가 없습니다.';
        summaryText = `${count(total)}개 중 ${count(executed)}개 실행 · ${count(reused)}개 재사용 · 생략 시간은 미측정`;
      } else {
        state = 'incomplete';
        heroValue = '요청 미완료';
        heroStatus = reason || '요청 범위의 최종 상태가 확정되지 않았습니다.';
        summaryText = '완료되지 않은 검증 요청은 절감 실적으로 표시하지 않습니다.';
      }
      guidance = complete
        ? (state === 'partial-timing' || state === 'unmeasured'
            ? `${heroStatus} 전체 실행 예상과 감소율은 표시하지 않습니다.`
            : state === 'no-reuse'
              ? '모든 샤드를 실제 실행했습니다. 같은 시간 축에서 전체 실행 예상과 이번 실행이 같습니다.'
              : state === 'all-reuse'
                ? '모든 샤드가 실제 재사용됐습니다. 이번 source-command 실행 구간은 0입니다.'
                : '전체 예상과 이번 실행은 같은 샤드의 순차 source-command 시간 축으로 비교합니다.')
        : heroStatus;
    }

    const full = savings.full_sequential_test_execution_estimate_ms;
    const current = savings.executed_test_execution_ms;
    const ratio = savings.test_execution_reduction_ratio;
    const comparisonReady = Boolean(
      complete
      && savings.full_sequential_test_execution_estimate_status === 'estimated'
      && savings.executed_test_execution_status === 'measured'
      && Number.isFinite(full)
      && Number.isFinite(current)
      && full > 0
      && Number.isFinite(ratio)
    );
    const currentPercent = comparisonReady ? Math.max(0, Math.min(100, 100 * current / full)) : null;
    return {
      state, heroValue, heroStatus, summaryText, guidance,
      comparisonReady,
      comparisonState: comparisonReady ? '동일 시간 축' : (complete ? '비교 미측정' : '요청 미완료'),
      fullText: Number.isFinite(full) ? estimatedDuration(full) : '측정 정보 없음',
      executedText: Number.isFinite(current)
        ? (savings.executed_test_execution_status === 'partial' ? `확인된 합계 ≥ ${fmt(current)}` : fmt(current))
        : '측정 정보 없음',
      reductionText: comparisonReady ? percent(ratio) : '측정 정보 없음',
      currentPercent,
      coverageText: Number.isInteger(timedReused) && Number.isInteger(actualReused) ? `${timedReused}/${actualReused}` : '알 수 없음',
    };
  }

  function renderOutcome(batch, summary, savings) {
    const view = outcomePresentation(batch, summary, savings);
    $('estimatedAvoided').textContent = view.heroValue;
    $('estimateCoverage').textContent = view.heroStatus;
    $('batchHeadline').textContent = view.summaryText;
    $('currentChecks').textContent = count(summary.total_source_count);
    $('executedChecks').textContent = count(summary.executed_source_count);
    $('reusedChecks').textContent = count(summary.authoritative_reuse_count);
    $('timingCoverage').textContent = view.coverageText;
    $('executionDetail').textContent = `통과 ${count(summary.passed_source_count)} · 실패 ${count(summary.failed_source_count)} · 중단 ${count(summary.interrupted_source_count)} · 미실행 ${count(summary.not_run_source_count)} · 대기/미확정 ${count(summary.pending_source_count)}`;
    $('comparisonState').textContent = view.comparisonState;
    $('comparisonState').className = `pill ${view.comparisonReady ? 'reused' : 'pending'}`;
    $('executionComparison').hidden = !view.comparisonReady;
    $('fullEstimate').textContent = view.fullText;
    $('executedDuration').textContent = view.executedText;
    $('reductionRate').textContent = view.reductionText;
    $('comparisonGuidance').textContent = view.guidance;
    $('fullBar').style.width = view.comparisonReady ? '100%' : '0';
    $('executedBar').style.width = view.comparisonReady ? `${view.currentPercent}%` : '0';
    $('fullBar').setAttribute('aria-label', view.comparisonReady ? `${OUTPUT_LABELS.full} ${view.fullText}` : '전체 실행 시간 미측정');
    $('executedBar').setAttribute('aria-label', view.comparisonReady ? `${OUTPUT_LABELS.executed} ${view.executedText}` : '이번 실행 시간 미측정');
    return view;
  }

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
    const reused = source.execution_status === 'reused';
    const successor = reused && Boolean(source.reuse_origin);
    $('originName').textContent = successor
      ? '이전 계약의 통과 결과를 현재 계약에서 다시 판정해 적용했습니다.'
      : reused
        ? '같은 계약 안의 유효한 통과 결과를 재사용했습니다.'
        : ['passed','failed','interrupted'].includes(source.execution_status)
          ? '이번 요청에서 실제 실행한 결과입니다.'
          : '아직 실행·재사용 결과가 없습니다.';
    $('lineageSummary').textContent = successor
      ? '이전 작업 → 원본 성공 실행 → 현재 적용'
      : reused
        ? '같은 계약의 성공 실행 → 현재 적용'
        : '이번 요청의 실제 실행 흐름';
    const observedAt = source.duration_baseline?.observed_at;
    const observedLabel = Number.isInteger(observedAt)
      ? new Date(observedAt * 1000).toLocaleString()
      : '관측 시점 미측정';
    const steps = successor ? [
      `이전 작업: ${source.origin_name || '보관 범위 밖의 이전 작업'}`,
      `원본 성공 실행: ${source.origin_check_label || source.label} · ${fmt(source.duration_baseline?.duration_ms)} · ${observedLabel}`,
      `현재 적용: 변경 ${source.current_revision}에서 재사용 판정 통과`,
    ] : reused ? [
      '현재 작업 안의 이전 성공 실행',
      `원본 성공 실행: ${source.origin_check_label || source.label} · ${fmt(source.duration_baseline?.duration_ms)} · ${observedLabel}`,
      `현재 적용: 변경 ${source.current_revision}에서 같은 계약 재사용`,
    ] : [
      `현재 요청: 변경 ${source.current_revision}`,
      `실제 결과: ${label} · source-command 구간 ${fmt(source.duration_ms)}`,
      `판정: ${reasonFor(source)}`,
    ];
    $('lineageSteps').replaceChildren(...steps.map(text => {
      const item = document.createElement('li');
      item.textContent = text;
      return item;
    }));
    const originId = source.reuse_origin?.contract_id || source.reuse_origin?.evidence_session_id || source.duration_baseline?.origin_task?.id;
    const tags = [
      `현재 revision ${source.current_revision}`,
      source.previous_revision >= 0 ? `이전 성공 revision ${source.previous_revision}` : '이전 성공 없음',
      `계획 ${executionLabels[source.execution_decision]?.[0] || '없음'} · 실제 ${label}`,
      `현재 실행 구간 ${fmt(source.duration_ms)}`,
      source.duration_baseline ? `원본 성공 표본 ${source.duration_baseline.sample_count}개 · revision ${source.duration_baseline.revision} · ${fmt(source.duration_baseline.duration_ms)} · ${source.duration_baseline.measurement_scope || '측정 구간 정보 없음'}` : '과거 시간 표본 없음',
      originId ? `원본 작업 ID ${originId}` : '원본 작업 ID 없음',
      source.duration_baseline?.batch_id ? `원본 성공 배치 ID ${source.duration_baseline.batch_id}` : '원본 성공 배치 ID 없음',
      source.duration_baseline?.source_key ? `원본 source ID ${source.duration_baseline.source_key}` : '원본 source ID 없음',
      source.reuse_origin ? `현재 재사용 출처 배치 ID ${source.reuse_origin.batch_id} · 출처 revision ${source.reuse_origin.origin_revision}` : '현재 계약 안의 근거',
      `정확한 검사 결합 ${source.check_digest || source.duration_baseline?.check_digest || '정보 없음'}`,
      source.duration_baseline?.timing_binding_digest ? `시간 조건 결합 ${source.duration_baseline.timing_binding_digest}` : '시간 조건 결합 없음',
      `판정 식별자 ${source.reason_code || '없음'}`,
      ...(source.shadow_limitations || []).map(item => `Shadow: ${item}`)
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
        ? `${source.reuse_origin ? '이전 계약에서 재판정' : '같은 계약 재사용'} · ${Number.isFinite(source.duration_baseline?.duration_ms) ? `과거 성공 실행 ${fmt(source.duration_baseline.duration_ms)}` : '과거 시간 근거 없음'}`
        : ['passed','failed','interrupted','running'].includes(source.execution_status)
          ? `실제 ${label} · 이번 source-command 구간 ${fmt(source.duration_ms)}`
          : `계획 ${executionLabels[source.execution_decision]?.[0] || '없음'} · 실제 시작 기록 없음`;
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

  const scenarios = {'first-run':'첫 실행','unchanged':'변경 없음','docs':'문서 변경','partial-reuse':'일부 실행 + 일부 재사용','code':'코드 변경','environment':'환경 변경','first-failure':'첫 검사 실패',
    'unrelated-code':'부분 영향 코드','related-code':'다른 단일 샤드 영향','all-code':'모든 샤드 영향','failure':'예상 실패','retry':'수정 후 재시도'};
  const criteria = {'same-shards':'같은 샤드 전체 실행','parent-suite':'기존 전체 검증 명령'};
  function readLegacyComparison(value) {
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

  function readWorkflowComparison(value) {
    const integer = n => Number.isInteger(n) && n >= 0 && n <= 1000000;
    const duration = n => Number.isFinite(n) && n >= 0;
    const sameKeys=(object,keys)=>object && typeof object==='object' && !Array.isArray(object) && Object.keys(object).sort().join(',')===[...keys].sort().join(',');
    const topKeys=['version','kind','source','unit','engine','environment','conditions','samples','comparison_samples','stage_summaries','cumulative_summaries','workflow_cost_summaries','summaries','repository_reference','dashboard_snapshot','limitations'];
    if (!sameKeys(value,topKeys) || value.version!==4 || value.kind!=='click-guarded-workflow-benchmark' || value.source!=='isolated-guarded-fixture' || value.unit!=='ms') throw Error('지원하지 않는 비교 형식');
    const c=value.conditions;
    const configurations=['baseline','click-default','explicit-reuse'];
    const stages=['first-run','unrelated-code','related-code','all-code','environment','failure','retry','unchanged'];
    const comparisons=['same-shards','parent-suite'];
    const conditionKeys=['iterations','warmups','workload_rounds','configurations','steps','comparisons','runtime_mode','scope_equivalence','measurement_order','authority','observer','cache','default_configuration','explicit_configuration','test_interval','click_request_interval','additional_cost','failure'];
    if (!sameKeys(c,conditionKeys) || ![c.iterations,c.warmups,c.workload_rounds].every(integer) || c.iterations<1 || c.iterations>10 || c.warmups>10 || c.workload_rounds<1 ||
        JSON.stringify(c.configurations)!==JSON.stringify(configurations) || JSON.stringify(c.steps)!==JSON.stringify(stages) || JSON.stringify(c.comparisons)!==JSON.stringify(comparisons) ||
        c.runtime_mode!=='guarded-explicitly-selected; baseline-without-click' || c.scope_equivalence!=='same-two-unittest-files-and-code-at-every-stage' ||
        c.measurement_order!=='rotating-within-stage-and-workflow' || c.authority!=='real-hooks-and-one-use-runner; distinct-scripted-fixture-approval-turns' || c.observer!=='off' ||
        c.cache!=='fresh-initial-state-per-configuration-and-repetition; OS-cache-not-flushed; bytecode-disabled' ||
        c.default_configuration!=='no-optional-shards-dependencies-or-safe-change-policy; product-default-mode-remains-Evidence' ||
        c.explicit_configuration!=='fixed-committed-two-shard-map-and-sibling-code-safe-change-policy-before-A' ||
        c.test_interval!=='source-command-dispatch-through-return; sequential-sum-for-executed-sources' || c.click_request_interval!=='driver-preflight-through-runner-return' ||
        c.additional_cost!=='setup-transition-and-two-same-state-full-executions-reported-separately' ||
        c.failure!=='expected-failure-kept-raw-and-in-workflow-cost; excluded-from-success-only-savings-statistics') throw Error('비교 조건 정보가 없습니다');
    if (!Array.isArray(value.samples) || value.samples.length!==c.iterations+c.warmups || !Array.isArray(value.stage_summaries) || value.stage_summaries.length!==32 || !Array.isArray(value.cumulative_summaries) || value.cumulative_summaries.length!==4 || !Array.isArray(value.workflow_cost_summaries) || value.workflow_cost_summaries.length!==2 || !Array.isArray(value.summaries) || JSON.stringify(value.summaries)!==JSON.stringify(value.cumulative_summaries) || !Array.isArray(value.limitations) || value.limitations.some(item=>typeof item!=='string') || (value.dashboard_snapshot!==null && (typeof value.dashboard_snapshot!=='object' || Array.isArray(value.dashboard_snapshot)))) throw Error('비교 보고서 구조가 잘못되었습니다');
    if (!Array.isArray(value.comparison_samples) || value.comparison_samples.length>640 || value.comparison_samples.length!==(c.iterations+c.warmups)*2*stages.length*comparisons.length) throw Error('비교 표본 수가 잘못되었습니다');
    const scopes={
      'same-shards':['sequential-shard-command-dispatch-through-return','executed-source-command-duration-sum'],
      'parent-suite':['parent-command-dispatch-through-return','driver-preflight-through-runner-return'],
    };
    const seen=new Set();
    const samples=value.comparison_samples.map(item=>{
      const itemKeys=['configuration','scenario','comparison','iteration','warmup','order','eligible','excluded_reason','scope_equivalent','unit','baseline','click','delta_ms','delta_percent'];
      if (!sameKeys(item,itemKeys) || !configurations.slice(1).includes(item.configuration) || !stages.includes(item.scenario) || !comparisons.includes(item.comparison) || !integer(item.iteration) || item.iteration>=c.iterations+c.warmups || typeof item.warmup!=='boolean' || item.warmup!==(item.iteration<c.warmups) || item.unit!=='ms') throw Error('비교 표본이 잘못되었습니다');
      const key=`${item.configuration}:${item.scenario}:${item.comparison}:${item.iteration}`;
      if (seen.has(key)) throw Error('중복 비교 표본입니다');seen.add(key);
      if (!Array.isArray(item.order) || item.order.length!==3 || [...item.order].sort().join(',')!=='click,parent-suite,same-shards') throw Error('표본 실행 순서가 잘못되었습니다');
      if (!sameKeys(item.baseline,['duration_ms','status','measurement_scope']) || !sameKeys(item.click,['duration_ms','status','measurement_scope']) || !duration(item.baseline.duration_ms) || !duration(item.click.duration_ms) || !['passed','failed'].includes(item.baseline.status) || !['passed','failed'].includes(item.click.status) || item.baseline.measurement_scope!==scopes[item.comparison][0] || item.click.measurement_scope!==scopes[item.comparison][1]) throw Error('실측 결과 정보가 없습니다');
      const scopeEquivalent=!(item.configuration==='click-default'&&item.comparison==='same-shards');
      const eligible=!item.warmup && scopeEquivalent && item.baseline.status==='passed' && item.click.status==='passed';
      const excluded=item.warmup?'warmup':!scopeEquivalent?'scope-not-equivalent':eligible?'':'verification-not-passed';
      const delta=item.baseline.duration_ms-item.click.duration_ms;
      const percent=item.baseline.duration_ms>0?100*delta/item.baseline.duration_ms:null;
      if (item.scope_equivalent!==scopeEquivalent || item.eligible!==eligible || item.excluded_reason!==excluded || !Number.isFinite(item.delta_ms) || Math.abs(item.delta_ms-delta)>1e-6 || (percent===null?item.delta_percent!==null:!Number.isFinite(item.delta_percent)||Math.abs(item.delta_percent-percent)>1e-6)) throw Error('비교 계산이 원시 시간과 다릅니다');
      return {configuration:item.configuration,scenario:item.scenario,comparison:item.comparison,iteration:item.iteration,warmup:item.warmup,order:[...item.order],eligible,
        baseline:{wall_ms:item.baseline.duration_ms,status:item.baseline.status,executed_source_count:0,reused_source_count:0,not_run_source_count:0},
        incremental:{wall_ms:item.click.duration_ms,status:item.click.status,executed_source_count:0,reused_source_count:0,not_run_source_count:0},
        delta_ms:delta,delta_percent:percent};
    });
    let repositoryReference=null;
    const reference=value.repository_reference;
    if (reference!==null) {
      const referenceKeys=['version','kind','source','unit','scope_digest','conditions','samples','summary','limitations'];
      if (!sameKeys(reference,referenceKeys) || reference.version!==1 || reference.kind!=='click-repository-bundle-reference' || reference.source!=='current-repository-test-bundle' || reference.unit!=='ms' || !/^[0-9a-f]{64}$/.test(reference.scope_digest) || !Array.isArray(reference.samples) || reference.samples.length>5) throw Error('저장소 번들 참조가 잘못되었습니다');
      const rc=reference.conditions;
      const rcKeys=['iterations','warmups','shard_count','scope_basis','measurement_order','cache','measurement_scope'];
      if (!sameKeys(rc,rcKeys) || !integer(rc.iterations) || rc.iterations<1 || rc.iterations>3 || !integer(rc.warmups) || rc.warmups>2 || !integer(rc.shard_count) || rc.shard_count<1 || rc.scope_basis!=='committed-evidence-shards-v1-inventory' || rc.measurement_order!=='alternating-pair-order' || rc.measurement_scope!=='driver-command-dispatch-through-return' || rc.cache!=='same-working-tree-and-environment; OS-cache-not-flushed; bytecode-disabled' || reference.samples.length!==rc.iterations+rc.warmups || !Array.isArray(reference.limitations) || reference.limitations.some(item=>typeof item!=='string')) throw Error('저장소 번들 조건이 잘못되었습니다');
      let eligibleCount=0;const referenceSeen=new Set();
      reference.samples.forEach(item=>{
        const keys=['iteration','warmup','order','eligible','excluded_reason','same_shards','parent_suite','delta_ms','delta_percent'];
        if (!sameKeys(item,keys) || !integer(item.iteration) || item.iteration>=reference.samples.length || referenceSeen.has(item.iteration) || typeof item.warmup!=='boolean' || item.warmup!==(item.iteration<rc.warmups) || !Array.isArray(item.order) || !['same-shards,parent-suite','parent-suite,same-shards'].includes(item.order.join(','))) throw Error('저장소 번들 표본이 잘못되었습니다');
        referenceSeen.add(item.iteration);
        for (const arm of [item.same_shards,item.parent_suite]) if (!sameKeys(arm,['duration_ms','status','exit_code','executed_command_count','not_run_command_count']) || !duration(arm.duration_ms) || !['passed','failed'].includes(arm.status) || !Number.isInteger(arm.exit_code) || !integer(arm.executed_command_count) || !integer(arm.not_run_command_count) || (arm.status==='passed')!==(arm.exit_code===0)) throw Error('저장소 번들 실행 결과가 잘못되었습니다');
        const passed=item.same_shards.status==='passed'&&item.parent_suite.status==='passed';const eligible=!item.warmup&&passed;const excluded=item.warmup?'warmup':passed?'':'verification-not-passed';
        const delta=item.parent_suite.duration_ms-item.same_shards.duration_ms;const percent=item.parent_suite.duration_ms>0?100*delta/item.parent_suite.duration_ms:null;
        if (item.eligible!==eligible || item.excluded_reason!==excluded || !Number.isFinite(item.delta_ms) || Math.abs(item.delta_ms-delta)>1e-6 || (percent===null?item.delta_percent!==null:!Number.isFinite(item.delta_percent)||Math.abs(item.delta_percent-percent)>1e-6)) throw Error('저장소 번들 계산이 원시 시간과 다릅니다');
        if (eligible) eligibleCount++;
      });
      const rs=reference.summary;const readDist=item=>{if(!sameKeys(item,['median','min','max'])||Object.values(item).some(number=>number!==null&&!Number.isFinite(number)))throw Error('저장소 번들 요약이 잘못되었습니다');return {median:item.median,min:item.min,max:item.max};};
      if (!sameKeys(rs,['eligible_samples','same_shards_duration_ms','parent_suite_duration_ms','parent_minus_shards_ms']) || rs.eligible_samples!==eligibleCount) throw Error('저장소 번들 요약이 잘못되었습니다');
      repositoryReference={source:reference.source,unit:'ms',scope_digest:reference.scope_digest,conditions:{iterations:rc.iterations,warmups:rc.warmups,shard_count:rc.shard_count},summary:{eligible_samples:eligibleCount,same_shards_duration_ms:readDist(rs.same_shards_duration_ms),parent_suite_duration_ms:readDist(rs.parent_suite_duration_ms),parent_minus_shards_ms:readDist(rs.parent_minus_shards_ms)}};
    }
    const engine=value.engine||{};const environment=value.environment||{};
    return {version:2,source:value.source,engine:{version:/^[0-9]+\.[0-9]+\.[0-9]+(?:[+-][0-9A-Za-z.-]+)?$/.test(engine.version)?engine.version:null,commit:/^[0-9a-f]{40,64}$/.test(engine.commit)?engine.commit:null,source_digest:/^[0-9a-f]{64}$/.test(engine.source_digest)?engine.source_digest:null,working_tree_modified:engine.working_tree_modified===true},
      environment:{system:['Linux','Darwin','Windows'].includes(environment.system)?environment.system:null,machine:['x86_64','AMD64','aarch64','arm64','i386','i686','x86'].includes(environment.machine)?environment.machine:null,python:/^[0-9]+\.[0-9]+\.[0-9]+$/.test(environment.python)?environment.python:null},
      conditions:{iterations:c.iterations,warmups:c.warmups,workload_rounds:c.workload_rounds,runtime_mode:'guarded',scope:['alpha','beta'],order:c.measurement_order,observer:'off',cache:'매 반복·구성마다 초기 상태 복원 · 단계 내 세 측정 교차 · OS 캐시 초기화 안 함 · bytecode 비활성'},samples,repository_reference:repositoryReference};
  }

  function readComparison(value) {
    if (value?.version===2 && value.kind==='click-paired-verification-benchmark') return readLegacyComparison(value);
    if (value?.version===4 && value.kind==='click-guarded-workflow-benchmark') return readWorkflowComparison(value);
    throw Error('지원하지 않는 비교 형식');
  }

  function comparisonRows(value = comparison) {
    if (!value) return [];
    const median = values => {const v=[...values].sort((a,b)=>a-b); return v.length ? (v[Math.floor((v.length-1)/2)]+v[Math.floor(v.length/2)])/2 : null;};
    const keys = [...new Set(value.samples.map(item => `${item.configuration||'legacy'}:${item.scenario}:${item.comparison}`))];
    return keys.map(key => {
      const [configuration,scenario,criterion] = key.split(':');
      const all = value.samples.filter(item => (item.configuration||'legacy') === configuration && item.scenario === scenario && item.comparison === criterion);
      const samples = all.filter(item => item.eligible);
      const deltas = samples.map(item => item.delta_ms);
      const prefix=configuration==='legacy'?'':`${configuration==='click-default'?'Click 기본':'명시적 재사용'} · `;
      return {label:`${prefix}${scenarios[scenario]} · ${criteria[criterion]}`, n:samples.length, excluded:all.length-samples.length,
        baseline:median(samples.map(item=>item.baseline.wall_ms)), incremental:median(samples.map(item=>item.incremental.wall_ms)),
        delta:median(deltas), percent:median(samples.map(item=>item.delta_percent).filter(Number.isFinite)),
        min:deltas.length?Math.min(...deltas):null,max:deltas.length?Math.max(...deltas):null};
    });
  }

  function renderComparison() {
    const root = $('comparisonChart'); root.replaceChildren();
    $('waitIncreaseNotice').hidden = true;
    if (!comparison) {
      $('comparisonStatus').textContent = '동등한 paired 비교 실측이 없습니다.';
      $('pairedNet').textContent = '측정 정보 없음';
      $('comparisonInfo').textContent = '아직 비교 측정이 없습니다. 아래 명령으로 별도 측정한 JSON만 가져올 수 있습니다. 가져온 결과는 승인·재사용 권한이 아닙니다.';
      return;
    }
    const c = comparison.conditions;
    const rows = comparisonRows();
    const eligible = rows.filter(row => row.n > 0);
    const increased = eligible.filter(row => row.delta < 0);
    const sourceText=comparison.version===2?'독립 Guarded fixture 단계별':'legacy fixture';
    $('comparisonStatus').textContent = eligible.length ? `별도 paired 비교 실측 ${eligible.length}개 표본군 · ${sourceText}` : '정상 완료한 paired 비교 표본 없음';
    $('pairedNet').textContent = eligible.length === 1
      ? `${eligible[0].delta < 0 ? '증가' : '감소'} ${fmt(Math.abs(eligible[0].delta))} · 쌍별 중앙값`
      : eligible.length
        ? `성공 표본군 ${eligible.length}개 · 아래 쌍별 중앙값 참조`
        : '측정 정보 없음';
    $('waitIncreaseNotice').hidden = increased.length === 0;
    const referenceText=comparison.repository_reference?' · 실제 저장소 전체 테스트 번들 그룹화 참조 첨부':' · 실제 저장소 번들 참조 없음';
    $('comparisonInfo').textContent = `가져온 로컬 실측 · 서명 없음 · 승인·재사용 권한 없음 · ${c.runtime_mode} · 반복 ${c.iterations} / 워밍업 ${c.warmups} · 각 경로의 2개 테스트 파일 범위 일치 · ${c.cache}${referenceText}. same-shards는 테스트 command 구간, parent-suite는 Click 전체 요청 구간의 비교이며 서로 합치지 않습니다. 아래는 성공 표본의 중앙값이고 음수는 이 표본에서 대기시간이 증가했다는 뜻입니다.`;
    rows.forEach(row => {
      const article = document.createElement('article'); article.className = 'comparison-row';
      const title = document.createElement('h3'); title.textContent = `${row.label} · ${row.n}회 측정 / ${row.excluded}회 제외`; article.append(title);
      if (row.n) {
        const max = Math.max(row.baseline,row.incremental,1);
        [['전체 재실행 기준',row.baseline,'baseline'],['Click 증분 실행',row.incremental,'incremental']].forEach(([label,value,kind]) => {
          const bar = document.createElement('div'); bar.className = `comparison-bar ${kind}`;
          bar.style.width = `${100*value/max}%`;
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
    const display = outcomePresentation(activeBatch, activeSummary, activeSavings);
    return {version:3,kind:'click-verification-efficiency-report',generated_at:snapshot.generated_at,
      projection_version:snapshot.version ?? null,engine:snapshot.engine ?? null,accounting:snapshot.accounting ?? null,controls:snapshot.controls ?? null,
      task:snapshot.task?{runtime_mode:snapshot.task.runtime_mode,contract_id:snapshot.task.contract_id,approval_bound:snapshot.task.approval_bound}:null,
      unit:'verification-group',summary:activeSummary,revalidation_savings:activeSavings,
      labels:OUTPUT_LABELS,display:{state:display.state,summary_text:display.summaryText,
        omitted_text:display.heroValue,omitted_status_text:display.heroStatus,
        full_text:display.fullText,executed_text:display.executedText,
        reduction_text:display.reductionText,comparison_status:display.comparisonState},
      measurement_scope:activeBatch?.measurement_scope || 'unknown',
      measurement:{request_wall_ms:activeSummary?.request_wall_ms ?? null,
        measured_processing_ms:activeSummary?.measured_processing_ms ?? null,
        executed_test_execution_ms:activeSavings?.executed_test_execution_ms ?? null,
        full_sequential_test_execution_estimate_ms:activeSavings?.full_sequential_test_execution_estimate_ms ?? null,
        click_management_overhead_ms:null,click_management_overhead_status:'unmeasured',
        live_net_time_saving_ms:null,live_net_time_saving_reason:'counterfactual-not-measured',
        observer_auxiliary_processing_ms:snapshot.summary.shadow.observer_overhead_ms},
      batch:activeBatch ? {batch_id:activeBatch.batch_id,current_revision:activeBatch.current_revision,timestamp:activeBatch.timestamp,
        finished_at:activeBatch.finished_at,status:activeBatch.status,reason_code:activeBatch.reason_code,
        task:activeBatch.task?{mode:activeBatch.task.mode,id:activeBatch.task.id}:null,
        sources:activeBatch.sources.map(item=>({label:item.label,source_key:item.source_key,check_digest:item.check_digest,
          decision:item.decision,status:item.status,started:item.started,completed:item.completed,reason_code:item.reason_code,
          execution_reason_code:item.execution_reason_code,authority_source:item.authority_source,
          current_revision:item.current_revision,previous_revision:item.previous_revision,duration_ms:item.duration_ms,
          duration_baseline:item.duration_baseline,reuse_origin:item.reuse_origin,
          estimated_avoided_ms:item.estimated_avoided_ms}))} : null,
      comparison,shadow:snapshot.summary.shadow,
      notes:['전체 사용자 대기시간은 측정하지 않음','Hook 진입부터 결과 기록 또는 준비와 runner 개별 구간만 부분 계측 · 호스트 요청 전·최종 저장·반환 제외',
        '생략한 테스트 실행시간은 실제 적용된 재사용의 적합한 이전 성공 실행 표본에 기반한 추정','전체 순차 실행 추정은 같은 샤드의 source command 구간 합이며 원래 parent 명령의 실측 wall time이 아님',
        '생략한 테스트 실행시간은 관리비용을 뺀 순절감이나 사용자 대기시간 절감이 아님','Shadow는 실제 재사용·실측 절약 아님',
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
    const savings=report.revalidation_savings;
    const display=report.display || outcomePresentation(report.batch,s,savings);
    add('h1','Click · 검증 효율 리포트');
    add('h2',report.labels?.omitted || OUTPUT_LABELS.omitted);
    add('p',`${display.omitted_text} · ${display.omitted_status_text}`);
    add('p',display.summary_text);
    add('p',(report.labels?.basis || OUTPUT_LABELS.basis).join(' / '));
    const comparisonTable=add('table','');
    [
      [report.labels?.full || OUTPUT_LABELS.full,display.full_text],
      [report.labels?.executed || OUTPUT_LABELS.executed,display.executed_text],
      [report.labels?.reduction || OUTPUT_LABELS.reduction,display.reduction_text],
    ].forEach(([label,value])=>{const tr=add('tr','',comparisonTable);add('th',label,tr);add('td',value,tr);});
    add('h2','검증 묶음별 실제 결과');const table=add('table','');
    const heading=add('tr','',table);['이름','계획','실제 결과','시간 / 과거 표본','이유'].forEach(text=>add('th',text,heading));
    report.batch?.sources.forEach(item=>{const tr=add('tr','',table);const origin=item.status==='reused'?(item.reuse_origin?' · 이전 계약에서 재판정':' · 같은 계약 재사용'):'';[item.label,executionLabels[item.decision]?.[0]||'없음',statusText[item.status],item.status==='reused'?fmt(item.duration_baseline?.duration_ms)+' (과거 성공 실행)':fmt(item.duration_ms),(outcomeText[item.execution_reason_code]||reasonText[item.reason_code]||'정보 없음')+origin].forEach(text=>add('td',text,tr));});
    add('h2','접힌 화면과 같은 측정 상세');
    add('p',`Hook 진입 → 결과 기록 부분 요청시간: ${fmt(report.measurement?.request_wall_ms)} / 현재 측정 가능한 처리 구간: ${fmt(report.measurement?.measured_processing_ms)}`);
    add('p','Click 전체 관리비용: 측정 정보 없음. 포함 관계가 있는 시간을 빼서 관리비용을 만들지 않습니다.');
    add('h2','별도 paired 비교 실측');
    if (!report.comparison) add('p','비교 측정 없음. 일상 추정 비용을 실측한 전체 재실행 시간으로 환산하지 않습니다.');
    else {
      add('p',`승인·재사용 권한 없음 · 반복 ${report.comparison.conditions.iterations} · 워밍업 ${report.comparison.conditions.warmups} · ${report.comparison.conditions.runtime_mode} · ${report.comparison.conditions.cache}`);
      add('p','same-shards 행은 모든 샤드 command 구간과 실제 실행된 source-command 구간을 비교합니다. parent-suite 행은 기존 parent 명령 전체 구간과 Click 요청 전체 구간을 비교합니다. 두 구간은 합산하지 않습니다.');
      const pairedTable=add('table','');comparisonRows(report.comparison).forEach(row=>{const tr=add('tr','',pairedTable);[row.label,`${row.n}회 / 제외 ${row.excluded}회`,fmt(row.baseline),fmt(row.incremental),row.delta===null?'성공 표본 없음':`${row.delta<0?'증가 ':'감소 '}${fmt(Math.abs(row.delta))} · ${row.percent===null?'비율 없음':decimal(Math.abs(row.percent))+'%'} · 범위 ${fmt(row.min)} ~ ${fmt(row.max)}`].forEach(text=>add('td',text,tr));});
      const reference=report.comparison.repository_reference;
      add('h2','실제 저장소 테스트 번들 참조');
      if (!reference) add('p','첨부된 실제 저장소 번들 참조 없음. fixture 결과를 저장소 전체 성능으로 일반화하지 않습니다.');
      else {add('p',`커밋된 shard inventory · 샤드 ${reference.conditions.shard_count}개 · 성공 표본 ${reference.summary.eligible_samples}개 · Click 재사용 반사실이 아닌 그룹화 비용 참조`);const rt=add('table','');[['모든 샤드 순차',fmt(reference.summary.same_shards_duration_ms.median)],['기존 parent 명령',fmt(reference.summary.parent_suite_duration_ms.median)],['parent − shards',fmt(reference.summary.parent_minus_shards_ms.median)]].forEach(([label,value])=>{const tr=add('tr','',rt);add('th',label,tr);add('td',value,tr);});}
    }
    add('h2','범위와 주의사항');report.notes.forEach(text=>add('p',text));
    const raw=add('details','');add('summary','기계 판독용 원시 ID·조건 (서명 없음)',raw);add('pre',JSON.stringify(report,null,2),raw);
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
    const selectedMetrics = activeBatch ? data.batch_summaries?.[activeBatch.batch_id] : null;
    const incremental = selectedMetrics?.incremental || data.summary.incremental;
    const savings = selectedMetrics?.revalidation_savings || data.summary.revalidation_savings;
    activeSummary = incremental;
    activeSavings = savings;
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
    $('reuseOrigins').textContent=`선택한 배치의 실제 재사용: 같은 계약 ${reusedItems.length-prior}개 · 이전 계약에서 재판정 ${prior}개`;
    const a=data.accounting;
    $('reuseRate').textContent=a ? `보관된 검증 그룹 요청 기준: ${a.reuse_numerator} / ${a.request_denominator} · ${a.reuse_rate===null?'비율 미측정':(100*a.reuse_rate).toFixed(1)+'%'} · ${a.from_timestamp?new Date(a.from_timestamp*1000).toLocaleString():'시작 기록 없음'} ~ ${a.through_timestamp?new Date(a.through_timestamp*1000).toLocaleString():'종료 기록 없음'}. 실제 재시도는 별도 요청이며 중복 수신·화면 갱신은 추가 집계하지 않습니다.` : '집계 정보 없음';
    $('zeroReuse').textContent=reusedItems.length ? `재사용 중 과거 시간 표본 미측정 ${Math.max(0,(savings?.coverage?.actual_reused_source_count ?? 0)-(savings?.coverage?.timed_reused_source_count ?? 0))}개` : [...new Set(view.sources.map(source=>source.next_action || reasonFor(source)))].slice(0,3).join(' ');
    const outcome=renderOutcome(activeBatch,incremental,savings);
    $('requestWall').textContent = fmt(incremental.request_wall_ms);
    $('processingDuration').textContent = fmt(incremental.measured_processing_ms);
    $('timeScope').textContent = activeBatch?.measurement_scope === 'hook-entry-to-result-recording' ? '부분 실측: 같은 호스트의 단조 시계로 Hook 진입부터 결과 기록 직전까지 측정했습니다. Hook 이전 요청 대기·최종 저장·호스트 반환은 제외합니다.' : activeBatch?.measurement_scope === 'prepare-only' ? '부분 계측: 준비·재사용 판정만 포함. 호스트 대기·전달·최종 저장·반환은 제외합니다.' : '부분 계측: 준비 + runner의 개별 경과시간 합계. 호스트 대기·전달·최종 저장·반환은 제외합니다.';
    if (!activeBatch) $('timeScope').textContent='이 계약의 요청-결과 시간은 아직 측정되지 않았습니다.';
    $('detailExecutedDuration').textContent = outcome.executedText;
    $('detailFullEstimate').textContent = outcome.fullText;
    $('managementOverhead').textContent = '측정 정보 없음';
    const setup=data.setup || {};
    $('setupStatus').textContent = setupStatusText[setup.status] || setup.status || '미설정';
    $('setupInitial').textContent = fmt(setup.initial_setup_ms);
    $('setupObservation').textContent = fmt(setup.observation_ms);
    $('setupProcessing').textContent = fmt(setup.click_processing_ms);
    $('setupRuns').textContent = `${fmt(setup.bootstrap_parent_ms)} / ${fmt(setup.bootstrap_shards_ms)}`;
    $('setupNet').textContent = signedDuration(setup.comparison_net_ms);
    $('setupScope').textContent = setup.comparison_scope === 'first-bootstrap-parent-vs-sequential-children-not-savings'
      ? '첫 기준 실행의 parent와 순차 shards 비교입니다. 절감 시간으로 집계하지 않습니다.'
      : '첫 기준 실행은 절감 시간으로 집계하지 않습니다.';
    $('shadowBreakdown').textContent = `${shadow.candidate_count} / ${shadow.confirmed_candidate_count} / ${shadow.contradiction_count}`;
    $('shadowTiming').textContent = `${fmt(shadow.potential_ms)} / ${fmt(shadow.observer_overhead_ms)}`;
    $('observerTitle').textContent = data.task.observer_mode === 'authoritative'
      ? 'Observer: authoritative'
      : data.task.observer_mode === 'shadow' ? 'Observer: Shadow 켜짐' : 'Observer: 꺼짐';
    $('observerBody').textContent = data.task.observer_mode === 'authoritative'
      ? '완전하고 현재 계약에 결합된 v2 관찰만 observed-input 재사용 권한이 됩니다.'
      : data.task.observer_mode === 'shadow'
        ? '예측 정확도를 측정하지만 검사 생략 권한은 만들지 않습니다.'
        : 'Dashboard는 계속 볼 수 있으며 기존 exact·policy reuse는 정상 동작합니다.';
    const conditions = [
      `집계 범위: ${savings?.aggregation_scope || '측정 정보 없음'}`,
      `시간 기준: ${savings?.basis || '측정 정보 없음'}`,
      `측정 단위: ${savings?.unit || '측정 정보 없음'}`,
      `생략 시간 상태: ${savings?.omitted_test_execution_status || 'unmeasured'}`,
      `이번 실행시간 상태: ${savings?.executed_test_execution_status || 'unmeasured'}`,
      `전체 순차 실행 상태: ${savings?.full_sequential_test_execution_estimate_status || 'unmeasured'}`,
      `감소율 상태: ${savings?.test_execution_reduction_status || 'unmeasured'}`,
      `상태 사유: ${(savings?.reason_codes || []).join(' · ') || '없음'}`,
      `재사용 판정: exact ${count(incremental.exact_reuse_count)} · observed-input ${count(incremental.dependency_reuse_count)} · safe-change ${count(incremental.safe_change_reuse_count)}`,
    ];
    $('rawConditions').replaceChildren(...conditions.map(text => {const tag=document.createElement('span');tag.className='tag';tag.textContent=text;return tag;}));
    $('updated').textContent = `${new Date(data.generated_at * 1000).toLocaleTimeString()} 갱신`;
    snapshot = view;
    renderSources(view);
    snapshot = data;
    renderComparison();
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
