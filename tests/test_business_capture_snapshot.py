"""Business parsing from synthetic native pixels, not an OCR accuracy test."""

import copy
from types import SimpleNamespace

import numpy as np
import pytest

from src.app import GTABusinessManager
from src.capture import screen_capture
from src.capture.regions import ScreenRegions
from src.config.settings import Settings
from src.detection.parsers.business_parser import BusinessType


@pytest.fixture
def business_screen(tmp_path, monkeypatch):
    # Install before construction: the real scaler would enumerate monitors.
    monkeypatch.setattr(screen_capture, "ResolutionScaler", lambda _index: SimpleNamespace(
        width=100, height=100, offset=(-300, 20), scale_factor=1.0,
    ))
    capture = screen_capture.ScreenCapture(regions=ScreenRegions())
    manager = GTABusinessManager(Settings(tmp_path / "settings.yaml"))
    manager._capture = capture
    screen = SimpleNamespace(
        manager=manager, capture=capture, available=0, grabs=[], events=[],
        waits=[], ocr_calls=0, empty_fields=set(), ocr_error_at=None,
        grab_error=None,
    )
    screen.frames = []
    for frame in range(2):
        pixels = np.zeros((100, 100, 4), dtype=np.uint8)
        for field, region in enumerate(capture.regions.get_business_regions().values()):
            left, top, right, bottom = region.to_absolute(100, 100)
            pixels[top:bottom, left:right] = (frame + 1, field + 1, 0, 255)
        screen.frames.append(pixels)

    def grab(rectangle):
        screen.events.append("grab")
        screen.grabs.append(rectangle.copy())
        if screen.grab_error == "raise":
            raise RuntimeError("synthetic native grab failure")
        left, top = rectangle["left"] + 300, rectangle["top"] - 20
        image = screen.frames[screen.available][
            top:top + rectangle["height"], left:left + rectangle["width"],
        ].copy()
        return image[:-1] if screen.grab_error == "shape" else image

    def recognize(image, **options):
        screen.events.append("ocr")
        screen.ocr_calls += 1
        assert options == {"invert": True, "scale": 2.0}
        frame, field = (int(value) - 1 for value in image[0, 0, :2])
        assert np.all(image[:, :, :2] == (frame + 1, field + 1))
        # The first OCR boundary makes a different screen available. Existing
        # crops still contain A; a subsequent native grab can only observe B.
        screen.available = 1
        if screen.ocr_calls == screen.ocr_error_at:
            raise RuntimeError("synthetic OCR failure")
        text = (
            ("Bunker Stock: 50%", "Supplies: 75%", "Value: $123,456"),
            ("Bunker Stock: 100%", "Supplies: 0%", "Value: $654,321"),
        )[frame][field]
        return SimpleNamespace(text="" if field in screen.empty_fields else text)

    monkeypatch.setattr(capture, "_ensure_mss", lambda: SimpleNamespace(grab=grab))
    monkeypatch.setattr(capture, "_wait_for_rate_limit", lambda: screen.waits.append(True))
    manager._ocr = SimpleNamespace(is_available=True, recognize_preprocessed=recognize)
    return screen


def snapshot(manager):
    return copy.deepcopy((manager.data.business_states,
                          manager._optimizer._business_states,
                          manager._business_parser.get_all_last_readings()))


def assert_reading(manager, expected):
    state = manager.get_business_state("bunker")
    assert (state["stock"], state["supply"], state["value"]) == expected
    optimized = manager._optimizer._business_states["bunker"]
    assert (optimized.stock_percent, optimized.supply_percent, optimized.estimated_value) == expected
    parsed = manager._business_parser.get_last_reading(BusinessType.BUNKER)
    assert (parsed.stock_level, parsed.supply_level, parsed.stock_value) == expected


@pytest.mark.parametrize("selected", [False, True], ids=["automatic", "selected"])
def test_business_fields_share_one_native_observation_before_ocr(business_screen, selected):
    screen = business_screen
    if selected:
        screen.manager.set_business_screen_target("bunker")
    screen.manager._process_business_computer()

    # This assertion intentionally comes first: the old path publishes mixed
    # (50, 0, 654321), demonstrating the data error before checking call count.
    assert_reading(screen.manager, (50, 75, 123456))
    assert screen.events == ["grab", "ocr", "ocr", "ocr"]
    assert screen.waits == []
    screen.manager._process_business_computer()
    assert_reading(screen.manager, (100, 0, 654321))
    assert screen.events == ["grab", "ocr", "ocr", "ocr"] * 2
    assert len(screen.grabs) == 2 and screen.waits == []


@pytest.mark.parametrize("failure", ["raise", "shape"])
def test_shared_grab_failure_preserves_all_caches_without_recapturing(business_screen, failure):
    screen = business_screen
    screen.manager._process_business_computer()
    before = snapshot(screen.manager)
    screen.events.clear()
    screen.grabs.clear()
    screen.grab_error = failure
    screen.manager._process_business_computer()
    assert snapshot(screen.manager) == before
    assert screen.events == ["grab"] and len(screen.grabs) == 1
    assert screen.waits == []
    screen.grab_error = None
    screen.manager._process_business_computer()
    assert_reading(screen.manager, (100, 0, 654321))


@pytest.mark.parametrize("missing", ["invalid", "empty"])
def test_available_fields_survive_an_invalid_crop_or_empty_ocr(business_screen, missing):
    screen = business_screen
    screen.manager.set_business_screen_target("bunker")
    if missing == "invalid":
        screen.capture.regions.business_stock = None
    else:
        screen.empty_fields.add(0)
    screen.manager._process_business_computer()
    state = screen.manager.get_business_state("bunker")
    assert (state["stock"], state["supply"], state["value"]) == (None, 75, 123456)
    assert len(screen.grabs) == 1
    assert screen.ocr_calls == (2 if missing == "invalid" else 3)


@pytest.mark.parametrize("failure", ["empty", "ocr_exception", "invalid"])
def test_unusable_business_batch_does_not_publish_partial_state(business_screen, failure, caplog):
    screen = business_screen
    screen.manager._process_business_computer()
    before = snapshot(screen.manager)
    screen.events.clear()
    if failure == "empty":
        screen.empty_fields.update(range(3))
    elif failure == "ocr_exception":
        screen.ocr_error_at = screen.ocr_calls + 2
    else:
        screen.capture.regions.get_business_regions = lambda: {"stock": None, "supplies": None, "value": None}
    screen.manager._process_business_computer()
    assert snapshot(screen.manager) == before
    assert screen.events == {
        "empty": ["grab", "ocr", "ocr", "ocr"],
        "ocr_exception": ["grab", "ocr", "ocr"],
        "invalid": [],
    }[failure]
    assert ("Error processing business computer: synthetic OCR failure" in caplog.text) == (failure == "ocr_exception")
