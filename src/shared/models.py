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
    """High-level route choices available to the orchestrator."""

    LOCAL_ACTION = "local_action"
    LOCAL_QUERY = "local_query"
    LOCAL_LLM = "local_llm"
    CLOUD_CHAT = "cloud_chat"


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
class RouteDecision:
    """Result of routing a transcript to the next execution path."""

    kind: RouteKind
    confidence: float
    action_name: str | None = None
    query_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    rationale: str | None = None


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
class InteractionRecord:
    """Persisted interaction data for memory and debugging."""

    user_text: str
    assistant_text: str
    language: Language
    timestamp: datetime
    route_kind: RouteKind
    user_id: str | None = None


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
