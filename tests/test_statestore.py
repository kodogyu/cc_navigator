import json
import pathlib
import tempfile
import unittest
from unittest import mock

from ccnav import statestore


def record(session_id="s1", socket="/tmp/tmux-1000/default", pane="%1", updated_at=100):
    return {
        "session_id": session_id,
        "cwd": "/proj",
        "tmux_socket": socket,
        "tmux_pane": pane,
        "state": "waiting",
        "reason": "idle",
        "message": "",
        "updated_at": updated_at,
    }


class StateStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_write_then_read_round_trips(self):
        statestore.write(self.dir, record())
        self.assertEqual(statestore.read_all(self.dir), [record()])

    def test_write_leaves_no_temp_files(self):
        statestore.write(self.dir, record())
        leftovers = [p.name for p in self.dir.iterdir() if p.name.startswith(".tmp-")]
        self.assertEqual(leftovers, [])

    def test_failed_write_leaves_no_partial_file_and_no_temp(self):
        with mock.patch("json.dump", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                statestore.write(self.dir, record())
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_rejects_unsafe_session_id(self):
        with self.assertRaises(ValueError):
            statestore.write(self.dir, record(session_id="../../etc/passwd"))

    def test_safe_session_id_predicate(self):
        self.assertTrue(statestore.is_safe_session_id("11111111-2222-3333"))
        self.assertFalse(statestore.is_safe_session_id("a/b"))
        self.assertFalse(statestore.is_safe_session_id(""))

    def test_read_all_skips_malformed_files(self):
        statestore.write(self.dir, record())
        (self.dir / "broken.json").write_text("{not json")
        self.assertEqual(statestore.read_all(self.dir), [record()])

    def test_read_all_on_missing_directory_is_empty(self):
        self.assertEqual(statestore.read_all(self.dir / "nope"), [])

    def test_prune_removes_records_whose_pane_is_gone(self):
        statestore.write(self.dir, record(session_id="alive", pane="%1"))
        statestore.write(self.dir, record(session_id="dead", pane="%9"))
        removed = statestore.prune(
            self.dir, {("/tmp/tmux-1000/default", "%1")}, now=100
        )
        self.assertEqual(removed, 1)
        names = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(names, ["alive.json"])

    def test_prune_removes_stale_records_even_if_pane_is_live(self):
        statestore.write(self.dir, record(session_id="old", updated_at=0))
        live = {("/tmp/tmux-1000/default", "%1")}
        removed = statestore.prune(
            self.dir, live, now=statestore.MAX_AGE_SECONDS + 1
        )
        self.assertEqual(removed, 1)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_prune_removes_malformed_files(self):
        (self.dir / "broken.json").write_text("{not json")
        self.assertEqual(statestore.prune(self.dir, set(), now=100), 1)
        self.assertEqual(list(self.dir.iterdir()), [])
