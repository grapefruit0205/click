# Phase 6 report — Jest 30 automatic sharding

Status: **complete**

Phase 6 adds a separate, profile-limited Jest semantic adapter on the shared
Node collector, runtime identity, proposal, setup, parent fallback, and Gate
paths introduced for Vitest. It uses Jest's machine-readable file listing and
`--runTestsByPath` exact children. Candidate collection remains non-authoritative.

The pinned fixture covers CommonJS, Babel-transformed TypeScript, duplicate
basenames, a regex-metacharacter directory, setup files, no-op global setup and
teardown, and a committed snapshot. Static configuration and every referenced
input are identity-bound. Dynamic configuration, custom discovery,
multi-project layouts, ESM, watch/update, and related/changed selection remain
outside the profile and preserve the parent verification.

Local Linux validation executed the real pinned runner, repeated parent and
child inventory, generated exact children, exercised parent fallback, completed
automatic-sharding setup and baseline checks, and reused an unchanged Gate
receipt. The final focused regression passed 20 tests in 199.788 seconds.
macOS and Windows execution is assigned to the CI matrix and remains
unexecuted in this checkout.

See `../JEST.md`, `../CAPABILITY_PHASE6.json`, and
`../logs/phase-6-verification.md` for the exact boundary and evidence.
