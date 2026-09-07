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
  <a class="skip" href="#main"><span data-i18n="대시보드로 건너뛰기">대시보드로 건너뛰기</span></a>
  <div class="app-shell">
    <aside class="sidebar" aria-label="주 탐색" data-i18n-aria-label="주 탐색">
      <a class="brand" href="#main"><span class="mark" aria-hidden="true">C<span>•</span></span><b>Click.</b></a>
      <p class="nav-caption">WORKSPACE</p>
      <nav><a class="nav-link active" href="#main" aria-current="page"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/></svg><span data-i18n="대시보드">대시보드</span></a><a class="nav-link" href="#checksSection"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 4H4v16h3M17 4h3v16h-3M8 12l3 3 5-7"/></svg><span data-i18n="검증 묶음">검증 묶음</span></a><a class="nav-link" href="#historySection"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9a9 9 0 1 1-1 7M3 4v6h6M12 7v6l4 2"/></svg><span data-i18n="작업 이력">작업 이력</span></a><a class="nav-link" href="#measurementDetails"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 4H4v16h5M15 4h5v16h-5M12 3v18"/></svg><span data-i18n="측정 상세">측정 상세</span></a><a class="nav-link" href="#exportsSection"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 18 19 5M8 5h11v11"/></svg><span data-i18n="공유 리포트">공유 리포트</span></a></nav>
      <div class="sidebar-note"><span class="note-symbol" aria-hidden="true">↺</span><b><span data-i18n="필요한 검증만, 다시.">필요한 검증만, 다시.</span></b><p><span data-i18n="실행한 결과와 재사용한 근거를 한눈에 확인하세요.">실행한 결과와 재사용한 근거를 한눈에 확인하세요.</span></p><ul class="sidebar-points"><li><span data-i18n="실제 실행 기록">실제 실행 기록</span></li><li><span data-i18n="확인 가능한 재사용">확인 가능한 재사용</span></li><li><span data-i18n="근거 있는 비교">근거 있는 비교</span></li></ul></div>
      <div class="sidebar-bottom"><a class="help-link" href="#measurementDetails"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 4H4v16h5M15 4h5v16h-5M12 3v18"/></svg><span data-i18n="측정 안내">측정 안내</span></a><small id="engineVersion"></small><span class="local-label"><span data-i18n="● 로컬 · 읽기 전용">● 로컬 · 읽기 전용</span></span></div>
    </aside>
    <main id="main" tabindex="-1">
      <header class="topbar">
        <div class="page-heading"><p class="eyebrow">LESS REPETITION. MORE PROGRESS.</p><h1><span data-i18n="검증은 더 스마트하게, 개발은 더 빠르게.">검증은 더 스마트하게, 개발은 더 빠르게.</span></h1><p class="page-subtitle"><span data-i18n="실행한 검사와 재사용한 근거를 모아, 이번 작업에서 달라진 점을 확인하세요.">실행한 검사와 재사용한 근거를 모아, 이번 작업에서 달라진 점을 확인하세요.</span></p></div>
        <div class="top-actions"><label class="language-picker" for="languageSelect"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a18 18 0 0 1 0 18 18 18 0 0 1 0-18"/></svg><span data-i18n="언어">언어</span><select id="languageSelect" aria-label="언어" data-i18n-aria-label="언어"><option value="ko" lang="ko">한국어</option><option value="en" lang="en">English</option><option value="zh-CN" lang="zh-CN">简体中文</option></select></label><div class="live"><i aria-hidden="true"></i><span id="connection"><span data-i18n="연결 중">연결 중</span></span></div><button id="refreshNow" class="icon-button" type="button" aria-label="기록 새로고침" data-i18n-aria-label="기록 새로고침">↻</button><a class="primary-link" href="#exportsSection"><span data-i18n="리포트 공유">리포트 공유</span><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 18 19 5M8 5h11v11"/></svg></a></div>
      </header>
      <section class="panel task-summary" aria-label="현재 작업과 승인 상태" data-i18n-aria-label="현재 작업과 승인 상태">
        <div class="task-icon"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20V10h4v10M10 20V4h4v16M16 20v-7h4v7"/></svg></div>
        <div class="task-identity"><p class="eyebrow" id="selectionLabel"><span data-i18n="현재 작업">현재 작업</span></p><h2 id="contractName"><span data-i18n="현재 작업">현재 작업</span></h2><p id="taskline"><span data-i18n="현재 Click 상태를 불러오는 중…">현재 Click 상태를 불러오는 중…</span></p></div>
        <div class="task-meta"><span id="approvalState" class="pill"><span data-i18n="승인 상태 확인 중">승인 상태 확인 중</span></span><time id="batchTimestamp"></time></div>
        <details id="scopeDetails"><summary><span data-i18n="작업 범위와 승인 근거">작업 범위와 승인 근거</span></summary><div id="contractPromises"></div><div id="allPromises"></div><div id="contractBoundary"></div><p class="muted"><span data-i18n="이 Viewer와 보관 이력은 승인·실행·재사용 권한을 만들거나 승계하지 않습니다.">이 Viewer와 보관 이력은 승인·실행·재사용 권한을 만들거나 승계하지 않습니다.</span></p><code id="contractId"></code><p id="controlSummary" class="muted"></p></details>
      </section>
      <div class="impact-grid">
        <section class="panel savings-hero" aria-labelledby="impactTitle">
          <div class="hero-copy">
            <div class="panel-title"><div><h2 id="impactTitle"><span data-i18n="이번 작업의 효과">이번 작업의 효과</span></h2><p class="panel-subtitle"><span data-i18n="검증 기록과 전체 작업 비교를 함께 확인하세요.">검증 기록과 전체 작업 비교를 함께 확인하세요.</span></p></div><a class="info-link" href="#measurementDetails" aria-label="측정 상세" data-i18n-aria-label="측정 상세">ⓘ</a></div>
            <div class="headline-metrics">
              <article id="taskEffect" class="headline-card time-card" aria-labelledby="taskEffectTitle" aria-live="polite">
                <div class="metric-heading"><span class="metric-icon"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 6v6h6"/></svg></span><h2 id="taskEffectTitle"><span data-i18n="순작업시간">순작업시간</span></h2></div>
                <div class="hero-number"><strong id="taskEffectValue" class="hero-value"><span data-i18n="미측정">미측정</span></strong></div>
                <p class="metric-scope"><span data-i18n="동등한 완료 조건 · 전체 작업 기준">동등한 완료 조건 · 전체 작업 기준</span></p><p id="taskEffectScope" class="hero-status"><span data-i18n="직접 비교한 작업 시작·종료 경계가 없습니다.">직접 비교한 작업 시작·종료 경계가 없습니다.</span></p>
              </article>
              <article class="headline-card token-card" aria-labelledby="tokenTitle">
                <div class="metric-heading"><span class="metric-icon"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/></svg></span><h2 id="tokenTitle"><span data-i18n="토큰 절감률">토큰 절감률</span></h2></div>
                <div class="hero-number"><strong id="tokenSavings" class="hero-value">—</strong><span id="tokenMeasuredBadge" class="estimate-badge" hidden><span data-i18n="실측 비교">실측 비교</span></span></div>
                <p class="metric-scope" id="tokenBasis"><span data-i18n="선택한 평가 · 개선 전 대비">선택한 평가 · 개선 전 대비</span></p><p id="tokenStatus" class="hero-status"><span data-i18n="동등한 완료 조건의 전체 작업 비교가 없습니다.">동등한 완료 조건의 전체 작업 비교가 없습니다.</span></p>
                <p id="tokenScope" class="truth-note"><span data-i18n="전체 작업 기준 · 절대 토큰 수는 공개하지 않습니다.">전체 작업 기준 · 절대 토큰 수는 공개하지 않습니다.</span></p>
              </article>
            </div>
            <div class="savings-breakdown"><h3><span data-i18n="측정 내역">측정 내역</span></h3><div class="breakdown-row"><span><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 3h6M10 3v7L4 20h16L14 10V3M8 14h8"/></svg><span id="title"><span data-i18n="테스트 실행에서 절감">테스트 실행에서 절감</span></span></span><span class="breakdown-value"><strong id="estimatedAvoided">—</strong><span id="heroEstimateBadge" class="estimate-badge" hidden><span data-i18n="추정">추정</span></span></span></div><div class="breakdown-row"><span><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 3-1 3-3 1-1 4 2 2v3l3 2 3-1 3 1 3-2v-3l2-2-1-4-3-1-1-3z"/><circle cx="12" cy="11" r="3"/></svg><span data-i18n="Click 전체 관리비용">Click 전체 관리비용</span></span><strong class="unknown"><span data-i18n="미측정">미측정</span></strong></div></div>
            <div class="task-result"><h3><span data-i18n="전체 작업 효과">전체 작업 효과</span></h3><span id="taskEffectState" class="pill pending"><span data-i18n="미측정">미측정</span></span></div>
            <p id="taskEffectAdverse" class="task-effect-adverse" hidden></p><label id="taskEvaluationLabel" for="taskEvaluationSelect" class="evaluation-picker" hidden><span data-i18n="작업 비교 선택">작업 비교 선택</span><select id="taskEvaluationSelect"></select></label>
            <p id="estimateCoverage" class="hero-status"><span data-i18n="과거 성공 실행의 시간 근거를 확인하는 중입니다.">과거 성공 실행의 시간 근거를 확인하는 중입니다.</span></p>
            <p class="truth-note"><span data-i18n="테스트 실행 구간의 추정입니다. 전체 요청 대기시간이나 관리비용을 차감한 순절감이 아닙니다.">테스트 실행 구간의 추정입니다. 전체 요청 대기시간이나 관리비용을 차감한 순절감이 아닙니다.</span></p>
          </div>
          <aside id="waitIncreaseNotice" class="wait-notice" hidden><a href="#measurementDetails"><span data-i18n="별도 비교에서 Click 구간 증가가 관측됐습니다. 상세 보기">별도 비교에서 Click 구간 증가가 관측됐습니다. 상세 보기</span></a><span><span data-i18n="가져온 fixture의 해당 비교 결과 · 이번 요청의 실측 결과가 아닙니다.">가져온 fixture의 해당 비교 결과 · 이번 요청의 실측 결과가 아닙니다.</span></span></aside>
        </section>
        <section class="panel execution-overview" aria-labelledby="executionTitle">
          <div class="panel-title"><div><h2 id="executionTitle"><span data-i18n="검증 실행 현황">검증 실행 현황</span></h2><p class="panel-subtitle"><span data-i18n="전체와 이번 실행, 한눈에">전체와 이번 실행, 한눈에</span></p></div><span id="executionStatus" class="pill pending"><span data-i18n="판정 중">판정 중</span></span></div>
          <p id="batchHeadline" class="batch-headline"><span data-i18n="실제 검증 기록을 기다리고 있습니다.">실제 검증 기록을 기다리고 있습니다.</span></p><p id="resultCoverage" class="result-coverage sr-only"></p>
          <div class="metrics" aria-label="실제 증분 검증 결과" data-i18n-aria-label="실제 증분 검증 결과">
            <article><span><span data-i18n="전체 검증 묶음">전체 검증 묶음</span></span><strong id="currentChecks">—</strong></article><article><span><span data-i18n="이번에 실제 실행">이번에 실제 실행</span></span><strong id="executedChecks">—</strong><small id="executionDetail"></small></article><article><span><span data-i18n="실제 재사용 적용">실제 재사용 적용</span></span><button id="reusedChecks" class="metric-link" type="button" aria-label="재사용한 검증 묶음 보기" data-i18n-aria-label="재사용한 검증 묶음 보기">—</button></article><article><span><span data-i18n="결과 확보">결과 확보</span></span><strong id="verifiedChecks">—</strong></article>
          </div>
          <div class="group-comparison" id="groupComparison"><div class="group-row"><div class="row-label"><span><span data-i18n="검증 묶음별 실행 상태">검증 묶음별 실행 상태</span></span><b id="actualGroupCount">—</b></div><div id="actualBlocks" class="group-blocks" aria-label="묶음별 실제 실행과 재사용 결과" data-i18n-aria-label="묶음별 실제 실행과 재사용 결과"></div></div><p class="block-legend"><span><span data-i18n="● 실행">● 실행</span></span><span><span data-i18n="↺ 재사용">↺ 재사용</span></span><span><span data-i18n="! 실패·중단">! 실패·중단</span></span><span><span data-i18n="· 미실행·미확정">· 미실행·미확정</span></span></p></div>
          <div id="executionComparison" class="execution-comparison" hidden><div class="section-heading"><h3><span data-i18n="테스트 실행 시간">테스트 실행 시간</span></h3><span id="comparisonState" class="pill"></span></div><div class="comparison-values"><p><span><span data-i18n="동일 묶음 전체 실행 예상">동일 묶음 전체 실행 예상</span></span><strong id="fullEstimate">—</strong></p><p><span><span data-i18n="이번 테스트 실행">이번 테스트 실행</span></span><strong id="executedDuration">—</strong></p><p><span><span data-i18n="명령 실행 구간 감소">명령 실행 구간 감소</span></span><strong id="reductionRate">—</strong></p></div><div class="axis" aria-label="동일 시간 축 비교" data-i18n-aria-label="동일 시간 축 비교"><div><span><span data-i18n="전체 순차 실행 예상">전체 순차 실행 예상</span></span><div class="track"><i id="fullBar" class="bar full"></i></div></div><div><span><span data-i18n="이번 테스트 실행">이번 테스트 실행</span></span><div class="track current-track"><i id="executedBar" class="bar executed"></i><i id="avoidedBar" class="bar avoided"></i></div></div></div></div>
          <p id="avoidedSegment" class="avoided-label" hidden></p><p id="comparisonGuidance" class="state-guidance"></p><button id="showReused" class="text-link" type="button"><span data-i18n="어떤 검증을 다시 실행하지 않았나요?">어떤 검증을 다시 실행하지 않았나요?</span><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h16m-6-6 6 6-6 6"/></svg></button>
        </section>
      </div>
      <div class="work-grid">
        <section class="panel checks" id="checksSection" tabindex="-1"><div class="panel-title"><div><h2><span data-i18n="재사용 근거">재사용 근거</span></h2><p class="panel-subtitle"><span data-i18n="어떤 검증을 다시 실행하지 않았나요?">어떤 검증을 다시 실행하지 않았나요?</span></p></div><span id="sourceCount" class="count">0</span></div><div class="source-toolbar"><div class="source-filters" role="group" aria-label="검증 결과 필터" data-i18n-aria-label="검증 결과 필터"><button type="button" id="filterAll" aria-pressed="true"><span data-i18n="전체">전체</span></button><button type="button" id="filterReused" aria-pressed="false"><span data-i18n="재사용">재사용</span></button><button type="button" id="filterExecuted" aria-pressed="false"><span data-i18n="실제 실행">실제 실행</span></button></div><p id="filterStatus" class="muted" role="status"></p></div><div class="source-scroll" role="region" aria-label="검증 묶음" data-i18n-aria-label="검증 묶음" tabindex="0"><table class="source-table"><thead><tr><th scope="col"><span data-i18n="검증 묶음">검증 묶음</span></th><th scope="col"><span data-i18n="마지막 성공">마지막 성공</span></th><th scope="col"><span data-i18n="변경 영향">변경 영향</span></th><th scope="col"><span data-i18n="실제 결과">실제 결과</span></th></tr></thead><tbody id="sources"></tbody></table></div><p id="reuseOrigins" class="muted"></p></section>
        <section class="panel timeline" id="historySection"><div class="panel-title"><div><h2><span data-i18n="작업 흐름">작업 흐름</span></h2><p class="panel-subtitle"><span data-i18n="실제로 기록된 검증 요청을 확인하세요.">실제로 기록된 검증 요청을 확인하세요.</span></p></div><button id="latestBatch" class="text-link" type="button"><span data-i18n="최신 배치">최신 배치</span><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h16m-6-6 6 6-6 6"/></svg></button></div><ol id="batchFlow" class="batch-flow"></ol><details class="history-details"><summary><span data-i18n="전체 작업 이력">전체 작업 이력</span></summary><label for="batchSelect"><span data-i18n="상세 결과 선택">상세 결과 선택</span></label><select id="batchSelect"></select><p id="batchState" class="muted"></p><div class="retained-impact"><p class="eyebrow"><span data-i18n="보관된 완료 요청">보관된 완료 요청</span></p><strong id="retainedReuse">—</strong><p id="retainedAvoided"></p><p id="retainedScope" class="muted"></p></div><p id="historyMeta" class="muted"></p></details></section>
      </div>
      <details class="panel explanation" id="explanationSection" tabindex="-1"><summary><span data-i18n="선택한 묶음의 실행·재사용 근거">선택한 묶음의 실행·재사용 근거</span></summary><h2 id="whyTitle"><span data-i18n="묶음을 선택하세요">묶음을 선택하세요</span></h2><p id="whyBody"><span data-i18n="실제 판정 결과와 시간 근거를 설명합니다.">실제 판정 결과와 시간 근거를 설명합니다.</span></p><p id="originName"></p><details class="lineage"><summary id="lineageSummary"><span data-i18n="실행·재사용 계보">실행·재사용 계보</span></summary><ol id="lineageSteps"></ol></details><details><summary><span data-i18n="원시 ID·revision·측정 결합 상세">원시 ID·revision·측정 결합 상세</span></summary><div id="limits"></div></details></details>
      <div class="dashboard-note"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20V10h4v10M10 20V4h4v16M16 20v-7h4v7"/></svg><span data-i18n="더 적은 반복, 더 많은 진짜 개발에 집중하세요.">더 적은 반복, 더 많은 진짜 개발에 집중하세요.</span><span class="note-caption"><span data-i18n="관찰한 결과와 측정 범위를 함께 표시합니다.">관찰한 결과와 측정 범위를 함께 표시합니다.</span></span></div>
    <details id="measurementDetails" class="panel measurement-details">
      <summary><span><b><span data-i18n="측정 상세">측정 상세</span></b><small><span data-i18n="요청·처리 구간, 관리비용, 비교 실측, 원시 조건">요청·처리 구간, 관리비용, 비교 실측, 원시 조건</span></small></span></summary>
      <div class="group-row baseline-row"><div class="row-label"><span><span data-i18n="전체 재실행 기준">전체 재실행 기준</span></span><b id="allGroupCount">—</b></div><div id="baselineBlocks" class="group-blocks" aria-label="전체 묶음을 실행하는 비교 기준" data-i18n-aria-label="전체 묶음을 실행하는 비교 기준"></div></div><p class="muted" id="blockScope"><span data-i18n="같은 묶음을 모두 실행하는 기준이며, 관찰한 이전 실행이 아닙니다.">같은 묶음을 모두 실행하는 기준이며, 관찰한 이전 실행이 아닙니다.</span></p><p class="muted"><span data-i18n="시간 근거 커버리지">시간 근거 커버리지</span>: <strong id="timingCoverage">—</strong></p><div class="basis" aria-label="추정 기준" data-i18n-aria-label="추정 기준"><span data-i18n="과거 실행 기록 기반 추정">과거 실행 기록 기반 추정</span><span data-i18n="동일 묶음·순차 실행 기준">동일 묶음·순차 실행 기준</span><span data-i18n="관리비용 별도">관리비용 별도</span></div><div class="metric-split">
        <article class="compact"><p class="eyebrow"><span data-i18n="현재 요청에서 관측한 구간">현재 요청에서 관측한 구간</span></p><div class="statline"><span><span data-i18n="Hook 진입 → 결과 기록 · 부분 요청시간">Hook 진입 → 결과 기록 · 부분 요청시간</span></span><strong id="requestWall">—</strong></div><div class="statline"><span><span data-i18n="현재 측정 가능한 처리 구간">현재 측정 가능한 처리 구간</span></span><strong id="processingDuration">—</strong></div><div class="statline"><span><span data-i18n="이번 source-command 실행 구간">이번 source-command 실행 구간</span></span><strong id="detailExecutedDuration">—</strong></div><div class="statline"><span><span data-i18n="동일 묶음 전체 순차 실행 추정">동일 묶음 전체 순차 실행 추정</span></span><strong id="detailFullEstimate">—</strong></div><p class="muted" id="timeScope"><span data-i18n="호스트 요청 전·최종 반환은 계측 범위 밖입니다.">호스트 요청 전·최종 반환은 계측 범위 밖입니다.</span></p></article>
        <article class="compact"><p class="eyebrow"><span data-i18n="관리비용과 순 요청시간">관리비용과 순 요청시간</span></p><div class="statline"><span><span data-i18n="Click 전체 관리비용">Click 전체 관리비용</span></span><strong id="managementOverhead"><span data-i18n="측정 정보 없음">측정 정보 없음</span></strong></div><p class="muted"><span data-i18n="포함 관계가 있는 요청시간과 검사시간을 빼서 관리비용을 만들지 않습니다.">포함 관계가 있는 요청시간과 검사시간을 빼서 관리비용을 만들지 않습니다.</span></p><div class="statline"><span><span data-i18n="비교 환경의 순 요청 시간 차이">비교 환경의 순 요청 시간 차이</span></span><strong id="pairedNet"><span data-i18n="측정 정보 없음">측정 정보 없음</span></strong></div><p id="comparisonStatus" class="muted"><span data-i18n="동등한 paired 비교 실측이 없습니다.">동등한 paired 비교 실측이 없습니다.</span></p><div class="statline"><span><span data-i18n="Observer 후보 / 확인 / 모순">Observer 후보 / 확인 / 모순</span></span><strong id="shadowBreakdown">—</strong></div><div class="statline"><span><span data-i18n="잠재 시간 / Observer 보조 처리시간">잠재 시간 / Observer 보조 처리시간</span></span><strong id="shadowTiming">—</strong></div><p class="muted" id="tracingSlowdown"><span data-i18n="추적으로 인한 검사 지연은 별도 측정하지 않았습니다.">추적으로 인한 검사 지연은 별도 측정하지 않았습니다.</span></p></article>
        <article class="compact setup-costs"><p class="eyebrow"><span data-i18n="최초 설정과 기준 실행">최초 설정과 기준 실행</span></p><div class="statline"><span><span data-i18n="설정 상태">설정 상태</span></span><strong id="setupStatus"><span data-i18n="미설정">미설정</span></strong></div><div class="statline"><span><span data-i18n="초기 설정 비용">초기 설정 비용</span></span><strong id="setupInitial"><span data-i18n="측정 정보 없음">측정 정보 없음</span></strong></div><div class="statline"><span><span data-i18n="Observer 비용">Observer 비용</span></span><strong id="setupObservation"><span data-i18n="측정 정보 없음">측정 정보 없음</span></strong></div><div class="statline"><span><span data-i18n="Click 처리">Click 처리</span></span><strong id="setupProcessing"><span data-i18n="측정 정보 없음">측정 정보 없음</span></strong></div><div class="statline"><span><span data-i18n="parent 전체 실행 / 순차 shards">parent 전체 실행 / 순차 shards</span></span><strong id="setupRuns"><span data-i18n="측정 정보 없음">측정 정보 없음</span></strong></div><div class="statline"><span><span data-i18n="parent − 순차 shards">parent − 순차 shards</span></span><strong id="setupNet"><span data-i18n="측정 정보 없음">측정 정보 없음</span></strong></div><p id="setupScope" class="muted"><span data-i18n="첫 기준 실행은 절감 시간으로 집계하지 않습니다.">첫 기준 실행은 절감 시간으로 집계하지 않습니다.</span></p></article>
        <article class="compact setup-costs" id="taskMeasurementDetails"><p class="eyebrow"><span data-i18n="전체 작업 비교 상세">전체 작업 비교 상세</span></p><div class="statline"><span><span data-i18n="선택한 비교 범위">선택한 비교 범위</span></span><strong id="detailTaskScope"><span data-i18n="미측정">미측정</span></strong></div><div class="statline"><span><span data-i18n="작업 완료시간 차이">작업 완료시간 차이</span></span><strong id="detailTaskDelta"><span data-i18n="측정 정보 없음">측정 정보 없음</span></strong></div><div class="statline"><span><span data-i18n="표본 · 완료/실패/취소/미완료">표본 · 완료/실패/취소/미완료</span></span><strong id="detailTaskSamples">—</strong></div><div class="statline"><span><span data-i18n="빨라짐/변화 없음/느려짐">빨라짐/변화 없음/느려짐</span></span><strong id="detailTaskOutcomes">—</strong></div><div class="statline"><span><span data-i18n="사용자 개입 · 기준/개선">사용자 개입 · 기준/개선</span></span><strong id="detailInterventions"><span data-i18n="측정 정보 없음">측정 정보 없음</span></strong></div><div class="statline"><span><span data-i18n="확인된 사용자 수행시간 · 기준/개선">확인된 사용자 수행시간 · 기준/개선</span></span><strong id="detailInterventionTime"><span data-i18n="측정 정보 없음">측정 정보 없음</span></strong></div><div class="statline"><span><span data-i18n="도구 호출 · 기준/개선">도구 호출 · 기준/개선</span></span><strong id="detailToolCalls"><span data-i18n="측정 정보 없음">측정 정보 없음</span></strong></div><div class="statline"><span><span data-i18n="실패 후 수정 전 조회 · 기준/개선">실패 후 수정 전 조회 · 기준/개선</span></span><strong id="detailFailureCalls"><span data-i18n="측정 정보 없음">측정 정보 없음</span></strong></div><div class="statline"><span><span data-i18n="수정·재검증 주기 · 기준/개선">수정·재검증 주기 · 기준/개선</span></span><strong id="detailRepairCycles"><span data-i18n="측정 정보 없음">측정 정보 없음</span></strong></div><div class="statline"><span><span data-i18n="모델 왕복 · 기준/개선">모델 왕복 · 기준/개선</span></span><strong id="detailRoundTrips"><span data-i18n="측정 정보 없음">측정 정보 없음</span></strong></div><div class="statline"><span><span data-i18n="활동 구간 · 기준/개선 (미분류)">활동 구간 · 기준/개선 (미분류)</span></span><strong id="detailActivity"><span data-i18n="측정 정보 없음">측정 정보 없음</span></strong></div><p id="taskObservability" class="muted"><span data-i18n="활동 구간은 겹칠 수 있어 전체 작업시간으로 합산하지 않습니다. 숨은 추론은 미측정입니다.">활동 구간은 겹칠 수 있어 전체 작업시간으로 합산하지 않습니다. 숨은 추론은 미측정입니다.</span></p><p id="taskLocalRef" class="muted"></p></article>
      </div>
      <details class="telemetry"><summary><span data-i18n="Evidence Map · Shadow 관찰 상세 · 재사용 권한 없음">Evidence Map · Shadow 관찰 상세 · 재사용 권한 없음</span></summary><p><strong id="observerTitle"><span data-i18n="Observer 확인 중">Observer 확인 중</span></strong> · <span id="observerBody"><span data-i18n="Dashboard와 Observer는 독립적입니다.">Dashboard와 Observer는 독립적입니다.</span></span></p><article class="map-panel"><div class="panel-title"><h2><span data-i18n="선택한 묶음의 관찰된 입력">선택한 묶음의 관찰된 입력</span></h2><span id="mapMeta" class="muted"></span></div><div id="emptyMap" class="empty"><span data-i18n="묶음을 선택하면 현재 입력과 이전 baseline의 관계를 보여줍니다.">묶음을 선택하면 현재 입력과 이전 baseline의 관계를 보여줍니다.</span></div><svg id="map" role="img" aria-label="선택한 묶음의 Evidence Map" data-i18n-aria-label="선택한 묶음의 Evidence Map"></svg></article></details>
      <section class="comparison"><p class="eyebrow"><span data-i18n="명시적으로 실행한 paired 비교 · 일상 추정치와 별개">명시적으로 실행한 paired 비교 · 일상 추정치와 별개</span></p><h2><span data-i18n="전체 재실행 기준 vs Click 부분 검증">전체 재실행 기준 vs Click 부분 검증</span></h2><p id="comparisonInfo"><span data-i18n="아직 비교 측정이 없습니다. 검증 구간 비교 JSON 또는 Phase 4 공개 작업 비교 JSON을 가져올 수 있습니다. 가져온 결과는 승인·재사용 권한이 아닙니다.">아직 비교 측정이 없습니다. 검증 구간 비교 JSON 또는 Phase 4 공개 작업 비교 JSON을 가져올 수 있습니다. 가져온 결과는 승인·재사용 권한이 아닙니다.</span></p><pre>python3 benchmarks/task_efficiency.py INTERNAL.json --public-output PUBLIC.json</pre><label><span data-i18n="비교 JSON 선택">비교 JSON 선택</span> <input id="comparisonFile" type="file" accept="application/json,.json"></label> <button id="clearComparison" type="button" hidden><span data-i18n="비교 자료 제거">비교 자료 제거</span></button><div id="comparisonChart"></div></section>
      <details><summary><span data-i18n="원시 측정 조건과 상태 사유">원시 측정 조건과 상태 사유</span></summary><div id="rawConditions"></div><p id="reuseRate" class="muted"></p><p id="zeroReuse" class="muted"></p><p class="muted"><span data-i18n="비교 실측이 없는 live 요청은 순절감을 계산하지 않습니다. 가져온 보고서와 Viewer는 실행 권한을 만들지 않습니다.">비교 실측이 없는 live 요청은 순절감을 계산하지 않습니다. 가져온 보고서와 Viewer는 실행 권한을 만들지 않습니다.</span></p></details>
    </details>
    <section class="panel exports" id="exportsSection"><h2><span data-i18n="공유 리포트">공유 리포트</span></h2><p class="muted"><span data-i18n="현재 선택한 배치와 가져온 비교 측정을 내보냅니다. 파일 경로·원시 명령·환경 값·토큰은 제외합니다. 검증은 실행하지 않습니다.">현재 선택한 배치와 가져온 비교 측정을 내보냅니다. 파일 경로·원시 명령·환경 값·토큰은 제외합니다. 검증은 실행하지 않습니다.</span></p><textarea id="shareSummary" readonly aria-label="공유용 요약 문구" data-i18n-aria-label="공유용 요약 문구" rows="4"></textarea><div class="export-actions"><button type="button" id="copySummary"><span data-i18n="요약 문구 복사">요약 문구 복사</span></button> <button type="button" id="exportJson"><span data-i18n="JSON 내보내기">JSON 내보내기</span></button> <button type="button" id="exportHtml"><span data-i18n="독립형 HTML 내보내기">독립형 HTML 내보내기</span></button><span id="exportStatus" role="status"></span></div></section>
  <footer><span><span data-i18n="로컬 전용 · 읽기 전용 · 파일 내용 미노출">로컬 전용 · 읽기 전용 · 파일 내용 미노출</span></span><span id="updated"><span data-i18n="아직 갱신되지 않음">아직 갱신되지 않음</span></span></footer>
  </main>
  </div>
  <script src="/app.js" defer></script>
</body>
</html>
"""

CSS = """:root { color-scheme:light; --text:#101e4b; --muted:#516f96; --teal:#008b98; --green:#008a72; --blue:#06a4ce; --violet:#6b61a8; --red:#bb3459; --amber:#865b1c; --line:#dce9f2; --panel:rgba(255,255,255,.91); font-family:"Pretendard","Noto Sans KR","Noto Sans SC","Microsoft YaHei",ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; font-synthesis:none }
* { box-sizing:border-box }
[hidden] { display:none!important }
html { scroll-behavior:smooth; scroll-padding-top:20px }
body { margin:0; padding:12px 14px; min-height:100vh; background:radial-gradient(ellipse at 95% 5%,#b9c7e8 0,transparent 60%),linear-gradient(120deg,#bfdfd9,#c7edf0); color:var(--text); font-size:14px; line-height:1.5 }
a { color:var(--teal); text-underline-offset:4px }
button,select,input,textarea { font:inherit }
button,a,select,input,summary,textarea { touch-action:manipulation }
button,summary { cursor:pointer }
button,select { border:1px solid #d3e4ed; border-radius:8px; padding:8px 12px; background:#f6fbfc; color:var(--text); font-size:12px }
button:hover:not(:disabled),a:hover { filter:brightness(.94) }
select,input { max-width:100% }
h1,h2,h3,p { overflow-wrap:anywhere; word-break:keep-all }
h1,h2,h3 { margin:0; letter-spacing:-.045em; line-height:1.35 }
h1 { font-size:clamp(25px,2.2vw,36px); font-weight:750 }
h2 { font-size:20px; font-weight:700 }
h3 { font-size:13px; font-weight:650 }
p { margin:0 0 10px }
.ui-icon { width:21px; height:21px; flex-shrink:0; vertical-align:middle }
.muted { font-size:11px; color:var(--muted); line-height:1.6 }
.eyebrow { margin:0 0 5px; color:var(--teal); font-size:10px; font-weight:650; letter-spacing:.09em }
.sr-only,.skip { position:absolute; width:1px; height:1px; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap }
.skip:focus { clip:auto; width:auto; height:auto; position:fixed; left:20px; top:10px; z-index:20; padding:12px; background:white }
:focus-visible { outline:3px solid #008fa4; outline-offset:3px }
main:focus,[tabindex="-1"]:focus { outline:none }
.app-shell { max-width:1660px; min-height:calc(100vh - 24px); margin:auto; display:grid; grid-template-columns:194px minmax(0,1fr); background:rgba(239,252,253,.66); border:1px solid rgba(255,255,255,.68); border-radius:21px; overflow:clip }
.sidebar { min-width:0; display:flex; flex-direction:column; position:sticky; top:12px; height:calc(100vh - 24px); align-self:start; overflow-y:auto; padding:26px 16px 20px; border-right:1px solid #cde3e8; background:rgba(239,253,248,.37) }
.brand { display:flex; align-items:center; gap:11px; padding:0 9px; margin-bottom:34px; color:#09193e; text-decoration:none; font-size:34px; letter-spacing:-1.6px }
.mark { display:flex; align-items:center; justify-content:center; width:36px; height:40px; border:2.5px solid #08637a; border-radius:12px; background:#f7fffd; color:#08637a; font-size:31px; font-weight:650; position:relative }
.mark span { position:absolute; top:-5px; right:1px; font-size:17px }
.nav-caption { font-size:10px; letter-spacing:.08em; color:#4a6c80; margin:0 12px 9px }
.sidebar nav { display:grid; gap:7px; scrollbar-width:none }
.nav-link { display:flex; gap:14px; align-items:center; padding:12px 14px; min-height:44px; border-radius:11px; color:#275474; font-size:14px; font-weight:550; text-decoration:none }
.nav-link.active { color:white; background:linear-gradient(110deg,#0098a7,#00acbd); box-shadow:0 4px 12px #03a6b311 }
.sidebar-note { border-top:1px solid #cadfe0; margin-top:30px; padding:18px 10px }
.note-symbol { display:block; font-size:43px; color:#094965; line-height:1; margin:0 0 14px }
.sidebar-note b { font-size:15px }
.sidebar-note p { color:#547688; font-size:12px; line-height:1.8; margin-top:9px }
.sidebar-points { list-style:none; padding:0; margin:20px 0 0; color:#315d76; font-size:12px }
.sidebar-points li { display:flex; align-items:center; gap:8px; margin:9px 0 }
.sidebar-points li:before { content:'✓'; width:15px; height:15px; display:grid; place-items:center; border-radius:50%; background:var(--green); color:white; font-size:10px }
.sidebar-bottom { display:grid; gap:12px; margin-top:auto; padding:18px 10px 0 }
.sidebar-bottom small { color:var(--muted); font-size:10px }
.language-picker { display:flex; align-items:center; gap:8px; min-height:42px; padding:4px 10px; border:1px solid #91cace; border-radius:10px; background:#fff; color:#006f7b; box-shadow:0 2px 8px #008b9810 }
.language-picker>.ui-icon { width:18px; height:18px }
.language-picker>span { font-size:11px; font-weight:600; white-space:nowrap }
.language-picker select { width:auto; min-width:104px; padding:6px; border:0; border-radius:5px; background:transparent; color:var(--text); font-size:13px; font-weight:600; cursor:pointer }
.language-picker:focus-within { border-color:var(--teal); box-shadow:0 0 0 3px #008b981a }
.help-link { display:flex; align-items:center; gap:10px; font-size:12px; text-decoration:none; color:#315d76 }
.local-label { font-size:10px; color:var(--green) }
main { min-width:0; padding:20px 22px 12px }
.topbar { position:relative; display:flex; flex-wrap:wrap; align-items:flex-start; justify-content:space-between; column-gap:15px; margin-bottom:14px }
.page-heading { min-width:0; flex:1 }
.page-subtitle { color:#315575; font-size:13px; margin:4px 0 0; line-height:1.55 }
.topbar .eyebrow { margin-bottom:5px; font-size:9px; letter-spacing:.1em }
.top-actions { display:flex; flex-wrap:wrap; align-items:center; justify-content:flex-end; gap:14px; max-width:100%; flex-shrink:0; padding-top:1px }
.live { display:flex; align-items:center; gap:8px; font-size:11px; white-space:nowrap; color:#244466 }
.live i { width:8px; height:8px; background:var(--amber); border-radius:50% }
.live.ok i { background:var(--green) }
.icon-button { padding:0; height:38px; width:38px; border-radius:50%; color:var(--teal); font-size:26px; background:rgba(255,255,255,.58) }
.primary-link { display:flex; align-items:center; justify-content:center; gap:14px; padding:11px 16px; border-radius:11px; background:linear-gradient(115deg,#008c9e,#00a4b2); color:white; font-size:12px; font-weight:600; text-decoration:none }
.primary-link .ui-icon { width:17px; height:17px }
.panel { min-width:0; padding:18px 21px; border:1px solid rgba(255,255,255,.95); border-radius:18px; background:var(--panel) }
.panel-title { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:15px }
.panel-subtitle { color:var(--muted); font-size:12px; margin:5px 0 0 }
.info-link { font-size:18px; color:#547a98; text-decoration:none }
.task-summary { display:grid; grid-template-columns:46px minmax(0,1fr) auto; gap:0 14px; padding:12px 18px; margin-bottom:14px; background:rgba(255,255,255,.55) }
.task-icon { width:46px; height:46px; border-radius:13px; display:grid; place-items:center; align-self:center; grid-row:1/3; color:var(--teal); background:#dff5ef }
.task-icon .ui-icon { width:24px; height:24px }
.task-identity h2 { font-size:15px; letter-spacing:-.02em }
.task-identity .eyebrow { font-size:10px; margin:0 }
#taskline { margin:1px 0 0; font-size:11px; color:var(--muted) }
.task-meta { display:grid; justify-items:end; gap:8px; grid-column:3; grid-row:1/3; align-self:center }
.task-meta time { color:var(--muted); font-size:11px }
.task-summary details { grid-column:2; font-size:11px }
.task-summary details[open] { grid-column:2/-1 }
.task-summary summary { padding:0; font-size:11px }
.task-summary details[open] summary { margin:8px 0 }
#contractPromises { display:flex; gap:18px }
#contractPromises p { flex:1 }
#allPromises,#contractBoundary,#contractId { overflow-wrap:anywhere }
.pill { display:inline-flex; align-items:center; padding:5px 9px; border-radius:15px; font-size:10px; line-height:1.4; background:#edf3f6; color:#486a7a; white-space:normal }
.pill.reused { background:#d8faec; color:#008b73 }
.pill.rerun { background:#ffedf1; color:var(--red) }
.pill.pending { background:#f5f1e9; color:#805f2d }
#approvalState { background:transparent; padding:0; font-size:10px; color:#537e8d }
.impact-grid { display:grid; grid-template-columns:minmax(0,.92fr) minmax(0,1.18fr); gap:14px; align-items:stretch }
.savings-hero { display:flex; flex-direction:column }
.headline-metrics { display:grid; grid-template-columns:1fr 1fr; gap:10px }
.headline-card { min-width:0; border:1px solid #d6e7f4; border-radius:10px; padding:13px 12px; background:linear-gradient(155deg,#fff,#fcfeff) }
.metric-heading { display:flex; align-items:center; gap:9px }
.metric-heading h2 { font-size:15px; letter-spacing:-.035em }
.metric-icon { display:grid; place-items:center; height:32px; width:32px; border-radius:50%; background:linear-gradient(140deg,#56d69b,#15aa61); color:white; flex-shrink:0; box-shadow:inset 0 0 0 1px #12a463 }
.token-card .metric-icon { background:linear-gradient(140deg,#3bd7ca,#00a7a6); box-shadow:inset 0 0 0 1px #04b8b1 }
.metric-icon .ui-icon { width:21px; height:21px }
.hero-number { display:flex; flex-wrap:wrap; align-items:baseline; gap:5px 8px; margin:9px 0 6px }
.hero-value { font-size:clamp(27px,2.45vw,39px); letter-spacing:-.055em; line-height:1.15; font-weight:680; color:var(--green); overflow-wrap:anywhere }
.hero-status { color:var(--muted); font-size:10px; line-height:1.6; margin:5px 0 0 }
.metric-scope { color:var(--teal); font-size:9px; line-height:1.5; margin:0 }
.estimate-badge { font-size:9px; font-weight:500; color:#497f8d; white-space:nowrap }
.truth-note { font-size:9px; color:var(--muted); line-height:1.6; margin:7px 0 0 }
.savings-breakdown { margin:13px 0 0 }
.savings-breakdown h3 { margin-bottom:5px }
.breakdown-row { display:flex; align-items:center; justify-content:space-between; gap:12px; border-top:1px solid var(--line); padding:8px 0; font-size:12px }
.breakdown-row>span { display:flex; align-items:center; gap:12px; color:#38557c }
.breakdown-row .ui-icon { width:19px; height:19px; color:var(--text) }
.breakdown-row strong { font-size:13px; color:var(--green); text-align:right }
.breakdown-row strong.unknown { font-weight:500; color:var(--muted) }
.breakdown-value { display:flex!important; justify-content:flex-end; align-items:baseline; gap:5px!important; flex-shrink:0 }
.task-result { display:flex; align-items:center; justify-content:space-between; border-top:1px solid #8b9bc0; padding-top:10px }
.task-result h3 { font-size:14px }
.task-effect-adverse { font-size:11px; color:var(--red); background:#fff0f2; border-radius:6px; padding:7px; margin:8px 0 0 }
.time-card[data-state=slower] .hero-value,#tokenSavings[data-state=increased] { color:var(--red) }
.time-card[data-state=unmeasured] .hero-value,#tokenSavings[data-state=unmeasured] { color:#667d95 }
.evaluation-picker { display:block; font-size:11px; color:var(--muted); margin-top:8px }
.evaluation-picker select { width:100%; margin-top:5px }
.savings-hero[data-state=failed] #estimatedAvoided,.savings-hero[data-state=cancelled] #estimatedAvoided { color:var(--red) }
.savings-hero[data-state=running] #estimatedAvoided { color:var(--amber) }
.wait-notice { margin-top:10px; padding:10px; background:#fff0ed; border-radius:8px; font-size:11px }
.wait-notice a { color:var(--red) }
.wait-notice>span { display:block; margin-top:4px; color:var(--muted) }
.batch-headline { color:#344f78; font-size:12px; line-height:1.5; margin:0 0 2px }
.result-coverage { color:var(--green); font-size:10px }
.execution-overview .panel-title { margin-bottom:10px }
.metrics { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); margin:14px 0 18px }
.metrics article { display:flex; flex-direction:column; gap:5px; min-width:0; padding:0 16px; border-left:1px solid var(--line) }
.metrics article:first-child { padding-left:0; border:0 }
.metrics article:last-child { padding-right:0 }
.metrics article>span { color:#44618a; font-size:11px }
.metrics strong,.metric-link { font-size:28px; line-height:1.3; font-weight:650; letter-spacing:-.035em }
.metrics small { display:none }
#executedChecks,.metric-link { color:var(--teal) }
.metric-link { padding:0; border:0; background:transparent; text-align:left; text-decoration:underline; text-underline-offset:4px; text-decoration-thickness:1px }
.group-comparison { padding:12px 0 0; border-top:1px solid var(--line) }
.row-label { display:flex; justify-content:space-between; gap:10px; color:var(--muted); font-size:11px; margin-bottom:7px }
.row-label b { font-weight:500; font-size:10px }
.group-row+.group-row { margin-top:10px }
.group-blocks { display:grid; grid-template-columns:repeat(auto-fit,minmax(32px,48px)); gap:5px }
.group-block { display:grid; justify-items:center; gap:5px; padding:6px 2px; min-width:0; border:1px solid #d3e3ef; border-radius:6px; font-size:10px; background:#f0f4f8; line-height:1.2; color:#152852 }
.group-block-symbol { display:grid; place-items:center; min-height:17px; font-size:13px }
.group-block.executed { background:linear-gradient(#edfbfc,#e6f6f5) }
.group-block.executed .group-block-symbol { color:#008891 }
.group-block.reused { background:linear-gradient(#f4f7fb,#e7faf2) }
.group-block.reused .group-block-symbol { background:#adf0cc; border-radius:50%; color:#087853; width:17px }
.group-block.problem { background:#fff0f4; border-color:#f0c8d4; color:var(--red) }
.group-block.pending { background:#f8fafc; border-style:dashed; color:#8093ad }
.baseline-row { display:flex; gap:10px; align-items:center }
.baseline-row .row-label { flex-shrink:0; display:block; margin:0; font-size:9px }
.baseline-row .row-label b { margin-left:6px }
.baseline-row .group-blocks { flex:1 }
.baseline-row .group-block { min-height:15px; padding:2px; background:#f1f5f8; color:#678299; font-size:8px }
.block-legend { display:flex; flex-wrap:wrap; gap:6px 13px; color:#48638c; font-size:9px; margin:8px 0 4px }
#blockScope { font-size:9px; margin:0 }
.execution-comparison { border-top:1px solid var(--line); padding-top:14px; margin-top:16px }
.section-heading { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:10px }
.section-heading .pill { font-size:9px; padding:2px 7px }
.comparison-values { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)) }
.comparison-values p { display:grid; gap:5px; padding:0 12px; border-left:1px solid var(--line); margin:0 }
.comparison-values p:first-child { padding-left:0; border:0 }
.comparison-values p:last-child { padding-right:0 }
.comparison-values span { font-size:10px; color:var(--muted) }
.comparison-values strong { font-size:16px; font-weight:650; color:var(--green) }
.comparison-values p:first-child strong { color:#344778 }
.axis { display:grid; gap:7px; margin-top:12px }
.axis>div { display:grid; grid-template-columns:105px minmax(0,1fr); align-items:center; gap:10px }
.axis>div>span { font-size:9px; color:var(--muted) }
.track { height:14px; background:#e2f1f3; overflow:hidden; border-radius:12px }
.current-track { display:flex }
.bar { display:block; height:100%; flex-shrink:0 }
.bar.full { background:#d7e4ec }
.bar.executed { background:linear-gradient(105deg,#05a7d7,#00998e) }
.bar.avoided { border:1px solid #a4dfbf; background:repeating-linear-gradient(130deg,#def8e9,#def8e9 4px,#bde9cd 4px,#bde9cd 5px) }
.avoided-label { color:var(--green); font-size:10px; text-align:right; margin:7px 0 0 }
.state-guidance { color:var(--muted); font-size:9px; line-height:1.6; margin:7px 0 }
.text-link { display:inline-flex; align-items:center; gap:8px; padding:0; border:0; border-radius:0; background:none; font-size:11px; color:var(--teal); text-decoration:none }
.text-link .ui-icon { width:16px; height:16px }
.text-link:disabled { cursor:default; color:#6c8293 }
.work-grid { display:grid; grid-template-columns:minmax(0,1.2fr) minmax(0,1fr); gap:14px; margin-top:14px; align-items:start }
.work-grid .panel-title { margin-bottom:10px }
.work-grid h2 { font-size:18px }
.count { display:grid; place-items:center; min-width:25px; height:25px; border-radius:8px; background:#e9f6f4; color:var(--teal); font-size:11px }
.source-toolbar { display:flex; gap:10px; align-items:center; justify-content:space-between; margin-bottom:8px }
.source-filters { display:flex; gap:3px }
.source-filters button { font-size:10px; padding:4px 9px; border:1px solid transparent; background:transparent }
.source-filters button[aria-pressed=true] { color:var(--teal); border-color:#c4e5e8; background:#eaf8f7 }
#filterStatus { font-size:9px; margin:0 }
.source-scroll { overflow:auto; max-height:166px; border-radius:6px }
.source-table { width:100%; border-collapse:collapse; text-align:left; font-size:11px }
.source-table th { padding:6px 10px; color:var(--muted); background:#f0f6f8; font-size:10px; font-weight:500; white-space:nowrap; position:sticky; top:0; z-index:1 }
.source-table td { padding:6px 10px; border-bottom:1px solid #e5eef4; color:#3f6088; white-space:nowrap }
.source-table td:first-child { max-width:190px; white-space:normal; overflow-wrap:anywhere }
.source { border:0; border-radius:3px; padding:2px 0; background:transparent; color:#1b3f69; font-size:11px; text-align:left }
.source.active { color:var(--teal); font-weight:650 }
.source-row:has(.source.active) { background:#f2fcf9 }
.source-table .pill { padding:0; background:transparent; font-size:10px; white-space:nowrap }
.source-table .pill.reused { color:var(--green) }
.source-table .pill.rerun { color:var(--red) }
#reuseOrigins { font-size:9px; margin:9px 0 0 }
.batch-flow { padding:0; margin:13px 0 7px; list-style:none }
.batch-flow li { position:relative; border-left:1px solid #b9dfdf; padding:0 0 13px 23px; margin-left:10px }
.batch-flow li:last-child { padding-bottom:0; border-color:transparent }
.batch-flow li:before { content:'↻'; position:absolute; left:-9px; top:1px; display:grid; place-items:center; width:18px; height:18px; border-radius:50%; background:linear-gradient(120deg,#17bdb4,#008c9b); box-shadow:0 0 0 4px #e5f6f4; color:white; font-size:12px }
.batch-flow button { display:grid; grid-template-columns:44px 1fr; gap:1px 10px; width:100%; padding:0; border:0; background:transparent; text-align:left; font-size:11px }
.batch-flow time { color:var(--muted); grid-row:1/3; font-variant-numeric:tabular-nums }
.batch-flow b { color:#233e6c; font-size:12px; font-weight:600 }
.batch-flow small { color:var(--muted); font-size:10px }
.batch-flow button[aria-pressed=true] b { color:var(--teal) }
.timeline label,.timeline select { font-size:11px }
.timeline select { width:100%; margin:5px 0 8px }
.history-details { margin-top:12px }
.history-details>summary { padding:0; font-size:11px }
.retained-impact { border-radius:10px; background:#edf9f4; padding:12px; margin-top:12px }
.retained-impact strong { font-size:20px; color:var(--green) }
.retained-impact p { font-size:10px; margin:5px 0 0 }
#historyMeta { font-size:10px; margin-top:12px }
.explanation { margin-top:12px; padding:12px 18px; font-size:12px }
.explanation>summary { padding:0; font-size:12px }
.explanation h2 { font-size:16px; margin-bottom:8px }
.explanation p { font-size:12px; color:var(--muted); line-height:1.7 }
summary { padding:10px 0; color:var(--teal) }
details[open]>summary { margin-bottom:12px }
.tag { display:inline-block; background:#f1f6fa; padding:5px 8px; border-radius:5px; color:var(--muted); font-size:11px; margin:4px 5px 0 0; max-width:100%; overflow-wrap:anywhere }
.lineage ol { color:var(--muted); padding-left:20px; line-height:1.8 }
.dashboard-note { display:flex; align-items:center; gap:12px; border-radius:12px; background:rgba(255,255,255,.64); padding:8px 14px; margin:12px 0; font-size:12px; font-weight:600; color:#214c75 }
.dashboard-note .ui-icon { color:var(--teal) }
.note-caption { font-weight:400; color:var(--muted); font-size:10px; margin-left:auto }
.measurement-details { margin-top:12px; padding:14px 18px }
.measurement-details>summary { padding:0; font-size:14px }
.measurement-details>summary>span { display:inline-grid; gap:3px }
.measurement-details small { font-size:10px; color:var(--muted); font-weight:400 }
.basis { display:flex; flex-wrap:wrap; gap:6px 14px; color:var(--muted); font-size:10px; margin:12px 0 }
.metric-split { display:grid; grid-template-columns:1fr 1fr; gap:18px }
.setup-costs { grid-column:1/-1 }
.compact { padding:8px 0 }
.statline { display:flex; justify-content:space-between; gap:14px; padding:9px 0; border-top:1px solid var(--line); color:var(--muted); font-size:11px }
.statline strong { text-align:right; font-weight:550; color:var(--text) }
.telemetry,.comparison { margin-top:16px; padding-top:14px; border-top:1px solid var(--line) }
.comparison p,.comparison label { font-size:12px }
.comparison h2 { font-size:18px; margin-bottom:12px }
.comparison pre { padding:14px; background:#f0f6f8; border-radius:8px; font-size:11px; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere }
.comparison-row { padding:12px 0; border-top:1px solid var(--line) }
.comparison-row h3 { font-size:13px }
.comparison-row p { color:var(--muted); font-size:11px }
.comparison-row p.slower { color:var(--red) }
.comparison-bar { height:13px; margin:7px 0; border-radius:4px; background:#b8cbdf }
.comparison-bar.incremental { background:var(--teal) }
.map-panel { overflow:hidden }
#map { width:100%; height:440px; display:none }
.empty { padding:25px; text-align:center; color:var(--muted); font-size:12px }
.edge { stroke:#a8bfcc; stroke-width:1.2 }
.node text { fill:var(--text); font-size:11px }
.node rect { fill:#eff8f5; stroke:#a5c9c2; rx:9 }
.node.source rect { fill:#e4f6f7; stroke:var(--blue) }
.node.changed rect { fill:#fff0f3; stroke:var(--red) }
.node.baseline-only rect { fill:#faf3e3; stroke:var(--amber) }
.node.newly-observed rect { fill:#f3efff; stroke:var(--violet) }
.exports { margin-top:12px }
.exports>p { margin-top:7px }
.exports textarea { width:100%; resize:vertical; min-height:120px; border:1px solid var(--line); border-radius:8px; background:#f8fbfe; padding:12px; color:#294c73; font-size:12px; line-height:1.8 }
.export-actions { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-top:12px }
#copySummary { background:var(--teal); border-color:var(--teal); color:white }
#exportStatus { font-size:11px; color:var(--muted) }
footer { display:flex; justify-content:space-between; gap:12px; color:var(--muted); font-size:10px; margin-top:16px }
:lang(en) .hero-value { font-size:clamp(24px,2.1vw,34px); letter-spacing:-.04em }
:lang(zh) h1,:lang(zh) h2,:lang(zh) p { word-break:normal }
@media(max-width:1250px) { .app-shell{grid-template-columns:170px minmax(0,1fr)} .sidebar{padding-inline:10px} main{padding-inline:17px} .panel{padding:17px} .topbar h1{font-size:26px} .top-actions{gap:10px} .headline-card{padding:11px} .hero-value{font-size:28px} .metric-heading{gap:7px} .metric-icon{width:27px;height:27px} .metric-heading h2{font-size:13px} .metrics article{padding-inline:10px} .nav-link{font-size:12px} }
@media(max-width:1050px) { .impact-grid{grid-template-columns:1fr} .topbar{flex-direction:column;gap:12px} .top-actions{align-self:flex-end} .hero-value{font-size:38px} .headline-card{padding:16px} .headline-metrics{gap:14px} .work-grid{grid-template-columns:1fr} .metric-heading h2{font-size:16px} .metric-icon{width:32px;height:32px} .note-caption{display:none} }
@media(max-width:760px) { body{padding:9px} .app-shell{grid-template-columns:1fr;border-radius:17px} .sidebar{position:relative;padding:14px 13px 9px;border-right:0;border-bottom:1px solid #d1e6e6} .brand{font-size:29px;margin:0 0 16px;padding:0;gap:8px} .mark{width:29px;height:33px;font-size:25px;border-radius:10px} .mark span{font-size:13px;top:-4px} .sidebar nav{display:flex;gap:4px;overflow:auto} .nav-link{white-space:nowrap;min-height:39px;padding:9px 10px;font-size:11px;gap:7px;border-radius:8px} .nav-link .ui-icon{width:16px;height:16px} .nav-caption,.sidebar-note{display:none} .sidebar-bottom{display:none} .language-picker{width:100%;min-height:44px;gap:9px} .language-picker select{margin-left:auto;min-width:128px;font-size:13px} main{padding:18px 12px 12px} .topbar{gap:10px;margin-bottom:12px} .topbar h1{font-size:25px;max-width:100%;line-height:1.4} .topbar .eyebrow{font-size:8px} .page-subtitle{font-size:11px;margin-top:6px} .top-actions{align-self:stretch;justify-content:flex-end;gap:12px} .top-actions .live{margin-right:auto;font-size:10px} .icon-button{width:32px;height:32px;font-size:23px} .primary-link{padding:8px 12px;font-size:11px;border-radius:8px} .panel{padding:16px;border-radius:14px} .task-summary{grid-template-columns:34px minmax(0,1fr);gap:0 9px;padding:12px;margin-bottom:12px} .task-icon{width:34px;height:38px;border-radius:9px} .task-icon .ui-icon{width:21px;height:21px} .task-identity h2{font-size:13px} .task-identity .eyebrow{font-size:9px} #taskline{font-size:10px} .task-meta{grid-column:1/-1;grid-row:3;display:flex;justify-content:space-between;gap:10px;align-items:center;margin-top:10px;padding-top:8px;border-top:1px solid #dbe9ec} .task-meta time{font-size:9px} #approvalState{font-size:9px} .task-summary details[open]{grid-column:1/-1} .panel-title{margin-bottom:13px} .panel-title h2{font-size:18px} .panel-subtitle{font-size:11px} .impact-grid{gap:12px} .headline-metrics{grid-template-columns:1fr;gap:10px} .headline-card{padding:13px 14px} .metric-heading h2{font-size:15px} .hero-number{margin:8px 0 5px} .hero-value,:lang(en) .hero-value{font-size:34px} .hero-status{font-size:10px} .savings-breakdown{margin-top:13px} .breakdown-row{font-size:11px} .task-effect-value{font-size:19px} .truth-note{font-size:9px} .metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 0;margin-bottom:14px} .metrics article:nth-child(3){border:0;padding-left:0} .metrics strong,.metric-link{font-size:28px} .baseline-row{display:block} .baseline-row .row-label{margin-bottom:5px} .group-blocks{grid-template-columns:repeat(auto-fit,minmax(31px,1fr))} .comparison-values{grid-template-columns:1fr;gap:8px} .comparison-values p{grid-template-columns:1fr 1fr;padding:0;border:0;align-items:center} .comparison-values strong{text-align:right;font-size:15px} .axis>div{grid-template-columns:87px minmax(0,1fr);gap:7px} .axis>div>span{font-size:8px} .track{height:12px} .work-grid{gap:12px;margin-top:12px} .source-table{min-width:465px} .source-scroll{max-height:235px} #filterStatus{font-size:8px} .source-filters button{font-size:10px;padding:4px 8px} .dashboard-note{font-size:11px;gap:9px;padding:10px} .dashboard-note .ui-icon{width:19px} .explanation{padding:12px 15px} .metric-split{grid-template-columns:1fr;gap:12px} .measurement-details{padding:13px 15px} .statline{font-size:10px} #contractPromises{display:block} .exports textarea{font-size:11px} .export-actions{gap:7px} .export-actions button{min-height:38px;font-size:10px} footer{flex-wrap:wrap;font-size:9px} }
@media(prefers-reduced-motion:reduce) { html{scroll-behavior:auto} *,*:before,*:after{animation:none!important;transition:none!important} }
@media(max-width:760px) { .sidebar{top:auto;height:auto;overflow:visible} }

"""

JS = r"""(() => {
  'use strict';
  const MESSAGES = Object.freeze({
  "Click Incremental Verification": [
    "Click Incremental Verification",
    "Click 增量验证"
  ],
  "대시보드로 건너뛰기": [
    "Skip to dashboard",
    "跳转到仪表盘"
  ],
  "주 탐색": [
    "Main navigation",
    "主导航"
  ],
  "WORKSPACE": [
    "WORKSPACE",
    "工作区"
  ],
  "대시보드": [
    "Dashboard",
    "仪表盘"
  ],
  "검증 묶음": [
    "Check groups",
    "验证组"
  ],
  "작업 이력": [
    "Task history",
    "任务历史"
  ],
  "측정 상세": [
    "Measurement details",
    "测量详情"
  ],
  "공유 리포트": [
    "Share report",
    "分享报告"
  ],
  "필요한 검증만, 다시.": [
    "Rerun only what is needed.",
    "只重新运行必要的验证。"
  ],
  "실행한 결과와 재사용한 근거를 한눈에 확인하세요.": [
    "See what ran and why results were reused.",
    "一览运行结果与复用依据。"
  ],
  "● 로컬 · 읽기 전용": [
    "● Local · Read only",
    "● 本地 · 只读"
  ],
  "Click workspace": [
    "Click workspace",
    "Click 工作区"
  ],
  "검증 기록": [
    "Verification history",
    "验证记录"
  ],
  "LESS REPETITION. MORE PROGRESS.": [
    "LESS REPETITION. MORE PROGRESS.",
    "减少重复，推进工作。"
  ],
  "검증 대시보드": [
    "Verification dashboard",
    "验证仪表盘"
  ],
  "언어": [
    "Language",
    "语言"
  ],
  "연결 중": [
    "Connecting",
    "连接中"
  ],
  "기록 새로고침": [
    "Refresh records",
    "刷新记录"
  ],
  "리포트 공유": [
    "Share report",
    "分享报告"
  ],
  "현재 작업과 승인 상태": [
    "Current task and approval status",
    "当前任务与批准状态"
  ],
  "현재 작업": [
    "Current task",
    "当前任务"
  ],
  "현재 Click 상태를 불러오는 중…": [
    "Loading the current Click state…",
    "正在加载 Click 当前状态…"
  ],
  "승인 상태 확인 중": [
    "Checking approval status",
    "正在确认批准状态"
  ],
  "작업 범위와 승인 근거": [
    "Task scope and approval basis",
    "任务范围与批准依据"
  ],
  "이 Viewer와 보관 이력은 승인·실행·재사용 권한을 만들거나 승계하지 않습니다.": [
    "This viewer and its retained history do not grant or transfer approval, execution, or reuse authority.",
    "此查看器及其保留的历史记录不会授予或转移批准、执行或复用权限。"
  ],
  "이번 수정으로 만든 차이": [
    "The difference this change made",
    "本次修改带来的差异"
  ],
  "재사용으로 피한 테스트 재실행 시간": [
    "Test rerun time avoided through reuse",
    "通过复用避免的测试重跑时间"
  ],
  "추정": [
    "Estimated",
    "估算"
  ],
  "과거 성공 실행의 시간 근거를 확인하는 중입니다.": [
    "Checking timing evidence from previous successful runs.",
    "正在检查历史成功运行的计时依据。"
  ],
  "실제 검증 기록을 기다리고 있습니다.": [
    "Waiting for actual verification records.",
    "正在等待实际验证记录。"
  ],
  "어떤 검증을 다시 실행하지 않았나요?": [
    "Which checks did not need to run again?",
    "哪些验证无需重新运行？"
  ],
  "추정 기준": [
    "Estimation basis",
    "估算依据"
  ],
  "과거 실행 기록 기반 추정": [
    "Estimated from previous execution records",
    "基于历史运行记录估算"
  ],
  "동일 묶음·순차 실행 기준": [
    "Same groups · Sequential execution",
    "相同验证组 · 顺序执行"
  ],
  "관리비용 별도": [
    "Overhead reported separately",
    "管理开销单独列示"
  ],
  "테스트 실행 구간의 추정입니다. 전체 요청 대기시간이나 관리비용을 차감한 순절감이 아닙니다.": [
    "An estimate of test execution intervals, not total request wait time or net savings after overhead.",
    "这是测试执行区间的估算，并非请求总等待时间或扣除管理开销后的净节省。"
  ],
  "별도 비교에서 Click 구간 증가가 관측됐습니다. 상세 보기": [
    "A separate comparison measured a longer Click interval. View details",
    "独立对比中观测到 Click 区间更长。查看详情"
  ],
  "가져온 fixture의 해당 비교 결과 · 이번 요청의 실측 결과가 아닙니다.": [
    "The corresponding comparison in the imported fixture · Not a measurement of this request.",
    "结果来自导入的测试场景中的对应对比，并非本次请求的实测结果。"
  ],
  "전체 대비 부분 실행": [
    "Partial execution vs full execution",
    "部分执行与全部执行对比"
  ],
  "전체와 이번 실행, 나란히": [
    "Full scope and this run, side by side",
    "全部范围与本次运行，直观对比"
  ],
  "판정 중": [
    "Evaluating",
    "判定中"
  ],
  "실제 증분 검증 결과": [
    "Actual incremental verification results",
    "实际增量验证结果"
  ],
  "전체 검증 묶음": [
    "Total check groups",
    "验证组总数"
  ],
  "개별 테스트 케이스 수가 아닙니다": [
    "Not the number of individual test cases",
    "不是单个测试用例的数量"
  ],
  "이번에 실제 실행": [
    "Actually run this time",
    "本次实际运行"
  ],
  "실제 시작한 묶음": [
    "Groups that actually started",
    "实际开始运行的验证组"
  ],
  "실제 재사용 적용": [
    "Reuse actually applied",
    "实际应用的复用"
  ],
  "재사용한 검증 묶음 보기": [
    "View reused check groups",
    "查看复用的验证组"
  ],
  "기존 권한 판정을 통과한 묶음": [
    "Groups that passed the existing authority checks",
    "通过现有权限判定的验证组"
  ],
  "시간 근거 커버리지": [
    "Timing evidence coverage",
    "计时依据覆盖率"
  ],
  "시간 표본이 적합한 재사용 묶음": [
    "Reused groups with suitable timing samples",
    "有适用计时样本的复用验证组"
  ],
  "전체 재실행 기준": [
    "Full rerun reference",
    "全部重跑基准"
  ],
  "전체 묶음을 실행하는 비교 기준": [
    "Reference assuming all groups run",
    "假设全部验证组运行的对比基准"
  ],
  "이번 실제 결과": [
    "Actual results this time",
    "本次实际结果"
  ],
  "묶음별 실제 실행과 재사용 결과": [
    "Actual execution and reuse by group",
    "各验证组的实际运行与复用结果"
  ],
  "● 실행": [
    "● Executed",
    "● 已运行"
  ],
  "↺ 재사용": [
    "↺ Reused",
    "↺ 已复用"
  ],
  "! 실패·중단": [
    "! Failed / interrupted",
    "! 失败／中断"
  ],
  "· 미실행·미확정": [
    "· Not run / unconfirmed",
    "· 未运行／未确认"
  ],
  "같은 묶음을 모두 실행하는 기준이며, 관찰한 이전 실행이 아닙니다.": [
    "A reference for running all the same groups, not an observed previous run.",
    "这是运行全部相同验证组的基准，并非观测到的历史运行。"
  ],
  "동일 묶음 전체 실행 예상": [
    "Estimated full execution of the same groups",
    "相同验证组的全部运行估算"
  ],
  "이번 테스트 실행": [
    "Test execution this time",
    "本次测试运行"
  ],
  "명령 실행 구간 감소": [
    "Command execution interval reduction",
    "命令执行区间缩短"
  ],
  "동일 시간 축 비교": [
    "Comparison on the same time axis",
    "同一时间轴上的对比"
  ],
  "전체 순차 실행 예상": [
    "Estimated full sequential run",
    "全部顺序运行估算"
  ],
  "완료된 검증의 시간 근거를 기다리고 있습니다.": [
    "Waiting for timing evidence from completed checks.",
    "正在等待已完成验证的计时依据。"
  ],
  "재사용 근거 · 배치 선택": [
    "Reuse evidence · Batch selection",
    "复用依据 · 批次选择"
  ],
  "최근 작업 흐름": [
    "Recent task flow",
    "近期任务流程"
  ],
  "최신 배치": [
    "Latest batch",
    "最新批次"
  ],
  "상세 결과 선택": [
    "Select detailed results",
    "选择详细结果"
  ],
  "보관된 완료 요청": [
    "Retained completed requests",
    "保留的已完成请求"
  ],
  "묶음별 근거": [
    "Evidence by group",
    "各验证组的依据"
  ],
  "실행·재사용 결과와 이유": [
    "Execution, reuse and their reasons",
    "运行、复用及其原因"
  ],
  "검증 결과 필터": [
    "Filter verification results",
    "筛选验证结果"
  ],
  "전체": [
    "All",
    "全部"
  ],
  "재사용": [
    "Reused",
    "复用"
  ],
  "실제 실행": [
    "Executed",
    "实际运行"
  ],
  "선택한 묶음": [
    "Selected group",
    "选中的验证组"
  ],
  "묶음을 선택하세요": [
    "Select a check group",
    "请选择验证组"
  ],
  "실제 판정 결과와 시간 근거를 설명합니다.": [
    "Actual decisions and their timing evidence are explained here.",
    "这里显示实际判定结果与计时依据。"
  ],
  "실행·재사용 계보": [
    "Execution and reuse lineage",
    "运行与复用的来源链"
  ],
  "원시 ID·revision·측정 결합 상세": [
    "Raw IDs, revisions and measurement bindings",
    "原始 ID、修订及测量绑定详情"
  ],
  "요청·처리 구간, 관리비용, 비교 실측, 원시 조건": [
    "Request and processing intervals, overhead, measured comparisons and raw conditions",
    "请求与处理区间、管理开销、实测对比及原始条件"
  ],
  "현재 요청에서 관측한 구간": [
    "Intervals observed in this request",
    "本次请求中观测到的区间"
  ],
  "Hook 진입 → 결과 기록 · 부분 요청시간": [
    "Hook entry → Result recording · Partial request time",
    "Hook 进入 → 结果记录 · 部分请求时间"
  ],
  "현재 측정 가능한 처리 구간": [
    "Currently measurable processing intervals",
    "当前可测量的处理区间"
  ],
  "이번 source-command 실행 구간": [
    "Source-command interval this time",
    "本次 source-command 执行区间"
  ],
  "동일 묶음 전체 순차 실행 추정": [
    "Estimated full sequential execution of the same groups",
    "相同验证组全部顺序执行的估算"
  ],
  "호스트 요청 전·최종 반환은 계측 범위 밖입니다.": [
    "Time before the host request and the final return are outside the measurement scope.",
    "主机请求之前及最终返回不在计时范围内。"
  ],
  "관리비용과 순 요청시간": [
    "Overhead and net request time",
    "管理开销与请求净时间"
  ],
  "Click 전체 관리비용": [
    "Total Click overhead",
    "Click 总管理开销"
  ],
  "측정 정보 없음": [
    "Not measured",
    "未测量"
  ],
  "포함 관계가 있는 요청시간과 검사시간을 빼서 관리비용을 만들지 않습니다.": [
    "Overlapping request and check intervals are not subtracted to invent overhead.",
    "不会通过相减有包含关系的请求与验证区间来推算管理开销。"
  ],
  "비교 환경의 순 요청 시간 차이": [
    "Net request time difference in the comparison environment",
    "对比环境中的请求净时间差"
  ],
  "동등한 paired 비교 실측이 없습니다.": [
    "No equivalent paired comparison has been measured.",
    "没有等价的配对实测对比。"
  ],
  "Observer 후보 / 확인 / 모순": [
    "Observer candidates / confirmed / contradicted",
    "Observer 候选／已确认／矛盾"
  ],
  "잠재 시간 / Observer 보조 처리시간": [
    "Potential time / Observer auxiliary processing",
    "潜在时间／Observer 辅助处理时间"
  ],
  "추적으로 인한 검사 지연은 별도 측정하지 않았습니다.": [
    "Test slowdown caused by tracing was not measured separately.",
    "未单独测量跟踪导致的测试延迟。"
  ],
  "최초 설정과 기준 실행": [
    "Initial setup and baseline run",
    "初始设置与基准运行"
  ],
  "설정 상태": [
    "Setup status",
    "设置状态"
  ],
  "미설정": [
    "Not configured",
    "未配置"
  ],
  "초기 설정 비용": [
    "Initial setup cost",
    "初始设置成本"
  ],
  "Observer 비용": [
    "Observer cost",
    "Observer 成本"
  ],
  "Click 처리": [
    "Click processing",
    "Click 处理"
  ],
  "parent 전체 실행 / 순차 shards": [
    "Full parent run / Sequential shards",
    "完整 parent 运行／顺序 shards"
  ],
  "parent − 순차 shards": [
    "Parent − Sequential shards",
    "parent − 顺序 shards"
  ],
  "첫 기준 실행은 절감 시간으로 집계하지 않습니다.": [
    "The first baseline run is not counted as time saved.",
    "首次基准运行不计入节省时间。"
  ],
  "Evidence Map · Shadow 관찰 상세 · 재사용 권한 없음": [
    "Evidence Map · Shadow observation details · No reuse authority",
    "Evidence Map · Shadow 观测详情 · 无复用权限"
  ],
  "Observer 확인 중": [
    "Checking Observer",
    "正在检查 Observer"
  ],
  "Dashboard와 Observer는 독립적입니다.": [
    "The dashboard and Observer are independent.",
    "仪表盘与 Observer 相互独立。"
  ],
  "선택한 묶음의 관찰된 입력": [
    "Observed inputs for the selected group",
    "所选验证组中观测到的输入"
  ],
  "묶음을 선택하면 현재 입력과 이전 baseline의 관계를 보여줍니다.": [
    "Select a group to see its current inputs and previous baseline.",
    "选择验证组可查看当前输入与历史基准的关系。"
  ],
  "선택한 묶음의 Evidence Map": [
    "Evidence Map for the selected group",
    "所选验证组的 Evidence Map"
  ],
  "명시적으로 실행한 paired 비교 · 일상 추정치와 별개": [
    "Explicitly run paired comparison · Separate from everyday estimates",
    "显式运行的配对对比 · 与日常估算分开"
  ],
  "전체 재실행 기준 vs Click 부분 검증": [
    "Full rerun reference vs Click partial verification",
    "全部重跑基准与 Click 部分验证对比"
  ],
  "아직 비교 측정이 없습니다. 아래 명령으로 별도 측정한 JSON만 가져올 수 있습니다. 가져온 결과는 승인·재사용 권한이 아닙니다.": [
    "No comparison has been measured yet. Import only JSON measured separately with the command below. Imported results do not grant approval or reuse authority.",
    "尚无对比测量。仅可导入使用下方命令单独测量的 JSON。导入结果不授予批准或复用权限。"
  ],
  "비교 JSON 선택": [
    "Choose comparison JSON",
    "选择对比 JSON"
  ],
  "비교 자료 제거": [
    "Remove comparison",
    "移除对比数据"
  ],
  "원시 측정 조건과 상태 사유": [
    "Raw measurement conditions and status reasons",
    "原始测量条件与状态原因"
  ],
  "비교 실측이 없는 live 요청은 순절감을 계산하지 않습니다. 가져온 보고서와 Viewer는 실행 권한을 만들지 않습니다.": [
    "Net savings are not calculated for live requests without a measured comparison. Imported reports and this viewer do not grant execution authority.",
    "没有实测对比的实时请求不计算净节省。导入报告和此查看器不授予执行权限。"
  ],
  "현재 선택한 배치와 가져온 비교 측정을 내보냅니다. 파일 경로·원시 명령·환경 값·토큰은 제외합니다. 검증은 실행하지 않습니다.": [
    "Export the selected batch and imported measurements. File paths, raw commands, environment values and tokens are excluded. No checks are run.",
    "导出选中批次和导入的测量数据。不包含文件路径、原始命令、环境值和令牌，也不会运行验证。"
  ],
  "공유용 요약 문구": [
    "Summary for sharing",
    "用于分享的摘要"
  ],
  "요약 문구 복사": [
    "Copy summary",
    "复制摘要"
  ],
  "JSON 내보내기": [
    "Export JSON",
    "导出 JSON"
  ],
  "독립형 HTML 내보내기": [
    "Export standalone HTML",
    "导出独立 HTML"
  ],
  "로컬 전용 · 읽기 전용 · 파일 내용 미노출": [
    "Local only · Read only · No file contents exposed",
    "仅限本地 · 只读 · 不暴露文件内容"
  ],
  "아직 갱신되지 않음": [
    "Not updated yet",
    "尚未更新"
  ],
  "현재 코드에서 직접 통과했습니다. 다음 관련 변경 전에는 재실행이 필요하지 않습니다.": [
    "Passed on the current code. No rerun is needed until the next relevant change.",
    "已在当前代码上直接通过。在下次相关修改前无需重新运行。"
  ],
  "현재 재판정을 통과한 결과입니다. 관련 입력이 바뀌면 다시 검증하세요.": [
    "This result passed the current reevaluation. Recheck when relevant inputs change.",
    "此结果已通过当前重新判定。相关输入变化时请重新验证。"
  ],
  "실패 또는 중단 원인을 확인한 뒤 승인 범위에서 같은 검증을 다시 실행하세요.": [
    "Investigate the failure or interruption, then rerun the same check within the approved scope.",
    "查明失败或中断原因后，在批准范围内重新运行相同验证。"
  ],
  "현재 환경에서 실제 검증을 실행해 새 기준 결과를 만드세요.": [
    "Run the actual check in the current environment to establish a new baseline.",
    "请在当前环境中实际运行验证，建立新基准。"
  ],
  "관련 입력이 변경됐습니다. 현재 코드로 실제 검증을 실행하세요.": [
    "Relevant inputs changed. Run the actual check on the current code.",
    "相关输入已变化。请使用当前代码实际运行验证。"
  ],
  "재사용 근거가 부족합니다. 정책을 완화하지 말고 현재 상태를 실제 검증하세요.": [
    "Reuse evidence is insufficient. Verify the current state without weakening the policy.",
    "复用依据不足。请实际验证当前状态，不要放宽策略。"
  ],
  "승인된 검증을 먼저 실행해 현재 상태의 성공 기준을 만드세요.": [
    "Run the approved checks first to establish a passing baseline for the current state.",
    "请先运行已批准的验证，为当前状态建立通过基准。"
  ],
  "이전 Evidence 작업": [
    "Previous Evidence task",
    "之前的 Evidence 任务"
  ],
  "현재 계약": [
    "Current contract",
    "当前契约"
  ],
  "동일 묶음 전체 순차 실행 예상": [
    "Estimated full sequential execution of the same groups",
    "相同验证组的全部顺序运行估算"
  ],
  "테스트 명령 실행 구간 감소": [
    "Test command execution interval reduction",
    "测试命令执行区间缩短"
  ],
  "{0}초": [
    "{0}s",
    "{0}秒"
  ],
  "{0}분 {1}초": [
    "{0}m {1}s",
    "{0}分{1}秒"
  ],
  "{0}분": [
    "{0}m",
    "{0}分"
  ],
  "약 {0}": [
    "About {0}",
    "约{0}"
  ],
  "약 {0}%": [
    "About {0}%",
    "约{0}%"
  ],
  "알 수 없음": [
    "Unknown",
    "未知"
  ],
  "실행 예정": [
    "Planned execution",
    "计划运行"
  ],
  "재사용 예정 · 미적용": [
    "Planned reuse · Not applied",
    "计划复用 · 尚未应用"
  ],
  "실행 중": [
    "Running",
    "运行中"
  ],
  "통과": [
    "Passed",
    "通过"
  ],
  "실패": [
    "Failed",
    "失败"
  ],
  "중단 · 일부 결과 미확정": [
    "Interrupted · Some outcomes unconfirmed",
    "已中断 · 部分结果未确认"
  ],
  "미실행": [
    "Not run",
    "未运行"
  ],
  "재사용 적용": [
    "Reuse applied",
    "已应用复用"
  ],
  "실행 전 거부": [
    "Rejected before execution",
    "执行前被拒绝"
  ],
  "미확정": [
    "Unconfirmed",
    "未确认"
  ],
  "승인 대기": [
    "Awaiting approval",
    "等待批准"
  ],
  "승인됨": [
    "Approved",
    "已批准"
  ],
  "활성 작업 없음": [
    "No active task",
    "无活动任务"
  ],
  "명령 선택 필요": [
    "Command selection required",
    "需要选择命令"
  ],
  "검토 필요": [
    "Review required",
    "需要审核"
  ],
  "커밋 필요": [
    "Commit required",
    "需要提交"
  ],
  "기준 실행 필요": [
    "Baseline run required",
    "需要基准运行"
  ],
  "샤딩 준비됨 · 재사용 불가": [
    "Sharding ready · Reuse unavailable",
    "分片已就绪 · 不可复用"
  ],
  "샤딩·재사용 준비됨": [
    "Sharding and reuse ready",
    "分片与复用已就绪"
  ],
  "미지원": [
    "Unsupported",
    "不支持"
  ],
  "차단됨": [
    "Blocked",
    "已阻止"
  ],
  "요청이 거부되어 시작하지 않았습니다.": [
    "The request was rejected and did not start.",
    "请求被拒绝，未开始运行。"
  ],
  "실행 전 안전 조건을 통과하지 못해 시작하지 않았습니다.": [
    "Pre-execution safety conditions were not met, so the check did not start.",
    "未满足执行前的安全条件，验证未开始。"
  ],
  "앞선 검사 실패나 중단 때문에 시작하지 않았습니다.": [
    "The check did not start because a preceding check failed or was interrupted.",
    "前一项验证失败或中断，因此本项未开始。"
  ],
  "검증 중 코드가 변경되어 결과를 현재 상태에 사용할 수 없습니다.": [
    "Code changed during verification, so the result cannot be used for the current state.",
    "验证期间代码发生变化，结果无法用于当前状态。"
  ],
  "실행이 시작되기 전에 요청이 만료되었습니다.": [
    "The request expired before execution started.",
    "请求在开始执行前已过期。"
  ],
  "사용자가 취소했습니다. 실행 중이던 검사의 종료 여부는 미확정입니다.": [
    "Cancelled by the user. The final outcome of the running check is unconfirmed.",
    "用户已取消。运行中验证的最终结果尚未确认。"
  ],
  "실행 경계에서 오류가 발생했습니다. 같은 검사를 다시 실행하지 않았습니다.": [
    "An error occurred at the execution boundary. The same check was not rerun.",
    "执行边界发生错误。未重新运行相同验证。"
  ],
  "검사 실행이 중단되었습니다.": [
    "Check execution was interrupted.",
    "验证执行已中断。"
  ],
  "종료 결과를 확인하지 못했습니다.": [
    "The final outcome could not be confirmed.",
    "无法确认最终结果。"
  ],
  "아직 요청되지 않은 검증입니다. 승인 후 기준 검증을 실행하세요.": [
    "This check has not been requested yet. Run baseline verification after approval.",
    "尚未请求此验证。批准后请运行基准验证。"
  ],
  "재실행": [
    "Rerun",
    "重新运行"
  ],
  "대기": [
    "Pending",
    "等待中"
  ],
  "같은 revision의 검사 결과가 현재 작업트리와 정확히 일치해 재사용했습니다.": [
    "Reused the same-revision result because it exactly matches the current working tree.",
    "同一修订的验证结果与当前工作树完全匹配，因此已复用。"
  ],
  "이전 작업의 실제 통과 결과를 현재 명령·작업트리·환경·실행 파일·호스트 범위에 다시 결합해 재사용했습니다.": [
    "Reused an actual pass from a previous task after rebinding it to the current command, working tree, environment, executable and host coverage.",
    "将之前任务的实际通过结果重新绑定到当前命令、工作树、环境、可执行文件与主机覆盖范围后，已复用。"
  ],
  "이전 작업의 실제 통과 결과를 가져와, 현재 변경 뒤에도 관찰된 입력이 바뀌지 않았음을 다시 확인해 재사용했습니다.": [
    "Reused an actual pass from a previous task after confirming that observed inputs remained unchanged after this change.",
    "确认本次修改后观测到的输入仍未变化，因此复用了之前任务的实际通过结果。"
  ],
  "이전 작업의 실제 통과 결과를 가져와, 사전에 커밋된 안전 변경 정책이 이번 변경을 허용하는지 다시 확인해 재사용했습니다.": [
    "Reused an actual pass from a previous task after confirming that the previously committed safe-change policy covers this change.",
    "确认预先提交的安全变更策略覆盖本次修改后，复用了之前任务的实际通过结果。"
  ],
  "이전 결과가 현재 호스트 세션과 작업 공간의 후속 작업 범위에 속하지 않아 실제 검사를 실행했습니다.": [
    "Ran the check because the previous result is outside the successor-task scope of this host session and workspace.",
    "之前结果不在当前主机会话与工作区的后续任务范围内，因此实际运行了验证。"
  ],
  "이전 실행 사실의 무결성이나 출처를 확인할 수 없어 실제 검사를 실행했습니다.": [
    "Ran the check because the integrity or origin of the previous execution could not be confirmed.",
    "无法确认之前运行记录的完整性或来源，因此实际运行了验证。"
  ],
  "이 검사가 실제로 읽었던 입력이 바뀌지 않아 이전 통과 결과를 재사용했습니다.": [
    "Reused the previous pass because the inputs this check actually read have not changed.",
    "此验证实际读取的输入未变化，因此复用了之前的通过结果。"
  ],
  "저장소 소유자가 미리 허용한 안전 변경 범위 안이라 이전 결과를 재사용했습니다.": [
    "Reused the previous result because the change is within the safe-change scope permitted in advance by the repository owner.",
    "修改位于仓库所有者预先允许的安全变更范围内，因此复用了之前结果。"
  ],
  "재사용할 수 있는 이전 통과 결과가 없어 실제 검사를 실행했습니다.": [
    "Ran the check because no previous passing result was available for reuse.",
    "没有可复用的历史通过结果，因此实际运行了验证。"
  ],
  "이전 검사가 통과하지 않아 실제 검사를 다시 실행했습니다.": [
    "Reran the check because the previous check did not pass.",
    "之前的验证未通过，因此重新运行了验证。"
  ],
  "이 검사가 읽었던 입력이 변경되어 실제 검사를 다시 실행했습니다.": [
    "Reran the check because an input it read has changed.",
    "验证读取过的输入发生变化，因此重新运行了验证。"
  ],
  "검사 명령의 결합 정보가 달라져 실제 검사를 실행했습니다.": [
    "Ran the check because its command binding changed.",
    "验证命令的绑定信息已变化，因此实际运行了验证。"
  ],
  "승인 계약의 결합 정보가 달라져 실제 검사를 실행했습니다.": [
    "Ran the check because the approval contract binding changed.",
    "批准契约的绑定信息已变化，因此实际运行了验证。"
  ],
  "검사 환경이 달라져 실제 검사를 실행했습니다.": [
    "Ran the check because the verification environment changed.",
    "验证环境已变化，因此实际运行了验证。"
  ],
  "검사 실행 파일이 달라져 실제 검사를 실행했습니다.": [
    "Ran the check because the verification executable changed.",
    "验证的可执行文件已变化，因此实际运行了验证。"
  ],
  "호스트 Hook 관찰 범위가 달라져 실제 검사를 실행했습니다.": [
    "Ran the check because the host Hook observation coverage changed.",
    "主机 Hook 观测覆盖范围已变化，因此实际运行了验证。"
  ],
  "현재 작업트리를 확실히 식별할 수 없어 안전하게 실제 검사를 실행했습니다.": [
    "Ran the check because the current working tree could not be identified reliably.",
    "无法可靠识别当前工作树，因此实际运行了验证。"
  ],
  "변경 전후 경계를 확실히 묶을 수 없어 실제 검사를 실행했습니다.": [
    "Ran the check because the boundaries before and after the change could not be reliably bound.",
    "无法可靠绑定修改前后的边界，因此实际运行了验证。"
  ],
  "의존성 관찰이 완전하지 않아 실제 검사를 실행했습니다.": [
    "Ran the check because dependency observation was incomplete.",
    "依赖观测不完整，因此实际运行了验证。"
  ],
  "저장소 밖 입력이 관찰되어 실제 검사를 실행했습니다.": [
    "Ran the check because inputs outside the repository were observed.",
    "观测到了仓库外部的输入，因此实际运行了验证。"
  ],
  "적용할 수 있는 재사용 정책이 없어 실제 검사를 실행했습니다.": [
    "Ran the check because no applicable reuse policy was available.",
    "没有适用的复用策略，因此实际运行了验证。"
  ],
  "변경이 안전 변경 정책 범위를 벗어나 실제 검사를 실행했습니다.": [
    "Ran the check because the change is outside the safe-change policy scope.",
    "修改超出安全变更策略范围，因此实际运行了验证。"
  ],
  "이전 영수증을 현재 상태에 유효하게 결합할 수 없어 실제 검사를 실행했습니다.": [
    "Ran the check because the previous receipt could not be validly bound to the current state.",
    "无法将之前的凭证有效绑定到当前状态，因此实际运行了验证。"
  ],
  "아직 이 검사에 대한 실행 계획이 없습니다.": [
    "No execution plan exists for this check yet.",
    "此验证尚无执行计划。"
  ],
  "검증 요청의 종료가 아직 확인되지 않았습니다.": [
    "The end of this verification request has not been confirmed.",
    "尚未确认此验证请求已结束。"
  ],
  "검증 요청이 정상 완료되지 않았습니다.": [
    "The verification request did not complete successfully.",
    "验证请求未正常完成。"
  ],
  "요청한 모든 묶음의 최종 상태가 확정되지 않았습니다.": [
    "The final status of every requested group has not been confirmed.",
    "尚未确认所有请求验证组的最终状态。"
  ],
  "실제로 시작한 묶음 중 실행시간이 없는 항목이 있습니다.": [
    "Some groups that actually started have no execution duration.",
    "部分已实际开始运行的验证组缺少执行时间。"
  ],
  "재사용된 묶음 중 과거 성공 실행시간이 없는 항목이 있습니다.": [
    "Some reused groups have no timing sample from a previous successful run.",
    "部分复用的验证组缺少历史成功运行的计时样本。"
  ],
  "구형 시간 기록에는 현재 추정에 필요한 측정 조건이 없습니다.": [
    "Legacy timing records lack the measurement context required for this estimate.",
    "旧版计时记录缺少本次估算所需的测量条件。"
  ],
  "과거 시간 기록의 묶음·검사·측정 조건이 현재 추정과 맞지 않습니다.": [
    "Previous timing records do not match the group, check or measurement conditions of this estimate.",
    "历史计时记录的验证组、检查项或测量条件与本次估算不匹配。"
  ],
  "같은 묶음의 순차 실행으로 비교할 시간 조건이 완전하지 않습니다.": [
    "Timing conditions are incomplete for a sequential comparison of the same groups.",
    "对相同验证组进行顺序执行对比的计时条件不完整。"
  ],
  "전체 실행 예상시간이 0이라 감소율을 계산하지 않습니다.": [
    "The reduction rate is not calculated because the estimated full execution time is zero.",
    "全部运行的预计时间为零，因此不计算缩短比例。"
  ],
  "현재 관찰됨": [
    "Currently observed",
    "当前已观测"
  ],
  "변경됨": [
    "Changed",
    "已变化"
  ],
  "이전 baseline에만 존재": [
    "Only in the previous baseline",
    "仅存在于历史基准中"
  ],
  "현재 새로 관찰됨": [
    "Newly observed",
    "本次新观测到"
  ],
  "기준 실행 대기": [
    "Awaiting baseline",
    "等待基准运行"
  ],
  "재사용할 기준 결과를 만드는 중입니다.": [
    "Establishing baseline results for reuse.",
    "正在建立可复用的基准结果。"
  ],
  "아직 이 작업의 검증 요청 기록이 없습니다.": [
    "This task has no verification request records yet.",
    "此任务尚无验证请求记录。"
  ],
  "실행 준비 중": [
    "Preparing to run",
    "准备运行"
  ],
  "재사용할 기준 결과를 확인하는 중입니다. 예정된 재사용은 실적에 포함하지 않습니다.": [
    "Checking baseline results for reuse. Planned reuse is not counted as an actual result.",
    "正在检查可复用的基准结果。计划复用不计入实际成果。"
  ],
  "{0}개 검증 묶음의 실제 결과를 기다리고 있습니다.": [
    "Waiting for actual results from {0} check groups.",
    "正在等待 {0} 个验证组的实际结果。"
  ],
  "검증 진행 중": [
    "Verification in progress",
    "验证进行中"
  ],
  "아직 요청이 끝나지 않았습니다. 아래는 현재까지 관찰한 결과입니다.": [
    "This request has not finished. The results below are those observed so far.",
    "此请求尚未结束。下方显示目前已观测到的结果。"
  ],
  "실제 시작 {0}개 · 재사용 적용 {1}개 · 요청 {2}개": [
    "Started {0} · Reuse applied {1} · Requested {2}",
    "实际开始 {0} 个 · 已复用 {1} 个 · 请求 {2} 个"
  ],
  "검증 중단": [
    "Verification interrupted",
    "验证已中断"
  ],
  "취소·중단된 요청입니다. 완료되지 않은 묶음은 재사용에 포함하지 않습니다.": [
    "This request was cancelled or interrupted. Unfinished groups are not counted as reused.",
    "此请求已取消或中断。未完成的验证组不计入复用。"
  ],
  "검증 실패": [
    "Verification failed",
    "验证失败"
  ],
  "결과 미확정": [
    "Outcome unconfirmed",
    "结果未确认"
  ],
  "정상 완료되지 않은 요청입니다. 관찰한 상태와 시간만 보존합니다.": [
    "This request did not complete successfully. Only observed states and timings are retained.",
    "此请求未正常完成。仅保留已观测到的状态和时间。"
  ],
  "실제 재사용 없음 · 피한 재실행 비용 없음": [
    "No actual reuse · No rerun cost avoided",
    "无实际复用 · 无避免的重跑成本"
  ],
  "이번에는 {0}개 모두 다시 검증했습니다.": [
    "All {0} groups were verified again this time.",
    "本次重新验证了全部 {0} 个验证组。"
  ],
  "{0}/{1}개 재사용 묶음의 적합한 과거 실행 기록 기반": [
    "Based on suitable previous timing records for {0}/{1} reused groups",
    "基于 {0}/{1} 个复用验证组的适用历史运行记录"
  ],
  "전체 {0}개 중 {1}개만 다시 실행 · {2}개 결과 재사용": [
    "Only {1} of {0} groups rerun · Reused: {2}",
    "全部 {0} 个中仅重跑 {1} 个 · 复用 {2} 个结果"
  ],
  "재실행하지 않은 검증 묶음": [
    "Check groups not rerun",
    "无需重跑的验证组"
  ],
  "{0} / {1}개": [
    "{0} / {1} groups",
    "{0} / {1} 个"
  ],
  "시간 근거 {0}/{1}개 · 부분 추정 {2} [추정]": [
    "Timing evidence for {0}/{1} groups · Partial estimate {2} [Estimated]",
    "计时依据覆盖 {0}/{1} 个 · 部分估算 {2} [估算]"
  ],
  "재사용은 확인됐습니다. 적합한 시간 표본이 없어 시간은 미측정입니다.": [
    "Reuse is confirmed. Timing is not measured because no suitable timing samples are available.",
    "复用已确认。没有适用的计时样本，因此时间未测量。"
  ],
  "{0}개 중 {1}개 재실행을 피했습니다. 실제 실행 {2}개.": [
    "Avoided rerunning {1} of {0} groups. Actually ran {2}.",
    "避免重跑 {0} 个中的 {1} 个验证组。实际运行 {2} 个。"
  ],
  "요청 미완료": [
    "Request incomplete",
    "请求未完成"
  ],
  "요청 범위의 최종 상태가 확정되지 않았습니다.": [
    "The final states within the requested scope are not confirmed.",
    "尚未确认请求范围内的最终状态。"
  ],
  "완료되지 않은 검증 요청은 절감 성과로 표시하지 않습니다.": [
    "Incomplete verification requests are not presented as savings.",
    "未完成的验证请求不显示为节省成果。"
  ],
  "횟수는 실제 결과입니다. 시간 조건이 불완전하거나 전체 예상이 0이면 시간 비율 그래프를 표시하지 않습니다.": [
    "Counts are actual results. The time-ratio graph is hidden if timing conditions are incomplete or the full estimate is zero.",
    "数量来自实际结果。计时条件不完整或全部运行估算为零时，不显示时间比例图。"
  ],
  "명령 실행 구간은 0입니다. 전체 요청 대기시간이 0이라는 뜻은 아닙니다.": [
    "The command execution interval is zero. This does not mean the entire request took zero time.",
    "命令执行区间为零，并不表示整个请求的等待时间为零。"
  ],
  "전체 묶음을 실행했습니다. 재사용에 따른 실행량 감소는 없습니다.": [
    "All groups were executed. Reuse did not reduce execution volume.",
    "已运行全部验证组，没有通过复用减少执行量。"
  ],
  "같은 0 시작 시간 축 · 빗금 구간은 과거 기록에 기반한 회피 비용 추정입니다.": [
    "Same zero-based time axis · Hatching shows avoided cost estimated from previous records.",
    "相同的零起点时间轴 · 斜线区间表示基于历史记录估算的避免成本。"
  ],
  "요청된 검증 묶음 {0}/{1} 결과 확보": [
    "Results available for {0}/{1} requested check groups",
    "已取得请求验证组 {0}/{1} 的结果"
  ],
  "동일 시간 축": [
    "Same time axis",
    "同一时间轴"
  ],
  "횟수 비교": [
    "Count comparison",
    "数量对比"
  ],
  "{0} [추정]": [
    "{0} [Estimated]",
    "{0} [估算]"
  ],
  "부분 기록 합계 {0} [실측]": [
    "Partial recorded total {0} [Measured]",
    "部分记录合计 {0} [实测]"
  ],
  "{0} [실측]": [
    "{0} [Measured]",
    "{0} [实测]"
  ],
  "전체 재실행 기준의 실행 대상": [
    "Execution target in the full rerun reference",
    "全部重跑基准中的运行对象"
  ],
  "재사용한 검증 묶음 {0}개 보기": [
    "View {0} reused check groups",
    "查看 {0} 个复用的验证组"
  ],
  "통과 {0} · 실패 {1} · 중단 {2} · 미실행 {3} · 대기/미확정 {4}": [
    "Passed {0} · Failed {1} · Interrupted {2} · Not run {3} · Pending/unconfirmed {4}",
    "通过 {0} · 失败 {1} · 中断 {2} · 未运行 {3} · 等待／未确认 {4}"
  ],
  "↺ 피한 재실행 비용 {0} [추정]": [
    "↺ Avoided rerun cost {0} [Estimated]",
    "↺ 避免的重跑成本 {0} [估算]"
  ],
  "{0}개": [
    "{0} groups",
    "{0} 个"
  ],
  "실행 {0} · 재사용 {1}": [
    "Executed {0} · Reused {1}",
    "运行 {0} · 复用 {1}"
  ],
  "같은 묶음을 모두 실행하는 기준이며 관찰한 이전 실행이 아닙니다.{0}": [
    "A reference for running all the same groups, not an observed previous run.{0}",
    "这是全部运行相同验证组的基准，并非观测到的历史运行。{0}"
  ],
  " 상세 기록 {0}/{1}개 · 나머지는 미확정": [
    " Details retained for {0}/{1} groups · The remainder is unconfirmed",
    " 详细记录覆盖 {0}/{1} 个 · 其余未确认"
  ],
  "실행 전에 취소되어 시작하지 않았습니다. 이전 계약의 승인이나 실행 권한은 이어받지 않습니다.": [
    "Cancelled before execution, so it did not start. Approval or execution authority from the previous contract does not carry over.",
    "执行前已取消，未开始运行。不会继承之前契约的批准或执行权限。"
  ],
  "실제 실행 기록이 없는 이전 데이터입니다. 계획을 실행 실적으로 표시하지 않습니다.": [
    "Legacy data without actual execution records. Plans are not presented as executed results.",
    "这是缺少实际运行记录的旧版数据。计划不会显示为运行成果。"
  ],
  "판정 근거 정보가 없습니다.": [
    "No decision evidence is available.",
    "没有可用的判定依据。"
  ],
  "현재 조건과 일치하는 기존 통과 결과의 재사용을 계획했습니다. 아직 적용하지 않았습니다.": [
    "Reuse of an existing pass matching current conditions is planned. It has not been applied.",
    "计划复用与当前条件匹配的已有通过结果。尚未应用。"
  ],
  "관찰된 입력의 판정에 따른 재사용 계획입니다. 아직 적용하지 않았습니다.": [
    "Reuse based on observed-input evaluation is planned. It has not been applied.",
    "计划根据观测输入的判定复用结果。尚未应用。"
  ],
  "기존 안전 변경 정책에 따른 재사용 계획입니다. 아직 적용하지 않았습니다.": [
    "Reuse under the existing safe-change policy is planned. It has not been applied.",
    "计划根据现有安全变更策略复用结果。尚未应用。"
  ],
  "현재 조건에 따라 실제 검증을 실행할 계획입니다. 아직 시작하지 않았습니다.": [
    "Actual verification is planned under the current conditions. It has not started.",
    "计划按照当前条件实际运行验证。尚未开始。"
  ],
  "이전 계약의 통과 결과를 현재 계약에서 다시 판정해 적용했습니다.": [
    "A pass from a previous contract was reevaluated and applied in the current contract.",
    "已在当前契约中重新判定并应用之前契约的通过结果。"
  ],
  "같은 계약 안의 유효한 통과 결과를 재사용했습니다.": [
    "A valid passing result within the same contract was reused.",
    "已复用同一契约内的有效通过结果。"
  ],
  "이번 요청에서 실제 실행한 결과입니다.": [
    "This is an actual execution result from this request.",
    "这是本次请求中实际运行的结果。"
  ],
  "아직 실행·재사용 결과가 없습니다.": [
    "No execution or reuse results yet.",
    "尚无运行或复用结果。"
  ],
  "이전 작업 → 원본 성공 실행 → 현재 적용": [
    "Previous task → Original successful run → Current application",
    "之前任务 → 原始成功运行 → 当前应用"
  ],
  "같은 계약의 성공 실행 → 현재 적용": [
    "Successful run in the same contract → Current application",
    "同一契约的成功运行 → 当前应用"
  ],
  "이번 요청의 실제 실행 흐름": [
    "Actual execution flow of this request",
    "本次请求的实际运行流程"
  ],
  "관측 시점 미측정": [
    "Observation time not measured",
    "观测时间未测量"
  ],
  "이전 작업: {0}": [
    "Previous task: {0}",
    "之前任务：{0}"
  ],
  "보관 범위 밖의 이전 작업": [
    "Previous task outside retained history",
    "超出保留范围的之前任务"
  ],
  "원본 성공 실행: {0} · {1} · {2}": [
    "Original successful run: {0} · {1} · {2}",
    "原始成功运行：{0} · {1} · {2}"
  ],
  "현재 적용: 변경 {0}에서 재사용 판정 통과": [
    "Current application: reuse evaluation passed at revision {0}",
    "当前应用：在修订 {0} 通过复用判定"
  ],
  "현재 작업 안의 이전 성공 실행": [
    "Previous successful run within the current task",
    "当前任务内的历史成功运行"
  ],
  "현재 적용: 변경 {0}에서 같은 계약 재사용": [
    "Current application: same-contract reuse at revision {0}",
    "当前应用：在修订 {0} 进行同一契约内复用"
  ],
  "현재 요청: 변경 {0}": [
    "Current request: revision {0}",
    "当前请求：修订 {0}"
  ],
  "실제 결과: {0} · source-command 구간 {1}": [
    "Actual result: {0} · Source-command interval {1}",
    "实际结果：{0} · source-command 区间 {1}"
  ],
  "판정: {0}": [
    "Decision: {0}",
    "判定：{0}"
  ],
  "현재 revision {0}": [
    "Current revision {0}",
    "当前修订 {0}"
  ],
  "이전 성공 revision {0}": [
    "Previous passing revision {0}",
    "历史通过修订 {0}"
  ],
  "이전 성공 없음": [
    "No previous pass",
    "无历史通过结果"
  ],
  "계획 {0} · 실제 {1}": [
    "Planned {0} · Actual {1}",
    "计划 {0} · 实际 {1}"
  ],
  "없음": [
    "None",
    "无"
  ],
  "현재 실행 구간 {0}": [
    "Current execution interval {0}",
    "当前运行区间 {0}"
  ],
  "원본 성공 표본 {0}개 · revision {1} · {2} · {3}": [
    "Original successful samples {0} · Revision {1} · {2} · {3}",
    "原始成功样本 {0} 个 · 修订 {1} · {2} · {3}"
  ],
  "측정 구간 정보 없음": [
    "Measurement interval unavailable",
    "无测量区间信息"
  ],
  "과거 시간 표본 없음": [
    "No previous timing sample",
    "无历史计时样本"
  ],
  "원본 작업 ID {0}": [
    "Original task ID {0}",
    "原始任务 ID {0}"
  ],
  "원본 작업 ID 없음": [
    "Original task ID unavailable",
    "无原始任务 ID"
  ],
  "원본 성공 배치 ID {0}": [
    "Original successful batch ID {0}",
    "原始成功批次 ID {0}"
  ],
  "원본 성공 배치 ID 없음": [
    "Original successful batch ID unavailable",
    "无原始成功批次 ID"
  ],
  "원본 source ID {0}": [
    "Original source ID {0}",
    "原始 source ID {0}"
  ],
  "원본 source ID 없음": [
    "Original source ID unavailable",
    "无原始 source ID"
  ],
  "현재 재사용 출처 배치 ID {0} · 출처 revision {1}": [
    "Reuse origin batch ID {0} · Origin revision {1}",
    "复用来源批次 ID {0} · 来源修订 {1}"
  ],
  "현재 계약 안의 근거": [
    "Evidence within the current contract",
    "当前契约内的依据"
  ],
  "정확한 검사 결합 {0}": [
    "Exact check binding {0}",
    "精确检查绑定 {0}"
  ],
  "정보 없음": [
    "Unavailable",
    "无信息"
  ],
  "시간 조건 결합 {0}": [
    "Timing-condition binding {0}",
    "计时条件绑定 {0}"
  ],
  "시간 조건 결합 없음": [
    "No timing-condition binding",
    "无计时条件绑定"
  ],
  "판정 식별자 {0}": [
    "Decision identifier {0}",
    "判定标识符 {0}"
  ],
  "{0}/{1}개 표시{2}": [
    "Showing {0}/{1} groups{2}",
    "显示 {0}/{1} 个验证组{2}"
  ],
  " · 해당 결과가 없습니다.": [
    " · No matching results.",
    " · 无匹配结果。"
  ],
  "이전 계약에서 재판정": [
    "Reevaluated from a previous contract",
    "从之前契约重新判定"
  ],
  "같은 계약 재사용": [
    "Reuse within the same contract",
    "同一契约内复用"
  ],
  "과거 성공 실행 {0}": [
    "Previous successful run {0}",
    "历史成功运行 {0}"
  ],
  "과거 시간 근거 없음": [
    "No previous timing evidence",
    "无历史计时依据"
  ],
  "실제 {0} · 이번 source-command 구간 {1}": [
    "Actual {0} · Source-command interval this time {1}",
    "实际 {0} · 本次 source-command 区间 {1}"
  ],
  "계획 {0} · 실제 시작 기록 없음": [
    "Planned {0} · No actual start recorded",
    "计划 {0} · 无实际开始记录"
  ],
  "해당 결과가 없습니다": [
    "No matching results",
    "无匹配结果"
  ],
  "아직 요청된 검증이 없습니다": [
    "No checks requested yet",
    "尚未请求验证"
  ],
  "검증 계획이 생성되면 실행과 재사용 이유가 여기에 표시됩니다.": [
    "Execution and reuse reasons will appear here once a verification plan is created.",
    "生成验证计划后，这里将显示运行和复用的原因。"
  ],
  "이 과거 배치의 입력 그래프는 보관하지 않습니다. 최신 배치에서 현재 Evidence Map을 볼 수 있습니다.": [
    "The input graph for this historical batch is not retained. Select the latest batch to view the current Evidence Map.",
    "未保留此历史批次的输入图。选择最新批次可查看当前 Evidence Map。"
  ],
  "입력 {0}개 표시 · {1}개 생략": [
    "Showing {0} inputs · {1} omitted",
    "显示 {0} 个输入 · 省略 {1} 个"
  ],
  "입력 {0}개": [
    "{0} inputs",
    "{0} 个输入"
  ],
  "현재 계약 · 검증 요청 기록 없음": [
    "Current contract · No verification request records",
    "当前契约 · 无验证请求记录"
  ],
  "{0} · {1} · 변경 {2} · {3}": [
    "{0} · {1} · Revision {2} · {3}",
    "{0} · {1} · 修订 {2} · {3}"
  ],
  "이전 검증": [
    "Previous verification",
    "之前验证"
  ],
  " 아직 종료가 확인되지 않았습니다. 연결이 끊겨도 정상 완료로 계산하지 않습니다.": [
    " Completion has not been confirmed. A disconnected session is not counted as a successful completion.",
    " 尚未确认结束。即使连接断开也不会计为正常完成。"
  ],
  "이전 데이터에 실제 실행 기록이 없으면 계획을 실적으로 계산하지 않습니다.": [
    "Plans in legacy data without actual execution records are not counted as results.",
    "旧版数据中没有实际运行记录的计划不计为成果。"
  ],
  "보관 배치 {0}개 중 {1}개 표시 · 최대 1,000건 / 7일 / 4 MiB · 종료 미확정 기록은 완료 통계에서 제외": [
    "Showing {1} of {0} retained batches · Up to 1,000 events / 7 days / 4 MiB · Unconfirmed endings excluded from completion statistics",
    "显示保留的 {0} 个批次中的 {1} 个 · 最多 1,000 条／7 天／4 MiB · 结束未确认的记录不计入完成统计"
  ],
  " · 종료 기록 {0}개: 실제 실행 {1} / 적용 재사용 {2} / 미실행 {3}": [
    " · Finalized records {0}: actually executed {1} / reuse applied {2} / not run {3}",
    " · 已结束记录 {0} 个：实际运行 {1}／已复用 {2}／未运行 {3}"
  ],
  "{0} · 변경 {1}": [
    "{0} · Revision {1}",
    "{0} · 修订 {1}"
  ],
  "{0} · 실행 {1} / 재사용 {2}": [
    "{0} · Executed {1} / Reused {2}",
    "{0} · 运行 {1}／复用 {2}"
  ],
  "{0}개 결과 재사용": [
    "Results reused: {0}",
    "复用 {0} 个结果"
  ],
  "이력 집계 없음": [
    "No history aggregate",
    "无历史汇总"
  ],
  "{0} {1} [추정]": [
    "{0} {1} [Estimated]",
    "{0} {1} [估算]"
  ],
  "기록 있는 요청의 부분 추정 합계": [
    "Partial estimate for requests with records",
    "有记录请求的部分估算合计"
  ],
  "피한 재실행 비용 합계": [
    "Total avoided rerun cost",
    "避免的重跑成本合计"
  ],
  "시간 근거 없음 · 시간 합계 미측정": [
    "No timing evidence · Total time not measured",
    "无计时依据 · 时间合计未测量"
  ],
  "완료 요청 {0}개 · 누락 시간 표본 {1}개 · {2} · {3} ~ {4} · 최대 7일/1,000건 · 전체 대기 절감시간이 아닙니다.": [
    "Completed requests {0} · Missing timing samples {1} · {2} · {3} ~ {4} · Up to 7 days/1,000 events · Not total waiting time saved.",
    "已完成请求 {0} 个 · 缺失计时样本 {1} 个 · {2} · {3} ~ {4} · 最多 7 天／1,000 条 · 并非总等待时间的节省。"
  ],
  "시작 기록 없음": [
    "No start recorded",
    "无开始记录"
  ],
  "종료 기록 없음": [
    "No end recorded",
    "无结束记录"
  ],
  "구형 projection은 완료 이력 합계를 제공하지 않습니다.": [
    "Legacy projections do not provide completed-history totals.",
    "旧版 projection 不提供已完成历史记录的汇总。"
  ],
  "첫 실행": [
    "First run",
    "首次运行"
  ],
  "변경 없음": [
    "Unchanged",
    "无变化"
  ],
  "문서 변경": [
    "Documentation change",
    "文档修改"
  ],
  "일부 실행 + 일부 재사용": [
    "Partial execution + partial reuse",
    "部分运行＋部分复用"
  ],
  "코드 변경": [
    "Code change",
    "代码修改"
  ],
  "환경 변경": [
    "Environment change",
    "环境变化"
  ],
  "첫 검사 실패": [
    "First check failed",
    "首项验证失败"
  ],
  "부분 영향 코드": [
    "Code affecting part of the scope",
    "影响部分范围的代码"
  ],
  "다른 단일 묶음 영향": [
    "Another single group affected",
    "影响另一个单独验证组"
  ],
  "모든 묶음 영향": [
    "All groups affected",
    "影响全部验证组"
  ],
  "예상 실패": [
    "Expected failure",
    "预期失败"
  ],
  "수정 후 재시도": [
    "Retry after fix",
    "修复后重试"
  ],
  "같은 묶음 전체 실행": [
    "Run all the same groups",
    "运行全部相同验证组"
  ],
  "기존 전체 검증 명령": [
    "Original full verification command",
    "原始完整验证命令"
  ],
  "지원하지 않는 비교 형식": [
    "Unsupported comparison format",
    "不支持的对比格式"
  ],
  "비교 조건 정보가 없습니다": [
    "Comparison conditions are missing",
    "缺少对比条件信息"
  ],
  "비교 반복 조건이 잘못되었습니다": [
    "Invalid comparison repetition conditions",
    "对比重复条件无效"
  ],
  "비교 표본이 잘못되었습니다": [
    "Invalid comparison sample",
    "对比样本无效"
  ],
  "표본의 실행 순서나 워밍업 조건이 잘못되었습니다": [
    "Invalid sample execution order or warmup conditions",
    "样本执行顺序或预热条件无效"
  ],
  "실측 결과 정보가 없습니다": [
    "Measured results are missing",
    "缺少实测结果信息"
  ],
  "각 비교 경로에 별도 저장소와 동일 baseline 절차 · OS 캐시 초기화 안 함 · bytecode 비활성": [
    "Separate repository and identical baseline procedure per comparison arm · OS cache not cleared · Bytecode disabled",
    "每条对比路径使用独立仓库与相同基准流程 · 未清空操作系统缓存 · 已禁用字节码"
  ],
  "비교 보고서 구조가 잘못되었습니다": [
    "Invalid comparison report structure",
    "对比报告结构无效"
  ],
  "비교 표본 수가 잘못되었습니다": [
    "Invalid comparison sample count",
    "对比样本数量无效"
  ],
  "중복 비교 표본입니다": [
    "Duplicate comparison sample",
    "对比样本重复"
  ],
  "표본 실행 순서가 잘못되었습니다": [
    "Invalid sample execution order",
    "样本执行顺序无效"
  ],
  "비교 계산이 원시 시간과 다릅니다": [
    "Comparison calculations do not match raw timings",
    "对比计算与原始计时不一致"
  ],
  "비교 추가 비용 표본이 잘못되었습니다": [
    "Invalid additional-cost comparison sample",
    "对比附加成本样本无效"
  ],
  "비교 추가 비용 구간이 잘못되었습니다": [
    "Invalid additional-cost comparison interval",
    "对比附加成本区间无效"
  ],
  "저장소 번들 참조가 잘못되었습니다": [
    "Invalid repository bundle reference",
    "仓库验证集合参考无效"
  ],
  "저장소 번들 조건이 잘못되었습니다": [
    "Invalid repository bundle conditions",
    "仓库验证集合条件无效"
  ],
  "저장소 번들 표본이 잘못되었습니다": [
    "Invalid repository bundle sample",
    "仓库验证集合样本无效"
  ],
  "저장소 번들 실행 결과가 잘못되었습니다": [
    "Invalid repository bundle execution result",
    "仓库验证集合执行结果无效"
  ],
  "저장소 번들 계산이 원시 시간과 다릅니다": [
    "Repository bundle calculations do not match raw timings",
    "仓库验证集合计算与原始计时不一致"
  ],
  "저장소 번들 요약이 잘못되었습니다": [
    "Invalid repository bundle summary",
    "仓库验证集合摘要无效"
  ],
  "매 반복·구성마다 초기 상태 복원 · 단계 내 세 측정 교차 · OS 캐시 초기화 안 함 · bytecode 비활성": [
    "Initial state restored per repetition and configuration · Three measurements rotated within each stage · OS cache not cleared · Bytecode disabled",
    "每次重复及每种配置恢复初始状态 · 每阶段轮换三种测量顺序 · 未清空操作系统缓存 · 已禁用字节码"
  ],
  "Click 기본": [
    "Click default",
    "Click 默认"
  ],
  "명시적 재사용": [
    "Explicit reuse",
    "显式复用"
  ],
  "same-shards는 테스트 명령 구간, parent-suite는 Click 요청 구간을 비교하며 서로 합치지 않습니다.": [
    "same-shards compares test-command intervals; parent-suite compares Click request intervals. They are not combined.",
    "same-shards 对比测试命令区间；parent-suite 对比 Click 请求区间。两者不合并。"
  ],
  "legacy paired는 전체 기준 명령의 wall time과 Click 요청 구간의 wall time을 비교합니다. source-command 실행 구간 비교가 아닙니다.": [
    "Legacy paired data compares full-reference command wall time with Click request wall time, not source-command execution intervals.",
    "旧版配对数据对比全部基准命令的实际耗时与 Click 请求的实际耗时，并非 source-command 执行区间。"
  ],
  "독립 Guarded fixture 단계별": [
    "Independent Guarded fixture stages",
    "独立 Guarded 测试场景的各阶段"
  ],
  "별도 paired 비교 실측 {0}개 표본군 · {1}": [
    "{0} separately measured paired sample groups · {1}",
    "单独实测的配对样本组 {0} 个 · {1}"
  ],
  "정상 완료한 paired 비교 표본 없음": [
    "No successfully completed paired samples",
    "没有正常完成的配对样本"
  ],
  "{0} {1} · 쌍별 중앙값": [
    "{0} {1} · Median paired difference",
    "{0} {1} · 配对差值中位数"
  ],
  "증가": [
    "Increase",
    "增加"
  ],
  "감소": [
    "Decrease",
    "减少"
  ],
  "성공 표본군 {0}개 · 아래 쌍별 중앙값 참조": [
    "{0} successful sample groups · See paired medians below",
    "成功样本组 {0} 个 · 参见下方配对中位数"
  ],
  "별도 fixture에서 Click 요청 구간이 더 길었습니다. 상세 보기": [
    "The Click request interval was longer in a separate fixture. View details",
    "独立测试场景中的 Click 请求区间更长。查看详情"
  ],
  "별도 fixture에서 Click 측정 구간이 더 길었습니다. 상세 보기": [
    "The Click measurement interval was longer in a separate fixture. View details",
    "独立测试场景中的 Click 测量区间更长。查看详情"
  ],
  " · 실제 저장소 전체 테스트 번들 그룹화 참조 첨부": [
    " · Actual repository test-bundle grouping reference attached",
    " · 已附实际仓库完整测试集合的分组参考"
  ],
  " · 실제 저장소 번들 참조 없음": [
    " · No actual repository bundle reference",
    " · 无实际仓库验证集合参考"
  ],
  "별도 fixture 직접 측정 · 이번 live 요청과 별개 · 서명 없음 · 승인·재사용 권한 없음 · {0} · 반복 {1} / 워밍업 {2} · 각 경로의 2개 테스트 파일 범위 일치 · {3}{4}. {5} 아래는 성공 표본의 중앙값이고 음수는 해당 행의 측정 구간이 증가했다는 뜻입니다.": [
    "Direct measurement of a separate fixture · Separate from this live request · Unsigned · No approval or reuse authority · {0} · Repetitions {1} / warmups {2} · Matching two-test-file scope in each arm · {3}{4}. {5} Values below are medians of successful samples; a negative value means that row's measured interval increased.",
    "独立测试场景的直接测量 · 与本次实时请求分开 · 未签名 · 无批准或复用权限 · {0} · 重复 {1}／预热 {2} · 各路径的两个测试文件范围相同 · {3}{4}。{5} 下方为成功样本的中位数；负值表示对应行的测量区间增加。"
  ],
  "{0} · {1}회 측정 / {2}회 제외": [
    "{0} · {1} measured / {2} excluded",
    "{0} · 测量 {1} 次／排除 {2} 次"
  ],
  "Click 증분 실행": [
    "Click incremental run",
    "Click 增量运行"
  ],
  "{0}: {1} [실측 · 경로 중앙값]": [
    "{0}: {1} [Measured · Arm median]",
    "{0}：{1} [实测 · 路径中位数]"
  ],
  "쌍별 차이 중앙값 {0} ms ({1}) · 범위 {2} ~ {3} ms": [
    "Median paired difference {0} ms ({1}) · Range {2} ~ {3} ms",
    "配对差值中位数 {0} ms（{1}）· 范围 {2} ~ {3} ms"
  ],
  "비율 계산 불가": [
    "Ratio unavailable",
    "无法计算比例"
  ],
  "정상 완료 성능 표본 없음 · 실패·중단·워밍업 표본은 JSON에 별도 보관": [
    "No successfully completed performance samples · Failed, interrupted and warmup samples are retained separately in JSON",
    "没有正常完成的性能样本 · 失败、中断及预热样本在 JSON 中单独保留"
  ],
  "별도 비교의 초기 준비·변경·추가 감사 비용 (워밍업·실패 포함)": [
    "Separate comparison: initial setup, changes and additional audit costs (including warmups and failures)",
    "独立对比的初始设置、修改及附加审计成本（含预热与失败）"
  ],
  "{0} · 반복 {1}{2} · 초기 준비 {3} / 변경 {4} / 추가 전체 감사 {5} / 요청 구간 합계 {6} [실측]. 각 구간은 별도 기록입니다.": [
    "{0} · Repetition {1}{2} · Initial setup {3} / changes {4} / additional full audit {5} / total request intervals {6} [Measured]. Each interval is recorded separately.",
    "{0} · 重复 {1}{2} · 初始设置 {3}／修改 {4}／附加完整审计 {5}／请求区间合计 {6} [实测]。各区间单独记录。"
  ],
  " · 워밍업": [
    " · Warmup",
    " · 预热"
  ],
  "검증 묶음 {0}": [
    "Check group {0}",
    "验证组 {0}"
  ],
  "피한 테스트 재실행 비용 {0} [{1}]{2}.": [
    "Avoided test rerun cost {0} [{1}]{2}.",
    "避免的测试重跑成本 {0} [{1}]{2}。"
  ],
  "부분 추정": [
    "Partial estimate",
    "部分估算"
  ],
  " · 시간 근거 {0}개": [
    " · Timing evidence for {0} groups",
    " · 计时依据覆盖 {0} 个验证组"
  ],
  "요청 미완료 · 실제 관찰된 부분 결과만 표시합니다.": [
    "Request incomplete · Only actually observed partial results are shown.",
    "请求未完成 · 仅显示实际观测到的部分结果。"
  ],
  "표시할 실행 기록이 없습니다.": [
    "No execution records to display.",
    "没有可显示的运行记录。"
  ],
  " [추정]": [
    " [Estimated]",
    " [估算]"
  ],
  "전체 사용자 대기시간은 측정하지 않음": [
    "Total user waiting time was not measured",
    "未测量用户总等待时间"
  ],
  "Hook 진입부터 결과 기록 또는 준비와 runner 개별 구간만 부분 계측 · 호스트 요청 전·최종 저장·반환 제외": [
    "Partial measurement only: Hook entry through result recording, or individual preparation and runner intervals · Excludes time before the host request, final saving and return",
    "仅部分测量：Hook 进入至结果记录，或准备与 runner 的独立区间 · 不含主机请求之前、最终保存及返回"
  ],
  "생략한 테스트 실행시간은 실제 적용된 재사용의 적합한 이전 성공 실행 표본에 기반한 추정": [
    "Omitted test execution time is estimated from suitable previous successful timing samples for reuse that was actually applied",
    "省略的测试运行时间基于实际应用复用所对应的适用历史成功计时样本估算"
  ],
  "전체 순차 실행 추정은 같은 묶음의 source command 구간 합이며 원래 parent 명령의 실측 wall time이 아님": [
    "The full sequential estimate sums source-command intervals for the same groups; it is not measured wall time of the original parent command",
    "全部顺序运行估算为相同验证组的 source-command 区间之和，并非原始 parent 命令的实测耗时"
  ],
  "생략한 테스트 실행시간은 관리비용을 뺀 순절감이나 사용자 대기시간 절감이 아님": [
    "Omitted test execution time is not net savings after overhead or reduced user waiting time",
    "省略的测试运行时间并非扣除管理开销后的净节省，也不是用户等待时间的减少"
  ],
  "Shadow는 실제 재사용·실측 절약 아님": [
    "Shadow is neither actual reuse nor measured savings",
    "Shadow 不是实际复用或实测节省"
  ],
  "입력 파일 경로와 원시 명령·환경·토큰은 공유본에 포함하지 않음": [
    "Input file paths, raw commands, environment values and tokens are excluded from the shared report",
    "分享报告不包含输入文件路径、原始命令、环境值及令牌"
  ],
  "비교 fixture 결과를 일반 저장소 성능으로 일반화할 수 없음": [
    "Comparison fixture results cannot be generalized to arbitrary repositories",
    "对比测试场景的结果不能推广为一般仓库性能"
  ],
  "Click 검증 효율 리포트": [
    "Click Verification Efficiency Report",
    "Click 验证效率报告"
  ],
  "Click · 검증 효율 리포트": [
    "Click · Verification Efficiency Report",
    "Click · 验证效率报告"
  ],
  "전체 기준과 이번 실제 결과": [
    "Full reference and actual results this time",
    "全部基准与本次实际结果"
  ],
  "전체 재실행 기준 {0}개 / 이번 실제 실행 {1}개 + 재사용 {2}개": [
    "Full rerun reference: {0} groups / Actually executed: {1} + reused: {2}",
    "全部重跑基准 {0} 个／本次实际运行 {1} 个＋复用 {2} 个"
  ],
  "● 실행 · ↺ 재사용 · ! 실패/중단 · · 미실행/미확정. 전체 기준은 관찰한 이전 실행이 아닙니다.": [
    "● Executed · ↺ Reused · ! Failed/interrupted · · Not run/unconfirmed. The full reference is not an observed previous run.",
    "● 已运行 · ↺ 已复用 · ! 失败／中断 · · 未运行／未确认。全部基准并非观测到的历史运行。"
  ],
  "빗금 구간: 피한 재실행 비용 {0} [추정] · 같은 0 시작 시간 축": [
    "Hatched interval: avoided rerun cost {0} [Estimated] · Same zero-based time axis",
    "斜线区间：避免的重跑成本 {0} [估算] · 相同的零起点时间轴"
  ],
  "검증 묶음별 실제 결과": [
    "Actual results by check group",
    "各验证组的实际结果"
  ],
  "이름": [
    "Name",
    "名称"
  ],
  "계획": [
    "Plan",
    "计划"
  ],
  "실제 결과": [
    "Actual result",
    "实际结果"
  ],
  "시간 / 과거 표본": [
    "Time / Previous sample",
    "时间／历史样本"
  ],
  "이유": [
    "Reason",
    "原因"
  ],
  " · 이전 계약에서 재판정": [
    " · Reevaluated from a previous contract",
    " · 从之前契约重新判定"
  ],
  " · 같은 계약 재사용": [
    " · Reuse within the same contract",
    " · 同一契约内复用"
  ],
  " (과거 성공 실행)": [
    " (previous successful run)",
    "（历史成功运行）"
  ],
  "접힌 화면과 같은 측정 상세": [
    "The same measurement details as the dashboard",
    "与仪表盘相同的测量详情"
  ],
  "Hook 진입 → 결과 기록 부분 요청시간: {0} / 현재 측정 가능한 처리 구간: {1}": [
    "Hook entry → Result recording, partial request time: {0} / Currently measurable processing intervals: {1}",
    "Hook 进入 → 结果记录，部分请求时间：{0}／当前可测量的处理区间：{1}"
  ],
  "Click 전체 관리비용: 측정 정보 없음. 포함 관계가 있는 시간을 빼서 관리비용을 만들지 않습니다.": [
    "Total Click overhead: not measured. Overlapping intervals are not subtracted to invent overhead.",
    "Click 总管理开销：未测量。不会通过相减有包含关系的时间来推算管理开销。"
  ],
  "별도 paired 비교 실측": [
    "Separately measured paired comparison",
    "单独实测的配对对比"
  ],
  "비교 측정 없음. 일상 추정 비용을 실측한 전체 재실행 시간으로 환산하지 않습니다.": [
    "No measured comparison. Everyday cost estimates are not converted into measured full-rerun time.",
    "无对比测量。不会将日常成本估算转换为实测的全部重跑时间。"
  ],
  "승인·재사용 권한 없음 · 반복 {0} · 워밍업 {1} · {2} · {3}": [
    "No approval or reuse authority · Repetitions {0} · Warmups {1} · {2} · {3}",
    "无批准或复用权限 · 重复 {0} · 预热 {1} · {2} · {3}"
  ],
  "same-shards 행은 명령 구간, parent-suite 행은 Click 요청 구간을 비교하며 서로 합산하지 않습니다.": [
    "same-shards rows compare command intervals; parent-suite rows compare Click request intervals. They are not added together.",
    "same-shards 行对比命令区间；parent-suite 行对比 Click 请求区间。两者不相加。"
  ],
  "legacy paired는 전체 기준 명령의 wall time과 Click 요청 구간의 wall time을 비교합니다.": [
    "Legacy paired data compares full-reference command wall time with Click request wall time.",
    "旧版配对数据对比全部基准命令的实际耗时与 Click 请求的实际耗时。"
  ],
  "각 경로 중앙값과 쌍별 차이 중앙값은 서로 다른 통계입니다. 음수는 해당 fixture 비교에서 Click 측정 구간이 증가했다는 뜻입니다.": [
    "Arm medians and medians of paired differences are distinct statistics. A negative value means the Click measurement interval increased in that fixture comparison.",
    "各路径中位数与配对差值中位数是不同的统计量。负值表示该测试场景对比中的 Click 测量区间增加。"
  ],
  "별도 비교의 준비·변경·추가 감사 비용": [
    "Separate comparison: setup, changes and additional audit costs",
    "独立对比的设置、修改及附加审计成本"
  ],
  "{0} · 반복 {1}{2} · 준비 {3} / 변경 {4} / 추가 감사 {5} / 요청 합계 {6} [실측 · 실패 포함]": [
    "{0} · Repetition {1}{2} · Setup {3} / changes {4} / additional audit {5} / request total {6} [Measured · Includes failures]",
    "{0} · 重复 {1}{2} · 设置 {3}／修改 {4}／附加审计 {5}／请求合计 {6} [实测 · 含失败]"
  ],
  "{0}회 / 제외 {1}회": [
    "{0} samples / {1} excluded",
    "{0} 次／排除 {1} 次"
  ],
  "성공 표본 없음": [
    "No successful samples",
    "无成功样本"
  ],
  "{0}{1} · {2} · 범위 {3} ~ {4}": [
    "{0}{1} · {2} · Range {3} ~ {4}",
    "{0}{1} · {2} · 范围 {3} ~ {4}"
  ],
  "증가 ": [
    "Increase ",
    "增加 "
  ],
  "감소 ": [
    "Decrease ",
    "减少 "
  ],
  "비율 없음": [
    "No ratio",
    "无比例"
  ],
  "실제 저장소 테스트 번들 참조": [
    "Actual repository test-bundle reference",
    "实际仓库测试集合参考"
  ],
  "첨부된 실제 저장소 번들 참조 없음. fixture 결과를 저장소 전체 성능으로 일반화하지 않습니다.": [
    "No actual repository bundle reference attached. Fixture results are not generalized to whole-repository performance.",
    "未附实际仓库验证集合参考。不会将测试场景结果推广为整个仓库的性能。"
  ],
  "커밋된 shard inventory · 묶음 {0}개 · 성공 표본 {1}개 · Click 재사용 반사실이 아닌 그룹화 비용 참조": [
    "Committed shard inventory · {0} groups · {1} successful samples · Grouping-cost reference, not a counterfactual for Click reuse",
    "已提交的 shard 清单 · {0} 个验证组 · {1} 个成功样本 · 分组成本参考，并非 Click 复用的反事实对比"
  ],
  "모든 묶음 순차": [
    "All groups sequentially",
    "全部验证组顺序运行"
  ],
  "기존 parent 명령": [
    "Original parent command",
    "原始 parent 命令"
  ],
  "범위와 주의사항": [
    "Scope and limitations",
    "范围与限制"
  ],
  "기계 판독용 원시 ID·조건 (서명 없음)": [
    "Machine-readable raw IDs and conditions (unsigned)",
    "机器可读的原始 ID 与条件（未签名）"
  ],
  "비교 파일은 4 MiB 이하만 읽습니다.": [
    "Comparison files must be 4 MiB or smaller.",
    "对比文件大小不能超过 4 MiB。"
  ],
  "비교 파일을 읽지 못했습니다: {0}": [
    "Could not read the comparison file: {0}",
    "无法读取对比文件：{0}"
  ],
  " JSON 내보내기 완료": [
    " JSON export complete",
    " JSON 导出完成"
  ],
  " HTML 내보내기 완료": [
    " HTML export complete",
    " HTML 导出完成"
  ],
  "요약 문구를 복사했습니다.": [
    "Summary copied.",
    "摘要已复制。"
  ],
  "자동 복사를 사용할 수 없어 문구를 선택했습니다. 복사 단축키를 사용하세요.": [
    "Automatic copying is unavailable. The summary is selected; use your copy shortcut.",
    "无法自动复制。已选中摘要，请使用复制快捷键。"
  ],
  "연결됨": [
    "Connected",
    "已连接"
  ],
  "현재 실행 중인 검증이 없습니다. 아래에서 최근 검증 결과를 볼 수 있습니다.": [
    "No verification is currently running. Recent results are available below.",
    "当前没有运行中的验证。可在下方查看近期结果。"
  ],
  "{0} 모드 · 변경 {1} · {2}": [
    "{0} mode · Revision {1} · {2}",
    "{0} 模式 · 修订 {1} · {2}"
  ],
  "상태 확인 중": [
    "Checking status",
    "正在确认状态"
  ],
  "과거 배치 기록 · 현재 요청과 별개": [
    "Historical batch · Separate from the current request",
    "历史批次 · 与当前请求分开"
  ],
  "이전 작업": [
    "Previous task",
    "之前任务"
  ],
  "{0} · 변경 {1} · {2}": [
    "{0} · Revision {1} · {2}",
    "{0} · 修订 {1} · {2}"
  ],
  "버전 정보 없음": [
    "Version unavailable",
    "无版本信息"
  ],
  "별도 승인됨 · Guarded": [
    "Separately approved · Guarded",
    "已单独批准 · Guarded"
  ],
  "호스트 권한 · Click 승인 없음": [
    "Host authority · No Click approval",
    "主机权限 · 无 Click 批准"
  ],
  "승인 대기 · Guarded": [
    "Awaiting approval · Guarded",
    "等待批准 · Guarded"
  ],
  "활성 계약 없음 · 이력 전용": [
    "No active contract · History only",
    "无活动契约 · 仅显示历史"
  ],
  "승인 정보 없음": [
    "Approval information unavailable",
    "无批准信息"
  ],
  "과거 결과 · 현재 승인 상태와 별개": [
    "Historical result · Separate from current approval",
    "历史结果 · 与当前批准状态分开"
  ],
  "승인 계약 ID 없음": [
    "No approval contract ID",
    "无批准契约 ID"
  ],
  "표시 가능한 약속 요약이 없습니다. 기존 승인 계약 또는 사용자 요청을 확인하세요.": [
    "No displayable promise summary. Check the existing approval contract or user request.",
    "没有可显示的承诺摘要。请查看现有批准契约或用户请求。"
  ],
  "포함: {0}": [
    "In scope: {0}",
    "范围内：{0}"
  ],
  "원문 확인": [
    "See original text",
    "请查看原文"
  ],
  "제외: {0}": [
    "Out of scope: {0}",
    "范围外：{0}"
  ],
  "유지 조건: {0}": [
    "Invariants: {0}",
    "保持条件：{0}"
  ],
  "이 계약의 관측 통제: 차단 {0}건 · 비차단 안내 {1}건. 의미적 범위 준수나 숨은 추론을 판정한 수치가 아닙니다.": [
    "Observed controls for this contract: {0} blocks · {1} non-blocking advisories. These counts do not evaluate semantic scope compliance or hidden reasoning.",
    "此契约中观测到的控制：阻止 {0} 次 · 非阻止提示 {1} 次。这些数量不代表对语义范围合规性或隐藏推理的判定。"
  ],
  "활성 계약이 없습니다. 보관된 viewer 이력은 승인이나 실행 권한을 전달하지 않습니다.": [
    "No active contract. Retained viewer history does not transfer approval or execution authority.",
    "没有活动契约。查看器保留的历史记录不会转移批准或执行权限。"
  ],
  "선택한 배치의 실제 재사용: 같은 계약 {0}개 · 이전 계약에서 재판정 {1}개": [
    "Actual reuse in the selected batch: {0} within the same contract · {1} reevaluated from previous contracts",
    "所选批次中的实际复用：同一契约内 {0} 个 · 从之前契约重新判定 {1} 个"
  ],
  "보관된 검증 그룹 요청 기준: {0} / {1} · {2} · {3} ~ {4}. 실제 재시도는 별도 요청이며 중복 수신·화면 갱신은 추가 집계하지 않습니다.": [
    "Retained check-group requests: {0} / {1} · {2} · {3} ~ {4}. Actual retries are separate requests; duplicate events and refreshes do not add to the count.",
    "保留的验证组请求：{0}／{1} · {2} · {3} ~ {4}。实际重试作为独立请求；重复事件和页面刷新不会重复计数。"
  ],
  "비율 미측정": [
    "Rate not measured",
    "比例未测量"
  ],
  "집계 정보 없음": [
    "No aggregate data",
    "无汇总信息"
  ],
  "재사용 중 과거 시간 표본 미측정 {0}개": [
    "{0} reused groups without measured historical timing samples",
    "{0} 个复用验证组缺少已测量的历史计时样本"
  ],
  "부분 실측: 같은 호스트의 단조 시계로 Hook 진입부터 결과 기록 직전까지 측정했습니다. Hook 이전 요청 대기·최종 저장·호스트 반환은 제외합니다.": [
    "Partial measurement: the same host's monotonic clock measures from Hook entry to just before result recording. Excludes pre-Hook waiting, final saving and host return.",
    "部分实测：使用同一主机的单调时钟，从 Hook 进入测量至结果记录之前。不含 Hook 前等待、最终保存及主机返回。"
  ],
  "부분 계측: 준비·재사용 판정만 포함. 호스트 대기·전달·최종 저장·반환은 제외합니다.": [
    "Partial measurement: preparation and reuse evaluation only. Excludes host waiting, delivery, final saving and return.",
    "部分测量：仅包含准备与复用判定。不含主机等待、传递、最终保存及返回。"
  ],
  "부분 계측: 준비 + runner의 개별 경과시간 합계. 호스트 대기·전달·최종 저장·반환은 제외합니다.": [
    "Partial measurement: sum of separate preparation and runner elapsed intervals. Excludes host waiting, delivery, final saving and return.",
    "部分测量：准备与 runner 独立耗时区间之和。不含主机等待、传递、最终保存及返回。"
  ],
  "이 계약의 요청-결과 시간은 아직 측정되지 않았습니다.": [
    "Request-to-result time has not been measured for this contract yet.",
    "尚未测量此契约的请求至结果时间。"
  ],
  "첫 기준 실행의 parent와 순차 shards 비교입니다. 절감 시간으로 집계하지 않습니다.": [
    "Comparison of the parent command and sequential shards in the first baseline run. Not counted as time saved.",
    "首次基准运行中的 parent 命令与顺序 shards 对比。不计入节省时间。"
  ],
  "Observer: Shadow 켜짐": [
    "Observer: Shadow on",
    "Observer：Shadow 已开启"
  ],
  "Observer: 꺼짐": [
    "Observer: off",
    "Observer：已关闭"
  ],
  "완전하고 현재 계약에 결합된 v2 관찰만 observed-input 재사용 권한이 됩니다.": [
    "Only complete v2 observations bound to the current contract grant observed-input reuse authority.",
    "仅完整且绑定到当前契约的 v2 观测授予 observed-input 复用权限。"
  ],
  "예측 정확도를 측정하지만 검사 생략 권한은 만들지 않습니다.": [
    "Measures prediction accuracy but does not grant permission to skip checks.",
    "测量预测准确性，但不授予跳过验证的权限。"
  ],
  "Dashboard는 계속 볼 수 있으며 기존 exact·policy reuse는 정상 동작합니다.": [
    "The dashboard remains available, and existing exact/policy reuse continues to work.",
    "仍可查看仪表盘，现有的精确匹配与策略复用正常工作。"
  ],
  "집계 범위: {0}": [
    "Aggregation scope: {0}",
    "汇总范围：{0}"
  ],
  "시간 기준: {0}": [
    "Timing basis: {0}",
    "计时依据：{0}"
  ],
  "측정 단위: {0}": [
    "Measurement unit: {0}",
    "测量单位：{0}"
  ],
  "생략 시간 상태: {0}": [
    "Omitted-time status: {0}",
    "省略时间状态：{0}"
  ],
  "이번 실행시간 상태: {0}": [
    "Current execution-time status: {0}",
    "本次执行时间状态：{0}"
  ],
  "전체 순차 실행 상태: {0}": [
    "Full sequential execution status: {0}",
    "全部顺序执行状态：{0}"
  ],
  "감소율 상태: {0}": [
    "Reduction-rate status: {0}",
    "缩短比例状态：{0}"
  ],
  "상태 사유: {0}": [
    "Status reasons: {0}",
    "状态原因：{0}"
  ],
  "재사용 판정: exact {0} · observed-input {1} · safe-change {2}": [
    "Reuse decisions: exact {0} · observed-input {1} · safe-change {2}",
    "复用判定：exact {0} · observed-input {1} · safe-change {2}"
  ],
  "절감 시간": [
    "Time saved",
    "节省时间"
  ],
  "토큰 절감률": [
    "Token savings rate",
    "令牌节省率"
  ],
  "이번 검증 · 테스트 실행 기준": [
    "This verification · test execution basis",
    "本次验证 · 测试执行口径"
  ],
  "선택한 평가 · 개선 전 대비": [
    "Selected evaluation · versus before improvement",
    "所选评估 · 相比改进前"
  ],
  "실측 비교": [
    "Measured comparison",
    "实测对比"
  ],
  "동등한 완료 조건의 전체 작업 비교가 없습니다.": [
    "No whole-task comparison with equivalent completion conditions.",
    "没有具有等效完成条件的完整任务对比。"
  ],
  "전체 작업 기준 · 절대 토큰 수는 공개하지 않습니다.": [
    "Whole-task basis · absolute token counts are not disclosed.",
    "完整任务口径 · 不公开绝对令牌数。"
  ],
  "동일 완료 조건 · 전체 작업": [
    "Equivalent completion · whole task",
    "等效完成条件 · 完整任务"
  ],
  "전체 작업 효과": [
    "Whole-task effect",
    "完整任务效果"
  ],
  "미측정": [
    "Unmeasured",
    "未测量"
  ],
  "직접 비교한 작업 시작·종료 경계가 없습니다.": [
    "No directly compared task start and finish boundaries are available.",
    "没有可直接比较的任务开始与结束边界。"
  ],
  "작업 비교 선택": [
    "Select task comparison",
    "选择任务对比"
  ],
  "전체 작업 비교 상세": [
    "Whole-task comparison details",
    "完整任务对比详情"
  ],
  "선택한 비교 범위": [
    "Selected comparison scope",
    "所选对比范围"
  ],
  "작업 완료시간 차이": [
    "Task completion-time difference",
    "任务完成时间差"
  ],
  "표본 · 완료/실패/취소/미완료": [
    "Samples · complete/failed/cancelled/incomplete",
    "样本 · 完成/失败/取消/未完成"
  ],
  "빨라짐/변화 없음/느려짐": [
    "Faster/unchanged/slower",
    "变快/无变化/变慢"
  ],
  "사용자 개입 · 기준/개선": [
    "User interventions · baseline/improved",
    "用户干预 · 基准/改进"
  ],
  "확인된 사용자 수행시간 · 기준/개선": [
    "Observed user time · baseline/improved",
    "已观测用户耗时 · 基准/改进"
  ],
  "도구 호출 · 기준/개선": [
    "Tool calls · baseline/improved",
    "工具调用 · 基准/改进"
  ],
  "실패 후 수정 전 조회 · 기준/개선": [
    "Post-failure calls before mutation · baseline/improved",
    "失败后修改前查询 · 基准/改进"
  ],
  "수정·재검증 주기 · 기준/개선": [
    "Repair and revalidation cycles · baseline/improved",
    "修改与重新验证周期 · 基准/改进"
  ],
  "모델 왕복 · 기준/개선": [
    "Model round trips · baseline/improved",
    "模型往返 · 基准/改进"
  ],
  "활동 구간 · 기준/개선 (미분류)": [
    "Activity intervals · baseline/improved (unclassified)",
    "活动区间 · 基准/改进（未分类）"
  ],
  "활동 구간은 겹칠 수 있어 전체 작업시간으로 합산하지 않습니다. 숨은 추론은 미측정입니다.": [
    "Activity intervals may overlap and are not summed into whole-task time. Hidden reasoning is unmeasured.",
    "活动区间可能重叠，不会合计为完整任务时间。隐藏推理未测量。"
  ],
  "아직 비교 측정이 없습니다. 검증 구간 비교 JSON 또는 Phase 4 공개 작업 비교 JSON을 가져올 수 있습니다. 가져온 결과는 승인·재사용 권한이 아닙니다.": [
    "No comparison is loaded. You may import a verification-interval JSON or a Phase 4 public task-comparison JSON. Imported results grant no approval or reuse authority.",
    "尚未加载对比。可导入验证区间 JSON 或 Phase 4 公开任务对比 JSON。导入结果不授予批准或复用权限。"
  ],
  "비교 선택 필요": [
    "Select a comparison",
    "需要选择对比"
  ],
  "서로 다른 비교 범위가 {0}개 있습니다. 사용할 비교를 선택하세요.": [
    "There are {0} distinct comparison scopes. Select one to use.",
    "有 {0} 个不同的对比范围。请选择一个。"
  ],
  "전체 작업 비교를 가져오지 않았습니다.": [
    "No whole-task comparison has been imported.",
    "尚未导入完整任务对比。"
  ],
  "호스트 작업 시작·종료 경계를 사용할 수 없습니다.": [
    "Host task start and finish boundaries are unavailable.",
    "无法使用主机任务开始与结束边界。"
  ],
  "동등한 완료 조건을 확인할 수 없습니다.": [
    "Equivalent completion conditions could not be confirmed.",
    "无法确认等效完成条件。"
  ],
  "작업 비교 자료가 없습니다.": [
    "Task comparison data is unavailable.",
    "没有任务对比数据。"
  ],
  "완전한 usage 범위를 사용할 수 없습니다.": [
    "Complete usage scope is unavailable.",
    "无法使用完整的 usage 范围。"
  ],
  "비교 형식이 호환되지 않습니다.": [
    "The comparison format is incompatible.",
    "对比格式不兼容。"
  ],
  "비교 조건이 불완전합니다.": [
    "Comparison conditions are incomplete.",
    "对比条件不完整。"
  ],
  "첫 사용": [
    "First use",
    "首次使用"
  ],
  "준비된 반복 사용": [
    "Prepared repeat",
    "已准备的重复使用"
  ],
  "Click 미사용 대비": [
    "Versus no Click",
    "相比未使用 Click"
  ],
  "개선 전 Click 대비": [
    "Versus pre-improvement Click",
    "相比改进前 Click"
  ],
  "{0} 대비": [
    "Versus {0}",
    "相比 {0}"
  ],
  "{0}% 감소": [
    "{0}% decrease",
    "减少 {0}%"
  ],
  "0% · 변화 없음": [
    "0% · unchanged",
    "0% · 无变化"
  ],
  "{0}% 증가": [
    "{0}% increase",
    "增加 {0}%"
  ],
  "{0}% 빨라짐": [
    "{0}% faster",
    "加快 {0}%"
  ],
  "{0}% 느려짐": [
    "{0}% slower",
    "变慢 {0}%"
  ],
  "빨라짐": [
    "Faster",
    "变快"
  ],
  "변화 없음": [
    "Unchanged",
    "无变化"
  ],
  "느려짐": [
    "Slower",
    "变慢"
  ],
  "전체 작업 동등 완료 표본 {0}개 · 합산 비율": [
    "{0} equivalent whole-task samples · aggregate ratio",
    "{0} 个等效完整任务样本 · 汇总比率"
  ],
  "측정 불가: {0}": [
    "Unmeasured: {0}",
    "无法测量：{0}"
  ],
  "{0} · {1} · {2} · {3} · 표본 {4}개": [
    "{0} · {1} · {2} · {3} · {4} samples",
    "{0} · {1} · {2} · {3} · {4} 个样本"
  ],
  "느려진 작업 {0}개 · 실패 {1}개 · 취소 {2}개 · 미완료 {3}개": [
    "Slower tasks {0} · failed {1} · cancelled {2} · incomplete {3}",
    "变慢任务 {0} · 失败 {1} · 取消 {2} · 未完成 {3}"
  ],
  "사용자 개입 {0} → {1}": [
    "User interventions {0} → {1}",
    "用户干预 {0} → {1}"
  ],
  "로컬 원자료 참조: {0} (공유본 제외)": [
    "Local source reference: {0} (excluded from shares)",
    "本地原始资料引用：{0}（不含在共享内容中）"
  ],
  "작업 비교 자료 제거": [
    "Remove task comparison",
    "移除任务对比"
  ],
  "공개 작업 비교 · 절대 토큰 수 제외": [
    "Public task comparison · absolute token counts excluded",
    "公开任务对比 · 不含绝对令牌数"
  ],
  "작업 비교를 불러왔습니다. {0}": [
    "Task comparison loaded. {0}",
    "已加载任务对比。{0}"
  ],
  "기준 {0} / 개선 {1}": [
    "Baseline {0} / improved {1}",
    "基准 {0} / 改进 {1}"
  ],
  "{0}개 / {1}개": [
    "{0} / {1}",
    "{0} 个 / {1} 个"
  ],
  "표본 {0} · 완료 {1} / 실패 {2} / 취소 {3} / 미완료 {4}": [
    "Samples {0} · complete {1} / failed {2} / cancelled {3} / incomplete {4}",
    "样本 {0} · 完成 {1} / 失败 {2} / 取消 {3} / 未完成 {4}"
  ],
  "{0} / {1} (미분류 {2} / {3})": [
    "{0} / {1} (unclassified {2} / {3})",
    "{0} / {1}（未分类 {2} / {3}）"
  ],
  "선택한 비교의 전체 작업시간을 직접 측정했습니다. 테스트 절감 시간과 합산하지 않습니다.": [
    "Whole-task time was measured directly for the selected comparison. It is not combined with test time saved.",
    "已直接测量所选对比的完整任务时间，不与测试节省时间相加。"
  ],
  "작업 완료시간은 미측정입니다: {0}": [
    "Task completion time is unmeasured: {0}",
    "任务完成时间未测量：{0}"
  ],
  "절감 시간: {0} [{1}]{2}.": [
    "Time saved: {0} [{1}]{2}.",
    "节省时间：{0} [{1}]{2}。"
  ],
  "절감 시간: {0} · {1}": [
    "Time saved: {0} · {1}",
    "节省时间：{0} · {1}"
  ],
  "토큰 절감률: {0} · {1}": [
    "Token savings rate: {0} · {1}",
    "令牌节省率：{0} · {1}"
  ],
  "전체 작업 효과: {0} · {1}": [
    "Whole-task effect: {0} · {1}",
    "完整任务效果：{0} · {1}"
  ],
  "{0} · 전체 작업 기준 · 절대 토큰 수는 공개하지 않습니다.": [
    "{0} · whole-task basis · absolute token counts are not disclosed.",
    "{0} · 完整任务口径 · 不公开绝对令牌数。"
  ],
  "{0} 갱신": [
    "Updated {0}",
    "更新于 {0}"
  ],
  "접근 토큰 없음": [
    "No access token",
    "无访问令牌"
  ],
  "연결 끊김": [
    "Disconnected",
    "连接已断开"
  ],
  "검증은 더 스마트하게, 개발은 더 빠르게.": [
    "Smarter checks. Faster development.",
    "验证更智能，开发更高效。"
  ],
  "실행한 검사와 재사용한 근거를 모아, 이번 작업에서 달라진 점을 확인하세요.": [
    "See what changed with a clear view of executed checks and reused results.",
    "汇总已执行的检查与复用依据，查看本次任务的变化。"
  ],
  "실제 실행 기록": [
    "Actual execution records",
    "实际执行记录"
  ],
  "확인 가능한 재사용": [
    "Traceable reuse",
    "可核实的复用"
  ],
  "근거 있는 비교": [
    "Evidence-based comparisons",
    "有依据的对比"
  ],
  "측정 안내": [
    "Measurement guide",
    "测量说明"
  ],
  "이번 작업의 효과": [
    "The effect of this work",
    "本次任务的效果"
  ],
  "검증 기록과 전체 작업 비교를 함께 확인하세요.": [
    "Verification results, alongside whole-task comparisons.",
    "同时查看验证记录与完整任务对比。"
  ],
  "측정 내역": [
    "Measurement breakdown",
    "测量明细"
  ],
  "테스트 실행에서 절감": [
    "Test execution saved",
    "节省的测试执行时间"
  ],
  "검증 실행 현황": [
    "Verification overview",
    "验证执行概况"
  ],
  "전체와 이번 실행, 한눈에": [
    "All checks and this run, at a glance",
    "全部检查与本次执行，一目了然"
  ],
  "검증 묶음별 실행 상태": [
    "Execution status by group",
    "各验证组的执行状态"
  ],
  "테스트 실행 시간": [
    "Test execution time",
    "测试执行时间"
  ],
  "재사용 근거": [
    "Reuse evidence",
    "复用依据"
  ],
  "마지막 성공": [
    "Last passed",
    "上次通过"
  ],
  "변경 영향": [
    "Change impact",
    "变更影响"
  ],
  "작업 흐름": [
    "Activity",
    "任务流程"
  ],
  "실제로 기록된 검증 요청을 확인하세요.": [
    "Recorded verification requests, in order.",
    "按顺序查看实际记录的验证请求。"
  ],
  "전체 작업 이력": [
    "Full activity history",
    "完整任务历史"
  ],
  "선택한 묶음의 실행·재사용 근거": [
    "Evidence for the selected group",
    "所选验证组的执行与复用依据"
  ],
  "더 적은 반복, 더 많은 진짜 개발에 집중하세요.": [
    "Less repetition. More focus on development.",
    "减少重复，专注真正的开发。"
  ],
  "관찰한 결과와 측정 범위를 함께 표시합니다.": [
    "Observed results with their measurement scope.",
    "同时呈现观测结果及测量范围。"
  ],
  "검증 완료": [
    "Verification complete",
    "验证完成"
  ],
  "정책 범위 내": [
    "Within policy",
    "符合策略范围"
  ],
  "입력 변경 없음": [
    "Inputs unchanged",
    "输入未变化"
  ],
  "현재 조건 일치": [
    "Current conditions match",
    "当前条件匹配"
  ],
  "재검증 필요": [
    "Recheck required",
    "需要重新验证"
  ],
  "확인 필요": [
    "Needs verification",
    "待验证"
  ],
  "시간 기록 없음": [
    "Time unavailable",
    "无时间记录"
  ],
  "기록된 검증 요청이 없습니다.": [
    "No verification requests recorded.",
    "暂无验证请求记录。"
  ],
  "순작업시간": [
    "Total task time",
    "总任务时间"
  ],
  "동등한 완료 조건 · 전체 작업 기준": [
    "Same completion criteria · Whole task",
    "相同完成条件 · 整项任务"
  ],
  "결과 확보": [
    "Results available",
    "已取得结果"
  ]
});
  // UI language only. Canonical measurements, decisions and user-authored text stay unchanged.
  const LANGUAGE_STORAGE_KEY='click.dashboard.language';
  const normalizeLocale=value=>['ko','en','zh-CN'].includes(value)?value:'ko';
  let locale='ko';
  try { locale=normalizeLocale(globalThis.localStorage?.getItem(LANGUAGE_STORAGE_KEY)); } catch (_) {}
  const localeTag=()=>({'ko':'ko-KR','en':'en-US','zh-CN':'zh-CN'}[locale]);
  function msg(strings,...values) {
    const key=Array.isArray(strings)
      ? strings.map((part,index)=>(index?`{${index-1}}`:'')+part).join('')
      : String(strings ?? '');
    const template=locale==='ko'?key:(MESSAGES[key]?.[locale==='en'?0:1] ?? key);
    // Values are inserted once, never reinterpreted as translation keys or HTML.
    return template.replace(/\{(\d+)\}/g,(match,index)=>Number(index)<values.length?String(values[Number(index)]):match);
  }
  function localized(values) {
    return new Proxy(values,{get(target,key){
      const value=target[key];
      return Array.isArray(value)?value.map(item=>msg(item)):typeof value==='string'?msg(value):value;
    }});
  }
  let connectionState='연결 중';
  function setConnection(key) {
    connectionState=key;
    $('connection').textContent=msg(key);
    document.querySelector('.live').classList.toggle('ok',key==='연결됨');
  }
  function applyStaticLanguage() {
    document.documentElement.setAttribute('lang',locale);
    document.title=msg('Click Incremental Verification');
    document.querySelectorAll('[data-i18n]').forEach(node=>{node.textContent=msg(node.getAttribute('data-i18n'));});
    for(const attribute of ['aria-label','title']) {
      document.querySelectorAll(`[data-i18n-${attribute}]`).forEach(node=>node.setAttribute(attribute,msg(node.getAttribute(`data-i18n-${attribute}`))));
    }
    $('languageSelect').value=locale;
  }
  function setLanguage(value) {
    locale=normalizeLocale(value);
    try { globalThis.localStorage?.setItem(LANGUAGE_STORAGE_KEY,locale); } catch (_) {}
    const previousConnection=connectionState;
    applyStaticLanguage();
    if(snapshot)render(snapshot);
    setConnection(previousConnection);
    $('exportStatus').textContent='';
    return locale;
  }

  const $ = id => document.getElementById(id);
  const OUTPUT_LABELS = localized({
    omitted:'절감 시간',
    full:'동일 묶음 전체 순차 실행 예상',
    executed:'이번 테스트 실행',
    reduction:'테스트 명령 실행 구간 감소',
    basis:['과거 실행 기록 기반 추정','동일 묶음·순차 실행 기준','관리비용 별도']
  });
  const decimal = value => Number(value.toFixed(2)).toLocaleString(localeTag());
  const fmt = ms => {
    if (!Number.isFinite(ms)) return msg('측정 정보 없음');
    if (ms < 1000) return `${decimal(ms)} ms`;
    if (ms < 60000) return msg`${decimal(ms / 1000)}초`;
    const rounded = Math.round(ms / 1000);
    const minutes = Math.floor(rounded / 60);
    const seconds = rounded % 60;
    return seconds ? msg`${minutes}분 ${seconds}초` : msg`${minutes}분`;
  };
  const estimatedDuration = ms => Number.isFinite(ms) ? msg`약 ${fmt(ms)}` : msg('측정 정보 없음');
  const signedDuration = ms => Number.isFinite(ms) ? `${ms >= 0 ? '+' : '−'}${fmt(Math.abs(ms))}` : msg('측정 정보 없음');
  const percent = ratio => Number.isFinite(ratio) ? msg`약 ${decimal(100 * ratio)}%` : msg('측정 정보 없음');
  const count = value => Number.isInteger(value) ? String(value) : msg('알 수 없음');
  const token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
  history.replaceState(null, '', location.pathname);
  let selected = '';
  let sourceFilter = 'all';
  let selectedTaskIdentity = '';
  let refreshInFlight = false;
  let snapshot = null;
  let selectedBatch = '';
  let activeBatch = null;
  let activeSummary = null;
  let activeSavings = null;
  let comparison = null;
  let taskEfficiency = null;
  let selectedTaskComparison = '';
  let taskEvaluationLocalRef = '';
  let taskEfficiencyImported = false;
  let lastSnapshotSignature = '';
  const statusText = localized({
    planned: '실행 예정', 'reuse-pending': '재사용 예정 · 미적용', running: '실행 중',
    passed: '통과', failed: '실패', interrupted: '중단 · 일부 결과 미확정',
    'not-run': '미실행', reused: '재사용 적용', unknown: '측정 정보 없음',
    rejected: '실행 전 거부', incomplete: '미확정', evidence: 'Evidence', staged: '승인 대기', approved: '승인됨', none: '활성 작업 없음'
  });
  const setupStatusText = localized({
    unconfigured:'미설정', 'selection-required':'명령 선택 필요',
    'approval-required':'승인 대기', 'review-required':'검토 필요',
    'commit-required':'커밋 필요', 'baseline-required':'기준 실행 필요',
    'sharding-ready':'샤딩 준비됨 · 재사용 불가',
    'reuse-ready':'샤딩·재사용 준비됨', unsupported:'미지원', blocked:'차단됨'
  });
  const outcomeText = localized({
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
  });

  const executionLabels = localized({
    'run': ['재실행', 'rerun'],
    'not-evaluable': ['재실행', 'rerun'],
    'reuse-exact': ['재사용', 'reused'],
    'reuse-dependency': ['재사용', 'reused'],
    'reuse-safe-change': ['재사용', 'reused'],
    'not-planned': ['대기', 'pending']
  });
  const reasonText = localized({
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
  });
  const savingsReasonText = localized({
    'request-not-finalized':'검증 요청의 종료가 아직 확인되지 않았습니다.',
    'request-not-passed':'검증 요청이 정상 완료되지 않았습니다.',
    'scope-incomplete':'요청한 모든 묶음의 최종 상태가 확정되지 않았습니다.',
    'executed-duration-missing':'실제로 시작한 묶음 중 실행시간이 없는 항목이 있습니다.',
    'reused-duration-sample-missing':'재사용된 묶음 중 과거 성공 실행시간이 없는 항목이 있습니다.',
    'legacy-timing-context-missing':'구형 시간 기록에는 현재 추정에 필요한 측정 조건이 없습니다.',
    'reused-duration-sample-incompatible':'과거 시간 기록의 묶음·검사·측정 조건이 현재 추정과 맞지 않습니다.',
    'sequential-comparison-invalid':'같은 묶음의 순차 실행으로 비교할 시간 조건이 완전하지 않습니다.',
    'zero-denominator':'전체 실행 예상시간이 0이라 감소율을 계산하지 않습니다.'
  });
  const inputStatus = localized({
    'current-observed': '현재 관찰됨',
    'changed': '변경됨',
    'baseline-only': '이전 baseline에만 존재',
    'newly-observed': '현재 새로 관찰됨'
  });

  function outcomePresentation(batch, summary = {}, savings = {}) {
    const total = summary.total_source_count;
    const executed = summary.executed_source_count;
    const reused = summary.authoritative_reuse_count;
    const timedReused = savings.coverage?.timed_reused_source_count;
    const actualReused = savings.coverage?.actual_reused_source_count;
    const complete = Boolean(batch && batch.status === 'passed' && batch.finished_at != null && savings.scope_complete === true);
    const reasons = Array.isArray(savings.reason_codes) ? savings.reason_codes : [];
    const reason = reasons.map(code => savingsReasonText[code]).filter(Boolean).join(' ');
    let state = 'first-run';
    let heroTitle = OUTPUT_LABELS.omitted;
    let heroValue = msg('기준 실행 대기');
    let heroIsEstimate = false;
    let heroStatus = msg('재사용할 기준 결과를 만드는 중입니다.');
    let summaryText = msg('아직 이 작업의 검증 요청 기록이 없습니다.');
    if (batch) {
      if (batch.status === 'planned') {
        heroValue = msg('실행 준비 중');
        heroStatus = msg('재사용할 기준 결과를 확인하는 중입니다. 예정된 재사용은 실적에 포함하지 않습니다.');
        summaryText = msg`${count(total)}개 검증 묶음의 실제 결과를 기다리고 있습니다.`;
      } else if (batch.status === 'running') {
        state = 'running'; heroValue = msg('검증 진행 중');
        heroStatus = msg('아직 요청이 끝나지 않았습니다. 아래는 현재까지 관찰한 결과입니다.');
        summaryText = msg`실제 시작 ${count(executed)}개 · 재사용 적용 ${count(reused)}개 · 요청 ${count(total)}개`;
      } else if (batch.status === 'interrupted') {
        state = 'cancelled'; heroValue = msg('검증 중단');
        heroStatus = msg('취소·중단된 요청입니다. 완료되지 않은 묶음은 재사용에 포함하지 않습니다.');
        summaryText = msg`실제 시작 ${count(executed)}개 · 재사용 적용 ${count(reused)}개 · 요청 ${count(total)}개`;
      } else if (['failed','rejected','incomplete'].includes(batch.status)) {
        state = 'failed'; heroValue = batch.status === 'failed' ? msg('검증 실패') : batch.status === 'rejected' ? msg('실행 전 거부') : msg('결과 미확정');
        heroStatus = msg('정상 완료되지 않은 요청입니다. 관찰한 상태와 시간만 보존합니다.');
        summaryText = msg`실제 시작 ${count(executed)}개 · 재사용 적용 ${count(reused)}개 · 요청 ${count(total)}개`;
      } else if (complete && reused === 0) {
        state = 'no-reuse'; heroValue = '0 ms';
        heroStatus = msg('실제 재사용 없음 · 피한 재실행 비용 없음');
        summaryText = msg`이번에는 ${count(total)}개 모두 다시 검증했습니다.`;
      } else if (complete && savings.omitted_test_execution_status === 'estimated') {
        state = executed === 0 ? 'all-reuse' : 'partial-execution';
        heroValue = estimatedDuration(savings.omitted_test_execution_ms); heroIsEstimate = true;
        heroStatus = msg`${count(timedReused)}/${count(actualReused)}개 재사용 묶음의 적합한 과거 실행 기록 기반`;
        summaryText = msg`전체 ${count(total)}개 중 ${count(executed)}개만 다시 실행 · ${count(reused)}개 결과 재사용`;
      } else if (complete) {
        state = savings.omitted_test_execution_status === 'partial' ? 'partial-timing' : 'unmeasured';
        heroValue = msg`${count(reused)} / ${count(total)}개`;
        heroStatus = state === 'partial-timing'
          ? msg`시간 근거 ${count(timedReused)}/${count(actualReused)}개 · 부분 추정 ${estimatedDuration(savings.omitted_test_execution_ms)} [추정]`
          : msg('재사용은 확인됐습니다. 적합한 시간 표본이 없어 시간은 미측정입니다.');
        summaryText = msg`${count(total)}개 중 ${count(reused)}개 재실행을 피했습니다. 실제 실행 ${count(executed)}개.`;
      } else {
        state = 'incomplete'; heroValue = msg('요청 미완료');
        heroStatus = reason || msg('요청 범위의 최종 상태가 확정되지 않았습니다.');
        summaryText = msg('완료되지 않은 검증 요청은 절감 성과로 표시하지 않습니다.');
      }
    }
    const full = savings.full_sequential_test_execution_estimate_ms;
    const current = savings.executed_test_execution_ms;
    const ratio = savings.test_execution_reduction_ratio;
    const comparisonReady = Boolean(complete && savings.full_sequential_test_execution_estimate_status === 'estimated'
      && savings.executed_test_execution_status === 'measured' && Number.isFinite(full) && Number.isFinite(current) && full > 0 && Number.isFinite(ratio));
    const currentPercent = comparisonReady ? Math.max(0, Math.min(100, 100 * current / full)) : null;
    const guidance = !complete ? heroStatus : !comparisonReady
      ? msg('횟수는 실제 결과입니다. 시간 조건이 불완전하거나 전체 예상이 0이면 시간 비율 그래프를 표시하지 않습니다.')
      : executed === 0 ? msg('명령 실행 구간은 0입니다. 전체 요청 대기시간이 0이라는 뜻은 아닙니다.')
      : reused === 0 ? msg('전체 묶음을 실행했습니다. 재사용에 따른 실행량 감소는 없습니다.')
      : msg('같은 0 시작 시간 축 · 빗금 구간은 과거 기록에 기반한 회피 비용 추정입니다.');
    return {state, heroTitle, heroValue, heroIsEstimate, heroStatus, summaryText, guidance, complete,
      resultText: complete ? msg`요청된 검증 묶음 ${count(total)}/${count(total)} 결과 확보` : '',
      comparisonReady, comparisonState: comparisonReady ? msg('동일 시간 축') : (complete ? msg('횟수 비교') : msg('요청 미완료')),
      fullText: Number.isFinite(full) ? msg`${estimatedDuration(full)} [추정]` : msg('측정 정보 없음'),
      executedText: Number.isFinite(current) ? (savings.executed_test_execution_status === 'partial' ? msg`부분 기록 합계 ${fmt(current)} [실측]` : msg`${fmt(current)} [실측]`) : msg('측정 정보 없음'),
      reductionText: comparisonReady ? msg`${percent(ratio)} [추정]` : msg('측정 정보 없음'), currentPercent,
      coverageText: Number.isInteger(timedReused) && Number.isInteger(actualReused) ? `${timedReused}/${actualReused}` : msg('알 수 없음')};
  }

  // One positional rendering path for live and standalone reports; no metric recomputation.
  function groupBlocks(doc, root, sources, baseline = false, onSelect = null) {
    root.replaceChildren();
    sources.forEach((item,index) => {
      const status = item.status || item.execution_status;
      const kind = baseline ? '' : status === 'reused' ? 'reused' : ['failed','interrupted'].includes(status) ? 'problem' : item.started ? 'executed' : 'pending';
      const symbol = baseline ? '' : kind === 'reused' ? '↺ ' : kind === 'problem' ? '! ' : kind === 'executed' ? '● ' : '· ';
      const block = doc.createElement(onSelect && !baseline ? 'button' : 'span');
      block.className = `group-block ${kind}`;
      const number = doc.createElement('span'); number.textContent=String(index+1).padStart(2,'0');
      block.append(number);
      if(!baseline){const mark=doc.createElement('span');mark.className='group-block-symbol';mark.setAttribute('aria-hidden','true');mark.textContent=symbol.trim();block.append(mark);}
      block.setAttribute('aria-label', `${index+1}. ${item.label} · ${baseline ? msg('전체 재실행 기준의 실행 대상') : statusText[status] || msg('미확정')}`);
      block.title = `${item.label} · ${baseline ? msg('전체 재실행 기준') : statusText[status] || msg('미확정')}`;
      if (onSelect && !baseline) {block.type='button';block.id=`block-${index}`;block.onclick=()=>onSelect(item);}
      root.append(block);
    });
  }

  function renderOutcome(batch, summary, savings) {
    const view = outcomePresentation(batch, summary, savings);
    document.querySelector('.savings-hero').dataset.state=view.state;
    $('title').textContent = view.heroTitle===msg('절감 시간')?msg('테스트 실행에서 절감'):view.heroTitle;
    $('estimatedAvoided').textContent = view.heroValue;
    $('heroEstimateBadge').hidden = !view.heroIsEstimate;
    $('estimateCoverage').textContent = view.heroStatus;
    $('executionStatus').textContent = view.complete ? msg('검증 완료') : statusText[batch?.status] || msg('판정 중');
    $('executionStatus').className = `pill ${view.complete?'reused':['failed','interrupted','rejected'].includes(batch?.status)?'rerun':'pending'}`;
    $('batchHeadline').textContent = view.summaryText;
    $('batchHeadline').hidden = view.complete;
    $('verifiedChecks').textContent = view.complete ? `${count(summary.total_source_count)}/${count(summary.total_source_count)}` : '—';
    $('resultCoverage').textContent = view.resultText;
    $('showReused').disabled = !summary.authoritative_reuse_count;
    $('currentChecks').textContent = count(summary.total_source_count);
    $('executedChecks').textContent = count(summary.executed_source_count);
    $('reusedChecks').textContent = count(summary.authoritative_reuse_count);
    $('reusedChecks').setAttribute('aria-label',msg`재사용한 검증 묶음 ${count(summary.authoritative_reuse_count)}개 보기`);
    $('timingCoverage').textContent = view.coverageText;
    $('executionDetail').textContent = msg`통과 ${count(summary.passed_source_count)} · 실패 ${count(summary.failed_source_count)} · 중단 ${count(summary.interrupted_source_count)} · 미실행 ${count(summary.not_run_source_count)} · 대기/미확정 ${count(summary.pending_source_count)}`;
    $('comparisonState').textContent = view.comparisonState;
    $('comparisonState').className = `pill ${view.complete ? 'reused' : 'pending'}`;
    $('executionComparison').hidden = !view.comparisonReady;
    $('fullEstimate').textContent = view.fullText;
    $('executedDuration').textContent = view.executedText;
    $('reductionRate').textContent = view.reductionText;
    $('comparisonGuidance').textContent = view.guidance;
    $('comparisonGuidance').hidden = view.comparisonReady;
    if (view.state === 'no-reuse') $('comparisonGuidance').textContent += ' '+[...new Set((batch?.sources || []).map(item=>reasonFor({...item,execution_status:item.status})))].slice(0,2).join(' ');
    $('fullBar').style.width = view.comparisonReady ? '100%' : '0';
    $('executedBar').style.width = view.comparisonReady ? `${view.currentPercent}%` : '0';
    $('avoidedBar').style.width = view.comparisonReady ? `${100-view.currentPercent}%` : '0';
    $('avoidedSegment').hidden = !view.comparisonReady;
    $('avoidedSegment').textContent = msg`↺ 피한 재실행 비용 ${estimatedDuration(savings.omitted_test_execution_ms)} [추정]`;
    $('fullBar').setAttribute('aria-label', `${OUTPUT_LABELS.full} ${view.fullText}`);
    $('executedBar').setAttribute('aria-label', `${OUTPUT_LABELS.executed} ${view.executedText}`);
    const sources = batch?.sources || [];
    $('groupComparison').hidden = !sources.length;
    $('allGroupCount').textContent = msg`${count(summary.total_source_count)}개`;
    $('actualGroupCount').textContent = msg`실행 ${count(summary.executed_source_count)} · 재사용 ${count(summary.authoritative_reuse_count)}`;
    groupBlocks(document, $('baselineBlocks'), sources, true);
    groupBlocks(document, $('actualBlocks'), sources, false, item=>{
      sourceFilter='all'; selected=`source:${item.source_key.slice(0,16)}`;
      renderSources(batchView(snapshot,activeBatch));
      $('explanationSection').open=true; $('explanationSection').scrollIntoView({block:'center'}); $('explanationSection').focus({preventScroll:true});
    });
    $('blockScope').textContent = msg`같은 묶음을 모두 실행하는 기준이며 관찰한 이전 실행이 아닙니다.${Number.isInteger(summary.total_source_count) && sources.length < summary.total_source_count ? msg` 상세 기록 ${sources.length}/${summary.total_source_count}개 · 나머지는 미확정` : ''}`;
    return view;
  }

  function reasonFor(source) {
    if (source.execution_reason_code === 'user-cancelled' && source.execution_status === 'not-run') return msg('실행 전에 취소되어 시작하지 않았습니다. 이전 계약의 승인이나 실행 권한은 이어받지 않습니다.');
    if (outcomeText[source.execution_reason_code]) return outcomeText[source.execution_reason_code];
    if (source.execution_status === 'unknown') return msg('실제 실행 기록이 없는 이전 데이터입니다. 계획을 실행 실적으로 표시하지 않습니다.');
    const planned = source.execution_status === 'planned' || source.execution_status === 'reuse-pending';
    // Shadow paths are not an explanation of an authoritative dependency decision.
    const text = reasonText[source.reason_code] || msg('판정 근거 정보가 없습니다.');
    return planned ? (source.execution_decision === 'reuse-exact' ? msg('현재 조건과 일치하는 기존 통과 결과의 재사용을 계획했습니다. 아직 적용하지 않았습니다.') : source.execution_decision === 'reuse-dependency' ? msg('관찰된 입력의 판정에 따른 재사용 계획입니다. 아직 적용하지 않았습니다.') : source.execution_decision === 'reuse-safe-change' ? msg('기존 안전 변경 정책에 따른 재사용 계획입니다. 아직 적용하지 않았습니다.') : msg('현재 조건에 따라 실제 검증을 실행할 계획입니다. 아직 시작하지 않았습니다.')) : text;
  }

  function explain(source) {
    selected = source.id;
    const label = statusText[source.execution_status] || msg('측정 정보 없음');
    $('whyTitle').textContent = `${source.label} · ${label}`;
    $('whyBody').textContent = `${reasonFor(source)} ${msg(source.next_action || '')}`;
    const reused = source.execution_status === 'reused';
    const successor = reused && Boolean(source.reuse_origin);
    $('originName').textContent = successor
      ? msg('이전 계약의 통과 결과를 현재 계약에서 다시 판정해 적용했습니다.')
      : reused
        ? msg('같은 계약 안의 유효한 통과 결과를 재사용했습니다.')
        : ['passed','failed','interrupted'].includes(source.execution_status)
          ? msg('이번 요청에서 실제 실행한 결과입니다.')
          : msg('아직 실행·재사용 결과가 없습니다.');
    $('lineageSummary').textContent = successor
      ? msg('이전 작업 → 원본 성공 실행 → 현재 적용')
      : reused
        ? msg('같은 계약의 성공 실행 → 현재 적용')
        : msg('이번 요청의 실제 실행 흐름');
    const observedAt = source.duration_baseline?.observed_at;
    const observedLabel = Number.isInteger(observedAt)
      ? new Date(observedAt * 1000).toLocaleString(localeTag())
      : msg('관측 시점 미측정');
    const steps = successor ? [
      msg`이전 작업: ${source.origin_name || msg('보관 범위 밖의 이전 작업')}`,
      msg`원본 성공 실행: ${source.origin_check_label || source.label} · ${fmt(source.duration_baseline?.duration_ms)} · ${observedLabel}`,
      msg`현재 적용: 변경 ${source.current_revision}에서 재사용 판정 통과`,
    ] : reused ? [
      msg('현재 작업 안의 이전 성공 실행'),
      msg`원본 성공 실행: ${source.origin_check_label || source.label} · ${fmt(source.duration_baseline?.duration_ms)} · ${observedLabel}`,
      msg`현재 적용: 변경 ${source.current_revision}에서 같은 계약 재사용`,
    ] : [
      msg`현재 요청: 변경 ${source.current_revision}`,
      msg`실제 결과: ${label} · source-command 구간 ${fmt(source.duration_ms)}`,
      msg`판정: ${reasonFor(source)}`,
    ];
    $('lineageSteps').replaceChildren(...steps.map(text => {
      const item = document.createElement('li');
      item.textContent = text;
      return item;
    }));
    const originId = source.reuse_origin?.contract_id || source.reuse_origin?.evidence_session_id || source.duration_baseline?.origin_task?.id;
    const tags = [
      msg`현재 revision ${source.current_revision}`,
      source.previous_revision >= 0 ? msg`이전 성공 revision ${source.previous_revision}` : msg('이전 성공 없음'),
      msg`계획 ${executionLabels[source.execution_decision]?.[0] || msg('없음')} · 실제 ${label}`,
      msg`현재 실행 구간 ${fmt(source.duration_ms)}`,
      source.duration_baseline ? msg`원본 성공 표본 ${source.duration_baseline.sample_count}개 · revision ${source.duration_baseline.revision} · ${fmt(source.duration_baseline.duration_ms)} · ${source.duration_baseline.measurement_scope || msg('측정 구간 정보 없음')}` : msg('과거 시간 표본 없음'),
      originId ? msg`원본 작업 ID ${originId}` : msg('원본 작업 ID 없음'),
      source.duration_baseline?.batch_id ? msg`원본 성공 배치 ID ${source.duration_baseline.batch_id}` : msg('원본 성공 배치 ID 없음'),
      source.duration_baseline?.source_key ? msg`원본 source ID ${source.duration_baseline.source_key}` : msg('원본 source ID 없음'),
      source.reuse_origin ? msg`현재 재사용 출처 배치 ID ${source.reuse_origin.batch_id} · 출처 revision ${source.reuse_origin.origin_revision}` : msg('현재 계약 안의 근거'),
      msg`정확한 검사 결합 ${source.check_digest || source.duration_baseline?.check_digest || msg('정보 없음')}`,
      source.duration_baseline?.timing_binding_digest ? msg`시간 조건 결합 ${source.duration_baseline.timing_binding_digest}` : msg('시간 조건 결합 없음'),
      msg`판정 식별자 ${source.reason_code || msg('없음')}`,
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
    const shown = data.sources.filter(source => sourceFilter === 'reused' ? source.execution_status === 'reused' : sourceFilter === 'executed' ? source.started === true : true);
    [['filterAll','all'],['filterReused','reused'],['filterExecuted','executed']].forEach(([id,filter]) => $(id).setAttribute('aria-pressed',String(sourceFilter===filter)));
    $('filterStatus').textContent = msg`${shown.length}/${data.sources.length}개 표시${!shown.length ? msg(' · 해당 결과가 없습니다.') : ''}`;
    shown.forEach(source => {
      const row=document.createElement('tr');row.className='source-row';
      const nameCell=document.createElement('td'),button=document.createElement('button');
      button.className='source';button.dataset.id=source.id;button.type='button';button.textContent=source.label;
      button.setAttribute('aria-controls','explanationSection');
      button.onclick=()=>{explain(source);$('explanationSection').open=true;$('explanationSection').scrollIntoView({block:'nearest'});};
      nameCell.append(button);row.append(nameCell);
      const lastPassed=document.createElement('td');
      const observedAt=source.duration_baseline?.observed_at;
      lastPassed.textContent=Number.isSafeInteger(observedAt)&&observedAt>0?new Date(observedAt*1000).toLocaleString(localeTag(),{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):msg('시간 기록 없음');
      row.append(lastPassed);
      const impact=document.createElement('td');
      const reason=source.reason_code||'';
      impact.textContent=reason.includes('safe-change')&&source.execution_status==='reused'?msg('정책 범위 내'):reason.includes('dependencies-unchanged')&&source.execution_status==='reused'?msg('입력 변경 없음'):source.execution_status==='reused'&&['same-revision-receipt-current','successor-evidence-current'].includes(reason)?msg('현재 조건 일치'):source.started?msg('실제 실행'):msg('확인 필요');
      impact.title=reasonFor(source);row.append(impact);
      const result=document.createElement('td'),pill=document.createElement('span');
      const state=source.execution_status;
      pill.className=`pill ${state==='reused'||state==='passed'?'reused':['failed','interrupted'].includes(state)?'rerun':'pending'}`;
      pill.textContent=`${state==='reused'?'↺ ':state==='passed'?'✓ ':['failed','interrupted'].includes(state)?'! ':''}${statusText[state]||msg('미확정')}`;
      result.append(pill);row.append(result);root.append(row);
    });
    $('sourceCount').textContent = String(data.sources.length);
    const current = shown.find(source => source.id === selected) || shown[0];
    if (current) {
      explain(current);
    } else {
      selected = '';
      $('whyTitle').textContent = data.sources.length ? msg('해당 결과가 없습니다') : msg('아직 요청된 검증이 없습니다');
      $('originName').textContent='';$('lineageSteps').replaceChildren();$('limits').replaceChildren();
      $('whyBody').textContent = msg('검증 계획이 생성되면 실행과 재사용 이유가 여기에 표시됩니다.');
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
      empty.textContent = msg('이 과거 배치의 입력 그래프는 보관하지 않습니다. 최신 배치에서 현재 Evidence Map을 볼 수 있습니다.');
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
    $('mapMeta').textContent = hidden > 0 ? msg`입력 ${inputNodes.length}개 표시 · ${hidden}개 생략` : msg`입력 ${inputNodes.length}개`;
  }

  function batchView(data, batch) {
    if (!batch) return {...data,sources:data.sources || []};
    const current = batch.batch_id === data.history.current_batch_id;
    return {...data, sources: batch.sources.map(item => {
      const id = `source:${item.source_key.slice(0,16)}`;
      const source = current ? data.sources.find(source => source.id === id) : null;
      const originBatch = item.reuse_origin ? data.batches.find(previous=>previous.task?.id === (item.reuse_origin.contract_id || item.reuse_origin.evidence_session_id)) : null;
      return {...(source || {input_count:0, changed_inputs:[], shadow_limitations:[], observer_status:'unavailable'}),
        id, label:item.label, status:item.status, execution_status:item.status, started:item.started, completed:item.completed,
        execution_decision:item.decision || 'not-planned', reason_code:item.reason_code,
        execution_reason_code:item.execution_reason_code, current_revision:item.current_revision,
        previous_revision:item.previous_revision, duration_ms:item.duration_ms, duration_baseline:item.duration_baseline,
        authority_source:item.authority_source, reuse_origin:item.reuse_origin, check_digest:item.check_digest,
        origin_name:source?.origin_name || originBatch?.task?.name || msg('보관 범위 밖의 이전 작업'),
        origin_check_label:source?.origin_check_label || originBatch?.sources.find(previous=>previous.source_key===item.source_key)?.label || item.label};
    }), map: current ? data.map : {nodes:[],edges:[]}};
  }

  function renderBatch(data) {
    const batches = data.batches || [];
    if (!batches.some(batch => batch.batch_id === selectedBatch)) selectedBatch = '';
    activeBatch = batches.find(batch => batch.batch_id === (selectedBatch || data.history?.current_batch_id)) || null;
    const select = $('batchSelect');
    select.replaceChildren();
    if (!activeBatch) {const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent=msg('현재 계약 · 검증 요청 기록 없음');placeholder.selected=true;select.append(placeholder);}
    [...batches].reverse().forEach(batch => {
      const option = document.createElement('option');
      option.value = batch.batch_id;
      option.textContent = msg`${batch.task?.name || msg('이전 검증')} · ${new Date(batch.timestamp*1000).toLocaleString(localeTag())} · 변경 ${batch.current_revision} · ${statusText[batch.status]}`;
      option.selected = batch.batch_id === activeBatch?.batch_id;
      select.append(option);
    });
    select.disabled = !batches.length;
    $('batchState').textContent = activeBatch ? `${statusText[activeBatch.status]} · ${outcomeText[activeBatch.reason_code] || ''}${['planned','running'].includes(activeBatch.status) ? msg(' 아직 종료가 확인되지 않았습니다. 연결이 끊겨도 정상 완료로 계산하지 않습니다.') : ''}` : msg('이전 데이터에 실제 실행 기록이 없으면 계획을 실적으로 계산하지 않습니다.');
    $('historyMeta').textContent = msg`보관 배치 ${count(data.history?.retained_batch_count)}개 중 ${batches.length}개 표시 · 최대 1,000건 / 7일 / 4 MiB · 종료 미확정 기록은 완료 통계에서 제외`;
    const totals=data.history?.totals;
    if (totals) $('historyMeta').textContent += msg` · 종료 기록 ${totals.finalized_batch_count}개: 실제 실행 ${totals.executed_source_count} / 적용 재사용 ${totals.authoritative_reuse_count} / 미실행 ${totals.not_run_source_count}`;
    const flow = $('batchFlow'); flow.replaceChildren();
    [...batches].slice(-4).reverse().forEach(batch=>{
      const li=document.createElement('li');const button=document.createElement('button');button.type='button';
      button.setAttribute('aria-pressed',String(batch.batch_id===activeBatch?.batch_id));
      const title=document.createElement('b');const meta=document.createElement('small');const stamp=document.createElement('time');stamp.textContent=new Date(batch.timestamp*1000).toLocaleTimeString(localeTag(),{hour:'2-digit',minute:'2-digit',hour12:false});
      const metrics=data.batch_summaries?.[batch.batch_id]?.incremental;
      title.textContent=msg`${statusText[batch.status] || msg('미확정')} · 변경 ${batch.current_revision}`;
      meta.textContent=msg`${new Date(batch.timestamp*1000).toLocaleString(localeTag())} · 실행 ${count(metrics?.executed_source_count)} / 재사용 ${count(metrics?.authoritative_reuse_count)}`;
      button.append(stamp,title,meta);button.onclick=()=>{selectedBatch=batch.batch_id;render(snapshot);};li.append(button);flow.append(li);
    });
    if(!batches.length){const empty=document.createElement('p');empty.className='muted';empty.textContent=msg('기록된 검증 요청이 없습니다.');flow.append(empty);}
    const impact=data.retained_impact;
    $('retainedReuse').textContent=impact ? msg`${count(impact.reused_group_request_count)}개 결과 재사용` : msg('이력 집계 없음');
    $('retainedAvoided').textContent=impact && impact.avoided_execution_ms !== null
      ? msg`${impact.timing_status==='partial'?msg('기록 있는 요청의 부분 추정 합계'):msg('피한 재실행 비용 합계')} ${estimatedDuration(impact.avoided_execution_ms)} [추정]`
      : msg('시간 근거 없음 · 시간 합계 미측정');
    const zone=Intl.DateTimeFormat().resolvedOptions().timeZone;
    $('retainedScope').textContent=impact ? msg`완료 요청 ${impact.completed_request_count}개 · 누락 시간 표본 ${impact.missing_timing_group_count}개 · ${zone} · ${impact.from_timestamp ? new Date(impact.from_timestamp*1000).toLocaleString(localeTag()) : msg('시작 기록 없음')} ~ ${impact.through_timestamp ? new Date(impact.through_timestamp*1000).toLocaleString(localeTag()) : msg('종료 기록 없음')} · 최대 7일/1,000건 · 전체 대기 절감시간이 아닙니다.` : msg('구형 projection은 완료 이력 합계를 제공하지 않습니다.');
    return batchView(data, activeBatch);
  }

  const scenarios = localized({'first-run':'첫 실행','unchanged':'변경 없음','docs':'문서 변경','partial-reuse':'일부 실행 + 일부 재사용','code':'코드 변경','environment':'환경 변경','first-failure':'첫 검사 실패',
    'unrelated-code':'부분 영향 코드','related-code':'다른 단일 묶음 영향','all-code':'모든 묶음 영향','failure':'예상 실패','retry':'수정 후 재시도'});
  const criteria = localized({'same-shards':'같은 묶음 전체 실행','parent-suite':'기존 전체 검증 명령'});
  function readLegacyComparison(value) {
    if (value?.version !== 2 || value.kind !== 'click-paired-verification-benchmark' || !Array.isArray(value.samples) || value.samples.length > 240) throw Error(msg('지원하지 않는 비교 형식'));
    const c = value.conditions;
    const integer = n => Number.isInteger(n) && n >= 0 && n <= 1000000;
    const duration = n => Number.isFinite(n) && n >= 0;
    if (!c || ![c.iterations,c.warmups,c.workload_rounds].every(integer) || !['evidence','guarded'].includes(c.runtime_mode) || c.scope_equivalence !== 'same-two-unittest-files' || c.authority !== 'real-hooks-and-one-use-runner' || c.observer !== 'off' || c.order !== 'alternating-pair-order') throw Error(msg('비교 조건 정보가 없습니다'));
    if (c.iterations < 1 || c.iterations > 10 || c.warmups > 10 || c.workload_rounds < 1) throw Error(msg('비교 반복 조건이 잘못되었습니다'));
    const seen = new Set();
    const samples = value.samples.map(item => {
      const key = `${item.scenario}:${item.comparison}:${item.iteration}`;
      if (!Object.hasOwn(scenarios,item.scenario) || !Object.hasOwn(criteria,item.comparison) || !integer(item.iteration) || typeof item.warmup !== 'boolean' || seen.has(key)) throw Error(msg('비교 표본이 잘못되었습니다'));
      if (item.iteration >= c.iterations+c.warmups || item.warmup !== (item.iteration < c.warmups) || !Array.isArray(item.order) || !['baseline,incremental','incremental,baseline'].includes(item.order.join(','))) throw Error(msg('표본의 실행 순서나 워밍업 조건이 잘못되었습니다'));
      seen.add(key);
      const arms = {};
      for (const name of ['baseline','incremental']) {
        const arm = item[name];
        if (!arm || !duration(arm.wall_ms) || !['passed','failed','interrupted','rejected','incomplete'].includes(arm.status) || ![arm.executed_source_count,arm.reused_source_count,arm.not_run_source_count].every(integer)) throw Error(msg('실측 결과 정보가 없습니다'));
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
    if (!sameKeys(value,topKeys) || value.version!==4 || value.kind!=='click-guarded-workflow-benchmark' || value.source!=='isolated-guarded-fixture' || value.unit!=='ms') throw Error(msg('지원하지 않는 비교 형식'));
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
        c.failure!=='expected-failure-kept-raw-and-in-workflow-cost; excluded-from-success-only-savings-statistics') throw Error(msg('비교 조건 정보가 없습니다'));
    if (!Array.isArray(value.samples) || value.samples.length!==c.iterations+c.warmups || !Array.isArray(value.stage_summaries) || value.stage_summaries.length!==32 || !Array.isArray(value.cumulative_summaries) || value.cumulative_summaries.length!==4 || !Array.isArray(value.workflow_cost_summaries) || value.workflow_cost_summaries.length!==2 || !Array.isArray(value.summaries) || JSON.stringify(value.summaries)!==JSON.stringify(value.cumulative_summaries) || !Array.isArray(value.limitations) || value.limitations.some(item=>typeof item!=='string') || (value.dashboard_snapshot!==null && (typeof value.dashboard_snapshot!=='object' || Array.isArray(value.dashboard_snapshot)))) throw Error(msg('비교 보고서 구조가 잘못되었습니다'));
    if (!Array.isArray(value.comparison_samples) || value.comparison_samples.length>640 || value.comparison_samples.length!==(c.iterations+c.warmups)*2*stages.length*comparisons.length) throw Error(msg('비교 표본 수가 잘못되었습니다'));
    const scopes={
      'same-shards':['sequential-shard-command-dispatch-through-return','executed-source-command-duration-sum'],
      'parent-suite':['parent-command-dispatch-through-return','driver-preflight-through-runner-return'],
    };
    const seen=new Set();
    const samples=value.comparison_samples.map(item=>{
      const itemKeys=['configuration','scenario','comparison','iteration','warmup','order','eligible','excluded_reason','scope_equivalent','unit','baseline','click','delta_ms','delta_percent'];
      if (!sameKeys(item,itemKeys) || !configurations.slice(1).includes(item.configuration) || !stages.includes(item.scenario) || !comparisons.includes(item.comparison) || !integer(item.iteration) || item.iteration>=c.iterations+c.warmups || typeof item.warmup!=='boolean' || item.warmup!==(item.iteration<c.warmups) || item.unit!=='ms') throw Error(msg('비교 표본이 잘못되었습니다'));
      const key=`${item.configuration}:${item.scenario}:${item.comparison}:${item.iteration}`;
      if (seen.has(key)) throw Error(msg('중복 비교 표본입니다'));seen.add(key);
      if (!Array.isArray(item.order) || item.order.length!==3 || [...item.order].sort().join(',')!=='click,parent-suite,same-shards') throw Error(msg('표본 실행 순서가 잘못되었습니다'));
      if (!sameKeys(item.baseline,['duration_ms','status','measurement_scope']) || !sameKeys(item.click,['duration_ms','status','measurement_scope']) || !duration(item.baseline.duration_ms) || !duration(item.click.duration_ms) || !['passed','failed'].includes(item.baseline.status) || !['passed','failed'].includes(item.click.status) || item.baseline.measurement_scope!==scopes[item.comparison][0] || item.click.measurement_scope!==scopes[item.comparison][1]) throw Error(msg('실측 결과 정보가 없습니다'));
      const scopeEquivalent=!(item.configuration==='click-default'&&item.comparison==='same-shards');
      const eligible=!item.warmup && scopeEquivalent && item.baseline.status==='passed' && item.click.status==='passed';
      const excluded=item.warmup?'warmup':!scopeEquivalent?'scope-not-equivalent':eligible?'':'verification-not-passed';
      const delta=item.baseline.duration_ms-item.click.duration_ms;
      const percent=item.baseline.duration_ms>0?100*delta/item.baseline.duration_ms:null;
      if (item.scope_equivalent!==scopeEquivalent || item.eligible!==eligible || item.excluded_reason!==excluded || !Number.isFinite(item.delta_ms) || Math.abs(item.delta_ms-delta)>1e-6 || (percent===null?item.delta_percent!==null:!Number.isFinite(item.delta_percent)||Math.abs(item.delta_percent-percent)>1e-6)) throw Error(msg('비교 계산이 원시 시간과 다릅니다'));
      return {configuration:item.configuration,scenario:item.scenario,comparison:item.comparison,iteration:item.iteration,warmup:item.warmup,order:[...item.order],eligible,excluded_reason:excluded,scope_equivalent:scopeEquivalent,
        baseline:{wall_ms:item.baseline.duration_ms,status:item.baseline.status,executed_source_count:0,reused_source_count:0,not_run_source_count:0},
        incremental:{wall_ms:item.click.duration_ms,status:item.click.status,executed_source_count:0,reused_source_count:0,not_run_source_count:0},
        delta_ms:delta,delta_percent:percent};
    });
    const costSeen=new Set();
    const costSamples=value.samples.flatMap(sample=>{
      if(!integer(sample.iteration) || sample.iteration>=c.iterations+c.warmups || costSeen.has(sample.iteration) || sample.warmup!==(sample.iteration<c.warmups) || !sample.arms) throw Error(msg('비교 추가 비용 표본이 잘못되었습니다'));
      costSeen.add(sample.iteration);
      return configurations.map(configuration=>{
        const arm=sample.arms[configuration];
        if(!arm || ![arm.setup_ms,arm.transition_ms,arm.audit_wall_ms,arm.validation_wall_ms].every(duration)) throw Error(msg('비교 추가 비용 구간이 잘못되었습니다'));
        return {configuration,iteration:sample.iteration,warmup:sample.warmup,setup_ms:arm.setup_ms,transition_ms:arm.transition_ms,additional_full_audit_ms:arm.audit_wall_ms,validation_wall_ms:arm.validation_wall_ms};
      });
    });
    let repositoryReference=null;
    const reference=value.repository_reference;
    if (reference!==null) {
      const referenceKeys=['version','kind','source','unit','scope_digest','conditions','samples','summary','limitations'];
      if (!sameKeys(reference,referenceKeys) || reference.version!==1 || reference.kind!=='click-repository-bundle-reference' || reference.source!=='current-repository-test-bundle' || reference.unit!=='ms' || !/^[0-9a-f]{64}$/.test(reference.scope_digest) || !Array.isArray(reference.samples) || reference.samples.length>5) throw Error(msg('저장소 번들 참조가 잘못되었습니다'));
      const rc=reference.conditions;
      const rcKeys=['iterations','warmups','shard_count','scope_basis','measurement_order','cache','measurement_scope'];
      if (!sameKeys(rc,rcKeys) || !integer(rc.iterations) || rc.iterations<1 || rc.iterations>3 || !integer(rc.warmups) || rc.warmups>2 || !integer(rc.shard_count) || rc.shard_count<1 || rc.scope_basis!=='committed-evidence-shards-v1-inventory' || rc.measurement_order!=='alternating-pair-order' || rc.measurement_scope!=='driver-command-dispatch-through-return' || rc.cache!=='same-working-tree-and-environment; OS-cache-not-flushed; bytecode-disabled' || reference.samples.length!==rc.iterations+rc.warmups || !Array.isArray(reference.limitations) || reference.limitations.some(item=>typeof item!=='string')) throw Error(msg('저장소 번들 조건이 잘못되었습니다'));
      let eligibleCount=0;const referenceSeen=new Set();
      reference.samples.forEach(item=>{
        const keys=['iteration','warmup','order','eligible','excluded_reason','same_shards','parent_suite','delta_ms','delta_percent'];
        if (!sameKeys(item,keys) || !integer(item.iteration) || item.iteration>=reference.samples.length || referenceSeen.has(item.iteration) || typeof item.warmup!=='boolean' || item.warmup!==(item.iteration<rc.warmups) || !Array.isArray(item.order) || !['same-shards,parent-suite','parent-suite,same-shards'].includes(item.order.join(','))) throw Error(msg('저장소 번들 표본이 잘못되었습니다'));
        referenceSeen.add(item.iteration);
        for (const arm of [item.same_shards,item.parent_suite]) if (!sameKeys(arm,['duration_ms','status','exit_code','executed_command_count','not_run_command_count']) || !duration(arm.duration_ms) || !['passed','failed'].includes(arm.status) || !Number.isInteger(arm.exit_code) || !integer(arm.executed_command_count) || !integer(arm.not_run_command_count) || (arm.status==='passed')!==(arm.exit_code===0)) throw Error(msg('저장소 번들 실행 결과가 잘못되었습니다'));
        const passed=item.same_shards.status==='passed'&&item.parent_suite.status==='passed';const eligible=!item.warmup&&passed;const excluded=item.warmup?'warmup':passed?'':'verification-not-passed';
        const delta=item.parent_suite.duration_ms-item.same_shards.duration_ms;const percent=item.parent_suite.duration_ms>0?100*delta/item.parent_suite.duration_ms:null;
        if (item.eligible!==eligible || item.excluded_reason!==excluded || !Number.isFinite(item.delta_ms) || Math.abs(item.delta_ms-delta)>1e-6 || (percent===null?item.delta_percent!==null:!Number.isFinite(item.delta_percent)||Math.abs(item.delta_percent-percent)>1e-6)) throw Error(msg('저장소 번들 계산이 원시 시간과 다릅니다'));
        if (eligible) eligibleCount++;
      });
      const rs=reference.summary;const readDist=item=>{if(!sameKeys(item,['median','min','max'])||Object.values(item).some(number=>number!==null&&!Number.isFinite(number)))throw Error(msg('저장소 번들 요약이 잘못되었습니다'));return {median:item.median,min:item.min,max:item.max};};
      if (!sameKeys(rs,['eligible_samples','same_shards_duration_ms','parent_suite_duration_ms','parent_minus_shards_ms']) || rs.eligible_samples!==eligibleCount) throw Error(msg('저장소 번들 요약이 잘못되었습니다'));
      repositoryReference={source:reference.source,unit:'ms',scope_digest:reference.scope_digest,conditions:{iterations:rc.iterations,warmups:rc.warmups,shard_count:rc.shard_count},summary:{eligible_samples:eligibleCount,same_shards_duration_ms:readDist(rs.same_shards_duration_ms),parent_suite_duration_ms:readDist(rs.parent_suite_duration_ms),parent_minus_shards_ms:readDist(rs.parent_minus_shards_ms)}};
    }
    const engine=value.engine||{};const environment=value.environment||{};
    return {version:2,source:value.source,engine:{version:/^[0-9]+\.[0-9]+\.[0-9]+(?:[+-][0-9A-Za-z.-]+)?$/.test(engine.version)?engine.version:null,commit:/^[0-9a-f]{40,64}$/.test(engine.commit)?engine.commit:null,source_digest:/^[0-9a-f]{64}$/.test(engine.source_digest)?engine.source_digest:null,working_tree_modified:engine.working_tree_modified===true},
      environment:{system:['Linux','Darwin','Windows'].includes(environment.system)?environment.system:null,machine:['x86_64','AMD64','aarch64','arm64','i386','i686','x86'].includes(environment.machine)?environment.machine:null,python:/^[0-9]+\.[0-9]+\.[0-9]+$/.test(environment.python)?environment.python:null},
      conditions:{iterations:c.iterations,warmups:c.warmups,workload_rounds:c.workload_rounds,runtime_mode:'guarded',scope:['alpha','beta'],order:c.measurement_order,observer:'off',cache:'매 반복·구성마다 초기 상태 복원 · 단계 내 세 측정 교차 · OS 캐시 초기화 안 함 · bytecode 비활성'},samples,cost_samples:costSamples,repository_reference:repositoryReference};
  }

  function readComparison(value) {
    if (value?.version===2 && value.kind==='click-paired-verification-benchmark') return readLegacyComparison(value);
    if (value?.version===4 && value.kind==='click-guarded-workflow-benchmark') return readWorkflowComparison(value);
    throw Error(msg('지원하지 않는 비교 형식'));
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
      const prefix=configuration==='legacy'?'':`${configuration==='click-default'?msg('Click 기본'):msg('명시적 재사용')} · `;
      return {criterion,label:`${prefix}${scenarios[scenario]} · ${criteria[criterion]}`, n:samples.length, excluded:all.length-samples.length,
        baseline:median(samples.map(item=>item.baseline.wall_ms)), incremental:median(samples.map(item=>item.incremental.wall_ms)),
        delta:median(deltas), percent:median(samples.map(item=>item.delta_percent).filter(Number.isFinite)),
        min:deltas.length?Math.min(...deltas):null,max:deltas.length?Math.max(...deltas):null};
    });
  }

  const TASK_PUBLIC_REQUIRED = [
    'comparison_ref','baseline_variant','improved_variant','comparison_label','scenario','run_kind','runtime_mode','measured_at',
    'sample_count','comparable_sample_count','incomplete_sample_count','failed_sample_count','cancelled_sample_count','completion_condition',
    'task_measurement_status','task_measurement_reason','task_completion_time_delta_ms','task_completion_time_savings_ratio','task_effect_status','task_time_ratio_range',
    'faster_sample_count','unchanged_sample_count','slower_sample_count','token_measurement_status','token_measurement_reason','token_savings_ratio',
    'token_pair_median_savings_ratio','token_ratio_range','token_aggregation','baseline_user_intervention_count','improved_user_intervention_count',
    'user_intervention_pair_count','observability'
  ];
  const TASK_PUBLIC_OPTIONAL = [
    'baseline_user_intervention_observed_duration_ms','improved_user_intervention_observed_duration_ms',
    'baseline_tool_call_count','improved_tool_call_count','baseline_post_failure_calls_before_mutation','improved_post_failure_calls_before_mutation',
    'baseline_repair_cycle_count','improved_repair_cycle_count','baseline_model_round_trip_count','improved_model_round_trip_count',
    'baseline_activity_interval_count','improved_activity_interval_count','baseline_unclassified_activity_interval_count','improved_unclassified_activity_interval_count'
  ];
  const TASK_PUBLIC_OPTIONAL_DURATIONS = new Set(['baseline_user_intervention_observed_duration_ms','improved_user_intervention_observed_duration_ms']);
  const TASK_PUBLIC_ALLOWED = new Set([...TASK_PUBLIC_REQUIRED,...TASK_PUBLIC_OPTIONAL]);
  const taskCount=value=>Number.isInteger(value)&&value>=0&&value<=1000000;
  const taskTimestamp=value=>Number.isSafeInteger(value)&&value>=0&&value<=253402300799;
  const nullableCount=value=>value===null||taskCount(value);
  const nullableNumber=value=>value===null||(Number.isFinite(value)&&Math.abs(value)<=Number.MAX_SAFE_INTEGER);
  const exactKeys=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join(',')===[...keys].sort().join(',');
  const safePublicText=(value,max=96)=>typeof value==='string'&&value.length>0&&value.length<=max&&!/[\u0000-\u001f\u007f<>\\/]/u.test(value)&&!/(token|secret|password|credential|api.?key|authorization|bearer)/i.test(value);
  const ratioRange=value=>value===null||(exactKeys(value,['min','max'])&&Number.isFinite(value.min)&&Number.isFinite(value.max)&&value.min<=value.max);
  function hasForbiddenUsage(value) {
    if(Array.isArray(value))return value.some(hasForbiddenUsage);
    if(!value||typeof value!=='object')return false;
    return Object.entries(value).some(([key,item])=>{
      const normalized=key.toLowerCase().replaceAll('-','_');
      return ['baseline_total_tokens','improved_total_tokens','saved_tokens','input_tokens','output_tokens','cached_input_tokens','reasoning_output_tokens','usage','usage_events','events'].includes(normalized)||hasForbiddenUsage(item);
    });
  }
  function readTaskEfficiency(value) {
    const top=['kind','version','generated_at','measurement_status','measurement_reason','presentations'];
    if(hasForbiddenUsage(value)||!exactKeys(value,top)||value.kind!=='click-task-efficiency-public'||value.version!==1||!taskTimestamp(value.generated_at)||!['measured','unmeasured'].includes(value.measurement_status)||typeof value.measurement_reason!=='string'||!/^[a-z0-9-]{0,96}$/.test(value.measurement_reason)||!Array.isArray(value.presentations)||value.presentations.length>64) throw Error(msg('지원하지 않는 비교 형식'));
    const refs=new Set(),scopes=new Set();
    const presentations=value.presentations.map(raw=>{
      if(!raw||typeof raw!=='object'||Array.isArray(raw)||TASK_PUBLIC_REQUIRED.some(key=>!(key in raw))||Object.keys(raw).some(key=>!TASK_PUBLIC_ALLOWED.has(key)))throw Error(msg('지원하지 않는 비교 형식'));
      const variants=['N','B0','B1','B2'];
      const counts=['sample_count','comparable_sample_count','incomplete_sample_count','failed_sample_count','cancelled_sample_count','faster_sample_count','unchanged_sample_count','slower_sample_count','user_intervention_pair_count'];
      if(!/^[0-9a-f]{24}$/.test(raw.comparison_ref)||refs.has(raw.comparison_ref)||!variants.includes(raw.baseline_variant)||!variants.includes(raw.improved_variant)||raw.baseline_variant===raw.improved_variant||raw.comparison_label!==`${raw.baseline_variant}→${raw.improved_variant}`||!safePublicText(raw.scenario)||!['first-use','prepared-repeat'].includes(raw.run_kind)||!['evidence','guarded'].includes(raw.runtime_mode)||!taskTimestamp(raw.measured_at)||counts.some(key=>!taskCount(raw[key]))||raw.comparable_sample_count+raw.incomplete_sample_count!==raw.sample_count||raw.failed_sample_count+raw.cancelled_sample_count>raw.incomplete_sample_count||raw.faster_sample_count+raw.unchanged_sample_count+raw.slower_sample_count>raw.comparable_sample_count||raw.completion_condition!=='same-version acceptance digest matched')throw Error(msg('지원하지 않는 비교 형식'));
      const scope=[raw.baseline_variant,raw.improved_variant,raw.scenario,raw.run_kind,raw.runtime_mode].join(':');
      if(scopes.has(scope))throw Error(msg('지원하지 않는 비교 형식'));refs.add(raw.comparison_ref);scopes.add(scope);
      if(!['measured','unmeasured'].includes(raw.task_measurement_status)||!['measured','unmeasured'].includes(raw.token_measurement_status)||typeof raw.task_measurement_reason!=='string'||!/^[a-z0-9-]{0,96}$/.test(raw.task_measurement_reason)||typeof raw.token_measurement_reason!=='string'||!/^[a-z0-9-]{0,96}$/.test(raw.token_measurement_reason)||!nullableNumber(raw.task_completion_time_delta_ms)||!nullableNumber(raw.task_completion_time_savings_ratio)||!nullableNumber(raw.token_savings_ratio)||!nullableNumber(raw.token_pair_median_savings_ratio)||!ratioRange(raw.task_time_ratio_range)||!ratioRange(raw.token_ratio_range))throw Error(msg('지원하지 않는 비교 형식'));
      const taskEffect=raw.task_completion_time_savings_ratio===null?'unmeasured':raw.task_completion_time_savings_ratio>0?'faster':raw.task_completion_time_savings_ratio<0?'slower':'unchanged';
      if(raw.task_effect_status!==taskEffect||(raw.task_measurement_status==='measured')!==(raw.task_completion_time_savings_ratio!==null&&raw.task_completion_time_delta_ms!==null)||(raw.token_measurement_status==='measured')!==(raw.token_savings_ratio!==null)||!['ratio-of-complete-pair-totals','unavailable'].includes(raw.token_aggregation)||(raw.token_measurement_status==='measured')!==(raw.token_aggregation==='ratio-of-complete-pair-totals'))throw Error(msg('지원하지 않는 비교 형식'));
      if(!nullableCount(raw.baseline_user_intervention_count)||!nullableCount(raw.improved_user_intervention_count)||!exactKeys(raw.observability,['activity_intervals_are_non_additive','hidden_reasoning_status'])||raw.observability.activity_intervals_are_non_additive!==true||raw.observability.hidden_reasoning_status!=='unknown'||TASK_PUBLIC_OPTIONAL.some(key=>key in raw&&!(TASK_PUBLIC_OPTIONAL_DURATIONS.has(key)?nullableNumber(raw[key])&&!(Number.isFinite(raw[key])&&raw[key]<0):nullableCount(raw[key]))))throw Error(msg('지원하지 않는 비교 형식'));
      return Object.fromEntries([...TASK_PUBLIC_REQUIRED,...TASK_PUBLIC_OPTIONAL].filter(key=>key in raw).map(key=>[key,raw[key]&&typeof raw[key]==='object'?JSON.parse(JSON.stringify(raw[key])):raw[key]]));
    });
    const measured=presentations.some(item=>item.task_measurement_status==='measured'||item.token_measurement_status==='measured');
    if((value.measurement_status==='measured')!==measured)throw Error(msg('지원하지 않는 비교 형식'));
    return {kind:value.kind,version:1,generated_at:value.generated_at,measurement_status:value.measurement_status,measurement_reason:value.measurement_reason,presentations};
  }

  const taskReasonText=localized({
    'host-task-and-usage-boundaries-unavailable':'호스트 작업 시작·종료 경계를 사용할 수 없습니다.',
    'host-task-or-usage-boundary-unavailable':'호스트 작업 시작·종료 경계를 사용할 수 없습니다.',
    'task-boundary-unavailable':'호스트 작업 시작·종료 경계를 사용할 수 없습니다.',
    'task-boundary-missing':'호스트 작업 시작·종료 경계를 사용할 수 없습니다.',
    'completion-digest-mismatch':'동등한 완료 조건을 확인할 수 없습니다.',
    'acceptance-version-mismatch':'동등한 완료 조건을 확인할 수 없습니다.',
    'evaluation-missing':'작업 비교 자료가 없습니다.',
    'usage-unavailable':'완전한 usage 범위를 사용할 수 없습니다.',
    'usage-events-missing':'완전한 usage 범위를 사용할 수 없습니다.',
    'usage-scope-incomplete':'완전한 usage 범위를 사용할 수 없습니다.',
    'evaluation-version-incompatible':'비교 형식이 호환되지 않습니다.'
  });
  const reasonTextForTask=reason=>taskReasonText[reason]||msg('비교 조건이 불완전합니다.');
  const comparisonBasis=p=>p.baseline_variant==='N'&&p.improved_variant==='B2'?msg('Click 미사용 대비'):p.baseline_variant==='B0'&&p.improved_variant==='B2'?msg('개선 전 Click 대비'):msg`${p.baseline_variant} 대비`;
  const runKindText=value=>value==='first-use'?msg('첫 사용'):msg('준비된 반복 사용');
  function ratioMagnitude(ratio) {
    const value=Math.abs(ratio)*100;
    return value>0&&value<0.01?'<0.01':decimal(value);
  }
  const tokenRatioText=ratio=>ratio===0?msg('0% · 변화 없음'):ratio>0?msg`${ratioMagnitude(ratio)}% 감소`:msg`${ratioMagnitude(ratio)}% 증가`;
  const taskRatioText=ratio=>ratio===0?msg('0% · 변화 없음'):ratio>0?msg`${ratioMagnitude(ratio)}% 빨라짐`:msg`${ratioMagnitude(ratio)}% 느려짐`;
  function selectedTaskPresentation() {
    const presentations=taskEfficiency?.presentations||[];
    if(presentations.length===1)return presentations[0];
    return presentations.find(item=>item.comparison_ref===selectedTaskComparison)||null;
  }
  function taskEfficiencyPresentation() {
    const selected=selectedTaskPresentation();
    if(!selected){
      const multiple=(taskEfficiency?.presentations||[]).length;
      const reason=multiple>1?msg`서로 다른 비교 범위가 ${multiple}개 있습니다. 사용할 비교를 선택하세요.`:reasonTextForTask(taskEfficiency?.measurement_reason||'evaluation-missing');
      return {selected:null,state:'unmeasured',taskValue:msg('미측정'),taskReason:reason,tokenValue:msg('미측정'),tokenReason:reason,basis:msg('선택한 평가 · 개선 전 대비'),scope:msg('전체 작업 비교를 가져오지 않았습니다.'),adverse:''};
    }
    const scope=msg`${selected.comparison_label} · ${selected.scenario} · ${runKindText(selected.run_kind)} · ${selected.runtime_mode==='evidence'?'Evidence':'Guarded'} · 표본 ${selected.sample_count}개`;
    const taskMeasured=selected.task_measurement_status==='measured';
    const tokenMeasured=selected.token_measurement_status==='measured';
    const adverse=[];
    if(selected.slower_sample_count||selected.failed_sample_count||selected.cancelled_sample_count||selected.incomplete_sample_count) adverse.push(msg`느려진 작업 ${selected.slower_sample_count}개 · 실패 ${selected.failed_sample_count}개 · 취소 ${selected.cancelled_sample_count}개 · 미완료 ${selected.incomplete_sample_count}개`);
    if(Number.isInteger(selected.baseline_user_intervention_count)&&Number.isInteger(selected.improved_user_intervention_count)&&selected.improved_user_intervention_count>selected.baseline_user_intervention_count) adverse.push(msg`사용자 개입 ${selected.baseline_user_intervention_count} → ${selected.improved_user_intervention_count}`);
    return {selected,state:taskMeasured?selected.task_effect_status:'unmeasured',taskValue:taskMeasured?taskRatioText(selected.task_completion_time_savings_ratio):msg('미측정'),taskReason:taskMeasured?msg('선택한 비교의 전체 작업시간을 직접 측정했습니다. 테스트 절감 시간과 합산하지 않습니다.'):msg`작업 완료시간은 미측정입니다: ${reasonTextForTask(selected.task_measurement_reason)}`,tokenValue:tokenMeasured?tokenRatioText(selected.token_savings_ratio):msg('미측정'),tokenReason:tokenMeasured?msg`전체 작업 동등 완료 표본 ${selected.comparable_sample_count}개 · 합산 비율`:msg`측정 불가: ${reasonTextForTask(selected.token_measurement_reason)}`,basis:comparisonBasis(selected),scope,adverse:adverse.join(' · ')};
  }
  const pairCount=(a,b)=>Number.isInteger(a)&&Number.isInteger(b)?msg`${a}개 / ${b}개`:msg('측정 정보 없음');
  const pairDuration=(a,b)=>Number.isFinite(a)&&Number.isFinite(b)?`${fmt(a)} / ${fmt(b)}`:msg('측정 정보 없음');
  function renderTaskEfficiency() {
    const items=taskEfficiency?.presentations||[];
    const select=$('taskEvaluationSelect');
    const label=$('taskEvaluationLabel');
    label.hidden=items.length<=1;
    select.replaceChildren();
    if(items.length>1){
      const prompt=document.createElement('option');prompt.value='';prompt.textContent=msg('비교 선택 필요');select.append(prompt);
      items.forEach(item=>{const option=document.createElement('option');option.value=item.comparison_ref;option.textContent=msg`${item.comparison_label} · ${item.scenario} · ${runKindText(item.run_kind)} · ${item.runtime_mode==='evidence'?'Evidence':'Guarded'} · 표본 ${item.sample_count}개`;select.append(option);});
      select.value=selectedTaskComparison;
    }
    const view=taskEfficiencyPresentation(),p=view.selected;
    $('tokenSavings').textContent=view.tokenValue;$('tokenStatus').textContent=view.tokenReason;$('tokenBasis').textContent=p?`${view.basis} · ${msg('실측 비교')}`:msg('선택한 평가 · 개선 전 대비');
    $('tokenMeasuredBadge').hidden=!(p&&p.token_measurement_status==='measured');
    $('tokenSavings').dataset.state=!p||p.token_measurement_status!=='measured'?'unmeasured':p.token_savings_ratio<0?'increased':'measured';
    $('tokenScope').textContent=p?msg`${view.scope} · 전체 작업 기준 · 절대 토큰 수는 공개하지 않습니다.`:msg('전체 작업 기준 · 절대 토큰 수는 공개하지 않습니다.');
    $('taskEffectValue').textContent=view.taskValue;$('taskEffectState').textContent=view.state==='faster'?msg('빨라짐'):view.state==='unchanged'?msg('변화 없음'):view.state==='slower'?msg('느려짐'):msg('미측정');
    $('taskEffectState').className=`pill ${view.state==='slower'?'rerun':view.state==='unmeasured'?'pending':'reused'}`;$('taskEffectScope').textContent=p?`${view.scope}. ${view.taskReason}`:view.taskReason;$('taskEffectAdverse').textContent=view.adverse;$('taskEffectAdverse').hidden=!view.adverse;$('taskEffect').dataset.state=view.state;
    $('detailTaskScope').textContent=p?`${view.scope} · ${view.basis}`:msg('미측정');
    $('detailTaskDelta').textContent=p&&p.task_measurement_status==='measured'?signedDuration(p.task_completion_time_delta_ms):msg('측정 정보 없음');
    $('detailTaskSamples').textContent=p?msg`표본 ${p.sample_count} · 완료 ${p.comparable_sample_count} / 실패 ${p.failed_sample_count} / 취소 ${p.cancelled_sample_count} / 미완료 ${p.incomplete_sample_count}`:'—';
    $('detailTaskOutcomes').textContent=p?`${p.faster_sample_count} / ${p.unchanged_sample_count} / ${p.slower_sample_count}`:'—';
    $('detailInterventions').textContent=p?pairCount(p.baseline_user_intervention_count,p.improved_user_intervention_count):msg('측정 정보 없음');
    $('detailInterventionTime').textContent=p?pairDuration(p.baseline_user_intervention_observed_duration_ms,p.improved_user_intervention_observed_duration_ms):msg('측정 정보 없음');
    $('detailToolCalls').textContent=p?pairCount(p.baseline_tool_call_count,p.improved_tool_call_count):msg('측정 정보 없음');
    $('detailFailureCalls').textContent=p?pairCount(p.baseline_post_failure_calls_before_mutation,p.improved_post_failure_calls_before_mutation):msg('측정 정보 없음');
    $('detailRepairCycles').textContent=p?pairCount(p.baseline_repair_cycle_count,p.improved_repair_cycle_count):msg('측정 정보 없음');
    $('detailRoundTrips').textContent=p?pairCount(p.baseline_model_round_trip_count,p.improved_model_round_trip_count):msg('측정 정보 없음');
    $('detailActivity').textContent=p&&Number.isInteger(p.baseline_activity_interval_count)&&Number.isInteger(p.improved_activity_interval_count)&&Number.isInteger(p.baseline_unclassified_activity_interval_count)&&Number.isInteger(p.improved_unclassified_activity_interval_count)?msg`${p.baseline_activity_interval_count} / ${p.improved_activity_interval_count} (미분류 ${p.baseline_unclassified_activity_interval_count} / ${p.improved_unclassified_activity_interval_count})`:msg('측정 정보 없음');
    $('taskObservability').textContent=msg('활동 구간은 겹칠 수 있어 전체 작업시간으로 합산하지 않습니다. 숨은 추론은 미측정입니다.');
    $('taskLocalRef').textContent=taskEvaluationLocalRef?msg`로컬 원자료 참조: ${taskEvaluationLocalRef} (공유본 제외)`:'';
    return view;
  }
  function publicTaskEfficiency() {
    const view=taskEfficiencyPresentation();
    return {kind:'click-task-efficiency-presentation',version:1,measurement_status:view.selected&&(view.selected.task_measurement_status==='measured'||view.selected.token_measurement_status==='measured')?'measured':'unmeasured',measurement_reason:view.selected?'':taskEfficiency?.measurement_reason||'evaluation-missing',presentation:view.selected?JSON.parse(JSON.stringify(view.selected)):null,display:{task_state:view.state,task_value:view.taskValue,task_reason:view.taskReason,token_value:view.tokenValue,token_reason:view.tokenReason,basis:view.basis,scope:view.scope,adverse:view.adverse}};
  }

  function renderComparison() {
    const root = $('comparisonChart'); root.replaceChildren();
    renderTaskEfficiency();
    $('waitIncreaseNotice').hidden = true;
    if (!comparison) {
      $('clearComparison').hidden=!taskEfficiencyImported;
      $('comparisonStatus').textContent = msg('동등한 paired 비교 실측이 없습니다.');
      $('pairedNet').textContent = msg('측정 정보 없음');
      $('comparisonInfo').textContent = taskEfficiencyImported?msg`작업 비교를 불러왔습니다. ${msg('공개 작업 비교 · 절대 토큰 수 제외')}`:msg('아직 비교 측정이 없습니다. 검증 구간 비교 JSON 또는 Phase 4 공개 작업 비교 JSON을 가져올 수 있습니다. 가져온 결과는 승인·재사용 권한이 아닙니다.');
      return;
    }
    const c = comparison.conditions;
    const scopeText=comparison.version===2 ? msg('same-shards는 테스트 명령 구간, parent-suite는 Click 요청 구간을 비교하며 서로 합치지 않습니다.') : msg('legacy paired는 전체 기준 명령의 wall time과 Click 요청 구간의 wall time을 비교합니다. source-command 실행 구간 비교가 아닙니다.');
    const rows = comparisonRows();
    const eligible = rows.filter(row => row.n > 0);
    const increased = eligible.filter(row => row.delta < 0);
    $('clearComparison').hidden=false;
    const sourceText=comparison.version===2?msg('독립 Guarded fixture 단계별'):'legacy fixture';
    $('comparisonStatus').textContent = eligible.length ? msg`별도 paired 비교 실측 ${eligible.length}개 표본군 · ${sourceText}` : msg('정상 완료한 paired 비교 표본 없음');
    $('pairedNet').textContent = eligible.length === 1
      ? msg`${eligible[0].delta < 0 ? msg('증가') : msg('감소')} ${fmt(Math.abs(eligible[0].delta))} · 쌍별 중앙값`
      : eligible.length
        ? msg`성공 표본군 ${eligible.length}개 · 아래 쌍별 중앙값 참조`
        : msg('측정 정보 없음');
    $('waitIncreaseNotice').hidden = increased.length === 0;
    const notice=$('waitIncreaseNotice').querySelector?.('a');
    if(notice) notice.textContent = increased.some(row=>row.criterion==='parent-suite') ? msg('별도 fixture에서 Click 요청 구간이 더 길었습니다. 상세 보기') : msg('별도 fixture에서 Click 측정 구간이 더 길었습니다. 상세 보기');
    const referenceText=comparison.repository_reference?msg(' · 실제 저장소 전체 테스트 번들 그룹화 참조 첨부'):msg(' · 실제 저장소 번들 참조 없음');
    $('comparisonInfo').textContent = msg`별도 fixture 직접 측정 · 이번 live 요청과 별개 · 서명 없음 · 승인·재사용 권한 없음 · ${c.runtime_mode} · 반복 ${c.iterations} / 워밍업 ${c.warmups} · 각 경로의 2개 테스트 파일 범위 일치 · ${msg(c.cache)}${referenceText}. ${scopeText} 아래는 성공 표본의 중앙값이고 음수는 해당 행의 측정 구간이 증가했다는 뜻입니다.`;
    rows.forEach(row => {
      const article = document.createElement('article'); article.className = 'comparison-row';
      const title = document.createElement('h3'); title.textContent = msg`${row.label} · ${row.n}회 측정 / ${row.excluded}회 제외`; article.append(title);
      if (row.n) {
        const max = Math.max(row.baseline,row.incremental,1);
        [[msg('전체 재실행 기준'),row.baseline,'baseline'],[msg('Click 증분 실행'),row.incremental,'incremental']].forEach(([label,value,kind]) => {
          const bar = document.createElement('div'); bar.className = `comparison-bar ${kind}`;
          bar.style.width = `${100*value/max}%`;
          const number=document.createElement('p');number.textContent=msg`${label}: ${fmt(value)} [실측 · 경로 중앙값]`;article.append(number,bar);
        });
        const delta = document.createElement('p'); delta.className = row.delta < 0 ? 'slower' : '';
        delta.textContent = msg`쌍별 차이 중앙값 ${row.delta.toFixed(1)} ms (${row.percent === null ? msg('비율 계산 불가') : row.percent.toFixed(1)+'%'}) · 범위 ${row.min.toFixed(1)} ~ ${row.max.toFixed(1)} ms`;
        article.append(delta);
      } else {const text=document.createElement('p');text.textContent=msg('정상 완료 성능 표본 없음 · 실패·중단·워밍업 표본은 JSON에 별도 보관');article.append(text);}
      root.append(article);
    });
    if(comparison.cost_samples?.length){
      const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent=msg('별도 비교의 초기 준비·변경·추가 감사 비용 (워밍업·실패 포함)');details.append(summary);
      comparison.cost_samples.forEach(item=>{const p=document.createElement('p');p.textContent=msg`${item.configuration} · 반복 ${item.iteration+1}${item.warmup?msg(' · 워밍업'):''} · 초기 준비 ${fmt(item.setup_ms)} / 변경 ${fmt(item.transition_ms)} / 추가 전체 감사 ${fmt(item.additional_full_audit_ms)} / 요청 구간 합계 ${fmt(item.validation_wall_ms)} [실측]. 각 구간은 별도 기록입니다.`;details.append(p);});root.append(details);
    }
  }

  function shareLabel(value,index) {
    return typeof value==='string' && value.length<=96 && /^[\p{L}\p{N} _().·:+-]+$/u.test(value)
      && !/(token|secret|password|credential|api.?key|authorization|bearer)/i.test(value)
      ? value : msg`검증 묶음 ${index+1}`;
  }

  function summaryCopy(batch, summary, savings) {
    const view=outcomePresentation(batch,summary,savings);
    const taskView=taskEfficiencyPresentation();
    const lines=[view.complete ? view.summaryText : `${view.heroValue} · ${view.summaryText}`];
    if(view.complete && summary.authoritative_reuse_count>0 && Number.isFinite(savings.omitted_test_execution_ms))
      lines.push(msg`절감 시간: ${estimatedDuration(savings.omitted_test_execution_ms)} [${savings.omitted_test_execution_status==='partial'?msg('부분 추정'):msg('추정')}]${savings.omitted_test_execution_status==='partial'?msg` · 시간 근거 ${view.coverageText}개`:''}.`);
    else lines.push(msg`절감 시간: ${view.heroValue} · ${view.heroStatus}`);
    lines.push(msg`토큰 절감률: ${taskView.tokenValue} · ${taskView.tokenReason}`);
    lines.push(msg`전체 작업 효과: ${taskView.taskValue} · ${taskView.taskReason}`);
    if(taskView.selected)lines.push(`${taskView.scope} · ${taskView.basis}.`);
    if(taskView.adverse)lines.push(taskView.adverse+'.');
    if(view.resultText) lines.push(view.resultText+'.');
    lines.push(view.complete ? OUTPUT_LABELS.basis.join(' / ')+'.' : msg('요청 미완료 · 실제 관찰된 부분 결과만 표시합니다.'));
    return lines.join('\n');
  }

  function publicComparison() {
    if(!comparison)return null;
    const result=JSON.parse(JSON.stringify(comparison));
    delete result.engine;delete result.environment;
    if(result.repository_reference){delete result.repository_reference.source;delete result.repository_reference.scope_digest;}
    result.conditions={...result.conditions,cache:msg(result.conditions.cache)};
    return result;
  }

  function shareReport() {
    if (!snapshot) throw Error(msg('표시할 실행 기록이 없습니다.'));
    const display = outcomePresentation(activeBatch, activeSummary, activeSavings);
    const historical=Boolean(activeBatch && activeBatch.batch_id!==snapshot.history?.current_batch_id);
    return {version:5,locale,selection_scope:historical?'historical-batch':'current-task',kind:'click-verification-efficiency-report',generated_at:snapshot.generated_at,
      projection_version:snapshot.version ?? null,retained_impact:snapshot.retained_impact ?? null,engine:null,accounting:snapshot.accounting ?? null,controls:null,
      task:!historical&&snapshot.task?{runtime_mode:snapshot.task.runtime_mode,approval_bound:snapshot.task.approval_bound}:null,
      unit:'verification-group',summary:activeSummary,revalidation_savings:activeSavings,
      labels:JSON.parse(JSON.stringify(OUTPUT_LABELS)),summary_copy:summaryCopy(activeBatch,activeSummary,activeSavings),display:{state:display.state,hero_title:display.heroTitle,result_text:display.resultText,comparison_ready:display.comparisonReady,current_percent:display.currentPercent,summary_text:display.summaryText,
        omitted_text:display.heroValue+(display.heroIsEstimate?msg(' [추정]'):''),omitted_status_text:display.heroStatus,
        full_text:display.fullText,executed_text:display.executedText,
        reduction_text:display.reductionText,comparison_status:display.comparisonState},
      measurement_scope:activeBatch?.measurement_scope || 'unknown',
      measurement:{request_wall_ms:activeSummary?.request_wall_ms ?? null,
        measured_processing_ms:activeSummary?.measured_processing_ms ?? null,
        executed_test_execution_ms:activeSavings?.executed_test_execution_ms ?? null,
        full_sequential_test_execution_estimate_ms:activeSavings?.full_sequential_test_execution_estimate_ms ?? null,
        click_management_overhead_ms:null,click_management_overhead_status:'unmeasured',
        live_net_time_saving_ms:null,live_net_time_saving_reason:'counterfactual-not-measured',
        observer_auxiliary_processing_ms:historical?null:snapshot.summary.shadow.observer_overhead_ms},
      batch:activeBatch ? {batch_id:activeBatch.batch_id,current_revision:activeBatch.current_revision,timestamp:activeBatch.timestamp,
        finished_at:activeBatch.finished_at,status:activeBatch.status,reason_code:activeBatch.reason_code,
        task:activeBatch.task?{mode:activeBatch.task.mode}:null,
        sources:activeBatch.sources.map((item,index)=>({label:shareLabel(item.label,index),
          decision:item.decision,status:item.status,started:item.started,completed:item.completed,reason_code:item.reason_code,
          execution_reason_code:item.execution_reason_code,authority_source:item.authority_source,
          current_revision:item.current_revision,previous_revision:item.previous_revision,duration_ms:item.duration_ms,
          estimated_avoided_ms:item.estimated_avoided_ms}))} : null,
      comparison:publicComparison(),task_efficiency:publicTaskEfficiency(),shadow:historical?null:snapshot.summary.shadow,
      notes:[msg('전체 사용자 대기시간은 측정하지 않음'),msg('Hook 진입부터 결과 기록 또는 준비와 runner 개별 구간만 부분 계측 · 호스트 요청 전·최종 저장·반환 제외'),
        msg('생략한 테스트 실행시간은 실제 적용된 재사용의 적합한 이전 성공 실행 표본에 기반한 추정'),msg('전체 순차 실행 추정은 같은 묶음의 source command 구간 합이며 원래 parent 명령의 실측 wall time이 아님'),
        msg('생략한 테스트 실행시간은 관리비용을 뺀 순절감이나 사용자 대기시간 절감이 아님'),msg('Shadow는 실제 재사용·실측 절약 아님'),
        msg('입력 파일 경로와 원시 명령·환경·토큰은 공유본에 포함하지 않음'),msg('비교 fixture 결과를 일반 저장소 성능으로 일반화할 수 없음')]};
  }

  function standaloneReport(report) {
    const previousLocale=locale;
    locale=normalizeLocale(report.locale);
    try { return buildStandaloneReport(report); }
    finally { locale=previousLocale; }
  }

  function buildStandaloneReport(report) {
    // Build with textContent, never interpolate user-provided HTML or scripts.
    const doc = document.implementation.createHTMLDocument(msg('Click 검증 효율 리포트'));
    doc.documentElement.setAttribute('lang',locale);
    const meta = doc.createElement('meta'); meta.setAttribute('charset','utf-8');doc.head.prepend(meta);
    const viewport=doc.createElement('meta');viewport.name='viewport';viewport.content='width=device-width, initial-scale=1';doc.head.append(viewport);
    const style=doc.createElement('style');style.textContent='body{font:16px/1.6 system-ui,sans-serif;max-width:1100px;margin:auto;padding:32px;color:#172333}table{border-collapse:collapse;width:100%;margin:16px 0}td,th{border-bottom:1px solid #ccd4df;text-align:left;padding:10px;overflow-wrap:anywhere}pre{white-space:pre-wrap;background:#f0f3f7;padding:16px}h2{margin-top:32px}';style.textContent+='body{background:#e9f4ef;color:#173c37}h1{color:#087f87}section{padding:20px;border:1px solid #c4ded4;border-radius:16px;background:#f5fbf7;margin:20px 0}.group-blocks{display:grid;grid-template-columns:repeat(auto-fit,minmax(28px,1fr));gap:4px;margin:8px 0}.group-block{border:1px solid #a5c2ba;border-radius:5px;text-align:center;font-size:11px;padding:5px;background:#dfece6}.group-block.reused{background:repeating-linear-gradient(135deg,#cce9dc,#cce9dc 5px,#e0f3e8 5px,#e0f3e8 7px)}.group-block.problem{background:#f3dde1}.group-block.pending{border-style:dashed;background:transparent}.track{height:18px;border-radius:4px;overflow:hidden;background:#dae8e2}.bar{height:100%;flex-shrink:0}.bar.full{background:#9ebeb5}.bar.executed{background:#07848b}.bar.avoided{background:repeating-linear-gradient(135deg,#cce8dc,#cce8dc 4px,#adcdbc 4px,#adcdbc 6px)}@media(max-width:600px){body{padding:16px;font-size:12px}td,th{padding:7px}table{display:block;overflow:auto}section{padding:12px}}';doc.head.append(style);
    const add=(tag,text,parent=doc.body)=>{const node=doc.createElement(tag);node.textContent=text;parent.append(node);return node;};
    const s=report.summary;
    const savings=report.revalidation_savings;
    const display=report.display || outcomePresentation(report.batch,s,savings);
    add('h1',msg('Click · 검증 효율 리포트'));
    add('h2',display.hero_title || report.labels?.omitted || OUTPUT_LABELS.omitted);
    add('p',`${display.omitted_text} · ${display.omitted_status_text}`);
    add('p',display.summary_text);
    add('p',display.result_text || '');
    add('p',(report.labels?.basis || OUTPUT_LABELS.basis).join(' / '));
    const taskDisplay=report.task_efficiency?.display;
    add('h2',msg('토큰 절감률'));
    add('p',taskDisplay?`${taskDisplay.token_value} · ${taskDisplay.token_reason}`:msg('미측정'));
    add('p',taskDisplay?.scope||msg('전체 작업 비교를 가져오지 않았습니다.'));
    add('h2',msg('전체 작업 효과'));
    add('p',taskDisplay?`${taskDisplay.task_value} · ${taskDisplay.task_reason}`:msg('미측정'));
    if(taskDisplay?.adverse)add('p',taskDisplay.adverse);
    const countSection=add('section','');
    add('h2',msg('전체 기준과 이번 실제 결과'),countSection);
    add('p',msg`전체 재실행 기준 ${count(s.total_source_count)}개 / 이번 실제 실행 ${count(s.executed_source_count)}개 + 재사용 ${count(s.authoritative_reuse_count)}개`,countSection);
    const fullBlocks=add('div','',countSection);fullBlocks.className='group-blocks';groupBlocks(doc,fullBlocks,report.batch?.sources || [],true);
    const actualBlocks=add('div','',countSection);actualBlocks.className='group-blocks';groupBlocks(doc,actualBlocks,report.batch?.sources || []);
    add('p',msg('● 실행 · ↺ 재사용 · ! 실패/중단 · · 미실행/미확정. 전체 기준은 관찰한 이전 실행이 아닙니다.'),countSection);
    if(display.comparison_ready) {
      [[display.full_text,100,'full'],[display.executed_text,display.current_percent,'executed']].forEach(([label,width,kind])=>{
        add('p',label,countSection);const track=add('div','',countSection);track.className='track';
        const bar=add('div','',track);bar.className=`bar ${kind}`;bar.style.width=`${width}%`;
        if(kind==='executed'){track.style.display='flex';const avoided=add('div','',track);avoided.className='bar avoided';avoided.style.width=`${100-width}%`;}
      });
      add('p',msg`빗금 구간: 피한 재실행 비용 ${estimatedDuration(savings.omitted_test_execution_ms)} [추정] · 같은 0 시작 시간 축`,countSection);
    }
    const comparisonTable=add('table','');
    [
      [report.labels?.full || OUTPUT_LABELS.full,display.full_text],
      [report.labels?.executed || OUTPUT_LABELS.executed,display.executed_text],
      [report.labels?.reduction || OUTPUT_LABELS.reduction,display.reduction_text],
    ].forEach(([label,value])=>{const tr=add('tr','',comparisonTable);add('th',label,tr);add('td',value,tr);});
    add('h2',msg('검증 묶음별 실제 결과'));const table=add('table','');
    const heading=add('tr','',table);[msg('이름'),msg('계획'),msg('실제 결과'),msg('시간 / 과거 표본'),msg('이유')].forEach(text=>add('th',text,heading));
    report.batch?.sources.forEach(item=>{const tr=add('tr','',table);const origin=item.status==='reused'?(item.reuse_origin?msg(' · 이전 계약에서 재판정'):msg(' · 같은 계약 재사용')):'';[item.label,executionLabels[item.decision]?.[0]||msg('없음'),statusText[item.status],item.status==='reused'?fmt(item.duration_baseline?.duration_ms)+msg(' (과거 성공 실행)'):fmt(item.duration_ms),(outcomeText[item.execution_reason_code]||reasonText[item.reason_code]||msg('정보 없음'))+origin].forEach(text=>add('td',text,tr));});
    add('h2',msg('접힌 화면과 같은 측정 상세'));
    add('p',msg`Hook 진입 → 결과 기록 부분 요청시간: ${fmt(report.measurement?.request_wall_ms)} / 현재 측정 가능한 처리 구간: ${fmt(report.measurement?.measured_processing_ms)}`);
    add('p',msg('Click 전체 관리비용: 측정 정보 없음. 포함 관계가 있는 시간을 빼서 관리비용을 만들지 않습니다.'));
    const taskMeasurement=report.task_efficiency?.presentation;
    add('h2',msg('전체 작업 비교 상세'));
    if(!taskMeasurement)add('p',msg('전체 작업 비교를 가져오지 않았습니다.'));
    else {
      add('p',`${taskDisplay.scope} · ${taskDisplay.basis}`);
      add('p',msg`표본 ${taskMeasurement.sample_count} · 완료 ${taskMeasurement.comparable_sample_count} / 실패 ${taskMeasurement.failed_sample_count} / 취소 ${taskMeasurement.cancelled_sample_count} / 미완료 ${taskMeasurement.incomplete_sample_count}`);
      add('p',msg`사용자 개입 ${taskMeasurement.baseline_user_intervention_count??msg('측정 정보 없음')} → ${taskMeasurement.improved_user_intervention_count??msg('측정 정보 없음')}`);
      add('p',msg('활동 구간은 겹칠 수 있어 전체 작업시간으로 합산하지 않습니다. 숨은 추론은 미측정입니다.'));
    }
    add('h2',msg('별도 paired 비교 실측'));
    if (!report.comparison) add('p',msg('비교 측정 없음. 일상 추정 비용을 실측한 전체 재실행 시간으로 환산하지 않습니다.'));
    else {
      add('p',msg`승인·재사용 권한 없음 · 반복 ${report.comparison.conditions.iterations} · 워밍업 ${report.comparison.conditions.warmups} · ${report.comparison.conditions.runtime_mode} · ${msg(report.comparison.conditions.cache)}`);
      add('p',report.comparison.version===2 ? msg('same-shards 행은 명령 구간, parent-suite 행은 Click 요청 구간을 비교하며 서로 합산하지 않습니다.') : msg('legacy paired는 전체 기준 명령의 wall time과 Click 요청 구간의 wall time을 비교합니다.'));
      add('p',msg('각 경로 중앙값과 쌍별 차이 중앙값은 서로 다른 통계입니다. 음수는 해당 fixture 비교에서 Click 측정 구간이 증가했다는 뜻입니다.'));
      if(report.comparison.cost_samples?.length){add('h2',msg('별도 비교의 준비·변경·추가 감사 비용'));report.comparison.cost_samples.forEach(item=>add('p',msg`${item.configuration} · 반복 ${item.iteration+1}${item.warmup?msg(' · 워밍업'):''} · 준비 ${fmt(item.setup_ms)} / 변경 ${fmt(item.transition_ms)} / 추가 감사 ${fmt(item.additional_full_audit_ms)} / 요청 합계 ${fmt(item.validation_wall_ms)} [실측 · 실패 포함]`));}
      const pairedTable=add('table','');comparisonRows(report.comparison).forEach(row=>{const tr=add('tr','',pairedTable);[row.label,msg`${row.n}회 / 제외 ${row.excluded}회`,fmt(row.baseline),fmt(row.incremental),row.delta===null?msg('성공 표본 없음'):msg`${row.delta<0?msg('증가 '):msg('감소 ')}${fmt(Math.abs(row.delta))} · ${row.percent===null?msg('비율 없음'):decimal(Math.abs(row.percent))+'%'} · 범위 ${fmt(row.min)} ~ ${fmt(row.max)}`].forEach(text=>add('td',text,tr));});
      const reference=report.comparison.repository_reference;
      add('h2',msg('실제 저장소 테스트 번들 참조'));
      if (!reference) add('p',msg('첨부된 실제 저장소 번들 참조 없음. fixture 결과를 저장소 전체 성능으로 일반화하지 않습니다.'));
      else {add('p',msg`커밋된 shard inventory · 묶음 ${reference.conditions.shard_count}개 · 성공 표본 ${reference.summary.eligible_samples}개 · Click 재사용 반사실이 아닌 그룹화 비용 참조`);const rt=add('table','');[[msg('모든 묶음 순차'),fmt(reference.summary.same_shards_duration_ms.median)],[msg('기존 parent 명령'),fmt(reference.summary.parent_suite_duration_ms.median)],['parent − shards',fmt(reference.summary.parent_minus_shards_ms.median)]].forEach(([label,value])=>{const tr=add('tr','',rt);add('th',label,tr);add('td',value,tr);});}
    }
    add('h2',msg('범위와 주의사항'));report.notes.forEach(text=>add('p',text));
    const raw=add('details','');add('summary',msg('기계 판독용 원시 ID·조건 (서명 없음)'),raw);add('pre',JSON.stringify(report,null,2),raw);
    return '<!doctype html>\n'+doc.documentElement.outerHTML;
  }

  function download(content,type,name) {
    const url=URL.createObjectURL(new Blob([content],{type}));const link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  $('clearComparison').onclick=()=>{comparison=null;taskEfficiencyImported=false;taskEvaluationLocalRef='';selectedTaskComparison='';try{taskEfficiency=snapshot?.task_efficiency?readTaskEfficiency(snapshot.task_efficiency):null;}catch(_){taskEfficiency=null;}$('comparisonFile').value='';renderComparison();};
  $('comparisonFile').onchange=async event=>{
    try {const file=event.target.files[0];if(!file)return;if(file.size>4*1024*1024)throw Error(msg('비교 파일은 4 MiB 이하만 읽습니다.'));const parsed=JSON.parse(await file.text());if(parsed?.kind==='click-task-efficiency-public'){taskEfficiency=readTaskEfficiency(parsed);taskEfficiencyImported=true;taskEvaluationLocalRef=shareLabel(file.name||'',0);selectedTaskComparison=taskEfficiency.presentations.length===1?taskEfficiency.presentations[0].comparison_ref:'';}else comparison=readComparison(parsed);renderComparison();}
    catch(error){renderComparison();$('comparisonInfo').textContent=msg`비교 파일을 읽지 못했습니다: ${error.message}`;}
  };
  $('taskEvaluationSelect').onchange=event=>{selectedTaskComparison=event.target.value;renderTaskEfficiency();if(snapshot)$('shareSummary').value=summaryCopy(activeBatch,activeSummary,activeSavings);};
  $('exportJson').onclick=()=>{try{download(JSON.stringify(shareReport(),null,2),'application/json','click-efficiency.json');$('exportStatus').textContent=msg(' JSON 내보내기 완료');}catch(error){$('exportStatus').textContent=error.message;}};
  $('exportHtml').onclick=()=>{try{download(standaloneReport(shareReport()),'text/html','click-efficiency.html');$('exportStatus').textContent=msg(' HTML 내보내기 완료');}catch(error){$('exportStatus').textContent=error.message;}};
  $('copySummary').onclick=async()=>{
    if(!snapshot)return;
    const text=summaryCopy(activeBatch,activeSummary,activeSavings);$('shareSummary').value=text;
    try {await navigator.clipboard.writeText(text);$('exportStatus').textContent=msg('요약 문구를 복사했습니다.');}
    catch(_) {$('shareSummary').focus();$('shareSummary').select();$('exportStatus').textContent=msg('자동 복사를 사용할 수 없어 문구를 선택했습니다. 복사 단축키를 사용하세요.');}
  };
  function applyFilter(filter,focus=false) {
    if(!snapshot)return;sourceFilter=filter;renderSources(batchView(snapshot,activeBatch));
    if(focus){$('checksSection').scrollIntoView({block:'start'});$('checksSection').focus({preventScroll:true});}
  }
  $('showReused').onclick=()=>applyFilter('reused',true);
  $('reusedChecks').onclick=()=>applyFilter('reused',true);
  $('filterAll').onclick=()=>applyFilter('all');
  $('filterReused').onclick=()=>applyFilter('reused');
  $('filterExecuted').onclick=()=>applyFilter('executed');
  $('refreshNow').onclick=()=>refresh();
  document.querySelectorAll('a[href="#measurementDetails"]').forEach(link=>{link.onclick=()=>{$('measurementDetails').open=true;};});
  document.querySelectorAll('.nav-link').forEach(link=>{link.onclick=()=>{
    document.querySelectorAll('.nav-link').forEach(other=>{other.classList.toggle('active',other===link);other.removeAttribute('aria-current');});
    link.setAttribute('aria-current','page');if(link.getAttribute('href')==='#measurementDetails')$('measurementDetails').open=true;
  };});
  $('batchSelect').onchange=event=>{selectedBatch=event.target.value;render(snapshot);};
  $('latestBatch').onclick=()=>{selectedBatch='';render(snapshot);};

  function render(data) {
    const focusId=document.activeElement?.id;
    const focusSource=document.activeElement?.dataset?.id;
    const identity=`${data.task.contract_id || ''}:${data.task.name || ''}:${data.history?.current_batch_id || ''}`;
    if(selectedTaskIdentity && identity!==selectedTaskIdentity && !selectedBatch){sourceFilter='all';selected='';}
    selectedTaskIdentity=identity;
    snapshot = data;
    if(!taskEfficiencyImported&&data.task_efficiency){try{taskEfficiency=readTaskEfficiency(data.task_efficiency);}catch(_){taskEfficiency=null;}selectedTaskComparison=taskEfficiency?.presentations?.length===1?taskEfficiency.presentations[0].comparison_ref:'';}
    setConnection('연결됨');
    document.querySelector('.live').classList.add('ok');
    $('taskline').textContent = data.task.runtime_mode === 'unknown'
      ? msg('현재 실행 중인 검증이 없습니다. 아래에서 최근 검증 결과를 볼 수 있습니다.')
      : msg`${data.task.runtime_mode === 'guarded' ? 'Guarded' : 'Evidence'} 모드 · 변경 ${data.task.mutation_revision} · ${statusText[data.task.status] || msg('상태 확인 중')}`;
    const view = renderBatch(data);
    const selectedMetrics = activeBatch ? data.batch_summaries?.[activeBatch.batch_id] : null;
    const currentMetrics = !activeBatch || activeBatch.batch_id === data.history?.current_batch_id;
    const incremental = selectedMetrics?.incremental || (currentMetrics ? data.summary.incremental : {}) || {};
    const savings = selectedMetrics?.revalidation_savings || (currentMetrics ? data.summary.revalidation_savings : {}) || {};
    activeSummary = incremental;
    activeSavings = savings;
    const shadow = data.summary.shadow;
    const historical=Boolean(activeBatch && activeBatch.batch_id!==data.history?.current_batch_id);
    $('selectionLabel').textContent=historical ? msg('과거 배치 기록 · 현재 요청과 별개') : msg('현재 작업');
    $('contractName').textContent = historical ? activeBatch.task?.name || msg('이전 작업') : data.task.name || msg('현재 작업');
    if(historical) $('taskline').textContent=msg`${new Date(activeBatch.timestamp*1000).toLocaleString(localeTag())} · 변경 ${activeBatch.current_revision} · ${statusText[activeBatch.status] || msg('미확정')}`;
    $('batchTimestamp').textContent=activeBatch?new Date(activeBatch.timestamp*1000).toLocaleString(localeTag()):'';
    $('engineVersion').textContent=data.engine?.version ? `v${data.engine.version}` : msg('버전 정보 없음');
    $('approvalState').textContent = data.task.approval_bound ? msg('별도 승인됨 · Guarded') : data.task.runtime_mode === 'evidence' ? msg('호스트 권한 · Click 승인 없음') : data.task.status === 'staged' ? msg('승인 대기 · Guarded') : data.task.status === 'none' ? msg('활성 계약 없음 · 이력 전용') : msg('승인 정보 없음');
    $('scopeDetails').hidden=historical;
    if(historical) $('approvalState').textContent=msg('과거 결과 · 현재 승인 상태와 별개');
    $('contractId').textContent = data.task.contract_id || msg('승인 계약 ID 없음');
    const paragraphs = values => values.map(text => {const p=document.createElement('p');p.textContent=text;return p;});
    $('contractPromises').replaceChildren(...paragraphs(data.task.promises?.length ? data.task.promises.slice(0,2) : [msg('표시 가능한 약속 요약이 없습니다. 기존 승인 계약 또는 사용자 요청을 확인하세요.')]));
    $('allPromises').replaceChildren(...paragraphs(data.task.promises || []));
    $('contractBoundary').replaceChildren(...paragraphs([
      msg`포함: ${(data.task.in_scope || []).join(' · ') || msg('원문 확인')}`,
      msg`제외: ${(data.task.out_of_scope || []).join(' · ') || msg('원문 확인')}`,
      msg`유지 조건: ${(data.task.must_hold || []).join(' · ') || msg('원문 확인')}`,
    ]));
    const controls=data.controls || [];
    $('controlSummary').textContent = msg`이 계약의 관측 통제: 차단 ${controls.filter(item=>item.effect==='blocked').length}건 · 비차단 안내 ${controls.filter(item=>item.effect==='advisory').length}건. 의미적 범위 준수나 숨은 추론을 판정한 수치가 아닙니다.`;
    if (data.task.status==='none') $('controlSummary').textContent=msg('활성 계약이 없습니다. 보관된 viewer 이력은 승인이나 실행 권한을 전달하지 않습니다.');
    const reusedItems=(activeBatch?.sources || []).filter(item=>item.status==='reused');
    const prior=reusedItems.filter(item=>item.reuse_origin).length;
    $('reuseOrigins').textContent=msg`선택한 배치의 실제 재사용: 같은 계약 ${reusedItems.length-prior}개 · 이전 계약에서 재판정 ${prior}개`;
    const a=data.accounting;
    $('reuseRate').textContent=a ? msg`보관된 검증 그룹 요청 기준: ${a.reuse_numerator} / ${a.request_denominator} · ${a.reuse_rate===null?msg('비율 미측정'):(100*a.reuse_rate).toFixed(1)+'%'} · ${a.from_timestamp?new Date(a.from_timestamp*1000).toLocaleString(localeTag()):msg('시작 기록 없음')} ~ ${a.through_timestamp?new Date(a.through_timestamp*1000).toLocaleString(localeTag()):msg('종료 기록 없음')}. 실제 재시도는 별도 요청이며 중복 수신·화면 갱신은 추가 집계하지 않습니다.` : msg('집계 정보 없음');
    $('zeroReuse').textContent=reusedItems.length ? msg`재사용 중 과거 시간 표본 미측정 ${Math.max(0,(savings?.coverage?.actual_reused_source_count ?? 0)-(savings?.coverage?.timed_reused_source_count ?? 0))}개` : [...new Set(view.sources.map(source=>(source.next_action ? msg(source.next_action) : reasonFor(source))))].slice(0,3).join(' ');
    const outcome=renderOutcome(activeBatch,incremental,savings);
    $('requestWall').textContent = fmt(incremental.request_wall_ms);
    $('processingDuration').textContent = fmt(incremental.measured_processing_ms);
    $('timeScope').textContent = activeBatch?.measurement_scope === 'hook-entry-to-result-recording' ? msg('부분 실측: 같은 호스트의 단조 시계로 Hook 진입부터 결과 기록 직전까지 측정했습니다. Hook 이전 요청 대기·최종 저장·호스트 반환은 제외합니다.') : activeBatch?.measurement_scope === 'prepare-only' ? msg('부분 계측: 준비·재사용 판정만 포함. 호스트 대기·전달·최종 저장·반환은 제외합니다.') : msg('부분 계측: 준비 + runner의 개별 경과시간 합계. 호스트 대기·전달·최종 저장·반환은 제외합니다.');
    if (!activeBatch) $('timeScope').textContent=msg('이 계약의 요청-결과 시간은 아직 측정되지 않았습니다.');
    $('detailExecutedDuration').textContent = outcome.executedText;
    $('detailFullEstimate').textContent = outcome.fullText;
    $('managementOverhead').textContent = msg('측정 정보 없음');
    const setup=data.setup || {};
    $('setupStatus').textContent = setupStatusText[setup.status] || setup.status || msg('미설정');
    $('setupInitial').textContent = fmt(setup.initial_setup_ms);
    $('setupObservation').textContent = fmt(setup.observation_ms);
    $('setupProcessing').textContent = fmt(setup.click_processing_ms);
    $('setupRuns').textContent = `${fmt(setup.bootstrap_parent_ms)} / ${fmt(setup.bootstrap_shards_ms)}`;
    $('setupNet').textContent = signedDuration(setup.comparison_net_ms);
    $('setupScope').textContent = setup.comparison_scope === 'first-bootstrap-parent-vs-sequential-children-not-savings'
      ? msg('첫 기준 실행의 parent와 순차 shards 비교입니다. 절감 시간으로 집계하지 않습니다.')
      : msg('첫 기준 실행은 절감 시간으로 집계하지 않습니다.');
    $('shadowBreakdown').textContent = `${shadow.candidate_count} / ${shadow.confirmed_candidate_count} / ${shadow.contradiction_count}`;
    $('shadowTiming').textContent = `${fmt(shadow.potential_ms)} / ${fmt(shadow.observer_overhead_ms)}`;
    $('observerTitle').textContent = data.task.observer_mode === 'authoritative'
      ? 'Observer: authoritative'
      : data.task.observer_mode === 'shadow' ? msg('Observer: Shadow 켜짐') : msg('Observer: 꺼짐');
    $('observerBody').textContent = data.task.observer_mode === 'authoritative'
      ? msg('완전하고 현재 계약에 결합된 v2 관찰만 observed-input 재사용 권한이 됩니다.')
      : data.task.observer_mode === 'shadow'
        ? msg('예측 정확도를 측정하지만 검사 생략 권한은 만들지 않습니다.')
        : msg('Dashboard는 계속 볼 수 있으며 기존 exact·policy reuse는 정상 동작합니다.');
    const conditions = [
      msg`집계 범위: ${savings?.aggregation_scope || msg('측정 정보 없음')}`,
      msg`시간 기준: ${savings?.basis || msg('측정 정보 없음')}`,
      msg`측정 단위: ${savings?.unit || msg('측정 정보 없음')}`,
      msg`생략 시간 상태: ${savings?.omitted_test_execution_status || 'unmeasured'}`,
      msg`이번 실행시간 상태: ${savings?.executed_test_execution_status || 'unmeasured'}`,
      msg`전체 순차 실행 상태: ${savings?.full_sequential_test_execution_estimate_status || 'unmeasured'}`,
      msg`감소율 상태: ${savings?.test_execution_reduction_status || 'unmeasured'}`,
      msg`상태 사유: ${(savings?.reason_codes || []).join(' · ') || msg('없음')}`,
      msg`재사용 판정: exact ${count(incremental.exact_reuse_count)} · observed-input ${count(incremental.dependency_reuse_count)} · safe-change ${count(incremental.safe_change_reuse_count)}`,
    ];
    $('rawConditions').replaceChildren(...conditions.map(text => {const tag=document.createElement('span');tag.className='tag';tag.textContent=text;return tag;}));
    $('updated').textContent = msg`${new Date(data.generated_at * 1000).toLocaleTimeString(localeTag())} 갱신`;
    snapshot = view;
    renderSources(view);
    snapshot = data;
    renderComparison();
    $('shareSummary').value=summaryCopy(activeBatch,activeSummary,activeSavings);
    if(focusId) $(focusId)?.focus?.({preventScroll:true});
    else if(focusSource) [...document.querySelectorAll('.source')].find(node=>node.dataset.id===focusSource)?.focus?.({preventScroll:true});
  }

  async function refresh() {
    if (!token) {
      setConnection('접근 토큰 없음');
      return;
    }
    if(refreshInFlight)return;
    refreshInFlight=true;
    try {
      const response = await fetch('/api/v1/snapshot', {
        headers: {Authorization: `Bearer ${token}`},
        cache: 'no-store'
      });
      if (!response.ok) throw new Error(String(response.status));
      const data=await response.json();
      const signature=JSON.stringify({...data,generated_at:null});
      if (signature!==lastSnapshotSignature) {lastSnapshotSignature=signature;render(data);}
      else { setConnection('연결됨');document.querySelector('.live').classList.add('ok'); }
    } catch (_) {
      setConnection('연결 끊김');
      document.querySelector('.live').classList.remove('ok');
    } finally {refreshInFlight=false;}
  }
  applyStaticLanguage();
  $('languageSelect').onchange=event=>setLanguage(event.target.value);
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
