# cc_navigator — system notifications on "your turn"

Date: 2026-07-12
Status: Approved for implementation

Builds on the existing app poll loop (`app.py`), the pure status model (`model.status_key`),
the single subprocess site (`proc.run_command`), and the live-apply settings dialog. Same hard
constraints: Python 3.8 `/usr/bin/python3` only, stdlib + system PyGObject/GTK3, zero third-party
deps, every module starts `from __future__ import annotations`, no `match`, no `X | Y` runtime
annotations.

## 1. Problem

Claude Code's own desktop notification says a session wants attention but not *which* one, and
the panel only shows state passively — the user must be looking at it. When a session becomes
"your turn" (needs input, or finished its turn) while the user's eyes are elsewhere, nothing
actively tells them, and the panel gives no per-session desktop nudge of its own.

## 2. Goal

When a session transitions **into** `입력 대기` (input-needed) or `보고 완료` (reported/idle) — i.e.
it becomes the user's turn — fire exactly one desktop notification naming that session, its
status, and a short summary. Toggleable in Settings, default on.

Non-goals (YAGNI, "간단하게"): click-to-jump, persistent/critical urgency, action buttons.

## 3. Design

### 3.1 Trigger (what counts as "your turn")

Per tick, each row has a status via `model.status_key`: `input` / `working` / `reported`.
Notify when a session's status **changes** and the new status is `input` or `reported`. This
covers `working→input`, `working→reported`, and the cross-transitions `input↔reported`. A status
that does not change fires nothing, so a session that stays reported while its message updates is
silent.

**First-tick priming.** On the first `_apply_rows`, the app has no prior statuses; notifying then
would burst one notification per already-waiting session at startup. So the first tick seeds the
baseline and fires nothing. From the second tick on, transitions fire — including a genuinely new
session that appears already reported/input (its prior status is absent → a transition).

**Toggle-off keeps the baseline.** When `notifications` is off the app still updates its status
baseline every tick, so re-enabling does not replay a backlog.

### 3.2 New module `src/ccnav/notify.py` (pure + thin sender)

- `NOTIFY_STATUSES = (model.INPUT_NEEDED, model.REPORTED)`.
- `changed_rows(prev_status: Dict[str, str], rows) -> Tuple[List[Tuple[Row, str]], Dict[str, str]]`
  — **pure**. Returns `(fires, new_status_map)`: `fires` is the rows whose status changed into a
  NOTIFY status; `new_status_map` maps every current session id → its status (for the next tick).
  No GTK, no threads, no I/O — fully unit-testable.
- `notification_for(row, status) -> Notification` — **pure**. A `Notification(summary, body, icon)`:
  - `summary`: `🔴 <title>` for input, `🟢 <title>` for reported (glyphs mirror the panel dots).
  - `body`: `<상태명> — <요약>`, 상태명 = `입력 필요` / `보고 완료`. 요약 = `row.message` if set,
    else `row.last_prompt` (the hook blanks `message` for reported/idle, so last_prompt is the
    fallback context), else omit the `— …` tail. Both fields are already flattened+truncated by
    the hook; re-truncated to a notification-sane length here.
  - `icon`: absolute path to `icons/window_icon.png` if it exists, else `None`.
- `build_argv(n: Notification) -> List[str]` — **pure**. `["notify-send", "-a", "cc_navigator",
  ("-i", icon)?, summary, body]`.
- `send(row, status, run=proc.run_command) -> None` — builds the argv and calls `run` (the single
  subprocess site). `run` is injectable so tests capture the argv without spawning anything.

### 3.3 `app.py` — detect transitions, send off the GTK thread

- `__init__`: `self._prev_status: Dict[str, str] = {}`, `self._notif_seeded = False`, and an
  injectable `notify_send=notify.send` (so `_maybe_notify` is testable without a subprocess).
- `_apply_rows`: after `set_rows`, call `self._maybe_notify(collected.rows)`.
- `_maybe_notify(rows)`: compute `(fires, new_map) = notify.changed_rows(self._prev_status, rows)`;
  set `self._prev_status = new_map`. If not seeded → set seeded, return (no fires). If
  `not self._settings.notifications` → return. Else dispatch each fire on a daemon worker thread
  (the `jump`/`send` pattern) calling `self._notify_send(row, status)`, so a slow `notify-send`
  never blocks the GTK main thread.

### 3.4 `config.py` — the toggle

- `Settings.notifications: bool = True`; one line in `to_dict`; one `isinstance(..., bool)` line
  in `_coerce` (mirrors `keep_above`).

### 3.5 `ui.py` — the settings checkbox

- In `_build_settings_dialog`, a `Gtk.CheckButton("시스템 알림")` initialised from
  `s.notifications`, committing `config.with_updates(self._settings, notifications=...)` on toggle
  (identical to `항상 위에 표시`).

## 4. Testing (TDD, RED first each)

- `tests/test_notify.py`: `changed_rows` (transition fires; unchanged is silent; new session
  fires; vanished session drops from the map; working never fires); `notification_for`
  (input→🔴/입력 필요/message; reported→🟢/보고 완료/last_prompt fallback; empty→status-only);
  `build_argv` (flags, `-i` present/absent); `send` (fake `run` captures argv).
- `tests/test_app.py`: first tick seeds silently; `working→input` fires once; unchanged fires
  nothing; `notifications=False` fires nothing but still updates baseline.
- `tests/test_config.py`: `notifications` round-trips; default `True`; a non-bool in the file
  keeps the default.

## 5. Risks

- **Notification storms.** Bounded by status-change dedup + first-tick priming. A session flapping
  input↔reported would notify each flip; acceptable and rare (real state genuinely changed).
- **Blocking the UI.** Avoided by the daemon-thread dispatch, consistent with jump/send.
- **`notify-send` absence.** `proc.run_command` swallows a missing binary (returns non-zero); the
  feature degrades to silent, never crashes. The doctor/README note the dependency.
