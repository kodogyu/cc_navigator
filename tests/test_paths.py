import os
import pathlib
import socket
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


class TmuxSocketsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.uid = os.getuid()
        self.directory = pathlib.Path(self.temp.name) / ("tmux-%d" % self.uid)
        self.directory.mkdir(mode=0o700)
        self.open_sockets = []
        self.addCleanup(self.close_sockets)

    def unix_socket(self, path):
        opened = socket.socket(socket.AF_UNIX)
        opened.bind(str(path))
        self.open_sockets.append(opened)
        return path

    def close_sockets(self):
        for opened in self.open_sockets:
            opened.close()

    def test_lists_same_user_sockets_in_the_standard_tmux_directory(self):
        default = self.unix_socket(self.directory / "default")
        (self.directory / "not-a-socket").write_text("x")
        self.assertEqual(
            paths.tmux_sockets(
                env={"TMUX_TMPDIR": self.temp.name}, uid=self.uid),
            [str(default)],
        )

    def test_includes_an_inherited_explicit_socket(self):
        explicit = self.unix_socket(pathlib.Path(self.temp.name) / "explicit.sock")
        self.assertIn(
            str(explicit),
            paths.tmux_sockets(
                env={"TMUX_TMPDIR": self.temp.name,
                     "TMUX": str(explicit) + ",123,0"},
                uid=self.uid),
        )

    def test_ignores_internal_doctor_probe_sockets(self):
        probe = self.unix_socket(self.directory / "ccnav_probe_12345")
        ordinary = self.unix_socket(self.directory / "ccnav_probe_work")

        found = paths.tmux_sockets(
            env={"TMUX_TMPDIR": self.temp.name}, uid=self.uid)

        self.assertNotIn(str(probe), found)
        self.assertIn(str(ordinary), found, "only the PID-only namespace is reserved")

    def test_cleanup_removes_only_a_same_user_pid_named_socket(self):
        probe = self.unix_socket(self.directory / "ccnav_probe_12345")

        removed = paths.cleanup_tmux_probe_socket(
            probe.name,
            env={"TMUX_TMPDIR": self.temp.name},
            uid=self.uid,
        )

        self.assertTrue(removed)
        self.assertFalse(probe.exists())

    def test_cleanup_refuses_a_lookalike_or_regular_file(self):
        lookalike = self.unix_socket(self.directory / "ccnav_probe_work")
        regular = self.directory / "ccnav_probe_12345"
        regular.write_text("not a socket")

        self.assertFalse(paths.cleanup_tmux_probe_socket(
            lookalike.name,
            env={"TMUX_TMPDIR": self.temp.name},
            uid=self.uid,
        ))
        self.assertFalse(paths.cleanup_tmux_probe_socket(
            regular.name,
            env={"TMUX_TMPDIR": self.temp.name},
            uid=self.uid,
        ))
        self.assertTrue(lookalike.exists())
        self.assertTrue(regular.exists())

    def test_cleanup_refuses_a_symlinked_tmux_directory(self):
        real_directory = pathlib.Path(self.temp.name) / "real-tmux-directory"
        real_directory.mkdir()
        probe = self.unix_socket(real_directory / "ccnav_probe_12345")
        linked_base = pathlib.Path(self.temp.name) / "linked"
        linked_base.mkdir()
        (linked_base / ("tmux-%d" % self.uid)).symlink_to(
            real_directory, target_is_directory=True)

        removed = paths.cleanup_tmux_probe_socket(
            probe.name,
            env={"TMUX_TMPDIR": str(linked_base)},
            uid=self.uid,
        )

        self.assertFalse(removed)
        self.assertTrue(probe.exists())

    def test_missing_socket_directory_is_empty(self):
        self.assertEqual(
            paths.tmux_sockets(
                env={"TMUX_TMPDIR": str(pathlib.Path(self.temp.name) / "missing")},
                uid=self.uid),
            [],
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

    def test_refuses_a_symlinked_state_dir_and_does_not_chmod_its_target(self):
        # An attacker plants our predictable path as a symlink to a dir they
        # want chmod'd. We must refuse rather than follow it.
        victim = pathlib.Path(self.runtime.name) / "victim"
        victim.mkdir()
        os.chmod(str(victim), 0o755)
        os.symlink(str(victim), str(self.expected))

        with self.assertRaises(OSError):
            paths.ensure_state_dir()

        self.assertEqual(
            self.mode_of(victim), 0o755, "the symlink target must be untouched"
        )

    def test_refuses_a_state_dir_owned_by_another_user(self):
        self.expected.mkdir(parents=True)
        # We own self.expected, so pretend to be a different uid: the ownership
        # check must then reject a directory we do not own.
        with mock.patch("os.getuid", return_value=os.getuid() + 1):
            with self.assertRaises(PermissionError):
                paths.ensure_state_dir()

    def test_refuses_a_state_dir_that_is_a_regular_file(self):
        self.expected.parent.mkdir(parents=True, exist_ok=True)
        self.expected.write_text("not a directory")

        with self.assertRaises(OSError):
            paths.ensure_state_dir()
