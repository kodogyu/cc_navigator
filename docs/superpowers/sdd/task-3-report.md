# Task 3 report: Atomic state store

Status: DONE

## What I implemented

`src/ccnav/statestore.py`, exactly matching the interface in the brief and plan
section `### Task 3`:

- `MAX_AGE_SECONDS: int = 24 * 60 * 60`
- `is_safe_session_id(session_id: str) -> bool` — a session id is safe iff it is
  non-empty and matches `^[A-Za-z0-9._-]+$`. Per the brief, `".."` is
  deliberately accepted (it matches the regex and yields the harmless filename
  `...json`); no extra hardening was added.
- `write(state_dir, record) -> None` — validates the session id, writes to a
  `tempfile.mkstemp(dir=state_dir, prefix=".tmp-")` file in the *same*
  directory, `fsync`s the file, then `os.replace`s it over
  `<session_id>.json`. On any exception (including `KeyboardInterrupt`, via
  `except BaseException`), the temp file is removed and the exception is
  re-raised.
- `read_all(state_dir) -> List[Dict]` — returns `[]` if the directory doesn't
  exist; otherwise reads `*.json` in sorted (filename) order, skipping any
  file that fails to parse (`ValueError`/`OSError`).
- `prune(state_dir, live_panes, now=None) -> int` — deletes a state file if
  its `(tmux_socket, tmux_pane)` isn't in `live_panes`, if its
  `now - updated_at` exceeds `MAX_AGE_SECONDS`, or if the file doesn't parse.
  Returns the count removed. Defaults `now` to `int(time.time())`.

`tests/test_statestore.py` — the 10 tests specified in the brief, verbatim.

## What I tested and the results

`./run-tests -k StateStoreTest`: 10/10 passing.
`./run-tests` (full suite): 29/29 passing (7 paths + 12 hookstate + 10
statestore), matching the brief's expected count exactly.

Extra check beyond the brief: ran the suite again with
`PYTHONWARNINGS=error::ResourceWarning` to make sure no unclosed file handle
was silently tolerated. Still 29/29, no warnings surfaced.

## TDD Evidence

### RED

Command:
```
./run-tests -k StateStoreTest
```

Output (before `src/ccnav/statestore.py` existed):
```
test_statestore (unittest.loader._FailedTest) ... ERROR

======================================================================
ERROR: test_statestore (unittest.loader._FailedTest)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_statestore
Traceback (most recent call last):
  ...
ImportError: cannot import name 'statestore' from 'ccnav' (/data/playground/cc_navigator/src/ccnav/__init__.py)

----------------------------------------------------------------------
Ran 1 test in 0.000s

FAILED (errors=1)
```

Why expected: `src/ccnav/statestore.py` did not exist yet, so
`from ccnav import statestore` in the test file fails at import time. This is
functionally the same failure the brief predicted
(`ModuleNotFoundError: No module named 'ccnav.statestore'`) — `unittest`'s
loader wraps it as an `ImportError` inside a synthetic `_FailedTest`, which is
the standard shape of this failure under `unittest discover`.

### GREEN

Command:
```
./run-tests -k StateStoreTest
```

Output (after implementation):
```
test_failed_write_leaves_no_partial_file_and_no_temp (test_statestore.StateStoreTest) ... ok
test_prune_removes_malformed_files (test_statestore.StateStoreTest) ... ok
test_prune_removes_records_whose_pane_is_gone (test_statestore.StateStoreTest) ... ok
test_prune_removes_stale_records_even_if_pane_is_live (test_statestore.StateStoreTest) ... ok
test_read_all_on_missing_directory_is_empty (test_statestore.StateStoreTest) ... ok
test_read_all_skips_malformed_files (test_statestore.StateStoreTest) ... ok
test_rejects_unsafe_session_id (test_statestore.StateStoreTest) ... ok
test_safe_session_id_predicate (test_statestore.StateStoreTest) ... ok
test_write_leaves_no_temp_files (test_statestore.StateStoreTest) ... ok
test_write_then_read_round_trips (test_statestore.StateStoreTest) ... ok

----------------------------------------------------------------------
Ran 10 tests in 0.007s

OK
```

Full suite:
```
./run-tests
...
----------------------------------------------------------------------
Ran 29 tests in 0.013s

OK
```

## Mutation Evidence

Each mutation was applied to `src/ccnav/statestore.py` in place, tested with
`./run-tests -k StateStoreTest`, then reverted from a saved copy of the
pre-mutation file (verified with `diff` after each restore, and the working
tree is clean per `git status` after the final revert).

| # | Mutation | Result | Killed by |
|---|----------|--------|-----------|
| 1 | Replace `os.replace(...)` with `shutil.copyfile(...)` + `os.unlink(...)` | **SURVIVED** | none — expected, see note below |
| 2 | Delete the `except BaseException` cleanup block (temp file no longer removed on failure, exception still propagates naturally) | KILLED | `test_failed_write_leaves_no_partial_file_and_no_temp` — asserts the temp dir is empty after a forced `RuntimeError` during `json.dump`; without cleanup a `.tmp-*` file is left behind |
| 3 | Make `is_safe_session_id` return `True` unconditionally | KILLED | `test_safe_session_id_predicate` (direct assertion `assertFalse(is_safe_session_id("a/b"))` fails) and `test_rejects_unsafe_session_id` (now raises `PermissionError` from `os.replace` trying to write outside `state_dir`, instead of the expected `ValueError`) |
| 4 | In `prune`, drop the `or age > MAX_AGE_SECONDS` clause (keep only pane-liveness check) | KILLED | `test_prune_removes_stale_records_even_if_pane_is_live` — expects a stale-but-live-paned record to be removed (`removed == 1`); mutation leaves it in place (`removed == 0`) |
| 5 | In `prune`, drop the malformed-file `unlink`/`removed += 1`/`continue` on `json.loads` failure (fall through, `record` undefined) | KILLED | `test_prune_removes_malformed_files` — expects the broken file to be removed (`removed == 1`, empty dir after); mutation leaves it (`removed == 0`, file survives) |

### Note on mutation 1 (SURVIVED)

This is the finding called out explicitly by the brief as expected: no test
in the suite — and no test *reasonably addable* within the scope of this
task — can observe the loss of atomicity from replacing `os.replace` with
`copyfile` + `unlink`. Doing so would require a concurrent reader actively
polling the file *during* the write window and asserting it never observes a
partial JSON payload — a timing-dependent, flaky-by-nature test that the
brief explicitly says not to invent ("Probably not, and that is fine — say so
in your report rather than inventing a test that cannot observe a race").
`test_write_then_read_round_trips` and `test_write_leaves_no_temp_files` both
still pass under this mutation because in the single-threaded test process
there is no window in which a reader can interleave with the writer — the
mutated code still produces a fully-written, correctly-named file by the time
`read_all` runs. I did not add a test to chase this; it is architecturally
unobservable at the unit level and is exactly the gap the brief predicted.

## Files changed

- `src/ccnav/statestore.py` (new)
- `tests/test_statestore.py` (new)

Commit: `7bea300` — "feat: atomic per-session state store with pruning"

## Self-review

- **Completeness:** All five interface symbols (`MAX_AGE_SECONDS`,
  `is_safe_session_id`, `write`, `read_all`, `prune`) implemented per spec, all
  10 brief-specified tests present and passing, full suite at the exact
  expected count (29).
- **Quality:** Names match the interface exactly. Module docstring and
  function docstrings explain *why* (atomicity, no-`SessionEnd` rationale),
  consistent with the style in `paths.py`/`hookstate.py`. `from __future__
  import annotations` present in the `src/` module; absent in the test file,
  matching the existing pattern.
- **Discipline (YAGNI):** Verified the four explicit "must not add" items are
  absent:
  - No `SessionEnd` branch anywhere (there is no hook-event handling in this
    module at all — that's Task 4's job).
  - No `delete(session_id)` helper — `prune` is the only removal path.
  - No directory `fsync` — only the file handle is `fsync`'d before rename.
  - No extra hardening of `is_safe_session_id` — `".."` still passes, as
    specified.
- **Testing:** Followed TDD strictly — test file written first, RED captured
  (ImportError, functionally equivalent to the brief's predicted
  ModuleNotFoundError), then implementation, then GREEN. Ran the suite an
  extra time with `PYTHONWARNINGS=error::ResourceWarning` to confirm no
  unclosed-file warnings; output stayed pristine. All 5 mutations exercised
  and reverted; working tree confirmed clean (`git status --short` empty
  after `git add` + commit) and `diff` against the pre-mutation copy confirmed
  byte-identical restoration before committing.

## Issues or concerns

None. The one mutation that survived (mutation 1) is the exact case the brief
anticipated and told me to report rather than paper over — reported above.
No other concerns.

---

## Revision after review

The review came back **Needs Revision**. The implementation was accepted as
correct, minimal, and spec-compliant (no `src/` change needed), and my
identification of which test catches mutations 2-5 was confirmed. **But my
claim that mutation 1 is unobservable was wrong, and the reviewer disproved it
empirically.** I am correcting the record here.

### The correction

`os.replace` renames a *fresh* temp file over the target, so the target gets a
**new inode** on every write. `shutil.copyfile` (and any `open(target, "w")`)
opens the *existing* target and truncates it in place, so the inode is
**unchanged**. `path.stat().st_ino` is deterministic, race-free, and
stdlib-only — no threads, no timing. My "no unit test can observe this"
assertion violated this project's own principle (*never trust an API's
self-report* — including my own reasoning). I should have spent five minutes
trying before declaring it impossible.

Empirical measurement, reproduced here and matching the reviewer's numbers
exactly:

```
real impl:            ino1=671320 ino2=671324  differ=True
under mutation 1:     ino1=671324 ino2=671324  differ=False   (test 1 FAILS)
```

Before this revision, the atomic-write property — the entire reason
`statestore.py` exists — had **no regression test**. A contributor
"simplifying" `write()` to `copyfile` (e.g. reasoning about cross-filesystem
safety) would have shipped green. That is now closed.

### Tests added (no `src/` change)

Two tests appended to `tests/test_statestore.py`:

1. **`test_write_replaces_the_file_rather_than_mutating_it`** — writes the same
   `session_id` twice and asserts the target's `st_ino` differs. The first
   write has no pre-existing target, so the inode divergence only appears on
   the second write; the test is written accordingly (write, stat, write,
   stat, `assertNotEqual`).
2. **`test_reader_during_write_never_sees_a_partial_file`** — patches
   `json.dump` with a spy that, *before* delegating to the real `json.dump`,
   reads the target and records what a reader would see at that instant, then
   asserts the sequence is `[None, <complete first record>]`. This observes the
   file exactly at the moment a naive in-place writer would have truncated it,
   with no threads and no timing. Non-vacuous: if the spy never ran the
   observed list would be `[]` and the assertion would fail; the spy is not
   inside a swallowed `except`.

Both pass against the real implementation. Full suite: **31/31**, pristine
(re-verified with `PYTHONWARNINGS=error::ResourceWarning`).

### What each new test actually pins down (no overclaiming)

- **Test 1 kills mutation 1.** Confirmed: under the `copyfile`+`unlink`
  mutation, `test_write_replaces_the_file_rather_than_mutating_it` FAILs
  (same inode on the second write).
- **Test 2 does *not* kill mutation 1**, and passes under it — exactly the case
  the coordinator flagged. Mutation 1 still stages the write through a temp
  file and only copies to the target *after* `json.dump` returns, so at the
  instant the spy runs the target is untouched. Test 2 therefore does not
  distinguish the real impl from mutation 1.
- **What test 2 *does* pin down:** a writer that opens the target directly
  (`open(target, "w")` / no temp staging). To prove test 2 is not merely
  decorative I introduced a sixth mutation — a naive direct-to-target
  `write()` — and test 2 catches it (ERROR: the spy reads a truncated,
  now-invalid-JSON target mid-write). So test 2 is a live regression guard for
  the design-spec §9 reader-safety property, distinct from what test 1 guards.

### Corrected mutation battery (re-run with both new tests present)

| # | Mutation | Result | Killed by |
|---|----------|--------|-----------|
| 1 | `os.replace` -> `shutil.copyfile` + `os.unlink` (in-place, inode preserved) | **KILLED** | `test_write_replaces_the_file_rather_than_mutating_it` |
| 2 | Delete the `except BaseException` cleanup block | KILLED | `test_failed_write_leaves_no_partial_file_and_no_temp` |
| 3 | `is_safe_session_id` returns `True` unconditionally | KILLED | `test_safe_session_id_predicate` + `test_rejects_unsafe_session_id` |
| 4 | `prune`: drop the `or age > MAX_AGE_SECONDS` clause | KILLED | `test_prune_removes_stale_records_even_if_pane_is_live` |
| 5 | `prune`: drop the malformed-file `unlink` | KILLED | `test_prune_removes_malformed_files` |
| 6 (supplementary) | naive direct-to-target `write()` (no temp staging) | KILLED | `test_reader_during_write_never_sees_a_partial_file` (ERROR) — also fails test 1 |

**6/6 killed. The prior SURVIVED entry for mutation 1 is withdrawn; it is now
killed by a named test.**

### Verification

- `git diff --stat HEAD -- src/ccnav/statestore.py` is empty — `src/` was not
  touched, only `tests/test_statestore.py`.
- Each mutation was applied via script, tested, and reverted from a saved
  pristine copy; `diff` confirms the working `src/` is byte-identical to the
  committed version.
- Full suite 31/31, output pristine under `PYTHONWARNINGS=error::ResourceWarning`.

### Calibration note to self

Taken. "No unit test can observe this" is a strong claim and I made it without
trying. The reviewer falsified it with one `stat()` call. Going forward, before
writing that something is untestable I will spend the five minutes to attempt a
test first — the burden of proof is on the "impossible" claim, not the other
way around.
