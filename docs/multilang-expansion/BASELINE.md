# Multilanguage expansion baseline

Phase 0 starts from Click `0.93.0` at
`9b05f790baaf69220df4c0fe6fa0716d5f458951`. The checkout was a clean `main`
before `feature/multilang-expansion` was created. The uploaded prompt package is
an implementation specification, not runtime authority. Its own digest and the
digest of the source archive it reviewed are recorded separately in
`SOURCE_PROVENANCE.json`.

## Local environment

| Component | Observed value |
| --- | --- |
| OS | Linux 7.0.0-31-generic x86_64, glibc 2.39 |
| Python | CPython 3.12.3 |
| pytest in selected system Python | unavailable |
| Node / npm | 22.23.2 / 10.9.8 |
| Go | 1.26.7 linux/amd64 |
| Git | 2.43.0 |
| ripgrep / strace | 15.2.0 / 6.8 |
| Rust, Java, .NET, CMake | unavailable on this host |

Native macOS and Windows behavior was not executed in this phase. CI still owns
those release checks. Python 3.13 and 3.14 behavior is assigned to the existing
runtime compatibility matrix, now with the PATH regressions explicitly listed.

## Preserved boundaries

The following v0.93.0 behavior is an invariant for every later phase:

- Evidence, Guarded, and Off remain distinct. Evidence uses host authority;
  Guarded approval remains independent for each contract.
- Strict non-negative integer revisions, malformed-ledger rejection, successor
  fact allowlisting, decision binding, and the final workspace, environment,
  executable, and host-coverage recheck remain fail closed.
- Automatic sharding `init`, `status`, and `refresh`, parent fallback, generated
  policy ownership, baseline separation, and shard reuse remain available.
- Exact, owner safe-change, and authoritative reuse keep their existing
  authority requirements. Candidate collection never becomes PASS or authority.
- A missing reuse basis causes the permitted check to run. Missing execution
  authority remains a rejection or explicit recovery path.

Phase 0 reran 46 strict-revision, successor, decision, and late-binding tests and
9 automatic-sharding setup tests. All passed.

## PATH compatibility finding

`sanitized_executable_path()` previously used non-strict `Path.resolve()`. The
uploaded review reproduced a symlink loop escaping that filter under CPython
3.13.5. The local CPython 3.12.3 control passed before the change, so that result
is not presented as a local reproduction of the 3.13 defect.

The implementation now uses `resolve(strict=True)` only at this PATH entry
boundary. Existing external directories remain usable; relative paths,
repository paths and aliases into the repository remain excluded; missing
paths, broken links, and loops are excluded. Other `resolve()` calls were not
changed.

## Inventory

The unmodified archive reported 951 tests. Running the official
`python3 -B scripts/run_ci_tests.py --list` after the three new Phase 0 tests
reports 954 unique tests in one complete partition, without missing or duplicate
ids. The count is descriptive and is regenerated rather than enforced as a
constant.

## Support dimensions

Support is tracked independently in `CAPABILITY_BASELINE.json` across command
recognition, real execution, exact reuse, automatic discovery, automatic
sharding, and cross-revision authority. A recognized command is not described
as an executed, discovered, sharded, or reusable integration.
