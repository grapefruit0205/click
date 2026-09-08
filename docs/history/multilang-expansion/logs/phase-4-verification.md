# Phase 4 verification transcript

Runtime: Linux 7.0.0-31-generic x86_64, CPython 3.12.3, Node 22.23.2,
npm 10.9.8, and jq 1.7. Native Windows and macOS jobs were not executed in
this checkout.

```text
python3 -m unittest \
  tests.test_click_verification_adapters \
  tests.test_click_runtime_identity \
  tests.test_click_content_validation -v
Ran 20 tests in 25.450s — OK

python3 -m unittest \
  tests.test_click_capability tests.test_click_gate_verification \
  tests.test_click_verification tests.test_click_verification_adapters \
  tests.test_click_runtime_identity tests.test_click_content_validation -q
Ran 159 tests in 262.831s — OK

python3 -m unittest \
  tests.test_click_content_validation.ContentValidationHookTests.\
test_oversized_explicit_input_runs_but_never_becomes_reusable -v
Ran 1 test in 2.145s — OK

python3 -m unittest \
  tests.test_click_auto_sharding tests.test_click_shard_proposal \
  tests.test_click_sharding_setup tests.test_click_evidence_shards \
  tests.test_click_verification_adapters -q
Ran 66 tests in 107.324s — OK (skipped=2)

python3 -m unittest \
  tests.test_distribution_validation tests.test_repository_shard_inventory -q
Ran 10 tests in 1.813s — OK

python3 -B scripts/run_ci_tests.py --list
Inventory: 996 tests; partitions: [996]; no missing or duplicate ids.
```

The first truncated inventory display caused a presentation-only
`BrokenPipeError` after `head` closed stdout. It was not used as evidence. The
full command was rerun to a temporary output file, exited zero, and produced
the 996-test result above. The Antigravity distribution was regenerated after
the final runtime guard and test changes. `compileall` and `git diff --check`
also passed.
