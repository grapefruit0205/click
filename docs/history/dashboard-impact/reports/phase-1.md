# Phase 1 — 시간·횟수 집계

상태: 완료.

- 기존 `revalidation_savings`를 화면과 내보내기의 단일 집계 경로로 유지했다. `scope_complete`에 최종 결과 기록을 요구하여 시작했지만 끝나지 않은 요청의 완료 표시를 막았다.
- 일부 시간 표본의 합계는 부분 추정으로 표시한다. 최소·≥ 표현을 제거하고 시간 자료가 없으면 null을 유지한다.
- `retained_impact`는 보관 이력의 batch_id를 중복 제거하고 정상 완료된 요청만 집계한다. 별도 재시도는 별도 요청이며, 기간·보관 제한·누락 표본 수를 전달한다.
- projection은 v7로 변경했다. v4~v6 입력 호환성을 유지하고 새 누적 필드의 개수·시간·범위 일관성을 검증한다. 실행·재사용 판정과 runner는 변경하지 않았다.

검증: `tests.test_click_efficiency`와 `tests.test_click_dashboard_projection` 합계 35개 통과. 부분/전체 재사용, 전부 실행, 0 분모, 누락 시간, 계측 조건 불일치, 실패·중단·거부·미완료, 구버전, 중복 및 별도 요청을 포함한다. 다른 Observer 조건의 표본은 시간 비율을 숨기되 재사용 사실을 바꾸지 않는 것을 확인했다.

변경: `hooks/click_incremental.py`, `hooks/click_dashboard_projection.py`, 관련 테스트 및 생성된 dist 사본. 수식과 범위는 [METRICS.md](../prompts/METRICS.md)에 기록했다.
