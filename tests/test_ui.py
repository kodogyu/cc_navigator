import os
import time
import unittest

from ccnav import hookstate, model, ui

# ui pins the Gtk version on import above, so this bare import is safe here.
from gi.repository import Gdk, GLib, Gtk  # noqa: E402


def pump_until(condition, timeout=10.0):
    """Iterate the GTK main context until `condition()` holds.

    Waiting a fixed 300ms and hoping is what made this suite flaky: the thing being
    awaited -- a worker thread posting back through idle_add, a GTK timeout firing, the
    WM answering a resize -- takes as long as the machine is busy, so under load the
    test failed on a slow machine rather than on wrong code. (Reproduced by pinning
    every core: the fixed-wait tests failed every run.) Poll the real condition instead,
    with a ceiling generous enough that only a genuine hang trips it.
    """
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError("condition not met within %.1fs" % timeout)
        if context.pending():
            context.iteration(False)
        else:
            time.sleep(0.005)  # let wall-clock advance so GTK timeouts come due


def pump_briefly(ms=50):
    """Spin the loop for a moment where there is no condition to wait ON -- e.g. to let
    GTK settle a layout before measuring it. Never use this to await async work: use
    pump_until, which cannot lose a race."""
    deadline = time.monotonic() + ms / 1000.0
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        if context.pending():
            context.iteration(False)
        else:
            time.sleep(0.005)


def row(state=hookstate.WAITING, reason="permission_prompt", message="Allow npm test?",
        cwd="/data/projects/demo_project", session_id="a", title="✳ 작업 중",
        last_prompt="", pane="%1", socket="/tmp/s", updated_at=1, subagent_ids=()):
    return model.Row(
        session_id=session_id, socket=socket, pane=pane, tmux_session="demo",
        title=title, state=state, reason=reason,
        message=message, cwd=cwd, updated_at=updated_at, last_prompt=last_prompt,
        subagent_ids=tuple(subagent_ids),
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


class _FakeMonitor:
    def __init__(self, geo, workarea):
        self._geo, self._workarea = geo, workarea

    def get_geometry(self):
        return self._geo

    def get_workarea(self):
        return self._workarea


class DockAreaTest(unittest.TestCase):
    """Docking targets the monitor's WORK area (which excludes system panels/docks
    that reserve space), so a docked bar sits beside such a panel, not under it."""

    def test_prefers_the_work_area_over_the_full_geometry(self):
        geo = _Geo()                    # full 1920x1080
        wa = _Geo(width=1840)           # a dock reserves 80px on the right
        self.assertIs(ui._dock_area(_FakeMonitor(geo, wa)), wa)

    def test_falls_back_to_geometry_when_the_work_area_is_degenerate(self):
        geo = _Geo()
        for bad in (None, _Geo(width=0), _Geo(height=0)):
            self.assertIs(ui._dock_area(_FakeMonitor(geo, bad)), geo)

    def test_a_right_dock_lands_beside_a_right_side_panel_not_under_it(self):
        # With 80px reserved on the right, a right-docked bar's right edge is the
        # work area's right edge (1840), not the screen edge (1920).
        wa = _Geo(width=1840)
        x, _y, w, _h = ui._dock_rect("right", wa)
        self.assertEqual(x + w, 1840)


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
class ClickToJumpTest(unittest.TestCase):
    def _session_child(self, window):
        return next(c for c in window._listbox.get_children()
                    if not getattr(c, "ccnav_is_header", False))

    def test_a_row_click_jumps_when_the_setting_is_on(self):
        from ccnav import config
        jumped = []
        window = ui.NavigatorWindow(
            on_jump=lambda r: jumped.append(r), on_send=lambda r, t: None,
            settings=config.Settings(click_to_jump=True))
        try:
            window.set_eval_available(True)
            window.set_rows([row(session_id="a")])
            window._on_row_activated(window._listbox, self._session_child(window))
            self.assertEqual([r.session_id for r in jumped], ["a"])
        finally:
            window.destroy()

    def test_a_row_click_does_not_jump_when_the_setting_is_off(self):
        from ccnav import config
        jumped = []
        window = ui.NavigatorWindow(
            on_jump=lambda r: jumped.append(r), on_send=lambda r, t: None,
            settings=config.Settings(click_to_jump=False))
        try:
            window.set_eval_available(True)
            window.set_rows([row(session_id="a")])
            window._on_row_activated(window._listbox, self._session_child(window))
            self.assertEqual(jumped, [])
        finally:
            window.destroy()

    def test_click_to_jump_is_suppressed_when_eval_is_unavailable(self):
        from ccnav import config
        jumped = []
        window = ui.NavigatorWindow(
            on_jump=lambda r: jumped.append(r), on_send=lambda r, t: None,
            settings=config.Settings(click_to_jump=True))
        try:
            window.set_eval_available(False)  # jump disabled -> click must not fire it
            window.set_rows([row(session_id="a")])
            window._on_row_activated(window._listbox, self._session_child(window))
            self.assertEqual(jumped, [])
        finally:
            window.destroy()


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
            self.assertEqual(len(self._session_children(window)), 3)
        finally:
            window.destroy()

    def test_identical_rows_reuse_the_same_entry_and_keep_its_text(self):
        # Task 10's one-second timer calls set_rows with unchanged rows. If it
        # rebuilds anyway, the Gtk.Entry the user is typing into is destroyed and
        # the text is silently lost. Same rows -> same Entry object, text intact.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row()])
            entry_before = self._first_row(window).ccnav_entry
            entry_before.set_text("please approve this")

            window.set_rows([row()])  # identical signature -> no rebuild

            entry_after = self._first_row(window).ccnav_entry
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
            target = self._first_row(window)
            self.assertEqual(target.ccnav_row.session_id, "a")
            window._listbox.select_row(target)
            target.ccnav_entry.set_text("please approve this")
            self.assertTrue(target.ccnav_revealer.get_reveal_child())

            # Session b changes; a is untouched but every widget is rebuilt.
            window.set_rows(
                [row(session_id="a"), row(session_id="b", message="something else")]
            )

            survivors = [
                c for c in self._session_children(window)
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
            target = self._first_row(window)
            window._listbox.select_row(target)
            target.ccnav_entry.set_text("please approve this")

            # Session a is gone, replaced by b.
            window.set_rows([row(session_id="b")])

            child = self._first_row(window)
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
            for child in self._session_children(window):
                self.assertTrue(child.ccnav_jump.get_sensitive())

            window.set_eval_available(False)

            for child in self._session_children(window):
                self.assertFalse(child.ccnav_jump.get_sensitive())
        finally:
            window.destroy()

    def test_set_row_jump_sensitive_toggles_one_rows_button(self):
        # Added for Task 10: Application disables one row's jump button while
        # its activation is in flight, and must not disturb any other row.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a"), row(session_id="b")])
            children = {c.ccnav_row.session_id: c for c in self._session_children(window)}

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
            self.assertTrue(self._first_row(window).ccnav_jump.get_sensitive())
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
            child = self._first_row(window)
            # the working front is the cairo reload spinner (a DrawingArea)
            self.assertTrue(self._front_is_spinner(child))
            self.assertFalse(self._back(child).ccnav_subagent)  # no subagent layer
            for text in self._labels_under(child):
                self.assertNotIn("Waiting input", text)  # the old badge is gone
        finally:
            window.destroy()

    def test_waiting_row_shows_a_dot_not_a_spinner(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(state=hookstate.WAITING, reason="idle", session_id="a")])
            child = self._first_row(window)
            self.assertIn("●", self._labels_under(child))  # a coloured dot
            self.assertFalse(self._front_is_spinner(child))  # front is a dot, not a spinner
        finally:
            window.destroy()

    def test_spinner_rotation_advances_under_the_main_loop(self):
        from gi.repository import GLib
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        window.show_all()
        try:
            window.set_rows([row(session_id="a", state=hookstate.WORKING)])
            spinner = self._front(self._first_row(window))
            before = spinner.ccnav_angle
            pump_until(lambda: spinner.ccnav_angle != before)
            self.assertNotEqual(spinner.ccnav_angle, before)  # it spun
        finally:
            window.destroy()

    def _dot_markup(self, child):
        for lbl in self._widgets_of_type(child, Gtk.Label):
            if "●" in lbl.get_label():
                return lbl.get_label()
        return ""

    def _front(self, child):
        return child.ccnav_indicator.ccnav_front

    def _back(self, child):
        return child.ccnav_indicator.ccnav_back

    def _front_is_spinner(self, child):
        # The working front is a bare DrawingArea; every other front is a dot
        # EventBox carrying ccnav_dot_label.
        return getattr(self._front(child), "ccnav_dot_label", None) is None

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
            child = self._first_row(window)
            self.assertIn("#2ec27e", self._dot_markup(child))  # green
            window.set_rows([row(session_id="a", state=hookstate.WAITING,
                                 reason="permission_prompt")])
            self.assertIs(self._first_row(window), child)  # same widget
            self.assertIn("#e01b24", self._dot_markup(child))  # recoloured red in place
        finally:
            window.destroy()

    def test_state_flip_swaps_dot_for_spinner_in_place(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", state=hookstate.WAITING, reason="idle")])
            child = self._first_row(window)
            self.assertIn("●", self._labels_under(child))  # a dot
            self.assertFalse(self._front_is_spinner(child))
            window.set_rows([row(session_id="a", state=hookstate.WORKING)])
            self.assertIs(self._first_row(window), child)  # same row widget
            self.assertTrue(self._front_is_spinner(child))  # dot -> spinner in place
        finally:
            window.destroy()

    def test_clicking_a_green_dot_toggles_it_to_a_check_and_back(self):
        from gi.repository import Gdk
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", state=hookstate.WAITING, reason="idle")])
            dot = self._first_row(window).ccnav_indicator.ccnav_front
            self.assertIn("●", dot.ccnav_dot_label.get_label())  # filled to start
            ev = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
            handled = dot.emit("button-press-event", ev)
            self.assertTrue(handled)  # swallowed, so the row is not also expanded
            self.assertIn("a", window._acknowledged)
            self.assertIn("✓", dot.ccnav_dot_label.get_label())  # check mark now
            dot.emit("button-press-event", ev)
            self.assertNotIn("a", window._acknowledged)
            self.assertIn("●", dot.ccnav_dot_label.get_label())  # filled again
        finally:
            window.destroy()

    def test_clicking_a_red_input_dot_is_ignored(self):
        from gi.repository import Gdk
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", state=hookstate.WAITING,
                                 reason="permission_prompt")])
            dot = self._first_row(window).ccnav_indicator.ccnav_front
            before = dot.ccnav_dot_label.get_label()
            handled = dot.emit("button-press-event", Gdk.Event.new(Gdk.EventType.BUTTON_PRESS))
            self.assertFalse(handled)  # falls through to the row (no toggle on red)
            self.assertNotIn("a", window._acknowledged)
            self.assertEqual(dot.ccnav_dot_label.get_label(), before)  # unchanged
        finally:
            window.destroy()

    def test_leaving_reported_clears_the_acknowledged_mark(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", state=hookstate.WAITING, reason="idle")])
            child = self._first_row(window)
            window._on_indicator_clicked(child.ccnav_indicator, None, child)  # acknowledge
            self.assertIn("a", window._acknowledged)
            # It resumes working -- leaving green must drop the check mark...
            window.set_rows([row(session_id="a", state=hookstate.WORKING)])
            self.assertNotIn("a", window._acknowledged)
            # ...so when it reports again the dot is filled, seeking a fresh glance.
            window.set_rows([row(session_id="a", state=hookstate.WAITING, reason="idle")])
            self.assertIn("●", self._dot_markup(self._first_row(window)))
        finally:
            window.destroy()

    def test_acknowledged_check_survives_an_update_that_stays_green(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", state=hookstate.WAITING,
                                 reason="idle", message="m1")])
            child = self._first_row(window)
            window._on_indicator_clicked(child.ccnav_indicator, None, child)  # -> check
            self.assertIn("✓", child.ccnav_indicator.ccnav_front.ccnav_dot_label.get_label())
            # A non-status field changes; the row stays green and same widget.
            window.set_rows([row(session_id="a", state=hookstate.WAITING,
                                 reason="idle", message="m2")])
            self.assertIs(self._first_row(window), child)
            self.assertIn("✓", child.ccnav_indicator.ccnav_front.ccnav_dot_label.get_label())
            self.assertIn("a", window._acknowledged)
        finally:
            window.destroy()

    def test_a_gone_session_drops_its_acknowledged_mark(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", state=hookstate.WAITING, reason="idle"),
                             row(session_id="b", state=hookstate.WAITING, reason="idle")])
            a = self._row_by_id(window, "a")
            window._on_indicator_clicked(a.ccnav_indicator, None, a)
            self.assertIn("a", window._acknowledged)
            window.set_rows([row(session_id="b", state=hookstate.WAITING, reason="idle")])
            self.assertNotIn("a", window._acknowledged)  # dropped when the row left
        finally:
            window.destroy()

    # --- subagent dual-icon layer -------------------------------------------

    def test_front_kind_maps_working_with_subagents_to_orchestrating(self):
        self.assertEqual(ui.front_kind(row(state=hookstate.WORKING)), "working")
        self.assertEqual(
            ui.front_kind(row(state=hookstate.WORKING, subagent_ids=("s1",))),
            "orchestrating")
        # a red wait / green report is shown in front unchanged, even with a helper
        self.assertEqual(
            ui.front_kind(row(state=hookstate.WAITING, reason="permission_prompt",
                              subagent_ids=("s1",))), "input")
        self.assertEqual(
            ui.front_kind(row(state=hookstate.WAITING, reason="idle",
                              subagent_ids=("s1",))), "reported")

    def test_a_working_row_with_a_subagent_shows_a_calm_dot_over_a_back_spinner(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", state=hookstate.WORKING,
                                 subagent_ids=("s1",))])
            child = self._first_row(window)
            # front is the calm blue 'orchestrating' dot, NOT a spinner
            self.assertFalse(self._front_is_spinner(child))
            self.assertIn("#3584e4", self._dot_markup(child))
            self.assertTrue(self._back(child).ccnav_subagent)  # helper layer active
        finally:
            window.destroy()

    def test_a_red_wait_with_a_subagent_shows_both_at_once(self):
        # The flagship case: main blocked on the user (red) while a helper runs --
        # a single icon could not show both; the two layers do.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", state=hookstate.WAITING,
                                 reason="permission_prompt", subagent_ids=("s1",))])
            child = self._first_row(window)
            self.assertIn("#e01b24", self._dot_markup(child))     # front stays red
            self.assertTrue(self._back(child).ccnav_subagent)     # back spinner shown
        finally:
            window.destroy()

    def test_subagent_layer_toggles_on_and_off_in_place(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", state=hookstate.WAITING,
                                 reason="permission_prompt")])
            child = self._first_row(window)
            self.assertFalse(self._back(child).ccnav_subagent)
            window.set_rows([row(session_id="a", state=hookstate.WAITING,
                                 reason="permission_prompt", subagent_ids=("s1",))])
            self.assertIs(self._first_row(window), child)  # same widget
            self.assertTrue(self._back(child).ccnav_subagent)
            window.set_rows([row(session_id="a", state=hookstate.WAITING,
                                 reason="permission_prompt")])
            self.assertFalse(self._back(child).ccnav_subagent)  # helper finished
        finally:
            window.destroy()

    def test_a_subagent_appearing_in_place_starts_the_back_spinner(self):
        from gi.repository import GLib
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        window.show_all()
        try:
            window.set_rows([row(session_id="a", state=hookstate.WAITING,
                                 reason="permission_prompt")])
            child = self._first_row(window)
            self.assertFalse(self._back(child).ccnav_spinning)  # idle: no timer
            window.set_rows([row(session_id="a", state=hookstate.WAITING,
                                 reason="permission_prompt", subagent_ids=("s1",))])
            back = self._back(child)
            before = back.ccnav_angle
            pump_until(lambda: back.ccnav_angle != before)
            self.assertNotEqual(back.ccnav_angle, before)  # started spinning in place
        finally:
            window.destroy()

    def test_gone_session_is_removed_and_new_one_added(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a"), row(session_id="b")])
            window.set_rows([row(session_id="b"), row(session_id="c")])
            ids = {c.ccnav_row.session_id for c in self._session_children(window)}
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

    def _display_groups(self, window):
        """The group keys in on-screen (sorted) order -- read from the group header
        rows, which lead their groups, so this IS the group display order."""
        out, i = [], 0
        while True:
            r = window._listbox.get_row_at_index(i)
            if r is None:
                break
            if getattr(r, "ccnav_is_header", False) and hasattr(r, "ccnav_group"):
                out.append(r.ccnav_group)
            i += 1
        return out

    def _display_sections(self, window):
        """The status-section keys in on-screen (sorted) order -- read from the
        status header rows, which lead their sections."""
        out, i = [], 0
        while True:
            r = window._listbox.get_row_at_index(i)
            if r is None:
                break
            if getattr(r, "ccnav_is_header", False) and hasattr(r, "ccnav_section"):
                out.append(r.ccnav_section)
            i += 1
        return out

    def _first_row(self, window):
        """The first SESSION row in display order, skipping the section/group
        header rows both modes now interleave. (Was window._listbox.get_children()
        [0] before Status mode grew header rows.)"""
        for c in window._listbox.get_children():
            if not getattr(c, "ccnav_is_header", False):
                return c
        return None

    def _session_children(self, window):
        """All non-header child rows (the sessions), in display order."""
        return [c for c in window._listbox.get_children()
                if not getattr(c, "ccnav_is_header", False)]

    def test_a_row_that_needs_input_jumps_above_working_rows(self):
        # The reconcile must keep the section priority sort live: updating a row in
        # place must re-sort, not leave it where it was. Input-needed is the top
        # section, above working (whereas a finished 'reported' row sorts BELOW
        # working now -- see test_status_mode_orders_rows_into_sections).
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", state=hookstate.WORKING, updated_at=90),
                             row(session_id="b", state=hookstate.WORKING, updated_at=100)])
            self.assertEqual(self._display_ids(window), ["b", "a"])  # newer working on top
            window.set_rows([
                row(session_id="a", state=hookstate.WAITING, reason="permission_prompt", updated_at=110),
                row(session_id="b", state=hookstate.WORKING, updated_at=100)])
            self.assertEqual(self._display_ids(window), ["a", "b"])  # a needs input -> top section
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

    def test_detach_restores_the_configured_window_height(self):
        # Regression: docking hides the titlebar to shrink to the bar. Detaching
        # shows it again, which re-negotiates the window to its natural (short)
        # height and silently drops set_collapsed's resize -- so the panel used to
        # restore only ~150px tall. A deferred re-assert (after the frame settles)
        # must bring the full configured height back.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            target = window._settings.height - 40
            window.set_rows([row(session_id="a"), row(session_id="b")])
            window.show_all()
            pump_until(window.get_mapped)
            window._dock_to_edge("left")
            pump_until(lambda: window.get_size()[0] < 120)  # shrunk to the thin bar
            window._undock()
            # The restore is deferred (a GTK timeout) and then WM-mediated, so wait for
            # the height itself rather than for a guessed number of milliseconds.
            pump_until(lambda: window.get_size()[1] >= target)
            _w, height = window.get_size()
            self.assertGreaterEqual(height, target)
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

    def test_the_rounded_shape_is_reapplied_on_allocation_while_docked_only(self):
        # The clip must track the window's REAL size, so it is re-applied on every
        # size-allocate while docked (a WM-settled or font-grown size differs from
        # the requested one) -- and never while undocked.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        window.show_all()
        seen = []
        window._apply_dock_shape = lambda edge: seen.append(edge)
        try:
            window._dock_to_edge("bottom")
            while Gtk.events_pending():
                Gtk.main_iteration()
            self.assertIn("bottom", seen)   # re-clipped at the real docked size
            seen.clear()
            window._undock()
            while Gtk.events_pending():
                Gtk.main_iteration()
            self.assertEqual(seen, [])       # the regrow's allocation must not re-clip
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
            self.assertFalse(self._first_row(status_win).ccnav_grip.get_visible())
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
            # input -> working -> reported (the Sort-by-Status section order)
            self.assertEqual(self._display_ids(window), ["i", "w", "r"])
        finally:
            window.destroy()

    def test_acked_reported_session_moves_to_the_acked_section(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([
                row(session_id="r1", state=hookstate.WAITING, reason="idle", updated_at=10),
                row(session_id="r2", state=hookstate.WAITING, reason="idle", updated_at=20)])
            self.assertEqual(self._display_ids(window), ["r2", "r1"])  # both reported, r2 newer on top
            self.assertEqual(self._display_sections(window), ["reported"])
            window._acknowledged.add("r2")  # mark r2 seen -> a check
            window._regroup_now()
            # r2 acknowledged -> the '확인 완료' (acked) section, which sits BELOW reported
            self.assertEqual(self._display_sections(window), ["reported", "acked"])
            self.assertEqual(self._display_ids(window), ["r1", "r2"])  # r1 reported, then r2 acked
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

    def test_group_order_is_stable_when_a_response_bumps_recency(self):
        # The whole point of #2: once groups are placed, a later response in a
        # lower group must NOT lift it above the others. Group order is by
        # appearance and frozen, not by recency.
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            # alpha's session is idle (100); beta's is still working (90).
            window.set_rows([
                row(session_id="a1", cwd="/p/alpha",
                    state=hookstate.WAITING, reason="idle", updated_at=100),
                row(session_id="b1", cwd="/p/beta", state=hookstate.WORKING, updated_at=90)])
            self.assertEqual(self._display_groups(window), ["/p/alpha", "/p/beta"])
            # beta's session finishes its turn -- a real response (state + reason
            # change, so the row signature changes and the panel re-sections), and
            # its recency now beats alpha's. Group order must NOT follow.
            window.set_rows([
                row(session_id="a1", cwd="/p/alpha",
                    state=hookstate.WAITING, reason="idle", updated_at=100),
                row(session_id="b1", cwd="/p/beta",
                    state=hookstate.WAITING, reason="idle", updated_at=200)])
            self.assertEqual(self._display_groups(window), ["/p/alpha", "/p/beta"])  # unchanged
            self.assertEqual(self._display_ids(window), ["a1", "b1"])
        finally:
            window.destroy()

    def test_a_new_group_appears_at_the_end_regardless_of_recency(self):
        # A group first seen later keeps to the bottom even if its recency is the
        # highest -- appearance order, not recency.
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([row(session_id="a1", cwd="/p/alpha", updated_at=100)])
            window.set_rows([
                row(session_id="a1", cwd="/p/alpha", updated_at=100),
                row(session_id="b1", cwd="/p/beta", updated_at=500)])  # newer, but new
            self.assertEqual(self._display_groups(window), ["/p/alpha", "/p/beta"])
        finally:
            window.destroy()

    def test_reorder_group_moves_a_whole_group(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([
                row(session_id="a1", cwd="/p/alpha", updated_at=100),
                row(session_id="b1", cwd="/p/beta", updated_at=90)])
            self.assertEqual(self._display_groups(window), ["/p/alpha", "/p/beta"])
            window._reorder_group("/p/beta", "/p/alpha")  # drag beta above alpha
            self.assertEqual(self._display_groups(window), ["/p/beta", "/p/alpha"])
            self.assertEqual(self._display_ids(window), ["b1", "a1"])  # sessions follow
            window._reorder_group("/p/beta", "/p/alpha", after=True)  # drop beta below alpha
            self.assertEqual(self._display_groups(window), ["/p/alpha", "/p/beta"])
        finally:
            window.destroy()

    def test_reorder_group_ignores_self_and_unknown_keys(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([
                row(session_id="a1", cwd="/p/alpha", updated_at=100),
                row(session_id="b1", cwd="/p/beta", updated_at=90)])
            window._reorder_group("/p/alpha", "/p/alpha")  # self -> no-op
            window._reorder_group("/p/zzz", "/p/alpha")    # unknown dragged -> no-op
            self.assertEqual(self._display_groups(window), ["/p/alpha", "/p/beta"])
        finally:
            window.destroy()

    def test_dropping_a_group_header_on_another_reorders_via_the_receiver(self):
        # The drop receiver must tell a GROUP drop (reorder groups) from a SESSION
        # drop (join group) by the target info id.
        from ccnav import config

        class _Sel:
            def __init__(self, data):
                self._data = data

            def get_data(self):
                return self._data

        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([
                row(session_id="a1", cwd="/p/alpha", updated_at=100),
                row(session_id="b1", cwd="/p/beta", updated_at=90)])
            alpha_header = next(c for c in window._listbox.get_children()
                                if getattr(c, "ccnav_is_header", False)
                                and c.ccnav_group == "/p/alpha")
            # Drop beta's header on alpha's (upper half) -> beta lands before alpha.
            window._on_group_header_drag_received(
                alpha_header, None, 0, 0, _Sel(b"/p/beta"), ui._DRAG_INFO_GROUP, 0)
            self.assertEqual(self._display_groups(window), ["/p/beta", "/p/alpha"])
        finally:
            window.destroy()

    def test_auto_sort_resets_the_manual_group_order(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([
                row(session_id="a1", cwd="/p/alpha", updated_at=100),
                row(session_id="b1", cwd="/p/beta", updated_at=90)])
            window._reorder_group("/p/beta", "/p/alpha")  # beta now first
            self.assertEqual(self._display_groups(window), ["/p/beta", "/p/alpha"])
            window._on_auto_sort_clicked(None)
            self.assertEqual(self._display_groups(window), ["/p/alpha", "/p/beta"])  # default restored
        finally:
            window.destroy()

    def test_group_header_count_icons_order_and_check_count(self):
        # #1: the header count icons read red(input) > blue(working) > green ●
        # (reported, unacked) > green ✓ (acked), and the ✓ count follows the
        # acknowledged set (a seen reported session moves from ● to ✓).
        import re
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), sort_mode="group"))
        try:
            window.set_rows([
                row(session_id="i1", cwd="/p/x", state=hookstate.WAITING, reason="permission_prompt"),
                row(session_id="i2", cwd="/p/x", state=hookstate.WAITING, reason="permission_prompt"),
                row(session_id="w1", cwd="/p/x", state=hookstate.WORKING),
                row(session_id="r1", cwd="/p/x", state=hookstate.WAITING, reason="idle"),
                row(session_id="r2", cwd="/p/x", state=hookstate.WAITING, reason="idle")])
            window._acknowledged.add("r2")  # one reported session marked seen -> a check
            window._regroup_now()
            header = next(c for c in window._listbox.get_children()
                          if getattr(c, "ccnav_is_header", False) and c.ccnav_group == "/p/x")
            m = header.ccnav_counts_label.get_label()
            pairs = re.findall(r'foreground="(#[0-9a-fA-F]{6})">(\S)</span>\s*(\d+)', m)
            self.assertEqual(pairs, [
                ("#e01b24", "●", "2"),   # red input
                ("#3584e4", "↻", "1"),   # blue working
                ("#2ec27e", "●", "1"),   # green reported, unacked (r1)
                ("#2ec27e", "✓", "1"),   # green check, acked (r2)
            ])
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
            populated = self._widgets_of_type(window._build_group_header_row("/p/proj"), Gtk.Label)
            # The blank ("~") group omits the empty path line, so it carries exactly
            # one fewer label than a populated group (both have the grip + name +
            # counts; only the populated one adds a path line).
            self.assertEqual(len(populated) - len(blank), 1)
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

    def test_status_mode_uses_status_section_headers_not_group_headers(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a", cwd="/p/x")])
            headers = [c for c in window._listbox.get_children()
                       if getattr(c, "ccnav_is_header", False)]
            self.assertTrue(headers)  # Status mode now has section header rows
            self.assertTrue(all(hasattr(h, "ccnav_section") for h in headers))  # status headers
            self.assertFalse(any(hasattr(h, "ccnav_group") for h in headers))   # not group headers
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

    def test_collapsing_a_status_section_filters_its_sessions_but_keeps_the_header(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)  # status mode
        try:
            window.set_rows([
                row(session_id="i", state=hookstate.WAITING, reason="permission_prompt"),
                row(session_id="w", state=hookstate.WORKING)])
            i = self._row_by_id(window, "i")
            self.assertTrue(window._filter_row(i))            # visible before collapse
            window._on_status_toggle(None, model.INPUT_NEEDED)  # collapse the input section
            self.assertFalse(window._filter_row(i))           # its session now filtered out
            self.assertIn(model.INPUT_NEEDED, self._display_sections(window))  # header row survives
            self.assertIn(model.INPUT_NEEDED, window._collapsed_status)
            self.assertTrue(window._filter_row(self._row_by_id(window, "w")))  # other section unaffected
            window._on_status_toggle(None, model.INPUT_NEEDED)  # expand again
            self.assertTrue(window._filter_row(i))
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
            self.assertTrue(any(hasattr(c, "ccnav_group")
                                for c in window._listbox.get_children()))  # group headers present
            window._sort_combo.set_active_id("status")  # -> group headers removed
            self.assertFalse(any(hasattr(c, "ccnav_group")
                                 for c in window._listbox.get_children()))  # no group headers left
        finally:
            config.config_path = orig
            window.destroy()

    def test_changing_only_sort_mode_does_not_reposition_the_window(self):
        # #3: switching sort mode goes through apply_settings, which must NOT yank
        # the window back to its configured corner -- the user may have dragged it.
        # A real geometry change (corner/size) still repositions.
        from ccnav import config
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            calls = []
            window._apply_geometry = lambda s: calls.append(s)  # spy on repositioning
            window.apply_settings(config.with_updates(window._settings, sort_mode="group"))
            self.assertEqual(calls, [])  # sort-mode-only change: window stays put
            window.apply_settings(config.with_updates(window._settings, corner="bottom-left"))
            self.assertEqual(len(calls), 1)  # a corner change DOES reposition
        finally:
            window.destroy()

    def test_status_header_row_shows_the_section_title_and_count(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="i", state=hookstate.WAITING,
                                 reason="permission_prompt")])
            header = window._build_status_header_row(model.INPUT_NEEDED)
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
            child = self._first_row(window)
            spinner = self._front(child)  # the working front spinner (not the back layer)
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
            child = self._first_row(window)
            window.set_rows([row(session_id="a", pane="%9")])  # pane changed -> update
            self.assertIs(self._first_row(window), child)
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
            child = self._first_row(window)
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
            _a, b = self._session_children(window)
            window._listbox.unselect_all()
            self._click(window, b)
            self.assertIs(window._listbox.get_selected_row(), b)  # stays expanded
        finally:
            window.destroy()

    def test_reclicking_selected_row_collapses_it(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a")])
            child = self._first_row(window)
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
            child = self._first_row(window)
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
            child = self._first_row(window)
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
            child = self._first_row(window)
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
            # Clearing both drops the USER overrides but leaves the built-in
            # theme: the custom colour and font size are gone, yet the provider
            # still carries the theme (which always sets a background-color).
            window.apply_settings(config.Settings(font_size=0, bg_color=""))
            cleared = window._css.to_string()
            self.assertNotIn("15pt", cleared)
            self.assertNotIn("rgb(18,52,86)", cleared)  # the user's custom bg is gone
            self.assertIn("background-color", cleared)   # theme background remains
        finally:
            window.destroy()

    def test_transient_status_arms_and_clears(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.Settings(),
        )
        try:
            window.set_status("답장을 지원하지 않습니다")
            self.assertEqual(window._transient, "답장을 지원하지 않습니다")
            self.assertNotEqual(window._status_clear_source, 0, "a clear timer must be armed")
            window._clear_transient()
            self.assertEqual(window._transient, "")
            self.assertEqual(window._status_clear_source, 0)
            # An empty status cancels any pending timer rather than arming one.
            window.set_status("x")
            window.set_status("")
            self.assertEqual(window._status_clear_source, 0)
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

    def test_settings_dialog_has_a_notifications_toggle_wired_to_the_setting(self):
        from ccnav import config

        def checks(root):
            out = []
            stack = [root]
            while stack:
                w = stack.pop()
                if isinstance(w, Gtk.CheckButton):
                    out.append(w)
                if isinstance(w, Gtk.Container):
                    stack.extend(w.get_children())
            return out

        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.with_updates(config.Settings(), notifications=False),
        )
        try:
            dialog = window._build_settings_dialog()
            try:
                found = [c for c in checks(dialog) if "시스템 알림" in (c.get_label() or "")]
                self.assertEqual(len(found), 1, "exactly one 시스템 알림 toggle")
                toggle = found[0]
                self.assertFalse(toggle.get_active(), "reflects notifications=False")
                toggle.set_active(True)  # emits 'toggled' -> commit
                self.assertTrue(window._settings.notifications)
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
            # The worker raises on a daemon thread and posts the failure back through
            # idle_add; wait for THAT, not for an arbitrary 300ms (which a loaded
            # machine misses -- this was the suite's worst flake).
            pump_until(lambda: "실패" in window._update_status.get_text())
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
            pump_until(lambda: window._update_button.get_sensitive())
            self.assertEqual(window._update_status.get_text(), "이미 최신 버전입니다.")
            self.assertTrue(window._update_button.get_sensitive())
        finally:
            window.destroy()


class UsageButtonTest(unittest.TestCase):
    """The bottom usage button: it must live at the bottom of the content box, fetch
    through the injected loader (never the network), guard against a double click, and
    render either the limit rows or the failure message into its popover."""

    def _widgets_of_type(self, root, cls):
        """Depth-first in child order -- the order the user sees them stacked."""
        found = []

        def walk(w):
            if isinstance(w, cls):
                found.append(w)
            if isinstance(w, Gtk.Container):
                for child in w.get_children():
                    walk(child)

        walk(root)
        return found

    def _popover_text(self, window):
        return " ".join(
            (l.get_text() or "") for l in self._widgets_of_type(window._usage_popover, Gtk.Label))

    def test_the_usage_button_is_the_last_widget_in_the_content_box(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            self.assertIs(window._content.get_children()[-1], window._usage_button)
            self.assertIn("사용량", window._usage_button.get_label())
        finally:
            window.destroy()

    def test_clicking_renders_a_row_per_limit_with_its_percent(self):
        from ccnav import usage
        result = usage.Usage(plan="Max 20x", entries=[
            usage.Entry("세션 (5시간)", 26, "normal", ""),
            usage.Entry("주간 (전체)", 7, "normal", ""),
        ])
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            usage_load=lambda: (result, ""))
        try:
            window._usage_button.emit("clicked")
            # The load runs on a worker thread and posts back via idle_add, which
            # re-enables the button -- wait for that, not for a fixed delay.
            pump_until(window._usage_button.get_sensitive)
            text = self._popover_text(window)
            self.assertIn("Max 20x", text)
            self.assertIn("세션 (5시간)", text)
            self.assertIn("26%", text)
            self.assertIn("7%", text)
            bars = self._widgets_of_type(window._usage_popover, Gtk.LevelBar)
            self.assertEqual([int(b.get_value()) for b in bars], [26, 7])
        finally:
            window.destroy()

    def test_a_failure_shows_the_message_instead_of_raising(self):
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            usage_load=lambda: (None, "인증이 만료되었습니다"))
        try:
            window._usage_button.emit("clicked")
            pump_until(window._usage_button.get_sensitive)
            self.assertIn("인증이 만료되었습니다", self._popover_text(window))
        finally:
            window.destroy()

    def test_a_second_click_while_a_fetch_is_in_flight_does_not_start_another(self):
        import threading
        calls = []
        release = threading.Event()

        def slow_load():
            calls.append(1)
            release.wait(2.0)
            return None, "네트워크"

        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None, usage_load=slow_load)
        try:
            window._usage_button.emit("clicked")
            self.assertFalse(window._usage_button.get_sensitive(), "disabled while in flight")
            window._usage_button.emit("clicked")  # the double click
            release.set()
            pump_until(window._usage_button.get_sensitive)
            self.assertEqual(len(calls), 1, "the loader ran once")
            self.assertTrue(window._usage_button.get_sensitive(), "re-enabled after")
        finally:
            window.destroy()


class BottomBarLayoutTest(unittest.TestCase):
    """The bottom strip must cost only what it shows: the usage button sits right-
    aligned at about a third of the width, and the status label -- empty almost all
    the time -- takes no vertical space until it actually has something to say."""

    def test_the_usage_button_is_right_aligned_and_about_a_third_wide(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.resize(340, 420)
            window.show_all()
            # Wait for a real allocation, not for a guessed delay.
            pump_until(lambda: window._usage_button.get_allocation().width > 1)
            self.assertEqual(window._usage_button.get_halign(), Gtk.Align.END)
            width = window._usage_button.get_allocation().width
            self.assertLess(width, 340 // 2, "must not span the panel")
            self.assertGreater(width, 40, "must still be clickable")
        finally:
            window.destroy()

    def test_an_empty_status_label_takes_no_space(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.show_all()
            pump_until(window.get_mapped)
            self.assertEqual(window._status.get_text(), "")
            self.assertFalse(window._status.get_visible(),
                             "an empty status label must not reserve a row")
        finally:
            window.destroy()

    def test_a_status_message_reveals_the_label(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.show_all()
            pump_until(window.get_mapped)
            window.set_status("이동하지 못했습니다")
            self.assertTrue(window._status.get_visible())
            self.assertIn("이동하지 못했습니다", window._status.get_text())
            window.set_status("")  # and it hides again when cleared
            self.assertFalse(window._status.get_visible())
        finally:
            window.destroy()

    def test_show_all_does_not_reveal_an_empty_status_label(self):
        # show_all() re-reveals every child by default -- the label must opt out, or
        # the row comes back the first time the window is shown again.
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.show_all()
            pump_until(window.get_mapped)
            window.show_all()
            self.assertFalse(window._status.get_visible())
        finally:
            window.destroy()


class UsagePopoverDismissTest(unittest.TestCase):
    """Once the popover is up, a click anywhere else must put it away -- the empty
    strip beside the button, a session row, or the button itself (a toggle). It must
    not depend on GTK's modal grab, which is why each path is asserted here."""

    def _window(self):
        from ccnav import usage
        result = usage.Usage(plan="Max 20x",
                             entries=[usage.Entry("세션 (5시간)", 26, "normal", "")])
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None,
                                    usage_load=lambda: (result, ""))
        window.show_all()
        pump_until(window.get_mapped)
        return window

    def _open(self, window):
        window._usage_button.emit("clicked")
        # popup() is synchronous, but the fetch that fills it is not -- wait for the
        # load to land so the popover is fully up before we try to dismiss it.
        pump_until(lambda: window._usage_popover.get_visible()
                   and window._usage_button.get_sensitive())
        self.assertTrue(window._usage_popover.get_visible(), "precondition: popover is up")

    def _closed(self, window):
        pump_until(lambda: not window._usage_popover.get_visible())

    def test_clicking_the_empty_space_beside_the_button_closes_the_popover(self):
        window = self._window()
        try:
            self._open(window)
            # A press that no child consumes bubbles to the toplevel -- the empty
            # strip beside the button is exactly that.
            window.emit("button-press-event",
                        Gdk.Event.new(Gdk.EventType.BUTTON_PRESS))
            self._closed(window)
            self.assertFalse(window._usage_popover.get_visible())
        finally:
            window.destroy()

    def test_the_popover_does_not_take_a_grab(self):
        # A modal popover grabs the pointer, and while it holds that grab the click
        # that should dismiss it never reaches our handlers -- which is exactly how it
        # got stuck open. Dismissal is ours, so the grab must stay off.
        window = self._window()
        try:
            self.assertFalse(window._usage_popover.get_modal())
        finally:
            window.destroy()

    def test_the_window_actually_listens_for_button_presses(self):
        # Emitting the signal by hand proves the handler works but NOT that a real
        # click ever reaches it: a toplevel's default event mask has no BUTTON_PRESS,
        # so without add_events the dead-space click is silently never delivered.
        # (Found the hard way -- the handler was right and the clicks still did
        # nothing.) Guard the routing, not just the handler.
        window = self._window()
        try:
            mask = window.get_events()
            self.assertTrue(mask & Gdk.EventMask.BUTTON_PRESS_MASK,
                            "the toplevel must ask for button presses")
        finally:
            window.destroy()

    def test_clicking_a_session_row_closes_the_popover(self):
        window = self._window()
        try:
            window.set_rows([row(session_id="a")])
            self._open(window)
            window._listbox.emit("button-press-event",
                                 Gdk.Event.new(Gdk.EventType.BUTTON_PRESS))
            self._closed(window)
            self.assertFalse(window._usage_popover.get_visible())
        finally:
            window.destroy()

    def test_clicking_the_button_again_toggles_the_popover_shut(self):
        loads = []

        def load():
            from ccnav import usage
            loads.append(1)
            return usage.Usage("Max 20x", [usage.Entry("세션 (5시간)", 26, "normal", "")]), ""

        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None,
                                    usage_load=load)
        try:
            window.show_all()
            pump_until(window.get_mapped)
            window._usage_button.emit("clicked")
            pump_until(lambda: window._usage_popover.get_visible()
                       and window._usage_button.get_sensitive())
            self.assertTrue(window._usage_popover.get_visible())
            window._usage_button.emit("clicked")   # the second press closes it
            pump_until(lambda: not window._usage_popover.get_visible())
            self.assertFalse(window._usage_popover.get_visible())
            self.assertEqual(len(loads), 1, "closing must not re-fetch")
        finally:
            window.destroy()


class OptionalCcusageUiTest(unittest.TestCase):
    @staticmethod
    def _widgets(root, cls):
        found = []

        def walk(widget):
            if isinstance(widget, cls):
                found.append(widget)
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    walk(child)

        walk(root)
        return found

    def test_token_row_is_hidden_by_default_and_after_disable(self):
        from ccnav import usage
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            self.assertFalse(window._token_row.get_visible())
            window.set_token_usage(usage.TokenUsage(10.0, 5.0, ""))
            self.assertTrue(window._token_row.get_visible())
            window.set_token_usage(None)
            self.assertFalse(window._token_row.get_visible())
            self.assertFalse(window._token_overlay.get_visible())
        finally:
            window.destroy()

    def test_missing_external_tool_is_shown_without_stale_progress(self):
        from ccnav import usage
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_token_usage(usage.TokenUsage(10.0, 5.0, ""))
            window.set_token_usage(
                usage.TokenUsage(None, None, usage.ERR_CCUSAGE_NOT_INSTALLED))
            self.assertTrue(window._token_row.get_visible())
            self.assertFalse(window._token_overlay.get_visible())
            self.assertIn("직접 설치", window._token_error.get_text())
        finally:
            window.destroy()

    def test_settings_names_the_external_program_and_its_privacy_boundary(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        dialog = window._build_settings_dialog()
        try:
            labels = " ".join(
                widget.get_text() for widget in self._widgets(dialog, Gtk.Label))
            checks = " ".join(
                widget.get_label() or ""
                for widget in self._widgets(dialog, Gtk.CheckButton))
            self.assertIn("ccusage 토큰 비용 계산 사용", checks)
            self.assertIn("별도 설치", labels)
            self.assertIn("로컬 Claude 대화 로그", labels)
            self.assertIn("자동 설치", labels)
            self.assertIn("npx", labels)
        finally:
            dialog.destroy()
            window.destroy()
