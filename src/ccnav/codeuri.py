"""Focus one Claude session's TAB inside VSCode, by session id.

The Claude Code extension registers a URI route -- vscode://Anthropic.claude-code/
open?session=<id> -- whose handler runs the extension's own
`claude-vscode.primaryEditor.open` command for that session id. That reveals (and
raises the window of) exactly that session's editor tab -- finer than a window
jump, which cannot tell two sessions sharing one workspace window apart. The URL
is handed to the already-running editor with `code --open-url`.
"""
from __future__ import annotations

from typing import List
from urllib.parse import quote

from .proc import Runner, run_command

# The extension id is the URI authority. VSCode matches it case-insensitively, so
# the exact casing from the extension's package.json is used but not depended on.
VSCODE_EXTENSION_ID = "Anthropic.claude-code"


def open_session_uri(session_id: str) -> str:
    """vscode://Anthropic.claude-code/open?session=<id>. The id is percent-encoded
    even though a session UUID is already URL-safe -- it costs nothing and keeps a
    surprising id from ever breaking the query."""
    return "vscode://%s/open?session=%s" % (VSCODE_EXTENSION_ID, quote(session_id, safe=""))


def open_url_argv(session_id: str) -> List[str]:
    """`code --open-url <uri>` delivers the URL to the running editor instance
    (not a new one), which routes it to the extension's registered handler."""
    return ["code", "--open-url", open_session_uri(session_id)]


def open_session(session_id: str, run: Runner = run_command) -> bool:
    """Ask VSCode to reveal `session_id`'s tab. Returns whether the URL was
    delivered (exit 0) -- NOT whether focus moved; the caller verifies focus
    through a separate channel (xprop), the same distrust of a self-report the
    window-jump path already applies. A missing `code` binary (FileNotFoundError,
    an OSError) degrades to False so the caller can fall back to a window jump."""
    if not session_id:
        return False
    try:
        code, _ = run(open_url_argv(session_id))
    except OSError:
        return False
    return code == 0
