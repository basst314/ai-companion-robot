"""Fast local reactive policies for active turns."""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.state import OrchestratorState
from shared.models import PlanStep, Transcript


@dataclass(slots=True)
class ReactivePolicyEngine:
    """No-op reactive policy while local behavior capabilities are retired."""

    orient_to_user_during_listening: bool = False

    def listening_started(self, state: OrchestratorState, *, has_attention_target: bool) -> tuple[PlanStep, ...]:
        del state, has_attention_target
        return ()

    def partial_transcript(self, state: OrchestratorState, transcript: Transcript) -> tuple[PlanStep, ...]:
        del state, transcript
        return ()

    def processing_started(self, state: OrchestratorState) -> tuple[PlanStep, ...]:
        del state
        return ()
