# Phase 5 verification transcript

Runtime: Linux 7.0.0-31-generic x86_64, CPython 3.12.3, Node 22.23.2,
npm 10.9.8, and Vitest 5.0.0. Native Windows and macOS jobs were not executed
in this checkout.

```text
# Clean temporary provisioning fixture
npm ci --ignore-scripts --no-audit --no-fund
npx --no-install vitest run
Test Files 3 passed; Tests 3 passed

python3 -B -m unittest \
  tests.test_click_auto_sharding.CommandParsingTests.\
test_vitest_parser_rejects_network_and_interactive_or_ambiguous_shapes \
  tests.test_click_auto_sharding.CommandParsingTests.\
test_vitest_collection_output_and_timeout_fail_closed \
  tests.test_click_collector_runtime.ClickCollectorRuntimeTests.\
test_supervisor_can_return_bounded_stdout_and_stderr \
  tests.test_click_runtime_identity.RuntimeIdentityTests.\
test_no_install_package_runner_is_bound_to_its_local_manifest_and_entry \
  tests.test_click_runtime_identity.RuntimeIdentityTests.\
test_package_runner_target_cannot_escape_node_modules \
  tests.test_click_evidence_shards.ClickEvidenceShardsTests.\
test_vitest_new_file_and_ambiguous_command_fall_back_to_parent \
  tests.test_click_auto_sharding.RealCollectionTests.\
test_vitest_collection_is_repeatable_and_exact_file_selectors_are_safe \
  tests.test_click_shard_proposal.ProposalTests.\
test_vitest_same_basenames_generate_exact_file_children \
  tests.test_click_sharding_setup.VitestShardingSetupTests \
  tests.test_click_multilang_execution.MultilangExecutionHookTests.\
test_vitest_no_install_runner_executes_and_reuses_exact_identity -v
Ran 11 tests in 69.236s — OK

python3 -B -m unittest \
  tests.test_click_auto_sharding tests.test_click_shard_proposal \
  tests.test_click_sharding_setup tests.test_click_evidence_shards \
  tests.test_click_verification_adapters tests.test_click_runtime_identity \
  tests.test_click_collector_runtime tests.test_click_multilang_execution -q
Ran 94 tests in 141.300s — OK (skipped=2)

python3 scripts/build_antigravity_distribution.py
python3 scripts/validate_distribution.py
Distribution validation — OK

python3 -m unittest \
  tests.test_distribution_validation tests.test_repository_shard_inventory -q
Ran 10 tests — OK

python3 -B scripts/run_ci_tests.py --list
Inventory: 1020 tests; partitions: [1020]; no missing or duplicate ids.

python3 -m compileall -q hooks tests scripts
git diff --check
Both exited zero.
```

The full 1,020-test suite is reserved for Phase 9. The CI job
`vitest-sharding-integration` provisions the same lockfile and is assigned to
Linux, macOS, and Windows.
