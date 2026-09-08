# Phase 2 verification transcript

Runtime: Linux, CPython 3.12.3. Native Windows and macOS were not executed.

```text
python3 -m unittest \
  tests.test_click_verification_inputs \
  tests.test_click_verification_freshness
Ran 15 tests in 19.220s — OK
```

The 11 Hook-to-runner tests cover default reuse, a second one-shot rerun,
persistent always-run, required outputs, failed forced rerun, tracked and
ignored input changes, missing/created files, glob membership, profile
expansion, successor reuse, private receipt export, mixed-policy rejection,
Guarded authority, and final input drift. Four leaf tests cover content and
membership hashing, deletion/rename, symlink and count boundaries, protected
paths, and content-free failure bindings.

```text
python3 -m unittest \
  tests.test_click_change_policy tests.test_click_dependency_cache \
  tests.test_click_diagnostics tests.test_click_evidence_shards \
  tests.test_click_efficiency tests.test_click_failure_collection \
  tests.test_click_gate_verification tests.test_click_incremental \
  tests.test_click_receipt tests.test_click_receipt_runtime \
  tests.test_click_verification tests.test_click_verification_bindings \
  tests.test_click_verification_freshness \
  tests.test_click_verification_inputs -q
Ran 305 tests in 279.915s — OK

python3 -m unittest \
  tests.test_click_auto_sharding tests.test_click_shard_proposal \
  tests.test_click_sharding_setup tests.test_click_evidence_shards
Ran 59 tests in 104.170s — OK (skipped=2)

python3 -m unittest \
  tests.test_distribution_validation tests.test_repository_shard_inventory
Ran 10 tests in 1.803s — OK

python3 -B scripts/run_ci_tests.py --list
Inventory: 975 tests; partitions: [975]; no missing or duplicate ids.
```

The first automatic-sharding run exposed one empty-input collapse regression.
The collapse path used the dependency-pattern validator, where an empty list is
invalid, instead of the explicit-input validator, where it is the required
default. The targeted test passed after the correction and the complete
59-test automatic-sharding group was rerun successfully.

The Antigravity distribution was regenerated. `git diff --check` and the
distribution/repository inventory tests passed.
