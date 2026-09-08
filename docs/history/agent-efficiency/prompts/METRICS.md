# 측정 정의와 공개 표시 계약

Phase 0에서 정의하고 Phase 4~5 구현에 적용한 계약이다. 실제 N/B0/B1/B2 모델 작업 pair는 아직 0개다. 근거가 없는 값은 null이며 시간·로그 길이·검사 수로 토큰 또는 모델 왕복을 환산하지 않는다.

## 최상위 제품 결과

동일한 초기 코드·과제·모델/추론 설정·권한·도구·완료 조건에서 실제로 완료된 task만 비교한다.

`task_completion_time_delta_ms = baseline_task_elapsed_ms - improved_task_elapsed_ms`

`task_completion_time_savings_ratio = 1 - improved_task_elapsed_ms / baseline_task_elapsed_ms`

task 시작은 고정 작업 지시가 host에 제출된 시점, 종료는 동일 acceptance를 충족한 결과가 host에
반환된 시점이다. 분석·탐색·구현·재작업·검증, Click 준비/판정/저장, 설정·복구와 필요한 재시도를
포함한다. baseline>0이고 양쪽 경계·완료 조건이 유효할 때만 계산한다. 양수는 빨라짐, 정확한 0은
변화 없음, 음수는 느려짐이며 근거가 없으면 null/미측정이다.

전체 task elapsed는 wall-clock 경계의 직접 차이다. 아래 활동 구간을 더해서 만들지 않는다.
final acceptance audit는 모든 비교군에 동일하게 적용하며 task 비용과 별도 기록한다. 첫 설치·샤딩
준비를 포함한 첫 사용과 준비된 반복 사용, Evidence와 Guarded, 현재 Click 대비와 Click 미사용
대비를 각각 분리한다.

사용자 개입은 추가 승인, 질문 응답, 상태 확인, 설정·복구 조치의 횟수와 확인 가능한 실제 수행시간이다.
응답을 기다린 전체 시간을 사용자 작업시간으로 간주하지 않는다. 구현 활동·검증 실행·Click runtime 관리·
사용자 개입·기타 활동은 관찰 가능한 사건 구간으로만 분류하고, 겹치면 중복 합산하지 않는다.
숨은 모델 사고시간과 분류할 수 없는 구간은 추측하지 않고 기타·미분류/null로 남긴다.

완료 품질은 사전 고정 acceptance 통과와 별도 검토로 판단한다. 파일 수·코드 줄 수·승인 거부 수는
과설계 판정값이 아니다. 요청에 실제로 필요한 구조인지와 첫 과제 뒤 소규모 변경의 이해·수정·검증
부담을 같은 조건에서 본다. 테스트 절감보다 전체시간·사용자 부담이 증가한 표본은 불리한 결과다.

## 내부 계측

| 항목 | 관찰과 집계 정의 | unknown / 이중 집계 방지 |
| --- | --- | --- |
| 도구 호출 수 | 고정 task scope의 host tool call ID별 1회. 실제 실행·준비 거부·캐시 hit·polling은 속성으로 분리 | Hook 관측 범위 밖은 미확인. 같은 pre/post는 한 호출; tool wrapper와 nested 실행을 혼합 합산하지 않음 |
| 확인 가능한 모델 응답 수 | 실제 usage record의 response_id를 task/thread/turn에 결속하여 중복 제거 | message/reasoning item 수, user turn 수, token_count snapshot 수는 모델 응답 수가 아님. 경계 누락 시 null |
| 인과관계가 확인된 왕복 수 | 모델 response→tool call/result→그 결과에 의존한 후속 response의 인과 ID가 확인된 연결 | 시간순 배열만으로 의존성을 만들지 않음. 현재 schema-only 감사는 인과 연결을 검증하지 않았으므로 null |
| 실패 후 첫 실제 수정 전 호출 | 최초 관측 failure 결과부터 첫 내용 digest가 실제 달라진 코드 mutation까지의 후속 tool call | mutation revision만 증가한 no-op, 진단 문서 쓰기는 코드 수정이 아님. 수정 없음/경계 누락 시 null와 사유 |
| 수정·재검증 주기 | 실패→실제 코드 수정→현재 조건의 원래 검증 요청 완료 단위 | 같은 결과 polling/재전송은 새 주기가 아님. 중단/미완료 주기도 별도 보존 |
| 사용자 개입 | 추가 승인·질문 응답·상태 확인·설정/복구 조치의 event ID별 횟수와 확인 가능한 active 구간 | 자동 fixture 승인과 단순 대기를 사람 작업시간으로 세지 않음. active 경계가 없으면 시간은 null |
| 활동 구간 | 관찰된 host/Click 사건을 implementation/verification/click-management/user-intervention/other로 분류 | 한 사건이 섞였거나 중첩되면 억지 분할·합산하지 않음. hidden reasoning은 분류하지 않음 |
| 입력·출력·캐시·reasoning usage | 버전 고정 공식 export/호스트 원기록의 실제 사용량. input/output/total 및 cache read/write·reasoning 세부를 내부에 보존 | 포함 관계/누적·증분 의미를 adapter에서 검증. cache/reasoning이 상위에 포함되면 다시 더하지 않음. schema의 default=0을 실제 관측 0으로 해석하지 않음 |
| 실행시간 | task elapsed, 기존 command interval, partial processing/request, 비교 driver의 request wall을 서로 구분 | 각 monotonic 원점 안에서만 차감. setup·진단·출력·추가 읽기·snapshot 비용·실패·재시도 포함. 활동 구간 합을 task elapsed로 사용하지 않음 |
| 완료 여부 | 사전에 고정한 acceptance 결과와 동등한 완료 조건 | Click 등록 evidence current와 제품 전체 정확성은 별개. 실패/취소/미완료를 성공 pair에 포함하지 않되 비용은 내부 보존 |

`hooks/click_host_coverage.py`의 `known-surfaces-only`/`host-may-omit-events`가 Click Hook 관측의 한계다. Hook은 pre/post/prompt/session을 관찰하며 모델 usage를 제공하지 않는다. 호스트 어댑터는 별도 관찰 계층이고 authority source가 될 수 없다.

## 이 호스트에서 확인한 범위

- 설치 앱: ChatGPT/Codex Desktop 패키지 26.901.51231. 현재 session metadata와 CLI는 0.153.4, 모델 gpt-6-astra, effort xhigh. 이 값은 다른 평가 실행의 설정으로 자동 상속되지 않는다.
- 설치 CLI의 `app-server generate-json-schema`에서 `threadId`, `turnId`, `tokenUsage.last/total` 및 `inputTokens`, `outputTokens`, `totalTokens`, `cachedInputTokens`, `cacheWriteInputTokens`, `reasoningOutputTokens`를 확인했다. 스키마 생성은 모델 호출이 아니다.
- 현재 작업의 로컬 rollout만 schema 수준으로 읽었다. `token_usage_record`에는 response_id/turn_id/root_turn_id/thread_id/session_id와 usage/turn_token_usage/thread_token_usage가 존재한다. `event_msg/token_count`에는 last/total token usage가 있다. 원시 숫자·프롬프트·도구 출력·계정 정보는 이 문서/증거 파일에 복사하지 않았다.
- 실제 response ID는 있지만 현재 작업의 tool call 인과 연결, 고정 task 시작·종료, usage 범위의 reset·resume·compaction 의미를 동등한 비교군 전체에 결속할 수 없었다. Phase 4 adapter는 명시적으로 제공된 증분/누적 의미와 중복 identity를 검증하지만, 이 불완전한 현재 thread를 완성된 비교로 승격하지 않는다.
- 현재 thread는 이전 대시보드 작업도 포함한다. thread 누적량을 새 과제 비용이나 paired baseline으로 사용하지 않는다. 이 감사의 이벤트 개수는 schema 가용성 확인이며 효율 비교 자료가 아니다.
- 공식 문서는 CLI의 JSONL `turn.completed` usage와 app-server의 item/usage 알림을 설명한다. 이는 하나의 turn을 개별 모델 호출로 볼 근거가 아니다. [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode), [App Server](https://learn.chatgpt.com/docs/app-server) (2026-09-07 조회).

## 전체 작업 효과 공개 상태

`task_completion_time_savings_ratio`의 유효한 직접 비교가 있을 때만 `전체 작업 효과`를 표시한다.
ratio>0은 빨라짐, ratio=0은 변화 없음, ratio<0은 느려짐이다. task 경계·동일 완료 조건·baseline이
없으면 미측정과 사유를 표시한다. `절감 시간`, request_wall_ms, 관리비용을 조합해 추정하지 않는다.
느려짐·실패·미완료·사용자 개입 증가는 첫 화면 결론에서 숨기지 않는다.

## 절감 시간

한국어 대표 제목은 정확히 **절감 시간**. 기존 `revalidation_savings.omitted_test_execution_ms`를 사용하며 실제 적용된 재사용의 적합한 과거 성공 command 실행 구간 합계다. 시간은 **이번 검증 · 테스트 실행 기준**이다. Observer/실행 호출 경계 내부의 비용이 포함될 수 있으므로 CPU 시간이나 전체 요청 순절감이 아니다.

| 기존 근거 | 표시 |
| --- | --- |
| omitted_test_execution_status=estimated, complete scope | 숫자 옆 추정 |
| partial, 일부 reusable sample 누락 | 유효한 부분 합계와 부분 추정; 최소 절감 아님 |
| unmeasured 또는 시간 표본 없음 | 미측정; 0 또는 토큰 추정으로 대체하지 않음 |
| 정상 완료이며 실제 적용 reuse=0 | 실제 0; 아직 요청 없음/실패로 미실행과 구분 |
| 계획·실행 중·실패·취소·거부 | 해당 상태를 우선, 실행 안 한 검사를 절감으로 더하지 않음 |

`executed_test_execution_ms`=E, `full_sequential_test_execution_estimate_ms`=F, `test_execution_reduction_ratio`=기존 시간 비율이며 토큰 비율이 아니다. coverage, omitted missing count, source timing baseline provenance와 scope_complete를 재사용한다. `request_wall_ms`/`measured_processing_ms`는 제한된 계측 구간이고 전체 사용자 대기나 management 순비용이 아니다. 기존 비교 benchmark의 실측 wall delta도 별도 상세다.

## 토큰 절감률

한국어 대표 제목은 정확히 **토큰 절감률**. 동일 과제·완료 조건·호스트 usage 정의를 갖춘 실제 비교의 **전체 작업 기준**이다.

`token_savings_ratio = 1 - improved_total_tokens / baseline_total_tokens`

내부 비율을 UI에서 100배 하여 %로 표시한다. baseline>0, 전체 scope의 유효 usage, 중복 제거, 동등한 완료 근거가 모두 필요하다. 현재 사용량만 있거나 누락/분모 0/비호환/실패/미완료이면 ratio=null와 사유를 표시한다. 음수는 내부 부호를 보존한다.

| 상태 | 사용자 표시 |
| --- | --- |
| ratio>0 | …% 감소 · 실측 비교 |
| ratio=0 정확한 0 | 0% · 변화 없음 · 실측 비교 |
| ratio<0 | …% 증가 · 실측 비교 |
| 작은 비영 값 | 유효 자릿수 또는 '<0.01% 감소/증가' 같은 방향 보존; 변화 없음으로 반올림 금지 |
| ratio=null | 미측정과 짧은 사유, 토큰 절대량 대체 표시 없음 |

B0→B1, B1→B2, B0→B2는 개선 전 Click 대비 추가 효과다. N→B2는 Click 미사용 대비 총효과이고 별도 군이다. 두 비율을 더하지 않는다. 쌍별 비율 중앙값과 전체 usage 합계로 구한 비율을 구분한다. 무관한 시나리오의 분모를 섞지 않는다. 실패/취소/비호환 pair 수를 표시하고 불리한 결과를 삭제하지 않는다. 설계 가설 10~20%/20~35%는 실측/기본값/통과 목표가 아니다.

## 내부 원자료와 공개 allowlist

기존 paired v2/workflow v4 내부 시간 schema를 보존한다. `benchmarks/task_efficiency.py`는
`click-task-efficiency-evaluation` v1 내부 입력을 읽고 `click-task-efficiency-public` v1을 만든다.
내부 입력은 실제 task 경계, completion/acceptance digest, 사용자 개입, 활동 구간과 공식 usage event를
보존한다. adapter는 event/response identity, 증분·누적 의미와 cache/reasoning 포함 관계를 확인해
중복 합산을 막는다. 공개 projection은 아래 allowlist만 복사한다.

공개 허용: task_completion_time_delta_ms/ratio와 전체 작업 상태, 공개 허용된 사용자 개입 횟수와
확인 가능한 수행시간, token_savings_ratio, status/reason code, 비교 기준(B0/B1/B2/N), 제한된
시나리오/실행 종류/runtime mode/시점, pair/성공/실패/취소/누락/느려진 작업 개수, 동일 완료 조건,
집계 방식, 도구 호출·실패 후 수정 전 조회·수정/재검증 주기·확인 가능한 모델 왕복과 활동 구간 개수.
활동 구간은 비가산이고 hidden reasoning은 unknown으로 표시한다. 임의 문자열·추가 필드·원시 series는
자동 복사하지 않는다.

공개 금지: baseline_total_tokens/improved_total_tokens/saved_tokens/usage 및 절대 토큰량을 복원할 수 있는 전후 수·raw series·임의 텍스트·숨겨진 DOM/data 속성/HTML 내장 JSON. 로컬 원자료 경로/로그/코드/명령 인자/환경/인증정보/원시 계약도 공유하지 않는다. 화면 상세·툴팁·접근성 텍스트·복사·공개 JSON·독립 HTML에 동일 적용한다. 숨김 CSS는 경계가 아니다.

현재 viewer는 토큰 절대량을 수집하거나 렌더링하지 않는다. Phase 4 공개 projection은 원시 usage key와
알 수 없는 필드를 제거하는 allowlist이고, Phase 5 importer는 정확한 top-level/presentation 필드와
상태·비율 일관성을 다시 검사한다. 같은 공개 presentation이 화면, 복사, JSON, HTML에 사용된다.
내부 평가 artifact에는 계산 재현에 필요한 절대 usage가 남지만 공개 경로에는 연결되지 않는다.

현재 `evidence/phase-4-internal.json`은 호스트 task/usage 경계를 확보하지 못한 사실과 pair 0개를
기록한다. `evidence/phase-4-public.json`은 presentations가 빈 미측정 객체다. 이것은 0%가 아니다.

시간 카드의 이번 검증과 토큰 카드의 선택한 비교가 다르면 각각 범위를 표시한다. ko/en/zh-CN 언어와 같은 수치/상태/creation locale을 보존한다. 토큰 비교가 없어도 시간 카드는 독립 동작한다. viewer 새로고침·공유·언어 전환으로 테스트나 모델 호출을 추가하지 않는다.
