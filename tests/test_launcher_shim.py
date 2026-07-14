"""bin/cc-navigator exercised as Claude Code -- err, the user -- actually runs
it: as a subprocess, through whatever path ends up on $PATH or a desktop
launcher, which may be a symlink.

DISPLAY is deliberately pointed at :99, a display that does not exist, so
this never touches the real, live X server. That is enough to prove the
launcher got past PYTHONPATH resolution and import: the failure it produces
is GTK/Gdk failing to open a display, not a Python import error.
"""
import os
import pathlib
import subprocess
import tempfile
import unittest

LAUNCHER = pathlib.Path(__file__).resolve().parent.parent / "bin" / "cc-navigator"


class LauncherShimTest(unittest.TestCase):
    def setUp(self):
        self._elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(self._elsewhere.cleanup)

    def env(self):
        env = dict(os.environ)
        env["DISPLAY"] = ":99"  # not a live display -- see module docstring
        # Isolate the runtime dir so the single-instance lock is this test's own,
        # never the live panel's: otherwise a running cc_navigator holds the lock
        # and the launcher takes its raise-existing-and-exit-0 path, which would
        # mask the display failure this test asserts on.
        env["XDG_RUNTIME_DIR"] = self._elsewhere.name
        return env

    def invoke(self, command):
        return subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=self.env(),
            cwd=self._elsewhere.name,
            timeout=15,
        )

    def test_runs_through_a_symlink_with_pythonpath_resolved(self):
        # Same defect Task 4's review found in bin/cc-navigator-hook:
        # dirname "$0" alone would resolve to this symlink's own directory,
        # which has no ../src, and PYTHONPATH would miss ccnav entirely.
        link_dir = pathlib.Path(self._elsewhere.name) / "bin"
        link_dir.mkdir()
        link = link_dir / "cc-navigator"
        link.symlink_to(LAUNCHER)

        result = self.invoke([str(link)])

        self.assertNotEqual(result.returncode, 0, "an invalid DISPLAY must not succeed")
        combined = result.stdout + result.stderr
        self.assertNotIn("ModuleNotFoundError", combined)
        self.assertNotIn("ImportError", combined)
        self.assertNotIn("No module named", combined)

    def test_invoked_by_absolute_path_also_resolves(self):
        result = self.invoke([str(LAUNCHER)])
        combined = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("ModuleNotFoundError", combined)
        self.assertNotIn("No module named", combined)

    def test_errors_are_not_swallowed(self):
        # Unlike the hook, this launcher must let a startup failure surface:
        # no "exit 0", no 2>/dev/null.
        result = self.invoke([str(LAUNCHER)])
        combined = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(combined.strip(), "the launcher must not swallow its error output")
