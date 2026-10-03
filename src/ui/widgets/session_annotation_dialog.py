"""Edit personal context for one captured completed-session identity."""

from datetime import datetime, timezone

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ...database.session_annotations import (
    InvalidSessionAnnotation, SessionAnnotationConflict, SessionAnnotationUnavailable,
    normalize_annotation,
)
from ...utils.logging import get_logger

logger = get_logger('ui.session_annotation')


class SessionAnnotationDialog(QDialog):
    """A modeless draft that never changes its repository or session target."""

    # A Python int preserves SQLite IDs beyond Qt's signed 32-bit signal type.
    saved = pyqtSignal(object)

    def __init__(self, repository, session_id, *, character_name='', started_at=None, parent=None):
        super().__init__(parent)
        self._repository = repository
        self._session_id = session_id
        self._expected_revision = 0
        self._baseline = None
        self._saved_note = ''
        self._loaded = False
        self._can_save = False
        self._busy = False
        self.setWindowTitle(f'Edit session notes · Session #{session_id}')
        self.setModal(False)
        self.resize(640, 570)
        self._setup_ui(character_name, started_at)
        self._load_saved()

    @staticmethod
    def _label(text=''):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def _setup_ui(self, character_name, started_at):
        layout = QVBoxLayout(self)
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        if isinstance(started_at, datetime):
            if started_at.tzinfo is not None:
                started_at = started_at.astimezone(timezone.utc)
            started_at = started_at.strftime('%Y-%m-%d %H:%M:%S')
        self._context_label = self._label(
            f'Session #{self._session_id} · {character_name or "Unknown character"}\n'
            f'Started {started_at or "--"} UTC'
        )
        body.addWidget(self._context_label)
        body.addWidget(self._label(
            'Personal context saved with this completed session. These notes are shared by anyone '
            'using this database; they are not captured activity notes or verified game information.'
        ))
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._label_edit = QLineEdit()
        self._label_edit.setObjectName('sessionAnnotationLabel')
        self._label_edit.setPlaceholderText('Optional label · up to 80 characters')
        self._tags_edit = QLineEdit()
        self._tags_edit.setObjectName('sessionAnnotationTags')
        self._tags_edit.setPlaceholderText('crew, weekend, heist')
        # Do not use QLineEdit.maxLength: Qt counts UTF-16 units, while the
        # shared validator counts Unicode code points and never truncates drafts.
        form.addRow('Label', self._label_edit)
        form.addRow('Tags', self._tags_edit)
        body.addLayout(form)
        body.addWidget(self._label(
            'Separate tags with commas. Up to 8 tags, 32 characters each. Repeated tags are combined.'
        ))
        body.addWidget(self._label('Personal note · up to 4,000 characters'))
        self._note_edit = QPlainTextEdit()
        self._note_edit.setObjectName('sessionAnnotationNote')
        self._note_edit.setMinimumHeight(160)
        body.addWidget(self._note_edit, 1)
        self._scroll_area.setWidget(content)
        layout.addWidget(self._scroll_area, 1)
        draft_actions = QHBoxLayout()
        self._clear_button = QPushButton('Clear draft')
        self._reload_button = QPushButton('Reload saved notes')
        draft_actions.addWidget(self._clear_button)
        draft_actions.addWidget(self._reload_button)
        draft_actions.addStretch()
        layout.addLayout(draft_actions)
        self._status_label = self._label()
        layout.addWidget(self._status_label)
        buttons = QDialogButtonBox()
        self._save_button = buttons.addButton('Save', QDialogButtonBox.ButtonRole.AcceptRole)
        self._cancel_button = buttons.addButton('Cancel', QDialogButtonBox.ButtonRole.RejectRole)
        self._save_button.setDefault(True)
        layout.addWidget(buttons)
        self._save_button.clicked.connect(self._save)
        self._cancel_button.clicked.connect(self.reject)
        self._clear_button.clicked.connect(self._clear_draft)
        self._reload_button.clicked.connect(self._reload_saved)
        self._update_actions()

    def _draft(self):
        # Qt's toPlainText also converts NBSP to a normal space. Raw document
        # text preserves it; paragraph breaks represent ordinary newlines.
        note = self._note_edit.document().toRawText().replace('\u2029', '\n')
        return self._label_edit.text(), self._tags_edit.text(), note

    def _is_dirty(self):
        return self._baseline is not None and self._draft() != self._baseline

    def _update_actions(self):
        for field in (self._label_edit, self._tags_edit, self._note_edit):
            field.setEnabled(self._loaded and not self._busy)
        self._save_button.setEnabled(self._can_save and not self._busy)
        self._clear_button.setEnabled(self._loaded and not self._busy)
        self._reload_button.setEnabled(not self._busy)
        self._cancel_button.setEnabled(not self._busy)

    def _accept_snapshot(self, snapshot):
        self._label_edit.setText(snapshot.label if snapshot else '')
        self._tags_edit.setText(', '.join(snapshot.tags) if snapshot else '')
        self._saved_note = snapshot.note if snapshot else ''
        self._note_edit.setPlainText(self._saved_note)
        self._expected_revision = snapshot.revision if snapshot else 0
        self._baseline = self._draft()
        self._loaded = self._can_save = True

    def _load_saved(self):
        if self._busy:
            return
        self._busy = True
        self._update_actions()
        try:
            snapshot = self._repository.get_session_annotation(self._session_id)
            self._accept_snapshot(snapshot)
            self._status_label.setText('Saved notes loaded.' if snapshot else 'No saved notes yet. Add optional context and Save.')
        except InvalidSessionAnnotation:
            self._can_save = False
            self._status_label.setText(
                'Saved notes are unavailable because their stored data is invalid. '
                'They have not been replaced. Recorded session history is still available.'
            )
        except SessionAnnotationUnavailable:
            self._can_save = False
            self._status_label.setText('This completed session is unavailable. Notes cannot be edited.')
        except Exception as exc:
            logger.warning('Could not load notes for session %s (%s)', self._session_id, type(exc).__name__)
            self._can_save = False
            self._status_label.setText('Saved notes could not be loaded. Your draft is unchanged. Use Reload saved notes to retry.')
        finally:
            self._busy = False
            self._update_actions()

    def _confirm_discard(self, action='close'):
        if not self._is_dirty():
            return True
        return QMessageBox.question(
            self, 'Discard unsaved session notes?',
            'Discard your unsaved draft and reload the saved notes?' if action == 'reload'
            else 'Discard your unsaved session notes and close?',
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Discard

    def _reload_saved(self):
        if not self._busy and self._confirm_discard('reload'):
            self._load_saved()

    def _clear_draft(self):
        if self._busy or not self._loaded:
            return
        self._label_edit.clear()
        self._tags_edit.clear()
        self._note_edit.clear()
        self._status_label.setText('Draft cleared. Save to clear the saved context, or Cancel to keep it.')

    def _save(self):
        if self._busy or not self._can_save:
            return
        self._busy = True
        self._update_actions()
        try:
            label, tags_text, note = self._draft()
            if note == self._baseline[2]:
                # Merely opening a note or editing another field must preserve
                # its original line-ending and Unicode separator spelling.
                note = self._saved_note
            tags = tags_text.split(',') if tags_text.strip() else ()
            label, tags, note = normalize_annotation(label, tags, note)
            snapshot = self._repository.save_session_annotation(
                self._session_id, label, tags, note, self._expected_revision,
            )
            self._accept_snapshot(snapshot)
            self._status_label.setText(f'Saved notes for session #{self._session_id}.')
            self.saved.emit(self._session_id)
            super().accept()
        except SessionAnnotationConflict:
            self._status_label.setText(
                'Saved notes changed in another editor. Your draft was not saved and is still here. '
                'Use Reload saved notes to review the latest version before editing again.'
            )
        except InvalidSessionAnnotation:
            self._can_save = False
            self._status_label.setText('Saved notes are unavailable because their stored data is invalid. Your draft was not saved and is still here.')
        except SessionAnnotationUnavailable:
            self._can_save = False
            self._status_label.setText('This completed session is unavailable. Your draft was not saved and is still here.')
        except (TypeError, ValueError) as exc:
            self._status_label.setText(f'Invalid notes: {exc}. Your draft was not saved.')
        except Exception as exc:
            logger.warning('Could not save notes for session %s (%s)', self._session_id, type(exc).__name__)
            self._status_label.setText('Notes were not saved. Your draft is still here. Try Save again, or reload the saved notes.')
        finally:
            self._busy = False
            self._update_actions()

    def reject(self):
        if not self._busy and self._confirm_discard():
            super().reject()

    def closeEvent(self, event):
        if self._busy or not self._confirm_discard():
            event.ignore()
            return
        super().reject()
        event.accept()
