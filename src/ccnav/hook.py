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

# A turn ending (Stop) or a new one beginning (SessionStart / UserPromptSubmit)
# means no subagent from a prior turn can still be running, so these clear the
# running-subagent set. This is the safety net that bounds a leak if a
# SubagentStop is ever missed (e.g. a crash): the set self-heals next turn.
_SUBAGENT_RESET_EVENTS = frozenset({"Stop", "SessionStart", "UserPromptSubmit"})


def _prev_subagent_ids(previous: Optional[Mapping[str, object]]) -> list:
    ids = (previous or {}).get("subagent_ids")
    return [str(x) for x in ids] if isinstance(ids, list) else []


def _next_subagent_ids(
    event: str, payload: Mapping[str, object],
    previous: Optional[Mapping[str, object]],
) -> list:
    """The set of running-subagent ids after this event: SubagentStart adds the
    payload's agent_id, SubagentStop removes it, a turn boundary clears it, and
    every other event carries the prior set forward unchanged. A SubagentStop
    with no agent_id removes nothing (it can't match one) and leans on the
    turn-boundary reset to self-heal."""
    ids = _prev_subagent_ids(previous)
    if event in _SUBAGENT_RESET_EVENTS:
        return []
    agent_id = str(payload.get("agent_id") or "")
    if event == "SubagentStart":
        if agent_id and agent_id not in ids:
            ids.append(agent_id)
    elif event == "SubagentStop":
        ids = [x for x in ids if x != agent_id]
    return ids


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

    event = str(payload.get("hook_event_name") or "")
    subagent_ids = _next_subagent_ids(event, payload, previous)

    classified = hookstate.classify(payload)
    if classified is None:
        # No main-state change -- a SubagentStart/Stop, or an ignored tool event.
        # Persist ONLY if the running-subagent set actually changed; otherwise
        # there is nothing to write. The main state is carried forward verbatim,
        # so a red "input" wait survives a subagent starting or finishing (that
        # concurrent case is exactly what the second icon exists to show).
        if subagent_ids == _prev_subagent_ids(previous):
            return None
        prev = previous if isinstance(previous, dict) else {}
        state = str(prev.get("state") or hookstate.WORKING)
        reason = str(prev.get("reason") or "")
        message = _flatten(prev.get("message"), MESSAGE_LIMIT)
        last_prompt = _flatten(prev.get("last_prompt"), PROMPT_LIMIT)
        cwd = str(payload.get("cwd") or prev.get("cwd") or "")
    else:
        state, reason = classified

        # A finished turn (Stop -> WAITING/idle, the green "reported" dot) must
        # STAY green. PostToolUse maps to WORKING as a resume signal -- what
        # un-sticks a red "input" dot after the user answers -- but one can also
        # arrive AFTER Stop (a late tool event from the turn that just ended),
        # which must not re-light the working spinner on an already-idle session.
        # Only UserPromptSubmit/SessionStart begin a new working phase. This
        # guards ONLY the idle-green state, so a red wait is still cleared.
        if (state == hookstate.WORKING
                and event == "PostToolUse"
                and isinstance(previous, dict)
                and previous.get("state") == hookstate.WAITING
                and previous.get("reason") == hookstate.STOP_IDLE):
            return None

        if event == "UserPromptSubmit":
            prompt = payload.get("user_prompt")
            if not isinstance(prompt, str):
                prompt = payload.get("prompt")
            last_prompt = _flatten(prompt, PROMPT_LIMIT)
        else:
            last_prompt = _flatten((previous or {}).get("last_prompt"), PROMPT_LIMIT)
        # A reported/idle (green) session is not blocking on anything, so it must
        # not carry a "waiting" message: idle_prompt's Notification text ("Claude
        # is waiting for your input") would otherwise render on a GREEN row and
        # read as a contradiction. Stop already arrives message-less; this makes
        # the idle-notification path match it.
        if reason == hookstate.STOP_IDLE:
            message = ""
        else:
            message = _flatten(payload.get("message"), MESSAGE_LIMIT)
        cwd = str(payload.get("cwd") or "")

    return {
        "session_id": session_id,
        "cwd": cwd,
        "tmux_socket": socket,
        "tmux_pane": pane,
        "state": state,
        "reason": reason,
        "message": message,
        "updated_at": now,
        "last_prompt": last_prompt,
        "subagent_ids": subagent_ids,
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
