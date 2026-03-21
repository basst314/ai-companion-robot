"""Text-to-speech service interface and mock implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from shared.models import SpeechOutput


class TtsService(Protocol):
    """Interface for robot speech playback."""

    async def speak(self, text: str) -> SpeechOutput:
        """Speak or acknowledge a text response."""


@dataclass(slots=True)
class MockTtsService:
    """Mock TTS that records the spoken text and acknowledges playback."""

    spoken_texts: list[str] = field(default_factory=list)
    should_fail: bool = False

    async def speak(self, text: str) -> SpeechOutput:
        if self.should_fail:
            raise RuntimeError("mock tts failure")

        self.spoken_texts.append(text)
        print(f"[TTS] {text}")
        return SpeechOutput(text=text, acknowledged=True, duration_ms=len(text) * 10)
