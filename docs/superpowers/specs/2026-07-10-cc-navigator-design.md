# cc_navigator — Design

Date: 2026-07-10
Status: Approved for planning

## 1. Problem

Many Claude Code sessions run at once across many terminal windows. When a session needs
input, a desktop notification fires but does not say which session it came from, and there
is no way to reach that session except by hunting through windows and tabs.

## 2. Goals

- An always-on-top window that lists every interactive Claude Code session.
- Sessions waiting for input are visually highlighted.
- Clicking a row lets the user send one line of input to that session.
- A "jump" action moves OS focus to the session's terminal — switching workspace and
  selecting the correct tmux pane.

## 3. Non-goals (v1)

- Background / daemon-managed sessions (`bg-pty-host`, forked sessions). They have no pane,
  so neither jump nor input applies to them.
- Subagents.
- Prompt-type-aware input UI (approve/deny buttons, choice lists). v1 sends free text.
- Automatic focus stealing on notification. Focus moves only when the user asks.
- Sending desktop notifications. `claude-notifications-go` already does that.
- Remote hosts, multiple X displays.

## 4. Environment and verified constraints

Every claim below was verified on this machine on 2026-07-10. Commands and raw output are in
Appendix A.

- Ubuntu 20.04, X11 (`XDG_SESSION_TYPE=x11`), GNOME Shell 3.36.9, gnome-terminal 3.36, tmux 3.0a.
- **All gnome-terminal windows are served by one process** (`gnome-terminal-server`). `_NET_WM_PID`
  is identical for every window and cannot distinguish them.
- **gnome-terminal exposes no tab API.** Its D-Bus screen object
  (`/org/gnome/Terminal/screen/<uuid>`) has exactly one method, `Exec()`. Tabs are GTK notebook
  pages, not X11 windows, so no window manager can reach them.
- `org.gnome.Shell.Eval` works on GNOME 3.36 (it is blocked from GNOME 41 onward). In its scope
  `global` and `Main` exist; `Shell` does not.
- `Meta.Window` on 3.36 has `get_id()`, `get_stable_sequence()`, `get_pid()`, `get_workspace()`.
  It does **not** have `get_xwindow()`.
- `xdotool` and `wmctrl` are not installed. `xprop`, `xwininfo`, `xdpyinfo`, `gdbus`, `tmux` are.
- `which python3` resolves to Anaconda, which has no `gi`. PyGObject/GTK 3.24 lives in
  `/usr/bin/python3`.

### 4.1 Two silent-failure bugs found while verifying

These shape the whole error-handling strategy.

1. `win.activate(0)` returns normally and Eval reports success, but **does nothing** when the
   window is on another workspace. `global.get_current_time()` also returns `0`. A valid X
   timestamp from `global.display.get_current_time_roundtrip()`, or `Main.activateWindow(w)`,
   is required.
2. `org.gnome.Shell.FocusApp` is not a window activator on GNOME 3.36. Its implementation is
   `this.ShowApplications(); Main.overview.viewSelector.appDisplay.selectApp(id);` — it opens the
   application grid. `gdbus` returns `()`, which callers misread as success.

**Principle adopted: never trust an API's self-report. Verify the effect through an independent
channel.**

## 5. Prerequisites

These must be true before cc_navigator can work. The installer checks each and refuses to run
with a clear message if any is unmet.

1. **`~/.tmux.conf` must not contain `set mode-keys vi`.** Without `-g`, the tmux server dies as
   soon as any command targets it from outside — which is precisely what cc_navigator does.
   Replace with `setw -g mode-keys vi`.
2. tmux global options:
   ```
   set -g set-titles on
   set -g set-titles-string 'ccnav:#{session_name}'
   ```
3. One tmux session per project, each attached in its own gnome-terminal window, exactly one
   client per session. Session names must be unique and match `[A-Za-z0-9_.-]+`.
4. The UI runs under `/usr/bin/python3`.

## 6. Addressing scheme

This is the core idea. gnome-terminal gives us no handle on a window, so **we take ownership of
the window title.**

| Layer | String | Owner | Role |
|---|---|---|---|
| Outer X11 window title | `ccnav:<tmux_session_name>` | tmux `set-titles-string` | **address** |
| tmux `pane_title` | `✳ 작업 중 (demo-project)` | Claude Code's OSC title | **display** |

The outer title is stable and unique, so it is a reliable address. The inner title is whatever
Claude Code writes, so it is only ever displayed, never parsed. If Claude Code changes its title
format, only the row label changes; addressing is unaffected.

## 7. Architecture

Three components. The hook never depends on the UI being alive.

```
Claude Code hook ──stdin JSON + $TMUX_PANE, $TMUX──▶  state file  (atomic write)
                                                          │ inotify
                                                          ▼
        tmux list-panes ─────────────────────────▶  join → rows ──▶ GTK window
                                                          │
                                         user click ───────┤
                                                          ├─▶ jump:  select-window/pane + Eval activate + verify
                                                          └─▶ input: send-keys -l -- <text> ; send-keys Enter
```

### 7.1 Hook shim

A single small executable invoked from `~/.claude/settings.json` hooks. It reads the hook JSON on
stdin, reads `$TMUX_PANE` and `$TMUX` from its own environment, writes one state file, exits 0.

It performs no network I/O, takes no locks, and never fails the hook. If it cannot write, it exits
0 silently. **cc_navigator being closed, crashed, or never installed must never slow down or block
Claude Code.**

Verified: a hook subprocess spawned by a real `claude` running inside a tmux pane sees
`TMUX_PANE=%0` and `TMUX=/tmp/tmux-1000/<sock>,<pid>,0`. The socket path is the first
comma-separated field of `$TMUX`.

If `$TMUX_PANE` is absent (the session is not in tmux), the shim exits 0 without writing. Such
sessions simply do not appear in the list.

**State file**: `$XDG_RUNTIME_DIR/cc-navigator/<session_id>.json`, falling back to
`/tmp/cc-navigator-$UID/` when `XDG_RUNTIME_DIR` is unset. Written to a temp file in the same
directory and `rename()`d, so a reader never sees a partial file.

```json
{
  "session_id":   "11111111-2222-3333-4444-555555555555",
  "cwd":          "/data/projects/demo_project",
  "tmux_socket":  "/tmp/tmux-1000/default",
  "tmux_pane":    "%12",
  "state":        "waiting",
  "reason":       "permission_prompt",
  "message":      "Allow Bash command: npm test?",
  "updated_at":   1783665780
}
```

`(tmux_socket, tmux_pane)` is the primary key. A pane id such as `%12` is unique only within one
tmux server, so the socket must be part of the key.

**Event → state**

| Hook event | Matcher | state | reason |
|---|---|---|---|
| `SessionStart` | — | `working` | — |
| `UserPromptSubmit` | — | `working` | — |
| `Notification` | (all) | `waiting` | the payload's `notification_type` |
| `PreToolUse` | `AskUserQuestion｜ExitPlanMode` | `waiting` | `question` / `plan` |
| `Stop` | — | `waiting` | `idle` |
| `SubagentStop` | — | ignored | — |

`SubagentStop` is ignored: a subagent finishing does not mean the session wants input, and it
fires often.

Note: `Notification` uses an empty matcher so `elicitation_dialog` and the other types are not
silently dropped. This is the coverage gap the `claude-notifications-go` plugin has.

### 7.2 Model

Rebuilt on inotify events on the state directory, and on a 1 s tmux poll.

For each distinct `tmux_socket` mentioned by a state file, two calls:

```
tmux -S <sock> list-panes -a -F '#{pane_id}=#{session_name}'
tmux -S <sock> list-panes -a -F '#{pane_id}=#{pane_title}'
```

Each output line is split on the **first** `=`. A pane id (`%12`) never contains `=`, while a
`pane_title` may contain `=`, `|`, spaces, and arbitrary UTF-8. This is why the fields are not
packed into one delimited record.

**A row exists if and only if its state file's `tmux_pane` appears in that socket's pane list.**
This prunes dead sessions with no `SessionEnd` hook and no reaper. State files whose pane is gone,
or whose `updated_at` is older than 24 h, are deleted.

Two state files can name the same `(socket, pane)` — a session ended and another started in the
same pane before the stale file was pruned. The file with the newer `updated_at` wins; the other
is deleted.

A Claude session running in a pane but with no state file — because it started before the hooks
were installed — does not appear. This is a real trap, so the installer refuses to finish until
the hooks are present in `~/.claude/settings.json`, and the UI shows a one-line hint when it finds
zero rows. Discovering such sessions from tmux alone is deliberately not attempted in v1: it would
need `pane_current_command` sniffing, and a row with no state has no state to show.

### 7.3 Jump

```
1. tmux -S <sock> switch-client -t <pane>          # best effort, ignore failure
   tmux -S <sock> select-window -t <pane>
   tmux -S <sock> select-pane   -t <pane>
2. gdbus → org.gnome.Shell.Eval:
      find the Meta.Window whose title === "ccnav:<session_name>"
      Main.activateWindow(w)
3. VERIFY: xprop -root _NET_ACTIVE_WINDOW  →  read that window's _NET_WM_NAME
      matches "ccnav:<session_name>"?  done.
      otherwise retry once with w.activate(global.display.get_current_time_roundtrip())
      still wrong?  mark the row with an inline error. Never claim success.
```

Step 3 exists because of the two silent-failure bugs in §4.1. Verification reads X11 properties
through `xprop`, deliberately a different channel from the one that performed the action.

Activating a window on another workspace switches to that workspace. Verified: desktop 6 → 7.

If more than one window matches the title, activate the first and log a warning. That state means
two clients are attached to one tmux session, which prerequisite 3 forbids.

### 7.4 Input

```
tmux -S <sock> send-keys -t <pane> -l -- "<text>"
tmux -S <sock> send-keys -t <pane> Enter
```

`-l` and `--` are mandatory. Without `-l`, tmux interprets words like `Enter` and `C-c` as key
names; without `--`, text beginning with `-` is parsed as an option. Verified: a 46-byte payload
containing single and double quotes, `$`, `;`, a backslash, Korean text and `✳` arrives
byte-identical.

User text is passed as a single `argv` element. It is never interpolated into a shell string.

After sending, the row is optimistically set to `working`. The `UserPromptSubmit` hook confirms it.

A permission prompt is answered by typing `1` and Enter, like in the terminal. Named keys
(`Down`, `Up`, `1`, `C-c`) also work and are reserved for a later prompt-aware UI.

### 7.5 UI

`/usr/bin/python3` + GTK 3.24. Verified that GTK sets `_NET_WM_STATE_ABOVE`,
`_NET_WM_STATE_SKIP_TASKBAR`, `_NET_WM_STATE_SKIP_PAGER` and `_NET_WM_WINDOW_TYPE_UTILITY`, and
that `move()` is honoured.

- Window: `keep_above`, `skip_taskbar`, `skip_pager`, type hint `UTILITY`, 340×420, positioned
  near the top-right of the primary monitor.
- One row per session:
  - a status dot — red when `state == waiting`, otherwise dim;
  - the label `Waiting input` beside the dot when waiting;
  - the `pane_title` as the row title;
  - a dim secondary line: `reason` and truncated `message` when waiting, otherwise the basename
    of `cwd`.
- Waiting rows sort first, then by `updated_at`, newest first.
- A selected row reveals a one-line text entry (Enter sends) and a **해당 세션으로 이동** button.

## 8. Error handling

| Failure | Behaviour |
|---|---|
| Eval blocked (GNOME 41+) | Detected at startup by evaluating `1+1`. List and input keep working; jump is disabled with an explanation. |
| Activation silently no-ops | Detected by the `_NET_ACTIVE_WINDOW` check. One retry, then an inline error on the row. |
| tmux server absent | Empty list. No crash. |
| State file unreadable or malformed | That file is skipped and logged. Other rows render. |
| Pane vanished | Row disappears; state file deleted. |
| Hook cannot write state | Hook exits 0. Claude Code is never blocked. |
| Two windows match one address | First is activated; a warning is logged. |

## 9. Testing

- **Unit** — the hook's event→state mapping, driven by fixture hook payloads for every event and
  `notification_type`.
- **Unit** — the tmux output parser, with `pane_title` values containing `=`, `|`, spaces, and
  multi-byte UTF-8 including `✳`.
- **Unit** — state file writes are atomic: a reader concurrent with a writer never sees a partial
  file.
- **Integration** — a fake `claude` that emits an OSC title and then blocks, run in a private tmux
  socket. Asserts that a row appears, that jump selects the right pane, and that input arrives
  byte-exact.
- **Regression** — the spike scripts from this design are kept in `spikes/`. They are the early
  warning for a GNOME or tmux upgrade breaking an assumption.
- **Rule** — assertions about focus read X11 properties. A test never asserts on an Eval return
  value.

## 10. Migration

The 12 sessions running today are attached to gnome-terminal directly and have no `$TMUX_PANE`,
so they will not appear. They must be restarted inside tmux.

1. Fix `~/.tmux.conf` (prerequisite 1) and add the two global options (prerequisite 2).
2. Install the hook shim into `~/.claude/settings.json`.
3. For each project, create a tmux session named after it and attach it in its own
   gnome-terminal window.
4. Restart each Claude session inside its pane with `claude --resume`, which preserves history.

Whether `~/.claude/settings.json` hook edits are picked up by a running session does not matter
here: every session is restarted in step 4 regardless, because a session outside tmux has no
`$TMUX_PANE` and can never be addressed.

## 11. Risks

- **GNOME upgrade removes `Eval`.** It is already blocked from GNOME 41. Mitigation: the startup
  probe degrades gracefully. The long-term replacement is the `activate-window-by-title` GNOME
  extension, whose D-Bus interface takes the same title we already control.
- **A future Claude Code drops the OSC title.** Only the row label degrades; addressing is
  unaffected, by design.
- **The user attaches a second client to a session.** Breaks the 1:1 address assumption. Detected
  and warned.

## Appendix A — Verification log (2026-07-10)

| # | Assumption | Verdict | Key evidence |
|---|---|---|---|
| 1 | `set-titles-string 'ccnav:#{session_name}'` controls the outer window title | Confirmed | `xprop _NET_WM_NAME` on the new window → `"ccnav:spikeproj"` |
| 2 | Eval can activate that window | Confirmed | `_NET_ACTIVE_WINDOW` became the target window id |
| 3 | Activation switches workspace | Confirmed **only** with a real timestamp | `activate(0)`: desktop 6→6, focus unchanged, Eval said `activated=1`. `activate(get_current_time_roundtrip())` and `Main.activateWindow(w)`: desktop 6→7, focus on `"ccnav:wsproj"` |
| 4 | `pane_title` captures Claude's OSC title | Confirmed | OSC 0 and OSC 2, BEL and ST terminators; a title mixing the `✳` symbol with Korean text round-tripped byte-exact, 37 bytes starting `e2 9c b3 …`; unaffected by `allow-rename` / `set-titles` |
| 5 | `send-keys -l --` delivers text verbatim | Confirmed | 46-byte payload with `'`, `"`, `$`, `;`, `\`, Korean, `✳` compared byte-exact. Without `-l`, the text did not arrive |
| 6 | Hook subprocess inherits `$TMUX_PANE` | Confirmed end-to-end | A real `claude --settings … -p` inside a tmux pane; its `Stop` hook saw `TMUX_PANE=%0` and `TMUX=/tmp/tmux-1000/ccnav_s3r,4039841,0` |
| 7 | GTK3 always-on-top overlay | Confirmed | `_NET_WM_STATE_ABOVE, _NET_WM_STATE_SKIP_TASKBAR, _NET_WM_STATE_SKIP_PAGER`; `/usr/bin/python3` only |
| 8 | `set mode-keys vi` in `~/.tmux.conf` kills the server | Confirmed | A/B: with the user's conf the server dies on the first `send-keys`; with `-f /dev/null`, or with `setw -g mode-keys vi`, it survives |

Also established while diagnosing the existing plugin: `GNOME_TERMINAL_SCREEN` is inherited by hook
subprocesses and uniquely identifies a gnome-terminal tab, but nothing can act on that identifier —
which is what forced the move to tmux.
