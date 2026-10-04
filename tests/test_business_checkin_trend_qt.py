"""Opt-in real Qt/pyqtgraph coverage for detached manual history trends."""

import importlib
import importlib.util
import json
import os
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for check-in trend UI tests', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from sqlalchemy import text

from src.database.business_checkins import BusinessCheckInHistoryFilters
from src.database.repository import Repository
from src.ui.widgets.business_checkins_dialog import BusinessCheckInsDialog
from src.utils.exporter import DataExporter, ExportResult


@pytest.fixture
def qt(native_qt_application):
    assert native_qt_application is not None
    existing = set(native_qt_application.topLevelWidgets())
    yield native_qt_application
    created = [widget for widget in native_qt_application.topLevelWidgets() if widget not in existing]
    # pyqtgraph owns top-level menus and control widgets through its graphics
    # scene. Destroying those first in Qt's unspecified topLevelWidgets order
    # can leave dangling native proxies. Dispose application dialogs before
    # cleaning up the library's remaining top-level auxiliaries.
    for widget in created:
        if not sip.isdeleted(widget) and isinstance(widget, QtWidgets.QDialog) and widget.parent() is None:
            widget.hide()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    for widget in created:
        if not sip.isdeleted(widget):
            widget.hide()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert all(sip.isdeleted(widget) for widget in created)


@pytest.fixture
def records(tmp_path):
    repo = Repository(str(tmp_path / 'trend-ui.db'))
    assert repo.initialize()
    owner = repo.get_or_create_character('Alpha <b>literal</b>').id
    beta = repo.get_or_create_character('Beta').id
    rows = tuple(repo.save_business_checkin(
        owner, 'bunker', stock_percent=index, supply_percent=0 if index % 2 == 0 else None,
        stock_value=1000 * index, note=f'Keep {index} <b>literal</b>\ncomplete note\u00a0{index}',
    ) for index in range(28))
    yield repo, owner, beta, rows
    repo.close()
    repo._session_factory.kw['bind'].dispose()


def choose_business(board, business='bunker'):
    for index in range(board._businesses_table.rowCount()):
        if board._businesses_table.item(index, 0).data(Qt.ItemDataRole.UserRole) == business:
            board._businesses_table.selectRow(index)
            return
    raise AssertionError(f'missing business {business}')


def make_board(records, qt):
    board = BusinessCheckInsDialog(records[0], character_id=records[1])
    board.show()
    choose_business(board)
    qt.processEvents()
    assert hasattr(board, '_trend_button'), 'board must offer history trend action'
    return board


def trend_module():
    name = 'src.ui.widgets.business_checkin_trend_dialog'
    assert importlib.util.find_spec(name) is not None, 'detached history trend dialog must exist'
    return importlib.import_module(name)


def make_dialog(records, qt, **changes):
    module = trend_module()
    from src.database.business_checkin_trend import BusinessCheckInTrend
    snapshot = BusinessCheckInTrend(
        character_id=records[1], character_name='Alpha <b>literal</b>', business_id='bunker',
        captured_at=datetime(2026, 10, 4, 17, 0, 0, 123456, tzinfo=timezone.utc), rows=records[3],
    )
    if changes:
        snapshot = replace(snapshot, **changes)
    dialog = module.BusinessCheckInTrendDialog(snapshot, exporter=DataExporter(records[0]))
    dialog.show()
    qt.processEvents()
    return dialog


def test_history_trend_needs_accepted_filters_but_no_selected_row_or_baseline(records, qt):
    board = make_board(records, qt)
    board._history_table.clearSelection()
    assert board._trend_button.isEnabled() and board._baseline_id is None
    board._trend_button.click()
    assert len(board._trend_dialog._trend.rows) == 28
    board._trend_dialog.close()
    board._history_note_filter.setText('Keep 0 ')
    assert not board._trend_button.isEnabled()
    board._open_trend()
    assert board._trend_dialog is None
    board._history_apply_button.click()
    board._trend_button.click()
    assert [row.id for row in board._trend_dialog._trend.rows] == [records[3][0].id]
    assert board._trend_dialog._trend.filters.note_query == 'Keep 0 '


def test_trend_captures_all_matches_independently_of_page_and_retains_context(records, qt):
    board = make_board(records, qt)
    board._next_button.click()
    assert board._page.offset == 25
    board._trend_button.click()
    child = board._trend_dialog
    snapshot = child._trend
    assert tuple(row.id for row in snapshot.rows) == tuple(row.id for row in records[3])
    board._character_combo.setCurrentIndex(board._character_combo.findData(records[2]))
    board._history_note_filter.setText('new draft')
    board._open_trend()
    assert board._trend_dialog is child and child._trend is snapshot
    assert 'captured' in board._status_label.text().lower()
    child.close()
    assert board._trend_dialog is None
    board._history_clear_button.click()
    board._open_trend()
    second = board._trend_dialog
    assert second is not None and second is not child
    assert second._trend.character_id == records[2]
    board._trend_finished(child)
    assert board._trend_dialog is second
    board.close()
    assert not second.isVisible()


@pytest.mark.parametrize('stage', ['read', 'construct'])
@pytest.mark.parametrize('change', ['owner', 'owner_back', 'business', 'business_back', 'draft', 'filter_back', 'page', 'repository'])
def test_reentrant_snapshot_admission_rejects_changed_and_aba_context(records, qt, monkeypatch, stage, change):
    board = make_board(records, qt)
    constructed = []
    def alter():
        if change.startswith('owner'):
            board._character_combo.setCurrentIndex(board._character_combo.findData(records[2]))
            if change.endswith('back'):
                board._character_combo.setCurrentIndex(board._character_combo.findData(records[1]))
        elif change.startswith('business'):
            choose_business(board, 'meth')
            if change.endswith('back'):
                choose_business(board)
        elif change in ('draft', 'filter_back'):
            board._history_note_filter.setText('new draft')
            if change == 'filter_back':
                board._history_note_filter.clear()
                board._history_dirty = False
        elif change == 'page':
            board._page = replace(board._page, offset=25)
        else:
            board._repository = object()
        board._open_trend()
    if stage == 'read':
        original = records[0].get_business_checkin_trend
        def read(*args, **kwargs):
            snapshot = original(*args, **kwargs)
            alter()
            return snapshot
        monkeypatch.setattr(records[0], 'get_business_checkin_trend', read)
    else:
        module = trend_module()
        original = module.BusinessCheckInTrendDialog
        def construct(*args, **kwargs):
            child = original(*args, **kwargs)
            constructed.append(child)
            alter()
            return child
        monkeypatch.setattr(module, 'BusinessCheckInTrendDialog', construct)
    board._trend_button.click()
    assert board._trend_dialog is None
    assert not board._opening_trend and not board._busy
    assert all(not child.isVisible() for child in constructed)


def test_retired_query_error_does_not_override_new_context_status(records, qt, monkeypatch):
    board = make_board(records, qt)
    def read(*args, **kwargs):
        board._character_combo.setCurrentIndex(board._character_combo.findData(records[2]))
        board._status_label.setText('Status for new owner')
        raise RuntimeError('SECRET_PATH')
    monkeypatch.setattr(records[0], 'get_business_checkin_trend', read)
    board._trend_button.click()
    assert board._trend_dialog is None
    assert board._status_label.text() == 'Status for new owner'


@pytest.mark.parametrize('error', ['limit', 'storage'])
def test_snapshot_query_error_preserves_history_and_allows_retry(records, qt, monkeypatch, error):
    from src.database.business_checkin_trend import BusinessCheckInTrendLimitError
    board = make_board(records, qt)
    page = board._page
    def fail(*args, **kwargs):
        raise BusinessCheckInTrendLimitError() if error == 'limit' else RuntimeError('SECRET_PATH')
    with monkeypatch.context() as patch:
        patch.setattr(records[0], 'get_business_checkin_trend', fail)
        board._trend_button.click()
    assert board._page is page and board._trend_dialog is None and board._trend_button.isEnabled()
    assert 'SECRET_PATH' not in board._status_label.text()
    assert ('filter' if error == 'limit' else 'could not') in board._status_label.text().lower()
    board._trend_button.click()
    assert board._trend_dialog is not None


def test_real_curve_paths_keep_missing_slots_and_isolated_points(records, qt):
    pg = pytest.importorskip('pyqtgraph')
    import numpy as np
    rows = tuple(replace(records[3][i], stock_percent=value, supply_percent=None, stock_value=None)
                 for i, value in enumerate((10, 20, None, 40, None, 60, 70)))
    dialog = make_dialog(records, qt, rows=rows)
    items = dialog._percent_plot.listDataItems()
    assert len(items) == 1 and isinstance(items[0], pg.PlotDataItem)
    item = items[0]
    x, y = item.getData()
    assert list(x) == list(range(1, 8))
    assert np.isnan(y[[2, 4]]).all() and list(y[[0, 1, 3, 5, 6]]) == [10, 20, 40, 60, 70]
    assert item.opts['connect'] == 'finite' and not item.opts['skipFiniteCheck']
    assert item.opts['symbol'] is not None
    # Inspect the actual Qt path: no line may bridge the missing x=3 or x=5.
    path = item.curve.getPath()
    previous = None
    segments = []
    for index in range(path.elementCount()):
        element = path.elementAt(index)
        point = (element.x, element.y)
        if element.isLineTo() and previous is not None and point != previous:
            segments.append((previous, point))
        previous = point
    assert segments == [((1.0, 10.0), (2.0, 20.0)), ((6.0, 60.0), (7.0, 70.0))]
    assert any(point.pos().x() == 4 and point.pos().y() == 40 for point in item.scatter.points())


@pytest.mark.parametrize('count', [0, 1, 1000])
def test_empty_single_and_full_cap_have_all_rows_and_bounded_plot_items(records, qt, count):
    pytest.importorskip('pyqtgraph')
    rows = tuple(replace(records[3][0], id=2**53 + index + 1, stock_percent=index % 101,
                         supply_percent=None, stock_value=index, recorded_at=datetime(2026, 1, 2, tzinfo=timezone.utc)
                         - timedelta(seconds=index)) for index in range(count))
    dialog = make_dialog(records, qt, rows=rows)
    assert dialog._rows_table.rowCount() == count
    assert f'{count}' in dialog._capture_label.text()
    plots = [plot for plot in (dialog._percent_plot, dialog._value_plot) if plot is not None]
    assert sum(len(plot.listDataItems()) for plot in plots) <= 3
    if count:
        assert dialog._rows_table.item(count - 1, 0).text() == str(count)
        assert dialog._rows_table.item(count - 1, 1).text() == str(rows[-1].id)
        assert dialog._percent_plot.listDataItems()[0].getData()[0][-1] == count
        assert dialog._metrics_table.item(0, 5).text() == ('--' if count == 1 else '+90 percentage points')
    else:
        assert 'no matching' in dialog._plot_status_label.text().lower()


def test_note_only_rows_are_not_zero_coverage_and_full_note_is_literal(records, qt):
    rows = tuple(replace(row, stock_percent=None, supply_percent=None, stock_value=None) for row in records[3][:2])
    dialog = make_dialog(records, qt, rows=rows)
    assert dialog._percent_plot is None and dialog._value_plot is None
    assert 'no recorded' in dialog._plot_status_label.text().lower()
    assert [[dialog._metrics_table.item(row, column).text() for column in range(1, 6)]
            for row in range(3)] == [['0', '2', '--', '--', '--']] * 3
    dialog._rows_table.selectRow(1)
    assert dialog._note_edit.document().toRawText().replace('\u2029', '\n') == rows[1].note
    dialog._rows_table.clearSelection()
    assert dialog._note_edit.toPlainText() == ''


@pytest.mark.parametrize('value,plottable', [(2**53, True), (2**53 + 1, False), (2**53 + 2, True), (2**63 - 1, False)])
def test_value_plot_requires_exact_roundtrip_without_rounding_table_or_summary(records, qt, value, plottable):
    pytest.importorskip('pyqtgraph')
    rows = (replace(records[3][0], stock_value=value), replace(records[3][1], stock_value=0))
    dialog = make_dialog(records, qt, rows=rows)
    assert (dialog._value_plot is not None) == plottable
    assert dialog._rows_table.item(0, 5).text() == str(value)
    assert dialog._metrics_table.item(2, 3).text() == str(value)
    assert dialog._metrics_table.item(2, 5).text() == f'-{value}'
    if not plottable:
        assert 'precision' in dialog._plot_status_label.text().lower()
        assert 'exact' in dialog._plot_status_label.text().lower()


def test_timestamp_filters_literal_context_and_small_scroll_layout(records, qt):
    first = replace(records[3][0], recorded_at=datetime(2026, 1, 2, 5, 30, 0, 123456,
                                                     tzinfo=timezone(timedelta(hours=5, minutes=30))),
                    note='😀 <b>literal</b>\n' * 100)
    name = 'Long <b>literal</b> character\n' * 100
    filters = BusinessCheckInHistoryFilters('<b>keep</b>', date(2026, 1, 1), date(2026, 1, 4))
    dialog = make_dialog(records, qt, rows=(first,), character_name=name, filters=filters)
    dialog.resize(480, 440)
    qt.processEvents()
    assert (dialog.width(), dialog.height()) == (480, 440)
    assert dialog._scroll_area.verticalScrollBar().maximum() > 0
    assert dialog._context_label.textFormat() == Qt.TextFormat.PlainText
    assert name in dialog._context_label.text()
    assert '<b>keep</b>' in dialog._filters_label.text()
    assert dialog._filters_label.textFormat() == Qt.TextFormat.PlainText
    assert dialog._rows_table.item(0, 2).text() == '2026-01-02 00:00:00.123456 UTC'
    assert dialog._note_edit.document().toRawText().count('<b>literal</b>') == 100
    assert dialog._export_button.isVisible() and dialog._close_button.isVisible()
    assert not dialog.isModal()


def test_missing_pyqtgraph_keeps_summary_rows_notes_and_export_usable(records, qt, monkeypatch, tmp_path):
    module = trend_module()
    # ImportError is the optional-dependency path used by the production module.
    import builtins
    original_import = builtins.__import__
    def importing(name, *args, **kwargs):
        if name == 'pyqtgraph':
            raise ImportError('optional plotting unavailable')
        return original_import(name, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(builtins, '__import__', importing)
        importlib.reload(module)
    try:
        dialog = make_dialog(records, qt)
        assert dialog._rows_table.rowCount() == 28 and dialog._metrics_table.rowCount() == 3
        assert dialog._percent_plot is None and dialog._value_plot is None
        assert 'unavailable' in dialog._plot_status_label.text().lower()
        path = tmp_path / 'fallback.json'
        monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *a, **k: (str(path), ''))
        dialog._export_button.click()
        assert json.loads(path.read_text()) == dialog._trend.to_report()
    finally:
        importlib.reload(module)


def test_export_uses_fixed_snapshot_and_blocks_nested_close_or_reexport(records, qt, monkeypatch, tmp_path):
    board = make_board(records, qt)
    board._trend_button.click()
    child = board._trend_dialog
    snapshot = child._trend
    path = tmp_path / 'trend.json'
    chosen = []
    def choose(*args, **kwargs):
        chosen.append(True)
        child._export_trend()
        child.close()
        child.accept()
        child.reject()
        board.close()
        assert board.isVisible() and child.isVisible()
        with records[0]._session_scope() as db:
            db.execute(text('DELETE FROM manual_business_checkins'))
        board.refresh()
        return str(path), ''
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
    child._export_button.click()
    assert chosen == [True]
    assert json.loads(path.read_text()) == snapshot.to_report()
    assert child._trend is snapshot and not child._exporting
    child.close()
    board._trend_button.click()
    assert board._trend_dialog._trend.rows == ()


@pytest.mark.parametrize('outcome', ['cancel', 'raise', 'failure'])
def test_export_failure_or_cancel_preserves_original_file(records, qt, monkeypatch, tmp_path, outcome):
    dialog = make_dialog(records, qt)
    path = tmp_path / 'original.json'
    path.write_text('previous file')
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName',
                        lambda *a, **k: ('', '') if outcome == 'cancel' else (str(path), ''))
    def fail(*args):
        if outcome == 'raise':
            raise RuntimeError('SECRET_PATH')
        return ExportResult(False, error_message='SECRET_PATH')
    monkeypatch.setattr(dialog._exporter, 'export_business_checkins_snapshot', fail)
    dialog._export_button.click()
    assert path.read_text() == 'previous file'
    assert dialog.isVisible() and dialog._export_button.isEnabled()
    assert 'SECRET_PATH' not in dialog._status_label.text()
    if outcome != 'cancel':
        assert 'failed' in dialog._status_label.text().lower()


def test_parent_prechecks_export_before_discard_and_rechecks_child_identity(records, qt, monkeypatch):
    board = make_board(records, qt)
    board._trend_button.click()
    child = board._trend_dialog
    board._record_button.click()
    editor = board._editor
    editor._note_edit.setPlainText('unsaved draft')
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *a, **k: pytest.fail('prompt during export'))
    def choose(*args, **kwargs):
        board.close()
        board.reject()
        assert board.isVisible() and child.isVisible() and editor.isVisible()
        return '', ''
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
    child._export_button.click()
    def discard(*args, **kwargs):
        child.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert sip.isdeleted(child)
        return QtWidgets.QMessageBox.StandardButton.Discard
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', discard)
    board.close()
    assert not board.isVisible() and board._trend_dialog is None


def test_board_action_row_preserves_two_history_rows_at_1000_by_700(records, qt):
    from src.ui.styles import DarkTheme
    board = make_board(records, qt)
    board.setStyleSheet(DarkTheme.get_stylesheet())
    board.resize(1000, 700)
    qt.processEvents()
    assert (board.width(), board.height()) == (1000, 700)
    table = board._history_table
    assert table.viewport().height() >= sum(table.rowHeight(row) for row in range(2))
    assert table.viewport().rect().contains(table.visualItemRect(table.item(1, 0)))
    assert board._trend_button.geometry().top() == board._export_board_button.geometry().top()


def test_repeated_parent_owned_close_reopen_disposes_fixed_children(records, qt):
    for _ in range(3):
        board = make_board(records, qt)
        for _ in range(6):
            board._trend_button.click()
            child = board._trend_dialog
            assert child is not None and child._rows_table.rowCount() == 28
            assert child.close()
            assert board._trend_dialog is None
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert sip.isdeleted(child)
        board._trend_button.click()
        final_child = board._trend_dialog
        assert board.close()
        assert board._trend_dialog is None
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert sip.isdeleted(final_child)
        board.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert sip.isdeleted(board)


def test_plots_disable_transform_menus_but_keep_pan_and_zoom(records, qt):
    pytest.importorskip('pyqtgraph')
    dialog = make_dialog(records, qt)
    for plot in (dialog._percent_plot, dialog._value_plot):
        item = plot.getPlotItem()
        assert not item.menuEnabled()
        assert item.getContextMenus(None) is None
        assert not item.getViewBox().menuEnabled()
        assert item.getViewBox().mouseEnabled() == [True, True]
