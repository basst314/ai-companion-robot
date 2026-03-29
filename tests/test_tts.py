"""Tests for TTS queueing, style resolution, and interruption behavior."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from shared.config import TtsConfig
from shared.events import Event, EventName
from shared.models import (
    ComponentName,
    Language,
    SpeechJobStatus,
    SpeechQueuePolicy,
    SpeechRequest,
    SpeechStyle,
    SynthesizedAudio,
)
from tts.service import (
    MockSpeechSynthesizer,
    PiperManagedProcess,
    PiperVoiceResolver,
    PlaybackResult,
    QueuedTtsService,
    ResolvedSpeechRequest,
)


@dataclass(slots=True)
class _ImmediatePlaybackSession:
    duration_ms: int

    async def wait(self) -> PlaybackResult:
        return PlaybackResult(duration_ms=self.duration_ms)

    async def interrupt(self) -> PlaybackResult:
        return PlaybackResult(duration_ms=self.duration_ms, interrupted=True)


@dataclass(slots=True)
class _ImmediatePlaybackService:
    spoken_texts: list[str] = field(default_factory=list)

    async def start(self, audio, *, job_id, request, selection):  # type: ignore[no-untyped-def]
        del audio, job_id, selection
        self.spoken_texts.append(request.text)
        return _ImmediatePlaybackSession(duration_ms=max(50, len(request.text) * 10))


@dataclass(slots=True)
class _BlockingPlaybackSession:
    text: str
    started: asyncio.Event
    released: asyncio.Event
    interrupted: bool = False

    async def wait(self) -> PlaybackResult:
        self.started.set()
        await self.released.wait()
        return PlaybackResult(duration_ms=100, interrupted=self.interrupted)

    async def interrupt(self) -> PlaybackResult:
        self.interrupted = True
        self.released.set()
        return PlaybackResult(duration_ms=50, interrupted=True)


@dataclass(slots=True)
class _BlockingPlaybackService:
    sessions: list[_BlockingPlaybackSession] = field(default_factory=list)
    active_started: asyncio.Event = field(default_factory=asyncio.Event)

    async def start(self, audio, *, job_id, request, selection):  # type: ignore[no-untyped-def]
        del audio, job_id, selection
        session = _BlockingPlaybackSession(
            text=request.text,
            started=self.active_started,
            released=asyncio.Event(),
        )
        self.sessions.append(session)
        return session


@dataclass(slots=True)
class _CapturingSynthesizer:
    provider_name: str = "fake"
    selections: list[ResolvedSpeechRequest] = field(default_factory=list)

    async def synthesize(self, request, selection):  # type: ignore[no-untyped-def]
        del request
        self.selections.append(selection)
        return SynthesizedAudio(audio_bytes=b"RIFFfake", voice_id=selection.voice_id, speaker_id=selection.speaker_id)


@dataclass(slots=True)
class _HangingProcess:
    returncode: int | None = None
    terminate_calls: int = 0
    kill_calls: int = 0
    stderr: object | None = None

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9

    async def wait(self) -> int:
        if self.kill_calls:
            return self.returncode or -9
        await asyncio.sleep(3600)
        return 0


@dataclass(slots=True)
class _ProcessManagerSpy:
    ensure_running_calls: int = 0
    shutdown_calls: int = 0

    async def ensure_running(self) -> None:
        self.ensure_running_calls += 1

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


def test_piper_voice_resolver_applies_single_speaker_style_fallback() -> None:
    resolver = PiperVoiceResolver(TtsConfig())

    selection = resolver.resolve(
        SpeechRequest(
            text="tell me a joke",
            language=Language.ENGLISH,
            style_hint=SpeechStyle.PLAYFUL,
        )
    )

    assert selection.voice_id == "en_US-hfc_female-medium"
    assert selection.speaker_id is None
    assert selection.length_scale == 0.96
    assert selection.noise_scale == 0.78
    assert selection.noise_w_scale == 0.88


def test_piper_voice_resolver_uses_expressive_german_pack_when_enabled() -> None:
    config = TtsConfig(expressive_de_enabled=True)
    resolver = PiperVoiceResolver(config)

    selection = resolver.resolve(
        SpeechRequest(
            text="psst",
            language=Language.GERMAN,
            style_hint=SpeechStyle.WHISPER,
        )
    )

    assert selection.voice_id == "de_DE-thorsten_emotional-medium"
    assert selection.speaker_id == 7
    assert selection.speaker_name == "whisper"
    assert selection.length_scale is None


def test_queued_tts_service_processes_append_jobs_in_order() -> None:
    playback = _ImmediatePlaybackService()
    service = QueuedTtsService(
        synthesizer=MockSpeechSynthesizer(),
        playback=playback,
        queue_max=4,
    )

    async def run() -> None:
        first = await service.enqueue(SpeechRequest(text="first", language=Language.ENGLISH))
        second = await service.enqueue(SpeechRequest(text="second", language=Language.ENGLISH))
        first_output = await service.wait_for_job(first.job_id)
        second_output = await service.wait_for_job(second.job_id)
        assert first_output.status is SpeechJobStatus.PLAYBACK_FINISHED
        assert second_output.status is SpeechJobStatus.PLAYBACK_FINISHED
        await service.shutdown()

    asyncio.run(run())

    assert playback.spoken_texts == ["first", "second"]


def test_queued_tts_service_interrupt_and_replace_stops_current_job() -> None:
    synthesizer = _CapturingSynthesizer()
    playback = _BlockingPlaybackService()
    events: list[Event] = []
    service = QueuedTtsService(
        synthesizer=synthesizer,
        playback=playback,
        queue_max=4,
    )

    async def record_event(event: Event) -> None:
        events.append(event)

    service.bind_event_handler(record_event)

    async def run() -> None:
        first = await service.enqueue(SpeechRequest(text="first", language=Language.ENGLISH))
        await asyncio.wait_for(playback.active_started.wait(), timeout=1.0)
        second = await service.enqueue(
            SpeechRequest(
                text="replacement",
                language=Language.ENGLISH,
                policy=SpeechQueuePolicy.INTERRUPT_AND_REPLACE,
                style_hint=SpeechStyle.SERIOUS,
            )
        )

        first_output = await service.wait_for_job(first.job_id)
        assert first_output.status is SpeechJobStatus.INTERRUPTED

        playback.sessions[-1].released.set()
        second_output = await service.wait_for_job(second.job_id)
        assert second_output.status is SpeechJobStatus.PLAYBACK_FINISHED
        await service.shutdown()

    asyncio.run(run())

    assert playback.sessions[0].interrupted is True
    assert synthesizer.selections[-1].style_hint is SpeechStyle.SERIOUS
    event_names = [event.name for event in events]
    assert EventName.TTS_INTERRUPTED in event_names
    assert EventName.TTS_PLAYBACK_STARTED in event_names
    assert EventName.TTS_PLAYBACK_FINISHED in event_names
    assert events[-1].name is EventName.AUDIO_FINISHED


def test_queued_tts_service_marks_queue_overflow_as_failed() -> None:
    service = QueuedTtsService(
        synthesizer=MockSpeechSynthesizer(),
        playback=_BlockingPlaybackService(),
        queue_max=1,
    )

    async def run() -> None:
        first = await service.enqueue(SpeechRequest(text="first", language=Language.ENGLISH))
        second = await service.enqueue(SpeechRequest(text="second", language=Language.ENGLISH))
        third = await service.enqueue(SpeechRequest(text="third", language=Language.ENGLISH))
        third_output = await service.wait_for_job(third.job_id)
        assert third_output.status is SpeechJobStatus.FAILED
        assert third_output.acknowledged is False
        await service.shutdown()
        assert first.job_id != second.job_id

    asyncio.run(run())


def test_queued_tts_service_start_prewarms_process_manager() -> None:
    process_manager = _ProcessManagerSpy()
    service = QueuedTtsService(
        synthesizer=MockSpeechSynthesizer(),
        playback=_ImmediatePlaybackService(),
        process_manager=process_manager,  # type: ignore[arg-type]
        queue_max=4,
    )

    async def run() -> None:
        await service.start()
        await service.shutdown()

    asyncio.run(run())

    assert process_manager.ensure_running_calls == 1
    assert process_manager.shutdown_calls == 1


def test_piper_managed_process_shutdown_kills_stuck_process() -> None:
    process = _HangingProcess()
    managed = PiperManagedProcess(
        base_url="http://127.0.0.1:5001",
        data_dir=Path("artifacts/piper-voices"),
        default_voice="en_US-hfc_female-medium",
        shutdown_timeout_seconds=0.01,
    )
    managed.process = process  # type: ignore[assignment]

    async def run() -> None:
        await managed.shutdown()

    asyncio.run(run())

    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert managed.process is None
