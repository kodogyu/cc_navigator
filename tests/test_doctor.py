"""Tests for the prerequisite doctor.

None of these start a real tmux server or call a real subprocess: every live
path is driven through an injected fake Runner that records argv and returns
scripted (code, out) pairs. A real server belongs in Task 12's integration
test, not here.

The expected results below are not taken on faith from the brief -- every
fatal/safe line was reproduced against real tmux 3.0a on a private -L socket
(see task-11-report.md, "The measured matrix").
"""
import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from ccnav import doctor

HOOK = "/home/kodogyu/playground/cc_navigator/bin/cc-navigator-hook"

GOOD_CONF = """
setw -g mode-keys vi
set -g mouse on
set -g set-titles on
set -g set-titles-string 'ccnav:#{session_name}'
"""


def settings_with_hook(command=HOOK):
    return {
        "hooks": {
            "Stop": [
                {"matcher": "", "hooks": [{"type": "command", "command": command}]}
            ]
        }
    }


# A realistic non-empty ~/.claude/settings.json that has NO hooks key at all --
# exactly this machine's file. The check must fail cleanly against it, not only
# against {}.
REAL_SETTINGS_NO_HOOKS = {
    "env": {"CLAUDE_CODE_ACCESSIBILITY": "1"},
    "model": "opus[1m]",
    "theme": "dark",
}


def _verb(argv):
    """The tmux subcommand inside a `tmux -L <sock> [-f <conf>] <verb> ...` argv."""
    for token in argv:
        if token in ("kill-server", "new-session", "send-keys", "list-sessions"):
            return token
    return ""


class FakeTmux(object):
    """Records argv; scripts a (code, out) per tmux verb; can raise on one verb."""

    def __init__(self, responses=None, raise_on=None):
        self.calls = []
        self._responses = responses or {}
        self._raise_on = raise_on

    def __call__(self, argv):
        argv = list(argv)
        self.calls.append(argv)
        verb = _verb(argv)
        if self._raise_on is not None and verb == self._raise_on:
            raise RuntimeError("boom in %s" % verb)
        return self._responses.get(verb, (0, ""))

    def verbs(self):
        return [_verb(c) for c in self.calls]

    def send_keys_call(self):
        for call in self.calls:
            if _verb(call) == "send-keys":
                return call
        return None


# --------------------------------------------------------------------------
# check_tmux_conf -- the corrected, per-character flag rule
# --------------------------------------------------------------------------
class TmuxConfFatalTest(unittest.TestCase):
    """Every line here was measured DEAD against real tmux 3.0a."""

    def _fatal(self, line):
        check = doctor.check_tmux_conf(line + "\n")
        self.assertFalse(check.ok, "%r should be flagged fatal" % line)
        self.assertIn(line, check.detail, "detail must name the offending line")
        return check

    def test_bare_set_mode_keys(self):
        check = self._fatal("set mode-keys vi")
        self.assertIn("mode-keys", check.detail)
        # The printed fix must show the -g form.
        self.assertIn("setw -g mode-keys vi", check.fix)

    def test_set_option_mode_keys(self):
        self._fatal("set-option mode-keys vi")

    def test_setw_mode_keys(self):
        # Plan's regex `^\s*set(-option)?\s+mode-keys` MISSES this. Mutation 1.
        self._fatal("setw mode-keys vi")

    def test_set_w_flag_mode_keys(self):
        self._fatal("set -w mode-keys vi")

    def test_set_window_option_mode_keys(self):
        self._fatal("set-window-option mode-keys vi")

    def test_set_mode_keys_emacs_the_default_value_is_still_fatal(self):
        # It is not about the value.
        self._fatal("set mode-keys emacs")

    def test_set_clock_mode_style(self):
        # It is not about mode-keys.
        self._fatal("set clock-mode-style 12")

    def test_set_status_bg_a_session_option(self):
        # Plan's mode-keys-only regex misses this too. Mutation 1.
        self._fatal("set status-bg black")

    def test_set_a_status_bg(self):
        # -a (append) is not protective.
        self._fatal("set -a status-bg black")

    def test_set_u_mode_keys(self):
        self._fatal("set -u mode-keys")


class TmuxConfSafeTest(unittest.TestCase):
    """Every line here was measured ALIVE against real tmux 3.0a."""

    def _safe(self, line):
        self.assertTrue(
            doctor.check_tmux_conf(line + "\n").ok, "%r should be safe" % line
        )

    def test_set_g_mode_keys(self):
        self._safe("set -g mode-keys vi")

    def test_set_gw_mode_keys(self):
        # -gw bundles g. A per-token (whole -g) check would call this fatal.
        # Mutation 4.
        self._safe("set -gw mode-keys vi")

    def test_setw_g_mode_keys(self):
        self._safe("setw -g mode-keys vi")

    def test_set_window_option_g_mode_keys(self):
        self._safe("set-window-option -g mode-keys vi")

    def test_set_q_mode_keys(self):
        # -q suppresses the config-load error. Mutation 2 drops q from safe.
        self._safe("set -q mode-keys vi")

    def test_set_qw_mode_keys(self):
        self._safe("set -qw mode-keys vi")

    def test_set_ug_mode_keys(self):
        # g is the SECOND character. A first-character-only check calls it
        # fatal. The other direction of mutation 4.
        self._safe("set -ug mode-keys")

    def test_set_s_escape_time(self):
        # -s addresses the server table. Mutation 3 drops s from safe.
        self._safe("set -s escape-time 0")

    def test_set_sg_escape_time(self):
        self._safe("set -sg escape-time 0")

    def test_set_as_terminal_overrides(self):
        # -as bundles s (second char). Mutations 3 and 4 both.
        self._safe("set -as terminal-overrides ,xterm-256color:RGB")

    def test_bind_key_is_not_this_checks_business(self):
        self._safe("bind-key r source-file ~/.tmux.conf")

    def test_unbind_is_not_this_checks_business(self):
        self._safe("unbind C-b")


class TmuxConfScopeTest(unittest.TestCase):
    def test_commented_out_fatal_line_is_ignored(self):
        self.assertTrue(doctor.check_tmux_conf("# set mode-keys vi\n").ok)

    def test_indented_commented_out_fatal_line_is_ignored(self):
        self.assertTrue(doctor.check_tmux_conf("    #set mode-keys vi\n").ok)

    def test_empty_conf_is_fine(self):
        self.assertTrue(doctor.check_tmux_conf("").ok)

    def test_hash_inside_a_value_is_not_a_comment(self):
        # set-titles-string carries '#{...}'. The line is safe (-g) and must not
        # be mistaken for a comment nor flagged.
        self.assertTrue(
            doctor.check_tmux_conf(
                "set -g set-titles-string 'ccnav:#{session_name}'\n"
            ).ok
        )

    def test_setenv_and_set_hook_are_out_of_scope_of_the_pure_parse(self):
        # MEASURED FATAL: bare `setenv FOO bar` and `set-hook ... ""` kill the
        # server too. The pure parse deliberately does not chase tmux's whole
        # command surface -- the live probe is the verdict for these. See report.
        self.assertTrue(doctor.check_tmux_conf("setenv FOO bar\n").ok)
        self.assertTrue(
            doctor.check_tmux_conf('set-hook after-new-session ""\n').ok
        )

    def test_names_every_offending_line_not_just_the_first(self):
        # Mutation 5: stop at the first offender.
        conf = "set mode-keys vi\nset -g mouse on\nset status-bg black\n"
        check = doctor.check_tmux_conf(conf)
        self.assertFalse(check.ok)
        self.assertIn("set mode-keys vi", check.detail)
        self.assertIn("set status-bg black", check.detail)


# --------------------------------------------------------------------------
# check_tmux_titles
# --------------------------------------------------------------------------
class TmuxTitlesTest(unittest.TestCase):
    def test_good_conf_passes(self):
        self.assertTrue(doctor.check_tmux_titles(GOOD_CONF).ok)

    def test_missing_set_titles_string_fails(self):
        check = doctor.check_tmux_titles("set -g set-titles on\n")
        self.assertFalse(check.ok)
        self.assertIn("set-titles-string", check.fix)

    def test_missing_set_titles_on_fails(self):
        conf = "set -g set-titles-string 'ccnav:#{session_name}'\n"
        self.assertFalse(doctor.check_tmux_titles(conf).ok)

    def test_wrong_title_format_fails(self):
        conf = "set -g set-titles on\nset -g set-titles-string 'x'\n"
        self.assertFalse(doctor.check_tmux_titles(conf).ok)

    def test_commented_out_titles_do_not_count(self):
        # Mutation 6: stop skipping comments. Commented lines set nothing, so
        # the jump would address a title that does not exist.
        conf = (
            "# set -g set-titles on\n"
            "# set -g set-titles-string 'ccnav:#{session_name}'\n"
        )
        self.assertFalse(doctor.check_tmux_titles(conf).ok)

    def test_unquoted_title_value_is_accepted(self):
        # tmux accepts an unquoted value; the plan's regex demanded a quote.
        conf = "set -g set-titles on\nset -g set-titles-string ccnav:#{session_name}\n"
        self.assertTrue(doctor.check_tmux_titles(conf).ok)

    def test_double_quoted_title_value_is_accepted(self):
        conf = (
            "set -g set-titles on\n"
            'set -g set-titles-string "ccnav:#{session_name}"\n'
        )
        self.assertTrue(doctor.check_tmux_titles(conf).ok)


# --------------------------------------------------------------------------
# check_claude_hooks
# --------------------------------------------------------------------------
class ClaudeHooksTest(unittest.TestCase):
    def test_detects_the_hook(self):
        self.assertTrue(doctor.check_claude_hooks(settings_with_hook(), HOOK).ok)

    def test_empty_settings_fails(self):
        self.assertFalse(doctor.check_claude_hooks({}, HOOK).ok)

    def test_real_settings_without_a_hooks_key_fails_cleanly(self):
        check = doctor.check_claude_hooks(REAL_SETTINGS_NO_HOOKS, HOOK)
        self.assertFalse(check.ok)
        self.assertIn(HOOK, check.fix)

    def test_a_different_command_does_not_count(self):
        settings = settings_with_hook(command="notify-send hi")
        self.assertFalse(doctor.check_claude_hooks(settings, HOOK).ok)

    def test_malformed_hooks_value_does_not_raise(self):
        for junk in ({"hooks": "nope"}, {"hooks": [1, 2]}, {"hooks": {"Stop": 5}}):
            self.assertFalse(doctor.check_claude_hooks(junk, HOOK).ok)


# --------------------------------------------------------------------------
# probe_tmux_conf -- the verdict (driven by a fake runner, no real tmux)
# --------------------------------------------------------------------------
class ProbeTmuxConfTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".conf", delete=False
        )
        tmp.write("set mode-keys vi\n")
        tmp.close()
        self.conf = tmp.name
        self.addCleanup(lambda: os.path.exists(self.conf) and os.unlink(self.conf))

    def test_socket_is_private_includes_pid_and_is_never_default(self):
        fake = FakeTmux()
        doctor.probe_tmux_conf(self.conf, run=fake)
        socket_args = []
        for call in fake.calls:
            self.assertIn("-L", call, "must use an explicit -L socket")
            socket_args.append(call[call.index("-L") + 1])
        self.assertTrue(socket_args)
        for name in socket_args:
            self.assertNotEqual(name, "default")
            self.assertIn(str(os.getpid()), name)
            self.assertTrue(name.startswith("ccnav"))
        # All calls share one socket.
        self.assertEqual(len(set(socket_args)), 1)

    def test_probe_sends_a_space_not_just_two_letters(self):
        # Mutation 9: the whole point of the probe is the space. If it sends
        # 'ab' every fatal config passes and the probe stops probing.
        fake = FakeTmux()
        doctor.probe_tmux_conf(self.conf, run=fake)
        send = fake.send_keys_call()
        self.assertIsNotNone(send, "the probe must send keys")
        self.assertIn("-l", send)
        self.assertIn("--", send)
        payload = send[send.index("--") + 1]
        self.assertEqual(payload, "a b")
        self.assertIn(" ", payload)

    def test_reports_fatal_when_the_server_dies(self):
        fake = FakeTmux(responses={"list-sessions": (1, "")})
        check = doctor.probe_tmux_conf(self.conf, run=fake)
        self.assertFalse(check.ok)
        self.assertIn("setw -g mode-keys vi", check.fix)

    def test_reports_safe_when_the_server_survives(self):
        fake = FakeTmux(responses={"list-sessions": (0, "probe: 1 windows\n")})
        check = doctor.probe_tmux_conf(self.conf, run=fake)
        self.assertTrue(check.ok)

    def test_kills_the_server_last(self):
        # Mutation 7: drop the finally kill-server. The pre-clean kill-server
        # would still be recorded, so assert the LAST call is a kill-server.
        fake = FakeTmux()
        doctor.probe_tmux_conf(self.conf, run=fake)
        self.assertEqual(_verb(fake.calls[-1]), "kill-server")

    def test_kills_the_server_even_when_a_step_raises(self):
        # The finally must fire on an unexpected raise. A leaked server is a bug.
        fake = FakeTmux(raise_on="send-keys")
        with self.assertRaises(RuntimeError):
            doctor.probe_tmux_conf(self.conf, run=fake)
        self.assertEqual(
            _verb(fake.calls[-1]),
            "kill-server",
            "kill-server must run in the finally after a raise",
        )

    def test_without_tmux_it_says_it_could_not_run_not_that_its_fine(self):
        # Mutation 8: return ok=True when tmux is absent. "I could not check" is
        # not "it is fine".
        fake = FakeTmux()
        with mock.patch("shutil.which", return_value=None):
            check = doctor.probe_tmux_conf(self.conf, run=fake)
        self.assertFalse(check.ok)
        self.assertEqual(fake.calls, [], "must not run tmux when tmux is absent")

    def test_missing_conf_says_it_could_not_run(self):
        fake = FakeTmux()
        check = doctor.probe_tmux_conf("/no/such/conf/at/all.conf", run=fake)
        self.assertFalse(check.ok)
        self.assertEqual(fake.calls, [], "must not run tmux with no conf to load")


# --------------------------------------------------------------------------
# Check dataclass + run_all + main
# --------------------------------------------------------------------------
class CheckDataclassTest(unittest.TestCase):
    def test_required_defaults_true(self):
        self.assertTrue(doctor.Check("x", True, "d", "f").required)


class RunAllTest(unittest.TestCase):
    def _fake_run(self, calls):
        def run(argv):
            argv = list(argv)
            calls.append(argv)
            if argv and "gdbus" in argv[0]:
                return 0, "(true, '2')"
            return 0, ""

        return run

    def _files(self):
        conf = tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False)
        conf.write(GOOD_CONF)
        conf.close()
        self.addCleanup(lambda: os.path.exists(conf.name) and os.unlink(conf.name))
        settings = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        settings.write("{}")
        settings.close()
        self.addCleanup(
            lambda: os.path.exists(settings.name) and os.unlink(settings.name)
        )
        return conf.name, settings.name

    def test_threads_the_runner_through_eval_and_probe(self):
        import pathlib

        conf, settings = self._files()
        calls = []
        checks = doctor.run_all(
            tmux_conf=pathlib.Path(conf),
            claude_settings=pathlib.Path(settings),
            hook_path=HOOK,
            run=self._fake_run(calls),
        )
        names = [c.name for c in checks]
        for expected in (
            "tmux.conf mode-keys",
            "tmux.conf live probe",
            "tmux.conf set-titles",
            "claude hooks",
            "gnome shell eval",
        ):
            self.assertIn(expected, names)
        # Injection actually happened: the fake saw both a gdbus and a tmux call.
        argv0s = [c[0] for c in calls if c]
        self.assertTrue(any("gdbus" in a for a in argv0s))
        self.assertTrue(any(a == "tmux" for a in argv0s))

    def test_eval_check_is_advisory_not_required(self):
        import pathlib

        conf, settings = self._files()
        calls = []
        checks = doctor.run_all(
            tmux_conf=pathlib.Path(conf),
            claude_settings=pathlib.Path(settings),
            hook_path=HOOK,
            run=self._fake_run(calls),
        )
        eval_check = [c for c in checks if c.name == "gnome shell eval"][0]
        self.assertFalse(eval_check.required)


class MainExitCodeTest(unittest.TestCase):
    def _main_with(self, checks):
        buf = io.StringIO()
        with mock.patch.object(doctor, "run_all", return_value=checks):
            with contextlib.redirect_stdout(buf):
                code = doctor.main()
        return code, buf.getvalue()

    def test_required_failure_exits_nonzero(self):
        # Mutation 11: main() ignores required failures.
        code, out = self._main_with(
            [doctor.Check("x", False, "detail", "fixit", required=True)]
        )
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)

    def test_only_advisory_failure_exits_zero_and_prints_warn(self):
        # Mutation 10: main() counts advisory failures. A doctor that can never
        # pass because of an unfixable Eval block teaches the user to ignore it.
        code, out = self._main_with(
            [
                doctor.Check("ok-req", True, "d", "", required=True),
                doctor.Check("adv", False, "d", "f", required=False),
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("warn", out)
        self.assertNotIn("FAIL", out)

    def test_all_passing_exits_zero(self):
        code, _ = self._main_with([doctor.Check("x", True, "d", "", required=True)])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
