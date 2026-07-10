"""System wiring: app-launcher, autostart, and Claude Code hook wiring.

Each action reads and writes external state a settings toggle drives. Every
path is injectable so the logic is tested without touching the real HOME.
Following well-known practice: freedesktop Desktop Entry / Autostart specs as
Syncthing/VS Code implement them (direct per-user file writes), and an
identity-based structural JSON merge for settings.json (npm pkg / VS Code
node-jsonc-parser style) -- see the spec's section 5.6.
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
import time
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


def _default_autostart_dir() -> pathlib.Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    return pathlib.Path(base) / "autostart"


def autostart_path(autostart_dir: Optional[pathlib.Path] = None) -> pathlib.Path:
    return (autostart_dir or _default_autostart_dir()) / (APP_ID + ".desktop")


def autostart_enabled(autostart_dir: Optional[pathlib.Path] = None) -> bool:
    path = autostart_path(autostart_dir)
    try:
        text = path.read_text()
    except OSError:
        return False
    # Present counts as enabled unless the GNOME key explicitly disables it.
    return "X-GNOME-Autostart-enabled=false" not in text


def set_autostart(
    enabled: bool, exec_path: str, autostart_dir: Optional[pathlib.Path] = None
) -> None:
    flag = "true" if enabled else "false"
    text = _DESKTOP % {"exec": exec_path} + "X-GNOME-Autostart-enabled=%s\n" % flag
    _atomic_write(autostart_path(autostart_dir), text)


# The canonical hook set: event -> matcher. "" is an empty (match-all) matcher.
# One source of truth so doctor's check and this installer cannot drift.
RECOMMENDED_HOOKS = {
    "SessionStart": "",
    "UserPromptSubmit": "",
    "Notification": "",
    "Stop": "",
    "SessionEnd": "",
    "PreToolUse": "AskUserQuestion|ExitPlanMode",
}


def _load_settings(settings_path: pathlib.Path) -> dict:
    try:
        data = json.loads(settings_path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _our_entry(hook_command: str, matcher: str) -> dict:
    return {"matcher": matcher, "hooks": [{"type": "command", "command": hook_command}]}


def _group_has(hook_command: str, group) -> bool:
    """True iff `group` is a well-formed matcher group containing our command."""
    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
        return False
    return any(
        isinstance(h, dict) and h.get("command") == hook_command
        for h in group["hooks"]
    )


def hooks_installed(hook_command: str, settings_path: pathlib.Path) -> bool:
    hooks = _load_settings(settings_path).get("hooks")
    if not isinstance(hooks, dict):
        return False
    for event in RECOMMENDED_HOOKS:
        groups = hooks.get(event)
        if not isinstance(groups, list) or not any(
            _group_has(hook_command, g) for g in groups
        ):
            return False
    return True


def _write_settings(settings_path: pathlib.Path, data: dict) -> None:
    if settings_path.exists():
        backup = settings_path.with_name(
            settings_path.name + ".bak-%d" % int(time.time()))
        _atomic_write(backup, settings_path.read_text())
    _atomic_write(settings_path, json.dumps(data, indent=2))


def install_hooks(hook_command: str, settings_path: pathlib.Path) -> None:
    data = _load_settings(settings_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    for event, matcher in RECOMMENDED_HOOKS.items():
        groups = hooks.get(event)
        if not isinstance(groups, list):
            groups = []
        ours = any(_group_has(hook_command, g) for g in groups)
        if not ours:
            groups.append(_our_entry(hook_command, matcher))
        hooks[event] = groups
    data["hooks"] = hooks
    _write_settings(settings_path, data)


def remove_hooks(hook_command: str, settings_path: pathlib.Path) -> bool:
    data = _load_settings(settings_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    changed = False
    for event in list(hooks):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        new_groups = []
        for g in groups:
            if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
                new_groups.append(g)
                continue
            kept = [h for h in g["hooks"]
                    if not (isinstance(h, dict) and h.get("command") == hook_command)]
            if len(kept) != len(g["hooks"]):
                changed = True
            if kept:
                new = dict(g)
                new["hooks"] = kept
                new_groups.append(new)
        if new_groups:
            hooks[event] = new_groups
        else:
            del hooks[event]
    if not hooks:
        data.pop("hooks", None)
    if changed:
        _write_settings(settings_path, data)
    return changed
