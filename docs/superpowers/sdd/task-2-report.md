# Task 2 Report: Hook event classification

## What I implemented

Created `src/ccnav/hookstate.py`, the pure decision function that maps a hook
payload dict to `(state, reason)` or `None`, following the brief verbatim.

- `hookstate.WAITING = "waiting"`, `hookstate.WORKING = "working"`.
- `hookstate.classify(payload: Dict[str, object]) -> Optional[Tuple[str, str]]`:
  - `SessionStart` / `UserPromptSubmit` -> `(WORKING, "")`.
  - `Notification` -> `(WAITING, reason)` where `reason` is `notification_type`
    if present and truthy, else the literal string `"notification"`.
  - `PreToolUse` -> `(WAITING, "question")` for `tool_name == "AskUserQuestion"`,
    `(WAITING, "plan")` for `"ExitPlanMode"`, `None` for every other tool
    (via the `_WAITING_TOOLS` lookup dict).
  - `Stop` -> `(WAITING, "idle")`.
  - Anything else (`SubagentStop`, unknown events, missing `hook_event_name`)
    -> `None`.
- No `try/except` — relies on `.get()` returning `None` for missing keys, as
  instructed by the brief's trap note.
- Module opens with `from __future__ import annotations`, matching the
  Task 1 pattern in `src/ccnav/paths.py`.

`tests/test_hookstate.py` — `ClassifyTest`, 12 cases. All 11 cases from the
brief's fixture, plus one I added:
`test_notification_with_empty_type_still_waits`, which sends
`{"hook_event_name": "Notification", "notification_type": ""}` and asserts
`(WAITING, "notification")`. The brief's own trap note says the "present but
empty" case must be covered and to add a test if it is not — the given
`test_notification_without_a_type_still_waits` only omits the key entirely, it
never sends `""`, so I added the sibling case rather than changing the
implementation. Test file otherwise matches the brief exactly (no
`from __future__ import annotations`, following the `test_paths.py` pattern
for test files).

## What I tested and the results

- `./run-tests -k ClassifyTest`: 12/12 passing.
- `./run-tests` (full suite): 19/19 passing (7 from Task 1 + 12 here — one more
  than the brief's predicted 18, accounted for by the added empty-string test).
- Output pristine both times: no warnings, no stray prints, no stderr.

## TDD Evidence

### RED

Command:

    ./run-tests -k ClassifyTest

Output:

    test_hookstate (unittest.loader._FailedTest) ... ERROR

    ======================================================================
    ERROR: test_hookstate (unittest.loader._FailedTest)
    ----------------------------------------------------------------------
    ImportError: Failed to import test module: test_hookstate
    Traceback (most recent call last):
      File "/usr/lib/python3.8/unittest/loader.py", line 436, in _find_test_path
        module = self._get_module_from_name(name)
      File "/usr/lib/python3.8/unittest/loader.py", line 377, in _get_module_from_name
        __import__(name)
      File "/data/playground/cc_navigator/tests/test_hookstate.py", line 3, in <module>
        from ccnav import hookstate
    ImportError: cannot import name 'hookstate' from 'ccnav' (/data/playground/cc_navigator/src/ccnav/__init__.py)

    ----------------------------------------------------------------------
    Ran 1 test in 0.000s

    FAILED (errors=1)

Why expected: `src/ccnav/hookstate.py` did not exist yet, so
`from ccnav import hookstate` cannot resolve. Same benign variance as Task 1's
RED evidence: the brief predicted `ModuleNotFoundError: No module named
'ccnav.hookstate'`, but because `ccnav/__init__.py` already exists as a
package, the failure surfaces as `ImportError: cannot import name 'hookstate'
from 'ccnav'` instead — same root cause (module absent), which is exactly what
the test is meant to force before implementation exists.

### GREEN

Command:

    ./run-tests -k ClassifyTest

Output:

    test_ask_user_question_waits (test_hookstate.ClassifyTest) ... ok
    test_exit_plan_mode_waits (test_hookstate.ClassifyTest) ... ok
    test_missing_event_name_is_ignored (test_hookstate.ClassifyTest) ... ok
    test_notification_carries_its_type_as_reason (test_hookstate.ClassifyTest) ... ok
    test_notification_with_empty_type_still_waits (test_hookstate.ClassifyTest) ... ok
    test_notification_without_a_type_still_waits (test_hookstate.ClassifyTest) ... ok
    test_other_tools_are_ignored (test_hookstate.ClassifyTest) ... ok
    test_session_start_is_working (test_hookstate.ClassifyTest) ... ok
    test_stop_is_idle_waiting (test_hookstate.ClassifyTest) ... ok
    test_subagent_stop_is_ignored (test_hookstate.ClassifyTest) ... ok
    test_unknown_event_is_ignored (test_hookstate.ClassifyTest) ... ok
    test_user_prompt_submit_is_working (test_hookstate.ClassifyTest) ... ok

    ----------------------------------------------------------------------
    Ran 12 tests in 0.000s

    OK

Full suite (`./run-tests`): `Ran 19 tests` / `OK`.

## Files changed

Commit `9f9ba72` "feat: classify hook events into session states":

    src/ccnav/hookstate.py  | 37 +++++++++++++++++++++++++++++++++++++
    tests/test_hookstate.py | 62 +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    2 files changed, 99 insertions(+)

(Note: a pre-existing, unrelated modification to
`figures/cc_navigator 화면.png` was left untouched and unstaged — not part of
this task.)

## Self-review findings

**Completeness:** All interface symbols produced (`WAITING`, `WORKING`,
`classify`). No `SessionEnd` branch, no extra events, no extra reasons — YAGNI
respected per the brief's explicit instruction.

**Quality:** Names match the brief exactly. Docstrings preserved verbatim,
explaining the "why" (e.g. why `Notification` has no matcher, why
`SubagentStop` returns `None`).

**Discipline:** Implementation is copied verbatim from the brief's Step 3 —
no embellishment. Only addition beyond the brief is the one extra test case,
explicitly invited by the brief's own trap note.

**Testing — non-vacuity check (required by task instructions):**
- `notification_type` fallback: if the `or "notification"` were deleted
  (leaving `reason = payload.get("notification_type")`), then for the
  missing-key case `reason` would be `None` and `str(None) == "None"`, and for
  the empty-string case `reason` would stay `""`. Both
  `test_notification_without_a_type_still_waits` and
  `test_notification_with_empty_type_still_waits` assert the reason equals the
  literal `"notification"`, so both would fail. Not vacuous.
- `_WAITING_TOOLS` lookup: if the dict were emptied, `test_ask_user_question_waits`
  and `test_exit_plan_mode_waits` would get `None` instead of the expected
  waiting tuples, and fail. If the branch condition were inverted (`not in`
  instead of `in`), `test_other_tools_are_ignored` (currently passing because
  `"Bash"` is absent from the dict) would instead return a waiting tuple and
  fail. Both directions of the lookup are covered. Not vacuous.

Output confirmed pristine on every run (no warnings, no prints, no stderr).

## Issues or concerns

None. Work matches the brief exactly plus one test case explicitly invited by
the brief's trap note.
