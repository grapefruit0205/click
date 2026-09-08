# Phase 0 verification log

All commands ran from the repository root on Linux with CPython 3.12.3.

## Inventory

```text
python3 -B scripts/run_ci_tests.py --list
Inventory: 954 tests; partitions: [954]; no missing or duplicate ids.
```

The uploaded unmodified-source inventory contained 951 tests. Phase 0 adds
three tests. The inventory is discovered rather than compared with a fixed
expected count.

## PATH and selected-interpreter regressions

```text
python3 -m unittest \
  tests.test_click_gate_inspection.ClickGateInspectionTests.test_sanitized_path_fails_closed_on_a_symlink_loop \
  tests.test_click_gate_inspection.ClickGateInspectionTests.test_sanitized_path_fails_closed_on_a_broken_symlink \
  tests.test_click_gate_inspection.ClickGateInspectionTests.test_sanitized_path_drops_a_missing_absolute_entry \
  tests.test_click_gate_inspection.ClickGateInspectionTests.test_sanitized_path_drops_relative_and_workspace_entries \
  -v
Ran 4 tests in 0.004s — OK

python3 -m unittest \
  tests.test_click_auto_sharding.RealCollectionTests.test_pytest_availability_uses_the_selected_interpreter \
  -v
Ran 1 test in 0.454s — OK
```

## Hardening and automatic-sharding regression

```text
python3 -m unittest \
  tests.test_click_gate_verification.ReviewHardeningGateTests \
  tests.test_click_verification \
  tests.test_click_evidence \
  -v
Ran 46 tests in 49.141s — OK

python3 -m unittest \
  tests.test_click_sharding_setup.ShardingControlParsingTests \
  tests.test_click_sharding_setup.ShardingSetupStateMachineTests \
  tests.test_click_sharding_setup.ShardingGateIntegrationTests.test_evidence_mode_runs_setup_through_baseline_without_guarded_contract \
  -v
Ran 9 tests in 31.648s — OK
```

These checks were submitted through Click protocol-v2 verification. Both
groups actually ran; none was counted as reused evidence.

## Distribution, policy, and diff validation

```text
python3 -m unittest \
  tests.test_distribution_validation \
  tests.test_repository_policy \
  tests.test_click_compatibility_surface \
  -v
Ran 51 tests in 2.457s — OK

git diff --check
exit 0
```

The generated Antigravity tree was rebuilt immediately before these checks.
