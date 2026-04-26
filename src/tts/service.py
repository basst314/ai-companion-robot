"""Text-to-speech services, provider adapters, and playback queueing."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import struct
import sys
import tempfile
import time
import uuid
import wave
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib import error, parse, request

from shared.config import TtsConfig
from shared.console import ConsoleFormatter, TerminalDebugSink
from shared.events import Event, EventName
from shared.models import (
    ComponentName,
    Language,
    SpeechJob,
    SpeechJobStatus,
    SpeechOutput,
    SpeechQueuePolicy,
    SpeechRequest,
    SpeechStyle,
    SynthesizedAudio,
)
from shared.process_utils import parent_death_signal_preexec_fn
from tts.alsa_backend import AlsaPcmConfig, AlsaPlaybackWorker, AlsaQueuedPcmJob

logger = logging.getLogger(__name__)

_PLAYBACK_LEAD_IN_MS = 120
_PLAYBACK_FADE_OUT_MS = 18
_PLAYBACK_TAIL_SILENCE_MS = 40
EventHandler = Callable[[Event], Awaitable[None]]


class TtsService(Protocol):
    """Interface for queued robot speech playback."""

    async def start(self) -> None:
        """Prepare any background dependencies needed before the first speech turn."""

    async def enqueue(self, request: SpeechRequest) -> SpeechJob:
        """Queue a request for synthesis/playback and return its job handle."""

    async def speak(self, request: SpeechRequest) -> SpeechOutput:
        """Queue a request and wait until it has finished playback."""

    async def wait_for_job(self, job_id: str) -> SpeechOutput:
        """Wait for a previously queued speech job to finish."""

    async def interrupt(self, *, reason: str = "interrupt") -> SpeechOutput | None:
        """Interrupt the active speech job if one is currently playing."""

    async def shutdown(self) -> None:
        """Cleanly stop all background work for the service."""

    def bind_event_handler(self, handler: EventHandler) -> None:
        """Bind the event sink used for TTS lifecycle events."""


class SpeechSynthesizer(Protocol):
    """Provider adapter that turns text into WAV bytes."""

    provider_name: str

    async def synthesize(self, request: SpeechRequest, selection: "ResolvedSpeechRequest") -> SynthesizedAudio:
        """Produce normalized audio bytes for a speech request."""


class AudioPlaybackSession(Protocol):
    """Handle for one active playback session."""

    async def wait_started(self) -> bool:
        """Wait until playback has actually started, if the backend can confirm it."""

    async def wait(self) -> "PlaybackResult":
        """Wait until playback has finished and return timing metadata."""

    async def interrupt(self) -> "PlaybackResult":
        """Stop playback as quickly as possible."""


class AudioPlaybackService(Protocol):
    """Adapter that plays synthesized speech through the host audio stack."""

    async def start(
        self,
        audio: SynthesizedAudio,
        *,
        job_id: str,
        request: SpeechRequest,
        selection: "ResolvedSpeechRequest",
    ) -> AudioPlaybackSession:
        """Start playback for one synthesized audio payload."""

    async def prewarm(self) -> None:
        """Prepare the playback stack before the first utterance."""

    async def shutdown(self) -> None:
        """Release any persistent playback resources."""


@dataclass(slots=True, frozen=True)
class ResolvedSpeechRequest:
    """Speech request with provider-specific voice selection resolved."""

    text: str
    voice_id: str
    style_hint: SpeechStyle
    speaker_id: int | None = None
    speaker_name: str | None = None
    length_scale: float | None = None
    noise_scale: float | None = None
    noise_w_scale: float | None = None


@dataclass(slots=True, frozen=True)
class PlaybackResult:
    """Outcome metadata for one playback session."""

    duration_ms: int | None = None
    artifact_path: Path | None = None
    interrupted: bool = False


@dataclass(slots=True, frozen=True)
class _StyleTuning:
    length_scale: float | None = None
    noise_scale: float | None = None
    noise_w_scale: float | None = None


_STYLE_TUNING: Mapping[SpeechStyle, _StyleTuning] = {
    SpeechStyle.NEUTRAL: _StyleTuning(),
    SpeechStyle.PLAYFUL: _StyleTuning(length_scale=0.96, noise_scale=0.78, noise_w_scale=0.88),
    SpeechStyle.SERIOUS: _StyleTuning(length_scale=1.03, noise_scale=0.56, noise_w_scale=0.72),
    SpeechStyle.WHISPER: _StyleTuning(length_scale=1.12, noise_scale=0.48, noise_w_scale=0.62),
    SpeechStyle.SURPRISED: _StyleTuning(length_scale=0.92, noise_scale=0.85, noise_w_scale=0.96),
}

_EXPRESSIVE_DE_SPEAKERS: Mapping[SpeechStyle, tuple[int, str]] = {
    SpeechStyle.PLAYFUL: (0, "amused"),
    SpeechStyle.SERIOUS: (4, "neutral"),
    SpeechStyle.WHISPER: (7, "whisper"),
    SpeechStyle.SURPRISED: (6, "surprised"),
}


@dataclass(slots=True)
class PiperVoiceResolver:
    """Resolve language/style hints into a Piper voice selection."""

    config: TtsConfig

    def resolve(self, request: SpeechRequest) -> ResolvedSpeechRequest:
        voice_id = request.voice_id or self._default_voice(request.language)
        speaker_id = request.speaker_id
        speaker_name: str | None = None
        tuning = _STYLE_TUNING.get(request.style_hint, _STYLE_TUNING[SpeechStyle.NEUTRAL])

        if (
            request.language is Language.GERMAN
            and request.style_hint in _EXPRESSIVE_DE_SPEAKERS
            and self.config.expressive_de_enabled
            and request.voice_id is None
            and request.speaker_id is None
        ):
            speaker_id, speaker_name = _EXPRESSIVE_DE_SPEAKERS[request.style_hint]
            voice_id = self.config.expressive_de_voice
            tuning = _STYLE_TUNING[SpeechStyle.NEUTRAL]

        return ResolvedSpeechRequest(
            text=request.text,
            voice_id=voice_id,
            style_hint=request.style_hint,
            speaker_id=speaker_id,
            speaker_name=speaker_name,
            length_scale=tuning.length_scale,
            noise_scale=tuning.noise_scale,
            noise_w_scale=tuning.noise_w_scale,
        )

    def _default_voice(self, language: Language) -> str:
        if language is Language.GERMAN:
            return self.config.default_voice_de
        if language is Language.INDONESIAN:
            return self.config.default_voice_id
        return self.config.default_voice_en


@dataclass(slots=True)
class PiperManagedProcess:
    """Managed Piper HTTP server process used in local dev mode."""

    base_url: str
    data_dir: Path
    default_voice: str
    command_override: tuple[str, ...] = ()
    startup_timeout_seconds: float = 8.0
    shutdown_timeout_seconds: float = 3.0
    process: asyncio.subprocess.Process | None = field(default=None, init=False, repr=False)
    _stderr_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    async def ensure_running(self) -> None:
        if await self._healthy():
            return

        if self.process is not None and self.process.returncode is None:
            return

        command = self.command_override or self._default_command()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        logger.info("starting managed Piper server: %s", " ".join(command))
        self.process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=parent_death_signal_preexec_fn(),
        )
        if self.process.stderr is not None:
            self._stderr_task = asyncio.create_task(self._capture_stderr(self.process))

        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if await self._healthy():
                return
            await asyncio.sleep(0.1)

        await self._stop_process()
        raise RuntimeError(f"managed Piper server at {self.base_url} did not become ready in time")

    async def shutdown(self) -> None:
        await self._stop_process()

    async def _stop_process(self) -> None:
        process = self.process
        stderr_task = self._stderr_task
        self.process = None
        self._stderr_task = None

        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=self.shutdown_timeout_seconds)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                with contextlib.suppress(ProcessLookupError):
                    await process.wait()

        if stderr_task is not None:
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task

    async def _healthy(self) -> bool:
        return await asyncio.to_thread(self._check_health)

    def _check_health(self) -> bool:
        try:
            with request.urlopen(self.base_url.rstrip("/") + "/voices", timeout=1.0) as response:
                return response.status == 200
        except Exception:
            return False

    def _default_command(self) -> tuple[str, ...]:
        parsed = parse.urlparse(self.base_url)
        host = parsed.hostname or "127.0.0.1"
        port = str(parsed.port or 5000)
        return (
            sys.executable,
            "-m",
            "piper.http_server",
            "-m",
            self.default_voice,
            "--host",
            host,
            "--port",
            port,
            "--data-dir",
            str(self.data_dir),
        )

    async def _capture_stderr(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        while True:
            line = await process.stderr.readline()
            if not line:
                return
            message = line.decode("utf-8", errors="replace").rstrip()
            if not message:
                continue
            if "This is a development server. Do not use it in a production deployment." in message:
                continue
            logger.info("piper.stderr %s", message)


@dataclass(slots=True)
class PiperHttpSynthesizer:
    """HTTP client for Piper's local synthesis API."""

    base_url: str
    timeout_seconds: float = 20.0
    provider_name: str = "piper"

    async def synthesize(self, request: SpeechRequest, selection: ResolvedSpeechRequest) -> SynthesizedAudio:
        payload: dict[str, object] = {
            "text": selection.text,
            "voice": selection.voice_id,
        }
        if selection.speaker_name is not None:
            payload["speaker"] = selection.speaker_name
        if selection.speaker_id is not None:
            payload["speaker_id"] = selection.speaker_id
        if selection.length_scale is not None:
            payload["length_scale"] = selection.length_scale
        if selection.noise_scale is not None:
            payload["noise_scale"] = selection.noise_scale
        if selection.noise_w_scale is not None:
            payload["noise_w_scale"] = selection.noise_w_scale

        audio_bytes = await asyncio.wait_for(
            asyncio.to_thread(self._post_json, payload),
            timeout=self.timeout_seconds,
        )
        return SynthesizedAudio(
            audio_bytes=audio_bytes,
            sample_rate_hz=_extract_wav_sample_rate(audio_bytes),
            voice_id=selection.voice_id,
            speaker_id=selection.speaker_id,
            metadata={"language": request.language.value, "style": selection.style_hint.value},
        )

    def list_voices(self) -> list[str]:
        with request.urlopen(self.base_url.rstrip("/") + "/voices", timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, dict):
            return sorted(str(key) for key in payload.keys())
        if isinstance(payload, list):
            return sorted(str(item) for item in payload)
        return []

    def _post_json(self, payload: dict[str, object]) -> bytes:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.base_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return response.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Piper synthesis failed with HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Piper synthesis failed: {exc.reason}") from exc


@dataclass(slots=True)
class CommandPlaybackSession:
    """Playback session backed by a host audio player command."""

    process: asyncio.subprocess.Process
    started_at: float
    artifact_path: Path
    cleanup_after: bool
    timeout_seconds: float
    _interrupted: bool = field(default=False, init=False, repr=False)

    async def wait_started(self) -> bool:
        return True

    async def wait(self) -> PlaybackResult:
        try:
            await asyncio.wait_for(self.process.wait(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            await self.interrupt()
            raise RuntimeError("audio playback timed out") from exc
        finally:
            await self._cleanup()

        if self.process.returncode not in {0, None} and not self._interrupted:
            raise RuntimeError(f"audio playback failed with exit code {self.process.returncode}")
        duration_ms = int(max(0.0, time.monotonic() - self.started_at) * 1000)
        return PlaybackResult(
            duration_ms=duration_ms,
            artifact_path=None if self.cleanup_after else self.artifact_path,
            interrupted=self._interrupted,
        )

    async def interrupt(self) -> PlaybackResult:
        self._interrupted = True
        if self.process.returncode is None:
            self.process.terminate()
            with contextlib.suppress(ProcessLookupError, asyncio.TimeoutError):
                await asyncio.wait_for(self.process.wait(), timeout=2.0)
            if self.process.returncode is None:
                self.process.kill()
                with contextlib.suppress(ProcessLookupError):
                    await self.process.wait()
        await self._cleanup()
        duration_ms = int(max(0.0, time.monotonic() - self.started_at) * 1000)
        return PlaybackResult(
            duration_ms=duration_ms,
            artifact_path=None if self.cleanup_after else self.artifact_path,
            interrupted=True,
        )

    async def _cleanup(self) -> None:
        if self.cleanup_after:
            with contextlib.suppress(FileNotFoundError):
                self.artifact_path.unlink()


@dataclass(slots=True)
class CommandAudioPlaybackService:
    """Playback implementation using a host command such as afplay or aplay."""

    command_template: tuple[str, ...]
    output_dir: Path
    save_artifacts: bool = False
    timeout_seconds: float = 60.0

    async def start(
        self,
        audio: SynthesizedAudio,
        *,
        job_id: str,
        request: SpeechRequest,
        selection: ResolvedSpeechRequest,
    ) -> AudioPlaybackSession:
        del request, selection
        artifact_path, cleanup_after = self._write_audio_file(job_id, audio)
        command = _format_input_command(self.command_template, artifact_path)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        return CommandPlaybackSession(
            process=process,
            started_at=time.monotonic(),
            artifact_path=artifact_path,
            cleanup_after=cleanup_after,
            timeout_seconds=self.timeout_seconds,
        )

    def _write_audio_file(self, job_id: str, audio: SynthesizedAudio) -> tuple[Path, bool]:
        prepared_audio = _prepare_audio_for_playback(audio)
        if self.save_artifacts:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = self.output_dir / f"{job_id}.wav"
            artifact_path.write_bytes(prepared_audio.audio_bytes)
            return artifact_path, False

        with tempfile.NamedTemporaryFile(prefix=f"tts-{job_id}-", suffix=".wav", delete=False) as handle:
            handle.write(prepared_audio.audio_bytes)
            return Path(handle.name), True

    async def shutdown(self) -> None:
        return None

    async def prewarm(self) -> None:
        return None


@dataclass(slots=True, frozen=True)
class _RawPcmAudio:
    pcm_frames: bytes
    channels: int
    sample_width_bytes: int
    sample_rate_hz: int

    @property
    def frame_count(self) -> int:
        frame_size = self.channels * self.sample_width_bytes
        if frame_size <= 0:
            return 0
        return len(self.pcm_frames) // frame_size

    @property
    def duration_seconds(self) -> float:
        if self.sample_rate_hz <= 0:
            return 0.0
        return self.frame_count / self.sample_rate_hz


@dataclass(slots=True)
class AlsaPlaybackSession:
    """Playback session backed by the dedicated ALSA worker."""

    service: "AlsaPersistentAudioPlaybackService"
    job_id: str
    started_future: asyncio.Future[bool]
    finished_future: asyncio.Future[PlaybackResult]

    async def wait_started(self) -> bool:
        return await self.started_future

    async def wait(self) -> PlaybackResult:
        return await self.finished_future

    async def interrupt(self) -> PlaybackResult:
        self.service.interrupt_job(self.job_id)
        return await self.finished_future


@dataclass(slots=True)
class AlsaPersistentAudioPlaybackService:
    """Dedicated ALSA output owner for Raspberry Pi playback."""

    device: str
    sample_rate_hz: int
    period_frames: int
    buffer_frames: int
    keepalive_interval_ms: int
    timeout_seconds: float = 60.0
    channels: int = 1
    _worker: AlsaPlaybackWorker | None = field(default=None, init=False, repr=False)
    _worker_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def start(
        self,
        audio: SynthesizedAudio,
        *,
        job_id: str,
        request: SpeechRequest,
        selection: ResolvedSpeechRequest,
    ) -> AudioPlaybackSession:
        del request, selection
        pcm_audio = _prepare_audio_for_alsa_playback(
            audio,
            target_sample_rate_hz=self.sample_rate_hz,
            target_channels=self.channels,
        )
        if pcm_audio is None:
            raise RuntimeError("ALSA playback requires uncompressed WAV audio")

        await self._ensure_worker()
        loop = asyncio.get_running_loop()
        started_future: asyncio.Future[bool] = loop.create_future()
        finished_future: asyncio.Future[PlaybackResult] = loop.create_future()

        def on_started() -> None:
            loop.call_soon_threadsafe(_resolve_started_future, started_future, True)

        def on_finished(duration_ms: int) -> None:
            loop.call_soon_threadsafe(
                _resolve_finished_future,
                started_future,
                finished_future,
                True,
                PlaybackResult(duration_ms=duration_ms),
            )

        def on_interrupted(duration_ms: int) -> None:
            loop.call_soon_threadsafe(
                _resolve_finished_future,
                started_future,
                finished_future,
                False,
                PlaybackResult(duration_ms=duration_ms, interrupted=True),
            )

        def on_failed(exc: Exception) -> None:
            loop.call_soon_threadsafe(_reject_playback_futures, started_future, finished_future, exc)

        assert self._worker is not None
        self._worker.enqueue(
            AlsaQueuedPcmJob(
                job_id=job_id,
                pcm_frames=pcm_audio.pcm_frames,
                on_started=on_started,
                on_finished=on_finished,
                on_interrupted=on_interrupted,
                on_failed=on_failed,
            )
        )
        return AlsaPlaybackSession(
            service=self,
            job_id=job_id,
            started_future=started_future,
            finished_future=finished_future,
        )

    async def prewarm(self) -> None:
        await self._ensure_worker()

    async def shutdown(self) -> None:
        async with self._worker_lock:
            worker = self._worker
            self._worker = None
        if worker is not None:
            await asyncio.to_thread(worker.shutdown)

    def interrupt_job(self, job_id: str) -> None:
        worker = self._worker
        if worker is not None:
            worker.interrupt(job_id=job_id)

    async def _ensure_worker(self) -> None:
        async with self._worker_lock:
            if self._worker is None:
                self._worker = AlsaPlaybackWorker(
                    AlsaPcmConfig(
                        device=self.device,
                        sample_rate_hz=self.sample_rate_hz,
                        channels=self.channels,
                        period_frames=self.period_frames,
                        buffer_frames=self.buffer_frames,
                        keepalive_interval_ms=self.keepalive_interval_ms,
                    )
                )
            worker = self._worker
        assert worker is not None
        await asyncio.to_thread(worker.ensure_started)


@dataclass(slots=True)
class _ImmediatePlaybackSession:
    """Playback session used by the mock playback adapter."""

    duration_ms: int
    interrupted: bool = False

    async def wait_started(self) -> bool:
        return True

    async def wait(self) -> PlaybackResult:
        return PlaybackResult(duration_ms=self.duration_ms, interrupted=self.interrupted)

    async def interrupt(self) -> PlaybackResult:
        self.interrupted = True
        return PlaybackResult(duration_ms=self.duration_ms, interrupted=True)


@dataclass(slots=True)
class MockSpeechSynthesizer:
    """Deterministic synthesizer used in tests and mock runtime mode."""

    provider_name: str = "mock"

    async def synthesize(self, request: SpeechRequest, selection: ResolvedSpeechRequest) -> SynthesizedAudio:
        del selection
        fake_wav = _build_silent_wav(duration_ms=max(80, len(request.text) * 10))
        return SynthesizedAudio(audio_bytes=fake_wav, voice_id="mock-voice")


@dataclass(slots=True)
class MockAudioPlaybackService:
    """No-op playback adapter that only records spoken text."""

    spoken_texts: list[str] = field(default_factory=list)

    async def start(
        self,
        audio: SynthesizedAudio,
        *,
        job_id: str,
        request: SpeechRequest,
        selection: ResolvedSpeechRequest,
    ) -> AudioPlaybackSession:
        del audio, job_id, selection
        self.spoken_texts.append(request.text)
        return _ImmediatePlaybackSession(duration_ms=max(80, len(request.text) * 10))

    async def shutdown(self) -> None:
        return None

    async def prewarm(self) -> None:
        return None


@dataclass(slots=True)
class _QueuedJob:
    job: SpeechJob
    request: SpeechRequest
    result_future: asyncio.Future[SpeechOutput]
    interrupted: bool = False
    failure_message: str | None = None


@dataclass(slots=True)
class QueuedTtsService:
    """Queue-backed TTS implementation shared by real and mock adapters."""

    synthesizer: SpeechSynthesizer
    playback: AudioPlaybackService
    voice_resolver: PiperVoiceResolver | None = None
    process_manager: PiperManagedProcess | None = None
    queue_max: int = 4
    terminal_debug: TerminalDebugSink | None = None
    spoken_texts: list[str] = field(default_factory=list)
    _event_handler: EventHandler | None = field(default=None, init=False, repr=False)
    _pending_jobs: deque[_QueuedJob] = field(default_factory=deque, init=False, repr=False)
    _queue_signal: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    _state_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _worker_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _current_job: _QueuedJob | None = field(default=None, init=False, repr=False)
    _current_session: AudioPlaybackSession | None = field(default=None, init=False, repr=False)
    _shutting_down: bool = field(default=False, init=False, repr=False)
    _job_results: dict[str, asyncio.Future[SpeechOutput]] = field(default_factory=dict, init=False, repr=False)

    def bind_event_handler(self, handler: EventHandler) -> None:
        self._event_handler = handler

    async def start(self) -> None:
        if self.process_manager is not None:
            await self.process_manager.ensure_running()
        await self.playback.prewarm()

    async def enqueue(self, request: SpeechRequest) -> SpeechJob:
        await self._ensure_worker()
        result_future: asyncio.Future[SpeechOutput] = asyncio.get_running_loop().create_future()
        job = SpeechJob(
            job_id=uuid.uuid4().hex,
            text=request.text,
            language=request.language,
            provider_name=self.synthesizer.provider_name,
            status=SpeechJobStatus.QUEUED,
            style_hint=request.style_hint,
            voice_id=request.voice_id,
            speaker_id=request.speaker_id,
        )
        queued_job = _QueuedJob(job=job, request=request, result_future=result_future)
        self._job_results[job.job_id] = result_future

        async with self._state_lock:
            active_count = len(self._pending_jobs) + (1 if self._current_job is not None else 0)
            if request.policy is SpeechQueuePolicy.DROP_IF_BUSY and active_count > 0:
                result = SpeechOutput(
                    text=request.text,
                    acknowledged=False,
                    job_id=job.job_id,
                    provider_name=self.synthesizer.provider_name,
                    language=request.language,
                    status=SpeechJobStatus.FAILED,
                )
                result_future.set_result(result)
                return job

            if request.policy is SpeechQueuePolicy.REPLACE_PENDING:
                self._drop_pending_locked()
            elif request.policy is SpeechQueuePolicy.INTERRUPT_AND_REPLACE:
                self._drop_pending_locked()
                if self._current_job is not None:
                    self._current_job.interrupted = True
                    if self._current_session is not None:
                        await self._current_session.interrupt()

            if len(self._pending_jobs) >= self.queue_max:
                overflow_result = SpeechOutput(
                    text=request.text,
                    acknowledged=False,
                    job_id=job.job_id,
                    provider_name=self.synthesizer.provider_name,
                    language=request.language,
                    status=SpeechJobStatus.FAILED,
                )
                result_future.set_result(overflow_result)
                await self._emit_tts_event(
                    EventName.TTS_FAILED,
                    {"job_id": job.job_id, "text": request.text, "error": "queue full"},
                )
                return job

            self._pending_jobs.append(queued_job)
            self._queue_signal.set()
            await self._emit_tts_event(
                EventName.TTS_ENQUEUED,
                {
                    "job_id": job.job_id,
                    "text": request.text,
                    "language": request.language.value,
                    "style": request.style_hint.value,
                    "queue_depth": self._queue_depth_locked(),
                },
            )
            self._update_debug(
                phase="queued",
                queue_depth=self._queue_depth_locked(),
                preview=request.text,
            )

        return job

    async def speak(self, request: SpeechRequest) -> SpeechOutput:
        job = await self.enqueue(request)
        output = await self.wait_for_job(job.job_id)
        if output.status is SpeechJobStatus.FAILED and output.error_message:
            raise RuntimeError(output.error_message)
        return output

    async def wait_for_job(self, job_id: str) -> SpeechOutput:
        future = self._job_results.get(job_id)
        if future is None:
            raise KeyError(f"unknown speech job id: {job_id}")
        try:
            return await future
        finally:
            self._job_results.pop(job_id, None)

    async def interrupt(self, *, reason: str = "interrupt") -> SpeechOutput | None:
        async with self._state_lock:
            if self._current_job is None:
                return None
            self._current_job.interrupted = True
            if self._current_session is not None:
                result = await self._current_session.interrupt()
            else:
                result = PlaybackResult(duration_ms=None, interrupted=True)
            await self._emit_tts_event(
                EventName.TTS_INTERRUPTED,
                {"job_id": self._current_job.job.job_id, "reason": reason},
            )
            self._update_debug(
                phase="interrupted",
                queue_depth=self._queue_depth_locked(),
            )
            return SpeechOutput(
                text=self._current_job.request.text,
                acknowledged=True,
                duration_ms=result.duration_ms,
                job_id=self._current_job.job.job_id,
                provider_name=self.synthesizer.provider_name,
                voice_id=self._current_job.job.voice_id,
                speaker_id=self._current_job.job.speaker_id,
                language=self._current_job.request.language,
                status=SpeechJobStatus.INTERRUPTED,
            )

    async def shutdown(self) -> None:
        self._shutting_down = True
        async with self._state_lock:
            self._drop_pending_locked()
        if self._current_session is not None:
            with contextlib.suppress(Exception):
                await self._current_session.interrupt()
        self._queue_signal.set()
        if self._worker_task is not None:
            await self._worker_task
        await self.playback.shutdown()
        if self.process_manager is not None:
            await self.process_manager.shutdown()

    async def _ensure_worker(self) -> None:
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def _worker_loop(self) -> None:
        while True:
            await self._queue_signal.wait()
            if self._shutting_down and not self._pending_jobs and self._current_job is None:
                return

            queued_job: _QueuedJob | None = None
            async with self._state_lock:
                if self._pending_jobs:
                    queued_job = self._pending_jobs.popleft()
                if not self._pending_jobs:
                    self._queue_signal.clear()

            if queued_job is None:
                if self._shutting_down:
                    return
                continue

            await self._run_job(queued_job)

    async def _run_job(self, queued_job: _QueuedJob) -> None:
        request_model = queued_job.request
        selection = self._resolve_request(request_model)
        self._current_job = queued_job
        queued_job.job = SpeechJob(
            job_id=queued_job.job.job_id,
            text=queued_job.job.text,
            language=queued_job.job.language,
            provider_name=queued_job.job.provider_name,
            status=SpeechJobStatus.SYNTHESIS_STARTED,
            style_hint=selection.style_hint,
            voice_id=selection.voice_id,
            speaker_id=selection.speaker_id,
        )
        self._update_debug(
            phase="synth",
            queue_depth=self._queue_depth_locked(),
            voice=selection.voice_id,
            style=selection.style_hint.value,
            speaker=selection.speaker_name or (str(selection.speaker_id) if selection.speaker_id is not None else "--"),
            preview=request_model.text,
        )
        await self._emit_tts_event(
            EventName.TTS_SYNTHESIS_STARTED,
            {
                "job_id": queued_job.job.job_id,
                "text": request_model.text,
                "voice_id": selection.voice_id,
                "speaker_id": selection.speaker_id,
                "style": selection.style_hint.value,
            },
        )

        try:
            if self.process_manager is not None:
                await self.process_manager.ensure_running()
            audio = await self.synthesizer.synthesize(request_model, selection)
        except Exception as exc:
            await self._finalize_failure(queued_job, exc)
            return

        if queued_job.interrupted:
            output = SpeechOutput(
                text=request_model.text,
                acknowledged=True,
                job_id=queued_job.job.job_id,
                provider_name=self.synthesizer.provider_name,
                voice_id=selection.voice_id,
                speaker_id=selection.speaker_id,
                language=request_model.language,
                status=SpeechJobStatus.INTERRUPTED,
            )
            if not queued_job.result_future.done():
                queued_job.result_future.set_result(output)
            self._current_job = None
            self._update_debug(phase="interrupted", queue_depth=self._queue_depth_locked())
            return

        await self._emit_tts_event(
            EventName.TTS_SYNTHESIS_FINISHED,
            {
                "job_id": queued_job.job.job_id,
                "text": request_model.text,
                "voice_id": selection.voice_id,
                "speaker_id": selection.speaker_id,
                "style": selection.style_hint.value,
            },
        )

        try:
            self._current_session = await self.playback.start(
                audio,
                job_id=queued_job.job.job_id,
                request=request_model,
                selection=selection,
            )
        except Exception as exc:
            await self._finalize_failure(queued_job, exc)
            return

        try:
            playback_started = await self._current_session.wait_started()
        except Exception as exc:
            await self._finalize_failure(queued_job, exc)
            return

        self._update_debug(
            phase="play",
            queue_depth=self._queue_depth_locked(),
            voice=selection.voice_id,
            style=selection.style_hint.value,
            speaker=selection.speaker_name or (str(selection.speaker_id) if selection.speaker_id is not None else "--"),
            preview=request_model.text,
        )
        if playback_started:
            await self._emit_tts_event(
                EventName.TTS_PLAYBACK_STARTED,
                {
                    "job_id": queued_job.job.job_id,
                    "text": request_model.text,
                    "voice_id": selection.voice_id,
                    "speaker_id": selection.speaker_id,
                    "style": selection.style_hint.value,
                },
            )
            await self._emit_tts_event(
                EventName.TTS_STARTED,
                {
                    "job_id": queued_job.job.job_id,
                    "text": request_model.text,
                    "voice_id": selection.voice_id,
                },
            )
            self._log_spoken_text(request_model.text)

        try:
            playback_result = await self._current_session.wait()
        except Exception as exc:
            await self._finalize_failure(queued_job, exc)
            return
        finally:
            self._current_session = None

        self.spoken_texts.append(request_model.text)
        if playback_result.interrupted or queued_job.interrupted:
            output = SpeechOutput(
                text=request_model.text,
                acknowledged=True,
                duration_ms=playback_result.duration_ms,
                job_id=queued_job.job.job_id,
                provider_name=self.synthesizer.provider_name,
                voice_id=selection.voice_id,
                speaker_id=selection.speaker_id,
                language=request_model.language,
                status=SpeechJobStatus.INTERRUPTED,
            )
            await self._emit_tts_event(
                EventName.TTS_INTERRUPTED,
                {"job_id": queued_job.job.job_id, "text": request_model.text},
            )
            self._update_debug(phase="interrupted", queue_depth=self._queue_depth_locked())
        else:
            output = SpeechOutput(
                text=request_model.text,
                acknowledged=True,
                duration_ms=playback_result.duration_ms,
                job_id=queued_job.job.job_id,
                provider_name=self.synthesizer.provider_name,
                voice_id=selection.voice_id,
                speaker_id=selection.speaker_id,
                language=request_model.language,
                status=SpeechJobStatus.PLAYBACK_FINISHED,
            )
            await self._emit_tts_event(
                EventName.TTS_PLAYBACK_FINISHED,
                {
                    "job_id": queued_job.job.job_id,
                    "text": request_model.text,
                    "voice_id": selection.voice_id,
                    "speaker_id": selection.speaker_id,
                    "duration_ms": playback_result.duration_ms,
                },
            )
            await self._emit_tts_event(
                EventName.TTS_FINISHED,
                {
                    "job_id": queued_job.job.job_id,
                    "text": request_model.text,
                    "voice_id": selection.voice_id,
                    "duration_ms": playback_result.duration_ms,
                },
            )
            await self._emit_tts_event(
                EventName.AUDIO_FINISHED,
                {
                    "job_id": queued_job.job.job_id,
                    "text": request_model.text,
                    "duration_ms": playback_result.duration_ms,
                },
            )
            self._update_debug(phase="idle", queue_depth=self._queue_depth_locked())

        if not queued_job.result_future.done():
            queued_job.result_future.set_result(output)
        self._current_job = None

    async def _finalize_failure(self, queued_job: _QueuedJob, exc: Exception) -> None:
        logger.exception("tts failed")
        output = SpeechOutput(
            text=queued_job.request.text,
            acknowledged=False,
            error_message=str(exc),
            job_id=queued_job.job.job_id,
            provider_name=self.synthesizer.provider_name,
            language=queued_job.request.language,
            status=SpeechJobStatus.FAILED,
        )
        if not queued_job.result_future.done():
            queued_job.result_future.set_result(output)
        self._update_debug(phase="failed", queue_depth=self._queue_depth_locked())
        await self._emit_tts_event(
            EventName.TTS_FAILED,
            {"job_id": queued_job.job.job_id, "text": queued_job.request.text, "error": str(exc)},
        )
        await self._emit_tts_event(
            EventName.ERROR_OCCURRED,
            {"job_id": queued_job.job.job_id, "error": str(exc)},
        )
        self._current_job = None
        self._current_session = None

    def _resolve_request(self, request_model: SpeechRequest) -> ResolvedSpeechRequest:
        if self.voice_resolver is None:
            return ResolvedSpeechRequest(
                text=request_model.text,
                voice_id=request_model.voice_id or f"{request_model.language.value}-default",
                style_hint=request_model.style_hint,
                speaker_id=request_model.speaker_id,
            )
        return self.voice_resolver.resolve(request_model)

    def _queue_depth_locked(self) -> int:
        return len(self._pending_jobs) + (1 if self._current_job is not None else 0)

    def _drop_pending_locked(self) -> None:
        while self._pending_jobs:
            dropped = self._pending_jobs.popleft()
            if not dropped.result_future.done():
                dropped.result_future.set_result(
                    SpeechOutput(
                        text=dropped.request.text,
                        acknowledged=False,
                        job_id=dropped.job.job_id,
                        provider_name=self.synthesizer.provider_name,
                        language=dropped.request.language,
                        status=SpeechJobStatus.INTERRUPTED,
                    )
                )

    async def _emit_tts_event(self, name: EventName, payload: Mapping[str, object]) -> None:
        if self._event_handler is None:
            return
        await self._event_handler(
            Event(
                name=name,
                source=ComponentName.TTS,
                payload=payload,
            )
        )

    def _update_debug(
        self,
        *,
        phase: str | None = None,
        queue_depth: int | None = None,
        voice: str | None = None,
        style: str | None = None,
        speaker: str | None = None,
        preview: str | None = None,
    ) -> None:
        if self.terminal_debug is None:
            return
        self.terminal_debug.update_tts_status(
            backend=self.synthesizer.provider_name,
            phase=phase,
            voice=voice,
            style=style,
            speaker=speaker,
            queue_depth=queue_depth,
            preview=preview,
        )

    def _log_spoken_text(self, text: str) -> None:
        formatter = ConsoleFormatter()
        formatter.emit(
            formatter.stamp(f"{formatter.tts_label('[TTS]')} {formatter.response(text)}"),
            plain_text=formatter.stamp(f"[TTS] {text}"),
        )


@dataclass(slots=True)
class MockTtsService(TtsService):
    """Queue-backed mock TTS used in tests and the default app configuration."""

    spoken_texts: list[str] = field(default_factory=list)
    should_fail: bool = False
    terminal_debug: TerminalDebugSink | None = None
    _delegate: QueuedTtsService = field(init=False, repr=False)

    def __post_init__(self) -> None:
        synthesizer: SpeechSynthesizer
        if self.should_fail:
            synthesizer = _FailingSpeechSynthesizer()
        else:
            synthesizer = MockSpeechSynthesizer()
        self._delegate = QueuedTtsService(
            synthesizer=synthesizer,
            playback=MockAudioPlaybackService(spoken_texts=self.spoken_texts),
            queue_max=4,
            terminal_debug=self.terminal_debug,
        )

    def bind_event_handler(self, handler: EventHandler) -> None:
        self._delegate.bind_event_handler(handler)

    async def start(self) -> None:
        await self._delegate.start()

    async def enqueue(self, request: SpeechRequest) -> SpeechJob:
        return await self._delegate.enqueue(request)

    async def speak(self, request: SpeechRequest) -> SpeechOutput:
        return await self._delegate.speak(request)

    async def wait_for_job(self, job_id: str) -> SpeechOutput:
        return await self._delegate.wait_for_job(job_id)

    async def interrupt(self, *, reason: str = "interrupt") -> SpeechOutput | None:
        return await self._delegate.interrupt(reason=reason)

    async def shutdown(self) -> None:
        await self._delegate.shutdown()


@dataclass(slots=True)
class _FailingSpeechSynthesizer:
    provider_name: str = "mock"

    async def synthesize(self, request: SpeechRequest, selection: ResolvedSpeechRequest) -> SynthesizedAudio:
        del request, selection
        raise RuntimeError("mock tts failure")


def build_piper_tts_service(
    config: TtsConfig,
    *,
    audio_output_dir: Path,
    terminal_debug: TerminalDebugSink | None = None,
) -> QueuedTtsService:
    """Construct the Piper-backed queued TTS service."""

    resolver = PiperVoiceResolver(config)
    process_manager = None
    if config.piper_service_mode == "managed":
        process_manager = PiperManagedProcess(
            base_url=config.piper_base_url,
            data_dir=config.piper_data_dir,
            default_voice=config.default_voice_en,
            command_override=config.piper_command,
        )
    playback_command = config.audio_play_command
    if not playback_command:
        playback_command = _default_audio_play_command()
    if config.audio_backend == "alsa_persistent":
        playback: AudioPlaybackService = AlsaPersistentAudioPlaybackService(
            device=config.alsa_device,
            sample_rate_hz=config.alsa_sample_rate,
            period_frames=config.alsa_period_frames,
            buffer_frames=config.alsa_buffer_frames,
            keepalive_interval_ms=config.alsa_keepalive_interval_ms,
            timeout_seconds=config.playback_timeout_seconds,
        )
    else:
        playback = CommandAudioPlaybackService(
            command_template=playback_command,
            output_dir=audio_output_dir,
            save_artifacts=config.save_artifacts,
            timeout_seconds=config.playback_timeout_seconds,
        )
    return QueuedTtsService(
        synthesizer=PiperHttpSynthesizer(
            base_url=config.piper_base_url,
            timeout_seconds=config.synthesis_timeout_seconds,
        ),
        playback=playback,
        voice_resolver=resolver,
        process_manager=process_manager,
        queue_max=config.queue_max,
        terminal_debug=terminal_debug,
    )


def _default_audio_play_command() -> tuple[str, ...]:
    if sys.platform == "darwin":
        return ("afplay", "{input_path}")
    return ("aplay", "{input_path}")


def _format_input_command(command_template: Sequence[str], input_path: Path) -> tuple[str, ...]:
    if not command_template:
        raise RuntimeError("audio playback command is not configured")
    resolved = tuple(part.replace("{input_path}", str(input_path)) for part in command_template)
    if not any(str(input_path) in part for part in resolved):
        return (*resolved, str(input_path))
    return resolved


def _build_silent_wav(*, duration_ms: int, sample_rate: int = 16000) -> bytes:
    frame_count = max(1, int(sample_rate * (duration_ms / 1000.0)))
    pcm_data = b"\x00\x00" * frame_count
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_data)
        return buffer.getvalue()


def _prepare_audio_for_playback(audio: SynthesizedAudio) -> SynthesizedAudio:
    if audio.mime_type != "audio/wav":
        return audio
    prepared_bytes = _prepare_wav_bytes_for_playback(audio.audio_bytes)
    if prepared_bytes == audio.audio_bytes:
        return audio
    return SynthesizedAudio(
        audio_bytes=prepared_bytes,
        mime_type=audio.mime_type,
        sample_rate_hz=audio.sample_rate_hz,
        voice_id=audio.voice_id,
        speaker_id=audio.speaker_id,
        metadata=audio.metadata,
    )


def _prepare_audio_for_alsa_playback(
    audio: SynthesizedAudio,
    *,
    target_sample_rate_hz: int,
    target_channels: int,
) -> _RawPcmAudio | None:
    if audio.mime_type != "audio/wav":
        return None
    pcm_audio = _extract_raw_pcm_audio(audio.audio_bytes)
    if pcm_audio is None:
        return None
    return _normalize_pcm_audio(
        pcm_audio,
        target_sample_rate_hz=target_sample_rate_hz,
        target_channels=target_channels,
        add_lead_in=False,
    )


def _prepare_wav_bytes_for_playback(audio_bytes: bytes) -> bytes:
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            params = wav_file.getparams()
            pcm_frames = wav_file.readframes(wav_file.getnframes())
    except Exception:
        return audio_bytes

    if params.comptype != "NONE" or params.nchannels <= 0 or params.sampwidth <= 0 or params.framerate <= 0:
        return audio_bytes

    frame_size = params.nchannels * params.sampwidth
    if frame_size <= 0:
        return audio_bytes

    lead_in_frames = int(params.framerate * (_PLAYBACK_LEAD_IN_MS / 1000.0))
    tail_silence_frames = int(params.framerate * (_PLAYBACK_TAIL_SILENCE_MS / 1000.0))
    prepared_frames = _apply_wav_fade_out(
        pcm_frames,
        channels=params.nchannels,
        sample_width=params.sampwidth,
        sample_rate=params.framerate,
        fade_out_ms=_PLAYBACK_FADE_OUT_MS,
    )
    prepared_frames = (
        (b"\x00" * (lead_in_frames * frame_size))
        + prepared_frames
        + (b"\x00" * (tail_silence_frames * frame_size))
    )

    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setparams(params)
            wav_file.writeframes(prepared_frames)
        return buffer.getvalue()


def _extract_raw_pcm_audio(audio_bytes: bytes) -> _RawPcmAudio | None:
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            if wav_file.getcomptype() != "NONE":
                return None
            sample_width = wav_file.getsampwidth()
            channels = wav_file.getnchannels()
            sample_rate = wav_file.getframerate()
            pcm_frames = wav_file.readframes(wav_file.getnframes())
    except Exception:
        return None

    if sample_width <= 0 or channels <= 0 or sample_rate <= 0:
        return None
    return _RawPcmAudio(
        pcm_frames=pcm_frames,
        channels=channels,
        sample_width_bytes=sample_width,
        sample_rate_hz=sample_rate,
    )


def _normalize_pcm_audio(
    pcm_audio: _RawPcmAudio,
    *,
    target_sample_rate_hz: int,
    target_channels: int,
    add_lead_in: bool,
) -> _RawPcmAudio | None:
    pcm_frames = pcm_audio.pcm_frames
    channels = pcm_audio.channels
    sample_width = pcm_audio.sample_width_bytes
    sample_rate = pcm_audio.sample_rate_hz

    if sample_width not in {1, 2, 4} or channels <= 0 or sample_rate <= 0:
        return None

    frame_samples = _decode_pcm_frames(pcm_frames, sample_width)
    if frame_samples is None:
        return None

    if channels != target_channels:
        if channels == 2 and target_channels == 1:
            frame_samples = _downmix_stereo_to_mono(frame_samples)
            channels = 1
        else:
            return None

    if sample_width != 2:
        frame_samples = _convert_sample_width_to_s16(frame_samples, sample_width)
        sample_width = 2

    if sample_rate != target_sample_rate_hz:
        frame_samples = _resample_interleaved_s16(
            frame_samples,
            channels=channels,
            source_rate_hz=sample_rate,
            target_rate_hz=target_sample_rate_hz,
        )
        sample_rate = target_sample_rate_hz

    pcm_frames = _encode_pcm_s16(frame_samples)

    if add_lead_in:
        lead_in_frames = int(sample_rate * (_PLAYBACK_LEAD_IN_MS / 1000.0))
        pcm_frames = (b"\x00" * (lead_in_frames * channels * sample_width)) + pcm_frames

    return _RawPcmAudio(
        pcm_frames=pcm_frames,
        channels=channels,
        sample_width_bytes=sample_width,
        sample_rate_hz=sample_rate,
    )


def _decode_pcm_frames(pcm_frames: bytes, sample_width: int) -> list[int] | None:
    if sample_width == 1:
        return [(byte - 128) << 8 for byte in pcm_frames]
    if sample_width == 2:
        sample_count = len(pcm_frames) // 2
        try:
            return list(struct.unpack(f"<{sample_count}h", pcm_frames))
        except struct.error:
            return None
    if sample_width == 4:
        sample_count = len(pcm_frames) // 4
        try:
            raw_samples = struct.unpack(f"<{sample_count}i", pcm_frames)
        except struct.error:
            return None
        return [_clamp_s16(sample >> 16) for sample in raw_samples]
    return None


def _downmix_stereo_to_mono(samples: list[int]) -> list[int]:
    mono: list[int] = []
    limit = len(samples) - (len(samples) % 2)
    for index in range(0, limit, 2):
        mono.append(_clamp_s16((samples[index] + samples[index + 1]) // 2))
    return mono


def _convert_sample_width_to_s16(samples: list[int], sample_width: int) -> list[int]:
    if sample_width == 2:
        return samples
    if sample_width == 1:
        return [_clamp_s16(sample) for sample in samples]
    if sample_width == 4:
        return [_clamp_s16(sample) for sample in samples]
    raise RuntimeError(f"unsupported sample width: {sample_width}")


def _resample_interleaved_s16(
    samples: list[int],
    *,
    channels: int,
    source_rate_hz: int,
    target_rate_hz: int,
) -> list[int]:
    if source_rate_hz == target_rate_hz or not samples:
        return samples
    frame_count = len(samples) // channels
    if frame_count <= 1:
        return samples
    target_frame_count = max(1, int(round(frame_count * target_rate_hz / source_rate_hz)))
    resampled: list[int] = []
    for target_index in range(target_frame_count):
        source_position = target_index * (frame_count - 1) / max(1, target_frame_count - 1)
        left_frame = int(source_position)
        right_frame = min(left_frame + 1, frame_count - 1)
        blend = source_position - left_frame
        left_offset = left_frame * channels
        right_offset = right_frame * channels
        for channel in range(channels):
            left_value = samples[left_offset + channel]
            right_value = samples[right_offset + channel]
            interpolated = int(round(left_value + ((right_value - left_value) * blend)))
            resampled.append(_clamp_s16(interpolated))
    return resampled


def _encode_pcm_s16(samples: list[int]) -> bytes:
    return struct.pack(f"<{len(samples)}h", *(_clamp_s16(sample) for sample in samples))


def _clamp_s16(value: int) -> int:
    return max(-32768, min(32767, int(value)))


def _resolve_started_future(future: asyncio.Future[bool], value: bool) -> None:
    if not future.done():
        future.set_result(value)


def _resolve_finished_future(
    started_future: asyncio.Future[bool],
    finished_future: asyncio.Future[PlaybackResult],
    started_value: bool,
    result: PlaybackResult,
) -> None:
    if not started_future.done():
        started_future.set_result(started_value)
    if not finished_future.done():
        finished_future.set_result(result)


def _reject_playback_futures(
    started_future: asyncio.Future[bool],
    finished_future: asyncio.Future[PlaybackResult],
    exc: Exception,
) -> None:
    if not started_future.done():
        started_future.set_exception(exc)
    if not finished_future.done():
        finished_future.set_exception(exc)


def _apply_wav_fade_out(
    pcm_frames: bytes,
    *,
    channels: int,
    sample_width: int,
    sample_rate: int,
    fade_out_ms: int,
) -> bytes:
    if sample_width != 2 or channels <= 0 or sample_rate <= 0 or fade_out_ms <= 0:
        return pcm_frames
    sample_count = len(pcm_frames) // sample_width
    if sample_count <= 0:
        return pcm_frames
    try:
        samples = list(struct.unpack(f"<{sample_count}h", pcm_frames))
    except struct.error:
        return pcm_frames

    total_frames = sample_count // channels
    fade_frames = min(total_frames, max(1, int(sample_rate * (fade_out_ms / 1000.0))))
    fade_start = total_frames - fade_frames
    for frame_index in range(fade_start, total_frames):
        gain = (total_frames - frame_index - 1) / fade_frames
        sample_offset = frame_index * channels
        for channel_index in range(channels):
            samples[sample_offset + channel_index] = int(samples[sample_offset + channel_index] * gain)
    return struct.pack(f"<{sample_count}h", *samples)


def _extract_wav_sample_rate(audio_bytes: bytes) -> int | None:
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            return wav_file.getframerate()
    except Exception:
        return None
