"""Best-effort discovery and background liveness for Claude tmux sessions.

Claude hooks name a background Bash job with an opaque id, but do not always
emit another hook when that job exits.  The tmux pane still gives us a safe,
local signal: a live background terminal job has its own process group and is
not the terminal's foreground process group. Detached jobs can be reparented
outside that tree; their writable Claude ``tasks/*.output`` descriptor is the
fallback liveness signal. This module inspects only Linux process metadata and
descriptor paths/flags; commands, arguments, environment, and output contents
are never read.
"""
from __future__ import annotations

import errno
import hashlib
import os
import pathlib
import re
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

from . import hookstate

PROCESS_LIMIT = 256
FD_LIMIT = 256
PROCESS_SCAN_LIMIT = 4096
_TASK_OUTPUT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,167}\.output$")


@dataclass(frozen=True)
class ClaudeProcess:
    pid: int
    started_at: int
    cwd: str


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


def _process(proc_root: pathlib.Path, pid: int) -> Optional[ClaudeProcess]:
    directory = proc_root / str(pid)
    try:
        started_at = int(directory.stat().st_mtime)
        cwd = os.readlink(str(directory / "cwd"))
    except OSError:
        return None
    return ClaudeProcess(pid=pid, started_at=started_at, cwd=cwd)


def find_claude_process(
    pane_pid: int, proc_root: pathlib.Path = pathlib.Path("/proc"),
) -> Optional[ClaudeProcess]:
    """Find the live Claude binary below one tmux pane without reading argv.

    tmux already limits this probe to panes whose foreground command is exactly
    ``claude``. The bounded process-tree walk then reads only process names,
    relationships, start metadata, and cwd; commands, arguments, and output are
    never inspected.
    """
    try:
        pending = [int(pane_pid)]
    except (TypeError, ValueError):
        return None
    seen = set()  # type: Set[int]
    matches = []  # type: List[ClaudeProcess]
    while pending and len(seen) < PROCESS_LIMIT:
        pid = pending.pop()
        if pid <= 1 or pid in seen:
            continue
        seen.add(pid)
        stat = _stat(proc_root, pid)
        if stat is not None and stat.comm == "claude":
            process = _process(proc_root, pid)
            if process is not None:
                matches.append(process)
        children, _observed = _children(proc_root, pid)
        pending.extend(children)
    return max(matches, key=lambda value: (value.started_at, value.pid)) if matches else None


def provisional_session_id(socket: str, pane: str) -> str:
    digest = hashlib.sha256((socket + "\0" + pane).encode("utf-8")).hexdigest()[:24]
    return "claude-pane-" + digest


def provisional_record(
    socket: str, pane: str, process: ClaudeProcess, working: bool = False,
) -> dict:
    """Non-persistent fallback for a live Claude pane missing hook state.

    This covers the second pane created by ``/branch`` immediately. A real hook
    record has an equal-or-newer timestamp and replaces this candidate for the
    same pane without the fallback ever touching persistent state.
    """
    return {
        "session_id": provisional_session_id(socket, pane),
        "provider": "claude",
        "provisional": True,
        "cwd": process.cwd,
        "tmux_socket": socket,
        "tmux_pane": pane,
        "state": hookstate.WORKING if working else hookstate.WAITING,
        "reason": "" if working else hookstate.STOP_IDLE,
        "message": "",
        "updated_at": process.started_at,
        "last_prompt": "",
        "subagent_ids": [],
        "background_process_ids": [],
        "background_task_ids": [],
    }


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


def _writable_fd(fdinfo: pathlib.Path) -> bool:
    """Whether one proc fdinfo entry is open for writing (metadata only)."""
    try:
        lines = fdinfo.read_text().splitlines()
    except OSError:
        return False
    for line in lines:
        key, separator, value = line.partition(":")
        if separator and key == "flags":
            try:
                flags = int(value.strip(), 8)
            except ValueError:
                return False
            return (flags & os.O_ACCMODE) in (os.O_WRONLY, os.O_RDWR)
    return False


def _task_output_path(target: str, task_root: pathlib.Path) -> bool:
    """Accept only ``<project>/<session>/tasks/<bounded-id>.output`` paths."""
    try:
        relative = pathlib.Path(target).relative_to(task_root)
    except (TypeError, ValueError):
        return False
    parts = relative.parts
    return (
        len(parts) == 4
        and parts[-2] == "tasks"
        and bool(_TASK_OUTPUT_NAME.match(parts[-1]))
    )


def live_task_output_cwds(
    candidate_cwds: Set[str],
    proc_root: pathlib.Path = pathlib.Path("/proc"),
    task_root: Optional[pathlib.Path] = None,
    uid: Optional[int] = None,
) -> Set[str]:
    """Project cwds with a live writer to a Claude background-task output.

    Claude detaches background shells/monitors into a user service, so they no
    longer descend from the pane process. Their stdout/stderr still point at a
    session-scoped ``tasks/*.output`` file. Scan only same-user processes whose
    cwd exactly matches an unambiguous live Claude pane, then inspect bounded fd
    symlink targets and open-mode flags. File contents and process argv/environ
    are never opened.
    """
    candidates = {str(value) for value in candidate_cwds if str(value)}
    if not candidates:
        return set()
    uid = os.getuid() if uid is None else int(uid)
    task_root = task_root or pathlib.Path("/tmp/claude-%d" % uid)
    live = set()  # type: Set[str]
    try:
        processes = list(proc_root.iterdir())[:PROCESS_SCAN_LIMIT]
    except OSError:
        return live

    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != uid:
                continue
            cwd = os.readlink(str(process / "cwd"))
        except OSError:
            continue
        if cwd not in candidates or cwd in live:
            continue
        fd_dir = process / "fd"
        try:
            descriptors = list(fd_dir.iterdir())[:FD_LIMIT]
        except OSError:
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(str(descriptor))
            except OSError:
                continue
            if (not _task_output_path(target, task_root)
                    or not _writable_fd(process / "fdinfo" / descriptor.name)):
                continue
            live.add(cwd)
            if live == candidates:
                return live
            break
    return live
