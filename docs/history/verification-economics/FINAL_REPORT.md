# Six-stage verification economics implementation — 2026-09-09

Implementation and boundaries: [maintained architecture](../../architecture/verification-economics.md).
Source base: v0.95.0; implementation branch: `feat/verification-economics`.

The implementation covers parent-relative cost selection, bounded long setup
execution, deferred Evidence child bootstrap, committed file-input policy v2,
stable Vitest/Jest grouping, shared request-local Git preflight, interruption
cleanup, current-input readiness, and an equivalent-work session driver.

## Validation

The full suite ran first. Corrected affected modules and an isolated pytest
9.1.1 environment were then checked against the complete discovery inventory:
**1,080 unique tests; 1,075 passed; 5 native Windows/macOS checks deferred;
0 unresolved failures or missing tests.**
[Validation record](evidence/validation.json) preserves the reconciliation
method. Initial failures were not discarded: interruption assumptions and
lifecycle timing fixtures were corrected; a new readiness check's module
binding and test environment were also corrected before final checks.
Distribution parity, Python compilation, whitespace, repository shard coverage
and modified maintained Markdown links passed.

## One short paired CLI session

[Raw comparison](evidence/paired-session.json) uses independent temporary
repositories, real hooks, one-use runners and precommitted owner policies.
Both arms ran the same eight fixed changes and a final full parent audit.
Preparation, transitions, failed attempts and repair count in each total.

| Measurement | Result |
| --- | ---: |
| Direct parent workflow | 710.958 ms |
| Click scoped-input workflow | 13,289.768 ms |
| Parent minus Click | **−12,578.810 ms** |
| Equivalent inputs and success/failure outcomes | Yes |
| Single-component edits | 1 child executed, 1 reused |
| Shared input or environment changes | Both children executed |
| Final unchanged request | Both children reused |

The workload is synthetic and tiny; an explicit fixture policy exercises reuse
below the default automatic-sharding parent-duration floor. Each Hook uses a
fresh CLI process, so this is not an installed resident-worker measurement.
Automatic inventory/bootstrap cost is not measured by this fixture.
No production speedup, whole-agent task-time reduction, token reduction or
tens-of-minutes saving is established.
