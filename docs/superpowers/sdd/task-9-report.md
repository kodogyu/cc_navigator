# Task 9 report: The overlay window

BASE commit: `444a85d`. Files created: `src/ccnav/ui.py`, `tests/test_ui.py`.

## What I implemented

- `ui.SECONDARY_LIMIT`, `ui.EMPTY_HINT`, `ui.EVAL_UNAVAILABLE_HINT` (plus
  `WINDOW_WIDTH`/`WINDOW_HEIGHT` from the plan).
- `ui.secondary_line(row)` and `ui.compose_status(sticky, hint, transient)` — pure
  functions, no GTK.
- `ui.NavigatorWindow(on_jump, on_send)` with `set_rows`, `set_status`,
  `set_eval_available`, and the private `_build_row`/`_render_status`/signal handlers.

Implementation is the plan's code verbatim (lines 1716-1884), including the
`GLib.markup_escape_text` calls on `title` and `secondary_line`, `Pango.EllipsizeMode.END`,
and the comment marking the deliberate absence of `destroy -> Gtk.main_quit`.

## TDD Evidence

**RED** (`./run-tests -k SecondaryLineTest`, before `ui.py` existed):

```
ImportError: cannot import name 'ui' from 'ccnav' (/data/playground/cc_navigator/src/ccnav/__init__.py)
Ran 1 test in 0.000s
FAILED (errors=1)
```

(Module didn't exist yet, so this manifested as `ImportError` rather than the brief's literal
`ModuleNotFoundError` wording — same root cause: `ccnav.ui` was not importable.)

**GREEN** (`./run-tests -k SecondaryLineTest -k ComposeStatusTest -k NavigatorWindowTest`):

```
Ran 15 tests in 0.102s
OK
```

15, not 12 — the plan's 12 plus the 3 additional `NavigatorWindowTest` assertions
authorised in Step 5 (see below).

Full suite (`./run-tests`):

```
Ran 119 tests in 0.413s
OK
```

104 pre-existing + 15 new = 119.

## Step 5: NavigatorWindowTest strengthening

Added three test methods (module docstring notes that reaching into `_status`/`_listbox`
is a deliberate exception, since the alternative is a widget with no behavioural test):

- `test_status_slots_do_not_clobber_each_other` — after `set_eval_available(False)` then
  `set_rows([])`, asserts `window._status.get_text()` contains **both**
  `EVAL_UNAVAILABLE_HINT` and `EMPTY_HINT`; then after `set_rows([row()])`, asserts
  `EMPTY_HINT` is gone and `EVAL_UNAVAILABLE_HINT` remains.
- `test_set_rows_populates_the_listbox_with_one_child_per_row` — asserts
  `len(window._listbox.get_children())` equals the number of rows passed (3).
- `test_destroy_does_not_touch_a_main_loop` — `os.dup2`s fd 2 to a `tempfile`, calls
  `window.destroy()`, flushes, restores fd 2, asserts the captured bytes are `b""`. Restore
  happens in a `finally`.

## Mutation Evidence

All seven applied one at a time against the real `src/ccnav/ui.py`, verified to fail a
named test, then restored via `cp` from a saved original and diff-verified clean before the
next mutation. Final `diff` against the pristine file after all seven: no output.

| # | Mutation | Caught by | Result |
|---|----------|-----------|--------|
| 1 | `compose_status` joins with one space instead of two | `ComposeStatusTest.test_all_three_slots_are_shown`, `test_the_eval_warning_survives_an_empty_list` | CAUGHT |
| 2 | `compose_status` drops the `if part` filter | `ComposeStatusTest.test_empty_slots_are_dropped`, `test_the_eval_warning_survives_an_empty_list`, `test_all_empty_is_empty` | CAUGHT |
| 3 | `secondary_line` omits `.rstrip("/")` | `SecondaryLineTest.test_working_tolerates_a_trailing_slash` | CAUGHT |
| 4 | `secondary_line` truncates each part before joining, not the joined string | `SecondaryLineTest.test_long_secondary_line_is_truncated` (line length 100 > 80) | CAUGHT |
| 5 | `set_eval_available(True)` sets `_sticky = EVAL_UNAVAILABLE_HINT` (inverted) | `NavigatorWindowTest.test_status_slots_do_not_clobber_each_other` (Step 5 assertion) | CAUGHT |
| 6 | `set_rows` always sets `_hint = EMPTY_HINT` | `NavigatorWindowTest.test_status_slots_do_not_clobber_each_other` (Step 5 assertion) | CAUGHT |
| 7 | `NavigatorWindow.__init__` connects `destroy` to `Gtk.main_quit` | `NavigatorWindowTest.test_destroy_does_not_touch_a_main_loop` (Step 5 fd-2 capture) | CAUGHT |

Mutation 7 is worth spelling out: with the mutation applied, **every** `NavigatorWindowTest`
that calls `.destroy()` printed `Gtk-CRITICAL **: gtk_main_quit: assertion 'main_loops !=
NULL' failed` to real fd 2, but only `test_destroy_does_not_touch_a_main_loop` actually
failed — the other three passed anyway, because Python's assertions never look at fd 2. This
is exactly the trap the brief describes: without the dedicated fd-2 capture, this mutation
would have been a silent survivor.

No survivors. All seven mutations 5, 6, 7 were caught specifically by the Step 5 assertions,
as predicted.

## GTK warning scan

Grepped the full `./run-tests` output (`/tmp/task9_final.log`) for `Gtk-CRITICAL`,
`Gtk-WARNING`, `Gdk-CRITICAL`, `GLib-GObject`: **zero matches**.

Two unrelated, non-matching warnings are present and worth flagging even though they don't
match the four grepped patterns:
- `PyGIWarning: Gdk was imported without specifying a version first. Use
  gi.require_version('Gdk', '3.0') before import...` — a Python-level warning from PyGObject
  at import time (`ui.py` line 10), not a GTK C-level fd-2 warning. Present in the plan's
  verbatim code (only `gi.require_version("Gtk", "3.0")` is called, not for `Gdk`).
- `DeprecationWarning: Gdk.Screen.get_width is deprecated` (`ui.py` line 57, inside
  `__init__`'s screen-positioning code) — also Python-level, from the `Gdk.Screen.get_width()`
  call used to compute the window's initial x-position.

Neither is a `Gtk-CRITICAL`/`Gtk-WARNING`/`Gdk-CRITICAL`/`GLib-GObject` fd-2 message, so per
the letter of the brief's grep they are not findings, but I'm reporting them since they are
real warning noise from code introduced in this task and both were carried over verbatim from
the plan. I did not change either, per "implement exactly what the brief specifies, verbatim."

## Step 7a — can the user type into a waiting session?

**No.** Traced and empirically verified (script run against the real widget tree, never
calling `show()`/`show_all()`/`present()` on the window itself — only `_listbox.show_all()`,
which the safety section confirms is inert since the listbox's toplevel is never shown):

1. Selected a row (`_listbox.select_row(...)`), which opens its `Gtk.Revealer`
   (`reveal_child=True`) and reveals the `Gtk.Entry`.
2. Set text in the entry: `"Allow this to run please"`.
3. Called `set_rows(...)` again with the same underlying rows — simulating exactly what Task
   10's one-second poll timer will do (per the plan's Task 10 interface, `app.main()` drives
   `NavigatorWindow.set_rows` on `POLL_SECONDS`).
4. Result: the new `Gtk.ListBoxRow`, new `Gtk.Revealer`, and new `Gtk.Entry` are **different
   objects** from the ones the user was interacting with (`is` comparisons all `False`). The
   new entry's text is `''` (empty — the typed text is gone). The new revealer's
   `reveal_child` is `False` (collapsed shut). `_listbox.get_selected_row()` is `None` (no
   selection at all).

`set_rows` unconditionally does:
```python
for child in self._listbox.get_children():
    self._listbox.remove(child)
for row in rows:
    self._listbox.add(self._build_row(row))
```
Every child — including whichever one the user has selected and is typing into — is
destroyed and replaced with a freshly constructed row, whose entry always starts as empty
(`Gtk.Entry()` with no restored text) and whose revealer always starts collapsed (`Gtk.Revealer()`
defaults to `reveal_child=False` — `_on_row_selected` is what opens it, and that signal never
fires for a row nobody selected). Nothing in `set_rows` or `_build_row` looks at or preserves
prior state.

With Task 10 calling this on a 1-second timer, any user who selects a waiting session, starts
typing a reply, and takes more than ~1 second to finish and press Enter, will have their
in-progress text silently erased, the input box collapsed, and the selection cleared — with no
error, no warning, nothing indicating loss. This is the feature the user named specifically in
the mockup ("select a row, type into it, hit the jump/send controls"), and as planned it does
not work reliably.

**Smallest fix:** have `NavigatorWindow` carry the "in-flight edit" state across a `set_rows`
rebuild instead of always starting clean:

1. In `_build_row`, additionally stash the row's identity and the entry widget on the
   `Gtk.ListBoxRow`, e.g. `list_row.ccnav_session_id = row.session_id` and
   `list_row.ccnav_entry = entry` (mirrors the existing `ccnav_revealer` stash).
2. At the top of `set_rows`, before removing children, if `self._listbox.get_selected_row()`
   is not `None`, capture `(selected.ccnav_session_id, selected.ccnav_entry.get_text())`.
3. After adding the new rows, find the new `Gtk.ListBoxRow` whose `ccnav_session_id` matches
   the captured id (if any is still present in the new `rows`); if found, call
   `self._listbox.select_row(new_row)` (which already re-opens the revealer via the existing
   `_on_row_selected` handler) and `new_row.ccnav_entry.set_text(captured_text)`.

This is a handful of lines confined to `set_rows`/`_build_row`; it does not require rethinking
the destroy-and-rebuild architecture. (A more thorough fix — diff rows by `session_id` and
only add/remove/update the widgets that actually changed, never touching an untouched row's
widgets at all — would be more robust and would also remove the once-a-second flicker, but is
a bigger change than "smallest.")

## Step 7b — are jump buttons sensitive after a late `set_eval_available(False)`?

**Yes, they remain sensitive** (stale) — determined empirically, not guessed. Script: built a
window (never shown), called `set_rows([row()])` while `_eval_available` defaulted to `True`,
read the jump button's `get_sensitive()` (`True`), then called `set_eval_available(False)`,
and re-read the **same** button object's `get_sensitive()`: still `True`. A subsequent
`set_rows(...)` call (a fresh rebuild) produces a **new** button whose `get_sensitive()` is
`False`.

Reason: `_build_row` reads `self._eval_available` exactly once, at construction
(`jump.set_sensitive(self._eval_available)`), and `set_eval_available` only writes
`self._eval_available`/`self._sticky` and re-renders the status label — it never touches
`self._listbox` or iterates existing children. So an already-built jump button's sensitivity is
frozen at whatever `_eval_available` was when that row was constructed, and only the *next*
`set_rows` rebuild picks up the new value. If Task 10 calls `set_eval_available(False)` between
two poll ticks, there is a window (up to one poll interval) where the status bar already says
Eval is unavailable but the visible jump buttons are still clickable.

## Step 7c — the hostname primary line

**Recommendation: not acceptable as-is; `_build_row` (or a new pure helper next to
`secondary_line`) should fall back to something other than the raw tmux-reported title when
that title isn't a real Claude Code title.**

Reasoning:
- `model.build_rows` sets `row.title = titles.get(pane) or pane`. Per the Task 5 finding, a
  plain shell's `#{pane_title}` right after `SessionStart` (before Claude Code has emitted its
  own OSC title) is not `""` — it's the terminal's default title, i.e. the hostname — so the
  `or pane` fallback never triggers and `row.title` really is a hostname.
- `_build_row` renders `row.title` verbatim (through `GLib.markup_escape_text`, bolded) as the
  primary line of the row — the single most prominent piece of text identifying that session.
- The failure mode is worse than "briefly ugly": the hostname is a property of the **machine**,
  not the session, so every session that hasn't yet received its OSC title shows the **same**
  string. If a user starts two or three Claude Code sessions in quick succession (a completely
  normal workflow — opening several tmux windows/panes and launching Claude Code in each), the
  overlay's list will briefly show multiple rows all reading the identical hostname, with no way
  to tell them apart, in the exact moment (right after start) when the user has the least other
  context to distinguish them. That directly undermines the panel's purpose.
- The window is short-lived (self-heals once Claude Code emits its OSC title, well under
  `POLL_SECONDS`+doctor's tick), which is the argument *for* leaving it — but "briefly wrong and
  indistinguishable" during exactly the moment several sessions might be starting together is
  still a real, user-visible defect, not just cosmetic noise.
- `model.Row` already carries `tmux_session`, which is guaranteed non-empty (both
  `_newest_per_pane` and `build_rows` require a live `(socket, pane)` key backed by a real tmux
  session) and unique per row by construction — no extra tmux round trip needed. It is a
  strictly better fallback than a hostname because it's already per-session, not per-machine.
- Concretely, I'd suggest showing `row.tmux_session` in place of (or in the same line as) the
  title whenever the title doesn't look like a real Claude Code title — the existing fixtures
  and spike (`spikes/02_pane_title.sh`) show Claude Code always prefixes its OSC titles with
  "✳", so `row.title.startswith("✳")` is an available (if slightly implicit) discriminator; the
  cleaner alternative is a small width/length or prefix heuristic decided by whoever owns
  `model.py`/`ui.py` jointly. I have not implemented this, per the brief.

## `env -u DISPLAY ./run-tests` result

```
...
test_constructs_and_accepts_rows (test_ui.NavigatorWindowTest) ... skipped 'needs an X11 display'
test_destroy_does_not_touch_a_main_loop (test_ui.NavigatorWindowTest) ... skipped 'needs an X11 display'
test_set_rows_populates_the_listbox_with_one_child_per_row (test_ui.NavigatorWindowTest) ... skipped 'needs an X11 display'
test_status_slots_do_not_clobber_each_other (test_ui.NavigatorWindowTest) ... skipped 'needs an X11 display'
...
Ran 119 tests in 0.285s

OK (skipped=4)
```

All four `NavigatorWindowTest` tests skip correctly; every other test (including the pure
`SecondaryLineTest`/`ComposeStatusTest` classes) still runs and passes without a display.

## Files changed

- `src/ccnav/ui.py` (new) — `/data/playground/cc_navigator/src/ccnav/ui.py`
- `tests/test_ui.py` (new) — `/data/playground/cc_navigator/tests/test_ui.py`

## Self-review

**Completeness:** All 12 plan tests present verbatim, plus the 3 Step-5-authorised
assertions (status-slot non-clobbering, listbox child count, fd-2-captured destroy silence).
All 7 mutations applied one at a time, each caught by a named test, each restored and
diff-verified clean. All three Step 7 investigations answered with traces/empirical scripts,
not fixed.

**Quality:** Implementation is the plan's code, unmodified. Test names are descriptive
(`test_status_slots_do_not_clobber_each_other`, `test_destroy_does_not_touch_a_main_loop`,
etc.) and each Step-5 addition carries a comment explaining why reaching into `_status`/
`_listbox` is acceptable here.

**Discipline / YAGNI:** No tray icon, no settings dialog, no keyboard shortcuts, no refresh
button. No import of `tmuxctl`, `gnome`, or `statestore` in `ui.py` — it only imports `model`
and GTK. No `destroy -> Gtk.main_quit` connection anywhere in `ui.py` (verified both by
reading the file and by the mutation-7 test).

**Testing:** Nothing calls `show()`, `show_all()`, or `present()` on `NavigatorWindow` itself,
in the test suite or in the two ad hoc investigation scripts I ran for Step 7a/7b — both only
call `_listbox.show_all()` indirectly via `set_rows`, exactly as the safety note describes as
inert. `env -u DISPLAY ./run-tests` passes with the window tests skipped, confirmed above.
`wmctrl -l` after every test run showed no `cc_navigator`/`ccnav` window mapped.

**Safety:** Nothing appeared on the user's screen. I did not run `show()`/`show_all()`/
`present()` on `NavigatorWindow` at any point, including during the ad hoc Step 7a/7b
verification scripts (those scripts only touch `_listbox` and its children, per the safety
note's explicit carve-out). I did not move, resize, or focus any window, and did not grab
focus on any widget (Step 7a's suggested fix mentions `grab_focus()` as part of a
*recommendation*, not something I executed).

## Issues or concerns

- Step 7a is a real, confirmed defect in the plan as written: given Task 10's one-second poll
  timer, typing into a waiting session's entry is not reliably possible today. I did not fix
  it (out of scope per the brief), but flagging it clearly since it's the feature the user
  asked for by name.
- Step 7b is a real staleness window: up to one poll interval where the status bar says Eval
  is unavailable but existing jump buttons are still clickable.
- Step 7c: the primary-line hostname collision (identical text across multiple freshly-started
  sessions) is a real but narrow-window defect; recommending a fallback, not implementing one.
- The plan's verbatim code has two Python-level warnings (`PyGIWarning` for unversioned `Gdk`
  import, `DeprecationWarning` for `Gdk.Screen.get_width`). Neither matches the four fd-2
  patterns the brief asks to grep for, so I left the code untouched, but flagging them since
  they're new noise introduced by this task's code.

---

# Revision after review

The coordinator reproduced Step 7a independently, confirmed that any *other* session
changing forces the whole list to rebuild (so with a one-second poll the entry is
destroyed close to always), and cleared five fixes on top of `5a8abaa`. All five are
implemented in a second commit. Nothing renders on the user's screen; no `show()`,
`show_all()`, or `present()` on the window.

## What changed in `src/ccnav/ui.py`

1. **No rebuild when nothing changed.** `set_rows` computes a `signature` — a tuple, in
   order, of `(session_id, socket, pane, tmux_session, title, state, reason, message, cwd)`
   per row — and returns early when it equals `self._signature` (initialised to `None` in
   `__init__` so the first call always renders). `updated_at` is deliberately excluded: the
   hook never bumps a timestamp without also changing `state` or `reason`, and a reordering
   changes the tuple order anyway, so ordering is still covered.
   - **Stale-hint analysis (the coordinator asked me to reason about it):** `_hint` is a pure
     function of emptiness, and emptiness is fully encoded in the signature (the signature is
     the empty tuple iff `rows` is empty). `_hint` and `_signature` are only ever written
     together in the non-early-return path, so they are always consistent; an unchanged
     signature therefore means `_hint` is already correct and the early return cannot strand
     it. `_sticky` and `_transient` are owned by `set_eval_available`/`set_status`, which each
     re-render independently, so the early return cannot strand those either. **Conclusion: it
     cannot strand a stale hint; no extra code needed.** This is recorded in a code comment.

2. **Preserve the in-flight edit across a rebuild that does happen.** Before clearing children,
   `set_rows` captures from the selected row its `ccnav_row.session_id`, its entry text, and
   whether the entry held focus (`has_focus()`). After rebuilding, it finds the row with the
   same `session_id`, `select_row`s it (which re-reveals it via `_on_row_selected`), restores
   the text, puts the cursor at the end (`set_position(-1)`), and re-grabs focus only if it had
   focus. If that session is gone, nothing is restored. `_build_row` now stashes `ccnav_row`
   and `ccnav_entry` on the `Gtk.ListBoxRow` (the pre-flight review had removed `ccnav_row` as
   unused; a comment records that it is used now).

3. **`set_eval_available` reaches existing buttons.** `_build_row` still stashes `ccnav_jump`,
   and `set_eval_available` iterates the current children calling `set_sensitive(available)` on
   each, so a late `set_eval_available(False)` disables buttons that already exist — not just
   future ones. (Construction still reads `_eval_available` too, so rows built later are also
   correct.)

4. **Hostname fallback — new pure function `primary_line(row, hostname=None)`.** Returns
   `row.tmux_session` when the title is empty, equals the pane id, equals the hostname, or
   equals the hostname's first dot-component; otherwise returns the title. `hostname` defaults
   to a module-level `_HOSTNAME = socket.gethostname()` evaluated at import, but the parameter
   lets tests inject their own. `_build_row` now renders `primary_line(row)` for the headline;
   `secondary_line` is unchanged.

5. **The two warnings are gone.** Added `gi.require_version("Gdk", "3.0")` and
   `gi.require_version("Pango", "1.0")` before the `from gi.repository import ...` line
   (kills the `PyGIWarning`). Replaced the deprecated `Gdk.Screen.get_width()` positioning with
   a new `_move_to_top_right()` that uses `Gdk.Display.get_default()` →
   `get_primary_monitor() or get_monitor(0)` → `get_geometry()`, guarding every step for `None`
   and simply skipping the move if anything is unavailable (kills the `DeprecationWarning`).

## New tests (each fails without its fix)

- `test_identical_rows_reuse_the_same_entry_and_keep_its_text` — two identical `set_rows` calls;
  the `Gtk.Entry` is the **same object** (`assertIs`) and its text survives. (Kills "delete the
  signature short-circuit".)
- `test_a_rebuild_preserves_a_still_present_session_the_user_was_typing_in` — a *different*
  session changes; the typed text, the selection, and the revealed input all survive for the
  still-present session. (Kills "delete the restore step".)
- `test_a_rebuild_restores_nothing_for_a_session_that_vanished` — the session the user was in
  disappears; nothing is restored, no row is selected.
- `test_a_late_set_eval_available_reaches_existing_jump_buttons` — after `set_eval_available(False)`
  every existing jump button reports `get_sensitive()` False. (Kills "make set_eval_available a
  no-op on existing buttons".)
- `PrimaryLineTest` (6 cases) — a normal title passes through; empty, pane id, `"myhost"`, and
  `"myhost.local"` (with `hostname="myhost.local"`) all fall back to `tmux_session`. (Kills
  "make primary_line return row.title unconditionally".)
- The fd-2 capture test around `destroy()` is retained unchanged.

The `row()` test helper gained `session_id` and `title` keyword params (defaults unchanged, so
every existing test is untouched).

## Test counts

`./run-tests` → **`Ran 129 tests` / `OK`** (was 119; +10: 6 `PrimaryLineTest` + 4 new
`NavigatorWindowTest`). Output is pristine: grep of the full run for
`Gtk-CRITICAL|Gtk-WARNING|Gdk-CRITICAL|GLib-GObject` → 0, and for `PyGIWarning|DeprecationWarning`
→ 0.

`PYTHONPATH=src /usr/bin/python3 -W error::DeprecationWarning -c "from ccnav import ui"` →
`import clean` (exit 0, no output). Note: that command only *proves* the import is free of
`PyGIWarning` and any import-time `DeprecationWarning`; the `Gdk.Screen.get_width` deprecation
actually fired inside `__init__` (window construction), so the stronger evidence is the grep of
the full `./run-tests` output above, which now shows zero.

`env -u DISPLAY ./run-tests` → `Ran 129 tests` / `OK (skipped=8)` — the 8 `NavigatorWindowTest`
methods skip; `PrimaryLineTest`, `SecondaryLineTest`, `ComposeStatusTest` all still run and pass
without a display.

## Mutation battery (11 total: original 7 + 4 new)

Applied one at a time against the real file, each confirmed to fail a **named** test, restored
from a pinned copy and `diff`-verified clean between each. No survivors.

| # | Mutation | Killed by |
|---|----------|-----------|
| 1 | `compose_status` one space | `ComposeStatusTest.test_all_three_slots_are_shown` (+ eval-survives) |
| 2 | `compose_status` drop `if part` filter | `ComposeStatusTest.test_empty_slots_are_dropped` (+ 2 more) |
| 3 | `secondary_line` omit `.rstrip("/")` | `SecondaryLineTest.test_working_tolerates_a_trailing_slash` |
| 4 | `secondary_line` truncate parts not the join | `SecondaryLineTest.test_long_secondary_line_is_truncated` |
| 5 | `set_eval_available` inverted sticky | `NavigatorWindowTest.test_status_slots_do_not_clobber_each_other` |
| 6 | `set_rows` always `EMPTY_HINT` | `NavigatorWindowTest.test_status_slots_do_not_clobber_each_other` |
| 7 | `__init__` connects `destroy` → `Gtk.main_quit` | `NavigatorWindowTest.test_destroy_does_not_touch_a_main_loop` |
| 8 | delete the signature short-circuit | `NavigatorWindowTest.test_identical_rows_reuse_the_same_entry_and_keep_its_text` |
| 9 | delete the restore step | `NavigatorWindowTest.test_a_rebuild_preserves_a_still_present_session_the_user_was_typing_in` |
| 10 | `set_eval_available` no-op on existing buttons | `NavigatorWindowTest.test_a_late_set_eval_available_reaches_existing_jump_buttons` |
| 11 | `primary_line` returns `row.title` unconditionally | `PrimaryLineTest` (5 fallback cases fail) |

## Step 7a re-verified — can the user type across a poll tick now? **Yes.**

Empirical trace (touches only `_listbox`; the window is never shown), reproducing the
coordinator's own scenario:

```
before tick:          text='please approve this' revealed=True  selected=True
after tick (changed): text='please approve this' revealed=True  selected=True
after tick (idle):    same entry object? True    text='please approve this'
```

A tick where a *different* session changes forces a real rebuild, and the user's half-typed
text, the selection, and the open input all survive on the still-present session. An idle tick
is now a genuine no-op — the exact same `Gtk.Entry` object stays in place, nothing is touched.
The feature the user asked for by name works.

## Files changed in the revision

- `src/ccnav/ui.py` — signature short-circuit + state preservation in `set_rows`; button
  iteration in `set_eval_available`; new `primary_line`; `_move_to_top_right`; extra
  `require_version` calls; `import socket`; `ccnav_row`/`ccnav_entry`/`ccnav_jump` stashes.
- `tests/test_ui.py` — `PrimaryLineTest`, four new `NavigatorWindowTest` methods, `row()` helper
  gained `session_id`/`title` params.

## Concerns after the revision

- Focus preservation (`grab_focus()` when the entry had focus) is implemented but **not
  covered by a test**: focus requires a realized, shown toplevel, and showing the window is
  forbidden here. In the headless test path `has_focus()` is always False, so `grab_focus()` is
  never exercised by the suite. It runs only in Task 10's real, shown window. Flagging that this
  one line rests on manual reasoning, not an automated assertion.
- Row identity for restore keys on `session_id`. Real `session_id`s are unique; if two rows
  ever shared one, the restore would match the first. Not a concern with real data, noted for
  completeness.
