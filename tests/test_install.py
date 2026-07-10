import os
import pathlib
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]


class InstallScriptTest(unittest.TestCase):
    def _run(self, home):
        env = dict(os.environ, HOME=str(home))
        return subprocess.run(["sh", str(REPO / "install")], env=env,
                              capture_output=True, text=True)

    def test_creates_symlink_on_path(self):
        with tempfile.TemporaryDirectory() as home:
            r = self._run(home)
            self.assertEqual(r.returncode, 0, r.stderr)
            link = pathlib.Path(home) / ".local" / "bin" / "cc-navigator"
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.path.realpath(link), str(REPO / "bin" / "cc-navigator"))

    def test_is_idempotent(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(self._run(home).returncode, 0)
            self.assertEqual(self._run(home).returncode, 0)  # second run must not fail
