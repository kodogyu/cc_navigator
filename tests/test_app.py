import os
import pathlib
import tempfile
import threading
import time
import unittest
from unittest import mock

from gi.repository import Gio, GLib

from ccnav import app, gnome, hookstate, model, paths, tmuxctl

SOCK = "/tmp/tmux-1000/default"


def record(pane="%1", socket=SOCK):
    return {
        "session_id": "a", "cwd": "/proj", "tmux_socket": socket, "tmux_pane": pane,
        "state": hookstate.WAITING, "reason": "idle", "message": "", "updated_at": 5,
    }


def row(session_id="a", socket=SOCK, pane="%1", tmux_session="demo", title="t"):
    return model.Row(
        session_id=session_id, socket=socket, pane=pane, tmux_session=tmux_session,
        title=title, state=hookstate.WAITING, reason="idle", message="",
        cwd="/proj", updated_at=5,
    )


def _pump_until(condition, timeout=2.0):
    """Drain the default GLib main context until `condition()` is true.

    GLib.idle_add only queues a callback; something has to iterate the main
    context to run it. Application deliberately never calls Gtk.main() in a
    test (forbidden), so pumping the default context directly is the
    equivalent of what Gtk.main() would do for these callbacks.
    """
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError("condition not met within %.1fs" % timeout)
        if context.pending():
            context.iteration(False)
        else:
            time.sleep(0.01)


class VersionTest(unittest.TestCase):
    def test_version_flag_prints_and_exits_zero(self):
        import io
        import contextlib
        from ccnav import __version__
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = app.main(["--version"])
        self.assertEqual(code, 0)
        self.assertIn(__version__, buf.getvalue())


class CollectRowsTest(unittest.TestCase):
    def test_queries_only_the_sockets_the_state_files_mention(self):
        asked = []

        def sessions_for(socket):
            asked.append(socket)
            return True, {"%1": "demo"}

        result = app.collect_rows(
            pathlib.Path("/nonexistent"),
            read_all=lambda d: [record()],
            sessions_for=sessions_for,
            titles_for=lambda s: {"%1": "t"},
            prune=lambda d, live, observed: 0,
        )
        self.assertEqual(asked, [SOCK])
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0].window_title, "ccnav:demo")
        self.assertEqual(result.unreachable, 0)

    def test_prunes_using_the_live_pane_set(self):
        seen = {}

        def fake_prune(directory, live, observed):
            seen["live"] = live
            seen["observed"] = observed
            return 0

        app.collect_rows(
            pathlib.Path("/nonexistent"),
            read_all=lambda d: [record()],
            sessions_for=lambda s: (True, {"%1": "demo", "%2": "sandbox"}),
            titles_for=lambda s: {},
            prune=fake_prune,
        )
        self.assertEqual(seen["live"], {(SOCK, "%1"), (SOCK, "%2")})
        self.assertEqual(seen["observed"], {SOCK})

    def test_a_socket_whose_query_failed_is_not_handed_to_prune(self):
        # F3: sessions_for reports ok=False (a slow or dead tmux). collect_rows
        # must exclude that socket from the observed set so prune leaves its
        # live state files alone. Without this a one-second stutter deletes a
        # waiting session that will never re-announce itself.
        seen = {}

        def fake_prune(directory, live, observed):
            seen["live"] = live
            seen["observed"] = observed
            return 0

        result = app.collect_rows(
            pathlib.Path("/nonexistent"),
            read_all=lambda d: [record()],
            sessions_for=lambda s: (False, {}),  # the query did not answer
            titles_for=lambda s: {},
            prune=fake_prune,
        )
        self.assertEqual(seen["observed"], set(), "a failed socket is not observed")
        self.assertEqual(seen["live"], set())
        self.assertEqual(result.rows, [], "with no live panes there is no row this tick")
        self.assertEqual(result.unreachable, 1, "the failed socket is reported to the UI")

    def test_no_state_files_means_no_tmux_calls_and_no_rows(self):
        def explode(socket):
            raise AssertionError("must not query tmux")

        result = app.collect_rows(
            pathlib.Path("/nonexistent"),
            read_all=lambda d: [],
            sessions_for=explode,
            titles_for=explode,
            prune=lambda d, live, observed: 0,
        )
        self.assertEqual(result.rows, [])
        self.assertEqual(result.unreachable, 0)

    def test_no_state_files_means_no_prune_either(self):
        # Deriving `sockets` from records and looping over it already makes a
        # tmux call structurally impossible when records is empty (the loop
        # body never runs). The one thing an accidentally-removed early
        # return *can* still change is calling prune with an empty live set
        # -- wasted work, and on a real directory it would delete every state
        # file present, which is exactly the bug "tmux is never called"
        # exists to keep this function cheap enough to avoid.
        def explode(directory, live, observed):
            raise AssertionError("must not prune when there is nothing to prune")

        result = app.collect_rows(
            pathlib.Path("/nonexistent"),
            read_all=lambda d: [],
            sessions_for=lambda s: (True, {}),
            titles_for=lambda s: {},
            prune=explode,
        )
        self.assertEqual(result.rows, [])


class JumpStatusTest(unittest.TestCase):
    def test_ok_single_match_is_silent(self):
        self.assertEqual(
            app.jump_status(gnome.ActivationResult(True, 1), "ccnav:demo"), ""
        )

    def test_not_ok_reports_the_activation_failure_in_korean(self):
        status = app.jump_status(gnome.ActivationResult(False, 0), "ccnav:demo")
        self.assertEqual(status, "창을 활성화하지 못했습니다: ccnav:demo")

    def test_ok_but_two_matched_warns_about_two_clients(self):
        status = app.jump_status(gnome.ActivationResult(True, 2), "ccnav:demo")
        self.assertIn("2개", status)
        self.assertIn("ccnav:demo", status)

    def test_not_ok_wins_over_matched_count(self):
        # A result cannot be both "activation failed" and "matched > 1" in
        # practice, but ok=False must take the failure branch regardless of
        # what matched says.
        status = app.jump_status(gnome.ActivationResult(False, 2), "ccnav:demo")
        self.assertEqual(status, "창을 활성화하지 못했습니다: ccnav:demo")


class PerformJumpTest(unittest.TestCase):
    def test_selects_the_pane_before_activating(self):
        order = []
        r = row()

        def fake_select(socket, pane):
            order.append(("select", socket, pane))

        def fake_activate(title):
            order.append(("activate", title))
            return gnome.ActivationResult(True, 1)

        status = app.perform_jump(r, fake_select, fake_activate)

        self.assertEqual(order, [("select", r.socket, r.pane), ("activate", r.window_title)])
        self.assertEqual(status, "")

    def test_still_activates_when_the_underlying_tmux_select_reports_failure(self):
        # select_pane (Task 6) always calls tmux and never surfaces its exit
        # code -- run() failing must not stop perform_jump from activating.
        activated = []
        r = row()
        always_fails = lambda argv: (1, "")

        def select_pane(socket, pane):
            tmuxctl.select_pane(socket, pane, run=always_fails)

        def activate(title):
            activated.append(title)
            return gnome.ActivationResult(True, 1)

        status = app.perform_jump(r, select_pane, activate)

        self.assertEqual(activated, [r.window_title])
        self.assertEqual(status, "")

    def test_returns_the_status_jump_status_would_compute(self):
        r = row()
        status = app.perform_jump(
            r,
            select_pane=lambda socket, pane: None,
            activate=lambda title: gnome.ActivationResult(False, 0),
        )
        self.assertEqual(status, app.jump_status(gnome.ActivationResult(False, 0), r.window_title))


class OnStateChangedTest(unittest.TestCase):
    """Mutation 7: Application must not refresh from the FileMonitor callback
    on the calling thread. It must only wake the poller.

    This constructs a bare Application via __new__ (bypassing __init__, so no
    GTK, no display, no thread is ever started) and monkeypatches the
    module-level collect_rows, and gives the instance a "poison" window whose
    set_rows() also raises. Either one firing means the callback did real
    work here instead of just setting the event -- exactly what a
    reintroduced "refresh on the main thread" bug would do. Using a stub
    object rather than None also means the failure -- if this regresses --
    reads as a clear assertion about the contract, not an incidental
    AttributeError from touching a None.
    """

    def test_only_sets_the_wake_event_and_never_touches_window_or_tmux(self):
        def explode_collect_rows(*_a, **_k):
            raise AssertionError(
                "_on_state_changed must not call collect_rows -- it must only "
                "wake the poll thread"
            )

        class PoisonWindow:
            def set_rows(self, rows):
                raise AssertionError(
                    "_on_state_changed must not touch self.window -- refreshing "
                    "here would run tmux on the GTK main thread"
                )

        original = app.collect_rows
        app.collect_rows = explode_collect_rows
        try:
            instance = app.Application.__new__(app.Application)
            instance._wake = threading.Event()
            instance.state_dir = pathlib.Path("/nonexistent")
            instance.window = PoisonWindow()

            app.Application._on_state_changed(instance)
        finally:
            app.collect_rows = original

        self.assertTrue(instance._wake.is_set())


class _FakeWindow:
    """A stand-in for ui.NavigatorWindow that records what Application asked
    of it, without needing a display."""

    def __init__(self):
        self.sensitivity = {}
        self.status = None
        self.rows = None
        self.unreachable = None

    def set_row_jump_sensitive(self, session_id, sensitive):
        self.sensitivity[session_id] = sensitive

    def set_status(self, text):
        self.status = text

    def set_rows(self, rows):
        self.rows = rows

    def set_unreachable(self, count):
        self.unreachable = count


def _bare_application():
    instance = app.Application.__new__(app.Application)
    instance.window = _FakeWindow()
    instance._jumping = set()
    return instance


class ApplicationJumpThreadingTest(unittest.TestCase):
    """Exercises Application.jump()'s thread + GLib.idle_add hand-off with no
    GTK widgets, no display and no real tmux/gdbus: tmuxctl.select_pane and
    gnome.activate_window_titled are monkeypatched at module level, the same
    technique as OnStateChangedTest. GLib.idle_add itself needs no display --
    only creating actual GTK widgets does -- so this runs under any DISPLAY.
    """

    def setUp(self):
        self._orig_select = tmuxctl.select_pane
        self._orig_activate = gnome.activate_window_titled

    def tearDown(self):
        tmuxctl.select_pane = self._orig_select
        gnome.activate_window_titled = self._orig_activate

    def test_disables_then_reenables_the_button_around_a_successful_jump(self):
        release = threading.Event()
        order = []

        def fake_select(socket, pane):
            order.append("select")

        def fake_activate(title):
            order.append("activate")
            self.assertTrue(release.wait(2.0), "release was never set")
            return gnome.ActivationResult(True, 1)

        tmuxctl.select_pane = fake_select
        gnome.activate_window_titled = fake_activate

        instance = _bare_application()
        r = row()
        instance.jump(r)

        # Disabled synchronously, before the thread has done anything.
        self.assertEqual(instance.window.sensitivity.get(r.session_id), False)

        release.set()
        _pump_until(lambda: instance.window.sensitivity.get(r.session_id) is True)

        self.assertEqual(order, ["select", "activate"])
        self.assertEqual(instance.window.status, "")
        self.assertNotIn(r.session_id, instance._jumping)

    def test_a_second_click_while_a_jump_is_in_flight_does_not_start_another(self):
        started = threading.Event()
        release = threading.Event()
        activate_calls = []

        def fake_select(socket, pane):
            pass

        def fake_activate(title):
            activate_calls.append(title)
            started.set()
            release.wait(2.0)
            return gnome.ActivationResult(True, 1)

        tmuxctl.select_pane = fake_select
        gnome.activate_window_titled = fake_activate

        instance = _bare_application()
        r = row()
        instance.jump(r)
        self.assertTrue(started.wait(2.0), "the first jump never started")

        instance.jump(r)  # the "double click"

        release.set()
        _pump_until(lambda: r.session_id not in instance._jumping)

        self.assertEqual(len(activate_calls), 1)

    def test_the_button_is_reenabled_even_if_the_jump_thread_raises(self):
        def fake_select(socket, pane):
            pass

        def fake_activate(title):
            raise RuntimeError("boom")

        tmuxctl.select_pane = fake_select
        gnome.activate_window_titled = fake_activate

        instance = _bare_application()
        r = row()
        instance.jump(r)

        _pump_until(lambda: instance.window.sensitivity.get(r.session_id) is True)

        self.assertNotIn(r.session_id, instance._jumping)
        self.assertTrue(instance.window.status)  # a message, not silence


class SendStatusTest(unittest.TestCase):
    """The pure decision, testable with no threads, GTK or subprocess."""

    def test_full_success_is_silent(self):
        result = tmuxctl.SendResult(delivered=True, submitted=True)
        self.assertEqual(app.send_status(result), "")

    def test_not_delivered_explains_it(self):
        result = tmuxctl.SendResult(delivered=False, submitted=False)
        status = app.send_status(result)
        self.assertTrue(status)

    def test_delivered_but_not_submitted_is_distinct_from_not_delivered(self):
        not_submitted = app.send_status(
            tmuxctl.SendResult(delivered=True, submitted=False)
        )
        not_delivered = app.send_status(
            tmuxctl.SendResult(delivered=False, submitted=False)
        )
        self.assertTrue(not_submitted)
        self.assertNotEqual(
            not_submitted, not_delivered, "the two failures must read differently"
        )

    def test_each_message_names_its_own_cause(self):
        # Without pinning content, swapping the two messages passes the suite --
        # the user would get the wrong diagnostic. Only the submit-failure names
        # the Enter step; a delivery failure never reached Enter, so it must not.
        not_submitted = app.send_status(
            tmuxctl.SendResult(delivered=True, submitted=False)
        )
        not_delivered = app.send_status(
            tmuxctl.SendResult(delivered=False, submitted=False)
        )
        self.assertIn("Enter", not_submitted)
        self.assertNotIn("Enter", not_delivered)


class ApplicationSendThreadingTest(unittest.TestCase):
    def setUp(self):
        self._orig_send = tmuxctl.send_text

    def tearDown(self):
        tmuxctl.send_text = self._orig_send

    def test_send_runs_off_thread_and_clears_status_on_success(self):
        calls = []

        def fake_send_text(socket, pane, text):
            calls.append((socket, pane, text))
            return tmuxctl.SendResult(delivered=True, submitted=True)

        tmuxctl.send_text = fake_send_text

        instance = _bare_application()
        r = row()
        instance.window.status = "stale"
        instance.send(r, "hello")

        _pump_until(lambda: instance.window.status == "")
        self.assertEqual(calls, [(r.socket, r.pane, "hello")])

    def test_send_reports_a_delivery_failure_instead_of_looking_successful(self):
        # F2: the reply never reached the pane (dead server), but the old code
        # discarded the exit code and left the status blank -- a silent success.
        def fake_send_text(socket, pane, text):
            return tmuxctl.SendResult(delivered=False, submitted=False)

        tmuxctl.send_text = fake_send_text

        instance = _bare_application()
        instance.send(row(), "hello")

        _pump_until(lambda: instance.window.status not in (None, ""))
        self.assertTrue(instance.window.status)

    def test_send_reports_typed_but_not_submitted(self):
        def fake_send_text(socket, pane, text):
            return tmuxctl.SendResult(delivered=True, submitted=False)

        tmuxctl.send_text = fake_send_text

        instance = _bare_application()
        instance.send(row(), "hello")

        _pump_until(lambda: instance.window.status not in (None, ""))
        self.assertTrue(instance.window.status)

    def test_send_reports_an_error_instead_of_dying_silently(self):
        def fake_send_text(socket, pane, text):
            raise RuntimeError("tmux is gone")

        tmuxctl.send_text = fake_send_text

        instance = _bare_application()
        r = row()
        instance.send(r, "hello")

        _pump_until(lambda: instance.window.status not in (None, ""))
        self.assertIn("tmux is gone", instance.window.status)


class PollLoopTest(unittest.TestCase):
    """The poll thread must survive a collector that raises. A dead poller
    leaves the window frozen on stale rows while looking alive -- the
    silent-success failure this project exists to catch, so a crash that at
    least stops the loop cleanly and shows why is strictly better than a
    thread that vanishes and leaves a healthy-looking, frozen window.

    Runs _poll_loop directly on the test thread (no real background thread):
    the injected collector drives the loop -- it raises on the first
    iteration and stops the loop on the second -- and GLib.idle_add callbacks
    are drained afterwards by pumping the default main context, exactly as
    Gtk.main() would. The collector is injected via Application.__init__'s
    `collect=` seam, set here directly on a bare __new__ instance.
    """

    def test_survives_a_raising_collect_keeps_polling_and_posts_a_status(self):
        instance = app.Application.__new__(app.Application)
        instance.window = _FakeWindow()
        instance._stop = threading.Event()
        instance._wake = threading.Event()
        instance.state_dir = pathlib.Path("/nonexistent")

        calls = []

        def flaky_collect(state_dir):
            calls.append(state_dir)
            instance._wake.set()  # keep the loop's wait from blocking a full second
            if len(calls) == 1:
                raise RuntimeError("prune could not delete a stale file")
            instance._stop.set()  # exit after the second, recovered iteration
            return app.Collected([], 0)

        instance._collect = flaky_collect

        instance._poll_loop()  # returns only because the loop kept going past the raise

        # The body ran again after the raise: the loop did not die on iteration 1.
        self.assertGreaterEqual(len(calls), 2)

        # Both idle callbacks (the error from #1, the empty rows from #2) drain.
        _pump_until(
            lambda: instance.window.status is not None and instance.window.rows == []
        )
        self.assertIn("prune could not delete a stale file", instance.window.status)

    def test_an_unreachable_socket_is_surfaced_to_the_window(self):
        # F3 fix's other half: a wedged tmux must not silently drop a live row.
        # collect_rows returns unreachable>0, and Application must hand that to
        # the window so the user sees a hint instead of an unexplained gap.
        instance = app.Application.__new__(app.Application)
        instance.window = _FakeWindow()
        instance._stop = threading.Event()
        instance._wake = threading.Event()
        instance.state_dir = pathlib.Path("/nonexistent")

        def collect(state_dir):
            instance._wake.set()
            instance._stop.set()
            return app.Collected([], 2)

        instance._collect = collect
        instance._poll_loop()
        _pump_until(lambda: instance.window.unreachable == 2)
        self.assertEqual(instance.window.unreachable, 2)


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs an X11 display")
class ApplicationWiringTest(unittest.TestCase):
    """Constructs a real Application -- a real ui.NavigatorWindow exists --
    but never calls show(), show_all() or present(), so nothing appears on
    the user's screen. state_dir and eval_available are monkeypatched so
    __init__ never touches the user's real state directory or spawns a real
    gdbus call.
    """

    def test_keeps_a_monitor_reference_and_stop_joins_the_poll_thread(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state_dir = pathlib.Path(tmp.name)

        orig_ensure = paths.ensure_state_dir
        orig_eval = gnome.eval_available
        paths.ensure_state_dir = lambda: state_dir
        gnome.eval_available = lambda run=None: True
        try:
            instance = app.Application()
        finally:
            paths.ensure_state_dir = orig_ensure
            gnome.eval_available = orig_eval

        try:
            self.assertIsInstance(instance._monitor, Gio.FileMonitor)
            self.assertTrue(instance._poll_thread.is_alive())

            # The empty state dir means collect_rows never touches tmux, so
            # this is safe to let run for real.
            instance.stop()
            self.assertFalse(instance._poll_thread.is_alive())
        finally:
            instance.window.destroy()


class EvalProbeTest(unittest.TestCase):
    def _fake_run(self, seen, stdout="(true, '2')\n"):
        def fake_run(argv, timeout=None):
            seen["argv"] = list(argv)
            seen["timeout"] = timeout
            return 0, stdout

        return fake_run

    def test_the_probe_is_bounded_more_tightly_than_an_ordinary_command(self):
        # Eval("1+1") is a local D-Bus round trip. Letting it wait the full
        # DEFAULT_TIMEOUT would hold the screen blank before the window is even
        # mapped, with nothing for the user to look at.
        seen = {}
        with mock.patch.object(app.proc, "run_command", self._fake_run(seen)):
            self.assertTrue(app.probe_eval_available())

        self.assertEqual(seen["argv"][0], "gdbus")
        self.assertLess(seen["timeout"], app.proc.DEFAULT_TIMEOUT)

    def test_a_wedged_gdbus_fails_to_the_safe_side(self):
        # 124 is what run_command returns on timeout. Eval must then count as
        # unavailable, so the jump buttons stay disabled and the hint explains why.
        def timed_out(argv, timeout=None):
            return 124, ""

        with mock.patch.object(app.proc, "run_command", timed_out):
            self.assertFalse(app.probe_eval_available())
