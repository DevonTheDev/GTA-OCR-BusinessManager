"""Read-only review of one freshly captured pair of manual observations."""

from datetime import timezone
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QLabel, QPlainTextEdit, QScrollArea,
    QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QFileDialog,
)

from ...database.business_checkins import business_label
from ...utils.logging import get_logger

logger = get_logger('ui.business_checkin_comparison')


def _timestamp(value):
    value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return value.isoformat(sep=' ').removesuffix('+00:00') + ' UTC'


def _number(value, *, difference=False):
    if value is None:
        return '--'
    return f'{value:+d}' if difference and value else str(value)


class BusinessCheckInComparisonDialog(QDialog):
    """The captured pair and exporter never follow the parent's later selection."""

    def __init__(self, comparison, *, exporter, parent=None):
        super().__init__(parent)
        self._comparison = comparison
        self._exporter = exporter
        self._exporting = False
        self._closed = False
        self.setWindowTitle('Manual business check-in comparison')
        self.setModal(False)
        self.resize(930, 700)
        self._setup_ui()

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

    @staticmethod
    def _note(text):
        note = QPlainTextEdit()
        note.setReadOnly(True)
        note.setPlainText(text)
        note.setPlaceholderText('No personal note recorded.')
        note.setMinimumHeight(120)
        note.setMaximumHeight(180)
        return note

    def _setup_ui(self):
        comparison = self._comparison
        layout = QVBoxLayout(self)
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        self._context_label = self._label(
            f'{comparison.character_name} (character #{comparison.character_id})\n'
            f'{business_label(comparison.business_id)} · {comparison.business_id}'
        )
        body.addWidget(self._context_label)
        body.addWidget(self._label(
            'Every difference is comparison B minus baseline A. -- means unknown; zero is known. '
            'A difference is unknown if either observation is unknown. Percentage differences use percentage points.'
        ))
        self._baseline_label = self._label(
            f'Baseline A · Check-in #{comparison.baseline.id}\n'
            f'Recorded {_timestamp(comparison.baseline.recorded_at)}'
        )
        self._comparison_label = self._label(
            f'Comparison B · Check-in #{comparison.comparison.id}\n'
            f'Recorded {_timestamp(comparison.comparison.recorded_at)}'
        )
        body.addWidget(self._baseline_label)
        body.addWidget(self._comparison_label)
        self._metrics_table = QTableWidget(3, 4)
        self._metrics_table.setHorizontalHeaderLabels(['Recorded field', 'Baseline A', 'Comparison B', 'Difference (B − A)'])
        self._metrics_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._metrics_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._metrics_table.verticalHeader().hide()
        self._metrics_table.setAlternatingRowColors(True)
        self._metrics_table.setWordWrap(False)
        self._metrics_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._metrics_table.horizontalHeader().setStretchLastSection(True)
        for index, (heading, field) in enumerate((('Stock (%)', 'stock_percent'), ('Supplies (%)', 'supply_percent'),
                                                 ('Observed stock value ($)', 'stock_value'))):
            value = getattr(comparison.differences, field)
            difference = _number(value, difference=True)
            if field != 'stock_value' and value is not None:
                difference += ' percentage points'
            values = (heading, _number(getattr(comparison.baseline, field)),
                      _number(getattr(comparison.comparison, field)), difference)
            for column, value in enumerate(values):
                self._metrics_table.setItem(index, column, QTableWidgetItem(value))
        for column, width in enumerate((200, 180, 180, 205)):
            self._metrics_table.setColumnWidth(column, width)
        self._metrics_table.setMinimumHeight(150)
        self._metrics_table.setMaximumHeight(160)
        body.addWidget(self._metrics_table)
        body.addWidget(self._label('Baseline A · full personal note'))
        self._baseline_note = self._note(comparison.baseline.note)
        body.addWidget(self._baseline_note)
        body.addWidget(self._label('Comparison B · full personal note'))
        self._comparison_note = self._note(comparison.comparison.note)
        body.addWidget(self._comparison_note)
        body.addWidget(self._label(
            'These are independent manual observations. Observed stock value is user-recorded, '
            'not verified proceeds or profit. Recorded timestamps are UTC save times and may move backward. '
            'The selected A/B order is preserved; no elapsed gameplay time or production rate is inferred.'
        ))
        body.addWidget(self._label(
            f'Pair captured {_timestamp(comparison.captured_at)}. Export saves this captured pair.'
        ))
        self._scroll_area.setWidget(content)
        layout.addWidget(self._scroll_area, 1)
        self._status_label = self._label()
        layout.addWidget(self._status_label)
        buttons = QDialogButtonBox()
        self._export_button = buttons.addButton('Export comparison as JSON…', QDialogButtonBox.ButtonRole.ActionRole)
        self._close_button = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        self._export_button.clicked.connect(self._export_comparison)
        self._close_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _update_actions(self):
        enabled = not (self._exporting or self._closed)
        self._export_button.setEnabled(enabled)
        self._close_button.setEnabled(enabled)

    def _export_comparison(self):
        if self._exporting or self._closed:
            return
        comparison = self._comparison
        exporter = self._exporter
        self._exporting = True
        self._update_actions()
        try:
            filename, _ = QFileDialog.getSaveFileName(
                self, 'Export manual business check-in comparison',
                f'manual_checkin_{comparison.baseline.id}_vs_{comparison.comparison.id}.json',
                'JSON files (*.json)',
            )
            if not filename:
                return
            result = exporter.export_business_checkin_comparison(comparison, Path(filename))
            if result.success:
                self._status_label.setText(f'Exported comparison to {result.file_path}')
            else:
                self._status_label.setText('Export failed. Choose a writable location and try again.')
        except Exception as exc:
            logger.warning('Could not export manual check-in comparison (%s)', type(exc).__name__)
            self._status_label.setText('Export failed. Choose a writable location and try again.')
        finally:
            self._exporting = False
            self._update_actions()

    def done(self, result):
        if self._exporting or self._closed:
            return
        self._closed = True
        self._update_actions()
        super().done(result)

    def reject(self):
        self.done(QDialog.DialogCode.Rejected)

    def closeEvent(self, event):
        if self._exporting:
            event.ignore()
            return
        self.done(QDialog.DialogCode.Rejected)
        event.accept()
