"""Atomic reads and writes of per-session-runtime state files.

A reader must never observe a half-written file, so every write goes to a
temp file in the same directory and is then renamed over the target. A Claude
``/branch`` may reuse one session id in multiple tmux panes, so tmux records are
additionally scoped by an opaque digest of their socket and pane.
"""
from __future__ import annotations

import hashlib
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


def _tmux_scope(socket: str, pane: str) -> str:
    """Opaque, filename-safe identity for one tmux location.

    Claude's ``/branch`` can keep the same session id alive in two panes.  The
    socket itself may contain private path components, so only a bounded digest
    is placed in the filename.
    """
    return hashlib.sha256((socket + "\0" + pane).encode("utf-8")).hexdigest()[:24]


def _record_path(state_dir: pathlib.Path, record: Dict[str, object]) -> pathlib.Path:
    session_id = str(record["session_id"])
    socket = str(record.get("tmux_socket") or "")
    pane = str(record.get("tmux_pane") or "")
    if socket and pane and str(record.get("kind") or "tmux") != "vscode":
        filename = session_id + "--tmux-" + _tmux_scope(socket, pane) + ".json"
        return state_dir / filename
    return state_dir / (session_id + ".json")


def _read_path(path: pathlib.Path) -> Optional[Dict[str, object]]:
    try:
        value = json.loads(path.read_text())
    except (ValueError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _legacy_path(state_dir: pathlib.Path, session_id: str) -> pathlib.Path:
    return state_dir / (session_id + ".json")


def _migrate_legacy_tmux_record(
    state_dir: pathlib.Path, session_id: str,
) -> None:
    """Move one pre-scoping record to its pane-qualified path, best effort."""
    legacy = _legacy_path(state_dir, session_id)
    record = _read_path(legacy)
    if record is None or str(record.get("session_id") or "") != session_id:
        return
    target = _record_path(state_dir, record)
    if target == legacy:
        return
    try:
        if target.exists():
            current = _read_path(target)
            old_time = int(record.get("updated_at", 0))
            current_time = int((current or {}).get("updated_at", 0))
            if current is not None and current_time >= old_time:
                legacy.unlink(missing_ok=True)
                return
        os.replace(str(legacy), str(target))
    except (OSError, TypeError, ValueError):
        # Migration is compatibility cleanup, never a reason to block a fresh
        # state write. Leaving the legacy file is safe: read_all can still use
        # it and the model de-duplicates records by tmux pane.
        return


def write(state_dir: pathlib.Path, record: Dict[str, object]) -> None:
    session_id = str(record["session_id"])
    if not is_safe_session_id(session_id):
        raise ValueError("unsafe session id: %r" % session_id)

    # Preserve an older unscoped record before writing this pane. If /branch
    # cloned the same session id into another pane, both records then survive
    # instead of the new event atomically replacing the old pane's only state.
    _migrate_legacy_tmux_record(state_dir, session_id)
    target = _record_path(state_dir, record)

    handle_fd, tmp_path = tempfile.mkstemp(dir=str(state_dir), prefix=".tmp-")
    try:
        with os.fdopen(handle_fd, "w") as handle:
            json.dump(record, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, str(target))
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
            value = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def read_one(
    state_dir: pathlib.Path, session_id: str,
    tmux_socket: str = "", tmux_pane: str = "",
) -> Optional[Dict[str, object]]:
    """Read one session's record, or None on missing/garbage/unsafe id. Never
    raises: the hook uses this to carry a prior field forward and must degrade
    to 'no previous record' rather than fail."""
    if not is_safe_session_id(session_id):
        return None
    if tmux_socket and tmux_pane:
        scoped = _record_path(state_dir, {
            "session_id": session_id,
            "tmux_socket": tmux_socket,
            "tmux_pane": tmux_pane,
        })
        record = _read_path(scoped)
        if record is not None and str(record.get("session_id") or "") == session_id:
            return record
        # Upgrade compatibility: accept the former session-only file only when
        # it belongs to this exact pane. A sibling made by /branch must never
        # donate its prompt, subagent set, or waiting state to the new pane.
        legacy = _read_path(_legacy_path(state_dir, session_id))
        if (legacy is not None
                and str(legacy.get("session_id") or "") == session_id
                and str(legacy.get("tmux_socket") or "") == tmux_socket
                and str(legacy.get("tmux_pane") or "") == tmux_pane):
            return legacy
        return None
    candidates = [
        record for record in read_all(state_dir)
        if str(record.get("session_id") or "") == session_id
    ]
    if not candidates:
        return None
    def updated_at(record: Dict[str, object]) -> int:
        try:
            return int(record.get("updated_at", 0))
        except (TypeError, ValueError):
            return 0
    return max(candidates, key=updated_at)


def remove(
    state_dir: pathlib.Path, session_id: str,
    tmux_socket: str = "", tmux_pane: str = "",
) -> bool:
    """Delete one runtime location, or a sole unambiguous unaddressed record.

    Returns True iff at least one file was removed.
    Tolerates a missing file, an unsafe id, and an undeletable file (returns
    False) -- the SessionEnd hook must never raise back into Claude Code.

    Records are matched by their bounded JSON session/location fields rather
    than trusting a filename supplied by hook input."""
    if not is_safe_session_id(session_id):
        return False
    try:
        paths = sorted(state_dir.glob("*.json"))
    except OSError:
        return False
    matches = []
    for path in paths:
        record = _read_path(path)
        if record is None or str(record.get("session_id") or "") != session_id:
            continue
        if tmux_socket and tmux_pane:
            if (str(record.get("tmux_socket") or "") != tmux_socket
                    or str(record.get("tmux_pane") or "") != tmux_pane):
                continue
        matches.append(path)
    # A background SessionEnd may omit tmux addressing. It is safe to clean up
    # the sole record, but with /branch multiple live panes can share this id;
    # deleting all of them would make surviving siblings disappear. Leave an
    # ambiguous set to the poller's pane-liveness pruning instead.
    if not tmux_socket and not tmux_pane and len(matches) > 1:
        return False
    removed = False
    for path in matches:
        removed = _try_unlink(path) or removed
    return removed


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
    live_pids: Set[object] = frozenset(),
    observed_pids: Set[object] = frozenset(),
) -> int:
    """Delete state files whose pane is gone, that are stale, or that are junk.

    This is what makes a SessionEnd hook unnecessary: a session that is gone
    from tmux is gone from the model.

    A VSCode (non-tmux) record has no pane, so its liveness comes from the
    kernel instead: `live_pids` is the set of claude process identities whose
    VS Code stdio transport is not known to be disconnected, and
    `observed_pids` is the set it actually checked. A VSCode record is reaped
    iff its claude_pid was observed and is not live --
    the same "never judge what you did not observe" rule the tmux path uses for
    an unanswered socket, so a pid the poller did not check is left alone.

    `observed_sockets` is the set of tmux sockets whose pane list was actually
    obtained this tick. The liveness test -- "this pane is not in live_panes,
    so its session ended" -- is only applied to a record whose socket is in
    that set. A failed or merely slow query returns an empty pane list, and
    judging a record against that emptiness would delete every live session on
    a socket that only stuttered; a waiting session then fires no further hooks
    and its row never comes back (F3). A record on an UNOBSERVED socket is left
    alone on liveness.

    The junk check stays unconditional: an unparseable file is removed
    regardless. The age check applies ONLY to a record whose socket was not
    observed, so an unreachable socket's files are reaped rather than leaking
    forever. A record whose pane tmux positively reports as live is never aged
    out -- an idle session fires no hooks, and killing it for that would be the
    same silent failure as F3, just on a slower clock.
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

        # A VSCode record is judged by its claude pid, never by a pane. Reap it
        # when the pid we checked is no longer a running claude; leave it alone
        # if we did not check it this tick (mirrors the unobserved-socket rule).
        if str(record.get("kind") or "") == "vscode":
            try:
                pid = int(record.get("claude_pid", 0))
            except (TypeError, ValueError):
                pid = 0
            try:
                started = int(record.get("claude_start_time", 0))
            except (TypeError, ValueError):
                started = 0
            process_key = (pid, started) if started > 0 else pid
            gone_pid = (
                pid <= 0
                or (process_key in observed_pids and process_key not in live_pids)
            )
            if gone_pid and _try_unlink(path):
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

        # Age is a reaper for records tmux cannot vouch for -- NOT a liveness
        # test for ones it can. A live, observed pane is kept however old its
        # record is: a session the user simply did not talk to overnight fires
        # no hooks, so its record ages while the session is perfectly alive.
        # Ageing it out made every idle-but-live session vanish from the panel
        # after 24h, the silent failure this project exists to prevent. Liveness
        # is derived from tmux, never announced by hook recency.
        if gone or (not observed and age > MAX_AGE_SECONDS):
            if _try_unlink(path):
                removed += 1
    return removed
