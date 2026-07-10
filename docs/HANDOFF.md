# cc_navigator — handoff

Written 2026-07-10, to resume this work on a different machine.

## What this is

An always-on-top panel that lists every live Claude Code session, marks the ones
waiting for input, and lets you either jump to that session's terminal or type a
reply straight into it. See `cc_navigator.txt` for the original requirement and
`figures/cc_navigator 화면.png` for the layout.

Design: `docs/superpowers/specs/2026-07-10-cc-navigator-design.md`
Plan: `docs/superpowers/plans/2026-07-10-cc-navigator.md` (12 tasks)
Ledger: `docs/superpowers/sdd/implementation-log.md` ← **read this first**

## Where things stand

| | |
|---|---|
| Branch | `feat/cc-navigator` at `ec0b008` |
| `master` | `2e91be3` — design + plan only, no code |
| Tests | 163, green. Headless: `OK (skipped=11)` |
| Done | Tasks 1–10 |
| Left | Task 11 (doctor), Task 12 (integration test + spike archive), then a whole-branch review |

Everything runs. Nothing has been installed into `~/.claude/settings.json` yet, and
the app has never been launched — by design, because launching it puts a window on
the user's screen and starts driving their tmux.

**The git history was rewritten on 2026-07-10** to strip a private project's name
from the docs. Any SHA you see quoted in an old conversation is gone. `git log` is
the truth.

## Picking it up on a new machine

```sh
git clone https://github.com/kodogyu/cc_navigator.git      # private repo
cd cc_navigator
git checkout feat/cc-navigator
./run-tests                                                 # expect: Ran 163 tests / OK
```

`run-tests` pins `/usr/bin/python3` and sets `PYTHONDONTWRITEBYTECODE=1`. Both
matter; see below.

### Required

| | why |
|---|---|
| `/usr/bin/python3` ≥ 3.8 **with PyGObject** | `apt install python3-gi gir1.2-gtk-3.0`. The code targets 3.8: no `match`, no `X \| Y` annotations. |
| `tmux` ≥ 3.0 | sessions are addressed by tmux pane |
| `gdbus`, `xprop` | the jump path, and the independent verification of it |
| **X11**, not Wayland | focus is verified by reading `_NET_ACTIVE_WINDOW` through `xprop` |
| GNOME Shell with `Eval` unlocked | **blocked from GNOME 41 onward.** Developed against 3.36.9. |

If `Eval` is unavailable the app still runs: jump buttons are disabled and the
status bar says why. Everything else — the list, the waiting badge, typing a reply
— works without it.

### Landmines, all of which cost real time here

**`which python3` may not be the python you want.** On the original machine it is
Anaconda's 3.11, which has no `gi`. Every script in this repo hardcodes
`/usr/bin/python3`. Do not "clean that up".

**`~/.tmux.conf` can kill the tmux server.** A bare `set mode-keys vi` (no `-g`,
no `-w`) makes tmux 3.0a abort the server on the *first external command*. With it
in place, cc_navigator's very first `list-panes` kills the server and every Claude
Code session inside it.

```diff
-set mode-keys vi
+setw -g mode-keys vi
```

`-S` isolates the socket but **not** the config file, so a real-tmux experiment
against a broken config "crashes" for reasons that have nothing to do with the code
under test. That misled one implementer into reporting a tmux segfault. Pass
`-f /dev/null` whenever you are testing tmux behaviour rather than the user's setup.

This was fixed on the original machine on 2026-07-10, and `set-titles` was added at
the same time. **A different machine has a different dotfile.** Check it before
running anything, and check it first when tmux behaves impossibly. Task 11's doctor
exists to make that check automatic.

**`PYTHONDONTWRITEBYTECODE=1` is not tidiness.** CPython invalidates a `.pyc` on
`(mtime, size)`. A mutation that preserves both — swapping two equal-length
strings, restoring a file within the same second — runs against stale bytecode and
the suite reports `OK` for code that is not on disk. Mutation testing is how this
project checks that its tests test anything; it must not be able to lie.

**`gdbus` may resolve to Anaconda's copy.** Both `/usr/bin/gdbus` and
`~/anaconda3/bin/gdbus` answer `Eval("1+1")` correctly here, so it has not bitten
us, but the code calls `gdbus` by name through `PATH`.

## How the work is done

Every task follows the same loop, and it has earned its keep:

1. Write a brief (see `docs/superpowers/sdd/task-*-brief.md` for the shape).
2. An implementer subagent does TDD — failing test first, then the code.
3. **Mutation testing is mandatory.** Break the implementation N ways; each break
   must fail a *named* test. A surviving mutation is a finding to report, never a
   thing to paper over with a contorted test.
4. The controller re-runs the load-bearing claims. Reports are evidence, not fact.
5. A reviewer subagent for the harder tasks.

The briefs carry the previous tasks' mistakes forward as calibration notes, and
several of them predict which mutation will survive — so the implementer has to
check rather than agree. Six of the ten tasks turned up a real hole in the plan's
own tests this way.

### The one rule everything else follows from

**Never trust an API's self-report.** This project exists because two GNOME APIs
report success while doing nothing:

- `gdbus` exits 0 even when `Eval` returns `(false, 'ReferenceError: ...')`.
- `win.activate(0)` returns normally, and Eval reports success, while doing nothing
  at all when the window is on another workspace.

So `gnome.py` performs its action through one channel (Eval) and verifies the
effect through another (`xprop`). It believes xprop, never Eval.

The rule generalises past APIs. It caught a false "tmux segfaults on `send-keys`"
diagnosis (it was the `~/.tmux.conf` line), a test suite that passed against stale
bytecode, and — twice — my own measurement being wrong rather than the code
(`pgrep -f 'sleep 47'` matches the *shell* whose command line contains that
string; `pgrep -x sleep` is the honest count).

## What the code looks like

```
bin/cc-navigator          launcher (exec's, surfaces errors)
bin/cc-navigator-hook     Claude Code hook shim (swallows everything, exit 0 always)
src/ccnav/
  paths.py       state dir, mode 0700
  hookstate.py   hook event -> (state, reason); pure
  statestore.py  atomic write / read_all / prune; the only filesystem owner
  hook.py        the shim's logic
  proc.py        the only subprocess call site; bounded by a timeout
  tmuxctl.py     tmux queries and actions
  gnome.py       activate a window by title, then prove it happened
  model.py       join state files with live tmux panes -> rows; pure
  ui.py          the overlay window; formatting is pure functions above the widgets
  app.py         wiring; nothing blocking ever runs on the GTK main thread
```

Some shapes look odd until you know why. The ledger explains all of them; the four
that matter most:

- **`hookstate` has no `SessionEnd`.** A session that vanishes from tmux vanishes
  from the model on the next tick, and `prune` deletes its file. Liveness is
  derived, not announced.
- **`ui.set_rows` short-circuits on an unchanged signature, and preserves the
  selected row's text and focus when it does rebuild.** Without that, the poll tick
  destroys the `Gtk.Entry` the user is typing into, roughly once a second. The
  plan's version could not do the thing the user asked for by name.
- **`app` runs every tmux/gdbus/xprop call on a daemon thread** and hands results
  back with `GLib.idle_add`. Activating a window can take 3 s; a wedged tmux used
  to be unbounded. Both would have frozen the window while it looked alive.
- **`_poll_loop` catches `Exception` and keeps looping.** One raise used to kill
  the thread for the life of the process, leaving an always-on-top window showing
  rows frozen at the moment of failure, perfectly healthy-looking, with the only
  trace a stderr stack dump nobody watches.

## What is left

### Task 11 — the prerequisite doctor
`docs/superpowers/plans/2026-07-10-cc-navigator.md` lines 2113–2425.
Creates `src/ccnav/doctor.py` and `bin/cc-navigator-doctor`. Checks: the
`~/.tmux.conf` line above; that `set-titles` / `set-titles-string` are configured;
that the hook is installed in `~/.claude/settings.json`. Pure `Check` dataclass,
so the checks are testable without touching the real files.

### Task 12 — integration test and spike archive
Plan lines 2426–2560. A real-tmux integration test on a private socket, plus
`spikes/01_jump.sh`, `02_pane_title.sh`, `03_send_keys.sh`.

**Both must pass `-f /dev/null` to tmux**, or fix `~/.tmux.conf` first.
`spikes/01_jump.sh` steals focus for a moment — it is for a human to run, not an
agent, and not while someone is working.

### Then
A whole-branch review (`git merge-base master HEAD`..`HEAD`), then
`superpowers:finishing-a-development-branch`.

## After the code: installing it

1. ~~Fix `~/.tmux.conf`~~ — **done on the original machine.** Redo it elsewhere.
2. ~~`set -g set-titles on` and `set -g set-titles-string 'ccnav:#{session_name}'`~~
   — **done on the original machine.** This is what makes the outer X11 window
   title the address.
3. Add the hook to `~/.claude/settings.json` — the exact JSON is in the plan's
   "Post-implementation" section. All four events point at
   `<repo>/bin/cc-navigator-hook`, by **absolute path**.
4. One tmux session per project, each attached in its own gnome-terminal window.
   Migrate existing sessions with `claude --resume`.
5. `./bin/cc-navigator &`

Run `bin/cc-navigator-doctor` first once Task 11 exists; it checks 1–3.

### The chain has been proved end to end, once

After fixing the config, on a private tmux socket: the real `bin/cc-navigator-hook`
was fed a real `Notification` payload, wrote its state file, and `app.collect_rows`
joined it against real `tmux list-panes` output:

```
primary  : myproject
secondary: permission_prompt — Allow Bash command: npm test?
waiting  : True
address  : ccnav:myproject   (pane %0)
```

Two things fell out of that, both of which the unit tests had only asserted in
isolation. `set-titles-string` expands to exactly `row.window_title`, so the
address the model computes is the title the window actually carries. And the
primary line reads `myproject`, not the hostname — tmux really does report a fresh
pane's `#{pane_title}` as the hostname, and Task 9's fallback really does catch it.

Not proved: the GNOME activation itself, and `grab_focus()`. Both need a window on
a screen.

## Open items

- **`~/.tmux.conf` was fixed on the original machine only.** A new machine needs the
  same two edits. Until Task 11's doctor exists, check by hand.
- **`grab_focus()` restoration is untested.** Focus needs a shown toplevel, which
  agents are forbidden from creating. Check it by hand the first time the app runs:
  type into a reply box, wait for another session to change, keep typing.
- **Task 9's report claims a `wmctrl` check.** `wmctrl` is not installed on that
  machine, so that verification cannot have run. Nothing depends on it — I
  confirmed with `xwininfo -root -tree` that no window was left behind — but it is
  a reminder that a report is a claim.
- **GitHub may still hold unreachable objects** from the history rewrite. The repo
  is private and has no forks, so this is noted, not acted on.
- The old spike sockets under `/tmp/tmux-$(id -u)/ccnav_*` are dead leftovers and
  can be deleted by hand.

## Continuing with an agent

Point it at `docs/superpowers/sdd/implementation-log.md` and this file, then have
it write `task-11-brief.md` in the shape of `task-10-brief.md`. The brief should:

- restate the global constraints (they bind every task);
- name the mutations to run, and predict which will survive;
- carry forward the calibration notes — the three real mistakes are worth repeating
  to every implementer, because each one was a plausible, confident, wrong claim;
- state the safety rules: never touch the default tmux socket, never show a window,
  never activate a window.
