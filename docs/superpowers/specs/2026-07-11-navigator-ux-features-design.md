# cc_navigator — UX feature batch (settings, collapse, refresh, detail, install)

Date: 2026-07-11
Status: Approved for planning

Builds on `2026-07-10-cc-navigator-design.md` and the settings feature already merged
(`config.py`, the HeaderBar gear, the live-apply dialog). Same hard constraints apply:
Python 3.8 `/usr/bin/python3` only, stdlib + system PyGObject/GTK3, zero third-party deps,
every module starts `from __future__ import annotations`, no `match`, no `X | Y` runtime
annotations.

## 1. Problem

The panel works, but five rough edges remain from real use:

1. Appearance is fixed — no window transparency or background colour, and no way to see
   which version is running.
2. The panel floats over everything with no way to shrink it out of the way while keeping it
   reachable.
3. A session whose Claude Code has exited but whose shell/pane survives lingers as a stale row
   until the 24-hour age-out, and there is no manual way to clear it.
4. A row shows only a one-line summary; there is no way to see the full working directory or
   what the session was last asked to do.
5. Running it means knowing the repo path and the `/usr/bin/python3` incantation; there is no
   simple install.

## 2. Goals

- Settings gain transparency, panel background colour, and a visible version string.
- A collapse toggle shrinks the panel to its titlebar and restores it.
- Ended sessions disappear automatically (a `SessionEnd` hook) and on demand (a refresh button).
- Expanding a row shows the full path, the last submitted prompt, state/reason, and freshness;
  re-clicking collapses it; the window grows and shrinks to fit.
- An `install` script puts `cc-navigator` on `PATH`; the app-launcher entry, login autostart,
  and Claude Code hook wiring become toggles inside settings.

## 3. Non-goals

- Update checking over the network (violates the no-network / no-deps posture). Version is
  displayed, not checked.
- A structured "current task" view — Claude Code does not expose one to hooks. The last user
  prompt stands in for it.
- Persisting the collapsed state across restarts (decided: runtime-only; the panel reopens
  expanded).
- A full theme editor. Only the panel *background* colour is settable; text and row colours stay
  themed (contrast safety).
- Prompt history beyond the single most-recent prompt.

## 4. Verified constraints (Claude Code hooks, confirmed 2026-07-11)

- **`SessionEnd` exists.** `hook_event_name == "SessionEnd"`, common fields `session_id`,
  `cwd`, `transcript_path`, plus `source` (one of `clear`, `resume`, `logout`,
  `prompt_input_exit`, `bypass_permissions_disabled`, `other`). Every `source` means "this
  `session_id` is over", so the handler deletes that session's state file regardless of source.
- **`UserPromptSubmit` carries the prompt text**, field name `user_prompt`. The hook reads
  `user_prompt` and falls back to `prompt` for forward/backward tolerance.
- The hook command (`bin/cc-navigator-hook`) is already wired for SessionStart, UserPromptSubmit,
  Notification, Stop, PreToolUse. Adding SessionEnd is one more matcher in `settings.json`.

## 5. Architecture — what changes

Five features, grouped by the layer they touch. Shared plumbing: `config.Settings`, the existing
`.ccnav` CSS provider, and the hook.

### 5.1 Settings model additions (`config.py`)

Two new fields on the frozen `Settings` dataclass, coerced exactly like the existing ones (never
raise on a hand-edited/garbage file):

- `opacity: float = 1.0`, clamped to `[OPACITY_MIN, OPACITY_MAX] = [0.3, 1.0]`. Garbage/NaN →
  default then clamp (same path as `poll_seconds`).
- `bg_color: str = ""`. `""` means "no override, keep the theme". A non-empty value must match
  `^#[0-9A-Fa-f]{6}$`; anything else coerces to `""`.

`to_dict`, `from_dict`/`_coerce`, and `with_updates` extend to cover both. No other field changes.

The collapse state and the integration toggles (5.5) are **not** in `Settings` — collapse is
transient, and integration toggles reflect external filesystem state, not preferences.

### 5.2 Appearance + version (`ui.py`, `ccnav/__init__.py`, `app.py`)

- `apply_settings` also calls `self.set_opacity(settings.opacity)`.
- `_apply_css` (generalised from today's `_apply_font`) builds one `.ccnav` rule set from both
  font size and background colour, so one provider carries both:
  `.ccnav { background-color: <hex>; }` plus `.ccnav, .ccnav * { font-size: <n>pt; }`, each part
  emitted only when set. Loading empty data clears it.
- `ccnav/__init__.py` defines `__version__ = "0.1.0"`. The settings dialog shows
  `cc-navigator v{__version__}` in a footer label. `app.main` grows a tiny `--version` argument
  that prints and exits 0 (the launcher passes args through).
- Settings dialog gains an **Appearance** frame (font size, background colour via
  `Gtk.ColorButton` with a "테마 그대로" clear affordance, opacity via a `Gtk.Scale`), a
  **Window** frame (corner, width, height, keep-above, all-workspaces), a **Behavior** frame
  (poll interval), and an **Integration** frame (5.5). Every control still live-applies through
  `_commit_settings`.

Colour handling detail: `Gtk.ColorButton` yields a `Gdk.RGBA`; we store the `#rrggbb` hex (drop
alpha — opacity is separate). Reading `""` back means the button shows a neutral default and the
CSS omits `background-color`.

### 5.3 Collapse toggle (`ui.py`)

A toggle button in the HeaderBar (icon `pan-up-symbolic` / `pan-down-symbolic`). Collapsed: hide
the content box (listbox + status), remember the current height, and `resize` the window to its
titlebar's natural height. Expanded: show the box and `resize` back to `settings.height` (or the
detail-expanded height if a row is open). Runtime-only; nothing persisted.

### 5.4 Refresh — auto + manual

**Auto (hook).** `hookstate` stays the state-producing mapping. The delete path is separate:
`hook.main` inspects `hook_event_name`; on `SessionEnd` it calls a new
`statestore.remove(state_dir, session_id)` (unlink, tolerate missing, validate the id first) and
returns 0 without writing. All other events keep today's build-record-and-write path. The hook
still never raises or blocks.

**Manual (button).** A refresh button (`view-refresh-symbolic`) in the HeaderBar calls a new
`Application.refresh()` that sets `self._wake`, so the poll thread immediately re-runs
`collect_rows` — which prunes any pane already gone from tmux. Cheap, no new tmux code.

`settings.json` needs the SessionEnd matcher; the Integration hook toggle (5.5) writes the full
recommended set including it.

### 5.5 Session detail view (`hook.py`, `statestore.py`, `ui.py`)

**Hook capture (carry-forward).** The record gains `last_prompt: str`. Because every hook event
overwrites the whole file, a prompt captured on `UserPromptSubmit` must survive later events that
carry no prompt. So `build_record` takes an optional `previous` record: on `UserPromptSubmit`,
`last_prompt = truncate(user_prompt or prompt)`; otherwise `last_prompt = previous.last_prompt`
(or `""`). `hook.main` loads the prior record via a new `statestore.read_one(state_dir,
session_id)` (returns `None` on missing/garbage). Truncation limit `PROMPT_LIMIT = 300` chars —
prompts run longer than the notification `message`, whose limit stays 200. `model.Row` /
`build_rows` carry `last_prompt` through
(defaulting to `""`), so a record written before this change still renders.

**UI expansion.** Today selecting a row reveals `[reply entry] [jump]`. The revealed area gains a
detail block above them: full `cwd` (selectable), `last_prompt` (wrapped, ellipsized to ~3 lines),
a `state · reason` line, and a relative "N분 전 갱신" from `updated_at`. Clicking the already-open
row collapses it (toggle selection on click). On expand the window grows to the content's natural
height, capped at the monitor height; on collapse it returns to `settings.height`.

### 5.6 Install + integration (`install`, `integration.py`, `ui.py`, reuse `doctor.py`)

**`install`** — a POSIX `sh` script: symlink `bin/cc-navigator` → `~/.local/bin/cc-navigator`
(idempotent, resolves the real path like the launcher does), and print a notice if `~/.local/bin`
is not on `PATH`. Nothing else; the rest are settings toggles.

**`integration.py`** — pure-ish helpers that read and write external state, each with a
`*_installed()`/`*_enabled()` predicate and install/remove actions, all idempotent and atomic:

- App launcher: `~/.local/share/applications/cc-navigator.desktop`.
- Autostart: `~/.config/autostart/cc-navigator.desktop`.
- Claude Code hooks: merge the recommended hook set (SessionEnd included) into
  `~/.claude/settings.json`, preserving any hooks the user already has, backing up first, writing
  atomically; the remove action strips only our entries. The canonical hook definition is
  centralised so `doctor.py` and `integration.py` cannot drift.

The Integration frame in settings shows each as a `Gtk.CheckButton` whose initial state comes from
the predicate; toggling runs the install/remove action and reports failure in a label rather than
crashing.

## 6. Error handling

Consistent with the project's rule — a broken navigator must never break Claude Code, and a
failure must be visible, never silently swallowed as success.

- The SessionEnd delete and the prompt carry-forward read run inside the hook's existing
  never-raise envelope; a failed prior-record read degrades to "no previous prompt", not a crash.
- `config` coercion clamps opacity and rejects a bad colour to `""`; a corrupt config still yields
  a working panel.
- Integration actions that touch `~/.claude/settings.json` back up and write atomically; a write
  failure leaves the original intact and surfaces a message in the dialog.
- The manual refresh only wakes the poll thread; it inherits the poll loop's existing
  survive-a-raising-collect behaviour.

## 7. Testing

Pure logic tested with no filesystem/GTK:

- `config`: opacity garbage/NaN/clamp; colour accept-`#rrggbb` / reject-everything-else / `""`.
- `hook`: SessionEnd routes to delete (not write); non-SessionEnd still writes; `last_prompt`
  captured from `user_prompt`, falls back to `prompt`, carried forward across a prompt-less event,
  truncated at the limit.
- `statestore`: `remove` unlinks and tolerates missing; `read_one` returns `None` on
  missing/garbage and the record otherwise.
- `integration`: `.desktop` create/remove idempotent; `settings.json` merge preserves foreign
  hooks, is idempotent, and remove strips only ours — all in a temp dir.

GTK parts under the existing `DISPLAY` guard, asserting only what is observable: CSS carries the
colour/opacity, the detail block builds with the expected labels, the collapse toggle hides the
content box, `--version` prints the version.

## 8. Build order

1. Settings additions — opacity, background colour, version (§5.1, §5.2).
2. Collapse toggle (§5.3).
3. Hook change + refresh — SessionEnd delete, manual refresh (§5.4).
4. Session detail view — prompt capture + expandable row (§5.5).
5. Install + integration toggles (§5.6).

Steps 3 and 4 both change the hook contract, so they are adjacent. Step 5 (touching the user's
`~/.claude/settings.json` and `~/.local`) is last and most isolated.

## 9. Risks

- **Writing `~/.claude/settings.json`.** The highest-stakes change — it edits a file the user owns
  and that Claude Code reads. Mitigated by backup + atomic write + merge-don't-clobber + a remove
  path, and by sharing one hook definition with the doctor. This is the piece to review hardest.
- **CSD titlebar crowding.** The HeaderBar now holds collapse, refresh, gear, and close. On a
  narrow panel these must still fit; group the custom three at the start/end sensibly.
- **Prompt privacy.** `last_prompt` writes (truncated) user prompt text to the state dir, same
  location and posture as today's `message`/`cwd`. No new exposure surface, but noted.
- **Window auto-grow.** Measuring natural height and resizing can fight the WM; cap at monitor
  height and fall back to scrolling if measurement is unavailable.
