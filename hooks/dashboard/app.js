(() => {
  'use strict';
  const MESSAGES = Object.freeze(globalThis.ClickDashboardMessages);
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

  function snapshotSignature(data) {
    // These two timestamps describe projection generation, not a new result.
    // Measurement timestamps inside presentations remain meaningful changes.
    const value={...data,generated_at:null};
    if (data.task_efficiency) value.task_efficiency={...data.task_efficiency,generated_at:null};
    return JSON.stringify(value);
  }

  function acceptSnapshot(data) {
    const signature=snapshotSignature(data);
    if(signature===lastSnapshotSignature)return false;
    lastSnapshotSignature=signature;
    render(data);
    return true;
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
      if (!acceptSnapshot(data)) { setConnection('연결됨');document.querySelector('.live').classList.add('ok'); }
    } catch (_) {
      setConnection('연결 끊김');
      document.querySelector('.live').classList.remove('ok');
    } finally {refreshInFlight=false;}
  }
  applyStaticLanguage();
  $('languageSelect').onchange=event=>setLanguage(event.target.value);
  if (typeof module !== 'undefined' && module.exports) {
    // An explicit module boundary lets rendering and export consumers exercise
    // the same application without rewriting its source or starting polling.
    module.exports=Object.freeze({readComparison,readTaskEfficiency,comparisonRows,
      summaryCopy,render,renderBatch,renderSources,renderMap,renderComparison,
      renderTaskEfficiency,taskEfficiencyPresentation,publicTaskEfficiency,
      outcomePresentation,renderOutcome,explain,standaloneReport,shareReport,
      setLanguage,applyStaticLanguage,msg,MESSAGES,getLocale:()=>locale,
      snapshotSignature,acceptSnapshot,
      setState(data,batch,summary,savings,measured,taskMeasured){
        snapshot=data;activeBatch=batch;activeSummary=summary;activeSavings=savings;comparison=measured;
        if(taskMeasured!==undefined){taskEfficiency=taskMeasured;taskEfficiencyImported=Boolean(taskMeasured);
          selectedTaskComparison=taskMeasured?.presentations?.length===1?taskMeasured.presentations[0].comparison_ref:String();}
      }});
  } else {
    refresh();
    setInterval(refresh, 1500);
  }
})();
