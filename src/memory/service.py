"""Memory service interface and in-memory mock implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from shared.models import InteractionRecord, UserIdentity


class MemoryService(Protocol):
    """Interface for memory and user context lookup."""

    async def get_active_user(self) -> UserIdentity | None:
        """Return the currently active user if known."""

    async def get_recent_history(self, limit: int = 5) -> tuple[InteractionRecord, ...]:
        """Return recent interactions."""

    async def save_interaction(self, record: InteractionRecord) -> None:
        """Persist an interaction record."""

    async def get_user_summary(self, user_id: str | None) -> str:
        """Return a short user summary for local queries."""


@dataclass(slots=True)
class InMemoryMemoryService:
    """In-memory memory store suitable for mock orchestration."""

    active_user: UserIdentity | None = None
    records: list[InteractionRecord] = field(default_factory=list)

    async def get_active_user(self) -> UserIdentity | None:
        return self.active_user

    async def get_recent_history(self, limit: int = 5) -> tuple[InteractionRecord, ...]:
        return tuple(self.records[-limit:])

    async def save_interaction(self, record: InteractionRecord) -> None:
        self.records.append(record)

    async def get_user_summary(self, user_id: str | None) -> str:
        if not self.active_user or self.active_user.user_id != user_id:
            return "I do not know much about you yet."

        return self.active_user.summary or f"I know you as {self.active_user.display_name or user_id}."
