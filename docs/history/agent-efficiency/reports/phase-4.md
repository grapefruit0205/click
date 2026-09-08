# Phase 4 — 전체 task 평가 adapter와 공개 projection 완료

기존 verification interval benchmark를 바꾸지 않고 `benchmarks/task_efficiency.py`를 추가했다. 내부 원자료와 공개 표시 모델을 분리했으며, 현재 호스트에서 동등한 N/B0/B1/B2 모델 작업을 새로 실행할 수 있는 task/usage 경계를 확보하지 못해 실제 비교 pair는 0이다. 따라서 task 완료시간, 사용자 개입 변화와 토큰 절감률은 모두 미측정이다.

## 내부 평가 v1

`click-task-efficiency-evaluation` v1은 B0/B1/B2/N, scenario, first-use/prepared-repeat, Evidence/Guarded를 exact scope로 분리한다. 각 pair는 다음 조건을 확인한다.

- 양쪽 task 시작·종료와 baseline>0, acceptance version과 completion digest 일치, acceptance pass.
- input/output counter의 incremental/cumulative 의미와 task 전체 coverage.
- event id/response id/sequence 중복 방지. cached input과 reasoning이 상위 token total에 포함된 선언이면 다시 더하지 않는다.
- 실패·취소·미완료를 comparable 성공 pair에서 제외하되 수와 불리한 비용은 보존.
- 사용자 개입 횟수와 확인 가능한 수행시간, tool call, 실패 후 첫 mutation 전 호출, repair cycle, 확인 가능한 model round trip, 활동/미분류 구간. 활동 구간은 전체 elapsed로 합하지 않고 hidden reasoning은 unknown이다.

내부 파일은 계산 재현에 필요한 raw usage와 절대 token 수를 보존한다. 이는 접근 제한 로컬 평가 자료이고 dashboard/share 입력이 아니다.

## 공개 projection v1

`click-task-efficiency-public` v1은 각 exact scope의 task 시간 차이/비율과 faster/unchanged/slower, token 비율, 표본·실패·취소·미완료, 공개 가능한 사용자 개입과 관찰 수치만 allowlist로 내보낸다. token 대표값은 완료 pair의 baseline/improved 총합으로 계산한 비율이고, 쌍별 비율 중앙값과 구분한다.

`baseline_total_tokens`, `improved_total_tokens`, `saved_tokens`, input/output/cache/reasoning 절대량, raw usage/event/log/path는 공개 객체에 없다. 잘못된 schema, 분모 0, 부분 usage, task 경계 누락과 완료 digest 불일치는 null과 사유가 된다. 양수·0·음수·미세한 비영 값의 부호를 보존한다.

재현 명령은 다음과 같다.

```sh
python3 benchmarks/task_efficiency.py docs/agent-efficiency/evidence/phase-4-internal.json \
  --public-output /tmp/click-phase-4-public.json
cmp /tmp/click-phase-4-public.json docs/agent-efficiency/evidence/phase-4-public.json
```

## 검증과 실제 측정 범위

`tests.test_task_efficiency`의 12개 검사가 통과했다. 시간 양수/0/음수, baseline 0/경계 누락, 동등 완료 실패, incremental/cumulative usage, cache/reasoning 포함 관계, duplicate/replay, 부분 usage, 공개 금지 key, exact scope 분리, 사용자 개입·활동 관측을 확인했다.

보존된 내부 evidence는 `observed_pair_count: 0`, `synthetic_values_used: false`, host task/usage source unavailable을 기록한다. 공개 evidence는 `measurement_status: unmeasured`, 빈 `presentations`다. importer/fixture 통과는 모델 task 실행이나 토큰 절감 실측이 아니며, 목표 10~20% 또는 20~35%를 값이나 acceptance로 사용하지 않았다.
