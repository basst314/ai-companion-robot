"""Integration-style tests for the hybrid orchestrator runtime."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai.cloud import CloudReplyResult, MockCloudResponseService
from main import build_application, main
from orchestrator.capabilities import build_default_capability_registry
from orchestrator.router import LocalShortcutPlanner, LocalTurnDirector
from orchestrator.state import LifecycleStage
from shared.config import AppConfig
from shared.events import Event, EventName
from shared.models import (
    ComponentName,
    Language,
    PlanStep,
    RouteKind,
    SpeechJobStatus,
    SpeechOutput,
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


class ScriptedSpeechLoopSttService:
    """Return deterministic final transcripts and record explicit utterance triggers."""

    def __init__(self, transcripts: list[str]) -> None:
        self._transcripts = transcripts
        self.stream_calls = 0
        self.begin_triggers: list[str] = []

    def begin_utterance(self, *, trigger: str, detection=None) -> None:  # type: ignore[no-untyped-def]
        del detection
        self.begin_triggers.append(trigger)

    async def stream_transcripts(self):  # type: ignore[no-untyped-def]
        if self.stream_calls >= len(self._transcripts):
            raise RuntimeError("mock STT has no remaining utterances configured")
        transcript_text = self._transcripts[self.stream_calls]
        self.stream_calls += 1
        yield Transcript(
            text=transcript_text,
            language=Language.ENGLISH,
            confidence=1.0,
            is_final=True,
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
        )


class SilentReplyCloudService:
    """Return a valid text response that should not be spoken."""

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
        del transcript, context, plan, step_results, previous_response_id, tool_handler
        from shared.models import AiResponse

        return CloudReplyResult(
            response=AiResponse(
                text="Quiet response",
                language=Language.ENGLISH,
                should_speak=False,
            )
        )


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


class BlockingWakeWordService:
    """Never detect a wake phrase within the test timeout."""

    async def wait_for_wake_word(self) -> WakeDetectionResult:
        await asyncio.sleep(60)
        return WakeDetectionResult(detected=False)


class StartupSpyTtsService:
    """Record whether orchestrator startup tries to prewarm TTS."""

    def __init__(self) -> None:
        self.start_calls = 0
        self.shutdown_calls = 0

    async def start(self) -> None:
        self.start_calls += 1

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


class ScriptedLifecycleTtsService:
    """Emit a controlled TTS event sequence for lifecycle assertions."""

    def __init__(self, lifecycle_probe) -> None:  # type: ignore[no-untyped-def]
        self.lifecycle_probe = lifecycle_probe
        self._event_handler = None
        self.lifecycle_after_synthesis = None
        self.lifecycle_after_playback_started = None

    def bind_event_handler(self, handler) -> None:  # type: ignore[no-untyped-def]
        self._event_handler = handler

    async def speak(self, request):  # type: ignore[no-untyped-def]
        assert self._event_handler is not None
        payload = {
            "job_id": "scripted-job",
            "text": request.text,
            "language": request.language.value,
            "style": request.style_hint.value,
            "voice_id": None,
            "speaker_id": None,
            "emitted_at_monotonic_ms": 1,
        }
        await self._event_handler(Event(EventName.TTS_ENQUEUED, ComponentName.TTS, payload))
        await self._event_handler(Event(EventName.TTS_SYNTHESIS_STARTED, ComponentName.TTS, payload))
        self.lifecycle_after_synthesis = self.lifecycle_probe()
        await self._event_handler(Event(EventName.TTS_SYNTHESIS_FINISHED, ComponentName.TTS, payload))
        await self._event_handler(Event(EventName.TTS_PLAYBACK_STARTED, ComponentName.TTS, payload))
        self.lifecycle_after_playback_started = self.lifecycle_probe()
        finish_payload = dict(payload)
        finish_payload["playback_duration_ms"] = 120
        await self._event_handler(Event(EventName.TTS_PLAYBACK_FINISHED, ComponentName.TTS, finish_payload))
        await self._event_handler(Event(EventName.TTS_FINISHED, ComponentName.TTS, finish_payload))
        await self._event_handler(Event(EventName.AUDIO_FINISHED, ComponentName.TTS, finish_payload))
        return SpeechOutput(
            text=request.text,
            acknowledged=True,
            duration_ms=120,
            job_id="scripted-job",
            provider_name="scripted",
            language=request.language,
            status=SpeechJobStatus.PLAYBACK_FINISHED,
        )

    async def shutdown(self) -> None:
        return None


def test_main_returns_success_code() -> None:
    """The entry point should construct the hybrid runtime successfully."""

    assert main(AppConfig()) == 0


def test_orchestrator_start_skips_tts_prewarm_when_tts_is_mock() -> None:
    config = AppConfig()
    service = build_application(config)
    startup_spy = StartupSpyTtsService()
    service.tts = startup_spy  # type: ignore[assignment]

    async def run() -> None:
        await service.start()
        await service.stop()

    asyncio.run(run())

    assert startup_spy.start_calls == 0
    assert startup_spy.shutdown_calls == 1


def test_orchestrator_start_prewarms_tts_when_backend_is_enabled() -> None:
    config = AppConfig()
    service = build_application(config)
    service.config.tts.backend = "piper"
    startup_spy = StartupSpyTtsService()
    service.tts = startup_spy  # type: ignore[assignment]

    async def run() -> None:
        await service.start()
        await service.stop()

    asyncio.run(run())

    assert startup_spy.start_calls == 1
    assert startup_spy.shutdown_calls == 1


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
    config.runtime.follow_up_mode_enabled = False
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
    config.runtime.follow_up_mode_enabled = False
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


def test_interactive_speech_console_chains_follow_up_without_second_wake_word(monkeypatch) -> None:
    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.interactive_console = True
    config.runtime.follow_up_mode_enabled = True
    service = build_application(config)
    service.stt = ScriptedSpeechLoopSttService(["look at me", "tell me a joke", ""])

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

    monkeypatch.setattr("orchestrator.service._read_console_line_ready", lambda _timeout: "exit")

    asyncio.run(service.run())

    assert service.wake_word.calls == 2
    assert service.stt.stream_calls == 3
    assert service.stt.begin_triggers == ["wake", "follow_up", "follow_up"]


def test_enter_started_turn_does_not_strip_mid_sentence_wake_word(monkeypatch) -> None:
    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.interactive_console = True
    config.runtime.follow_up_mode_enabled = False
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
    event_names = [event.name for event in service.event_history]
    assert EventName.TTS_PLAYBACK_STARTED in event_names
    assert EventName.TTS_PLAYBACK_FINISHED in event_names
    assert event_names[-1] == EventName.AUDIO_FINISHED


def test_orchestrator_enters_speaking_only_after_tts_playback_starts() -> None:
    config = AppConfig()
    config.runtime.manual_inputs = ("tell me a joke",)
    service = build_application(config)
    scripted_tts = ScriptedLifecycleTtsService(lambda: service.state.lifecycle)
    service.tts = scripted_tts  # type: ignore[assignment]
    scripted_tts.bind_event_handler(service.handle_event)

    asyncio.run(service.run())

    assert scripted_tts.lifecycle_after_synthesis is LifecycleStage.RESPONDING
    assert scripted_tts.lifecycle_after_playback_started is LifecycleStage.SPEAKING
    rendered_lifecycles = [lifecycle for lifecycle, _emotion, _preview in service.ui.rendered_states]
    assert rendered_lifecycles.index("responding") < rendered_lifecycles.index("speaking")


def test_speech_mode_follow_up_turn_runs_without_second_wake_word() -> None:
    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.stt_backend = "mock"
    config.runtime.follow_up_mode_enabled = True
    config.runtime.manual_inputs = ("look at me", "tell me a joke")
    service = build_application(config)
    service.wake_word = FakeWakeWordService(WakeDetectionResult(detected=True))
    service.stt = ScriptedSpeechLoopSttService(["look at me", "tell me a joke"])

    asyncio.run(service.run())

    assert service.wake_word.calls == 1
    assert service.stt.stream_calls == 2
    assert service.stt.begin_triggers == ["wake", "follow_up"]
    assert len(service.memory.records) == 2
    assert service.memory.records[0].user_text == "look at me"
    assert service.memory.records[1].user_text == "tell me a joke"


def test_speech_mode_follow_up_chains_until_user_is_silent() -> None:
    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.stt_backend = "mock"
    config.runtime.follow_up_mode_enabled = True
    config.runtime.manual_inputs = ("look at me", "tell me a joke", "")
    service = build_application(config)
    service.wake_word = FakeWakeWordService(WakeDetectionResult(detected=True))
    service.stt = ScriptedSpeechLoopSttService(["look at me", "tell me a joke", ""])

    asyncio.run(service.run())

    assert service.wake_word.calls == 1
    assert service.stt.stream_calls == 3
    assert service.stt.begin_triggers == ["wake", "follow_up", "follow_up"]
    assert len(service.memory.records) == 2
    assert service.state.current_transcript is not None
    assert service.state.current_transcript.text == ""
    assert service.state.lifecycle is LifecycleStage.IDLE


def test_speech_mode_follow_up_stops_when_reply_is_not_spoken() -> None:
    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.stt_backend = "mock"
    config.runtime.follow_up_mode_enabled = True
    config.runtime.manual_inputs = ("tell me a joke", "look at me")
    service = build_application(config)
    service.wake_word = FakeWakeWordService(WakeDetectionResult(detected=True))
    service.stt = ScriptedSpeechLoopSttService(["tell me a joke", "look at me"])
    service.cloud_response = SilentReplyCloudService()

    asyncio.run(service.run())

    assert service.wake_word.calls == 2
    assert service.stt.stream_calls == 2
    assert service.stt.begin_triggers == ["wake", "wake"]
    assert service.tts.spoken_texts == ["I am looking at you now."]


def test_speech_mode_follow_up_stops_when_tts_fails() -> None:
    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.stt_backend = "mock"
    config.runtime.follow_up_mode_enabled = True
    config.runtime.manual_inputs = ("look at me", "tell me a joke")
    service = build_application(config)
    service.wake_word = FakeWakeWordService(WakeDetectionResult(detected=True))
    service.stt = ScriptedSpeechLoopSttService(["look at me", "tell me a joke"])
    failing_tts = MockTtsService(should_fail=True)
    failing_tts.bind_event_handler(service.handle_event)
    service.tts = failing_tts

    asyncio.run(service.run())

    assert service.wake_word.calls == 2
    assert service.stt.stream_calls == 2
    assert service.stt.begin_triggers == ["wake", "wake"]
    assert service.state.last_error == "mock tts failure"


def test_speech_mode_follow_up_stops_when_feature_flag_disabled() -> None:
    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.stt_backend = "mock"
    config.runtime.follow_up_mode_enabled = False
    config.runtime.manual_inputs = ("look at me", "tell me a joke")
    service = build_application(config)
    service.wake_word = FakeWakeWordService(WakeDetectionResult(detected=True))
    service.stt = ScriptedSpeechLoopSttService(["look at me", "tell me a joke"])

    asyncio.run(service.run())

    assert service.wake_word.calls == 2
    assert service.stt.stream_calls == 2
    assert service.stt.begin_triggers == ["wake", "wake"]


def test_speech_mode_follow_up_stops_after_configured_max_turns() -> None:
    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.stt_backend = "mock"
    config.runtime.follow_up_mode_enabled = True
    config.runtime.follow_up_max_turns = 2
    config.runtime.manual_inputs = ("look at me", "tell me a joke", "who do you see", "turn your head left")
    service = build_application(config)
    service.wake_word = FakeWakeWordService(WakeDetectionResult(detected=True))
    service.stt = ScriptedSpeechLoopSttService(["look at me", "tell me a joke", "who do you see", "turn your head left"])

    asyncio.run(service.run())

    assert service.wake_word.calls == 2
    assert service.stt.stream_calls == 4
    assert service.stt.begin_triggers == ["wake", "follow_up", "follow_up", "wake"]


def test_turn_director_chooses_local_cloud_and_hybrid_paths() -> None:
    """The local-first director should distinguish the main path shapes."""

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
    director = LocalTurnDirector()

    visible = asyncio.run(shortcut.plan(transcript, context, ()))
    cloud = asyncio.run(
        director.direct_turn(
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
        director.direct_turn(
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


def test_local_only_turn_does_not_call_cloud() -> None:
    class FailingCloudResponse:
        async def generate_reply(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("cloud reply should not run for explicit local-only turns")

    service = build_application(AppConfig())
    service.cloud_response = FailingCloudResponse()

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
    assert service.memory.records[-1].route_kind is RouteKind.LOCAL_QUERY


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


def test_camera_tool_turn_speaks_ack_then_final_reply() -> None:
    service = build_application(AppConfig())

    asyncio.run(
        service.run_turn(
            Transcript(
                text="what do you see here",
                language=Language.ENGLISH,
                confidence=1.0,
                is_final=True,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
            )
        )
    )

    assert service.tts.spoken_texts == [
        "Let me take a look.",
        "Cloud reply: I took a look. Mock camera snapshot with Sebastian.",
    ]
    assert service.state.current_response == "Cloud reply: I took a look. Mock camera snapshot with Sebastian."
    assert service.memory.records[-1].route_kind is RouteKind.CLOUD_CHAT


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


def test_cloud_turn_stores_and_reuses_previous_response_id_within_resume_window() -> None:
    service = build_application(AppConfig())
    service.cloud_response = CapturingCloudResponseService(["resp_1", "resp_2"])

    first = Transcript(
        text="tell me a joke",
        language=Language.ENGLISH,
        confidence=1.0,
        is_final=True,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    second = Transcript(
        text="and another one",
        language=Language.ENGLISH,
        confidence=1.0,
        is_final=True,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )

    async def run_sequence() -> None:
        await service.run_turn(first)
        await service.run_turn(second)

    asyncio.run(run_sequence())

    assert service.cloud_response.previous_response_ids == [None, "resp_1"]
    assert service.state.last_openai_response_id == "resp_2"
    assert service.state.last_openai_response_at is not None


def test_cloud_turn_does_not_reuse_expired_previous_response_id() -> None:
    service = build_application(AppConfig())
    service.cloud_response = CapturingCloudResponseService(["resp_fresh"])
    service.state.last_openai_response_id = "resp_old"
    service.state.last_openai_response_at = datetime.now(UTC) - timedelta(minutes=6)

    async def run_once() -> None:
        await service.run_turn(
            Transcript(
                text="tell me a joke",
                language=Language.ENGLISH,
                confidence=1.0,
                is_final=True,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
            )
        )

    asyncio.run(run_once())

    assert service.cloud_response.previous_response_ids == [None]
    assert service.state.last_openai_response_id == "resp_fresh"


def test_local_only_turn_does_not_create_or_refresh_openai_resume_state() -> None:
    service = build_application(AppConfig())
    service.state.last_openai_response_id = "resp_keep"
    original_timestamp = datetime.now(UTC) - timedelta(minutes=1)
    service.state.last_openai_response_at = original_timestamp

    async def run_once() -> None:
        await service.run_turn(
            Transcript(
                text="who do you see",
                language=Language.ENGLISH,
                confidence=1.0,
                is_final=True,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
            )
        )

    asyncio.run(run_once())

    assert service.state.last_openai_response_id == "resp_keep"
    assert service.state.last_openai_response_at == original_timestamp


def test_cloud_failure_does_not_overwrite_existing_openai_resume_state() -> None:
    service = build_application(AppConfig())
    existing_timestamp = datetime.now(UTC) - timedelta(minutes=1)
    service.state.last_openai_response_id = "resp_keep"
    service.state.last_openai_response_at = existing_timestamp
    service.cloud_response = CapturingCloudResponseService(["resp_new"], fail_on_call=1)

    async def run_once() -> None:
        await service.run_turn(
            Transcript(
                text="tell me a joke",
                language=Language.ENGLISH,
                confidence=1.0,
                is_final=True,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
            )
        )

    asyncio.run(run_once())

    assert service.state.last_openai_response_id == "resp_keep"
    assert service.state.last_openai_response_at == existing_timestamp


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
