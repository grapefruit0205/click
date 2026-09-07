# Phase 1 — 명령별 실제 결과 기록 완료

01-command-outcomes.md만 수행했다. 기존 순차 fail-fast 실행은 유지하면서 source마다 필수 명령의 실제 결과를 저장하고, 성공 receipt·source 상태·Shadow exit code·parent 완료 판정이 같은 명령별 사실을 사용하도록 연결했다. 다음 단계는 02-actionable-report.md이며 자동 진행하지 않는다.

## 구현 결과

- 기존 incremental batch/source ledger를 v5로 확장했다. 별도 tracing ledger는 만들지 않았다.
- batch의 `task.mode/id/name`, mutation revision, batch id, source key와 각 명령의 전체 `position`·source 내부 `source_position`·정확한 `check_digest`를 함께 결속한다.
- 각 명령은 `planned`, `started`, `passed`, `failed`, `interrupted`, `not-run`, `unknown` 중 하나와 시작·종료 offset, duration, 원래 exit code, reason code, 측정 범위, 선택적 digest형 `log_ref`를 보존한다. argv·경로·로그 원문은 이 배열에 복사하지 않는다.
- 실제 target 시작 callback부터 명령 반환까지를 `target-start-through-command-return`으로 측정한다. 시작을 관측하지 못한 명령은 시간과 exit code를 만들지 않고 `unknown`으로 남긴다.
- source aggregate는 명령 배열에서 접는다. 필수 명령이 모두 실제 `passed`인 경우만 source가 `passed`이고, 어느 위치의 실패·중단·누락도 뒤의 성공으로 지워지지 않는다.
- 새 v5 batch에서 명령 결과가 없거나 손상되면 `succeeded_count`로 되돌아가지 않는다. `succeeded_count`는 v1~v4 기록과 기존 runner 호출면의 호환 필드로만 남겼다.
- v1~v4 batch/receipt 읽기와 구형 contiguous fail-fast 해석은 유지했다. 구형 기록에 없는 성공을 새로 만들지 않는다.
- 취소, 명령 시작 전 KeyboardInterrupt, 결과 누락, 중복 위치/digest 불일치, workspace 변경은 fail-closed로 처리한다. 시작 전 중단은 command `unknown`, batch `interrupted`이며 실행시간은 미측정이다.
- Observer 기본값 `off`, 실행환경·실행파일·host coverage 결속, dependency/safe-change provenance, one-use claim/replay 방지, shard parent/child 권한은 바꾸지 않았다.

## 판정 연결

| 소비 경로 | Phase 1 이후의 기준 |
| --- | --- |
| source 결과 | 해당 source의 명령 상태를 검증하고 fold한 결과 |
| 성공 receipt | 모든 필수 명령의 검증된 `passed`; 누락·손상 시 생성 안 함 |
| evidence `last_exit_code` | 명령 fold의 원래 실패/중단 exit code |
| Shadow source exit | 동일한 명령 fold에서 확인된 pass 또는 정확한 exit만 사용 |
| parent 완료 | 모든 요청 source가 `passed` 또는 실제 `reused`인 경우만 성공 |
| legacy v1~v4 | 기존 prefix 호환 경로; 새 v5의 authority로 승격하지 않음 |

실제 runner 회귀는 같은 source의 세 명령 중 두 번째가 exit 7이면 두 개만 실행하고 `passed / failed / not-run`을 저장했다. 실패 뒤 다른 source 성공과 `success / failure / success`는 비연속 합성 자료에서만 검사했으며 실제 runner가 그 순서로 계속 실행했다고 보고하지 않는다.

## 변경 파일

- `hooks/click_incremental.py`와 `dist/antigravity/hooks/click_incremental.py`: v5 command schema, validator, 전이, source/batch fold, legacy 읽기 호환.
- `hooks/click_verification.py`와 배포 복사본: command plan 결속, 실제 start/completion 기록, receipt/evidence/Shadow/parent 판정 연결.
- `hooks/click_gate.py`와 배포 복사본: 기존 호출 인자를 유지한 optional exact-outcome/timing 전달 wrapper.
- `tests/test_click_incremental.py`, `tests/test_click_gate_verification.py`: 명령 배열, fail-fast, 비연속 합성, 누락·중복·취소·시작 전 중단·legacy 회귀.
- `docs/agent-efficiency/evidence/phase-1-overhead.json`, `phase-1-test-results.json`, 이 보고서와 `progress.json`·`PLAN.md`: 측정과 진행 기록.

Phase 0 B0, `.click` sharding/reuse policy, `docs/dashboard-impact/`, `docs/auto-sharding/`, 기존 한국어·영어·중국어 대시보드는 수정하지 않았다. source와 `dist/antigravity`의 세 runtime 파일은 byte-for-byte 동일하게 유지했다.

## 검증

최종 제품 코드에서 원래 parent argv `python3 -m unittest discover -s tests -q`를 `E-P1-final-regression`으로 제출했다. committed shard map이 8개 child로 확장했고 총 822개 중 815개가 통과, 기존 환경 조건 7개가 skip, 실패는 0이었다. unittest가 보고한 shard 시간 합은 676.473초이며 Click verification 경로는 약 11분 19초였다.

| 범위 | 결과 |
| --- | --- |
| auto-sharding analysis | 37개, 35 통과, 2 skip |
| sharding setup | 11개 통과 |
| capability | 51개 통과 |
| core state | 58개 통과 |
| gate | 194개 통과 |
| Observer/dashboard/host | 170개, 167 통과, 3 skip |
| distribution/repository | 84개, 82 통과, 2 skip |
| verification/receipt/incremental | 217개 통과 |

공통 필수 매트릭스의 `E-P1-sharding-regression`은 74개 중 72개 통과·기존 native authoritative 2개 skip·실패 0, `E-P1-shard-reuse-regression`은 8개 모두 통과했다. 이는 public init/status/refresh, proposal/application/commit/bootstrap 구분, 정확한 parent argv와 map fallback, same-revision sibling 보존, 실패 child 재시도, safe-change 및 수정 후 current-condition 재판정을 포함한다. 숫자는 겹치는 전체 suite와 합산하지 않는다.

이 보고서·evidence·progress 작성은 제품 코드 검증 뒤의 문서 전용 mutation이다. 작성 후 같은 필수 매트릭스를 `E-P1-final-sharding-current`와 `E-P1-final-shard-reuse-current`로 다시 실행하고, JSON/schema·source/dist 동일성·`git diff --check`를 확인한다. 최종 current-revision 실행의 권한 원본은 Click Evidence ledger이며 이 문서 JSON은 receipt나 재사용 권한이 아니다.

개발 중 실패도 숨기지 않았다. 첫 전체 실행은 v5 fixture에 exact facts가 없어 verification shard 한 건이 실패했고, compatibility wrapper의 새 optional 인자 누락 한 건도 실패했다. 시작 전 KeyboardInterrupt 회귀는 batch가 `running`에 남는 문제를 드러냈다. 각각 fixture/wrapper를 보정하고 admission 거부와 명시적 `unknown`을 분리했으며, 이후 관련 217개 shard와 최종 822개 suite가 통과했다. 세 실행은 `phase-1-test-results.json`과 Click ledger에 남아 있다.

## 추가 기록 비용

CPython 3.12.3 로컬 합성 fixture에서 한 source의 v4 aggregate와 v5 exact history를 비교했다. 처리시간은 독립 lifecycle의 생성·저장·명령 전이·최종 fold·검증·canonical JSON 직렬화를 포함한 중앙값이다.

| 명령 수 | 완료 history 추가 저장량 | 명령당 추가량 | 로컬 처리 중앙값 증가 | public progress 출력 증가 |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 365 B | 365.000 B | 223.726 µs | 0 B, 동일 내용 |
| 8 | 2,837 B | 354.625 B | 1,836.300 µs | 0 B, 동일 내용 |
| 64 | 22,801 B | 356.266 B | 28,921.299 µs | 0 B, 동일 내용 |

원자료와 planned/completed 절대 크기는 `evidence/phase-1-overhead.json`에 있다. 이 microbenchmark는 로컬 component 비용이며 request wall, 전체 task elapsed, 사용자 시간, 모델 호출 또는 토큰 사용량이 아니다. command 배열은 내부 bounded history에만 있고 현재 `progress_projection`에는 노출하지 않아 공개 출력 증가는 0이었다.

## 한계와 다음 단계

- native authoritative automatic-sharding E2E 두 사례는 B0와 같은 `prepared native authoritative profile required` 조건으로 skip됐다. 나머지 sharding state machine과 reuse 권한 회귀는 실제 실행해 통과했지만 이 환경에서 native partial reuse를 새로 실증했다고 주장하지 않는다.
- Phase 1은 결과 무결성 기반만 만든다. 유효한 N/B0/B1/B2 전체 task 비교가 없으므로 전체 완료시간 절감, 사용자 개입 변화, 완료 품질 비교, 토큰 절감률은 계속 null/미측정이다.
- 새 명령별 기록의 추가 비용은 측정했지만 실제 workload 분포나 전체 요청 순효과로 외삽하지 않는다.
- Phase 2는 이 exact outcome을 기존 상태/진단/남은 검사/다음 행동 presentation에 연결해야 한다. 실행·reuse·완료 권한은 계속 CORE 경로에 남긴다.

Phase 1 상태는 completed이고 다음 prompt는 `/home/grapefruit/click-agent-efficiency-prompts-v2/02-actionable-report.md`이다. commit, push, install, Observer 활성화는 수행하지 않았다.
