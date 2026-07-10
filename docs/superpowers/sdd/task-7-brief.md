# Task 7 brief: GNOME window activation with independent verification

BASE commit: `cb114a4` (feat/cc-navigator) — confirm with `git log -1`.
Plan: `docs/superpowers/plans/2026-07-10-cc-navigator.md`, section `### Task 7`
(lines 1053-1399). The plan is authoritative if it disagrees with this brief.

## Global Constraints (bind every task)

- **Interpreter is `/usr/bin/python3` (3.8.10).** Never invoke bare `python3`.
- **Zero third-party dependencies.** Stdlib `unittest` only.
- Python 3.8: no `match`, no `X | Y` runtime annotations. `src/` modules start with
  `from __future__ import annotations`. Test files do not.
- **Never trust an API's self-report.** This task *is* that principle.
- Outer window title `ccnav:<tmux_session_name>` is the **address**, compared with
  `===`. tmux's `pane_title` is display only and is never parsed.
- Test command: `./run-tests` (it sets `PYTHONDONTWRITEBYTECODE=1`; leave that alone).

## Context — read this carefully, it is the whole point of the module

cc_navigator has to move the user's focus to a specific gnome-terminal window. On
this machine (GNOME 3.36) the only lever is `org.gnome.Shell.Eval`, and **two
separate APIs on that path lie about succeeding**. Both were found empirically
during design, and both are recorded in the spec's Appendix A:

1. **`gdbus` exits 0 even when Eval returns `(false, ...)`.** A `ReferenceError`
   inside the JS comes back as exit code 0 with `(false, 'ReferenceError: ...')`
   on stdout. Checking the return code proves nothing. The stdout must be parsed.

2. **`win.activate(0)` returns normally, and Eval reports success, while doing
   nothing at all** when the window lives on another workspace. `global.get_current_time()`
   returns 0, which is not a valid X timestamp. The fix is
   `Main.activateWindow(w)` or `w.activate(global.display.get_current_time_roundtrip())`.

So the module's structure is: perform the action through one channel (Eval), then
**verify the effect through a different channel** (`xprop`, reading
`_NET_ACTIVE_WINDOW` and then that window's `_NET_WM_NAME`). If the effect did not
happen, retry once with the timestamp variant. Report success only when xprop —
not Eval — says focus moved.

One more rule: only the **first** matching window is activated, but **all** matches
are counted and the count is returned. Two windows titled `ccnav:foo` means two
clients attached to one tmux session, which the design forbids; the caller (Task 9)
warns rather than raising an arbitrary window.

Tasks 1-6 are committed; HEAD is `cb114a4`. `proc.run_command` already exists and is
the only subprocess call site — reuse it for both `gdbus` and `xprop`.

## Files

- Create: `src/ccnav/gnome.py`
- Test: `tests/test_gnome.py`

## Interface

- `gnome.escape_js(value: str) -> str`
- `gnome.activate_js(title: str) -> str`
- `gnome.activate_ts_js(title: str) -> str`
- `gnome.parse_eval_result(stdout: str) -> Tuple[bool, str]`
- `gnome.parse_match_count(stdout: str) -> int`
- `gnome.eval_js(js: str, run=...) -> Tuple[bool, str]`
- `gnome.eval_available(run=...) -> bool`
- `gnome.active_window_title(run=...) -> Optional[str]`
- `gnome.ActivationResult` — frozen dataclass, fields `ok: bool`, `matched: int`
- `gnome.activate_window_titled(title, run=..., sleep=..., timeout: float = 1.5) -> ActivationResult`

## This is a TDD task.

### Step 1: Write the failing test

Use `tests/test_gnome.py` exactly as the plan gives it at lines 1083-1230. Copy it
verbatim; it is 20 tests across seven classes.

### Step 2: Run it and capture the failure

`./run-tests -k EscapeJsTest -k ActivateJsTest -k ParseEvalResultTest -k ParseMatchCountTest -k EvalAvailableTest -k ActiveWindowTitleTest -k ActivateWindowTitledTest`

Expected: `ModuleNotFoundError: No module named 'ccnav.gnome'`. RED evidence.

### Step 3: Write the implementation

Use the plan's code at lines 1241-1387 verbatim. Note especially:

- `escape_js` replaces `\\` **first**, then `'`, then newlines. Order matters.
- `_match_first_js` is shared by both `activate_js` and `activate_ts_js` so the
  match-and-count loop exists once.
- `eval_js` parses stdout; it never trusts the exit code alone.
- `activate_window_titled` ignores Eval's `ok` entirely and believes only `xprop`.

### Step 4: Green

Same `-k` command → `Ran 20 tests` / `OK`.
Then `./run-tests` → `Ran 87 tests` / `OK` (67 existing + 20). Output pristine.

### Step 5: Mutation testing — mandatory

Break the implementation, confirm a **named** test fails, restore. At minimum:

1. In `escape_js`, move the backslash replacement to **last**.
2. In `_match_first_js`, compare titles with `==` instead of `===`.
3. In `_match_first_js`, drop the `if(!found)` guard so the **last** match wins.
4. In `parse_eval_result`, return `text.startswith("(")` instead of `"(true"`.
5. In `eval_js`, delete the `if code != 0: return False, out` guard.
6. In `activate_window_titled`, delete the retry — return after the first
   `_wait_for_focus`.
7. Make `_wait_for_focus` return `True` unconditionally.

**I expect mutation 5 to survive**, and I want you to check rather than take my
word. Think about what `parse_eval_result("")` returns when `gdbus` is missing and
`run` gave you `(127, "")`. If the guard is genuinely unobservable through the
plan's tests, say so and name the test that is missing. Do **not** add it unless I
ask. A truthful survivor is worth more than a clean table — the Task 5 implementer
reported one and it exposed a real hole.

### Step 6: Read-only reality check against the actual desktop

The unit tests feed canned strings. Two of them encode assumptions about what real
tools print, and those assumptions are cheap to check **without touching anyone's
focus**. Do exactly these two, and nothing else:

```bash
# a) Does Eval really answer in the (true, '...') shape this parser expects?
gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
  --method org.gnome.Shell.Eval "1+1"; echo "exit=$?"

# b) Does a deliberately broken expression really come back exit 0 + (false, ...)?
gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
  --method org.gnome.Shell.Eval "Shell.nonsense"; echo "exit=$?"

# c) What does xprop actually print here?
xprop -root _NET_ACTIVE_WINDOW
xprop -id "$(xprop -root _NET_ACTIVE_WINDOW | sed 's/.*# *//; s/,.*//')" _NET_WM_NAME
```

Paste the raw output into your report. Then state, for each, whether
`parse_eval_result`, `parse_match_count`, and `active_window_title` handle that
exact text. If `DISPLAY` is unset in your environment, say so and skip (c) rather
than guessing.

**Do not activate, raise, move, or close any window. Do not open a terminal. Do not
switch workspaces.** The user may be at the keyboard. Focus-stealing verification is
deliberately deferred to the Task 12 spike, which the user runs by hand. If you feel
you cannot verify the module without stealing focus, say so in your report and stop
— do not do it anyway.

### Step 7: Two questions I want answered in the report

**a) The blocking cost.** `activate_window_titled` polls `xprop` every 100 ms for up
to `timeout` seconds, twice (once per attempt). With the default `timeout=1.5`,
what is the worst-case wall time of a single call? Task 10 will call this from a GTK
callback. State the number. Do not fix anything.

**b) Titles containing `=`.** `active_window_title` does `out.split("=", 1)[1]`.
Reason about — and if you can, demonstrate with a canned string — whether a window
title containing `=` or `"` survives that parse. Our own titles are
`ccnav:<session>` so they are safe, but the *currently focused* window when we
probe may be anything on the user's desktop. Does a hostile title cause a wrong
answer, an exception, or a harmless `None`? An exception here would propagate into
the GTK main loop. Report your verdict.

### Step 8: Commit

```bash
git add src/ccnav/gnome.py tests/test_gnome.py
git commit -m "feat: activate window by title and verify focus through xprop"
```

## Notes and traps

- `Shell` is **not** defined in Eval's scope on GNOME 3.36. `global` and `Main` are.
  That is why probe (b) above returns `(false, 'ReferenceError: ...')` with exit 0.
- Never emit `activate(0)`. `test_retry_variant_uses_a_roundtrip_timestamp` pins it.
- Do not add a third retry, exponential backoff, or a cache of window ids. YAGNI.
- Do not import `gi` here. This module shells out on purpose; the GTK dependency
  lives only in `ui.py` (Task 9).
- Work from `/data/playground/cc_navigator`.
