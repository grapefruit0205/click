# Phase 3 — 신뢰 가능한 관찰과 실제 재사용 권한 연결

## 시작 조건
- Phase 0~2 완료
- Phase 3~5 범위를 명시적으로 포함하는 별도 Guarded 계약 승인
- 자동 생성 proposal은 아직 authority가 아님

승인이 없으면 구현하지 않는다.

## 목표
자동 생성된 dependency declaration과 실제 성공 실행의 신뢰 가능한 관찰을
기존 계약 간 successor requalification 경로에 연결한다.

새 Guarded 계약 B에서 코드가 변경되었어도
현재 재사용 조건을 충족하는 샤드만 실제로 스킵할 수 있게 한다.

## 기존 경계 우선
기존 제품에 authoritative observation supplier가 실제 존재한다면 우선 활용한다.
없다면 Phase 0에서 지정한 Linux backend 1종에 한해 명시적으로 enable하는 authoritative observation 경로를 구현한다.

mock-only 또는 test-only positive path로 완료하지 않는다.

## Shadow와 분리
Shadow:
- authoritative=false
- reuse_authorized=false

를 유지한다.

금지:
- Shadow flag 뒤집기
- Shadow complete 복사
- 정적 dependency graph를 authority로 승격
- 대시보드/JSON 보고서를 재사용 증거로 수용

collector implementation을 공유할 수는 있지만
authoritative run은 별도의 검증된 실행에서 증거를 생성해야 한다.

## 입력 완전성
지원 backend가 최소 다음을 다룰 수 있는지 검증한다.
- file reads
- metadata
- directory membership
- missing path lookup
- symlink
- executable/interpreter
- child processes
- source/config/data inputs
- file create/delete/rename 영향
- import search path changes

인터프리터/stdlib/external package를 단순히 “시스템 경로”라는 이유로 제외하지 않는다.
내용 identity 또는 검증 가능한 environment identity에 바인딩한다.

다음은 지원 모델 밖이면 재사용 불가:
- network
- DB
- time/random
- untracked IPC
- incomplete child process observation
- truncated/unknown event stream

complete를 발급할 수 없으면 재사용을 막고 실제 검증으로 돌아간다.
관찰 실패가 test PASS/FAIL 의미를 바꾸지 않는다.
관찰 실패 후 데이터를 얻기 위해 테스트를 몰래 다시 실행하지 않는다.

## 증거 바인딩
실제 runner가 발급하는 증거는 최소 다음에 결속한다.
- original execution
- shard
- exact argv
- cwd
- repo/workspace
- environment identity
- executable/interpreter
- policy/config digest
- observer backend/version
- input snapshot

호출자 제공 임의 JSON의 complete를 authority로 믿지 않는다.

초안/profiling 결과를 소급해서 active evidence로 만들지 않는다.

## 계약 간 재판정
계약 A의 성공은 후보로만 가져온다.
계약 B는 별도 승인되어야 한다.

B에서:
- 동일 검증
- 동일/허용된 입력 조건
- 현재 dependency/observation
- policy validity
를 재판정한다.

적격 샤드만 reuse.
신규 검증, 관련 입력 변경, 환경 변경, 불완전 observation은 execute.

승인, runner token, completion state, unfinished work는 A에서 B로 승계하지 않는다.

실행 직전 상태 변경을 재확인한다.
불확실하면 재실행한다.

## 검증
실제 지원 backend 양성 경로가 필요하다.

최소:
- 서로 독립적인 두 샤드
- 무관한 코드 변경 → 일부 reuse
- 관련 소스 변경 → 재실행
- shared config 변경 → 재실행
- new import candidate → 재실행/불완전 처리
- missing path 생성
- directory membership 변화
- env 변경
- event loss
- observer unavailable
- forged evidence
- Shadow contamination

실제 backend 양성 path가 검증되지 않으면 전체 제품 목표 완료로 처리하지 않는다.

## 완료조건
- 새 contract_id에서 실제 부분 reuse가 가능하다.
- authority와 candidate가 분리된다.
- 불완전 관찰은 fail-safe로 재실행된다.
- 승인/권한은 계약 간 승계되지 않는다.

완료되면 progress를 갱신하고 오케스트레이터에 제어를 반환한다.
Phase 3~5 승인 범위가 유효하면 Phase 4로 자동 진행한다.
