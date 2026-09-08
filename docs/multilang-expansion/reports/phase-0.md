# Phase 0 report — corrected baseline and remaining compatibility

Status: **complete**

## Purpose and result

Phase 0 fixed the remaining CPython 3.13 PATH compatibility defect without
reimplementing the v0.93.0 review hardening. It also made the selected pytest
interpreter boundary explicit in a real-process test, corrected the mode guide
to match the implemented Evidence sharding path, and created a capability
baseline that keeps six support dimensions separate.

## Changed call paths

- `hooks/click_inspection.py`: `sanitized_executable_path()` now requires each
  accepted PATH entry to resolve with `strict=True`. No other resolve boundary
  changed.
- `tests/test_click_gate_inspection.py`: covers loops, broken links, missing
  absolute entries, and the existing normal external/workspace filtering case.
- `tests/test_click_auto_sharding.py`: creates a real isolated Python without
  pytest and proves that the chosen command interpreter is also the collector
  interpreter. The controller's environment cannot supply pytest implicitly.
- `.github/workflows/ci.yml`: CPython 3.10, 3.11, 3.13, and 3.14 compatibility
  jobs now run all three strict PATH regressions explicitly.
- `skills/click/references/modes.md`: describes Evidence collection and explicit
  refresh separately from Guarded approval, consistent with router/runtime
  tests and `automatic-sharding-setup.md`.

The generated Antigravity distribution is rebuilt and validated after source
changes, so the shipped Hook and mode guide match the source tree.

## Environment and inventory

The exact start state, local toolchain, preserved invariants, and inventory are
in `../BASELINE.md`. The official post-change inventory reports 954 unique tests
with no duplicate or missing ids. macOS, Windows, CPython 3.13, and CPython 3.14
are not claimed from this local Linux 3.12 execution; they remain required CI
jobs.

## Verification

See `../logs/phase-0-verification.md` for commands and results. The focused
regressions passed 5/5, hardening passed 46/46, and automatic-sharding setup
passed 9/9. The uploaded Python 3.13.5 pre-fix failure is preserved at
`../logs/phase-0-path-before-python-3.13.5.log`; the local Python 3.12 pre-fix
control passed and is not mislabeled as a reproduction.

After rebuilding the generated tree, distribution, repository policy, and
compatibility-surface validation passed 51/51. `git diff --check` also passed.

## Remaining limits

- This phase adds no language adapter and makes no new support claim.
- pytest is absent from the system Python. The negative selected-interpreter
  path ran locally; the pinned positive integration remains CI evidence.
- Rust, JVM, .NET, and CMake toolchains are absent locally.
- Native macOS and Windows execution remains CI-only.

## Next condition

Phase 1 may start because the compatibility defect is fixed, the core
hardening and automatic-sharding regressions pass, and the capability baseline
exists. Phase 1 must preserve every invariant in `../BASELINE.md`.
