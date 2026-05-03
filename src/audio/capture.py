"""Realtime microphone capture and shared live audio buffering."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import struct
import tempfile
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from audio.respeaker_capture import InterleavedChannelExtractor

logger = logging.getLogger(__name__)


class AudioCaptureService(Protocol):
    """Interface for capturing microphone audio."""

    async def capture_wav(self) -> Path:
        """Compatibility one-shot capture path."""


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
class CommandResult:
    """Minimal subprocess result used for dependency injection in tests."""

    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


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
    last_vad_speech_offset_seconds: float = 0.0
    trailing_non_speech_seconds: float = 0.0
    has_vad_speech: bool = False
    vad_active: bool = False
    stream_start_offset: int = 0


@dataclass(slots=True)
class SharedLiveSpeechState:
    """One live microphone session shared by wake listening and realtime streaming."""

    audio_capture: AudioCaptureService
    wake_buffer_seconds: float
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2
    session_recording_enabled: bool = False
    session_recording_dir: Path | None = None
    session_recording_keep_count: int = 5
    session: RecordingSession | None = field(default=None, init=False, repr=False)
    utterance_active: bool = field(default=False, init=False)
    utterance_started_at: datetime | None = field(default=None, init=False)
    utterance_buffer: bytearray = field(default_factory=bytearray, init=False, repr=False)
    utterance_stream_start_offset: int | None = field(default=None, init=False, repr=False)
    _wake_buffer: RollingAudioBuffer = field(init=False, repr=False)
    _source_offset_bytes: int = field(default=0, init=False, repr=False)
    _callback_driven: bool = field(default=False, init=False, repr=False)
    _source_path: Path | None = field(default=None, init=False, repr=False)
    _chunk_listeners: list[Callable[[bytes, int], None]] = field(default_factory=list, init=False, repr=False)
    _session_recording_writer: wave.Wave_write | None = field(default=None, init=False, repr=False)
    _session_recording_path: Path | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        bytes_per_second = max(1, self.channels * self.sample_width * self.sample_rate)
        self._wake_buffer = RollingAudioBuffer(max(1, int(bytes_per_second * max(0.5, self.wake_buffer_seconds))))

    async def ensure_session(self) -> RecordingSession:
        if self.session is not None:
            return self.session
        if not hasattr(self.audio_capture, "start_capture"):
            raise RuntimeError("shared live audio state requires streaming audio capture")

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
        initial_window: AudioWindow | None = None,
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
    ) -> AudioWindow | None:
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

    @property
    def wake_buffer_start_offset(self) -> int:
        return self._wake_buffer.start_offset

    def current_utterance_window(self, *, threshold: int, source_path: Path | None = None) -> AudioWindow | None:
        return self._build_window(
            bytes(self.utterance_buffer),
            threshold=threshold,
            source_path=source_path,
            stream_start_offset=self.utterance_stream_start_offset or 0,
        )

    def ring_buffer_debug_state(self, *, wake_window_seconds: float) -> tuple[float, float, float, float | None, float]:
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
        write_head_seconds = (self._wake_buffer.end_offset % self._wake_buffer.max_bytes) / bytes_per_second
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
        try:
            if session is not None:
                with contextlib.suppress(ProcessLookupError):
                    if session.returncode is None:
                        await session.stop()
        finally:
            self._close_session_recording()
            self._wake_buffer.clear()
            self.reset_utterance()

    def start_session_recording(self, *, initial_pcm: bytes = b"") -> Path | None:
        """Start a diagnostic WAV for the current active interaction."""

        self._open_session_recording()
        if self._session_recording_writer is not None and initial_pcm:
            self._session_recording_writer.writeframes(initial_pcm)
        return self._session_recording_path

    def stop_session_recording(self) -> None:
        """Finish the current active interaction WAV if one is open."""

        self._close_session_recording()

    def add_chunk_listener(self, listener: Callable[[bytes, int], None]) -> None:
        if listener not in self._chunk_listeners:
            self._chunk_listeners.append(listener)

    def remove_chunk_listener(self, listener: Callable[[bytes, int], None]) -> None:
        with contextlib.suppress(ValueError):
            self._chunk_listeners.remove(listener)

    def _handle_chunk(self, chunk: bytes) -> None:
        if not chunk:
            return
        chunk_start_offset = self._wake_buffer.end_offset
        self._wake_buffer.append(chunk)
        if self._session_recording_writer is not None:
            self._session_recording_writer.writeframes(chunk)
        if self.utterance_active:
            self.utterance_buffer.extend(chunk)
        for listener in tuple(self._chunk_listeners):
            listener(chunk, chunk_start_offset)

    @property
    def session_recording_path(self) -> Path | None:
        return self._session_recording_path

    def _open_session_recording(self) -> None:
        if not self.session_recording_enabled or self._session_recording_writer is not None:
            return
        output_dir = self.session_recording_dir or Path("data/audio/session-recordings")
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        path = output_dir / f"ai-companion-session-{timestamp}.wav"
        writer = wave.open(str(path), "wb")
        writer.setnchannels(self.channels)
        writer.setsampwidth(self.sample_width)
        writer.setframerate(self.sample_rate)
        self._session_recording_writer = writer
        self._session_recording_path = path
        logger.info("audio session_recording_started path=%s", path)

    def _close_session_recording(self) -> None:
        writer = self._session_recording_writer
        path = self._session_recording_path
        self._session_recording_writer = None
        if writer is None:
            return
        writer.close()
        logger.info("audio session_recording_finished path=%s", path)
        self._rotate_session_recordings()

    def _rotate_session_recordings(self) -> None:
        if not self.session_recording_enabled:
            return
        keep_count = max(1, self.session_recording_keep_count)
        output_dir = self.session_recording_dir or Path("data/audio/session-recordings")
        if not output_dir.exists():
            return
        recordings = sorted(
            output_dir.glob("ai-companion-session-*.wav"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for stale_path in recordings[keep_count:]:
            with contextlib.suppress(OSError):
                stale_path.unlink()
                logger.info("audio session_recording_deleted path=%s", stale_path)

    def _build_window(
        self,
        pcm_data: bytes,
        *,
        threshold: int,
        source_path: Path | None,
        stream_start_offset: int,
    ) -> AudioWindow | None:
        if not pcm_data:
            return None
        return audio_window_from_pcm(
            pcm_data,
            source_path=source_path or Path("shared-live-session.pcm"),
            channels=self.channels,
            sample_width=self.sample_width,
            sample_rate=self.sample_rate,
            threshold=threshold,
            stream_start_offset=stream_start_offset,
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
class MicLevelSampler:
    """Convert PCM16 microphone chunks into throttled normalized UI levels."""

    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2
    updates_per_second: float = 10.0
    noise_floor: float = 90.0
    speech_energy: float = 5200.0
    _next_publish_offset: int = field(default=0, init=False, repr=False)
    _last_level: float = field(default=0.0, init=False, repr=False)

    def sample(self, chunk: bytes, chunk_start_offset: int) -> float | None:
        if self.sample_width != 2 or not chunk:
            return None
        interval_bytes = self._publish_interval_bytes()
        if chunk_start_offset < self._next_publish_offset:
            return None
        self._next_publish_offset = chunk_start_offset + interval_bytes
        self._last_level = normalize_pcm16_mic_level(
            chunk,
            noise_floor=self.noise_floor,
            speech_energy=self.speech_energy,
        )
        return self._last_level

    def _publish_interval_bytes(self) -> int:
        bytes_per_second = max(1, self.channels * self.sample_width * self.sample_rate)
        return max(self.sample_width, int(bytes_per_second / max(1.0, self.updates_per_second)))


def pcm16_rms_energy(window: bytes) -> float:
    """Return RMS energy for little-endian signed 16-bit PCM data."""

    if not window:
        return 0.0
    sample_count = len(window) // 2
    if sample_count == 0:
        return 0.0
    samples = struct.unpack("<" + "h" * sample_count, window[: sample_count * 2])
    mean_square = sum(float(sample) * sample for sample in samples) / sample_count
    return math.sqrt(mean_square)


def normalize_pcm16_mic_level(
    window: bytes,
    *,
    noise_floor: float = 90.0,
    speech_energy: float = 5200.0,
) -> float:
    """Map PCM16 RMS energy to a conservative normalized microphone level."""

    energy = pcm16_rms_energy(window)
    raw_level = (energy - noise_floor) / max(1.0, speech_energy - noise_floor)
    return min(1.0, max(0.0, math.sqrt(min(1.0, max(0.0, raw_level)))))


@dataclass(slots=True)
class ShellAudioCaptureService:
    """Capture microphone audio by running a configured external recorder."""

    command_template: tuple[str, ...]
    output_dir: Path | None = None
    init_command: tuple[str, ...] = ()
    startup_poll_seconds: float = 0.05
    startup_timeout_seconds: float = 2.0
    sample_rate: int = 16000
    channels: int = 1
    input_channels: int = 1
    channel_index: int = 0
    sample_width: int = 2
    stream_format: str = "s16le"
    _init_ran: bool = field(default=False, init=False, repr=False)
    _init_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def start_capture(self, on_chunk: Callable[[bytes], None] | None = None) -> RecordingSession:
        await self._ensure_initialized()
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
        try:
            await self._wait_for_capture_data(session)
        except Exception:
            with contextlib.suppress(Exception):
                await session.stop()
            with contextlib.suppress(OSError):
                pcm_path.unlink()
            raise
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
                raise RuntimeError(error_text or "audio capture exited before producing PCM data")
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError("audio capture did not produce PCM data in time")
            await asyncio.sleep(self.startup_poll_seconds)

    def _render_command(self, output_path: Path) -> tuple[str, ...]:
        if not self.command_template:
            raise RuntimeError(
                "audio_record_command is not configured; provide a recorder command such as arecord, rec, or ffmpeg"
            )
        return tuple(token.replace("{output_path}", "-") for token in self.command_template)

    async def _ensure_initialized(self) -> None:
        if self._init_ran or not self.init_command:
            return
        async with self._init_lock:
            if self._init_ran:
                return
            logger.info("audio init command starting command=%s", " ".join(self.init_command))
            process = await asyncio.create_subprocess_exec(
                *self.init_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            stdout_text = stdout.decode(errors="replace").strip()
            stderr_text = stderr.decode(errors="replace").strip()
            if process.returncode != 0:
                message = stderr_text or stdout_text
                raise RuntimeError(f"audio init command failed with status {process.returncode}: {message}")
            for line in stdout_text.splitlines():
                logger.info("audio init stdout %s", line)
            for line in stderr_text.splitlines():
                logger.info("audio init stderr %s", line)
            logger.info("audio init command completed")
            self._init_ran = True

    def materialize_wav_bytes(self, pcm_data: bytes, wav_path: Path) -> Path:
        write_wav_file(
            wav_path,
            channels=self.channels,
            sample_width=self.sample_width,
            sample_rate=self.sample_rate,
            pcm_data=pcm_data,
        )
        return wav_path

    async def _capture_stdout(
        self,
        process: asyncio.subprocess.Process,
        session: ShellRecordingSession,
        *,
        on_chunk: Callable[[bytes], None] | None = None,
    ) -> None:
        if process.stdout is None:
            return
        extractor = None
        if self.input_channels > 1:
            extractor = InterleavedChannelExtractor(
                channels=self.input_channels,
                channel_index=self.channel_index,
                sample_width=self.sample_width,
            )
        with session.pcm_path.open("ab") if on_chunk is None else contextlib.nullcontext() as handle:
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    break
                processed_chunk = extractor.feed(chunk) if extractor is not None else chunk
                if not processed_chunk:
                    continue
                session.note_chunk(processed_chunk)
                if on_chunk is not None:
                    on_chunk(processed_chunk)
                    continue
                handle.write(processed_chunk)
                handle.flush()
            if extractor is not None:
                final_chunk = extractor.flush()
                if not final_chunk:
                    return
                session.note_chunk(final_chunk)
                if on_chunk is not None:
                    on_chunk(final_chunk)
                    return
                handle.write(final_chunk)
                handle.flush()

    async def _capture_stderr(self, process: asyncio.subprocess.Process, session: ShellRecordingSession) -> None:
        if process.stderr is None:
            return
        while True:
            chunk = await process.stderr.read(4096)
            if not chunk:
                break
            session.append_stderr(chunk.decode(errors="replace"))


def write_wav_file(
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


def audio_window_from_pcm(
    pcm_data: bytes,
    *,
    source_path: Path,
    channels: int,
    sample_width: int,
    sample_rate: int,
    threshold: int,
    stream_start_offset: int = 0,
) -> AudioWindow:
    trailing_silence, current_energy, peak_energy = measure_trailing_silence_seconds(
        pcm_data,
        sample_width=sample_width,
        channels=channels,
        sample_rate=sample_rate,
        threshold=threshold,
    )
    bytes_per_second = max(1, channels * sample_width * sample_rate)
    return AudioWindow(
        source_path=source_path,
        channels=channels,
        sample_width=sample_width,
        sample_rate=sample_rate,
        pcm_data=pcm_data,
        duration_seconds=len(pcm_data) / bytes_per_second,
        trailing_silence_seconds=trailing_silence,
        has_speech=peak_energy >= threshold,
        current_energy=current_energy,
        peak_energy=peak_energy,
        stream_start_offset=stream_start_offset,
    )


def measure_trailing_silence_seconds(
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
        if window:
            energies.append(window_energy(window, sample_width=sample_width))
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


def window_energy(window: bytes, *, sample_width: int) -> float:
    if sample_width != 2 or not window:
        return 0.0
    sample_count = len(window) // sample_width
    if sample_count == 0:
        return 0.0
    samples = struct.unpack("<" + "h" * sample_count, window[: sample_count * sample_width])
    return sum(abs(sample) for sample in samples) / sample_count


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
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return ""
    ffmpeg_lines = [line for line in lines if not line.lower().startswith("ffmpeg version ")]
    if ffmpeg_lines:
        lines = ffmpeg_lines
    return "\n".join(lines[-4:])
