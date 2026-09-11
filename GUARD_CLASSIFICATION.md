# Click Guard Classification

Status: **Living inventory; v0.30 policy migrations complete; dependency-aware receipt reuse completed for v0.31.0; host coverage identity completed for v0.32.0; runtime domain boundaries completed for v0.33.0; completion receipts completed for v0.34.0; Evidence/Guarded dual authority completed for v0.35.0; authority-preserving migration and Hook-rendered approval projection completed for v0.36.0; non-authoritative Shadow Intelligence Phase 2, plain-language-first Guarded presentation, and Evidence Shards staged for v0.52**

Baseline: **v0.24.6 plus the canonical product-boundary change at `73072a9`**

This document applies the [Click Product Constitution](PRODUCT_CONSTITUTION.md)
to the guards shipped in the baseline above and tracks migrations made after
that point. It records current ownership and remaining migration intent; it does
not claim that every hard gate has already been moved to its target layer.

## CORE

Guarded successor candidate capture and current-contract requalification are
Core evidence boundaries (`click_lifecycle`, `click_evidence`,
`click_verification`, receipt v5). They never convey approval or runner claims.
Display summaries, observed denial/advisory counts, group rates and partial
timing are non-authoritative telemetry, not semantic scope enforcement.

| Guard or invariant | Current owner | Why it remains Core |
| --- | --- | --- |
| Authority-mode identity, Evidence intent/follow-up lineage, and Guarded contract validation, digest, ID, later-turn approval, session and working-directory identity | `hooks/click_contract.py`; typed scalar projection in `hooks/click_runtime_state.py`; mode and authority lifecycle in `hooks/click_lifecycle.py` | Prevents receipts from claiming approval in Evidence and prevents mutation under a substituted or unapproved Guarded intent |
| Mode-aware mutation and external side-effect handling | `hooks/click_mutation.py`; managed-service prepare/claim/record paths in `hooks/click_service.py` | Records host-authorized effects in Evidence and controls them under contract authority in Guarded without turning model strategy into permission |
| Exact one-use runner token, action, request, root and state binding | mutation claims in `hooks/click_mutation.py`; managed-service claims in `hooks/click_service.py`; observation claims in `hooks/click_observation.py`; verification claims in `hooks/click_verification.py` | Prevents replay, request substitution and cross-state execution |
| Canonical state paths, locks, atomic writes and authorized state-root recovery | `hooks/click_state.py` | Preserves approval state across failure without weakening identity checks |
| Cancellation, expiry, consumed-runner and tampering revocation | `hooks/click_state.py`; lifecycle transitions in `hooks/click_lifecycle.py`; host routing in `hooks/click_gate.py` | Prevents stale authority from becoming executable again |
| Evidence registry identity, current-revision state and receipt matching | `hooks/click_evidence.py`; verification claim/result paths in `hooks/click_verification.py` | Prevents evidence from a different request or revision from completing work |
| Browser source admission, serial-call interlock, stable host-call/result mapping, current revision and finalization replay | `hooks/click_browser.py`; host routing in `hooks/click_gate.py` | Prevents an unassigned, parallel, mismatched, stale or replayed Browser result from completing evidence without judging the model's interaction strategy |
| Protected Git-tree, environment and executable fingerprints for argv verification; verification-time mutation detection | receipt helpers and runners in `hooks/click_verification.py` | Preserves the identity of the code and environment actually checked by Click's argv runner |
| Opt-in dependency provider, relevant-entry and resolved-path receipts, plus approved mutation pre/post binding | `hooks/click_dependency_cache.py`; mutation-boundary and receipt transitions in `hooks/click_mutation.py` | Allows cross-revision reuse without treating a model's post-approval assertion, unrelated manifest content, or later workspace drift as proof |
| Committed broad-suite decomposition, exact inventory partition, child identity, pre-execution revalidation, and complete shard aggregation | `hooks/click_evidence_shards.py`; registry transitions in `hooks/click_evidence.py`; verification integration in `hooks/click_verification.py`; receipt v3 in `hooks/click_receipt.py` | Retains honest partial successes without letting an edited map, missing child, command substitution, or shard declaration manufacture completion or cross-revision reuse |
| Canonical incremental-verification plan assembled from existing exact, dependency-observation, and committed safe-change authority | `hooks/click_incremental.py`; authoritative decision wiring in `hooks/click_verification.py` | The plan contains only stable reason codes and digests, and the real runner batch is derived from it; the plan cannot independently authorize reuse |
| Direct-argv capability safety, executable/path/environment checks, process-control rejection and read-only side-effect defenses | shared protocol validation in `hooks/click_capability.py`; inspection policy and hardened execution in `hooks/click_inspection.py`; mutation admission in `hooks/click_mutation.py` | Prevents an admitted inspect, verify or service request from gaining unapproved effects |
| Mutation, observation, verification and managed-service interlocks around active runner claims | mutation reservation and claim state in `hooks/click_mutation.py`; observation reservations and claims in `hooks/click_observation.py`; verification state in `hooks/click_verification.py`; managed-service state in `hooks/click_service.py` | Prevents an authorized side effect or receipt from racing into a different state without treating distinct observation concurrency as authority |
| Host coverage identity, event normalization and state/receipt-preserving result mapping | `hooks/click_host_coverage.py`, `hooks/click_hook.py`, `hooks/claude_hook.py`, `hooks/antigravity_gate.py`, `hooks/platform_protocol.py` | Keeps the known pre/post surface symmetric and prevents verification receipts from crossing a host or coverage-registry revision while making the limited assurance explicit |
| Mode-aware capability ledger and canonical completion receipt export/offline integrity verification | `hooks/click_claims.py`, pure schema in `hooks/click_receipt.py`, assembly in `hooks/click_receipt_runtime.py`, and routing in `hooks/click_gate.py` | Binds honest authority metadata, intent or contract identity, one-use and host-tool-use claims, final workspace revision, and evidence lineage without exposing tokens or overstating unsigned authenticity |

## USER_POLICY

| Policy | Current owner | Constitutional boundary |
| --- | --- | --- |
| Evidence, Guarded and Off defaults; review, bypass and cancel availability | mode state and `skills/click/references/modes.md` | Choosing authority mode is policy; legacy `on` maps to Guarded and `manual` maps to Off so upgrades preserve that choice; exact receipt and one-use enforcement after selection is Core |
| An explicit numeric verification budget | Not currently implemented | A numeric ceiling becomes policy only when the user deliberately opts into that exact restriction; Click's built-in profile suggestions are not a substitute for user choice |
| Acceptance of hosted, manual or existing attestations | evidence-completion handling in `hooks/click_lifecycle.py` | Acceptance must be explicit and must not be described as independent proof |
| Which environment variables bind a verification receipt | `ENVIRONMENT_FINGERPRINT_KEYS`/`_PREFIXES` in `hooks/click_verification_bindings.py` plus the owner's working-tree `.click/environment.json` | The fingerprint covers interpreter, toolchain, locale, time zone, path and proxy variables; a project variable outside that set is a documented runtime assumption until the owner declares it, and a declaration can only add reruns, never reuse |

## HEURISTIC

| Current strategic guard | Current owner | Target behavior |
| --- | --- | --- |
| Advising on a repeated successful read or search and on repeated incomplete/failing reads | observation preparation in `hooks/click_observation.py` | Advisory only — fresh separately authorized repeats use a new one-use runner; an active same-digest reservation remains a Core interlock |
| Advising after broad repository inventory | classifiers in `hooks/click_inspection.py`, preparation in `hooks/click_observation.py`, and host adapters | Advisory only — cross-digest running/success hard denial removed in v0.30.0; active same-digest reservations and execution interlocks remain separate |
| Advising on `update_plan` or equivalent host plan tools | plan-tool branch in `hooks/click_gate.py`; host matchers and adapters | Advisory only — hard denial removed in v0.30.0 |
| Choosing Guarded evidence sources and qualitative verification profile before approval, Evidence ids and concrete argv during execution, and the cheapest proof strategy | Skill, eval and documentation policy | Model strategy or an explicit user constraint; Click records the choice but does not interpret it as runtime authority |
| Legacy verification class-unit values | `hooks/click_verification_policy.py` and `hooks/click_verification_meter.py` | Compatibility data only — they do not produce runtime advice, prove cost or sufficiency, or participate in receipt authority |
| Requiring a mutation after a fixed number of otherwise legitimate failed retries | argv handling in `hooks/click_verification.py` and Browser retry handling | Advisory only in v0.30.0; fresh authorization remains distinct from replay of an active or consumed runner |
| Browser repeat/retry guidance, preferred timing thresholds and interaction-history depth | `hooks/click_browser_advisory.py`; bounded history compaction in Browser preparation | Advisory only — normalized repeats and long timed interactions remain receipt-bound and allowed; old per-input guidance is compacted instead of blocking a new call |
| Cross-platform Shadow Observer selection, native collection and status | schema in `hooks/click_dependency_cache.py`; compatibility facade in `hooks/click_dependency_trace.py`; capability selection in `hooks/click_observer_backend.py`; common result and lifecycle helpers in `hooks/click_observer_common.py`; Linux collection in `hooks/click_observer_linux.py`; privileged native macOS collection in `hooks/click_observer_macos.py`; native Windows ETW collection in `hooks/click_observer_windows.py`; and isolated storage in `hooks/click_verification.py` | Telemetry only — every record is non-authoritative, never permits reuse, never changes check execution or result, and is excluded from completion receipts; permission or collector absence falls back honestly rather than simulating observation |
| Shadow input fingerprints, pre-run predictions and post-run evaluations | `hooks/click_shadow_intelligence.py`; preparation and result wiring in `hooks/click_verification.py` | Telemetry only — every real check still runs exactly once; prediction agreement is measured after the run and never becomes evidence, reuse, approval, completion, or receipt authority |
| Local Evidence Map and Shadow ROI dashboard | sanitized projection in `hooks/click_shadow_intelligence.py`, read-only loopback server in `hooks/click_shadow_dashboard.py`, and lifecycle routing in `hooks/click_lifecycle.py` | Explanatory view only — it exposes no raw state or state-changing endpoint, and start/stop cannot change revision, evidence, or completion |
| Localized `click-gate status` summary | `hooks/click_status_summary.py`; status routing in `hooks/click_gate.py` | Explanatory view only — it repeats ledger counts, the report's own avoided-time estimate and next action in the dashboard language; the full report stays behind `status --json`, and neither form executes a check, changes evidence, or grants reuse |
| Observed-input identity and conditional native receipts | `_fingerprint` in `hooks/click_observation_inputs.py`, `fingerprint` in `hooks/click_conditional_observer.py`, status selection in `hooks/click_authoritative_observer.py` | Identity is content, type/permission bits, directory membership, size and link text; inode numbers and timestamps are runtime assumptions. A followed child process, concurrent threads or dynamic introspection yield a `conditional` receipt whose reuse is disclosed as completeness-unproven; time, random, network and capture loss stay non-reusable |
| Evidence default of actionable reporting for supported Python runners; avoided-output estimate | `reporting_was_omitted`/`supports_actionable` in `hooks/click_diagnostics.py`, `avoided_output` in `hooks/click_incremental.py` | Presentation choices — they change what the host reads, never what runs, passes, or is reused; the avoided-output figure is a disclosed estimate of reading not repeated |
| Observed-input identity and conditional native receipts | `_fingerprint` and `identity_pass` in `hooks/click_observation_inputs.py`, `fingerprint` in `hooks/click_conditional_observer.py`, status selection in `hooks/click_authoritative_observer.py` | Identity is content, type/permission bits, directory membership, size and link text; inode numbers and timestamps are runtime assumptions. A followed child process, concurrent threads or dynamic introspection yield a `conditional` receipt whose reuse is disclosed as completeness-unproven; time, random, network and capture loss stay non-reusable |
| Retained rerun-reason distribution | `retained_reuse_reasons` in `hooks/click_incremental.py`; dashboard rendering | Explanatory view only — it counts recorded plan decisions and reason codes and relaxes no binding |
| Plain-language-first Guarded contract presentation and optional original disclosure | `hooks/click_contract.py`, Skills, documentation, and semantic grader | User-facing explanation policy — the easy body must preserve the canonical meaning, while digest, ID, later-turn approval, and mutation authority remain Core |
| Contract compactness, planning prose and workflow-shape preferences beyond parser or transport safety | `hooks/click_contract.py` schema policy | Keep only identity-bearing protocol fields in Core |
| Main Skill authoring compactness | reference split, plugin validation and review | Heuristic documentation quality — no word-count permission gate; required links and protocol structure remain testable without optimizing prose to an arbitrary number |

## Mixed guards that must be split carefully

- Plan-tool guidance is `HEURISTIC`; preventing replacement or widening of an
  approved contract without new approval is `CORE`.
- Broad-inventory count and scope are `HEURISTIC`; exact request identity,
  one-use runner claims, mutation and verification interlocks are `CORE`.
- Selecting verification breadth and interpreting its cost is `HEURISTIC`
  unless the user explicitly constrains it; exact argv, evidence ID, revision,
  check-group reservation and receipt binding are `CORE`. Legacy class
  normalization is compatibility behavior, not a receipt fact, and does not
  deny or advise on an otherwise authorized check.
- Read path, environment, pager, text-conversion and executable-shadow defenses
  are `CORE`; the supported command surface is an implementation compatibility
  choice, and deciding that a read is too broad is `HEURISTIC`.
- Reusing the same one-use runner token is a `CORE` replay violation; limiting
  fresh, separately authorized retries is `HEURISTIC`.
- A verification command that observably changes protected repository content
  violates the read-only evidence boundary and remains `CORE`; limiting fresh
  retries of an ordinary failing argv check is `HEURISTIC`.
- Browser source and current-revision accounting in `hooks/click_browser.py` are
  `CORE` when the host event is observable; duplicate-input and call-count
  tuning in `hooks/click_browser_advisory.py` are `HEURISTIC`.
- The contract digest and approval identity are `CORE`; required planning prose
  and judgments about whether a contract is concise are not.

## Known assurance gaps

- The Hook proves a later user turn and the exact staged ID, while the Skill and
  model still interpret whether the user's words semantically grant approval.
- A follow-up digest proves that the request was recorded under the active
  lineage; the runtime does not prove that its meaning was inside the prior
  scope or merely narrowed it.
- Natural-language `boundary.in_scope` is digest-bound but is not semantically
  compared with every resulting diff by the runtime.
- Direct `apply_patch`, Edit and Write surfaces receive approval gating,
  revision tracking, and observable pre/post Git snapshots, but they do not use
  the structured runner's one-use request digest. Changes racing inside one
  approved host-tool interval cannot be attributed independently without an
  operating-system monitor.
- Hosted, manual and existing evidence completion is an agent attestation, not
  an independently observed external fact.
- Browser success proves an observed tool result and source binding, not the
  truth of an arbitrary natural-language completion condition.
- Host coverage receipts assert `known-surfaces-only`: they fingerprint the
  configured events Click knows how to consume, but cannot prove that a host
  emitted an event for every capability it may expose.
- Current state and receipts are inspectable, but Click does not keep an
  append-only durable history of every authorization and evidence transition.
- The Linux Shadow Observer is syscall-derived best-effort telemetry. Partial,
  unknown, truncated, or externally resolved inputs are counted rather than
  guessed, and the data is not proof that every semantic dependency was seen.
- A Shadow candidate agreeing with one real rerun is not proof that future reuse
  is safe. Outside-repository inputs remain unmodeled, tracing slowdown is not
  measured, and Shadow mode's actual saved time is always zero.
- A repository owner asserts that shard argv groups together are semantically
  equivalent to their broad parent. Click proves exact mapping and complete
  file partitioning, but does not parse framework output or prove that a child
  command actually executes every file named by its `covers` patterns.
- Loopback binding, a random bearer token, strict Host checks, no CORS, and a
  restrictive CSP reduce accidental dashboard exposure. They are not a sandbox
  against another process or compromised browser running as the same user.

## Operational limits requiring disposition

Hardcoded request, command and retained-state size thresholds are operational
constraints, not automatically `USER_POLICY`. Each limit must either document
the observable availability or integrity invariant that qualifies it as a Core
implementation safeguard, become an explicitly configured user policy, or stop
hard-blocking. They must not be promoted as product identity merely because the
current implementation enforces them.

For v0.30.0, contract prose length, verification check count, and the former
6,000-character raw capability threshold stop hard-blocking. Inspection retains
an eight-command cap because one request is one atomic read-runner claim with
bounded output exposure. Decoded transport, actual Windows command-line, argv,
output and retained-state bounds remain implementation safeguards to review
against the same rule rather than workflow-quality judgments.

Shadow Observer's 4 MiB transient trace cap, 4,096-input record cap, and active-
lifecycle retention bound protect availability and privacy. Reaching a bound
only downgrades or drops telemetry; it does not block or fail verification.

Shadow Intelligence additionally retains at most 64 sources, fingerprints at
most 1,024 inputs per baseline, projects at most 512 input nodes, limits its
state to 2 MiB and each dashboard projection to 512 KiB, and stops a dashboard
after two hours. Reaching a telemetry bound cannot block or fail verification.

These gaps must remain explicit. Marketing and documentation must not claim a
stronger guarantee than the runtime can observe.

## Migration order

1. **Complete:** Treat the v0.24.6 state-root recovery, distribution, tests and release as complete.
2. **Complete:** Establish this classification without changing behavior.
3. **Complete in v0.30.0:** Convert plan-tool hard denial to advisory output.
4. **Complete in v0.30.0:** Convert broad-inventory counting to advisory output while retaining active exact-digest reservations and Core execution interlocks.
5. **Complete in v0.30.0:** Convert fresh duplicate-read and fixed legitimate argv-retry denials to advisory output while retaining active-runner and verification-time mutation denials.
6. **Complete in v0.30.0:** Separate model-selected verification strategy, qualitative profile metadata, legacy unit compatibility, and exact receipt binding; remove plugin-authored cumulative ceilings and numeric overage advice from runtime authority.
7. **Complete in v0.30.0:** Separate Browser source, serial-call,
   result, revision and replay integrity from non-blocking repeat, retry and
   timing guidance.
8. **Complete in v0.30.0:** Update Skill, evals, manifest, README and
   host distributions together with the behavior they describe.
9. **Complete in v0.31.0:** Add approval-bound, dependency-aware cross-revision
   argv receipt reuse with exact check, environment, executable, relevant
   manifest entry, resolved-path and mutation-boundary invalidation.
10. **Complete in v0.32.0:** Centralize the known Codex and Antigravity Hook
    surface, test pre/post symmetry, and bind that limited coverage identity to
    argv verification runners and receipts.
11. **Complete in v0.33.0:** Extract service, Browser admission, mutation,
    capability, inspection, observation, verification and approval lifecycle
    domains while retaining `click_gate.py` as host routing and compatibility
    facade with unchanged Core behavior.
12. **Complete in v0.34.0:** Export and offline-verify deterministic completion
    receipts with capability-claim, workspace, environment, executable,
    host-coverage and evidence lineage.
13. **Complete in v0.35.0:** Make Evidence the new-install default, represent
    host authority without manufactured approval, and retain Guarded as the
    approval-bound authority.
14. **Complete in v0.36.0:** Preserve legacy stored authority choices by
    translating `on` to Guarded and `manual` to Off; deliver the exact human
    approval projection through the stage Hook response; and cover same-ID
   follow-up mutation through stale-evidence transition.
15. **Staged for v0.52:** Collect bounded Linux `strace` aggregates beside
    compatible argv checks while keeping all Shadow Observer data outside
    evidence, reuse, approval, completion, and receipt authority.
16. **Staged for v0.52:** Freeze non-authoritative predictions before real
    checks, evaluate them only after those checks, and expose the bounded
    current-lifecycle Evidence Map and honest Shadow ROI through an explicit
    read-only loopback dashboard.
17. **Staged for v0.52:** Render the exact digest-bound easy contract once by
    default, disclose the canonical original only on request under the same id,
    and keep disclosure distinct from approval or restaging.
18. **Staged for v0.52:** Decompose an exact repository-declared broad suite
    into independently retained evidence shards, retain existing per-child
    reuse authority, fall back to the original parent on ambiguity, and export
    complete shard provenance without adding Phase 3B UI or cache authority.
19. **Phase 3B.1 candidate:** Attach the native macOS `fs_usage` collector only
    when the current process is already privileged, synchronize PID-scoped
    collection before target execution, retain bounded content-free Shadow
    aggregates, and never elevate privilege or change evidence authority.

Each migration is a separate behavior-preserving-for-Core change. It must retain
approval, side-effect authority, one-use runner, revision, receipt, cancellation,
replay, tampering, recovery, current-state and receipt tests.
