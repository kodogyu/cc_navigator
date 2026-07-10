# Task 8 brief: The model

BASE commit: `50bc6c2` (feat/cc-navigator) — confirm with `git log -1`.
Plan: `docs/superpowers/plans/2026-07-10-cc-navigator.md`, section `### Task 8`
(lines 1403-1609). The plan is authoritative if it disagrees with this brief.

## Global Constraints (bind every task)

- **Interpreter is `/usr/bin/python3` (3.8.10).** Never invoke bare `python3`.
- **Zero third-party dependencies.** Stdlib `unittest` only.
- Python 3.8: no `match`, no `X | Y` runtime annotations. `src/` modules start with
  `from __future__ import annotations`. Test files do not.
- **Never trust an API's self-report** — including your own tests. Break them.
- Outer window title `ccnav:<tmux_session_name>` is the **address**. tmux's
  `pane_title` is **display only** and is never parsed or matched.
- Test command: `./run-tests` (it sets `PYTHONDONTWRITEBYTECODE=1`; leave that).

## Context

This module is pure. It joins two things:

- the per-session JSON records written by the hook (`statestore.read_all`), and
- what tmux currently reports (`tmuxctl.sessions_by_pane`, `tmuxctl.titles_by_pane`),

into the list of rows the UI renders.

The join rule is the design's liveness mechanism: **a row exists if and only if its
state file's pane is currently live in tmux.** That is why there is no `SessionEnd`
hook — a session that vanished from tmux vanishes from the model on the next tick.
`live_pane_keys` feeds `statestore.prune`, which then deletes the orphaned files.

Two records can name the same `(socket, pane)` if a pane was reused by a new Claude
session; the newest `updated_at` wins. The same pane id on two different tmux
sockets is two different panes.

Sorting: waiting rows first, then most recently updated first. That is the whole
UX — the thing demanding your attention is at the top.

Tasks 1-7 are committed; HEAD is `50bc6c2`. `hookstate.WAITING` already exists.

## Files

- Create: `src/ccnav/model.py`
- Test: `tests/test_model.py`

## Interface

- `model.Row` — frozen dataclass: `session_id, socket, pane, tmux_session, title,
  state, reason, message, cwd, updated_at`, plus properties `waiting -> bool` and
  `window_title -> str` returning `"ccnav:" + tmux_session`.
- `model.build_rows(records, sessions_by_socket, titles_by_socket) -> List[Row]`
- `model.live_pane_keys(sessions_by_socket) -> Set[Tuple[str, str]]`

## This is a TDD task.

### Step 1: Write the failing test

Use `tests/test_model.py` exactly as the plan gives it at lines 1420-1503 — nine
tests across two classes.

### Step 2: Run it, capture the failure

`./run-tests -k BuildRowsTest -k LivePaneKeysTest`
Expected: `ModuleNotFoundError: No module named 'ccnav.model'`. RED evidence.

### Step 3: Implement

Use the plan's code at lines 1514-1596 verbatim.

### Step 4: Green

Same `-k` command → `Ran 9 tests` / `OK`.
Then `./run-tests` → `Ran 99 tests` / `OK` (90 existing + 9). Output pristine.

### Step 5: Mutation testing — mandatory, and I expect two survivors

Break the implementation, confirm a **named** test fails, restore. At minimum:

1. `_newest_per_pane` keeps the **first** record per pane instead of the newest
   (change `>` to `<`).
2. Delete the `if not key[0] or not key[1]: continue` guard in `_newest_per_pane`.
3. Delete the `if pane not in sessions: continue` guard in `build_rows`.
4. Drop the waiting-first term from the sort key.
5. Sort by `+row.updated_at` instead of `-row.updated_at`.
6. Change `titles.get(pane) or pane` to `titles.get(pane, pane)`.
7. Key `_newest_per_pane` by pane alone, ignoring the socket.

**My predictions, which I want you to check rather than accept:**

- **Mutation 2 survives.** Reason it through: if the socket is `""`, what does
  `sessions_by_socket.get("", {})` return, and what does the *next* guard then do?
  If the record is dropped anyway, by a different guard, then
  `test_records_without_socket_or_pane_are_dropped` proves nothing about the guard
  it appears to test.
- **Mutation 6 survives.** No test supplies a title that is present but empty.

If I am right, **name the missing test in your report and do not add it.** If I am
wrong, show me the named test that catches it — I would rather be corrected than
agreed with. The Task 5 and Task 7 implementers each found a real survivor by
checking instead of assuming; both times it exposed a hole in the plan.

### Step 6: A fact from Task 5 that this task must account for

tmux 3.0a reports a plain shell's `#{pane_title}` as the **hostname**, not the empty
string. The plan assumed empty. Consequences to think through and address in your
report:

- Does a pane with no Claude Code session ever produce a row? Trace it: no hook ran,
  so no state file, so no record, so no row. Confirm that reasoning holds in
  `build_rows` and say so explicitly.
- `test_missing_title_falls_back_to_the_pane_id` therefore tests a case that may
  never occur in production, since tmux will supply *something*. Is the fallback
  dead code? Say what you think, and what would have to be true for `titles` to
  lack the pane.
- Immediately after `SessionStart`, before Claude Code emits its OSC title, `title`
  will be the hostname. The UI (Task 9) would show a hostname as the row's primary
  line. **Do not fix this here.** Flag it in your report so I can decide whether
  Task 9 needs a fallback.

### Step 7: One more thing to check, and only report

`build_rows` calls `int(rec.get("updated_at", 0))`. State files are written by our
own hook, which always writes an int — but `read_all` will happily return a record
someone hand-edited. What happens to `build_rows` if `updated_at` is `"abc"`, or
`None`, or absent? Does it raise? `statestore.prune` guards this with a
`try/except`; `build_rows` does not.

Determine the answer empirically. If it raises, that exception lands in the GTK main
loop on the next tick. **Report it. Do not fix it.** I will decide.

### Step 8: Commit

```bash
git add src/ccnav/model.py tests/test_model.py
git commit -m "feat: join state files and live tmux panes into rows"
```

## Notes and traps

- `Row` is frozen. Do not add setters, `__post_init__` validation, or an `update()`.
- `window_title` is a property, not a stored field. It must be exactly
  `"ccnav:" + tmux_session` — Task 7 compares it with `===` against the real X11
  title, so a stray space or a different prefix breaks the jump silently.
- Do not sort inside `_newest_per_pane`. One sort, at the end of `build_rows`.
- Do not add filtering by age; `statestore.prune` owns staleness.
- Do not import `tmuxctl` or `statestore` here. This module takes plain dicts so it
  stays pure and instant to test.
- Work from `/data/playground/cc_navigator`.
