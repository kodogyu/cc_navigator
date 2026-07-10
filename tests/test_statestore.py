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
            self.dir,
            {("/tmp/tmux-1000/default", "%1")},
            {"/tmp/tmux-1000/default"},
            now=100,
        )
        self.assertEqual(removed, 1)
        names = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(names, ["alive.json"])

    def test_prune_removes_stale_records_even_if_pane_is_live(self):
        statestore.write(self.dir, record(session_id="old", updated_at=0))
        live = {("/tmp/tmux-1000/default", "%1")}
        removed = statestore.prune(
            self.dir,
            live,
            {"/tmp/tmux-1000/default"},
            now=statestore.MAX_AGE_SECONDS + 1,
        )
        self.assertEqual(removed, 1)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_prune_removes_malformed_files(self):
        (self.dir / "broken.json").write_text("{not json")
        self.assertEqual(statestore.prune(self.dir, set(), set(), now=100), 1)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_prune_spares_a_record_whose_socket_was_not_observed(self):
        # F3: a failed or slow tmux query returns an empty pane set. Pruning on
        # that would delete a LIVE session's state file, and a waiting session
        # fires no more hooks, so the row would never come back. A socket that
        # did not answer this tick must not have its records judged for liveness.
        statestore.write(self.dir, record(session_id="alive", pane="%1"))
        removed = statestore.prune(
            self.dir,
            set(),  # empty live set, because the query failed
            set(),  # and the socket was NOT observed
            now=100,
        )
        self.assertEqual(removed, 0)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["alive.json"])

    def test_prune_still_ages_out_an_unobserved_socket(self):
        # An unreachable socket's files must not leak forever: age still reaps.
        statestore.write(self.dir, record(session_id="old", updated_at=0))
        removed = statestore.prune(
            self.dir, set(), set(), now=statestore.MAX_AGE_SECONDS + 1
        )
        self.assertEqual(removed, 1)

    def test_prune_removes_gone_pane_only_when_socket_was_observed(self):
        # Same live set (empty) and same record, but now the socket WAS observed
        # and its pane is genuinely absent -> the record is correctly pruned.
        statestore.write(self.dir, record(session_id="dead", pane="%9"))
        removed = statestore.prune(
            self.dir, set(), {"/tmp/tmux-1000/default"}, now=100
        )
        self.assertEqual(removed, 1)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_prune_tolerates_a_file_it_cannot_delete(self):
        # A state file owned by another uid in a shared /tmp fallback raises
        # PermissionError on unlink. That one file must stay put, must not be
        # counted as removed, and -- crucially -- must not stop prune from
        # removing the others or let an OSError escape to kill the poll thread.
        statestore.write(self.dir, record(session_id="a-gone", pane="%8"))
        statestore.write(self.dir, record(session_id="b-locked", pane="%9"))
        live = {("/tmp/tmux-1000/default", "%1")}  # neither pane is live
        observed = {"/tmp/tmux-1000/default"}

        real_unlink = pathlib.Path.unlink

        def guarded_unlink(self, *args, **kwargs):
            if self.name == "b-locked.json":
                raise PermissionError(13, "Permission denied")
            return real_unlink(self, *args, **kwargs)

        with mock.patch.object(pathlib.Path, "unlink", guarded_unlink):
            removed = statestore.prune(self.dir, live, observed, now=100)  # no raise

        self.assertEqual(removed, 1)  # only the deletable one counts
        names = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(names, ["b-locked.json"])  # the locked file is left in place

    def test_write_replaces_the_file_rather_than_mutating_it(self):
        # An atomic write renames a fresh temp file over the target, so the
        # target gets a new inode every time. An in-place writer (open+truncate,
        # copyfile) keeps the same inode. The divergence only shows on the
        # second write, once a target already exists to be replaced or mutated.
        statestore.write(self.dir, record(updated_at=100))
        target = self.dir / "s1.json"
        ino_first = target.stat().st_ino
        statestore.write(self.dir, record(updated_at=200))
        ino_second = target.stat().st_ino
        self.assertNotEqual(ino_first, ino_second)

    def test_reader_during_write_never_sees_a_partial_file(self):
        # Observe the target at the exact instant json.dump runs -- the moment a
        # naive writer that opened the target directly would have truncated it.
        # A correct writer stages in a temp file, so at that instant the target
        # is either absent (first write) or still the previous, complete record.
        target = self.dir / "s1.json"
        real_dump = json.dump
        observed = []

        def dump_spy(obj, fp, *args, **kwargs):
            if target.exists():
                observed.append(json.loads(target.read_text()))
            else:
                observed.append(None)
            return real_dump(obj, fp, *args, **kwargs)

        with mock.patch("json.dump", side_effect=dump_spy):
            statestore.write(self.dir, record(updated_at=100))
            statestore.write(self.dir, record(updated_at=200))

        # Non-vacuous: the spy ran once per write, and each time the target was
        # untouched -- absent, then the complete first record, never a fragment.
        self.assertEqual(observed, [None, record(updated_at=100)])
