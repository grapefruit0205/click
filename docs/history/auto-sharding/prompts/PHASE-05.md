# Phase 5 — 설정 없는 별도 프로젝트 종단간 검증

## 시작 조건
- Phase 0~4 완료
- Phase 3~5 승인 계약 유효

## 목표
Click 개발자가 미리 JSON을 써둔 fixture가 아니라
설정이 전혀 없는 별도 프로젝트에서 실제 제품 초기화 경로만으로
부분 재사용과 재검증 절감 가시화까지 재현한다.

## E2E 대상
최소 두 개의 독립 unittest 프로젝트를 사용한다.
서로 다른:
- directory layout
- module names
- test counts
- shared dependencies
를 사용한다.

auth/alpha/beta 같은 특정 fixture 이름이나 경로를 제품 코드에 하드코딩하지 않는다.
한 사례 이상은 독립적인 library code + tests 구조를 사용한다.

## E2E 흐름
프로젝트에는 일반 source/test만 준비하고 Click 설정은 두지 않는다.

실제 제품 경로로:
1. test collection
2. auto shard/dependency proposal
3. 별도 승인 턴을 거친 설정 적용
4. 설정 commit
5. full/sharded baseline validation
6. authoritative observation
7. 계약 A 검증 완료
8. 새 contract_id의 계약 B 별도 승인
9. 한 모듈 실제 코드 변경
10. B에서 원래 전체 검증 요구
11. 관련 샤드 실행
12. 적격 샤드 실제 reuse
13. 원본 contract/execution/time provenance 확인
14. 같은 최종 코드 상태의 full validation audit

테스트 코드가 정답 shard JSON이나 reuse policy를 미리 작성하지 않는다.
제품 generator가 만들어야 한다.

fixture 내부의 승인/commit simulation은 fixture repo 안에서만 사용한다.
현재 개발 세션의 실제 사용자 Git index를 건드리지 않는다.

배포 패키지/설치 경로에서도 adapter와 필수 파일이 포함되는지 확인한다.
개발 source tree에서만 우연히 동작하는 결과를 E2E 성공으로 보지 않는다.

## 안전 회귀
다음을 검증한다.
- related input change
- shared config change
- lockfile/runtime identity change
- runner change
- new test/file
- delete/rename
- missing path creation
- symlink
- dynamic/external input
- failed/cancelled validation
- collection error
- observation loss
- permission missing
- uncommitted/tampered config
- concurrent change
- forged receipt

미지원 환경에서는 reuse를 주장하지 않고 full validation을 유지한다.
원래 full command 자체에 실행 권한이 없으면 fallback을 권한 우회로 사용하지 않는다.

계약 간:
- approval
- runner token
- unfinished work
- completion state
는 승계되지 않는다.

Hook/Guarded 검사를 비활성화해서 시험을 통과시키지 않는다.

## 절감 비교
동일 최종 코드, 검증 범위, cache 조건, 병렬 조건에서:
- full revalidation
- partial sharded revalidation
을 비교한다.

단계별 기록:
- full test execution time
- partial test execution time
- executed shards
- reused shards
- avoided test execution estimate
- time baseline coverage
- test-count reuse rate
- shard reuse rate
- time reduction rate
- Click request wall time
- net comparison result

초기 설정과 Click 관리비용은 상세로 분리한다.
음수 net result도 보존한다.

인공 sleep/부하 확대를 실제 사용자 성과로 제시하지 않는다.
양수 자체를 완료조건으로 만들지 않는다.

대표 메시지는:
“전체 재검증을 다시 수행하지 않고 적격 샤드를 재사용해 실제로 얼마의 테스트 실행을 피했는가”
여야 한다.

## 문서/공유
지원 Python/OS/backend, 미지원 조건, 첫 사용 흐름, 승인/commit 필요성, 실패 복구를 문서화한다.

금지 홍보:
“모든 프로젝트에 설치만 하면 완전 자동”
“모든 환경에서 빨라짐”
“과구현/과추론 완전 차단”

허용 범위에 맞는 설명:
“지원 프로젝트에서 테스트 묶음과 의존성 설정을 자동 준비하고,
기준 검증 후 유효한 샤드만 재사용한다.”

대시보드/host summary/HTML/JSON의 숫자와 라벨이 일치하는지 확인한다.

## 최종 완료조건
직접 JSON을 작성하지 않은 새 프로젝트에서:
- auto setup
- baseline
- 새 Guarded 계약
- 실제 코드 변경
- 실제 부분 reuse
- avoided revalidation time 표시
를 재현한다.

설정 생성만 성공하거나 모든 검증을 항상 실행하면 전체 목표 완료가 아니다.

마지막 변경 이후 영향받는 회귀검증의 유효한 증거를 확보한다.
실행 명령, 결과, 지원 환경, 양성 사례, 음수/실패 사례, 미검증 항목을 보고한다.

핵심 시험이 막히면 partial completion + blocker로 종료한다.
자동 배포/게시/commit/push하지 않는다.
임시 프로세스와 파일을 정리한다.

완료되면 최종 감사로 이동한다.
