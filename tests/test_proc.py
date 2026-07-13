"""proc.py had no test file before because it had no logic; it has logic now:
a timeout, and the guarantee that a timed-out child does not survive it."""
import subprocess
import time
import unittest

from ccnav import proc

# A duration unlikely to collide with any unrelated "sleep" process already
# running on the box, so pgrep -f can single out the one this test starts.
MARKER = "5.913371"


class RunCommandTest(unittest.TestCase):
    def test_returns_stdout_and_zero_exit_for_success(self):
        code, out = proc.run_command(["/usr/bin/python3", "-c", "print('hi')"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "hi\n")

    def test_nonzero_exit_is_reported(self):
        code, _out = proc.run_command(
            ["/usr/bin/python3", "-c", "import sys; sys.exit(3)"]
        )
        self.assertEqual(code, 3)

    def test_default_timeout_is_five_seconds(self):
        self.assertEqual(proc.DEFAULT_TIMEOUT, 5.0)


class RunCommandTimeoutTest(unittest.TestCase):
    def tearDown(self):
        # Defensive: if the assertion above ever fails, do not leak a sleep.
        subprocess.run(
            ["pkill", "-f", "sleep %s" % MARKER],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def test_returns_promptly_with_124_and_kills_the_child(self):
        start = time.monotonic()
        code, out = proc.run_command(["sleep", MARKER], timeout=0.2)
        elapsed = time.monotonic() - start

        # "Promptly" means close to the 0.2s timeout, nowhere near the 5.9s
        # the child would sleep if subprocess.run(timeout=...) let it run on.
        self.assertLess(elapsed, 2.0)
        self.assertEqual((code, out), (124, ""))

        # subprocess.run(timeout=...) is documented to kill the child before
        # raising TimeoutExpired -- confirmed here, not assumed. A brief grace
        # period covers the OS reaping the killed process.
        deadline = time.monotonic() + 2.0
        survivors = "x"
        while time.monotonic() < deadline:
            survivors = subprocess.run(
                ["pgrep", "-f", "sleep %s" % MARKER],
                stdout=subprocess.PIPE,
                universal_newlines=True,
            ).stdout.strip()
            if not survivors:
                break
            time.sleep(0.05)
        self.assertEqual(survivors, "", "a sleep process survived the timeout")

    def test_a_nontimeout_call_still_completes_normally_with_a_generous_timeout(self):
        code, out = proc.run_command(["/usr/bin/python3", "-c", "print('ok')"], timeout=5.0)
        self.assertEqual((code, out), (0, "ok\n"))


class RunnerWithTimeoutTest(unittest.TestCase):
    def test_the_bound_runner_takes_only_argv_and_still_times_out(self):
        # A Runner's signature is argv-only, so a caller that knows its command
        # should be instant has no other way to shorten the deadline.
        runner = proc.runner_with_timeout(0.2)

        started = time.monotonic()
        code, out = runner(["sleep", "40"])
        elapsed = time.monotonic() - started

        self.assertEqual((code, out), (124, ""))
        self.assertLess(elapsed, 2.0)

    def test_the_bound_runner_passes_a_successful_call_through(self):
        runner = proc.runner_with_timeout(5.0)
        self.assertEqual(runner(["/usr/bin/python3", "-c", "print('ok')"]), (0, "ok\n"))


class MissingBinaryTest(unittest.TestCase):
    """A binary that is not installed must be a nonzero exit, not an exception.

    Every caller treats nonzero as failure, so a missing tool degrades on its own --
    but an escaping FileNotFoundError took down the doctor (a raw traceback, zero
    checks printed), the app's own startup probe, and the notification worker thread.
    """

    def test_a_missing_binary_returns_nonzero_instead_of_raising(self):
        code, out = proc.run_command(["definitely-not-a-real-binary-xyz"])
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")

    def test_a_binary_that_is_a_directory_also_degrades(self):
        # Not FileNotFoundError but still an OSError (IsADirectoryError/PermissionError).
        code, out = proc.run_command(["/tmp"])
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")
