"""Wire the state directory, tmux, GNOME and the overlay together.

Two costs are too large to pay on the GTK main thread (measured in earlier
tasks): activating a window can take up to ~3s (two attempts at 1.5s each),
and a tmux call is now bounded but can still take up to proc.DEFAULT_TIMEOUT.
So all tmux, gdbus and xprop calls happen on daemon threads, and results are
handed back to GTK with GLib.idle_add -- the only sanctioned way to touch GTK
from a thread other than the one running the main loop (PyGObject threading
guide: "GTK isn't thread safe; only ... the main thread[] is allowed to call
GTK code at all times").
"""
from __future__ import annotations

import pathlib
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Set

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import config, gnome, model, paths, proc, statestore, tmuxctl, ui  # noqa: E402

# Fallback only: the live interval comes from self._settings.poll_seconds. Kept
# so a bare Application built in a test (which does not load config) still polls
# at a sane rate before a settings object is attached.
POLL_SECONDS = 1

# Eval("1+1") is a local D-Bus round trip and answers in milliseconds. The probe
# runs before the window is mapped, so it cannot freeze a live window -- but on a
# wedged gdbus it would hold the screen blank for DEFAULT_TIMEOUT with nothing to
# look at. Bound it tightly and fail to the safe side: Eval counts as unavailable,
# the jump buttons stay disabled, and EVAL_UNAVAILABLE_HINT explains why.
EVAL_PROBE_TIMEOUT = 1.0


def probe_eval_available() -> bool:
    return gnome.eval_available(run=proc.runner_with_timeout(EVAL_PROBE_TIMEOUT))


@dataclass(frozen=True)
class Collected:
    """The rows to show, plus how many sockets held sessions but did not answer.

    `unreachable` exists so the UI can say *something* when a query fails.
    Without it a wedged tmux makes a live, waiting session's row simply vanish
    (its pane is not in the empty result, so build_rows drops it) with no hint
    -- indistinguishable from the session having ended. That is the project's
    signature silent-failure, muted. The state file now survives (see prune),
    so the row returns as soon as tmux answers; the hint covers the gap.
    """

    rows: List[model.Row]
    unreachable: int


def collect_rows(
    state_dir: pathlib.Path,
    read_all: Callable[[pathlib.Path], List[Dict[str, object]]] = statestore.read_all,
    sessions_for: Callable[[str], "tuple"] = tmuxctl.sessions_by_pane_result,
    titles_for: Callable[[str], Dict[str, str]] = tmuxctl.titles_by_pane,
    prune: Callable[..., int] = statestore.prune,
) -> Collected:
    records = read_all(state_dir)
    sockets = sorted({str(r.get("tmux_socket") or "") for r in records if r.get("tmux_socket")})
    if not sockets:
        return Collected([], 0)
    # sessions_for reports (ok, panes): ok is False when tmux did not answer
    # (dead socket, or a timed-out slow one). Only sockets that DID answer may
    # gate pruning -- otherwise a one-tick stutter deletes live state (F3).
    results = {socket: sessions_for(socket) for socket in sockets}
    sessions = {socket: panes for socket, (_ok, panes) in results.items()}
    observed = {socket for socket, (ok, _panes) in results.items() if ok}
    titles = {socket: titles_for(socket) for socket in sockets}
    prune(state_dir, model.live_pane_keys(sessions), observed)
    rows = model.build_rows(records, sessions, titles)
    return Collected(rows, len(set(sockets) - observed))


def jump_status(result: gnome.ActivationResult, window_title: str) -> str:
    """The plan's Korean strings, verbatim -- only the decision moved."""
    if not result.ok:
        return "창을 활성화하지 못했습니다: " + window_title
    if result.matched > 1:
        return (
            "같은 제목의 창이 %d개입니다. tmux 세션 하나에는 "
            "클라이언트 하나만 붙이세요: %s" % (result.matched, window_title)
        )
    return ""


def send_status(result: tmuxctl.SendResult) -> str:
    """Map a send outcome to the status line. Pure, so it is tested without
    threads, GTK or a subprocess (like jump_status). Silent on full success.

    The "not delivered" case covers both a hard failure (tmux exited nonzero)
    and a timeout ((124, "") -- a slow tmux the reply may yet reach). We cannot
    tell them apart from the exit code, so the message claims only that delivery
    could not be *confirmed*, never that it definitely failed. This is the same
    distrust of a self-report that makes prune refuse to delete state on a
    (124, "") (F3): the difference is only in stakes -- a hedged status is
    reversible, deleting a live session's state is not -- so here we inform, and
    there we decline to act. Both are "do not trust the empty/failed answer."
    """
    if not result.delivered:
        return (
            "답장 전송을 확인하지 못했습니다. tmux 세션이 응답하지 않거나 "
            "종료되었을 수 있습니다. 세션을 직접 확인하세요."
        )
    if not result.submitted:
        return "답장을 입력했지만 전송(Enter)에 실패했습니다. 세션을 직접 확인하세요."
    return ""


def perform_jump(
    row: model.Row,
    select_pane: Callable[[str, str], None],
    activate: Callable[[str], gnome.ActivationResult],
) -> str:
    """select_pane must run before activate: tmux needs the pane selected
    before the window is raised, or the user lands on the wrong pane.

    select_pane ignores tmux's exit codes by design (Task 6), so there is
    nothing to branch on here -- activate always runs, even when the pane
    selection failed.
    """
    select_pane(row.socket, row.pane)
    result = activate(row.window_title)
    return jump_status(result, row.window_title)


class Application:
    def __init__(
        self,
        collect: Callable[[pathlib.Path], List[model.Row]] = collect_rows,
        probe_eval: Callable[[], bool] = probe_eval_available,
    ) -> None:
        # `collect` is injectable so the poll loop's error handling can be
        # tested with a collector that raises, without GTK or a real tmux.
        self._collect = collect
        self.state_dir = paths.ensure_state_dir()
        self._settings = config.load()
        self.window = ui.NavigatorWindow(
            on_jump=self.jump,
            on_send=self.send,
            settings=self._settings,
            on_settings_changed=self._on_settings_changed,
        )
        self.window.set_eval_available(probe_eval())

        self._jumping = set()  # type: Set[str]  # session ids with a jump in flight

        monitor = Gio.File.new_for_path(str(self.state_dir)).monitor_directory(
            Gio.FileMonitorFlags.NONE, None
        )
        monitor.connect("changed", self._on_state_changed)
        self._monitor = monitor  # keep a reference or it is collected and the watch stops

        self._stop = threading.Event()
        self._wake = threading.Event()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    # -- polling ----------------------------------------------------------

    def _on_state_changed(self, *_args) -> None:
        # No tmux and no GTK work here: just wake the poller. Calling
        # collect_rows from this callback would run tmux on the GTK main
        # thread -- exactly the freeze this task exists to prevent.
        self._wake.set()

    def _poll_loop(self) -> None:
        """Runs on a daemon thread. Every tmux call happens here, never on
        the GTK main thread."""
        while not self._stop.is_set():
            try:
                collected = self._collect(self.state_dir)
                GLib.idle_add(self._apply_rows, collected)
            except Exception as exc:  # noqa: BLE001
                # Not BaseException: a KeyboardInterrupt must still end us.
                # A collect that raises (e.g. statestore.prune hitting an
                # OSError we did not foresee) must NOT kill this thread. A dead
                # poller leaves the window frozen on stale rows while looking
                # perfectly alive -- the silent-success failure this whole
                # project exists to catch. So we keep looping and make the
                # failure visible instead of swallowing it. Re-posting the same
                # status every tick while it flaps is deliberately fine: set_text
                # on an unchanged string is idempotent and cheap, so a counter
                # or backoff would be complexity with no payoff.
                GLib.idle_add(self._apply_poll_error, str(exc))
            # Read the interval fresh every tick: a settings change replaces
            # self._settings atomically (frozen dataclass, single reference
            # swap on the GTK thread) and wakes us, so the next wait already
            # uses the new period. getattr guards a bare test Application that
            # never attached a settings object.
            settings = getattr(self, "_settings", None)
            interval = settings.poll_seconds if settings is not None else POLL_SECONDS
            self._wake.wait(interval)
            self._wake.clear()

    def _apply_rows(self, collected: Collected) -> bool:
        self.window.set_rows(collected.rows)
        # Surface an unreachable-tmux hint in its own status slot, so a wedged
        # socket is not silently indistinguishable from every session ending.
        self.window.set_unreachable(collected.unreachable)
        return False  # one-shot idle source; True would re-run it forever

    def _apply_poll_error(self, message: str) -> bool:
        self.window.set_status("세션 목록을 새로 고치지 못했습니다: " + message)
        return False  # one-shot idle source; True would re-run it forever

    def _on_settings_changed(self, settings: config.Settings) -> None:
        """Called on the GTK thread when the dialog commits a change. The window
        already applied the visual part; here we only adopt the new interval and
        wake the poll thread so it does not wait out the old period first. The
        window itself persisted the file, so there is nothing to save here."""
        self._settings = settings
        self._wake.set()

    def stop(self) -> None:
        """Signal the poll thread to stop and wait for it to actually stop.

        Daemon threads mean a hang cannot wedge process exit on their own,
        but a clean join here means main() does not race a collect_rows call
        against interpreter shutdown.
        """
        self._stop.set()
        self._wake.set()
        self._poll_thread.join()

    # -- jump ---------------------------------------------------------------

    def jump(self, row: model.Row) -> None:
        if row.session_id in self._jumping:
            return  # a double click while the previous jump is still in flight
        self._jumping.add(row.session_id)
        self.window.set_row_jump_sensitive(row.session_id, False)

        def worker() -> None:
            try:
                status = perform_jump(row, tmuxctl.select_pane, gnome.activate_window_titled)
            except Exception as exc:  # noqa: BLE001 -- the button must come back regardless
                status = "이동 중 예기치 않은 오류가 발생했습니다: %s" % exc
            GLib.idle_add(self._on_jump_done, row.session_id, status)

        threading.Thread(target=worker, daemon=True).start()

    def _on_jump_done(self, session_id: str, status: str) -> bool:
        self._jumping.discard(session_id)
        self.window.set_row_jump_sensitive(session_id, True)
        self.window.set_status(status)
        return False

    # -- send -----------------------------------------------------------

    def send(self, row: model.Row, text: str) -> None:
        def worker() -> None:
            try:
                result = tmuxctl.send_text(row.socket, row.pane, text)
                status = send_status(result)
            except Exception as exc:  # noqa: BLE001 -- never leave the status stuck silently
                status = "전송 중 예기치 않은 오류가 발생했습니다: %s" % exc
            GLib.idle_add(self._on_send_done, status)

        threading.Thread(target=worker, daemon=True).start()

    def _on_send_done(self, status: str) -> bool:
        self.window.set_status(status)
        return False


def main(argv=None) -> int:
    import sys
    from . import __version__
    argv = sys.argv[1:] if argv is None else argv
    if "--version" in argv:
        print("cc-navigator %s" % __version__)
        return 0
    application = Application()
    # Wired here, not in NavigatorWindow: this is where the main loop exists.
    application.window.connect("destroy", Gtk.main_quit)
    application.window.show_all()
    Gtk.main()
    application.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
