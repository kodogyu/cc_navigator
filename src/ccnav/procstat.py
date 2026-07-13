"""Process identity and liveness from /proc -- the non-tmux (VSCode) address path.

tmux hands cc_navigator two things at once: a session's ADDRESS (socket+pane)
and its LIVENESS (the pane shows up in list-panes). A VSCode extension-hosted
Claude session has neither -- it is a headless `claude` subprocess the editor
drives over stream-json, with no pty and no tmux. So liveness is taken straight
from the kernel: the owning `claude` process's pid. This module is the one place
that parses /proc for it, and every reader is injectable so the logic is tested
from fixtures rather than a live process table.
"""
from __future__ import annotations

from typing import Callable, Optional, Set, Tuple

# A reader maps a pid to the raw bytes of its /proc/<pid>/stat, and raises
# OSError when the pid is gone (exactly what open() does on a missing path).
StatReader = Callable[[int], bytes]


def _default_read_stat(pid: int) -> bytes:
    with open("/proc/%d/stat" % pid, "rb") as handle:
        return handle.read()


def parse_stat(data: bytes) -> Optional[Tuple[str, int]]:
    """(comm, ppid) from a /proc/<pid>/stat blob, or None if unparseable.

    comm (field 2) is wrapped in parentheses and may itself contain spaces and
    parentheses -- a process is free to be named "(gnome-shell)" or literally
    ") ". Splitting on whitespace would then miscount every following field. So
    comm is taken as everything between the FIRST '(' and the LAST ')', and the
    numeric fields (state, ppid, ...) are split from the remainder: ppid is the
    second of those.
    """
    left = data.find(b"(")
    right = data.rfind(b")")
    if left < 0 or right < 0 or right < left:
        return None
    comm = data[left + 1:right].decode("utf-8", "replace")
    fields = data[right + 1:].split()
    if len(fields) < 2:
        return None
    try:
        ppid = int(fields[1])
    except ValueError:
        return None
    return comm, ppid


def find_claude_ancestor(
    start_pid: int, read_stat: StatReader = _default_read_stat, max_depth: int = 32
) -> int:
    """Walk parents from start_pid to the nearest process named 'claude'.

    Claude Code runs a hook command as a child process, possibly through one or
    more shells (`sh -c ...` then the hook's own `sh`). Walking up to the first
    'claude' ancestor finds the pid whose lifetime bounds the session however
    many shell layers sit between. Returns 0 when no such ancestor is found
    within max_depth or the chain breaks first -- a dead pid, reaching pid 1, or
    a cycle (guarded by `seen` so a corrupted stat can never loop forever).
    """
    pid = int(start_pid)
    seen = set()  # type: Set[int]
    for _ in range(max_depth):
        if pid <= 1 or pid in seen:
            return 0
        seen.add(pid)
        try:
            data = read_stat(pid)
        except OSError:
            return 0
        parsed = parse_stat(data)
        if parsed is None:
            return 0
        comm, ppid = parsed
        if comm == "claude":
            return pid
        pid = ppid
    return 0


def pid_is_claude(pid: int, read_stat: StatReader = _default_read_stat) -> bool:
    """True iff /proc/<pid> exists AND names a process still called 'claude'.

    The comm check is what guards PID reuse. A state file records claude_pid at
    hook time; by the next poll that number could name an unrelated process the
    kernel recycled the pid to. Liveness must mean "the SAME claude is still
    running", not merely "some process holds this number" -- otherwise a stale
    VSCode row would linger, and a jump could raise a window for a session that
    already ended.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 1:
        return False
    try:
        data = read_stat(pid)
    except OSError:
        return False
    parsed = parse_stat(data)
    return parsed is not None and parsed[0] == "claude"


def live_claude_pids(
    pids, read_stat: StatReader = _default_read_stat
) -> Set[int]:
    """The subset of `pids` that are still a running 'claude'. The poller calls
    this once per tick over the claude_pids its state files carry, then hands
    the result to both build_rows (which rows to show) and prune (which files to
    reap)."""
    return {p for p in pids if pid_is_claude(p, read_stat=read_stat)}
