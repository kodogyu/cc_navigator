"""Process identity and liveness from /proc -- the non-tmux (VSCode) address path.

tmux hands cc_navigator two things at once: a session's ADDRESS (socket+pane)
and its LIVENESS (the pane shows up in list-panes). A VSCode extension-hosted
Claude session has neither -- it is a headless `claude` subprocess the editor
drives over a stream-json stdio socket, with no pty and no tmux. Its process may
outlive a closed editor tab, so liveness needs both the owning process identity
and (when the kernel exposes it) a connected stdio peer. This module is the one
place that inspects those process details, and every reader is injectable so the
decision logic is tested from fixtures rather than a live process table.
"""
from __future__ import annotations

import errno
import os
import re
import socket
import struct
from typing import Callable, Optional, Set, Tuple

# A reader maps a pid to the raw bytes of its /proc/<pid>/stat, and raises
# OSError when the pid is gone (exactly what open() does on a missing path).
StatReader = Callable[[int], bytes]

_PTS_PATH = re.compile(r"^/dev/pts/[0-9]+$")

# Linux SOCK_DIAG constants and fixed-size structures. cc_navigator is a Linux
# desktop application; querying this kernel API avoids running/parsing `ss` once
# per second and reveals only socket identity/connectivity -- never stream data.
_NETLINK_SOCK_DIAG = 4
_SOCK_DIAG_BY_FAMILY = 20
_NLM_F_REQUEST = 1
_NLMSG_ERROR = 2
_NLMSG_DONE = 3
_UDIAG_SHOW_PEER = 4
_UNIX_DIAG_PEER = 2
_NLMSG_HEADER = struct.Struct("=IHHII")
_UNIX_DIAG_REQUEST = struct.Struct("=BBHIIIII")
_UNIX_DIAG_MESSAGE = struct.Struct("=BBBBIII")
_RTATTR = struct.Struct("=HH")


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


def parse_start_time(data: bytes) -> Optional[int]:
    """Kernel start-time ticks (field 22), used to distinguish PID reuse."""
    right = data.rfind(b")")
    if right < 0:
        return None
    # After comm, index 0 is field 3 (state), so field 22 is index 19.
    fields = data[right + 1:].split()
    if len(fields) <= 19:
        return None
    try:
        return int(fields[19])
    except ValueError:
        return None


def process_start_time(pid: int, read_stat: StatReader = _default_read_stat) -> int:
    try:
        data = read_stat(int(pid))
    except (OSError, TypeError, ValueError):
        return 0
    return parse_start_time(data) or 0


def process_tty(
    pid: int, readlink: Callable[[str], str] = os.readlink,
) -> str:
    """A Claude process's bounded pseudo-terminal path, or empty if unknown.

    Only the fd-0 symlink target is inspected; terminal contents, process
    arguments, and environment are never read. Restricting the accepted shape
    prevents an unexpected descriptor target from escaping into tmux queries or
    state.
    """
    try:
        pid = int(pid)
        target = readlink("/proc/%d/fd/0" % pid)
    except (OSError, TypeError, ValueError):
        return ""
    return target if _PTS_PATH.match(target) else ""


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


def pid_is_claude(
    pid: int,
    expected_start_time: int = 0,
    read_stat: StatReader = _default_read_stat,
) -> bool:
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
    if parsed is None or parsed[0] != "claude":
        return False
    # Old state files have no start time and retain the previous name-only
    # behaviour until the next hook refreshes them. New records require both.
    if expected_start_time:
        return parse_start_time(data) == expected_start_time
    return True


def _unix_peer_inode(
    inode: int, socket_factory: Callable[..., object] = socket.socket,
) -> Optional[int]:
    """Connected peer inode for a Unix socket, 0 when disconnected, or None.

    ``None`` deliberately means "could not observe" (unsupported kernel,
    permission failure, malformed response, timeout). Callers must fall back to
    PID liveness in that case rather than hiding a possibly live session.
    """
    try:
        inode = int(inode)
    except (TypeError, ValueError):
        return None
    if inode <= 0:
        return None
    try:
        diag = socket_factory(socket.AF_NETLINK, socket.SOCK_RAW, _NETLINK_SOCK_DIAG)
    except OSError:
        return None
    sequence = 1
    try:
        # A local kernel query normally answers immediately. Keep failure
        # bounded so several stale VS Code records cannot stall the poll tick.
        diag.settimeout(0.05)
        request = _UNIX_DIAG_REQUEST.pack(
            socket.AF_UNIX, 0, 0, 0xFFFFFFFF, inode, _UDIAG_SHOW_PEER,
            0xFFFFFFFF, 0xFFFFFFFF)
        diag.send(_NLMSG_HEADER.pack(
            _NLMSG_HEADER.size + len(request), _SOCK_DIAG_BY_FAMILY,
            _NLM_F_REQUEST, sequence, 0) + request)
        while True:
            data = diag.recv(8192)
            offset = 0
            while offset + _NLMSG_HEADER.size <= len(data):
                length, kind, _flags, got_sequence, _pid = (
                    _NLMSG_HEADER.unpack_from(data, offset))
                if length < _NLMSG_HEADER.size or offset + length > len(data):
                    return None
                body = data[offset + _NLMSG_HEADER.size:offset + length]
                if got_sequence == sequence and kind == _NLMSG_ERROR:
                    return None
                if got_sequence == sequence and kind == _NLMSG_DONE:
                    return 0
                if (got_sequence == sequence
                        and kind == _SOCK_DIAG_BY_FAMILY
                        and len(body) >= _UNIX_DIAG_MESSAGE.size):
                    message = _UNIX_DIAG_MESSAGE.unpack_from(body)
                    if message[4] == inode:
                        attr_offset = _UNIX_DIAG_MESSAGE.size
                        while attr_offset + _RTATTR.size <= len(body):
                            attr_len, attr_type = _RTATTR.unpack_from(
                                body, attr_offset)
                            if (attr_len < _RTATTR.size
                                    or attr_offset + attr_len > len(body)):
                                return None
                            if (attr_type == _UNIX_DIAG_PEER
                                    and attr_len >= _RTATTR.size + 4):
                                return struct.unpack_from(
                                    "=I", body, attr_offset + _RTATTR.size)[0]
                            attr_offset += (attr_len + 3) & ~3
                        return 0
                offset += (length + 3) & ~3
    except (OSError, socket.timeout):
        return None
    finally:
        try:
            diag.close()
        except OSError:
            pass


def vscode_transport_connected(
    pid: int,
    readlink: Callable[[str], str] = os.readlink,
    peer_inode: Callable[[int], Optional[int]] = _unix_peer_inode,
) -> Optional[bool]:
    """Whether a VS Code-hosted Claude still has its stdio peer connected.

    Claude's VS Code process can remain alive after its webview tab closes. The
    stream-json transport is fd 0, normally one side of an anonymous Unix
    socketpair. A peer inode of zero means the editor side closed and the row can
    disappear immediately. A non-socket fd or any observation failure returns
    None, preserving the previous process-only behaviour for other extension
    versions and restricted systems.
    """
    try:
        pid = int(pid)
        target = readlink("/proc/%d/fd/0" % pid)
    except OSError as exc:
        # pid_is_claude already succeeded immediately before this probe. If the
        # process or fd vanished in that tiny race, it is definitely not a live
        # editor transport. Permission/feature failures remain unknown so they
        # take the conservative PID-only fallback.
        if exc.errno in (errno.ENOENT, errno.ESRCH):
            return False
        return None
    except (TypeError, ValueError):
        return None
    prefix = "socket:["
    if not target.startswith(prefix) or not target.endswith("]"):
        return None
    try:
        inode = int(target[len(prefix):-1])
        peer = peer_inode(inode)
    except (TypeError, ValueError, OSError):
        return None
    return None if peer is None else peer > 0


def live_claude_pids(
    pids,
    read_stat: StatReader = _default_read_stat,
    transport_connected: Callable[[int], Optional[bool]] = (
        vscode_transport_connected),
) -> Set[object]:
    """Return the live subset of pid keys.

    New records use ``(pid, start_time)`` keys, which distinguish a recycled PID
    even when the new process is also named ``claude``. A definitely disconnected
    VS Code stdio peer removes a still-running backend whose editor tab closed;
    an unobservable transport falls back to process identity. Legacy integer
    keys keep the old name-only identity check until their next hook update.
    """
    live = set()  # type: Set[object]
    for key in pids:
        if isinstance(key, tuple) and len(key) == 2:
            pid, started = key
            is_claude = pid_is_claude(
                pid, expected_start_time=started, read_stat=read_stat)
        else:
            pid = key
            is_claude = pid_is_claude(pid, read_stat=read_stat)
        if not is_claude:
            continue
        try:
            connected = transport_connected(int(pid))
        except Exception:  # a liveness probe must never stop the poll thread
            connected = None
        if connected is not False:
            live.add(key)
    return live
