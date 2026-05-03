"""Integration-style tests for the current orchestrator runtime."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai.cloud import CloudReplyResult, MockCloudResponseService
from main import build_application, main
from orchestrator.capabilities import build_default_capability_registry
from orchestrator.router import LocalShortcutPlanner, LocalTurnDirector
from orchestrator.state import LifecycleStage
from shared.config import AppConfig
from shared.events import EventName
from shared.models import (
    ComponentName,
    Language,
    PlanStep,
    RouteKind,
    Transcript,
    TurnPlan,
)
from vision.service import MockVisionService


class CapturingCloudResponseService:
    """Record previous_response_id usage and return deterministic response ids."""

    def __init__(self, response_ids: list[str], *, fail_on_call: int | None = None) -> None:
        self.response_ids = response_ids
        self.fail_on_call = fail_on_call
        self.previous_response_ids: list[str | None] = []
        self.calls = 0

    async def generate_reply(  # type: ignore[no-untyped-def]
        self,
        transcript,
        context,
        plan,
        step_results,
        *,
        previous_response_id=None,
        tool_handler=None,
    ):
        del context, plan, step_results, tool_handler
        self.calls += 1
        self.previous_response_ids.append(previous_response_id)
        if self.fail_on_call == self.calls:
            raise RuntimeError("captured cloud failure")
        response_id = self.response_ids[min(self.calls - 1, len(self.response_ids) - 1)]
        from shared.models import AiResponse

        return CloudReplyResult(
            response=AiResponse(
                text=f"Cloud reply: {transcript.text}",
                language=Language.ENGLISH,
            ),
            response_id=response_id,
        )


class _CompletingRealtimeConversation:
    async def run_awake_session(self, *, audio_chunks):  # type: ignore[no-untyped-def]
        del audio_chunks


class _AwakeSessionSharedState:
    sample_rate = 16000
    channels = 1
    sample_width = 2

    def __init__(self) -> None:
        self.listeners = []

    async def ensure_session(self) -> None:
        return None

    async def sync(self) -> None:
        return None

    def start_utterance(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        del kwargs

    def current_utterance_window(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        return None

    def start_session_recording(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        del kwargs

    def add_chunk_listener(self, listener) -> None:  # type: ignore[no-untyped-def]
        self.listeners.append(listener)

    def remove_chunk_listener(self, listener) -> None:  # type: ignore[no-untyped-def]
        self.listeners.remove(listener)

    def stop_session_recording(self) -> None:
        return None

    def reset_utterance(self) -> None:
        return None


def _transcript(text: str) -> Transcript:
    return Transcript(
        text=text,
        language=Language.ENGLISH,
        confidence=1.0,
        is_final=True,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )


def test_main_returns_success_code() -> None:
    assert main(AppConfig()) == 0


def test_interactive_console_handles_eof_cleanly(monkeypatch) -> None:
    config = AppConfig()
    config.runtime.interactive_console = True
    service = build_application(config)

    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError()))

    asyncio.run(service.run())

    assert service.state.lifecycle is LifecycleStage.IDLE
    assert service.state.last_error is None


def test_orchestrator_manual_turn_completes_and_returns_to_idle() -> None:
    config = AppConfig()
    config.runtime.manual_inputs = ("look at me",)
    service = build_application(config)

    asyncio.run(service.run())

    assert service.state.lifecycle is LifecycleStage.IDLE
    assert service.state.head_direction == "user"
    assert service.state.current_response == "I am looking at you now."
    assert service.memory.records[-1].user_text == "look at me"


def test_realtime_awake_session_publishes_idle_only_after_session_exit() -> None:
    service = build_application(AppConfig())
    service.realtime_conversation = _CompletingRealtimeConversation()
    service.shared_live_speech_state = _AwakeSessionSharedState()

    asyncio.run(service._run_realtime_awake_session())

    assert [event.name for event in service.event_history] == [
        EventName.LISTENING,
        EventName.IDLE,
    ]
    assert service.event_history[-1].payload == {}
    assert service.state.lifecycle is LifecycleStage.IDLE


def test_turn_director_chooses_local_cloud_and_hybrid_paths() -> None:
    transcript = _transcript("who do you see")
    context = asyncio.run(build_application(AppConfig())._build_context())
    shortcut = LocalShortcutPlanner()
    director = LocalTurnDirector()

    visible = asyncio.run(shortcut.plan(transcript, context, ()))
    cloud = asyncio.run(director.direct_turn(_transcript("tell me a joke"), context, ()))
    mixed = asyncio.run(director.direct_turn(_transcript("look at me and tell me a joke"), context, ()))

    assert visible is not None
    assert visible.route_kind is RouteKind.LOCAL_QUERY
    assert cloud.route_kind is RouteKind.CLOUD_CHAT
    assert mixed.route_kind is RouteKind.HYBRID


def test_partial_transcript_updates_state_without_triggering_plan() -> None:
    service = build_application(AppConfig())
    partial = Transcript(
        text="who do you",
        language=Language.ENGLISH,
        confidence=0.9,
        is_final=False,
        started_at=datetime.now(UTC),
    )

    asyncio.run(service.handle_partial_transcript(partial))

    assert service.state.lifecycle is LifecycleStage.LISTENING
    assert service.state.current_transcript == partial
    assert service.state.last_plan is None
    assert {event.name for event in service.event_history} <= {
        EventName.IDLE,
        EventName.LISTENING,
        EventName.SPEAKING,
    }


def test_capability_registry_rejects_unknown_bad_and_unavailable_steps() -> None:
    registry = build_default_capability_registry()
    plan = TurnPlan(
        route_kind=RouteKind.HYBRID,
        confidence=1.0,
        source="test",
        steps=(
            PlanStep(capability_id="missing_capability"),
            PlanStep(capability_id="turn_head", arguments={"direction": "up"}),
            PlanStep(capability_id="cloud_reply"),
        ),
    )

    validated_plan, skipped = registry.validate_plan(
        plan,
        available_components={
            ComponentName.ORCHESTRATOR,
            ComponentName.HARDWARE,
            ComponentName.UI,
        },
    )

    assert validated_plan.steps == ()
    assert len(skipped) == 3
    assert skipped[0].skipped is True
    assert skipped[1].message.startswith("Capability 'turn_head' argument")
    assert skipped[2].message == "Capability 'cloud_reply' is currently unavailable."


def test_memory_and_vision_context_are_used_for_local_query() -> None:
    service = build_application(AppConfig())

    asyncio.run(service.run_turn(_transcript("who do you see")))

    assert service.state.current_response == "I can currently see Builder."
    assert service.state.active_user_id == "builder"
    assert service.memory.records[-1].route_kind is RouteKind.LOCAL_QUERY
    assert service.memory.records[-1].executed_steps == ("visible_people",)


def test_local_only_turn_does_not_call_cloud() -> None:
    class FailingCloudResponse:
        async def generate_reply(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("cloud reply should not run for explicit local-only turns")

    service = build_application(AppConfig())
    service.cloud_response = FailingCloudResponse()

    asyncio.run(service.run_turn(_transcript("who do you see")))

    assert service.state.current_response == "I can currently see Builder."
    assert service.memory.records[-1].route_kind is RouteKind.LOCAL_QUERY


def test_hybrid_turn_executes_local_action_and_cloud_reply() -> None:
    service = build_application(AppConfig())

    asyncio.run(service.run_turn(_transcript("look at me and tell me a joke")))

    assert service.state.head_direction == "user"
    assert service.state.current_response.startswith("Cloud reply:")
    assert service.state.last_plan is not None
    assert service.state.last_plan.route_kind is RouteKind.HYBRID
    assert service.memory.records[-1].executed_steps[-2:] == ("look_at_user", "cloud_reply")


def test_camera_tool_turn_captures_snapshot_and_final_reply() -> None:
    service = build_application(AppConfig())

    asyncio.run(service.run_turn(_transcript("what do you see here")))

    assert service.state.current_response == "Cloud reply: I took a look. Mock camera snapshot with Builder."
    assert service.memory.records[-1].route_kind is RouteKind.CLOUD_CHAT


def test_cloud_failure_falls_back_to_local_message() -> None:
    service = build_application(AppConfig())
    service.cloud_response = MockCloudResponseService(fail_on_text="fail")

    asyncio.run(service.run_turn(_transcript("please fail the cloud")))

    assert "falling back" in service.state.current_response
    assert service.state.lifecycle is LifecycleStage.IDLE
    assert service.state.last_error == "mock cloud failure"


def test_cloud_turn_stores_and_reuses_previous_response_id_within_resume_window() -> None:
    service = build_application(AppConfig())
    service.cloud_response = CapturingCloudResponseService(["resp_1", "resp_2"])

    async def run_sequence() -> None:
        await service.run_turn(_transcript("tell me a joke"))
        await service.run_turn(_transcript("and another one"))

    asyncio.run(run_sequence())

    assert service.cloud_response.previous_response_ids == [None, "resp_1"]
    assert service.state.last_openai_response_id == "resp_2"
    assert service.state.last_openai_response_at is not None


def test_cloud_turn_does_not_reuse_expired_previous_response_id() -> None:
    service = build_application(AppConfig())
    service.cloud_response = CapturingCloudResponseService(["resp_fresh"])
    service.state.last_openai_response_id = "resp_old"
    service.state.last_openai_response_at = datetime.now(UTC) - timedelta(minutes=6)

    asyncio.run(service.run_turn(_transcript("tell me a joke")))

    assert service.cloud_response.previous_response_ids == [None]
    assert service.state.last_openai_response_id == "resp_fresh"


def test_local_only_turn_does_not_create_or_refresh_openai_resume_state() -> None:
    service = build_application(AppConfig())
    service.state.last_openai_response_id = "resp_keep"
    original_timestamp = datetime.now(UTC) - timedelta(minutes=1)
    service.state.last_openai_response_at = original_timestamp

    asyncio.run(service.run_turn(_transcript("who do you see")))

    assert service.state.last_openai_response_id == "resp_keep"
    assert service.state.last_openai_response_at == original_timestamp


def test_cloud_failure_does_not_overwrite_existing_openai_resume_state() -> None:
    service = build_application(AppConfig())
    existing_timestamp = datetime.now(UTC) - timedelta(minutes=1)
    service.state.last_openai_response_id = "resp_keep"
    service.state.last_openai_response_at = existing_timestamp
    service.cloud_response = CapturingCloudResponseService(["resp_new"], fail_on_call=1)

    asyncio.run(service.run_turn(_transcript("tell me a joke")))

    assert service.state.last_openai_response_id == "resp_keep"
    assert service.state.last_openai_response_at == existing_timestamp


def test_reactive_step_happens_before_cloud_completion() -> None:
    service = build_application(AppConfig())

    asyncio.run(service.run_turn(_transcript("tell me a joke")))

    assert service.state.current_response.startswith("Cloud reply:")
    assert {event.name for event in service.event_history} <= {
        EventName.IDLE,
        EventName.LISTENING,
        EventName.SPEAKING,
    }


def test_vision_failure_does_not_block_text_flow() -> None:
    service = build_application(AppConfig())
    service.vision = MockVisionService(should_fail=True)

    asyncio.run(service.run_turn(_transcript("tell me a joke")))

    assert service.state.current_response.startswith("Cloud reply:")
    assert service.state.lifecycle is LifecycleStage.IDLE
    assert service.state.last_error == "mock vision failure"
