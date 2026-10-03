"""Live display methods refresh after real app accounting and session resets."""

import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.app import AppData
from src.detection.parsers.money_parser import MoneyReading
from src.game.activities import ActivityType
from src.tracking import session as session_module
from src.utils.helpers import format_money, format_money_short, format_percentage, format_time
from tests.test_app_accounting import app as app


def actual_method(path, class_name, method_name):
    """Execute the actual method; substitute labels, not its display logic."""
    source = Path(__file__).resolve().parents[1] / path
    cls = next(node for node in ast.parse(source.read_text()).body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == method_name)
    namespace = dict(format_money=format_money, format_money_short=format_money_short,
                     format_time=format_time, format_percentage=format_percentage)
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[method_name]


class Display:
    def __init__(self):
        self.text = self.subtitle = self.style = ''
    def setText(self, value):
        self.text = value
    def set_subtitle(self, value):
        self.subtitle = value
    def set_value(self, value, *args):
        self.text = value
    def setStyleSheet(self, value):
        self.style = value
    def set_recommendation(self, *args):
        pass
    def set_activity(self, *args, **kwargs):
        pass
    def show(self):
        pass
    def hide(self):
        pass


def surface(app, kind):
    widget = SimpleNamespace(_app=app)
    if kind == 'dashboard':
        names = ['money_card', 'session_card', 'activities_card', 'time_card',
                 'activity_card', 'recommendation_card']
        widget._update_recent_list = lambda activities: None
        path, cls, method = 'src/ui/widgets/dashboard.py', 'DashboardWidget', '_update_display'
    elif kind == 'overlay':
        names = ['money_label', 'session_label', 'rate_label', 'state_badge',
                 'activity_label', 'timer_label', 'recommendation_label']
        widget._update_goal = widget._update_bonus = lambda: None
        path, cls, method = 'src/ui/overlay.py', 'OverlayWindow', '_update_ui'
    else:
        names = []
        widget.labels = {name: Display() for name in (
            'start_money', 'current_money', 'earnings', 'rate', 'duration', 'activities',
            'success_rate', 'avg_earnings', 'mission_time', 'idle_time', 'sells',
            'last_change', 'best_activity', 'best_rate', 'from_missions', 'from_sells')}
        widget._get_stat_label = widget.labels.__getitem__
        path, cls, method = 'src/ui/widgets/session_panel.py', 'SessionPanel', '_update_display'
    for name in names:
        setattr(widget, '_' + name, Display())
    update = actual_method(path, cls, method)
    widget.refresh = lambda: update(widget)
    return widget


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=datetime(2026, 1, 1, 12))
    monkeypatch.setattr(session_module, 'datetime', SimpleNamespace(now=lambda: clock.now))
    return clock


def earning_session(app, clock):
    app._session_tracker.start_session()
    app._process_money_change(MoneyReading(total=100_000))
    app._process_money_change(MoneyReading(total=130_000))
    clock.now += timedelta(hours=1)


def rate_text(widget, kind):
    if kind == 'dashboard':
        return widget._time_card.subtitle
    if kind == 'overlay':
        return widget._rate_label.text
    return widget.labels['rate'].text


@pytest.mark.parametrize('kind,empty', [('dashboard', '$0/hr'), ('overlay', ''), ('session', '--')])
def test_reset_replaces_positive_hourly_rate_immediately(app, clock, kind, empty):
    earning_session(app, clock)
    widget = surface(app, kind)
    widget.refresh()
    assert '$30.0K/hr' in rate_text(widget, kind)
    app.reset_session()
    widget.refresh()
    assert rate_text(widget, kind) == empty


@pytest.mark.parametrize('kind', ['dashboard', 'overlay', 'session'])
def test_rate_recovers_after_new_session_warmup_and_stops_with_session(app, clock, kind):
    earning_session(app, clock)
    widget = surface(app, kind)
    widget.refresh()
    app.reset_session()
    app._process_money_change(MoneyReading(total=131_000))
    clock.now += timedelta(seconds=120)
    widget.refresh()
    assert '$30.0K/hr' in rate_text(widget, kind)
    app._session_tracker.end_session()
    clock.now += timedelta(hours=1)
    widget.refresh()
    assert '$30.0K/hr' in rate_text(widget, kind)


@pytest.mark.parametrize('kind', ['dashboard', 'session'])
def test_completed_count_uses_full_session_and_resets_without_deleting_history(app, clock, kind):
    app._session_tracker.start_session()
    for index in range(12):
        app._activity_tracker.start_activity(ActivityType.CONTACT_MISSION, str(index))
        app._activity_tracker.complete_activity(success=index % 2 == 0, earnings=100)
        app._session_tracker.record_activity_complete(success=index % 2 == 0)
    assert len(app.recent_activities) == 10
    widget = surface(app, kind)
    widget.refresh()
    label = widget._activities_card if kind == 'dashboard' else widget.labels['activities']
    assert label.text == '12'
    app.reset_session()
    widget.refresh()
    assert label.text == '0'
    assert len(app.recent_activities) == 10


@pytest.mark.parametrize('field', ['start_money', 'current_money', 'rate', 'duration',
                                  'success_rate', 'avg_earnings', 'mission_time',
                                  'idle_time', 'sells', 'last_change', 'best_activity',
                                  'best_rate', 'from_missions', 'from_sells'])
def test_session_panel_replaces_unavailable_values_instead_of_retaining_old_labels(app, field):
    app._data = AppData()
    app._session_tracker._stats = None
    widget = surface(app, 'session')
    widget.labels[field].text = 'Prior session value'
    widget.labels[field].style = 'color: #F44336;'
    widget.refresh()
    assert widget.labels[field].text == '--'
    if field in {'success_rate', 'last_change', 'best_activity', 'best_rate',
                 'from_missions', 'from_sells'}:
        assert '#F44336' not in widget.labels[field].style


def test_reset_clears_success_rate_but_keeps_valid_zero_balances(app, clock):
    app._session_tracker.start_session()
    app._process_money_change(MoneyReading(total=0))
    app._session_tracker.record_activity_complete(True)
    widget = surface(app, 'session')
    widget.refresh()
    assert widget.labels['success_rate'].text == '100.0%'
    app.reset_session()
    widget.refresh()
    assert widget.labels['success_rate'].text == '--'
    assert widget.labels['start_money'].text == widget.labels['current_money'].text == '$0'


@pytest.mark.parametrize('kind', ['overlay', 'session'])
@pytest.mark.parametrize('seconds', [0, 60, 61])
def test_existing_rate_warmup_boundary_is_preserved(app, clock, kind, seconds):
    app._session_tracker.start_session()
    app._process_money_change(MoneyReading(total=100))
    app._process_money_change(MoneyReading(total=200))
    clock.now += timedelta(seconds=seconds)
    widget = surface(app, kind)
    widget.refresh()
    assert ('/hr' in rate_text(widget, kind)) is (seconds > 60)


def test_dashboard_without_stats_clears_old_duration_and_subtitle(app):
    widget = surface(app, 'dashboard')
    widget._time_card.text = '1h'
    widget._time_card.subtitle = '$30.0K/hr'
    app._session_tracker._stats = None
    widget.refresh()
    assert widget._time_card.text == '--'
    assert widget._time_card.subtitle == ''


def test_session_panel_keeps_current_stats_and_recent_history_calculations(app, clock):
    earning_session(app, clock)
    for kind, amount in [(ActivityType.CONTACT_MISSION, 1000), (ActivityType.SELL_MISSION, 3000)]:
        activity = app._activity_tracker.start_activity(kind, kind.name)
        activity.started_at -= timedelta(minutes=10)
        app._activity_tracker.complete_activity(success=True, earnings=amount)
        app._session_tracker.record_activity_complete(True, is_sell=kind == ActivityType.SELL_MISSION)
    app._session_tracker.add_mission_time(1200)
    app._session_tracker.add_idle_time(2400)
    widget = surface(app, 'session')
    widget.refresh()
    expected = {'start_money': '$100,000', 'current_money': '$130,000', 'earnings': '+$30,000',
                'rate': '$30.0K/hr', 'duration': '1h', 'activities': '2', 'success_rate': '100.0%',
                'avg_earnings': '$2.0K', 'mission_time': '20m', 'idle_time': '40m', 'sells': '1',
                'last_change': '+$30.0K', 'best_activity': 'Sell Mission',
                'from_missions': '+$1.0K', 'from_sells': '+$3.0K'}
    for name, text in expected.items():
        assert widget.labels[name].text == text
    assert widget.labels['best_rate'].text == '$18.0K/hr'
    app.reset_session()
    widget.refresh()
    assert widget.labels['activities'].text == '0'
    # The existing recent-history average and analytics scope are deliberately retained.
    assert widget.labels['avg_earnings'].text == '$2.0K'
    assert widget.labels['from_missions'].text == '--'
    clock.now += timedelta(seconds=61)
    app._recalculate_analytics(force=True)
    widget.refresh()
    assert widget.labels['from_missions'].text == '+$1.0K'


def test_session_panel_clears_average_when_recent_history_has_no_successes(app):
    widget = surface(app, 'session')
    widget.labels['avg_earnings'].text = '$20.0K'
    app._activity_tracker.start_activity(ActivityType.CONTACT_MISSION, 'Failed')
    app._activity_tracker.complete_activity(success=False)
    widget.refresh()
    assert widget.labels['avg_earnings'].text == '--'
