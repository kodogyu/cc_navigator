import unittest

from ccnav import hookstate, model, notify


def row(session_id="a", state=hookstate.WAITING, reason="permission_prompt",
        message="Allow npm test?", last_prompt="", title="t-a", provider="claude",
        provisional=False):
    return model.Row(
        session_id=session_id, socket="/tmp/s", pane="%1", tmux_session="demo",
        title=title, state=state, reason=reason, message=message,
        cwd="/proj", updated_at=1, last_prompt=last_prompt, provider=provider,
        provisional=provisional,
    )


def input_row(**kw):
    kw.setdefault("reason", "permission_prompt")
    return row(state=hookstate.WAITING, **kw)


def reported_row(**kw):
    kw.setdefault("reason", hookstate.STOP_IDLE)
    kw.setdefault("message", "")
    return row(state=hookstate.WAITING, **kw)


def working_row(**kw):
    return row(state=hookstate.WORKING, reason="", message="", **kw)


class ChangedRowsTest(unittest.TestCase):
    def test_a_transition_into_input_fires(self):
        fires, new_map = notify.changed_rows({"a": model.WORKING_SECTION}, [input_row()])
        self.assertEqual([(r.session_id, s) for r, s in fires], [("a", model.INPUT_NEEDED)])
        self.assertEqual(new_map, {"a": model.INPUT_NEEDED})

    def test_an_unchanged_status_is_silent(self):
        fires, new_map = notify.changed_rows({"a": model.INPUT_NEEDED}, [input_row()])
        self.assertEqual(fires, [])
        self.assertEqual(new_map, {"a": model.INPUT_NEEDED})

    def test_a_newly_seen_reported_session_fires(self):
        fires, _new = notify.changed_rows({}, [reported_row(session_id="b")])
        self.assertEqual([(r.session_id, s) for r, s in fires], [("b", model.REPORTED)])

    def test_a_provisional_input_ready_codex_pane_does_not_fake_completion(self):
        fires, new_map = notify.changed_rows(
            {}, [reported_row(session_id="p", provider="codex", provisional=True)])
        self.assertEqual(fires, [])
        self.assertEqual(new_map, {"p": model.REPORTED})

    def test_a_transition_into_working_never_fires(self):
        fires, new_map = notify.changed_rows({"a": model.INPUT_NEEDED}, [working_row()])
        self.assertEqual(fires, [])
        self.assertEqual(new_map, {"a": model.WORKING_SECTION})

    def test_a_vanished_session_drops_from_the_map(self):
        prev = {"a": model.INPUT_NEEDED, "b": model.REPORTED}
        _fires, new_map = notify.changed_rows(prev, [input_row(session_id="a")])
        self.assertEqual(new_map, {"a": model.INPUT_NEEDED})


class NotificationForTest(unittest.TestCase):
    def test_input_uses_a_red_glyph_status_name_and_the_message(self):
        n = notify.notification_for(input_row(message="Allow rm -rf?"), model.INPUT_NEEDED)
        self.assertEqual(n.summary, "🔴 t-a")
        self.assertEqual(n.body, "입력 필요 — Allow rm -rf?")

    def test_reported_uses_a_green_glyph_and_falls_back_to_last_prompt(self):
        # The hook blanks message for a reported/idle session, so last_prompt is
        # the only context left.
        n = notify.notification_for(
            reported_row(message="", last_prompt="refactor the parser"), model.REPORTED)
        self.assertEqual(n.summary, "🟢 t-a")
        self.assertEqual(n.body, "보고 완료 — refactor the parser")

    def test_no_detail_leaves_just_the_status_name(self):
        n = notify.notification_for(reported_row(message="", last_prompt=""), model.REPORTED)
        self.assertEqual(n.body, "보고 완료")

    def test_codex_notification_names_the_provider(self):
        n = notify.notification_for(
            input_row(provider="codex"), model.INPUT_NEEDED)
        self.assertEqual(n.summary, "🔴 Codex · t-a")

    def test_a_long_detail_is_truncated(self):
        n = notify.notification_for(input_row(message="x" * 500), model.INPUT_NEEDED)
        self.assertLessEqual(len(n.body), 140)
        self.assertTrue(n.body.endswith("…"))


class BuildArgvTest(unittest.TestCase):
    def test_argv_carries_app_name_summary_and_body(self):
        argv = notify.build_argv(notify.Notification("🔴 t-a", "입력 필요 — hi"), icon=None)
        self.assertEqual(argv[0], "notify-send")
        self.assertIn("-a", argv)
        self.assertIn("cc_navigator", argv)
        self.assertEqual(argv[-2:], ["🔴 t-a", "입력 필요 — hi"])
        self.assertNotIn("-i", argv)

    def test_an_icon_is_passed_with_dash_i(self):
        argv = notify.build_argv(notify.Notification("s", "b"), icon="/tmp/x.png")
        self.assertIn("-i", argv)
        self.assertIn("/tmp/x.png", argv)


class SendTest(unittest.TestCase):
    def test_send_invokes_the_runner_with_a_notify_send_argv(self):
        captured = []
        notify.send(reported_row(last_prompt="done"), model.REPORTED,
                    run=lambda argv: captured.append(list(argv)) or (0, ""),
                    icon=None)
        self.assertEqual(len(captured), 1)
        argv = captured[0]
        self.assertEqual(argv[0], "notify-send")
        self.assertIn("🟢 t-a", argv)
