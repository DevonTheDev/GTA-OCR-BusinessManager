"""Recommendations panel."""

from typing import TYPE_CHECKING, List, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QScrollArea,
)
from PyQt6.QtCore import QTimer, Qt

from ...optimization.optimizer import Recommendation
from ...utils.helpers import format_money_short, format_time

if TYPE_CHECKING:
    from ...app import GTABusinessManager


class RecommendationCard(QFrame):
    """Card displaying a single recommendation."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            QFrame {
                background-color: #16213e;
                border-radius: 8px;
                border-left: 4px solid #FFD700;
                padding: 16px;
                margin-bottom: 8px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        # Priority badge
        header_layout = QHBoxLayout()

        self._priority_label = QLabel("Priority 1")
        self._priority_label.setStyleSheet(
            "color: #FFD700; font-size: 10px; background-color: #1a1a2e; "
            "padding: 2px 8px; border-radius: 4px;"
        )
        header_layout.addWidget(self._priority_label)
        header_layout.addStretch()

        self._value_label = QLabel("")
        self._value_label.setStyleSheet("color: #4CAF50; font-size: 12px;")
        header_layout.addWidget(self._value_label)

        layout.addLayout(header_layout)

        # Action
        self._action_label = QLabel("Action")
        self._action_label.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
        self._action_label.setWordWrap(True)
        layout.addWidget(self._action_label)

        # Reason
        self._reason_label = QLabel("Reason")
        self._reason_label.setStyleSheet("color: #AAA; font-size: 12px;")
        self._reason_label.setWordWrap(True)
        layout.addWidget(self._reason_label)

        # Time estimate
        self._time_label = QLabel("")
        self._time_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self._time_label)

    def set_recommendation(self, rec: Recommendation, index: int) -> None:
        """Update card with recommendation data."""
        self._priority_label.setText(f"Priority {rec.priority}")

        # Color based on priority
        colors = {1: "#F44336", 2: "#FF9800", 3: "#FFD700", 4: "#4CAF50", 5: "#2196F3"}
        color = colors.get(rec.priority, "#AAA")
        self._priority_label.setStyleSheet(
            f"color: {color}; font-size: 10px; background-color: #1a1a2e; "
            "padding: 2px 8px; border-radius: 4px;"
        )
        self.setStyleSheet(f"""
            QFrame {{
                background-color: #16213e;
                border-radius: 8px;
                border-left: 4px solid {color};
                padding: 16px;
                margin-bottom: 8px;
            }}
        """)

        self._action_label.setText(rec.action)
        self._reason_label.setText(rec.reason)

        if rec.estimated_value > 0:
            self._value_label.setText(f"~{format_money_short(rec.estimated_value)}")
        else:
            self._value_label.setText("")

        if rec.estimated_time_minutes > 0:
            self._time_label.setText(f"Est. time: {rec.estimated_time_minutes} min")
        else:
            self._time_label.setText("")


class RecommendationsPanel(QWidget):
    """Panel showing workflow recommendations."""

    def __init__(self, app: Optional["GTABusinessManager"] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._app = app
        self._cards: List[RecommendationCard] = []
        self._setup_ui()
        self._setup_update_timer()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header
        header = QLabel("Recommended Actions")
        header.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        layout.addWidget(header)

        info = QLabel("Based on your tracked businesses and activity history")
        info.setStyleSheet("color: #666; font-size: 11px; margin-bottom: 10px;")
        layout.addWidget(info)

        # Scroll area for recommendations
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        scroll_content = QWidget()
        self._scroll_layout = QVBoxLayout(scroll_content)
        self._scroll_layout.setSpacing(8)

        # Create placeholder cards
        for i in range(5):
            card = RecommendationCard()
            card.hide()
            self._cards.append(card)
            self._scroll_layout.addWidget(card)

        self._scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

        # Empty state
        self._empty_label = QLabel(
            "No recommendations yet.\n\n"
            "Recommendations will appear as you:\n"
            "- Track business stock levels\n"
            "- Complete activities\n"
            "- Update your business states"
        )
        self._empty_label.setStyleSheet("color: #666; font-size: 12px;")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty_label)

    def _setup_update_timer(self) -> None:
        """Setup update timer."""
        self._timer = QTimer()
        self._timer.timeout.connect(self._update_display)
        self._timer.start(5000)  # Update every 5 seconds

    def _update_display(self) -> None:
        """Update recommendations display."""
        if not self._app:
            return

        recommendations = self._app.recommendations

        if recommendations:
            self._empty_label.hide()

            for i, card in enumerate(self._cards):
                if i < len(recommendations):
                    card.set_recommendation(recommendations[i], i)
                    card.show()
                else:
                    card.hide()
        else:
            self._empty_label.show()
            for card in self._cards:
                card.hide()
