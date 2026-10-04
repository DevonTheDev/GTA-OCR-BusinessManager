"""Manual LIVE observations through actual Qt controls and disposable SQLite."""

import copy
import os
from types import SimpleNamespace

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for live reading UI tests', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6.QtCore import QRect, Qt
from PyQt6.QtTest import QTest

from src.app import AppState
from src.game.businesses import BUSINESSES
from src.ui.widgets.business_panel import BusinessCard, BusinessPanel
from src.ui.widgets.quick_stats import ExpandedQuickStats
from tests.test_business_checkins_app_qt import app_window as app_window, legacy_rows
from tests.test_business_screen_target_app_qt import saved_rows
from tests.test_business_target_controls_qt import qt as qt, widgets as widgets


def open_live(window, qt, business_id='bunker'):
    panel = window._business_panel
    card = panel._cards[business_id]
    assert hasattr(card, '_live_reading_button'), 'Each business needs Enter live reading…'
    assert card._live_reading_button.text() == 'Enter live reading…'
    card._live_reading_button.click()
    qt.processEvents()
    editor = panel._live_reading_dialog
    assert editor is not None and editor.isVisible() and editor.isModal()
    return editor


def test_live_zero_replaces_previous_values_and_preserves_history_and_lifecycle(app_window, qt, monkeypatch):
    manager, window, repository, alpha, beta = app_window
    repository.save_business_checkin(alpha, 'bunker', 12, 34, 56000, 'Saved observation')
    repository.set_manual_business_pin(alpha, 'bunker', True)
    records = legacy_rows(repository), saved_rows(repository)
    config = manager._settings._config_path.read_bytes()
    manager.set_business_screen_target('acid_lab')
    manager.update_business_state('bunker', 95, 5, 123456)
    # Fill the optimizer's recommendation cache before applying a new observation.
    assert any('Bunker' in rec.action for rec in manager.recommendations)
    lifecycle = manager.state, manager.data.character_id, manager.business_screen_target
    monkeypatch.setattr(manager, 'start', lambda: pytest.fail('Live entry started capture'))
    monkeypatch.setattr(manager, '_initialize_database', lambda: pytest.fail('Live entry opened history'))
    editor = open_live(window, qt)
    assert editor._stock_edit.text() == editor._supply_edit.text() == editor._value_edit.text() == ''
    assert editor._context_label.textFormat() == Qt.TextFormat.PlainText
    assert 'Bunker' in editor._context_label.text()
    editor._value_edit.setText('0')
    editor._apply_button.click()
    assert not editor.isVisible()
    state = manager.get_business_state('bunker')
    assert (state['stock'], state['supply'], state['value']) == (None, None, 0)
    assert state['identity_source'] == 'manual_entry'
    card = window._business_panel._cards['bunker']
    assert (card._stock_bar.format(), card._supply_bar.format(), card._value_label.text()) == (
        'Unknown', 'Unknown', '$0',
    )
    assert 'Manual entry' in card._status_label.text()
    assert not any('Bunker' in rec.action for rec in manager.recommendations)
    assert (manager.state, manager.data.character_id, manager.business_screen_target) == lifecycle
    assert (legacy_rows(repository), saved_rows(repository)) == records
    assert manager._settings._config_path.read_bytes() == config
    assert window._business_panel._live_reading_dialog is None


@pytest.mark.parametrize('field,text,expected', [
    ('stock', '0', (0, None, None)),
    ('supply', '0', (None, 0, None)),
    ('value', '9223372036854775807', (None, None, 9223372036854775807)),
])
def test_partial_input_and_reopen_are_blank(app_window, qt, field, text, expected):
    manager, window, _, _, _ = app_window
    editor = open_live(window, qt)
    getattr(editor, f'_{field}_edit').setText(text)
    editor._apply_button.click()
    state = manager.get_business_state('bunker')
    assert (state['stock'], state['supply'], state['value']) == expected
    reopened = open_live(window, qt)
    assert reopened is not editor
    assert reopened._stock_edit.text() == reopened._supply_edit.text() == reopened._value_edit.text() == ''
    reopened.reject()


@pytest.mark.parametrize('field,text', [
    ('stock', ''), ('stock', '101'), ('supply', '-1'), ('stock', '1.5'),
    ('stock', '١'), ('value', '1,000'), ('value', '$1'),
    ('value', '9223372036854775808'), ('value', '1' * 5000),
])
def test_invalid_or_blank_input_keeps_exact_draft_and_live_state(app_window, qt, field, text):
    manager, window, _, _, _ = app_window
    before = copy.deepcopy(manager.data.business_states)
    editor = open_live(window, qt)
    control = getattr(editor, f'_{field}_edit')
    control.setText(text)
    editor._apply_button.click()
    assert editor.isVisible() and editor._apply_button.isEnabled()
    assert control.text() == text
    assert 'not applied' in editor._status_label.text().lower()
    assert manager.data.business_states == before


@pytest.mark.parametrize('dismiss', ['cancel', 'escape', 'close', 'accept'])
def test_dirty_dismissal_requires_discard_and_never_applies(app_window, qt, monkeypatch, dismiss):
    manager, window, _, _, _ = app_window
    before = copy.deepcopy(manager.data.business_states)
    editor = open_live(window, qt)
    editor._stock_edit.setText('0')
    answers = []
    def keep(*args, **kwargs):
        answers.append(True)
        return QtWidgets.QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', keep)
    if dismiss == 'cancel':
        editor._cancel_button.click()
    elif dismiss == 'escape':
        QTest.keyClick(editor, Qt.Key.Key_Escape)
    elif dismiss == 'close':
        editor.close()
    else:
        editor.accept()
    assert answers == [True]
    assert editor.isVisible() and editor._stock_edit.text() == '0'
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                        lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)
    editor.close()
    assert not editor.isVisible() and window._business_panel._live_reading_dialog is None
    editor._apply()
    assert manager.data.business_states == before


def test_apply_failure_keeps_draft_and_retry_is_single_flight(app_window, qt, monkeypatch, caplog):
    manager, window, _, _, _ = app_window
    before = copy.deepcopy(manager.data.business_states)
    editor = open_live(window, qt)
    editor._stock_edit.setText('70')
    apply = manager.set_manual_business_reading
    def fail(*args, **kwargs):
        raise RuntimeError('PRIVATE_APP_ERROR')
    monkeypatch.setattr(manager, 'set_manual_business_reading', fail)
    editor._apply_button.click()
    assert editor.isVisible() and editor._stock_edit.text() == '70'
    assert editor._apply_button.isEnabled()
    assert manager.data.business_states == before
    assert 'PRIVATE' not in editor._status_label.text() + caplog.text
    calls = []
    def apply_once(*args, **kwargs):
        calls.append(args)
        editor._apply()
        editor.reject()
        editor.close()
        return apply(*args, **kwargs)
    monkeypatch.setattr(manager, 'set_manual_business_reading', apply_once)
    editor._apply_button.click()
    editor._apply()
    assert len(calls) == 1 and not editor.isVisible()
    assert manager.get_business_state('bunker')['stock'] == 70


def test_successful_apply_with_refresh_failure_closes_and_reports_success(app_window, qt, monkeypatch, caplog):
    manager, window, _, _, _ = app_window
    panel = window._business_panel
    panel._timer.stop()
    editor = open_live(window, qt)
    editor._supply_edit.setText('50')
    def fail():
        raise RuntimeError('PRIVATE_REFRESH_ERROR')
    monkeypatch.setattr(panel, '_update_display', fail)
    editor._apply_button.click()
    assert manager.get_business_state('bunker')['supply'] == 50
    assert not editor.isVisible() and panel._live_reading_dialog is None
    assert 'applied' in panel._live_reading_status_label.text().lower()
    assert 'could not refresh' in panel._live_reading_status_label.text().lower()
    assert 'PRIVATE' not in panel._live_reading_status_label.text() + caplog.text
    editor._apply()


@pytest.mark.parametrize('clear_refresh_fails', [False, True])
def test_apply_clear_apply_shows_only_the_latest_success(app_window, qt, monkeypatch, clear_refresh_fails):
    manager, window, _, _, _ = app_window
    panel = window._business_panel
    panel._timer.stop()
    editor = open_live(window, qt)
    editor._value_edit.setText('0')
    editor._apply_button.click()
    assert not panel._live_reading_status_label.isHidden()
    with monkeypatch.context() as patch:
        if clear_refresh_fails:
            def fail():
                raise RuntimeError('Unavailable display')
            patch.setattr(panel, '_update_display', fail)
        panel._clear_readings_button.click()
    assert manager.get_business_state('bunker') is None
    assert panel._live_reading_status_label.isHidden()
    assert not panel._clear_readings_status_label.isHidden()
    assert 'cleared' in panel._clear_readings_status_label.text().lower()
    editor = open_live(window, qt)
    editor._stock_edit.setText('50')
    editor._apply_button.click()
    assert manager.get_business_state('bunker')['stock'] == 50
    assert panel._clear_readings_status_label.isHidden()
    assert not panel._live_reading_status_label.isHidden()


def test_one_editor_has_fixed_business_despite_other_selection_changes(app_window, qt):
    manager, window, _, _, _ = app_window
    panel = window._business_panel
    editor = open_live(window, qt)
    editor._stock_edit.setText('63')
    panel._open_live_reading('acid_lab')
    assert panel._live_reading_dialog is editor
    assert editor._business_id == 'bunker' and editor._stock_edit.text() == '63'
    manager.set_business_screen_target('acid_lab')
    panel._sync_business_screen_target()
    editor._apply_button.click()
    assert manager.get_business_state('bunker')['stock'] == 63
    assert manager.get_business_state('acid_lab') is None
    assert manager.business_screen_target == 'acid_lab'


def test_supplies_are_not_applicable_for_catalog_business_without_them(app_window, qt):
    manager, window, _, _, _ = app_window
    editor = open_live(window, qt, 'special_cargo')
    assert not editor._supply_edit.isEnabled()
    assert editor._supply_edit.placeholderText() == 'Not applicable'
    editor._stock_edit.setText('0')
    editor._apply_button.click()
    assert manager.get_business_state('special_cargo')['supply'] is None
    card = window._business_panel._cards['special_cargo']
    assert card._supply_bar.format() == 'Not applicable'
    assert 'supplies' not in card._status_label.text().lower()


def test_card_unknown_zero_and_estimated_values_are_distinct(widgets):
    card = widgets(BusinessCard, BUSINESSES['bunker'])
    card.update_data(None, None, None, identity_source='manual_entry')
    for bar in (card._stock_bar, card._supply_bar):
        assert (bar.minimum(), bar.maximum(), bar.value(), bar.format()) == (0, 100, 0, 'Unknown')
    assert card._value_label.text() == 'Unknown'
    assert '#AAA' in card._status_label.styleSheet()
    assert 'producing' not in card._status_label.text().lower()
    assert 'needs supplies' not in card._status_label.text().lower()
    card.update_data(0, 0, 0)
    assert card._stock_bar.text() == card._supply_bar.text() == '0%'
    assert card._value_label.text() == '$0'
    card.update_data(50, None, None)
    assert card._value_label.text().startswith('~$')
    assert card._supply_bar.format() == 'Unknown'
    card.set_not_tracked()
    assert card._stock_bar.format() == 'Unknown'
    assert card._value_label.text() == '--'
    assert 'enter' in card._status_label.text().lower()
    assert '#666' in card._status_label.styleSheet()


@pytest.mark.parametrize('state', [AppState.STOPPED, AppState.PAUSED, AppState.RUNNING])
def test_keyboard_entry_preserves_tracking_state(app_window, qt, state):
    manager, window, _, _, _ = app_window
    manager._state = state
    button = window._business_panel._cards['bunker']._live_reading_button
    button.setFocus()
    qt.processEvents()
    QTest.keyClick(button, Qt.Key.Key_Space)
    editor = window._business_panel._live_reading_dialog
    assert editor is not None and editor.isVisible()
    editor._value_edit.setFocus()
    QTest.keyClicks(editor._value_edit, '0')
    QTest.keyClick(editor._value_edit, Qt.Key.Key_Return)
    assert not editor.isVisible()
    assert manager.get_business_state('bunker')['value'] == 0
    assert manager.state == state


def test_expanded_quick_stats_orders_known_stock_and_keeps_unknown_time_honest(widgets):
    app = SimpleNamespace(data=SimpleNamespace(business_states={
        'bunker': {'stock': None, 'supply': None, 'value': 0},
        'acid_lab': {'stock': 70, 'supply': None, 'value': None},
        'special_cargo': {'stock': 10, 'supply': 0, 'value': None},
        'cocaine': {'stock': None, 'supply': 0, 'value': None},
        'vehicle_warehouse': {'stock': 0, 'supply': None, 'value': None},
    }))
    widget = widgets(ExpandedQuickStats, app)
    widget._timer.stop()
    widget._timer.deleteLater()
    widget._update_businesses()
    rows = {row._name.text(): (row._status.text(), row._time.text()) for row in widget._business_widgets}
    assert [row._status.text() for row in widget._business_widgets] == ['70%', '10%', '0%', 'Unknown', 'Unknown']
    assert rows['Bunker'] == ('Unknown', 'Unknown')
    assert rows['Cocaine Lockup'] == ('Unknown', 'Unknown')
    assert rows[BUSINESSES['special_cargo'].name[:15]][1] == 'Unknown'
    assert rows[BUSINESSES['vehicle_warehouse'].name[:15]][1] == 'Unknown'
    assert all(time != 'FULL' and time != 'Full' for _, time in rows.values())


@pytest.mark.parametrize('app', [None, SimpleNamespace(set_manual_business_reading=42)])
def test_live_entry_is_disabled_without_callable_app_boundary(widgets, app):
    panel = widgets(BusinessPanel, app)
    assert all(not card._live_reading_button.isEnabled() for card in panel._cards.values())


def test_known_stock_and_supported_low_supply_are_required_for_attention(widgets):
    app = SimpleNamespace(data=SimpleNamespace(business_states={
        'bunker': {'stock': 15, 'supply': 0},
        'cocaine': {'stock': None, 'supply': 0},
        'nightclub': {'stock': 15, 'supply': 0},
    }))
    widget = widgets(ExpandedQuickStats, app)
    widget._timer.stop()
    widget._timer.deleteLater()
    widget._update_businesses()
    rows = {row._name.text(): row._time.text() for row in widget._business_widgets}
    assert rows['Bunker'] == 'Low supplies!'
    assert rows['Cocaine Lockup'] == 'Unknown'
    assert rows[BUSINESSES['nightclub'].name[:15]] != 'Low supplies!'


def test_zero_observed_value_never_invites_a_sale(widgets):
    card = widgets(BusinessCard, BUSINESSES['bunker'])
    for stock in (50, 100):
        card.update_data(stock, None, 0)
        assert card._value_label.text() == '$0'
        assert 'sell' not in card._status_label.text().lower()


@pytest.mark.parametrize('width,height', [(900, 650), (1000, 700)])
def test_card_large_value_and_long_name_fit_without_horizontal_scroll(app_window, qt, width, height):
    manager, window, _, _, _ = app_window
    for business_id in BUSINESSES:
        manager.set_manual_business_reading(business_id, value=9223372036854775807)
    panel = window._business_panel
    panel._update_display()
    window.resize(width, height)
    qt.processEvents()
    assert window.size().width() == width
    scroll = panel.findChild(QtWidgets.QScrollArea)
    assert scroll.horizontalScrollBar().maximum() == 0
    for card in panel._cards.values():
        assert card._value_label.toolTip() == '$9,223,372,036,854,775,807'
        for control in (card._value_label, card._status_label, card._live_reading_button):
            assert card.rect().contains(control.mapTo(card, control.rect().topLeft()))
            assert card.rect().contains(control.mapTo(card, control.rect().bottomRight()))
        for label in card.findChildren(QtWidgets.QLabel):
            content = label.contentsRect()
            needed = label.fontMetrics().boundingRect(
                QRect(0, 0, content.width(), 1000), int(Qt.TextFlag.TextWordWrap), label.text(),
            )
            assert content.height() >= needed.height(), (label.text(), content, needed)
            assert content.width() >= needed.width(), (label.text(), content, needed)
