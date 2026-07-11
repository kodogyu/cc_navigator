"""Entry point invoked by Claude Code hooks.

Contract: write one state file, exit 0. Never block, never raise, never make
Claude Code wait on anything. cc_navigator not running is not an error.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, Mapping, Optional

from . import hookstate, paths, statestore

MESSAGE_LIMIT = 200
PROMPT_LIMIT = 300


def _flatten(value: object, limit: int) -> str:
    """Collapse a free-text field to a single bounded line: every whitespace run
    (newlines, tabs) becomes one space, then truncate. A prompt or message can
    be a multi-line blob -- a pasted diff, or a raw task-notification payload
    submitted as a turn -- and the panel renders one line per field, so a stored
    record must never carry embedded newlines or it wrecks the layout."""
    return " ".join(str(value or "").split())[:limit]


def tmux_socket_from_env(env: Mapping[str, str]) -> Optional[str]:
    """$TMUX is "<socket path>,<server pid>,<session id>"."""
    raw = env.get("TMUX")
    if not raw:
        return None
    return raw.split(",")[0] or None


def build_record(
    payload: Dict[str, object], env: Mapping[str, str], now: int,
    previous: Optional[Dict[str, object]] = None,
) -> Optional[Dict[str, object]]:
    pane = env.get("TMUX_PANE")
    socket = tmux_socket_from_env(env)
    if not pane or not socket:
        return None  # not in tmux: the session can never be addressed

    session_id = str(payload.get("session_id") or "")
    if not statestore.is_safe_session_id(session_id):
        return None

    classified = hookstate.classify(payload)
    if classified is None:
        return None
    state, reason = classified

    # A finished turn (Stop -> WAITING/idle, the green "reported" dot) must STAY
    # green. PostToolUse/SubagentStop map to WORKING as a resume signal -- that is
    # what un-sticks a red "input" dot after the user answers -- but one can also
    # arrive AFTER Stop (a late tool/subagent event from the turn that just ended),
    # which must not re-light the working spinner on an already-idle session. Only
    # UserPromptSubmit/SessionStart begin a new working phase. This guards ONLY the
    # idle-green state, so a red wait (a different reason) is still cleared.
    if (state == hookstate.WORKING
            and payload.get("hook_event_name") in ("PostToolUse", "SubagentStop")
            and isinstance(previous, dict)
            and previous.get("state") == hookstate.WAITING
            and previous.get("reason") == hookstate.STOP_IDLE):
        return None

    if payload.get("hook_event_name") == "UserPromptSubmit":
        prompt = payload.get("user_prompt")
        if not isinstance(prompt, str):
            prompt = payload.get("prompt")
        last_prompt = _flatten(prompt, PROMPT_LIMIT)
    else:
        last_prompt = _flatten((previous or {}).get("last_prompt"), PROMPT_LIMIT)

    return {
        "session_id": session_id,
        "cwd": str(payload.get("cwd") or ""),
        "tmux_socket": socket,
        "tmux_pane": pane,
        "state": state,
        "reason": reason,
        "message": _flatten(payload.get("message"), MESSAGE_LIMIT),
        "updated_at": now,
        "last_prompt": last_prompt,
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0

    if payload.get("hook_event_name") == "SessionEnd":
        session_id = str(payload.get("session_id") or "")
        try:
            statestore.remove(paths.ensure_state_dir(), session_id)
        except Exception:
            pass  # a broken navigator must never break Claude Code
        return 0

    # ensure_state_dir() is *designed to raise* (PermissionError on a foreign
    # state dir, OSError from mkdir/open) and read_one/write touch the disk, so
    # the whole non-SessionEnd path lives inside one guard: a broken navigator
    # must never break Claude Code. Before Task 9 only write() was protected;
    # moving ensure_state_dir()/read_one() to the top re-exposed that surface,
    # so the guard now spans acquire-dir -> read-previous -> build -> write.
    try:
        state_dir = paths.ensure_state_dir()
        session_id = str(payload.get("session_id") or "")
        previous = statestore.read_one(state_dir, session_id)
        record = build_record(payload, os.environ, int(time.time()), previous)
        if record is None:
            return 0
        statestore.write(state_dir, record)
    except Exception:
        pass  # a broken navigator must never break Claude Code
    return 0


if __name__ == "__main__":
    sys.exit(main())
