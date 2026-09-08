# Click Gate compatibility surface

Status: transitional architecture boundary, baselined from v0.36.0.

`hooks/click_gate.py` is the executable facade for Codex and the shared host
runtime used by the bundled Antigravity adapter. Domain behavior belongs in the
`click_*` modules below that facade. The facade still exposes historical names
that predate those extractions; those names are migration debt, not a template
for new code.

## Supported user-facing surface

The supported product surface is the Hook and `click-gate` command protocol
documented in the README and capability protocol. Python names beginning with
an underscore are not a general public extension API.

## Transitional host-adapter bridge

The current host adapters still call a small, explicit set of gate symbols:

- `hooks/click_hook.py`: `main`
- `hooks/antigravity_gate.py`: public `host_router`

`hooks/click_windows.py` and the Antigravity launcher-path check use the formal
`click_runner_transport` boundary. They no longer reach through private gate
symbols to configure or decode runner commands.

The named `click_host_router` and `click_runner_transport` interfaces now own
adapter routing and runner transport. The remaining `click_gate.main` and
`click_gate.host_router` entries are the explicit host-facing facade rather
than private compatibility reach-through.

## Shadow Observer backend boundary

`hooks/click_dependency_trace.py` remains the verification-facing compatibility
facade for Shadow Observer v1. It selects a backend through
`hooks/click_observer_backend.py`, delegates operating-system-neutral record and
lifecycle handling to `hooks/click_observer_common.py`, and delegates Linux
`strace` collection and parsing to `hooks/click_observer_linux.py`. Privileged
native macOS `fs_usage` collection lives in `hooks/click_observer_macos.py`.
The facade continues to expose the existing tested entry points. Native macOS
and Windows ETW collectors require their supported host conditions and report
unavailable when those conditions are absent; they do not elevate privilege
automatically. OS-specific CI exercises their native paths. Local Linux checks
do not validate native macOS or Windows execution.

## Documented legacy symbol

`click_gate._validate_contract` remains bound to
`click_contract.validate_contract` because an earlier extraction explicitly
promised that compatibility. Its removal requires a deliberate compatibility
decision and release note.

## Private module forwarders

The v0.36.0 facade baseline started with 144 private module-forwarding bindings.
The current facade contains **1 private module-forwarding binding**:
`_validate_contract = click_contract.validate_contract`. Its exact owner is
recorded in `tests/click_gate_compatibility_baseline.py`.

This binding and the facade follow four rules:

1. New domain code and tests import the owning module instead of adding another
   `click_gate._name` dependency.
2. The baseline must not grow merely to make a local test or adapter convenient.
3. Retargeting, adding, or removing a forwarder requires an explicit baseline
   diff so the compatibility effect is reviewable.
4. Removal happens only after real host callers and compatibility tests have
   migrated to the owning module or a formal host API.

The architecture test records the one deliberate legacy exception and requires
all other domain callers to use their owning modules. Its purpose is to prevent
the private forwarding surface from growing again.
