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
    """Robot status names shared by the realtime runtime and UI."""

    IDLE = "idle"
    LISTENING = "listening"
    SPEAKING = "speaking"


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
