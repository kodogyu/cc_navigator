import os
import pathlib
import tempfile
import unittest
from unittest import mock

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

    def test_non_utf8_autostart_file_is_not_enabled_not_raise(self):
        # A corrupt (non-UTF-8) autostart .desktop must read as "not enabled",
        # never raise -- the settings dialog reads this on every open.
        (self.dir / (wiring.APP_ID + ".desktop")).write_bytes(b"\xff\xfe bad \x80")
        self.assertFalse(wiring.autostart_enabled(self.dir))  # must not raise


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

    def test_install_on_non_utf8_settings_degrades_and_backs_up(self):
        # A non-UTF-8 settings.json must not crash install_hooks: read_text()
        # would raise UnicodeDecodeError (a ValueError) that _load_settings
        # already tolerated. The raw bytes must be preserved in a backup.
        self.path.parent.mkdir(parents=True)
        raw = b"\xff\xfe\x00bad \x80\x81"
        self.path.write_bytes(raw)
        wiring.install_hooks(self.cmd, self.path)  # must not raise
        self.assertTrue(wiring.hooks_installed(self.cmd, self.path))
        backups = list(self.path.parent.glob("settings.json.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), raw)  # original bytes intact

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "mode 000 is still readable by root")
    def test_install_on_unreadable_settings_leaves_it_untouched(self):
        # If the existing file cannot be read to back it up, it must NOT be
        # overwritten -- replacing an un-backupable file would lose it silently.
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"model": "sonnet"}')
        os.chmod(str(self.path), 0)
        self.addCleanup(lambda: os.chmod(str(self.path), 0o600))
        wiring.install_hooks(self.cmd, self.path)  # must not raise
        os.chmod(str(self.path), 0o600)
        self.assertEqual(json.loads(self.path.read_text()), {"model": "sonnet"})
        self.assertFalse(wiring.hooks_installed(self.cmd, self.path))

    def test_install_then_remove_in_same_second_keeps_pristine_backup(self):
        # Two writes in the same wall-clock second must not clobber the pristine
        # pre-install backup (second-granularity names would collide).
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"hooks": {"Stop": [
            {"matcher": "", "hooks": [{"type": "command", "command": "/other/tool"}]}]}}))
        with mock.patch("ccnav.wiring.time.time", return_value=1000000000):
            wiring.install_hooks(self.cmd, self.path)
            wiring.remove_hooks(self.cmd, self.path)
        backups = sorted(self.path.parent.glob("settings.json.bak-*"))
        self.assertEqual(len(backups), 2)  # neither clobbered the other
        pristine = json.loads(backups[0].read_text())  # install's = pre-install
        stop_cmds = [h["command"] for g in pristine["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(stop_cmds, ["/other/tool"])  # our cmd never in the pristine copy

    def test_remove_preserves_an_already_empty_foreign_group(self):
        # A foreign matcher group that was ALREADY empty must survive a remove;
        # only a group WE emptied (by stripping our command) may be pruned.
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"hooks": {"Stop": [
            {"matcher": "foo", "hooks": []},
            {"matcher": "", "hooks": [{"type": "command", "command": self.cmd}]}]}}))
        self.assertTrue(wiring.remove_hooks(self.cmd, self.path))
        data = json.loads(self.path.read_text())
        self.assertEqual(data["hooks"]["Stop"], [{"matcher": "foo", "hooks": []}])

    def test_remove_keeps_an_untouched_empty_foreign_event(self):
        # A foreign event mapped to [] that we never touch must survive a remove
        # triggered by another event -- the event-level prune must fire only for
        # an event WE emptied, mirroring the group-level guard.
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"hooks": {
            "PreCompact": [],
            "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": self.cmd}]}]}}))
        self.assertTrue(wiring.remove_hooks(self.cmd, self.path))
        data = json.loads(self.path.read_text())
        self.assertEqual(data["hooks"].get("PreCompact"), [])  # untouched foreign event kept
        self.assertNotIn("Stop", data["hooks"])                 # our-only event pruned

    def test_reinstalling_an_installed_hook_set_writes_no_new_backup(self):
        # An idempotent install must not rewrite settings.json or drop another
        # settings.json.bak-* -- else re-opening the dialog spams ~/.claude.
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{}")
        wiring.install_hooks(self.cmd, self.path)        # first: writes + 1 backup
        first = json.loads(self.path.read_text())
        self.assertEqual(len(list(self.path.parent.glob("settings.json.bak-*"))), 1)
        wiring.install_hooks(self.cmd, self.path)        # already installed: no-op
        self.assertEqual(len(list(self.path.parent.glob("settings.json.bak-*"))), 1)
        self.assertEqual(json.loads(self.path.read_text()), first)


class CodexHooksMergeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = pathlib.Path(self._tmp.name) / ".codex" / "hooks.json"
        self.cmd = "/repo/bin/cc-navigator-hook --provider codex"

    def test_installs_only_codex_supported_events(self):
        wiring.install_hooks(self.cmd, self.path, wiring.CODEX_RECOMMENDED_HOOKS)
        data = json.loads(self.path.read_text())
        self.assertEqual(set(data["hooks"]), set(wiring.CODEX_RECOMMENDED_HOOKS))
        self.assertNotIn("PermissionRequest", data["hooks"])
        self.assertNotIn("Notification", data["hooks"])
        self.assertNotIn("SessionEnd", data["hooks"])
        self.assertTrue(wiring.hooks_installed(
            self.cmd, self.path, wiring.CODEX_RECOMMENDED_HOOKS))

    def test_preserves_foreign_codex_hooks_and_removes_only_ours(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"hooks": {"Stop": [{
            "hooks": [{"type": "command", "command": "/other/codex-hook"}]
        }]}}))
        wiring.install_hooks(self.cmd, self.path, wiring.CODEX_RECOMMENDED_HOOKS)
        self.assertTrue(wiring.remove_hooks(self.cmd, self.path))
        data = json.loads(self.path.read_text())
        commands = [hook["command"] for group in data["hooks"]["Stop"]
                    for hook in group["hooks"]]
        self.assertEqual(commands, ["/other/codex-hook"])
