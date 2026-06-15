"""Vision module interfaces (Phase 3 — future ready).

The agent core never imports a concrete vision model; it depends only on
these interfaces, so Qwen2.5-VL, InternVL3 or SmolVLM2 can be plugged in
later without touching the planner or executor.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class BoundingBox:
    x: int
    y: int
    width: int
    height: int


@dataclass
class GroundedElement:
    """An element located visually on a screenshot."""
    description: str
    box: BoundingBox
    confidence: float


class BaseVisionModel(ABC):
    @abstractmethod
    def describe_screenshot(self, image_bytes: bytes) -> str:
        """Summarise what is visible in the screenshot."""

    @abstractmethod
    def ground_element(self, image_bytes: bytes, description: str) -> Optional[GroundedElement]:
        """Find the on-screen location of an element described in natural language.

        Used when the DOM snapshot fails (canvas apps, shadow DOM, image buttons).
        """

    @abstractmethod
    def detect_popups(self, image_bytes: bytes) -> List[GroundedElement]:
        """Detect cookie banners, modals and overlays blocking the page."""
