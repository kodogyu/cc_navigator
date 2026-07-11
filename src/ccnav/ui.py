"""The always-on-top overlay. All formatting lives in pure functions above the widgets."""
from __future__ import annotations

import math
import os
import socket
import threading
from typing import Callable, List

import cairo
import gi

# Pin every namespace we import below. Without an explicit version, PyGObject
# emits a PyGIWarning on the first `from gi.repository import <ns>` and prints it
# on every run -- noise that trains everyone to ignore warnings.
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango  # noqa: E402

from . import config, hookstate, model, updater, wiring  # noqa: E402

_CORNER_LABELS = {
    "top-right": "오른쪽 위",
    "top-left": "왼쪽 위",
    "bottom-right": "오른쪽 아래",
    "bottom-left": "왼쪽 아래",
}

SECONDARY_LIMIT = 80
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


def _oneline(text: str) -> str:
    """Flatten a free-text field to a single, control-free line for the panel's
    one-line-per-field layout. The hook already stores flattened records, but a
    label built straight from a Row is the last line of defence -- an older or
    hand-edited record whose prompt/message/reason still holds embedded newlines
    (a pasted diff, a raw task-notification blob) must not render as many raw
    lines and break the row. Non-whitespace control chars are dropped too: a NUL
    would truncate the Pango label at the byte and an ESC would garble it, both
    silently losing content. Printable text (incl. CJK/Hangul) is kept; every
    whitespace run then collapses to one space.
    """
    cleaned = "".join(ch for ch in text if ch.isprintable() or ch.isspace())
    return " ".join(cleaned.split())


def dot_state(row: model.Row) -> str:
    """Which status indicator a row shows:
    - 'working'  -- Claude is running a turn (shown as a spinner);
    - 'input'    -- Claude is blocking on the user, a permission/question/plan
                    prompt (a red dot);
    - 'reported' -- Claude finished its turn and is idle, not blocking (green).

    WORKING is the only non-waiting state, and a waiting row always carries a
    reason -- 'idle' only for a Stop -- so reason 'idle' is the reported case
    and every other waiting reason means Claude wants an answer now.
    """
    if not row.waiting:
        return "working"
    if row.reason == hookstate.STOP_IDLE:
        return "reported"
    return "input"


# The "working" indicator: two curved arrows (a reload/refresh glyph) that spin.
# Drawn with cairo rather than a font glyph so it renders identically everywhere
# and can rotate smoothly.
_WORKING_COLOUR = (0.208, 0.518, 0.894)  # #3584e4
_WORKING_SIZE = 16
_WORKING_PERIOD_MS = 80
_WORKING_STEP = 0.30  # radians per tick


def _draw_reload_spinner(widget: Gtk.Widget, cr: "cairo.Context", angle: float) -> None:
    """Draw two curved arrows chasing each other around a circle, rotated by
    `angle`. Two ~150-degree arcs on opposite sides, each capped by a triangular
    arrowhead pointing clockwise along the arc."""
    w = widget.get_allocated_width() or _WORKING_SIZE
    h = widget.get_allocated_height() or _WORKING_SIZE
    size = min(w, h)
    r = size * 0.30
    head = size * 0.24
    cr.save()
    cr.translate(w / 2.0, h / 2.0)
    cr.rotate(angle)
    cr.set_source_rgb(*_WORKING_COLOUR)
    cr.set_line_width(max(1.5, size * 0.12))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    for base in (0.0, math.pi):
        a0 = base + 0.42
        a1 = base + math.pi - 0.42
        cr.new_sub_path()
        cr.arc(0, 0, r, a0, a1)
        cr.stroke()
        # Arrowhead at a1 -- the clockwise-leading tip of the arc.
        px, py = r * math.cos(a1), r * math.sin(a1)
        tx, ty = -math.sin(a1), math.cos(a1)   # clockwise tangent
        nx, ny = math.cos(a1), math.sin(a1)    # radial outward
        cr.move_to(px + tx * head, py + ty * head)  # tip, ahead along the arc
        cr.line_to(px - tx * head * 0.2 + nx * head * 0.75,
                   py - ty * head * 0.2 + ny * head * 0.75)
        cr.line_to(px - tx * head * 0.2 - nx * head * 0.75,
                   py - ty * head * 0.2 - ny * head * 0.75)
        cr.close_path()
        cr.fill()
    cr.restore()


def _build_working_arrow() -> Gtk.DrawingArea:
    """Two curved arrows that spin while Claude works. One self-cleaning GLib
    timeout advances the rotation and drops itself the moment the widget is no
    longer inside a list -- which covers BOTH the row being removed AND the
    indicator being swapped out for a dot in place. Keying the stop on the
    widget's own ancestry (not its row's) is what stops a working->waiting flip
    from orphaning the timer. The current angle is exposed for tests."""
    area = Gtk.DrawingArea()
    area.set_size_request(_WORKING_SIZE, _WORKING_SIZE)
    area.ccnav_angle = 0.0  # type: ignore[attr-defined]

    def on_draw(widget, cr):
        _draw_reload_spinner(widget, cr, widget.ccnav_angle)
        return False

    area.connect("draw", on_draw)

    def tick() -> bool:
        try:
            if area.get_ancestor(Gtk.ListBox) is None:
                return False  # detached (swap) or its row left the list -> stop
            area.ccnav_angle = (area.ccnav_angle + _WORKING_STEP) % (2 * math.pi)
            area.queue_draw()
            return True
        except Exception:  # noqa: BLE001 -- widget torn down mid-animation
            return False

    GLib.timeout_add(_WORKING_PERIOD_MS, tick)
    return area


def _app_icon_pixbuf(size):
    """The app icon (icons/window_icon.png) scaled to `size` px, or None if the
    asset is missing or unreadable -- a missing icon must never stop the panel
    from opening, so every failure degrades to no icon."""
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[2] / "icons" / "window_icon.png"
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), size, size)
    except Exception:  # noqa: BLE001 -- GLib.Error on a missing/corrupt asset
        return None


# The per-field markup, shared by _build_row (first render) and _update_row
# (in-place refresh). Keeping them in one place means a rebuilt row and an
# updated row can never render the same field two different ways.
def _title_markup(row: model.Row) -> str:
    return "<b>%s</b>" % GLib.markup_escape_text(_oneline(primary_line(row)))


def _secondary_markup(row: model.Row) -> str:
    return ('<small><span foreground="#77767b">%s</span></small>'
            % GLib.markup_escape_text(_oneline(secondary_line(row))))


def _path_markup(row: model.Row) -> str:
    return ('<small><span foreground="#77767b">%s</span></small>'
            % GLib.markup_escape_text(_oneline(row.cwd)))


def _prompt_markup(prompt: str) -> str:
    return "<small>%s</small>" % GLib.markup_escape_text(prompt)


def _meta_markup(row: model.Row) -> str:
    state_line = _oneline(row.state + (" · " + row.reason if row.reason else ""))
    return ('<small><span foreground="#77767b">%s</span></small>'
            % GLib.markup_escape_text(state_line))


def _build_indicator(kind: str) -> Gtk.Widget:
    """The status widget for a row: a rotating arrow while working, else a
    coloured dot (green 'reported', red 'input')."""
    if kind == "working":
        return _build_working_arrow()
    dot = Gtk.Label()
    colour = "#2ec27e" if kind == "reported" else "#e01b24"
    dot.set_markup('<span foreground="%s">●</span>' % colour)
    return dot


def _row_signature(row: model.Row):
    """The fields whose change the user can see. Excludes updated_at on purpose:
    the hook never bumps a timestamp without also changing state or reason."""
    return (row.session_id, row.socket, row.pane, row.tmux_session, row.title,
            row.state, row.reason, row.message, row.cwd, row.last_prompt)


class NavigatorWindow(Gtk.Window):
    def __init__(
        self,
        on_jump: Callable[[model.Row], None],
        on_send: Callable[[model.Row, str], None],
        settings: "config.Settings" = None,
        on_settings_changed: Callable[["config.Settings"], None] = None,
        on_refresh: Callable[[], None] = None,
    ) -> None:
        super().__init__(title="cc_navigator")
        self._on_jump = on_jump
        self._on_send = on_send
        self._on_settings_changed = on_settings_changed
        self._on_refresh = on_refresh
        self._settings = settings or config.Settings()
        self._eval_available = True
        self._sticky = ""
        self._hint = ""
        self._transient = ""
        # None so the very first set_rows always renders; thereafter it holds the
        # signature of the rows on screen so an unchanged tick is a no-op.
        self._signature = None
        # Wiring seams: None -> the wiring module's real defaults (HOME-relative).
        # Tests redirect these at temp dirs so the toggles never touch real state.
        self._wiring_apps_dir = None      # None -> wiring's default (~/.local/share)
        self._wiring_autostart_dir = None
        self._wiring_settings_path = None  # None -> ~/.claude/settings.json
        # Seams so tests drive the update button without git or re-exec.
        self._updater_update = updater.update
        self._updater_restart = updater.restart

        # A HeaderBar so a settings gear can sit next to the window close button,
        # as asked. This makes the titlebar client-side (GTK-drawn) rather than
        # the WM default, which is the price of putting our own button up there.
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        # No header.set_title(): the custom title below replaces the built-in
        # title label. The window/taskbar title comes from super().__init__.
        # Put the app icon to the LEFT of the name via a custom title box (the
        # default HeaderBar title is centred text with no room for an icon).
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon_pixbuf = _app_icon_pixbuf(18)
        if icon_pixbuf is not None:
            title_box.pack_start(Gtk.Image.new_from_pixbuf(icon_pixbuf), False, False, 0)
        title_name = Gtk.Label()
        title_name.set_markup("<b>cc_navigator</b>")
        title_box.pack_start(title_name, False, False, 0)
        title_box.show_all()
        header.set_custom_title(title_box)
        # The window icon (alt-tab / task switcher) uses the same asset.
        window_icon = _app_icon_pixbuf(48)
        if window_icon is not None:
            self.set_icon(window_icon)
        gear = Gtk.Button()
        gear.set_relief(Gtk.ReliefStyle.NONE)
        gear.add(Gtk.Image.new_from_icon_name("emblem-system-symbolic", Gtk.IconSize.BUTTON))
        gear.set_tooltip_text("설정")
        gear.connect("clicked", self._on_settings_clicked)
        header.pack_end(gear)

        refresh = Gtk.Button()
        refresh.set_relief(Gtk.ReliefStyle.NONE)
        refresh.add(Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON))
        refresh.set_tooltip_text("새로고침")
        refresh.connect("clicked", self._on_refresh_clicked)
        header.pack_end(refresh)
        self._refresh_button = refresh

        collapse = Gtk.ToggleButton()
        collapse.set_relief(Gtk.ReliefStyle.NONE)
        collapse.add(Gtk.Image.new_from_icon_name("pan-up-symbolic", Gtk.IconSize.BUTTON))
        collapse.set_tooltip_text("접기")
        collapse.connect("toggled", self._on_collapse_toggled)
        header.pack_start(collapse)
        self._collapse_button = collapse

        self.set_titlebar(header)

        # Font override lives in a CSS provider scoped to this window by a class,
        # so changing the panel's font size never touches any other application.
        self.get_style_context().add_class("ccnav")
        self._css = Gtk.CssProvider()
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(
                screen, self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)

        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        # The listbox owns display order via a sort func over model.sort_key, so
        # set_rows can reconcile in place (never destroying widgets) and still
        # keep waiting sessions on top: after updating rows, invalidate_sort()
        # re-sorts the survivors without a rebuild.
        self._listbox.set_sort_func(self._sort_rows)
        self._listbox.connect("row-selected", self._on_row_selected)
        self._listbox.connect("row-activated", self._on_row_activated)
        # row-selected fires BEFORE row-activated, so by the time the activation
        # arrives get_selected_row() already points at the just-clicked row --
        # a first click is then indistinguishable from a re-click. Capture the
        # selection at button-press, before GTK moves it, so _on_row_activated
        # can tell them apart and only collapse a genuine re-click.
        self._pre_press_selected = None
        self._listbox.connect("button-press-event", self._on_listbox_button_press)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self._listbox)

        self._status = Gtk.Label(xalign=0.0)
        self._status.set_line_wrap(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.pack_start(scroller, True, True, 0)
        box.pack_start(self._status, False, False, 4)
        self._content = box
        self.add(box)
        # Default state is expanded: without this, box.get_visible() is False
        # until something shows it, and the collapse toggle would have nothing
        # correct to restore to. Application.main() still calls window.show_all()
        # to reveal every child widget on screen; this only pins the box itself.
        self._content.show()
        # No destroy -> Gtk.main_quit here: app.main() owns the main loop.

        # Apply the saved settings once everything exists: this sets size,
        # position, keep-above/sticky and font from one place, so the very
        # first paint already reflects the user's config rather than defaults.
        self.apply_settings(self._settings)

    def apply_settings(self, settings: "config.Settings") -> None:
        """Make the live window match `settings`. Idempotent, so it serves both
        the first paint and every later change from the settings dialog."""
        self._settings = settings

        self.set_keep_above(settings.keep_above)
        # stick(): appear on EVERY workspace, not just the one it was created on.
        # keep_above alone floats it above other windows but only within its own
        # workspace, so switching workspaces would lose the panel. unstick()
        # reverses it when the user turns the option off.
        if settings.all_workspaces:
            self.stick()
        else:
            self.unstick()

        # Honour a live collapse: applying a settings change while collapsed
        # must not re-grow the frame over the still-hidden body (which would
        # leave a tall, empty titlebar while the toggle still reads "collapsed").
        height = 1 if self._collapse_button.get_active() else settings.height
        self.resize(settings.width, height)
        self._apply_geometry(settings)
        Gtk.Widget.set_opacity(self, settings.opacity)
        self._apply_css(settings)

    def _apply_geometry(self, settings: "config.Settings") -> None:
        """Pin the window to the chosen corner of the primary monitor.

        Every lookup can return None on an odd display setup, so guard each step
        and simply skip the move if anything is unavailable -- a window in the
        wrong place is a nuisance, a NoneType traceback at startup is a dead
        program. Gdk.Screen.get_width() is deprecated (prints a warning every
        run); monitor geometry is the supported path.
        """
        display = Gdk.Display.get_default()
        if display is None:
            return
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor is None:
            return
        geo = monitor.get_geometry()
        margin = 20
        top = geo.y + margin + 20  # a little extra below the top bar
        bottom = geo.y + geo.height - settings.height - margin
        left = geo.x + margin
        right = geo.x + geo.width - settings.width - margin
        positions = {
            "top-right": (right, top),
            "top-left": (left, top),
            "bottom-right": (right, bottom),
            "bottom-left": (left, bottom),
        }
        x, y = positions.get(settings.corner, positions["top-right"])
        self.move(x, y)

    def _apply_css(self, settings: "config.Settings") -> None:
        """Scale the panel's font and tint its background via the scoped provider.
        Both are optional: font_size 0 and bg_color "" each omit their rule, and
        an empty provider restores the theme. Scoped to .ccnav so no other app is
        touched."""
        parts = []
        if settings.bg_color:
            parts.append(".ccnav { background-color: %s; }" % settings.bg_color)
        if settings.font_size > 0:
            parts.append(".ccnav, .ccnav * { font-size: %dpt; }" % settings.font_size)
        self._css.load_from_data("\n".join(parts).encode("utf-8"))

    def _on_collapse_toggled(self, button: Gtk.ToggleButton) -> None:
        self.set_collapsed(button.get_active())

    def set_collapsed(self, collapsed: bool) -> None:
        """Collapsed hides the body and shrinks the window to its titlebar; the
        panel stays floating and one click brings the list back."""
        image = self._collapse_button.get_child()
        if collapsed:
            self._content.hide()
            image.set_from_icon_name("pan-down-symbolic", Gtk.IconSize.BUTTON)
            self.resize(self._settings.width, 1)  # shrink to titlebar's minimum
        else:
            self._content.show()
            image.set_from_icon_name("pan-up-symbolic", Gtk.IconSize.BUTTON)
            self.resize(self._settings.width, self._settings.height)
        if self._collapse_button.get_active() != collapsed:
            self._collapse_button.set_active(collapsed)

    def _on_refresh_clicked(self, _button) -> None:
        if self._on_refresh is not None:
            self._on_refresh()

    def _on_settings_clicked(self, _button) -> None:
        dialog = self._build_settings_dialog()
        dialog.run()
        dialog.destroy()

    def _on_update_clicked(self, button) -> None:
        """Fetch + fast-forward off the GTK thread (it touches the network and
        disk), then hand the result back with idle_add. On success the process
        re-execs into the new code; otherwise the reason lands in the label.

        The button and its status label are captured here, not read from self in
        the callback: reopening the dialog rebuilds those widgets, so a worker
        started for an old dialog must write its result into that dialog's own
        widgets (harmless if it was closed), never into the current one."""
        status = self._update_status
        button.set_sensitive(False)
        status.set_text("업데이트 확인 중…")

        def worker() -> None:
            try:
                updated, message = self._updater_update()
            except Exception as exc:  # noqa: BLE001 -- must never crash the panel
                updated, message = False, "업데이트 확인 실패: %s" % exc
            GLib.idle_add(self._apply_update_result, updated, message, button, status)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_update_result(self, updated: bool, message: str, button, status) -> bool:
        status.set_text(message)
        if updated:
            self._updater_restart()  # replaces the process image; does not return
        else:
            button.set_sensitive(True)
        return False  # one-shot idle source

    def _cc_exec_path(self) -> str:
        # The repo's own launcher, resolved absolutely -- the same basis as
        # _hook_command. The .desktop Exec must point here, NOT at the
        # ~/.local/bin/cc-navigator symlink that only the optional ./install
        # creates: otherwise ticking the launcher/autostart toggles after the
        # documented `./bin/cc-navigator` run writes a desktop entry whose Exec
        # does not exist, yet reports installed -- a silent-success dead link.
        import pathlib
        return str(pathlib.Path(__file__).resolve().parents[2] / "bin" / "cc-navigator")

    def _settings_json_path(self):
        import pathlib
        return self._wiring_settings_path or (
            pathlib.Path(os.path.expanduser("~")) / ".claude" / "settings.json")

    def _hook_command(self) -> str:
        import pathlib
        return str(pathlib.Path(__file__).resolve().parents[2] / "bin" / "cc-navigator-hook")

    def _set_launcher(self, on: bool) -> None:
        if on:
            wiring.install_launcher(self._cc_exec_path(), self._wiring_apps_dir)
        else:
            wiring.remove_launcher(self._wiring_apps_dir)

    def _set_autostart(self, on: bool) -> None:
        wiring.set_autostart(on, self._cc_exec_path(), self._wiring_autostart_dir)

    def _set_hooks(self, on: bool) -> None:
        if on:
            wiring.install_hooks(self._hook_command(), self._settings_json_path())
        else:
            wiring.remove_hooks(self._hook_command(), self._settings_json_path())

    def _build_settings_dialog(self) -> Gtk.Dialog:
        """A live-apply settings dialog: every control writes straight through
        _commit_settings, so the panel updates as the user drags, and there is
        no Apply/Cancel to get out of sync with what is on screen. Closing it
        just dismisses it -- the settings are already saved."""
        s = self._settings
        dialog = Gtk.Dialog(title="cc_navigator 설정", transient_for=self, modal=True)
        dialog.add_button("닫기", Gtk.ResponseType.CLOSE)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        content.add(grid)
        row = 0

        def add_label(text: str) -> None:
            label = Gtk.Label(label=text, xalign=0.0)
            grid.attach(label, 0, row, 1, 1)

        # Polling interval (seconds).
        add_label("폴링 주기 (초)")
        poll = Gtk.SpinButton.new_with_range(config.POLL_MIN, config.POLL_MAX, 0.25)
        poll.set_digits(2)
        poll.set_value(s.poll_seconds)
        poll.connect("value-changed", lambda w: self._commit_settings(
            config.with_updates(self._settings, poll_seconds=w.get_value())))
        grid.attach(poll, 1, row, 1, 1)
        row += 1

        # Corner.
        add_label("창 위치")
        corner = Gtk.ComboBoxText()
        for key in config.CORNERS:
            corner.append(key, _CORNER_LABELS[key])
        corner.set_active_id(s.corner)
        corner.connect("changed", lambda w: self._commit_settings(
            config.with_updates(self._settings, corner=w.get_active_id() or self._settings.corner)))
        grid.attach(corner, 1, row, 1, 1)
        row += 1

        # Width / height.
        add_label("창 너비 (px)")
        width = Gtk.SpinButton.new_with_range(config.WIDTH_MIN, config.WIDTH_MAX, 10)
        width.set_value(s.width)
        width.connect("value-changed", lambda w: self._commit_settings(
            config.with_updates(self._settings, width=int(w.get_value()))))
        grid.attach(width, 1, row, 1, 1)
        row += 1

        add_label("창 높이 (px)")
        height = Gtk.SpinButton.new_with_range(config.HEIGHT_MIN, config.HEIGHT_MAX, 10)
        height.set_value(s.height)
        height.connect("value-changed", lambda w: self._commit_settings(
            config.with_updates(self._settings, height=int(w.get_value()))))
        grid.attach(height, 1, row, 1, 1)
        row += 1

        # Font size, with a 0-means-default checkbox that gates the spinner.
        add_label("글꼴 크기 (pt)")
        font_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        font_default = Gtk.CheckButton(label="시스템 기본")
        font_spin = Gtk.SpinButton.new_with_range(config.FONT_MIN, config.FONT_MAX, 1)
        font_default.set_active(s.font_size == 0)
        font_spin.set_sensitive(s.font_size != 0)
        font_spin.set_value(s.font_size or config.FONT_MIN)

        def commit_font() -> None:
            size = 0 if font_default.get_active() else int(font_spin.get_value())
            self._commit_settings(config.with_updates(self._settings, font_size=size))

        def on_font_default(check) -> None:
            font_spin.set_sensitive(not check.get_active())
            commit_font()

        font_default.connect("toggled", on_font_default)
        font_spin.connect("value-changed", lambda w: commit_font())
        font_box.pack_start(font_default, False, False, 0)
        font_box.pack_start(font_spin, False, False, 0)
        grid.attach(font_box, 1, row, 1, 1)
        row += 1

        # Background colour: a colour button plus a "테마 그대로" clear button.
        add_label("배경색")
        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        color_btn = Gtk.ColorButton()
        if s.bg_color:
            rgba = Gdk.RGBA()
            rgba.parse(s.bg_color)
            color_btn.set_rgba(rgba)

        def on_color(btn):
            rgba = btn.get_rgba()
            hexcolor = "#%02x%02x%02x" % (
                int(round(rgba.red * 255)), int(round(rgba.green * 255)),
                int(round(rgba.blue * 255)))
            self._commit_settings(config.with_updates(self._settings, bg_color=hexcolor))

        clear_btn = Gtk.Button(label="테마 그대로")

        def on_clear(_b):
            self._commit_settings(config.with_updates(self._settings, bg_color=""))

        color_btn.connect("color-set", on_color)
        clear_btn.connect("clicked", on_clear)
        color_box.pack_start(color_btn, False, False, 0)
        color_box.pack_start(clear_btn, False, False, 0)
        grid.attach(color_box, 1, row, 1, 1)
        row += 1

        # Opacity.
        add_label("투명도")
        opacity = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, config.OPACITY_MIN, config.OPACITY_MAX, 0.05)
        opacity.set_value(s.opacity)
        opacity.set_hexpand(True)
        opacity.connect("value-changed", lambda w: self._commit_settings(
            config.with_updates(self._settings, opacity=w.get_value())))
        grid.attach(opacity, 1, row, 1, 1)
        row += 1

        # Keep-above and all-workspaces toggles.
        keep_above = Gtk.CheckButton(label="항상 위에 표시")
        keep_above.set_active(s.keep_above)
        keep_above.connect("toggled", lambda w: self._commit_settings(
            config.with_updates(self._settings, keep_above=w.get_active())))
        grid.attach(keep_above, 1, row, 1, 1)
        row += 1

        all_ws = Gtk.CheckButton(label="모든 워크스페이스에 표시")
        all_ws.set_active(s.all_workspaces)
        all_ws.connect("toggled", lambda w: self._commit_settings(
            config.with_updates(self._settings, all_workspaces=w.get_active())))
        grid.attach(all_ws, 1, row, 1, 1)
        row += 1

        from . import __version__
        # Bottom row: an update button + its status on the left, the version on
        # the right. Same version format as `cc-navigator --version` (app.main),
        # so the CLI and the dialog never disagree on how the version line reads.
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        update_btn = Gtk.Button(label="업데이트 확인")
        update_status = Gtk.Label(xalign=0.0)
        update_status.set_line_wrap(True)
        version_label = Gtk.Label(label="cc-navigator %s" % __version__)
        version_label.get_style_context().add_class("dim-label")
        footer.pack_start(update_btn, False, False, 0)
        footer.pack_start(update_status, True, True, 0)
        footer.pack_end(version_label, False, False, 0)
        content.add(footer)
        self._update_button = update_btn
        self._update_status = update_status
        update_btn.connect("clicked", self._on_update_clicked)

        # Integration: reflect the current on-disk wiring state and let each
        # toggle install/remove it. BOTH the initial state read (is_on) and the
        # setter run under try/except: a corrupt on-disk file (e.g. a non-UTF-8
        # autostart .desktop) must not abort the whole dialog build -- if that
        # read escaped, opening the gear would raise and the entire settings
        # surface (font, opacity, colour, geometry, hooks) would be unreachable.
        integ_status = Gtk.Label(xalign=0.0)
        integ_status.set_line_wrap(True)

        def make_toggle(label_text, is_on, setter):
            btn = Gtk.CheckButton(label=label_text)
            try:
                active = is_on()
            except Exception as exc:  # noqa: BLE001 -- a bad on-disk file must not sink the dialog
                active = False
                integ_status.set_text("상태 확인 실패: %s" % exc)
            btn.set_active(active)
            def on_toggle(w):
                try:
                    setter(w.get_active())
                except Exception as exc:  # noqa: BLE001 -- a toggle must never crash the panel
                    integ_status.set_text("설정 변경 실패: %s" % exc)
            btn.connect("toggled", on_toggle)
            return btn

        integ = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        integ.add(make_toggle(
            "앱 목록에 등록",
            lambda: wiring.launcher_installed(self._wiring_apps_dir),
            self._set_launcher))
        integ.add(make_toggle(
            "로그인 시 자동 실행",
            lambda: wiring.autostart_enabled(self._wiring_autostart_dir),
            self._set_autostart))
        integ.add(make_toggle(
            "Claude Code 훅 설정",
            lambda: wiring.hooks_installed(self._hook_command(), self._settings_json_path()),
            self._set_hooks))
        integ.add(integ_status)

        frame = Gtk.Frame(label="통합")
        frame.add(integ)
        content.add(frame)
        content.reorder_child(footer, -1)  # keep the version line at the very bottom

        dialog.show_all()
        return dialog

    def _commit_settings(self, new: "config.Settings") -> None:
        """Apply a settings change everywhere: to the live window, to disk, and
        to whoever owns the poll loop (via the callback). Persisting on every
        change is cheap (atomic write of a tiny file) and means a crash never
        loses a setting the user just made."""
        self.apply_settings(new)
        try:
            config.save(new)
        except OSError:
            # A settings file we cannot write is a nuisance, not a reason to
            # kill the panel; the change still took effect for this session.
            pass
        if self._on_settings_changed is not None:
            self._on_settings_changed(new)

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
        # Called on a one-second timer. It RECONCILES in place rather than
        # rebuilding: only rows that actually changed are updated, gone rows are
        # removed, new rows are inserted. Rebuilding destroyed every widget --
        # including the Gtk.Entry a reply is being typed into and the row's
        # selection/expansion -- which flashed the whole list and reset the
        # working arrow on every tick (the "flicker" report). Keeping each
        # widget alive means an unchanged row is never touched at all.
        signature = tuple(_row_signature(r) for r in rows)
        if signature == self._signature:
            # _hint is a pure function of emptiness, and emptiness is encoded in
            # the signature (the empty tuple iff rows is empty). An unchanged
            # signature therefore means _hint is already correct, so the early
            # return cannot strand a stale hint.
            return
        self._signature = signature

        existing = {c.ccnav_row.session_id: c for c in self._listbox.get_children()}
        desired_ids = set(r.session_id for r in rows)

        # Remove rows whose session is gone.
        for session_id, child in list(existing.items()):
            if session_id not in desired_ids:
                self._listbox.remove(child)
                del existing[session_id]

        # Add new rows and update changed ones in place. Display order is owned
        # by the listbox sort func (model.sort_key), so position on insert does
        # not matter -- a new row lands in its sorted slot, and invalidate_sort()
        # below re-sorts the rows whose key just changed (e.g. one that flipped
        # to waiting must jump to the top). A session's (waiting, -updated_at)
        # key is volatile, so this re-sort is what keeps the priority order live.
        for row in rows:
            child = existing.get(row.session_id)
            if child is None:
                child = self._build_row(row)
                self._listbox.insert(child, -1)
                child.show_all()
            elif child.ccnav_sig != _row_signature(row):
                self._update_row(child, row)

        self._listbox.invalidate_sort()
        self._hint = "" if rows else EMPTY_HINT
        self._render_status()

    def _sort_rows(self, a: Gtk.ListBoxRow, b: Gtk.ListBoxRow) -> int:
        """Order rows by model.sort_key -- the same key build_rows uses -- so the
        live panel and a fresh build agree. Waiting sessions first, then newest."""
        ka, kb = model.sort_key(a.ccnav_row), model.sort_key(b.ccnav_row)
        return -1 if ka < kb else (1 if ka > kb else 0)

    def _update_row(self, list_row: Gtk.ListBoxRow, row: model.Row) -> None:
        """Refresh one existing row's widgets in place -- no teardown, so its
        entry text, focus, selection and revealed state all survive untouched."""
        list_row.ccnav_row = row
        list_row.ccnav_sig = _row_signature(row)

        new_kind = dot_state(row)
        if (new_kind == "working") != (list_row.ccnav_kind == "working"):
            # working-ness flipped: swap the widget (rotating arrow <-> dot).
            list_row.ccnav_header.remove(list_row.ccnav_indicator)
            indicator = _build_indicator(new_kind)
            list_row.ccnav_header.pack_start(indicator, False, False, 0)
            list_row.ccnav_header.reorder_child(indicator, 0)
            indicator.show()
            list_row.ccnav_indicator = indicator
        elif new_kind != list_row.ccnav_kind:
            # still a dot, only the colour changed (reported <-> input).
            colour = "#2ec27e" if new_kind == "reported" else "#e01b24"
            list_row.ccnav_indicator.set_markup('<span foreground="%s">●</span>' % colour)
        list_row.ccnav_kind = new_kind

        list_row.ccnav_title.set_markup(_title_markup(row))
        list_row.ccnav_secondary.set_markup(_secondary_markup(row))
        list_row.ccnav_path.set_markup(_path_markup(row))
        prompt = _oneline(row.last_prompt)
        list_row.ccnav_prompt.set_markup(_prompt_markup(prompt))
        list_row.ccnav_prompt.set_visible(bool(prompt))
        list_row.ccnav_meta.set_markup(_meta_markup(row))

    def _build_row(self, row: model.Row) -> Gtk.ListBoxRow:
        list_row = Gtk.ListBoxRow()

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        # Status at a glance, by colour/shape only (the old "Waiting input" text
        # is gone): a rotating arrow while Claude works, a red dot when it needs
        # an answer, a green dot when it has reported and is idle.
        kind = dot_state(row)
        indicator = _build_indicator(kind)
        header.pack_start(indicator, False, False, 0)

        title = Gtk.Label(xalign=0.0)
        title.set_markup(_title_markup(row))
        title.set_ellipsize(Pango.EllipsizeMode.END)
        header.pack_start(title, True, True, 0)

        secondary = Gtk.Label(xalign=0.0)
        secondary.set_markup(_secondary_markup(row))
        secondary.set_ellipsize(Pango.EllipsizeMode.END)

        entry = Gtk.Entry()
        entry.set_placeholder_text("입력 후 Enter")
        # Bind the handlers to the ROW WIDGET, not the Row value: _update_row
        # swaps list_row.ccnav_row in place, so reading it at click time always
        # targets the current session (a captured Row would go stale).
        entry.connect("activate", self._on_entry_activate, list_row)

        jump = Gtk.Button(label="해당 세션으로 이동")
        jump.set_sensitive(self._eval_available)
        jump.connect("clicked", self._on_jump_clicked, list_row)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        actions.pack_start(entry, True, True, 0)
        actions.pack_start(jump, False, False, 0)

        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        path_label = Gtk.Label(xalign=0.0)
        path_label.set_markup(_path_markup(row))
        path_label.set_selectable(True)
        path_label.set_line_wrap(True)
        detail.pack_start(path_label, False, False, 0)

        # Always create the prompt label, shown only when there is a prompt, so
        # an in-place update can reveal/hide it without adding/removing a widget.
        prompt = _oneline(row.last_prompt)
        prompt_label = Gtk.Label(xalign=0.0)
        prompt_label.set_markup(_prompt_markup(prompt))
        prompt_label.set_line_wrap(True)
        prompt_label.set_lines(3)
        prompt_label.set_ellipsize(Pango.EllipsizeMode.END)
        prompt_label.set_no_show_all(True)
        prompt_label.set_visible(bool(prompt))
        detail.pack_start(prompt_label, False, False, 0)

        meta = Gtk.Label(xalign=0.0)
        meta.set_markup(_meta_markup(row))
        detail.pack_start(meta, False, False, 0)

        reveal_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        reveal_body.pack_start(detail, False, False, 0)
        reveal_body.pack_start(actions, False, False, 0)

        revealer = Gtk.Revealer()
        revealer.add(reveal_body)
        # Refs so set_rows/_update_row/set_eval_available reach a row's widgets
        # without walking the tree. ccnav_row is the CURRENT Row (swapped in
        # place by _update_row); ccnav_sig gates whether an update is needed.
        list_row.ccnav_revealer = revealer  # type: ignore[attr-defined]
        list_row.ccnav_row = row  # type: ignore[attr-defined]
        list_row.ccnav_entry = entry  # type: ignore[attr-defined]
        list_row.ccnav_jump = jump  # type: ignore[attr-defined]
        list_row.ccnav_header = header  # type: ignore[attr-defined]
        list_row.ccnav_indicator = indicator  # type: ignore[attr-defined]
        list_row.ccnav_kind = kind  # type: ignore[attr-defined]
        list_row.ccnav_title = title  # type: ignore[attr-defined]
        list_row.ccnav_secondary = secondary  # type: ignore[attr-defined]
        list_row.ccnav_path = path_label  # type: ignore[attr-defined]
        list_row.ccnav_prompt = prompt_label  # type: ignore[attr-defined]
        list_row.ccnav_meta = meta  # type: ignore[attr-defined]
        list_row.ccnav_sig = _row_signature(row)  # type: ignore[attr-defined]

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

    def _on_listbox_button_press(self, listbox, _event) -> bool:
        """Runs before GTK moves the selection for this click. Stash what WAS
        selected so _on_row_activated can distinguish a re-click (collapse) from
        a first click (expand). Return False so the click is never swallowed."""
        self._pre_press_selected = listbox.get_selected_row()
        return False

    def _on_row_activated(self, listbox, activated) -> None:
        """A click on the row that was ALREADY selected collapses it (deselects).
        row-selected has by now moved the selection onto `activated`, so compare
        against the pre-press selection, not the live one, or every first click
        would collapse the row it just opened."""
        if activated is self._pre_press_selected:
            listbox.unselect_row(activated)

    def _on_jump_clicked(self, _button, list_row: Gtk.ListBoxRow) -> None:
        self._on_jump(list_row.ccnav_row)

    def _on_entry_activate(self, entry: Gtk.Entry, list_row: Gtk.ListBoxRow) -> None:
        text = entry.get_text()
        if not text.strip():
            return
        entry.set_text("")
        self._on_send(list_row.ccnav_row, text)
