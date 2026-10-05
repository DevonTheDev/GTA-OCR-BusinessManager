"""Activity history panel."""

from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QFrame,
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QPushButton,
)
from PyQt6.QtCore import QTimer, Qt

from ...constants import UI
from ...utils.helpers import format_money, format_money_short, format_time

if TYPE_CHECKING:
    from ...app import DetectionRecoverySnapshot, GTABusinessManager


def _recovery_label(text: str, name: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


class DetectionRecoveryDialog(QDialog):
    """Review one frozen detection; accepting never fetches a replacement target."""

    def __init__(self, snapshot: "DetectionRecoverySnapshot", parent: QWidget):
        super().__init__(parent)
        self.setObjectName("detection_recovery_confirmation")
        self.setWindowTitle("Discard detected activity?")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(_recovery_label("Discard this unfinished activity detection?"))
        target = QPlainTextEdit()
        target.setObjectName("recovery_target")
        target.setReadOnly(True)
        target.setPlainText(
            f"{snapshot.name or 'Unnamed activity'}\n"
            f"Type: {snapshot.activity_type.name.replace('_', ' ').title()}\n"
            f"Started: {snapshot.started_at.isoformat(sep=' ', timespec='seconds')}"
        )
        layout.addWidget(target, 1)
        layout.addWidget(_recovery_label(
            "Its unfinished estimate will be dropped. No result will be recorded. "
            "Completed history and session earnings will remain. Capture stays paused; "
            "after resuming, tracking needs a readable activity and balance together.",
            "recovery_explanation",
        ))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        discard = buttons.addButton("Discard detection", QDialogButtonBox.ButtonRole.AcceptRole)
        discard.setObjectName("recovery_accept")
        discard.setAutoDefault(False)
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setObjectName("recovery_cancel")
        cancel.setDefault(True)
        cancel.setFocus()
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        available = parent.screen().availableGeometry()
        self.resize(min(540, available.width() - 32), min(380, available.height() - 48))


class ActivityPanel(QWidget):
    """Panel showing activity history."""

    def __init__(self, app: Optional["GTABusinessManager"] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._app = app
        self._cooldown_dialog = None
        self._recovery_dialog = None
        self._setup_ui()
        self._setup_update_timer()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header
        header_row = QHBoxLayout()
        header = QLabel("Activity History")
        header.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        header_row.addWidget(header)
        header_row.addStretch()
        self._manage_cooldowns_button = QPushButton("Manage cooldowns…")
        self._manage_cooldowns_button.setObjectName("manage_cooldowns")
        self._manage_cooldowns_button.setEnabled(self._app is not None)
        self._manage_cooldowns_button.clicked.connect(self._open_cooldown_manager)
        header_row.addWidget(self._manage_cooldowns_button)
        layout.addLayout(header_row)

        # The current unfinished detection is separate from completed history.
        detection = QFrame()
        detection.setObjectName("current_detection")
        detection.setStyleSheet("QFrame#current_detection { background: #16213e; border-radius: 8px; }")
        detection_layout = QVBoxLayout(detection)
        detection_layout.setContentsMargins(12, 12, 12, 12)
        detection_layout.setSpacing(6)
        detection_layout.addWidget(_recovery_label("Current detection"))
        # A read-only single line stays compact and lets long OCR names scroll
        # and be selected without expanding the main window or interpreting HTML.
        self._detection_name = QLineEdit()
        self._detection_name.setObjectName("detection_name")
        self._detection_name.setReadOnly(True)
        self._detection_name.setAccessibleName("Current detected activity")
        self._detection_name.setToolTip("Select text or use the arrow keys to read a long activity name.")
        self._detection_name.setPlaceholderText("No unfinished detected activity")
        detection_layout.addWidget(self._detection_name)
        self._detection_details = _recovery_label("")
        detection_layout.addWidget(self._detection_details)
        self._detection_status = _recovery_label("Capture is stopped.", "detection_status")
        detection_layout.addWidget(self._detection_status)
        actions = QHBoxLayout()
        self._pause_resume_button = QPushButton("Pause capture")
        self._pause_resume_button.setObjectName("detection_pause_resume")
        self._pause_resume_button.clicked.connect(self._pause_resume_capture)
        self._pause_resume_button.setEnabled(False)
        actions.addWidget(self._pause_resume_button)
        self._discard_button = QPushButton("Discard detected activity…")
        self._discard_button.setObjectName("discard_detection")
        self._discard_button.clicked.connect(self._open_detection_recovery)
        self._discard_button.setEnabled(False)
        actions.addWidget(self._discard_button)
        actions.addStretch()
        detection_layout.addLayout(actions)
        self._recovery_feedback = _recovery_label("", "detection_feedback")
        self._recovery_feedback.hide()
        detection_layout.addWidget(self._recovery_feedback)
        layout.addWidget(detection)

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
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_display)
        self._timer.start(UI.BUSINESS_UPDATE_INTERVAL_MS)

    def _pause_resume_capture(self) -> None:
        if self._app is None:
            return
        from ...app import AppState

        if self._app.state == AppState.RUNNING:
            self._app.pause()
        elif self._app.state == AppState.PAUSED:
            self._app.resume()
        self._show_recovery_feedback("")
        self._update_display()

    def _show_recovery_feedback(self, message: str) -> None:
        self._recovery_feedback.setText(message)
        self._recovery_feedback.setVisible(bool(message))

    def _open_detection_recovery(self) -> None:
        if self._app is None:
            return
        if self._recovery_dialog is not None:
            self._recovery_dialog.show()
            self._recovery_dialog.raise_()
            self._recovery_dialog.activateWindow()
            return
        status = self._app.get_detection_recovery_status()
        snapshot = status.snapshot
        if snapshot is None:
            self._show_recovery_feedback(status.message)
            self._update_display()
            return
        self._show_recovery_feedback("")
        # No app lock is held while Qt processes confirmation/tray events.
        dialog = DetectionRecoveryDialog(snapshot, self)
        self._recovery_dialog = dialog
        dialog.finished.connect(
            lambda result, closed=dialog, target=snapshot:
            self._recovery_finished(closed, target, result)
        )
        self._discard_button.setEnabled(False)
        dialog.show()

    def _recovery_finished(self, dialog, snapshot, result: int) -> None:
        if self._recovery_dialog is not dialog:
            return
        self._recovery_dialog = None
        if result == QDialog.DialogCode.Accepted:
            success, message = self._app.discard_detected_activity(snapshot)
            self._show_recovery_feedback(
                "Unfinished detection discarded. Completed history and session earnings were kept."
                if success else message
            )
        dialog.deleteLater()
        self._update_display()

    def _open_cooldown_manager(self) -> None:
        """Keep one live, modeless manager without starting capture."""
        if self._app is None:
            return
        if self._cooldown_dialog is not None:
            self._cooldown_dialog.show()
            self._cooldown_dialog.raise_()
            self._cooldown_dialog.activateWindow()
            return
        from .cooldown_manager_dialog import CooldownManagerDialog

        dialog = CooldownManagerDialog(self._app.cooldown_tracker, self)
        self._cooldown_dialog = dialog
        dialog.finished.connect(lambda result, closed=dialog: self._cooldown_finished(closed))
        dialog.show()

    def _cooldown_finished(self, dialog) -> None:
        if self._cooldown_dialog is not dialog:
            return
        self._cooldown_dialog = None
        dialog.deleteLater()

    def _update_display(self) -> None:
        """Update activity table."""
        if not self._app:
            return

        from ...app import AppState

        status = self._app.get_detection_recovery_status()
        if self._detection_name.text() != status.name:
            self._detection_name.setText(status.name)
            self._detection_name.setCursorPosition(0)
        details = ""
        if status.activity_type is not None and status.started_at is not None:
            details = (f"{status.activity_type.name.replace('_', ' ').title()} · "
                       f"Started {status.started_at.isoformat(sep=' ', timespec='seconds')}")
        self._detection_details.setText(details)
        self._detection_details.setVisible(bool(details))
        self._detection_status.setText(status.message)
        self._pause_resume_button.setText(
            "Resume capture" if status.app_state == AppState.PAUSED else "Pause capture"
        )
        self._pause_resume_button.setEnabled(status.app_state in (AppState.RUNNING, AppState.PAUSED))
        self._discard_button.setEnabled(status.snapshot is not None and self._recovery_dialog is None)

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
