# Review hardening — v0.93.0 release-note source

These points were incorporated into the v0.93.0 section of `RELEASE_NOTES.md` after the user explicitly authorized commit, PR, main integration, release, and reinstall. Final local verification and platform limitations remain in the Phase 6 report; the public release is conditional on the release PR's required CI.

- Reject malformed successful/current revisions consistently at evidence, reuse, claim and result boundaries while preserving revision 0 and the unverified -1 sentinel.
- Bind safe-change decisions to the exact original receipt, check and current source/workspace/revision context. This is defensive producer/consumer consistency, not a claim of a public decision-injection exploit.
- Initialize successor sources from normal defaults and copy only named facts and original measurement provenance. Current declarations, shard identity, execution state and Guarded approval remain owned by the current task. Preserve the original execution timestamp.
- Confirm inputs after tentative reuse, restore discarded promotions and run affected checks when evidence becomes stale. Keep valid exact, observed-dependency, safe-change and partial-shard reuse.
- Bind result recording and collection boundaries to the original one-use claim and current task identity. Existing atomic-write failures remain explicit and do not create successful evidence.
- During explicit POSIX termination/timeout, handle a remaining isolated process group even if its leader already exited. Windows native execution requires separate validation.
- Stream fallback-report cleanup without a directory-wide list. Preserve the one-hour TTL and reader, delete at most 128 expired writer-shaped regular files, and reach stale reports behind fresh entries. Metadata scans remain O(N); large fresh directories can take longer despite lower retained memory.

The dashboard layout, read-only retrieval/export, measurement definitions and model reasoning are unchanged. Whole-task time and token savings are unmeasured. Supported public commands, stored evidence/receipt schemas and the single legacy facade exception are unchanged; private invocation context is not a persisted extension API.
