"""Local-first turn routing for the AI companion robot."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from shared.models import CapabilityDefinition, InteractionContext, PlanStep, RouteKind, Transcript, TurnPlan


class TurnDirector(Protocol):
    """Interface for selecting a local-first route for the current turn."""

    async def direct_turn(
        self,
        transcript: Transcript,
        context: InteractionContext,
        capabilities: tuple[CapabilityDefinition, ...],
    ) -> TurnPlan:
        """Return the local route record for the current turn."""


@dataclass(slots=True)
class LocalShortcutPlanner:
    """No-op planner while local shortcut capabilities are retired."""

    async def plan(
        self,
        transcript: Transcript,
        context: InteractionContext,
        capabilities: tuple[CapabilityDefinition, ...],
    ) -> TurnPlan | None:
        del transcript, context, capabilities
        return None


@dataclass(slots=True)
class LocalTurnDirector:
    """Local-first router that falls back to a single cloud reply."""

    local_shortcuts: LocalShortcutPlanner = field(default_factory=LocalShortcutPlanner)

    async def direct_turn(
        self,
        transcript: Transcript,
        context: InteractionContext,
        capabilities: tuple[CapabilityDefinition, ...],
    ) -> TurnPlan:
        shortcut_plan = await self.local_shortcuts.plan(transcript, context, capabilities)
        if shortcut_plan is not None:
            return shortcut_plan
        return TurnPlan(
            route_kind=RouteKind.CLOUD_CHAT,
            confidence=0.55,
            source="local_turn_director",
            rationale="defaulted to a single cloud reply",
            steps=(PlanStep(capability_id="cloud_reply", reason="generate the spoken reply"),),
        )
