# 구현 계획

기준: Click 0.90.0, commit `3b7f8e6ec260729b4ba8a13484ea4c33dce7baeb`, 시작 시 clean.

| 단계 | 기존 기능 / 부족한 점 | 변경 대상 및 완료 조건 |
| --- | --- | --- |
| 0 | Python loopback viewer, projection v6, 시간 Hero, A/E/F/R 집계, 배치 선택, importer, 독립 HTML이 이미 있음 | 조사·지표 계약·원문·디자인 참고와 진행 기록 보존 |
| 1 | `revalidation_savings` 단일 집계와 적합한 표본 선택이 있음. 부분 표본 최소 표기, 정상 완료 조건 보완 필요 | 집계 경로 보존, 완료·누락·0·계측 조건 회귀 검증, 보관 완료 요청의 누적 집계 추가 |
| 2 | 어두운 세로 화면, 시간 미측정 시 Hero가 비어 보임. 대응 묶음 블록 없음 | 민트/청록 카드와 sidebar, 횟수 fallback, 같은 0축 시간 비교와 회피 구간, 상태/추정 표시, 반응형 |
| 3 | 이유·출처 상세와 배치 선택 존재 | Hero→재사용 필터→묶음 상세, 실제 이력 흐름, 과거 배치 표시, polling 시 선택/초점 유지, 누적 범위/시간대 표시 |
| 4 | importer는 실제 v4 workflow와 v2 paired를 지원하나 문서는 구버전 안내. fixture 음수 경고가 현재 요청처럼 보임 | fixture 범위 경고, 비교 제거, 안전한 요약 복사, 화면/JSON/HTML에 같은 지표·표현·그래프, 문서 정정 |
| 5 | 산식·DOM fixture·실제 Hook/runner 테스트 존재 | 관련 회귀, 실제 기준→변경→부분 재사용→projection/export 연결, 1440×900/390×844 캡처, 키보드/reduced-motion, 배포본 동기화 검사 |

주요 파일: `hooks/click_incremental.py`, `hooks/click_dashboard_projection.py`, `hooks/click_shadow_dashboard.py`, `tests/test_click_efficiency.py`, `tests/test_click_dashboard_projection.py`, `tests/test_click_shadow_dashboard.py`, 기존 benchmark/Hook 테스트, README와 VERIFICATION_EFFICIENCY.md. dist는 기존 생성기를 통해 동기화한다.
