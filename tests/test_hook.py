import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from ccnav import hook, hookstate


ENV = {"TMUX": "/tmp/tmux-1000/default,4039841,0", "TMUX_PANE": "%12"}

PAYLOAD = {
    "hook_event_name": "Notification",
    "notification_type": "permission_prompt",
    "message": "Allow Bash command: npm test?",
    "session_id": "11111111-2222-3333-4444-555555555555",
    "cwd": "/data/projects/demo_project",
}


class TmuxSocketTest(unittest.TestCase):
    def test_takes_the_first_comma_field(self):
        self.assertEqual(
            hook.tmux_socket_from_env(ENV), "/tmp/tmux-1000/default"
        )

    def test_missing_tmux_is_none(self):
        self.assertIsNone(hook.tmux_socket_from_env({}))

    def test_empty_tmux_is_none(self):
        self.assertIsNone(hook.tmux_socket_from_env({"TMUX": ""}))


class BuildRecordTest(unittest.TestCase):
    def test_builds_a_full_record(self):
        result = hook.build_record(PAYLOAD, ENV, now=1783665780)
        self.assertEqual(
            result,
            {
                "session_id": "11111111-2222-3333-4444-555555555555",
                "cwd": "/data/projects/demo_project",
                "tmux_socket": "/tmp/tmux-1000/default",
                "tmux_pane": "%12",
                "state": hookstate.WAITING,
                "reason": "permission_prompt",
                "message": "Allow Bash command: npm test?",
                "updated_at": 1783665780,
            },
        )

    def test_outside_tmux_returns_none(self):
        self.assertIsNone(hook.build_record(PAYLOAD, {}, now=1))

    def test_tmux_without_pane_returns_none(self):
        env = {"TMUX": ENV["TMUX"]}
        self.assertIsNone(hook.build_record(payload=PAYLOAD, env=env, now=1))

    def test_ignored_event_returns_none(self):
        payload = dict(PAYLOAD, hook_event_name="SubagentStop")
        self.assertIsNone(hook.build_record(payload, ENV, now=1))

    def test_missing_session_id_returns_none(self):
        payload = dict(PAYLOAD)
        del payload["session_id"]
        self.assertIsNone(hook.build_record(payload, ENV, now=1))

    def test_unsafe_session_id_returns_none(self):
        payload = dict(PAYLOAD, session_id="../escape")
        self.assertIsNone(hook.build_record(payload, ENV, now=1))

    def test_long_message_is_truncated(self):
        payload = dict(PAYLOAD, message="x" * 5000)
        result = hook.build_record(payload, ENV, now=1)
        self.assertEqual(len(result["message"]), hook.MESSAGE_LIMIT)


class MainTest(unittest.TestCase):
    """Covers hook.main()'s hardest invariant: it always returns 0.

    Every test pins XDG_RUNTIME_DIR to a TemporaryDirectory and clears the
    real environment, so nothing here can touch the actual state directory.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runtime_dir = self._tmp.name
        self.state_dir = pathlib.Path(self.runtime_dir) / "cc-navigator"

    def _run_main(self, stdin_text, env):
        with mock.patch("sys.stdin", io.StringIO(stdin_text)):
            with mock.patch.dict(os.environ, env, clear=True):
                return hook.main()

    def test_returns_0_when_stdin_is_not_json(self):
        result = self._run_main("not json", {"XDG_RUNTIME_DIR": self.runtime_dir})
        self.assertEqual(result, 0)

    def test_returns_0_when_stdin_is_empty(self):
        result = self._run_main("", {"XDG_RUNTIME_DIR": self.runtime_dir})
        self.assertEqual(result, 0)

    def test_returns_0_when_payload_is_a_json_list(self):
        result = self._run_main("[]", {"XDG_RUNTIME_DIR": self.runtime_dir})
        self.assertEqual(result, 0)

    def test_returns_0_when_statestore_write_raises(self):
        env = dict(ENV, XDG_RUNTIME_DIR=self.runtime_dir)
        with mock.patch("ccnav.hook.statestore.write", side_effect=OSError("disk full")):
            result = self._run_main(json.dumps(PAYLOAD), env)
        self.assertEqual(result, 0)

    def test_writes_nothing_without_tmux(self):
        result = self._run_main(json.dumps(PAYLOAD), {"XDG_RUNTIME_DIR": self.runtime_dir})
        self.assertEqual(result, 0)
        self.assertFalse(self.state_dir.exists())

    def test_happy_path_writes_expected_state_file(self):
        payload = {
            "hook_event_name": "Stop",
            "session_id": "abc-123",
            "cwd": "/proj",
        }
        env = dict(ENV, XDG_RUNTIME_DIR=self.runtime_dir)

        result = self._run_main(json.dumps(payload), env)

        self.assertEqual(result, 0)
        written = json.loads((self.state_dir / "abc-123.json").read_text())
        self.assertEqual(written["state"], hookstate.WAITING)
        self.assertEqual(written["reason"], "idle")
        self.assertEqual(written["tmux_pane"], "%12")
        self.assertEqual(written["tmux_socket"], "/tmp/tmux-1000/default")
