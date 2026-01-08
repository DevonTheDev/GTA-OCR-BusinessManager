"""Performance monitoring utilities."""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class PerformanceMetrics:
    """Container for performance metrics."""

    avg_capture_ms: float = 0.0
    avg_ocr_ms: float = 0.0
    avg_detection_ms: float = 0.0
    avg_total_ms: float = 0.0
    captures_per_second: float = 0.0
    cpu_percent: float = 0.0
    memory_mb: float = 0.0


@dataclass
class TimingWindow:
    """Rolling window for timing measurements."""

    samples: deque = field(default_factory=lambda: deque(maxlen=100))

    def add(self, value: float) -> None:
        """Add a timing sample."""
        self.samples.append(value)

    def average(self) -> float:
        """Get average of samples."""
        if not self.samples:
            return 0.0
        return sum(self.samples) / len(self.samples)

    def clear(self) -> None:
        """Clear all samples."""
        self.samples.clear()


class PerformanceMonitor:
    """Monitors and reports application performance metrics."""

    def __init__(self, window_size: int = 100):
        """Initialize performance monitor.

        Args:
            window_size: Number of samples to keep for rolling averages
        """
        self._window_size = window_size
        self._capture_times = TimingWindow()
        self._capture_times.samples = deque(maxlen=window_size)
        self._ocr_times = TimingWindow()
        self._ocr_times.samples = deque(maxlen=window_size)
        self._detection_times = TimingWindow()
        self._detection_times.samples = deque(maxlen=window_size)
        self._total_times = TimingWindow()
        self._total_times.samples = deque(maxlen=window_size)

        self._capture_count = 0
        self._last_report_time = time.time()
        self._capture_count_at_last_report = 0

    def record_capture(self, duration_ms: float) -> None:
        """Record a capture operation duration."""
        self._capture_times.add(duration_ms)
        self._capture_count += 1

    def record_ocr(self, duration_ms: float) -> None:
        """Record an OCR operation duration."""
        self._ocr_times.add(duration_ms)

    def record_detection(self, duration_ms: float) -> None:
        """Record a detection operation duration."""
        self._detection_times.add(duration_ms)

    def record_total(self, duration_ms: float) -> None:
        """Record total frame processing time."""
        self._total_times.add(duration_ms)

    def time_operation(self, category: str) -> "TimingContext":
        """Context manager for timing operations.

        Args:
            category: One of "capture", "ocr", "detection", "total"

        Returns:
            Timing context manager
        """
        record_func = {
            "capture": self.record_capture,
            "ocr": self.record_ocr,
            "detection": self.record_detection,
            "total": self.record_total,
        }.get(category)

        return TimingContext(record_func)

    def get_metrics(self) -> PerformanceMetrics:
        """Get current performance metrics."""
        now = time.time()
        elapsed = now - self._last_report_time
        captures_since_report = self._capture_count - self._capture_count_at_last_report

        fps = captures_since_report / elapsed if elapsed > 0 else 0.0

        # Get system metrics (optional, may fail on some systems)
        cpu_percent = 0.0
        memory_mb = 0.0
        try:
            import psutil

            process = psutil.Process()
            cpu_percent = process.cpu_percent()
            memory_mb = process.memory_info().rss / (1024 * 1024)
        except ImportError:
            pass  # psutil not installed

        return PerformanceMetrics(
            avg_capture_ms=self._capture_times.average(),
            avg_ocr_ms=self._ocr_times.average(),
            avg_detection_ms=self._detection_times.average(),
            avg_total_ms=self._total_times.average(),
            captures_per_second=fps,
            cpu_percent=cpu_percent,
            memory_mb=memory_mb,
        )

    def reset(self) -> None:
        """Reset all metrics."""
        self._capture_times.clear()
        self._ocr_times.clear()
        self._detection_times.clear()
        self._total_times.clear()
        self._capture_count = 0
        self._last_report_time = time.time()
        self._capture_count_at_last_report = 0

    def mark_report(self) -> None:
        """Mark the current time for FPS calculation."""
        self._last_report_time = time.time()
        self._capture_count_at_last_report = self._capture_count


class TimingContext:
    """Context manager for timing operations."""

    def __init__(self, record_func: Callable[[float], None] | None):
        self._record_func = record_func
        self._start_time: float = 0

    def __enter__(self) -> "TimingContext":
        self._start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._record_func:
            elapsed_ms = (time.perf_counter() - self._start_time) * 1000
            self._record_func(elapsed_ms)
