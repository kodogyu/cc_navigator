# Task 5 report: tmux queries

## What I implemented

- `src/ccnav/proc.py` — the single subprocess call site in the codebase.
  `Runner` type alias and `run_command(argv) -> (returncode, stdout)`, using
  `subprocess.run` with `stdout=PIPE`, `stderr=DEVNULL`, `universal_newlines=True`.
  No logic beyond the call itself; no test file (intentional per brief).
- `src/ccnav/tmuxctl.py` — the query half:
  - `parse_kv_lines(text)` — splits each line on the **first** `=` via
    `str.partition`, since pane titles are arbitrary UTF-8 that may contain
    `=`, `|`, spaces, etc. Blank lines and lines with no `=` are skipped.
  - `list_argv(socket, fmt)` — builds
    `["tmux", "-S", socket, "list-panes", "-a", "-F", fmt]`.
  - `_query(socket, fmt, run)` — private helper: runs, returns `{}` on
    non-zero exit, else `parse_kv_lines(out)`.
  - `sessions_by_pane(socket, run=run_command)` — format `#{pane_id}=#{session_name}`.
  - `titles_by_pane(socket, run=run_command)` — format `#{pane_id}=#{pane_title}`.
- `tests/test_tmuxctl_query.py` — the ten tests specified in the brief, copied
  verbatim (no changes needed).

Implementation matches the brief's Step 3 code exactly; no deviations.

## TDD Evidence

### RED

```
$ ./run-tests -k ParseKvLinesTest -k QueryTest
test_tmuxctl_query (unittest.loader._FailedTest) ... ERROR

======================================================================
ERROR: test_tmuxctl_query (unittest.loader._FailedTest)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_tmuxctl_query
Traceback (most recent call last):
  File "/usr/lib/python3.8/unittest/loader.py", line 436, in _find_test_path
    module = self._get_module_from_name(name)
  File "/usr/lib/python3.8/unittest/loader.py", line 377, in _get_module_from_name
    __import__(name)
  File "/data/playground/cc_navigator/tests/test_tmuxctl_query.py", line 3, in <module>
    from ccnav import tmuxctl
ImportError: cannot import name 'tmuxctl' from 'ccnav' (/data/playground/cc_navigator/src/ccnav/__init__.py)

----------------------------------------------------------------------
Ran 1 test in 0.000s

FAILED (errors=1)
```

Note: the brief predicted `ModuleNotFoundError`; the actual failure is
`ImportError: cannot import name 'tmuxctl' from 'ccnav'`. Same root cause
(module doesn't exist) — the exact exception class differs because
`ccnav` is a package with an `__init__.py`, so `from ccnav import tmuxctl`
raises `ImportError` for a missing submodule rather than
`ModuleNotFoundError`. This is expected and consistent with the brief's intent.

### GREEN

```
$ ./run-tests -k ParseKvLinesTest -k QueryTest
...
----------------------------------------------------------------------
Ran 10 tests in 0.000s

OK
```

```
$ ./run-tests
...
----------------------------------------------------------------------
Ran 60 tests in 0.218s

OK
```

60 = 50 existing + 10 new, as predicted. Output pristine both times: no
warnings, no stray prints, no `ResourceWarning`.

## Mutation Evidence

All five mutations applied one at a time to `src/ccnav/tmuxctl.py`, verified
against `tests/test_tmuxctl_query.py`, then reverted (confirmed via `diff`
against a saved original after each revert; final `diff` before commit was
clean).

| # | Mutation | Result | Killed by |
|---|----------|--------|-----------|
| 1 | `parse_kv_lines` uses `line.split("=")[0], [1]` instead of `partition` | KILLED | `test_splits_on_the_first_equals_only` (fails: `{'%1':'a'} != {'%1':'a=b=c'}`); also errors `test_blank_and_malformed_lines_are_skipped` (`IndexError` on the `garbage` line, which has no `=`) |
| 2 | `parse_kv_lines` keeps malformed lines (drop `if not separator: continue`) | KILLED | `test_blank_and_malformed_lines_are_skipped` (fails: `{'%5':'ok','garbage':''} != {'%5':'ok'}`) |
| 3 | `_query` returns `parse_kv_lines(out)` regardless of `code` | **SURVIVED** | none — see below |
| 4 | `list_argv` drops the `-a` flag | KILLED | `test_list_argv_uses_explicit_socket` (full-list `assertEqual` including `-a`) |
| 5 | `sessions_by_pane`/`titles_by_pane` swap format strings | KILLED (asymmetrically) | `test_sessions_by_pane` only — see below |

### Mutation 3 — genuine survivor

Removing the `if code != 0: return {}` guard in `_query` makes the whole
60-test suite stay green. Root cause: `test_no_tmux_server_yields_empty_dict`
uses `fake_run` returning `(1, "")` — **empty stdout**. `parse_kv_lines("")`
already returns `{}` regardless of whether the code check ran, so the test
can't distinguish "we checked the exit code" from "we happened to be handed
empty output." I did not invent a contorted test to force a kill; per the
brief's instruction, I'm reporting the gap:

**Missing test:** a case where `run` returns a *non-zero* code with
*non-empty* stdout, e.g. `fake_run` returning `(1, "%0=demo\n")`, asserting
`sessions_by_pane(...) == {}`. That would kill mutation 3. I did not add it,
since the brief caps the test file at the ten specified tests and asks me to
report gaps rather than chase survivors.

### Mutation 4 — killed, but worth a nuance

The brief specifically asked me to think about whether *any* current test
would notice a missing `-a`. It does get caught, but only because
`test_list_argv_uses_explicit_socket` does a full-list `assertEqual` on the
entire argv (including `-a`). If that test were loosened to check only
individual elements (as `test_sessions_by_pane`/`test_titles_by_pane` do,
checking only `calls[0][-1]`), a dropped `-a` would slip through invisibly —
`sessions_by_pane`/`titles_by_pane` tests never assert on the `-a` flag at
all, only on the trailing format string. So the coverage for `-a` rests
entirely on one test being written as an exact list comparison, not on any
semantic assertion that `-a` matters. That's a thin margin, worth knowing
about even though it currently kills the mutant.

### Mutation 5 — asymmetric kill

Swapping the two format strings is caught by `test_sessions_by_pane`, which
asserts `calls[0][-1] == "#{pane_id}=#{session_name}"`. But
`test_titles_by_pane`'s `fake_run` ignores the argv it's given and always
returns a fixed string — so that test alone would **not** detect its own
half of the swap. The kill exists, but it's carried entirely by the
sessions-side assertion.

## Smoke check (real tmux, private socket)

Ran on a throwaway socket in `/tmp`, never the default tmux socket. tmux
version in this environment: `tmux 3.0a`.

```
$ S=$(mktemp -u /tmp/ccnav-smoke.XXXX)
$ tmux -S "$S" new-session -d -s smoke
$ tmux -S "$S" list-panes -a -F '#{pane_id}=#{session_name}'
%0=smoke
$ tmux -S "$S" list-panes -a -F '#{pane_id}=#{pane_title}'
%0=kodogyu-desktop
$ tmux -S "$S" kill-server
```

Observation on `#{pane_title}` for a plain shell: it did **not** come back
empty. tmux 3.0a defaults an unset pane title to the *hostname*
(`kodogyu-desktop`) rather than an empty string — this is tmux's own
default-title behavior before any program has emitted an OSC title
escape. This differs slightly from the brief's framing ("if it comes back
empty, that's expected") but is consistent with the underlying design fact:
the title is only meaningful (a real Claude Code status string) once an
inner program overwrites it with its own OSC sequence. An untouched shell's
title is just whatever tmux's default happens to be — display noise either
way, never parsed.

I also attempted an additional (optional, beyond-brief) check: sending an
OSC-2 title containing `=`, `|` via `send-keys` to confirm the round-trip
through a live pane. This repeatedly crashed the throwaway tmux server
("server exited unexpectedly") even for a plain `echo hello` with no OSC
sequence at all — this reproduces with any `send-keys` + `Enter` in this
sandboxed environment, most likely a pty/tty limitation of this container
(no real terminal backing the session), unrelated to cc_navigator code. I
did not chase this further since `send-keys`/pane interaction is Task 6's
concern, not this task's, and the four exact commands specified in the
brief's Step 6 all ran cleanly and produced real, useful output. All
throwaway sockets and any stray tmux server processes were killed and
`rm -f`'d; confirmed via `ps aux` and `ls /tmp/ccnav-smoke*` that nothing
was left behind (I did initially leave one stray socket/server from an
exploratory step — caught it and cleaned it up before finishing).

## The timeout observation (for Task 10)

`proc.run_command` calls `subprocess.run` with no `timeout=`. If the tmux
server is hung (e.g., a wedged pane, a stuck socket, or tmux itself
deadlocked), this call blocks forever. Since `sessions_by_pane`/
`titles_by_pane` are meant to be polled once per second by the UI, a single
hang would freeze whichever thread calls them, with no way to recover
short of killing the process. I did not add a `timeout=` — the brief and
the plan are explicit that this is a Task 10 threading-model decision (the
guard might belong in `proc.py` as a `timeout=`, or it might belong in the
caller as a watchdog thread with its own timeout, depending on how Task 10
structures the polling loop). Flagging it here per instructions; the code
is untouched.

## Files changed

- Created: `src/ccnav/proc.py`
- Created: `src/ccnav/tmuxctl.py`
- Created: `tests/test_tmuxctl_query.py`

Commit: `58d32ce` — "feat: tmux pane and title queries"

## Self-review

**Completeness:** all seven brief steps done, including the mutation table
(5/5 attempted, one genuine survivor reported) and the real-tmux smoke check
on a private socket, with output pasted above.

**Quality:** names match the interface spec exactly
(`Runner`, `run_command`, `parse_kv_lines`, `list_argv`, `sessions_by_pane`,
`titles_by_pane`). Docstrings explain the "why" (title is display-only,
partition not split) rather than restating the "what". Style matches
`statestore.py`/`hook.py`: `from __future__ import annotations`, type
comments for dict literals, `Dict`/`List`/`Tuple` from `typing` (3.8-safe).

**Discipline (YAGNI):** no retry logic, no caching, no `Runner` class (it's
a type alias only, as specified), no test file for `proc.py`, no `timeout=`.
Confirmed by re-reading both files after implementation — they match the
brief's Step 3 code verbatim.

**Testing:** all ten tests assert on real behavior (parsed dicts, exact
argv lists, exact format strings) — no vacuous assertions. Output is
pristine across every run: no warnings, no stray prints, no
`ResourceWarning`. No test spawns a real subprocess — `run` is injected as a
fake in every `QueryTest` case; `proc.py` itself is exercised only in the
separate, non-suite smoke check against a real, private, throwaway tmux
socket.

## Issues or concerns

1. Mutation 3 (the `code != 0` guard) survived the current test suite — see
   the dedicated section above for the exact missing test case.
2. Mutation 4's kill is thin: it depends on one test doing a full-list
   comparison; no test asserts on `-a` semantically or independently of that
   single test's construction.
3. Mutation 5's kill is asymmetric, carried entirely by
   `test_sessions_by_pane`.
4. `run_command` has no `timeout=` and will block forever on a hung tmux
   server — explicitly left alone per the brief, flagged for Task 10.
5. tmux 3.0a's default pane title for an untouched shell is the hostname,
   not an empty string — noted as a minor deviation from the brief's
   framing, but doesn't change any conclusion since the title is always
   display-only regardless of its default value.

---

# Revision after review

The coordinator reviewed the report, independently reproduced the mutation-3
survivor, and confirmed it is a defect in the plan's *test*, not in the
implementation. Per instructions I left `src/ccnav/tmuxctl.py` and
`src/ccnav/proc.py` untouched and changed only
`tests/test_tmuxctl_query.py`. Two edits:

1. **`test_no_tmux_server_yields_empty_dict` → renamed
   `test_nonzero_exit_yields_empty_dict_even_with_output`.** Its `fake_run`
   now returns `(1, "%0=zombie\n")` — a non-zero exit *with* real stdout. Now
   an empty dict can only come from the `if code != 0: return {}` guard, not
   from `parse_kv_lines("")` incidentally returning `{}`. The rename says what
   the test now pins down. It still asserts both `sessions_by_pane` and
   `titles_by_pane` return `{}`. A comment records *why* the guard is
   load-bearing (a dead socket must not read as "every session vanished,"
   which in Task 8 would prune live state files).

2. **`test_titles_by_pane` now captures `calls`** the same way
   `test_sessions_by_pane` does, and asserts `calls[0][-1] ==
   "#{pane_id}=#{pane_title}"`. Previously its `fake_run` ignored the argv, so
   only the sessions test noticed a format-string swap. Each query test now
   stands on its own.

Left mutation 4 alone: `test_list_argv_uses_explicit_socket` asserts the
entire argv list including `-a`, so deleting the flag genuinely fails a named
test. That is a real kill.

## Corrected mutation table (all runs cache-cleared)

One caution worth recording: mutation 5 (swapping the two format strings)
leaves the source file byte-for-byte the same *size*, and CPython invalidates
`.pyc` bytecode on `(mtime, size)` with 1-second mtime granularity. Rapid
`cp`-based restores within the same second can therefore make the test
harness silently reuse stale mutated bytecode. I hit exactly that once (a
phantom `FAILED (failures=2)` on an already-restored tree) and resolved it by
clearing `__pycache__` between mutation runs. The table below was produced
with the cache cleared before every run, and mutations 3 and 5 were each
re-verified a second time in isolation with the cache cleared.

| # | Mutation | Result | Killed by |
|---|----------|--------|-----------|
| 1 | `parse_kv_lines` uses `split("=")[0],[1]` not `partition` | KILLED | `test_splits_on_the_first_equals_only` (+ errors `test_blank_and_malformed_lines_are_skipped`) |
| 2 | `parse_kv_lines` keeps malformed lines (drop the guard) | KILLED | `test_blank_and_malformed_lines_are_skipped` |
| 3 | `_query` ignores the exit code | **KILLED** (was SURVIVED) | `test_nonzero_exit_yields_empty_dict_even_with_output` |
| 4 | `list_argv` drops the `-a` flag | KILLED | `test_list_argv_uses_explicit_socket` (full-list assert incl. `-a`) |
| 5 | `sessions_by_pane`/`titles_by_pane` swap formats | **KILLED symmetrically** | `test_sessions_by_pane` **and** `test_titles_by_pane` (both now assert their argv) |

5/5 killed, each by a named test. The mutation-3 survivor and the mutation-5
asymmetry from the original report are both closed. Full suite:
`Ran 60 tests ... OK`, output pristine, `git status` clean.

## tmux 3.0a reports the hostname (not empty) for a plain shell's pane_title

Recording this under its own heading at the coordinator's request. In the
Step 6 smoke check, `#{pane_title}` for a freshly-spawned, plain-shell pane
came back as the **hostname** (`kodogyu-desktop`), not an empty string:

```
$ tmux -S "$S" list-panes -a -F '#{pane_id}=#{pane_title}'
%0=kodogyu-desktop
```

tmux 3.0a seeds an unset pane title with the client hostname; it only becomes
a meaningful status string once an inner program (Claude Code) emits its own
OSC-2 title. Consequences for later tasks:

- **Task 9 (UI):** any tmux pane *not* running Claude Code will present a
  hostname as its title, not a blank. The UI must not assume an empty title.
- **Task 8 (state-file join):** such panes should be filtered out by the join
  against the state files — no state file for that pane means no row. A pane
  showing only a hostname has no cc_navigator state and must not appear.

The coordinator will carry this into the Task 8 brief.

## Open risk carried forward

`proc.run_command` still has no `timeout=` and will block forever on a hung
tmux server. Left untouched per the brief; recorded as an open risk for
Task 10's threading-model decision.
