"""Explicit, shared-character personal reminders using the app's tracker."""

from math import ceil
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QVBoxLayout, QWidget,
)

from ...tracking.cooldowns import (
    ACTIVITY_COOLDOWNS, CooldownInfo, CooldownTracker, validate_reminder_values,
)
from .cooldown_widget import CooldownWidget


_LOAD_WARNING = (
    "Saved timers couldn't be loaded. Starting or adjusting a timer will replace "
    "the unreadable saved file."
)
_SAVE_WARNING = (
    "Timer changes couldn't be saved. Reopening may restore older timers."
)
_DIALOG_STYLE = """
    QDialog { background-color: #1a1a2e; color: #EEE; }
    QLabel { color: #DDD; }
    QLineEdit, QComboBox, QSpinBox {
        background-color: #16213e; color: #EEE;
        border: 1px solid #455570; border-radius: 4px; padding: 5px;
    }
    QComboBox QAbstractItemView { background-color: #16213e; color: #EEE; }
    QPushButton {
        background-color: #0f3460; color: white;
        border: 1px solid #456082; border-radius: 4px; padding: 7px 12px;
    }
    QPushButton:hover { background-color: #204570; }
    QPushButton:focus { border: 1px solid #FFD700; }
    QPushButton:disabled { color: #888; }
    QScrollArea { background: transparent; border: none; }
"""


def _label(text: str, name: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def _fit_to_screen(dialog: QDialog, width: int, height: int) -> None:
    """Keep the initial window inside the available desktop work area."""
    screen = dialog.parentWidget().screen() if dialog.parentWidget() else QGuiApplication.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        width = min(width, max(1, available.width() - 32))
        height = min(height, max(1, available.height() - 48))
    dialog.resize(width, height)


class CooldownEditorDialog(QDialog):
    """Validate and commit a timer only when Set timer is accepted."""

    def __init__(self, tracker: CooldownTracker, parent: Optional[QWidget] = None,
                 cooldown: Optional[CooldownInfo] = None):
        super().__init__(parent)
        self._tracker = tracker
        # Capture the identity once: acceptance deliberately replaces that key,
        # even if capture changes it or the timer expires while this editor is open.
        self._activity_name = cooldown.activity_name if cooldown else None
        self._fixed_name = (cooldown.display_name if cooldown and
                            not cooldown.activity_name.startswith("manual:") else None)
        self.setObjectName("cooldown_editor")
        self.setWindowTitle("Adjust timer" if cooldown else "Start timer")
        self.setModal(True)
        self.setStyleSheet(_DIALOG_STYLE)
        self._setup_ui()
        if cooldown is not None:
            self._preset.hide()
            self._preset_label.hide()
            self._name.setText(cooldown.display_name)
            self._name.setReadOnly(self._fixed_name is not None)
            self._set_duration(max(1, min(604800, ceil(cooldown.remaining_seconds))))
            self._semantics.setText(
                "Set timer replaces this timer's countdown from now. "
                "It sets the captured timer again even if it expires or changes while this editor is open."
            )
        else:
            self._preset_changed()
        _fit_to_screen(self, 540, 420)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        # Keep controls reachable on smaller screens; only the form scrolls.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        form_layout = QVBoxLayout(content)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(12)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._preset = QComboBox()
        self._preset.setObjectName("cooldown_preset")
        self._preset.addItem("Custom timer", None)
        for key, seconds in ACTIVITY_COOLDOWNS.items():
            if seconds > 0:
                self._preset.addItem(key.replace("_", " ").title(), key)
        self._preset.currentIndexChanged.connect(self._preset_changed)
        self._preset_label = _label("Timer")
        self._preset_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        form.addRow(self._preset_label, self._preset)
        self._name = QLineEdit()
        self._name.setObjectName("cooldown_name")
        self._name.setPlaceholderText("Name your reminder (1–200 characters)")
        # QLineEdit's maxLength counts UTF-16 units; validate Unicode code points
        # on acceptance so 200 astral characters are also allowed.
        form.addRow("Name", self._name)
        form_layout.addLayout(form)
        form_layout.addWidget(_label("Time from accepting this editor (1 second to 7 days)"))
        duration_row = QHBoxLayout()
        for part, maximum in (("hours", 168), ("minutes", 59), ("seconds", 59)):
            column = QVBoxLayout()
            column.addWidget(_label(part.title()))
            spin = QSpinBox()
            spin.setObjectName("cooldown_" + part)
            spin.setAccessibleName(part.title())
            spin.setRange(0, maximum)
            setattr(self, "_" + part, spin)
            column.addWidget(spin)
            duration_row.addLayout(column, 1)
        form_layout.addLayout(duration_row)
        self._semantics = _label("", "cooldown_editor_semantics")
        form_layout.addWidget(self._semantics)
        policy = _label(
            "Personal reminders shared across this app's characters. "
            "Changing a duration affects this timer only. Timers don't change recommendations "
            "or confirm in-game availability."
        )
        policy.setStyleSheet("color: #AAA; font-size: 11px;")
        form_layout.addWidget(policy)
        warning = _label(_LOAD_WARNING, "cooldown_editor_storage_error")
        warning.setStyleSheet("color: #FFB74D;")
        warning.setVisible(self._tracker.storage_error == "load_failed")
        form_layout.addWidget(warning)
        form_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self._error = _label("", "cooldown_editor_error")
        self._error.setStyleSheet("color: #FFB74D;")
        self._error.hide()
        layout.addWidget(self._error)
        buttons = QDialogButtonBox()
        save = buttons.addButton("Set timer", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setObjectName("cooldown_editor_save")
        save.setDefault(True)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        cancel.setObjectName("cooldown_editor_cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_duration(self, seconds: int) -> None:
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self._hours.setValue(hours)
        self._minutes.setValue(minutes)
        self._seconds.setValue(seconds)

    def _preset_changed(self) -> None:
        key = self._preset.currentData()
        self._name.setReadOnly(key is not None)
        if key is None:
            self._name.clear()
            self._set_duration(300)
            self._semantics.setText("Set timer starts a new, separate reminder from now.")
        else:
            self._name.setText(key.replace("_", " ").title())
            self._set_duration(ACTIVITY_COOLDOWNS[key])
            self._semantics.setText(
                "Set timer replaces this activity's countdown from now. "
                "A later automatic completion may restart it. "
                "This is the app's saved default; check the duration against your game."
            )
        self._error.hide()

    def accept(self) -> None:
        for spin in (self._hours, self._minutes, self._seconds):
            spin.interpretText()
        seconds = self._hours.value() * 3600 + self._minutes.value() * 60 + self._seconds.value()
        name = self._fixed_name if self._fixed_name is not None else self._name.text()
        key = self._activity_name if self._activity_name is not None else self._preset.currentData()
        try:
            name, seconds = validate_reminder_values(name, seconds)
            if key is None:
                self._tracker.start_custom_timer(name, seconds)
            else:
                self._tracker.set_timer(key, name, seconds)
        except (TypeError, ValueError) as error:
            self._error.setText(str(error))
            self._error.show()
            return
        super().accept()


class CooldownManagerDialog(QDialog):
    """Modeless manager displaying and editing the supplied shared tracker."""

    def __init__(self, tracker: CooldownTracker, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._tracker = tracker
        self.setObjectName("cooldown_manager")
        self.setWindowTitle("Manage cooldowns")
        self.setModal(False)
        self.setStyleSheet(_DIALOG_STYLE)
        self._setup_ui()
        _fit_to_screen(self, 680, 560)
        self.refresh()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        heading = _label("Cooldowns & personal reminders")
        heading.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        layout.addWidget(heading)
        policy = _label(
            "Personal reminders shared across this app's characters. Timers continue while "
            "tracking is paused or stopped. Preset durations are the app's saved defaults; "
            "check the duration against your game. Timers don't confirm in-game availability "
            "or change recommendations.", "cooldown_policy",
        )
        policy.setStyleSheet("color: #BBB; font-size: 11px;")
        layout.addWidget(policy)
        start_row = QHBoxLayout()
        start = QPushButton("Start timer…")
        start.setObjectName("cooldown_start")
        start.clicked.connect(self._start_timer)
        start_row.addWidget(start)
        start_row.addStretch()
        layout.addLayout(start_row)
        self._cooldown_list = CooldownWidget(self._tracker, self, management=True)
        self._cooldown_list.setObjectName("cooldown_list")
        self._cooldown_list.adjust_requested.connect(self._adjust_timer)
        self._cooldown_list.remove_requested.connect(self._remove_timer)
        self._cooldown_list.refreshed.connect(self._refresh_storage_status)
        layout.addWidget(self._cooldown_list, 1)
        error_row = QHBoxLayout()
        self._storage_error = _label("", "cooldown_storage_error")
        self._storage_error.setStyleSheet("color: #FFB74D; font-size: 11px;")
        error_row.addWidget(self._storage_error, 1)
        self._retry = QPushButton("Retry save")
        self._retry.setObjectName("cooldown_retry")
        self._retry.clicked.connect(self._retry_save)
        error_row.addWidget(self._retry)
        layout.addLayout(error_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setObjectName("cooldown_close")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def refresh(self) -> None:
        self._cooldown_list.refresh()

    def _refresh_storage_status(self) -> None:
        error = self._tracker.storage_error
        self._storage_error.setText(_LOAD_WARNING if error == "load_failed" else _SAVE_WARNING if error else "")
        self._storage_error.setVisible(bool(error))
        self._retry.setVisible(self._tracker.needs_save_retry)

    def _edit_timer(self, cooldown: Optional[CooldownInfo] = None) -> None:
        dialog = CooldownEditorDialog(self._tracker, self, cooldown=cooldown)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
            self.refresh()

    def _start_timer(self) -> None:
        self._edit_timer()

    def _adjust_timer(self, activity_name: str) -> None:
        cooldown = self._tracker.get_cooldown(activity_name)
        if cooldown is not None:
            self._edit_timer(cooldown)
        else:
            self.refresh()

    def _remove_timer(self, activity_name: str) -> None:
        self._tracker.clear_cooldown(activity_name)
        self.refresh()

    def _retry_save(self) -> None:
        self._tracker.retry_save()
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._cooldown_list._update_timer.start(1000)
        self.refresh()

    def done(self, result: int) -> None:
        self._cooldown_list._update_timer.stop()
        super().done(result)
