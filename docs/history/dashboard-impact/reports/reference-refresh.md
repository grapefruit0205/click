# Dashboard reference refresh — 2026-09-07

사용자가 제공한 Click 대시보드 이미지를 기존 frontend에 적용했다. 공개 버전은 0.90.0이다. 소스와 Antigravity 배포 파일을 동기화했으며, 설치된 Codex 플러그인 캐시는 갱신하지 않았다.

## 적용 결과

- 민트·청록 배경, 흰 카드, 남색 제목, 사이드바와 현재 작업 표시줄.
- 첫 화면의 주요 지표는 전체 작업의 순작업시간과 토큰 절감률이다. 비교 자료가 없으면 미측정으로 표시한다. 토큰 절대량은 표시하거나 공유하지 않는다.
- 테스트 실행의 회피 비용 추정은 별도 내역으로 표시하며, 관리비용을 차감한 전체 작업 순절감으로 해석하지 않는다.
- 실행·재사용·결과 확보 요약, 묶음별 실제 상태, 동일 시간 축 비교를 유지했다.
- 재사용 근거 표의 필터와 상세 펼치기, 실제 배치 이력 선택, 키보드 조작, 공유 리포트를 연결했다.
- 한국어·영어·중국어와 모바일 레이아웃을 유지했다. 불리한 토큰·작업시간 변화는 증가·느려짐으로 표시한다.
- 공개 비교 파일의 실제 Unix timestamp를 표본 수 상한으로 잘못 거부하던 frontend 검증을 수정했다. 음수·소수·문자열·범위 밖 timestamp와 과도한 표본 수는 계속 거부한다.

## 화면 검수

[최종 데스크톱 화면](../screenshots/reference-refresh/reference-desktop-ko.jpg)은 보관된 `real-hook-projection.v7.json`의 실제 격리 Evidence fixture 실행 기록을 사용한다. 현재 저장소의 성능 측정값은 아니다. 전체 작업 비교는 미측정 상태로 렌더링했으며, 참고 이미지의 성과 수치를 주입하지 않았다.

실제 CSS viewport는 데스크톱 1439×900 및 1586×992, 모바일 390×844에서 확인했다. 세 언어에서 문서 전체의 가로 넘침이 없음을 확인했고, 모바일 표는 별도 스크롤 영역이다. 원본 브라우저 캡처는 데스크톱 1429×893 / 1576×975, 모바일 379×821 JPEG다. 브라우저가 반환한 크기를 그대로 보관했다.

브라우저에서 재사용 필터 → 해당 묶음 → 근거 펼치기, 언어 전환, Enter 키로 묶음 선택을 확인했다. 브라우저 오류 로그는 비어 있었다. 검수용 서버는 종료했다.

## 검증

`click-gate verify`에 소스의 절대 workdir와 아래 다섯 검사를 선언해 실행했다. 작업 중인 shard manifest가 커밋본과 달라 각 원래 명령으로 fallback했다. 이번 최종 검증에서 실제 재사용은 0개다.

| 검사 | 실행 | 통과 | 제외 | 시간 |
| --- | ---: | ---: | ---: | ---: |
| Dashboard / projection / task efficiency | 61 | 61 | 0 | 16.383초 |
| Automatic sharding init/status/refresh 및 기존 경계 | 74 | 72 | 2 | 103.972초 |
| Shard reuse 필수 회귀 | 8 | 8 | 0 | 32.785초 |
| Distribution / repository policy | 45 | 45 | 0 | 1.698초 |
| git diff --check | 통과 | — | — | — |

총 188개 중 186개 통과, 기존 native authoritative profile 환경 제약에 따른 2개 제외, 실패 0개다. 필수 sharding/reuse 명령은 `docs/agent-efficiency/evidence/phase-0-checks.json`과 동일하다. Dashboard 검사는 `tests.test_click_efficiency`, `tests.test_click_shadow_dashboard`, `tests.test_click_dashboard_projection`, `tests.test_task_efficiency`를 실행했다.

완료 receipt export는 누적 세션의 증거가 stale/remaining으로 남아 있어 차단됐다. 위 실행 결과를 현재 revision의 완료 receipt로 주장하지 않는다. Evidence 모드의 실행 권한은 host이며 Click 계약 승인을 만들지 않았다. 이 문서는 관찰한 결과의 기록이며 실행·재사용 권한을 제공하지 않는다.

## 언어 선택 위치 조정

추가 요청에 따라 언어 선택을 사이드바 아래에서 상단 도구 영역으로 옮겼다. 지구본·언어 라벨·현재 언어를 흰 배경과 청록 테두리 안에 표시한다. 모바일에서는 제목 아래에 전체 너비로 표시한다. 기존 JavaScript, 검증 및 재사용 로직은 변경하지 않았다.

기존 언어·dashboard JavaScript 검사 7개가 통과했다. 브라우저에서 1586×992 데스크톱과 390×844 모바일 표시, 세 언어 전환, 가로 넘침 없음을 확인했다. 소스와 배포본을 동기화하고 같은 미리보기 서버를 갱신했다. 화면은 `language-top-desktop.jpg`와 `language-top-mobile.jpg`에 보관했다.
