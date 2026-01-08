"""Tests for settings validation."""

import pytest
import tempfile
from pathlib import Path

from src.config.settings import Settings, SettingsValidationError


class TestSettingsValidation:
    """Tests for settings validation."""

    @pytest.fixture
    def settings(self, tmp_path):
        """Create a settings instance with a temp config file."""
        config_path = tmp_path / "config.yaml"
        return Settings(config_path)

    def test_valid_fps_setting(self, settings):
        """Test valid FPS values are accepted."""
        settings.set("capture.idle_fps", 1.0)
        assert settings.get("capture.idle_fps") == 1.0

        settings.set("capture.active_fps", 30)
        assert settings.get("capture.active_fps") == 30

    def test_fps_below_minimum(self, settings):
        """Test FPS below minimum raises error."""
        with pytest.raises(SettingsValidationError) as exc:
            settings.set("capture.idle_fps", 0.05)
        assert "must be >= 0.1" in str(exc.value)

    def test_fps_above_maximum(self, settings):
        """Test FPS above maximum raises error."""
        with pytest.raises(SettingsValidationError) as exc:
            settings.set("capture.idle_fps", 100)
        assert "must be <= 60" in str(exc.value)

    def test_negative_fps(self, settings):
        """Test negative FPS raises error."""
        with pytest.raises(SettingsValidationError) as exc:
            settings.set("capture.active_fps", -1)
        assert "must be >= 0.1" in str(exc.value)

    def test_valid_opacity(self, settings):
        """Test valid opacity values are accepted."""
        settings.set("display.overlay_opacity", 0.5)
        assert settings.get("display.overlay_opacity") == 0.5

        settings.set("display.overlay_opacity", 0.0)
        assert settings.get("display.overlay_opacity") == 0.0

        settings.set("display.overlay_opacity", 1.0)
        assert settings.get("display.overlay_opacity") == 1.0

    def test_opacity_out_of_range(self, settings):
        """Test opacity outside 0-1 range raises error."""
        with pytest.raises(SettingsValidationError) as exc:
            settings.set("display.overlay_opacity", 1.5)
        assert "must be <= 1.0" in str(exc.value)

        with pytest.raises(SettingsValidationError) as exc:
            settings.set("display.overlay_opacity", -0.1)
        assert "must be >= 0.0" in str(exc.value)

    def test_valid_monitor_index(self, settings):
        """Test valid monitor index values are accepted."""
        settings.set("capture.monitor_index", 0)
        assert settings.get("capture.monitor_index") == 0

        settings.set("capture.monitor_index", 2)
        assert settings.get("capture.monitor_index") == 2

    def test_negative_monitor_index(self, settings):
        """Test negative monitor index raises error."""
        with pytest.raises(SettingsValidationError) as exc:
            settings.set("capture.monitor_index", -1)
        assert "must be >= 0" in str(exc.value)

    def test_valid_display_mode(self, settings):
        """Test valid display mode values are accepted."""
        for mode in ["overlay", "window", "both"]:
            settings.set("display.mode", mode)
            assert settings.get("display.mode") == mode

    def test_invalid_display_mode(self, settings):
        """Test invalid display mode raises error."""
        with pytest.raises(SettingsValidationError) as exc:
            settings.set("display.mode", "invalid")
        assert "must be one of" in str(exc.value)

    def test_valid_overlay_position(self, settings):
        """Test valid overlay position values are accepted."""
        valid_positions = ["top-left", "top-right", "bottom-left", "bottom-right"]
        for pos in valid_positions:
            settings.set("display.overlay_position", pos)
            assert settings.get("display.overlay_position") == pos

    def test_invalid_overlay_position(self, settings):
        """Test invalid overlay position raises error."""
        with pytest.raises(SettingsValidationError) as exc:
            settings.set("display.overlay_position", "center")
        assert "must be one of" in str(exc.value)

    def test_boolean_settings(self, settings):
        """Test boolean settings accept True/False."""
        settings.set("display.always_on_top", True)
        assert settings.get("display.always_on_top") is True

        settings.set("display.always_on_top", False)
        assert settings.get("display.always_on_top") is False

    def test_boolean_wrong_type(self, settings):
        """Test boolean setting rejects non-boolean."""
        with pytest.raises(SettingsValidationError) as exc:
            settings.set("display.always_on_top", "yes")
        assert "must be of type" in str(exc.value)

    def test_skip_validation(self, settings):
        """Test validation can be skipped."""
        # This would normally raise an error
        settings.set("capture.idle_fps", -100, validate=False)
        assert settings.get("capture.idle_fps") == -100

    def test_unvalidated_keys_allowed(self, settings):
        """Test keys without validation rules accept any value."""
        # Custom key not in validation rules
        settings.set("custom.setting", "any value")
        assert settings.get("custom.setting") == "any value"

        settings.set("custom.nested.value", 12345)
        assert settings.get("custom.nested.value") == 12345

    def test_audio_volume_validation(self, settings):
        """Test audio volume validation."""
        settings.set("notifications.audio_volume", 0.5)
        assert settings.get("notifications.audio_volume") == 0.5

        with pytest.raises(SettingsValidationError):
            settings.set("notifications.audio_volume", 1.5)

    def test_threshold_validation(self, settings):
        """Test OCR threshold validation."""
        settings.set("advanced.ocr_confidence_threshold", 0.8)
        assert settings.get("advanced.ocr_confidence_threshold") == 0.8

        with pytest.raises(SettingsValidationError):
            settings.set("advanced.ocr_confidence_threshold", 2.0)

    def test_settings_persist(self, settings):
        """Test that validated settings are saved."""
        settings.set("capture.idle_fps", 2.0)

        # Create new settings instance with same path
        settings2 = Settings(settings.config_path)
        assert settings2.get("capture.idle_fps") == 2.0
