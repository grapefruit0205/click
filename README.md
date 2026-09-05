# Click

[![HOL Guard](https://img.shields.io/endpoint?url=https%3A%2F%2Fhol.org%2Fapi%2Fregistry%2Fbadges%2Fplugin%3Fslug%3Djunseok-pak%252Fclick%26metric%3Dtrust)](https://hol.org/go/guard/pjseok1219?dest=%2Fguard%2Fbilling%3Fpromo%3DGUARD20-PJSEOK1219%23upgrade&link_id=351107f3-00d1-4b0f-8aac-1bb449193d84&utm_source=insights_share&utm_medium=affiliate_cta&utm_campaign=share20)
[![CI](https://github.com/grapefruit0205/click/actions/workflows/ci.yml/badge.svg)](https://github.com/grapefruit0205/click/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

English | [한국어](README.ko.md) | [简体中文](README.zh-CN.md)

> Incremental verification for coding agents.

Click keeps passing checks reusable until the code they depend on actually changes. Its **revision-aware evidence** lets the runtime rerun only the checks affected by the current edit instead of blindly repeating the whole verification set.

Click does not prove that the code is correct or that the selected tests are sufficient. It tracks whether existing verification evidence still applies to the current code.

You keep working normally. Click remembers:

- what the agent was asked to do;
- when the workspace changed;
- which checks really ran;
- whether an old result is still safe to reuse.

It does not tell the model how to think or which files to read.

## The problem in one example

~~~text
revision 12  auth code changed   → auth tests run and pass
revision 13  README changed      → auth inputs unchanged, reuse the result
revision 14  auth code changed   → old result is stale, run the tests again
~~~

Without a revision-aware record, an agent may trust an old test after the code changed or rerun a large suite after an unrelated edit. Click keeps the result only while the inputs that made it valid still match.

That is the core of Click.

## How it feels to use

Click has three modes:

| Mode | Use it for | What you see |
| --- | --- | --- |
| **Evidence** (default) | Everyday coding | No Click approval step. Work normally and receive an evidence receipt. |
| **Guarded** | Risky or tightly bounded changes | Review one short contract before the agent can change anything. |
| **Off** | Work where Click is not needed | The host handles execution on its own. |

### Evidence: the normal default

Evidence mode uses the permissions already provided by Codex or the host. Click does not pretend it approved the work.

The final receipt says:

~~~text
approval_bound: false
execution_authority: host
~~~

### Guarded: one approval when the boundary matters

Use Guarded for payments, authentication, deletion, migrations, public API changes, or any task where changing the wrong thing would matter.

The approval view starts with one plain-language contract, not a list of developer fields. For example:

~~~text
Revision 12 changed src/auth/token.py.
The authentication tests that used this file are affected.
The previous result is now stale, so those tests must run again.
Click will record the changed revision, the affected check, why the old result became stale, and the completion checks.
It only displays this information; the contract excludes test-skipping authority, UI work, and external transmission.
In short: this builds the safe data layer that a future Evidence Map can read.
~~~

The original canonical JSON stays hidden unless the user requests the original contract. Viewing it keeps the same contract id and does not approve, change, or restage anything. The approval prompt is equivalent to:

> The contract above is explained in plain language. Do you approve it as written, or would you like to see the original contract first?

Approve, request changes, cancel, and view original are all available. Approval happens in a later user turn, and work inside the approved boundary continues without repeated approval prompts.

## Install

~~~bash
codex plugin marketplace add grapefruit0205/click
codex plugin add click@click
~~~

Restart Codex so its Hooks reload, then start a new task.

New installations use Evidence mode. You can change the default:

~~~text
click-gate default evidence
click-gate default guarded
click-gate default off
~~~

Then ask for work normally:

~~~text
Refactor the authentication parser and keep its public behavior unchanged.
~~~

Or explicitly choose Guarded:

~~~text
@Click Add order cancellation and prevent duplicate refunds.
~~~

## Update

Current release: **v0.81.1**

~~~bash
codex plugin marketplace upgrade click
codex plugin add click@click
~~~

Start a fresh task after updating.

See [release notes](RELEASE_NOTES.md) for version history.

## What makes evidence reusable?

A result is reused only when its important bindings still match, such as:

- the exact check;
- the relevant files and their contents;
- the workspace state;
- the environment and executable;
- the known host Hook coverage.

If Click cannot establish that match, it runs the check again.

Cross-revision reuse is intentionally conservative. Evidence mode uses a committed dependency map:

~~~text
.click/evidence-dependencies.json
~~~

The committed map authorizes reuse for a specific check, and concrete paths remain hard dependencies. When the baseline observation is complete, expanding map
patterns such as `*`, `**`, and directory prefixes are refined to the inputs
the check actually consumed; observed inputs are then hashed into the receipt.
Working-tree edits cannot narrow the committed policy. If observation is
unavailable, fails, sees an external input, or cannot cover the full child-
process tree, Click runs the check again after a mutation. The map remains
optional; leaving it out also means that the check reruns.

For common changes that are known not to affect a check, such as documentation,
the repository may instead commit an observer-free safe-change policy:

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

Save it as `.click/evidence-reuse.json`. After a successful baseline, Click
records the Git commit plus compact fingerprints for effective uncommitted
files. Before the same exact check runs again, it reports the net changed paths.
It reuses the result only when every path matches the unchanged committed policy;
any unlisted path, policy edit, Git ambiguity, environment or executable change,
or later workspace drift runs the real check. Policy files cannot declare
themselves safe. This path uses only Git and the plugin's Python runtime, so it
does not require a platform-specific observer or another installation on Linux,
macOS, or Windows. The declaration is repository-owner policy, not an inferred
claim that Click discovered every dependency.
A committed [Evidence Shards map](skills/click/references/evidence-shards-v1.md) can split one exact broad suite into independent children, retaining a passed sibling after another fails. The map alone never permits reuse after a mutation; the rules above still decide each child, and an invalid map runs the original suite.

Observer collection is off by default and independent from the dashboard. Use `click-gate observer off`, `shadow`, or `status`; only explicit `shadow` mode attaches a trusted native collector to compatible real checks. Linux uses `strace`, privileged macOS uses `fs_usage`, and Windows uses the inbox `logman.exe` and `tracerpt.exe` ETW tools. Click installs nothing or elevates no privilege. Shadow predictions never authorize a skipped check.

Use `click-gate dashboard start`, `status`, or `stop` for actual **verification-group** outcomes, batch history and JSON/standalone HTML exports. Planned, started, reused and unstarted groups are distinct; partial processing measurements, full request wait (unknown when unmeasured), baseline-cost estimates and Shadow remain separate. Run `python3 benchmarks/incremental_verification.py --iterations 3 --warmups 1 --output /tmp/click-comparison.json`, then select that JSON in the viewer for a real hook/runner comparison. Short checks can be slower with runtime overhead. See [measurement scope, mode boundaries and exports](VERIFICATION_EFFICIENCY.md).

An opened dashboard remains attached to the same host session and workspace across successive Evidence tasks. Each verification group is persisted as soon as it finishes, so an already-passed group remains visible while the next group runs and after cancellation. Viewer connectivity does not carry Guarded approval, runner tokens, unfinished commands, or completion authority into the next task. A completed Evidence task may pass real successful results forward as **candidates only**; Click rechecks the exact source and check, workspace and mutation boundary, environment, executable, host coverage, and existing dependency or committed safe-change rules. Equal revision numbers, dashboard history, exports, timing, and Shadow predictions never authorize reuse.

## Completion receipt

Click can export a receipt after current evidence is complete:

~~~text
click-gate receipt export
click-gate receipt verify ./completion-receipt.json
~~~

The receipt binds request lineage, mutation revision, final workspace, checks, environment, executable identity, host coverage, and reuse lineage. When a later Evidence task requalifies and applies an earlier success, receipt v4 records `successor-reused` lineage with the origin Evidence session and batch, origin revision, requalification mode, and candidate digest.

Receipt verification currently reports **unsigned-integrity-only**. It detects accidental or uncoordinated changes to the receipt, but it does not yet prove the publisher's identity.

## What Click enforces

Click keeps hard rules for things that need runtime integrity:

- approval and contract identity in Guarded mode;
- one-use execution claims and replay protection;
- mutation revisions and stale-evidence invalidation;
- exact verification receipts;
- managed local service cleanup;
- receipt integrity.

Suggestions about exploration, retries, planning, and verification depth remain advice. They do not block the model's search strategy.

## Antigravity

The repository also ships an experimental Google Antigravity adapter:

~~~bash
agy plugin install ./dist/antigravity
~~~

The adapter supports the same Evidence and Guarded model through Antigravity's available Hook surface. Host coverage is reported honestly; unsupported paths are not described as independently observed.

See [the Antigravity adapter guide](platforms/antigravity/README.md).

## Honest limits

Click is a workflow guardrail, not an operating-system sandbox.

It cannot prove hidden reasoning, semantic correctness, unmatched external tool activity, or the quality of a test chosen by the model. Hosted and manual evidence outside a matched Hook is recorded as an attestation rather than independent observation.

Use normal code review, CI, branch protection, deployment controls, and security boundaries where they belong.

## Technical reference

The README stays short on purpose. Protocol and architecture details live here:

- [Product Constitution](PRODUCT_CONSTITUTION.md)
- [Guard classification](GUARD_CLASSIFICATION.md)
- [Operating modes](skills/click/references/modes.md)
- [Guarded contract format](skills/click/references/directive-format.md)
- [Verification profiles](skills/click/references/verification-profiles.md)
- [Capability protocol](skills/click/references/capability-protocol.md)
- [Shadow Observer v1](skills/click/references/observer-v1.md), [Shadow Intelligence v1](skills/click/references/shadow-intelligence-v1.md), and [Evidence Shards v1](skills/click/references/evidence-shards-v1.md)
- [Anti-loop policy](skills/click/references/anti-loop-policy.md)

## License

[MIT](LICENSE)
