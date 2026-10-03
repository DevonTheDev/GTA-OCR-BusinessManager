"""Create a saved owner for manual records without starting a tracking session."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ...database.character_profiles import CharacterProfileError, normalize_character_name
from ...utils.logging import get_logger

logger = get_logger('ui.saved_character')


class SavedCharacterDialog(QDialog):
    """A modeless name draft, owned by one board and bound to its repository."""

    saved = pyqtSignal(object)

    def __init__(self, repository, parent=None):
        super().__init__(parent)
        self._repository = repository
        self._busy = False
        self._closed = False
        self._confirming_discard = False
        self._committed_result = None
        self.setWindowTitle('Add saved character')
        self.setModal(False)
        self.resize(580, 360)
        self._setup_ui()

    @staticmethod
    def _label(text=''):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return label

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        body.addWidget(self._label(
            'Create a saved character to own your manual records. '
            'You can record check-ins immediately, without starting tracking or OCR.'
        ))
        form = QFormLayout()
        self._name_edit = QLineEdit()
        self._name_edit.setObjectName('savedCharacterName')
        # A widget limit counts UTF-16 units and can turn invalid input into a
        # different valid name. Validate the complete Unicode draft instead.
        self._name_edit.setMaxLength(2147483647)
        self._name_edit.setPlaceholderText('1–50 characters, on one line')
        form.addRow('Saved character name', self._name_edit)
        body.addLayout(form)
        body.addWidget(self._label(
            'Spaces around the name are removed. If exactly one saved character '
            'already has this exact name, that character will be selected.'
        ))
        body.addWidget(self._label(
            'The character name used when starting tracking is configured '
            'separately in Settings.'
        ))
        body.addStretch()
        self._scroll_area.setWidget(content)
        layout.addWidget(self._scroll_area, 1)
        self._status_label = self._label()
        layout.addWidget(self._status_label)
        buttons = QDialogButtonBox()
        self._save_button = buttons.addButton('Create and select', QDialogButtonBox.ButtonRole.AcceptRole)
        self._cancel_button = buttons.addButton('Cancel', QDialogButtonBox.ButtonRole.RejectRole)
        self._save_button.setDefault(True)
        self._save_button.clicked.connect(self._save)
        self._cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)
        self._name_edit.setFocus()

    def _update_actions(self):
        enabled = not (self._busy or self._closed or self._confirming_discard)
        for control in (self._name_edit, self._save_button, self._cancel_button):
            control.setEnabled(enabled)

    def _save(self):
        if self._busy or self._closed or self._confirming_discard:
            return
        self._busy = True
        self._update_actions()
        try:
            name = normalize_character_name(self._name_edit.text())
            result = self._repository.create_saved_character(name)
        except CharacterProfileError as exc:
            self._status_label.setText(f'Character not saved: {exc} Your draft is still here.')
        except Exception as exc:
            logger.warning('Could not save character (%s)', type(exc).__name__)
            self._status_label.setText('Character not saved. Your draft is still here. Try Create and select again.')
        else:
            # Commit and close truth precede callbacks. Board refresh failures
            # must never make this draft retryable after its row was committed.
            self._committed_result = result
            self._closed = True
            verb = 'Created saved character' if result.created else 'Reused existing saved character'
            self._status_label.setText(f'{verb} #{result.id} · {result.name}.')
            self.saved.emit(result)
            super().done(QDialog.DialogCode.Accepted)
        finally:
            self._busy = False
            self._update_actions()

    def _confirm_discard(self):
        if self._busy or self._closed or self._confirming_discard:
            return False
        if not self._name_edit.text():
            return True
        self._confirming_discard = True
        self._update_actions()
        try:
            return QMessageBox.question(
                self, 'Discard unsaved character?', 'Discard this unsaved character name and close?',
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            ) == QMessageBox.StandardButton.Discard
        finally:
            self._confirming_discard = False
            self._update_actions()

    def done(self, result):
        # Only a committed save can accept, even if callers invoke accept/done.
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
