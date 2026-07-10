# Task 6 brief: tmux actions — select pane and send text

BASE commit: `909e8a2` (feat/cc-navigator) — confirm with `git log -1`.
Plan: `docs/superpowers/plans/2026-07-10-cc-navigator.md`, section `### Task 6`
(lines 919-1049). The plan is authoritative if it disagrees with this brief.

## Global Constraints (bind every task)

- **Interpreter is `/usr/bin/python3` (3.8.10).** Never invoke bare `python3`.
- **Zero third-party dependencies.** Stdlib `unittest` only.
- Python 3.8: no `match`, no `X | Y` runtime annotations. `src/` modules start with
  `from __future__ import annotations`. Test files do not.
- **Never trust an API's self-report.** Verify by running code — including your own
  claims about what can and cannot be tested.
- **User text sent to tmux is always one `argv` element after `-l --`. It is never
  interpolated into a shell string.** This task is where that rule lives.
- Test command: `./run-tests`.

## Context

This is the action half of `tmuxctl`. Two things the UI does:

**Jump to a session.** Three tmux commands in order: `switch-client`,
`select-window`, `select-pane`. All three run unconditionally — `switch-client`
fails when no client is attached to that server, and that failure is expected and
harmless. Do not short-circuit on a non-zero exit.

**Send a line of text into a session.** The user types free text in cc_navigator's
input box; it must arrive in Claude Code's prompt byte-for-byte. Two flags carry
the whole safety argument:

- Without `-l`, tmux interprets words like `Enter`, `C-c`, `Escape` as **key
  names**. A user typing the word "Enter" would send a keypress, not the word.
- Without `--`, text beginning with `-` is parsed as an **option**.

The text travels as a single `argv` element from Python straight to `execve`. It
never touches a shell, so quoting, `$HOME`, backslashes, and semicolons are inert.
`Enter` is then sent as a **separate** command, without `-l`, so it is a keypress.

Tasks 1-5 are committed; HEAD is `909e8a2`. `tmuxctl.py` already imports `Runner`
and `run_command` from `proc`. Append to it; do not restructure it.

## Files

- Modify: `src/ccnav/tmuxctl.py` (append only)
- Test: `tests/test_tmuxctl_action.py`

## Interface

- `tmuxctl.select_argvs(socket: str, pane: str) -> List[List[str]]`
- `tmuxctl.send_text_argvs(socket: str, pane: str, text: str) -> List[List[str]]`
- `tmuxctl.select_pane(socket: str, pane: str, run: Runner = run_command) -> None`
- `tmuxctl.send_text(socket: str, pane: str, text: str, run: Runner = run_command) -> None`

## This is a TDD task. Follow the steps in order.

### Step 1: Write the failing test

`tests/test_tmuxctl_action.py`, exactly as the plan gives it:

```python
import unittest

from ccnav import tmuxctl

HOSTILE = "yes; echo 'x' \"y\" $HOME \\ 한글 ✳ Enter C-c"


class SelectArgvsTest(unittest.TestCase):
    def test_switch_then_select_window_then_select_pane(self):
        argvs = tmuxctl.select_argvs("/tmp/s", "%12")
        self.assertEqual(
            argvs,
            [
                ["tmux", "-S", "/tmp/s", "switch-client", "-t", "%12"],
                ["tmux", "-S", "/tmp/s", "select-window", "-t", "%12"],
                ["tmux", "-S", "/tmp/s", "select-pane", "-t", "%12"],
            ],
        )

    def test_select_pane_runs_every_argv_even_if_one_fails(self):
        seen = []

        def fake_run(argv):
            seen.append(list(argv))
            return (1, "") if "switch-client" in argv else (0, "")

        tmuxctl.select_pane("/tmp/s", "%12", run=fake_run)
        self.assertEqual(len(seen), 3)


class SendTextArgvsTest(unittest.TestCase):
    def test_uses_literal_flag_and_double_dash(self):
        argvs = tmuxctl.send_text_argvs("/tmp/s", "%12", "hello")
        self.assertEqual(
            argvs,
            [
                ["tmux", "-S", "/tmp/s", "send-keys", "-t", "%12", "-l", "--", "hello"],
                ["tmux", "-S", "/tmp/s", "send-keys", "-t", "%12", "Enter"],
            ],
        )

    def test_hostile_text_is_a_single_argv_element(self):
        argvs = tmuxctl.send_text_argvs("/tmp/s", "%12", HOSTILE)
        self.assertEqual(argvs[0][-1], HOSTILE)
        self.assertEqual(len(argvs[0]), 9)

    def test_text_starting_with_dash_is_protected_by_double_dash(self):
        argvs = tmuxctl.send_text_argvs("/tmp/s", "%12", "-n --flag")
        self.assertEqual(argvs[0][-2], "--")
        self.assertEqual(argvs[0][-1], "-n --flag")

    def test_enter_is_sent_as_a_separate_named_key(self):
        argvs = tmuxctl.send_text_argvs("/tmp/s", "%12", "Enter")
        # The word "Enter" as user text must be literal, not a keypress.
        self.assertIn("-l", argvs[0])
        self.assertEqual(argvs[0][-1], "Enter")
        self.assertNotIn("-l", argvs[1])
```

### Step 2: Run the test to verify it fails

`./run-tests -k SelectArgvsTest -k SendTextArgvsTest`
Expected: FAIL with `AttributeError: module 'ccnav.tmuxctl' has no attribute 'select_argvs'`
Capture it — RED evidence.

### Step 3: Append the implementation

```python
def select_argvs(socket: str, pane: str) -> List[List[str]]:
    """switch-client is best effort: it fails when no client is attached."""
    return [
        ["tmux", "-S", socket, "switch-client", "-t", pane],
        ["tmux", "-S", socket, "select-window", "-t", pane],
        ["tmux", "-S", socket, "select-pane", "-t", pane],
    ]


def send_text_argvs(socket: str, pane: str, text: str) -> List[List[str]]:
    """`-l` and `--` are mandatory.

    Without -l, tmux reads words like 'Enter' and 'C-c' as key names.
    Without --, text beginning with '-' is parsed as an option.
    The text travels as one argv element and never touches a shell.
    """
    return [
        ["tmux", "-S", socket, "send-keys", "-t", pane, "-l", "--", text],
        ["tmux", "-S", socket, "send-keys", "-t", pane, "Enter"],
    ]


def select_pane(socket: str, pane: str, run: Runner = run_command) -> None:
    for argv in select_argvs(socket, pane):
        run(argv)


def send_text(socket: str, pane: str, text: str, run: Runner = run_command) -> None:
    for argv in send_text_argvs(socket, pane, text):
        run(argv)
```

### Step 4: Run the test to verify it passes

`./run-tests -k SelectArgvsTest -k SendTextArgvsTest` → `Ran 6 tests` / `OK`

### Step 5: You are authorised to add one test the plan forgot

Look carefully at the plan's six tests. `select_pane` has a test proving it runs
all three argvs. **`send_text` has no test at all** — only `send_text_argvs` does.
Nothing would notice if `send_text` ran only the first argv and never pressed
Enter, which would leave the user's text sitting unsubmitted in Claude Code's
prompt.

Add a test mirroring `test_select_pane_runs_every_argv_even_if_one_fails`: inject a
fake `run`, call `send_text`, and assert both argvs were executed in order. This is
explicitly requested, not scope creep.

Confirm my reasoning before you act on it — if you find an existing test that
would in fact catch that regression, say so and skip the addition.

### Step 6: Prove your tests are not vacuous

Break the implementation, confirm a **named** test fails, restore. At minimum:

1. Drop `-l` from the first `send-keys` argv.
2. Drop `--` from the first `send-keys` argv.
3. Add `-l` to the `Enter` argv.
4. Make `select_pane` `break` on a non-zero exit code.
5. Make `send_text` run only `argvs[0]`.
6. Reorder `select_argvs` to put `select-pane` first.

Report which named test caught each. A survivor is a finding — name the missing
test rather than inventing a contorted one.

**Clear `__pycache__` is no longer necessary** — `run-tests` now sets
`PYTHONDONTWRITEBYTECODE=1`, because a size-preserving mutation restored within the
same second used to run against stale bytecode and report a false survivor. Mutations
2, 3 and 6 above are near-size-preserving, so this matters. Do not remove that
setting from `run-tests`.

### Step 7: The verification that actually matters — real tmux, private socket

The unit tests only check argv **shape**. They cannot tell you whether tmux truly
delivers hostile text byte-for-byte. Find out.

**Never touch the default tmux socket. The user has live Claude Code sessions on
it. Always pass `-S` with a socket path under your own temp directory.**

```bash
S=$(mktemp -u /tmp/ccnav-t6.XXXX)
OUT=$(mktemp /tmp/ccnav-t6-out.XXXX)
tmux -S "$S" new-session -d -s t6 "cat > $OUT"
# then, from python, call tmuxctl.send_text(S, "<pane id>", HOSTILE)
# then kill the server and diff $OUT against HOSTILE
```

Requirements:
- Use the **real `tmuxctl.send_text`** with the real `run_command`, not a hand-typed
  tmux command. The point is to test the code that ships.
- Get the pane id from `tmux -S "$S" list-panes -a -F '#{pane_id}'`.
- Compare the bytes that landed in `$OUT` against `HOSTILE` **exactly**. Print both
  as hex if they differ. Give `cat` a moment to flush before killing the server.
- Then repeat with the single word `Enter` as the text, and confirm the file
  contains the five characters `Enter` followed by a newline — not an empty line.
  This is the test that proves `-l` does what the docstring claims.
- `tmux -S "$S" kill-server` and remove the temp files when done.

Paste the real output into your report. If the bytes do not match, **stop and report
BLOCKED** — that would invalidate the whole input feature and I need to know before
Task 9 builds a UI on top of it.

### Step 8: Investigate `switch-client -t <pane-id>` and report

`switch-client -t` documents its argument as a **target-session**, but
`select_argvs` passes a **pane id** (`%12`). I do not know whether tmux resolves a
pane id to its session here, or whether the command simply fails every time and the
"best effort" comment is quietly covering a bug.

Find out empirically on your private socket. Create two sessions, and for each of
the three commands record the exit code and stderr when given a pane id as `-t`.
Distinguish "failed because no client is attached" from "failed because it cannot
resolve a pane id as a session". `run_command` sends stderr to `DEVNULL`, so call
tmux directly for this diagnostic.

**Do not fix anything.** Report what you found. If `switch-client` cannot take a
pane id, that is a real defect in the jump path and I will decide how to handle it
before Task 8 wires it up.

### Step 9: Commit

```bash
git add src/ccnav/tmuxctl.py tests/test_tmuxctl_action.py
git commit -m "feat: tmux pane selection and literal text injection"
```

## Notes and traps

- Append to `tmuxctl.py`. Do not reorder or rewrite what Task 5 put there.
- `select_pane` runs all three argvs and **ignores every exit code**. That is
  deliberate, and `test_select_pane_runs_every_argv_even_if_one_fails` pins it.
- The `Enter` argv has **no** `-l`. That asymmetry is the whole point.
- Do not add shell quoting, `shlex.quote`, or escaping of any kind. Adding it would
  be actively wrong: the text never reaches a shell, so quoting would corrupt it.
- Do not add a `confirm` parameter, a dry-run mode, or logging. YAGNI.
- Work from `/data/playground/cc_navigator`.
