import pathlib
import shutil
import subprocess
import tempfile
import unittest

from ccnav import updater


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, text=True)


class FakeGit:
    """A scripted git runner keyed by subcommand (args[0]); records calls."""

    def __init__(self, **responses):
        self.responses = responses
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        return self.responses.get(args[0], (0, "", ""))

    def ran(self, subcommand):
        return any(c[0] == subcommand for c in self.calls)


class UpdateTest(unittest.TestCase):
    def test_up_to_date_does_not_merge(self):
        git = FakeGit(status=(0, "", ""), fetch=(0, "", ""),
                      **{"rev-list": (0, "0\n", "")})
        updated, msg = updater.update(git=git)
        self.assertFalse(updated)
        self.assertIn("최신", msg)
        self.assertFalse(git.ran("merge"))

    def test_available_fast_forwards_with_ff_only(self):
        git = FakeGit(status=(0, "", ""), fetch=(0, "", ""),
                      **{"rev-list": (0, "3\n", "")}, merge=(0, "", ""))
        updated, msg = updater.update(git=git)
        self.assertTrue(updated)
        self.assertTrue(git.ran("merge"))
        merge_call = [c for c in git.calls if c[0] == "merge"][0]
        self.assertIn("--ff-only", merge_call)  # the only tree-touching command is guarded

    def test_tracked_changes_abort_before_any_network_or_merge(self):
        git = FakeGit(status=(0, " M cc_navigator.txt\n", ""))
        updated, msg = updater.update(git=git)
        self.assertFalse(updated)
        self.assertIn("로컬 변경", msg)
        self.assertFalse(git.ran("fetch"))  # never reached the network
        self.assertFalse(git.ran("merge"))  # never touched the tree

    def test_untracked_files_alone_do_not_block(self):
        git = FakeGit(status=(0, "?? figures/x.png\n?? icons/\n", ""),
                      fetch=(0, "", ""), **{"rev-list": (0, "2\n", "")}, merge=(0, "", ""))
        updated, _ = updater.update(git=git)
        self.assertTrue(updated)  # a ff does not touch untracked files

    def test_offline_fetch_aborts_without_merge(self):
        git = FakeGit(status=(0, "", ""), fetch=(1, "", "could not resolve host"))
        updated, msg = updater.update(git=git)
        self.assertFalse(updated)
        self.assertIn("원격", msg)
        self.assertFalse(git.ran("merge"))

    def test_no_upstream_aborts(self):
        git = FakeGit(status=(0, "", ""), fetch=(0, "", ""),
                      **{"rev-list": (128, "", "no upstream configured")})
        updated, msg = updater.update(git=git)
        self.assertFalse(updated)
        self.assertIn("업스트림", msg)

    def test_non_fast_forward_aborts_without_data_loss(self):
        git = FakeGit(status=(0, "", ""), fetch=(0, "", ""),
                      **{"rev-list": (0, "3\n", "")},
                      merge=(1, "", "fatal: Not possible to fast-forward"))
        updated, msg = updater.update(git=git)
        self.assertFalse(updated)
        self.assertIn("fast-forward", msg)

    def test_not_a_git_checkout_aborts(self):
        git = FakeGit(status=(128, "", "not a git repository"))
        updated, msg = updater.update(git=git)
        self.assertFalse(updated)
        self.assertFalse(git.ran("fetch"))


@unittest.skipUnless(shutil.which("git"), "git not available")
class RealRepoUpdateTest(unittest.TestCase):
    """Exercise the safety guarantee against REAL git, not a scripted runner:
    the fast-forward must never lose local work."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = pathlib.Path(self._tmp.name)
        self.remote = self.base / "remote.git"
        self.work = self.base / "work"
        subprocess.run(["git", "init", "--bare", str(self.remote)], capture_output=True)
        subprocess.run(["git", "clone", str(self.remote), str(self.work)], capture_output=True)
        self._config(self.work)
        (self.work / "a.txt").write_text("v1\n")
        _git(self.work, "add", "a.txt")
        _git(self.work, "commit", "-m", "c1")
        _git(self.work, "push", "-u", "origin", "HEAD")

    def _config(self, repo):
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        _git(repo, "config", "commit.gpgsign", "false")

    def _advance_upstream(self, filename, content):
        # A second clone commits `filename` and pushes it, leaving `work` behind.
        other = self.base / ("other-" + filename)
        subprocess.run(["git", "clone", str(self.remote), str(other)], capture_output=True)
        self._config(other)
        (other / filename).write_text(content)
        _git(other, "add", filename)
        _git(other, "commit", "-m", "up " + filename)
        _git(other, "push", "origin", "HEAD")

    def _runner(self):
        return updater._git_runner(self.work)

    def test_clean_and_behind_fast_forwards(self):
        self._advance_upstream("b.txt", "v2\n")
        updated, msg = updater.update(git=self._runner())
        self.assertTrue(updated, msg)
        self.assertTrue((self.work / "b.txt").exists())  # the upstream commit landed

    def test_untracked_collision_aborts_and_preserves_the_file(self):
        self._advance_upstream("new.txt", "from upstream\n")
        (self.work / "new.txt").write_text("PRECIOUS LOCAL\n")  # untracked collision
        updated, msg = updater.update(git=self._runner())
        self.assertFalse(updated)
        # git --ff-only refuses to overwrite an untracked file; it must survive.
        self.assertEqual((self.work / "new.txt").read_text(), "PRECIOUS LOCAL\n")

    def test_tracked_local_change_aborts_untouched(self):
        self._advance_upstream("b.txt", "v2\n")
        (self.work / "a.txt").write_text("LOCAL EDIT\n")  # tracked modification
        updated, msg = updater.update(git=self._runner())
        self.assertFalse(updated)
        self.assertIn("로컬 변경", msg)
        self.assertEqual((self.work / "a.txt").read_text(), "LOCAL EDIT\n")  # untouched
        self.assertFalse((self.work / "b.txt").exists())  # nothing pulled

    def test_up_to_date_leaves_the_tree_unchanged(self):
        before = (self.work / "a.txt").read_text()
        updated, msg = updater.update(git=self._runner())
        self.assertFalse(updated)
        self.assertIn("최신", msg)
        self.assertEqual((self.work / "a.txt").read_text(), before)


if __name__ == "__main__":
    unittest.main()
