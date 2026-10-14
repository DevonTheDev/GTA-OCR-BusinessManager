"""Real YAML admission and persistence, runnable without GUI/worker dependencies."""

from copy import deepcopy
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import yaml

from src.config.defaults import DEFAULT_CONFIG
from src.config.settings import Settings, SettingsValidationError


FPS_DEFAULTS = {"idle_fps": 0.5, "active_fps": 2.0, "business_fps": 4.0}
VALID_RATES = {"idle_fps": 0.25, "active_fps": 3.0, "business_fps": 5.0}


class TestCaptureSettingsAdmission(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "settings.yaml"

    def write_config(self, config):
        self.path.write_text(yaml.safe_dump(config), encoding="utf-8")
        return self.path.read_bytes()

    def test_invalid_saved_fps_falls_back_only_at_its_leaf(self):
        invalid_values = (
            None, "fast", "1.0", [], {}, False, 0.05, 61,
            float("nan"), float("inf"), float("-inf"), 10**400,
        )
        for field, default in FPS_DEFAULTS.items():
            for invalid in invalid_values:
                with self.subTest(field=field, value=invalid):
                    rates = dict(VALID_RATES, **{field: invalid})
                    original = self.write_config({
                        "capture": dict(rates, custom_rate_note="keep"),
                        "general": {"character_name": "Retained character"},
                        "custom": {"nested": ["retained", 42]},
                    })
                    settings = Settings(self.path)
                    self.assertEqual(settings.get(f"capture.{field}"), default)
                    for sibling in FPS_DEFAULTS.keys() - {field}:
                        self.assertEqual(settings.get(f"capture.{sibling}"), rates[sibling])
                    self.assertEqual(settings.get("capture.custom_rate_note"), "keep")
                    self.assertEqual(settings.get("general.character_name"), "Retained character")
                    self.assertEqual(settings.get("custom.nested"), ["retained", 42])
                    self.assertEqual(self.path.read_bytes(), original)

    def test_valid_saved_rates_keep_their_type_and_value(self):
        for field in FPS_DEFAULTS:
            for value in (0.1, 0.5, 2, 60.0, True):
                with self.subTest(field=field, value=value):
                    original = self.write_config({"capture": {field: value}})
                    settings = Settings(self.path)
                    actual = settings.get(f"capture.{field}")
                    self.assertIs(type(actual), type(value))
                    self.assertEqual(actual, value)
                    self.assertEqual(self.path.read_bytes(), original)

    def test_missing_fields_merge_the_existing_defaults(self):
        for config in ({}, {"capture": {"active_fps": 3.5}}):
            with self.subTest(config=config):
                original = self.write_config(config)
                settings = Settings(self.path)
                for field, default in FPS_DEFAULTS.items():
                    expected = config.get("capture", {}).get(field, default)
                    self.assertEqual(settings.get(f"capture.{field}"), expected)
                self.assertEqual(self.path.read_bytes(), original)

    def test_non_mapping_capture_keeps_existing_get_fallback(self):
        for section in (None, "legacy", ["legacy"], False):
            with self.subTest(section=section):
                original = self.write_config({"capture": section, "custom": "keep"})
                settings = Settings(self.path)
                self.assertEqual(settings.get_section("capture"), section)
                for field, default in FPS_DEFAULTS.items():
                    self.assertEqual(settings.get(f"capture.{field}", default), default)
                self.assertEqual(settings.get("custom"), "keep")
                self.assertEqual(self.path.read_bytes(), original)

    def test_normal_save_persists_admitted_defaults_and_unrelated_values(self):
        original = self.write_config({
            "capture": {"idle_fps": "fast", "active_fps": None, "business_fps": 61},
            "general": {"character_name": "Retained character"},
            "custom": {"nested": ["retained", 42]},
        })
        settings = Settings(self.path)
        self.assertEqual(self.path.read_bytes(), original)

        settings.set("general.minimize_to_tray", False)

        saved = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        restored = Settings(self.path)
        for field, default in FPS_DEFAULTS.items():
            self.assertEqual(saved["capture"][field], default)
            self.assertEqual(restored.get(f"capture.{field}"), default)
        self.assertFalse(restored.get("general.minimize_to_tray"))
        self.assertEqual(restored.get("general.character_name"), "Retained character")
        self.assertEqual(restored.get("custom.nested"), ["retained", 42])
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_nonfinite_set_is_rejected_without_memory_or_file_changes(self):
        for field in FPS_DEFAULTS:
            for invalid in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field, value=invalid):
                    original = self.write_config({"capture": VALID_RATES})
                    settings = Settings(self.path)
                    with self.assertRaises(SettingsValidationError):
                        settings.set(f"capture.{field}", invalid)
                    self.assertEqual(settings.get(f"capture.{field}"), VALID_RATES[field])
                    self.assertEqual(self.path.read_bytes(), original)

    def test_set_retains_numeric_string_and_boolean_contract(self):
        settings = Settings(self.path)
        for field in FPS_DEFAULTS:
            with self.subTest(field=field):
                with self.assertRaises(SettingsValidationError):
                    settings.set(f"capture.{field}", "1.0", save=False)
                settings.set(f"capture.{field}", True, save=False)
                self.assertIs(settings.get(f"capture.{field}"), True)
                with self.assertRaises(SettingsValidationError):
                    settings.set(f"capture.{field}", False, save=False)

    def test_explicit_validation_bypass_and_other_numeric_rules_are_unchanged(self):
        settings = Settings(self.path)
        for field in FPS_DEFAULTS:
            settings.set(f"capture.{field}", float("nan"), save=False, validate=False)
            self.assertTrue(math.isnan(settings.get(f"capture.{field}")))
        # This correction is deliberately limited to capture FPS validation.
        settings.set("advanced.ocr_confidence_threshold", float("nan"), save=False)
        self.assertTrue(math.isnan(settings.get("advanced.ocr_confidence_threshold")))

    def test_admission_does_not_mutate_defaults_or_another_profile(self):
        defaults = deepcopy(DEFAULT_CONFIG)
        self.write_config({"capture": {"idle_fps": "fast"}})
        settings = Settings(self.path)
        other = Settings(self.path.parent / "other.yaml")
        settings.set("capture.active_fps", 7, save=False)
        settings.set("notifications.events.safe_full", False, save=False)
        self.assertEqual(DEFAULT_CONFIG, defaults)
        self.assertEqual(other.get("capture.active_fps"), 2.0)
        self.assertTrue(other.get("notifications.events.safe_full"))


if __name__ == "__main__":
    unittest.main()
