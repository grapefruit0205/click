# Long-running worker boundaries

## Lifecycle Hook worker

Measured Hook startup made repeated process loading a material cost before
Phase 9: a fresh `click_hook` import took about 145 ms and about 23 MiB maximum
RSS on the local Linux host before this change. Codex also produced overlapping
PreToolUse and PostToolUse processes during ordinary tool calls. A session-scoped
worker is therefore implemented now for the four lifecycle modes
`prompt-submit`, `pre-tool`, `post-tool`, and `session-end`.

Codex command Hooks still start a small Python client for each event. That client
normalizes the event and sends it to one authenticated loopback worker. The
worker keeps Click's heavy module graph loaded, while rebinding the current Hook
process environment and canonical working directory for every request. Its
identity includes the session, canonical project root, plugin source tree,
interpreter, and configuration-relevant environment. A `SessionEnd` event,
identity change, or the default five-minute idle timeout retires it. Set
`CLICK_HOOK_WORKER=0` to use the compatible one-shot path, or set
`CLICK_HOOK_WORKER_IDLE_SECONDS` between 30 and 3600 seconds to change the idle
bound.

The runtime directory is private to the user. State files contain a random
token, loopback port, process id, and non-secret identity digest. Requests and
responses use size-limited framed JSON. The client falls back only when it knows
that no request byte was delivered. If delivery is uncertain, it returns a Hook
error without replaying the event, preventing duplicate state transitions.

The Windows launcher no longer starts Python once to probe its version and a
second time to handle the Hook. It locates a launcher from `PATH`, invokes it
once, and only falls back when the standard `py` launcher reports that no Python
installation exists. The resident Windows worker retains the selected
interpreter when it renders follow-up Click runners.

Internal `run-*` actions are deliberately excluded. Verification execution,
one-use claims, mutation runners, inspection runners, dashboard services, and
automatic sharding init/status/refresh continue to use the existing fresh
process and freshness boundaries.

A local alternating 15-sample command-level check used the same read-only
`click-gate default status` event and warmed filesystem caches. The compatible
one-shot path had a 104.192 ms median; warm worker dispatch had a 60.921 ms
median, 41.5% lower. Initial worker startup took 253.484 ms. Import-only maximum
RSS was 22,600 KiB for the full gate and 16,100 KiB for the client; the idle
worker reported 23,800 KiB RSS. The worker therefore reduces repeated CPU and
latency after its first event, but retains one gate-sized process and briefly
overlaps it with each small client. These are local component measurements, not
a claim that Codex process crashes are fixed or that whole-task time fell by the
same percentage.

## Pure analysis worker

Repeated pure analysis is a suitable boundary for a project-local Python
worker. Inventory normalization, AST parsing, dependency candidates, and
content-addressed analysis caches may reuse an already-started interpreter.
Verification execution authority, executable/environment identity, explicit
input snapshots at claim and final result, cancellation, and one-use claims
must remain fresh Hook boundaries and cannot be delegated to a stale cache.

The intended lifecycle is one worker per canonical project root and plugin
installation identity. It starts lazily after a supported analysis request and
stops on Click task completion, cancellation, project identity change, plugin
upgrade, explicit service stop, or a bounded idle timeout. Requests need size,
time, output, working-directory, and process limits. Cache keys include adapter
version, command/project/config identity, and content digests; mtime alone
cannot grant authority. A crashed or unavailable worker falls back to the
existing bounded one-shot collector.

This separate inventory/AST worker remains deferred until the Phase 9
measurements isolate repeated parsing as a material cost. The lifecycle Hook
worker above does not cache analysis results and does not change this decision.
