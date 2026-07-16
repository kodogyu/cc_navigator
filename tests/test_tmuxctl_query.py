import unittest

from ccnav import tmuxctl


class ParseKvLinesTest(unittest.TestCase):
    def test_splits_on_the_first_equals_only(self):
        # pane_title is arbitrary text and may contain '='.
        parsed = tmuxctl.parse_kv_lines("%1=a=b=c\n")
        self.assertEqual(parsed, {"%1": "a=b=c"})

    def test_title_may_contain_pipes_and_spaces(self):
        parsed = tmuxctl.parse_kv_lines("%2=make test | tee log\n")
        self.assertEqual(parsed, {"%2": "make test | tee log"})

    def test_utf8_title_survives(self):
        parsed = tmuxctl.parse_kv_lines("%3=✳ 작업 중 (X)\n")
        self.assertEqual(parsed, {"%3": "✳ 작업 중 (X)"})

    def test_empty_title_is_empty_string(self):
        self.assertEqual(tmuxctl.parse_kv_lines("%4=\n"), {"%4": ""})

    def test_blank_and_malformed_lines_are_skipped(self):
        parsed = tmuxctl.parse_kv_lines("\n%5=ok\ngarbage\n\n")
        self.assertEqual(parsed, {"%5": "ok"})

    def test_empty_input(self):
        self.assertEqual(tmuxctl.parse_kv_lines(""), {})


class QueryTest(unittest.TestCase):
    def test_list_argv_uses_explicit_socket(self):
        argv = tmuxctl.list_argv("/tmp/s", "#{pane_id}=#{session_name}")
        self.assertEqual(
            argv,
            ["tmux", "-S", "/tmp/s", "list-panes", "-a", "-F",
             "#{pane_id}=#{session_name}"],
        )

    def test_sessions_by_pane(self):
        calls = []

        def fake_run(argv):
            calls.append(list(argv))
            return 0, "%0=demo\n%1=sandbox\n"

        result = tmuxctl.sessions_by_pane("/tmp/s", run=fake_run)
        self.assertEqual(result, {"%0": "demo", "%1": "sandbox"})
        self.assertEqual(calls[0][-1], "#{pane_id}=#{session_name}")

    def test_titles_by_pane(self):
        calls = []

        def fake_run(argv):
            calls.append(list(argv))
            return 0, "%0=✳ 작업 중 (demo-project)\n"

        result = tmuxctl.titles_by_pane("/tmp/s", run=fake_run)
        self.assertEqual(
            result, {"%0": "✳ 작업 중 (demo-project)"}
        )
        self.assertEqual(calls[0][-1], "#{pane_id}=#{pane_title}")

    def test_pane_processes_carry_pid_and_foreground_command(self):
        calls = []

        def fake_run(argv):
            calls.append(list(argv))
            return 0, "%0=123\tnode\n%1=456\tbash\n"

        result = tmuxctl.pane_processes_by_pane("/tmp/s", run=fake_run)
        self.assertEqual(result["%0"], tmuxctl.PaneProcess(123, "node"))
        self.assertEqual(result["%1"], tmuxctl.PaneProcess(456, "bash"))
        self.assertEqual(
            calls[0][-1], "#{pane_id}=#{pane_pid}\t#{pane_current_command}")

    def test_pane_processes_skip_malformed_pids(self):
        result = tmuxctl.pane_processes_by_pane(
            "/tmp/s", run=lambda a: (0, "%0=nope\tnode\n%1=0\tnode\n"))
        self.assertEqual(result, {})

    def test_pane_for_tty_maps_only_an_exact_pts_device(self):
        calls = []

        def fake_run(argv):
            calls.append(list(argv))
            return 0, "%30=/dev/pts/34\n%1=/dev/pts/3\n"

        self.assertEqual(
            tmuxctl.pane_for_tty("/tmp/s", "/dev/pts/3", run=fake_run), "%1")
        self.assertEqual(calls[0][-1], "#{pane_id}=#{pane_tty}")

    def test_pane_for_tty_rejects_unbounded_or_unknown_targets(self):
        never = lambda argv: (_ for _ in ()).throw(AssertionError("must not run"))
        self.assertEqual(tmuxctl.pane_for_tty("/tmp/s", "/private/path", run=never), "")
        self.assertEqual(
            tmuxctl.pane_for_tty(
                "/tmp/s", "/dev/pts/9", run=lambda argv: (0, "%1=/dev/pts/3\n")),
            "",
        )

    def test_nonzero_exit_yields_empty_dict_even_with_output(self):
        # A dead tmux socket may still print a line to stdout. The exit-code
        # guard is what makes "no server" mean "no panes"; without it a dead
        # socket would read as "every session vanished" and prune live state.
        def fake_run(argv):
            return 1, "%0=zombie\n"

        self.assertEqual(tmuxctl.sessions_by_pane("/tmp/s", run=fake_run), {})
        self.assertEqual(tmuxctl.titles_by_pane("/tmp/s", run=fake_run), {})


class SessionsByPaneResultTest(unittest.TestCase):
    """F3: an empty pane dict is ambiguous -- 'no sessions' or 'query failed'.
    collect_rows must be able to tell them apart or it prunes live state on a
    stuttering tmux. The _result variant reports whether tmux actually answered.
    """

    def test_success_reports_ok_and_the_panes(self):
        ok, panes = tmuxctl.sessions_by_pane_result(
            "/tmp/s", run=lambda a: (0, "%0=demo\n")
        )
        self.assertTrue(ok)
        self.assertEqual(panes, {"%0": "demo"})

    def test_success_with_no_panes_is_still_ok(self):
        # tmux exit 0 with empty output is not reachable in practice (an empty
        # server exits), but if it were, "ok and empty" must not read as failure.
        ok, panes = tmuxctl.sessions_by_pane_result("/tmp/s", run=lambda a: (0, ""))
        self.assertTrue(ok)
        self.assertEqual(panes, {})

    def test_nonzero_exit_reports_not_ok(self):
        ok, panes = tmuxctl.sessions_by_pane_result(
            "/tmp/s", run=lambda a: (1, "%0=zombie\n")
        )
        self.assertFalse(ok)
        self.assertEqual(panes, {})

    def test_timeout_status_reports_not_ok(self):
        # proc.run_command returns (124, "") on TimeoutExpired: a slow, not dead,
        # tmux. That must read as "could not observe", never as "no sessions".
        ok, _ = tmuxctl.sessions_by_pane_result("/tmp/s", run=lambda a: (124, ""))
        self.assertFalse(ok)

    def test_plain_wrapper_still_returns_just_the_dict(self):
        self.assertEqual(
            tmuxctl.sessions_by_pane("/tmp/s", run=lambda a: (0, "%0=demo\n")),
            {"%0": "demo"},
        )
