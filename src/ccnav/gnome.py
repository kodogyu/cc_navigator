"""Activate a gnome-terminal window by title, then prove it actually happened.

Two silent-failure bugs motivate the structure of this module:
  * `gdbus` exits 0 even when Eval returns "(false, ...)".
  * `win.activate(0)` returns normally and reports success while doing nothing
    when the window lives on another workspace.
So the effect is always verified through xprop, a different channel from the
one that performed the action.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from .proc import Runner, run_command

EVAL_ARGV = [
    "gdbus", "call", "--session",
    "--dest", "org.gnome.Shell",
    "--object-path", "/org/gnome/Shell",
    "--method", "org.gnome.Shell.Eval",
]

_MATCH_COUNT = re.compile(r"matched=(\d+)")


@dataclass(frozen=True)
class ActivationResult:
    ok: bool
    matched: int


def escape_js(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _match_first_js(title: str, prelude: str, action: str) -> str:
    """Build JS that finds windows titled exactly `title`, acts on the FIRST, counts all.

    Two matches mean two clients on one tmux session. The count comes back so
    the caller can warn instead of silently raising an arbitrary window.
    """
    return (
        "(function(){%svar found=null,n=0;"
        "global.get_window_actors().forEach(function(a){"
        "var w=a.get_meta_window();"
        "if((w.get_title()||'')==='%s'){n++;if(!found)found=w;}"
        "});if(found)%s;"
        "return 'matched='+n;})()" % (prelude, escape_js(title), action)
    )


def activate_js(title: str) -> str:
    """Main.activateWindow picks the workspace and the timestamp for us."""
    return _match_first_js(title, "", "Main.activateWindow(found)")


def activate_ts_js(title: str) -> str:
    """Fallback: an explicit, valid X timestamp. Never pass 0."""
    return _match_first_js(
        title,
        "var t=global.display.get_current_time_roundtrip();",
        "found.activate(t)",
    )


def parse_eval_result(stdout: str) -> Tuple[bool, str]:
    text = stdout.strip()
    return text.startswith("(true"), text


def parse_match_count(stdout: str) -> int:
    match = _MATCH_COUNT.search(stdout)
    return int(match.group(1)) if match else 0


def eval_js(js: str, run: Runner = run_command) -> Tuple[bool, str]:
    code, out = run(EVAL_ARGV + [js])
    if code != 0:
        return False, out
    return parse_eval_result(out)


def eval_available(run: Runner = run_command) -> bool:
    """Blocked from GNOME 41 onward. Probe once at startup."""
    ok, raw = eval_js("1+1", run=run)
    return ok and "2" in raw


def _active_window_id(run: Runner) -> Optional[str]:
    code, out = run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
    if code != 0 or "#" not in out:
        return None
    window_id = out.split("#", 1)[1].split(",")[0].strip()
    return window_id or None


def active_window_title(run: Runner = run_command) -> Optional[str]:
    window_id = _active_window_id(run)
    if not window_id:
        return None
    code, out = run(["xprop", "-id", window_id, "_NET_WM_NAME"])
    if code != 0 or "=" not in out:
        return None
    value = out.split("=", 1)[1].strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return None


def _wait_for_focus(
    title: str, run: Runner, sleep: Callable[[float], None], timeout: float
) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if active_window_title(run=run) == title:
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(0.1)


def activate_window_titled(
    title: str,
    run: Runner = run_command,
    sleep: Callable[[float], None] = time.sleep,
    timeout: float = 1.5,
) -> ActivationResult:
    """Activate the window titled exactly `title`. Verify, then retry once."""
    _, raw = eval_js(activate_js(title), run=run)
    matched = parse_match_count(raw)
    if _wait_for_focus(title, run, sleep, timeout):
        return ActivationResult(True, matched)

    # Eval claimed success but focus did not move: the activate(0) trap.
    _, raw = eval_js(activate_ts_js(title), run=run)
    matched = max(matched, parse_match_count(raw))
    return ActivationResult(_wait_for_focus(title, run, sleep, timeout), matched)
