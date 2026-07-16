import os
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


class DiscoveryTest(ProcessTreeFixture):
    def test_finds_claude_below_the_tmux_shell_without_reading_argv(self):
        self.process(10, "bash", children=[20], pgrp=10, tpgid=20)
        self.process(20, "claude", ppid=10, pgrp=20, tpgid=20)
        os.symlink("/proj", str(self.root / "20" / "cwd"))

        found = claudesession.find_claude_process(10, self.root)

        self.assertIsNotNone(found)
        self.assertEqual(found.pid, 20)
        self.assertEqual(found.cwd, "/proj")

    def test_non_claude_tree_is_not_discovered(self):
        self.process(10, "bash", children=[20], pgrp=10, tpgid=20)
        self.process(20, "python", ppid=10, pgrp=20, tpgid=20)
        self.assertIsNone(claudesession.find_claude_process(10, self.root))

    def test_provisional_record_tracks_the_pane_and_live_title_state(self):
        process = claudesession.ClaudeProcess(pid=20, started_at=50, cwd="/proj")
        socket = "/tmp/tmux/default"
        idle = claudesession.provisional_record(socket, "%1", process)
        working = claudesession.provisional_record(socket, "%1", process, working=True)

        self.assertEqual(idle["provider"], "claude")
        self.assertEqual(idle["state"], "waiting")
        self.assertEqual(idle["reason"], "idle")
        self.assertEqual(working["state"], "working")
        self.assertEqual(working["reason"], "")
        self.assertTrue(working["provisional"])


class DetachedTaskOutputTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        self.proc = root / "proc"
        self.tasks = root / "claude-1000"
        self.proc.mkdir()
        self.tasks.mkdir()

    def process(self, pid, cwd, target=None, flags="0102001"):
        process = self.proc / str(pid)
        (process / "fd").mkdir(parents=True)
        (process / "fdinfo").mkdir()
        os.symlink(cwd, str(process / "cwd"))
        if target is not None:
            os.symlink(str(target), str(process / "fd" / "1"))
            (process / "fdinfo" / "1").write_text("flags:\t%s\n" % flags)

    def output(self, project="project", session="session", name="b123.output"):
        path = self.tasks / project / session / "tasks" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def scan(self, cwds):
        return claudesession.live_task_output_cwds(
            set(cwds), self.proc, self.tasks, uid=os.getuid())

    def test_a_writable_task_output_marks_its_exact_candidate_cwd(self):
        self.process(10, "/project", self.output())
        self.process(20, "/other", self.output("other", "session2"))
        self.assertEqual(self.scan({"/project"}), {"/project"})

    def test_readers_unbounded_paths_and_other_cwds_do_not_count(self):
        self.process(10, "/project", self.output(), flags="0100000")
        self.process(20, "/project", self.tasks / "too-shallow.output")
        self.process(30, "/other", self.output("other", "session2"))
        self.assertEqual(self.scan({"/project"}), set())

    def test_missing_proc_metadata_degrades_to_empty(self):
        self.assertEqual(
            claudesession.live_task_output_cwds(
                {"/project"}, self.proc / "missing", self.tasks,
                uid=os.getuid()),
            set(),
        )
