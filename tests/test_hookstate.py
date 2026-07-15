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

    def test_post_tool_use_is_working(self):
        # A finished tool means Claude resumed -- this un-sticks the red "input"
        # dot after the user answers a permission prompt / question.
        result = hookstate.classify(
            {"hook_event_name": "PostToolUse", "tool_name": "Bash"}
        )
        self.assertEqual(result, (hookstate.WORKING, ""))

    def test_post_tool_use_for_a_waiting_tool_is_also_working(self):
        # Answering an AskUserQuestion fires its PostToolUse; that is exactly the
        # resume signal, so it must read as WORKING, not stay WAITING.
        result = hookstate.classify(
            {"hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion"}
        )
        self.assertEqual(result, (hookstate.WORKING, ""))

    def test_stop_is_idle_waiting(self):
        result = hookstate.classify({"hook_event_name": "Stop"})
        self.assertEqual(result, (hookstate.WAITING, hookstate.STOP_IDLE))

    def test_idle_prompt_notification_reads_as_reported_not_input(self):
        # idle_prompt fires after an idle timeout -- Claude finished its turn and
        # is waiting at the prompt, NOT blocking on a choice. It must read GREEN
        # (reported), like a Stop; otherwise the idle nudge turns a finished
        # session red. (Enum from the Claude Code binary.)
        result = hookstate.classify(
            {"hook_event_name": "Notification", "notification_type": "idle_prompt"})
        self.assertEqual(result, (hookstate.WAITING, hookstate.STOP_IDLE))

    def test_agent_team_attention_preserves_its_reason_for_reconciliation(self):
        # Opening Claude's agent-team view can emit this notification for a
        # teammate card. Preserve the reason so the model can combine it with a
        # live title spinner; the model still prevents it from appearing red.
        result = hookstate.classify({
            "hook_event_name": "Notification",
            "notification_type": "agent_needs_input",
        })
        self.assertEqual(
            result, (hookstate.WAITING, hookstate.AGENT_NEEDS_INPUT))

    def test_permission_prompt_notification_still_reads_as_input(self):
        result = hookstate.classify(
            {"hook_event_name": "Notification", "notification_type": "permission_prompt"})
        self.assertEqual(result, (hookstate.WAITING, "permission_prompt"))
        self.assertNotEqual(result[1], hookstate.STOP_IDLE)  # stays red

    def test_notification_cannot_shadow_the_stop_idle_reason(self):
        # A Notification typed "idle" must not read as Stop's reserved reason
        # (which the UI paints green as "reported, not blocking") -- a
        # notification always means "wants attention".
        result = hookstate.classify(
            {"hook_event_name": "Notification", "notification_type": hookstate.STOP_IDLE})
        self.assertEqual(result, (hookstate.WAITING, "notification"))
        self.assertNotEqual(result[1], hookstate.STOP_IDLE)

    def test_codex_permission_request_is_not_proof_of_user_input(self):
        # This policy hook also fires when an automatic reviewer approves the
        # operation. Its payload contains no final routing/decision field.
        self.assertIsNone(
            hookstate.classify({"hook_event_name": "PermissionRequest"}))

    def test_codex_request_user_input_waits(self):
        self.assertEqual(
            hookstate.classify({
                "hook_event_name": "PreToolUse",
                "tool_name": "request_user_input",
            }),
            (hookstate.WAITING, "question"),
        )

    def test_subagent_events_carry_no_main_state(self):
        # SubagentStart/Stop drive the separate running-subagent count (see
        # hook.build_record); they never change the MAIN agent's state, so a red
        # "input" wait persists while a subagent runs instead of being cleared.
        self.assertIsNone(hookstate.classify({"hook_event_name": "SubagentStart"}))
        self.assertIsNone(hookstate.classify({"hook_event_name": "SubagentStop"}))

    def test_unknown_event_is_ignored(self):
        self.assertIsNone(hookstate.classify({"hook_event_name": "Nonsense"}))

    def test_missing_event_name_is_ignored(self):
        self.assertIsNone(hookstate.classify({}))
