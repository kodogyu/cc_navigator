import unittest

from ccnav import gnome

ACTIVE_WINDOW_OUT = "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x3ce416e, 0x0\n"
WM_NAME_OUT = '_NET_WM_NAME(UTF8_STRING) = "ccnav:demo"\n'


class EscapeJsTest(unittest.TestCase):
    def test_escapes_quote_and_backslash(self):
        self.assertEqual(gnome.escape_js("a'b\\c"), "a\\'b\\\\c")

    def test_escapes_newlines(self):
        self.assertEqual(gnome.escape_js("a\nb"), "a\\nb")


class ActivateJsTest(unittest.TestCase):
    def test_compares_titles_with_strict_equality(self):
        js = gnome.activate_js("ccnav:demo")
        self.assertIn("==='ccnav:demo'", js.replace(" ", ""))

    def test_activates_only_the_first_match(self):
        js = gnome.activate_js("ccnav:demo").replace(" ", "")
        self.assertIn("if(!found)found=w", js)
        self.assertIn("Main.activateWindow(found)", js)

    def test_reports_how_many_windows_matched(self):
        self.assertIn("'matched='+n", gnome.activate_js("ccnav:demo"))

    def test_retry_variant_uses_a_roundtrip_timestamp(self):
        js = gnome.activate_ts_js("ccnav:demo")
        self.assertIn("get_current_time_roundtrip()", js)
        self.assertNotIn("activate(0)", js)

    def test_title_with_a_quote_cannot_break_out(self):
        js = gnome.activate_js("ccnav:it's")
        self.assertIn("ccnav:it\\'s", js)


class ParseEvalResultTest(unittest.TestCase):
    def test_true_prefix_is_success(self):
        ok, raw = gnome.parse_eval_result("(true, '\"matched=1\"')\n")
        self.assertTrue(ok)
        self.assertIn("matched=1", raw)

    def test_false_prefix_is_failure_even_though_gdbus_exits_zero(self):
        ok, _ = gnome.parse_eval_result("(false, 'ReferenceError: Shell')\n")
        self.assertFalse(ok)


class ParseMatchCountTest(unittest.TestCase):
    def test_extracts_the_count(self):
        self.assertEqual(gnome.parse_match_count("(true, '\"matched=2\"')\n"), 2)

    def test_absent_count_is_zero(self):
        self.assertEqual(gnome.parse_match_count("(false, 'boom')\n"), 0)


class EvalJsTest(unittest.TestCase):
    def test_nonzero_exit_is_failure_even_when_stdout_looks_successful(self):
        # The stdout of a process that failed is not evidence. Without the
        # exit-code guard, a gdbus that died after printing would be believed.
        ok, _ = gnome.eval_js("1+1", run=lambda argv: (1, "(true, '\"matched=1\"')\n"))
        self.assertFalse(ok)

    def test_zero_exit_with_a_false_result_is_failure(self):
        ok, _ = gnome.eval_js("1+1", run=lambda argv: (0, "(false, 'boom')\n"))
        self.assertFalse(ok)

    def test_the_js_travels_as_the_last_argv_element(self):
        seen = []

        def fake_run(argv):
            seen.append(list(argv))
            return 0, "(true, '2')\n"

        gnome.eval_js("1+1", run=fake_run)
        self.assertEqual(seen[0][0], "gdbus")
        self.assertEqual(seen[0][-1], "1+1")


class EvalAvailableTest(unittest.TestCase):
    def test_detects_a_working_eval(self):
        self.assertTrue(gnome.eval_available(run=lambda argv: (0, "(true, '2')\n")))

    def test_detects_a_blocked_eval(self):
        self.assertFalse(gnome.eval_available(run=lambda argv: (0, "(false, '')\n")))

    def test_detects_a_missing_gdbus(self):
        self.assertFalse(gnome.eval_available(run=lambda argv: (127, "")))


class ActiveWindowTitleTest(unittest.TestCase):
    def _run(self, argv):
        if argv[1] == "-root":
            return 0, ACTIVE_WINDOW_OUT
        return 0, WM_NAME_OUT

    def test_reads_the_focused_window_title_through_xprop(self):
        self.assertEqual(gnome.active_window_title(run=self._run), "ccnav:demo")

    def test_missing_active_window_is_none(self):
        self.assertIsNone(gnome.active_window_title(run=lambda argv: (1, "")))


class ActivateWindowTitledTest(unittest.TestCase):
    def test_succeeds_when_focus_actually_moved(self):
        calls = []

        def fake_run(argv):
            calls.append(argv[0])
            if argv[0] == "gdbus":
                return 0, "(true, '\"matched=1\"')\n"
            if argv[1] == "-root":
                return 0, ACTIVE_WINDOW_OUT
            return 0, WM_NAME_OUT

        result = gnome.activate_window_titled(
            "ccnav:demo", run=fake_run, sleep=lambda s: None
        )
        self.assertTrue(result.ok)
        self.assertEqual(calls.count("gdbus"), 1)

    def test_reports_the_number_of_matching_windows(self):
        def fake_run(argv):
            if argv[0] == "gdbus":
                return 0, "(true, '\"matched=2\"')\n"
            if argv[1] == "-root":
                return 0, ACTIVE_WINDOW_OUT
            return 0, WM_NAME_OUT

        result = gnome.activate_window_titled(
            "ccnav:demo", run=fake_run, sleep=lambda s: None
        )
        self.assertEqual(result.matched, 2)

    def test_retries_with_a_timestamp_when_eval_lied(self):
        # Eval reports success but focus never moves: exactly the activate(0) bug.
        seen_js = []
        state = {"focused": "something-else"}

        def fake_run(argv):
            if argv[0] == "gdbus":
                seen_js.append(argv[-1])
                if "get_current_time_roundtrip" in argv[-1]:
                    state["focused"] = "ccnav:demo"
                return 0, "(true, '\"matched=1\"')\n"
            if argv[1] == "-root":
                return 0, ACTIVE_WINDOW_OUT
            return 0, '_NET_WM_NAME(UTF8_STRING) = "%s"\n' % state["focused"]

        result = gnome.activate_window_titled(
            "ccnav:demo", run=fake_run, sleep=lambda s: None, timeout=0.0
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(seen_js), 2)

    def test_reports_failure_when_focus_never_moves(self):
        def fake_run(argv):
            if argv[0] == "gdbus":
                return 0, "(true, '\"matched=1\"')\n"
            if argv[1] == "-root":
                return 0, ACTIVE_WINDOW_OUT
            return 0, '_NET_WM_NAME(UTF8_STRING) = "other"\n'

        result = gnome.activate_window_titled(
            "ccnav:demo", run=fake_run, sleep=lambda s: None, timeout=0.0
        )
        self.assertFalse(result.ok)
