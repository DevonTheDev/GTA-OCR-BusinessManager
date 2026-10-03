"""Actual chart methods with real session accounting and deterministic plot stand-ins."""

import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import app as app_module
from src.app import AppState
from src.detection.parsers.money_parser import MoneyReading
from src.game.activities import ActivityType
from src.tracking import session as session_module
from src.tracking.analytics import EarningsBreakdown
from tests.test_app_accounting import app as app


class Widget:
    def __init__(self, *args, **kwargs):
        pass


class Curve:
    def __init__(self):
        self.data = ([], [])

    def setData(self, times, values):
        self.data = (list(times), list(values))


class Axis:
    def __init__(self):
        self.ticks = []

    def setTicks(self, ticks):
        self.ticks = ticks


class Plot:
    def __init__(self):
        self.items = []
        self.axis = Axis()

    def clear(self):
        self.items.clear()

    def addItem(self, item):
        self.items.append(item)

    def getAxis(self, name):
        assert name == 'bottom'
        return self.axis


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=datetime(2026, 1, 1, 12))
    monkeypatch.setattr(session_module, 'datetime', SimpleNamespace(now=lambda: clock.now))
    return clock


def chart_classes(clock, available=True):
    """Compile original constructors/update/reset; only layout and timers are boundaries."""
    source = Path(__file__).resolve().parents[1] / 'src/ui/widgets/charts.py'
    classes = [node for node in ast.parse(source.read_text()).body
               if isinstance(node, ast.ClassDef)]
    # Native Qt layout/timer startup is intentionally outside these display tests.
    for cls in classes:
        cls.body = [node for node in cls.body if not isinstance(node, ast.FunctionDef)
                    or node.name not in {'_setup_ui', '_setup_timer'}]
    future = ast.parse('from __future__ import annotations').body
    namespace = dict(QWidget=Widget, datetime=SimpleNamespace(now=lambda: clock.now),
                     PYQTGRAPH_AVAILABLE=available,
                     pg=SimpleNamespace(BarGraphItem=lambda **kwargs: kwargs))
    exec(compile(ast.Module(body=future + classes, type_ignores=[]), str(source), 'exec'), namespace)
    earnings = namespace['EarningsChart']
    breakdown = namespace['ActivityBreakdownChart']
    combined = namespace['SessionCharts']
    earnings._setup_ui = lambda self: setattr(self, '_curve', Curve()) if available else None
    breakdown._setup_ui = lambda self: setattr(self, '_plot', Plot()) if available else None
    combined._setup_ui = lambda self: None
    earnings._setup_timer = breakdown._setup_timer = lambda self: None
    return earnings, breakdown, combined


def earning_session(app, clock):
    app._session_tracker.start_session()
    app._process_money_change(MoneyReading(total=100_000))
    app._process_money_change(MoneyReading(total=130_000))


def test_chart_created_after_session_started_uses_actual_session_age(app, clock):
    earning_session(app, clock)
    clock.now += timedelta(minutes=12)
    chart = chart_classes(clock)[0](app)
    chart._update_chart()
    assert chart._earnings_history == [(12, 30_000)]
    assert chart._curve.data == ([12, 12], [30_000, 30_000])


def test_application_reset_without_chart_callback_discards_prior_points(app, clock):
    earning_session(app, clock)
    chart = chart_classes(clock)[0](app)
    clock.now += timedelta(minutes=5)
    chart._update_chart()
    previous_stats = app.session_stats
    app.reset_session()  # Main-window Reset calls only this method.
    assert app.session_stats is not previous_stats
    chart._update_chart()
    assert chart._earnings_history == []
    assert chart._curve.data == ([], [])
    clock.now += timedelta(minutes=2)
    app._process_money_change(MoneyReading(total=132_000))
    chart._update_chart()
    assert chart._earnings_history == [(2, 2_000)]
    assert chart._curve.data == ([2, 2], [2_000, 2_000])


def test_new_session_identity_resets_even_when_earnings_match_last_sample(app, clock):
    earning_session(app, clock)
    chart = chart_classes(clock)[0](app)
    clock.now += timedelta(minutes=5)
    chart._update_chart()
    app.reset_session()
    # The next sample can arrive after the new session has earned the same amount.
    app._process_money_change(MoneyReading(total=160_000))
    clock.now += timedelta(minutes=1)
    chart._update_chart()
    assert chart._earnings_history == [(1, 30_000)]
    assert chart._curve.data == ([1, 1], [30_000, 30_000])


def test_application_start_replaces_chart_session_without_manual_reset(app, clock, monkeypatch):
    earning_session(app, clock)
    chart = chart_classes(clock)[0](app)
    clock.now += timedelta(minutes=5)
    chart._update_chart()
    previous_stats = app.session_stats
    # Replace native capture initialization/thread startup, retaining app.start itself.
    monkeypatch.setattr(app, '_initialize_components', app._session_tracker.start_session)
    monkeypatch.setattr(app_module.threading, 'Thread', lambda **kwargs: SimpleNamespace(
        start=lambda: None, is_alive=lambda: False))
    assert app.start()
    assert app.session_stats is not previous_stats
    chart._update_chart()
    assert chart._curve.data == ([], [])
    assert chart._earnings_history == []
    clock.now += timedelta(minutes=2)
    app._process_money_change(MoneyReading(total=1_000))
    app._process_money_change(MoneyReading(total=1_500))
    chart._update_chart()
    assert chart._earnings_history == [(2, 500)]


def test_completed_application_session_stops_extending_chart(app, clock):
    earning_session(app, clock)
    chart = chart_classes(clock)[0](app)
    clock.now += timedelta(minutes=5)
    chart._update_chart()
    clock.now += timedelta(minutes=2)
    app._state = AppState.RUNNING
    app.stop()
    assert app.session_stats.duration_seconds == 7 * 60
    chart._update_chart()
    before = chart._curve.data
    clock.now += timedelta(hours=1)
    chart._update_chart()
    assert chart._curve.data == before == ([5, 7], [30_000, 30_000])


def test_manual_reset_does_not_replace_the_session_clock(app, clock):
    earning_session(app, clock)
    chart = chart_classes(clock)[0](app)
    clock.now += timedelta(minutes=4)
    chart._update_chart()
    chart.reset()
    assert chart._curve.data == ([], [])
    assert chart._earnings_history == []
    clock.now += timedelta(minutes=2)
    chart._update_chart()
    assert chart._earnings_history == [(6, 30_000)]


def test_missing_stats_clear_a_previously_rendered_session(app, clock):
    earning_session(app, clock)
    chart = chart_classes(clock)[0](app)
    clock.now += timedelta(minutes=1)
    chart._update_chart()
    app._session_tracker._stats = None
    chart._update_chart()
    assert chart._curve.data == ([], [])
    assert chart._earnings_history == []


def test_healthy_samples_keep_change_only_history_and_continuous_endpoint(app, clock):
    earning_session(app, clock)
    chart = chart_classes(clock)[0](app)
    clock.now += timedelta(minutes=1)
    chart._update_chart()
    clock.now += timedelta(minutes=2)
    chart._update_chart()
    assert chart._earnings_history == [(1, 30_000)]
    assert chart._curve.data == ([1, 3], [30_000, 30_000])
    app._process_money_change(MoneyReading(total=140_000))
    chart._update_chart()
    assert chart._earnings_history == [(1, 30_000), (3, 40_000)]
    assert chart._curve.data == ([1, 3, 3], [30_000, 40_000, 40_000])


def test_zero_earnings_session_stays_empty(app, clock):
    app._session_tracker.start_session()
    chart = chart_classes(clock)[0](app)
    clock.now += timedelta(minutes=5)
    chart._update_chart()
    assert chart._curve.data == ([], [])
    assert chart._earnings_history == []


@pytest.mark.parametrize('empty', [None, EarningsBreakdown(), EarningsBreakdown(from_missions=-1)])
def test_missing_or_nonpositive_breakdown_clears_bars_and_labels(clock, empty):
    owner = SimpleNamespace(earnings_breakdown=EarningsBreakdown(from_missions=500))
    chart = chart_classes(clock)[1](owner)
    chart._update_chart()
    assert chart._plot.items
    assert chart._plot.axis.ticks == [[(0, 'Missions')]]
    owner.earnings_breakdown = empty
    chart._update_chart()
    assert chart._plot.items == []
    assert chart._plot.axis.ticks == []


def test_manual_breakdown_reset_also_clears_labels(clock):
    owner = SimpleNamespace(earnings_breakdown=EarningsBreakdown(from_heists=500))
    chart = chart_classes(clock)[1](owner)
    chart._update_chart()
    chart.reset()
    assert chart._plot.items == []
    assert chart._plot.axis.ticks == []


def test_healthy_breakdown_redraw_keeps_order_color_and_positive_filter(clock):
    owner = SimpleNamespace(earnings_breakdown=EarningsBreakdown(
        from_missions=100, from_sells=200, from_vip_work=300, from_heists=400, from_other=500))
    chart = chart_classes(clock)[1](owner)
    chart._update_chart()
    assert [bar['height'] for bar in chart._plot.items] == [[100], [200], [300], [400], [500]]
    assert [bar['brush'] for bar in chart._plot.items] == [
        '#2196F3', '#4CAF50', '#FF9800', '#9C27B0', '#607D8B']
    assert chart._plot.axis.ticks == [[(0, 'Missions'), (1, 'Sells'), (2, 'VIP Work'),
                                      (3, 'Heists'), (4, 'Other')]]
    owner.earnings_breakdown = EarningsBreakdown(from_missions=0, from_sells=-1, from_heists=9)
    chart._update_chart()
    assert chart._plot.items == [dict(x=[0], height=[9], width=0.6, brush='#9C27B0')]
    assert chart._plot.axis.ticks == [[(0, 'Heists')]]


def test_real_reset_preserves_existing_history_based_breakdown_scope(app, clock):
    app._session_tracker.start_session()
    clock.now += timedelta(seconds=1)
    app._activity_tracker.start_activity(ActivityType.CONTACT_MISSION, 'Synthetic mission')
    app._activity_tracker.complete_activity(success=True, earnings=500)
    chart = chart_classes(clock)[1](app)
    chart._update_chart()
    before = list(chart._plot.items)
    assert before
    app.reset_session()
    clock.now += timedelta(seconds=1)
    chart._update_chart()
    assert chart._plot.items == before


@pytest.mark.parametrize('kind', [0, 1, 2])
def test_optional_graph_dependency_returns_without_native_plot_or_app_access(clock, kind):
    class UnreadableApp:
        def __getattribute__(self, name):
            raise AssertionError(f'Unexpected app read: {name}')

    chart = chart_classes(clock, available=False)[kind](UnreadableApp())
    if kind != 2:
        chart._update_chart()
    chart.reset()
    assert not hasattr(chart, '_curve')
    assert not hasattr(chart, '_plot')


@pytest.mark.parametrize('kind', [0, 1])
def test_no_app_update_is_safe(clock, kind):
    chart = chart_classes(clock)[kind]()
    chart._update_chart()
    chart.reset()
