# Phase 0 — 지원 범위·권한·완료조건 확정

## 현재 Phase 목표
Click 설정이 없는 지원 대상 프로젝트에서 사용자가 JSON이나 테스트 배치를 직접 작성하지 않아도
테스트 탐색 → 의존성 후보 분석 → 샤드 구성 → 설정 승인·커밋 → 기준 검증 → 새 계약과 코드 변경 후 적격 샤드 재사용
까지 이어지는 자동 샤딩·재사용 초기 설정의 지원 범위와 안전 경계를 확정한다.

대시보드의 대표 가치는 “전체 재검증을 피해서 생략한 테스트 실행시간”이며,
관리비용·요청 전체 시간·순시간 절감은 상세 영역으로 둔다.

## 시작 전 확인
- AGENTS.md
- PRODUCT_CONSTITUTION.md
- GUARD_CLASSIFICATION.md
- skills/click/SKILL.md
- shards / dependency / observer / verification / capability 관련 참조
- 현재 브랜치와 사용자 변경
- 기존 샤드 loader, verification runner, dependency declaration/observation, 계약 간 계승, CLI, 패키징, 시간 집계

이미 구현됨 / 결함 / 미구현을 구분한다.

## 승인 경계
Phase 0~2는 “프로젝트 분석과 샤드·의존성 설정안 생성” 범위를 다룬다.
실제 변경 후 스킵 권한을 제공하는 authoritative observation 경계는 Phase 3에서 별도 승인 대상으로 취급한다.

유효한 승인 계약이 없다면 Phase 0~2를 포괄하는 최소 Guarded 계약을 stage하고,
Hook이 출력한 실제 contract_id와 plain-language 설명을 사용자에게 제시한 뒤 승인 대기 상태로 전환한다.
이 문서, /goal, 자동 continuation을 승인으로 간주하지 않는다.

승인되면 Phase 0을 수행하고, 완료 후 오케스트레이터에 제어를 반환한다.

## 초기 지원 범위
첫 adapter는 표준 CPython unittest의 명시적인 전체 검증 명령과 모듈 단위 테스트를 대상으로 한다.
변경 후 자동 재사용의 최초 운영 지원은 검증 가능한 Linux observation backend 1종으로 좁힌다.
실제 지원 Python/OS/backend 버전과 명령 옵션을 명세에 고정한다.
미지원 환경은 명시적으로 표시하고 원래 전체 검증을 유지한다.

초기 범위에서 제외:
- pytest / Jest / Vitest 동시 지원
- 원격 캐시
- 새로운 병렬 스케줄러
- 자체 테스트 프레임워크
- 외부 분석 수집
- 자동 배포·게시
- 자동 commit/push

## 안전 모델
정적 메타데이터 읽기와 테스트 수집·import·실행을 분리한다.
수집도 코드를 실행할 수 있으므로 승인된 실행 경로를 사용한다.

“테스트를 어느 샤드에 넣을지”와
“현재 변경 후 이전 성공 결과를 재사용해도 되는지”는 별도 문제로 취급한다.

정적 import, coverage, Shadow, 성공한 시범 실행은 후보 정보일 뿐 스킵 권한이 아니다.
설정 생성 권한, 설정 확정, 기준 검증, 현재 요청 실행 권한을 분리한다.
미커밋 JSON을 활성 정책으로 사용하지 않는다.
모델 밖 외부 상태나 불완전 관찰이 있으면 재실행한다.

## 상태 모델
최소 다음 상태를 정의한다.
- unconfigured
- analysis-required
- proposal-ready
- approval-required
- commit-required
- baseline-required
- sharding-ready
- reuse-ready
- unsupported
- blocked

각 상태의 진입 조건, 종료 조건, fallback을 문서화한다.

## 산출물
docs/auto-sharding-plan.md 를 작성/갱신한다.

포함 항목:
- 지원표
- 상태 전이
- 데이터 흐름
- candidate vs authority 구분
- 수정 지점
- 실패 처리
- Phase별 완료조건
- 검증 전략
- 최초 설치 사용 흐름
- 시간 절감 지표와 대시보드 연결

## 전체 제품 완료조건
설정이 전혀 없는 별도 지원 프로젝트에서:
1. 사용자가 JSON을 직접 작성하지 않는다.
2. 테스트 inventory와 샤드 구성이 생성된다.
3. 승인·커밋·기준 검증을 거친다.
4. 새 Guarded contract_id에서 실제 코드가 변경된다.
5. 적격 샤드는 재사용되고 관련 샤드는 실행된다.
6. 전체 재검증 대신 부분 검증을 수행해 생략한 테스트 시간이 대시보드에 나타난다.

설정 파일 생성만 성공하거나 모든 검증을 항상 실행하는 상태는 전체 완료가 아니다.

## Phase 0 완료조건
- 이후 Phase가 추측 없이 구현할 수 있는 명세가 존재한다.
- 지원/미지원 경계가 명확하다.
- 승인 경계가 명확하다.
- 기존 기능과 신규 구현 범위가 구분된다.

완료되면 progress 상태를 갱신하고 오케스트레이터에 제어를 반환한다.
오케스트레이터가 승인 상태와 완료조건을 확인한 뒤 Phase 1로 진행한다.
