"""Where cc_navigator keeps its per-session state files."""
from __future__ import annotations

import os
import pathlib


def state_dir() -> pathlib.Path:
    """Directory holding one JSON file per live Claude session.

    XDG_RUNTIME_DIR is tmpfs and is wiped at logout, which is exactly the
    lifetime we want. Fall back to a uid-scoped /tmp path when it is unset.
    """
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return pathlib.Path(base) / "cc-navigator"
    return pathlib.Path("/tmp/cc-navigator-%d" % os.getuid())


def ensure_state_dir() -> pathlib.Path:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(str(directory), 0o700)
    return directory
