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


def prune(
    state_dir: pathlib.Path,
    live_panes: Set[Tuple[str, str]],
    now: Optional[int] = None,
) -> int:
    """Delete state files whose pane is gone, that are stale, or that are junk.

    This is what makes a SessionEnd hook unnecessary: a session that is gone
    from tmux is gone from the model.
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
            path.unlink(missing_ok=True)
            removed += 1
            continue

        key = (str(record.get("tmux_socket") or ""), str(record.get("tmux_pane") or ""))
        try:
            age = now - int(record.get("updated_at", 0))
        except (TypeError, ValueError):
            age = MAX_AGE_SECONDS + 1

        if key not in live_panes or age > MAX_AGE_SECONDS:
            path.unlink(missing_ok=True)
            removed += 1
    return removed
