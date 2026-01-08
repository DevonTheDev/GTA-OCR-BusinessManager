"""Passive income widget for displaying NC and Agency predictions."""

from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QProgressBar, QGridLayout
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

from ...tracking.passive_income import PassiveIncomeTracker, get_passive_income_tracker
from ...utils.logging import get_logger
from ...utils.helpers import format_money_short


logger = get_logger("ui.passive_income_widget")


class PassiveIncomeItemWidget(QFrame):
    """Widget displaying a single passive income source."""

    def __init__(
        self,
        name: str,
        parent: Optional[QWidget] = None,
    ):
        """Initialize passive income item widget.

        Args:
            name: Display name of the income source
            parent: Parent widget
        """
        super().__init__(parent)
        self._name = name
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the widget UI."""
        self.setStyleSheet("""
            QFrame {
                background-color: rgba(40, 40, 60, 150);
                border-radius: 6px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # Header row
        header_layout = QHBoxLayout()

        self._name_label = QLabel(self._name)
        self._name_label.setStyleSheet("color: white; font-size: 12px; font-weight: bold;")
        header_layout.addWidget(self._name_label)

        header_layout.addStretch()

        self._value_label = QLabel("$0")
        self._value_label.setStyleSheet("color: #4CAF50; font-size: 12px; font-weight: bold;")
        header_layout.addWidget(self._value_label)

        layout.addLayout(header_layout)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(8)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: rgba(255, 255, 255, 20);
                border: none;
                border-radius: 4px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #4CAF50, stop: 1 #8BC34A
                );
                border-radius: 4px;
            }
        """)
        layout.addWidget(self._progress_bar)

        # Info row
        info_layout = QHBoxLayout()

        self._percent_label = QLabel("0%")
        self._percent_label.setStyleSheet("color: #888; font-size: 10px;")
        info_layout.addWidget(self._percent_label)

        info_layout.addStretch()

        self._eta_label = QLabel("")
        self._eta_label.setStyleSheet("color: #FFD700; font-size: 10px;")
        info_layout.addWidget(self._eta_label)

        layout.addLayout(info_layout)

    def update_display(
        self,
        current_value: int,
        max_value: int,
        fill_percent: float,
        time_until_full: str,
        is_full: bool,
    ) -> None:
        """Update the display with current values.

        Args:
            current_value: Current estimated value
            max_value: Maximum capacity value
            fill_percent: Fill percentage (0-100)
            time_until_full: Formatted time until full
            is_full: Whether at capacity
        """
        self._value_label.setText(f"{format_money_short(current_value)}")
        self._progress_bar.setValue(int(fill_percent))
        self._percent_label.setText(f"{fill_percent:.0f}%")

        if is_full:
            self._eta_label.setText("FULL - Collect!")
            self._eta_label.setStyleSheet("color: #FF9800; font-size: 10px; font-weight: bold;")
            self._progress_bar.setStyleSheet("""
                QProgressBar {
                    background-color: rgba(255, 255, 255, 20);
                    border: none;
                    border-radius: 4px;
                }
                QProgressBar::chunk {
                    background-color: qlineargradient(
                        x1: 0, y1: 0, x2: 1, y2: 0,
                        stop: 0 #FF9800, stop: 1 #FFC107
                    );
                    border-radius: 4px;
                }
            """)
        elif fill_percent >= 80:
            self._eta_label.setText(f"Full in: {time_until_full}")
            self._eta_label.setStyleSheet("color: #FFD700; font-size: 10px;")
            self._progress_bar.setStyleSheet("""
                QProgressBar {
                    background-color: rgba(255, 255, 255, 20);
                    border: none;
                    border-radius: 4px;
                }
                QProgressBar::chunk {
                    background-color: qlineargradient(
                        x1: 0, y1: 0, x2: 1, y2: 0,
                        stop: 0 #FFD700, stop: 1 #FFC107
                    );
                    border-radius: 4px;
                }
            """)
        else:
            self._eta_label.setText(f"Full in: {time_until_full}")
            self._eta_label.setStyleSheet("color: #888; font-size: 10px;")
            self._progress_bar.setStyleSheet("""
                QProgressBar {
                    background-color: rgba(255, 255, 255, 20);
                    border: none;
                    border-radius: 4px;
                }
                QProgressBar::chunk {
                    background-color: qlineargradient(
                        x1: 0, y1: 0, x2: 1, y2: 0,
                        stop: 0 #4CAF50, stop: 1 #8BC34A
                    );
                    border-radius: 4px;
                }
            """)


class PassiveIncomeWidget(QWidget):
    """Widget displaying passive income predictions."""

    def __init__(
        self,
        tracker: Optional[PassiveIncomeTracker] = None,
        parent: Optional[QWidget] = None,
    ):
        """Initialize passive income widget.

        Args:
            tracker: Passive income tracker (uses global if None)
            parent: Parent widget
        """
        super().__init__(parent)
        self._tracker = tracker or get_passive_income_tracker()
        self._item_widgets: dict[str, PassiveIncomeItemWidget] = {}

        self._setup_ui()
        self._setup_update_timer()

    def _setup_ui(self) -> None:
        """Setup the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Header
        header = QLabel("Passive Income")
        header.setStyleSheet("color: #AAA; font-size: 11px; font-weight: bold;")
        layout.addWidget(header)

        # Total value
        total_layout = QHBoxLayout()
        total_label = QLabel("Estimated Total:")
        total_label.setStyleSheet("color: #888; font-size: 11px;")
        total_layout.addWidget(total_label)

        self._total_value = QLabel("$0")
        self._total_value.setStyleSheet("color: #4CAF50; font-size: 13px; font-weight: bold;")
        total_layout.addWidget(self._total_value)

        total_layout.addStretch()
        layout.addLayout(total_layout)

        # Nightclub widget
        self._nc_widget = PassiveIncomeItemWidget("Nightclub")
        self._item_widgets["nightclub"] = self._nc_widget
        layout.addWidget(self._nc_widget)

        # Agency widget
        self._agency_widget = PassiveIncomeItemWidget("Agency Safe")
        self._item_widgets["agency"] = self._agency_widget
        layout.addWidget(self._agency_widget)

        layout.addStretch()

    def _setup_update_timer(self) -> None:
        """Setup timer for updating display."""
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._update_display)
        self._update_timer.start(5000)  # Update every 5 seconds

        # Initial update
        self._update_display()

    def _update_display(self) -> None:
        """Update the display with current predictions."""
        predictions = self._tracker.get_predictions()

        # Update total
        total = self._tracker.total_passive_value
        self._total_value.setText(f"{format_money_short(total)}")

        # Update individual widgets
        for pred in predictions:
            name = pred["name"].lower().replace(" ", "_")
            if "nightclub" in name:
                self._nc_widget.update_display(
                    pred["current_value"],
                    pred["max_value"],
                    pred["fill_percent"],
                    pred["time_until_full"],
                    pred["is_full"],
                )
            elif "agency" in name:
                self._agency_widget.update_display(
                    pred["current_value"],
                    pred["max_value"],
                    pred["fill_percent"],
                    pred["time_until_full"],
                    pred["is_full"],
                )


class CompactPassiveIncomeWidget(QWidget):
    """Compact passive income display for overlay use."""

    def __init__(
        self,
        tracker: Optional[PassiveIncomeTracker] = None,
        parent: Optional[QWidget] = None,
    ):
        """Initialize compact widget.

        Args:
            tracker: Passive income tracker
            parent: Parent widget
        """
        super().__init__(parent)
        self._tracker = tracker or get_passive_income_tracker()

        self._setup_ui()
        self._setup_update_timer()

    def _setup_ui(self) -> None:
        """Setup the widget UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Nightclub
        self._nc_label = QLabel()
        self._nc_label.setStyleSheet("""
            color: #888;
            font-size: 10px;
            background-color: rgba(0, 0, 0, 50);
            padding: 2px 6px;
            border-radius: 3px;
        """)
        layout.addWidget(self._nc_label)

        # Agency
        self._agency_label = QLabel()
        self._agency_label.setStyleSheet("""
            color: #888;
            font-size: 10px;
            background-color: rgba(0, 0, 0, 50);
            padding: 2px 6px;
            border-radius: 3px;
        """)
        layout.addWidget(self._agency_label)

        layout.addStretch()

    def _setup_update_timer(self) -> None:
        """Setup timer for updating display."""
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._update_display)
        self._update_timer.start(10000)  # Update every 10 seconds

        # Initial update
        self._update_display()

    def _update_display(self) -> None:
        """Update the display."""
        nc = self._tracker.nightclub
        if nc:
            value = format_money_short(nc.estimated_current_value)
            pct = nc.fill_percent

            if nc.is_full:
                self._nc_label.setText(f"NC: {value} FULL")
                self._nc_label.setStyleSheet("""
                    color: #FF9800;
                    font-size: 10px;
                    font-weight: bold;
                    background-color: rgba(0, 0, 0, 50);
                    padding: 2px 6px;
                    border-radius: 3px;
                """)
            else:
                self._nc_label.setText(f"NC: {value} ({pct:.0f}%)")
                self._nc_label.setStyleSheet("""
                    color: #888;
                    font-size: 10px;
                    background-color: rgba(0, 0, 0, 50);
                    padding: 2px 6px;
                    border-radius: 3px;
                """)

        agency = self._tracker.agency
        if agency:
            value = format_money_short(agency.estimated_current_value)
            pct = agency.fill_percent

            if agency.is_full:
                self._agency_label.setText(f"Safe: {value} FULL")
                self._agency_label.setStyleSheet("""
                    color: #FF9800;
                    font-size: 10px;
                    font-weight: bold;
                    background-color: rgba(0, 0, 0, 50);
                    padding: 2px 6px;
                    border-radius: 3px;
                """)
            else:
                self._agency_label.setText(f"Safe: {value} ({pct:.0f}%)")
                self._agency_label.setStyleSheet("""
                    color: #888;
                    font-size: 10px;
                    background-color: rgba(0, 0, 0, 50);
                    padding: 2px 6px;
                    border-radius: 3px;
                """)
