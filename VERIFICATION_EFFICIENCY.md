# 검증 효율 대시보드

Click의 실제 canonical plan과 runner 결과를 읽기 전용으로 보여줍니다.
화면이 실행 계획을 만들거나 재사용 권한을 판단하지 않습니다.

## 열기와 공유

Click을 사용하는 현재 작업에서 `click-gate dashboard start`로 열고,
`click-gate dashboard status`로 상태를 확인하며, 확인 후 `click-gate dashboard stop`으로 닫습니다.
Observer는 별개이며 `click-gate observer off` 상태에서도 계측·receipt·기존 재사용이 동작합니다.
새 코드가 설치되지 않은 환경의 기존 viewer에는 새 화면이 나타나지 않습니다.

대시보드는 개별 계약이나 Evidence 배치가 아니라 **검증 가능한 host 세션 + 작업 공간**에
속합니다. 한 번 열면 같은 범위에서 `수정 → 검증 → 다음 Evidence 작업 → 수정 → 검증`을
반복해도 같은 URL과 접근 토큰으로 현재 배치와 이전 배치를 계속 읽습니다. 새 Evidence
작업이 이전 Guarded 승인, runner token, 미완료 명령, 완료 상태를 상속한다는 뜻은 아닙니다.
`click-gate cancel`은 실행 권한을 폐기하지만 이미 열린 읽기 전용 화면은 최근 결과를
history-only 상태로 보여줍니다. `dashboard stop`, SessionEnd, 기존 최대 수명 정리는 계속
viewer를 종료합니다. 다른 host 세션이나 작업 공간은 별도 바인딩이므로 연결되지 않습니다.

상단은 선택한 배치의 **검증 묶음** 수입니다. 개별 테스트 케이스 수나 시간 절감률이 아닙니다.
현재 계약의 약속과 승인도 함께 표시하며, 새 계약에 현재 배치가 없으면 이전 배치를
새 계약의 결과로 선택하지 않습니다. 과거 계약에서 재판정한 결과와 같은 계약 재사용은
구분합니다. 원시 계약·검증 ID와 Evidence Map·Shadow는 펼치는 상세입니다.
최근 배치를 선택하면 계획, 실제 시작·완료·통과·실패·중단·미실행, 재사용 적용,
영어 reason code와 한국어 설명, 이전 성공 실행 표본을 확인할 수 있습니다.
각 묶음이 끝나는 즉시 다음 묶음이 시작되기 전에 완료 상태와 실행 구간을 저장하므로,
`A 통과 → B 실행 중`인 순간에도 A는 통과로 보입니다. 이후 취소하더라도 A의 실행 사실은
유지되고, B는 확인한 범위에 따라 중단/결과 미확정, 뒤 묶음은 미실행으로 남습니다.
첫 묶음 실패로 다음 묶음이 시작되지 않았다면 실제 실행 1개 / 미실행 1개입니다.
종료를 확인하지 못한 실행은 미확정으로 남습니다. 재사용 예정은 적용 실적이 아닙니다.
Evidence Map은 최신 배치의 상세 분석이며 과거 배치의 입력 그래프를 추측해 복원하지 않습니다.
표시 제한 48개에 맞춰 실제 렌더링 수와 생략 수를 표시합니다.

화면의 **JSON 내보내기**, **독립형 HTML 내보내기**는 선택한 실제 배치와 가져온 비교 측정을
브라우저 다운로드로 저장합니다. HTML은 외부 스크립트·자산·분석 서비스 없이 열립니다.
공유본은 원시 명령·로그·파일 내용·입력 파일 경로·절대경로·환경 값·runner/access token,
계약 ID·계약 문구를 제외합니다. 로컬 상태에는 제한된 name/promises/범위/불변조건과
최근 8개 계약의 표시용 이력이 남습니다. 제한·필터는 완전한 비밀 탐지기가 아닙니다.
공유본의 검증 묶음은 허용된 표시명·상태·사유·시간·개수만 남기며 source/check digest와
reuse origin 계약을 복사하지 않습니다. 표시 데이터는 실행 권한에 역으로 쓰지 않습니다.
검증을 실행하거나 재사용 권한을 바꾸지 않습니다. 서명이나 발행자 인증이 있는 receipt는 아닙니다.
사용자 문자열은 DOM textContent로 표시하여 HTML로 실행하지 않습니다.

## 실패 진단 출력

검증 요청의 `reporting`을 생략하면 v1 `raw`가 적용되어 기존 stdout/stderr 전달 동작을 유지합니다.
`format: actionable`을 명시하면 원래 명령을 다시 실행하지 않고 그 한 번의 stdout/stderr에서
unittest/pytest 실패 test id, 오류 종류, 안전한 파일/행, 핵심 메시지와 제한된 stack을 정리합니다.

```json
{
  "reporting": {
    "version": 1,
    "format": "actionable",
    "max_bytes": 65536,
    "context": {"enabled": false, "max_files": 2, "max_lines": 120}
  }
}
```

capture는 stdout과 stderr를 동시에 끝까지 drain하되 각각 설정 상한 안의 앞/뒤 구간만 보존합니다.
기본 상한은 stream당 64 KiB, 허용 범위는 4~128 KiB입니다. 전체 관측 bytes, 잘림, decode 대체,
reader 오류와 parser 지원 여부를 함께 표시합니다. 원문은 기존 plugin data 아래 owner-only 파일에
최대 24시간, 128개, 총 8 MiB 범위로 보관합니다. 자격증명 형태는 actionable 요약에서 가립니다.

`context.enabled`는 자동 소스 읽기 허가가 아닙니다. 출력에 나온 workspace 내부의 안전한 파일/행만
별도 inspect 후보로 반환하며 기존 read claim을 거쳐야 합니다. 진단, status와 `next_action`은 안내이고
검증 pass, receipt, reuse, 전체 작업 완료 권한을 만들지 않습니다. `등록된 검사가 현재 유효함`과
`작업 전체가 올바름`도 별도 상태로 표시합니다.

## 제한적 실패 수집

`failure_collection`을 생략하면 v1 `off`이며 첫 실패에서 멈추는 기존 동작입니다. 이미 요청한 source 중
실행 독립성을 사용자가 명시한 경우에만 `bounded`를 선택할 수 있습니다.

```json
{
  "failure_collection": {
    "version": 1,
    "mode": "bounded",
    "independent_sources": ["unitA", "unitB"],
    "max_extra_sources": 1,
    "max_extra_failures": 1,
    "start_window_ms": 30000
  }
}
```

두 개 이상의 제출된 evidence id가 필요합니다. 최대값은 추가 source 3개, 추가 실패 3개, 다음 source
시작 허용창 30초입니다. 시작창은 실행 중인 명령의 timeout이 아닙니다. 같은 source 안에서는 첫 실패로
멈추며, parser가 실제 `test-failure`로 확인한 결과만 다음 독립 source로 이어집니다. ERROR/setup 오류,
취소, claim/state/Git/environment/executable drift, 불명확한 출력은 즉시 중단합니다. source 경계마다
현재 조건을 다시 확인하고 첫 nonzero exit를 보존합니다. automatic sharding이 source identity를
바꾸면 독립성을 추측하지 않고 fail-fast로 돌아갑니다.

수정 후에는 현재 조건의 원래 부모 검증 요청을 다시 제출해야 합니다. 앞서 성공한 sibling도 기존 exact,
dependency 또는 precommitted safe-change 조건을 다시 통과해야 재사용됩니다. 실패 수집 설정 자체는
재사용 권한이나 검사 범위를 만들지 않습니다.

## 전체 task 평가 파일

내부 v1 평가 파일에서 원시 usage와 토큰 절대량을 제외한 공개 파일을 만들려면 다음을 명시적으로 실행합니다.

```sh
python3 benchmarks/task_efficiency.py INTERNAL.json --public-output PUBLIC.json
```

adapter는 동일한 acceptance version과 completion digest로 동등한 완료 조건을 확인합니다.
작업 완료시간은 유효한 task 시작/종료 경계, 토큰 절감률은 완전한 task 범위 usage를 각각 요구하며
독립적으로 계산합니다. usage가 없어도 작업시간은 측정될 수 있으며, 각 항목의 근거가 없으면
그 항목만 미측정입니다. 증분/누적 usage event 의미를 선언하고 event/response identity로 중복을 제거합니다.
cached input과 reasoning이 상위 합계에 포함된 경우 다시 더하지 않습니다. 원시 usage와 토큰 절대량은
내부 파일에만 있고 공개 v1에는 비율, 상태, 비교 범위, 표본·불리한 결과와 허용된 작업 흐름 집계만
남습니다. 현재 저장된 Phase 4 공개 파일은 실제 pair 0개라 미측정입니다.


## 전후 차이와 상태를 읽는 법

첫 화면의 두 카드는 **순작업시간**과 **토큰 절감률**입니다. 순작업시간은 선택한 공개 비교의
전체 작업시간 변화 비율이며, 테스트 실행을 생략한 시간의 추정값이 아닙니다. 유효한 비교가 없는
항목은 미측정으로 표시합니다. **검증 실행 현황** 패널은 전체 N개, 실제 실행 X개, 실제 재사용 U개와
결과 확보 수 및 현재 상태를 별도로 보여줍니다.

**측정 내역**의 **테스트 실행에서 절감**은 실제 재사용에 결속된 적합한 과거 성공 실행시간
합계이며 `약 … [추정]`입니다. 정상 완료했지만 시간 표본이 없거나 일부만 있으면 이 행의 값은
`U / N개`로 바뀌고 시간 근거의 상태와 커버리지를 함께 표시합니다. 일부 표본의 합계는 최소
절약시간이 아닙니다. 실제 재사용 0의 정상 완료는 0ms이고, 진행·실패·취소·미완료는 성과 0과 다릅니다.

토큰 절감률은 `click-task-efficiency-public` v1의 선택한 presentation에 유효한 전체 작업 비율이
있을 때만 표시합니다. 양수는 감소, 정확한 0은 변화 없음, 음수는 증가입니다. 0이 아닌 아주 작은
값은 `<0.01%`와 방향을 표시해 0으로 보이지 않게 합니다. baseline·완전한 task 범위 usage·동등한
완료 조건 중 하나라도 없으면 미측정이며 현재 usage, 실행시간, 생략 묶음 수로 대신 계산하지 않습니다.
화면·접근성 문구·복사·JSON·HTML에는 원시 usage와 전후/절감 토큰 절대량을 넣지 않습니다.

**순작업시간** 카드와 **전체 작업 효과** 상태는 동일 presentation의
`task_completion_time_savings_ratio`만 사용합니다. 양수/0/음수는 빨라짐/변화 없음/느려짐이고,
직접 비교가 없으면 미측정입니다. 테스트 실행 절감 추정, `request_wall_ms`, Click 관리 구간을 합하거나 빼서
이 값을 만들지 않습니다. 느려진 표본·실패·취소·미완료와 사용자 개입 증가는 첫 화면에도 남습니다.
N/B0/B1/B2, 첫 사용/준비된 반복 사용, Evidence/Guarded, 시나리오가 다른 presentation은 합치지
않으며 둘 이상이면 사용자가 하나를 고르기 전까지 대표 결론을 표시하지 않습니다.

**검증 묶음별 실행 상태**는 실제 결과를 한 줄의 블록으로 표시합니다. 숫자/기호/텍스트로 실행·재사용·실패·중단·미실행·미확정을 구분하며, 재사용 수를 선택하면 해당 목록과 canonical reason으로 이어집니다. 완전한 시간 근거가 있으면 별도 시간 차트의 동일한 0 시작 축에서 F(전체 순차 예상)와 E(이번 실행)를 비교하고, 빗금으로 A(피한 비용 추정)를 표시합니다. 묶음의 횟수 비율과 시간 비율은 별개입니다. 시간 비교 조건이 불완전하면 시간 차트는 표시하지 않고 묶음 수와 상태를 유지합니다.

N/N 결과 확보는 정상 완료한 요청에서만 표시합니다. 검증의 충분성이나 코드 전체의 정확성 보장이 아닙니다. 실패·진행·취소·실행 전 거부는 상태를 우선 표시합니다. 새 작업에 배치가 없으면 과거 성공을 현재 결과로 고르지 않습니다. 과거 배치를 직접 선택하면 배치 시점과 과거 기록 표시가 함께 바뀌고 polling/기록 새로고침에도 선택을 유지합니다.

누적 보조 지표는 최대 7일/1,000건의 **보관된 정상 완료 요청**만 대상으로 합니다. 같은 batch의 중복 전송은 한 번, 실제 재시도는 각각 집계합니다. 기간·표시 시간대·누락 시간 표본 수를 표시하며, 부분 합계를 전체 대기 절감시간이나 평생 절감으로 해석하지 않습니다. 기존 전체 요청 accounting에는 실패·취소 이력이 계속 남습니다.

공유 요약 문구, JSON(v5), 독립 HTML은 같은 시간/작업 presentation 수치를 사용합니다. projection
v8은 기본 미측정 `task_efficiency` 공개 객체를 추가하며 v4/v5/v6/v7 읽기 호환성을 유지합니다.
구형 과거 배치에 집계가 없을 때 현재 배치의 수치로 채우지 않습니다. 공유 묶음명과 Phase 4
presentation은 별도 allowlist를 통과하며 알 수 없는 필드나 원시 usage key가 있으면 가져오기를
거부합니다. 로컬에서 고른 원자료 파일명은 상세 화면에만 보이고 공유본에 포함하지 않습니다.

별도 workflow 비교에서는 각 경로의 중앙값과 쌍별 차이의 중앙값을 구분하고, 워밍업·제외 사유·실패·음수·초기 준비·변경·추가 감사 비용을 보존합니다. `same-shards`는 source-command 구간, `parent-suite`는 Click 요청 구간 비교입니다. legacy paired v2의 wall time 비교는 별도로 표시합니다. fixture에서 Click 구간이 더 길었던 사실은 해당 fixture에 귀속하며 현재 요청이 느려졌다고 표시하지 않습니다. 가져오기·제거·공유·새로고침은 검증을 실행하지 않습니다.

참고 이미지와 합성 화면 캡처, 실제 Hook/runner 검증은 [구현 기록](docs/dashboard-impact/PLAN.md)에서 구분합니다. Observer는 기본값인 off 상태에서도 검증·정확 일치/정책 재사용·계측·대시보드가 동작합니다.

상단 언어 선택에서 **한국어 / English / 简体中文(중국어 간체)**를 고를 수 있습니다. 선택은 같은 viewer origin의 브라우저 저장소에 보관하며, 저장소 접근이 제한돼도 현재 페이지의 전환은 가능합니다. 상태·이유·날짜·시간 표현과 복사 문구·공유 리포트가 함께 바뀝니다. 작업명과 사용자가 작성한 검증 묶음 이름, canonical ID·판정·측정값은 원문을 유지합니다.

공유 report v5의 `locale` 필드(`ko`, `en`, `zh-CN`)와 독립 HTML의 `lang` 및 표시 문구는
보고서를 생성한 언어를 따릅니다. 이후 대시보드 언어를 바꿔도 이미 만든 보고서는 바뀌지 않습니다.
기존 검증 결과와 시간 산식은 유지하고, task public presentation만 별도 필드로 추가했습니다.

## 시간을 읽는 법

| 값 | 의미와 계측 경계 |
| --- | --- |
| 부분 요청 구간 `request_wall_ms` | Hook 검증 준비 진입부터 결과 기록까지. 같은 monotonic 구현과 배치 바인딩을 확인할 때만 기록합니다. 호스트 큐·Hook 초기 로딩·최종 저장과 호스트 반환은 제외하므로 전체 대기가 아닙니다. 옛 데이터·시계 불일치·미확정 종료는 **null / 미측정**이며 0ms로 채우지 않습니다. |
| 부분 처리 구간 `measured_processing_ms` | 준비 함수 진입부터 기존 판정·상태 저장 직후까지, 그리고 runner 진입부터 결과 기록 중 계측 저장 직전까지의 **각 프로세스 로컬 경과시간 합계**. 서로 다른 monotonic 원점을 빼지 않습니다. 호스트 대기·전달, 계측 자체의 최종 저장, host 최종 반환은 제외합니다. |
| 검사 실행 구간 `executed_duration_ms` | 실제 시작이 관찰된 묶음의 명령 호출 구간 합계. spawn/시작 기록과 Observer 준비·수집·정리 비용이 이 호출 안에 있으면 포함됩니다. 순수 테스트 CPU 시간이 아닙니다. |
| 생략한 비용 `estimated_avoided_ms` | **실제로 적용된 재사용**의 최근 성공 실행 표본을 합한 추정치. 실행하지 않은 현재 명령의 반사실적 시간은 알 수 없으며 실측 절약이라고 부르지 않습니다. |

전부 재사용이면 명령 실행 시간은 0이지만 준비 처리시간은 측정됩니다.
부분 계측에는 Hook 프로세스 시작·모듈 로딩·준비 함수 이전의 제어문 해석도 포함되지 않습니다.
거부·실패·일반 중단에서도 관찰한 부분 시간과 상태가 남습니다. 원시 요청조차 검증할 수 없으면
묶음 수는 알 수 없습니다. 실행 전 거부는 실행 실적이 아니고, 미실행은 절감 실적이 아닙니다.
명령 시작은 성공한 프로세스 시작/재개 경계에서 기록합니다. Linux Shadow에서는 대상 명령을
포함한 strace launcher 시작 경계이며 대상 exec 실패도 실패한 호출로 남습니다.
강제 프로세스 종료나 저장 실패로 종료를 확인하지 못하면 완료 시간을 만들지 않습니다.

과거 시간 표본에는 원래 실행 revision, check digest, batch id, 관측 시각, 표본 수(묶음 실행 1회)가
연결됩니다. 일부 재사용 묶음의 표본만 있으면 그 부분만 합하고 **추정 가능 / 전체 재사용** 수를 표시합니다.
옛 정수 시간 필드만 있는 데이터는 정확한 출처가 없으므로 새 추정 근거로 꾸미지 않습니다.
Observer overhead는 별도 Shadow 정보이며 위 실행 구간에 다시 더하거나 빼지 않습니다.
tracing slowdown은 비교 측정하지 않았으며 Shadow 후보를 실제 재사용 수나 실측 절약에 합치지 않습니다.

재사용률은 보관 이력의 **재사용 그룹 요청 수 / 전체 그룹 요청 수**와 기간을 공개합니다.
별도 재시도는 실제 새 요청이므로 분모에 포함되지만 같은 배치의 재전송과 화면 새로고침은
중복 누적하지 않습니다. 분모가 불명확하거나 0이면 비율은 null입니다. 시간 표본이 없는
재사용 그룹 수도 별도로 남깁니다. 승인 전 거부·ID 불일치 등 실제 관측 사건은 blocked,
검증 지침은 advisory이며 문구 의미 분석이나 숨은 추론 감시로 만든 사건은 없습니다.

## Guarded A → B · 세 구성의 고정 작업 흐름

```sh
python3 benchmarks/incremental_verification.py --guarded-workflow --iterations 3 --warmups 1 --workload-rounds 40000 --output /tmp/click-workflow.json --html-output /tmp/click-workflow.html
```

새 출력 파일에만 기록합니다. 현재 v4 세 구성 보고서는 독립 HTML로 열거나 대시보드에 가져올 수 있습니다. 기존 v2 paired 보고서도 읽습니다. 새 프레임워크나 서비스가 필요 없습니다.

- baseline: Click Hook 없이 동일한 unittest discover 전체 실행.
- click-default: Guarded를 명시적으로 선택하고 추가 shard/dependency/reuse 설정 없이 실행.
- explicit-reuse: 같은 코드와 테스트를 두 샤드로 나누고 서로 독립인 sibling 코드 변경을
  허용하는 정책을 최초 baseline 전에 커밋. 정책은 관찰기나 자동 의존성 발견이 아닙니다.

제품 기본 모드는 Evidence 그대로입니다. 각 구성은 독립 임시 Git root와 receipt를 사용하며
첫 실행→beta 코드 변경→alpha 코드 변경→전체 코드 변경→환경 변경→alpha 실패→수정 후 재시도→변경 없음의
같은 여덟 단계를 진행합니다. 테스트·코드 digest를 매 단계 구성 간 대조합니다. A 완료 뒤
B를 새 ID로 stage하고 별도 fixture 턴에서 승인합니다. 이후 완료된 단계도 별도 계약을
사용하고 실패·수정은 같은 미완료 계약 안에서 처리합니다. 승인 전 수정·같은 턴 승인·잘못된
ID가 실제로 거부되는지 확인합니다. 개발 세션의 승인이나 Hook 설정은 변경하지 않습니다.

각 단계의 primary 요청 뒤 동일 상태의 전체 suite를 직접 실행하여 PASS/FAIL과 exit code를
대조합니다. 이 감사는 별도 추가 비용이며 Click의 성공 기록을 만들지 않습니다. 마지막 전체
suite가 통과해야 보고서를 생성합니다. B와 최종 영수증은 실제 export와 오프라인 무결성 검사를
거친 unsigned envelope입니다. 정상 표본의 일치는 모든 프로젝트에서 안전하다는 증명이 아닙니다.

워밍업은 전체 workflow 반복을 제외하며 반복마다 새 checkout을 만듭니다. 세 구성 실행 순서를
회전하고 bytecode는 비활성화하지만 OS 캐시·스케줄러는 초기화/제어하지 않습니다. 여덟 요청 합계의
차이와 비율은 기대된 실패·재시도도 포함하고 음수를 보존합니다. Click 요청에는 Hook·runner
시작 비용도 포함됩니다. Git·첫 fixture 승인 setup, 이후 승인/수정 transition, 추가 full audit를
별도 기록합니다. 영수증 export·보고서 직렬화 비용은 제외됩니다. Scripted 승인 시간은 사람의
의사결정 시간이 아니며 검증 시간 차이를 토큰·요금·전체 개발시간 절감으로 환산하지 않습니다.

## 실제 비교 벤치마크

명시적으로 로컬에서만 실행합니다. 평상시 verify와 viewer 새로고침은 비교용 검사를 실행하지 않습니다.

```sh
python3 benchmarks/incremental_verification.py --iterations 3 --warmups 1 --output /tmp/click-comparison.json
python3 benchmarks/incremental_verification.py --mode guarded --iterations 3 --warmups 1 --output /tmp/click-guarded-comparison.json
```

Windows에서는 쓰기 가능한 출력 경로와 현재 Python 명령을 사용하세요. 기존 출력 파일을 덮어쓰지 않습니다.
비교 driver, Hook 준비, 실제 runner는 같은 Python으로 고정합니다. Windows의 `py -3`가 다른 버전을
선택해 비교 환경이 달라지는 것을 막되, 발급된 capability와 실제 재사용 권한 검사는 그대로 유지합니다.
`--scenario`는 first-run, unchanged, docs, partial-reuse, code, environment,
first-failure 중 여러 번 지정할 수 있습니다.
`--workload-rounds`는 PBKDF2 계산량이며 sleep은 사용하지 않습니다.

각 비교 쌍은 별도 임시 Git 저장소에 같은 코드·두 test 파일·정확한 shard inventory·안전 변경 정책을
먼저 커밋합니다. 양쪽 모두 동일 절차의 실제 baseline을 준비한 뒤 고정 변경을 적용합니다.
Click arm은 실제 prompt/pre/post Hook, 재사용 판정, one-use runner, 결과 저장을 거칩니다.
ledger·passing receipt·dependency observation·skip 결정은 주입하지 않습니다.
Guarded fixture는 **임시 테스트 사용자**가 실제 stage와 다음 turn의 pass를 통과하고,
비교 종료 조건을 가진 계약을 유지합니다. 사용자 저장소의 실제 승인으로 간주하지 않습니다.

`partial-reuse`는 기본 Evidence에서 baseline 완료 후 실제 다음 Evidence lifecycle로 넘어간
다음 README를 변경합니다. 커밋된 서로 다른 정책 때문에 alpha는 이전 실제 성공을 후보로
가져와 현재 상태에서 safe-change 규칙을 다시 통과해 재사용하고, beta는 정책 범위 밖이라
실제로 실행합니다. 정책 기반 결과이며 자동 의존성 발견이라고 표현하지 않습니다.
Guarded fixture는 기존 계약 안에서 같은 권한 규칙을 사용하며 계약 간 승인을 상속하지 않습니다.
현재 native Shadow는 authoritative dependency observation이 아니므로 코드 변경의 부분 재사용을
미리 지정하지 않습니다. 실행 권한이 없으면 보수적 재실행 결과를 그대로 보고합니다.

비교 기준은 (1) 같은 두 샤드를 전부 직접 실행 (2) 동일한 두 파일을 발견하는 원래 unittest discover입니다.
대조군은 실제 명령 반환까지, Click은 driver의 Hook 요청부터 runner 반환까지 단일 프로세스 시계로 잽니다.
baseline 준비 시간은 양쪽 측정에서 제외합니다. 두 경로의 코드·범위는 같지만 서로 다른 절대 root의 receipt를
교차 재사용하지 않습니다. 원래 parent는 검증 묶음 1개, shard 경로는 2개입니다.
새 체크아웃, baseline 유무, bytecode 비활성, OS 캐시 미초기화, 실행 순서와 반복 조건을 JSON에 남깁니다.
쌍별 순서를 번갈아 실행합니다. OS 캐시와 스케줄러 영향을 완전히 제어하지는 않습니다.

`delta_ms = baseline_wall_ms - incremental_wall_ms`이며 baseline이 양수일 때만 비율을 계산합니다.
느려지면 음수를 유지합니다. 성공한 비워밍업 표본의 중앙값·최소·최대와 모든 원시 쌍별 시간을 남깁니다.
실패·중단과 워밍업 표본은 성공 성능 통계에서 제외하지만 JSON에는 제외 이유와 함께 남습니다.
화면에서 JSON을 선택하면 실제 비교 조건·측정 수·음수 결과를 포함한 비교 그래프를 표시합니다.
비교 파일이 없으면 실행 안내만 보이며 추정 비용으로 가상의 전체 재실행 baseline을 만들지 않습니다.
가져온 파일은 로컬·서명 없는 측정 자료이며 Click authority의 새 증거가 아닙니다.

작은 fixture는 관리 비용 때문에 오히려 느릴 수 있습니다. 특정 절감률을 통과 기준으로 두지 않으며
임시 저장소 결과를 사용자 저장소 전체의 성능으로 일반화하지 않습니다.

## 이력과 호환성

canonical plan v2는 계획만 저장하고, 실제 결과는 `verification-batch` event에 batch_id로 연결합니다.
같은 batch의 재전송·재기록은 갱신 또는 no-op이며 별도 실행으로 누적되지 않습니다.
계획만 있는 v1 데이터도 읽지만 실제 실행과 시간은 unknown입니다. 최종 상태가 확인된 batch만 누적 통계에 씁니다.
각 이력은 기존 1,000 events / 7일 / 4 MiB 중 먼저 닿는 제한으로 오래된 기록부터 제거합니다.
화면에는 최근 최대 30개 배치만 보이고 실제 보관·표시 개수를 구분합니다.
취소 시 계측만 담은 `efficiency-history-*.json` 보관본을 기존 managed state 위치에 원자적으로 저장합니다.
권한·명령·환경·원시 요청은 보관본에 없고 다음 lifecycle에 결과 이력만 합칩니다.
취소된 실행의 실제 자식 종료가 확인되지 않으면 미확정 중단으로 표시하며 재사용 후보는 적용하지 않습니다.
파일 시스템 오류가 나면 기록이 없을 수 있지만 계측 실패가 승인 경계·검사 결과·취소를 변경하지 않습니다.
정상 저장은 기존 atomic replace 방식을 사용하므로 저장 도중 종료에도 반쪽 JSON을 노출하지 않습니다.

완료된 Evidence lifecycle은 같은 host 세션·작업 공간의 다음 Evidence 작업에 이전 성공을
**재판정 후보**로만 전달할 수 있습니다. 후보는 원래 evidence session, batch, source 사실과
무결성 digest를 보존합니다. 새 요청의 source/check 범위, 현재 Git tree, mutation boundary,
환경, 실행 파일, host coverage를 다시 비교하고, 변경된 tree에서는 기존 complete dependency
observation 또는 현재 커밋된 safe-change policy까지 다시 통과해야 실제 재사용됩니다. revision
숫자가 우연히 같거나 과거 실행시간이 있다는 이유만으로는 재사용하지 않습니다. Dashboard
history, 공유 리포트와 Shadow record를 authoritative evidence로 역변환하지 않습니다.
현재 조합을 안전하게 입증할 수 없으면 reason code를 남기고 실제 검사를 실행합니다.

후속 Evidence 재사용이 있는 completion receipt는 v4 `successor-reused` lineage로 원래 batch와
Evidence session, 원래 revision, 재판정 방식(exact/dependency/safe-change), 후보 digest를
기록합니다. 이것은 여전히 `unsigned-integrity-only`이며 발행자 신원 인증은 아닙니다.

Guarded A가 완전히 끝난 뒤 B가 별도 승인되면 같은 조건으로 성공 후보를 재판정할 수 있습니다.
B의 의존성 선언이 달라지면 A 선언을 물려받지 않고 실제로 다시 실행합니다. 실제 적용된
Guarded 후속 재사용은 receipt v5에서 원본 contract id를 기록합니다. 실패한 B 결과를 A 후보로
덮어씌우지 않으며 B에서 다시 실제 실행한 그룹은 과거 successor 표시를 지웁니다. 기존 v1–v4와
Evidence의 호스트 권한은 보존됩니다.
