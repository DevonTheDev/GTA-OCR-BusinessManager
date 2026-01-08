"""Common helper functions for GTA Business Manager."""

import os
from pathlib import Path


def format_money(amount: int | float) -> str:
    """Format a money amount with GTA-style formatting.

    Args:
        amount: Money amount

    Returns:
        Formatted string (e.g., "$1,234,567")
    """
    return f"${amount:,.0f}"


def format_money_short(amount: int | float) -> str:
    """Format money in short form (K/M/B).

    Args:
        amount: Money amount

    Returns:
        Short formatted string (e.g., "$1.23M")
    """
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.2f}B"
    elif amount >= 1_000_000:
        return f"${amount / 1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"${amount / 1_000:.1f}K"
    else:
        return f"${amount:.0f}"


def format_time(seconds: int | float) -> str:
    """Format time duration in human readable format.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string (e.g., "1h 23m 45s")
    """
    seconds = int(seconds)

    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def format_time_short(seconds: int | float) -> str:
    """Format time in MM:SS or HH:MM:SS format.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string (e.g., "01:23:45")
    """
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes:02d}:{secs:02d}"


def format_percentage(value: float, decimals: int = 1) -> str:
    """Format a percentage value.

    Args:
        value: Value between 0 and 1 (or 0-100)
        decimals: Number of decimal places

    Returns:
        Formatted string (e.g., "85.5%")
    """
    # Assume values > 1 are already percentages
    if value > 1:
        return f"{value:.{decimals}f}%"
    return f"{value * 100:.{decimals}f}%"


def get_data_dir() -> Path:
    """Get the application data directory.

    Returns:
        Path to data directory
    """
    if os.name == "nt":
        app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        app_data = Path.home() / ".config"

    data_dir = app_data / "GTABusinessManager"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_assets_dir() -> Path:
    """Get the assets directory (templates, sounds, etc.).

    Returns:
        Path to assets directory
    """
    # Assets are relative to the package
    return Path(__file__).parent.parent.parent / "assets"


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value between min and max.

    Args:
        value: Value to clamp
        min_val: Minimum value
        max_val: Maximum value

    Returns:
        Clamped value
    """
    return max(min_val, min(max_val, value))


def parse_resolution(resolution_str: str) -> tuple[int, int] | None:
    """Parse a resolution string like '1920x1080'.

    Args:
        resolution_str: Resolution string

    Returns:
        Tuple of (width, height) or None if invalid
    """
    try:
        parts = resolution_str.lower().split("x")
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except (ValueError, AttributeError):
        pass
    return None
