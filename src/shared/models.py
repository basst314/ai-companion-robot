"""Core typed models shared across modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Language(StrEnum):
    """Languages called out in the architecture notes."""

    ENGLISH = "en"
    GERMAN = "de"
    INDONESIAN = "id"


class ComponentName(StrEnum):
    """Named subsystems used across the local runtime."""

    ORCHESTRATOR = "orchestrator"
    STT = "stt"
    TTS = "tts"
    VISION = "vision"
    UI = "ui"
    MEMORY = "memory"
    HARDWARE = "hardware"
    AUDIO = "audio"
    CLOUD = "cloud"
    LOCAL_AI = "local_ai"


class EmotionState(StrEnum):
    """Minimal emotional state labels for future UI and response hints."""

    NEUTRAL = "neutral"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    CURIOUS = "curious"
    HAPPY = "happy"


class RouteKind(StrEnum):
    """High-level route summaries retained for memory and telemetry."""

    LOCAL_ACTION = "local_action"
    LOCAL_QUERY = "local_query"
    LOCAL_LLM = "local_llm"
    CLOUD_CHAT = "cloud_chat"
    HYBRID = "hybrid"


class CapabilityKind(StrEnum):
    """Kinds of local/cloud capabilities that a turn plan may reference."""

    ACTION = "action"
    QUERY = "query"
    RESPONSE = "response"


class StepPhase(StrEnum):
    """Execution phases for a planned turn."""

    REACTIVE = "reactive"
    IMMEDIATE = "immediate"
    QUERY = "query"
    REPLY = "reply"
    SPEAK = "speak"
    CLEANUP = "cleanup"


@dataclass(slots=True, frozen=True)
class UserIdentity:
    """Basic user identity placeholder for future memory integration."""

    user_id: str
    display_name: str | None = None
    preferred_language: Language | None = None
    summary: str | None = None


@dataclass(slots=True, frozen=True)
class Transcript:
    """Structured transcription result supporting partial and final updates."""

    text: str
    language: Language
    confidence: float
    is_final: bool
    started_at: datetime | None = None
    ended_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class CapabilityDefinition:
    """Registered capability exposed to routing and execution."""

    capability_id: str
    description: str
    kind: CapabilityKind
    target: ComponentName
    phase: StepPhase
    argument_schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    requires_components: tuple[ComponentName, ...] = ()
    allow_parallel: bool = False
    safe_by_default: bool = True


@dataclass(slots=True, frozen=True)
class PlanStep:
    """One executable step within a turn plan."""

    capability_id: str
    arguments: dict[str, Any] = field(default_factory=dict)
    phase: StepPhase | None = None
    reason: str | None = None


@dataclass(slots=True, frozen=True)
class PlanStepResult:
    """Outcome of executing or skipping a single plan step."""

    capability_id: str
    success: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    state_changes: dict[str, Any] = field(default_factory=dict)
    skipped: bool = False


@dataclass(slots=True, frozen=True)
class TurnPlan:
    """Structured multi-step plan produced before a turn is executed."""

    route_kind: RouteKind
    confidence: float
    steps: tuple[PlanStep, ...]
    rationale: str | None = None
    source: str = "turn_director"


@dataclass(slots=True, frozen=True)
class ActionRequest:
    """Typed request for local hardware or UI actions."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ActionResult:
    """Structured result from an action handler."""

    success: bool
    message: str
    state_changes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class QueryResult:
    """Structured result from a local state, memory, or vision query."""

    answer_text: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class AiResponse:
    """Structured AI response used by local and cloud generators."""

    text: str
    emotion: EmotionState = EmotionState.NEUTRAL
    intent: str | None = None
    should_speak: bool = True
    display_text: str | None = None


@dataclass(slots=True, frozen=True)
class SpeechOutput:
    """Acknowledgement from TTS playback."""

    text: str
    acknowledged: bool
    duration_ms: int | None = None


@dataclass(slots=True, frozen=True)
class VisionDetection:
    """Minimal representation of a perceived person or object."""

    label: str
    confidence: float
    user_id: str | None = None


@dataclass(slots=True, frozen=True)
class VisionSnapshot:
    """Minimal camera snapshot payload used for cloud vision follow-ups."""

    image_url: str
    mime_type: str
    summary: str | None = None


@dataclass(slots=True, frozen=True)
class InteractionRecord:
    """Persisted interaction data for memory and debugging."""

    user_text: str
    assistant_text: str
    language: Language
    timestamp: datetime
    route_kind: RouteKind
    user_id: str | None = None
    plan_summary: str | None = None
    executed_steps: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class RobotStateSnapshot:
    """Small, serializable view of the robot state for routing and responses."""

    lifecycle: str
    emotion: EmotionState
    eyes_open: bool
    head_direction: str


@dataclass(slots=True, frozen=True)
class InteractionContext:
    """Context assembled by the orchestrator before executing a route."""

    active_user: UserIdentity | None
    recent_history: tuple[InteractionRecord, ...]
    current_detections: tuple[VisionDetection, ...]
    robot_state: RobotStateSnapshot
