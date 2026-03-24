"""Integration-style tests for the hybrid orchestrator runtime."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from ai.cloud import MockCloudPlanningService, MockCloudResponseService
from main import build_application, main
from orchestrator.capabilities import build_default_capability_registry
from orchestrator.router import HybridTurnPlanner, LocalShortcutPlanner
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
from stt.service import AudioWindow, WakeDetectionResult
from tts.service import MockTtsService
from vision.service import MockVisionService


class FailingInteractiveSttService:
    """Raise if the interactive console incorrectly falls back to STT."""

    async def listen_once(self):  # type: ignore[no-untyped-def]
        raise AssertionError("STT should not run when a typed utterance is provided")

    async def stream_transcripts(self):  # type: ignore[no-untyped-def]
        if False:
            yield None


class StreamingInteractiveSttService:
    """Emit partial and final transcript updates for console tests."""

    async def listen_once(self):  # type: ignore[no-untyped-def]
        raise AssertionError("interactive speech loop should use stream_transcripts")

    async def stream_transcripts(self):  # type: ignore[no-untyped-def]
        yield Transcript(
            text="look at",
            language=Language.ENGLISH,
            confidence=0.9,
            is_final=False,
            started_at=datetime.now(UTC),
        )
        yield Transcript(
            text="look at me",
            language=Language.ENGLISH,
            confidence=1.0,
            is_final=True,
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
        )


class MidSentenceWakeWordSttService:
    """Emit a transcript containing the wake phrase mid-sentence."""

    async def listen_once(self):  # type: ignore[no-untyped-def]
        raise AssertionError("interactive speech loop should use stream_transcripts")

    async def stream_transcripts(self):  # type: ignore[no-untyped-def]
        yield Transcript(
            text="one two three hello wow are you",
            language=Language.ENGLISH,
            confidence=1.0,
            is_final=True,
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
        )


class FakeWakeWordService:
    """Return a deterministic wake hit for speech-loop tests."""

    def __init__(self, result: WakeDetectionResult) -> None:
        self.result = result
        self.calls = 0

    async def wait_for_wake_word(self) -> WakeDetectionResult:
        self.calls += 1
        return self.result


class BlockingWakeWordService:
    """Never detect a wake phrase within the test timeout."""

    async def wait_for_wake_word(self) -> WakeDetectionResult:
        await asyncio.sleep(60)
        return WakeDetectionResult(detected=False)


def test_main_returns_success_code() -> None:
    """The entry point should construct the hybrid runtime successfully."""

    assert main(AppConfig()) == 0


def test_interactive_console_handles_eof_cleanly(monkeypatch) -> None:
    """Interactive mode should exit gracefully when stdin closes."""

    config = AppConfig()
    config.runtime.interactive_console = True
    service = build_application(config)

    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError()))

    asyncio.run(service.run())

    assert service.state.lifecycle is LifecycleStage.IDLE
    assert service.state.last_error is None


def test_interactive_speech_console_accepts_typed_phrase(monkeypatch) -> None:
    """Interactive speech mode should accept a typed utterance without recording audio."""

    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.interactive_console = True
    service = build_application(config)

    entries = iter(["look at me", "exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(entries))
    service.stt = FailingInteractiveSttService()

    asyncio.run(service.run())

    assert service.state.lifecycle is LifecycleStage.IDLE
    assert service.state.head_direction == "user"
    assert service.state.current_response == "I am looking at you now."
    assert any(event.name is EventName.TRANSCRIPT_FINAL for event in service.event_history)


def test_interactive_speech_console_shows_incremental_transcript(monkeypatch, capsys) -> None:
    """Speech mode should show growing transcript text before executing the final turn."""

    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.interactive_console = True
    service = build_application(config)

    entries = iter(["", "exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(entries))
    service.stt = StreamingInteractiveSttService()

    asyncio.run(service.run())

    captured = capsys.readouterr().out
    assert captured.count("[CTRL]") >= 2
    assert "Listening [en]:" in captured
    assert "Final transcript [en]:" in captured
    assert "look at me" in captured
    assert "[ROUTE]" in captured
    assert service.state.current_response == "I am looking at you now."


def test_interactive_speech_console_prioritizes_wake_word_when_input_and_wake_finish_together(
    monkeypatch,
) -> None:
    """Wake-word activation should win ties with pending non-TTY input completion."""

    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.interactive_console = True
    service = build_application(config)
    service.stt = StreamingInteractiveSttService()

    class OneShotWakeWordService:
        def __init__(self) -> None:
            self.calls = 0

        async def wait_for_wake_word(self) -> WakeDetectionResult:
            self.calls += 1
            if self.calls == 1:
                return WakeDetectionResult(detected=True)
            await asyncio.sleep(60)
            return WakeDetectionResult(detected=False)

    service.wake_word = OneShotWakeWordService()

    monkeypatch.setattr("builtins.input", lambda _prompt: "exit")

    asyncio.run(service.run())

    assert service.state.current_response == "I am looking at you now."
    assert service.wake_word.calls >= 1


def test_enter_started_turn_does_not_strip_mid_sentence_wake_word(monkeypatch) -> None:
    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.interactive_console = True
    service = build_application(config)
    service.config.runtime.wake_word_enabled = True
    service.config.runtime.wake_word_phrase = "Hello"
    service.stt = MidSentenceWakeWordSttService()
    service.wake_word = BlockingWakeWordService()

    entries = iter(["", "exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(entries))

    asyncio.run(service.run())

    assert service.state.current_transcript is not None
    assert service.state.current_transcript.text == "one two three hello wow are you"


def test_speech_mode_waits_for_wake_word_before_processing() -> None:
    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.stt_backend = "mock"
    service = build_application(config)
    service.config.runtime.wake_word_enabled = True
    service.config.runtime.wake_word_phrase = "Oreo"
    service.wake_word = BlockingWakeWordService()

    try:
        asyncio.run(asyncio.wait_for(service.run(), timeout=0.01))
    except TimeoutError:
        pass
    else:
        raise AssertionError("expected wake-word gated speech loop to remain idle while waiting for wake")

    assert service.state.lifecycle is LifecycleStage.IDLE
    assert service.memory.records == []


def test_speech_mode_strips_wake_word_before_planning_and_memory() -> None:
    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.stt_backend = "mock"
    config.runtime.manual_inputs = ("Oreo look at me",)
    service = build_application(config)
    service.config.runtime.wake_word_enabled = True
    service.config.runtime.wake_word_phrase = "Oreo"
    service.wake_word = FakeWakeWordService(WakeDetectionResult(detected=True))

    asyncio.run(service.run())

    assert service.state.current_transcript is not None
    assert service.state.current_transcript.text == "look at me"
    assert service.memory.records[-1].user_text == "look at me"
    assert service.state.current_response == "I am looking at you now."


def test_speech_mode_preserves_words_after_wake_boundary() -> None:
    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.stt_backend = "mock"
    config.runtime.manual_inputs = ("Oreo look at me and tell me a joke",)
    service = build_application(config)
    service.config.runtime.wake_word_enabled = True
    service.config.runtime.wake_word_phrase = "Oreo"
    service.wake_word = FakeWakeWordService(
        WakeDetectionResult(
            detected=True,
            audio_window=AudioWindow(
                source_path=Path("/tmp/wake.wav"),
                channels=1,
                sample_width=2,
                sample_rate=16000,
                pcm_data=b"\x00\x00" * 16000,
                duration_seconds=0.5,
                trailing_silence_seconds=0.0,
                has_speech=True,
                current_energy=120.0,
                peak_energy=200.0,
            ),
        )
    )

    asyncio.run(service.run())

    assert service.state.current_transcript is not None
    assert service.state.current_transcript.text == "look at me and tell me a joke"
    assert service.memory.records[-1].user_text == "look at me and tell me a joke"
    assert service.memory.records[-1].route_kind is RouteKind.HYBRID


def test_speech_mode_strips_only_first_wake_phrase_when_repeated() -> None:
    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.stt_backend = "mock"
    config.runtime.manual_inputs = ("Hello one two Hello wow are you",)
    service = build_application(config)
    service.config.runtime.wake_word_enabled = True
    service.config.runtime.wake_word_phrase = "Hello"
    service.wake_word = FakeWakeWordService(WakeDetectionResult(detected=True))

    asyncio.run(service.run())

    assert service.state.current_transcript is not None
    assert service.state.current_transcript.text == "one two Hello wow are you"
    assert service.memory.records[-1].user_text == "one two Hello wow are you"


def test_orchestrator_manual_turn_completes_and_returns_to_idle() -> None:
    """A manual input should complete one full end-to-end turn."""

    config = AppConfig()
    config.runtime.manual_inputs = ("look at me",)
    service = build_application(config)

    asyncio.run(service.run())

    assert service.state.lifecycle is LifecycleStage.IDLE
    assert service.state.head_direction == "user"
    assert service.state.current_response == "I am looking at you now."
    assert service.tts.spoken_texts == ["I am looking at you now."]
    assert [event.name for event in service.event_history][-1] == EventName.TTS_FINISHED


def test_planners_choose_local_cloud_and_hybrid_paths() -> None:
    """The shortcut and hybrid planners should distinguish the main path shapes."""

    transcript = Transcript(
        text="who do you see",
        language=Language.ENGLISH,
        confidence=1.0,
        is_final=True,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    context = asyncio.run(build_application(AppConfig())._build_context())
    shortcut = LocalShortcutPlanner()
    hybrid = HybridTurnPlanner(cloud_planner=MockCloudPlanningService())

    visible = asyncio.run(shortcut.plan(transcript, context, ()))
    cloud = asyncio.run(
        hybrid.plan(
            Transcript(
                text="tell me a joke",
                language=Language.ENGLISH,
                confidence=1.0,
                is_final=True,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
            ),
            context,
            (),
        )
    )
    mixed = asyncio.run(
        hybrid.plan(
            Transcript(
                text="look at me and tell me a joke",
                language=Language.ENGLISH,
                confidence=1.0,
                is_final=True,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
            ),
            context,
            (),
        )
    )

    assert visible is not None
    assert visible.route_kind is RouteKind.LOCAL_QUERY
    assert cloud.route_kind is RouteKind.CLOUD_CHAT
    assert mixed.route_kind is RouteKind.HYBRID


def test_partial_transcript_updates_state_without_triggering_plan() -> None:
    """Partial transcripts should update listening UI without executing a turn."""

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
    assert service.event_history[-1].name is EventName.STEP_FINISHED


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
    """Local queries should answer from mock vision and memory context."""

    service = build_application(AppConfig())

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
    assert service.memory.records[-1].executed_steps == ("visible_people",)


def test_hybrid_turn_executes_local_action_and_cloud_reply() -> None:
    service = build_application(AppConfig())

    asyncio.run(
        service.run_turn(
            Transcript(
                text="look at me and tell me a joke",
                language=Language.ENGLISH,
                confidence=1.0,
                is_final=True,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
            )
        )
    )

    assert service.state.head_direction == "user"
    assert service.state.current_response.startswith("Cloud reply:")
    assert service.state.last_plan is not None
    assert service.state.last_plan.route_kind is RouteKind.HYBRID
    assert service.memory.records[-1].executed_steps[-2:] == ("look_at_user", "cloud_reply")


def test_cloud_failure_falls_back_to_local_message() -> None:
    """Cloud chat failures should produce a safe fallback response and keep the loop alive."""

    config = AppConfig()
    service = build_application(config)
    service.cloud_response = MockCloudResponseService(fail_on_text="fail")

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


def test_reactive_step_happens_before_cloud_completion() -> None:
    service = build_application(AppConfig())

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

    step_finished_index = next(
        index
        for index, event in enumerate(service.event_history)
        if event.name is EventName.STEP_FINISHED and event.payload.get("result").capability_id == "set_emotion"
    )
    response_ready_index = next(
        index
        for index, event in enumerate(service.event_history)
        if event.name is EventName.RESPONSE_READY
    )

    assert step_finished_index < response_ready_index


def test_tts_failure_does_not_break_interaction_persistence() -> None:
    """TTS failures should still preserve the interaction record."""

    service = build_application(AppConfig())
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

    service = build_application(AppConfig())
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
