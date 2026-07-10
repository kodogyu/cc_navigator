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


def tmux_socket_from_env(env: Mapping[str, str]) -> Optional[str]:
    """$TMUX is "<socket path>,<server pid>,<session id>"."""
    raw = env.get("TMUX")
    if not raw:
        return None
    return raw.split(",")[0] or None


def build_record(
    payload: Dict[str, object], env: Mapping[str, str], now: int
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

    return {
        "session_id": session_id,
        "cwd": str(payload.get("cwd") or ""),
        "tmux_socket": socket,
        "tmux_pane": pane,
        "state": state,
        "reason": reason,
        "message": str(payload.get("message") or "")[:MESSAGE_LIMIT],
        "updated_at": now,
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

    record = build_record(payload, os.environ, int(time.time()))
    if record is None:
        return 0

    try:
        statestore.write(paths.ensure_state_dir(), record)
    except Exception:
        pass  # a broken navigator must never break Claude Code
    return 0


if __name__ == "__main__":
    sys.exit(main())
