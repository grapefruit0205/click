# Phase 2 — 진단·상태·다음 행동 통합 완료

기존 명령별 outcome과 status projection을 유지하면서, 원래 검증 실행 한 번의 출력에서 로컬 진단을 만드는 선택형 reporting v1을 추가했다. 기본 출력은 기존과 같은 `raw`이고 `actionable`은 요청에서 명시해야 한다. 진단은 안내 정보이며 pass, receipt, reuse 또는 작업 완료 권한에 쓰이지 않는다.

## 구현 결과

- `hooks/click_process.py`는 stdout/stderr pipe를 별도 thread로 동시에 끝까지 drain한다. 각 stream은 기본 64 KiB, 허용 4~128 KiB 안에서 앞/뒤 구간을 남기며 전체 관측 bytes, 잘림과 reader 오류를 기록한다. target 명령은 한 번만 실행되고 기존 process-group 종료와 실제 시작 callback을 유지한다.
- `hooks/click_diagnostics.py`는 unittest와 pytest의 test id, failure/error 분류, 안전한 workspace 상대 파일/행, 제한된 stack과 핵심 메시지를 파싱한다. ANSI/control 문자와 일반적인 secret assignment/Bearer 값을 actionable 요약에서 제거한다. 지원하지 않는 형식은 원문 참조와 parser 사유를 남긴다.
- 원문 capture는 기존 plugin data 아래 owner-only 디렉터리와 0600 파일에 저장한다. 파일당 512 KiB, 24시간, 128개, 총 8 MiB이며 state에는 최근 32 record와 최대 512 KiB만 유지한다. 보존에 실패하거나 잘린 경우 완전한 원문이라고 표시하지 않는다.
- `reporting.context`의 초기 상한은 파일 2개와 총 120줄이다. 이 옵션도 자동 읽기를 수행하지 않고, 출력에 실제 나온 안전한 파일/행을 기존 inspect claim으로 읽을 후보만 돌려준다. root 밖 경로, symlink escape와 민감 경로는 제외한다.
- 기존 `progress_projection`, `host_summary`, `next_action`에 같은 batch/revision의 진단을 연결했다. 실행·재사용·실패·미실행 수, 남은 검사, 진단 parser/capture 상태, 로컬 log ref, 차단 사유를 함께 보여주며 `registered_checks_current`와 `whole_task_correctness_established`를 구분한다.

## 검증과 관찰 비용

`tests.test_click_diagnostics`의 12개 검사가 통과했다. 정상 성공, unittest/pytest 실패, unsupported parser, 잘못된 UTF-8, 긴 stream과 동시 drain, 로그 누락·잘림, 안전하지 않은 경로, secret 형태, revision 불일치, owner-only 보관, raw/actionable 호환과 한 번 실행을 확인했다. Phase 2~5 집중 묶음에서는 관련 검사를 포함한 81개가 통과했다.

각 record는 `generation.processing_ms`, `input_bytes`, `output_bytes`, `storage_bytes`를 기록한다. 이 값은 진단 component의 관찰 비용이며 전체 task 완료시간이나 토큰 절감이 아니다. 실제 사용자 작업에서 실패 후 첫 수정까지의 도구 호출과 사용자 수행시간을 비교할 동등한 B0/B1 task 경계는 이 호스트에서 얻지 못했으므로 효과는 미측정이다.

## 보존한 동작과 한계

raw 기본값, 원래 argv, exit code, signal/cleanup, Observer 입력 경계와 default off를 유지했다. 진단 때문에 검사를 재실행하거나 소스 파일을 자동으로 읽지 않는다. parser가 분류하지 못한 런타임 오류는 추가 실행 권한으로 승격하지 않는다.

automatic sharding init/status/refresh와 shard reuse는 이 모듈의 authority 소비자가 아니다. 최종 공통 회귀 결과는 `reports/final.md`에 기록한다. 실제 모델 작업의 관리 호출 감소와 전체 완료시간 순효과는 Phase 4 공개 상태와 같이 미측정이다.
