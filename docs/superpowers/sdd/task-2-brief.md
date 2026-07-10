# Task 2 brief: Hook event classification

BASE commit: d610319 (feat/cc-navigator)
Plan: `docs/superpowers/plans/2026-07-10-cc-navigator.md`, section `### Task 2`
(lines 139-274). Read that section — the text below is the same content, but the
plan is authoritative if they ever disagree.

## Global Constraints (bind every task)

- **Interpreter is `/usr/bin/python3` (3.8.10).** `which python3` is Anaconda and
  has no `gi`. Never invoke bare `python3`.
- **Zero third-party dependencies.** `pytest` is not installed. Use stdlib `unittest`.
- Python 3.8: no `match`, no `X | Y` runtime annotations. Every module under `src/`
  starts with `from __future__ import annotations`. (Test files do not — follow the
  existing pattern in `tests/test_paths.py`.)
- **Never trust an API's self-report.**
- **The hook must never block or fail Claude Code.** It exits 0 on every path.
- User text sent to tmux is always one `argv` element after `-l --`, never
  interpolated into a shell string.
- Outer window title `ccnav:<tmux_session_name>` is the **address**, compared with
  `===`. tmux's `pane_title` is **display only** and is never parsed.
- Test command: `./run-tests` (add `-k ClassifyTest` to focus).

## Context

`cc_navigator` is a small always-on-top GTK window that lists every live Claude
Code session and highlights the ones waiting for input. Claude Code hooks fire a
shim (`bin/cc-navigator-hook`, Task 4) on every interesting event; the shim writes
one JSON file per session into the state directory (Task 3).

This task is the pure decision function at the heart of that shim: given a hook
payload, is this session now *working* or *waiting*, and why? It does no I/O so
the whole state machine is testable from fixtures. Task 3 (state store) and Task 4
(shim) build directly on it.

The field names come from real hook payloads captured during design verification:
`hook_event_name`, `notification_type`, `tool_name`.

## Files

- Create: `src/ccnav/hookstate.py`
- Test: `tests/test_hookstate.py`

## Interface

- Consumes: nothing.
- Produces:
  - `hookstate.WAITING: str`
  - `hookstate.WORKING: str`
  - `hookstate.classify(payload: Dict[str, object]) -> Optional[Tuple[str, str]]`
    returning `(state, reason)`, or `None` when the event carries no state change.

## This is a TDD task. Follow the steps in order.

### Step 1: Write the failing test

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

### Step 2: Run the test to verify it fails

Run: `./run-tests -k ClassifyTest`
Expected: FAIL with `ModuleNotFoundError: No module named 'ccnav.hookstate'`

Capture this output — it is your RED evidence.

### Step 3: Write the minimal implementation

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

### Step 4: Run the test to verify it passes

Run: `./run-tests -k ClassifyTest`
Expected: `Ran 11 tests` / `OK`

Then run the full suite once: `./run-tests` — expect `Ran 18 tests` / `OK`
(7 from Task 1 + 11 here). Output must be pristine: no warnings, no stray prints.

### Step 5: Commit

```bash
git add src/ccnav/hookstate.py tests/test_hookstate.py
git commit -m "feat: classify hook events into session states"
```

## Notes and traps

- `classify` must not raise on a malformed payload. `{}` and unknown event names
  return `None`. Do not add defensive `try/except` — the dict `.get` calls already
  cover it, and the shim (Task 4) has its own top-level guard.
- Do not add extra events, extra reasons, or a `SessionEnd` branch. Task 3 handles
  staleness by age; Task 8 handles liveness by checking whether the tmux pane still
  exists. YAGNI applies hard here.
- `notification_type` may be present but empty (`""`); the `or "notification"`
  fallback is deliberate and covered by
  `test_notification_without_a_type_still_waits`'s sibling case — if you find that
  case is *not* covered, add it rather than changing the implementation.
- Work from `/data/playground/cc_navigator`.
