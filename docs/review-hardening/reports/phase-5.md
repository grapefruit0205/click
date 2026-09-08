# Phase 5 — bounded-allocation JSON report cleanup

Status: passed locally. Start/end HEAD remain `d48d327c7a6836dbe11b3d331b49420b4bb1058c` on `codex/review-hardening-0.92.0`, with the previous phase changes retained in the dirty working tree. The pre-implementation red run fingerprint is `2829846a3b709c70006779048fc7336efa75019da65c70dbdbeefeabc19abe33`; the final source/test fingerprint is `97e2af1d709de9e92dcae80133250c098d09ca9a0c3fcf2c95792a9592674994`. Every focused check and measurement records its exact command, dirty list, environment and start/end fingerprint in `../evidence/phase-5-cleanup-*.json`. No commit, push, release or user policy edit occurred.

## Reached path and change

`hooks/click_gate.py` still attempts inline JSON command transport first. Only a renderer refusal (`exit 2`) with a report at most 2 MiB creates a one-shot fallback in `click_state.state_root()` and invokes cleanup. The ordinary inline path does not perform cleanup. The state root remains `$PLUGIN_DATA/gate-state`, or the existing temporary-directory fallback.

The reproduced resource/progress problem was `list(root.glob(...))[:128]`: all matching `Path` objects were created, while only the first 128 candidates were examined. CPython 3.12.3's glob also materialized all directory entries. A fresh prefix prevented examination of an expired suffix. This is a resource/retention defect, not a verification-authority bypass.

One local `_prune_json_reports` helper now streams `os.scandir` and deletes at most 128 expired regular reports. Fresh entries and unsuccessful deletions do not consume this deletion budget, so scanning reaches older reports behind them. The iterator is closed when the budget is reached or iteration finishes. The successful-deletion limit is explicit; directory enumeration, metadata calls and fallback latency remain O(N). There is no directory-wide list, sort, index, cursor, background service or new retention policy.

Cleanup accepts only the writer's exact filename shape: `.click-json-report-` followed by 32 lowercase hexadecimal characters and `.json`. Metadata is requested with `follow_symlinks=False`; directories and symlinks are preserved. The currently written filename is excluded before metadata inspection, even if its timestamp appears old. The TTL remains **strictly older than 3,600 seconds**. Opening/enumerating/stat/deleting failures remain best effort and do not determine verification authority or alter the new report's result. Report write failures and final runner-render failure retain their prior explicit behavior.

Only `hooks/click_gate.py` and the existing `tests/test_click_runner_transport.py` changed for this phase. The new `ClickJsonReportCleanupTests` class lives in the existing, shard-owned test module. No new test module or `.click/evidence-shards.json` edit was needed.

## Compatibility and classifications

- **Reproduced defect fixed:** complete candidate-list allocation and no progress past a continuously fresh leading set.
- **Defensive ownership/no-follow restriction:** the old cleanup glob and reader accept broader `prefix*.json` names. The writer already emits 32-hex names. Cleanup now leaves older/custom broader names alone; the unchanged reader can still consume those files once. Thus legacy readability is preserved, but automated TTL deletion of non-writer-shaped legacy files is intentionally no longer attempted. This is not represented as a new receipt format or external-file deletion fix.
- **Already protected, retained with tests:** strict TTL boundary, new-report exclusion, cleanup filesystem error tolerance, explicit report write failure and one-shot read/delete behavior. Earlier probing showed that old cleanup could stat through a symlink but removed only that link, not its outside target.
- **Remaining limit:** no global disk-usage bound or constant-time guarantee. A directory of all-fresh matching reports needs metadata checks for the whole set. Progress requires future fallback invocations. Metadata inspection and unlink are not atomic against same-user path replacement; replacing a path with a directory produces a tolerated deletion error. `unlink` of a replacement symlink cannot delete its outside target. External edits after the final metadata check are not claimed to be fully serialized.

No Evidence/Guarded/Off semantics, verification receipts, reuse decisions, authorization, claims, reader format, dashboard design or observer behavior changed here. Native Windows/macOS performance and actual Windows open-file deletion were not executed; the permission-refusal error boundary was tested deterministically on Linux. Symlink creation succeeded in the local tests, so no Linux cases skipped. Python 3.10 test API compatibility was reviewed and the initial `enterContext` use was replaced with existing `start`/`addCleanup` patterns; native execution on other Python versions is deferred to CI.

## Focused validation

Commands ran through `/tmp/click-review-hardening-20260908/run_check.py` with isolated temporary roots, `python3 -B`, Python 3.12.3 and Linux 7.0.0-31-generic x86_64/glibc 2.39.

| Record | Exact unittest selector | Result |
|---|---|---|
| `phase-5-cleanup-red` | `tests.test_click_runner_transport.ClickJsonReportCleanupTests -v` | 13 methods: 5 passed, 8 failed, 0 errors/skips; exit 1, before implementation |
| `phase-5-cleanup-green` | `tests.test_click_runner_transport -v` | 20 passed, 0 failed/skipped; exit 0 |
| `phase-5-cleanup-final` | `tests.test_click_runner_transport -v` | **21 passed**, 0 failed/skipped; exit 0, 0.550468 s wrapper elapsed |

The red result includes new no-follow metadata instrumentation absent from the old implementation; eight failures are not claimed as eight independently reproduced runtime vulnerabilities. The decisive resource/progress failures are eager iteration beyond the cap and the unchanged expired suffix. Other cases establish the selected cleanup ownership contract or preserve already-supported error handling.

Final coverage includes empty and small directories, 3,073 existing matching/nonmatching files, strict TTL, exact filename shape, 128-deletion early stop, 128 fresh reports followed by 300 expired ones, repeated progress, failure without budget consumption, missing candidates, stat/iteration/open errors, path replacement, symlinks/directories, new-report timestamp anomaly, writer failure, renderer failure and one-shot legacy reading. With 300 expired files behind the fresh prefix, three calls leave **172 → 44 → 0** expired files, preserving fresh and newly written reports. The pre-existing Unicode/transport/one-shot tests also pass. `git diff --check` on both changed source/test files passed.

## Paired cleanup measurements

Phase 0 had acknowledged concurrent-task noise and counted `Path.stat` only. For the new scandir implementation, the same v2 harness now counts both `DirEntry.stat` and `Path.stat` in separate instrumentation runs and orders the starvation fixture through `os.scandir`, making both versions comparable. The old Phase 0 raw results are retained unchanged; they are not mixed into this comparison.

Two measurement blocks ran in order **baseline → current, current → baseline**, with no other heavy test or benchmark started by this task running. Each source/case/mode/block had two warmups, eleven wall-time samples and three separate tracemalloc samples: **22 timing and 6 peak-allocation samples per table cell**. Actual tempfile creation, fixture population and removal of each new report are outside timing. Fallback is forced only at the inline renderer refusal; the real JSON writer, cleanup and bounded command rendering execute. Timings include the small mock context. Source imports are warmed, OS caches are not flushed and scheduling is uncontrolled on the working desktop. These are descriptive small samples, not delay guarantees or confidence intervals.

| Existing writer reports / other files | Fallback median ms, before → after | Python peak allocation bytes, before → after | Existing-report metadata calls, before → after |
|---|---:|---:|---:|
| 0 / 0 | 0.469350 → 0.384491 | 12,072 → 12,144 | 0 → 0 |
| 16 / 0 | 0.512937 → 0.419219 | 18,668 → 11,384 | 16 → 16 |
| 6,000 / 0 | 14.985912 → **20.291900** | 4,937,103 → 11,816 | 128 → 6,000 |
| 16 / 6,000 | 3.407992 → **3.595193** | 2,010,290 → 11,384 | 16 → 16 |
| 6,000 / 6,000 | 17.709567 → **24.913390** | 6,930,290 → 11,384 | 128 → 6,000 |

The bold after-times are slower and retained. Reaching an expired suffix costs more metadata work than the old first-128-only behavior. At 6,000 matching + 6,000 other files, before samples ranged 17.080–26.692 ms (sample SD 3.183), after 24.309–26.149 ms (SD 0.480). Both versions enumerate 12,001 entries including the new report. The old path creates 6,001 matching `Path` objects; the new path creates none through glob and performs 6,000 no-follow metadata calls. Nonmatching files get no metadata request. Fresh fixtures have zero deletions. Peak measures Python allocation, **not total RSS**, and demonstrates directory-size-independent retained allocation in the new implementation, not a global process-memory ceiling.

The identical deterministic 128-fresh/8-expired actual-file scenario leaves `[8,8,8,8,8,8]` after six baseline calls and `[0,0,0,0,0,0]` after current calls. Current deletes eight on the first call and zero thereafter; all fresh/new files survive. Inline calls have no cleanup operations and pooled medians of approximately 0.004–0.013 ms. No whole development request, human approval time, token usage or token-saving percentage is measured.

Exact gate source SHA-256: before `dadaa5f7e80e719cdf0ffa0c74fad7752b32573c2b192211352d06eb8c47fe6a`, after `d3bcd14b1a1302babdc1b708b4cafd83b439f0a307d51f0734c0c3ed1b91da78`. The final code/test fingerprint stayed unchanged through all four measurement subprocesses.

Each measurement command has the following form; the exact four argv arrays and exit 0 results are stored in `../evidence/phase-5-cleanup-benchmark-pair-*.json`:

```bash
python3 -B /tmp/click-review-hardening-20260908/run_check.py phase-5-cleanup-benchmark-pair-1-baseline -- python3 -B /tmp/click-review-hardening-20260908/report-cleanup/measure_report_cleanup_v2.py --source /tmp/click-review-hardening-20260908/baseline --output /tmp/click-review-hardening-20260908/report-cleanup/pair-1-baseline.json
```

The current source path is `/home/grapefruit/Documents/Codex/2026-09-06/click-live-0-81-0-codex/work/click-release`. Pair 2 reverses source order. Raw arrays and source hashes remain in `/tmp/click-review-hardening-20260908/report-cleanup/pair-{1,2}-{baseline,current}.json`; full command logs are `/tmp/click-review-hardening-20260908/phase-5-cleanup-*.log`. `measure_report_cleanup_v2.py` and `summarize_pairs.py` remain alongside them. The four raw measurement blocks and these two scripts are also preserved in `../evidence/report-cleanup-raw/`. The pooled statistics, block medians, operation counts, source hashes and deterministic progress results are copied to `../evidence/phase-5-cleanup-paired-summary.json`.

## Handoff

Phase 5 source and focused validation are complete. Phase 6 owns final full regression, manifest/distribution regeneration/parity, inventory coverage and settled-source workflow comparison. The prepared Phase 6 Guarded benchmark harness has **not** been run by this phase.
