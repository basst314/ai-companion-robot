"""Integration-style tests for the mock orchestrator runtime."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

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
            text="open your",
            language=Language.ENGLISH,
            confidence=0.9,
            is_final=False,
            started_at=datetime.now(UTC),
        )
        yield Transcript(
            text="open your eyes",
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
    """The entry point should construct the mock runtime successfully."""

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

    entries = iter(["open your eyes", "exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(entries))
    service.stt = FailingInteractiveSttService()

    asyncio.run(service.run())

    assert service.state.lifecycle is LifecycleStage.IDLE
    assert service.state.eyes_open is True
    assert service.state.current_response == "Opening my eyes now."
    assert any(event.name is EventName.TRANSCRIPT_FINAL for event in service.event_history)


def test_interactive_speech_console_shows_incremental_transcript(monkeypatch, capsys) -> None:
    """Speech mode should show growing transcript text before routing the final turn."""

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
    assert "open your eyes" in captured
    assert "[UI] lifecycle=" not in captured
    assert "[ROUTE]" in captured
    assert service.state.current_response == "Opening my eyes now."


def test_interactive_speech_console_accepts_wake_word_without_enter(monkeypatch) -> None:
    """Interactive speech mode should allow wake-word activation while input is still pending."""

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

    responses = iter(["exit"])

    def delayed_input(_prompt: str) -> str:
        time.sleep(0.05)
        return next(responses, "exit")

    monkeypatch.setattr("builtins.input", delayed_input)

    asyncio.run(service.run())

    assert service.state.current_response == "Opening my eyes now."
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


def test_speech_mode_strips_wake_word_before_routing_and_memory() -> None:
    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.stt_backend = "mock"
    config.runtime.manual_inputs = ("Oreo open your eyes",)
    service = build_application(config)
    service.config.runtime.wake_word_enabled = True
    service.config.runtime.wake_word_phrase = "Oreo"
    service.wake_word = FakeWakeWordService(WakeDetectionResult(detected=True))

    asyncio.run(service.run())

    assert service.state.current_transcript is not None
    assert service.state.current_transcript.text == "open your eyes"
    assert service.memory.records[-1].user_text == "open your eyes"
    assert service.state.current_response == "Opening my eyes now."


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
    context = asyncio.run(build_application(AppConfig())._build_context())

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
    assert service.state.last_route is None
    assert service.event_history[-1].name is EventName.TRANSCRIPT_PARTIAL


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
