"""Browse and export detached, explicitly recorded manual business observations."""

from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import QDate, QEvent, QSignalBlocker, Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ...database.business_checkins import (
    BUSINESS_LABELS, BusinessCheckInHistoryFilters, BusinessCheckInValidationError,
    business_label, validate_business_checkin_history_filters,
)
from ...database.business_pins import BusinessPinLimitError
from ...utils.exporter import DataExporter
from ...utils.logging import get_logger

logger = get_logger('ui.business_checkins')


def _text(value):
    return '--' if value is None else str(value)


def _timestamp(value):
    if value is None:
        return '--'
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.strftime('%Y-%m-%d %H:%M:%S')


class BusinessCheckInsDialog(QDialog):
    """An explicit character board with fixed-target modeless append editor."""

    PAGE_SIZE = 25

    def __init__(self, repository, parent=None, *, character_id=None):
        super().__init__(parent)
        self._repository = repository
        self._exporter = DataExporter(repository)
        self._board = None
        self._pins = None
        self._page = None
        self._editor = None
        self._opening_editor = False
        self._creator = None
        self._opening_creator = False
        self._saved_character_result = None
        self._pending_character_id = None
        self._busy = False
        self._refresh_after_busy = False
        self._exporting = False
        self._closed = False
        self._closing = False
        self._generation = 0
        self._history_generation = 0
        self._history_context = None
        self._history_filters = None
        self._history_dirty = False
        self._history_offset = 0
        self.setWindowTitle('Manual business check-ins')
        self.setModal(False)
        self.resize(1050, 800)
        self._setup_ui()
        self.refresh(character_id=character_id, initial=True)

    @staticmethod
    def _label(text=''):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    @staticmethod
    def _table(headings):
        table = QTableWidget(0, len(headings))
        table.setHorizontalHeaderLabels(headings)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(self._label(
            'Manual observations for a saved character. Blank fields are unknown; zero is known. '
            'Entries stand alone, separate from live OCR cards and recommendations.'
        ))
        controls = QHBoxLayout()
        controls.addWidget(self._label('Character'))
        self._character_combo = QComboBox()
        self._character_combo.setMinimumContentsLength(18)
        self._character_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        controls.addWidget(self._character_combo, 1)
        self._refresh_button = QPushButton('Refresh')
        self._add_character_button = QPushButton('Add saved character…')
        self._record_button = QPushButton('Record check-in…')
        controls.addWidget(self._refresh_button)
        controls.addWidget(self._add_character_button)
        controls.addWidget(self._record_button)
        layout.addLayout(controls)
        pin_controls = QHBoxLayout()
        self._pin_button = QPushButton('Pin selected business')
        self._pin_scope_label = self._label(
            'Personal pins put businesses first on this manual board only. '
            'All businesses and saved check-ins stay visible.'
        )
        pin_label_policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        pin_label_policy.setHeightForWidth(True)
        self._pin_scope_label.setSizePolicy(pin_label_policy)
        pin_controls.addWidget(self._pin_button)
        pin_controls.addWidget(self._pin_scope_label, 1)
        layout.addLayout(pin_controls)
        self._pin_status_label = self._label()
        self._pin_status_label.setSizePolicy(pin_label_policy)
        layout.addWidget(self._pin_status_label)
        self._context_label = self._label()
        self._context_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._context_label)

        splitter = QSplitter(Qt.Orientation.Vertical)
        board_area = QWidget()
        board_layout = QVBoxLayout(board_area)
        board_layout.setContentsMargins(0, 0, 0, 0)
        board_layout.addWidget(self._label('Latest saved check-in per business · -- means never recorded or unknown'))
        self._businesses_table = self._table(['Business', 'Stock %', 'Supplies %', 'Stock value ($)', 'Recorded (UTC)', 'Personal note'])
        for column, width in enumerate((180, 75, 85, 160, 165, 240)):
            self._businesses_table.setColumnWidth(column, width)
        board_layout.addWidget(self._businesses_table)
        splitter.addWidget(board_area)

        history_area = QWidget()
        history_layout = QVBoxLayout(history_area)
        history_layout.setContentsMargins(0, 0, 0, 0)
        self._history_label = self._label('Select a business to browse its saved check-ins')
        history_layout.addWidget(self._history_label)
        filters = QHBoxLayout()
        filters.addWidget(self._label('Note contains'))
        self._history_note_filter = QLineEdit()
        # Qt counts UTF-16 units. Validate the complete draft as Python Unicode
        # on Apply, instead of clipping astral characters with setMaxLength(200).
        self._history_note_filter.setMaxLength(2_147_483_647)
        self._history_note_filter.setPlaceholderText('Literal phrase (up to 200 characters)')
        self._history_note_filter.setMinimumWidth(80)
        self._history_note_filter.setAccessibleName('History note contains')
        self._history_note_filter.installEventFilter(self)
        filters.addWidget(self._history_note_filter, 1)
        self._history_from_enabled = QCheckBox('From UTC')
        self._history_until_enabled = QCheckBox('Until UTC')
        self._history_from_date = QDateEdit()
        self._history_until_date = QDateEdit()
        for toggle, control in ((self._history_from_enabled, self._history_from_date),
                                (self._history_until_enabled, self._history_until_date)):
            control.setDisplayFormat('yyyy-MM-dd')
            control.setDateRange(QDate(1, 1, 1), QDate(9999, 12, 31))
            control.setDate(QDate(datetime.now(timezone.utc).date()))
            control.setCalendarPopup(True)
            control.setAccessibleName(f'History recorded {toggle.text()} date')
            filters.addWidget(toggle)
            filters.addWidget(control)
        self._history_apply_button = QPushButton('Apply')
        self._history_clear_button = QPushButton('Clear')
        filters.addWidget(self._history_apply_button)
        filters.addWidget(self._history_clear_button)
        history_layout.addLayout(filters)
        self._history_filter_status = self._label()
        self._history_filter_status.setSizePolicy(pin_label_policy)
        history_layout.addWidget(self._history_filter_status)
        self._update_history_filter_status()
        self._history_table = self._table(['Check-in', 'Recorded (UTC)', 'Stock %', 'Supplies %', 'Stock value ($)'])
        for column, width in enumerate((95, 180, 95, 100, 170)):
            self._history_table.setColumnWidth(column, width)
        history_layout.addWidget(self._history_table)
        paging = QHBoxLayout()
        self._previous_button = QPushButton('Previous')
        self._next_button = QPushButton('Next')
        self._page_label = self._label('No history loaded')
        paging.addWidget(self._previous_button)
        paging.addWidget(self._page_label, 1)
        paging.addWidget(self._next_button)
        history_layout.addLayout(paging)
        history_layout.addWidget(self._label('Selected check-in · full personal note'))
        self._note_edit = QPlainTextEdit()
        self._note_edit.setReadOnly(True)
        self._note_edit.setPlaceholderText('Select a saved check-in to read its full note.')
        self._note_edit.setMinimumHeight(55)
        self._note_edit.setMaximumHeight(100)
        history_layout.addWidget(self._note_edit)
        splitter.addWidget(history_area)
        splitter.setSizes([260, 340])
        layout.addWidget(splitter, 1)
        self._saved_notice_label = self._label()
        self._saved_notice_label.hide()
        layout.addWidget(self._saved_notice_label)
        self._character_saved_notice_label = self._label()
        self._character_saved_notice_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._character_saved_notice_label.hide()
        layout.addWidget(self._character_saved_notice_label)
        self._pin_saved_notice_label = self._label()
        self._pin_saved_notice_label.setSizePolicy(pin_label_policy)
        self._pin_saved_notice_label.hide()
        layout.addWidget(self._pin_saved_notice_label)
        self._status_label = self._label()
        layout.addWidget(self._status_label)
        actions = QHBoxLayout()
        self._export_board_button = QPushButton('Export displayed board…')
        self._export_history_button = QPushButton('Export displayed history page…')
        actions.addWidget(self._export_board_button)
        actions.addWidget(self._export_history_button)
        actions.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)
        self._character_combo.currentIndexChanged.connect(self._character_changed)
        self._businesses_table.itemSelectionChanged.connect(self._business_changed)
        self._history_table.itemSelectionChanged.connect(self._history_changed)
        self._refresh_button.clicked.connect(self.refresh)
        self._add_character_button.clicked.connect(self._open_creator)
        self._record_button.clicked.connect(self._open_editor)
        self._pin_button.clicked.connect(self._set_selected_business_pin)
        self._previous_button.clicked.connect(self._previous_page)
        self._next_button.clicked.connect(self._next_page)
        self._export_board_button.clicked.connect(self._export_board)
        self._export_history_button.clicked.connect(self._export_history)
        self._history_note_filter.textChanged.connect(self._history_filter_edited)
        self._history_from_enabled.toggled.connect(self._history_filter_edited)
        self._history_until_enabled.toggled.connect(self._history_filter_edited)
        self._history_from_date.dateChanged.connect(self._history_filter_edited)
        self._history_until_date.dateChanged.connect(self._history_filter_edited)
        self._history_apply_button.clicked.connect(self._apply_history_filters)
        self._history_clear_button.clicked.connect(self._clear_history_filters)
        self._update_actions()

    def eventFilter(self, watched, event):
        if (watched is self._history_note_filter and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            # Consume Return here so QDialog cannot also activate its current
            # default button (for example Refresh) after applying the draft.
            self._apply_history_filters()
            return True
        return super().eventFilter(watched, event)

    def _update_actions(self):
        available = not (self._busy or self._closed or self._closing)
        self._character_combo.setEnabled(available)
        self._refresh_button.setEnabled(available)
        self._add_character_button.setEnabled(available and not self._exporting and not self._opening_creator)
        self._record_button.setEnabled(available and self._board is not None and self._selected_business_id() in BUSINESS_LABELS)
        business = self._selected_business_id()
        pins_current = (self._board is not None and self._pins is not None
                        and self._pins.character_id == self._board.character_id
                        and self._character_combo.currentData() == self._board.character_id)
        pinned = pins_current and business in self._pins.business_ids
        self._pin_button.setText('Unpin selected business' if pinned else 'Pin selected business')
        self._pin_button.setEnabled(
            available and not self._exporting and pins_current and (pinned or business in BUSINESS_LABELS)
        )
        self._previous_button.setEnabled(available and self._page is not None and self._page.offset > 0)
        self._next_button.setEnabled(available and self._page is not None and self._page.has_more)
        self._export_board_button.setEnabled(available and not self._exporting and self._board is not None)
        self._export_history_button.setEnabled(available and not self._exporting and self._page is not None)
        can_filter = (available and self._board is not None and business is not None
                      and self._character_combo.currentData() == self._board.character_id)
        self._history_note_filter.setEnabled(can_filter)
        self._history_from_enabled.setEnabled(can_filter)
        self._history_until_enabled.setEnabled(can_filter)
        self._history_from_date.setEnabled(can_filter and self._history_from_enabled.isChecked())
        self._history_until_date.setEnabled(can_filter and self._history_until_enabled.isChecked())
        self._history_apply_button.setEnabled(can_filter)
        self._history_clear_button.setEnabled(can_filter)

    def _update_history_filter_status(self, message=None):
        if message is None:
            if self._history_dirty:
                message = 'Filters changed. Apply to load history, or Clear.'
            elif self._history_filters is None:
                message = 'Applied: all saved check-ins for this business.'
            else:
                accepted = self._history_filters
                parts = []
                if accepted.note_query is not None:
                    query = accepted.note_query
                    preview = query[:36] + '…' if len(query) > 36 else query
                    parts.append(f'note contains “{preview}”')
                if accepted.recorded_from is not None:
                    parts.append(f'from {accepted.recorded_from.isoformat()}')
                if accepted.recorded_until is not None:
                    parts.append(f'until {accepted.recorded_until.isoformat()}')
                message = 'Applied: ' + '; '.join(parts) + '.'
        self._history_filter_status.setText(
            message + '\nLiteral notes: ASCII letters ignore case; other Unicode is exact. Dates include both UTC bounds.'
        )

    def _reset_history_filters(self, context=None):
        self._history_generation += 1
        self._history_context = context
        self._history_filters = None
        self._history_dirty = False
        self._history_offset = 0
        controls = (self._history_note_filter, self._history_from_enabled, self._history_until_enabled,
                    self._history_from_date, self._history_until_date)
        blockers = [QSignalBlocker(control) for control in controls]
        self._history_note_filter.clear()
        self._history_from_enabled.setChecked(False)
        self._history_until_enabled.setChecked(False)
        today = QDate(datetime.now(timezone.utc).date())
        self._history_from_date.setDate(today)
        self._history_until_date.setDate(today)
        del blockers
        self._update_history_filter_status()

    def _history_filter_edited(self, *_):
        self._history_generation += 1
        self._history_dirty = True
        self._retire_history()
        self._update_history_filter_status()

    def _history_action_available(self):
        return (not (self._busy or self._closed or self._closing) and self._board is not None
                and self._selected_business_id() is not None
                and self._character_combo.currentData() == self._board.character_id)

    def _apply_history_filters(self):
        if not self._history_action_available():
            return
        try:
            accepted = validate_business_checkin_history_filters(BusinessCheckInHistoryFilters(
                note_query=self._history_note_filter.text(),
                recorded_from=self._history_from_date.date().toPyDate() if self._history_from_enabled.isChecked() else None,
                recorded_until=self._history_until_date.date().toPyDate() if self._history_until_enabled.isChecked() else None,
            ))
        except BusinessCheckInValidationError as exc:
            self._retire_history()
            self._history_dirty = True
            self._update_history_filter_status(f'Invalid filters. {exc}')
            return
        self._history_generation += 1
        self._history_filters = accepted
        self._history_dirty = False
        self._history_offset = 0
        self._read_applied_history()

    def _clear_history_filters(self):
        if not self._history_action_available():
            return
        self._reset_history_filters(self._history_context)
        self._read_applied_history()

    def _read_applied_history(self):
        self._busy = True
        self._update_actions()
        try:
            self._load_history(0, self._generation)
        except Exception as exc:
            self._show_history_error(exc)
        finally:
            self._finish_read()

    def _show_history_error(self, error):
        logger.warning('Could not load filtered manual business history (%s)', type(error).__name__)
        self._retire_history()
        self._update_history_filter_status(
            'History could not be loaded. Apply again or use Refresh to retry the accepted filters.'
        )

    def _retire_history(self):
        self._page = None
        with QSignalBlocker(self._history_table):
            self._history_table.clearSelection()
            self._history_table.setRowCount(0)
        self._note_edit.clear()
        self._page_label.setText('No history loaded')
        self._update_actions()

    def _finish_read(self):
        self._busy = False
        self._update_actions()
        if self._refresh_after_busy:
            self._refresh_after_busy = False
            self.refresh()

    def _retire_board(self):
        self._board = None
        self._pins = None
        self._pin_status_label.clear()
        with QSignalBlocker(self._businesses_table):
            self._businesses_table.clearSelection()
            self._businesses_table.setRowCount(0)
        self._context_label.clear()
        self._history_label.setText('Select a business to browse its saved check-ins')
        self._retire_history()

    def _reload_characters(self, selected, initial):
        characters = self._repository.get_business_checkin_characters()
        ids = {character.id for character in characters}
        if type(selected) is not int or selected not in ids:
            selected = None
            if initial:
                active = [character.id for character in characters if character.is_active]
                if len(active) == 1:
                    selected = active[0]
                elif len(characters) == 1:
                    selected = characters[0].id
        with QSignalBlocker(self._character_combo):
            self._character_combo.clear()
            self._character_combo.addItem('Choose a saved character…', None)
            for character in characters:
                self._character_combo.addItem(f'{character.name} (#{character.id})', character.id)
            self._character_combo.setCurrentIndex(max(0, self._character_combo.findData(selected)))
        return characters

    def refresh(self, _checked=False, *, character_id=None, initial=False):
        if self._closed or self._busy or self._closing:
            return
        pending = self._pending_character_id
        selected = pending if pending is not None else (character_id if initial else self._character_combo.currentData())
        business = self._selected_business_id()
        offset = self._history_offset
        self._generation += 1
        generation = self._generation
        self._busy = True
        self._retire_board()
        try:
            # A committed creation is an exact identity request, with no active
            # or sole-character fallback even if the row disappears afterwards.
            characters = self._reload_characters(selected, initial and pending is None)
            owner = self._character_combo.currentData()
            if pending is not None and owner is None:
                self._status_label.setText(
                    f'Saved character #{pending} is currently unavailable. '
                    'Choose another saved character or Refresh after restoring it.'
                )
            elif not characters:
                self._status_label.setText(
                    'No saved characters yet. Use Add saved character to create an owner '
                    'for manual check-ins, without starting tracking or OCR.'
                )
            elif owner is None:
                self._status_label.setText('Choose a saved character to view or record manual check-ins.')
            else:
                self._load_board(owner, business, offset, generation)
                if self._board is not None and self._board.character_id == pending:
                    self._pending_character_id = None
        except Exception as exc:
            self._show_error(exc)
        finally:
            self._finish_read()

    def _load_board(self, owner, business, offset, generation):
        repository = self._repository
        board = repository.get_business_checkin_board(owner)
        if (self._closed or generation != self._generation or repository is not self._repository
                or self._character_combo.currentData() != owner):
            return
        # Preferences are independent of observations. An unreadable preference
        # set must not retire a valid board, history, or export snapshot.
        pins = None
        try:
            pins = repository.get_manual_business_pins(owner)
        except Exception as exc:
            logger.warning('Could not load manual business pins (%s)', type(exc).__name__)
        if (self._closed or generation != self._generation or repository is not self._repository
                or self._character_combo.currentData() != owner):
            return
        self._board = board
        self._pins = pins
        self._pin_status_label.setText(
            f'{len(pins.business_ids)} pinned for this saved character.' if pins is not None else
            'Personal pins could not be loaded. Showing the usual business order; '
            'pin changes are unavailable until Refresh succeeds.'
        )
        rows = {row.business_id: row for row in board.rows}
        pinned = set(pins.business_ids) if pins is not None else set()
        businesses = ([key for key in BUSINESS_LABELS if key in pinned]
                      + sorted(pinned - set(BUSINESS_LABELS))
                      + [key for key in BUSINESS_LABELS if key not in pinned]
                      + sorted(set(rows) - set(BUSINESS_LABELS) - pinned))
        selected_index = 0
        with QSignalBlocker(self._businesses_table):
            self._businesses_table.setRowCount(len(businesses))
            for index, business_id in enumerate(businesses):
                row = rows.get(business_id)
                label = ('★ ' if business_id in pinned else '') + business_label(business_id)
                values = [label, _text(row.stock_percent) if row else '--',
                          _text(row.supply_percent) if row else '--', _text(row.stock_value) if row else '--',
                          _timestamp(row.recorded_at) if row else '--', row.note if row else '--']
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(value)
                    if column == 0:
                        cell.setData(Qt.ItemDataRole.UserRole, business_id)
                    self._businesses_table.setItem(index, column, cell)
                if business_id == business:
                    selected_index = index
            if businesses:
                self._businesses_table.selectRow(selected_index)
        # Legacy names can contain many lines. Keep this summary bounded; the
        # selector and detached report retain the complete literal saved name.
        preview = ' '.join(board.character_name.split())
        if len(preview) > 96:
            preview = preview[:96] + '…'
        self._context_label.setText(
            f'Character #{board.character_id} · {preview}\n'
            f'Board captured {_timestamp(board.captured_at)} UTC'
        )
        try:
            self._load_history(offset if self._selected_business_id() == business else 0, generation)
        except Exception as exc:
            if self._history_filters is None:
                raise
            self._show_history_error(exc)
        if self._board is board:
            self._status_label.setText(
                'Latest entries use save order. Export saves the displayed snapshot. '
                'Manual observations do not verify sales, production or profit.'
            )

    def _set_selected_business_pin(self):
        if self._closed or self._closing or self._busy or self._exporting:
            return
        repository = self._repository
        board = self._board
        pins = self._pins
        business = self._selected_business_id()
        if (board is None or pins is None or pins.character_id != board.character_id
                or self._character_combo.currentData() != board.character_id):
            return
        desired = business not in pins.business_ids
        if business is None or (desired and business not in BUSINESS_LABELS):
            return
        owner = board.character_id
        generation = self._generation
        self._busy = True
        self._update_actions()
        committed = False
        try:
            saved = repository.set_manual_business_pin(owner, business, desired)
            committed = True
            # Acknowledgment describes the committed target even if a callback
            # has changed the selector. Subsequent reads cannot undo this truth.
            self._pin_saved_notice_label.setText(
                f'Saved personal pin preference for character #{owner} · '
                f'{business_label(business)}: {"pinned" if desired else "unpinned"}.'
            )
            self._pin_saved_notice_label.show()
            if generation == self._generation and self._board is board and repository is self._repository:
                self._pins = saved
        except Exception as exc:
            logger.warning('Could not save manual business pin (%s)', type(exc).__name__)
            if generation == self._generation and self._board is board:
                self._pin_status_label.setText(
                    'Personal pin preference was not saved. Unpin a business before adding another.'
                    if isinstance(exc, BusinessPinLimitError) else
                    'Personal pin preference was not saved. Refresh and try again.'
                )
        finally:
            self._finish_read()
        # A business selection may have changed reentrantly. A fresh read keeps
        # that new selection while observing this commit; a changed owner has
        # retired the board and must never receive the old owner's pins.
        if (committed and self._board is board and self._character_combo.currentData() == owner
                and repository is self._repository and not self._closed and not self._closing):
            self.refresh()

    def _character_changed(self, *_):
        self._pending_character_id = None
        self._generation += 1
        self._reset_history_filters()
        self._retire_board()
        if self._busy:
            self._status_label.setText('Selection changed. Use Refresh to load the selected character.')
        else:
            self.refresh()

    def _selected_business_id(self):
        index = self._businesses_table.currentRow()
        cell = self._businesses_table.item(index, 0) if index >= 0 else None
        return cell.data(Qt.ItemDataRole.UserRole) if cell is not None else None

    def _business_changed(self):
        self._generation += 1
        self._reset_history_filters()
        self._retire_history()
        if self._busy or self._closed or self._board is None:
            return
        self._busy = True
        self._update_actions()
        try:
            self._load_history(0, self._generation)
        except Exception as exc:
            self._show_error(exc)
        finally:
            self._finish_read()

    def _load_history(self, offset, generation):
        board = self._board
        business = self._selected_business_id()
        repository = self._repository
        self._retire_history()
        if board is None or business is None:
            return
        owner = board.character_id
        context = (repository, owner, business)
        if context != self._history_context:
            self._reset_history_filters(context)
            offset = 0
        if self._history_dirty:
            self._update_history_filter_status()
            return
        self._history_generation += 1
        history_generation = self._history_generation
        filters = self._history_filters

        def current():
            return (not (self._closed or self._closing) and generation == self._generation
                    and history_generation == self._history_generation and self._board is board
                    and repository is self._repository and self._character_combo.currentData() == owner
                    and self._selected_business_id() == business and self._history_context == context
                    and self._history_filters == filters and not self._history_dirty)

        def read(page_offset):
            kwargs = {'offset': page_offset, 'limit': self.PAGE_SIZE}
            # Empty filters preserve the old call shape for older adapters.
            if filters is not None:
                kwargs['filters'] = filters
            return repository.get_business_checkin_history(owner, business, **kwargs)

        try:
            page = read(offset)
            # Check before issuing a clamp read: a retired response cannot
            # trigger another read for an old owner, business or filter.
            if not current():
                return
            if offset and offset >= page.total:
                offset = ((page.total - 1) // self.PAGE_SIZE) * self.PAGE_SIZE if page.total else 0
                page = read(offset)
            if not current():
                return
        except Exception:
            if current():
                raise
            return
        self._page = page
        self._history_offset = page.offset
        self._update_history_filter_status()
        self._history_label.setText(f'{business_label(business)} · Saved check-ins, newest save first')
        with QSignalBlocker(self._history_table):
            self._history_table.setRowCount(len(page.rows))
            for index, row in enumerate(page.rows):
                values = [str(row.id), _timestamp(row.recorded_at), _text(row.stock_percent),
                          _text(row.supply_percent), _text(row.stock_value)]
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(value)
                    if column == 0:
                        cell.setData(Qt.ItemDataRole.UserRole, row.id)
                    self._history_table.setItem(index, column, cell)
            if page.rows:
                self._history_table.selectRow(0)
        self._page_label.setText(
            f'Check-ins {page.offset + 1}–{page.offset + len(page.rows)} of {page.total}'
            if page.rows else ('No matching saved check-ins for this business' if filters is not None
                               else 'No saved check-ins for this business')
        )
        self._history_changed()

    def _history_changed(self):
        self._note_edit.clear()
        if self._page is None:
            return
        index = self._history_table.currentRow()
        if 0 <= index < len(self._page.rows):
            self._note_edit.setPlainText(self._page.rows[index].note)

    def _change_page(self, offset):
        if self._closed or self._closing or self._busy or self._page is None or self._history_dirty:
            return
        self._busy = True
        self._update_actions()
        try:
            self._load_history(offset, self._generation)
        except Exception as exc:
            self._show_history_error(exc)
        finally:
            self._finish_read()

    def _previous_page(self):
        if self._page is not None and self._page.offset:
            self._change_page(max(0, self._page.offset - self.PAGE_SIZE))

    def _next_page(self):
        if self._page is not None and self._page.has_more:
            self._change_page(self._page.offset + self.PAGE_SIZE)

    def _show_error(self, error):
        logger.warning('Could not load manual business check-ins (%s)', type(error).__name__)
        self._retire_board()
        self._status_label.setText(
            'Manual check-ins could not be loaded. No current board or history is available. '
            'Use Refresh to retry. Any open draft is unchanged.'
        )

    def _open_editor(self):
        if self._closed or self._closing or self._opening_editor:
            return
        if self._editor is not None:
            self._editor.show()
            self._editor.raise_()
            self._editor.activateWindow()
            return
        board = self._board
        business = self._selected_business_id()
        if self._busy or board is None or business not in BUSINESS_LABELS:
            return
        from .business_checkin_editor import BusinessCheckInEditor
        self._opening_editor = True
        try:
            editor = BusinessCheckInEditor(
                self._repository, board.character_id, business,
                parent=self, character_name=board.character_name,
            )
            self._editor = editor
            editor.saved.connect(self._checkin_saved)
            editor.finished.connect(lambda result, closed=editor: self._editor_finished(closed))
            editor.show()
        except Exception as exc:
            logger.warning('Could not open manual check-in editor (%s)', type(exc).__name__)
            self._status_label.setText('The check-in editor could not be opened. Try again or Refresh.')
        finally:
            self._opening_editor = False

    def _open_creator(self):
        if self._closed or self._closing or self._busy or self._exporting or self._opening_creator:
            return
        if self._creator is not None:
            self._creator.show()
            self._creator.raise_()
            self._creator.activateWindow()
            return
        from .saved_character_dialog import SavedCharacterDialog
        self._opening_creator = True
        self._update_actions()
        try:
            creator = SavedCharacterDialog(self._repository, parent=self)
            self._creator = creator
            creator.saved.connect(self._character_saved)
            creator.finished.connect(lambda result, closed=creator: self._creator_finished(closed))
            creator.show()
        except Exception as exc:
            logger.warning('Could not open saved-character editor (%s)', type(exc).__name__)
            self._status_label.setText('The saved-character editor could not be opened. Try again or Refresh.')
        finally:
            self._opening_creator = False
            self._update_actions()

    def _creator_finished(self, creator):
        if self._creator is not creator:
            return
        self._creator = None
        creator.deleteLater()

    def _character_saved(self, result):
        if self._closed:
            return
        self._saved_character_result = result
        if self._history_context is None or self._history_context[1] != result.id:
            self._reset_history_filters()
        self._pending_character_id = result.id
        self._refresh_after_busy = self._busy
        if self._busy:
            self._generation += 1
            self._retire_board()
        # Retire an unrelated selector value before reading storage. If the
        # character-list refresh fails, it must not still claim another owner.
        with QSignalBlocker(self._character_combo):
            self._character_combo.setCurrentIndex(max(0, self._character_combo.findData(result.id)))
        if result.created:
            notice = f'Created saved character #{result.id} · {result.name}.'
        else:
            notice = f'Saved character #{result.id} · {result.name} already exists. Reusing this saved identity.'
        self._character_saved_notice_label.setText(notice)
        self._character_saved_notice_label.show()
        self.refresh()

    def _editor_finished(self, editor):
        if self._editor is not editor:
            return
        self._editor = None
        editor.deleteLater()

    def _checkin_saved(self, record):
        if self._closed:
            return
        self._saved_notice_label.setText(
            f'Saved check-in #{record.id} for character #{record.character_id} · {business_label(record.business_id)}.'
        )
        self._saved_notice_label.show()
        self.refresh()

    def _export_board(self):
        self._export_snapshot(self._board, 'board')

    def _export_history(self):
        self._export_snapshot(self._page, 'history')

    def _export_snapshot(self, snapshot, kind):
        if snapshot is None or self._exporting or self._busy or self._closed or self._closing:
            return
        context = (f' for character #{snapshot.character_id} · {business_label(snapshot.business_id)}'
                   if kind == 'history' else '')
        self._exporting = True
        self._update_actions()
        try:
            filename, _ = QFileDialog.getSaveFileName(
                self, f'Export displayed manual check-in {kind}', f'manual_business_checkins_{kind}.json',
                'JSON files (*.json)',
            )
            if not filename or self._closed:
                return
            result = self._exporter.export_business_checkins_snapshot(snapshot, Path(filename))
            current = self._board if kind == 'board' else self._page
            if result.success:
                if current is snapshot or kind == 'history':
                    self._status_label.setText(
                        f'Exported {len(snapshot.rows)} displayed manual check-ins{context} to {result.file_path}'
                    )
            elif not self._closed:
                self._status_label.setText(f'Export failed{context}. Choose a writable location and try again.')
        except Exception as exc:
            logger.warning('Could not export manual check-ins (%s)', type(exc).__name__)
            if not self._closed:
                self._status_label.setText(f'Export failed{context}. Choose a writable location and try again.')
        finally:
            self._exporting = False
            self._update_actions()

    def _prepare_close(self):
        if (self._closed or self._closing or self._busy or self._opening_editor
                or self._opening_creator or self._exporting):
            return False
        children = (self._editor, self._creator)
        if any(child is not None and (child._busy or child._confirming_discard) for child in children):
            return False
        self._closing = True
        self._update_actions()
        try:
            for child in children:
                if child is not None and not child.close():
                    return False
            self._closed = True
            self._retire_board()
            return True
        finally:
            self._closing = False
            self._update_actions()

    def done(self, result):
        if self._prepare_close():
            super().done(result)

    def reject(self):
        self.done(QDialog.DialogCode.Rejected)

    def closeEvent(self, event):
        if self._prepare_close():
            super().done(QDialog.DialogCode.Rejected)
            event.accept()
        else:
            event.ignore()
