"""Business status panel."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QGridLayout,
    QProgressBar,
    QScrollArea,
    QPushButton,
    QComboBox,
)
from PyQt6.QtCore import QSignalBlocker, QTimer, Qt

from ...constants import UI, BUSINESS
from ...game.businesses import BUSINESSES, Business
from ...utils.helpers import format_money_short, format_time
from ...utils.logging import get_logger

logger = get_logger('ui.business_panel')

if TYPE_CHECKING:
    from ...app import GTABusinessManager


class BusinessCard(QFrame):
    """Card displaying a single business status."""

    def __init__(self, business: Business, parent=None):
        super().__init__(parent)
        self._business = business

        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setObjectName("businessCard")
        self.setStyleSheet("""
            QFrame#businessCard {
                background-color: #16213e;
                border-radius: 8px;
                padding: 0;
            }
        """)
        self.setFixedHeight(204)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        # Header
        header_layout = QVBoxLayout()
        header_layout.setSpacing(0)
        name = QLabel(business.name)
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setWordWrap(True)
        name.setStyleSheet("color: white; font-size: 14px; font-weight: bold;")
        header_layout.addWidget(name)

        self._value_label = QLabel("--")
        self._value_label.setTextFormat(Qt.TextFormat.PlainText)
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
        self._status_label.setTextFormat(Qt.TextFormat.PlainText)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(self._status_label)

        self._live_reading_button = QPushButton('Enter live reading…')
        self._live_reading_button.setAccessibleName(f'Enter live reading for {business.name}')
        self._live_reading_button.setEnabled(False)
        layout.addWidget(self._live_reading_button)
        self.set_not_tracked()

    @staticmethod
    def _set_progress(bar, value, *, applicable=True):
        # 0..0 would animate a busy indicator, which is not an unknown reading.
        bar.setRange(0, 100)
        bar.setValue(value if value is not None and applicable else 0)
        bar.setFormat('Not applicable' if not applicable else 'Unknown' if value is None else '%p%')

    def update_data(
        self, stock: Optional[int], supply: Optional[int], value: Optional[int] = None, updated: str = "",
        identity_source: Optional[str] = None,
    ) -> None:
        """Update business card data."""
        self._set_progress(self._stock_bar, stock)
        self._set_progress(self._supply_bar, supply, applicable=self._business.uses_supplies)

        if value is not None:
            self._value_label.setText(format_money_short(value))
            self._value_label.setToolTip(f'${value:,}')
        elif stock is not None:
            # Estimate from percentage
            est_value = int(self._business.max_value * (stock / 100))
            self._value_label.setText(f"~{format_money_short(est_value)}")
            self._value_label.setToolTip(f'Estimated from stock: ${est_value:,}')
        else:
            self._value_label.setText('Unknown')
            self._value_label.setToolTip('No observed value or known stock to estimate from')

        # Update stock bar color based on level
        if stock is None:
            stock_color = '#666'
            status = 'Stock unknown'
        elif stock >= BUSINESS.HIGH_STOCK_THRESHOLD:
            stock_color = "#4CAF50"
            status = "Ready to sell!" if value is None or value > 0 else 'Stock observed'
        elif stock >= BUSINESS.MEDIUM_SUPPLY_THRESHOLD:
            stock_color = "#FFD700"
            status = "Consider selling" if value is None or value > 0 else 'Stock observed'
        else:
            stock_color = "#2196F3"
            status = "Stock observed"

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
        if not self._business.uses_supplies or supply is None:
            supply_color = '#666'
        elif supply <= BUSINESS.LOW_SUPPLY_THRESHOLD:
            supply_color = "#F44336"
            if stock is not None and stock < 100:
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
            status = f"{status} (Updated: {updated})"
        source_label = {
            "ocr_text": "OCR text match",
            "selected_target": "Selected target",
            "manual_entry": "Manual entry",
        }.get(identity_source)
        if source_label:
            status = f"{status}\n{source_label}"
        self._status_label.setText(status)
        self._status_label.setStyleSheet("color: #AAA; font-size: 10px;")

    def set_not_tracked(self) -> None:
        """Mark business as not currently tracked."""
        self._set_progress(self._stock_bar, None)
        self._set_progress(self._supply_bar, None, applicable=self._business.uses_supplies)
        self._value_label.setText("--")
        self._value_label.setToolTip('No live reading')
        self._status_label.setText("Not tracked · visit or enter a reading")
        self._status_label.setStyleSheet("color: #666; font-size: 10px;")


class BusinessPanel(QWidget):
    """Panel showing business status cards."""

    def __init__(self, app: "GTABusinessManager" = None, parent=None):
        super().__init__(parent)
        self._app = app
        self._cards: Dict[str, BusinessCard] = {}
        self._checkins_dialog = None
        self._opening_checkins = False
        self._live_reading_dialog = None
        self._opening_live_reading = False
        self._setup_ui()
        self._setup_update_timer()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header
        header = QLabel("Business Status")
        header.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        heading_row = QHBoxLayout()
        heading_row.addWidget(header, 1)
        self._clear_readings_button = QPushButton("Clear live readings")
        self._clear_readings_button.setAccessibleName("Clear live readings")
        self._clear_readings_button.setEnabled(
            callable(getattr(self._app, "clear_business_readings", None))
        )
        self._clear_readings_button.clicked.connect(self._clear_live_readings)
        heading_row.addWidget(self._clear_readings_button)
        self._manual_checkins_button = QPushButton('Manual check-ins…')
        self._manual_checkins_button.setEnabled(self._app is not None)
        self._manual_checkins_button.clicked.connect(self._open_manual_checkins)
        heading_row.addWidget(self._manual_checkins_button)
        layout.addLayout(heading_row)
        self._checkins_status_label = QLabel()
        self._checkins_status_label.setTextFormat(Qt.TextFormat.PlainText)
        self._checkins_status_label.setWordWrap(True)
        self._checkins_status_label.hide()
        layout.addWidget(self._checkins_status_label)
        self._live_reading_status_label = QLabel()
        self._live_reading_status_label.setTextFormat(Qt.TextFormat.PlainText)
        self._live_reading_status_label.setWordWrap(True)
        self._live_reading_status_label.hide()
        layout.addWidget(self._live_reading_status_label)

        info = QLabel("Visit each business in-game to update stock and supply levels")
        info.setTextFormat(Qt.TextFormat.PlainText)
        info.setStyleSheet("color: #666; font-size: 11px; margin-bottom: 10px;")
        layout.addWidget(info)

        self._clear_readings_help_label = QLabel(
            "Clears live stock, supply and value observations for all businesses. "
            "Later OCR can refill the cards. Saved check-ins are kept."
        )
        self._clear_readings_help_label.setTextFormat(Qt.TextFormat.PlainText)
        self._clear_readings_help_label.setWordWrap(True)
        self._clear_readings_help_label.setStyleSheet("color: #AAA; font-size: 11px;")
        self._clear_readings_button.setAccessibleDescription(
            self._clear_readings_help_label.text()
        )
        layout.addWidget(self._clear_readings_help_label)
        self._clear_readings_status_label = QLabel()
        self._clear_readings_status_label.setTextFormat(Qt.TextFormat.PlainText)
        self._clear_readings_status_label.setWordWrap(True)
        self._clear_readings_status_label.hide()
        layout.addWidget(self._clear_readings_status_label)

        target_row = QHBoxLayout()
        self._business_target_label = QLabel("Business screen target")
        self._business_target_label.setTextFormat(Qt.TextFormat.PlainText)
        target_row.addWidget(self._business_target_label)
        self._business_target_combo = QComboBox()
        self._business_target_combo.setAccessibleName("Business screen target")
        self._business_target_combo.addItem("Automatic", None)
        for business_id, business in BUSINESSES.items():
            self._business_target_combo.addItem(business.name, business_id)
        self._business_target_combo.setEnabled(
            callable(getattr(self._app, "set_business_screen_target", None))
        )
        self._business_target_label.setBuddy(self._business_target_combo)
        self._sync_business_screen_target()
        self._business_target_combo.currentIndexChanged.connect(self._on_business_target_changed)
        target_row.addWidget(self._business_target_combo, 1)
        layout.addLayout(target_row)

        self._business_target_help_label = QLabel(
            "Assigns recognized readings to the selected business; it does not verify "
            "the screen or OCR values. Automatic uses OCR text matches.\n"
            "Readings still need recognizable labels or markers; bare numbers are not "
            "supported. The selection lasts until Stop and is not saved."
        )
        self._business_target_help_label.setTextFormat(Qt.TextFormat.PlainText)
        self._business_target_help_label.setWordWrap(True)
        self._business_target_help_label.setStyleSheet("color: #AAA; font-size: 11px;")
        layout.addWidget(self._business_target_help_label)

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
            card._live_reading_button.setEnabled(
                callable(getattr(self._app, 'set_manual_business_reading', None))
            )
            card._live_reading_button.clicked.connect(
                lambda checked=False, bid=business_id: self._open_live_reading(bid)
            )
            self._cards[business_id] = card
            scroll_layout.addWidget(card, row, col)

            col += 1
            if col >= 3:
                col = 0
                row += 1

        scroll_layout.setRowStretch(row + 1, 1)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

    def _open_live_reading(self, business_id):
        """Open one fixed-business draft, independently of the OCR target."""
        if self._opening_live_reading:
            return
        if self._live_reading_dialog is not None:
            self._live_reading_dialog.show()
            self._live_reading_dialog.raise_()
            self._live_reading_dialog.activateWindow()
            return
        if not callable(getattr(self._app, 'set_manual_business_reading', None)):
            return
        self._opening_live_reading = True
        try:
            from .live_business_reading_dialog import LiveBusinessReadingDialog
            dialog = LiveBusinessReadingDialog(self._app, business_id, parent=self)
            self._live_reading_dialog = dialog
            dialog.applied.connect(self._live_reading_applied)
            dialog.finished.connect(lambda result, closed=dialog: self._live_reading_finished(closed))
            self._live_reading_status_label.hide()
            dialog.show()
        except Exception as exc:
            logger.warning('Could not open live reading editor (%s)', type(exc).__name__)
            self._live_reading_status_label.setText('Live reading editor could not be opened. Try again.')
            self._live_reading_status_label.show()
        finally:
            self._opening_live_reading = False

    def _live_reading_applied(self, business_id):
        self._clear_readings_status_label.hide()
        try:
            self._update_display()
        except Exception as exc:
            logger.warning('Could not refresh applied live reading (%s)', type(exc).__name__)
            self._live_reading_status_label.setText(
                'Live reading applied, but the cards could not refresh. They will retry automatically.'
            )
        else:
            self._live_reading_status_label.setText(f'Live reading applied for {BUSINESSES[business_id].name}.')
        self._live_reading_status_label.show()

    def _live_reading_finished(self, dialog):
        if self._live_reading_dialog is dialog:
            self._live_reading_dialog = None
            dialog.deleteLater()

    def _clear_live_readings(self) -> None:
        """Clear disposable observations without changing capture or saved history."""
        clear = getattr(self._app, "clear_business_readings", None)
        if not callable(clear):
            self._clear_readings_button.setEnabled(False)
            return
        try:
            clear()
        except Exception as exc:
            logger.warning('Could not clear live readings (%s)', type(exc).__name__)
            self._clear_readings_status_label.setText(
                "Live readings could not be cleared. Try again."
            )
        else:
            self._live_reading_status_label.hide()
            try:
                self._update_display()
            except Exception as exc:
                logger.warning('Could not refresh business cards (%s)', type(exc).__name__)
                self._clear_readings_status_label.setText(
                    "Live readings cleared, but the cards could not refresh. Try again."
                )
            else:
                self._clear_readings_status_label.setText(
                    "Live readings cleared. Later OCR can refill the cards."
                )
        self._clear_readings_status_label.show()

    def _on_business_target_changed(self, index: int) -> None:
        """Assign future live readings without starting capture or editing cards."""
        if not 0 <= index < self._business_target_combo.count():
            return
        setter = getattr(self._app, "set_business_screen_target", None)
        if callable(setter):
            setter(self._business_target_combo.itemData(index))

    def _sync_business_screen_target(self) -> None:
        """Reflect app-side target changes without writing them back to the app."""
        target = getattr(self._app, "business_screen_target", None)
        index = self._business_target_combo.findData(target)
        with QSignalBlocker(self._business_target_combo):
            self._business_target_combo.setCurrentIndex(max(0, index))

    def _open_manual_checkins(self):
        """Resolve stopped-safe storage only when the manual workflow is requested."""
        if self._opening_checkins or self._app is None:
            return
        if self._checkins_dialog is not None:
            self._checkins_dialog.show()
            self._checkins_dialog.raise_()
            self._checkins_dialog.activateWindow()
            return
        self._opening_checkins = True
        try:
            from .business_checkins_dialog import BusinessCheckInsDialog
            repository = self._app.history_repository
            character_id = self._app.data.character_id
            dialog = BusinessCheckInsDialog(
                repository, parent=self, character_id=character_id,
                live_reading_provider=getattr(self._app, 'get_live_business_reading_snapshot', None),
                live_readings_provider=getattr(self._app, 'get_live_business_reading_snapshots', None),
            )
            self._checkins_dialog = dialog
            dialog.finished.connect(lambda result, closed=dialog: self._checkins_finished(closed))
            self._checkins_status_label.hide()
            dialog.show()
        except Exception as exc:
            logger.warning('Could not open manual check-ins (%s)', type(exc).__name__)
            self._checkins_status_label.setText('Manual check-ins could not be opened. Try again when saved history is available.')
            self._checkins_status_label.show()
        finally:
            self._opening_checkins = False

    def _checkins_finished(self, dialog):
        if self._checkins_dialog is not dialog:
            return
        self._checkins_dialog = None
        dialog.deleteLater()

    def closeEvent(self, event):
        if self._live_reading_dialog is not None and not self._live_reading_dialog.close():
            event.ignore()
            return
        if self._checkins_dialog is not None and not self._checkins_dialog.close():
            event.ignore()
            return
        super().closeEvent(event)

    def _setup_update_timer(self):
        """Setup update timer."""
        self._timer = QTimer()
        self._timer.timeout.connect(self._update_display)
        self._timer.start(UI.BUSINESS_UPDATE_INTERVAL_MS)

    def _update_display(self):
        """Update all business cards."""
        self._sync_business_screen_target()
        if not self._app:
            return

        for business_id, card in self._cards.items():
            state = self._app.get_business_state(business_id)
            if state:
                updated_str = ""
                if "updated" in state:
                    updated_time = state["updated"]
                    # Naive app observations use local time; aware readings keep
                    # their offset when compared with an aware UTC clock.
                    now = (datetime.now() if updated_time.tzinfo is None
                           else datetime.now(timezone.utc))
                    elapsed = (now - updated_time).total_seconds()
                    updated_str = format_time(elapsed) + " ago"

                card.update_data(
                    stock=state.get("stock"),
                    supply=state.get("supply"),
                    value=state.get("value"),
                    updated=updated_str,
                    identity_source=state.get("identity_source"),
                )
            else:
                card.set_not_tracked()
