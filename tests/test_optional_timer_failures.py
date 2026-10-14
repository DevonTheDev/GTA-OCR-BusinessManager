"""Timer failures through the synchronous capture loop; no native OCR accuracy claim."""

import json

import pytest

from src.detection.detection_sample import DetectionSampleCollector
from src.detection.parsers.money_parser import MoneyReading
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app
from tests.test_screen_capture_cadence import CycleFrames


ACTIVE = {1: "$100,000", 2: "Headhunter", 4: "20:00"}


def fail_first_timer(app, monkeypatch, frames, stage):
    recognize = frames.engine.recognize_preprocessed
    attempts = []

    def recognize_timer(image, **kwargs):
        result = recognize(image, **kwargs)
        if image is frames.images[4]:
            attempts.append(frames.index)
            if stage == "ocr" and frames.index == 1:
                raise RuntimeError("controlled timer recognition failure")
        return result

    monkeypatch.setattr(frames.engine, "recognize_preprocessed", recognize_timer)
    if stage == "parse":
        parse = app._timer_parser.parse

        def parse_timer(text):
            if frames.index == 1:
                raise ValueError("controlled timer parsing failure")
            return parse(text)

        monkeypatch.setattr(app._timer_parser, "parse", parse_timer)
    return attempts


@pytest.mark.parametrize("stage", ["ocr", "parse"])
@pytest.mark.parametrize("sampled", [False, True])
def test_timer_failure_publishes_mission_and_recovers_next_frame(
    app, monkeypatch, caplog, stage, sampled,
):
    app._process_money_change(MoneyReading(total=90_000))
    previous_timer = app._timer_parser.parse("30:00")
    frames = CycleFrames(app, monkeypatch, [ACTIVE, {**ACTIVE, 4: "19:59"}])
    attempts = fail_first_timer(app, monkeypatch, frames, stage)
    waits, snapshots, completions = [], [], []
    monkeypatch.setattr(app._stop_event, "wait", lambda timeout: waits.append(timeout) or False)
    app.on_mission_complete(completions.append)
    app.on_capture(lambda result: snapshots.append((
        app._activity_tracker.current_activity, app._data.mission_start_time,
        app._data.mission_start_money, app._timer_parser.get_last_valid(),
    )))
    request = app.request_detection_sample() if sampled else None
    frames.run()
    app._stop_event.clear()
    app.pause()

    assert len(frames.results) == 2
    failed, recovered = frames.results
    assert failed.timer is None
    assert snapshots[0][3] is previous_timer
    assert recovered.timer.total_seconds == 1199
    assert snapshots[1][3] is recovered.timer
    assert attempts == [1, 2]
    assert [index for index, _kwargs in frames.calls] == [2, 3, 5, 6, 8, 7, 1, 4] * 2
    assert waits == []
    assert [r.money_change for r in frames.results] == [10_000, 0]
    assert all(r.money.display_value == 100_000 for r in frames.results)
    assert app.current_money == 100_000
    assert app.session_earnings == app.session_stats.total_earnings == 10_000
    assert app._state_machine.state is GameState.MISSION_ACTIVE
    assert all(r.game_state is GameState.MISSION_ACTIVE for r in frames.results)
    assert all(r.activity_name == "Headhunter" for r in frames.results)
    assert all(r.activity_type is ActivityType.VIP_WORK for r in frames.results)
    assert all(r.activity_identity_status == "known_name" for r in frames.results)
    assert snapshots[0][0] is snapshots[1][0] is app._activity_tracker.current_activity
    assert snapshots[0][1:3] == snapshots[1][1:3]
    assert snapshots[0][1] is not None and snapshots[0][2] == 100_000
    assert completions == app._activity_tracker.completed_activities == []
    stored = app._repository.export_session_data(app._data.db_session_id)
    assert stored["activities"] == []
    assert [earning["amount"] for earning in stored["earnings"]] == [10_000]
    assert "Timer read error: controlled timer" in caplog.text
    assert "Capture cycle error" not in caplog.text
    if sampled:
        assert app.get_detection_sample_status().state == "ready"
        sample = app.claim_detection_sample_for_save(request.token)
        assert sample is not None
        report = json.loads(sample.json_bytes)
        assert report["processed"]["state"] == "MISSION_ACTIVE"
        assert report["selection"]["after"]["activity"]["name"] == "Headhunter"
        assert report["selection"]["ownership"] == "new"
        assert set(report["sources"]) == {"mission", "center", "banner", "bottom", "header", "vip_status"}
        assert "timer" not in report["processed"] and "money" not in report["processed"]
    else:
        assert app.get_detection_sample_status().state == "idle"


@pytest.mark.parametrize("stage", ["mission_ocr", "money_ocr", "state", "money", "mission"])
def test_non_timer_failures_keep_cycle_error_and_failed_sample(app, monkeypatch, caplog, stage):
    frames = CycleFrames(app, monkeypatch, [ACTIVE])

    def fail(*_args, **_kwargs):
        raise RuntimeError("controlled non-timer failure")

    if stage.endswith("_ocr"):
        recognize = frames.engine.recognize_preprocessed
        index = 2 if stage == "mission_ocr" else 1

        def recognize_source(image, **kwargs):
            if image is frames.images[index]:
                fail()
            return recognize(image, **kwargs)

        monkeypatch.setattr(frames.engine, "recognize_preprocessed", recognize_source)
    elif stage == "state":
        monkeypatch.setattr(app._state_machine, "transition_to", fail)
    else:
        monkeypatch.setattr(app, "_process_money_change" if stage == "money" else "_process_state", fail)
    waits = []

    def stop_on_error(timeout):
        waits.append(timeout)
        app._stop_event.set()
        return True

    monkeypatch.setattr(app._stop_event, "wait", stop_on_error)
    app.request_detection_sample()
    frames.run()

    assert frames.results == []
    assert waits == [1.0]
    assert app.get_detection_sample_status().state == "failed"
    assert "Capture cycle error: controlled non-timer failure" in caplog.text
    assert "Timer read error" not in caplog.text


def test_timer_failure_keeps_recorder_failure_passive_and_sample_failed(app, monkeypatch):
    frames = CycleFrames(app, monkeypatch, [ACTIVE])
    fail_first_timer(app, monkeypatch, frames, "ocr")

    def fail_stage(*_args):
        raise RuntimeError("controlled sample recorder failure")

    monkeypatch.setattr(DetectionSampleCollector, "stage", fail_stage)
    waits = []
    monkeypatch.setattr(app._stop_event, "wait", lambda timeout: waits.append(timeout) or False)
    app.request_detection_sample()
    frames.run()

    assert len(frames.results) == 1
    assert frames.results[0].timer is None
    assert frames.results[0].activity_name == "Headhunter"
    assert app.current_money == 100_000
    assert waits == []
    assert app.get_detection_sample_status().state == "failed"
