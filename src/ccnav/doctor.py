"""Prerequisite checks. cc_navigator refuses to be useful until these pass.

This project's one rule -- never trust an API's self-report; act through one
channel and verify through another -- shapes the tmux-config check, which is the
dangerous one. A regex over tmux's option tables can never be authoritative
(tmux has hundreds of options and several command aliases), so the check comes
in two layers:

  * check_tmux_conf(text): a pure, offline, best-effort parse. Its value is that
    it can NAME the offending line and print a fix. It is a hint, not a verdict.
  * probe_tmux_conf(conf): THE VERDICT. It loads the user's config into a tmux
    server of its own on a private socket, reproduces the exact segfault, and
    believes the reproduction.

If the probe says a config is fatal, the doctor fails regardless of what the
regex thought. If the probe cannot run, it says so; it never guesses "fine".
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import gnome
from .proc import Runner, run_command

TITLE_FORMAT = "ccnav:#{session_name}"

# Commands that set an option. Measured against tmux 3.0a on a private socket: a
# line whose first word is one of these and whose leading flags include none of
# -g/-q/-s corrupts the server at config-load time; a Space then sent through
# send-keys to a detached session segfaults it.
_SET_COMMANDS = ("set", "set-option", "setw", "set-window-option")

# -g (global), -q (quiet: suppresses the config-load error), -s (server table:
# needs no target) are each protective. Flags bundle (-gw, -qw, -sg, -ug, -as),
# so membership is tested PER CHARACTER. Derived from the measured matrix in
# task-11-report.md, not from tmux's source. The probe is the verdict.
_SAFE_FLAGS = frozenset("gqs")

# Unanchored on purpose (`.search`, no `^`): a `#`-prefixed comment line, if it
# were NOT filtered out first, WOULD then match the pattern after the `# `. That
# is what makes the comment-skip in check_tmux_titles load-bearing and testable.
# An anchored `^set` would make the skip vacuous (a `#` line never matches it) --
# which is why the plan's re.M version did not, in fact, have the comment bug the
# brief attributes to it (measured; see task-11-report.md).
_TITLES_ON = re.compile(r"set(-option)?\s+-g\s+set-titles\s+on\b")
_TITLES_STRING = re.compile(
    r"set(-option)?\s+-g\s+set-titles-string\s+['\"]?" + re.escape(TITLE_FORMAT)
)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str
    required: bool = True


def _line_is_fatal(line: str) -> bool:
    tokens = line.split()
    if not tokens or tokens[0] not in _SET_COMMANDS:
        return False
    for token in tokens[1:]:
        if not token.startswith("-"):
            break  # reached the option name; no protective flag was seen
        if any(char in _SAFE_FLAGS for char in token[1:]):
            return False
    return True


def check_tmux_conf(text: str) -> Check:
    """Best-effort HINT: name every `set`-family line that lacks -g/-q/-s.

    A line is fatal iff, after stripping comments and leading whitespace, its
    first word is one of set/set-option/setw/set-window-option AND none of the
    single-letter flags in its leading `-xyz` groups is g, q or s.

    This check is ADVISORY (required=False): it names a likely offender and its
    fix, but it is not the gate. probe_tmux_conf is. A hint that could fail the
    doctor on its own would let the parser's blind spots (below) override the
    authoritative reproduction -- e.g. a line-continuation `set \` + `-g ...`
    reads as fatal here but actually works, and must not veto a passing probe.

    Known blind spots, stated rather than papered over. Each is caught by the
    probe; none is caught here:
      * `source-file` includes are not followed.
      * A line split across a trailing `\` continuation is judged unjoined, so
        `set \` on its own line reads as a false positive.
      * Command-name abbreviations (`set-o`, `set-w`, `set-win`) and a fatal set
        nested inside `if-shell "..." "set ..."` read as safe (false negatives).
      * The flag rule is derived from a measured matrix, not tmux's source. Bare
        `setenv FOO bar` and `set-hook after-new-session ""` were ALSO measured
        fatal (report investigation (a)) but are out of scope: chasing tmux's
        whole command surface with a regex is the trap the two-layer design
        exists to avoid.
    """
    offenders = []  # type: List[str]
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _line_is_fatal(stripped):
            offenders.append(stripped)
    if offenders:
        return Check(
            name="tmux.conf mode-keys",
            ok=False,
            detail=(
                "these lines set an option without -g/-q/-s; tmux 3.0a corrupts "
                "the server at config load and segfaults on the next space sent "
                "to a detached session: " + "; ".join(offenders)
            ),
            fix="add -g, e.g. `set mode-keys vi` -> `setw -g mode-keys vi`",
            required=False,
        )
    return Check(
        "tmux.conf mode-keys",
        True,
        "no `set`-family line is missing -g/-q/-s",
        "",
        required=False,
    )


def _uncommented_lines(text: str) -> List[str]:
    lines = []  # type: List[str]
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
    return lines


def check_tmux_titles(text: str) -> Check:
    """Both `set-titles on` AND a `set-titles-string` of the ccnav format must be
    present. Comments are skipped in BOTH tests (a commented-out line sets
    nothing, so the jump would address a title that does not exist). An unquoted
    value is accepted; tmux accepts it.
    """
    lines = _uncommented_lines(text)
    missing = []  # type: List[str]
    if not any(_TITLES_ON.search(line) for line in lines):
        missing.append("set -g set-titles on")
    if not any(_TITLES_STRING.search(line) for line in lines):
        missing.append("set -g set-titles-string '%s'" % TITLE_FORMAT)
    if missing:
        return Check(
            name="tmux.conf set-titles",
            ok=False,
            detail="the outer window title is cc_navigator's only address to jump to",
            fix="add to ~/.tmux.conf:\n    " + "\n    ".join(missing),
        )
    return Check("tmux.conf set-titles", True, "window title is owned by tmux", "")


def _hook_commands(settings: Dict[str, object]) -> List[str]:
    commands = []  # type: List[str]
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return commands
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            inner = entry.get("hooks")
            if not isinstance(inner, list):
                continue
            for hook in inner:
                if not isinstance(hook, dict):
                    continue
                command = hook.get("command")
                if isinstance(command, str):
                    commands.append(command)
    return commands


def check_claude_hooks(settings: Dict[str, object], hook_path: str) -> Check:
    if any(hook_path in command for command in _hook_commands(settings)):
        return Check(
            "claude hooks", True, "cc-navigator-hook is installed", "", required=False
        )
    return Check(
        name="claude hooks",
        ok=False,
        detail="sessions started without the hook never appear in the list",
        fix="point the recommended hooks (Notification, Stop, PreToolUse, "
        "PostToolUse, SubagentStart, SubagentStop, SessionStart, SessionEnd, "
        "UserPromptSubmit) at %s in ~/.claude/settings.json" % hook_path,
        required=False,
    )


def check_codex_hooks(settings: Dict[str, object], hook_path: str) -> Check:
    command = hook_path + " --provider codex"
    if command in _hook_commands(settings):
        return Check(
            "codex hooks",
            True,
            "cc-navigator-hook is configured (review trust in Codex /hooks)",
            "",
            required=False,
        )
    return Check(
        name="codex hooks",
        ok=False,
        detail="Codex sessions started without the hook never appear in the list",
        fix="install the Codex hook from Settings → Integration, then trust it in /hooks",
        required=False,
    )


def check_any_agent_hooks(claude: Check, codex: Check) -> Check:
    if claude.ok or codex.ok:
        return Check("session hooks", True, "at least one agent integration is installed", "")
    return Check(
        "session hooks",
        False,
        "neither Claude Code nor Codex can report sessions",
        "enable at least one hook in Settings → Integration",
    )


_PROBE_SESSION = "ccnav_probe"
# The space is the whole point: it is what reproduces the segfault (report
# investigation (c)). Sending 'ab' instead would make every fatal config pass.
_PROBE_PAYLOAD = "a b"


def _probe_socket_name() -> str:
    # Private, unique, and NEVER "default". os.getpid() keeps concurrent doctors
    # (and the test suite) from colliding on one socket.
    return "ccnav_probe_%d" % os.getpid()


def probe_tmux_conf(
    conf_path,
    run: Runner = run_command,
    socket_name: Optional[str] = None,
) -> Check:
    """THE VERDICT: reproduce the segfault in a server of our own, then believe it.

    Loads the user's config with `-f` into a throwaway tmux server on a private
    -L socket, sends a literal space into a DETACHED session, and asks whether
    the server is still alive (`list-sessions` exit 0 == safe).

    Two measured facts about the method (see task-11-report.md):
      * The session is detached on purpose. With a client attached the segfault
        does NOT fire, so an attached probe would false-negative on a fatal conf.
      * The conf runs in a server of ours, so a `run-shell` line in the user's
        conf WOULD execute. This is the user's own config, on a private socket,
        run once.

    "I could not check" is never reported as "it is fine": if tmux is absent or
    the conf does not exist, the Check is ok=False and says which.
    """
    name = "tmux.conf live probe"
    if shutil.which("tmux") is None:
        return Check(
            name,
            False,
            "could not reproduce the crash: tmux is not installed",
            "install tmux so the doctor can verify your config",
        )
    conf_path = str(conf_path)
    if not os.path.exists(conf_path):
        return Check(
            name,
            False,
            "could not reproduce the crash: no config file at %s" % conf_path,
            "",
        )

    socket = socket_name or _probe_socket_name()
    # `tmux -L default` resolves to the user's REAL server: kill-server here would
    # destroy every live Claude session. The project's prime directive is never to
    # touch the default socket, so refuse it before any tmux runs.
    if socket == "default":
        raise ValueError("probe_tmux_conf must never target the default socket")
    base = ["tmux", "-L", socket]
    try:
        run(base + ["kill-server"])  # ignore failure: nothing to kill yet
        run(base + ["-f", conf_path, "new-session", "-d", "-s", _PROBE_SESSION])
        run(base + ["send-keys", "-t", _PROBE_SESSION, "-l", "--", _PROBE_PAYLOAD])
        code, _ = run(base + ["list-sessions"])
    finally:
        # A leaked server is a bug; this runs even if a step above raised.
        run(base + ["kill-server"])

    if code == 0:
        return Check(name, True, "the config survives a space sent to a session", "")
    return Check(
        name,
        False,
        "the server DIED after a space was typed -- this config will kill every "
        "detached Claude session the moment a reply contains a space",
        "add -g/-q/-s to the offending option, e.g. `setw -g mode-keys vi`",
    )


def _read(path: pathlib.Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def run_all(
    tmux_conf: Optional[pathlib.Path] = None,
    claude_settings: Optional[pathlib.Path] = None,
    codex_hooks: Optional[pathlib.Path] = None,
    hook_path: str = "",
    run: Runner = run_command,
) -> List[Check]:
    home = pathlib.Path(os.path.expanduser("~"))
    tmux_conf = tmux_conf or home / ".tmux.conf"
    claude_settings = claude_settings or home / ".claude" / "settings.json"
    codex_home = pathlib.Path(os.environ.get("CODEX_HOME") or (home / ".codex"))
    codex_hooks = codex_hooks or codex_home / "hooks.json"
    hook_path = hook_path or str(
        pathlib.Path(__file__).resolve().parents[2] / "bin" / "cc-navigator-hook"
    )

    conf_text = _read(tmux_conf)
    try:
        settings = json.loads(_read(claude_settings) or "{}")
    except ValueError:
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    try:
        codex_settings = json.loads(_read(codex_hooks) or "{}")
    except ValueError:
        codex_settings = {}
    if not isinstance(codex_settings, dict):
        codex_settings = {}

    claude_check = check_claude_hooks(settings, hook_path)
    codex_check = check_codex_hooks(codex_settings, hook_path)

    # Ask the interpreter to actually import what the app imports, rather than
    # stat'ing /usr/bin/python3 -- which exists on every Debian box, while
    # python3-gi does not. Stat'ing it made the single most likely fresh-user
    # failure (no PyGObject) the one case guaranteed to print [ok]. cairo is in
    # the probe because ui.py imports it unconditionally and python3-gi does not
    # pull it in. Import-only, so this is safe with no display.
    gi_code, _out = run([
        "/usr/bin/python3", "-c",
        "import gi, cairo; gi.require_version('Gtk','3.0'); from gi.repository import Gtk",
    ])

    return [
        Check(
            "python3",
            gi_code == 0,
            "/usr/bin/python3 can import PyGObject (gi + Gtk 3) and cairo",
            "apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0",
        ),
        Check(
            "tmux",
            shutil.which("tmux") is not None,
            "tmux addresses every session by pane",
            "apt install tmux",
        ),
        Check(
            "xprop",
            shutil.which("xprop") is not None,
            "xprop independently verifies that focus actually moved",
            "apt install x11-utils",
        ),
        Check(
            "gdbus",
            shutil.which("gdbus") is not None,
            "gdbus carries the jump request to GNOME Shell",
            "apt install libglib2.0-bin",
        ),
        Check(
            "notify-send",
            shutil.which("notify-send") is not None,
            "desktop notifications when a session becomes your turn",
            "apt install libnotify-bin",
            required=False,  # a feature, not a prerequisite: the panel works without it
        ),
        check_tmux_conf(conf_text),
        probe_tmux_conf(tmux_conf, run=run),
        check_tmux_titles(conf_text),
        claude_check,
        codex_check,
        check_any_agent_hooks(claude_check, codex_check),
        Check(
            "gnome shell eval",
            gnome.eval_available(run=run),
            "required only for jump; blocked from GNOME 41 onward",
            "jump stays disabled; listing and typing replies still work",
            required=False,
        ),
    ]


def main() -> int:
    required_failures = 0
    for check in run_all():
        if check.ok:
            mark = "ok  "
        elif check.required:
            mark = "FAIL"
            required_failures += 1
        else:
            mark = "warn"
        print("[%s] %-22s %s" % (mark, check.name, check.detail))
        if not check.ok and check.fix:
            print("       fix: %s" % check.fix)
    # Exit nonzero ONLY on a failed REQUIRED check. An advisory failure (Eval is
    # blocked from GNOME 41 on, which the user cannot fix) prints as `warn` and
    # must NOT make the doctor unable to ever pass -- a doctor that can never
    # exit 0 teaches the user to ignore the doctor.
    return 1 if required_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
