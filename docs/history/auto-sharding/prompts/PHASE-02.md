# Phase 2 — 샤드·의존성 설정안 자동 생성

## 시작 조건
- Phase 1 완료
- 실제 테스트 inventory 존재
- Phase 0~2 승인 계약 유효

## 목표
사용자가 .click/evidence-shards.json 또는 dependencies 파일을 직접 작성하지 않아도
실제 inventory와 dependency candidates를 기반으로 검토 가능한 설정안을 자동 생성한다.

여기서 만드는 것은 활성 권한이 아니라 proposal이다.

## 샤딩 원칙
첫 버전의 최소 분할 단위는 테스트 모듈이다.
class/module fixture를 임의로 분해하지 않는다.

샤드 생성 기준:
- project/module boundary
- dependency similarity
- shared fixture
- common config/data
- 기존 실행시간 표본이 있다면 보조적 balancing

실행시간 표본이 없으면 시간을 지어내지 않는다.

공유 전역 상태나 독립 실행 가능성이 불명확하면:
- 함께 묶거나
- 자동 분할을 거절한다.

샤드 수, 명령 길이, 분석량에 상한을 둔다.
매우 짧은 검증을 무조건 잘게 쪼개지 않는다.

같은 입력은 같은:
- shard plan
- shard ID
- child argv
를 생성해야 한다.

코드 한 줄 변경만으로 전체 shard layout을 재배치하지 않는다.

## 부모/자식 범위 일치
parent full command의 실제 collected test IDs와
생성된 child commands의 collected test IDs를 대조한다.

필수 조건:
multiset(children) == parent inventory

금지:
- 누락
- 의도하지 않은 중복
- cwd 변화
- import path 왜곡
- filter 손실
- unsupported option silent drop

파일 covers만 일치한다고 충분하다고 보지 않는다.

collection equivalence는 필요조건일 뿐
fixture/order/global state의 의미적 동등성 증명은 아니다.
불명확하면 auto-sharding unsupported 또는 review-required로 남긴다.

## 생성 파일
기존 schema와 호환되는 proposal을 생성한다.
최소:
- .click/evidence-shards.json proposal
- .click/evidence-dependencies.json proposal

각 child argv에 dependency candidate를 연결한다.

금지:
- 기존 커밋된 사용자 설정 덮어쓰기
- 기존 dependency 범위 축소
- .click/evidence-reuse.json을 근거 없이 자동 확장
- 정책 파일/테스트 선택 설정/runner 변경을 safe change로 자동 선언

proposal은 관리되는 proposal 영역에 저장한다.
생성 즉시 활성 정책으로 사용하지 않는다.
자동 git add/commit/push 하지 않는다.

필요 메타데이터:
- adapter version
- inventory digest
- generation reason
- review state
- source project identity

이 메타데이터 자체는 authority가 아니다.

## 검증
최소 두 독립 프로젝트에서 수작업 JSON 없이 proposal을 생성한다.

검증:
- 누락
- 중복
- invalid child argv
- shared fixture
- new/deleted test
- deterministic regeneration
- existing config preservation
- unsupported split

## 완료조건
- 사용자가 직접 JSON을 쓰지 않고 proposal을 얻는다.
- parent/child inventory가 검증된다.
- proposal-ready와 reuse-ready가 분리된다.
- 기존 사용자 설정을 보존한다.

완료되면 progress를 갱신한다.

다음 Phase 3은 실제 변경 후 스킵 권한 경계를 다룬다.
Phase 3용 별도 Guarded 계약이 아직 승인되지 않았다면:
1. Phase 3~5의 authoritative observation / activation / E2E 범위를 포함하는 계약을 stage한다.
2. 실제 contract_id와 Hook 설명을 사용자에게 제시한다.
3. approval-required 상태로 전환하고 오케스트레이터에 제어를 반환한다.

이미 유효한 Phase 3~5 계약이 있으면 오케스트레이터가 Phase 3을 자동 시작한다.
