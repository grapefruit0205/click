# Mixed-project partial verification boundary

The Phase 7 fixture combines a Vitest frontend, Python `unittest` backend, Go
package, shared JSON schema, Markdown guide, and static assets. Each check
declares its local and shared inputs explicitly. Click binds those declarations,
the discovered input membership, command, working directory, runtime, and
configuration to the result ledger.

Cross-revision partial reuse is available only through the committed
`.click/evidence-reuse.json` policy in the fixture. The adapter's dependency
output remains a candidate and cannot create PASS evidence or reuse authority.
Without the owner policy, a frontend-only change conservatively runs all four
checks. With it, unaffected checks may use `reuse-safe-change` while the owning
check runs.

Shared-schema changes run the frontend, backend, and Go checks. Package-lock
changes run both Node-based checks because their runtime/configuration identity
is affected. New tests, dynamic imports, and asset membership changes are bound
by the declared directory inputs. Failed checks remain failed until a later
execution succeeds; omitted checks require an exact or policy-backed receipt.

The fixture does not infer a general dependency graph, restore build outputs,
or enable runtime-authoritative reuse for JavaScript or Go. Those conditions
continue to fall back to a parent execution when no existing authority applies.
