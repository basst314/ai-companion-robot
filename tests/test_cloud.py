"""Tests for cloud response logging and tool calls."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from datetime import UTC, datetime
import pytest

import ai.cloud as cloud_mod
from ai.cloud import (
    CloudReplyResult,
    CloudToolRequest,
    CloudToolResult,
    OpenAiResponsesClient,
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
        text_format=None,
        previous_response_id=None,
        max_output_tokens=None,
        parallel_tool_calls=False,
        stream=False,
    ):
        assert model == "gpt-5.2"
        assert "spoken response layer" in instructions
        assert "one or two short sentences" in instructions
        assert "Do not end every reply with a follow-up question." in instructions
        assert "A brief reciprocal question is fine in a natural social exchange" in instructions
        assert "If the user is closing the exchange" in instructions
        assert "leftover wake-word audio" in instructions
        assert "The robot's wake-word/name is 'Oreo'." in instructions
        assert tools is None
        assert text_format is not None
        assert text_format["type"] == "json_schema"
        assert text_format["name"] == "spoken_reply"
        assert previous_response_id is None
        assert max_output_tokens == 72
        assert parallel_tool_calls is True
        assert stream is False
        prompt = input_items[0]["content"][0]["text"]
        assert "Current turn language: en" in prompt
        assert "Robot wake-word/name: 'Oreo'" in prompt
        assert "'Oreo'" in prompt
        assert "Executed local step results:" in prompt
        return {
            "id": "resp_1",
            "output_text": json.dumps(
                {
                    "text": "I can see you, and I am looking your way.",
                    "language": Language.ENGLISH.value,
                }
            ),
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
        text_format=None,
        previous_response_id=None,
        max_output_tokens=None,
        parallel_tool_calls=False,
        stream=False,
    ):
        assert model == "gpt-5.2"
        assert "camera_snapshot" in instructions
        assert text_format is not None
        assert text_format["name"] == "spoken_reply"
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
            "output_text": json.dumps(
                {
                    "text": "I took a look. I can see Basti in front of me.",
                    "language": Language.ENGLISH.value,
                }
            ),
        }


class FakeResponseClientWithPreviousId(FakeResponseClient):
    def __init__(self) -> None:
        self.captured_previous_response_id = None

    async def create_response(  # type: ignore[no-untyped-def]
        self,
        *,
        model,
        instructions,
        input_items,
        tools=None,
        text_format=None,
        previous_response_id=None,
        max_output_tokens=None,
        parallel_tool_calls=False,
        stream=False,
    ):
        self.captured_previous_response_id = previous_response_id
        assert model == "gpt-5.2"
        assert "spoken response layer" in instructions
        assert tools is None
        assert text_format is not None
        assert max_output_tokens == 72
        assert parallel_tool_calls is True
        assert stream is False
        prompt = input_items[0]["content"][0]["text"]
        assert "Current turn language: en" in prompt
        assert "Robot wake-word/name: 'Oreo'" in prompt
        return {
            "id": "resp_1",
            "output_text": json.dumps(
                {
                    "text": "I can see you, and I am looking your way.",
                    "language": Language.ENGLISH.value,
                }
            ),
        }


class FakeIndonesianResponseClient:
    async def create_response(  # type: ignore[no-untyped-def]
        self,
        *,
        model,
        instructions,
        input_items,
        tools=None,
        text_format=None,
        previous_response_id=None,
        max_output_tokens=None,
        parallel_tool_calls=False,
        stream=False,
    ):
        assert model == "gpt-5.2"
        assert "reply in that language" in instructions
        assert "current turn language as the default" in instructions
        assert tools is None
        assert text_format is not None
        assert previous_response_id is None
        assert max_output_tokens == 72
        assert parallel_tool_calls is True
        assert stream is False
        return {
            "id": "resp_id_indo",
            "output_text": json.dumps(
                {
                    "text": "Ini lelucon untukmu.",
                    "language": Language.INDONESIAN.value,
                }
            ),
        }


class FakeEnglishAfterIndonesianClient:
    def __init__(self) -> None:
        self.calls = 0

    async def create_response(  # type: ignore[no-untyped-def]
        self,
        *,
        model,
        instructions,
        input_items,
        tools=None,
        text_format=None,
        previous_response_id=None,
        max_output_tokens=None,
        parallel_tool_calls=False,
        stream=False,
    ):
        assert model == "gpt-5.2"
        assert "current turn" in instructions
        assert tools is None
        assert text_format is not None
        assert max_output_tokens == 72
        assert parallel_tool_calls is True
        assert stream is False
        self.calls += 1
        if self.calls == 1:
            assert previous_response_id is None
            return {
                "id": "resp_indo",
                "output_text": json.dumps(
                    {
                        "text": "Ini lelucon untukmu.",
                        "language": Language.INDONESIAN.value,
                    }
                ),
            }
        assert previous_response_id == "resp_indo"
        return {
            "id": "resp_en",
            "output_text": json.dumps(
                {
                    "text": "Glad you liked it.",
                    "language": Language.ENGLISH.value,
                }
            ),
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
    service = OpenAiCloudResponseService(
        client=FakeResponseClient(),
        model="gpt-5.2",
        max_output_tokens=72,
        wake_word_phrase="Oreo",
    )
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
        result = asyncio.run(service.generate_reply(transcript, _context(), plan, step_results))

    assert result.response.text == "I can see you, and I am looking your way."
    assert result.response.language is Language.ENGLISH
    assert result.response_id == "resp_1"
    log_text = caplog.text
    assert "[AI] reply request" in log_text
    assert "max_output_tokens=72" in log_text
    assert "can you see me?" in log_text
    assert "I can currently see Basti." in log_text
    assert "Robot wake-word/name: 'Oreo'" in log_text
    assert "leftover wake-word audio" in log_text
    assert "turn_trace cloud_request_sent" in log_text
    assert "turn_trace cloud_response_received" in log_text
    assert "[AI] reply output" in log_text
    assert "I can see you, and I am looking your way." in log_text


def test_openai_reply_handles_tool_call_round_trip() -> None:
    service = OpenAiCloudResponseService(
        client=FakeToolClient(),
        model="gpt-5.2",
        max_output_tokens=72,
        wake_word_phrase="Oreo",
    )
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

    result = asyncio.run(
        service.generate_reply(
            transcript,
            _context(),
            plan,
            (),
            tool_handler=tool_handler,
        )
    )

    assert result.response.text == "I took a look. I can see Basti in front of me."
    assert result.response.language is Language.ENGLISH
    assert result.response_id == "resp_tool_2"


class FakeIncompleteStructuredResponseClient:
    async def create_response(  # type: ignore[no-untyped-def]
        self,
        *,
        model,
        instructions,
        input_items,
        tools=None,
        text_format=None,
        previous_response_id=None,
        max_output_tokens=None,
        parallel_tool_calls=False,
        stream=False,
    ):
        del model, instructions, input_items, tools, text_format, previous_response_id, max_output_tokens
        del parallel_tool_calls, stream
        return {
            "id": "resp_incomplete",
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "{\"text\":\"Once upon a time",
                        }
                    ],
                }
            ],
        }


def test_openai_reply_forwards_previous_response_id_when_provided() -> None:
    client = FakeResponseClientWithPreviousId()
    service = OpenAiCloudResponseService(
        client=client,
        model="gpt-5.2",
        max_output_tokens=72,
        wake_word_phrase="Oreo",
    )
    transcript = Transcript(
        text="and what about now?",
        language=Language.ENGLISH,
        confidence=0.98,
        is_final=True,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    plan = TurnPlan(
        route_kind=RouteKind.CLOUD_CHAT,
        confidence=0.9,
        rationale="follow-up cloud reply",
        source="local_turn_director",
        steps=(PlanStep(capability_id="cloud_reply"),),
    )

    result = asyncio.run(
        service.generate_reply(
            transcript,
            _context(),
            plan,
            (),
            previous_response_id="resp_prev_123",
        )
    )

    assert client.captured_previous_response_id == "resp_prev_123"
    assert result.response_id == "resp_1"


def test_openai_reply_surfaces_incomplete_structured_output_cleanly() -> None:
    service = OpenAiCloudResponseService(
        client=FakeIncompleteStructuredResponseClient(),
        model="gpt-5.2",
        max_output_tokens=72,
        wake_word_phrase="Oreo",
    )
    transcript = Transcript(
        text="tell a long story",
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

    with pytest.raises(RuntimeError, match="max_output_tokens"):
        asyncio.run(service.generate_reply(transcript, _context(), plan, ()))


def test_openai_reply_can_set_indonesian_language_even_when_transcript_is_english() -> None:
    service = OpenAiCloudResponseService(
        client=FakeIndonesianResponseClient(),
        model="gpt-5.2",
        max_output_tokens=72,
        wake_word_phrase="Oreo",
    )
    transcript = Transcript(
        text="tell me an indonesian joke",
        language=Language.ENGLISH,
        confidence=1.0,
        is_final=True,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    plan = TurnPlan(
        route_kind=RouteKind.CLOUD_CHAT,
        confidence=0.7,
        rationale="language-specific cloud reply",
        source="local_turn_director",
        steps=(PlanStep(capability_id="cloud_reply"),),
    )

    result = asyncio.run(service.generate_reply(transcript, _context(), plan, ()))

    assert result.response.language is Language.INDONESIAN
    assert result.response.text == "Ini lelucon untukmu."
    assert result.response_id == "resp_id_indo"


def test_openai_reply_can_return_to_current_turn_language_after_prior_foreign_language_turn() -> None:
    service = OpenAiCloudResponseService(
        client=FakeEnglishAfterIndonesianClient(),
        model="gpt-5.2",
        max_output_tokens=72,
        wake_word_phrase="Oreo",
    )
    indo_request = Transcript(
        text="tell me an indonesian joke",
        language=Language.ENGLISH,
        confidence=1.0,
        is_final=True,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    english_follow_up = Transcript(
        text="that was funny",
        language=Language.ENGLISH,
        confidence=1.0,
        is_final=True,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    plan = TurnPlan(
        route_kind=RouteKind.CLOUD_CHAT,
        confidence=0.7,
        rationale="language continuity test",
        source="local_turn_director",
        steps=(PlanStep(capability_id="cloud_reply"),),
    )

    first = asyncio.run(service.generate_reply(indo_request, _context(), plan, ()))
    second = asyncio.run(
        service.generate_reply(
            english_follow_up,
            _context(),
            plan,
            (),
            previous_response_id=first.response_id,
        )
    )

    assert first.response.language is Language.INDONESIAN
    assert second.response.language is Language.ENGLISH
    assert second.response.text == "Glad you liked it."


def test_cloud_helper_functions_cover_prompt_and_parsing_branches() -> None:
    transcript = Transcript(
        text="can you look at this?",
        language=Language.ENGLISH,
        confidence=1.0,
        is_final=True,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    context = _context()
    plan = TurnPlan(
        route_kind=RouteKind.HYBRID,
        confidence=0.9,
        rationale="vision then reply",
        source="local_turn_director",
        steps=(PlanStep(capability_id="cloud_reply"),),
    )
    prompt = cloud_mod._build_response_prompt(
        transcript,
        context,
        plan,
        (),
        wake_word_phrase=" Oreo ",
    )
    instructions = cloud_mod._build_reply_instructions(" Oreo ")
    schema = cloud_mod._spoken_reply_schema()
    wake_line = cloud_mod._build_wake_word_context_line(" Oreo ")

    assert "Robot wake-word/name: 'Oreo'" in prompt
    assert "Executed local step results" in prompt
    assert "The robot's wake-word/name is 'Oreo'." in instructions
    assert schema["name"] == "spoken_reply"
    assert wake_line == "Robot wake-word/name: 'Oreo'"
    assert cloud_mod._normalize_wake_word_phrase("   ") is None
    assert cloud_mod._parse_reply_language("de", default=Language.ENGLISH) is Language.GERMAN
    assert cloud_mod._parse_reply_language("unknown", default=Language.INDONESIAN) is Language.INDONESIAN
    assert cloud_mod._camera_tool_hint("Can you see this?") is True
    assert cloud_mod._camera_tool_hint("How are you?") is False
    assert cloud_mod._tool_result_input_item(
        CloudToolResult(call_id="call_1", tool_name="camera_snapshot")
    ) == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": [{"type": "input_text", "text": ""}],
    }
    assert cloud_mod._camera_snapshot_tool_definition()["name"] == "camera_snapshot"


def test_cloud_helper_extractors_cover_success_and_error_paths() -> None:
    output_text_payload = {"output_text": "  hello  "}
    message_payload = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "  hi there  "}],
            }
        ]
    }
    call_payload = {
        "output": [
            {"type": "function_call", "call_id": "call_1", "name": "camera_snapshot", "arguments": '{"x": 1}'},
            {"type": "function_call", "call_id": "call_2", "name": "camera_snapshot", "arguments": "{broken}"},
            {"type": "message", "content": []},
        ]
    }
    structured_payload = {"output_text": json.dumps({"text": "  hello  ", "language": "en"})}

    assert cloud_mod._extract_output_text(output_text_payload) == "  hello  "
    assert cloud_mod._extract_output_text(message_payload) == "hi there"
    assert cloud_mod._extract_function_calls(call_payload) == (
        CloudToolRequest(call_id="call_1", tool_name="camera_snapshot", arguments={"x": 1}),
        CloudToolRequest(call_id="call_2", tool_name="camera_snapshot", arguments={}),
    )
    assert cloud_mod._extract_structured_reply(structured_payload) == {"text": "hello", "language": "en"}
    assert cloud_mod._extract_structured_reply(
        {"status": "incomplete", "incomplete_details": {"reason": "other"}, "output_text": json.dumps({"text": "ok", "language": "en"})}
    ) == {"text": "ok", "language": "en"}

    with pytest.raises(RuntimeError, match="OpenAI structured reply was missing text or language"):
        cloud_mod._extract_structured_reply({"output_text": json.dumps({"text": "hello"})})
    with pytest.raises(RuntimeError, match="OpenAI response did not contain any assistant text output"):
        cloud_mod._extract_output_text({"output": []})


def test_openai_responses_client_and_service_error_paths(monkeypatch) -> None:
    client = OpenAiResponsesClient(api_key="test-key", base_url="http://example.test")
    captured: dict[str, object] = {}
    real_post_json = OpenAiResponsesClient._post_json

    def fake_post_json(self, payload: dict[str, object]) -> dict[str, object]:
        del self
        captured["payload"] = payload
        return {"output_text": "hello"}

    monkeypatch.setattr(OpenAiResponsesClient, "_post_json", fake_post_json)

    response = asyncio.run(
        client.create_response(
            model="gpt-5.2",
            instructions="be nice",
            input_items="hello",
            tools=[{"type": "function"}],
            text_format={"type": "json_schema"},
            previous_response_id="resp_prev",
            max_output_tokens=12,
            parallel_tool_calls=True,
            stream=True,
        )
    )
    assert response == {"output_text": "hello"}
    assert captured["payload"] == {
        "model": "gpt-5.2",
        "instructions": "be nice",
        "input": "hello",
        "tools": [{"type": "function"}],
        "text": {"format": {"type": "json_schema"}},
        "previous_response_id": "resp_prev",
        "max_output_tokens": 12,
        "parallel_tool_calls": True,
        "stream": True,
    }
    assert asyncio.run(client.create_text_response(model="gpt-5.2", instructions="be nice", input_text="hello")) == "hello"
    monkeypatch.setattr(
        OpenAiResponsesClient,
        "_post_json",
        lambda self, payload: {"output_text": json.dumps({"hello": "world"})},
    )
    assert (
        asyncio.run(
            client.create_structured_response(
                model="gpt-5.2",
                instructions="be nice",
                input_text="hello",
                schema_name="reply",
                schema={"type": "object"},
            )
        )
        == {"hello": "world"}
    )

    http_error = cloud_mod.error.HTTPError(
        url="http://example.test",
        code=500,
        msg="boom",
        hdrs=None,
        fp=io.BytesIO(b"broken"),
    )
    monkeypatch.setattr(cloud_mod.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(http_error))
    with pytest.raises(RuntimeError, match="HTTP 500"):
        real_post_json(client, {"model": "gpt-5.2"})

    monkeypatch.setattr(
        cloud_mod.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(cloud_mod.error.URLError("dns failed")),
    )
    with pytest.raises(RuntimeError, match="dns failed"):
        real_post_json(client, {"model": "gpt-5.2"})

    class _ToolClient:
        async def create_response(self, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            return {
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "camera_snapshot",
                        "arguments": "{}",
                    }
                ]
            }

    service = OpenAiCloudResponseService(client=_ToolClient(), model="gpt-5.2", max_tool_rounds=0)
    transcript = Transcript(
        text="what do you see?",
        language=Language.ENGLISH,
        confidence=1.0,
        is_final=True,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    plan = TurnPlan(
        route_kind=RouteKind.CLOUD_CHAT,
        confidence=1.0,
        rationale="tool path",
        source="local_turn_director",
        steps=(PlanStep(capability_id="cloud_reply"),),
    )

    with pytest.raises(RuntimeError, match="no local tool handler"):
        asyncio.run(service.generate_reply(transcript, _context(), plan, (), tool_handler=None))

    class _MissingIdToolClient:
        async def create_response(self, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            return {
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "camera_snapshot",
                        "arguments": "{}",
                    }
                ]
            }

    service = OpenAiCloudResponseService(client=_MissingIdToolClient(), model="gpt-5.2", max_tool_rounds=0)

    async def handler(request: CloudToolRequest) -> CloudToolResult:
        return CloudToolResult(call_id=request.call_id, tool_name=request.tool_name)

    with pytest.raises(RuntimeError, match="response id"):
        asyncio.run(
            service._resolve_response(
                transcript,
                _context(),
                (),
                instructions="instructions",
                tools=None,
                payload={"output": [{"type": "function_call", "call_id": "call_1", "name": "camera_snapshot", "arguments": "{}"}]},
                tool_handler=handler,
            )
        )

    with pytest.raises(RuntimeError, match="maximum number of tool rounds"):
        asyncio.run(
            service._resolve_response(
                transcript,
                _context(),
                (),
                instructions="instructions",
                tools=None,
                payload={"id": "resp_1", "output": [{"type": "function_call", "call_id": "call_1", "name": "camera_snapshot", "arguments": "{}"}]},
                tool_handler=handler,
            )
        )
