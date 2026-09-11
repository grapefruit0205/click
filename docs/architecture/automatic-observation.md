# Automatic observation for Evidence users

Implementation sequence:

1. Separate observation integrity from Guarded approval; prepare supported
   observation automatically for host-authorized Evidence verification.
2. Derive observation policy from a versioned built-in provider when the owner
   has supplied no dependency policy. Never manufacture owner declarations.
3. Extend process coverage only where complete input capture can be established;
   incomplete workers, external state and capture loss remain ineligible.
4. Extend validated runtime/framework profiles using the existing collectors,
   preserving the original command and one execution per requested check.
5. Requalify each child independently and exercise clean external-project
   mutation scenarios against a full parent audit.

Required acceptance: existing automatic sharding init/status/refresh, complete
inventory checks, authorized shard reuse and conservative parent fallback do
not regress. Explicit observer off and Shadow remain distinct from automatic
authoritative capture. No installation, privilege escalation, policy commit or
extra test run occurs merely to obtain observation data. Public reporting must
distinguish available, partial, unsupported and measured savings.

The initial rollout connects the existing validated profiles rather than
claiming additional framework coverage. Evidence now selects `auto`, prepares
once with installed prerequisites, and retains explicit controls and capability
candidates across completed turns. The runner, signature, input snapshot and
current binding checks remain the authority boundary. Without a dependency
manifest or declaration, `runtime-observed-inputs-v1` binds built-in capture
rules without writing project policy or fabricating approval.

User compatibility takes priority over capture coverage:

- Existing owner `evidence-reuse.json` policy keeps its reuse route; auto does
  not implicitly replace a precise declared boundary with coarser observations.
- Actionable diagnostics and bounded failure collection retain output alongside
  native input capture from the same admitted target. Collector output is separate.
- Explicit Python environment values are preserved. If they do not satisfy the
  deterministic native profile, the original check runs without an input receipt.
- Missing prerequisites are recorded once, without installation, elevation or
  repeated compiler attempts. Explicit `observer auto` retries preparation.
- Switching off removes this lifecycle's selection, not a shared artifact that
  another session may be using. Auto adds no persistent observer service.

Exact and successor reuse recheck complete observed inputs at the final common
boundary, including ignored data and missing-path lookups outside Git's tree.
An observed change invalidates only the affected source. Partial shard readiness
is reported separately, while the existing partition completeness checks and
parent fallback remain intact.

After a source establishes automatic observation authority, its input requirement
survives capture loss and successor lifecycles. A successful execution with an
incomplete observation still records the actual pass, but cannot turn into a
Git-only reusable receipt. The affected source executes again until complete
capture recovers or a separately validated explicit owner input policy applies.
Unchanged siblings retain their eligible receipts.

Completion also rechecks the observed inputs of previously reused sources. A
sibling can change ignored data after planning without changing the Git tree.
In that case the affected source returns to `ready`, the overall verification
does not claim a current pass, and the execution ledger excludes the invalidated
reuse from savings. Original target output and exit status are retained, with a
notice explaining that more verification is required. The next request can run
only the affected child; completion never starts an extra execution by itself.

Real Linux fixtures exercise two independent sources with no `.click` policy,
one source edit, ignored file changes, an ignored missing input appearing,
shared configuration changes, a child process that makes one observation
incomplete, explicit environment settings, and a final complete parent run.
Each planned execution emits a sentinel exactly once. Existing generated shard
integration now reaches `reuse-ready` in Evidence without a Guarded contract.

Profile expansion remains gated by real capture completeness. Native profiles
now cover the bounded CPython 3.12.3–3.12.14 family, with unittest and narrowly
modeled pytest 8.4.2/9.1.1 execution. Build identities include the actual Python
binary, version, ABI, headers and observation rules, so another runtime cannot
borrow an artifact or receipt. Nonstandard Python distributions can read extra
process/runtime inputs and remain ineligible even when compilation succeeds.

pytest collection, conftest, fixtures and ancestor configuration lookups are
included. Absent configuration files are explicit inputs. Framework timing and
descriptor operations use bound function-code identities; project clock/random
consumption, foreign reporting hooks and profiler replacement are ineligible.
The validated reusable fixture explicitly requests `-s -p no:cacheprovider`.
The claimed-runner fixture separates independent checks into directories.
pytest may inspect sibling files while collecting a flat directory; a
changed sibling content, permission or directory membership conservatively
invalidates the affected receipt, while timestamps and inodes are not part of
input identity.
Ordinary pytest capture/cache options remain unchanged and can leave its input
observation incomplete; normal execution and failure diagnostics still work.

Node, Vitest and Jest now collect OS file/process candidates, including workers,
without pool changes or a second diagnostic run. Interleaved strace calls are
reassembled by task id; every task requires a bound birth and terminal event.
Missing workers, ambiguous identities and lost resumes cannot claim a complete
lifecycle. Observed signal termination closes OS lifecycle only; it does not
replace the Inspector completion flush. Raw traces remain transient and bounded.

These JavaScript candidates are stored separately in `framework_observations`,
with `runtime_inputs_complete: false` and `reuse_authorized: false`. They never
enter the dependency receipt builder or Shadow prediction authority. A successful
process-tree capture cannot prove V8 time/random, native-addon or shared-memory
input completeness. The default V8 collector records selected consumed values,
per-realm native PRNG state and shared-byte samples, alongside call and
missing-session diagnostics; see [value/state coverage](node-runtime-observation.md).
Complete engine authority remains unavailable. The separate
`conditional-js-observation-v1` receipt permits explicitly conditional reuse
without claiming completeness; it binds the actual runner and snapshots the
observed inputs before/after a requested execution. Known dynamic inputs and
unsupported coverage still execute the child. See the [conditional runtime
assumptions and user-visible limitations](node-runtime-observation.md).
Independent owner policies can also permit automatic reuse. Python
threads and followed subprocesses remain ineligible for *complete* authoritative
reuse; when the process tree and every file input were captured they yield a
`conditional` native receipt with the same disclosed-limitation semantics as
the JS receipt. Time, random, network and capture loss still execute the check.

Unsupported framework candidates have bounded diagnostic attempts. Eligible
seeds continue learning on requested executions so the next stable observed
run can issue a conditional receipt; no extra test execution is launched. Explicit off and owner-selected reuse policies are
preserved. Existing execution, inventory, sharding and valid policy/receipt routes
remain available. No production savings claim follows from regression fixtures.

## Stabilization scope

Freeze additional language and runtime expansion at Python and JavaScript for
this iteration. Existing execution profiles remain available. Two validation
passes have separate purposes:

1. Real Python/unittest/pytest and Node/npm/Vitest/Jest executions preserve the
   command, output, failure and one-execution behavior. Check runtime collection,
   worker/VM boundaries, bounded cleanup and incomplete-state handling.
2. Independent child changes, ignored/missing inputs and shared configuration
   invalidate the appropriate receipts. Recheck one-use claims, automatic
   sharding init/status/refresh, partition completeness, valid child reuse and
   parent fallback, then verify generated distribution parity.

Release scope must distinguish execution support from observation-only reuse.
Complete Python profiles can use runner-signed observations. JavaScript keeps
verified receipts and committed owner input policies. Separately signed
conditional JS receipts can also support reuse without owner JSON, with
unproven completeness disclosed in host output, dashboard and reports. Raw
partial engine observations do not themselves authorize reuse; see
[conditional scope and assumptions](node-runtime-observation.md).
The current value/state collection ends at
selected returned values, per-realm PRNG state and shared-byte samples. Complete
shared-access tracing and the remaining value/state surfaces are follow-up work,
not completed support. Automatic input authority requires a bounded profile
proving completeness and current-input requalification with real positive and
negative cases. Do not expand the
support list or claim time savings from these correctness fixtures.

Current validation has an unresolved intermittent result: an initial run of
`test_no_policy_reuses_unaffected_child_and_tracks_ignored_shared_inputs`
reported the opposite child decisions after changing the ignored `local.cfg`
input. Repeated isolated runs, the complete automatic-observation module and
the final 59-case integration set passed. The test now also checks that each
complete observation contains its own source file and that only the intended
child owns `local.cfg`. These passes do not establish the cause of the initial
mismatch; retain the historical attribution as unconfirmed instead of claiming
that passing retries establish its cause.

The 2026-09-10 follow-up reproduced and corrected two concrete failures:

- Adding clock consumption after a complete automatic observation removed the
  input receipt. A later ignored-file change incorrectly reused that child from
  Git equality alone. The persisted automatic-input requirement prevents this
  downgrade across same-turn and successor reuse, and allows reuse to recover
  when complete capture returns. A fixture-local invalid runtime identity also
  exercises capture unavailability without deleting the shared companion.
- An executed child changed an ignored input of a reused sibling, but completion
  still reported the whole verification as passed. Completion now invalidates
  that sibling, records `observed-input-changed`, and preserves the other result.
  Tests cover changes after planning and during sibling execution, followed by
  recovery through only the affected child.

The native tests also stopped deleting the shared content-addressed companion at
teardown. Other test processes and live sessions may still be using it. The
original two suites overlapped, so cache removal is a possible trigger, but no
per-child observation state survived the first failure to establish that exact
historical sequence. New cases bind each observation to its child ID and source
file and reverse request order to check independent ownership.

Release follow-up also injects beta's dependency receipt into alpha's stored
source while retaining alpha's exact execution bindings. Changing alpha's ignored
input must then execute alpha and preserve beta's valid reuse. Decision assertions
retain child bindings and project-input snapshots in failure output, so a future
mismatch has more evidence than the initial failure. A global
`PYTHONHASHSEED=1` experiment disabled the deterministic observation profile and
failed its baseline expectations; it did not reproduce the historical ownership
mismatch and is not a supported-profile success test.

The historical physical trigger remains unconfirmed. The follow-up below
reproduces the same unsafe decision through companion loss and contrasts it
with the current safeguards. Keep this work as a draft pending review and
native CI results; passing retries alone do not establish historical cause.

### Bounded child-decision investigation (2026-09-10)

The original ignored-input scenario was exercised through the actual Hook and
runner in independent synthetic repositories. Normal, reversed and alternating
request order were compared with fresh driver processes (no forced hash seed).
Before/after Hook and runner states, input content/metadata fingerprints,
stdout/stderr and exit status were retained in local diagnostic artifacts.

| Condition | Result |
| --- | --- |
| Existing native cache, serial, three request orders | 3/3 passed |
| Existing native cache, three concurrent drivers, two rounds | 6/6 passed |
| New private native cache, three concurrent drivers | 1/3 passed; 2 initialization failures |
| Same private cache after its build completed, three concurrent drivers | 3/3 passed |
| Ignored input changed to cause an actual test failure, three baseline request orders | 3/3 passed |

The two cold-cache failures identified a separate preparation race, not
opposite child decisions. `prepare()` creates the shared content-addressed
directory before compiling its artifact and writing `build.json`. Another
process sees the directory, fails to read a completed build record, and reports
`native-build-cache-invalid`. Both children actually ran and passed; the fixture
failed its expectation of a complete observed baseline. Automatic preparation
records the unavailable result instead of retrying within that lifecycle.
The same cache passed concurrent runs after preparation completed. This race
cannot by itself establish the historical sequence, whose first complete
baseline had already passed. No shared cache was deleted or changed to create
this experiment. The subsequent publication fix is described below.

The negative oracle is now a permanent test:
`AutomaticEvidenceTests.test_ignored_input_failure_is_executed_and_reported`.
Emptying ignored `local.cfg` after an observed baseline and one source revision
executes alpha, preserves beta's valid reuse, reports failure, and agrees with
the same-state full parent result. The three test cases passed because they
observed the expected failure, not because the synthetic target succeeded.

In total, 18 scenario invocations yielded 16 passes and the 2 classified
cold-start failures. No opposite-child reuse was reproduced. This narrows the
investigation and preserves evidence for future failures; it does not establish
the original trigger, native Windows/macOS behavior, or production performance.

### Cache publication fix and reproduced unsafe decision chain

Native preparation now builds in a private staging directory and publishes the
complete artifact, bootstrap and `build.json` together by directory rename.
Concurrent winners are validated using the same source, compiler, backend and
artifact digests. A losing or failed builder cleans only its own staging path;
an existing invalid published cache is rejected without rebuilding or deleting
it. First-time contenders may compile separate candidates, but later requests
use the normal validated cache. No polling service, unbounded retry, test rerun
or weaker reuse policy was added. The formerly unused `discard()` helper was
removed so lifecycle code cannot accidentally delete another session's shared
companion through that API. The cold-cache scenario that previously failed
2 of 3 baselines now passed all 3 real Hook/runner scenarios.

The first failure's local execution history was also recovered. It confirms
that another suite containing the then-present
`AuthoritativeObserverRuntimeTests.tearDownClass -> observer_runtime.discard`
overlapped the failing suite. The exact instant of teardown relative to each
child's observation was not recorded. The following controlled experiment
therefore establishes a causal mechanism, not a claim to have recovered every
event of that historical run:

1. Establish complete alpha/beta observations, edit alpha, and plan alpha's
   execution with beta's valid reuse.
2. Remove only this synthetic fixture's private companion before its runner.
   Alpha still passes its actual test, but its new input capture is unavailable.
3. Empty ignored `local.cfg`, which must make alpha fail while Git stays equal.

In an isolated negative-control copy, disabling only the later-added
capture-loss and completion safeguards produced **alpha `reuse-exact`, beta
`run`, exit 0**. This matches the original opposite-child decision and actually
hides alpha's failure. No source-to-child swap was required: alpha had lost its
receipt and incorrectly fell back to Git-only reuse; beta still had observed
inputs whose runtime was no longer valid, so beta ran.

With the current safeguards, the identical scenario plans both children to
run, executes alpha, and reports **exit 1 / failed**. The input requirement
survives capture loss, and completion invalidates reused evidence when its
companion disappears. The permanent regression is
`AutomaticEvidenceTests.test_private_companion_loss_cannot_hide_ignored_input_failure`.
The isolated weakened copy is not part of the plugin or distribution. The
current protection was already implemented during stabilization; this follow-up
establishes the complete reproduced failure chain and guards it against regression.

Final focused validation after the publication change: automatic observation
and cache lifecycle **24/24**, sharding setup/boundaries/child reuse **28/28**,
distribution and repository shard inventory **22/22**. All **74** checks passed;
distribution validation, maintained local documentation links and whitespace
checks also passed. The intentionally weakened negative control failed its
safety assertion as expected and is not included in these passing checks.

Focused Linux validation: **125 tests passed** in 254.191 seconds with CPython
3.12.3 and pytest 9.1.1. This covers automatic observation, evidence state,
incremental outcomes, one-use claims, native authority, explicit owner inputs,
automatic sharding init/status/refresh, independent child reuse, exact parent
fallback, output/cancellation handling, CI inventory and distribution parity.
The full repository suite was not repeated for this stabilization change.
A separate system-CPython pass completed **7 checks** in 116.322 seconds:
Node observation boundaries, actual Node/npm/Vitest/Jest Hook execution and
owner-policy reuse, plus the original ignored-input regression. All passed.
Generated distribution parity, the changed documents' local links and
`git diff --check` also passed. These are correctness checks, not a speed benchmark.

## Follow-up acceptance

- One admitted execution retains bounded stdout/stderr and original exit status,
  including failure, large output, cancellation and collector preparation failure.
- Interpreter/profile drift, changed fixture data, clock consumption, missing
  worker completion and forged candidate completeness cannot authorize reuse.
- Real Node workers, Vitest 5 and Jest 30 preserve their existing runtime/pool,
  setup, transformation and teardown behavior while collecting candidate paths.
- Existing sharding init/status/refresh, partition completeness, child reuse and
  conservative parent fallback remain mandatory regressions.
- Linux CI tests Python 3.12.3/3.12.14 and pytest 8.4.2/9.1.1. Added profiles and
  output plumbing on macOS/Windows still require their native CI results; local
  Linux results are not a new claim of native validation on those systems.

## Framework expansion validation — 2026-09-09

At this framework-expansion checkpoint the inventory contained **1,112 unique tests**. Across the full regression
run and corrective focused rerun, **1,106 passed and 6 were skipped**; no
discovered test id was omitted. The skips are five native macOS/Windows checks
and the missing-pytest scenario, since pytest is installed in this test runtime.

The full four-part run initially covered 1,110 tests and exposed one outdated CI
gate count. The gate and its tests now require all eleven matrix-job results.
After fixing that issue, binding custom-interpreter archive probes, and adding
two cancellation regressions, the final **89-test focused suite passed**. It
covers output/process handling, native observation, actual frameworks, automatic
Evidence reuse, repository inventory and CI gates. This is a full run followed
by targeted corrective verification, not a claim of a second full-suite run.

| Python | pytest | Native framework integration |
| --- | --- | --- |
| 3.12.3 | 8.4.2 | 10 passed |
| 3.12.3 | 9.1.1 | 10 passed within the 89-test focused run |
| 3.12.14 | 8.4.2 | 10 passed |
| 3.12.14 | 9.1.1 | 10 passed |

The 3.12.14 checks use a locally built standard CPython runtime. They do not
establish support for every bundled Python distribution. The actual Node worker,
Vitest 5 and Jest 30 checks pass with their original options and no forced pool.
SIGTERM tests verify retained output, one target admission, target termination,
and no fallback execution when cancellation occurs before target admission.

Distribution parity, maintained Markdown file links and `git diff --check`
also pass. No production speed or savings estimate is derived from these tests.

## Initial rollout validation — 2026-09-09

Final Linux CPython 3.12.3 source regression inventory: **1,094 tests**,
**1,087 passed**, **7 skipped**, no failures or errors. Four disjoint partitions
preserved TestCase fixtures and checked that no test was missing or duplicated:

| Partition | Tests | Skipped | Result |
| --- | ---: | ---: | --- |
| 1 | 304 | 4 | PASS |
| 2 | 237 | 2 | PASS |
| 3 | 352 | 0 | PASS |
| 4 | 201 | 1 | PASS |

The excluded checks were five native macOS/Windows-only cases and two pytest
collection/proposal cases because pytest was unavailable in this interpreter.
They are not counted as native validation for this change. Existing real
unittest, Vitest/Jest inventory/refresh/fallback and authorized child-reuse
regressions ran, along with the new automatic Evidence fixtures.

Reproduce with `python3 scripts/run_ci_tests.py --parts 4 --part N` for N=1..4.
The first full pass exposed diagnostic output capture and baseline mode
compatibility issues; those were fixed and the complete final inventory above
was rerun. Distribution validation, maintained Markdown file-link checks,
dashboard JavaScript syntax and `git diff --check` also passed. Codex source and
the generated Antigravity distribution match. These are regression results,
not a production speed benchmark or new platform-support claim.

## Bounded input-coverage regression cases

Native Python integration fixtures exercise configuration-driven dynamic imports,
ignored file reads and unrelated existing file changes. Each consumed input has
its own before/after fingerprint. Threaded Python checks still report incomplete
runtime coverage; they do not gain authority solely because file candidates were
captured. The framework fixtures also retain the original output and exit status.

JS conditional fixtures cover configuration, dynamic ESM imports, ignored inputs,
environment rebinding and recovery after an unsupported worker is removed. See
[node runtime observation](node-runtime-observation.md#input-changes-and-recovery)
for the distinction between recorded inputs, conditional confidence and complete
runtime evidence. This is a bounded JS/Python support target, not a promise to
infer every external input without project policy.
