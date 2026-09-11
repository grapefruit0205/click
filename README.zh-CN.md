# Click

[English](README.md) | [한국어](README.ko.md) | 简体中文

[![CI](https://github.com/grapefruit0205/click/actions/workflows/ci.yml/badge.svg)](https://github.com/grapefruit0205/click/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **重新验证变化的部分，复用仍然有效的结果。**

只改了登录逻辑，编码代理却又跑了一遍完整测试。
再做一个小修改，刚刚通过的检查又要重新等待。

**Click 是管理验证与复用的工作流护栏（workflow guardrail），保持你选择的
模型和推理设置不变。** 模型分析问题并选择实现方法；Click 将实际执行结果与
工作区变更关联起来，减少仍然有效的重复检查，并在复用前确认旧结果是否依然适用。

这就是**增量验证（incremental verification）**。支撑判断的
**revision-aware evidence** 记录“什么通过了，现在是否仍然有效”。
节省针对重复的工作流执行；Click 不通过更换更弱的模型或降低推理设置来节省成本。

## 使用后有什么不同？

假设项目有 12 个验证组，而这次只修改了登录代码：

```text
首次验证     → 建立 12 个组的成功基准
修改登录代码 → 执行受影响的 3 个组
             → 复用依据仍然有效的 9 个组
```

这是工作方式示例，不是性能实测。它要求分片完整，且各组有有效的输入或策略
依据。公共输入变化时可能需要执行所有组；无法确认分片完整性时执行原始完整套件。

- **减少修改之间的等待：** 执行受影响的检查，避免重复仍有效的检查。
- **解释每次决定：** 看清哪些执行了、哪些被复用，以及依据是什么。
- **让结果延续到后续任务：** 把先前成功作为候选，重新核对当前条件。
- **查看实际结果：** 在本地仪表板区分验证结果和有依据的节省估算。

Click 更适合**测试耗时长、频繁修改并验证、测试可合理分组的项目**。
如果整个套件只需两秒，设置与判断成本可能更高。目标是减少完成相同工作所需
的时间与麻烦；真实项目节省多少分钟和 Token，仍需单独测量。

## 安装与更新

Click 以 **Codex CLI** 和 **Claude Code** 插件的形式提供。两种宿主共用同一
运行时、同一证据规则和同一组 `click-gate` 命令。

### Codex CLI

```sh
codex plugin marketplace add grapefruit0205/click
codex plugin add click@click
```

重启 Codex 并新建任务，让已安装的 Hook 和技能重新加载。在依赖 Hook 之前，先通过 CLI 的 `/hooks` 页面审阅待确认的 Click Hook；详见 [Hook 故障排查](#hook-故障排查)。

更新命令：

```sh
codex plugin marketplace upgrade click
codex plugin add click@click
```

### Claude Code

```sh
claude plugin marketplace add grapefruit0205/click
claude plugin install click@click
```

新建 Claude Code 会话后，已安装的 Hook 和技能即会加载。所有 `click-gate`
命令都是普通的 Bash 命令，由已安装的 `PreToolUse` Hook 改写到 Click 运行器；
Evidence 状态保存在 `~/.claude/plugins/data/click-click/`。支持 Linux 与
macOS；宿主限制详见 [Click for Claude Code](platforms/claude/README.md)。

更新命令：

```sh
claude plugin marketplace update click
claude plugin update click@click
```

当前版本：**v0.96.1**。更新后请重启，并使用新任务。

本 README 也描述 `main` 上自动观测、条件 JS 复用与恢复的**未发布 v0.97 候选源码**。公开版本仍为 **v0.96.1**；更新该版本不会安装候选改动。详见[版本说明](RELEASE_NOTES.md)。

## 从下一次代码修改开始

安装后，可以向 Codex 或 Claude Code 这样请求：

```text
这次修改使用 Click Evidence。运行相关测试，展示哪些检查执行或复用了，
然后打开 Click 仪表板。
```

**Evidence 是默认模式。** 它沿用宿主权限，不增加额外的 Click 批准步骤。
首次成功执行建立基准，后续请求满足复用条件时才能使用该结果。
自动拆分测试是受支持项目中的独立设置步骤。

下文 `click-gate` 是由代理在 Codex 任务内执行的控制命令。对于大型套件，
可先让它用 `click-gate sharding init` 查看配置，再按照 `click-gate sharding status`
和[设置指南](skills/click/references/automatic-sharding-setup.md)的下一步提示操作。

## 在仪表板查看结果

```text
click-gate status
click-gate dashboard start
```

打开命令返回的本地 URL，查看已执行、已复用、失败和剩余检查，以及复用依据、
输入采集尚未就绪时的下一步操作。右上角可选择 **한국어 · English · 简体中文**。

避免的测试执行时间根据实际复用与过去成功耗时估算。完整任务耗时和 Token
节省在导入合适的比较资料前保持**未测量**。复用 75% 的组不等于任务快了 75%。

## 适合哪些项目？

| 项目 | 当前范围 |
| --- | --- |
| Python 后端与库 | 支持的 unittest/pytest 命令可以分片和复用。自动输入观测限于受支持的 CPython 3.12 配置。 |
| JS/TS 前端与 Node 项目 | 支持的 Vitest/Jest 套件可拆分并逐项重新判定。仅凭观测进行条件复用限于符合条件的 Linux Node 22.23.2 执行。 |
| Go 服务 | `go test` 执行及符合条件的结果复用，不提供自动测试分片。 |
| 多语言仓库 | 按注册的检查分别决定执行与复用，不声称自动发现所有跨语言依赖。 |

Rust、Java、.NET、C/C++ 等命令配置及工具 CI 范围见下方详细支持表。
执行命令、拆分测试、根据输入观测授权复用，是不同的能力。

## 自动流程是怎样的？

安装的 Hook 记录工作与执行，并在**下一次验证请求时**处理以下流程：

```text
执行请求的检查 → 记录成功结果与执行条件
修改代码       → 记录工作区变化
下一次验证请求 → 重新核对命令、环境、输入与复用依据
               → 执行必要的组 + 复用依据仍有效的组
               → 记录实际结果与原因
```

受支持的自动输入观测可以在无需项目 JSON 的情况下建立依据。条件 JS 复用
需要两次符合条件的正常请求执行来学习与核对输入。自动分片需要初次设置与
基准验证；需要安装工具或提交策略时，状态页面会提示下一步。Click 不自行安装工具。

自动化程度取决于执行工具与输入配置：

| 功能 | 适用范围 |
| --- | --- |
| 记录验证 | 默认 Evidence 模式，在宿主权限下运行。 |
| 拆分测试 | 支持的 unittest、pytest、Vitest、Jest 配置，需先设置。 |
| Python 输入观测 | 满足平台前置条件的受限 CPython 3.12 与 unittest/pytest 配置。 |
| JS 条件复用 | 符合条件的 Linux Node 22.23.2 执行，重新核对观测输入并披露采集限制。 |
| 现有仓库策略 | 保留已声明策略的复用规则，Observer 可以关闭。 |

支持的配置可以追踪设置文件、动态 import 与忽略文件，worker 和动态输入仍有
限制。[从源码生成的支持表](docs/architecture/runtime-support.md)区分执行、拆分和复用；
支持某个语言不等于三者全部支持。

<details>
<summary>展开：模式、复用规则、自动分片与 Observer 恢复</summary>

| 模式 | 行为 |
| --- | --- |
| **Evidence — 默认** | 在宿主权限下记录工作与验证，不增加额外的 Click 批准步骤。 |
| **Guarded — 按需启用** | 提出可读的合约，等待用户在后续轮次明确批准后，再执行合约内的工作。 |
| **Off** | 由宿主管理执行，不施加 Click 的工作流约束。 |

若要改变默认模式，选择以下一项：

```text
click-gate default evidence
click-gate default guarded
click-gate default off
```

明确请求 Guarded 工作：

```text
@Click 使用 Guarded 模式添加订单取消功能，并防止重复退款。
```

你可以批准合约、要求修改、取消，或查看其原始表示。后续 Guarded 任务需要自己的合约与批准。先前成功的检查经重新核验后可以成为复用候选；批准和未完成的工作不会继承。详见[运行模式](skills/click/references/modes.md)。

## 什么时候可以复用结果？

Click 会核对精确命令、工作区与变更状态、相关输入、环境、可执行文件身份，以及已知的宿主 Hook 覆盖范围。仅有历史成功或仪表板记录还不够。

| 路径 | 所需依据 |
| --- | --- |
| 同一修订 | 精确检查已有成功凭据，且当前绑定条件仍然一致。 |
| 已提交的安全变更策略 | `.click/evidence-reuse.json` 在**基准执行之前**已提交且保持不变，并允许该精确检查对应的全部净变更路径。无需 Observer。 |
| 声明的文件输入策略 v2 | 已提交策略的允许变更范围与所有者声明的完整文件输入均符合基准，包括忽略文件。这是所有者策略，不是自动依赖发现。 |
| 权威输入观测 | 来自受支持的 Evidence 自动观测或已批准的 Guarded 执行的完整、签名输入快照，并重新核对所有复用条件。 |
| 条件 JS 观测 | 符合条件的正常执行建立单独签署的观测输入凭据，后续重新核对输入与执行条件。报告明确标注输入完整性尚未得到证明。 |

例如，在 revision 12 之前，仓库已为精确的认证测试命令提交策略，允许 `README.md` 变更：

```text
revision 12  认证代码发生变化 → 执行检查并记录通过
revision 13  仅 README.md 变化 → 策略和绑定条件仍一致时复用
revision 14  认证代码发生变化 → 策略不允许该变更，重新执行
```

未列出的路径、策略变更、无法明确解释的 Git 状态、可执行文件或环境变化，以及后续工作区漂移，都要求真实执行。安全变更声明是仓库所有者的策略，不代表自动发现了依赖关系。

可选的 `.click/evidence-dependencies.json` 映射，或已批准 Guarded 合约中的依赖声明，用于指定候选输入边界。映射本身不构成权威观测依据。受批准约束的合约依赖及映射中的具体路径始终是硬依赖；完整的权威观测可以收窄映射中的扩展模式。对于这条基于观测的路径，缺失或不完整的权威依据不能支持变更后的复用。详见[验证配置与复用规则](skills/click/references/verification-profiles.md)及 [Authoritative Observer v2](skills/click/references/authoritative-observer-v2.md)。

已提交的 [Evidence Shards 映射](skills/click/references/evidence-shards-v1.md)可将一个精确的父套件拆成子项。某个子项失败时，其他已通过子项仍可在逐项复用规则允许的情况下保留结果。映射无效时会回退到原始套件。

## 自动分片：init → status → refresh

先查看元数据，或提供仓库中精确且受支持的测试命令：

```text
click-gate sharding init
click-gate sharding init -- python3 -m unittest discover -s tests -q
click-gate sharding status
click-gate sharding refresh
```

不带命令的 `init` 只读取仓库元数据，不导入项目代码、收集测试、执行套件或写入策略。带命令的初始化需要活跃的 Evidence 执行权限或已批准的 Guarded 执行范围；有边界限制的测试收集和成本测量可能会运行父命令及候选子项。较短的套件可能返回 `whole-suite-preferred`。

符合条件的提案通常按以下顺序推进：

1. 审阅提案；`refresh` 在 Evidence 模式下，或在单独批准的 Guarded 范围内，应用符合条件的策略。
2. 到达 `commit-required` 后，通过正常 Git 流程提交提案要求的精确策略内容。设置控制器不会执行 `git add`、`commit` 或 `push`。
3. `refresh` 执行父项与子项的初始化校验（bootstrap），然后报告 `baseline-required`。Bootstrap 属于设置成本。
4. `refresh` 为当前修订获取基准验证。所有子项通过后会达到 `sharding-ready`；即使 Observer 关闭，未发生变更的重复请求也可以使用精确凭据。已提交策略与权威观测的准备状态会分别显示。

每一步之间查看 `status` 并按其下一步提示操作。状态会分别显示命令执行、自动清单与拆分、精确复用、已提交策略复用和权威观测复用；其中一条路径就绪不代表其他路径也已就绪。后续测试发现结果发生变化时，会生成限定范围的 diff；刷新只更新与 Click 先前已提交内容谱系一致的策略，不覆盖用户拥有或修改过的策略。

自动清单与精确拆分已在有限定范围的 unittest、固定版本的 Vitest 5 和 Jest 30 配置中通过本地验证。保守的 pytest collect-only 配置由固定版本集成与观测 CI 覆盖；实际支持范围取决于命令和配置。Vitest 与 Jest 仅支持受限的静态配置；不支持或存在歧义的收集会保留父命令。详见[自动分片指南](skills/click/references/automatic-sharding-setup.md)及[两个项目的端到端记录](docs/history/auto-sharding/e2e.md)。

支持范围按验证工具配置管理，而不是只按编程语言名称管理。

| 工具/配置 | 执行验证依据 | 自动清单与拆分 |
| --- | --- | --- |
| CPython unittest | 已验证 | 受限配置 |
| pytest | 受限 collect-only 配置；固定版本集成 CI | 受限配置 |
| Vitest 5 / Jest 30 | 已使用固定 fixture 验证 | 受限配置、精确文件子项 |
| Node test/check、npm test、Go test | 已验证 | 仅父命令执行 |
| JSON/YAML/Markdown/SVG 项目验证器、jq | 已验证 fixture | 仅父命令执行 |
| Cargo、Gradle、.NET、TypeScript、CMake/CTest | Linux CI 工具冒烟检查；不代表 Click 完整复用集成 | 无 |
| xmllint、ImageMagick identify | Linux CI 工具冒烟检查 | 无 |
| Maven、直接 SQL linter | 识别命令与运行时配置；没有专用原生 CI fixture | 无 |

工具冒烟检查的范围小于从 Hook 到执行器再到复用的集成测试。当前 [.NET 检查](.github/workflows/ci.yml)使用类库，不能证明测试发现有效。仅识别配置不证明上述任一范围。当前检查安排见 [CI](.github/workflows/ci.yml)，历史 Phase 依据见[多语言扩展历史](docs/history/multilang-expansion/README.md)。

## Observer 可以一直关闭吗？

**可以。** 新的 Evidence 任务为受支持的检查选择自动观测，Guarded 默认关闭。Observer 关闭时，Evidence 记录、普通验证、仪表板，以及满足条件的精确凭据或安全变更复用仍可使用。明确关闭的选择会保留到同一会话中后续的 Evidence 任务。

`click-gate observer status`、`click-gate verification status` 及仪表板会显示准备失败原因、恢复操作和最近逐项检查的决策。这些只读视图不授予复用权限。[从代码生成的支持表](docs/architecture/runtime-support.md)区分普通执行、自动拆分、完整观测和条件 JS 复用，并列出平台前置条件。相关环境或工具变化后会重试准备，也可使用 `click-gate observer auto` 显式重试。

```text
click-gate observer status
click-gate observer off
```

可选模式各有用途：

- `click-gate observer shadow` 在受支持的 Linux、macOS 和 Windows 后端上收集非权威遥测。预测不会授予复用权限。
- `click-gate observer auto` 准备已安装的本地采集工具，并在相关环境或工具变化后重试，不安装工具或提升权限。没有所有者依赖策略时，完整签名输入可支持免写 JSON 的复用。观测不完整时仍执行原始检查。
- `click-gate observer authoritative` 在活动 Evidence 或已批准的 Guarded 中明确准备采集。原生配置支持 CPython **3.12.3–3.12.14** 的直接 `python -m unittest` 及受支持的 `python -m pytest` 命令。仍须满足运行时、平台和输入完整性条件；启用模式本身不授予复用权限。

输出、失败诊断与输入观测来自同一次执行。pytest 输入配置覆盖 8.4.2 和 9.1.1；缓存写入、输出捕获文件、依赖时间的插件或 worker 可能导致观测不完整。原有参数和结果保持不变。自动模式下，Node/Vitest/Jest 以有界诊断采集文件与 worker **候选信息**；符合条件的输入会在后续正常请求的执行中继续核对。原始候选不能授权复用；单独签署并重新核验的条件凭据可以允许复用，同时明确披露输入完整性尚未得到证明。参见[框架扩展与限制](docs/architecture/automatic-observation.md)。

默认 `auto` 验证会在每项检查首次实际执行时采集 Linux Node 22.23.2 的时间、随机数及共享内存调用诊断，包括 worker 和 VM 上下文。部分 API 会记录实际消费值的摘要；匹配的原生采集器还会记录各上下文的随机数状态及共享缓冲区字节样本。这些样本不代表所有 JavaScript 输入已被完整捕获。经过验证的执行凭据及已提交的仓库输入策略仍可允许自动复用；诊断信息本身不能授权 JavaScript 复用。`observer runtime` 可显式重试采集。参见[默认采集、条件复用与限制](docs/architecture/node-runtime-observation.md)。

自动观测按检查逐项判断；即使 Git 树相同，也会重新核对已观测的忽略文件。这不代表能完整发现所有语言、worker 或外部数据库输入。父命令拆分仍使用现有自动分片流程，采集不会额外重跑检查。

已有 `evidence-reuse.json` 所有者策略时，自动准备不会改变该复用路径。结构化诊断与有界后续失败收集保留与原生输入观测同一次执行的输出。

Linux strace 6.8、macOS 特权 `fs_usage` 和 Windows 内置 ETW 配置均有原生主机验证记录。自动分片端到端记录的范围是 Linux。Click 不会安装前置工具或提升权限。观测不完整时保留测试的实际结果，但不能据此建立未来复用的权威依据。详见[平台要求与验证范围](skills/click/references/authoritative-observer-v2.md)。

在受支持的 Linux Node 22.23.2 配置下，默认 JavaScript 观测可在无需所有者
JSON 的情况下生成**条件复用**凭据。两次符合条件的正常请求执行学习并核对
观测输入，后续请求再次检查。配置文件、动态 import 和忽略文件在被采集后也
会核对。环境绑定采用保守范围，一个变量变化可能使多个子检查重跑。
已知时间、随机数、共享内存输入和不支持的 worker 仍不符合条件；采集了诊断
值不等于允许复用。

已进入条件观测的子检查在新增不支持的 worker 后仍须满足观测依据要求，并会
实际执行。移除 worker 后，自动模式会在下一次正常请求执行时重试先前已启动
的 Inspector 采集，无需重置 Click 状态。仅开启新任务不会触发该恢复采集。
不支持的启动方式和纯诊断模式保留采集限制，也不会为学习而额外运行测试。
参见[条件范围与恢复](docs/architecture/node-runtime-observation.md)。


</details>

<details>
<summary>展开：测量、凭据、基准与 Hook 故障排查</summary>

## 仪表板：结果与实测效果

```text
click-gate dashboard start
click-gate dashboard status
click-gate dashboard stop
```

打开控制命令返回的本地 URL。首屏会分别展示命令、自动清单、精确复用、已提交策略和观测的准备状态，并提示下一步操作；同时展示当前任务、验证组状态、复用依据和工作历史。后续组运行期间，已完成组的结果会持续保留。同一宿主会话及工作区内，查看器可以在连续的 Evidence 任务之间保持连接。

**右上角的语言选择器**提供 **한국어 · English · 简体中文**。默认语言为韩语；本地存储可用时，浏览器会记住同一来源的语言偏好。报告跟随所选语言，用户编写的任务名称和检查名称则保留原文。

首屏卡片展示**任务净耗时**和 **Token 节省率**。导入合适的完整任务对比前，这些指标保持未测量状态。单独的**测试执行节省**一行，根据实际复用的验证组及符合条件的历史成功耗时，估算避免重复执行的时间。

| 指标 | 依据 |
| --- | --- |
| 完整任务耗时 | 相同的完成条件，以及有效的任务开始和结束边界。 |
| Token 减少比例 | 可比较的基准，以及所选任务范围内的完整用量。这与时间指标分别核验。 |
| 避免的测试执行时间 | 与实际复用绑定的历史成功耗时；属于估算，并显示覆盖范围。 |
| Hook 处理时间 | 局部运行时区间，不是宿主全部等待时间或总开发时间。 |

从内部测量文件生成可公开的完整任务对比：

```sh
python3 benchmarks/task_efficiency.py INTERNAL.json --public-output PUBLIC.json
```

在仪表板中导入 `PUBLIC.json`。这是一项需要明确执行的测量流程；Click 不会自动收集完整任务耗时或 Token 用量。缺失数据保持未测量。负收益、失败、取消、不完整样本及首次使用成本仍会显示。不同模式、基准和场景分别呈现。

分享方式包括复制摘要、公开 JSON 和独立 HTML。公开报告排除原始命令与日志、输入路径、环境值、原始用量及绝对 Token 数量。仪表板支持导入公开任务效率 v1，以及基准测试的 v4 工作流报告和 v2 配对报告，文件上限为 4 MiB；仪表板导出的 v5 JSON 不属于可导入格式。查看、导入、导出或切换语言均不授予批准或复用权限。详见[测量与隐私边界](VERIFICATION_EFFICIENCY.md)。

## 验证状态与失败反馈

通过 `click-gate status` 获取紧凑的只读视图，查看已执行、已复用、未执行或尚未请求的检查，以及变更后失效的状态。它报告已登记的验证依据，不代表整个任务的正确性。

默认保留原始输出，并按来源顺序在首次失败时停止。可选择为受支持的 unittest/pytest 输出启用便于处理的失败摘要，并通过限定大小的本地详情查看原因。可选的有界失败收集只会在指定限制内继续执行明确提交、由调用者声明相互独立的来源；不会推定自动分片相互独立。设置错误、取消、状态漂移和无法识别的输出会停止收集。详见[报告与失败收集](skills/click/references/verification-efficiency.md)。

Click 也会缓存受支持的显式本地读取，例如 `cat`、限定范围的 `sed -n` 和 `rg`。复用会绑定请求、文件内容、目录清单、忽略规则及执行条件。不支持、已过期、失败或过大的读取请求会正常执行。缓存仅保存在本地，不会成为验证依据。详见[反重复策略](skills/click/references/anti-loop-policy.md)。

## 完成凭据与可复现基准

当前验证依据完整后：

```text
click-gate receipt export
```

请代理将返回的 JSON 保存为 `completion-receipt.json`，再验证该文件：

```text
click-gate receipt verify ./completion-receipt.json
```

凭据绑定请求、修订、最终工作区、检查、执行条件、覆盖范围及复用来源。Evidence 后续任务使用 v4；实际应用复用的 Guarded 后续任务使用 receipt v5，记录重新核验的候选来源，以及存在时的分片谱系。旧版凭据仍可读取。校验结果为 **unsigned-integrity-only**：可以发现凭据内容不一致，但不证明发布者身份。

在源码检出目录中复现已完成的 Guarded A → B 对比：

```sh
python3 benchmarks/incremental_verification.py --guarded-workflow --iterations 3 --warmups 1 --workload-rounds 40000 --output /tmp/click-workflow.json --html-output /tmp/click-workflow.html
```

基准使用独立的真实 Hook/runner 测试环境，不代表当前任务获得了批准。它比较不使用 Click、使用默认复用设置的 Guarded，以及预先提交分片和安全变更策略的 Guarded；产品默认模式仍为 Evidence。流程涵盖无关与相关改动、环境变化、失败、修复及不变重试，并与相同状态下的完整套件逐步核对。设置、状态转换、审计成本、预热及更慢的结果都会保留在报告中。当前 v4 工作流报告和 v2 配对报告均可导入仪表板。这些样本不能证明普遍的开发时间或 Token 节省。

## Hook 故障排查

在 Windows 上，确认 Click 已启用，且至少一个 Python 3 启动器可用。随附启动器依次尝试 `py -3`、`python`、`python3`：

```powershell
codex --version
codex plugin list --json
py -3 --version
python --version
python3 --version
```

安装或更新后重启 Codex。在 CLI 中使用 `/hooks` 审阅并信任待确认的 Click 定义。信任绑定当前 Hook 哈希。`[features].hooks = false` 会关闭 Hook；管理员策略 `allow_managed_hooks_only = true` 会跳过插件 Hook。详见官方 [Codex Hooks 指南](https://learn.chatgpt.com/docs/hooks)。

然后新建任务，执行一个小型真实验证，并检查 `click-gate status`。仅看到插件已启用，并不能说明其 Hook 实际运行过。Windows CI 覆盖情况和原生 Observer 验证记录见[版本说明](RELEASE_NOTES.md)；它们不能替代对用户实际安装的宿主及配置的检查。

在 Claude Code 中，用 `claude plugin list` 查看已安装插件，用 `/hooks` 查看 `[plugin:click]` 的 Hook 定义；源码构建可用 `claude plugin validate ./dist/claude --strict` 检查。Hook 命令运行 `python3`，请确认 Claude Code 使用的 shell 中 `python3 --version` 可用。Hook 的输出和错误会以 `click hook error` 行出现在对话记录中。

## Antigravity

源码检出目录还提供实验性的 Google Antigravity 适配器：

```sh
agy plugin install ./dist/antigravity
```

适配器通过宿主可用的 Hook 接口支持 Evidence 与 Guarded 工作流。不受支持的覆盖范围不会被报告为独立观测。详见 [Antigravity 适配器指南](platforms/antigravity/README.md)。


</details>

## 限制与技术参考

Click 是工作流护栏，不是操作系统沙箱。它不能证明隐藏推理、语义正确性、测试充分性，或匹配的 Hook 之外的外部活动。没有独立观测的手工或托管验证依据仍属于自述证明。请保留正常的代码审查、CI、分支保护和部署控制。

协议详情与实现边界：

- [产品原则](PRODUCT_CONSTITUTION.md)与[约束分类](GUARD_CLASSIFICATION.md)
- [运行模式](skills/click/references/modes.md)与 [Guarded 合约格式](skills/click/references/directive-format.md)
- [验证配置](skills/click/references/verification-profiles.md)与[能力协议](skills/click/references/capability-protocol.md)
- [自动分片设置](skills/click/references/automatic-sharding-setup.md)与 [Evidence Shards v1](skills/click/references/evidence-shards-v1.md)
- [Authoritative Observer v2](skills/click/references/authoritative-observer-v2.md)、[Shadow Observer v1](skills/click/references/observer-v1.md)与 [Shadow Intelligence v1](skills/click/references/shadow-intelligence-v1.md)
- [文档地图](docs/README.md)、[验证效率](skills/click/references/verification-efficiency.md)、[反重复策略](skills/click/references/anti-loop-policy.md)与[运行时架构及优化](docs/architecture/runtime-optimization.md)
- [验证生命周期模块](docs/architecture/verification-lifecycle.md)：在保留现有 API 的前提下，分离准备、一次性 claim、执行与结果记录。

## 许可证

[MIT](LICENSE)
