"""Earnings rate widget for real-time $/hour display."""

from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QComboBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

from ...tracking.earnings_rate import EarningsRateTracker, get_earnings_rate_tracker
from ...utils.logging import get_logger
from ...utils.helpers import format_money_short


logger = get_logger("ui.earnings_rate_widget")


class EarningsRateWidget(QWidget):
    """Widget displaying real-time earnings rate."""

    def __init__(
        self,
        tracker: Optional[EarningsRateTracker] = None,
        parent: Optional[QWidget] = None,
    ):
        """Initialize earnings rate widget.

        Args:
            tracker: Earnings rate tracker (uses global if None)
            parent: Parent widget
        """
        super().__init__(parent)
        self._tracker = tracker or get_earnings_rate_tracker()
        self._selected_window = "session"

        self._setup_ui()
        self._setup_update_timer()

    def _setup_ui(self) -> None:
        """Setup the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Header
        header_layout = QHBoxLayout()

        title = QLabel("Earnings Rate")
        title.setStyleSheet("color: #AAA; font-size: 11px; font-weight: bold;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Time window selector
        self._window_combo = QComboBox()
        self._window_combo.addItem("Session", "session")
        self._window_combo.addItem("Last Hour", "1hour")
        self._window_combo.addItem("Last 30min", "30min")
        self._window_combo.addItem("Last 15min", "15min")
        self._window_combo.addItem("Last 5min", "5min")
        self._window_combo.setStyleSheet("""
            QComboBox {
                background-color: rgba(40, 40, 60, 180);
                color: white;
                border: 1px solid rgba(255, 255, 255, 30);
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 10px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: rgba(40, 40, 60, 240);
                color: white;
                selection-background-color: rgba(76, 175, 80, 150);
            }
        """)
        self._window_combo.currentIndexChanged.connect(self._on_window_changed)
        header_layout.addWidget(self._window_combo)

        layout.addLayout(header_layout)

        # Main rate display
        rate_frame = QFrame()
        rate_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(40, 40, 60, 150);
                border-radius: 8px;
            }
        """)
        rate_layout = QVBoxLayout(rate_frame)
        rate_layout.setContentsMargins(12, 10, 12, 10)
        rate_layout.setSpacing(4)

        # Rate value
        self._rate_label = QLabel("$0/hr")
        self._rate_label.setFont(QFont("Arial", 24, QFont.Weight.Bold))
        self._rate_label.setStyleSheet("color: #4CAF50;")
        self._rate_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rate_layout.addWidget(self._rate_label)

        # Trend indicator
        self._trend_label = QLabel("")
        self._trend_label.setStyleSheet("color: #888; font-size: 11px;")
        self._trend_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rate_layout.addWidget(self._trend_label)

        layout.addWidget(rate_frame)

        # Stats row
        stats_layout = QHBoxLayout()

        # Session total
        total_frame = QFrame()
        total_frame.setStyleSheet("background: transparent;")
        total_layout = QVBoxLayout(total_frame)
        total_layout.setContentsMargins(0, 0, 0, 0)
        total_layout.setSpacing(2)

        total_label = QLabel("Session Total")
        total_label.setStyleSheet("color: #666; font-size: 9px;")
        total_layout.addWidget(total_label)

        self._total_value = QLabel("$0")
        self._total_value.setStyleSheet("color: #FFD700; font-size: 13px; font-weight: bold;")
        total_layout.addWidget(self._total_value)

        stats_layout.addWidget(total_frame)

        stats_layout.addStretch()

        # Avg per event
        avg_frame = QFrame()
        avg_frame.setStyleSheet("background: transparent;")
        avg_layout = QVBoxLayout(avg_frame)
        avg_layout.setContentsMargins(0, 0, 0, 0)
        avg_layout.setSpacing(2)

        avg_label = QLabel("Avg per Activity")
        avg_label.setStyleSheet("color: #666; font-size: 9px;")
        avg_layout.addWidget(avg_label)

        self._avg_value = QLabel("$0")
        self._avg_value.setStyleSheet("color: white; font-size: 13px; font-weight: bold;")
        avg_layout.addWidget(self._avg_value)

        stats_layout.addWidget(avg_frame)

        layout.addLayout(stats_layout)

    def _setup_update_timer(self) -> None:
        """Setup timer for updating display."""
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._update_display)
        self._update_timer.start(1000)  # Update every second

        # Initial update
        self._update_display()

    def _on_window_changed(self, index: int) -> None:
        """Handle window selection change."""
        self._selected_window = self._window_combo.currentData()
        self._update_display()

    def _update_display(self) -> None:
        """Update the display with current rates."""
        # Get rate for selected window
        rate = self._tracker.get_rate(self._selected_window)

        # Format rate
        if rate >= 1_000_000:
            rate_text = f"${rate / 1_000_000:.2f}M/hr"
        elif rate >= 1_000:
            rate_text = f"${rate / 1_000:.0f}K/hr"
        else:
            rate_text = f"${rate:,.0f}/hr"

        self._rate_label.setText(rate_text)

        # Update trend
        trend = self._tracker.get_trend()
        if trend == "up":
            self._trend_label.setText("Trending Up")
            self._trend_label.setStyleSheet("color: #4CAF50; font-size: 11px;")
        elif trend == "down":
            self._trend_label.setText("Trending Down")
            self._trend_label.setStyleSheet("color: #F44336; font-size: 11px;")
        else:
            self._trend_label.setText("Stable")
            self._trend_label.setStyleSheet("color: #888; font-size: 11px;")

        # Update session total
        total = self._tracker.session_earnings
        self._total_value.setText(f"{format_money_short(total)}")

        # Update average
        avg = self._tracker.get_average_earning()
        self._avg_value.setText(f"{format_money_short(avg)}")

    def record_earning(self, amount: int, source: str = "") -> None:
        """Record an earning to the tracker.

        Args:
            amount: Amount earned
            source: Source of earning
        """
        self._tracker.record_earning(amount, source)
        self._update_display()


class CompactEarningsRateWidget(QWidget):
    """Compact earnings rate display for overlay use."""

    def __init__(
        self,
        tracker: Optional[EarningsRateTracker] = None,
        parent: Optional[QWidget] = None,
    ):
        """Initialize compact widget.

        Args:
            tracker: Earnings rate tracker
            parent: Parent widget
        """
        super().__init__(parent)
        self._tracker = tracker or get_earnings_rate_tracker()

        self._setup_ui()
        self._setup_update_timer()

    def _setup_ui(self) -> None:
        """Setup the widget UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Rate label
        self._rate_label = QLabel("$0/hr")
        self._rate_label.setStyleSheet("""
            color: #4CAF50;
            font-size: 12px;
            font-weight: bold;
        """)
        layout.addWidget(self._rate_label)

        # Trend indicator
        self._trend_icon = QLabel("")
        self._trend_icon.setStyleSheet("font-size: 10px;")
        layout.addWidget(self._trend_icon)

    def _setup_update_timer(self) -> None:
        """Setup timer for updating display."""
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._update_display)
        self._update_timer.start(2000)  # Update every 2 seconds

        self._update_display()

    def _update_display(self) -> None:
        """Update the display."""
        rate = self._tracker.get_rate("session")

        # Format rate
        if rate >= 1_000_000:
            rate_text = f"${rate / 1_000_000:.1f}M/hr"
        elif rate >= 1_000:
            rate_text = f"${rate / 1_000:.0f}K/hr"
        else:
            rate_text = f"${rate:,.0f}/hr"

        self._rate_label.setText(rate_text)

        # Update trend icon
        trend = self._tracker.get_trend()
        if trend == "up":
            self._trend_icon.setText("^")
            self._trend_icon.setStyleSheet("color: #4CAF50; font-size: 10px;")
        elif trend == "down":
            self._trend_icon.setText("v")
            self._trend_icon.setStyleSheet("color: #F44336; font-size: 10px;")
        else:
            self._trend_icon.setText("-")
            self._trend_icon.setStyleSheet("color: #888; font-size: 10px;")
