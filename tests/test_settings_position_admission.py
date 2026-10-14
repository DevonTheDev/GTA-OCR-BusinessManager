"""Saved position admission with real YAML and unchanged UI method adapters.

These tests do not import native Qt or launch the application. The overlay
adapter replaces QApplication, screen/window geometry and move; the existing
panel_for adapter replaces widgets, message boxes, logging and timers.
"""

import ast
from copy import deepcopy
from datetime import date, datetime
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

from src.config.defaults import DEFAULT_CONFIG
from src.config.settings import Settings, SettingsValidationError
from tests.test_settings_batch import Control, panel_for


UNHASHABLE_POSITIONS = ([], ["bottom-left"], {}, {"corner": "bottom-left"},
                       set(), {"bottom-left"})
HASHABLE_UNKNOWN_POSITIONS = ("", "center", None, 0, 1, 1.5, False, True,
                              float("nan"), float("inf"), float("-inf"),
                              10**400, b"bottom-left", date(2026, 10, 14),
                              datetime(2026, 10, 14, 12, 30))
CORNERS = (("top-left", 0, (20, 20)), ("top-right", 1, (1620, 20)),
           ("bottom-left", 2, (20, 860)), ("bottom-right", 3, (1620, 860)))


def overlay_for():
    """Compile unchanged set_position/_position_overlay with a fake screen."""
    source = Path(__file__).resolve().parents[1] / "src/ui/overlay.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "OverlayWindow")
    names = ("set_position", "_position_overlay")
    methods = [node for node in cls.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {}
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), "exec"), namespace)
    geometry = SimpleNamespace(width=lambda: 1920, height=lambda: 1080)
    screen = SimpleNamespace(availableGeometry=lambda: geometry)
    qt_widgets = SimpleNamespace(QApplication=SimpleNamespace(primaryScreen=lambda: screen))
    moves = []
    overlay = SimpleNamespace(width=lambda: 280, height=lambda: 200,
                              move=lambda x, y: moves.append((x, y)), moves=moves)
    overlay._position_overlay = lambda position: namespace["_position_overlay"](overlay, position)

    def set_position(position):
        # Keep the production method's local QApplication import unchanged.
        with patch.dict("sys.modules", {"PyQt6.QtWidgets": qt_widgets}):
            namespace["set_position"](overlay, position)

    overlay.set_position = set_position
    return overlay


class TestSavedPositionAdmission(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "settings.yaml"

    def write_config(self, config):
        self.path.write_text("# Keep this saved profile unchanged on load.\n" +
                             yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        return self.path.read_bytes()

    def load_position(self, value):
        original = self.write_config({
            "display": {"overlay_position": value, "overlay_opacity": 0.63,
                        "overlay_scale": 1.25, "mode": "both", "always_on_top": False,
                        "custom_display_note": ["keep", 7]},
            "capture": {"idle_fps": 0.25, "active_fps": 3.0, "business_fps": 5.0},
            "general": {"character_name": "Retained character"},
            "custom": {"nested": ["retained", 42]},
        })
        return Settings(self.path), original

    def assert_preserved(self, settings, original):
        self.assertEqual(settings.get("display.overlay_opacity"), 0.63)
        self.assertEqual(settings.get("display.overlay_scale"), 1.25)
        self.assertEqual(settings.get("display.mode"), "both")
        self.assertIs(settings.get("display.always_on_top"), False)
        self.assertEqual(settings.get("display.custom_display_note"), ["keep", 7])
        for field, value in (("idle_fps", 0.25), ("active_fps", 3.0), ("business_fps", 5.0)):
            self.assertEqual(settings.get(f"capture.{field}"), value)
        self.assertEqual(settings.get("general.character_name"), "Retained character")
        self.assertEqual(settings.get("custom.nested"), ["retained", 42])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_unhashable_saved_positions_load_settings_panel(self):
        for value in UNHASHABLE_POSITIONS:
            with self.subTest(value=value):
                settings, original = self.load_position(value)
                panel = panel_for(settings)
                # The unchanged baseline raises at production pos_map.get.
                panel._load_settings()
                self.assertEqual(panel._pos_combo.current, 1)
                self.assertFalse(panel._unsaved_changes)
                self.assertFalse(panel._save_btn.current)
                self.assertTrue(all(not item.blocked for item in vars(panel).values()
                                    if isinstance(item, Control)))
                self.assert_preserved(settings, original)

    def test_unhashable_saved_positions_reach_overlay_position(self):
        for value in UNHASHABLE_POSITIONS:
            with self.subTest(value=value):
                settings, original = self.load_position(value)
                overlay = overlay_for()
                # The unchanged baseline raises at production positions.get.
                overlay.set_position(settings.get("display.overlay_position"))
                self.assertEqual(overlay.moves, [(1620, 20)])
                self.assert_preserved(settings, original)

    def test_unhashable_saved_positions_fall_back_only_at_the_leaf(self):
        for value in UNHASHABLE_POSITIONS:
            with self.subTest(value=value):
                settings, original = self.load_position(value)
                self.assertEqual(settings.get("display.overlay_position"),
                                 DEFAULT_CONFIG["display"]["overlay_position"])
                self.assert_preserved(settings, original)

    def test_all_four_corners_keep_panel_index_and_overlay_coordinates(self):
        overlay = overlay_for()
        for value, index, coordinates in CORNERS:
            with self.subTest(value=value):
                settings, original = self.load_position(value)
                self.assertEqual(settings.get("display.overlay_position"), value)
                panel = panel_for(settings)
                panel._load_settings()
                self.assertEqual(panel._pos_combo.current, index)
                overlay.set_position(settings.get("display.overlay_position"))
                self.assertEqual(overlay.moves[-1], coordinates)
                self.assert_preserved(settings, original)
        self.assertEqual(overlay.moves, [corner[2] for corner in CORNERS])

    def test_hashable_unknown_values_keep_type_value_and_consumer_fallbacks(self):
        for value in HASHABLE_UNKNOWN_POSITIONS:
            with self.subTest(value=value, type=type(value).__name__):
                settings, original = self.load_position(value)
                actual = settings.get("display.overlay_position")
                self.assertIs(type(actual), type(value))
                if isinstance(value, float) and math.isnan(value):
                    self.assertTrue(math.isnan(actual))
                else:
                    self.assertEqual(actual, value)
                panel = panel_for(settings)
                panel._load_settings()
                self.assertEqual(panel._pos_combo.current, 1)
                overlay = overlay_for()
                overlay.set_position(actual)
                self.assertEqual(overlay.moves, [(1620, 20)])
                self.assert_preserved(settings, original)

    def test_missing_leaf_uses_the_existing_merged_default(self):
        for config in ({}, {"display": {"custom_display_note": "keep"}}):
            with self.subTest(config=config):
                original = self.write_config(config)
                settings = Settings(self.path)
                self.assertEqual(settings.get("display.overlay_position"), "top-right")
                self.assertEqual(self.path.read_bytes(), original)
                if config:
                    self.assertEqual(settings.get("display.custom_display_note"), "keep")

    def test_non_mapping_display_sections_are_unchanged(self):
        for section in (None, "legacy", ["legacy"], False, 7, {"legacy"}):
            with self.subTest(section=section):
                original = self.write_config({"display": section, "custom": "keep"})
                settings = Settings(self.path)
                self.assertIs(type(settings.get_section("display")), type(section))
                self.assertEqual(settings.get_section("display"), section)
                self.assertEqual(settings.get("custom"), "keep")
                self.assertEqual(self.path.read_bytes(), original)

    def test_ordinary_save_persists_admitted_leaf_and_unrelated_values(self):
        settings, original = self.load_position(["bottom-left"])
        self.assert_preserved(settings, original)
        settings.set("general.minimize_to_tray", False)
        saved = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        restored = Settings(self.path)
        self.assertEqual(saved["display"]["overlay_position"], "top-right")
        self.assertEqual(restored.get("display.overlay_position"), "top-right")
        self.assertIs(restored.get("general.minimize_to_tray"), False)
        self.assert_preserved(restored, self.path.read_bytes())

    def test_set_and_set_many_validation_remain_unchanged(self):
        for method in ("set", "set_many"):
            with self.subTest(method=method):
                settings, original = self.load_position("bottom-left")
                for value in (*UNHASHABLE_POSITIONS, *HASHABLE_UNKNOWN_POSITIONS):
                    with self.subTest(value=value):
                        with self.assertRaises(SettingsValidationError):
                            if method == "set":
                                settings.set("display.overlay_position", value)
                            else:
                                settings.set_many({"display.overlay_position": value})
                        self.assertEqual(settings.get("display.overlay_position"), "bottom-left")
                        self.assert_preserved(settings, original)

    def test_explicit_set_validation_bypass_is_not_load_admission(self):
        settings, original = self.load_position("bottom-left")
        settings.set("display.overlay_position", [], save=False, validate=False)
        self.assertEqual(settings.get("display.overlay_position"), [])
        with self.assertRaises(TypeError):
            panel_for(settings)._load_settings()
        with self.assertRaises(TypeError):
            overlay_for().set_position(settings.get("display.overlay_position"))
        self.assert_preserved(settings, original)

    def test_admission_does_not_mutate_defaults_or_another_profile(self):
        defaults = deepcopy(DEFAULT_CONFIG)
        settings, original = self.load_position({})
        other_path = self.path.parent / "other.yaml"
        other_path.write_text("display:\n  overlay_position: bottom-right\n", encoding="utf-8")
        other = Settings(other_path)
        settings.get_section("display")["overlay_position"] = "bottom-left"
        settings.set("notifications.events.safe_full", False, save=False)
        self.assertEqual(other.get("display.overlay_position"), "bottom-right")
        self.assertTrue(other.get("notifications.events.safe_full"))
        self.assertEqual(DEFAULT_CONFIG, defaults)
        self.assertEqual(self.path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
