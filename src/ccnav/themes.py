"""Selectable colour themes for the panel, plus the CSS each one produces.

A theme is a small palette; build_css() turns it into the `.ccnav`-scoped stylesheet
the window applies. Surface tints (hover, entry/button fills, borders, scrollbar)
are drawn as alpha overlays of one base colour (white on dark themes, black on
light) so a single palette drives every derived shade. The user can pick a theme
and override its two darkest colours (background and header) from Settings; the
semantic status colours (green token bar, orange weekly, red/green/blue dots) stay
fixed across themes because they carry meaning, not style.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

DEFAULT_THEME = "midnight"


@dataclass(frozen=True)
class Palette:
    name: str        # display name (shown in the Settings dropdown)
    bg: str          # window background
    dark: str        # header bar / darkest surface ("the black")
    accent: str      # selection, jump button, focus, checks
    on_accent: str   # text drawn ON the accent (contrast)
    text: str        # primary text
    dim: str         # secondary/dim text
    hover: str       # row hover
    selected: str    # row selected
    track: str       # progress-bar trough
    bar_text: str    # text inside the token bar
    overlay: str     # "r,g,b" base for alpha surface tints (white on dark, black on light)


THEMES = {
    "midnight": Palette(
        name="Midnight (민트)", bg="#1e1e2e", dark="#181825", accent="#5eead4",
        on_accent="#12121c", text="#e6e6ef", dim="#9aa0b4", hover="#26263a",
        selected="#2a2a3c", track="#33334a", bar_text="#ffffff", overlay="255,255,255"),
    "nord": Palette(
        name="Nord Dark", bg="#2e3440", dark="#272c36", accent="#88c0d0",
        on_accent="#12222b", text="#eceff4", dim="#9aa7b8", hover="#363d4a",
        selected="#3b4252", track="#434c5e", bar_text="#ffffff", overlay="255,255,255"),
    "graphite": Palette(
        name="Graphite Terminal", bg="#181818", dark="#0f0f0f", accent="#ff9e64",
        on_accent="#241405", text="#f5f5f5", dim="#8a8a8a", hover="#242424",
        selected="#2a2a2a", track="#333333", bar_text="#ffffff", overlay="255,255,255"),
    "light": Palette(
        name="Clean Light", bg="#ffffff", dark="#eef1f4", accent="#3584e4",
        on_accent="#ffffff", text="#2e3436", dim="#6b6f76", hover="#f2f4f7",
        selected="#e7f0ff", track="#dde3ea", bar_text="#1a1a1a", overlay="0,0,0"),
}

# Dropdown order.
THEME_ORDER = ["midnight", "nord", "graphite", "light"]


def theme_choices() -> List[Tuple[str, str]]:
    """(id, display name) pairs in dropdown order."""
    return [(tid, THEMES[tid].name) for tid in THEME_ORDER if tid in THEMES]


def _hex_to_rgb(value: str) -> Tuple[int, int, int]:
    v = value.lstrip("#")
    if len(v) != 6:
        return (94, 234, 212)  # mint fallback for a malformed hex
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except ValueError:
        return (94, 234, 212)


def _lighten(value: str, amount: float) -> str:
    r, g, b = _hex_to_rgb(value)
    mix = lambda c: int(round(c + (255 - c) * amount))
    return "#%02x%02x%02x" % (mix(r), mix(g), mix(b))


def resolve(theme_id: str, bg_override: str = "", dark_override: str = "") -> Palette:
    """The chosen theme's palette, with optional background / header overrides
    (a hex string; "" keeps the theme's own colour)."""
    base = THEMES.get(theme_id) or THEMES[DEFAULT_THEME]
    bg = bg_override or base.bg
    dark = dark_override or base.dark
    if bg == base.bg and dark == base.dark:
        return base
    return Palette(**{**base.__dict__, "bg": bg, "dark": dark})


def build_css(palette: Palette) -> str:
    """The full `.ccnav`-scoped stylesheet for `palette`. No font-size (that is the
    user's separate setting and must compose on top)."""
    p = palette
    ov = p.overlay
    ar, ag, ab = _hex_to_rgb(p.accent)
    accent_rgb = "%d,%d,%d" % (ar, ag, ab)
    accent_hi = _lighten(p.accent, 0.18)
    return f"""
.ccnav, .ccnav.background {{ background-color:{p.bg}; color:{p.text}; }}
.ccnav decoration {{ border-radius:14px; }}
.ccnav headerbar {{ background:{p.dark}; border:none; box-shadow:none;
    min-height:34px; color:{p.text}; padding:0 4px; }}
.ccnav headerbar button {{ background:transparent; border:none; box-shadow:none;
    color:{p.dim}; margin:3px 1px; padding:2px 6px; }}
.ccnav headerbar button:hover {{ background:rgba({ov},0.10); border-radius:8px; }}
.ccnav scrolledwindow, .ccnav viewport, .ccnav list, .ccnav row {{ background:transparent; }}
.ccnav row {{ color:{p.text}; border-radius:10px; margin:2px 6px; padding:4px 4px;
    border:1px solid transparent; }}
.ccnav row:hover {{ background:{p.hover}; }}
.ccnav row:selected {{ background:{p.selected}; border:1px solid {p.accent}; }}
.ccnav row:selected:hover {{ background:{p.selected}; }}
.ccnav entry {{ background:rgba({ov},0.06); color:{p.text};
    border:1px solid rgba({ov},0.14); border-radius:8px; padding:4px 8px;
    box-shadow:none; caret-color:{p.accent}; }}
.ccnav entry:focus {{ border-color:{p.accent}; box-shadow:none; }}
.ccnav button {{ background:rgba({ov},0.06); color:{p.text};
    border:1px solid rgba({ov},0.12); border-radius:8px; padding:4px 10px;
    box-shadow:none; text-shadow:none; }}
.ccnav button:hover {{ background:rgba({ov},0.12); }}
.ccnav button:disabled {{ color:rgba({ov},0.35); }}
.ccnav-jump {{ background:{p.accent}; color:{p.on_accent}; font-weight:700; border:none; }}
.ccnav-jump:hover {{ background:{accent_hi}; }}
.ccnav-jump:disabled {{ background:rgba({accent_rgb},0.22); color:rgba({ov},0.40); }}
.ccnav combobox button {{ background:rgba({ov},0.06); color:{p.text}; }}
.ccnav separator {{ background:rgba({ov},0.08); }}
.ccnav scrollbar {{ background:transparent; border:none; }}
.ccnav scrollbar slider {{ background:rgba({ov},0.20); border-radius:8px;
    min-width:6px; min-height:24px; }}
.ccnav scrollbar slider:hover {{ background:rgba({ov},0.34); }}
.ccnav progressbar.token-bar {{ min-height:18px; }}
.ccnav progressbar.token-bar trough {{ min-height:18px; border-radius:9px;
    border:1px solid rgba({ov},0.14); background:{p.track}; }}
.ccnav progressbar.token-bar progress {{ min-height:18px; border-radius:9px; border:none;
    background:#2ec27e; }}
.ccnav .token-bar-text {{ color:{p.bar_text}; text-shadow:0 1px 2px rgba(0,0,0,0.55); }}
.ccnav scale trough {{ background:rgba({ov},0.12); border:none; }}
.ccnav scale highlight, .ccnav scale progress {{ background:{p.accent}; border:none; }}
.ccnav scale slider {{ background:{p.text}; box-shadow:none; border:none; }}
.ccnav spinbutton {{ background:rgba({ov},0.06); color:{p.text};
    border:1px solid rgba({ov},0.12); border-radius:8px; }}
.ccnav spinbutton:focus-within {{ border-color:{p.accent}; }}
.ccnav spinbutton entry {{ background:transparent; border:none; box-shadow:none; }}
.ccnav spinbutton button {{ background:transparent; border:none; color:{p.dim}; }}
.ccnav spinbutton button:hover {{ background:rgba({ov},0.10); }}
.ccnav check {{ border-radius:4px; border:1px solid rgba({ov},0.25);
    background:rgba({ov},0.06); }}
.ccnav check:checked {{ background:{p.accent}; border-color:{p.accent}; }}
.ccnav frame > border {{ border-color:rgba({ov},0.12); }}
.ccnav selection, .ccnav entry selection {{ background-color:{p.accent}; color:{p.on_accent}; }}
"""
