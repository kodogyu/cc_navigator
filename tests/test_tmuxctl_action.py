import unittest

from ccnav import tmuxctl

HOSTILE = "yes; echo 'x' \"y\" $HOME \\ 한글 ✳ Enter C-c"


class SelectArgvsTest(unittest.TestCase):
    def test_switch_then_select_window_then_select_pane(self):
        argvs = tmuxctl.select_argvs("/tmp/s", "%12")
        self.assertEqual(
            argvs,
            [
                ["tmux", "-S", "/tmp/s", "switch-client", "-t", "%12"],
                ["tmux", "-S", "/tmp/s", "select-window", "-t", "%12"],
                ["tmux", "-S", "/tmp/s", "select-pane", "-t", "%12"],
            ],
        )

    def test_select_pane_runs_every_argv_even_if_one_fails(self):
        seen = []

        def fake_run(argv):
            seen.append(list(argv))
            return (1, "") if "switch-client" in argv else (0, "")

        tmuxctl.select_pane("/tmp/s", "%12", run=fake_run)
        self.assertEqual(len(seen), 3)


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
