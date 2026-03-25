"""Tests for cloud response logging and tool calls."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from ai.cloud import (
    CloudToolRequest,
    CloudToolResult,
    OpenAiCloudResponseService,
)
from shared.models import (
    EmotionState,
    InteractionContext,
    Language,
    PlanStep,
    PlanStepResult,
    RobotStateSnapshot,
    RouteKind,
    Transcript,
    TurnPlan,
    UserIdentity,
    VisionDetection,
)


class FakeResponseClient:
    async def create_response(  # type: ignore[no-untyped-def]
        self,
        *,
        model,
        instructions,
        input_items,
        tools=None,
        previous_response_id=None,
        max_output_tokens=None,
        parallel_tool_calls=False,
        stream=False,
    ):
        assert model == "gpt-5.2"
        assert "spoken response layer" in instructions
        assert "one or two short sentences" in instructions
        assert tools is None
        assert previous_response_id is None
        assert max_output_tokens == 72
        assert parallel_tool_calls is True
        assert stream is False
        prompt = input_items[0]["content"][0]["text"]
        assert "Executed local step results:" in prompt
        return {
            "id": "resp_1",
            "output_text": "I can see you, and I am looking your way.",
        }


class FakeToolClient:
    def __init__(self) -> None:
        self.calls = 0

    async def create_response(  # type: ignore[no-untyped-def]
        self,
        *,
        model,
        instructions,
        input_items,
        tools=None,
        previous_response_id=None,
        max_output_tokens=None,
        parallel_tool_calls=False,
        stream=False,
    ):
        assert model == "gpt-5.2"
        assert "camera_snapshot" in instructions
        assert max_output_tokens == 72
        assert parallel_tool_calls is True
        assert stream is False
        self.calls += 1
        if self.calls == 1:
            assert tools is not None
            assert previous_response_id is None
            return {
                "id": "resp_tool_1",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "camera_snapshot",
                        "arguments": "{}",
                    }
                ],
            }

        assert previous_response_id == "resp_tool_1"
        assert tools is not None
        assert input_items == [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": [
                    {"type": "input_text", "text": "Snapshot shows Basti."},
                    {
                        "type": "input_image",
                        "image_url": "data:image/gif;base64,R0lGODlhAQABAIABAP///wAAACwAAAAAAQABAAACAkQBADs=",
                        "detail": "auto",
                    },
                ],
            }
        ]
        return {
            "id": "resp_tool_2",
            "output_text": "I took a look. I can see Basti in front of me.",
        }


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
def test_openai_reply_logs_exact_request_and_output(caplog) -> None:
    service = OpenAiCloudResponseService(client=FakeResponseClient(), model="gpt-5.2", max_output_tokens=72)
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
        source="local_turn_director",
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
    assert "max_output_tokens=72" in log_text
    assert "can you see me?" in log_text
    assert "I can currently see Basti." in log_text
    assert "[AI] reply output" in log_text
    assert "I can see you, and I am looking your way." in log_text


def test_openai_reply_handles_tool_call_round_trip() -> None:
    service = OpenAiCloudResponseService(client=FakeToolClient(), model="gpt-5.2", max_output_tokens=72)
    transcript = Transcript(
        text="what do you see here?",
        language=Language.ENGLISH,
        confidence=1.0,
        is_final=True,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    plan = TurnPlan(
        route_kind=RouteKind.CLOUD_CHAT,
        confidence=0.6,
        rationale="defaulted to a single cloud reply",
        source="local_turn_director",
        steps=(PlanStep(capability_id="cloud_reply"),),
    )

    async def tool_handler(request: CloudToolRequest) -> CloudToolResult:
        assert request.tool_name == "camera_snapshot"
        return CloudToolResult(
            call_id=request.call_id,
            tool_name=request.tool_name,
            output_text="Snapshot shows Basti.",
            image_url="data:image/gif;base64,R0lGODlhAQABAIABAP///wAAACwAAAAAAQABAAACAkQBADs=",
        )

    response = asyncio.run(
        service.generate_reply(
            transcript,
            _context(),
            plan,
            (),
            tool_handler=tool_handler,
        )
    )

    assert response.text == "I took a look. I can see Basti in front of me."
