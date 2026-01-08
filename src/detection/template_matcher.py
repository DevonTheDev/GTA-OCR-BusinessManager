"""Template matching for UI element detection."""

from typing import Optional, List, Tuple
from pathlib import Path
from dataclasses import dataclass

import cv2
import numpy as np

from ..utils.logging import get_logger


logger = get_logger("detection.template")


@dataclass
class MatchResult:
    """Result of a template match operation."""

    matched: bool
    confidence: float
    location: Tuple[int, int]  # (x, y) of top-left corner
    template_name: str


class TemplateMatcher:
    """Matches UI templates against screen captures."""

    def __init__(self, templates_dir: Optional[Path] = None):
        """Initialize template matcher.

        Args:
            templates_dir: Directory containing template images
        """
        self._templates_dir = templates_dir
        self._templates: dict[str, np.ndarray] = {}
        self._default_threshold = 0.8

    def load_template(self, name: str, filepath: Path) -> bool:
        """Load a template image.

        Args:
            name: Name to reference this template
            filepath: Path to template image

        Returns:
            True if loaded successfully
        """
        try:
            template = cv2.imread(str(filepath), cv2.IMREAD_COLOR)
            if template is not None:
                self._templates[name] = template
                logger.debug(f"Loaded template '{name}' from {filepath}")
                return True
            else:
                logger.warning(f"Failed to load template: {filepath}")
                return False
        except Exception as e:
            logger.error(f"Error loading template {filepath}: {e}")
            return False

    def load_templates_from_dir(self, directory: Path, recursive: bool = False) -> int:
        """Load all templates from a directory.

        Args:
            directory: Directory to scan
            recursive: Whether to scan subdirectories

        Returns:
            Number of templates loaded
        """
        count = 0
        pattern = "**/*.png" if recursive else "*.png"

        for filepath in directory.glob(pattern):
            name = filepath.stem
            if self.load_template(name, filepath):
                count += 1

        logger.info(f"Loaded {count} templates from {directory}")
        return count

    def match(
        self,
        image: np.ndarray,
        template_name: str,
        threshold: Optional[float] = None,
    ) -> MatchResult:
        """Match a template against an image.

        Args:
            image: Image to search in (BGR)
            template_name: Name of template to match
            threshold: Match threshold (0-1). If None, uses default.

        Returns:
            MatchResult with match information
        """
        if template_name not in self._templates:
            return MatchResult(
                matched=False,
                confidence=0.0,
                location=(0, 0),
                template_name=template_name,
            )

        template = self._templates[template_name]
        threshold = threshold or self._default_threshold

        # Perform template matching
        result = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

        matched = max_val >= threshold

        return MatchResult(
            matched=matched,
            confidence=max_val,
            location=max_loc,
            template_name=template_name,
        )

    def match_any(
        self,
        image: np.ndarray,
        template_names: List[str],
        threshold: Optional[float] = None,
    ) -> Optional[MatchResult]:
        """Try to match any of several templates.

        Args:
            image: Image to search in
            template_names: List of template names to try
            threshold: Match threshold

        Returns:
            Best matching result, or None if no match
        """
        best_result: Optional[MatchResult] = None
        best_confidence = 0.0

        for name in template_names:
            result = self.match(image, name, threshold)
            if result.matched and result.confidence > best_confidence:
                best_result = result
                best_confidence = result.confidence

        return best_result

    def match_all(
        self,
        image: np.ndarray,
        template_name: str,
        threshold: Optional[float] = None,
    ) -> List[MatchResult]:
        """Find all occurrences of a template.

        Args:
            image: Image to search in
            template_name: Template to match
            threshold: Match threshold

        Returns:
            List of all matches above threshold
        """
        if template_name not in self._templates:
            return []

        template = self._templates[template_name]
        threshold = threshold or self._default_threshold

        result = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
        locations = np.where(result >= threshold)

        matches = []
        for pt in zip(*locations[::-1]):
            confidence = result[pt[1], pt[0]]
            matches.append(
                MatchResult(
                    matched=True,
                    confidence=confidence,
                    location=pt,
                    template_name=template_name,
                )
            )

        return matches

    def set_default_threshold(self, threshold: float) -> None:
        """Set the default matching threshold.

        Args:
            threshold: New threshold (0-1)
        """
        self._default_threshold = max(0.0, min(1.0, threshold))

    @property
    def loaded_templates(self) -> List[str]:
        """Get list of loaded template names."""
        return list(self._templates.keys())
