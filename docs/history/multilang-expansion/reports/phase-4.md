# Phase 4 report — content validation profiles

Status: **complete**

## Purpose and result

Phase 4 added a non-authoritative content-validation adapter for explicit,
read-only checker commands. It does not infer support from an extension and it
does not parse project content inside Click. Project-owned npm validators prove
JSON schema, YAML schema, Markdown local-link, and SVG format/dimension/reference
checks through the existing Hook, claim, runner, and ledger path. A provisioned
jq 1.7 binary separately proves a direct JSON parser profile.

## Changed call paths

- `click_verification_adapters.py` recognizes bounded jq, schema, YAML,
  Markdown, SQL lint, XML, image inspection, and package validation shapes and
  rejects known write or network-capable variants.
- `click_runtime_identity.py` binds validator configuration and the npm/Node
  runtime chain. Unbounded schema fetching is unsafe and is denied before the
  runner.
- `test_click_content_validation.py` supplies actual one-shot project checker
  processes with explicit v3 inputs, including shared schemas, missing local
  references, ignored opt-in content, Unicode/space paths, and the input size
  limit.
- Official shard/reuse manifests and the generated Antigravity distribution
  include the new validation coverage.

The exact profile guarantees and exclusions are documented in
`../CONTENT_VALIDATION.md`; capability evidence is in
`../CAPABILITY_PHASE4.json`.

## Verification

The final command-classification and gate suite passed 159/159. The added
oversized-input integration passed independently. Automatic sharding
init/status/refresh, proposal, reuse, and parent fallback passed 66 tests with
two existing environment skips. Distribution and repository inventory checks
passed 10/10. The official inventory contains 996 unique tests. See
`../logs/phase-4-verification.md` for commands and exact timings.

## Remaining limits

- The project fixtures prove Click's validator boundary and their documented
  semantics; they do not claim feature parity with third-party validators.
- Direct yamllint, markdownlint, sqlfluff, xmllint, identify, and
  check-jsonschema executions remain locally unverified because those CLI tools
  are absent or their transitive network behavior is not bounded.
- Markdown remote links, SQL database execution, migrations, formatting writes,
  generated docs artifacts, visual correctness, and pixel comparison remain
  outside this phase.
- Automatic inventory and sharding are unchanged and remain Python-only at this
  point.

## Next condition

Phase 5 may start because zero-test validation commands can create evidence
only through a successful real process, all transitive local fixture inputs are
bound or explicitly non-reusable, write/network shapes fail closed, and the
mandatory automatic-sharding regression remains green.
