"""Core typed models shared across modules."""

from dataclasses import dataclass
from enum import StrEnum


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


class EmotionState(StrEnum):
    """Minimal emotional state labels for future UI and response hints."""

    NEUTRAL = "neutral"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


@dataclass(slots=True, frozen=True)
class UserIdentity:
    """Basic user identity placeholder for future memory integration."""

    user_id: str
    display_name: str | None = None
    preferred_language: Language | None = None

