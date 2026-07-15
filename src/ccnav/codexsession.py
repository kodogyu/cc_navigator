"""Discover a Codex TUI before its first lifecycle hook fires.

Codex creates the interactive TUI before it persists the first turn.  In that
pre-prompt window current releases do not emit SessionStart, so hook state alone
cannot make the pane visible.  tmux gives us the pane's root PID; walking that
small Linux process tree lets us identify the actual OpenAI Codex launcher or
native binary without mistaking every Node process for Codex.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from . import hookstate

PROCESS_LIMIT = 256


@dataclass(frozen=True)
class CodexProcess:
    pid: int
    started_at: int
    cwd: str


@dataclass(frozen=True)
class _ProcessStat:
    ppid: int
    session: int
    started_at: int


def is_codex_argv(argv: Sequence[str]) -> bool:
    """Recognise the native binary and the official npm launcher's Node argv."""
    if not argv:
        return False
    if pathlib.Path(argv[0]).name == "codex":
        return True
    if pathlib.Path(argv[0]).name not in ("node", "nodejs"):
        return False
    for argument in argv[1:3]:
        normalised = argument.replace("\\", "/")
        if normalised.endswith("/@openai/codex/bin/codex.js"):
            return True
    return False


def _children(proc_root: pathlib.Path, pid: int) -> List[int]:
    """Children created by any thread in a process.

    ``task/<pid>/children`` is sufficient for a single-threaded tmux shell, but
    Codex launches terminals from worker threads. Linux exposes each thread's
    children separately, so reading only the main thread silently misses the
    very processes this module is looking for.
    """
    children = set()  # type: Set[int]
    task_root = proc_root / str(pid) / "task"
    try:
        paths = list(task_root.glob("*/children"))
    except OSError:
        return []
    for path in paths[:PROCESS_LIMIT]:
        try:
            text = path.read_text()
        except (OSError, ValueError):
            continue
        for value in text.split():
            try:
                children.add(int(value))
            except ValueError:
                continue
    return sorted(children)


def _argv(proc_root: pathlib.Path, pid: int) -> List[str]:
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _stat(proc_root: pathlib.Path, pid: int) -> Optional[_ProcessStat]:
    """Read the fields needed to identify one process without trusting its PID.

    Linux's ``comm`` field may contain spaces or parentheses, so the numeric
    fields must be split after the final ``)`` rather than splitting the whole
    line.  ``started_at`` is the kernel start-time tick (field 22), which makes
    a stored identity safe against PID reuse.
    """
    try:
        data = (proc_root / str(pid) / "stat").read_bytes()
    except OSError:
        return None
    right = data.rfind(b")")
    if right < 0:
        return None
    fields = data[right + 1:].split()
    if len(fields) <= 19:
        return None
    try:
        return _ProcessStat(
            ppid=int(fields[1]), session=int(fields[3]),
            started_at=int(fields[19]),
        )
    except ValueError:
        return None


def _identity(pid: int, started_at: int) -> str:
    return "%d:%d" % (pid, started_at)


def _parse_identity(value: object) -> Optional[Tuple[int, int]]:
    if not isinstance(value, str):
        return None
    parts = value.split(":", 1)
    if len(parts) != 2:
        return None
    try:
        pid, started_at = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return (pid, started_at) if pid > 1 and started_at > 0 else None


def _codex_ancestor_and_child(
    start_pid: int, proc_root: pathlib.Path, max_depth: int = 32,
) -> Tuple[int, int]:
    """Nearest Codex ancestor and the direct child leading to ``start_pid``.

    A hook normally runs through one or more shell processes.  The second value
    lets background_process_ids exclude that hook branch from the Codex
    process's other children.
    """
    pid = int(start_pid)
    child = 0
    seen = set()  # type: Set[int]
    for _ in range(max_depth):
        if pid <= 1 or pid in seen:
            return 0, 0
        seen.add(pid)
        if is_codex_argv(_argv(proc_root, pid)):
            return pid, child
        stat = _stat(proc_root, pid)
        if stat is None:
            return 0, 0
        child, pid = pid, stat.ppid
    return 0, 0


def background_process_ids(
    start_pid: int, proc_root: pathlib.Path = pathlib.Path("/proc")
) -> List[str]:
    """Live background terminal identities owned by this hook's Codex session.

    Codex starts command terminals as direct children of its native process and
    puts each in a new process session.  At PostToolUse time a foreground
    command has exited; a command child that is still alive is therefore a
    background terminal.  The hook's own child branch and Codex sidecars are
    excluded.  Only ``pid:start_ticks`` is returned: commands, arguments and
    output never enter cc_navigator's state files.
    """
    owner, hook_child = _codex_ancestor_and_child(start_pid, proc_root)
    if not owner:
        return []
    identities = []
    for pid in _children(proc_root, owner):
        if pid == hook_child:
            continue
        argv = _argv(proc_root, pid)
        if not argv or pathlib.Path(argv[0]).name == "codex-code-mode":
            continue
        stat = _stat(proc_root, pid)
        # Command terminals are session leaders. Persistent Codex helpers/MCP
        # transports inherit the owner's session and must not light the icon.
        if stat is None or stat.session != pid:
            continue
        identities.append(_identity(pid, stat.started_at))
    return sorted(set(identities))


def live_process_ids(
    identities: Iterable[object], proc_root: pathlib.Path = pathlib.Path("/proc")
) -> Set[str]:
    """The subset that still names the same kernel process, not a reused PID."""
    live = set()  # type: Set[str]
    for value in identities:
        parsed = _parse_identity(value)
        if parsed is None:
            continue
        pid, expected_start = parsed
        stat = _stat(proc_root, pid)
        if stat is not None and stat.started_at == expected_start:
            live.add(_identity(pid, expected_start))
    return live


def _process(proc_root: pathlib.Path, pid: int) -> Optional[CodexProcess]:
    directory = proc_root / str(pid)
    try:
        started_at = int(directory.stat().st_mtime)
        cwd = os.readlink(str(directory / "cwd"))
    except OSError:
        return None
    return CodexProcess(pid=pid, started_at=started_at, cwd=cwd)


def find_codex_process(
    root_pid: int, proc_root: pathlib.Path = pathlib.Path("/proc")
) -> Optional[CodexProcess]:
    """Return the newest Codex process below a tmux pane root PID.

    Linux exposes direct children in ``task/<pid>/children``, so this avoids a
    full /proc scan every poll.  Every read is best-effort: a process exiting in
    the middle of traversal simply means no provisional row on that tick.
    """
    pending = [root_pid]
    seen = set()
    matches = []
    while pending and len(seen) < PROCESS_LIMIT:
        pid = pending.pop()
        if pid in seen or pid <= 0:
            continue
        seen.add(pid)
        if is_codex_argv(_argv(proc_root, pid)):
            process = _process(proc_root, pid)
            if process is not None:
                matches.append(process)
        pending.extend(_children(proc_root, pid))
    return max(matches, key=lambda item: (item.started_at, item.pid)) if matches else None


def provisional_session_id(socket: str, pane: str) -> str:
    digest = hashlib.sha256((socket + "\0" + pane).encode("utf-8")).hexdigest()[:24]
    return "codex-pane-" + digest


def provisional_record(
    socket: str, pane: str, process: CodexProcess
) -> dict:
    """Build a non-persistent row candidate for a pre-prompt Codex pane.

    The process start time is the ordering key.  A stale state record from an
    earlier command in a reused pane loses to it, while the first real hook from
    this process has an equal-or-newer wall-clock timestamp and takes over.
    """
    return {
        "session_id": provisional_session_id(socket, pane),
        "provider": "codex",
        "provisional": True,
        "cwd": process.cwd,
        "tmux_socket": socket,
        "tmux_pane": pane,
        # A live TUI before its first prompt is ready for input, not executing a
        # turn.  Treating mere process liveness as WORKING produced a permanent
        # calm-blue false positive for untouched Codex panes.
        "state": hookstate.WAITING,
        "reason": hookstate.STOP_IDLE,
        "message": "",
        "updated_at": process.started_at,
        "last_prompt": "",
        "subagent_ids": [],
        "background_process_ids": [],
    }
