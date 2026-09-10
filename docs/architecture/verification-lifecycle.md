# Verification lifecycle boundaries

`hooks/click_verification.py` preserves the existing public API and private
compatibility entry points. It does not implement admission or execute commands.
The implementation has the following one-way dependencies:

| Domain | Responsibility | Lifecycle dependencies |
| --- | --- | --- |
| `click_verification_common.py` | State primitives, current binding helpers and compatibility aliases to lower domains | None |
| `click_verification_prepare.py` | Validate requests, expand complete shard plans, recheck reuse, reserve the selected commands | Common |
| `click_verification_claims.py` | Consume a one-use claim, release unclaimed reservations, recheck cancellation and inputs before execution | Common |
| `click_verification_runner.py` | Execute admitted commands once, collect output and observations, handle termination | Common, claims, results |
| `click_verification_results.py` | Validate the claimed result and persist evidence, diagnostics and timing | Common |

The runner hands a frozen `VerificationRunResult` to `record_outcome`. Large
observation maps are retained by reference during this local handoff; persistence
still validates their schema, bindings and attestation. Neither this type nor a
successful process exit grants reuse authority by itself.

Each module imports explicit bindings. There is no shared mutable service object,
import-time injection into another module's globals, or dependency on the gate or
host router. Caller-provided Git and execution functions retain their existing
signatures. Tests inject late input changes at the preparation module that owns
the matching operation, then check that the real runner executes the affected
checks. Claim replay, cancellation and result validation remain separate tests.

Automatic sharding `init`/`status`/`refresh`, child identity, reuse rules and parent
fallback are unchanged by this extraction. A child with insufficient evidence
runs. Parent execution remains the fallback when partition completeness cannot
be established. The distribution manifest explicitly includes all lifecycle
modules and its validator checks that generated copies match the source.

The dependency test checks the lifecycle DAG and forbids gate, host-router and
service dependencies throughout the verification domains. The preparation and
runner algorithms remain substantial; further extraction should follow a
specific behavior or test boundary rather than introducing generic wrappers.
