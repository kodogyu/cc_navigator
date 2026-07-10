"""Atomic reads and writes of the per-session state files.

A reader must never observe a half-written file, so every write goes to a
temp file in the same directory and is then renamed over the target.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
import time
from typing import Dict, List, Optional, Set, Tuple

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")

MAX_AGE_SECONDS = 24 * 60 * 60


def is_safe_session_id(session_id: str) -> bool:
    """Session ids become filenames, so reject anything with a path in it."""
    return bool(session_id) and bool(_SAFE_ID.match(session_id))


def write(state_dir: pathlib.Path, record: Dict[str, object]) -> None:
    session_id = str(record["session_id"])
    if not is_safe_session_id(session_id):
        raise ValueError("unsafe session id: %r" % session_id)

    handle_fd, tmp_path = tempfile.mkstemp(dir=str(state_dir), prefix=".tmp-")
    try:
        with os.fdopen(handle_fd, "w") as handle:
            json.dump(record, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, str(state_dir / (session_id + ".json")))
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def read_all(state_dir: pathlib.Path) -> List[Dict[str, object]]:
    records = []  # type: List[Dict[str, object]]
    if not state_dir.is_dir():
        return records
    for path in sorted(state_dir.glob("*.json")):
        try:
            records.append(json.loads(path.read_text()))
        except (ValueError, OSError):
            continue
    return records


def read_one(state_dir: pathlib.Path, session_id: str) -> Optional[Dict[str, object]]:
    """Read one session's record, or None on missing/garbage/unsafe id. Never
    raises: the hook uses this to carry a prior field forward and must degrade
    to 'no previous record' rather than fail."""
    if not is_safe_session_id(session_id):
        return None
    try:
        text = (state_dir / (session_id + ".json")).read_text()
    except (ValueError, OSError):
        # OSError: missing/unreadable. ValueError: read_text() raises
        # UnicodeDecodeError (a ValueError) on non-UTF-8 bytes -- an
        # externally corrupted file must degrade to "no previous", not raise.
        return None
    try:
        record = json.loads(text)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def remove(state_dir: pathlib.Path, session_id: str) -> bool:
    """Delete one session's state file. Returns True iff a file was removed.
    Tolerates a missing file, an unsafe id, and an undeletable file (returns
    False) -- the SessionEnd hook must never raise back into Claude Code.

    _try_unlink alone is not enough here: it calls unlink(missing_ok=True),
    which swallows FileNotFoundError and reports success even when there was
    nothing to delete. That is fine for prune (it only ever unlinks a path a
    glob just found), but remove's contract is True iff a file was actually
    removed, so a missing file must be checked for first."""
    if not is_safe_session_id(session_id):
        return False
    path = state_dir / (session_id + ".json")
    try:
        exists = path.exists()
    except OSError:
        # exists() itself can raise for errors it does not treat as "absent"
        # (e.g. ENAMETOOLONG) -- an id this task never bounds in length must
        # still degrade to "nothing removed" rather than escape.
        return False
    if not exists:
        return False
    return _try_unlink(path)


def _try_unlink(path: pathlib.Path) -> bool:
    """Delete `path`, tolerating a file we are not allowed to remove.

    A state file owned by another uid in the shared /tmp fallback, or a
    directory that has turned read-only, makes unlink raise OSError
    (PermissionError, EROFS, ...). prune runs inside collect_rows on the poll
    thread, so an OSError escaping here would propagate out of collect_rows
    and kill the poller for the life of the process -- a dead poll thread
    leaves the window frozen on stale rows while looking alive, the exact
    silent-success failure this project exists to catch. So one undeletable
    file must leave itself in place and let prune carry on with the rest.
    Returns True only when the file actually went away.
    """
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def prune(
    state_dir: pathlib.Path,
    live_panes: Set[Tuple[str, str]],
    observed_sockets: Set[str],
    now: Optional[int] = None,
) -> int:
    """Delete state files whose pane is gone, that are stale, or that are junk.

    This is what makes a SessionEnd hook unnecessary: a session that is gone
    from tmux is gone from the model.

    `observed_sockets` is the set of tmux sockets whose pane list was actually
    obtained this tick. The liveness test -- "this pane is not in live_panes,
    so its session ended" -- is only applied to a record whose socket is in
    that set. A failed or merely slow query returns an empty pane list, and
    judging a record against that emptiness would delete every live session on
    a socket that only stuttered; a waiting session then fires no further hooks
    and its row never comes back (F3). A record on an UNOBSERVED socket is left
    alone on liveness.

    The age and junk checks stay unconditional: an unparseable file, or a
    genuinely stale one, is removed regardless -- so an unreachable socket's
    files are still reaped by age rather than leaking forever.
    """
    if now is None:
        now = int(time.time())
    if not state_dir.is_dir():
        return 0

    removed = 0
    for path in sorted(state_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (ValueError, OSError):
            if _try_unlink(path):
                removed += 1
            continue

        socket = str(record.get("tmux_socket") or "")
        pane = str(record.get("tmux_pane") or "")
        try:
            age = now - int(record.get("updated_at", 0))
        except (TypeError, ValueError):
            age = MAX_AGE_SECONDS + 1

        # A record with no socket/pane can never match a live pane, so it is
        # judged immediately (there is nothing to "observe"). A placeable
        # record is judged for liveness only if we actually queried its socket.
        placeable = bool(socket) and bool(pane)
        observed = socket in observed_sockets or not placeable
        gone = observed and (socket, pane) not in live_panes

        if gone or age > MAX_AGE_SECONDS:
            if _try_unlink(path):
                removed += 1
    return removed
