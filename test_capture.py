"""Test script to verify the capture-to-OCR pipeline."""

import sys
import time


def test_screen_capture():
    """Test basic screen capture functionality."""
    print("Testing screen capture...")

    try:
        from src.capture.screen_capture import ScreenCapture
        from src.capture.regions import ScreenRegions

        capture = ScreenCapture()
        print(f"  Resolution: {capture.resolution}")
        print(f"  Scale factor: {capture.scale_factor}")

        # Test full screen capture
        print("  Capturing full screen...")
        img = capture.capture_full_screen()
        if img is not None:
            print(f"  Success! Shape: {img.shape}")
        else:
            print("  Failed to capture")
            return False

        # Test region capture
        print("  Capturing money region...")
        money_img = capture.capture_money_display()
        if money_img is not None:
            print(f"  Success! Shape: {money_img.shape}")
        else:
            print("  Failed to capture money region")

        capture.close()
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
            return True  # Not a failure, just not available

        # Test OCR on screen capture
        from src.capture.screen_capture import ScreenCapture

        capture = ScreenCapture()
        img = capture.capture_money_display()

        if img is not None:
            print("  Running OCR on money region...")
            result = ocr.recognize_preprocessed(img, invert=True, scale=2.0)
            print(f"  OCR text: '{result.text}'")
            print(f"  Confidence: {result.confidence:.2f}")

        capture.close()
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

        capture = ScreenCapture()
        ocr = OCREngine()
        parser = MoneyParser()

        if not ocr.is_available:
            print("  Skipping - OCR not available")
            return True

        print("  Running 5 capture cycles...")

        for i in range(5):
            start = time.perf_counter()

            # Capture
            img = capture.capture_money_display()
            capture_time = (time.perf_counter() - start) * 1000

            if img is None:
                print(f"  Cycle {i+1}: Capture failed")
                continue

            # OCR
            ocr_start = time.perf_counter()
            ocr_result = ocr.recognize_preprocessed(img, invert=True, scale=2.0)
            ocr_time = (time.perf_counter() - ocr_start) * 1000

            # Parse
            money = parser.parse(ocr_result.text)

            total_time = (time.perf_counter() - start) * 1000

            if money.has_value:
                print(
                    f"  Cycle {i+1}: ${money.display_value:,} "
                    f"(capture: {capture_time:.1f}ms, ocr: {ocr_time:.1f}ms, total: {total_time:.1f}ms)"
                )
            else:
                print(
                    f"  Cycle {i+1}: No money detected - raw: '{ocr_result.text[:50]}...' "
                    f"(total: {total_time:.1f}ms)"
                )

            time.sleep(0.5)  # Brief pause between captures

        capture.close()
        print("  Full pipeline test PASSED")
        return True

    except Exception as e:
        print(f"  Full pipeline test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
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
