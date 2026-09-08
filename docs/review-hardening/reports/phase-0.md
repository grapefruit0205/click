# Phase 0 — baseline and finding classification

Status: passed. No product implementation or tests changed in this phase.

## Starting and ending state

Start HEAD `d48d327c7a6836dbe11b3d331b49420b4bb1058c`, branch `main`, clean. End HEAD unchanged, branch `codex/review-hardening-0.92.0`, dirty only through new `docs/review-hardening/`. Source/test/benchmark/script/CI content fingerprint before and after focused checks: `564e305fd562c5ee91c14af16495eaed13da0cad12a363473f36129fae725a50`.

Frozen tracked-source archive: `/tmp/click-review-hardening-20260908/baseline`; archive digest and tool environment in `../evidence/baseline.json`. Python 3.12.3 on Linux x86_64. System Python has no pytest; an already installed Python 3.12.3 / pytest 9.1.1 environment exists at `/tmp/click-release-pytest-shyO6a/venv/bin/python` and can be used for final coverage without installation. Git, rg, strace, Node, and C compiler are available. Native Windows/macOS execution is unavailable on this host.

## Trace and responsibilities

`click_gate._handle_pre_tool` → `click_verification.prepare/_prepare_verification_impl` → contract/runtime state → `click_evidence.sources_from_state` → Evidence dynamic registration → current revision → exact/dependency/safe-change matcher → promotion → incremental plan save → reuse-only reply or one-use runner. Runner rechecks claims/bindings and records completion only against its current reservation.

The source validator rejects malformed revisions, but Evidence registration currently reintroduces raw sources after a `{}` validation failure. Guarded rejects that ledger. `prepare` coerces current revision in both modes. Pure matcher failures and actual subprocess Hook behavior were examined separately. [Findings](../findings.md) distinguishes reachability from defensive consistency.

The public safe-change call passes the exact same source checks and baseline receipt to `decide`; it does not accept caller-injected decisions. Its digest binds snapshots and policy, but the independent matcher does not recompute it. Successor requalification copies one source, not the whole contract; no approval-token leak was demonstrated. Candidate collection, separate Guarded B approval, dependency/shard bindings, and failed-candidate restoration already exist.

Safe-change already checks a second workspace snapshot after the decision and before promotion. A common final reuse-only snapshot/binding check is a test gap, not yet a proven bypass. Existing replay, running child, cancellation and result-save boundaries must be retained.

`_json_report_command` normally returns an inline command. Only inline transport failure creates a one-shot JSON file in `click_state.state_root()` and prunes `.click-json-report-*.json`. TTL is one hour; successful readers remove their own report. No authority state is a cleanup target.

## Executed baseline checks and probes

- `python3 -B -m unittest tests.test_click_evidence tests.test_click_change_policy tests.test_click_verification_bindings tests.test_click_runner_transport tests.test_click_auto_sharding -q`: exit 0, ran 61, passed 60, skipped 1, failures/errors 0. Wrapper elapsed 10.175737 s. Exact environment and fingerprints in `../evidence/phase-0-focused.json`; raw `.log` in the artifact directory.
- Policy plus five existing Hook regressions: exit 0, ran/passed 18, no failures/errors/skips, unittest 13.174 s. Exact selectors in `../evidence/phase-0-policy-integration.json`. The policy module (13 cases) overlapped once because the parallel audit began before coordination arrived; no further baseline rerun was scheduled. Start fingerprint was not captured for this independent command, and is not claimed.
- `python3 -B /tmp/click-review-hardening-20260908/reuse_hook_revision_probe.py <repository>`: exit 0, two real passing fixture baselines, 38 malformed state probes. Probe-specific denial/reuse/runner/exit-1 outcomes are in `reuse_hook_revision_probe.jsonl`; these probes are not counted as passing regression tests.
- `python3 -B /tmp/click-review-hardening-20260908/reuse_pure_revision_probe.py <repository>` and `phase-0-policy-pure-repro.py`: exit 0, malformed pure-function behavior and source-copy facts retained in JSONL/log. No external decision injection API was introduced.
- Component cleanup baseline: five tempfile distributions, 2 warmups + 11 timing samples and 3 separate tracemalloc samples. Fallback median at 0/16/6000 matching entries: 0.356/0.456/15.805 ms; Python peak 11,978/17,361/4,516,945 B. 6000 matching + 6000 nonmatching: 18.337 ms and 6,324,064 B. Inline median 0.004–0.008 ms, zero enumeration. Deterministic 128-fresh + 8-stale fixture retains all stale entries after six calls. Full raw data and methodology: `/tmp/click-review-hardening-20260908/report-cleanup/baseline-results.json`. This is a component measurement, not whole-task or token savings; concurrent audit activity may add timing noise.

## Subsequent phases and commands

1. Strict revision and container validation: evidence/verification/change-policy focused modules plus both-mode malformed Hook tests.
2. Decision linkage: change-policy/reuse tests, safe-change Hook, successor and per-shard successful reuse.
3. Explicit successor carry: source initializer/fact classification, Evidence A→B and separately approved Guarded A→B, receipt export/verify and shard duration provenance.
4. Deterministic drift and failure boundaries: gate verification, claims/cancellation/result-save fixtures, same-final-state full-suite workflow audit.
5. Report cleanup: runner transport tests and instrumented tempfile before/after distributions; preserve one-hour TTL and one-shot reader.
6. `python3 -B -m scripts.run_ci_tests` (complete discovery), `--parts 4 --list` union/uniqueness, existing generated distribution builder and validator; paired baseline/current component and workflow measurements with actual losses retained. Native unavailable platforms are constraints, never converted to passes.

The fixed archive supports later paired measurements. Total task token usage is unavailable and remains unmeasured. No dependencies were installed and no remote state, release version, or committed user policy changed.
