"""Every tmux interaction: the queries that build the model, the actions the UI fires."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from .proc import Runner, run_command


@dataclass(frozen=True)
class PaneProcess:
    pid: int
    command: str


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


def _query_result(socket: str, fmt: str, run: Runner) -> Tuple[bool, Dict[str, str]]:
    """Report BOTH whether tmux answered and what it said.

    A nonzero exit -- a dead socket, or (124, "") from a timed-out but merely
    slow server -- yields (False, {}). An empty dict alone is ambiguous: it
    reads identically whether every session vanished or the query never ran.
    collect_rows needs the difference, because pruning state files on a query
    that only stuttered would delete live, waiting sessions that fire no more
    hooks and so never come back. See statestore.prune and F3 in task-13.
    """
    code, out = run(list_argv(socket, fmt))
    if code != 0:
        return False, {}
    return True, parse_kv_lines(out)


def sessions_by_pane_result(
    socket: str, run: Runner = run_command
) -> Tuple[bool, Dict[str, str]]:
    return _query_result(socket, "#{pane_id}=#{session_name}", run)


def sessions_by_pane(socket: str, run: Runner = run_command) -> Dict[str, str]:
    return sessions_by_pane_result(socket, run)[1]


def titles_by_pane(socket: str, run: Runner = run_command) -> Dict[str, str]:
    # Titles are cosmetic (build_rows falls back to the pane id), so a failed
    # titles query costs at most a blank title, never a prune decision. Only
    # the sessions query gates pruning, so only it needs the _result variant.
    return _query_result(socket, "#{pane_id}=#{pane_title}", run)[1]


def pane_processes_by_pane(
    socket: str, run: Runner = run_command
) -> Dict[str, PaneProcess]:
    """Return each pane's root PID and foreground command in one tmux query."""
    _ok, raw = _query_result(
        socket, "#{pane_id}=#{pane_pid}\t#{pane_current_command}", run)
    processes = {}
    for pane, value in raw.items():
        pid_text, separator, command = value.partition("\t")
        if not separator:
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid > 0:
            processes[pane] = PaneProcess(pid=pid, command=command)
    return processes


def select_argvs(socket: str, pane: str) -> List[List[str]]:
    """Make the pane's OWN session show its window and pane -- nothing else.

    Deliberately NO `switch-client`. switch-client is the one tmux command that
    re-attaches a client to a different session, and with no `-c` it acts on an
    arbitrary client on the server. On the documented layout -- several sessions
    on one socket, one gnome-terminal client attached to each -- a jump to
    session B would drag some other terminal's client onto session B (the panel
    lists sessions by socket+pane, so many sessions share the default socket).
    That was a real bug: jumping to one project switched an unrelated terminal.

    select-window and select-pane are session-scoped: `-t <pane>` resolves to the
    pane's session and can only ever change THAT session's current window/pane,
    which the terminal already attached to it follows. GNOME activation raises
    the right X11 window; tmux never needs to move a client between sessions.
    """
    return [
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


@dataclass(frozen=True)
class SendResult:
    """Two independent ways a reply can fail, so the UI can say which happened.

    delivered: the literal text reached the pane. submitted: Enter was accepted.
    A bare bool would collapse "typed but not submitted" (the text sits unsent
    in the input line) into plain success or plain failure, and the user would
    never know their reply is stuck.
    """

    delivered: bool
    submitted: bool

    @property
    def ok(self) -> bool:
        return self.delivered and self.submitted


def send_text(
    socket: str, pane: str, text: str, run: Runner = run_command
) -> SendResult:
    """Type the text, then press Enter -- and report whether each step landed.

    F2: the old version discarded both exit codes, so a reply into a server
    that had just died returned normally and the UI reported success while
    nothing was delivered. That is this project's signature silent-success
    failure, in the one feature the user asked for by name.
    """
    literal_argv, enter_argv = send_text_argvs(socket, pane, text)
    code, _ = run(literal_argv)
    if code != 0:
        # The text never reached the pane. Do NOT press Enter: submitting a bare
        # newline into whatever is there is worse than doing nothing, and a
        # failed literal almost always means the server just died (the segfault
        # case), so Enter would fail too.
        return SendResult(delivered=False, submitted=False)
    code, _ = run(enter_argv)
    return SendResult(delivered=True, submitted=(code == 0))
