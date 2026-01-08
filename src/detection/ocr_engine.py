"""OCR engine wrapper using Windows OCR API."""

import asyncio
import atexit
from typing import Optional
from dataclasses import dataclass

import numpy as np
from PIL import Image

from ..utils.logging import get_logger


logger = get_logger("ocr")


# Module-level event loop for OCR operations
_ocr_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_ocr_loop() -> asyncio.AbstractEventLoop:
    """Get or create a dedicated event loop for OCR operations.

    This reuses a single loop to avoid memory leaks from creating
    multiple event loops.
    """
    global _ocr_loop
    if _ocr_loop is None or _ocr_loop.is_closed():
        _ocr_loop = asyncio.new_event_loop()
    return _ocr_loop


def _cleanup_ocr_loop() -> None:
    """Clean up the OCR event loop on exit."""
    global _ocr_loop
    if _ocr_loop is not None and not _ocr_loop.is_closed():
        try:
            _ocr_loop.close()
            logger.debug("OCR event loop closed")
        except Exception as e:
            logger.debug(f"Error closing OCR event loop: {e}")
        _ocr_loop = None


# Register cleanup on module exit
atexit.register(_cleanup_ocr_loop)


@dataclass
class OCRResult:
    """Result from OCR operation."""

    text: str
    confidence: float
    words: list[dict]  # List of {text, confidence, bounds}

    @property
    def is_empty(self) -> bool:
        """Check if no text was detected."""
        return len(self.text.strip()) == 0

    def get_text_lines(self) -> list[str]:
        """Get text split into lines."""
        return [line.strip() for line in self.text.split("\n") if line.strip()]


class OCREngine:
    """OCR engine using Windows OCR API via winocr.

    Falls back to a simpler approach if winocr is not available.
    """

    def __init__(self, language: str = "en"):
        """Initialize OCR engine.

        Args:
            language: Language code for OCR (default: English)
        """
        self._language = language
        self._winocr_available = False
        self._check_winocr()

    def _check_winocr(self) -> None:
        """Check if winocr is available."""
        try:
            import winocr
            self._winocr_available = True
            logger.info("Using Windows OCR API (winocr)")
        except ImportError:
            logger.warning(
                "winocr not available. Install with: pip install winocr"
            )
            self._winocr_available = False

    async def _recognize_async(self, image: Image.Image) -> OCRResult:
        """Perform OCR asynchronously using winocr.

        Args:
            image: PIL Image to process

        Returns:
            OCRResult with detected text
        """
        import winocr

        # winocr expects the image in a specific format
        result = await winocr.recognize_pil(image, self._language)

        # Parse winocr result (result is a Windows.Media.Ocr.OcrResult object)
        words = []
        all_text = []
        total_confidence = 0.0

        # Access the Lines property of the OcrResult object
        for line in result.Lines:
            line_text = line.Text  # Get the text from the line
            all_text.append(line_text)

            # Access the Words property of each OcrLine object
            for word in line.Words:
                # Get bounding rectangle
                bounds = word.BoundingRect
                word_info = {
                    "text": word.Text,
                    "confidence": float(word.Confidence),
                    "bounds": {
                        "x": bounds.X,
                        "y": bounds.Y,
                        "width": bounds.Width,
                        "height": bounds.Height,
                    },
                }
                words.append(word_info)
                total_confidence += float(word.Confidence)

        avg_confidence = total_confidence / len(words) if words else 0.0

        return OCRResult(
            text="\n".join(all_text),
            confidence=avg_confidence,
            words=words,
        )

    def recognize(self, image: np.ndarray | Image.Image) -> OCRResult:
        """Perform OCR on an image.

        Args:
            image: Image as numpy array (BGR) or PIL Image

        Returns:
            OCRResult with detected text
        """
        # Convert numpy array to PIL Image if needed
        if isinstance(image, np.ndarray):
            # Assume BGR format from OpenCV/mss, convert to RGB
            if len(image.shape) == 3 and image.shape[2] == 3:
                image = Image.fromarray(image[:, :, ::-1])
            else:
                image = Image.fromarray(image)

        if self._winocr_available:
            # Run async OCR in shared event loop (reused to avoid memory leaks)
            loop = _get_ocr_loop()
            try:
                return loop.run_until_complete(self._recognize_async(image))
            except Exception as e:
                logger.error(f"OCR failed: {e}")
                return OCRResult(text="", confidence=0.0, words=[])
        else:
            # Fallback: no OCR available
            logger.warning("No OCR backend available")
            return OCRResult(text="", confidence=0.0, words=[])

    def recognize_region(
        self,
        image: np.ndarray,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> OCRResult:
        """Perform OCR on a specific region of an image.

        Args:
            image: Full image as numpy array
            x: Left edge of region
            y: Top edge of region
            width: Region width
            height: Region height

        Returns:
            OCRResult with detected text
        """
        # Crop the region
        region = image[y : y + height, x : x + width]
        return self.recognize(region)

    def preprocess_for_ocr(
        self,
        image: np.ndarray,
        threshold: bool = True,
        invert: bool = False,
        scale: float = 1.0,
    ) -> np.ndarray:
        """Preprocess image for better OCR accuracy.

        Args:
            image: Input image (BGR numpy array)
            threshold: Apply adaptive thresholding
            invert: Invert colors (useful for white text on dark background)
            scale: Scale factor for resizing

        Returns:
            Preprocessed image
        """
        import cv2

        result = image.copy()

        # Scale if needed
        if scale != 1.0:
            result = cv2.resize(
                result,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA,
            )

        # Convert to grayscale
        if len(result.shape) == 3:
            result = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)

        # Invert if needed (GTA has white text on dark backgrounds)
        if invert:
            result = cv2.bitwise_not(result)

        # Apply thresholding
        if threshold:
            result = cv2.adaptiveThreshold(
                result,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                11,
                2,
            )

        return result

    def recognize_preprocessed(
        self,
        image: np.ndarray,
        invert: bool = True,
        scale: float = 2.0,
    ) -> OCRResult:
        """Preprocess and recognize an image.

        Useful for GTA's UI text which is typically white on dark.

        Args:
            image: Input image (BGR numpy array)
            invert: Invert colors
            scale: Scale factor

        Returns:
            OCRResult with detected text
        """
        processed = self.preprocess_for_ocr(
            image,
            threshold=True,
            invert=invert,
            scale=scale,
        )
        # Convert grayscale back to RGB for winocr
        import cv2
        processed_rgb = cv2.cvtColor(processed, cv2.COLOR_GRAY2RGB)
        return self.recognize(processed_rgb)

    @property
    def is_available(self) -> bool:
        """Check if OCR backend is available."""
        return self._winocr_available
