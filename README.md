# Click

[![HOL Guard](https://img.shields.io/endpoint?url=https%3A%2F%2Fhol.org%2Fapi%2Fregistry%2Fbadges%2Fplugin%3Fslug%3Djunseok-pak%252Fclick%26metric%3Dtrust)](https://hol.org/go/guard/pjseok1219?dest=%2Fguard%2Fbilling%3Fpromo%3DGUARD20-PJSEOK1219%23upgrade&link_id=351107f3-00d1-4b0f-8aac-1bb449193d84&utm_source=insights_share&utm_medium=affiliate_cta&utm_campaign=share20)
[![CI](https://github.com/grapefruit0205/click/actions/workflows/ci.yml/badge.svg)](https://github.com/grapefruit0205/click/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

English | [한국어](README.ko.md) | [简体中文](README.zh-CN.md)

> Keep work inside the approved boundary, and keep valid verification reusable.

Click provides incremental verification for coding agents. It helps constrain unrequested scope expansion through a reviewed Guarded contract, while **revision-aware evidence** lets valid checks survive a new task. Reuse requires matching execution bindings and, after changes, a complete dependency observation or an explicit policy committed before the baseline. Missing authority means a real rerun, not automatic dependency inference.

Release note: v0.91.0 adds outcome-aware verification telemetry, bounded failure diagnostics, whole-task efficiency evaluation, and a responsive Korean, English, and Simplified Chinese dashboard.

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

After A completes, B receives a **new contract id and separate approval**. A's successful checks are candidates only: B must request and requalify them. Unrelated code can permit partial reuse under an unchanged precommitted policy; related inputs, changed environment, and new checks run. Approval, runner tokens, unfinished work, and completion do not transfer. The Hook does not semantically prove that every implementation stayed in scope.

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

Current release: **v0.91.0**

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

Cross-revision reuse is intentionally conservative. A repository can commit a dependency map:

~~~text
.click/evidence-dependencies.json
~~~

The map declares candidate inputs for a specific check, and concrete paths
remain hard dependencies. It does not authorize reuse by itself. In an approved
Guarded contract, an explicitly enabled authoritative run may refine expanding
patterns such as `*`, `**`, and directory prefixes to the inputs that the check
actually consumed. Click hashes every resulting input into the receipt.
Working-tree edits cannot narrow the committed policy. If observation is
unavailable or incomplete, Click runs the check again after a mutation. The map
remains optional; leaving it out also means that the check reruns.

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

For a supported project, `click-gate sharding init`, `sharding status`, and
`sharding refresh` provide a JSON-free path from command selection to a reviewed
proposal, Evidence application or separately approved Guarded application,
user-owned commit, parent/child bootstrap, and baseline evidence. A measured
cost rule keeps short suites on the parent command. Later test discovery or test
structure changes produce a bounded diff and update only policy that still
matches Click's prior committed digest lineage. User-owned or modified policy is
never overwritten, and Click does not run `git add`, `commit`, or `push`. A
successful bootstrap is setup cost; it reports `sharding-ready /
reuse-unavailable` until every child has a complete authoritative observation. See
[automatic sharding setup](skills/click/references/automatic-sharding-setup.md).
The automatic collector supports bounded unittest discovery and a conservative
pytest collect-only profile on CPython 3.10 through 3.14. Its common process and
locking adapters are implemented for Linux, macOS, and Windows; platform-native
validation status is tracked separately from implementation status.

The [two-project setting-free E2E record](docs/auto-sharding-e2e.md) shows an
actual Guarded A→B module change, partial child reuse, same-final-code audit,
and the retained negative whole-request result for short fixtures.

Observer collection is off by default and independent from the dashboard. Use
`click-gate observer off`, `click-gate observer shadow`,
`click-gate observer authoritative`, or `click-gate observer status`. Explicit
`shadow` mode attaches non-authoritative telemetry on its supported Linux,
macOS, and Windows backends; Shadow predictions never authorize a skipped
check. Explicit `authoritative` mode is available only inside a separately
approved Guarded contract for direct CPython 3.12.3 `python -m unittest`
checks. The Linux strace 6.8, macOS privileged `fs_usage`, and Windows inbox
ETW profiles have passed native-host authoritative contract and cross-contract
reuse validation on CPython 3.12.3. The setting-free automatic-sharding E2E
remains Linux-scoped. Each profile
prepares an identity-bound native companion from already installed build
inputs, runs the original check once, and authorizes reuse only for a complete
signed input snapshot. Click installs nothing or elevates no privilege. See the
[Authoritative Observer v2 contract](skills/click/references/authoritative-observer-v2.md).

Use `click-gate status` for a compact read-only JSON view of verification
progress. It distinguishes checks that ran in the current batch, checks reused
after current-condition requalification, checks that did not run, and checks
that have not been requested. It also reports which registered checks remain,
which are valid for the current mutation revision, and which a mutation
invalidated. This view cannot create reuse authority or complete a task.

Verification protocol v2 also accepts a versioned `reporting` object. The
compatibility default is `{"version":1,"format":"raw",...}`: the original
stdout/stderr still streams and Click retains only a bounded owner-readable
copy. Explicit `format:"actionable"` replaces repeated raw failure output with
the failing test identity, error, safe workspace-relative frame, truncation
state, remaining registered checks, and an opaque local detail reference.
`click-gate status` joins that diagnosis to the exact task, batch, revision and
check digest. It separately says that current registered checks are current;
it does not claim whole-task correctness. Retained output is capped per stream,
pruned by age/count/total bytes, and never becomes a receipt or reuse input.
Automatic source-code context is disabled; any suggested context read requires
its own normal read authority.

The optional `failure_collection` object is also versioned. Its default mode is
`off`, preserving source-order fail-fast. Explicit `mode:"bounded"` requires a
caller-provided list of distinct submitted evidence IDs plus limits for extra
sources, extra failures and the admission window. Click rechecks the claim,
workspace, environment and executable before each extra source. It continues
only after parser-classified test failures; setup/import errors, cancellation,
drift, unknown output and the first failure inside one source stop collection.
Automatic shards are never inferred to be independent for this policy.

During Evidence, approved Guarded work, or read-only review, Click can reuse a
complete result for one explicit local `cat`, bounded `sed -n`, or supported
`rg` request. The cache binds the request, cwd, trusted executable, relevant
environment, file contents, and a conservative directory inventory. Directory
searches also bind applicable project ignore files, so additions, deletions,
renames, and ignore changes miss the old entry and run normally. Cached output
lives only in the owner-readable plugin data directory, expires after 24 hours,
and never becomes verification evidence. Unsupported commands, ambiguous
targets, failures, output over 48 KB, missing entries, and corrupt entries use
the normal read-only runner. For an intentional same-request rerun, use
`click-gate inspect` with `"fresh":true` in its version 1 request.

Use `click-gate dashboard start`, `status`, or `stop` for actual **verification-group** outcomes, batch history and JSON/standalone HTML exports. Planned, started, reused and unstarted groups are distinct; partial processing measurements, full request wait (unknown when unmeasured), baseline-cost estimates and Shadow remain separate. The first screen keeps **Time saved** and **Token savings rate** together with the whole-task effect. Time saved is the existing estimate from actually reused groups and suitable prior successful durations. The token card and whole-task faster/unchanged/slower result accept only the Phase 4 public comparison schema; without equivalent task boundaries and complete usage they remain unmeasured. Absolute token counts and raw usage never enter the viewer, copied summary, public JSON or standalone HTML. Run `python3 benchmarks/task_efficiency.py INTERNAL.json --public-output PUBLIC.json` to build that allowlisted public file. The existing verification-interval benchmark imports remain supported. Short checks can be slower with runtime overhead. See [measurement scope, mode boundaries and exports](VERIFICATION_EFFICIENCY.md).

The dashboard language selector supports **한국어, English, and 简体中文**. The default is Korean; a browser-local preference persists across reloads of the same viewer origin. If browser storage is unavailable, language switching still works for the open page. Navigation, state explanations, dates, duration labels, comparisons and shared reports follow the selected language. Task names and user-authored check labels retain their original text. Switching language redraws cached data without running checks or changing the selected batch, filters or authority.

An opened dashboard remains attached to the same host session and workspace across successive Evidence tasks. Each verification group is persisted as soon as it finishes, so an already-passed group remains visible while the next group runs and after cancellation. Viewer connectivity does not carry Guarded approval, runner tokens, unfinished commands, or completion authority into the next task. A completed Evidence task may pass real successful results forward as **candidates only**; Click rechecks the exact source and check, workspace and mutation boundary, environment, executable, host coverage, and existing dependency or committed safe-change rules. Equal revision numbers, dashboard history, exports, timing, and Shadow predictions never authorize reuse.

## Completion receipt

Click can export a receipt after current evidence is complete:

~~~text
click-gate receipt export
click-gate receipt verify ./completion-receipt.json
~~~

The receipt binds request lineage, mutation revision, final workspace, checks, environment, executable identity, host coverage, and reuse lineage. Evidence successors use v4; an applied Guarded successor uses v5 with the origin contract, batch, revision, requalification mode and candidate digest. Merely retaining candidates does not select v5. Legacy v1–v4 remain readable; v5 retains complete shard provenance when present.

## Reproduce a completed Guarded A → B workflow

~~~sh
python3 benchmarks/incremental_verification.py --guarded-workflow --iterations 3 --warmups 1 --workload-rounds 40000 --output /tmp/click-workflow.json --html-output /tmp/click-workflow.html
~~~

This uses independent real Hook/runner fixtures, not approval in your development session. It compares no Click, explicitly selected Guarded with default reuse settings, and Guarded with precommitted shards/sibling-code policy. Evidence remains the product default mode. The fixed flow includes first run, unrelated/related code, environment change, failure, repair and unchanged retry; every step is audited against the same-state full suite. Warmups, rotating execution order, setup/transition/audit costs and negative timing differences are retained. The offline HTML and dashboard importer support the current v4 workflow report; the importer also retains v2 paired-report compatibility. Neither sample results nor unsigned exports prove universal safety or total development-time savings.

The dashboard uses a mint and teal layout with two first-screen metric cards, a count-based fallback when timing is missing, aligned verification-group blocks, and a shared zero-origin time comparison. Reuse links filter the actual groups and their canonical reasons. Selected historical requests are explicitly labeled; retained successful-request totals include their time coverage and retention window. Different N/B0/B1/B2, first-use/prepared-repeat, Evidence/Guarded and scenario scopes stay separate and require selection when more than one is present. Copy, JSON and standalone HTML share the same projected metrics and labels. Imported losses, warmups, failures, incomplete samples and additional costs remain visible in their own scope. Shared exports omit contract identifiers and prose, raw commands and logs, input paths, environment values, raw usage and absolute token counts. Request timing is partial (`hook-entry-to-result-recording`), not the full host wait. [Detailed measurement and privacy boundaries](VERIFICATION_EFFICIENCY.md).

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
- [Authoritative Observer v2](skills/click/references/authoritative-observer-v2.md), [Shadow Observer v1](skills/click/references/observer-v1.md), [Shadow Intelligence v1](skills/click/references/shadow-intelligence-v1.md), and [Evidence Shards v1](skills/click/references/evidence-shards-v1.md)
- [Anti-loop policy](skills/click/references/anti-loop-policy.md)

## License

[MIT](LICENSE)
