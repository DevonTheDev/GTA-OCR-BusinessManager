"""Read-only browsing and fixed-page export of completed-session activities."""

from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import QDate, QSignalBlocker, Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ...database.activity_ledger import ActivityLedgerFilters, validate_ledger_request
from ...utils.exporter import DataExporter
from ...utils.logging import get_logger

logger = get_logger("ui.activity_ledger")


def _text(value):
    return "--" if value is None else str(value)


def _timestamp(value):
    if value is None:
        return "--"
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.isoformat(sep=" ")


class ActivityLedgerDialog(QDialog):
    """Display one detached page; edits immediately retire the accepted snapshot."""

    PAGE_SIZE = 25

    def __init__(self, repository, parent=None, *, character_id=None):
        super().__init__(parent)
        self._repository = repository
        self._exporter = DataExporter(repository)
        self._page = None
        self._applied_filters = None
        self._offset = 0
        self._dirty = False
        self._exporting = False
        self._closed = False
        self.setWindowTitle("Recorded activity ledger")
        self.setModal(False)
        self.resize(1080, 820)
        self._setup_ui()
        try:
            self._reload_characters(character_id)
            self._apply_filters()
        except Exception as exc:
            self._show_error(exc)

    @staticmethod
    def _label(text=""):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    @staticmethod
    def _date_edit():
        control = QDateEdit()
        control.setDisplayFormat("yyyy-MM-dd")
        control.setDateRange(QDate(1, 1, 1), QDate(9999, 12, 31))
        today = datetime.now(timezone.utc).date()
        control.setDate(QDate(today.year, today.month, today.day))
        control.setCalendarPopup(True)
        control.setEnabled(False)
        return control

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        title = self._label("Recorded activities from completed sessions")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        filters = QGridLayout()
        filters.addWidget(self._label("Character"), 0, 0)
        self._character_combo = QComboBox()
        self._character_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._character_combo.setMinimumContentsLength(18)
        self._character_combo.addItem("All characters", None)
        filters.addWidget(self._character_combo, 0, 1)
        self._from_checkbox = QCheckBox("From (UTC, inclusive)")
        self._from_date = self._date_edit()
        self._until_checkbox = QCheckBox("Until (UTC, inclusive)")
        self._until_date = self._date_edit()
        filters.addWidget(self._from_checkbox, 0, 2)
        filters.addWidget(self._from_date, 0, 3)
        filters.addWidget(self._until_checkbox, 0, 4)
        filters.addWidget(self._until_date, 0, 5)
        filters.addWidget(self._label("Exact type"), 1, 0)
        self._type_edit = QLineEdit()
        self._type_edit.setPlaceholderText("All types")
        filters.addWidget(self._type_edit, 1, 1)
        filters.addWidget(self._label("Outcome"), 1, 2)
        self._outcome_combo = QComboBox()
        for title, value in (("All outcomes", None), ("Passed", "passed"),
                             ("Failed", "failed"), ("Unknown", "unknown")):
            self._outcome_combo.addItem(title, value)
        filters.addWidget(self._outcome_combo, 1, 3)
        filters.addWidget(self._label("Name or notes"), 1, 4)
        self._query_edit = QLineEdit()
        self._query_edit.setPlaceholderText("Literal phrase; empty means all")
        filters.addWidget(self._query_edit, 1, 5)
        filters.setColumnStretch(1, 1)
        filters.setColumnStretch(5, 2)
        layout.addLayout(filters)
        self._search_hint = self._label(
            "Name/notes search treats %, _ and \\ literally. A–Z ignores case; other letters match exactly. "
            "Type matches exactly. Edit filters, then Apply."
        )
        layout.addWidget(self._search_hint)
        actions = QHBoxLayout()
        self._apply_button = QPushButton("Apply filters")
        self._refresh_button = QPushButton("Refresh")
        actions.addWidget(self._apply_button)
        actions.addWidget(self._refresh_button)
        actions.addStretch()
        layout.addLayout(actions)

        headings = ["Activity", "Character", "Activity time (UTC)", "Type", "Name",
                    "Outcome", "Recorded amount", "Stored seconds"]
        self._activities_table = QTableWidget(0, len(headings))
        self._activities_table.setHorizontalHeaderLabels(headings)
        self._activities_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._activities_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._activities_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._activities_table.setAlternatingRowColors(True)
        self._activities_table.setWordWrap(False)
        self._activities_table.verticalHeader().setVisible(False)
        self._activities_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((75, 150, 175, 100, 190, 90, 125, 110)):
            self._activities_table.setColumnWidth(column, width)
        layout.addWidget(self._activities_table, 3)
        paging = QHBoxLayout()
        self._previous_button = QPushButton("Previous")
        self._next_button = QPushButton("Next")
        self._page_label = self._label("No page loaded")
        paging.addWidget(self._previous_button)
        paging.addWidget(self._page_label, 1)
        paging.addWidget(self._next_button)
        layout.addLayout(paging)
        layout.addWidget(self._label("Selected activity · full recorded details"))
        self._details_edit = QPlainTextEdit()
        self._details_edit.setReadOnly(True)
        self._details_edit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self._details_edit.setPlaceholderText("Select an activity to inspect its saved fields.")
        layout.addWidget(self._details_edit, 2)
        self._notes_label = self._label(
            "Activity time uses recorded completion, falling back to recorded start. Recorded start may be "
            "a persistence-time default, not the gameplay start. Date filters use inclusive UTC days.\n"
            "Amounts are stored activity values, distinct from session net and not verified payouts or profit. "
            "Duration is stored seconds, not time calculated from timestamps. -- means unavailable; zero is known.\n"
            "Each page is a fresh observation. Refresh or paging can see later database changes. "
            "Export saves only the accepted displayed page."
        )
        layout.addWidget(self._notes_label)
        self._status_label = self._label()
        layout.addWidget(self._status_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._export_button = buttons.addButton("Export this page as JSON…", QDialogButtonBox.ButtonRole.ActionRole)
        layout.addWidget(buttons)
        buttons.rejected.connect(self.reject)
        self._export_button.clicked.connect(self._export_page)
        self._apply_button.clicked.connect(self._apply_filters)
        self._refresh_button.clicked.connect(self.refresh)
        self._previous_button.clicked.connect(self._previous_page)
        self._next_button.clicked.connect(self._next_page)
        self._activities_table.itemSelectionChanged.connect(self._selection_changed)
        self._character_combo.currentIndexChanged.connect(self._filter_changed)
        self._outcome_combo.currentIndexChanged.connect(self._filter_changed)
        self._type_edit.textChanged.connect(self._filter_changed)
        self._query_edit.textChanged.connect(self._filter_changed)
        self._from_date.dateChanged.connect(self._filter_changed)
        self._until_date.dateChanged.connect(self._filter_changed)
        self._from_checkbox.toggled.connect(self._from_date.setEnabled)
        self._until_checkbox.toggled.connect(self._until_date.setEnabled)
        self._from_checkbox.toggled.connect(self._filter_changed)
        self._until_checkbox.toggled.connect(self._filter_changed)
        self._retire_page()

    def _reload_characters(self, selected):
        characters = self._repository.get_all_characters()
        with QSignalBlocker(self._character_combo):
            self._character_combo.clear()
            self._character_combo.addItem("All characters", None)
            for character in characters:
                self._character_combo.addItem(character.name, character.id)
            index = self._character_combo.findData(selected)
            if index < 0 and selected is not None:
                # Preserve an unavailable selection rather than silently broadening it.
                self._character_combo.addItem(f"Character #{selected} (unavailable)", selected)
                index = self._character_combo.count() - 1
            self._character_combo.setCurrentIndex(max(0, index))

    def _retire_page(self):
        self._page = None
        with QSignalBlocker(self._activities_table):
            self._activities_table.clearSelection()
            self._activities_table.setRowCount(0)
        self._details_edit.clear()
        self._previous_button.setEnabled(False)
        self._next_button.setEnabled(False)
        self._export_button.setEnabled(False)

    def _filter_changed(self, *_):
        self._dirty = True
        self._retire_page()
        self._page_label.setText("Filters changed")
        self._status_label.setText("Filters changed. Apply to load matching recorded activities.")

    def _read_filters(self):
        filters = ActivityLedgerFilters(
            character_id=self._character_combo.currentData(),
            date_from=self._from_date.date().toPyDate() if self._from_checkbox.isChecked() else None,
            date_until=self._until_date.date().toPyDate() if self._until_checkbox.isChecked() else None,
            activity_type=self._type_edit.text() or None,
            outcome=self._outcome_combo.currentData(),
            query=self._query_edit.text() or None,
        )
        return validate_ledger_request(filters, limit=self.PAGE_SIZE, offset=0)

    def _apply_filters(self):
        self._retire_page()
        try:
            filters = self._read_filters()
        except (TypeError, ValueError) as exc:
            self._dirty = True
            self._page_label.setText("Invalid filters")
            self._status_label.setText(f"Invalid filters: {exc}. Correct them and Apply again.")
            return
        self._applied_filters = filters
        self._offset = 0
        self._dirty = False
        self._load_page()

    def refresh(self):
        """Refresh applied filters and character choices, preserving unapplied edits."""
        selected_id = self._selected_activity_id()
        self._retire_page()
        try:
            self._reload_characters(self._character_combo.currentData())
            if self._dirty:
                self._status_label.setText("Filters changed. Apply to load matching recorded activities.")
                return
            if self._applied_filters is None:
                self._apply_filters()
            else:
                self._load_page(selected_id)
        except Exception as exc:
            self._show_error(exc)

    def _show_error(self, error):
        logger.warning("Could not load recorded activities: %s", error)
        self._retire_page()
        self._page_label.setText("Activities unavailable")
        self._status_label.setText(f"Could not load recorded activities: {error}. Use Refresh to retry.")

    def _load_page(self, selected_id=None):
        self._retire_page()
        self._page_label.setText("Loading…")
        self._status_label.setText("Loading recorded activities…")
        try:
            page = self._repository.get_completed_activity_ledger(
                self._applied_filters, limit=self.PAGE_SIZE, offset=self._offset,
            )
            if self._offset and self._offset >= page.total:
                self._offset = ((page.total - 1) // self.PAGE_SIZE) * self.PAGE_SIZE if page.total else 0
                page = self._repository.get_completed_activity_ledger(
                    self._applied_filters, limit=self.PAGE_SIZE, offset=self._offset,
                )
            self._page = page
            self._offset = page.offset
            selected_index = 0
            with QSignalBlocker(self._activities_table):
                self._activities_table.setRowCount(len(page.rows))
                for index, row in enumerate(page.rows):
                    values = [str(row.id), _text(row.character_name), _timestamp(row.activity_time),
                              _text(row.activity_type), _text(row.name), row.outcome.title(),
                              _text(row.recorded_amount), _text(row.duration_seconds)]
                    for column, value in enumerate(values):
                        cell = QTableWidgetItem(value)
                        if column == 0:
                            cell.setData(Qt.ItemDataRole.UserRole, row.id)
                        self._activities_table.setItem(index, column, cell)
                    if row.id == selected_id:
                        selected_index = index
                if page.rows:
                    self._activities_table.selectRow(selected_index)
            self._previous_button.setEnabled(page.offset > 0 and page.total > 0)
            self._next_button.setEnabled(page.offset + len(page.rows) < page.total)
            self._export_button.setEnabled(not self._exporting)
            if page.rows:
                self._page_label.setText(
                    f"Activities {page.offset + 1}–{page.offset + len(page.rows)} of {page.total} "
                    f"· Page {page.offset // page.limit + 1} of {(page.total + page.limit - 1) // page.limit}"
                )
                self._status_label.setText(f"Observed {_timestamp(page.observed_at)} UTC. Export saves this displayed page.")
                self._selection_changed()
            else:
                self._page_label.setText(f"0 displayed activities · {page.total} matching")
                self._status_label.setText("No recorded activities match these filters in completed sessions.")
        except Exception as exc:
            self._show_error(exc)

    def _previous_page(self):
        if self._page is not None and self._page.offset > 0:
            self._offset = max(0, self._page.offset - self.PAGE_SIZE)
            self._load_page()

    def _next_page(self):
        if self._page is not None and self._page.offset + len(self._page.rows) < self._page.total:
            self._offset = self._page.offset + self.PAGE_SIZE
            self._load_page()

    def _selected_activity_id(self):
        index = self._activities_table.currentRow()
        cell = self._activities_table.item(index, 0) if index >= 0 else None
        return cell.data(Qt.ItemDataRole.UserRole) if cell is not None else None

    def _selection_changed(self):
        self._details_edit.clear()
        if self._page is None:
            return
        selected_id = self._selected_activity_id()
        row = next((item for item in self._page.rows if item.id == selected_id), None)
        if row is None:
            return
        values = [
            ("Activity ID", row.id), ("Session ID", row.session_id),
            ("Character ID", row.character_id), ("Character", row.character_name),
            ("Type", row.activity_type), ("Name", row.name), ("Business type", row.business_type),
            ("Outcome", row.outcome.title()), ("Recorded amount", row.recorded_amount),
            ("Stored duration (seconds)", row.duration_seconds),
            ("Recorded start (UTC)", _timestamp(row.recorded_start)),
            ("Recorded completion (UTC)", _timestamp(row.completed_at)),
            ("Activity time (UTC)", _timestamp(row.activity_time)), ("Notes", row.notes),
        ]
        self._details_edit.setPlainText("\n".join(f"{label}: {_text(value)}" for label, value in values))

    def _export_page(self):
        page = self._page
        if page is None or self._exporting or self._closed:
            return
        self._exporting = True
        self._export_button.setEnabled(False)
        try:
            filename, _ = QFileDialog.getSaveFileName(
                self, "Export recorded activity page", f"activity_ledger_page_{page.offset // page.limit + 1}.json",
                "JSON files (*.json)",
            )
            if not filename or self._closed:
                return
            result = self._exporter.export_activity_ledger_page(page, Path(filename))
            if result.success:
                if self._page is page:
                    self._status_label.setText(f"Exported {len(page.rows)} displayed activities to {result.file_path}")
            else:
                self._show_export_error(result.error_message)
        except Exception as exc:
            logger.warning("Could not export recorded activity page: %s", exc)
            if not self._closed:
                self._show_export_error(exc)
        finally:
            self._exporting = False
            if not self._closed:
                self._export_button.setEnabled(self._page is not None)

    def _show_export_error(self, error):
        retry = "Apply filters before exporting again." if self._dirty else "Try exporting again."
        self._status_label.setText(f"Export failed: {error}. {retry}")

    def done(self, result):
        self._closed = True
        self._page = None
        super().done(result)
