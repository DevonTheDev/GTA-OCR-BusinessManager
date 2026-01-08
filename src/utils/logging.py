"""Logging configuration for GTA Business Manager."""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def setup_logging(
    log_dir: Path | None = None,
    level: int = logging.INFO,
    console: bool = True,
    file: bool = True,
) -> logging.Logger:
    """Set up application logging.

    Args:
        log_dir: Directory for log files. If None, uses default data dir.
        level: Logging level (default: INFO)
        console: Whether to log to console
        file: Whether to log to file

    Returns:
        Root logger instance
    """
    # Create root logger for the application
    logger = logging.getLogger("gta_manager")
    logger.setLevel(level)

    # Clear any existing handlers
    logger.handlers.clear()

    # Log format
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # File handler
    if file:
        if log_dir is None:
            # Import here to avoid circular imports
            from ..config.settings import get_settings

            log_dir = get_settings().data_dir / "logs"

        log_dir.mkdir(parents=True, exist_ok=True)

        # Create log file with date
        log_file = log_dir / f"gta_manager_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a specific module.

    Args:
        name: Module name (e.g., "capture", "detection.ocr")

    Returns:
        Logger instance
    """
    return logging.getLogger(f"gta_manager.{name}")


class PerformanceLogger:
    """Context manager for logging performance of operations."""

    def __init__(self, logger: logging.Logger, operation: str, level: int = logging.DEBUG):
        """Initialize performance logger.

        Args:
            logger: Logger instance to use
            operation: Name of the operation being timed
            level: Log level for the timing message
        """
        self.logger = logger
        self.operation = operation
        self.level = level
        self.start_time: Optional[float] = None

    def __enter__(self) -> "PerformanceLogger":
        import time

        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        import time

        if self.start_time is not None:
            elapsed_ms = (time.perf_counter() - self.start_time) * 1000
            self.logger.log(self.level, f"{self.operation} completed in {elapsed_ms:.2f}ms")
