"""Join state files with live tmux panes into the rows the UI renders."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Set, Tuple

from . import hookstate

# Most working sessions update their record on every tool event (PreToolUse /
# PostToolUse). A record left at "working" with no update for this long usually
# means its final Stop hook was missed, so it is shown idle instead of spinning
# forever. Exception: a native spinner in the live pane title proves that a long
# operation is still progressing even though it has emitted no lifecycle event.
STALE_WORKING_SECONDS = 900

# Native pane-title activity frames. Claude alternates the two sparse Braille
# glyphs while a turn is still progressing; Codex uses the ten-frame sequence.
# A live frame is stronger evidence than an old hook timestamp: long-running
# work can legitimately emit no lifecycle hook for longer than the stale limit.
CLAUDE_TITLE_SPINNER_FRAMES = ("⠂", "⠐")
CODEX_TITLE_SPINNER_FRAMES = (
    "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏",
)


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
    background_process_ids: Tuple[str, ...] = ()
    background_task_ids: Tuple[str, ...] = ()
    background_output_active: bool = False
    provider: str = "claude"
    provisional: bool = False
    kind: str = "tmux"
    claude_pid: int = 0
    ai_title: str = ""
    runtime_id: str = ""

    @property
    def waiting(self) -> bool:
        return self.state == hookstate.WAITING

    @property
    def identity(self) -> str:
        """UI/runtime key, distinct for /branch panes sharing a session id."""
        return self.runtime_id or self.session_id

    @property
    def is_vscode(self) -> bool:
        """A VSCode extension-hosted session: no tmux pane, addressed by raising
        its editor window instead. Reply is unavailable for these; a jump maps to
        focusing the workspace's VSCode window (see gnome.activate_vscode_window)."""
        return self.kind == "vscode"

    @property
    def vscode_folder(self) -> str:
        """The workspace folder name a VSCode window's title carries
        ('... - <folder> - Visual Studio Code') -- the cwd's last path segment,
        which is how a jump finds the right editor window."""
        return group_label(self.cwd)

    @property
    def subagent_active(self) -> bool:
        """True while one or more subagents this session launched are still
        running -- the second, overlapping status icon is shown iff this is set."""
        return bool(self.subagent_ids)

    @property
    def background_process_active(self) -> bool:
        """True while a Codex background terminal still owns the same process."""
        return bool(self.background_process_ids)

    @property
    def background_task_active(self) -> bool:
        """True while Claude reports an in-flight shell or monitor task."""
        return bool(self.background_task_ids)

    @property
    def auxiliary_activity(self) -> bool:
        """Any work that runs independently of the main session's input state."""
        return (
            self.subagent_active
            or self.background_process_active
            or self.background_task_active
            or self.background_output_active
        )

    @property
    def window_title(self) -> str:
        """The address. tmux's set-titles-string puts exactly this on the window."""
        return "ccnav:" + self.tmux_session


class ClaudeTitleActivityTracker:
    """Promote a stale idle row only after its native title frame *moves*.

    A hook ``Stop`` can race Claude's final pane-title update and leave one
    static Braille frame behind. Merely seeing ``⠂`` or ``⠐`` therefore cannot
    override an input-ready state. Observing the frame change is independent
    evidence that the main Claude turn is live; a short grace window smooths
    over polls that happen to sample the same animation frame twice.
    """

    def __init__(self) -> None:
        self._frames = {}  # type: Dict[str, str]
        self._last_motion = {}  # type: Dict[str, float]

    def reconcile(
        self, rows: List[Row], now: float, grace_seconds: float,
    ) -> List[Row]:
        visible = set()  # type: Set[str]
        reconciled = []  # type: List[Row]
        frames = CLAUDE_TITLE_SPINNER_FRAMES
        grace_seconds = max(0.0, float(grace_seconds))

        for row in rows:
            key = row.identity
            visible.add(key)
            frame = row.title[0] if row.title else ""
            previous = self._frames.get(key)
            candidate = (
                row.provider == "claude"
                and row.state == hookstate.WAITING
                and row.reason == hookstate.STOP_IDLE
                and not row.auxiliary_activity
            )
            if not candidate:
                # Motion observed while a question or an explicitly tracked
                # background task owns the row must not be reused later as
                # evidence about the main session.
                self._last_motion.pop(key, None)
            elif previous is not None and frame in frames and frame != previous:
                self._last_motion[key] = now
            self._frames[key] = frame

            motion_at = self._last_motion.get(key)
            moving = (
                candidate
                and frame in frames
                and motion_at is not None
                and now - motion_at <= grace_seconds
            )
            # A red question/permission wait still owns the front indicator.
            # Likewise, known auxiliary work must keep the main input-ready dot
            # green and use only the back spinner. Promote just an otherwise
            # idle main session whose own native title is demonstrably moving.
            if moving:
                row = replace(row, state=hookstate.WORKING, reason="", message="")
            reconciled.append(row)

        for key in set(self._frames) - visible:
            self._frames.pop(key, None)
            self._last_motion.pop(key, None)
        return reconciled


def _tmux_runtime_id(socket: str, pane: str) -> str:
    """Stable opaque UI identity for one pane, independent of Claude's id."""
    digest = hashlib.sha256((socket + "\0" + pane).encode("utf-8")).hexdigest()[:24]
    return "tmux-pane-" + digest


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


def _background_process_ids(value, live: Optional[Set[str]]) -> Tuple[str, ...]:
    """Safe, live background identities from a state record.

    ``live is None`` keeps the stored values for pure/unit callers.  The real
    poller always supplies a kernel-verified set, so a process that exits drops
    its activity icon immediately even if no later Codex hook is emitted.
    """
    if not isinstance(value, list):
        return ()
    ids = tuple(str(x) for x in value)
    return ids if live is None else tuple(x for x in ids if x in live)


def _background_task_ids(value, clear_shell: bool = False) -> Tuple[str, ...]:
    """Opaque Claude shell/monitor task identities from a trusted hook record."""
    if not isinstance(value, list):
        return ()
    accepted = []
    for item in value:
        if not isinstance(item, str) or len(item) > 168 or ":" not in item:
            continue
        kind, task_id = item.split(":", 1)
        if (kind not in ("shell", "monitor") or not task_id
                or not all(ch.isalnum() or ch in "-_." for ch in task_id)):
            continue
        if clear_shell and kind == "shell":
            continue
        if item not in accepted:
            accepted.append(item)
    return tuple(accepted)


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


def _newest_vscode(records):
    """Newest record per session_id among the VSCode (non-tmux) records.

    tmux records are keyed (socket, pane); a VSCode session has neither, so it
    is keyed by its own session_id -- which is stable across a session's whole
    life. Two sessions open on the same workspace folder keep distinct rows
    because their session_ids differ."""
    newest = {}  # type: Dict[str, dict]
    for rec in records:
        if str(rec.get("kind") or "") != "vscode":
            continue
        sid = str(rec.get("session_id") or "")
        if not sid:
            continue
        current = newest.get(sid)
        if current is None or _as_int(rec.get("updated_at", 0)) > _as_int(
            current.get("updated_at", 0)
        ):
            newest[sid] = rec
    return newest


def _destale(row: Row, now: int, stale_seconds: int) -> Row:
    """Present a long-untouched 'working' row as idle/reported instead. Only a
    real hook-backed working row can go stale (a waiting one is already
    terminal). A provisional Codex row is rebuilt from a process that was
    positively observed alive this tick, so its process start time must not be
    mistaken for a missed Stop hook timestamp. A native pane-title spinner also
    prevents de-staling because it is an independent live activity signal."""
    frames = (
        CODEX_TITLE_SPINNER_FRAMES
        if row.provider == "codex" else CLAUDE_TITLE_SPINNER_FRAMES
    )
    title_reports_work = bool(row.title) and row.title[0] in frames
    if (row.state == hookstate.WORKING and not row.provisional
            and not title_reports_work and stale_seconds > 0
            and (now - row.updated_at) > stale_seconds):
        return replace(row, state=hookstate.WAITING, reason=hookstate.STOP_IDLE)
    return row


def _normalize_legacy_codex_permission(row: Row) -> Row:
    """Repair Codex permission rows written by older cc_navigator versions.

    A Codex ``PermissionRequest`` is emitted before the approval router decides
    whether an automatic reviewer can handle the operation.  It is not proof
    that the terminal is waiting for a person.  Current hooks no longer write
    this state; normalizing old records here removes an already-stuck false red
    indicator immediately after upgrading, without rewriting users' state files.
    """
    reason = row.reason.strip().lower()
    if (row.provider == "codex" and row.state == hookstate.WAITING
            and reason in ("permission", "permission_request", "permissionrequest")):
        return replace(row, state=hookstate.WORKING, reason="", message="")
    return row


def _normalize_claude_nonblocking_notification(row: Row) -> Row:
    """Repair status notifications older builds incorrectly presented as red.

    Completion, dialog-response, authentication, idle, and agent-team notices
    are not evidence that the main prompt needs input. A live native title frame
    is independent evidence that Claude is working; otherwise present the row
    as input-ready. This takes effect immediately without rewriting state files.
    """
    if (row.provider == "claude" and row.state == hookstate.WAITING
            and row.reason.strip().lower()
            in hookstate.NON_BLOCKING_NOTIFICATIONS):
        if (row.title and row.title[0] in CLAUDE_TITLE_SPINNER_FRAMES):
            return replace(row, state=hookstate.WORKING, reason="", message="")
        return replace(row, reason=hookstate.STOP_IDLE, message="")
    return row


def build_rows(
    records: List[Dict[str, object]],
    sessions_by_socket: Dict[str, Dict[str, str]],
    titles_by_socket: Dict[str, Dict[str, str]],
    live_pids: Set[object] = frozenset(),
    now: Optional[int] = None,
    stale_seconds: int = STALE_WORKING_SECONDS,
    live_background_ids: Optional[Set[str]] = None,
    inactive_background_shell_panes: Set[Tuple[str, str]] = frozenset(),
) -> List[Row]:
    """A tmux row exists iff its pane is live in tmux; a VSCode row exists iff
    its process identity is in `live_pids` (the poller's kernel liveness check).

    When `now` is given, a 'working' row untouched for longer than `stale_seconds`
    is shown as idle (see _destale). `now` is None for callers that do not want
    that (existing tests), so staleness is opt-in per call."""
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
                background_process_ids=_background_process_ids(
                    rec.get("background_process_ids"), live_background_ids),
                background_task_ids=_background_task_ids(
                    rec.get("background_task_ids"),
                    clear_shell=(socket, pane) in inactive_background_shell_panes),
                provider=("codex" if rec.get("provider") == "codex" else "claude"),
                provisional=(rec.get("provisional") is True),
                runtime_id=_tmux_runtime_id(socket, pane),
            )
        )
    for sid, rec in _newest_vscode(records).items():
        pid = _as_int(rec.get("claude_pid", 0))
        started = _as_int(rec.get("claude_start_time", 0))
        process_key = (pid, started) if started > 0 else pid
        if process_key not in live_pids:
            continue  # the owning claude process is gone -> the session ended
        cwd = str(rec.get("cwd") or "")
        # Headline priority for a VSCode session: its AI-generated tab title (the
        # name the user sees in VSCode), then the last real prompt, then -- via
        # ui.primary_line's fallback to tmux_session -- the workspace folder. The
        # folder alone cannot tell two sessions in one workspace apart; the title
        # can, and matches what VSCode shows.
        ai_title = str(rec.get("ai_title") or "")
        last_prompt = str(rec.get("last_prompt") or "")
        rows.append(
            Row(
                session_id=sid,
                socket="",
                pane="",
                tmux_session=group_label(cwd),
                title=ai_title or last_prompt,
                state=str(rec.get("state") or ""),
                reason=str(rec.get("reason") or ""),
                message=str(rec.get("message") or ""),
                cwd=cwd,
                updated_at=_as_int(rec.get("updated_at", 0)),
                last_prompt=last_prompt,
                subagent_ids=_subagent_ids(rec.get("subagent_ids")),
                background_process_ids=_background_process_ids(
                    rec.get("background_process_ids"), live_background_ids),
                background_task_ids=_background_task_ids(
                    rec.get("background_task_ids")),
                kind="vscode",
                claude_pid=pid,
                ai_title=ai_title,
            )
        )
    rows = [_normalize_legacy_codex_permission(row) for row in rows]
    rows = [_normalize_claude_nonblocking_notification(row) for row in rows]
    if now is not None:
        rows = [_destale(row, now, stale_seconds) for row in rows]
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
