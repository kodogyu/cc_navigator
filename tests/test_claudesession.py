import pathlib
import tempfile
import unittest

from ccnav import claudesession


class ProcessTreeFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)

    def process(self, pid, comm, children=(), ppid=1, pgrp=None, session=10,
                tpgid=20, state="S"):
        directory = self.root / str(pid)
        task = directory / "task" / str(pid)
        task.mkdir(parents=True)
        (task / "children").write_text(" ".join(str(x) for x in children))
        pgrp = pid if pgrp is None else pgrp
        fields = [state, str(ppid), str(pgrp), str(session), "34816", str(tpgid)]
        (directory / "stat").write_text(
            "%d (%s) %s\n" % (pid, comm, " ".join(fields)))

    def thread_children(self, pid, thread_id, children):
        task = self.root / str(pid) / "task" / str(thread_id)
        task.mkdir(parents=True)
        (task / "children").write_text(" ".join(str(x) for x in children))


class BackgroundShellTest(ProcessTreeFixture):
    def test_a_separate_nonforeground_process_group_is_background_work(self):
        self.process(10, "bash", children=[20], pgrp=10, tpgid=20)
        self.process(20, "claude", children=[30, 40], ppid=10, pgrp=20, tpgid=20)
        self.process(30, "mcp-server", ppid=20, pgrp=20, tpgid=20)
        self.process(40, "bash", children=[41], ppid=20, pgrp=40, tpgid=20)
        self.process(41, "python", ppid=40, pgrp=40, tpgid=20)

        self.assertTrue(claudesession.background_shell_active(10, self.root))

    def test_foreground_mcp_helpers_do_not_count(self):
        self.process(10, "bash", children=[20], pgrp=10, tpgid=20)
        self.process(20, "claude", children=[30], ppid=10, pgrp=20, tpgid=20)
        self.process(30, "mcp-server", children=[31], ppid=20, pgrp=20, tpgid=20)
        self.process(31, "node", ppid=30, pgrp=20, tpgid=20)

        self.assertFalse(claudesession.background_shell_active(10, self.root))

    def test_a_foreground_tool_process_does_not_count(self):
        self.process(10, "bash", children=[20], pgrp=10, tpgid=40)
        self.process(20, "claude", children=[40], ppid=10, pgrp=20, tpgid=40)
        self.process(40, "bash", ppid=20, pgrp=40, tpgid=40)

        self.assertFalse(claudesession.background_shell_active(10, self.root))

    def test_a_zombie_background_group_does_not_count(self):
        self.process(10, "bash", children=[20], pgrp=10, tpgid=20)
        self.process(20, "claude", children=[40], ppid=10, pgrp=20, tpgid=20)
        self.process(40, "bash", ppid=20, pgrp=40, tpgid=20, state="Z")

        self.assertFalse(claudesession.background_shell_active(10, self.root))

    def test_worker_thread_children_are_seen(self):
        self.process(10, "bash", children=[20], pgrp=10, tpgid=20)
        self.process(20, "claude", ppid=10, pgrp=20, tpgid=20)
        self.thread_children(20, 99, [40])
        self.process(40, "bash", ppid=20, pgrp=40, tpgid=20)

        self.assertTrue(claudesession.background_shell_active(10, self.root))

    def test_missing_or_non_claude_tree_is_unknown_not_false(self):
        self.assertIsNone(claudesession.background_shell_active(999, self.root))
        self.process(10, "bash", pgrp=10, tpgid=10)
        self.assertIsNone(claudesession.background_shell_active(10, self.root))

    def test_an_incomplete_claude_tree_is_unknown_not_false(self):
        self.process(10, "bash", children=[20], pgrp=10, tpgid=20)
        self.process(20, "claude", children=[30], ppid=10, pgrp=20, tpgid=20)
        # PID 30 appeared in the children list but vanished / was unreadable.
        self.assertIsNone(claudesession.background_shell_active(10, self.root))
