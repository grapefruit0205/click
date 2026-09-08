# Click v0.94.0 multilingual expansion — final report

## Result

Phases 0 through 9 were implemented on `feature/multilang-expansion` from the
v0.93.0 source revision `9b05f790baaf69220df4c0fe6fa0716d5f458951`
and prepared for the v0.94.0 release.

The common evidence and authority engine now supports tool-specific command and
runtime identity, explicit input membership, exact and owner-policy reuse, and
conservative parent fallback across the implemented profiles. Automatic
inventory and splitting remain narrower than general execution support.

## Verified support

| Tool or format | Real local execution | Exact reuse | Automatic inventory/split |
| --- | --- | --- | --- |
| CPython unittest | Yes | Yes | Yes, bounded profile |
| pytest | Collector/profile implemented; unavailable in final local runtime | Not established locally | Assigned to pinned CI |
| Vitest 5 | Yes, pinned fixture | Yes | Yes, limited static profile |
| Jest 30 | Yes, pinned fixture | Yes | Yes, limited static profile |
| Node test/check and npm test | Yes | Yes | No; parent execution |
| Go test | Yes, local/offline boundary | Yes | No; parent execution |
| JSON/YAML/Markdown/SVG project validators and jq | Yes | Yes | No; parent execution |
| Cargo, Gradle/Maven, .NET, TypeScript, CMake/CTest | Command/runtime profiles only in this checkout | Not established locally | No |
| Direct SQL/XML and other content linters | Recognized where documented; native execution incomplete | Not established locally | No |

Dynamic Vitest/Jest configuration, workspace/multi-project modes, watch/update,
network-dependent setup, ambiguous selectors, and unsupported discovery retain
the original parent command. Adapter candidates never create PASS evidence.

## Mixed-project correctness

The deterministic mixed fixture contains Vitest, Python, Go, shared JSON,
Markdown, and assets. Without a committed owner policy, a frontend change runs
all checks. With the policy, only unaffected checks reuse prior PASS evidence.
Shared schema and Node lock changes invalidate every affected check; new tests,
dynamic imports, asset additions/deletions, failures, and repairs preserve the
same final outcome as the full verification.

## User status and languages

The first dashboard screen and sharding status separate command execution,
automatic inventory/split, same-state exact reuse, owner-committed policy
reuse, and authoritative-observation reuse. Observer can stay off for exact and
committed-policy routes. The dashboard and reports support Korean, English, and
Simplified Chinese with matching translation keys. Projection v9 retains v4-v8
read compatibility.

## Regression evidence

- Official inventory: 1,030 unique tests, no missing or duplicate IDs.
- Final result: 1,023 passed, 7 expected environment/platform skips, 0 failed.
- Distribution rebuilt and validated; Python compilation and whitespace checks
  passed.
- Linux/macOS/Windows, Python 3.10–3.14, pinned JS runners, mixed-project, native
  observer, native toolchain, and content profiles are defined in CI.

The cross-platform and newly defined native CI jobs have not run for this dirty
working tree. Local skips include pytest plus macOS/Windows-native checks.

## Existing speed evidence

No new samples were collected in Phase 9. Earlier Linux component measurements
showed:

- warm resident Hook median: 104.192ms one-shot to 60.921ms resident, 41.5%
  lower; first resident event 253.484ms;
- warm new-process import median: 130.548ms to 95.448ms, 26.9% lower;
- executable-record construction for 1/8/64 checks: 4.138/30.702/244.158ms to
  5.553/5.469/7.442ms.

The single-check binding path is slightly slower. These numbers describe local
components and do not establish faster completed development tasks or token
savings.

## Release decision

`release_ready` is **false**. The implementation is ready for review, but the
new native and cross-platform CI matrix must pass on a committed revision before
a release claim. Cargo/JVM/.NET/TypeScript/CMake profiles must remain described
as recognized or CI-assigned until those jobs provide actual results.
