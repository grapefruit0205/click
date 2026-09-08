# Phase 5 report — Vitest 5 automatic sharding

Status: **complete**

## Purpose and result

Phase 5 adds the first non-Python automatic inventory and split adapter. The
supported profile is the pinned, one-shot command
`npx --no-install vitest run` with default Vitest 5 discovery and an optional
exact test-file child. Candidate inventory, proposal, setup, committed-policy
status, refresh, baseline preparation, parent fallback, Gate execution, and
same-revision exact reuse all pass locally.

Vitest's CLI file argument is a substring filter. Click therefore accepts a
child only when `vitest list --json --run` returns tests from exactly the
requested repository-relative file. The fixture deliberately contains two
`shared.test.js` basenames in different directories; both produce distinct
child argv and exact inventory ownership.

## Execution and identity boundaries

- Provisioning uses the committed npm lockfile and may run `npm ci`. Runtime
  collection and verification always use `--no-install` and npm offline mode.
- Runtime identity binds npx, Node, the declared local Vitest launcher, package
  and installed lock state, the bounded installed dependency tree, adapter
  source, and project identity files. A transitive installed-file change
  invalidates reuse.
- Derived `.cache`, `.vite`, and `.vitest` directories do not affect runtime
  identity. Setup cost and baseline probes add `--no-cache` so they remain
  read-only; normal Gate verification preserves the original argv.
- Custom Vitest/Vite/workspace configuration and additional CLI modes are
  outside the profile. Unsupported or ambiguous conditions keep the whole
  parent verification.
- Dependency candidates are `**` for every Vitest shard. This deliberately
  avoids selective cross-revision reuse until a later phase supplies stronger
  JavaScript/TypeScript dependency evidence.

The complete profile and fallback rules are documented in `../VITEST.md`;
capability evidence is in `../CAPABILITY_PHASE5.json`.

## Verification

The final Phase 5-specific suite passed 11/11 in 69.236 seconds. A broader
automatic-sharding, proposal, setup, parent-fallback, adapter, runtime,
collector, and multi-language Gate regression passed 94/94 in 141.300 seconds
with two environment-dependent pytest skips. Distribution and repository
inventory checks passed 10/10. The generated distribution validates, Python
sources compile, `git diff --check` passes, and the official inventory contains
1,020 unique tests with no missing or duplicate IDs.

A clean temporary fixture ran `npm ci --ignore-scripts --no-audit --no-fund`
and then the pinned Vitest suite; all three tests passed. This is provisioning
evidence only. The network-independent Gate test executed the local pinned
runner once and reused its exact receipt on the unchanged revision.

See `../logs/phase-5-verification.md` for the commands. Native Linux, macOS,
and Windows Vitest integration is assigned to the new CI matrix and was not
executed from this Linux checkout.

## Preserved behavior

Existing automatic sharding `init`, `status`, and `refresh`, generated-policy
ownership, commit and baseline boundaries, parent fallback, and ordinary shard
reuse remain green. Exact, owner-safe-change, and authoritative reuse authority
rules are unchanged. Candidate collection does not create PASS or reuse
authority.

## Next condition

Phase 6 may start. The Vitest profile has a real pinned fixture, exact parent
and child inventory equivalence, setup and refresh invalidation, safe whole
parent fallback, offline runtime execution, and no regression in the existing
automatic-sharding path.
