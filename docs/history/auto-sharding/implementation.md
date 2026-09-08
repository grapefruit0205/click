# Automatic sharding setup implementation

Phase 4 adds `click-gate sharding init|status|refresh` as the public setup
surface. The command parser preserves the selected unittest argv after `--`;
users do not construct capability or policy JSON.

The implementation keeps two authority domains separate:

- `hooks/click_sharding_setup.py` owns resumable proposal and bootstrap state in
  the plugin data directory. It uses one atomic state file, content-addressed
  proposal artifacts, and a per-project native file lock.
- the existing mutation and verification runners remain the only execution
  paths. Setup reports and artifacts do not create evidence or reuse authority.

An approved analysis contract can create a proposal but cannot apply it. A
different approved Guarded contract must contain the proposal digest in its
human-readable projection and declare the argv evidence id
`E_AUTO_SHARDING_BASELINE`. Application creates only absent shard and
dependency policy files with no-replace semantics. It compares the Git index
before and after, never invokes a Git write command, and reports the exact files
that the user must commit.

`status` recognizes the policy only after its worktree, index, and `HEAD`
contents all equal the reviewed artifact. `refresh` then recollects the parent
and every child, confirms the same inventories and runtime, and runs both
shapes without changing the project. A second refresh submits the parent id to
ordinary protocol-v2 verification. The committed shard map performs the
expansion and retains the established parent fallback behavior.

The final state is derived from actual child sources in the active contract:

- all current passes with incomplete or absent observations:
  `sharding-ready`, `reuse-unavailable`;
- all current passes with complete Authoritative Observer v2 receipts:
  `reuse-ready`, `authoritative-v2`;
- any failure, drift, unsupported profile, missing approval, uncommitted policy,
  or invalid observation remains explicitly non-ready.

The dashboard projection is version 6 and keeps v4/v5 read compatibility. The
result-first summary order is unchanged. Its expanded measurement detail shows
initial setup, measurable observation cost, Click processing, request wall
time, parent and sequential-child bootstrap time, and the signed comparison.
The first bootstrap is labeled as setup rather than savings, and a negative
parent-minus-child result remains visible.

The Phase 5 end-to-end tests start with two independent unconfigured Git
fixtures. After proposal review and application, contract A records the real
authoritative child baseline. A separately approved contract B changes one
library module, submits the original full parent request, executes one related
child, and reuses one of two or two of three unaffected children with complete
origin and timing provenance. A same-final-code parent audit and the shipped
controller path also pass. The short fixtures retain negative whole-request
comparisons rather than claiming a speedup. See
[`e2e.md`](e2e.md) for measurements, scopes, support
limits, and the safety regression map.
