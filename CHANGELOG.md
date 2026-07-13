# Changelog

cc_navigator has no build artifact: **Settings ⚙ → 업데이트 확인** fast-forwards your
checkout to the latest `master` and restarts. So this file, not a download page, is how
you find out what changed — and what a new version starts doing on your machine.

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
