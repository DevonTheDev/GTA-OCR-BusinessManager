"""Read-only display and export of one detached completed-session pair."""

import json
from datetime import timezone
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHeaderView, QLabel, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...utils.logging import get_logger

if TYPE_CHECKING:
    from ...database.session_comparison import SessionComparison, SessionSummary
    from ...utils.exporter import DataExporter

logger = get_logger("ui.session_comparison")


def _number(value):
    """Compact readable numbers without rounding a nonzero value into zero."""
    text = f"{abs(value):,.2f}".rstrip("0").rstrip(".")
    return f"{abs(value):.3g}" if value and text == "0" else text


def _sign(value, difference):
    return "-" if value < 0 else "+" if difference and value > 0 else ""


def _money(value, difference=False):
    return "--" if value is None else f"{_sign(value, difference)}${_number(value)}"


def _duration(value, difference=False):
    if value is None:
        return "--"
    magnitude = abs(value)
    hours = int(magnitude // 3600)
    minutes = int((magnitude % 3600) // 60)
    seconds = magnitude % 60
    parts = []
    if hours:
        parts.append(f"{hours:,}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{_number(seconds)}s")
    return _sign(value, difference) + " ".join(parts)


def _rate(value, difference=False):
    return "--" if value is None else _money(value, difference) + "/h"


def _count(value, difference=False):
    return _sign(value, difference) + f"{abs(value):,}"


def _timestamp(value):
    if value is None:
        return "--"
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M:%S")


class SessionComparisonDialog(QDialog):
    """Keep a fixed snapshot for display and export until the dialog closes."""

    def __init__(self, comparison: "SessionComparison", *, exporter: "DataExporter", parent=None):
        super().__init__(parent)
        self._comparison = comparison
        self._exporter = exporter
        self._exporting = False
        self.setWindowTitle("Completed session comparison")
        self.resize(880, 760)
        self._setup_ui()

    @staticmethod
    def _label(text):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    @staticmethod
    def _identity(role: str, summary: "SessionSummary"):
        return (
            f"{role} · Session #{summary.session_id}\n"
            f"Character #{summary.character_id}: {summary.character_name}\n"
            f"Started {_timestamp(summary.started_at)} UTC · Closed {_timestamp(summary.ended_at)} UTC\n"
            f"Opening balance {_money(summary.start_money)} · Ending balance {_money(summary.end_money)}"
        )

    @staticmethod
    def _table(first_heading, rows):
        table = QTableWidget(len(rows), 4)
        table.setHorizontalHeaderLabels([
            first_heading, "Baseline A", "Comparison B", "Difference (B − A)",
        ])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setAlternatingRowColors(True)
        table.setWordWrap(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(0, 255)
        table.horizontalHeader().setStretchLastSection(True)
        for row, values in enumerate(rows):
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                # Qt tooltips interpret rich text; escape stored names before wrapping.
                item.setToolTip(f"<qt>{escape(text)}</qt>")
                if column:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(row, column, item)
        table.setMinimumHeight(70 + min(max(len(rows), 1), 7) * 31)
        return table

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        title = self._label("Completed session comparison")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        content_layout.addWidget(title)
        content_layout.addWidget(self._label(
            "Baseline A is pinned in History. Every difference is comparison B minus baseline A."
        ))
        self._baseline_label = self._label(self._identity("Baseline A", self._comparison.baseline))
        self._comparison_label = self._label(self._identity("Comparison B", self._comparison.comparison))
        content_layout.addWidget(self._baseline_label)
        content_layout.addWidget(self._comparison_label)

        rows = []
        for title, attribute, formatter in (
            ("Net balance change", "net_change", _money),
            ("Duration", "duration_seconds", _duration),
            ("Net balance change per hour", "net_per_hour", _rate),
            ("Recorded activities", "recorded_activities", _count),
            ("Passed", "passed", _count),
            ("Failed", "failed", _count),
            ("Unknown outcome", "unknown", _count),
        ):
            rows.append([
                title,
                formatter(getattr(self._comparison.baseline.metrics, attribute)),
                formatter(getattr(self._comparison.comparison.metrics, attribute)),
                formatter(getattr(self._comparison.differences, attribute), difference=True),
            ])
        self._metrics_table = self._table("Metric", rows)
        content_layout.addWidget(self._metrics_table)

        content_layout.addWidget(self._label("Recorded activities by type"))
        type_rows = [[
            "(unknown type)" if item.activity_type is None else json.dumps(item.activity_type, ensure_ascii=False),
            _count(item.baseline), _count(item.comparison), _count(item.difference, difference=True),
        ] for item in self._comparison.activity_types]
        self._activity_types_table = self._table("Activity type", type_rows)
        content_layout.addWidget(self._activity_types_table)
        if not type_rows:
            content_layout.addWidget(self._label("Neither session has recorded activities."))

        self._notes_label = self._label(
            "Net balance change is the stored session net, includes spending, and is not a verified payout "
            "or profit. It is not recalculated from balances, activity earnings, or balance-change events.\n"
            "Activity counts include all recorded rows, including failed or unknown outcomes. Type rows show "
            "counts only; a type absent from one session has zero records. Stored type strings are quoted in "
            'JSON notation: "" is a blank string; the unquoted (unknown type) marker means a missing type.\n'
            "-- means unavailable; zero is a known value. Net per hour requires a known net and a positive "
            "duration. Missing or nonpositive durations leave the rate unavailable. Negative durations derived "
            "from recorded timestamps are shown without correction. Differences require both values.\n"
            "These differences do not establish causes or which session was better. Money and rates are "
            "rounded for display; JSON preserves numeric values. Close and compare again to read current records."
        )
        content_layout.addWidget(self._notes_label)
        self._snapshot_label = self._label(
            f"Snapshot generated {_timestamp(self._comparison.generated_at)} UTC. Export saves this displayed snapshot."
        )
        content_layout.addWidget(self._snapshot_label)
        self._scroll_area.setWidget(content)
        layout.addWidget(self._scroll_area, 1)

        self._status_label = self._label("")
        layout.addWidget(self._status_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._export_button = buttons.addButton(
            "Export comparison as JSON…", QDialogButtonBox.ButtonRole.ActionRole,
        )
        self._export_button.clicked.connect(self._export_comparison)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _export_comparison(self):
        if self._exporting:
            return
        comparison = self._comparison
        self._exporting = True
        self._export_button.setEnabled(False)
        try:
            filename, _ = QFileDialog.getSaveFileName(
                self, "Export completed session comparison",
                f"session_{comparison.baseline.session_id}_vs_{comparison.comparison.session_id}.json",
                "JSON files (*.json)",
            )
            if not filename:
                return
            result = self._exporter.export_session_comparison(comparison, Path(filename))
            if result.success:
                self._status_label.setText(f"Exported comparison to {result.file_path}")
            else:
                self._status_label.setText(f"Export failed: {result.error_message}. Try exporting again.")
        except Exception as exc:
            logger.warning("Could not export session comparison: %s", exc)
            self._status_label.setText(f"Export failed: {exc}. Try exporting again.")
        finally:
            self._exporting = False
            self._export_button.setEnabled(True)
