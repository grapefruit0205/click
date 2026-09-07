# Phase 5 — 통합 검증

상태: 완료.

실제 Hook/runner fixture에서 기준 실행 → 일부 코드 변경 → 기존 정책에 따른 재판정 → 1개 실행·1개 재사용 → projection v7 → 공유 JSON/HTML까지 연결했다. 성공 receipt나 재사용 결정을 주입하지 않았다. 이 fixture는 Evidence 모드, Observer off로 실행했다.

최종 관련 검증은 111개 테스트가 모두 통과했다. 산식/projection/DOM 35개, loopback HTTP/기존 incremental 20개, 실제 benchmark/importer 11개, 배포·저장소 정책 45개다. 마지막 코드·문서·캡처 보관 후 완료 증빙을 갱신하는 최종 관련 검사에서 다시 확인했다.

기존 생성기를 실행하여 세 Hook 파일의 Antigravity 배포 사본을 동기화했다. README와 VERIFICATION_EFFICIENCY.md의 화면·지표·형식·Observer 설명을 갱신했다. 테스트용 로컬 서버는 종료했다.

실제 1440×900 / 390×844 화면, 키보드 필터, 미측정·실패·새 작업·과거 선택, 실제 benchmark 파일 가져오기를 확인했다. 합성 화면과 실제 실행 자료는 별도 경로에 보관했다.

실행하지 않은 항목과 범위는 [최종 보고서](final.md)에 명시했다. 전체 저장소 테스트의 반복 실행, 설치본 교체, commit/push, 배포는 수행하지 않았다.

완료 receipt 내보내기는 활성 capability claim 오류로 거부됐다. 테스트 111개 통과와 canonical receipt 부재를 구분해서 기록했다. 제품 구현·검증은 완료했으며 런타임 ledger는 수정하지 않았다.
