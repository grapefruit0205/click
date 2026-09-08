# Phase 1 — 실제 테스트 탐색과 의존성 후보 수집

## 시작 조건
- Phase 0 완료
- docs/auto-sharding-plan.md 존재
- Phase 0~2 범위를 포함하는 Guarded 계약이 유효
- 사용자 변경 보존

조건이 충족되지 않으면 오케스트레이터에 blocker를 반환한다.

## 목표
설정이 없는 대상 저장소에서:
- 전체 검증 명령
- 실제 unittest 테스트 inventory
- 테스트별 프로젝트 파일/모듈 관계
- 의존성 후보
- unknown dependency

를 구조화해 수집한다.

Click 자신의 테스트를 대상 프로젝트의 inventory로 사용하지 않는다.

## 프로젝트 탐색
Git root, 실행 cwd, interpreter, 프로젝트 설정, 이미 사용 중인 검증 명령을 확인한다.
지원하는 명시적 argv만 해석한다.

금지:
- shell 문자열을 임의 평가
- package 설정을 실행해 명령 추측
- 사용자 동의 없이 dependency 설치
- 네트워크 접근을 전제로 한 탐색
- 부모 저장소/다른 프로젝트 증거 혼합

기존 명령이 모호하면 후보와 이유를 표시하고 selection-required 상태로 남긴다.

## unittest collection
표준 unittest adapter로 실제 TestLoader 결과를 구조화한다.
프로젝트 import가 필요한 collection은 read-only 분석으로 위장하지 말고 승인된 bootstrap 실행 경로를 이용한다.

수집 정보:
- test ID
- 모듈
- 파일
- loader/discovery 조건
- collection 오류
- runtime 버전
- cwd / top-level / pattern / filter
- inventory digest

지원하지 않는 옵션은 조용히 무시하지 말고 명시적으로 거부한다.

다음 사례를 별도 판정:
- load_tests
- custom loader
- 동적 생성
- import failure
- unstable test ID
- 0 tests
- duplicate ID
- collection 중 파일 변경
- timeout / excessive output

collection 성공은 test PASS나 baseline success가 아니다.

## 의존성 후보
정적 import와 저장소 내부의 추적 가능한 transitive import를 후보로 분석한다.
공통 test helper, config, data file 후보도 수집한다.

다음은 unknown으로 남긴다.
- dynamic import
- runtime-generated path
- subprocess
- external service
- DB
- time/random
- 미추적 IPC
- 해석 불가능한 file access

“관찰되지 않음”을 “의존하지 않음”으로 바꾸지 않는다.

공통 의존성은 global 또는 multi-shard invalidation 후보로 표시한다.
모든 테스트를 하나의 샤드로 강제 합치지 않는다.

이 그래프는 구성안 생성과 설명용 candidate 정보다.
Phase 3 이전에는 실제 변경 후 스킵 권한으로 사용하지 않는다.

## 데이터 저장
원시 환경값, 토큰, 민감 파일 내용은 저장하지 않는다.
임시 결과는 관리되는 임시 영역에 둔다.
collector가 프로젝트 파일을 변경하면 감지·보고하고 결과를 유효한 분석으로 채택하지 않는다.
사용자 변경을 자동 revert하지 않는다.

## 검증
최소 두 개의 독립 unittest fixture/project를 사용한다.
다음을 포함한다.
- 서로 다른 디렉터리 구조
- nested package
- filter
- import error
- custom loader
- 0 tests
- non-Git / unsupported command
- path with spaces

기대 결과는 “정확한 테스트 inventory + dependency candidates”다.
“reuse-ready”로 승격하지 않는다.

## 완료조건
- 실제 inventory가 구조화되어 있다.
- unknown dependency가 보존된다.
- 프로젝트 경계가 지켜진다.
- collection 실패가 성공으로 둔갑하지 않는다.
- Phase 2가 사용할 안정적인 입력 형식이 존재한다.

완료되면 progress를 갱신하고 오케스트레이터에 제어를 반환한다.
승인 범위가 여전히 유효하면 오케스트레이터가 Phase 2를 자동 시작한다.
