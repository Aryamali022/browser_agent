"""Vision locator: finds a UI element's coordinates on a screenshot.

Used as the fallback path when a DOM action fails — the agent screenshots
the tab, asks the vision model where the target element is, and retries the
action at those coordinates.

This client covers hosted VLMs behind OpenAI-compatible APIs (NVIDIA NIM,
OpenAI). The top-level vision/ package holds the interfaces for future
local models (Qwen-VL, InternVL, SmolVLM) — see vision/base.py.
"""

import json
import logging
from typing import Optional, Tuple

from core.config import settings
from services.llm import extract_json

logger = logging.getLogger("vision")

LOCATE_SYSTEM = """You locate UI elements in browser screenshots.
Given a screenshot and a description of one element, find that element.
Use a coordinate grid normalized to 0-1000 on both axes: (0,0) is the top-left corner of the screenshot, (1000,1000) the bottom-right.
Respond with ONE JSON object only, no other text:
{"found": true/false, "x": <0-1000 x of the element's center>, "y": <0-1000 y of the element's center>}
If the element is not visible in the screenshot, respond {"found": false, "x": 0, "y": 0}."""


def _coord(value) -> float:
    """Coerce a coordinate that may come back as a number or a [min, max]
    box edge pair (VLMs often answer in bounding-box style)."""
    if isinstance(value, (list, tuple)) and value:
        numbers = [float(v) for v in value]
        return sum(numbers) / len(numbers)
    return float(value)


class VisionLocator:
    def __init__(self, base_url: str, api_key: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def locate(self, image_data_url: str, width: int, height: int,
               description: str) -> Optional[Tuple[float, float]]:
        """Return the element's center as viewport fractions (0..1), or None."""
        prompt = (f"This is a screenshot of a web page.\n"
                  f"Find this element: {description}\n"
                  "Return the 0-1000 normalized coordinates of its center as the JSON object.")
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": LOCATE_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ]},
            ],
            temperature=0.0,
            max_tokens=1024,
        )
        raw = completion.choices[0].message.content or ""
        verdict = json.loads(extract_json(raw))
        if not verdict.get("found"):
            return None
        x, y = _coord(verdict["x"]), _coord(verdict["y"])
        # Primary convention: 0-1000 normalized grid (asked for in the prompt,
        # and Qwen-VL's native format). Fallback: raw pixel coordinates.
        if 0 <= x <= 1000 and 0 <= y <= 1000:
            return x / 1000.0, y / 1000.0
        if 0 <= x <= width and 0 <= y <= height:
            return x / width, y / height
        logger.warning("Vision model returned out-of-bounds point (%s, %s)", x, y)
        return None


def create_vision() -> Optional[VisionLocator]:
    """Build the configured vision locator, or None if vision is unavailable."""
    if not settings.VISION_ENABLED:
        return None
    try:
        provider = settings.VISION_PROVIDER.lower()
        if provider == "nvidia" and settings.NVIDIA_API_KEY:
            return VisionLocator("https://integrate.api.nvidia.com/v1",
                                 settings.NVIDIA_API_KEY, settings.VISION_MODEL)
        if provider == "openai" and settings.OPENAI_API_KEY:
            return VisionLocator("https://api.openai.com/v1",
                                 settings.OPENAI_API_KEY, settings.VISION_MODEL)
    except Exception:
        logger.exception("Vision locator could not be created; continuing without vision")
    return None
