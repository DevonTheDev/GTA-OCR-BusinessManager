"""One screen observation supplies every requested crop; no native capture."""

from types import SimpleNamespace

import numpy as np
import pytest

from src.capture import screen_capture
from src.capture.regions import Region


@pytest.fixture
def capture(monkeypatch):
    scaler = SimpleNamespace(width=100, height=80, offset=(-300, 20), scale_factor=1.0)
    monkeypatch.setattr(screen_capture, "ResolutionScaler", lambda _index: scaler)
    result = screen_capture.ScreenCapture()
    result.waits = []
    result.grabs = []
    monkeypatch.setattr(result, "_wait_for_rate_limit", lambda: result.waits.append(True))

    def grab(rect):
        result.grabs.append(rect.copy())
        height, width = rect["height"], rect["width"]
        image = np.empty((height, width, 4), dtype=np.uint8)
        image[:, :, 0] = np.arange(width) + rect["left"] + 300
        image[:, :, 1] = np.arange(height)[:, None] + rect["top"] - 20
        image[:, :, 2] = len(result.grabs)
        image[:, :, 3] = 255
        return image

    monkeypatch.setattr(result, "_ensure_mss", lambda: SimpleNamespace(grab=grab))
    return result


def test_all_hud_images_come_from_one_screen_observation(capture):
    regions = capture.regions
    requested = [regions.full_screen, regions.money_display, regions.mission_text,
                 regions.center_prompt, regions.timer_bottom_right, regions.mission_banner]
    images = capture.capture_multiple_regions(requested)

    assert len(capture.grabs) == 1
    assert capture.waits == [True]
    assert list(images) == list(range(6))
    for index, region in enumerate(requested):
        left, top, right, bottom = region.to_absolute(100, 80)
        crop = images[index]
        assert crop.shape == (bottom - top, right - left, 3)
        assert np.all(crop[:, :, 2] == 1)
        np.testing.assert_array_equal(crop[:, :, 0], np.broadcast_to(np.arange(left, right), crop.shape[:2]))
        np.testing.assert_array_equal(crop[:, :, 1], np.broadcast_to(np.arange(top, bottom)[:, None], crop.shape[:2]))


def test_subset_grabs_only_its_enclosing_rectangle_with_monitor_offset(capture):
    regions = [Region(.1, .2, .2, .2), Region(.5, .5, .1, .1)]
    images = capture.capture_multiple_regions(regions)

    assert capture.grabs == [{"left": -290, "top": 36, "width": 50, "height": 32}]
    assert images[0][0, 0].tolist() == [10, 16, 1]
    assert images[1][0, 0].tolist() == [50, 40, 1]
    assert images[0].shape == (16, 20, 3)
    assert images[1].shape == (8, 10, 3)


def test_overlapping_and_duplicate_crops_have_independent_storage(capture):
    whole = capture.regions.full_screen
    small = Region(.1, .2, .2, .2)
    images = capture.capture_multiple_regions([small, whole, small])

    assert len(capture.grabs) == 1
    assert not np.shares_memory(images[0], images[1])
    assert not np.shares_memory(images[0], images[2])
    expected = images[2].copy()
    images[0][:] = 0
    np.testing.assert_array_equal(images[2], expected)
    np.testing.assert_array_equal(images[1][16:32, 10:30], expected)


def test_later_batch_observes_new_screen_without_changing_earlier_crops(capture):
    requested = [capture.regions.full_screen, capture.regions.mission_text]
    first = capture.capture_multiple_regions(requested)
    second = capture.capture_multiple_regions(requested)
    assert len(capture.grabs) == 2 and capture.waits == [True, True]
    assert all(np.all(crop[:, :, 2] == 1) for crop in first.values())
    assert all(np.all(crop[:, :, 2] == 2) for crop in second.values())


@pytest.mark.parametrize("bad", [None, Region(0, 0, 0, .5), Region(0, 0, -.1, .5),
                                 Region(float("nan"), 0, .1, .1), Region(0, 0, .001, .001)])
def test_invalid_region_keeps_its_index_without_discarding_valid_crops(capture, bad):
    region = Region(.5, .5, .1, .1)
    images = capture.capture_multiple_regions([bad, region, bad, region])

    assert list(images) == [0, 1, 2, 3]
    assert images[0] is None and images[2] is None
    assert capture.grabs == [{"left": -250, "top": 60, "width": 10, "height": 8}]
    assert images[1][0, 0].tolist() == [50, 40, 1]
    np.testing.assert_array_equal(images[1], images[3])
    assert capture.waits == [True]


def test_empty_batch_does_not_capture_wait_or_change_completion_time(capture):
    capture._last_capture_time = 123.0
    assert capture.capture_multiple_regions([]) == {}
    assert capture.grabs == [] and capture.waits == []
    assert capture._last_capture_time == 123.0


def test_single_region_batch_preserves_existing_absolute_capture_coordinates(capture):
    region = Region(.173, .237, .319, .283)
    image = capture.capture_multiple_regions([region])[0]
    assert capture.grabs == [region.to_mss_monitor(100, 80, -300, 20)]
    left, top, right, bottom = region.to_absolute(100, 80)
    assert image.shape == (bottom - top, right - left, 3)
    assert image[0, 0].tolist() == [left, top, 1]
    assert image[-1, -1].tolist() == [right - 1, bottom - 1, 1]


def test_batch_keeps_existing_custom_rectangles_outside_monitor_bounds(capture):
    regions = [Region(-.1, -.1, .2, .2), Region(.9, .9, .2, .2)]
    images = capture.capture_multiple_regions(regions)
    assert capture.grabs == [{"left": -310, "top": 12, "width": 120, "height": 96}]
    assert images[0].shape == images[1].shape == (16, 20, 3)
    assert images[0][0, 0].tolist() == [246, 248, 1]
    assert images[1][0, 0].tolist() == [90, 72, 1]


def test_one_failed_grab_returns_no_mixed_retry_images(capture, monkeypatch):
    calls = []

    def fail_once(rect):
        calls.append(rect)
        if len(calls) == 1:
            raise RuntimeError("synthetic first screenshot failure")
        return np.ones((rect["height"], rect["width"], 4), dtype=np.uint8)

    monkeypatch.setattr(capture, "_ensure_mss", lambda: SimpleNamespace(grab=fail_once))
    requested = [capture.regions.full_screen, capture.regions.mission_text]
    assert capture.capture_multiple_regions(requested) == {0: None, 1: None}
    assert len(calls) == 1
    assert capture.waits == [True]


@pytest.mark.parametrize("shape", [(79, 100, 4), (80, 99, 4), (80, 100), (80, 100, 2)])
def test_incomplete_screenshot_is_not_silently_clipped_into_crops(capture, monkeypatch, shape):
    monkeypatch.setattr(capture, "_ensure_mss", lambda: SimpleNamespace(
        grab=lambda _rect: np.zeros(shape, dtype=np.uint8),
    ))
    requested = [capture.regions.full_screen, capture.regions.mission_text]
    assert capture.capture_multiple_regions(requested) == {0: None, 1: None}


def test_capture_pacing_records_completion_after_crop_work(capture, monkeypatch):
    clock = iter([10.0, 20.0, 30.0, 40.0])
    monkeypatch.setattr(screen_capture.time, "monotonic", lambda: next(clock))
    capture.capture_multiple_regions([capture.regions.full_screen, capture.regions.mission_text])
    assert capture._last_capture_time == 10.0


def test_all_invalid_regions_are_paced_without_opening_native_capture(capture, monkeypatch):
    monkeypatch.setattr(capture, "_ensure_mss", lambda: pytest.fail("No pixels to capture"))
    monkeypatch.setattr(screen_capture.time, "monotonic", lambda: 33.0)
    assert capture.capture_multiple_regions([None, Region(0, 0, 0, .1)]) == {0: None, 1: None}
    assert capture._last_capture_time == 33.0
    assert capture.waits == [True]
