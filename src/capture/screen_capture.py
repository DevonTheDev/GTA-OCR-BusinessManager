"""Screen capture functionality for GTA Business Manager."""

import time
from typing import Optional

import mss
import mss.tools
import numpy as np
from PIL import Image

from ..utils.logging import get_logger
from .regions import Region, ScreenRegions, DEFAULT_REGIONS
from .resolution import ResolutionScaler


logger = get_logger("capture")


class ScreenCapture:
    """Handles screen capture with region support and adaptive rate limiting."""

    def __init__(
        self,
        monitor_index: int = 0,
        regions: Optional[ScreenRegions] = None,
    ):
        """Initialize screen capture.

        Args:
            monitor_index: Which monitor to capture (0 = primary)
            regions: Screen regions definition. If None, uses defaults.
        """
        self._scaler = ResolutionScaler(monitor_index)
        self._regions = regions or DEFAULT_REGIONS
        self._sct: Optional[mss.mss] = None
        self._last_capture_time: float = 0
        self._min_capture_interval: float = 0.0  # Seconds between captures

        logger.info(
            f"ScreenCapture initialized: {self._scaler.width}x{self._scaler.height} "
            f"(scale: {self._scaler.scale_factor:.2f})"
        )

    def _ensure_mss(self) -> mss.mss:
        """Ensure mss instance exists and return it."""
        if self._sct is None:
            self._sct = mss.mss()
        return self._sct

    def set_capture_rate(self, fps: float) -> None:
        """Set the maximum capture rate.

        Args:
            fps: Maximum captures per second (0 = unlimited)
        """
        self._min_capture_interval = 1.0 / fps if fps > 0 else 0.0

    def _should_capture(self) -> bool:
        """Check if enough time has passed since last capture."""
        if self._min_capture_interval <= 0:
            return True
        return (time.time() - self._last_capture_time) >= self._min_capture_interval

    def _wait_for_rate_limit(self) -> None:
        """Wait if necessary to respect rate limiting."""
        if self._min_capture_interval > 0:
            elapsed = time.time() - self._last_capture_time
            if elapsed < self._min_capture_interval:
                time.sleep(self._min_capture_interval - elapsed)

    def capture_region(self, region: Region, wait_for_rate: bool = True) -> Optional[np.ndarray]:
        """Capture a specific screen region.

        Args:
            region: Region to capture
            wait_for_rate: Whether to wait for rate limiting

        Returns:
            Numpy array (BGR format) or None on failure
        """
        if wait_for_rate:
            self._wait_for_rate_limit()

        try:
            sct = self._ensure_mss()

            # Convert relative region to absolute coordinates
            monitor_dict = region.to_mss_monitor(
                self._scaler.width,
                self._scaler.height,
                self._scaler.offset[0],
                self._scaler.offset[1],
            )

            # Capture the region
            screenshot = sct.grab(monitor_dict)

            self._last_capture_time = time.time()

            # Convert to numpy array (BGRA -> BGR)
            img = np.array(screenshot)
            return img[:, :, :3]  # Remove alpha channel

        except Exception as e:
            logger.error(f"Failed to capture region: {e}")
            return None

    def capture_full_screen(self, wait_for_rate: bool = True) -> Optional[np.ndarray]:
        """Capture the full screen.

        Args:
            wait_for_rate: Whether to wait for rate limiting

        Returns:
            Numpy array (BGR format) or None on failure
        """
        return self.capture_region(self._regions.full_screen, wait_for_rate)

    def capture_money_display(self) -> Optional[np.ndarray]:
        """Capture the money display region."""
        return self.capture_region(self._regions.money_display)

    def capture_mission_text(self) -> Optional[np.ndarray]:
        """Capture the mission text region."""
        return self.capture_region(self._regions.mission_text)

    def capture_timer(self) -> Optional[np.ndarray]:
        """Capture the timer display region."""
        return self.capture_region(self._regions.timer_bottom_right)

    def capture_center_prompt(self) -> Optional[np.ndarray]:
        """Capture the center prompt region."""
        return self.capture_region(self._regions.center_prompt)

    def capture_multiple_regions(
        self, regions: list[Region]
    ) -> dict[int, Optional[np.ndarray]]:
        """Capture multiple regions efficiently.

        Captures are done without rate limiting between them,
        only the first capture respects rate limiting.

        Args:
            regions: List of regions to capture

        Returns:
            Dict mapping region index to captured image
        """
        results = {}
        for i, region in enumerate(regions):
            # Only rate limit the first capture
            results[i] = self.capture_region(region, wait_for_rate=(i == 0))
        return results

    def capture_to_pil(self, region: Region) -> Optional[Image.Image]:
        """Capture a region and return as PIL Image.

        Args:
            region: Region to capture

        Returns:
            PIL Image (RGB format) or None on failure
        """
        img = self.capture_region(region)
        if img is not None:
            # Convert BGR to RGB for PIL
            return Image.fromarray(img[:, :, ::-1])
        return None

    def save_capture(self, region: Region, filepath: str) -> bool:
        """Capture a region and save to file.

        Args:
            region: Region to capture
            filepath: Path to save the image

        Returns:
            True if successful
        """
        img = self.capture_to_pil(region)
        if img:
            try:
                img.save(filepath)
                logger.debug(f"Saved capture to {filepath}")
                return True
            except Exception as e:
                logger.error(f"Failed to save capture: {e}")
        return False

    def get_region_size(self, region: Region) -> tuple[int, int]:
        """Get the pixel size of a region.

        Args:
            region: Region to measure

        Returns:
            Tuple of (width, height) in pixels
        """
        left, top, right, bottom = region.to_absolute(
            self._scaler.width, self._scaler.height
        )
        return (right - left, bottom - top)

    @property
    def resolution(self) -> tuple[int, int]:
        """Get current capture resolution."""
        return (self._scaler.width, self._scaler.height)

    @property
    def scale_factor(self) -> float:
        """Get current scale factor."""
        return self._scaler.scale_factor

    @property
    def regions(self) -> ScreenRegions:
        """Get the screen regions definition."""
        return self._regions

    def set_monitor(self, index: int) -> bool:
        """Change the capture monitor.

        Args:
            index: Monitor index

        Returns:
            True if successful
        """
        success = self._scaler.set_monitor(index)
        if success:
            logger.info(
                f"Switched to monitor {index}: "
                f"{self._scaler.width}x{self._scaler.height}"
            )
        return success

    def refresh_monitors(self) -> None:
        """Refresh monitor information."""
        self._scaler.refresh()
        logger.info(f"Monitors refreshed: {len(self._scaler.monitors)} found")

    def close(self) -> None:
        """Clean up resources."""
        if self._sct:
            self._sct.close()
            self._sct = None

    def __enter__(self) -> "ScreenCapture":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __repr__(self) -> str:
        return (
            f"ScreenCapture(resolution={self._scaler.width}x{self._scaler.height}, "
            f"monitor={self._scaler.monitor.index if self._scaler.monitor else 'None'})"
        )
