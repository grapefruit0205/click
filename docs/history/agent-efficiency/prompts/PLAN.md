# Agent efficiency 실행 계획

2026-09-07 · Phase 0 감사와 보정 뒤 Phase 1~5 구현을 순서대로 완료했다. 전체 task 비교는 이 호스트에서 유효한 경계와 usage를 얻지 못해 미측정이다.

## 시작 상태와 순서

원본은 `/home/grapefruit/click-agent-efficiency-prompts-v2`이다. 사용자가 Phase 1을 먼저 지시한 뒤 Phase 2~5의 연속 실행을 지시했다. 이 체크아웃에는 시작 시 agent-efficiency 진행 기록이 없었고, 완료된 `docs/dashboard-impact/`와 `docs/auto-sharding/`는 별도 작업이었다. 기존 대시보드와 검증 ledger는 B0로 재사용했다.

Phase 0 완료 후 첨부 텍스트 SHA-256 `5e0f6bfe36a58a71a86d0dbca33ec121ef20b1014ef60daa9d1945c4da99bf40`의
제품 기준을 반영했다. B0 snapshot과 최초 prompt hash/evidence는 변경하지 않았다. Phase 0 산출물과
01~05 prompt를 보정한 뒤 각 Phase의 선행 결과를 확인하며 순서대로 구현·검증했다. 자세한 보정 결정은
`reports/phase-0-amendment.md`, 구현 결과는 `reports/phase-1.md`부터 `reports/phase-5.md` 및 `reports/final.md`에 있다.

| Phase | 작업과 완료 산출물 | 선행 조건 | 상태 |
| --- | --- | --- | --- |
| 0 | 감사·B0 복구 가능한 스냅샷·공통 표시 계약·회귀 기준 | 현재 dirty 소스 확인 | completed |
| 1 | 명령별 outcome 정체성/기록, 연속 성공 수 의존 제거 | Phase 0 보고서와 B0 무결성 확인 | completed |
| 2 | 한 실행의 진단·상태·남은 검사·다음 행동 통합 | Phase 1 실제 결과/호환성 | completed |
| 3 | 명시적으로 선택한 독립 source의 제한적 실패 수집 | Phase 2, 기본 fail-fast 유지 | completed |
| 4 | N/B0/B1/B2 전체 task 평가 adapter와 비율 전용 공개 projection | Phase 3, 유효한 task 경계/usage/동등한 완료 조건 | completed · 실제 pair 0, 미측정 |
| 5 | 기존 viewer에 두 대표 카드와 전체 작업 효과 상태 | Phase 4 공개 결과 또는 명시적 미측정 | completed |

## 재사용할 기능과 수정 경계

| 현재 구현/근거 | 부족한 기능과 후속 수정 대상 | 분류 |
| --- | --- | --- |
| `hooks/click_verification.py:3902` runner는 순차 실행, 4159의 첫 nonzero에서 break | Phase 1은 순서를 유지한 채 명령별 실제 outcome 추가. Phase 3에서만 선택적 source 경계 continuation | CORE 결과 무결성 / USER_POLICY 계속 실행 예산 |
| 같은 파일 3997~4158의 source_results, source_completed_commands 및 incremental start/completion | source aggregate는 있으나 명령별 정확한 위치/exit/reason/시각 배열이 없음. 기존 batch에 확장하여 병렬 ledger 방지 | CORE |
| `record_result` 3024, 3289~3340; Observer 집계 4300; `click_gate.py:1290` 호환 wrapper | `succeeded_count`는 연속 성공 prefix이다. source 전체 passed/receipt/timing/dependency 성공 저장과 Shadow exit code를 명령별 사실에 연결해야 함 | CORE |
| `hooks/click_incremental.py:1795` progress_projection, 1382 host_summary; `click_gate.py:490` status | task/revision/실행·재사용·stale·remaining은 이미 있음. 실패 test id/원문 연결/완료 차단 사유를 같은 read-only 결과 모델에 결합 | HEURISTIC; 기존 완료 검사는 CORE |
| `click_dashboard_projection.py:211` next_action; 기존 완료/receipt 검사 | 다음 행동을 새 승인·자동 실행·자동 완료로 만들지 않는다. Browser/외부 근거·service·claim은 현재 검사를 재사용 | HEURISTIC 안내 / CORE 결속 |
| `click_inspection.py` stdout_file/stderr_file 인자, `click_process.run_argv`; 검증 기본은 스트림 상속 | Phase 2가 원래 1회 실행의 두 스트림을 동시에 drain하는 bounded capture를 추가. signal/cleanup/start boundary 및 Observer는 유지 | CORE 실행 / HEURISTIC 진단·보관 |
| inspect v1 최대 8 argv의 순차 배치, 입력 fingerprint 기반 owner-only read cache | 추가 코드 문맥은 opt-in, 읽기 claim 확보 후 제한. 검증 claim 활성 중 별도 read 실행 불가 | CORE claim / USER_POLICY 문맥 예산 |
| `click_verification.py:2040,4334` summary 출력, host_router capture는 마지막 payload 사용 | 재사용-only pre-tool 설명과 실제 runner 종료 설명이 별도 경로. Phase 2에서 원문+요약 중복과 adapter 재전송을 검증; 현재 중복 비용은 미측정 | HEURISTIC |
| `click_incremental.revalidation_savings`, `retained_impact`, projection v7 | 시간 집계를 새로 만들 필요 없음. Phase 5에서 카드 제목/부분 추정/미측정 매핑 변경 | HEURISTIC |
| viewer `outcomePresentation`/`summaryCopy`/`shareReport` v5/`standaloneReport`, ko/en/zh-CN | Phase 5가 시간 카드 옆에 비율 전용 토큰 카드와 전체 작업 효과를 추가. 같은 presentation을 모든 언어·복사·공유에 적용 | HEURISTIC |
| `benchmarks/incremental_verification.py` paired v2 / guarded workflow v4, `benchmarks/task_efficiency.py` 내부/공개 v1 | 기존 scripted 시간 비교를 유지하면서 B0/B1/B2/N task·usage 최소 adapter와 공개 allowlist를 추가 | HEURISTIC 평가 |
| `evals/semantic_grader.py` 및 golden-prompts의 token_usage 금지 항목 | offline 의미 판정이며 실제 모델 usage 수집기는 아님. Phase 4 adapter의 입력으로 자동 간주하지 않음 | HEURISTIC 평가 |
| host task/turn/response/tool/file-change/command 사건과 Click batch/timing | Phase 4 adapter가 명시적 task 경계·사용자 개입·활동 구간·usage를 연결하고 미분류를 보존. 현재 호스트의 실제 비교 경계는 unavailable | HEURISTIC 평가; authority로 사용 금지 |

위 line은 B0 기준이며 후속 변경 시 이동할 수 있다. 수정 전 실제 함수와 호출자를 다시 확인한다. runtime/helper 수정 시 `scripts/build_antigravity_distribution.py`의 기존 생성 경로를 쓰고 배포 복사본·호환 wrapper·명시적 module inventory를 함께 확인한다.

## Phase 1 연결 결과

`click_verification.py` runner는 v5 batch에 task/revision/batch/source와 명령 위치·digest를 결속하고 실제 start/completion을 기록한다. source aggregate, 성공 receipt, evidence exit code, Shadow exit code와 parent 완료는 검증된 command fold를 사용한다. 새 v5 record는 `succeeded_count` prefix로 되돌아가지 않으며 v1~v4 읽기 호환만 기존 prefix를 유지한다. 실패 후 다른 source 성공과 성공→실패→성공은 합성 단위 자료로만 검증했고 실제 runner의 기본 fail-fast는 유지했다. 자세한 schema·취소/누락 처리·비용은 Phase 1 보고서에 있다.

## Phase 2~5 연결 결과

- Phase 2는 원래 실행의 stdout/stderr를 한 번만 동시에 drain해 제한된 로컬 원문과 unittest/pytest 진단을 만든다. `reporting`을 생략하면 v1 `raw`가 유지되고 `actionable`은 명시적 선택이다. 진단과 `next_action`은 안내이며 pass/reuse/completion 권한이 아니다.
- Phase 3는 v1 `failure_collection`을 추가했다. 생략 시 `off`와 기존 fail-fast이고, `bounded`는 제출한 source 중 두 개 이상을 명시적으로 독립 선언해야 한다. source 경계의 상태·claim·취소·Git/environment/executable 재확인과 최대 3 source/3 failure/30초 시작창을 적용한다.
- Phase 4는 `benchmarks/task_efficiency.py`에서 내부 원자료 v1과 공개 v1을 분리한다. task 경계·완료 digest·usage 포함 관계가 유효한 pair만 비율을 계산한다. 현재 호스트에서는 독립 N/B0/B1/B2 작업과 완전한 usage 경계를 만들 수 없어 실제 pair는 0이고 공개 결과는 미측정이다.
- Phase 5는 기존 dashboard projection을 v8로 확장했다. 첫 화면에 정확한 제목 `절감 시간`, `토큰 절감률`과 별도 `전체 작업 효과` 상태를 표시한다. 공개 task presentation만 가져오며 원시 usage와 토큰 절대량은 화면·복사·공유 JSON/HTML에 전달하지 않는다. ko/en/zh-CN과 구형 projection 읽기를 유지한다.

## 필수 sharding/reuse 회귀 매트릭스

| 보호할 동작 | 기존 primary 검사 |
| --- | --- |
| exact argv, JSON-free init/status/refresh 문법 | `tests.test_click_sharding_setup.ShardingControlParsingTests` |
| proposal/apply/commit/bootstrap 구분, 멱등성, 사용자 policy 보호, discovery drift, 짧은 parent, 동시 초기화 | `tests.test_click_sharding_setup.ShardingSetupStateMachineTests` |
| 실제 Evidence control→baseline 흐름 | `ShardingGateIntegrationTests.test_evidence_mode_runs_setup_through_baseline_without_guarded_contract` |
| 실제 native authoritative 생성 shard 부분 재사용 | `ShardingGateIntegrationTests.test_library_layout_reuses_one_of_two_generated_shards`, `test_nested_layout_reuses_two_of_three_generated_shards` — B0는 프로필 준비 조건으로 skip; 다음 단계도 이 제한을 표시 |
| collector/proposal의 완전성·보수적 fallback·공개 module 경계 | `test_click_auto_sharding`, `test_click_shard_proposal`, `test_click_sharding_boundaries`, `test_repository_shard_inventory` |
| committed exact map, overlap/새 test/변조/mid-run drift | `tests.test_click_evidence_shards` |
| 실패 sibling만 재시도, 부모 재제출, shard map 단독 권한 불가, caller observation 권한 불가 | `ClickGateVerificationTests`의 phase-0-checks.json에 고정한 tests |
| precommitted safe-change 재사용, 코드/정책 수정 시 rerun, successor timing/receipt | 같은 gate 회귀 + `tests.test_click_change_policy` |

기존 committed broad parent는 `python3 -m unittest discover -s tests -q`이고 8개 내부 shard로 분해된다. broad 검사 시 원래 parent argv와 외부 evidence ID를 제출한다. 내부 shard ID를 직접 호출하지 않는다. 이번 baseline은 별도의 명시적 targeted 모듈 요청이며 Click 최소 class는 broad로 정규화되었다.

현재 `.click/evidence-reuse.json`의 안전 변경 목록은 README.md/README.ko.md/README.zh-CN.md이다. 새 `docs/agent-efficiency/**`를 자동으로 안전 경로에 추가하지 않는다. `.click/evidence-dependencies.json`은 현재 체크아웃에 없다. Phase 0은 이 정책들을 수정하지 않았다.

모든 단계는 새 실패·새 skip·완료 상태 왜곡을 허용하지 않는다. native 테스트의 환경상 미실행은 통과나 실측 효과가 아니다. 각 변경에 필요한 receipt/claim/취소/drift, distribution, 대시보드·다국어 검사도 해당 Phase에서 수행한다. Phase 1은 원래 broad parent를 8개 committed shard로 실행해 822개 중 815개 통과·기존 7개 skip·실패 0을 확인했다.

## 보정된 제품 판정 순서

1. 동일한 고정 acceptance를 실제로 통과했는지 확인한다.
2. 요청 수신부터 완료 결과 반환까지의 wall-clock 전체 완료시간을 직접 비교한다.
3. 추가 승인·질문·상태 확인·설정 복구와 확인 가능한 사용자 수행시간을 비교한다.
4. 토큰 절감률, 수정·재검증 주기, 후속 호출, 테스트 절감 시간으로 원인을 분석한다.
5. 느려진 작업·사용자 개입 증가·실패·취소·미완료와 계측 비용을 함께 보존한다.

전체시간은 활동 구간 합으로 만들지 않는다. 구현·검증·Click 관리가 겹치면 각각 원인 분석에는
남기되 task elapsed에 중복 합산하지 않는다. 첫 사용/반복 사용과 Evidence/Guarded를 분리한다.

## 후속 평가용 동일 조건 시나리오

모든 군은 동일 B0 파일/dirty 상태에서 시작하고 동일 과제 텍스트·모델/추론 설정·권한·도구·sharding 정책·acceptance를 사용한다. 각 실행의 workspace/session/Click state를 분리한다. 모델에는 오류 위치/정답을 미리 주지 않는다. 아래는 아직 실행하지 않은 실제 호스트 pilot 설계다.

| ID | 초기 상태와 과제 | 독립 완료 조건 / 주의 |
| --- | --- | --- |
| S0 | 작은 단일 검사 정상 성공, 변경 불필요한 검증 과제 | 고정 acceptance 통과; 불필요한 수정/진단 비용도 포함 |
| S1 | 같은 unittest/pytest 명령 안의 서로 다른 실패 2개를 수정 | 모두 복구; 프레임워크의 기존 다중 실패 출력은 B0에도 유지 |
| S2 | 독립 source 2~3개의 코드 결함 수정 | 각각과 고정 전체 acceptance 통과; 동일 source 내부는 순서 의존 |
| S3 | 공통 import/setup 오류로 여러 검사가 영향받음 | 공통 원인 복구; 독립성을 추측하여 다음 source를 실행하지 않음 |
| S4 | 한 실패 이후 긴 검사와 작은 검사가 배치에 남음 | 시작 허용창과 실제 검사 종료 제한을 구분; 추가 검사 비용/완료 모두 기록 |
| S5 | runner 도중 취소와 그 이후 정상 재시도 | 취소 시 추가 시작 없음, 관측된 partial/unknown 보존, 새 유효 검증 필요 |
| S6 | 환경/실행파일/보호 파일 drift | reuse 불가 또는 보수적 중단, 원래 검사·정상 재시도 경로 보존 |
| S7 | 첫 과제 완료 후 작은 요구 변경 | 불필요한 구조 추가 여부와 이해·수정·검증 부담; 파일/줄 수만으로 판정하지 않음 |

S0~S7의 코드 seed·prompt·완료 검사는 실제 pilot 실행 전에 파일과 digest로 고정한다. 첫 사용과 warm baseline 반복 사용, Evidence와 Guarded를 분리하고 B0/B1/B2 순서를 교대한다. N은 같은 호스트 조건이 가능할 때만 별도 군으로 추가한다. 현재 pair 수는 0이며 목표 절감률은 acceptance가 아니다.
