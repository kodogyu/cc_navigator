"""The single place cc_navigator spawns a subprocess."""
from __future__ import annotations

import functools
import json
import queue
import subprocess
import threading
import time
from typing import Callable, Dict, Optional, Sequence, Tuple

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


def request_json_line(
    argv: Sequence[str],
    messages: Sequence[Dict[str, object]],
    response_id: int,
    timeout: float = DEFAULT_TIMEOUT,
    ready_id: Optional[int] = None,
) -> Tuple[int, Optional[dict]]:
    """Talk to a long-lived JSON-lines subprocess until one response arrives.

    Codex app-server exits as soon as stdin reaches EOF, even when an account
    request is still in flight. ``subprocess.run(input=...)`` therefore loses
    the response. Keep stdin open, read line-by-line with a deadline, and tear
    the child down as soon as the matching JSON-RPC id arrives. When
    ``ready_id`` is set, send only the first message, wait for that handshake
    response, then send the remaining messages.

    This remains in proc.py so every process spawned by cc_navigator still has
    one timeout/error boundary. Malformed notification lines are ignored; the
    caller only cares about the response carrying ``response_id``.
    """
    process = None
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            bufsize=1,
        )
        if process.stdin is None or process.stdout is None:
            return 127, None
        lines = queue.Queue()

        def read_stdout() -> None:
            for output_line in process.stdout:
                lines.put(output_line)
            lines.put(None)

        threading.Thread(target=read_stdout, daemon=True).start()

        pending = list(messages)

        def send(batch) -> None:
            for message in batch:
                process.stdin.write(
                    json.dumps(message, separators=(",", ":")) + "\n"
                )
            process.stdin.flush()

        if ready_id is not None and pending:
            send(pending[:1])
            pending = pending[1:]
            waiting_for = ready_id
        else:
            send(pending)
            pending = []
            waiting_for = response_id

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return 124, None
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                return 124, None
            if line is None:
                code = process.poll()
                return (code if code is not None else 1), None
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if isinstance(payload, dict) and payload.get("id") == waiting_for:
                if waiting_for == ready_id:
                    if "error" in payload:
                        return 1, payload
                    send(pending)
                    pending = []
                    waiting_for = response_id
                    continue
                return 0, payload
    except OSError:
        return 127, None
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except OSError:
                    pass
