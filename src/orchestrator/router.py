"""Intent routing for the AI companion robot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from shared.models import InteractionContext, RouteDecision, RouteKind, Transcript


class IntentRouter(Protocol):
    """Interface for selecting the execution path for a user utterance."""

    async def route(self, transcript: Transcript, context: InteractionContext) -> RouteDecision:
        """Choose how the orchestrator should handle a final transcript."""


@dataclass(slots=True)
class RuleBasedIntentRouter:
    """Deterministic first-pass router for local actions, queries, and chat."""

    prefer_local_llm_for_uncertain: bool = False

    async def route(self, transcript: Transcript, context: InteractionContext) -> RouteDecision:
        text = transcript.text.lower()

        if "open your eyes" in text:
            return RouteDecision(
                kind=RouteKind.LOCAL_ACTION,
                confidence=0.99,
                action_name="open_eyes",
                rationale="matched eye-open command",
            )
        if "close your eyes" in text:
            return RouteDecision(
                kind=RouteKind.LOCAL_ACTION,
                confidence=0.99,
                action_name="close_eyes",
                rationale="matched eye-close command",
            )
        if "look at me" in text:
            return RouteDecision(
                kind=RouteKind.LOCAL_ACTION,
                confidence=0.97,
                action_name="look_at_user",
                rationale="matched visual attention command",
            )
        if "turn your head" in text or "turn left" in text or "turn right" in text:
            direction = "left" if "left" in text else "right" if "right" in text else "center"
            return RouteDecision(
                kind=RouteKind.LOCAL_ACTION,
                confidence=0.95,
                action_name="turn_head",
                arguments={"direction": direction},
                rationale="matched head-turn command",
            )
        if "who do you see" in text:
            return RouteDecision(
                kind=RouteKind.LOCAL_QUERY,
                confidence=0.98,
                query_name="visible_people",
                rationale="matched vision query",
            )
        if "what do you know about me" in text:
            return RouteDecision(
                kind=RouteKind.LOCAL_QUERY,
                confidence=0.98,
                query_name="user_summary",
                rationale="matched memory query",
            )
        if "what state are you in" in text:
            return RouteDecision(
                kind=RouteKind.LOCAL_QUERY,
                confidence=0.95,
                query_name="robot_status",
                rationale="matched robot-status query",
            )
        if "use your local brain" in text or "reason locally" in text:
            return RouteDecision(
                kind=RouteKind.LOCAL_LLM,
                confidence=0.85,
                rationale="matched explicit local-reasoning request",
            )

        if self.prefer_local_llm_for_uncertain:
            return RouteDecision(
                kind=RouteKind.LOCAL_LLM,
                confidence=0.55,
                rationale="configured uncertain utterances to local LLM",
            )

        return RouteDecision(
            kind=RouteKind.CLOUD_CHAT,
            confidence=0.7,
            rationale="defaulted to cloud conversation",
        )
