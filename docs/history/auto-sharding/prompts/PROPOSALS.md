# Unittest proposal bootstrap

The Phase 2 adapter produces reviewable configuration from actual collection.
It does not apply policy, approve a contract, commit files, run test bodies, or
authorize reuse. Installation and module import do not collect project tests.

With an existing authorized Click implementation capability, run the source CLI:

```text
python3 -m hooks.click_auto_sharding propose --project /absolute/project -- python3 -m unittest discover -s tests -q
```

The shipped equivalent is the absolute `hooks/click_auto_sharding.py` script.
Use `--cwd /absolute/project/subdirectory` when the original command runs there.
The bounded unittest and pytest collection adapters accept CPython 3.10 through
3.14 through the common Linux, macOS, and Windows process layer. Actual runtime
and native-platform validation status is tracked separately from implementation
status. Unsupported interpreter, discovery, framework, or verification-policy
forms are reported explicitly.
The parent argv may use `python`, `python3`, a version-qualified `python3.x`
launcher, the corresponding Windows `.exe` name, or the supported Windows
`py -3.x` form; Click still requires an explicit shell-free `-m` runner.

Output names a new private directory outside the project containing:

- `analysis.json`: parent test IDs, collection conditions and dependency candidates.
- `proposal.json`: adapter, source/project/inventory identity, review state,
  actual child inventories, comparison, grouping reasons and limitations.
- `.click/evidence-shards.json`: proposed v1 exact parent-to-child mapping.
- `.click/evidence-dependencies.json`: proposed v1 per-child dependency candidates.

The last two files are present only when generation and equivalence succeeded.
They are proposals within that private directory. The target project's `.click`
directory and Git index are not changed. Every result has `authority: false`
and `reuse_ready: false`; metadata is not execution or reuse authority.

Whole modules sharing a filename stay together under an exact discovery pattern.
Other modules remain separate, preserving start/top/import conditions and test
filters. IDs derive from sorted relative module files, so source-only line edits
do not redistribute the plan. Common transitive imports, fixtures, data and
configuration explain dependency overlap and review decisions. With no measured
timing samples, balancing and profitability remain unmeasured. There is no
method-level splitting or claim that a small suite benefits from decomposition.

Both parent and children are collected twice in fresh processes. The adapter
checks the test-ID multiset, source identity, whole-module ownership, runtime,
cwd and project snapshot. This is not proof of fixture/order/global-state
semantics. Known mutable shared state, repeated module fixtures, unresolved
dependencies, empty child groups and uncollected matching files prevent an
automatic proposal. Static analysis is bounded and cannot prove arbitrary
Python program independence; human review and later runtime evidence are needed.

Limits include 64 shards, 64 child arguments, 128 test modules, 10,000 test IDs,
256 KiB per proposed policy, 30 seconds per collection and a 120-second proposal
collection budget. Snapshot and dependency-source bounds are in the Phase 0
plan. A single group, exceeded bounds or unknown semantics returns a reason
instead of a misleading successful split.

Existing policy bytes are preserved. Existing dependency entries remain in the
proposal, and their entire declared scope is inherited by each new child.
Existing shard/reuse settings require explicit reconciliation; the shard proposal
is a candidate addition/replacement for review, never an automatic overwrite.
Malformed existing dependencies block proposal generation. No safe-change reuse
policy is generated. Even a schema-valid proposal remains pending human review.

Added, deleted or renamed tests require regeneration and another real equivalence
check. The runtime's committed shard-map inventory guard remains authoritative
for decomposition and falls back to the original parent when coverage drifts.
Application, commit confirmation, baseline verification and authoritative runtime
observation belong to later approved workflow phases.

The independent retail and science fixtures in `tests/test_click_shard_proposal.py`
generate these policies without handwritten shard JSON, including filtered nested
discovery and project paths containing spaces. Negative fixtures cover omitted
or duplicate children, invalid argv, shared fixtures/state, changed inventory,
existing settings, grouping limits and unsupported splits.
