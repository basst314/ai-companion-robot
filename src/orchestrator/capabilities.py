"""Capability registry and plan validation helpers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from shared.models import (
    CapabilityDefinition,
    CapabilityKind,
    ComponentName,
    EmotionState,
    PlanStep,
    PlanStepResult,
    StepPhase,
    TurnPlan,
)


@dataclass(slots=True)
class CapabilityRegistry:
    """Registry of planner-visible capabilities and local validation rules."""

    definitions: dict[str, CapabilityDefinition]

    def get(self, capability_id: str) -> CapabilityDefinition | None:
        return self.definitions.get(capability_id)

    def list_available(self, available_components: set[ComponentName]) -> tuple[CapabilityDefinition, ...]:
        available: list[CapabilityDefinition] = []
        for definition in self.definitions.values():
            if all(component in available_components for component in definition.requires_components):
                available.append(definition)
        return tuple(available)

    def validate_plan(
        self,
        plan: TurnPlan,
        *,
        available_components: set[ComponentName],
    ) -> tuple[TurnPlan, tuple[PlanStepResult, ...]]:
        """Normalize step arguments and skip any step that is unknown or unavailable."""

        normalized_steps: list[PlanStep] = []
        skipped_results: list[PlanStepResult] = []

        for step in plan.steps:
            definition = self.get(step.capability_id)
            if definition is None:
                skipped_results.append(
                    PlanStepResult(
                        capability_id=step.capability_id,
                        success=False,
                        message=f"Unknown capability '{step.capability_id}'.",
                        skipped=True,
                    )
                )
                continue

            if not all(component in available_components for component in definition.requires_components):
                skipped_results.append(
                    PlanStepResult(
                        capability_id=step.capability_id,
                        success=False,
                        message=f"Capability '{step.capability_id}' is currently unavailable.",
                        skipped=True,
                    )
                )
                continue

            arguments, error = _normalize_arguments(step.arguments, definition)
            if error is not None:
                skipped_results.append(
                    PlanStepResult(
                        capability_id=step.capability_id,
                        success=False,
                        message=error,
                        skipped=True,
                    )
                )
                continue

            normalized_steps.append(
                replace(
                    step,
                    arguments=arguments,
                    phase=step.phase or definition.phase,
                )
            )

        return (
            replace(plan, steps=tuple(normalized_steps)),
            tuple(skipped_results),
        )


def build_default_capability_registry() -> CapabilityRegistry:
    """Return the default capability catalog exposed to the planner."""

    definitions = {
        "look_at_user": CapabilityDefinition(
            capability_id="look_at_user",
            description="Orient the robot head toward the active speaker or user.",
            kind=CapabilityKind.ACTION,
            target=ComponentName.HARDWARE,
            phase=StepPhase.IMMEDIATE,
            requires_components=(ComponentName.HARDWARE,),
        ),
        "turn_head": CapabilityDefinition(
            capability_id="turn_head",
            description="Turn the robot head in a requested direction.",
            kind=CapabilityKind.ACTION,
            target=ComponentName.HARDWARE,
            phase=StepPhase.IMMEDIATE,
            requires_components=(ComponentName.HARDWARE,),
            argument_schema={
                "direction": {
                    "type": "string",
                    "enum": ("left", "right", "center", "user"),
                    "required": True,
                }
            },
        ),
        "set_emotion": CapabilityDefinition(
            capability_id="set_emotion",
            description="Update the robot's visible emotion or attention state.",
            kind=CapabilityKind.ACTION,
            target=ComponentName.UI,
            phase=StepPhase.IMMEDIATE,
            requires_components=(ComponentName.UI,),
            allow_parallel=True,
            argument_schema={
                "emotion": {
                    "type": "string",
                    "enum": tuple(emotion.value for emotion in EmotionState),
                    "required": True,
                }
            },
        ),
        "visible_people": CapabilityDefinition(
            capability_id="visible_people",
            description="Answer what people are currently visible to the robot.",
            kind=CapabilityKind.QUERY,
            target=ComponentName.VISION,
            phase=StepPhase.QUERY,
            requires_components=(ComponentName.VISION,),
        ),
        "user_summary": CapabilityDefinition(
            capability_id="user_summary",
            description="Answer what the robot currently knows about the active user.",
            kind=CapabilityKind.QUERY,
            target=ComponentName.MEMORY,
            phase=StepPhase.QUERY,
            requires_components=(ComponentName.MEMORY,),
        ),
        "robot_status": CapabilityDefinition(
            capability_id="robot_status",
            description="Answer with the robot's current lifecycle and hardware state.",
            kind=CapabilityKind.QUERY,
            target=ComponentName.ORCHESTRATOR,
            phase=StepPhase.QUERY,
        ),
        "cloud_reply": CapabilityDefinition(
            capability_id="cloud_reply",
            description="Generate a conversational reply from the cloud backend using transcript and prior observations.",
            kind=CapabilityKind.RESPONSE,
            target=ComponentName.CLOUD,
            phase=StepPhase.REPLY,
            requires_components=(ComponentName.CLOUD,),
        ),
    }

    return CapabilityRegistry(definitions=definitions)


def _normalize_arguments(
    arguments: dict[str, Any],
    definition: CapabilityDefinition,
) -> tuple[dict[str, Any], str | None]:
    schema = definition.argument_schema
    if not schema:
        if arguments:
            return {}, f"Capability '{definition.capability_id}' does not accept arguments."
        return {}, None

    normalized: dict[str, Any] = {}
    for key in arguments:
        if key not in schema:
            return {}, f"Capability '{definition.capability_id}' does not accept argument '{key}'."

    for key, spec in schema.items():
        value = arguments.get(key)
        if value is None:
            if spec.get("required"):
                return {}, f"Capability '{definition.capability_id}' requires argument '{key}'."
            if "default" in spec:
                normalized[key] = spec["default"]
            continue

        expected_type = spec.get("type")
        if expected_type == "string" and not isinstance(value, str):
            return {}, f"Capability '{definition.capability_id}' argument '{key}' must be a string."
        if expected_type == "boolean" and not isinstance(value, bool):
            return {}, f"Capability '{definition.capability_id}' argument '{key}' must be a boolean."
        if expected_type == "number" and not isinstance(value, (int, float)):
            return {}, f"Capability '{definition.capability_id}' argument '{key}' must be numeric."

        allowed_values = spec.get("enum")
        if allowed_values is not None and value not in allowed_values:
            return {}, f"Capability '{definition.capability_id}' argument '{key}' must be one of {tuple(allowed_values)!r}."

        normalized[key] = value

    return normalized, None
