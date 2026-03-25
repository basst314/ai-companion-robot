"""Tests for cloud planning/response logging and parsing."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from ai.cloud import OpenAiCloudPlanningService, OpenAiCloudResponseService, _turn_plan_from_json
from shared.models import (
    CapabilityDefinition,
    CapabilityKind,
    ComponentName,
    EmotionState,
    InteractionContext,
    Language,
    PlanStep,
    PlanStepResult,
    RobotStateSnapshot,
    RouteKind,
    StepPhase,
    Transcript,
    TurnPlan,
    UserIdentity,
    VisionDetection,
)


class FakeStructuredClient:
    async def create_structured_response(  # type: ignore[no-untyped-def]
        self,
        *,
        model,
        instructions,
        input_text,
        schema_name,
        schema,
    ):
        assert model == "gpt-5-mini"
        assert schema_name == "robot_turn_plan"
        assert "planning layer" in instructions
        assert input_text.startswith("Available capabilities:\n- look_at_user")
        assert "\nLanguage: en\nActive user: Basti\nVisible people: Basti\n" in input_text
        assert input_text.rstrip().endswith("User transcript: look at me and say hi")
        assert schema["type"] == "object"
        assert schema["required"] == ["route_kind", "steps"]
        return {
            "route_kind": "hybrid",
            "steps": [
                {
                    "capability_id": "look_at_user",
                    "arguments": {"direction": None, "emotion": None},
                },
                {
                    "capability_id": "cloud_reply",
                    "arguments": {"direction": None, "emotion": None},
                },
            ],
        }


class FakeTextClient:
    async def create_text_response(self, *, model, instructions, input_text):  # type: ignore[no-untyped-def]
        assert model == "gpt-5.2"
        assert "spoken response layer" in instructions
        assert "Executed local step results:" in input_text
        return "I can see you, and I am looking your way."


def _context() -> InteractionContext:
    return InteractionContext(
        active_user=UserIdentity(user_id="u1", display_name="Basti"),
        recent_history=(),
        current_detections=(VisionDetection(label="Basti", confidence=0.98, user_id="u1"),),
        robot_state=RobotStateSnapshot(
            lifecycle="thinking",
            emotion=EmotionState.THINKING,
            eyes_open=True,
            head_direction="center",
        ),
    )


def test_openai_planner_logs_exact_request_and_output(caplog) -> None:
    service = OpenAiCloudPlanningService(client=FakeStructuredClient(), model="gpt-5-mini")
    transcript = Transcript(
        text="look at me and say hi",
        language=Language.ENGLISH,
        confidence=0.99,
        is_final=True,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    capabilities = (
        CapabilityDefinition(
            capability_id="look_at_user",
            description="Turn toward the active user",
            kind=CapabilityKind.ACTION,
            target=ComponentName.HARDWARE,
            phase=StepPhase.IMMEDIATE,
        ),
        CapabilityDefinition(
            capability_id="cloud_reply",
            description="Generate a conversational response",
            kind=CapabilityKind.RESPONSE,
            target=ComponentName.CLOUD,
            phase=StepPhase.REPLY,
        ),
    )

    with caplog.at_level(logging.INFO, logger="ai.cloud"):
        plan = asyncio.run(service.plan_turn(transcript, _context(), capabilities))

    assert plan.route_kind is RouteKind.HYBRID
    assert plan.confidence == 0.6
    assert plan.rationale is None
    assert plan.steps[0].capability_id == "look_at_user"
    assert plan.steps[0].arguments == {}
    assert plan.steps[0].reason is None
    log_text = caplog.text
    assert "[AI] planner request" in log_text
    assert "look at me and say hi" in log_text
    assert "Available capabilities:" in log_text
    assert "[AI] planner output" in log_text
    assert '"capability_id": "look_at_user"' in log_text


def test_openai_reply_logs_exact_request_and_output(caplog) -> None:
    service = OpenAiCloudResponseService(client=FakeTextClient(), model="gpt-5.2")
    transcript = Transcript(
        text="can you see me?",
        language=Language.ENGLISH,
        confidence=0.98,
        is_final=True,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    plan = TurnPlan(
        route_kind=RouteKind.HYBRID,
        confidence=0.9,
        rationale="vision then reply",
        source="openai_planner",
        steps=(
            PlanStep(capability_id="visible_people"),
            PlanStep(capability_id="cloud_reply"),
        ),
    )
    step_results = (
        PlanStepResult(
            capability_id="visible_people",
            success=True,
            message="I can currently see Basti.",
        ),
    )

    with caplog.at_level(logging.INFO, logger="ai.cloud"):
        response = asyncio.run(service.generate_reply(transcript, _context(), plan, step_results))

    assert response.text == "I can see you, and I am looking your way."
    log_text = caplog.text
    assert "[AI] reply request" in log_text
    assert "can you see me?" in log_text
    assert "I can currently see Basti." in log_text
    assert "[AI] reply output" in log_text
    assert "I can see you, and I am looking your way." in log_text


def test_openai_planner_normalizes_cloud_reply_arguments_and_route_kind() -> None:
    payload = {
        "route_kind": "local_action",
        "steps": [
            {
                "capability_id": "cloud_reply",
                "arguments": {
                    "direction": None,
                    "emotion": "curious",
                },
            },
            {
                "capability_id": "set_emotion",
                "arguments": {
                    "direction": None,
                    "emotion": "neutral",
                },
            },
            {
                "capability_id": "look_at_user",
                "arguments": {
                    "direction": None,
                    "emotion": None,
                },
            },
        ],
    }
    capabilities = (
        CapabilityDefinition(
            capability_id="cloud_reply",
            description="Generate a conversational response",
            kind=CapabilityKind.RESPONSE,
            target=ComponentName.CLOUD,
            phase=StepPhase.REPLY,
        ),
        CapabilityDefinition(
            capability_id="set_emotion",
            description="Update emotion",
            kind=CapabilityKind.ACTION,
            target=ComponentName.UI,
            phase=StepPhase.IMMEDIATE,
            argument_schema={
                "emotion": {
                    "type": "string",
                    "enum": tuple(emotion.value for emotion in EmotionState),
                    "required": True,
                }
            },
        ),
        CapabilityDefinition(
            capability_id="look_at_user",
            description="Turn toward the active user",
            kind=CapabilityKind.ACTION,
            target=ComponentName.HARDWARE,
            phase=StepPhase.IMMEDIATE,
        ),
    )

    turn_plan = _turn_plan_from_json(payload, capabilities=capabilities)

    assert turn_plan.route_kind is RouteKind.HYBRID
    assert turn_plan.steps[0].capability_id == "cloud_reply"
    assert turn_plan.steps[0].arguments == {}
    assert turn_plan.steps[1].arguments == {"emotion": "neutral"}
    assert turn_plan.steps[2].arguments == {}
