"""Default configuration values for GTA Business Manager."""

from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "general": {
        "character_name": "Default",
        "launch_on_startup": False,
        "minimize_to_tray": True,
        "check_for_updates": True,
    },
    "display": {
        "mode": "overlay",  # "overlay" | "window" | "both"
        "overlay_position": "top-right",  # "top-left" | "top-right" | "bottom-left" | "bottom-right"
        "overlay_opacity": 0.85,
        "overlay_scale": 1.0,
        "always_on_top": True,
    },
    "capture": {
        "resolution": "auto",  # "auto" | "1920x1080" | "2560x1440" | "3840x2160"
        "display_mode": "auto",  # "auto" | "fullscreen" | "borderless" | "windowed"
        "idle_fps": 0.5,  # Captures per second when idle
        "active_fps": 2.0,  # Captures per second during activity
        "business_fps": 4.0,  # Captures per second when viewing business UI
        "monitor_index": 0,  # Which monitor to capture (0 = primary)
    },
    "notifications": {
        "audio_enabled": False,
        "audio_volume": 0.7,
        "desktop_notifications": True,
        "events": {
            "business_ready": True,
            "supplies_low": True,
            "supplies_empty": True,
            "safe_full": True,
            "session_milestone": True,  # e.g., earned $1M this session
        },
    },
    "tracking": {
        "auto_start_session": True,
        "track_money_changes": True,
        "businesses": {
            "cocaine": True,
            "meth": True,
            "cash": True,
            "weed": True,
            "documents": True,
            "bunker": True,
            "nightclub": True,
            "agency": True,
            "acid_lab": True,
            "hangar": True,
            "vehicle_warehouse": True,
            "special_cargo": True,
            "auto_shop": True,
        },
    },
    "hotkeys": {
        "toggle_overlay": "ctrl+shift+g",
        "toggle_tracking": "ctrl+shift+t",
        "show_window": "ctrl+shift+m",
        "mark_activity": "ctrl+shift+a",  # Manual activity marker
    },
    "optimization": {
        "enabled": True,
        "solo_mode": True,  # Affects sell mission recommendations
        "consider_cooldowns": True,
        "prioritize_high_value": True,
    },
    "advanced": {
        "debug_mode": False,
        "save_captures": False,  # Save captured images for debugging
        "capture_save_path": "",  # Empty = default data directory
        "ocr_confidence_threshold": 0.7,
        "template_match_threshold": 0.8,
    },
}

# Resolution presets with their scaling factors
RESOLUTION_PRESETS: dict[str, dict[str, int | float]] = {
    "1920x1080": {"width": 1920, "height": 1080, "scale": 1.0},
    "2560x1440": {"width": 2560, "height": 1440, "scale": 1.333},
    "3840x2160": {"width": 3840, "height": 2160, "scale": 2.0},
    "1280x720": {"width": 1280, "height": 720, "scale": 0.667},
    "1366x768": {"width": 1366, "height": 768, "scale": 0.711},
}
