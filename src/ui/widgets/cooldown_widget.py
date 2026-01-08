"""Cooldown timer widget for displaying active cooldowns."""

from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QProgressBar, QScrollArea, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

from ...tracking.cooldowns import CooldownTracker, CooldownInfo, get_cooldown_tracker
from ...utils.logging import get_logger


logger = get_logger("ui.cooldown_widget")


class CooldownItemWidget(QFrame):
    """Widget displaying a single cooldown timer."""

    def __init__(self, cooldown: CooldownInfo, parent: Optional[QWidget] = None):
        """Initialize cooldown item widget.

        Args:
            cooldown: Cooldown info to display
            parent: Parent widget
        """
        super().__init__(parent)
        self._cooldown = cooldown
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the widget UI."""
        self.setStyleSheet("""
            QFrame {
                background-color: rgba(40, 40, 60, 180);
                border-radius: 6px;
                padding: 4px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # Header row with name and time
        header_layout = QHBoxLayout()

        self._name_label = QLabel(self._cooldown.display_name)
        self._name_label.setStyleSheet("color: white; font-size: 11px; font-weight: bold;")
        header_layout.addWidget(self._name_label)

        header_layout.addStretch()

        self._time_label = QLabel(self._cooldown.remaining_formatted)
        self._time_label.setStyleSheet("color: #FFD700; font-size: 11px;")
        header_layout.addWidget(self._time_label)

        layout.addLayout(header_layout)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(8)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setRange(0, 1000)
        self._progress_bar.setValue(int(self._cooldown.progress * 1000))
        self._progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: rgba(255, 255, 255, 30);
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

    def update_display(self) -> bool:
        """Update the display with current cooldown state.

        Returns:
            True if cooldown is still active, False if expired
        """
        if self._cooldown.is_expired:
            return False

        self._time_label.setText(self._cooldown.remaining_formatted)
        self._progress_bar.setValue(int(self._cooldown.progress * 1000))

        # Change color as cooldown nears completion
        progress = self._cooldown.progress
        if progress >= 0.9:
            self._progress_bar.setStyleSheet("""
                QProgressBar {
                    background-color: rgba(255, 255, 255, 30);
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
            self._time_label.setStyleSheet("color: #4CAF50; font-size: 11px; font-weight: bold;")

        return True

    @property
    def activity_name(self) -> str:
        """Get the activity name for this cooldown."""
        return self._cooldown.activity_name


class CooldownWidget(QWidget):
    """Widget displaying all active cooldowns with progress bars."""

    def __init__(
        self,
        tracker: Optional[CooldownTracker] = None,
        parent: Optional[QWidget] = None,
        compact: bool = False,
    ):
        """Initialize cooldown widget.

        Args:
            tracker: Cooldown tracker to use (uses global if None)
            parent: Parent widget
            compact: Use compact display mode
        """
        super().__init__(parent)
        self._tracker = tracker or get_cooldown_tracker()
        self._compact = compact
        self._item_widgets: dict[str, CooldownItemWidget] = {}

        self._setup_ui()
        self._setup_update_timer()
        self._refresh_cooldowns()

    def _setup_ui(self) -> None:
        """Setup the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Header
        if not self._compact:
            header = QLabel("Active Cooldowns")
            header.setStyleSheet("color: #AAA; font-size: 11px; font-weight: bold;")
            layout.addWidget(header)

        # Scroll area for cooldowns
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: rgba(255, 255, 255, 10);
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 40);
                border-radius: 3px;
                min-height: 20px;
            }
        """)

        # Container for cooldown items
        self._container = QWidget()
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(4)
        self._container_layout.addStretch()

        scroll.setWidget(self._container)
        layout.addWidget(scroll)

        # Empty state label
        self._empty_label = QLabel("No active cooldowns")
        self._empty_label.setStyleSheet("color: #666; font-size: 10px; font-style: italic;")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty_label)

    def _setup_update_timer(self) -> None:
        """Setup timer for updating cooldown displays."""
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._update_cooldowns)
        self._update_timer.start(1000)  # Update every second

    def _refresh_cooldowns(self) -> None:
        """Refresh the list of cooldowns from tracker."""
        active = self._tracker.get_active_cooldowns()

        # Track which activities we've seen
        active_names = {cd.activity_name for cd in active}

        # Remove widgets for expired cooldowns
        to_remove = [name for name in self._item_widgets if name not in active_names]
        for name in to_remove:
            widget = self._item_widgets.pop(name)
            self._container_layout.removeWidget(widget)
            widget.deleteLater()

        # Add widgets for new cooldowns
        for cooldown in active:
            if cooldown.activity_name not in self._item_widgets:
                widget = CooldownItemWidget(cooldown)
                self._item_widgets[cooldown.activity_name] = widget
                # Insert before the stretch
                self._container_layout.insertWidget(
                    self._container_layout.count() - 1, widget
                )

        # Update visibility
        has_cooldowns = len(self._item_widgets) > 0
        self._empty_label.setVisible(not has_cooldowns)

    def _update_cooldowns(self) -> None:
        """Update all cooldown displays."""
        # Check for expired cooldowns
        expired = []
        for name, widget in self._item_widgets.items():
            if not widget.update_display():
                expired.append(name)

        # Remove expired widgets
        for name in expired:
            widget = self._item_widgets.pop(name)
            self._container_layout.removeWidget(widget)
            widget.deleteLater()

        # Refresh to pick up new cooldowns
        self._refresh_cooldowns()

    def add_cooldown(
        self,
        activity_name: str,
        display_name: Optional[str] = None,
        duration_seconds: Optional[int] = None,
    ) -> None:
        """Add a cooldown manually.

        Args:
            activity_name: Internal activity name
            display_name: Human-readable name
            duration_seconds: Cooldown duration
        """
        self._tracker.start_cooldown(activity_name, display_name, duration_seconds)
        self._refresh_cooldowns()

    def clear_cooldown(self, activity_name: str) -> None:
        """Clear a specific cooldown.

        Args:
            activity_name: Activity to clear
        """
        self._tracker.clear_cooldown(activity_name)
        self._refresh_cooldowns()

    def clear_all(self) -> None:
        """Clear all cooldowns."""
        for name in list(self._item_widgets.keys()):
            self._tracker.clear_cooldown(name)
        self._refresh_cooldowns()


class CompactCooldownWidget(QWidget):
    """Compact cooldown display for overlay use."""

    def __init__(
        self,
        tracker: Optional[CooldownTracker] = None,
        parent: Optional[QWidget] = None,
        max_display: int = 3,
    ):
        """Initialize compact cooldown widget.

        Args:
            tracker: Cooldown tracker to use
            parent: Parent widget
            max_display: Maximum number of cooldowns to show
        """
        super().__init__(parent)
        self._tracker = tracker or get_cooldown_tracker()
        self._max_display = max_display
        self._labels: list[QLabel] = []

        self._setup_ui()
        self._setup_update_timer()

    def _setup_ui(self) -> None:
        """Setup the widget UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Create labels for displaying cooldowns
        for _ in range(self._max_display):
            label = QLabel()
            label.setStyleSheet("""
                color: #888;
                font-size: 10px;
                background-color: rgba(0, 0, 0, 50);
                padding: 2px 6px;
                border-radius: 3px;
            """)
            label.hide()
            self._labels.append(label)
            layout.addWidget(label)

        layout.addStretch()

    def _setup_update_timer(self) -> None:
        """Setup timer for updating display."""
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._update_display)
        self._update_timer.start(1000)

    def _update_display(self) -> None:
        """Update the cooldown display."""
        cooldowns = self._tracker.get_active_cooldowns()[:self._max_display]

        # Update labels
        for i, label in enumerate(self._labels):
            if i < len(cooldowns):
                cd = cooldowns[i]
                # Use short name and time
                short_name = cd.display_name[:10]
                label.setText(f"{short_name}: {cd.remaining_formatted}")

                # Color based on progress
                if cd.progress >= 0.9:
                    label.setStyleSheet("""
                        color: #4CAF50;
                        font-size: 10px;
                        background-color: rgba(0, 0, 0, 50);
                        padding: 2px 6px;
                        border-radius: 3px;
                    """)
                else:
                    label.setStyleSheet("""
                        color: #FFD700;
                        font-size: 10px;
                        background-color: rgba(0, 0, 0, 50);
                        padding: 2px 6px;
                        border-radius: 3px;
                    """)
                label.show()
            else:
                label.hide()

        # Show/hide widget based on cooldowns
        self.setVisible(len(cooldowns) > 0)
