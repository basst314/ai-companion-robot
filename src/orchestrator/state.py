"""State definitions for the orchestrator."""

from dataclasses import dataclass, field
from enum import StrEnum

from shared.models import EmotionState, Language, PlanStepResult, Transcript, TurnPlan, VisionDetection


class LifecycleStage(StrEnum):
    """High-level lifecycle stages for the local orchestrator."""

    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    RESPONDING = "responding"
    SPEAKING = "speaking"
    ERROR = "error"


@dataclass(slots=True)
class OrchestratorState:
    """Mutable state container for orchestrated robot interactions."""

    lifecycle: LifecycleStage = LifecycleStage.IDLE
    active_language: Language = Language.ENGLISH
    emotion: EmotionState = EmotionState.NEUTRAL
    last_event_name: str | None = None
    active_user_id: str | None = None
    current_transcript: Transcript | None = None
    current_response: str | None = None
    last_detections: tuple[VisionDetection, ...] = ()
    last_error: str | None = None
    interaction_id: int = 0
    last_plan: TurnPlan | None = None
    last_step_results: tuple[PlanStepResult, ...] = field(default_factory=tuple)
    eyes_open: bool = False
    head_direction: str = "center"

    @classmethod
    def initial(cls) -> "OrchestratorState":
        """Create the default starting state for the application."""

        return cls()
