# Click 0.90.0 대시보드 개선 결과

참고 이미지의 민트·청록색 반투명 카드와 왼쪽 탐색 구조를 기존 대시보드에 적용했다. 첫 화면에서 **전체 N개 중 X개 실행·U개 재사용**과 적합한 시간 근거가 있는 회피 비용 추정을 함께 보여준다. 시간 근거가 없으면 묶음 수를 크게 표시한다.

기준 체크아웃은 `3b7f8e6ec260729b4ba8a13484ea4c33dce7baeb`, 시작 상태는 clean이었다. 플러그인 버전은 **0.90.0을 유지**했다. 디렉터리명의 `0-81-0`은 현재 manifest 버전이 아니다. Observer off 상태에서도 검증·기존 재사용·계측·대시보드가 동작한다.

## 구현

- 같은 위치의 묶음 블록, 같은 0축의 전체 예상·실제 실행·회피 추정 막대, 실패·진행·미측정 등 상태별 표현.
- Hero→재사용 필터→묶음 상세 연결, 관찰된 최근 이력, 과거 선택과 현재 작업의 명확한 구분, 중복 없는 완료 요청 누적.
- fixture와 현재 요청의 범위를 구분하는 실측 비교, 음수·제외·실패·준비 비용 보존, 요약 복사와 같은 수치의 JSON/독립 HTML.
- canonical 집계와 기존 표본 선택 유지. 읽기 전용 projection v7, 공유 report v4. projection v4~v6와 paired v2/workflow v4 importer 호환.

## 변경 파일

| 파일 | 역할 |
| --- | --- |
| `hooks/click_incremental.py` | 최종 완료 조건, 부분 추정 문구, 완료 이력 누적과 validator |
| `hooks/click_dashboard_projection.py` | projection v7, 완료 누적 필드, 이전 버전 호환 |
| `hooks/click_shadow_dashboard.py` | 레이아웃·상태·필터·이력·비교·복사·공유 |
| `dist/antigravity/hooks/`의 위 세 파일 | 기존 생성기로 동기화한 배포 사본 |
| `tests/test_click_efficiency.py` | 지표/DOM 회귀 및 실제 Hook→projection→export 검증 |
| `tests/test_click_dashboard_projection.py` | v7, 이전 버전 및 누적 필드 검증 |
| `tests/test_click_shadow_dashboard.py` | 새 UI와 기존 HTTP 경계 검증 |
| `tests/test_incremental_benchmark.py` | 실제 workflow 출력의 importer 호환성 |
| `README.md`, `VERIFICATION_EFFICIENCY.md` | 현재 UI·형식·지표·Observer 설명 |
| `docs/dashboard-impact/` | 원문·계획·계약·단계 보고서·화면·검증 자료 |

## 검증 결과

아래 명령은 저장소 루트를 명시한 `click-gate verify`의 개별 argv로 실행했다. 서로 다른 관련 테스트는 총 **111개 통과**했다.

| Evidence ID | 명령 | 결과 |
| --- | --- | --- |
| `E_IMPACT_METRICS_UI` | `python3 -m unittest tests.test_click_efficiency tests.test_click_dashboard_projection -q` | 35개 통과 |
| `E_IMPACT_VIEWER_HTTP` | `python3 -m unittest tests.test_click_shadow_dashboard tests.test_click_incremental -q` | 20개 통과 |
| `E_IMPACT_BENCHMARK_COMPAT` | `python3 -m unittest tests.test_incremental_benchmark -q` | 11개 통과 |
| `E_IMPACT_DISTRIBUTION` | `python3 -m unittest tests.test_distribution_validation tests.test_repository_policy -q` | 45개 통과 |

최종 코드·문서·캡처 보관 상태의 완료 증빙을 맞추기 위해 위 111개 관련 테스트와 `git diff --check`를 함께 확인했다. 저장소 전체 검증으로 범위를 넓히지는 않았다.

실제 실행 연결 검증의 원본 수치는 N=2, X=1, U=1이다. 실행 명령 구간 E=41.830133ms, 재사용 원본 비용 A=57.129845ms, F=98.959978ms로 projection과 export가 일치한다. **A/F는 기록 기반 추정이며 전체 요청 대기시간의 실측 감소가 아니다.** [실제 fixture 결과](../evidence/real-hook-report.v4.json)를 보관했다.

별도 Guarded workflow는 실제 Hook/one-use runner로 3개 구성을 8단계에 걸쳐 검증했다. 32개 비교 쌍 중 유효 21개, 그중 음수 15개를 그대로 보존했다. 반복 1회·워밍업 0회의 소규모 fixture이며 사용자 저장소의 성능 향상을 입증하지 않는다. [원시 workflow 결과](../evidence/guarded-workflow.v4.json)에 준비·전환·감사·예상 실패 비용도 남겼다.

## 화면 검수

| 항목 | 확인 내용 |
| --- | --- |
| 데스크톱 1440×900 | 첫 화면의 대표 시간, 추정 표시, N/X/U 및 전후 막대 |
| 모바일 390×844 | 정상·미측정·실패 상태, 가로 넘침과 핵심 수치 잘림 수정 |
| 탐색 | Hero의 재사용 필터, 키보드 Enter 실행 필터, 상세 근거 연결 |
| 갱신·이력 | 새 작업에 이전 성과 없음, 명시적 과거 선택, 새로고침 후 선택·누적 유지 |
| 비교·공유 | 실제 workflow v4 파일 가져오기, 음수 fixture 안내, 비용 상세, 복사·HTML export 완료 상태 |
| 접근성 | 텍스트·상태 기호·패턴, 키보드 조작, Chromium CSSOM의 reduced-motion 규칙 |

[데스크톱](../screenshots/desktop.jpg), [모바일](../screenshots/mobile.jpg), [미측정](../screenshots/mobile-untimed.jpg), [실패](../screenshots/mobile-failed.jpg). 화면 캡처의 12/3/9와 90초는 **합성 예시 데이터**이며 실제 검증 기록과 분리했다.

## 호환성과 남은 제한

- 승인·runner·검사 선택·재사용 권한 경로는 변경하지 않았다. 기존 HTTP loopback, 세션 토큰, Host 검증, CSP, no-store 및 no-CORS 테스트가 통과했다. 공유본에는 경로·원시 명령·환경 값·토큰·계약 원문을 추가하지 않는다.
- 과거 배치 공유는 현재 작업의 task/controls/shadow를 포함하지 않는다. 원본 배치의 출처는 유지한다. 사용자 라벨은 허용 필드와 문자 규칙을 거치고 HTML로 실행하지 않는다.
- OS reduced-motion 설정을 직접 전환하거나 다른 OS에서 브라우저를 실행하지는 않았다. CSS 규칙과 Chromium 파싱은 확인했다.
- in-app browser에서 다운로드 이벤트가 반환되지 않아 다운로드된 HTML 파일을 다시 열어 시각 검수하지 못했다. HTML 내용 일치·외부 script/asset 부재는 테스트로 확인했고 브라우저 export 동작도 완료 상태를 확인했다.
- 저장소 전체 테스트를 다시 실행하지 않았다. 변경 관련 회귀와 실제 실행 연결을 검증했으며 성능 개선 수치를 통과 조건으로 요구하지 않았다.
- 구현은 소스 체크아웃에 있다. 설치된 플러그인 캐시 교체·재설치, commit/push, 게시·배포는 수행하지 않았다. 테스트용 로컬 서버는 종료했다.

이 작업은 Click Evidence 모드에서 호스트 권한으로 수행했다. `approval_bound: false`, `execution_authority: host`이며 Guarded 승인을 받았다는 의미가 아니다. 위 실제 Guarded workflow는 테스트 fixture 내부의 별도 승인 경로다.

최종 111개 테스트와 diff 검사 통과 후 `click-gate receipt export`를 시도했지만, 런타임이 `Click cannot export a receipt while a capability claim is active.`를 반환했다. canonical 완료 receipt는 생성하지 못했다. 테스트 통과 결과와 receipt 생성 성공은 구분하며, 이 제한을 해결하려고 권한 상태나 ledger를 수정하지 않았다. 이 문서의 Evidence 모드 설명은 canonical receipt를 대신하는 인증이 아니다.
