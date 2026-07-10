import unittest

from ccnav import tmuxctl

HOSTILE = "yes; echo 'x' \"y\" $HOME \\ 한글 ✳ Enter C-c"


class SelectArgvsTest(unittest.TestCase):
    def test_select_window_then_select_pane(self):
        argvs = tmuxctl.select_argvs("/tmp/s", "%12")
        self.assertEqual(
            argvs,
            [
                ["tmux", "-S", "/tmp/s", "select-window", "-t", "%12"],
                ["tmux", "-S", "/tmp/s", "select-pane", "-t", "%12"],
            ],
        )

    def test_never_switches_client_across_sessions(self):
        # switch-client is the only command that can move a client to another
        # session; on a shared socket a jump would then hijack an unrelated
        # terminal. The jump path must never use it.
        for argv in tmuxctl.select_argvs("/tmp/s", "%12"):
            self.assertNotIn("switch-client", argv)

    def test_every_target_is_the_requested_pane_only(self):
        # Session-scoped safety: every argv addresses exactly the pane we were
        # asked for, so no other session's client can be affected.
        for argv in tmuxctl.select_argvs("/tmp/s", "%12"):
            self.assertEqual(argv[argv.index("-t") + 1], "%12")

    def test_select_pane_runs_every_argv_even_if_one_fails(self):
        seen = []

        def fake_run(argv):
            seen.append(list(argv))
            return (1, "") if "select-window" in argv else (0, "")

        tmuxctl.select_pane("/tmp/s", "%12", run=fake_run)
        self.assertEqual(len(seen), 2)


class SendTextArgvsTest(unittest.TestCase):
    def test_uses_literal_flag_and_double_dash(self):
        argvs = tmuxctl.send_text_argvs("/tmp/s", "%12", "hello")
        self.assertEqual(
            argvs,
            [
                ["tmux", "-S", "/tmp/s", "send-keys", "-t", "%12", "-l", "--", "hello"],
                ["tmux", "-S", "/tmp/s", "send-keys", "-t", "%12", "Enter"],
            ],
        )

    def test_hostile_text_is_a_single_argv_element(self):
        argvs = tmuxctl.send_text_argvs("/tmp/s", "%12", HOSTILE)
        self.assertEqual(argvs[0][-1], HOSTILE)
        self.assertEqual(len(argvs[0]), 9)

    def test_text_starting_with_dash_is_protected_by_double_dash(self):
        argvs = tmuxctl.send_text_argvs("/tmp/s", "%12", "-n --flag")
        self.assertEqual(argvs[0][-2], "--")
        self.assertEqual(argvs[0][-1], "-n --flag")

    def test_enter_is_sent_as_a_separate_named_key(self):
        argvs = tmuxctl.send_text_argvs("/tmp/s", "%12", "Enter")
        # The word "Enter" as user text must be literal, not a keypress.
        self.assertIn("-l", argvs[0])
        self.assertEqual(argvs[0][-1], "Enter")
        self.assertNotIn("-l", argvs[1])

    def test_send_text_runs_both_argvs_in_order(self):
        seen = []

        def fake_run(argv):
            seen.append(list(argv))
            return (0, "")

        tmuxctl.send_text("/tmp/s", "%12", "hello", run=fake_run)
        self.assertEqual(seen, tmuxctl.send_text_argvs("/tmp/s", "%12", "hello"))


class SendTextResultTest(unittest.TestCase):
    """F2: send_text discarded exit codes, so a reply that never reached a dead
    server was reported as success. It must now say what actually happened."""

    def test_both_ok_is_delivered_and_submitted(self):
        result = tmuxctl.send_text("/tmp/s", "%12", "hi", run=lambda a: (0, ""))
        self.assertTrue(result.delivered)
        self.assertTrue(result.submitted)
        self.assertTrue(result.ok)

    def test_literal_failure_is_not_delivered_and_does_not_press_enter(self):
        seen = []

        def fake_run(argv):
            seen.append(list(argv))
            return (1, "")  # the literal send fails: the server just died

        result = tmuxctl.send_text("/tmp/s", "%12", "hi", run=fake_run)
        self.assertFalse(result.delivered)
        self.assertFalse(result.ok)
        # Enter must NOT be sent: submitting a bare newline into whatever is
        # there is worse than doing nothing.
        self.assertEqual(len(seen), 1)
        self.assertNotIn("Enter", seen[0])

    def test_literal_ok_but_enter_fails_is_delivered_not_submitted(self):
        def fake_run(argv):
            return (0, "") if "-l" in argv else (1, "")

        result = tmuxctl.send_text("/tmp/s", "%12", "hi", run=fake_run)
        self.assertTrue(result.delivered)
        self.assertFalse(result.submitted)
        self.assertFalse(result.ok)
