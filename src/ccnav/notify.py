"""Desktop notifications when a session becomes "your turn".

A session that transitions *into* the input-needed or reported state is one the
user must now act on. `changed_rows` detects those transitions purely (no I/O),
`notification_for` renders the text purely, and `send` is the only side effect --
a `notify-send` through `proc.run_command`, the app's single subprocess site.
"""
from __future__ import annotations

import pathlib
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from . import model, proc

# The two "your turn" states. 'working' never notifies -- the agent is still busy.
NOTIFY_STATUSES = (model.INPUT_NEEDED, model.REPORTED)

_STATUS_GLYPH = {model.INPUT_NEEDED: "🔴", model.REPORTED: "🟢"}
_STATUS_NAME = {model.INPUT_NEEDED: "입력 필요", model.REPORTED: "보고 완료"}
_DETAIL_LIMIT = 120  # the body's answer-summary tail; keeps the popup one glance


class Notification(NamedTuple):
    summary: str
    body: str


def changed_rows(
    prev_status: Dict[str, str], rows: Sequence["model.Row"]
) -> Tuple[List[Tuple["model.Row", str]], Dict[str, str]]:
    """Rows whose status changed into a NOTIFY_STATUSES value, plus the full
    current status map (every session id -> its status) for the next tick. A
    session absent from `prev_status` counts as changed, so a newly appeared
    reported/input session fires; the caller suppresses the very first tick to
    avoid a startup burst."""
    fires = []  # type: List[Tuple[model.Row, str]]
    new_map = {}  # type: Dict[str, str]
    for row in rows:
        status = model.status_key(row)
        new_map[row.session_id] = status
        if status in NOTIFY_STATUSES and prev_status.get(row.session_id) != status:
            fires.append((row, status))
    return fires, new_map


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())  # flatten any stray whitespace, defensively
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def notification_for(row: "model.Row", status: str) -> Notification:
    """The popup text: '<glyph> <title>' summary, '<status> — <answer summary>'
    body. The answer summary is the waiting message (input) or, since the hook
    blanks that for a reported/idle turn, the last prompt (reported)."""
    glyph = _STATUS_GLYPH.get(status, "")
    name = _STATUS_NAME.get(status, status)
    title = row.title.strip() or row.session_id
    if row.provider == "codex":
        title = "Codex · " + title
    summary = ("%s %s" % (glyph, title)).strip()
    detail = _shorten((row.message or "").strip() or (row.last_prompt or "").strip(), _DETAIL_LIMIT)
    body = "%s — %s" % (name, detail) if detail else name
    return Notification(summary, body)


def _icon_path() -> Optional[str]:
    # icons/window_icon.png sits at the repo root (src/ccnav/notify.py -> up 2).
    path = pathlib.Path(__file__).resolve().parents[2] / "icons" / "window_icon.png"
    return str(path) if path.exists() else None


def build_argv(notification: Notification, icon: Optional[str]) -> List[str]:
    argv = ["notify-send", "-a", "cc_navigator"]
    if icon:
        argv += ["-i", icon]
    argv += [notification.summary, notification.body]
    return argv


def send(
    row: "model.Row",
    status: str,
    run: Callable[[Sequence[str]], Tuple[int, str]] = proc.run_command,
    icon: Optional[str] = None,
) -> None:
    """Fire one desktop notification for `row`. `run` and `icon` are injectable
    so tests capture the argv without spawning a process or touching the disk;
    the default resolves the app icon and goes through proc.run_command."""
    if icon is None:
        icon = _icon_path()
    run(build_argv(notification_for(row, status), icon))
