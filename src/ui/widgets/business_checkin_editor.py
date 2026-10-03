"""Append one manual observation to a fixed saved character and business."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget,
)

from ...database.business_checkins import (
    BusinessCheckInValidationError, BusinessCheckInUnavailable, business_label,
    normalize_business_checkin, parse_checkin_percent, parse_checkin_value,
)
from ...utils.logging import get_logger

logger = get_logger('ui.business_checkin_editor')


class BusinessCheckInEditor(QDialog):
    """A blank draft whose repository and target never follow app selection."""

    saved = pyqtSignal(object)

    def __init__(self, repository, character_id, business_id, parent=None, *, character_name=''):
        super().__init__(parent)
        self._repository = repository
        self._character_id = character_id
        self._business_id = business_id
        self._busy = False
        self._closed = False
        self._confirming_discard = False
        self.setWindowTitle('Record manual business check-in')
        self.setModal(False)
        self.resize(620, 530)
        self._setup_ui(character_name)

    @staticmethod
    def _label(text=''):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def _setup_ui(self, character_name):
        layout = QVBoxLayout(self)
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        self._context_label = self._label(
            f'{character_name or "Saved character"} (#{self._character_id})\n'
            f'{business_label(self._business_id)} · {self._business_id}'
        )
        body.addWidget(self._context_label)
        body.addWidget(self._label(
            'Record only what you observed. Blank means unknown; zero is a known value. '
            'Each check-in stands alone and uses the time you press Save. '
            'Enter at least one value or a personal note.'
        ))
        form = QFormLayout()
        self._stock_edit = QLineEdit()
        self._supply_edit = QLineEdit()
        self._value_edit = QLineEdit()
        for name, field in (('Stock', self._stock_edit), ('Supply', self._supply_edit), ('Value', self._value_edit)):
            field.setObjectName(f'businessCheckIn{name}')
            # Validation rejects the full input instead of silently truncating it.
            field.setMaxLength(2147483647)
        self._stock_edit.setPlaceholderText('Optional · whole number 0–100')
        self._supply_edit.setPlaceholderText('Optional · whole number 0–100')
        self._value_edit.setPlaceholderText('Optional · digits only, no commas or currency symbol')
        form.addRow('Observed stock (%)', self._stock_edit)
        form.addRow('Observed supplies (%)', self._supply_edit)
        form.addRow('Observed stock value ($)', self._value_edit)
        body.addLayout(form)
        body.addWidget(self._label('Personal note · optional, up to 2,000 characters'))
        self._note_edit = QPlainTextEdit()
        self._note_edit.setObjectName('businessCheckInNote')
        self._note_edit.setMinimumHeight(130)
        body.addWidget(self._note_edit, 1)
        body.addWidget(self._label(
            'Saved manual context is separate from live OCR cards and recommendations. '
            'These entries do not verify sales, production or profit.'
        ))
        self._scroll_area.setWidget(content)
        layout.addWidget(self._scroll_area, 1)
        self._status_label = self._label()
        layout.addWidget(self._status_label)
        buttons = QDialogButtonBox()
        self._save_button = buttons.addButton('Save', QDialogButtonBox.ButtonRole.AcceptRole)
        self._cancel_button = buttons.addButton('Cancel', QDialogButtonBox.ButtonRole.RejectRole)
        self._save_button.setDefault(True)
        self._save_button.clicked.connect(self._save)
        self._cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _draft(self):
        # QTextDocument.toPlainText converts NBSP to space; raw text preserves it.
        note = self._note_edit.document().toRawText().replace('\u2029', '\n')
        return self._stock_edit.text(), self._supply_edit.text(), self._value_edit.text(), note

    def _update_actions(self):
        enabled = not (self._busy or self._closed or self._confirming_discard)
        for control in (self._stock_edit, self._supply_edit, self._value_edit,
                        self._note_edit, self._save_button, self._cancel_button):
            control.setEnabled(enabled)

    def _save(self):
        if self._busy or self._closed or self._confirming_discard:
            return
        self._busy = True
        self._update_actions()
        try:
            stock, supply, value, note = self._draft()
            values = normalize_business_checkin(
                parse_checkin_percent(stock), parse_checkin_percent(supply),
                parse_checkin_value(value), note,
            )
            record = self._repository.save_business_checkin(
                self._character_id, self._business_id,
                stock_percent=values[0], supply_percent=values[1],
                stock_value=values[2], note=values[3],
            )
        except BusinessCheckInValidationError as exc:
            self._status_label.setText(f'Check-in not saved: {exc} Your draft is still here.')
        except BusinessCheckInUnavailable:
            self._status_label.setText(
                'Check-in not saved. This saved character or storage is unavailable. '
                'Your draft is still here. Restore access and try Save again.'
            )
        except Exception as exc:
            logger.warning('Could not save manual check-in (%s)', type(exc).__name__)
            self._status_label.setText('Check-in not saved. Your draft is still here. Try Save again.')
        else:
            # Commit truth is set before observers run. A refresh failure cannot
            # turn a saved record into an editable draft that could be appended twice.
            self._closed = True
            self._status_label.setText(f'Saved manual check-in #{record.id}.')
            self.saved.emit(record)
            super().done(QDialog.DialogCode.Accepted)
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
                self, 'Discard unsaved check-in?', 'Discard your unsaved check-in and close?',
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            ) == QMessageBox.StandardButton.Discard
        finally:
            self._confirming_discard = False
            self._update_actions()

    def done(self, result):
        # Only _save may accept; external done/accept must not bypass draft protection.
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
