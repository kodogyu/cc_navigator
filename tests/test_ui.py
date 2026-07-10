import os
import unittest

from ccnav import hookstate, model, ui

# ui pins the Gtk version on import above, so this bare import is safe here.
from gi.repository import Gtk  # noqa: E402


def row(state=hookstate.WAITING, reason="permission_prompt", message="Allow npm test?",
        cwd="/data/projects/demo_project", session_id="a", title="✳ 작업 중",
        last_prompt=""):
    return model.Row(
        session_id=session_id, socket="/tmp/s", pane="%1", tmux_session="demo",
        title=title, state=state, reason=reason,
        message=message, cwd=cwd, updated_at=1, last_prompt=last_prompt,
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
