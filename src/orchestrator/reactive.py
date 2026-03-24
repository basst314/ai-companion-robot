"""Fast local reactive policies for active turns."""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.state import OrchestratorState
from shared.models import EmotionState, PlanStep, StepPhase, Transcript


@dataclass(slots=True)
class ReactivePolicyEngine:
    """Small local policy layer for bounded nonverbal turn-time behavior."""

    orient_to_user_during_listening: bool = True

    def listening_started(self, state: OrchestratorState, *, has_attention_target: bool) -> tuple[PlanStep, ...]:
        steps = [
            PlanStep(
                capability_id="set_emotion",
                arguments={"emotion": EmotionState.LISTENING.value},
                phase=StepPhase.REACTIVE,
                reason="show listening attention",
            )
        ]
        if self.orient_to_user_during_listening and has_attention_target and state.head_direction != "user":
            steps.append(
                PlanStep(
                    capability_id="look_at_user",
                    phase=StepPhase.REACTIVE,
                    reason="orient toward the speaker",
                )
            )
        return tuple(steps)

    def partial_transcript(self, state: OrchestratorState, transcript: Transcript) -> tuple[PlanStep, ...]:
        if not transcript.text.strip() or state.emotion is EmotionState.CURIOUS:
            return ()
        return (
            PlanStep(
                capability_id="set_emotion",
                arguments={"emotion": EmotionState.CURIOUS.value},
                phase=StepPhase.REACTIVE,
                reason="show active interpretation while speech is arriving",
            ),
        )

    def processing_started(self, state: OrchestratorState) -> tuple[PlanStep, ...]:
        if state.emotion is EmotionState.CURIOUS:
            return ()
        return (
            PlanStep(
                capability_id="set_emotion",
                arguments={"emotion": EmotionState.CURIOUS.value},
                phase=StepPhase.REACTIVE,
                reason="show a thinking/curious response cue",
            ),
        )
