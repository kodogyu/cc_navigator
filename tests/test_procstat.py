"""Tests for procstat: /proc parsing and the VSCode-session liveness it feeds."""
import unittest

from ccnav import procstat


def stat_blob(comm, ppid, state="S"):
    """A minimal but faithful /proc/<pid>/stat line: 'pid (comm) state ppid ...'."""
    return ("999 (%s) %s %d 999 999 0" % (comm, state, ppid)).encode()


class ParseStatTest(unittest.TestCase):
    def test_reads_comm_and_ppid(self):
        self.assertEqual(procstat.parse_stat(stat_blob("claude", 42)), ("claude", 42))

    def test_comm_may_contain_spaces_and_parens(self):
        # comm is delimited by the FIRST '(' and the LAST ')', so a name that
        # itself holds parentheses/spaces must not shift the ppid field.
        blob = b"7 (weird ) name) S 1234 7 7 0"
        self.assertEqual(procstat.parse_stat(blob), ("weird ) name", 1234))

    def test_garbage_is_none(self):
        self.assertIsNone(procstat.parse_stat(b"no parens here"))
        self.assertIsNone(procstat.parse_stat(b""))

    def test_non_numeric_ppid_is_none(self):
        self.assertIsNone(procstat.parse_stat(b"1 (x) S notapid 1 0"))


class FindClaudeAncestorTest(unittest.TestCase):
    def _reader(self, table):
        def read(pid):
            if pid not in table:
                raise OSError("no such pid")
            return table[pid]
        return read

    def test_walks_up_to_the_nearest_claude(self):
        # python(100) -> sh(90) -> claude(80) -> node(70)
        table = {
            100: stat_blob("python3", 90),
            90: stat_blob("sh", 80),
            80: stat_blob("claude", 70),
            70: stat_blob("node", 1),
        }
        self.assertEqual(
            procstat.find_claude_ancestor(100, read_stat=self._reader(table)), 80
        )

    def test_returns_zero_when_no_claude_ancestor(self):
        table = {100: stat_blob("python3", 90), 90: stat_blob("bash", 1)}
        self.assertEqual(
            procstat.find_claude_ancestor(100, read_stat=self._reader(table)), 0
        )

    def test_returns_zero_when_chain_breaks(self):
        table = {100: stat_blob("python3", 55)}  # 55 not readable
        self.assertEqual(
            procstat.find_claude_ancestor(100, read_stat=self._reader(table)), 0
        )

    def test_a_cycle_cannot_loop_forever(self):
        table = {100: stat_blob("a", 200), 200: stat_blob("b", 100)}
        self.assertEqual(
            procstat.find_claude_ancestor(100, read_stat=self._reader(table)), 0
        )


class PidIsClaudeTest(unittest.TestCase):
    def test_live_claude_is_true(self):
        read = lambda pid: stat_blob("claude", 1)
        self.assertTrue(procstat.pid_is_claude(123, read_stat=read))

    def test_reused_pid_now_a_different_process_is_false(self):
        # Guards PID reuse: the number is alive, but it names something else now.
        read = lambda pid: stat_blob("bash", 1)
        self.assertFalse(procstat.pid_is_claude(123, read_stat=read))

    def test_dead_pid_is_false(self):
        def read(pid):
            raise OSError("gone")
        self.assertFalse(procstat.pid_is_claude(123, read_stat=read))

    def test_nonpositive_and_garbage_are_false(self):
        never = lambda pid: stat_blob("claude", 1)
        self.assertFalse(procstat.pid_is_claude(0, read_stat=never))
        self.assertFalse(procstat.pid_is_claude(1, read_stat=never))
        self.assertFalse(procstat.pid_is_claude("x", read_stat=never))

    def test_live_claude_pids_filters_to_the_running_ones(self):
        alive = {10, 30}
        read = lambda pid: stat_blob("claude" if pid in alive else "sh", 1)
        self.assertEqual(
            procstat.live_claude_pids({10, 20, 30}, read_stat=read), {10, 30}
        )


if __name__ == "__main__":
    unittest.main()
