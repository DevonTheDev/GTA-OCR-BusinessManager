"""Deterministic capture pacing, with no native screen or system-clock edits."""
from types import SimpleNamespace

import numpy as np
import pytest

from src.capture import screen_capture


@pytest.fixture
def capture_clock(monkeypatch):
    clock = SimpleNamespace(wall=1000.0, elapsed=10.0, waits=[])
    def sleep(seconds):
        clock.waits.append(seconds)
        clock.elapsed += seconds
        clock.wall += seconds
    monkeypatch.setattr(screen_capture, 'time', SimpleNamespace(
        time=lambda: clock.wall, monotonic=lambda: clock.elapsed, sleep=sleep,
    ))
    monkeypatch.setattr(screen_capture, 'ResolutionScaler', lambda index: SimpleNamespace(
        width=2, height=2, offset=(0, 0), scale_factor=1.0,
    ))
    capture = screen_capture.ScreenCapture()
    monkeypatch.setattr(capture, '_ensure_mss', lambda: SimpleNamespace(
        grab=lambda monitor: np.zeros((2, 2, 4), dtype=np.uint8),
    ))
    capture.set_capture_rate(1.0)
    return capture, clock


@pytest.mark.parametrize('jump', [-3600.0, 3600.0])
def test_clock_adjustments_do_not_change_remaining_capture_wait(capture_clock, jump):
    capture, clock = capture_clock
    assert capture.capture_full_screen() is not None
    clock.elapsed += 0.25
    clock.wall += jump
    assert capture.capture_full_screen() is not None
    assert clock.waits == [0.75]


@pytest.mark.parametrize('jump', [-3600.0, 3600.0])
def test_capture_readiness_uses_elapsed_time_only(capture_clock, jump):
    capture, clock = capture_clock
    capture.capture_full_screen()
    clock.wall += jump
    clock.elapsed += 0.25
    assert not capture._should_capture()
    clock.elapsed += 0.75
    assert capture._should_capture()


def test_failed_capture_attempts_are_still_rate_limited(capture_clock, monkeypatch):
    capture, clock = capture_clock
    def fail():
        raise RuntimeError('synthetic capture unavailable')
    monkeypatch.setattr(capture, '_ensure_mss', fail)
    assert capture.capture_full_screen() is None
    assert capture.capture_full_screen() is None
    assert clock.waits == [1.0]


def test_first_capture_never_waits_even_at_zero_clock_origin(capture_clock):
    capture, clock = capture_clock
    clock.wall = clock.elapsed = 0.0
    assert capture._should_capture()
    assert capture.capture_full_screen() is not None
    assert clock.waits == []


def test_unlimited_and_explicit_no_wait_still_skip_throttle(capture_clock):
    capture, clock = capture_clock
    capture.capture_full_screen()
    capture.capture_full_screen(wait_for_rate=False)
    capture.set_capture_rate(0)
    capture.capture_full_screen()
    assert clock.waits == []


def test_batch_failure_is_paced_once_between_batches(capture_clock, monkeypatch):
    capture, clock = capture_clock
    def fail():
        raise RuntimeError('synthetic capture unavailable')
    monkeypatch.setattr(capture, '_ensure_mss', fail)
    # This fixture is only 2x2 pixels; use nonempty regions so the failure
    # exercises the native capture boundary instead of invalid crop geometry.
    regions = [capture.regions.full_screen, capture.regions.full_screen]
    assert capture.capture_multiple_regions(regions) == {0: None, 1: None}
    assert capture.capture_multiple_regions(regions) == {0: None, 1: None}
    assert clock.waits == [1.0]


@pytest.mark.parametrize('wait_for_rate', [True, False])
@pytest.mark.parametrize('outcome', ['success', 'failure', 'invalid'])
def test_batch_wait_option_preserves_completion_pacing(capture_clock, monkeypatch, wait_for_rate, outcome):
    capture, clock = capture_clock
    capture.capture_full_screen()
    clock.elapsed += 0.25
    regions = [capture.regions.full_screen]
    if outcome == 'failure':
        def fail():
            raise RuntimeError('synthetic capture unavailable')
        monkeypatch.setattr(capture, '_ensure_mss', fail)
    elif outcome == 'invalid':
        regions = [None]
        monkeypatch.setattr(capture, '_ensure_mss', lambda: pytest.fail('No valid pixels'))
    images = capture.capture_multiple_regions(regions, wait_for_rate=wait_for_rate)
    assert (images[0] is not None) == (outcome == 'success')
    assert clock.waits == ([0.75] if wait_for_rate else [])
    assert capture._last_capture_time == (11.0 if wait_for_rate else 10.25)
    # Even a no-wait failure/invalid batch records completion for later callers.
    capture._wait_for_rate_limit()
    assert clock.waits == ([0.75, 1.0] if wait_for_rate else [1.0])


@pytest.mark.parametrize('wait_for_rate', [True, False])
def test_empty_batch_ignores_wait_option_and_keeps_completion_time(capture_clock, wait_for_rate):
    capture, clock = capture_clock
    capture.capture_full_screen()
    completed = capture._last_capture_time
    assert capture.capture_multiple_regions([], wait_for_rate=wait_for_rate) == {}
    assert capture._last_capture_time == completed
    assert clock.waits == []
