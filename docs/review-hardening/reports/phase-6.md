# Phase 6 — final regression, comparison and distribution readiness review

Status: passed within the user's final measurement scope. Complete local regression and distribution parity are verified; the planned repeated performance experiment stopped at the user's explicit request and remains incomplete. No background measurement is scheduled. Native Windows/macOS validation was unavailable on this Linux host and is delegated to the subsequent release PR's own CI.

## Source and execution identity

Start/end Git HEAD remains `d48d327c7a6836dbe11b3d331b49420b4bb1058c` on `codex/review-hardening-0.92.0`. The working tree contains the uncommitted changes from Phases 1–5, generated distribution updates and this record. The Phase 0 reference is a separate `git archive` checkout at `/tmp/click-review-hardening-20260908/baseline`; the original archive SHA-256 is `3bd84128771a1abfdb39eaf720ad0006ad62f5879fdd8e480c29c594645e2eb5`.

The full regression, inventory, distribution and compilation started with code/test fingerprint `97e2af1d709de9e92dcae80133250c098d09ca9a0c3fcf2c95792a9592674994`. An isolated test-fixture correction produced `42739e8239af6ee2182d65b793df32221fdfde9b3781e2d2a21f398bd9e78b60`; product source did not change after the full run. The official affected partition passed at that final fingerprint. These fingerprints cover `hooks`, `tests`, `scripts`, `benchmarks` and `.github`, not documentation or generated distribution copies. The distribution validator checks the latter separately. Exact dirty lists and start/end fingerprints are in each command's evidence JSON.

Local environment: Linux 7.0.0-31-generic x86_64, glibc 2.39; CPython 3.12.3. Full regression used the already-existing `/tmp/click-release-pytest-shyO6a/venv/bin/python` with pytest 9.1.1. Component measurements use the same resolved `/usr/bin/python3.12` for both source versions. No dependency was installed. Read-only source inspection and document writing could occur during measurement; no other test or benchmark from this task ran concurrently with the workflow comparison. This is a working desktop, without CPU isolation, controlled background applications or flushed OS caches.

At the measured Phase 6 boundary, the source, marketplace and installed version remained **0.92.0**. No commit, push, PR, tag, release, remote workflow dispatch, install/reinstall, automatic stash/reset/clean or privilege elevation occurred during the Phase. `.click/evidence-shards.json` and `.click/evidence-reuse.json` remained unchanged. Afterward, the user explicitly authorized packaging this result as **0.93.0**, opening and merging a release PR, publishing the release, reinstalling it, and cleaning up the branch. Metadata-only version changes are validated separately and are not folded into the earlier source/test performance fingerprints.

## Official regression and the one fixture correction

The current `.github/workflows/ci.yml` uses the official `scripts.run_ci_tests` discovery/partition runner. Local listing found **951 unique test IDs**. The four disjoint partitions contain **97, 280, 291 and 283** tests and their union equals the complete inventory. New cases are in existing owned modules: 15 `ReviewHardeningGateTests`, 10 `VerificationLifecycleBoundaryTests`, and 14 `ClickJsonReportCleanupTests` are included. Other new unit cases also occur in existing discovered modules. No new test file requires a user shard-policy change.

| Run | Passed | Failed | Errors | Skipped | Exit | Wrapper elapsed |
|---|---:|---:|---:|---:|---:|---:|
| Official full inventory, initial run | 944 | 1 | 0 | 6 | 1 | 787.329531 s |
| Isolated reproduction of the failing fixture | 0 | 1 | 0 | 0 | 1 | See command record |
| Corrected fixture plus directly related claim/lifecycle checks | 14 | 0 | 0 | 0 | 0 | 13.673864 s |
| Official partition 1 after fixture correction | 97 | 0 | 0 | 0 | 0 | 215.411791 s |
| Final unique coverage, combining valid records | **945** | **0 unresolved** | **0** | **6** | Composite, not one process | Not summed as a speed metric |

The initial failure was `tests.test_click_gate_verification.ClickGateVerificationTests.test_synthetic_noncontiguous_results_cannot_create_false_receipts`. After obtaining a real one-use claim, that synthetic direct-record fixture removed every `_click_` key, including the newly required invocation-local `_click_claim_binding`. The real runner retains that binding. The fixture now retains the original claimed binding while continuing to remove unrelated execution context. Its original assertions still require the first failed source to fail, the second complete source to pass, the absent source to stay unverified and the overall batch to fail. No assertion was weakened and no runtime check was bypassed.

The full-run failure is retained, not replaced with a fabricated clean run. After the isolated correction, official partition 1 was rerun. Results for the other **854** test IDs are reused from the full run: their product inputs and fixtures did not change; the only change was inside the corrected test method in partition 1. Thus final complete local coverage is **945 passed and 6 skipped**. There was no second complete run. The per-ID inventory, selected result provenance, duplicate/missing-ID check and skip reasons are recorded in `../evidence/phase-6-final-coverage.json`.

### Acceptance boundaries exercised

| Boundary | Final verification evidence |
|---|---|
| Automatic sharding init/status/refresh and setup | Official full-run collection, proposal, refresh/state machine and Hook setup tests; real pytest integration ran in the existing pytest environment. |
| Shard reuse and failure isolation | Nested/library partial reuse, related-input rerun, unchanged siblings after failure, exact/cross-revision/successor and per-shard safe-change cases passed. |
| Evidence / Guarded / Off | Existing mode and host-authority regressions plus independent successor Guarded approval and invalid-state cases passed. |
| Decision provenance and revision admission | Valid revision 0/positive and permitted reuse remain supported; malformed revisions and cross-check/baseline/context decisions are rejected at tested boundaries. |
| Observation coverage | Linux supported observation and authoritative cross-contract tests passed; incomplete/unsupported observations remain non-authoritative. Native implementations on other OSes were not run. |
| One-use execution and result lifecycle | Replay, cancellation, late result, reservation lifetime, identity drift, collection boundaries and atomic-write/recovery failure tests passed. |
| Receipt and display boundaries | Existing receipt export/verify, successor provenance, dashboard read-only, sharing/privacy and public-output tests passed. Reads/exports do not initiate model calls or tests. |
| Process cleanup and report fallback | Linux same-group survivor regressions and existing process tests passed; all fallback cleanup/transport tests passed. |

Focused runs from earlier phases establish particular red/green behavior and are not added to the final unique test count. Passing these fixtures confirms their asserted boundaries, not the completeness of every user's verification commands or every possible external edit schedule.

### Exact command paths

The local wrapper records argv, cwd, environment, exit, counts, elapsed time and input identity; its child output is retained at `/tmp/click-review-hardening-20260908/<record>.log`. Its command shape is:

```bash
python3 -B /tmp/click-review-hardening-20260908/run_check.py <record> -- <child argv>
```

All test/build children run from `/home/grapefruit/Documents/Codex/2026-09-06/click-live-0-81-0-codex/work/click-release`. Relevant exact child argv:

```bash
python3 -B scripts/build_antigravity_distribution.py
python3 -B scripts/validate_distribution.py
/tmp/click-release-pytest-shyO6a/venv/bin/python -B -m scripts.run_ci_tests --parts 4 --list
/tmp/click-release-pytest-shyO6a/venv/bin/python -B -m scripts.run_ci_tests --timings-out /tmp/click-review-hardening-20260908/phase-6-full-durations.json
/tmp/click-release-pytest-shyO6a/venv/bin/python -B -m scripts.run_ci_tests --parts 4 --part 1 --timings-out /tmp/click-review-hardening-20260908/phase-6-part-1-durations.json
/tmp/click-release-pytest-shyO6a/venv/bin/python -B -m compileall -q hooks evals scripts benchmarks tests dist/antigravity/hooks
git diff --check
```

Build, distribution validation, inventory listing and compilation exited 0; whitespace inspection passed. Compilation used a `PYTHONPYCACHEPREFIX` outside the checkout. The exact focused reproduction/correction selectors are retained in `phase-6-noncontiguous-red.json` and `phase-6-noncontiguous-fix-focused.json`.

## Platform and runtime limits

The six skips have two different causes; they are not six platform skips:

- Windows native argv round-trip (`test_windows_encoded_runner_round_trips_without_shell_expansion`).
- Windows native launcher fallback when `py` is broken (`test_broken_py_launcher_falls_back_to_python`).
- Windows native command Hook integration (`test_hooks_execute_via_cmd_and_exec_aliases_rewrite`).
- Windows native ETW smoke and macOS native `fs_usage` smoke.
- Missing-pytest control (`test_missing_pytest_is_an_explicit_safe_unsupported_result`) because pytest is installed in the chosen full-run environment. The actual pytest collection/proposal integration ran instead; the exact missing-dependency path is not claimed as executed in this final run.

Exact test IDs/reasons from the log take precedence over shortened names above and are preserved in the coverage record. Linux native execution and deterministic Windows/macOS mocks do not validate their native platforms.

CI defines Python 3.10 deterministic partitions on Linux/macOS/Windows, native authoritative jobs on macOS/Windows with Python 3.12.3, additional Linux runtime compatibility for 3.10/3.11/3.13/3.14, and pinned pytest integration. These workflows were inspected but not dispatched. Local runtime execution was CPython 3.12.3 only; compatibility review caught and removed a Python-3.11-only test API during Phase 5. Native Windows/macOS checks and the remaining Python matrix are still release constraints. Historic 0.92.0 CI results are not evidence for these uncommitted changes.

## Paired workflow experiment

Measurement has stopped. **Nine valid CLI reports** completed: four warmups, four measured reports making **one complete frozen/current pair per workload**, and one additional short-workload frozen report without its current counterpart. Warmups and the unmatched report are not used in the comparison below. Planned three-pair replication did not finish; variance cannot be estimated from one pair and is not represented as zero. No further measurements or tests were started to finish this report.

An initial attempt completed eight CLI calls and was interrupted during the ninth after a comparability problem was identified: the archive had no Hook bytecode cache while the current checkout had 230 `.pyc` files, including 65 for CPython 3.12. `-B` prevents cache writes but still permits reads. The observed timing difference therefore cannot be attributed to source changes. All eight original raw results and the interruption record remain at `/tmp/click-review-hardening-20260908/workflow-comparison/final-results`; their exclusion is recorded in `../evidence/phase-6-excluded-workflow-attempt.json`. They are not pooled with the replacement experiment. Only this task's owned benchmark processes were stopped; their exit was checked before restarting. No existing source or bytecode cache was deleted.

The replacement `controlled-results` run gives every child an empty, isolated `PYTHONPYCACHEPREFIX` with `-B` and `PYTHONDONTWRITEBYTECODE=1`. It also removes `PYTHONPATH` and `PYTHONHOME` from both child environments. The cache prefix must remain absent before/after each call or that run is invalid. This controls the discovered asymmetry and also bypasses existing standard-library bytecode caches. Consequently these absolute times describe that explicit import condition, not normal warm-cache Hook latency. OS page cache remains uncontrolled.

Both source roots are on the same filesystem and use the same interpreter. The benchmark file is byte-identical. Fixtures create independent repositories and state/config roots, with Git environment isolation. The archive has no `.git`; its raw engine `commit: null` and `working_tree_modified: false` are not proof of a clean Git checkout. Source identity instead comes from the original Phase 0 archive and each run's full Hook/benchmark digest. Source-root Git metadata collection occurs outside the per-stage/setup timings but can affect the overall benchmark process metric. Path length and desktop scheduling remain small uncontrolled differences.

The controlled experiment was first interrupted when a user status question ended the active tool session. Eight completed valid reports were preserved. The incomplete ninth attempt had no completed run sidecar and is excluded, with its files retained under `interrupted-segment-1-short-trial-2-frozen/`. Before resuming, the source manifests, interpreter, cache-prefix state and all eight completed records were rechecked. The resumed frozen short-workload run completed; its current counterpart was then stopped at the user's explicit request. No completed result was rerun. The pause fell between completed frozen/current pairs, so both complete comparison pairs belong to the first segment. `resume-segment-2.json` records continuity, and `user-stop.json` records the final stop. Interrupted attempts do not enter the statistics.

### Results available at the stop

The workload presets are one and 40,000 PBKDF rounds per synthetic test. The latter is the existing benchmark's default workload; it is substantially more computation than one round, but is not a large production test suite and does not establish that setup costs are amortized. Each CLI covers three configurations, eight scenarios each, scripted Guarded approval controls and experimental same-state full audits. Source-version order rotates. These are component measurements on Linux with the explicit empty-bytecode condition described above, not user development time.

| Entire benchmark subprocess | Frozen source | Current source | Observed change |
|---|---:|---:|---:|
| Short workload, one measured pair | 140.557 s | 140.333 s | 0.16% lower |
| Default 40,000-round workload, one measured pair | 142.280 s | 142.770 s | 0.34% higher |

These small differences do not establish a speedup or slowdown beyond measurement variation. They also do not isolate the cost of one Click request: the subprocess includes all three fixture configurations, transitions, actual checks, audits, receipt handling and output.

The repeated unchanged request does isolate a relevant boundary: the added final input confirmation occurs before accepting reuse. Its observed request times increased in these samples:

| Unchanged prepared request | Frozen source | Current source | Observed increase |
|---|---:|---:|---:|
| Short, Click default | 746.791 ms | 796.468 ms | 49.676 ms / 6.65% |
| Short, explicit reuse | 782.233 ms | 899.261 ms | 117.028 ms / 14.96% |
| Default workload, Click default | 707.905 ms | 842.833 ms | 134.927 ms / 19.06% |
| Default workload, explicit reuse | 752.409 ms | 882.076 ms | 129.666 ms / 17.23% |

These are individual paired observations, not stable latency estimates or a causal allocation of every millisecond to the new check. They are compatible with a correctness cost and are retained rather than hidden by the near-equal whole-benchmark totals.

| Other components, frozen → current | Short workload | Default workload |
|---|---:|---:|
| First setup, Click default | 5.570 → 5.718 s | 5.916 → 5.923 s |
| First setup, explicit reuse | 5.883 → 5.792 s | 5.733 → 5.717 s |
| First validation, Click default | 1.885 → 1.941 s | 2.037 → 1.934 s |
| First validation, explicit reuse | 2.539 → 2.656 s | 2.697 → 2.542 s |
| Unrelated change, explicit reuse | 3.138 → 3.084 s | 3.161 → 3.305 s |
| Related change, explicit reuse | 3.129 → 3.337 s | 3.119 → 3.316 s |
| Setup + transitions + validation, Click default | 53.248 → 52.996 s | 53.027 → 53.228 s |
| Setup + transitions + validation, explicit reuse | 60.026 → 60.004 s | 60.572 → 60.865 s |

The last two rows exclude additional audits, untimed fixture bookkeeping and human decision time. The no-Click arm is not added into a derived subtotal because its audit field overlaps its parent validation. Failure and retry costs remain in the original per-stage data; they were not removed from validation subtotals.

Every completed report passed the existing workflow validator and source-digest checks, and all **216 completed stage/audit comparisons** agreed (nine reports × three configurations × eight stages). The designated failure stage failed, retry passed, unrelated/related shard behavior remained correct, and unchanged reuse stayed supported. This verifies the fixture outcomes, not test sufficiency for arbitrary projects.

Phase 5 provides a separate, better-repeated component result: with 6,000 reports and 6,000 other files, Python peak allocation changed from **6,930,290 to 11,384 bytes**, while median fallback latency increased from **17.709567 to 24.913390 ms**. That result has 22 timing and six allocation samples per version. It establishes reduced retained allocation and cleanup progress, with additional scanning cost; it is not total RSS or whole-task savings.

The completed raw reports, all stage metrics/outcomes, source manifests, stop/resume records and exact helper commands are preserved in `../evidence/workflow-comparison/`. `partial-results.json` explicitly identifies the complete pairs and excluded unmatched result. The earlier cache-asymmetric attempt is preserved separately in `../evidence/excluded-workflow-attempt/` and is not used in these results.

The controlled command was:

```bash
python3 -B /tmp/click-review-hardening-20260908/workflow-comparison/compare_guarded_workflows.py --baseline /tmp/click-review-hardening-20260908/baseline --current /home/grapefruit/Documents/Codex/2026-09-06/click-live-0-81-0-codex/work/click-release --output-dir /tmp/click-review-hardening-20260908/workflow-comparison/controlled-results
```

After interruption, `python3 -B /tmp/click-review-hardening-20260908/workflow-comparison/resume_guarded_workflows.py` resumed only incomplete scheduled work. The original 16-result aggregate was never produced, and no successful aggregate exit is claimed. All nine completed individual commands exited 0; interrupted attempts have no completed result. Whole-task duration and token savings remain unmeasured.

## Compatibility and changed files

Runtime changes stay in six existing owning modules: `hooks/click_evidence.py`, `hooks/click_change_policy.py`, `hooks/click_verification_reuse.py`, `hooks/click_verification.py`, `hooks/click_process.py` and `hooks/click_gate.py`. The official builder regenerated their six corresponding `dist/antigravity/hooks/` copies; the explicit module manifest is unchanged. No generated source was hand-edited. Distribution validation and the full parity tests passed.

Tests changed only in the existing `test_click_evidence.py`, `test_click_change_policy.py`, `test_click_verification.py`, `test_click_gate_verification.py`, `test_click_process.py` and `test_click_runner_transport.py` modules. `COMPATIBILITY_SURFACE.md` now describes the already-implemented Windows ETW backend and its real host prerequisites; the old placeholder wording was inaccurate. `docs/runtime-optimization.md` separates this unreleased work from historical 0.92.0 measurements. Phase reports, findings, progress and an unassigned release-note draft are under `docs/review-hardening/`.

There is no new private gate facade forwarder, import cycle, eager dashboard/native-backend import on normal Hook startup, broad state framework or dependency. The single documented legacy facade exception is unchanged. `_click_claim_binding` is an invocation-local value attached only after canonical batch/claim validation and consumed by the real runner's record/collection paths. It is not a persisted receipt-schema change, external API or transferable approval. Public commands, stored evidence/receipt versions and legacy receipt readability are preserved, while malformed success/current revisions are safely rejected.

Fallback cleanup deliberately accepts only the existing writer's 32-lowercase-hex filename shape. The unchanged reader can still consume broader legacy names once, but automatic cleanup leaves those non-writer names alone. The one-hour strict TTL and one-shot reader remain. Directory scanning is still O(N); deletion count and retained Python allocation are the bounded resources. See Phase 5 for the measured latency tradeoff and actual progress behind fresh entries.

## Classification and remaining risks

Reproduced defects fixed: coercive/malformed revision handling at reached boundaries, stale-input acceptance at deterministic late-reuse points, surviving same-group children during explicit POSIX termination, and fallback report allocation/starvation. Defensive hardening: canonical decision/target consistency, successor fact allowlisting and current lifecycle identity binding. Already protected and retained with tests: independent Guarded approval, one-use replay/cancellation, atomic-write failure boundaries, unsupported observation refusal, valid partial reuse and receipt/display privacy. This record makes no public exploit or remote vulnerability claim.

State locking serializes Click state, not arbitrary external edits. Filesystem and executable/environment snapshots do not eliminate changes after the final check. Processes that create a new session are outside the tested same-group termination boundary; zombie-only groups may consume the remaining grace period. Cleanup does not guarantee total disk capacity or constant-time latency, and metadata inspection/unlink are not one atomic external-filesystem transaction. Native Windows/macOS behavior and other Python runtimes remain unexecuted locally. Whole user-task time, complete usage and token savings are unmeasured.

The model's reasoning and exploration strategy were not limited by this work. Preserving the same tested outcomes and supported reuse does not establish universal software quality or universal performance gains.
