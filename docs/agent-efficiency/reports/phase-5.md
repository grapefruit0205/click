# Phase 5 — 절감 시간·토큰 절감률 대시보드 완료

별도 화면을 만들지 않고 기존 projection/viewer/export를 확장했다. 첫 화면에는 현재 검증의 묶음 결과 바로 아래에 정확한 제목 `절감 시간`, `토큰 절감률` 두 카드와 별도 `전체 작업 효과` 상태가 함께 보인다.

## 첫 화면과 상세

- 절감 시간은 기존 `revalidation_savings.omitted_test_execution_ms`와 coverage를 그대로 사용한다. 실제 적용 reuse 0, 추정, 부분 추정, 미측정, 진행, 실패와 취소를 구분하며 이번 검증의 테스트 실행 기준임을 가까이 표시한다.
- 토큰 절감률과 전체 작업 효과는 Phase 4 public v1의 같은 선택 presentation만 사용한다. task 비율은 빨라짐/변화 없음/느려짐/미측정, token 비율은 감소/변화 없음/증가/미측정이다. 0이 아닌 0.01% 미만 변화는 방향과 `<0.01%`를 표시한다.
- B0→B2는 개선 전 Click 대비, N→B2는 Click 미사용 대비로 표시한다. scenario, 첫 사용/준비된 반복 사용, Evidence/Guarded가 다른 자료를 합치지 않으며 presentation이 여러 개면 선택 전까지 미측정이다.
- 느려진 표본, 실패·취소·미완료와 사용자 개입 증가를 첫 화면 상태 영역에 표시한다. 상세에는 task delta, 표본, intervention, tool/failure-follow-up/repair/model-round-trip과 활동·미분류 구간을 표시하며 활동 시간을 전체 task로 더하지 않는다.
- 화면은 900px 이하에서 sidebar를 접고 760px 이하에서 대표 카드를 세로로 쌓는다. 기존 reduced-motion 규칙과 keyboard button/select/anchor 요소를 유지한다.

## 공개 경계와 호환성

projection은 v8이며 빈 실제 평가에서는 token/task 상태가 미측정인 public 객체를 제공한다. v4/v5/v6/v7 projection 읽기와 기존 paired v2/workflow v4 importer를 유지한다.

Phase 4 importer는 top-level과 presentation의 정확한 allowlist, 상태/ratio/count 일관성, 중복 scope와 raw usage key를 검사한다. 로컬 입력 파일명은 현재 화면 상세에만 보이고 공유본에서 제외한다. 공유 report v5와 독립 HTML은 화면과 같은 presentation을 쓰며 raw usage와 token 절대량, 로그·코드·명령·경로·환경·계약·인증정보를 싣지 않는다. 모든 사용자 문자열은 `textContent`로 렌더링한다. import, 언어 전환, 새로고침과 export는 검증이나 모델 호출을 시작하지 않는다.

기존 한국어, English, 简体中文 선택과 브라우저 저장을 유지했고 새 상태·근거·복사·HTML 문구를 세 언어에 추가했다. Observer는 계속 기본 off이며 dashboard와 exact/policy reuse에 필요하지 않다.

## 검증

대시보드 관련 `tests.test_click_efficiency`, `tests.test_click_dashboard_projection`, `tests.test_click_shadow_dashboard`, `tests.test_task_efficiency` 60개가 통과했다. Phase 2~5 전체 집중 묶음은 81개가 통과했다. 양수/0/음수/미세 비율, null과 불리한 상태, 여러 scope 선택, 구형 projection, share/HTML 금지 key와 ko/en/zh-CN를 확인했다. 실제 runner dashboard/export 테스트도 통과했으며 authorization stale 문구는 거부를 기대하는 기존 negative fixture의 stderr다.

실제 Hook projection과 v8 미측정 task 객체를 사용해 1440×900과 390×844 PNG를 캡처하고 직접 확인했다. 두 화면에서 첫 상태, 두 대표 카드의 연결, 세로 mobile 흐름, 텍스트 줄바꿈과 overflow가 정상이다. semantic button/link/select와 `:focus-visible`, `prefers-reduced-motion: reduce` 규칙을 코드 회귀로 확인했다. 캡처와 digest는 `evidence/phase-5-test-results.json`에 있다.

실제 Phase 4 pair가 0개이므로 현재 카드의 token/task 기본 상태는 미측정이다. 예시 비율을 기본값이나 실제 결과로 넣지 않았다. 전체 automatic sharding/reuse 회귀 결과는 최종 보고서에 기록한다.
