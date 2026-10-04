"""Fixed, read-only history trends from explicitly saved manual observations."""

from datetime import timezone
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHeaderView, QLabel, QPlainTextEdit,
    QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

try:
    import pyqtgraph as pg
except ImportError:
    pg = None

from ...database.business_checkin_trend import MAX_CHECKIN_TREND_ROWS
from ...database.business_checkins import business_label
from ...utils.logging import get_logger

logger = get_logger('ui.business_checkin_trend')

_FIELDS = (
    ('stock_percent', 'Stock (%)', '#2563eb'),
    ('supply_percent', 'Supplies (%)', '#c2410c'),
    ('stock_value', 'Observed stock value ($)', '#047857'),
)


def _timestamp(value):
    value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return value.isoformat(sep=' ').removesuffix('+00:00') + ' UTC'


def _number(value, *, difference=False):
    if value is None:
        return '--'
    return f'{value:+d}' if difference and value else str(value)


class BusinessCheckInTrendDialog(QDialog):
    """A captured snapshot; navigation and later writes never retarget this view."""

    def __init__(self, trend, *, exporter, parent=None):
        super().__init__(parent)
        self._trend = trend
        self._exporter = exporter
        self._exporting = False
        self._closed = False
        self._percent_plot = None
        self._value_plot = None
        self.setWindowTitle('Manual business check-in history trend')
        self.setModal(False)
        self.resize(900, 720)
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
    def _table(headings):
        table = QTableWidget(0, len(headings))
        table.setHorizontalHeaderLabels(headings)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _setup_ui(self):
        trend = self._trend
        layout = QVBoxLayout(self)
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        self._context_label = self._label(
            f'{trend.character_name} (character #{trend.character_id})\n'
            f'{business_label(trend.business_id)} · {trend.business_id}'
        )
        body.addWidget(self._context_label)
        self._capture_label = self._label(
            f'{len(trend.rows)} matching saved check-ins · limit {MAX_CHECKIN_TREND_ROWS}\n'
            f'Captured {_timestamp(trend.captured_at)}. This view and its export keep this snapshot.'
        )
        body.addWidget(self._capture_label)
        filters = trend.filters
        parts = []
        if filters is not None:
            if filters.note_query is not None:
                parts.append(f'note contains “{filters.note_query}”')
            if filters.recorded_from is not None:
                parts.append(f'from {filters.recorded_from.isoformat()} UTC')
            if filters.recorded_until is not None:
                parts.append(f'until {filters.recorded_until.isoformat()} UTC')
        self._filters_label = self._label(
            'Applied filters: ' + ('; '.join(parts) if parts else 'all saved check-ins for this business') + '.\n'
            'Note matching is literal: ASCII letters ignore case; other Unicode is exact. UTC dates include both bounds.'
        )
        body.addWidget(self._filters_label)
        body.addWidget(self._label(
            'Coverage and endpoint change · first and last mean the first and last selected check-ins. '
            '-- means unknown; zero is known. Change needs two check-ins with known endpoints.'
        ))
        self._metrics_table = self._table(['Recorded field', 'Known', 'Unknown', 'First', 'Last', 'Change (last − first)'])
        self._metrics_table.setRowCount(3)
        metrics = {metric.field: metric for metric in trend.metrics}
        for index, (field, heading, _) in enumerate(_FIELDS):
            metric = metrics[field]
            change = _number(metric.change, difference=True)
            if field != 'stock_value' and metric.change is not None:
                change += ' percentage points'
            values = (heading, str(metric.known_count), str(metric.unknown_count),
                      _number(metric.first_value), _number(metric.last_value), change)
            for column, value in enumerate(values):
                self._metrics_table.setItem(index, column, QTableWidgetItem(value))
        for column, width in enumerate((185, 65, 75, 140, 140, 205)):
            self._metrics_table.setColumnWidth(column, width)
        self._metrics_table.setFixedHeight(145)
        body.addWidget(self._metrics_table)
        body.addWidget(self._label(
            'Charts use selected check-in sequence 1…N, oldest insertion first. Every selected check-in keeps '
            'its place. Missing measurements create gaps; dots show isolated observations. '
            'Spacing does not represent elapsed time, and recorded UTC save times may move backward.'
        ))
        self._plot_status_label = self._label()
        self._add_plots(body, metrics)
        body.addWidget(self._plot_status_label)
        body.addWidget(self._label('All captured check-ins · exact recorded values and full UTC save times'))
        self._rows_table = self._table(['Sequence', 'Check-in ID', 'Recorded (UTC)', 'Stock %', 'Supplies %', 'Stock value ($)'])
        self._rows_table.setRowCount(len(trend.rows))
        for index, row in enumerate(trend.rows):
            values = (str(index + 1), str(row.id), _timestamp(row.recorded_at),
                      _number(row.stock_percent), _number(row.supply_percent), _number(row.stock_value))
            for column, value in enumerate(values):
                self._rows_table.setItem(index, column, QTableWidgetItem(value))
        for column, width in enumerate((85, 170, 265, 90, 100, 180)):
            self._rows_table.setColumnWidth(column, width)
        self._rows_table.setMinimumHeight(190)
        self._rows_table.setMaximumHeight(250)
        self._rows_table.itemSelectionChanged.connect(self._selection_changed)
        body.addWidget(self._rows_table)
        body.addWidget(self._label('Selected check-in · full personal note'))
        self._note_edit = QPlainTextEdit()
        self._note_edit.setReadOnly(True)
        self._note_edit.setPlaceholderText('Select a check-in to read its full personal note.')
        self._note_edit.setMinimumHeight(100)
        self._note_edit.setMaximumHeight(160)
        body.addWidget(self._note_edit)
        if trend.rows:
            self._rows_table.selectRow(0)
        body.addWidget(self._label(
            'Independent manual observations only. Observed stock value is user-recorded, '
            'not verified proceeds or profit. No production, growth rate or elapsed gameplay time is inferred.'
        ))
        self._scroll_area.setWidget(content)
        layout.addWidget(self._scroll_area, 1)
        self._status_label = self._label()
        layout.addWidget(self._status_label)
        buttons = QDialogButtonBox()
        self._export_button = buttons.addButton('Export trend as JSON…', QDialogButtonBox.ButtonRole.ActionRole)
        self._close_button = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        self._export_button.clicked.connect(self._export_trend)
        self._close_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _add_plots(self, body, metrics):
        statuses = []
        if not self._trend.rows:
            statuses.append('No matching saved check-ins. There are no observations to plot.')
        elif not any(metric.known_count for metric in metrics.values()):
            statuses.append('No recorded measurements in these check-ins. Notes remain available below.')
        if pg is None:
            statuses.append('Charts are unavailable because pyqtgraph could not be loaded. Summary, exact table, notes and JSON export remain available.')
        else:
            percent_fields = [entry for entry in _FIELDS[:2] if metrics[entry[0]].plot_status == 'available']
            if percent_fields:
                self._percent_plot = self._make_plot('Recorded stock and supplies', 'Percent', percent_fields)
                self._percent_plot.setYRange(0, 100, padding=0.05)
                body.addWidget(self._percent_plot)
            elif self._trend.rows:
                statuses.append('No recorded stock or supply percentages to plot.')
            value_status = metrics['stock_value'].plot_status
            if value_status == 'available':
                self._value_plot = self._make_plot('Recorded stock value', 'Observed value ($)', _FIELDS[2:])
                body.addWidget(self._value_plot)
            elif value_status == 'no_observations' and self._trend.rows:
                statuses.append('No recorded stock values to plot.')
        if metrics['stock_value'].plot_status == 'precision_limit':
            statuses.append('Value chart unavailable: at least one recorded integer exceeds exact plotting precision. Exact values remain in the summary, table and JSON export.')
        self._plot_status_label.setText('\n'.join(statuses))
        self._plot_status_label.setVisible(bool(statuses))

    def _make_plot(self, title, ylabel, fields):
        plot = pg.PlotWidget(background='w')
        # Raw-observation axes must not offer FFT, averaging or resampling.
        # PlotItem also disables its ViewBox menu; ordinary pan/zoom stays on.
        plot.getPlotItem().setMenuEnabled(False)
        plot.setMinimumHeight(210)
        plot.setMaximumHeight(260)
        plot.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        plot.setTitle(title, color='#172033')
        plot.setLabel('bottom', 'Selected check-in sequence (oldest insertion first)', color='#172033')
        plot.setLabel('left', ylabel, color='#172033')
        for name in ('bottom', 'left'):
            plot.getAxis(name).setTextPen('#172033')
            plot.getAxis(name).setPen('#64748b')
        plot.showGrid(x=True, y=True, alpha=0.2)
        plot.addLegend(labelTextColor='#172033')
        # One item per field retains every position, including unknown slots.
        # Finite checks and explicit finite connectivity prevent gap bridging.
        x = list(range(1, len(self._trend.rows) + 1))
        for field, heading, color in fields:
            y = [float('nan') if getattr(row, field) is None else float(getattr(row, field))
                 for row in self._trend.rows]
            plot.plot(x, y, name=heading, pen=pg.mkPen(color, width=2), connect='finite',
                      skipFiniteCheck=False, symbol='o', symbolSize=6, symbolPen=color,
                      symbolBrush=color, autoDownsample=False)
        plot.setXRange(0.5, max(1.5, len(x) + 0.5), padding=0)
        return plot

    def _selection_changed(self):
        self._note_edit.clear()
        selected = self._rows_table.selectionModel().selectedRows()
        if len(selected) == 1 and 0 <= selected[0].row() < len(self._trend.rows):
            self._note_edit.setPlainText(self._trend.rows[selected[0].row()].note)

    def _parent_closing(self):
        return bool(getattr(self.parent(), '_closing', False))

    def _update_actions(self):
        self._export_button.setEnabled(not (self._exporting or self._closed or self._parent_closing()))
        self._close_button.setEnabled(not (self._exporting or self._closed))

    def _export_trend(self):
        if self._exporting or self._closed or self._parent_closing():
            return
        trend, exporter = self._trend, self._exporter
        self._exporting = True
        self._update_actions()
        try:
            filename, _ = QFileDialog.getSaveFileName(
                self, 'Export captured manual check-in history trend',
                f'manual_checkin_trend_{trend.character_id}_{trend.business_id}.json', 'JSON files (*.json)',
            )
            if not filename:
                return
            result = exporter.export_business_checkins_snapshot(trend, Path(filename))
            if result.success:
                self._status_label.setText(f'Exported {len(trend.rows)} captured check-ins to {result.file_path}')
            else:
                self._status_label.setText('Export failed. Choose a writable location and try again.')
        except Exception as exc:
            logger.warning('Could not export manual check-in history trend (%s)', type(exc).__name__)
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
