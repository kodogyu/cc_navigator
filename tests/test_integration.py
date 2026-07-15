"""Drives a real tmux server on a private socket. No GNOME, no display.

Never touches the user's tmux server or their ~/.tmux.conf: every server here
uses an explicit -S socket in our own temp dir and is started with -f /dev/null.
This is the seam the unit tests could only assert in isolation -- the OSC title
an inner program sets really becoming a Row, a reply really arriving byte-exact,
and the F3 pruning rule behaving correctly against a real, and a really-dead,
tmux server.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import unittest

from ccnav import app, hook, statestore, tmuxctl

# A private socket in our own temp dir: this test can never touch a real session.
# Kept short on purpose -- an AF_UNIX path over ~108 bytes fails with "File name
# too long" for reasons that have nothing to do with what is under test.
SOCKET = os.path.join(tempfile.gettempdir(), "ccnav-itest-%d" % os.getuid())


def tmux(*args):
    return subprocess.run(
        ["tmux", "-S", SOCKET] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        universal_newlines=True,
    )


def _alive():
    """The honest liveness probe: list-sessions exit 0 means the server serves.
    (`pgrep -x tmux` never matches -- the server's comm is `tmux: server`.)"""
    return tmux("list-sessions").returncode == 0


def wait_until(condition, what, timeout=10.0):
    """Poll `condition` until it holds. Everything this file waits on -- a shell
    running a command, tmux recording an OSC title, a server finishing its death --
    takes as long as the machine is busy. Sleeping a fixed 0.3-1.0s and hoping was
    this suite's flakiness: pin every core and those tests failed every run, on a slow
    machine rather than on wrong code. Poll the real thing instead.
    """
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError("timed out after %.1fs waiting for: %s" % (timeout, what))
        time.sleep(0.02)


@unittest.skipUnless(shutil.which("tmux"), "needs tmux")
class TmuxIntegrationTest(unittest.TestCase):
    def setUp(self):
        tmux("kill-server")
        # -f /dev/null: never load the user's ~/.tmux.conf.
        subprocess.run(
            ["tmux", "-S", SOCKET, "-f", "/dev/null", "new-session", "-d", "-s", "proj"],
            check=True,
        )
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = pathlib.Path(self._tmp.name)

    def tearDown(self):
        tmux("kill-server")
        # tmux 3.0a leaves a 0-byte socket file after kill-server; remove it so
        # the test leaves nothing behind at all.
        try:
            os.unlink(SOCKET)
        except OSError:
            pass
        self._tmp.cleanup()

    def _pane_id(self, session="proj"):
        return tmux("display", "-p", "-t", session, "#{pane_id}").stdout.strip()

    def _write_state(self, pane, session_id="s1"):
        record = hook.build_record(
            {"hook_event_name": "Stop", "session_id": session_id, "cwd": "/proj"},
            {"TMUX": SOCKET + ",1,0", "TMUX_PANE": pane},
            now=int(time.time()),
        )
        self.assertIsNotNone(record, "build_record needs TMUX + TMUX_PANE")
        statestore.write(self.state_dir, record)

    # -- the join: an inner program's OSC title becomes a Row --------------

    def test_pane_title_from_an_osc_escape_reaches_the_row(self):
        pane = self._pane_id()
        title = "✳ 작업 중 (demo-project)"
        tmux("send-keys", "-t", pane, "printf '\\033]2;%s\\007'" % title, "Enter")
        # The shell has to actually run the printf and tmux has to parse the escape.
        # Wait for the title tmux itself reports -- the very thing under test.
        wait_until(lambda: tmuxctl.titles_by_pane(SOCKET).get(pane) == title,
                   "tmux to record the pane title set by the OSC escape")
        self._write_state(pane)

        collected = app.collect_rows(self.state_dir, socket_candidates=lambda: [])

        self.assertEqual(len(collected.rows), 1)
        self.assertEqual(collected.rows[0].title, title)
        self.assertEqual(collected.rows[0].window_title, "ccnav:proj")
        self.assertTrue(collected.rows[0].waiting)
        self.assertEqual(collected.unreachable, 0)

    # -- the reply path: send_text arrives byte-exact and reports success --

    def test_send_text_arrives_byte_exact_and_reports_success(self):
        pane = self._pane_id()
        got = pathlib.Path(self._tmp.name) / "got.txt"
        ready = pathlib.Path(self._tmp.name) / "ready"
        # The reader announces itself: it touches `ready` immediately before blocking on
        # the read, so we can wait for the shell to actually be there instead of
        # sleeping 0.6s and hoping it beat us to it.
        tmux(
            "send-keys", "-t", pane,
            "touch " + str(ready) + "; IFS= read -r line < /dev/tty; "
            'printf %s "$line" > ' + str(got),
            "Enter",
        )
        wait_until(ready.exists, "the shell to reach the read")

        payload = "yes; echo 'x' \"y\" $HOME \\ 한글 ✳ Enter C-c"
        result = tmuxctl.send_text(SOCKET, pane, payload)
        wait_until(lambda: got.exists() and got.read_text() == payload,
                   "the reply to arrive byte-exact in the pane")

        self.assertEqual(got.read_text(), payload)
        self.assertTrue(result.ok, "a delivered+submitted reply must report success")

    # -- F3: a whole server dying must NOT delete the state file -----------

    def test_row_gone_but_state_survives_when_the_whole_server_dies(self):
        # CORRECTED from the plan, which asserted the state dir goes empty here.
        # That assertion pinned the F3 bug: a dead socket answers nothing, and
        # pruning on that empty answer would delete a live session's file. Now
        # the socket is UNOBSERVED, so the row leaves the UI (its pane is gone)
        # but the file is spared and returns if the socket ever answers again.
        pane = self._pane_id()
        self._write_state(pane)
        tmux("kill-server")
        wait_until(lambda: not _alive(), "the tmux server to actually die")
        self.assertFalse(_alive(), "the server really is dead")

        collected = app.collect_rows(self.state_dir, socket_candidates=lambda: [])

        self.assertEqual(collected.rows, [], "no live pane, so no row this tick")
        self.assertEqual(collected.unreachable, 1, "the dead socket is reported")
        remaining = statestore.read_all(self.state_dir)
        self.assertEqual(
            len(remaining), 1,
            "an unobserved socket's state file must survive (F3)")
        self.assertEqual(remaining[0]["session_id"], "s1")

    # -- the liveness path still works when the server is observed ----------

    def test_row_and_file_pruned_when_a_pane_dies_but_the_server_lives(self):
        pane = self._pane_id("proj")
        # A second session keeps the SERVER alive, so its socket stays OBSERVED
        # even after 'proj' is gone -- the case where pruning is correct.
        tmux("new-session", "-d", "-s", "keep")
        self._write_state(pane)

        tmux("kill-session", "-t", "proj")
        # Wait for the pane to actually leave tmux's list -- that absence is the whole
        # premise of the assertions below.
        wait_until(lambda: pane not in tmuxctl.sessions_by_pane_result(SOCKET)[1],
                   "proj's pane to disappear from tmux")
        self.assertTrue(_alive(), "the server survives on the 'keep' session")

        collected = app.collect_rows(self.state_dir, socket_candidates=lambda: [])

        self.assertEqual(collected.rows, [], "proj's pane is genuinely gone")
        self.assertEqual(collected.unreachable, 0, "the socket answered")
        self.assertEqual(
            list(self.state_dir.iterdir()),
            [],
            "an observed-but-absent pane's file is correctly pruned",
        )
