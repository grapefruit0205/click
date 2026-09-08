# Review hardening findings

Baseline: `d48d327c7a6836dbe11b3d331b49420b4bb1058c`, initially clean `main`; implementation branch `codex/review-hardening-0.92.0`.

| Finding | Evidence and classification | Allowed phase |
| --- | --- | --- |
| Coercive success/current revision | Reproduced in subprocess Hook after editing owner-only fixture state. Evidence source revision False/float/string can reuse; both modes coerce current revision; some malformed values raise TypeError. Not a remote attack demonstration. | 1: strict success/current revision and malformed ledger admission; keep ready -1. |
| changed_paths container validation | Mixed/nested elements raise before validation in a pure helper. No Hook skipping bypass demonstrated. | 1: validate element types before set/sort. |
| Safe-change decision provenance | Production prepare passes the same source/check/baseline to decide; matcher independently accepts altered decision digest/paths/current receipt. Defensive boundary hardening; no public decision injection route. | 2: bind canonical decision to original receipt and exact source/current conditions without new filesystem hashing. |
| Successor whole-source copy | Unknown source fields propagate; no contract/approval-token copy demonstrated. verified_at is also refreshed on reuse. Maintenance risk and timestamp semantics. | 3: current source initialization plus explicit fact allowlist; preserve actual execution time. |
| Reuse drift windows | Safe-change has a post-decision snapshot check. Exact/dependency/shared reuse-only final boundary needs deterministic coverage. Existing claim/result save guards remain. | 4: reproduce deterministic gaps first; preserve real-rerun versus authority-denial distinction. |
| Report cleanup allocation/starvation | Fallback materializes every matching Path but inspects only 128; 128 fresh entries can indefinitely hide stale tail entries. CPython glob also allocates DirEntry inventory. | 5: streaming scan, bounded retained memory/deletions, explicit scan/stat cost and TTL compatibility. |
| Distribution/platform parity | Bundled source is generated through the existing explicit builder. New code has not run on native Windows/macOS. | 6: official complete runner, partition union, generated parity, honest native limitations. |

## Resolution record

The original findings above remain the Phase 0 classification, rather than being rewritten as claims that every suspected issue was an exploitable defect.

| Item | Disposition and evidence |
|---|---|
| Revisions and adjacent container validation | Fixed at reached admission/reuse/claim/result boundaries. Valid nonnegative integer revisions and unverified -1 remain supported; malformed source/current revisions do not create passing evidence. [Phase 1](reports/phase-1.md), [Phase 4](reports/phase-4.md). |
| Safe-change decision target | Defensive canonical producer/consumer binding added without new filesystem rehashing in the matcher. Different check, original receipt or current context is refused; valid decisions and independently approved successor reuse pass. No supported public decision injection was demonstrated. [Phase 2](reports/phase-2.md). |
| Successor source copy and timestamps | New sources start from existing normal defaults and receive only named facts/measurement provenance. Current declaration/shard and lifecycle authority stay current-owned; unknown fields are excluded and verified_at remains the actual execution time. [Phase 3](reports/phase-3.md). |
| Late input drift | Deterministically reproduced stale reuse paths now pass a common fresh input/binding confirmation. Invalid evidence returns to actual verification when authority is intact. Existing safe-change post-decision checking remains. [Phase 4](reports/phase-4.md). |
| Claim/collection/result identity | Original live claim binding is retained only inside the invocation and compared before collection/result admission. Repeated claims, changed authority and malformed revisions are rejected. [Phase 4](reports/phase-4.md). |
| Explicit process termination | Reproduced surviving same-group children are terminated on Linux even after the leader exits. Separate-session children and native other-OS behavior remain outside the demonstrated guarantee. [Phase 4](reports/phase-4.md). |
| Primary/recovery write failure, replay, supported partial reuse | Existing protections verified with focused and full-run tests; no new transaction/approval system introduced. [Phase 4](reports/phase-4.md), [Phase 6](reports/phase-6.md). |
| Fallback report cleanup | Streaming iteration removes eager candidate-list allocation and fresh-prefix starvation. Successful deletions are capped at 128 and retained allocation stays small; scanning/stat work remains O(N), with measured slowdowns for large fresh directories preserved. [Phase 5](reports/phase-5.md). |
| Distribution and compatibility | Six generated hook copies synchronized through the official builder; parity/inventory checks pass. Public commands and persisted receipt/schema versions remain unchanged. Native Windows/macOS and other-Python execution remain verification constraints. [Phase 6](reports/phase-6.md). |

Final regression records retain the initial full-run fixture failure and the corrected affected-partition rerun. They must not be summarized as a single clean initial full-suite pass. Whole user-task duration and token savings remain unmeasured. Phase execution made no release; the later user-authorized v0.93.0 release handoff uses this record with its stated limits.
