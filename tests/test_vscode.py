"""Feature-level tests for VSCode (non-tmux, extension-hosted) sessions.

The VSCode path spans hook -> model -> statestore -> gnome -> app. Each layer
has its own unit tests; this file pins the behaviour that only makes sense as a
whole: a session with no tmux pane still appears (keyed by a live claude pid) and
a jump raises its editor window rather than a tmux pane.
"""
import pathlib
import tempfile
import unittest

from ccnav import app, gnome, hook, hookstate, model, statestore

VSCODE_ENV = {"CLAUDE_CODE_ENTRYPOINT": "claude-vscode"}  # no TMUX / TMUX_PANE


def vscode_payload(session_id="v1", cwd="/home/u/robotics/datamil"):
    return {
        "session_id": session_id,
        "cwd": cwd,
        "hook_event_name": "Stop",
    }


class HookVscodeTest(unittest.TestCase):
    def test_records_a_vscode_session_with_its_claude_pid(self):
        rec = hook.build_record(
            vscode_payload(), VSCODE_ENV, now=100,
            find_claude_pid=lambda: 4321,
            find_claude_start_time=lambda pid: 9876,
        )
        self.assertEqual(rec["kind"], "vscode")
        self.assertEqual(rec["claude_pid"], 4321)
        self.assertEqual(rec["claude_start_time"], 9876)
        self.assertEqual(rec["tmux_socket"], "")
        self.assertEqual(rec["tmux_pane"], "")
        self.assertEqual(rec["cwd"], "/home/u/robotics/datamil")

    def test_no_tmux_and_not_vscode_is_still_none(self):
        # The one behaviour that must NOT change: a plain non-tmux session (a bare
        # terminal, no VSCode) remains unaddressable.
        self.assertIsNone(hook.build_record(vscode_payload(), {}, now=100))

    def test_vscode_without_a_findable_claude_pid_is_none(self):
        # No owning process means no liveness signal, so there is nothing to key
        # a row on -- drop it rather than record a session that can never expire.
        self.assertIsNone(
            hook.build_record(
                vscode_payload(), VSCODE_ENV, now=100, find_claude_pid=lambda: 0
            )
        )


class AiTitleTest(unittest.TestCase):
    def _write(self, lines):
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as fh:
            fh.write("\n".join(lines))
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return path

    def test_reads_the_last_ai_title(self):
        path = self._write([
            '{"type":"ai-title","aiTitle":"first name","sessionId":"v1"}',
            '{"type":"user","message":{"content":"hi"}}',
            '{"type":"ai-title","aiTitle":"CA-Select 실험 결과 분석","sessionId":"v1"}',
        ])
        self.assertEqual(hook._last_ai_title(path), "CA-Select 실험 결과 분석")

    def test_missing_or_titleless_transcript_is_empty(self):
        self.assertEqual(hook._last_ai_title(""), "")
        self.assertEqual(hook._last_ai_title("/no/such/file.jsonl"), "")
        path = self._write(['{"type":"user","message":{"content":"hi"}}'])
        self.assertEqual(hook._last_ai_title(path), "")

    def test_a_partial_first_line_from_the_tail_cut_is_skipped(self):
        # A tail read can start mid-line; that garbage line must not break parsing
        # of the good records after it.
        path = self._write([
            'e":"ai-title","aiTitle":"BROKEN"}',  # truncated leading fragment
            '{"type":"ai-title","aiTitle":"good title","sessionId":"v1"}',
        ])
        self.assertEqual(hook._last_ai_title(path), "good title")

    def test_hook_stores_ai_title_for_a_vscode_session(self):
        path = self._write([
            '{"type":"ai-title","aiTitle":"내 세션 이름","sessionId":"v1"}',
        ])
        rec = hook.build_record(
            {"session_id": "v1", "cwd": "/p", "hook_event_name": "Stop",
             "transcript_path": path},
            VSCODE_ENV, now=1, find_claude_pid=lambda: 7,
        )
        self.assertEqual(rec["ai_title"], "내 세션 이름")


class ModelVscodeTest(unittest.TestCase):
    def _rec(self, session_id="v1", pid=4321, cwd="/home/u/robotics/datamil"):
        return {
            "session_id": session_id, "cwd": cwd, "kind": "vscode", "claude_pid": pid,
            "tmux_socket": "", "tmux_pane": "", "state": hookstate.WAITING,
            "reason": "idle", "message": "", "updated_at": 7, "last_prompt": "fix bug",
        }

    def test_row_appears_when_its_pid_is_live(self):
        rows = model.build_rows([self._rec()], {}, {}, live_pids={4321})
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertTrue(r.is_vscode)
        self.assertEqual(r.vscode_folder, "datamil")
        self.assertEqual(r.claude_pid, 4321)

    def test_row_absent_when_its_pid_is_dead(self):
        self.assertEqual(model.build_rows([self._rec()], {}, {}, live_pids=set()), [])

    def test_ai_title_is_the_headline_over_the_prompt(self):
        from ccnav import ui
        rec = dict(self._rec(), ai_title="CA-Select 실험 결과 분석", last_prompt="fix bug")
        row = model.build_rows([rec], {}, {}, live_pids={4321})[0]
        self.assertEqual(row.ai_title, "CA-Select 실험 결과 분석")
        self.assertEqual(ui.primary_line(row), "CA-Select 실험 결과 분석")

    def test_falls_back_to_folder_when_no_title_or_prompt(self):
        from ccnav import ui
        rec = dict(self._rec(), ai_title="", last_prompt="")
        row = model.build_rows([rec], {}, {}, live_pids={4321})[0]
        self.assertEqual(ui.primary_line(row), "datamil")  # tmux_session fallback

    def test_two_sessions_same_folder_stay_distinct(self):
        recs = [self._rec("v1", 1), self._rec("v2", 2)]
        rows = model.build_rows(recs, {}, {}, live_pids={1, 2})
        self.assertEqual({r.session_id for r in rows}, {"v1", "v2"})

    def test_same_name_pid_reuse_does_not_keep_the_old_session(self):
        rec = dict(self._rec(), claude_start_time=100)
        self.assertEqual(
            model.build_rows([rec], {}, {}, live_pids={(4321, 200)}), [])


class PruneVscodeTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())

    def _write(self, session_id, pid, started=0):
        statestore.write(self.dir, {
            "session_id": session_id, "cwd": "/p", "kind": "vscode", "claude_pid": pid,
            "claude_start_time": started,
            "tmux_socket": "", "tmux_pane": "", "state": "waiting", "reason": "idle",
            "message": "", "updated_at": 7,
        })

    def test_dead_pid_is_reaped(self):
        self._write("v1", 4321)
        removed = statestore.prune(
            self.dir, set(), set(), live_pids=set(), observed_pids={4321}
        )
        self.assertEqual(removed, 1)

    def test_live_pid_is_kept(self):
        self._write("v1", 4321)
        removed = statestore.prune(
            self.dir, set(), set(), live_pids={4321}, observed_pids={4321}
        )
        self.assertEqual(removed, 0)

    def test_unobserved_pid_is_left_alone(self):
        # Mirrors the unobserved-socket rule: a pid the poller did not check this
        # tick must not be judged dead against an empty live set.
        self._write("v1", 4321)
        removed = statestore.prune(
            self.dir, set(), set(), live_pids=set(), observed_pids=set()
        )
        self.assertEqual(removed, 0)

    def test_same_pid_with_a_new_start_time_reaps_the_old_session(self):
        self._write("v1", 4321, started=100)
        removed = statestore.prune(
            self.dir, set(), set(),
            live_pids={(4321, 200)}, observed_pids={(4321, 100), (4321, 200)},
        )
        self.assertEqual(removed, 1)


class GnomeVscodeTest(unittest.TestCase):
    def test_js_targets_the_folder_and_vscode_suffix(self):
        js = gnome.activate_vscode_js("datamil")
        self.assertIn("datamil", js)
        self.assertIn("Visual Studio Code", js)
        self.assertIn("Main.activateWindow", js)

    def test_focus_check_accepts_a_real_vscode_title(self):
        title = "app.py - datamil - Visual Studio Code"
        run = lambda argv: (0, '_NET_WM_NAME = "%s"' % title) if "_NET_WM_NAME" in argv \
            else (0, "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x123")
        self.assertTrue(gnome._vscode_focused("datamil", run))

    def test_focus_check_rejects_a_chrome_tab_naming_the_folder(self):
        title = "datamil - Google Chrome"
        run = lambda argv: (0, '_NET_WM_NAME = "%s"' % title) if "_NET_WM_NAME" in argv \
            else (0, "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x123")
        self.assertFalse(gnome._vscode_focused("datamil", run))


class AppVscodeJumpTest(unittest.TestCase):
    def _row(self):
        return model.Row(
            session_id="v1", socket="", pane="", tmux_session="datamil",
            title="fix bug", state=hookstate.WAITING, reason="idle", message="",
            cwd="/home/u/robotics/datamil", updated_at=7, kind="vscode",
            claude_pid=4321,
        )

    def test_jump_activates_the_session_and_skips_tmux(self):
        calls = {}

        def fake_select(socket, pane):
            calls["selected"] = True  # must NOT be called for a VSCode row

        def fake_activate_title(title):
            calls["title"] = title  # the tmux path, also must not run

        def fake_vscode(row):
            calls["session_id"] = row.session_id  # addressed by session id, tab-precise
            return gnome.ActivationResult(True, 1)

        status = app.perform_jump(
            self._row(), fake_select, fake_activate_title, fake_vscode
        )
        self.assertEqual(status, "")
        self.assertEqual(calls.get("session_id"), "v1")
        self.assertNotIn("selected", calls)
        self.assertNotIn("title", calls)

    def test_failed_vscode_activation_names_the_session(self):
        status = app.perform_jump(
            self._row(),
            select_pane=lambda s, p: None,
            activate=lambda t: gnome.ActivationResult(True, 1),
            activate_vscode=lambda row: gnome.ActivationResult(False, 0),
        )
        # The row has a headline ("fix bug"), so the failure names it, not just
        # the shared folder.
        self.assertIn("fix bug", status)
        self.assertIn("활성화하지 못했습니다", status)


class CodeUriTest(unittest.TestCase):
    def test_uri_targets_the_session_id(self):
        from ccnav import codeuri
        uri = codeuri.open_session_uri("b503638f-26e9-401c-a86c-383cbb727338")
        self.assertEqual(
            uri,
            "vscode://Anthropic.claude-code/open?session="
            "b503638f-26e9-401c-a86c-383cbb727338",
        )

    def test_open_session_reports_delivery(self):
        from ccnav import codeuri
        argv_seen = {}

        def run(argv):
            argv_seen["argv"] = list(argv)
            return 0, ""
        self.assertTrue(codeuri.open_session("v1", run=run))
        self.assertEqual(argv_seen["argv"][0], "code")
        self.assertIn("--open-url", argv_seen["argv"])

    def test_missing_code_binary_is_not_delivered(self):
        from ccnav import codeuri

        def run(argv):
            raise FileNotFoundError("no code on PATH")
        self.assertFalse(codeuri.open_session("v1", run=run))

    def test_empty_session_id_is_not_delivered(self):
        from ccnav import codeuri
        self.assertFalse(codeuri.open_session("", run=lambda a: (0, "")))


class ActivateVscodeSessionTest(unittest.TestCase):
    """gnome.activate_vscode_session: raise the window, THEN switch to the tab."""

    def _runner(self, title):
        # Serves the window-match gdbus Eval (reports 1 match) and xprop focus.
        def run(argv):
            if argv and argv[0] == "gdbus":
                return 0, "(true, '\"matched=1\"')"
            if "_NET_WM_NAME" in argv:
                return 0, '_NET_WM_NAME = "%s"' % title
            if "_NET_ACTIVE_WINDOW" in argv:
                return 0, "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x1"
            return 1, ""
        return run

    def test_raises_the_window_then_opens_the_session_tab_in_order(self):
        order = []
        run = self._runner("chat - datamil - Visual Studio Code")

        def open_session(session_id, run):
            order.append(("open", session_id))
            return True

        # wrap run to record when the window was raised (the gdbus Eval)
        base = run
        def traced(argv):
            if argv and argv[0] == "gdbus":
                order.append(("window", "datamil"))
            return base(argv)

        result = gnome.activate_vscode_session(
            "v1", "datamil", run=traced, sleep=lambda _s: None, timeout=0.05,
            open_session=open_session,
        )
        self.assertTrue(result.ok)  # window focus confirmed via xprop
        # the window is raised BEFORE the session tab is opened into it
        self.assertEqual(order, [("window", "datamil"), ("open", "v1")])

    def test_still_returns_the_window_result_when_code_is_missing(self):
        run = self._runner("chat - datamil - Visual Studio Code")
        result = gnome.activate_vscode_session(
            "v1", "datamil", run=run, sleep=lambda _s: None, timeout=0.05,
            open_session=lambda session_id, run: False,  # code absent
        )
        self.assertTrue(result.ok)  # landed on the right window regardless

    def test_failed_window_activation_does_not_send_the_uri_elsewhere(self):
        opened = []

        def run(argv):
            if argv and argv[0] == "gdbus":
                return 0, "(true, '\"matched=0\"')"
            if "_NET_WM_NAME" in argv:
                return 0, '_NET_WM_NAME = "other - Visual Studio Code"'
            return 0, "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x1"

        result = gnome.activate_vscode_session(
            "v1", "datamil", run=run, sleep=lambda _s: None, timeout=0,
            open_session=lambda session_id, run: opened.append(session_id) or True,
        )
        self.assertFalse(result.ok)
        self.assertEqual(opened, [])

    def test_ambiguous_window_match_does_not_send_the_uri(self):
        opened = []

        def run(argv):
            if argv and argv[0] == "gdbus":
                return 0, "(true, '\"matched=2\"')"
            if "_NET_WM_NAME" in argv:
                return 0, '_NET_WM_NAME = "chat - datamil - Visual Studio Code"'
            return 0, "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x1"

        result = gnome.activate_vscode_session(
            "v1", "datamil", run=run, sleep=lambda _s: None, timeout=0,
            open_session=lambda session_id, run: opened.append(session_id) or True,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.matched, 2)
        self.assertEqual(opened, [])


class CollectRowsVscodeTest(unittest.TestCase):
    def test_fresh_hook_record_gets_a_workbench_state_grace_window(self):
        rec = {
            "session_id": "v1", "cwd": "/p", "kind": "vscode",
            "updated_at": 100,
        }
        live, observed = app._vscode_ui_sessions(  # noqa: SLF001
            [rec], lambda session_id, cwd: False, now=102)
        self.assertEqual(live, set())
        self.assertEqual(observed, set())

    def test_builds_a_vscode_row_with_no_tmux_sockets_present(self):
        rec = {
            "session_id": "v1", "cwd": "/home/u/robotics/datamil", "kind": "vscode",
            "claude_pid": 4321, "tmux_socket": "", "tmux_pane": "",
            "state": hookstate.WAITING, "reason": "idle", "message": "", "updated_at": 7,
        }

        def explode_tmux(_socket):
            raise AssertionError("a VSCode-only tick must not query tmux")

        result = app.collect_rows(
            pathlib.Path("/nonexistent"),
            read_all=lambda d: [rec],
            sessions_for=explode_tmux,
            titles_for=explode_tmux,
            prune=lambda *a, **k: 0,
            # Codex support independently scans discoverable tmux sockets for
            # pre-hook TUIs. This test isolates the VSCode-only path by making
            # that discovery seam empty.
            socket_candidates=lambda: [],
            live_pids_for=lambda pids: {4321} if 4321 in pids else set(),
            vscode_session_visible=lambda session_id, cwd: None,
        )
        self.assertEqual(len(result.rows), 1)
        self.assertTrue(result.rows[0].is_vscode)
        self.assertEqual(result.rows[0].vscode_folder, "datamil")

    def test_closed_ui_hides_row_while_the_backend_pid_is_still_live(self):
        rec = {
            "session_id": "v1", "cwd": "/home/u/robotics/datamil",
            "kind": "vscode", "claude_pid": 4321,
            "claude_start_time": 100, "tmux_socket": "", "tmux_pane": "",
            "state": hookstate.WAITING, "reason": "idle", "message": "",
            "updated_at": 7,
        }
        pruned = {}

        def capture_prune(*_args, **kwargs):
            pruned.update(kwargs)
            return 0

        result = app.collect_rows(
            pathlib.Path("/nonexistent"), read_all=lambda d: [rec],
            sessions_for=lambda socket: (True, {}), titles_for=lambda socket: {},
            prune=capture_prune, socket_candidates=lambda: [],
            live_pids_for=lambda pids: {(4321, 100)},
            vscode_session_visible=lambda session_id, cwd: False,
        )
        self.assertEqual(result.rows, [])
        # UI hiding is reversible: the live backend record is retained so the
        # row can return if the same sidebar becomes visible without a new hook.
        self.assertEqual(pruned["live_pids"], {(4321, 100)})


if __name__ == "__main__":
    unittest.main()
