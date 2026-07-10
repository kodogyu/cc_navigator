"""The always-on-top overlay. All formatting lives in pure functions above the widgets."""
from __future__ import annotations

import os
import socket
from typing import Callable, List

import gi

# Pin every namespace we import below. Without an explicit version, PyGObject
# emits a PyGIWarning on the first `from gi.repository import <ns>` and prints it
# on every run -- noise that trains everyone to ignore warnings.
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
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
UNREACHABLE_HINT = "tmux %d곳에 연결하지 못해 일부 세션이 목록에서 빠졌을 수 있습니다."

# Resolved once, at import, so it costs nothing per row. Tests inject their own
# via the `hostname` parameter rather than depending on this machine's name.
_HOSTNAME = socket.gethostname()


def primary_line(row: model.Row, hostname: str = None) -> str:
    """The row's headline. Falls back to the tmux session name when the title
    is not a real Claude Code title.

    Right after SessionStart, before Claude Code emits its OSC title, tmux
    reports a plain shell's #{pane_title} as the hostname (Task 5 finding), not
    "". A hostname is a property of the machine, identical across every session,
    so showing it as the headline makes freshly-started sessions indistinguishable
    -- which defeats the panel's only job. The tmux session name is per-session
    and guaranteed non-empty by model.build_rows, so it is a strictly better
    fallback.
    """
    if hostname is None:
        hostname = _HOSTNAME
    title = row.title
    if (
        not title
        or title == row.pane
        or title == hostname
        or title == hostname.split(".")[0]
    ):
        return row.tmux_session
    return title


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
        # None so the very first set_rows always renders; thereafter it holds the
        # signature of the rows on screen so an unchanged tick is a no-op.
        self._signature = None

        self.set_keep_above(True)
        # stick(): appear on EVERY workspace, not just the one it was created on.
        # keep_above alone floats it above other windows but only within its own
        # workspace, so switching workspaces would lose the panel. The panel's
        # whole job is to be reachable from any workspace, so it must be sticky.
        self.stick()
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_default_size(WINDOW_WIDTH, WINDOW_HEIGHT)
        self._move_to_top_right()

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

    def _move_to_top_right(self) -> None:
        """Position in the top-right of the primary monitor.

        Gdk.Screen.get_width() is deprecated and prints a DeprecationWarning on
        every run; monitor geometry is the supported path. Every lookup can
        return None on an odd display setup, so guard each step and simply skip
        the move if anything is unavailable -- a window in the wrong place is a
        nuisance, a NoneType traceback at startup is a dead program.
        """
        display = Gdk.Display.get_default()
        if display is None:
            return
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor is None:
            return
        geo = monitor.get_geometry()
        self.move(geo.x + geo.width - WINDOW_WIDTH - 20, geo.y + 40)

    def _render_status(self) -> None:
        self._status.set_text(compose_status(self._sticky, self._hint, self._transient))

    def set_eval_available(self, available: bool) -> None:
        self._eval_available = available
        self._sticky = "" if available else EVAL_UNAVAILABLE_HINT
        # Reach the buttons that already exist. _build_row reads _eval_available
        # only at construction, so without this a late set_eval_available(False)
        # would leave every visible jump button live: the user clicks it, nothing
        # happens, and nothing explains why -- the project's signature failure.
        for child in self._listbox.get_children():
            child.ccnav_jump.set_sensitive(available)
        self._render_status()

    def set_status(self, text: str) -> None:
        self._transient = text
        self._render_status()

    def set_unreachable(self, count: int) -> None:
        """The hint slot: a poll found `count` sockets that held sessions but did
        not answer. Its own slot so it never clobbers a jump/send status, and it
        clears itself the moment every socket answers again (count == 0)."""
        self._hint = UNREACHABLE_HINT % count if count else ""
        self._render_status()

    def set_row_jump_sensitive(self, session_id: str, sensitive: bool) -> None:
        """Added for Task 10: the smallest accessor that lets Application
        disable one row's jump button while its activation is in flight (so a
        double click cannot start two activations) and re-enable it when the
        result comes back, without Application reaching into the widget tree.
        A no-op if the row is gone (e.g. the session ended mid-jump).
        """
        for child in self._listbox.get_children():
            if child.ccnav_row.session_id == session_id:
                child.ccnav_jump.set_sensitive(sensitive)
                return

    def set_rows(self, rows: List[model.Row]) -> None:
        # Task 10 calls this on a one-second timer. Rebuilding the list destroys
        # every child, including the Gtk.Entry the user is typing a reply into,
        # so we must not rebuild when nothing the user can see has changed.
        # updated_at is excluded on purpose: the hook never bumps a timestamp
        # without also changing state or reason, and a reordering changes the
        # tuple order anyway, so ordering is still covered.
        signature = tuple(
            (r.session_id, r.socket, r.pane, r.tmux_session, r.title,
             r.state, r.reason, r.message, r.cwd)
            for r in rows
        )
        if signature == self._signature:
            # _hint is a pure function of emptiness, and emptiness is encoded in
            # the signature (the empty tuple iff rows is empty). An unchanged
            # signature therefore means _hint is already correct, so the early
            # return cannot strand a stale hint.
            return
        self._signature = signature

        # Preserve what the user was doing across a rebuild that does happen:
        # the selected session, its half-typed text, and whether it held focus.
        restore_id = None
        restore_text = ""
        restore_focus = False
        selected = self._listbox.get_selected_row()
        if selected is not None:
            restore_id = selected.ccnav_row.session_id
            restore_text = selected.ccnav_entry.get_text()
            restore_focus = selected.ccnav_entry.has_focus()

        for child in self._listbox.get_children():
            self._listbox.remove(child)
        for row in rows:
            self._listbox.add(self._build_row(row))
        self._hint = "" if rows else EMPTY_HINT
        self._render_status()
        self._listbox.show_all()

        if restore_id is not None:
            for child in self._listbox.get_children():
                if child.ccnav_row.session_id == restore_id:
                    # select_row re-reveals the row through _on_row_selected.
                    self._listbox.select_row(child)
                    child.ccnav_entry.set_text(restore_text)
                    child.ccnav_entry.set_position(-1)
                    if restore_focus:
                        child.ccnav_entry.grab_focus()
                        child.ccnav_entry.set_position(-1)
                    break
        # If that session is gone, nothing is restored.

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
        title.set_markup("<b>%s</b>" % GLib.markup_escape_text(primary_line(row)))
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
        # Stash the widgets set_rows and set_eval_available need so they can find
        # them without walking the widget tree. ccnav_row was removed as unused in
        # a pre-flight plan review; it is used now -- set_rows matches on
        # ccnav_row.session_id to restore the selection across a rebuild.
        list_row.ccnav_revealer = revealer  # type: ignore[attr-defined]
        list_row.ccnav_row = row  # type: ignore[attr-defined]
        list_row.ccnav_entry = entry  # type: ignore[attr-defined]
        list_row.ccnav_jump = jump  # type: ignore[attr-defined]

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
