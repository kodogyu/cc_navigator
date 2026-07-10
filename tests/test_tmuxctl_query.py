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
        def fake_run(argv):
            return 0, "%0=✳ 작업 중 (demo-project)\n"

        result = tmuxctl.titles_by_pane("/tmp/s", run=fake_run)
        self.assertEqual(
            result, {"%0": "✳ 작업 중 (demo-project)"}
        )

    def test_no_tmux_server_yields_empty_dict(self):
        def fake_run(argv):
            return 1, ""

        self.assertEqual(tmuxctl.sessions_by_pane("/tmp/s", run=fake_run), {})
        self.assertEqual(tmuxctl.titles_by_pane("/tmp/s", run=fake_run), {})
