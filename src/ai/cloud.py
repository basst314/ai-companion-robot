"""Cloud planning and response services."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request

from shared.models import (
    AiResponse,
    CapabilityDefinition,
    EmotionState,
    InteractionContext,
    PlanStep,
    PlanStepResult,
    RouteKind,
    Transcript,
    TurnPlan,
)

logger = logging.getLogger(__name__)


class CloudResponseService(Protocol):
    """Interface for cloud-backed conversational generation."""

    async def generate_reply(
        self,
        transcript: Transcript,
        context: InteractionContext,
        plan: TurnPlan,
        step_results: tuple[PlanStepResult, ...],
    ) -> AiResponse:
        """Generate a cloud-backed conversational reply."""


@dataclass(slots=True)
class MockCloudPlanningService:
    """Deterministic mock planner for tests and local development."""

    async def plan_turn(
        self,
        transcript: Transcript,
        context: InteractionContext,
        capabilities: tuple[CapabilityDefinition, ...],
    ) -> TurnPlan:
        del context, capabilities
        text = transcript.text.lower()
        steps: list[PlanStep] = []
        route_kind = RouteKind.CLOUD_CHAT
        rationale_parts: list[str] = []

        if "look at me" in text:
            steps.append(PlanStep(capability_id="look_at_user", reason="maintain visual attention"))
            rationale_parts.append("look at user")

        if "turn left" in text:
            steps.append(
                PlanStep(
                    capability_id="turn_head",
                    arguments={"direction": "left"},
                    reason="user asked for a left turn",
                )
            )
            rationale_parts.append("turn head left")
        elif "turn right" in text:
            steps.append(
                PlanStep(
                    capability_id="turn_head",
                    arguments={"direction": "right"},
                    reason="user asked for a right turn",
                )
            )
            rationale_parts.append("turn head right")

        if "can you see me" in text or "who do you see" in text or "what do you see" in text:
            steps.append(PlanStep(capability_id="visible_people", reason="answer with current vision state"))
            rationale_parts.append("use vision context")

        if "what do you know about me" in text:
            steps.append(PlanStep(capability_id="user_summary", reason="answer from memory"))
            rationale_parts.append("use memory context")

        if "what state are you in" in text or "what is your status" in text:
            steps.append(PlanStep(capability_id="robot_status", reason="answer from robot state"))
            rationale_parts.append("use robot state")

        if "smile" in text or "happy" in text:
            steps.append(
                PlanStep(
                    capability_id="set_emotion",
                    arguments={"emotion": EmotionState.HAPPY.value},
                    reason="show a positive expression",
                )
            )
            rationale_parts.append("show a happy expression")

        explicit_local_only = len(steps) == 1 and steps[0].capability_id in {"visible_people", "user_summary", "robot_status"}
        if explicit_local_only and " and " not in text:
            route_kind = RouteKind.LOCAL_QUERY
        elif steps and not any(
            phrase in text
            for phrase in (
                "tell me",
                "joke",
                "why",
                "how",
                "what do you think",
                "explain",
                "say",
                "chat",
            )
        ):
            route_kind = RouteKind.LOCAL_ACTION
        else:
            steps.append(PlanStep(capability_id="cloud_reply", reason="generate final conversational reply"))
            route_kind = RouteKind.HYBRID if len(steps) > 1 else RouteKind.CLOUD_CHAT
            rationale_parts.append("generate cloud reply")

        rationale = ", ".join(rationale_parts) if rationale_parts else "defaulted to cloud conversation"
        return TurnPlan(
            route_kind=route_kind,
            confidence=0.82,
            rationale=rationale,
            source="mock_cloud_planner",
            steps=tuple(steps),
        )


@dataclass(slots=True)
class MockCloudResponseService:
    """Mock cloud response generator that uses prior observations when available."""

    fail_on_text: str | None = None

    async def generate_reply(
        self,
        transcript: Transcript,
        context: InteractionContext,
        plan: TurnPlan,
        step_results: tuple[PlanStepResult, ...],
    ) -> AiResponse:
        del plan
        if self.fail_on_text and self.fail_on_text in transcript.text.lower():
            raise RuntimeError("mock cloud failure")

        observations = [result.message for result in step_results if result.success and result.capability_id != "cloud_reply"]
        visible_people = ", ".join(detection.label for detection in context.current_detections) or "nobody right now"
        if observations:
            return AiResponse(
                text=f"Cloud reply: you said '{transcript.text}'. I already did this: {' '.join(observations)}",
                emotion=EmotionState.HAPPY,
                intent="cloud_chat",
            )

        return AiResponse(
            text=f"Cloud reply: you said '{transcript.text}'. I currently see {visible_people}.",
            emotion=EmotionState.HAPPY,
            intent="cloud_chat",
        )


@dataclass(slots=True)
class OpenAiResponsesClient:
    """Small stdlib-based client for the OpenAI Responses API."""

    api_key: str
    base_url: str
    timeout_seconds: float = 20.0

    async def create_text_response(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
    ) -> str:
        payload = {
            "model": model,
            "instructions": instructions,
            "input": input_text,
        }
        response = await asyncio.to_thread(self._post_json, payload)
        return _extract_output_text(response)

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
        response = await asyncio.to_thread(self._post_json, payload)
        raw_text = _extract_output_text(response)
        return json.loads(raw_text)

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
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
class OpenAiCloudPlanningService:
    """Cloud planner backed by OpenAI structured JSON output."""

    client: OpenAiResponsesClient
    model: str

    async def plan_turn(
        self,
        transcript: Transcript,
        context: InteractionContext,
        capabilities: tuple[CapabilityDefinition, ...],
    ) -> TurnPlan:
        instructions = (
            "You are the planning layer for a local companion robot. "
            "Choose only from the provided capabilities. "
            "Return the minimum safe plan needed to satisfy the user. "
            "If a plan includes cloud_reply plus any local step, route_kind must be 'hybrid'. "
            "If cloud_reply is the only step, route_kind must be 'cloud_chat'. "
            "If there is no cloud_reply step, route_kind must be 'local_action' or 'local_query' as appropriate. "
            "Only include arguments that the chosen capability accepts. "
            "For capabilities with no arguments, return an empty object for arguments."
        )
        prompt = _build_planning_prompt(transcript, context, capabilities)
        _log_ai_text_block("planner request", f"model={self.model}\nInstructions:\n{instructions}\n\nInput:\n{prompt}")
        raw_plan = await self.client.create_structured_response(
            model=self.model,
            instructions=instructions,
            input_text=prompt,
            schema_name="robot_turn_plan",
            schema=_TURN_PLAN_SCHEMA,
        )
        _log_ai_text_block("planner output", json.dumps(raw_plan, indent=2, ensure_ascii=True))
        return _turn_plan_from_json(raw_plan, capabilities=capabilities)


@dataclass(slots=True)
class OpenAiCloudResponseService:
    """Cloud response generator backed by OpenAI text output."""

    client: OpenAiResponsesClient
    model: str

    async def generate_reply(
        self,
        transcript: Transcript,
        context: InteractionContext,
        plan: TurnPlan,
        step_results: tuple[PlanStepResult, ...],
    ) -> AiResponse:
        instructions = (
            "You are the spoken response layer for a friendly companion robot. "
            "Return only the final reply text, not JSON and not stage directions."
        )
        prompt = _build_response_prompt(transcript, context, plan, step_results)
        _log_ai_text_block("reply request", f"model={self.model}\nInstructions:\n{instructions}\n\nInput:\n{prompt}")
        text = await self.client.create_text_response(
            model=self.model,
            instructions=instructions,
            input_text=prompt,
        )
        _log_ai_text_block("reply output", text)
        return AiResponse(
            text=text.strip(),
            emotion=EmotionState.HAPPY if step_results else EmotionState.CURIOUS,
            intent="cloud_chat",
        )


_TURN_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "route_kind": {
            "type": "string",
            "enum": [route_kind.value for route_kind in RouteKind],
        },
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "capability_id": {"type": "string"},
                    "arguments": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "direction": {
                                "type": ["string", "null"],
                                "enum": ["left", "right", "center", "user", None],
                            },
                            "emotion": {
                                "type": ["string", "null"],
                                "enum": [emotion.value for emotion in EmotionState] + [None],
                            },
                        },
                        "required": ["direction", "emotion"],
                    },
                },
                "required": ["capability_id", "arguments"],
            },
        },
    },
    "required": ["route_kind", "steps"],
}


def _build_planning_prompt(
    transcript: Transcript,
    context: InteractionContext,
    capabilities: tuple[CapabilityDefinition, ...],
) -> str:
    capability_lines = []
    for capability in capabilities:
        schema_summary = ", ".join(
            f"{name}:{spec.get('type', 'any')}"
            for name, spec in capability.argument_schema.items()
        )
        if not schema_summary:
            schema_summary = "no arguments"
        capability_lines.append(
            f"- {capability.capability_id} [{capability.kind.value}/{capability.phase.value}] "
            f"{capability.description} ({schema_summary})"
        )

    visible_people = ", ".join(detection.label for detection in context.current_detections) or "nobody"
    active_user = context.active_user.display_name if context.active_user and context.active_user.display_name else "unknown"

    return (
        "Available capabilities:\n"
        + "\n".join(capability_lines)
        + f"\nLanguage: {transcript.language.value}\n"
        + f"Active user: {active_user}\n"
        + f"Visible people: {visible_people}\n"
        + f"Robot state: lifecycle={context.robot_state.lifecycle}, "
        + f"emotion={context.robot_state.emotion.value}, eyes_open={context.robot_state.eyes_open}, "
        + f"head_direction={context.robot_state.head_direction}\n"
        + f"\nUser transcript: {transcript.text}"
    )


def _build_response_prompt(
    transcript: Transcript,
    context: InteractionContext,
    plan: TurnPlan,
    step_results: tuple[PlanStepResult, ...],
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
        f"User transcript: {transcript.text}\n"
        f"Plan route kind: {plan.route_kind.value}\n"
        f"Plan rationale: {plan.rationale or 'n/a'}\n"
        f"Visible people: {visible_people}\n"
        "Executed local step results:\n"
        + "\n".join(result_lines)
    )


def _turn_plan_from_json(
    payload: dict[str, Any],
    *,
    capabilities: tuple[CapabilityDefinition, ...] = (),
) -> TurnPlan:
    definitions = {capability.capability_id: capability for capability in capabilities}
    raw_steps = payload.get("steps", [])
    steps = []
    for raw_step in raw_steps:
        capability_id = str(raw_step.get("capability_id", "")).strip()
        definition = definitions.get(capability_id)
        raw_arguments = raw_step.get("arguments", {})
        arguments = (
            {
                str(name): value
                for name, value in dict(raw_arguments).items()
                if value is not None
            }
            if isinstance(raw_arguments, dict)
            else {}
        )
        if definition is not None:
            allowed_argument_names = set(definition.argument_schema)
            if not allowed_argument_names:
                arguments = {}
            else:
                arguments = {
                    name: value
                    for name, value in arguments.items()
                    if name in allowed_argument_names
                }
        steps.append(
            PlanStep(
                capability_id=capability_id,
                arguments=arguments,
            )
        )
    route_kind = _normalize_route_kind(payload.get("route_kind"), steps)

    return TurnPlan(
        route_kind=route_kind,
        confidence=0.6,
        rationale=None,
        source="openai_planner",
        steps=tuple(step for step in steps if step.capability_id),
    )


def _normalize_route_kind(raw_route_kind: Any, steps: list[PlanStep]) -> RouteKind:
    step_ids = {step.capability_id for step in steps if step.capability_id}
    non_reply_step_ids = step_ids - {"cloud_reply"}

    if "cloud_reply" in step_ids:
        return RouteKind.HYBRID if non_reply_step_ids else RouteKind.CLOUD_CHAT

    try:
        route_kind = RouteKind(str(raw_route_kind))
    except ValueError:
        route_kind = RouteKind.CLOUD_CHAT

    if route_kind in {RouteKind.HYBRID, RouteKind.CLOUD_CHAT} and "cloud_reply" not in step_ids:
        return RouteKind.LOCAL_QUERY if any(step_id in {"visible_people", "user_summary", "robot_status"} for step_id in step_ids) else RouteKind.LOCAL_ACTION

    return route_kind


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


def _log_ai_text_block(label: str, text: str) -> None:
    """Emit multi-line AI traffic into the runtime log/debug terminal."""

    logger.info("[AI] %s\n%s", label, text)
