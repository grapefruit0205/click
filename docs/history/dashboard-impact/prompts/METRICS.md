# 지표 계약

| 표시 | 기존 필드 / 정의 |
| --- | --- |
| N 요청 묶음 | `batch_summary.total_source_count` = canonical expanded batch requested count |
| X 실제 시작 | `executed_source_count`, passed/failed/interrupted는 이 수의 부분집합 |
| U 실제 재사용 | `authoritative_reuse_count`, status=reused인 실제 적용만 포함 |
| E 실행 구간 합계 | `revalidation_savings.executed_test_execution_ms` |
| A 회피 비용 추정 | `omitted_test_execution_ms`, 적합한 원본 성공 표본 합계 |
| F 동일 묶음 전체 순차 예상 | `full_sequential_test_execution_estimate_ms` = E+A, 정상 완료·완전 범위·동일 계측 조건일 때만 |
| R 실행 구간 감소 추정 비율 | `test_execution_reduction_ratio` = A/F, F>0일 때만; U/N과 별개 |

정상 완료에서만 N=X+U 및 N/N 결과 확보를 표현한다. 미측정은 null, 실제 0과 구분한다. 일부 표본의 A는 부분 추정이며 F/R은 null이다. 현재 대표값은 count fallback을 사용한다. 진행/실패/취소는 상태를 우선 표시한다.

표본은 `build_duration_baseline`의 source/check/원본 task·batch·시각·unit·scope·sequential model·observer mode·timing binding을 갖는다. 실제 planning의 `baseline_is_suitable`이 환경/실행 파일/호스트/Observer의 현재 timing binding을 대조한 뒤 batch에 적합한 표본만 기록한다. renderer는 표본을 다시 고르거나 산식을 재구현하지 않는다. runner는 source command를 순차 실행한다. 구형·병렬·조건 불일치 표본을 순차 대기시간으로 환산하지 않는다.

E/F는 전체 사용자 대기시간이 아니다. Hook 진입→결과 기록은 부분 요청 구간이다. 중첩된 구간의 임의 차감으로 전체 관리비용을 만들지 않는다.

완료 이력의 보조 누적치는 보관 범위에서 batch_id를 중복 제거하고 정상 완료·완전 범위 요청만 포함한다. 별도 재시도는 별도 요청이다. 시간 표본 누락·보관 기간·UTC 범위를 함께 제공하고, UI는 표시 시간대를 명시한다. 기존 accounting(실패 포함 모든 요청)은 그대로 유지한다.

실측 importer: paired v2 및 workflow v4. workflow fixture와 실제 저장소 번들 참조는 별개다. D=full−Click의 쌍별 차이 중앙값을 각 경로 중앙값과 구분한다. 음수는 해당 비교 표본에서 유지하고 현재 live 요청으로 귀속하지 않는다.
