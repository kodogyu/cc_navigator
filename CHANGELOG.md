# Changelog

cc_navigator has no build artifact: **Settings ⚙ → 업데이트 확인** fast-forwards your
checkout to the latest `master` and restarts. So this file, not a download page, is how
you find out what changed — and what a new version starts doing on your machine.

## 0.3.0-beta — Unreleased

### New

- **Codex sessions now appear alongside Claude Code sessions.** Settings → Integration
  has a separate Codex hook toggle that safely merges lifecycle hooks into
  `$CODEX_HOME/hooks.json` (normally `~/.codex/hooks.json`). Codex requires one explicit
  trust review in `/hooks`; sessions then report working, explicit question waits,
  completion, and subagent activity through the same tmux-backed model. A blue Codex
  badge distinguishes their rows. Because Codex currently defers its first lifecycle
  hook until the first prompt, cc_navigator also discovers the actual Codex process in
  each tmux pane and shows a provisional row immediately at TUI startup.
- **Usage now shows both providers independently.** Claude Code retains its existing
  account-limit request. Codex limits come from the local authenticated app-server's
  `account/rateLimits/read` method, so cc_navigator never handles Codex credentials.
  The two loads run concurrently, and a failure or missing login on one side no longer
  hides the other provider's result.
- VS Code extension sessions now appear without a tmux pane, use their AI title as
  the headline, and can jump to the matching editor tab.
- Four selectable colour themes, custom background/header colours, a dock-integrated
  launcher, and a single-instance guard.
- An optional local token-cost estimate can be enabled in Settings. It uses the
  separately installed `ccusage` executable every five minutes and is **off by
  default**.

### Security and privacy

- Codex limits come from the local authenticated `codex app-server`, so cc_navigator
  does not read, copy, or send Codex credentials itself.
- cc_navigator never installs `ccusage`, never falls back to `npx`, and never
  downloads executable code when the option is enabled. The Settings warning states
  that the external program reads local Claude conversation logs before consent is
  given.
- Account usage keeps the redirect-refusing HTTPS client from 0.2.x, so the OAuth
  bearer token is sent only to the intended Anthropic endpoint.
- VS Code tab URIs are sent only after one exact target window is independently
  confirmed; failed or ambiguous window matches leave the editor untouched.
- VS Code process liveness records both PID and kernel start time, preventing a stale
  session from surviving PID reuse by another Claude process.
- VS Code UI liveness reads only exact local workbench-state keys. Open-editor
  state is reduced to a boolean inside SQLite, so unrelated file names, tab
  titles, and contents are never returned to cc_navigator.
- Claude background Shell/Monitor tracking stores only the task type and bounded
  opaque task ID. Commands, descriptions, server names, and output are never
  copied into cc_navigator state.
- Fallback discovery for an unreported Claude tmux pane reads only bounded Linux
  process metadata (name, parent relationship, start metadata, and cwd). It does
  not read command arguments or terminal output, and pane locations are hashed
  before they become state filenames.
- Claude hook address repair compares only the owning process's bounded
  `/dev/pts/N` stdin symlink with tmux's `pane_tty` table. It does not inspect
  the process environment, terminal contents, or conversation transcript.

### Fixed

- If the desktop cannot allocate a GIO/inotify directory monitor, startup now
  falls back to the existing periodic session poll instead of aborting. Hook
  writes may take up to the configured poll interval to appear in that mode.
- Claude completion/status notifications (including `agent_completed`) no
  longer masquerade as unanswered prompts. Notification-driven red states are
  now limited to explicit permission/dialog events; completed dialog responses
  clear an earlier red state, passive or unknown notices preserve the main
  state, and existing stale records are normalized immediately from the live
  pane title.
- A Claude pane whose native title animation is visibly moving now recovers
  from a stale `Stop`/idle hook and shows the main working arrow. A single
  leftover title frame is not enough: cc_navigator requires observed frame
  motion, and never uses it to hide a question/permission wait or replace the
  green input-ready dot for known background work.
- Detached Claude Shell/Monitor work now keeps the auxiliary arrow even after
  systemd reparents it outside the pane process tree or an old `/branch` record
  hides its hook task ID. The fallback checks only same-user process cwd plus a
  bounded, writable `tasks/*.output` FD path; it never reads the command,
  process environment, or output contents, and it declines to associate the
  signal when multiple live Claude panes share one project directory.
- Claude Code `/branch` sessions no longer disappear before the fork's first
  addressable hook event. A live process-backed provisional row covers the new
  pane immediately, and state is isolated by hashed tmux location so simultaneous
  copies cannot overwrite one another.
- A question or permission prompt inside a `/branch` pane now turns that pane
  red even when Claude inherited a missing or stale `TMUX_PANE`. The hook
  resolves its actual pane from the Claude process's controlling pseudo-terminal
  before reading or writing state.
- Claude's agent-team `agent_needs_input` notification no longer turns the main
  session red when its prompt is still available. Existing stale records are
  normalized immediately after upgrading, while a simultaneous native title
  spinner still marks the session as actively working.
- A completed Claude background Shell no longer leaves the auxiliary spinner
  behind when no terminal lifecycle hook follows it. The poller verifies the
  pane's process-group metadata and clears only stale Shell ids; commands,
  arguments, and output are never read, and an unobservable process tree keeps
  the prior state rather than risking a false negative.
- Codex `PermissionRequest` no longer produces a false red input-needed state.
  That hook runs before Codex decides whether automatic review can handle the
  operation and provides no final routing result. Fresh hook installations omit
  the unnecessary event, reinstalling reconciles stale installations by removing
  only cc_navigator's exact command from that event, and existing `permission`
  records are repaired both at display time and on a stale callback. Explicit
  `request_user_input` questions still appear red.
- A newly opened, pre-prompt Codex pane now appears input-ready (green) instead
  of showing a false calm-blue working state merely because its process exists.
- Codex background terminals now use the same rotating auxiliary-work layer as
  subagents. The front dot remains green when the main session can accept input,
  even while either kind of auxiliary work continues. Only opaque PID/start-time
  identities are stored; terminal commands and output are never recorded.
- Claude background Shell and Monitor tasks now use that auxiliary-work layer as
  well. Stop snapshots and both generic and Claude-native (`BashOutput`/`KillBash`)
  tool lifecycle events keep the indicator current, while the front dot remains
  green whenever the main session is ready for input.
- VS Code Claude sessions disappear when their editor tab and sidebar close even
  if the extension leaves the headless `claude` backend and stdio peer running.
  Liveness now checks for a matching Claude editor or visible active sidebar in
  the workspace state, then the anonymous stdio peer and PID/start time. Unknown
  or unsupported workbench state safely falls back to the process checks.
- Codex's Braille loading glyph in the pane title now animates locally at the same
  80 ms cadence as the working arrow. Normal one-second tmux polling still handles
  session data, but no longer makes the title spinner jump once per poll.
- Transient jump/send status messages clear automatically after ten seconds instead
  of occupying the panel indefinitely.
- A working session with no hook update for fifteen minutes is presented as idle,
  recovering from a missed final `Stop` event without hiding the row. A live native
  Claude/Codex spinner in the tmux pane title now overrides that timeout, so a
  genuinely long-running turn stays marked as working.
- Settings can optionally make a single row click jump immediately to that session;
  the safer expand-first interaction remains the default.

### Compatibility

- Existing state files without a provider remain Claude Code rows.
- The hook installer still preserves unrelated commands and creates a raw backup before
  changing either provider's JSON file.

## 0.2.1-beta — 2026-07-14

### Fixed

- **The usage button no longer needs a second press.** A transient failure (a reset
  connection, a blip) is now retried once automatically — the retry was already
  happening, it was just being done by hand.
- **Its error messages stop lying.** Every non-401 failure used to be reported as
  "(네트워크)", so a rate limit or a server error sent you to check your wifi for our
  problem. A 429 and a 5xx now say what they are.
- **An expired token gives the advice that actually works.** The access token lives ~8h
  and *Claude Code* refreshes it — cc_navigator only reads it. So the old "log in again"
  was wrong: using any Claude Code session refreshes it. The panel now checks the expiry
  in the credentials file, says so, and does not waste a request on a token it can see is
  dead. (It deliberately does **not** refresh the token itself: the refresh token rotates,
  and consuming it without atomically persisting the new one could break your real Claude
  Code login. Not a risk a status panel gets to take.)

## 0.2.0-beta — 2026-07-13

First tagged release. **Beta** because of one known limitation (see below) that can put a
row in front of you that no longer has a Claude session behind it.

### New

- **Desktop notifications, on by default.** When a session becomes *your turn* — it starts
  waiting on your input, or it finishes its turn — a notification names that session, its
  status, and a one-line summary. Toggle it off in Settings (**시스템 알림**). Needs
  `notify-send` (`apt install libnotify-bin`); without it, nothing is notified and nothing
  breaks.
- **A usage button** at the bottom right shows the logged-in account's plan and its limits
  (session / weekly / per-model weekly), each with a bar, a percentage, and when it resets.
  It reads the OAuth token from `~/.claude/.credentials.json` and calls Anthropic's
  **undocumented internal** `/api/oauth/usage` endpoint — the same one Claude Code's own
  `/usage` uses. **It may stop working after any Claude Code update**; if it does, the
  popover says so and the rest of the panel carries on. The token is sent only to
  `api.anthropic.com` over TLS, is never logged or stored, and is not followed to a
  redirect.
- **Four collapsible status sections** (입력 필요 → 작업 중 → 보고 완료 → 확인 완료).
  Clicking a green dot marks a session seen (a ✓) and files it under 확인 완료.
- **Group view improvements:** per-group status counts, drag a group header to reorder,
  and group order that stays put as sessions come and go.
- **Docking is panel-aware:** the docked bar uses the monitor's work area, so it sits
  *beside* a system panel/dock instead of under it.

### Fixed

- **Live sessions no longer disappear.** `prune` aged out any session whose state file was
  more than 24h old — but an idle session fires no hooks, so a session you simply had not
  talked to overnight was deleted from the panel while tmux still had it running. Liveness
  now comes from tmux alone; age only reaps records tmux cannot vouch for.
- **Detaching a docked panel restores its real size** instead of a ~150px stub, and no
  longer emits a negative-width GTK warning.
- **A missing binary no longer crashes anything.** `gdbus` absent used to take the doctor
  down with a raw traceback (printing *zero* checks on the exact fresh machine it exists to
  diagnose); a missing `notify-send` threw on a worker thread, invisibly, forever.
- **A tmux stutter no longer bursts notifications.** A tick whose tmux query did not answer
  is blind, not empty; it used to erase the notification baseline, so the next good tick
  re-notified every waiting session.
- Switching sort mode no longer moves the window.

### Doctor

- `python3` is now actually *probed* (it imports `gi`, `Gtk`, and `cairo`) instead of being
  assumed present because `/usr/bin/python3` exists — the most common fresh-machine failure
  used to print `[ok]`.
- `gdbus` and `notify-send` are checked. **The documented `apt` line was incomplete**: it
  now includes `python3-gi-cairo`, without which the app cannot start.

### Known limitations

- **Ghost rows.** If Claude Code dies without firing `SessionEnd` (a crash, a `kill`) while
  its tmux pane survives, its row stays in the panel indefinitely — and typing a reply into
  that row types into whatever is now in that pane (usually your shell). Ending the pane
  clears it. The fix (asking tmux whether the pane still runs Claude) is the next release's
  first job; it is the reason this one is a beta.
- The usage endpoint is undocumented and may break, as described above.

## 0.1.0

Unreleased development history. See the git log.
