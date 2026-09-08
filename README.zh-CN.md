# Click

[English](README.md) | [한국어](README.ko.md) | 简体中文

[![CI](https://github.com/grapefruit0205/click/actions/workflows/ci.yml/badge.svg)](https://github.com/grapefruit0205/click/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 少些重复，多些进展。

Click 为编码代理提供**增量验证（incremental verification）**，通过**感知修订状态的验证记录（revision-aware evidence）**记录哪些检查实际执行过、工作区发生了什么变化，以及先前结果是否仍然适用。Evidence 是默认模式，沿用宿主已有的权限；需要时，Guarded 模式会增加一个经明确批准的工作边界。

目标是在完成同样约定工作的前提下，减少时间、Token 消耗和人工介入。复用检查只是其中一环，不能单凭复用数量就认定整个任务完成得更快。

- **说明复用依据：** 只有执行条件和复用规则仍成立时，才保留先前的有效结果。
- **自动分片：** 为受支持的测试套件提出并维护分组；无法拆分或拆分不划算时，保留完整套件执行路径。
- **实用的验证反馈：** 展示实际执行、复用、失败和待完成的检查，并可选择汇总失败原因。
- **本地仪表板：** 使用韩语、英语或简体中文查看结果、复用依据、测量数据及可分享报告。

Click 不证明代码正确，也不证明所选测试足够充分。

## 安装与更新

通过 Codex CLI 安装：

```sh
codex plugin marketplace add grapefruit0205/click
codex plugin add click@click
```

重启 Codex 并新建任务，让已安装的 Hook 和技能重新加载。在依赖 Hook 之前，先通过 CLI 的 `/hooks` 页面审阅待确认的 Click Hook；详见 [Hook 故障排查](#hook-故障排查)。

当前版本：**v0.94.0**。更新命令：

```sh
codex plugin marketplace upgrade click
codex plugin add click@click
```

更新后请重启，并使用新任务。v0.94.0 为固定版本的 Vitest 5 和 Jest 30 配置增加了有界自动 inventory 与精确文件分片，将精确运行时和输入绑定扩展到 Node、npm、Go 与内容验证，并通过常驻 Hook worker 减少重复的 Python 启动开销。仪表板以韩语、英语和简体中文分别显示执行、自动分片、同状态精确复用、仓库所有者策略复用和权威观察。遇到不支持或不明确的发现过程时仍执行原始 parent 命令；自动分片 `init/status/refresh` 与经授权的分片复用仍是必须满足的回归标准。验证结果与测量限制见[版本说明](RELEASE_NOTES.md)和[多语言扩展记录](docs/multilang-expansion/FINAL_REPORT.md)。

## 从日常工作开始

照常向 Codex 提出请求，例如：

```text
重构认证解析器，并保持现有对外行为。
用 Click Evidence 运行仓库中相关的测试，并展示 click-gate status。
```

本指南中的 `click-gate` 是由代理在 Codex 任务内发出的 Click 控制命令。上面的安装命令则在终端中运行。

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
| 权威输入观测 | 来自受支持且明确启用的 Guarded 执行的完整、签名输入快照，并重新核对所有复用条件。 |

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

自动清单与精确拆分已在有限定范围的 unittest、固定版本的 Vitest 5 和 Jest 30 配置中通过本地验证。保守的 pytest collect-only 配置已经实现并分配给固定版本的 pytest CI，但此 checkout 的最终本地运行没有 pytest。Vitest 与 Jest 仅支持受限的静态配置；不支持或存在歧义的收集会保留父命令。详见[自动分片指南](skills/click/references/automatic-sharding-setup.md)及[两个项目的端到端记录](docs/auto-sharding-e2e.md)。

支持范围按验证工具配置管理，而不是只按编程语言名称管理。

| 工具/配置 | 实际本地执行 | 自动清单与拆分 |
| --- | --- | --- |
| CPython unittest | 已验证 | 受限配置 |
| pytest | 收集器与配置已实现；已分配固定版本 CI；最终本地运行不可用 | 受限配置 |
| Vitest 5 / Jest 30 | 已使用固定 fixture 验证 | 受限配置、精确文件子项 |
| Node test/check、npm test、Go test | 已验证 | 仅父命令执行 |
| JSON/YAML/Markdown/SVG 项目验证器、jq | 已验证 fixture | 仅父命令执行 |
| Cargo、Gradle/Maven、.NET、TypeScript/CMake/CTest、直接 SQL/XML linter | 仅识别命令与运行时配置；此 checkout 尚未验证原生执行 | 无 |

“仅识别”不代表相应原生工具链已经通过。各 Phase 的运行时与 CI 依据记录在 [`docs/multilang-expansion/`](docs/multilang-expansion/)。

## Observer 可以一直关闭吗？

**可以，关闭就是默认状态。** Observer 关闭时，Evidence 记录、普通验证、仪表板，以及满足条件的精确凭据或安全变更复用仍可使用。

```text
click-gate observer status
click-gate observer off
```

可选模式各有用途：

- `click-gate observer shadow` 在受支持的 Linux、macOS 和 Windows 后端上收集非权威遥测。预测不会授予复用权限。
- `click-gate observer authoritative` 需要单独批准的 Guarded 合约、受支持的直接 CPython **3.12.3** unittest 命令，以及平台所需的原生运行条件。仅启用模式还不够；复用需要完整的签名观测。

Linux strace 6.8、macOS 特权 `fs_usage` 和 Windows 内置 ETW 配置均有原生主机验证记录。自动分片端到端记录的范围是 Linux。Click 不会安装前置工具或提升权限。观测不完整时保留测试的实际结果，但不能据此建立未来复用的权威依据。详见[平台要求与验证范围](skills/click/references/authoritative-observer-v2.md)。

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

## Antigravity

源码检出目录还提供实验性的 Google Antigravity 适配器：

```sh
agy plugin install ./dist/antigravity
```

适配器通过宿主可用的 Hook 接口支持 Evidence 与 Guarded 工作流。不受支持的覆盖范围不会被报告为独立观测。详见 [Antigravity 适配器指南](platforms/antigravity/README.md)。

## 限制与技术参考

Click 是工作流护栏，不是操作系统沙箱。它不能证明隐藏推理、语义正确性、测试充分性，或匹配的 Hook 之外的外部活动。没有独立观测的手工或托管验证依据仍属于自述证明。请保留正常的代码审查、CI、分支保护和部署控制。

协议详情与实现边界：

- [产品原则](PRODUCT_CONSTITUTION.md)与[约束分类](GUARD_CLASSIFICATION.md)
- [运行模式](skills/click/references/modes.md)与 [Guarded 合约格式](skills/click/references/directive-format.md)
- [验证配置](skills/click/references/verification-profiles.md)与[能力协议](skills/click/references/capability-protocol.md)
- [自动分片设置](skills/click/references/automatic-sharding-setup.md)与 [Evidence Shards v1](skills/click/references/evidence-shards-v1.md)
- [Authoritative Observer v2](skills/click/references/authoritative-observer-v2.md)、[Shadow Observer v1](skills/click/references/observer-v1.md)与 [Shadow Intelligence v1](skills/click/references/shadow-intelligence-v1.md)
- [验证效率](skills/click/references/verification-efficiency.md)、[反重复策略](skills/click/references/anti-loop-policy.md)与[运行时架构及优化](docs/runtime-optimization.md)

## 许可证

[MIT](LICENSE)
