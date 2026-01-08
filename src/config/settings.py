"""Settings manager for GTA Business Manager."""

import os
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from .defaults import DEFAULT_CONFIG


class SettingsValidationError(ValueError):
    """Raised when a settings value fails validation."""
    pass


# Validation rules for settings
# Format: "key.path" -> (type, min_value, max_value, allowed_values)
# type: expected type or tuple of types
# min_value/max_value: for numeric bounds (None = no bound)
# allowed_values: set of valid values (None = any value)
_VALIDATION_RULES: dict[str, tuple[type, Optional[float], Optional[float], Optional[set]]] = {
    # FPS settings
    "capture.idle_fps": ((int, float), 0.1, 60.0, None),
    "capture.active_fps": ((int, float), 0.1, 60.0, None),
    "capture.business_fps": ((int, float), 0.1, 60.0, None),
    "capture.monitor_index": (int, 0, 99, None),

    # Display settings
    "display.overlay_opacity": ((int, float), 0.0, 1.0, None),
    "display.overlay_scale": ((int, float), 0.5, 3.0, None),
    "display.mode": (str, None, None, {"overlay", "window", "both"}),
    "display.overlay_position": (str, None, None, {"top-left", "top-right", "bottom-left", "bottom-right"}),
    "display.always_on_top": (bool, None, None, None),

    # Audio settings
    "notifications.audio_volume": ((int, float), 0.0, 1.0, None),
    "notifications.audio_enabled": (bool, None, None, None),
    "notifications.desktop_notifications": (bool, None, None, None),

    # Advanced settings
    "advanced.ocr_confidence_threshold": ((int, float), 0.0, 1.0, None),
    "advanced.template_match_threshold": ((int, float), 0.0, 1.0, None),
    "advanced.debug_mode": (bool, None, None, None),
    "advanced.save_captures": (bool, None, None, None),

    # General settings
    "general.launch_on_startup": (bool, None, None, None),
    "general.minimize_to_tray": (bool, None, None, None),
    "general.check_for_updates": (bool, None, None, None),

    # Optimization settings
    "optimization.enabled": (bool, None, None, None),
    "optimization.solo_mode": (bool, None, None, None),
    "optimization.consider_cooldowns": (bool, None, None, None),
    "optimization.prioritize_high_value": (bool, None, None, None),

    # Tracking settings
    "tracking.auto_start_session": (bool, None, None, None),
    "tracking.track_money_changes": (bool, None, None, None),
}


class Settings:
    """Manages application settings with YAML persistence."""

    def __init__(self, config_path: Path | None = None):
        """Initialize settings manager.

        Args:
            config_path: Path to config file. If None, uses default location.
        """
        if config_path is None:
            config_path = self._get_default_config_path()

        self._config_path = Path(config_path)
        self._config: dict[str, Any] = {}
        self._load()

    def _get_default_config_path(self) -> Path:
        """Get the default configuration file path."""
        # Use AppData/Local on Windows for user-specific data
        if os.name == "nt":
            app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        else:
            app_data = Path.home() / ".config"

        config_dir = app_data / "GTABusinessManager"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "config.yaml"

    def _load(self) -> None:
        """Load configuration from file, creating with defaults if needed."""
        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                # Merge with defaults (loaded values override defaults)
                self._config = self._deep_merge(DEFAULT_CONFIG.copy(), loaded)
            except Exception as e:
                print(f"Warning: Failed to load config, using defaults: {e}")
                self._config = DEFAULT_CONFIG.copy()
        else:
            self._config = DEFAULT_CONFIG.copy()
            self._save()

    def _save(self) -> None:
        """Save current configuration to file."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_path, "w", encoding="utf-8") as f:
            yaml.dump(self._config, f, default_flow_style=False, sort_keys=False)

    def _deep_merge(self, base: dict, override: dict) -> dict:
        """Deep merge two dictionaries, with override taking precedence."""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def get(self, key: str, default: Any = None) -> Any:
        """Get a setting value using dot notation.

        Args:
            key: Setting key in dot notation (e.g., "capture.idle_fps")
            default: Default value if key not found

        Returns:
            Setting value or default
        """
        keys = key.split(".")
        value = self._config
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default

    def _validate(self, key: str, value: Any) -> Any:
        """Validate a setting value against the rules.

        Args:
            key: Setting key in dot notation
            value: Value to validate

        Returns:
            The validated (possibly coerced) value

        Raises:
            SettingsValidationError: If validation fails
        """
        if key not in _VALIDATION_RULES:
            # No validation rule, allow any value
            return value

        expected_type, min_val, max_val, allowed = _VALIDATION_RULES[key]

        # Type check
        if not isinstance(value, expected_type):
            raise SettingsValidationError(
                f"Setting '{key}' must be of type {expected_type.__name__ if isinstance(expected_type, type) else expected_type}, "
                f"got {type(value).__name__}"
            )

        # Bounds check for numeric types
        if isinstance(value, (int, float)):
            if min_val is not None and value < min_val:
                raise SettingsValidationError(
                    f"Setting '{key}' must be >= {min_val}, got {value}"
                )
            if max_val is not None and value > max_val:
                raise SettingsValidationError(
                    f"Setting '{key}' must be <= {max_val}, got {value}"
                )

        # Allowed values check
        if allowed is not None and value not in allowed:
            raise SettingsValidationError(
                f"Setting '{key}' must be one of {allowed}, got '{value}'"
            )

        return value

    def set(self, key: str, value: Any, save: bool = True, validate: bool = True) -> None:
        """Set a setting value using dot notation.

        Args:
            key: Setting key in dot notation (e.g., "capture.idle_fps")
            value: Value to set
            save: Whether to immediately save to file
            validate: Whether to validate the value (default True)

        Raises:
            SettingsValidationError: If validation is enabled and fails
        """
        if validate:
            value = self._validate(key, value)

        keys = key.split(".")
        config = self._config

        # Navigate to the parent dict
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]

        # Set the value
        config[keys[-1]] = value

        if save:
            self._save()

    def get_section(self, section: str) -> dict[str, Any]:
        """Get an entire configuration section.

        Args:
            section: Section name (e.g., "capture", "notifications")

        Returns:
            Section dictionary or empty dict if not found
        """
        return self._config.get(section, {})

    def update_section(self, section: str, values: dict[str, Any], save: bool = True) -> None:
        """Update multiple values in a section.

        Args:
            section: Section name
            values: Dictionary of values to update
            save: Whether to immediately save to file
        """
        if section not in self._config:
            self._config[section] = {}
        self._config[section].update(values)

        if save:
            self._save()

    def reset_to_defaults(self, save: bool = True) -> None:
        """Reset all settings to defaults."""
        self._config = DEFAULT_CONFIG.copy()
        if save:
            self._save()

    def reset_section(self, section: str, save: bool = True) -> None:
        """Reset a specific section to defaults."""
        if section in DEFAULT_CONFIG:
            self._config[section] = DEFAULT_CONFIG[section].copy()
            if save:
                self._save()

    @property
    def config_path(self) -> Path:
        """Get the configuration file path."""
        return self._config_path

    @property
    def data_dir(self) -> Path:
        """Get the data directory (same as config dir)."""
        return self._config_path.parent

    def __repr__(self) -> str:
        return f"Settings(config_path={self._config_path})"


# Global settings instance (initialized lazily)
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get the global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
