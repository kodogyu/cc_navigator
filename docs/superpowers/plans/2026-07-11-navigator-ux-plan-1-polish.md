# cc_navigator UX Plan 1 (polish: settings, collapse, refresh, detail, install) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship features 1–5 of `docs/superpowers/specs/2026-07-11-navigator-ux-features-design.md`: window transparency + background colour + version display, a collapse-to-titlebar toggle, automatic (SessionEnd) plus manual (button) removal of ended sessions, an expandable per-row detail view, and an `install` script with launcher/autostart/hook-wiring toggles in settings.

**Architecture:** Extend the existing pure `config.Settings` model and the `.ccnav` CSS provider for appearance; add small pure helpers to `statestore` and `hook` for deletion and prompt capture; add a new pure `wiring.py` for desktop/settings-file wiring; keep all GTK in `ui.py` and threading in `app.py`. Every new piece follows the codebase's rule: pure logic is I/O-free and unit-tested without GTK/filesystem, and nothing raises on hostile input.

**Tech Stack:** Python 3.8 (`/usr/bin/python3` only), stdlib + system PyGObject/GTK3, zero third-party deps. Tests via `./run-tests` (unittest discover).

## Global Constraints

- `/usr/bin/python3` ONLY — Anaconda's `python3` has no `gi`. All test commands use it.
- Every `src/ccnav/*.py` module starts with `from __future__ import annotations`.
- No `match` statement; no `X | Y` runtime type annotations (use `Optional[...]`, `Dict[...]`, etc.).
- Zero third-party dependencies; stdlib + system GTK3 only.
- Pure logic must never raise on hostile/garbage input — coerce or degrade, never crash.
- GTK-touching tests are guarded with `@unittest.skipUnless(os.environ.get("DISPLAY"), "needs an X11 display")`.
- Atomic file writes use the tempfile-in-same-dir + `os.replace` pattern already in `statestore`/`config`.
- Run the full suite with `./run-tests`. Run one module with:
  `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.<module> -v`
- Commit after every task. End commit messages with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Reverse-DNS app id (verbatim): `io.github.kodogyu.CcNavigator`.
- Canonical hook events + matchers (verbatim): `SessionStart`=`""`, `UserPromptSubmit`=`""`,
  `Notification`=`""`, `Stop`=`""`, `SessionEnd`=`""`, `PreToolUse`=`"AskUserQuestion|ExitPlanMode"`.
- Version string (verbatim): `0.1.0`.

---

## Task 1: Settings — opacity + background colour

**Files:**
- Modify: `src/ccnav/config.py` (add constants, two `Settings` fields, extend `to_dict`/`_coerce`)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.Settings(opacity: float = 1.0, bg_color: str = "")`; module constants
  `OPACITY_MIN = 0.3`, `OPACITY_MAX = 1.0`; coercion in `from_dict`/`with_updates` (existing entry points).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py` inside `FromDictCoercionTest` (new methods) and a small block in `DefaultsTest`:

```python
    def test_appearance_defaults(self):
        s = config.Settings()
        self.assertEqual(s.opacity, 1.0)
        self.assertEqual(s.bg_color, "")

    def test_opacity_is_clamped_and_garbage_ignored(self):
        self.assertEqual(config.from_dict({"opacity": 0.0}).opacity, config.OPACITY_MIN)
        self.assertEqual(config.from_dict({"opacity": 5}).opacity, config.OPACITY_MAX)
        self.assertEqual(config.from_dict({"opacity": "clear"}).opacity, 1.0)  # default
        self.assertEqual(config.from_dict({"opacity": float("nan")}).opacity, 1.0)

    def test_bg_color_accepts_only_hex_rrggbb(self):
        self.assertEqual(config.from_dict({"bg_color": "#1a2b3c"}).bg_color, "#1a2b3c")
        self.assertEqual(config.from_dict({"bg_color": "red"}).bg_color, "")
        self.assertEqual(config.from_dict({"bg_color": "#fff"}).bg_color, "")
        self.assertEqual(config.from_dict({"bg_color": "#12345g"}).bg_color, "")
        self.assertEqual(config.from_dict({"bg_color": 123}).bg_color, "")
```

Also extend the existing `test_a_full_valid_dict_round_trips` raw dict to include the two new keys so `to_dict()` round-trips:

```python
        raw = {
            "poll_seconds": 2.5, "corner": "bottom-left", "width": 500,
            "height": 600, "keep_above": False, "all_workspaces": False,
            "font_size": 14, "opacity": 0.8, "bg_color": "#101010",
        }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_config -v`
Expected: FAIL (`AttributeError: ... 'opacity'` / `module 'ccnav.config' has no attribute 'OPACITY_MIN'`).

- [ ] **Step 3: Implement**

In `src/ccnav/config.py`, add constants near the other ranges:

```python
OPACITY_MIN, OPACITY_MAX = 0.3, 1.0
# A background colour is either "" (no override, keep the theme) or a #rrggbb hex.
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
```

Add `import re` at the top with the other imports. Add two fields to the `Settings` dataclass (after `font_size`):

```python
    opacity: float = 1.0
    bg_color: str = ""  # "" = no override, keep the theme
```

Extend `to_dict` to include them:

```python
            "opacity": self.opacity,
            "bg_color": self.bg_color,
```

In `_coerce`, before the `return Settings(...)`, add:

```python
    opacity = _clamp(_as_number(raw.get("opacity"), base.opacity), OPACITY_MIN, OPACITY_MAX)

    bg = raw.get("bg_color")
    bg = bg if isinstance(bg, str) and _HEX_RE.match(bg) else base.bg_color
```

and pass `opacity=opacity, bg_color=bg` into the `Settings(...)` constructor call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_config -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/config.py tests/test_config.py
git commit -m "feat: add opacity and background-colour settings"
```

---

## Task 2: Version constant + `cc-navigator --version`

**Files:**
- Modify: `src/ccnav/__init__.py` (add `__version__`)
- Modify: `src/ccnav/app.py` (handle `--version` in `main`)
- Modify: `bin/cc-navigator` (forward args)
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `ccnav.__version__ == "0.1.0"`; `app.main(argv=None)` prints the version and returns 0 when
  `argv` contains `--version`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py` (new test class near the top-level tests):

```python
class VersionTest(unittest.TestCase):
    def test_version_flag_prints_and_exits_zero(self):
        import io
        import contextlib
        from ccnav import __version__
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = app.main(["--version"])
        self.assertEqual(code, 0)
        self.assertIn(__version__, buf.getvalue())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_app.VersionTest -v`
Expected: FAIL (`main() takes 0 positional arguments` / `cannot import name '__version__'`).

- [ ] **Step 3: Implement**

`src/ccnav/__init__.py` (whole file):

```python
__version__ = "0.1.0"
```

In `src/ccnav/app.py`, change `main` to accept argv and short-circuit `--version` BEFORE creating any GTK objects:

```python
def main(argv=None) -> int:
    import sys
    from . import __version__
    argv = sys.argv[1:] if argv is None else argv
    if "--version" in argv:
        print("cc-navigator %s" % __version__)
        return 0
    application = Application()
    # Wired here, not in NavigatorWindow: this is where the main loop exists.
    application.window.connect("destroy", Gtk.main_quit)
    application.window.show_all()
    Gtk.main()
    application.stop()
    return 0
```

In `bin/cc-navigator`, forward args to the module — change the last line to:

```sh
PYTHONPATH="$here/../src${PYTHONPATH:+:$PYTHONPATH}" exec /usr/bin/python3 -m ccnav.app "$@"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_app.VersionTest -v`
Expected: PASS. Also verify the launcher: `./bin/cc-navigator --version` prints `cc-navigator 0.1.0`.

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/__init__.py src/ccnav/app.py bin/cc-navigator tests/test_app.py
git commit -m "feat: expose __version__ and a --version flag"
```

---

## Task 3: Apply opacity + background colour to the live window

**Files:**
- Modify: `src/ccnav/ui.py` (`apply_settings`, replace `_apply_font` with `_apply_css`)
- Test: `tests/test_ui.py` (update the existing font test, add opacity/colour tests)

**Interfaces:**
- Consumes: `config.Settings.opacity`, `config.Settings.bg_color`, `config.Settings.font_size`.
- Produces: `NavigatorWindow._apply_css(self, settings)` builds one `.ccnav` rule set from
  `bg_color` + `font_size`; `apply_settings` additionally calls `self.set_opacity(settings.opacity)`.
  `_apply_font` is removed.

- [ ] **Step 1: Write/adjust the failing tests**

In `tests/test_ui.py`, replace `test_font_size_writes_scoped_css_and_zero_clears_it` with:

```python
    def test_css_carries_font_and_background(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.Settings(font_size=15, bg_color="#123456"),
        )
        try:
            css = window._css.to_string()
            self.assertIn("15pt", css)
            self.assertIn("#123456", css)
            self.assertIn("background-color", css)
            # Clearing both means an empty provider.
            window.apply_settings(config.Settings(font_size=0, bg_color=""))
            self.assertNotIn("pt", window._css.to_string())
            self.assertNotIn("background-color", window._css.to_string())
        finally:
            window.destroy()

    def test_opacity_is_applied(self):
        from ccnav import config
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.Settings(opacity=0.6),
        )
        try:
            self.assertAlmostEqual(window.get_opacity(), 0.6, places=2)
        finally:
            window.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_ui -v`
Expected: FAIL (background-color not in CSS; opacity not applied).

- [ ] **Step 3: Implement**

In `src/ccnav/ui.py`, replace the `_apply_font` method with `_apply_css`:

```python
    def _apply_css(self, settings: "config.Settings") -> None:
        """Scale the panel's font and tint its background via the scoped provider.
        Both are optional: font_size 0 and bg_color "" each omit their rule, and
        an empty provider restores the theme. Scoped to .ccnav so no other app is
        touched."""
        parts = []
        if settings.bg_color:
            parts.append(".ccnav { background-color: %s; }" % settings.bg_color)
        if settings.font_size > 0:
            parts.append(".ccnav, .ccnav * { font-size: %dpt; }" % settings.font_size)
        self._css.load_from_data("\n".join(parts).encode("utf-8"))
```

In `apply_settings`, replace the line `self._apply_font(settings.font_size)` with:

```python
        self.set_opacity(settings.opacity)
        self._apply_css(settings)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_ui -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/ui.py tests/test_ui.py
git commit -m "feat: apply opacity and background colour to the panel"
```

---

## Task 4: Appearance controls in the settings dialog (colour, opacity, version)

**Files:**
- Modify: `src/ccnav/ui.py` (`_build_settings_dialog` — add colour, opacity, version footer)
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `config.with_updates`, `ccnav.__version__`.
- Produces: dialog controls that live-apply via `_commit_settings` (unchanged signature).

- [ ] **Step 1: Write the failing test**

In `tests/test_ui.py` `SettingsUiTest`, add:

```python
    def test_dialog_shows_version_and_commits_colour(self):
        import tempfile, pathlib
        from ccnav import config, __version__
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            settings=config.Settings(),
        )
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        orig = config.config_path
        config.config_path = lambda: pathlib.Path(tmp.name) / "c.json"
        try:
            window._commit_settings(config.with_updates(window._settings, bg_color="#abcdef"))
            self.assertIn("#abcdef", window._css.to_string())
            dialog = window._build_settings_dialog()
            try:
                # The version string appears somewhere in the dialog's labels.
                found = []
                def walk(w):
                    if isinstance(w, Gtk.Label):
                        found.append(w.get_text())
                    if isinstance(w, Gtk.Container):
                        for c in w.get_children():
                            walk(c)
                walk(dialog.get_content_area())
                self.assertTrue(any(__version__ in t for t in found))
            finally:
                dialog.destroy()
        finally:
            config.config_path = orig
            window.destroy()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_ui.SettingsUiTest.test_dialog_shows_version_and_commits_colour -v`
Expected: FAIL (no version label in the dialog).

- [ ] **Step 3: Implement**

In `_build_settings_dialog`, after the font row block and before the keep-above/all-workspaces toggles, add background colour + opacity controls:

```python
        # Background colour: a colour button plus a "테마 그대로" clear button.
        add_label("배경색")
        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        color_btn = Gtk.ColorButton()
        if s.bg_color:
            rgba = Gdk.RGBA()
            rgba.parse(s.bg_color)
            color_btn.set_rgba(rgba)

        def on_color(btn):
            rgba = btn.get_rgba()
            hexcolor = "#%02x%02x%02x" % (
                int(round(rgba.red * 255)), int(round(rgba.green * 255)),
                int(round(rgba.blue * 255)))
            self._commit_settings(config.with_updates(self._settings, bg_color=hexcolor))

        clear_btn = Gtk.Button(label="테마 그대로")

        def on_clear(_b):
            self._commit_settings(config.with_updates(self._settings, bg_color=""))

        color_btn.connect("color-set", on_color)
        clear_btn.connect("clicked", on_clear)
        color_box.pack_start(color_btn, False, False, 0)
        color_box.pack_start(clear_btn, False, False, 0)
        grid.attach(color_box, 1, row, 1, 1)
        row += 1

        # Opacity.
        add_label("투명도")
        opacity = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, config.OPACITY_MIN, config.OPACITY_MAX, 0.05)
        opacity.set_value(s.opacity)
        opacity.set_hexpand(True)
        opacity.connect("value-changed", lambda w: self._commit_settings(
            config.with_updates(self._settings, opacity=w.get_value())))
        grid.attach(opacity, 1, row, 1, 1)
        row += 1
```

After the grid is attached to `content` and before `dialog.show_all()`, add the version footer:

```python
        from . import __version__
        footer = Gtk.Label(label="cc-navigator v%s" % __version__, xalign=1.0)
        footer.get_style_context().add_class("dim-label")
        content.add(footer)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_ui -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/ui.py tests/test_ui.py
git commit -m "feat: colour, opacity, and version in the settings dialog"
```

---

## Task 5: Collapse-to-titlebar toggle

**Files:**
- Modify: `src/ccnav/ui.py` (HeaderBar toggle + collapse logic; keep a reference to the content box)
- Test: `tests/test_ui.py`

**Interfaces:**
- Produces: `NavigatorWindow.set_collapsed(collapsed: bool)` and a HeaderBar toggle that drives it.
  Collapsed hides the content box and shrinks the window; expanded restores it.

- [ ] **Step 1: Write the failing test**

In `tests/test_ui.py` `NavigatorWindowTest`, add:

```python
    def test_collapse_hides_content_and_expand_restores(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row()])
            self.assertTrue(window._content.get_visible())
            window.set_collapsed(True)
            self.assertFalse(window._content.get_visible())
            window.set_collapsed(False)
            self.assertTrue(window._content.get_visible())
        finally:
            window.destroy()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_ui.NavigatorWindowTest.test_collapse_hides_content_and_expand_restores -v`
Expected: FAIL (`_content` / `set_collapsed` missing).

- [ ] **Step 3: Implement**

In `__init__`, keep a reference to the content box — change `self.add(box)` to:

```python
        self._content = box
        self.add(box)
```

Add a collapse toggle to the HeaderBar. In `__init__`, right before `self.set_titlebar(header)`, add:

```python
        collapse = Gtk.ToggleButton()
        collapse.set_relief(Gtk.ReliefStyle.NONE)
        collapse.add(Gtk.Image.new_from_icon_name("pan-up-symbolic", Gtk.IconSize.BUTTON))
        collapse.set_tooltip_text("접기")
        collapse.connect("toggled", self._on_collapse_toggled)
        header.pack_start(collapse)
        self._collapse_button = collapse
```

Add the methods:

```python
    def _on_collapse_toggled(self, button: Gtk.ToggleButton) -> None:
        self.set_collapsed(button.get_active())

    def set_collapsed(self, collapsed: bool) -> None:
        """Collapsed hides the body and shrinks the window to its titlebar; the
        panel stays floating and one click brings the list back."""
        image = self._collapse_button.get_child()
        if collapsed:
            self._content.hide()
            image.set_from_icon_name("pan-down-symbolic", Gtk.IconSize.BUTTON)
            self.resize(self._settings.width, 1)  # shrink to titlebar's minimum
        else:
            self._content.show()
            image.set_from_icon_name("pan-up-symbolic", Gtk.IconSize.BUTTON)
            self.resize(self._settings.width, self._settings.height)
        if self._collapse_button.get_active() != collapsed:
            self._collapse_button.set_active(collapsed)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_ui -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/ui.py tests/test_ui.py
git commit -m "feat: collapse the panel to its titlebar"
```

---

## Task 6: `statestore.remove` and `statestore.read_one`

**Files:**
- Modify: `src/ccnav/statestore.py`
- Test: `tests/test_statestore.py`

**Interfaces:**
- Produces:
  - `statestore.remove(state_dir: pathlib.Path, session_id: str) -> bool` — deletes
    `<session_id>.json`; returns True iff a file was removed; tolerates a missing file and an
    unsafe id (returns False, never raises).
  - `statestore.read_one(state_dir: pathlib.Path, session_id: str) -> Optional[dict]` — returns the
    parsed record, or `None` on missing/garbage/unsafe id.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_statestore.py` (create the file if it does not exist; it does — append a class):

```python
class RemoveAndReadOneTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def _write(self, sid, rec):
        statestore.write(self.dir, dict(rec, session_id=sid))

    def test_remove_deletes_and_reports_true(self):
        self._write("abc", {"state": "working"})
        self.assertTrue(statestore.remove(self.dir, "abc"))
        self.assertFalse((self.dir / "abc.json").exists())

    def test_remove_missing_is_false_not_error(self):
        self.assertFalse(statestore.remove(self.dir, "nope"))

    def test_remove_rejects_unsafe_id(self):
        self.assertFalse(statestore.remove(self.dir, "../etc/passwd"))

    def test_read_one_returns_record(self):
        self._write("abc", {"state": "waiting", "last_prompt": "hi"})
        rec = statestore.read_one(self.dir, "abc")
        self.assertEqual(rec["last_prompt"], "hi")

    def test_read_one_missing_is_none(self):
        self.assertIsNone(statestore.read_one(self.dir, "gone"))

    def test_read_one_garbage_is_none(self):
        (self.dir / "bad.json").write_text("{ not json")
        self.assertIsNone(statestore.read_one(self.dir, "bad"))

    def test_read_one_unsafe_id_is_none(self):
        self.assertIsNone(statestore.read_one(self.dir, "../x"))
```

Ensure the test file imports `tempfile`, `pathlib`, `unittest`, and `from ccnav import statestore` (add any missing).

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_statestore -v`
Expected: FAIL (`module 'ccnav.statestore' has no attribute 'remove'`).

- [ ] **Step 3: Implement**

Add to `src/ccnav/statestore.py` (after `read_all`):

```python
def read_one(state_dir: pathlib.Path, session_id: str) -> Optional[Dict[str, object]]:
    """Read one session's record, or None on missing/garbage/unsafe id. Never
    raises: the hook uses this to carry a prior field forward and must degrade
    to 'no previous record' rather than fail."""
    if not is_safe_session_id(session_id):
        return None
    try:
        text = (state_dir / (session_id + ".json")).read_text()
    except OSError:
        return None
    try:
        record = json.loads(text)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def remove(state_dir: pathlib.Path, session_id: str) -> bool:
    """Delete one session's state file. Returns True iff a file was removed.
    Tolerates a missing file, an unsafe id, and an undeletable file (returns
    False) -- the SessionEnd hook must never raise back into Claude Code."""
    if not is_safe_session_id(session_id):
        return False
    return _try_unlink(state_dir / (session_id + ".json"))
```

`_try_unlink` and `Optional` are already imported/defined in the module.

- [ ] **Step 4: Run tests to verify they pass**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_statestore -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/statestore.py tests/test_statestore.py
git commit -m "feat: statestore.remove and read_one"
```

---

## Task 7: Hook — SessionEnd deletes the state file

**Files:**
- Modify: `src/ccnav/hook.py` (`main` routes SessionEnd to `statestore.remove`)
- Test: `tests/test_hook.py`

**Interfaces:**
- Consumes: `statestore.remove`.
- Produces: on a `SessionEnd` payload, `hook.main` deletes `<session_id>.json` and returns 0 without
  writing; other events keep the existing build-record-and-write path.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_hook.py`:

```python
class SessionEndTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)
        self._orig = paths.ensure_state_dir
        paths.ensure_state_dir = lambda: self.dir

    def tearDown(self):
        paths.ensure_state_dir = self._orig

    def _run(self, payload, env):
        stdin = io.StringIO(json.dumps(payload))
        orig_stdin, sys.stdin = sys.stdin, stdin
        orig_env = os.environ.copy()
        os.environ.clear(); os.environ.update(env)
        try:
            return hook.main()
        finally:
            sys.stdin = orig_stdin
            os.environ.clear(); os.environ.update(orig_env)

    def test_session_end_deletes_the_state_file(self):
        statestore.write(self.dir, {"session_id": "s1", "state": "waiting",
                                    "tmux_socket": "/x", "tmux_pane": "%1"})
        code = self._run(
            {"session_id": "s1", "hook_event_name": "SessionEnd", "source": "logout"},
            {"TMUX": "/x,1,0", "TMUX_PANE": "%1"})
        self.assertEqual(code, 0)
        self.assertFalse((self.dir / "s1.json").exists())

    def test_session_end_without_pane_still_deletes(self):
        statestore.write(self.dir, {"session_id": "s2", "state": "waiting",
                                    "tmux_socket": "/x", "tmux_pane": "%1"})
        code = self._run(
            {"session_id": "s2", "hook_event_name": "SessionEnd", "source": "clear"},
            {})  # no TMUX in a background session
        self.assertEqual(code, 0)
        self.assertFalse((self.dir / "s2.json").exists())
```

Ensure the test file imports `io, json, os, sys, tempfile, pathlib, unittest` and `from ccnav import hook, paths, statestore`.

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_hook.SessionEndTest -v`
Expected: FAIL (the file is not deleted — SessionEnd currently classifies to None and writes nothing, leaving the old file in place).

- [ ] **Step 3: Implement**

In `src/ccnav/hook.py` `main`, after parsing `payload` and the `isinstance(payload, dict)` guard, add the SessionEnd branch before `build_record`:

```python
    if payload.get("hook_event_name") == "SessionEnd":
        session_id = str(payload.get("session_id") or "")
        try:
            statestore.remove(paths.ensure_state_dir(), session_id)
        except Exception:
            pass  # a broken navigator must never break Claude Code
        return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_hook -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/hook.py tests/test_hook.py
git commit -m "feat: SessionEnd hook removes the session's state file"
```

---

## Task 8: Manual refresh — `Application.refresh` + HeaderBar button

**Files:**
- Modify: `src/ccnav/app.py` (`refresh` method; pass `on_refresh` to the window)
- Modify: `src/ccnav/ui.py` (`__init__` gains `on_refresh`; HeaderBar refresh button)
- Test: `tests/test_app.py`, `tests/test_ui.py`

**Interfaces:**
- Produces: `Application.refresh()` sets `self._wake`; `NavigatorWindow.__init__(..., on_refresh=None)`
  wires a HeaderBar refresh button to it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`:

```python
class RefreshTest(unittest.TestCase):
    def test_refresh_wakes_the_poll_thread(self):
        instance = app.Application.__new__(app.Application)
        instance._wake = threading.Event()
        app.Application.refresh(instance)
        self.assertTrue(instance._wake.is_set())
```

Add to `tests/test_ui.py` `NavigatorWindowTest`:

```python
    def test_refresh_button_calls_back(self):
        called = []
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None,
            on_refresh=lambda: called.append(True))
        try:
            window._refresh_button.clicked()
            self.assertEqual(called, [True])
        finally:
            window.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_app.RefreshTest tests.test_ui.NavigatorWindowTest.test_refresh_button_calls_back -v`
Expected: FAIL (`refresh` / `_refresh_button` / `on_refresh` missing).

- [ ] **Step 3: Implement**

In `src/ccnav/ui.py`, extend `__init__`'s signature and store the callback:

```python
        on_refresh: Callable[[], None] = None,
```

(add the parameter after `on_settings_changed`), and near the other `self._on_*` assignments:

```python
        self._on_refresh = on_refresh
```

In `__init__`, before `self.set_titlebar(header)`, add a refresh button next to the gear:

```python
        refresh = Gtk.Button()
        refresh.set_relief(Gtk.ReliefStyle.NONE)
        refresh.add(Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON))
        refresh.set_tooltip_text("새로고침")
        refresh.connect("clicked", self._on_refresh_clicked)
        header.pack_end(refresh)
        self._refresh_button = refresh
```

Add the handler:

```python
    def _on_refresh_clicked(self, _button) -> None:
        if self._on_refresh is not None:
            self._on_refresh()
```

In `src/ccnav/app.py`, add the method to `Application`:

```python
    def refresh(self) -> None:
        """Force an immediate poll: wake the poll thread so collect_rows re-runs
        now, pruning any pane already gone from tmux. Inherits the poll loop's
        survive-a-raising-collect behaviour."""
        self._wake.set()
```

and pass it to the window in `__init__` (add to the `ui.NavigatorWindow(...)` call):

```python
            on_refresh=self.refresh,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./run-tests`
Expected: PASS (full suite; confirms the new `__init__` param did not break existing window constructions).

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/app.py src/ccnav/ui.py tests/test_app.py tests/test_ui.py
git commit -m "feat: manual refresh button forces an immediate poll"
```

---

## Task 9: Hook captures `last_prompt` (carry-forward) + `model.Row.last_prompt`

**Files:**
- Modify: `src/ccnav/hook.py` (`build_record` gains `previous`; `main` reads prior record; `PROMPT_LIMIT`)
- Modify: `src/ccnav/model.py` (`Row.last_prompt`; `build_rows` sets it)
- Test: `tests/test_hook.py`, `tests/test_model.py`

**Interfaces:**
- Produces:
  - `hook.build_record(payload, env, now, previous=None)` — on `UserPromptSubmit` sets
    `last_prompt = (user_prompt or prompt)[:PROMPT_LIMIT]`; otherwise carries
    `previous.get("last_prompt", "")` forward. `PROMPT_LIMIT = 300`.
  - `model.Row.last_prompt: str = ""`, populated by `build_rows` from `rec.get("last_prompt")`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_hook.py`:

```python
class LastPromptTest(unittest.TestCase):
    ENV = {"TMUX": "/x,1,0", "TMUX_PANE": "%1"}

    def test_user_prompt_is_captured_and_truncated(self):
        rec = hook.build_record(
            {"session_id": "s", "hook_event_name": "UserPromptSubmit",
             "user_prompt": "x" * 500}, self.ENV, 1)
        self.assertEqual(rec["last_prompt"], "x" * hook.PROMPT_LIMIT)

    def test_falls_back_to_prompt_field(self):
        rec = hook.build_record(
            {"session_id": "s", "hook_event_name": "UserPromptSubmit",
             "prompt": "hello"}, self.ENV, 1)
        self.assertEqual(rec["last_prompt"], "hello")

    def test_prompt_is_carried_forward_across_a_promptless_event(self):
        rec = hook.build_record(
            {"session_id": "s", "hook_event_name": "Stop"}, self.ENV, 1,
            previous={"last_prompt": "earlier"})
        self.assertEqual(rec["last_prompt"], "earlier")

    def test_no_previous_and_no_prompt_is_empty(self):
        rec = hook.build_record(
            {"session_id": "s", "hook_event_name": "Stop"}, self.ENV, 1)
        self.assertEqual(rec["last_prompt"], "")
```

Add a standalone class to `tests/test_model.py` (it already imports `unittest` and
`from ccnav import ... model`):

```python
class LastPromptRowTest(unittest.TestCase):
    def test_build_rows_carries_last_prompt(self):
        records = [{"session_id": "s", "tmux_socket": "/x", "tmux_pane": "%1",
                    "state": "working", "updated_at": 1, "last_prompt": "do X"}]
        rows = model.build_rows(records, {"/x": {"%1": "sess"}}, {"/x": {"%1": "t"}})
        self.assertEqual(rows[0].last_prompt, "do X")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_hook.LastPromptTest tests.test_model -v`
Expected: FAIL (`build_record() got an unexpected keyword argument 'previous'`; `Row` has no `last_prompt`).

- [ ] **Step 3: Implement**

In `src/ccnav/hook.py`, add near `MESSAGE_LIMIT`:

```python
PROMPT_LIMIT = 300
```

Change `build_record`'s signature and body. New signature:

```python
def build_record(
    payload: Dict[str, object], env: Mapping[str, str], now: int,
    previous: Optional[Dict[str, object]] = None,
) -> Optional[Dict[str, object]]:
```

Inside, after `state, reason = classified` and before the `return {...}`, compute `last_prompt`:

```python
    if payload.get("hook_event_name") == "UserPromptSubmit":
        prompt = payload.get("user_prompt")
        if not isinstance(prompt, str):
            prompt = payload.get("prompt")
        last_prompt = str(prompt or "")[:PROMPT_LIMIT]
    else:
        last_prompt = str((previous or {}).get("last_prompt") or "")
```

Add `"last_prompt": last_prompt,` to the returned dict.

In `main`, read the prior record and pass it through — change the `record = build_record(...)` line to:

```python
    state_dir = paths.ensure_state_dir()
    session_id = str(payload.get("session_id") or "")
    previous = statestore.read_one(state_dir, session_id)
    record = build_record(payload, os.environ, int(time.time()), previous)
```

and change the following `statestore.write(paths.ensure_state_dir(), record)` to `statestore.write(state_dir, record)`.

In `src/ccnav/model.py`, add a field to `Row` (after `updated_at`):

```python
    last_prompt: str = ""
```

and in `build_rows`, add to the `Row(...)` construction:

```python
                last_prompt=str(rec.get("last_prompt") or ""),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./run-tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/hook.py src/ccnav/model.py tests/test_hook.py tests/test_model.py
git commit -m "feat: capture last prompt in the hook and carry it in Row"
```

---

## Task 10: Detail view — expandable row + window grow

**Files:**
- Modify: `src/ccnav/ui.py` (`_build_row` adds a detail block; row re-click collapses; window grows/shrinks)
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `model.Row.last_prompt`, `Row.cwd`, `Row.state`, `Row.reason`, `Row.updated_at`.
- Produces: the revealed area of a row now includes a detail block; clicking the selected row again
  deselects (collapses) it.

- [ ] **Step 1: Write the failing test**

In `tests/test_ui.py` `NavigatorWindowTest`, add:

```python
    def test_expanded_row_shows_the_last_prompt_and_path(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(cwd="/data/projects/demo", session_id="a")])
            child = window._listbox.get_children()[0]
            window._listbox.select_row(child)
            texts = []
            def walk(w):
                if isinstance(w, Gtk.Label):
                    texts.append(w.get_text())
                if isinstance(w, Gtk.Container):
                    for c in w.get_children():
                        walk(c)
            walk(child)
            joined = " ".join(texts)
            self.assertIn("/data/projects/demo", joined)
        finally:
            window.destroy()

    def test_reclicking_selected_row_collapses_it(self):
        window = ui.NavigatorWindow(on_jump=lambda r: None, on_send=lambda r, t: None)
        try:
            window.set_rows([row(session_id="a")])
            child = window._listbox.get_children()[0]
            window._listbox.select_row(child)
            self.assertIsNotNone(window._listbox.get_selected_row())
            window._on_row_activated(window._listbox, child)  # re-click
            self.assertIsNone(window._listbox.get_selected_row())
        finally:
            window.destroy()
```

No change to the `row()` helper is needed: `model.Row.last_prompt` (Task 9) defaults to `""`, and
these tests exercise the `cwd` path and the re-click collapse, not a non-empty prompt.

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_ui.NavigatorWindowTest.test_expanded_row_shows_the_last_prompt_and_path -v`
Expected: FAIL (no detail labels; `_on_row_activated` missing).

- [ ] **Step 3: Implement**

In `_build_row`, build a detail block and put it inside the revealer above the actions. Replace the
`revealer = Gtk.Revealer()` / `revealer.add(actions)` lines with:

```python
        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        path_label = Gtk.Label(xalign=0.0)
        path_label.set_markup(
            '<small><span foreground="#77767b">%s</span></small>'
            % GLib.markup_escape_text(row.cwd))
        path_label.set_selectable(True)
        path_label.set_line_wrap(True)
        detail.pack_start(path_label, False, False, 0)

        if row.last_prompt:
            prompt_label = Gtk.Label(xalign=0.0)
            prompt_label.set_markup(
                "<small>%s</small>" % GLib.markup_escape_text(row.last_prompt))
            prompt_label.set_line_wrap(True)
            prompt_label.set_lines(3)
            prompt_label.set_ellipsize(Pango.EllipsizeMode.END)
            detail.pack_start(prompt_label, False, False, 0)

        meta = Gtk.Label(xalign=0.0)
        state_line = row.state + (" · " + row.reason if row.reason else "")
        meta.set_markup(
            '<small><span foreground="#77767b">%s</span></small>'
            % GLib.markup_escape_text(state_line))
        detail.pack_start(meta, False, False, 0)

        reveal_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        reveal_body.pack_start(detail, False, False, 0)
        reveal_body.pack_start(actions, False, False, 0)

        revealer = Gtk.Revealer()
        revealer.add(reveal_body)
```

Wire row re-click to collapse. In `__init__`, after connecting `row-selected`, also connect
`row-activated`:

```python
        self._listbox.connect("row-activated", self._on_row_activated)
```

Add the handler:

```python
    def _on_row_activated(self, listbox, activated) -> None:
        """A click on the already-selected row collapses it (deselects). GTK's
        single-click select does not toggle, so we do it here."""
        if listbox.get_selected_row() is activated:
            listbox.unselect_row(activated)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./run-tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/ui.py tests/test_ui.py
git commit -m "feat: expandable per-row detail view with the last prompt"
```

---

## Task 11: `install` script (symlink onto PATH)

**Files:**
- Create: `install`
- Test: `tests/test_install.py`

**Interfaces:**
- Produces: `./install` creates `~/.local/bin/cc-navigator` → repo `bin/cc-navigator` (idempotent),
  honouring `$HOME`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_install.py`:

```python
import os
import pathlib
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]


class InstallScriptTest(unittest.TestCase):
    def _run(self, home):
        env = dict(os.environ, HOME=str(home))
        return subprocess.run(["sh", str(REPO / "install")], env=env,
                              capture_output=True, text=True)

    def test_creates_symlink_on_path(self):
        with tempfile.TemporaryDirectory() as home:
            r = self._run(home)
            self.assertEqual(r.returncode, 0, r.stderr)
            link = pathlib.Path(home) / ".local" / "bin" / "cc-navigator"
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.path.realpath(link), str(REPO / "bin" / "cc-navigator"))

    def test_is_idempotent(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(self._run(home).returncode, 0)
            self.assertEqual(self._run(home).returncode, 0)  # second run must not fail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_install -v`
Expected: FAIL (`install` does not exist → nonzero returncode).

- [ ] **Step 3: Implement**

Create `install` (POSIX sh):

```sh
#!/bin/sh
# Put cc-navigator on PATH: symlink the launcher into ~/.local/bin. Idempotent.
# The app-list launcher, login autostart, and Claude Code hook wiring are NOT
# done here -- they are toggles inside the app's settings (see wiring.py).
set -e
here=$(cd "$(dirname "$0")" && pwd)
target="$here/bin/cc-navigator"
bindir="${HOME}/.local/bin"
mkdir -p "$bindir"
ln -sf "$target" "$bindir/cc-navigator"
echo "installed: $bindir/cc-navigator -> $target"
case ":$PATH:" in
  *":$bindir:"*) ;;
  *) echo "note: $bindir is not on your PATH; add it to run 'cc-navigator' directly." ;;
esac
```

Make it executable: `chmod +x install`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_install -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add install tests/test_install.py
git commit -m "feat: install script symlinks cc-navigator onto PATH"
```

---

## Task 12: `wiring.py` — application launcher `.desktop`

**Files:**
- Create: `src/ccnav/wiring.py`
- Test: `tests/test_wiring.py`

**Interfaces:**
- Produces (all paths injectable for tests):
  - `wiring.APP_ID = "io.github.kodogyu.CcNavigator"`
  - `wiring.launcher_path(apps_dir=None) -> pathlib.Path`
  - `wiring.launcher_installed(apps_dir=None) -> bool`
  - `wiring.install_launcher(exec_path: str, apps_dir=None) -> None`
  - `wiring.remove_launcher(apps_dir=None) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wiring.py`:

```python
import pathlib
import tempfile
import unittest

from ccnav import wiring


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.apps = pathlib.Path(self._tmp.name)

    def test_install_then_detect_then_remove(self):
        self.assertFalse(wiring.launcher_installed(self.apps))
        wiring.install_launcher("/home/u/.local/bin/cc-navigator", self.apps)
        self.assertTrue(wiring.launcher_installed(self.apps))
        text = wiring.launcher_path(self.apps).read_text()
        self.assertIn("Exec=/home/u/.local/bin/cc-navigator", text)
        self.assertIn("Type=Application", text)
        self.assertTrue(wiring.remove_launcher(self.apps))
        self.assertFalse(wiring.launcher_installed(self.apps))

    def test_install_is_idempotent(self):
        wiring.install_launcher("/x/cc-navigator", self.apps)
        wiring.install_launcher("/x/cc-navigator", self.apps)  # must not raise
        self.assertTrue(wiring.launcher_installed(self.apps))

    def test_remove_missing_is_false(self):
        self.assertFalse(wiring.remove_launcher(self.apps))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_wiring -v`
Expected: FAIL (`No module named 'ccnav.wiring'`).

- [ ] **Step 3: Implement**

Create `src/ccnav/wiring.py`:

```python
"""System wiring: app-launcher, autostart, and Claude Code hook wiring.

Each action reads and writes external state a settings toggle drives. Every
path is injectable so the logic is tested without touching the real HOME.
Following well-known practice: freedesktop Desktop Entry / Autostart specs as
Syncthing/VS Code implement them (direct per-user file writes), and an
identity-based structural JSON merge for settings.json (npm pkg / VS Code
node-jsonc-parser style) -- see the spec's section 5.6.
"""
from __future__ import annotations

import os
import pathlib
import tempfile
from typing import Optional

APP_ID = "io.github.kodogyu.CcNavigator"

_DESKTOP = """[Desktop Entry]
Type=Application
Name=cc-navigator
Comment=Navigate Claude Code sessions
Exec=%(exec)s
Icon=utilities-terminal
Categories=Utility;Development;
Terminal=false
"""


def _default_apps_dir() -> pathlib.Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return pathlib.Path(base) / "applications"


def _atomic_write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def launcher_path(apps_dir: Optional[pathlib.Path] = None) -> pathlib.Path:
    return (apps_dir or _default_apps_dir()) / (APP_ID + ".desktop")


def launcher_installed(apps_dir: Optional[pathlib.Path] = None) -> bool:
    return launcher_path(apps_dir).exists()


def install_launcher(exec_path: str, apps_dir: Optional[pathlib.Path] = None) -> None:
    _atomic_write(launcher_path(apps_dir), _DESKTOP % {"exec": exec_path})


def remove_launcher(apps_dir: Optional[pathlib.Path] = None) -> bool:
    path = launcher_path(apps_dir)
    try:
        path.unlink()
        return True
    except OSError:
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_wiring -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/wiring.py tests/test_wiring.py
git commit -m "feat: wiring.py application launcher entry"
```

---

## Task 13: `wiring.py` — autostart with `X-GNOME-Autostart-enabled` toggle

**Files:**
- Modify: `src/ccnav/wiring.py`
- Test: `tests/test_wiring.py`

**Interfaces:**
- Produces:
  - `wiring.autostart_path(autostart_dir=None) -> pathlib.Path`
  - `wiring.autostart_enabled(autostart_dir=None) -> bool` (present AND not `X-GNOME-Autostart-enabled=false`)
  - `wiring.set_autostart(enabled: bool, exec_path: str, autostart_dir=None) -> None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_wiring.py`:

```python
class AutostartTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def test_enable_creates_enabled_entry(self):
        wiring.set_autostart(True, "/x/cc-navigator", self.dir)
        self.assertTrue(wiring.autostart_enabled(self.dir))
        text = wiring.autostart_path(self.dir).read_text()
        self.assertIn("X-GNOME-Autostart-enabled=true", text)

    def test_disable_flips_the_key_not_deletes(self):
        wiring.set_autostart(True, "/x/cc-navigator", self.dir)
        wiring.set_autostart(False, "/x/cc-navigator", self.dir)
        self.assertTrue(wiring.autostart_path(self.dir).exists())  # not deleted
        self.assertFalse(wiring.autostart_enabled(self.dir))
        self.assertIn("X-GNOME-Autostart-enabled=false",
                      wiring.autostart_path(self.dir).read_text())

    def test_absent_is_not_enabled(self):
        self.assertFalse(wiring.autostart_enabled(self.dir))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_wiring.AutostartTest -v`
Expected: FAIL (`module 'ccnav.wiring' has no attribute 'set_autostart'`).

- [ ] **Step 3: Implement**

Add to `src/ccnav/wiring.py`:

```python
def _default_autostart_dir() -> pathlib.Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    return pathlib.Path(base) / "autostart"


def autostart_path(autostart_dir: Optional[pathlib.Path] = None) -> pathlib.Path:
    return (autostart_dir or _default_autostart_dir()) / (APP_ID + ".desktop")


def autostart_enabled(autostart_dir: Optional[pathlib.Path] = None) -> bool:
    path = autostart_path(autostart_dir)
    try:
        text = path.read_text()
    except OSError:
        return False
    # Present counts as enabled unless the GNOME key explicitly disables it.
    return "X-GNOME-Autostart-enabled=false" not in text


def set_autostart(
    enabled: bool, exec_path: str, autostart_dir: Optional[pathlib.Path] = None
) -> None:
    flag = "true" if enabled else "false"
    text = _DESKTOP % {"exec": exec_path} + "X-GNOME-Autostart-enabled=%s\n" % flag
    _atomic_write(autostart_path(autostart_dir), text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_wiring -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/wiring.py tests/test_wiring.py
git commit -m "feat: autostart toggle via X-GNOME-Autostart-enabled"
```

---

## Task 14: `wiring.py` — settings.json hook merge/remove (identity-based)

**Files:**
- Modify: `src/ccnav/wiring.py`
- Test: `tests/test_wiring.py`

**Interfaces:**
- Produces:
  - `wiring.RECOMMENDED_HOOKS` — dict of event → matcher (the canonical set).
  - `wiring.hooks_installed(hook_command: str, settings_path: pathlib.Path) -> bool`
  - `wiring.install_hooks(hook_command: str, settings_path: pathlib.Path) -> None`
  - `wiring.remove_hooks(hook_command: str, settings_path: pathlib.Path) -> bool`
  - identity: a hook entry is "ours" iff its `command` equals `hook_command`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_wiring.py`:

```python
import json


class HooksMergeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = pathlib.Path(self._tmp.name) / ".claude" / "settings.json"
        self.cmd = "/repo/bin/cc-navigator-hook"

    def test_install_creates_every_recommended_event(self):
        wiring.install_hooks(self.cmd, self.path)
        self.assertTrue(wiring.hooks_installed(self.cmd, self.path))
        data = json.loads(self.path.read_text())
        self.assertEqual(set(data["hooks"]), set(wiring.RECOMMENDED_HOOKS))

    def test_install_preserves_foreign_hooks_and_keys(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({
            "model": "sonnet",
            "hooks": {"Stop": [{"matcher": "", "hooks": [
                {"type": "command", "command": "/other/tool"}]}]},
        }))
        wiring.install_hooks(self.cmd, self.path)
        data = json.loads(self.path.read_text())
        self.assertEqual(data["model"], "sonnet")  # untouched
        stop_cmds = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
        self.assertIn("/other/tool", stop_cmds)   # foreign kept
        self.assertIn(self.cmd, stop_cmds)        # ours added

    def test_install_is_idempotent(self):
        wiring.install_hooks(self.cmd, self.path)
        wiring.install_hooks(self.cmd, self.path)
        data = json.loads(self.path.read_text())
        stop_cmds = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(stop_cmds.count(self.cmd), 1)  # no duplicate

    def test_remove_strips_only_ours_and_prunes(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({
            "hooks": {"Stop": [{"matcher": "", "hooks": [
                {"type": "command", "command": "/other/tool"}]}]}}))
        wiring.install_hooks(self.cmd, self.path)
        self.assertTrue(wiring.remove_hooks(self.cmd, self.path))
        data = json.loads(self.path.read_text())
        stop_cmds = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(stop_cmds, ["/other/tool"])  # only ours removed
        self.assertNotIn("SessionEnd", data["hooks"])  # our-only event pruned away

    def test_backup_is_written(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{}")
        wiring.install_hooks(self.cmd, self.path)
        backups = list(self.path.parent.glob("settings.json.bak-*"))
        self.assertEqual(len(backups), 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_wiring.HooksMergeTest -v`
Expected: FAIL (`module 'ccnav.wiring' has no attribute 'install_hooks'`).

- [ ] **Step 3: Implement**

Add to `src/ccnav/wiring.py` (add `import json` and `import time` at the top):

```python
# The canonical hook set: event -> matcher. "" is an empty (match-all) matcher.
# One source of truth so doctor's check and this installer cannot drift.
RECOMMENDED_HOOKS = {
    "SessionStart": "",
    "UserPromptSubmit": "",
    "Notification": "",
    "Stop": "",
    "SessionEnd": "",
    "PreToolUse": "AskUserQuestion|ExitPlanMode",
}


def _load_settings(settings_path: pathlib.Path) -> dict:
    try:
        data = json.loads(settings_path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _our_entry(hook_command: str, matcher: str) -> dict:
    return {"matcher": matcher, "hooks": [{"type": "command", "command": hook_command}]}


def _group_has(hook_command: str, group) -> bool:
    """True iff `group` is a well-formed matcher group containing our command."""
    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
        return False
    return any(
        isinstance(h, dict) and h.get("command") == hook_command
        for h in group["hooks"]
    )


def hooks_installed(hook_command: str, settings_path: pathlib.Path) -> bool:
    hooks = _load_settings(settings_path).get("hooks")
    if not isinstance(hooks, dict):
        return False
    for event in RECOMMENDED_HOOKS:
        groups = hooks.get(event)
        if not isinstance(groups, list) or not any(
            _group_has(hook_command, g) for g in groups
        ):
            return False
    return True


def _write_settings(settings_path: pathlib.Path, data: dict) -> None:
    if settings_path.exists():
        backup = settings_path.with_name(
            settings_path.name + ".bak-%d" % int(time.time()))
        _atomic_write(backup, settings_path.read_text())
    _atomic_write(settings_path, json.dumps(data, indent=2))


def install_hooks(hook_command: str, settings_path: pathlib.Path) -> None:
    data = _load_settings(settings_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    for event, matcher in RECOMMENDED_HOOKS.items():
        groups = hooks.get(event)
        if not isinstance(groups, list):
            groups = []
        ours = any(_group_has(hook_command, g) for g in groups)
        if not ours:
            groups.append(_our_entry(hook_command, matcher))
        hooks[event] = groups
    data["hooks"] = hooks
    _write_settings(settings_path, data)


def remove_hooks(hook_command: str, settings_path: pathlib.Path) -> bool:
    data = _load_settings(settings_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    changed = False
    for event in list(hooks):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        new_groups = []
        for g in groups:
            if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
                new_groups.append(g)
                continue
            kept = [h for h in g["hooks"]
                    if not (isinstance(h, dict) and h.get("command") == hook_command)]
            if len(kept) != len(g["hooks"]):
                changed = True
            if kept:
                new = dict(g)
                new["hooks"] = kept
                new_groups.append(new)
        if new_groups:
            hooks[event] = new_groups
        else:
            del hooks[event]
    if not hooks:
        data.pop("hooks", None)
    if changed:
        _write_settings(settings_path, data)
    return changed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_wiring -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/wiring.py tests/test_wiring.py
git commit -m "feat: identity-based settings.json hook merge and removal"
```

---

## Task 15: Settings dialog — group into frames + Integration frame

**Files:**
- Modify: `src/ccnav/ui.py` (`_build_settings_dialog` — wrap sections in `Gtk.Frame`s; add Integration frame)
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `wiring.*` (launcher/autostart/hooks predicates + actions), `paths`,
  `ccnav.__version__`.
- Produces: an Integration frame with three `Gtk.CheckButton`s reflecting current on-disk state and
  toggling install/remove; failures land in a label, never a crash.

- [ ] **Step 1: Write the failing test**

In `tests/test_ui.py` `SettingsUiTest`, add:

```python
    def test_wiring_frame_reflects_and_toggles_launcher(self):
        import tempfile, pathlib
        from ccnav import config, wiring
        window = ui.NavigatorWindow(
            on_jump=lambda r: None, on_send=lambda r, t: None, settings=config.Settings())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        apps = pathlib.Path(tmp.name)
        # Point the window's wiring helpers at a temp dir via its hook seam.
        window._wiring_apps_dir = apps
        try:
            self.assertFalse(wiring.launcher_installed(apps))
            window._set_launcher(True)
            self.assertTrue(wiring.launcher_installed(apps))
            window._set_launcher(False)
            self.assertFalse(wiring.launcher_installed(apps))
        finally:
            window.destroy()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest tests.test_ui.SettingsUiTest.test_wiring_frame_reflects_and_toggles_launcher -v`
Expected: FAIL (`_set_launcher` / `_wiring_apps_dir` missing).

- [ ] **Step 3: Implement**

At the top of `ui.py`, add `from . import config, wiring, model, paths` (extend the existing
import). In `__init__`, add seams (near the other `self._` fields) so tests can redirect paths:

```python
        self._wiring_apps_dir = None      # None -> wiring's default (~/.local/share)
        self._wiring_autostart_dir = None
        self._wiring_settings_path = None  # None -> ~/.claude/settings.json
```

Add the exec path + settings path helpers and the three toggle methods:

```python
    def _cc_exec_path(self) -> str:
        return os.path.join(os.path.expanduser("~"), ".local", "bin", "cc-navigator")

    def _settings_json_path(self):
        import pathlib
        return self._wiring_settings_path or (
            pathlib.Path(os.path.expanduser("~")) / ".claude" / "settings.json")

    def _hook_command(self) -> str:
        import pathlib
        return str(pathlib.Path(__file__).resolve().parents[2] / "bin" / "cc-navigator-hook")

    def _set_launcher(self, on: bool) -> None:
        if on:
            wiring.install_launcher(self._cc_exec_path(), self._wiring_apps_dir)
        else:
            wiring.remove_launcher(self._wiring_apps_dir)

    def _set_autostart(self, on: bool) -> None:
        wiring.set_autostart(on, self._cc_exec_path(), self._wiring_autostart_dir)

    def _set_hooks(self, on: bool) -> None:
        if on:
            wiring.install_hooks(self._hook_command(), self._settings_json_path())
        else:
            wiring.remove_hooks(self._hook_command(), self._settings_json_path())
```

In `_build_settings_dialog`, add an Integration frame after the Task 4 version footer, then move the
footer back to the bottom so the version line stays last (`footer` is in scope — both edits are in
this same method). Each check button reflects the current predicate and calls the setter, reporting
failure in a shared status label:

```python
        integ_status = Gtk.Label(xalign=0.0)
        integ_status.set_line_wrap(True)

        def make_toggle(label_text, is_on, setter):
            btn = Gtk.CheckButton(label=label_text)
            btn.set_active(is_on())
            def on_toggle(w):
                try:
                    setter(w.get_active())
                except Exception as exc:  # noqa: BLE001 -- a toggle must never crash the panel
                    integ_status.set_text("설정 변경 실패: %s" % exc)
            btn.connect("toggled", on_toggle)
            return btn

        integ = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        integ.add(make_toggle(
            "앱 목록에 등록",
            lambda: wiring.launcher_installed(self._wiring_apps_dir),
            self._set_launcher))
        integ.add(make_toggle(
            "로그인 시 자동 실행",
            lambda: wiring.autostart_enabled(self._wiring_autostart_dir),
            self._set_autostart))
        integ.add(make_toggle(
            "Claude Code 훅 설정",
            lambda: wiring.hooks_installed(self._hook_command(), self._settings_json_path()),
            self._set_hooks))
        integ.add(integ_status)

        frame = Gtk.Frame(label="통합")
        frame.add(integ)
        content.add(frame)
        content.reorder_child(footer, -1)  # keep the version line at the very bottom
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./run-tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccnav/ui.py tests/test_ui.py
git commit -m "feat: wiring toggles (launcher, autostart, hooks) in settings"
```

---

## Final verification

- [ ] **Run the whole suite**

Run: `./run-tests`
Expected: all green (the ~273 existing plus the new tests).

- [ ] **Manual smoke (with a display)**

Run `./bin/cc-navigator`. Verify: gear opens the dialog with Appearance/colour/opacity/version and an
Integration frame; the collapse button shrinks/restores the panel; the refresh button exists; opacity
and background colour change live; a background colour + font persist across a restart.

## Self-review notes (already reconciled)

- Spec §5.1 opacity/bg_color → Task 1; §5.2 version + `--version` + apply → Tasks 2–4; §5.3 collapse →
  Task 5; §5.4 SessionEnd + refresh → Tasks 6–8; §5.5 prompt capture + detail row → Tasks 9–10; §5.6
  install + launcher + autostart + hooks + Integration frame → Tasks 11–15.
- Type consistency: `config.Settings.opacity/bg_color` (Task 1) are the only new config fields, used in
  Tasks 3–4. `model.Row.last_prompt` (Task 9) is consumed in Task 10. `wiring.*` signatures
  (Tasks 12–14) are exactly what Task 15 calls. `NavigatorWindow.__init__`'s new `on_refresh` (Task 8)
  and the `_wiring_*` seams (Task 15) are the only constructor changes.
- Deferred to Plan 2 (features 6–7): `agents.py`, background rows, the convert button.
- Naming: the module the spec calls `integration.py` is built here as `src/ccnav/wiring.py`
  (tests `tests/test_wiring.py`). Renamed because `tests/test_integration.py` already exists — the
  real-tmux end-to-end test — and must not be clobbered.
