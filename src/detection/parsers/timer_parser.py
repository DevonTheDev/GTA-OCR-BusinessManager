"""Parser for extracting timer values from OCR text."""

import re
from typing import Optional
from dataclasses import dataclass

from ...utils.logging import get_logger


logger = get_logger("parser.timer")


@dataclass
class TimerReading:
    """Parsed timer reading from screen."""

    hours: int = 0
    minutes: int = 0
    seconds: int = 0
    total_seconds: int = 0
    raw_text: str = ""
    is_countdown: bool = True  # True for countdown, False for elapsed

    @property
    def has_value(self) -> bool:
        """Check if a timer value was parsed."""
        return self.total_seconds > 0 or (
            self.hours == 0 and self.minutes == 0 and self.seconds == 0
            and "0:00" in self.raw_text
        )

    @property
    def formatted(self) -> str:
        """Get formatted time string."""
        if self.hours > 0:
            return f"{self.hours}:{self.minutes:02d}:{self.seconds:02d}"
        return f"{self.minutes}:{self.seconds:02d}"


class TimerParser:
    """Parser for GTA timer displays."""

    # Pattern for MM:SS format
    MMSS_PATTERN = re.compile(r"(\d{1,2})\s*[:;.]\s*(\d{2})")

    # Pattern for HH:MM:SS format
    HHMMSS_PATTERN = re.compile(r"(\d{1,2})\s*[:;.]\s*(\d{2})\s*[:;.]\s*(\d{2})")

    # Pattern for just seconds (less common)
    SECONDS_PATTERN = re.compile(r"^(\d{1,3})\s*(?:s|sec)?$", re.IGNORECASE)

    # OCR corrections for timer digits
    OCR_CORRECTIONS = {
        "O": "0",
        "o": "0",
        "I": "1",
        "l": "1",
        "|": "1",
        "!": "1",
        ";": ":",  # Common OCR mistake for colon
        ".": ":",  # Sometimes periods are read as colons
    }

    def __init__(self):
        """Initialize timer parser."""
        self._last_valid_reading: Optional[TimerReading] = None

    def parse(self, text: str) -> TimerReading:
        """Parse timer value from OCR text.

        Args:
            text: Raw OCR text from timer region

        Returns:
            TimerReading with parsed values
        """
        if not text or not text.strip():
            return TimerReading(raw_text=text)

        # Clean the text
        cleaned = self._clean_text(text)

        # Try HH:MM:SS first
        result = self._parse_hhmmss(cleaned)
        if result.has_value:
            self._last_valid_reading = result
            return result

        # Try MM:SS
        result = self._parse_mmss(cleaned)
        if result.has_value:
            self._last_valid_reading = result
            return result

        # Try just seconds
        result = self._parse_seconds(cleaned)
        if result.has_value:
            self._last_valid_reading = result
            return result

        return TimerReading(raw_text=text)

    def _clean_text(self, text: str) -> str:
        """Clean OCR text for timer parsing."""
        cleaned = text.strip()

        # Apply OCR corrections
        for wrong, right in self.OCR_CORRECTIONS.items():
            cleaned = cleaned.replace(wrong, right)

        return cleaned

    def _parse_hhmmss(self, text: str) -> TimerReading:
        """Parse HH:MM:SS format."""
        match = self.HHMMSS_PATTERN.search(text)
        if match:
            try:
                hours = int(match.group(1))
                minutes = int(match.group(2))
                seconds = int(match.group(3))

                # Validate ranges
                if 0 <= hours <= 99 and 0 <= minutes <= 59 and 0 <= seconds <= 59:
                    total = hours * 3600 + minutes * 60 + seconds
                    return TimerReading(
                        hours=hours,
                        minutes=minutes,
                        seconds=seconds,
                        total_seconds=total,
                        raw_text=text,
                    )
                else:
                    logger.debug(f"Timer HH:MM:SS out of range: {hours}:{minutes}:{seconds}")
            except ValueError as e:
                logger.debug(f"Failed to parse HH:MM:SS from '{text}': {e}")

        return TimerReading(raw_text=text)

    def _parse_mmss(self, text: str) -> TimerReading:
        """Parse MM:SS format."""
        match = self.MMSS_PATTERN.search(text)
        if match:
            try:
                minutes = int(match.group(1))
                seconds = int(match.group(2))

                # Validate ranges
                if 0 <= minutes <= 99 and 0 <= seconds <= 59:
                    total = minutes * 60 + seconds
                    return TimerReading(
                        minutes=minutes,
                        seconds=seconds,
                        total_seconds=total,
                        raw_text=text,
                    )
                else:
                    logger.debug(f"Timer MM:SS out of range: {minutes}:{seconds}")
            except ValueError as e:
                logger.debug(f"Failed to parse MM:SS from '{text}': {e}")

        return TimerReading(raw_text=text)

    def _parse_seconds(self, text: str) -> TimerReading:
        """Parse seconds-only format."""
        match = self.SECONDS_PATTERN.search(text)
        if match:
            try:
                seconds = int(match.group(1))
                if 0 <= seconds <= 9999:
                    minutes, secs = divmod(seconds, 60)
                    hours, mins = divmod(minutes, 60)
                    return TimerReading(
                        hours=hours,
                        minutes=mins,
                        seconds=secs,
                        total_seconds=seconds,
                        raw_text=text,
                    )
                else:
                    logger.debug(f"Seconds value out of range: {seconds}")
            except ValueError as e:
                logger.debug(f"Failed to parse seconds from '{text}': {e}")

        return TimerReading(raw_text=text)

    def estimate_time_remaining(
        self,
        current: TimerReading,
        previous: TimerReading,
        elapsed_real_seconds: float,
    ) -> Optional[float]:
        """Estimate time remaining based on timer progression.

        Args:
            current: Current timer reading
            previous: Previous timer reading
            elapsed_real_seconds: Real time elapsed between readings

        Returns:
            Estimated seconds remaining, or None if can't determine
        """
        if not current.has_value or not previous.has_value:
            return None

        timer_diff = previous.total_seconds - current.total_seconds

        # Timer should be decreasing for countdown
        if timer_diff > 0 and current.is_countdown:
            # Timer is progressing normally
            rate = timer_diff / elapsed_real_seconds if elapsed_real_seconds > 0 else 1.0
            return current.total_seconds / rate if rate > 0 else None

        return current.total_seconds

    def get_last_valid(self) -> Optional[TimerReading]:
        """Get the last successfully parsed reading."""
        return self._last_valid_reading
