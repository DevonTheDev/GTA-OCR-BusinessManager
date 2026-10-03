"""Opt-in native Qt/pyqtgraph regression smoke tests, without screen capture.

Run with GTA_RUN_QT_TESTS=1 and QT_QPA_PLATFORM=offscreen after installing the
repository's existing UI dependencies. Ordinary offline runs skip this module.
"""

import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for native Qt smoke tests', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
pg = pytest.importorskip('pyqtgraph')
QApplication = QtWidgets.QApplication

from src.app import GTABusinessManager
from src.config.settings import Settings
from src.detection.parsers.money_parser import MoneyReading
from src.game.activities import ActivityType
from src.tracking import session as session_module
from src.ui.widgets import charts


@pytest.fixture(scope='module')
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def case(tmp_path, monkeypatch, qt):
    clock = SimpleNamespace(now=datetime(2026, 1, 1, 12))
    fake = SimpleNamespace(now=lambda: clock.now)
    monkeypatch.setattr(session_module, 'datetime', fake)
    # Baseline charts have their own wall clock; fixed charts use session duration.
    monkeypatch.setattr(charts, 'datetime', fake, raising=False)
    app = GTABusinessManager(Settings(tmp_path / 'config.yaml'))
    app._session_tracker.start_session()
    app._process_money_change(MoneyReading(total=100_000))
    return app, clock, qt


def earning(case):
    app, clock, qt = case
    chart = charts.EarningsChart(app)
    chart._timer.stop()
    clock.now += timedelta(minutes=10)
    app._process_money_change(MoneyReading(total=150_000))
    chart._update_chart()
    chart.resize(480,280)
    chart.show()
    qt.processEvents()
    return chart


def test_native_earnings_clears_points_on_app_reset(case):
    app, clock, qt = case
    chart = earning(case)
    try:
        assert chart._curve.getData()[1][-1] == 50_000
        app.reset_session()
        chart._update_chart()
        qt.processEvents()
        x, y = chart._curve.getData()
        assert y is None or len(y) == 0
        clock.now += timedelta(minutes=2)
        app._process_money_change(MoneyReading(total=155_000))
        chart._update_chart()
        qt.processEvents()
        x, y = chart._curve.getData()
        assert x.tolist() == [2, 2]
        assert y.tolist() == [5000, 5000]
        assert not chart.grab().isNull()
    finally:
        chart.close()


def test_native_earnings_endpoint_stops_with_session(case):
    app, clock, qt = case
    chart = earning(case)
    try:
        app._session_tracker.end_session()
        clock.now += timedelta(hours=1)
        chart._update_chart()
        qt.processEvents()
        x, y = chart._curve.getData()
        assert x[-1] == 10
        assert y[-1] == 50_000
    finally:
        chart.close()


def test_native_breakdown_clears_bars_and_labels_without_data(case):
    app, clock, qt = case
    activity = app._activity_tracker.start_activity(ActivityType.CONTACT_MISSION, 'Fixture')
    activity.started_at -= timedelta(minutes=5)
    app._activity_tracker.complete_activity(True,1000)
    clock.now += timedelta(minutes=10)
    app._recalculate_analytics(force=True)
    chart = charts.ActivityBreakdownChart(app)
    chart._timer.stop()
    try:
        chart._update_chart()
        assert any(isinstance(item,pg.BarGraphItem) for item in chart._plot.items())
        app._activity_tracker._completed_activities.clear()
        app._invalidate_analytics()
        chart._update_chart()
        assert not any(isinstance(item,pg.BarGraphItem) for item in chart._plot.items())
        assert not any(chart._plot.getAxis('bottom')._tickLevels)
    finally:
        chart.close()
