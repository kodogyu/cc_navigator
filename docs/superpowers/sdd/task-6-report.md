# Task 6 report: tmux actions — select pane and send text

## What I implemented

Appended four functions to `src/ccnav/tmuxctl.py` (pure append, no restructuring
of Task 5's `parse_kv_lines` / `list_argv` / `_query` / `sessions_by_pane` /
`titles_by_pane`):

- `select_argvs(socket, pane) -> List[List[str]]` — builds the three argvs
  (`switch-client`, `select-window`, `select-pane`), each targeting the pane id.
- `send_text_argvs(socket, pane, text) -> List[List[str]]` — builds
  `send-keys -t <pane> -l -- <text>` followed by a separate
  `send-keys -t <pane> Enter` (no `-l` on the second one).
- `select_pane(socket, pane, run=run_command) -> None` — runs all three
  `select_argvs` unconditionally, ignoring every exit code (switch-client is
  best-effort when no client is attached).
- `send_text(socket, pane, text, run=run_command) -> None` — runs both
  `send_text_argvs` in order.

Test file `tests/test_tmuxctl_action.py` has the six tests exactly as given in
the plan, plus one authorized addition (see below) — 7 tests total.

## TDD Evidence

**RED** (`./run-tests -k SelectArgvsTest -k SendTextArgvsTest`, before
implementation):
```
AttributeError: module 'ccnav.tmuxctl' has no attribute 'select_argvs'
...
Ran 6 tests in 0.001s
FAILED (errors=6)
```

**GREEN** (same command, after implementation):
```
Ran 6 tests in 0.000s
OK
```

## Step 5: the authorized extra test

Checked all 6 original tests: `SelectArgvsTest.test_select_pane_runs_every_argv_even_if_one_fails`
calls `select_pane` and asserts 3 argvs ran. Nothing in `SendTextArgvsTest`
calls `send_text` — all four of its tests call `send_text_argvs` directly. I
confirmed there is no existing coverage of `send_text` itself, so I added:

```python
def test_send_text_runs_both_argvs_in_order(self):
    seen = []

    def fake_run(argv):
        seen.append(list(argv))
        return (0, "")

    tmuxctl.send_text("/tmp/s", "%12", "hello", run=fake_run)
    self.assertEqual(seen, tmuxctl.send_text_argvs("/tmp/s", "%12", "hello"))
```

Suite is now 7 tests, still green. This test is what caught mutation 5 below —
confirming the gap was real, not theoretical.

## Mutation Evidence

All six mutations applied one at a time to the working tree, verified with
`./run-tests -k <Class>`, then reverted before moving to the next. File was
byte-identical to the pre-mutation version after each revert (confirmed by
final `git diff --stat` showing only the intended 32-line net addition).

| # | Mutation | Caught by |
|---|----------|-----------|
| 1 | Drop `-l` from first `send-keys` argv | `test_uses_literal_flag_and_double_dash`, `test_hostile_text_is_a_single_argv_element`, `test_enter_is_sent_as_a_separate_named_key` |
| 2 | Drop `--` from first `send-keys` argv | `test_uses_literal_flag_and_double_dash`, `test_hostile_text_is_a_single_argv_element`, `test_text_starting_with_dash_is_protected_by_double_dash` |
| 3 | Add `-l` to the `Enter` argv | `test_uses_literal_flag_and_double_dash`, `test_enter_is_sent_as_a_separate_named_key` |
| 4 | Make `select_pane` `break` on non-zero exit | `test_select_pane_runs_every_argv_even_if_one_fails` |
| 5 | Make `send_text` run only `argvs[0]` | `test_send_text_runs_both_argvs_in_order` **(the added test — no original test catches this)** |
| 6 | Reorder `select_argvs` to put `select-pane` first | `test_switch_then_select_window_then_select_pane` (note: `test_select_pane_runs_every_argv_even_if_one_fails` does NOT catch this — it only counts calls, not order) |

No survivors. Every mutation was killed by a named test, and mutation 5 in
particular validates that Step 5's added test was not redundant — it is the
*only* test that kills it.

## Byte-exactness result (Step 7) — PASS

### RETRACTION of my earlier BLOCKED / segfault finding

An earlier version of this section claimed byte-exactness was **BLOCKED by a
segfault in tmux 3.0a itself**, triggered by "any literal space character" and,
separately, by an `Enter` keypress. **That diagnosis was wrong, and I retract
it in full. tmux 3.0a sends spaces, `Enter`, and the full HOSTILE string
correctly. Byte-exactness PASSES.**

**Real cause:** `tmux -S <socket>` still reads `~/.tmux.conf`. Line 1 of this
user's config is a bare `set mode-keys vi`. `mode-keys` is a *window* option;
setting it with bare `set` (no `-g`, no `-w`) makes tmux 3.0a abort the server
the moment an external command touches that option. Every experiment I ran was
the *first external command against a fresh server that had just parsed that
config line*, so every one of them killed the server — which is exactly why the
crash signature was byte-for-byte identical across three supposedly different
"triggers." An identical signature across different inputs is evidence of a
**common cause upstream of the inputs**, not of three separate input-triggered
bugs; I should have drawn that inference instead of inventing a space/Enter
theory. The project's own principle — *never trust an API's self-report* —
applies here: "server exited unexpectedly" is a self-report, and I took it at
face value as a tmux defect instead of asking what was different about this
machine. The design spec's Prerequisite 1 already records this exact
`~/.tmux.conf` bug (it is why Task 11 builds a doctor); reading it first would
have saved the misdiagnosis.

### Bisection (mine, reproducing the cause)

Each row: fresh private-socket server started with the given config, then a
single `send-keys -l -- 'hello world'`, then `list-sessions`.

| config passed via `-f` | result |
|---|---|
| empty file | survives |
| `set mode-keys vi` (the user's line 1) | **DIES — "server exited unexpectedly"** |
| `setw -g mode-keys vi` (window-scoped, correct) | survives |
| `set -g mode-keys vi` (global) | survives |

Only the bare-`set` form kills it. Spaces are a red herring — the space in
`hello world` was incidental; the server was already doomed by the config.

### Step 7 re-run under `-f /dev/null` (real `send_text`, real `run_command`)

Server started with `tmux -S "$S" -f /dev/null new-session -d ...` so
`~/.tmux.conf` is not read. Private socket under my temp dir, path confirmed
non-existent first, `-S` on every command. The Python driver was:

```python
from ccnav import tmuxctl
from ccnav.proc import run_command
tmuxctl.send_text(S, PANE, TEXT, run=run_command)   # the real shipping code
```

After each call the server was **still alive** (`list-sessions` listed the
session), then I `kill-server` to flush `cat` and compared `$OUT` bytes to
`(TEXT + "\n").encode("utf-8")` in Python:

| TEXT sent | landed in `$OUT` (hex) | expected (hex) | match |
|---|---|---|---|
| `yes; echo 'x' "y" $HOME \ 한글 ✳ Enter C-c` | `7965733b206563686f20277827202279222024484f4d45205c20ed959ceab88020e29cb320456e74657220432d630a` | identical | **YES** |
| `Enter` | `456e7465720a` (`E n t e r \n`) | `456e7465720a` | **YES** |
| `-n --flag` | `2d6e202d2d666c61670a` | `2d6e202d2d666c61670a` | **YES** |

- **HOSTILE** arrived byte-for-byte identical — 46 UTF-8 bytes plus the newline.
  Semicolons, single/double quotes, `$HOME`, the backslash, the Korean `한글`,
  the `✳`, and the words `Enter`/`C-c` all landed as literal text.
- **`Enter`** arrived as the five characters `E n t e r` followed by a newline —
  **not** an empty line, **not** a bare keypress. This is the proof that `-l`
  does what the docstring claims: the word `Enter` was sent as literal text by
  argv[0] (`send-keys -l -- Enter`), and the submit was a *separate* keypress
  from argv[1] (`send-keys Enter`, no `-l`) which `cat` recorded as the newline.
- **`-n --flag`** arrived identical — the leading `-` was not parsed as an
  option, confirming `--` does its job.

**Verdict: Byte-exactness PASS.** `-l` and `--` deliver arbitrary user text
into a pane byte-for-byte, and the trailing bare `Enter` submits it as a
keypress. The input feature's safety argument holds against real tmux.

### Prerequisite for all real-tmux work in this repo

Any real-tmux experiment or integration test in this repo **must** start its
server with `tmux -f /dev/null` (or first neutralize `~/.tmux.conf`), or it
will keep "crashing" for reasons that have nothing to do with the code under
test — specifically the bare `set mode-keys vi` on line 1 of this user's
config. Task 12's integration tests will hit this if they don't. The private
socket (`-S`) isolates *which server* you talk to; it does **not** isolate you
from the user's config file, which a private-socket server still reads at
startup.

### The real landmine (correct conclusion, corrected cause)

My earlier report said this code path "would crash the entire server (all
sessions), not just misdirect keystrokes." **That conclusion is correct and
important — but the cause is the config bug, not `send-keys`.** With
`~/.tmux.conf` as it currently stands, cc_navigator's *very first* real tmux
command against the user's live server — even a read-only `list-panes` in the
Task 5 query path — would trip the bare-`set mode-keys` abort and kill the
user's tmux server and every Claude Code session in it. This is a genuine,
serious hazard in this environment; it just fires on the first external command
of any kind, not specifically on space/Enter input. I am flagging it plainly so
it can be escalated. I did **not** modify `~/.tmux.conf` — fixing it is out of
scope and Task 11's doctor is the intended remedy.

## `switch-client -t <pane-id>` investigation (Step 8) — verdict HOLDS, re-verified clean

**Note:** my original Step 8 was run on a server that was dying from the config
bug above, so I redid the whole thing on a healthy server started with
`tmux -S "$S" -f /dev/null`. Two sessions (`sessA` → pane `%0`, `sessB` →
pane `%1`), tmux called directly (bypassing `run_command`'s `DEVNULL`) to
capture stderr. The verdict is unchanged and is now backed by clean evidence.

**Phase 1 — no client attached (healthy `-f /dev/null` server):**
```
$ tmux -S "$S" switch-client -t %0        # pane id      -> "no current client", exit 1
$ tmux -S "$S" switch-client -t sessA     # session name -> "no current client", exit 1
$ tmux -S "$S" select-window -t %0        # exit 0
$ tmux -S "$S" select-pane  -t %0         # exit 0
$ tmux -S "$S" list-sessions              # server STILL ALIVE afterwards
```
The server stayed up through all of these — the "no server running" I saw
before was the config abort, not the commands. With no attached client,
`switch-client` fails identically ("no current client") whether the `-t` target
is a pane id, a session name, or garbage: it short-circuits *before* resolving
`-t`, because there is no current client to switch. `select-window` and
`select-pane` both succeed with a pane id as `-t`.

**Phase 2 — with a real attached client** (attached through a Python `pty.fork`
so tmux sees a genuine terminal; the earlier background control-mode attach was
flaky and sometimes didn't register a `client_name`):
```
$ tmux -S "$S" list-clients -F 'name=[#{client_name}] session=[#{client_session}]'
name=[/dev/pts/46] session=[sessA]
$ tmux -S "$S" switch-client -c /dev/pts/46 -t %1    # pane id living in sessB
exit 0
  -> client session now: /dev/pts/46|sessB           # CLIENT MOVED sessA -> sessB
$ tmux -S "$S" switch-client -c /dev/pts/46 -t %0    # pane id living in sessA
exit 0
  -> client session now: /dev/pts/46|sessA           # CLIENT MOVED BACK
$ tmux -S "$S" switch-client -c /dev/pts/46 -t %999  # nonexistent pane id
can't find pane: %999                                 # exit 1
$ tmux -S "$S" switch-client -c /dev/pts/46 -t sessB # session name, control
exit 0  -> /dev/pts/46|sessB
```

**Verdict (unchanged, now trustworthy):** `switch-client -t <pane-id>` **does
resolve** a pane id to its containing session and switches the client there —
proven by the attached client's `client_session` actually moving `sessA` →
`sessB` when given `%1`, and back to `sessA` when given `%0`. A nonexistent
pane id fails cleanly with `can't find pane: %999` (exit 1), distinct from the
`no current client` failure — so tmux really is resolving the pane id, not
ignoring it. tmux's target parser accepts a pane id even though the man page
names the argument "target-session": not documentation-accurate, but not a bug.
The Phase 1 "no current client" failures are the *separate*, unconditional
no-attached-client case, which is exactly the harmless "best effort" fallback
the plan describes — cc_navigator will hit it whenever it fires the jump
against a server that has no client attached, and it does no damage. **No fix
is needed to `select_argvs`/`select_pane`; the jump path is sound.**

## Files changed

- `src/ccnav/tmuxctl.py` — appended `select_argvs`, `send_text_argvs`,
  `select_pane`, `send_text` (32 lines, pure append).
- `tests/test_tmuxctl_action.py` — new file, 7 tests (6 from the plan + 1
  authorized addition).
- Commit `cb114a4`: "feat: tmux pane selection and literal text injection".

## Self-review

- **Completeness:** all brief steps done, including both real-tmux
  investigations under `-f /dev/null` (Step 7 byte-exactness PASS with hex
  evidence for all three strings; Step 8 answered and re-verified clean).
- **Quality:** function names match the interface exactly; docstrings carried
  over from the plan verbatim since they're accurate; no dead code.
- **Discipline (YAGNI):** no `shlex.quote`, no shell quoting, no `confirm`
  param, no dry-run, no logging. Verified by reading the diff — it's exactly
  the four functions specified, nothing more.
- **Testing:** every unit test injects a fake `run`; none spawns a subprocess.
  Confirmed by inspection of `tests/test_tmuxctl_action.py` — the only real
  subprocess use anywhere in this task was in the manual Step 7/8
  investigations (ad hoc shell commands and a scratch `python3 -c` snippet),
  never inside `./run-tests`. Full suite output is clean (`Ran 67 tests ...
  OK`), no stray prints.
- **Safety:** every tmux invocation across all of Step 7 and Step 8 used
  `-S <private temp socket>` (and `-f /dev/null` on the server-starting
  command). No bare `tmux` command was run. Every socket path was confirmed
  non-existent before first use. All private-socket servers were killed and
  all temp files removed at the end; final sweep confirmed no `ccnav-t6*`
  files or orphan tmux processes remain, and the default socket was never
  touched.

## Issues / concerns

1. **Retracted:** my earlier claim that tmux 3.0a segfaults on spaces / on
   `Enter` was a misdiagnosis. tmux 3.0a sends all of it correctly;
   byte-exactness PASSES. The "crashes" were the user's `~/.tmux.conf` line 1
   (`set mode-keys vi`, bare `set` on a window option) aborting the server on
   the first external command. See the Byte-exactness section for the full
   retraction and bisection.
2. **Real, serious hazard (correct conclusion, corrected cause):** with
   `~/.tmux.conf` as it currently stands, the *first* real tmux command
   cc_navigator issues against the user's live default-socket server — even a
   read-only `list-panes` from the Task 5 query path, not just `send_text` —
   would trip the bare-`set mode-keys vi` abort and kill the user's tmux
   server and every Claude Code session in it. This is environment-wide, fires
   on any external command, and should be escalated. Task 11's doctor is the
   intended detector; the design spec's Prerequisite 1 already documents it.
   I did not modify `~/.tmux.conf` (out of scope).
3. **Prerequisite for downstream tasks:** all real-tmux work here (Task 12
   integration tests especially) must start servers with `tmux -f /dev/null`
   or first neutralize `~/.tmux.conf`. A private `-S` socket isolates *which
   server* you talk to but not the config file the server reads at startup.
4. The `switch-client` jump path is sound — a pane id resolves to its session
   and the client switches; no fix needed. Verified on a healthy server.
