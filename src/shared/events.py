"""Event types shared across the system."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from shared.models import ComponentName


class EventName(StrEnum):
    """Core event names implied by the architecture docs."""

    SPEECH_DETECTED = "speech_detected"
    TRANSCRIPT_READY = "transcript_ready"
    FACE_DETECTED = "face_detected"
    RESPONSE_READY = "response_ready"
    TTS_STARTED = "tts_started"
    TTS_FINISHED = "tts_finished"
    AUDIO_FINISHED = "audio_finished"


@dataclass(slots=True, frozen=True)
class Event:
    """Base event payload container for event-driven coordination."""

    name: EventName
    source: ComponentName
    payload: Mapping[str, object] = field(default_factory=dict)

