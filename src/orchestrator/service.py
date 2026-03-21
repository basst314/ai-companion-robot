"""Orchestrator service scaffold."""

from dataclasses import dataclass

from orchestrator.state import OrchestratorState
from shared.config import AppConfig
from shared.events import Event


@dataclass(slots=True)
class OrchestratorService:
    """Central coordinator placeholder for future interaction flow."""

    config: AppConfig
    state: OrchestratorState

    async def start(self) -> None:
        """Prepare future startup wiring for the local runtime."""

    async def stop(self) -> None:
        """Prepare future shutdown wiring for the local runtime."""

    async def handle_event(self, event: Event) -> None:
        """Accept an event for future routing and state transitions."""

