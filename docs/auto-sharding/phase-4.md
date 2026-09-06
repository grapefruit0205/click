# Phase 4 — 최초 설정·활성화·기준 실행·대시보드 연결

## 시작 조건
- Phase 3 완료
- Phase 3~5 계약 유효

## 목표
일반 사용자가 JSON을 직접 작성하지 않고
프로젝트 초기 설정부터 reuse-ready 상태까지 도달하는 최소 UX를 만든다.

설치 사실만으로 사용자 프로젝트 코드를 실행하거나 정책을 변경하지 않는다.

## 진입점
기존 CLI/Skill 구조에 맞춰 init / status / refresh에 해당하는 최소 인터페이스를 추가한다.
정확한 명령 이름은 기존 구조와 일관되게 정한다.

설정이 없으면:
1. 안전한 metadata 확인
2. 초기 설정 제안
3. 승인된 collection/profiling
4. shard/dependency proposal 생성
5. 사람이 이해할 수 있는 diff/범위/제약 표시
6. 승인된 설정 적용
7. commit-required 상태
8. commit 확인
9. baseline-required
10. full vs sharded bootstrap validation
11. valid baseline evidence
12. reuse-ready

동일 질문을 반복하지 않는다.
승인 경계에서는 상태를 저장하고 종료한 뒤 승인 후 재개한다.
자동 승인/배경 실행을 가장하지 않는다.

## Git 경계
미커밋 proposal을 active policy로 사용하지 않는다.

금지:
- 자동 git add
- 자동 commit
- 자동 push
- 기존 index 수정

commit-required 상태에서는 정확한 대상 파일과 필요한 사용자 행동을 안내한다.
커밋 전에는 변경 후 reuse를 활성화하지 않는다.

## baseline
초기 analysis/profiling과 active configuration 이후의 baseline success를 분리한다.
baseline 전에는 reuse-ready로 표시하지 않는다.

parent full command와 generated child commands의:
- collected inventory
- 지원 조건 내 실행 결과
를 대조한다.

불일치하면 ready가 아니다.
sharding은 가능하지만 reuse authority가 없다면:
sharding-ready / reuse-unavailable
로 표시한다.

첫 baseline 실행 비용은 savings로 계산하지 않는다.

## 갱신/복구
init/status/refresh는 멱등적이어야 한다.

사용자가 편집한 설정은 자동 overwrite하지 않는다.
일반 코드 내용 변경마다 shard plan을 재생성하지 않는다.

다음 변경은 review-required를 유발할 수 있다.
- tests added/deleted/renamed
- full command changed
- runner changed
- discovery condition changed
- sharding assumptions changed

검증 실행 도중 policy 파일을 자동 수정하지 않는다.
유효 plan이 없으면 권한이 있는 원래 full validation으로 fallback한다.

중단/동시 초기화/partial write를 안전하게 처리한다.
사용자 코드를 자동 revert하지 않는다.

## 대시보드
기존 savings projection을 우선 재사용한다.

첫 화면의 대표 순서:
1. 생략한 테스트 실행시간
2. 동일 샤드 전체 실행 예상 vs 이번 부분 실행
3. 실행/재사용 샤드 수
4. 추정/실측 구분
5. 재사용 근거

상세:
- initial setup cost
- observation cost
- Click processing
- request wall time
- comparison net savings/loss

추정치를 전체 대기시간의 순절감으로 표현하지 않는다.
실제 total wait이 증가한 비교 실측은 숨기지 않는다.

## 완료조건
설정 없는 지원 프로젝트에서:
- 사용자가 JSON을 직접 작성하지 않고 proposal 생성
- 승인된 적용
- commit 확인
- baseline
- reuse-ready

까지 진행할 수 있다.

미지원/승인대기/커밋대기/관찰불가/실패 상태도 정확히 표시한다.

완료되면 progress를 갱신하고 Phase 5로 자동 진행한다.
