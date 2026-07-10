# Task 13 brief: two silent successes on the critical path

Not in the plan. This task exists because two experiments during Task 11's
preparation found defects that the whole suite, all 163 tests of it, is blind to.

BASE commit: whatever Task 11 committed. Confirm with `git log -1`.

## Global Constraints (bind every task)

- **Interpreter is `/usr/bin/python3` (3.8.10).** Never bare `python3` — that is
  Anaconda's 3.11 and has no `gi`.
- **Zero third-party dependencies.** Stdlib + system `gi` only.
- Python 3.8: no `match`, no `X | Y` runtime annotations. `src/` modules start with
  `from __future__ import annotations`. Test files do not.
- **Never trust an API's self-report.**
- Test command: `./run-tests` (sets `PYTHONDONTWRITEBYTECODE=1`; leave it).

## SAFETY

- `DISPLAY=:1` is live. Never run `bin/cc-navigator`; never `show()`, `show_all()`,
  `present()` or `Gtk.main()`. Never activate, raise or close a window.
- Never touch the default tmux socket. Private sockets only (`-L ccnav_<tag>_$$`),
  killed afterwards. Socket paths over ~108 bytes fail (`File name too long`).
- The honest liveness probe is `tmux -L <sock> list-sessions; echo $?`.
  `pgrep -x tmux` does not match the server — its `comm` is `tmux: server`.
- Do not edit `~/.tmux.conf` or `~/.claude/settings.json`.

---

## Defect F2 — `send_text` reports success while the server is dead

`src/ccnav/tmuxctl.py`:

```python
def send_text(socket, pane, text, run=run_command) -> None:
    for argv in send_text_argvs(socket, pane, text):
        run(argv)          # <- exit codes discarded
```

Measured, against a real tmux whose config carries a fatal `set` line (see
`task-11-brief.md` for the rule):

```
  send_text returned normally
  server after send_text: DEAD
```

`app.send`'s worker only catches exceptions, so `status` stays `""` and the status
bar says nothing. The user's reply was never delivered, their tmux server has
segfaulted, and every Claude Code session inside it is gone. cc_navigator says
nothing at all.

This is not a hypothetical. It is the project's own signature failure mode —
`gdbus` exits 0 while `Eval` returns `(false, ...)` — reproduced in cc_navigator's
own reply path, which is the one feature the user asked for by name.

### What to build

`send_text` must report whether the text was delivered **and** whether it was
submitted. Two tmux calls, two ways to fail:

- the literal `send-keys -l -- <text>` fails → nothing was typed
- the literal succeeds but `send-keys Enter` fails → the text is sitting in the
  session's input line, unsubmitted, and the user will never know

Give `send_text` a return value that distinguishes those, and make `app.send` show a
distinct Korean status for each. Keep the existing "unexpected exception" path.
Design the return type yourself — a `bool` loses the second case; do not lose it.

`select_pane` keeps ignoring exit codes: `switch-client` legitimately fails when no
client is attached (Task 6 measured this). Do not "fix" it. Say in your report why
the two are different.

Consider, and answer in the report: when `send-keys -l` fails, should we still run
`send-keys Enter`? Argue from what the failures actually are, not from symmetry.

---

## Defect F3 — a wedged tmux deletes every live session's state file

`src/ccnav/app.py`:

```python
sessions = {socket: sessions_for(socket) for socket in sockets}
titles   = {socket: titles_for(socket)   for socket in sockets}
prune(state_dir, model.live_pane_keys(sessions))
```

`tmuxctl._query` returns `{}` when tmux exits nonzero. `proc.run_command` returns
`(124, "")` on timeout. So a tmux that is merely **slow** — not dead — produces an
empty pane set, `live_pane_keys` is empty, and `statestore.prune` deletes the state
file of every live session on that socket.

Reproduced with `SIGSTOP` on a real tmux server, one poll at a 0.5 s timeout:

```
  healthy poll:  rows=1  files=['s1.json']
  wedged  poll:  rows=0  files=[]
  (SIGCONT)      tmux still serving: %0=proj
```

The server was never dead. And the row never comes back: a session **waiting for
input** fires no further hooks by definition, so nothing rewrites its state file.
The panel drops the one row it exists to show, looks perfectly healthy, and the user
never learns their session is waiting.

`collect_rows` reads an empty result as *"every session vanished"*. It is really
*"I could not find out."* Absence of evidence, read as evidence of absence.

Note that the ledger's Task 5 entry justifies `_query`'s `code != 0` guard as "what
stops a dead tmux socket from reading as 'every session vanished'". That reasoning
is backwards — the guard is what *produces* the empty dict. The guard is still
right (a failed process's stdout is not evidence, and Task 7's test pins that), but
its stated purpose is wrong, and the pruning hazard it was supposed to prevent is
live. Correct the ledger.

### What to build

`prune` must only judge a socket whose pane list was actually **observed**.

- Make the query's success visible. `tmuxctl` currently hides it. Add a function
  that returns both the ok flag and the mapping — e.g.
  `sessions_by_pane_result(socket, run) -> Tuple[bool, Dict[str, str]]` — and keep
  `sessions_by_pane` as the thin wrapper the rest of the code already uses, or
  restructure more boldly if you can justify it. Do not make `build_rows` learn
  about failure; it is pure and should stay that way.
- `collect_rows` computes the set of sockets whose query succeeded, and passes it to
  `prune`.
- `prune` gains a parameter naming the sockets it is allowed to judge. A record
  whose socket was **not** observed this tick is left alone.
- **The age check stays unconditional.** A 24-hour-old file for a socket that has not
  answered in a day must still be reaped, or an unreachable socket leaks files
  forever. Junk (unparseable) files are still deleted unconditionally too.

Consequence you must handle, not paper over: when a whole tmux server exits, its
socket stops answering, so its state files are no longer pruned on liveness — they
are reaped by age instead. The row disappears from the UI immediately either way
(`build_rows` requires the pane to be live). The plan's Task 12 integration test
`test_row_disappears_when_the_pane_dies` asserts `state_dir` is empty right after
`kill-server`; **that assertion pins the bug**, and Task 12 must be written against
the corrected behaviour. Say so explicitly in your report so the Task 12 implementer
does not "fix" your fix.

Ask yourself, and answer: is there any case where a socket answers `exit 0` with an
empty pane list? If there is, the whole scheme rests on sand. Measure it.

---

## Mutation testing — mandatory

Break the implementation, run `./run-tests`, record which **named** test fails.

1. `send_text` discards the literal send's exit code again.
2. `send_text` discards the `Enter` send's exit code (the "typed but not submitted"
   case) and reports overall success.
3. `app.send` maps a delivery failure to an empty status string.
4. `collect_rows` passes every socket to `prune`, not just the observed ones.
5. `collect_rows` passes only the *failed* sockets to `prune` (inverted set).
6. `prune` ignores its new allowed-sockets parameter.
7. `prune` skips the age check for unobserved sockets (so nothing is ever reaped).
8. `prune` stops deleting unparseable junk files.
9. `tmuxctl`'s new result function returns `ok=True` on a nonzero exit.

**Prediction, to be checked and not agreed with:** I expect 2 and 7 to survive a
careless suite. Mutation 2 because a test that only asserts "failure is reported"
will be satisfied by the first call's code alone; mutation 7 because the age path
and the liveness path are easy to test separately and never together. If you cannot
kill one, that is a finding — report it, do not contort a test around it.

## Also verify, and report

**a) The end-to-end repro.** After your fix, redo both experiments with real tmux on
a private socket and paste the output:
   - `SIGSTOP` the server, poll once with a short timeout, show the state file
     survives, `SIGCONT`, poll again, show the row returns.
   - Under a fatal config, call `send_text` and show that it now reports failure.

**b) Does the poll thread survive?** `_poll_loop` catches `Exception`. Confirm your
new code paths cannot raise past it, and that a failed query now leaves the status
bar saying something true rather than silently emptying the list.

**c) The status bar.** `_apply_poll_error` posts on every tick while a failure flaps.
With F3 fixed, a wedged tmux gives an empty row list but no exception. Does the user
see *anything*? If the answer is "the list just goes empty and the status bar is
blank", that is F3 wearing a different hat. Fix it or argue why not.

## Calibration: four confident, plausible, wrong claims from this project's history

1. **"tmux 3.0a segfaults on `send-keys -l`."** (Task 6.) Reported, then retracted as
   a misdiagnosis. It was **true**; only the config precondition was missing. The
   retraction is now the error in the record.
2. **"`os.replace` vs `copyfile` is unobservable without a flaky concurrency
   harness."** (Task 3.) `stat().st_ino` distinguishes them deterministically.
   "No test can observe X" is a claim to falsify, not to accept.
3. **"`pgrep -f 'sleep 47'` proves no orphan survived."** (Task 10.) `-f` matches the
   *shell* whose command line contains the string.
4. **"`pgrep -x tmux` shows the server is dead."** (Controller, today.) The server's
   `comm` is `tmux: server`. And an identical failure across every input in a matrix
   meant my socket path was too long, not that every input failed.

Distrusting an API's self-report has to extend to distrusting your own instrument.

## Then

- Full suite green, plus your new tests. `env -u DISPLAY ./run-tests` green.
- Output pristine; no leaked tmux servers or sockets.
- Commit `src/ccnav/tmuxctl.py`, `src/ccnav/app.py`, `src/ccnav/statestore.py`,
  the touched tests, and `docs/superpowers/sdd/task-13-report.md`.
- Write the report in the shape of `task-10-report.md`: mutation table, the two
  reproductions, and everything this brief got wrong. **This brief is a claim, too.**

## Notes and traps

- Work from `/home/kodogyu/playground/cc_navigator`. The plan says
  `/data/playground/cc_navigator`; that path does not exist on this machine.
- `_poll_loop` must keep looping through anything you add.
- Do not add logging, a config file, or a retry/backoff policy. If a query fails, the
  next tick is one second away.
- `model.build_rows` and `model.live_pane_keys` are pure. Keep them pure.
