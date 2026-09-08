# Click 코드 보강 통합 프롬프트

COMMON 다음 Phase 0~6 순서. 각 Phase는 아래 전문을 사용한다.

# Click 코드 리뷰 후속 보강 — 공통 실행 규칙

작성 기준: 2026-09-08, 공개 main에서 확인한 핵심 소스와 호출부.
이 문서는 구현 지시서다. 작성자가 저장소 전체를 실행 검증했거나 아래 결함 모두의 운영 경로 재현을 완료했다는 뜻은 아니다. 실제 수정 전 Phase 0에서 작업 저장소의 commit과 dirty 상태를 고정해서 다시 확인한다.

## 전체 목표

유효한 검증 결과의 재사용과 자동 샤딩은 유지하면서, 잘못된 입력의 처리, 재사용 결정의 대상 연결, 작업 간 상태 승계, 판정 시점의 변경 감지, 임시 리포트 관리 비용을 보강한다. 모델의 사고량이나 탐색 전략을 제한하는 작업이 아니다.

## 반드시 보존할 경계

1. Evidence / Guarded / Off의 의미, 호스트 실행 권한, 별도 Guarded 승인, 한 번만 사용할 수 있는 runner claim과 replay 방어를 보존한다.
2. 이전 작업의 성공 결과는 다음 작업에서 재심사할 후보일 뿐이다. 이전 승인은 승계하지 않는다. 반대로 계약 ID가 달라졌다는 이유만으로 적법한 successor 재사용을 전부 막지도 않는다.
3. 동일 상태 재사용, 의존성 관측 기반 재사용, 사전 커밋된 safe-change 정책, 부분 샤드 재사용을 보존한다. Shadow, 대시보드, 로그, 시간 측정치는 재사용 권한이 아니다.
4. 증거만 부족하고 실행 권한과 명령이 유효하면 실제 검증으로 돌아간다. 승인·claim·상태 신뢰성까지 확인할 수 없으면 실행을 차단하거나 기존 복구 경로를 사용한다. 모든 오류를 무조건 실행하는 fallback으로 처리하지 않는다.
5. 사용자 변경을 보존한다. git reset --hard, clean, 자동 stash, 자동 commit/push, 원격 변경, 임의 권한 상승, 새 의존성 설치, 사용자 정책 덮어쓰기는 하지 않는다. 실제 검증 도구 실행에 호스트 승인이 필요하면 그 경계를 따른다.
6. 확인되지 않은 취약점을 확정 사실처럼 고치지 않는다. 기존 경계와 테스트로 이미 보장되면 그 근거를 남기고 verified-no-change 또는 테스트 보강으로 종료한다.
7. 숫자 처리에서는 미검증 상태의 -1 sentinel과 성공 증거의 0 이상 revision을 구분한다. 단순 일괄 검색/치환으로 상태 의미를 바꾸지 않는다.
8. 기존 owning module과 초기화·검증 함수를 우선 사용한다. 범용 검증 프레임워크, 전체 타입 전환, 대규모 파일 분해, 새로운 캐시/DB/서명 시스템을 이번 범위에 끼워 넣지 않는다.
9. 테스트를 통과시키려고 assert를 약화하거나 테스트를 삭제하지 않는다. 정상 재사용 성공과 잘못된 재사용 거부를 함께 검사한다. 모든 재사용을 꺼서 통과시키는 것은 실패다.
10. 각 Phase에서는 영향받는 회귀를 실행하고, 이전 결과를 재사용할 때는 그 결과의 입력과 환경이 여전히 유효한지 판단한다. 마지막에는 전체 회귀를 수행하되, 의존성 변경·실패 조사 등 정당한 이유 없이 같은 전체 회귀를 반복하지 않는다.
11. 측정은 실제 관측 범위를 그대로 표시한다. 전체 작업 시간과 컴포넌트 시간, 최초 설정과 준비 후 반복 작업을 분리한다. 실제 usage와 동등한 완료 조건이 없으면 토큰 절감률은 미측정이다. 테스트 수·로그 길이·시간으로 토큰을 추정하지 않는다. 공개 화면·자료에 토큰 절대량을 추가하지 않는다.
12. 대시보드의 절감 시간/토큰 절감률 중심 구성과 관리비용 상세 표시를 이번 작업으로 재설계하지 않는다. 화면 조회나 공유가 검사·모델 호출을 일으키게 만들지 않는다.

## 순서와 범위

Phase 0 → 1 → 2 → 3 → 4 → 5 → 6.
개별 Phase만 요청받았다면 그 Phase만 실행한다. 전체 순차 실행을 명시적으로 요청받았다면 완료 기준을 충족한 다음 상세 파일을 읽어 같은 승인 범위 안에서 이어간다. 다음 파일을 읽는 행위가 새 Guarded 승인이나 권한을 대신하지 않는다. 필요한 기준이 충족되지 않으면 후속 Phase를 완료한 것처럼 처리하지 않는다.

## 기록

기존 동등한 문서 구조가 없으면 docs/review-hardening/ 아래에 작업 기록을 둔다. 기존 파일이 있으면 읽고 병합하며 덮어쓰지 않는다.

각 Phase 보고서에 다음을 남긴다.
- 시작/종료 commit과 dirty 상태. 수정 중인 저장소라면 commit만 같다고 동일 코드라고 간주하지 말고 관련 diff 또는 content fingerprint를 기록한다.
- 확인한 호출 경로와 책임 경계.
- 재현된 결함 / 방어적 보강 / 이미 보장됨 / 아직 미확인 분류.
- 변경 파일과 핵심 동작 변화, 변경하지 않은 이유.
- 실행한 정확한 명령, 환경, 종료 코드, 통과·실패·skip 수.
- 수행하지 못한 테스트와 그 이유, 남은 위험.
- 다음 Phase로 전달할 결정과 호환성 영향.

progress.json이 필요하면 workflow의 문서용 상태로만 사용한다. 가능한 상태는 pending, in_progress, passed, verified-no-change, failed, blocked다. 이 파일 자체는 실행 승인이나 재사용 권한을 만들지 않는다.


---

# Phase 0 — 기준선 확보와 지적사항 재분류

## 실행 프롬프트

Click 저장소의 COMMON.md를 먼저 읽고 Phase 0만 수행해라. 구현 수정부터 시작하지 말고, 아래 지적의 실제 도달 경로와 현재 보장 범위를 확인해 수정 기준선을 만들어라.

### 목표

함수만 보면 약한 부분과 실제 호출 경로에서도 잘못 작동하는 부분을 구분한다. 이전 리뷰를 그대로 정답으로 취급하지 않는다.

### 우선 확인 대상

- hooks/click_verification_reuse.py: verification_receipt_matches, dependency_receipt_matches, safe_change_receipt_matches, requalify_successor_baseline, 관련 promotion 함수.
- hooks/click_evidence.py: sources_from_state, is_current, 초기 source 생성과 successor 검증.
- hooks/click_change_policy.py: decide, receipt 검증, changed_paths 검증.
- hooks/click_verification.py: prepare, 재사용 결정 생성/적용, runner 진입, 상태 저장, 재사용 후 drift 검사.
- hooks/click_gate.py: _json_report_command.
- 위 경로와 연결되는 기존 테스트 및 .github/workflows/ci.yml, scripts/run_ci_tests.py, 배포 동기화 규칙.

### 수행할 일

1. git HEAD, branch, dirty diff, Python/OS, 필요한 테스트 도구 유무를 기록한다. 기존 사용자 작업을 정리하거나 되돌리지 않는다.
2. 실제 호출 흐름을 상태 읽기 → source 검증 → 재사용 판정 → 성공 상태 승격 → 저장/반환까지 추적한다. 함수 별칭과 공개 진입점도 확인한다.
3. revision의 int 형변환이 있는 판정과 상위 타입 검증을 대조한다. 순수 함수에서 비정상 값이 통과하거나 예외가 나더라도 전체 Hook 경로의 실행 생략까지 재현한 것으로 기록하지 않는다.
4. safe-change decision이 실제로 어떤 check와 baseline에서 만들어지는지 확인한다. 현재 공개 소스의 해당 호출은 같은 source의 기존 receipt를 decide에 전달한다. 작업 checkout에서도 이 전제가 유지되는지 확인한다.
5. requalify_successor_baseline의 전체 복사가 source 수준인지 계약 전체 상태인지 구분한다. 승인 토큰이 실제 source에 있지도 않은데 유출된 것처럼 쓰지 않는다.
6. 리포트 정리의 현재 트리거를 확인한다. inline 리포트가 실패한 fallback에서만 작동하는지와 실제 대상 디렉터리를 기록한다.
7. 기존 집중 회귀를 실행하고, 수정 전 정상 재사용 사례와 작은 성능 기준선을 보존한다. 전체 회귀 기준선이 필요하면 저장소 공식 runner를 이용한다. 제한된 환경에서 실행하지 못한 항목은 명시한다.
8. 각 finding을 재현 결함, 방어적 일관성 보강, 유지보수 위험, 테스트 공백, 이미 보장됨으로 분류한다. 후속 Phase별 허용 변경 범위를 정한다.

### 산출물

reports/phase-0.md, findings.md, 원본 실행 로그/측정치의 로컬 위치, 필요 시 progress.json. 신규 문서는 기존 동등한 구조가 없을 때만 만든다.

### 완료 기준

재현과 추측이 분리돼 있고, 정상 재사용 기준 사례가 보존되며, 모든 후속 Phase에 실제 대상 함수와 검증 명령이 연결돼 있어야 한다. 실행 환경 제약을 성공으로 바꾸지 않는다. 이 Phase에서 기능 수정은 하지 않는다. 회귀 재현용 테스트 추가가 필요하면 구현과 분리해 기록한다.


---

# Phase 1 — revision 및 증거 입력 검증 일관성

## 실행 프롬프트

COMMON.md와 Phase 0 보고서를 읽고 Phase 1을 수행해라. 재사용 성공 판정에서 형변환으로 잘못된 값을 정상 값처럼 만들지 않도록 입력 계약을 통일해라.

### 수정 중심

hooks/click_verification_reuse.py의 receipt matcher와 promotion 전제, hooks/click_evidence.py의 is_current 및 관련 상태 검증. 동일 증거 경계에서 호출하는 changed_paths/digest 검증도 잘못된 컨테이너 형식으로 예외가 나는지 점검한다. 저장소의 모든 int()를 일괄 제거하지 않는다.

### 구현 요구

1. 성공 증거 revision과 현재 revision은 bool이 아닌 정수이며 0 이상이어야 한다. 동일 revision matcher는 정확한 값 비교를 한다. cross-revision matcher는 유효한 과거 revision과 현재 revision의 순서를 명시적으로 검사한다.
2. -1은 ready/미검증 상태에서 유지할 수 있지만 성공 재사용 근거가 될 수 없다. 정상 0 revision을 실수로 거부하지 않는다.
3. True/False, float, 숫자 문자열, None, list, dict를 자동 정수화하지 않는다. 과거 저장 형식이 실제로 문자열 revision을 허용했는지 근거가 있을 때만 읽기 경계에서 버전별 마이그레이션을 고려한다. 추측으로 호환 계층을 만들지 않는다.
4. 기존 작은 validator가 적절하면 재사용한다. 새로운 공통 helper가 필요하면 실제 공유되는 좁은 계약만 추출하고 순환 import나 정상 Hook의 무거운 import를 만들지 않는다.
5. malformed 데이터는 matcher에서 예측 가능한 False 또는 기존 명시적 오류로 처리한다. 예외 전체를 광범위하게 잡고 성공이나 자동 실행으로 처리하지 않는다.
6. promotion 함수가 검증된 입력만 받는다는 전제가 있다면 호출 경로와 타입/테스트에 드러나게 한다. 통계 카운터 오류가 재사용 허가로 변환돼서는 안 된다.
7. 값 검증 전 set/sort/비교/정수화가 수행되는 인접 validator를 확인한다. 비해시 가능 원소나 혼합 타입 목록은 필요한 경우 타입과 크기를 먼저 검사한 뒤 거부한다. 입력 상한을 새로 발명하지 말고 기존 스키마 상한을 보존한다.

### 테스트

정상 0, 정상 양수, 동일 revision, 과거 revision, 미래 revision, -1 sentinel, True/False, 1.0/1.9, 숫자 문자열, None, 누락, list, dict를 포함한다. 관련 float 입력을 지원하는 진입점이 있으면 NaN/Infinity도 거부 경계를 확인한다. changed_paths에는 정상 문자열 배열, 중복, 비정렬, 혼합 타입, 중첩 컨테이너를 넣는다.

순수 함수 테스트와 상태 파일 읽기/Hook 통합 테스트를 분리한다. 상위 로더가 이미 거부하는 케이스는 그 사실을 확인하는 테스트로 남긴다. 정상 동일 revision 재사용과 적법한 cross-revision 재사용은 계속 성공해야 한다.

### 완료 기준

잘못된 입력으로 새 성공 증거가 생기지 않고, 검사 가능한 malformed 입력 때문에 예기치 않은 예외가 발생하지 않으며, 정상 재사용 의미와 -1 sentinel 호환성이 유지된다. receipt 부족과 실행 권한 부족의 fallback을 구분한다. reports/phase-1.md에 수정 전/후 차이를 남긴다.


---

# Phase 2 — 재사용 결정과 대상 증거의 연결 명시

## 실행 프롬프트

COMMON.md 및 Phase 0~1 보고서를 읽고 Phase 2를 수행해라. safe-change decision이 현재 재사용하려는 source의 원본 증거와 현재 입력 조건을 대상으로 생성된 결정인지, 적용 경계에서 확인할 수 있게 해라.

### 중요한 전제

현재 공개 호출부는 같은 source의 check와 baseline receipt를 click_change_policy.decide에 전달한다. decision digest에도 이전/현재 snapshot과 정책 entry의 정보가 포함된다. 따라서 이 작업을 이미 재현된 우회 취약점으로 소개하지 말고, 기존 보장 확인 및 필요할 때의 명시적 연결 보강으로 수행한다.

### 구현 요구

1. hooks/click_change_policy.py의 결정 생성과 hooks/click_verification_reuse.py의 matcher/promotion, hooks/click_verification.py의 소비 경로를 함께 검토한다.
2. 재사용 승인의 책임 위치를 하나로 명확히 한다. 판정 함수의 반환형이나 좁은 검증 helper가 도움이 되면 사용하되, 전체 dict 구조를 dataclass로 바꾸지 않는다. TypedDict는 문서화 도구일 뿐 런타임 검증의 대체물이 아니다.
3. 원본 baseline, check/source identity, 현재 snapshot, 정책 identity, revision 및 실행 binding이 어디에서 결합·검사되는지 기록한다. 이미 검증되는 binding을 매 단계에서 비싼 파일 재해싱으로 반복하지 않는다.
4. 적용 함수가 독립적으로 입력을 받는 경계라면 결정의 digest를 정규 payload에서 재계산하고 해당 source의 baseline 및 현재 조건과 대조하는 등, 실제로 필요한 연결을 추가한다. 기존 fields와 canonical digest 함수를 우선 사용한다. 단지 64자리 형식이 맞는지만 검사하는 것으로 provenance를 증명하지 않는다.
5. hash를 사용자/발행자 신원에 대한 암호학적 인증으로 표현하지 않는다. 새 HMAC/서명 체계가 필요한지 증거 없이 도입하지 않는다.
6. 기존 계약의 candidate를 새 계약에서 재심사하는 정상 successor 경로를 보존한다. 이전 계약 ID와 새 계약 ID가 다르다는 이유만으로 일괄 거부하지 말고 origin과 현재 승인 대상의 역할을 구분한다.
7. 실패 시 source를 partially passed로 남기지 않는다. 판정은 가능하면 side effect 없이 수행하고, 검증된 결과만 기존 상태 변경 경계를 통해 적용한다.

### 테스트

검사 A에서 만든 결정을 다른 검사 B에 적용, 같은 검사지만 다른 baseline, 다른 workspace, 바뀐 현재 snapshot, 바뀐 정책, decision_digest만 바꿈, digest는 그대로 두고 changed_paths/receipt 수정, 다른 revision에서의 낡은 결정 재사용을 검사한다. 실제 지원되지 않는 외부 decision 주입 경로를 만들지는 않는다.

모든 binding이 동일한 유효한 결정은 유지하고, 새 계약의 정상 후보 재심사와 부분 샤드 재사용도 성공해야 한다. 기존 호출부만으로 보장이 충분하다면 해당 경계의 회귀 테스트와 책임 문서만 보강해도 된다.

### 완료 기준

결정 생성과 적용의 책임이 명확하며 다른 검사/원본/현재 조건의 결정을 적용할 수 없다. 정상 reuse 비율을 불필요하게 낮추지 않는다. 저장 형식 변경이 필요했다면 구버전 읽기/거부 정책을 테스트와 함께 기록한다. reports/phase-2.md에 실제 결함 수정인지 방어적 보강인지 명시한다.


---

# Phase 3 — 작업 간 증거 승계를 허용 목록 기반으로 변경

## 실행 프롬프트

COMMON.md와 이전 보고서를 읽고 Phase 3을 수행해라. requalify_successor_baseline의 source 전체 복사 후 초기화 패턴을, 새 작업의 정상 초기 상태에 허용된 검증 사실만 넣는 방식으로 바꿔라.

### 목표

source에 새 필드가 추가되더라도 자동으로 다음 작업에 따라가지 않도록 한다. 현재 계약 전체가 복사되거나 승인 토큰 유출이 재현됐다는 전제로 작업하지 않는다. 이번 보강은 source 수준의 승계 계약을 명시하는 것이다.

### 구현 요구

1. source의 모든 필드를 세 그룹으로 분류한다: 이전 검증에서 승계 가능한 사실, 현재 작업에서 새로 정해야 하는 상태, 감사·표시용 메타데이터. 정확한 필드 목록은 현재 checkout의 초기화/검증 코드에서 도출한다.
2. hooks/click_evidence.py의 기존 source 초기화 경계를 우선 활용한다. 초기화 schema를 다른 파일에 통째로 복제하지 않는다.
3. check/input/root/environment/executable/host coverage와 완전한 관측 receipt 등 실제로 필요한 검증 사실만 명시적으로 복사한다. 시간 측정이나 과거 성공 duration은 허용할 경우에도 authority와 분리하고 원본 provenance를 보존한다.
4. 현재 요청의 kind, dependency declaration, shard 정보 등 현재 권한과 identity는 현재 상태를 기준으로 검증·유지한다. 이전 값으로 덮어쓰지 않는다.
5. attempts, retry, 실행 중 상태, 예약/claim, 완료 판단, 현 작업의 승인 상태는 이전 작업에서 가져오지 않는다. 현재 lifecycle에서 필요한 예약 필드는 기존 승인/검증 경로가 새로 설정해야 한다.
6. 알 수 없는 필드를 previous source에 추가해도 승계되지 않게 한다. 접두어가 verified_라는 이유만으로 모두 복사하는 식의 느슨한 허용 목록도 쓰지 않는다.
7. 중첩된 list/dict/receipt가 이전 source와 새 source 사이에서 의도치 않게 공유되지 않게 한다. 전체 JSON 왕복 복사를 더 넓은 deepcopy로 대체하는 것으로 끝내지 않는다.
8. baseline의 실제 실행 시점과 현재 재사용 시점을 구분한다. 오래된 검증의 verified_at을 새 실행처럼 바꾸지 않는다.
9. 재심사 실패 시 current와 previous를 모두 훼손하지 않는다. exact-tree, dependency, safe-change 각각의 기존 상태 전이를 보존한다.

### 테스트

기존의 실제 source fixture를 사용해 정상 승계 필드와 새 작업 기본값을 비교한다. 알 수 없는 future field가 배제되는지, 중첩 객체 수정이 이전 증거를 바꾸지 않는지, 현재 shard/declaration이 보존되는지, 실패/진행 중/미완료 상태가 성공으로 바뀌지 않는지 확인한다.

Evidence A→B와 별도 승인된 Guarded A→B를 각각 검사한다. Guarded B의 승인은 A와 독립이어야 한다. 관련 없는 변경에서는 허용된 샤드만 재사용되고, 관련 입력 변경에서는 실제 검증을 실행해야 한다. 필요한 경우 receipt export/verify 및 기존 successor provenance 테스트도 실행한다.

### 완료 기준

새 필드의 기본값이 자동 승계가 아니라 미승계다. 검증 사실과 현재 작업 상태의 소유권이 분리되고 정상 successor 재사용·샤드 provenance·receipt 호환성이 유지된다. reports/phase-3.md에 필드 분류와 정당화한 메타데이터 승계 목록을 남긴다.


---

# Phase 4 — 변경 시점·동시 실행·실패 경계 통합 검증

## 실행 프롬프트

COMMON.md와 이전 보고서를 읽고 Phase 4를 수행해라. 재사용 판단 이후 입력이 달라지거나 실행/저장이 실패할 때, 오래된 성공이 현재 성공으로 남지 않는지 통합 테스트로 확인하고 실제 공백만 수정해라.

### 중요한 전제

공개 코드에는 safe-change 판정 뒤 workspace snapshot을 다시 확인하는 경로가 이미 있다. 기존 drift 검사나 claim을 제거하고 새 잠금 체계를 만드는 Phase가 아니다. 상태 lock만으로 사용자의 외부 편집까지 원자적으로 통제한다고 주장해서도 안 된다.

### 수행할 일

1. prepare → 판정 → 상태 승격/저장 → reuse-only 반환 또는 runner claim → source 실행 → 결과 기록 경계를 지도화한다. 각 경계에서 최신 확인이 필요한 이유를 설명한다.
2. sleep에 의존한 확률적 race 테스트보다 기존 test hook, fake clock, barrier, mock 등의 결정적 동기화를 활용한다. 프로덕션에 테스트 전용 우회나 승인 생략 옵션을 추가하지 않는다.
3. 파일 변경, 파일 생성/삭제/rename, symlink 대상 교체, 환경/실행 파일/공유 설정/lockfile 변경을 실제 관련 입력 범위에서 주입한다. 관측 미지원·불완전 상태에서는 적법한 재사용이 성립하지 않는지 확인한다.
4. 같은 claim의 중복 소비, 취소/timeout, wrapper/child 종료, 상태 저장 실패를 기존 runner/state test infrastructure에서 검증한다. 단지 부모 PID가 없다는 이유로 독립 child가 끝났다고 가정하지 않는다.
5. 증거만 무효라면 기존 권한 아래 실제 검증으로 복귀한다. 실행 권한이나 one-use claim을 확인할 수 없다면 실행하지 않는다. 테스트 결과 기록 실패를 성공 증거로 바꾸지 않는다.
6. 이미 기존 테스트가 정확하게 보장하는 항목은 중복 재구현하지 말고 실행 결과를 연결한다. 실제 검증 공백이 있는 경우에만 최소한의 상태 전이 또는 최신 binding 확인을 추가한다.
7. 관련 없는 입력과 유효한 precommitted policy 조건에서는 정상 부분 재사용을 계속 허용한다. 모든 의심을 전체 테스트 재실행으로 해결하지 않는다.

### 핵심 통합 시나리오

정상 최초 실행 → 변경 없는 재요청 → 허용된 무관 변경 → 관련 변경 → 실패 → 수정 → 재요청의 흐름을 유지한다. Evidence와 Guarded의 지원 경로를 각각 검사한다. 적법하게 나뉜 샤드에서 성공 형제는 유지하고 실패/영향 샤드는 실제 재검증한다.

테스트 fixture의 같은 최종 코드 상태에 대해 원래 전체 검증을 직접 실행하는 대조를 포함한다. 이 감사 실행은 실험 검증용이며, 사용자의 매 요청마다 강제로 추가하는 기능이 아니다. 비교하는 명령의 테스트 충분성까지 증명했다고 쓰지 않는다.

### 완료 기준

검사한 변경/동시성/오류 시나리오에서 잘못된 최신 성공이나 중복 실행 권한이 생기지 않는다. 실제 지원 밖의 OS/native observer는 미실행/환경 제약으로 기록한다. 보장할 수 없는 외부 동시 변경 범위를 솔직히 문서화한다. reports/phase-4.md에 테스트→보호 경계→실제 결과를 연결한다.


---

# Phase 5 — JSON 리포트 정리의 자원 사용과 누적 관리

## 실행 프롬프트

COMMON.md와 이전 보고서를 읽고 Phase 5를 수행해라. hooks/click_gate.py의 _json_report_command에서 불필요한 전체 후보 리스트 생성을 제거하고, 정리 대상이 계속 밀리지 않도록 관리 정책을 확인해라.

### 수정 범위

리포트 fallback 파일 생성/정리 경로와 관련 단위/성능 테스트. 검증 증거, 계약 상태, inspector cache, 진단 retention 전체를 동시에 재작성하지 않는다.

### 구현 요구

1. 현재 list(root.glob(...))[:128]의 실제 비용을 측정한다. inline 리포트가 가능한 일반 경로와 파일 fallback 경로를 구분한다.
2. 후보 제한이 목적이면 lazy iteration을 사용하되, islice로 바꿨다고 디렉터리 전체 탐색 비용까지 고정 상한이 됐다고 주장하지 않는다. glob 구현이나 비매칭 항목 분포에 따라 내부 열거는 더 많을 수 있다.
3. 열거 항목 수, stat 호출 수, 삭제 수, peak memory 중 무엇을 제한하는지 명시한다. 필요한 경우 기존 보관 구조를 활용해 hot path의 작업량을 제한한다. 이 작업만을 위한 무거운 DB/index/상시 서비스는 만들지 않는다.
4. 항상 앞부분만 조사해서 뒤의 오래된 리포트가 영원히 남는 starvation을 피한다. 실제 요구에 맞는 순환 정리/주기적 sweep/기존 전용 경로 활용 중 가장 작은 방식을 선택하고, 반복 호출에서 정리가 진전하는지 검증한다.
5. 한 번에 전체 정렬하는 방식으로 다시 O(N) 메모리를 만들지 않는다. 전체 sweep이 필요하면 그 비용을 숨기지 말고 hot path와 구분한다.
6. 방금 작성한 리포트와 아직 유효한 리포트를 보호한다. 정확한 prefix/파일 형식과 소유 경계 안의 대상만 정리한다. symlink를 따라가 외부 파일을 지우거나 다른 Click 상태 파일을 건드리지 않는다.
7. 파일이 동시에 사라짐, 권한 거부, stat 실패, 경로 교체 등 정리 실패가 기존 검증 권한/결과를 바꾸지 않게 한다. 새 리포트 기록 실패는 기존 명시적 실패 동작을 보존한다.
8. TTL, 현재 reader 동작, Windows 파일 사용 중 삭제 제약 등 기존 호환성을 확인한다. 근거 없는 새 보관 정책을 임의 적용하지 않는다.

### 테스트와 측정

빈 디렉터리, 소수 리포트, 수천 개 리포트, 다수 비매칭 상태 파일, 최신 파일이 앞에 있는 분포, 반복 정리, 동시 삭제, 접근 오류를 포함한다. 실제 tempfile 테스트와 iterator/stat 호출 계수 테스트를 구분한다.

동일 조건에서 수정 전/후 wall time과 메모리 또는 연산 횟수를 기록한다. 전체 저장 용량 상한을 보장하지 않는 방식이라면 그 한계를 그대로 남긴다. 측정되지 않은 지연시간 상한은 약속하지 않는다.

### 완료 기준

후보 전체 리스트 생성을 제거하고, 무엇이 bounded인지 계측으로 증명한다. 오래된 후보 정리가 계속 진행되며 새 리포트와 다른 상태 파일은 보존된다. 이 개선을 전체 개발 시간·토큰 절감 수치로 둔갑시키지 않는다. reports/phase-5.md에 효과와 한계를 남긴다.


---

# Phase 6 — 전체 회귀, 성능 비교, 호환성과 배포 준비

## 실행 프롬프트

COMMON.md와 Phase 0~5 보고서를 읽고 Phase 6을 수행해라. 보강된 코드가 정상 재사용·샤딩·승인 경계를 유지하는지 최종 확인하고, 실제 측정 범위만 문서화해라. 이 Phase는 배포 준비까지이며 사용자 요청 없이 commit/push/tag/release를 하지 않는다.

### 전체 회귀

1. 현재 checkout의 CI 정의와 공식 test runner를 확인한다. 공개 runtime optimization 문서에는 python3 -B -m scripts.run_ci_tests가 전체 실행 경로이며 parts 1~4가 전체 분할 합집합을 구성한다고 설명돼 있다. 실제 checkout이 다른 경우 현재 명세를 따른다.
2. 테스트 discovery 누락, 중복 ID, partition 누락을 확인한다. 새 테스트가 CI에 실제 포함되는지 검사한다. 각 Phase 집중 테스트와 최종 전체 테스트 결과를 분리한다.
3. Evidence/Guarded/Off, exact/cross-revision/successor reuse, safe-change, dependency observer, shard init/status/refresh, 실패 후 부분 재검증, claim replay, receipt export/verify, dashboard read-only와 공개 출력 경계를 확인한다.
4. Windows/macOS/Linux의 구현 상태와 네이티브 실행 검증 상태를 구분한다. 로컬 Linux 통과를 다른 OS 통과로 표시하지 않는다. 호스트/native 조건 미충족이면 skip 이유와 릴리스 제약을 보고한다.

### 성능 검증

5. Phase 0의 고정 기준선과 수정 코드를 같은 테스트 조건에서 비교한다. baseline/수정 버전을 별도 작업 공간에서 실행하거나 사용자 변경을 보존하는 동등한 방법을 사용한다.
6. 최초 설정, 준비 후 반복, 짧은 검증, 충분히 무거운 검증, 무관/관련 변경 시나리오를 구분한다. warmup, 측정 순서, 반복 수, 중간값/분산, 실패와 음수 효과를 기록한다.
7. 재사용 판정/상태 준비/리포트 정리의 컴포넌트 비용과 사용자 전체 요청의 경과 시간을 분리한다. correctness를 위해 추가된 비용은 숨기지 말고 실제 영향과 tradeoff를 설명한다. 안전장치를 제거해 성능 결과를 맞추지 않는다.
8. 전체 작업 토큰 비교가 실제로 가능한 경우에만 동등 완료 조건과 완전한 usage로 절감률을 계산한다. 아니면 미측정으로 남긴다. 공개 출력에는 토큰 절대량을 넣지 않는다.

### 호환성·배포 정합성

9. 기존 모듈 경계와 COMPATIBILITY_SURFACE를 확인한다. 새 private facade forwarder, 순환 import, 정상 Hook 시작 시 dashboard/native backend의 불필요한 eager import를 만들지 않았는지 검사한다.
10. source/distribution parity와 명시적 manifest를 확인한다. 새 모듈/파일을 추가했거나 번들 소스가 바뀌었다면 기존 빌드 스크립트로 필요한 generated artifacts를 동기화하고 parity tests를 실행한다. 생성 파일을 수작업으로 개별 수정하지 않는다.
11. 저장 schema나 receipt 형식이 바뀌었다면 버전별 읽기/마이그레이션/안전한 거부를 시험한다. 권한을 낮춰 호환성을 유지하지 않는다. 호환성 판단에 따라 필요한 문서와 release-note 초안을 갱신하되 버전 번호/릴리스 생성은 승인 없이 진행하지 않는다.
12. README/기술문서에 확정된 수정만 쓰고, 실제 우회가 재현되지 않은 방어적 보강을 보안 취약점 패치로 과장하지 않는다. 모델 추론량을 제한하지 않았다는 설계 설명과 모든 결과물 품질을 보장한다는 주장을 구분한다.

### 최종 보고

수정 요약, 변경 파일, 결함/보강/verified-no-change 분류, 실행 명령, 테스트 통과·실패·skip, 플랫폼 상태, 실제 성능 원자료, 호환성 영향, 미확인 위험을 포함한다. 요청받지 않은 배포·벤치마크 결과는 만들어내지 않는다.

### 완료 기준

허용된 정상 재사용과 샤딩이 유지되고, 검사한 잘못된 재사용 경로가 거부되며, 승인/claim/receipt의 경계가 보존된다. 공식 전체 회귀와 배포 parity가 확인돼야 최종 완료로 표시한다. 필요한 검증을 실행할 수 없으면 reports/phase-6.md와 progress에 blocked 또는 검증 제약을 명시하고 릴리스 준비 완료라고 주장하지 않는다.
