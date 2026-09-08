# Content validation profiles

Phase 4 treats JSON, YAML, Markdown, SQL, and static assets as explicit
validation work. A suffix alone never creates evidence. The submitted argv,
working directory, selected executable, runtime/config identity, explicit
inputs, and reuse policy remain part of Click's existing verification
contract.

The static adapter recognizes bounded read-only forms of `jq`,
`check-jsonschema`, `yamllint`, `markdownlint`, `sqlfluff lint`, `xmllint`, and
ImageMagick `identify`, plus npm scripts whose name begins with `validate` or
`verify`. It rejects `markdownlint --fix`, `sqlfluff fix`, XML formatting or
shell modes, image write modes, arbitrary jq programs, and XML checks without
`--nonet`. These profiles only classify a command; they cannot issue approval,
PASS evidence, or a receipt.

Project validation scripts are supported through the existing package-script
boundary. Phase 4 fixtures use one explicit npm script per content kind and
declare every source, local schema, Markdown target, and SVG reference in the
v3 `inputs` list. This keeps parsing and project semantics in the project's
checker rather than in Click. A validator can succeed without test IDs; that
does not change the separate rule that test discovery with zero tests is not a
general PASS.

External schema fetching is blocked for `check-jsonschema`, including local
schema commands whose transitive `$ref` network behavior cannot be bounded.
The Markdown fixture checks local links and deliberately ignores HTTP, HTTPS,
and mail links. Its PASS therefore makes no statement about remote
availability. SQL is registered only for the read-only `sqlfluff lint` shape
and remains locally unverified. Database migrations and formatter write modes
are outside the verification profile.

Explicit inputs retain the Phase 2 limits. Missing local references are a
stable input state and the checker can fail on them. Symlinks, sensitive
matches, races, and files over 8 MiB do not become reusable evidence. An
oversized JSON fixture proves that a valid checker may still run while the
same request remains non-reusable.

The asset fixture validates SVG format, positive declared dimensions, and
local reference existence. It does not prove visual meaning, rendering
quality, accessibility, or pixel equivalence. Documentation build outputs are
not created in this phase; a future build command that requires artifacts must
use `outputs_required` and cannot reuse a PASS as though an artifact had been
restored.
