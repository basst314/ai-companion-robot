"""Cloud AI service interface and deterministic mock implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from shared.models import AiResponse, EmotionState, InteractionContext, Transcript


class CloudAiService(Protocol):
    """Interface for open-ended conversational generation."""

    async def generate_reply(
        self,
        transcript: Transcript,
        context: InteractionContext,
    ) -> AiResponse:
        """Generate a cloud-backed conversational reply."""


@dataclass(slots=True)
class MockCloudAiService:
    """Mock cloud AI that can produce contextual conversational replies."""

    fail_on_text: str | None = None

    async def generate_reply(
        self,
        transcript: Transcript,
        context: InteractionContext,
    ) -> AiResponse:
        if self.fail_on_text and self.fail_on_text in transcript.text.lower():
            raise RuntimeError("mock cloud failure")

        visible_people = ", ".join(detection.label for detection in context.current_detections)
        if not visible_people:
            visible_people = "nobody right now"

        return AiResponse(
            text=(
                f"Cloud reply: you said '{transcript.text}'. "
                f"I currently see {visible_people}."
            ),
            emotion=EmotionState.HAPPY,
            intent="cloud_chat",
        )
