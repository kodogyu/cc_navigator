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
                "kind": "tmux",
                "claude_pid": 0,
                "ai_title": "",
                "tmux_socket": "/tmp/tmux-1000/default",
                "tmux_pane": "%12",
                "state": hookstate.WAITING,
                "reason": "permission_prompt",
                "message": "Allow Bash command: npm test?",
                "updated_at": 1783665780,
                "last_prompt": "",
                "subagent_ids": [],
            },
        )

    def test_idle_prompt_notification_is_reported_and_message_free(self):
        # The idle nudge must leave a finished session GREEN, and must not stamp
        # a "waiting for input" message onto that green row.
        payload = dict(PAYLOAD, notification_type="idle_prompt",
                       message="Claude is waiting for your input")
        rec = hook.build_record(payload, ENV, now=1)
        self.assertEqual(rec["state"], hookstate.WAITING)
        self.assertEqual(rec["reason"], hookstate.STOP_IDLE)  # green, not red
        self.assertEqual(rec["message"], "")

    def test_permission_prompt_notification_stays_red_with_its_message(self):
        rec = hook.build_record(PAYLOAD, ENV, now=1)  # PAYLOAD is a permission_prompt
        self.assertEqual(rec["reason"], "permission_prompt")
        self.assertEqual(rec["message"], "Allow Bash command: npm test?")

    def test_outside_tmux_returns_none(self):
        self.assertIsNone(hook.build_record(PAYLOAD, {}, now=1))

    def test_tmux_without_pane_returns_none(self):
        env = {"TMUX": ENV["TMUX"]}
        self.assertIsNone(hook.build_record(payload=PAYLOAD, env=env, now=1))

    def test_ignored_event_returns_none(self):
        # PreCompact carries no state change (classify returns None), so no record
        # is written. (SubagentStop now maps to WORKING, so it is no longer inert.)
        payload = dict(PAYLOAD, hook_event_name="PreCompact")
        self.assertIsNone(hook.build_record(payload, ENV, now=1))

    def test_a_late_resume_event_keeps_an_idle_session_idle(self):
        # After Stop (WAITING/idle -> green), a late PostToolUse from the just-
        # ended turn must NOT re-light the working spinner. A late SubagentStop
        # is inert too: it carries no main state and its id set is unchanged.
        idle = {"state": hookstate.WAITING, "reason": hookstate.STOP_IDLE, "last_prompt": "hi"}
        for event in ("PostToolUse", "SubagentStop"):
            payload = dict(PAYLOAD, hook_event_name=event, tool_name="Bash")
            self.assertIsNone(
                hook.build_record(payload, ENV, now=2, previous=idle),
                "%s must not override an idle session" % event)

    def test_a_resume_event_still_clears_a_red_wait(self):
        # The un-stick fix is preserved: a PostToolUse resume clears a red input
        # wait (any reason that is NOT Stop's reserved idle).
        red = {"state": hookstate.WAITING, "reason": "permission_prompt"}
        payload = dict(PAYLOAD, hook_event_name="PostToolUse", tool_name="Bash")
        rec = hook.build_record(payload, ENV, now=2, previous=red)
        self.assertEqual(rec["state"], hookstate.WORKING)

    def test_a_post_tool_use_with_no_previous_is_working(self):
        payload = dict(PAYLOAD, hook_event_name="PostToolUse", tool_name="Bash")
        rec = hook.build_record(payload, ENV, now=2, previous=None)
        self.assertEqual(rec["state"], hookstate.WORKING)


class SubagentTrackingTest(unittest.TestCase):
    def _rec(self, event, previous, agent_id=None, **extra):
        payload = dict(PAYLOAD, hook_event_name=event, **extra)
        if agent_id is not None:
            payload["agent_id"] = agent_id
        return hook.build_record(payload, ENV, now=5, previous=previous)

    def test_subagent_start_records_the_running_id(self):
        rec = self._rec("SubagentStart", previous=None, agent_id="sub-1")
        self.assertEqual(rec["subagent_ids"], ["sub-1"])
        # No previous state -> the main agent is taken to be working.
        self.assertEqual(rec["state"], hookstate.WORKING)

    def test_subagent_stop_removes_only_its_own_id(self):
        prev = {"state": hookstate.WORKING, "reason": "", "subagent_ids": ["sub-1", "sub-2"]}
        rec = self._rec("SubagentStop", previous=prev, agent_id="sub-1")
        self.assertEqual(rec["subagent_ids"], ["sub-2"])

    def test_a_subagent_event_carries_the_main_state_forward(self):
        # The flagship case: main is blocked on the user (red) while a subagent
        # runs. The SubagentStart must NOT disturb the red wait.
        red = {"state": hookstate.WAITING, "reason": "permission_prompt",
               "message": "Allow Bash?", "last_prompt": "hi"}
        rec = self._rec("SubagentStart", previous=red, agent_id="sub-1")
        self.assertEqual(rec["state"], hookstate.WAITING)
        self.assertEqual(rec["reason"], "permission_prompt")
        self.assertEqual(rec["message"], "Allow Bash?")   # the wait's text persists
        self.assertEqual(rec["subagent_ids"], ["sub-1"])

    def test_an_unchanged_subagent_set_writes_nothing(self):
        # A SubagentStop for an id that is not tracked (or a duplicate Start)
        # leaves the set unchanged, so there is nothing to persist.
        prev = {"state": hookstate.WORKING, "reason": "", "subagent_ids": ["sub-1"]}
        self.assertIsNone(self._rec("SubagentStop", previous=prev, agent_id="ghost"))
        self.assertIsNone(self._rec("SubagentStart", previous=prev, agent_id="sub-1"))

    def test_a_turn_boundary_clears_the_running_set(self):
        # Stop / a new prompt / session start reset the set -- the leak safety net.
        prev = {"state": hookstate.WORKING, "reason": "", "subagent_ids": ["a", "b"]}
        for event in ("Stop", "UserPromptSubmit", "SessionStart"):
            rec = self._rec(event, previous=prev)
            self.assertEqual(rec["subagent_ids"], [], event)

    def test_a_normal_classified_event_carries_the_running_set(self):
        # A Notification (main goes red) while a subagent runs keeps the set.
        prev = {"state": hookstate.WORKING, "reason": "", "subagent_ids": ["a"]}
        rec = self._rec("Notification", previous=prev, notification_type="permission_prompt")
        self.assertEqual(rec["state"], hookstate.WAITING)
        self.assertEqual(rec["subagent_ids"], ["a"])

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
