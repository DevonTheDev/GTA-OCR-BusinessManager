"""Detection and recognition module for GTA Business Manager."""

# Lazy imports to avoid requiring cv2 for basic functionality
# These are imported on demand to support testing without opencv installed


def __getattr__(name):
    """Lazy import for detection components."""
    if name == "OCREngine":
        from .ocr_engine import OCREngine
        return OCREngine
    elif name == "TemplateMatcher":
        from .template_matcher import TemplateMatcher
        return TemplateMatcher
    elif name == "StateDetector":
        from .state_detector import StateDetector
        return StateDetector
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["OCREngine", "TemplateMatcher", "StateDetector"]
