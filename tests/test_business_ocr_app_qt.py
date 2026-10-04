"""Synthetic business OCR through the actual app and offscreen business cards."""

import logging
import os
from types import SimpleNamespace

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for native business OCR integration', allow_module_level=True)

pytest.importorskip('PyQt6.QtWidgets')

from src.capture.regions import ScreenRegions
from tests.test_business_checkins_app_qt import app_window as app_window, legacy_rows, qt as qt


@pytest.mark.parametrize('stock_text,supply_text,stock,supply', [
    ('5/10', '3/4', 50, 75),
    ('10/10', '0/4', 100, 0),
    ('1/3', '2/3', 33, 66),
    ('75%', '25%', 75, 25),
])
def test_normalized_live_reading_reaches_business_bars_without_writing_history(
    app_window, qt, monkeypatch, caplog, stock_text, supply_text, stock, supply,
):
    manager, window, repository, alpha, _beta = app_window
    repository.save_business_checkin(alpha, 'bunker', stock_percent=12, note='Manual observation')
    manual = repository.get_business_checkin_history(alpha, 'bunker')
    legacy = legacy_rows(repository)
    scheduled = manager._optimizer._scheduler.schedule_sell('bunker', estimated_value=987654)
    regions = ScreenRegions()
    parts = [f'Bunker Stock: {stock_text}', f'Supplies: {supply_text}', 'Value: $123,456']
    region_text = dict(zip(regions.get_business_regions().values(), parts))
    captures = []
    recognitions = []

    def capture_region(region, *, wait_for_rate):
        captures.append((region, wait_for_rate))
        return region_text[region]

    def recognize(image, **options):
        recognitions.append((image, options))
        return SimpleNamespace(text=image)

    monkeypatch.setattr(manager, '_capture', SimpleNamespace(
        regions=regions, capture_region=capture_region, close=lambda: None,
    ))
    monkeypatch.setattr(manager, '_ocr', SimpleNamespace(is_available=True, recognize_preprocessed=recognize))
    caplog.set_level(logging.INFO)
    manager._process_business_computer()
    window._business_panel._update_display()
    qt.processEvents()

    card = window._business_panel._cards['bunker']
    assert (card._stock_bar.value(), card._supply_bar.value()) == (stock, supply)
    assert card._value_label.text() == '$123.5K'
    cached = manager.get_business_state('bunker')
    assert (cached['stock'], cached['supply'], cached['value']) == (stock, supply, 123456)
    optimized = manager._optimizer._business_states['bunker']
    assert (optimized.stock_percent, optimized.supply_percent, optimized.estimated_value) == (stock, supply, 123456)
    assert captures == [(region, False) for region in regions.get_business_regions().values()]
    assert recognitions == [(text, {'invert': True, 'scale': 2.0}) for text in parts]
    assert f'Business detected: BUNKER - Stock: {stock}%, Supply: {supply}%, Value: $123,456' in caplog.text
    assert 'Error processing business computer' not in caplog.text
    assert manager._optimizer._scheduler.get_next_action() is scheduled
    after = repository.get_business_checkin_history(alpha, 'bunker')
    # The page's capture timestamp is fresh on every read; compare stored rows.
    assert after.rows == manual.rows and after.total == manual.total
    assert legacy_rows(repository) == legacy
