"""Activity history panel."""

from typing import TYPE_CHECKING, List, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)
from PyQt6.QtCore import QTimer, Qt

from ...constants import UI
from ...game.activities import Activity
from ...utils.helpers import format_money, format_money_short, format_time

if TYPE_CHECKING:
    from ...app import GTABusinessManager


class ActivityPanel(QWidget):
    """Panel showing activity history."""

    def __init__(self, app: Optional["GTABusinessManager"] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._app = app
        self._setup_ui()
        self._setup_update_timer()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header
        header = QLabel("Activity History")
        header.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        layout.addWidget(header)

        # Summary stats
        stats_frame = QFrame()
        stats_frame.setStyleSheet("""
            QFrame {
                background-color: #16213e;
                border-radius: 8px;
                padding: 16px;
            }
        """)
        stats_layout = QHBoxLayout(stats_frame)

        self._total_label = QLabel("Total: 0")
        self._total_label.setStyleSheet("color: white; font-size: 14px;")
        stats_layout.addWidget(self._total_label)

        self._success_label = QLabel("Success: 0")
        self._success_label.setStyleSheet("color: #4CAF50; font-size: 14px;")
        stats_layout.addWidget(self._success_label)

        self._failed_label = QLabel("Failed: 0")
        self._failed_label.setStyleSheet("color: #F44336; font-size: 14px;")
        stats_layout.addWidget(self._failed_label)

        self._earnings_label = QLabel("Earnings: $0")
        self._earnings_label.setStyleSheet("color: #FFD700; font-size: 14px;")
        stats_layout.addWidget(self._earnings_label)

        stats_layout.addStretch()
        layout.addWidget(stats_frame)

        # Activity table
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["Type", "Name", "Duration", "Earnings", "Status"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setStyleSheet("""
            QTableWidget {
                background-color: #16213e;
                border: none;
                border-radius: 8px;
            }
            QTableWidget::item {
                padding: 8px;
            }
            QHeaderView::section {
                background-color: #0f3460;
                color: white;
                padding: 8px;
                border: none;
            }
        """)

        layout.addWidget(self._table)

    def _setup_update_timer(self) -> None:
        """Setup update timer."""
        self._timer = QTimer()
        self._timer.timeout.connect(self._update_display)
        self._timer.start(UI.BUSINESS_UPDATE_INTERVAL_MS)

    def _update_display(self) -> None:
        """Update activity table."""
        if not self._app:
            return

        activities = self._app.recent_activities

        # Update summary stats
        total = len(activities)
        successful = [a for a in activities if a.success]
        failed = [a for a in activities if a.success is False]
        total_earnings = sum(a.earnings for a in successful)

        self._total_label.setText(f"Total: {total}")
        self._success_label.setText(f"Success: {len(successful)}")
        self._failed_label.setText(f"Failed: {len(failed)}")
        self._earnings_label.setText(f"Earnings: {format_money(total_earnings)}")

        # Update table
        self._table.setRowCount(len(activities))

        for row, activity in enumerate(activities):
            # Type
            type_item = QTableWidgetItem(activity.activity_type.name.replace("_", " ").title())
            type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, type_item)

            # Name
            name_item = QTableWidgetItem(activity.name or "--")
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 1, name_item)

            # Duration
            duration_item = QTableWidgetItem(format_time(activity.duration_seconds))
            duration_item.setFlags(duration_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 2, duration_item)

            # Earnings
            earnings_text = format_money_short(activity.earnings) if activity.earnings > 0 else "--"
            earnings_item = QTableWidgetItem(earnings_text)
            earnings_item.setFlags(earnings_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if activity.earnings > 0:
                earnings_item.setForeground(Qt.GlobalColor.green)
            self._table.setItem(row, 3, earnings_item)

            # Status
            if activity.success is True:
                status_text = "Passed"
                status_color = Qt.GlobalColor.green
            elif activity.success is False:
                status_text = "Failed"
                status_color = Qt.GlobalColor.red
            else:
                status_text = "In Progress"
                status_color = Qt.GlobalColor.yellow

            status_item = QTableWidgetItem(status_text)
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            status_item.setForeground(status_color)
            self._table.setItem(row, 4, status_item)
