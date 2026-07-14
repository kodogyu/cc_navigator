"""Entry point invoked by Claude Code and Codex hooks.

Contract: write one state file, exit 0. Never block, never raise, never make
Claude Code wait on anything. cc_navigator not running is not an error.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Callable, Dict, Mapping, Optional

from . import hookstate, paths, procstat, statestore

# The entrypoint Claude Code exports when a session runs inside the VSCode
# extension (verified in the running process's environ). It is the one non-tmux
# session cc_navigator can still address: no pty/pane, but the editor window can
# be focused and the `claude` process gives liveness.
VSCODE_ENTRYPOINT = "claude-vscode"

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


# Prefixes of the synthetic "user" turns the IDE/CLI injects, which must never
# become a session's visible headline. A real prompt can legitimately start with
# '<' (a pasted XML snippet), so this matches the specific known wrappers rather
# than every '<'.
_SYNTHETIC_PROMPT_PREFIXES = (
    "<ide_opened_file",
    "<ide_selection",
    "<command-name",
    "<command-message",
    "<local-command-stdout",
    "<system-reminder",
)


def _is_synthetic_prompt(flattened: str) -> bool:
    """True if `flattened` (already whitespace-collapsed) is an injected turn, not
    something the user typed -- see _SYNTHETIC_PROMPT_PREFIXES."""
    return flattened.startswith(_SYNTHETIC_PROMPT_PREFIXES)


# How much of the transcript's tail to scan for the session's AI title. The
# `ai-title` records recur throughout the file, so the most recent one is always
# near the end; a bounded tail read keeps this cheap enough for the hook (which
# must never be slow) even on a multi-megabyte transcript.
_AI_TITLE_TAIL_BYTES = 65536


def _last_ai_title(transcript_path: str, tail_bytes: int = _AI_TITLE_TAIL_BYTES) -> str:
    """The session's AI-generated title -- what VSCode shows on the session tab --
    read from the last `ai-title` record in the transcript's tail. Returns "" for
    a missing/short/unreadable transcript or before any title has been generated.

    This is the ONE good per-session name for a VSCode session: it has no tmux
    pane title, and the last user prompt is a poor headline (and identical across
    sessions that opened the same file). A bytes tail-read tolerates a partial
    first line (we may seek into the middle of one) -- json.loads just fails on it
    and it is skipped."""
    if not transcript_path:
        return ""
    try:
        with open(transcript_path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - tail_bytes))
            tail = handle.read()
    except OSError:
        return ""
    title = ""
    for raw in tail.splitlines():
        if b'"ai-title"' not in raw:  # cheap prefilter before the JSON parse
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "ai-title":
            value = obj.get("aiTitle")
            if isinstance(value, str) and value.strip():
                title = value  # keep scanning; the LAST one wins (most recent)
    return _flatten(title, PROMPT_LIMIT)


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
    provider: str = "claude",
    find_claude_pid: Optional[Callable[[], int]] = None,
    find_claude_start_time: Optional[Callable[[int], int]] = None,
) -> Optional[Dict[str, object]]:
    pane = env.get("TMUX_PANE")
    socket = tmux_socket_from_env(env)
    kind = "tmux"
    claude_pid = 0
    claude_start_time = 0
    if not pane or not socket:
        # Not in tmux. The ONLY non-tmux session we can still address is a VSCode
        # extension-hosted one: it has no pane, but its editor window can be
        # focused and its `claude` process supplies liveness. Everything else
        # stays unaddressable and returns None exactly as before.
        if env.get("CLAUDE_CODE_ENTRYPOINT") != VSCODE_ENTRYPOINT:
            return None
        if find_claude_pid is None:
            find_claude_pid = lambda: procstat.find_claude_ancestor(os.getpid())
        claude_pid = find_claude_pid()
        if not claude_pid:
            return None  # cannot locate the owning claude process -> no liveness
        start_reader = find_claude_start_time or procstat.process_start_time
        claude_start_time = start_reader(claude_pid)
        kind = "vscode"
        socket = ""
        pane = ""

    # A VSCode session's headline: its AI-generated tab title. Read only for
    # VSCode sessions -- a tmux session already has its pane title, and this is a
    # file read we should not add to that path.
    ai_title = ""
    if kind == "vscode":
        ai_title = _last_ai_title(str(payload.get("transcript_path") or ""))

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
            flattened = _flatten(prompt, PROMPT_LIMIT)
            # The IDE injects synthetic "user" turns -- <ide_opened_file>, a
            # <command-name> run, a <local-command-stdout> echo -- that are not
            # something the user typed. Letting one become last_prompt makes it
            # the row's headline, which for a VSCode session (whose only headline
            # IS the last prompt) reads as garbage and, worse, hides which session
            # it is. Keep the previous real prompt instead of overwriting with one.
            if _is_synthetic_prompt(flattened):
                last_prompt = _flatten((previous or {}).get("last_prompt"), PROMPT_LIMIT)
            else:
                last_prompt = flattened
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
        "provider": "codex" if provider == "codex" else "claude",
        "cwd": cwd,
        "kind": kind,
        "claude_pid": claude_pid,
        "claude_start_time": claude_start_time,
        "ai_title": ai_title,
        "tmux_socket": socket,
        "tmux_pane": pane,
        "state": state,
        "reason": reason,
        "message": message,
        "updated_at": now,
        "last_prompt": last_prompt,
        "subagent_ids": subagent_ids,
    }


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    provider = "codex" if "--provider" in argv and "codex" in argv else "claude"
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
        record = build_record(
            payload, os.environ, int(time.time()), previous, provider=provider
        )
        if record is None:
            return 0
        statestore.write(state_dir, record)
    except Exception:
        pass  # a broken navigator must never break Claude Code
    return 0


if __name__ == "__main__":
    sys.exit(main())
