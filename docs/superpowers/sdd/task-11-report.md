# Task 11 report: the prerequisite doctor

BASE commit confirmed: `025866e` (feat/cc-navigator), `git log -1` matches the
brief.

## What I implemented

**`src/ccnav/doctor.py`.** A two-layer tmux-config check plus the surrounding
prerequisite checks, exactly as the brief frames them (act through one channel,
verify through another; believe the verifier):

- `Check` — frozen dataclass `name, ok, detail, fix, required=True`.
- `check_tmux_conf(text)` — pure, offline HINT. A line is fatal iff its first
  word is one of `set / set-option / setw / set-window-option` and none of the
  single-letter flags in its leading `-xyz` groups is `g`, `q` or `s`. Flags are
  tested **per character** (`-gw`, `-qw`, `-sg`, `-ug`, `-as` all bundle). It
  names **every** offending line, not just the first. Comments are skipped.
- `check_tmux_titles(text)` — requires both `set-titles on` and a
  `set-titles-string` of `ccnav:#{session_name}`; skips comments; accepts an
  unquoted value.
- `check_claude_hooks(settings, hook_path)` — the plan's implementation, kept.
- `probe_tmux_conf(conf_path, run=run_command, socket_name=None)` — THE VERDICT.
  Loads the conf with `-f` into a throwaway server on a private
  `-L ccnav_probe_<pid>` socket, `send-keys -l -- 'a b'` into a **detached**
  session, and reads `list-sessions`' exit code. `kill-server` runs in a
  `finally`. If tmux is absent or the conf is missing it returns `ok=False`
  ("I could not check" is never "it is fine").
- `run_all(..., run=run_command)` — composes all eight checks. The `run` seam
  is threaded into both `gnome.eval_available` and `probe_tmux_conf` so the
  tests drive them with a fake runner and never touch real tmux/gdbus.
- `main()` — prints `ok / FAIL / warn`; exits nonzero **only** on a failed
  `required=True` check. The Eval check is `required=False`.

**`bin/cc-navigator-doctor`** (mode 100755). Same `readlink -f "$0"` symlink
resolution as `bin/cc-navigator`; execs `/usr/bin/python3 -m ccnav.doctor` and
passes its exit code straight up.

### Deviations from the plan/brief, each deliberate

1. **`run_all` includes `probe_tmux_conf`.** The plan's `run_all` had no probe;
   the brief says "if the probe says the config is fatal, the doctor fails,"
   which only holds if the probe is in `run_all`. So every real doctor run does
   start a private tmux server. This is the point, and investigation (a) below
   is the proof it is necessary.
2. **`run_all` gained a `run=run_command` parameter** (not in the plan's
   signature) purely as a test seam. Without it, testing `run_all` would call
   real `gdbus` and real `tmux`.
3. **`check_tmux_titles` uses unanchored `.search`, not `^`-anchored `.match`.**
   Forced by the comment-skip requirement — see "What the brief got wrong" #4.

## TDD evidence

`tests/test_doctor.py` was written first. RED: `./run-tests -k TmuxConfFatalTest
-k ProbeTmuxConfTest -k MainExitCodeTest` → `ImportError: cannot import name
'doctor' from 'ccnav'`. Then the implementation → GREEN, 54 doctor tests OK.
Every mutation round below is itself a RED→GREEN cycle (mutate → a named test
fails → revert → green).

The test expectations for `check_tmux_conf` are **not** taken on faith from the
brief's table: every fatal/safe line was reproduced against real tmux 3.0a on a
private `-L` socket before being written into a test (matrix below).

## The measured matrix (real tmux 3.0a, private socket, honest liveness probe)

Liveness = `tmux -L <sock> list-sessions; echo $?` after `send-keys -l -- 'a b'`
into a **detached** `new-session -d` (`pgrep -x tmux` never matches — the
server's comm is `tmux: server`). Every row confirms the brief's table:

| line | after send-keys `'a b'` |
|---|---|
| `set mode-keys vi` / `set-option mode-keys vi` / `setw mode-keys vi` | **DEAD** |
| `set -w mode-keys vi` / `set-window-option mode-keys vi` | **DEAD** |
| `set mode-keys emacs` (default value) | **DEAD** |
| `set clock-mode-style 12` | **DEAD** |
| `set status-bg black` / `set -a status-bg black` | **DEAD** |
| `set -u mode-keys` | **DEAD** |
| `set -g mode-keys vi` / `set -gw` / `setw -g` / `set-window-option -g` | alive |
| `set -q mode-keys vi` / `set -qw mode-keys vi` | alive |
| `set -ug mode-keys` | alive |
| `set -s escape-time 0` / `set -sg escape-time 0` / `set -as terminal-overrides …` | alive |
| `setenv -g FOO bar` / `bind-key …` / `unbind …` / `set-hook -g …` / (empty) | alive |

Every DEAD/alive result matched the brief. The `new=0 pre=0` columns confirm the
brief's two supporting facts: `new-session -d` exits 0 under a fatal config, and
the server is alive *before* the space — the corruption is silent at load and
the death is triggered by the space.

## Mutation evidence

All 11 applied one at a time by an automated harness (mutate → run the named
test → restore), with a final `diff` proving the source was restored
byte-identical and the full suite green again.

| # | Mutation | Result — killed by |
|---|---|---|
| 1 | `check_tmux_conf` uses the plan regex `^\s*set(-option)?\s+mode-keys\b` | **KILLED** — `test_setw_mode_keys`, `test_set_status_bg_a_session_option` (+ `test_set_clock_mode_style`, `test_set_a_status_bg`) |
| 2 | `-q` dropped from the safe set | **KILLED** — `test_set_q_mode_keys`, `test_set_qw_mode_keys` |
| 3 | `-s` dropped from the safe set | **KILLED** — `test_set_s_escape_time`, `test_set_as_terminal_overrides` |
| 4 | whole-token flag membership instead of per-character | **KILLED** — `test_set_gw_mode_keys`, `test_set_qw_mode_keys`, `test_set_as_terminal_overrides` |
| 5 | stops at the first offending line | **KILLED** — `test_names_every_offending_line_not_just_the_first` |
| 6 | `check_tmux_titles` stops skipping comments | **KILLED** — `test_commented_out_titles_do_not_count` |
| 7 | probe omits `kill-server` in the `finally` | **KILLED** — `test_kills_the_server_last`, `test_kills_the_server_even_when_a_step_raises` |
| 8 | probe returns `ok=True` when tmux is absent | **KILLED** — `test_without_tmux_it_says_it_could_not_run_not_that_its_fine` |
| 9 | probe sends `'ab'` (no space) instead of `'a b'` | **KILLED** — `test_probe_sends_a_space_not_just_two_letters` |
| 10 | `main()` counts advisory failures toward the exit code | **KILLED** — `test_only_advisory_failure_exits_zero_and_prints_warn` |
| 11 | `main()` ignores required failures | **KILLED** — `test_required_failure_exits_nonzero` |

**On the brief's prediction (mutations 4 and 9 will survive a careless suite):**
both were killed. Mutation 9 is killed by a test that pulls the payload out of
the recorded `send-keys` argv and asserts `payload == "a b"` and `" " in payload`
— it looks at *what* the probe sends, not merely at whether a scripted
`list-sessions` failure produces `ok=False`. Mutation 4 is killed from **both**
directions the brief named: `-gw` (a per-token check would call it fatal) and
`-ug` (a first-character-only check would call it fatal), because g is the second
character there.

## Investigations

### (a) Bare `setenv FOO bar` and `set-hook after-new-session ""` — MEASURED FATAL

Both are fatal, and so are `setenv -u FOO` and `set-hook -u after-new-session`:

```
DEAD | setenv FOO bar
DEAD | set-hook after-new-session ""
DEAD | setenv -u FOO
DEAD | set-hook -u after-new-session
```

(`setenv -g FOO bar` and `set-hook -g …` remain alive, as the brief had.) So the
corruption is a general property of **any** option-setting command that lacks
`-g/-q/-s`, not just the four `set*` aliases.

**I did NOT widen `check_tmux_conf`'s command list**, and the probe catches
them. Reason: the brief's own thesis is that a regex over tmux's command surface
can never be authoritative. `setenv` and `set-hook` are two commands; there may
be others I have not enumerated. Widening the list turns the hint into a second,
still-incomplete verdict competing with the real one. The right division of
labour is: `check_tmux_conf` names the *common* offender (`setw mode-keys` etc.)
and prints a fix; `probe_tmux_conf` is authoritative and catches everything,
including `setenv`. `test_setenv_and_set_hook_are_out_of_scope_of_the_pure_parse`
pins this contract, and the docstring states it. This finding is in fact the
strongest argument for putting the probe in `run_all`: a doctor with only
`check_tmux_conf` would hand a user with `setenv FOO bar` a clean bill of health.

### (b) An attached client CHANGES EVERYTHING — the brief's danger model is wrong

Every measurement above, and every measurement in the brief, used a **detached**
session. The user's real situation is a session with a client attached (their
terminal). I measured it, 7/7:

```
DEAD  | DETACHED | send-keys -l -- 'a b'   (x3)
ALIVE | ATTACHED | send-keys -l -- 'a b'   (x3)
ALIVE | ATTACHED | send-keys Space / Escape / C-a / Up
```

**With a client attached, the fatal config does not crash.** This directly
contradicts the brief's headline claim that cc_navigator "annihilates every
Claude Code session the first time they type a reply containing a space — i.e.
the first real reply." A session the user is actively working in has their
terminal attached, so the reply-with-space path is survivable there. The real
danger is narrower: **the crash fires only against a session with no attached
client** — a detached-but-alive tmux session, which is a normal state (the user
closed the terminal but left the session running) and one cc_navigator will
happily list and send a reply into.

This does **not** make the fatal config safe or the check pointless — a config
that crashes tmux on any detached send is a genuine landmine, and detached
sessions are routine. But two consequences matter:

1. The brief's "worse than advertised / first real reply" framing is overstated.
   The honest statement is "will kill every **detached** Claude session the
   moment a reply contains a space," which is what the probe's failure message
   and `check_tmux_conf`'s detail now say.
2. It **validates the probe's design**. `probe_tmux_conf` uses `new-session -d`
   (detached) precisely because that is the sensitive case; an attached probe
   would report a fatal config as safe (false negative). So the brief's probe
   recipe is right even though the brief's danger narrative that motivated it is
   wrong. The probe docstring now records why the session is detached.

### (c) The trigger is the Space character — and four other keys

For literal text via `-l`, the trigger is the **Space character (0x20)**,
including the ASCII space *inside* multi-byte text:

```
DEAD  'a b'   DEAD  '한 글'   DEAD  ' '
ALIVE 'ab'    ALIVE 'a\tb'    ALIVE 'a-b' / 'a_b' / 'a.b'
```

But it is **not only Space**. Sending key *names* (no `-l`) under the fatal
config:

```
DEAD  Space   DEAD  Escape   DEAD  C-a   DEAD  C-Space   DEAD  Up
ALIVE Enter   ALIVE Tab      ALIVE BSpace ALIVE a        ALIVE F1
```

So the brief's implied "it's the space" is too narrow — Escape, C-a, C-Space and
the arrow keys also kill a detached fatal server. For the doctor this is moot:
`send-keys -l -- 'a b'` is a reliable trigger, and cc_navigator's reply box
sends arbitrary user text that almost always contains a space. But it means the
blast radius of a fatal config is wider than "replies with spaces."

### (d) The hook is unaffected

Confirmed by code, not guessed: `hook.py` never spawns tmux — its only contact
with tmux is `tmux_socket_from_env`, which reads `$TMUX` as a *string* and splits
it on commas. A corrupted or dead tmux server cannot affect a process that never
calls tmux, so `cc-navigator-hook` writes its state file regardless.

## What the brief got wrong (the brief is a claim too)

1. **The attached-client danger model (investigation (b)).** The brief's central
   scare — every session dies on the first spaced reply — is false for the
   attached case, which is the common one. The crash needs a **detached** target.
2. **"setenv/set-hook … is not this check's business" understates them.** The
   brief lists them as if benign; they are in fact **fatal** when bare
   (investigation (a)). They are correctly left to the probe, but they are not
   harmless.
3. **The trigger is not only Space (investigation (c)).** Escape/C-a/C-Space/Up
   also kill a detached fatal server.
4. **The `check_tmux_titles` comment bug does not exist in the plan.** The brief
   says the plan's `re.M` version lets a fully-commented set-titles conf "pass
   the check." I ran the plan's exact regexes against
   `# set -g set-titles on\n# set -g set-titles-string '…'`:

   ```
   plan ON.search(commented)  -> False
   plan STR.search(commented) -> False   =>  plan check returns ok=False
   ```

   The plan's `^\s*set` anchor already refuses a `#`-prefixed line, so the plan
   would correctly fail a commented conf. Comment-skipping over an anchored regex
   is therefore *vacuous* — which also means mutation 6 would have **survived** a
   naive port of the plan's check. To make the comment-skip both correct and
   testable, `check_tmux_titles` matches with **unanchored `.search`** on
   comment-stripped lines: a `#`-line, if not stripped, *would* then match after
   the `# `, so stripping it is load-bearing and `test_commented_out_titles_do_
   not_count` kills mutation 6. The brief's remedy (skip comments) is kept; its
   diagnosis of the existing code was wrong.

## The doctor against this machine

```
[ok  ] python3                /usr/bin/python3 is the interpreter with PyGObject
[ok  ] tmux                   tmux addresses every session by pane
[ok  ] xprop                  xprop independently verifies that focus actually moved
[ok  ] tmux.conf mode-keys    no `set`-family line is missing -g/-q/-s
[ok  ] tmux.conf live probe   the config survives a space sent to a session
[FAIL] tmux.conf set-titles   the outer window title is cc_navigator's only address to jump to
       fix: add to ~/.tmux.conf:
    set -g set-titles on
    set -g set-titles-string 'ccnav:#{session_name}'
[FAIL] claude hooks           sessions started without the hook never appear in the list
       fix: point the Notification, Stop, PreToolUse, SessionStart and UserPromptSubmit hooks at /home/kodogyu/playground/cc_navigator/bin/cc-navigator-hook in ~/.claude/settings.json
[ok  ] gnome shell eval       required only for jump; blocked from GNOME 41 onward
exit=1
```

Exactly the brief's prediction: `mode-keys` passes (this machine has
`setw -g mode-keys vi`), the live probe passes (the config is safe), `set-titles`
FAILS (**the lines are absent — the ledger claims they were added; they were
not**), `claude hooks` FAILS (this machine's `settings.json` has no `hooks` key
at all), `gnome shell eval` passes (GNOME 3.36.9). `exit=1`.

## Verification

- Full suite: **217 tests, OK** (163 existing + 54 new). Output pristine — zero
  matches for `Gtk-CRITICAL|Gtk-WARNING|Gdk-CRITICAL|GLib-GObject|PyGIWarning|
  DeprecationWarning|ResourceWarning|Traceback` and no stray prints (`main()`'s
  output is captured with `redirect_stdout` in the tests that exercise it).
- `env -u DISPLAY ./run-tests`: **217, OK (skipped=11)**, same clean scan.
- **No real tmux/gdbus in the test suite.** Every live path is driven by an
  injected fake `Runner`. `probe`'s socket-name test asserts the argv uses
  `-L ccnav_probe_<pid>`, never `default`.
- **No leaked servers.** After the whole task, `/tmp/tmux-1000/` is empty and
  `ps -eo comm | grep tmux` (checking the real `tmux: server` comm) shows
  nothing. All investigation sockets and the doctor's probe socket were removed.
- `git ls-files -s bin/cc-navigator-doctor` records mode **100755**.

### One honest cleanup note

A `tmux kill-server` in tmux 3.0a leaves the 0-byte socket file behind even on a
clean shutdown (verified: `new-session -d` then `kill-server`, socket file still
present, server dead). So each real doctor run leaves one dead, pid-scoped,
0-byte socket file at `/tmp/tmux-<uid>/ccnav_probe_<pid>`. It is a stale file,
not a server (no leak), and matches the project's existing dead-spike-socket
leftovers noted in HANDOFF. I did not add filesystem cleanup to the probe: the
brief's recipe does not, reconstructing the socket path depends on
`TMUX_TMPDIR`, and a dead 0-byte file is harmless. Flagging it rather than
hiding it.

## Self-review

- **TDD:** tests first, RED verified, then GREEN.
- **Mutations:** 11/11 killed, named tests, source restored byte-identical,
  suite green after.
- **Investigations:** all four answered with measurements; three of them
  contradict the brief and are reported loudly above and in the return value.
- **YAGNI:** no `--json`, no `--fix`, no config file, no logging. `~/.tmux.conf`
  and `~/.claude/settings.json` untouched.
- **Safety:** no window shown/activated/raised; `bin/cc-navigator` never run;
  every tmux server used a private `-L ccnav_*_<pid>` socket and was killed;
  the default tmux socket was never touched.

## Files

- `src/ccnav/doctor.py` — new
- `bin/cc-navigator-doctor` — new, executable (100755)
- `tests/test_doctor.py` — new (54 tests)
- `docs/superpowers/sdd/task-11-report.md` — this file

---

## Post-review revision (controller, 2026-07-11)

The Task 11 workflow ran 11 mutation testers and 3 adversarial reviewers against the
committed doctor (66a6f24). All 11 mutations were independently confirmed KILLED,
including the two the brief predicted might survive (4 and 9). The reviewers, however,
found real gaps the implementer's self-report claimed were covered. Verified each,
then fixed the load-bearing ones:

**Fixed**

- **check_tmux_conf was a required gate, contradicting its own docstring.** The
  module says the parse "is a hint, not a verdict; probe_tmux_conf is THE VERDICT,"
  yet run_all wired both `required=True`. So a parser false positive — e.g. a
  line-continuation `set \` + `-g ...`, which reads as fatal unjoined but actually
  works — would veto a passing probe and fail the doctor on a working config
  (RULE reviewer, reproduced). Made check_tmux_conf advisory (`required=False`). The
  probe remains the required gate, so no fatal config slips through: when tmux is
  present the probe catches it; when tmux is absent both the tmux check and the probe
  fail required. New test: `test_tmux_conf_parse_is_advisory_not_the_gate`.

- **Two survived mutations the implementer never tested.** No named test observed
  that the probe loads the user's config with `-f conf_path`, or that its session is
  detached (`-d`). Dropping `-f` boots tmux's empty default config so every fatal
  config passes; dropping `-d` attaches a client, and an attached session never
  crashes (investigation (b)), so every fatal config passes. Both survived the
  committed suite (TEST reviewer, reproduced). Added
  `test_probe_loads_the_users_config_with_dash_f` and `test_probe_session_is_detached`,
  and verified each fails under its mutation.

- **`socket_name="default"` footgun.** `tmux -L default` targets the user's real
  server; the probe's kill-server would wipe every live Claude session. Not reachable
  today (no caller passes socket_name), but the project's prime directive is never to
  touch the default socket. Added a guard that raises before any tmux runs, plus
  `test_refuses_the_default_socket` (SAFETY reviewer).

- **Malformed ~/.claude/settings.json guard was untested.** Added
  `test_malformed_settings_json_does_not_crash`.

- **Docstring blind spots.** check_tmux_conf now states its false negatives
  (abbreviations `set-o`/`set-w`/`set-win`, and a fatal set nested in `if-shell`) and
  false positives (line continuations) — all caught by the probe.

**Accepted, not fixed**

- **kill-server leaves a 0-byte socket file** under /tmp/tmux-<uid>/. Confirmed tmux
  3.0a behaviour, not a leak: no server and no process survive (SAFETY reviewer
  approved). Reconstructing tmux's socket-path logic to unlink it is more dangerous
  than a harmless empty file, so it stays.

- **check_tmux_conf false negatives on abbreviations / if-shell.** The probe is
  authoritative and catches them; widening the regex to chase tmux's whole command
  surface is the exact trap the two-layer design avoids. Documented, not chased.

Suite after revision: 222 tests, green (163 pre-existing + 59 doctor). Headless green
(skipped=11). Doctor output unchanged on this machine. No live tmux server leaked.
