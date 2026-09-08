# 샤드 재사용에 따른 재검증 절감 가시화

> 상태: Phase 0 명세 · Phase 1 시간·출처 · Phase 2 단일 집계 · Phase 3 Dashboard · Phase 4 설정 · Phase 5 별도 프로젝트 E2E 완료
> 범위: Phase 0–5 구현과 문서·플랫폼 정합화까지 설명한다. 최종 증거의 권위는 이 문구가 아니라 마지막 변경 뒤 실행한 Click 검증 receipt에 있다.

이 문서의 `MUST`, `MUST NOT`, `SHOULD`는 후속 구현의 규범 요구사항을 뜻한다. 제품의 재사용·실행 권한은 기존 Click receipt, dependency observation, committed safe-change policy와 Guarded 승인이 계속 결정한다. 이 문서의 시간 지표, projection, Dashboard, benchmark, 공유 보고서는 휴리스틱·설명 계층이며 실행, 재사용, 승인, 완료를 허가하는 입력이 아니다.

## 1. 목표

1. 새 계약에서 코드가 바뀐 뒤에도 기존 권한 규칙으로 재사용이 실제 적용된 샤드는 runner에 넘기지 않고, 나머지 샤드만 실행한다.
2. 완료된 검증 요청의 대표 성과를 **재사용으로 생략한 테스트 실행시간 추정**으로 표시한다.
3. 실제 실행 시간, 생략 시간, 모든 샤드를 순차 실행했을 때의 추정 시간, 시간 감소율을 서로 같은 범위에서 계산한다.
4. 값의 출처와 완결성에 따라 `measured`, `estimated`, `partial`, `unmeasured`를 명시하고, 결측을 0으로 바꾸지 않는다.
5. 대시보드 첫 화면에는 생략한 테스트 실행시간과 조건이 충족된 경우의 감소율을 우선한다. 관리·전달 시간, 요청 시간, 순시간 차이는 상세에서 계측 범위와 함께 보여준다.

## 2. 비목표

다음은 Phase 0–5 전체에서도 하지 않는다.

- exact receipt, runtime dependency observation, committed safe-change policy, successor requalification의 재사용 권한을 넓히지 않는다.
- Guarded 승인 의미, 별도 턴, contract id binding, one-use runner, fail-closed 조건을 바꾸지 않는다.
- Shadow를 authoritative dependency discovery로 올리거나 자동 의존성 발견을 추가하지 않는다.
- 새 샤딩 알고리즘, 병렬 scheduler, 일반 cache, 원격 분석 서비스를 도입하지 않는다.
- 관리 비용을 최적화하거나 특정 절감율을 통과 기준으로 삼지 않는다.
- 추정 생략 시간을 원래 parent suite의 실측 wall time, 병렬 wall time, 토큰·요금·전체 개발 시간, 관리 비용을 반영한 사용자 순 대기시간 절감으로 부르지 않는다.
- 이 Phase 0에서 코드, UI, schema, 테스트, reuse manifest를 수정하지 않고 commit, push, PR, release, publish를 수행하지 않는다.

## 3. 용어와 불변 규칙

- **샤드 / 검증 묶음(source)**: 하나의 evidence id에 연속으로 결합된 argv 그룹이다. 샤드 맵이 적용되면 사용자가 제출한 parent evidence id는 승인 식별로 남고, 실제 계획·실행·시간의 단위는 내부 child source가 된다. 개별 test case 수가 아니다.
- **실제 재사용**: 최종 source `status == "reused"`인 경우만 뜻한다. `decision`이 `reuse-*`이지만 `reuse-pending`, `planned`, `not-run`인 항목은 실적이 아니다.
- **실제 실행**: target process의 시작이 관찰되어 `started == true`인 source다. 앞 검사 실패로 시작하지 않은 source는 실행이 아니다.
- **시간 표본**: source의 모든 argv가 성공한 실제 runner 실행 1회의 호출 구간이다. 지금 schema의 `sample_count` 1은 통계 평균이 아니라 단일 관찰을 뜻한다.
- **완결 범위**: 요청 source 수와 보관 source 수가 같고, 최종 batch가 `passed`이며, 모든 source가 `passed` 또는 `reused`로 확정된 상태다.
- **순차 비교**: 각 source 시간을 더해 다시 실행 시간을 추정할 수 있도록 source가 서로 겹치지 않고 같은 계측 경계로 순차 실행된 모델이다. 현재 runner는 이 조건을 만족하지만 표본에 메타데이터가 없어 구버전 표본에서 자동 증명하지 못한다.

## 4. 현재 데이터 흐름

```text
click-gate verify의 parent argv
  → 검증 요청 검사 + committed shard map 확장
  → child source별 기존 권한 규칙 재판정
  → canonical incremental plan + 실행할 source만 argv batch에 남김
  → one-use runner의 source별 시작·종료·시간 관찰
  → 최신 성공 시간 baseline + actual verification-batch 기록
  → click_incremental.batch_summary
  → click_dashboard_projection.dashboard_projection
  → loopback Dashboard snapshot API
  → 브라우저 표시 + JSON/독립 HTML 공유본
```

| 구간 | 현재 생성자와 구조 | 확인한 동작 | 현재 공백 |
| --- | --- | --- | --- |
| 요청·샤드 확장 | `hooks/click_verification.py::_prepare_verification_impl`, `::_expand_evidence_shards`, `hooks/click_evidence_shards.py`, `.click/evidence-shards.json` | 현재 broad `unittest discover`는 커밋된 inventory에 따라 6개 child shard로 분해된다. 맵이 불완전하면 parent suite로 fail closed한다. | parent 명령의 wall time과 child 시간은 동일 값이 아니다. |
| 재사용 판정 | `click_verification.py::_canonical_incremental_plan` 전단의 exact/dependency/safe-change/successor binding 검사 | 권한 검사가 끝난 뒤 child source별 `run`, `not-evaluable`, `reuse-*`를 만든다. | 시간 적합성은 권한 판정의 일부가 아니다. |
| 실제 실행 선택 | `hooks/click_incremental.py::keys_to_execute`, `click_verification.py::_prepare_verification_impl` | `reuse-*`가 아닌 source만 최종 runner `checks`에 남긴다. 모두 재사용이면 runner를 띄우지 않고 `finish_reuse`로 완료한다. | 없음. 이 동작은 이미 제품 목표를 만족한다. |
| source 실행 시간 | `click_verification.py::_run_verification` | 각 source의 연속 argv를 `perf_counter_ns` 구간으로 재고 `source_durations_ms`에 더한다. 첫 nonzero exit에서 뒤 source는 시작하지 않는다. | Observer 설정, 계측 schema, 순차 모델이 baseline 자체에 결합되지 않았다. |
| 성공 baseline | `click_verification.py::_record_verification_result` | source의 모든 argv가 통과한 경우만 `last_success_duration_baseline`을 저장한다. `duration_ms`, revision, check digest, observed time, batch id, `sample_count: 1`을 가진다. | 구버전 정수 `last_success_duration_ms`는 0으로 fallback되며 새 추정의 근거로 쓰지 않는다. baseline에 계측 문맥이 부족하다. |
| actual batch | `click_incremental.py::new_batch`, `mark_started`, `mark_completed`, `record_execution`, `finish_reuse`, `interrupt_batch` | 계획과 별개로 actual status, started/completed, source duration, reuse origin, 미실행 이유를 보관한다. | 파생 절감 지표의 완결성·적합성 상태가 없다. |
| 집계 | `click_incremental.py::batch_summary` | 실제 시작 source 시간 합 `executed_duration_ms`과 actual reused source의 유효 baseline 합 `estimated_avoided_ms`를 계산한다. | `F`, `R`, 순차 비교 가능성, metric status가 없다. 일부 baseline만 있어도 합계 숫자가 나올 수 있다. |
| projection | `hooks/click_dashboard_projection.py::dashboard_projection` v4 | 실제 summary, source, 최대 30개 batch, 시간 baseline, 재사용 출처를 제한된 정보로 복사한다. | 선택한 과거 batch의 summary는 Python이 아닌 Dashboard JS가 다시 계산해 드리프트 가능성이 있다. |
| Dashboard | `hooks/click_shadow_dashboard.py` HTML/JS | 첫 화면의 4개 card가 요청/실행/재사용 개수 뒤 4번째로 생략 비용을 보여준다. 요청·처리·실행 구간과 수 기준 재사용률을 따로 보여준다. | 생략 시간이 대표가 아니고 `F/R`과 부분/미측정 대표 상태가 없다. |
| Dashboard API | `click_shadow_dashboard.py::_DashboardHandler`, `/api/v1/snapshot` | loopback Host, Bearer token, no-store, CSP, read-only GET 검사 뒤 projection만 반환한다. | 없음. 현재 보안·권한 경계를 그대로 유지해야 한다. |
| 공유 내보내기 | `click_shadow_dashboard.py::shareReport`, `::standaloneReport` 로직 | 브라우저가 선택 batch를 JSON v2/독립 HTML로 만든다. 계약 문구, raw argv, 입력 경로, 환경 값, token을 뺀다. | 새 지표와 상태가 없고 JS 재계산에 의존한다. |
| 비교 실측 | `benchmarks/incremental_verification.py::Fixture.verify`, `::Fixture.full`, `::run_benchmark`, `::run_guarded_workflow_benchmark`, `::run_repository_bundle_reference` | legacy v2를 유지한다. v4는 8개 단계에서 같은 최종 입력 상태의 Click 요청, 같은 샤드 전체 실행, 기존 parent 명령을 교차하고 setup, transition, full audit를 분리한다. 커밋된 실제 저장소 6샤드와 parent 명령의 그룹화 참조도 별도 source로 기록한다. | fixture 결과는 일반 저장소 순절감이 아니다. 실제 저장소 참조는 Click 재사용 반사실이 아니라 그룹화 비용 1회 표본이다. live 요청에 대응하는 counterfactual은 없다. |
| completion receipt | `hooks/click_receipt_runtime.py::_evidence_receipts`, `hooks/click_receipt.py` | v2–v5 receipt는 실행 결과·환경·executable·host coverage·reuse lineage를 결합한다. | 성능 시간을 포함하지 않는다. 후속에도 efficiency export와 authority receipt를 합치지 않는다. |

## 5. 현재 시간 필드의 정확한 계측 경계

모든 내부 지속 시간의 기본 단위는 millisecond(ms)이며 finite, non-negative number다. 화면에서만 ms 또는 초로 서식화한다.

| 필드 | 지금 측정하는 구간 | 포함 / 제외 |
| --- | --- | --- |
| source `duration_ms` | `_run_verification` 내에서 각 argv의 `execute_current` 직전부터 반환까지. 하나의 evidence id에 argv가 여러 개면 순차 구간을 더한다. target 시작이 관찰된 경우만 저장한다. | process 시작·대기, 명령 실행, Observer가 켜져 있을 때 준비·수집·정리가 호출 안에 있으면 포함한다. 순수 test CPU time은 아니다. runner의 전체 준비·상태 저장은 뺀다. |
| `last_success_duration_baseline.duration_ms` | 최근 source 전체가 성공했을 때의 source `duration_ms` 1개. | 실패, 미실행, 취소, 오래된 정수 fallback은 뺀다. 재사용 시 새 실측으로 불리지 않는다. |
| `executed_duration_ms` | `started == true`인 source `duration_ms`의 합. 시작 source가 0개이면 빈 합 0이다. | 현재는 실패·중단 source의 관찰 구간도 합에 들어간다. 하나라도 시간이 `null`이면 전체가 `null`이다. |
| `estimated_avoided_ms` | **실제 `reused`** source 중 strict `duration_baseline`이 있는 항목의 합. | 재사용 0개면 0. 재사용은 있지만 baseline이 하나도 없으면 `null`. 일부만 있으면 현재는 일부 합을 숫자로 반환하고 coverage count만 따로 제공한다. 이는 Phase 2에서 반드시 `partial`로 표시할 공백이다. |
| `prepare_duration_ms` | `_prepare_verification` 진입 직후부터 `_prepare_verification_impl` 반환 직후까지의 현지 `perf_counter_ns` 구간. | 요청 검사, shard 확장, 권한 재판정, 계획·runner 준비 로직을 포함한다. 이후 incremental batch 계측 저장과 host 반환은 뺀다. |
| `runner_duration_ms` | `_run_verification` 초입부터 `_record_verification_result` 내 `record_execution` 직전까지의 runner 현지 구간. | decode, claim, 작업트리 전·후 snapshot, 실제 명령, Observer 후처리를 포함한다. 최종 claim 완료, state write, process return은 뺀다. |
| `measured_processing_ms` | runner가 없으면 prepare, runner가 있으면 prepare + runner 현지 구간의 합. | 서로 다른 process의 clock origin을 빼지 않는다. host 대기·전달·최종 반환은 뺀다. |
| `request_wall_ms` | 준비 Hook 진입에서 `start_request_clock`을 저장하고, 같은 host monotonic clock·batch binding이 유효할 때 `complete_request_timing` 직전까지. | prepare→runner 전달 gap과 결과 fold까지 포함하지만 pre-Hook queue, 최종 state write, tool 반환은 뺀다. **사용자 대기 또는 요청 전체가 아니다.** |
| Shadow `observer_overhead_ms` | collector가 보고한 제한된 observer 준비 비용. | tracing slowdown을 실측한 값이 아니고 authoritative 관리 비용이 아니다. source `duration_ms`에서 무조건 더하거나 빼지 않는다. |
| benchmark incremental `wall_ms` | fixture driver가 Hook request를 보내기 직전부터 재사용 echo 또는 runner 반환까지. | 임시 fixture의 driver 구간이며 live host 요청 전체가 아니다. |
| benchmark baseline `wall_ms` | 같은 fixture의 child 샤드 또는 parent suite를 직접 dispatch하기 직전부터 반환까지. | 성공한 비워밍업 표본만 `delta_ms = baseline - incremental`의 자격이 있다. |

## 6. 지표 모델

### 6.1 대상 집합

선택한 하나의 actual batch에 대해 다음을 정의한다.

- `S`: batch에 보관된 전체 source.
- `U`: `S` 중 최종 `status == "reused"`인 source.
- `X`: `S` 중 `started == true`인 source.
- `Q_U`: `U` 중 7절의 시간 적합성을 만족한 baseline이 있는 source.
- `Q_X`: `X` 중 finite, non-negative source `duration_ms`가 있는 source.

`scope_complete`는 다음을 모두 만족할 때만 `true`다.

1. batch `status == "passed"`.
2. `requested_source_count`가 알려져 있고 `len(S)`와 같다.
3. 모든 source가 둘 중 하나다.
   - `passed`: `started == true`, `completed == true`.
   - `reused`: `started == false`, reuse decision과 기존 권한 결과가 일치.
4. `failed`, `interrupted`, `not-run`, `running`, `planned`, `reuse-pending`, `unknown`인 source가 없다.

`timing_complete`는 `len(Q_U) == len(U)`이고 `len(Q_X) == len(X)`일 때만 `true`다. `sequential_comparison_valid`는 `Q_U` 표본과 `Q_X` 결과가 모두 같은 source-command 계측 경계, compatible timing schema, 같은 Observer mode, `sequential` execution model을 사용할 때만 `true`다. 이 판정은 표시 자격이지 재사용 권한이 아니다.

### 6.2 공식과 표시 자격

#### A — 재사용으로 생략한 테스트 실행시간

```text
A_subtotal = Σ duration_baseline.duration_ms, source ∈ Q_U
```

- `scope_complete` 이고 `Q_U == U`이면 `A = A_subtotal`, status `estimated`.
- 완결 batch에 실제 재사용이 0개면 빈 합 `A = 0`, status `estimated`.
- 일부 표본만 있거나 batch 범위가 미완결인데 `Q_U`가 비어 있지 않으면 `value_ms = A_subtotal`, status `partial`. UI는 이를 `확인된 표본 합 ≥ value` 형태로만 표시한다.
- 실제 재사용은 있지만 적합 표본이 하나도 없으면 `value_ms = null`, status `unmeasured`.
- plan의 `estimated_avoided_ms`나 `reuse-pending`을 직접 A에 더하지 않는다.

#### E — 이번에 실제 실행한 샤드 시간

```text
E_subtotal = Σ source.duration_ms, source ∈ Q_X
```

- `scope_complete` 이고 `Q_X == X`이면 `E = E_subtotal`, status `measured`.
- 완결 all-reuse batch에서 `X`가 비어 있으면 `E = 0`, status `measured`.
- 실패·중단·진행 중 batch이거나 일부 source 시간이 없고 `Q_X`가 비어 있지 않으면 합계는 status `partial`.
- 시작은 관찰했지만 시간을 하나도 확정하지 못했거나 요청 범위 자체를 모르면 `value_ms = null`, status `unmeasured`.
- 미실행 source는 E에 0으로 더하지 않는다.

#### F — 같은 샤드를 모두 순차 실행했을 때의 예상 시간

```text
F = E + A
```

`scope_complete && timing_complete && sequential_comparison_valid`일 때만 숫자와 status `estimated`를 제공한다. 그렇지 않으면 `value_ms = null`, status `unmeasured`다. F는 같은 child source 구간의 합이며 parent suite의 실측 wall time이 아니다.

#### R — 테스트 실행시간 감소율 추정

```text
R = A / F
```

F의 자격 조건이 모두 참이고 `F > 0`일 때만 0–1 비율과 status `estimated`를 제공한다. 화면은 `100 * R`%로 서식화한다. `F == 0`이면 0%로 꾸미지 않고 `null / unmeasured / zero-denominator`다. 이 비율은 `history_accounting.reuse_rate`의 **수 기준 재사용 비율**과 다르다.

### 6.3 설명용 기준 예시

10개 샤드 중 8개가 실제 `reused`이고 8개의 적합한 과거 성공 표본 합이 400초, 나머지 2개의 이번 실행 합이 100초면:

```text
A = 400초
E = 100초
F = 100초 + 400초 = 500초
R = 400 / 500 = 0.8 = 80%
```

표시 문구는 `생략한 테스트 실행시간 400초 추정`, `테스트 실행시간 감소율 80% 추정`이다. `400초 순절감`, `사용자가 400초 덜 기다림`, `관리비용 반영 후 80% 절감`으로 표시하면 안 된다.

### 6.4 canonical 파생 schema

Phase 2의 단일 Python 계산기는 actual batch에서 다음 content-free object를 파생한다. 이 object를 evidence state, receipt, reuse 판정의 입력으로 저장하지 않는다.

```json
{
  "version": 1,
  "unit": "ms",
  "basis": "sequential-source-command-intervals",
  "omitted_test_execution_ms": 400000,
  "omitted_test_execution_status": "estimated",
  "executed_test_execution_ms": 100000,
  "executed_test_execution_status": "measured",
  "full_sequential_test_execution_estimate_ms": 500000,
  "full_sequential_test_execution_estimate_status": "estimated",
  "test_execution_reduction_ratio": 0.8,
  "test_execution_reduction_status": "estimated",
  "scope_complete": true,
  "timing_complete": true,
  "sequential_comparison_valid": true,
  "coverage": {
    "requested_source_count": 10,
    "actual_executed_source_count": 2,
    "timed_executed_source_count": 2,
    "actual_reused_source_count": 8,
    "timed_reused_source_count": 8
  },
  "reason_codes": []
}
```

숫자 field는 `null` 또는 finite, non-negative number다. ratio는 `null` 또는 0–1이다. status는 metric 성격에 따라 `measured`, `estimated`, `partial`, `unmeasured` 중 하나다. `reason_codes`는 중복 없이 정렬한 다음 안정 identifier만 쓴다.

- `request-not-finalized`
- `request-not-passed`
- `scope-incomplete`
- `executed-duration-missing`
- `reused-duration-sample-missing`
- `reused-duration-sample-incompatible`
- `legacy-timing-context-missing`
- `sequential-comparison-invalid`
- `zero-denominator`

## 7. 과거 시간 표본 적합성

### 7.1 MUST 조건

재사용 source의 baseline은 다음을 모두 만족할 때만 `Q_U`에 들어간다.

1. 현재 actual source가 `status == "reused"`이며 기존 authoritative requalification이 이미 성공했다.
2. baseline이 실제 `passed` source의 finite, non-negative `duration_ms`에서 만들어졌다. 실패, 취소, 미실행, Shadow candidate는 안 된다.
3. source key, child shard identity, exact check digest가 같다. parent suite 시간을 child baseline으로 나누거나 그 반대로 합성하지 않는다.
4. timing schema version, source-command measurement scope, `sequential` execution model, Observer mode가 새 요청과 호환된다. Observer mode가 다르거나 알 수 없으면 재사용 자체는 유지하되 시간 표본은 적합하지 않다.
5. timing binding이 check, environment, executable, host coverage, measurement schema의 content-free digest를 묶는다. 값 자체는 노출하지 않는다.
6. 단일 성공 표본 1개는 point estimate에 충분하되 UI가 `최근 실행 1회 기반 추정`임을 밝힌다. 통계적 신뢰구간을 암시하지 않는다. 임의의 표본 유효기간은 도입하지 않는다.

### 7.2 baseline schema 진화

현재 baseline field를 권한에 쓰지 않는 timing schema v2로 확장한다.

```json
{
  "version": 2,
  "duration_ms": 50000,
  "unit": "ms",
  "measurement_scope": "source-command-dispatch-through-return",
  "execution_model": "sequential",
  "observer_mode": "off",
  "timing_binding_digest": "<64-hex>",
  "source_key": "<64-hex>",
  "revision": 7,
  "check_digest": "<64-hex>",
  "observed_at": 1700000000,
  "batch_id": "<32-hex>",
  "sample_count": 1,
  "origin_task": {
    "mode": "guarded",
    "id": "ctr_<32-hex>"
  }
}
```

- `duration_ms`와 `unit`은 같은 ms 단위를 명시하고, `measurement_scope`는 source command dispatch 직전부터 반환까지의 구간임을 고정한다.
- `source_key`, `batch_id`, `origin_task`는 원본 실제 실행의 shard·batch·Guarded contract 또는 Evidence 작업을 식별한다. 현재 재사용 적용은 current batch의 task/status와 별도 `reuse_origin`에 남겨 원본 실행과 같은 사건으로 덮어쓰지 않는다.
- `timing_binding_digest`는 source, exact check, environment, executable, host coverage, 단위·측정 구간·순차 모델·Observer mode를 content-free하게 묶는다. revision과 task id는 계약 간 정당한 재사용을 막지 않도록 시간 호환성 digest에 넣지 않는다.
- v1 baseline은 기존 Dashboard·history 표시와 권한 호환성을 위해 계속 읽는다.
- v1에는 execution model·Observer·timing binding이 없으므로 새 `F/R`의 완전 표본으로 자동 승격하지 않고 `legacy-timing-context-missing`으로 남긴다.
- successor에서 baseline을 재판정할 때 시간 호환성이 틀려도 authoritative reuse는 기존 규칙 결과를 그대로 따르고, 새 절감 metric만 `partial` 또는 `unmeasured`로 내린다.
- 재사용한 baseline은 새 실행 표본으로 증식하지 않는다. 실제 재실행 후 성공했을 때만 최신 1개를 교체한다.

## 8. 결측·실패·취소·재시도·중복 처리

| 상황 | 규칙 |
| --- | --- |
| 계획만 있음 | `reuse-*` decision과 `reuse-pending`은 A가 아니다. A/E/F/R은 `unmeasured`. |
| 실행 전 거부 / reservation 만료 | 모든 source를 `not-run`으로 보고 A/E/F/R을 성과 0으로 표시하지 않는다. |
| 첫 source 실패 | 실제 시작한 source 구간만 E `partial`. 뒤 `not-run`은 E 또는 A에 넣지 않는다. 이미 `reused`로 확정된 source가 있으면 적합 baseline 합만 A `partial`. F/R은 `null`. |
| 사용자 취소 / Ctrl-C | 취소 전 완료 기록은 사실로 유지하되, 시작한 미확정 source는 `interrupted`, 나머지는 `not-run`이다. `not-run` 시간을 절감으로 세지 않고 F/R을 만들지 않는다. |
| workspace invalidation | reuse key를 적용한 것으로 세지 않고 batch를 미완결로 처리한다. 시간 관찰은 `partial`일 수 있으나 F/R은 없다. |
| 일부 reused baseline 결측 | 적합 표본만 더한 A subtotal을 `partial` + coverage count로 보여준다. 누락 source를 0ms로 보지 않고 F/R은 `null`. |
| 실행 duration 결측 | 확정한 구간만 E subtotal `partial`; 하나도 없으면 `null`. F/R은 `null`. |
| 재시도 | 새 tool use와 새 `batch_id`는 별도 요청이다. 실패 batch와 통과한 retry batch의 A/E를 합쳐 하나의 F/R로 만들지 않는다. 현재 선택 batch만 대표 지표에 쓴다. workflow 전체 비교는 benchmark에서만 한다. |
| 같은 batch 재전송 | `batch_id`로 upsert/no-op하고 실적을 추가하지 않는다. source key도 batch 안에서 유일해야 한다. Dashboard refresh와 export는 새 표본이 아니다. |
| source completion 재전송 | 동일 status/reason/duration은 idempotent. 서로 다른 재기록은 거부한다. |
| crash / 최종 저장 실패 | `running`, `unknown`, 미확정 종료를 성공으로 올리지 않는다. 확정 시간이 없으면 `null`. |
| legacy history | 계획-only 및 timing context 없는 기록은 개수·상태 호환 표시만 하고 F/R을 백필하지 않는다. |
| 재사용 0개의 정상 완료 | A=0. E가 있고 F>0이면 F=E, R=0%. 실패·거부의 0 reused를 정상 완료 0ms 절감으로 포장하지 않는다. |

## 9. 대시보드 정보 우선순위

### 9.1 첫 화면

1. 가장 큰 대표 card: `생략한 테스트 실행시간 추정` = A.
2. 보조 card: `테스트 실행시간 감소율 추정` = R. F/R 자격이 없으면 비율 숫자를 숨기고 사유를 보여준다.
3. 축소 요약: 전체 source / 실제 실행 / 실제 재사용 / 시간 적합 baseline coverage. 수 기준 reuse rate는 `검증 묶음 수 기준`이라고 명시하고 R 옆에 동등하게 배치하지 않는다.
4. 상시 문구: `과거 성공 실행 표본 기반의 테스트 구간 추정입니다. 관리 비용을 반영한 사용자 순 대기시간 절감이 아닙니다.`

metric status별 표시는 다음으로 고정한다.

| status | A 표시 | F/R |
| --- | --- | --- |
| `estimated` | `6분 40초 추정` + `8/8 재사용 샤드 표본` | 조건이 모두 참이면 표시 |
| `partial` | `확인된 표본 합 ≥ 6분 40초` + `8/10 표본`처럼 부분임을 숫자 바로 옆에 표시 | 숨김, 사유 표시 |
| `unmeasured` | `측정 정보 없음` + reason | 숨김 |
| 완결·재사용 0 | `0 ms · 실제 재사용 없음` | E>0이면 R=0%, F=E |
| 실패·취소·진행 중 | `요청 미완료`를 대표로 표시; 부분 값은 상세로 이동 | F/R 숨김 |

### 9.2 상세 시간 영역

다음은 접힌 상세에 두고 각 값의 measurement scope를 항상 함께 보여준다.

- E: 이번 source command 호출 구간.
- F: 같은 source를 모두 순차 실행했을 때의 추정. parent suite wall time이 아님을 표시한다.
- `request_wall_ms`: Hook 진입→결과 기록의 부분 request wall. `요청 전체`로 단축하지 않는다.
- `measured_processing_ms`: prepare + runner의 별도 현지 구간 합.
- `observed_non_test_ms = request_wall_ms - E`: batch가 완결되고 E가 `measured`이며 둘의 계측 구간이 호환되고 결과가 0 이상일 때만 제공한다. 표시명은 `관찰 구간의 비검사 시간`이다. Click 전체 관리 비용이나 사용자 전체 대기시간으로 부르지 않는다.
- `host_request_total_ms`: Phase 4에서 stable tool-use/batch id와 같은 host monotonic clock으로 유효한 verify PreToolUse 진입→재작성된 runner의 맞는 PostToolUse 반환을 결합할 수 있을 때만 실측한다. host adapter가 맞는 PostToolUse를 제공하지 않으면 `null / host-return-unobserved`다. 사용자 prompt 대기는 여전히 제외된다.
- `net_time_saving_ms`: live batch에서 A 또는 `A - observed_non_test_ms`로 만들지 않는다. 같은 scope의 baseline/incremental을 교대 순서로 실행하고 둘 다 통과한 paired benchmark의 `baseline_wall_ms - incremental_wall_ms`만 `비교 환경의 순 요청 시간 차이`로 보여준다. 일상 live batch에는 `null / counterfactual-not-measured`다.
- Shadow `observer_overhead_ms`는 non-authoritative 보조 정보로 따로 둔다. tracing slowdown이 미측정이므로 순절감에서 더하거나 빼지 않는다.

## 10. projection·Dashboard·공유 일치

1. `click_incremental.py`에 actual batch 하나를 받는 순수 계산기 `revalidation_savings(batch)`를 둔다. Python이 A/E/F/R·status·coverage·reason의 단일 정의다.
2. `click_dashboard_projection.py` projection은 v5로 증가하고 다음을 제공한다.
   - `summary.revalidation_savings`: 현재 batch의 파생 object 또는 전 field `null` 형태의 unmeasured object.
   - `batch_summaries`: 표시된 최대 30개 batch id를 각 `batch_summary` + `revalidation_savings`에 매핑한 content-free object.
3. Dashboard JS의 현재 `summarize(batch)` 계산은 제거하고 projection의 선택 batch summary만 렌더링한다. JS는 지표 의미를 재판정하지 않는다.
4. JSON efficiency report는 v3으로 증가하고 `revalidation_savings` object와 상세 timing scope를 그대로 담는다. 독립 HTML은 같은 object만 읽어 같은 값·status·coverage·reason을 표시한다.
5. 과거 projection v1–v4, efficiency report v2, batch v1–v4, baseline v1은 계속 읽되 새 field를 추측해 백필하지 않는다. 새 자격을 증명하지 못하면 `null / unmeasured`다.
6. completion receipt v1–v5에 성능 지표를 넣지 않고 efficiency report를 receipt 권한으로 역변환하지 않는다.
7. snapshot, JSON, HTML은 기존처럼 계약 원문, raw argv, 입력 경로, 파일 내용, 절대경로, 환경 값, runner/access token을 제외한다. 새 timing binding은 digest만 공유한다.

## 11. 실제 수정 대상

Phase 0에서는 아래 파일을 수정하지 않는다. 후속 Phase의 최소 변경 지점은 다음과 같다.

| Phase | 주 수정 대상 | 수정 내용 |
| --- | --- | --- |
| 1. 시간·출처 연결 | `hooks/click_verification.py` (`_run_verification`, `_record_verification_result`, `_requalify_successor_baseline`, `_canonical_incremental_plan`), `hooks/click_incremental.py` baseline validator | timing baseline v2의 scope/model/Observer/binding을 실제 성공 source에만 저장하고 successor에서 시간 적합성을 별도 판정한다. reuse 권한 결과는 바꾸지 않는다. |
| 2. 전체 대비 부분 집계 | `hooks/click_incremental.py` (`batch_summary`, 새 `revalidation_savings`, strict validators), `hooks/click_dashboard_projection.py` schema/validator | A/E/F/R, metric status, coverage, reason을 Python에서 한 번만 계산하고 projection v5의 current/history summary로 보낸다. |
| 3. 결과 중심 Dashboard | `hooks/click_shadow_dashboard.py` HTML/CSS/JS | A와 R을 hero에 우선하고 partial/unmeasured 상태, 개수 비율과 시간 비율 구분, 접힌 상세, JSON v3/독립 HTML 일치를 구현한다. |
| 4. 변경별 실측·보고서 | `benchmarks/incremental_verification.py`, 필요 시 `hooks/click_gate.py::_handle_post_tool` + `hooks/click_verification.py`의 별도 timing recorder | unchanged, partial reuse, related change, environment, failure, retry의 A/E/F/R을 기록하고 paired net difference를 상세에 연결한다. matched PostToolUse가 있을 때만 host request total을 기록한다. |
| 5. 회귀·문서·공유 일치 | `tests/test_click_incremental.py`, `tests/test_click_efficiency.py`, `tests/test_click_gate_verification.py`, `tests/test_click_evidence_shards.py`, `tests/test_click_dashboard_projection.py`, `tests/test_click_shadow_dashboard.py`, `tests/test_incremental_benchmark.py`, `VERIFICATION_EFFICIENCY.md`, `README.md`, `README.ko.md`, `README.zh-CN.md` | 계산·예외·privacy·legacy·cross-platform 회귀, 사용자 문구, export schema를 맞춘다. |
| 배포 파생본 일치 | `scripts/build_antigravity_distribution.py`, `scripts/validate_distribution.py`, `dist/antigravity/hooks/{click_incremental.py,click_verification.py,click_dashboard_projection.py,click_shadow_dashboard.py}` | source hook을 Antigravity 파생본으로 재생성하고 byte-for-byte validator를 통과한다. 파생본을 별도 의미 구현으로 만들지 않는다. |

새 테스트 파일을 만들지 않고 기존 소유 test module에 케이스를 추가하는 것을 우선한다. 새 test file이 불가피하면 `.click/evidence-shards.json`의 완전 inventory에만 포함하고, 이 작업을 재사용 권한 확대로 이용하지 않는다. `.click/evidence-reuse.json`은 이 기능을 위해 완화하지 않는다.

## 12. Phase별 완료 조건과 검증

| Phase | 완료 조건 | 주 검증 |
| --- | --- | --- |
| 0 | 현재 데이터 흐름, 정확한 계측 경계, 기존/수정/미측정 구분, A/E/F/R, 표본 적합성, 예외, UI, 수정 대상, 후속 검증이 이 문서에 모호함 없이 있다. | 코드 심볼·현재 테스트·참조 문서와 항목별 manual 대조, `git diff --check`. Phase 1 file이 바뀌지 않음을 `git status`/diff로 확인. |
| 1 | 실제 성공 source 표본에 timing context가 붙고, actual reused source의 적합 baseline만 A 후보가 된다. 시간 비적합이 reuse 권한을 바꾸지 않는다. | baseline v2 strict validation, exact/dependency/safe-change/successor, Observer mismatch, legacy v1, 실패·취소 unit/integration tests. |
| 2 | 단일 Python 계산기가 actual batch에서 A/E/F/R·status·coverage·reason을 이 문서와 같이 산출하고 JS에 계산 규칙이 남지 않는다. | 10/8 예시, all reuse, reuse 0, 일부 baseline, duration 결측, F=0, 실패, 취소, retry, duplicate, legacy가 독립 테스트로 통과. |
| 3 | 첫 화면의 대표가 A이고 R은 자격이 있을 때만 보이며, 순절감으로 오인할 문구가 없다. 상세·JSON·HTML이 같은 projection 값을 쓴다. | Node DOM unit test, projection validator, export privacy test, actual loopback Browser test에서 표시 우선순위·partial/unmeasured·responsive layout·no mutation/network를 확인. |
| 4 | paired benchmark가 실제 전체/부분 실행 구간, 순 차이, setup/transition/audit, 실패·재시도 제외/포함 규칙을 분리해 기록한다. | v2 paired importer 호환, v4 Guarded A→B workflow, 단계 내·workflow 교차 순서, 성공 비워밍업만 통계 자격, 범위 불일치 제외, 음수 delta 보존, 엄격한 JSON/HTML round-trip, 실제 저장소 번들 참조. live host total은 matched PostToolUse 없이 값을 만들지 않음. |
| 5 | source/dist, projection/Dashboard/JSON/HTML, 한·영·중 문서가 같은 의미이고 기존 Core/receipt/privacy 호환성을 유지한다. | 전체 deterministic suite, distribution validator, `git diff --check`, Linux/macOS/Windows CI, 실제 Dashboard Browser 검증. 기존 receipt v1–v5 offline validation 회귀. |

후속 실측의 최소 acceptance fixture는 다음을 포함해야 한다.

1. 10 source: 8 reused, A=400s; 2 executed, E=100s; F=500s; R=80%.
2. all reused: E=0, A/F 유효, R=100%.
3. no reuse + passed: A=0, F=E, E>0이면 R=0%.
4. reused baseline 1개 누락: A `partial`, F/R `unmeasured`.
5. failed first source + later not-run: E `partial`, not-run은 savings 0으로 세지 않음.
6. cancel during source: 완료/미확정/미실행 구분, F/R 없음.
7. retry success: 실패 batch와 retry batch를 별도 유지.
8. duplicate delivery/refresh/export: 값과 표본 수 불변.
9. Observer/timing binding mismatch: authoritative reuse는 유지, A/F/R 자격만 없음.
10. legacy batch/baseline: 예전 표시 가능, 새 완전 추정은 백필하지 않음.

## 13. Phase 0 결론과 남은 불확실성

### 이미 있는 기능

- committed shard map으로 parent suite를 child source로 분해하고 각 child를 독립적으로 재판정한다.
- 권한 규칙으로 재사용된 child를 runner batch에서 제거하고 필요한 child만 실제 실행한다.
- source별 실제 호출 시간, 최신 성공 표본, actual status, reuse origin, partial request timing, 최근 batch history가 있다.
- actual reused source의 과거 표본 합 A에 가까운 `estimated_avoided_ms`와 실제 시작 source 합 E에 가까운 `executed_duration_ms`가 있다.
- local read-only Dashboard, sanitized projection, JSON/독립 HTML, paired benchmark, Guarded A→B workflow benchmark가 있다.

### 필요한 수정

- baseline timing context v2와 authority와 분리된 시간 적합성 검사.
- A/E/F/R의 단일 Python 계산기, status, coverage, reason.
- projection current/history의 파생 지표와 JS 재계산 제거.
- A 우선 Dashboard, R 조건부 표시, 상세 시간, export 일치.
- benchmark/report의 필드·조건·설명 연결과 source/dist/docs 회귀.

### 현재 미측정 또는 제한

- live host의 맞는 verify PostToolUse 종료가 절감 batch에 결합되지 않아 최종 tool 반환까지의 request total은 없다. 후속 host가 안정적으로 제공하지 않으면 값은 계속 `null`이다.
- live batch에서 같은 상태의 full counterfactual을 실행하지 않으므로 사용자 순시간 절감은 알 수 없다. paired fixture delta만 해당 fixture의 실측 비교다.
- 현재 baseline은 최신 성공 1회이므로 분산·신뢰구간이 없다. Phase 1–5에서 임의의 평균이나 신뢰도를 추가하지 않고 point estimate로 라벨한다.
- source 호출 시간에 process startup과 Observer 영향이 들어갈 수 있어 순수 CPU time이 아니다. 호환되는 timing context가 없으면 F/R을 만들지 않는다.
- OS cache·scheduler·백그라운드 부하는 benchmark에서 완전히 제어하지 못한다. 반복·교대 순서·워밍업 제외·분포를 보고하고 음수 결과를 보존한다.
- Codex와 Antigravity의 PostToolUse surface가 동일한 request-total 계측을 제공하는지는 Phase 4에서 adapter별로 검증해야 한다. 불확실하면 adapter 간 거짓 parity 대신 `unmeasured`를 선택한다.

Phase 0의 완료는 이 명세와 조사 근거를 확인하는 시점이다. 후속 구현자는 이 문서의 조건을 축소하거나 결측을 0으로 바꾸지 말고 Phase 1부터 별도 진행해야 한다.

## 14. Phase 1 구현 기록

Phase 1은 기존 `duration_baseline`, verification receipt source, successor requalification 구조 안에서만 구현했다. Dashboard projection, A/E/F/R 집계 모델, UI, receipt export schema, 재사용 권한 규칙은 변경하지 않았다.

### 변경 파일

- `hooks/click_incremental.py`: timing baseline v2 validator/builder와 권한 판정과 독립된 시간 적합성 검사를 추가했다.
- `hooks/click_verification.py`: 실제 성공 source에만 원본 task/batch/source와 timing binding을 기록하고, 재사용 계획에는 적합한 시간 근거만 연결한다.
- `dist/antigravity/hooks/click_incremental.py`, `dist/antigravity/hooks/click_verification.py`: source hook의 배포 파생본을 재생성했다.
- `tests/test_click_incremental.py`: v2 strict validation, revision 독립성, Observer/source mismatch, legacy 호환성을 검증한다.
- `tests/test_click_gate_verification.py`: 별도 승인된 A→B→C 통합 경로, 원본 출처 연결, 반복 재사용 표본 불변, 누락·legacy·부적합 시간 근거, 실패·취소 표본 배제를 검증한다.
- `docs/architecture/revalidation-savings.md`: Phase 1 구현 결과와 검증·한계를 기록했다.

### 검증

- 명령: `python3 -m unittest discover -s tests -q`
- Click의 committed shard inventory가 broad 명령을 6개 child shard로 확장했고, 구현 직후 첫 실행은 6/6 shard가 모두 `OK`로 종료했다. 마지막 broad shard는 196 tests를 179.934초에 통과했다.
- 문서 기록을 포함한 최종 revision도 같은 명령으로 다시 검증하며, 결과는 Guarded 계약의 `E_REGRESSION` argv 증거에 기록한다.
- `git diff --check`는 오류 없이 통과했다. 전체 suite에는 source/dist byte-for-byte 배포 검증과 기존 승인·runner one-use·완료 상태 비승계 회귀가 포함된다.

### Phase 1 한계

- 기록한 시간은 순수 test CPU 시간이 아니라 source command dispatch 직전부터 반환까지의 호출 구간이며 process startup과 Observer 영향을 포함할 수 있다.
- baseline은 성공 실행의 최신 단일 표본(`sample_count: 1`)이다. 평균, 분산, 신뢰구간 또는 별도 성능 예측 엔진을 추가하지 않았다.
- legacy, 누락, timing binding 불일치 표본은 현재 재사용 사건에서 시간 미측정으로 남는다. 이 상태는 기존 exact/dependency/safe-change/successor 재사용 권한을 취소하거나 확대하지 않는다.
- 반복 재사용은 현재 batch의 별도 적용 사건과 immediate `reuse_origin`만 갱신한다. 원본 실제 실행의 `origin_task`, `batch_id`, `source_key`, `observed_at`, `sample_count`는 바꾸지 않는다.
- live 사용자 순 대기시간 절감과 관리 비용은 여전히 미측정이다. Dashboard와 A/E/F/R 파생 집계는 Phase 1 범위 밖이므로 구현하지 않았다.

## 15. Phase 2 구현 기록

Phase 2는 actual verification batch 하나를 입력으로 받는 순수 Python 계산기 `revalidation_savings(batch)`를 A/E/F/R의 단일 정의로 추가했다. 이 파생 객체는 evidence state나 receipt 권한 입력으로 저장하지 않으며, 실제 재사용·실행 판정을 바꾸지 않는다.

### 변경 파일

- `hooks/click_incremental.py`: canonical revalidation-savings v1 schema, strict validator, 결측·부분·완전 상태와 reason code, A/E/F/R 계산을 추가했다. 기존 `batch_summary`와 host summary도 적합한 timing v2 표본 및 같은 canonical 결과를 사용한다.
- `hooks/click_dashboard_projection.py`: projection을 v5로 올리고 현재 batch의 `summary.revalidation_savings`와 표시 이력별 `batch_summaries`를 제공한다. 각 이력 항목은 기존 incremental summary와 같은 canonical savings 객체를 함께 가지며 validator가 raw batch 재계산 결과와 일치하는지 검증한다. projection v4 읽기 호환성을 유지한다.
- `hooks/click_shadow_dashboard.py`: 선택 batch를 JavaScript에서 다시 합산하던 `summarize(batch)`를 제거했다. 기존 화면, host summary, JSON v3·독립 HTML 내보내기가 projection의 같은 객체를 사용하도록 연결했으며 화면 배치 개편은 하지 않았다.
- `tests/test_click_efficiency.py`: 400초+100초 기준식, 개수 재사용률과 시간 감소율 차이, all-run, all-reuse, 일부·legacy·부적합 시간, 실패·취소·재시도, 0 분모, 중복 수신, parent plan 제외를 검증한다.
- `tests/test_click_dashboard_projection.py`: 현재/이력 공통 값, 서로 다른 계약·배치 분리, duplicate batch 제거, v4 호환성과 content-free projection을 검증한다.
- `tests/test_click_shadow_dashboard.py`: client-side 재계산 제거 및 canonical export 연결을 검증한다.
- `dist/antigravity/hooks/`: 변경한 source hook의 배포 파생본을 재생성했다.

### 계산 및 출력 결과

- A는 최종 `status == "reused"`인 source 중 source/check가 일치하는 strict timing v2 표본만 합한다. 일부 표본만 있으면 알려진 합과 coverage를 `partial`로 제공하고 F/R은 `null`이다.
- E는 실제 `started == true`인 source의 확정 duration만 합한다. 완결 all-reuse는 E=0 `measured`이고, 실패·취소·duration 결측은 부분 또는 미측정이다.
- `scope_complete && timing_complete && sequential_comparison_valid`일 때만 F=E+A를 제공한다. F>0일 때만 R=A/F를 제공하고 F=0은 `zero-denominator`로 남긴다.
- aggregation scope는 batch id로 분리된 `actual-expanded-verification-batch`, 비교 basis는 `sequential-source-command-intervals`다. parent 계획 이벤트, reuse candidate, Shadow 제안, 미실행 source, 다른 batch는 현재 값에 합치지 않는다.
- 보관 누적 A/E/F/R은 새로 만들지 않았다. 기존 요청 수 집계는 계속 `retained-history` 기간·상한과 timestamp 범위를 명시한다.

### 검증

- 명령: `python3 -m unittest discover -s tests -q`
- 구현 및 테스트 수정 후 committed shard inventory가 명령을 6개 child shard로 확장했고 40/58/190/124/73/200 tests가 모두 `OK`로 종료했다. 합계 685 tests, 5 skipped다.
- `git diff --check`와 suite 내 source/dist byte-for-byte 배포 검증이 통과했다.
- 이 문서와 최종 배포 파생본을 포함한 최종 revision도 같은 `E_REGRESSION` 명령으로 다시 검증하며 Guarded evidence에 기록한다.

### Phase 2 한계

- A/F/R은 같은 샤드의 순차 source-command 호출 구간 추정이다. 원래 parent suite wall time, 병렬 wall time, 관리비용 차감 후 순절감 또는 사용자 전체 대기시간이 아니다.
- 현재 실행기는 순차 모델이므로 그 basis만 활성화한다. 향후 병렬 실행 모델을 도입하면 별도 호환 가능한 실행 모델 증거 없이는 F/R을 비활성화해야 한다.
- latest successful sample 1개 기반 point estimate이며 분산·신뢰구간을 추가하지 않았다.
- 기존 Dashboard의 정보 배치와 대표 카드 개편은 Phase 3 범위로 남겼다. Phase 2에서는 기존 표시·내보내기가 서버 projection의 공통 값만 읽도록 연결했다.

## 16. Phase 3 구현 기록

Phase 3는 Phase 2의 `revalidation_savings`와 projection v5를 다시 계산하지 않고 그대로 표시한다. Dashboard의 정보 순서를 생략한 테스트 실행시간, 동일 샤드 전체 대비 이번 실행, 샤드별 재사용 근거, 접힌 측정 상세 순으로 바꿨다. 권한·영수증·재사용 판정·benchmark 실행 방식은 변경하지 않았다.

### 변경 파일

- `hooks/click_shadow_dashboard.py`: 생략 시간을 가장 큰 대표 수치로 올리고, 추정 기준 3가지를 상시 표시한다. 전체/이번 실행은 같은 시간 축에서 F와 E가 모두 적격일 때만 막대로 그리며, 부분·미측정·진행·실패·취소 상태에는 막대와 감소율을 숨긴다. 샤드 카드와 선택 상세는 같은 계약 재사용과 이전 계약 재판정을 구분하고, 이전 작업명→원본 성공 실행→현재 적용 계보를 제공한다. 원시 ID와 timing binding은 별도 상세에 둔다.
- `hooks/click_shadow_dashboard.py`: 요청 부분시간, 측정 가능한 처리 구간, 미측정 Click 전체 관리비용, 별도 paired 비교의 순 요청시간 차이, Observer 보조 정보와 원시 조건을 하나의 접힌 측정 상세로 이동했다. 포함 관계가 있는 값을 빼서 관리비용을 만들지 않는다.
- `hooks/click_shadow_dashboard.py`: JSON v3과 독립 HTML이 Dashboard와 같은 label/display object 및 canonical savings object를 사용한다. 독립 HTML은 사용자·가져오기 값을 `textContent`로만 구성하고 원시 기계 판독 데이터는 접힌 상세에 둔다. 가져온 비교 보고서는 승인·실행·재사용 권한이 아님을 표시한다.
- `hooks/click_incremental.py`: 기존 host summary 경로가 같은 A/E/F/R과 같은 한국어 라벨을 사용해 `N개 중 E개만 실행 · R개 재사용으로 … 테스트 재실행 생략〔추정〕` 형태로 출력한다. 완료되지 않은 요청, 부분 시간, 재사용 없음은 각각 별도 문구로 보존한다.
- `tests/test_click_efficiency.py`: first run, running, all reuse, no reuse, failure, cancellation, partial timing 상태의 대표 수치·막대·감소율을 Node DOM에서 검증한다. 이전 계약 계보에서 원시 ID가 접힌 상세에만 나타나는지, paired 총 대기 증가 안내, JSON/HTML 공통 라벨과 HTML 삽입 방지도 검증한다.
- `tests/test_click_shadow_dashboard.py`: 화면의 네 영역 순서, 고정 추정 기준, 접힌 측정 상세, 가짜 최소 막대 제거, 공통 projection 소비와 loopback viewer 보안 회귀를 검증한다.
- `dist/antigravity/hooks/click_incremental.py`, `dist/antigravity/hooks/click_shadow_dashboard.py`: 기존 distribution 생성기로 source와 byte-for-byte 동기화했다.

### 검증

- 명령: `python3 -m unittest discover -s tests -q`
- 구현 revision에서 committed shard inventory가 상위 명령을 6개 child shard로 확장했고 40/58/190/124/73/200 tests가 모두 `OK`로 종료했다. 합계 685 tests, 5 skipped다.
- 전체 suite 안에서 source/dist 동기화, Dashboard JavaScript 구문, 상태별 렌더링, projection/JSON/HTML 공통 값, Guarded 승인·실행 권한·완료 상태 비승계를 함께 확인했다.
- `git diff --check`와 변경한 source/dist 파일의 byte-for-byte 비교가 통과했다. 이 문서를 포함한 최종 revision도 같은 `E_REGRESSION` 명령으로 다시 검증한다.
- 실제 Browser로 설치된 `click-gate dashboard` 연결과 읽기 전용 loopback 동작은 확인했지만, 설치 캐시가 작업트리보다 앞선 Dashboard asset을 제공해 새 Phase 3 화면의 시각 증거로 채택하지 않았다. 작업트리 상태별 동작은 위 Node DOM 검증으로 다뤘으며, 배포 후 Browser 재검증이 필요하다.

### Phase 3 한계

- live host의 전체 요청 반환시간과 Click 전체 관리비용은 계속 미측정이다. `request_wall_ms` 또는 E와의 차이를 관리비용으로 재명명하지 않았다.
- 대표 A/F/R은 최신 성공 표본 1개와 동일 샤드 순차 source-command 구간에 대한 point estimate다. 전체 parent 명령 wall time, 병렬 wall time, 사용자 전체 대기시간 또는 순절감이 아니다.
- paired 비교를 가져온 경우에만 그 fixture의 총 검증 대기 차이를 별도로 표시한다. 증가 결과도 숨기지 않지만 live batch의 A와 결합하거나 일반 성능으로 확장하지 않는다.
- Dashboard 개편, 공통 출력 라벨과 상태 UI 검증까지만 구현했다. 성능 최적화, 새 benchmark, 보관 누적 모델, 별도 관리비용 분석기는 추가하지 않았다.

## 17. Phase 4 구현 기록

Phase 4는 기존 fixture와 `duration_baseline`/actual batch에서 파생한 시간을 사용해 **동일한 최종 입력 상태**의 두 비교를 분리했다. `same-shards`는 모든 shard command 호출 구간과 Click이 실제 실행한 source-command 구간 합을 비교한다. `parent-suite`는 기존 parent command 전체 호출 구간과 fixture driver가 본 Click preflight→runner 반환 요청 구간을 비교한다. 전자는 테스트 호출 구간 차이이고 후자는 해당 fixture의 순 요청시간 차이다. 둘을 합치거나 어느 하나를 live 사용자 대기시간으로 바꾸지 않는다.

### 변경 파일

- `benchmarks/incremental_verification.py`: Guarded workflow 보고서를 v4로 올렸다. A 성공 뒤 B를 별도 승인하는 기존 실제 Hook/one-use runner 흐름을 유지하고 `all-code` 단계를 추가했다. 각 단계에서 Click, 같은 샤드 전체, parent 전체의 순서를 회전시키며 raw repetition, warmup, 성공 자격, 범위 동등성, 두 측정 구간, 음수 delta, setup/transition/추가 full audit를 분리해 기록한다.
- `benchmarks/incremental_verification.py`: 커밋된 `.click/evidence-shards.json`의 실제 6개 shard command와 원래 parent command를 그대로 실행하는 `current-repository-test-bundle` 참조를 추가했다. 이는 인위적 sleep/load가 없는 그룹화 비용 참조이며 Click 부분 재사용 표본으로 계산하지 않는다.
- `benchmarks/incremental_verification.py`: v4와 repository reference의 top-level fields, source, unit, engine/environment, 조건, 표본 수, 측정 scope, finite duration, status/exit, warmup/eligibility/exclusion, delta 재계산을 엄격히 검증한다. 보고서나 fixture 승인 데이터는 제품의 승인·실행·재사용 입력으로 읽히지 않는다.
- `hooks/click_shadow_dashboard.py`: legacy v2 importer를 유지하고 v4 importer를 추가했다. 가져온 v4의 source/unit/scope/order/수학을 독립 검증하고 필요한 수치만 content-free 내부 모델로 복사한다. 단계별 두 비교를 표시하고 실제 저장소 번들 참조는 별도 그룹화 비용으로 표시한다. 독립 HTML도 같은 구간 구분과 음수 결과를 유지한다.
- `tests/test_incremental_benchmark.py`: 별도 A→B 승인, successor-contract 계보, partial/all-impact/environment/failure/retry/unchanged, 같은 상태의 두 full 대조, 순서 회전, 기본/명시 구성 구분, scope mismatch 제외, strict schema와 repository reference를 검증한다.
- `tests/test_click_efficiency.py`: Node DOM에서 v2 호환과 v4 strict import, `+codex` 버전, 저장소 참조, 단위·source·scope·delta 변조 거부, 안전한 standalone export를 검증한다.
- `dist/antigravity/hooks/click_shadow_dashboard.py`: 기존 배포 생성기로 source와 동기화했다.

### 실험 설계와 자격 규칙

- 반복마다 초기 파일·정책·Git 상태가 같은 새 임시 저장소를 세 구성(`baseline`, `click-default`, `explicit-reuse`)에 각각 만든다. OS cache는 비우지 않고 Python bytecode는 끈다.
- Click 구성은 A 계약을 별도 fixture turn에서 승인해 full success를 만든 뒤, B 계약을 다시 stage하고 다른 fixture turn에서 exact contract id로 승인한다. 승인 전 mutate, 같은 turn 승인, 틀린 contract id가 모두 거부되는지를 매 계약에서 확인한다. 이는 테스트 fixture의 scripted approval일 뿐 현재 개발 세션 승인이 아니다.
- 단계는 `first-run → unrelated-code → related-code → all-code → environment → failure → retry → unchanged`다. 고정된 두-shard/sibling-safe-change 정책은 A 전에 커밋하고 결과를 좋게 만들기 위해 넓히지 않는다.
- 각 Click 단계는 그 단계의 최종 파일·환경 상태에서 `click`, `same-shards`, `parent-suite`를 회전 순서로 모두 실행한다. 두 full 실행은 정확성 대조이자 추가 비용이며 receipt나 재사용 표본을 만들지 않는다.
- 정상 통과하고 warmup이 아니며 scope가 같은 paired 표본만 절감 분포에 들어간다. 예상 실패는 raw 표본과 전체 workflow cost에는 남지만 절감 통계에서는 제외한다. Click 기본은 shard map이 없으므로 그 구성의 `same-shards` raw 시간은 `scope-not-equivalent`로 제외하고 parent 비교만 사용한다.
- `click_non_test_interval_ms`는 같은 fixture 요청 wall 구간이 실제 source-command 구간을 포함할 때의 차이만 `derived-contained-intervals`로 기록한다. 독립 profiler나 일반 관리비용 모델로 부르지 않는다.

### 실행한 측정과 결과

명령:

```text
python3 benchmarks/incremental_verification.py --guarded-workflow --iterations 2 --warmups 1 --workload-rounds 40000 --repository-bundle --output /tmp/click-phase4-20260906-v4.json --html-output /tmp/click-phase4-20260906-v4.html
python3 benchmarks/incremental_verification.py --guarded-workflow --iterations 2 --warmups 1 --workload-rounds 40000 --output /tmp/click-phase4-current-v4.json --html-output /tmp/click-phase4-current-v4.html
```

두 번째 명령은 첫 실측에서 발견한 Click 기본 `same-shards` scope 불일치를 제외하도록 라벨·validator를 보정한 뒤 fixture만 다시 측정했다. 최종 `/tmp/click-phase4-final-v4.json`과 `/tmp/click-phase4-final-v4.html`은 보정된 fixture raw 시간과 첫 명령의 실제 저장소 reference raw 시간을 변경 없이 결합했으며 `workflow_report_is_valid == true`, `repository_reference_is_valid == true`다. 총 3개 repetition(1 warmup+2 measured), 96개 raw comparison sample을 가진다.

| 구성·구간 | 포함 단계 | 전체 기준 중앙값 | Click 중앙값 | `baseline - Click` 중앙값 | 범위/해석 |
| --- | ---: | ---: | ---: | ---: | --- |
| explicit-reuse · 같은 shard command 구간 | 성공 7단계 | 1,425.07 ms | 914.39 ms | **+510.68 ms (+35.83%)** | 두 repetition delta 476.01–545.35 ms. 이 fixture의 테스트 호출 구간 감소다. |
| explicit-reuse · parent vs Click 요청 구간 | 성공 7단계 | 810.68 ms | 11,219.32 ms | **−10,408.65 ms (−1,284.11%)** | 두 repetition delta −10,417.41–−10,399.88 ms. 이 fixture에서는 Click 요청이 더 길었다. |
| click-default · parent vs Click 요청 구간 | 성공 7단계 | 815.28 ms | 6,306.32 ms | **−5,491.04 ms (−673.51%)** | shard map 없는 기본 구성의 같은-shards 비교는 scope 불일치로 제외했다. |

명시적 재사용의 `unrelated-code` 단계는 두 측정 repetition에서 alpha 1개를 원본 성공 실행에서 successor-contract로 재판정하고 beta 1개만 실제 실행했다. 같은-shards 중앙값은 202.10→97.58 ms로 104.51 ms(51.72%) 감소했지만, parent-vs-Click 요청 중앙값은 114.91→2,018.26 ms로 1,903.35 ms 증가했다. `all-code`는 두 shard를 모두 실행했고 같은-shards delta 중앙값도 −7.78 ms로 음수였다. `unchanged`는 source-command 실행 합이 0 ms라 테스트 구간 감소율이 100%였지만 Click 요청은 parent보다 283.90 ms 길었다. 예상 실패의 두 비교는 raw 3개씩 보관하고 eligible sample은 0이다.

명시적 구성의 실패 포함 Click 요청 합 중앙값은 13,323.75 ms다. 별도 setup은 1,827.25 ms, 승인·변경 transition 합은 11,012.39 ms, 단계별 두 full 대조의 추가 비용 합은 2,434.39 ms다. 이 값들은 절감 통계에 더하거나 빼지 않는다.

실제 저장소 참조는 커밋된 6개 shard를 순차 실행해 모두 통과하는 데 474,999.26 ms, 기존 parent 명령이 통과하는 데 473,351.27 ms가 걸렸다. `parent - shards`는 **−1,647.99 ms(−0.348%)**로, 이 1회 표본에서는 shard 순차 실행이 더 느렸다. 이는 Click 부분 재사용 효과가 아니라 동일 inventory의 process/grouping 비용 참조다.

### 개발 중 검증

- `python3 -m unittest tests.test_click_efficiency -q`: 21 tests, `OK`.
- `python3 -m unittest tests.test_incremental_benchmark -q`: 실제 Guarded fixture lifecycle을 포함해 `OK`.
- `python3 scripts/build_antigravity_distribution.py`: source Dashboard hook을 `dist/antigravity`에 재생성했다.
- `git diff --check`: 오류 없이 통과했다.
- 최종 전체 `python3 -m unittest discover -s tests -q` 결과는 아래 최종 검증 후 이 절에 추가한다.

### Phase 4 한계

- fixture의 PBKDF2 workload는 결정적인 작은 회귀·비교 장치이지 프로젝트 성능 대표값이 아니다. 실제 저장소 참조는 인위적 workload가 없지만 1회·warmup 0이며 Click reuse counterfactual이 아니다.
- 실제 저장소 reference는 최종 scope-equivalence 라벨 보정 직전에 측정했다. 원시 repository 시간은 바꾸지 않았고 이 차이를 reference limitation에 기록했다. 최종 코드 전체 회귀검증은 별도로 수행한다.
- 단계 내 실행 순서와 workflow 구성 순서를 회전했지만 OS cache, scheduler, background load를 통제하지 않았다. 두 측정 repetition의 중앙값과 min/max는 신뢰구간이 아니다.
- fixture driver의 Click 요청 구간은 live Codex/Antigravity host의 queue, 최종 tool 반환, 사람의 판단 시간을 포함하지 않는다. matched live PostToolUse counterfactual이 없으므로 live 관리비용과 사용자 순 대기시간은 계속 미측정이다.
- 양수 test-command 감소는 성공 조건이 아니며, 실제로 관측한 parent 요청 증가와 shard grouping 증가를 숨기지 않았다. 성능 최적화, 별도 profiler, 보관 누적 모델, dashboard 구조 개편은 Phase 4에 포함하지 않았다.
