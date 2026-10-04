"""Read-only completed sessions grouped by their UTC completion day."""

from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path

from PyQt6.QtCore import QDate, QSignalBlocker, Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...database.daily_session_overview import (
    DailySessionDataError,
    DailySessionFilters,
    DailySessionLimitError,
    DailySessionUnavailable,
    validate_daily_session_filters,
)
from ...utils.exporter import DataExporter
from ...utils.logging import get_logger

logger = get_logger("ui.daily_session_overview")
SOURCE_PAGE_SIZE = 25


def _number(value):
    return "--" if value is None else str(value)


def _compact(value):
    if value is None:
        return "--"
    return str(value) if isinstance(value, int) else format(value, ".6g")


def _timestamp(value):
    return "--" if value is None else value.isoformat(sep=" ")


def _metrics_details(metrics):
    values = (
        ("Sessions", metrics.sessions), ("Known net", metrics.known_net),
        ("Unavailable net", metrics.sessions - metrics.known_net),
        ("Saved net change total", metrics.net_change_total),
        ("Positive durations", metrics.positive_durations),
        ("Zero durations", metrics.zero_durations),
        ("Negative durations", metrics.negative_durations),
        ("Unavailable durations", metrics.unavailable_durations),
        ("Positive duration total (seconds)", metrics.duration_seconds_total),
        ("Paired sessions", metrics.paired_sessions), ("Net per hour", metrics.net_per_hour),
    )
    return "\n".join(f"{name}: {_number(value)}" for name, value in values) + (
        "\nNumeric issues: " + (", ".join(metrics.issues) if metrics.issues else "None")
    )


class DailySessionOverviewDialog(QDialog):
    """An accepted snapshot is shared by daily metrics, source rows and export."""

    def __init__(self, repository, parent=None, *, character_id=None):
        today = datetime.now(UTC).date()
        # Validate before QVariant lookup can treat True or 1.0 as character #1.
        initial_filters = validate_daily_session_filters(DailySessionFilters(
            today - timedelta(days=29), today, character_id,
        ))
        super().__init__(parent)
        self._repository = repository
        self._exporter = DataExporter(repository)
        self._snapshot = None
        self._applied_filters = None
        self._dirty = False
        self._closed = False
        self._exporting = False
        self._filter_revision = 0
        self._request_serial = 0
        self._display_days = ()
        self._source_rows = ()
        self._source_page = 0
        self.setWindowTitle("Daily session overview")
        self.setModal(False)
        self.resize(1000, 750)
        self._setup_ui(initial_filters)
        request = self._new_request()
        try:
            if self._reload_characters(character_id, request):
                self._apply_filters()
        except Exception as exc:  # noqa: BLE001 - A Qt entry point must show a recoverable failure.
            if self._is_current(request):
                self._show_error(exc)

    @staticmethod
    def _label(text=""):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        return label

    @staticmethod
    def _details():
        control = QPlainTextEdit()
        control.setReadOnly(True)
        control.setMinimumSize(0, 90)
        control.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        return control

    @staticmethod
    def _date_edit(day):
        control = QDateEdit()
        control.setDisplayFormat("yyyy-MM-dd")
        control.setDateRange(QDate(1, 1, 1), QDate(9999, 12, 31))
        control.setDate(QDate(day.year, day.month, day.day))
        control.setCalendarPopup(True)
        return control

    @staticmethod
    def _table(headings, widths):
        table = QTableWidget(0, len(headings))
        table.setHorizontalHeaderLabels(headings)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setMinimumSize(0, 135)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for index, width in enumerate(widths):
            table.setColumnWidth(index, width)
        return table

    @staticmethod
    def _fill_row(table, index, values):
        for column, value in enumerate(values):
            text = str(value)
            item = QTableWidgetItem(text)
            item.setToolTip(f"<qt>{escape(text)}</qt>")
            table.setItem(index, column, item)

    def _setup_ui(self, initial_filters):
        layout = QVBoxLayout(self)
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setMinimumSize(0, 0)
        content = QWidget()
        body = QVBoxLayout(content)
        title = self._label("Daily completed-session overview")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        body.addWidget(title)
        filters = QGridLayout()
        self._character_combo = QComboBox()
        self._character_combo.setMinimumWidth(0)
        self._character_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._character_combo.setMinimumContentsLength(14)
        # Keep the requested identity even if the first character-list read fails.
        self._character_combo.addItem("All characters", None)
        if initial_filters.character_id is not None:
            self._character_combo.addItem(
                f"Character #{initial_filters.character_id} (unavailable)", initial_filters.character_id,
            )
            self._character_combo.setCurrentIndex(1)
        filters.addWidget(self._label("Character"), 0, 0)
        filters.addWidget(self._character_combo, 0, 1)
        self._from_date = self._date_edit(initial_filters.date_from)
        self._until_date = self._date_edit(initial_filters.date_until)
        filters.addWidget(self._label("From (UTC, inclusive)"), 1, 0)
        filters.addWidget(self._from_date, 1, 1)
        filters.addWidget(self._label("Until (UTC, inclusive)"), 2, 0)
        filters.addWidget(self._until_date, 2, 1)
        # Word-wrapped labels may shrink to zero in a grid with an expanding
        # control column, even though they work well in the surrounding VBox.
        filters.setColumnMinimumWidth(0, 140)
        filters.setColumnStretch(1, 1)
        body.addLayout(filters)
        body.addWidget(self._label("Both dates are required; maximum 366 inclusive days. Edit filters, then Apply."))
        self._scope_label = self._label()
        self._overall_label = self._label()
        self._coverage_label = self._label()
        body.addWidget(self._scope_label)
        body.addWidget(self._overall_label)
        body.addWidget(self._coverage_label)
        self._days_table = self._table(
            ["Completion day (UTC)", "Sessions", "Saved net change", "Known net",
             "Positive seconds", "Positive / all", "Paired sessions", "Net per hour"],
            (170, 80, 135, 95, 135, 115, 115, 130),
        )
        body.addWidget(self._days_table, 1)
        self._detail_tabs = QTabWidget()
        sources = QWidget()
        source_layout = QVBoxLayout(sources)
        self._selected_day_label = self._label("Select a day to browse captured source sessions.")
        source_layout.addWidget(self._selected_day_label)
        self._sources_table = self._table(
            ["Session ID", "Character", "Completed (UTC)", "Saved net", "Elapsed seconds", "Start status"],
            (95, 170, 220, 110, 135, 110),
        )
        source_layout.addWidget(self._sources_table)
        pages = QHBoxLayout()
        self._previous_button = QPushButton("Previous")
        self._next_button = QPushButton("Next")
        self._page_label = self._label()
        pages.addWidget(self._previous_button)
        pages.addWidget(self._page_label, 1)
        pages.addWidget(self._next_button)
        source_layout.addLayout(pages)
        self._source_details_edit = self._details()
        self._source_details_edit.setPlaceholderText("Select a source session to see full literal values.")
        source_layout.addWidget(self._source_details_edit)
        self._detail_tabs.addTab(sources, "Source sessions")
        self._day_details_edit = self._details()
        self._detail_tabs.addTab(self._day_details_edit, "Day coverage")
        self._overall_details_edit = self._details()
        self._detail_tabs.addTab(self._overall_details_edit, "Overall coverage")
        self._notes_label = self._label(
            "Saved net balance change includes spending; it is not verified payout or profit. "
            "Values use saved session net only, without adding activities or balance-change events.\n\n"
            "Each whole session is assigned to its UTC completion day. Elapsed duration includes "
            "paused and cross-midnight time. Summed positive session durations are not daily active-play time.\n\n"
            "Net per hour uses only sessions with known net and positive elapsed duration: paired net × "
            "3600 / paired seconds. Zero, negative and unavailable durations remain counted separately. "
            "-- means unavailable; zero is a known value. Empty days have no numeric totals or rate.\n\n"
            "Tables show compact numbers; selected details preserve full values and numeric issue codes. "
            "Source pages and JSON use this same captured snapshot. Refresh reads current records only "
            "for applied filters; unfinished filter edits stay retired until Apply. History note search does not apply."
        )
        notes = QWidget()
        notes_layout = QVBoxLayout(notes)
        notes_layout.addWidget(self._notes_label)
        notes_layout.addStretch()
        self._detail_tabs.addTab(notes, "How to read this")
        body.addWidget(self._detail_tabs, 2)
        self._scroll_area.setWidget(content)
        layout.addWidget(self._scroll_area, 1)
        self._status_label = self._label()
        self._status_label.setMaximumHeight(52)
        layout.addWidget(self._status_label)
        actions = QHBoxLayout()
        self._apply_button = QPushButton("Apply filters")
        self._refresh_button = QPushButton("Refresh")
        actions.addWidget(self._apply_button)
        actions.addWidget(self._refresh_button)
        actions.addStretch()
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._export_button = buttons.addButton("Export snapshot as JSON…", QDialogButtonBox.ButtonRole.ActionRole)
        layout.addWidget(buttons)
        for name in ("character_combo", "from_date", "until_date", "apply_button", "refresh_button",
                     "days_table", "sources_table", "day_details_edit", "source_details_edit",
                     "previous_button", "next_button", "page_label", "export_button", "status_label"):
            getattr(self, "_" + name).setObjectName("daily_session_" + name)
        buttons.rejected.connect(self.reject)
        self._apply_button.clicked.connect(self._apply_filters)
        self._refresh_button.clicked.connect(self.refresh)
        self._export_button.clicked.connect(self._export_snapshot)
        self._previous_button.clicked.connect(lambda: self._change_page(-1))
        self._next_button.clicked.connect(lambda: self._change_page(1))
        self._days_table.itemSelectionChanged.connect(self._day_selection_changed)
        self._sources_table.itemSelectionChanged.connect(self._source_selection_changed)
        self._character_combo.currentIndexChanged.connect(self._filter_changed)
        self._from_date.dateChanged.connect(self._filter_changed)
        self._until_date.dateChanged.connect(self._filter_changed)
        self._retire_snapshot()

    def _new_request(self):
        self._request_serial += 1
        return self._request_serial, self._filter_revision

    def _is_current(self, request):
        return not self._closed and request == (self._request_serial, self._filter_revision)

    def _reload_characters(self, selected, request):
        characters = self._repository.get_all_characters()
        if not self._is_current(request):
            return False
        with QSignalBlocker(self._character_combo):
            self._character_combo.clear()
            self._character_combo.addItem("All characters", None)
            for character in characters:
                self._character_combo.addItem(character.name, character.id)
            index = self._character_combo.findData(selected)
            if index < 0 and selected is not None:
                self._character_combo.addItem(f"Character #{selected} (unavailable)", selected)
                index = self._character_combo.count() - 1
            self._character_combo.setCurrentIndex(max(0, index))
        return True

    def _retire_snapshot(self):
        self._snapshot = None
        self._display_days = ()
        self._source_rows = ()
        self._source_page = 0
        for table in (self._days_table, self._sources_table):
            with QSignalBlocker(table):
                table.clearSelection()
                table.setRowCount(0)
        for edit in (self._day_details_edit, self._source_details_edit, self._overall_details_edit):
            edit.clear()
        self._scope_label.clear()
        self._overall_label.setText("No accepted daily summary")
        self._coverage_label.clear()
        self._selected_day_label.setText("Select a day to browse captured source sessions.")
        self._page_label.setText("No source sessions")
        self._previous_button.setEnabled(False)
        self._next_button.setEnabled(False)
        self._export_button.setEnabled(False)

    def _filter_changed(self, *_):
        if self._closed:
            return
        self._filter_revision += 1
        self._dirty = True
        self._retire_snapshot()
        self._status_label.setText("Filters changed. Apply to load matching completed sessions.")

    def _read_filters(self):
        return validate_daily_session_filters(DailySessionFilters(
            date_from=self._from_date.date().toPyDate(),
            date_until=self._until_date.date().toPyDate(),
            character_id=self._character_combo.currentData(),
        ))

    def _apply_filters(self):
        if self._closed:
            return
        request = self._new_request()
        self._retire_snapshot()
        try:
            filters = self._read_filters()
        except (TypeError, ValueError) as exc:
            if self._is_current(request):
                self._dirty = True
                self._status_label.setText(f"Invalid filters: {exc}. Correct them and Apply again.")
            return
        if not self._is_current(request):
            return
        self._applied_filters = filters
        self._dirty = False
        self._load_snapshot(filters, request)

    def refresh(self):
        """Read applied controls, preserving an unfinished draft without applying it."""
        if self._closed:
            return
        selected_day = self._selected_day()
        selected_date = selected_day.day if selected_day is not None else None
        request = self._new_request()
        selected_character = self._character_combo.currentData()
        self._retire_snapshot()
        try:
            if not self._reload_characters(selected_character, request):
                return
            if self._dirty:
                self._status_label.setText("Filters changed. Apply to load matching completed sessions.")
                return
            if self._applied_filters is None:
                self._apply_filters()
            else:
                self._load_snapshot(self._applied_filters, request, selected_date)
        except Exception as exc:  # noqa: BLE001 - Keep unexpected storage failures inside the dialog.
            if self._is_current(request):
                self._show_error(exc)

    def _load_snapshot(self, filters, request, selected_date=None):
        try:
            snapshot = self._repository.get_daily_session_overview(filters)
        except Exception as exc:  # noqa: BLE001 - Never leave stale data after a failed Qt action.
            if self._is_current(request):
                self._show_error(exc)
            return
        if not self._is_current(request) or self._dirty:
            return
        self._snapshot = snapshot
        self._display_days = tuple(sorted(snapshot.days, key=lambda item: item.day, reverse=True))
        with QSignalBlocker(self._days_table):
            self._days_table.setRowCount(len(self._display_days))
            for index, day in enumerate(self._display_days):
                metrics = day.metrics
                self._fill_row(self._days_table, index, (
                    day.day.isoformat(), metrics.sessions, _compact(metrics.net_change_total),
                    f"{metrics.known_net}/{metrics.sessions}", _compact(metrics.duration_seconds_total),
                    f"{metrics.positive_durations}/{metrics.sessions}", metrics.paired_sessions,
                    _compact(metrics.net_per_hour),
                ))
            index = next((i for i, day in enumerate(self._display_days) if day.day == selected_date), None)
            if index is None:
                index = next((i for i, day in enumerate(self._display_days) if day.metrics.sessions), 0)
            if self._display_days:
                self._days_table.selectRow(index)
        # Character choices are a separate read; only the accepted ID belongs to
        # this scope. Captured names remain authoritative in source row details.
        character = "All characters" if filters.character_id is None else f"Character #{filters.character_id}"
        self._scope_label.setText(
            f"{character}\nUTC completion dates: {filters.date_from.isoformat()} through "
            f"{filters.date_until.isoformat()} (inclusive)"
        )
        metrics = snapshot.overall
        self._overall_label.setText(
            f"{metrics.sessions} sessions · {len(snapshot.days)} days · "
            f"Saved net {_compact(metrics.net_change_total)} · "
            f"Positive elapsed seconds {_compact(metrics.duration_seconds_total)} · "
            f"Net per hour {_compact(metrics.net_per_hour)}"
        )
        self._coverage_label.setText(
            f"Known net: {metrics.known_net}/{metrics.sessions} · "
            f"Positive: {metrics.positive_durations} · Zero: {metrics.zero_durations} · "
            f"Negative: {metrics.negative_durations} · Unavailable: {metrics.unavailable_durations} · "
            f"Paired: {metrics.paired_sessions}/{metrics.sessions}\n"
            f"Observed {_timestamp(snapshot.observed_at)} UTC"
        )
        self._overall_details_edit.setPlainText(_metrics_details(metrics))
        self._export_button.setEnabled(not self._exporting)
        self._status_label.setText(
            "Select a day to browse its captured source sessions. Export saves all days and sources."
            if metrics.sessions else "No completed sessions match these filters. Every selected UTC day is shown."
        )
        self._day_selection_changed()

    def _show_error(self, error):
        logger.warning("Could not load daily session overview (%s)", type(error).__name__)
        self._retire_snapshot()
        if isinstance(error, (DailySessionDataError, DailySessionLimitError, DailySessionUnavailable)):
            self._status_label.setText(f"{error} Correct the scope and Apply, or use Refresh to retry.")
        else:
            self._status_label.setText("Could not load daily session overview. Use Refresh to retry.")

    def _selected_day(self):
        index = self._days_table.currentRow()
        if (self._snapshot is None or not self._days_table.selectedItems()
                or not 0 <= index < len(self._display_days)):
            return None
        return self._display_days[index]

    def _day_selection_changed(self):
        self._day_details_edit.clear()
        day = self._selected_day()
        self._source_rows = () if day is None else self._snapshot.rows_for_day(day.day)
        self._source_page = 0
        if day is not None:
            self._selected_day_label.setText(f"{day.day.isoformat()} UTC · {day.metrics.sessions} captured sessions")
            empty = "No completed sessions\n\n" if day.metrics.sessions == 0 else ""
            self._day_details_edit.setPlainText(
                f"Completion day (UTC): {day.day.isoformat()}\n{empty}{_metrics_details(day.metrics)}"
            )
        else:
            self._selected_day_label.setText("Select a day to browse captured source sessions.")
        self._render_source_page()

    def _change_page(self, delta):
        if self._snapshot is None or self._closed:
            return
        page = self._source_page + delta
        if 0 <= page <= max(0, (len(self._source_rows) - 1) // SOURCE_PAGE_SIZE):
            self._source_page = page
            self._render_source_page()

    def _render_source_page(self):
        offset = self._source_page * SOURCE_PAGE_SIZE
        rows = self._source_rows[offset:offset + SOURCE_PAGE_SIZE]
        with QSignalBlocker(self._sources_table):
            self._sources_table.clearSelection()
            self._sources_table.setRowCount(len(rows))
            for index, row in enumerate(rows):
                self._fill_row(self._sources_table, index, (
                    row.session_id, row.character_name, _timestamp(row.ended_at),
                    _compact(row.net_change), _compact(row.duration_seconds), row.start_status,
                ))
            if rows:
                self._sources_table.selectRow(0)
        self._page_label.setText(
            f"{offset + 1}–{offset + len(rows)} of {len(self._source_rows)}"
            if rows else "No completed sessions" if self._selected_day() is not None else "No source sessions"
        )
        self._previous_button.setEnabled(bool(rows) and self._source_page > 0)
        self._next_button.setEnabled(offset + len(rows) < len(self._source_rows))
        self._source_selection_changed()

    def _source_selection_changed(self):
        self._source_details_edit.clear()
        index = self._sources_table.currentRow()
        offset = self._source_page * SOURCE_PAGE_SIZE
        if (self._snapshot is None or not self._sources_table.selectedItems()
                or not 0 <= index < self._sources_table.rowCount()
                or offset + index >= len(self._source_rows)):
            return
        row = self._source_rows[offset + index]
        self._source_details_edit.setPlainText("\n".join((
            f"Session ID: {row.session_id}", f"Character ID: {row.character_id}",
            f"Character name: {row.character_name}", f"Started (UTC): {_timestamp(row.started_at)}",
            f"Completed (UTC): {_timestamp(row.ended_at)}",
            f"Completion day (UTC): {row.completion_date.isoformat()}",
            f"Saved net change: {_number(row.net_change)}",
            f"Elapsed duration (seconds): {_number(row.duration_seconds)}",
            f"Elapsed duration (microseconds): {_number(row.duration_microseconds)}",
            f"Start status: {row.start_status}",
        )))

    def _export_snapshot(self):
        snapshot = self._snapshot
        if snapshot is None or self._exporting or self._closed:
            return
        self._exporting = True
        self._export_button.setEnabled(False)
        try:
            filename, _ = QFileDialog.getSaveFileName(
                self, "Export daily session overview",
                f"daily_sessions_{snapshot.filters.date_from}_{snapshot.filters.date_until}.json",
                "JSON files (*.json)",
            )
            if not filename or self._closed:
                return
            result = self._exporter.export_daily_session_overview(snapshot, Path(filename))
            if not self._closed and self._snapshot is snapshot:
                self._status_label.setText(
                    f"Exported {len(snapshot.days)} days and {len(snapshot.rows)} source sessions to {result.file_path}"
                    if result.success else f"Export failed: {result.error_message}. Try exporting again."
                )
        except Exception as exc:  # noqa: BLE001 - The chooser and writer may fail independently.
            logger.warning("Could not export daily session overview (%s)", type(exc).__name__)
            if not self._closed and self._snapshot is snapshot:
                self._status_label.setText("Export failed. Try exporting again.")
        finally:
            self._exporting = False
            if not self._closed:
                self._export_button.setEnabled(self._snapshot is not None)

    def done(self, result):
        self._closed = True
        self._request_serial += 1
        self._retire_snapshot()
        super().done(result)
