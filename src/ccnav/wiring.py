"""System wiring: app-launcher, autostart, and Claude Code hook wiring.

Each action reads and writes external state a settings toggle drives. Every
path is injectable so the logic is tested without touching the real HOME.
Following well-known practice: freedesktop Desktop Entry / Autostart specs as
Syncthing/VS Code implement them (direct per-user file writes), and an
identity-based structural JSON merge for settings.json (npm pkg / VS Code
node-jsonc-parser style) -- see the spec's section 5.6.
"""
from __future__ import annotations

import os
import pathlib
import tempfile
from typing import Optional

APP_ID = "io.github.kodogyu.CcNavigator"

_DESKTOP = """[Desktop Entry]
Type=Application
Name=cc-navigator
Comment=Navigate Claude Code sessions
Exec=%(exec)s
Icon=utilities-terminal
Categories=Utility;Development;
Terminal=false
"""


def _default_apps_dir() -> pathlib.Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return pathlib.Path(base) / "applications"


def _atomic_write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def launcher_path(apps_dir: Optional[pathlib.Path] = None) -> pathlib.Path:
    return (apps_dir or _default_apps_dir()) / (APP_ID + ".desktop")


def launcher_installed(apps_dir: Optional[pathlib.Path] = None) -> bool:
    return launcher_path(apps_dir).exists()


def install_launcher(exec_path: str, apps_dir: Optional[pathlib.Path] = None) -> None:
    _atomic_write(launcher_path(apps_dir), _DESKTOP % {"exec": exec_path})


def remove_launcher(apps_dir: Optional[pathlib.Path] = None) -> bool:
    path = launcher_path(apps_dir)
    try:
        path.unlink()
        return True
    except OSError:
        return False
