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

import fcntl
import os
import pathlib
import time
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import (codexsession, config, gnome, model, notify, paths, proc,
               procstat, statestore, tmuxctl, ui, usage, vscodestate,
               wiring)  # noqa: E402

# Fallback only: the live interval comes from self._settings.poll_seconds. Kept
# so a bare Application built in a test (which does not load config) still polls
# at a sane rate before a settings object is attached.
POLL_SECONDS = 1

# The optional local ccusage estimate changes slowly and runs in its own thread.
USAGE_POLL_SECONDS = 300

# Eval("1+1") is a local D-Bus round trip and answers in milliseconds. The probe
# runs before the window is mapped, so it cannot freeze a live window -- but on a
# wedged gdbus it would hold the screen blank for DEFAULT_TIMEOUT with nothing to
# look at. Bound it tightly and fail to the safe side: Eval counts as unavailable,
# the jump buttons stay disabled, and EVAL_UNAVAILABLE_HINT explains why.
EVAL_PROBE_TIMEOUT = 1.0

# SessionStart can beat VS Code's asynchronous workbench-state write by a few
# ticks. Do not judge a brand-new record against a temporarily old DB snapshot.
VSCODE_UI_STATE_GRACE_SECONDS = 5


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


def _vscode_pids(records: List[Dict[str, object]]) -> Set[object]:
    """Process identity keys carried by VSCode records.

    New records use (pid, kernel-start-time); old records remain integer-only
    until a hook refreshes them. The tuple prevents same-name PID reuse.
    """
    pids = set()  # type: Set[object]
    for rec in records:
        if str(rec.get("kind") or "") != "vscode":
            continue
        try:
            pid = int(rec.get("claude_pid", 0))
        except (TypeError, ValueError):
            continue
        if pid > 0:
            try:
                started = int(rec.get("claude_start_time", 0))
            except (TypeError, ValueError):
                started = 0
            pids.add((pid, started) if started > 0 else pid)
    return pids


def _vscode_ui_sessions(
    records: List[Dict[str, object]],
    visible: Callable[[str, str], Optional[bool]],
    now: int,
) -> "tuple":
    """(live, observed) VS Code session ids from persisted workbench UI state.

    A None result is deliberately unobserved: unsupported/missing/locked state
    must retain process-only liveness. A fresh record also gets a short grace
    window because its SessionStart hook and VS Code's editor-state persistence
    are asynchronous.
    """
    live = set()  # type: Set[str]
    observed = set()  # type: Set[str]
    for rec in records:
        if str(rec.get("kind") or "") != "vscode":
            continue
        session_id = str(rec.get("session_id") or "")
        if not session_id:
            continue
        try:
            updated_at = int(rec.get("updated_at", 0))
        except (TypeError, ValueError):
            updated_at = 0
        if now - updated_at < VSCODE_UI_STATE_GRACE_SECONDS:
            continue
        try:
            result = visible(session_id, str(rec.get("cwd") or ""))
        except Exception:  # a local-state probe must never stop the poll thread
            result = None
        if result is None:
            continue
        observed.add(session_id)
        if result:
            live.add(session_id)
    return live, observed


def collect_rows(
    state_dir: pathlib.Path,
    read_all: Callable[[pathlib.Path], List[Dict[str, object]]] = statestore.read_all,
    sessions_for: Callable[[str], "tuple"] = tmuxctl.sessions_by_pane_result,
    titles_for: Callable[[str], Dict[str, str]] = tmuxctl.titles_by_pane,
    prune: Callable[..., int] = statestore.prune,
    socket_candidates: Callable[[], List[str]] = paths.tmux_sockets,
    pane_processes_for: Callable[[str], Dict[str, tmuxctl.PaneProcess]] = (
        tmuxctl.pane_processes_by_pane),
    find_codex: Callable[[int], Optional[codexsession.CodexProcess]] = (
        codexsession.find_codex_process),
    live_pids_for: Callable[[Set[object]], Set[object]] = procstat.live_claude_pids,
    vscode_session_visible: Callable[[str, str], Optional[bool]] = (
        vscodestate.session_visible),
) -> Collected:
    now = int(time.time())
    records = read_all(state_dir)
    recorded_sockets = {
        str(r.get("tmux_socket") or "") for r in records if r.get("tmux_socket")
    }
    # Codex currently emits its first lifecycle hook only after the first
    # submitted prompt, so also inspect same-user tmux sockets for a real Codex
    # process. Claude/VSCode records continue to provide their own addresses.
    sockets = sorted(recorded_sockets | set(socket_candidates()))
    observed_pids = _vscode_pids(records)
    if not sockets and not observed_pids:
        # Nothing to observe: no tmux candidate and no VSCode session. Return
        # before touching tmux or prune -- an empty directory must cost nothing.
        return Collected([], 0)
    # sessions_for reports (ok, panes): ok is False when tmux did not answer
    # (dead socket, or a timed-out slow one). Only sockets that DID answer may
    # gate pruning -- otherwise a one-tick stutter deletes live state (F3).
    results = {socket: sessions_for(socket) for socket in sockets}
    sessions = {socket: panes for socket, (_ok, panes) in results.items()}
    observed = {socket for socket, (ok, _panes) in results.items() if ok}
    titles = {socket: titles_for(socket) for socket in sockets}
    # Codex's first SessionStart is deferred until the first submitted prompt in
    # current TUI releases.  Discover a real Codex process before that hook and
    # append a non-persistent candidate record.  model._newest_per_pane chooses
    # it over stale state from an older command, then chooses the first real hook
    # over it by timestamp.  Short-listing on tmux's foreground command avoids
    # walking unrelated pane process trees.
    for socket in observed:
        for pane, pane_process in pane_processes_for(socket).items():
            command = pane_process.command.lower()
            if (command not in ("node", "nodejs", "codex")
                    and not command.startswith("codex")):
                continue
            process = find_codex(pane_process.pid)
            if process is not None and pane in sessions.get(socket, {}):
                records.append(codexsession.provisional_record(socket, pane, process))

    # Kernel liveness for the VSCode sessions: process identity plus, where the
    # platform exposes it, the stream-json stdio peer. The Claude backend can
    # outlive a closed editor tab, while its disconnected transport tells us the
    # session UI is already gone. Keep the same "observed vs live" split tmux
    # uses, so prune never reaps a pid it did not actually check this tick.
    live_pids = live_pids_for(observed_pids)
    live_vscode_sessions, observed_vscode_sessions = _vscode_ui_sessions(
        records, vscode_session_visible, now)
    prune(
        state_dir,
        model.live_pane_keys(sessions),
        observed,
        live_pids=live_pids,
        observed_pids=observed_pids,
    )
    # Hide a positively closed VS Code UI without deleting its state record.
    # The extension may reuse the same still-running backend when its sidebar is
    # shown again and emit no new lifecycle hook; retaining the record lets the
    # row return as soon as workbench state says the UI is visible again.
    records = [
        rec for rec in records
        if not (
            str(rec.get("kind") or "") == "vscode"
            and str(rec.get("session_id") or "") in observed_vscode_sessions
            and str(rec.get("session_id") or "") not in live_vscode_sessions
        )
    ]
    rows = model.build_rows(
        records, sessions, titles, live_pids=live_pids, now=now)
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


def _default_activate_vscode(row: model.Row) -> gnome.ActivationResult:
    """A VSCode row is addressed by its session id (tab-precise), with the
    workspace folder as the focus-verification target and window fallback."""
    return gnome.activate_vscode_session(row.session_id, row.vscode_folder)


def perform_jump(
    row: model.Row,
    select_pane: Callable[[str, str], None],
    activate: Callable[[str], gnome.ActivationResult],
    activate_vscode: Callable[[model.Row], gnome.ActivationResult] = _default_activate_vscode,
) -> str:
    """Raise the session's window (VSCode: its tab) and report the outcome.

    A VSCode session has no tmux pane to select -- its address is the editor tab,
    reached by session id -- so it skips select_pane. A tmux session selects its
    pane before activating: tmux needs the pane selected before the window is
    raised, or the user lands on the wrong pane. select_pane ignores tmux's exit
    codes by design (Task 6), so there is nothing to branch on there -- activate
    always runs, even when selection failed.
    """
    if row.is_vscode:
        result = activate_vscode(row)
        # Label the status with the session's own headline when it has one, so a
        # failure names the specific session, not just its (shared) folder.
        label = row.title or row.vscode_folder
        return jump_status(result, "%s (VS Code)" % label)
    select_pane(row.socket, row.pane)
    result = activate(row.window_title)
    return jump_status(result, row.window_title)


class Application:
    def __init__(
        self,
        collect: Callable[[pathlib.Path], List[model.Row]] = collect_rows,
        probe_eval: Callable[[], bool] = probe_eval_available,
        usage_fetch: Optional[Callable[[], object]] = None,
    ) -> None:
        # `collect` is injectable so the poll loop's error handling can be
        # tested with a collector that raises, without GTK or a real tmux.
        self._collect = collect
        # The worker exists independently of the account-usage button. Calls to
        # this external-tool seam are still gated by Settings.ccusage_enabled.
        self._usage_fetch = usage_fetch
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

        # Desktop-notification state: the last-seen status per session id, and a
        # first-tick guard so startup does not burst one notification per already-
        # waiting session. _notify_send is injectable for tests.
        self._prev_status = {}  # type: Dict[str, str]
        self._notif_seeded = False
        self._notify_send = notify.send

        monitor = Gio.File.new_for_path(str(self.state_dir)).monitor_directory(
            Gio.FileMonitorFlags.NONE, None
        )
        monitor.connect("changed", self._on_state_changed)
        self._monitor = monitor  # keep a reference or it is collected and the watch stops

        self._stop = threading.Event()
        self._wake = threading.Event()
        self._usage_wake = threading.Event()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        # Usage runs on its OWN thread, not the 1s poll loop: ccusage takes ~1s
        # and the weekly total barely moves, so it fetches on a slow cadence.
        self._usage_thread = None
        if self._usage_fetch is not None:
            self._usage_thread = threading.Thread(target=self._usage_loop, daemon=True)
            self._usage_thread.start()

    # -- optional external usage tool -------------------------------------

    def _usage_loop(self) -> None:
        """Run ccusage only after explicit opt-in, never merely at startup."""
        while not self._stop.is_set():
            snapshot = None
            if self._settings.ccusage_enabled:
                try:
                    snapshot = self._usage_fetch()
                except Exception:  # a broken external tool must not end the thread
                    snapshot = usage.TokenUsage(None, None, usage.ERR_CCUSAGE_FAILED)
            # A user can disable the option while a subprocess is in flight. Do
            # not re-show its result after consent has been withdrawn.
            if not self._settings.ccusage_enabled:
                snapshot = None
            GLib.idle_add(self._apply_token_usage, snapshot)
            if self._stop.is_set():
                break
            self._usage_wake.wait(USAGE_POLL_SECONDS)
            self._usage_wake.clear()

    def _apply_token_usage(self, snapshot) -> bool:
        self.window.set_token_usage(snapshot)
        return False  # one-shot idle source

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
        self._maybe_notify(collected)
        return False  # one-shot idle source; True would re-run it forever

    def _maybe_notify(self, collected: Collected) -> None:
        """Fire a desktop notification for each session that just became 'your
        turn' (transitioned into input-needed or reported). Runs on the GTK main
        thread; the actual notify-send is dispatched to a worker thread."""
        # A tick with an unreachable socket is BLIND, not empty: a tmux query that
        # did not answer makes build_rows drop every row on that socket (which is
        # what `unreachable` exists to report). Rebaselining on that erased the
        # baseline, so the next good tick saw every session as new and re-fired a
        # popup for all of them; and a stuttered first tick spent the startup guard
        # on an empty baseline and then burst. A blind tick must change nothing.
        if collected.unreachable:
            return
        fires, new_status = notify.changed_rows(self._prev_status, collected.rows)
        self._prev_status = new_status
        # The first tick has no prior statuses, so every waiting session would
        # look 'new'. Seed the baseline silently and only notify from the second
        # tick on. The baseline is updated above even when notifications are off,
        # so toggling them back on does not replay a backlog.
        if not self._notif_seeded:
            self._notif_seeded = True
            return
        if not self._settings.notifications:
            return
        for row, status in fires:
            self._send_notification_async(row, status)

    def _send_notification_async(self, row: model.Row, status: str) -> None:
        # notify-send is a subprocess; never run it on the GTK main thread (the
        # freeze this whole app is built to avoid). Same daemon-thread hand-off
        # as jump/send.
        threading.Thread(
            target=lambda: self._notify_send(row, status), daemon=True
        ).start()

    def _apply_poll_error(self, message: str) -> bool:
        self.window.set_status("세션 목록을 새로 고치지 못했습니다: " + message)
        return False  # one-shot idle source; True would re-run it forever

    def _on_settings_changed(self, settings: config.Settings) -> None:
        """Called on the GTK thread when the dialog commits a change. The window
        already applied the visual part; here we only adopt the new interval and
        wake the poll thread so it does not wait out the old period first. The
        window itself persisted the file, so there is nothing to save here."""
        ccusage_changed = self._settings.ccusage_enabled != settings.ccusage_enabled
        self._settings = settings
        self._wake.set()
        if ccusage_changed:
            self._usage_wake.set()

    def refresh(self) -> None:
        """Force an immediate poll: wake the poll thread so collect_rows re-runs
        now, pruning any pane already gone from tmux. Inherits the poll loop's
        survive-a-raising-collect behaviour."""
        self._wake.set()

    def stop(self) -> None:
        """Signal the poll thread to stop and wait for it to actually stop.

        Daemon threads mean a hang cannot wedge process exit on their own,
        but a clean join here means main() does not race a collect_rows call
        against interpreter shutdown.
        """
        self._stop.set()
        self._wake.set()
        self._usage_wake.set()
        self._poll_thread.join()
        # Wake and join the optional tool scheduler too. A subprocess already in
        # flight remains bounded by usage.CCUSAGE_TIMEOUT.
        if self._usage_thread is not None:
            self._usage_thread.join(timeout=1.0)

    # -- jump ---------------------------------------------------------------

    def jump(self, row: model.Row) -> None:
        if row.session_id in self._jumping:
            return  # a double click while the previous jump is still in flight
        self._jumping.add(row.session_id)
        self.window.set_row_jump_sensitive(row.session_id, False)

        def worker() -> None:
            try:
                status = perform_jump(
                    row,
                    tmuxctl.select_pane,
                    gnome.activate_window_titled,
                )
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
        if row.is_vscode:
            # A VSCode session has no pane to type into; only the window jump is
            # supported. Say so plainly instead of routing an empty socket/pane
            # into tmux, which would fail with a misleading "could not confirm
            # delivery" message.
            self.window.set_status(
                "VS Code 세션은 답장을 지원하지 않습니다. '세션으로 이동'만 가능합니다."
            )
            return

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


# Held open for the whole process lifetime so the flock below is not released.
# Closing the fd (or the process dying) frees the lock; that is exactly the
# lifetime we want, so this is never explicitly closed.
_instance_lock_fd = None  # type: Optional[int]


def acquire_single_instance(lock_path: pathlib.Path) -> Optional[int]:
    """Take an exclusive, non-blocking flock. Return the held fd, or None if
    another live instance already holds it. The lock is tied to the open file
    description, so a crash releases it automatically -- no stale pidfile to
    reap. Returns -1 (a truthy sentinel, not None) if the lock file itself can't
    be opened, so a locking failure never blocks the app from starting."""
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return -1
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def main(argv=None) -> int:
    import sys
    from . import __version__
    argv = sys.argv[1:] if argv is None else argv
    if "--version" in argv:
        print("cc-navigator %s" % __version__)
        return 0
    # Give the window a unique, stable WM_CLASS (res_name via prgname, res_class
    # via program_class) equal to the launcher's basename/StartupWMClass, so GNOME
    # binds the running window to the installed .desktop -- otherwise it inherits
    # a generic "python3" class and the dock shows a stray unnamed icon instead of
    # this app's. Must run before the window is created (below), so WM_CLASS is set
    # when it is realized.
    GLib.set_prgname(wiring.APP_ID)
    Gdk.set_program_class(wiring.APP_ID)

    # Single instance: the panel is a skip-taskbar utility window, so GNOME does
    # not track it as a running app -- clicking the dock launcher would otherwise
    # spawn a SECOND panel instead of focusing the one already open. Guard with an
    # flock: if another instance holds it, raise that window (matched by the
    # WM_CLASS we set above) and exit, so the launcher click reads as "focus the
    # running panel". A lock error returns the -1 sentinel and we start normally.
    global _instance_lock_fd
    try:
        lock_path = paths.ensure_state_dir() / "instance.lock"
        _instance_lock_fd = acquire_single_instance(lock_path)
    except OSError:
        _instance_lock_fd = -1
    if _instance_lock_fd is None:
        gnome.activate_window_by_class(wiring.APP_ID)
        return 0

    application = Application(usage_fetch=usage.fetch_token_usage)
    # Wired here, not in NavigatorWindow: this is where the main loop exists.
    application.window.connect("destroy", Gtk.main_quit)
    application.window.show_all()
    Gtk.main()
    application.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
