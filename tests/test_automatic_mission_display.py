"""Actual display updates over capture results; no Qt, screen capture, or gameplay."""

from types import SimpleNamespace

import pytest

from src.app import AppState, CaptureResult
from src.detection.parsers.mission_parser import MissionReading, MissionType
from src.detection.parsers.timer_parser import TimerParser, TimerReading
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_session_display_refresh import Display, surface
from tests.test_app_accounting import app as app
from tests.test_timer_capture import TRUNCATED_TIMER_INPUTS, timer_capture


class ActivityDisplay(Display):
    def set_activity(self, name, timer="", objective=""):
        self.text, self.timer, self.objective = name, timer, objective


class TimerDisplay(Display):
    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


def make_app(capture=None, state=GameState.MISSION_ACTIVE):
    return SimpleNamespace(
        current_money=125_000, session_earnings=1000, session_stats=None,
        recent_activities=[], recommendations=[], game_state=state,
        last_capture=capture,
    )


@pytest.fixture(params=["dashboard", "overlay"])
def view(request):
    app = make_app()
    widget = surface(app, request.param)
    if request.param == "dashboard":
        widget._activity_card = ActivityDisplay()
    return request.param, app, widget


def name(view):
    kind, _app, widget = view
    return (widget._activity_card if kind == "dashboard" else widget._activity_label).text


def generic(view, state):
    text = state.name.replace("_", " ")
    return text.title() if view[0] == "dashboard" else text


@pytest.mark.parametrize("state", [
    GameState.MISSION_ACTIVE, GameState.SELLING, GameState.HEIST_PREP, GameState.HEIST_FINALE,
])
def test_active_state_shows_current_canonical_name_and_preserves_state_badge(view, state):
    _, app, widget = view
    app.game_state = state
    app.last_capture = CaptureResult(
        game_state=state, activity_name="Headhunter", activity_type=ActivityType.VIP_WORK,
        activity_identity_status="known_name",
    )
    widget.refresh()
    assert name(view) == "Headhunter"
    if view[0] == "overlay":
        assert widget._state_badge.text == state.name.replace("_", " ")


@pytest.mark.parametrize("activity_type, expected", [
    (ActivityType.SECURITY_CONTRACT, "Security Contract"),
    (ActivityType.VIP_WORK, "VIP Work"),
    (ActivityType.MC_CONTRACT, "MC Contract"),
    (ActivityType.SELL_MISSION, "Sell Mission"),
    (ActivityType.HEIST_PREP, "Heist Prep"),
])
def test_category_only_shows_friendly_type_instead_of_raw_objective(view, activity_type, expected):
    _, app, widget = view
    app.last_capture = CaptureResult(
        game_state=app.game_state, activity_name="Deliver the goods",
        activity_type=activity_type, activity_identity_status="type_only",
    )
    widget.refresh()
    assert name(view) == expected


@pytest.mark.parametrize("status", ["unknown", "ambiguous", "unexpected"])
def test_unresolved_identity_keeps_generic_state_without_a_guessed_type(view, status):
    _, app, widget = view
    app.last_capture = CaptureResult(
        game_state=app.game_state, activity_name="Unconfirmed contact",
        activity_type=ActivityType.CONTACT_MISSION, activity_identity_status=status,
        mission=MissionReading(mission_name="Sightseer", identity_status="known_name"),
    )
    widget.refresh()
    assert name(view) == generic(view, app.game_state)


def test_current_identity_wins_over_conflicting_latest_frame(view):
    _, app, widget = view
    app.last_capture = CaptureResult(
        game_state=app.game_state, activity_name="Headhunter", activity_type=ActivityType.VIP_WORK,
        activity_identity_status="known_name",
        mission=MissionReading(
            mission_name="Sightseer", mission_type=MissionType.VIP_WORK,
            identity_status="known_name",
        ),
    )
    widget.refresh()
    assert name(view) == "Headhunter"


@pytest.mark.parametrize("state", [AppState.RUNNING, AppState.PAUSED, AppState.STOPPING, AppState.STOPPED])
@pytest.mark.parametrize("status, label", [("known_name", "Headhunter"), ("type_only", "VIP Work")])
def test_stopped_capture_suppresses_stale_identity_but_pause_keeps_it(view, state, status, label):
    _, app, widget = view
    app.last_capture = CaptureResult(
        game_state=app.game_state, activity_name="Headhunter",
        activity_type=ActivityType.VIP_WORK, activity_identity_status=status,
    )
    widget.refresh()
    app.state = state
    widget.refresh()
    expected = label
    if state in (AppState.STOPPING, AppState.STOPPED):
        expected = generic(view, app.game_state)
    assert name(view) == expected


@pytest.mark.parametrize("state", [
    GameState.MISSION_COMPLETE, GameState.MISSION_FAILED, GameState.IDLE, GameState.UNKNOWN,
])
def test_end_state_replaces_resolved_name_even_if_capture_retains_old_identity(view, state):
    _, app, widget = view
    app.last_capture = CaptureResult(
        game_state=app.game_state, activity_name="Headhunter",
        activity_identity_status="known_name",
    )
    widget.refresh()
    app.game_state = app.last_capture.game_state = state
    widget.refresh()
    assert name(view) == generic(view, state)


@pytest.mark.parametrize("replacement", [None, CaptureResult(game_state=GameState.MISSION_ACTIVE)])
def test_missing_capture_or_current_activity_clears_previous_name(view, replacement):
    kind, app, widget = view
    app.last_capture = CaptureResult(
        game_state=app.game_state, activity_name="Headhunter",
        activity_identity_status="known_name", objective_text="Old objective",
        timer=TimerReading(minutes=5, seconds=30, total_seconds=330),
    )
    widget.refresh()
    app.last_capture = replacement
    widget.refresh()
    expected = "Idle" if kind == "dashboard" and replacement is None else generic(view, app.game_state)
    assert name(view) == expected
    if kind == "dashboard":
        assert widget._activity_card.timer == widget._activity_card.objective == ""
    else:
        assert widget._timer_label.text == ""


@pytest.mark.parametrize("identity", [{}, {"activity_identity_status": "known_name", "activity_name": ""},
                                    {"activity_identity_status": "type_only", "activity_type": None},
                                    {"activity_identity_status": "type_only", "activity_type": ActivityType.UNKNOWN}])
def test_older_or_incomplete_capture_keeps_state_and_timer(view, identity):
    _, app, widget = view
    app.last_capture = SimpleNamespace(
        game_state=app.game_state, objective_text="Existing objective",
        timer=TimerReading(minutes=5, seconds=30, total_seconds=330), **identity,
    )
    widget.refresh()
    assert name(view) == generic(view, app.game_state)
    if view[0] == "dashboard":
        assert widget._activity_card.timer == "5:30"
        assert widget._activity_card.objective == "Existing objective"
    else:
        assert widget._timer_label.text == "Timer: 5:30"


@pytest.mark.parametrize("mission_objective, expected", [
    ("Go to the warehouse", "Go to the warehouse"), ("", "Legacy objective"),
])
def test_dashboard_prefers_parsed_objective_with_legacy_fallback(mission_objective, expected):
    app = make_app(CaptureResult(
        game_state=GameState.MISSION_ACTIVE, objective_text="Legacy objective",
        mission=MissionReading(objective=mission_objective),
    ))
    widget = surface(app, "dashboard")
    widget._activity_card = ActivityDisplay()
    widget.refresh()
    assert widget._activity_card.objective == expected


@pytest.mark.parametrize("timer, expected", [
    (None, ""), (TimerReading(), ""), (TimerReading(raw_text="0:00"), "0:00"),
    (TimerReading(hours=1, minutes=2, seconds=3, total_seconds=3723), "1:02:03"),
])
def test_identity_display_preserves_timer_values(view, timer, expected):
    kind, app, widget = view
    app.last_capture = CaptureResult(
        game_state=app.game_state, activity_name="Headhunter",
        activity_identity_status="known_name", timer=timer,
    )
    widget.refresh()
    if kind == "dashboard":
        assert widget._activity_card.timer == expected
    else:
        assert widget._timer_label.text == (f"Timer: {expected}" if expected else "")


def test_parsed_twenty_minute_timer_reaches_display(view):
    kind, app, widget = view
    app.last_capture = CaptureResult(
        game_state=app.game_state, activity_name="Headhunter",
        activity_identity_status="known_name", timer=TimerParser().parse("20:00"),
    )

    widget.refresh()

    assert name(view) == "Headhunter"
    if kind == "dashboard":
        assert widget._activity_card.timer == "20:00"
    else:
        assert widget._timer_label.text == "Timer: 20:00"


def test_parsed_positive_invalid_zero_sequence_refreshes_timer(view):
    kind, app, widget = view
    parser = TimerParser()
    for text, expected in (("20:00", "20:00"), ("60:00:60", ""), ("0", "0:00")):
        app.last_capture = CaptureResult(
            game_state=app.game_state, activity_name="Headhunter",
            activity_type=ActivityType.VIP_WORK, activity_identity_status="known_name",
            timer=parser.parse(text),
        )
        widget.refresh()
        assert name(view) == "Headhunter"
        assert app.game_state is GameState.MISSION_ACTIVE
        assert app.current_money == 125_000 and app.session_earnings == 1000
        assert app.recent_activities == []
        if kind == "dashboard":
            assert widget._activity_card.timer == expected
        else:
            assert widget._timer_label.text == (f"Timer: {expected}" if expected else "")


@pytest.mark.parametrize("kind", ["dashboard", "overlay"])
@pytest.mark.parametrize("invalid_text", TRUNCATED_TIMER_INPUTS)
def test_actual_capture_sequence_clears_and_restores_display_timer(app, kind, invalid_text):
    app._state = AppState.RUNNING
    observe = timer_capture(app)
    widget = surface(app, kind)
    if kind == "dashboard":
        widget._activity_card = ActivityDisplay()
    else:
        widget._timer_label = TimerDisplay()
    owner = None
    previous_timer = None

    for text, expected in (("20:00", "20:00"), (invalid_text, ""), ("0", "0:00"), ("5:30", "5:30")):
        result = observe(text)
        # Mirror the capture loop's publication of this actual cycle result.
        app._last_capture_result = result
        if owner is None:
            owner = app._activity_tracker.current_activity
            started = owner.started_at
        widget.refresh()

        assert app.game_state is result.game_state is GameState.MISSION_ACTIVE
        assert result.activity_name == "Headhunter"
        assert app._activity_tracker.current_activity is owner
        assert owner.started_at == started
        assert app.current_money == 100_000 and app.session_earnings == 0
        assert app.recent_activities == []
        if expected:
            assert result.timer is not None and result.timer.has_value
            assert app._timer_parser.get_last_valid() is result.timer
            previous_timer = result.timer
        else:
            assert result.timer is None
            assert app._timer_parser.get_last_valid() is previous_timer
        if kind == "dashboard":
            assert widget._activity_card.text == "Headhunter"
            assert widget._activity_card.timer == expected
        else:
            assert widget._activity_label.text == "Headhunter"
            assert widget._state_badge.text == "MISSION ACTIVE"
            assert widget._timer_label.text == (f"Timer: {expected}" if expected else "")
            assert widget._timer_label.visible is bool(expected)
