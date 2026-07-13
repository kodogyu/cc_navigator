# cc_navigator

**English** · [한국어](README.ko.md)

> An always-on-top panel that lists every live Claude Code session, flags the ones
> waiting for you, and lets you **jump** to a session's terminal or **type a reply**
> straight into it — so you never hunt through a dozen windows to find which session
> a notification came from.

<p align="center">
  <img src="docs/images/screenshot-status.png" alt="cc_navigator panel" width="340">
</p>

> The panel's UI is in Korean; this document describes it in English.

---

## Why

You run many Claude Code sessions across many terminal windows. When one needs
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
| Interpreter | **`/usr/bin/python3` ≥ 3.8 + PyGObject** — `apt install python3-gi gir1.2-gtk-3.0` |
| Also needed | `gdbus`, `xprop`, `notify-send` (libnotify) |

```sh
git clone https://github.com/kodogyu/cc_navigator.git
cd cc_navigator

./bin/cc-navigator-doctor      # checks prerequisites; prints exactly what to fix
./install                      # optional: symlink cc-navigator onto your PATH
cc-navigator &                 # or ./bin/cc-navigator &
```

Then, **to make sessions appear**, enable the hooks once: open **Settings** (the ⚙
gear) → **통합 (Integration)** → **"Claude Code 훅 설정"**. That merges a tiny hook
shim into `~/.claude/settings.json`; every Claude Code session started afterward
shows up. The panel makes **zero** tmux calls until the first hook fires.

> **Run the doctor first.** Besides listing what's missing, it reproduces a tmux
> 3.0a config pitfall — a `set`-family line without `-g`/`-q`/`-s` can silently
> corrupt the server and crash every session the moment you send a space — on a
> throwaway socket, so you find out before it touches your real tmux.

---

## Screen layout

Each session is **one row**: a status glyph, its title, and (when selected) its
working directory, last prompt, a reply box, and a jump button.

- 🔴 **red** — waiting on you (permission, question, plan). A title-bar badge counts these.
- 🟢 **green** — finished its turn / idle. Click it to mark seen (a green check ✓).
- ↻ **spinning** — Claude is working. A working session that spawned **subagents**
  shows a calm **blue dot** with a second spinner behind it.

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
