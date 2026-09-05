# Click — 面向编码代理的增量验证

[English](README.md) | [한국어](README.ko.md) | 简体中文

[![CI](https://github.com/grapefruit0205/click/actions/workflows/ci.yml/badge.svg)](https://github.com/grapefruit0205/click/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 代码只改一点时，不再重复全部验证，只重新运行真正受影响的检查。

Click 的核心是 **Incremental verification for coding agents**。只要某项已通过检查所依赖的代码没有变化，它的结果就可以继续复用；判断依据记录为 **revision-aware evidence**。

Click 不会证明代码正确，也不会证明所选测试足够充分。它只跟踪已有验证结果是否仍适用于当前代码。

你可以照常让 AI 工作。Click 会记住：

- 用户提出了什么请求；
- workspace 何时发生变化；
- 哪些检查真正执行过；
- 旧结果现在能否安全复用。

Click 不规定模型如何思考，也不限制它读取文件的顺序。

## 一个例子

~~~text
revision 12  修改认证代码 → 运行认证测试 → 通过
revision 13  只改 README   → 认证输入未变 → 复用结果
revision 14  再改认证代码 → 旧结果过期 → 重新测试
~~~

没有可靠记录时，代理可能在代码变化后继续相信旧测试，也可能因为无关改动重新运行很大的测试套件。

Click 只会在证明所依赖的输入仍然一致时复用结果。无法确认时，就重新检查。

这就是 Click 最重要的功能。

## 三种模式

| 模式 | 适用场景 | 使用体验 |
| --- | --- | --- |
| **Evidence**（默认） | 日常编码 | 不增加 Click 批准步骤，正常工作并获得 evidence receipt。 |
| **Guarded** | 付款、认证、删除等边界重要的工作 | 先确认一份简短 contract，再在范围内执行。 |
| **Off** | 不需要 Click 的工作 | 执行权限完全交给 host。 |

### Evidence：日常默认

Evidence 使用 Codex 或 host 已提供的权限。Click 不会假装自己批准了任务。

receipt 会明确写出：

~~~text
approval_bound: false
execution_authority: host
~~~

### Guarded：风险较高时使用

用户首先看到的是一份通俗易懂的合同，而不是开发者字段列表。例如：

~~~text
Revision 12 修改了 src/auth/token.py。
使用这个文件作为输入的认证测试会受到影响。
以前的测试结果已经过期，现在需要重新运行。

Click 会记录哪一版修改了什么、哪些检查受影响、旧结果为何失效，
以及怎样确认工作完成。这些数据只用于说明状态，不会授权跳过测试。
这份合同不包含 UI、自动跳过测试或向外部发送数据。

一句话概括：先建立一个让未来 Evidence Map 可以安全读取的数据层。
~~~

只有用户要求查看原始合同时，才显示规范 JSON。查看原文不会批准、修改
或重新暂存合同，并且继续使用同一个合同 ID。最后的问题等价于：

> 上面的合同已经用通俗语言说明。您要按当前内容批准，还是先查看原始合同？

用户可以选择批准、要求修改、取消或查看原文。批准发生在后续用户 turn；
批准后，原范围内的细节调整无需反复批准。

## 安装

~~~bash
codex plugin marketplace add grapefruit0205/click
codex plugin add click@click
~~~

重启 Codex 以重新加载 Hook，然后开始新任务。

新安装默认使用 Evidence。

~~~text
click-gate default evidence
click-gate default guarded
click-gate default off
~~~

Evidence 模式下直接提出普通请求即可：

~~~text
重构认证解析器，并保持公开行为不变。
~~~

也可以明确选择 Guarded：

~~~text
@Click 添加订单取消功能，并防止重复退款。
~~~

## 更新

当前版本：**v0.81.1**

~~~bash
codex plugin marketplace upgrade click
codex plugin add click@click
~~~

更新后请开始一个新任务。

版本历史见 [Release Notes](RELEASE_NOTES.md)。

## evidence 何时可以复用？

以下信息必须继续匹配：

- 执行的检查；
- 与检查相关的文件和内容；
- 当前 workspace 状态；
- 环境和可执行文件；
- host Hook coverage。

任何一项不明确，Click 都会重新执行检查。

跨 revision 复用可以选择使用已提交的依赖映射：

~~~text
.click/evidence-dependencies.json
~~~

已提交的映射决定每项检查的复用权限。明确列出的文件始终是硬依赖；当
baseline 观察完整时，`*`、`**` 和目录前缀等扩展模式会收窄到检查实际读取的
输入，并一起写入 receipt 的哈希。仅存在于工作区的映射修改不能缩小已提交的
策略。如果观察不可用、失败、读取了外部输入，或未覆盖完整的子进程树，Click
会在 mutation 之后重新执行检查。该文件仍非必需；没有映射时也会重新检查。

对于 README 或文档等仓库明确知道不会影响某项检查的改动，可以提交无需
observer 的安全改动策略：

~~~json
{
  "version": 1,
  "entries": [
    {
      "checks": [["python3", "-m", "pytest", "tests/unit"]],
      "reuse_if_only_changed": ["README.md", "docs/**"]
    }
  ]
}
~~~

文件名为 `.click/evidence-reuse.json`。首次检查成功后，Click 会记录 Git commit
以及当时有效的未提交文件指纹。再次执行完全相同的检查前，它会报告基线与当前
状态之间的净变更路径；只有所有路径仍匹配同一个已提交策略时才复用结果。未列出
的路径、策略修改、无法解释的 Git 状态、环境或可执行文件变化，以及 mutation
后的额外漂移都会触发真实检查。策略文件不能把自身声明为安全路径。该流程只用
Git 和插件自带的 Python，因此 Linux、macOS 与 Windows 都无需另装平台 observer。
此列表是仓库所有者的明确策略，并不表示 Click 自动发现了全部依赖。
已提交的 [Evidence Shards 映射](skills/click/references/evidence-shards-v1.md)可把一个精确 broad suite 拆成独立子项，并在后续 shard 失败时保留先前通过结果。该映射本身不能授权 mutation 后复用；上述规则仍逐项生效，映射无效时会执行原始 suite。

Observer 默认关闭，并且与 Dashboard 独立。使用 `click-gate observer off`、
`shadow`、`status` 控制；只有明确开启 `shadow` 后，兼容的真实检查才会附加原生
收集器。Linux 使用 `strace`，已有权限的 macOS 使用 `fs_usage`，Windows 使用系统
自带的 ETW 工具 `logman.exe` 与 `tracerpt.exe`。Click 不安装工具，也不提升权限。
Shadow 预测本身绝不会授权跳过检查。Dashboard 分开显示真实执行、获得权威授权的
exact/dependency/policy 复用、根据最近运行估算的避免时间，以及 Shadow 潜在值。
使用 `click-gate dashboard start`、`status`、`stop` 打开、查看或关闭。

运行 `python3 benchmarks/incremental_verification.py --iterations 3 --warmups 1 --output /tmp/click-comparison.json` 可通过真实 Hook 和 runner 进行本地配对比较，再在 Dashboard 中选择 JSON。界面按“验证组”区分计划、实际执行、复用和未执行，提供批次时间线与 JSON/独立 HTML 导出；局部实测、未测量的完整等待时间、历史成本估算和 Shadow 分开显示。短检查可能因管理成本而变慢。详见[计量范围与使用说明](VERIFICATION_EFFICIENCY.md)。

## 完成 receipt

当前代码所需的 evidence 完整后，可以导出并验证 receipt：

~~~text
click-gate receipt export
click-gate receipt verify ./completion-receipt.json
~~~

receipt 会绑定请求链路、mutation revision、最终 workspace、检查结果、环境、可执行文件、host coverage 和复用来源。

当前验证报告为 **unsigned-integrity-only**：可以检查 receipt 是否被修改，但还不能证明发布者身份。

## Click 强制保证什么？

- Guarded 的批准和 contract ID；
- one-use 执行与 replay 防护；
- mutation revision 与过期 evidence 失效；
- 实际验证结果的 receipt；
- managed service 清理；
- receipt 完整性。

探索次数、计划方式、重试次数和模型推理策略不会被阻断，只会在必要时收到建议。

## Antigravity

仓库还包含实验性的 Google Antigravity 适配器：

~~~bash
agy plugin install ./dist/antigravity
~~~

适配器在 Antigravity 提供的 Hook 范围内支持 Evidence 和 Guarded。无法观察的路径不会被描述成已经独立观察。

详见 [Antigravity 适配器说明](platforms/antigravity/README.md)。

## 限制

Click 是 workflow guardrail，不是操作系统 sandbox。

它不能证明隐藏推理、语义正确性、未接入 Hook 的外部工具行为，也不能判断模型选择的测试是否足够好。请继续配合代码审查、CI、branch protection 和部署控制。

## 技术文档

README 有意保持简单。协议和架构细节放在以下文档：

- [产品宪法](PRODUCT_CONSTITUTION.md)
- [Guard 分类](GUARD_CLASSIFICATION.md)
- [运行模式](skills/click/references/modes.md)
- [Guarded contract 格式](skills/click/references/directive-format.md)
- [验证 profile](skills/click/references/verification-profiles.md)
- [Capability protocol](skills/click/references/capability-protocol.md)
- [Shadow Observer v1](skills/click/references/observer-v1.md)、[Shadow Intelligence v1](skills/click/references/shadow-intelligence-v1.md) 与 [Evidence Shards v1](skills/click/references/evidence-shards-v1.md)
- [Anti-loop policy](skills/click/references/anti-loop-policy.md)

## 许可证

[MIT](LICENSE)
