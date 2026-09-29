# Paired agent sessions with Haiku 4.5: Click with auto-routing vs off (2026-09-23)

The [2026-09-12 record](../agent-ab-2026-09-12/README.md) found that Opus 5
followed Click's Evidence directive in every cycle while Haiku 4.5, with the
same directive in its context, ran `python3 -m unittest discover -s tests`
directly in all four cycles of its pilot and issued no `click-gate` request.
Evidence mode now routes such a plain check command through Click. This record
repeats the paired design with Haiku 4.5 to see whether that gives a model that
ignores the directive the same effect.

| median (min–max) | A: Click off | B: Click on, auto-routing | change |
|---|---|---|---|
| session wall time | 399 s (252–459) | **190 s (142–200)** | **−52%** |
| test wall time inside the session | 331 s (201–395) | 87 s (52–89) | −74% |
| test module executions | 21 (12–24) | 14 (8–16) | −33% |
| test requests | 6 (3–7) | 5 (4–8) | |
| turns | 16 (12–19) | 22 (19–25) | **+39%** |
| tool calls | 14.5 (11–18) | 20.5 (18–24) | **+41%** |
| cost | $0.124 (0.094–0.139) | $0.163 (0.141–0.176) | **+31%** |
| output tokens | 5,488 | 7,375 | +34% |

All eight sessions fixed the three bugs, left `tests/` untouched and passed an
independent full-suite run afterwards. **Every one of the 22 test requests in
the Click-on sessions went through Click**: 12 were the model's own
`python3 -m unittest discover -s tests`, routed by the Hook, and 10 were typed
as `click-gate verify -- …`: in two sessions Haiku switched to the Click form
after the notice on its first routed run and kept it, in one it typed it twice
and went back to the plain command, in one it never typed it. In the 2026-09-12
pilot the same model issued none. The session got faster and more expensive at
the same time, and the record says both on the same line wherever it is cited.
`evidence/analysis.txt` is the authoritative table.

## What the numbers do and do not show

- **The wall-time gain is mostly concurrent shard execution**, as in the Opus
  record: any request that executes at least one shard takes about 17 s against
  65–68 s for a serial full discover on this machine. Reuse shows up as
  executions (21 → 14) and as final requests that executed nothing (two of four
  sessions).
- **Turns and tool calls went up.** In every Click-on session Haiku opened three
  or four of the test files, and in three of them it reproduced a failure by
  hand with `python3 -c`; the Click-off sessions opened zero to two test files
  and never probed. The
  actionable failure summary Click gives unittest in Evidence mode names the
  file, line and assertion message but not the failing test's own code line,
  which unittest's traceback shows. That is a hypothesis from four sessions per
  arm, not an isolated cause. With Opus 5 the 2026-09-12 record found no such
  increase (16 turns in both arms).
- **A read-only probe cost reuse.** In sessions 3, 6 and 7 Haiku ran two
  `python3 -c "from ledger import beta; …"` probes around its last edit, and the
  next request re-executed all four shards; Click's reason for the three whose
  sources had not changed was `observed-input-changed`. Session 2, with no probe,
  re-ran only the edited shard at the same step. An unrecognized shell command
  is recorded as a host mutation; the exact mechanism was not isolated.
- **The typed form broke on a pipe.** In session 7 Haiku typed
  `click-gate verify -- python3 -m unittest discover -s tests 2>&1 | tail -50`.
  The argv form passed `2>&1`, `|`, `tail` and `-50` to unittest as arguments and
  the check failed with exit 2 (again with `2>&1` alone), after which Haiku went
  back to the plain command, which the Hook routed. This predates the branch: a
  routed command keeps such a suffix for the shell, the typed form does not.
- Four sessions per arm, one machine, one model, one task. The machine's full
  discover took 65–68 s here against 41–57 s in the 2026-09-12 record, which
  inflates the test share of a Click-off session (80–86% of its wall time).

## Setup

- **Click** build of branch `feat/auto-route-checks`, loaded with
  `--plugin-dir` from a pinned copy of `dist/claude` outside the worktree
  (plugin digest in `evidence/summary.json`); the installed Click is disabled
  in both arms, as is the unrelated `deploy-on-aws` plugin. Evidence mode, no
  repository policy, no committed shard plan.
- **Host** Claude Code CLI 2.1.280, `claude -p --output-format stream-json`,
  `--model claude-haiku-4-5-20251001`, `--max-turns 80`, tools `Bash Read Edit
  MultiEdit Write Glob Grep`, sessions alternated A B B A A B B A and run one at
  a time.
- **Fixture and task prompt** identical to the 2026-09-12 record
  (`evidence/make_fixture.py`, 300,000 PBKDF2 rounds; three seeded bugs, 13 of
  19 tests failing, `delta` passing from the start).
- **Environment.** The sessions were started from another Claude Code session.
  `evidence/run_ab.py` removes that session's own `CLAUDE_*` variables (effort,
  session id, messaging socket) from the child environment and sets
  `DEEPSEEK_POLICY=off`, so a user-level hook on the measurement machine that
  gates edits when a session is routed to DeepSeek stays inert in both arms.
  The child sessions used the Anthropic API directly.
- **Metrics** come from the transcripts. A routed check is the model's own
  command whose output is Click's, so a call counts as Click-handled by its
  output (`[Click verification i/n:...]`, the `[Click result]` line), not by
  `click-gate` in the command: `direct` requests are unittest runs (a full
  discover executes the four modules), `routed` and `click-gate` requests
  execute the shards Click lists, and their test time is Click's parallel wall
  clock. Tool calls are counted per type from each `stream.jsonl`.

## Measured build and final branch

The measured build routed test runners only and replaced output filters with
Click's summary; the final branch routes every check Click recognizes, keeps a
command's own `2>&1` and filters, and records a routed check that writes the
tree as a host change. Every test request in the Click-on sessions was the
plain `python3 -m unittest discover -s tests` or the typed Click form of it: no
filter, a test runner, and a fixture whose tests write nothing. For those
requests the two builds produce the same runner request, so the numbers apply
to the final branch.

## Reproduction

```sh
python3 docs/history/agent-ab-2026-09-23-haiku/evidence/run_ab.py --root /tmp/click-ab-haiku --plugin-dir <pinned copy of dist/claude>
python3 docs/history/agent-ab-2026-09-23-haiku/evidence/analyze.py /tmp/click-ab-haiku
```

`evidence/` keeps, per session, `stream.jsonl` (the full transcript),
`result.json` (the `claude -p` result event) and `bash_calls.json` (Bash calls
paired with their outputs), plus `summary.json`. `evidence/analysis.txt` is
generated by `analyze.py` and is the authoritative table.
