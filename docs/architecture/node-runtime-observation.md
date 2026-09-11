# JavaScript input values, native state and conditional reuse

Default Evidence verification runs the input collector for supported
Node/Vitest/Jest checks. Automatic reuse supports **explicitly conditional JS receipts** in addition to
complete native receipts and repository input policies. A raw Inspector or
Shadow record is still diagnostic; only the actual runner can attest a
conditional snapshot. Input completeness is never claimed. The diagnostic profile is Linux Node **22.23.2**.
Other runtimes keep normal execution. No npm dependency, custom Node build,
compiler or persistent service is installed. If an existing C++ compiler and
matching headers are available, an exact-binary native state reader is built in
a private cache outside the project. Unknown binaries or a failed smoke check
leave native state unavailable; the requested check still runs.

## Use

In an active Evidence task or approved Guarded task:

```text
click-gate observer auto
click-gate observer status
```

Evidence selects `auto` by default; no control command is required. The next
actual supported framework execution collects runtime diagnostics, including
projects with an existing `evidence-reuse.json` policy.
An existing reusable result is not forcibly re-executed to collect telemetry.
Unsupported profiles retain a bounded diagnostic attempt. An eligible capture
is a learning seed; the next requested real execution compares the same input
set before and after running and can attest a conditional receipt. No extra
learning execution is launched.
Selecting `observer runtime` again clears its candidate history for an explicit
retry on a later actual execution. Return to ordinary automatic observation with
`click-gate observer auto`, or disable observation with `click-gate observer off`.

The default `auto` path combines OS candidates and V8 diagnostics. Collection
adds debugger overhead on the first actual execution of a check; it does not
cause already reusable checks to run. Timing baselines bind the observer mode.
`runtime` explicitly retries collection; it uses the same receipt checks.

For cross-revision JavaScript reuse, an owner can commit a version 2
`.click/evidence-reuse.json` policy with each child's complete `inputs` and
`reuse_if_only_changed` scope. The real runner binds the declared inputs before
and after the successful check. On reuse it rechecks the current inputs,
policy, command, environment, executable and host coverage. Changed inputs
execute their child; an unchanged sibling can reuse its result. Missing inputs
appearing later and unreported input edits also invalidate the receipt. Click
does not generate owner declarations from incomplete observations.

## Conditional JS reuse

The default `auto` mode accepts `conditional-js-observation-v1` receipts with
`confidence: conditional` and `runtime_inputs_complete: false`. The command,
evidence key, environment, executable, working directory, host scope, policy,
partition and runner execution are bound. Admission rechecks content and
content, type and membership of observed files, missing paths and directories, including
ignored files and ordinary external files. A sibling modifying these inputs
also invalidates reuse at completion. A learning seed or a lost receipt cannot
downgrade to Git-only reuse.

The dashboard, shared reports and host summary label this **conditional reuse:
observed inputs only; completeness unproven**. This permits reuse without owner
JSON, under these explicit assumptions:

- The fixed Node bootstrap's proc/cgroup startup probes and path-traversal
  metadata of workspace ancestors are runtime assumptions. Their changing
  timestamps, available memory and process layout are not proven equivalent.
- The runner's stdout/stderr transport and the normal `/dev/null` device are
  runtime facilities. Stdin, arbitrary descriptors and application proc/device
  reads are not granted the same exception.
- Unobserved native, asynchronous, scheduling or environment-introspection inputs
  may still affect results. Conditional confidence accepts this residual risk;
  it is not a proof of JavaScript semantics or of every possible input.

Known clock/random/shared-memory calls, network/native escape markers, missing
Inspector completion, unknown file events, changing inputs and unsupported
process coverage leave the child executing normally. Framework-internal calls
also count; the current collector does not guess that a framework call is
irrelevant. Consequently some real Vitest/Jest invocations remain ineligible.
The conditional OS projection currently requires a single Node process. Every
worker context must finish its collection; fork diagnostics do not establish
conditional coverage. Scheduling and unobserved shared state remain limitations.
`node --test` keeps its original scheduler and is outside the Inspector profile.
An existing repository input policy remains an independent reuse path.

Local private receipts retain input paths and digests (including external paths),
not file contents. Public dashboard exports retain the confidence/limitation
explanation and omit private input paths. The initial conditional baseline needs
two normally requested executions; the first discovers the input set and the
second checks it before and after running.

## Collection

An external Node controller connects through loopback V8 Inspector endpoints.
A bounded ESM preload publishes each Node process's endpoint through a private
FIFO before the entry point runs. The controller acknowledges completed setup;
without acknowledgement the preload stops waiting after five seconds and lets
the original entry point continue. Existing `NODE_OPTIONS`, preloads and
debuggers are preserved by declining preparation. Node can print its own
temporary debugger endpoint and attachment messages on stderr; those are
ordinary captured process output, not persisted runtime-record fields.
Command argv, framework pool
options and test functions are not rewritten.

The controller binds breakpoints to the original V8 functions. Saving a reference
and replacing `Date.now` does not conceal a call to the saved original. Known
VM-creation APIs enable a temporary before-script breakpoint for fresh realms;
workers are attached before startup through the parent inspector. Forked Node
processes use the private endpoint channel even when their stdout/stderr are
not forwarded. A context arriving outside the tracked setup path is incomplete.

The Linux framework file collector reassembles interleaved syscall lines before
parsing inputs. Descriptor-based `statx`/`newfstatat` calls with `AT_EMPTY_PATH`
bind the descriptor's annotated target; an unresolved descriptor stays incomplete.
An observed `exit_group` closes only its thread group, including idle threads
without separate terminal lines. A forked process needs its own terminal event.
A captured signal termination is lifecycle coverage, not a passing test result
or a substitute for the Inspector's final value flush.

Read-then-write and `O_RDWR` inputs remain dependencies. Output writes never
erase earlier reads, and a changing file kind remains an unresolved input.
Two identical observed input lists cannot prove completeness: external inputs,
async dependency calls and plain runtime data may still affect a result.

Recorded categories are:

- `clock`: Date and selected performance/process clock APIs.
- `random`: Math.random and selected crypto/WebCrypto random/key APIs.
- `shared-memory`: SharedArrayBuffer and Atomics APIs.
- `native-escape`: native binding/addon and selected Wasm/V8/GC entry points.
- `inspector-access`: debugger manipulation APIs.

The `counts` values are **sticky presence indicators, capped at 1**, not call
counts. Once a category is detected its breakpoints are disarmed to avoid paying
for repeated stops that add no reuse information. Framework/bootstrap calls can
set an indicator too; it is not an attribution of nondeterminism to a test body.
The version 2 record also retains per-source value counts and hash chains,
last consumed-value digests, state-pair counts and last state-pair digests. Raw
values, random state, shared bytes, source text, process ids and inspector URLs
are not stored in verification state. Exception outcomes are recorded without
reading user-controlled error getters or replacing the thrown object.

`node --test`, including test children reached through a package script, stays
outside this inspector profile because activating the debugger can serialize
its scheduler. It keeps ordinary execution and existing OS observation.

## Input completeness and reuse are separate decisions

A complete deterministic input model could permit clock, random or shared-memory
reuse in a future profile. The present conditional path does not have such a
model and executes checks with those detected calls. The required decisions are, in order:

1. **Complete observation:** account for the values actually consumed, their
   source/state, mutations and ordering, across every participating context.
2. **Current inputs:** verify that those result-relevant inputs are unchanged,
   or governed by an equivalent, verified deterministic input model.
3. **Authority:** bind the successful execution and complete current inputs to
   the real one-use runner, command, revision, environment and child identity.

| Input | Evidence required beyond a call marker | Reuse condition |
| --- | --- | --- |
| Clock | Consumed values, clock source and any virtual-clock state/advances | A verified virtual clock can be stable; an earlier real timestamp cannot stand in for the next timestamp. |
| Randomness | Actual values or a verified generator algorithm, seed, complete state and consumption order | A fixed seed alone is insufficient when state or ordering differs. Fresh entropy is a changed input even if completely recorded. |
| Shared memory | Initial contents, aliasing, all reads/writes and relevant synchronization across workers | The observed input state and result-relevant ordering must remain equivalent. A final buffer hash alone misses intermediate writes and races. |

For example, changing a random seed requires execution because the input
changed, not because randomness is universally forbidden. An unobserved typed
array write requires execution because coverage is incomplete. These are
different failure reasons. No assumption that an unused or unchanged-looking
input is irrelevant can substitute for a verified input dependency model.

## Current completeness and remaining work

`capture_complete` means that this collector's admitted sessions/contexts and OS
process identities reconcile. Missing completion, loss, unsupported primitives,
source/runtime drift and unknown processes make it false. It does **not** mean
all JavaScript inputs were captured.

The diagnostic record's `inputs_complete`, `runtime_inputs_complete` and
`reuse_authorized` remain false. Conditional receipts are a separate attested
confidence level and never change these diagnostic fields. These fields describe the observation's
authority, not whether the independently verified repository policy permits
automatic reuse.
Every runtime result includes `engine-input-coverage-incomplete`. In particular,
Inspector function breakpoints do not prove consumption of plain runtime data
properties such as `process.pid`, native-addon memory, all shared-memory access
paths, or scheduler-dependent inputs. An absence of detected calls is not proof
of determinism. No broad framework-internal clock exemption is introduced.

## Value and state collection implemented

The Inspector native-call API does not expose the value returned to the caller.
A private realm probe therefore wraps `Date.now`, `Math.random`, and synchronous
Atomics methods to record the value from the **one original invocation**. It
preserves native exceptions, argument coercion count, method name/length,
property flags and non-constructibility. Function identity and reflective
`Function.prototype.toString()` output differ; this is an instrumentation
compatibility limit, not a claim of transparent JavaScript semantics. Runtime
records remain non-authoritative.

The native reader adds:

- V8's actual per-realm PRNG index, both generator state words and the 64-value
  cache digest immediately before/after a random call. The controller checks
  adjacent state continuity and cache-index transitions, including refill.
- Native shared backing-store identities shared across workers, byte length and
  bounded byte samples before/after Atomics operations. Operands are recorded
  when already primitive; user `valueOf`/`toPrimitive` is never invoked twice.
- Same-realm native callbacks for fresh VMs through a V8 extension, without a
  VM escape or giving the VM Node's `process`. The setup binding is consumed and
  removed before the first project script; contexts with incomplete setup or
  shutdown remain partial.

The reader admits only the tested Linux x64 Node 22.23.2 binary SHA-256, without
pointer compression. Runtime, native source, headers, compiler and build flags
bind the cached artifact. A disposable seeded runtime validates the V8 layout
before it can be loaded by a check. Execution also binds the artifact digest;
this cache metadata is not an authoritative dependency receipt.

Limits are explicit: 100,000 value events per realm, 200,000 controller events,
256 values per batch, 64 KiB per shared byte sample and 4,096 backing identities.
Limits, missing state, unsupported coercions, opaque exceptions, replaced input
sources, discontinuous random state or missing completion cannot assert input
completeness. The controller and temporary endpoint directory are closed at the
end of the requested execution.

These samples do **not** cover all intermediate typed-array/DataView writes or
establish an atomic shared-buffer snapshot and full worker synchronization
history. Date construction, other clock APIs, cryptographic randomness and
ordinary runtime properties still have call-only or incomplete coverage. A
fixed seed with state samples is not by itself a whole-program completeness
proof. Those gaps remain visible and do not acquire authority by setting a
boolean flag.

Removing the repository-policy requirement for cross-revision JavaScript
reuse still requires an engine/input integration
that covers those surfaces, input snapshots requalified against current state,
complete process/worker binding, and runner-signed receipt integration. A
project-written hook, candidate JSON, or a debugger session alone cannot replace
that authority boundary. The value/state extension narrows concrete collection gaps; it does not claim
that the remaining whole-program authority work is finished.

The implementation follows the versioned
[Node worker/inspector protocol](https://github.com/nodejs/node/blob/v22.23.2/src/inspector/node_protocol.pdl)
and the experimental
[V8 function-call breakpoint API](https://chromedevtools.github.io/devtools-protocol/v8/Debugger/#method-setBreakpointOnFunctionCall).

## Conditional-path validation scope

The deterministic Node regression executes each requested command once, issues a
runner-signed conditional receipt only after learning, rejects a wrong token or
binding, and invalidates ignored/missing/external inputs. The real Hook test uses
a small deterministic runner fixture at the existing Jest command boundary;
this tests routing, signatures, same-tree reuse and cross-revision child reuse,
not eligibility of every production Jest suite. Real Jest 30 and Vitest 5 tests
separately cover existing execution, diagnostics and repository-policy reuse.
No production time-savings estimate is inferred from these fixtures.

## Input changes and recovery

Configuration files, ignored files and modules reached through CommonJS `require`
or dynamic ESM `import` are candidates from the real file trace. The collector
binds their actual consumed paths; unrelated existing file contents are not
added just because they belong to the repository. Regression fixtures mutate
configuration, a dynamically imported module and an ignored input separately.

A child with an established conditional observation keeps requiring that evidence
if a later edit introduces unsupported worker inputs or capture becomes incomplete.
It cannot fall back to same-tree exact reuse while an ignored worker input changes.
Explicit owner policy and the existing exact-reuse path outside conditional
observation retain their established rules; neither claims complete JS coverage.
Input uncertainty in the conditional path executes that child and does not
invalidate an otherwise complete shard partition.

Automatic mode can retry a previously started Inspector capture on the next
requested execution after a code change, even when it produced no input projection.
Removing an unsupported worker can therefore recover into conditional learning
without resetting Click state. Unchanged unsupported executions, unsupported
launchers such as `node --test`, and diagnostic-only runtime mode keep their
existing repeated-collection limits. No extra test is launched to learn.

Framework record version 4 stores the already-computed workspace content digest
for this retry decision. Evidence task revisions restart at zero, so a revision
counter alone cannot distinguish a new task from a code change. This digest only
schedules collection and grants no reuse authority. Versions 1–3 remain readable;
a version 3 capture with a started Inspector can be refreshed once in auto mode.

Environment binding remains conservative: the canonical execution environment
is bound per command, so a changed inherited variable can rerun multiple children.
This does not claim complete per-variable environment-reader attribution. Worker
file candidates are diagnostic when runtime state or lifecycle coverage is
insufficient; collecting a path alone does not prove worker input completeness.
