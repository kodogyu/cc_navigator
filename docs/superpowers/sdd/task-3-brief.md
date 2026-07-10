# Task 3 brief: Atomic state store

BASE commit: `2b5c4d4` (feat/cc-navigator)
Plan: `docs/superpowers/plans/2026-07-10-cc-navigator.md`, section `### Task 3`
(lines 277-497). Read that section — the text below repeats it, but the plan is
authoritative if they ever disagree.

> Heads-up: this repository's history was rewritten today to strip real project
> names. If you have any stale SHAs in mind, ignore them. `git log` is the truth.

## Global Constraints (bind every task)

- **Interpreter is `/usr/bin/python3` (3.8.10).** `which python3` is Anaconda and
  has no `gi`. Never invoke bare `python3`. `./run-tests` already handles this.
- **Zero third-party dependencies.** `pytest` is not installed. Use stdlib `unittest`.
- Python 3.8: no `match`, no `X | Y` runtime annotations. Every module under `src/`
  starts with `from __future__ import annotations`. Test files do not — follow the
  existing pattern in `tests/test_paths.py` and `tests/test_hookstate.py`.
- **Never trust an API's self-report.**
- **The hook must never block or fail Claude Code.**
- Test command: `./run-tests` (focus: `./run-tests -k StateStoreTest`).

## Context

cc_navigator is an always-on-top GTK window listing every live Claude Code
session. A hook shim writes one JSON file per session into a state directory; the
UI polls that directory. This task is that directory's read/write layer.

Two properties matter and both are load-bearing:

1. **Atomicity.** The UI reads while hooks write. A reader must never observe a
   half-written file. Every write goes to a temp file in the *same directory*
   (so `os.replace` is a same-filesystem rename) and is then renamed over the
   target.
2. **Self-healing liveness.** `prune()` deletes records whose tmux pane is gone,
   whose `updated_at` is older than `MAX_AGE_SECONDS`, or that are unparseable.
   This is *why the design has no `SessionEnd` hook*: a session that has vanished
   from tmux vanishes from the model on the next prune. Do not add a `SessionEnd`
   branch anywhere.

Already committed and available to you:
- `src/ccnav/paths.py` — `state_dir()`, `ensure_state_dir()` (mode 0700)
- `src/ccnav/hookstate.py` — `classify()`, `WAITING`, `WORKING`

Task 4 (the hook shim) composes `hookstate.classify` with `statestore.write`.
Task 8 (the model) calls `read_all` and `prune`.

## Files

- Create: `src/ccnav/statestore.py`
- Test: `tests/test_statestore.py`

## Interface

- Consumes: nothing.
- Produces:
  - `statestore.MAX_AGE_SECONDS: int`
  - `statestore.is_safe_session_id(session_id: str) -> bool`
  - `statestore.write(state_dir: pathlib.Path, record: Dict[str, object]) -> None`
  - `statestore.read_all(state_dir: pathlib.Path) -> List[Dict[str, object]]`
  - `statestore.prune(state_dir: pathlib.Path, live_panes: Set[Tuple[str, str]], now: Optional[int] = None) -> int`

`live_panes` is a set of `(tmux_socket, tmux_pane)` tuples. `prune` returns how
many files it removed.

## This is a TDD task. Follow the steps in order.

### Step 1: Write the failing test

`tests/test_statestore.py`:

```python
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from ccnav import statestore


def record(session_id="s1", socket="/tmp/tmux-1000/default", pane="%1", updated_at=100):
    return {
        "session_id": session_id,
        "cwd": "/proj",
        "tmux_socket": socket,
        "tmux_pane": pane,
        "state": "waiting",
        "reason": "idle",
        "message": "",
        "updated_at": updated_at,
    }


class StateStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_write_then_read_round_trips(self):
        statestore.write(self.dir, record())
        self.assertEqual(statestore.read_all(self.dir), [record()])

    def test_write_leaves_no_temp_files(self):
        statestore.write(self.dir, record())
        leftovers = [p.name for p in self.dir.iterdir() if p.name.startswith(".tmp-")]
        self.assertEqual(leftovers, [])

    def test_failed_write_leaves_no_partial_file_and_no_temp(self):
        with mock.patch("json.dump", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                statestore.write(self.dir, record())
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_rejects_unsafe_session_id(self):
        with self.assertRaises(ValueError):
            statestore.write(self.dir, record(session_id="../../etc/passwd"))

    def test_safe_session_id_predicate(self):
        self.assertTrue(statestore.is_safe_session_id("11111111-2222-3333"))
        self.assertFalse(statestore.is_safe_session_id("a/b"))
        self.assertFalse(statestore.is_safe_session_id(""))

    def test_read_all_skips_malformed_files(self):
        statestore.write(self.dir, record())
        (self.dir / "broken.json").write_text("{not json")
        self.assertEqual(statestore.read_all(self.dir), [record()])

    def test_read_all_on_missing_directory_is_empty(self):
        self.assertEqual(statestore.read_all(self.dir / "nope"), [])

    def test_prune_removes_records_whose_pane_is_gone(self):
        statestore.write(self.dir, record(session_id="alive", pane="%1"))
        statestore.write(self.dir, record(session_id="dead", pane="%9"))
        removed = statestore.prune(
            self.dir, {("/tmp/tmux-1000/default", "%1")}, now=100
        )
        self.assertEqual(removed, 1)
        names = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(names, ["alive.json"])

    def test_prune_removes_stale_records_even_if_pane_is_live(self):
        statestore.write(self.dir, record(session_id="old", updated_at=0))
        live = {("/tmp/tmux-1000/default", "%1")}
        removed = statestore.prune(
            self.dir, live, now=statestore.MAX_AGE_SECONDS + 1
        )
        self.assertEqual(removed, 1)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_prune_removes_malformed_files(self):
        (self.dir / "broken.json").write_text("{not json")
        self.assertEqual(statestore.prune(self.dir, set(), now=100), 1)
        self.assertEqual(list(self.dir.iterdir()), [])
```

### Step 2: Run the test to verify it fails

Run: `./run-tests -k StateStoreTest`
Expected: FAIL with `ModuleNotFoundError: No module named 'ccnav.statestore'`

Capture the output — it is your RED evidence.

### Step 3: Write the minimal implementation

`src/ccnav/statestore.py`:

```python
"""Atomic reads and writes of the per-session state files.

A reader must never observe a half-written file, so every write goes to a
temp file in the same directory and is then renamed over the target.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
import time
from typing import Dict, List, Optional, Set, Tuple

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")

MAX_AGE_SECONDS = 24 * 60 * 60


def is_safe_session_id(session_id: str) -> bool:
    """Session ids become filenames, so reject anything with a path in it."""
    return bool(session_id) and bool(_SAFE_ID.match(session_id))


def write(state_dir: pathlib.Path, record: Dict[str, object]) -> None:
    session_id = str(record["session_id"])
    if not is_safe_session_id(session_id):
        raise ValueError("unsafe session id: %r" % session_id)

    handle_fd, tmp_path = tempfile.mkstemp(dir=str(state_dir), prefix=".tmp-")
    try:
        with os.fdopen(handle_fd, "w") as handle:
            json.dump(record, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, str(state_dir / (session_id + ".json")))
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def read_all(state_dir: pathlib.Path) -> List[Dict[str, object]]:
    records = []  # type: List[Dict[str, object]]
    if not state_dir.is_dir():
        return records
    for path in sorted(state_dir.glob("*.json")):
        try:
            records.append(json.loads(path.read_text()))
        except (ValueError, OSError):
            continue
    return records


def prune(
    state_dir: pathlib.Path,
    live_panes: Set[Tuple[str, str]],
    now: Optional[int] = None,
) -> int:
    """Delete state files whose pane is gone, that are stale, or that are junk.

    This is what makes a SessionEnd hook unnecessary: a session that is gone
    from tmux is gone from the model.
    """
    if now is None:
        now = int(time.time())
    if not state_dir.is_dir():
        return 0

    removed = 0
    for path in sorted(state_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (ValueError, OSError):
            path.unlink(missing_ok=True)
            removed += 1
            continue

        key = (str(record.get("tmux_socket") or ""), str(record.get("tmux_pane") or ""))
        try:
            age = now - int(record.get("updated_at", 0))
        except (TypeError, ValueError):
            age = MAX_AGE_SECONDS + 1

        if key not in live_panes or age > MAX_AGE_SECONDS:
            path.unlink(missing_ok=True)
            removed += 1
    return removed
```

### Step 4: Run the test to verify it passes

Run: `./run-tests -k StateStoreTest` → expect `Ran 10 tests` / `OK`
Then the full suite once: `./run-tests` → expect `Ran 29 tests` / `OK`
(7 paths + 12 hookstate + 10 here). Output must be pristine: no warnings, no
stray prints, no `ResourceWarning` about unclosed files.

### Step 5: Prove your tests are not vacuous

Before committing, break the implementation and confirm a *named* test fails, then
restore. At minimum:

1. Replace `os.replace(...)` with `shutil.copyfile` + `os.unlink` — does anything
   catch the loss of atomicity? (Probably not, and that is fine — say so in your
   report rather than inventing a test that cannot observe a race.)
2. Delete the `except BaseException` cleanup block.
3. Make `is_safe_session_id` return `True` unconditionally.
4. In `prune`, drop the `or age > MAX_AGE_SECONDS` clause.
5. In `prune`, drop the malformed-file `unlink`.

Record which test caught each. If a mutation survives, that is a finding: name the
missing test in your report. Do not add tests beyond the brief to chase them
unless the gap is real — report it instead.

### Step 6: Commit

```bash
git add src/ccnav/statestore.py tests/test_statestore.py
git commit -m "feat: atomic per-session state store with pruning"
```

## Notes and traps

- `pathlib.Path.unlink(missing_ok=True)` requires Python 3.8. That is exactly what
  we have. Do not replace it with a `try/except FileNotFoundError`.
- `except BaseException` is deliberate, not a typo. A `KeyboardInterrupt` in the
  middle of a write must not strand a `.tmp-` file. Keep it, and keep the bare
  `raise`.
- The temp file **must** be created in `state_dir`, not in `/tmp`. `os.replace`
  across filesystems raises `OSError`.
- `is_safe_session_id("..")` returns `True` (it matches `[A-Za-z0-9._-]+`), which
  yields the filename `...json` inside `state_dir` — not a traversal. This is
  acceptable and in scope. Do not harden it further; that is scope creep.
- `read_all` returns records in filename order because `glob` is sorted. Do not
  sort by `updated_at` here; ordering is the UI's problem (Task 8/9).
- Do not `fsync` the directory. The plan does not, and a lost state file after a
  power cut is harmless — the next hook rewrites it.
- Do not add a `SessionEnd` branch or a `delete(session_id)` helper. `prune` is the
  only removal path. YAGNI.
- Work from `/data/playground/cc_navigator`.
