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

    if payload.get("hook_event_name") == "UserPromptSubmit":
        prompt = payload.get("user_prompt")
        if not isinstance(prompt, str):
            prompt = payload.get("prompt")
        last_prompt = str(prompt or "")[:PROMPT_LIMIT]
    else:
        last_prompt = str((previous or {}).get("last_prompt") or "")[:PROMPT_LIMIT]

    return {
        "session_id": session_id,
        "cwd": str(payload.get("cwd") or ""),
        "tmux_socket": socket,
        "tmux_pane": pane,
        "state": state,
        "reason": reason,
        "message": str(payload.get("message") or "")[:MESSAGE_LIMIT],
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
