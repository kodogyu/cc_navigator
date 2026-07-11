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

from . import config, model, updater, wiring  # noqa: E402

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
    # model.status_key is the single source of truth for the three-way split;
    # the dot/spinner and the Sort-by-Status sections must agree.
    return model.status_key(row)


# The Sort-by-Status section titles, keyed by model's section constants.
_STATUS_LABELS = {
    model.INPUT_NEEDED: "입력이 필요한 세션",
    model.REPORTED: "보고 완료 (추가 입력 불필요)",
    model.WORKING_SECTION: "작업 중",
}


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


# Drag target for manual-mode row reordering (within this app only).
_DRAG_TARGETS = [Gtk.TargetEntry.new("CCNAV_SESSION", Gtk.TargetFlags.SAME_APP, 0)]

# Docked-bar dimensions: a thin strip pinned across one screen edge.
_DOCK_THICK = 44    # thickness perpendicular to the edge
_DOCK_LENGTH = 220  # extent along the edge


def _dock_rect(edge, geo, along=None):
    """Geometry of the docked bar for `edge` within monitor rect `geo`.

    Pure so the placement math is testable without a real window. The bar is
    pinned flush to the edge; `along` is the free coordinate ALONG the edge (x
    for top/bottom, y for left/right) and is clamped to stay on the monitor.
    When `along` is None the bar is centred on the edge. Returns (x, y, w, h)."""
    if edge in ("left", "right"):
        w, h = _DOCK_THICK, min(geo.height, _DOCK_LENGTH)
        x = geo.x if edge == "left" else geo.x + geo.width - w
        lo, hi = geo.y, geo.y + geo.height - h
        y = geo.y + (geo.height - h) // 2 if along is None else along
        return x, max(lo, min(hi, y)), w, h
    w, h = min(geo.width, _DOCK_LENGTH), _DOCK_THICK
    y = geo.y if edge == "top" else geo.y + geo.height - h
    lo, hi = geo.x, geo.x + geo.width - w
    x = geo.x + (geo.width - w) // 2 if along is None else along
    return max(lo, min(hi, x)), y, w, h


class NavigatorWindow(Gtk.Window):
    def __init__(
        self,
        on_jump: Callable[[model.Row], None],
        on_send: Callable[[model.Row, str], None],
        settings: "config.Settings" = None,
        on_settings_changed: Callable[["config.Settings"], None] = None,
    ) -> None:
        super().__init__(title="cc_navigator")
        self._on_jump = on_jump
        self._on_send = on_send
        self._on_settings_changed = on_settings_changed
        self._settings = settings or config.Settings()
        # Section metadata for the two list views, recomputed on each set_rows
        # and read by the sort/header funcs. Initialised empty so those funcs are
        # safe on the very first insert (before the first recompute).
        self._group_rank = {}     # group_key -> display rank (recent groups first)
        self._group_counts = {}   # group_key -> {input, reported, working}
        self._status_counts = {}  # status section -> count
        self._collapsed_groups = set()  # group keys collapsed in Group mode
        self._manual_order = []         # session_ids in the user's manual order
        self._group_override = {}       # session_id -> group_key it was dragged into
        self._group_names = {}          # group_key -> user-chosen display name
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
        # A red badge showing how many sessions are waiting for the user's input
        # (hidden when none). Rightmost, next to the window controls.
        self._count_badge = Gtk.Label()
        self._count_badge.set_no_show_all(True)  # visibility is driven by the count
        header.pack_end(self._count_badge)

        gear = Gtk.Button()
        gear.set_relief(Gtk.ReliefStyle.NONE)
        gear.add(Gtk.Image.new_from_icon_name("emblem-system-symbolic", Gtk.IconSize.BUTTON))
        gear.set_tooltip_text("설정")
        gear.connect("clicked", self._on_settings_clicked)
        header.pack_end(gear)

        collapse = Gtk.ToggleButton()
        collapse.set_relief(Gtk.ReliefStyle.NONE)
        collapse.add(Gtk.Image.new_from_icon_name("pan-up-symbolic", Gtk.IconSize.BUTTON))
        collapse.set_tooltip_text("접기 (길게 눌러 가장자리에 붙이기)")
        collapse.connect("toggled", self._on_collapse_toggled)
        header.pack_start(collapse)
        self._collapse_button = collapse
        # Long-press the collapse button to enter attach mode (a direction picker
        # to dock the panel to a screen edge). The gesture claims the press, so a
        # short click still collapses; the flag undoes the toggle a long press
        # would otherwise cause.
        self._suppress_collapse_toggle = False
        self._collapse_long_press = Gtk.GestureLongPress.new(collapse)
        # Require a clearly-long hold so a normal (even slightly slow) click
        # collapses instead of accidentally opening the attach picker.
        self._collapse_long_press.set_property("delay-factor", 2.0)
        self._collapse_long_press.connect("pressed", self._on_collapse_long_press)

        self.set_titlebar(header)
        self._header = header  # kept so attach mode can drop/restore the titlebar
        # Show the header now and mark it no-show-all, so a later window.show_all()
        # (app.main calls it once at startup) can't re-reveal the titlebar while
        # the panel is docked (attach mode hides it).
        header.show_all()
        header.set_no_show_all(True)

        # Font override lives in a CSS provider scoped to this window by a class,
        # so changing the panel's font size never touches any other application.
        self.get_style_context().add_class("ccnav")
        self._css = Gtk.CssProvider()
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(
                screen, self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            # While docked we add the "docked" class; zero the client-side
            # decoration so the bar sits FLUSH against the screen edge. The CSD
            # shadow reserves an (asymmetric, bottom/side-heavy) margin around the
            # window, which is what left it inset from the left/right/bottom edges.
            dock_css = Gtk.CssProvider()
            dock_css.load_from_data(
                b"window.ccnav.docked decoration,"
                b" window.ccnav.docked.background {"
                b" margin: 0; padding: 0; box-shadow: none; border-radius: 0;"
                b" border-width: 0; }")
            Gtk.StyleContext.add_provider_for_screen(
                screen, dock_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
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
        # Sections/groups are drawn as list headers (set_header_func), so the
        # in-place reconcile is untouched: rows stay a flat listbox and GTK adds
        # a section title / group header above each row that starts a new one.
        self._listbox.set_header_func(self._header_func)
        # In Group mode the group headers are real (non-selectable) list rows, so
        # a collapsed group can hide its sessions while its header row stays put.
        self._listbox.set_filter_func(self._filter_row)
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

        # "Sort by" selector: status sections vs project groups. In group mode
        # the list is arranged manually by drag; the "자동 정렬" button re-groups
        # everything by directory and restores the automatic order.
        sort_combo = Gtk.ComboBoxText()
        sort_combo.append(config.SORT_MODES[0], "상태별 정렬")   # "status"
        sort_combo.append(config.SORT_MODES[1], "그룹별 정렬")   # "group"
        sort_combo.set_active_id(self._settings.sort_mode)
        sort_combo.connect("changed", self._on_sort_mode_changed)
        self._sort_combo = sort_combo
        auto_sort = Gtk.Button(label="자동 정렬")
        auto_sort.set_relief(Gtk.ReliefStyle.NONE)
        auto_sort.set_tooltip_text("디렉터리 기준으로 다시 그룹화하고 자동 순서로 정렬")
        auto_sort.set_no_show_all(True)  # shown only in group mode
        auto_sort.set_visible(self._settings.sort_mode == "group")
        auto_sort.connect("clicked", self._on_auto_sort_clicked)
        self._auto_sort_button = auto_sort
        sort_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        sort_row.pack_start(sort_combo, False, False, 0)
        sort_row.pack_start(auto_sort, False, False, 0)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.pack_start(sort_row, False, False, 0)
        box.pack_start(scroller, True, True, 0)
        box.pack_start(self._status, False, False, 4)
        self._content = box

        # A Stack holds the full panel and a minimal "docked" bar. Attach mode
        # (long-press the collapse button, then pick an edge) swaps to the docked
        # bar pinned to a screen edge; the detach button swaps back.
        self._docked_edge = None
        self._pre_dock_pos = None
        self._dock_geo = None  # monitor rect of the current dock, for edge-sliding
        self._dock_bar = self._build_dock_bar()
        self._stack = Gtk.Stack()
        self._stack.set_hhomogeneous(False)
        self._stack.set_vhomogeneous(False)
        self._stack.add_named(self._content, "full")
        self._stack.add_named(self._dock_bar, "docked")
        self._stack.set_visible_child_name("full")
        self.add(self._stack)
        # Default state is expanded: without this, box.get_visible() is False
        # until something shows it, and the collapse toggle would have nothing
        # correct to restore to. Application.main() still calls window.show_all()
        # to reveal every child widget on screen; this only pins the box itself.
        # The dock bar stays HIDDEN until we actually dock: if it were a visible
        # stack child, collapsing (which hides _content, the shown child) would
        # make GtkStack fall back to showing the dock bar -- leaking its icon and
        # detach button into what should be a bare titlebar. _build_dock_bar has
        # already shown its children, so _dock_to_edge only needs to show the bar.
        self._content.show()
        self._stack.show()
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

        # While docked the window's size/position belong to attach mode; only a
        # detach may move it. Opacity and the CSS tint still apply either way.
        if self._docked_edge is None:
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
        if self._suppress_collapse_toggle:
            # A long press fired: undo the toggle it caused rather than collapse.
            self._suppress_collapse_toggle = False
            if button.get_active():
                button.set_active(False)  # re-enters with the flag cleared -> no-op
            return
        self.set_collapsed(button.get_active())

    def set_collapsed(self, collapsed: bool) -> None:
        """Collapsed hides the body and shrinks the window to its titlebar; the
        panel stays floating and one click brings the list back."""
        # While docked, the stack child and window size belong to attach mode.
        # A stray toggle can still arrive here -- e.g. docking FROM a collapsed
        # panel: the attach popover's animated "closed" fires after _dock_to_edge
        # and un-presses the (still-active) collapse button, which would otherwise
        # swap the stack back to "full" and re-grow the window over the dock bar,
        # leaving no titlebar and the detach button off-screen. Ignore it; _undock
        # clears _docked_edge before its own reset, so detach is unaffected.
        if self._docked_edge is not None:
            return
        image = self._collapse_button.get_child()
        if collapsed:
            self._content.hide()
            image.set_from_icon_name("pan-down-symbolic", Gtk.IconSize.BUTTON)
            self.resize(self._settings.width, 1)  # shrink to titlebar's minimum
        else:
            self._content.show()
            # Force the stack back onto the full view: if a prior collapse hid
            # _content while docked state left the stack pointing elsewhere, just
            # showing _content would not re-select it.
            self._stack.set_visible_child_name("full")
            image.set_from_icon_name("pan-up-symbolic", Gtk.IconSize.BUTTON)
            self.resize(self._settings.width, self._settings.height)
        if self._collapse_button.get_active() != collapsed:
            self._collapse_button.set_active(collapsed)

    # -- attach mode (dock to a screen edge) ---------------------------------

    def _build_dock_bar(self) -> Gtk.Box:
        """The minimal bar shown while docked: the app icon, the waiting-count,
        and a detach button. Its orientation is set when docking."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.set_margin_top(4)
        bar.set_margin_bottom(4)
        bar.set_margin_start(4)
        bar.set_margin_end(4)
        icon_pixbuf = _app_icon_pixbuf(22)
        self._dock_icon = (Gtk.Image.new_from_pixbuf(icon_pixbuf)
                           if icon_pixbuf is not None else Gtk.Image())
        self._dock_count = Gtk.Label()
        # The icon+count area doubles as a drag handle: while docked the window has
        # no titlebar, so grabbing here slides the bar ALONG its edge (a docked bar
        # can only move on the one free axis). An EventBox gives it an input window.
        drag_area = Gtk.EventBox()
        drag_area.set_tooltip_text("드래그해서 가장자리를 따라 이동")
        drag_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        drag_inner.pack_start(self._dock_icon, False, False, 0)
        drag_inner.pack_start(self._dock_count, False, False, 0)
        drag_area.add(drag_inner)
        drag_area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                             | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.POINTER_MOTION_MASK
                             | Gdk.EventMask.BUTTON1_MOTION_MASK)
        drag_area.connect("button-press-event", self._on_dock_drag_begin)
        drag_area.connect("motion-notify-event", self._on_dock_drag_motion)
        drag_area.connect("button-release-event", self._on_dock_drag_end)
        self._dock_drag_inner = drag_inner
        self._dock_drag = None  # (start pointer-root, start along) while dragging
        detach = Gtk.Button()
        detach.set_relief(Gtk.ReliefStyle.NONE)
        detach.add(Gtk.Image.new_from_icon_name("view-restore-symbolic", Gtk.IconSize.BUTTON))
        detach.set_tooltip_text("떼기 (detach)")
        detach.connect("clicked", lambda _b: self._undock())
        self._dock_detach = detach
        bar.pack_start(drag_area, True, True, 0)
        bar.pack_start(detach, False, False, 0)
        # Show the bar's children now, then hide the bar itself and mark it
        # no-show-all: the children are ready to render the instant _dock_to_edge
        # calls bar.show(), while window.show_all() at startup can't re-reveal the
        # bar (which would leak it into the collapsed titlebar view).
        bar.show_all()
        bar.set_no_show_all(True)
        bar.hide()
        return bar

    def _on_collapse_long_press(self, gesture, _x, _y) -> None:
        """A long press on the collapse button opens a direction picker to dock
        the panel to a screen edge."""
        # Claim the press so the button does NOT also toggle (collapse) -- a long
        # press means "attach", a short click means "collapse".
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._suppress_collapse_toggle = True
        popover = Gtk.Popover.new(self._collapse_button)
        grid = Gtk.Grid()
        grid.set_row_spacing(2)
        grid.set_column_spacing(2)
        grid.set_border_width(6)

        def edge_button(icon, edge, col, row):
            btn = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.connect("clicked", lambda _b, e=edge: (popover.popdown(), self._dock_to_edge(e)))
            grid.attach(btn, col, row, 1, 1)

        edge_button("go-up-symbolic", "top", 1, 0)
        edge_button("go-previous-symbolic", "left", 0, 1)
        edge_button("go-next-symbolic", "right", 2, 1)
        edge_button("go-down-symbolic", "bottom", 1, 2)
        grid.show_all()
        popover.add(grid)

        # Clear the suppress flag when the popover closes (a long press whose
        # release the popover grab swallowed would otherwise leave it stuck True
        # and eat the next real collapse click), and destroy the popover so one
        # is not leaked per long press.
        def _on_closed(pop):
            self._suppress_collapse_toggle = False
            # A dismissed attach popover must not leave the button stuck toggled
            # / the panel collapsed.
            if self._collapse_button.get_active():
                self._collapse_button.set_active(False)
            pop.destroy()
        popover.connect("closed", _on_closed)
        popover.popup()

    def _dock_to_edge(self, edge: str) -> None:
        """Dock as a minimal bar pinned to one screen edge. Icons run vertically
        when docked left/right, horizontally when docked top/bottom."""
        if edge not in ("top", "bottom", "left", "right"):
            return
        if self._docked_edge is None:  # remember where to return on detach
            self._pre_dock_pos = self.get_position()
        self._docked_edge = edge
        orientation = (Gtk.Orientation.VERTICAL if edge in ("left", "right")
                       else Gtk.Orientation.HORIZONTAL)
        self._dock_bar.set_orientation(orientation)
        self._dock_drag_inner.set_orientation(orientation)  # icon over count when vertical
        self.get_style_context().add_class("docked")  # zero the CSD shadow -> flush
        self._update_dock_count()
        # Reveal the bar before switching: GtkStack refuses to switch to a hidden
        # child, and the bar is kept hidden while undocked (see _build_dock_bar).
        self._dock_bar.show()
        self._stack.set_visible_child_name("docked")
        # Hide the titlebar so the window can shrink to just the dock bar (its
        # buttons otherwise force a wide minimum); the detach button lives in the
        # bar itself while docked. (set_titlebar(None) warns on a realized window,
        # so hide the header widget instead.)
        self._header.hide()
        self._resize_and_position_dock(edge)

    def _undock(self) -> None:
        if self._docked_edge is None:
            return
        if self._dock_drag is not None:
            self._dock_drag = None  # defensively release a live slide grab
            display = Gdk.Display.get_default()
            seat = display.get_default_seat() if display is not None else None
            if seat is not None:
                seat.ungrab()
        self._docked_edge = None
        self.get_style_context().remove_class("docked")  # restore the normal frame
        # Hide the bar before the switch so a later collapse (which hides _content)
        # can't make the stack fall back to showing it.
        self._dock_bar.hide()
        # Fully restore the expanded view via set_collapsed(False): it shows
        # _content, re-selects the "full" stack child, resizes, and -- crucially --
        # resets the chevron icon to pan-up and un-presses the collapse button.
        # Calling it directly (not gated on the button being active) fixes the
        # dock-from-collapsed case where the attach popover already un-pressed the
        # button, which would otherwise leave the chevron stuck pointing down.
        # _docked_edge is already None, so set_collapsed's docked-guard lets it run.
        self.set_collapsed(False)
        self._header.show()  # restore the titlebar
        if self._pre_dock_pos is not None:
            self.move(*self._pre_dock_pos)
        else:
            self._apply_geometry(self._settings)

    def _resize_and_position_dock(self, edge: str, along=None) -> None:
        display = Gdk.Display.get_default()
        monitor = (display.get_primary_monitor() or display.get_monitor(0)) if display else None
        if monitor is None:
            return
        geo = monitor.get_geometry()
        self._dock_geo = geo  # remembered so a drag can re-pin along this edge
        x, y, w, h = _dock_rect(edge, geo, along)
        self.resize(w, h)  # keep GTK's requested size in sync
        # Move+resize the X window atomically: for the right/bottom edges the
        # position depends on the NEW size, so moving while the window is still
        # full-size lets the WM clamp it inward (it slid left by the old width).
        gdk_window = self.get_window()
        if gdk_window is not None:
            gdk_window.move_resize(x, y, w, h)
        else:
            self.move(x, y)

    # -- sliding a docked bar along its edge ---------------------------------

    def _on_dock_drag_begin(self, _widget, event) -> bool:
        """Start sliding the docked bar: record where the pointer and the bar
        began. A pointer grab keeps motion events coming even when the pointer
        leaves the thin bar mid-drag."""
        if event.button != 1 or self._docked_edge is None:
            return False
        x, y = self.get_position()
        along = y if self._docked_edge in ("left", "right") else x
        root = event.y_root if self._docked_edge in ("left", "right") else event.x_root
        self._dock_drag = (root, along)
        seat = getattr(event, "get_seat", lambda: None)() or (
            Gdk.Display.get_default().get_default_seat() if Gdk.Display.get_default() else None)
        gdk_window = _widget.get_window()
        if seat is not None and gdk_window is not None:
            seat.grab(gdk_window, Gdk.SeatCapabilities.ALL_POINTING, False, None, event, None)
        return False

    def _on_dock_drag_motion(self, _widget, event) -> bool:
        if self._dock_drag is None or self._docked_edge is None:
            return False
        geo = getattr(self, "_dock_geo", None)
        if geo is None:
            return False
        start_root, start_along = self._dock_drag
        vertical = self._docked_edge in ("left", "right")
        root = event.y_root if vertical else event.x_root
        along = int(start_along + (root - start_root))
        x, y, _w, _h = _dock_rect(self._docked_edge, geo, along)
        self.move(x, y)
        return True

    def _on_dock_drag_end(self, _widget, event) -> bool:
        # Only the button that started the slide ends it: while button 1 is held,
        # the seat grab also delivers stray button 2/3 clicks here, which must not
        # abort the drag mid-gesture.
        if getattr(event, "button", 1) != 1:
            return False
        if self._dock_drag is None:
            return False
        self._dock_drag = None
        seat = getattr(event, "get_seat", lambda: None)() or (
            Gdk.Display.get_default().get_default_seat() if Gdk.Display.get_default() else None)
        if seat is not None:
            seat.ungrab()
        return True

    def _update_dock_count(self) -> None:
        waiting = self._status_counts.get(model.INPUT_NEEDED, 0)
        self._dock_count.set_markup(
            '<span foreground="#e01b24"><b>%d</b></span>' % waiting if waiting else "")

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
            if not getattr(child, "ccnav_is_header", False):
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
            if getattr(child, "ccnav_is_header", False):
                continue
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

        existing = {c.ccnav_row.session_id: c for c in self._listbox.get_children()
                    if not getattr(c, "ccnav_is_header", False)}
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

        self._recompute_sections(rows)
        self._sync_manual_order(rows)
        self._reconcile_group_headers(rows)
        self._update_count_badge()
        self._update_dock_count()
        self._listbox.invalidate_sort()
        self._listbox.invalidate_headers()
        self._listbox.invalidate_filter()
        self._hint = "" if rows else EMPTY_HINT
        self._render_status()

    def _update_count_badge(self) -> None:
        """Show the header badge iff some session is waiting for the user."""
        waiting = self._status_counts.get(model.INPUT_NEEDED, 0)
        if waiting > 0:
            self._count_badge.set_markup(
                '<span background="#e01b24" foreground="#ffffff"><b> %d </b></span>' % waiting)
            self._count_badge.set_visible(True)
        else:
            self._count_badge.set_visible(False)

    def _group_of(self, row: model.Row) -> str:
        """The group a row belongs to: the one it was dragged into, else its
        project directory (the auto default). Presence, not truthiness -- a move
        INTO the blank-cwd "" group is a real override, not "no override"."""
        sid = row.session_id
        if sid in self._group_override:
            return self._group_override[sid]
        return model.group_key(row)

    def _dragged_row(self, session_id: str):
        for child in self._listbox.get_children():
            if (not getattr(child, "ccnav_is_header", False)
                    and child.ccnav_row.session_id == session_id):
                return child.ccnav_row
        return None

    def _set_group_override(self, session_id: str, group_key: str) -> None:
        """Record a move into `group_key`, but keep an override only when it
        actually differs from the session's own directory (so a self/redundant
        drop doesn't pin a session to a directory it may later leave)."""
        row = self._dragged_row(session_id)
        if row is not None and group_key == model.group_key(row):
            self._group_override.pop(session_id, None)
        else:
            self._group_override[session_id] = group_key

    def _recompute_sections(self, rows: List[model.Row]) -> None:
        """Tally per-section state from the current rows: counts for the headers
        and a display rank for the groups (most-recently-active group first)."""
        counts = {}         # group_key -> {input, reported, working}
        status_counts = {}  # status section -> count
        recent = {}         # group_key -> newest updated_at in the group
        for row in rows:
            gk = self._group_of(row)
            sk = model.status_key(row)
            counts.setdefault(gk, {model.INPUT_NEEDED: 0, model.REPORTED: 0,
                                   model.WORKING_SECTION: 0})[sk] += 1
            status_counts[sk] = status_counts.get(sk, 0) + 1
            recent[gk] = max(recent.get(gk, row.updated_at), row.updated_at)
        self._group_counts = counts
        self._status_counts = status_counts
        # Ties broken by key so the order is stable across ticks with equal times.
        order = sorted(recent, key=lambda k: (-recent[k], k))
        self._group_rank = {k: i for i, k in enumerate(order)}

    def _section_sort_key(self, row: model.Row):
        if self._settings.sort_mode == "group":
            # Group by rank, then the user's manual order within the group.
            rank = self._group_rank.get(self._group_of(row), 1 << 30)
            return (rank, self._manual_index(row.session_id))
        rank = model.STATUS_SECTIONS.index(model.status_key(row))
        return (rank,) + tuple(model.sort_key(row))

    # -- manual order (Manual mode) ------------------------------------------

    def _sync_manual_order(self, rows: List[model.Row]) -> None:
        """Keep _manual_order in step with the live sessions: drop the gone,
        append the new at the end. The user's relative order is preserved."""
        present = {r.session_id for r in rows}
        self._manual_order = [s for s in self._manual_order if s in present]
        # Forget group moves for sessions that have ended.
        self._group_override = {s: g for s, g in self._group_override.items() if s in present}
        known = set(self._manual_order)
        for row in rows:
            if row.session_id not in known:
                self._manual_order.append(row.session_id)
                known.add(row.session_id)

    def _manual_index(self, session_id: str) -> int:
        try:
            return self._manual_order.index(session_id)
        except ValueError:
            return 1 << 30

    def _reorder_session(self, dragged_id: str, target_id: str, after: bool = False) -> None:
        """Move `dragged_id` next to `target_id` in the manual order -- before it,
        or after it when `after` (a drop on the lower half of the target row), so
        the very bottom slot is reachable in one drag."""
        if dragged_id == target_id or dragged_id not in self._manual_order:
            return
        self._manual_order.remove(dragged_id)
        try:
            index = self._manual_order.index(target_id)
        except ValueError:
            index = len(self._manual_order)  # target gone: land at the end
        else:
            if after:
                index += 1
        self._manual_order.insert(index, dragged_id)
        self._listbox.invalidate_sort()

    def _set_row_draggable(self, list_row: Gtk.ListBoxRow, on: bool) -> None:
        """Show the drag handle only in Group mode (the handle IS the drag
        source, so hiding it disables dragging)."""
        list_row.ccnav_grip.set_visible(on)

    def _on_grip_drag_get(self, _grip, _context, selection, _info, _time, list_row) -> None:
        selection.set(selection.get_target(), 8,
                      list_row.ccnav_row.session_id.encode("utf-8"))

    def _on_row_drag_received(self, list_row, _context, _x, y, selection, _info, _time) -> None:
        """Drop a session on another: it joins that session's group and lands
        next to it (upper half -> before, lower half -> after)."""
        if self._settings.sort_mode != "group" or getattr(list_row, "ccnav_is_header", False):
            return
        data = selection.get_data()
        if not data:
            return
        dragged = data.decode("utf-8", "replace")
        target = list_row.ccnav_row
        if dragged == target.session_id:
            return
        self._set_group_override(dragged, self._group_of(target))  # adopt the group
        after = y > list_row.get_allocated_height() / 2
        self._reorder_session(dragged, target.session_id, after)
        self._regroup_now()

    def _on_group_header_drag_received(self, header_row, _context, _x, _y, selection, _info, _time) -> None:
        """Drop a session on a group header: it joins that group, at the end."""
        if self._settings.sort_mode != "group":
            return
        data = selection.get_data()
        if not data:
            return
        dragged = data.decode("utf-8", "replace")
        self._set_group_override(dragged, header_row.ccnav_group)
        if dragged in self._manual_order:  # move to the end of the manual order
            self._manual_order.remove(dragged)
            self._manual_order.append(dragged)
        self._regroup_now()

    def _regroup_now(self) -> None:
        """Recompute groups/counts/headers from the live rows and re-sort/filter.
        Used after a drag or the auto-sort button, without waiting for a poll."""
        rows = [c.ccnav_row for c in self._listbox.get_children()
                if not getattr(c, "ccnav_is_header", False)]
        self._recompute_sections(rows)
        self._reconcile_group_headers(rows)
        self._update_count_badge()
        self._listbox.invalidate_sort()
        self._listbox.invalidate_filter()
        self._listbox.invalidate_headers()

    def _on_auto_sort_clicked(self, _button) -> None:
        """Re-group everything by directory and restore the automatic order:
        clear the manual group moves and re-sort the manual order by (group by
        directory, then priority)."""
        self._group_override.clear()
        rows = [c.ccnav_row for c in self._listbox.get_children()
                if not getattr(c, "ccnav_is_header", False)]
        ordered = sorted(rows, key=lambda r: (model.group_key(r),) + tuple(model.sort_key(r)))
        self._manual_order = [r.session_id for r in ordered]
        self._regroup_now()

    def _row_sort_key(self, widget):
        if getattr(widget, "ccnav_is_header", False):
            # A group header leads its group: same group rank, then ahead of its
            # sessions (whose second key element is 0 or 1, both > -1).
            return (self._group_rank.get(widget.ccnav_group, 1 << 30), -1)
        return self._section_sort_key(widget.ccnav_row)

    def _sort_rows(self, a: Gtk.ListBoxRow, b: Gtk.ListBoxRow) -> int:
        """Order by section, then by model.sort_key within it, so sections/groups
        stay contiguous. In Group mode a group's header row leads its sessions."""
        ka, kb = self._row_sort_key(a), self._row_sort_key(b)
        return -1 if ka < kb else (1 if ka > kb else 0)

    def _filter_row(self, widget) -> bool:
        """Hide a session row iff its group is collapsed (Group mode only). Group
        header rows are never hidden, so a collapsed group keeps its header."""
        if getattr(widget, "ccnav_is_header", False):
            return True
        if self._settings.sort_mode != "group":
            return True
        return self._group_of(widget.ccnav_row) not in self._collapsed_groups

    def _header_func(self, row_widget, before_widget) -> None:
        """Only Status mode draws section titles as GtkListBox headers (on the
        first session of each section). Group mode uses header ROWS and Manual
        mode has no sections, so every row's list-header is cleared there."""
        if (self._settings.sort_mode != "status"
                or getattr(row_widget, "ccnav_is_header", False)):
            row_widget.set_header(None)
            return
        this = model.status_key(row_widget.ccnav_row)
        prev = (model.status_key(before_widget.ccnav_row)
                if before_widget is not None
                and not getattr(before_widget, "ccnav_is_header", False)
                else None)
        row_widget.set_header(None if this == prev else self._make_status_header(this))

    def _make_status_header(self, section: str) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_start(4)
        box.set_margin_bottom(2)
        title = Gtk.Label(xalign=0.0)
        title.set_markup("<b>%s</b>" % GLib.markup_escape_text(
            _STATUS_LABELS.get(section, section)))
        badge = Gtk.Label()
        badge.set_markup('<small><span foreground="#77767b">%d</span></small>'
                         % self._status_counts.get(section, 0))
        box.pack_start(title, False, False, 0)
        box.pack_start(badge, False, False, 0)
        box.show_all()
        return box

    # -- group header rows (Group mode) --------------------------------------

    def _reconcile_group_headers(self, rows: List[model.Row]) -> None:
        """Keep exactly one non-selectable header row per project group in Group
        mode (and none in Status mode). Reconciled like the session rows, so the
        listbox is never rebuilt."""
        existing = {c.ccnav_group: c for c in self._listbox.get_children()
                    if getattr(c, "ccnav_is_header", False)}
        if self._settings.sort_mode != "group":
            for child in existing.values():
                self._listbox.remove(child)
            return
        wanted = {self._group_of(r) for r in rows}
        # Forget the collapsed state / custom name of groups that no longer
        # exist, so a group whose sessions all ended does not silently reappear
        # collapsed or renamed later.
        self._collapsed_groups &= wanted
        self._group_names = {k: v for k, v in self._group_names.items() if k in wanted}
        for group_key, child in list(existing.items()):
            if group_key not in wanted:
                self._listbox.remove(child)
                del existing[group_key]
        for group_key in wanted:
            if group_key in existing:
                self._update_group_header_row(existing[group_key])
            else:
                header_row = self._build_group_header_row(group_key)
                self._listbox.insert(header_row, -1)
                header_row.show_all()

    def _build_group_header_row(self, group_key: str) -> Gtk.ListBoxRow:
        header_row = Gtk.ListBoxRow()
        header_row.ccnav_is_header = True   # type: ignore[attr-defined]
        header_row.ccnav_group = group_key  # type: ignore[attr-defined]
        header_row.set_selectable(False)
        header_row.set_activatable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.set_margin_top(8)
        box.set_margin_start(2)
        box.set_margin_end(4)
        box.set_margin_bottom(2)

        chevron = Gtk.Button()
        chevron.set_relief(Gtk.ReliefStyle.NONE)
        chevron_img = Gtk.Image()
        chevron.add(chevron_img)
        chevron.set_tooltip_text("그룹 접기/펼치기")
        chevron.connect("clicked", self._on_group_toggle, group_key)
        box.pack_start(chevron, False, False, 0)

        box.pack_start(
            Gtk.Image.new_from_icon_name("folder-symbolic", Gtk.IconSize.MENU), False, False, 0)

        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        name = Gtk.Label(xalign=0.0)  # markup set in _update_group_header_row
        names.pack_start(name, False, False, 0)
        if group_key:  # a blank cwd shows just "~"; no empty path line under it
            path = Gtk.Label(xalign=0.0)
            path.set_markup('<small><span foreground="#77767b">%s</span></small>'
                            % GLib.markup_escape_text(_oneline(group_key)))
            path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            names.pack_start(path, False, False, 0)
        box.pack_start(names, True, True, 0)

        rename = Gtk.Button()
        rename.set_relief(Gtk.ReliefStyle.NONE)
        rename.add(Gtk.Image.new_from_icon_name("document-edit-symbolic", Gtk.IconSize.MENU))
        rename.set_tooltip_text("그룹 이름 변경")
        rename.connect("clicked", self._on_group_rename_clicked, group_key)
        box.pack_end(rename, False, False, 0)

        counts = Gtk.Label()
        box.pack_end(counts, False, False, 0)
        header_row.add(box)
        header_row.ccnav_chevron_img = chevron_img  # type: ignore[attr-defined]
        header_row.ccnav_counts_label = counts       # type: ignore[attr-defined]
        header_row.ccnav_name_label = name            # type: ignore[attr-defined]
        # Drop a session on this header to move it into the group.
        header_row.drag_dest_set(Gtk.DestDefaults.ALL, _DRAG_TARGETS, Gdk.DragAction.MOVE)
        header_row.connect("drag-data-received", self._on_group_header_drag_received)
        self._update_group_header_row(header_row)
        return header_row

    def _group_display_name(self, group_key: str) -> str:
        return self._group_names.get(group_key) or model.group_label(group_key)

    def _on_group_rename_clicked(self, button, group_key: str) -> None:
        popover = Gtk.Popover.new(button)
        entry = Gtk.Entry()
        entry.set_text(self._group_display_name(group_key))
        entry.set_margin_top(6)
        entry.set_margin_bottom(6)
        entry.set_margin_start(6)
        entry.set_margin_end(6)

        def commit():
            text = _oneline(entry.get_text()).strip()
            if text and text != model.group_label(group_key):
                self._group_names[group_key] = text
            else:
                self._group_names.pop(group_key, None)  # empty / default -> auto name
            self._regroup_now()

        # Commit on Enter AND on click-away, so a typed name is not lost; closing
        # then destroys the popover. (activate -> popdown -> closed -> commit again,
        # which is idempotent.)
        entry.connect("activate", lambda _e: popover.popdown())

        def on_closed(pop):
            commit()
            pop.destroy()

        popover.connect("closed", on_closed)
        popover.add(entry)
        popover.show_all()
        popover.popup()
        entry.grab_focus()

    def _update_group_header_row(self, header_row: Gtk.ListBoxRow) -> None:
        group_key = header_row.ccnav_group
        collapsed = group_key in self._collapsed_groups
        header_row.ccnav_chevron_img.set_from_icon_name(
            "pan-end-symbolic" if collapsed else "pan-down-symbolic", Gtk.IconSize.MENU)
        header_row.ccnav_name_label.set_markup(
            "<b>%s</b>" % GLib.markup_escape_text(_oneline(self._group_display_name(group_key))))
        counts = self._group_counts.get(
            group_key, {model.INPUT_NEEDED: 0, model.REPORTED: 0, model.WORKING_SECTION: 0})
        header_row.ccnav_counts_label.set_markup(
            '<small><span foreground="#e01b24">●</span> %d  '
            '<span foreground="#2ec27e">●</span> %d  '
            '<span foreground="#3584e4">↻</span> %d</small>'
            % (counts[model.INPUT_NEEDED], counts[model.REPORTED],
               counts[model.WORKING_SECTION]))

    def _on_group_toggle(self, _button, group_key: str) -> None:
        if group_key in self._collapsed_groups:
            self._collapsed_groups.discard(group_key)
        else:
            self._collapsed_groups.add(group_key)
            # Don't leave the selection stranded on a row we're about to hide.
            # Test the row's EFFECTIVE group (override-aware), matching the filter.
            selected = self._listbox.get_selected_row()
            if (selected is not None
                    and not getattr(selected, "ccnav_is_header", False)
                    and self._group_of(selected.ccnav_row) == group_key):
                self._listbox.unselect_row(selected)
        self._listbox.invalidate_filter()
        for child in self._listbox.get_children():
            if getattr(child, "ccnav_is_header", False) and child.ccnav_group == group_key:
                self._update_group_header_row(child)
                break

    def _on_sort_mode_changed(self, combo: Gtk.ComboBoxText) -> None:
        mode = combo.get_active_id()
        if not mode or mode == self._settings.sort_mode:
            return
        self._commit_settings(config.with_updates(self._settings, sort_mode=mode))
        # Add/remove group header rows for the new mode, then re-sort, filter and
        # re-header. Derive the current rows from the live session widgets.
        rows = [c.ccnav_row for c in self._listbox.get_children()
                if not getattr(c, "ccnav_is_header", False)]
        self._reconcile_group_headers(rows)
        # Rows are drag sources only in group mode; the auto-sort button shows
        # only there too.
        group = mode == "group"
        for child in self._listbox.get_children():
            if not getattr(child, "ccnav_is_header", False):
                self._set_row_draggable(child, group)
        self._auto_sort_button.set_visible(group)
        self._listbox.invalidate_sort()
        self._listbox.invalidate_filter()
        self._listbox.invalidate_headers()

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

        # Drag handle on the RIGHT (visible only in group mode). A GtkListBoxRow is
        # activatable and swallows button drags, so the drag SOURCE is this grip,
        # not the row.
        grip = Gtk.EventBox()
        grip_label = Gtk.Label()
        grip_label.set_markup('<span foreground="#77767b">⠿</span>')
        grip.add(grip_label)
        grip.set_tooltip_text("드래그해서 순서 변경 / 그룹 이동")
        grip.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, _DRAG_TARGETS, Gdk.DragAction.MOVE)
        grip.connect("drag-data-get", self._on_grip_drag_get, list_row)
        # no_show_all protects the grip's OWN visibility from row.show_all() (so the
        # sort-mode toggle below wins), but that also stops show_all reaching the
        # label -- so show the '⠿' glyph explicitly, or the box renders empty.
        grip_label.show()
        grip.set_no_show_all(True)
        grip.set_visible(self._settings.sort_mode == "group")
        header.pack_end(grip, False, False, 0)
        list_row.ccnav_grip = grip  # type: ignore[attr-defined]

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

        # Manual-mode drag-to-reorder: every row is a drop target; it is a drag
        # SOURCE only while in manual mode (toggled here and on mode change).
        list_row.drag_dest_set(Gtk.DestDefaults.ALL, _DRAG_TARGETS, Gdk.DragAction.MOVE)
        list_row.connect("drag-data-received", self._on_row_drag_received)

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
