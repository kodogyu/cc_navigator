"""Pure mapping from a Claude Code/Codex hook event to a session state.

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

# Claude agent-team attention is not a main-prompt blockade, but retaining its
# distinct reason lets the display reconcile it with an independent live pane
# title spinner.  The model presents it as green when the title is idle and as
# working when Claude is visibly active.
AGENT_NEEDS_INPUT = "agent_needs_input"

# PreToolUse fires for every tool. Only these two mean "the user must answer".
_WAITING_TOOLS = {"AskUserQuestion": "question", "ExitPlanMode": "plan"}

# A Notification is not synonymous with "the main prompt is blocked". Claude
# also sends lifecycle/status notices through this event. Keep the red state an
# evidence-based allowlist so a newly added informational notice cannot leave a
# session stuck asking for input forever.
INPUT_NOTIFICATIONS = frozenset({"permission_prompt", "elicitation_dialog"})

# idle_prompt means Claude finished and is waiting for the next prompt. The
# completion/response notices also prove that a preceding dialog no longer owns
# the prompt, so they clear a stale red wait to the same input-ready state. A
# moving native title can independently promote that green state to working.
IDLE_NOTIFICATIONS = frozenset({"idle_prompt"})
RESOLVED_NOTIFICATIONS = frozenset({
    "elicitation_complete",
    "elicitation_response",
})

# These notices carry useful UI information elsewhere, but do not change the
# main session's input state. In particular, a teammate asking for input does
# not block the main Claude prompt.
PASSIVE_NOTIFICATIONS = frozenset({
    "agent_completed",
    "auth_success",
    AGENT_NEEDS_INPUT,
})

# Display-time repair uses this to normalize red records written by older
# cc_navigator versions before the allowlist above existed.
NON_BLOCKING_NOTIFICATIONS = (
    IDLE_NOTIFICATIONS | RESOLVED_NOTIFICATIONS | PASSIVE_NOTIFICATIONS
)


def classify(payload: Dict[str, object]) -> Optional[Tuple[str, str]]:
    """Return (state, reason), or None when the event carries no state change."""
    event = payload.get("hook_event_name")

    if event in ("SessionStart", "UserPromptSubmit"):
        return (WORKING, "")

    if event == "Notification":
        reason = str(payload.get("notification_type") or "").strip().lower()
        if reason in INPUT_NOTIFICATIONS:
            return (WAITING, reason)
        # A completed dialog notice actively clears an older red prompt.
        if reason in IDLE_NOTIFICATIONS or reason in RESOLVED_NOTIFICATIONS:
            return (WAITING, STOP_IDLE)
        # Passive and unknown future notices are not evidence of a blocked main
        # prompt. Preserve the previous state rather than manufacturing a red
        # false alarm.
        return None

    # Codex emits PermissionRequest at the policy checkpoint, before it knows
    # whether an automatic reviewer will approve the operation or the user will
    # actually see a prompt.  Its payload has no final decision / routing field,
    # and an automatically approved request may emit no later hook at all.  It
    # therefore cannot be used as evidence that the session is blocking on the
    # user: doing so leaves a false red `permission` state behind.
    if event == "PermissionRequest":
        return None

    if event == "PreToolUse":
        tool = str(payload.get("tool_name") or "")
        if tool in _WAITING_TOOLS:
            return (WAITING, _WAITING_TOOLS[tool])
        if tool == "request_user_input":
            return (WAITING, "question")
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
