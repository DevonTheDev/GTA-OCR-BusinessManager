"""Exercise image limits at the Windows-only boundary, without native OCR.

All caps are deliberate synthetic values. The real adapter, event loop, image
conversion, and preprocessing run unchanged; these tests do not assert a Windows
limit or validate recognition accuracy on gameplay images.
"""

import asyncio
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from src.capture.regions import ScreenRegions
from src.detection import ocr_engine
from src.detection.ocr_engine import OCREngine


NATIVE_TEXT = 'Mission: "Headhunter"\n  Take $1,234,567.  '


def native_response(image):
    width, height = image.size
    return SimpleNamespace(lines=[
        SimpleNamespace(text='Mission: "Headhunter"', words=[SimpleNamespace(
            text='"Headhunter"', bounding_rect=SimpleNamespace(
                x=width * 0.1, y=height * 0.25, width=width * 0.2, height=height * 0.5,
            ),
        )]),
        SimpleNamespace(text="  Take $1,234,567.  ", words=[]),
    ])


@pytest.fixture
def capped_engine(monkeypatch):
    """Double WinOCR's async call and projected static runtime cap property."""
    def make(limit=128, *, error=None, limit_error=None, response=native_response):
        state = SimpleNamespace(limit=limit, reads=0, calls=[])

        class NativeEngineMeta(type):
            @property
            def max_image_dimension(cls):
                state.reads += 1
                if limit_error is not None:
                    raise limit_error
                return state.limit

        class NativeEngine(metaclass=NativeEngineMeta):
            pass

        async def recognize_pil(image, language):
            await asyncio.sleep(0)
            state.calls.append((image, language))
            if error is not None:
                raise error
            if type(state.limit) is int and state.limit > 0:
                if max(image.size) > state.limit:
                    raise ValueError("Image exceeds the synthetic native cap")
            return response(image)

        state.module = SimpleNamespace(OcrEngine=NativeEngine, recognize_pil=recognize_pil)
        monkeypatch.setitem(sys.modules, "winocr", state.module)
        return OCREngine(language="en-US"), state

    yield make
    ocr_engine._cleanup_ocr_loop()


def patterned_image(size):
    """Use color variation across the entire image so cropping loses content."""
    width, height = size
    y, x = np.indices((height, width))
    pixels = np.stack((x % 256, y % 256, (x + y) % 256), axis=2).astype(np.uint8)
    return Image.fromarray(pixels)


def assert_input_bounds(result, size):
    width, height = size
    assert result.text == NATIVE_TEXT
    assert result.confidence is None
    assert len(result.words) == 1
    assert result.words[0]["text"] == '"Headhunter"'
    assert result.words[0]["confidence"] is None
    assert result.words[0]["bounds"] == pytest.approx({
        "x": width * 0.1, "y": height * 0.25,
        "width": width * 0.2, "height": height * 0.5,
    })


@pytest.mark.parametrize("size,limit,expected_size", [
    ((301, 101), 128, (128, 42)),
    ((101, 301), 128, (42, 128)),
    ((257, 257), 113, (113, 113)),
    ((1, 300), 97, (1, 97)),
    ((300, 1), 97, (97, 1)),
    ((3, 7), 1, (1, 1)),
], ids=["landscape", "portrait", "square", "one-pixel-wide", "one-pixel-high", "cap-one"])
@pytest.mark.parametrize("input_kind", ["pil", "numpy-bgr"])
def test_oversized_images_keep_whole_content_and_input_coordinates(
    capped_engine, size, limit, expected_size, input_kind,
):
    engine, state = capped_engine(limit)
    original = patterned_image(size)
    image = original if input_kind == "pil" else np.asarray(original)[:, :, ::-1].copy()
    before = image.tobytes()

    result = engine.recognize(image)

    assert_input_bounds(result, size)
    assert state.reads == 1
    assert len(state.calls) == 1
    received, language = state.calls[0]
    assert received.size == expected_size
    assert received.mode == "RGB"
    assert received is not image
    assert language == "en-US"
    assert received.tobytes() == original.resize(expected_size, Image.Resampling.LANCZOS).tobytes()
    assert image.tobytes() == before
    if input_kind == "pil":
        assert image.size == size


@pytest.mark.parametrize("size", [(33, 17), (128, 17), (17, 128), (128, 128)])
def test_images_at_or_below_limit_are_untouched(capped_engine, size):
    engine, state = capped_engine()
    image = patterned_image(size)
    image.info["source"] = "preserve metadata"
    before = image.tobytes()

    result = engine.recognize(image)

    assert_input_bounds(result, size)
    assert state.calls[0][0] is image
    assert image.tobytes() == before
    assert image.info == {"source": "preserve metadata"}


def test_rounding_uses_separate_actual_axis_ratios(capped_engine):
    bounds = {"x": 9.25, "y": 7.75, "width": 49.5, "height": 18.25}
    response = SimpleNamespace(lines=[SimpleNamespace(text="A, B!", words=[
        SimpleNamespace(text="B!", bounding_rect=SimpleNamespace(**bounds)),
        SimpleNamespace(text="A,", bounding_rect=SimpleNamespace(**bounds)),
    ])])
    engine, state = capped_engine(response=lambda image: response)

    result = engine.recognize(Image.new("RGB", (301, 101)))

    assert state.calls[0][0].size == (128, 42)
    assert result.text == "A, B!"
    assert result.confidence is None
    assert [word["text"] for word in result.words] == ["B!", "A,"]
    for word in result.words:
        assert word["confidence"] is None
        assert word["bounds"] == pytest.approx({
            "x": 9.25 * 301 / 128, "y": 7.75 * 101 / 42,
            "width": 49.5 * 301 / 128, "height": 18.25 * 101 / 42,
        })
    # Restoring coordinates must not change the backend's response object.
    assert vars(response.lines[0].words[0].bounding_rect) == bounds


def test_runtime_limit_is_read_again_for_every_recognition(capped_engine):
    engine, state = capped_engine()
    image = patterned_image((4200, 101))

    for limit, expected_size in [
        (1733, (1733, 41)), (2400, (2400, 57)),
        (4096, (4096, 98)), (5000, (4200, 101)),
    ]:
        state.limit = limit
        result = engine.recognize(image)
        assert_input_bounds(result, image.size)
        assert state.calls[-1][0].size == expected_size

    assert state.reads == 4
    assert state.calls[-1][0] is image


@pytest.mark.parametrize("limit", [
    0, -1, True, False, 1.5, 2400.0, "2400", None, float("nan"), float("inf"), np.int64(2400),
], ids=[
    "zero", "negative", "true", "false", "fraction", "float", "string", "none",
    "nan", "infinity", "numpy-int",
])
def test_invalid_runtime_limit_returns_existing_failure_without_backend_call(capped_engine, limit):
    engine, state = capped_engine(limit)

    result = engine.recognize(Image.new("RGB", (12, 8)))

    assert (result.text, result.confidence, result.words) == ("", 0.0, [])
    assert state.calls == []


@pytest.mark.parametrize("missing", ["engine", "limit"])
def test_missing_runtime_limit_returns_existing_failure(capped_engine, missing):
    engine, state = capped_engine()
    if missing == "engine":
        del state.module.OcrEngine
    else:
        state.module.OcrEngine = type("NativeEngineWithoutLimit", (), {})

    result = engine.recognize(Image.new("RGB", (12, 8)))

    assert (result.text, result.confidence, result.words) == ("", 0.0, [])
    assert state.calls == []


def test_runtime_limit_property_error_returns_existing_failure(capped_engine):
    engine, state = capped_engine(limit_error=RuntimeError("Native property unavailable"))

    result = engine.recognize(Image.new("RGB", (12, 8)))

    assert (result.text, result.confidence, result.words) == ("", 0.0, [])
    assert state.calls == []


def test_backend_failure_after_resizing_returns_existing_failure(capped_engine):
    engine, state = capped_engine(error=RuntimeError("Native OCR failed"))
    image = patterned_image((301, 101))
    before = image.tobytes()

    result = engine.recognize(image)

    assert state.calls[0][0].size == (128, 42)
    assert (result.text, result.confidence, result.words) == ("", 0.0, [])
    assert image.size == (301, 101)
    assert image.tobytes() == before


def test_resize_failure_returns_existing_failure(capped_engine, monkeypatch):
    engine, state = capped_engine()
    image = Image.new("RGB", (301, 101))

    def failed_resize(*args, **kwargs):
        raise OSError("Cannot resize image")

    monkeypatch.setattr(image, "resize", failed_resize)

    result = engine.recognize(image)

    assert (result.text, result.confidence, result.words) == ("", 0.0, [])
    assert state.calls == []


@pytest.mark.parametrize("resolution", [(2560, 1440), (3840, 2160)], ids=["1440p", "4k"])
@pytest.mark.parametrize("region_name", ["mission_text", "mission_banner"])
def test_real_high_resolution_preprocessing_is_bounded_in_preprocessed_coordinates(
    capped_engine, resolution, region_name,
):
    engine, state = capped_engine(2400)
    region = getattr(ScreenRegions(), region_name)
    left, top, right, bottom = region.to_absolute(*resolution)
    size = (right - left, bottom - top)
    image = np.asarray(patterned_image(size))[:, :, ::-1].copy()
    original = image.copy()
    processed_size = (size[0] * 2, size[1] * 2)
    assert max(processed_size) > state.limit

    result = engine.recognize_preprocessed(image)

    assert_input_bounds(result, processed_size)
    received = state.calls[0][0]
    assert received.width == state.limit
    assert received.height == processed_size[1] * state.limit // processed_size[0]
    assert received.mode == "RGB"
    pixels = np.asarray(received)
    np.testing.assert_array_equal(pixels[:, :, 0], pixels[:, :, 1])
    np.testing.assert_array_equal(pixels[:, :, 0], pixels[:, :, 2])
    assert pixels.min() == 0 and pixels.max() == 255
    np.testing.assert_array_equal(image, original)
