# Phase 0 — 현행 감사

- 체크아웃: `3b7f8e6ec260729b4ba8a13484ea4c33dce7baeb`, `.codex-plugin/plugin.json` version 0.90.0. 시작 시 `git status --short` 출력 없음.
- 적용되는 AGENTS.md 및 `.openai/hosting.json` 없음. 기존 Python/vanilla JS/CSS 경로를 유지한다.
- `hooks/click_incremental.py`: `revalidation_savings`(1013), `batch_summary`(906), `history_accounting`(864), `host_summary`(1329). 이미 단일 A/E/F/R 집계가 있다.
- `hooks/click_dashboard_projection.py`: `dashboard_projection`(279), `projection_is_valid`(580), projection v6, 과거 30개 배치별 동일 집계 전달.
- `hooks/click_shadow_dashboard.py`: `outcomePresentation`(263), `renderOutcome`(365), `renderSources`(468), `renderBatch`(593), `readComparison`(726), `shareReport`(790), `standaloneReport`(825).
- 발견: 부분 시간 표본에 `≥` 사용, 시간 미측정 Hero의 실적 누락, 실제 묶음 대응 블록 없음, fixture 음수 경고의 scope 모호함, current/selected task 상단 혼동 가능성, 공유 요약 복사 없음.
- 시간 표본 선택은 `hooks/click_verification.py` planning에서 `baseline_is_suitable`로 현재 조건에 결합한다. 승인/runner 변경 없이 표현·집계를 개선할 수 있다.
- importer의 실제 지원은 workflow v4 / paired v2. README와 VERIFICATION_EFFICIENCY.md의 workflow v2/v3 안내는 뒤처져 있다.
- 검증 근거: 실제 파일/함수 검색, version/HEAD/status 조회, 기존 산식·projection validator·DOM fixture·Hook 테스트 읽기. Phase 0에서 운영 코드 변경 없음.

다음: 기존 경로의 정확성 보완과 UI 개선. 첨부 중단 지시보다 사용자의 대시보드 구현 요청을 기준으로 이어서 수행한다.
