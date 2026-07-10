# cc_navigator SDD progress

Plan: docs/superpowers/plans/2026-07-10-cc-navigator.md
Branch: feat/cc-navigator

NOTE: history was rewritten on 2026-07-10 to strip real project identifiers.
Every SHA below is post-rewrite. The old SHAs (c189bf3, 8df7c32, d610319,
9f9ba72, b82cfd9, 6aced61) no longer exist. A pre-rewrite bundle lives at
$CLAUDE_JOB_DIR/tmp/pre-rewrite.bundle for the life of this job only.

Task 1 BASE: f5e9be0 (was c189bf3)

Task 1: complete (f5e9be0..a0f8b06). Review: Approved.
  - Should-Fix closed by a0f8b06: ensure_state_dir had no tests; added four,
    mutation-verified (removing the chmod fails them).
  - Minor left open by design: mkdir-then-chmod is not atomic, and the /tmp
    fallback would follow a pre-existing symlink. Single-user box, plan-specified
    sequence. Revisit only if cc_navigator ever runs on a shared host.

Task 2: complete (138a51f). Review: Approved, no Critical/Important findings.
  - Reviewer independently killed all five required mutations plus a sixth of
    its own (`.get(k, default)` in place of `.get(k) or default`), which the
    implementer's extra test `test_notification_with_empty_type_still_waits`
    catches. That test earns its place.
  - Minor, accepted: a non-string `notification_type` stringifies oddly
    (`["x"]` -> `"['x']"`). Never raises; `reason` is opaque display text.
  - Minor, accepted: PreToolUse's early `return None` is redundant with the
    tail fallback. Kept — it makes the branch self-contained.

Task 3: complete (7bea300 impl, 7d9efbc test fix). Review: Needs Revision -> fixed.
  - Implementer reported mutation 1 (os.replace -> copyfile) as unobservable
    without a flaky concurrency harness. False. `stat().st_ino` distinguishes
    them deterministically: os.replace renames a fresh temp file over the target
    (new inode); copyfile truncates in place (same inode). Reviewer proved it;
    controller re-proved it independently after the fix.
  - 7d9efbc adds two tests, no src change:
      test_write_replaces_the_file_rather_than_mutating_it  -- kills mutation 1
      test_reader_during_write_never_sees_a_partial_file    -- spies on json.dump
        to observe the target mid-write; asserts observed == [None, record(100)],
        which pins both the property and the spy's own execution.
  - Closes a spec/plan gap: design spec section 9 asks for the concurrent-reader
    atomicity test; the plan's Task 3 omitted it. Plan bug, now covered.
  - Mutations: 6/6 killed.

Task 4: complete (6599c86 impl, 5d50fc6 symlink fix). Review: Approved.
  - Mutations 6/6 killed, independently reverified. The exit-0 invariant survived
    ~16 attacks (garbage stdin, 5 MB input, read-only state dir, XDG pointing at a
    file, NUL/newline/5000-char session ids, TMUX=",", empty TMUX_PANE, HOME unset,
    PYTHONWARNINGS=error, deleted CWD, symlink).
  - Important, FIXED by 5d50fc6: invoked through a symlink, `dirname "$0"` gave the
    link's directory, PYTHONPATH missed src/, the import failed, and 2>/dev/null ate
    the traceback. Exit 0, no output, no state -- a failure with no symptom. Now
    `readlink -f`. tests/test_hook_shim.py runs the shim as a subprocess; reverting
    the fix fails exactly one test.
  - Important, ACCEPTED (unfixable here): from a deleted CWD, /bin/sh itself prints
    `getcwd() failed` to stderr before the script body runs. Reproduced with a bare
    `/bin/sh -c "echo hi"`, so it is not ours. Exit code stays 0.
  - Blocking question settled empirically: main() does hang if stdin never reaches
    EOF (proved with a FIFO held open -> exit 124). Not a real risk -- every
    subprocess spawner closes its write end, and reading stdin to EOF is the
    documented contract for all Claude Code hooks. No timeout added.

Task 5: complete (58d32ce impl, dd3fb05 test fix). Mutations 5/5 after fix.
  - Implementer honestly reported mutation 3 (drop `_query`'s `code != 0` guard)
    as a SURVIVOR rather than hiding it. Controller reproduced. The plan's own
    test fed the fake `(1, "")`, and `parse_kv_lines("")` returns {} anyway, so
    the guard had no test at all. That guard is what stops a dead tmux socket
    from reading as "every session vanished" -- which in Task 8 would prune live
    state files. Fixed by feeding `(1, "%0=zombie\n")`.
  - Mutation 5 (swap format strings) was killed asymmetrically; the titles test
    now asserts its argv too.
  - Mutation 4 (drop `-a`) is a genuine kill: test_list_argv_uses_explicit_socket
    asserts the whole argv list. Left alone.

909e8a2: run-tests now sets PYTHONDONTWRITEBYTECODE=1.
  CPython invalidates .pyc on (mtime, size). A size-preserving mutation restored
  within the same second runs stale bytecode and the suite reports OK for code
  that is not on disk. Reproduced controller-side with an exact-size edit.
  Direction matters: stale bytecode makes a mutation look like a SURVIVOR, never
  like a false KILL, so no earlier "killed" verdict is invalidated -- the hazard
  only ever understated test strength. Closed anyway; mutation testing is this
  project's only check that its tests test anything, and it must not be able to
  lie.

Suite: 60 tests, green, output pristine, no __pycache__.

Task 6: complete (cb114a4). Mutations 6/6.
  - The plan had no test for `send_text` at all, only for `send_text_argvs`.
    Nothing would have noticed if it ran the first argv and never pressed Enter,
    leaving the user's text unsubmitted. The authorised extra test is the only
    thing that kills that mutation.
  - Byte-exactness PASS, verified through the real send_text/run_command against a
    real tmux on a private socket with `-f /dev/null`:
      HOSTILE  -> identical      Enter -> E n t e r \n (5 chars, not a keypress)
      -n --flag -> identical
    So `-l` and `--` do exactly what the docstring claims.
  - switch-client -t <pane-id> RESOLVES to the containing session and switches the
    attached client (sessA->sessB, and a bogus %999 fails distinctly with "can't
    find pane"). The plan's "best effort" comment is accurate: the only failure is
    the harmless no-client-attached case. Jump path is sound.
  - Implementer first reported BLOCKED, claiming tmux 3.0a segfaults on
    `send-keys -l` with a space. Misdiagnosis, retracted in the report. See below.

PREREQUISITE, FIXED 2026-07-10: `~/.tmux.conf` line 1 was `set mode-keys vi`.
  `mode-keys` is a window option; bare `set` makes tmux 3.0a abort the server on
  the first external command. `-S` isolates the socket but NOT the config file, so
  every real-tmux experiment in this repo must pass `-f /dev/null` until the user
  fixes it (`setw -g mode-keys vi`).
  Consequence: with the config as it stands, cc_navigator's first `list-panes` --
  Task 5's read-only query -- would kill the user's tmux server and every Claude
  session in it. Task 11's doctor is the detector. Asked the user; awaiting reply.
  No tmux server is running today, so nothing is at risk right now.

Method note: an identical crash signature across different inputs is evidence of a
common cause upstream of the inputs, not of several input-triggered bugs.

CARRY INTO TASK 8: tmux 3.0a reports a plain shell's #{pane_title} as the
HOSTNAME, not "". The plan assumed empty. Panes not running Claude Code must be
filtered out by the state-file join (no state file -> no row), not by an empty
title check.

Task 7: complete (eddd59f impl, 50bc6c2 test fix). Mutations 7/7 after fix.
  - Controller predicted mutation 5 (delete eval_js's `if code != 0` guard) would
    survive; implementer checked rather than assuming, and confirmed it. The only
    nonzero-exit test fed `(127, "")`, and parse_eval_result("") is already False,
    so nothing observed the guard. 50bc6c2 pins the premise -- a failed process's
    stdout is not evidence -- by pairing a nonzero exit with success-shaped stdout.
  - Hostile focused-window titles containing `=` or `"` cannot raise from
    active_window_title; worst case is a harmless mismatch. No GTK-main-loop hazard.
  - Read-only desktop probes only. No window was activated, raised, or closed.
    Focus-stealing verification stays in the Task 12 spike, run by hand.

Task 8: complete (74974ef impl, 444a85d fixes). Mutations 8/8 after fix.
  - Controller predicted mutations 2 and 6 would survive the plan's tests; both did.
    Neither is unobservable -- the plan's tests just never steered into them:
      drop the empty socket/pane guard -> a garbage record ACQUIRES a window
        address (window_title becomes "ccnav:ghost-session"), which Task 7 would
        then match with === and activate. A corrupted state file must never be
        able to name a window. Pinned by
        test_a_record_without_a_socket_cannot_acquire_a_window_address.
      titles.get(pane, pane) -> a present-but-empty tmux title leaves the UI's
        primary line blank. Pinned by test_present_but_empty_title_falls_back_...
    test_records_without_socket_or_pane_are_dropped passes for the WRONG reason
    (a different guard catches it first). Kept, with a comment so nobody deletes
    one as a duplicate of the other.
  - REAL DEFECT found by the Step 7 investigation and fixed in 444a85d: a garbage
    `updated_at` raised out of build_rows, which runs on a 1 s GTK timer. An
    exception there does not skip one tick -- it escapes the callback and the model
    never updates again. cc_navigator would sit on screen showing stale rows
    forever: exactly the failure mode this project exists to prevent. `_as_int`
    now mirrors statestore.prune's policy (unparseable -> 0 -> sorts oldest ->
    pruned on age). Verified: 'abc'/None/[]/{} -> 0, '100' -> 100, 1.9 -> 1.
  - A non-Claude pane never produces a row: the join is records-driven, and no hook
    ran means no state file. The pane-id title fallback survives only as a guard
    against a race between the two tmux queries.

Task 9: complete (5a8abaa impl, d4d8453 fixes). Mutations 11/11. Suite 129.
  - The plan's UI could not do the thing the user asked for by name. set_rows
    destroys and rebuilds every child, and Task 10 calls it on a 1 s timer, so the
    Gtk.Entry a user is typing a reply into is destroyed roughly every second --
    and not only when *their* session changes; any other row changing rebuilds the
    whole list. Reproduced: text lost, revealer collapsed, selection cleared.
    Fixed by (a) short-circuiting on an unchanged display signature (updated_at
    excluded on purpose) and (b) capturing and restoring the selected session's id,
    entry text and focus across a rebuild that does happen.
  - set_eval_available now reaches buttons that already exist. It read
    _eval_available only at construction, so a late set_eval_available(False) left
    every jump button live: click, nothing happens, no explanation. The project's
    signature failure mode, in our own UI.
  - primary_line() falls back to tmux_session when the title is empty, a pane id,
    or the hostname. Per the Task 5 finding a fresh pane's title IS the hostname,
    which is identical across sessions and defeats the panel's only job.
  - Two Python warnings (PyGIWarning, Gdk.Screen.get_width DeprecationWarning) were
    in every run. Fixed: gi.require_version for Gdk/Pango, guarded monitor geometry.
    A warning that prints every run teaches everyone to ignore warnings.
  - Known gap, accepted: grab_focus() restoration is untestable headlessly -- focus
    needs a shown toplevel, which is forbidden here. It runs only in Task 10's real
    window. Check it by hand when the app first runs.

Task 10: complete (ba6613a wiring, 5fd852a poll guard, ec0b008 bounded probe).
  The blocking budget was spent, not deferred:
  - proc.run_command now takes timeout= (DEFAULT_TIMEOUT 5.0) and returns (124, "")
    on TimeoutExpired. Every caller already treats nonzero as failure, so it
    composes for free. Verified: returns in 0.20 s and leaves no orphan child.
  - No tmux, gdbus or xprop call runs on the GTK main thread. A daemon poll thread
    owns collect_rows; jump and send each use a short-lived daemon thread; results
    cross back only through GLib.idle_add, and every idle callback returns False.
    Gio.FileMonitor's callback only sets the wake event.
  - perform_jump/jump_status extracted as pure functions, so the ordering rule
    (select_pane BEFORE activate, or the user lands on the wrong pane) is pinned by
    a test with no GTK, threads or subprocesses.
  - REAL DEFECT, fixed in 5fd852a: _poll_loop had no exception guard. One raise
    from collect_rows (statestore.prune's unlink was outside its try, so a single
    PermissionError suffices) killed the thread for the life of the process. The
    window then sat always-on-top showing rows frozen at the failure, looking
    perfectly healthy, with the only trace a stderr stack dump nobody watches.
    Now guarded, the failure is surfaced in the status bar, and prune tolerates a
    file it cannot delete.
  - ec0b008: the startup Eval probe was bounded to 1.0 s (measured 21 ms in
    reality) and fails to the safe side. Chosen over moving it to the poll thread,
    which would have added an intermediate UI state to save a now-capped delay.
  - bin/cc-navigator got the same readlink -f fix as the hook, but keeps exec and
    does not swallow stderr: if the app cannot start, the user must see why.

Suite: 163 tests, green, headless green (11 skipped), no warnings of any kind.

Correction worth keeping: `pgrep -f 'sleep 47'` matches the SHELL whose command
line contains that string, so it "finds" an orphan that does not exist. `pgrep -x
sleep` is the honest count. Distrusting an API's self-report has to extend to
distrusting your own instrument.

Standing lesson for briefs: "no test can observe X" is a claim to be falsified,
not accepted. Ask the implementer to spend five minutes trying before writing it.


## 2026-07-10, after Task 10: the environment was repaired and the chain proved

~/.tmux.conf line 1 fixed (`setw -g mode-keys vi`), verified against a real server:
it now survives `list-panes` and `send-keys -l`, and mouse / default-terminal /
mode-keys / the six root key bindings all still apply. `set-titles on` and
`set-titles-string 'ccnav:#{session_name}'` added.

First end-to-end run, private socket, no GNOME: the real hook shim consumed a real
Notification payload, wrote its state file, and collect_rows joined it against real
tmux output to produce one waiting row addressed `ccnav:myproject`.

Two facts confirmed in reality that the unit tests had only asserted in isolation:
  - set-titles-string expands to exactly Row.window_title. The address the model
    computes is the title the window actually carries.
  - A fresh pane's #{pane_title} really is the HOSTNAME, and ui.primary_line's
    fallback really does replace it with the tmux session name. The Task 5 finding
    and the Task 9 fix met each other and held.

Still unproved by anything but reasoning: GNOME activation, and grab_focus().
Both need a window on a screen, which no agent here is allowed to create.

Environment note for a new machine: `gdbus` may resolve to Anaconda's copy. Both
/usr/bin/gdbus and ~/anaconda3/bin/gdbus answer Eval("1+1") correctly here.
`wmctrl` and `xdotool` are NOT installed -- Task 9's report claims a wmctrl check
that therefore cannot have run. Use `xwininfo -root -tree` instead.
