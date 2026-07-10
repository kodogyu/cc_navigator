# Task 11 brief: the prerequisite doctor

BASE commit: `025866e` (feat/cc-navigator) — confirm with `git log -1`.
Plan: `docs/superpowers/plans/2026-07-10-cc-navigator.md`, section `### Task 11`
(lines 2113-2422).

**This brief deliberately deviates from the plan.** The plan's `check_tmux_conf`
encodes a rule that is *factually wrong*, and I have the kernel log to prove it.
Where they conflict, this brief wins. Everything else in the plan still stands.

## Global Constraints (bind every task)

- **Interpreter is `/usr/bin/python3` (3.8.10).** Never invoke bare `python3` —
  `which python3` is Anaconda's 3.11 and has no `gi`.
- **Zero third-party dependencies.** Stdlib + system `gi` only.
- Python 3.8: no `match`, no `X | Y` runtime annotations. `src/` modules start
  with `from __future__ import annotations`. Test files do not.
- **Never trust an API's self-report.** Act through one channel, verify through
  another.
- Test command: `./run-tests` (sets `PYTHONDONTWRITEBYTECODE=1`; leave it).
- Suite is 163 tests, green, output pristine: no `Gtk-CRITICAL`, `PyGIWarning`,
  `DeprecationWarning`, `ResourceWarning`, no stray prints.

## SAFETY — read twice

- `DISPLAY=:1` is live. **Never run `bin/cc-navigator`**, never call `show_all()`,
  `show()`, `present()` or `Gtk.main()`. No window may appear on the user's screen.
- **Never activate, raise or close a window.** No `Main.activateWindow`. Read-only
  desktop probes only (`xprop`, `gdbus ... Eval("1+1")`).
- **Never touch the default tmux socket.** Every tmux server you start must use
  `-L ccnav_<something>_$$` or `-S <private path>`, and must be killed afterwards.
  A socket path longer than ~108 bytes fails with `File name too long` — do not put
  test sockets under the long scratchpad path (this cost me an experiment; see
  Calibration 4).
- Do **not** edit `~/.tmux.conf` or `~/.claude/settings.json`. The doctor reports;
  the user fixes.

---

## The plan's rule is wrong. Here is the measured truth.

The plan says:

> The `set mode-keys vi` check is the important one: without `-g`, the tmux server
> dies the moment cc_navigator sends it a command.

`HANDOFF.md` says the same, and adds that "cc_navigator's very first `list-panes`
kills the server". `implementation-log.md` says "bare `set` makes tmux 3.0a abort
the server on the first external command", and records Task 6's implementer as
having *misdiagnosed* a segfault.

I re-ran all of it against tmux 3.0a on this machine. Every one of those sentences
is false, and Task 6's implementer was right.

### What actually happens

Two conditions must hold **at the same time**:

1. `~/.tmux.conf` contains a `set` / `set-option` / `setw` / `set-window-option`
   line whose flags include **none of `-g`, `-q`, `-s`**. This corrupts the server
   *at config-load time*, silently: `new-session -d` exits 0 and prints nothing.
2. Something later sends a **Space** through `send-keys` — either
   `send-keys -l -- 'a b'` or the key name `Space`.

Then the server dies. Not gracefully:

```
tmux: server[11634]: segfault at 709 ip 000055f18ed29db1 sp 00007fff3d98a510
                     error 4 in tmux[55f18ecfd000+67000]
```

`dmesg` says segfault, so **`segfault` was the correct word all along**. The
retraction in the ledger over-corrected: the implementer had the trigger right
(`send-keys -l` with a space) and only lacked the precondition (the config line).

### The matrix (all measured, tmux 3.0a, private socket, payload `'a b'`)

| `~/.tmux.conf` line | window `mode-keys` after load | server after `send-keys -l -- 'a b'` |
|---|---|---|
| `set mode-keys vi` | emacs (never applied!) | **DEAD** |
| `set-option mode-keys vi` | emacs | **DEAD** |
| `setw mode-keys vi` | emacs | **DEAD** |
| `set -w mode-keys vi` | emacs | **DEAD** |
| `set-window-option mode-keys vi` | emacs | **DEAD** |
| `set mode-keys emacs` | emacs | **DEAD** |
| `set clock-mode-style 12` | — | **DEAD** |
| `set status-bg black` | — | **DEAD** |
| `set -a status-bg black` | — | **DEAD** |
| `set -u mode-keys` | — | **DEAD** |
| `set -g mode-keys vi` | vi | alive |
| `set -gw mode-keys vi` | vi | alive |
| `setw -g mode-keys vi` | vi | alive |
| `set-window-option -g mode-keys vi` | vi | alive |
| `set -q mode-keys vi` | emacs | alive |
| `set -qw mode-keys vi` | emacs | alive |
| `set -ug mode-keys` | — | alive |
| `set -s escape-time 0` | — | alive |
| `set -sg escape-time 0` | — | alive |
| `set -as terminal-overrides ,xterm-256color:RGB` | — | alive |
| `setenv -g FOO bar` | — | alive |
| `bind-key r source-file ~/.tmux.conf` | — | alive |
| `unbind C-b` | — | alive |
| `set-hook -g after-new-session ""` | — | alive |
| (empty conf) | emacs | alive |

Read the table twice. Four things fall out of it, and each one breaks the plan:

- **It is not about `mode-keys`.** `set clock-mode-style 12` and
  `set status-bg black` are equally fatal. `status-bg` is a *session* option, so it
  is not about window options either.
- **It is not about the option's value.** `set mode-keys emacs` — the default value
  — is fatal.
- **`-q` and `-s` are as protective as `-g`.** `-q` suppresses the config-load
  error; `-s` addresses the server table, which needs no target.
- **The plan's regex is wrong in both directions it can be.** `^\s*set(-option)?\s+
  mode-keys\b` catches `set mode-keys vi` and `set-option mode-keys vi` — and
  **misses `setw mode-keys vi`, `set -w mode-keys vi`, `set status-bg black`, and
  every other bare `set`**. It would hand a user with a fatal config a clean bill of
  health.

### Two more measured facts you will need

- **The whole read path is safe.** Under the fatal config, `list-panes -a`,
  `display -p`, `select-pane`, `select-window` and `switch-client` all succeed and
  leave the server alive. So `HANDOFF.md`'s "the very first `list-panes` kills the
  server" is false, and the danger is *worse* than advertised: cc_navigator lists
  sessions perfectly, jumps perfectly, and then annihilates every Claude Code
  session in the user's tmux the first time they type a reply containing a space —
  i.e. the first real reply.
- **A `set mode-keys vi` typed at a *running* server is harmless.** Only the
  config-load path corrupts it. So the doctor cannot detect this by asking a live
  server; it must load the user's config into a server of its own.

### What this means for the doctor

The doctor's job is to catch condition 1. A regex over tmux's option tables can
never be authoritative — tmux has hundreds of options and several command aliases.
So do what `gnome.py` does:

> **Act through one channel, verify through another. Believe the verifier.**

- `check_tmux_conf(text)` stays a **pure, offline, best-effort** parse. Its value is
  that it can *name the offending line* and print a fix. It is a hint, not a verdict.
- `probe_tmux_conf(...)` is the **verdict**: start a tmux server on a private socket
  with `-f <the user's conf>`, `send-keys -l -- 'a b'` into a throwaway session,
  then ask whether the server is still alive. That is the exact failure, reproduced,
  in a place where it cannot hurt anyone.

If the probe says the config is fatal, the doctor fails, regardless of what the
regex thought. If the probe cannot run (no tmux), say so; do not guess.

---

## Files

- Create: `src/ccnav/doctor.py`
- Create: `bin/cc-navigator-doctor` (executable, mode 100755)
- Test: `tests/test_doctor.py`

## Interfaces

Keep the plan's shape where the plan is right:

```python
@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str
    required: bool = True     # see "the doctor that can never pass", below

def check_tmux_conf(text: str) -> Check          # pure, offline hint
def check_tmux_titles(text: str) -> Check        # pure
def check_claude_hooks(settings: Dict[str, object], hook_path: str) -> Check  # pure
def probe_tmux_conf(conf_path, run=proc.run_command, socket_name=...) -> Check # live
def run_all(...) -> List[Check]
def main() -> int
```

### `check_tmux_conf` — the corrected rule

A line is fatal iff, after stripping comments and leading whitespace:

- its first word is one of `set`, `set-option`, `setw`, `set-window-option`; **and**
- none of the single-letter flags in its leading `-xyz` groups is `g`, `q` or `s`.

Flags bundle (`-gw`, `-qw`, `-sg`, `-ug`, `-as`), so test membership per character,
not per token. Stop scanning flags at the first token that does not start with `-`
(that token is the option name). A line whose first word is `bind`, `bind-key`,
`unbind`, `setenv`, `set-hook`, `source-file` … is not this check's business.

`check_tmux_conf` returns `ok=False` naming **every** offending line, not just the
first — a user with three of them should not have to run the doctor three times.

Two limits you must state in the docstring rather than paper over:
- `source-file` includes are not followed. The live probe covers them; the regex
  cannot.
- The flag rule is derived from the measured matrix above, not from tmux's source.
  It is a hint. The probe is the verdict.

### `probe_tmux_conf` — the verdict

```
tmux -L <private> kill-server                     # ignore failure
tmux -L <private> -f <conf> new-session -d -s ccnav_probe
tmux -L <private> send-keys -t ccnav_probe -l -- 'a b'
tmux -L <private> list-sessions                   # exit 0 => the config is safe
tmux -L <private> kill-server                     # in a finally:
```

Requirements, each of which is a test:

- The socket name must be private and unique — include `os.getpid()`. It must never
  be `default`. **Assert this in a test that inspects the argv**, the way
  `test_list_argv_uses_explicit_socket` does for `tmuxctl`.
- Every tmux invocation goes through `proc.run_command`, so it is bounded by
  `DEFAULT_TIMEOUT` and injectable as a fake `Runner` in tests. The doctor's own
  tests must **not** start a real tmux server; drive it with a fake runner that
  records argv and returns scripted `(code, out)` pairs. (Task 12's integration
  test is where a real server belongs.)
- `kill-server` runs even when a step raises. A leaked server is a bug.
- If `shutil.which("tmux")` is None, or the conf file does not exist, return a
  `Check` that says the probe did not run. **Do not return `ok=True`.** "I could not
  check" is not "it is fine" — that is this project's whole thesis.
- The conf file is passed to tmux by path. It executes the user's config in a server
  of ours: a `run-shell` line in their conf would run. Note it in the docstring.

### The doctor that can never pass

The plan's `run_all` includes `gnome.eval_available()` as a check, and `main()`
returns 1 if any check fails. On GNOME 41+, `Eval` is blocked, the user cannot fix
it, and the check's own "fix" text reads *"jump will be disabled; listing and input
still work"* — which is not a fix, it is a consolation. A doctor that can never exit
0 teaches the user to ignore the doctor.

So: `Check.required: bool = True`. The Eval check is `required=False`. `main()`
exits nonzero only on a failed **required** check, and prints advisory failures as
`warn`, not `FAIL`. Justify the exit-code rule in a comment.

(On this machine GNOME Shell is 3.36.9 and `Eval("1+1")` answers `(true, '2')`, so
this path is not exercised here. It is still wrong to ship.)

### `check_tmux_titles` — a bug in the plan's version

`check_tmux_conf` skips commented-out lines. `check_tmux_titles` uses bare `re.M`
searches and **does not**. So

```
# set -g set-titles on
# set -g set-titles-string 'ccnav:#{session_name}'
```

passes the check, and the jump then addresses a window title that does not exist.
Skip comments in both. Add a test named for the behaviour.

Also accept an unquoted value (`set -g set-titles-string ccnav:#{session_name}`);
tmux does. The plan's regex demands a quote.

### `check_claude_hooks`

Keep the plan's implementation; it is correct. Note that on this machine
`~/.claude/settings.json` exists and contains **no `hooks` key at all**, so this
check must fail cleanly against a real, non-empty settings file — not only against
`{}`. Add that test.

---

## Investigations whose answers I want in the report

**a) `setenv FOO bar` and `set-hook after-new-session ""` — bare, no `-g`.** I did
not measure these. Both take a target. Are they fatal? Extend the matrix. If they
are, say whether you widened `check_tmux_conf`'s command list or left the probe to
catch them, and why. (`setenv -g FOO bar` and `set-hook -g ...` are measured safe.)

**b) Does an attached client change anything?** Every measurement above used
`new-session -d` with no client. Determine whether the segfault also fires when a
client is attached — that is the user's real situation. You may attach a client on
your own private socket (`script -qfc 'tmux -L ... attach' /dev/null` backgrounded,
then kill it). Do **not** attach to any server you did not create.

**c) Is the trigger the Space *character* or the Space *key*?** Measured: `-l -- 'a b'`
dies, `-l -- 'ab'` lives, `-l -- $'a\tb'` lives, `-l -- 'a-b'` lives, `-l -- '한 글'`
dies, `send-keys Space` dies, `send-keys Enter` lives. Is there any other single key
that kills it? Five minutes of trying, then report what you found. This matters
because `cc_navigator`'s reply box sends arbitrary user text.

**d) Does the fatal config break `cc-navigator-hook`?** The hook does not touch
tmux, but it reads `$TMUX`. Confirm the hook is unaffected, in one sentence.

---

## Mutation testing — mandatory

Break the implementation, run `./run-tests`, and record which **named** test fails.
A surviving mutation is a finding to report, never a thing to paper over.

1. `check_tmux_conf` uses the plan's regex `^\s*set(-option)?\s+mode-keys\b`.
   Must be killed by a test feeding `setw mode-keys vi` **and** one feeding
   `set status-bg black`.
2. `check_tmux_conf` treats `-q` as fatal (drop `q` from the safe set).
3. `check_tmux_conf` treats `-s` as fatal (drop `s` from the safe set).
4. `check_tmux_conf` checks only the first flag token, not every character
   (so `-gw` reads as fatal, or `-wg` reads as safe — pick the direction that
   your implementation makes possible).
5. `check_tmux_conf` stops at the first offending line instead of listing all.
6. `check_tmux_titles` stops skipping comments.
7. `probe_tmux_conf` omits `kill-server` in the `finally`.
8. `probe_tmux_conf` returns `ok=True` when `tmux` is absent.
9. `probe_tmux_conf` sends `-l -- 'ab'` (no space) instead of `'a b'` — i.e. the
   probe stops reproducing the bug and every fatal config passes.
10. `main()` counts advisory (`required=False`) failures toward the exit code.
11. `main()` ignores `required=True` failures.

**Prediction, to be checked and not agreed with:** mutation 4 and mutation 9 are the
two I expect a careless test suite to miss. Mutation 9 especially — a test that fakes
the runner and only asserts "the probe returned ok=False when list-sessions failed"
never looks at the payload, so the probe can stop probing and nothing notices. If you
cannot kill 9, you have not tested the thing that matters. Every implementer before
you who reported a survivor was right to; each one exposed a real hole in the plan.

---

## Calibration: four confident, plausible, wrong claims from this project's history

Each of these was believed and written down before being disproved.

1. **"tmux 3.0a segfaults on `send-keys -l`."** (Task 6 implementer.) Reported as a
   misdiagnosis and retracted. It was *true*; the reporter simply had not isolated
   the config precondition. The retraction is now the error in the record. Reporting
   a surprising observation is cheap; erasing one is expensive.
2. **"`os.replace` vs `copyfile` is unobservable without a flaky concurrency
   harness."** (Task 3 implementer.) `stat().st_ino` distinguishes them
   deterministically. "No test can observe X" is a claim to falsify, not to accept.
   Spend five minutes trying before you write it.
3. **"`pgrep -f 'sleep 47'` proves no orphan survived."** (Task 10.) `-f` matches the
   *shell* whose command line contains the string. It finds a process that does not
   exist. `pgrep -x sleep` is the honest count.
4. **"`pgrep -x tmux` shows the server is dead."** (Me, today, twice.) The tmux
   server's `comm` is `tmux: server`, so `-x tmux` never matches it and reports 0
   while the server is happily serving. And an earlier run of the whole matrix
   showed *every* variant failing identically — because my socket path exceeded
   `sun_path`'s 108 bytes, not because of anything I was testing.
   **An identical failure signature across different inputs is evidence of a common
   cause upstream of the inputs.** The honest liveness probe is
   `tmux -L <sock> list-sessions; echo $?`.

Distrusting an API's self-report has to extend to distrusting your own instrument.

---

## Then

- Full suite green, plus your new tests. `env -u DISPLAY ./run-tests` green.
- Output pristine. No leaked tmux servers: `ls /tmp/tmux-$(id -u)/` unchanged, and
  no server answering on any socket you created.
- `chmod +x bin/cc-navigator-doctor`; confirm git records mode `100755`.
- Run `./bin/cc-navigator-doctor; echo "exit=$?"` against the real machine and paste
  the output into your report. Expected here: `tmux.conf mode-keys` **passes** (this
  machine's conf already has `setw -g mode-keys vi`), `tmux.conf set-titles` FAILS
  (the lines are absent — the ledger claims they were added; they were not),
  `claude hooks` FAILS, `gnome shell eval` passes. `exit=1`.
- Commit:
  `git add src/ccnav/doctor.py bin/cc-navigator-doctor tests/test_doctor.py docs/`
  `git commit -m "feat: prerequisite doctor that reproduces the tmux segfault instead of guessing at it"`
- Write `docs/superpowers/sdd/task-11-report.md` in the shape of `task-10-report.md`:
  the mutation table, the answers to the investigations, and anything you found that
  this brief got wrong. **This brief is a claim, too.**

## Notes and traps

- Work from `/home/kodogyu/playground/cc_navigator`. The plan and the older briefs
  say `/data/playground/cc_navigator`; that path does not exist on this machine.
  The hook path baked into the plan's `settings.json` snippet is likewise stale.
- Do not add a config file, a `--json` flag, logging, or a `--fix` mode. The doctor
  prints what is wrong and how to fix it. YAGNI.
- `gdbus` on this machine resolves to `~/anaconda3/bin/gdbus`. Both it and
  `/usr/bin/gdbus` answer `Eval("1+1")` with `(true, '2')`. `wmctrl` and `xdotool`
  are **not installed**; use `xwininfo -root -tree` if you need to look at windows.
