# Spikes

These reproduce, from scratch, the assumptions the design rests on. Run them after a
GNOME, tmux or gnome-terminal upgrade — or on a new machine, before trusting the app.
Each cleans up after itself and uses a **private** tmux socket (`tmux -L ccnav_spike_*`),
so none of them can touch a real session.

    sh spikes/02_pane_title.sh     # safe, headless
    sh spikes/03_send_keys.sh      # safe, headless
    sh spikes/01_jump.sh           # opens a terminal window and steals focus briefly

`01_jump.sh` is for a **human** to run, not an agent, and not while someone is
working: it opens a gnome-terminal window and moves OS focus to it. `02` and `03`
need no display and touch nothing you can see.

Expected results are recorded in Appendix A of
`docs/superpowers/specs/2026-07-10-cc-navigator-design.md`. The two facts they pin:

- tmux captures the OSC-2 title an inner program sets, as `#{pane_title}` — this is
  the title cc_navigator reads to name a session.
- `send-keys -l --` delivers arbitrary text byte-exact, so a reply travels literally
  and is never re-interpreted as tmux key names or shell syntax.

## The honest tmux liveness probe

If you extend these, note two instruments that lie:

- `pgrep -x tmux` never matches the server — its `comm` is `tmux: server`. Use
  `tmux -L <sock> list-sessions; echo $?` (0 = alive).
- A socket path over ~108 bytes fails with `File name too long` for reasons that have
  nothing to do with what you are testing. Keep `-L` names short.

## The segfault these spikes deliberately avoid

`03_send_keys.sh` loads `-f /dev/null`, a known-good empty config, on purpose. tmux
3.0a segfaults if you send a **space** to a **detached** session under a config
carrying a `set`-family line without `-g`/`-q`/`-s` (see `docs/HANDOFF.md`). The
spikes never load the user's `~/.tmux.conf`, so they cannot trip it. `bin/cc-navigator-doctor`
is what reproduces that crash safely, on its own private socket, to check your config.
