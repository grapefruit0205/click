# Click for Claude Code

This directory is the source manifest for Click's Claude Code plugin. Build the
self-contained package from the repository root:

```bash
python3 scripts/build_claude_distribution.py
```

Install the published release from the Click marketplace:

```bash
claude plugin marketplace add grapefruit0205/click
claude plugin install click@click
```

Start a new Claude Code session so the installed Hooks and skill load. To try
an unreleased build, point a session at the generated directory instead:

```bash
claude --plugin-dir ./dist/claude
```

The adapter shares Click's Evidence/Guarded/Off lifecycle, contract validation,
evidence ledger, verification classifier, resident Hook worker, and shell-free
runners with the Codex plugin. Claude Code already consumes the same
`hookSpecificOutput` wire format, so the adapter only normalizes the inbound
event: it stamps the `claude` host id, derives Click's turn identity from Claude
Code's per-prompt `prompt_id`, maps native editor and plan tools onto Click's
canonical names, and merges a rewritten command back over the original tool
input because Claude Code replaces `updatedInput` wholesale.

## Modes

- **Evidence** is the default: Claude Code retains host authority while Click
  records prompt lineage, mutation revisions, exact checks, cache lineage, and
  an approval-free receipt.
- **Guarded** adds one plain-language approval contract for higher-risk work.
  Approval must arrive in a later user turn; the `prompt_id` boundary proves
  that separation. Its canonical technical object stays hidden unless the user
  requests the original.
- **Off** leaves ordinary work unmanaged; explicit `@Click` may still start
  Guarded.

Change the persistent default from a Claude Code session:

```text
click-gate default evidence
click-gate default guarded
click-gate default off
```

## Storage

Click keeps its ledger under Claude Code's persistent plugin data directory,
`${CLAUDE_PLUGIN_DATA}` (`~/.claude/plugins/data/click-click/` for a marketplace
install). It survives plugin updates and is removed by `claude plugin uninstall`
unless `--keep-data` is passed. Preferences live in `${CLAUDE_PLUGIN_DATA}/config`.
Neither directory is shared with a Codex or Antigravity installation, so
receipts never cross hosts.

## Current platform limits

- Hook events are registered for `Bash`, `Edit`, `Write`, `MultiEdit`,
  `NotebookEdit`, `TodoWrite`, and `ExitPlanMode`. `MultiEdit` and
  `NotebookEdit` are recorded as `Edit` mutation boundaries; `TodoWrite` and
  `ExitPlanMode` receive plan advisories only. Other tools, including MCP tools
  and subagent-internal calls that emit no matching event, are outside the
  `known-surfaces-only` coverage digest that successful receipts bind.
- Subagents share the parent `session_id`. Their events join the same
  Evidence lineage when they carry the parent `prompt_id`; an event without a
  prompt identity fails closed for every turn-bound action.
- A `permissionDecision: allow` returned for a rewritten `click-gate` command
  skips Claude Code's permission prompt for that call. Every other command
  keeps the host's ordinary permission flow.
- No Claude Code Browser tool is bound to Click's Browser evidence meter. Do not
  declare `kind: browser` in a Claude Code contract.
- The `plugin://click@click` autocomplete mention is a Codex surface. On Claude
  Code, bypass and cancel use the plain first-line `@Click bypass` and
  `@Click cancel` forms.
- The Hook command is `sh "${CLAUDE_PLUGIN_ROOT}/hooks/claude_hook.sh"`, a
  shell-form hook on purpose: exec form has no shell and so no way to fall
  back between interpreters. Claude Code runs it through `sh -c` on Linux and
  macOS and through Git Bash on Windows. The POSIX launcher runs `python3`
  (then `python`) on Linux and macOS and `py -3`, `python`, `python3` in that
  order on Windows, skips the Microsoft Store alias that only offers to
  install Python and the macOS stub that only offers the command line tools,
  and starts the adapter in UTF-8 mode so a Korean prompt or the localized
  result-line label survives a legacy console code page. Click needs Python
  3.10 or newer. Without a usable interpreter, or with an older one, the
  prompt hook tells the user what to install (`systemMessage`) and tells the
  model not to use `click-gate` until a new session; tool hooks stay silent so
  no work is blocked and no error line repeats.
- Windows needs Git for Windows: Claude Code's Bash tool and its shell-form
  Hooks run there. Rewritten `click-gate` commands are rendered for Git Bash
  (forward-slash interpreter and script paths plus the bounded encoded
  transport). Without Git Bash, Claude Code offers only its PowerShell tool,
  which this package does not rewrite yet. The Codex Windows batch bridge is
  not part of this package.
- The shared runtime runs on Windows: the resident worker, receipts, sharding
  and the inbox ETW observer for Python checks. Conditional JS reuse
  (the Node inspector observer with an ETW projection) works on Windows from
  an elevated session, since the inbox `logman` sessions need it; without
  elevation checks run and same-state receipts still reuse. The native
  random-state reader needs the MSVC toolchain on PATH and the Node headers
  (MSI install or node-gyp cache); without them a Windows check that consumes
  `Math.random` or shared memory stays ineligible.
- Automatic input observation keeps its documented platform prerequisites; the
  host does not change which observer backends are available.

These limits keep unsupported host behavior explicit instead of weakening the
shared runtime or claiming feature parity that the available Hook fields cannot
prove.
