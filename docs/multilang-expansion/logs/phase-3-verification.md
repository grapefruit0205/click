# Phase 3 verification transcript

Runtime: Linux 7.0.0-31-generic x86_64, CPython 3.12.3, Node 22.23.2,
npm/npx 10.9.8, and Go 1.26.7. Cargo, Rust, Java, Gradle, Maven, .NET,
CMake, CTest, and TypeScript were unavailable locally. Native Windows and
macOS jobs are configured but were not executed in this checkout.

```text
python3 -m unittest \
  tests.test_click_runtime_identity \
  tests.test_click_multilang_execution \
  tests.test_click_verification_bindings -v
Ran 18 tests in 20.040s — OK
```

The six real Hook fixtures exercised Node `--test`, Node `--check`, npm test,
npm lifecycle non-reuse, Go test in local/offline mode, and an npx download
denial. Node and Go covered fresh execution, unchanged reuse, real failure, and
successful correction. Seven runtime identity tests covered config mutation,
package lock/install disagreement, lifecycle incompleteness, and Go, Cargo,
JVM, .NET, wrapper, and package-runner download boundaries.

```text
python3 -m unittest <verification authority modules including Phase 3> -q
Ran 311 tests in 300.339s — OK

python3 -m unittest <capability-runtime shard modules> -q
Ran 61 tests in 5.011s — OK

python3 -m unittest \
  tests.test_click_auto_sharding tests.test_click_shard_proposal \
  tests.test_click_sharding_setup tests.test_click_evidence_shards -q
Ran 59 tests in 101.691s — OK (skipped=2)

python3 -m unittest \
  tests.test_distribution_validation tests.test_repository_shard_inventory -q
Ran 10 tests in 2.006s — OK

python3 -B scripts/run_ci_tests.py --list
Inventory: 988 tests; partitions: [988]; no missing or duplicate ids.
```

The 311-test authority run preceded the final offline-guard tightening. The
18-test focused run and 61-test runtime shard were executed after that change.
The Antigravity distribution was then regenerated. Repository inventory and
distribution validation passed against the generated copy.
