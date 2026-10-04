"""Recommendations and temporary, app-wide snooze controls."""

from typing import TYPE_CHECKING, Callable, List, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QScrollArea,
    QPushButton,
    QSizePolicy,
)
from PyQt6.QtCore import QTimer, Qt

from ...optimization.optimizer import Recommendation
from ...utils.helpers import format_money_short

if TYPE_CHECKING:
    from ...app import GTABusinessManager


def _label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class _BoundActionButton(QPushButton):
    """Keep a press attached to its original owner and recommendation identity.

    Recycled cards can change between press and release, including A -> B -> A.
    Each new binding gets a distinct control and token. Retiring the control also
    isolates Qt's native animateClick timer from a newer physical press. Each
    control has one clicked connection; bare clicked signals do nothing.
    """

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._binding = None
        self._pressed_binding = None
        self._callback = None
        self.setAutoDefault(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setEnabled(False)
        self.pressed.connect(self._begin_activation)
        self.clicked.connect(self._dispatch)

    def bind(self, owner, key: Optional[str], action: Optional[Callable]) -> "_BoundActionButton":
        if owner is None or not isinstance(key, str) or not key.strip() or action is None:
            owner, key, action = None, None, None
        if (self._binding is not None
                and (self._binding[0] is not owner or self._binding[1] != key)):
            had_focus = self.hasFocus()
            self._binding = None
            self._callback = None
            self._cancel_press()
            self.setEnabled(False)
            replacement = _BoundActionButton(self.text(), self.parentWidget())
            replacement.setAccessibleName(self.accessibleName())
            replacement.setToolTip(self.toolTip())
            self.parentWidget().layout().replaceWidget(self, replacement)
            # New children otherwise append to the window's Tab chain. Insert
            # beside the retired control so its deletion preserves visual order.
            QWidget.setTabOrder(self, replacement)
            self.hide()
            self.deleteLater()
            replacement.bind(owner, key, action)
            replacement.show()
            if had_focus and action is not None:
                replacement.setFocus(Qt.FocusReason.OtherFocusReason)
            return replacement
        if self._binding is None:
            self._binding = (owner, key) if action else None
        binding = self._binding
        if action is None:
            self._callback = None
        else:
            def invoke():
                if self._binding is binding and self.isEnabled():
                    action(owner, key)
            self._callback = invoke
        self.setEnabled(action is not None)
        return self

    def _cancel_press(self) -> None:
        self._pressed_binding = None
        self.setDown(False)

    def _begin_activation(self) -> None:
        # Native accessibility, click() and animateClick() enter through this
        # signal too, without necessarily calling Python event handlers.
        self._pressed_binding = self._binding

    def _dispatch(self, _checked: bool = False) -> None:
        binding = self._pressed_binding
        try:
            if binding is not None and binding is self._binding and self._callback is not None:
                self._callback()
        finally:
            self._pressed_binding = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed_binding = self._binding
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            try:
                if self._pressed_binding is not None and self._pressed_binding is self._binding:
                    super().mouseReleaseEvent(event)
                else:
                    event.accept()
            finally:
                self._cancel_press()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            if event.isAutoRepeat():
                event.accept()
                return
            self._pressed_binding = self._binding
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            if event.isAutoRepeat():
                event.accept()
                return
            try:
                if self._pressed_binding is not None and self._pressed_binding is self._binding:
                    super().keyReleaseEvent(event)
                else:
                    event.accept()
            finally:
                self._cancel_press()
        else:
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event) -> None:
        self._cancel_press()
        super().focusOutEvent(event)


class RecommendationCard(QFrame):
    """Card displaying a recommendation with an identity-bound snooze action."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("recommendationCard")
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 12, 12)
        layout.setSpacing(4)

        header = QHBoxLayout()
        self._priority_label = _label("Priority 1")
        header.addWidget(self._priority_label)
        header.addStretch()
        self._value_label = _label()
        self._value_label.setStyleSheet("color: #4CAF50; font-size: 12px;")
        header.addWidget(self._value_label)
        layout.addLayout(header)

        self._action_label = _label()
        self._action_label.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
        self._action_label.setWordWrap(True)
        self._action_label.setMinimumWidth(0)
        layout.addWidget(self._action_label)
        self._reason_label = _label()
        self._reason_label.setStyleSheet("color: #AAA; font-size: 12px;")
        self._reason_label.setWordWrap(True)
        self._reason_label.setMinimumWidth(0)
        layout.addWidget(self._reason_label)

        footer = QHBoxLayout()
        self._time_label = _label()
        self._time_label.setStyleSheet("color: #999; font-size: 11px;")
        footer.addWidget(self._time_label)
        footer.addStretch()
        self._snooze_button = _BoundActionButton("Snooze 10 min")
        footer.addWidget(self._snooze_button)
        layout.addLayout(footer)

    def set_recommendation(
        self, rec: Recommendation, index: int, owner=None, action: Optional[Callable] = None,
    ) -> None:
        """Update content; only a stable identity and owning app enable snoozing."""
        self._priority_label.setText(f"Priority {rec.priority}")
        colors = {1: "#F44336", 2: "#FF9800", 3: "#FFD700", 4: "#4CAF50", 5: "#2196F3"}
        color = colors.get(rec.priority, "#AAA")
        self._priority_label.setStyleSheet(
            f"color: {color}; font-size: 10px; background-color: #1a1a2e; "
            "padding: 2px 8px; border-radius: 4px;"
        )
        self.setStyleSheet(f"""
            QFrame#recommendationCard {{
                background-color: #16213e;
                border-radius: 8px;
                border-left: 4px solid {color};
            }}
        """)
        self._action_label.setText(rec.action)
        self._reason_label.setText(rec.reason)
        self._value_label.setText(
            f"~{format_money_short(rec.estimated_value)}" if rec.estimated_value > 0 else ""
        )
        self._time_label.setText(
            f"Est. time: {rec.estimated_time_minutes} min" if rec.estimated_time_minutes > 0 else ""
        )
        self._snooze_button.setAccessibleName(f"Snooze {rec.action} for 10 min")
        self._snooze_button.setToolTip("Temporarily hide this action across the app for 10 minutes")
        self._snooze_button = self._snooze_button.bind(
            owner, getattr(rec, "recommendation_id", None), action
        )

    def retire(self) -> None:
        """Cancel a pending activation before hiding a reused card."""
        self._snooze_button = self._snooze_button.bind(None, None, None)
        self.hide()


class RecommendationsPanel(QWidget):
    """Panel showing workflow recommendations and temporary snooze status."""

    def __init__(self, app: Optional["GTABusinessManager"] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._app = app
        self._display_owner = None
        self._cards: List[RecommendationCard] = []
        self._setup_ui()
        self._setup_update_timer()
        self._update_display()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 12, 16, 12)
        header = _label("Recommended Actions")
        header.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        layout.addWidget(header)
        info = _label(
            "Based on tracked businesses and activity history.\n"
            "Snooze for 10 min app-wide. Temporary snoozes are not saved after closing the app."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #AAA; font-size: 11px;")
        layout.addWidget(info)

        status = QHBoxLayout()
        self._snooze_status_label = _label()
        self._snooze_status_label.setWordWrap(True)
        status.addWidget(self._snooze_status_label, 1)
        self._restore_button = _BoundActionButton("Restore all")
        self._restore_button.setAccessibleName("Restore all snoozed recommendations")
        status.addWidget(self._restore_button)
        layout.addLayout(status)
        self._action_status_label = _label()
        self._action_status_label.setWordWrap(True)
        self._action_status_label.hide()
        layout.addWidget(self._action_status_label)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        content = QWidget()
        self._scroll_layout = QVBoxLayout(content)
        self._scroll_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll_layout.setSpacing(8)
        for _ in range(5):
            card = RecommendationCard()
            card.hide()
            self._cards.append(card)
            self._scroll_layout.addWidget(card)
        self._scroll_layout.addStretch()
        self._scroll.setWidget(content)
        layout.addWidget(self._scroll, 1)

        self._empty_label = _label()
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet("color: #AAA; font-size: 12px;")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty_label, 1)

    def _setup_update_timer(self) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_display)
        self._timer.start(5000)

    def _show_outcome(self, text: str) -> None:
        self._action_status_label.setText(text)
        self._action_status_label.setVisible(bool(text))

    def _retire_actions(self) -> None:
        for card in self._cards:
            card.retire()
        self._restore_button = self._restore_button.bind(None, None, None)

    def _snooze(self, owner, recommendation_id: str) -> None:
        if self._app is not owner:
            return
        try:
            changed = owner.snooze_recommendation(recommendation_id)
        except Exception:
            self._show_outcome("This recommendation could not be snoozed. Try again.")
            return
        self._show_outcome(
            "Snoozed for 10 min across the app." if changed
            else "This recommendation could not be snoozed. Try again, or choose Restore all."
        )
        self._update_display()

    def _restore(self, owner, _key: str) -> None:
        if self._app is not owner:
            return
        try:
            owner.restore_snoozed_recommendations()
        except Exception:
            self._show_outcome("Snoozed recommendations could not be restored. Try again.")
            return
        self._show_outcome("All snoozed recommendations restored.")
        self._update_display()

    def _update_display(self) -> None:
        owner = self._app
        if owner is not self._display_owner:
            self._retire_actions()
            self._show_outcome("")
            self._display_owner = owner
        if owner is None:
            self._retire_actions()
            self._snooze_status_label.setText("No recommendations available.")
            self._empty_label.setText("No recommendations yet. Connect to the app to see actions.")
            self._empty_label.show()
            self._scroll.hide()
            return
        try:
            snapshot = owner.recommendation_snapshot
            recommendations = snapshot.visible
            total = snapshot.total_candidates
            hidden = snapshot.hidden_count
            snoozed = snapshot.snoozed_count
        except Exception:
            self._retire_actions()
            self._snooze_status_label.setText("Snooze status unavailable.")
            self._empty_label.setText("Recommendations could not be refreshed. Please try again shortly.")
            self._empty_label.show()
            self._scroll.hide()
            return

        self._snooze_status_label.setText(
            f"{snoozed} snoozed · {hidden} currently hidden" if snoozed else "No snoozed recommendations"
        )
        can_restore = snoozed > 0 and callable(getattr(owner, "restore_snoozed_recommendations", None))
        self._restore_button = self._restore_button.bind(
            owner if can_restore else None, "restore", self._restore
        )
        can_snooze = callable(getattr(owner, "snooze_recommendation", None))
        for index, card in enumerate(self._cards):
            if index < len(recommendations):
                card.set_recommendation(
                    recommendations[index], index, owner if can_snooze else None, self._snooze
                )
                card.show()
            else:
                card.retire()
        self._scroll.setVisible(bool(recommendations))
        self._empty_label.setVisible(not recommendations)
        self._empty_label.setText(
            "All available recommendations are snoozed.\n"
            "They return after 10 min, or choose Restore all."
            if total > 0 and hidden == total else
            "No recommendations yet.\n\n"
            "Track business stock, complete activities, or update business states to see actions."
        )
