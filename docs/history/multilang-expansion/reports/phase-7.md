# Phase 7 report — mixed-project partial verification

Status: **complete**

Phase 7 adds a deterministic mixed fixture and end-to-end Gate tests for a
Vitest frontend, Python backend, Go package, shared JSON schema, Markdown, and
assets. It uses the existing version-3 explicit input contract and a committed
owner safe-change policy; no adapter candidate is promoted to authority.

The tests prove conservative all-check execution without policy, unchanged
exact reuse, frontend-only and backend-only partial reuse, Node lockfile
invalidation, new test and dynamic-import membership, shared-schema failure and
repair, and asset addition/deletion. Existing successor and runtime-identity
regressions remain the authority for independent Guarded approval and executable
replacement boundaries.

Local Linux validation ran the actual pinned Vitest runner, CPython unittest,
Go test, and Node test through Hook-to-runner batches. The final mixed-project
regression passed 2 tests in 62.464 seconds. Linux CI provisions the pinned
fixture and reruns the integration. Native macOS and Windows results remain
unexecuted for this mixed fixture.

See `../MIXED_PROJECT.md`, `../CAPABILITY_PHASE7.json`, and
`../logs/phase-7-verification.md`.
