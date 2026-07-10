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
        time.sleep(1.0)
        self._write_state(pane)

        collected = app.collect_rows(self.state_dir)

        self.assertEqual(len(collected.rows), 1)
        self.assertEqual(collected.rows[0].title, title)
        self.assertEqual(collected.rows[0].window_title, "ccnav:proj")
        self.assertTrue(collected.rows[0].waiting)
        self.assertEqual(collected.unreachable, 0)

    # -- the reply path: send_text arrives byte-exact and reports success --

    def test_send_text_arrives_byte_exact_and_reports_success(self):
        pane = self._pane_id()
        got = pathlib.Path(self._tmp.name) / "got.txt"
        tmux(
            "send-keys", "-t", pane,
            'IFS= read -r line < /dev/tty; printf %s "$line" > ' + str(got), "Enter",
        )
        time.sleep(0.6)

        payload = "yes; echo 'x' \"y\" $HOME \\ 한글 ✳ Enter C-c"
        result = tmuxctl.send_text(SOCKET, pane, payload)
        time.sleep(0.8)

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
        time.sleep(0.3)
        self.assertFalse(_alive(), "the server really is dead")

        collected = app.collect_rows(self.state_dir)

        self.assertEqual(collected.rows, [], "no live pane, so no row this tick")
        self.assertEqual(collected.unreachable, 1, "the dead socket is reported")
        self.assertEqual(
            [p.name for p in self.state_dir.iterdir()],
            ["s1.json"],
            "an unobserved socket's state file must survive (F3)",
        )

    # -- the liveness path still works when the server is observed ----------

    def test_row_and_file_pruned_when_a_pane_dies_but_the_server_lives(self):
        pane = self._pane_id("proj")
        # A second session keeps the SERVER alive, so its socket stays OBSERVED
        # even after 'proj' is gone -- the case where pruning is correct.
        tmux("new-session", "-d", "-s", "keep")
        self._write_state(pane)

        tmux("kill-session", "-t", "proj")
        time.sleep(0.3)
        self.assertTrue(_alive(), "the server survives on the 'keep' session")

        collected = app.collect_rows(self.state_dir)

        self.assertEqual(collected.rows, [], "proj's pane is genuinely gone")
        self.assertEqual(collected.unreachable, 0, "the socket answered")
        self.assertEqual(
            list(self.state_dir.iterdir()),
            [],
            "an observed-but-absent pane's file is correctly pruned",
        )
