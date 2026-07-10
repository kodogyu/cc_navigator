import os
import pathlib
import tempfile
import unittest
from unittest import mock

from ccnav import paths


class StateDirTest(unittest.TestCase):
    def test_uses_xdg_runtime_dir_when_set(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000"}):
            self.assertEqual(
                paths.state_dir(), pathlib.Path("/run/user/1000/cc-navigator")
            )

    def test_falls_back_to_uid_scoped_tmp(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                paths.state_dir(), pathlib.Path("/tmp/cc-navigator-%d" % os.getuid())
            )

    def test_empty_xdg_runtime_dir_is_treated_as_unset(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}):
            self.assertEqual(
                paths.state_dir(), pathlib.Path("/tmp/cc-navigator-%d" % os.getuid())
            )


class EnsureStateDirTest(unittest.TestCase):
    def setUp(self):
        self.runtime = tempfile.TemporaryDirectory()
        self.addCleanup(self.runtime.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.runtime.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.expected = pathlib.Path(self.runtime.name) / "cc-navigator"

    def mode_of(self, directory):
        return directory.stat().st_mode & 0o777

    def test_creates_the_directory_with_mode_0700(self):
        directory = paths.ensure_state_dir()

        self.assertEqual(directory, self.expected)
        self.assertTrue(directory.is_dir())
        self.assertEqual(self.mode_of(directory), 0o700)

    def test_creates_missing_parents(self):
        nested = pathlib.Path(self.runtime.name) / "a" / "b"
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(nested)}):
            directory = paths.ensure_state_dir()

        self.assertTrue(directory.is_dir())
        self.assertEqual(self.mode_of(directory), 0o700)

    def test_tightens_an_existing_loose_directory(self):
        self.expected.mkdir(parents=True)
        os.chmod(str(self.expected), 0o755)

        directory = paths.ensure_state_dir()

        self.assertEqual(self.mode_of(directory), 0o700)

    def test_is_idempotent_and_keeps_existing_contents(self):
        first = paths.ensure_state_dir()
        (first / "session.json").write_text("{}")

        second = paths.ensure_state_dir()

        self.assertEqual(first, second)
        self.assertEqual(self.mode_of(second), 0o700)
        self.assertEqual((second / "session.json").read_text(), "{}")
