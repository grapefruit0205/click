# Phase 1 report — verification adapter boundary

Status: **complete**

## Purpose and result

Phase 1 separated static command recognition and adapter capabilities from the
Python collectors without changing Click's verification authority. The common
contract is documented in `../ADAPTER_CONTRACT.md`. Only unittest and pytest
advertise inventory and split implementations; the other recognized tools
remain execute-only.

## Changed call paths

- `click_verification_plan.py` delegates minimum-class routing to the static
  adapter registry while preserving its public compatibility aliases.
- `click_test_inventory.py` remains the Python facade, gates collection and AST
  dependency candidates on adapter capabilities, loads AST analysis lazily,
  and emits the non-authoritative common execution model.
- `click_shard_proposal.py` delegates child selectors, discovery, inventory
  ownership, and dependency paths to the selected adapter.
- `click_evidence_shards.py` uses the same Python discovery parser for automatic
  completeness checks while continuing to accept arbitrary owner-committed
  shard v1 definitions.
- `click_sharding_setup.py` reports adapter identity and independent capability
  states. These fields do not set `sharding_ready` or `reuse_ready`.
- `click_capability.py` now applies its existing Win32 executable normalizer in
  `command_parts()`, so Windows absolute launcher paths route consistently.
- The Antigravity hook manifest and generated distribution include the adapter
  module. The repository shard and safe-change manifests include its six tests.

## Verification

The focused adapter, Python facade, parent fallback, capability, verification,
and distribution checks passed. The full automatic-sharding group passed 62
tests with two environment-dependent skips, covering proposal, init/status/
refresh, fallback, bootstrap, and repository shard completeness. The official
inventory now contains 960 unique tests. See
`../logs/phase-1-verification.md` for the commands and results.

## Remaining limits

- Vitest, Jest, Node, package scripts, Go, Cargo, JVM, .NET, and native-build
  adapters have no collection or automatic split implementation in this phase.
- Runtime observation is still the existing Python profile-limited path.
- Environment test values remain `not-recorded` capability metadata; a stored
  capability flag never authorizes execution or reuse.
- Windows path routing has parser coverage, while native Windows execution
  remains assigned to CI.

## Next condition

Phase 2 may start because the adapter contract is static and non-authoritative,
Python behavior and owner shard compatibility are preserved, generated output
is current, and the full automatic-sharding regression passes.
