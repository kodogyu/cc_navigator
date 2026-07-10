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


class AutostartTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def test_enable_creates_enabled_entry(self):
        wiring.set_autostart(True, "/x/cc-navigator", self.dir)
        self.assertTrue(wiring.autostart_enabled(self.dir))
        text = wiring.autostart_path(self.dir).read_text()
        self.assertIn("X-GNOME-Autostart-enabled=true", text)

    def test_disable_flips_the_key_not_deletes(self):
        wiring.set_autostart(True, "/x/cc-navigator", self.dir)
        wiring.set_autostart(False, "/x/cc-navigator", self.dir)
        self.assertTrue(wiring.autostart_path(self.dir).exists())  # not deleted
        self.assertFalse(wiring.autostart_enabled(self.dir))
        self.assertIn("X-GNOME-Autostart-enabled=false",
                      wiring.autostart_path(self.dir).read_text())

    def test_absent_is_not_enabled(self):
        self.assertFalse(wiring.autostart_enabled(self.dir))


import json


class HooksMergeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = pathlib.Path(self._tmp.name) / ".claude" / "settings.json"
        self.cmd = "/repo/bin/cc-navigator-hook"

    def test_install_creates_every_recommended_event(self):
        wiring.install_hooks(self.cmd, self.path)
        self.assertTrue(wiring.hooks_installed(self.cmd, self.path))
        data = json.loads(self.path.read_text())
        self.assertEqual(set(data["hooks"]), set(wiring.RECOMMENDED_HOOKS))

    def test_install_preserves_foreign_hooks_and_keys(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({
            "model": "sonnet",
            "hooks": {"Stop": [{"matcher": "", "hooks": [
                {"type": "command", "command": "/other/tool"}]}]},
        }))
        wiring.install_hooks(self.cmd, self.path)
        data = json.loads(self.path.read_text())
        self.assertEqual(data["model"], "sonnet")  # untouched
        stop_cmds = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
        self.assertIn("/other/tool", stop_cmds)   # foreign kept
        self.assertIn(self.cmd, stop_cmds)        # ours added

    def test_install_is_idempotent(self):
        wiring.install_hooks(self.cmd, self.path)
        wiring.install_hooks(self.cmd, self.path)
        data = json.loads(self.path.read_text())
        stop_cmds = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(stop_cmds.count(self.cmd), 1)  # no duplicate

    def test_remove_strips_only_ours_and_prunes(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({
            "hooks": {"Stop": [{"matcher": "", "hooks": [
                {"type": "command", "command": "/other/tool"}]}]}}))
        wiring.install_hooks(self.cmd, self.path)
        self.assertTrue(wiring.remove_hooks(self.cmd, self.path))
        data = json.loads(self.path.read_text())
        stop_cmds = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(stop_cmds, ["/other/tool"])  # only ours removed
        self.assertNotIn("SessionEnd", data["hooks"])  # our-only event pruned away

    def test_backup_is_written(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{}")
        wiring.install_hooks(self.cmd, self.path)
        backups = list(self.path.parent.glob("settings.json.bak-*"))
        self.assertEqual(len(backups), 1)
