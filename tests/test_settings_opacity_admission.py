"""Saved opacity admission with real YAML and production panel method adapters.

These tests do not launch Qt or the application. The adapter does not emulate
Qt's numeric limits; huge finite values are checked only at Settings admission.
"""

from copy import deepcopy
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import yaml

from src.config.defaults import DEFAULT_CONFIG
from src.config.settings import Settings, SettingsValidationError
from tests.test_settings_batch import Control, panel_for


INVALID_OPACITIES = (None, "opaque", "0.63", [], {},
                     float("nan"), float("inf"), float("-inf"))
PRESERVED_OPACITIES = (0, 0.0, 0.05, 0.2, 0.63, 1, 1.0, -0.1, 1.5, False, True)


class TestSavedOpacityAdmission(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "settings.yaml"

    def write_config(self, config):
        # Keep a comment and deliberate key order so a load-time rewrite cannot
        # pass merely by producing semantically equivalent YAML.
        self.path.write_text("# Keep this saved profile unchanged on load.\n" +
                             yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        return self.path.read_bytes()

    def load_opacity(self, value):
        original = self.write_config({
            "display": {"overlay_opacity": value, "overlay_scale": 1.25,
                        "mode": "both", "overlay_position": "bottom-left",
                        "always_on_top": False, "custom_display_note": ["keep", 7]},
            "capture": {"idle_fps": 0.25, "active_fps": 3.0, "business_fps": 5.0},
            "general": {"character_name": "Retained character"},
            "custom": {"nested": ["retained", 42]},
        })
        return Settings(self.path), original

    def assert_preserved(self, settings, original):
        self.assertEqual(settings.get("display.overlay_scale"), 1.25)
        self.assertEqual(settings.get("display.mode"), "both")
        self.assertEqual(settings.get("display.overlay_position"), "bottom-left")
        self.assertIs(settings.get("display.always_on_top"), False)
        self.assertEqual(settings.get("display.custom_display_note"), ["keep", 7])
        for field, value in (("idle_fps", 0.25), ("active_fps", 3.0), ("business_fps", 5.0)):
            self.assertEqual(settings.get(f"capture.{field}"), value)
        self.assertEqual(settings.get("general.character_name"), "Retained character")
        self.assertEqual(settings.get("custom.nested"), ["retained", 42])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_invalid_saved_opacity_loads_settings_panel(self):
        for value in INVALID_OPACITIES:
            with self.subTest(value=value):
                settings, original = self.load_opacity(value)
                panel = panel_for(settings)
                # Exercise the actual production conversion before asserting
                # fallback; the unchanged baseline raises here for all 8 cases.
                panel._load_settings()
                self.assertEqual(panel._opacity_slider.current, 85)
                self.assertEqual(panel._opacity_value.current, "85%")
                self.assertFalse(panel._unsaved_changes)
                self.assertFalse(panel._save_btn.current)
                self.assertTrue(all(not item.blocked for item in vars(panel).values()
                                    if isinstance(item, Control)))
                self.assert_preserved(settings, original)

    def test_invalid_saved_opacity_falls_back_only_at_its_leaf(self):
        for value in INVALID_OPACITIES:
            with self.subTest(value=value):
                settings, original = self.load_opacity(value)
                self.assertIs(type(settings.get("display.overlay_opacity")), float)
                self.assertEqual(settings.get("display.overlay_opacity"), 0.85)
                self.assert_preserved(settings, original)

    def test_finite_numeric_and_bool_values_keep_their_type_and_value(self):
        for value in PRESERVED_OPACITIES:
            with self.subTest(value=value, type=type(value).__name__):
                settings, original = self.load_opacity(value)
                actual = settings.get("display.overlay_opacity")
                self.assertIs(type(actual), type(value))
                self.assertEqual(actual, value)
                panel = panel_for(settings)
                panel._load_settings()
                self.assertEqual(panel._opacity_slider.current, int(value * 100))
                self.assert_preserved(settings, original)

    def test_huge_finite_values_are_preserved_at_admission_only(self):
        # A huge float can overflow during * 100, and huge ints can exceed Qt's
        # conversion range. Those unchanged limits are outside this correction.
        for value in (10**400, 1e308):
            with self.subTest(type=type(value).__name__):
                settings, original = self.load_opacity(value)
                actual = settings.get("display.overlay_opacity")
                self.assertIs(type(actual), type(value))
                self.assertEqual(actual, value)
                self.assert_preserved(settings, original)

    def test_missing_leaf_uses_the_existing_merged_default(self):
        for config in ({}, {"display": {"custom_display_note": "keep"}}):
            with self.subTest(config=config):
                original = self.write_config(config)
                settings = Settings(self.path)
                self.assertEqual(settings.get("display.overlay_opacity"), 0.85)
                self.assertEqual(self.path.read_bytes(), original)
                if config:
                    self.assertEqual(settings.get("display.custom_display_note"), "keep")

    def test_non_mapping_display_sections_are_unchanged(self):
        for section in (None, "legacy", ["legacy"], False):
            with self.subTest(section=section):
                original = self.write_config({"display": section, "custom": "keep"})
                settings = Settings(self.path)
                self.assertIs(type(settings.get_section("display")), type(section))
                self.assertEqual(settings.get_section("display"), section)
                self.assertEqual(settings.get("custom"), "keep")
                self.assertEqual(self.path.read_bytes(), original)

    def test_ordinary_save_persists_admitted_leaf_and_unrelated_values(self):
        settings, original = self.load_opacity("0.63")
        self.assert_preserved(settings, original)
        settings.set("general.minimize_to_tray", False)
        saved = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        restored = Settings(self.path)
        self.assertEqual(saved["display"]["overlay_opacity"], 0.85)
        self.assertEqual(restored.get("display.overlay_opacity"), 0.85)
        self.assertIs(restored.get("general.minimize_to_tray"), False)
        self.assert_preserved(restored, self.path.read_bytes())

    def test_set_and_set_many_validation_remain_unchanged(self):
        for method in ("set", "set_many"):
            with self.subTest(method=method):
                settings, original = self.load_opacity(0.63)
                for value in (-0.1, 1.5, "0.63"):
                    with self.subTest(value=value):
                        with self.assertRaises(SettingsValidationError):
                            if method == "set":
                                settings.set("display.overlay_opacity", value)
                            else:
                                settings.set_many({"display.overlay_opacity": value})
                        self.assertEqual(settings.get("display.overlay_opacity"), 0.63)
                        self.assert_preserved(settings, original)
                # Direct setter NaN admission is an existing, separate boundary.
                if method == "set":
                    settings.set("display.overlay_opacity", float("nan"), save=False)
                else:
                    settings.set_many({"display.overlay_opacity": float("nan")})
                self.assertTrue(math.isnan(settings.get("display.overlay_opacity")))

    def test_admission_does_not_mutate_defaults_or_another_profile(self):
        defaults = deepcopy(DEFAULT_CONFIG)
        settings, original = self.load_opacity(None)
        other_path = self.path.parent / "other.yaml"
        other_path.write_text("display:\n  overlay_opacity: 0.2\n", encoding="utf-8")
        other = Settings(other_path)
        settings.get_section("display")["overlay_opacity"] = 0.4
        self.assertEqual(other.get("display.overlay_opacity"), 0.2)
        self.assertEqual(DEFAULT_CONFIG, defaults)
        self.assertEqual(self.path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
