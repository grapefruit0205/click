# Claude community marketplace listing

The submission kit for `anthropics/claude-plugins-community`, the public
marketplace where third-party Claude Code plugins land after review
(`/plugin marketplace add anthropics/claude-plugins-community`, installed as
`click@claude-community`). The official `claude-plugins-official` marketplace
is curated by Anthropic with no application process, so it is not a target.

Submission goes through one of Anthropic's in-app forms:

- Console (individual authors): <https://platform.claude.com/plugins/submit>
- claude.ai (Team or Enterprise organizations with directory management
  access): <https://claude.ai/admin-settings/directory/submissions/plugins/new>

The review pipeline runs `claude plugin validate` on the submitted directory
plus automated safety screening. An approved plugin is pinned to a commit SHA in
the community catalog, CI bumps the pin as new commits land on the submitted
ref, and the public catalog syncs nightly. Keep the text below in step with the
README and `RELEASE_NOTES.md`; the plugin `name` is an immutable slug once
published.

## Pre-flight (done for v1.3.0)

- `claude plugin validate ./dist/claude --strict` → `✔ Validation passed`.
- `click` is not used by any entry in the official or community catalog.
- The packaged directory carries its own `README.md`, MIT `license`, `author`,
  `homepage`, `repository` and `keywords` in `.claude-plugin/plugin.json`.
- No network access besides loopback ([privacy](privacy.md)); nothing is
  downloaded or installed by the plugin.

## Form values

| Field | Value |
| --- | --- |
| Name (slug) | `click` |
| Display name | Click |
| Repository | `https://github.com/grapefruit0205/click` |
| Path within the repository | `dist/claude` (source type `git-subdir`) |
| Ref | `main` — squash merges only, every merge passes the full CI matrix and distribution validation, and the packaged `version` changes only at a release, so installed users receive updates at releases |
| Homepage | `https://github.com/grapefruit0205/click` |
| Category | `testing` (alternative: `development`) |
| Author | Junseok Pak — `https://github.com/grapefruit0205` |
| License | MIT |

## Description

Incremental verification for Claude Code. Run each test or check through
`click-gate verify -- <command>`: Click records what the check actually read,
signs a receipt, and on the next request reruns only the checks whose inputs
changed while reusing the rest — one result line per reused check instead of its
full output, never a silent skip. Every decision carries a reason code, and a
local dashboard shows what ran, what was reused and where reuse was lost.
Supported unittest/pytest and Vitest/Jest suites can be split into groups so an
edit reruns only the affected groups. An optional Guarded mode binds higher-risk
work to one human-readable approval.

Requirements: Python 3.10 or newer; Linux, macOS or Windows. On Windows, Git
for Windows is required (Claude Code's Bash tool and Hooks run through Git
Bash). Observation-based reuse has platform prerequisites: Python input
observation covers CPython 3.12.3–3.12.14 on Linux (strace), macOS (privileged
fs_usage) and Windows (inbox ETW, elevated session); conditional JS reuse
covers Node 22.23.2 on Linux (strace) and Windows (inbox ETW, elevated
session), and the Windows random-state reader additionally needs the MSVC
toolchain on PATH and the Node headers (MSI install or node-gyp cache). Without
a prerequisite, checks run unchanged and only exact same-state receipts are
reused. Click installs nothing, elevates nothing and makes no network requests
(the dashboard is loopback only). MIT.

## Short description (if the form limits length)

Incremental verification for Claude Code: run checks through `click-gate verify
-- <command>`, get signed receipts of what each check read, and rerun only the
checks whose inputs changed — reused checks return one line, never a silent
skip. Python 3.10+; Linux, macOS, Windows (Git for Windows). Observation-based
reuse: CPython 3.12.3–3.12.14 and Node 22.23.2 with platform prerequisites
(strace / privileged fs_usage / elevated ETW; MSVC + Node headers for the
Windows random-state reader). No network, MIT.
