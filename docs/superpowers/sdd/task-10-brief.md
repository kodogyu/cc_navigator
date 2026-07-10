# Task 10 brief: Wiring and the launcher

BASE commit: `d4d8453` (feat/cc-navigator) — confirm with `git log -1`.
Plan: `docs/superpowers/plans/2026-07-10-cc-navigator.md`, section `### Task 10`
(lines 1900-2109).

**This brief deliberately deviates from the plan.** The plan's `Application` blocks
the GTK main thread, and I am changing that. Where they conflict, this brief wins.
Everything else in the plan still stands.

## Global Constraints (bind every task)

- **Interpreter is `/usr/bin/python3` (3.8.10).** Never invoke bare `python3`.
- **Zero third-party dependencies.** Stdlib + system `gi` only.
- Python 3.8: no `match`, no `X | Y` runtime annotations. `src/` modules start with
  `from __future__ import annotations`. Test files do not.
- **Never trust an API's self-report.**
- Test command: `./run-tests` (sets `PYTHONDONTWRITEBYTECODE=1`; leave it).

## SAFETY

`DISPLAY=:1` is live. **Never run `bin/cc-navigator`, and never call `show_all()`,
`show()` or `present()` on the window** — it would appear on the user's desktop and
start driving tmux and GNOME. Never call `Gtk.main()` in a test. The launcher is for
the user to run, not you.

`tmuxctl` talks to real tmux. Any tmux you start must use `-S <your own temp
socket>` **and** `-f /dev/null` (the user's `~/.tmux.conf` has a line that aborts the
server on the first external command).

## Why this task deviates from the plan

Earlier tasks measured two costs, and the plan spends both on the GTK main thread:

| call | worst case |
|---|---|
| `gnome.activate_window_titled` | **~3.0 s** — two attempts, 1.5 s each, measured 3.017 s |
| `proc.run_command` | **unbounded** — no `timeout=`; a wedged tmux never returns |

The plan calls `activate_window_titled` directly from the jump button's `clicked`
handler, and `collect_rows` (two tmux subprocesses per socket) directly from a
`GLib.timeout_add_seconds` callback. So a failed jump freezes the window for three
seconds, and a hung tmux freezes it forever — while the window sits there looking
alive. That is the exact failure this project exists to make impossible, and we are
not shipping it in our own UI.

Fix both, minimally.

## Files

- Modify: `src/ccnav/proc.py` (add a timeout)
- Create: `src/ccnav/app.py`
- Create: `bin/cc-navigator` (executable)
- Test: `tests/test_app.py`, and add to `tests/` a test for the new `proc` behaviour

## Part 1 — bound every subprocess

Give `run_command` a `timeout` parameter, default `DEFAULT_TIMEOUT = 5.0` seconds,
and on `subprocess.TimeoutExpired` return `(124, "")` — the shell's conventional
timeout status, and non-zero, so every existing caller already treats it as failure
(`_query` returns `{}`; `eval_js` returns `False`). Confirm that claim by reading
those callers rather than assuming it.

`subprocess.run(timeout=...)` kills the child before raising. Verify that it does
— do not take my word — by timing `run_command(["sleep", "5"], timeout=0.2)` and
checking that it returns promptly and that no `sleep` process survives.

Test it with a real subprocess. `proc.py` had no test file before because it had no
logic; it has logic now.

## Part 2 — keep the main thread free

Restructure `Application` so **no tmux call, no gdbus call, and no xprop call ever
runs on the GTK main thread**.

- One daemon worker thread runs the poll loop: wait on a `threading.Event` with a
  `POLL_SECONDS` timeout, call `collect_rows(...)`, then hand the rows back to the
  main thread with `GLib.idle_add`. Gtk calls happen only on the main thread.
- `Gio.FileMonitor`'s `changed` callback does not refresh. It just sets the event,
  so a hook writing a state file wakes the poller immediately.
- `jump` and `send` each run their work on a short-lived daemon thread and post the
  resulting status string back with `GLib.idle_add`. Disable the row's jump button
  while a jump is in flight, and re-enable it when the status comes back, so a
  double click cannot start two activations. (`ui.NavigatorWindow` already exposes
  what you need to reach a row's button; if it does not, add the smallest accessor
  and say so.)
- `main()` sets a stop event and joins the poll thread before returning, after
  `Gtk.main()` exits. Daemon threads mean a hang cannot wedge exit, but a clean join
  means we do not race a `collect_rows` against interpreter shutdown.

Keep `collect_rows` exactly as the plan has it — it already takes its collaborators
as arguments and is pure enough to test without GTK, tmux or a display.

## Part 3 — extract the decision, then test it without threads

Pull the jump's decision out of the widget callback into a pure function:

```python
def jump_status(result: gnome.ActivationResult, window_title: str) -> str:
```

returning `""` on a clean single-match success, the activation-failure message when
`not result.ok`, and the two-clients warning when `result.matched > 1`. Use the
plan's Korean strings verbatim. Then `perform_jump(row, select_pane, activate)`
composes `select_pane` + `activate_window_titled` + `jump_status` with both
collaborators injected, returning the status string. The thread only calls
`perform_jump` and posts its result.

Now the interesting behaviour is testable with no GTK, no threads, and no
subprocesses:

- `jump_status` for ok/single, ok/two-matched, and not-ok.
- `perform_jump` calls `select_pane` **before** `activate` — order matters; tmux
  must have selected the pane before the window is raised, or the user lands on the
  wrong pane. Assert the call order.
- `perform_jump` still activates even when `select_pane` fails, because
  `select_pane` ignores exit codes by design (see Task 6).

Do the same for `send` if it earns it; it is two tmux calls and a status reset, so a
`perform_send` may be more ceremony than it is worth. Your call — justify it either way.

## The plan's three `collect_rows` tests still apply

Copy `tests/test_app.py` from the plan (lines 1917-1980). They pin: only sockets
named by state files are queried; `prune` receives the live pane set; and with no
state files, tmux is never called at all. That last one matters — it is what makes
cc_navigator free when nothing is running.

## Part 4 — the launcher, with the bug we already found once

`bin/cc-navigator`, per the plan, resolves its own directory with
`here=$(cd "$(dirname "$0")" && pwd)`. **That is the same defect Task 4's review
found in `bin/cc-navigator-hook`**: invoked through a symlink, `dirname "$0"` gives
the symlink's directory, `PYTHONPATH` misses `src/`, and the import fails. Read
`bin/cc-navigator-hook` and apply the same `readlink -f` treatment.

Unlike the hook, this launcher **should** `exec` and **should** let errors surface —
if the app cannot start, the user must see why. Do not add `2>/dev/null`.

Add a test, mirroring `tests/test_hook_shim.py`, that runs the launcher through a
symlink with a bogus `DISPLAY` and asserts it fails with an *import-free* error —
i.e. it got far enough to try to open a display, proving `PYTHONPATH` resolved. Do
**not** let it open a real window: point `DISPLAY` at something invalid like `:99`.

## Mutation testing — mandatory

At minimum:

1. `run_command` swallows `TimeoutExpired` and returns `(0, "")`.
2. `collect_rows` queries tmux even when there are no records.
3. `collect_rows` passes `model.live_pane_keys(...)` of the wrong dict to `prune`.
4. `perform_jump` calls `activate` before `select_pane`.
5. `jump_status` returns `""` when `result.ok` is False.
6. `jump_status` ignores `matched > 1`.
7. `Application` refreshes from the `FileMonitor` callback on the main thread
   (i.e. calls `collect_rows` there) instead of setting the event.
8. The launcher drops `readlink -f`.

Mutation 7 may be awkward to observe. If you cannot kill it with a test, say so and
name what would be needed. Every implementer before you who reported a survivor was
right to; every one exposed a real hole.

## Also verify, and report

**a) Threading and GTK.** Confirm by reading PyGObject's documentation or by
experiment that `GLib.idle_add` is the correct main-thread hand-off, and that
calling `Gtk` from a worker thread is not. State what you relied on.

**b) Does the poll thread's `collect_rows` race `set_rows`?** `set_rows` now
preserves the user's typed text across a rebuild. Trace whether a refresh landing
while the user types can still lose it, given the hand-off happens on the main
thread.

**c) The re-enable path.** If a jump thread dies from an unexpected exception, does
the button stay disabled forever? Make sure it does not, and say how.

## Then

- Full suite green. `env -u DISPLAY ./run-tests` green with window tests skipped.
- Output pristine: no `Gtk-CRITICAL`, `Gtk-WARNING`, `Gdk-CRITICAL`, `GLib-GObject`,
  `PyGIWarning`, `DeprecationWarning`, no stray prints, no `ResourceWarning`.
- No lingering `sleep` or `tmux` processes from your tests.
- `chmod +x bin/cc-navigator`, and confirm git records mode `100755`.
- Commit.

## Notes and traps

- `NavigatorWindow` does not connect `destroy`. `app.main()` must:
  `application.window.connect("destroy", Gtk.main_quit)`.
- Keep a reference to the `Gio.FileMonitor` or it is garbage collected and the
  watch silently stops. The plan comments this; keep the comment.
- `GLib.idle_add` callbacks must return `False` or they repeat forever.
- Do not add a systemd unit, a `--daemon` flag, a config file, or logging. YAGNI.
- Do not touch `~/.claude/settings.json`. Installing the hook is the user's step.
- Work from `/data/playground/cc_navigator`.
