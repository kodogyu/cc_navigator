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
from typing import List, Optional, Sequence

from . import hookstate

PROCESS_LIMIT = 256


@dataclass(frozen=True)
class CodexProcess:
    pid: int
    started_at: int
    cwd: str


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
    try:
        text = (proc_root / str(pid) / "task" / str(pid) / "children").read_text()
    except (OSError, ValueError):
        return []
    children = []
    for value in text.split():
        try:
            children.append(int(value))
        except ValueError:
            continue
    return children


def _argv(proc_root: pathlib.Path, pid: int) -> List[str]:
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


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
        "state": hookstate.WORKING,
        "reason": "",
        "message": "",
        "updated_at": process.started_at,
        "last_prompt": "",
        "subagent_ids": [],
    }
