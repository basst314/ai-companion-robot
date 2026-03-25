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
    """High-precision local routing shortcuts for explicit local requests."""

    async def plan(
        self,
        transcript: Transcript,
        context: InteractionContext,
        capabilities: tuple[CapabilityDefinition, ...],
    ) -> TurnPlan | None:
        del context, capabilities
        text = transcript.text.lower()
        wants_cloud_reply = _wants_cloud_reply(text)

        if _contains_any(text, "look at me"):
            return _build_action_plan(
                "look_at_user",
                reason="user asked to be looked at",
                wants_cloud_reply=wants_cloud_reply,
            )

        if _contains_any(text, "turn your head left", "turn left"):
            return _build_action_plan(
                "turn_head",
                arguments={"direction": "left"},
                reason="user requested a left head turn",
                wants_cloud_reply=wants_cloud_reply,
            )

        if _contains_any(text, "turn your head right", "turn right"):
            return _build_action_plan(
                "turn_head",
                arguments={"direction": "right"},
                reason="user requested a right head turn",
                wants_cloud_reply=wants_cloud_reply,
            )

        if _contains_any(text, "turn your head", "look forward", "center your head"):
            return _build_action_plan(
                "turn_head",
                arguments={"direction": "center"},
                reason="center the head position",
                wants_cloud_reply=wants_cloud_reply,
            )

        if _is_local_visible_people_query(text):
            return _build_query_plan(
                "visible_people",
                reason="answer from current detections",
                wants_cloud_reply=wants_cloud_reply,
            )

        if _contains_any(text, "what do you know about me"):
            return _build_query_plan(
                "user_summary",
                reason="answer from user memory",
                wants_cloud_reply=wants_cloud_reply,
            )

        if _contains_any(text, "what state are you in", "what is your status"):
            return _build_query_plan(
                "robot_status",
                reason="answer from orchestrator state",
                wants_cloud_reply=wants_cloud_reply,
            )

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
def _build_action_plan(
    capability_id: str,
    *,
    reason: str,
    arguments: dict[str, object] | None = None,
    wants_cloud_reply: bool,
) -> TurnPlan:
    steps = [PlanStep(capability_id=capability_id, arguments=arguments or {}, reason=reason)]
    route_kind = RouteKind.LOCAL_ACTION
    rationale = reason
    if wants_cloud_reply:
        steps.append(PlanStep(capability_id="cloud_reply", reason="finish the turn with spoken text"))
        route_kind = RouteKind.HYBRID
        rationale = f"{reason}, then generate a spoken reply"
    return TurnPlan(
        route_kind=route_kind,
        confidence=0.98,
        source="local_shortcut",
        rationale=rationale,
        steps=tuple(steps),
    )


def _build_query_plan(
    capability_id: str,
    *,
    reason: str,
    wants_cloud_reply: bool,
) -> TurnPlan:
    steps = [PlanStep(capability_id=capability_id, reason=reason)]
    route_kind = RouteKind.LOCAL_QUERY
    rationale = reason
    if wants_cloud_reply:
        steps.append(PlanStep(capability_id="cloud_reply", reason="finish the turn with spoken text"))
        route_kind = RouteKind.HYBRID
        rationale = f"{reason}, then generate a spoken reply"
    return TurnPlan(
        route_kind=route_kind,
        confidence=0.98,
        source="local_shortcut",
        rationale=rationale,
        steps=tuple(steps),
    )


def _contains_any(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)


def _wants_cloud_reply(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            " and ",
            "tell me",
            "joke",
            "why",
            "how",
            "what do you think",
            "explain",
            "say",
            "chat",
        )
    )


def _is_local_visible_people_query(text: str) -> bool:
    if _contains_any(text, "can you see me", "who do you see"):
        return True
    if "what do you see" not in text:
        return False
    return not _contains_any(text, "here", "this", "in front of you")
