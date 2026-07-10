# Task 9 brief: The overlay window

BASE commit: `444a85d` (feat/cc-navigator) — confirm with `git log -1`.
Plan: `docs/superpowers/plans/2026-07-10-cc-navigator.md`, section `### Task 9`
(lines 1613-1896). The plan is authoritative if it disagrees with this brief.

## Global Constraints (bind every task)

- **Interpreter is `/usr/bin/python3` (3.8.10).** `which python3` is Anaconda and has
  no PyGObject. This task is the reason that rule exists. Never invoke bare `python3`.
- **Zero third-party dependencies.** GTK comes from the system `gi`, not from pip.
- Python 3.8: no `match`, no `X | Y` runtime annotations. `src/` modules start with
  `from __future__ import annotations`. Test files do not.
- **Never trust an API's self-report** — including your own tests.
- Test command: `./run-tests` (it sets `PYTHONDONTWRITEBYTECODE=1`; leave that).

## SAFETY — this task can put pixels on the user's screen

`DISPLAY=:1` is live and `Gtk.init_check()` succeeds, so a window you show will
appear on the user's actual desktop, on top of whatever they are doing.

- **Never call `show()`, `show_all()`, or `present()` on the `NavigatorWindow` itself.**
- `set_rows` calls `self._listbox.show_all()`. That is fine: the listbox's toplevel
  is never shown, so nothing is realized on screen.
- Do not `move()` or `resize()` a shown window. Do not grab focus.
- If you want to see it, describe what you would run and let me decide. Do not run it.

## Context

This is the window from the user's mockup: a small always-on-top panel in the
top-right listing every live Claude Code session, with a red dot and a
"Waiting input" badge on any session that needs attention. Selecting a row reveals
a one-line text entry and a "해당 세션으로 이동" (jump) button.

All formatting lives in **pure functions above the widgets** — `secondary_line` and
`compose_status` — so the GTK class stays thin and testable without a display.

The status bar has **three independent slots** because they co-occur and must not
clobber one another:

- `sticky` — Eval is unavailable; set once at startup, must never be overwritten.
- `hint` — the empty-list message; driven by `set_rows`.
- `transient` — a jump failure or warning; set by the caller.

This three-slot design exists because a pre-flight review of the plan caught
`set_rows([])` silently wiping the "Eval unavailable" warning. Do not collapse them.

`NavigatorWindow` **does not** connect `destroy` to `Gtk.main_quit`. Task 10 wires
that in `app.main()`, where a main loop actually exists. The same pre-flight review
caught that too: connecting it here made the widget's own test emit `Gtk-CRITICAL`.

Tasks 1-8 are committed; HEAD is `444a85d`. `model.Row` gives you `.waiting` and
`.window_title`.

## Files

- Create: `src/ccnav/ui.py`
- Test: `tests/test_ui.py`

## Interface

- `ui.SECONDARY_LIMIT: int`, `ui.EMPTY_HINT: str`, `ui.EVAL_UNAVAILABLE_HINT: str`
- `ui.secondary_line(row: model.Row) -> str`
- `ui.compose_status(sticky: str, hint: str, transient: str) -> str`
- `ui.NavigatorWindow(on_jump, on_send)` with `set_rows(rows)`, `set_status(text)`,
  `set_eval_available(available)`

## This is a TDD task.

### Step 1: Write the failing test

Use `tests/test_ui.py` exactly as the plan gives it at lines 1635-1705 — twelve tests
across three classes. `NavigatorWindowTest` is `@unittest.skipUnless(os.environ.get("DISPLAY"), ...)`.

### Step 2: Run it, capture the failure

`./run-tests -k SecondaryLineTest` → `ModuleNotFoundError: No module named 'ccnav.ui'`.

### Step 3: Implement

Use the plan's code at lines 1716-1884 verbatim.

### Step 4: Green

`./run-tests -k SecondaryLineTest -k ComposeStatusTest -k NavigatorWindowTest`
→ `Ran 12 tests` / `OK`.
Then `./run-tests` → `Ran 116 tests` / `OK` (104 existing + 12).

**Output must be pristine, and here that means something specific:** GTK warnings
are emitted by C code on file descriptor 2, not through Python's `warnings` module,
so they do not fail a test — they just scroll past. Check for `Gtk-CRITICAL`,
`Gtk-WARNING`, `Gdk-CRITICAL` and `GLib-GObject` in the full output. If any appear,
they are a finding, not noise.

### Step 5: You are authorised to strengthen `NavigatorWindowTest`

The plan's window test only constructs the window and calls three setters. It
asserts nothing. Two properties deserve real assertions, and both have already been
broken once during plan review:

**a) The three status slots do not clobber each other.** After
`set_eval_available(False)` followed by `set_rows([])`, the status label's text must
contain **both** `EVAL_UNAVAILABLE_HINT` and `EMPTY_HINT`. Read it back with
`window._status.get_text()`. Then `set_rows([row()])` and assert `EMPTY_HINT` is gone
while `EVAL_UNAVAILABLE_HINT` remains.

**b) Destroying the window does not touch a main loop.** Capture C-level stderr
around `window.destroy()` by `os.dup2`-ing file descriptor 2 to a temp file, and
assert nothing was written. If `NavigatorWindow` ever reconnects `destroy` to
`Gtk.main_quit`, GTK will print `gtk_main_quit: assertion 'main_loops != NULL'
failed` and this test will catch it. Flush and restore fd 2 in a `finally`.

Also assert `len(window._listbox.get_children())` matches the number of rows passed.

Touching `_status` and `_listbox` from a test is reaching into privates; that is
acceptable here because the alternative is a widget with no behavioural test at all.
Say so in a comment.

### Step 6: Mutation testing — mandatory

Break the implementation, confirm a **named** test fails, restore:

1. `compose_status` joins with one space instead of two.
2. `compose_status` drops the `if part` filter.
3. `secondary_line` omits `.rstrip("/")`.
4. `secondary_line` truncates each part before joining rather than the joined string.
5. `set_eval_available(True)` sets `_sticky = EVAL_UNAVAILABLE_HINT` (inverted).
6. `set_rows` always sets `_hint = EMPTY_HINT`, even for a non-empty list.
7. `NavigatorWindow.__init__` connects `destroy` to `Gtk.main_quit`.

Mutations 5, 6 and 7 should be caught only by the assertions you add in Step 5. If
any mutation survives even so, **report it and name the missing test.** Three
implementers before you found real survivors by checking rather than assuming; every
one exposed a hole in the plan.

### Step 7: Three questions I need answered. Report, do not fix.

**a) The entry is destroyed while the user types.** `set_rows` removes every child
and rebuilds. Task 10 will call it on a one-second timer. Trace what happens to a
`Gtk.Entry` the user is halfway through typing into, and to the row selection that
revealed it. Does the text survive? Does focus? Does the revealer stay open? State
plainly whether cc_navigator, as planned, would let anyone actually type a message
into a waiting session. This is the feature the user asked for by name, so I need a
clear yes or no, and if no, the smallest change that fixes it.

**b) `set_eval_available` after `set_rows` does nothing to existing buttons.**
`_build_row` reads `self._eval_available` once, at construction. If Task 10 calls
`set_eval_available(False)` after the first `set_rows`, are the jump buttons
sensitive or not? Determine it, do not guess.

**c) The hostname problem.** Per the Task 5 finding, tmux reports a plain shell's
`#{pane_title}` as the **hostname**, not `""`. So immediately after `SessionStart`,
before Claude Code emits its OSC title, `row.title` is a hostname and the UI's
primary line shows it. Is that acceptable, or should `_build_row` fall back to
something better? Give a recommendation with reasoning; do not implement it.

### Step 8: Commit

```bash
git add src/ccnav/ui.py tests/test_ui.py
git commit -m "feat: always-on-top overlay listing sessions"
```

## Notes and traps

- `GLib.markup_escape_text` on every string that reaches `set_markup`. A pane title
  is arbitrary UTF-8 from Claude Code and will contain `&` and `<` sooner or later.
  Both `title` and `secondary_line` go through it. Do not remove either.
- `Pango.EllipsizeMode.END`, not the integer `3`.
- No `destroy` → `Gtk.main_quit` in this class. Task 10 owns the loop.
- Do not import `tmuxctl`, `gnome`, or `statestore` here. The window takes callbacks
  and rows; it performs no actions itself. That is what makes it testable.
- Do not add a tray icon, a settings dialog, keyboard shortcuts, or a refresh button.
  YAGNI.
- Work from `/data/playground/cc_navigator`.
