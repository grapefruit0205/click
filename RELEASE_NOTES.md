# Release notes

## v0.81.1 — 2026-09-06

- Distribution validation now recognizes the exact
  `+codex.<14-digit timestamp>` metadata used by installed Codex cache builds,
  while continuing to validate release notes, README labels, and immutable
  marketplace refs against the underlying stable release.
- Regression coverage reproduces the installed-cache manifest rewrite and keeps
  prerelease, generic build, malformed, and shortened Codex metadata rejected.

## v0.81.0 — 2026-09-05

- Verification runners now persist each verification-group completion and duration
  before starting the next group. Cancellation preserves already-witnessed passes,
  distinguishes an active unconfirmed interruption from later not-run groups, and
  cannot let a late runner restore revoked authority or overwrite another batch.
- Dashboard viewer state is now bound to the host session and workspace in a
  separate sidecar rather than an individual Evidence lifecycle. One explicitly
  opened viewer survives successive Evidence tasks and cancellation as a read-only
  recent-results view; explicit stop, SessionEnd, access-token/origin checks, and
  bounded lifetime remain in force.
- A completed Evidence task can retain real successful source facts as candidates
  for the next Evidence task in the same scope. The new task requalifies check,
  tree, mutation, environment, executable, host coverage, dependency, and committed
  safe-change bindings. History, timing, Shadow data, equal revision numbers, and
  prior approval never grant reuse.
- Successor reuse records its origin batch and Evidence session in dashboard and
  sanitized exports. Completion receipt v4 adds strict `successor-reused` lineage.
- The real benchmark adds a `partial-reuse` Evidence lifecycle scenario in which
  one shard is policy-reused and one shard actually runs; no receipt or decision is
  injected. Lifecycle state-machine tests use an in-process hook harness while
  dedicated transport suites retain executable-boundary coverage.

## v0.80.0 — 2026-09-05

- The verification-efficiency dashboard now projects actual batch results,
  separately from the canonical plan. Started, completed, passed, failed,
  interrupted, not-run, and applied-reuse states cannot be confused with
  planned work. A first-check failure leaves later checks unexecuted.
- Bounded, content-free batch history links plans and outcomes by batch id,
  avoids double counting redelivered events, and preserves honest incomplete
  and legacy-unknown states across lifecycle changes and cancellation.
- Measured processing segments and command-invocation durations are separate
  from estimated avoided execution cost. Estimates carry the original
  successful-run baseline and coverage count. Unobserved full host request
  time remains unknown; Observer costs are not counted twice.
- The read-only dashboard presents verification bundles, real execution and
  reuse, recent batch details, Korean explanations, and a selected-source
  Evidence Map with accurate displayed and omitted input counts. Shadow
  observations stay separate from authoritative results and estimates.
- Local comparison benchmarks now create temporary Git repositories, commit
  policies before baseline runs, and exercise real Hook, reuse-authority,
  runner, and receipt paths in Evidence or Guarded mode. They compare both
  full shard execution and an equivalent original parent command, retain
  negative results, and separate warmups and failed performance samples.
- Imported comparison measurements can be viewed alongside a batch and
  exported as sanitized JSON or standalone HTML without external scripts,
  remote telemetry, raw commands, environment values, tokens, or input paths.
  Viewing and exporting reports never execute a verification command.
- Added regression coverage for actual-versus-planned counts, time and
  baseline provenance, bounded deduplication, cancellation, legacy data,
  comparison calculations, display limits, and safe report exports.
- Existing reuse authority, approval boundaries, one-use runners, workspace
  and environment bindings, and non-authoritative native Shadow collectors
  remain unchanged. Small checks may be slower when management overhead
  outweighs avoided execution; fixture results are not product speed claims.

See [Verification efficiency](VERIFICATION_EFFICIENCY.md) for dashboard,
benchmark, export, measurement-scope, and compatibility details.

## v0.70.0 — 2026-09-05

- Click now presents its primary product as **incremental verification for
  coding agents**: the runtime builds one canonical per-source plan before a
  verification batch and executes only sources that are stale or cannot be
  evaluated safely.
- The plan preserves a strict authority order: current exact receipts first,
  complete runtime dependency observation second, unchanged committed
  safe-change policy third, and a real rerun for every ambiguous case. Stable
  reason codes and receipt lineage explain every run or reuse decision.
- Broad suites with a valid Evidence Shards map are planned per child. Python
  `unittest discover` start directories and default or explicit patterns are
  checked against the complete discoverable inventory; a narrower manifest
  runs the original parent suite instead of omitting a test.
- Incremental planning history is content-free and pruned at the first of
  1,000 events, seven days, or 4 MiB. Actual execution counts and durations are
  stored separately from recent-run-based `estimated_avoided_ms` values.
- Observer control is explicit with `click-gate observer off`, `shadow`, and
  `status`; new lifecycles default to off, and dashboard activation is
  independent. Shadow records remain permanently non-authoritative and cannot
  authorize reuse or enter completion receipts.
- Native Shadow collection now has bounded backends for trusted Linux
  `strace`, already-privileged macOS `fs_usage`, and Windows inbox ETW through
  `logman.exe` plus `tracerpt.exe`. Collector failure after target launch never
  reruns the check, and incomplete trees, unresolved events, and external
  inputs stay visible as fail-closed telemetry.
- The local read-only dashboard opens on the product question: which checks did
  this change actually rerun? It separates authoritative execution and reuse
  from Shadow candidates, reports estimated rather than asserted avoided time,
  and renders an Evidence Map around one selected source with current,
  changed, newly observed, and baseline-only inputs.
- A dependency-free benchmark fixture emits JSON with measured full and
  incremental wall-clock durations, executed and reused source counts,
  observer overhead, Shadow contradictions, and a separately labeled estimate
  based on the most recent successful full run. Its output is not hard-coded as
  a product performance claim.
- README and plugin metadata now state the direct value and the nearby limit:
  Click tracks whether existing verification evidence still applies; it does
  not prove code correctness or test sufficiency. Guarded remains an optional
  approval boundary for higher-risk work rather than the main product value.

## v0.60.0 — 2026-09-04

- Guarded staging now presents the exact digest-bound plain-language contract
  once as the default approval body. The canonical technical contract is shown
  only on request under the same id, and viewing it never approves or restages
  the proposal. Approval, requested changes, cancellation, and original-view
  are explicit responses.
- Added automatic Linux shadow collection around compatible argv verification
  when a trusted system `strace` is already available. Click binds the backend
  version and executable digest but does not install it.
- Shadow records are forced to `authoritative: false` and
  `reuse_authorized: false`; they cannot affect live verification, evidence,
  approval, reuse, blocking, or completion.
- Exact field allowlists, canonical repository-relative paths, aggregate-only
  external and unresolved event counters, strict status invariants, and size
  limits keep malformed or privacy-sensitive records fail-closed.
- Raw trace data crosses a private transient FIFO outside the repository, with
  only a bounded in-memory prefix retained for normalization. The FIFO and raw
  data are always discarded. Only the latest canonical record per evidence
  source is kept in the active lifecycle, outside completion receipts.
- Missing, unsupported, denied, failed, incomplete, or overflowing collection
  never blocks, reruns, or changes a real check. Non-Linux hosts preserve their
  existing verification behavior and may record collector unavailability.
- Phase 2 fingerprints bounded observed repository inputs after a successful
  real check, freezes a non-authoritative prediction before the next run, and
  evaluates that prediction only after the real check finishes. Environment,
  executable, host-coverage, collector, workspace, and input drift remain
  explicit reasons to rerun or mark the comparison not evaluable.
- An explicit local dashboard presents the current lifecycle as an Evidence
  Map with prediction reasons and outcome labels. It is an authenticated,
  read-only IPv4-loopback service with embedded assets, strict Host checks, no
  CORS, restrictive CSP, no-store responses, and automatic session cleanup.
- Shadow ROI reports zero actual time saved, counts gross potential only for a
  candidate confirmed by its real rerun, reports measured observer overhead,
  and labels tracing slowdown as unmeasured. It makes no net-saving claim.
- Fingerprints, predictions, evaluations, map data, and ROI remain bounded,
  lifecycle-only telemetry excluded from evidence and completion receipts.
- A Browser call whose PostToolUse event is lost now closes its exact expired
  capability claim as failed before a receipt-bound retry. A later successful
  Browser check can therefore complete and export without leaving a phantom
  running claim.
- The technical contract and generated Antigravity distribution are kept in
  sync for the v0.60.0 release.
- A committed `.click/evidence-shards.json` can bind one exact broad argv group
  to stable child groups and a complete current test-file inventory. Every
  inventory member must belong to exactly one child; edited, malformed,
  incomplete, unsupported, or racing maps run the original parent suite.
- Shard children use the existing evidence ledger. A passing child survives a
  later sibling failure, failed and unexecuted children never pass, and the
  next parent request runs only unresolved children at the same revision.
- The shard map authorizes decomposition only. Cross-revision reuse still
  requires each child's complete dependency receipt or exact committed
  safe-change entry; the shard policy path cannot declare itself safe.
- The one-use runner revalidates the relevant committed entry, identical
  working copy, inventory, and child bindings before execution. Unsharded
  receipts stay at v2; sharded completion uses strict v3 provenance while the
  offline verifier remains compatible with receipt v1 and v2.
- Click's own broad verification suite now rolls out through six committed
  evidence shards, so passing siblings can remain reusable when another shard
  fails or remains unresolved.
- Codex verification batches can bind an explicit canonical working directory.
  Shard filtering and expansion preserve that binding, and the runner rejects
  any actual working-directory mismatch before executing a check.
- Interrupting verification now terminates the retained process group, records
  a non-passing exit status, and releases the one-use claim instead of leaving
  a permanently running verification lock.

## v0.51.1 — 2026-09-03

- Antigravity now remembers when the staged Guarded projection has already
  been delivered within one execution epoch. Repeated `PreInvocation` events
  no longer ask the model to compile, present, or request approval for the same
  contract again.
- A later explicit approval is routed to the existing `contract_id`, and an
  already approved execution continues without another pass, stage, or approval
  request. The one-user-response approval boundary and fail-closed behavior are
  unchanged.

## v0.51.0 — 2026-09-03

- A committed `.click/evidence-reuse.json` can bind an exact check group to
  repository paths that are explicitly safe to change without rerunning it.
- Successful baselines store a compact effective Git snapshot. Later
  verification computes and reports net changed paths, and reuses only when
  every path is covered by the unchanged committed entry.
- Policy edits, unlisted or unsupported paths, unmerged or ambiguous Git state,
  environment/executable/host-coverage drift, and mutation-boundary drift all
  fail closed to a real check. Click policy files cannot authorize themselves.
- This path requires no runtime observer or extra platform package and uses the
  same implementation on Linux, macOS, and Windows. Existing complete runtime
  observations remain stronger evidence and cannot be overridden by this policy.
- Cross-revision dependency receipts now combine hard concrete dependencies
  with repository inputs observed during the baseline run. A complete
  observation refines expanding committed-manifest patterns to consumed inputs,
  while approval-bound paths remain hard dependencies. Missing or failed
  observation, external input, and incomplete child-process coverage fail
  closed to a real rerun without changing the check result.
- The committed `HEAD` dependency manifest is the policy authority. Malformed,
  deleted, or changed working-tree copies cannot narrow it; a check that reads
  the working copy still records it as an observed input and invalidates on
  content change.
- The obsolete one-off `publish-v0.24.6.yml` workflow has been removed. Release
  metadata, the immutable marketplace ref, the Git tag, and the GitHub Release
  are advanced together after the exact release commit passes CI.

## v0.50.0 — 2026-09-02

- The executable gate facade now retains only the deliberately documented
  private compatibility binding
  `_validate_contract = click_contract.validate_contract`. The reviewed
  baseline falls from 144 private module forwarders to one without changing
  the public `main`, `host_router`, or runner-transport surfaces.
- Domain tests and runtime patch points now target the owning capability,
  inspection, observation, mutation, service, verification, lifecycle, prompt,
  contract-state, and state modules instead of reaching through
  `click_gate.py`.
- The architecture check detects both ordinary assignments and tuple
  destructuring, preventing removed private forwarders from being hidden or
  reintroduced while preserving the two public state-lock timing constants.
- Evidence and Guarded authority, exact errors, one-use claims, replay and
  tamper rejection, and Codex/Antigravity host behavior remain unchanged.

## v0.36.2 — 2026-09-02

- Mode and preference persistence plus read-only inspection policy now live in
  explicit leaf modules, keeping lifecycle orchestration and the gate facade
  focused on coordination without changing public authority behavior.
- Prompt lineage, exact bypass/cancel authorization, follow-up digests, and
  active-turn validation now share one state-only prompt leaf while preserving
  the existing lifecycle and gate compatibility symbols.
- Contract JSON read, atomic save, and clear operations now share one
  `click_contract_state` leaf across lifecycle, Browser, mutation, observation,
  service, verification, and the gate facade. Clear still revokes the runner
  recovery mirror, malformed state keeps the exact fallback shape, and all
  established compatibility identities remain intact.
- Codex and Antigravity distributions remain byte-synchronized, and the full
  468-test regression suite passes with three documented skips.

## v0.36.1 — 2026-09-02

- Receipt export now recovers a nested execution workdir omitted by the host
  only from the single canonical Git root shared by every current argv evidence
  source. Stale, missing, non-canonical, or conflicting bindings are never used
  as an implicit workspace choice, and the final protected tree is still
  recomputed before export.
- Regression coverage reproduces the Code Mode shape where the outer Hook cwd
  is not a Git repository but the verification runner used an explicit nested
  repository workdir. Unit coverage also keeps stale and invalid root bindings
  fail-closed.

## v0.36.0 — 2026-09-01

- Legacy stored authority choices now preserve their meaning during the public
  mode-schema migration: `on` becomes Guarded and `manual` becomes Off. New and
  unset installations continue to default to Evidence, and active Guarded
  contracts remain locked.
- The canonical Product Constitution and guard inventory now describe the
  shared integrity layer plus separate Evidence host authority and Guarded
  contract authority instead of implying that every receipt is approval-bound.
- A successful Guarded stage Hook response now carries the exact runtime-made
  Goal, Changes, Unchanged, and Completion checks projection. It includes build
  approach and verification scale without persisting contract plaintext.
- Integration coverage now follows an approved Guarded contract through a
  digest-bound in-scope follow-up, same-ID resume, actual mutation, revision
  increment, and stale-evidence transition without a second user approval.
- Receipt export now snapshots the working directory actually selected by the
  host tool while retaining the event workspace for contract-state identity,
  so a valid nested-worktree export is not rejected as the wrong final tree.
- A completed Guarded state now rolls into a fresh Evidence session when
  Evidence is the default. Staged and approved-incomplete Guarded states remain
  locked and cannot be silently converted or discarded.
- Codex and Antigravity distributions are rebuilt from the same source, and the
  complete 423-test regression suite passes with three documented skips.

## v0.35.0 — 2026-09-01

- Evidence is now the default mode. It leaves execution authority with the
  host, adds no Click approval prompt, and records intent lineage, mutation
  revisions, exact verification receipts, dependency reuse, and an honest
  approval-free completion receipt. Stored legacy `on` and `manual`
  preferences migrate once to Evidence; an active Guarded contract remains
  locked until completion or explicit cancellation.
- Guarded remains available for higher-risk work and now presents Goal,
  Changes, Unchanged, and Completion checks as its primary approval view.
  Canonical JSON remains digest-bound technical detail rather than the default
  user interface. In-scope and narrowing follow-ups are audit-bound by turn and
  prompt digest without routine reapproval; material outcome, boundary,
  invariant, authority, or verification changes still require a new contract.
- Evidence sessions dynamically bind the exact argv checks actually selected
  by the model. Approval-free receipts set `contract` to `null`,
  `approval_bound` to `false`, and `execution_authority` to `host`; Guarded
  receipts retain contract, approval-turn, replay, tamper, revision,
  environment, executable, host-coverage, and dependency-lineage bindings.
- Receipt export no longer becomes permanently unavailable merely because a
  supported host omitted a mutation's matching `PostToolUse`. A later passing
  one-use verification at the same or a newer revision may settle that admitted
  host claim only as `observed`, never `passed`; an unwitnessed claim or active
  runner still blocks export.
- Read-only PDF admission accepts bounded metadata and stdout-only text
  extraction while continuing to reject output-file writes, shell wrappers,
  and ambiguous arguments. Dependency-aware reuse remains conservative:
  Evidence uses only committed mappings, while Guarded may also use the
  approval-bound declaration.
- Codex and Antigravity ship the same Evidence/Guarded/Off lifecycle, receipt
  semantics, cache rules, documentation, and regression behavior. The
  Antigravity distribution is rebuilt from the canonical runtime and Skills.

## v0.34.0 — 2026-09-01

- A versioned append-only capability ledger now records approval-bound
  one-use runner and host-tool-use claims across mutation, observation,
  verification, Browser, managed-service, and explicit evidence-attestation
  paths. Receipts expose commitments and results without storing raw runner
  tokens, argv, contract prose, or workspace paths.
- `click-gate receipt export` emits one canonical completion envelope after all
  declared evidence is current and managed execution has stopped. It binds the
  contract ID and digest, staging and approval turns, mutation revision, final
  protected Git workspace digest, capability claims, and per-source result,
  environment, executable, host-coverage, and dependency-reuse lineage.
- `click-gate receipt verify <path>` performs bounded strict JSON parsing and
  canonical digest verification without network access or active Click state.
  The envelope is deliberately labelled `unsigned-integrity-only`: it detects
  malformed or mismatched content but does not claim publisher identity or
  resist an attacker who can rewrite both the body and digest.
- Legacy and minimal authorized runner state remains recoverable with revision
  zero compatibility, while consumed-runner replay stays blocked. A backfilled
  ledger is marked incomplete and cannot be exported as a complete receipt,
  preventing missing historical authority from being invented during upgrade.
- Codex and the bundled Antigravity distribution ship the same claim, receipt,
  recovery, routing, documentation, and deterministic regression behavior.

## v0.33.0 — 2026-09-01

- Managed local-service validation, admission, one-use start and supervisor
  claims, stop polling, process ownership, and exact state updates now live in
  the standard-library-only `click_service.py` runtime domain.
- `click_gate.py` retains event routing and the existing compatibility surface,
  while supplying the cross-domain contract-mutation transition required before
  service start. The service domain imports only the lower `click_state.py` and
  `click_process.py` leaves.
- Service request schema, error text, runner argv transport, digest and
  constant-time token bindings, timeout behavior, and public/private compatibility
  symbols remain unchanged. Antigravity bundles the same extracted runtime.
- Browser source admission, serial `tool_use_id` interlocks, bounded attempt
  receipts, PostToolUse result mapping, current-revision observation, and final
  evidence transition now live in `click_browser.py`. The gate retains host
  event routing and injects only cross-domain lifecycle predicates.
- Browser timing and repeat suggestions remain non-authoritative in
  `click_browser_advisory.py`; the extracted receipt runtime cannot emit a host
  allow/deny decision. Existing state fields, exact error text, compatibility
  helpers, retry compaction, and legacy running-entry handling are preserved.
- Approved mutation request validation, revision invalidation, dependency-cache
  lineage boundaries, one-use runner claims, execution receipts, and PostToolUse
  workspace binding now live in `click_mutation.py`.
- The mutation runtime imports only state, evidence, and dependency-cache leaves.
  Host rendering, shared argv execution, workspace snapshots, and observation
  activity predicates are injected by the gate, preserving exact command
  transport, errors, state schema, replay behavior, and compatibility symbols.
- Shared direct-argv protocol decoding, executable normalization, shell-free
  validation, request transport, and stable digests now live in the leaf
  `click_capability.py` module for reuse without an upward gate dependency.
- Read-only command admission, Git and structured SSH policy, trusted executable
  resolution, environment sanitization, remote-URL redaction, and inspection
  execution now live in `click_inspection.py`.
- Observation reservations, duplicate-read advisories, one-use runner claims,
  bounded output receipts, review state, replay rejection, and exact result
  recording now live in `click_observation.py`. Gate wrappers preserve existing
  private symbols and runtime patch points while injecting host command rendering.
- Verification batch validation, check classification compatibility, protected
  Git snapshots, environment and executable fingerprints, host-coverage and
  dependency receipts, preparation, one-use claims, result recording, and the
  shell-free verification runner now live in `click_verification.py`.
- `click_gate.py` retains verification routing and compatibility wrappers. Its
  wrappers inject current snapshot, executable-digest, and argv-execution patch
  points so existing callers and the deterministic suite retain exact behavior.
- Approval state, persistent mode state, prompt-turn authorization, contract
  IDs and completion, stage/pass transitions, prompt context, cleanup, and
  non-argv evidence completion now live in `click_lifecycle.py`.
- `click_gate.py` is the host event router and compatibility facade. Lifecycle
  code coordinates lower runtime domains without importing a host adapter or
  the gate, preserving exact state schemas, error text, and approval behavior.

## v0.32.0 — 2026-09-01

- Codex and Antigravity now derive their known mutation, plan, Browser, and
  exec-alias surfaces from one standard-library-only host coverage registry.
  Deterministic tests require every registered mutation or Browser surface to
  have matching `PreToolUse` and `PostToolUse` configuration; plan tools remain
  intentionally pre-only advisory.
- Verification preparation binds a compact host, registry digest, and explicit
  `known-surfaces-only` assurance to its one-use runner. Successful argv
  evidence records the same coverage identity, and exact or dependency-aware
  reuse requires an exact current match. A host change, registry change, or
  tampered runner binding therefore runs verification again or fails before a
  check executes.
- Legacy evidence state without a coverage receipt remains readable but cannot
  authorize receipt reuse until a new successful verification records one.
  The registry does not claim to observe a capability for which the host emits
  no matching Hook event.

## v0.31.0 — 2026-08-31

Click v0.31.0 adds opt-in dependency-aware evidence reuse across approved
mutation revisions while preserving whole-tree verification as the safe
fallback.

- An `argv` evidence source may opt into cross-revision reuse with deterministic
  repository-relative `dependencies` proposed before staging and bound by the
  approved contract digest. Sources without a precise declaration keep the
  existing rerun behavior.
- A committed `.click/evidence-dependencies.json` may map an exact adjacent argv
  group to repository paths. Contract and repository declarations are unioned;
  only the normalized relevant entry participates in cache identity, so an
  unrelated committed manifest-entry change does not invalidate the source.
- `*`, complete-segment `**`, and trailing-slash directory prefixes have fixed
  semantics. Receipts record the sorted resolved file set and hash file modes,
  contents, safe repository-internal relative symlinks, and their targets.
  Ambiguous patterns, external or broken links, unmatched paths, malformed or
  uncommitted manifests, and special files fall back to real verification.
- A successful cross-revision promotion still requires the same active
  contract, exact check group, Git root, canonical environment, executable
  fingerprint, provider, entry, and dependency digest. The receipt records its
  prior revision, reuse time, count, current full-tree digest, and resolved
  dependency paths.
- Mutation pre/post snapshots are bound to the host `tool_use_id`. Pre-existing
  drift, a missing `PostToolUse` receipt, or workspace drift after the approved
  tool returns disables dependency reuse. Current-revision whole-tree receipt
  matching and verification-time mutation detection remain unchanged.
- The exact release commit is gated by the deterministic suite on Linux, macOS,
  and Windows, plus distribution validation and the Plugin Security Scan.

## v0.30.0 — 2026-08-31

Click now states its stable product boundary directly: bind AI execution to
approved intent and return verifiable evidence. Model workflow strategy is not
runtime authority.

### Plan tools become non-blocking advisory

- `update_plan` and equivalent host plan tools remain available while a Click
  workflow is armed, staged, approved, or in read-only review.
- Plan output cannot stage, approve, replace, or widen an execution contract. It
  does not change the contract digest, mutation authority, revision, or evidence
  state.
- Skill guidance may still recommend implementing directly from the compact
  contract and avoiding unnecessary parallel planning, but that recommendation
  is non-blocking and non-authoritative.

### Broad-inventory counts become non-blocking advisory

- A distinct-digest broad repository inventory may run even while another broad
  inventory is running or after one succeeds; narrowing context is advisory.
- The decision depends on observable argv, request digest, revision, and runtime
  state—not model identity or a model-specific workflow score.
- An active exact-digest observation reservation remains blocked by a separate
  runner-state interlock. A completed exact-digest request is handled by the
  logical-repeat advisory below. Broad advice cannot alter contract, digest,
  mutation, or evidence authority.
- Structured read admission, one-use claims, path and environment safety,
  cancellation, replay and tamper checks, mutation and verification interlocks,
  output caps, and retained-state limits remain unchanged.

### Logical repetition and fixed argv retries become non-blocking advisory

- A fresh request for an identical successful read or search is allowed through
  a newly issued one-use runner and receives reuse or narrowing guidance. This
  is a new authorization, not replay of the consumed runner token.
- An observation that has already failed or produced incomplete output twice,
  and an ordinary argv evidence source that has already failed twice, may be
  retried under fresh authorization with non-blocking repair guidance.
- The decision remains model-neutral and depends only on the request digest,
  revision, result state, and exact runner authorization.
- A same-digest observation already running remains blocked because issuing a
  second reservation would conflict with its active token and result record.
  Verification that changed protected repository content also remains blocked
  until an approved mutation repairs or reconciles the workspace.
- Consumed-token replay, request substitution, active mutation or verification
  races, receipt mismatch, cancellation, tampering, exact check-group binding,
  and verification-time mutation detection remain hard.

### Verification profiles are qualitative and non-authoritative

- Before approval, the Skill or model recommends evidence and a verification
  profile; during execution it chooses concrete argv. Those choices are
  strategy and cannot grant or revoke execution authority.
- The selected profile remains digest-bound as a qualitative statement of
  intended depth. It has no plugin-authored numeric ceiling or overage advice.
- The model chooses concrete argv during execution. Exact check-group digests,
  revision, environment, executable fingerprints, and observed results remain
  receipt facts; legacy class-unit values remain compatibility data only.
- Separate leaf modules now own profile names and legacy unit arithmetic.
  Contract schema, one-use runners, evidence receipts, and completion behavior
  remain unchanged except for the prose-length gate described below.
- A future exact numeric ceiling belongs in an explicit opt-in user-policy
  field; plugin-authored defaults will not silently stand in for that choice.

### Browser receipt integrity is separate from workflow advice

- Assigned-source admission, serial calls, stable `tool_use_id` and matching
  `PostToolUse` results, current mutation revision, explicit finalization, and
  completed-evidence replay remain hard receipt-integrity checks.
- A normalized interaction seen after success or repeated failure is allowed as
  a fresh host call with non-blocking reuse or repair guidance. This does not
  replay an old result or manufacture completion evidence.
- Requests above the previous 30-second timeout and five-second explicit-wait
  recommendations remain allowed with timing guidance. Their active-call expiry
  follows the declared runtime so a legitimate long call is not mistaken for a
  lost result.
- Browser attempt history remains bounded: after 256 normalized inputs, Click
  compacts the oldest per-input guidance record instead of denying the next
  receipt-bound call. Source and revision receipts are preserved.
- These decisions depend on observable host input, result state, source and
  revision—not model identity or a model-specific workflow test.

### Runtime authority remains hard

- Distinct-turn approval and exact contract-digest binding remain required.
- Unauthorized mutations, mid-run contract replacement, runner replay,
  cancellation bypass, state tampering, and evidence-integrity failures remain
  fail-closed.
- One-use runners remain bound to their exact request, and evidence receipts
  remain bound to the active contract, mutation revision, protected workspace,
  execution environment, and executable fingerprint.

### Skill authoring is structural, not word-count authority

- The arbitrary 900-word Click and 350-word Fix CI caps are removed. Required
  reference links, lifecycle markers, manifest shape, Skill validation, and
  distribution consistency remain checked, while prose length and clarity stay
  authoring-review concerns rather than runtime or CI permission rules.
- This item remains in the v0.30.0 notes because the policy removal landed
  after v0.24.6 and has not previously shipped in a stable release.

### Contract and verification size heuristics stop granting authority

- A valid Execution Contract is no longer rejected solely because its encoded
  prose exceeds 4,000 characters. Typed schema validation and contract-digest
  binding remain unchanged.
- A verification request is no longer rejected solely because it contains more
  than eight checks or its JSON exceeds the former arbitrary 6,000-character
  threshold. Argv policy, exact source-group binding, serial execution, and
  mutation detection remain.
- Inspection, mutation, service, and explicit evidence requests likewise no
  longer inherit that raw-JSON character heuristic from the common decoder.
- Inspection retains its eight-command request cap: it bounds one atomic
  read-runner claim and its output exposure rather than judging model strategy.
- Capability transport remains bounded by its decoded payload guard and the
  host-imposed Windows command-line limit; those execution checks replace the
  unrelated raw-JSON character heuristic.
- Unused `_verification_executable_digest` and
  `_is_broad_exploration_command` compatibility-free helpers are removed.

### Evidence reuse boundary

- v0.30.0 continues to reuse a successful exact receipt only when its active
  contract, mutation revision, protected Git tree, normalized check group,
  canonical environment, and executable fingerprint still match.
- A dependency-aware cache across revisions is intentionally not included. It
  requires a separate invalidation design backed by repository-owned dependency
  data, with whole-tree invalidation as the safe fallback; model-declared impact
  alone will not become receipt authority.

## v0.24.6 — 2026-08-31

Click v0.24.6 is a focused Windows/Codex Desktop compatibility and runner-state
recovery patch. Contract shape, approval semantics, evidence protocol,
verification budgets, and the observable workflow policy remain unchanged.

### Recover approved runner state before strict path resolution

- Stateful mutation, verification, and managed-service runners keep the final
  canonical `Path.resolve(strict=True)` admission check, but an already-issued
  approved runner may now restore its exact missing session-contract state from
  a short-lived recovery mirror before that strict check runs.
- Recovery requires the explicit bound state path plus the existing request or
  service binding and one-use runner token to match. Wrong tokens, mismatched
  requests, cancelled contracts, expired reservations, and consumed-runner
  replay remain fail-closed.
- Explicit contract cancellation removes the recovery mirror. Recovery-only
  paths use non-strict canonicalization so macOS aliases such as `/var` and
  `/private/var` resolve to the same snapshot without weakening the final state
  path validation.

### Windows Python launcher fallback

- Windows lifecycle Hooks no longer assume that `py -3` can resolve an
  installed interpreter. The launcher probes Python 3 through `py -3`, then
  `python`, then `python3`, and a broken `py` launcher can fall through to a
  working `python.exe`.
- Once the Hook starts, rewritten Click runners reuse the exact selected
  `sys.executable` instead of returning to `py -3`. Existing encoded transport,
  runner claims, state-root binding, and shell-free capability execution remain
  unchanged.
- The Windows regression suite includes a fake `py` launcher that reports
  `No installed Python found!` while a real `python.exe` remains available.

### Codex Desktop exec routing and Hook launch

- The existing canonical `Bash|apply_patch|...` PreToolUse matcher remains
  unchanged. A separate Desktop execution matcher covers `exec_command`,
  `shell_command`, `unified_exec`, function-qualified forms, and observed Code
  Mode aliases.
- Direct execution aliases are normalized onto Click's canonical Bash policy
  before the core state machine sees the event, so `click-gate` control commands
  and structured reads receive the same rewrite and enforcement when the host
  dispatches the matching Hook event.
- Windows lifecycle Hooks invoke the quoted `.cmd` launcher directly instead of
  wrapping the entrypoint in an embedded PowerShell `-Command` payload.
  UserPromptSubmit, PreToolUse, PostToolUse, and SessionEnd keep their existing
  lifecycle semantics and timeouts.
- This does not claim to solve a host path that never dispatches PreToolUse.
  If Codex Code Mode executes through a surface for which the client emits no
  matching Hook event, Click cannot observe or enforce that execution.

### Regression and release gate

- Focused regressions cover deleted state-root/state-file recovery, wrong-token
  rejection, cancel/replay revocation, Windows encoded-runner recovery, broken
  `py` fallback, direct `cmd.exe` UserPromptSubmit context, Desktop
  `exec_command` rewriting, structured inspection, and rewritten runner
  execution through PowerShell and `cmd.exe`.
- The compatibility patches passed deterministic CI on Ubuntu, macOS, and
  Windows and the Plugin Security Scan before this release metadata was staged.
- The immutable `v0.24.6` tag and GitHub Release must point to the exact merged
  main commit that passes the final release CI and security checks.

## v0.24.5 — 2026-08-31

Click v0.24.5 is a focused Windows internal-runner shell compatibility
release. Contract shape, evidence transport, approval behavior, and
non-Windows runner rendering remain unchanged from v0.24.4.

### Cross-shell Windows runner launch

- Rewritten `inspect`, observation, mutation, service, and verification
  runners now reuse the bare `py -3` launcher already required by the Windows
  lifecycle hooks.
- The generated command no longer places a quoted absolute `sys.executable`
  path in command position, where PowerShell treats it as a string expression
  unless an incompatible shell-specific call operator is added.
- The resolved Click script path remains quoted, while every action, state
  path, token, and request stays inside the bounded encoded-runner transport.

### End-to-end Windows regression

- The Windows integration suite now asks the real PreToolUse hook to rewrite
  structured `click-gate inspect` requests, then executes the returned
  commands through both PowerShell and `cmd.exe`.
- Normal and space-containing plugin roots are covered. Stateless inspection
  and separately authorized stateful review runners must both return the
  inspected file contents successfully from both shells.
- Platform-independent tests pin the bare `py -3` prefix, exclude the
  interpreter's absolute path from the emitted command, and retain expansion-
  token hiding, launcher-path rejection, payload bounds, and decode fidelity.

### Compatibility and release gate

- POSIX rendering still uses `shlex.join`; Windows command-length limits and
  fail-closed launcher-path checks are unchanged.
- Fail-closed state-root validation is also unchanged: this release verifies
  reachable state bindings cross-shell but does not recreate a missing or
  inaccessible approval state.
- The shared source hook and generated Antigravity distribution remain byte-
  equivalent. On Windows, the Antigravity adapter translates the portable
  launcher back to its active interpreter argv before direct shell-free
  execution, avoiding a new launcher dependency or behavior change.
- The exact merged-main commit must pass the deterministic suite on Linux,
  macOS, and Windows plus Plugin Security Scan before the immutable `v0.24.5`
  tag and GitHub Release are published and reinstalled.

## v0.24.4 — 2026-08-31

Click v0.24.4 is a focused contract-boundary extraction and verification
environment recovery release. Existing contract shape, evidence protocol,
mode behavior, and the v0.24.3 runner-claim lifecycle remain unchanged.

### Contract validation leaf extraction

- Contract constants and pure validation now live in `hooks/click_contract.py`,
  which has no upward runtime dependency on `click_gate` or state/process
  modules.
- `click_gate._validate_contract` remains the exact validator function through
  a direct compatibility alias. Validation order, accepted values, returned
  contract objects, and error messages are unchanged.
- Focused unit and architecture-policy tests pin the new leaf boundary and
  compatibility surface.

### Self-healing verification environment admission

- Prepared environment key/value HMAC records now carry an authenticated
  aggregate binding tied to the one-use runner token.
- If a prepared project, user, PATH, or toolchain value changes or disappears
  before runner claim, Click projects current values onto the prepared key
  set, ignores runner-only additions, and rebinds the canonical environment
  digest automatically without another approval.
- Successful evidence receipts store the actual rebound environment digest.
  Exact executable fingerprints remain fixed; changed executables and
  malformed or tampered bindings still fail closed before any check executes.

### Compatibility and release gate

- The source and Antigravity distribution share the extracted validator and
  verification recovery behavior; unrelated adapter behavior is unchanged.
- Focused regressions cover changed and missing environment values,
  runner-only additions, Windows case-insensitive keys, tampered bindings,
  executable changes, receipt identity, and exact contract errors.
- The exact merged-main commit must pass the deterministic suite on Linux,
  macOS, and Windows plus Plugin Security Scan before the immutable `v0.24.4`
  tag and GitHub Release are published and reinstalled.

## v0.24.3 — 2026-08-30

Click v0.24.3 is a focused observation-runner lifecycle hardening patch.
Contract shape, evidence protocol, mode behavior, module boundaries, and the
v0.24.2 Windows launcher repair remain unchanged.

### Claim before read

- A tracked inspection now writes an unclaimed reservation, then atomically
  claims its managed state path, active status, current revision, exact request
  digest, one-use token, replay state, and freshness immediately before any
  read executes.
- An unclaimed startup reservation expires after 30 seconds. Once claimed, a
  synchronous read does not expire merely because time passes; it continues to
  block mutation and final verification until the runner records its result or
  the user explicitly cancels the contract.
- Tampered, unmanaged, stale-revision, expired, cancelled, or replayed runners
  execute no read. Successful, failed, and incomplete results clear the claim;
  a safe no-child startup failure records exit 127 and releases the claim for
  the existing bounded retry path.

### Recoverable verification admission

- Verification environment binding now canonicalizes the Hook-owned
  `PLUGIN_ROOT` value as launcher bookkeeping while retaining project, user,
  PATH, toolchain, executable, tree, and exact-check binding.
- When verification admission fails before any check executes, the runner may
  release only the exact digest/token-matched unclaimed reservation. Its
  sources return to `ready` without fabricated evidence or a consumed
  test-failure retry. Claimed, stale, unavailable, tampered, and replayed state
  remains fail-closed.

### Compatibility and release gate

- Focused regressions cover claim-before-execution, replay rejection,
  unclaimed expiry, claimed-read interlocks, startup-failure cleanup,
  Hook-owned environment normalization, verification admission cleanup,
  tampered/claimed fail-closed behavior, parallel result recording, and
  existing inspection behavior on Linux, macOS, and Windows.
- The Antigravity distribution is regenerated from the same source, while its
  documented host limitations remain unchanged.
- Issue #25 remains open because this patch does not claim to repair a host
  path that does not deliver Click's matching PreToolUse event.
- The exact merged-main commit must pass the deterministic suite on Linux,
  macOS, and Windows plus Plugin Security Scan before the immutable `v0.24.3`
  tag and GitHub Release are published and reinstalled.

## v0.24.2 — 2026-08-30

Click v0.24.2 is a focused Windows Codex hook-launch compatibility patch.
Contract shape, evidence protocol, mode behavior, and runtime authorization
rules remain unchanged from v0.24.1.

### Windows plugin-root template compatibility

- `hooks/hooks.json` now uses Codex's `${PLUGIN_ROOT}` template in every
  `commandWindows` hook command instead of the cmd-style `%PLUGIN_ROOT%` form.
  The path remains quoted and uses Windows separators after host rendering.
- A Windows regression suite pins all four lifecycle commands and, on the
  Windows CI runner, executes them through PowerShell from both an ordinary
  plugin root and a plugin root containing spaces.
- The patch covers UserPromptSubmit, PreToolUse, PostToolUse, and SessionEnd
  launcher rendering without changing their Click modes or authorization
  semantics. SessionEnd remains capped at the host-supported three seconds.

### Scope and release gate

- This patch does not claim to fix host-side hook dispatch paths that do not
  invoke Click's PreToolUse hook. The separately reported Windows Codex Desktop
  unified-exec dispatch issue remains an upstream/host compatibility boundary
  unless the host begins delivering the matching hook event.
- Existing Always ON or Manual preferences and active-state formats require no
  migration. Users refresh the `click` marketplace, reinstall `click@click`,
  restart the desktop app, review the updated Hook, and begin a new task.
- The exact release commit must pass the deterministic suite on Linux, macOS,
  and Windows, repository distribution checks, and Plugin Security Scan before
  the immutable `v0.24.2` tag and GitHub Release are published.

## v0.24.1 — 2026-08-30

Click v0.24.1 is a focused host-compatibility patch for the SessionEnd
lifecycle hook. Contract shape, evidence protocol, mode behavior, and all
runtime authorization rules remain unchanged from v0.24.0.

### SessionEnd timeout compatibility

- `hooks/hooks.json` now declares the SessionEnd command timeout as three
  seconds, matching the host's supported maximum and removing the startup
  clamping warning.
- UserPromptSubmit, PreToolUse, and PostToolUse retain their seven-second
  timeouts, and every hook command and matcher remains unchanged.
- The deterministic hook-configuration regression test now pins all four
  timeout values to prevent this compatibility setting from drifting.

### Compatibility and release gate

- Existing Always ON or Manual preferences and active-state formats require no
  migration. Users refresh the `click` marketplace, reinstall `click@click`,
  restart the desktop app, review the updated Hook, and begin a new task.
- The exact release commit must pass the full deterministic suite on Linux,
  macOS, and Windows, the repository distribution checks, and Plugin Security
  Scan before the immutable `v0.24.1` tag and GitHub Release are published.

## v0.24.0 — 2026-08-30

Click v0.24.0 changes normal anti-loop decisions from raw call counts to
current, revision-bound evidence. Authorization, process claims, concurrent
execution guards, and verification-time mutation detection remain fail-closed.

### Content-free evidence boundary

- `click_evidence.py` now owns deterministic evidence-ID hashing, registry
  digests, initial source and Browser-session state, ledger-shape validation,
  and pure current-revision and kind lookups.
- `click_gate.py` retains contract and protocol validation, transition timing,
  verification budgets and retries, Browser admission, completion policy,
  persistence, and runner orchestration. Dependency direction remains
  `click_gate → click_evidence`.
- Compatibility aliases preserve direct callers and legacy state keeps its
  distinct fail-closed migration path. Codex and Antigravity bundle the same
  standard-library-only evidence module.

### Evidence-driven inspection and verification

- Approved implementation and read-only review may perform the first broad
  repository inventory for the current mutation revision. A concurrent broad
  inventory, or any later broad inventory after success, is blocked even when
  it uses different argv; narrower inspection remains available.
- Verification protocol v2 may submit any nonempty subset of unresolved argv
  sources. The first accepted check group for each source reserves its exact
  normalized digest and Hook-inferred units for the active contract, and the
  cumulative reservations must fit the approved scale. Partial requests cannot
  split around the budget.
- A current successful exact argv check is skipped only when the same active
  contract and mutation revision, check group, protected Git tree digest,
  Hook-prepared execution context, and resolved executable fingerprint still
  match. A new mutation revision never auto-promotes stale evidence; non-Git
  worktrees and missing or mismatched receipts rerun the check.
- Click binds every prepared environment key and value with keyed content-free
  hashes before issuing the rewritten runner. The runner requires every bound
  value to match, excludes launcher-only additions from the child check, then
  fingerprints the resolved target and pins the selected launcher path
  immediately before execution, preserving virtual-environment and shim
  semantics. Hardened structured SSH policy and remote-URL redaction also
  remain active after executable pinning.
  macOS and Windows shell bookkeeping therefore cannot invalidate an unchanged
  receipt, while a changed prepared value or executable fails closed.

### Browser input deduplication

- Assigned Browser work remains serial but no longer uses a normal three-call
  or 90-second session cap. A normalized input that succeeds is blocked on
  repetition for the current revision. A failed input gets one identical retry
  and is then blocked, while a different input remains available.
- The 30-second per-call timeout and five-second explicit-wait maximum remain.
  A 256-unique-input ceiling protects state growth and is not an expected usage
  target. Once a source is observed, a later distinct failure does not demote
  it before finalization.
- These receipts and counters remain workflow guardrails, not a sandbox.
  Protected Git snapshots exclude ignored content and do not prove external
  dependencies, services, or semantic sufficiency.

### Distribution and release gate

- Codex and the generated Antigravity distribution bundle byte-equivalent gate,
  evidence, and policy sources where their host capabilities overlap.
- The exact release candidate is gated by the deterministic suite on Linux,
  macOS, and Windows plus plugin, marketplace, skill, compilation, whitespace,
  distribution-consistency, and Plugin Security Scan checks.

## v0.23.0 — 2026-08-30

Click v0.23.0 extracts the shared shell-free process mechanics into a small,
standard-library-only module. Contract JSON, evidence protocol, modes, approval
behavior, executable trust, and Git/SSH policy remain unchanged.

### Shared process boundary

- `click_process.py` now owns synchronous argv execution, managed-process
  spawning, platform-specific child-process-group isolation and termination,
  and bounded runner-output copying.
- Every shared runner still executes an already-authorized argv with
  `shell=False`; synchronous execution also remains `check=False`. The gate
  continues to resolve trusted executables and construct sanitized environments
  before invoking this process layer.
- `click_gate.py` retains contract and capability policy, state transitions,
  one-use runner claims, Git/SSH restrictions, service and verification
  orchestration, workspace snapshots, budgets, and evidence semantics.
  Compatibility aliases preserve the existing internal test and direct-caller
  surface while modularization proceeds incrementally.
- Evidence-ledger modularization is intentionally not included in this release;
  verification protocol version `2` and its stored completion state are unchanged.

### Distribution and release gate

- Codex and the generated Antigravity distribution bundle the same
  `click_process.py` and `click_gate.py` sources. Antigravity's adapter-specific
  host launcher remains separate because its lifecycle semantics differ.
- Dedicated regressions cover POSIX and Windows isolation, graceful and forced
  termination paths, shell-free run/spawn calls, bounded output, one-way module
  dependencies, and sibling-only distribution startup.
- The exact release candidate is gated by the deterministic suite on Linux,
  macOS, and Windows plus plugin, marketplace, skill, compilation, whitespace,
  distribution-consistency, and Plugin Security Scan checks.

## v0.22.0 — 2026-08-30

Click v0.22.0 hardens the experimental Antigravity adapter and extracts the
shared runtime state-storage boundary into a dedicated module. Contract JSON,
evidence protocol, modes, and the one-approval workflow remain unchanged.

### Antigravity launcher boundary

- The experimental Antigravity adapter accepts only the exact absolute Python
  and adapter paths injected by its current `PreInvocation`; basename lookalikes,
  relative launchers, and substituted interpreters cannot authorize a Click
  control command.
- The accepted launcher is parsed as one expansion-free Bash command before its
  absolute argv prefix is compared. Appended or glued shell operators,
  redirects, substitutions, globs, and multiline suffixes fail closed.
- Because Antigravity cannot rewrite `run_command` argv, direct read-only
  `run_command` calls now fail closed and use `control inspect` instead. Native
  file/search tools and unrelated MCP, Skill, and Plugin tools remain available.
- Antigravity parses Click's encoded Windows runner command with the native
  Windows argv parser and still executes the resulting argv without a shell.

### Shared state-storage boundary

- `click_state.py` now owns configuration and state paths, hashed workspace and
  thread identities, canonical managed-state validation, atomic JSON writes,
  and the cross-process state lock used by both Codex and Antigravity adapters.
- Contract policy, capability classification, process execution, and evidence
  semantics remain in `click_gate.py`; this release intentionally moves only
  storage primitives so the refactor does not change authorization behavior.
- Source and Antigravity distribution tests launch each gate with only its
  sibling runtime modules available, preventing accidental imports from the
  repository source tree. Existing managed state remains compatible.

### Compatibility and release gate

- Direct `click-gate` integrations continue to use verification protocol
  version `2`; no contract or evidence migration is required.
- The exact release candidate is gated by the deterministic suite on Linux,
  macOS, and Windows plus plugin, marketplace, skill, compilation, whitespace,
  distribution-consistency, and Plugin Security Scan checks.

## v0.21.1 — 2026-08-30

Click v0.21.1 is a focused workflow-security maintenance release. It closes executable-resolution and runner-authorization gaps without changing the compact contract schema or one-approval workflow.

### Read-only execution boundary

- Read-only capabilities now accept only bare executable names. Names containing `/` or `\\`, Windows drive-prefixed forms such as `C:cat.exe`, and UNC forms fail closed before execution.
- Direct recognized reads are always rewritten through Click's shell-free inspection runner, including when no contract or review ledger is active. This preserves lightweight reads while preventing the original shell from resolving a workspace-controlled lookalike.
- The runner removes empty, relative, and repository-resolving PATH entries, rejects repository executables and symlinks in either direction, resolves the accepted executable to an absolute real path, and executes that path. The boundary is the nearest containing Git repository, or the current working directory outside Git. The same rule covers local Git and SSH inspection.
- Read and Git children also drop inherited `LD_*`, `DYLD_*`, `GCONV_PATH`, and `LOCPATH`; Git additionally drops inherited `GIT_*` configuration. Internal Git snapshots resolve Git through the same executable boundary.

### Mutation authorization and runner state

- A structured mutation runner now atomically claims its managed state path, approved status, request digest, one-use token, replay state, and expiry before starting the requested process. Invalid, expired, tampered, unmanaged, or replayed runners execute zero mutation commands. An unstarted reservation may expire; after claim, it remains active until result recording or explicit cancellation rather than guessing that the child stopped.
- Managed-service start and supervisor launches now use the same digest-bound, one-use pre-execution claim, preventing replay from spawning another server. A claimed verification batch likewise cannot expire into a parallel retry while its process may still be running.
- Unclaimed verification and managed-service reservations are rechecked for malformed, future, or expired timestamps at the execution claim itself. On Windows, rewritten runner arguments travel in a bounded compressed encoding so legal `%...%` and `!...!` path text cannot be expanded by `cmd.exe`; launcher paths containing cmd.exe or PowerShell expansion characters fail closed.
- Mutation-result recording accepts only a successfully claimed runner. Every stateful rewritten command carries the Hook-selected canonical `gate-state` root and canonical state path, so child runners do not depend on an ambient `PLUGIN_DATA` value or accept a symlinked/mismatched state file.
- In a detected Git worktree, failure to establish the initial protected-content snapshot fails closed before any verification check runs.

### Compatibility and release gate

- Contract JSON, evidence protocol, modes, and user approval behavior are unchanged from v0.21.0. Existing users should refresh the marketplace snapshot, reinstall Click, restart the app, review the updated Hook, and begin a new task.
- The deterministic suite includes path-qualified, PATH-shadow, symlink, absolute-executable, state-tamper, expiry, and replay regressions. The tag is published only after the exact release branch passes the full local gate and Linux, macOS, Windows, and Plugin Security Scan workflows.
- These changes harden Click's observable workflow boundary. Click still does not claim to be an operating-system sandbox or to protect secrets, network access, external paths, concurrent same-user replacement of an executable outside the repository, or arbitrary behavior hidden inside an approved custom program.

## v0.21.0 — 2026-08-29

Click v0.21.0 connects every declared completion source to current-revision Hook state. It removes the ceremonial local verification batch from contracts whose sufficient evidence is Browser, hosted, manual, or existing, while binding every local argv check to the exact approved evidence ID it proves.

### Upgrade required

Existing Click users must refresh the marketplace snapshot and reinstall the plugin:

```bash
codex plugin marketplace upgrade click
codex plugin add click@click
```

Direct `click-gate` callers must send verification protocol version `2` and include an approved argv `evidence_id` on every check. A v0.20 contract that was staged or approved but not completed has no reconstructable per-source ledger; complete it before upgrading, or use the exact `@Click cancel` flow after upgrading and then stage and approve a fresh contract.

### Per-source execution state

- Final argv verification now uses protocol version `2`; every check carries an `evidence_id` that must resolve to a declared source with `kind: "argv"`.
- The Hook stores a content-free, hashed per-id ledger whose source count and typed registry digest detect partial entry loss, and completes a contract only when every declared source passed at the current mutation revision and no managed service remains active. A contract whose evidence is entirely Browser, hosted, manual, or existing no longer needs an unrelated local verification command.
- One final argv batch must cover every unresolved argv source. Staging rejects an argv registry whose minimum one-check-per-source cost or check count cannot fit the selected scale. Several adjacent checks may share one id, and all must pass. If an earlier source succeeds before a later source fails, its result remains current while the unresolved source receives the bounded retry. The rewritten runner is atomically claimed before checks execute, so replay cannot rerun them.
- Successful Browser work is observed first and explicitly finalized with `click-gate evidence`. The same command records hosted, manual, or existing completion as an explicit attestation; it cannot complete argv evidence and does not claim to independently prove unmatched external events.
- A mutation, including protected workspace content created by verification, invalidates every source. Evidence IDs are hashed in persistent state; descriptions, conditions, argv, and output remain absent. The deterministic hashes avoid plaintext storage but do not make predictable IDs confidential.
- Incomplete contracts staged before the evidence ledger cannot be reconstructed from a digest. They fail closed with cancel-and-restage guidance. A legacy contract that had already completed under its prior current-revision rule still permits normal rollover.

### Smaller entrypoint, honest cumulative cost

- `plain_language` remains canonical and digest-bound, but presentation now renders its exact value once instead of duplicating the easy explanation outside and inside the displayed contract.
- Measured from v0.18 to v0.21, the always-loaded Click entry skill shrank from 12,996 to 6,029 bytes (53.6%), while the root plus all six references grew from 38,358 to 44,774 bytes (16.7%). The usual pre-stage bundle (root, modes, directive format, and verification profiles) moved from 25,047 to 24,669 bytes (-1.5%). Relative to v0.20, the cumulative root-plus-reference source grew 10.9% to document the evidence protocol. This is progressive disclosure and a smaller entrypoint, not a claim that every full-workflow prompt became half as large.

## v0.20.0 — 2026-08-29

Click v0.20.0 keeps the one-contract, one-approval workflow while making its purpose clearer: agree once on what will change, what must stay true, and what evidence will count, then keep implementation and necessary verification inside that boundary without observable replanning, repository-wide rescans, or duplicate proof.

### Upgrade required

Existing Click users must refresh the marketplace snapshot and reinstall the plugin to load v0.20.0:

```bash
codex plugin marketplace upgrade click
codex plugin add click@click
```

Restart the ChatGPT desktop app, review and trust the updated Click Hook, and start a new task so the new Skill and Hook definitions are loaded.

If you call `click-gate` directly, update `pass` to send the emitted `contract_id` instead of contract JSON, and migrate inline `done_when` strings to structured condition objects that reference `verification.evidence` ids.

### Contract-id approval and lean skill routing

- `click-gate stage` validates the canonical contract once, stores its digest and derived runtime state, binds them to a fresh opaque 128-bit `contract_id`, and returns that content-free lifecycle handle.
- A later approval or interrupted-run resume passes only the emitted id. Contract JSON is never reconstructed in the approval turn; same-turn pass, malformed ids, stale ids after a revised stage, and corrupted digests fail closed.
- Pre-id active state receives a deterministic digest-derived compatibility handle so an already staged or incomplete session can finish without exposing contract plaintext or deleting state.
- Click and Fix now route exact schema, verification, anti-loop, capability, and mode rules to their canonical references instead of repeating those details in both entry skills.

### Structured and bounded primary evidence

- Contracts declare each evidence source once with an id, typed `kind`, and description. Every `done_when` condition references exactly one source id, so one source can cover several conditions without duplicating natural-language evidence text.
- Inline `done_when` strings are rejected with a migration message; contracts now use condition objects and `primary_evidence` references, and unused or unresolved evidence ids fail closed.
- Browser MCP work is available during an approved contract only when one referenced evidence source has `kind: "browser"`. Locale-specific marker and substring matching no longer controls Browser authorization; otherwise the Hook rejects it as shadow verification.
- One representative Browser session is capped at three serial tool calls and 90 seconds of measured tool time. Tool timeouts above 30 seconds and obvious waits above five seconds are rejected in favor of deterministic state or one representative interaction.
- Browser evidence is reset by a later mutation, is required before a Browser-assigned contract can complete, and cannot be repeated after current-revision completion.
- CI runs feature-branch commits through `pull_request` only and reserves the `push` trigger for `main`, eliminating the duplicate three-OS matrix that previously ran when a pushed branch also had a PR.

### Managed local execution

- Recognizable development servers use `click-gate service` with `start` and `stop` requests. A Click-owned supervisor retains the exact child handle, isolates its process group, stops it on request or `SessionEnd`, and enforces a two-hour final lifetime ceiling.
- Foreground server forms are rejected by `click-gate mutate`, preventing a long-running child from holding the implementation command open. Direct process-control executables remain blocked.
- Exact `node --check <file>` and `node --test <file>` checks qualify as targeted evidence; project-wide `node --test` remains broad, while Node eval/print forms are not verification capabilities.

### Release gate

- The plugin manifest, repository marketplace, deterministic policy tests, READMEs, and release notes identify v0.20.0.
- The tag and GitHub Release must point to the exact protected-main commit that passes the full local suite and required GitHub Actions checks.

## v0.18.0 — 2026-08-29

Click v0.18.0 turns the hardened post-v0.17 source into one reproducible stable release without expanding the one-contract workflow.

### Enforcement boundary

- Git inspection uses subcommand-specific positive option policies and a dedicated sanitized executor. `git grep` and `git cat-file` remain excluded; pager and caller-supplied config overrides are rejected; inherited `GIT_*` variables and system/global Git config are isolated; supported diff rendering forces `--no-ext-diff` and `--no-textconv`.
- Arbitrary `--format` and `--pretty` output, signature-rendering paths, and `git status -v/-vv` are no longer read capabilities. Ordinary bounded status, diff, log, show, ref, revision, merge-base, and remote-URL reads remain available through their explicit allowlists.
- SSH Git inspection remains Experimental and is limited to the existing bounded `status`, `rev-parse HEAD`, `merge-base`, and `remote get-url` forms. It assumes a POSIX remote shell, rejects caller-supplied SSH options, requires an already-known host key, disables interactive password flows and forwarding, and uses fail-fast connection and keepalive settings.
- `click-gate bypass` requires an exact first-line `@Click bypass` directive, is same-turn and one-use, and does not clear an active contract. `@Click cancel` separately authorizes one same-turn contract cancellation.
- Staged and approved-incomplete contracts no longer expire on the ephemeral seven-day cleanup window. Final verification fails stale for every newly created non-ignored path.

### Reproducible distribution

- The plugin manifest, three READMEs, and release notes identify v0.18.0.
- The repository marketplace pins the immutable `v0.18.0` tag instead of following `main`.
- Required CI keeps the Linux, macOS, and Windows deterministic suite and adds Ubuntu release checks for the repository-owned plugin/marketplace/Click/Fix validator, Python compilation, and `git diff --check`.
- The Hook remains standard-library-only, external Click state remains content-free and outside target repositories, and Git/SSH inspection remains a workflow guardrail rather than a security sandbox.

### Release gate

The tag and GitHub Release must point to the exact protected-main commit that passed the full local suite and every required GitHub Actions check. The installed plugin is compared with that tagged artifact after publication. No unmeasured accuracy, time, token, or overdesign improvement is claimed.
