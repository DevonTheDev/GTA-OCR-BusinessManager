"""Screen region definitions for GTA V UI elements."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import NamedTuple


class RegionType(Enum):
    """Types of screen regions to capture."""

    MONEY_DISPLAY = auto()  # Top-right corner money display
    MISSION_TEXT = auto()  # Top-center mission objective text
    MISSION_BANNER = auto()  # Large mission name banner
    TIMER_DISPLAY = auto()  # Timer (bottom-right or center)
    MINIMAP = auto()  # Bottom-left minimap area
    CENTER_PROMPT = auto()  # Center screen prompts/notifications
    BUSINESS_STOCK = auto()  # Business computer stock display
    BUSINESS_SUPPLIES = auto()  # Business computer supplies display
    PHONE_SCREEN = auto()  # In-game phone when open
    FULL_SCREEN = auto()  # Full screen capture (for template matching)


class Region(NamedTuple):
    """A screen region defined by relative coordinates (0.0 to 1.0)."""

    x: float  # Left edge (0.0 = left, 1.0 = right)
    y: float  # Top edge (0.0 = top, 1.0 = bottom)
    width: float  # Width as fraction of screen
    height: float  # Height as fraction of screen

    def to_absolute(self, screen_width: int, screen_height: int) -> tuple[int, int, int, int]:
        """Convert relative region to absolute pixel coordinates.

        Args:
            screen_width: Screen width in pixels
            screen_height: Screen height in pixels

        Returns:
            Tuple of (left, top, right, bottom) in pixels
        """
        left = int(self.x * screen_width)
        top = int(self.y * screen_height)
        right = int((self.x + self.width) * screen_width)
        bottom = int((self.y + self.height) * screen_height)
        return (left, top, right, bottom)

    def to_mss_monitor(self, screen_width: int, screen_height: int, offset_x: int = 0, offset_y: int = 0) -> dict:
        """Convert to mss monitor dict format.

        Args:
            screen_width: Screen width in pixels
            screen_height: Screen height in pixels
            offset_x: X offset of the monitor
            offset_y: Y offset of the monitor

        Returns:
            Dict compatible with mss.grab()
        """
        left, top, right, bottom = self.to_absolute(screen_width, screen_height)
        return {
            "left": left + offset_x,
            "top": top + offset_y,
            "width": right - left,
            "height": bottom - top,
        }


@dataclass
class ScreenRegions:
    """Collection of all GTA V screen regions.

    All coordinates are relative (0.0 to 1.0) to support any resolution.
    Based on GTA V's default HUD layout.
    """

    # Money display (top-right, shows current cash/bank)
    # Format: "$1,234,567" or "CASH $XXX | BANK $XXX"
    money_display: Region = Region(x=0.78, y=0.015, width=0.21, height=0.045)

    # Mission objective text (top-center)
    # Shows current mission objectives like "Go to the location"
    mission_text: Region = Region(x=0.25, y=0.02, width=0.50, height=0.08)

    # Mission banner (center, appears when mission starts)
    # Large text showing mission name
    mission_banner: Region = Region(x=0.20, y=0.35, width=0.60, height=0.15)

    # Timer display (usually bottom-right or center-bottom)
    # Shows countdown timers during missions
    timer_bottom_right: Region = Region(x=0.85, y=0.90, width=0.14, height=0.06)
    timer_center: Region = Region(x=0.42, y=0.88, width=0.16, height=0.06)

    # Minimap area (bottom-left)
    # Can detect player state from minimap icons
    minimap: Region = Region(x=0.01, y=0.75, width=0.20, height=0.24)

    # Center screen prompt area
    # Used for notifications, prompts, and interaction messages
    center_prompt: Region = Region(x=0.25, y=0.45, width=0.50, height=0.15)

    # Business computer regions (when viewing business laptop)
    # Stock level display
    business_stock: Region = Region(x=0.55, y=0.35, width=0.25, height=0.08)

    # Supplies level display
    business_supplies: Region = Region(x=0.55, y=0.45, width=0.25, height=0.08)

    # Business value display
    business_value: Region = Region(x=0.55, y=0.55, width=0.25, height=0.08)

    # Phone screen (when phone is open)
    phone_screen: Region = Region(x=0.65, y=0.25, width=0.30, height=0.55)

    # Heist board regions
    heist_header: Region = Region(x=0.30, y=0.10, width=0.40, height=0.10)

    # Session earnings popup (appears after completing activities)
    earnings_popup: Region = Region(x=0.35, y=0.35, width=0.30, height=0.30)

    # RP and money earned display (bottom-center after missions)
    reward_display: Region = Region(x=0.30, y=0.75, width=0.40, height=0.15)

    # Loading screen text (mission name during load)
    loading_text: Region = Region(x=0.05, y=0.85, width=0.90, height=0.10)

    # Full screen (for template matching, state detection)
    full_screen: Region = Region(x=0.0, y=0.0, width=1.0, height=1.0)

    def get_region(self, region_type: RegionType) -> Region:
        """Get a specific region by type.

        Args:
            region_type: The type of region to get

        Returns:
            The Region definition
        """
        mapping = {
            RegionType.MONEY_DISPLAY: self.money_display,
            RegionType.MISSION_TEXT: self.mission_text,
            RegionType.MISSION_BANNER: self.mission_banner,
            RegionType.TIMER_DISPLAY: self.timer_bottom_right,
            RegionType.MINIMAP: self.minimap,
            RegionType.CENTER_PROMPT: self.center_prompt,
            RegionType.BUSINESS_STOCK: self.business_stock,
            RegionType.BUSINESS_SUPPLIES: self.business_supplies,
            RegionType.PHONE_SCREEN: self.phone_screen,
            RegionType.FULL_SCREEN: self.full_screen,
        }
        return mapping.get(region_type, self.full_screen)

    def get_all_hud_regions(self) -> dict[str, Region]:
        """Get all HUD-related regions for monitoring.

        Returns:
            Dict mapping region names to Region objects
        """
        return {
            "money": self.money_display,
            "mission_text": self.mission_text,
            "timer_bottom": self.timer_bottom_right,
            "timer_center": self.timer_center,
            "center_prompt": self.center_prompt,
        }

    def get_business_regions(self) -> dict[str, Region]:
        """Get business computer regions.

        Returns:
            Dict mapping region names to Region objects
        """
        return {
            "stock": self.business_stock,
            "supplies": self.business_supplies,
            "value": self.business_value,
        }


# Default regions instance
DEFAULT_REGIONS = ScreenRegions()
