"""Tests for procstat: /proc parsing and the VSCode-session liveness it feeds."""
import errno
import struct
import unittest

from ccnav import procstat


def stat_blob(comm, ppid, state="S", start_time=12345):
    """A minimal but faithful /proc/<pid>/stat line: 'pid (comm) state ppid ...'."""
    # After comm: field 3 is state, field 4 is ppid, field 22 is starttime.
    fields = [state, str(ppid)] + ["0"] * 17 + [str(start_time)]
    return ("999 (%s) %s" % (comm, " ".join(fields))).encode()


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

    def test_reads_the_kernel_start_time(self):
        self.assertEqual(procstat.parse_start_time(stat_blob("claude", 1, start_time=77)), 77)


class ProcessTtyTest(unittest.TestCase):
    def test_accepts_only_a_bounded_pts_path(self):
        seen = []
        tty = procstat.process_tty(
            42, readlink=lambda path: seen.append(path) or "/dev/pts/3")
        self.assertEqual(tty, "/dev/pts/3")
        self.assertEqual(seen, ["/proc/42/fd/0"])

    def test_rejects_non_tty_targets_and_read_failures(self):
        self.assertEqual(
            procstat.process_tty(42, readlink=lambda path: "socket:[123]"), "")
        self.assertEqual(
            procstat.process_tty(42, readlink=lambda path: "/private/path"), "")

        def missing(_path):
            raise OSError("gone")
        self.assertEqual(procstat.process_tty(42, readlink=missing), "")


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

    def test_reused_pid_now_another_claude_is_false_when_start_time_changed(self):
        read = lambda pid: stat_blob("claude", 1, start_time=200)
        self.assertFalse(procstat.pid_is_claude(
            123, expected_start_time=100, read_stat=read))

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
            procstat.live_claude_pids(
                {10, 20, 30}, read_stat=read,
                transport_connected=lambda pid: True),
            {10, 30}
        )

    def test_identity_keys_survive_only_with_the_same_start_time(self):
        read = lambda pid: stat_blob("claude", 1, start_time=200)
        keys = {(10, 100), (10, 200)}
        self.assertEqual(
            procstat.live_claude_pids(
                keys, read_stat=read,
                transport_connected=lambda pid: True),
            {(10, 200)})

    def test_a_disconnected_vscode_transport_reaps_a_live_backend(self):
        read = lambda pid: stat_blob("claude", 1, start_time=200)
        self.assertEqual(
            procstat.live_claude_pids(
                {(10, 200)}, read_stat=read,
                transport_connected=lambda pid: False),
            set())

    def test_an_unobservable_transport_falls_back_to_process_liveness(self):
        read = lambda pid: stat_blob("claude", 1, start_time=200)
        self.assertEqual(
            procstat.live_claude_pids(
                {(10, 200)}, read_stat=read,
                transport_connected=lambda pid: None),
            {(10, 200)})

    def test_transport_probe_failure_falls_back_without_escaping(self):
        read = lambda pid: stat_blob("claude", 1)
        def explode(_pid):
            raise RuntimeError("probe failed")
        self.assertEqual(
            procstat.live_claude_pids(
                {10}, read_stat=read, transport_connected=explode),
            {10})


class VscodeTransportTest(unittest.TestCase):
    def test_connected_socket_peer_is_true(self):
        self.assertTrue(procstat.vscode_transport_connected(
            42,
            readlink=lambda path: "socket:[123]",
            peer_inode=lambda inode: 456))

    def test_closed_socket_peer_is_false(self):
        self.assertFalse(procstat.vscode_transport_connected(
            42,
            readlink=lambda path: "socket:[123]",
            peer_inode=lambda inode: 0))

    def test_unobservable_or_non_socket_stdin_is_unknown(self):
        self.assertIsNone(procstat.vscode_transport_connected(
            42, readlink=lambda path: "pipe:[123]"))
        self.assertIsNone(procstat.vscode_transport_connected(
            42,
            readlink=lambda path: "socket:[123]",
            peer_inode=lambda inode: None))

    def test_readlink_failure_is_unknown(self):
        def missing(_path):
            raise OSError("gone")
        self.assertIsNone(procstat.vscode_transport_connected(42, readlink=missing))

    def test_a_vanished_process_or_stdin_is_disconnected(self):
        def vanished(_path):
            raise OSError(errno.ENOENT, "gone")
        self.assertFalse(procstat.vscode_transport_connected(42, readlink=vanished))


class UnixPeerQueryTest(unittest.TestCase):
    class FakeSocket:
        def __init__(self, response):
            self.response = response
            self.sent = b""
            self.closed = False

        def settimeout(self, _seconds):
            pass

        def send(self, data):
            self.sent = data

        def recv(self, _size):
            return self.response

        def close(self):
            self.closed = True

    @staticmethod
    def _response(inode, peer=None):
        body = struct.pack("=BBBBIII", 1, 1, 1, 0, inode, 0, 0)
        if peer is not None:
            body += struct.pack("=HHI", 8, 2, peer)
        return struct.pack("=IHHII", 16 + len(body), 20, 0, 1, 0) + body

    def test_reads_the_connected_peer_inode_and_closes_the_query_socket(self):
        fake = self.FakeSocket(self._response(123, peer=456))
        result = procstat._unix_peer_inode(  # noqa: SLF001 -- kernel parser seam
            123, socket_factory=lambda *args: fake)
        self.assertEqual(result, 456)
        self.assertTrue(fake.sent)
        self.assertTrue(fake.closed)

    def test_a_socket_without_a_peer_attribute_is_disconnected(self):
        fake = self.FakeSocket(self._response(123))
        self.assertEqual(procstat._unix_peer_inode(  # noqa: SLF001
            123, socket_factory=lambda *args: fake), 0)

    def test_an_unavailable_diag_socket_is_unknown(self):
        def denied(*_args):
            raise OSError(errno.EPERM, "denied")
        self.assertIsNone(procstat._unix_peer_inode(  # noqa: SLF001
            123, socket_factory=denied))


if __name__ == "__main__":
    unittest.main()
