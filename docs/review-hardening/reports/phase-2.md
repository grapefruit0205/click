# Phase 2 — decision-to-source linkage

Status: passed. HEAD remains `d48d327c7a6836dbe11b3d331b49420b4bb1058c`, branch `codex/review-hardening-0.92.0`. This is defensive producer/consumer hardening, not a demonstrated public decision-injection vulnerability. Start state and fingerprint are Phase 1's final record; end state/diff fingerprint is in `../evidence/phase-2-policy-reuse-unit.json`. The working tree remains dirty and uncommitted.

## Responsibility and changes

`prepare` captures one context with source key, current revision, workspace root and current tree digest, and passes it to `decide`, matcher and promotion. The policy owner recomputes a canonical payload binding exact check digest, original receipt digest, old/current snapshots, changed paths, policy entry and captured context. Both policy receipt config/entry/patterns must agree and every changed path must still be allowed. Matcher retains the current contract, environment, executable and host coverage checks. Promotion repeats the pure consistency check immediately before changing source state; it performs no new file hashing. This digest is not publisher authentication.

Changed functions: click_change_policy.decide/reuse_decision_matches, click_verification_reuse.safe_change_receipt_matches/promote_safe_change_receipt, and the prepare consumer. Optional `decision_context` keyword preserves call signatures; context-free policy decisions remain available for reporting but do not grant execution reuse. Existing stored receipt format and ephemeral decision keys are unchanged. Previous/current contract IDs remain distinct: successor requalification supplies the new contract binding while preserving the old baseline facts.

The current snapshot and execution bindings are still the caller's responsibility. Phase 4 will verify the final drift boundary; hashing the decision does not make filesystem reads atomic.

## Tests and results

All commands use Linux Python 3.12.3. Exact argv, durations, dirty status and fingerprints are in each `../evidence/<name>.json`; raw `.log` is in `/tmp/click-review-hardening-20260908/`.

- `phase-2-policy-reuse-unit`: `python3 -B -m unittest tests.test_click_change_policy tests.test_click_verification -q`, 33 passed, 0 failures/errors/skips, exit 0, 2.460323 s wrapper time. Actual generated decisions are tested against changed check, original baseline, workspace/source/revision/tree context, policy, paths, current receipt, digest and status. Unbound decisions cannot be applied. Invalid promotion leaves the source unchanged.
- `phase-2-hook-red`: two tests against frozen Phase 1 Hook, both fail because an internally mocked altered digest still yields a reuse-only reply. This deterministic internal test seam does not introduce a public injection path.
- `phase-2-hook-green`: 4/5 passed; all actual denial/rerun and normal reuse behavior was correct, but the Evidence test assumed attempts persisted from the prior lifecycle. That assertion was corrected to require exactly one increment from the current pending source, preserving the actual-execution check. `phase-2-evidence-rerun-confirmed` then passed (1/1, exit 0). The final five distinct integration cases cover both-mode altered-decision real execution, ordinary committed safe-change, per-shard safe-change and Guarded successor partial reuse/new check/relevant rerun.
- Root `git diff --check` passed. Independent read-only source review found no additional actionable binding gap.

Normal reuse is retained, invalid evidence goes to an authorized real runner, and invalid execution authority is not converted into a fallback grant. No schema migration, new signing service, filesystem rehash loop, release or remote mutation was added. Native platform limitations and final generated distribution parity remain for Phase 6.

## Next

Phase 3 replaces the source-wide successor copy with the owning initializer plus explicit facts, and preserves original execution timestamps. Current dependency declarations, shards, approval and runner state must not transfer from the prior source.

Additional pure matcher evidence: `phase-2-matcher-old-regression` preserves a valid control while three changed digest/path/policy subcases fail against frozen Phase 1; the same matcher script passes in `phase-2-matcher-green-regression`. These script artifacts remain in the local evidence directory.
