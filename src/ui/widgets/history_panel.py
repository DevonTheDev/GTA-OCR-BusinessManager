"""Browse recorded sessions and export a selected session without capture."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import Qt, QSignalBlocker
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget, QFileDialog,
    QLineEdit, QPlainTextEdit,
)

from ...database.repository import Repository
from ...database.session_annotations import normalize_annotation_query
from ...utils.exporter import DataExporter
from ...utils.helpers import format_money, format_time
from ...utils.logging import get_logger

if TYPE_CHECKING:
    from ...app import GTABusinessManager

logger = get_logger("ui.history")


def _money(value) -> str:
    return "--" if value is None else format_money(value)


def _time(value) -> str:
    if value is None:
        return "--"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _duration(value) -> str:
    return "--" if value is None else format_time(value)


class SessionHistoryPanel(QWidget):
    """Browse captured history and edit its separately saved personal context."""

    PAGE_SIZE = 25
    DETAIL_LIMIT = 1000

    def __init__(self, app: Optional["GTABusinessManager"] = None, parent=None,
                 *, repository: Optional[Repository] = None):
        super().__init__(parent)
        self._app = app
        self._repository = repository
        self._offset = 0
        self._total = 0
        self._selected_id = None
        self._exporting = False
        self._baseline_id = None
        self._comparison_dialog = None
        self._activity_ledger_dialog = None
        self._opening_activity_ledger = False
        self._comparing = False
        self._annotation_dialog = None
        self._opening_annotation = False
        self._annotation_available = False
        self._applied_annotation_query = None
        self._annotation_query_dirty = False
        self._setup_ui()

    def _label(self, text=""):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    def _table(self, columns):
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return table

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        title = self._label("Completed Session History")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(self._label(
            "Review previous sessions and export their recorded activities and balance changes. "
            "Net balance change includes spending and differs from live gross earnings. Times are UTC."
        ))
        controls = QHBoxLayout()
        controls.addWidget(self._label("Character"))
        self._character_combo = QComboBox()
        self._character_combo.addItem("All characters", None)
        controls.addWidget(self._character_combo)
        controls.addStretch()
        self._activity_ledger_button = QPushButton("Browse recorded activities…")
        controls.addWidget(self._activity_ledger_button)
        self._refresh_button = QPushButton("Refresh history")
        controls.addWidget(self._refresh_button)
        layout.addLayout(controls)
        search = QHBoxLayout()
        search.addWidget(self._label("Session notes"))
        self._annotation_query_edit = QLineEdit()
        self._annotation_query_edit.setObjectName("sessionAnnotationQuery")
        self._annotation_query_edit.setPlaceholderText("Find a label, tag or personal note; empty means all")
        self._annotation_query_edit.setToolTip(
            "Literal search: %, _ and \\ match themselves. A–Z ignores case; other letters match exactly. "
            "Up to 200 characters. Edit, then Apply."
        )
        self._annotation_apply_button = QPushButton("Apply search")
        search.addWidget(self._annotation_query_edit, 1)
        search.addWidget(self._annotation_apply_button)
        layout.addLayout(search)

        self._sessions_table = self._table([
            "Session", "Character", "Started (UTC)", "Duration", "Net balance change", "Activities", "Label / tags",
        ])
        layout.addWidget(self._sessions_table, 2)
        paging = QHBoxLayout()
        self._previous_button = QPushButton("Previous")
        self._next_button = QPushButton("Next")
        self._page_label = self._label("No sessions loaded")
        paging.addWidget(self._previous_button)
        paging.addWidget(self._page_label, 1)
        paging.addWidget(self._next_button)
        layout.addLayout(paging)

        self._baseline_label = self._label("No baseline selected. Pin a session as A, then select another as B.")
        layout.addWidget(self._baseline_label)
        comparison_controls = QHBoxLayout()
        self._pin_baseline_button = QPushButton("Use as baseline")
        self._clear_baseline_button = QPushButton("Clear baseline")
        self._compare_button = QPushButton("Compare with baseline")
        comparison_controls.addWidget(self._pin_baseline_button)
        comparison_controls.addWidget(self._clear_baseline_button)
        comparison_controls.addStretch()
        comparison_controls.addWidget(self._compare_button)
        layout.addLayout(comparison_controls)

        self._detail_label = self._label("Select a completed session to inspect its records.")
        layout.addWidget(self._detail_label)
        self._activities_table = self._table([
            "Completed (UTC)", "Type", "Name", "Outcome", "Earnings", "Duration",
        ])
        self._earnings_table = self._table([
            "Timestamp (UTC)", "Change", "Source", "Balance after",
        ])
        details = QTabWidget()
        self._detail_tabs = details
        details.addTab(self._activities_table, "Activities")
        details.addTab(self._earnings_table, "Balance changes")
        self._annotation_preview = QPlainTextEdit()
        self._annotation_preview.setReadOnly(True)
        self._annotation_preview.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self._annotation_preview.setPlaceholderText("Select a completed session to read its saved personal context.")
        details.addTab(self._annotation_preview, "Session notes")
        layout.addWidget(details, 2)
        detail_actions = QHBoxLayout()
        self._edit_annotation_button = QPushButton("Edit session notes…")
        detail_actions.addWidget(self._edit_annotation_button)
        detail_actions.addStretch()
        self._export_button = QPushButton("Export selected session as JSON…")
        detail_actions.addWidget(self._export_button)
        layout.addLayout(detail_actions)
        self._annotation_notice_label = self._label()
        self._annotation_notice_label.setVisible(False)
        layout.addWidget(self._annotation_notice_label)
        self._status_label = self._label("Refresh to load recorded sessions. Tracking does not need to be running.")
        layout.addWidget(self._status_label)

        self._previous_button.setEnabled(False)
        self._next_button.setEnabled(False)
        self._export_button.setEnabled(False)
        self._edit_annotation_button.setEnabled(False)
        self._annotation_query_edit.textChanged.connect(self._annotation_query_changed)
        self._annotation_query_edit.returnPressed.connect(self._apply_annotation_query)
        self._annotation_apply_button.clicked.connect(self._apply_annotation_query)
        self._edit_annotation_button.clicked.connect(self._open_annotation)
        self._activity_ledger_button.clicked.connect(self._open_activity_ledger)
        self._refresh_button.clicked.connect(self.refresh)
        self._character_combo.currentIndexChanged.connect(self._filter_changed)
        self._previous_button.clicked.connect(self._previous_page)
        self._next_button.clicked.connect(self._next_page)
        self._sessions_table.itemSelectionChanged.connect(self._selection_changed)
        self._export_button.clicked.connect(self._export_selected)
        self._pin_baseline_button.clicked.connect(self._pin_baseline)
        self._clear_baseline_button.clicked.connect(self._clear_baseline)
        self._compare_button.clicked.connect(self._compare_selected)
        self._update_comparison_controls()

    def _annotation_query_changed(self):
        self._annotation_query_dirty = True
        self._retire_annotation_query()

    def _retire_annotation_query(self):
        self._clear_details()
        with QSignalBlocker(self._sessions_table):
            self._sessions_table.setRowCount(0)
        self._previous_button.setEnabled(False)
        self._next_button.setEnabled(False)
        self._page_label.setText("Search changed")
        self._status_label.setText("Session-note search changed. Apply search to load matching completed sessions.")

    def _apply_annotation_query(self):
        try:
            query = normalize_annotation_query(self._annotation_query_edit.text())
        except (TypeError, ValueError) as exc:
            self._annotation_query_dirty = True
            self._retire_annotation_query()
            self._status_label.setText(f"Invalid search: {exc}. Correct it and Apply search again.")
            return
        self._applied_annotation_query = query
        self._annotation_query_dirty = False
        with QSignalBlocker(self._annotation_query_edit):
            self._annotation_query_edit.setText(query or "")
        self._offset = 0
        self._load_page()

    def _open_annotation(self):
        if self._annotation_dialog is not None:
            self._annotation_dialog.show()
            self._annotation_dialog.raise_()
            self._annotation_dialog.activateWindow()
            return
        session_id = self._selected_id
        if session_id is None or not self._annotation_available or self._opening_annotation:
            return
        row = self._sessions_table.currentRow()
        cell = self._sessions_table.item(row, 0)
        if cell is None or cell.data(Qt.ItemDataRole.UserRole) != session_id:
            return
        from .session_annotation_dialog import SessionAnnotationDialog

        character_name = self._sessions_table.item(row, 1).text()
        started_at = self._sessions_table.item(row, 2).text()
        self._opening_annotation = True
        try:
            dialog = SessionAnnotationDialog(
                self._get_repository(), session_id, character_name=character_name,
                started_at=started_at, parent=self,
            )
            self._annotation_dialog = dialog
            dialog.saved.connect(self._annotation_saved)
            dialog.finished.connect(lambda result, closed=dialog: self._annotation_finished(closed))
            dialog.show()
        except Exception as exc:
            logger.warning("Could not open notes for session %s (%s)", session_id, type(exc).__name__)
            self._status_label.setText("Session notes could not be opened. Try again or refresh history.")
        finally:
            self._opening_annotation = False

    def _annotation_finished(self, dialog):
        if self._annotation_dialog is not dialog:
            return
        self._annotation_dialog = None
        dialog.deleteLater()

    def _annotation_saved(self, session_id):
        # Refresh the current selection, never the editor's possibly old target.
        # Keep committed acknowledgement separate from a failed/stale page refresh.
        self._annotation_notice_label.setText(f"Saved notes for session #{session_id}.")
        self._annotation_notice_label.setVisible(True)
        self.refresh()

    def _show_annotation(self, annotation):
        self._annotation_available = not annotation or annotation.get("available") is True
        if not annotation:
            text = "No saved session notes. Use Edit session notes to add personal context."
        elif not self._annotation_available:
            text = ("Saved session notes are unavailable because their stored data is invalid. "
                    "Recorded history and export are still available. The notes have not been replaced.")
        else:
            text = (f"Label: {annotation['label'] or '(none)'}\n"
                    f"Tags: {', '.join(annotation['tags']) or '(none)'}\n"
                    f"Saved (UTC): {annotation['updated_at']}\n\n"
                    f"Personal note:\n{annotation['note']}")
        self._annotation_preview.setPlainText(text)
        self._edit_annotation_button.setEnabled(self._annotation_available)

    def _open_activity_ledger(self):
        if self._activity_ledger_dialog is not None:
            self._activity_ledger_dialog.show()
            self._activity_ledger_dialog.raise_()
            self._activity_ledger_dialog.activateWindow()
            return
        if self._opening_activity_ledger:
            return
        from .activity_ledger_dialog import ActivityLedgerDialog

        self._opening_activity_ledger = True
        try:
            dialog = ActivityLedgerDialog(
                self._get_repository(), self, character_id=self._character_combo.currentData(),
            )
            self._activity_ledger_dialog = dialog
            dialog.finished.connect(lambda result, closed=dialog: self._activity_ledger_finished(closed))
            dialog.show()
        except Exception as exc:
            logger.warning("Could not open recorded activities: %s", exc)
            self._status_label.setText(f"Could not open recorded activities: {exc}. Try again.")
        finally:
            self._opening_activity_ledger = False

    def _activity_ledger_finished(self, dialog):
        if self._activity_ledger_dialog is not dialog:
            return
        self._activity_ledger_dialog = None
        dialog.deleteLater()

    def _update_comparison_controls(self):
        self._pin_baseline_button.setEnabled(self._selected_id is not None)
        self._clear_baseline_button.setEnabled(self._baseline_id is not None)
        self._compare_button.setEnabled(
            self._baseline_id is not None and self._selected_id is not None
            and self._baseline_id != self._selected_id
            and not self._comparing and self._comparison_dialog is None
        )

    def _pin_baseline(self):
        if self._selected_id is None:
            return
        row = self._sessions_table.currentRow()
        cell = self._sessions_table.item(row, 0)
        if cell is None or cell.data(Qt.ItemDataRole.UserRole) != self._selected_id:
            return
        self._baseline_id = self._selected_id
        character_name = self._sessions_table.item(row, 1).text()
        started_at = self._sessions_table.item(row, 2).text()
        self._baseline_label.setText(
            f"Baseline A · Session #{self._baseline_id} · {character_name} · Started {started_at} UTC"
        )
        self._update_comparison_controls()

    def _clear_baseline(self):
        self._baseline_id = None
        self._baseline_label.setText("No baseline selected. Pin a session as A, then select another as B.")
        self._update_comparison_controls()

    def _compare_selected(self):
        baseline_id, comparison_id = self._baseline_id, self._selected_id
        if (baseline_id is None or comparison_id is None or baseline_id == comparison_id
                or self._comparing or self._comparison_dialog is not None):
            return
        from ...database.session_comparison import SessionComparisonUnavailable
        from .session_comparison_dialog import SessionComparisonDialog

        self._comparing = True
        self._update_comparison_controls()
        try:
            repository = self._get_repository()
            comparison = repository.get_session_comparison(baseline_id, comparison_id)
            dialog = SessionComparisonDialog(comparison, exporter=DataExporter(repository), parent=self)
            self._comparison_dialog = dialog
            dialog.finished.connect(lambda result, closed=dialog: self._comparison_finished(closed))
            self._baseline_label.setText(
                f"Baseline A · Session #{baseline_id} · {comparison.baseline.character_name} · "
                f"Started {_time(comparison.baseline.started_at)} UTC"
            )
            dialog.open()
        except SessionComparisonUnavailable as exc:
            unavailable = ", ".join(f"#{session_id}" for session_id in exc.session_ids)
            if baseline_id in exc.session_ids:
                self._clear_baseline()
            if comparison_id in exc.session_ids:
                self._clear_details()
            self._status_label.setText(
                f"Completed session {unavailable} is no longer available. Refresh and select completed sessions again."
            )
        except Exception as exc:
            logger.warning("Could not compare sessions %s and %s: %s", baseline_id, comparison_id, exc)
            self._status_label.setText(f"Could not compare sessions: {exc}. Try Compare again or refresh history.")
        finally:
            self._comparing = False
            self._update_comparison_controls()

    def _comparison_finished(self, dialog):
        if self._comparison_dialog is not dialog:
            return
        self._comparison_dialog = None
        dialog.deleteLater()
        self._update_comparison_controls()

    def _get_repository(self) -> Repository:
        if self._repository is not None:
            return self._repository
        if self._app is None:
            raise RuntimeError("No history data source is available")
        return self._app.history_repository

    def refresh(self):
        """Refresh records, preserving a still-visible selection and filter."""
        previous_id = self._selected_id
        selected_character = self._character_combo.currentData()
        try:
            characters = self._get_repository().get_all_characters()
            with QSignalBlocker(self._character_combo):
                self._character_combo.clear()
                self._character_combo.addItem("All characters", None)
                for character in characters:
                    self._character_combo.addItem(character.name, character.id)
                index = self._character_combo.findData(selected_character)
                self._character_combo.setCurrentIndex(max(0, index))
                if index < 0:
                    self._offset = 0
            return self._load_page(previous_id)
        except Exception as exc:
            self._show_load_error(exc)

    def _filter_changed(self):
        self._offset = 0
        self._load_page()

    def _previous_page(self):
        if self._annotation_query_dirty:
            return
        self._offset = max(0, self._offset - self.PAGE_SIZE)
        self._load_page()

    def _next_page(self):
        if self._annotation_query_dirty:
            return
        if self._offset + self.PAGE_SIZE < self._total:
            self._offset += self.PAGE_SIZE
            self._load_page()

    def _clear_details(self):
        self._selected_id = None
        self._annotation_available = False
        self._edit_annotation_button.setEnabled(False)
        self._annotation_preview.clear()
        self._update_comparison_controls()
        self._export_button.setEnabled(False)
        self._activities_table.setRowCount(0)
        self._earnings_table.setRowCount(0)
        self._detail_label.setText("Select a completed session to inspect its records.")

    def _show_load_error(self, error):
        logger.warning("Could not load session history: %s", error)
        self._total = 0
        self._clear_details()
        with QSignalBlocker(self._sessions_table):
            self._sessions_table.setRowCount(0)
        self._previous_button.setEnabled(False)
        self._next_button.setEnabled(False)
        self._page_label.setText("History unavailable")
        self._status_label.setText(f"Could not load history: {error}. Use Refresh to retry.")

    def _load_page(self, previous_id=None):
        if self._annotation_query_dirty:
            self._retire_annotation_query()
            return False
        try:
            repository = self._get_repository()
            character_id = self._character_combo.currentData()
            page = repository.get_completed_session_history(
                character_id=character_id, limit=self.PAGE_SIZE, offset=self._offset,
                annotation_query=self._applied_annotation_query,
            )
            if page.total and self._offset >= page.total:
                self._offset = ((page.total - 1) // self.PAGE_SIZE) * self.PAGE_SIZE
                page = repository.get_completed_session_history(
                    character_id=character_id, limit=self.PAGE_SIZE, offset=self._offset,
                    annotation_query=self._applied_annotation_query,
                )
            self._total = page.total
            self._clear_details()
            selected_row = 0
            with QSignalBlocker(self._sessions_table):
                self._sessions_table.setRowCount(len(page.sessions))
                for row, item in enumerate(page.sessions):
                    values = [str(item.id), item.character_name, _time(item.started_at),
                              _duration(item.duration_seconds), _money(item.net_change), str(item.activities_count)]
                    context = "Notes unavailable" if item.annotation_status == "unavailable" else " · ".join(
                        part for part in (item.annotation_label, ", ".join(item.annotation_tags)) if part
                    )
                    values.append(context)
                    for column, value in enumerate(values):
                        cell = QTableWidgetItem(value)
                        if column == 0:
                            cell.setData(Qt.ItemDataRole.UserRole, item.id)
                        self._sessions_table.setItem(row, column, cell)
                    if item.id == previous_id:
                        selected_row = row
                if page.sessions:
                    self._sessions_table.selectRow(selected_row)
            self._previous_button.setEnabled(self._offset > 0 and page.total > 0)
            self._next_button.setEnabled(self._offset + len(page.sessions) < page.total)
            if page.sessions:
                self._page_label.setText(f"Sessions {self._offset + 1}–{self._offset + len(page.sessions)} of {page.total}")
                self._selection_changed()
            else:
                self._page_label.setText("0 completed sessions")
                self._status_label.setText(
                    "No completed sessions match this character and session-note search." if self._applied_annotation_query
                    else "No completed sessions yet for this character. A session is completed when tracking stops."
                )
            return True
        except Exception as exc:
            self._show_load_error(exc)

    def _fill_details(self, table, rows):
        table.setRowCount(min(len(rows), self.DETAIL_LIMIT))
        for row, values in enumerate(rows[:self.DETAIL_LIMIT]):
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(str(value)))

    def _selection_changed(self):
        self._clear_details()
        if self._annotation_query_dirty:
            return
        row = self._sessions_table.currentRow()
        if row < 0:
            return
        cell = self._sessions_table.item(row, 0)
        if cell is None:
            return
        session_id = cell.data(Qt.ItemDataRole.UserRole)
        try:
            data = self._get_repository().export_session_data(session_id)
            if not data or data["session"]["ended_at"] is None:
                raise RuntimeError("The completed session is no longer available")
            session = data["session"]
            self._detail_label.setText(
                f"Session #{session_id} · Closed {_time(session['ended_at'])} UTC · "
                f"Opening balance {_money(session['start_money'])} · Ending balance {_money(session['end_money'])}"
            )
            activities = data.get("activities", [])
            earnings = data.get("earnings", [])
            self._fill_details(self._activities_table, [
                [_time(item.get("ended_at")), item.get("type") or "", item.get("name") or "",
                 "Passed" if item.get("success") is True else "Failed" if item.get("success") is False else "Unknown",
                 _money(item.get("earnings")), _duration(item.get("duration_seconds"))]
                for item in activities[:self.DETAIL_LIMIT]
            ])
            self._fill_details(self._earnings_table, [
                [_time(item.get("timestamp")), _money(item.get("amount")), item.get("source") or "",
                 _money(item.get("balance_after"))] for item in earnings[:self.DETAIL_LIMIT]
            ])
            self._selected_id = session_id
            self._show_annotation(data.get("annotation"))
            self._export_button.setEnabled(not self._exporting)
            self._update_comparison_controls()
            self._status_label.setText(
                f"{len(activities)} recorded activities; {len(earnings)} balance changes. "
                f"Tables show up to {self.DETAIL_LIMIT} rows each; JSON export includes every record."
            )
        except Exception as exc:
            logger.warning("Could not read session %s: %s", session_id, exc)
            self._status_label.setText(f"Could not read session #{session_id}: {exc}. Refresh or select another session.")

    def _export_selected(self):
        session_id = self._selected_id
        if session_id is None or self._exporting:
            return
        self._exporting = True
        self._export_button.setEnabled(False)
        try:
            filename, _ = QFileDialog.getSaveFileName(
                self, "Export completed session", f"session_{session_id}.json", "JSON files (*.json)",
            )
            if not filename:
                return
            result = DataExporter(self._get_repository()).export_to_json(session_id, Path(filename))
            if result.success:
                self._status_label.setText(f"Exported session #{session_id} to {result.file_path}")
            else:
                self._status_label.setText(f"Export failed: {result.error_message}. No history records were changed.")
        except Exception as exc:
            logger.warning("Could not export session %s: %s", session_id, exc)
            self._status_label.setText(f"Export failed: {exc}. No history records were changed.")
        finally:
            self._exporting = False
            self._export_button.setEnabled(self._selected_id is not None)
