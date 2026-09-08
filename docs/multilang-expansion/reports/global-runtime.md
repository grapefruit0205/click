# Global runtime prerequisite report

Status: **complete**

## Scope

Implement the user's global architecture/performance prerequisite before
resuming Phase 5. Preserve existing dirty Phase 0–4 and Hook worker work.
The review and retained boundaries are in `../ARCHITECTURE_REVIEW.md`.

## Changes

- `click_verification_bindings.py`: group-local environment/payload reuse,
  explicit-input hash sharing, and one normal HEAD/tree resolution.
- `click_verification_inputs.py`: accept the caller's current hash stage while
  retaining fresh membership, file/type/sensitivity/size and race checks.
- `click_test_inventory.py`: batch Git metadata path lookup without caching
  snapshots or dropping metadata protection.
- `click_incremental.py`: linear encoded-size retention, removal of redundant
  detached copies, and a single history projection for display consumers.
- `click_dashboard_projection.py`: reuse that history view for current batch,
  visible summaries, accounting and retained totals.
- Existing binding, inventory and history test modules cover byte-compatible
  snapshots, same-mtime edits, membership changes, exact history size bounds,
  detached results and reduced call counts.

## Verification

- `global-runtime-focused.log`: 44 tests passed in 4.741s. This includes the
  new regressions and existing binding/input/history/dashboard tests.
- `global-runtime-sharding.log`: 60 tests in 86.393s, 58 passed and 2 skipped
  because this interpreter has no pytest. The real unittest setup flow,
  init/status/refresh, whole-parent fallback, and normal shard reuse passed.
- `global-runtime-boundaries.log`: 172 tests passed in 302.446s, including real
  Hook/runner Node, npm, Go and content validation, one-use claims, fresh input
  checks, state merging, result-recording failures and recovery.
- `global-runtime-integration.log`: 40 tests passed in 12.622s, including the
  session Hook worker, import bootstrap, receipts, runtime state, dashboard
  and distribution tests.
- Total: **316 tests run, 314 passed, 2 skipped, 0 failed**. The skipped tests
  are `RealCollectionTests.test_pytest_collection_is_real_repeatable_and_never_runs_tests`
  and `ProposalTests.test_pytest_nested_modules_generate_exact_children_and_regenerate`.
  They remain covered by the existing CI job pinned to pytest 9.1.1; no remote
  CI or native Windows/macOS result is claimed here.
- The canonical Antigravity distribution was regenerated. Distribution
  validation and `git diff --check` passed.

The focused command was `PYTHONPATH=tests:. python3 -B -m unittest
test_click_verification_bindings test_click_verification_inputs
test_click_incremental test_click_dashboard_projection`. The other suites used
`python3 -B`, `unittest.defaultTestLoader.loadTestsFromNames`, and
`scripts.run_ci_tests.isolated_temp()` to isolate concurrent collector caches.

## Verified reductions and retained boundaries

- Inventory snapshots now make 2 Git reads on the ordinary path instead of 6,
  covering the same metadata contents and commit identity.
- A committed verification snapshot uses 4 Git captures instead of 5 and has
  the same digest as the previous representation. Unborn and unreadable-tree
  handling remain separate.
- Common group inputs share a content read only within the current binding
  stage. Same-mtime edits, membership changes and a fresh stage force renewed
  checks; Windows continues to read each record.
- History retention encodes each bounded candidate once, and a dashboard
  request prunes/detaches history once for its summaries and accounting.

Prepare, claim, reuse confirmation and result freshness, one-use tokens, state
locks, atomic writes, recovery and actual tool execution remain intact. These
source changes are not a new release or a claim that the installed plugin has
already been upgraded.

## Next condition

The global prerequisite is complete. Resume Phase 5's limited Vitest profile
and keep its inventory proof, setup/commit/baseline and parent fallback gates.
Phase 9 remains the final full-inventory and end-to-end cost verification.

All logs are under `../logs/`. These timings describe test execution, not a
before/after product-speed measurement. Native Windows/macOS results are not
claimed from this Linux host.
