# cc_navigator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An always-on-top window listing every interactive Claude Code session, highlighting the ones waiting for input, with one-click jump to the session's tmux pane and a one-line input box.

**Architecture:** A hook shim writes one atomic state file per session, carrying `$TMUX_PANE` and `$TMUX`. A GTK3 overlay joins those files with live `tmux list-panes` output to build rows. Jump selects the tmux pane, then activates the gnome-terminal window whose title tmux set to `ccnav:<session>`, then verifies through `xprop` that focus actually moved. The hook never depends on the UI being alive.

**Tech Stack:** Python 3.8.10 (`/usr/bin/python3` only), PyGObject/GTK 3.24, `Gio.FileMonitor`, `tmux` 3.0a, `gdbus` → `org.gnome.Shell.Eval`, `xprop`. Tests: stdlib `unittest`.

Spec: `docs/superpowers/specs/2026-07-10-cc-navigator-design.md`

## Global Constraints

- **Interpreter is `/usr/bin/python3` (3.8.10).** `which python3` is Anaconda and has no `gi`. Never invoke bare `python3`.
- **Zero third-party dependencies.** `pytest` and `pyinotify` are not installed. Use stdlib `unittest` and `Gio.FileMonitor`.
- Python 3.8: no `match`, no `X | Y` runtime annotations. Every module starts with `from __future__ import annotations`.
- **Never trust an API's self-report.** `gdbus` exits 0 even when Eval returns `(false, ...)`. `win.activate(0)` reports success while doing nothing across workspaces. Every focus action is verified by reading `_NET_ACTIVE_WINDOW` through `xprop`.
- **The hook must never block or fail Claude Code.** It exits 0 on every path.
- User text sent to tmux is always passed as one `argv` element after `-l --`. It is never interpolated into a shell string.
- Outer window title `ccnav:<tmux_session_name>` is the **address** and is compared with `===`. tmux's `pane_title` is **display only** and is never parsed.
- Test command: `./run-tests`

---

### Task 1: Scaffolding and state directory

**Files:**
- Create: `run-tests`
- Create: `src/ccnav/__init__.py`
- Create: `src/ccnav/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `paths.state_dir() -> pathlib.Path`, `paths.ensure_state_dir() -> pathlib.Path`.

- [ ] **Step 1: Create the test runner**

`run-tests`:

```sh
#!/bin/sh
# cc_navigator test runner. /usr/bin/python3 is mandatory: `which python3` is
# Anaconda and has no PyGObject.
set -e
exec env PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -v "$@"
```

```bash
chmod +x run-tests
mkdir -p src/ccnav tests
touch src/ccnav/__init__.py
```

- [ ] **Step 2: Write the failing test**

`tests/test_paths.py`:

```python
import os
import pathlib
import unittest
from unittest import mock

from ccnav import paths


class StateDirTest(unittest.TestCase):
    def test_uses_xdg_runtime_dir_when_set(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000"}):
            self.assertEqual(
                paths.state_dir(), pathlib.Path("/run/user/1000/cc-navigator")
            )

    def test_falls_back_to_uid_scoped_tmp(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                paths.state_dir(), pathlib.Path("/tmp/cc-navigator-%d" % os.getuid())
            )

    def test_empty_xdg_runtime_dir_is_treated_as_unset(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}):
            self.assertEqual(
                paths.state_dir(), pathlib.Path("/tmp/cc-navigator-%d" % os.getuid())
            )
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./run-tests -k StateDirTest`
Expected: FAIL with `ModuleNotFoundError: No module named 'ccnav.paths'`

- [ ] **Step 4: Write minimal implementation**

`src/ccnav/paths.py`:

```python
"""Where cc_navigator keeps its per-session state files."""
from __future__ import annotations

import os
import pathlib


def state_dir() -> pathlib.Path:
    """Directory holding one JSON file per live Claude session.

    XDG_RUNTIME_DIR is tmpfs and is wiped at logout, which is exactly the
    lifetime we want. Fall back to a uid-scoped /tmp path when it is unset.
    """
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return pathlib.Path(base) / "cc-navigator"
    return pathlib.Path("/tmp/cc-navigator-%d" % os.getuid())


def ensure_state_dir() -> pathlib.Path:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(str(directory), 0o700)
    return directory
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./run-tests -k StateDirTest`
Expected: `Ran 3 tests` / `OK`

- [ ] **Step 6: Commit**

```bash
git add run-tests src/ccnav/__init__.py src/ccnav/paths.py tests/test_paths.py
git commit -m "feat: state directory resolution and test runner"
```

---

### Task 2: Hook event classification

**Files:**
- Create: `src/ccnav/hookstate.py`
- Test: `tests/test_hookstate.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `hookstate.WAITING: str`, `hookstate.WORKING: str`, `hookstate.classify(payload: Dict[str, object]) -> Optional[Tuple[str, str]]` returning `(state, reason)` or `None` when the event carries no state change.

The field names come from real hook payloads: `hook_event_name`, `notification_type`, `tool_name`.

- [ ] **Step 1: Write the failing test**

`tests/test_hookstate.py`:

```python
import unittest

from ccnav import hookstate


class ClassifyTest(unittest.TestCase):
    def test_session_start_is_working(self):
        result = hookstate.classify({"hook_event_name": "SessionStart"})
        self.assertEqual(result, (hookstate.WORKING, ""))

    def test_user_prompt_submit_is_working(self):
        result = hookstate.classify({"hook_event_name": "UserPromptSubmit"})
        self.assertEqual(result, (hookstate.WORKING, ""))

    def test_notification_carries_its_type_as_reason(self):
        result = hookstate.classify(
            {"hook_event_name": "Notification", "notification_type": "permission_prompt"}
        )
        self.assertEqual(result, (hookstate.WAITING, "permission_prompt"))

    def test_notification_without_a_type_still_waits(self):
        result = hookstate.classify({"hook_event_name": "Notification"})
        self.assertEqual(result, (hookstate.WAITING, "notification"))

    def test_ask_user_question_waits(self):
        result = hookstate.classify(
            {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"}
        )
        self.assertEqual(result, (hookstate.WAITING, "question"))

    def test_exit_plan_mode_waits(self):
        result = hookstate.classify(
            {"hook_event_name": "PreToolUse", "tool_name": "ExitPlanMode"}
        )
        self.assertEqual(result, (hookstate.WAITING, "plan"))

    def test_other_tools_are_ignored(self):
        result = hookstate.classify(
            {"hook_event_name": "PreToolUse", "tool_name": "Bash"}
        )
        self.assertIsNone(result)

    def test_stop_is_idle_waiting(self):
        result = hookstate.classify({"hook_event_name": "Stop"})
        self.assertEqual(result, (hookstate.WAITING, "idle"))

    def test_subagent_stop_is_ignored(self):
        self.assertIsNone(hookstate.classify({"hook_event_name": "SubagentStop"}))

    def test_unknown_event_is_ignored(self):
        self.assertIsNone(hookstate.classify({"hook_event_name": "Nonsense"}))

    def test_missing_event_name_is_ignored(self):
        self.assertIsNone(hookstate.classify({}))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./run-tests -k ClassifyTest`
Expected: FAIL with `ModuleNotFoundError: No module named 'ccnav.hookstate'`

- [ ] **Step 3: Write minimal implementation**

`src/ccnav/hookstate.py`:

```python
"""Pure mapping from a Claude Code hook event to a session state.

Kept free of I/O so the whole state machine is testable from fixtures.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

WAITING = "waiting"
WORKING = "working"

# PreToolUse fires for every tool. Only these two mean "the user must answer".
_WAITING_TOOLS = {"AskUserQuestion": "question", "ExitPlanMode": "plan"}


def classify(payload: Dict[str, object]) -> Optional[Tuple[str, str]]:
    """Return (state, reason), or None when the event carries no state change."""
    event = payload.get("hook_event_name")

    if event in ("SessionStart", "UserPromptSubmit"):
        return (WORKING, "")

    if event == "Notification":
        # Empty matcher: every notification_type counts, including
        # elicitation_dialog, which other tools drop.
        reason = payload.get("notification_type") or "notification"
        return (WAITING, str(reason))

    if event == "PreToolUse":
        tool = str(payload.get("tool_name") or "")
        if tool in _WAITING_TOOLS:
            return (WAITING, _WAITING_TOOLS[tool])
        return None

    if event == "Stop":
        return (WAITING, "idle")

    # SubagentStop fires constantly and never means the session wants input.
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./run-tests -k ClassifyTest`
Expected: `Ran 11 tests` / `OK`

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/hookstate.py tests/test_hookstate.py
git commit -m "feat: classify hook events into session states"
```

---

### Task 3: Atomic state store

**Files:**
- Create: `src/ccnav/statestore.py`
- Test: `tests/test_statestore.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `statestore.is_safe_session_id(session_id: str) -> bool`
  - `statestore.write(state_dir: pathlib.Path, record: Dict[str, object]) -> None`
  - `statestore.read_all(state_dir: pathlib.Path) -> List[Dict[str, object]]`
  - `statestore.prune(state_dir: pathlib.Path, live_panes: Set[Tuple[str, str]], now: Optional[int] = None) -> int`
  - `statestore.MAX_AGE_SECONDS: int`

`live_panes` is a set of `(tmux_socket, tmux_pane)` tuples.

- [ ] **Step 1: Write the failing test**

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

- [ ] **Step 2: Run test to verify it fails**

Run: `./run-tests -k StateStoreTest`
Expected: FAIL with `ModuleNotFoundError: No module named 'ccnav.statestore'`

- [ ] **Step 3: Write minimal implementation**

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

- [ ] **Step 4: Run test to verify it passes**

Run: `./run-tests -k StateStoreTest`
Expected: `Ran 10 tests` / `OK`

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/statestore.py tests/test_statestore.py
git commit -m "feat: atomic per-session state store with pruning"
```

---

### Task 4: Hook shim entry point

**Files:**
- Create: `src/ccnav/hook.py`
- Create: `bin/cc-navigator-hook`
- Test: `tests/test_hook.py`

**Interfaces:**
- Consumes: `hookstate.classify`, `statestore.write`, `statestore.is_safe_session_id`, `paths.ensure_state_dir`.
- Produces:
  - `hook.tmux_socket_from_env(env: Mapping[str, str]) -> Optional[str]`
  - `hook.build_record(payload: Dict[str, object], env: Mapping[str, str], now: int) -> Optional[Dict[str, object]]`
  - `hook.main() -> int` (always returns 0)
  - `hook.MESSAGE_LIMIT: int`

`$TMUX` looks like `/tmp/tmux-1000/default,4039841,0`; the socket path is the first comma-separated field.

- [ ] **Step 1: Write the failing test**

`tests/test_hook.py`:

```python
import unittest

from ccnav import hook, hookstate


ENV = {"TMUX": "/tmp/tmux-1000/default,4039841,0", "TMUX_PANE": "%12"}

PAYLOAD = {
    "hook_event_name": "Notification",
    "notification_type": "permission_prompt",
    "message": "Allow Bash command: npm test?",
    "session_id": "11111111-2222-3333-4444-555555555555",
    "cwd": "/data/projects/demo_project",
}


class TmuxSocketTest(unittest.TestCase):
    def test_takes_the_first_comma_field(self):
        self.assertEqual(
            hook.tmux_socket_from_env(ENV), "/tmp/tmux-1000/default"
        )

    def test_missing_tmux_is_none(self):
        self.assertIsNone(hook.tmux_socket_from_env({}))

    def test_empty_tmux_is_none(self):
        self.assertIsNone(hook.tmux_socket_from_env({"TMUX": ""}))


class BuildRecordTest(unittest.TestCase):
    def test_builds_a_full_record(self):
        result = hook.build_record(PAYLOAD, ENV, now=1783665780)
        self.assertEqual(
            result,
            {
                "session_id": "11111111-2222-3333-4444-555555555555",
                "cwd": "/data/projects/demo_project",
                "tmux_socket": "/tmp/tmux-1000/default",
                "tmux_pane": "%12",
                "state": hookstate.WAITING,
                "reason": "permission_prompt",
                "message": "Allow Bash command: npm test?",
                "updated_at": 1783665780,
            },
        )

    def test_outside_tmux_returns_none(self):
        self.assertIsNone(hook.build_record(PAYLOAD, {}, now=1))

    def test_tmux_without_pane_returns_none(self):
        env = {"TMUX": ENV["TMUX"]}
        self.assertIsNone(hook.build_record(PAYLOAD, env, now=1))

    def test_ignored_event_returns_none(self):
        payload = dict(PAYLOAD, hook_event_name="SubagentStop")
        self.assertIsNone(hook.build_record(payload, ENV, now=1))

    def test_missing_session_id_returns_none(self):
        payload = dict(PAYLOAD)
        del payload["session_id"]
        self.assertIsNone(hook.build_record(payload, ENV, now=1))

    def test_unsafe_session_id_returns_none(self):
        payload = dict(PAYLOAD, session_id="../escape")
        self.assertIsNone(hook.build_record(payload, ENV, now=1))

    def test_long_message_is_truncated(self):
        payload = dict(PAYLOAD, message="x" * 5000)
        result = hook.build_record(payload, ENV, now=1)
        self.assertEqual(len(result["message"]), hook.MESSAGE_LIMIT)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./run-tests -k TmuxSocketTest -k BuildRecordTest`
Expected: FAIL with `ModuleNotFoundError: No module named 'ccnav.hook'`

- [ ] **Step 3: Write minimal implementation**

`src/ccnav/hook.py`:

```python
"""Entry point invoked by Claude Code hooks.

Contract: write one state file, exit 0. Never block, never raise, never make
Claude Code wait on anything. cc_navigator not running is not an error.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, Mapping, Optional

from . import hookstate, paths, statestore

MESSAGE_LIMIT = 200


def tmux_socket_from_env(env: Mapping[str, str]) -> Optional[str]:
    """$TMUX is "<socket path>,<server pid>,<session id>"."""
    raw = env.get("TMUX")
    if not raw:
        return None
    return raw.split(",")[0] or None


def build_record(
    payload: Dict[str, object], env: Mapping[str, str], now: int
) -> Optional[Dict[str, object]]:
    pane = env.get("TMUX_PANE")
    socket = tmux_socket_from_env(env)
    if not pane or not socket:
        return None  # not in tmux: the session can never be addressed

    session_id = str(payload.get("session_id") or "")
    if not statestore.is_safe_session_id(session_id):
        return None

    classified = hookstate.classify(payload)
    if classified is None:
        return None
    state, reason = classified

    return {
        "session_id": session_id,
        "cwd": str(payload.get("cwd") or ""),
        "tmux_socket": socket,
        "tmux_pane": pane,
        "state": state,
        "reason": reason,
        "message": str(payload.get("message") or "")[:MESSAGE_LIMIT],
        "updated_at": now,
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0

    record = build_record(payload, os.environ, int(time.time()))
    if record is None:
        return 0

    try:
        statestore.write(paths.ensure_state_dir(), record)
    except Exception:
        pass  # a broken navigator must never break Claude Code
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`bin/cc-navigator-hook`:

```sh
#!/bin/sh
# Claude Code hook entry point.
# No `exec`: we want to swallow every failure and exit 0 unconditionally.
here=$(cd "$(dirname "$0")" && pwd)
PYTHONPATH="$here/../src${PYTHONPATH:+:$PYTHONPATH}" \
    /usr/bin/python3 -m ccnav.hook 2>/dev/null
exit 0
```

```bash
chmod +x bin/cc-navigator-hook
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./run-tests -k TmuxSocketTest -k BuildRecordTest`
Expected: `Ran 10 tests` / `OK`

- [ ] **Step 5: Verify the shim end-to-end by hand**

```bash
mkdir -p /tmp/ccnav-manual
XDG_RUNTIME_DIR=/tmp/ccnav-manual \
TMUX=/tmp/tmux-1000/default,1,0 TMUX_PANE=%12 \
  printf '%s' '{"hook_event_name":"Stop","session_id":"abc-123","cwd":"/proj"}' \
  | XDG_RUNTIME_DIR=/tmp/ccnav-manual TMUX=/tmp/tmux-1000/default,1,0 TMUX_PANE=%12 \
    ./bin/cc-navigator-hook
cat /tmp/ccnav-manual/cc-navigator/abc-123.json; echo
rm -rf /tmp/ccnav-manual
```

Expected: a JSON object with `"state": "waiting"`, `"reason": "idle"`, `"tmux_pane": "%12"`.

- [ ] **Step 6: Verify it exits 0 with garbage input**

```bash
printf 'not json' | ./bin/cc-navigator-hook; echo "exit=$?"
printf '{}' | ./bin/cc-navigator-hook; echo "exit=$?"
```

Expected: `exit=0` both times.

- [ ] **Step 7: Commit**

```bash
git add src/ccnav/hook.py bin/cc-navigator-hook tests/test_hook.py
git commit -m "feat: hook shim writes session state and always exits 0"
```

---

### Task 5: tmux queries

**Files:**
- Create: `src/ccnav/proc.py`
- Create: `src/ccnav/tmuxctl.py`
- Test: `tests/test_tmuxctl_query.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `proc.Runner` — the type alias `Callable[[Sequence[str]], Tuple[int, str]]`, returning `(returncode, stdout)`.
  - `proc.run_command(argv: Sequence[str]) -> Tuple[int, str]` — the only place `subprocess` is called. Task 7 reuses it.
  - `tmuxctl.parse_kv_lines(text: str) -> Dict[str, str]`
  - `tmuxctl.list_argv(socket: str, fmt: str) -> List[str]`
  - `tmuxctl.sessions_by_pane(socket: str, run=...) -> Dict[str, str]`
  - `tmuxctl.titles_by_pane(socket: str, run=...) -> Dict[str, str]`

`run` is injected everywhere so tests never spawn a subprocess. `proc.py` has no logic of its own and therefore no test of its own.

- [ ] **Step 1: Write the failing test**

`tests/test_tmuxctl_query.py`:

```python
import unittest

from ccnav import tmuxctl


class ParseKvLinesTest(unittest.TestCase):
    def test_splits_on_the_first_equals_only(self):
        # pane_title is arbitrary text and may contain '='.
        parsed = tmuxctl.parse_kv_lines("%1=a=b=c\n")
        self.assertEqual(parsed, {"%1": "a=b=c"})

    def test_title_may_contain_pipes_and_spaces(self):
        parsed = tmuxctl.parse_kv_lines("%2=make test | tee log\n")
        self.assertEqual(parsed, {"%2": "make test | tee log"})

    def test_utf8_title_survives(self):
        parsed = tmuxctl.parse_kv_lines("%3=✳ 작업 중 (X)\n")
        self.assertEqual(parsed, {"%3": "✳ 작업 중 (X)"})

    def test_empty_title_is_empty_string(self):
        self.assertEqual(tmuxctl.parse_kv_lines("%4=\n"), {"%4": ""})

    def test_blank_and_malformed_lines_are_skipped(self):
        parsed = tmuxctl.parse_kv_lines("\n%5=ok\ngarbage\n\n")
        self.assertEqual(parsed, {"%5": "ok"})

    def test_empty_input(self):
        self.assertEqual(tmuxctl.parse_kv_lines(""), {})


class QueryTest(unittest.TestCase):
    def test_list_argv_uses_explicit_socket(self):
        argv = tmuxctl.list_argv("/tmp/s", "#{pane_id}=#{session_name}")
        self.assertEqual(
            argv,
            ["tmux", "-S", "/tmp/s", "list-panes", "-a", "-F",
             "#{pane_id}=#{session_name}"],
        )

    def test_sessions_by_pane(self):
        calls = []

        def fake_run(argv):
            calls.append(list(argv))
            return 0, "%0=demo\n%1=sandbox\n"

        result = tmuxctl.sessions_by_pane("/tmp/s", run=fake_run)
        self.assertEqual(result, {"%0": "demo", "%1": "sandbox"})
        self.assertEqual(calls[0][-1], "#{pane_id}=#{session_name}")

    def test_titles_by_pane(self):
        def fake_run(argv):
            return 0, "%0=✳ 작업 중 (demo-project)\n"

        result = tmuxctl.titles_by_pane("/tmp/s", run=fake_run)
        self.assertEqual(
            result, {"%0": "✳ 작업 중 (demo-project)"}
        )

    def test_no_tmux_server_yields_empty_dict(self):
        def fake_run(argv):
            return 1, ""

        self.assertEqual(tmuxctl.sessions_by_pane("/tmp/s", run=fake_run), {})
        self.assertEqual(tmuxctl.titles_by_pane("/tmp/s", run=fake_run), {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./run-tests -k ParseKvLinesTest -k QueryTest`
Expected: FAIL with `ModuleNotFoundError: No module named 'ccnav.tmuxctl'`

- [ ] **Step 3: Write minimal implementation**

`src/ccnav/proc.py`:

```python
"""The single place cc_navigator spawns a subprocess."""
from __future__ import annotations

import subprocess
from typing import Callable, Sequence, Tuple

Runner = Callable[[Sequence[str]], Tuple[int, str]]


def run_command(argv: Sequence[str]) -> Tuple[int, str]:
    completed = subprocess.run(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        universal_newlines=True,
    )
    return completed.returncode, completed.stdout
```

`src/ccnav/tmuxctl.py`:

```python
"""Every tmux interaction: the queries that build the model, the actions the UI fires."""
from __future__ import annotations

from typing import Dict, List

from .proc import Runner, run_command


def parse_kv_lines(text: str) -> Dict[str, str]:
    """Split each line on its FIRST '='.

    A pane id ('%12') never contains '='. A pane title is whatever Claude Code
    wrote and may contain '=', '|', spaces and arbitrary UTF-8. Splitting once
    is what makes it safe to carry the title in the same record.
    """
    parsed = {}  # type: Dict[str, str]
    for line in text.splitlines():
        if not line:
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        parsed[key] = value
    return parsed


def list_argv(socket: str, fmt: str) -> List[str]:
    return ["tmux", "-S", socket, "list-panes", "-a", "-F", fmt]


def _query(socket: str, fmt: str, run: Runner) -> Dict[str, str]:
    code, out = run(list_argv(socket, fmt))
    if code != 0:
        return {}
    return parse_kv_lines(out)


def sessions_by_pane(socket: str, run: Runner = run_command) -> Dict[str, str]:
    return _query(socket, "#{pane_id}=#{session_name}", run)


def titles_by_pane(socket: str, run: Runner = run_command) -> Dict[str, str]:
    return _query(socket, "#{pane_id}=#{pane_title}", run)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./run-tests -k ParseKvLinesTest -k QueryTest`
Expected: `Ran 10 tests` / `OK`

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/proc.py src/ccnav/tmuxctl.py tests/test_tmuxctl_query.py
git commit -m "feat: tmux pane and title queries"
```

---

### Task 6: tmux actions — select pane and send text

**Files:**
- Modify: `src/ccnav/tmuxctl.py` (append)
- Test: `tests/test_tmuxctl_action.py`

**Interfaces:**
- Consumes: `proc.Runner`, `proc.run_command` (already imported into `tmuxctl` by Task 5).
- Produces:
  - `tmuxctl.select_argvs(socket: str, pane: str) -> List[List[str]]`
  - `tmuxctl.send_text_argvs(socket: str, pane: str, text: str) -> List[List[str]]`
  - `tmuxctl.select_pane(socket: str, pane: str, run=...) -> None`
  - `tmuxctl.send_text(socket: str, pane: str, text: str, run=...) -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_tmuxctl_action.py`:

```python
import unittest

from ccnav import tmuxctl

HOSTILE = "yes; echo 'x' \"y\" $HOME \\ 한글 ✳ Enter C-c"


class SelectArgvsTest(unittest.TestCase):
    def test_switch_then_select_window_then_select_pane(self):
        argvs = tmuxctl.select_argvs("/tmp/s", "%12")
        self.assertEqual(
            argvs,
            [
                ["tmux", "-S", "/tmp/s", "switch-client", "-t", "%12"],
                ["tmux", "-S", "/tmp/s", "select-window", "-t", "%12"],
                ["tmux", "-S", "/tmp/s", "select-pane", "-t", "%12"],
            ],
        )

    def test_select_pane_runs_every_argv_even_if_one_fails(self):
        seen = []

        def fake_run(argv):
            seen.append(list(argv))
            return (1, "") if "switch-client" in argv else (0, "")

        tmuxctl.select_pane("/tmp/s", "%12", run=fake_run)
        self.assertEqual(len(seen), 3)


class SendTextArgvsTest(unittest.TestCase):
    def test_uses_literal_flag_and_double_dash(self):
        argvs = tmuxctl.send_text_argvs("/tmp/s", "%12", "hello")
        self.assertEqual(
            argvs,
            [
                ["tmux", "-S", "/tmp/s", "send-keys", "-t", "%12", "-l", "--", "hello"],
                ["tmux", "-S", "/tmp/s", "send-keys", "-t", "%12", "Enter"],
            ],
        )

    def test_hostile_text_is_a_single_argv_element(self):
        argvs = tmuxctl.send_text_argvs("/tmp/s", "%12", HOSTILE)
        self.assertEqual(argvs[0][-1], HOSTILE)
        self.assertEqual(len(argvs[0]), 9)

    def test_text_starting_with_dash_is_protected_by_double_dash(self):
        argvs = tmuxctl.send_text_argvs("/tmp/s", "%12", "-n --flag")
        self.assertEqual(argvs[0][-2], "--")
        self.assertEqual(argvs[0][-1], "-n --flag")

    def test_enter_is_sent_as_a_separate_named_key(self):
        argvs = tmuxctl.send_text_argvs("/tmp/s", "%12", "Enter")
        # The word "Enter" as user text must be literal, not a keypress.
        self.assertIn("-l", argvs[0])
        self.assertEqual(argvs[0][-1], "Enter")
        self.assertNotIn("-l", argvs[1])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./run-tests -k SelectArgvsTest -k SendTextArgvsTest`
Expected: FAIL with `AttributeError: module 'ccnav.tmuxctl' has no attribute 'select_argvs'`

- [ ] **Step 3: Append the implementation**

Append to `src/ccnav/tmuxctl.py`:

```python
def select_argvs(socket: str, pane: str) -> List[List[str]]:
    """switch-client is best effort: it fails when no client is attached."""
    return [
        ["tmux", "-S", socket, "switch-client", "-t", pane],
        ["tmux", "-S", socket, "select-window", "-t", pane],
        ["tmux", "-S", socket, "select-pane", "-t", pane],
    ]


def send_text_argvs(socket: str, pane: str, text: str) -> List[List[str]]:
    """`-l` and `--` are mandatory.

    Without -l, tmux reads words like 'Enter' and 'C-c' as key names.
    Without --, text beginning with '-' is parsed as an option.
    The text travels as one argv element and never touches a shell.
    """
    return [
        ["tmux", "-S", socket, "send-keys", "-t", pane, "-l", "--", text],
        ["tmux", "-S", socket, "send-keys", "-t", pane, "Enter"],
    ]


def select_pane(socket: str, pane: str, run: Runner = run_command) -> None:
    for argv in select_argvs(socket, pane):
        run(argv)


def send_text(socket: str, pane: str, text: str, run: Runner = run_command) -> None:
    for argv in send_text_argvs(socket, pane, text):
        run(argv)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./run-tests -k SelectArgvsTest -k SendTextArgvsTest`
Expected: `Ran 6 tests` / `OK`

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/tmuxctl.py tests/test_tmuxctl_action.py
git commit -m "feat: tmux pane selection and literal text injection"
```

---

### Task 7: GNOME window activation with independent verification

**Files:**
- Create: `src/ccnav/gnome.py`
- Test: `tests/test_gnome.py`

**Interfaces:**
- Consumes: `proc.Runner`, `proc.run_command`.
- Produces:
  - `gnome.escape_js(value: str) -> str`
  - `gnome.activate_js(title: str) -> str`
  - `gnome.activate_ts_js(title: str) -> str`
  - `gnome.parse_eval_result(stdout: str) -> Tuple[bool, str]`
  - `gnome.parse_match_count(stdout: str) -> int`
  - `gnome.eval_js(js: str, run=...) -> Tuple[bool, str]`
  - `gnome.eval_available(run=...) -> bool`
  - `gnome.active_window_title(run=...) -> Optional[str]`
  - `gnome.ActivationResult` — frozen dataclass with fields `ok: bool`, `matched: int`.
  - `gnome.activate_window_titled(title: str, run=..., sleep=..., timeout: float = 1.5) -> ActivationResult`

Three facts drive this module.

`gdbus` exits 0 even when Eval returns `(false, ...)`, so the return value must be parsed, not trusted.
`Main.activateWindow(w)` and `w.activate(get_current_time_roundtrip())` both switch workspaces; `w.activate(0)` silently does not.
Titles are compared with `===`, and **only the first matching window is activated** — two matches mean two clients on one tmux session, which the spec forbids, so `matched` is reported back for the caller to warn about.

- [ ] **Step 1: Write the failing test**

`tests/test_gnome.py`:

```python
import unittest

from ccnav import gnome

ACTIVE_WINDOW_OUT = "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x3ce416e, 0x0\n"
WM_NAME_OUT = '_NET_WM_NAME(UTF8_STRING) = "ccnav:demo"\n'


class EscapeJsTest(unittest.TestCase):
    def test_escapes_quote_and_backslash(self):
        self.assertEqual(gnome.escape_js("a'b\\c"), "a\\'b\\\\c")

    def test_escapes_newlines(self):
        self.assertEqual(gnome.escape_js("a\nb"), "a\\nb")


class ActivateJsTest(unittest.TestCase):
    def test_compares_titles_with_strict_equality(self):
        js = gnome.activate_js("ccnav:demo")
        self.assertIn("==='ccnav:demo'", js.replace(" ", ""))

    def test_activates_only_the_first_match(self):
        js = gnome.activate_js("ccnav:demo").replace(" ", "")
        self.assertIn("if(!found)found=w", js)
        self.assertIn("Main.activateWindow(found)", js)

    def test_reports_how_many_windows_matched(self):
        self.assertIn("'matched='+n", gnome.activate_js("ccnav:demo"))

    def test_retry_variant_uses_a_roundtrip_timestamp(self):
        js = gnome.activate_ts_js("ccnav:demo")
        self.assertIn("get_current_time_roundtrip()", js)
        self.assertNotIn("activate(0)", js)

    def test_title_with_a_quote_cannot_break_out(self):
        js = gnome.activate_js("ccnav:it's")
        self.assertIn("ccnav:it\\'s", js)


class ParseEvalResultTest(unittest.TestCase):
    def test_true_prefix_is_success(self):
        ok, raw = gnome.parse_eval_result("(true, '\"matched=1\"')\n")
        self.assertTrue(ok)
        self.assertIn("matched=1", raw)

    def test_false_prefix_is_failure_even_though_gdbus_exits_zero(self):
        ok, _ = gnome.parse_eval_result("(false, 'ReferenceError: Shell')\n")
        self.assertFalse(ok)


class ParseMatchCountTest(unittest.TestCase):
    def test_extracts_the_count(self):
        self.assertEqual(gnome.parse_match_count("(true, '\"matched=2\"')\n"), 2)

    def test_absent_count_is_zero(self):
        self.assertEqual(gnome.parse_match_count("(false, 'boom')\n"), 0)


class EvalAvailableTest(unittest.TestCase):
    def test_detects_a_working_eval(self):
        self.assertTrue(gnome.eval_available(run=lambda argv: (0, "(true, '2')\n")))

    def test_detects_a_blocked_eval(self):
        self.assertFalse(gnome.eval_available(run=lambda argv: (0, "(false, '')\n")))

    def test_detects_a_missing_gdbus(self):
        self.assertFalse(gnome.eval_available(run=lambda argv: (127, "")))


class ActiveWindowTitleTest(unittest.TestCase):
    def _run(self, argv):
        if argv[1] == "-root":
            return 0, ACTIVE_WINDOW_OUT
        return 0, WM_NAME_OUT

    def test_reads_the_focused_window_title_through_xprop(self):
        self.assertEqual(gnome.active_window_title(run=self._run), "ccnav:demo")

    def test_missing_active_window_is_none(self):
        self.assertIsNone(gnome.active_window_title(run=lambda argv: (1, "")))


class ActivateWindowTitledTest(unittest.TestCase):
    def test_succeeds_when_focus_actually_moved(self):
        calls = []

        def fake_run(argv):
            calls.append(argv[0])
            if argv[0] == "gdbus":
                return 0, "(true, '\"matched=1\"')\n"
            if argv[1] == "-root":
                return 0, ACTIVE_WINDOW_OUT
            return 0, WM_NAME_OUT

        result = gnome.activate_window_titled(
            "ccnav:demo", run=fake_run, sleep=lambda s: None
        )
        self.assertTrue(result.ok)
        self.assertEqual(calls.count("gdbus"), 1)

    def test_reports_the_number_of_matching_windows(self):
        def fake_run(argv):
            if argv[0] == "gdbus":
                return 0, "(true, '\"matched=2\"')\n"
            if argv[1] == "-root":
                return 0, ACTIVE_WINDOW_OUT
            return 0, WM_NAME_OUT

        result = gnome.activate_window_titled(
            "ccnav:demo", run=fake_run, sleep=lambda s: None
        )
        self.assertEqual(result.matched, 2)

    def test_retries_with_a_timestamp_when_eval_lied(self):
        # Eval reports success but focus never moves: exactly the activate(0) bug.
        seen_js = []
        state = {"focused": "something-else"}

        def fake_run(argv):
            if argv[0] == "gdbus":
                seen_js.append(argv[-1])
                if "get_current_time_roundtrip" in argv[-1]:
                    state["focused"] = "ccnav:demo"
                return 0, "(true, '\"matched=1\"')\n"
            if argv[1] == "-root":
                return 0, ACTIVE_WINDOW_OUT
            return 0, '_NET_WM_NAME(UTF8_STRING) = "%s"\n' % state["focused"]

        result = gnome.activate_window_titled(
            "ccnav:demo", run=fake_run, sleep=lambda s: None, timeout=0.0
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(seen_js), 2)

    def test_reports_failure_when_focus_never_moves(self):
        def fake_run(argv):
            if argv[0] == "gdbus":
                return 0, "(true, '\"matched=1\"')\n"
            if argv[1] == "-root":
                return 0, ACTIVE_WINDOW_OUT
            return 0, '_NET_WM_NAME(UTF8_STRING) = "other"\n'

        result = gnome.activate_window_titled(
            "ccnav:demo", run=fake_run, sleep=lambda s: None, timeout=0.0
        )
        self.assertFalse(result.ok)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./run-tests -k EscapeJsTest -k ActivateJsTest -k ParseEvalResultTest -k ParseMatchCountTest -k EvalAvailableTest -k ActiveWindowTitleTest -k ActivateWindowTitledTest`
Expected: FAIL with `ModuleNotFoundError: No module named 'ccnav.gnome'`

- [ ] **Step 3: Write minimal implementation**

`src/ccnav/gnome.py`:

```python
"""Activate a gnome-terminal window by title, then prove it actually happened.

Two silent-failure bugs motivate the structure of this module:
  * `gdbus` exits 0 even when Eval returns "(false, ...)".
  * `win.activate(0)` returns normally and reports success while doing nothing
    when the window lives on another workspace.
So the effect is always verified through xprop, a different channel from the
one that performed the action.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from .proc import Runner, run_command

EVAL_ARGV = [
    "gdbus", "call", "--session",
    "--dest", "org.gnome.Shell",
    "--object-path", "/org/gnome/Shell",
    "--method", "org.gnome.Shell.Eval",
]

_MATCH_COUNT = re.compile(r"matched=(\d+)")


@dataclass(frozen=True)
class ActivationResult:
    ok: bool
    matched: int


def escape_js(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _match_first_js(title: str, prelude: str, action: str) -> str:
    """Build JS that finds windows titled exactly `title`, acts on the FIRST, counts all.

    Two matches mean two clients on one tmux session. The count comes back so
    the caller can warn instead of silently raising an arbitrary window.
    """
    return (
        "(function(){%svar found=null,n=0;"
        "global.get_window_actors().forEach(function(a){"
        "var w=a.get_meta_window();"
        "if((w.get_title()||'')==='%s'){n++;if(!found)found=w;}"
        "});if(found)%s;"
        "return 'matched='+n;})()" % (prelude, escape_js(title), action)
    )


def activate_js(title: str) -> str:
    """Main.activateWindow picks the workspace and the timestamp for us."""
    return _match_first_js(title, "", "Main.activateWindow(found)")


def activate_ts_js(title: str) -> str:
    """Fallback: an explicit, valid X timestamp. Never pass 0."""
    return _match_first_js(
        title,
        "var t=global.display.get_current_time_roundtrip();",
        "found.activate(t)",
    )


def parse_eval_result(stdout: str) -> Tuple[bool, str]:
    text = stdout.strip()
    return text.startswith("(true"), text


def parse_match_count(stdout: str) -> int:
    match = _MATCH_COUNT.search(stdout)
    return int(match.group(1)) if match else 0


def eval_js(js: str, run: Runner = run_command) -> Tuple[bool, str]:
    code, out = run(EVAL_ARGV + [js])
    if code != 0:
        return False, out
    return parse_eval_result(out)


def eval_available(run: Runner = run_command) -> bool:
    """Blocked from GNOME 41 onward. Probe once at startup."""
    ok, raw = eval_js("1+1", run=run)
    return ok and "2" in raw


def _active_window_id(run: Runner) -> Optional[str]:
    code, out = run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
    if code != 0 or "#" not in out:
        return None
    window_id = out.split("#", 1)[1].split(",")[0].strip()
    return window_id or None


def active_window_title(run: Runner = run_command) -> Optional[str]:
    window_id = _active_window_id(run)
    if not window_id:
        return None
    code, out = run(["xprop", "-id", window_id, "_NET_WM_NAME"])
    if code != 0 or "=" not in out:
        return None
    value = out.split("=", 1)[1].strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return None


def _wait_for_focus(
    title: str, run: Runner, sleep: Callable[[float], None], timeout: float
) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if active_window_title(run=run) == title:
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(0.1)


def activate_window_titled(
    title: str,
    run: Runner = run_command,
    sleep: Callable[[float], None] = time.sleep,
    timeout: float = 1.5,
) -> ActivationResult:
    """Activate the window titled exactly `title`. Verify, then retry once."""
    _, raw = eval_js(activate_js(title), run=run)
    matched = parse_match_count(raw)
    if _wait_for_focus(title, run, sleep, timeout):
        return ActivationResult(True, matched)

    # Eval claimed success but focus did not move: the activate(0) trap.
    _, raw = eval_js(activate_ts_js(title), run=run)
    matched = max(matched, parse_match_count(raw))
    return ActivationResult(_wait_for_focus(title, run, sleep, timeout), matched)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./run-tests -k EscapeJsTest -k ActivateJsTest -k ParseEvalResultTest -k ParseMatchCountTest -k EvalAvailableTest -k ActiveWindowTitleTest -k ActivateWindowTitledTest`
Expected: `Ran 20 tests` / `OK`

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/gnome.py tests/test_gnome.py
git commit -m "feat: activate window by title and verify focus through xprop"
```

---

### Task 8: The model

**Files:**
- Create: `src/ccnav/model.py`
- Test: `tests/test_model.py`

**Interfaces:**
- Consumes: `hookstate.WAITING`.
- Produces:
  - `model.Row` — frozen dataclass with fields `session_id, socket, pane, tmux_session, title, state, reason, message, cwd, updated_at`, plus properties `waiting -> bool` and `window_title -> str` (`"ccnav:" + tmux_session`).
  - `model.build_rows(records, sessions_by_socket, titles_by_socket) -> List[Row]`
  - `model.live_pane_keys(sessions_by_socket) -> Set[Tuple[str, str]]`

- [ ] **Step 1: Write the failing test**

`tests/test_model.py`:

```python
import unittest

from ccnav import hookstate, model

SOCK = "/tmp/tmux-1000/default"


def record(session_id, pane, state=hookstate.WAITING, updated_at=100, socket=SOCK):
    return {
        "session_id": session_id,
        "cwd": "/data/projects/demo_project",
        "tmux_socket": socket,
        "tmux_pane": pane,
        "state": state,
        "reason": "idle",
        "message": "",
        "updated_at": updated_at,
    }


class BuildRowsTest(unittest.TestCase):
    def test_row_carries_the_tmux_session_and_title(self):
        rows = model.build_rows(
            [record("a", "%1")],
            {SOCK: {"%1": "demo"}},
            {SOCK: {"%1": "✳ 작업 중"}},
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].tmux_session, "demo")
        self.assertEqual(rows[0].title, "✳ 작업 중")
        self.assertEqual(rows[0].window_title, "ccnav:demo")
        self.assertTrue(rows[0].waiting)

    def test_record_whose_pane_is_gone_produces_no_row(self):
        rows = model.build_rows([record("a", "%9")], {SOCK: {"%1": "demo"}}, {SOCK: {}})
        self.assertEqual(rows, [])

    def test_missing_title_falls_back_to_the_pane_id(self):
        rows = model.build_rows([record("a", "%1")], {SOCK: {"%1": "demo"}}, {SOCK: {}})
        self.assertEqual(rows[0].title, "%1")

    def test_two_records_on_one_pane_keep_the_newest(self):
        rows = model.build_rows(
            [record("old", "%1", updated_at=1), record("new", "%1", updated_at=2)],
            {SOCK: {"%1": "demo"}},
            {SOCK: {"%1": "t"}},
        )
        self.assertEqual([r.session_id for r in rows], ["new"])

    def test_same_pane_id_on_different_sockets_are_distinct_rows(self):
        other = "/tmp/tmux-1000/other"
        rows = model.build_rows(
            [record("a", "%1"), record("b", "%1", socket=other)],
            {SOCK: {"%1": "demo"}, other: {"%1": "sandbox"}},
            {SOCK: {"%1": "t1"}, other: {"%1": "t2"}},
        )
        self.assertEqual(sorted(r.session_id for r in rows), ["a", "b"])

    def test_waiting_rows_sort_first_then_newest_first(self):
        rows = model.build_rows(
            [
                record("w-old", "%1", updated_at=1),
                record("working", "%2", state=hookstate.WORKING, updated_at=50),
                record("w-new", "%3", updated_at=9),
            ],
            {SOCK: {"%1": "a", "%2": "b", "%3": "c"}},
            {SOCK: {}},
        )
        self.assertEqual([r.session_id for r in rows], ["w-new", "w-old", "working"])

    def test_records_without_socket_or_pane_are_dropped(self):
        bad = {"session_id": "x", "tmux_socket": "", "tmux_pane": "", "updated_at": 1}
        self.assertEqual(model.build_rows([bad], {}, {}), [])


class LivePaneKeysTest(unittest.TestCase):
    def test_flattens_sockets_and_panes(self):
        keys = model.live_pane_keys({SOCK: {"%1": "a", "%2": "b"}})
        self.assertEqual(keys, {(SOCK, "%1"), (SOCK, "%2")})

    def test_empty(self):
        self.assertEqual(model.live_pane_keys({}), set())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./run-tests -k BuildRowsTest -k LivePaneKeysTest`
Expected: FAIL with `ModuleNotFoundError: No module named 'ccnav.model'`

- [ ] **Step 3: Write minimal implementation**

`src/ccnav/model.py`:

```python
"""Join state files with live tmux panes into the rows the UI renders."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from . import hookstate


@dataclass(frozen=True)
class Row:
    session_id: str
    socket: str
    pane: str
    tmux_session: str
    title: str
    state: str
    reason: str
    message: str
    cwd: str
    updated_at: int

    @property
    def waiting(self) -> bool:
        return self.state == hookstate.WAITING

    @property
    def window_title(self) -> str:
        """The address. tmux's set-titles-string puts exactly this on the window."""
        return "ccnav:" + self.tmux_session


def live_pane_keys(sessions_by_socket: Dict[str, Dict[str, str]]) -> Set[Tuple[str, str]]:
    keys = set()  # type: Set[Tuple[str, str]]
    for socket, panes in sessions_by_socket.items():
        for pane in panes:
            keys.add((socket, pane))
    return keys


def _newest_per_pane(records):
    newest = {}  # type: Dict[Tuple[str, str], dict]
    for rec in records:
        key = (str(rec.get("tmux_socket") or ""), str(rec.get("tmux_pane") or ""))
        if not key[0] or not key[1]:
            continue
        current = newest.get(key)
        if current is None or int(rec.get("updated_at", 0)) > int(
            current.get("updated_at", 0)
        ):
            newest[key] = rec
    return newest


def build_rows(
    records: List[Dict[str, object]],
    sessions_by_socket: Dict[str, Dict[str, str]],
    titles_by_socket: Dict[str, Dict[str, str]],
) -> List[Row]:
    """A row exists iff its state file's pane is currently live in tmux."""
    rows = []  # type: List[Row]
    for (socket, pane), rec in _newest_per_pane(records).items():
        sessions = sessions_by_socket.get(socket, {})
        if pane not in sessions:
            continue
        titles = titles_by_socket.get(socket, {})
        rows.append(
            Row(
                session_id=str(rec.get("session_id") or ""),
                socket=socket,
                pane=pane,
                tmux_session=sessions[pane],
                title=titles.get(pane) or pane,
                state=str(rec.get("state") or ""),
                reason=str(rec.get("reason") or ""),
                message=str(rec.get("message") or ""),
                cwd=str(rec.get("cwd") or ""),
                updated_at=int(rec.get("updated_at", 0)),
            )
        )
    rows.sort(key=lambda row: (0 if row.waiting else 1, -row.updated_at))
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./run-tests -k BuildRowsTest -k LivePaneKeysTest`
Expected: `Ran 9 tests` / `OK`

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/model.py tests/test_model.py
git commit -m "feat: join state files and live tmux panes into rows"
```

---

### Task 9: The overlay window

**Files:**
- Create: `src/ccnav/ui.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `model.Row`.
- Produces:
  - `ui.SECONDARY_LIMIT: int`, `ui.EMPTY_HINT: str`
  - `ui.secondary_line(row: model.Row) -> str`
  - `ui.compose_status(sticky: str, hint: str, transient: str) -> str`
  - `ui.NavigatorWindow(on_jump: Callable[[Row], None], on_send: Callable[[Row, str], None])` with methods `set_rows(rows: List[Row]) -> None`, `set_status(text: str) -> None`, `set_eval_available(available: bool) -> None`.

`secondary_line` and `compose_status` are pure and carry all the formatting logic, so the GTK class stays thin.

The status bar has three independent slots, because they can co-occur and must not clobber one another: `sticky` (Eval unavailable — set once at startup), `hint` (the empty-list message — driven by `set_rows`), and `transient` (a jump failure or warning — set by the caller). `NavigatorWindow` does **not** connect `destroy` to `Gtk.main_quit`; Task 10 wires that in `app.main()`, where the main loop actually exists.

- [ ] **Step 1: Write the failing test**

`tests/test_ui.py`:

```python
import os
import unittest

from ccnav import hookstate, model, ui


def row(state=hookstate.WAITING, reason="permission_prompt", message="Allow npm test?",
        cwd="/data/projects/demo_project"):
    return model.Row(
        session_id="a", socket="/tmp/s", pane="%1", tmux_session="demo",
        title="✳ 작업 중", state=state, reason=reason,
        message=message, cwd=cwd, updated_at=1,
    )


class SecondaryLineTest(unittest.TestCase):
    def test_waiting_shows_reason_and_message(self):
        self.assertEqual(
            ui.secondary_line(row()), "permission_prompt — Allow npm test?"
        )

    def test_waiting_without_message_shows_only_reason(self):
        self.assertEqual(ui.secondary_line(row(message="")), "permission_prompt")

    def test_waiting_without_reason_or_message_is_empty(self):
        self.assertEqual(ui.secondary_line(row(reason="", message="")), "")

    def test_working_shows_the_project_directory_name(self):
        self.assertEqual(
            ui.secondary_line(row(state=hookstate.WORKING)), "demo_project"
        )

    def test_working_tolerates_a_trailing_slash(self):
        self.assertEqual(
            ui.secondary_line(row(state=hookstate.WORKING, cwd="/a/b/")), "b"
        )

    def test_working_without_cwd_is_empty(self):
        self.assertEqual(ui.secondary_line(row(state=hookstate.WORKING, cwd="")), "")

    def test_long_secondary_line_is_truncated(self):
        line = ui.secondary_line(row(message="x" * 500))
        self.assertLessEqual(len(line), ui.SECONDARY_LIMIT)


class ComposeStatusTest(unittest.TestCase):
    def test_all_three_slots_are_shown(self):
        self.assertEqual(ui.compose_status("a", "b", "c"), "a  b  c")

    def test_the_eval_warning_survives_an_empty_list(self):
        # A jump failure must never hide the fact that Eval is unavailable.
        self.assertEqual(ui.compose_status("eval off", "no sessions", ""), "eval off  no sessions")

    def test_empty_slots_are_dropped(self):
        self.assertEqual(ui.compose_status("", "", "c"), "c")

    def test_all_empty_is_empty(self):
        self.assertEqual(ui.compose_status("", "", ""), "")


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs an X11 display")
class NavigatorWindowTest(unittest.TestCase):
    def test_constructs_and_accepts_rows(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        window.set_rows([row(), row(state=hookstate.WORKING)])
        window.set_status("hello")
        window.set_eval_available(False)
        # destroy must not call Gtk.main_quit: no main loop is running here.
        window.destroy()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./run-tests -k SecondaryLineTest`
Expected: FAIL with `ModuleNotFoundError: No module named 'ccnav.ui'`

- [ ] **Step 3: Write minimal implementation**

`src/ccnav/ui.py`:

```python
"""The always-on-top overlay. All formatting lives in pure functions above the widgets."""
from __future__ import annotations

import os
from typing import Callable, List

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import model  # noqa: E402

SECONDARY_LIMIT = 80
WINDOW_WIDTH = 340
WINDOW_HEIGHT = 420
EMPTY_HINT = (
    "세션이 없습니다. tmux 안에서 실행 중이고 훅이 설치되었는지 "
    "확인하세요 (bin/cc-navigator-doctor)."
)
EVAL_UNAVAILABLE_HINT = "GNOME Shell Eval을 쓸 수 없어 '이동'이 비활성화되었습니다."


def secondary_line(row: model.Row) -> str:
    if row.waiting:
        parts = [part for part in (row.reason, row.message) if part]
        return " — ".join(parts)[:SECONDARY_LIMIT]
    return os.path.basename(row.cwd.rstrip("/"))


def compose_status(sticky: str, hint: str, transient: str) -> str:
    """Three independent slots that must not clobber one another."""
    return "  ".join(part for part in (sticky, hint, transient) if part)


class NavigatorWindow(Gtk.Window):
    def __init__(
        self,
        on_jump: Callable[[model.Row], None],
        on_send: Callable[[model.Row, str], None],
    ) -> None:
        super().__init__(title="cc_navigator")
        self._on_jump = on_jump
        self._on_send = on_send
        self._eval_available = True
        self._sticky = ""
        self._hint = ""
        self._transient = ""

        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_default_size(WINDOW_WIDTH, WINDOW_HEIGHT)
        screen = Gdk.Screen.get_default()
        if screen is not None:
            self.move(screen.get_width() - WINDOW_WIDTH - 20, 40)

        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._listbox.connect("row-selected", self._on_row_selected)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self._listbox)

        self._status = Gtk.Label(xalign=0.0)
        self._status.set_line_wrap(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.pack_start(scroller, True, True, 0)
        box.pack_start(self._status, False, False, 4)
        self.add(box)
        # No destroy -> Gtk.main_quit here: app.main() owns the main loop.

    def _render_status(self) -> None:
        self._status.set_text(compose_status(self._sticky, self._hint, self._transient))

    def set_eval_available(self, available: bool) -> None:
        self._eval_available = available
        self._sticky = "" if available else EVAL_UNAVAILABLE_HINT
        self._render_status()

    def set_status(self, text: str) -> None:
        self._transient = text
        self._render_status()

    def set_rows(self, rows: List[model.Row]) -> None:
        for child in self._listbox.get_children():
            self._listbox.remove(child)
        for row in rows:
            self._listbox.add(self._build_row(row))
        self._hint = "" if rows else EMPTY_HINT
        self._render_status()
        self._listbox.show_all()

    def _build_row(self, row: model.Row) -> Gtk.ListBoxRow:
        list_row = Gtk.ListBoxRow()

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dot = Gtk.Label()
        dot.set_markup(
            '<span foreground="#e01b24">●</span>'
            if row.waiting
            else '<span foreground="#77767b">○</span>'
        )
        header.pack_start(dot, False, False, 0)

        if row.waiting:
            badge = Gtk.Label()
            badge.set_markup('<small><b>Waiting input</b></small>')
            header.pack_start(badge, False, False, 0)

        title = Gtk.Label(xalign=0.0)
        title.set_markup("<b>%s</b>" % GLib.markup_escape_text(row.title))
        title.set_ellipsize(Pango.EllipsizeMode.END)
        header.pack_start(title, True, True, 0)

        secondary = Gtk.Label(xalign=0.0)
        secondary.set_markup(
            '<small><span foreground="#77767b">%s</span></small>'
            % GLib.markup_escape_text(secondary_line(row))
        )
        secondary.set_ellipsize(Pango.EllipsizeMode.END)

        entry = Gtk.Entry()
        entry.set_placeholder_text("입력 후 Enter")
        entry.connect("activate", self._on_entry_activate, row)

        jump = Gtk.Button(label="해당 세션으로 이동")
        jump.set_sensitive(self._eval_available)
        jump.connect("clicked", self._on_jump_clicked, row)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        actions.pack_start(entry, True, True, 0)
        actions.pack_start(jump, False, False, 0)

        revealer = Gtk.Revealer()
        revealer.add(actions)
        list_row.ccnav_revealer = revealer  # type: ignore[attr-defined]

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        body.set_margin_top(6)
        body.set_margin_bottom(6)
        body.set_margin_start(8)
        body.set_margin_end(8)
        body.pack_start(header, False, False, 0)
        body.pack_start(secondary, False, False, 0)
        body.pack_start(revealer, False, False, 0)
        list_row.add(body)
        return list_row

    def _on_row_selected(self, _listbox, selected) -> None:
        for child in self._listbox.get_children():
            revealer = getattr(child, "ccnav_revealer", None)
            if revealer is not None:
                revealer.set_reveal_child(child is selected)

    def _on_jump_clicked(self, _button, row: model.Row) -> None:
        self._on_jump(row)

    def _on_entry_activate(self, entry: Gtk.Entry, row: model.Row) -> None:
        text = entry.get_text()
        if not text.strip():
            return
        entry.set_text("")
        self._on_send(row, text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./run-tests -k SecondaryLineTest -k ComposeStatusTest -k NavigatorWindowTest`
Expected: `Ran 12 tests` / `OK`, with no `Gtk-CRITICAL` warnings in the output (the window test is skipped without `DISPLAY`)

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/ui.py tests/test_ui.py
git commit -m "feat: always-on-top overlay listing sessions"
```

---

### Task 10: Wiring and the launcher

**Files:**
- Create: `src/ccnav/app.py`
- Create: `bin/cc-navigator`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `paths.ensure_state_dir`, `statestore.read_all`, `statestore.prune`, `tmuxctl.sessions_by_pane`, `tmuxctl.titles_by_pane`, `tmuxctl.select_pane`, `tmuxctl.send_text`, `gnome.eval_available`, `gnome.activate_window_titled` (returns `ActivationResult`), `model.build_rows`, `model.live_pane_keys`, `ui.NavigatorWindow`. `NavigatorWindow` deliberately does not connect `destroy`; `app.main()` must.
- Produces: `app.collect_rows(state_dir, read_all, sessions_for, titles_for, prune) -> List[model.Row]`, `app.main() -> int`, `app.POLL_SECONDS: int`.

`collect_rows` holds all the logic and takes its collaborators as arguments, so it is testable without GTK, tmux, or a display.

- [ ] **Step 1: Write the failing test**

`tests/test_app.py`:

```python
import pathlib
import unittest

from ccnav import app, hookstate

SOCK = "/tmp/tmux-1000/default"


def record(pane="%1", socket=SOCK):
    return {
        "session_id": "a", "cwd": "/proj", "tmux_socket": socket, "tmux_pane": pane,
        "state": hookstate.WAITING, "reason": "idle", "message": "", "updated_at": 5,
    }


class CollectRowsTest(unittest.TestCase):
    def test_queries_only_the_sockets_the_state_files_mention(self):
        asked = []

        def sessions_for(socket):
            asked.append(socket)
            return {"%1": "demo"}

        rows = app.collect_rows(
            pathlib.Path("/nonexistent"),
            read_all=lambda d: [record()],
            sessions_for=sessions_for,
            titles_for=lambda s: {"%1": "t"},
            prune=lambda d, live: 0,
        )
        self.assertEqual(asked, [SOCK])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].window_title, "ccnav:demo")

    def test_prunes_using_the_live_pane_set(self):
        seen = {}

        def fake_prune(directory, live):
            seen["live"] = live
            return 0

        app.collect_rows(
            pathlib.Path("/nonexistent"),
            read_all=lambda d: [record()],
            sessions_for=lambda s: {"%1": "demo", "%2": "sandbox"},
            titles_for=lambda s: {},
            prune=fake_prune,
        )
        self.assertEqual(seen["live"], {(SOCK, "%1"), (SOCK, "%2")})

    def test_no_state_files_means_no_tmux_calls_and_no_rows(self):
        def explode(socket):
            raise AssertionError("must not query tmux")

        rows = app.collect_rows(
            pathlib.Path("/nonexistent"),
            read_all=lambda d: [],
            sessions_for=explode,
            titles_for=explode,
            prune=lambda d, live: 0,
        )
        self.assertEqual(rows, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./run-tests -k CollectRowsTest`
Expected: FAIL with `ModuleNotFoundError: No module named 'ccnav.app'`

- [ ] **Step 3: Write minimal implementation**

`src/ccnav/app.py`:

```python
"""Wire the state directory, tmux, GNOME and the overlay together."""
from __future__ import annotations

import pathlib
from typing import Callable, Dict, List

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import gnome, model, paths, statestore, tmuxctl, ui  # noqa: E402

POLL_SECONDS = 1


def collect_rows(
    state_dir: pathlib.Path,
    read_all: Callable[[pathlib.Path], List[Dict[str, object]]] = statestore.read_all,
    sessions_for: Callable[[str], Dict[str, str]] = tmuxctl.sessions_by_pane,
    titles_for: Callable[[str], Dict[str, str]] = tmuxctl.titles_by_pane,
    prune: Callable[..., int] = statestore.prune,
) -> List[model.Row]:
    records = read_all(state_dir)
    sockets = sorted({str(r.get("tmux_socket") or "") for r in records if r.get("tmux_socket")})
    if not sockets:
        return []
    sessions = {socket: sessions_for(socket) for socket in sockets}
    titles = {socket: titles_for(socket) for socket in sockets}
    prune(state_dir, model.live_pane_keys(sessions))
    return model.build_rows(records, sessions, titles)


class Application:
    def __init__(self) -> None:
        self.state_dir = paths.ensure_state_dir()
        self.window = ui.NavigatorWindow(on_jump=self.jump, on_send=self.send)
        self.window.set_eval_available(gnome.eval_available())

        monitor = Gio.File.new_for_path(str(self.state_dir)).monitor_directory(
            Gio.FileMonitorFlags.NONE, None
        )
        monitor.connect("changed", self._on_state_changed)
        self._monitor = monitor  # keep a reference or it is collected
        GLib.timeout_add_seconds(POLL_SECONDS, self._tick)

    def _on_state_changed(self, *_args) -> None:
        self.refresh()

    def _tick(self) -> bool:
        self.refresh()
        return True

    def refresh(self) -> None:
        self.window.set_rows(collect_rows(self.state_dir))

    def jump(self, row: model.Row) -> None:
        tmuxctl.select_pane(row.socket, row.pane)
        result = gnome.activate_window_titled(row.window_title)
        if not result.ok:
            self.window.set_status("창을 활성화하지 못했습니다: " + row.window_title)
        elif result.matched > 1:
            self.window.set_status(
                "같은 제목의 창이 %d개입니다. tmux 세션 하나에는 "
                "클라이언트 하나만 붙이세요: %s" % (result.matched, row.window_title)
            )
        else:
            self.window.set_status("")

    def send(self, row: model.Row, text: str) -> None:
        tmuxctl.send_text(row.socket, row.pane, text)
        self.window.set_status("")


def main() -> int:
    application = Application()
    # Wired here, not in NavigatorWindow: this is where the main loop exists.
    application.window.connect("destroy", Gtk.main_quit)
    application.refresh()
    application.window.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`bin/cc-navigator`:

```sh
#!/bin/sh
# /usr/bin/python3 is mandatory: `which python3` is Anaconda and has no PyGObject.
set -e
here=$(cd "$(dirname "$0")" && pwd)
PYTHONPATH="$here/../src${PYTHONPATH:+:$PYTHONPATH}" exec /usr/bin/python3 -m ccnav.app
```

```bash
chmod +x bin/cc-navigator
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./run-tests -k CollectRowsTest`
Expected: `Ran 3 tests` / `OK`

- [ ] **Step 5: Run the whole suite**

Run: `./run-tests`
Expected: `OK` with zero failures.

- [ ] **Step 6: Commit**

```bash
git add src/ccnav/app.py bin/cc-navigator tests/test_app.py
git commit -m "feat: wire state watching, tmux polling and the overlay"
```

---

### Task 11: Prerequisite doctor

**Files:**
- Create: `src/ccnav/doctor.py`
- Create: `bin/cc-navigator-doctor`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `gnome.eval_available`.
- Produces:
  - `doctor.Check` — frozen dataclass with fields `name: str`, `ok: bool`, `detail: str`, `fix: str`.
  - `doctor.check_tmux_conf(text: str) -> Check`
  - `doctor.check_tmux_titles(text: str) -> Check`
  - `doctor.check_claude_hooks(settings: Dict[str, object], hook_path: str) -> Check`
  - `doctor.run_all(...) -> List[Check]`

The `set mode-keys vi` check is the important one: without `-g`, the tmux server dies the moment cc_navigator sends it a command.

- [ ] **Step 1: Write the failing test**

`tests/test_doctor.py`:

```python
import unittest

from ccnav import doctor

HOOK = "/data/playground/cc_navigator/bin/cc-navigator-hook"

GOOD_CONF = """
setw -g mode-keys vi
set -g mouse on
set -g set-titles on
set -g set-titles-string 'ccnav:#{session_name}'
"""

FATAL_CONF = """
set mode-keys vi
set -g mouse on
"""


def settings_with_hook(command=HOOK):
    return {
        "hooks": {
            "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": command}]}]
        }
    }


class TmuxConfTest(unittest.TestCase):
    def test_bare_set_mode_keys_is_fatal(self):
        check = doctor.check_tmux_conf(FATAL_CONF)
        self.assertFalse(check.ok)
        self.assertIn("mode-keys", check.detail)
        self.assertIn("setw -g mode-keys vi", check.fix)

    def test_global_set_mode_keys_is_fine(self):
        self.assertTrue(doctor.check_tmux_conf(GOOD_CONF).ok)

    def test_set_g_mode_keys_is_fine(self):
        self.assertTrue(doctor.check_tmux_conf("set -g mode-keys vi\n").ok)

    def test_set_gw_mode_keys_is_fine(self):
        self.assertTrue(doctor.check_tmux_conf("set -gw mode-keys vi\n").ok)

    def test_commented_out_line_is_ignored(self):
        self.assertTrue(doctor.check_tmux_conf("# set mode-keys vi\n").ok)

    def test_absent_line_is_fine(self):
        self.assertTrue(doctor.check_tmux_conf("").ok)


class TmuxTitlesTest(unittest.TestCase):
    def test_requires_both_options(self):
        self.assertTrue(doctor.check_tmux_titles(GOOD_CONF).ok)

    def test_missing_set_titles_string_fails(self):
        check = doctor.check_tmux_titles("set -g set-titles on\n")
        self.assertFalse(check.ok)
        self.assertIn("set-titles-string", check.fix)

    def test_wrong_title_format_fails(self):
        conf = "set -g set-titles on\nset -g set-titles-string 'x'\n"
        self.assertFalse(doctor.check_tmux_titles(conf).ok)


class ClaudeHooksTest(unittest.TestCase):
    def test_detects_the_hook(self):
        self.assertTrue(doctor.check_claude_hooks(settings_with_hook(), HOOK).ok)

    def test_missing_hook_fails(self):
        self.assertFalse(doctor.check_claude_hooks({}, HOOK).ok)

    def test_a_different_command_does_not_count(self):
        settings = settings_with_hook(command="notify-send hi")
        self.assertFalse(doctor.check_claude_hooks(settings, HOOK).ok)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./run-tests -k TmuxConfTest -k TmuxTitlesTest -k ClaudeHooksTest`
Expected: FAIL with `ModuleNotFoundError: No module named 'ccnav.doctor'`

- [ ] **Step 3: Write minimal implementation**

`src/ccnav/doctor.py`:

```python
"""Prerequisite checks. cc_navigator refuses to be useful until these pass."""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import gnome

TITLE_FORMAT = "ccnav:#{session_name}"

# `set mode-keys vi` without -g kills the tmux server the moment an outside
# command targets it, which is precisely what cc_navigator does.
_FATAL_MODE_KEYS = re.compile(r"^\s*set(-option)?\s+mode-keys\b")
_SET_TITLES_ON = re.compile(r"^\s*set(-option)?\s+-g\s+set-titles\s+on\b", re.M)
_SET_TITLES_STRING = re.compile(
    r"^\s*set(-option)?\s+-g\s+set-titles-string\s+['\"]" + re.escape(TITLE_FORMAT),
    re.M,
)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str


def check_tmux_conf(text: str) -> Check:
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if _FATAL_MODE_KEYS.match(line):
            return Check(
                name="tmux.conf mode-keys",
                ok=False,
                detail="`%s` has no -g; the tmux server dies on the first "
                "scripted command" % line.strip(),
                fix="replace it with: setw -g mode-keys vi",
            )
    return Check("tmux.conf mode-keys", True, "no bare `set mode-keys`", "")


def check_tmux_titles(text: str) -> Check:
    missing = []
    if not _SET_TITLES_ON.search(text):
        missing.append("set -g set-titles on")
    if not _SET_TITLES_STRING.search(text):
        missing.append("set -g set-titles-string '%s'" % TITLE_FORMAT)
    if missing:
        return Check(
            name="tmux.conf set-titles",
            ok=False,
            detail="the outer window title is cc_navigator's only address",
            fix="add to ~/.tmux.conf:\n    " + "\n    ".join(missing),
        )
    return Check("tmux.conf set-titles", True, "window title is owned by tmux", "")


def _hook_commands(settings: Dict[str, object]) -> List[str]:
    commands = []  # type: List[str]
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return commands
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            inner = entry.get("hooks")
            if not isinstance(inner, list):
                continue
            for hook in inner:
                if not isinstance(hook, dict):
                    continue
                command = hook.get("command")
                if isinstance(command, str):
                    commands.append(command)
    return commands


def check_claude_hooks(settings: Dict[str, object], hook_path: str) -> Check:
    if any(hook_path in command for command in _hook_commands(settings)):
        return Check("claude hooks", True, "cc-navigator-hook is installed", "")
    return Check(
        name="claude hooks",
        ok=False,
        detail="sessions started without the hook never appear in the list",
        fix="add %s to the Notification, Stop, PreToolUse, SessionStart and "
        "UserPromptSubmit hooks in ~/.claude/settings.json" % hook_path,
    )


def _read(path: pathlib.Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def run_all(
    tmux_conf: Optional[pathlib.Path] = None,
    claude_settings: Optional[pathlib.Path] = None,
    hook_path: str = "",
) -> List[Check]:
    home = pathlib.Path(os.path.expanduser("~"))
    tmux_conf = tmux_conf or home / ".tmux.conf"
    claude_settings = claude_settings or home / ".claude" / "settings.json"
    hook_path = hook_path or str(
        pathlib.Path(__file__).resolve().parents[2] / "bin" / "cc-navigator-hook"
    )

    conf_text = _read(tmux_conf)
    try:
        settings = json.loads(_read(claude_settings) or "{}")
    except ValueError:
        settings = {}

    checks = [
        Check(
            "python3",
            os.path.exists("/usr/bin/python3"),
            "/usr/bin/python3 is the only interpreter with PyGObject",
            "install python3-gi",
        ),
        Check(
            "tmux",
            shutil.which("tmux") is not None,
            "tmux is required",
            "apt install tmux",
        ),
        Check(
            "xprop",
            shutil.which("xprop") is not None,
            "xprop verifies that focus actually moved",
            "apt install x11-utils",
        ),
        check_tmux_conf(conf_text),
        check_tmux_titles(conf_text),
        check_claude_hooks(settings, hook_path),
        Check(
            "gnome shell eval",
            gnome.eval_available(),
            "required for jump; blocked from GNOME 41 onward",
            "jump will be disabled; listing and input still work",
        ),
    ]
    return checks


def main() -> int:
    failures = 0
    for check in run_all():
        mark = "ok  " if check.ok else "FAIL"
        print("[%s] %-22s %s" % (mark, check.name, check.detail))
        if not check.ok:
            failures += 1
            if check.fix:
                print("       fix: %s" % check.fix)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`bin/cc-navigator-doctor`:

```sh
#!/bin/sh
set -e
here=$(cd "$(dirname "$0")" && pwd)
PYTHONPATH="$here/../src${PYTHONPATH:+:$PYTHONPATH}" exec /usr/bin/python3 -m ccnav.doctor
```

```bash
chmod +x bin/cc-navigator-doctor
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./run-tests -k TmuxConfTest -k TmuxTitlesTest -k ClaudeHooksTest`
Expected: `Ran 12 tests` / `OK`

- [ ] **Step 5: Run the doctor against the real machine**

Run: `./bin/cc-navigator-doctor; echo "exit=$?"`
Expected: `FAIL` lines for `tmux.conf mode-keys`, `tmux.conf set-titles` and `claude hooks`, each with a `fix:` line. `exit=1`.

- [ ] **Step 6: Commit**

```bash
git add src/ccnav/doctor.py bin/cc-navigator-doctor tests/test_doctor.py
git commit -m "feat: prerequisite doctor, including the fatal tmux mode-keys bug"
```

---

### Task 12: Integration test and spike archive

**Files:**
- Create: `tests/test_integration.py`
- Create: `spikes/README.md`
- Create: `spikes/01_jump.sh`
- Create: `spikes/02_pane_title.sh`
- Create: `spikes/03_send_keys.sh`

**Interfaces:**
- Consumes: `tmuxctl`, `hook`, `statestore`, `model`, `paths`.
- Produces: nothing importable.

The integration test drives real tmux on a private socket. It never touches the user's tmux server or the user's config.

- [ ] **Step 1: Write the integration test**

`tests/test_integration.py`:

```python
"""Drives a real tmux server on a private socket. No GNOME, no display."""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import unittest

from ccnav import hook, model, statestore, tmuxctl

# A private socket in our own temp dir: this test can never touch a real session.
SOCKET = os.path.join(tempfile.gettempdir(), "ccnav-itest-%d" % os.getuid())


def tmux(*args):
    return subprocess.run(
        ["tmux", "-S", SOCKET] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        universal_newlines=True,
    )


@unittest.skipUnless(shutil.which("tmux"), "needs tmux")
class TmuxIntegrationTest(unittest.TestCase):
    def setUp(self):
        tmux("kill-server")
        # -f /dev/null: never load the user's ~/.tmux.conf.
        subprocess.run(
            ["tmux", "-S", SOCKET, "-f", "/dev/null", "new-session", "-d", "-s", "proj"],
            check=True,
        )
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = pathlib.Path(self._tmp.name)

    def tearDown(self):
        tmux("kill-server")
        self._tmp.cleanup()

    def _pane_id(self):
        return tmux("display", "-p", "-t", "proj", "#{pane_id}").stdout.strip()

    def test_pane_title_from_an_osc_escape_reaches_the_row(self):
        pane = self._pane_id()
        title = "✳ 작업 중 (demo-project)"
        tmux("send-keys", "-t", pane,
             "printf '\\033]2;%s\\007'" % title, "Enter")
        time.sleep(1.0)

        record = hook.build_record(
            {"hook_event_name": "Stop", "session_id": "s1", "cwd": "/proj"},
            {"TMUX": SOCKET + ",1,0", "TMUX_PANE": pane},
            now=int(time.time()),
        )
        statestore.write(self.state_dir, record)

        sessions = {SOCKET: tmuxctl.sessions_by_pane(SOCKET)}
        titles = {SOCKET: tmuxctl.titles_by_pane(SOCKET)}
        rows = model.build_rows(statestore.read_all(self.state_dir), sessions, titles)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, title)
        self.assertEqual(rows[0].window_title, "ccnav:proj")
        self.assertTrue(rows[0].waiting)

    def test_send_text_arrives_byte_exact(self):
        pane = self._pane_id()
        got = pathlib.Path(self._tmp.name) / "got.txt"
        tmux("send-keys", "-t", pane,
             'IFS= read -r line < /dev/tty; printf %s "$line" > ' + str(got), "Enter")
        time.sleep(0.6)

        payload = "yes; echo 'x' \"y\" $HOME \\ 한글 ✳ Enter C-c"
        tmuxctl.send_text(SOCKET, pane, payload)
        time.sleep(0.8)

        self.assertEqual(got.read_text(), payload)

    def test_row_disappears_when_the_pane_dies(self):
        pane = self._pane_id()
        record = hook.build_record(
            {"hook_event_name": "Stop", "session_id": "s1", "cwd": "/proj"},
            {"TMUX": SOCKET + ",1,0", "TMUX_PANE": pane},
            now=int(time.time()),
        )
        statestore.write(self.state_dir, record)
        tmux("kill-server")
        time.sleep(0.3)

        sessions = {SOCKET: tmuxctl.sessions_by_pane(SOCKET)}
        rows = model.build_rows(
            statestore.read_all(self.state_dir), sessions, {SOCKET: {}}
        )
        self.assertEqual(rows, [])

        statestore.prune(self.state_dir, model.live_pane_keys(sessions))
        self.assertEqual(list(self.state_dir.iterdir()), [])
```

- [ ] **Step 2: Run the integration test to verify it passes**

Run: `./run-tests -k TmuxIntegrationTest`
Expected: `Ran 3 tests` / `OK`

If it fails with `server exited unexpectedly`, the `-f /dev/null` was dropped from `setUp`: the
user's `~/.tmux.conf` contains `set mode-keys vi` and kills the server.

- [ ] **Step 3: Archive the spikes**

`spikes/README.md`:

```markdown
# Spikes

These reproduce, from scratch, the assumptions the design rests on. Run them after a
GNOME, tmux or gnome-terminal upgrade. Each cleans up after itself and uses a private
tmux socket, so none of them can touch a real session.

    sh spikes/01_jump.sh         # opens a terminal window and steals focus briefly
    sh spikes/02_pane_title.sh
    sh spikes/03_send_keys.sh

Expected results are recorded in Appendix A of
`docs/superpowers/specs/2026-07-10-cc-navigator-design.md`.
```

`spikes/02_pane_title.sh`:

```sh
#!/bin/sh
# Does tmux capture the OSC title an inner program sets, as #{pane_title}?
set -e
S=ccnav_spike_title
tmux -L $S kill-server 2>/dev/null || true
tmux -L $S -f /dev/null new-session -d -s t
tmux -L $S send-keys -t t "printf '\033]2;\342\234\263 TEST (proj)\007'" Enter
sleep 1
echo "pane_title=[$(tmux -L $S display -p -t t '#{pane_title}')]"
echo "window_name=[$(tmux -L $S display -p -t t '#{window_name}')]"
tmux -L $S kill-server 2>/dev/null || true
echo "expected: pane_title=[✳ TEST (proj)], window_name unchanged"
```

`spikes/03_send_keys.sh`:

```sh
#!/bin/sh
# Does `send-keys -l --` deliver arbitrary text byte-exact?
set -e
S=ccnav_spike_send
OUT=/tmp/ccnav_spike_send.txt
rm -f "$OUT"
tmux -L $S kill-server 2>/dev/null || true
tmux -L $S -f /dev/null new-session -d -s t
sleep 0.5
tmux -L $S send-keys -t t "IFS= read -r line < /dev/tty; printf %s \"\$line\" > $OUT" Enter
sleep 0.5
PAYLOAD='yes; echo '"'"'x'"'"' "y" $HOME \ 한글 ✳ Enter C-c'
tmux -L $S send-keys -t t -l -- "$PAYLOAD"
tmux -L $S send-keys -t t Enter
sleep 0.8
printf '%s' "$PAYLOAD" > "$OUT.expected"
if cmp -s "$OUT" "$OUT.expected"; then echo "byte-exact OK"; else echo "MANGLED"; fi
tmux -L $S kill-server 2>/dev/null || true
rm -f "$OUT" "$OUT.expected"
```

`spikes/01_jump.sh`:

```sh
#!/bin/sh
# Does set-titles own the outer window title, and does Eval activation actually move focus?
# Opens a gnome-terminal window and steals focus for a moment.
set -e
S=ccnav_spike_jump
EV() { gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
        --method org.gnome.Shell.Eval "$1"; }
tmux -L $S kill-server 2>/dev/null || true
tmux -L $S -f /dev/null new-session -d -s spikeproj
tmux -L $S set -g set-titles on
tmux -L $S set -g set-titles-string 'ccnav:#{session_name}'
gnome-terminal --window -- tmux -L $S attach -t spikeproj
sleep 3
echo "--- titles containing ccnav: ---"
for w in $(xprop -root _NET_CLIENT_LIST | sed 's/.*# //' | tr -d ' ' | tr ',' '\n'); do
  n=$(xprop -id "$w" _NET_WM_NAME 2>/dev/null | sed 's/.*= //')
  case "$n" in *ccnav:spikeproj*) echo "  $w $n";; esac
done
echo "--- activate via Main.activateWindow, then verify with xprop ---"
EV "(function(){var found=null,n=0;global.get_window_actors().forEach(function(a){var w=a.get_meta_window();if((w.get_title()||'')==='ccnav:spikeproj'){n++;if(!found)found=w;}});if(found)Main.activateWindow(found);return 'matched='+n;})()"
sleep 1
A=$(xprop -root _NET_ACTIVE_WINDOW | sed 's/.*# *//' | cut -d, -f1)
echo "  focused now: $(xprop -id "$A" _NET_WM_NAME | sed 's/.*= //')"
echo "  expected:    \"ccnav:spikeproj\""
tmux -L $S kill-server 2>/dev/null || true
```

```bash
chmod +x spikes/*.sh
```

- [ ] **Step 4: Run the spikes**

Run: `sh spikes/02_pane_title.sh && sh spikes/03_send_keys.sh`
Expected: `pane_title=[✳ TEST (proj)]` and `byte-exact OK`.

- [ ] **Step 5: Run the whole suite**

Run: `./run-tests`
Expected: `OK`, zero failures.

- [ ] **Step 6: Commit**

```bash
git add tests/test_integration.py spikes
git commit -m "test: real-tmux integration test and archived spikes"
```

---

## Post-implementation: enabling it

Not a task — this is what the user runs once the code is in.

1. `./bin/cc-navigator-doctor` and apply every `fix:` it prints.
2. Add the hooks to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart":     [{"matcher": "", "hooks": [{"type": "command", "command": "/data/playground/cc_navigator/bin/cc-navigator-hook"}]}],
    "UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command", "command": "/data/playground/cc_navigator/bin/cc-navigator-hook"}]}],
    "Notification":     [{"matcher": "", "hooks": [{"type": "command", "command": "/data/playground/cc_navigator/bin/cc-navigator-hook"}]}],
    "Stop":             [{"matcher": "", "hooks": [{"type": "command", "command": "/data/playground/cc_navigator/bin/cc-navigator-hook"}]}],
    "PreToolUse":       [{"matcher": "AskUserQuestion|ExitPlanMode", "hooks": [{"type": "command", "command": "/data/playground/cc_navigator/bin/cc-navigator-hook"}]}]
  }
}
```

3. For each project, `tmux new-session -s <project>` in its own gnome-terminal window, then
   restart Claude inside the pane with `claude --resume`.
4. `./bin/cc-navigator &`
