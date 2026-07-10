import unittest

from ccnav import hookstate


class ClassifyTest(unittest.TestCase):
    def test_session_start_is_working(self):
        result = hookstate.classify({"hook_event_name": "SessionStart"})
        self.assertEqual(result, (hookstate.WORKING, ""))

    def test_user_prompt_submit_is_working(self):
        result = hookstate.classify({"hook_event_name": "UserPromptSubmit"})
        self.assertEqual(result, (hookstate.WORKING, ""))

    def test_notification_carries_its_type_as_reason(self):
        result = hookstate.classify(
            {"hook_event_name": "Notification", "notification_type": "permission_prompt"}
        )
        self.assertEqual(result, (hookstate.WAITING, "permission_prompt"))

    def test_notification_without_a_type_still_waits(self):
        result = hookstate.classify({"hook_event_name": "Notification"})
        self.assertEqual(result, (hookstate.WAITING, "notification"))

    def test_notification_with_empty_type_still_waits(self):
        result = hookstate.classify(
            {"hook_event_name": "Notification", "notification_type": ""}
        )
        self.assertEqual(result, (hookstate.WAITING, "notification"))

    def test_ask_user_question_waits(self):
        result = hookstate.classify(
            {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"}
        )
        self.assertEqual(result, (hookstate.WAITING, "question"))

    def test_exit_plan_mode_waits(self):
        result = hookstate.classify(
            {"hook_event_name": "PreToolUse", "tool_name": "ExitPlanMode"}
        )
        self.assertEqual(result, (hookstate.WAITING, "plan"))

    def test_other_tools_are_ignored(self):
        result = hookstate.classify(
            {"hook_event_name": "PreToolUse", "tool_name": "Bash"}
        )
        self.assertIsNone(result)

    def test_stop_is_idle_waiting(self):
        result = hookstate.classify({"hook_event_name": "Stop"})
        self.assertEqual(result, (hookstate.WAITING, "idle"))

    def test_subagent_stop_is_ignored(self):
        self.assertIsNone(hookstate.classify({"hook_event_name": "SubagentStop"}))

    def test_unknown_event_is_ignored(self):
        self.assertIsNone(hookstate.classify({"hook_event_name": "Nonsense"}))

    def test_missing_event_name_is_ignored(self):
        self.assertIsNone(hookstate.classify({}))
