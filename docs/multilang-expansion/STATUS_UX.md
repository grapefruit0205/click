# Verification readiness and dashboard status

`click-gate sharding status` and dashboard projection v9 expose five separate
readiness facts:

| Field | Meaning |
| --- | --- |
| Command execution | The exact command matches a supported adapter profile. |
| Automatic inventory/split | The adapter can collect and create exact children within its documented profile. |
| Same-state exact reuse | A current PASS receipt exists for unchanged bindings. |
| Committed-policy reuse | A valid owner-committed safe-change receipt exists for all or some groups. |
| Observation-based reuse | Every relevant child has a complete current authoritative observation. |

The aggregate `reuse_ready` remains for compatibility, but now becomes true
when exact reuse is available; consumers must read the separate status fields
to know which route applies. `sharding_ready` still describes the committed
split and baseline only. Neither value creates authority.

Status is read-only. It validates bounded repository metadata and persisted
setup identity, but does not collect tests or execute a project command. The
dashboard copies only sanitized status tokens into its read-only projection and
maps them to Korean, English, and Simplified Chinese in the browser. Projection
v4 through v8 remains readable.

Observer is optional. An unchanged request can use exact evidence and a later
safe change can use a valid committed policy while Observer is off.
Authoritative observation remains a separate Guarded capability.
