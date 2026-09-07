# Click agent efficiency Phase 0~5 최종 보고서

Phase 0에서 B0를 복구 가능한 형태로 보존하고 전체 작업 순효과 기준을 반영한 뒤, Phase 1~5를 순서대로 구현했다. 공개 릴리스 기준은 계속 **v0.90.0**이며 버전 bump, commit, push, plugin 설치와 Observer 활성화는 하지 않았다.

## 최종 동작

1. Phase 1은 v5 verification batch에 각 명령의 실제 planned/started/passed/failed/interrupted/not-run/unknown 결과, 위치, digest, exit와 측정 구간을 기록한다. source, receipt, Shadow와 parent 완료 판정은 같은 명령 fold를 사용한다.
2. Phase 2는 원래 명령을 한 번만 실행하면서 stdout/stderr를 동시에 bounded capture한다. 기본은 기존 raw 출력이고 명시적 actionable에서는 unittest/pytest 실패 test id, 안전한 파일/행, 핵심 오류, 잘림·parser·local log 상태와 다음 행동을 반환한다. 자동 source 읽기나 authority 생성은 없다.
3. Phase 3은 기본 fail-fast를 유지한다. 명시적으로 독립 선언한 제출 source에서만 최대 3 source/3 failure/30초 시작창 안의 bounded collection을 허용하며 각 source 전에 state/claim/cancel/Git/environment/executable을 다시 확인한다.
4. Phase 4는 내부 task/usage v1에서 비율 전용 public v1을 만드는 최소 adapter를 제공한다. task 경계·동일 acceptance/completion·완전 usage와 중복/포함 관계가 유효할 때만 task 및 token 비율을 계산한다.
5. Phase 5는 기존 dashboard projection/viewer/export를 확장했다. 첫 화면에 `절감 시간`, `토큰 절감률`과 별도 `전체 작업 효과`를 배치하고 양수·0·음수·미세 변화·미측정 및 불리한 결과를 구분한다. B0→B2/N→B2, first-use/prepared-repeat, Evidence/Guarded와 scenario를 섞지 않는다.

Observer는 계속 기본 `off`다. 이 상태에서도 dashboard, 명령 결과 기록, exact reuse와 기존 policy reuse가 동작한다. `shadow` 또는 `authoritative` 관찰이 필요한 별도 경우에만 기존 명시적 제어를 사용한다.

## 공개 데이터 경계

내부 평가 파일은 실제 계산 재현에 필요한 raw usage와 절대 token 수를 보존한다. 공개 projection과 viewer importer는 exact allowlist를 사용하며 raw usage, 전후/절감 token 절대량, 임의 event/series를 거부한다. 화면, 접근성 문구, 복사, JSON v5와 독립 HTML은 같은 public presentation을 쓴다. 로컬 원자료 파일명, 로그, 코드, 명령/인자, 경로, 환경, 계약과 인증정보는 공유본에 넣지 않는다.

현재 `phase-4-internal.json`에는 실제 비교 pair가 0개이고 합성 값도 없다. 호스트에서 분리된 N/B0/B1/B2 task의 시작·종료와 완전 usage를 확보하지 못했기 때문에 public presentation은 미측정이다. 따라서 전체 task 완료시간 절감, 사용자 개입 변화, token 절감률의 성과 수치를 주장하지 않는다. 이 상태는 0%가 아니다.

## 검증 결과

최종 broad 명령 `python3 -m unittest discover -s tests -q`는 **856개 중 849개 통과, 기존 7개 skip, 실패 0**으로 끝났다. Phase 1 기준 822개보다 기능 검사가 34개 늘었고 skip은 증가하지 않았다.

공통 필수 매트릭스 결과는 다음과 같다.

| 범위 | 결과 |
| --- | --- |
| automatic sharding init/status/refresh, proposal/application/bootstrap, inventory/fallback | 74개 중 72 통과, 기존 native authoritative 2 skip, 실패 0 |
| original parent, sibling retry/reuse, map-only/caller observation 금지, safe-change/current-condition 재판정 | 8개 모두 통과 |
| Phase 2~5 집중 검증 | 81개 모두 통과 |
| 전체 repository suite | 856개 중 849 통과, 7 skip, 실패 0 |

첫 mandatory 실행에서는 새 테스트 파일 3개가 `.click/evidence-shards.json`의 owner에 배정되지 않은 회귀가 1건 발견됐다. 진단/collection 테스트를 `verification-evidence`, task 평가 테스트를 `repository-policy` shard에 배정하고 inventory와 전체 74개를 다시 실행해 통과했다. 실패를 숨기거나 skip으로 바꾸지 않았다.

실제 runner fixture는 독립 실패 2개 수집 → 허용된 fixture 수정 → 현재 조건 재판정 → 두 source 통과 → 변경 없는 재제출에서 두 source 재사용을 확인했다. collection off의 fail-fast, 취소·drift·setup/unknown 오류, 예산과 자동 shard identity fallback도 통과했다.

Antigravity distribution을 기존 build script로 다시 생성했다. 명시적 hook 60개의 source/dist bytes가 모두 같고 manifest 누락은 없다. Python compile, dashboard JavaScript syntax, JSON parsing, Phase 4 public 재생성 byte equality와 `git diff --check`도 통과했다.

## 시각·언어 검수

기존 실제 Hook v7 projection에 v8의 실제 미측정 task 객체를 붙인 로컬 전용 화면으로 검수했다. 예시 token/task 비율은 사용하지 않았다.

- 1440×900: `evidence/phase-5-dashboard-1440x900.png`
- 390×844: `evidence/phase-5-dashboard-390x844.png`

데스크톱에서 상태와 두 카드, 전체 작업 효과가 첫 화면에 보이며 모바일에서는 같은 순서로 세로 배치된다. interactive control의 semantic 요소, `:focus-visible`, skip link와 `prefers-reduced-motion: reduce`를 회귀 검사에 고정했다. 한국어/English/简体中文에서 task/token 상태·근거·복사와 export 의미가 함께 전환되는지 확인했다.

## 재현

```sh
python3 benchmarks/task_efficiency.py \
  docs/agent-efficiency/evidence/phase-4-internal.json \
  --public-output /tmp/click-phase-4-public.json
cmp /tmp/click-phase-4-public.json \
  docs/agent-efficiency/evidence/phase-4-public.json

python3 -m unittest \
  tests.test_click_diagnostics \
  tests.test_click_failure_collection \
  tests.test_task_efficiency \
  tests.test_click_efficiency \
  tests.test_click_dashboard_projection \
  tests.test_click_shadow_dashboard -q

python3 -m unittest discover -s tests -q
```

요청 schema, 기본값, 보관 한계와 대시보드 측정 범위는 `VERIFICATION_EFFICIENCY.md`와 `skills/click/references/verification-efficiency.md`에 있다. Phase별 상세와 machine-readable 결과는 같은 디렉터리의 `phase-2.md`~`phase-5.md`, `../evidence/final-test-results.json`을 참조한다.

## 남은 제한

- 준비된 native authoritative profile이 필요한 두 E2E는 B0와 마찬가지로 skip이다. 나머지 setup state machine과 reuse/fallback은 실행해 통과했지만 이 환경에서 native partial reuse를 새로 실증했다고 주장하지 않는다.
- 실제 전체 task 비교를 수행하려면 각 variant의 분리된 workspace/session, 같은 seed/prompt/acceptance, host task 경계와 완전 usage export가 필요하다. 각 compatible scope당 5쌍 pilot 목표는 아직 0/5다.
- actionable 기본 전환은 하지 않았다. 실제 paired host 평가가 전체 완료시간과 사용자 부담 개선을 뒷받침하기 전까지 raw가 호환 기본값이다. bounded failure collection도 계속 명시적 opt-in이다.
