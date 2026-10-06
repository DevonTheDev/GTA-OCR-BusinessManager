"""Event-controlled real capture worker: capture/OCR boundaries stay synthetic.

No sleeps synchronize these tests. Idle/error event waits observe the real loop
boundary; production parsing, state processing, callbacks and SQLite remain real.
"""

import threading
from types import SimpleNamespace

import numpy as np
import pytest

from src.app import AppState, GTABusinessManager
from src.config.settings import Settings
from src.database.repository import Repository
from src.detection.state_detector import StateDetector
from src.game.state_machine import GameStateMachine
from src.utils.performance import PerformanceMonitor
from tests.test_detection_recovery import recovery_status


class ObservedStopEvent:
    def __init__(self, app):
        self.app = app
        self.event = threading.Event()
        self.idle = threading.Event()
        self.error = threading.Event()

    def is_set(self):
        return self.event.is_set()

    def set(self):
        self.event.set()

    def clear(self):
        self.event.clear()

    def wait(self, timeout=None):
        # Observe existing wait boundaries; don't add a production test hook.
        with self.app._lifecycle_lock:
            if timeout == 0.1 and self.app.state is AppState.PAUSED:
                self.idle.set()
            if timeout == 1.0:
                self.error.set()
        return self.event.wait(timeout)


class Pipeline:
    def __init__(self, app, repository, character):
        self.app, self.repository, self.character = app, repository, character
        self.stage = None
        self.raises = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.captures = []
        self.balance = 1000
        self.extra_callback = None
        self.callback_results = []
        self.images = [np.full((120, 200, 3), 100, dtype=np.uint8),
                       object(), object(), object(), None, object(), None, None]

    def gate(self, stage):
        if self.stage != stage:
            return
        self.entered.set()
        assert self.release.wait(5), f"Test did not release {stage}"
        if self.raises:
            raise RuntimeError("synthetic " + stage + " failure")

    def initialize(self):
        capture = SimpleNamespace(closed=False)
        capture.regions = SimpleNamespace(full_screen=0, money_display=1, mission_text=2,
                                          center_prompt=3, timer_bottom_right=4, mission_banner=5, bottom_objective=6, result_header=7)

        def capture_regions(regions):
            self.gate("capture")
            return [self.images[index] for index in regions]

        def close():
            capture.closed = True

        capture.capture_multiple_regions = capture_regions
        capture.set_capture_rate = lambda fps: self.gate("rate")
        capture.close = close
        self.captures.append(capture)
        self.app._capture = capture
        self.app._ocr = SimpleNamespace(is_available=True, recognize_preprocessed=self.recognize)
        self.app._state_machine = GameStateMachine()
        self.app._state_machine.add_listener(self.app._on_game_state_transition)
        self.app._state_detector = StateDetector(
            template_matcher=SimpleNamespace(match_any=lambda *args: None), ocr_engine=self.app._ocr,
        )
        self.app._perf_monitor = PerformanceMonitor()
        self.app._repository = self.repository
        self.app._data.character_id = self.character.id
        self.app._data.db_session_id = self.repository.start_session(self.character).id
        self.app._session_tracker.start_session()

    def recognize(self, image, **kwargs):
        self.gate("ocr")
        texts = {id(self.images[1]): f"${self.balance:,}", id(self.images[2]): "",
                 id(self.images[3]): "Eliminate the targets", id(self.images[5]): "Headhunter"}
        return SimpleNamespace(text=texts[id(image)])

    def money_callback(self, *_args):
        self.gate("money_callback")

    def capture_callback(self, result):
        self.app.pause()
        self.callback_results.append(result)
        if self.extra_callback:
            self.extra_callback(result)
        self.gate("capture_callback")

    def arm(self, stage, *, raises=False):
        with self.app._lifecycle_lock:
            self.stage, self.raises = stage, raises
            self.entered.clear()
            self.release.clear()
            self.app._stop_event.idle.clear()
            self.app._stop_event.error.clear()
            self.app.resume()
        assert self.entered.wait(2), f"Worker did not enter {stage}"

    def drain(self):
        self.release.set()
        assert self.app._stop_event.idle.wait(3), "Worker did not return to paused loop"


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    manager = GTABusinessManager(Settings(tmp_path / "worker.yaml"))
    repository = Repository(str(tmp_path / "worker.db"))
    assert repository.initialize()
    character = repository.get_or_create_character("Synthetic Player")
    fixture = Pipeline(manager, repository, character)
    manager._stop_event = ObservedStopEvent(manager)
    monkeypatch.setattr(manager, "_initialize_components", fixture.initialize)
    manager.on_money_change(fixture.money_callback)
    manager.on_capture(fixture.capture_callback)
    assert manager.start()
    assert manager._stop_event.idle.wait(2)
    yield fixture
    fixture.release.set()
    manager.stop()
    if manager._capture_thread:
        manager._capture_thread.join(3)
    repository.close()


@pytest.mark.parametrize("stage", ["capture", "ocr", "money_callback", "capture_callback", "rate"])
def test_pause_requires_whole_admitted_iteration_to_drain(pipeline, stage):
    p, app = pipeline, pipeline.app
    original = app._activity_tracker.current_activity
    old = recovery_status(app).snapshot
    assert old is not None
    p.balance = 1250
    p.arm(stage)
    app.pause()
    busy = recovery_status(app)
    assert busy.capture_busy and busy.snapshot is None
    assert "finish" in busy.message.lower()
    success, message = app.discard_detected_activity(old)
    assert not success and "finish" in message.lower()
    assert app._activity_tracker.current_activity is original
    p.drain()
    ready = recovery_status(app)
    assert not ready.capture_busy and ready.snapshot is not None
    assert app.last_capture is p.callback_results[-1]
    assert app.last_capture.money.display_value == 1250
    assert not app.discard_detected_activity(old)[0]  # Admission retired old confirmation.
    assert app.discard_detected_activity(ready.snapshot)[0]
    assert app.last_capture is None
    assert app._activity_tracker.current_activity is None
    assert app._repository.export_session_data(app._data.db_session_id)["activities"] == []


def test_synchronous_callback_cannot_reenter_recovery_before_publication_finishes(pipeline):
    p, app = pipeline, pipeline.app
    old = recovery_status(app).snapshot
    attempts = []

    def try_recovery(_result):
        app.pause()
        attempts.append((recovery_status(app), app.discard_detected_activity(old)))

    p.extra_callback = try_recovery
    p.arm("rate")  # Callback has returned, but rate adjustment is still admitted.
    status, (success, message) = attempts[0]
    assert status.capture_busy and status.snapshot is None
    assert not success and "finish" in message.lower()
    assert app._activity_tracker.current_activity is not None
    p.drain()
    assert app.discard_detected_activity(recovery_status(app).snapshot)[0]


@pytest.mark.parametrize("stage", ["capture", "ocr", "capture_callback", "rate"])
def test_iteration_exception_clears_busy_before_error_or_idle_wait(pipeline, stage):
    p, app = pipeline, pipeline.app
    recovery_status(app)  # Meaningful missing API failure on the unmodified source.
    p.arm(stage, raises=True)
    app.pause()
    assert recovery_status(app).capture_busy
    p.release.set()
    boundary = app._stop_event.idle if stage == "capture_callback" else app._stop_event.error
    assert boundary.wait(2)
    status = recovery_status(app)
    assert not status.capture_busy and status.snapshot is not None
    assert app.discard_detected_activity(status.snapshot)[0]


def test_stop_timeout_and_new_start_retire_confirmations_and_recovery_wait(pipeline, monkeypatch):
    p, app = pipeline, pipeline.app
    old = recovery_status(app).snapshot
    p.arm("capture")
    worker = app._capture_thread
    real_join = worker.join
    monkeypatch.setattr(worker, "join", lambda timeout=None: real_join(0))
    app.stop()
    assert app.state is AppState.STOPPING and worker.is_alive()
    assert recovery_status(app).snapshot is None
    assert not app.discard_detected_activity(old)[0]
    assert not app.start()
    assert not p.captures[0].closed
    p.release.set()
    real_join(2)
    assert not worker.is_alive()
    assert app.state is AppState.STOPPED and p.captures[0].closed
    assert not recovery_status(app).capture_busy
    p.stage = None
    app._stop_event.idle.clear()
    assert app.start()
    assert app._stop_event.idle.wait(2)
    newer = app._activity_tracker.current_activity
    assert newer is not None
    assert not app.discard_detected_activity(old)[0]
    assert app._activity_tracker.current_activity is newer
    assert app.discard_detected_activity(recovery_status(app).snapshot)[0]
    assert recovery_status(app).waiting_for_balance
    app.stop()
    app._stop_event.idle.clear()
    assert app.start()
    assert app._stop_event.idle.wait(2)
    assert not recovery_status(app).waiting_for_balance
    assert app._activity_tracker.current_activity is not None


def test_pause_before_admission_keeps_previous_confirmation_eligible(pipeline):
    app = pipeline.app
    status = recovery_status(app)
    previous_captures = app._data.total_captures
    app.pause()  # Already paused; no admission and no revision change.
    assert app.discard_detected_activity(status.snapshot)[0]
    assert app._data.total_captures == previous_captures


def test_worker_checks_running_and_admits_under_the_same_lifecycle_lock(pipeline):
    p, app = pipeline, pipeline.app
    comparing, release_comparison, paused = threading.Event(), threading.Event(), threading.Event()

    class AdmissionState:
        def __eq__(self, state):
            if state is AppState.RUNNING:
                if threading.current_thread() is app._capture_thread:
                    comparing.set()
                    assert release_comparison.wait(3)
                return True
            return False

    with app._lifecycle_lock:
        p.stage = "capture"
        p.entered.clear()
        p.release.clear()
        app._stop_event.idle.clear()
        app.resume()
        app._state = AdmissionState()
    try:
        assert comparing.wait(2)
        # This exact state comparison used to happen outside the lifecycle lock.
        # Observe its ownership while held at a real event barrier.
        acquired = app._lifecycle_lock.acquire(blocking=False)
        if acquired:
            app._lifecycle_lock.release()
        assert not acquired, "RUNNING admission check must exclude concurrent Pause"
        pauser = threading.Thread(target=lambda: (app.pause(), paused.set()))
        pauser.start()
    finally:
        release_comparison.set()
    assert p.entered.wait(2)
    assert paused.wait(2)
    pauser.join(2)
    assert not pauser.is_alive()
    status = recovery_status(app)
    assert status.app_state is AppState.PAUSED and status.capture_busy
    assert status.snapshot is None
    p.drain()
    assert recovery_status(app).snapshot is not None


def test_money_callback_pause_and_discard_does_not_clear_the_outer_activity(pipeline):
    p, app = pipeline, pipeline.app
    old = recovery_status(app).snapshot
    original = app._activity_tracker.current_activity
    attempts = []

    def try_recovery(*_args):
        app.pause()
        attempts.append((recovery_status(app), app.discard_detected_activity(old)))

    app.on_money_change(try_recovery)
    p.balance = 1250
    p.arm("rate")
    status, (success, message) = attempts[0]
    assert status.capture_busy and status.snapshot is None
    assert not success and "finish" in message.lower()
    assert app._activity_tracker.current_activity is original
    assert app._data.mission_start_money == 1000
    p.drain()
    assert app.last_capture.activity_name == "Headhunter"
    assert app.discard_detected_activity(recovery_status(app).snapshot)[0]
