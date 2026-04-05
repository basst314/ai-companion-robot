"""Tests for TTS queueing, style resolution, and interruption behavior."""

from __future__ import annotations

import asyncio
import io
import struct
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
import pytest

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
    AlsaPersistentAudioPlaybackService,
    MockSpeechSynthesizer,
    PiperManagedProcess,
    PiperVoiceResolver,
    PlaybackResult,
    PersistentAplayAudioPlaybackService,
    QueuedTtsService,
    ResolvedSpeechRequest,
    build_piper_tts_service,
    _build_aplay_stream_command,
    _extract_raw_pcm_audio,
    _prepare_wav_bytes_for_playback,
)


@dataclass(slots=True)
class _ImmediatePlaybackSession:
    duration_ms: int

    async def wait_started(self) -> bool:
        return True

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

    async def shutdown(self) -> None:
        return None

    async def prewarm(self) -> None:
        return None


@dataclass(slots=True)
class _PrewarmingPlaybackService(_ImmediatePlaybackService):
    prewarm_calls: int = 0

    async def prewarm(self) -> None:
        self.prewarm_calls += 1


@dataclass(slots=True)
class _BlockingPlaybackSession:
    text: str
    started: asyncio.Event
    released: asyncio.Event
    interrupted: bool = False

    async def wait_started(self) -> bool:
        self.started.set()
        return True

    async def wait(self) -> PlaybackResult:
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

    async def shutdown(self) -> None:
        return None

    async def prewarm(self) -> None:
        return None


@dataclass(slots=True)
class _DelayedStartPlaybackSession:
    started_signal: asyncio.Event
    released: asyncio.Event

    async def wait_started(self) -> bool:
        await self.started_signal.wait()
        return True

    async def wait(self) -> PlaybackResult:
        await self.released.wait()
        return PlaybackResult(duration_ms=100)

    async def interrupt(self) -> PlaybackResult:
        self.released.set()
        return PlaybackResult(duration_ms=50, interrupted=True)


@dataclass(slots=True)
class _DelayedStartPlaybackService:
    started_signal: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)

    async def start(self, audio, *, job_id, request, selection):  # type: ignore[no-untyped-def]
        del audio, job_id, request, selection
        return _DelayedStartPlaybackSession(
            started_signal=self.started_signal,
            released=self.released,
        )

    async def shutdown(self) -> None:
        return None

    async def prewarm(self) -> None:
        return None


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


def test_prepare_wav_bytes_for_playback_adds_lead_in_and_tail_silence() -> None:
    sample_rate = 16000
    source_wav = _build_test_wav([4000] * 160, sample_rate=sample_rate)

    prepared_wav = _prepare_wav_bytes_for_playback(source_wav)

    with wave.open(io.BytesIO(prepared_wav), "rb") as wav_file:
        assert wav_file.getframerate() == sample_rate
        frames = wav_file.readframes(wav_file.getnframes())

    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    assert len(samples) > 160
    assert all(sample == 0 for sample in samples[: sample_rate * 120 // 1000])
    assert all(sample == 0 for sample in samples[-(sample_rate * 40 // 1000) :])


def test_prepare_wav_bytes_for_playback_fades_final_samples() -> None:
    source_wav = _build_test_wav([6000] * 800, sample_rate=16000)

    prepared_wav = _prepare_wav_bytes_for_playback(source_wav)

    with wave.open(io.BytesIO(prepared_wav), "rb") as wav_file:
        frames = wav_file.readframes(wav_file.getnframes())

    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    trimmed_samples = samples[16000 * 120 // 1000 : -(16000 * 40 // 1000)]
    assert trimmed_samples[-1] == 0
    assert abs(trimmed_samples[-2]) <= abs(trimmed_samples[-20])


def test_build_aplay_stream_command_rewrites_file_playback_to_stdin() -> None:
    audio = _extract_raw_pcm_audio(_build_test_wav([500] * 32, sample_rate=16000))

    assert audio is not None
    assert _build_aplay_stream_command(("aplay", "-D", "default:CARD=vc4hdmi1", "{input_path}"), audio) == (
        "aplay",
        "-D",
        "default:CARD=vc4hdmi1",
        "-q",
        "-t",
        "raw",
        "-f",
        "S16_LE",
        "-r",
        "16000",
        "-c",
        "1",
        "-",
    )


def test_persistent_aplay_service_reuses_running_process(monkeypatch: pytest.MonkeyPatch) -> None:
    starts: list[tuple[str, ...]] = []
    payloads: list[bytes] = []

    class _FakeStdin:
        def __init__(self) -> None:
            self.closed = False

        def write(self, data: bytes) -> None:
            payloads.append(data)

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

        def is_closing(self) -> bool:
            return self.closed

    class _FakeProcess:
        def __init__(self, command: tuple[str, ...]) -> None:
            self.command = command
            self.returncode: int | None = None
            self.stdin = _FakeStdin()

        def terminate(self) -> None:
            self.returncode = 0

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            self.returncode = 0 if self.returncode is None else self.returncode
            return self.returncode

    async def _fake_create_subprocess_exec(*command, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        starts.append(tuple(command))
        return _FakeProcess(tuple(command))

    monkeypatch.setattr("tts.service.asyncio.create_subprocess_exec", _fake_create_subprocess_exec)

    async def run() -> None:
        service = PersistentAplayAudioPlaybackService(
            command_template=("aplay", "-D", "default:CARD=vc4hdmi1", "{input_path}"),
            timeout_seconds=5,
        )
        audio = SynthesizedAudio(audio_bytes=_build_test_wav([1200] * 400, sample_rate=16000))
        request = SpeechRequest(text="hello", language=Language.ENGLISH)
        selection = ResolvedSpeechRequest(text="hello", voice_id="test", style_hint=SpeechStyle.NEUTRAL)

        first = await service.start(audio, job_id="1", request=request, selection=selection)
        await first.wait()
        await asyncio.sleep(0.15)
        second = await service.start(audio, job_id="2", request=request, selection=selection)
        await second.wait()
        await service.shutdown()

    asyncio.run(run())

    assert len(starts) == 1
    assert len(payloads) >= 3
    assert any(payload and set(payload) == {0} for payload in payloads)


def test_build_piper_tts_service_selects_alsa_backend(tmp_path: Path) -> None:
    config = TtsConfig(
        backend="piper",
        audio_backend="alsa_persistent",
        alsa_device="default:CARD=vc4hdmi1",
    )

    service = build_piper_tts_service(config, audio_output_dir=tmp_path)

    assert isinstance(service.playback, AlsaPersistentAudioPlaybackService)


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


def _build_test_wav(samples: list[int], *, sample_rate: int) -> bytes:
    pcm_frames = struct.pack(f"<{len(samples)}h", *samples)
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_frames)
        return buffer.getvalue()


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


def test_queued_tts_service_waits_for_playback_start_before_emitting_event() -> None:
    playback = _DelayedStartPlaybackService()
    events: list[Event] = []
    service = QueuedTtsService(
        synthesizer=MockSpeechSynthesizer(),
        playback=playback,
        queue_max=4,
    )

    async def record_event(event: Event) -> None:
        events.append(event)

    service.bind_event_handler(record_event)

    async def run() -> None:
        speak_task = asyncio.create_task(service.speak(SpeechRequest(text="hello", language=Language.ENGLISH)))
        deadline = time.monotonic() + 1.0
        while EventName.TTS_SYNTHESIS_FINISHED not in [event.name for event in events]:
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        assert EventName.TTS_SYNTHESIS_FINISHED in [event.name for event in events]
        assert EventName.TTS_PLAYBACK_STARTED not in [event.name for event in events]

        playback.started_signal.set()
        await asyncio.sleep(0)
        assert EventName.TTS_PLAYBACK_STARTED in [event.name for event in events]

        playback.released.set()
        output = await speak_task
        assert output.status is SpeechJobStatus.PLAYBACK_FINISHED
        await service.shutdown()

    asyncio.run(run())


def test_queued_tts_service_start_prewarms_process_manager() -> None:
    process_manager = _ProcessManagerSpy()
    playback = _PrewarmingPlaybackService()
    service = QueuedTtsService(
        synthesizer=MockSpeechSynthesizer(),
        playback=playback,
        process_manager=process_manager,  # type: ignore[arg-type]
        queue_max=4,
    )

    async def run() -> None:
        await service.start()
        await service.shutdown()

    asyncio.run(run())

    assert process_manager.ensure_running_calls == 1
    assert process_manager.shutdown_calls == 1
    assert playback.prewarm_calls == 1


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
