"""Where cc_navigator keeps its per-session state files."""
from __future__ import annotations

import errno
import os
import pathlib
import stat
from typing import List, Mapping, Optional


def state_dir() -> pathlib.Path:
    """Directory holding one JSON file per live agent session.

    XDG_RUNTIME_DIR is tmpfs and is wiped at logout, which is exactly the
    lifetime we want. Fall back to a uid-scoped /tmp path when it is unset.
    """
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return pathlib.Path(base) / "cc-navigator"
    return pathlib.Path("/tmp/cc-navigator-%d" % os.getuid())


def tmux_sockets(
    env: Optional[Mapping[str, str]] = None, uid: Optional[int] = None,
) -> List[str]:
    """Return same-user tmux sockets that may contain a pre-hook Codex pane.

    State records still supply arbitrary ``tmux -S`` sockets.  This discovery
    covers the standard tmux socket directory (including ``-L`` names), plus a
    socket inherited through ``$TMUX`` when the navigator itself was launched
    inside tmux.  Ownership and file type are checked before anything is handed
    to the tmux client.
    """
    env = os.environ if env is None else env
    uid = os.getuid() if uid is None else uid
    candidates = set()
    inherited = (env.get("TMUX") or "").split(",", 1)[0]
    if inherited:
        candidates.add(pathlib.Path(inherited))

    base = pathlib.Path(env.get("TMUX_TMPDIR") or "/tmp")
    directory = base / ("tmux-%d" % uid)
    try:
        directory_stat = directory.lstat()
        if (not stat.S_ISDIR(directory_stat.st_mode)
                or directory_stat.st_uid != uid
                or directory.is_symlink()):
            return []
        candidates.update(directory.iterdir())
    except OSError:
        pass

    sockets = []
    for candidate in candidates:
        try:
            candidate_stat = candidate.lstat()
        except OSError:
            continue
        if stat.S_ISSOCK(candidate_stat.st_mode) and candidate_stat.st_uid == uid:
            sockets.append(str(candidate))
    return sorted(set(sockets))


def ensure_state_dir() -> pathlib.Path:
    """Create the state directory (mode 0700) and refuse an unsafe one.

    This directory names cc_navigator's files and prune deletes from it, so a
    symlink -- or another user's directory -- planted at our path would turn the
    chmod below into an arbitrary-directory chmod and prune into an
    arbitrary-`*.json` delete. That is only reachable on the /tmp fallback,
    whose parent is world-writable and whose name is predictable, but it is
    reachable, so we fail closed rather than operate on a directory we do not
    trust.

    Two defences, no TOCTOU window:
      * Create it ourselves with os.mkdir, which never follows a symlink at the
        final component and fails if the name already exists.
      * When it already exists, open it with O_NOFOLLOW | O_DIRECTORY -- which
        fails on a symlink or a non-directory -- and do every subsequent check
        and the chmod against that one fd, so the thing we verify is the thing
        we chmod.
    """
    directory = state_dir()
    directory.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.mkdir(str(directory), 0o700)
    except FileExistsError:
        pass  # verified below through a no-follow open before we touch it

    fd = os.open(str(directory), os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    try:
        if os.fstat(fd).st_uid != os.getuid():
            raise PermissionError(
                errno.EPERM,
                "refusing state dir owned by another user: %s" % directory,
            )
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)
    return directory
