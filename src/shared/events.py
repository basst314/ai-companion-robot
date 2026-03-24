"""Event types shared across the system."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Awaitable, Callable, Mapping

from shared.models import ComponentName

logger = logging.getLogger(__name__)

EventHandler = Callable[["Event"], Awaitable[None]]


class EventName(StrEnum):
    """Core event names implied by the architecture docs."""

    SPEECH_DETECTED = "speech_detected"
    LISTENING_STARTED = "listening_started"
    TRANSCRIPT_PARTIAL = "transcript_partial"
    TRANSCRIPT_FINAL = "transcript_final"
    FACE_DETECTED = "face_detected"
    ROUTE_SELECTED = "route_selected"
    PLAN_CREATED = "plan_created"
    PLAN_VALIDATED = "plan_validated"
    STEP_STARTED = "step_started"
    STEP_FINISHED = "step_finished"
    STEP_SKIPPED = "step_skipped"
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


@dataclass(slots=True)
class EventBus:
    """Minimal in-process pub/sub used by the orchestrator and tests."""

    _specific_subscribers: dict[EventName, list[EventHandler]] = field(default_factory=dict)
    _global_subscribers: list[EventHandler] = field(default_factory=list)

    def subscribe(self, handler: EventHandler, *, event_name: EventName | None = None) -> None:
        if event_name is None:
            self._global_subscribers.append(handler)
            return
        self._specific_subscribers.setdefault(event_name, []).append(handler)

    async def publish(self, event: Event) -> None:
        handlers = list(self._global_subscribers)
        handlers.extend(self._specific_subscribers.get(event.name, ()))
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception("event subscriber failed for %s", event.name.value)
