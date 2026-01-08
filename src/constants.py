"""Centralized constants for GTA Business Manager.

This module consolidates magic numbers and configuration values used
throughout the codebase for easier maintenance and consistency.
"""

from dataclasses import dataclass
from typing import Tuple


# =============================================================================
# UI Constants
# =============================================================================

@dataclass(frozen=True)
class UIConstants:
    """UI-related constants."""

    # Overlay dimensions
    OVERLAY_MIN_WIDTH: int = 280
    OVERLAY_MIN_HEIGHT: int = 180

    # Main window dimensions
    MAIN_WINDOW_WIDTH: int = 900
    MAIN_WINDOW_HEIGHT: int = 650
    MAIN_WINDOW_MIN_WIDTH: int = 800
    MAIN_WINDOW_MIN_HEIGHT: int = 600

    # Panel dimensions
    PANEL_MIN_HEIGHT: int = 200
    BUSINESS_PANEL_MIN_HEIGHT: int = 150

    # Update intervals (milliseconds)
    FAST_UPDATE_INTERVAL_MS: int = 500
    NORMAL_UPDATE_INTERVAL_MS: int = 1000
    SLOW_UPDATE_INTERVAL_MS: int = 2000
    SESSION_UPDATE_INTERVAL_MS: int = 1000
    BUSINESS_UPDATE_INTERVAL_MS: int = 2000

    # Opacity
    DEFAULT_OVERLAY_OPACITY: float = 0.9
    MIN_OVERLAY_OPACITY: float = 0.1
    MAX_OVERLAY_OPACITY: float = 1.0

    # Font sizes
    LARGE_FONT_SIZE: int = 24
    MEDIUM_FONT_SIZE: int = 16
    SMALL_FONT_SIZE: int = 12


UI = UIConstants()


# =============================================================================
# Detection Constants
# =============================================================================

@dataclass(frozen=True)
class DetectionConstants:
    """Detection and OCR-related constants."""

    # Brightness thresholds for state detection
    MIN_BRIGHTNESS: int = 15
    MAX_BRIGHTNESS: int = 200
    LOADING_SCREEN_BRIGHTNESS_MIN: int = 5
    LOADING_SCREEN_BRIGHTNESS_MAX: int = 250

    # Template matching thresholds
    TEMPLATE_MATCH_THRESHOLD: float = 0.8
    ICON_MATCH_THRESHOLD: float = 0.7

    # HSV color ranges for GTA UI elements
    HUD_GREEN_HSV_LOW: Tuple[int, int, int] = (35, 100, 100)
    HUD_GREEN_HSV_HIGH: Tuple[int, int, int] = (85, 255, 255)
    MONEY_GREEN_HSV_LOW: Tuple[int, int, int] = (40, 50, 50)
    MONEY_GREEN_HSV_HIGH: Tuple[int, int, int] = (80, 255, 255)

    # Ratio thresholds for detection
    HUD_VISIBLE_RATIO: float = 0.02
    LOADING_SCREEN_RATIO: float = 0.03
    MENU_VISIBLE_RATIO: float = 0.05

    # OCR preprocessing
    OCR_SCALE_FACTOR: float = 2.0
    OCR_BINARY_THRESHOLD: int = 127

    # Confidence thresholds
    HIGH_CONFIDENCE: float = 0.9
    MEDIUM_CONFIDENCE: float = 0.7
    LOW_CONFIDENCE: float = 0.5


DETECTION = DetectionConstants()


# =============================================================================
# Tracking Constants
# =============================================================================

@dataclass(frozen=True)
class TrackingConstants:
    """Session and activity tracking constants."""

    # History limits
    MAX_ACTIVITY_HISTORY: int = 100
    MAX_STATE_HISTORY: int = 100
    MAX_EARNINGS_HISTORY: int = 50

    # Time thresholds (seconds)
    MIN_ACTIVITY_DURATION: int = 5
    MAX_IDLE_TIME: int = 300  # 5 minutes
    SESSION_TIMEOUT: int = 1800  # 30 minutes

    # Money tracking
    MIN_EARNING_AMOUNT: int = 100
    MAX_SINGLE_EARNING: int = 10_000_000

    # State change debouncing (seconds)
    STATE_DEBOUNCE_TIME: float = 0.5


TRACKING = TrackingConstants()


# =============================================================================
# Business Constants
# =============================================================================

@dataclass(frozen=True)
class BusinessConstants:
    """Business tracking and estimation constants."""

    # Stock/Supply thresholds (percentage)
    LOW_SUPPLY_THRESHOLD: int = 20
    MEDIUM_SUPPLY_THRESHOLD: int = 50
    HIGH_STOCK_THRESHOLD: int = 80
    FULL_THRESHOLD: int = 100

    # Time estimation
    SUPPLY_CHECK_INTERVAL: int = 300  # 5 minutes
    STOCK_UPDATE_INTERVAL: int = 60  # 1 minute

    # Safe maximums
    AGENCY_SAFE_MAX: int = 250_000
    ARCADE_SAFE_MAX: int = 100_000
    NIGHTCLUB_SAFE_MAX: int = 210_000


BUSINESS = BusinessConstants()


# =============================================================================
# Capture Constants
# =============================================================================

@dataclass(frozen=True)
class CaptureConstants:
    """Screen capture related constants."""

    # FPS limits
    MIN_FPS: float = 0.1
    MAX_FPS: float = 60.0
    DEFAULT_IDLE_FPS: float = 0.5
    DEFAULT_ACTIVE_FPS: float = 2.0
    DEFAULT_BUSINESS_FPS: float = 4.0

    # Resolution scaling
    BASE_RESOLUTION_WIDTH: int = 1920
    BASE_RESOLUTION_HEIGHT: int = 1080

    # Region margins (percentage of screen)
    REGION_MARGIN: float = 0.01


CAPTURE = CaptureConstants()


# =============================================================================
# Notification Constants
# =============================================================================

@dataclass(frozen=True)
class NotificationConstants:
    """Audio and visual notification constants."""

    # Rate limiting (seconds)
    MIN_NOTIFICATION_INTERVAL: float = 30.0
    COOLDOWN_SAME_TYPE: float = 60.0

    # Audio
    DEFAULT_VOLUME: float = 0.7
    MAX_VOLUME: float = 1.0

    # Visual notification duration (ms)
    TOAST_DURATION_MS: int = 5000


NOTIFICATION = NotificationConstants()


# =============================================================================
# Color Constants (for UI theming)
# =============================================================================

@dataclass(frozen=True)
class ColorConstants:
    """Color values for UI elements."""

    # Status colors (hex)
    SUCCESS_GREEN: str = "#4CAF50"
    WARNING_YELLOW: str = "#FFC107"
    ERROR_RED: str = "#F44336"
    INFO_BLUE: str = "#2196F3"

    # Business status colors
    STOCK_FULL: str = "#4CAF50"
    STOCK_HIGH: str = "#8BC34A"
    STOCK_MEDIUM: str = "#FFC107"
    STOCK_LOW: str = "#FF9800"
    STOCK_EMPTY: str = "#F44336"

    # GTA-inspired colors
    GTA_GREEN: str = "#9ACD32"
    GTA_GOLD: str = "#FFD700"
    GTA_DARK_BG: str = "#1a1a2e"


COLORS = ColorConstants()
