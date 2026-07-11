import unittest

from ccnav import updater


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


if __name__ == "__main__":
    unittest.main()
