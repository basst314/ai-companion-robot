"""Speech-to-text service interfaces and whisper.cpp adapter."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import struct
import tempfile
import wave
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from shared.models import Language, Transcript

import logging
from shared.console import ConsoleFormatter, TerminalDebugSink


logger = logging.getLogger(__name__)


def _emit_whisper_terminal_status(message: str, terminal_debug: TerminalDebugSink | None = None) -> None:
    """Show concise terminal-only Whisper status without polluting tests."""

    if terminal_debug is not None:
        terminal_debug.update_whisper_status(message)
        return
    formatter = ConsoleFormatter()
    if not formatter.enabled:
        return
    plain_message = formatter.stamp(f"[STT] Whisper {message}")
    styled_message = formatter.stamp(f"{formatter.stt_label('[STT]')} {formatter.whisper(f'Whisper {message}')}")
    formatter.emit(f"\r{styled_message}".ljust(120), plain_text=plain_message)


class SttService(Protocol):
    """Interface for streaming transcript updates."""

    async def listen_once(self) -> Transcript:
        """Capture one utterance and return the final transcript."""

    async def stream_transcripts(self) -> AsyncIterator[Transcript]:
        """Yield partial and final transcript results."""


class WakeWordService(Protocol):
    """Interface for bounded wake-word detection."""

    async def wait_for_wake_word(self) -> "WakeDetectionResult":
        """Block until a wake phrase is detected and return the matched audio context."""


class AudioCaptureService(Protocol):
    """Interface for capturing microphone audio."""

    async def capture_wav(self) -> Path:
        """Compatibility one-shot capture path."""


class StreamingAudioCaptureService(AudioCaptureService, Protocol):
    """Capture service that supports start/stop control for streaming."""

    async def start_capture(self) -> "RecordingSession":
        """Start recording and return a live session handle."""


class RecordingSession(Protocol):
    """Handle for a running microphone capture process."""

    @property
    def output_path(self) -> Path:
        """Return the recording WAV path."""

    @property
    def returncode(self) -> int | None:
        """Return the underlying recorder process exit code if known."""

    async def stop(self) -> None:
        """Stop the running capture."""

    async def wait(self) -> int:
        """Wait until the recorder exits and return the exit code."""

    def mark_stop_requested(self) -> None:
        """Record that shutdown was initiated by the app."""

    @property
    def stop_requested(self) -> bool:
        """Return whether shutdown was initiated by the app."""


@dataclass(slots=True, frozen=True)
class WhisperSegment:
    """Timestamped whisper segment metadata used for wake alignment."""

    start_seconds: float
    end_seconds: float
    text: str


@dataclass(slots=True, frozen=True)
class WakeDetectionResult:
    """Wake-word detection outcome used to seed the next speech turn."""

    detected: bool
    matched_transcript: str = ""
    prefilled_command_text: str = ""
    audio_window: "AudioWindow | None" = None
    utterance_start_offset_seconds: float = 0.0
    segments: tuple[WhisperSegment, ...] = ()


@dataclass(slots=True, frozen=True)
class CommandResult:
    """Minimal subprocess result used for dependency injection in tests."""

    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


SubprocessRunner = Callable[[Sequence[str]], Awaitable[CommandResult]]


@dataclass(slots=True)
class RollingAudioBuffer:
    """Bounded PCM ring buffer used for idle wake-word listening."""

    max_bytes: int
    _pcm_data: bytearray = field(default_factory=bytearray, repr=False)
    _start_offset: int = field(default=0, init=False, repr=False)

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._pcm_data.extend(chunk)
        overflow = len(self._pcm_data) - self.max_bytes
        if overflow > 0:
            del self._pcm_data[:overflow]
            self._start_offset += overflow

    def clear(self) -> None:
        self._pcm_data.clear()
        self._start_offset = 0

    def snapshot(self) -> bytes:
        return bytes(self._pcm_data)

    @property
    def start_offset(self) -> int:
        return self._start_offset

    @property
    def end_offset(self) -> int:
        return self._start_offset + len(self._pcm_data)

    def recent(self, byte_count: int) -> tuple[bytes, int]:
        if byte_count <= 0 or len(self._pcm_data) <= byte_count:
            return bytes(self._pcm_data), self._start_offset
        return bytes(self._pcm_data[-byte_count:]), self.end_offset - byte_count

    def slice_from(self, start_offset: int) -> tuple[bytes, int]:
        actual_start = max(start_offset, self._start_offset)
        relative_start = max(0, actual_start - self._start_offset)
        return bytes(self._pcm_data[relative_start:]), actual_start


@dataclass(slots=True)
class SharedLiveSpeechState:
    """One live microphone session shared by wake listening and utterance capture."""

    audio_capture: AudioCaptureService
    wake_buffer_seconds: float
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2
    session: RecordingSession | None = field(default=None, init=False, repr=False)
    utterance_active: bool = field(default=False, init=False)
    utterance_started_at: datetime | None = field(default=None, init=False)
    utterance_buffer: bytearray = field(default_factory=bytearray, init=False, repr=False)
    utterance_stream_start_offset: int | None = field(default=None, init=False, repr=False)
    _wake_buffer: RollingAudioBuffer = field(init=False, repr=False)
    _source_offset_bytes: int = field(default=0, init=False, repr=False)
    _callback_driven: bool = field(default=False, init=False, repr=False)
    _source_path: Path | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        bytes_per_second = max(1, self.channels * self.sample_width * self.sample_rate)
        self._wake_buffer = RollingAudioBuffer(max(1, int(bytes_per_second * max(0.5, self.wake_buffer_seconds))))

    async def ensure_session(self) -> RecordingSession:
        if self.session is not None:
            return self.session

        if not hasattr(self.audio_capture, "start_capture"):
            raise RuntimeError("shared live speech state requires streaming audio capture")

        self._callback_driven = False
        start_capture = getattr(self.audio_capture, "start_capture")
        try:
            session = await start_capture(on_chunk=self._handle_chunk)
            self._callback_driven = True
        except TypeError:
            session = await start_capture()
        self.session = session
        if self._callback_driven:
            self._source_path = getattr(session, "output_path", getattr(session, "pcm_path", None))
        else:
            self._source_path = getattr(session, "pcm_path", getattr(session, "output_path", None))
        return session

    async def sync(self) -> None:
        session = await self.ensure_session()
        if self._callback_driven:
            return

        source_path = self._source_path or getattr(session, "pcm_path", getattr(session, "output_path", None))
        if source_path is None or not source_path.exists():
            return
        pcm_data = source_path.read_bytes()
        if len(pcm_data) < self._source_offset_bytes:
            self._source_offset_bytes = 0
        chunk = pcm_data[self._source_offset_bytes :]
        self._source_offset_bytes = len(pcm_data)
        self._handle_chunk(chunk)

    def start_utterance(
        self,
        initial_window: "AudioWindow | None" = None,
        *,
        stream_start_offset: int | None = None,
    ) -> None:
        if self.utterance_active:
            return
        if stream_start_offset is not None:
            pcm_data, actual_start = self._wake_buffer.slice_from(stream_start_offset)
            self.utterance_buffer = bytearray(pcm_data)
            self.utterance_stream_start_offset = actual_start
        else:
            self.utterance_buffer = bytearray(initial_window.pcm_data if initial_window is not None else b"")
            self.utterance_stream_start_offset = None
        self.utterance_active = True
        self.utterance_started_at = datetime.now(UTC)

    def reset_utterance(self) -> None:
        self.utterance_active = False
        self.utterance_started_at = None
        self.utterance_stream_start_offset = None
        self.utterance_buffer.clear()

    def current_wake_window(
        self,
        *,
        duration_seconds: float,
        threshold: int,
        source_path: Path | None = None,
    ) -> "AudioWindow | None":
        bytes_per_frame = self.channels * self.sample_width
        frame_count = int(self.sample_rate * max(0.0, duration_seconds))
        byte_count = frame_count * bytes_per_frame
        pcm_data, start_offset = self._wake_buffer.recent(byte_count)
        return self._build_window(
            pcm_data,
            threshold=threshold,
            source_path=source_path,
            stream_start_offset=start_offset,
        )

    def current_utterance_window(self, *, threshold: int, source_path: Path | None = None) -> "AudioWindow | None":
        return self._build_window(
            bytes(self.utterance_buffer),
            threshold=threshold,
            source_path=source_path,
            stream_start_offset=self.utterance_stream_start_offset or 0,
        )

    def finish_utterance(self, *, threshold: int, source_path: Path | None = None) -> "AudioWindow | None":
        audio_window = self.current_utterance_window(threshold=threshold, source_path=source_path)
        self.reset_utterance()
        return audio_window

    def ring_buffer_debug_state(
        self,
        *,
        wake_window_seconds: float,
    ) -> tuple[float, float, float, float | None, float]:
        bytes_per_second = max(1.0, float(self.channels * self.sample_width * self.sample_rate))
        capacity_seconds = self._wake_buffer.max_bytes / bytes_per_second
        filled_seconds = len(self._wake_buffer.snapshot()) / bytes_per_second
        utterance_start_seconds = None
        if self.utterance_active and self.utterance_stream_start_offset is not None:
            utterance_start_seconds = max(
                0.0,
                (self.utterance_stream_start_offset - self._wake_buffer.start_offset) / bytes_per_second,
            )
            utterance_start_seconds = min(capacity_seconds, utterance_start_seconds)
        write_head_seconds = ((self._wake_buffer.end_offset % self._wake_buffer.max_bytes) / bytes_per_second)
        return (
            capacity_seconds,
            filled_seconds,
            min(wake_window_seconds, capacity_seconds),
            utterance_start_seconds,
            write_head_seconds,
        )

    async def close(self) -> None:
        session = self.session
        self.session = None
        self._source_path = None
        self._source_offset_bytes = 0
        self._callback_driven = False
        if session is None:
            self._wake_buffer.clear()
            self.reset_utterance()
            return
        with contextlib.suppress(ProcessLookupError):
            if session.returncode is None:
                await session.stop()
        self._wake_buffer.clear()
        self.reset_utterance()

    def _handle_chunk(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._wake_buffer.append(chunk)
        if self.utterance_active:
            self.utterance_buffer.extend(chunk)

    def _build_window(
        self,
        pcm_data: bytes,
        *,
        threshold: int,
        source_path: Path | None,
        stream_start_offset: int,
    ) -> "AudioWindow | None":
        if not pcm_data:
            return None
        return _audio_window_from_pcm(
            pcm_data,
            source_path=source_path or Path("shared-live-session.pcm"),
            channels=self.channels,
            sample_width=self.sample_width,
            sample_rate=self.sample_rate,
            threshold=threshold,
            stream_start_offset=stream_start_offset,
        )


async def _default_run_command(command: Sequence[str]) -> CommandResult:
    """Run a subprocess and capture text output."""

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            if process.returncode is None:
                process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        raise
    return CommandResult(
        args=tuple(command),
        returncode=process.returncode,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
    )


@dataclass(slots=True)
class MockSttService:
    """Deterministic transcript stream for tests and early development."""

    utterances: tuple[str, ...] = ()
    emit_partials: bool = True
    language: Language = Language.ENGLISH
    confidence: float = 0.98
    _sequences: tuple[tuple[Transcript, ...], ...] = field(default_factory=tuple)
    _listen_index: int = 0

    async def listen_once(self) -> Transcript:
        if self._sequences:
            if self._listen_index >= len(self._sequences):
                raise RuntimeError("mock STT has no remaining transcript sequences")

            sequence = self._sequences[self._listen_index]
            self._listen_index += 1
            for transcript in sequence:
                if transcript.is_final:
                    return transcript
            raise RuntimeError("mock STT has no final transcript configured")

        if not self.utterances:
            raise RuntimeError("mock STT has no utterances configured")
        if self._listen_index >= len(self.utterances):
            raise RuntimeError("mock STT has no remaining utterances configured")

        utterance = self.utterances[self._listen_index]
        self._listen_index += 1
        return next(
            transcript
            for transcript in self._build_sequence(utterance)
            if transcript.is_final
        )

    async def stream_transcripts(self) -> AsyncIterator[Transcript]:
        if self._sequences:
            if self._listen_index >= len(self._sequences):
                raise RuntimeError("mock STT has no remaining transcript sequences")

            sequence = self._sequences[self._listen_index]
            self._listen_index += 1
            for transcript in sequence:
                yield transcript
            return

        if not self.utterances:
            raise RuntimeError("mock STT has no utterances configured")
        if self._listen_index >= len(self.utterances):
            raise RuntimeError("mock STT has no remaining utterances configured")

        utterance = self.utterances[self._listen_index]
        self._listen_index += 1
        for transcript in self._build_sequence(utterance):
            yield transcript

    def _build_sequence(self, utterance: str) -> Iterable[Transcript]:
        started_at = datetime.now(UTC)
        if self.emit_partials:
            words = utterance.split()
            if len(words) > 1:
                yield Transcript(
                    text=" ".join(words[:-1]),
                    language=self.language,
                    confidence=self.confidence,
                    is_final=False,
                    started_at=started_at,
                )

        yield Transcript(
            text=utterance,
            language=self.language,
            confidence=self.confidence,
            is_final=True,
            started_at=started_at,
            ended_at=datetime.now(UTC),
        )


@dataclass(slots=True)
class ShellRecordingSession:
    """Recorder subprocess handle used by the shell capture service."""

    process: asyncio.subprocess.Process
    output_path: Path
    pcm_path: Path
    stdout_task: asyncio.Task[None]
    stderr_task: asyncio.Task[None]
    _stderr_chunks: list[str] = field(default_factory=list)
    _stop_requested: bool = False
    _bytes_received: int = 0

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    def mark_stop_requested(self) -> None:
        self._stop_requested = True

    async def stop(self) -> None:
        self.mark_stop_requested()
        if self.process.returncode is not None:
            await self._await_stream_tasks()
            return

        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()
        await self._await_stream_tasks()

    async def wait(self) -> int:
        result = await self.process.wait()
        await self._await_stream_tasks()
        return result

    async def _await_stream_tasks(self) -> None:
        await asyncio.gather(self.stdout_task, self.stderr_task, return_exceptions=True)

    def append_stderr(self, chunk: str) -> None:
        if chunk:
            self._stderr_chunks.append(chunk)

    def stderr_text(self) -> str:
        return _summarize_stderr("".join(self._stderr_chunks))

    @property
    def bytes_received(self) -> int:
        return self._bytes_received

    def note_chunk(self, chunk: bytes) -> None:
        if chunk:
            self._bytes_received += len(chunk)


@dataclass(slots=True)
class ShellAudioCaptureService:
    """Capture microphone audio by running a configured external recorder."""

    command_template: tuple[str, ...]
    output_dir: Path | None = None
    startup_poll_seconds: float = 0.05
    startup_timeout_seconds: float = 2.0
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2
    stream_format: str = "s16le"

    async def start_capture(self, on_chunk: Callable[[bytes], None] | None = None) -> RecordingSession:
        output_dir = self.output_dir or Path(tempfile.gettempdir())
        output_dir.mkdir(parents=True, exist_ok=True)
        pcm_path = output_dir / f"ai-companion-recording-{datetime.now(UTC).timestamp():.0f}.pcm"
        output_path = pcm_path.with_suffix(".wav")
        command = self._render_command(pcm_path)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if on_chunk is None:
            pcm_path.write_bytes(b"")
        session = ShellRecordingSession(
            process=process,
            output_path=output_path,
            pcm_path=pcm_path,
            stdout_task=asyncio.create_task(asyncio.sleep(0)),
            stderr_task=asyncio.create_task(asyncio.sleep(0)),
        )
        session.stdout_task = asyncio.create_task(self._capture_stdout(process, session, on_chunk=on_chunk))
        session.stderr_task = asyncio.create_task(self._capture_stderr(process, session))
        await self._wait_for_capture_data(session)
        return session

    async def capture_wav(self) -> Path:
        captured_pcm = bytearray()

        def collect_chunk(chunk: bytes) -> None:
            captured_pcm.extend(chunk)

        session = await self.start_capture(on_chunk=collect_chunk)
        await session.stop()
        return self.materialize_wav_bytes(bytes(captured_pcm), session.output_path)

    async def _wait_for_capture_data(self, session: ShellRecordingSession) -> None:
        deadline = asyncio.get_running_loop().time() + self.startup_timeout_seconds
        while True:
            if session.bytes_received > 0:
                return
            if session.returncode is not None:
                error_text = session.stderr_text() or await _read_stderr(session.process)
                raise RuntimeError(error_text or "audio capture exited before producing a WAV file")
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError("audio capture did not produce PCM data in time")
            await asyncio.sleep(self.startup_poll_seconds)

    def _render_command(self, output_path: Path) -> tuple[str, ...]:
        if not self.command_template:
            raise RuntimeError(
                "audio_record_command is not configured; provide a recorder command such as arecord, rec, or ffmpeg"
            )

        replacements = {
            "{output_path}": "-",
        }
        return tuple(_replace_many(token, replacements) for token in self.command_template)

    def materialize_wav(self, recording_path: Path) -> Path:
        wav_path = self.wav_artifact_path(recording_path)
        pcm_data = recording_path.read_bytes()
        return self.materialize_wav_bytes(pcm_data, wav_path)

    def materialize_wav_bytes(self, pcm_data: bytes, wav_path: Path) -> Path:
        _write_wav_file(
            wav_path,
            channels=self.channels,
            sample_width=self.sample_width,
            sample_rate=self.sample_rate,
            pcm_data=pcm_data,
        )
        return wav_path

    def wav_artifact_path(self, recording_path: Path) -> Path:
        return recording_path.with_suffix(".wav")

    async def _capture_stdout(
        self,
        process: asyncio.subprocess.Process,
        session: ShellRecordingSession,
        *,
        on_chunk: Callable[[bytes], None] | None = None,
    ) -> None:
        if process.stdout is None:
            return
        with session.pcm_path.open("ab") if on_chunk is None else contextlib.nullcontext() as handle:
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    break
                session.note_chunk(chunk)
                if on_chunk is not None:
                    on_chunk(chunk)
                    continue
                handle.write(chunk)
                handle.flush()

    async def _capture_stderr(self, process: asyncio.subprocess.Process, session: ShellRecordingSession) -> None:
        if process.stderr is None:
            return
        while True:
            chunk = await process.stderr.read(4096)
            if not chunk:
                break
            session.append_stderr(chunk.decode(errors="replace"))


def _replace_many(value: str, replacements: dict[str, str]) -> str:
    """Replace placeholder tokens inside a command template value."""

    rendered = value
    for key, replacement in replacements.items():
        rendered = rendered.replace(key, replacement)
    return rendered


@dataclass(slots=True, frozen=True)
class AudioWindow:
    """Current decoded PCM snapshot and speech activity estimate."""

    source_path: Path
    channels: int
    sample_width: int
    sample_rate: int
    pcm_data: bytes
    duration_seconds: float
    trailing_silence_seconds: float
    has_speech: bool
    current_energy: float
    peak_energy: float
    stream_start_offset: int = 0


def _audio_window_from_pcm(
    pcm_data: bytes,
    *,
    source_path: Path,
    channels: int,
    sample_width: int,
    sample_rate: int,
    threshold: int,
    stream_start_offset: int = 0,
) -> AudioWindow | None:
    bytes_per_frame = channels * sample_width
    if bytes_per_frame <= 0 or sample_rate <= 0 or not pcm_data:
        return None
    duration_seconds = len(pcm_data) / bytes_per_frame / sample_rate
    trailing_silence_seconds, current_energy, peak_energy = _measure_trailing_silence_seconds(
        pcm_data,
        sample_width=sample_width,
        channels=channels,
        sample_rate=sample_rate,
        threshold=threshold,
    )
    has_speech = peak_energy >= threshold and trailing_silence_seconds < duration_seconds
    return AudioWindow(
        source_path=source_path,
        channels=channels,
        sample_width=sample_width,
        sample_rate=sample_rate,
        pcm_data=pcm_data,
        duration_seconds=duration_seconds,
        trailing_silence_seconds=trailing_silence_seconds,
        has_speech=has_speech,
        current_energy=current_energy,
        peak_energy=peak_energy,
        stream_start_offset=stream_start_offset,
    )


def _slice_audio_window(
    audio_window: AudioWindow,
    start_offset_seconds: float,
    *,
    threshold: int,
) -> AudioWindow | None:
    bytes_per_frame = audio_window.channels * audio_window.sample_width
    if bytes_per_frame <= 0 or audio_window.sample_rate <= 0:
        return audio_window
    frame_offset = int(audio_window.sample_rate * max(0.0, start_offset_seconds))
    byte_offset = max(0, frame_offset * bytes_per_frame)
    if byte_offset <= 0:
        return audio_window
    return _audio_window_from_pcm(
        audio_window.pcm_data[byte_offset:],
        source_path=audio_window.source_path,
        channels=audio_window.channels,
        sample_width=audio_window.sample_width,
        sample_rate=audio_window.sample_rate,
        threshold=threshold,
        stream_start_offset=audio_window.stream_start_offset + byte_offset,
    )


def _seconds_to_byte_offset(
    *,
    seconds: float,
    channels: int,
    sample_width: int,
    sample_rate: int,
) -> int:
    bytes_per_frame = channels * sample_width
    if bytes_per_frame <= 0 or sample_rate <= 0:
        return 0
    return max(0, int(sample_rate * max(0.0, seconds)) * bytes_per_frame)


@dataclass(slots=True)
class WhisperCppSttService:
    """Streaming `whisper.cpp` adapter backed by CLI invocations."""

    audio_capture: AudioCaptureService
    model_path: Path
    binary_path: Path | None = None
    language_mode: str = "auto"
    runner: SubprocessRunner = _default_run_command
    command_extra_args: tuple[str, ...] = ()
    keep_recent_recordings: int = 5
    speech_silence_seconds: float = 1.2
    no_speech_timeout_seconds: float = 8.0
    quiet_abort_seconds: float = 2.5
    poll_interval_seconds: float = 0.35
    minimum_transcribe_seconds: float = 0.45
    partial_update_interval_seconds: float = 1.0
    minimum_utterance_seconds: float = 2.0
    utterance_end_grace_seconds: float = 0.25
    utterance_finalize_timeout_seconds: float = 0.6
    utterance_tail_stable_polls: int = 2
    silence_confirmation_polls: int = 1
    speech_energy_threshold: int = 60
    speech_start_energy_threshold: int = 120
    ring_debug_wake_window_seconds: float = 0.0
    ring_debug_stride_seconds: float = 0.0
    terminal_debug: TerminalDebugSink | None = None
    shared_live_state: SharedLiveSpeechState | None = None
    _primed_audio_window: AudioWindow | None = field(default=None, init=False, repr=False)

    def prime_wake_audio(self, audio_window: AudioWindow | None) -> None:
        """Seed the next speech turn with audio already captured during wake detection."""

        self._primed_audio_window = audio_window

    def begin_utterance(
        self,
        *,
        trigger: str,
        detection: WakeDetectionResult | None = None,
    ) -> None:
        """Start an utterance on the shared live stream without restarting capture."""

        if self.shared_live_state is None:
            if detection is not None and detection.audio_window is not None:
                self._primed_audio_window = _slice_audio_window(
                    detection.audio_window,
                    detection.utterance_start_offset_seconds,
                    threshold=self.speech_energy_threshold,
                )
            return

        initial_window = None
        if detection is not None and detection.audio_window is not None:
            initial_window = _slice_audio_window(
                detection.audio_window,
                detection.utterance_start_offset_seconds,
                threshold=self.speech_energy_threshold,
            )
        if trigger == "manual" and self.shared_live_state.utterance_active:
            self.shared_live_state.reset_utterance()
        self.shared_live_state.start_utterance(initial_window=initial_window)

    async def shutdown(self) -> None:
        """Stop any shared live microphone session owned by this service."""

        if self.shared_live_state is not None:
            await self.shared_live_state.close()

    async def listen_once(self) -> Transcript:
        async for transcript in self.stream_transcripts():
            if transcript.is_final:
                return transcript
        raise RuntimeError("stream_transcripts() completed without a final transcript")

    async def stream_transcripts(self) -> AsyncIterator[Transcript]:
        if not hasattr(self.audio_capture, "start_capture"):
            yield await self._transcribe_one_shot()
            return

        if self.shared_live_state is not None:
            async for transcript in self._stream_transcripts_shared():
                yield transcript
            return

        started_at = datetime.now(UTC)
        session = await self.audio_capture.start_capture()
        primed_audio_window = self._primed_audio_window
        self._primed_audio_window = None
        last_partial_text = ""
        speech_started = False
        silence_poll_count = 0
        last_duration_seconds = 0.0
        last_partial_request_at = 0.0
        partial_task: asyncio.Task[Transcript] | None = None

        try:
            while True:
                await asyncio.sleep(self.poll_interval_seconds)
                elapsed_seconds = (datetime.now(UTC) - started_at).total_seconds()
                partial_task, partial_transcript = await self._collect_partial_task(partial_task, last_partial_text)
                if partial_transcript is not None:
                    last_partial_text = partial_transcript.text
                    speech_started = True
                    logger.info("stt partial_ready text_len=%s", len(partial_transcript.text))
                    yield partial_transcript
                live_audio_path = getattr(session, "pcm_path", session.output_path)
                audio_window = self._read_audio_window(live_audio_path)
                audio_window = self._merge_audio_windows(primed_audio_window, audio_window)
                if audio_window is not None:
                    if audio_window.has_speech and audio_window.peak_energy >= self.speech_start_energy_threshold:
                        speech_started = True

                    duration_progressed = audio_window.duration_seconds > last_duration_seconds + 0.05
                    if duration_progressed:
                        last_duration_seconds = audio_window.duration_seconds

                    if (
                        audio_window.duration_seconds >= self.minimum_transcribe_seconds
                        and partial_task is None
                        and audio_window.peak_energy >= self.speech_start_energy_threshold
                        and audio_window.trailing_silence_seconds < self.speech_silence_seconds
                        and (datetime.now(UTC) - started_at).total_seconds() - last_partial_request_at
                        >= self.partial_update_interval_seconds
                    ):
                        logger.info(
                            "stt partial_requested bytes=%s duration=%.2f peak=%.1f silence=%.2f",
                            len(audio_window.pcm_data),
                            audio_window.duration_seconds,
                            audio_window.peak_energy,
                            audio_window.trailing_silence_seconds,
                        )
                        partial_task = asyncio.create_task(
                            self._transcribe_snapshot(audio_window, started_at, is_final=False)
                        )
                        last_partial_request_at = (datetime.now(UTC) - started_at).total_seconds()
                        self._publish_audio_status(
                            current_noise=audio_window.current_energy,
                            peak_energy=audio_window.peak_energy,
                            trailing_silence_seconds=(
                                audio_window.trailing_silence_seconds if speech_started else elapsed_seconds
                            ),
                            speech_started=speech_started,
                            partial_pending=True,
                        )

                    if (
                        speech_started
                        and audio_window.duration_seconds >= self.minimum_utterance_seconds
                        and audio_window.trailing_silence_seconds >= self.speech_silence_seconds
                        and duration_progressed
                    ):
                        silence_poll_count += 1
                    else:
                        silence_poll_count = 0

                    if silence_poll_count >= self.silence_confirmation_polls:
                        logger.info(
                            "stt stop_reason=confirmed_silence duration=%.2f peak=%.1f silence=%.2f",
                            audio_window.duration_seconds,
                            audio_window.peak_energy,
                            audio_window.trailing_silence_seconds,
                        )
                        if self.utterance_end_grace_seconds > 0:
                            await asyncio.sleep(self.utterance_end_grace_seconds)
                        break

                    logger.info(
                        "stt poll bytes=%s duration=%.2f peak=%.1f silence=%.2f speech_started=%s silence_polls=%s partial_pending=%s",
                        len(audio_window.pcm_data),
                        audio_window.duration_seconds,
                        audio_window.peak_energy,
                        audio_window.trailing_silence_seconds,
                        speech_started,
                        silence_poll_count,
                        partial_task is not None,
                    )
                    self._publish_audio_status(
                        current_noise=audio_window.current_energy,
                        peak_energy=audio_window.peak_energy,
                        trailing_silence_seconds=(
                            audio_window.trailing_silence_seconds if speech_started else elapsed_seconds
                        ),
                        speech_started=speech_started,
                        partial_pending=partial_task is not None,
                    )
                else:
                    logger.info("stt poll bytes=0 unreadable=true speech_started=%s", speech_started)
                    self._publish_audio_status(
                        current_noise=0.0,
                        trailing_silence_seconds=elapsed_seconds if not speech_started else None,
                        speech_started=speech_started,
                        partial_pending=partial_task is not None,
                    )

                if await self._capture_failed(session):
                    raise RuntimeError("audio capture failed while recording")

                if (
                    not speech_started
                    and audio_window is not None
                    and elapsed_seconds >= self.quiet_abort_seconds
                    and audio_window.peak_energy < self.speech_energy_threshold
                ):
                    logger.info(
                        "stt stop_reason=quiet_abort elapsed=%.2f peak=%.1f",
                        elapsed_seconds,
                        audio_window.peak_energy,
                    )
                    break
                if not speech_started and elapsed_seconds >= self.no_speech_timeout_seconds:
                    logger.info("stt stop_reason=no_speech_timeout elapsed=%.2f", elapsed_seconds)
                    break

            await session.stop()
            partial_task, partial_transcript = await self._collect_partial_task(partial_task, last_partial_text)
            if partial_transcript is not None:
                last_partial_text = partial_transcript.text
                speech_started = True
                logger.info("stt partial_ready text_len=%s", len(partial_transcript.text))
                yield partial_transcript
            live_audio_path = getattr(session, "pcm_path", session.output_path)
            final_audio_window = self._read_audio_window(live_audio_path)
            final_audio_window = self._merge_audio_windows(primed_audio_window, final_audio_window)
            ended_at = datetime.now(UTC)
            if final_audio_window is None or not final_audio_window.pcm_data:
                logger.info("stt final_audio empty=true")
                self._publish_audio_status(
                    current_noise=0.0,
                    peak_energy=0.0,
                    trailing_silence_seconds=0.0,
                    speech_started=speech_started,
                    partial_pending=False,
                )
                yield Transcript(
                    text="",
                    language=Language.ENGLISH,
                    confidence=1.0,
                    is_final=True,
                    started_at=started_at,
                    ended_at=ended_at,
                )
                return

            final_audio_path = self._persist_final_audio(final_audio_window)
            logger.info(
                "stt final_audio bytes=%s duration=%.2f peak=%.1f silence=%.2f path=%s",
                len(final_audio_window.pcm_data),
                final_audio_window.duration_seconds,
                final_audio_window.peak_energy,
                final_audio_window.trailing_silence_seconds,
                final_audio_path,
            )
            self._publish_audio_status(
                current_noise=final_audio_window.current_energy,
                peak_energy=final_audio_window.peak_energy,
                trailing_silence_seconds=final_audio_window.trailing_silence_seconds,
                speech_started=speech_started,
                partial_pending=False,
            )
            final_transcript = await self._transcribe_snapshot(final_audio_window, started_at, is_final=True)
            self._prune_recording_artifacts(final_audio_path)
            yield Transcript(
                text=final_transcript.text,
                language=final_transcript.language,
                confidence=final_transcript.confidence,
                is_final=True,
                started_at=started_at,
                ended_at=ended_at,
            )
        finally:
            if partial_task is not None and not partial_task.done():
                partial_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await partial_task
            with contextlib.suppress(ProcessLookupError):
                if session.returncode is None:
                    await session.stop()
            with contextlib.suppress(OSError):
                live_audio_path = getattr(session, "pcm_path", None)
                if live_audio_path is not None:
                    live_audio_path.unlink()
            self._publish_audio_status(
                current_noise=0.0,
                peak_energy=0.0,
                trailing_silence_seconds=0.0,
                speech_started=False,
                partial_pending=False,
            )
            self._publish_whisper_status(None)

    async def _stream_transcripts_shared(self) -> AsyncIterator[Transcript]:
        if self.shared_live_state is None:
            raise RuntimeError("shared transcript streaming requires shared live speech state")

        session = await self.shared_live_state.ensure_session()
        await self.shared_live_state.sync()
        self._publish_ring_buffer_state(self.ring_debug_wake_window_seconds, self.ring_debug_stride_seconds)
        if not self.shared_live_state.utterance_active:
            self.shared_live_state.start_utterance()
        started_at = self.shared_live_state.utterance_started_at or datetime.now(UTC)
        last_partial_text = ""
        speech_started = False
        silence_poll_count = 0
        last_partial_request_at = 0.0
        partial_task: asyncio.Task[Transcript] | None = None
        finalize_started_at: datetime | None = None
        stable_tail_polls = 0
        last_speech_endpoint_seconds = 0.0

        try:
            while True:
                await asyncio.sleep(self.poll_interval_seconds)
                await self.shared_live_state.sync()
                self._publish_ring_buffer_state(self.ring_debug_wake_window_seconds, self.ring_debug_stride_seconds)
                elapsed_seconds = (datetime.now(UTC) - started_at).total_seconds()
                partial_task, partial_transcript = await self._collect_partial_task(partial_task, last_partial_text)
                if partial_transcript is not None:
                    last_partial_text = partial_transcript.text
                    speech_started = True
                    yield partial_transcript

                audio_window = self.shared_live_state.current_utterance_window(
                    threshold=self.speech_energy_threshold,
                    source_path=getattr(session, "output_path", Path("shared-live-session.wav")),
                )
                if audio_window is not None:
                    if audio_window.has_speech and audio_window.peak_energy >= self.speech_start_energy_threshold:
                        speech_started = True

                    if (
                        audio_window.duration_seconds >= self.minimum_transcribe_seconds
                        and partial_task is None
                        and audio_window.peak_energy >= self.speech_start_energy_threshold
                        and audio_window.trailing_silence_seconds < self.speech_silence_seconds
                        and elapsed_seconds - last_partial_request_at >= self.partial_update_interval_seconds
                    ):
                        partial_task = asyncio.create_task(
                            self._transcribe_snapshot(audio_window, started_at, is_final=False)
                        )
                        last_partial_request_at = elapsed_seconds

                    if (
                        speech_started
                        and audio_window.duration_seconds >= self.minimum_utterance_seconds
                        and audio_window.trailing_silence_seconds >= self.speech_silence_seconds
                    ):
                        silence_poll_count += 1
                    else:
                        silence_poll_count = 0
                        if (
                            finalize_started_at is not None
                            and audio_window.trailing_silence_seconds < self.speech_silence_seconds
                        ):
                            finalize_started_at = None
                            stable_tail_polls = 0

                    speech_endpoint_seconds = max(
                        0.0,
                        audio_window.duration_seconds - audio_window.trailing_silence_seconds,
                    )
                    if finalize_started_at is None and silence_poll_count >= self.silence_confirmation_polls:
                        finalize_started_at = datetime.now(UTC)
                        stable_tail_polls = 0
                        last_speech_endpoint_seconds = speech_endpoint_seconds
                    elif finalize_started_at is not None:
                        if speech_endpoint_seconds > last_speech_endpoint_seconds + 0.05:
                            finalize_started_at = None
                            stable_tail_polls = 0
                            silence_poll_count = 0
                        else:
                            if speech_endpoint_seconds <= last_speech_endpoint_seconds + 0.02:
                                stable_tail_polls += 1
                            else:
                                stable_tail_polls = 0
                            last_speech_endpoint_seconds = max(last_speech_endpoint_seconds, speech_endpoint_seconds)
                            if (
                                stable_tail_polls >= max(1, self.utterance_tail_stable_polls)
                                or (datetime.now(UTC) - finalize_started_at).total_seconds()
                                >= self.utterance_finalize_timeout_seconds
                            ):
                                break

                    self._publish_audio_status(
                        current_noise=audio_window.current_energy,
                        peak_energy=audio_window.peak_energy,
                        trailing_silence_seconds=(
                            audio_window.trailing_silence_seconds if speech_started else elapsed_seconds
                        ),
                        speech_started=speech_started,
                        partial_pending=partial_task is not None,
                    )
                else:
                    self._publish_audio_status(
                        current_noise=0.0,
                        trailing_silence_seconds=elapsed_seconds if not speech_started else None,
                        speech_started=speech_started,
                        partial_pending=partial_task is not None,
                    )

                if await self._capture_failed(session):
                    raise RuntimeError("audio capture failed while recording")

                if (
                    not speech_started
                    and audio_window is not None
                    and elapsed_seconds >= self.quiet_abort_seconds
                    and audio_window.peak_energy < self.speech_energy_threshold
                ):
                    break
                if not speech_started and elapsed_seconds >= self.no_speech_timeout_seconds:
                    break

            partial_task, partial_transcript = await self._collect_partial_task(partial_task, last_partial_text)
            if partial_transcript is not None:
                last_partial_text = partial_transcript.text
                speech_started = True
                yield partial_transcript
            await self.shared_live_state.sync()
            self._publish_ring_buffer_state(self.ring_debug_wake_window_seconds, self.ring_debug_stride_seconds)
            final_audio_window = self.shared_live_state.finish_utterance(
                threshold=self.speech_energy_threshold,
                source_path=getattr(session, "output_path", Path("shared-live-session.wav")),
            )
            ended_at = datetime.now(UTC)
            if final_audio_window is None or not final_audio_window.pcm_data:
                self._publish_audio_status(
                    current_noise=0.0,
                    peak_energy=0.0,
                    trailing_silence_seconds=0.0,
                    speech_started=speech_started,
                    partial_pending=False,
                )
                yield Transcript(
                    text="",
                    language=Language.ENGLISH,
                    confidence=1.0,
                    is_final=True,
                    started_at=started_at,
                    ended_at=ended_at,
                )
                return

            final_audio_path = self._persist_final_audio(final_audio_window)
            self._publish_audio_status(
                current_noise=final_audio_window.current_energy,
                peak_energy=final_audio_window.peak_energy,
                trailing_silence_seconds=final_audio_window.trailing_silence_seconds,
                speech_started=speech_started,
                partial_pending=False,
            )
            final_transcript = await self._transcribe_snapshot(final_audio_window, started_at, is_final=True)
            self._prune_recording_artifacts(final_audio_path)
            yield Transcript(
                text=final_transcript.text,
                language=final_transcript.language,
                confidence=final_transcript.confidence,
                is_final=True,
                started_at=started_at,
                ended_at=ended_at,
            )
        finally:
            if partial_task is not None and not partial_task.done():
                partial_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await partial_task
            if self.shared_live_state is not None and self.shared_live_state.utterance_active:
                self.shared_live_state.reset_utterance()
            self._publish_ring_buffer_state(self.ring_debug_wake_window_seconds, self.ring_debug_stride_seconds)
            self._publish_audio_status(
                current_noise=0.0,
                peak_energy=0.0,
                trailing_silence_seconds=0.0,
                speech_started=False,
                partial_pending=False,
            )
            self._publish_whisper_status(None)

    async def _transcribe_one_shot(self) -> Transcript:
        audio_path = await self.audio_capture.capture_wav()
        started_at = datetime.now(UTC)
        transcript = await self._transcribe_file(audio_path, started_at, is_final=True)
        self._prune_recording_artifacts(audio_path)
        return transcript

    async def _capture_failed(self, session: RecordingSession) -> bool:
        return session.returncode not in (None, 0) and not session.stop_requested

    async def _collect_partial_task(
        self,
        partial_task: asyncio.Task[Transcript] | None,
        last_partial_text: str,
    ) -> tuple[asyncio.Task[Transcript] | None, Transcript | None]:
        if partial_task is None or not partial_task.done():
            return partial_task, None

        try:
            transcript = await partial_task
        except Exception:
            logger.exception("stt partial transcription failed")
            return None, None
        if transcript.text and transcript.text != last_partial_text:
            return None, transcript
        return None, None

    async def _transcribe_snapshot(
        self,
        audio_window: AudioWindow,
        started_at: datetime,
        *,
        is_final: bool,
    ) -> Transcript:
        transcript, _segments = await self._transcribe_snapshot_with_segments(
            audio_window,
            started_at,
            is_final=is_final,
        )
        return transcript

    async def _transcribe_snapshot_with_segments(
        self,
        audio_window: AudioWindow,
        started_at: datetime,
        *,
        is_final: bool,
    ) -> tuple[Transcript, tuple[WhisperSegment, ...]]:
        snapshot_path = self._write_snapshot_wav(audio_window)
        try:
            logger.info("stt whisper_snapshot is_final=%s path=%s bytes=%s", is_final, snapshot_path, len(audio_window.pcm_data))
            transcript, segments = await self._transcribe_file_with_segments(snapshot_path, started_at, is_final=is_final)
        finally:
            with contextlib.suppress(OSError):
                snapshot_path.unlink()
            for json_path in self._candidate_json_paths(snapshot_path.with_suffix("")):
                with contextlib.suppress(OSError):
                    json_path.unlink()
        return transcript, segments

    async def _transcribe_file(self, audio_path: Path, started_at: datetime, *, is_final: bool) -> Transcript:
        transcript, _segments = await self._transcribe_file_with_segments(audio_path, started_at, is_final=is_final)
        return transcript

    async def _transcribe_file_with_segments(
        self,
        audio_path: Path,
        started_at: datetime,
        *,
        is_final: bool,
    ) -> tuple[Transcript, tuple[WhisperSegment, ...]]:
        output_path = audio_path.with_suffix("")
        command = self._build_command(audio_path, output_path)
        logger.info("stt whisper_start is_final=%s path=%s", is_final, audio_path)
        whisper_started_at = datetime.now(UTC)
        self._publish_whisper_status("running")
        try:
            result = await self.runner(command)
            ended_at = datetime.now(UTC)
            if result.returncode != 0:
                error_text = result.stderr.strip() or result.stdout.strip() or "whisper.cpp transcription failed"
                raise RuntimeError(error_text)
            logger.info("stt whisper_done is_final=%s stdout_len=%s", is_final, len(result.stdout))
            elapsed_seconds = max(0.0, (ended_at - whisper_started_at).total_seconds())
            self._publish_whisper_status(f"{elapsed_seconds:0.2f}s")
        except Exception:
            self._publish_whisper_status(None)
            raise
        transcript_json = self._load_transcript_json(output_path, result.stdout)
        data = _extract_json_payload(transcript_json)
        transcript = self._parse_transcript_payload(data, started_at, ended_at, is_final=is_final)
        return transcript, _extract_whisper_segments(data)

    def _publish_audio_status(
        self,
        *,
        current_noise: float | None = None,
        peak_energy: float | None = None,
        trailing_silence_seconds: float | None = None,
        speech_started: bool | None = None,
        partial_pending: bool | None = None,
    ) -> None:
        if self.terminal_debug is None:
            return
        self.terminal_debug.update_audio(
            current_noise=current_noise,
            peak_energy=peak_energy,
            trailing_silence_seconds=trailing_silence_seconds,
            speech_started=speech_started,
            partial_pending=partial_pending,
        )

    def _publish_ring_buffer_state(self, wake_window_seconds: float, stride_seconds: float) -> None:
        if self.terminal_debug is None or self.shared_live_state is None:
            return
        capacity_seconds, filled_seconds, wake_window, utterance_start, write_head = self.shared_live_state.ring_buffer_debug_state(
            wake_window_seconds=wake_window_seconds
        )
        self.terminal_debug.update_ring_buffer(
            capacity_seconds=capacity_seconds,
            filled_seconds=filled_seconds,
            wake_window_seconds=wake_window,
            stride_seconds=stride_seconds,
            utterance_start_seconds=utterance_start,
            write_head_seconds=write_head,
        )

    def _publish_whisper_status(self, status: str | None) -> None:
        if self.terminal_debug is not None:
            self.terminal_debug.update_whisper_status(status)
            return
        if status is None:
            return
        _emit_whisper_terminal_status(status)

    def _merge_audio_windows(
        self,
        primed_audio_window: AudioWindow | None,
        live_audio_window: AudioWindow | None,
    ) -> AudioWindow | None:
        if primed_audio_window is None:
            return live_audio_window
        if live_audio_window is None:
            return primed_audio_window
        if (
            primed_audio_window.channels != live_audio_window.channels
            or primed_audio_window.sample_width != live_audio_window.sample_width
            or primed_audio_window.sample_rate != live_audio_window.sample_rate
        ):
            return live_audio_window

        pcm_data = primed_audio_window.pcm_data + live_audio_window.pcm_data
        bytes_per_frame = live_audio_window.channels * live_audio_window.sample_width
        duration_seconds = len(pcm_data) / bytes_per_frame / live_audio_window.sample_rate
        trailing_silence_seconds, current_energy, peak_energy = _measure_trailing_silence_seconds(
            pcm_data,
            sample_width=live_audio_window.sample_width,
            channels=live_audio_window.channels,
            sample_rate=live_audio_window.sample_rate,
            threshold=self.speech_energy_threshold,
        )
        has_speech = peak_energy >= self.speech_energy_threshold and trailing_silence_seconds < duration_seconds
        return AudioWindow(
            source_path=live_audio_window.source_path,
            channels=live_audio_window.channels,
            sample_width=live_audio_window.sample_width,
            sample_rate=live_audio_window.sample_rate,
            pcm_data=pcm_data,
            duration_seconds=duration_seconds,
            trailing_silence_seconds=trailing_silence_seconds,
            has_speech=has_speech,
            current_energy=current_energy,
            peak_energy=peak_energy,
            stream_start_offset=primed_audio_window.stream_start_offset,
        )

    def _build_command(self, audio_path: Path, output_path: Path) -> tuple[str, ...]:
        if self.binary_path is None:
            raise RuntimeError("whisper binary path is not configured")

        command = [
            str(self.binary_path),
            "-m",
            str(self.model_path),
            "-f",
            str(audio_path),
            "--output-json",
            "--output-file",
            str(output_path),
            "-l",
            self.language_mode,
        ]
        command.extend(self.command_extra_args)
        return tuple(command)

    def _load_transcript_json(self, output_path: Path, stdout: str) -> str:
        """Load whisper output from the generated JSON file or stdout fallback."""

        for json_path in self._candidate_json_paths(output_path):
            if json_path.exists():
                return json_path.read_text()

        return stdout

    def _candidate_json_paths(self, output_path: Path) -> tuple[Path, ...]:
        """Support both `output.json` and `output.wav.json` whisper output names."""

        return (
            output_path.with_suffix(".json"),
            Path(f"{output_path}.json"),
            Path(f"{output_path}.wav.json"),
        )

    def _prune_recording_artifacts(self, latest_audio_path: Path) -> None:
        """Keep a small rolling history of recent WAV/JSON debugging artifacts."""

        if self.keep_recent_recordings <= 0:
            return

        pattern = "ai-companion-recording-*.wav"
        audio_paths = sorted(
            latest_audio_path.parent.glob(pattern),
            key=self._recording_recency_key,
            reverse=True,
        )
        for stale_audio_path in audio_paths[self.keep_recent_recordings :]:
            with contextlib.suppress(OSError):
                stale_audio_path.unlink()

            for json_path in self._recording_json_paths(stale_audio_path):
                with contextlib.suppress(OSError):
                    json_path.unlink()

    def _recording_recency_key(self, audio_path: Path) -> tuple[int, float, float, str]:
        """Build a stable ordering key for rolling recording artifact cleanup."""

        match = re.search(r"ai-companion-recording-(.+)\.wav$", audio_path.name)
        identifier = match.group(1) if match else ""
        parsed_identifier: float | None = None
        with contextlib.suppress(ValueError):
            parsed_identifier = float(identifier)

        with contextlib.suppress(OSError):
            mtime = audio_path.stat().st_mtime
            if parsed_identifier is not None:
                return (1, parsed_identifier, mtime, audio_path.name)
            return (0, mtime, mtime, audio_path.name)
        if parsed_identifier is not None:
            return (1, parsed_identifier, 0.0, audio_path.name)
        return (0, 0.0, 0.0, audio_path.name)

    def _recording_json_paths(self, audio_path: Path) -> tuple[Path, ...]:
        """Return all JSON sidecar path variants that may exist for a WAV recording."""

        return (
            audio_path.with_suffix(".json"),
            Path(f"{audio_path}.json"),
        )

    def _parse_transcript_payload(
        self,
        data: dict[str, object],
        started_at: datetime,
        ended_at: datetime,
        *,
        is_final: bool,
    ) -> Transcript:
        transcript_text = _extract_transcript_text(data)
        result = data.get("result")
        language_code = result.get("language") if isinstance(result, dict) else None
        language = _map_language_code(language_code)
        return Transcript(
            text=transcript_text,
            language=language,
            confidence=1.0,
            is_final=is_final,
            started_at=started_at,
            ended_at=ended_at if is_final else None,
        )

    def _read_audio_window(self, audio_path: Path) -> AudioWindow | None:
        try:
            pcm_header = self._read_pcm_source(audio_path)
        except (OSError, ValueError):
            return None
        return _audio_window_from_pcm(
            pcm_header.pcm_data,
            source_path=audio_path,
            channels=pcm_header.channels,
            sample_width=pcm_header.sample_width,
            sample_rate=pcm_header.sample_rate,
            threshold=self.speech_energy_threshold,
        )

    def _read_pcm_source(self, audio_path: Path) -> _WavHeader:
        if getattr(self.audio_capture, "stream_format", None) == "s16le":
            return _read_raw_pcm(
                audio_path,
                channels=getattr(self.audio_capture, "channels", 1),
                sample_width=getattr(self.audio_capture, "sample_width", 2),
                sample_rate=getattr(self.audio_capture, "sample_rate", 16000),
            )
        return _read_wav_header(audio_path)

    def _write_snapshot_wav(self, audio_window: AudioWindow) -> Path:
        output_dir = audio_window.source_path.parent
        snapshot_name = (
            f"{audio_window.source_path.stem}-snapshot-"
            f"{os.getpid()}-{datetime.now(UTC).timestamp():.6f}.wav"
        )
        snapshot_path = output_dir / snapshot_name
        _write_wav_file(
            snapshot_path,
            channels=audio_window.channels,
            sample_width=audio_window.sample_width,
            sample_rate=audio_window.sample_rate,
            pcm_data=audio_window.pcm_data,
        )
        return snapshot_path

    def _persist_final_audio(self, audio_window: AudioWindow) -> Path:
        if self.shared_live_state is not None:
            wav_path = self._new_recording_artifact_path(audio_window.source_path.parent)
        elif hasattr(self.audio_capture, "wav_artifact_path"):
            wav_path = self.audio_capture.wav_artifact_path(audio_window.source_path)
        else:
            wav_path = audio_window.source_path.with_suffix(".wav")
        _write_wav_file(
            wav_path,
            channels=audio_window.channels,
            sample_width=audio_window.sample_width,
            sample_rate=audio_window.sample_rate,
            pcm_data=audio_window.pcm_data,
        )
        return wav_path

    def _new_recording_artifact_path(self, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        while True:
            timestamp = datetime.now(UTC).timestamp()
            candidate = output_dir / f"ai-companion-recording-{timestamp:.6f}.wav"
            if not candidate.exists():
                return candidate


@dataclass(slots=True)
class WhisperCppWakeWordService(WhisperCppSttService):
    """Bounded wake-word detector using overlapping audio windows."""

    wake_phrase: str = ""
    wake_window_seconds: float = 1.5
    wake_stride_seconds: float = 0.5
    _wake_session: RecordingSession | None = field(default=None, init=False, repr=False)
    _wake_carry_window: AudioWindow | None = field(default=None, init=False, repr=False)

    async def wait_for_wake_word(self) -> WakeDetectionResult:
        wake_phrase = self.wake_phrase.strip()
        if not wake_phrase:
            return WakeDetectionResult(detected=True)

        if self.shared_live_state is not None:
            return await self._wait_for_wake_word_shared(wake_phrase)

        self._publish_wake_status("listening", wake_phrase)
        try:
            while True:
                next_window = await self._capture_bounded_window(
                    self.wake_window_seconds if self._wake_session is None and self._wake_carry_window is None else self.wake_stride_seconds
                )
                if next_window is None or not next_window.pcm_data:
                    continue

                transcript, segments = await self._transcribe_snapshot_with_segments(
                    next_window,
                    datetime.now(UTC),
                    is_final=True,
                )
                self._publish_wake_transcript(transcript)
                stripped_text = strip_wake_phrase(transcript.text, wake_phrase)
                if stripped_text is None:
                    self._publish_wake_status("listening", wake_phrase)
                    continue

                utterance_start_offset = _wake_phrase_start_offset_seconds(
                    transcript.text,
                    wake_phrase,
                    segments,
                    pre_roll_seconds=0.2,
                )
                self._publish_wake_status("awake", wake_phrase)
                return WakeDetectionResult(
                    detected=True,
                    matched_transcript=transcript.text,
                    prefilled_command_text=stripped_text,
                    audio_window=next_window,
                    utterance_start_offset_seconds=utterance_start_offset,
                    segments=segments,
                )
        finally:
            await self._close_wake_session()

    async def _wait_for_wake_word_shared(self, wake_phrase: str) -> WakeDetectionResult:
        if self.shared_live_state is None:
            raise RuntimeError("shared wake detection requires shared live speech state")

        await self.shared_live_state.ensure_session()
        self._publish_ring_buffer_state(self.wake_window_seconds, self.wake_stride_seconds)
        self._publish_wake_status("listening", wake_phrase)
        initial_delay_seconds = self.wake_window_seconds
        try:
            while True:
                await asyncio.sleep(initial_delay_seconds)
                initial_delay_seconds = self.wake_stride_seconds
                await self.shared_live_state.sync()
                self._publish_ring_buffer_state(self.wake_window_seconds, self.wake_stride_seconds)
                audio_window = self.shared_live_state.current_wake_window(
                    duration_seconds=self.wake_window_seconds,
                    threshold=self.speech_energy_threshold,
                )
                self._publish_wake_audio(audio_window, self.wake_window_seconds)
                if audio_window is None or not audio_window.pcm_data:
                    continue

                transcript, segments = await self._transcribe_snapshot_with_segments(
                    audio_window,
                    datetime.now(UTC),
                    is_final=True,
                )
                self._publish_wake_transcript(transcript)
                stripped_text = strip_wake_phrase(transcript.text, wake_phrase)
                if stripped_text is None:
                    self._publish_wake_status("listening", wake_phrase)
                    continue

                utterance_start_offset = _wake_phrase_start_offset_seconds(
                    transcript.text,
                    wake_phrase,
                    segments,
                    pre_roll_seconds=0.2,
                )
                utterance_start_stream_offset = audio_window.stream_start_offset + _seconds_to_byte_offset(
                    seconds=utterance_start_offset,
                    channels=audio_window.channels,
                    sample_width=audio_window.sample_width,
                    sample_rate=audio_window.sample_rate,
                )
                self.shared_live_state.start_utterance(stream_start_offset=utterance_start_stream_offset)
                self._publish_ring_buffer_state(self.wake_window_seconds, self.wake_stride_seconds)
                self._publish_wake_status("awake", wake_phrase)
                return WakeDetectionResult(
                    detected=True,
                    matched_transcript=transcript.text,
                    prefilled_command_text=stripped_text,
                    audio_window=audio_window,
                    utterance_start_offset_seconds=utterance_start_offset,
                    segments=segments,
                )
        except asyncio.CancelledError:
            self._publish_wake_status("listening", wake_phrase)
            raise

    async def _capture_bounded_window(self, duration_seconds: float) -> AudioWindow | None:
        if not hasattr(self.audio_capture, "start_capture"):
            audio_path = await self.audio_capture.capture_wav()
            audio_window = self._read_audio_window(audio_path)
            self._publish_wake_audio(audio_window, duration_seconds)
            return audio_window

        if self._wake_session is None:
            self._wake_session = await self.audio_capture.start_capture()
        session = self._wake_session
        started_at = datetime.now(UTC)
        try:
            remaining_seconds = max(0.0, duration_seconds)
            while remaining_seconds > 0:
                sleep_seconds = min(self.poll_interval_seconds, remaining_seconds)
                await asyncio.sleep(sleep_seconds)
                remaining_seconds -= sleep_seconds
                live_audio_path = getattr(session, "pcm_path", session.output_path)
                self._publish_wake_audio(
                    self._read_audio_window(live_audio_path),
                    (datetime.now(UTC) - started_at).total_seconds(),
                )
            live_audio_path = getattr(session, "pcm_path", session.output_path)
            audio_window = self._read_audio_window(live_audio_path)
            self._publish_wake_audio(audio_window, duration_seconds)
            recent_window = self._recent_audio_window(audio_window, self.wake_window_seconds)
            detection_window = self._select_detection_window(self._wake_carry_window, recent_window)
            if audio_window is not None and audio_window.duration_seconds >= self._max_wake_buffer_seconds():
                self._wake_carry_window = recent_window
                await self._restart_wake_session()
            return detection_window
        except Exception:
            await self._close_wake_session()
            raise

    def _merge_overlapping_windows(
        self,
        previous_window: AudioWindow | None,
        next_window: AudioWindow | None,
    ) -> AudioWindow | None:
        if next_window is None:
            return previous_window
        if previous_window is None:
            return next_window
        if (
            previous_window.channels != next_window.channels
            or previous_window.sample_width != next_window.sample_width
            or previous_window.sample_rate != next_window.sample_rate
        ):
            return next_window

        prefix = self._tail_pcm(previous_window, max(0.0, self.wake_window_seconds - self.wake_stride_seconds))
        combined_window = AudioWindow(
            source_path=next_window.source_path,
            channels=next_window.channels,
            sample_width=next_window.sample_width,
            sample_rate=next_window.sample_rate,
            pcm_data=prefix + next_window.pcm_data,
            duration_seconds=0.0,
            trailing_silence_seconds=0.0,
            has_speech=False,
            current_energy=0.0,
            peak_energy=0.0,
            stream_start_offset=max(
                previous_window.stream_start_offset,
                previous_window.stream_start_offset + len(previous_window.pcm_data) - len(prefix),
            ),
        )
        return self._rebuild_audio_window(combined_window)

    def _select_detection_window(
        self,
        previous_window: AudioWindow | None,
        next_window: AudioWindow | None,
    ) -> AudioWindow | None:
        if next_window is None:
            return previous_window
        if previous_window is None:
            return next_window
        merged_window = self._merge_overlapping_windows(previous_window, next_window)
        return self._recent_audio_window(merged_window, self.wake_window_seconds)

    def _tail_pcm(self, audio_window: AudioWindow, duration_seconds: float) -> bytes:
        bytes_per_frame = audio_window.channels * audio_window.sample_width
        frame_count = int(audio_window.sample_rate * max(0.0, duration_seconds))
        byte_count = frame_count * bytes_per_frame
        if byte_count <= 0:
            return b""
        return audio_window.pcm_data[-byte_count:]

    def _rebuild_audio_window(self, audio_window: AudioWindow) -> AudioWindow | None:
        return _audio_window_from_pcm(
            audio_window.pcm_data,
            source_path=audio_window.source_path,
            channels=audio_window.channels,
            sample_width=audio_window.sample_width,
            sample_rate=audio_window.sample_rate,
            threshold=self.speech_energy_threshold,
            stream_start_offset=audio_window.stream_start_offset,
        )

    def _recent_audio_window(self, audio_window: AudioWindow | None, duration_seconds: float) -> AudioWindow | None:
        if audio_window is None:
            return None
        bytes_per_frame = audio_window.channels * audio_window.sample_width
        if bytes_per_frame <= 0 or audio_window.sample_rate <= 0:
            return audio_window
        frame_count = int(audio_window.sample_rate * max(0.0, duration_seconds))
        byte_count = frame_count * bytes_per_frame
        if byte_count <= 0 or len(audio_window.pcm_data) <= byte_count:
            return audio_window
        trimmed_window = AudioWindow(
            source_path=audio_window.source_path,
            channels=audio_window.channels,
            sample_width=audio_window.sample_width,
            sample_rate=audio_window.sample_rate,
            pcm_data=audio_window.pcm_data[-byte_count:],
            duration_seconds=0.0,
            trailing_silence_seconds=0.0,
            has_speech=False,
            current_energy=0.0,
            peak_energy=0.0,
            stream_start_offset=audio_window.stream_start_offset + max(0, len(audio_window.pcm_data) - byte_count),
        )
        return self._rebuild_audio_window(trimmed_window)

    async def _close_wake_session(self) -> None:
        session = self._wake_session
        self._wake_session = None
        if session is None:
            self._wake_carry_window = None
            return
        with contextlib.suppress(ProcessLookupError):
            if session.returncode is None:
                await session.stop()
        with contextlib.suppress(OSError):
            live_audio_path = getattr(session, "pcm_path", None)
            if live_audio_path is not None:
                live_audio_path.unlink()
        self._wake_carry_window = None

    async def _restart_wake_session(self) -> None:
        session = self._wake_session
        self._wake_session = None
        if session is None:
            return
        with contextlib.suppress(ProcessLookupError):
            if session.returncode is None:
                await session.stop()
        with contextlib.suppress(OSError):
            live_audio_path = getattr(session, "pcm_path", None)
            if live_audio_path is not None:
                live_audio_path.unlink()

    def _max_wake_buffer_seconds(self) -> float:
        return max(self.wake_window_seconds * 2.0, self.wake_window_seconds + self.wake_stride_seconds)

    def _publish_wake_status(self, status: str, detail: str | None = None) -> None:
        if self.terminal_debug is None:
            return
        self.terminal_debug.update_wake_status(status, detail)

    def _publish_wake_transcript(self, transcript: Transcript) -> None:
        if self.terminal_debug is None:
            return
        self.terminal_debug.update_transcript(
            transcript.text or "...",
            language=transcript.language.value,
            is_final=False,
        )

    def _publish_wake_audio(self, audio_window: AudioWindow | None, elapsed_seconds: float) -> None:
        if audio_window is None:
            self._publish_audio_status(
                current_noise=0.0,
                peak_energy=0.0,
                trailing_silence_seconds=elapsed_seconds,
                speech_started=False,
                partial_pending=False,
            )
            return
        self._publish_audio_status(
            current_noise=audio_window.current_energy,
            peak_energy=audio_window.peak_energy,
            trailing_silence_seconds=audio_window.trailing_silence_seconds,
            speech_started=audio_window.has_speech,
            partial_pending=False,
        )


@dataclass(slots=True, frozen=True)
class _WavHeader:
    channels: int
    sample_width: int
    sample_rate: int
    pcm_data: bytes


def _read_wav_header(audio_path: Path) -> _WavHeader:
    data = audio_path.read_bytes()
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("unsupported WAV header")

    fmt_offset = None
    fmt_size = 0
    data_offset = None
    data_size = None
    cursor = 12
    while cursor + 8 <= len(data):
        chunk_id = data[cursor : cursor + 4]
        chunk_size = struct.unpack("<I", data[cursor + 4 : cursor + 8])[0]
        chunk_start = cursor + 8
        chunk_end = chunk_start + chunk_size
        if chunk_end > len(data):
            chunk_end = len(data)
        if chunk_id == b"fmt ":
            fmt_offset = chunk_start
            fmt_size = chunk_size
        elif chunk_id == b"data":
            data_offset = chunk_start
            data_size = chunk_size
            break
        cursor = chunk_start + chunk_size + (chunk_size % 2)

    if fmt_offset is None or fmt_size < 16 or data_offset is None or data_size is None:
        raise ValueError("missing PCM WAV metadata")

    audio_format, channels, sample_rate, _, _, bits_per_sample = struct.unpack(
        "<HHIIHH",
        data[fmt_offset : fmt_offset + 16],
    )
    if audio_format != 1:
        raise ValueError("only PCM WAV input is supported")
    sample_width = bits_per_sample // 8
    return _WavHeader(
        channels=channels,
        sample_width=sample_width,
        sample_rate=sample_rate,
        pcm_data=data[data_offset : data_offset + data_size],
    )


def _read_raw_pcm(
    audio_path: Path,
    *,
    channels: int,
    sample_width: int,
    sample_rate: int,
) -> _WavHeader:
    return _WavHeader(
        channels=channels,
        sample_width=sample_width,
        sample_rate=sample_rate,
        pcm_data=audio_path.read_bytes(),
    )


def _write_wav_file(
    path: Path,
    *,
    channels: int,
    sample_width: int,
    sample_rate: int,
    pcm_data: bytes,
) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)


def _measure_trailing_silence_seconds(
    pcm_data: bytes,
    *,
    sample_width: int,
    channels: int,
    sample_rate: int,
    threshold: int,
) -> tuple[float, float, float]:
    bytes_per_frame = sample_width * channels
    if bytes_per_frame <= 0 or sample_rate <= 0:
        return 0.0, 0.0, 0.0

    window_frames = max(1, int(sample_rate * 0.1))
    window_bytes = window_frames * bytes_per_frame
    energies: list[float] = []

    for start in range(0, len(pcm_data), window_bytes):
        window = pcm_data[start : start + window_bytes]
        if not window:
            continue
        energies.append(_window_energy(window, sample_width=sample_width))

    if not energies:
        return 0.0, 0.0, 0.0

    current_energy = energies[-1]
    peak_energy = max(energies)
    adaptive_threshold = max(12.0, min(float(threshold), peak_energy * 0.35))
    silence_windows = 0

    for energy in reversed(energies):
        if energy >= adaptive_threshold:
            break
        silence_windows += 1

    return silence_windows * 0.1, current_energy, peak_energy


def _window_energy(window: bytes, *, sample_width: int) -> float:
    if sample_width != 2 or not window:
        return 0.0
    sample_count = len(window) // sample_width
    if sample_count == 0:
        return 0.0

    samples = struct.unpack("<" + "h" * sample_count, window[: sample_count * sample_width])
    total = sum(abs(sample) for sample in samples)
    return total / sample_count


async def _read_stderr(process: asyncio.subprocess.Process) -> str:
    if process.stderr is None:
        return ""
    if process.returncode is not None:
        data = await process.stderr.read()
        return _summarize_stderr(data.decode())
    try:
        data = await asyncio.wait_for(process.stderr.read(), timeout=0.1)
    except asyncio.TimeoutError:
        return ""
    return _summarize_stderr(data.decode())


def _summarize_stderr(stderr: str) -> str:
    """Trim noisy recorder output to the most actionable lines."""

    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return ""

    ffmpeg_lines = [line for line in lines if not line.lower().startswith("ffmpeg version ")]
    if ffmpeg_lines:
        lines = ffmpeg_lines

    return "\n".join(lines[-4:])


def _extract_json_payload(stdout: str) -> dict[str, object]:
    """Parse stdout that may contain logs plus a final JSON document."""

    text = stdout.strip()
    if not text:
        raise RuntimeError("whisper.cpp returned no output")

    for start_index in range(len(text)):
        if text[start_index] != "{":
            continue
        try:
            parsed = json.loads(text[start_index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise RuntimeError("unable to parse whisper.cpp JSON output")


def _extract_transcript_text(data: dict[str, object]) -> str:
    """Support a couple of plausible whisper.cpp JSON result shapes."""

    result = data.get("result")
    if isinstance(result, dict):
        text = result.get("text")
        if isinstance(text, str):
            return text.strip()

        segments = result.get("segments")
        if isinstance(segments, list):
            pieces = []
            for segment in segments:
                if isinstance(segment, dict):
                    segment_text = segment.get("text")
                    if isinstance(segment_text, str):
                        pieces.append(segment_text.strip())
            if pieces:
                return " ".join(piece for piece in pieces if piece).strip()
            return ""

    transcription = data.get("transcription")
    if isinstance(transcription, str):
        return transcription.strip()
    if isinstance(transcription, list):
        pieces = []
        for item in transcription:
            if isinstance(item, dict):
                item_text = item.get("text")
                if isinstance(item_text, str):
                    pieces.append(item_text.strip())
        if pieces:
            return " ".join(piece for piece in pieces if piece).strip()
        return ""

    raise RuntimeError("whisper.cpp JSON output did not include transcript text")


def _extract_whisper_segments(data: dict[str, object]) -> tuple[WhisperSegment, ...]:
    result = data.get("result")
    if not isinstance(result, dict):
        return ()
    segments = result.get("segments")
    if not isinstance(segments, list):
        return ()

    parsed_segments: list[WhisperSegment] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = segment.get("text")
        start = segment.get("t0", segment.get("start"))
        end = segment.get("t1", segment.get("end"))
        if not isinstance(text, str):
            continue
        start_seconds = _normalize_segment_timestamp(start)
        end_seconds = _normalize_segment_timestamp(end)
        parsed_segments.append(
            WhisperSegment(
                start_seconds=start_seconds,
                end_seconds=max(start_seconds, end_seconds),
                text=text.strip(),
            )
        )
    return tuple(parsed_segments)


def _normalize_segment_timestamp(value: object) -> float:
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 100:
            return numeric / 100.0
        return numeric
    return 0.0


def _wake_phrase_start_offset_seconds(
    transcript_text: str,
    wake_phrase: str,
    segments: Sequence[WhisperSegment],
    *,
    pre_roll_seconds: float,
) -> float:
    phrase_words = _normalized_phrase_words(wake_phrase)
    if not phrase_words:
        return 0.0
    if not segments:
        return 0.0

    cumulative_words: list[str] = []
    for segment in segments:
        cumulative_words.extend(_normalized_phrase_words(segment.text))
        for index in range(len(cumulative_words) - len(phrase_words) + 1):
            if cumulative_words[index : index + len(phrase_words)] != phrase_words:
                continue
            return max(0.0, segment.start_seconds - pre_roll_seconds)

    if strip_wake_phrase(transcript_text, wake_phrase) is not None:
        return max(0.0, segments[0].start_seconds - pre_roll_seconds)
    return 0.0


def strip_wake_phrase(text: str, wake_phrase: str) -> str | None:
    """Return transcript text without the first matching wake phrase, if present."""

    phrase_words = _normalized_phrase_words(wake_phrase)
    if not phrase_words:
        return None

    text_words = text.split()
    normalized_words = [_normalize_spoken_token(word) for word in text_words]
    for index in range(len(normalized_words) - len(phrase_words) + 1):
        if normalized_words[index : index + len(phrase_words)] != phrase_words:
            continue
        remainder_words = text_words[index + len(phrase_words) :]
        return " ".join(remainder_words).strip()
    return None


def _normalized_phrase_words(text: str) -> list[str]:
    return [token for token in (_normalize_spoken_token(word) for word in text.split()) if token]


def _normalize_spoken_token(token: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", token.casefold())


def _map_language_code(code: object) -> Language:
    """Map a whisper language code to the project's supported enum."""

    if code == Language.GERMAN.value:
        return Language.GERMAN
    if code == Language.INDONESIAN.value:
        return Language.INDONESIAN
    return Language.ENGLISH
