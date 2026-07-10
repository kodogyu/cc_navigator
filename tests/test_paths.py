import os
import pathlib
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
