# Phase 1 — strict evidence inputs

Status: passed. Start/end HEAD remains `d48d327c7a6836dbe11b3d331b49420b4bb1058c`. Start source fingerprint was Phase 0's `564e305f…5a50`; end fingerprint is `8addbd2f21639bb3e0ed2217b6af3556e27e1622c665d7c3ef3410960822d777`. Branch `codex/review-hardening-0.92.0`; end dirty paths and full fingerprints are retained in each linked metadata file. Source snapshot for subsequent negative tests: `/tmp/click-review-hardening-20260908/phase-1-snapshot`.

## Change and classification

Reproduced input-handling defects fixed: `click_evidence` shares strict ledger validation with dynamic Evidence registration, so malformed successes cannot re-enter after loader rejection. The owning `revision_is_valid` helper requires non-bool integer >=0. `prepare` and its measurement wrapper check the current revision before any coercion. Independent exact/dependency/safe-change matchers validate both revisions; same revision equality and past<current ordering remain distinct. `ready` and other unverified state keep -1, while successful -1 and values below -1 are malformed. No historical string-revision schema was found, so no guessed migration was added.

`click_change_policy` validates paths before set/sort, handles non-encodable filesystem strings, and rejects bool/float snapshot versions. Existing policy limits, normal SHA-1/SHA-256 digests, duplicate and ordering rules remain. Promotion validates revision, counter and required payload before changing the source and raises an explicit ValueError on invalid internal inputs. This is defensive consistency; the normal caller still owns current binding checks.

Changed: hooks/click_evidence.py, hooks/click_verification_reuse.py, hooks/click_verification.py, hooks/click_change_policy.py, tests/test_click_evidence.py, tests/test_click_verification.py, tests/test_click_change_policy.py, and new tests/test_click_review_hardening.py. No schema/version bump, new dependency, authority fallback, or global int() removal.

## Validation

Linux Python 3.12.3; exact argv, environment and input fingerprints are in `../evidence/<name>.json`; original output is `/tmp/click-review-hardening-20260908/<name>.log`.

| Check | Ran | Passed | Skip | Failure/error | Exit | Wrapper elapsed |
| --- | --- | --- | --- | --- | --- | --- |
| `phase-1-reuse-unit` | 24 | 24 | 0 | 0 | 0 | 0.410687 s |
| `phase-1-policy-validation` | 19 | 19 | 0 | 0 | 0 | 1.512048 s |
| `phase-1-hook-green` | 4 | 4 | 0 | 0 | 0 | 18.870083 s |
| `phase-1-normal-reuse` | 6 | 6 | 0 | 0 | 0 | 28.173283 s |

The Hook suite covers 4 test methods and 60 malformed subcases across Evidence/Guarded and current/success revisions, including missing, bool, float, numeric string, None, nested containers, NaN, Infinity, -1 and -2. It asserts explicit denial, no runner rewrite, and unchanged evidence/revision. Rejection telemetry is permitted to change. The initial negative test on the frozen baseline also asserted byte-identical whole-state serialization, which incorrectly included permitted rejection telemetry; that assertion was replaced by specific unchanged-authority checks, not by relaxing the denial requirement. The old code's acceptance/uncaught-error findings remain independently recorded in Phase 0. Initial red run recorded 56 failed subtests across 4 methods; its old logger's negative derived pass count was a reporting bug, not an actual test count.

New policy regression on frozen old source produced 2 failures and 5 errors; raw log `phase-1-policy-old-regression.log`. The final green policy suite has 19 passing methods. Normal integration checks preserve same-revision reuse, Evidence successor, independent Guarded approval/receipt origin, Guarded partial code reuse, per-shard safe-change and failed-sibling retention. `git diff --check` passed at root.

## Next phase

Phase 2 may strengthen canonical safe-change decision/source linkage. It must not conflate origin and current contract IDs or replace valid reuse with unconditional reruns. Existing copied successor field behavior and timestamp semantics are intentionally unchanged until Phase 3. Native Windows/macOS remain unexecuted for these edits; final official regression and distribution synchronization are reserved for Phase 6. Component timings above are test duration, not performance savings or task/token measurements.
