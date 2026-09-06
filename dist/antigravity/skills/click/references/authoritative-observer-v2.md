# Authoritative Observer v2 contract

Authoritative Observer v2 is Click's narrowly supported source of runtime input
authority for dependency-aware cross-revision reuse. It is separate from
Shadow Observer v1 and from static dependency discovery. The public control is:

```text
click-gate observer authoritative
```

The control is accepted only in a separately approved, active Guarded contract.
It prepares candidate runtime state and grants no reuse by itself. Evidence
mode, a dashboard, a report, a proposal, a caller-provided JSON object, and a
Shadow record cannot enable or impersonate this authority.

## Implemented profiles and validation

| Profile | Native backend | Implementation and validation |
| --- | --- | --- |
| `linux-cpython3123-strace68-v1` | exact strace 6.8 and a successful ptrace capability probe | Implemented and validated on a real Linux host |
| `darwin-cpython3123-fsusage-v1` | privileged native `fs_usage` plus a DYLD native audit companion | Implemented; real macOS host validation is pending |
| `windows-cpython3123-etw-v1` | inbox `logman.exe` and `tracerpt.exe` ETW sessions plus a native audit extension | Implemented; real Windows host validation is pending |

Every profile is limited to CPython 3.12.3, one direct
`python -m unittest ...` command per evidence source, and a bound verification
environment with `PYTHONHASHSEED=0` and `PYTHONDONTWRITEBYTECODE=1`. A platform
profile is usable only when its exact runtime, privilege, compiler, headers,
backend identity, and capture capability pass their local probes. A missing or
changed input makes authoritative observation unavailable and preserves normal
verification.

Click uses already installed build inputs to build a small native audit
companion. It installs no package and requests no privilege. The companion
source, compiler, backend executables and versions, bootstrap where applicable,
and resulting artifact are content-identified. The artifact directory is
outside the repository and is revalidated before each use; POSIX directories
must also be owned by the current user with mode `0700`. Unsupported versions
or missing build inputs make authoritative mode unavailable.

## One execution and failure behavior

The one-use Click verification runner launches the original command under the
selected platform collector and native audit companion. The command is
executed exactly once.
If the backend cannot start before the target begins, Click falls back to the
same unobserved command once. If observation becomes incomplete after the
target starts, Click preserves that execution's PASS or FAIL result, records an
ineligible observation, and never reruns the target to obtain better telemetry.

The runner signs the observation envelope with its one-use token. Result
recording verifies that signature and all current bindings before storing a v2
receipt. Arbitrary observation JSON supplied through an internal API is ignored.

## Completeness boundary

A complete observation snapshots every modeled input consumed by the check,
including:

- file contents and metadata;
- directory membership and metadata;
- missing-path lookups;
- lexical symlinks and their resolved targets;
- the executable, interpreter, native companion, platform backend, compiler, and
  relevant Python runtime files;
- source, configuration, data, import-search, standard-library, distribution,
  site-package, loader, locale, and runtime support inputs.

Input records use role-based content, metadata, directory, missing, or symlink
fingerprints. Persisted project paths are repository-relative. Absolute runtime
paths are reduced to role and identity records; raw trace paths are transient.
Reuse re-fingerprints every record, so content or metadata changes, directory
membership changes, a missing path appearing, a symlink change, a new import
candidate, backend drift, or companion drift makes the prior observation
ineligible.

Every implemented profile deliberately refuses to mark an observation complete
when it sees a child process, concurrent thread execution, network or IPC
activity, project time or random input, inherited descriptor input, unsupported native or dynamic-runtime
access, observer introspection or tampering, an unresolved event, capture loss,
or an incomplete process stream. These conditions affect reuse eligibility
only; they never change the test result.

## Binding and successor requalification

`runtime-dependency-observation-v2` binds the original execution to:

- evidence source and shard;
- normalized check digest and exact argv;
- working directory and Git workspace root;
- protected workspace tree and mutation revision;
- Guarded contract digest;
- verification environment and executable identity;
- host Hook coverage;
- dependency policy or declaration digest;
- observer profile, backend, companion, and complete input snapshot.

A completed Guarded contract contributes only a candidate to a successor.
The successor needs its own contract id and later-turn approval, must explicitly
prepare a current authoritative runtime, and must request the same check. Click
then revalidates the current policy, environment, executable, host coverage,
shard identity, runtime identities, and every recorded input immediately before
reuse. Only eligible sources are skipped; all uncertain, changed, new, or
incomplete sources execute normally.

## Shadow separation

Shadow Observer v1 stays `"authoritative": false` and
`"reuse_authorized": false`. Its records are content-free lifecycle telemetry.
Changing a Shadow flag, copying a Shadow `complete` status, replaying a report,
or promoting static dependency analysis cannot produce an Authoritative
Observer v2 envelope. The paths may share low-level parsing code, but authority
comes only from the separate verified execution and runner-token signature
described above.
