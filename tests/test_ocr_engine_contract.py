"""Exercise the Windows adapter with documented WinOCR-shaped responses.

Only the Windows-only library boundary is doubled. Recognition, preprocessing,
image conversion, and the dedicated event loop use the production code.
"""

import asyncio
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from src.detection import ocr_engine
from src.detection.ocr_engine import OCREngine


@dataclass(frozen=True, slots=True)
class NativeRect:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class NativeWord:
    text: str
    bounding_rect: NativeRect


@dataclass(frozen=True, slots=True)
class NativeLine:
    text: str
    words: list[NativeWord]


@dataclass(frozen=True, slots=True)
class NativeResult:
    lines: list[NativeLine]


@pytest.fixture
def native_engine(monkeypatch):
    """Replace winocr, whose actual WinRT implementation needs Windows."""

    def make(response, *, language="en", error=None):
        calls = []

        async def recognize_pil(image, lang):
            # Yield once to ensure the production adapter awaits the backend.
            await asyncio.sleep(0)
            calls.append((image, lang))
            if error is not None:
                raise error
            return response

        monkeypatch.setitem(sys.modules, "winocr", SimpleNamespace(recognize_pil=recognize_pil))
        return OCREngine(language=language), calls

    yield make
    ocr_engine._cleanup_ocr_loop()


def test_recognize_preserves_native_lines_punctuation_and_word_bounds(native_engine):
    response = NativeResult(
        [
            NativeLine(
                'Mission: "Sightseer"',
                [
                    NativeWord("Mission:", NativeRect(120.5, 40.25, 85.75, 18.0)),
                    NativeWord('"Sightseer"', NativeRect(211.0, 40.25, 99.5, 18.0)),
                ],
            ),
            NativeLine(
                "  Take $1,234,567.  ",
                [
                    NativeWord("Take", NativeRect(12.5, 9.0, 36.0, 20.5)),
                    NativeWord("$1,234,567.", NativeRect(52.5, 9.0, 105.0, 20.5)),
                ],
            ),
        ]
    )
    engine, _ = native_engine(response)

    result = engine.recognize(Image.new("RGB", (400, 100)))

    # Backend ordering and line text are authoritative, even when bounds differ.
    assert result.text == 'Mission: "Sightseer"\n  Take $1,234,567.  '
    assert result.get_text_lines() == ['Mission: "Sightseer"', "Take $1,234,567."]
    assert not result.is_empty
    assert result.confidence is None
    assert result.words == [
        {
            "text": "Mission:", "confidence": None,
            "bounds": {"x": 120.5, "y": 40.25, "width": 85.75, "height": 18.0},
        },
        {
            "text": '"Sightseer"', "confidence": None,
            "bounds": {"x": 211.0, "y": 40.25, "width": 99.5, "height": 18.0},
        },
        {
            "text": "Take", "confidence": None,
            "bounds": {"x": 12.5, "y": 9.0, "width": 36.0, "height": 20.5},
        },
        {
            "text": "$1,234,567.", "confidence": None,
            "bounds": {"x": 52.5, "y": 9.0, "width": 105.0, "height": 20.5},
        },
    ]


@pytest.mark.parametrize(
    "lines,text",
    [
        ([], ""),
        ([NativeLine("", [])], ""),
        (
            [NativeLine("Mission passed!", []), NativeLine("", []), NativeLine("+$10,000", [])],
            "Mission passed!\n\n+$10,000",
        ),
    ],
    ids=["no-lines", "empty-line", "text-without-words"],
)
def test_successful_native_output_without_words_has_unknown_confidence(native_engine, lines, text):
    engine, _ = native_engine(NativeResult(lines))

    result = engine.recognize(Image.new("RGB", (100, 40)))

    assert result.text == text
    assert result.words == []
    assert result.confidence is None
    assert result.is_empty == (not text.strip())


@pytest.mark.parametrize(
    "response",
    [
        None,
        SimpleNamespace(),
        SimpleNamespace(lines=None),
        SimpleNamespace(lines=[SimpleNamespace(words=[])]),
        SimpleNamespace(lines=[SimpleNamespace(text=None, words=[])]),
        SimpleNamespace(lines=[SimpleNamespace(text="Mission", words=None)]),
        SimpleNamespace(lines=[SimpleNamespace(text="Mission", words=[SimpleNamespace(text="X")])]),
        NativeResult([
            NativeLine("Valid", []),
            NativeLine("Broken", [
                SimpleNamespace(text="X", bounding_rect=SimpleNamespace(x=1, y=2, width=3)),
            ]),
        ]),
    ],
    ids=[
        "null-result", "missing-lines", "null-lines", "missing-line-text", "null-line-text",
        "null-words", "missing-word-bounds", "partial-result-missing-bound-height",
    ],
)
def test_malformed_native_response_returns_empty_result(native_engine, response):
    engine, _ = native_engine(response)

    result = engine.recognize(Image.new("RGB", (100, 40)))

    assert result.text == ""
    assert result.words == []
    assert result.confidence == 0.0
    assert result.is_empty


def test_backend_exception_returns_empty_result(native_engine):
    engine, _ = native_engine(None, error=RuntimeError("native OCR failed"))

    result = engine.recognize(Image.new("RGB", (100, 40)))

    assert result.text == ""
    assert result.words == []
    assert result.confidence == 0.0


def test_unavailable_backend_returns_empty_result(monkeypatch):
    monkeypatch.setitem(sys.modules, "winocr", None)
    engine = OCREngine()

    result = engine.recognize(Image.new("RGB", (100, 40)))

    assert not engine.is_available
    assert result.text == ""
    assert result.words == []
    assert result.confidence == 0.0


def test_recognize_converts_bgr_to_rgb_without_changing_input(native_engine):
    engine, calls = native_engine(NativeResult([NativeLine("BGR", [])]))
    image = np.array([[[10, 20, 30], [40, 50, 60]]], dtype=np.uint8)
    original = image.copy()

    result = engine.recognize(image)

    assert result.text == "BGR"
    assert len(calls) == 1
    assert calls[0][0].mode == "RGB"
    assert calls[0][0].getpixel((0, 0)) == (30, 20, 10)
    assert calls[0][0].getpixel((1, 0)) == (60, 50, 40)
    np.testing.assert_array_equal(image, original)


@pytest.mark.parametrize("language", ["en", "en-US", "ja"])
def test_recognize_passes_pil_image_and_language_through(native_engine, language):
    engine, calls = native_engine(NativeResult([NativeLine("PIL", [])]), language=language)
    image = Image.new("RGB", (12, 8), (17, 31, 59))

    result = engine.recognize(image)

    assert result.text == "PIL"
    assert engine.is_available
    assert len(calls) == 1
    assert calls[0][0] is image
    assert calls[0][1] == language


def test_recognize_accepts_grayscale_array(native_engine):
    engine, calls = native_engine(NativeResult([NativeLine("Gray", [])]))

    result = engine.recognize(np.array([[10, 240]], dtype=np.uint8))

    assert result.text == "Gray"
    assert calls[0][0].mode == "L"
    assert calls[0][0].getpixel((0, 0)) == 10
    assert calls[0][0].getpixel((1, 0)) == 240


def test_preprocessed_recognition_uses_real_image_pipeline(native_engine):
    engine, calls = native_engine(NativeResult([NativeLine("Mission: Headhunter", [])]))
    image = np.zeros((20, 30, 3), dtype=np.uint8)
    image[6:14, 8:22] = (255, 255, 255)
    original = image.copy()

    result = engine.recognize_preprocessed(image, invert=True, scale=2.0)

    assert result.text == "Mission: Headhunter"
    assert result.confidence is None
    assert len(calls) == 1
    received = calls[0][0]
    assert received.mode == "RGB"
    assert received.size == (60, 40)
    pixels = np.asarray(received)
    assert set(np.unique(pixels)) == {0, 255}
    np.testing.assert_array_equal(pixels[:, :, 0], pixels[:, :, 1])
    np.testing.assert_array_equal(pixels[:, :, 0], pixels[:, :, 2])
    np.testing.assert_array_equal(image, original)
