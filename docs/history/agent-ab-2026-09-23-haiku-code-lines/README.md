# Paired Haiku 4.5 sessions with the traceback's lines in Click's failure summary (2026-09-23)

The [earlier Haiku record](../agent-ab-2026-09-23-haiku/README.md) found that
Click with Evidence auto-routing made Haiku 4.5 sessions 52% faster but added
turns (16 → 22), tool calls (14.5 → 20.5) and 31% cost. Its hypothesis was the
actionable failure summary: it names file, line and message but not the
failing line the traceback shows, and every Click-on session opened three or
four test files while three reproduced a failure with `python3 -c`. The build
measured here adds, under each summarized failure, the code lines the
traceback printed, and takes the message from the exception line: for five of
the fixture's thirteen failures the old message was the last line of
assertEqual's diff. The paired design, fixture, prompt and model are the
earlier record's, unchanged.

| median (min–max) | A: Click off | B: Click on | change | earlier record: B vs A |
|---|---|---|---|---|
| turns | 16 (15–18) | 16.5 (16–20) | +3% | 21.5 vs 15.5, +39% |
| tool calls | 15 (14–17) | 15.5 (15–19) | +3% | 20.5 vs 14.5, +41% |
| cost | $0.123 (0.117–0.142) | $0.121 (0.116–0.156) | −2% | $0.163 vs $0.124, +31% |
| output tokens | 5,672 | 5,479 | −3% | 7,375 vs 5,488, +34% |
| session wall time | 382 s (314–453) | **162 s (146–275)** | **−58%** | 190 s vs 399 s, −52% |
| test wall time inside the session | 290 s (248–368) | 76 s (68–86) | −74% | 87 s vs 331 s, −74% |
| test module executions | 20 (16–24) | 10 (10–11) | −50% | 14 vs 21, −33% |
| test requests | 5 (4–6) | 5.5 (5–6) | | 5 vs 5.5 |

Tool calls by type, median (min–max) per session:

| | earlier A | earlier B | A | B |
|---|---|---|---|---|
| Bash | 6.5 (4–10) | 8 (5–11) | 7 (5–7) | 6.5 (6–7) |
| … `python3 -c` probes | 0 (0–2) | 2 (0–2) | 0 | 0 |
| Read | 4 (3–5) | 7 (7–8) | 5 (3–6) | 6 (6–7) |
| … files under `tests/` | 1 (0–2) | 3 (3–4) | 1.5 (0–3) | 3 (3–3) |
| Edit | 4 (3–4) | 5.5 (5–6) | 4 (3–5) | 3 (3–5) |
| … of `ledger/beta.py` | 2 (1–2) | 3.5 (3–4) | 2 (1–3) | 1 (1–3) |

All eight sessions fixed the three bugs, left `tests/` untouched and passed an
independent full-suite run afterwards. Every one of the 22 test requests in
the Click-on sessions went through Click (10 routed plain commands, 12 typed
`click-gate verify`, none with a pipe), and all 11 failing summaries they
printed carried code lines. `evidence/analysis.txt` (the earlier record's
`analyze.py`) and `evidence/comparison.txt` (`compare.py`, both records side
by side) are the authoritative tables; the medians above that analyze.py
rounds to whole numbers are exact.

## What the numbers show

- **The Click-on arm's extra turns, tool calls and cost are gone, and the
  wall-time gain stayed.** The Click-off arm reproduced the earlier one (16
  turns, 15 tool calls, $0.123 against 15.5, 14.5 and $0.124), so the two
  records compare. Against the earlier Click-on arm, the Click-on median fell
  by five turns and five tool calls and its cost by 26%. Cost is at parity
  with Click off, not below it.
- **Not through fewer test-file reads.** Every Click-on session still opened
  the test file of each failing module before editing it, three per session
  (earlier: three or four). This record's Click-off sessions opened none to
  three (earlier: none to two). The code lines did not change that habit on
  this fixture, so the earlier record's reading explanation is not supported.
- **The probes are gone, and the old message explains them.** In the earlier
  record, each of the three probing Click-on sessions ran
  `python3 -c "from ledger import beta; print(beta.allocate(100, [1, 1, 1]))"`
  right after a summary reading
  `test_equal_weights_split_evenly_with_remainder_first at tests/test_beta.py:15: AssertionError: + [34, 33, 33]`:
  the last line of assertEqual's diff, which names only the expected list.
  The probe printed the actual `[33, 33, 34]`. Here session 07-B made the same
  first fix and read `AssertionError: Lists differ: [33, 33, 34] != [34, 33, 33]`
  with `self.assertEqual(beta.allocate(100, [1, 1, 1]), [34, 33, 33])` under
  it, and edited again without a probe. No session probed. The earlier record
  says its Click-off sessions never probed; its session 08-A ran the same probe
  twice, after runs filtered with `grep -E "^(FAIL|OK|Ran)"`, which hides the
  message too, and two attempts to run the failing test by name that did not
  import.
- **Fewer rounds on `beta`, not attributable.** A first fix that sorts
  `(remainder, index)` tuples with `reverse=True` gives the leftover cent to
  the last share and fails once more. Three of the four Click-on sessions here
  wrote a fix that breaks ties by the lower index first (one edit of
  `ledger/beta.py`), against one of the four Click-off sessions here and one of
  the eight earlier sessions. The expected `[34, 33, 33]` was in front of every
  arm (the old summary's `+ [34, 33, 33]`, the traceback, the test file), so
  four sessions cannot say whether the summary had a part in it. Together with
  the probes this is the remaining difference to the earlier Click-on arm.
- **The summary grew and stayed bounded.** The first failing run's Click output
  was 4,530 bytes against 3,293 in the earlier record; unittest's own output for
  the same run is 9,203 bytes at this record's fixture paths (8,579 earlier).
- **One session's wall time includes a network failure.** Session 02-B spent
  74 s in API retry backoff before its first response (seven `api_retry`
  events; no other session in either record has one). Its wall time is 275 s,
  about 201 s without the backoff; the median does not change.
- Four sessions per arm, one machine, one model, one task. The build changes
  the code lines and the message together, and on this fixture the effect runs
  through the message.

## What changed in the summary

The same first run of the fixture, module `test_alpha`, as the Click-on arm
read it in the earlier record and in this one:

```text
- test_alpha.PaginationTests.test_last_page_is_short at tests/test_alpha.py:17: AssertionError: ?  +
```

```text
- test_alpha.PaginationTests.test_last_page_is_short at tests/test_alpha.py:17: AssertionError: Lists differ: [] != [6]
    tests/test_alpha.py:17: self.assertEqual(alpha.paginate(list(range(7)), 3, 3), [6])
```

- **Code lines.** Under each failure, the source lines the traceback printed
  for the test's frame and, when different, the innermost frame in the project
  (at most two frames of three lines of 160 characters, each location once per
  summary), taken from the check's captured output. Click reads no file for
  them.
- **Message.** The exception line instead of the last line of a multi-line
  message. Five of the fixture's thirteen failures read `+ [0, 1, 2]`, `?  +`,
  `+  22]`, `+ [34, 33, 33]` or `+ hello-world-again` in the earlier record.
- **Subtests** keep their test name: `- total=100, weights=[3, 2, 1] at …`
  became `- test_beta.AllocationTests.test_shares_sum_to_the_total (total=100,
  weights=[3, 2, 1]) at …`.
- Not exercised by this fixture: an error keeps its exception type
  (`ERROR: KeyError: 'missing'`, was `ERROR: 'missing'`); each pytest failure
  takes its location, code and full `E` line from its own report section
  (before, every pytest failure got the same location, the last of the first
  eight frames found anywhere in the output, and none when pytest printed only
  long entries: six failures of a pytest 9.1.1 sample all read
  `pkg/core.py:16`); frames in an in-repository virtualenv are skipped; and the
  output `unittest -b` appends after a traceback is not parsed as the failure.

## Setup

- **Click** build of branch `feat/auto-route-checks` (PR #156) at `84d9902`
  plus this change, loaded with `--plugin-dir` from a pinned copy of
  `dist/claude` outside the worktree (plugin digest `c03bef67624328e5` in
  `evidence/summary.json`, equal to this branch's `dist/claude`). The earlier
  record measured an earlier build of #156 (`8d4ec6161b5de046`). Apart from
  this change, the two builds differ in routing checks other than test
  runners, in keeping a routed command's pipe with raw output, and in recording
  a routed check that writes the tree as a host change; no Click-on request
  here was anything but a plain unittest run, and the fixture's tests write
  nothing.
- **Host, fixture and task** as in the earlier record: Claude Code CLI
  2.1.280, `claude -p --output-format stream-json`,
  `--model claude-haiku-4-5-20251001`, `--max-turns 80`, tools `Bash Read Edit
  MultiEdit Write Glob Grep`, the installed Click and the unrelated
  `deploy-on-aws` plugin disabled, order A B B A A B B A, one session at a
  time; `make_fixture.py` with 300,000 PBKDF2 rounds and the same prompt.
- **Driver.** Other Claude Code sessions on the machine were running Click's
  own test suites during this measurement. `evidence/run_gated.py` starts each
  session with the earlier record's `run_ab.run_session`, unchanged, and adds
  an idle gate: before each session it waits until processes outside the
  driver have used less than 1.5 CPU cores on average (and never more than 3)
  over 60 s. It then samples every 5 s the cores used by the machine and by the
  session's own process tree. Sessions waited 61–707 s, and other processes
  used 1.1–1.9 cores on average during each session (the desktop app alone
  uses about 0.4–0.9), under `machine` in `summary.json`. The machine has 14
  cores, four of them performance cores; Click-on requests that executed
  shards took 16–19 s but one of 11 s (earlier record: 17–19 s). A first
  attempt with a 1.0-core gate was stopped before any session started,
  because the desktop session alone came close to that threshold.
- **Metrics** as in the earlier record, from the transcripts; `compare.py`
  counts Read calls under `tests/`, `python3 -c` probes, edits of
  `ledger/beta.py` and `api_retry` backoff from each `stream.jsonl`.

## Reproduction

```sh
python3 docs/history/agent-ab-2026-09-23-haiku-code-lines/evidence/run_gated.py --root /tmp/click-ab-code-lines --plugin-dir <pinned copy of dist/claude> --idle-cores 1.5
python3 docs/history/agent-ab-2026-09-23-haiku/evidence/analyze.py /tmp/click-ab-code-lines
python3 docs/history/agent-ab-2026-09-23-haiku-code-lines/evidence/compare.py /tmp/click-ab-code-lines
```

`evidence/` keeps, per session, `stream.jsonl` (the full transcript),
`result.json` (the `claude -p` result event) and `bash_calls.json` (Bash calls
paired with their outputs), plus `summary.json`, the driver's `run.log`,
`analysis.txt` and `comparison.txt`.
