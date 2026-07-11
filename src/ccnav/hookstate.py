"""Pure mapping from a Claude Code hook event to a session state.

Kept free of I/O so the whole state machine is testable from fixtures.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

WAITING = "waiting"
WORKING = "working"

# Stop's reserved reason. It drives the UI's green "reported / idle, not
# blocking" dot, so it must mean ONLY "Claude finished its turn" -- never a
# Notification asking for attention. A Notification passes notification_type
# through verbatim, so classify actively refuses to let one shadow this value.
STOP_IDLE = "idle"

# PreToolUse fires for every tool. Only these two mean "the user must answer".
_WAITING_TOOLS = {"AskUserQuestion": "question", "ExitPlanMode": "plan"}


def classify(payload: Dict[str, object]) -> Optional[Tuple[str, str]]:
    """Return (state, reason), or None when the event carries no state change."""
    event = payload.get("hook_event_name")

    if event in ("SessionStart", "UserPromptSubmit"):
        return (WORKING, "")

    if event == "Notification":
        # Empty matcher: every notification_type counts, including
        # elicitation_dialog, which other tools drop.
        reason = str(payload.get("notification_type") or "notification")
        # A notification means "Claude wants your attention", so it must never
        # carry Stop's reserved reason (which the UI paints green as "reported,
        # not blocking"). If a payload's type collides with it, relabel it.
        if reason == STOP_IDLE:
            reason = "notification"
        return (WAITING, reason)

    if event == "PreToolUse":
        tool = str(payload.get("tool_name") or "")
        if tool in _WAITING_TOOLS:
            return (WAITING, _WAITING_TOOLS[tool])
        return None

    if event == "PostToolUse":
        # A tool just finished, so Claude has resumed working. This is the signal
        # that un-sticks a red "input" dot once the user answers: after they reply
        # to a permission prompt (Notification) or an AskUserQuestion/ExitPlanMode
        # (PreToolUse), nothing else flips the session back to WORKING -- the next
        # thing that happens is a tool completing, right before Claude generates
        # its "..." response. Without this the dot stays red while Claude works.
        return (WORKING, "")

    # SubagentStart / SubagentStop carry NO main-agent state change. They drive a
    # separate axis -- the count of running subagents -- maintained by
    # hook.build_record (add on Start, remove on Stop). The main agent keeps
    # whatever state it already had: while its helpers run it is typically parked
    # (still WORKING), and if it is genuinely blocked on the user (a red wait)
    # that must PERSIST, not be cleared by a subagent finishing. So both return
    # None here and the main state is carried forward verbatim.
    if event in ("SubagentStart", "SubagentStop"):
        return None

    if event == "Stop":
        return (WAITING, STOP_IDLE)

    return None
