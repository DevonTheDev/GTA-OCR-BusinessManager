"""Live Windows pipeline checks or a bounded single-image OCR diagnostic."""

import argparse
from contextlib import closing, redirect_stdout
import json
import sys
import time


def _native_ocr_failed(result):
    """Recognize the production native failure sentinel, not valid empty text."""
    return result.text == "" and result.confidence == 0.0 and not result.words


def test_screen_capture():
    """Test basic screen capture functionality."""
    print("Testing screen capture...")

    try:
        from src.capture.screen_capture import ScreenCapture

        with closing(ScreenCapture()) as capture:
            print(f"  Resolution: {capture.resolution}")
            print(f"  Scale factor: {capture.scale_factor}")

            # Both captures are required for this check to pass.
            print("  Capturing full screen...")
            img = capture.capture_full_screen()
            if img is None:
                print("  Failed to capture")
                return False
            print(f"  Success! Shape: {img.shape}")

            print("  Capturing money region...")
            money_img = capture.capture_money_display()
            if money_img is None:
                print("  Failed to capture money region")
                return False
            print(f"  Success! Shape: {money_img.shape}")

        print("  Screen capture test PASSED")
        return True

    except Exception as e:
        print(f"  Screen capture test FAILED: {e}")
        return False


def test_ocr():
    """Test OCR functionality."""
    print("\nTesting OCR...")

    try:
        from src.detection.ocr_engine import OCREngine

        ocr = OCREngine()
        print(f"  OCR available: {ocr.is_available}")

        if not ocr.is_available:
            print("  OCR not available - install winocr: pip install winocr")
            return False

        # Test OCR on screen capture
        from src.capture.screen_capture import ScreenCapture

        with closing(ScreenCapture()) as capture:
            img = capture.capture_money_display()
            if img is None:
                print("  Failed to capture money region")
                return False
            print("  Running OCR on money region...")
            result = ocr.recognize_preprocessed(img, invert=True, scale=2.0)
            if _native_ocr_failed(result):
                print("  Native OCR failed")
                return False
            print(f"  OCR text: '{result.text}'")
            confidence = "unknown" if result.confidence is None else f"{result.confidence:.2f}"
            print(f"  Confidence: {confidence}")

        print("  OCR test PASSED")
        return True

    except Exception as e:
        print(f"  OCR test FAILED: {e}")
        return False


def test_money_parser():
    """Test money parsing."""
    print("\nTesting money parser...")

    try:
        from src.detection.parsers.money_parser import MoneyParser

        parser = MoneyParser()

        # Test cases
        test_cases = [
            ("$1,234,567", 1234567),
            ("$50,000", 50000),
            ("CASH $100,000 | BANK $500,000", 600000),
            ("$ 2,500,000", 2500000),
            ("$10.000.000", 10000000),  # EU format
        ]

        all_passed = True
        for text, expected in test_cases:
            result = parser.parse(text)
            actual = result.display_value
            status = "OK" if actual == expected else "FAIL"
            print(f"  '{text}' -> ${actual:,} (expected ${expected:,}) [{status}]")
            if actual != expected:
                all_passed = False

        if all_passed:
            print("  Money parser test PASSED")
        else:
            print("  Money parser test FAILED")

        return all_passed

    except Exception as e:
        print(f"  Money parser test FAILED: {e}")
        return False


def test_full_pipeline():
    """Test the full capture -> OCR -> parse pipeline."""
    print("\nTesting full pipeline...")

    try:
        from src.capture.screen_capture import ScreenCapture
        from src.detection.ocr_engine import OCREngine
        from src.detection.parsers.money_parser import MoneyParser

        with closing(ScreenCapture()) as capture:
            ocr = OCREngine()
            parser = MoneyParser()

            if not ocr.is_available:
                print("  Full pipeline test incomplete - OCR not available")
                return False

            required_cycles = 5
            completed = 0
            capture_failures = 0
            ocr_failures = 0
            print(f"  Running {required_cycles} capture cycles...")

            for i in range(required_cycles):
                start = time.perf_counter()

                # Capture
                img = capture.capture_money_display()
                capture_time = (time.perf_counter() - start) * 1000

                if img is None:
                    capture_failures += 1
                    print(f"  Cycle {i+1}: Capture failed")
                    continue

                # OCR
                ocr_start = time.perf_counter()
                ocr_result = ocr.recognize_preprocessed(img, invert=True, scale=2.0)
                ocr_time = (time.perf_counter() - ocr_start) * 1000
                if _native_ocr_failed(ocr_result):
                    ocr_failures += 1
                    print(f"  Cycle {i+1}: Native OCR failed")
                    continue

                # An empty successful OCR reading can legitimately contain no money.
                money = parser.parse(ocr_result.text)

                total_time = (time.perf_counter() - start) * 1000

                if money.has_value:
                    print(
                        f"  Cycle {i+1}: ${money.display_value:,} "
                        f"(capture: {capture_time:.1f}ms, ocr: {ocr_time:.1f}ms, "
                        f"total: {total_time:.1f}ms)"
                    )
                else:
                    print(
                        f"  Cycle {i+1}: No money detected - raw: '{ocr_result.text[:50]}...' "
                        f"(total: {total_time:.1f}ms)"
                    )

                completed += 1
                time.sleep(0.5)  # Brief pause between captures

        print(
            f"  Completed {completed}/{required_cycles} cycles "
            f"(capture failures: {capture_failures}, OCR failures: {ocr_failures})"
        )
        passed = completed == required_cycles
        print("  Full pipeline test " + ("PASSED" if passed else "FAILED"))
        return passed

    except Exception as e:
        print(f"  Full pipeline test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def _run_image_diagnostic(path, backend):
    """Keep image mode isolated from live checks and stdout machine-readable."""
    try:
        # Imports and dependencies may print. Image mode must not initialize
        # application logging/settings, capture a desktop, or write output files.
        with redirect_stdout(sys.stderr):
            from src.detection.screenshot_diagnostic import diagnose_image

            report = diagnose_image(path, backend=backend)
            output = json.dumps(report, allow_nan=False)
    except Exception as error:
        report = {
            "schema_version": 1,
            "mode": "single_image_diagnostic",
            "status": "error",
            "backend": {"name": backend},
            "error": {"type": type(error).__name__, "message": str(error)},
        }
        print(json.dumps(report, allow_nan=False))
        return 1
    print(output)
    return 0


def main(argv=None):
    """Dispatch one local image diagnostic or run all four live checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", metavar="PATH", help="diagnose one local PNG or JPEG")
    parser.add_argument(
        "--backend",
        choices=("windows", "tesseract"),
        help="image OCR backend (default: windows; tesseract is diagnostic-only)",
    )
    args = parser.parse_args(argv)
    if args.image is not None:
        return _run_image_diagnostic(args.image, args.backend or "windows")
    if args.backend is not None:
        parser.error("--backend requires --image")

    print("=" * 50)
    print("GTA Business Manager - Pipeline Test")
    print("=" * 50)

    results = {
        "Screen Capture": test_screen_capture(),
        "OCR": test_ocr(),
        "Money Parser": test_money_parser(),
        "Full Pipeline": test_full_pipeline(),
    }

    print("\n" + "=" * 50)
    print("Test Results:")
    print("=" * 50)

    for name, passed in results.items():
        status = "PASSED" if passed else "FAILED"
        print(f"  {name}: {status}")

    all_passed = all(results.values())
    print("\n" + ("All tests passed!" if all_passed else "Some tests failed."))

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
