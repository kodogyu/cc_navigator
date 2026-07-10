# Task 5 brief: tmux queries

BASE commit: `5d50fc6` (feat/cc-navigator) — confirm with `git log -1` before you start.
Plan: `docs/superpowers/plans/2026-07-10-cc-navigator.md`, section `### Task 5`
(lines 737-915). The plan is authoritative if it disagrees with this brief.

## Global Constraints (bind every task)

- **Interpreter is `/usr/bin/python3` (3.8.10).** Never invoke bare `python3`.
- **Zero third-party dependencies.** Stdlib `unittest` only.
- Python 3.8: no `match`, no `X | Y` runtime annotations. Every module under `src/`
  starts with `from __future__ import annotations`. Test files do not.
- **Never trust an API's self-report.** Verify by running code — including your own
  claims about what can and cannot be tested.
- User text sent to tmux is always one `argv` element. It is never interpolated
  into a shell string. (That bites in Task 6; keep it in mind here.)
- Test command: `./run-tests`.

## Context

cc_navigator's model is built by asking tmux two questions, once per second:
which session owns each pane, and what is each pane's title. Both are answered by
`tmux list-panes -a -F '<format>'`, which prints one line per pane.

Two design facts drive this task, and both were verified empirically during design:

1. **The pane title is display-only.** tmux's `#{pane_title}` captures whatever OSC
   title the inner program set — for Claude Code that is something like
   `✳ 작업 중 (demo-project)`. It is arbitrary UTF-8 and may contain `=`, `|`,
   spaces, anything. It is shown to the user and **never parsed or matched**.
2. **The address is the outer X11 window title**, `ccnav:<tmux_session_name>`,
   which is why we need `#{session_name}` per pane. That comes later (Task 7/8).

Consequence for this task: `parse_kv_lines` must split each line on its **first**
`=` only. A pane id (`%12`) never contains `=`; a title routinely might. Splitting
once is the entire reason it is safe to carry a title through the same channel.

`run` is injected into every query so tests never spawn a subprocess. `proc.py`
holds the only `subprocess` call in the codebase and has no logic of its own, so
it gets no test of its own — the injected fakes cover its callers.

## Files

- Create: `src/ccnav/proc.py`
- Create: `src/ccnav/tmuxctl.py`
- Test: `tests/test_tmuxctl_query.py`

## Interface

- `proc.Runner` — type alias `Callable[[Sequence[str]], Tuple[int, str]]`, returning `(returncode, stdout)`.
- `proc.run_command(argv: Sequence[str]) -> Tuple[int, str]`
- `tmuxctl.parse_kv_lines(text: str) -> Dict[str, str]`
- `tmuxctl.list_argv(socket: str, fmt: str) -> List[str]`
- `tmuxctl.sessions_by_pane(socket: str, run: Runner = run_command) -> Dict[str, str]`
- `tmuxctl.titles_by_pane(socket: str, run: Runner = run_command) -> Dict[str, str]`

Task 6 adds actions to `tmuxctl`. Task 7 reuses `proc.run_command` for `gdbus`
and `xprop`. Do not create a second subprocess call site anywhere.

## This is a TDD task. Follow the steps in order.

### Step 1: Write the failing test

`tests/test_tmuxctl_query.py`:

```python
import unittest

from ccnav import tmuxctl


class ParseKvLinesTest(unittest.TestCase):
    def test_splits_on_the_first_equals_only(self):
        # pane_title is arbitrary text and may contain '='.
        parsed = tmuxctl.parse_kv_lines("%1=a=b=c\n")
        self.assertEqual(parsed, {"%1": "a=b=c"})

    def test_title_may_contain_pipes_and_spaces(self):
        parsed = tmuxctl.parse_kv_lines("%2=make test | tee log\n")
        self.assertEqual(parsed, {"%2": "make test | tee log"})

    def test_utf8_title_survives(self):
        parsed = tmuxctl.parse_kv_lines("%3=✳ 작업 중 (X)\n")
        self.assertEqual(parsed, {"%3": "✳ 작업 중 (X)"})

    def test_empty_title_is_empty_string(self):
        self.assertEqual(tmuxctl.parse_kv_lines("%4=\n"), {"%4": ""})

    def test_blank_and_malformed_lines_are_skipped(self):
        parsed = tmuxctl.parse_kv_lines("\n%5=ok\ngarbage\n\n")
        self.assertEqual(parsed, {"%5": "ok"})

    def test_empty_input(self):
        self.assertEqual(tmuxctl.parse_kv_lines(""), {})


class QueryTest(unittest.TestCase):
    def test_list_argv_uses_explicit_socket(self):
        argv = tmuxctl.list_argv("/tmp/s", "#{pane_id}=#{session_name}")
        self.assertEqual(
            argv,
            ["tmux", "-S", "/tmp/s", "list-panes", "-a", "-F",
             "#{pane_id}=#{session_name}"],
        )

    def test_sessions_by_pane(self):
        calls = []

        def fake_run(argv):
            calls.append(list(argv))
            return 0, "%0=demo\n%1=sandbox\n"

        result = tmuxctl.sessions_by_pane("/tmp/s", run=fake_run)
        self.assertEqual(result, {"%0": "demo", "%1": "sandbox"})
        self.assertEqual(calls[0][-1], "#{pane_id}=#{session_name}")

    def test_titles_by_pane(self):
        def fake_run(argv):
            return 0, "%0=✳ 작업 중 (demo-project)\n"

        result = tmuxctl.titles_by_pane("/tmp/s", run=fake_run)
        self.assertEqual(
            result, {"%0": "✳ 작업 중 (demo-project)"}
        )

    def test_no_tmux_server_yields_empty_dict(self):
        def fake_run(argv):
            return 1, ""

        self.assertEqual(tmuxctl.sessions_by_pane("/tmp/s", run=fake_run), {})
        self.assertEqual(tmuxctl.titles_by_pane("/tmp/s", run=fake_run), {})
```

### Step 2: Run the test to verify it fails

`./run-tests -k ParseKvLinesTest -k QueryTest`
Expected: FAIL with `ModuleNotFoundError: No module named 'ccnav.tmuxctl'`
Capture it — RED evidence.

### Step 3: Write the minimal implementation

`src/ccnav/proc.py`:

```python
"""The single place cc_navigator spawns a subprocess."""
from __future__ import annotations

import subprocess
from typing import Callable, Sequence, Tuple

Runner = Callable[[Sequence[str]], Tuple[int, str]]


def run_command(argv: Sequence[str]) -> Tuple[int, str]:
    completed = subprocess.run(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        universal_newlines=True,
    )
    return completed.returncode, completed.stdout
```

`src/ccnav/tmuxctl.py`:

```python
"""Every tmux interaction: the queries that build the model, the actions the UI fires."""
from __future__ import annotations

from typing import Dict, List

from .proc import Runner, run_command


def parse_kv_lines(text: str) -> Dict[str, str]:
    """Split each line on its FIRST '='.

    A pane id ('%12') never contains '='. A pane title is whatever Claude Code
    wrote and may contain '=', '|', spaces and arbitrary UTF-8. Splitting once
    is what makes it safe to carry the title in the same record.
    """
    parsed = {}  # type: Dict[str, str]
    for line in text.splitlines():
        if not line:
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        parsed[key] = value
    return parsed


def list_argv(socket: str, fmt: str) -> List[str]:
    return ["tmux", "-S", socket, "list-panes", "-a", "-F", fmt]


def _query(socket: str, fmt: str, run: Runner) -> Dict[str, str]:
    code, out = run(list_argv(socket, fmt))
    if code != 0:
        return {}
    return parse_kv_lines(out)


def sessions_by_pane(socket: str, run: Runner = run_command) -> Dict[str, str]:
    return _query(socket, "#{pane_id}=#{session_name}", run)


def titles_by_pane(socket: str, run: Runner = run_command) -> Dict[str, str]:
    return _query(socket, "#{pane_id}=#{pane_title}", run)
```

### Step 4: Run the test to verify it passes

`./run-tests -k ParseKvLinesTest -k QueryTest` → `Ran 10 tests` / `OK`
Then `./run-tests` → expect `Ran 60 tests` / `OK` (50 existing + 10 here).
Output must be pristine.

### Step 5: Prove your tests are not vacuous

Break the implementation, confirm a **named** test fails, restore. At minimum:

1. `parse_kv_lines` uses `line.split("=")` and takes `[0], [1]` instead of `partition`.
2. `parse_kv_lines` keeps malformed lines (drop the `if not separator: continue`).
3. `_query` returns `parse_kv_lines(out)` regardless of `code`.
4. `list_argv` drops the `-a` flag.
5. `sessions_by_pane` and `titles_by_pane` swap their format strings.

Report which named test caught each. A survivor is a finding — name the missing
test. Do not invent contorted tests to chase one; report the gap instead.

Mutation 4 in particular: think about whether *any* current test would notice a
missing `-a`. If none would, say so — that is honest and useful.

### Step 6: One real-tmux smoke check (do not add it to the suite)

The unit tests never spawn tmux. Before you commit, convince yourself the format
strings are actually right by running them against a real tmux server on a
**private socket** so you can never touch the user's sessions:

```bash
S=$(mktemp -u /tmp/ccnav-smoke.XXXX)
tmux -S "$S" new-session -d -s smoke
tmux -S "$S" list-panes -a -F '#{pane_id}=#{session_name}'
tmux -S "$S" list-panes -a -F '#{pane_id}=#{pane_title}'
tmux -S "$S" kill-server
```

Paste the real output into your report. If `#{pane_title}` comes back empty for a
plain shell, that is expected — it is only set once an inner program emits an OSC
title. Say what you observed either way. **Never touch the default tmux socket.**

### Step 7: Commit

```bash
git add src/ccnav/proc.py src/ccnav/tmuxctl.py tests/test_tmuxctl_query.py
git commit -m "feat: tmux pane and title queries"
```

## Notes and traps

- `partition` not `split`. The docstring explains why; do not "simplify" it.
- `universal_newlines=True` (not `text=True`) — the codebase targets 3.8 and the
  plan uses the older spelling. Match it.
- `stderr=subprocess.DEVNULL` is deliberate: a dead tmux socket prints to stderr
  and we treat a non-zero exit as "no server", not as an error to surface.
- Do not add a `timeout=` to `subprocess.run` in this task even though you may
  notice `run_command` would block forever on a hung tmux server. **Raise it in
  your report instead.** The UI's threading model is decided in Task 10, and
  whether this needs a guard depends on that. Flag it; do not fix it here.
- `proc.py` gets no test file. That is intentional, not an oversight.
- Do not add retry logic, caching, or a `Runner` class. YAGNI.
- Work from `/data/playground/cc_navigator`.
