# Phase 9 report — final inventory and release assessment

Status: **complete**  
Release ready: **no**

The official discovery contains 1,030 unique tests. Four class-preserving
partitions cover 207, 251, 253, and 319 tests with no missing or duplicate IDs.
The final local results are 1,023 passed, 7 platform/tool skips, and 0 failures.
Partition durations were 211.830s, 135.199s, 227.446s, and 141.870s.

The first parallel pass exposed an omitted mixed-project owner, a stale
Antigravity distribution, and an unregistered resident Hook host adapter. Those
were fixed. Two timing-sensitive setup fixtures also selected the conservative
whole-parent route under parallel load; the same tests passed sequentially.
The failed first-pass logs are retained rather than overwritten. Final
repository policy, distribution, shard ownership, and compatibility tests
passed 54/54 after the last README and CI edits.

CI now covers deterministic Linux/macOS/Windows partitions, pinned Vitest and
Jest fixtures, Node/npm/Go, the mixed project, CPython 3.10–3.14, pytest 9.1.1,
Linux/macOS/Windows authoritative observer contracts, Rust offline, Gradle
offline, .NET no-restore, TypeScript no-emit, CMake/CTest, and bounded content
tools. The newly added jobs have not run remotely, so they are assignments and
requirements rather than pass claims.

The canonical Antigravity distribution was rebuilt. Distribution validation,
Python compilation, workflow YAML parsing, and `git diff --check` passed.

No new repeated performance experiment was run because the user stopped
measurement. Existing component evidence remains: a warm resident Hook event
median of 60.921ms versus 104.192ms through one-shot startup (41.5% lower), with
a 253.484ms first worker event; warm new-process import median 95.448ms versus
130.548ms (26.9% lower); and shared executable binding that improves the 8- and
64-check cases while adding a small single-check cost. These are Linux
component measurements, not whole-task or token-savings claims.

Release readiness remains false until the new native/cross-platform CI jobs
pass and their results are attached to the target revision. No commit, push,
tag, release, merge, or reinstall was performed in this phase.

See `../FINAL_REPORT.md`, `../CAPABILITY_PHASE9.json`, and
`../logs/phase-9-verification.md`.
