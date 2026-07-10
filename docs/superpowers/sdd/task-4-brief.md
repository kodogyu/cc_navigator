# Task 4 brief: Hook shim entry point

BASE commit: `7d9efbc` (feat/cc-navigator)
Plan: `docs/superpowers/plans/2026-07-10-cc-navigator.md`, section `### Task 4`
(lines 501-733). The plan is authoritative if it ever disagrees with this brief.

## Global Constraints (bind every task)

- **Interpreter is `/usr/bin/python3` (3.8.10).** `which python3` is Anaconda and
  has no `gi`. Never invoke bare `python3`.
- **Zero third-party dependencies.** Stdlib `unittest` only.
- Python 3.8: no `match`, no `X | Y` runtime annotations. Every module under `src/`
  starts with `from __future__ import annotations`. Test files do not.
- **Never trust an API's self-report.** Verify by running code.
- **The hook must never block or fail Claude Code. It exits 0 on every path.**
  This is the single most important invariant in the whole project.
- Test command: `./run-tests`.

## Context

cc_navigator is an always-on-top GTK window listing every live Claude Code
session. This task is the program Claude Code actually invokes: a hook shim that
reads a JSON payload on stdin, decides what it means, and writes one state file.

It sits on top of three modules that are already committed and tested:
- `src/ccnav/hookstate.py` — `classify(payload) -> Optional[Tuple[state, reason]]`
- `src/ccnav/statestore.py` — `write(state_dir, record)`, `is_safe_session_id(id)`
- `src/ccnav/paths.py` — `ensure_state_dir()`

The shim's contract is asymmetric: it is allowed to do nothing, but it is never
allowed to fail. cc_navigator not running, no state directory, a malformed
payload, a full disk — all of these must end in `exit 0` with Claude Code none
the wiser. A session that is not inside tmux simply has no addressable location,
so the shim writes nothing and exits 0.

`$TMUX` looks like `/tmp/tmux-1000/default,4039841,0` — the socket path is the
first comma-separated field. Hook subprocesses are `setsid`'d (no controlling
tty) but **inherit Claude Code's full environment**, including `TMUX` and
`TMUX_PANE`. That inheritance is what makes the whole design possible; it was
verified empirically during design (see the spec's Appendix A).

## Files

- Create: `src/ccnav/hook.py`
- Create: `bin/cc-navigator-hook` (executable)
- Test: `tests/test_hook.py`

## Interface

- Consumes: `hookstate.classify`, `statestore.write`, `statestore.is_safe_session_id`, `paths.ensure_state_dir`.
- Produces:
  - `hook.MESSAGE_LIMIT: int`
  - `hook.tmux_socket_from_env(env: Mapping[str, str]) -> Optional[str]`
  - `hook.build_record(payload: Dict[str, object], env: Mapping[str, str], now: int) -> Optional[Dict[str, object]]`
  - `hook.main() -> int` — always returns 0

## This is a TDD task. Follow the steps in order.

### Step 1: Write the failing test

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
        self.assertIsNone(hook.build_record(payload=PAYLOAD, env=env, now=1))

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

(If `build_record(payload=..., env=..., now=...)` as keywords does not match your
signature, use positional args as in the other tests. The plan's version is
positional; either is fine as long as it runs.)

### Step 2: Run the test to verify it fails

Run: `./run-tests -k TmuxSocketTest -k BuildRecordTest`
Expected: FAIL with `ModuleNotFoundError: No module named 'ccnav.hook'`

Capture the output — RED evidence.

### Step 3: Write the minimal implementation

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

Then: `chmod +x bin/cc-navigator-hook`

### Step 4: Run the test to verify it passes

`./run-tests -k TmuxSocketTest -k BuildRecordTest` → `Ran 10 tests` / `OK`

### Step 5: You are authorised to add tests for `main()`

The plan checks `main()` only by hand (Steps 6-7 below). "Always exits 0" is the
project's hardest constraint, so it deserves automated cover. **This is not scope
creep — it is explicitly requested.** Add to `tests/test_hook.py` a class covering:

- `main()` returns `0` when stdin is not JSON.
- `main()` returns `0` when stdin is empty.
- `main()` returns `0` when the payload is a JSON list, not an object.
- `main()` returns `0` when `statestore.write` raises (patch it to raise `OSError`).
- `main()` returns `0` and **writes nothing** when the environment has no `TMUX`.
- The happy path: with a temp `XDG_RUNTIME_DIR` and a tmux-shaped environment,
  `main()` returns `0` and the expected `<session_id>.json` exists with the
  expected `state`/`reason`.

Drive stdin with `mock.patch("sys.stdin", io.StringIO(...))` and the environment
with `mock.patch.dict(os.environ, ..., clear=True)`. Point `XDG_RUNTIME_DIR` at a
`tempfile.TemporaryDirectory()` so nothing touches the real state directory.

Keep these tests honest: assert on the file that lands on disk, not on a mock's
call args.

### Step 6: Verify the shim end-to-end by hand

```bash
mkdir -p /tmp/ccnav-manual
printf '%s' '{"hook_event_name":"Stop","session_id":"abc-123","cwd":"/proj"}' \
  | XDG_RUNTIME_DIR=/tmp/ccnav-manual TMUX=/tmp/tmux-1000/default,1,0 TMUX_PANE=%12 \
    ./bin/cc-navigator-hook
cat /tmp/ccnav-manual/cc-navigator/abc-123.json; echo
rm -rf /tmp/ccnav-manual
```

Expected: a JSON object with `"state": "waiting"`, `"reason": "idle"`,
`"tmux_pane": "%12"`. Paste the actual output into your report.

### Step 7: Verify it exits 0 with garbage input

```bash
printf 'not json' | ./bin/cc-navigator-hook; echo "exit=$?"
printf '{}'       | ./bin/cc-navigator-hook; echo "exit=$?"
printf ''         | ./bin/cc-navigator-hook; echo "exit=$?"
```

Expected: `exit=0` all three times. Also confirm nothing is printed to stdout or
stderr. Paste the actual output.

### Step 8: Prove your tests are not vacuous

Break the implementation, confirm a *named* test fails, restore. At minimum:

1. `tmux_socket_from_env` returns the whole `$TMUX` instead of the first field.
2. Drop the `if not pane or not socket` guard.
3. Drop the `is_safe_session_id` check in `build_record`.
4. Drop the `[:MESSAGE_LIMIT]` slice.
5. Make `main()` return `1` instead of `0` when `json.load` raises.
6. Remove the `try/except` around `statestore.write`.

Record which named test caught each. A survivor is a finding — report it, name the
missing test, and do not invent a contorted test to chase it.

Restore exactly (`git checkout -- src/ccnav/hook.py`) and confirm `./run-tests` is
green and `git status` is clean before committing.

### Step 9: Commit

```bash
git add src/ccnav/hook.py bin/cc-navigator-hook tests/test_hook.py
git commit -m "feat: hook shim writes session state and always exits 0"
```

## Notes and traps

- `bin/cc-navigator-hook` deliberately does **not** `exec`. It runs python, throws
  away stderr, and then unconditionally `exit 0`. Do not "clean this up" into an
  `exec` — that would propagate python's exit status to Claude Code.
- `2>/dev/null` in the shim is intentional. A traceback on stderr would surface in
  Claude Code's UI.
- The shim must work no matter the caller's CWD; that is what `here=$(cd ...)` is
  for. Test it from a different directory.
- `main()` reading `os.environ` directly (rather than taking it as a parameter) is
  intentional — `build_record` is the testable seam.
- Do not add logging, a `--debug` flag, or a lockfile. YAGNI.
- Do not add a `SessionEnd` branch. `statestore.prune` handles disappearance.
- Work from `/data/playground/cc_navigator`.
