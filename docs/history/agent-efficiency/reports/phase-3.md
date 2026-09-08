# Phase 3 — 독립 실패의 제한적 일괄 수집 완료

기본 fail-fast를 그대로 두고, 사용자가 이미 제출한 source의 실행 독립성을 명시한 요청에만 적용되는 `failure_collection` v1을 추가했다. 샤드 분리나 파일 겹침만으로 독립성을 추정하지 않으며 이 정책은 reuse authority나 검사 범위를 만들지 않는다.

## 정책과 실행 경계

- 생략 시 `mode: off`, 빈 source 목록과 0 예산이다. bounded는 서로 다른 제출 evidence id가 둘 이상이어야 한다.
- bounded 상한은 추가 source 3개, 추가 실패 3개, 첫 실패 뒤 다음 source 시작 허용창 30초다. 실제 요청은 이 상한 안에서 더 작은 값을 명시한다.
- 같은 source 안의 뒤 명령은 첫 실패 후 `not-run`이다. 다음 source는 지원 parser가 실제 `test-failure`로 분류한 경우에만 시작한다. unittest ERROR, 공통 setup/runtime 오류, unknown parser, interrupt는 즉시 멈춘다.
- 다음 source마다 runner token/state/batch claim/cancel, 보호된 Git snapshot, environment, executable과 command binding을 다시 확인한다. drift나 결과 기록 실패가 있으면 중단하고 첫 nonzero exit를 유지한다.
- 결과에는 최초 실패 source, 실제로 허용한 source, 추가 시작/실패 수, 경계 확인 수와 시간, 정확한 중단 사유를 기록한다. v5 명령 배열은 비연속 source outcome을 보존하고 뒤 성공으로 최초 실패를 덮지 않는다.
- automatic sharding이 parent를 child id로 확장해 명시한 독립 source identity와 달라지면 bounded를 적용하지 않고 안전하게 fail-fast로 돌아간다.

## 실제 runner 통합 검증

`tests.test_click_failure_collection`의 9개 검사가 통과했다. A 실패+B 실패+C 성공, A 실패+B 성공, 같은 source 중간 실패, setup/unknown 실패, source·failure·시간 예산, cancel/drift와 자동 샤딩 identity fallback을 확인했다.

실제 runner fixture에서는 독립 source E1과 E2의 서로 다른 unittest 실패 두 개를 한 요청에 기록했다. 허용된 file mutation 경로로 fixture를 수정한 뒤 원래 요청을 다시 제출해 두 source가 통과했고, 같은 조건으로 한 번 더 제출했을 때 runner 실행 없이 두 결과가 `reused`인지 확인했다. 이 과정에서 all-reuse 안내 shell 문자열의 괄호가 `/bin/sh` syntax error를 만드는 기존 문제를 발견해 괄호 없는 문구로 수정했다.

이 fixture는 실제 Click runner, state/claim, mutation 재판정과 reuse 경로를 사용하지만 자동화된 테스트 사용자다. 사람의 승인시간, 모델 왕복, 전체 task 완료시간 또는 토큰 실측으로 세지 않는다.

## 순효과와 호환성

추가 source 시작 수, 추가 실패 수와 경계 확인 비용은 기록하지만 여러 실패를 얻었다는 이유로 사용자 호출이나 토큰 절감을 선언하지 않는다. 동등한 B0/B2 모델 작업 비교가 없어 전체 순효과는 미측정이다. collection을 쓰지 않으려면 필드를 생략하거나 `mode: off`를 유지한다.

기존 original parent 재제출, 현재 revision 재판정, exact/dependency/precommitted safe-change reuse, sibling stale/retry와 Observer default off를 유지했다. 최종 automatic sharding/reuse 공통 회귀는 `reports/final.md`에 기록한다.
