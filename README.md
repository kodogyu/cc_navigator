# cc_navigator

**English** · [한국어](README.ko.md)

An always-on-top panel that lists every live Claude Code session, highlights the
ones waiting for your input, and lets you either **jump** to that session's terminal
or **type a reply** straight into it — without hunting through a dozen windows and
tabs to find which session the notification came from.

```
┌─────────────────────────────┐
│ cc_navigator                │
├─────────────────────────────┤
│   "Session1 title"          │
│   working details …         │
├─────────────────────────────┤
│ ● "Session2 title"          │  ← waiting for input, highlighted
│   permission_prompt …       │     [ jump ]  [ reply ▸ ]
├─────────────────────────────┤
│   "Session3 title"          │
│   working details …         │
└─────────────────────────────┘
```

## The problem

You run many Claude Code sessions at once, spread across many terminal windows. When
one needs input, a desktop notification fires — but it does not say *which* session,
and there is no way to reach that session except by hunting through windows and tabs.
cc_navigator gives every session one row, one badge, and one click to get there.

## How it works

Claude Code fires **hooks** on session lifecycle events. A tiny shim
(`bin/cc-navigator-hook`) records each event as a per-session state file. The panel
joins those state files against live `tmux` panes to decide what to show:

- **Liveness is derived, not announced.** There is no "session ended" event. A
  session that vanishes from `tmux` vanishes from the panel on the next tick, and its
  state file is pruned. That is why a crashed session never leaves a ghost row.
- **Jump addresses the window by title.** `tmux`'s `set-titles-string` stamps
  `ccnav:<session>` onto the outer X11 window title; GNOME Shell activates the window
  carrying that title.
- **Reply injects one line** into the session's pane with `tmux send-keys -l`, so the
  text is delivered literally and never interpreted by a shell.

### The one rule the whole design follows

> **Never trust an API's self-report. Act through one channel, verify through another.**

This project exists because two GNOME APIs report success while doing nothing:
`gdbus` exits `0` even when `Eval` returns `(false, 'ReferenceError…')`, and
`window.activate(0)` returns normally while failing to move focus across workspaces.
So the jump path *acts* through GNOME Shell `Eval` and *verifies* the effect through
`xprop` — it believes `xprop`, never `Eval`. The same discipline runs through every
module, and through the tests: each task is checked with **mutation testing**, where
the implementation is deliberately broken N ways and every break must fail a named
test. A test suite that cannot catch a broken implementation is not evidence.

## Requirements

This is an **X11 + GNOME + tmux** tool, deliberately narrow. It was developed and
verified against:

| | |
|---|---|
| Display server | **X11** (not Wayland) — focus is verified by reading `_NET_ACTIVE_WINDOW` via `xprop` |
| Desktop | **GNOME Shell with `Eval` unlocked** — blocked from GNOME 41 onward; developed on 3.36.9 |
| Terminal multiplexer | **tmux ≥ 3.0** — sessions are addressed by tmux pane |
| Interpreter | **`/usr/bin/python3` ≥ 3.8 with PyGObject** (`apt install python3-gi gir1.2-gtk-3.0`) |
| Also needed | `gdbus`, `xprop` |

No third-party Python dependencies — standard library plus the system `gi` bindings
only.

If GNOME `Eval` is unavailable, **the app still runs**: the jump buttons are disabled
and the status bar explains why. Listing sessions and typing replies work without it.

## Getting started

```sh
git clone https://github.com/kodogyu/cc_navigator.git
cd cc_navigator
./run-tests                    # expect: Ran 217 tests / OK
./bin/cc-navigator-doctor      # checks your machine and prints exactly what to fix
```

**Run the doctor first.** It does not guess from a config file — it reproduces the
one failure that matters (see below) on a private tmux socket and reports the verdict,
then tells you precisely which lines to add to `~/.tmux.conf` and `~/.claude/settings.json`.

Once the doctor passes:

1. Add the five hooks (`SessionStart`, `UserPromptSubmit`, `Notification`, `Stop`,
   `PreToolUse`) to `~/.claude/settings.json`, each pointing at
   `<repo>/bin/cc-navigator-hook` by **absolute path**.
2. Run one tmux session per project, each attached in its own terminal window.
3. `./bin/cc-navigator &`

With no sessions registered the panel is inert — it makes **zero** tmux calls until a
hook writes the first state file, so it costs nothing while you are not using it.

## ⚠️ The tmux landmine the doctor exists to catch

On tmux 3.0a, a `~/.tmux.conf` line of the `set` family (`set`, `setw`,
`set-option`, `set-window-option`) whose flags include **none of `-g`, `-q`, `-s`**
silently corrupts the server at config-load time. The server then runs normally —
lists panes, switches windows — right up until something sends a **space** through
`send-keys`, at which point it **segfaults**, taking every Claude Code session inside
it with it. The trigger is the first reply you type that contains a space.

`bin/cc-navigator-doctor` detects this by loading your config into a throwaway server
and sending it a space, so you find out before cc_navigator ever touches your real
tmux. The fix is to add `-g`:

```diff
-set mode-keys vi
+setw -g mode-keys vi
```

## Project layout

```
bin/
  cc-navigator            launcher — exec's the app, surfaces errors
  cc-navigator-hook       Claude Code hook shim — swallows everything, always exit 0
  cc-navigator-doctor     prerequisite checker
src/ccnav/
  paths.py       state directory (mode 0700)
  hookstate.py   hook event → (state, reason); pure
  statestore.py  atomic write / read_all / prune — the only filesystem owner
  hook.py        the shim's logic
  proc.py        the only subprocess call site; bounded by a timeout
  tmuxctl.py     tmux queries and actions
  gnome.py       activate a window by title, then prove it happened via xprop
  model.py       join state files with live tmux panes → rows; pure
  ui.py          the overlay window; formatting is pure functions above the widgets
  doctor.py      prerequisite checks, including the segfault reproduction
  app.py         wiring — nothing blocking ever runs on the GTK main thread
```

Design, plan, and a per-task engineering ledger live under
[`docs/`](docs/) — start with
[`docs/superpowers/sdd/implementation-log.md`](docs/superpowers/sdd/implementation-log.md).

## Status

Core is built and the full suite is green (217 tests). The end-to-end chain — hook →
state file → live tmux join → one waiting row addressed `ccnav:<session>` — has been
proved once on a private socket.

Still in progress:

- **Two silent-failure fixes** on the critical path: a reply that fails to send while
  the UI reports success, and a slow (not dead) tmux causing live sessions' state
  files to be pruned. Being addressed now.
- **Real-tmux integration test and archived spikes.**
- Not yet proved on a live desktop: the GNOME jump activation and reply-box focus
  restoration — both need a window on a real screen.

This is a personal tool built for one specific desktop, not a packaged application.
The requirements section is a hard boundary, not a starting point to generalize from.
