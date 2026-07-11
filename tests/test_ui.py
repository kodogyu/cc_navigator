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


class _Geo:
    def __init__(self, x=0, y=0, width=1920, height=1080):
        self.x, self.y, self.width, self.height = x, y, width, height


class DockRectTest(unittest.TestCase):
    """The docked-bar geometry is pure, so its flush-to-edge placement and the
    edge-sliding clamp are testable without a real window."""

    def test_each_edge_sits_flush_and_centred(self):
        g = _Geo()
        self.assertEqual(ui._dock_rect("top", g), (850, 0, 220, 44))
        self.assertEqual(ui._dock_rect("bottom", g), (850, 1080 - 44, 220, 44))
        self.assertEqual(ui._dock_rect("left", g), (0, 430, 44, 220))
        self.assertEqual(ui._dock_rect("right", g), (1920 - 44, 430, 44, 220))

    def test_a_monitor_offset_is_respected(self):
        g = _Geo(x=100, y=50)
        x, y, w, h = ui._dock_rect("top", g)
        self.assertEqual(y, 50)             # flush to THIS monitor's top
        self.assertEqual(x, 100 + (1920 - 220) // 2)

    def test_sliding_along_the_edge_is_clamped_on_screen(self):
        g = _Geo()
        # Slide a top bar far right / far left: x clamps, y stays pinned to the top.
        self.assertEqual(ui._dock_rect("top", g, 5000), (1920 - 220, 0, 220, 44))
        self.assertEqual(ui._dock_rect("top", g, -100), (0, 0, 220, 44))
        # A left bar slides on y only, clamped to the bottom.
        self.assertEqual(ui._dock_rect("left", g, 9999), (0, 1080 - 220, 44, 220))


class RoundedRegionTest(unittest.TestCase):
    """The docked-bar corner clip is a pure geometry, testable without a window."""

    def test_only_the_flagged_corners_are_cut(self):
        reg = ui._rounded_region(20, 20, 6, (True, False, False, False))  # top-left only
        self.assertFalse(reg.contains_point(0, 0))    # top-left cut
        self.assertTrue(reg.contains_point(19, 0))    # top-right square
        self.assertTrue(reg.contains_point(0, 19))    # bottom-left square
        self.assertTrue(reg.contains_point(19, 19))   # bottom-right square
        self.assertTrue(reg.contains_point(10, 10))   # interior kept

    def test_each_edge_rounds_the_corners_away_from_the_wall(self):
        # docked right => the two LEFT corners are rounded, the right ones flush.
        right = ui._rounded_region(44, 220, 12, ui._DOCK_CORNERS["right"])
        self.assertFalse(right.contains_point(0, 0))
        self.assertFalse(right.contains_point(0, 219))
        self.assertTrue(right.contains_point(43, 0))
        self.assertTrue(right.contains_point(43, 219))
        # docked bottom => the two TOP corners are rounded, the bottom ones flush.
        bottom = ui._rounded_region(220, 44, 12, ui._DOCK_CORNERS["bottom"])
        self.assertFalse(bottom.contains_point(0, 0))
        self.assertFalse(bottom.contains_point(219, 0))
        self.assertTrue(bottom.contains_point(0, 43))
        self.assertTrue(bottom.contains_point(219, 43))


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

    def test_collapse_hides_content_and_expand_restores(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        window.show_all()
        try:
            window.set_rows([row()])
            self.assertTrue(window._content.get_visible())
            window.set_collapsed(True)
            self.assertFalse(window._content.get_visible())
            # Collapsing must show a bare titlebar -- NOT leak the dock bar's icon
            # and detach button (GtkStack used to fall back to the visible bar).
            self.assertFalse(window._dock_bar.get_visible())
            self.assertNotEqual(window._stack.get_visible_child_name(), "docked")
            window.set_collapsed(False)
            self.assertTrue(window._content.get_visible())
            self.assertEqual(window._stack.get_visible_child_name(), "full")
        finally:
            window.destroy()

    def test_collapse_after_undock_does_not_leak_the_dock_bar(self):
        # Once docked-then-detached, the dock bar must go hidden again so a later
        # collapse cannot make the stack fall back to showing it.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        window.show_all()
        try:
            window._dock_to_edge("right")
            self.assertTrue(window._dock_bar.get_visible())
            window._undock()
            self.assertFalse(window._dock_bar.get_visible())
            window.set_collapsed(True)
            self.assertFalse(window._dock_bar.get_visible())  # no stale leak
            self.assertNotEqual(window._stack.get_visible_child_name(), "docked")
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

    def test_working_row_shows_a_spinner_and_no_waiting_text(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(state=hookstate.WORKING, session_id="a")])
            child = window._listbox.get_children()[0]
            # the working indicator is the cairo reload spinner (a DrawingArea)
            self.assertEqual(len(self._widgets_of_type(child, Gtk.DrawingArea)), 1)
            for text in self._labels_under(child):
                self.assertNotIn("Waiting input", text)  # the old badge is gone
        finally:
            window.destroy()

    def test_waiting_row_shows_a_dot_not_a_spinner(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(state=hookstate.WAITING, reason="idle", session_id="a")])
            child = window._listbox.get_children()[0]
            self.assertIn("●", self._labels_under(child))  # a coloured dot
            self.assertEqual(len(self._widgets_of_type(child, Gtk.DrawingArea)), 0)  # no spinner
        finally:
            window.destroy()

    def test_spinner_rotation_advances_under_the_main_loop(self):
        from gi.repository import GLib
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        window.show_all()
        try:
            window.set_rows([row(session_id="a", state=hookstate.WORKING)])
            spinner = self._widgets_of_type(
                window._listbox.get_children()[0], Gtk.DrawingArea)[0]
            before = spinner.ccnav_angle
            loop = GLib.MainLoop()
            GLib.timeout_add(300, loop.quit)
            loop.run()
            self.assertNotEqual(spinner.ccnav_angle, before)  # it spun
        finally:
            window.destroy()

    def _dot_markup(self, child):
        for lbl in self._widgets_of_type(child, Gtk.Label):
            if "●" in lbl.get_label():
                return lbl.get_label()
        return ""

    def _row_by_id(self, window, session_id):
        for c in window._listbox.get_children():
            if getattr(c, "ccnav_is_header", False):
                continue
            if c.ccnav_row.session_id == session_id:
                return c
        return None

    def test_changing_one_row_keeps_every_rows_widget(self):
        # The flicker fix: set_rows reconciles in place, so a change to one
        # session must NOT destroy/rebuild any row's widgets -- unchanged rows
        # are untouched and the changed row keeps its widget identity.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a"), row(session_id="b")])
            a0 = self._row_by_id(window, "a")
            b0 = self._row_by_id(window, "b")
            window.set_rows([row(session_id="a"),
                             row(session_id="b", state=hookstate.WORKING)])
            self.assertIs(self._row_by_id(window, "a"), a0)  # unchanged: same widget, never rebuilt
            self.assertIs(self._row_by_id(window, "b"), b0)  # changed: same widget, updated in place
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

    def test_state_flip_swaps_dot_for_spinner_in_place(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", state=hookstate.WAITING, reason="idle")])
            child = window._listbox.get_children()[0]
            self.assertIn("●", self._labels_under(child))  # a dot
            self.assertEqual(len(self._widgets_of_type(child, Gtk.DrawingArea)), 0)
            window.set_rows([row(session_id="a", state=hookstate.WORKING)])
            self.assertIs(window._listbox.get_children()[0], child)  # same row widget
            self.assertEqual(len(self._widgets_of_type(child, Gtk.DrawingArea)), 1)  # dot -> spinner
        finally:
            window.destroy()

    def test_gone_session_is_removed_and_new_one_added(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a"), row(session_id="b")])
            window.set_rows([row(session_id="b"), row(session_id="c")])
            ids = {c.ccnav_row.session_id for c in window._listbox.get_children()}
            self.assertEqual(ids, {"b", "c"})  # a removed, c inserted
        finally:
            window.destroy()

    def _display_ids(self, window):
        out, i = [], 0
        while True:
            r = window._listbox.get_row_at_index(i)
            if r is None:
                break
            if not getattr(r, "ccnav_is_header", False):  # skip group header rows
                out.append(r.ccnav_row.session_id)
            i += 1
        return out

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

    def test_header_badge_counts_input_needed_sessions(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([
                row(session_id="i1", state=hookstate.WAITING, reason="permission_prompt"),
                row(session_id="i2", state=hookstate.WAITING, reason="question"),
                row(session_id="w", state=hookstate.WORKING)])
            self.assertTrue(window._count_badge.get_visible())
            self.assertIn("2", window._count_badge.get_text())  # 2 waiting for input
            window.set_rows([row(session_id="w", state=hookstate.WORKING)])
            self.assertFalse(window._count_badge.get_visible())  # none waiting -> hidden
        finally:
            window.destroy()

    def test_manual_mode_orders_by_the_manual_order(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="a"), row(session_id="b"), row(session_id="c")])
            self.assertEqual(self._display_ids(window), ["a", "b", "c"])  # insertion order
            window._reorder_session("c", "a")  # drag c to just before a
            self.assertEqual(self._display_ids(window), ["c", "a", "b"])
            window._reorder_session("c", "b", after=True)  # drop c below b (reaches bottom)
            self.assertEqual(self._display_ids(window), ["a", "b", "c"])
        finally:
            window.destroy()

    def test_manual_order_appends_new_and_prunes_gone_sessions(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="a"), row(session_id="b")])
            self.assertEqual(window._manual_order, ["a", "b"])
            window.set_rows([row(session_id="b"), row(session_id="c")])  # a gone, c new
            self.assertEqual(window._manual_order, ["b", "c"])  # a pruned, c appended at end
        finally:
            window.destroy()

    def test_reorder_ignores_self_and_unknown_ids(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="a"), row(session_id="b")])
            window._reorder_session("a", "a")  # self -> no-op
            window._reorder_session("z", "a")  # unknown dragged -> no-op
            self.assertEqual(window._manual_order, ["a", "b"])
        finally:
            window.destroy()

    def test_moving_a_session_to_another_group_regroups_it(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="a", cwd="/p/x"), row(session_id="b", cwd="/p/y")])
            self.assertEqual(window._group_of(self._row_by_id(window, "a").ccnav_row), "/p/x")
            window._group_override["a"] = "/p/y"  # what a drop on /p/y's header does
            window._regroup_now()
            self.assertEqual(window._group_of(self._row_by_id(window, "a").ccnav_row), "/p/y")
            groups = {c.ccnav_group for c in window._listbox.get_children()
                      if getattr(c, "ccnav_is_header", False)}
            self.assertEqual(groups, {"/p/y"})  # /p/x is now empty -> its header removed
        finally:
            window.destroy()

    def test_auto_sort_clears_overrides_and_regroups_by_directory(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="a", cwd="/p/x"), row(session_id="b", cwd="/p/y")])
            window._group_override["a"] = "/p/y"
            window._regroup_now()
            self.assertEqual(window._group_of(self._row_by_id(window, "a").ccnav_row), "/p/y")
            window._on_auto_sort_clicked(None)
            self.assertEqual(window._group_override, {})  # moves cleared
            self.assertEqual(window._group_of(self._row_by_id(window, "a").ccnav_row), "/p/x")
        finally:
            window.destroy()

    def test_group_rename_shows_the_custom_name(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="a", cwd="/p/x")])
            window._group_names["/p/x"] = "My Project"
            window._regroup_now()
            self.assertEqual(window._group_display_name("/p/x"), "My Project")
            header = [c for c in window._listbox.get_children()
                      if getattr(c, "ccnav_is_header", False)][0]
            self.assertIn("My Project", header.ccnav_name_label.get_text())
        finally:
            window.destroy()

    def test_move_into_the_blank_cwd_group_takes_effect(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="a", cwd="/p/x"), row(session_id="b", cwd="")])
            window._set_group_override("a", "")  # move a into the "" ("~") group
            window._regroup_now()
            self.assertEqual(window._group_of(self._row_by_id(window, "a").ccnav_row), "")
        finally:
            window.destroy()

    def test_collapse_deselect_uses_the_effective_group_not_the_directory(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="a", cwd="/p/x"), row(session_id="b", cwd="/p/y")])
            window._set_group_override("a", "/p/y")  # a moved into /p/y
            window._regroup_now()
            window._listbox.select_row(self._row_by_id(window, "a"))
            window._on_group_toggle(None, "/p/x")  # a's ORIGINAL dir, not its group now
            self.assertIsNotNone(window._listbox.get_selected_row())  # a stays selected
            window._on_group_toggle(None, "/p/y")  # a's EFFECTIVE group -> deselect
            self.assertIsNone(window._listbox.get_selected_row())
        finally:
            window.destroy()

    def test_a_redundant_self_override_is_not_stored(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="a", cwd="/p/x")])
            window._set_group_override("a", "/p/x")  # its own directory
            self.assertNotIn("a", window._group_override)  # not pinned redundantly
        finally:
            window.destroy()

    def test_auto_sort_button_visible_only_in_group_mode(self):
        from ccnav import config
        status_win = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        group_win = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            self.assertFalse(status_win._auto_sort_button.get_visible())
            self.assertTrue(group_win._auto_sort_button.get_visible())
        finally:
            status_win.destroy()
            group_win.destroy()

    def test_dock_to_edge_switches_to_the_docked_bar_with_orientation(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window._dock_to_edge("left")
            self.assertEqual(window._docked_edge, "left")
            self.assertEqual(window._stack.get_visible_child_name(), "docked")
            self.assertEqual(window._dock_bar.get_orientation(), Gtk.Orientation.VERTICAL)
            window._dock_to_edge("top")  # re-dock horizontally
            self.assertEqual(window._dock_bar.get_orientation(), Gtk.Orientation.HORIZONTAL)
        finally:
            window.destroy()

    def test_detach_restores_the_full_view(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window._dock_to_edge("right")
            window._dock_detach.emit("clicked")  # the detach button
            self.assertIsNone(window._docked_edge)
            self.assertEqual(window._stack.get_visible_child_name(), "full")
        finally:
            window.destroy()

    def test_detach_recovers_full_view_even_after_a_pre_dock_collapse(self):
        # GtkStack won't switch to a hidden child; a collapse before docking hid
        # _content, so detach must re-show it or the window stays stuck on the bar.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_collapsed(True)
            window._dock_to_edge("left")
            window._dock_detach.emit("clicked")
            self.assertEqual(window._stack.get_visible_child_name(), "full")
            self.assertTrue(window._content.get_visible())
            self.assertFalse(window._collapse_button.get_active())  # collapse cleared
        finally:
            window.destroy()

    def test_a_late_collapse_toggle_while_docked_does_not_corrupt_the_dock(self):
        # Docking FROM a collapsed panel: the attach popover's animated "closed"
        # un-presses the still-active collapse button AFTER _dock_to_edge ran, so
        # set_collapsed(False) arrives while docked. It must not swap the stack
        # back to "full" or re-grow the window (which would hide the detach button).
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        window.show_all()
        try:
            window.set_collapsed(True)
            window._dock_to_edge("left")
            self.assertEqual(window._stack.get_visible_child_name(), "docked")
            window._collapse_button.set_active(False)  # the late toggle
            self.assertEqual(window._docked_edge, "left")
            self.assertEqual(window._stack.get_visible_child_name(), "docked")
            self.assertTrue(window._dock_bar.get_visible())
            self.assertFalse(window._header.get_visible())
            window._undock()  # detach still recovers cleanly
            self.assertEqual(window._stack.get_visible_child_name(), "full")
            self.assertTrue(window._content.get_visible())
            self.assertFalse(window._dock_bar.get_visible())
            # The chevron must point up (expanded) again: the popover already
            # un-pressed the button, so undock can't rely on it being active.
            name, _size = window._collapse_button.get_child().get_icon_name()
            self.assertEqual(name, "pan-up-symbolic")
        finally:
            window.destroy()

    def test_a_stray_button_does_not_end_a_docked_slide(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window._dock_to_edge("top")
            window._dock_drag = (10, 850)  # a button-1 slide is in progress

            class Ev:
                def __init__(self, b):
                    self.button = b

                def get_seat(self):
                    return None

            # A stray button-3 release (delivered by the grab) must NOT end it.
            self.assertFalse(window._on_dock_drag_end(window._dock_drag_inner, Ev(3)))
            self.assertIsNotNone(window._dock_drag)
            # The button-1 release ends the slide.
            self.assertTrue(window._on_dock_drag_end(window._dock_drag_inner, Ev(1)))
            self.assertIsNone(window._dock_drag)
        finally:
            window.destroy()

    def test_show_all_does_not_reveal_the_header_while_docked(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        window.show_all()
        try:
            window._dock_to_edge("top")
            self.assertFalse(window._header.get_visible())
            window.show_all()  # must NOT re-reveal the titlebar while docked
            self.assertFalse(window._header.get_visible())
        finally:
            window._undock()
            window.destroy()

    def test_dock_ignores_an_unknown_edge(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window._dock_to_edge("nowhere")
            self.assertIsNone(window._docked_edge)
            self.assertEqual(window._stack.get_visible_child_name(), "full")
        finally:
            window.destroy()

    def test_docking_adds_the_flush_style_class_and_undocking_removes_it(self):
        # The "docked" class zeroes the CSD shadow so the bar sits flush; it must
        # only be present while docked (else the floating panel loses its frame).
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            self.assertFalse(window.get_style_context().has_class("docked"))
            window._dock_to_edge("bottom")
            self.assertTrue(window.get_style_context().has_class("docked"))
            self.assertIsNotNone(window._dock_geo)
            window._undock()
            self.assertFalse(window.get_style_context().has_class("docked"))
        finally:
            window.destroy()

    def test_docking_sets_a_per_edge_class_for_rounded_corners(self):
        # "docked-<edge>" rounds the two corners away from the edge; a re-dock must
        # swap it, and undock must clear every docked class.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        ctx = window.get_style_context()
        try:
            window._dock_to_edge("right")
            self.assertTrue(ctx.has_class("docked-right"))
            window._dock_to_edge("top")  # re-dock swaps the edge class
            self.assertFalse(ctx.has_class("docked-right"))
            self.assertTrue(ctx.has_class("docked-top"))
            window._undock()
            for c in ("docked", "docked-top", "docked-bottom", "docked-left", "docked-right"):
                self.assertFalse(ctx.has_class(c))
        finally:
            window.destroy()

    def test_dock_resnap_repins_flush_only_while_still_docked(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window._dock_to_edge("right")
            moves = []
            window.move = lambda x, y: moves.append((x, y))
            window._dock_geo = _Geo()
            window._dock_drag = None
            self.assertFalse(window._resnap_dock("right"))       # one-shot -> False
            self.assertEqual(moves, [(1920 - 44, (1080 - 220) // 2)])  # flush right, centred
            moves.clear()
            self.assertFalse(window._resnap_dock("left"))        # stale edge -> guarded
            self.assertEqual(moves, [])
            # A slide started within the settle window must NOT be re-centered.
            window._dock_drag = (500, 430)
            self.assertFalse(window._resnap_dock("right"))
            self.assertEqual(moves, [])                          # drag in progress -> no move
        finally:
            window.destroy()

    def test_dragging_a_docked_bar_slides_along_the_edge_and_pins_the_other_axis(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window._dock_to_edge("right")
            moves = []
            window.move = lambda x, y: moves.append((x, y))  # capture, no real WM move
            window._dock_geo = _Geo()
            window._dock_drag = (500, 430)  # pointer y_root started at 500, bar y at 430

            class Ev:  # a drag 60px down
                button, x_root, y_root = 1, 0, 560

                def get_seat(self):
                    return None

            self.assertTrue(window._on_dock_drag_motion(window._dock_drag_inner, Ev()))
            # x stays pinned to the right edge; y slides by the drag delta.
            self.assertEqual(moves, [(1920 - 44, 490)])
        finally:
            window.destroy()

    def test_a_docked_drag_motion_is_a_noop_when_no_drag_is_in_progress(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window._dock_to_edge("top")
            window._dock_drag = None  # not dragging

            class Ev:
                button, x_root, y_root = 1, 10, 10

                def get_seat(self):
                    return None

            self.assertFalse(window._on_dock_drag_motion(window._dock_drag_inner, Ev()))
        finally:
            window.destroy()

    def test_the_drag_grip_is_the_last_widget_in_the_row_header(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="a")])
            child = self._row_by_id(window, "a")
            self.assertIs(child.ccnav_header.get_children()[-1], child.ccnav_grip)
        finally:
            window.destroy()

    def test_drag_grip_visible_only_in_group_mode(self):
        from ccnav import config
        status_win = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        group_win = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            status_win.set_rows([row(session_id="a")])
            group_win.set_rows([row(session_id="a")])
            self.assertFalse(status_win._listbox.get_children()[0].ccnav_grip.get_visible())
            grip = self._row_by_id(group_win, "a").ccnav_grip
            self.assertTrue(grip.get_visible())
            # The '⠿' glyph must be shown too: no_show_all on the grip stops
            # show_all reaching the label, so it is shown explicitly or renders empty.
            self.assertTrue(grip.get_child().get_visible())
        finally:
            status_win.destroy()
            group_win.destroy()

    def test_grip_drag_provides_the_session_id(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="xyz")])
            child = self._row_by_id(window, "xyz")
            captured = {}

            class Sel:
                def get_target(self):
                    return None

                def set(self, target, fmt, data):
                    captured["d"] = data

            window._on_grip_drag_get(child.ccnav_grip, None, Sel(), 0, 0, child)
            self.assertEqual(captured["d"], b"xyz")  # the drag carries the session id
        finally:
            window.destroy()

    def test_short_click_collapses_but_a_long_press_does_not(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        window.show_all()
        try:
            window._collapse_button.set_active(True)  # short click -> collapse
            self.assertFalse(window._content.get_visible())
            window._collapse_button.set_active(False)
            self.assertTrue(window._content.get_visible())
            window._on_collapse_long_press(window._collapse_long_press, 0, 0)  # long press -> attach
            self.assertTrue(window._suppress_collapse_toggle)
            self.assertTrue(window._content.get_visible())  # long press must NOT collapse
        finally:
            window.destroy()

    def test_status_mode_orders_rows_into_sections(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([
                row(session_id="w", state=hookstate.WORKING, updated_at=50),
                row(session_id="i", state=hookstate.WAITING, reason="permission_prompt", updated_at=40),
                row(session_id="r", state=hookstate.WAITING, reason="idle", updated_at=30)])
            # input -> reported -> working (the Sort-by-Status section order)
            self.assertEqual(self._display_ids(window), ["i", "r", "w"])
        finally:
            window.destroy()

    def test_group_mode_orders_rows_by_project_group(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([
                row(session_id="a1", cwd="/p/alpha", reason="permission_prompt", updated_at=100),
                row(session_id="b1", cwd="/p/beta", reason="permission_prompt", updated_at=90),
                row(session_id="a2", cwd="/p/alpha", reason="permission_prompt", updated_at=80)])
            # alpha group first (its newest 100 beats beta's 90); alpha rows contiguous
            self.assertEqual(self._display_ids(window), ["a1", "a2", "b1"])
        finally:
            window.destroy()

    def test_group_header_shows_project_name_path_and_counts(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([
                row(session_id="a1", cwd="/p/alpha",
                    state=hookstate.WAITING, reason="permission_prompt"),
                row(session_id="a2", cwd="/p/alpha", state=hookstate.WORKING)])
            header = window._build_group_header_row("/p/alpha")
            texts = " ".join(l.get_text() for l in self._widgets_of_type(header, Gtk.Label))
            self.assertIn("alpha", texts)     # project name (last path segment)
            self.assertIn("/p/alpha", texts)  # project path
            markup = " ".join(l.get_label() for l in self._widgets_of_type(header, Gtk.Label))
            self.assertIn("1", markup)  # 1 input + 1 working in this group
        finally:
            window.destroy()

    def test_group_header_flattens_name_and_omits_empty_path(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="x", cwd="/p/pr\noj")])
            for lbl in self._widgets_of_type(window._build_group_header_row("/p/pr\noj"), Gtk.Label):
                self.assertNotIn("\n", lbl.get_text())  # name + path both single-line
            window.set_rows([row(session_id="y", cwd="")])
            blank = self._widgets_of_type(window._build_group_header_row(""), Gtk.Label)
            self.assertEqual(len(blank), 2)  # "~" name + counts; no empty path line
        finally:
            window.destroy()

    def test_group_mode_inserts_a_header_row_per_group(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="a", cwd="/p/x"), row(session_id="b", cwd="/p/y")])
            groups = {c.ccnav_group for c in window._listbox.get_children()
                      if getattr(c, "ccnav_is_header", False)}
            self.assertEqual(groups, {"/p/x", "/p/y"})
        finally:
            window.destroy()

    def test_status_mode_has_no_header_rows(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", cwd="/p/x")])
            self.assertFalse(any(getattr(c, "ccnav_is_header", False)
                                 for c in window._listbox.get_children()))
        finally:
            window.destroy()

    def test_collapsing_a_group_filters_its_sessions_but_keeps_the_header(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="a", cwd="/p/x"), row(session_id="b", cwd="/p/y")])
            a = self._row_by_id(window, "a")
            self.assertTrue(window._filter_row(a))          # visible before collapse
            window._on_group_toggle(None, "/p/x")           # collapse group /p/x
            self.assertFalse(window._filter_row(a))         # its session now filtered out
            groups = {c.ccnav_group for c in window._listbox.get_children()
                      if getattr(c, "ccnav_is_header", False)}
            self.assertIn("/p/x", groups)                   # header row survives collapse
            window._on_group_toggle(None, "/p/x")           # expand again
            self.assertTrue(window._filter_row(a))
        finally:
            window.destroy()

    def test_collapse_deselects_only_a_session_in_the_collapsed_group(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="a", cwd="/p/x"), row(session_id="b", cwd="/p/y")])
            a = self._row_by_id(window, "a")
            window._listbox.select_row(a)
            window._on_group_toggle(None, "/p/y")   # collapse the OTHER group
            self.assertIs(window._listbox.get_selected_row(), a)  # a's selection kept
            window._on_group_toggle(None, "/p/x")   # collapse a's group
            self.assertIsNone(window._listbox.get_selected_row())  # a deselected, not hidden-selected
        finally:
            window.destroy()

    def test_collapsed_state_is_forgotten_when_a_group_disappears(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="a", cwd="/p/x")])
            window._on_group_toggle(None, "/p/x")
            self.assertIn("/p/x", window._collapsed_groups)
            window.set_rows([row(session_id="b", cwd="/p/y")])  # /p/x group is gone
            self.assertNotIn("/p/x", window._collapsed_groups)  # its collapse state pruned
        finally:
            window.destroy()

    def test_switching_to_status_mode_removes_group_header_rows(self):
        import tempfile, pathlib
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        orig = config.config_path
        config.config_path = lambda: pathlib.Path(tmp.name) / "c.json"
        try:
            window.set_rows([row(session_id="a", cwd="/p/x")])
            self.assertTrue(any(getattr(c, "ccnav_is_header", False)
                                for c in window._listbox.get_children()))
            window._sort_combo.set_active_id("status")  # -> group headers removed
            self.assertFalse(any(getattr(c, "ccnav_is_header", False)
                                 for c in window._listbox.get_children()))
        finally:
            config.config_path = orig
            window.destroy()

    def test_status_header_shows_the_section_title_and_count(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="i", state=hookstate.WAITING,
                                 reason="permission_prompt")])
            header = window._make_status_header(model.INPUT_NEEDED)
            texts = " ".join(l.get_text() for l in self._widgets_of_type(header, Gtk.Label))
            self.assertIn("입력이 필요한 세션", texts)
            self.assertIn("1", texts)  # the section count
        finally:
            window.destroy()

    def test_switching_sort_mode_via_the_combo_regroups(self):
        import tempfile, pathlib
        from ccnav import config
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        orig = config.config_path
        config.config_path = lambda: pathlib.Path(tmp.name) / "c.json"
        try:
            window.set_rows([
                row(session_id="w", state=hookstate.WORKING, cwd="/p/x", updated_at=50),
                row(session_id="i", state=hookstate.WAITING, reason="permission_prompt",
                    cwd="/p/y", updated_at=40)])
            self.assertEqual(self._display_ids(window), ["i", "w"])  # status: input before working
            window._sort_combo.set_active_id("group")  # fires _on_sort_mode_changed
            self.assertEqual(window._settings.sort_mode, "group")
            # group mode: x group (newest 50) before y group (40) -> w before i
            self.assertEqual(self._display_ids(window), ["w", "i"])
        finally:
            config.config_path = orig
            window.destroy()

    def test_swapping_spinner_for_dot_detaches_it_so_its_timer_stops(self):
        # The spinner's timer self-stops when the widget loses its ListBox
        # ancestor. After a working->waiting in-place swap the old spinner must
        # be detached (else its timer leaks for the window's life).
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", state=hookstate.WORKING)])
            child = window._listbox.get_children()[0]
            spinner = self._widgets_of_type(child, Gtk.DrawingArea)[0]
            self.assertIsNotNone(spinner.get_ancestor(Gtk.ListBox))  # live -> timer runs
            window.set_rows([row(session_id="a", state=hookstate.WAITING, reason="idle")])
            self.assertIsNone(spinner.get_ancestor(Gtk.ListBox))  # detached -> timer self-stops
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
            self.assertIn("#2ec27e", self._dot_markup(self._row_by_id(window, "a")))  # idle -> green
            self.assertIn("#e01b24", self._dot_markup(self._row_by_id(window, "b")))  # input -> red
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
            btn, status = window._update_button, window._update_status
            btn.set_sensitive(False)
            window._apply_update_result(False, "이미 최신 버전입니다.", btn, status)
            self.assertEqual(status.get_text(), "이미 최신 버전입니다.")
            self.assertTrue(btn.get_sensitive())  # re-enabled
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
            window._apply_update_result(True, "업데이트했습니다. 재시작합니다…",
                                        window._update_button, window._update_status)
            self.assertEqual(restarted, [True])  # success -> re-exec
        finally:
            window.destroy()

    def test_update_that_raises_does_not_restart(self):
        from gi.repository import GLib
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None, settings=config.Settings())
        restarted = []
        window._updater_restart = lambda: restarted.append(True)

        def boom():
            raise RuntimeError("git blew up")
        window._updater_update = boom
        try:
            window._build_settings_dialog()
            window._on_update_clicked(window._update_button)
            loop = GLib.MainLoop()
            GLib.timeout_add(300, loop.quit)
            loop.run()
            self.assertEqual(restarted, [])  # a failure never re-execs
            self.assertIn("실패", window._update_status.get_text())
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
