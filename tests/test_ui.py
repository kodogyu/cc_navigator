import os
import unittest

from ccnav import hookstate, model, ui

# ui pins the Gtk version on import above, so this bare import is safe here.
from gi.repository import Gtk  # noqa: E402


def row(state=hookstate.WAITING, reason="permission_prompt", message="Allow npm test?",
        cwd="/data/projects/demo_project", session_id="a", title="✳ 작업 중",
        last_prompt="", pane="%1", socket="/tmp/s", updated_at=1):
    return model.Row(
        session_id=session_id, socket=socket, pane=pane, tmux_session="demo",
        title=title, state=state, reason=reason,
        message=message, cwd=cwd, updated_at=updated_at, last_prompt=last_prompt,
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


class OnelineTest(unittest.TestCase):
    def test_collapses_newlines_and_tabs_to_single_spaces(self):
        self.assertEqual(ui._oneline("a\n\nb\tc  d"), "a b c d")

    def test_drops_non_whitespace_control_chars(self):
        # A NUL would otherwise truncate a Pango label at the byte; ESC garbles.
        self.assertEqual(ui._oneline("hello\x00world"), "helloworld")
        self.assertEqual(ui._oneline("\x1b[31mred\x1b[0m"), "[31mred[0m")

    def test_keeps_hangul_and_ordinary_text(self):
        self.assertEqual(ui._oneline("작업 중\n디렉터리"), "작업 중 디렉터리")

    def test_empty_stays_empty(self):
        self.assertEqual(ui._oneline(""), "")


class DotStateTest(unittest.TestCase):
    def test_working_state_is_working(self):
        self.assertEqual(ui.dot_state(row(state=hookstate.WORKING)), "working")

    def test_blocking_waiting_reasons_are_input(self):
        for reason in ("permission_prompt", "question", "plan", "elicitation_dialog"):
            self.assertEqual(
                ui.dot_state(row(state=hookstate.WAITING, reason=reason)), "input", reason)

    def test_idle_waiting_is_reported(self):
        self.assertEqual(
            ui.dot_state(row(state=hookstate.WAITING, reason="idle")), "reported")


class AppIconTest(unittest.TestCase):
    def test_icon_asset_loads_at_the_requested_size(self):
        pb = ui._app_icon_pixbuf(18)
        self.assertIsNotNone(pb)  # icons/window_icon.png is a committed asset
        self.assertLessEqual(pb.get_width(), 18)
        self.assertLessEqual(pb.get_height(), 18)


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

    def test_set_row_jump_sensitive_toggles_one_rows_button(self):
        # Added for Task 10: Application disables one row's jump button while
        # its activation is in flight, and must not disturb any other row.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a"), row(session_id="b")])
            children = {c.ccnav_row.session_id: c for c in window._listbox.get_children()}

            window.set_row_jump_sensitive("a", False)
            self.assertFalse(children["a"].ccnav_jump.get_sensitive())
            self.assertTrue(children["b"].ccnav_jump.get_sensitive())

            window.set_row_jump_sensitive("a", True)
            self.assertTrue(children["a"].ccnav_jump.get_sensitive())
        finally:
            window.destroy()

    def test_set_row_jump_sensitive_is_a_noop_for_a_vanished_session(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a")])
            window.set_row_jump_sensitive("gone", False)  # must not raise
            self.assertTrue(window._listbox.get_children()[0].ccnav_jump.get_sensitive())
        finally:
            window.destroy()

    def test_refresh_button_calls_back(self):
        called = []
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            on_refresh=lambda: called.append(True))
        try:
            window._refresh_button.clicked()
            self.assertEqual(called, [True])
        finally:
            window.destroy()

    def test_collapse_hides_content_and_expand_restores(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row()])
            self.assertTrue(window._content.get_visible())
            window.set_collapsed(True)
            self.assertFalse(window._content.get_visible())
            window.set_collapsed(False)
            self.assertTrue(window._content.get_visible())
        finally:
            window.destroy()

    @staticmethod
    def _labels_under(child):
        texts = []
        def walk(w):
            if isinstance(w, Gtk.Label):
                texts.append(w.get_text())
            if isinstance(w, Gtk.Container):
                for c in w.get_children():
                    walk(c)
        walk(child)
        return texts

    @staticmethod
    def _widgets_of_type(root, cls):
        found = []
        def walk(w):
            if isinstance(w, cls):
                found.append(w)
            if isinstance(w, Gtk.Container):
                for c in w.get_children():
                    walk(c)
        walk(root)
        return found

    def test_working_row_shows_a_rotating_arrow_and_no_waiting_text(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(state=hookstate.WORKING, session_id="a")])
            child = window._listbox.get_children()[0]
            arrows = [t for t in self._labels_under(child) if t in ui._WORKING_FRAMES]
            self.assertEqual(len(arrows), 1)  # exactly one rotating-arrow indicator
            self.assertEqual(len(self._widgets_of_type(child, Gtk.Spinner)), 0)  # no spinner
            for text in self._labels_under(child):
                self.assertNotIn("Waiting input", text)  # the old badge is gone
        finally:
            window.destroy()

    def test_waiting_row_shows_a_dot_not_an_arrow(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(state=hookstate.WAITING, reason="idle", session_id="a")])
            child = window._listbox.get_children()[0]
            texts = self._labels_under(child)
            self.assertIn("●", texts)  # a coloured dot
            self.assertEqual([t for t in texts if t in ui._WORKING_FRAMES], [])  # no arrow
        finally:
            window.destroy()

    def _dot_markup(self, child):
        for lbl in self._widgets_of_type(child, Gtk.Label):
            if "●" in lbl.get_label():
                return lbl.get_label()
        return ""

    def test_changing_one_row_keeps_every_rows_widget(self):
        # The flicker fix: set_rows reconciles in place, so a change to one
        # session must NOT destroy/rebuild any row's widgets -- unchanged rows
        # are untouched and the changed row keeps its widget identity.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a"), row(session_id="b")])
            a0, b0 = window._listbox.get_children()
            window.set_rows([row(session_id="a"),
                             row(session_id="b", state=hookstate.WORKING)])
            a1, b1 = window._listbox.get_children()
            self.assertIs(a1, a0)  # unchanged session: same widget, never rebuilt
            self.assertIs(b1, b0)  # changed session: same widget, updated in place
        finally:
            window.destroy()

    def test_dot_recolours_in_place_on_a_reason_change(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", state=hookstate.WAITING, reason="idle")])
            child = window._listbox.get_children()[0]
            self.assertIn("#2ec27e", self._dot_markup(child))  # green
            window.set_rows([row(session_id="a", state=hookstate.WAITING,
                                 reason="permission_prompt")])
            self.assertIs(window._listbox.get_children()[0], child)  # same widget
            self.assertIn("#e01b24", self._dot_markup(child))  # recoloured red in place
        finally:
            window.destroy()

    def test_state_flip_swaps_dot_for_arrow_in_place(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", state=hookstate.WAITING, reason="idle")])
            child = window._listbox.get_children()[0]
            self.assertIn("●", self._labels_under(child))  # a dot
            window.set_rows([row(session_id="a", state=hookstate.WORKING)])
            self.assertIs(window._listbox.get_children()[0], child)  # same row widget
            arrows = [t for t in self._labels_under(child) if t in ui._WORKING_FRAMES]
            self.assertEqual(len(arrows), 1)  # dot swapped for a rotating arrow
        finally:
            window.destroy()

    def test_gone_session_is_removed_and_new_one_added(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a"), row(session_id="b")])
            window.set_rows([row(session_id="b"), row(session_id="c")])
            ids = [c.ccnav_row.session_id for c in window._listbox.get_children()]
            self.assertEqual(ids, ["b", "c"])  # a removed, c inserted, order kept
        finally:
            window.destroy()

    def _display_ids(self, window):
        n = len(window._listbox.get_children())
        return [window._listbox.get_row_at_index(i).ccnav_row.session_id for i in range(n)]

    def test_a_row_that_becomes_waiting_jumps_above_working_rows(self):
        # The reconcile must keep the (waiting, -updated_at) priority sort live:
        # updating a row in place must re-sort, not leave it where it was.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", state=hookstate.WORKING, updated_at=90),
                             row(session_id="b", state=hookstate.WORKING, updated_at=100)])
            self.assertEqual(self._display_ids(window), ["b", "a"])  # newer working on top
            window.set_rows([
                row(session_id="a", state=hookstate.WAITING, reason="idle", updated_at=110),
                row(session_id="b", state=hookstate.WORKING, updated_at=100)])
            self.assertEqual(self._display_ids(window), ["a", "b"])  # a now waiting -> top
        finally:
            window.destroy()

    def test_swapping_arrow_for_dot_detaches_the_arrow_so_its_timer_stops(self):
        # The rotating arrow's timer self-stops when the label loses its ListBox
        # ancestor. After a working->waiting in-place swap the old arrow must be
        # detached (else the 8Hz timer leaks for the window's life).
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", state=hookstate.WORKING)])
            child = window._listbox.get_children()[0]
            arrow = [l for l in self._widgets_of_type(child, Gtk.Label)
                     if l.get_text() in ui._WORKING_FRAMES][0]
            self.assertIsNotNone(arrow.get_ancestor(Gtk.ListBox))  # live -> timer runs
            window.set_rows([row(session_id="a", state=hookstate.WAITING, reason="idle")])
            self.assertIsNone(arrow.get_ancestor(Gtk.ListBox))  # detached -> timer self-stops
        finally:
            window.destroy()

    def test_send_targets_the_current_row_after_an_in_place_update(self):
        # The entry handler must read the row's CURRENT data, not a Row captured
        # at build time (in-place update swaps ccnav_row).
        sent = []
        window = ui.NavigatorWindow(
            on_jump=lambda r: None,
            on_send=lambda r, t: sent.append((r.session_id, r.pane, t)))
        try:
            window.set_rows([row(session_id="a", pane="%1")])
            child = window._listbox.get_children()[0]
            window.set_rows([row(session_id="a", pane="%9")])  # pane changed -> update
            self.assertIs(window._listbox.get_children()[0], child)
            child.ccnav_entry.set_text("hello")
            child.ccnav_entry.emit("activate")
            self.assertEqual(sent, [("a", "%9", "hello")])  # current pane, not the stale %1
        finally:
            window.destroy()

    def test_reported_dot_is_green_and_input_dot_is_red(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([
                row(state=hookstate.WAITING, reason="idle", session_id="a"),
                row(state=hookstate.WAITING, reason="permission_prompt", session_id="b")])
            a, b = window._listbox.get_children()
            self.assertIn("#2ec27e", self._dot_markup(a))  # reported/idle -> green
            self.assertIn("#e01b24", self._dot_markup(b))  # blocking -> red
        finally:
            window.destroy()

    def test_missing_icon_degrades_to_a_name_only_title(self):
        # A missing/corrupt asset must not stop the panel opening; the header
        # builds with just the name, no icon, no crash.
        from unittest import mock
        with mock.patch("ccnav.ui._app_icon_pixbuf", return_value=None):
            window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            title = window.get_titlebar().get_custom_title()
            kinds = [type(c).__name__ for c in title.get_children()]
            self.assertNotIn("Image", kinds)  # no icon widget
            self.assertIn("Label", kinds)      # name still shown
        finally:
            window.destroy()

    @staticmethod
    def _click(window, child):
        # Reproduce a real single click's signal order: button-press captures the
        # prior selection, then GTK selects the row (row-selected reveals it),
        # then it activates it (row-activated). row-selected precedes row-activated.
        window._on_listbox_button_press(window._listbox, None)
        window._listbox.select_row(child)
        window._on_row_activated(window._listbox, child)

    def test_expanded_row_shows_the_last_prompt_and_path(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(cwd="/data/projects/demo", session_id="a",
                                 last_prompt="fix the parser bug")])
            child = window._listbox.get_children()[0]
            self._click(window, child)
            joined = " ".join(self._labels_under(child))
            self.assertIn("/data/projects/demo", joined)
            self.assertIn("fix the parser bug", joined)  # prompt actually rendered
        finally:
            window.destroy()

    def test_clicking_an_unselected_row_expands_it(self):
        # Regression guard: a first click must SELECT (expand), never collapse.
        # row-selected fires before row-activated, so a naive activated-handler
        # that compares against the live selection collapses every first click.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a"), row(session_id="b")])
            _a, b = window._listbox.get_children()
            window._listbox.unselect_all()
            self._click(window, b)
            self.assertIs(window._listbox.get_selected_row(), b)  # stays expanded
        finally:
            window.destroy()

    def test_reclicking_selected_row_collapses_it(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a")])
            child = window._listbox.get_children()[0]
            self._click(window, child)               # first click: expands
            self.assertIs(window._listbox.get_selected_row(), child)
            self._click(window, child)               # re-click: collapses
            self.assertIsNone(window._listbox.get_selected_row())
        finally:
            window.destroy()

    def test_detail_prompt_with_embedded_newlines_renders_on_one_line(self):
        # Defense in depth: even if a Row still carries a multi-line prompt (an
        # older/hand-edited record, e.g. a raw task-notification blob), no detail
        # label may render embedded newlines that break the row's layout.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            blob = "<task-notification>\n<task-id>x</task-id>\n<status>done</status>"
            window.set_rows([row(session_id="a", last_prompt=blob)])
            child = window._listbox.get_children()[0]
            self._click(window, child)
            labels = self._labels_under(child)
            for text in labels:
                self.assertNotIn("\n", text)  # nothing rendered multi-line
            joined = " ".join(labels)
            self.assertIn("<task-notification> <task-id>x</task-id>", joined)  # flattened, present
        finally:
            window.destroy()

    def test_detail_meta_flattens_a_newline_in_reason(self):
        # reason is a payload-controlled notification_type; a newline in it must
        # not render the meta/state line multi-line (same bug class as the prompt).
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", reason="perm\nission\nthree")])
            child = window._listbox.get_children()[0]
            self._click(window, child)
            for text in self._labels_under(child):
                self.assertNotIn("\n", text)  # no detail label rendered multi-line
        finally:
            window.destroy()

    def test_detail_labels_escape_pango_markup(self):
        # cwd/prompt are user-controlled; a '&' or '<' must not corrupt or crash
        # the Pango markup in the detail labels. get_text() returns the unescaped
        # text, so a round-trip proves the markup was well-formed.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(cwd="/a & b/<x>", session_id="a",
                                 last_prompt="grep 'a & b' <file>")])
            child = window._listbox.get_children()[0]
            self._click(window, child)
            joined = " ".join(self._labels_under(child))
            self.assertIn("/a & b/<x>", joined)
            self.assertIn("grep 'a & b' <file>", joined)
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


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs an X11 display")
class SettingsUiTest(unittest.TestCase):
    """The gear/dialog feature. Font CSS and the commit path are asserted; the
    X11-hint parts (keep-above, sticky, geometry) can only be requested, not
    reliably read back on an unmapped window, so apply_settings is exercised for
    'does not raise' and the parts we CAN observe (font, stored settings)."""

    def test_css_carries_font_and_background(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.Settings(font_size=15, bg_color="#123456"),
        )
        try:
            css = window._css.to_string()
            self.assertIn("15pt", css)
            # GTK's CssProvider re-serializes on to_string(): a hex color comes
            # back as rgb(r,g,b), not the original "#123456" literal, so we
            # assert the round-tripped decimal form rather than the hex text.
            self.assertIn("rgb(18,52,86)", css)
            self.assertIn("background-color", css)
            # Clearing both means an empty provider.
            window.apply_settings(config.Settings(font_size=0, bg_color=""))
            self.assertNotIn("pt", window._css.to_string())
            self.assertNotIn("background-color", window._css.to_string())
        finally:
            window.destroy()

    def test_opacity_is_applied(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.Settings(opacity=0.6),
        )
        try:
            self.assertAlmostEqual(Gtk.Widget.get_opacity(window), 0.6, places=2)
        finally:
            window.destroy()

    def test_the_gear_dialog_builds_with_every_control(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.Settings(),
        )
        try:
            dialog = window._build_settings_dialog()
            try:
                self.assertIsInstance(dialog, Gtk.Dialog)
            finally:
                dialog.destroy()
        finally:
            window.destroy()

    def test_commit_settings_applies_saves_and_notifies(self):
        import tempfile
        import pathlib
        from ccnav import config

        seen = []
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.Settings(),
            on_settings_changed=lambda s: seen.append(s),
        )
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = pathlib.Path(tmp.name) / "config.json"
        orig_path = config.config_path
        config.config_path = lambda: path
        try:
            new = config.with_updates(config.Settings(), font_size=20, corner="bottom-left")
            window._commit_settings(new)

            # Applied to the live window...
            self.assertEqual(window._settings, new)
            self.assertIn("20pt", window._css.to_string())
            # ...persisted to disk...
            self.assertEqual(config.load(path), new)
            # ...and the owner was told.
            self.assertEqual(seen, [new])
        finally:
            config.config_path = orig_path
            window.destroy()

    def test_dialog_shows_version_and_commits_colour(self):
        import tempfile, pathlib
        from ccnav import config, __version__
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.Settings(),
        )
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        orig = config.config_path
        config.config_path = lambda: pathlib.Path(tmp.name) / "c.json"
        try:
            window._commit_settings(config.with_updates(window._settings, bg_color="#abcdef"))
            # GTK's CssProvider re-serializes on to_string(): a hex color comes
            # back as rgb(r,g,b), not the original "#abcdef" literal (as with
            # "#123456" -> "rgb(18,52,86)" above), so we assert the round-tripped
            # decimal form: 0xab=171, 0xcd=205, 0xef=239.
            self.assertIn("rgb(171,205,239)", window._css.to_string())
            dialog = window._build_settings_dialog()
            try:
                # The version string appears somewhere in the dialog's labels.
                found = []
                def walk(w):
                    if isinstance(w, Gtk.Label):
                        found.append(w.get_text())
                    if isinstance(w, Gtk.Container):
                        for c in w.get_children():
                            walk(c)
                walk(dialog.get_content_area())
                self.assertTrue(any(__version__ in t for t in found))
            finally:
                dialog.destroy()
        finally:
            config.config_path = orig
            window.destroy()

    def test_wiring_frame_reflects_and_toggles_launcher(self):
        import tempfile, pathlib
        from ccnav import config, wiring
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None, settings=config.Settings())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        apps = pathlib.Path(tmp.name)
        # Point the window's wiring helpers at a temp dir via its hook seam.
        window._wiring_apps_dir = apps
        try:
            self.assertFalse(wiring.launcher_installed(apps))
            window._set_launcher(True)
            self.assertTrue(wiring.launcher_installed(apps))
            window._set_launcher(False)
            self.assertFalse(wiring.launcher_installed(apps))
        finally:
            window.destroy()

    def test_settings_dialog_survives_a_corrupt_autostart_file(self):
        # A non-UTF-8 autostart .desktop makes autostart_enabled's reader raise
        # if unguarded; make_toggle must swallow it so the ENTIRE dialog still
        # builds (else the gear opens nothing and every setting is unreachable).
        import tempfile, pathlib
        from ccnav import config, wiring
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None, settings=config.Settings())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        autostart = pathlib.Path(tmp.name)
        (autostart / (wiring.APP_ID + ".desktop")).write_bytes(b"\xff\xfe corrupt \x80")
        window._wiring_autostart_dir = autostart
        try:
            dialog = window._build_settings_dialog()  # must not raise
            self.assertIsNotNone(dialog)
            dialog.destroy()
        finally:
            window.destroy()

    def test_cc_exec_path_points_at_an_existing_launcher(self):
        # The launcher/autostart .desktop Exec must resolve to a real file, not
        # the ~/.local/bin symlink that only ./install creates.
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None, settings=config.Settings())
        try:
            exec_path = window._cc_exec_path()
            self.assertTrue(exec_path.endswith("/bin/cc-navigator"))
            self.assertTrue(os.path.exists(exec_path), exec_path)
        finally:
            window.destroy()

    def test_update_result_shows_message_and_does_not_restart_when_not_updated(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None, settings=config.Settings())
        restarted = []
        window._updater_restart = lambda: restarted.append(True)
        try:
            window._build_settings_dialog()
            window._update_button.set_sensitive(False)
            window._apply_update_result(False, "이미 최신 버전입니다.")
            self.assertEqual(window._update_status.get_text(), "이미 최신 버전입니다.")
            self.assertTrue(window._update_button.get_sensitive())  # re-enabled
            self.assertEqual(restarted, [])  # nothing updated -> no restart
        finally:
            window.destroy()

    def test_update_result_restarts_when_updated(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None, settings=config.Settings())
        restarted = []
        window._updater_restart = lambda: restarted.append(True)
        try:
            window._build_settings_dialog()
            window._apply_update_result(True, "업데이트했습니다. 재시작합니다…")
            self.assertEqual(restarted, [True])  # success -> re-exec
        finally:
            window.destroy()

    def test_update_button_click_runs_the_injected_updater(self):
        from gi.repository import GLib
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None, settings=config.Settings())
        window._updater_update = lambda: (False, "이미 최신 버전입니다.")
        window._updater_restart = lambda: None
        try:
            window._build_settings_dialog()
            window._on_update_clicked(window._update_button)
            # The worker runs on a daemon thread and posts back via idle_add.
            loop = GLib.MainLoop()
            GLib.timeout_add(300, loop.quit)
            loop.run()
            self.assertEqual(window._update_status.get_text(), "이미 최신 버전입니다.")
            self.assertTrue(window._update_button.get_sensitive())
        finally:
            window.destroy()
