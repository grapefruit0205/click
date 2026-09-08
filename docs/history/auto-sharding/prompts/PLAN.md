# Automatic sharding and cross-contract reuse — execution plan

Status: Phase 0 specification; implementation and proof are recorded separately in
`docs/auto-sharding/progress.json`. This plan grants no execution, commit or reuse authority.

## Authority and workspace

- Work in `/home/grapefruit/Documents/Codex/2026-09-06/click-live-0-81-0-codex/work/click-release`, current main `ea7ce2c0073da4031a6bf0dc9d2f37f7094d6e1a`, selected explicitly by the user.
- The initial worktree has 15 modified tracked files in timing, dashboard, benchmark and their tests/distribution, plus `docs/revalidation-savings-plan.md`. Preserve them. Initial byte hashes are stored outside this repository in `/home/grapefruit/docs/auto-sharding-session/initial-worktree.json`.
- No repository or ancestor AGENTS.md was found. Recheck when the target changes. PRODUCT_CONSTITUTION.md and GUARD_CLASSIFICATION.md remain the product/authority rules.
- Phase documents are the authoritative execution specifications. Read the entire corresponding document on every phase entry. This plan does not replace their requirements.
- Phase 0–2 contract: `ctr_dba22b2e9573102454b7b6c143c7b42d`, staged and passed by the actual Hook after a later explicit user approval. Query actual state on resume; checkpoint text is never a substitute.
- The goal/continuation is not approval. Phase 3–5 needs a separate later-turn approved contract. Never inherit approval, a runner capability, unfinished work or completion.
- The workflow-source checkpoint at `/home/grapefruit/docs/auto-sharding/progress.json` and the repository checkpoint are synchronized. The repository copy is the implementation checkpoint; actual Hook/evidence state outranks both.
- No automatic add/commit/push, installed Hook edits, dependency installation, deployment or publishing. Test-owned disposable Git fixtures are isolated from the user index; simulated authority is only test evidence, never live product approval.

## Existing implementation, gaps and preserved user work

| Area | Current evidence | Disposition |
| --- | --- | --- |
| Committed shards | `hooks/click_evidence_shards.py` loads HEAD policy, verifies working-copy equivalence, inventory partition and executable child groups. `click_verification._expand_evidence_shards` preserves parent identity and child receipts. | Reuse schemas and runtime; automatic generator is missing. File coverage alone does not prove collected-test equivalence. |
| Dependencies | `hooks/click_dependency_cache.py` validates committed declarations and complete runtime observations; expanding patterns may be refined to observed inputs. | Candidate graph cannot populate this authority ledger. |
| Native observer | `click_dependency_trace` and Linux/macOS/Windows collectors currently produce Shadow records. The production verification branch is controlled by Shadow/off. | No production authoritative supplier was found in that branch. Phase 3 must introduce a separately admitted run and real backend proof. Do not convert Shadow records. |
| Successor | `click_lifecycle._carry_completed_candidates`, `click_evidence`, `click_verification._requalify_successor_baseline`, receipt v5. | Reuse current requalification, preserve current contract authority; no global/remote cache. |
| Timing/UI | Existing dirty changes add timing baseline v2, actual-batch savings, projection v5 and benchmark/UI reporting. | Preserve this implementation and assess its current tests. Do not preimplement the later dashboard phase or call unverified dirty work complete. |
| CLI/capabilities | Direct shell-free inspect/mutate/verify controls, one-use runner claims and protected verification snapshots already exist. | Analysis and proposals use the existing approved mutation capability; do not invent a weaker read-only import path or another approval mechanism. |
| Packaging | `scripts/build_antigravity_distribution.py` has explicit runtime/reference lists; `validate_distribution.py` and unittest distribution/policy tests compare bundled files. | Add new modules/references to those lists and synchronize only relevant generated files, preserving original changes. |

No tests have yet established completion of a new phase. Source inspection is not a test pass.

## Initial supported profile

The first implementation and positive-validation target is standard **CPython
3.12.3 on Linux**, with a local Git project and standard unittest discovery.
The product's existing Python 3.10+ runtime compatibility is retained for importing
the plugin; an analysis interpreter outside the tested profile returns
`unsupported-runtime` instead of pretending support. The exact patch version can
be broadened only with matching evidence and a documented support update.

| Surface | Initial profile | Outside profile |
| --- | --- | --- |
| Parent command | Explicit argv: `python3 -m unittest discover` (or trusted `python`/absolute interpreter resolving outside the target repository), with `-s/--start-directory`, `-t/--top-level-directory`, `-p/--pattern`, repeated `-k`, and `-q` or `-v`. Standard positional start/pattern/top arguments may be normalized according to unittest parsing. | Shell strings, Python eval, wrapper/package-manager commands, arbitrary flags, implicit guessed commands and other frameworks are rejected or selection-required. |
| Defaults | Start `.`, pattern `test*.py`, top-level equal to the supplied start when omitted, as in CPython. All paths remain inside the exact Git root and run cwd. | Traversal to another project, external symlinks and ambiguous paths are unsupported. |
| Tests | Standard TestCase/TestSuite/TestLoader collection; module is the smallest split unit. Package discovery and repeated filters retain their actual semantics. | load_tests, custom loader/suite/id behavior, dynamic generated cases or unknown shared state are explicitly classified; no silent split. |
| Candidate analysis | Python AST imports, repository-resolvable transitive modules, shared test helpers, literal internal file/config/data references and stable unknown reason codes. | Dynamic imports/paths, subprocess, network/DB, time/random, IPC and unresolved file access remain unknown. |
| Collection | Fresh subprocess with the selected interpreter, target cwd and equivalent import path; no test methods run. Use the approved mutate runner. | Import/collection failure, unstable/duplicate IDs, 0 tests, workspace change, timeout/output/resource limit invalidates analysis. |
| Authoritative backend | CPython 3.12.3, explicit opt-in, local closed-input profiles. Linux exact strace 6.8, macOS privileged `fs_usage`, and Windows inbox ETW plus native companions are implemented and native-host validated for the authoritative contract and cross-contract reuse. Every profile fingerprints its backend, interpreter, companion, compiler, and consumed runtime/stdlib/package files. | No backend, denied privilege, unknown/lost events, unbound runtime inputs, unobserved clocks/random/IPC/network/concurrency, or incomplete process trees force real validation. A profile is not recorded as native-host validated until that OS evidence exists. |

The authoritative profile must model observable external-runtime identities, not
exclude inputs merely for residing in a system directory. Native/vDSO time or
random behavior is not proven absent by a file-only trace. If the selected
backend cannot close a required input channel, reject authoritative completeness
for that workload; do not claim that a static candidate or successful trial
closed it. Phase 3 requires an actual supported positive path in addition to
these negative cases.

Initial resource limits: 50,000 project files, 64 MiB total snapshot input,
10,000 collected cases, 1 MiB structured collector result, 256 KiB combined
collector stdout/stderr, 30 seconds per collection, 128 modules per analysis,
64 shards, 64 argv items per generated command. Bounds fail with named reasons;
they never create passing evidence or silently truncate a valid inventory.
Interpreter startup is included in the timeout. All retained child process
groups are cleaned up on timeout/interruption. Output content is discarded.

## State transitions and safe fallback

| State | Entry condition | Exit condition | Fallback |
| --- | --- | --- | --- |
| unconfigured | No committed Click configuration | User authorizes project analysis | Existing authorized full command |
| analysis-required | Metadata identifies explicit supported command; no current analysis | Approved collection yields a stable valid inventory | Do not import during metadata inspection |
| proposal-ready | Actual parent and child inventories agree; deterministic candidate proposals exist | User reviews exact proposal/diff | Proposal is inactive; full validation remains available |
| approval-required | Bootstrap or application or successor authority is missing | Actual later-turn approval of the shown contract/configuration | Save non-authoritative checkpoint and stop dependent work |
| commit-required | Approved proposal was applied, but matching policy is not committed | User commits exact policy; verify HEAD and working copy | Do not touch index; no new cross-revision reuse |
| baseline-required | Matching committed policy, no valid active baseline | Approved parent/full and child baseline run successfully with equivalent inventory/results | Run real tests; setup cost is not savings |
| sharding-ready | Committed decomposition and successful validation; authority for reuse unavailable | A new admitted baseline supplies complete authoritative observation | Run stale children; show reuse-unavailable |
| reuse-ready | Current policy, baseline and complete observations meet current check/environment/input bindings | Each subsequent request requalifies; changed bindings return to baseline/analysis/full execution as appropriate | No implied future skip |
| unsupported | Command/runtime/discovery or split semantics outside tested profile | User chooses supported explicit command/environment and repeats analysis | Original full command only if independently authorized |
| blocked | Error, mutation, observation incompleteness, permission or failed required verification | Observable cause fixed and appropriate work rerun under valid authority | Preserve errors and user files; never auto-revert |

Auxiliary statuses: `selection-required` for an ambiguous command,
`review-required` for split assumptions requiring human judgment,
`reuse-unavailable` for sharding without reuse authority. They do not replace
the ten states above or grant execution permission.

## Data flow and stable candidate format

1. Read Git root, command tokens, target cwd, interpreter identity and project
   metadata without importing or executing package configuration.
2. Execute the bounded collector through `click-gate mutate`. Use a private
   temporary result channel outside the target repository, suppress expected
   bytecode writes and give the child the same import search semantics as the
   parent. The collector emits data, not test success.
3. Snapshot project files before and after collection, including ignored
   ordinary files within the bounded profile. Git metadata is excluded from the
   file walk, but index/HEAD state is separately protected. Do not follow
   external symlinks. A mutation invalidates the analysis without reverting it.
4. Repeat collection in a fresh process and compare the complete ordered
   inventory/metadata. Preserve unstable IDs or discovery results as errors.
5. Build a bounded static dependency graph from the actual collected modules;
   never use Click's own test suite as the target inventory.
6. Phase 2 groups whole modules, then collects generated child commands under
   the same protection. Compare `Counter(child IDs) == Counter(parent IDs)`.
   Check the target snapshot again before publishing proposal artifacts.
7. Store proposals in a new private managed directory outside the target root.
   Do not place generated policy directly in `.click/`, overwrite an existing
   artifact or reinterpret a stale analysis file as authority.

Analysis schema v1:
- `version`, `kind: unittest-analysis`, `adapter: cpython-unittest-v1`,
  `status`, sorted `reasons`, `candidate_only: true`,
  `authority: false`, `reuse_ready: false`.
- `project_identity`: digest of canonical Git root/cwd; `workspace_digest`;
  `command`: explicit parent argv and normalized discovery options;
  `runtime`: implementation/version/executable digest (no raw environment).
- `inventory`: entries `{id, module, class, method, file}`, loader conditions,
  canonical inventory digest, detected collection concerns/errors.
- `dependencies`: per-module candidate paths, import edges, common/global
  candidates and sorted unknown `{file, line, reason}` records.
- Structured error codes rather than traceback/file contents or raw stdout.
  Project-relative paths only inside exported analysis metadata.

Proposals use the existing exact v1 policy schemas. Metadata stays in a separate
proposal record with adapter version, source project identity, workspace/inventory
digests, generation reasons, review state, parent/child equivalence and
`authority: false`. No new metadata fields are smuggled into the policy schema.

## Collection semantics

Use actual standard TestLoader results; recursively flatten only supported
TestSuite/TestCase shapes. IDs must agree with standard TestCase identity and
be unique, bounded and stable. Store module source paths within the target root.
Detect `load_tests`, replaced/custom loader behavior, custom suite/id methods,
import errors and cases not corresponding to static test methods. Class/module
fixtures stay intact. Presence of hooks or unexplained dynamic behavior cannot
be converted to collection success or independent execution merely because two
collections happened to agree.

Static reads and imports are separate operations. Static analysis does not
execute setup.py/pyproject hooks. An interpreter is resolved outside the target
root using the existing executable trust principles; never execute a
repository-shadowed python program. Stdlib is allowed by identity for collection,
but that does not pre-authorize ignoring it in future runtime observations.

## Deterministic sharding and dependency proposals

- A whole module is indivisible. Initially group modules by an exact discoverable
  file selector under the parent's original start/top conditions. Modules with
  the same basename stay together so a filename selector cannot duplicate a
  descendant unexpectedly.
- Generate child unittest discover argv with the same cwd, top-level, filters
  and supported flags and an exact module-file pattern. Validate each emitted
  command with the normal verification policy. Verify actual ID equality; the
  selector design is never sufficient evidence by itself.
- Shared fixtures or possible process-global interactions are conservative
  merge/review/unsupported reasons. Dependency similarity and common helpers
  explain grouping; common config creates multi-shard invalidation candidates
  without merging every test into one shard.
- Stable IDs derive from sorted module membership/relative filenames, not source
  bytes or measured duration. A one-line code change must not reshuffle layout.
  With no timing samples, report timing unknown. Do not invent cost thresholds
  that pretend a short suite was measured.
- At least two useful groups are necessary for a split. If the bound or global
  state prevents a meaningful partition, return unsupported/review-required.
- Child dependency candidates include module tests, transitive internal imports,
  common configuration/helpers/data and existing applicable dependency scope.
  Preserve or widen existing scope, never silently narrow it. Existing
  committed or dirty user configuration is reported for review and not replaced.
- Added/deleted/renamed tests and changed command/discovery require refresh and
  a new equivalence check. Ordinary source content changes do not automatically
  authorize a new policy or shard layout.

## Implementation ownership and entry points

Phase 1 adds small stdlib modules under hooks:
`click_test_inventory.py` for parsing, snapshots and supervised collection,
`click_unittest_collector.py` for the isolated collector,
`click_dependency_candidates.py` for AST candidate analysis, and
`click_auto_sharding.py` as a bootstrap CLI.
Use `python3 -m hooks.click_auto_sharding analyze --project PATH -- PARENT_ARGV...`
from source, or the shipped absolute `hooks/click_auto_sharding.py` path from a
distribution, inside the existing `click-gate mutate` capability.

Phase 2 adds `click_shard_proposal.py` and the `propose` bootstrap operation.
It rechecks current analysis/child collection rather than accepting arbitrary
caller JSON as validated input. It writes separate analysis/proposal files to
managed storage and reports the artifact directory. Public init/status/refresh,
policy application, baseline and authoritative-observer activation are Phase 4,
not quietly introduced here.

Tests live in focused auto-sharding unittest modules and use at least two
independent projects with unrelated layouts/names/counts. Add shipped modules to
the distribution list and keep source/distribution parity. Avoid editing the
existing timing feature just to make new tests easier.

## Completion and verification gates

| Phase | Required deliverable and evidence |
| --- | --- |
| 0 | This concrete supported-profile/state/data/authority/failure/ownership/first-use/metric specification; audit against the entire phase-0.md, preserve original user-file hashes, synchronized checkpoint. |
| 1 | Real stable TestLoader inventory and dependency candidate schema, preserved unknowns and project boundary, independent fixtures and all phase-1 failure/option cases; no reuse-ready. |
| 2 | Two independent no-JSON proposals, exact parent/child multiset, deterministic IDs/argv, preservation and unsupported-split tests; complete current E_TESTS/E_SPEC evidence before the next contract. |
| 3 | Separate approved contract, actual Linux backend positive path with separately approved successor and real code change, exact current bindings, negative observation/forgery/Shadow tests; no simulated-only success. |
| 4 | Product init/status/refresh, approved application, user commit confirmation, parent/child baseline, honest sharding/reuse readiness and dashboard timing semantics; interruption/concurrency recovery. |
| 5 | Two setting-free projects through the real shipped product, separately approved A/B with real change and partial execution/reuse, full final-code audit, safety cases and comparable full/partial measurements; matching dashboard/host/HTML/JSON results. |

Phase 0 uses E_SPEC manual document/diff audit, explicitly an agent attestation.
Phase 1/2 use the approved E_TESTS argv source with a stable adjacent group:
`python3 -m unittest discover -s tests -q` and `git diff --check`.
It includes meaningful new collector/proposal integration tests and existing
Core/packaging regressions. The suite may rerun after a relevant mutation; do
not resubmit an unchanged passing source without a reason. Build/distribution
generation and compileall are mutation operations, not disguised verification.
Run distribution validation through its existing unittest coverage. Supply the
actual absolute repository as the verification request's top-level workdir.
A stale committed Click shard map must fall back to the full suite as new test
files appear; never auto-commit or edit the policy to obtain reuse.

Stop on failed phase gates. Record exact failures, changed files, evidence and
the minimal required action. No failed test is relabeled passed to advance.

## First-install user flow and later timing audit

Installation itself executes no user project code. Metadata offers an explicit
analysis command; authorized collection yields a candidate proposal and readable
scope/diff. Approved application writes configuration; user commits it.
Verify the actual commit and run the real parent/children baseline. A valid
shard plan without complete runtime authority is sharding-ready/reuse-unavailable.
A separately approved later contract changes real code, requests the original
parent, executes related children and reuses only requalified children.

The headline is **estimated omitted test execution time** from actually reused
shards' compatible historical successful samples. Preserve the existing
actual-batch savings calculator as the single source for dashboard, host, JSON
and standalone HTML. Show executed and reused counts, baseline coverage,
estimate/measured/partial/unmeasured labels and source provenance consistently.
Do not add parent wall time and child times or count setup as savings.

Initial setup cost, observation cost, processing intervals and request wall time
belong in detail. Net request savings/loss requires paired full/partial runs on
the same final code, verification scope, cache and concurrency conditions;
`avoided test estimate` minus a partial internal interval is not total wait saved.
Retain negative comparison results. Do not manufacture positive performance with
sleep/inflated work. Final audit covers every item in the goal objective and
phase-5.md, including shipped artifacts and fail-safe reruns; Phase 2 completion
alone is not completion of this goal.
