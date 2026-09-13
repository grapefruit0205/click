# Click community drafts

Local drafts, not published posts. Written for v1.1.0. Every number below comes
from [`docs/history/agent-ab-2026-09-12/`](docs/history/agent-ab-2026-09-12/README.md)
(eight paired Opus 5 sessions) or from `RELEASE_NOTES.md` fixture measurements;
cite the record when a number is challenged. Never replace a measured value with
an estimate, and never drop the cost line from a post that carries the time line.

## What may be said, and what may not

Allowed, with the condition attached:

- "Same task, Opus 5, 8 sessions: 381 s → 123 s (−68%) with Click; test
  executions 20 → 10; cost +6–11%." — 40 s four-module `unittest` fixture,
  three seeded bugs fixed one at a time, one machine, Linux, v1.0.0.
- "With v1.1.0's plain `click-gate verify -- <command>` form, Click-on cost
  came down to the Click-off level ($0.57 vs $0.60)" — two sessions only;
  always say n=2 and "direction", never "measured saving".
- "The same test never runs twice, its output is never read twice, and nothing
  is skipped by guess." — README hero line; the code enforces it.
- "Every skip has a receipt: an exact receipt, a signed input observation, or a
  committed owner policy; anything ambiguous runs."
- "Reading the clock or the environment inside a test helper was refused for
  reuse and for splitting" — the pilot did exactly that, with reason codes.
- "Opus 5 followed the Click directive 20/20; Haiku 4.5 ignored it 0/4."
- Fixture measurements from `RELEASE_NOTES.md`, labelled as such.

Not allowed:

- Any token or cost *saving*. In the eight-session record cost went up +6–11%
  (from ~1.6 extra turns of cache reads and longer typed commands); the
  v1.1.0 plain form brought cost level with Click off in two sessions, which
  is parity, not a saving. Quote cost, not raw token totals: those are
  dominated by cheap cache reads and mislead on their own.
- Any percentage for "real projects" or "production". The record is a fixture.
- Attributing the wall-time gain to reuse alone: on equal shards it is mostly
  concurrent shard execution; reuse shows as executions 20 → 10 and one fully
  reused request.
- Omitting the limits: JS conditional reuse is Linux + Node 22.23.2; signed
  complete Python observation is CPython 3.12.3–3.12.14; a two-second suite is
  slower with Click (measured: 0.7 s → 13.3 s on a tiny CLI fixture).

## A/B plan

One variable per post: the title/lead. Body, screenshots, install block and the
prepared first comment are identical across variants. Forums do not tolerate
two posts in one board, so cross over instead:

| slot | linux.do | DCInside |
|---|---|---|
| first post | variant A (pain → number) | variant B (safety first) |
| second post, 72 h later, different board or thread | variant B | variant A |

Measure per variant with a distinct short link (GitHub Insights keeps only the
referrer, not UTM): link clicks in 72 h, stars/clones delta (Insights →
Traffic), and the count of "installed / tried it" comments. Decide by a 1.5×
click-rate gap; below that, decide by tried-it comments. Post the record link
in the first comment, not the body, so the body stays short.

Screenshots (same three everywhere): (1) the fourth Click-on request —
`4개 중 1개 실행 · 3개는 … 재사용` with the parallel wall clock; (2) the fifth —
`Click reused 4 current unchanged-tree verification receipts`; (3) the eight-row
results table from `analysis.txt`.

## 한국어 — 디시인사이드 (프로그래밍 갤러리 등)

후기 톤. 마크다운 불릿 남발 금지, 문장 짧게, 스크린샷 3장, 한계는 본문에 먼저.
저장소 링크·기록 링크·설치 명령은 첫 댓글에.

### 제목 A (고통 → 숫자)

AI 에이전트가 파일 하나 고칠 때마다 전체 테스트 다시 돌리는 거, 6분 → 2분으로 줄인 실측 (Opus 5, 8세션)

### 제목 B (안전성 우선)

AI 에이전트 테스트 재사용 플러그인 만들었는데, 추측으로 스킵은 절대 안 하게 만든 후기

### 본문 (A/B 공통)

코딩 에이전트 쓰면 다들 겪는 거. 함수 하나 고치고 "테스트 돌려봐" 하면 전체
스위트 다시 돌고, 로그 다시 읽고, 다음 수정에서 또 반복. 그거 없애는 플러그인을
만들어서 v1.1.0 냈다. Claude Code랑 Codex CLI 둘 다 붙는다. MIT.

원칙은 한 줄: **같은 테스트는 두 번 안 돌리고, 같은 로그는 두 번 안 읽고, 추측으로는
아무것도 안 건너뛴다.** 건너뛸 때는 반드시 영수증이 있어야 한다 — 같은 상태의 정확한
영수증, 서명된 입력 관찰, 아니면 저장소 오너가 커밋한 정책. 셋 중 하나도 없으면 그냥
돌린다.

숫자. 같은 버그 3개 고치는 작업을 Opus 5로 8세션 돌렸다. 4세션은 플러그인 끄고,
4세션은 켜고, 교차로.

- 세션 시간: 381초 → 123초 (−68%). 끈 쪽은 전부 6분대, 켠 쪽은 전부 2분대.
- 테스트 모듈 실행: 20회 → 10회. 켠 쪽은 매번 4 → 3 → 2 → 1 → 0으로 줄고 마지막
  확인 실행은 아예 안 돌았다 ("4개 전부 재사용").
- 비용: +6~11%. 토큰은 오히려 늘었다. 턴이 1~2개 더 돌고, 매번 타이핑하는 JSON
  명령이 길어서. 그래서 v1.1.0에서 `click-gate verify -- <명령>` 축약형을 넣었고,
  추가 2세션에서는 비용이 Click 꺼진 쪽과 같아졌다 ($0.57 vs $0.60). 2세션이라
  방향만 본 거고, 절감이 아니라 동등이다.
- 8세션 전부 버그 3개 다 고쳤고 테스트 파일은 안 건드렸다.

솔직히 써야 할 것. 첫째, 시간 줄어든 건 재사용보다 **샤드 병렬 실행** 덕이 크다.
테스트 파일 4개 길이가 같아서 하나라도 돌면 15초다. 재사용은 실행 횟수 절반과
마지막 0초에서 보인다. 둘째, 40초짜리 스위트 얘기다. 2초짜리 스위트면 관리 비용
때문에 오히려 느리다 (그것도 실측해서 저장소에 남겨뒀다, 0.7초 → 13초). 셋째,
JS 조건부 재사용은 Linux + Node 22.23.2에서만, 파이썬 서명 관찰은 3.12.3~3.12.14.

재밌었던 건 이거. 측정하려고 테스트 헬퍼에 `time.time()`이랑 `os.environ` 읽기를
넣었더니 플러그인이 "시계 입력 있음, 재사용 불가", "환경 접근·파일 쓰기 있음, 분할
보류"라고 거부했다. 내 계측을 내 도구가 잡아낸 거라 짜증났는데, 이게 정확히 이 도구가
해야 하는 일이다. 계측 빼고 트랜스크립트에서 세었다.

또 하나. 이 실험 준비하다가 다른 플러그인이 하나라도 설치돼 있으면 Claude Code에서
재사용이 전부 깨지는 버그를 찾았다. 호스트가 PATH에 모든 플러그인 bin을 붙이는데
내 것만 지문에서 빼고 있었다. v1.0.1에서 고쳤다.

모델 얘기. Opus 5는 "테스트는 click-gate로 돌려라" 지시를 20번 중 20번 따랐고,
Haiku 4.5는 4번 중 0번 따랐다. 모델 따라 다르다.

### 첫 댓글 (미리 준비)

설치:

```
claude plugin marketplace add grapefruit0205/click && claude plugin install click@click
codex plugin marketplace add grapefruit0205/click && codex plugin add click@click
```

저장소 https://github.com/grapefruit0205/click · 8세션 기록과 재현 스크립트는
`docs/history/agent-ab-2026-09-12/` (트랜스크립트 전부 있음).

예상 질문:

- "깨진 테스트를 통과로 둔갑하면?" → 재사용은 영수증 없으면 안 됨. 입력 파일 하나만
  바뀌어도 그 샤드는 돈다. 위 실험에서 실행 횟수 반으로 줄이고도 결과가 같았던 게
  그 증거고, 계측 코드가 거부당한 게 반대 방향 증거.
- "토큰은?" → 8세션 기록에선 늘었다(+6~11% 비용). v1.1.0 축약형으로 2세션 재측정하니
  Click 꺼진 쪽과 같은 비용. 절감 주장은 안 한다.
- "내 프로젝트도 빨라지나?" → 편집→테스트 반복이 많고, 테스트가 파일 단위로 독립적이고,
  스위트가 관리 비용(1초 미만)을 묻을 만큼 길면. 아니면 그냥 원래대로 돈다.

## 简体中文 — linux.do

技术论坛语气。命令、限制、实测条件写在正文；仓库和记录链接放在正文末尾即可。
标题带发布信息。

### 标题 A（痛点 → 数字）

给 Claude Code / Codex 做了个插件：同一个测试不跑两次，同一份日志不读两次。8 个 Opus 5 会话实测 6 分钟 → 2 分钟（v1.1.0 发布）

### 标题 B（先讲安全）

编码代理的测试复用插件，但绝不凭猜测跳过任何检查 —— Click v1.1.0

### 正文（A/B 共用）

用编码代理的佬友应该都遇到过：改一个函数，让它跑测试，整个套件重跑一遍，日志再读一遍，
下一次修改再来一遍。Click 是一个 MIT 插件，装进 Claude Code 或 Codex CLI，记录每次检查的
精确凭据，在执行时决定哪些检查必须重跑，被跳过的检查只返回一行而不是整段输出。

一句话原则：**同一个测试不跑两次，同一份输出不读两次，任何东西都不凭猜测跳过。**
跳过必须有凭据 —— 同一状态的精确凭据、签名的输入观测、或仓库所有者提交的策略。
三者都没有，就照常执行。它不改你的模型选择，也不改推理设置。

实测（完整记录和复现脚本都在仓库里）：同一个修 3 个 bug 的任务，Opus 5 跑 8 个无头会话，
4 个关闭插件、4 个开启，交替执行。fixture 是 4 个 `unittest` 模块、约 40 秒。

| 中位数 | 关闭 Click | 开启 Click | 变化 |
|---|---|---|---|
| 会话耗时 | 381 s | 123 s | **−68%** |
| 测试模块执行次数 | 20 | 10 | **−50%** |
| 费用 | $0.60 | $0.66 | **+6~11%**（v1.1.0 简写形式下另测 2 个会话：$0.57，与关闭时持平） |

开启侧的 4 个会话轨迹完全一致：每轮执行 4 → 3 → 2 → 1 → 0 个分片，最后一次确认
"4 个凭据全部复用"，一个都没跑。8 个会话全部修好了 3 个 bug，没有动 `tests/`。

必须说清楚的三点。第一，耗时下降主要来自**分片并发执行**而不是复用：4 个分片长度相同，
只要有一个要跑，这一轮就是约 15 秒；复用体现在执行次数减半和最后一轮 0 秒。第二，
**费用是上升的**（+6~11%），多出来的是 1~2 个回合的缓存读取和
模型每轮敲的那段约 200 字符的 JSON 命令，不是 Click 的输出更长。v1.1.0 为此加了简写形式
`click-gate verify -- <命令>`，另测的 2 个会话里费用降到与关闭 Click 持平（$0.57 vs $0.60）；
样本只有 2 个，是方向，不是节省。第三，这是 40 秒套件的结果；2 秒的套件会因为管理成本
反而更慢（也实测并留在仓库里：0.7 s → 13.3 s）。JS 条件复用只支持 Linux + Node 22.23.2，
Python 签名完整观测支持 CPython 3.12.3~3.12.14。

一个小插曲：为了计数，我在测试 helper 里加了 `time.time()` 和 `os.environ` 读取，结果插件
直接拒绝复用（"时钟输入"）并拒绝自动分片（"环境访问、文件写入，分割需审查"）。被自己的
工具抓住很烦，但这正是它该做的事。后来改成从会话记录里数。

另外在准备实验时发现一个 bug：Claude Code 会把**所有**已安装插件的 `bin` 追加到 PATH，
而 Click 的环境指纹只排除了自己的目录，所以只要还装了别的插件，复用就完全失效。
v1.0.1 已修复。

模型差异：Opus 5 对"通过 click-gate 跑测试"的指令 20/20 遵守；Haiku 4.5 是 0/4。

安装：

```sh
claude plugin marketplace add grapefruit0205/click && claude plugin install click@click
codex plugin marketplace add grapefruit0205/click && codex plugin add click@click
```

仓库：https://github.com/grapefruit0205/click ·
8 会话记录：`docs/history/agent-ab-2026-09-12/`（含全部会话记录、fixture 生成器、驱动脚本）

### 预备回复

- "会不会把坏掉的测试当成通过？" → 没有凭据就不复用；任何一个输入文件变了，对应分片就重跑。
  上面的实验执行次数减半而结果不变是正向证据，计数代码被拒绝是反向证据。
- "Token 呢？" → 8 会话记录里是上升的（费用 +6~11%）；v1.1.0 简写形式另测 2 个会话，费用与关闭时持平。
  不主张节省。
- "我的项目会更快吗？" → 前提是编辑→测试循环多、测试按文件独立、套件长到能摊掉不到 1 秒的
  管理成本。不满足就照常执行，没有损失以外的损失。

## English — reference copy (forum / Show HN)

### Title

Click: the same test never runs twice, its output is never read twice, nothing is skipped by guess — 8 paired Opus 5 sessions, 381 s → 123 s

### Post

Click is an MIT plugin for Claude Code and Codex CLI. It records each check's
exact receipt, decides at execution time which checks must run again, and
replaces a skipped run with one line instead of its output. A check is skipped
only with an exact same-state receipt, a signed input observation, or a
committed owner policy; anything ambiguous runs. Model and reasoning settings
are untouched.

Measured: the same three-bug fix task, Opus 5, eight headless sessions, four
with the plugin off and four on, alternated, on a four-module 40 s `unittest`
fixture. Session time 381 s → 123 s (−68%); test module executions 20 → 10;
cost +6–11% (mostly cache reads from ~1.6 extra turns). All eight fixed every bug without touching tests.
v1.1.0's plain `click-gate verify -- <command>` form brought Click-on cost level
with Click off in two further sessions (n=2, a direction).
The gain is mostly concurrent shard execution on equal-length shards; reuse is
the halved executions and a final request that ran nothing. A two-second suite
is slower with Click (measured). JS conditional reuse: Linux + Node 22.23.2;
signed Python observation: CPython 3.12.3–3.12.14. Opus 5 followed the
directive 20/20, Haiku 4.5 0/4.

Record, transcripts and reproduction: `docs/history/agent-ab-2026-09-12/` in
https://github.com/grapefruit0205/click
