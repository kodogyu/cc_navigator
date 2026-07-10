import os
import unittest

from ccnav import hookstate, model, ui


def row(state=hookstate.WAITING, reason="permission_prompt", message="Allow npm test?",
        cwd="/data/projects/demo_project"):
    return model.Row(
        session_id="a", socket="/tmp/s", pane="%1", tmux_session="demo",
        title="✳ 작업 중", state=state, reason=reason,
        message=message, cwd=cwd, updated_at=1,
    )


class SecondaryLineTest(unittest.TestCase):
    def test_waiting_shows_reason_and_message(self):
        self.assertEqual(
            ui.secondary_line(row()), "permission_prompt — Allow npm test?"
        )

    def test_waiting_without_message_shows_only_reason(self):
        self.assertEqual(ui.secondary_line(row(message="")), "permission_prompt")

    def test_waiting_without_reason_or_message_is_empty(self):
        self.assertEqual(ui.secondary_line(row(reason="", message="")), "")

    def test_working_shows_the_project_directory_name(self):
        self.assertEqual(
            ui.secondary_line(row(state=hookstate.WORKING)), "demo_project"
        )

    def test_working_tolerates_a_trailing_slash(self):
        self.assertEqual(
            ui.secondary_line(row(state=hookstate.WORKING, cwd="/a/b/")), "b"
        )

    def test_working_without_cwd_is_empty(self):
        self.assertEqual(ui.secondary_line(row(state=hookstate.WORKING, cwd="")), "")

    def test_long_secondary_line_is_truncated(self):
        line = ui.secondary_line(row(message="x" * 500))
        self.assertLessEqual(len(line), ui.SECONDARY_LIMIT)


class ComposeStatusTest(unittest.TestCase):
    def test_all_three_slots_are_shown(self):
        self.assertEqual(ui.compose_status("a", "b", "c"), "a  b  c")

    def test_the_eval_warning_survives_an_empty_list(self):
        # A jump failure must never hide the fact that Eval is unavailable.
        self.assertEqual(ui.compose_status("eval off", "no sessions", ""), "eval off  no sessions")

    def test_empty_slots_are_dropped(self):
        self.assertEqual(ui.compose_status("", "", "c"), "c")

    def test_all_empty_is_empty(self):
        self.assertEqual(ui.compose_status("", "", ""), "")


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs an X11 display")
class NavigatorWindowTest(unittest.TestCase):
    def test_constructs_and_accepts_rows(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        window.set_rows([row(), row(state=hookstate.WORKING)])
        window.set_status("hello")
        window.set_eval_available(False)
        # destroy must not call Gtk.main_quit: no main loop is running here.
        window.destroy()

    def test_status_slots_do_not_clobber_each_other(self):
        # Reaching into ._status and ._listbox is touching privates. That is
        # acceptable here because the alternative is a widget with no
        # behavioural test at all -- see Task 9 brief, Step 5.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_eval_available(False)
            window.set_rows([])
            text = window._status.get_text()
            self.assertIn(ui.EVAL_UNAVAILABLE_HINT, text)
            self.assertIn(ui.EMPTY_HINT, text)

            window.set_rows([row()])
            text = window._status.get_text()
            self.assertNotIn(ui.EMPTY_HINT, text)
            self.assertIn(ui.EVAL_UNAVAILABLE_HINT, text)
        finally:
            window.destroy()

    def test_set_rows_populates_the_listbox_with_one_child_per_row(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(), row(state=hookstate.WORKING), row()])
            self.assertEqual(len(window._listbox.get_children()), 3)
        finally:
            window.destroy()

    def test_destroy_does_not_touch_a_main_loop(self):
        # If NavigatorWindow ever reconnects destroy to Gtk.main_quit, GTK
        # prints "gtk_main_quit: assertion 'main_loops != NULL' failed" to
        # the real fd 2 (C-level stderr) -- Python's warnings filters never
        # see it. Capture fd 2 directly to prove destroy() is silent.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)

        stderr_fd = 2
        saved_fd = os.dup(stderr_fd)
        tmp_path = None
        try:
            import tempfile
            tmp = tempfile.TemporaryFile()
            tmp_path = tmp
            os.dup2(tmp.fileno(), stderr_fd)

            window.destroy()

            import sys
            sys.stderr.flush()
            os.dup2(saved_fd, stderr_fd)

            tmp.seek(0)
            captured = tmp.read()
            self.assertEqual(captured, b"")
        finally:
            os.dup2(saved_fd, stderr_fd)
            os.close(saved_fd)
            if tmp_path is not None:
                tmp_path.close()
