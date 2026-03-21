"""State definitions for the orchestrator."""

from dataclasses import dataclass
from enum import StrEnum

from shared.events import Event
from shared.models import EmotionState, Language


class LifecycleStage(StrEnum):
    """High-level lifecycle stages for the local orchestrator."""

    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    RESPONDING = "responding"


@dataclass(slots=True)
class OrchestratorState:
    """Minimal state container for future orchestration logic."""

    lifecycle: LifecycleStage = LifecycleStage.IDLE
    active_language: Language = Language.ENGLISH
    emotion: EmotionState = EmotionState.NEUTRAL
    last_event: Event | None = None
    active_user_id: str | None = None

    @classmethod
    def initial(cls) -> "OrchestratorState":
        """Create the default starting state for the application."""

        return cls()

