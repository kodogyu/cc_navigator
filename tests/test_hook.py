import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

from ccnav import hook, hookstate, paths, statestore


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
                "last_prompt": "",
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

    def test_multiline_message_is_flattened_to_one_line(self):
        payload = dict(PAYLOAD, message="line one\n\nline two\ttabbed")
        result = hook.build_record(payload, ENV, now=1)
        self.assertEqual(result["message"], "line one line two tabbed")
        self.assertNotIn("\n", result["message"])


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

    def test_returns_0_when_ensure_state_dir_raises(self):
        # ensure_state_dir() is designed to raise (PermissionError on a foreign
        # state dir; OSError from mkdir/open). Task 9 moved it to the top of
        # main() -- unwrapped it would propagate and break Claude Code.
        env = dict(ENV, XDG_RUNTIME_DIR=self.runtime_dir)
        with mock.patch(
            "ccnav.hook.paths.ensure_state_dir",
            side_effect=PermissionError("foreign-owned state dir"),
        ):
            result = self._run_main(json.dumps(PAYLOAD), env)
        self.assertEqual(result, 0)

    def test_returns_0_when_read_one_raises(self):
        # read_one() joined the critical path in Task 9. Even if it ever raised
        # (e.g. a decode error on a corrupt prior record), main() must return 0.
        env = dict(ENV, XDG_RUNTIME_DIR=self.runtime_dir)
        boom = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        with mock.patch("ccnav.hook.statestore.read_one", side_effect=boom):
            result = self._run_main(json.dumps(PAYLOAD), env)
        self.assertEqual(result, 0)

    def test_writes_nothing_without_tmux(self):
        # main() now reads the prior record (Task 9) before build_record's
        # tmux check runs, which creates the state dir as a side effect via
        # ensure_state_dir. The invariant this test guards is that no state
        # *file* is written, not that the directory never comes into being.
        result = self._run_main(json.dumps(PAYLOAD), {"XDG_RUNTIME_DIR": self.runtime_dir})
        self.assertEqual(result, 0)
        self.assertEqual(list(self.state_dir.glob("*.json")), [])

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


class LastPromptTest(unittest.TestCase):
    ENV = {"TMUX": "/x,1,0", "TMUX_PANE": "%1"}

    def test_user_prompt_is_captured_and_truncated(self):
        rec = hook.build_record(
            {"session_id": "s", "hook_event_name": "UserPromptSubmit",
             "user_prompt": "x" * 500}, self.ENV, 1)
        self.assertEqual(rec["last_prompt"], "x" * hook.PROMPT_LIMIT)

    def test_falls_back_to_prompt_field(self):
        rec = hook.build_record(
            {"session_id": "s", "hook_event_name": "UserPromptSubmit",
             "prompt": "hello"}, self.ENV, 1)
        self.assertEqual(rec["last_prompt"], "hello")

    def test_prompt_is_carried_forward_across_a_promptless_event(self):
        rec = hook.build_record(
            {"session_id": "s", "hook_event_name": "Stop"}, self.ENV, 1,
            previous={"last_prompt": "earlier"})
        self.assertEqual(rec["last_prompt"], "earlier")

    def test_no_previous_and_no_prompt_is_empty(self):
        rec = hook.build_record(
            {"session_id": "s", "hook_event_name": "Stop"}, self.ENV, 1)
        self.assertEqual(rec["last_prompt"], "")

    def test_multiline_prompt_is_flattened_to_one_line(self):
        # A task-notification / pasted diff submitted as a turn arrives with
        # embedded newlines; it must be stored as a single line (see the
        # 'broken content' report where a raw <task-notification> blob rendered
        # as many raw lines and wrecked the panel row).
        rec = hook.build_record(
            {"session_id": "s", "hook_event_name": "UserPromptSubmit",
             "user_prompt": "<task-notification>\n<id>x</id>\n<status>done</status>"},
            self.ENV, 1)
        self.assertEqual(
            rec["last_prompt"], "<task-notification> <id>x</id> <status>done</status>")
        self.assertNotIn("\n", rec["last_prompt"])


class SessionEndTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)
        self._orig = paths.ensure_state_dir
        paths.ensure_state_dir = lambda: self.dir

    def tearDown(self):
        paths.ensure_state_dir = self._orig

    def _run(self, payload, env):
        stdin = io.StringIO(json.dumps(payload))
        orig_stdin, sys.stdin = sys.stdin, stdin
        orig_env = os.environ.copy()
        os.environ.clear(); os.environ.update(env)
        try:
            return hook.main()
        finally:
            sys.stdin = orig_stdin
            os.environ.clear(); os.environ.update(orig_env)

    def test_session_end_deletes_the_state_file(self):
        statestore.write(self.dir, {"session_id": "s1", "state": "waiting",
                                    "tmux_socket": "/x", "tmux_pane": "%1"})
        code = self._run(
            {"session_id": "s1", "hook_event_name": "SessionEnd", "source": "logout"},
            {"TMUX": "/x,1,0", "TMUX_PANE": "%1"})
        self.assertEqual(code, 0)
        self.assertFalse((self.dir / "s1.json").exists())

    def test_session_end_without_pane_still_deletes(self):
        statestore.write(self.dir, {"session_id": "s2", "state": "waiting",
                                    "tmux_socket": "/x", "tmux_pane": "%1"})
        code = self._run(
            {"session_id": "s2", "hook_event_name": "SessionEnd", "source": "clear"},
            {})  # no TMUX in a background session
        self.assertEqual(code, 0)
        self.assertFalse((self.dir / "s2.json").exists())
