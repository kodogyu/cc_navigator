"""System wiring: app-launcher, autostart, and agent hook wiring.

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

# The app ships its own icon; point the launcher at it by absolute path so it
# shows the real icon in the app grid / dock instead of a generic terminal glyph,
# with no icon-theme cache step to go stale. Resolved from this file's location
# (src/ccnav/wiring.py -> repo root), so it stays correct wherever the repo lives.
_ICON_PATH = str(pathlib.Path(__file__).resolve().parents[2] / "icons" / "window_icon.png")

# StartupWMClass binds the running window to THIS launcher, so a pinned dock icon
# is the one that lights up (or relaunches) rather than a stray generic entry.
# The app sets its WM_CLASS to APP_ID (see app.main), so they match exactly.
_DESKTOP = """[Desktop Entry]
Type=Application
Name=cc-navigator
Comment=Navigate Claude Code and Codex sessions
Exec=%(exec)s
Icon=%(icon)s
StartupWMClass=%(wmclass)s
Categories=Utility;Development;
Terminal=false
"""


def _default_apps_dir() -> pathlib.Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return pathlib.Path(base) / "applications"


def _atomic_write_bytes(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _atomic_write(path: pathlib.Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def launcher_path(apps_dir: Optional[pathlib.Path] = None) -> pathlib.Path:
    return (apps_dir or _default_apps_dir()) / (APP_ID + ".desktop")


def launcher_installed(apps_dir: Optional[pathlib.Path] = None) -> bool:
    return launcher_path(apps_dir).exists()


def _desktop_text(exec_path: str) -> str:
    """The filled-in .desktop body. One place so the launcher and the autostart
    entry (which appends its own key) can never drift on icon/WM_CLASS."""
    return _DESKTOP % {"exec": exec_path, "icon": _ICON_PATH, "wmclass": APP_ID}


def install_launcher(exec_path: str, apps_dir: Optional[pathlib.Path] = None) -> None:
    _atomic_write(launcher_path(apps_dir), _desktop_text(exec_path))


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
    except (OSError, ValueError):
        # OSError: missing/unreadable. ValueError: read_text() raises
        # UnicodeDecodeError (a ValueError) on a non-UTF-8 .desktop -- a corrupt
        # file must read as "not enabled", not raise into the settings dialog.
        return False
    # Present counts as enabled unless the GNOME key explicitly disables it.
    return "X-GNOME-Autostart-enabled=false" not in text


def set_autostart(
    enabled: bool, exec_path: str, autostart_dir: Optional[pathlib.Path] = None
) -> None:
    flag = "true" if enabled else "false"
    text = _desktop_text(exec_path) + "X-GNOME-Autostart-enabled=%s\n" % flag
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
    # A finished tool means Claude resumed working -- this un-sticks the red
    # "input" dot after the user answers a prompt (see hookstate.classify).
    "PostToolUse": "",
    # SubagentStart/Stop drive the second, overlapping status icon: they bracket a
    # subagent's lifetime so the panel can show a helper running behind the main
    # agent's state (see hook.build_record's subagent_ids tracking).
    "SubagentStart": "",
    "SubagentStop": "",
}

# Codex supports the same command-hook envelope but a slightly different event
# surface. PermissionRequest is deliberately omitted: it fires before Codex
# decides between automatic review and a real user prompt, so it is not a valid
# user-input state signal. Codex also has no Notification/SessionEnd.
CODEX_RECOMMENDED_HOOKS = {
    "SessionStart": "startup|resume|clear|compact",
    "UserPromptSubmit": "",
    "Stop": "",
    "PreToolUse": "request_user_input|AskUserQuestion|ExitPlanMode",
    "PostToolUse": "",
    "SubagentStart": "",
    "SubagentStop": "",
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


def hooks_installed(
    hook_command: str,
    settings_path: pathlib.Path,
    recommended_hooks=None,
) -> bool:
    recommended_hooks = (
        RECOMMENDED_HOOKS if recommended_hooks is None else recommended_hooks
    )
    hooks = _load_settings(settings_path).get("hooks")
    if not isinstance(hooks, dict):
        return False
    for event in recommended_hooks:
        groups = hooks.get(event)
        if not isinstance(groups, list) or not any(
            _group_has(hook_command, g) for g in groups
        ):
            return False
    return True


def _unique_backup_path(settings_path: pathlib.Path) -> pathlib.Path:
    """A settings.json.bak-<epoch> name that does not already exist. Second
    granularity collides when two writes land in the same wall-clock second
    (an install immediately followed by a remove), and _atomic_write would then
    os.replace the pristine pre-install backup with post-install content -- so
    fall through to a -1, -2, ... suffix until the name is free."""
    base = settings_path.name + ".bak-%d" % int(time.time())
    candidate = settings_path.with_name(base)
    suffix = 0
    while candidate.exists():
        suffix += 1
        candidate = settings_path.with_name("%s-%d" % (base, suffix))
    return candidate


def _write_settings(settings_path: pathlib.Path, data: dict) -> None:
    if settings_path.exists():
        # Copy the ORIGINAL as raw bytes: read_text() would raise on a non-UTF-8
        # file (UnicodeDecodeError, a ValueError) that _load_settings already
        # tolerated, turning a graceful degrade into a crash. If we cannot read
        # it at all (e.g. mode 000), do NOT overwrite -- replacing a file we
        # could not back up would lose it silently.
        try:
            original = settings_path.read_bytes()
        except OSError:
            return
        _atomic_write_bytes(_unique_backup_path(settings_path), original)
    _atomic_write(settings_path, json.dumps(data, indent=2))


def install_hooks(
    hook_command: str,
    settings_path: pathlib.Path,
    recommended_hooks=None,
) -> None:
    recommended_hooks = (
        RECOMMENDED_HOOKS if recommended_hooks is None else recommended_hooks
    )
    data = _load_settings(settings_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    changed = False
    for event, matcher in recommended_hooks.items():
        groups = hooks.get(event)
        if not isinstance(groups, list):
            groups = []
        ours = any(_group_has(hook_command, g) for g in groups)
        if not ours:
            groups.append(_our_entry(hook_command, matcher))
            changed = True
        hooks[event] = groups
    data["hooks"] = hooks
    # Skip the write (and its backup) when every event already holds our entry:
    # a settings dialog that re-installs an already-installed hook set must not
    # rewrite settings.json or leave a fresh settings.json.bak-* behind.
    if changed:
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
        removed_from_event = False
        for g in groups:
            if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
                new_groups.append(g)
                continue
            kept = [h for h in g["hooks"]
                    if not (isinstance(h, dict) and h.get("command") == hook_command)]
            removed_here = len(kept) != len(g["hooks"])
            if removed_here:
                changed = True
                removed_from_event = True
            # Drop a group only when removing OUR command is what emptied it.
            # A group that was ALREADY empty (foreign structure we touched
            # nothing in) must survive, or a plain remove silently deletes it.
            if kept or not removed_here:
                new = dict(g)
                new["hooks"] = kept
                new_groups.append(new)
        if new_groups:
            hooks[event] = new_groups
        elif removed_from_event:
            # Same rule at the event level: prune an event only when WE emptied
            # it. A foreign event that was already [] (or that we never touched)
            # must be left exactly as it was.
            del hooks[event]
    if not hooks:
        data.pop("hooks", None)
    if changed:
        _write_settings(settings_path, data)
    return changed
