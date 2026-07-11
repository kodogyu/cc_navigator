"""User-editable settings: a pure, validated model plus atomic load/save.

Everything here is I/O-free logic except load()/save(), so the coercion rules --
which are the part that must never crash the app on a hand-edited file -- are
testable without touching the filesystem. The discipline mirrors statestore and
model: a garbage or out-of-range value is coerced to a safe default or clamped,
never allowed to raise. The settings dialog writes this file; a broken file must
still yield a working panel.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
from dataclasses import dataclass, replace
from typing import Optional

CORNERS = ("top-right", "top-left", "bottom-right", "bottom-left")

# How the session list is grouped: "status" (input-needed / reported / working
# sections) or "group" (project-directory groups the user can rearrange by drag,
# with an auto-sort button to re-group by directory).
SORT_MODES = ("status", "group")

# Ranges are clamps, not rejections: an out-of-range number is pulled to the
# nearest bound rather than dropped, so a fat-fingered edit still does something
# sane instead of silently reverting.
POLL_MIN, POLL_MAX = 0.25, 60.0
WIDTH_MIN, WIDTH_MAX = 200, 2000
HEIGHT_MIN, HEIGHT_MAX = 150, 2000
# font_size 0 is special: "do not override", so the panel keeps the system font
# unless the user opts in. Any other value is clamped into this readable range.
FONT_MIN, FONT_MAX = 7, 30
OPACITY_MIN, OPACITY_MAX = 0.3, 1.0
# A background colour is either "" (no override, keep the theme) or a #rrggbb hex.
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}\Z")


@dataclass(frozen=True)
class Settings:
    poll_seconds: float = 1.0
    corner: str = "top-right"
    width: int = 340
    height: int = 420
    keep_above: bool = True
    all_workspaces: bool = True
    font_size: int = 0  # 0 = use the system default font size
    opacity: float = 1.0
    bg_color: str = ""  # "" = no override, keep the theme
    sort_mode: str = "status"  # "status" | "group"
    notifications: bool = True  # desktop notify when a session becomes "your turn"

    def to_dict(self) -> dict:
        return {
            "poll_seconds": self.poll_seconds,
            "corner": self.corner,
            "width": self.width,
            "height": self.height,
            "keep_above": self.keep_above,
            "all_workspaces": self.all_workspaces,
            "font_size": self.font_size,
            "opacity": self.opacity,
            "bg_color": self.bg_color,
            "sort_mode": self.sort_mode,
            "notifications": self.notifications,
        }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _as_number(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN -- float('nan') parses but must not survive
        return default
    return number


def _coerce(raw: dict, base: Settings) -> Settings:
    """Merge a possibly-hostile dict over `base`, field by field, never raising."""
    poll = _clamp(_as_number(raw.get("poll_seconds"), base.poll_seconds), POLL_MIN, POLL_MAX)

    corner = raw.get("corner")
    corner = corner if corner in CORNERS else base.corner

    width = int(_clamp(_as_number(raw.get("width"), base.width), WIDTH_MIN, WIDTH_MAX))
    height = int(_clamp(_as_number(raw.get("height"), base.height), HEIGHT_MIN, HEIGHT_MAX))

    # bool() of a non-bool is surprising ([] -> False, "false" -> True), so only
    # a real JSON bool changes the setting; anything else keeps the default.
    keep_above = raw.get("keep_above")
    keep_above = keep_above if isinstance(keep_above, bool) else base.keep_above
    all_ws = raw.get("all_workspaces")
    all_ws = all_ws if isinstance(all_ws, bool) else base.all_workspaces
    notifications = raw.get("notifications")
    notifications = notifications if isinstance(notifications, bool) else base.notifications

    font_raw = _as_number(raw.get("font_size"), base.font_size)
    font = 0 if font_raw <= 0 else int(_clamp(font_raw, FONT_MIN, FONT_MAX))

    opacity = _clamp(_as_number(raw.get("opacity"), base.opacity), OPACITY_MIN, OPACITY_MAX)

    bg = raw.get("bg_color")
    bg = bg if isinstance(bg, str) and _HEX_RE.match(bg) else base.bg_color

    sort_mode = raw.get("sort_mode")
    sort_mode = sort_mode if sort_mode in SORT_MODES else base.sort_mode

    return Settings(
        poll_seconds=poll,
        corner=corner,
        width=width,
        height=height,
        keep_above=keep_above,
        all_workspaces=all_ws,
        font_size=font,
        opacity=opacity,
        bg_color=bg,
        sort_mode=sort_mode,
        notifications=notifications,
    )


def from_dict(raw, base: Optional[Settings] = None) -> Settings:
    """Public coercion: build Settings from untrusted data over defaults."""
    base = base or Settings()
    if not isinstance(raw, dict):
        return base
    return _coerce(raw, base)


def config_path() -> pathlib.Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return pathlib.Path(base) / "cc-navigator" / "config.json"


def load(path: Optional[pathlib.Path] = None) -> Settings:
    """Read settings, or return defaults for a missing/unreadable/garbage file."""
    path = path or config_path()
    try:
        text = path.read_text()
    except OSError:
        return Settings()
    try:
        raw = json.loads(text)
    except ValueError:
        return Settings()
    return from_dict(raw)


def save(settings: Settings, path: Optional[pathlib.Path] = None) -> None:
    """Atomically write settings: temp file in the same dir, then os.replace, so
    a concurrent load never sees a half-written file (same rule as statestore)."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(handle_fd, "w") as handle:
            json.dump(settings.to_dict(), handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, str(path))
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def with_updates(settings: Settings, **changes) -> Settings:
    """Validated field update: apply `changes`, then re-coerce so the result is
    always in-range regardless of what the caller passed."""
    merged = replace(settings, **changes)
    return from_dict(merged.to_dict())
