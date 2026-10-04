"""A blank manual draft that replaces one business's disposable live reading."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox,
    QVBoxLayout,
)

from ...database.business_checkins import (
    BusinessCheckInValidationError, parse_checkin_percent, parse_checkin_value,
)
from ...game.businesses import BUSINESSES
from ...utils.logging import get_logger

logger = get_logger('ui.live_business_reading')


class LiveBusinessReadingDialog(QDialog):
    """Apply one observation to a fixed business, without accessing history."""

    applied = pyqtSignal(str)

    def __init__(self, app, business_id, parent=None):
        super().__init__(parent)
        self._app = app
        self._business_id = business_id
        self._business = BUSINESSES[business_id]
        self._busy = False
        self._closed = False
        self._confirming_discard = False
        self.setWindowTitle('Enter live business reading')
        self.setModal(True)
        self.resize(520, 340)
        self._setup_ui()

    @staticmethod
    def _label(text=''):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        self._context_label = self._label(f'{self._business.name} · LIVE reading')
        layout.addWidget(self._context_label)
        self._help_label = self._label(
            'Enter at least one observation. Blank replaces that field with unknown; '
            'zero is a known value. This replaces only this business’s LIVE reading. '
            'It does not save history or change saved check-ins. Later OCR can replace it.'
        )
        layout.addWidget(self._help_label)
        form = QFormLayout()
        self._stock_edit = QLineEdit()
        self._supply_edit = QLineEdit()
        self._value_edit = QLineEdit()
        for name, field in (('Stock', self._stock_edit), ('Supply', self._supply_edit),
                            ('Value', self._value_edit)):
            field.setObjectName(f'liveBusinessReading{name}')
            # Reject full invalid input rather than silently truncating its digits.
            field.setMaxLength(2147483647)
        self._stock_edit.setPlaceholderText('Optional · whole number 0–100')
        self._supply_edit.setPlaceholderText('Optional · whole number 0–100')
        self._value_edit.setPlaceholderText('Optional · digits only, no commas or $')
        self._value_edit.setToolTip('Whole number from 0 to 9223372036854775807')
        form.addRow('Observed stock (%)', self._stock_edit)
        form.addRow('Observed supplies (%)', self._supply_edit)
        form.addRow('Observed stock value ($)', self._value_edit)
        layout.addLayout(form)
        layout.addStretch()
        self._status_label = self._label()
        layout.addWidget(self._status_label)
        buttons = QDialogButtonBox()
        self._apply_button = buttons.addButton('Apply', QDialogButtonBox.ButtonRole.AcceptRole)
        self._cancel_button = buttons.addButton('Cancel', QDialogButtonBox.ButtonRole.RejectRole)
        self._apply_button.setDefault(True)
        self._apply_button.clicked.connect(self._apply)
        self._cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)
        if not self._business.uses_supplies:
            self._supply_edit.setPlaceholderText('Not applicable')
        self._update_actions()

    def _draft(self):
        return self._stock_edit.text(), self._supply_edit.text(), self._value_edit.text()

    def _update_actions(self):
        enabled = not (self._busy or self._closed or self._confirming_discard)
        for control in (self._stock_edit, self._value_edit,
                        self._apply_button, self._cancel_button):
            control.setEnabled(enabled)
        self._supply_edit.setEnabled(enabled and self._business.uses_supplies)

    def _apply(self):
        if self._busy or self._closed or self._confirming_discard:
            return
        self._busy = True
        self._update_actions()
        try:
            stock, supply, value = self._draft()
            stock = parse_checkin_percent(stock)
            supply = parse_checkin_percent(supply) if self._business.uses_supplies else None
            value = parse_checkin_value(value)
            if stock is supply is value is None:
                raise BusinessCheckInValidationError('Enter at least one observation.')
            self._app.set_manual_business_reading(
                self._business_id, stock_percent=stock, supply_percent=supply, value=value,
            )
        except BusinessCheckInValidationError as exc:
            self._status_label.setText(f'Live reading not applied: {exc} Your draft is still here.')
        except Exception as exc:
            logger.warning('Could not apply live reading (%s)', type(exc).__name__)
            self._status_label.setText(
                'Live reading not applied. Your draft is still here. Try Apply again.'
            )
        else:
            # An applied observation is final even if a separate display refresh fails.
            self._closed = True
            super().done(QDialog.DialogCode.Accepted)
            self.applied.emit(self._business_id)
        finally:
            self._busy = False
            self._update_actions()

    def _confirm_discard(self):
        if self._busy or self._closed or self._confirming_discard:
            return False
        if not any(self._draft()):
            return True
        self._confirming_discard = True
        self._update_actions()
        try:
            return QMessageBox.question(
                self, 'Discard live reading?', 'Discard your unapplied live reading and close?',
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            ) == QMessageBox.StandardButton.Discard
        finally:
            self._confirming_discard = False
            self._update_actions()

    def done(self, result):
        # Only _apply may accept. External accept/done cannot bypass draft protection.
        if self._confirm_discard():
            self._closed = True
            self._update_actions()
            super().done(QDialog.DialogCode.Rejected)

    def reject(self):
        self.done(QDialog.DialogCode.Rejected)

    def closeEvent(self, event):
        if not self._confirm_discard():
            event.ignore()
            return
        self._closed = True
        self._update_actions()
        super().done(QDialog.DialogCode.Rejected)
        event.accept()
