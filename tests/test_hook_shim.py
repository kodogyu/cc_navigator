"""The shell shim, exercised as Claude Code actually runs it: as a subprocess.

tests/test_hook.py covers hook.main() in-process. These tests cover the thing
in between -- argv[0] resolution, PYTHONPATH, and the unconditional exit 0.
"""
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

SHIM = pathlib.Path(__file__).resolve().parent.parent / "bin" / "cc-navigator-hook"

PAYLOAD = '{"hook_event_name":"Stop","session_id":"shim-1","cwd":"/proj"}'


class HookShimTest(unittest.TestCase):
    def setUp(self):
        self._runtime = tempfile.TemporaryDirectory()
        self._elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(self._runtime.cleanup)
        self.addCleanup(self._elsewhere.cleanup)
        self.state_dir = pathlib.Path(self._runtime.name) / "cc-navigator"

    def env(self):
        return dict(
            os.environ,
            XDG_RUNTIME_DIR=self._runtime.name,
            TMUX="/tmp/tmux-1000/default,1,0",
            TMUX_PANE="%1",
        )

    def invoke(self, command, cwd=None):
        return subprocess.run(
            command,
            input=PAYLOAD,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=self.env(),
            cwd=cwd or self._elsewhere.name,
        )

    def assert_wrote_state(self, result):
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        written = self.state_dir / "shim-1.json"
        self.assertTrue(written.is_file(), "the shim wrote no state file")
        self.assertEqual(json.loads(written.read_text())["reason"], "idle")

    def test_writes_state_when_invoked_by_absolute_path(self):
        self.assert_wrote_state(self.invoke([str(SHIM)]))

    def test_writes_state_when_invoked_through_a_symlink(self):
        # `dirname "$0"` gives the symlink's directory, not the target's. Without
        # readlink -f, PYTHONPATH misses src/, the import fails, 2>/dev/null eats
        # the traceback, and the hook silently never writes state.
        link_dir = pathlib.Path(self._elsewhere.name) / "bin"
        link_dir.mkdir()
        link = link_dir / "cc-navigator-hook"
        link.symlink_to(SHIM)

        self.assert_wrote_state(self.invoke([str(link)]))

    def test_exits_0_and_stays_silent_on_garbage(self):
        for junk in ("not json", "", "[]", "null"):
            result = subprocess.run(
                [str(SHIM)],
                input=junk,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                env=self.env(),
                cwd=self._elsewhere.name,
            )
            self.assertEqual(result.returncode, 0, "input %r" % junk)
            self.assertEqual(result.stdout, "", "input %r" % junk)
            self.assertEqual(result.stderr, "", "input %r" % junk)
