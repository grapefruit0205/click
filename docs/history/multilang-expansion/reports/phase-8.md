# Phase 8 report — readiness status and multilingual guidance

Status: **complete**

Phase 8 separates command execution, automatic inventory/split, same-state
exact reuse, committed-policy reuse, and authoritative-observation reuse in the
sharding status and dashboard projection. A current baseline now reports exact
reuse as available when Observer is off instead of presenting all reuse as
unavailable. Sharding and observation readiness remain separate.

Dashboard projection v9 carries sanitized readiness tokens and keeps v4-v8
read compatibility. The first screen shows all five states and one next action.
Korean, English, and Simplified Chinese locale keys match. Status inspection is
covered by a test that rejects any attempt to collect tests or execute checks.

The three READMEs, operating modes, automatic sharding guide, and verification
profiles now describe the same actual tool-profile boundary. Verified fixtures
and recognized-only native profiles are presented separately.

The final focused regression passed 34 tests in 27.085 seconds on local Linux.
No native macOS or Windows UI execution was performed in this checkout.

See `../STATUS_UX.md`, `../CAPABILITY_PHASE8.json`, and
`../logs/phase-8-verification.md`.
