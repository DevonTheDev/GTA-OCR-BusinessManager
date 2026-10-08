"""One-shot sample ownership through the real event-controlled capture worker."""

import copy
import threading
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import src.app as app_module
from src.app import AppState
from tests.test_detection_recovery_worker import pipeline as pipeline


def sample_status(app):
    assert hasattr(app, "get_detection_sample_status"), "One-shot sample lifecycle is missing"
    return app.get_detection_sample_status()


@dataclass(frozen=True)
class DetachedSample:
    sample_id: str
    run_id: str
    captured_at: str
    png_bytes: bytes = b"synthetic PNG"
    json_bytes: bytes = b"{}"


@pytest.fixture
def recorders(monkeypatch):
    instances = []

    class Recorder:
        failure = None

        def __init__(self, sample_id, run_id, requested_at, admitted_at):
            self.sample_id, self.run_id = sample_id, run_id
            self.events = []
            instances.append(self)

        def call(self, name, *args):
            if type(self).failure == name:
                raise ValueError("sensitive synthetic evidence must not enter status")
            self.events.append((name, args))

        def capture(self, images, region_present, provider):
            self.call("capture", images[0].copy() if images[0] is not None else None,
                      dict(region_present), provider)
            if images[0] is None:
                raise ValueError("Capture unavailable")

        def observe_ocr(self, *args):
            self.call("ocr", *args)

        def mission_observation_unavailable(self):
            self.call("unavailable")

        def stage(self, name, result):
            self.call("stage", name, copy.deepcopy(result))

        def selection(self, phase, activity, identity_status, waiting):
            self.call("selection", phase, id(activity), copy.deepcopy(activity),
                      identity_status, waiting)

        def finish(self):
            self.call("finish")
            return DetachedSample(self.sample_id, self.run_id, "2026-10-08T12:00:00+00:00")

    monkeypatch.setattr(app_module, "DetectionSampleCollector", Recorder, raising=False)
    return instances, Recorder


def next_cycle(p):
    p.stage = None
    p.app._stop_event.idle.clear()
    p.app.resume()
    assert p.app._stop_event.idle.wait(3)


def test_paused_arm_waits_for_admission_and_repeated_requests_retain_exact_token(pipeline, recorders):
    p, app = pipeline, pipeline.app
    assert sample_status(app).state == "idle"
    captures = app._data.total_captures
    requested = app.request_detection_sample()
    assert requested.state == "armed"
    assert requested.token is app.request_detection_sample().token
    assert app._data.total_captures == captures
    assert recorders[0] == []
    next_cycle(p)
    ready = sample_status(app)
    assert ready.state == "ready" and ready.token is requested.token
    assert ready.sample_id == requested.sample_id and ready.run_id == requested.run_id
    assert app._data.total_captures == captures + 1
    assert len(recorders[0]) == 1
    next_cycle(p)
    assert sample_status(app) == ready
    assert len(recorders[0]) == 1


@pytest.mark.parametrize("stage", ["capture", "ocr", "money_callback", "capture_callback", "rate"])
def test_arming_during_busy_cycle_waits_for_next_admission(pipeline, recorders, stage):
    p, app = pipeline, pipeline.app
    sample_status(app)
    p.balance = 1250
    p.arm(stage)
    requested = app.request_detection_sample()
    assert requested.state == "armed"
    p.drain()
    assert sample_status(app).state == "armed"
    assert recorders[0] == []
    p.images[0][:] = 177
    next_cycle(p)
    assert sample_status(app).state == "ready"
    captured = next(args[0] for name, args in recorders[0][0].events if name == "capture")
    assert (captured == 177).all()


def test_exact_cancel_and_claim_reserve_single_slot(pipeline, recorders):
    app = pipeline.app
    sample_status(app)
    first = app.request_detection_sample()
    assert not app.cancel_detection_sample(copy.copy(first.token))
    assert app.cancel_detection_sample(first.token)
    second = app.request_detection_sample()
    assert second.token is not first.token
    assert not app.cancel_detection_sample(first.token)
    next_cycle(pipeline)
    assert app.claim_detection_sample_for_save(first.token) is None
    detached = app.claim_detection_sample_for_save(second.token)
    assert detached.sample_id == second.sample_id
    assert sample_status(app).state == "saving"
    assert app.claim_detection_sample_for_save(second.token) is None
    assert not app.cancel_detection_sample(second.token)
    assert app.request_detection_sample().token is second.token
    result = SimpleNamespace(success=True, message="Saved.")
    assert app.complete_detection_sample_save(second.token, result)
    assert sample_status(app).state == "saved"
    assert not app.complete_detection_sample_save(second.token, result)
    assert app.request_detection_sample().token is not second.token


@pytest.mark.parametrize("failure", ["capture", "ocr", "stage", "selection", "finish"])
def test_recorder_failure_is_passive_and_consumes_request(pipeline, recorders, failure):
    p, app = pipeline, pipeline.app
    sample_status(app)
    recorders[1].failure = failure
    original = app._activity_tracker.current_activity
    p.balance = 1250
    app.request_detection_sample()
    next_cycle(p)
    status = sample_status(app)
    assert status.state == "failed"
    assert "sensitive" not in status.message
    assert app.current_money == 1250 and app.session_earnings == 250
    assert app._activity_tracker.current_activity is original
    assert app.last_capture is p.callback_results[-1]
    assert app.last_capture.activity_name == "Headhunter"
    next_cycle(p)
    assert len(recorders[0]) == 1


def test_missing_full_screen_consumes_request_without_extra_capture(pipeline, recorders):
    p, app = pipeline, pipeline.app
    sample_status(app)
    before = app._data.total_captures
    p.images[0] = None
    app.request_detection_sample()
    next_cycle(p)
    assert sample_status(app).state == "failed"
    assert app._data.total_captures == before + 1
    assert app.last_capture is p.callback_results[-1]


@pytest.mark.parametrize("stage", ["capture", "ocr", "rate"])
def test_normal_cycle_exception_consumes_request_and_releases_busy(pipeline, recorders, stage):
    p, app = pipeline, pipeline.app
    sample_status(app)
    app.request_detection_sample()
    p.arm(stage, raises=True)
    app.pause()
    p.release.set()
    assert app._stop_event.error.wait(3)
    assert sample_status(app).state == "failed"
    assert not app._capture_busy


def test_stop_timeout_restart_and_late_save_cannot_change_newer_request(pipeline, recorders, monkeypatch):
    p, app = pipeline, pipeline.app
    sample_status(app)
    old = app.request_detection_sample()
    next_cycle(p)
    detached = app.claim_detection_sample_for_save(old.token)
    p.arm("capture")
    worker = app._capture_thread
    real_join = worker.join
    monkeypatch.setattr(worker, "join", lambda timeout=None: real_join(0))
    app.stop()
    assert app.state is AppState.STOPPING
    assert sample_status(app).state == "unavailable"
    assert not app.start()
    p.release.set()
    real_join(3)
    assert app.state is AppState.STOPPED
    p.stage = None
    app._stop_event.idle.clear()
    assert app.start()
    assert app._stop_event.idle.wait(3)
    current = app.request_detection_sample()
    assert current.run_id != old.run_id
    assert detached.sample_id == old.sample_id
    assert not app.complete_detection_sample_save(old.token, SimpleNamespace(success=True, message="Saved"))
    assert not app.cancel_detection_sample(old.token)
    assert app.claim_detection_sample_for_save(old.token) is None
    assert sample_status(app).token is current.token


def test_stop_during_collection_cannot_publish_old_sample(pipeline, recorders, monkeypatch):
    p, app = pipeline, pipeline.app
    sample_status(app)
    requested = app.request_detection_sample()
    p.arm("capture")
    assert sample_status(app).state == "collecting"
    worker = app._capture_thread
    real_join = worker.join
    monkeypatch.setattr(worker, "join", lambda timeout=None: real_join(0))
    app.stop()
    p.release.set()
    real_join(3)
    assert sample_status(app).state == "unavailable"
    assert app.claim_detection_sample_for_save(requested.token) is None


def test_unsupported_custom_detector_is_called_once_without_new_keyword(pipeline, recorders, monkeypatch):
    p, app = pipeline, pipeline.app
    sample_status(app)
    original = app._state_detector.detect
    calls = []

    def custom(image, **kwargs):
        calls.append(kwargs)
        assert "observation" not in kwargs
        return original(image, **kwargs)

    monkeypatch.setattr(app._state_detector, "detect", custom)
    app.request_detection_sample()
    next_cycle(p)
    assert len(calls) == 1
    assert sample_status(app).state == "ready"
    assert any(name == "unavailable" for name, _ in recorders[0][0].events)


def test_candidate_first_guard_processed_and_selection_are_separate(pipeline, recorders, monkeypatch):
    from dataclasses import replace

    p, app = pipeline, pipeline.app
    sample_status(app)
    guard_calls = []

    def changing_guard(result):
        guard_calls.append(result)
        return replace(result, reason="first guard" if len(guard_calls) == 1 else "final guard")

    monkeypatch.setattr(app, "_guard_mission_observation", changing_guard)
    app.request_detection_sample()
    next_cycle(p)
    events = recorders[0][0].events
    stages = {args[0]: args[1] for name, args in events if name == "stage"}
    assert set(stages) == {"candidate", "first_guarded", "processed"}
    assert stages["candidate"].reason not in ("first guard", "final guard")
    assert stages["first_guarded"].reason == "first guard"
    assert stages["processed"].state_reason == "final guard"
    selections = [args for name, args in events if name == "selection"]
    assert [selection[0] for selection in selections] == ["before", "after"]
    assert selections[0][1] == selections[1][1]


def test_cancel_during_encoding_does_not_clear_next_armed_token(pipeline, recorders, monkeypatch):
    p, app = pipeline, pipeline.app
    sample_status(app)
    entered, release = threading.Event(), threading.Event()
    original_finish = recorders[1].finish

    def gated_finish(recorder):
        entered.set()
        assert release.wait(3)
        return original_finish(recorder)

    monkeypatch.setattr(recorders[1], "finish", gated_finish)
    old = app.request_detection_sample()
    app._stop_event.idle.clear()
    app.resume()
    assert entered.wait(2)
    try:
        assert app._capture_busy
        # The encoder must not hold the lifecycle or data locks.
        assert app._lifecycle_lock.acquire(blocking=False)
        app._lifecycle_lock.release()
        assert app._data_lock.acquire(blocking=False)
        app._data_lock.release()
        assert app.cancel_detection_sample(old.token)
        newer = app.request_detection_sample()
        assert newer.token is not old.token
    finally:
        release.set()
    assert app._stop_event.idle.wait(3)
    assert sample_status(app).token is newer.token
    assert sample_status(app).state == "armed"
    next_cycle(p)
    assert sample_status(app).state == "ready"
    assert sample_status(app).token is newer.token


def test_collector_initialization_failure_keeps_normal_cycle_and_callbacks(pipeline, recorders, monkeypatch):
    p, app = pipeline, pipeline.app
    sample_status(app)

    def broken_collector(*_args):
        raise RuntimeError("synthetic construction failure")

    monkeypatch.setattr(app_module, "DetectionSampleCollector", broken_collector)
    old_captures = app._data.total_captures
    app.request_detection_sample()
    next_cycle(p)
    assert sample_status(app).state == "failed"
    assert app._data.total_captures == old_captures + 1
    assert app.last_capture is p.callback_results[-1]


def test_supported_custom_detector_type_error_is_never_retried(pipeline, recorders, monkeypatch):
    p, app = pipeline, pipeline.app
    sample_status(app)
    calls = []

    def broken_detector(*args, **kwargs):
        calls.append((args, kwargs))
        raise TypeError("synthetic backend failure")

    broken_detector.supports_detection_sample_observation = True
    monkeypatch.setattr(app._state_detector, "detect", broken_detector)
    app.request_detection_sample()
    app._stop_event.error.clear()
    app.resume()
    assert app._stop_event.error.wait(3)
    app.pause()
    assert len(calls) == 1
    assert "observation" in calls[0][1]
    assert sample_status(app).state == "failed"
    assert not app._capture_busy


def test_region_presence_uses_actual_request_even_if_provider_changes_during_capture(pipeline, recorders, monkeypatch):
    p, app = pipeline, pipeline.app
    original_capture = app._capture.capture_multiple_regions

    def changing_capture(requested_regions):
        images = original_capture(requested_regions)
        app._capture.regions.mission_text = None
        app._capture.regions.center_prompt = None
        app._capture.regions.mission_banner = None
        app._capture.regions.bottom_objective = None
        app._capture.regions.result_header = None
        return images

    monkeypatch.setattr(app._capture, "capture_multiple_regions", changing_capture)
    app.request_detection_sample()
    next_cycle(p)
    assert sample_status(app).state == "ready"
    present = next(args[1] for name, args in recorders[0][0].events if name == "capture")
    assert present == {"mission": True, "center": True, "banner": True,
                       "bottom": True, "header": True, "vip_status": False}

