# Click: AI 코딩 에이전트를 위한 점진적 검증

[English](README.md) | 한국어 | [简体中文](README.zh-CN.md)

커뮤니티: [LINUX DO](https://linux.do/)

[![CI](https://github.com/grapefruit0205/click/actions/workflows/ci.yml/badge.svg)](https://github.com/grapefruit0205/click/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **바뀐 것은 다시 검증하고, 아직 유효한 것은 다시 하지 않습니다.**

로그인 코드 하나를 고쳤는데 에이전트가 전체 테스트를 다시 실행합니다.
작은 수정을 한 번 더 하면, 방금 통과한 테스트까지 또 기다려야 합니다.

**Click은 모델의 추론 설정을 바꾸지 않고, 개발 과정의 검증과 재사용을 관리하는
워크플로 가드레일(workflow guardrail)입니다.** 모델은 문제를 분석하고 구현 방법을
선택합니다. Click은 실제 실행 결과와 변경 이력을 연결해, 아직 유효한 검증의
반복을 줄이고 오래된 성공 기록을 현재 결과로 쓰지 않도록 확인합니다.

이 방식을 **점진적 검증(incremental verification)**이라고 부릅니다.
판단 근거인 **revision-aware evidence**는 “무엇이 통과했고, 지금도 유효한가”를
확인할 수 있는 검증 기록입니다. 모델을 낮은 등급으로 바꾸거나 추론량을 줄여
비용을 아끼는 방식이 아닙니다.

## 쓰면 무엇이 달라지나요?

검증 묶음이 12개인 프로젝트에서 로그인 코드만 수정했다고 가정해 보겠습니다.

```text
최초 검증       → 12개 묶음의 성공 기준을 확보
로그인 코드 수정 → 영향받은 3개 묶음 실행
                → 근거가 유지된 9개 묶음 재사용
```

동작을 설명하는 예시이며 실측 성능이 아닙니다. 완전한 분할과 묶음별 입력·정책
근거가 있어야 가능합니다. 공통 입력이 바뀌면 모든 묶음을 실행할 수 있고,
분할의 완전성을 확인할 수 없으면 원래 전체 테스트를 실행합니다.

- **수정 사이의 대기를 줄입니다.** 바뀐 검사는 실행하고, 유효한 검사의 반복을 피합니다.
- **왜 재사용했는지 보여줍니다.** 실행·재사용 여부와 판단 근거를 확인할 수 있습니다.
- **다음 작업에도 성공 기록을 활용합니다.** 이전 결과를 후보로 가져와 현재 조건을 다시 확인합니다.
- **효과를 확인할 수 있습니다.** 대시보드에서 검증 결과와 측정 가능한 절감을 구분해 봅니다.

**테스트가 오래 걸리고, 수정·검증을 자주 반복하며, 검증을 나눌 수 있는 프로젝트**에
특히 잘 맞습니다. 전체 테스트가 2초면 설정·판정 비용이 더 클 수도 있습니다.
목표는 같은 작업을 완성하는 데 드는 시간과 번거로움을 줄이는 것입니다.
실제 프로젝트의 수십 분 절감이나 토큰 절감은 별도 측정이 필요합니다.

## 설치와 업데이트

Click은 **Codex CLI**와 **Claude Code** 플러그인으로 제공됩니다. 두 호스트는
같은 런타임, 같은 증거 규칙, 같은 `click-gate` 명령을 사용합니다.

### Codex CLI

```sh
codex plugin marketplace add grapefruit0205/click
codex plugin add click@click
```

Codex를 재시작하고 새 작업을 시작해 설치된 Hook과 스킬을 다시 불러옵니다. CLI의 `/hooks`에서 검토 대기 중인 Click Hook을 확인한 뒤 사용하세요. 자세한 내용은 [Hook 문제 확인](#hook-문제-확인)을 참고하세요.

업데이트 명령은 다음과 같습니다.

```sh
codex plugin marketplace upgrade click
codex plugin add click@click
```

### Claude Code

```sh
claude plugin marketplace add grapefruit0205/click
claude plugin install click@click
```

새 Claude Code 세션을 시작하면 설치된 Hook과 스킬이 로드됩니다. 모든
`click-gate` 명령은 평범한 Bash 명령이며, 설치된 `PreToolUse` Hook이 Click
러너로 다시 씁니다. Evidence 상태는 `~/.claude/plugins/data/click-click/`에
저장됩니다. Linux와 macOS를 지원하며, 호스트별 제한은
[Click for Claude Code](platforms/claude/README.md)를 참고하세요.

업데이트 명령은 다음과 같습니다.

```sh
claude plugin marketplace update click
claude plugin update click@click
```

현재 릴리스는 **v0.96.1**입니다. 업데이트 후에도 재시작하고 새 작업을 사용합니다.

이 README는 `main`에 있는 자동 관찰·조건부 JS 재사용·복구를 포함한 **미출시 v0.97 후보 소스**도 설명합니다. 공개 릴리스는 **v0.96.1**이며, 해당 릴리스를 업데이트하는 것만으로 후보 변경이 설치되지는 않습니다. [릴리스 노트](RELEASE_NOTES.md)를 참고하세요.

## 다음 코드 수정에서 시작해 보세요

설치 후 Codex 또는 Claude Code에 이렇게 요청합니다.

```text
이번 수정을 Click Evidence로 진행해줘. 관련 테스트를 실행하고,
어떤 검사를 실행·재사용했는지 보여준 뒤 Click 대시보드를 열어줘.
```

**기본값은 Evidence 모드입니다.** 호스트의 기존 권한으로 동작하며 별도의 Click
승인 단계를 추가하지 않습니다. 최초 성공 실행이 기준이 되고, 이후 요청이
재사용 조건을 충족해야 그 결과를 활용합니다. 테스트 자동 분할은 지원하는
프로젝트에서 별도로 설정합니다.

아래 `click-gate`는 Codex 작업 안에서 에이전트가 실행하는 제어 명령입니다.
큰 테스트 묶음이라면 `click-gate sharding init`으로 구성을 살펴보고,
`click-gate sharding status`와 [설정 안내](skills/click/references/automatic-sharding-setup.md)의 다음 행동을 따르게 하세요.

## 결과는 대시보드에서 확인하세요

```text
click-gate status
click-gate status --json
click-gate dashboard start
```

`click-gate status`는 대시보드 언어로 몇 줄만 출력합니다. 실행·재사용 수와
추정 절약 시간, 모드와 변경 번호, 다음 행동입니다. `--json`은 전체 보고서를
반환합니다.

명령이 알려주는 로컬 URL에서 실행·재사용·실패·남은 검사를 확인합니다.
재사용 근거와 입력 수집이 준비되지 않았을 때의 다음 행동도 표시합니다.
오른쪽 상단에서 **한국어 · English · 简体中文**을 선택할 수 있습니다.

테스트 재실행 절감은 실제 재사용과 과거 성공 실행시간으로 추정합니다.
전체 작업시간·토큰 절감은 적합한 비교 자료를 가져오기 전까지 **미측정**입니다.
검증 묶음의 75%를 재사용했다고 전체 작업이 75% 빨라진다는 뜻은 아닙니다.

## 어떤 프로젝트에 쓸 수 있나요?

| 프로젝트 | 현재 가능한 범위 |
| --- | --- |
| Python 백엔드·라이브러리 | 지원되는 unittest·pytest 명령의 분할과 재사용. 자동 입력 관찰은 제한된 CPython 3.12 프로필입니다. |
| JS/TS 프런트엔드·Node 프로젝트 | 지원되는 Vitest·Jest 테스트를 나누고 자식별로 재판정합니다. 관찰만으로 조건부 재사용하는 경로는 Linux Node 22.23.2의 제한된 실행 대상입니다. |
| Go 서비스 | `go test` 실행과 조건을 충족한 결과 재사용을 지원합니다. 자동 테스트 분할은 제공하지 않습니다. |
| 여러 언어를 함께 쓰는 저장소 | 등록한 검증 명령별로 실행·재사용을 판단합니다. 언어 간 의존성을 모두 자동으로 발견하지는 않습니다. |

Rust·Java·.NET·C/C++ 등의 명령 프로필과 도구별 CI 범위는 아래 상세 지원표에
있습니다. 명령 실행 지원, 테스트 분할, 입력 관찰에 의한 재사용은 각각 다릅니다.

## 자동으로 어떻게 동작하나요?

설치된 Hook이 작업과 실행을 기록하고, **다음 검증 요청 때** 아래 흐름을 처리합니다.

```text
요청한 검증 실행 → 성공 결과와 실행 조건 기록
코드 수정       → 변경 상태 기록
다음 검증 요청  → 명령·환경·입력·재사용 근거 재확인
                → 필요한 묶음 실행 + 유효한 묶음 재사용
                → 실제 결과와 판단 이유 기록
```

지원되는 자동 입력 관찰은 프로젝트용 JSON 없이 근거를 만들 수 있습니다.
JS 조건부 재사용은 조건에 맞는 정상 요청 두 번에서 입력을 학습·대조합니다.
자동 샤딩은 최초 구성과 기준 검증이 필요하며, 수집 도구 설치·정책 커밋이 필요한
경우 상태 화면이 다음 행동을 알려줍니다. Click이 도구를 임의로 설치하지는 않습니다.

어디까지 자동으로 되는지는 실행 도구와 입력 조건에 따라 달라집니다.

| 기능 | 적용 범위 |
| --- | --- |
| 검증 기록 | 기본 Evidence 모드에서 호스트 권한으로 기록합니다. |
| 테스트 분할 | 지원되는 unittest·pytest·Vitest·Jest 프로필에서 설정 후 사용합니다. |
| Python 입력 관찰 | 플랫폼 준비 조건을 갖춘 제한된 CPython 3.12 및 unittest·pytest 프로필입니다. |
| JS 조건부 재사용 | 지원되는 Linux Node 22.23.2 실행의 관찰 입력을 재확인하며, 수집의 한계를 표시합니다. |
| 기존 저장소 정책 | 선언한 재사용 정책의 규칙을 유지합니다. Observer를 꺼도 사용할 수 있습니다. |

지원되는 프로필에서는 설정 파일·동적 import·무시된 파일도 추적할 수 있습니다.
worker와 동적 입력에는 여전히 제한이 있습니다. [소스에서 생성한 지원 표](docs/architecture/runtime-support.md)는
실행·분할·재사용을 구분합니다. 언어를 지원한다는 말이 세 기능 모두를 뜻하지는 않습니다.

<details>
<summary>모드·재사용 규칙·자동 샤딩·Observer 복구 자세히 보기</summary>

| 모드 | 동작 |
| --- | --- |
| **Evidence — 기본값** | 호스트 권한으로 작업과 검증을 기록합니다. 별도의 Click 승인 단계가 없습니다. |
| **Guarded — 선택** | 읽기 쉬운 계약을 제시하고, 이후 사용자 응답에서 명시적으로 승인받은 뒤 해당 범위의 작업을 진행합니다. |
| **Off** | Click의 작업 흐름 통제 없이 호스트가 실행을 처리합니다. |

기본 모드를 바꾸려면 다음 중 하나를 선택합니다.

```text
click-gate default evidence
click-gate default guarded
click-gate default off
```

Guarded 작업을 명시적으로 요청할 수도 있습니다.

```text
@Click Guarded 모드로 주문 취소를 추가하고 중복 환불을 막아줘.
```

제안된 계약을 승인하거나, 수정을 요청하거나, 취소하거나, 원문을 볼 수 있습니다. 다음 Guarded 작업에는 새 계약과 별도 승인이 필요합니다. 통과한 검사는 조건을 다시 확인한 뒤 재사용 후보가 될 수 있지만, 승인과 미완료 작업은 다음 계약으로 넘어가지 않습니다. 자세한 내용은 [모드 안내](skills/click/references/modes.md)를 참고하세요.

Click은 **검증된 사실(Fact)**, **현재 작업의 실행 권한(Authorization)**, **대시보드와 측정값(Metrics)**을 별도로 관리합니다. 이전 작업의 성공 근거는 후속 작업에서 재판정할 수 있지만, 이전 작업의 권한까지 자동으로 승계하지 않습니다.

## 언제 결과를 재사용하나요?

Click은 정확한 명령, 작업 공간과 변경 상태, 관련 입력, 환경, 실행 파일의 식별 정보, 확인된 호스트 Hook 적용 범위를 검사합니다. 과거에 성공했다는 사실이나 대시보드 기록만으로 재사용하지 않습니다.

| 경로 | 필요한 근거 |
| --- | --- |
| 같은 리비전 | 정확히 같은 검사의 성공 영수증이 있고 현재 실행 조건도 일치해야 합니다. |
| 커밋된 안전 변경 정책 | **기준 실행 전에 커밋한** `.click/evidence-reuse.json` 정책이 그대로 유지되고, 해당 명령에 대해 모든 최종 변경 경로를 허용해야 합니다. Observer는 필요 없습니다. |
| 선언한 파일 입력 정책 v2 | 커밋한 정책의 허용 변경 범위와 소유자가 선언한 전체 파일 입력이 기준 실행과 맞아야 합니다. 무시된 파일도 확인하며 자동 의존성 발견과는 구분합니다. |
| 권위 있는 입력 관찰 | 지원되는 Evidence 자동 관찰 또는 승인된 Guarded 실행에서 완전한 서명된 입력 스냅샷을 남겨야 하며, 재사용 조건도 다시 확인합니다. |
| 조건부 JS 관찰 | 대상 실행에서 관찰한 입력을 별도로 서명한 근거로 남기고, 이후 입력·실행 조건을 재확인합니다. 리포트에 입력 완전성 미입증을 표시합니다. |

예를 들어 정확한 인증 테스트 명령에 대해 `README.md` 변경을 허용하는 정책을 리비전 12의 기준 실행 전에 커밋했다면 다음과 같이 동작합니다.

```text
revision 12  인증 코드 변경     → 검사 실행 후 성공 기록
revision 13  README.md만 변경   → 정책과 실행 조건이 여전히 맞으면 재사용
revision 14  인증 코드 변경     → 정책이 허용하지 않는 변경이므로 다시 실행
```

허용되지 않은 경로, 정책 수정, 불명확한 Git 상태, 실행 파일·환경 변경, 이후 작업 공간 변경이 있으면 실제로 실행합니다. 안전 변경 선언은 저장소 소유자가 정한 정책이며, Click이 의존성을 자동 발견했다는 뜻이 아닙니다.

선택적인 `.click/evidence-dependencies.json` 맵이나 승인된 Guarded 계약의 의존성 선언은 입력 후보의 범위를 정합니다. 맵만으로 관찰 권한이 생기지는 않습니다. 승인에 연결된 계약 의존성과 맵의 구체적인 경로는 필수 의존성으로 유지되며, 완전한 권위 관찰로 맵의 확장 패턴 범위를 좁힐 수 있습니다. 이 관찰 기반 경로에서는 근거가 없거나 불완전하면 변경 뒤 재사용할 수 없습니다. 자세한 조건은 [검증 프로필과 재사용 규칙](skills/click/references/verification-profiles.md), [Authoritative Observer v2](skills/click/references/authoritative-observer-v2.md)에 있습니다.

커밋된 [Evidence Shards 맵](skills/click/references/evidence-shards-v1.md)은 정확한 상위 테스트 명령을 하위 묶음으로 나눌 수 있습니다. 한 묶음이 실패해도 통과한 형제 묶음은 각자의 재사용 조건을 충족하면 유지됩니다. 맵이 유효하지 않으면 원래 전체 명령으로 돌아갑니다.

## 자동 샤딩: init → status → refresh

메타데이터부터 확인하거나, 저장소에서 사용하는 지원 대상 테스트 명령을 정확히 지정합니다.

```text
click-gate sharding init
click-gate sharding init -- python3 -m unittest discover -s tests -q
click-gate sharding status
click-gate sharding refresh
```

명령 없는 `init`은 저장소 메타데이터만 읽습니다. 프로젝트 코드를 import하거나, 테스트를 수집·실행하거나, 정책을 쓰지 않습니다. 명령을 지정하면 활성 Evidence 또는 승인된 Guarded 실행이 필요하며, 제한된 수집과 비용 측정 과정에서 상위 명령과 제안된 하위 묶음을 실제로 실행할 수 있습니다. 짧은 테스트는 `whole-suite-preferred`가 정상 결과일 수 있습니다.

적용 가능한 제안의 일반적인 진행 순서는 다음과 같습니다.

1. 제안을 확인합니다. `refresh`는 Evidence 또는 별도로 승인된 Guarded 범위에서 적용 가능한 정책을 반영합니다.
2. `commit-required`이면 제안된 정확한 정책을 평소 Git 작업 흐름으로 커밋합니다. 설정 컨트롤러는 `git add`, `commit`, `push`를 실행하지 않습니다.
3. `refresh`로 상위·하위 명령의 bootstrap을 진행하면 `baseline-required`가 됩니다. Bootstrap은 설정 비용입니다.
4. `refresh`로 현재 리비전의 기준 검증을 수행합니다. 하위 묶음이 통과하면 `sharding-ready`가 되며, Observer가 꺼져 있어도 변경 없는 재요청은 exact 영수증을 사용할 수 있습니다. 커밋 정책과 권위 관찰의 준비 상태는 따로 표시합니다.

단계 사이에 `status`를 확인하고 표시된 다음 동작을 따릅니다. 상태는 명령 실행, 자동 목록·분할, exact 재사용, 커밋 정책 재사용, 권위 관찰 재사용을 분리하므로 한 경로가 준비되어도 다른 경로까지 준비된 것은 아닙니다. 이후 테스트 목록이 바뀌면 제한된 변경 내역을 제시하며, `refresh`는 Click이 이전에 커밋한 이력과 일치하는 정책만 갱신합니다. 사용자가 소유하거나 수정한 정책을 덮어쓰지 않습니다.

자동 목록과 정확한 분할은 제한된 unittest, 고정된 Vitest 5, 고정된 Jest 30 프로필에서 로컬 검증했습니다. 보수적인 pytest collect-only 프로필은 고정 버전 통합·관찰 CI 대상으로 관리하며 실제 지원 여부는 명령과 설정에 따라 결정됩니다. Vitest와 Jest는 제한된 정적 설정만 지원하며, 미지원 또는 불명확한 수집에서는 상위 명령을 유지합니다. [자동 샤딩 안내](skills/click/references/automatic-sharding-setup.md)와 [두 프로젝트 E2E 기록](docs/history/auto-sharding/e2e.md)을 참고하세요.

지원 범위는 언어 이름 하나가 아니라 검증 도구 프로필별로 관리합니다.

| 도구/프로필 | 실행 검증 근거 | 자동 목록·분할 |
| --- | --- | --- |
| CPython unittest | 검증함 | 제한된 프로필 |
| pytest | 제한된 collect-only 프로필, 고정 버전 통합 CI | 제한된 프로필 |
| Vitest 5 / Jest 30 | 고정 fixture로 검증함 | 제한된 프로필, 정확한 파일 child |
| Node test/check, npm test, Go test | 검증함 | parent 실행만 |
| JSON/YAML/Markdown/SVG 프로젝트 validator, jq | fixture 검증함 | parent 실행만 |
| Cargo, Gradle, .NET, TypeScript, CMake/CTest | Linux CI의 도구 실행 검사. Click 재사용 전체 통합 검증은 아님 | 없음 |
| xmllint, ImageMagick identify | Linux CI의 도구 실행 검사 | 없음 |
| Maven, 직접 SQL linter | 명령·런타임 프로필 인식. 전용 네이티브 CI fixture 없음 | 없음 |

도구 실행 검사는 Hook부터 재사용까지의 통합 검사보다 좁은 범위입니다. 현재 [.NET 검사](.github/workflows/ci.yml)는 class library를 사용하므로 테스트 발견까지 입증하지 않습니다. 프로필 인식만으로 어느 쪽도 검증됐다고 보지 않습니다. 현재 검사 구성은 [CI](.github/workflows/ci.yml), 과거 Phase별 근거는 [다국어 확장 이력](docs/history/multilang-expansion/README.md)에 있습니다.

## Observer는 꺼져 있어도 되나요?

**네.** 새 Evidence 작업은 지원되는 검사에 자동 관찰을 선택하며 Guarded의 기본값은 Off입니다. Observer를 꺼도 Evidence 기록, 일반 검증, 대시보드, 조건을 충족한 정확한 영수증·안전 변경 정책 재사용이 동작합니다. 명시적으로 끈 설정은 같은 세션의 완료된 Evidence 작업 다음에도 유지됩니다.

`click-gate observer status`, `click-gate status --json`과 대시보드에서 준비 실패 이유·다음 행동·최근 검사별 판정을 확인할 수 있습니다. 이 읽기 전용 표시는 재사용을 승인하지 않습니다. [코드에서 생성한 지원 표](docs/architecture/runtime-support.md)는 일반 실행·자동 분할·완전한 관찰·조건부 JS 재사용과 플랫폼별 준비 조건을 구분합니다. 준비 실패 후 관련 환경이나 도구가 바뀌면 다시 시도하며, `click-gate observer auto`로 명시적으로 재시도할 수도 있습니다.

```text
click-gate observer status
click-gate observer off
```

선택 모드는 목적이 다릅니다.

- `click-gate observer shadow`: 지원되는 Linux·macOS·Windows 백엔드에서 관찰 정보를 수집합니다. 예측은 재사용 권한을 부여하지 않습니다.
- `click-gate observer auto`: 설치된 수집 도구를 준비하고 관련 환경·도구가 바뀌면 다시 시도합니다. 도구 설치나 권한 상승은 하지 않습니다. 소유자 의존성 정책이 없으면 완전한 서명 입력으로 JSON 작성 없이 재사용할 수 있습니다. 관찰이 불완전하면 원래 검사를 실행합니다.
- `click-gate observer authoritative`: 활성 Evidence 또는 승인된 Guarded에서 명시적으로 준비합니다. CPython **3.12.3–3.12.14**의 직접 `python -m unittest`, 지원되는 `python -m pytest` 명령을 대상으로 합니다. 런타임·플랫폼·입력 관찰 조건을 충족해야 하며, 모드를 켜는 것만으로 재사용이 허용되지는 않습니다.

로그·실패 진단과 입력 관찰은 같은 한 번의 실행에서 수집합니다. pytest 입력 프로필은 8.4.2와 9.1.1을 대상으로 하며 캐시 쓰기, 캡처 파일, 시간에 의존하는 플러그인, worker 때문에 재사용 근거가 불완전할 수 있습니다. 기존 실행 옵션과 결과는 유지합니다. Node/Vitest/Jest는 자동 모드에서 파일·worker **후보 정보**를 제한된 횟수로 수집하며, 조건부 대상인 입력은 다음 요청된 실행에서 대조합니다. 원시 후보만으로 재사용을 허용하지 않습니다. 별도로 서명하고 재검증한 조건부 영수증은 입력 완전성이 미입증임을 표시하며 재사용할 수 있습니다. 자세한 범위는 [프레임워크 확장과 제한](docs/architecture/automatic-observation.md)에 있습니다.

기본 `auto` 검증은 각 검사의 첫 실제 실행에서 Linux Node 22.23.2의 시간·난수·공유 메모리 호출을 worker·VM 실행 공간까지 수집합니다. 일부 API는 실제 소비한 값의 해시를 기록하며, 일치하는 네이티브 수집기가 있으면 실행 공간별 난수 상태와 공유 버퍼 바이트 표본도 기록합니다. 이 표본만으로 모든 JavaScript 입력의 완전성을 증명하지는 않습니다. 검증된 실행 영수증과 커밋된 저장소 입력 정책에 따른 자동 재사용은 허용하며, 진단 정보만으로 JavaScript 재사용 권한을 만들지는 않습니다. `observer runtime`은 수집을 명시적으로 다시 시도할 때 사용합니다. [기본 수집·조건부 재사용과 제한](docs/architecture/node-runtime-observation.md)을 참고하세요.

자동 관찰은 검증 묶음별로 판단합니다. Git이 무시한 데이터 파일도 관측했다면 재사용 전에 다시 확인합니다. 모든 언어·worker·외부 DB 입력을 완전히 발견한다는 의미는 아닙니다. 상위 명령 분할에는 기존 자동 샤딩 절차를 사용하며, 관측 기능을 위해 검사를 추가 실행하지 않습니다.

기존 `evidence-reuse.json` 소유자 정책이 있으면 자동 준비로 해당 경로를 바꾸지 않습니다. 실패 진단 요약과 제한된 후속 실패 수집도 native 입력 관찰과 같은 실행의 출력을 보존합니다.

Linux strace 6.8, macOS의 권한이 필요한 `fs_usage`, Windows 기본 ETW 프로필은 해당 OS에서의 검증 기록이 있습니다. 자동 샤딩 E2E 기록의 범위는 Linux입니다. Click이 필요한 도구를 설치하거나 권한을 높이지는 않습니다. 관찰이 불완전해도 테스트의 실제 결과는 유지하지만, 이후 재사용 권한을 만들지는 못합니다. [플랫폼 요구 사항과 검증 범위](skills/click/references/authoritative-observer-v2.md)를 참고하세요.

지원되는 Linux Node 22.23.2 프로필의 기본 JavaScript 관찰은 별도 JSON 없이
**조건부 재사용** 근거도 만들 수 있습니다. 조건을 충족하는 정상 요청 두 번에서
관찰 입력을 학습·대조하고, 이후 요청마다 다시 확인합니다. 설정 파일·동적 import·
무시된 파일도 수집된 경우 확인합니다. 환경 변수는 보수적으로 묶어 확인하므로
한 변수의 변경이 여러 자식을 실행시킬 수 있습니다. 시간·난수·공유 메모리를
사용하거나 지원하지 않는 worker가 있으면 대상에서 제외됩니다. 진단 값을
수집했다는 것만으로 재사용이 허용되지는 않습니다.

이미 조건부 관찰을 사용하는 자식에 미지원 worker가 추가되면 근거 요구를 유지하고
실제로 실행합니다. worker를 제거하면 자동 모드가 다음 요청된 실행에서 이전에
시작됐던 Inspector 수집을 다시 시도하므로 Click 상태를 초기화할 필요가 없습니다.
새 작업이 시작됐다는 이유만으로 이 복구 수집을 반복하지 않습니다. 미지원 런처와
진단 전용 모드의 수집 제한도 유지하며, 학습을 위한 별도 테스트를 실행하지 않습니다.
[조건부 범위와 복구](docs/architecture/node-runtime-observation.md)를 참고하세요.


</details>

<details>
<summary>측정·영수증·벤치마크·Hook 문제 확인 자세히 보기</summary>

## 대시보드: 실행 결과와 측정된 효과

재사용률, Click 자체의 판단 비용, 요청 지연 시간, 전체 작업시간, 토큰 사용량은 서로 다른 지표입니다. 예를 들어 샤드의 50%를 재사용해도 전체 개발시간이나 토큰이 정확히 50% 줄었다는 뜻은 아닙니다. 대시보드는 가능한 범위와 근거를 함께 표시하며, 동등한 비교 자료가 없으면 전체 작업 효과를 미측정으로 유지합니다.

```text
click-gate dashboard start
click-gate dashboard status
click-gate dashboard stop
```

명령이 알려주는 로컬 URL을 엽니다. 첫 화면에서 명령, 자동 목록, exact 재사용, 커밋 정책, 관찰 준비 상태와 다음 행동을 구분해 볼 수 있습니다. 현재 작업, 검증 묶음의 상태, 재사용 근거, 작업 이력도 함께 확인할 수 있습니다. 각 묶음은 끝나는 즉시 저장되므로 다음 묶음이 실행되는 동안에도 결과가 보입니다. 같은 호스트 세션과 작업 공간에서는 다음 Evidence 작업에도 뷰어 연결을 유지할 수 있습니다.

**오른쪽 상단 언어 선택기**에서 **한국어 · English · 简体中文**을 선택합니다. 기본값은 한국어이며, 로컬 저장소를 사용할 수 있으면 브라우저가 같은 origin의 언어 설정을 기억합니다. 리포트도 선택한 언어를 따르고, 사용자가 작성한 작업명과 검사명은 원문을 유지합니다.

첫 카드는 **순작업시간**과 **토큰 절감률**입니다. 적합한 전체 작업 비교 파일을 가져오기 전에는 미측정으로 표시합니다. 별도의 **테스트 실행에서 절감** 행은 실제로 재사용한 묶음과 적합한 과거 성공 실행시간을 바탕으로 피한 재실행 비용을 추정합니다.

| 측정값 | 필요한 근거 |
| --- | --- |
| 전체 작업시간 | 동등한 완료 조건과 유효한 작업 시작·종료 경계 |
| 토큰 절감률 | 비교 가능한 기준과 선택한 작업 범위의 완전한 usage. 시간과 별도로 조건을 확인합니다. |
| 테스트 실행 절감 | 실제 재사용에 연결된 과거 성공 실행시간. 근거 범위가 표시되는 추정값입니다. |
| Hook 처리 시간 | 런타임의 일부 구간이며, 호스트 전체 대기나 개발 전체 시간이 아닙니다. |

내부 측정 파일에서 공개 가능한 전체 작업 비교 파일을 만듭니다.

```sh
python3 benchmarks/task_efficiency.py INTERNAL.json --public-output PUBLIC.json
```

대시보드에서 `PUBLIC.json`을 가져옵니다. 이는 명시적인 측정 흐름이며, Click이 전체 작업시간과 토큰 사용량을 자동 수집하는 기능은 아닙니다. 근거가 없으면 미측정을 유지합니다. 느려진 결과, 실패, 취소, 미완료 표본, 첫 사용 비용을 함께 남기며, 서로 다른 모드·기준·시나리오를 섞지 않습니다.

공유는 요약 복사, 공개 JSON, 독립 HTML을 지원합니다. 공개 리포트에는 원시 명령·로그, 입력 경로, 환경 값, 원시 usage, 토큰 절대량을 넣지 않습니다. 가져오기는 공개 task-efficiency v1과 benchmark v4 workflow/v2 paired 리포트를 최대 4 MiB까지 지원합니다. 대시보드에서 내보낸 v5 JSON은 가져오기 형식이 아닙니다. 보기·가져오기·내보내기·언어 변경은 승인이나 재사용 권한을 부여하지 않습니다. [측정 범위와 공개 정보 기준](VERIFICATION_EFFICIENCY.md)을 참고하세요.

## 검증 상태와 실패 피드백

`click-gate status`는 읽기 전용 요약을 짧게 출력합니다. 실행·재사용 수와 추정 절약 시간, 모드와 변경 번호, 다음 행동을 `CLICK_LANGUAGE` 또는 POSIX 로케일이 정한 대시보드 언어(기본 한국어)로 보여줍니다. `click-gate status --json`은 실제 실행·재사용·미실행·미요청 검사와 코드 변경으로 무효화된 검사, 검사별 이유 코드, 실패 진단을 담은 전체 보고서를 반환합니다. 두 형식 모두 등록된 검증 근거의 상태를 보여줄 뿐 전체 작업의 정확성을 보장하거나 재사용을 승인하지 않습니다.

기본값은 원시 출력과 제출 순서대로 첫 실패에서 중단하는 방식입니다. 지원되는 unittest/pytest 출력에는 actionable 보고를 선택해 실패 테스트와 제한된 로컬 상세 정보를 요약할 수 있습니다. 선택적인 bounded failure collection은 호출자가 독립적이라고 명시해 제출한 검증 소스 사이에서만 정해진 한도 내로 계속합니다. 자동 샤드를 독립적이라고 가정하지 않습니다. 설정 오류, 취소, 실행 조건 변경, 알 수 없는 출력에서는 수집을 중단합니다. [보고와 실패 수집 안내](skills/click/references/verification-efficiency.md)를 참고하세요.

Click은 명시적인 로컬 `cat`, 범위가 제한된 `sed -n`, `rg` 등 지원하는 읽기의 결과도 캐시합니다. 요청, 파일 내용, 디렉터리 목록, ignore 규칙, 실행 조건이 맞아야 재사용합니다. 지원하지 않거나 오래되었거나 실패했거나 크기 제한을 넘은 읽기는 정상 실행합니다. 이 캐시는 로컬에 보관하며 검증 근거가 되지 않습니다. [반복 방지 정책](skills/click/references/anti-loop-policy.md)에 세부 조건이 있습니다.

## 완료 영수증과 재현 가능한 벤치마크

현재 검증 근거가 완성되면 다음 명령을 사용합니다.

```text
click-gate receipt export
```

에이전트에게 출력된 JSON을 `completion-receipt.json`으로 저장하게 한 뒤 해당 파일을 검증합니다.

```text
click-gate receipt verify ./completion-receipt.json
```

영수증은 요청, 리비전, 최종 작업 공간, 검사, 실행 조건, 관찰 범위, 재사용 이력을 연결합니다. Evidence 후속 작업에는 v4를, 후보가 실제 적용된 Guarded 후속 작업에는 재판정한 후보와 해당하는 샤드 이력을 담는 v5를 사용합니다. 이전 영수증도 읽을 수 있습니다. 검증 결과는 **unsigned-integrity-only**입니다. 영수증 내용의 불일치를 감지하지만 발행자의 신원을 증명하지 않습니다.

소스 체크아웃에서 완료된 Guarded A → B 흐름을 재현하려면 다음을 실행합니다.

```sh
python3 benchmarks/incremental_verification.py --guarded-workflow --iterations 3 --warmups 1 --workload-rounds 40000 --output /tmp/click-workflow.json --html-output /tmp/click-workflow.html
```

벤치마크는 별도의 실제 Hook/runner fixture를 사용하며 현재 작업의 승인을 대신하지 않습니다. Click 없음, 기본 재사용 설정의 Guarded, 미리 커밋한 샤드·안전 변경 정책이 있는 Guarded를 비교합니다. 제품의 기본 모드는 Evidence입니다. 무관·관련 코드 변경, 환경 변경, 실패, 수정, 변경 없는 재시도를 포함하고 각 단계를 같은 상태의 전체 테스트로 대조합니다. 설정·전환·대조 검증 비용, 워밍업, 느려진 결과도 남깁니다. 현재 v4 workflow와 v2 paired 리포트를 대시보드에 가져올 수 있습니다. 이 표본만으로 보편적인 개발시간·토큰 절감을 주장하지 않습니다.

## Hook 문제 확인

Windows에서는 Click 활성화 상태와 Python 3 실행기 중 하나가 동작하는지 확인합니다. 번들 실행기는 `py -3`, `python`, `python3` 순으로 찾습니다.

```powershell
codex --version
codex plugin list --json
py -3 --version
python --version
python3 --version
```

설치·업데이트 후 Codex를 재시작합니다. CLI의 `/hooks`에서 대기 중인 Click 정의를 검토하고 신뢰합니다. 신뢰는 현재 Hook 해시에 연결됩니다. `[features].hooks = false`는 Hook을 끄며, 관리자 정책 `allow_managed_hooks_only = true`는 플러그인 Hook을 제외합니다. [Codex 공식 Hook 안내](https://learn.chatgpt.com/docs/hooks)를 참고하세요.

이후 새 작업에서 작은 실제 검증을 실행하고 `click-gate status`를 확인합니다. 플러그인 활성화 표시만으로 Hook 실행까지 확인된 것은 아닙니다. Windows CI와 OS별 Observer 검증 범위는 [릴리스 노트](RELEASE_NOTES.md)에 있으며, 사용자에게 설치된 호스트와 설정도 별도로 확인해야 합니다.

Claude Code에서는 `claude plugin list`로 설치된 플러그인을, `/hooks`에서 `[plugin:click]` Hook 정의를 확인합니다. 소스 빌드는 `claude plugin validate ./dist/claude --strict`로 검사할 수 있습니다. Hook 명령은 `python3`를 실행하므로 Claude Code가 사용하는 셸에서 `python3 --version`이 동작해야 합니다. Hook 출력과 오류는 대화 기록에 `click hook error` 줄로 표시됩니다.

## Antigravity

소스 체크아웃에서 실험적인 Google Antigravity 어댑터를 사용할 수 있습니다.

```sh
agy plugin install ./dist/antigravity
```

호스트가 제공하는 Hook 범위에서 Evidence와 Guarded 작업을 지원합니다. 지원하지 않는 경로를 독립적으로 관찰했다고 표시하지 않습니다. [Antigravity 어댑터 안내](platforms/antigravity/README.md)를 참고하세요.


</details>

## 한계와 기술 문서

Click은 작업 흐름의 가드레일이며 운영체제 샌드박스가 아닙니다. 숨겨진 추론, 의미적 정확성, 테스트의 충분성, 일치하는 Hook 밖의 외부 활동을 증명하지 못합니다. 독립 관찰이 없는 수동·호스팅 근거는 보고자의 진술로 남습니다.

재사용 판단은 실제로 추적하거나 관찰할 수 있는 입력에 한정됩니다. 검증 경로의 설정에 따라 Git에서 제외된 로컬 설정, 외부 데이터베이스와 원격 API, 클라우드 상태, 시간에 따라 달라지는 비결정적 로직, 머신에만 있는 상태, 런타임에 새로 생긴 동적 의존성을 자동으로 감지하지 못할 수 있습니다. 불확실하면 실제 검증을 다시 실행하며, 코드 리뷰·CI·브랜치 보호·배포 통제를 함께 사용하세요.

프로토콜과 구현 범위는 다음 문서에서 설명합니다.

- [제품 원칙](PRODUCT_CONSTITUTION.md), [가드 분류](GUARD_CLASSIFICATION.md)
- [동작 모드](skills/click/references/modes.md), [Guarded 워크플로](skills/click/references/guarded-mode.md), [Guarded 계약 형식](skills/click/references/directive-format.md)
- [검증 프로필](skills/click/references/verification-profiles.md), [실행 기능 프로토콜](skills/click/references/capability-protocol.md)
- [자동 샤딩 설정](skills/click/references/automatic-sharding-setup.md), [Evidence Shards v1](skills/click/references/evidence-shards-v1.md)
- [Authoritative Observer v2](skills/click/references/authoritative-observer-v2.md), [Shadow Observer v1](skills/click/references/observer-v1.md), [Shadow Intelligence v1](skills/click/references/shadow-intelligence-v1.md)
- [문서 지도](docs/README.md), [검증 효율](skills/click/references/verification-efficiency.md), [반복 방지 정책](skills/click/references/anti-loop-policy.md), [런타임 구조와 최적화](docs/architecture/runtime-optimization.md)
- [검증 단계별 모듈](docs/architecture/verification-lifecycle.md): 기존 API를 유지하면서 준비·일회용 claim·실행·결과 기록을 분리한 구조입니다.

## 라이선스

[MIT](LICENSE)
