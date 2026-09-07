# Phase 0 보정 — 전체 작업 순효율

2026-09-07에 사용자 요청으로 Phase 0 기준선을 유지한 채 성공 기준과 향후 01~05 prompt를 보정했다.
첨부 문서의 설명 전체를 실행 지시로 취급하지 않았다. 사용자 요청과 일치하는 제품 결론·측정 계약만
채택했고 예시 수치, 반복된 설명, 외부 주장과 인용은 사실값으로 복사하지 않았다.

## 채택한 기준

- 동일한 초기 상태·과제·모델 설정·권한·도구·acceptance에서 완료된 결과를 비교한다.
- 최우선 결과는 요청 수신부터 동일 완료 조건을 충족한 결과 반환까지의 전체 완료시간,
  사용자 개입 부담, 완료 품질이다.
- 분석·탐색·구현·재작업·검증, Click 준비/판정/저장, 설정·복구와 필요한 재시도를 task 경계에 포함한다.
  활동 구간이 겹치면 중복 합산하지 않고, 숨은 사고시간과 불명확한 구간은 미분류/null로 둔다.
- 추가 승인·질문·상태 확인·설정 복구의 횟수와 확인 가능한 실제 수행시간을 기록한다.
  단순 응답 대기를 사람의 작업시간으로 간주하지 않는다.
- 첫 사용/준비된 반복 사용, Evidence/Guarded, N/B0/B1/B2를 분리한다.
- 테스트 실행시간이 줄어도 전체가 느려졌거나 사용자 부담이 늘어난 결과와 실패·미완료를 보존한다.
- 과설계는 파일/줄 수로 판정하지 않고 요청에 필요한 구조와 후속 작은 변경의 유지보수 부담으로 검토한다.
- 새 대형 tracing 플랫폼을 만들기보다 기존 host/Click 사건과 최소 adapter를 사용한다.

## 기존 계약과의 결합

`절감 시간`은 실제 재사용으로 생략한 테스트 명령 실행비용이라는 기존 정의를 유지한다.
`토큰 절감률`은 실제 전체 작업 usage 비교에서만 계산하며 공개 화면에는 비율만 둔다.
두 카드와 같은 첫 화면에 별도 `전체 작업 효과` 상태를 추가하되 유효한 task 비교가 없으면 미측정이다.
느려짐을 테스트 절감으로 덮거나 상세 안에만 숨기지 않는다.

기존 automatic sharding init/status/refresh 및 shard reuse 비회귀는 계속 모든 Phase의 필수 acceptance다.
전체 task 계측과 진단/collection은 샤딩 분해, 독립 실행, reuse authority를 변경하지 않는다.

## 반영 위치

- Phase 0: `COMMON.md`, `PLAN.md`, `METRICS.md`, `BASELINE.md`, `progress.json`, `reports/phase-0.md`.
- prompt package: `README.md`, `COMMON.md`, `DISPLAY-REQUIREMENTS.md`, `ESTIMATES.md`, `SOURCE.md`,
  `CHANGELOG.md`, `APPLY-TO-EXISTING.md`, `phase-manifest.json`, `01-command-outcomes.md`부터
  `05-dashboard-and-final.md`까지.
- 이미 실행한 `00-audit-and-baseline.md`는 당시 입력의 이력을 보존하기 위해 바꾸지 않았다.
  현재 실행의 보정 기준은 Phase 0 산출물과 package `COMMON.md`, 보정된 01~05 prompt다.
- Phase 01~03: 정확성/기능 범위는 유지하고 계측 비용·후속 호출·전체 task 연결 및 공통 회귀를 추가했다.
- Phase 04: 전체 task 경계, 사용자 개입, first/prepared, Evidence/Guarded, 후속 유지보수 시나리오와
  전체 완료시간 순효과를 주 평가로 추가했다.
- Phase 05: 두 카드 의미를 유지하고 전체 작업의 빨라짐/느려짐/변화 없음/미측정을 첫 화면에 추가했다.

## 기준선과 진행 상태

B0 snapshot `/home/grapefruit/click-agent-efficiency-baselines/b0-20260907T070638Z`와 manifest hash
`141ec42934f8b3c5694d3fc6e48b11277fe3be74aba88bf5b9a38adf7b6c3886`는 변경하지 않았다.
최초 prompt package hash 목록도 `evidence/prompt-package.json`에 보존했다. 보정 후 prompt hash는
`evidence/outcome-amendment-source.json`에 별도로 기록한다.

Phase 0은 completed 상태이며 다음은 Phase 1이다. 이번 보정에서 제품 runtime, tests, dist,
sharding/reuse policy와 설치 plugin을 변경하지 않았다. N/B0/B1/B2 모델 비교를 실행하지 않았으므로
task_completion_time_savings_ratio, 사용자 개입 비교, token_savings_ratio는 모두 null/미측정이다.
commit/push/install은 수행하지 않았다.
