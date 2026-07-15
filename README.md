# cc_navigator

**English** · [한국어](README.ko.md)

> An always-on-top panel that lists every live Claude Code and Codex session, flags the ones
> waiting for you, and lets you **jump** to a session's terminal or **type a reply**
> straight into it — so you never hunt through a dozen windows to find which session
> a notification came from.

<p align="center">
  <img src="docs/images/screenshot-status.png" alt="cc_navigator panel" width="340">
</p>

> The panel's UI is in Korean; this document describes it in English.

---

## Why

You run many Claude Code and Codex sessions across many terminal windows. When one needs
input, a desktop notification fires — but it never says *which* session, and there
is no quick way to reach it.

cc_navigator gives every session **one row, one status glyph, and one click to get
there**: it flags who is waiting on you, raises that session's terminal on click,
sends a one-line reply into its tmux pane, and — when a session becomes *your turn*
— fires a desktop notification that actually names it.

---

## Install & run

An **X11 + GNOME + tmux** tool, deliberately narrow. No third-party Python deps —
the standard library plus the system `gi` bindings only.

| | |
|---|---|
| Display server | **X11** (not Wayland) |
| Desktop | **GNOME Shell with `Eval` unlocked** (blocked from GNOME 41 on; developed on 3.36.9). Without it the app still runs — only the jump buttons are disabled. |
| Multiplexer | **tmux ≥ 3.0** — one session per project, each in its own terminal window |
| Interpreter | **`/usr/bin/python3` ≥ 3.8 + PyGObject** — `apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0` |
| Also needed | `gdbus`, `xprop`, `notify-send` (libnotify) |

```sh
git clone https://github.com/kodogyu/cc_navigator.git
cd cc_navigator

./bin/cc-navigator-doctor      # checks prerequisites; prints exactly what to fix
./install                      # optional: symlink cc-navigator onto your PATH
cc-navigator &                 # or ./bin/cc-navigator &
```

Then, **to make sessions appear**, open **Settings** (the ⚙ gear) → **통합
(Integration)** and enable the hook for each tool you use:

- **Claude Code 훅 설정** merges into `~/.claude/settings.json`.
- **Codex 훅 설정** merges into `$CODEX_HOME/hooks.json` (normally
  `~/.codex/hooks.json`). Open `/hooks` in Codex afterward and trust
  `cc-navigator-hook` once.

Sessions appear after they start or resume. Codex rows carry a blue **Codex** badge.
Current Codex releases defer their first lifecycle hook until the first prompt, so the
panel also discovers the actual Codex process on same-user tmux sockets and shows a calm
provisional row immediately; the first hook replaces it with the full live state.

**Jump also needs tmux to own the window title** — it is the only address the panel
has. Put these in `~/.tmux.conf` (the doctor checks them):

```tmux
set -g set-titles on
set -g set-titles-string 'ccnav:#{session_name}'
```

> **Run the doctor first.** Besides listing what's missing, it reproduces a tmux
> 3.0a config pitfall — a `set`-family line without `-g`/`-q`/`-s` can silently
> corrupt the server and crash every session the moment you send a space — on a
> throwaway socket, so you find out before it touches your real tmux.

**Update:** Settings ⚙ → **업데이트 확인** fast-forwards this checkout to the latest
`master` and restarts (it refuses if you have local changes). What each version
changes — including what it starts doing on your machine — is in
[CHANGELOG.md](CHANGELOG.md).

---

## Screen layout

Each session is **one row**: a status glyph, its title, and (when selected) its
working directory, last prompt, a reply box, and a jump button.

- 🔴 **red** — waiting on you (permission, question, plan). A title-bar badge counts these.
- 🟢 **green** — finished its turn / idle. Click it to mark seen (a green check ✓).
- ↻ **spinning** — the main agent is working. Running **subagents or Codex background
  terminals** add a second spinner behind the main indicator. The front stays green
  whenever the main session is idle and ready for input; otherwise a working main
  agent with auxiliary work uses a calm blue dot.

Switch views with the **Sort by** dropdown:

- **상태별 정렬 (by status)** — four **collapsible** sections in priority order:
  입력이 필요한 세션 (needs input) → 작업 중 (working) → 보고 완료 (reported) →
  확인 완료 (acknowledged ✓).
- **그룹별 정렬 (by group)** — one folder per project directory, each with its own
  counts (red ● → ↻ → green ● → ✓). Drag headers to reorder groups, drag a row's
  ⠿ handle to move it, rename with the pencil, or press **자동 정렬** to re-group.

<p align="center">
  <img src="docs/images/screenshot-groups.png" alt="cc_navigator grouped by project" width="340">
</p>

**Act on a session:** click a row, type a line and press **Enter** to send it, or
click **"세션으로 이동"** to raise its terminal.

**Get it out of the way:** the top-left chevron collapses the panel to its title
bar; **long-press** it to dock the panel as a thin bar against a screen edge (slide
it along, detach to restore).

**Stay informed:** a desktop notification fires when a session becomes *your turn*
(needs input, or finished). On by default — toggle it, opacity, colour, font, and
more in **Settings**.

**Check your usage:** the bottom-right **"사용량 확인"** button shows separate Claude
Code and Codex sections, with plan, limit bars, percentages, and reset times. One
provider failing never hides the other. Claude Code still uses Anthropic's
**undocumented internal** `/api/oauth/usage` endpoint with the token from
`~/.claude/.credentials.json`. Codex uses the local `codex app-server` method
`account/rateLimits/read`, so cc_navigator never reads or sends the Codex auth token.

**External-tool warning:** the weekly token-cost estimate runs only after you explicitly
enable **"ccusage token-cost calculation"** in Settings. `ccusage` is a separate program
that reads local Claude conversation logs. cc_navigator never installs it or downloads it
through `npx`; verify its source and install it separately if you choose to use it.
