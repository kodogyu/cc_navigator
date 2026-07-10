#!/bin/sh
# Does `send-keys -l --` deliver arbitrary text byte-exact?
# Safe and headless: private -L socket, empty config (-f /dev/null), killed on exit.
# The empty config also keeps us clear of the tmux 3.0a segfault (see spikes/README.md).
set -e
S=ccnav_spike_send
OUT=$(mktemp)
trap 'tmux -L $S kill-server 2>/dev/null || true; rm -f "$OUT" "$OUT.expected"' EXIT
tmux -L $S kill-server 2>/dev/null || true
tmux -L $S -f /dev/null new-session -d -s t
sleep 0.5
tmux -L $S send-keys -t t "IFS= read -r line < /dev/tty; printf %s \"\$line\" > $OUT" Enter
sleep 0.5
PAYLOAD='yes; echo '"'"'x'"'"' "y" $HOME \ 한글 ✳ Enter C-c'
tmux -L $S send-keys -t t -l -- "$PAYLOAD"
tmux -L $S send-keys -t t Enter
sleep 0.8
printf '%s' "$PAYLOAD" > "$OUT.expected"
if cmp -s "$OUT" "$OUT.expected"; then echo "byte-exact OK"; else echo "MANGLED"; fi
