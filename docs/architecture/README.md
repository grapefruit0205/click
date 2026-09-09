# Maintained architecture documents

- [Verification economics and file inputs](verification-economics.md) records
  parent-relative costs, scoped owner policy, stable file groups and paired sessions.

- [Runtime optimization](runtime-optimization.md) records the current runtime
  boundaries and measured optimization work.
- [Revalidation savings](revalidation-savings.md) describes the design used to
  expose shard reuse and saved verification work.
- [Child verification continuity](child-verification-continuity.md) describes
  per-command dependency scopes and evidence retention across reviewed shard refreshes.
- [CI change scopes](ci-scope.md) describes documentation/version checks,
  conservative full-matrix selection, and required-check aggregation.

Completed implementation phases and their raw evidence are kept in
[`../history/`](../history/README.md). Runtime behavior is defined by the source
and the canonical references under [`../../skills/`](../../skills/).
