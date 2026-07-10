"""The always-on-top overlay. All formatting lives in pure functions above the widgets."""
from __future__ import annotations

import os
from typing import Callable, List

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import model  # noqa: E402

SECONDARY_LIMIT = 80
WINDOW_WIDTH = 340
WINDOW_HEIGHT = 420
EMPTY_HINT = (
    "세션이 없습니다. tmux 안에서 실행 중이고 훅이 설치되었는지 "
    "확인하세요 (bin/cc-navigator-doctor)."
)
EVAL_UNAVAILABLE_HINT = "GNOME Shell Eval을 쓸 수 없어 '이동'이 비활성화되었습니다."


def secondary_line(row: model.Row) -> str:
    if row.waiting:
        parts = [part for part in (row.reason, row.message) if part]
        return " — ".join(parts)[:SECONDARY_LIMIT]
    return os.path.basename(row.cwd.rstrip("/"))


def compose_status(sticky: str, hint: str, transient: str) -> str:
    """Three independent slots that must not clobber one another."""
    return "  ".join(part for part in (sticky, hint, transient) if part)


class NavigatorWindow(Gtk.Window):
    def __init__(
        self,
        on_jump: Callable[[model.Row], None],
        on_send: Callable[[model.Row, str], None],
    ) -> None:
        super().__init__(title="cc_navigator")
        self._on_jump = on_jump
        self._on_send = on_send
        self._eval_available = True
        self._sticky = ""
        self._hint = ""
        self._transient = ""

        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_default_size(WINDOW_WIDTH, WINDOW_HEIGHT)
        screen = Gdk.Screen.get_default()
        if screen is not None:
            self.move(screen.get_width() - WINDOW_WIDTH - 20, 40)

        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._listbox.connect("row-selected", self._on_row_selected)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self._listbox)

        self._status = Gtk.Label(xalign=0.0)
        self._status.set_line_wrap(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.pack_start(scroller, True, True, 0)
        box.pack_start(self._status, False, False, 4)
        self.add(box)
        # No destroy -> Gtk.main_quit here: app.main() owns the main loop.

    def _render_status(self) -> None:
        self._status.set_text(compose_status(self._sticky, self._hint, self._transient))

    def set_eval_available(self, available: bool) -> None:
        self._eval_available = available
        self._sticky = "" if available else EVAL_UNAVAILABLE_HINT
        self._render_status()

    def set_status(self, text: str) -> None:
        self._transient = text
        self._render_status()

    def set_rows(self, rows: List[model.Row]) -> None:
        for child in self._listbox.get_children():
            self._listbox.remove(child)
        for row in rows:
            self._listbox.add(self._build_row(row))
        self._hint = "" if rows else EMPTY_HINT
        self._render_status()
        self._listbox.show_all()

    def _build_row(self, row: model.Row) -> Gtk.ListBoxRow:
        list_row = Gtk.ListBoxRow()

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dot = Gtk.Label()
        dot.set_markup(
            '<span foreground="#e01b24">●</span>'
            if row.waiting
            else '<span foreground="#77767b">○</span>'
        )
        header.pack_start(dot, False, False, 0)

        if row.waiting:
            badge = Gtk.Label()
            badge.set_markup('<small><b>Waiting input</b></small>')
            header.pack_start(badge, False, False, 0)

        title = Gtk.Label(xalign=0.0)
        title.set_markup("<b>%s</b>" % GLib.markup_escape_text(row.title))
        title.set_ellipsize(Pango.EllipsizeMode.END)
        header.pack_start(title, True, True, 0)

        secondary = Gtk.Label(xalign=0.0)
        secondary.set_markup(
            '<small><span foreground="#77767b">%s</span></small>'
            % GLib.markup_escape_text(secondary_line(row))
        )
        secondary.set_ellipsize(Pango.EllipsizeMode.END)

        entry = Gtk.Entry()
        entry.set_placeholder_text("입력 후 Enter")
        entry.connect("activate", self._on_entry_activate, row)

        jump = Gtk.Button(label="해당 세션으로 이동")
        jump.set_sensitive(self._eval_available)
        jump.connect("clicked", self._on_jump_clicked, row)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        actions.pack_start(entry, True, True, 0)
        actions.pack_start(jump, False, False, 0)

        revealer = Gtk.Revealer()
        revealer.add(actions)
        list_row.ccnav_revealer = revealer  # type: ignore[attr-defined]

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        body.set_margin_top(6)
        body.set_margin_bottom(6)
        body.set_margin_start(8)
        body.set_margin_end(8)
        body.pack_start(header, False, False, 0)
        body.pack_start(secondary, False, False, 0)
        body.pack_start(revealer, False, False, 0)
        list_row.add(body)
        return list_row

    def _on_row_selected(self, _listbox, selected) -> None:
        for child in self._listbox.get_children():
            revealer = getattr(child, "ccnav_revealer", None)
            if revealer is not None:
                revealer.set_reveal_child(child is selected)

    def _on_jump_clicked(self, _button, row: model.Row) -> None:
        self._on_jump(row)

    def _on_entry_activate(self, entry: Gtk.Entry, row: model.Row) -> None:
        text = entry.get_text()
        if not text.strip():
            return
        entry.set_text("")
        self._on_send(row, text)
