"""Review a fixed live capture before atomically saving selected manual check-ins."""

from datetime import datetime, timedelta

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ...database.business_checkins import (
    BUSINESS_LABELS, BusinessCheckIn, BusinessCheckInDataError, BusinessCheckInUnavailable,
    BusinessCheckInValidationError, business_label, normalize_business_checkin, validate_checkin_id,
)
from ...database.business_checkin_batches import BusinessCheckInBatchUncertain, BusinessCheckInDraft
from ...game.live_business_snapshot import (
    LiveBusinessReadingSnapshot, create_live_business_reading_snapshot,
)
from ...utils.logging import get_logger
from .business_checkin_live_preview import _observed_time, _SOURCE_LABELS

logger = get_logger('ui.business_checkin_batch_editor')


def validate_live_snapshots(supplied):
    """Reject an entire malformed capture; never silently repair or omit a row."""
    if type(supplied) is not tuple or len(supplied) > len(BUSINESS_LABELS):
        raise ValueError('Invalid live reading collection')
    catalog = tuple(BUSINESS_LABELS)
    snapshots = []
    previous = -1
    captured_at = None
    for item in supplied:
        if (type(item) is not LiveBusinessReadingSnapshot
                or type(item.business_id) is not str or item.business_id not in BUSINESS_LABELS
                or type(item.uses_supplies) is not bool):
            raise ValueError('Invalid live reading snapshot')
        position = catalog.index(item.business_id)
        if position <= previous:
            raise ValueError('Live readings must have unique catalog order')
        snapshot = create_live_business_reading_snapshot(
            item.business_id, stock_percent=item.stock_percent, supply_percent=item.supply_percent,
            stock_value=item.stock_value, updated_at=item.updated_at,
            identity_source=item.identity_source, captured_at=item.captured_at,
        )
        if snapshot != item:
            raise ValueError('Invalid live reading metadata')
        if snapshots and snapshot.captured_at != captured_at:
            raise ValueError('Live readings require one common capture time')
        captured_at = snapshot.captured_at
        previous = position
        snapshots.append(snapshot)
    return tuple(snapshots)


class BusinessCheckInBatchEditor(QDialog):
    """A modeless review whose observations, repository and saved owner stay fixed."""

    saved = pyqtSignal(object)

    def __init__(self, repository, character_id, snapshots, parent=None, *, character_name=''):
        super().__init__(parent)
        validate_checkin_id(character_id)
        self._snapshots = validate_live_snapshots(snapshots)
        self._repository = repository
        self._character_id = character_id
        self._character_name = character_name
        self._busy = False
        self._closed = False
        self._committed = False
        self._uncertain = False
        self._confirming_discard = False
        self.setWindowTitle('Review live check-ins')
        self.setModal(False)
        self.resize(720, 700)
        self._setup_ui()
        self._update_actions()

    @staticmethod
    def _label(text=''):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        label.setSizePolicy(policy)
        return label

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        self._context_label = self._label(
            f'Saved character: {self._character_name or "Saved character"} (#{self._character_id})'
        )
        body.addWidget(self._context_label)
        self._help_label = self._label(
            'Live readings are not character-tagged and have no freshness guarantee. '
            'Verify each selected business and value belongs to the saved character above. '
            'This capture stays fixed when tracking, settings or the selected character changes.\n\n'
            'Select only the readings you want to record. Values cannot be edited here; '
            'use Record check-in for corrections. Save appends manual check-ins together '
            'with one new UTC save time. Source and capture times are shown for review '
            'and are not stored in the check-ins.'
        )
        body.addWidget(self._help_label)
        self._availability_label = self._label(
            f'{len(self._snapshots)} available · {len(BUSINESS_LABELS) - len(self._snapshots)} missing '
            f'from {len(BUSINESS_LABELS)} catalog businesses. Missing readings are omitted.'
        )
        body.addWidget(self._availability_label)
        self._captured_label = self._label(
            f'Captured together: {self._snapshots[0].captured_at.isoformat(sep=" ", timespec="microseconds")} UTC'
            if self._snapshots else 'No capture timestamp is available for this empty collection.'
        )
        body.addWidget(self._captured_label)
        self._empty_label = self._label(
            'No live readings are available. Close this review, read or enter live values, '
            'then open Review live check-ins again.' if not self._snapshots else ''
        )
        body.addWidget(self._empty_label)
        self.row_checks = {}
        self.row_labels = {}
        self._row_checks = self.row_checks
        self._row_labels = self.row_labels
        for snapshot in self._snapshots:
            check = QCheckBox(f'{business_label(snapshot.business_id)} · {snapshot.business_id}')
            check.setObjectName(f'businessBatchSelect_{snapshot.business_id}')
            check.setAccessibleName(f'Select {business_label(snapshot.business_id)} for manual check-in')
            check.setChecked(False)
            check.toggled.connect(self._update_actions)
            self.row_checks[snapshot.business_id] = check
            body.addWidget(check)
            form = QFormLayout()
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            source = _SOURCE_LABELS.get(snapshot.identity_source, 'Source not recorded')
            if snapshot.identity_source:
                source += f' · {snapshot.identity_source}'
            labels = {
                'stock': self._label('Unknown' if snapshot.stock_percent is None else f'{snapshot.stock_percent}%'),
                'supply': self._label('N/A' if not snapshot.uses_supplies else
                                      'Unknown' if snapshot.supply_percent is None else f'{snapshot.supply_percent}%'),
                'value': self._label('Unknown' if snapshot.stock_value is None else f'${snapshot.stock_value}'),
                'source': self._label(source), 'updated': self._label(_observed_time(snapshot.updated_at)),
            }
            for title, key in (('Observed stock', 'stock'), ('Observed supplies', 'supply'),
                               ('Observed stock value', 'value'), ('Reading source', 'source'),
                               ('Live reading updated', 'updated')):
                labels[key].setAccessibleName(f'{business_label(snapshot.business_id)} {title}')
                title_label = self._label(title)
                # Form titles need a width hint; Ignored is only appropriate for
                # the wrapping context and values, not QFormLayout's label column.
                policy = title_label.sizePolicy()
                policy.setHorizontalPolicy(QSizePolicy.Policy.Preferred)
                title_label.setSizePolicy(policy)
                form.addRow(title_label, labels[key])
            self.row_labels[snapshot.business_id] = labels
            body.addLayout(form)
        body.addWidget(self._label(
            'Shared personal note · optional, up to 2,000 characters. '
            'Copied unchanged to every selected check-in.'
        ))
        self.note_edit = self._note_edit = QPlainTextEdit()
        self.note_edit.setObjectName('businessBatchNote')
        self.note_edit.setAccessibleName('Shared personal note copied to every selected check-in')
        self.note_edit.setMinimumHeight(120)
        body.addWidget(self.note_edit)
        body.addStretch()
        self._scroll_area.setWidget(content)
        layout.addWidget(self._scroll_area, 1)
        selection = QHBoxLayout()
        self.select_all_button = self._select_all_button = QPushButton('Select all')
        self.clear_selection_button = self._clear_selection_button = QPushButton('Clear selection')
        self.selection_label = self._selection_label = self._label('0 selected')
        for button in (self.select_all_button, self.clear_selection_button):
            button.setAutoDefault(False)
            selection.addWidget(button)
        selection.addWidget(self.selection_label, 1)
        self.select_all_button.clicked.connect(lambda: self._select_all(True))
        self.clear_selection_button.clicked.connect(lambda: self._select_all(False))
        layout.addLayout(selection)
        self._status_label = self._label()
        layout.addWidget(self._status_label)
        buttons = QDialogButtonBox()
        self.save_button = self._save_button = buttons.addButton(
            'Save selected check-ins (0)', QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._cancel_button = buttons.addButton('Cancel', QDialogButtonBox.ButtonRole.RejectRole)
        self._cancel_button.setDefault(True)
        self.save_button.setAutoDefault(False)
        self.save_button.clicked.connect(self._save)
        self._cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _note(self):
        return self.note_edit.document().toRawText().replace('\u2029', '\n')

    def _selected_snapshots(self):
        return tuple(item for item in self._snapshots if self.row_checks[item.business_id].isChecked())

    def _owner_closing(self):
        owner = self.parent()
        return owner is not None and (getattr(owner, '_closing', False) or getattr(owner, '_closed', False))

    def _update_actions(self):
        selected = len(self._selected_snapshots())
        enabled = not (self._busy or self._closed or self._uncertain or self._confirming_discard
                       or self._owner_closing())
        for control in (*self.row_checks.values(), self.note_edit):
            control.setEnabled(enabled)
        self.select_all_button.setEnabled(enabled and selected < len(self._snapshots))
        self.clear_selection_button.setEnabled(enabled and selected > 0)
        self.selection_label.setText(f'{selected} selected')
        self.save_button.setText(f'Save selected check-ins ({selected})')
        self.save_button.setEnabled(enabled and selected > 0)
        self._cancel_button.setText('Close' if self._uncertain else 'Cancel')
        self._cancel_button.setEnabled(not (self._busy or self._closed or self._confirming_discard))

    def _select_all(self, selected):
        if self._busy or self._closed or self._uncertain or self._confirming_discard:
            return
        for check in self.row_checks.values():
            check.setChecked(selected)

    def _validate_saved(self, records, drafts):
        if type(records) is not tuple or len(records) != len(drafts):
            raise ValueError('Invalid batch save result')
        ids = set()
        recorded_at = None
        for record, draft in zip(records, drafts):
            if type(record) is not BusinessCheckIn:
                raise ValueError('Invalid saved check-in')
            validate_checkin_id(record.id)
            if (type(record.id) is not int or record.id in ids
                    or type(record.character_id) is not int or record.character_id != self._character_id
                    or type(record.business_id) is not str or record.business_id != draft.business_id):
                raise ValueError('Saved check-in target mismatch')
            values = normalize_business_checkin(
                record.stock_percent, record.supply_percent, record.stock_value, record.note,
            )
            if values != (draft.stock_percent, draft.supply_percent, draft.stock_value, draft.note):
                raise ValueError('Saved check-in values mismatch')
            if (type(record.recorded_at) is not datetime or record.recorded_at.utcoffset() != timedelta(0)
                    or (ids and record.recorded_at != recorded_at)):
                raise ValueError('Saved check-in time mismatch')
            ids.add(record.id)
            recorded_at = record.recorded_at

    def _mark_uncertain(self):
        self._uncertain = True
        self._status_label.setText(
            'These check-ins may already be saved. Inspect saved history before another attempt. '
            'Saving and editing are disabled for this review. You can close it.'
        )

    def _save(self):
        if (self._busy or self._closed or self._uncertain or self._confirming_discard
                or self._owner_closing()):
            return
        snapshots = self._selected_snapshots()
        if not snapshots:
            return
        self._busy = True
        self._update_actions()
        try:
            # Complete every local validation before making the single write call.
            try:
                note = self._note()
                drafts = tuple(BusinessCheckInDraft(item.business_id, *normalize_business_checkin(
                    item.stock_percent, item.supply_percent, item.stock_value, note,
                )) for item in snapshots)
            except BusinessCheckInValidationError as exc:
                self._status_label.setText(f'Check-ins not saved: {exc} Your selection and note are still here.')
                return
            try:
                records = self._repository.save_business_checkins(self._character_id, drafts)
            except BusinessCheckInBatchUncertain:
                self._mark_uncertain()
                return
            except (BusinessCheckInValidationError, BusinessCheckInUnavailable, BusinessCheckInDataError):
                self._status_label.setText(
                    'Check-ins not saved. Your selection and note are still here. '
                    'Restore access to the saved character or storage, then try Save again.'
                )
                return
            except Exception as exc:
                logger.warning('Batch save outcome unavailable (%s)', type(exc).__name__)
                self._mark_uncertain()
                return
            # Validation exceptions after the call cannot imply a failed write.
            try:
                self._validate_saved(records, drafts)
            except Exception as exc:
                logger.warning('Batch save result could not be verified (%s)', type(exc).__name__)
                self._mark_uncertain()
                return
            self._committed = True
            self._closed = True
            self._status_label.setText(f'Saved {len(records)} check-ins for character #{self._character_id}.')
            try:
                self.saved.emit(records)
            except Exception as exc:
                logger.warning('Could not refresh after saved check-ins (%s)', type(exc).__name__)
            finally:
                super().done(QDialog.DialogCode.Accepted)
        finally:
            self._busy = False
            self._update_actions()

    def _confirm_discard(self):
        if self._busy or self._closed or self._confirming_discard:
            return False
        if self._uncertain or (not self._selected_snapshots() and not self._note()):
            return True
        self._confirming_discard = True
        self._update_actions()
        try:
            return QMessageBox.question(
                self, 'Discard unsaved check-ins?',
                'Discard the selected check-ins and shared note, and close this review?',
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            ) == QMessageBox.StandardButton.Discard
        finally:
            self._confirming_discard = False
            self._update_actions()

    def done(self, result):
        if self._confirm_discard():
            self._closed = True
            self._update_actions()
            super().done(QDialog.DialogCode.Rejected)

    def reject(self):
        self.done(QDialog.DialogCode.Rejected)

    def close(self):
        # Qt can treat recursive QWidget.close() during a closeEvent as already
        # accepted, without sending another event. Guard before entering Qt.
        if self._busy or self._confirming_discard:
            return False
        return super().close()

    def closeEvent(self, event):
        if not self._confirm_discard():
            event.ignore()
            return
        self._closed = True
        self._update_actions()
        super().done(QDialog.DialogCode.Rejected)
        event.accept()
