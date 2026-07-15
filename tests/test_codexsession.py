import os
import pathlib
import tempfile
import unittest

from ccnav import codexsession, hookstate


class ArgvTest(unittest.TestCase):
    def test_recognises_the_native_binary(self):
        self.assertTrue(codexsession.is_codex_argv(
            ["/opt/codex/vendor/codex", "resume"]))

    def test_recognises_the_official_npm_launcher(self):
        self.assertTrue(codexsession.is_codex_argv([
            "/usr/bin/node", "/usr/lib/node_modules/@openai/codex/bin/codex.js"]))

    def test_does_not_call_an_arbitrary_node_process_codex(self):
        self.assertFalse(codexsession.is_codex_argv(
            ["/usr/bin/node", "/proj/server.js"]))
        self.assertFalse(codexsession.is_codex_argv(["codex-code-mode"]))


class ProcessTreeFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.cwd = self.root / "project"
        self.cwd.mkdir()

    def process(self, pid, argv, children=(), started_at=1, ppid=1, session=None):
        directory = self.root / str(pid)
        task = directory / "task" / str(pid)
        task.mkdir(parents=True)
        (task / "children").write_text(" ".join(str(x) for x in children))
        (directory / "cmdline").write_bytes(
            b"\0".join(x.encode() for x in argv) + b"\0")
        os.symlink(str(self.cwd), str(directory / "cwd"))
        session = pid if session is None else session
        fields = ["S", str(ppid), str(pid), str(session)] + ["0"] * 15 + [str(started_at)]
        (directory / "stat").write_text(
            "%d (%s) %s\n" % (pid, pathlib.Path(argv[0]).name, " ".join(fields)))
        os.utime(str(directory), (started_at, started_at))

    def thread_children(self, pid, thread_id, children):
        task = self.root / str(pid) / "task" / str(thread_id)
        task.mkdir(parents=True)
        (task / "children").write_text(" ".join(str(x) for x in children))


class ProcessTreeTest(ProcessTreeFixture):

    def test_finds_codex_below_the_tmux_shell(self):
        self.process(10, ["bash"], children=[11], started_at=1)
        self.process(11, ["/usr/bin/node",
                          "/usr/lib/node_modules/@openai/codex/bin/codex.js"],
                     children=[12], started_at=20)
        self.process(12, ["/vendor/codex", "resume"], started_at=21)

        found = codexsession.find_codex_process(10, self.root)

        self.assertEqual(found.pid, 12)
        self.assertEqual(found.started_at, 21)
        self.assertEqual(found.cwd, str(self.cwd))

    def test_missing_and_non_codex_trees_degrade_to_none(self):
        self.process(10, ["bash"], children=[11])
        self.process(11, ["node", "/proj/server.js"])
        self.assertIsNone(codexsession.find_codex_process(10, self.root))
        self.assertIsNone(codexsession.find_codex_process(999, self.root))

    def test_a_children_cycle_is_bounded(self):
        self.process(10, ["bash"], children=[10])
        self.assertIsNone(codexsession.find_codex_process(10, self.root))


class ProvisionalRecordTest(unittest.TestCase):
    def test_id_is_stable_per_socket_and_pane(self):
        first = codexsession.provisional_session_id("/tmp/s", "%1")
        self.assertEqual(first, codexsession.provisional_session_id("/tmp/s", "%1"))
        self.assertNotEqual(first, codexsession.provisional_session_id("/tmp/s", "%2"))

    def test_record_is_input_ready_codex_and_carries_process_start(self):
        record = codexsession.provisional_record(
            "/tmp/s", "%1", codexsession.CodexProcess(7, 123, "/proj"))
        self.assertEqual(record["provider"], "codex")
        self.assertEqual(record["state"], hookstate.WAITING)
        self.assertEqual(record["reason"], hookstate.STOP_IDLE)
        self.assertEqual(record["updated_at"], 123)
        self.assertTrue(record["provisional"])


class BackgroundProcessTest(ProcessTreeFixture):
    def test_finds_only_other_session_leader_children_of_the_owning_codex(self):
        self.process(10, ["bash"], children=[11], ppid=1, session=10)
        self.process(11, ["/vendor/codex", "resume"], children=[12, 30],
                     ppid=10, session=10)
        # Codex launches terminals from worker threads, not necessarily main.
        self.thread_children(11, 99, [20])
        self.process(12, ["sh", "-c", "hook"], children=[13], ppid=11, session=12)
        self.process(13, ["cc-navigator-hook"], ppid=12, session=12)
        self.process(20, ["sleep", "30"], ppid=11, session=20, started_at=200)
        # A persistent helper inherits Codex's session and is not a terminal.
        self.process(30, ["mcp-helper"], ppid=11, session=10, started_at=300)

        self.assertEqual(
            codexsession.background_process_ids(13, self.root), ["20:200"])

    def test_live_identity_rejects_pid_reuse_and_garbage(self):
        self.process(20, ["sleep", "30"], started_at=200)
        live = codexsession.live_process_ids(
            ["20:200", "20:199", "bad", 20], self.root)
        self.assertEqual(live, {"20:200"})
