"""Read-only VS Code workbench state for extension-hosted Claude sessions.

The Claude extension intentionally keeps its sidebar backend (and its stdio
socket) alive while the sidebar and every Claude editor tab are closed. Process
liveness therefore cannot answer whether there is still a session UI to jump
to. VS Code already persists that distinction in the workspace state database.

Only exact workbench keys and boolean/string membership are queried. The editor
state value can contain users' open file names, so it is never returned to or
parsed by cc_navigator; SQLite evaluates the two narrowly scoped ``instr``
checks and returns one integer.
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
from typing import Iterable, Optional
from urllib.parse import unquote, urlparse


EDITOR_STATE_KEY = "memento/workbench.parts.editor"
SIDEBAR_MEMENTO_KEY = "memento/webviewView.claudeVSCodeSidebarSecondary"
SIDEBAR_STATE_KEY = "workbench.view.extension.claude-sidebar-secondary.state"
AUXILIARY_HIDDEN_KEY = "workbench.auxiliaryBar.hidden"
AUXILIARY_ACTIVE_KEY = "workbench.auxiliarybar.activepanelid"
CLAUDE_EXTENSION_ID = "Anthropic.claude-code"
CLAUDE_SIDEBAR_CONTAINER = "workbench.view.extension.claude-sidebar-secondary"


def _default_roots() -> Iterable[pathlib.Path]:
    config = pathlib.Path(
        os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
    # Official stable/insiders directories. Other builds or --user-data-dir
    # safely fall back to process/transport liveness instead of guessing.
    return (
        config / "Code" / "User" / "workspaceStorage",
        config / "Code - Insiders" / "User" / "workspaceStorage",
    )


def _local_folder(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    return os.path.normpath(unquote(parsed.path))


def _workspace_db(
    cwd: str, roots: Optional[Iterable[pathlib.Path]] = None,
) -> Optional[pathlib.Path]:
    target = os.path.normpath(str(cwd or ""))
    if not target or not os.path.isabs(target):
        return None
    for root in (_default_roots() if roots is None else roots):
        try:
            workspaces = sorted(pathlib.Path(root).iterdir())
        except OSError:
            continue
        for workspace in workspaces:
            metadata = workspace / "workspace.json"
            try:
                # workspace.json is a tiny VS Code-owned address record. Bound
                # the read so a corrupt/replaced file cannot consume memory.
                with metadata.open("r", encoding="utf-8") as handle:
                    raw = handle.read(16_385)
                if len(raw) > 16_384:
                    continue
                folder = _local_folder(json.loads(raw).get("folder"))
            except (OSError, ValueError, AttributeError):
                continue
            if folder == target:
                database = workspace / "state.vscdb"
                return database if database.is_file() else None
    return None


def _bool_text(value: object) -> Optional[bool]:
    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return None


def _sidebar_hidden(value: object) -> Optional[bool]:
    if not isinstance(value, str):
        return None
    try:
        state = json.loads(value)
        view = state.get("claudeVSCodeSidebarSecondary")
        hidden = view.get("isHidden") if isinstance(view, dict) else None
    except (ValueError, AttributeError):
        return None
    return hidden if isinstance(hidden, bool) else None


def session_visible(
    session_id: str,
    cwd: str,
    roots: Optional[Iterable[pathlib.Path]] = None,
) -> Optional[bool]:
    """True when a Claude editor/sidebar is open, False when closed, or None.

    None is the conservative fallback for an unknown workspace, unavailable or
    changed schema, lock/permission error, or unsupported VS Code build. Callers
    retain the existing process/transport result for None.
    """
    session_id = str(session_id or "")
    if not session_id:
        return None
    database = _workspace_db(cwd, roots=roots)
    if database is None:
        return None
    try:
        uri = database.as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=0.05)
        try:
            # Return only a boolean. The editor memento itself can contain file
            # names/titles and must never cross this SQLite privacy boundary.
            editor = connection.execute(
                "SELECT CASE WHEN instr(lower(value), ?) > 0 "
                "AND instr(value, ?) > 0 THEN 1 ELSE 0 END "
                "FROM ItemTable WHERE key = ?",
                (CLAUDE_EXTENSION_ID.lower(), session_id, EDITOR_STATE_KEY),
            ).fetchone()
            if editor is not None and editor[0] == 1:
                return True

            keys = (
                SIDEBAR_STATE_KEY, AUXILIARY_HIDDEN_KEY, AUXILIARY_ACTIVE_KEY,
            )
            placeholders = ",".join("?" for _key in keys)
            rows = connection.execute(
                "SELECT key, value FROM ItemTable WHERE key IN (%s)" % placeholders,
                keys,
            ).fetchall()
            sidebar = connection.execute(
                "SELECT CASE WHEN instr(value, ?) > 0 THEN 1 ELSE 0 END "
                "FROM ItemTable WHERE key = ?",
                (session_id, SIDEBAR_MEMENTO_KEY),
            ).fetchone()
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return None

    values = {str(key): value for key, value in rows}
    if editor is None and sidebar is None:
        return None
    sidebar_owns_session = sidebar is not None and sidebar[0] == 1
    if not sidebar_owns_session:
        # The workspace DB was observed and neither an editor nor the sidebar
        # owns this session. It has no UI even if an old backend remains alive.
        return False

    view_hidden = _sidebar_hidden(values.get(SIDEBAR_STATE_KEY))
    auxiliary_hidden = _bool_text(values.get(AUXILIARY_HIDDEN_KEY))
    active_container = values.get(AUXILIARY_ACTIVE_KEY)
    if (view_hidden is None or auxiliary_hidden is None
            or not isinstance(active_container, str)):
        return None
    return (
        not view_hidden
        and not auxiliary_hidden
        and active_container == CLAUDE_SIDEBAR_CONTAINER
    )
