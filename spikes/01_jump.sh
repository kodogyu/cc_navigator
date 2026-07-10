#!/bin/sh
# Does set-titles own the outer window title, and does Eval activation actually move
# focus? Opens a gnome-terminal window and STEALS FOCUS for a moment.
# HUMAN-RUN ONLY -- not for an agent, not while someone is working. Private -L socket.
set -e
S=ccnav_spike_jump
EV() { gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
        --method org.gnome.Shell.Eval "$1"; }
tmux -L $S kill-server 2>/dev/null || true
tmux -L $S -f /dev/null new-session -d -s spikeproj
tmux -L $S set -g set-titles on
tmux -L $S set -g set-titles-string 'ccnav:#{session_name}'
gnome-terminal --window -- tmux -L $S attach -t spikeproj
sleep 3
echo "--- titles containing ccnav: ---"
for w in $(xprop -root _NET_CLIENT_LIST | sed 's/.*# //' | tr -d ' ' | tr ',' '\n'); do
  n=$(xprop -id "$w" _NET_WM_NAME 2>/dev/null | sed 's/.*= //')
  case "$n" in *ccnav:spikeproj*) echo "  $w $n";; esac
done
echo "--- activate via Main.activateWindow, then verify with xprop ---"
EV "(function(){var found=null,n=0;global.get_window_actors().forEach(function(a){var w=a.get_meta_window();if((w.get_title()||'')==='ccnav:spikeproj'){n++;if(!found)found=w;}});if(found)Main.activateWindow(found);return 'matched='+n;})()"
sleep 1
A=$(xprop -root _NET_ACTIVE_WINDOW | sed 's/.*# *//' | cut -d, -f1)
echo "  focused now: $(xprop -id "$A" _NET_WM_NAME | sed 's/.*= //')"
echo "  expected:    \"ccnav:spikeproj\""
tmux -L $S kill-server 2>/dev/null || true
