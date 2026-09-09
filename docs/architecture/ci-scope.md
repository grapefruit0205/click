# CI change scopes

CI always starts for pull requests, pushes to `main`, release-tag pushes (`v*`),
and manual dispatch. It does not use workflow-level path filters.

| Scope | Selection | Checks |
| --- | --- | --- |
| `docs` | Only the README translations, release notes, community/security documents, or supported files under `docs/` | Distribution/version parity, maintained local documentation links, repository/distribution tests, whitespace |
| `release-metadata` | Those documents plus only the version in `.codex-plugin/plugin.json` and/or the Click tag ref in `.agents/plugins/marketplace.json` | Same repository checks; release notes, README versions and marketplace ref must agree |
| `full` | Everything else, an unknown comparison, manual dispatch or release-tag push | Repository checks and the entire existing platform/integration matrix |

Changes to Hook/runtime code, tests, dependency/reuse/shard policy, skills,
governing policy documents, CI, or unclassified files select `full`. Manifest
fields other than the exact version/ref are compared structurally, including
JSON value types. A changed URL, permission or execution path selects `full`.
Symlinks, executable documents and file-type changes also select `full`.

## Comparison and required checks

[`ci_scope.py`](../../scripts/ci_scope.py) compares the complete local Git diff.
Pull requests compare the event's base commit to the actual merge checkout;
pushes compare `before` to the checked-out `after`. It verifies the checkout and
ancestry, examines both sides of renames as deletion/addition, and does not rely
on a truncated webhook file list or a changed-path API. Missing history, invalid
input and empty comparisons select `full`. A failed classification job causes
the full jobs to run and prevents the required aggregate from succeeding.

The three established `deterministic-tests` names remain required-check
aggregates. They require successful planning and repository checks. Full scope
also requires every partition and integration job to succeed; reduced scope
requires the matrix jobs to be skipped. These aggregate names do not claim that
macOS or Windows runtime tests executed on a documentation-only change.
Failures, cancellation, missing jobs or an unknown scope cannot produce a
passing aggregate. The dependency/skip handling follows the
[GitHub Actions job dependency rules](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idneeds).

## Documentation targets

[`check_doc_links.py`](../../scripts/check_doc_links.py) checks repository file
targets in maintained Markdown: inline links/images, reference definitions,
and HTML `href`/`src`. It ignores fenced/inline code examples and does not fetch
external URLs or validate heading anchors. Historical phase artifacts under
`docs/history/`, generated distribution copies and test fixtures are excluded
from this check. Distribution parity has its own validator.

With a base commit, unchanged pre-existing broken targets do not become new
failures. New broken links and removal of an existing linked target do fail.
Without a usable base, all maintained local targets are checked.

## Validation

Repository tests exercise real Git push/merge comparisons, version-field
classification, renames, missing history, release/manual events, documentation
targets and the actual required-check shell script. `actionlint` validates the
workflow syntax. Local checks do not represent a hosted Windows/macOS CI run.
