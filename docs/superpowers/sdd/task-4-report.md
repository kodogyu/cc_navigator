# Task 4 report: Hook shim entry point

Commit: `6599c86` "feat: hook shim writes session state and always exits 0"
Base: `7d9efbc`

## What I implemented

- `src/ccnav/hook.py` — the hook shim module:
  - `MESSAGE_LIMIT = 200`
  - `tmux_socket_from_env(env)` — extracts the socket path (first
    comma-separated field of `$TMUX`); `None` if `$TMUX` is missing/empty.
  - `build_record(payload, env, now)` — the pure, testable seam. Returns
    `None` if not addressable (no tmux pane/socket), the session id is
    unsafe, or `hookstate.classify` says the event carries no state
    change. Otherwise returns the full state record, with `message`
    truncated to `MESSAGE_LIMIT`.
  - `main() -> int` — reads `os.environ` and `sys.stdin` directly (the
    untestable I/O edges), delegates all logic to `build_record`, and
    swallows every exception on every path. Always returns `0`.
- `bin/cc-navigator-hook` — POSIX shell entry point. Resolves its own
  directory so it works from any CWD, sets `PYTHONPATH`, redirects stderr
  to `/dev/null`, and unconditionally `exit 0`s (no `exec`, so a nonzero
  python exit status can never propagate to Claude Code).
- `tests/test_hook.py` — `TmuxSocketTest` (3 tests), `BuildRecordTest`
  (7 tests) exactly as specified in the brief, plus `MainTest` (6 tests,
  authorized by Step 5) covering `main()`'s exit-0 invariant end-to-end.

Both `hook.py` and the shell shim are byte-for-byte what the brief
specified in Step 3 (confirmed by diffing the restored file against the
brief's literal text after mutation testing — see Mutation Evidence).

## TDD Evidence

### RED

Command: `./run-tests -k TmuxSocketTest -k BuildRecordTest`

```
ERROR: test_hook (unittest.loader._FailedTest)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_hook
Traceback (most recent call last):
  File "/usr/lib/python3.8/unittest/loader.py", line 436, in _find_test_path
    module = self._get_module_from_name(name)
  File "/usr/lib/python3.8/unittest/loader.py", line 377, in _get_module_from_name
    __import__(name)
  File "/data/playground/cc_navigator/tests/test_hook.py", line 9, in <module>
    from ccnav import hook, hookstate
ImportError: cannot import name 'hook' from 'ccnav' (/data/playground/cc_navigator/src/ccnav/__init__.py)

----------------------------------------------------------------------
Ran 1 test in 0.000s

FAILED (errors=1)
```

This is the same underlying failure the brief anticipated
(`ccnav.hook` does not exist yet) — Python 3.8's loader reports it as an
`ImportError` at collection time rather than a per-test
`ModuleNotFoundError`, because `test_hook.py` itself failed to import.
Expected, given `hook.py` did not exist yet.

### GREEN

Command: `./run-tests -k TmuxSocketTest -k BuildRecordTest`

```
test_builds_a_full_record (test_hook.BuildRecordTest) ... ok
test_ignored_event_returns_none (test_hook.BuildRecordTest) ... ok
test_long_message_is_truncated (test_hook.BuildRecordTest) ... ok
test_missing_session_id_returns_none (test_hook.BuildRecordTest) ... ok
test_outside_tmux_returns_none (test_hook.BuildRecordTest) ... ok
test_tmux_without_pane_returns_none (test_hook.BuildRecordTest) ... ok
test_unsafe_session_id_returns_none (test_hook.BuildRecordTest) ... ok
test_empty_tmux_is_none (test_hook.TmuxSocketTest) ... ok
test_missing_tmux_is_none (test_hook.TmuxSocketTest) ... ok
test_takes_the_first_comma_field (test_hook.TmuxSocketTest) ... ok

----------------------------------------------------------------------
Ran 10 tests in 0.000s

OK
```

Matches the brief's expected `Ran 10 tests` / `OK`.

`MainTest` (6 tests, added per Step 5) was also green from the start
since it was written alongside the implementation:

```
test_happy_path_writes_expected_state_file (test_hook.MainTest) ... ok
test_returns_0_when_payload_is_a_json_list (test_hook.MainTest) ... ok
test_returns_0_when_statestore_write_raises (test_hook.MainTest) ... ok
test_returns_0_when_stdin_is_empty (test_hook.MainTest) ... ok
test_returns_0_when_stdin_is_not_json (test_hook.MainTest) ... ok
test_writes_nothing_without_tmux (test_hook.MainTest) ... ok

----------------------------------------------------------------------
Ran 6 tests in 0.005s

OK
```

Full suite: `./run-tests` → `Ran 47 tests in 0.024s` / `OK`.

## Manual verification (brief Steps 6 and 7)

### Step 6: end-to-end happy path

```
$ mkdir -p /tmp/ccnav-manual
$ printf '%s' '{"hook_event_name":"Stop","session_id":"abc-123","cwd":"/proj"}' \
  | XDG_RUNTIME_DIR=/tmp/ccnav-manual TMUX=/tmp/tmux-1000/default,1,0 TMUX_PANE=%12 \
    ./bin/cc-navigator-hook
exit=0
$ cat /tmp/ccnav-manual/cc-navigator/abc-123.json; echo
{"session_id": "abc-123", "cwd": "/proj", "tmux_socket": "/tmp/tmux-1000/default", "tmux_pane": "%12", "state": "waiting", "reason": "idle", "message": "", "updated_at": 1783670723}
```

Matches expectation: `"state": "waiting"`, `"reason": "idle"`,
`"tmux_pane": "%12"`.

Also ran from a different CWD (`/tmp`, invoking the shim by absolute
path) to prove `here=$(cd "$(dirname "$0")" && pwd)` resolves correctly
regardless of caller's working directory:

```
$ cd /tmp
$ printf '%s' '{"hook_event_name":"Stop","session_id":"abc-123","cwd":"/proj"}' \
  | XDG_RUNTIME_DIR=/tmp/ccnav-manual2 TMUX=/tmp/tmux-1000/default,1,0 TMUX_PANE=%12 \
    /data/playground/cc_navigator/bin/cc-navigator-hook
exit=0
{"session_id": "abc-123", "cwd": "/proj", "tmux_socket": "/tmp/tmux-1000/default", "tmux_pane": "%12", "state": "waiting", "reason": "idle", "message": "", "updated_at": 1783670729}
```

### Step 7: garbage input always exits 0, no stdout/stderr

```
$ printf 'not json' | ./bin/cc-navigator-hook; echo "exit=$?"
exit=0
$ printf '{}'       | ./bin/cc-navigator-hook; echo "exit=$?"
exit=0
$ printf ''         | ./bin/cc-navigator-hook; echo "exit=$?"
exit=0
```

All three captured with stdout+stderr combined to confirm silence:
each produced `stdout+stderr: []` before the `exit=0` line.

## Mutation Evidence

All six mutations applied one at a time to `src/ccnav/hook.py`, full
suite run (`./run-tests`), then reverted by hand (the file is new/
untracked, so `git checkout --` doesn't apply — reverted via Edit and
confirmed byte-identical to the brief's Step 3 text via `diff`, which
produced no output).

| # | Mutation | Result | Named test(s) that caught it |
|---|---|---|---|
| 1 | `tmux_socket_from_env` returns the whole `$TMUX` instead of the first field | KILLED (3 failures) | `TmuxSocketTest.test_takes_the_first_comma_field` (also `BuildRecordTest.test_builds_a_full_record`, `MainTest.test_happy_path_writes_expected_state_file`) |
| 2 | Drop the `if not pane or not socket` guard | KILLED (3 failures) | `BuildRecordTest.test_outside_tmux_returns_none` (also `test_tmux_without_pane_returns_none`, `MainTest.test_writes_nothing_without_tmux`) |
| 3 | Drop the `is_safe_session_id` check in `build_record` | KILLED (2 failures) | `BuildRecordTest.test_unsafe_session_id_returns_none` (also `test_missing_session_id_returns_none`) |
| 4 | Drop the `[:MESSAGE_LIMIT]` slice | KILLED (1 failure) | `BuildRecordTest.test_long_message_is_truncated` |
| 5 | `main()` returns `1` instead of `0` when `json.load` raises | KILLED (2 failures) | `MainTest.test_returns_0_when_stdin_is_not_json` (also `test_returns_0_when_stdin_is_empty`) |
| 6 | Remove the `try/except` around `statestore.write` | KILLED (1 error) | `MainTest.test_returns_0_when_statestore_write_raises` |

**6/6 killed. No survivors.** I did not find any mutation I was tempted
to write off as "impossible to test" — each one was caught
deterministically by an existing named test on the first try, so there
was no case requiring the "spend five minutes trying anyway" push from
the calibration note. After the last mutation, `diff`ed the restored
file against the brief's literal Step 3 text: no differences, confirming
exact restoration. Final `./run-tests` after restoration: `Ran 47 tests`
/ `OK`; `git status` clean before committing.

## Files changed

- `src/ccnav/hook.py` (new)
- `bin/cc-navigator-hook` (new, executable)
- `tests/test_hook.py` (new)

## Self-review

- **Completeness:** All brief steps done, including both manual
  verification steps (6 and 7), plus the different-CWD check the "Notes
  and traps" section calls for.
- **Quality:** Names match the interface spec exactly
  (`tmux_socket_from_env`, `build_record`, `main`, `MESSAGE_LIMIT`).
  Code is the brief's literal Step 3 text — no embellishment.
- **Discipline (YAGNI):** Verified by grep — no `logging`, no `--debug`,
  no lockfile, no `SessionEnd` branch, and the shell shim has no `exec`
  (only an explanatory comment about *not* using one, which is
  intentional and required by the brief).
- **Testing:** Every `MainTest` assertion (except the injected-failure
  test, which necessarily mocks `statestore.write` to simulate an
  `OSError`) checks either a return value or the actual JSON file
  written to a `tempfile.TemporaryDirectory()`-backed `XDG_RUNTIME_DIR`
  — never a mock's call args. Ran the `MainTest` class with
  `PYTHONWARNINGS=error::ResourceWarning` and it stayed green — no
  leaked file handles. No test touches the real state directory: every
  environment mutation goes through `mock.patch.dict(os.environ, ...,
  clear=True)` scoped to a temp dir, and mutation 2's survivor-check
  independently confirmed that a bug which *would* write into the real
  dir under no-tmux conditions gets caught by
  `test_writes_nothing_without_tmux`.

No issues or concerns found.
