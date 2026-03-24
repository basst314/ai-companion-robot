"""Turn planning for the AI companion robot."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from shared.models import CapabilityDefinition, InteractionContext, PlanStep, RouteKind, Transcript, TurnPlan


class CloudPlanningService(Protocol):
    """Interface for cloud-assisted turn planning."""

    async def plan_turn(
        self,
        transcript: Transcript,
        context: InteractionContext,
        capabilities: tuple[CapabilityDefinition, ...],
    ) -> TurnPlan:
        """Return a structured multi-step plan for the current utterance."""


class TurnPlanner(Protocol):
    """Interface for selecting a turn plan from transcript and context."""

    async def plan(
        self,
        transcript: Transcript,
        context: InteractionContext,
        capabilities: tuple[CapabilityDefinition, ...],
    ) -> TurnPlan:
        """Choose how the orchestrator should execute the turn."""


@dataclass(slots=True)
class LocalShortcutPlanner:
    """Temporary deterministic shortcuts for obvious safe commands."""

    async def plan(
        self,
        transcript: Transcript,
        context: InteractionContext,
        capabilities: tuple[CapabilityDefinition, ...],
    ) -> TurnPlan | None:
        del context, capabilities
        text = transcript.text.lower()

        if _contains_any(text, "look at me") and " and " not in text:
            return TurnPlan(
                route_kind=RouteKind.LOCAL_ACTION,
                confidence=0.98,
                source="local_shortcut",
                rationale="matched direct attention command",
                steps=(PlanStep(capability_id="look_at_user", reason="user asked to be looked at"),),
            )

        if _contains_any(text, "turn your head left", "turn left") and " and " not in text:
            return TurnPlan(
                route_kind=RouteKind.LOCAL_ACTION,
                confidence=0.98,
                source="local_shortcut",
                rationale="matched direct head turn command",
                steps=(
                    PlanStep(
                        capability_id="turn_head",
                        arguments={"direction": "left"},
                        reason="user requested a left head turn",
                    ),
                ),
            )

        if _contains_any(text, "turn your head right", "turn right") and " and " not in text:
            return TurnPlan(
                route_kind=RouteKind.LOCAL_ACTION,
                confidence=0.98,
                source="local_shortcut",
                rationale="matched direct head turn command",
                steps=(
                    PlanStep(
                        capability_id="turn_head",
                        arguments={"direction": "right"},
                        reason="user requested a right head turn",
                    ),
                ),
            )

        if _contains_any(text, "turn your head", "look forward", "center your head") and " and " not in text:
            return TurnPlan(
                route_kind=RouteKind.LOCAL_ACTION,
                confidence=0.95,
                source="local_shortcut",
                rationale="matched direct head-centering command",
                steps=(
                    PlanStep(
                        capability_id="turn_head",
                        arguments={"direction": "center"},
                        reason="center the head position",
                    ),
                ),
            )

        if _contains_any(text, "can you see me", "who do you see", "what do you see") and " and " not in text:
            return TurnPlan(
                route_kind=RouteKind.LOCAL_QUERY,
                confidence=0.98,
                source="local_shortcut",
                rationale="matched direct vision question",
                steps=(PlanStep(capability_id="visible_people", reason="answer from current detections"),),
            )

        if _contains_any(text, "what do you know about me") and " and " not in text:
            return TurnPlan(
                route_kind=RouteKind.LOCAL_QUERY,
                confidence=0.98,
                source="local_shortcut",
                rationale="matched direct memory question",
                steps=(PlanStep(capability_id="user_summary", reason="answer from user memory"),),
            )

        if _contains_any(text, "what state are you in", "what is your status") and " and " not in text:
            return TurnPlan(
                route_kind=RouteKind.LOCAL_QUERY,
                confidence=0.95,
                source="local_shortcut",
                rationale="matched direct robot-status question",
                steps=(PlanStep(capability_id="robot_status", reason="answer from orchestrator state"),),
            )

        return None


@dataclass(slots=True)
class HybridTurnPlanner:
    """Two-stage turn planner with local shortcuts and cloud fallback."""

    cloud_planner: CloudPlanningService
    local_shortcuts: LocalShortcutPlanner = field(default_factory=LocalShortcutPlanner)

    async def plan(
        self,
        transcript: Transcript,
        context: InteractionContext,
        capabilities: tuple[CapabilityDefinition, ...],
    ) -> TurnPlan:
        shortcut_plan = await self.local_shortcuts.plan(transcript, context, capabilities)
        if shortcut_plan is not None:
            return shortcut_plan
        return await self.cloud_planner.plan_turn(transcript, context, capabilities)


def _contains_any(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)
