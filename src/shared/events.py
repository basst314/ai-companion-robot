"""Event types shared across the system."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from shared.models import ComponentName


class EventName(StrEnum):
    """Core event names implied by the architecture docs."""

    SPEECH_DETECTED = "speech_detected"
    LISTENING_STARTED = "listening_started"
    TRANSCRIPT_PARTIAL = "transcript_partial"
    TRANSCRIPT_FINAL = "transcript_final"
    FACE_DETECTED = "face_detected"
    ROUTE_SELECTED = "route_selected"
    RESPONSE_READY = "response_ready"
    ACTION_EXECUTED = "action_executed"
    QUERY_EXECUTED = "query_executed"
    TTS_STARTED = "tts_started"
    TTS_FINISHED = "tts_finished"
    AUDIO_FINISHED = "audio_finished"
    ERROR_OCCURRED = "error_occurred"


@dataclass(slots=True, frozen=True)
class Event:
    """Base event payload container for event-driven coordination."""

    name: EventName
    source: ComponentName
    payload: Mapping[str, object] = field(default_factory=dict)
