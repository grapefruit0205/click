# Phase 3 report — real execution and runtime identity

Status: **complete**

## Purpose and result

Phase 3 added content-bound runtime identities to execute-level adapters and
proved the first Node/npm/Go paths with real Hook-to-runner fixtures. Runtime
identity is checked at each existing authority boundary. A changed identity
invalidates reuse, an incomplete identity forces a non-reusable real run, and
a launcher that may download an unprovisioned runtime or dependency is denied
before a runner is issued.

## Changed call paths

- `click_runtime_identity.py` binds bounded runtime, manifest, lock, installed
  state, project, wrapper, and toolchain configuration without executing or
  importing the project.
- `click_verification_bindings.py` embeds the private runtime identity in the
  executable digest and emits one group status for planning.
- `click_verification.py` blocks unsafe launchers and excludes incomplete
  identities from every exact, dependency, safe-change, and successor reuse
  path.
- `click_incremental.py` recognizes the stable
  `runtime-identity-incomplete` decision reason.
- `test_click_multilang_execution.py` runs real Node, npm, and Go commands
  through Hook, claim, runner, and ledger boundaries.
- CI has a native Linux/macOS/Windows job which hard-requires provisioned
  Node/npm/npx/Go tools. Its results are not claimed until that workflow runs.
- The generated Antigravity distribution and repository shard manifests include
  the runtime collector and all Phase 3 tests.

The full safety and identity rules are documented in
`../RUNTIME_IDENTITY.md`; the honest support matrix is
`../CAPABILITY_PHASE3.json`.

## Verification

The final focused suite passed 18/18, the capability/runtime shard passed
61/61, and distribution/inventory validation passed 10/10. The broader
verification authority suite passed 311/311 before the final guard tightening.
Automatic sharding init/status/refresh, proposal, reuse, and parent fallback
passed 59 tests with two existing environment skips after the final code. The
official inventory contains 988 unique tests. Exact commands and limits are in
`../logs/phase-3-verification.md`.

## Remaining limits

- Only Linux Node `--test`, Node `--check`, npm test, and Go test have local
  real-execution evidence in this phase.
- Cargo/Rust, JVM, .NET, TypeScript, CMake, and CTest are recognized but remain
  locally unverified because their toolchains are absent.
- Gradle/Maven wrappers and project Rust toolchain overrides stay blocked until
  their installed distribution can be proven without download.
- Package lifecycle hooks may execute as part of an explicitly requested npm
  command, but incomplete identity prevents their reuse.
- Automatic discovery and automatic sharding remain Python-only at this phase.

## Next condition

Phase 4 may start because real Node/npm/Go execution and unchanged reuse are
bound to fresh runtime/config identities, unsafe download paths fail before
runner issuance, generated output is current, and the mandatory automatic
sharding regression remains green.
