"""Cooldown displays shared by the reminder manager and the overlay."""

from math import ceil
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QProgressBar, QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import QPointF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPainter, QTextLayout, QTextOption

from ...tracking.cooldowns import CooldownTracker, CooldownInfo, get_cooldown_tracker


def _remaining_text(cooldown: CooldownInfo) -> str:
    """Show a countdown, including the final fraction of a second."""
    remaining = max(0, ceil(cooldown.remaining_seconds))
    minutes, seconds = divmod(remaining, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


class _WrappingNameLabel(QLabel):
    """Plain text that also wraps a single long word instead of clipping it."""

    def _layout_text(self, width: int):
        text_layout = QTextLayout(self.text(), self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        text_layout.setTextOption(option)
        text_layout.beginLayout()
        height = 0.0
        while True:
            line = text_layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1, width))
            line.setPosition(QPointF(0, height))
            height += line.height()
        text_layout.endLayout()
        return text_layout, ceil(height)

    def heightForWidth(self, width: int) -> int:
        return self._layout_text(width)[1]

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        text_layout, _ = self._layout_text(self.contentsRect().width())
        text_layout.draw(painter, QPointF(self.contentsRect().topLeft()))


class CooldownItemWidget(QFrame):
    """Display a detached timer record, with optional stable-key actions."""

    adjust_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)

    def __init__(self, cooldown: CooldownInfo, parent: Optional[QWidget] = None,
                 management: bool = False):
        super().__init__(parent)
        self._cooldown = cooldown
        self._management = management
        self._near_completion = None
        self.setObjectName("cooldown_item")
        self.setProperty("activity_name", cooldown.activity_name)
        self._setup_ui()
        self.update_display()

    def _setup_ui(self) -> None:
        self.setStyleSheet("""
            QFrame#cooldown_item {
                background-color: rgba(40, 40, 60, 180);
                border-radius: 6px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        header = QHBoxLayout()
        self._name_label = _WrappingNameLabel(self._cooldown.display_name)
        self._name_label.setObjectName("cooldown_name_label")
        self._name_label.setTextFormat(Qt.TextFormat.PlainText)
        self._name_label.setWordWrap(True)
        self._name_label.setMinimumWidth(0)
        self._name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._name_label.setStyleSheet("color: white; font-size: 11px; font-weight: bold;")
        header.addWidget(self._name_label, 1)
        self._time_label = QLabel()
        self._time_label.setObjectName("cooldown_remaining")
        self._time_label.setTextFormat(Qt.TextFormat.PlainText)
        self._time_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        header.addWidget(self._time_label)
        layout.addLayout(header)
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(8)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setRange(0, 1000)
        layout.addWidget(self._progress_bar)
        if self._management:
            actions = QHBoxLayout()
            actions.addStretch()
            adjust = QPushButton("Adjust…")
            adjust.setObjectName("cooldown_adjust")
            adjust.clicked.connect(lambda: self.adjust_requested.emit(self.activity_name))
            actions.addWidget(adjust)
            remove = QPushButton("Remove")
            remove.setObjectName("cooldown_remove")
            remove.clicked.connect(lambda: self.remove_requested.emit(self.activity_name))
            actions.addWidget(remove)
            layout.addLayout(actions)

    def rebind(self, cooldown: CooldownInfo) -> None:
        """Keep the card while refreshing its detached, possibly replaced record."""
        self._cooldown = cooldown
        self.setProperty("activity_name", cooldown.activity_name)
        self._name_label.setText(cooldown.display_name)
        self.update_display()

    def update_display(self) -> bool:
        self._time_label.setText(_remaining_text(self._cooldown))
        progress = self._cooldown.progress
        self._progress_bar.setValue(int(progress * 1000))
        near_completion = progress >= 0.9
        if near_completion != self._near_completion:
            self._near_completion = near_completion
            start, end = ("#FFD700", "#FFC107") if near_completion else ("#4CAF50", "#8BC34A")
            self._progress_bar.setStyleSheet(f"""
                QProgressBar {{
                    background-color: rgba(255, 255, 255, 30);
                    border: none;
                    border-radius: 4px;
                }}
                QProgressBar::chunk {{
                    background-color: qlineargradient(
                        x1: 0, y1: 0, x2: 1, y2: 0,
                        stop: 0 {start}, stop: 1 {end}
                    );
                    border-radius: 4px;
                }}
            """)
            self._time_label.setStyleSheet(
                "color: #4CAF50; font-size: 11px; font-weight: bold;" if near_completion
                else "color: #FFD700; font-size: 11px;"
            )
        return not self._cooldown.is_expired

    @property
    def activity_name(self) -> str:
        return self._cooldown.activity_name


class CooldownWidget(QWidget):
    """Full, scrollable countdown list with opt-in management signals."""

    adjust_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)
    refreshed = pyqtSignal()

    def __init__(self, tracker: Optional[CooldownTracker] = None,
                 parent: Optional[QWidget] = None, compact: bool = False,
                 management: bool = False):
        super().__init__(parent)
        self._tracker = tracker if tracker is not None else get_cooldown_tracker()
        self._compact = compact
        self._management = management
        self._item_widgets: dict[str, CooldownItemWidget] = {}
        self._setup_ui()
        self._setup_update_timer()
        self._refresh_cooldowns()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        if not self._compact:
            header = QLabel("Active timers" if self._management else "Active Cooldowns")
            header.setStyleSheet("color: #AAA; font-size: 11px; font-weight: bold;")
            layout.addWidget(header)
        scroll = QScrollArea()
        scroll.setObjectName("cooldown_scroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: rgba(255, 255, 255, 10);
                width: 8px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 40);
                border-radius: 3px;
                min-height: 20px;
            }
        """)
        self._container = QWidget()
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(4)
        self._container_layout.addStretch()
        scroll.setWidget(self._container)
        self._container.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)
        layout.addWidget(scroll, 1)
        self._empty_label = QLabel("No active timers. Start a timer to add a personal reminder."
                                   if self._management else "No active cooldowns")
        self._empty_label.setObjectName("cooldown_empty")
        self._empty_label.setTextFormat(Qt.TextFormat.PlainText)
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet("color: #AAA; font-size: 11px;")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty_label)

    def _setup_update_timer(self) -> None:
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._update_cooldowns)
        self._update_timer.start(1000)

    def _refresh_cooldowns(self) -> None:
        active = self._tracker.get_active_cooldowns()
        active_names = {cooldown.activity_name for cooldown in active}
        for name in list(self._item_widgets):
            if name not in active_names:
                widget = self._item_widgets.pop(name)
                self._container_layout.removeWidget(widget)
                widget.hide()
                widget.deleteLater()
        for index, cooldown in enumerate(active):
            widget = self._item_widgets.get(cooldown.activity_name)
            if widget is None:
                widget = CooldownItemWidget(cooldown, self._container, management=self._management)
                widget.adjust_requested.connect(self.adjust_requested.emit)
                widget.remove_requested.connect(self.remove_requested.emit)
                self._item_widgets[cooldown.activity_name] = widget
            else:
                widget.rebind(cooldown)
            # Moving an existing widget also updates ordering after replacement.
            self._container_layout.insertWidget(index, widget)
        self._empty_label.setVisible(not active)
        self.refreshed.emit()

    def refresh(self) -> None:
        """Refresh records and order from the shared tracker."""
        self._refresh_cooldowns()

    def _update_cooldowns(self) -> None:
        # Fetch replacements before deciding whether an old card has expired.
        self._refresh_cooldowns()

    def add_cooldown(self, activity_name: str, display_name: Optional[str] = None,
                     duration_seconds: Optional[int] = None) -> None:
        """Keep the existing standalone programmatic API."""
        self._tracker.start_cooldown(activity_name, display_name, duration_seconds)
        self._refresh_cooldowns()

    def clear_cooldown(self, activity_name: str) -> None:
        self._tracker.clear_cooldown(activity_name)
        self._refresh_cooldowns()

    def clear_all(self) -> None:
        """Legacy programmatic API; the manager exposes no bulk-clear control."""
        for name in list(self._item_widgets):
            self._tracker.clear_cooldown(name)
        self._refresh_cooldowns()


class CompactCooldownWidget(QWidget):
    """Compact cooldown display for overlay use."""

    def __init__(self, tracker: Optional[CooldownTracker] = None,
                 parent: Optional[QWidget] = None, max_display: int = 3):
        super().__init__(parent)
        self._tracker = tracker if tracker is not None else get_cooldown_tracker()
        self._max_display = max_display
        self._labels: list[QLabel] = []
        self._setup_ui()
        self._setup_update_timer()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for _ in range(self._max_display):
            label = QLabel()
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.hide()
            self._labels.append(label)
            layout.addWidget(label)
        layout.addStretch()

    def _setup_update_timer(self) -> None:
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._update_display)
        self._update_timer.start(1000)

    def _update_display(self) -> None:
        cooldowns = self._tracker.get_active_cooldowns()[:self._max_display]
        for i, label in enumerate(self._labels):
            if i < len(cooldowns):
                cooldown = cooldowns[i]
                label.setText(f"{cooldown.display_name[:10]}: {_remaining_text(cooldown)}")
                color = "#4CAF50" if cooldown.progress >= 0.9 else "#FFD700"
                label.setStyleSheet(f"""
                    color: {color};
                    font-size: 10px;
                    background-color: rgba(0, 0, 0, 50);
                    padding: 2px 6px;
                    border-radius: 3px;
                """)
                label.show()
            else:
                label.hide()
        self.setVisible(bool(cooldowns))
