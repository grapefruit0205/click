# Verification economics and declared file inputs

Click saves substantial wall time only when expensive unchanged verification
can be reused across repeated work. Faster Hook startup helps responsiveness;
it does not establish minutes saved in a complete development task.

## Six implementation stages

| Stage | Current behavior | Boundary |
| --- | --- | --- |
| 1. Parent-relative cost | Compare the measured parent with the largest selected child plus management reserve; reject a slower split. | One-child-change estimate, not a measured saving. |
| 2. Long checks and setup | Setup execution has a separate 30-minute default, bounded at two hours. Evidence bootstrap defers child execution to the normal baseline runner. | Collection remains bounded separately; Guarded retains its approved bootstrap comparison. |
| 3. Cross-revision inputs | Version 2 owner policy binds exact checks to unchanged file content, modes, missing files and directory membership. | Owner-declared completeness, not inferred dependency authority. |
| 4. Large inventories | Vitest/Jest can group up to 2,048 discovered files into at most 64 children, with at most 48 files per child. Refresh preserves unaffected ownership. | Static supported profiles and complete fresh inventory only. Python grouping limits remain unchanged. |
| 5. Repeated work | Child decisions share an ephemeral Git preflight. Source-only commits keep validated setup; fresh child receipts remain required. SIGTERM during managed waits cleans up the child and reaches interruption handling. | No persistent authorization cache or implicit parallel execution. SIGKILL and host crashes still need explicit recovery. |
| 6. Equivalent-work evaluation | Paired Evidence fixture sessions count preparation, transitions, failures, retries and final full audits. | Real hooks and runners, synthetic test workload; whole-agent time and tokens remain unmeasured. |

Automatic sharding init/status/refresh, complete coverage, existing v1 policy,
one-use runner claims, exact environment/command bindings, and authorized shard
reuse remain required regression checks.

## Interpreting cost fields

The estimated net per repeat is:

`parent duration - largest child duration - management reserve`.

Child duration already includes command startup. It is not charged twice.
`setup_probe_total_ms` includes the parent, startup probe and all child probes.
`estimated_probe_break_even_repeats` amortizes only those probes; it excludes
inventory, review, policy preparation and the subsequent bootstrap/baseline.
The setup report separately records `initial_setup_ms`. It remains setup cost.
Negative deltas are retained, and the cost decision never creates passing evidence.

Use `CLICK_SHARDING_EXECUTION_TIMEOUT_SECONDS` (1–7,200; default 1,800) and
`CLICK_SHARDING_EXECUTION_OUTPUT_BYTES` (1–67,108,864; default 8,388,608) for setup
execution. Invalid, non-finite and excessive values fail closed. These settings
do not extend the independent collection or proposal budget.

## Owner policy version 2

An owner may commit a complete file-content boundary before obtaining a
baseline. For example, a deterministic Vitest authentication check can declare:

```json
{
  "version": 2,
  "entries": [{
    "checks": [["npx", "--no-install", "vitest", "run", "tests/auth.test.js"]],
    "reuse_if_only_changed": ["src/", "tests/", "docs/"],
    "inputs": [
      "src/auth/", "src/shared/", "tests/auth.test.js", "tests/setup.js",
      "vitest.config.js", "package.json", "package-lock.json", "test-data/auth/"
    ]
  }]
}
```

This is an example, not a complete boundary for arbitrary projects. Declare all
transitive file inputs and common configuration, fixtures, generated artifacts
and dependency installation inputs that can affect the command. External
services, time, random state, undeclared environment and filesystem metadata
semantics are not proven by a content digest. Such checks should execute or use
a supported complete observation profile. Click does not generate or narrow
this owner authority from a static dependency graph.

Both conditions must hold: every net Git change fits the allowed envelope, and
all declared inputs still match the passing baseline. A change to `src/auth/`
reruns this child even though `src/` is in the envelope. A sibling-only edit may
reuse it. Shared input changes rerun every child declaring that input. A changed
lockfile does not by itself invalidate a complete split: affected children run.

Input capture includes ignored files and absent literal paths. Directory inputs
need a trailing slash. Repository-root wildcards, symlinks, special files,
unreadable inputs, excess resource use and detectable races cannot mint a
usable input receipt. Limits are 50,000 scanned entries and 128 MiB of content.
Pre/post execution bindings prevent input changes during execution from
producing a scoped baseline; a final fresh check also guards reuse. Missing
scoped baseline evidence cannot fall back to same-revision exact reuse.

Version 1 remains unchanged. Policy changes require a new baseline. Existing
observation precedence, contract/session, executable, environment and host
coverage checks remain mandatory. All phases use real runner results.

## Running the comparison

```sh
python3 benchmarks/incremental_verification.py --scoped-session --iterations 1 --warmups 0 --output /tmp/click-scoped-session.json
```

The output refuses overwrites. It compares the same fixed inputs and outcomes
with direct parent execution, includes a failure and repair, and finishes with
a real full parent audit in both arms. File-policy preparation and initial
baseline count in the total. Automatic inventory/bootstrap is covered by setup
integration tests and is not included in this fixture's measured setup.

Use representative external projects and complete agent-task records before
claiming production savings. A short fixture can be slower with Click; that
result is valid and is not converted into a positive estimate.

The [implementation validation and one local paired sample](../history/verification-economics/FINAL_REPORT.md)
record a negative result for a tiny CLI fixture. Its fresh Hook processes do
not measure the installed resident worker or a complete agent task.
