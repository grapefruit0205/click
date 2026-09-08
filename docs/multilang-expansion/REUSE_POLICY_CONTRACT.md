# Verification reuse policy contract v3

Verification request protocol v3 extends each check with three optional fields:

- `reuse`: `conditional` (default), one-shot `rerun`, or persistent
  `always-run`.
- `inputs`: up to 32 normalized repository-relative local file patterns.
- `outputs_required`: a boolean that forces real execution because Click does
  not restore build artifacts.

Protocol v2 remains accepted with its original three check fields. A v2 request
cannot silently opt into v3 fields. Internally normalized requests use v3; the
evidence ledger remains schema v1 with backward-compatible missing-field
defaults. An old source gains no input receipt merely because the default can
be read: the first request with explicit inputs runs the real check.

The executable argv group remains the check identity. Reuse restrictions are
bound to that source separately and can only become stricter within its
lifecycle and successor lineage. `rerun` applies to one reservation.
`always-run`, explicit input declarations, and `outputs_required` persist;
later omission cannot relax them. Automatic shard children inherit the parent
policy, and collapse conservatively combines child restrictions.

Explicit inputs are content and membership snapshots. The digest changes for
content edits, missing/created/deleted files, renames, and glob membership.
Absolute paths, traversal, duplicate or malformed patterns, symlinks,
non-regular files, races, sensitive names, and configured scan/file/count/byte
limits never authorize reuse. A broad glob that encounters a sensitive file is
unavailable without reading its contents. Bindings returned to plans and
receipts contain no path or content values; the direct input digest remains in
the private evidence ledger and is folded into the existing environment
binding used by exact, dependency, safe-change, successor, claim, and final
result checks.

Reuse eligibility never grants execution authority. Evidence still uses host
authority, Guarded still requires its independent approval, and cancellation,
replay, malformed state, workdir, executable, environment, host-coverage, and
final workspace checks retain their existing boundaries. A forced execution
that fails, is interrupted, or changes an explicit input records no reusable
PASS. Stable plan reasons distinguish `explicit-rerun-requested`,
`always-run-policy`, `required-output-not-guaranteed`,
`explicit-input-receipt-missing`, `explicit-input-changed`, and
`explicit-input-unavailable`.
