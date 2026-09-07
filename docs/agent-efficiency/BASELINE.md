# B0 기준선

Phase 0 시작 시점의 현재 Click을 보존했다. **B0는 HEAD 단독이 아니라 아래 dirty 파일을 포함한 스냅샷**이다. 후속 B1/B2는 B0와 같은 초기 과제에서 비교한다.

2026-09-07 순효율 보정은 B0 캡처 뒤 prompt와 Phase 0 문서에 적용했다. B0 bytes·HEAD·manifest와
최초 prompt hash를 다시 만들거나 덮어쓰지 않았다. 보정은 평가 목표를 전체 완료시간·사용자 개입·
완료 품질 중심으로 명확히 했으며 B0 제품 동작을 바꾸지 않는다.

- 소스 root: `/home/grapefruit/Documents/Codex/2026-09-06/click-live-0-81-0-codex/work/click-release`
- HEAD: `3b7f8e6ec260729b4ba8a13484ea4c33dce7baeb`
- source manifest version: `0.90.0`
- 설치 plugin: `0.90.0+codex.20260907045039` (`/home/grapefruit/.codex/plugins/cache/click/click/0.90.0+codex.20260907045039`)
- 캡처 시점 UTC: `20260907T070638Z`
- 보존 위치: `/home/grapefruit/click-agent-efficiency-baselines/b0-20260907T070638Z`
- 300개 tracked/untracked non-ignored 경로, 총 6,229,032 bytes. `worktree/`에는 유효 파일 bytes/원래 mode/symlink를 보존했다.
- manifest SHA-256: `141ec42934f8b3c5694d3fc6e48b11277fe3be74aba88bf5b9a38adf7b6c3886`
- `HEAD.bundle`은 HEAD history를 독립 보존하며 `git bundle verify`가 통과했다. `index.patch`, `worktree.patch`, `status.txt`, NUL 구분 원본 status, manifest에 복구 근거가 있다.
- baseline 디렉터리와 부모는 0700이다. 작업 트리와 hardlink를 공유하지 않는다. 후속 작업은 여기에 쓰지 않고 별도 복사본을 사용한다.
- Git metadata/ignored cache·bytecode·가상 환경, 호스트 계정/credentials/usage 원문, 실행중 claim/receipt/session은 복사하지 않았다. 원래 런타임이나 토큰/승인을 재현한다고 주장하지 않는다.

## 실제 버전

| 구성 | 확인 결과 |
| --- | --- |
| ChatGPT/Codex Desktop package | 26.901.51231 (`dpkg-query --show chatgpt`) |
| 호스트 session metadata / standalone CLI | 0.153.4 / codex-cli 0.153.4 |
| 현재 모델 / effort | gpt-6-astra / xhigh |
| Python | 3.12.3, /usr/bin/python3.12 |
| Node | v22.23.2 |
| Git | 2.43.0 |
| OS | Linux x86_64, kernel 7.0.0-31-generic |

설치 plugin은 대시보드 변경 전 cache이며, 이번 검사 대상은 소스 checkout이다. 외부 Hook가 실행하는 runner와 검사에서 import하는 소스 Click을 혼동하지 않는다. 설치/업데이트는 수행하지 않았다.

## Dirty 상태

전체 경로별 종류·mode·SHA-256은 `evidence/b0-files.json`, 변경/미추적 목록은 `evidence/b0-status.txt` 및 baseline manifest를 참조한다. 캡처 당시 tracked 수정은 12개이고 staged patch는 비어 있다. 기존 docs/dashboard-impact/ 미추적 파일들도 보존했다.

```text
M README.md
 M VERIFICATION_EFFICIENCY.md
 M dist/antigravity/hooks/click_dashboard_projection.py
 M dist/antigravity/hooks/click_incremental.py
 M dist/antigravity/hooks/click_shadow_dashboard.py
 M hooks/click_dashboard_projection.py
 M hooks/click_incremental.py
 M hooks/click_shadow_dashboard.py
 M tests/test_click_dashboard_projection.py
 M tests/test_click_efficiency.py
 M tests/test_click_shadow_dashboard.py
 M tests/test_incremental_benchmark.py
?? docs/dashboard-impact/COMMON.md
?? docs/dashboard-impact/METRICS.md
?? docs/dashboard-impact/PLAN.md
?? docs/dashboard-impact/README.md
?? docs/dashboard-impact/SOURCE-PROMPT.md
?? docs/dashboard-impact/evidence/README.md
?? docs/dashboard-impact/evidence/guarded-workflow.v4.json
?? docs/dashboard-impact/evidence/real-hook-projection.v7.json
?? docs/dashboard-impact/evidence/real-hook-report.v4.json
?? docs/dashboard-impact/progress.json
?? docs/dashboard-impact/reference-dashboard.png
?? docs/dashboard-impact/reports/final.md
?? docs/dashboard-impact/reports/languages.md
?? docs/dashboard-impact/reports/phase-0.md
?? docs/dashboard-impact/reports/phase-1.md
?? docs/dashboard-impact/reports/phase-2.md
?? docs/dashboard-impact/reports/phase-3.md
?? docs/dashboard-impact/reports/phase-4.md
?? docs/dashboard-impact/reports/phase-5.md
?? docs/dashboard-impact/screenshots/desktop.jpg
?? docs/dashboard-impact/screenshots/languages/desktop-en.jpg
?? docs/dashboard-impact/screenshots/languages/desktop-zh.jpg
?? docs/dashboard-impact/screenshots/languages/mobile-en.jpg
?? docs/dashboard-impact/screenshots/languages/mobile-zh.jpg
?? docs/dashboard-impact/screenshots/mobile-failed.jpg
?? docs/dashboard-impact/screenshots/mobile-untimed.jpg
?? docs/dashboard-impact/screenshots/mobile.jpg
```

## 복구 절차

새 빈 임시 저장소에서만 수행한다. 현재 작업 트리에 checkout/reset/clean을 적용하지 않는다.

1. manifest와 HEAD.bundle의 SHA-256을 대조하고 `git bundle verify`로 bundle을 확인한다.
2. 새 저장소를 만들고 로컬 HEAD.bundle의 HEAD를 fetch하여 detached checkout한다. 외부 네트워크는 필요 없다.
3. index.patch가 비어 있지 않으면 새 저장소에만 `git apply --index`한다.
4. manifest의 모든 경로에 대해 worktree/의 bytes/symlink/mode를 복원하고 kind=absent는 제거한다. 이를 통해 unstaged/untracked 변경을 복구한다.
5. 모든 파일 digest/종류/mode 및 status를 캡처 값과 대조한다. 실행 환경/host settings/권한/Click runtime은 별도로 명시하여 새로 시작한다.

복구 실검사 결과는 reports/phase-0.md 및 evidence/phase-0-integrity.json에 기록한다. source snapshot만으로 live receipt나 재사용 준비 상태가 자동 복구되는 것은 아니다.

## 비교 정의

- B0: 이 source snapshot. 기존 진단 출력과 fail-fast, 기존 sharding/reuse 동작.
- B1: 같은 기반에 Phase 1 결과 기록과 Phase 2 진단 통합을 적용하되 collection off.
- B2: 같은 기반 + 진단 통합 + Phase 3에서 명시적으로 선택한 독립 source/예산.
- N: 동일 조건의 Click 미사용 호스트 평가가 가능한 때만 별도 선택.

각 군은 첫 사용/준비된 반복 사용과 Evidence/Guarded를 분리한다. 요청 수신부터 동일 acceptance를
충족한 결과 반환까지의 task elapsed, 사용자 개입, 실패·미완료·느려진 사례를 기록한다.
현재 paired 모델 평가 0회이며 task_completion_time_savings_ratio=null,
token_savings_ratio=null, user_intervention 비교도 미측정이다. 기존 dashboard-impact/auto-sharding
benchmark는 실행 동작/시간에 대한 이전 자료이며 이번 B0→B2 전체 작업 효과가 아니다.
Phase 4는 B1도 변경 전 별도 snapshot으로 보존하고 task seed/prompt/acceptance를 고정해야 한다.
