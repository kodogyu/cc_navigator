"""The single place cc_navigator spawns a subprocess."""
from __future__ import annotations

import functools
import subprocess
from typing import Callable, Sequence, Tuple

Runner = Callable[[Sequence[str]], Tuple[int, str]]

# Every caller already treats a nonzero exit as failure (tmuxctl._query
# returns {}; gnome.eval_js returns False), so a bounded timeout composes for
# free with the existing error handling -- nothing downstream needs to learn
# about timeouts specifically.
DEFAULT_TIMEOUT = 5.0


def run_command(argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT) -> Tuple[int, str]:
    try:
        completed = subprocess.run(
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run() kills the child before raising this -- verified in
        # tests/test_proc.py rather than assumed. 124 is the shell's
        # conventional timeout status: nonzero, so every existing caller's
        # "code != 0 means failure" check already covers it.
        return 124, ""
    except OSError:
        # The binary is missing (FileNotFoundError), not executable, or not a file.
        # This has to be an exit code like any other failure, not an exception: the
        # callers are a diagnostic tool, the app's startup probe, and a notification
        # worker thread, and an escaping FileNotFoundError killed all three (the
        # doctor printed a traceback and ZERO checks on a box without gdbus -- the
        # exact fresh machine it exists to diagnose). 127 is the shell's convention
        # for "command not found": nonzero, so "code != 0 means failure" covers it.
        return 127, ""
    return completed.returncode, completed.stdout


def runner_with_timeout(timeout: float) -> Runner:
    """A Runner bound to a tighter deadline than DEFAULT_TIMEOUT.

    A Runner takes only argv, so a caller that knows its command should be
    instant has no other way to say so.
    """
    return functools.partial(run_command, timeout=timeout)
