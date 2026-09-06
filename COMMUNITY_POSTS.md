# Click community drafts

Local drafts, not published posts. The Guarded successor work described here is
part of the v0.82.0 release.
Attach the generated report and its engine/source identity when sharing measured
results; never substitute an avoided-rerun-cost estimate for a measured saving.

## 한국어 — 개발자 커뮤니티

### 제목

Click: 시키지 않은 범위 확장은 억제하고, 유효한 검증은 다시 기다리지 않도록

### 본문

Click은 코딩 에이전트가 실제로 실행한 검증과 현재 적용 가능한 근거를 기록하는
MIT 라이선스 플러그인입니다. 기본 Evidence 모드는 Click 승인 절차를 추가하지
않고 호스트의 권한으로 작업합니다. Guarded를 선택하면 결과·포함/제외 범위·불변조건을
쉬운 계약으로 검토하고, 다음 사용자 턴에서 정확한 계약 ID를 승인합니다.

v0.82.0은 완료된 계약 A의 성공 결과를 새 계약 B에 **후보로만** 전달합니다.
B는 별도로 승인해야 하며, 정확한 검사와 코드·환경·실행 파일·알려진 Hook 범위,
기존 의존성 근거 또는 사전에 커밋한 정책을 다시 확인합니다. 무관 코드 변경에는
유효한 묶음만 이어받고 관련 입력이나 환경이 바뀌면 실제로 다시 검사합니다.
승인·runner token·미완료 작업·완료 상태를 넘기는 기능은 아닙니다.

대시보드 첫 화면에는 이번 약속과 승인, 실제 실행·재사용·실패·미시작, 재실행 비용
회피 추정과 비교 실측 여부를 함께 표시합니다. 과거 계약의 작업명에서 원본 검증과
재사용 이유를 펼쳐볼 수 있습니다. 관측된 승인 전 거부와 비차단 지침을 구분하며,
정책을 느슨하게 만들어 cache hit를 늘리라고 권하지 않습니다.

재현 데모는 독립 fixture의 실제 Hook과 runner를 사용합니다. 기존 전체 검증,
추가 재사용 설정 없는 Guarded, 사전 샤드·무관 코드 정책을 선언한 Guarded의 같은
일곱 단계를 비교합니다. 최초 실행, 무관/관련 코드 변경, 환경 변경, 실패, 수정 후
재시도와 변경 없는 반복을 포함하고 매 단계 같은 상태의 전체 검증과 대조합니다.
fixture의 승인 모사를 개발 세션의 실제 승인으로 쓰지 않습니다.

~~~sh
python3 benchmarks/incremental_verification.py --guarded-workflow --iterations 3 --warmups 1 --workload-rounds 40000 --output /tmp/click-workflow.json --html-output /tmp/click-workflow.html
~~~

워밍업·반복·OS 캐시 미초기화·추가 승인/준비/감사 비용을 공개하며, 짧은 검사에서
관리 비용 때문에 느려지는 음수 결과도 그대로 남깁니다. 재사용 그룹 비율은 테스트
케이스 절감률이 아니며, 부분 계측과 회피 비용 추정을 토큰·요금·개발시간으로
환산하지 않습니다. 표본이 전체 검증과 일치한 것은 보편적 안전성 증명이 아닙니다.

Click은 의미적 범위 준수나 과추론을 완전히 차단하는 엔진, OS 보안 샌드박스,
테스트 충분성 증명이 아닙니다. 관측 가능한 권한·실행·증거 경계를 지키는 도구입니다.
서명 없는 영수증은 무결성을 검사할 뿐 발행자 신원을 인증하지 않습니다.

어떤 승인·검증 경계가 실제 작업에 도움이 되고, 어떤 비용이 더 드는지 피드백을
듣고 싶습니다. [저장소](https://github.com/grapefruit0205/click) ·
[측정 조건과 한계](VERIFICATION_EFFICIENCY.md)

## English — forum / Show HN

### Title

Click: keep work in its approved boundary and carry valid verification forward

### Post

Click records what a coding agent actually verified. Evidence is the default:
host-authorized work without a Click approval ceremony. Opt-in Guarded presents
one readable scope contract and requires its exact id in a later approval turn.

The v0.82.0 release lets a completed Guarded contract A supply successful
check candidates to a separately approved B. B requests and requalifies them
against current execution bindings and existing dependency evidence or an
unchanged policy committed before the baseline. Unrelated code can allow partial
reuse; related inputs, changed environments, and new checks run. Approval,
tokens, unfinished commands, and completion do not carry forward.

The result-first dashboard separates real group outcomes, prior-contract
provenance, estimated avoided rerun cost, partial request timing, and independently
measured comparisons. Observed denials are not semantic scope judgments.

The command above compares no Click, Guarded with default reuse configuration,
and explicit precommitted shards/policy in independent real Hook/runner fixtures.
It includes failure and repair, rotates configuration order, records warmups and
extra setup/transition/audit costs, and checks every state with the full suite.
Negative differences remain visible: short checks can be slower with Click.

This is a workflow guardrail, not a sandbox or a claim to prevent all overthinking,
scope drift, or incorrect code. Fixture agreement is not universal safety;
avoided-cost estimates are not measured net savings, tokens, fees, or developer
time. Please share the generated report with its conditions and unsigned source
identity, not an unsupported speed claim.

[Source](https://github.com/grapefruit0205/click) · [Measurement reference](VERIFICATION_EFFICIENCY.md)
