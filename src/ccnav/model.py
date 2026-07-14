"""Join state files with live tmux panes into the rows the UI renders."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from . import hookstate


@dataclass(frozen=True)
class Row:
    session_id: str
    socket: str
    pane: str
    tmux_session: str
    title: str
    state: str
    reason: str
    message: str
    cwd: str
    updated_at: int
    last_prompt: str = ""
    subagent_ids: Tuple[str, ...] = ()
    provider: str = "claude"
    provisional: bool = False

    @property
    def waiting(self) -> bool:
        return self.state == hookstate.WAITING

    @property
    def subagent_active(self) -> bool:
        """True while one or more subagents this session launched are still
        running -- the second, overlapping status icon is shown iff this is set."""
        return bool(self.subagent_ids)

    @property
    def window_title(self) -> str:
        """The address. tmux's set-titles-string puts exactly this on the window."""
        return "ccnav:" + self.tmux_session


def live_pane_keys(sessions_by_socket: Dict[str, Dict[str, str]]) -> Set[Tuple[str, str]]:
    keys = set()  # type: Set[Tuple[str, str]]
    for socket, panes in sessions_by_socket.items():
        for pane in panes:
            keys.add((socket, pane))
    return keys


def _as_int(value) -> int:
    """Coerce a state file's updated_at to an int, or 0 if it is garbage.

    read_all returns whatever is on disk, and a hand-edited file may carry a
    non-numeric timestamp. build_rows runs on a one-second GTK timer, so an
    unhandled TypeError/ValueError here would propagate out of the timeout
    callback and freeze the model for the life of the process. Mirror
    statestore.prune's policy: an unparseable timestamp means maximally stale
    (0), so the row sorts oldest and prune deletes its file on age.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _subagent_ids(value) -> Tuple[str, ...]:
    """Coerce a state file's subagent_ids to a tuple of strings. A hand-edited
    or older file may carry a non-list (or be missing the field entirely), which
    must degrade to 'no running subagents' rather than raise in the poll loop."""
    if not isinstance(value, list):
        return ()
    return tuple(str(x) for x in value)


def _newest_per_pane(records):
    newest = {}  # type: Dict[Tuple[str, str], dict]
    for rec in records:
        key = (str(rec.get("tmux_socket") or ""), str(rec.get("tmux_pane") or ""))
        if not key[0] or not key[1]:
            continue
        current = newest.get(key)
        if current is None or _as_int(rec.get("updated_at", 0)) > _as_int(
            current.get("updated_at", 0)
        ):
            newest[key] = rec
    return newest


def build_rows(
    records: List[Dict[str, object]],
    sessions_by_socket: Dict[str, Dict[str, str]],
    titles_by_socket: Dict[str, Dict[str, str]],
) -> List[Row]:
    """A row exists iff its state file's pane is currently live in tmux."""
    rows = []  # type: List[Row]
    for (socket, pane), rec in _newest_per_pane(records).items():
        sessions = sessions_by_socket.get(socket, {})
        if pane not in sessions:
            continue
        titles = titles_by_socket.get(socket, {})
        rows.append(
            Row(
                session_id=str(rec.get("session_id") or ""),
                socket=socket,
                pane=pane,
                tmux_session=sessions[pane],
                title=titles.get(pane) or pane,
                state=str(rec.get("state") or ""),
                reason=str(rec.get("reason") or ""),
                message=str(rec.get("message") or ""),
                cwd=str(rec.get("cwd") or ""),
                updated_at=_as_int(rec.get("updated_at", 0)),
                last_prompt=str(rec.get("last_prompt") or ""),
                subagent_ids=_subagent_ids(rec.get("subagent_ids")),
                provider=("codex" if rec.get("provider") == "codex" else "claude"),
                provisional=(rec.get("provisional") is True),
            )
        )
    rows.sort(key=sort_key)
    return rows


def sort_key(row: "Row"):
    """Display priority: sessions waiting for input come first (they need the
    user), then most-recently-updated. Both components are volatile -- a hook
    event flips `waiting` and bumps `updated_at` -- so the UI must re-sort on
    every change, not assume a fixed order. Shared with the UI's list sort func
    so build_rows and the live panel can never order rows differently."""
    return (0 if row.waiting else 1, -row.updated_at)


# --- sectioning for the two list views (Sort by Status / Sort by Group) -------

INPUT_NEEDED = "input"
REPORTED = "reported"
WORKING_SECTION = "working"
# The Sort-by-Status sections, in display order (matches the layout design).
STATUS_SECTIONS = (INPUT_NEEDED, REPORTED, WORKING_SECTION)


def status_key(row: "Row") -> str:
    """Which Sort-by-Status section a row belongs to: 'input' (the agent is
    blocking on the user), 'reported' (a finished Stop turn, idle), or
    'working'. Same three-way split the status dot/spinner uses."""
    if not row.waiting:
        return WORKING_SECTION
    return REPORTED if row.reason == hookstate.STOP_IDLE else INPUT_NEEDED


def group_key(row: "Row") -> str:
    """Which Sort-by-Group section a row belongs to: its project directory."""
    return row.cwd or ""


def group_label(cwd: str) -> str:
    """The short project name for a group header -- the cwd's last path
    segment (e.g. '/home/u/projects/cc_navigator' -> 'cc_navigator')."""
    trimmed = (cwd or "").rstrip("/")
    return trimmed.rsplit("/", 1)[-1] or "~"
