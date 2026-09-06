# Setting-free automatic sharding end-to-end evidence

Phase 5 validates the shipped automatic-sharding path in two independent Git
projects that contain ordinary Python source and tests but no `.click`
directory. The tests select Guarded mode through the public control, use
`click-gate sharding init|status|refresh`, and never write shard or dependency
JSON themselves.

## Supported profile used

The positive runs use Linux, CPython 3.12.3, direct `python -m unittest`
discovery, and exact strace 6.8 with the identity-bound native companion.
Authoritative observation is explicitly selected inside each approved Guarded
contract. The build uses only the compiler and headers already present; it
does not install a package or elevate privileges.

Other Python versions, non-Linux authoritative backends, pytest, custom or
dynamic loaders were not admitted by this E2E profile. The separately
implemented macOS and Windows profiles still require their own real-host
validation. Unavailable observer permissions, changed runtime identity, and
incomplete observation do not gain reuse authority; Click runs the approved
validation or reports a non-ready state according to the existing fallback
boundary.

## Reproduced workflow

Each project follows the same public sequence:

1. confirm `selection-required` and absence of `.click`;
2. reject unapproved initialization;
3. approve bounded collection and generate a review-only proposal;
4. approve a different digest-bound application contract declaring
   `E_AUTO_SHARDING_BASELINE`;
5. apply only absent policy files and commit those exact files in the temporary
   fixture repository;
6. run parent and generated-child bootstrap, then authoritative baseline
   validation under contract A;
7. confirm every child has a complete v2 observation and the setup is
   `reuse-ready`;
8. stage contract B with a new contract ID, verify that approval, runner token,
   evidence completion, and unfinished work were not inherited, then approve
   B in a later turn;
9. make one real semantics-preserving library-module change through the Hook;
10. request the original full parent command through `sharding refresh`;
11. execute the related child and reuse only eligible unaffected children;
12. check origin contract, origin batch, revision, candidate digest, and the
    original timing sample on every reused child;
13. run the original parent command directly against the same final code as a
    full-validation audit;
14. run the shipped `dist/antigravity` setup controller and verify byte parity
    with the source controller.

The first project uses `app/` plus two `tests/test_*.py` modules and two test
cases. The second uses `science/` plus nested `checks/unit/case_*.py` modules,
three generated shards, four test cases, and a different shared module. Fixture
names appear only in tests; product code derives all paths and identifiers.

## Representative measurement

This table records one focused run on 2026-09-07. No artificial sleep or added
workload was used. Millisecond values naturally vary between runs.

| Metric | library layout | nested science layout |
| --- | ---: | ---: |
| Tests / generated shards | 2 / 2 | 4 / 3 |
| Actually executed shards | 1 | 1 |
| Actually reused shards | 1 | 2 |
| Test-count reuse rate | 50.00% | 50.00% |
| Shard reuse rate | 50.00% | 66.67% |
| Reused timing coverage | 1 / 1 | 2 / 2 |
| Avoided test execution estimate | 447.93 ms | 975.04 ms |
| Executed child command time | 520.34 ms | 576.42 ms |
| Same-shard time reduction estimate | 46.26% | 62.85% |
| Measured Click processing segments | 1,604.96 ms | 1,675.88 ms |
| Click request wall (`hook-entry-to-result-recording`) | 1,727.07 ms | 1,788.66 ms |
| External fixture call wall | 1,815.70 ms | 1,883.24 ms |
| Same-final-code direct parent wall | 57.40 ms | 56.53 ms |
| Direct parent minus Click request wall | **−1,669.67 ms** | **−1,732.13 ms** |

The avoided and reduction values use compatible prior authoritative child
samples and the current actual expanded batch. They answer how much eligible
test execution was not repeated. The direct-parent comparison has a different
measurement boundary and includes the cost of using Click in these very short
fixtures; it is kept as a signed negative result rather than presented as a
saving.

Initial setup is also separate. The library run recorded 2,330.23 ms total,
including a 51.45 ms parent, 99.79 ms of sequential children, and 1,159.23 ms
of measured setup processing. The science run recorded 3,089.72 ms total,
including a 47.85 ms parent, 159.98 ms of children, and 1,505.30 ms of setup
processing. Their parent-minus-children bootstrap comparisons were −48.34 ms
and −112.13 ms and are labeled setup comparisons, never savings. Separate
observer-only setup cost and live Click management overhead remain unmeasured;
the dashboard and exports preserve `null` for both.

Python calculates the canonical savings object once. The dashboard current
summary and per-batch summary must equal that object exactly, and the host
summary emitted for the request is derived from the same state. The Node UI
contract test feeds those exact values through JSON export and standalone HTML
and verifies labels, escaping, negative values, and measurement scopes.

## Safety regression map

The final suite includes these fail-closed cases:

| Boundary | Representative regression |
| --- | --- |
| Related module / shared config / lockfile | `test_separate_contract_reuses_only_the_unaffected_shard` |
| Runtime, executable, environment, runner binding | `test_new_import_candidate_and_binding_change_invalidate_reuse`, `test_verification_runner_rejects_executable_change_before_execution`, `test_prepared_environment_value_change_is_rebound_before_execution` |
| New, deleted, or renamed test/file | `test_added_deleted_tests_require_new_inventory_and_exact_coverage`, `test_input_records_cover_content_membership_missing_and_symlink` |
| Missing-path creation and symlink replacement | `test_input_records_cover_content_membership_missing_and_symlink` |
| Dynamic, time, child-process, native, or external input | `test_time_child_and_event_loss_are_non_reusable_without_a_retry`, `test_external_symlink_is_explicitly_unsupported`, `test_native_memory_access_is_incomplete_without_changing_test_result` |
| Failed or cancelled validation | `test_unsupported_command_and_bootstrap_failure_stay_non_ready`, `test_keyboard_interrupt_records_failure_and_releases_runner`, `test_late_completion_from_cancelled_batch_cannot_overwrite_successor_batch` |
| Collection/import error | `test_import_error_is_not_success_and_does_not_leak_exception` |
| Observation loss or missing permission/backend | `test_time_child_and_event_loss_are_non_reusable_without_a_retry`, `test_denied_backend_probe_leaves_the_real_command_unchanged`, `test_unavailable_backend_executes_the_original_command_once` |
| Uncommitted, changed, or racing configuration | `test_concurrent_initialization_and_existing_target_fail_closed`, `test_edited_shard_map_runs_original_parent_suite`, `test_running_batch_blocks_parallel_mutation_and_verification` |
| Forged receipt, binding, or runner token | `test_shadow_record_and_forged_envelope_are_not_v2_authority`, `test_verification_runner_rejects_tampered_environment_binding`, `test_tampered_verification_token_does_not_release_reservation` |

The positive result is actual partial reuse with complete provenance. The
negative result is the measured net comparison for short fixtures. macOS,
Windows, other Python versions, other test frameworks, parallel runners, and a
positive whole-request speedup are not validated by this profile and are not
claimed.
