"""Clear disposable live observations through actual offscreen business controls."""

import copy
import os
from types import SimpleNamespace

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for clear live readings controls', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6.QtCore import QRect, Qt
from PyQt6.QtTest import QSignalSpy, QTest

from src.app import AppState
from src.capture.regions import ScreenRegions
from src.ui.widgets.business_panel import BusinessPanel
from tests.test_business_checkins_app_qt import app_window as app_window, legacy_rows
from tests.test_business_screen_target_app_qt import saved_rows
from tests.test_business_target_controls_qt import (
    TargetApp, qt as qt, widgets as widgets,
)


class ClearApp(TargetApp):
    """The UI boundary exposes disposable observations and a separate target."""

    def __init__(self, state=AppState.STOPPED):
        super().__init__('acid_lab')
        self.state = state
        self.is_running = state == AppState.RUNNING
        self.clear_calls = 0
        self.clear_failure = None
        self.states = {
            'bunker': {'stock': 73, 'supply': 22, 'value': 123456,
                       'identity_source': 'ocr_text'},
            'acid_lab': {'stock': 40, 'supply': 60, 'value': 50000,
                         'identity_source': 'selected_target'},
        }

    def clear_business_readings(self):
        self.clear_calls += 1
        if self.clear_failure:
            raise self.clear_failure
        self.states.clear()

    def pause(self):
        pytest.fail('Clearing live readings must not pause capture')

    def resume(self):
        pytest.fail('Clearing live readings must not resume capture')

    def stop(self):
        pytest.fail('Clearing live readings must not stop capture')

    def reset_session(self):
        pytest.fail('Clearing live readings must not reset the session')


def clear_button(panel):
    matches = [button for button in panel.findChildren(QtWidgets.QPushButton)
               if button.text() == 'Clear live readings']
    assert len(matches) == 1, 'Businesses needs an explicit Clear live readings button'
    return matches[0]


def card_values(panel):
    return {business_id: (card._stock_bar.value(), card._supply_bar.value(),
                          card._value_label.text(), card._status_label.text())
            for business_id, card in panel._cards.items()}


@pytest.mark.parametrize('state', [AppState.STOPPED, AppState.PAUSED, AppState.RUNNING])
def test_clear_refreshes_every_card_immediately_and_keeps_target(widgets, state, monkeypatch):
    app = ClearApp(state)
    panel = widgets(BusinessPanel, app)
    panel._timer.stop()
    panel._update_display()
    assert panel._cards['bunker']._stock_bar.value() == 73
    assert panel._cards['acid_lab']._value_label.text() == '$50.0K'
    emitted = QSignalSpy(panel._business_target_combo.currentIndexChanged)
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                        lambda *a, **k: pytest.fail('Disposable observations need no modal'))

    button = clear_button(panel)
    assert button.isEnabled()
    button.click()

    # No timer tick or event-loop turn is needed for the first empty display.
    assert app.clear_calls == 1 and app.states == {}
    assert app.state == state and app.is_running == (state == AppState.RUNNING)
    assert app.business_screen_target == 'acid_lab'
    assert panel._business_target_combo.currentData() == 'acid_lab'
    assert app.target_changes == [] and len(emitted) == 0
    assert panel._checkins_dialog is None
    assert set(card_values(panel).values()) == {
        (0, 0, '--', 'Not tracked - visit business to update'),
    }
    assert 'Live readings cleared' in panel._clear_readings_status_label.text()
    assert 'OCR' in panel._clear_readings_status_label.text()
    button.click()
    assert app.clear_calls == 2 and button.isEnabled()


def test_clear_is_keyboard_accessible_and_next_observation_refills_card(widgets, qt):
    app = ClearApp()
    panel = widgets(BusinessPanel, app)
    panel._timer.stop()
    panel.show()
    button = clear_button(panel)
    assert button.accessibleName() == 'Clear live readings'
    assert button.focusPolicy() & Qt.FocusPolicy.TabFocus
    button.setFocus()
    qt.processEvents()
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert app.clear_calls == 1 and app.states == {}

    app.states['acid_lab'] = {'stock': 20, 'supply': 80, 'value': 45000,
                              'identity_source': 'selected_target'}
    panel._update_display()
    card = panel._cards['acid_lab']
    assert (card._stock_bar.value(), card._supply_bar.value(), card._value_label.text()) == (
        20, 80, '$45.0K',
    )
    assert 'Selected target' in card._status_label.text()
    assert panel._cards['bunker']._value_label.text() == '--'


@pytest.mark.parametrize('app', [None, TargetApp(), SimpleNamespace(clear_business_readings=42)])
def test_clear_is_disabled_without_a_callable_app_method(widgets, app):
    panel = widgets(BusinessPanel, app)
    panel._timer.stop()
    button = clear_button(panel)
    assert not button.isEnabled()
    button.click()
    assert not panel._clear_readings_status_label.text()


def test_failed_clear_keeps_cards_and_replaces_success_with_fixed_error(widgets, qt, caplog):
    app = ClearApp()
    panel = widgets(BusinessPanel, app)
    panel._timer.stop()
    panel.show()
    clear_button(panel).click()
    assert 'Live readings cleared' in panel._clear_readings_status_label.text()
    app.states['bunker'] = {'stock': 70, 'supply': 80, 'value': 123456}
    panel._update_display()
    before = card_values(panel)
    states = copy.deepcopy(app.states)
    app.clear_failure = RuntimeError('private <b>exception details</b>')

    clear_button(panel).click()
    qt.processEvents()

    label = panel._clear_readings_status_label
    assert label.isVisible()
    assert label.text() == 'Live readings could not be cleared. Try again.'
    assert label.textFormat() == Qt.TextFormat.PlainText and label.wordWrap()
    assert 'private' not in label.text() and 'private' not in caplog.text
    assert card_values(panel) == before and app.states == states
    assert app.business_screen_target == 'acid_lab'
    assert clear_button(panel).isEnabled()

    app.clear_failure = None
    clear_button(panel).click()
    assert app.clear_calls == 3 and app.states == {}
    assert 'Live readings cleared' in label.text()


def test_successful_clear_with_failed_refresh_reports_actual_outcome(widgets, monkeypatch, caplog):
    app = ClearApp()
    panel = widgets(BusinessPanel, app)
    panel._timer.stop()
    panel._update_display()
    before = card_values(panel)

    def unreadable_state(business_id):
        raise RuntimeError('private refresh details')

    monkeypatch.setattr(app, 'get_business_state', unreadable_state)
    clear_button(panel).click()

    assert app.clear_calls == 1 and app.states == {}
    assert app.business_screen_target == 'acid_lab'
    assert card_values(panel) == before
    assert panel._clear_readings_status_label.text() == (
        'Live readings cleared, but the cards could not refresh. Try again.'
    )
    assert 'private' not in panel._clear_readings_status_label.text()
    assert 'private' not in caplog.text

    monkeypatch.setattr(app, 'get_business_state', lambda business_id: None)
    clear_button(panel).click()
    assert app.clear_calls == 2
    assert panel._cards['bunker']._value_label.text() == '--'
    assert 'could not refresh' not in panel._clear_readings_status_label.text()


@pytest.mark.parametrize('width,height', [(900, 650), (1000, 700)])
def test_clear_guidance_and_header_controls_fit_available_panel(widgets, qt, width, height):
    panel = widgets(BusinessPanel, ClearApp())
    panel._timer.stop()
    panel.resize(width, height)
    panel.show()
    clear_button(panel).click()
    qt.processEvents()

    assert panel.size().width() == width and panel.size().height() == height
    assert panel.minimumSizeHint().width() <= width
    assert panel.minimumSizeHint().height() <= height
    help_label = panel._clear_readings_help_label
    help_text = help_label.text().lower()
    assert all(word in help_text for word in ('clears', 'live', 'stock', 'supply', 'value'))
    assert 'all businesses' in help_text and 'ocr' in help_text and 'refill' in help_text
    assert 'check-ins' in help_text
    controls = (clear_button(panel), panel._manual_checkins_button,
                panel._business_target_combo, panel._business_target_help_label,
                help_label, panel._clear_readings_status_label)
    for control in controls:
        assert panel.rect().contains(control.mapTo(panel, control.rect().topLeft()))
        assert panel.rect().contains(control.mapTo(panel, control.rect().bottomRight()))
    assert not clear_button(panel).geometry().intersects(panel._manual_checkins_button.geometry())
    for label in (help_label, panel._clear_readings_status_label,
                  panel._business_target_help_label):
        assert label.wordWrap() and label.textFormat() == Qt.TextFormat.PlainText
        content = label.contentsRect()
        needed = label.fontMetrics().boundingRect(
            QRect(0, 0, content.width(), 1000), int(Qt.TextFlag.TextWordWrap), label.text(),
        )
        assert content.height() >= needed.height(), (label.text(), content, needed)
        assert content.width() >= needed.width(), (label.text(), content, needed)


@pytest.mark.parametrize('width,height', [(900, 650), (1000, 700)])
def test_real_window_clear_preserves_saved_data_then_ocr_refills_selected_card(
    app_window, qt, monkeypatch, width, height,
):
    manager, window, repository, alpha, _beta = app_window
    repository.save_business_checkin(alpha, 'bunker', 12, 34, 56000, 'Saved observation')
    repository.set_manual_business_pin(alpha, 'bunker', True)
    records = legacy_rows(repository), saved_rows(repository)
    settings = manager._settings._config_path.read_bytes()
    manager.set_business_screen_target('acid_lab')
    manager.update_business_state('bunker', 95, 0, 123456)
    manager._business_parser.parse('Bunker Stock: 95% Supplies: 0% Value: $123,456')
    panel = window._business_panel
    panel._timer.stop()
    panel._update_display()
    recommendation_panel = window._recommendations
    recommendation_panel._timer.stop()
    recommendation_panel._update_display()
    assert any('Bunker' in card._action_label.text() and not card.isHidden()
               for card in recommendation_panel._cards)
    window.resize(width, height)
    qt.processEvents()
    regions = ScreenRegions()
    contents = dict(zip(regions.get_business_regions().values(),
                        ['Stock: 5/10', 'Supplies: 3/4', 'Value: $123,456']))
    monkeypatch.setattr(manager, '_capture', SimpleNamespace(
        regions=regions, capture_region=lambda region, **kwargs: contents[region],
        close=lambda: None,
    ))
    monkeypatch.setattr(manager, '_ocr', SimpleNamespace(
        is_available=True,
        recognize_preprocessed=lambda image, **kwargs: SimpleNamespace(text=image),
    ))
    monkeypatch.setattr(manager, 'start', lambda: pytest.fail('Clear must not start capture'))
    monkeypatch.setattr(manager, 'pause', lambda: pytest.fail('Clear must not pause capture'))
    monkeypatch.setattr(manager, '_initialize_database',
                        lambda: pytest.fail('Clear must not initialize saved history'))

    clear_button(panel).click()

    assert manager.data.business_states == {}
    assert manager._optimizer._business_states == {}
    assert manager._business_parser.get_all_last_readings() == {}
    assert manager.business_screen_target == 'acid_lab' and manager.state == AppState.STOPPED
    assert manager._repository is repository
    assert panel._cards['bunker']._value_label.text() == '--'
    assert (legacy_rows(repository), saved_rows(repository)) == records
    assert manager._settings._config_path.read_bytes() == settings
    recommendation_panel._update_display()
    assert all('Bunker' not in card._action_label.text() for card in recommendation_panel._cards
               if not card.isHidden())
    assert all(rec.business_type is None for rec in manager.recommendations)

    qt.processEvents()
    assert window.width() == width and window.height() == height
    for control in (clear_button(panel), panel._manual_checkins_button,
                    panel._business_target_combo, panel._business_target_help_label,
                    panel._clear_readings_help_label, panel._clear_readings_status_label):
        assert window.rect().contains(control.mapTo(window, control.rect().topLeft()))
        assert window.rect().contains(control.mapTo(window, control.rect().bottomRight()))
        assert control.isVisibleTo(window)
    for label in (panel._clear_readings_help_label, panel._clear_readings_status_label,
                  panel._business_target_help_label):
        content = label.contentsRect()
        needed = label.fontMetrics().boundingRect(
            QRect(0, 0, content.width(), 1000), int(Qt.TextFlag.TextWordWrap), label.text(),
        )
        assert content.height() >= needed.height(), (label.text(), content, needed)
        assert content.width() >= needed.width(), (label.text(), content, needed)

    manager._process_business_computer()
    panel._update_display()

    assert manager.get_business_state('acid_lab')['identity_source'] == 'selected_target'
    card = panel._cards['acid_lab']
    assert (card._stock_bar.value(), card._supply_bar.value(), card._value_label.text()) == (
        50, 75, '$123.5K',
    )
    assert 'Selected target' in card._status_label.text()
    assert panel._business_target_combo.currentData() == 'acid_lab'
    assert panel._cards['bunker']._value_label.text() == '--'
    assert (legacy_rows(repository), saved_rows(repository)) == records
    assert manager._settings._config_path.read_bytes() == settings
