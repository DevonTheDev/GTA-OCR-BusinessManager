"""Resolution detection and scaling utilities."""

from dataclasses import dataclass
from typing import Optional

import mss


@dataclass
class MonitorInfo:
    """Information about a monitor."""

    index: int
    left: int
    top: int
    width: int
    height: int
    is_primary: bool

    @property
    def resolution(self) -> str:
        """Get resolution as string (e.g., '1920x1080')."""
        return f"{self.width}x{self.height}"

    @property
    def aspect_ratio(self) -> float:
        """Get aspect ratio (width/height)."""
        return self.width / self.height if self.height > 0 else 0


class ResolutionScaler:
    """Handles resolution detection and coordinate scaling."""

    # Common resolutions and their base scale factors (relative to 1080p)
    RESOLUTION_SCALES = {
        (1280, 720): 0.667,
        (1366, 768): 0.711,
        (1600, 900): 0.833,
        (1920, 1080): 1.0,
        (2560, 1440): 1.333,
        (3440, 1440): 1.333,  # Ultrawide
        (3840, 2160): 2.0,
        (5120, 1440): 1.333,  # Super ultrawide
    }

    def __init__(self, monitor_index: int = 0):
        """Initialize resolution scaler.

        Args:
            monitor_index: Index of the monitor to use (0 = primary)
        """
        self._monitor_index = monitor_index
        self._monitors: list[MonitorInfo] = []
        self._active_monitor: Optional[MonitorInfo] = None
        self._scale_factor: float = 1.0
        self._refresh_monitors()

    def _refresh_monitors(self) -> None:
        """Refresh the list of available monitors."""
        self._monitors.clear()

        with mss.mss() as sct:
            # mss.monitors[0] is the "all monitors" virtual monitor
            # Real monitors start at index 1
            for i, mon in enumerate(sct.monitors[1:], start=0):
                self._monitors.append(
                    MonitorInfo(
                        index=i,
                        left=mon["left"],
                        top=mon["top"],
                        width=mon["width"],
                        height=mon["height"],
                        is_primary=(i == 0),
                    )
                )

        # Set active monitor
        if self._monitors:
            idx = min(self._monitor_index, len(self._monitors) - 1)
            self._active_monitor = self._monitors[idx]
            self._calculate_scale_factor()

    def _calculate_scale_factor(self) -> None:
        """Calculate the scale factor for the active monitor."""
        if not self._active_monitor:
            self._scale_factor = 1.0
            return

        width = self._active_monitor.width
        height = self._active_monitor.height

        # Check for exact match first
        if (width, height) in self.RESOLUTION_SCALES:
            self._scale_factor = self.RESOLUTION_SCALES[(width, height)]
        else:
            # Calculate based on height (GTA scales UI based on height)
            self._scale_factor = height / 1080

    @property
    def monitor(self) -> Optional[MonitorInfo]:
        """Get the active monitor info."""
        return self._active_monitor

    @property
    def monitors(self) -> list[MonitorInfo]:
        """Get list of all monitors."""
        return self._monitors.copy()

    @property
    def width(self) -> int:
        """Get active monitor width."""
        return self._active_monitor.width if self._active_monitor else 1920

    @property
    def height(self) -> int:
        """Get active monitor height."""
        return self._active_monitor.height if self._active_monitor else 1080

    @property
    def scale_factor(self) -> float:
        """Get the UI scale factor relative to 1080p."""
        return self._scale_factor

    @property
    def offset(self) -> tuple[int, int]:
        """Get the monitor offset (for multi-monitor setups)."""
        if self._active_monitor:
            return (self._active_monitor.left, self._active_monitor.top)
        return (0, 0)

    def set_monitor(self, index: int) -> bool:
        """Set the active monitor.

        Args:
            index: Monitor index

        Returns:
            True if successful, False if index invalid
        """
        if 0 <= index < len(self._monitors):
            self._monitor_index = index
            self._active_monitor = self._monitors[index]
            self._calculate_scale_factor()
            return True
        return False

    def refresh(self) -> None:
        """Refresh monitor information (call if displays change)."""
        self._refresh_monitors()

    def scale_value(self, value: int) -> int:
        """Scale a pixel value from 1080p base to current resolution.

        Args:
            value: Value in 1080p pixels

        Returns:
            Scaled value for current resolution
        """
        return int(value * self._scale_factor)

    def scale_region(
        self, x: int, y: int, width: int, height: int
    ) -> tuple[int, int, int, int]:
        """Scale a region from 1080p base to current resolution.

        Args:
            x: X position in 1080p
            y: Y position in 1080p
            width: Width in 1080p
            height: Height in 1080p

        Returns:
            Tuple of (x, y, width, height) scaled to current resolution
        """
        return (
            int(x * self._scale_factor),
            int(y * self._scale_factor),
            int(width * self._scale_factor),
            int(height * self._scale_factor),
        )

    def get_mss_monitor_dict(self) -> dict:
        """Get monitor definition for mss.grab().

        Returns:
            Dict with monitor bounds for mss
        """
        if not self._active_monitor:
            return {"left": 0, "top": 0, "width": 1920, "height": 1080}

        return {
            "left": self._active_monitor.left,
            "top": self._active_monitor.top,
            "width": self._active_monitor.width,
            "height": self._active_monitor.height,
        }

    def __repr__(self) -> str:
        if self._active_monitor:
            return (
                f"ResolutionScaler(monitor={self._monitor_index}, "
                f"resolution={self._active_monitor.resolution}, "
                f"scale={self._scale_factor:.3f})"
            )
        return "ResolutionScaler(no monitor)"
