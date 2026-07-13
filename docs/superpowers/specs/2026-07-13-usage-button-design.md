# cc_navigator — account usage button

Date: 2026-07-13
Status: Approved for implementation

Same hard constraints as the rest of the project: Python 3.8 `/usr/bin/python3` only,
stdlib + system PyGObject/GTK3, zero third-party deps, every module starts
`from __future__ import annotations`, no `match`, no `X | Y` runtime annotations,
nothing blocking ever runs on the GTK main thread.

## 1. Problem

The panel shows what every session is doing, but says nothing about the account driving
them. Checking how much of the plan's limit is left means opening a Claude Code session
and running `/usage` — the panel is already the place you look at all your sessions, so
it should be able to answer this too.

## 2. Goal

A button at the bottom of the panel. Click it → a popover above the button shows the
logged-in account's plan and its current limits (session / weekly / per-model weekly)
with a bar, a percentage, and when each resets.

Non-goals (YAGNI): periodic auto-refresh, a settings toggle, local token/cost
aggregation from transcripts.

## 3. Data source

Claude Code's own `/usage` reads `GET https://api.anthropic.com/api/oauth/usage` with
the OAuth access token from `~/.claude/.credentials.json` (`claudeAiOauth.accessToken`),
sending `anthropic-beta: oauth-2025-04-20`. Verified by hand: HTTP 200 with

```jsonc
{
  "five_hour": {"utilization": 26.0, "resets_at": "2026-07-13T06:29:59Z", ...},
  "seven_day": {"utilization": 7.0,  "resets_at": "2026-07-19T11:59:59Z", ...},
  "limits": [
    {"kind": "session",       "percent": 26, "severity": "normal", "resets_at": "...", "scope": null},
    {"kind": "weekly_all",    "percent": 7,  "severity": "normal", "resets_at": "...", "scope": null},
    {"kind": "weekly_scoped", "percent": 3,  "severity": "normal", "resets_at": "...",
     "scope": {"model": {"display_name": "Fable"}}}
  ],
  ...
}
```

`limits[]` is the authoritative list (it already carries kind, percent, severity, reset,
and the scoped model), so parsing reads `limits[]` and ignores the rest.

**This endpoint is undocumented and internal.** It can change or vanish with any Claude
Code update. The design therefore treats *every* field as untrusted: a shape we do not
recognise degrades to a plain message, never an exception or a crash.

## 4. Design

### 4.1 New module `src/ccnav/usage.py`

- `Entry = NamedTuple(label, percent, severity, resets_at)` — one limit row.
- `Usage = NamedTuple(plan, entries)`.
- `read_credentials(path=None) -> Optional[Credentials]` — reads
  `claudeAiOauth.{accessToken, subscriptionType, rateLimitTier}`. Missing, unreadable, or
  garbage file → `None`, never raises.
- `plan_name(credentials) -> str` — "Max 20x" from `subscriptionType`/`rateLimitTier`;
  falls back to the raw string, then to "".
- `parse(payload) -> Usage` — **pure**. Walks `limits[]`, keeps entries that carry a
  numeric `percent`, labels them:
  `session` → "세션 (5시간)", `weekly_all` → "주간 (전체)",
  `weekly_scoped` → "주간 (<model display_name>)", anything else → its raw `kind`.
  Non-dict payload, missing `limits`, or zero usable entries → `Usage(plan, [])`, which the
  caller renders as the "unknown shape" message.
- `describe_reset(iso, now) -> str` — **pure**. "2시간 12분 후 리셋" / "7월 19일 리셋"
  (>24h) / "" when unparseable. Handles the `+00:00` offset that Python 3.8's
  `fromisoformat` accepts and the trailing `Z` it does not.
- `fetch(token, opener=urllib.request.urlopen, timeout=8.0) -> Tuple[Optional[dict], str]`
  — the only I/O. Returns `(payload, "")` or `(None, <korean error>)`. `opener` is
  injectable so tests never touch the network.
- `load(read=read_credentials, opener=...) -> Tuple[Optional[Usage], str]` — the seam the
  UI calls. Maps every failure to a Korean message:
  - no credentials → "로그인 정보를 찾을 수 없습니다"
  - HTTP 401/403 → "인증이 만료되었습니다 — claude에서 다시 로그인하세요"
  - network/timeout → "사용량을 가져오지 못했습니다 (네트워크)"
  - unrecognised shape (0 entries) → "사용량 형식을 알 수 없습니다 (Claude Code 업데이트?)"

### 4.2 `ui.py` — the button and its popover

- A `Gtk.Button("사용량 확인")` packed last in `_content`, below the status label.
- Click → open a `Gtk.Popover` (`PositionType.TOP`) anchored to the button, showing
  "불러오는 중…", and disable the button (the `_jumping`-style in-flight guard, so a
  double click cannot start two fetches).
- The fetch runs on a **daemon worker thread**; the result returns via `GLib.idle_add`,
  which rebuilds the popover body: the plan, then one row per entry —
  label, a `Gtk.LevelBar` (0–100), "26%", and the reset text. On failure: the message.
- `NavigatorWindow(..., usage_load=usage.load)` — injected, so tests drive the popover
  with a fake loader and no network.

## 5. Testing (TDD, RED first)

- `tests/test_usage.py`: credentials (missing / garbage / valid); `parse` (session +
  weekly + scoped model; unknown kind; missing percent; non-dict → empty); `describe_reset`
  (minutes/hours, >24h, unparseable); `fetch` (fake opener 200 → payload; 401 → auth
  message; URLError → network message); `load` wiring each failure to its message.
- `tests/test_ui.py`: the button exists at the bottom of the content box; clicking calls
  the injected loader and disables the button; a successful load renders a row per entry
  with its percent; a failed load renders the message.

## 6. Risks

- **The endpoint breaks.** Contained: parsing is total (never raises), and an unrecognised
  shape surfaces as a message that names the likely cause. The panel keeps working.
- **Blocking the UI.** Avoided by the worker thread, consistent with jump/send/notify.
- **The token.** Read from the user's own `~/.claude/.credentials.json` and sent only to
  `api.anthropic.com` over HTTPS — the same request Claude Code itself makes. It is never
  logged, printed, or persisted anywhere by cc_navigator.
