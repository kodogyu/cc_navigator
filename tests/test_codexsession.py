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


class ProcessTreeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.cwd = self.root / "project"
        self.cwd.mkdir()

    def process(self, pid, argv, children=(), started_at=1):
        directory = self.root / str(pid)
        task = directory / "task" / str(pid)
        task.mkdir(parents=True)
        (task / "children").write_text(" ".join(str(x) for x in children))
        (directory / "cmdline").write_bytes(
            b"\0".join(x.encode() for x in argv) + b"\0")
        os.symlink(str(self.cwd), str(directory / "cwd"))
        os.utime(str(directory), (started_at, started_at))

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

    def test_record_is_working_codex_and_carries_process_start(self):
        record = codexsession.provisional_record(
            "/tmp/s", "%1", codexsession.CodexProcess(7, 123, "/proj"))
        self.assertEqual(record["provider"], "codex")
        self.assertEqual(record["state"], hookstate.WORKING)
        self.assertEqual(record["updated_at"], 123)
        self.assertTrue(record["provisional"])
