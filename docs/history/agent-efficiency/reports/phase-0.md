# Phase 0 — 감사·기준선 보존 완료

00번 프롬프트만 수행했다. 다음은 01-command-outcomes.md이다. runner/재사용/승인/receipt/샤딩 정책/대시보드 제품 코드는 변경하지 않았다. 기존 dirty 소스와 완료된 별도 문서를 B0에 보존했다.

Phase 0 완료 뒤 사용자가 제공한 전체 작업 순효율 기준을 산출물과 향후 01~05 prompt에 보정했다.
B0는 다시 만들지 않았고 Phase 1 제품 코드는 아직 시작하지 않았다. 보정 기록은
`reports/phase-0-amendment.md`와 `evidence/outcome-amendment-source.json`에 있다.

## 결과

- 공통 원문 COMMON.md를 보존하고 사용자가 지정한 automatic sharding init/status/refresh 및 shard reuse 비회귀 조건을 모든 단계에 추가했다.
- PLAN.md에 기존 기능·부족한 기능·수정 대상·CORE/USER_POLICY/HEURISTIC 경계와 단계별 회귀 검사를 연결했다.
- B0: source version 0.90.0, HEAD 3b7f8e6ec260729b4ba8a13484ea4c33dce7baeb + tracked dirty 12개와 기존 untracked 문서/증거. 300개 경로와 HEAD bundle을 독립 로컬 스냅샷으로 보존했다.
- host는 실제 usage/response ID를 기록하지만 B0/B1/B2 비교는 없다. METRICS.md는 미확인 causal 왕복/토큰 비율을 null로 정의하고 내부 raw usage와 공개 ratio-only allowlist를 분리했다. 원시 토큰 사용량은 공개 산출물에 복사하지 않았다.
- 최상위 제품 판단을 동일 완료 품질의 전체 완료시간과 사용자 개입으로 보정했다. 테스트 기준 절감 시간과 토큰 절감률은 원인·효율 지표로 유지한다. 현재 전체 task 비교는 없으므로 결과는 미측정이다.
- 시간 집계·status·next_action·source 결과·inspect 배치·대시보드/export와 existing paired/workflow benchmark를 재사용할 수 있다. 명령별 outcome/통합 진단/선택적 bounded collection/모델 비교·토큰 presentation은 후속 작업이다.

## 검증

| 검사 | 결과 |
| --- | --- |
| E_AGENT_SHARDING_BASELINE — setup/collector/proposal/boundary/inventory/map/safe-change | 74 tests, 72 통과, 2 skip, 82.927s |
| E_AGENT_SHARD_REUSE_BASELINE — 실제 gate의 parent 재제출·실패 후 sibling·safe-change·변조 fallback·successor timing/receipt | 8 tests 통과, 29.358s |
| 합계 | 82개 중 80개 통과, 2개 skip, 실패 0 |
| B0 원본/스냅샷 해시·bundle·임시 저장소 복구·최종 변경 범위 | evidence/phase-0-integrity.json 참조 |
| 최종 diff 공백 검사 | E_AGENT_PHASE0_DIFF, git diff --check |

테스트는 변경 전 보존한 B0 제품 코드에서 실행했고 이후 추가 파일은 docs/agent-efficiency뿐이다. 후속 문서 기록 때문에 테스트를 반복하지 않는다. 최종 해시 대조가 기존 모든 파일의 동일성을 확인한다. 결과 JSON과 progress는 감사 기록이며 현재 revision의 completion receipt나 재사용 권한이 아니다. 이번 단계는 자동 receipt export를 수행하지 않는다.

## 한계와 관측한 마찰

1. native authoritative sharding E2E의 library/nested 두 사례는 기존 테스트의 `prepared native authoritative profile required` 조건으로 skip했다. Evidence setup→baseline 및 committed-policy/same-revision/successor reuse 검사는 통과했지만 native 자동 partial reuse가 이 실행에서 검증됐다고 주장하지 않는다. 예전 docs/auto-sharding의 성공 기록과 이번 실검사 결과를 구분한다.
2. 현재 host task cwd는 /home/grapefruit/docs로 non-Git이다. per-call workdir을 실제 repo로 선택했어도 직접 `click-gate sharding status`가 `non-git-project`로 거부되었다. setup control이 Hook의 `_tool_working_directory(event)`를 쓰는 경로를 확인했다. 이 명령에 임의 workdir 옵션/승인/상태를 주입하지 않았다. verify는 공식 protocol v2의 명시적 workdir으로 수행했고 public control은 격리 fixture에서 검증했다. 이 제한은 이번 작업의 제품 변경으로 생긴 회귀가 아니다.
3. 검증 중 별도 read 요청 한 번은 active verification interlock으로 거부되었다. 종료를 기다린 뒤 읽었다. 진단의 추가 코드 문맥은 Phase 2에서도 이 claim 경계를 유지해야 한다.
4. 모델 비교, 신규 진단 capture 비용, 실제 인과 왕복, 사용량의 중복/누적/포함 관계 adapter 검증은 아직 실행하지 않았다. 별도 모델 호출·서비스·SDK를 추가하지 않았다. 실제 token 절감률은 미측정이다.
5. 전체 테스트 suite와 브라우저 검수는 이번 audit에서 실행하지 않았다. 기존 ko/en/zh-CN 대시보드 코드를 그대로 보존했다. Phase 5의 두 카드 요구를 이미 구현한 것으로 표시하지 않는다.

## 다음 단계

Phase 1은 B0 무결성·이 보고서·순효율 보정 보고서·COMMON/PLAN/METRICS/BASELINE/progress를 읽고 시작한다. 기존 fail-fast 실행을 유지하면서 `succeeded_count` prefix 의존을 실제 명령별 outcome으로 정리한다. receipt/Observer/parent 결과가 같은 사실을 사용해야 하며 mandatory sharding/reuse 회귀를 다시 확인한다. Phase 2 이후는 자동 진행하지 않는다. commit/push/install은 수행하지 않았다.
