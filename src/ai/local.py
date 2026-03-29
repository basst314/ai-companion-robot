"""Local AI service interface and deterministic mock implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from shared.models import AiResponse, EmotionState, InteractionContext, Transcript


class LocalAiService(Protocol):
    """Interface for lightweight local AI behavior."""

    async def generate_reply(
        self,
        transcript: Transcript,
        context: InteractionContext,
    ) -> AiResponse:
        """Generate a local response without relying on cloud services."""


@dataclass(slots=True)
class MockLocalAiService:
    """Small deterministic responder for constrained local reasoning."""

    async def generate_reply(
        self,
        transcript: Transcript,
        context: InteractionContext,
    ) -> AiResponse:
        active_user = context.active_user.display_name if context.active_user else "friend"
        return AiResponse(
            text=(
                f"Local brain online. I heard '{transcript.text}' and I know you as {active_user}."
            ),
            language=transcript.language,
            emotion=EmotionState.CURIOUS,
            intent="local_reasoning",
        )
