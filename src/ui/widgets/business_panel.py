"""Business status panel."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QGridLayout,
    QProgressBar,
    QScrollArea,
)
from PyQt6.QtCore import QTimer, Qt

from ...constants import UI, BUSINESS
from ...game.businesses import BUSINESSES, Business
from ...utils.helpers import format_money, format_money_short, format_time

if TYPE_CHECKING:
    from ...app import GTABusinessManager


class BusinessCard(QFrame):
    """Card displaying a single business status."""

    def __init__(self, business: Business, parent=None):
        super().__init__(parent)
        self._business = business

        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            QFrame {
                background-color: #16213e;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        self.setFixedHeight(140)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Header
        header_layout = QHBoxLayout()
        name = QLabel(business.name)
        name.setStyleSheet("color: white; font-size: 14px; font-weight: bold;")
        header_layout.addWidget(name)
        header_layout.addStretch()

        self._value_label = QLabel("--")
        self._value_label.setStyleSheet("color: #4CAF50; font-size: 12px;")
        header_layout.addWidget(self._value_label)

        layout.addLayout(header_layout)

        # Stock bar
        stock_layout = QHBoxLayout()
        stock_label = QLabel("Stock")
        stock_label.setStyleSheet("color: #AAA; font-size: 10px;")
        stock_label.setFixedWidth(50)
        stock_layout.addWidget(stock_label)

        self._stock_bar = QProgressBar()
        self._stock_bar.setMaximum(100)
        self._stock_bar.setValue(0)
        self._stock_bar.setTextVisible(True)
        self._stock_bar.setStyleSheet("""
            QProgressBar {
                background-color: #1a1a2e;
                border: none;
                border-radius: 4px;
                height: 16px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 4px;
            }
        """)
        stock_layout.addWidget(self._stock_bar)

        layout.addLayout(stock_layout)

        # Supplies bar
        supply_layout = QHBoxLayout()
        supply_label = QLabel("Supplies")
        supply_label.setStyleSheet("color: #AAA; font-size: 10px;")
        supply_label.setFixedWidth(50)
        supply_layout.addWidget(supply_label)

        self._supply_bar = QProgressBar()
        self._supply_bar.setMaximum(100)
        self._supply_bar.setValue(0)
        self._supply_bar.setTextVisible(True)
        self._supply_bar.setStyleSheet("""
            QProgressBar {
                background-color: #1a1a2e;
                border: none;
                border-radius: 4px;
                height: 16px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #2196F3;
                border-radius: 4px;
            }
        """)
        supply_layout.addWidget(self._supply_bar)

        layout.addLayout(supply_layout)

        # Status/info
        self._status_label = QLabel("Not tracked")
        self._status_label.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(self._status_label)

    def update_data(self, stock: int, supply: int, value: int = 0, updated: str = "") -> None:
        """Update business card data."""
        self._stock_bar.setValue(stock)
        self._supply_bar.setValue(supply)

        if value > 0:
            self._value_label.setText(format_money_short(value))
        else:
            # Estimate from percentage
            est_value = int(self._business.max_value * (stock / 100))
            self._value_label.setText(f"~{format_money_short(est_value)}")

        # Update stock bar color based on level
        if stock >= BUSINESS.HIGH_STOCK_THRESHOLD:
            stock_color = "#4CAF50"
            status = "Ready to sell!"
        elif stock >= BUSINESS.MEDIUM_SUPPLY_THRESHOLD:
            stock_color = "#FFD700"
            status = "Consider selling"
        else:
            stock_color = "#2196F3"
            status = "Producing..."

        self._stock_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: #1a1a2e;
                border: none;
                border-radius: 4px;
                height: 16px;
                text-align: center;
                color: white;
            }}
            QProgressBar::chunk {{
                background-color: {stock_color};
                border-radius: 4px;
            }}
        """)

        # Update supply bar color
        if supply <= BUSINESS.LOW_SUPPLY_THRESHOLD:
            supply_color = "#F44336"
            status = "Needs supplies!"
        elif supply <= BUSINESS.MEDIUM_SUPPLY_THRESHOLD:
            supply_color = "#FFD700"
        else:
            supply_color = "#2196F3"

        self._supply_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: #1a1a2e;
                border: none;
                border-radius: 4px;
                height: 16px;
                text-align: center;
                color: white;
            }}
            QProgressBar::chunk {{
                background-color: {supply_color};
                border-radius: 4px;
            }}
        """)

        if updated:
            self._status_label.setText(f"{status} (Updated: {updated})")
        else:
            self._status_label.setText(status)

    def set_not_tracked(self) -> None:
        """Mark business as not currently tracked."""
        self._stock_bar.setValue(0)
        self._supply_bar.setValue(0)
        self._value_label.setText("--")
        self._status_label.setText("Not tracked - visit business to update")
        self._status_label.setStyleSheet("color: #666; font-size: 10px;")


class BusinessPanel(QWidget):
    """Panel showing business status cards."""

    def __init__(self, app: "GTABusinessManager" = None, parent=None):
        super().__init__(parent)
        self._app = app
        self._cards: Dict[str, BusinessCard] = {}
        self._setup_ui()
        self._setup_update_timer()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header
        header = QLabel("Business Status")
        header.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        layout.addWidget(header)

        info = QLabel("Visit each business in-game to update stock and supply levels")
        info.setStyleSheet("color: #666; font-size: 11px; margin-bottom: 10px;")
        layout.addWidget(info)

        # Scroll area for businesses
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        scroll_content = QWidget()
        scroll_layout = QGridLayout(scroll_content)
        scroll_layout.setSpacing(12)

        # Create cards for each business
        row, col = 0, 0
        for business_id, business in BUSINESSES.items():
            card = BusinessCard(business)
            self._cards[business_id] = card
            scroll_layout.addWidget(card, row, col)

            col += 1
            if col >= 3:
                col = 0
                row += 1

        scroll_layout.setRowStretch(row + 1, 1)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

    def _setup_update_timer(self):
        """Setup update timer."""
        self._timer = QTimer()
        self._timer.timeout.connect(self._update_display)
        self._timer.start(UI.BUSINESS_UPDATE_INTERVAL_MS)

    def _update_display(self):
        """Update all business cards."""
        if not self._app:
            return

        for business_id, card in self._cards.items():
            state = self._app.get_business_state(business_id)
            if state:
                updated_str = ""
                if "updated" in state:
                    now = datetime.now(timezone.utc)
                    updated_time = state["updated"]
                    # Handle timezone-aware vs naive datetime comparison
                    if now.tzinfo is not None and updated_time.tzinfo is None:
                        now = now.replace(tzinfo=None)
                    elif updated_time.tzinfo is not None and now.tzinfo is None:
                        updated_time = updated_time.replace(tzinfo=None)
                    elapsed = (now - updated_time).total_seconds()
                    updated_str = format_time(elapsed) + " ago"

                card.update_data(
                    stock=state.get("stock", 0),
                    supply=state.get("supply", 0),
                    value=state.get("value", 0),
                    updated=updated_str
                )
            else:
                card.set_not_tracked()
