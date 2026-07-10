import pathlib
import tempfile
import unittest

from ccnav import wiring


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.apps = pathlib.Path(self._tmp.name)

    def test_install_then_detect_then_remove(self):
        self.assertFalse(wiring.launcher_installed(self.apps))
        wiring.install_launcher("/home/u/.local/bin/cc-navigator", self.apps)
        self.assertTrue(wiring.launcher_installed(self.apps))
        text = wiring.launcher_path(self.apps).read_text()
        self.assertIn("Exec=/home/u/.local/bin/cc-navigator", text)
        self.assertIn("Type=Application", text)
        self.assertTrue(wiring.remove_launcher(self.apps))
        self.assertFalse(wiring.launcher_installed(self.apps))

    def test_install_is_idempotent(self):
        wiring.install_launcher("/x/cc-navigator", self.apps)
        wiring.install_launcher("/x/cc-navigator", self.apps)  # must not raise
        self.assertTrue(wiring.launcher_installed(self.apps))

    def test_remove_missing_is_false(self):
        self.assertFalse(wiring.remove_launcher(self.apps))
