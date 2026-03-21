"""Integration-style tests for the mock orchestrator runtime."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ai.cloud import MockCloudAiService
from main import build_application, main
from orchestrator.router import RuleBasedIntentRouter
from orchestrator.state import LifecycleStage, OrchestratorState
from shared.config import AppConfig
from shared.events import EventName
from shared.models import (
    ComponentName,
    Language,
    RouteKind,
    Transcript,
)
from tts.service import MockTtsService
from vision.service import MockVisionService


def test_main_returns_success_code() -> None:
    """The entry point should construct the mock runtime successfully."""

    assert main() == 0


def test_orchestrator_manual_turn_completes_and_returns_to_idle() -> None:
    """A manual input should complete one full end-to-end turn."""

    config = AppConfig()
    config.runtime.manual_inputs = ("open your eyes",)
    service = build_application(config)

    asyncio.run(service.run())

    assert service.state.lifecycle is LifecycleStage.IDLE
    assert service.state.eyes_open is True
    assert service.state.current_response == "Opening my eyes now."
    assert service.tts.spoken_texts == ["Opening my eyes now."]
    assert [event.name for event in service.event_history][-1] == EventName.TTS_FINISHED


def test_router_classifies_local_and_cloud_paths() -> None:
    """The rule-based router should distinguish local actions, queries, local LLM, and chat."""

    router = RuleBasedIntentRouter()
    transcript = Transcript(
        text="who do you see",
        language=Language.ENGLISH,
        confidence=1.0,
        is_final=True,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    context = asyncio.run(build_application()._build_context())

    visible = asyncio.run(router.route(transcript, context))
    local_llm = asyncio.run(
        router.route(
            Transcript(
                text="please use your local brain",
                language=Language.ENGLISH,
                confidence=1.0,
                is_final=True,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
            ),
            context,
        )
    )
    cloud = asyncio.run(
        router.route(
            Transcript(
                text="tell me a joke",
                language=Language.ENGLISH,
                confidence=1.0,
                is_final=True,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
            ),
            context,
        )
    )

    assert visible.kind is RouteKind.LOCAL_QUERY
    assert local_llm.kind is RouteKind.LOCAL_LLM
    assert cloud.kind is RouteKind.CLOUD_CHAT


def test_partial_transcript_updates_state_without_triggering_route() -> None:
    """Partial transcripts should update listening UI without executing a turn."""

    service = build_application()
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
    assert service.state.last_route is None
    assert service.event_history[-1].name is EventName.TRANSCRIPT_PARTIAL


def test_memory_and_vision_context_are_used_for_local_query() -> None:
    """Local queries should answer from mock vision and memory context."""

    service = build_application()

    asyncio.run(
        service.run_turn(
            Transcript(
                text="who do you see",
                language=Language.ENGLISH,
                confidence=1.0,
                is_final=True,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
            )
        )
    )

    assert service.state.current_response == "I can currently see Sebastian."
    assert service.state.active_user_id == "sebastian"
    assert service.memory.records[-1].route_kind is RouteKind.LOCAL_QUERY


def test_cloud_failure_falls_back_to_local_message() -> None:
    """Cloud chat failures should produce a safe fallback response and keep the loop alive."""

    config = AppConfig()
    service = build_application(config)
    service.cloud_ai = MockCloudAiService(fail_on_text="fail")

    asyncio.run(
        service.run_turn(
            Transcript(
                text="please fail the cloud",
                language=Language.ENGLISH,
                confidence=1.0,
                is_final=True,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
            )
        )
    )

    assert "falling back" in service.state.current_response
    assert service.state.lifecycle is LifecycleStage.IDLE
    assert any(event.name is EventName.ERROR_OCCURRED for event in service.event_history)


def test_tts_failure_does_not_break_interaction_persistence() -> None:
    """TTS failures should still preserve the interaction record."""

    service = build_application()
    service.tts = MockTtsService(should_fail=True)

    asyncio.run(
        service.run_turn(
            Transcript(
                text="tell me a joke",
                language=Language.ENGLISH,
                confidence=1.0,
                is_final=True,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
            )
        )
    )

    assert service.memory.records[-1].assistant_text.startswith("Cloud reply:")
    assert service.state.lifecycle is LifecycleStage.IDLE
    assert service.state.last_error == "mock tts failure"


def test_vision_failure_does_not_block_voice_flow() -> None:
    """Vision failures should degrade gracefully while the voice path still completes."""

    service = build_application()
    service.vision = MockVisionService(should_fail=True)

    asyncio.run(
        service.run_turn(
            Transcript(
                text="tell me a joke",
                language=Language.ENGLISH,
                confidence=1.0,
                is_final=True,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
            )
        )
    )

    assert service.state.current_response.startswith("Cloud reply:")
    assert service.state.lifecycle is LifecycleStage.IDLE
    assert any(
        event.name is EventName.ERROR_OCCURRED and event.source is ComponentName.VISION
        for event in service.event_history
    )
