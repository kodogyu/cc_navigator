# Task 1 Report: Scaffolding and state directory

## What I implemented

Created the package skeleton, test runner, and the state-directory resolution
module, following the brief verbatim (TDD order).

Files created:
- `run-tests` — POSIX `sh` test runner, executable (mode 100755). Uses
  `/usr/bin/python3` (mandatory: `which python3` is Anaconda with no PyGObject),
  runs `unittest discover -s tests` with `PYTHONPATH=src`, and forwards `"$@"`
  so later tasks can pass `-k` etc.
- `src/ccnav/__init__.py` — empty package marker.
- `src/ccnav/paths.py` — `state_dir()` and `ensure_state_dir()`.
  - `state_dir()` returns `$XDG_RUNTIME_DIR/cc-navigator` when the env var is set
    and non-empty, else `/tmp/cc-navigator-<uid>`.
  - `ensure_state_dir()` creates the directory (`parents=True, exist_ok=True`)
    and chmods it to `0o700`.
- `tests/test_paths.py` — `StateDirTest` with 3 cases (XDG set, XDG unset,
  XDG empty-string treated as unset).

Module starts with `from __future__ import annotations` per the Python 3.8
global constraint. Zero third-party dependencies; stdlib `unittest` only.

## What I tested and the results

- Interpreter preflight: `/usr/bin/python3 --version` -> Python 3.8.10;
  `import unittest` OK.
- Targeted run `./run-tests -k StateDirTest`: 3 tests, OK.
- Full suite `./run-tests`: 3 tests, OK, output pristine (no warnings/stderr).

## TDD evidence

### RED

Command:

    ./run-tests -k StateDirTest

Output (abridged):

    test_paths (unittest.loader._FailedTest) ... ERROR
    ...
    File ".../tests/test_paths.py", line 6, in <module>
        from ccnav import paths
    ImportError: cannot import name 'paths' from 'ccnav' (.../src/ccnav/__init__.py)
    ----------------------------------------------------------------------
    Ran 1 test in 0.000s
    FAILED (errors=1)
    EXIT: 1

Why expected: `paths.py` did not exist yet, so `from ccnav import paths`
cannot resolve. The brief predicted `ModuleNotFoundError: No module named
'ccnav.paths'`. The actual message is `ImportError: cannot import name
'paths' from 'ccnav'` — a benign variance: because Step 1 already created the
`ccnav` package (`__init__.py` present), the import failure surfaces on the
missing submodule rather than a missing top-level module. Same root cause
(no `paths` module), which is exactly the failure the test is meant to force.

### GREEN

Command:

    ./run-tests -k StateDirTest

Output:

    test_empty_xdg_runtime_dir_is_treated_as_unset (test_paths.StateDirTest) ... ok
    test_falls_back_to_uid_scoped_tmp (test_paths.StateDirTest) ... ok
    test_uses_xdg_runtime_dir_when_set (test_paths.StateDirTest) ... ok
    ----------------------------------------------------------------------
    Ran 3 tests in 0.001s
    OK
    EXIT: 0

## Files changed

Commit `8df7c32` "feat: state directory resolution and test runner":

    run-tests             |  5 +++++
    src/ccnav/__init__.py |  0
    src/ccnav/paths.py    | 24 ++++++++++++++++++++++++
    tests/test_paths.py   | 26 ++++++++++++++++++++++++++
    4 files changed, 55 insertions(+)

## Self-review findings

- Completeness: all 4 brief files present; `run-tests` executable; both
  interface functions produced (`state_dir`, `ensure_state_dir`). No missing
  steps.
- Naming: matches the brief exactly.
- YAGNI: built only what the brief lists. No `setup.py`, `pyproject.toml`,
  `conftest.py`, `tests/__init__.py`, `.gitignore`, or README created.
- Pristine output: full-suite run has no warnings or stray stderr.
- `__pycache__/` dirs appear as untracked after the run; they were NOT staged
  (I added the 4 files explicitly, per the brief). No `.gitignore` created, as
  instructed.

## Concerns

None. The only deviation from the brief's literal prediction is the RED error
message wording (ImportError vs ModuleNotFoundError), explained above; it does
not affect correctness.
