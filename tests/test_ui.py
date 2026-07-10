import os
import unittest

from ccnav import hookstate, model, ui


def row(state=hookstate.WAITING, reason="permission_prompt", message="Allow npm test?",
        cwd="/data/projects/demo_project", session_id="a", title="✳ 작업 중"):
    return model.Row(
        session_id=session_id, socket="/tmp/s", pane="%1", tmux_session="demo",
        title=title, state=state, reason=reason,
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


class PrimaryLineTest(unittest.TestCase):
    def test_a_real_title_passes_through(self):
        self.assertEqual(
            ui.primary_line(row(title="✳ 작업 중"), hostname="myhost"), "✳ 작업 중"
        )

    def test_empty_title_falls_back_to_the_tmux_session(self):
        self.assertEqual(ui.primary_line(row(title=""), hostname="myhost"), "demo")

    def test_a_pane_id_title_falls_back_to_the_tmux_session(self):
        # row()'s pane is "%1".
        self.assertEqual(ui.primary_line(row(title="%1"), hostname="myhost"), "demo")

    def test_a_bare_hostname_title_falls_back_to_the_tmux_session(self):
        self.assertEqual(ui.primary_line(row(title="myhost"), hostname="myhost"), "demo")

    def test_the_hostname_first_component_falls_back(self):
        # tmux reports "myhost" while the FQDN is "myhost.local".
        self.assertEqual(
            ui.primary_line(row(title="myhost"), hostname="myhost.local"), "demo"
        )

    def test_the_full_fqdn_title_falls_back(self):
        self.assertEqual(
            ui.primary_line(row(title="myhost.local"), hostname="myhost.local"), "demo"
        )


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

    def test_identical_rows_reuse_the_same_entry_and_keep_its_text(self):
        # Task 10's one-second timer calls set_rows with unchanged rows. If it
        # rebuilds anyway, the Gtk.Entry the user is typing into is destroyed and
        # the text is silently lost. Same rows -> same Entry object, text intact.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row()])
            entry_before = window._listbox.get_children()[0].ccnav_entry
            entry_before.set_text("please approve this")

            window.set_rows([row()])  # identical signature -> no rebuild

            entry_after = window._listbox.get_children()[0].ccnav_entry
            self.assertIs(entry_after, entry_before)
            self.assertEqual(entry_after.get_text(), "please approve this")
        finally:
            window.destroy()

    def test_a_rebuild_preserves_a_still_present_session_the_user_was_typing_in(self):
        # A DIFFERENT session changing forces a real rebuild. The selection, the
        # revealed input, and the half-typed text of the session the user is in
        # must survive if that session is still present.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a"), row(session_id="b")])
            target = window._listbox.get_children()[0]
            self.assertEqual(target.ccnav_row.session_id, "a")
            window._listbox.select_row(target)
            target.ccnav_entry.set_text("please approve this")
            self.assertTrue(target.ccnav_revealer.get_reveal_child())

            # Session b changes; a is untouched but every widget is rebuilt.
            window.set_rows(
                [row(session_id="a"), row(session_id="b", message="something else")]
            )

            survivors = [
                c for c in window._listbox.get_children()
                if c.ccnav_row.session_id == "a"
            ]
            self.assertEqual(len(survivors), 1)
            new_a = survivors[0]
            self.assertEqual(new_a.ccnav_entry.get_text(), "please approve this")
            self.assertIs(window._listbox.get_selected_row(), new_a)
            self.assertTrue(new_a.ccnav_revealer.get_reveal_child())
        finally:
            window.destroy()

    def test_a_rebuild_restores_nothing_for_a_session_that_vanished(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a")])
            target = window._listbox.get_children()[0]
            window._listbox.select_row(target)
            target.ccnav_entry.set_text("please approve this")

            # Session a is gone, replaced by b.
            window.set_rows([row(session_id="b")])

            child = window._listbox.get_children()[0]
            self.assertEqual(child.ccnav_row.session_id, "b")
            self.assertEqual(child.ccnav_entry.get_text(), "")
            self.assertIsNone(window._listbox.get_selected_row())
        finally:
            window.destroy()

    def test_a_late_set_eval_available_reaches_existing_jump_buttons(self):
        # set_eval_available(False) after set_rows must disable the buttons that
        # already exist, not just future ones. Otherwise the user clicks a live
        # jump button and nothing happens, with no explanation.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a"), row(state=hookstate.WORKING,
                                                       session_id="b")])
            for child in window._listbox.get_children():
                self.assertTrue(child.ccnav_jump.get_sensitive())

            window.set_eval_available(False)

            for child in window._listbox.get_children():
                self.assertFalse(child.ccnav_jump.get_sensitive())
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
