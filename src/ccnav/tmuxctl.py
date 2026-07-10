"""Every tmux interaction: the queries that build the model, the actions the UI fires."""
from __future__ import annotations

from typing import Dict, List

from .proc import Runner, run_command


def parse_kv_lines(text: str) -> Dict[str, str]:
    """Split each line on its FIRST '='.

    A pane id ('%12') never contains '='. A pane title is whatever Claude Code
    wrote and may contain '=', '|', spaces and arbitrary UTF-8. Splitting once
    is what makes it safe to carry the title in the same record.
    """
    parsed = {}  # type: Dict[str, str]
    for line in text.splitlines():
        if not line:
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        parsed[key] = value
    return parsed


def list_argv(socket: str, fmt: str) -> List[str]:
    return ["tmux", "-S", socket, "list-panes", "-a", "-F", fmt]


def _query(socket: str, fmt: str, run: Runner) -> Dict[str, str]:
    code, out = run(list_argv(socket, fmt))
    if code != 0:
        return {}
    return parse_kv_lines(out)


def sessions_by_pane(socket: str, run: Runner = run_command) -> Dict[str, str]:
    return _query(socket, "#{pane_id}=#{session_name}", run)


def titles_by_pane(socket: str, run: Runner = run_command) -> Dict[str, str]:
    return _query(socket, "#{pane_id}=#{pane_title}", run)


def select_argvs(socket: str, pane: str) -> List[List[str]]:
    """switch-client is best effort: it fails when no client is attached."""
    return [
        ["tmux", "-S", socket, "switch-client", "-t", pane],
        ["tmux", "-S", socket, "select-window", "-t", pane],
        ["tmux", "-S", socket, "select-pane", "-t", pane],
    ]


def send_text_argvs(socket: str, pane: str, text: str) -> List[List[str]]:
    """`-l` and `--` are mandatory.

    Without -l, tmux reads words like 'Enter' and 'C-c' as key names.
    Without --, text beginning with '-' is parsed as an option.
    The text travels as one argv element and never touches a shell.
    """
    return [
        ["tmux", "-S", socket, "send-keys", "-t", pane, "-l", "--", text],
        ["tmux", "-S", socket, "send-keys", "-t", pane, "Enter"],
    ]


def select_pane(socket: str, pane: str, run: Runner = run_command) -> None:
    for argv in select_argvs(socket, pane):
        run(argv)


def send_text(socket: str, pane: str, text: str, run: Runner = run_command) -> None:
    for argv in send_text_argvs(socket, pane, text):
        run(argv)
