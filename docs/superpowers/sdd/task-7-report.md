# Task 7 report: GNOME window activation with independent verification

BASE commit: `cb114a4` (confirmed via `git log -1` before starting).
Result commit: `eddd59f` — "feat: activate window by title and verify focus through xprop"

## What I implemented

`src/ccnav/gnome.py`, verbatim from the plan (lines 1241-1387):

- `escape_js` — escapes `\`, `'`, `\n`, `\r`, backslash **first**.
- `_match_first_js(title, prelude, action)` — shared JS-builder: finds all windows
  whose `get_title()` `===` the target, activates only the first, counts all matches.
- `activate_js` / `activate_ts_js` — the two activation variants; the latter never
  passes `0` as a timestamp, using `get_current_time_roundtrip()` instead.
- `parse_eval_result` — trusts only a literal `(true` prefix, never the gdbus exit code.
- `parse_match_count` — regex-extracts `matched=N` from the Eval reply.
- `eval_js` — the one function that shells out to gdbus; returns `False` immediately
  if the exit code is nonzero, else parses stdout.
- `eval_available` — startup probe (`1+1` should come back `(true, '2')`).
- `_active_window_id` / `active_window_title` — read `_NET_ACTIVE_WINDOW` and then that
  window's `_NET_WM_NAME` via `xprop`, the independent verification channel.
- `_wait_for_focus` — polls `active_window_title` every 100 ms until it equals the
  target title or `timeout` elapses.
- `ActivationResult` (frozen dataclass, `ok: bool`, `matched: int`).
- `activate_window_titled` — fires `activate_js`, waits for focus; if it never
  arrived, fires `activate_ts_js` (the `activate(0)`-trap fallback) and waits again.
  It **ignores Eval's own `ok` value entirely** and reports success only when xprop
  confirms focus moved.

`tests/test_gnome.py` — copied verbatim from the plan (lines 1083-1230), 20 tests
across 7 classes: `EscapeJsTest`, `ActivateJsTest`, `ParseEvalResultTest`,
`ParseMatchCountTest`, `EvalAvailableTest`, `ActiveWindowTitleTest`,
`ActivateWindowTitledTest`.

## TDD Evidence

**RED** (test file present, module absent):

```
$ ./run-tests -k EscapeJsTest -k ActivateJsTest -k ParseEvalResultTest -k ParseMatchCountTest -k EvalAvailableTest -k ActiveWindowTitleTest -k ActivateWindowTitledTest
ERROR: test_gnome (unittest.loader._FailedTest)
ImportError: cannot import name 'gnome' from 'ccnav' (.../src/ccnav/__init__.py)
Ran 1 test in 0.000s
FAILED (errors=1)
```

(The package `ccnav/__init__.py` already exists from Tasks 1-6, so the concrete
error is `ImportError: cannot import name 'gnome'` rather than
`ModuleNotFoundError` — equivalent RED signal: the module under test does not exist.)

**GREEN** (implementation written):

```
$ ./run-tests -k EscapeJsTest -k ActivateJsTest -k ParseEvalResultTest -k ParseMatchCountTest -k EvalAvailableTest -k ActiveWindowTitleTest -k ActivateWindowTitledTest
Ran 20 tests in 0.001s
OK

$ ./run-tests
Ran 87 tests in 0.226s
OK
```

67 pre-existing + 20 new = 87. Output pristine both times.

## Mutation Evidence

All seven mutations were applied one at a time to `src/ccnav/gnome.py`, verified
against the real file (not a copy), then restored from a pristine backup
(`diff` confirmed byte-identical after every restore) before moving to the next.

| # | Mutation | Result |
|---|---|---|
| 1 | Move backslash replacement to **last** in `escape_js` | **CAUGHT** — `test_escapes_quote_and_backslash` and `test_escapes_newlines` both fail (backslashes inserted by the quote/newline replacements get double-escaped) |
| 2 | Compare titles with `==` instead of `===` in `_match_first_js` | **CAUGHT** — `test_compares_titles_with_strict_equality` fails (looks for the literal `===` in the generated JS) |
| 3 | Drop `if(!found)` guard so the **last** match wins | **CAUGHT** — `test_activates_only_the_first_match` fails (looks for the literal `if(!found)found=w` fragment) |
| 4 | `parse_eval_result` returns `text.startswith("(")` instead of `"(true"` | **CAUGHT** — `test_false_prefix_is_failure_even_though_gdbus_exits_zero` fails (a `(false, ...)` string now reports `ok=True`) |
| 5 | Delete `if code != 0: return False, out` guard in `eval_js` | **SURVIVED.** Full 87-test suite stays green with this line deleted. |
| 6 | Delete the retry in `activate_window_titled`; return after the first `_wait_for_focus` | **CAUGHT** — `test_retries_with_a_timestamp_when_eval_lied` fails (`result.ok` is `False` instead of `True`, since the retry that would have flipped it never runs) |
| 7 | `_wait_for_focus` returns `True` unconditionally | **CAUGHT** — two tests fail: `test_reports_failure_when_focus_never_moves` (`result.ok` wrongly `True`) and `test_retries_with_a_timestamp_when_eval_lied` (`len(seen_js)` is `1` instead of `2`, since the retry path never triggers) |

### Mutation 5 — honest account

You predicted this survives, and it does. I did not take that on faith; I spent
the five minutes trying to find a test in the plan's 20 that would catch it, and
then confirmed empirically that none does (full 87-test suite green with the
guard deleted).

Why it survives: `parse_eval_result("")` on its own already returns `(False, "")`
— an empty string does not start with `"(true"` — so the *only* test that exercises
`eval_js` with a nonzero code (`EvalAvailableTest.test_detects_a_missing_gdbus`,
`run=lambda argv: (127, "")`) gets the same answer (`False`) whether the guard is
present or not. There is no `EvalJsTest` class at all in the 20 tests — `eval_js`
is only ever exercised indirectly through `eval_available` (which always pairs
nonzero-code with empty stdout) and through `ActivateWindowTitledTest` (whose
fake `run` functions always return code `0` on the `gdbus` branch, success or
failure). No test in the plan pairs a **nonzero exit code with stdout that would
independently parse as `(true, ...)`.**

I demonstrated the gap concretely:

```
>>> gnome.eval_js('1+1', run=lambda argv: (127, "(true, '2')\n"))
(True, "(true, '2')")   # with the guard deleted — wrong: nonzero exit reported as success
```

**The missing test** would be something like (not added, per instructions):

```python
class EvalJsTest(unittest.TestCase):
    def test_nonzero_exit_is_a_failure_even_if_stdout_looks_like_success(self):
        ok, _ = gnome.eval_js("1+1", run=lambda argv: (127, "(true, '2')\n"))
        self.assertFalse(ok)
```

This is a real, if narrow, hole: the guard is not decorative — the module's own
docstring and the whole design principle ("`gdbus` exits 0 even when Eval returns
`(false, ...)`... the stdout must be parsed") say nothing about the reverse case
(nonzero exit with success-shaped stdout), and no test enforces it. In practice a
nonzero-exit gdbus process (e.g. `gdbus: command not found`, permission denied,
D-Bus daemon unreachable) is very unlikely to *also* emit a string starting with
`(true`, so the real-world risk is low, but the code path is untested. I did not
add the test — reporting it as instructed.

## Read-only desktop check

Environment: `DISPLAY=:1`, `XDG_SESSION_TYPE=x11`. Real desktop, not a fake.

**(a) Eval sanity check:**

```
$ gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
  --method org.gnome.Shell.Eval "1+1"; echo "exit=$?"
(true, '2')
exit=0
```

**(b) Deliberately broken expression:**

```
$ gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
  --method org.gnome.Shell.Eval "Shell.nonsense"; echo "exit=$?"
(false, 'ReferenceError: Shell is not defined')
exit=0
```

Both match the design spec's Appendix A and the plan's stated assumptions exactly:
`gdbus` really does exit 0 in both the success and the `ReferenceError` case, and
`Shell` really is undefined in Eval's scope on this GNOME 3.36 install.

**(c) xprop (real currently-focused window on this desktop):**

```
$ xprop -root _NET_ACTIVE_WINDOW
_NET_ACTIVE_WINDOW(WINDOW): window id # 0x920000c

$ xprop -id 0x920000c _NET_WM_NAME
_NET_WM_NAME(UTF8_STRING) = "OmniGibson 4.1.0 - New Stage*"
```

(Note the real active window on this box has nothing to do with cc_navigator —
some other app named "OmniGibson" happens to be focused. That is fine; the check
only needs *some* real xprop output, not a ccnav window.)

**Parser handling of this exact text** (fed the literal captured strings into the
library functions directly — no subprocess spawned, no module touching the real
desktop):

- `parse_eval_result("(true, '2')\n")` → `(True, "(true, '2')")` — correct.
- `parse_eval_result("(false, 'ReferenceError: Shell is not defined')\n")` →
  `(False, "(false, 'ReferenceError: Shell is not defined')")` — correct.
- `parse_match_count(b's text)` → `0` — correct (no `matched=` substring present,
  which is expected since this probe never calls `activate_js`).
- `active_window_title` fed the real `(root_out, wmname_out)` pair above →
  returns `'OmniGibson 4.1.0 - New Stage*'` — correctly parsed, including the
  trailing `*` and embedded spaces/dash, which the naive `split("=", 1)` and
  quote-stripping handle without issue.

All three parsers handle the real, unmodified text from this desktop correctly.

**Safety:** only `gdbus ... Eval "1+1"` / `"Shell.nonsense"` and `xprop -root` /
`xprop -id ... _NET_WM_NAME` were run. Nothing activated, raised, moved, resized,
or closed any window. No terminal was opened. No workspace switch occurred.

## Step 7a — worst-case blocking time

`activate_window_titled` calls `_wait_for_focus` up to twice (once per attempt),
each of which polls at ~100 ms intervals for up to `timeout` seconds before giving
up. With the default `timeout=1.5`:

**Worst case ≈ 2 × 1.5 s = 3.0 seconds**, plus the (comparatively small) wall time
of the two `gdbus` Eval calls and the many `xprop` subprocess spawns inside each
poll loop.

I measured this empirically rather than just asserting it: with a zero-latency
fake `run` (no real subprocess spawns) and the real `time.sleep`/`time.monotonic`,
a call that never matches (`ok=False` at the end) took:

```
elapsed seconds: 3.017112...
```

— confirming the `2 × timeout` model to within polling-loop granularity. In
production, real `gdbus`/`xprop` process-spawn overhead (roughly tens of ms per
call, and there can be ~15 xprop-pair polls per attempt at 100 ms intervals over
1.5 s, plus 2 gdbus calls) would push the true worst case somewhat past 3.0 s, but
3.0 s is the dominant, reportable number. Task 10 calling this synchronously from
a GTK callback will block the main loop for up to ~3 seconds in the failure case.
I did not fix or flag this in code — this is a report-only finding as instructed.

## Step 7b — titles containing `=` or `"`

`active_window_title` does `out.split("=", 1)[1]` — the key detail is the `1` (max
one split), not a search for the *last* `=` or all `=` occurrences.

**Titles containing `=`:** harmless, correctly parsed. The xprop line has the
fixed prefix `_NET_WM_NAME(UTF8_STRING) ` before the `=` that separates property
descriptor from value; that prefix never itself contains `=` (X atom/type names
don't), so the *first* `=` in the whole line is always the right split point.
Anything after it — including further `=` characters that are part of the title
— survives untouched, because `split("=", 1)` stops after one split and keeps the
remainder as a single string. Demonstrated: fed `_NET_WM_NAME(UTF8_STRING) =
"a=b=c"\n` → returned `'a=b=c'`, exactly right.

**Titles containing `"`:** never raises an exception, in any of the three
scenarios I constructed:

1. If xprop escapes an embedded quote (standard C-string-style backslash
   escaping, which is the typical Xlib/xprop behavior for STRING/UTF8_STRING
   property values) — e.g. `"she said \"hi\""` — the code does no *unescaping*, so
   it returns the literal characters between the outer quotes including the
   backslashes: `'she said \\"hi\\"'` — technically a **wrong** reconstruction of
   the true title (extra backslashes appear that weren't in the original string),
   but not an exception.
2. If xprop somehow did not escape at all (hypothetical worst case) — e.g. `"she
   said "hi""` — `startswith('"')`/`endswith('"')` still both hold on the outer
   characters, so `value[1:-1]` still executes cleanly, returning `'she said
   "hi"'` — again just an inexact reconstruction, not an exception.
3. Degenerate case, value is a single `"` character: length guard
   (`len(value) >= 2`) prevents indexing past the string; falls through to
   `return None` cleanly.

All three demonstrated in Python directly against the real functions with no
exception raised in any case.

**Verdict: harmless `None`/wrong-string, never an exception.** The parsed value
from `active_window_title` is only ever used in a strict `==` comparison against
our own `ccnav:<session>`-format title inside `_wait_for_focus`. A mis-parsed
*other* window's title (the one that happens to be focused when we probe) can at
worst produce a false *mismatch* against our target (which is already the correct
outcome when the focused window isn't ours) — it can never spuriously equal our
own `ccnav:` title, since our titles never contain quotes to begin with. So a
hostile title on the user's desktop cannot crash the GTK main loop and cannot
cause `activate_window_titled` to report a false success.

## Files changed

- Created: `/data/playground/cc_navigator/src/ccnav/gnome.py`
- Created: `/data/playground/cc_navigator/tests/test_gnome.py`
- Commit: `eddd59f` "feat: activate window by title and verify focus through xprop"

## Self-review

- **Completeness:** all 20 tests present and passing; all 7 mutations attempted
  and reported; both read-only checks done with raw output pasted; both Step 7
  questions answered.
- **Quality:** code is verbatim from the plan; names (`_match_first_js`,
  `_wait_for_focus`, `_active_window_id`) match their single responsibilities.
- **Discipline (YAGNI):** no third retry, no exponential backoff, no window-id
  cache, no `gi` import in this module — confirmed by reading the final file back
  and diffing against the plan's code block. `activate_window_titled` retries
  exactly once, exactly as specified.
- **Testing hygiene:** no unit test spawns a real subprocess or touches the real
  desktop — every `test_gnome.py` test passes a `run=` (and where relevant
  `sleep=`) fake. The full-suite run (87 tests, 0.226s) is far too fast to be
  doing real I/O, which corroborates this. The only real subprocess/desktop
  interaction anywhere in this session was the three explicitly-approved
  read-only probes in Step 6, run directly from the shell, not through the test
  suite.
- **Safety:** confirmed above — no focus change, no window opened/closed/moved,
  no workspace switch. I did not attempt any additional experiment (e.g. spinning
  up a virtual/offscreen X window to empirically pin down xprop's exact
  quote-escaping behavior) beyond what Step 6 authorized, even though a headless
  Xvfb-backed test would have been technically safe — the brief said "do exactly
  these two, and nothing else," so Step 7b relies on reasoning plus canned-string
  demonstration rather than a live experiment.

## Issues or concerns

1. **Mutation 5 is a real, if narrow, gap** in the plan's test suite: `eval_js`
   has no direct test class, and no existing test pairs a nonzero exit code with
   stdout that would independently parse as `(true, ...)`. Reported per
   instructions; not fixed, not added to, without asking first.
2. **Step 7a's ~3 second worst-case blocking time** is a real UX risk once Task
   10 wires this into a GTK callback (main-loop freeze on a failed activation).
   Flagging per instructions; not fixed here.
3. I could not empirically confirm xprop's exact quote-escaping behavior for
   embedded `"` characters without either touching the real desktop (which the
   brief forbade beyond the two named probes) or standing up additional X
   infrastructure not authorized by Step 6. My Step 7b answer is therefore backed
   by reasoning over both plausible xprop behaviors (escaped and unescaped) — in
   both cases the conclusion (no exception, functionally harmless) holds, so the
   uncertainty does not change the verdict, but I want that caveat on record.
