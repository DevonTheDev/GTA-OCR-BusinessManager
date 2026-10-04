"""Explicit live business targeting controls using native offscreen widgets."""

import copy
import os
from datetime import datetime, timedelta, timezone

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for business target controls', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6.QtCore import QCoreApplication, QEvent, QRect, Qt
from PyQt6.QtTest import QSignalSpy

from src.detection.parsers.business_parser import BusinessType
from src.game.businesses import BUSINESSES
from src.ui.widgets import business_panel as business_panel_module
from src.ui.widgets.business_panel import BusinessCard, BusinessPanel
from tests.test_business_checkins_app_qt import app_window as app_window


class TargetApp:
    """Small app protocol: selection has no capture, history, or settings effects."""

    def __init__(self, target=None):
        self._target = target
        self.target_changes = []
        self.states = {}
        self.is_running = False

    @property
    def business_screen_target(self):
        return self._target

    def set_business_screen_target(self, business_id):
        if business_id is not None and business_id not in BUSINESSES:
            raise ValueError('Unsupported business')
        self.target_changes.append(business_id)
        self._target = business_id

    def get_business_state(self, business_id):
        return copy.deepcopy(self.states.get(business_id))

    def start(self):
        pytest.fail('Choosing a business target must not start capture')

    @property
    def history_repository(self):
        pytest.fail('Choosing a business target must not open saved history')


@pytest.fixture
def qt(native_qt_application):
    return native_qt_application


@pytest.fixture
def widgets(qt):
    owned = []

    def make(kind, *args):
        widget = kind(*args)
        owned.append(widget)
        return widget

    yield make
    for widget in owned:
        if isinstance(widget, BusinessPanel):
            widget._timer.stop()
            widget._timer.deleteLater()
        widget.hide()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_selector_maps_exact_catalog_ids_and_names(widgets):
    app = TargetApp()
    panel = widgets(BusinessPanel, app)
    assert hasattr(panel, '_business_target_combo'), 'Businesses needs an explicit screen target selector'
    combo = panel._business_target_combo
    assert not combo.isEditable()
    assert combo.count() == len(BUSINESSES) + 1 == 12
    assert (combo.itemText(0), combo.itemData(0)) == ('Automatic', None)
    assert combo.currentIndex() == 0 and combo.isEnabled()
    for index, (business_id, business) in enumerate(BUSINESSES.items(), 1):
        assert (combo.itemText(index), combo.itemData(index)) == (business.name, business_id)
        assert BusinessType[business_id.upper()]
        combo.setCurrentIndex(index)
        assert app.business_screen_target == business_id
    assert app.target_changes == list(BUSINESSES)
    assert combo.findData('hangar') == -1 and combo.findData('auto_shop') == -1


def test_stopped_selection_leaves_live_cards_and_saved_workflows_untouched(widgets):
    app = TargetApp()
    app.states['bunker'] = {'stock': 73, 'supply': 22, 'value': 123456,
                            'identity_source': 'ocr_text'}
    panel = widgets(BusinessPanel, app)
    panel._update_display()
    card = panel._cards['bunker']
    before = (card._stock_bar.value(), card._supply_bar.value(),
              card._value_label.text(), card._status_label.text())
    states = copy.deepcopy(app.states)
    combo = panel._business_target_combo
    combo.setCurrentIndex(combo.findData('acid_lab'))
    assert app.business_screen_target == 'acid_lab'
    assert not app.is_running
    assert panel._checkins_dialog is None
    assert app.states == states
    panel._update_display()
    assert (card._stock_bar.value(), card._supply_bar.value(),
            card._value_label.text(), card._status_label.text()) == before
    assert panel._cards['acid_lab']._value_label.text() == '--'
    assert 'OCR text match' in card._status_label.text()


def test_initial_preselection_and_programmatic_reset_block_selector_signals(widgets):
    app = TargetApp('bunker')
    panel = widgets(BusinessPanel, app)
    combo = panel._business_target_combo
    assert combo.currentData() == 'bunker'
    assert app.target_changes == []
    emitted = QSignalSpy(combo.currentIndexChanged)
    for target in ('acid_lab', None, 'special_cargo', None):
        app.set_business_screen_target(target)
        changes = list(app.target_changes)
        panel._update_display()
        assert combo.currentData() == target
        assert app.target_changes == changes
    assert len(emitted) == 0
    combo.setCurrentIndex(combo.findData('meth'))
    assert len(emitted) == 1
    assert app.target_changes[-1] == 'meth'
    assert not combo.signalsBlocked()
    changes = list(app.target_changes)
    combo.setCurrentIndex(combo.findData('meth'))
    assert app.target_changes == changes
    combo.blockSignals(True)
    app.set_business_screen_target(None)
    panel._update_display()
    assert combo.signalsBlocked() and combo.currentData() is None
    combo.blockSignals(False)


@pytest.mark.parametrize('index', [-1, -100, 12, 999])
def test_invalid_selector_index_does_not_assign_automatic_or_a_business(widgets, index):
    app = TargetApp('bunker')
    panel = widgets(BusinessPanel, app)
    panel._on_business_target_changed(index)
    assert app.business_screen_target == 'bunker'
    assert app.target_changes == []


def test_standalone_or_legacy_panel_keeps_old_callers_usable(widgets):
    class LegacyApp:
        def get_business_state(self, business_id):
            return {'stock': 50, 'supply': 75} if business_id == 'bunker' else None

    for app in (None, LegacyApp()):
        panel = widgets(BusinessPanel, app)
        assert panel._business_target_combo.currentData() is None
        assert not panel._business_target_combo.isEnabled()
        panel._update_display()
    assert panel._cards['bunker']._stock_bar.value() == 50
    assert panel._cards['bunker']._status_label.text() == 'Consider selling'


@pytest.mark.parametrize('source,label', [('ocr_text', 'OCR text match'), ('selected_target', 'Selected target')])
def test_card_adds_allowlisted_provenance_without_losing_status_or_age(widgets, source, label):
    card = widgets(BusinessCard, BUSINESSES['bunker'])
    card.update_data(75, 0, 123456, '2m ago', identity_source=source)
    assert card._stock_bar.value() == 75 and card._supply_bar.value() == 0
    assert card._value_label.text() == '$123.5K'
    assert 'Needs supplies!' in card._status_label.text()
    assert '(Updated: 2m ago)' in card._status_label.text()
    assert label in card._status_label.text()
    assert card._status_label.textFormat() == Qt.TextFormat.PlainText
    card.set_not_tracked()
    assert card._status_label.text() == 'Not tracked · visit or enter a reading'


def test_card_old_direct_calls_keep_status_and_replace_prior_provenance(widgets):
    card = widgets(BusinessCard, BUSINESSES['bunker'])
    card.update_data(80, 75, 123456, '2m ago')
    assert card._status_label.text() == 'Ready to sell! (Updated: 2m ago)'
    card.update_data(75, 75, identity_source='selected_target')
    assert 'Selected target' in card._status_label.text()
    card.update_data(50, 75)
    assert card._status_label.text() == 'Consider selling'
    card.update_data(50, 75, identity_source='<b>Untrusted source</b>')
    assert card._status_label.text() == 'Consider selling'


@pytest.mark.parametrize('width,height', [(900, 650), (1000, 700)])
def test_target_guidance_is_plain_text_and_fits_available_panel(widgets, qt, width, height):
    panel = widgets(BusinessPanel, TargetApp())
    panel.resize(width, height)
    panel.show()
    qt.processEvents()
    assert panel.width() == width and panel.height() == height
    assert panel.minimumSizeHint().width() <= width
    guidance = panel._business_target_help_label.text().lower()
    assert 'assign' in guidance and 'recognized' in guidance
    assert 'does not verify' in guidance and 'ocr' in guidance
    assert 'labels' in guidance and 'markers' in guidance and 'bare numbers' in guidance
    assert 'stop' in guidance
    for label in (panel._business_target_label, panel._business_target_help_label):
        assert label.textFormat() == Qt.TextFormat.PlainText
    for control in (panel._business_target_label, panel._business_target_combo,
                    panel._business_target_help_label, panel._manual_checkins_button):
        assert panel.rect().contains(control.mapTo(panel, control.rect().topLeft()))
        assert panel.rect().contains(control.mapTo(panel, control.rect().bottomRight()))


def test_panel_passes_observation_provenance_without_relabeling_on_selection(widgets):
    app = TargetApp('acid_lab')
    app.states['bunker'] = {'stock': 25, 'supply': 50, 'value': 700,
                            'updated': datetime.now(timezone.utc),
                            'identity_source': 'ocr_text'}
    app.states['acid_lab'] = {'stock': 50, 'supply': 75, 'identity_source': 'selected_target'}
    panel = widgets(BusinessPanel, app)
    panel._update_display()
    bunker = panel._cards['bunker']._status_label
    acid = panel._cards['acid_lab']._status_label
    assert 'OCR text match' in bunker.text() and 'Updated:' in bunker.text()
    assert 'Selected target' in acid.text()
    panel._business_target_combo.setCurrentIndex(0)
    panel._update_display()
    assert 'OCR text match' in bunker.text()
    assert 'Selected target' in acid.text()


def test_main_window_existing_refresh_synchronizes_explicit_stop_without_emitting(app_window):
    manager, window, _repository, _alpha, _beta = app_window
    combo = window._business_panel._business_target_combo
    combo.setCurrentIndex(combo.findData('bunker'))
    assert manager.business_screen_target == 'bunker'
    emitted = QSignalSpy(combo.currentIndexChanged)
    manager.stop()
    window._update_ui()
    assert combo.currentData() is None
    assert len(emitted) == 0
    manager.set_business_screen_target('acid_lab')
    window._update_ui()
    assert combo.currentData() == 'acid_lab'
    assert len(emitted) == 0


@pytest.mark.parametrize('width,height', [(900, 650), (1000, 700)])
def test_tracked_card_text_has_visible_content_area_in_main_window(app_window, qt, width, height):
    _manager, window, _repository, _alpha, _beta = app_window
    panel = window._business_panel
    panel._timer.stop()
    window.resize(width, height)
    card = panel._cards['acid_lab']
    card.update_data(50, 75, 123456, '5m ago', identity_source='selected_target')
    qt.processEvents()
    scroll = next(area for area in panel.findChildren(QtWidgets.QScrollArea)
                  if area.isAncestorOf(card))
    scroll.ensureWidgetVisible(card)
    qt.processEvents()
    assert card._status_label.text() == 'Consider selling (Updated: 5m ago)\nSelected target'
    labels = card.findChildren(QtWidgets.QLabel)
    assert {label.text() for label in labels} >= {'Acid Lab', '$123.5K', 'Stock', 'Supplies'}
    for label in labels:
        content = label.contentsRect()
        flags = Qt.TextFlag.TextWordWrap if label.wordWrap() else Qt.TextFlag.TextSingleLine
        needed = label.fontMetrics().boundingRect(
            QRect(0, 0, content.width(), 1000), int(flags), label.text(),
        )
        assert content.height() >= needed.height(), (label.text(), content, needed)
        assert content.width() >= needed.width(), (label.text(), content, needed)
        assert label.isVisibleTo(window)
        assert label.visibleRegion().contains(content), label.text()


@pytest.mark.parametrize('offset_minutes', [330, -420])
@pytest.mark.parametrize('aware', [False, True], ids=['naive-local', 'aware-offset'])
def test_reading_age_preserves_local_and_aware_clock_meaning(widgets, monkeypatch, offset_minutes, aware):
    local_zone = timezone(timedelta(minutes=offset_minutes))
    local_now = datetime(2026, 10, 4, 14, 30, tzinfo=local_zone)

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return local_now.replace(tzinfo=None) if tz is None else local_now.astimezone(tz)

    monkeypatch.setattr(business_panel_module, 'datetime', FrozenClock)
    updated = local_now - timedelta(minutes=5)
    if not aware:
        updated = updated.replace(tzinfo=None)
    app = TargetApp('acid_lab')
    app.states['acid_lab'] = {
        'stock': 50, 'supply': 75, 'updated': updated, 'identity_source': 'selected_target',
    }
    panel = widgets(BusinessPanel, app)
    panel._update_display()
    assert panel._cards['acid_lab']._status_label.text() == (
        'Consider selling (Updated: 5m ago)\nSelected target'
    )
    assert app.states['acid_lab']['updated'] is updated
