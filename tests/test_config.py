import json
import pathlib
import tempfile
import unittest

from ccnav import config


class DefaultsTest(unittest.TestCase):
    def test_defaults_are_sane(self):
        s = config.Settings()
        self.assertEqual(s.poll_seconds, 1.0)
        self.assertEqual(s.corner, "top-right")
        self.assertTrue(s.keep_above)
        self.assertTrue(s.all_workspaces)
        self.assertEqual(s.font_size, 0)


class FromDictCoercionTest(unittest.TestCase):
    def test_a_full_valid_dict_round_trips(self):
        raw = {
            "poll_seconds": 2.5, "corner": "bottom-left", "width": 500,
            "height": 600, "keep_above": False, "all_workspaces": False,
            "font_size": 14,
        }
        self.assertEqual(config.from_dict(raw).to_dict(), raw)

    def test_missing_keys_fall_back_to_defaults(self):
        s = config.from_dict({"corner": "top-left"})
        self.assertEqual(s.corner, "top-left")
        self.assertEqual(s.poll_seconds, 1.0)  # default preserved

    def test_non_dict_input_yields_defaults(self):
        self.assertEqual(config.from_dict(["not", "a", "dict"]), config.Settings())
        self.assertEqual(config.from_dict(None), config.Settings())

    def test_garbage_poll_seconds_is_ignored(self):
        self.assertEqual(config.from_dict({"poll_seconds": "soon"}).poll_seconds, 1.0)

    def test_nan_poll_seconds_is_ignored(self):
        # float('nan') parses but must never become a real timeout.
        self.assertEqual(config.from_dict({"poll_seconds": float("nan")}).poll_seconds, 1.0)

    def test_poll_seconds_is_clamped_not_rejected(self):
        self.assertEqual(config.from_dict({"poll_seconds": 0.01}).poll_seconds, config.POLL_MIN)
        self.assertEqual(config.from_dict({"poll_seconds": 9999}).poll_seconds, config.POLL_MAX)

    def test_unknown_corner_falls_back(self):
        self.assertEqual(config.from_dict({"corner": "middle"}).corner, "top-right")

    def test_width_and_height_are_clamped(self):
        s = config.from_dict({"width": 1, "height": 100000})
        self.assertEqual(s.width, config.WIDTH_MIN)
        self.assertEqual(s.height, config.HEIGHT_MAX)

    def test_only_a_real_bool_changes_a_toggle(self):
        # A truthy/falsy non-bool must not flip the toggle in a surprising way.
        self.assertTrue(config.from_dict({"keep_above": "false"}).keep_above)  # default kept
        self.assertTrue(config.from_dict({"keep_above": []}).keep_above)  # default kept
        self.assertFalse(config.from_dict({"keep_above": False}).keep_above)  # real bool wins

    def test_font_size_zero_means_default(self):
        self.assertEqual(config.from_dict({"font_size": 0}).font_size, 0)
        self.assertEqual(config.from_dict({"font_size": -5}).font_size, 0)

    def test_font_size_is_clamped_when_positive(self):
        self.assertEqual(config.from_dict({"font_size": 2}).font_size, config.FONT_MIN)
        self.assertEqual(config.from_dict({"font_size": 99}).font_size, config.FONT_MAX)


class WithUpdatesTest(unittest.TestCase):
    def test_update_is_revalidated(self):
        s = config.Settings()
        # An out-of-range update is clamped, not stored raw.
        self.assertEqual(config.with_updates(s, width=5).width, config.WIDTH_MIN)
        self.assertEqual(config.with_updates(s, corner="bottom-right").corner, "bottom-right")


class LoadSaveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = pathlib.Path(self._tmp.name) / "sub" / "config.json"

    def test_save_then_load_round_trips(self):
        s = config.Settings(poll_seconds=3.0, corner="bottom-right", font_size=16)
        config.save(s, self.path)
        self.assertEqual(config.load(self.path), s)

    def test_save_creates_missing_parent(self):
        config.save(config.Settings(), self.path)
        self.assertTrue(self.path.exists())

    def test_load_missing_file_is_defaults(self):
        self.assertEqual(config.load(self.path), config.Settings())

    def test_load_garbage_file_is_defaults(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{ not json")
        self.assertEqual(config.load(self.path), config.Settings())

    def test_load_applies_coercion_to_a_hand_edited_file(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"poll_seconds": 100000, "corner": "nope"}))
        s = config.load(self.path)
        self.assertEqual(s.poll_seconds, config.POLL_MAX)
        self.assertEqual(s.corner, "top-right")

    def test_save_is_atomic_and_leaves_no_temp(self):
        config.save(config.Settings(), self.path)
        leftovers = [p.name for p in self.path.parent.iterdir() if p.name.startswith(".tmp-")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
