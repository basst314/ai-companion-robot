"""Cloud response services."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib import error, request

from shared.http_client import AsyncPersistentHttpClient
from shared.models import (
    AiResponse,
    EmotionState,
    InteractionContext,
    Language,
    PlanStepResult,
    Transcript,
    TurnPlan,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class CloudToolRequest:
    """Machine-readable tool request emitted by the cloud response layer."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class CloudToolResult:
    """Output returned by the local runtime for a cloud-requested tool."""

    call_id: str
    tool_name: str
    output_text: str | None = None
    image_url: str | None = None
    image_detail: str = "auto"


ToolExecutionHandler = Callable[[CloudToolRequest], Awaitable[CloudToolResult]]


@dataclass(slots=True, frozen=True)
class CloudReplyResult:
    """Cloud reply payload plus provider metadata needed across turns."""

    response: AiResponse
    response_id: str | None = None
    first_byte_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class OpenAiResponseEnvelope:
    """Response payload plus transport timing metadata."""

    payload: dict[str, Any]
    first_byte_at: datetime | None = None
    finished_at: datetime | None = None


class CloudResponseService(Protocol):
    """Interface for cloud-backed conversational generation."""

    async def generate_reply(
        self,
        transcript: Transcript,
        context: InteractionContext,
        plan: TurnPlan,
        step_results: tuple[PlanStepResult, ...],
        *,
        previous_response_id: str | None = None,
        tool_handler: ToolExecutionHandler | None = None,
    ) -> CloudReplyResult:
        """Generate a cloud-backed conversational reply."""


@dataclass(slots=True)
class MockCloudResponseService:
    """Mock cloud response generator that can request a local camera tool."""

    fail_on_text: str | None = None

    async def generate_reply(
        self,
        transcript: Transcript,
        context: InteractionContext,
        plan: TurnPlan,
        step_results: tuple[PlanStepResult, ...],
        *,
        previous_response_id: str | None = None,
        tool_handler: ToolExecutionHandler | None = None,
    ) -> CloudReplyResult:
        del plan
        del previous_response_id
        if self.fail_on_text and self.fail_on_text in transcript.text.lower():
            raise RuntimeError("mock cloud failure")

        if _camera_tool_hint(transcript.text) and tool_handler is not None:
            tool_result = await tool_handler(
                CloudToolRequest(
                    call_id="mock_camera_snapshot",
                    tool_name="camera_snapshot",
                    arguments={},
                )
            )
            return CloudReplyResult(
                response=AiResponse(
                    text=f"Cloud reply: I took a look. {tool_result.output_text or 'I have the snapshot now.'}",
                    language=transcript.language,
                    emotion=EmotionState.HAPPY,
                    intent="cloud_chat",
                )
            )

        observations = [
            result.message
            for result in step_results
            if result.success and result.capability_id != "cloud_reply"
        ]
        visible_people = ", ".join(detection.label for detection in context.current_detections) or "nobody right now"
        if observations:
            return CloudReplyResult(
                response=AiResponse(
                    text=f"Cloud reply: you said '{transcript.text}'. I already did this: {' '.join(observations)}",
                    language=transcript.language,
                    emotion=EmotionState.HAPPY,
                    intent="cloud_chat",
                )
            )

        return CloudReplyResult(
            response=AiResponse(
                text=f"Cloud reply: you said '{transcript.text}'. I currently see {visible_people}.",
                language=transcript.language,
                emotion=EmotionState.HAPPY,
                intent="cloud_chat",
            )
        )


@dataclass(slots=True)
class OpenAiResponsesClient:
    """Small persistent client for the OpenAI Responses API."""

    api_key: str
    base_url: str
    timeout_seconds: float = 20.0
    _http_client: AsyncPersistentHttpClient | None = field(default=None, init=False, repr=False)

    async def create_response(
        self,
        *,
        model: str,
        instructions: str,
        input_items: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        text_format: dict[str, Any] | None = None,
        previous_response_id: str | None = None,
        max_output_tokens: int | None = None,
        parallel_tool_calls: bool = False,
        stream: bool = False,
    ) -> OpenAiResponseEnvelope:
        payload: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": input_items,
        }
        if tools:
            payload["tools"] = tools
        if text_format is not None:
            payload["text"] = {"format": text_format}
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens
        if parallel_tool_calls:
            payload["parallel_tool_calls"] = True
        if stream:
            payload["stream"] = True
        return await self._post_json_async(payload)

    async def create_text_response(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
    ) -> str:
        response = _coerce_response_envelope(
            await self.create_response(
                model=model,
                instructions=instructions,
                input_items=input_text,
            )
        )
        return _extract_output_text(response.payload)

    async def create_structured_response(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "instructions": instructions,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
            "input": input_text,
        }
        response = await self._post_json_async(payload)
        raw_text = _extract_output_text(response.payload)
        return json.loads(raw_text)

    async def start(self) -> None:
        await self._ensure_http_client()

    async def shutdown(self) -> None:
        if self._http_client is not None:
            await self._http_client.close()

    async def _post_json_async(self, payload: dict[str, Any]) -> OpenAiResponseEnvelope:
        client = await self._ensure_http_client()
        response = await client.post(
            body=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        if response.status >= 400:
            raise RuntimeError(f"OpenAI request failed with HTTP {response.status}: {response.text()}")
        return OpenAiResponseEnvelope(
            payload=response.json(),
            first_byte_at=response.first_byte_at,
            finished_at=response.finished_at,
        )

    async def _ensure_http_client(self) -> AsyncPersistentHttpClient:
        if self._http_client is None:
            self._http_client = AsyncPersistentHttpClient(
                base_url=self.base_url,
                timeout_seconds=self.timeout_seconds,
            )
        return self._http_client

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Synchronous compatibility helper used by tests."""

        from urllib import error, request

        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        req = request.Request(self.base_url, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI request failed with HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"OpenAI request failed: {exc.reason}") from exc


@dataclass(slots=True)
class OpenAiCloudResponseService:
    """Cloud response generator backed by the OpenAI Responses API."""

    client: OpenAiResponsesClient
    model: str
    max_output_tokens: int = 120
    max_tool_rounds: int = 3
    wake_word_phrase: str | None = None

    async def start(self) -> None:
        await self.client.start()

    async def shutdown(self) -> None:
        await self.client.shutdown()

    async def generate_reply(
        self,
        transcript: Transcript,
        context: InteractionContext,
        plan: TurnPlan,
        step_results: tuple[PlanStepResult, ...],
        *,
        previous_response_id: str | None = None,
        tool_handler: ToolExecutionHandler | None = None,
    ) -> CloudReplyResult:
        instructions = _build_reply_instructions(self.wake_word_phrase)
        prompt = _build_response_prompt(
            transcript,
            context,
            plan,
            step_results,
            wake_word_phrase=self.wake_word_phrase,
        )
        reply_schema = _spoken_reply_schema()
        tools = [_camera_snapshot_tool_definition()] if tool_handler is not None else None
        logger.info(
            "turn_trace cloud_request_sent model=%s previous_response_id=%s prompt_chars=%s",
            self.model,
            previous_response_id or "--",
            len(prompt),
        )
        response_envelope = _coerce_response_envelope(
            await self.client.create_response(
                model=self.model,
                instructions=instructions,
                input_items=[
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": prompt}],
                    }
                ],
                tools=tools,
                text_format=reply_schema,
                previous_response_id=previous_response_id,
                max_output_tokens=self.max_output_tokens,
                parallel_tool_calls=True,
            )
        )
        response_payload = response_envelope.payload
        _log_ai_text_block(
            "reply request",
            f"model={self.model}\nmax_output_tokens={self.max_output_tokens}\nInstructions:\n{instructions}\n\nInput:\n{prompt}",
        )
        _log_ai_text_block("reply output", json.dumps(response_payload, indent=2, ensure_ascii=True))
        logger.info(
            "turn_trace cloud_response_received model=%s response_id=%s",
            self.model,
            str(response_payload.get("id", "")).strip() or "--",
        )
        return await self._resolve_response(
            transcript,
            context,
            step_results,
            instructions=instructions,
            tools=tools,
            payload=response_envelope,
            tool_handler=tool_handler,
        )

    async def _resolve_response(
        self,
        transcript: Transcript,
        context: InteractionContext,
        step_results: tuple[PlanStepResult, ...],
        *,
        instructions: str,
        tools: list[dict[str, Any]] | None,
        payload: OpenAiResponseEnvelope,
        tool_handler: ToolExecutionHandler | None,
    ) -> CloudReplyResult:
        current_payload = _coerce_response_envelope(payload)
        first_byte_at = current_payload.first_byte_at
        for _round in range(self.max_tool_rounds + 1):
            tool_calls = _extract_function_calls(current_payload.payload)
            if not tool_calls:
                reply_payload = _extract_structured_reply(current_payload.payload)
                response_id = str(current_payload.payload.get("id", "")).strip() or None
                return CloudReplyResult(
                    response=AiResponse(
                        text=reply_payload["text"],
                        language=_parse_reply_language(reply_payload["language"], default=transcript.language),
                        emotion=EmotionState.HAPPY if step_results else EmotionState.CURIOUS,
                        intent="cloud_chat",
                    ),
                    response_id=response_id,
                    first_byte_at=first_byte_at,
                    finished_at=current_payload.finished_at,
                )

            if tool_handler is None:
                raise RuntimeError("cloud reply requested tools but no local tool handler was provided")

            response_id = str(current_payload.payload.get("id", "")).strip()
            if not response_id:
                raise RuntimeError("tool-calling response was missing a response id")

            tool_outputs = []
            for tool_call in tool_calls:
                tool_result = await tool_handler(tool_call)
                tool_outputs.append(_tool_result_input_item(tool_result))

            current_payload = _coerce_response_envelope(
                await self.client.create_response(
                    model=self.model,
                    instructions=instructions,
                    input_items=tool_outputs,
                    tools=tools,
                    text_format=_spoken_reply_schema(),
                    previous_response_id=response_id,
                    max_output_tokens=self.max_output_tokens,
                    parallel_tool_calls=True,
                )
            )
            _log_ai_text_block("reply output", json.dumps(current_payload.payload, indent=2, ensure_ascii=True))

        raise RuntimeError("cloud reply exceeded the maximum number of tool rounds")

def _build_response_prompt(
    transcript: Transcript,
    context: InteractionContext,
    plan: TurnPlan,
    step_results: tuple[PlanStepResult, ...],
    *,
    wake_word_phrase: str | None = None,
) -> str:
    visible_people = ", ".join(detection.label for detection in context.current_detections) or "nobody"
    result_lines = [
        f"- {result.capability_id}: success={result.success} message={result.message}"
        for result in step_results
        if result.capability_id != "cloud_reply"
    ]
    if not result_lines:
        result_lines = ["- no prior local action/query output"]

    return (
        f"{_build_wake_word_context_line(wake_word_phrase)}\n"
        f"Current turn language: {transcript.language.value}\n"
        f"User transcript: {transcript.text}\n"
        f"Route kind: {plan.route_kind.value}\n"
        f"Route rationale: {plan.rationale or 'n/a'}\n"
        f"Visible people: {visible_people}\n"
        "Executed local step results:\n"
        + "\n".join(result_lines)
    )


def _build_reply_instructions(wake_word_phrase: str | None) -> str:
    wake_phrase = _normalize_wake_word_phrase(wake_word_phrase)
    parts = [
        "You are the spoken response layer for a friendly companion robot.",
        "Return only the words the robot should say, not JSON and not stage directions.",
        "Keep replies concise and easy to speak aloud, usually one or two short sentences.",
        "Unless the user explicitly asks for more detail, avoid long explanations.",
        "Do not end every reply with a follow-up question.",
        "When a brief acknowledgment or answer is enough, stop there instead of pushing the conversation forward.",
        "A brief reciprocal question is fine in a natural social exchange, but avoid repeatedly reopening the conversation with generic prompt-like questions.",
        "Ask a follow-up question only when it is genuinely helpful for clarifying the user's request or naturally fits the moment.",
        "If the user is closing the exchange, acknowledging, or saying they do not need anything, respond briefly and do not ask another question.",
        "If the transcript starts with leftover wake-word audio or a mis-transcribed near-sounding phrase, "
        "ignore that leading fragment when a clear request follows.",
    ]
    if wake_phrase:
        parts.append(
            f"The robot's wake-word/name is '{wake_phrase}'. A leading fragment may be an imperfect transcription "
            f"of '{wake_phrase}' rather than part of the request."
        )
    parts.append(
        "If the user is asking what is visible here, what you see in front of you, or to look at something, "
        "call camera_snapshot before answering."
    )
    parts.append(
        "If the user asks you to answer, speak, translate, joke, or write in a specific language, reply in that language."
    )
    parts.append(
        "Otherwise, reply in the language the user is using in the current turn, even if an earlier turn used a different language."
    )
    parts.append(
        "Treat the current turn language as the default for this reply; do not stay in a previous foreign-language thread unless this turn clearly asks for that language again."
    )
    parts.append(
        "When you reply, also set the structured language field to the language you are actually using."
    )
    return " ".join(parts)


def _spoken_reply_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "spoken_reply",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string"},
                "language": {
                    "type": "string",
                    "enum": [Language.ENGLISH.value, Language.GERMAN.value, Language.INDONESIAN.value],
                },
            },
            "required": ["text", "language"],
        },
    }


def _extract_structured_reply(payload: dict[str, Any]) -> dict[str, str]:
    if str(payload.get("status", "")).strip().lower() == "incomplete":
        reason = ""
        details = payload.get("incomplete_details")
        if isinstance(details, dict):
            reason = str(details.get("reason", "")).strip().lower()
        if reason == "max_output_tokens":
            raise RuntimeError(
                "OpenAI structured reply was truncated by max_output_tokens; "
                "increase AI_COMPANION_OPENAI_REPLY_MAX_OUTPUT_TOKENS"
            )
    raw_text = _extract_output_text(payload)
    parsed = json.loads(raw_text)
    if not isinstance(parsed, dict):
        raise RuntimeError("OpenAI structured reply was not an object")
    text = parsed.get("text")
    language = parsed.get("language")
    if not isinstance(text, str) or not isinstance(language, str):
        raise RuntimeError("OpenAI structured reply was missing text or language")
    return {"text": text.strip(), "language": language.strip()}


def _parse_reply_language(value: str, *, default: Language) -> Language:
    normalized = value.strip().lower()
    if normalized == Language.GERMAN.value:
        return Language.GERMAN
    if normalized == Language.INDONESIAN.value:
        return Language.INDONESIAN
    if normalized == Language.ENGLISH.value:
        return Language.ENGLISH
    return default


def _build_wake_word_context_line(wake_word_phrase: str | None) -> str:
    wake_phrase = _normalize_wake_word_phrase(wake_word_phrase)
    if not wake_phrase:
        return "Robot wake-word/name: n/a"
    return f"Robot wake-word/name: '{wake_phrase}'"


def _normalize_wake_word_phrase(wake_word_phrase: str | None) -> str | None:
    if wake_word_phrase is None:
        return None
    normalized = wake_word_phrase.strip()
    return normalized or None


def _extract_output_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                text = content["text"].strip()
                if text:
                    return text

    raise RuntimeError("OpenAI response did not contain any assistant text output")


def _coerce_response_envelope(payload: OpenAiResponseEnvelope | dict[str, Any]) -> OpenAiResponseEnvelope:
    if isinstance(payload, OpenAiResponseEnvelope):
        return payload
    return OpenAiResponseEnvelope(
        payload=payload,
        first_byte_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )


def _extract_function_calls(payload: dict[str, Any]) -> tuple[CloudToolRequest, ...]:
    tool_calls: list[CloudToolRequest] = []
    for item in payload.get("output", []):
        if item.get("type") != "function_call":
            continue
        call_id = str(item.get("call_id", "")).strip()
        tool_name = str(item.get("name", "")).strip()
        raw_arguments = item.get("arguments", "{}")
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else {}
        except json.JSONDecodeError:
            arguments = {}
        if call_id and tool_name:
            tool_calls.append(
                CloudToolRequest(
                    call_id=call_id,
                    tool_name=tool_name,
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
    return tuple(tool_calls)


def _tool_result_input_item(tool_result: CloudToolResult) -> dict[str, Any]:
    output: list[dict[str, Any]] = []
    if tool_result.output_text:
        output.append({"type": "input_text", "text": tool_result.output_text})
    if tool_result.image_url:
        output.append(
            {
                "type": "input_image",
                "image_url": tool_result.image_url,
                "detail": tool_result.image_detail,
            }
        )
    if not output:
        output = [{"type": "input_text", "text": ""}]
    return {
        "type": "function_call_output",
        "call_id": tool_result.call_id,
        "output": output,
    }


def _camera_snapshot_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "camera_snapshot",
        "description": "Capture the robot's current camera view when visual evidence is needed.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
            "required": [],
        },
    }


def _camera_tool_hint(text: str) -> bool:
    lowered = text.lower()
    return any(
        phrase in lowered
        for phrase in (
            "what do you see here",
            "what do you see in front of you",
            "take a look",
            "look at this",
            "can you see this",
        )
    )
def _log_ai_text_block(label: str, text: str) -> None:
    """Emit multi-line AI traffic into the runtime log/debug terminal."""

    logger.info("[AI] %s\n%s", label, text)
