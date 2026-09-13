# Privacy

Click runs entirely on the machine that runs your coding agent. This page
describes what it stores, where, and what it never does. It is a description of
the current code, not a contractual promise; the sources under `hooks/` are the
authority.

## No network traffic

Click's Hooks, one-use runner, resident Hook worker, dashboard and Node
Inspector controller open no connection to any host other than the local
loopback interface (`127.0.0.1`). Nothing is uploaded, no telemetry is sent, and
no update check is performed. Native observer companions are compiled locally
from the sources shipped with the plugin; nothing is downloaded.

## What is stored, and where

- **Evidence state, receipts and dashboards** live under the host's plugin data
  directory (`PLUGIN_DATA` on Codex, `CLAUDE_PLUGIN_DATA` on Claude Code).
  Receipts record command lines, file paths relative to the repository,
  content digests, environment *digests* for an allowlisted set of variables,
  and timing. They do not store file contents or environment values.
- **Repository-owned policy** is only written inside the repository when you
  ask for it (`click-gate sharding init` writes `.click/evidence-shards.json`;
  input and environment policies are files you commit yourself). Automatic
  shard plans are kept in the plugin data directory, not in your repository.
- **Native observation caches** (compiled companions, input indexes) live under
  the system temporary directory, keyed by the interpreter and source identity.
- **Bounded output logs** referenced by actionable failure summaries are kept
  under the plugin data directory so the host does not have to read raw test
  output twice.

## What is observed

To decide whether a passing check is still valid, Click observes which files a
check reads (Linux `strace`, macOS privileged `fs_usage`, Windows ETW, and a
CPython or V8 Inspector companion). The record keeps paths and digests of the
observed inputs; it never keeps their contents. Public dashboard exports omit
private input paths.

## Removal

Deleting the plugin data directory removes every receipt, dashboard and log.
Deleting `.click/` in a repository removes any policy you committed there.
Removing the plugin removes its hooks; no other files on the system are
modified.
