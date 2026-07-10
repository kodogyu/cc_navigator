# Task 8 report: The model

## What I implemented

- `src/ccnav/model.py` — pure module joining state-file records with live tmux
  panes into `Row` objects for the UI. Contents match the plan's code at lines
  1514-1596 verbatim: `Row` (frozen dataclass with `waiting` and `window_title`
  properties), `live_pane_keys`, `_newest_per_pane` (private helper), and
  `build_rows`.
- `tests/test_model.py` — the nine tests from the plan (lines 1420-1503)
  verbatim, across `BuildRowsTest` (7 tests) and `LivePaneKeysTest` (2 tests).

No deviation from the plan's code or tests was needed.

## TDD Evidence

**RED** — before `src/ccnav/model.py` existed:

```
$ ./run-tests -k BuildRowsTest -k LivePaneKeysTest
ERROR: test_model (unittest.loader._FailedTest)
ImportError: cannot import name 'model' from 'ccnav' (/data/playground/cc_navigator/src/ccnav/__init__.py)
Ran 1 test in 0.000s
FAILED (errors=1)
```

(The `ccnav` package already exists from prior tasks, so the failure surfaces
as an `ImportError` on the `model` name rather than `ModuleNotFoundError` on
the package — same root cause, module not found, expected per the brief.)

**GREEN** — after implementing `src/ccnav/model.py`:

```
$ ./run-tests -k BuildRowsTest -k LivePaneKeysTest
Ran 9 tests in 0.000s
OK

$ ./run-tests
Ran 99 tests in 0.232s
OK
```

90 pre-existing + 9 new = 99. Output pristine, no warnings.

## Mutation Evidence

All seven mutations from the brief were applied one at a time to
`src/ccnav/model.py`, tested against `BuildRowsTest` + `LivePaneKeysTest`, and
reverted (verified byte-identical to the original via `diff` before moving to
the next). Full suite confirmed green (99/OK) after final restoration.

| # | Mutation | Result | Named test |
|---|----------|--------|------------|
| 1 | `_newest_per_pane` keeps first record, not newest (`>` → `<`) | CAUGHT | `test_two_records_on_one_pane_keep_the_newest` |
| 2 | Delete `if not key[0] or not key[1]: continue` guard in `_newest_per_pane` | **SURVIVED** | — (see below) |
| 3 | Delete `if pane not in sessions: continue` in `build_rows` | CAUGHT (as `KeyError`) | `test_record_whose_pane_is_gone_produces_no_row` |
| 4 | Drop waiting-first term from sort key | CAUGHT | `test_waiting_rows_sort_first_then_newest_first` |
| 5 | Sort by `+row.updated_at` instead of `-row.updated_at` | CAUGHT | `test_waiting_rows_sort_first_then_newest_first` |
| 6 | `titles.get(pane) or pane` → `titles.get(pane, pane)` | **SURVIVED** | — (see below) |
| 7 | Key `_newest_per_pane` by pane alone, ignoring the socket | CAUGHT | `test_same_pane_id_on_different_sockets_are_distinct_rows` |

### Prediction 1: mutation 2 survives — **correct, confirmed empirically**

With the guard removed, I traced the one test that looks like it targets this
guard, `test_records_without_socket_or_pane_are_dropped`:

```python
bad = {"session_id": "x", "tmux_socket": "", "tmux_pane": "", "updated_at": 1}
model.build_rows([bad], {}, {})
```

I ran `_newest_per_pane` directly with the guard removed and confirmed:

```
newest (guard removed): {('', ''): {'session_id': 'x', ...}}
sessions_by_socket.get("", {}) = {}
"" not in {} -> True
```

The record survives `_newest_per_pane` and is keyed `("", "")`. It is then
dropped in `build_rows` by the *other* guard — `pane not in sessions`, since
`sessions_by_socket.get("", {})` returns `{}` and `"" not in {}` is `True`.
The test passes either way; it proves nothing about the guard it appears to
target.

**Missing test** (per your instruction, named and not added): a test that
supplies a `sessions_by_socket` entry for the empty-string socket, e.g.
`sessions_by_socket = {"": {"": "phantom"}}`, alongside a record with
`tmux_socket=""`, `tmux_pane=""`. With the guard present, the record is still
dropped (blank key rejected at the source). With the guard removed, it would
now survive `build_rows`'s second guard too (since `"" in {"": "phantom"}` is
`True`) and produce a bogus row — only *that* input isolates the
`_newest_per_pane` guard specifically. I did not add this test, per your
instruction.

### Prediction 2: mutation 6 survives — **correct, confirmed empirically**

`titles.get(pane) or pane` and `titles.get(pane, pane)` differ only when
`pane` is present in `titles` as a falsy value (i.e. an empty string). No
existing test supplies that shape: `test_missing_title_falls_back_to_the_pane_id`
uses `titles_by_socket = {SOCK: {}}` (key *absent*, not present-but-empty),
and `test_row_carries_the_tmux_session_and_title` supplies a non-empty title.
Both mutation and original behave identically for every input the suite
exercises.

**Missing test** (named, not added): a case with
`titles_by_socket = {SOCK: {"%1": ""}}` — pane present with an empty title.
Under the current implementation the row's `title` falls back to `"%1"`
(the pane id); under the mutant it would be `""`. I did not add this test,
per your instruction.

## Step 6: the hostname finding and this module

Task 5 established that tmux 3.0a reports a plain shell's `#{pane_title}` as
the machine's hostname, not `""`. Consequences for `model.py`:

**Does a non-Claude pane ever produce a row? No.** `build_rows` is driven
entirely by `records` (from `statestore.read_all`, i.e. actual JSON state
files on disk), not by the set of panes tmux knows about. A plain shell pane
that never ran Claude Code has no hook that ever wrote a state file, so it
never contributes a record to `records`, so it never appears as a key in
`_newest_per_pane`'s output, so `build_rows`'s loop never even looks at that
pane's entry in `titles_by_socket`/`sessions_by_socket`. The hostname title
tmux reports for that pane is simply never read. This holds regardless of the
Task 5 finding — the join is anchored on records, not on tmux's pane
inventory, so a title-shape change to tmux's self-report for untouched panes
is irrelevant to whether a row is created.

**Is the pane-id fallback (`titles.get(pane) or pane`) dead code? Mostly, but
not entirely.** Given the Task 5 finding, a *live* pane basically always has
*some* non-empty `#{pane_title}` (hostname at minimum, Claude Code's OSC title
once emitted), so the "explicitly empty title" branch is unlikely to occur in
practice. But the fallback can still trigger through the other path: `pane`
absent from `titles` entirely (`titles.get(pane)` returns `None`, not `""`).
That happens if `sessions_by_socket` and `titles_by_socket` are populated by
two separate tmux queries (`sessions_by_pane` and `titles_by_pane`,
per the design) that don't observe an identical pane set — e.g. a pane closes
between the two calls, or one query's tmux invocation fails/returns
incomplete output while the other succeeds. So the fallback is not dead: its
justification has shifted from "no program running in the pane" (the plan's
original assumption, now falsified for a plain shell) to "the two live-tmux
queries momentarily disagree about what panes exist." I'd keep the fallback;
I would not call it provably dead code, but its *primary* justification per
the plan text is gone.

**Immediately after `SessionStart`, before Claude Code emits its OSC title:**
`title` will be the hostname (or whatever the shell's title was, now
overwritten if the pane title reflects the running foreground process —
either way, not Claude-specific). This is real and I did not fix it. Flagging
for you to decide whether Task 9 needs a fallback (e.g. detect a bare
hostname/shell-shaped title and substitute the `cwd` basename or session id
until Claude Code's own OSC title lands) — a hostname string as the row's
primary line the moment a session starts would look wrong to the user, even
though it resolves itself within a tick or two.

## Step 7: `build_rows` and a malformed `updated_at`

Determined empirically with the actual module (not the test doubles):

| `updated_at` value | Result |
|---|---|
| `"abc"` (single record, only occurrence for its pane) | **Raises `ValueError`** — `invalid literal for int() with base 10: 'abc'` |
| `None` (single record) | **Raises `TypeError`** — `int() argument must be a string, a bytes-like object or a number, not 'NoneType'` |
| absent (key missing from the dict entirely) | **Does not raise.** `rec.get("updated_at", 0)` returns the default `0`, so `int(0) == 0`. Row is built normally with `updated_at=0`. |

Traceback for the realistic single-bad-record case:

```
Traceback (most recent call last):
  File "<string>", line 10, in <module>
  File "src/ccnav/model.py", line 78, in build_rows
    updated_at=int(rec.get("updated_at", 0)),
ValueError: invalid literal for int() with base 10: 'abc'
```

Mechanism: `_newest_per_pane`'s comparison line is
`if current is None or int(rec.get("updated_at", 0)) > int(current.get("updated_at", 0))`.
Python's `or` short-circuits, so for the *first* (and, in the common case,
only) record seen for a given `(socket, pane)`, `current is None` is `True`
and the `int(...)` conversions on the right-hand side are never evaluated —
a single malformed record sails through `_newest_per_pane` untouched. The
raise then happens later, unconditionally, at `build_rows`'s own
`updated_at=int(rec.get("updated_at", 0))` line when building the `Row`.

If a *second* record for the same pane is malformed, the comparison line
itself evaluates `int(...)` and raises there instead (confirmed empirically:
same `ValueError`, different frame). Either way, the exception is
unhandled — `build_rows` has no `try/except` around either `int()` call
(unlike `statestore.prune`, which does).

**Consequence, not fixed:** since `build_rows` runs on every UI tick, one
hand-edited or corrupted state file with a non-integer `updated_at` will
raise out of `build_rows` and reach whatever calls it — per the brief, that's
the GTK main loop in Task 9's tick handler. Depending on how that call site is
wired, this could crash the app or spam the loop with tracebacks every tick,
from a single bad file that `statestore.prune` was specifically designed to
guard against but never gets the chance to prune (pruning likely happens on
the same or an adjacent tick, but there's no ordering guarantee preventing
`build_rows` from running first and raising before `prune` ever runs). I did
not fix this, per your instruction — flagging for your decision.

## Files changed

- `src/ccnav/model.py` (new)
- `tests/test_model.py` (new)

## Self-review

- **Completeness:** nine tests present and passing, mutation step done for
  all seven mutations (with both predictions checked and confirmed rather
  than assumed), both investigations (Step 6, Step 7) completed empirically.
- **Quality:** names in `Row` match the interface spec exactly
  (`session_id, socket, pane, tmux_session, title, state, reason, message,
  cwd, updated_at`); `window_title` returns exactly `"ccnav:" + tmux_session`
  with no extra whitespace, verified by
  `test_row_carries_the_tmux_session_and_title`'s assertion
  `assertEqual(rows[0].window_title, "ccnav:demo")`.
- **Discipline (YAGNI):** `Row` is frozen with no setters, no
  `__post_init__`, no `update()`. `_newest_per_pane` does not sort — the one
  `rows.sort(...)` call is at the end of `build_rows`, as required. No age
  filtering anywhere in this module (left entirely to `statestore.prune`).
  `model.py` imports only `dataclasses`, `typing`, and `. import hookstate`
  — no `tmuxctl`, no `statestore`.
- **Testing:** the suite has zero subprocess calls, zero filesystem I/O, zero
  `sleep`. All fixtures are plain dicts constructed in-memory. Full suite run
  is deterministic and fast (0.23s for 99 tests).

## Issues or concerns

- Both predicted mutation survivors (2 and 6) are real gaps in the plan's
  test suite, confirmed by direct inspection, not assumed. Per your
  instruction I have named the missing tests and not added them.
- Step 6: the pane-id title fallback's justification has shifted (from "no
  program running" to "the two tmux queries raced"), and immediately after
  `SessionStart` the row's title will visibly be a hostname until Claude
  Code's OSC title lands — flagged for Task 9, not fixed here.
- Step 7: `build_rows` raises uncaught on a malformed `updated_at` (`"abc"` →
  `ValueError`, `None` → `TypeError`); absent is safely defaulted to `0` and
  does not raise. Flagged for your decision, not fixed here.

---

## Revision after review

The coordinator confirmed both survivors are observable (not merely testable in
theory) and directed three changes: pin mutation 2, pin mutation 6, and fix the
tick-loop crash. All three are done.

### 1. Mutation 2 pinned — `test_a_record_without_a_socket_cannot_acquire_a_window_address`

The failure mode is worse than a stray row: without the `_newest_per_pane`
guard, a record with `tmux_socket=""` / `tmux_pane=""` joins against a socket
keyed `""` and **acquires a window address**. Confirmed with the mutant in
place — the produced row was:

```
Row(session_id='x', socket='', pane='', tmux_session='ghost-session',
    title='', ..., updated_at=1)
```

Its `window_title` is therefore `ccnav:ghost-session`, which Task 7 would
compare `===` against real X11 titles and activate. A corrupted/hand-edited
state file must never name a window. The new test supplies exactly the garbage
record plus `sessions_by_socket={"": {"": "ghost-session"}}` and asserts no
rows. `test_records_without_socket_or_pane_are_dropped` is kept — it is not
wrong, it just passes via the *other* guard (`pane not in sessions`); a comment
on the new test documents that both are deliberate and neither is a duplicate.

### 2. Mutation 6 pinned — `test_present_but_empty_title_falls_back_to_the_pane_id`

Feeds `titles_by_socket={SOCK: {"%1": ""}}` (title present but empty) and
asserts `title == "%1"`. With the `titles.get(pane, pane)` mutant the title is
`''` (confirmed: `AssertionError: '' != '%1'`), leaving the UI's primary line
blank.

### 3. Tick-loop crash fixed — `_as_int`

`build_rows` runs on a one-second GTK timer. An unhandled exception there does
not skip one tick — it propagates out of the timeout callback and the model
stops updating for the life of the process, leaving cc_navigator showing stale
rows forever. That is exactly the "looks like it works while doing nothing"
failure this project exists to prevent.

Fix mirrors `statestore.prune`'s existing policy (bad timestamp = maximally
stale): a module-private `_as_int(value) -> int` returns `0` on `TypeError` or
`ValueError`, used at **both** sites — `_newest_per_pane`'s comparison and
`build_rows`'s `updated_at=` argument. Both were required: leaving the
comparison as bare `int()` would still raise when a second record for the same
pane carries a bad timestamp. A row with an unparseable timestamp now sorts as
oldest and `prune` deletes its file on age — coherent with `statestore`.

New tests:
- `test_unparseable_updated_at_coerces_to_zero_and_never_raises` — `"abc"` and
  `None`: no exception, `updated_at == 0`.
- `test_absent_updated_at_coerces_to_zero` — key absent: `updated_at == 0`.
- `test_numeric_string_updated_at_is_still_honoured` — `"100"`: coercion is not
  over-narrowed, `updated_at == 100`.

### Re-run mutation battery (corrected table)

Full battery re-applied one at a time against the revised module, each reverted
to a byte-identical snapshot (`diff` clean) before the next.

| # | Mutation | Result | Named test |
|---|----------|--------|------------|
| 1 | `_newest_per_pane` keeps first, not newest (`>` → `<`) | CAUGHT | `test_two_records_on_one_pane_keep_the_newest` |
| 2 | Delete empty socket/pane guard in `_newest_per_pane` | **CAUGHT** | `test_a_record_without_a_socket_cannot_acquire_a_window_address` |
| 3 | Delete `if pane not in sessions: continue` in `build_rows` | CAUGHT (`KeyError`) | `test_record_whose_pane_is_gone_produces_no_row` |
| 4 | Drop waiting-first term from sort key | CAUGHT | `test_waiting_rows_sort_first_then_newest_first` |
| 5 | Sort by `+row.updated_at` instead of `-row.updated_at` | CAUGHT | `test_waiting_rows_sort_first_then_newest_first` |
| 6 | `titles.get(pane) or pane` → `titles.get(pane, pane)` | **CAUGHT** | `test_present_but_empty_title_falls_back_to_the_pane_id` |
| 7 | Key `_newest_per_pane` by pane alone, ignoring socket | CAUGHT | `test_same_pane_id_on_different_sockets_are_distinct_rows` |
| + | Remove `_as_int`'s `try/except` (bare `int()`) | CAUGHT (`ValueError`) | `test_unparseable_updated_at_coerces_to_zero_and_never_raises` |

Zero survivors. Both previously-surviving mutations are now killed by named
tests. The `_as_int` guard is itself pinned by the eighth row (a bare `int()`
raises on the `"abc"` input and the test catches it).

### Suite

`./run-tests` → `Ran 104 tests` / `OK` (90 existing + 14 model: 9 original + 5
new). Output pristine; no subprocess, filesystem, or sleep in any new test.
