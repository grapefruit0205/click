# 검증 자료의 출처

- `real-hook-projection.v7.json`: 실제 Evidence fixture의 기준 실행 → 일부 코드 변경 → 1개 실행·1개 재사용 이후 읽기 전용 projection.
- `real-hook-report.v4.json`: 같은 fixture projection으로 생성한 공유 JSON. 합성 성공 receipt나 재사용 결정을 주입하지 않았다. Observer off.
- `guarded-workflow.v4.json`: 기존 benchmark driver를 실제 실행한 격리 Guarded fixture. 3개 구성·8단계, 반복 1회·워밍업 0회. 총 32개 비교 쌍과 별도 준비/전환/감사 비용을 보존한다. 현재 저장소 성능 실측으로 해석하지 않는다.
- `../screenshots/*.jpg`: 별도 합성 fixture로 렌더링한 화면이다. 정상 화면의 N=12, X=3, U=9, A=90초는 디자인 검수용이다.

재현 명령과 제한은 [최종 보고서](../reports/final.md)에 있다. 이 파일들은 테스트 자료이며 현재 작업의 서명된 완료 receipt 또는 실행·재사용 권한 증명이 아니다.

브라우저 검수는 1440×900 및 390×844 viewport에서 수행했다. 도구가 반환한 캡처는 JPEG이며 반환 이미지의 실제 크기는 desktop 1425×891, mobile/mobile-untimed 375×812, mobile-failed 270×584다. 원본 반환 바이트를 보관했으며 이미지 크기와 viewport 크기를 동일하게 주장하지 않는다.
