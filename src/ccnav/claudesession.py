"""Best-effort liveness for Claude's opaque background shell tasks.

Claude hooks name a background Bash job with an opaque id, but do not always
emit another hook when that job exits.  The tmux pane still gives us a safe,
local signal: a live background terminal job has its own process group and is
not the terminal's foreground process group.  This module inspects only Linux
process metadata (names, parent ids and process-group ids); commands, arguments
and output are never read.
"""
from __future__ import annotations

import errno
import pathlib
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

PROCESS_LIMIT = 256


@dataclass(frozen=True)
class _ProcessStat:
    comm: str
    state: str
    ppid: int
    pgrp: int
    session: int
    tpgid: int


def _stat(proc_root: pathlib.Path, pid: int) -> Optional[_ProcessStat]:
    try:
        data = (proc_root / str(pid) / "stat").read_bytes()
    except OSError:
        return None
    left = data.find(b"(")
    right = data.rfind(b")")
    if left < 0 or right < left:
        return None
    fields = data[right + 1:].split()
    # After comm: state, ppid, pgrp, session, tty_nr, tpgid.
    if len(fields) < 6:
        return None
    try:
        return _ProcessStat(
            comm=data[left + 1:right].decode("utf-8", "replace"),
            state=fields[0].decode("ascii", "replace"),
            ppid=int(fields[1]),
            pgrp=int(fields[2]),
            session=int(fields[3]),
            tpgid=int(fields[5]),
        )
    except ValueError:
        return None


def _children(proc_root: pathlib.Path, pid: int) -> Tuple[List[int], bool]:
    """Children launched by any thread, plus whether the tree was observable."""
    task_root = proc_root / str(pid) / "task"
    try:
        paths = list(task_root.glob("*/children"))
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ESRCH):
            return [], True
        return [], False
    children = set()  # type: Set[int]
    for path in paths[:PROCESS_LIMIT]:
        try:
            text = path.read_text()
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ESRCH):
                continue
            return [], False
        for value in text.split():
            try:
                children.add(int(value))
            except ValueError:
                continue
    return sorted(children), True


def background_shell_active(
    pane_pid: int, proc_root: pathlib.Path = pathlib.Path("/proc"),
) -> Optional[bool]:
    """Whether a tmux pane's Claude process owns a live background terminal job.

    ``False`` is returned only after a complete, bounded observation. ``None``
    means the process tree could not be trusted, so callers must preserve stored
    hook state.  A zombie never counts as live. Claude's persistent MCP helpers
    inherit its foreground process group; background Bash jobs use another group
    whose id differs from the tty foreground group.
    """
    try:
        pane_pid = int(pane_pid)
    except (TypeError, ValueError):
        return None
    if pane_pid <= 1:
        return None

    pending = [(pane_pid, None)]  # type: List[Tuple[int, Optional[_ProcessStat]]]
    seen = set()  # type: Set[int]
    found_claude = False
    complete = True
    while pending and len(seen) < PROCESS_LIMIT:
        pid, owner = pending.pop()
        if pid in seen or pid <= 1:
            continue
        seen.add(pid)
        stat = _stat(proc_root, pid)
        if stat is None:
            # A child may legitimately exit during the walk, but on this tick
            # that is indistinguishable from a permission/read failure. Never
            # use an incomplete tree as proof that stored work has ended; the
            # next poll can make the positive no-background observation.
            complete = False
            continue
        if stat.comm == "claude":
            owner = stat
            found_claude = True
        elif owner is not None:
            if (stat.state != "Z" and stat.pgrp > 1
                    and stat.pgrp != owner.pgrp
                    and (stat.tpgid <= 0 or stat.pgrp != stat.tpgid)):
                return True

        children, observed = _children(proc_root, pid)
        complete = complete and observed
        pending.extend((child, owner) for child in children)

    if pending:  # process limit reached: absence was not proved
        complete = False
    if not found_claude or not complete:
        return None
    return False
