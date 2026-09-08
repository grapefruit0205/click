# Phase 2 report — reuse policy and explicit input freshness

Status: **complete**

## Purpose and result

Phase 2 adds an end-to-end distinction between normal conditional reuse, a
one-time rerun, and a persistent always-run profile. It also adds bounded local
input snapshots and a required-output condition without changing who may
execute a command. Protocol v2 remains compatible; v3 owns the new fields.

## Changed call paths

- `click_verification_plan.py` validates v2/v3 separately and normalizes one
  policy for every adjacent evidence group.
- `click_verification_inputs.py` collects private, bounded content and glob-set
  bindings while rejecting unsafe paths, sensitive matches, symlinks, races,
  non-files, and resource-limit overflow.
- `click_verification_bindings.py` folds an explicit input digest into the
  environment/context binding used by existing receipt comparisons.
- `click_verification.py` persists restrictive source policy, forces affected
  checks out of all exact/dependency/safe-change/successor reuse paths, accepts
  a valid forced current source through `repeated_keys`, rechecks inputs at
  claim, collection, and final result boundaries, and records a new digest only
  after a successful unchanged run.
- `click_evidence.py` validates the optional ledger fields and conservatively
  carries them across successor and automatic shard activate/collapse paths.
- `click_incremental.py` accepts six stable reasons for the new run decisions.
- The generated distribution and official shard inventories contain the new
  module and 15 tests.

The full contract and compatibility defaults are documented in
`../REUSE_POLICY_CONTRACT.md`. The proposed measured lifecycle for a reusable
Python analysis worker is recorded in `../ANALYSIS_WORKER_LIFECYCLE.md`.

## Verification

The Phase 2 focused tests passed 15/15. The wider verification authority group
passed 305/305. Automatic sharding init/status/refresh, proposal, shard reuse,
and parent fallback passed 59 tests with two existing environment skips. The
distribution and repository shard checks passed 10/10. The official inventory
contains 975 unique tests. See `../logs/phase-2-verification.md`.

## Remaining limits

- `outputs_required` forces execution because Click has no artifact restoration
  protocol; it does not claim that a build product was restored.
- External DB/API/time/random or unobservable machine inputs need an owner
  always-run declaration until a later adapter can model them safely.
- Explicit inputs are local repository-relative files only. They do not grant
  access to external paths and do not add a remote cache.
- Native Windows and macOS execution remains CI evidence, not a local claim.
- The long-running analysis worker is a measured follow-up, not a Phase 2
  runtime service.

## Next condition

Phase 3 may start because default exact reuse still works, stricter requests
force real execution without bypassing authority, stale PASS cannot survive a
failed forced run, successor and shard restrictions are preserved, and the
automatic-sharding acceptance criterion passes.
