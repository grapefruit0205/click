# Verification adapter contract v1

`hooks/click_verification_adapters.py` is Click's static adapter registry and
command-routing boundary. It is candidate metadata, never approval, PASS,
receipt, or reuse authority.

Every adapter has a stable ID and version, one or more command profiles, and
independent `execute`, `inventory`, `split`, `dependency_candidates`, and
`runtime_observation` capabilities. Each capability records an implementation
state separately from environment test evidence. A command can therefore be
recognized for execution while collection and automatic splitting remain
unsupported.

The common model records the original argv and cwd, execution identity,
project/file/test inventory units, selector, policy and context digests,
unknown reasons, and the capability report. It has no mandatory Python module
field. Original argv values keep path spelling and case; normalized command
tokens are used only for static routing and minimum verification class.

The `unittest` and `pytest` collectors remain fully implemented inventory and
split adapters. Their existing facade is `click_test_inventory.py`; their
Python AST dependency candidate analysis is loaded only after the selected
adapter declares that capability. Phase 5 adds a profile-limited Vitest 5
inventory and exact-file split adapter. Jest, Node, package-script, Go, Cargo,
JVM, .NET, and native-build entries remain execute-only and do not imply
automatic discovery or sharding.

The registry is closed and source-controlled. Click does not import arbitrary
project adapters. Owner-committed evidence shard v1 manifests remain valid for
unknown runners; the registry only constrains Click-generated inventory and
split proposals.
