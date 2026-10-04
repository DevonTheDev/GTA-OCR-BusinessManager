"""Append one manual observation to a fixed saved character and business."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
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

    def __init__(self, repository, character_id, business_id, parent=None, *,
                 character_name='', live_reading_provider=None):
        super().__init__(parent)
        self._repository = repository
        self._character_id = character_id
        self._business_id = business_id
        self._character_name = character_name
        self._live_reading_provider = live_reading_provider
        self._live_preview = None
        self._busy = False
        self._closed = False
        self._confirming_discard = False
        self.setWindowTitle('Record manual business check-in')
        self.setModal(False)
        self.resize(620, 530)
        self._setup_ui(character_name)
        self._update_actions()

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
        self._preview_live_button = QPushButton('Preview live values…')
        self._preview_live_button.setObjectName('businessCheckInPreviewLive')
        self._preview_live_button.setAutoDefault(False)
        self._preview_live_button.setToolTip(
            'Review the last live reading before choosing whether to copy its values into this draft.'
            if callable(self._live_reading_provider) else
            'Live previews are unavailable here. You can still enter and save a manual check-in.'
        )
        self._preview_live_button.clicked.connect(self._preview_live_values)
        body.addWidget(self._preview_live_button)
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
        self._preview_live_button.setEnabled(enabled and callable(self._live_reading_provider))

    def _preview_live_values(self):
        if (self._busy or self._closed or self._confirming_discard
                or not callable(self._live_reading_provider)):
            return
        self._busy = True
        self._update_actions()
        preview = None
        try:
            from ...game.live_business_snapshot import (
                LiveBusinessReadingSnapshot, create_live_business_reading_snapshot,
            )
            from .business_checkin_live_preview import BusinessCheckInLivePreview

            supplied = self._live_reading_provider(self._business_id)
            if supplied is None:
                self._status_label.setText(
                    'No live reading is available for this business. Your draft is unchanged. '
                    'Read or enter live values, then try again.'
                )
                return
            if (type(supplied) is not LiveBusinessReadingSnapshot
                    or supplied.business_id != self._business_id
                    or type(supplied.uses_supplies) is not bool):
                raise ValueError('Invalid live reading snapshot')
            # Revalidate a provider's detached result, never fall back to mutable
            # app state or computed recommendations. Keep our own captured copy.
            snapshot = create_live_business_reading_snapshot(
                self._business_id, stock_percent=supplied.stock_percent,
                supply_percent=supplied.supply_percent, stock_value=supplied.stock_value,
                updated_at=supplied.updated_at, identity_source=supplied.identity_source,
                captured_at=supplied.captured_at,
            )
            if snapshot != supplied:
                raise ValueError('Invalid live reading metadata')
            preview = BusinessCheckInLivePreview(
                snapshot, self._character_id, self._character_name, parent=self,
            )
            self._live_preview = preview
            accepted = preview.exec() == QDialog.DialogCode.Accepted
        except Exception as exc:
            logger.warning('Could not preview live check-in values (%s)', type(exc).__name__)
            self._status_label.setText(
                'Live values could not be previewed. Your draft is unchanged. '
                'Try Preview live values again.'
            )
        else:
            if accepted:
                self._copy_live_values(snapshot)
        finally:
            # A finished signal can run inside exec(). Retire only after that
            # nested modal loop unwinds, while the editor still owns its guard.
            self._live_preview = None
            if preview is not None:
                preview.deleteLater()
            self._busy = False
            self._update_actions()

    def _copy_live_values(self, snapshot):
        # Ownership covers textChanged handlers and restoration too: Save,
        # reentry and parent closure cannot observe a partially replaced draft.
        controls = (self._stock_edit, self._supply_edit, self._value_edit)
        previous = tuple(control.text() for control in controls)
        values = (snapshot.stock_percent, snapshot.supply_percent, snapshot.stock_value)
        try:
            for control, value in zip(controls, values):
                control.setText('' if value is None else str(value))
        except Exception as exc:
            logger.warning('Could not copy live check-in values (%s)', type(exc).__name__)
            for control, value in zip(controls, previous):
                try:
                    if control.text() != value:
                        control.setText(value)
                except Exception as restore_error:
                    logger.warning('Could not restore check-in measurement (%s)', type(restore_error).__name__)
            restored = tuple(control.text() for control in controls) == previous
            self._status_label.setText(
                'Live values could not be copied. Your draft is unchanged. '
                'Try Preview live values again.' if restored else
                'Live values could not be fully copied. Review your draft measurements '
                'before saving. Your personal note is unchanged. Try Preview live values again.'
            )
        else:
            self._status_label.setText(
                'Live values copied into this draft. Your personal note is unchanged. '
                'Review the values and press Save to record the check-in.'
            )

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
