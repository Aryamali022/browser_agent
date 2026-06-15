"""Voice module interfaces (Phase 4 — future ready).

Pipeline: microphone -> wake word -> speech-to-text -> planner ->
tool execution -> text-to-speech.

The side panel already has a voice input button (browser SpeechRecognition);
this module is for the full offline/continuous experience.
"""

from abc import ABC, abstractmethod
from typing import Callable


class BaseSpeechToText(ABC):
    @abstractmethod
    def transcribe(self, audio_bytes: bytes) -> str: ...


class BaseTextToSpeech(ABC):
    @abstractmethod
    def synthesize(self, text: str) -> bytes:
        """Return audio bytes (wav/mp3) for the given text."""


class BaseWakeWordDetector(ABC):
    """Listens continuously and fires the callback on the wake word ("Hey Agent")."""

    @abstractmethod
    def start(self, on_wake: Callable[[], None]) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...
