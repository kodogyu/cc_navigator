import unittest

from ccnav import hookstate, model

SOCK = "/tmp/tmux-1000/default"


def record(session_id, pane, state=hookstate.WAITING, updated_at=100, socket=SOCK):
    return {
        "session_id": session_id,
        "cwd": "/data/projects/demo_project",
        "tmux_socket": socket,
        "tmux_pane": pane,
        "state": state,
        "reason": "idle",
        "message": "",
        "updated_at": updated_at,
    }


class BuildRowsTest(unittest.TestCase):
    def test_row_carries_the_tmux_session_and_title(self):
        rows = model.build_rows(
            [record("a", "%1")],
            {SOCK: {"%1": "demo"}},
            {SOCK: {"%1": "✳ 작업 중"}},
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].tmux_session, "demo")
        self.assertEqual(rows[0].title, "✳ 작업 중")
        self.assertEqual(rows[0].window_title, "ccnav:demo")
        self.assertTrue(rows[0].waiting)

    def test_record_whose_pane_is_gone_produces_no_row(self):
        rows = model.build_rows([record("a", "%9")], {SOCK: {"%1": "demo"}}, {SOCK: {}})
        self.assertEqual(rows, [])

    def test_missing_title_falls_back_to_the_pane_id(self):
        rows = model.build_rows([record("a", "%1")], {SOCK: {"%1": "demo"}}, {SOCK: {}})
        self.assertEqual(rows[0].title, "%1")

    def test_two_records_on_one_pane_keep_the_newest(self):
        rows = model.build_rows(
            [record("old", "%1", updated_at=1), record("new", "%1", updated_at=2)],
            {SOCK: {"%1": "demo"}},
            {SOCK: {"%1": "t"}},
        )
        self.assertEqual([r.session_id for r in rows], ["new"])

    def test_same_pane_id_on_different_sockets_are_distinct_rows(self):
        other = "/tmp/tmux-1000/other"
        rows = model.build_rows(
            [record("a", "%1"), record("b", "%1", socket=other)],
            {SOCK: {"%1": "demo"}, other: {"%1": "sandbox"}},
            {SOCK: {"%1": "t1"}, other: {"%1": "t2"}},
        )
        self.assertEqual(sorted(r.session_id for r in rows), ["a", "b"])

    def test_waiting_rows_sort_first_then_newest_first(self):
        rows = model.build_rows(
            [
                record("w-old", "%1", updated_at=1),
                record("working", "%2", state=hookstate.WORKING, updated_at=50),
                record("w-new", "%3", updated_at=9),
            ],
            {SOCK: {"%1": "a", "%2": "b", "%3": "c"}},
            {SOCK: {}},
        )
        self.assertEqual([r.session_id for r in rows], ["w-new", "w-old", "working"])

    def test_records_without_socket_or_pane_are_dropped(self):
        bad = {"session_id": "x", "tmux_socket": "", "tmux_pane": "", "updated_at": 1}
        self.assertEqual(model.build_rows([bad], {}, {}), [])


class LivePaneKeysTest(unittest.TestCase):
    def test_flattens_sockets_and_panes(self):
        keys = model.live_pane_keys({SOCK: {"%1": "a", "%2": "b"}})
        self.assertEqual(keys, {(SOCK, "%1"), (SOCK, "%2")})

    def test_empty(self):
        self.assertEqual(model.live_pane_keys({}), set())
