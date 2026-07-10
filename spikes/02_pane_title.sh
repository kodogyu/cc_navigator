#!/bin/sh
# Does tmux capture the OSC-2 title an inner program sets, as #{pane_title}?
# Safe and headless: private -L socket, empty config, killed on exit.
set -e
S=ccnav_spike_title
tmux -L $S kill-server 2>/dev/null || true
tmux -L $S -f /dev/null new-session -d -s t
tmux -L $S send-keys -t t "printf '\033]2;\342\234\263 TEST (proj)\007'" Enter
sleep 1
echo "pane_title=[$(tmux -L $S display -p -t t '#{pane_title}')]"
echo "window_name=[$(tmux -L $S display -p -t t '#{window_name}')]"
tmux -L $S kill-server 2>/dev/null || true
echo "expected: pane_title=[✳ TEST (proj)], window_name unchanged"
