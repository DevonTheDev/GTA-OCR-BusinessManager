"""Read-only, coverage-aware summaries of recorded completed-session activities."""

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import QDate, QSignalBlocker, Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ...database.activity_insights import (
    ActivityInsightsDataError, ActivityInsightsLimitError, INSIGHT_ISSUE_MESSAGES,
)
from ...database.activity_ledger import ActivityLedgerFilters, validate_ledger_request
from ...utils.exporter import DataExporter
from ...utils.logging import get_logger

logger = get_logger("ui.activity_insights")
_NO_SELECTION = object()


def _number(value):
    """Keep full precision in the selectable detail view, including known zero."""
    return "--" if value is None else str(value)


def _compact(value):
    if value is None:
        return "--"
    return str(value) if isinstance(value, int) else format(value, ".6g")


def _type_label(value):
    if value is None:
        return "(missing type)"
    if value == "":
        return "(blank type)"
    if not value.strip():
        return f"(blank type: {value!r})"
    return value


class ActivityInsightsDialog(QDialog):
    """Retain one immutable observation; filter edits immediately retire it."""

    def __init__(self, repository, parent=None, *, character_id=None):
        super().__init__(parent)
        self._repository = repository
        self._exporter = DataExporter(repository)
        self._snapshot = None
        self._applied_filters = None
        self._dirty = False
        self._exporting = False
        self._closed = False
        self._ledger_dialog = None
        self._opening_ledger = False
        self.setWindowTitle("Activity insights")
        self.setModal(False)
        self.resize(1080, 800)
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
        title = self._label("Recorded activity insights · completed sessions")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        filters = QGridLayout()
        filters.addWidget(self._label("Character"), 0, 0)
        self._character_combo = QComboBox()
        self._character_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._character_combo.setMinimumContentsLength(15)
        filters.addWidget(self._character_combo, 0, 1)
        self._from_checkbox = QCheckBox("From (UTC, inclusive)")
        self._from_date = self._date_edit()
        self._until_checkbox = QCheckBox("Until (UTC, inclusive)")
        self._until_date = self._date_edit()
        filters.addWidget(self._from_checkbox, 0, 2)
        filters.addWidget(self._from_date, 0, 3)
        filters.addWidget(self._until_checkbox, 0, 4)
        filters.addWidget(self._until_date, 0, 5)
        filters.addWidget(self._label("Outcome"), 1, 0)
        self._outcome_combo = QComboBox()
        for title, value in (("All outcomes", None), ("Passed", "passed"),
                             ("Failed", "failed"), ("Unknown", "unknown")):
            self._outcome_combo.addItem(title, value)
        filters.addWidget(self._outcome_combo, 1, 1)
        filters.addWidget(self._label("Name or notes"), 1, 2)
        self._query_edit = QLineEdit()
        self._query_edit.setPlaceholderText("Literal phrase; empty means all")
        filters.addWidget(self._query_edit, 1, 3, 1, 3)
        filters.setColumnStretch(1, 1)
        filters.setColumnStretch(5, 1)
        layout.addLayout(filters)
        layout.addWidget(self._label(
            "Name/notes search treats %, _ and \\ literally. A–Z ignores case; other letters match exactly. "
            "History session-note search does not apply here. Edit filters, then Apply."
        ))
        actions = QHBoxLayout()
        self._apply_button = QPushButton("Apply filters")
        self._refresh_button = QPushButton("Refresh")
        actions.addWidget(self._apply_button)
        actions.addWidget(self._refresh_button)
        actions.addStretch()
        layout.addLayout(actions)
        self._overall_label = self._label("No summary loaded")
        layout.addWidget(self._overall_label)

        headings = ["Exact activity type", "Activities", "Sessions", "Passed / known outcomes",
                    "Recorded amount / known", "Mean seconds / positive", "Amount per hour / paired"]
        self._types_table = QTableWidget(0, len(headings))
        self._types_table.setHorizontalHeaderLabels(headings)
        self._types_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._types_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._types_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._types_table.setAlternatingRowColors(True)
        self._types_table.setWordWrap(False)
        self._types_table.verticalHeader().setVisible(False)
        self._types_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((190, 85, 85, 180, 200, 200, 210)):
            self._types_table.setColumnWidth(column, width)
        layout.addWidget(self._types_table, 3)
        layout.addWidget(self._label("Selected type · full metrics and coverage"))
        self._details_edit = QPlainTextEdit()
        self._details_edit.setReadOnly(True)
        self._details_edit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self._details_edit.setPlaceholderText("Select a type to inspect its recorded values and coverage.")
        layout.addWidget(self._details_edit, 2)
        drill = QHBoxLayout()
        self._drill_button = QPushButton("Browse matching activities…")
        self._drill_hint = self._label()
        drill.addWidget(self._drill_button)
        drill.addWidget(self._drill_hint, 1)
        layout.addLayout(drill)
        self._notes_label = self._label(
            "Amounts are recorded activity values, not verified payouts, profit or session net. "
            "Failed activities remain included unless filtered out. -- means unavailable; zero is known.\n"
            "Mean/total duration use positive stored seconds. Rate uses only rows with a known amount and "
            "positive duration: their total amount × 3600 / their total seconds. "
            "Date bounds use inclusive UTC days, completion then start; bounded dates exclude undated rows.\n"
            "Groups keep exact stored types. Full precision and numeric limits appear in selected details. "
            "JSON exports this accepted summary; refresh and ledger views are fresh observations."
        )
        layout.addWidget(self._notes_label)
        self._status_label = self._label()
        layout.addWidget(self._status_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._export_button = buttons.addButton("Export summary as JSON…", QDialogButtonBox.ButtonRole.ActionRole)
        layout.addWidget(buttons)
        buttons.rejected.connect(self.reject)
        self._apply_button.clicked.connect(self._apply_filters)
        self._refresh_button.clicked.connect(self.refresh)
        self._query_edit.returnPressed.connect(self._apply_filters)
        self._export_button.clicked.connect(self._export_snapshot)
        self._drill_button.clicked.connect(self._open_activity_ledger)
        self._types_table.itemSelectionChanged.connect(self._selection_changed)
        self._character_combo.currentIndexChanged.connect(self._filter_changed)
        self._outcome_combo.currentIndexChanged.connect(self._filter_changed)
        self._query_edit.textChanged.connect(self._filter_changed)
        self._from_date.dateChanged.connect(self._filter_changed)
        self._until_date.dateChanged.connect(self._filter_changed)
        self._from_checkbox.toggled.connect(self._from_date.setEnabled)
        self._until_checkbox.toggled.connect(self._until_date.setEnabled)
        self._from_checkbox.toggled.connect(self._filter_changed)
        self._until_checkbox.toggled.connect(self._filter_changed)
        self._retire_snapshot()

    def _reload_characters(self, selected):
        characters = self._repository.get_all_characters()
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

    def _retire_snapshot(self):
        self._snapshot = None
        with QSignalBlocker(self._types_table):
            self._types_table.clearSelection()
            self._types_table.setRowCount(0)
        self._details_edit.clear()
        self._export_button.setEnabled(False)
        self._update_drill_control()

    def _filter_changed(self, *_):
        self._dirty = True
        self._retire_snapshot()
        self._overall_label.setText("Filters changed")
        self._status_label.setText("Filters changed. Apply to load matching recorded activity insights.")

    def _read_filters(self):
        return validate_ledger_request(ActivityLedgerFilters(
            character_id=self._character_combo.currentData(),
            date_from=self._from_date.date().toPyDate() if self._from_checkbox.isChecked() else None,
            date_until=self._until_date.date().toPyDate() if self._until_checkbox.isChecked() else None,
            outcome=self._outcome_combo.currentData(), query=self._query_edit.text() or None,
        ))

    def _apply_filters(self):
        if self._closed:
            return
        self._retire_snapshot()
        try:
            filters = self._read_filters()
        except (TypeError, ValueError) as exc:
            self._dirty = True
            self._overall_label.setText("Invalid filters")
            self._status_label.setText(f"Invalid filters: {exc}. Correct them and Apply again.")
            return
        self._applied_filters = filters
        self._dirty = False
        self._load_snapshot()

    def refresh(self):
        """Refresh the accepted filters without applying a user's unfinished draft."""
        if self._closed:
            return
        group = self._selected_group()
        selected_type = group.activity_type if group is not None else _NO_SELECTION
        self._retire_snapshot()
        try:
            self._reload_characters(self._character_combo.currentData())
            if self._dirty:
                self._status_label.setText("Filters changed. Apply to load matching recorded activity insights.")
                return
            if self._applied_filters is None:
                self._apply_filters()
            else:
                self._load_snapshot(selected_type)
        except Exception as exc:
            self._show_error(exc)

    def _show_error(self, error):
        logger.warning("Could not load activity insights (%s)", type(error).__name__)
        self._retire_snapshot()
        self._overall_label.setText("Activity insights unavailable")
        if isinstance(error, (ActivityInsightsLimitError, ActivityInsightsDataError)):
            self._status_label.setText(f"{error} Narrow the filters and Apply again, or use Refresh to retry.")
        else:
            self._status_label.setText("Could not load activity insights. Use Refresh to retry.")

    def _load_snapshot(self, selected_type=_NO_SELECTION):
        self._retire_snapshot()
        try:
            snapshot = self._repository.get_completed_activity_insights(self._applied_filters)
            self._snapshot = snapshot
            selected_row = 0
            with QSignalBlocker(self._types_table):
                self._types_table.setRowCount(len(snapshot.groups))
                for index, group in enumerate(snapshot.groups):
                    metrics = group.metrics
                    rate = "--" if metrics.pass_rate is None else f"{metrics.pass_rate:.1%}"
                    values = [
                        _type_label(group.activity_type), str(metrics.activities), str(metrics.sessions),
                        f"{metrics.passed}/{metrics.known_outcomes} ({rate})",
                        f"{_compact(metrics.recorded_amount_total)} · {metrics.known_amounts}/{metrics.activities} known",
                        f"{_compact(metrics.duration_seconds_mean)} · {metrics.positive_durations}/{metrics.activities} positive",
                        f"{_compact(metrics.recorded_amount_per_hour)} · {metrics.paired_records}/{metrics.activities} paired",
                    ]
                    for column, value in enumerate(values):
                        self._types_table.setItem(index, column, QTableWidgetItem(value))
                    if group.activity_type == selected_type:
                        selected_row = index
                if snapshot.groups:
                    self._types_table.selectRow(selected_row)
            observed = snapshot.observed_at.astimezone(timezone.utc).isoformat(sep=" ")
            self._overall_label.setText(
                f"{snapshot.overall.activities} activities · {snapshot.overall.sessions} sessions · "
                f"{len(snapshot.groups)} types · Observed {observed} UTC"
            )
            self._export_button.setEnabled(not self._exporting)
            self._status_label.setText(
                "Select a type for full metrics. Export saves this accepted summary."
                if snapshot.groups else "No recorded activities match these filters in completed sessions."
            )
            self._selection_changed()
        except Exception as exc:
            self._show_error(exc)

    def _selected_group(self):
        index = self._types_table.currentRow()
        if (self._snapshot is None or not self._types_table.selectedItems()
                or not 0 <= index < len(self._snapshot.groups)):
            return None
        return self._snapshot.groups[index]

    def _selection_changed(self):
        self._details_edit.clear()
        group = self._selected_group()
        if group is not None:
            metrics = group.metrics
            values = [
                ("Exact activity type", repr(group.activity_type)),
                ("Activities", metrics.activities), ("Sessions", metrics.sessions),
                ("Passed", metrics.passed), ("Failed", metrics.failed), ("Unknown", metrics.unknown),
                ("Known outcomes", metrics.known_outcomes), ("Pass rate", metrics.pass_rate),
                ("Known amounts", metrics.known_amounts),
                ("Unavailable amounts", metrics.activities - metrics.known_amounts),
                ("Recorded amount total", metrics.recorded_amount_total),
                ("Recorded amount mean", metrics.recorded_amount_mean),
                ("Positive durations", metrics.positive_durations), ("Zero durations", metrics.zero_durations),
                ("Negative durations", metrics.negative_durations),
                ("Unavailable durations", metrics.unavailable_durations),
                ("Positive duration total (seconds)", metrics.duration_seconds_total),
                ("Positive duration mean (seconds)", metrics.duration_seconds_mean),
                ("Paired records", metrics.paired_records),
                ("Recorded amount per hour", metrics.recorded_amount_per_hour),
            ]
            details = "\n".join(f"{label}: {_number(value)}" for label, value in values)
            issues = [f"{code}: {INSIGHT_ISSUE_MESSAGES.get(code, 'Numeric value is unavailable.')}"
                      for code in metrics.issues]
            details += "\n\nNumeric issues: " + ("\n".join(issues) if issues else "None")
            details += "\nPass rate is passed / known outcomes (0–1); unknown outcomes are excluded."
            self._details_edit.setPlainText(details)
        self._update_drill_control()

    def _drill_filters(self):
        group = self._selected_group()
        if group is None:
            return None, "Select a type from an accepted summary to browse its activities."
        if group.activity_type is None or not group.activity_type.strip():
            return None, "Drilldown unavailable: missing or blank types cannot be selected exactly in the ledger."
        try:
            filters = validate_ledger_request(replace(self._snapshot.filters, activity_type=group.activity_type))
        except (TypeError, ValueError):
            return None, "Drilldown unavailable: this stored type contains text the exact ledger filter cannot accept."
        return filters, ""

    def _update_drill_control(self):
        filters, reason = self._drill_filters()
        self._drill_button.setEnabled(filters is not None and not self._opening_ledger and not self._closed)
        if filters is None:
            self._drill_hint.setText(reason)
        elif self._ledger_dialog is not None:
            self._drill_hint.setText(
                "An existing ledger is open. Browse focuses it with its original filters; "
                "close it to browse another type. Each ledger is a fresh observation."
            )
        else:
            self._drill_hint.setText("Opens a fresh matching ledger; later records may differ from this summary.")

    def _open_activity_ledger(self):
        filters, _ = self._drill_filters()
        if self._closed or self._opening_ledger or filters is None:
            return
        if self._ledger_dialog is not None:
            self._ledger_dialog.show()
            self._ledger_dialog.raise_()
            self._ledger_dialog.activateWindow()
            return
        from .activity_ledger_dialog import ActivityLedgerDialog

        self._opening_ledger = True
        self._update_drill_control()
        try:
            dialog = ActivityLedgerDialog(self._repository, self, initial_filters=filters)
            self._ledger_dialog = dialog
            dialog.finished.connect(lambda result, closed=dialog: self._ledger_finished(closed))
            dialog.show()
        except Exception as exc:
            logger.warning("Could not open insights ledger (%s)", type(exc).__name__)
            self._status_label.setText("Could not open the matching activity ledger. Try Browse again.")
        finally:
            self._opening_ledger = False
            self._update_drill_control()

    def _ledger_finished(self, dialog):
        if self._ledger_dialog is not dialog:
            return
        self._ledger_dialog = None
        dialog.deleteLater()
        self._update_drill_control()

    def _export_snapshot(self):
        snapshot = self._snapshot
        if snapshot is None or self._exporting or self._closed:
            return
        self._exporting = True
        self._export_button.setEnabled(False)
        try:
            filename, _ = QFileDialog.getSaveFileName(
                self, "Export recorded activity insights", "activity_insights.json", "JSON files (*.json)",
            )
            if not filename or self._closed:
                return
            result = self._exporter.export_activity_insights(snapshot, Path(filename))
            # A native chooser can reenter this modeless dialog. Never label an old
            # captured observation as the current summary or overwrite draft guidance.
            if self._snapshot is snapshot:
                if result.success:
                    self._status_label.setText(f"Exported {len(snapshot.groups)} activity types to {result.file_path}")
                else:
                    self._status_label.setText(f"Export failed: {result.error_message}. Try exporting again.")
        except Exception as exc:
            logger.warning("Could not export activity insights (%s)", type(exc).__name__)
            if not self._closed and self._snapshot is snapshot:
                self._status_label.setText("Export failed. Try exporting again.")
        finally:
            self._exporting = False
            if not self._closed:
                self._export_button.setEnabled(self._snapshot is not None)

    def done(self, result):
        self._closed = True
        self._retire_snapshot()
        if self._ledger_dialog is not None:
            self._ledger_dialog.close()
        super().done(result)
