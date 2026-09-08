# Click runtime optimization — 2026-09-08

Click v0.92.0 follows v0.91.0 with the runtime and CI changes described here. The work reduces repeated runtime setup and makes the implementation easier to change. It does not use skipped tests as a whole-task efficiency claim.

## Runtime changes

- Verification request parsing/classification, environment/executable/workspace bindings, and receipt reuse policy now have separate owners: `click_verification_plan.py`, `click_verification_bindings.py`, and `click_verification_reuse.py`. The existing facade and receipt validation remain compatible.
- `VerificationRunResult` is the runner's typed handoff to the existing result validation. Large diagnostic/observation mappings are not recursively copied.
- Executable hashes are shared only within one collection stage on POSIX. Preparation, issuance, claim and subsequent source boundaries collect fresh hashes. Windows keeps per-record content hashing because its creation timestamp cannot prove an in-place file remained unchanged.
- Shadow root lookup no longer computes an entire Git snapshot just to retrieve the root. Workspace snapshots used for admission, reuse and execution drift remain intact.
- Normal Hook startup does not import the dashboard HTTP server, projection, sharding setup, receipt runtime, or native OS collectors. Each is loaded when its functionality is needed.

## Dashboard

`click_shadow_dashboard.py` owns lifecycle/control. `click_dashboard_server.py` owns HTTP serving. HTML, CSS, JavaScript and ko/en/zh-CN locales live under `hooks/dashboard/` and are loaded when the viewer requests them. The existing Python asset API is retained for callers.

Only the two transport-generated snapshot timestamps are excluded from change detection. Actual task data and measurement timestamps still trigger updates. Idle/start/stop checks read the sidecar rather than full contract history. Projection generation, validation and socket writes run outside the global state lock after acquiring an independent JSON snapshot.

The current authentication, loopback/Host validation, CSP, language selection and report exports are preserved. The loopback server binds its numeric address without a reverse DNS lookup; server-start regression tests report early exits and always stop their server thread. The public engine file digest includes the moved assets and their relative paths. Projection responses are not cached by mutation revision: completion, history, clock and installed-file inputs can change independently of that revision.

## Evidence storage failure

If ordinary read preparation cannot write Evidence storage, a positively identified, intact Evidence session can use the validated stateless read runner. This path reports that no receipt was recorded or reused. Explicit inspect capabilities, Guarded/review/unknown states and active mutation/verification/observation conflicts retain their existing restrictions.

If the runner's sandbox alone cannot persist its one-use claim, it does not execute. If execution finished but its result cannot be recorded, output is preserved with an unsuccessful receipt status. A claimed wrapper's death is not proof that its independent read child exited; claimed reservations are not automatically released on PID death. A real orphan-child regression verifies this boundary and cleans up the child.

## CI and distribution

`scripts/run_ci_tests.py` discovers the full inventory, rejects discovery errors/duplicate ids, partitions complete TestCase classes, and verifies that the union covers every discovered test exactly once. Measured class durations affect ordering only. Each partition has its own temporary root, inherited by children, so native observer build/cleanup cannot interfere across local workers.

CI runs four partitions on each existing OS, explicitly installs Node for dashboard tests, retains native/Windows checks and existing required-check names, and uploads duration artifacts even when a partition fails. Older PR-commit CI runs are cancelled. The scheduling seed is a local Linux estimate; hosted Windows/macOS durations should inform future tuning.

```sh
python3 -B -m scripts.run_ci_tests --parts 4 --list
python3 -B -m scripts.run_ci_tests --parts 4 --part 1 --timings-out /tmp/click-part-1.json
```

Run parts 1–4 for complete coverage. Without `--parts`, the same command runs the whole suite. Test durations are stored by test id; `scripts/ci_test_timings.json` also accepts class means. New tests are always discovered and use a default weight until measured.

Distribution generation now writes changed bytes and removes stale generated files. Runtime modules and every dashboard asset are covered by explicit manifests and source/distribution parity checks. `python3 scripts/build_antigravity_distribution.py --clean` remains available for a complete release regeneration.

## Validation and measurement

The complete local partitioned run before the release follow-ups discovered **886 unique tests: 879 passed and 7 platform skips**, with every partition exiting successfully. Partition times were 116.683s, 114.705s, 87.648s and 86.720s on this Linux host. This is not a before/after CI speed claim. Distribution parity, compilation and whitespace checks passed. The new Windows same-size executable-change test preserves fresh content checking, and the orphan-child test was confirmed failing against the unsafe PID-release implementation before that implementation was removed.

Matched seven-sample local trials alternated baseline commit `b06b8ec879b0a17d9acfb5754da757e76482755b` and the modified code. With warmed bytecode, new-process Hook import median changed **130.548 → 95.448ms (26.9% lower)**. With bytecode reads disabled via an empty cache prefix, it changed **575.495 → 415.628ms**.

Repeated executable-record construction medians for 1 / 8 / 64 checks changed **4.138 / 30.702 / 244.158ms → 5.553 / 5.469 / 7.442ms**. The single-check case pays a small stat-check cost; larger groups benefit from shared hashing. These are Linux component costs, not whole-task or token savings. Windows deliberately retains per-record hashing. Raw samples and final test counts are in [the measurement record](agent-efficiency/evidence/runtime-optimization-2026-09-08.json).

The required acceptance criterion remains automatic sharding `init/status/refresh` and shard reuse without regression. The repository shard inventory adds only the new binding test module to its existing owner. The reuse policy is unchanged; edited/uncommitted shard policy still falls back conservatively.

The measurement host is Linux. Portable Windows parsing/transport and metadata regressions run locally. Release validation on Windows and macOS, including native backends, is reported by the OS-specific CI checks on [PR #95](https://github.com/grapefruit0205/click/pull/95).

## v0.93.0 review follow-up

The subsequent [review-hardening work](review-hardening/reports/phase-0.md) is separate from the v0.92.0 measurements above. Successful evidence now uses strict nonnegative integer revisions, and safe-change decisions bind their original receipt, exact check and current source context. Successor sources start from normal defaults and copy only named verification facts and provenance; the original execution timestamp is preserved.

All reuse modes confirm workspace and executable/environment bindings again before storing the final plan. Observed drift returns affected checks to real execution. Claim, result and collection boundaries also compare the current task and approval identity with the original one-use invocation. Explicit POSIX timeout/termination handles a surviving process group even if its leader has exited. These changes retain normal authorized reuse and do not constrain model reasoning or establish test sufficiency.

Phase reports distinguish reproduced behavior from defensive consistency checks and retain failed intermediate checks. This follow-up is packaged as v0.93.0; its local regression, partial performance comparison and platform limits are recorded in [Phase 6](review-hardening/reports/phase-6.md), with workflow state in [progress](review-hardening/progress.json). Native Windows/macOS results from v0.92.0 do not validate the v0.93.0 source; the v0.93.0 PR's own CI is the applicable cross-platform record.
