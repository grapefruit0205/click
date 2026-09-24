# Click CI

Skip a CI test or check command when nothing it was observed reading has
changed since its last passing run on the default branch. It works for any
language and needs no configuration: wrap the command you already run.

```yaml
- uses: grapefruit0205/click/ci@<release tag or commit>
  with:
    run: pytest -q
```

## How it decides

| Where | Mode | What happens |
|---|---|---|
| push to the default branch (and schedules there) | `record` | The command runs under `strace -f`. When it passes, every file it read, directory it listed, program it executed and path it looked up without finding is stored with a content digest, together with a fingerprint of the environment variables that change how commands run. The record is saved with `actions/cache`. |
| pull requests, merge queues, other branches | `select` | The latest default-branch record is restored and each recorded input is digested again in this checkout. If every input and the environment fingerprint are unchanged, the step is skipped; otherwise the command runs as usual. |
| any | `shadow` | Decide as `select` would, run anyway, and report whether a skip would have been wrong. |

The reasoning is the same as Click's agent-side evidence reuse: a program
whose every read returns the same bytes takes the same path. Digests are of
content, never timestamps, so a fresh checkout on another runner compares
correctly.

Inputs are whatever the process tree touched, so child processes, shell
scripts, data files, fixtures, lockfiles, installed packages and the
toolchain itself are covered without a language-specific adapter. A file the
command created itself is its product, not an input.

## When it never skips

The record is marked volatile, and the command always runs, when the command:

- failed on the default branch;
- changed or deleted a file it had read (tool caches such as `__pycache__`,
  `.pytest_cache` and `.coverage` excepted);
- connected to a non-loopback address or DNS, or to a local socket it did not
  create (a database or Docker daemon);
- left processes running after it exited;
- used ptrace (a debugger, strace, or a leak checker inside the command);
- produced a trace line the reducer could not place.

The step summary and `$RUNNER_TEMP/click-ci-report.jsonl` state the reason,
and for a run caused by changes, the first changed inputs.

Default-branch pushes always run the command, so a skip on a pull request that
should not have happened surfaces, at the latest, when the change merges.

## Limits

- **Linux runners only.** Observation uses strace (installed with `apt-get`
  when missing, in record mode only). On macOS and Windows runners the action
  runs the command unchanged.
- **The command is the unit.** A single command that runs the whole suite
  reads nearly every source file, so it skips only when a change touches
  nothing it reads (documentation, other packages, other jobs' files). Split
  suites (matrix partitions, per-package steps) skip per part.
- **Environment variables** are compared through an allow-list (PATH, locale,
  time zone, proxies, and the variable families of common toolchains such as
  `PYTHON*`, `NODE_*`, `GO*`, `CARGO_*`, `JAVA_*`). CI bookkeeping such as run
  ids and refs is ignored. A test that reads another variable must not depend
  on it changing between runs.
- **Commands that run git** on the checkout read `.git/HEAD` and the index,
  which change with every commit, so they always run. Give them their own
  step to keep the rest of the suite skippable.
- **Temporary directories** (`/tmp`, `$RUNNER_TEMP`) and `/proc`, `/sys`,
  `/dev`, `/run` are not inputs.
- **Recording costs time**: tracing makes file-heavy commands two to four
  times slower on the default branch. Pull requests run untraced.
- **Tracing never decides pass or fail.** A command that fails under
  observation (tight timeouts, a debugger that cannot attach) runs again
  unobserved, that result is the step's result, nothing is recorded, and a
  warning names the cause.
- **Deciding reads every input once** when nothing changed, toolchain
  included. A Python suite that loaded 19,000 files (2.2 GB, mostly shared
  libraries) took about 6 seconds to decide. Repository files are checked
  first, so a changed source file ends the check early.
- When the same command runs in several matrix legs, give each leg a `unit`
  (for example `unit: py${{ matrix.python }}`) so each keeps its own record.

## Command line

```bash
python3 ci/click_ci.py run --mode record -- pytest -q
python3 ci/click_ci.py run --mode select -- pytest -q
python3 ci/click_ci.py plan -- pytest -q
```

`--store DIR` (or `CLICK_CI_STORE`) selects where records live;
`CLICK_CI_REPORT` names a JSON-lines report file. `.click/ci.json` may add
ignore patterns: `{"ignore": ["build/cache/*"]}`.
