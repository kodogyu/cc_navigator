"""Tests for the colour theme palettes and CSS generation."""
import unittest

from ccnav import config, themes


class ThemeChoicesTest(unittest.TestCase):
    def test_all_ids_resolve_to_a_palette_and_a_name(self):
        for tid, name in themes.theme_choices():
            self.assertIn(tid, themes.THEMES)
            self.assertTrue(name)

    def test_config_theme_ids_match_themes_module(self):
        # config validates ids without importing themes; keep the two in sync.
        self.assertEqual(set(config.THEME_IDS), set(themes.THEMES))


class ResolveTest(unittest.TestCase):
    def test_unknown_id_falls_back_to_default(self):
        self.assertEqual(themes.resolve("nope"), themes.THEMES[themes.DEFAULT_THEME])

    def test_no_override_returns_the_base_palette(self):
        self.assertEqual(themes.resolve("nord"), themes.THEMES["nord"])

    def test_overrides_replace_bg_and_dark_only(self):
        p = themes.resolve("midnight", bg_override="#123456", dark_override="#010203")
        self.assertEqual(p.bg, "#123456")
        self.assertEqual(p.dark, "#010203")
        self.assertEqual(p.accent, themes.THEMES["midnight"].accent)  # untouched


class BuildCssTest(unittest.TestCase):
    def test_css_uses_the_palette_colours_and_is_scoped(self):
        css = themes.build_css(themes.resolve("midnight"))
        self.assertIn("#1e1e2e", css)   # bg
        self.assertIn("#5eead4", css)   # mint accent
        self.assertIn(".ccnav", css)
        self.assertNotIn("font-size", css)  # font is the user's separate setting

    def test_override_flows_into_the_css(self):
        css = themes.build_css(themes.resolve("midnight", bg_override="#abcdef"))
        self.assertIn("#abcdef", css)


class ConfigThemeTest(unittest.TestCase):
    def test_valid_theme_is_kept_invalid_falls_back(self):
        self.assertEqual(config.from_dict({"theme": "graphite"}).theme, "graphite")
        self.assertEqual(config.from_dict({"theme": "bogus"}).theme, config.DEFAULT_THEME)

    def test_dark_color_override_validated_like_bg(self):
        self.assertEqual(config.from_dict({"dark_color": "#0a0a0a"}).dark_color, "#0a0a0a")
        self.assertEqual(config.from_dict({"dark_color": "notahex"}).dark_color, "")


if __name__ == "__main__":
    unittest.main()
