"""OpenAI Realtime conversation support for low-latency robot speech."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import subprocess
import struct
import sys
import tempfile
import threading
import time
import wave
from array import array
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib import parse

from shared.events import Event, EventName
from shared.models import CapabilityDefinition, CapabilityKind, ComponentName

logger = logging.getLogger(__name__)


def _client_event_id(prefix: str) -> str:
    return f"{prefix}_{int(time.monotonic() * 1000)}"


@dataclass(slots=True, frozen=True)
class RealtimeToolCall:
    """Realtime model request for a local robot tool."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RealtimeToolResult:
    """Validated local tool result returned to the realtime model."""

    call_id: str
    tool_name: str
    output_text: str
    image_url: str | None = None


RealtimeToolExecutionHandler = Callable[[RealtimeToolCall], Awaitable[RealtimeToolResult]]
EventHandler = Callable[[Event], Awaitable[None]]


class RealtimeWebSocket(Protocol):
    """Small async WebSocket surface used by the realtime client."""

    async def send(self, message: str) -> None:
        """Send a JSON-serialized client event."""

    async def recv(self) -> str:
        """Receive the next JSON-serialized server event."""

    async def close(self) -> None:
        """Close the socket."""


RealtimeWebSocketFactory = Callable[[str, Mapping[str, str]], Awaitable[RealtimeWebSocket]]


class RealtimePcmOutput(Protocol):
    """Streaming PCM playback sink for realtime model audio."""

    async def start(self) -> None:
        """Prepare the output sink."""

    async def write(self, pcm_frames: bytes) -> None:
        """Play or buffer one PCM chunk."""

    async def interrupt(self) -> None:
        """Stop active playback quickly."""

    async def shutdown(self) -> None:
        """Release the output device."""


@dataclass(slots=True)
class NullRealtimePcmOutput:
    """PCM sink used when no native realtime playback device is available."""

    received_bytes: int = 0
    start_calls: int = 0
    interrupt_calls: int = 0
    shutdown_calls: int = 0

    async def start(self) -> None:
        self.start_calls += 1

    async def write(self, pcm_frames: bytes) -> None:
        self.received_bytes += len(pcm_frames)

    async def interrupt(self) -> None:
        self.interrupt_calls += 1

    async def shutdown(self) -> None:
        self.shutdown_calls += 1

    def is_active(self) -> bool:
        return False


@dataclass(slots=True)
class CommandRealtimePcmOutput:
    """Buffered PCM sink that plays completed realtime audio through a command."""

    command_template: tuple[str, ...]
    sample_rate_hz: int = 24000
    channels: int = 1
    _buffer: bytearray = field(default_factory=bytearray, init=False, repr=False)
    _playback_process: asyncio.subprocess.Process | None = field(default=None, init=False, repr=False)
    _cleanup_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    async def start(self) -> None:
        return None

    async def write(self, pcm_frames: bytes) -> None:
        self._buffer.extend(pcm_frames)

    async def finish(self) -> None:
        if not self._buffer:
            return
        if self._playback_process is not None and self._playback_process.returncode is None:
            await self.interrupt()
        audio_path = self._write_temp_wav(bytes(self._buffer))
        self._buffer.clear()
        command = _format_command_input_path(self.command_template, audio_path)
        self._playback_process = await asyncio.create_subprocess_exec(
            *command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._cleanup_task = asyncio.create_task(self._cleanup_after_playback(audio_path, self._playback_process))

    def is_active(self) -> bool:
        return bool(self._buffer) or (
            self._playback_process is not None and self._playback_process.returncode is None
        )

    async def interrupt(self) -> None:
        self._buffer.clear()
        process = self._playback_process
        if process is not None and process.returncode is None:
            process.terminate()
            with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
                await asyncio.wait_for(process.wait(), timeout=1.0)
            if process.returncode is None:
                process.kill()
                with contextlib.suppress(ProcessLookupError):
                    await process.wait()

    async def shutdown(self) -> None:
        await self.interrupt()
        if self._cleanup_task is not None:
            with contextlib.suppress(Exception):
                await self._cleanup_task

    def _write_temp_wav(self, pcm_frames: bytes) -> str:
        handle = tempfile.NamedTemporaryFile(prefix="realtime-", suffix=".wav", delete=False)
        path = handle.name
        handle.close()
        with wave.open(path, "wb") as wav_file:
            wav_file.setnchannels(self.channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate_hz)
            wav_file.writeframes(pcm_frames)
        return path

    async def _cleanup_after_playback(self, path: str, process: asyncio.subprocess.Process) -> None:
        try:
            await process.wait()
        finally:
            with contextlib.suppress(FileNotFoundError):
                import os

                os.unlink(path)


@dataclass(slots=True)
class Pcm16RateConverter:
    """Convert little-endian mono PCM16 chunks between sample rates."""

    source_rate_hz: int
    target_rate_hz: int
    channels: int = 1
    _source_position: float = field(default=0.0, init=False, repr=False)

    def convert(self, pcm_frames: bytes) -> bytes:
        if not pcm_frames or self.source_rate_hz == self.target_rate_hz:
            return pcm_frames
        if self.channels != 1:
            raise RuntimeError("Realtime PCM conversion currently expects mono PCM16 audio")
        if len(pcm_frames) % 2:
            pcm_frames = pcm_frames[:-1]
        samples = array("h")
        samples.frombytes(pcm_frames)
        if sys.byteorder != "little":
            samples.byteswap()

        step = self.source_rate_hz / self.target_rate_hz
        position = self._source_position
        converted = array("h")
        while position < len(samples):
            converted.append(samples[min(int(position), len(samples) - 1)])
            position += step
        self._source_position = position - len(samples)

        if sys.byteorder != "little":
            converted.byteswap()
        return converted.tobytes()


@dataclass(slots=True)
class AlsaRealtimePcmOutput:
    """Low-latency ALSA PCM sink for realtime output audio."""

    device: str
    sample_rate_hz: int = 24000
    channels: int = 1
    period_frames: int = 512
    buffer_frames: int = 2048
    lead_in_silence_ms: int = 300
    _pcm: object | None = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _device_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _needs_lead_in: bool = field(default=True, init=False, repr=False)
    _pending_frames: bytearray = field(default_factory=bytearray, init=False, repr=False)
    _playback_generation: int = field(default=0, init=False, repr=False)
    _playback_active: bool = field(default=False, init=False, repr=False)
    _playback_queue: asyncio.Queue[tuple[int, bytes] | None] | None = field(default=None, init=False, repr=False)
    _playback_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    async def start(self) -> None:
        async with self._lock:
            if self._pcm is None:
                self._pcm = await asyncio.to_thread(self._open_pcm)
            if self._playback_queue is None:
                self._playback_queue = asyncio.Queue()
            if self._playback_task is None or self._playback_task.done():
                self._playback_task = asyncio.create_task(self._playback_loop(self._playback_queue))

    async def write(self, pcm_frames: bytes) -> None:
        if not pcm_frames:
            return
        await self.start()
        if self._needs_lead_in:
            pcm_frames = self._lead_in_silence() + pcm_frames
            self._needs_lead_in = False
        self._pending_frames.extend(pcm_frames)
        chunk_size = self._period_chunk_bytes()
        writable_bytes = len(self._pending_frames) - (len(self._pending_frames) % chunk_size)
        if writable_bytes <= 0:
            return
        chunk = bytes(self._pending_frames[:writable_bytes])
        del self._pending_frames[:writable_bytes]
        await self._enqueue_pcm(chunk, self._playback_generation)

    async def finish(self) -> None:
        if self._pending_frames:
            chunk_size = self._period_chunk_bytes()
            padding = (-len(self._pending_frames)) % chunk_size
            chunk = bytes(self._pending_frames) + (b"\x00" * padding)
            self._pending_frames.clear()
            await self._enqueue_pcm(chunk, self._playback_generation)
        self._needs_lead_in = True

    def is_active(self) -> bool:
        queue = self._playback_queue
        return bool(self._pending_frames) or self._playback_active or (queue is not None and queue.qsize() > 0)

    async def interrupt(self) -> None:
        self._playback_generation += 1
        self._pending_frames.clear()
        self._drain_playback_queue()
        pcm = self._pcm
        if pcm is None:
            return
        drop = getattr(pcm, "drop", None)
        prepare = getattr(pcm, "prepare", None)
        if callable(drop) or callable(prepare):
            await asyncio.to_thread(self._drop_and_prepare_pcm, pcm)
        self._needs_lead_in = True

    async def shutdown(self) -> None:
        self._pending_frames.clear()
        queue = self._playback_queue
        task = self._playback_task
        if queue is not None:
            await queue.join()
            queue.put_nowait(None)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._playback_generation += 1
        self._playback_queue = None
        self._playback_task = None
        async with self._lock:
            pcm = self._pcm
            self._pcm = None
        if pcm is not None:
            close = getattr(pcm, "close", None)
            if callable(close):
                await asyncio.to_thread(close)

    def _open_pcm(self):  # type: ignore[no-untyped-def]
        import alsaaudio  # type: ignore[import-not-found]

        try:
            pcm = alsaaudio.PCM(
                type=alsaaudio.PCM_PLAYBACK,
                mode=alsaaudio.PCM_NORMAL,
                device=self.device,
                channels=self.channels,
                rate=self.sample_rate_hz,
                format=alsaaudio.PCM_FORMAT_S16_LE,
                periodsize=self.period_frames,
            )
        except TypeError:
            pcm = alsaaudio.PCM(
                type=alsaaudio.PCM_PLAYBACK,
                mode=alsaaudio.PCM_NORMAL,
                device=self.device,
            )
            pcm.setchannels(self.channels)
            pcm.setrate(self.sample_rate_hz)
            pcm.setformat(alsaaudio.PCM_FORMAT_S16_LE)
            pcm.setperiodsize(self.period_frames)
        set_buffer_size = getattr(pcm, "setbuffersize", None)
        if callable(set_buffer_size):
            set_buffer_size(max(self.period_frames, self.buffer_frames))
        return pcm

    def _lead_in_silence(self) -> bytes:
        frame_count = int(self.sample_rate_hz * max(0, self.lead_in_silence_ms) / 1000)
        return b"\x00" * frame_count * self.channels * 2

    def _period_chunk_bytes(self) -> int:
        return max(1, self.period_frames) * self.channels * 2

    async def _enqueue_pcm(self, pcm_frames: bytes, generation: int) -> None:
        queue = self._playback_queue
        if queue is None:
            raise RuntimeError("ALSA realtime playback queue is not open")
        await queue.put((generation, pcm_frames))

    async def _playback_loop(self, queue: asyncio.Queue[tuple[int, bytes] | None]) -> None:
        while True:
            item = await queue.get()
            try:
                if item is None:
                    return
                generation, pcm_frames = item
                pcm = self._pcm
                if pcm is None or generation != self._playback_generation:
                    continue
                chunk_size = self._period_chunk_bytes()
                for offset in range(0, len(pcm_frames), chunk_size):
                    if generation != self._playback_generation:
                        break
                    self._playback_active = True
                    try:
                        await asyncio.to_thread(self._write_all_pcm, pcm, pcm_frames[offset : offset + chunk_size])
                    except Exception as exc:
                        if generation != self._playback_generation:
                            logger.debug("realtime alsa_playback_write_interrupted error=%s", exc)
                        else:
                            logger.warning("realtime alsa_playback_write_failed error=%s", exc)
                            await asyncio.to_thread(self._prepare_pcm, pcm)
                        break
                    finally:
                        self._playback_active = False
            finally:
                queue.task_done()

    def _drain_playback_queue(self, queue: asyncio.Queue[tuple[int, bytes] | None] | None = None) -> None:
        playback_queue = queue or self._playback_queue
        if playback_queue is None:
            return
        while True:
            try:
                playback_queue.get_nowait()
                playback_queue.task_done()
            except asyncio.QueueEmpty:
                return

    def _write_all_pcm(self, pcm: object, pcm_frames: bytes) -> None:
        frame_size = self.channels * 2
        offset = 0
        while offset < len(pcm_frames):
            chunk = pcm_frames[offset : offset + self._period_chunk_bytes()]
            with self._device_lock:
                try:
                    written = pcm.write(chunk)  # type: ignore[attr-defined]
                except Exception:
                    self._prepare_pcm_unlocked(pcm)
                    written = pcm.write(chunk)  # type: ignore[attr-defined]
            if isinstance(written, int):
                if written <= 0:
                    time.sleep(self.period_frames / max(1, self.sample_rate_hz))
                    continue
                offset += written * frame_size
            else:
                offset += len(chunk)

    def _drop_and_prepare_pcm(self, pcm: object) -> None:
        with self._device_lock:
            drop = getattr(pcm, "drop", None)
            prepare = getattr(pcm, "prepare", None)
            if callable(drop):
                drop()
            if callable(prepare):
                prepare()

    def _prepare_pcm(self, pcm: object) -> None:
        with self._device_lock:
            self._prepare_pcm_unlocked(pcm)

    @staticmethod
    def _prepare_pcm_unlocked(pcm: object) -> None:
        prepare = getattr(pcm, "prepare", None)
        if callable(prepare):
            prepare()


@dataclass(slots=True)
class RealtimeConversationService:
    """Own one wake-triggered OpenAI Realtime speech session."""

    api_key: str
    base_url: str
    model: str
    voice: str
    turn_detection: str
    audio_capture_sample_rate_hz: int
    realtime_sample_rate_hz: int
    audio_output: RealtimePcmOutput
    websocket_factory: RealtimeWebSocketFactory = field(default_factory=lambda: _connect_websocket)
    event_handler: EventHandler | None = None
    tool_handler: RealtimeToolExecutionHandler | None = None
    instructions: str = (
        "You are Oreo, a friendly desktop companion robot. Speak naturally and concisely. "
        "Use local robot tools when they are helpful, but do not claim an action succeeded until the tool result confirms it."
    )
    tools: tuple[dict[str, Any], ...] = ()
    follow_up_idle_timeout_seconds: float = 5.0
    turn_eagerness: str = "auto"
    local_barge_in_enabled: bool = False
    interrupt_response: bool = False
    playback_barge_in_enabled: bool = True
    playback_barge_in_threshold: float = 2500.0
    playback_barge_in_required_ms: int = 320
    playback_barge_in_grace_ms: int = 700
    playback_barge_in_recent_vad_ms: int = 1200
    playback_barge_in_recent_required_ms: int = 180

    async def start(self) -> None:
        await self.audio_output.start()

    async def shutdown(self) -> None:
        await self.audio_output.shutdown()

    async def run_awake_session(
        self,
        *,
        audio_chunks: "asyncio.Queue[bytes | None]",
    ) -> None:
        """Stream queued microphone chunks to Realtime and play streamed audio replies."""

        await self.audio_output.start()
        websocket = await self.websocket_factory(
            _realtime_url(self.base_url, self.model),
            {"Authorization": f"Bearer {self.api_key}"},
        )
        sender_task: asyncio.Task[None] | None = None
        receiver_task: asyncio.Task[None] | None = None
        stop_event = asyncio.Event()
        state: _RealtimeEventState | None = None
        try:
            await websocket.send(json.dumps(self._session_update_event()))
            state = _RealtimeEventState()
            sender_task = asyncio.create_task(self._send_audio_loop(websocket, audio_chunks, stop_event, state))
            receiver_task = asyncio.create_task(self._receive_events_loop(websocket, stop_event, state))
            await stop_event.wait()
            logger.info("realtime session stop %s", state.stats_summary())
        finally:
            stop_event.set()
            if sender_task is not None:
                sender_task.cancel()
            if receiver_task is not None:
                receiver_task.cancel()
            for task in (sender_task, receiver_task):
                if task is not None:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            await websocket.close()

    async def _send_audio_loop(
        self,
        websocket: RealtimeWebSocket,
        audio_chunks: "asyncio.Queue[bytes | None]",
        stop_event: asyncio.Event,
        state: "_RealtimeEventState",
    ) -> None:
        converter = Pcm16RateConverter(
            source_rate_hz=self.audio_capture_sample_rate_hz,
            target_rate_hz=self.realtime_sample_rate_hz,
        )
        barge_in_detector = _LocalBargeInDetector(sample_rate_hz=self.audio_capture_sample_rate_hz)
        playback_barge_in_detector = _LocalBargeInDetector(
            sample_rate_hz=self.audio_capture_sample_rate_hz,
            threshold=self.playback_barge_in_threshold,
            required_speech_ms=self.playback_barge_in_required_ms,
        )
        while not stop_event.is_set():
            chunk = await audio_chunks.get()
            if chunk is None:
                return
            if state.speaker_active:
                if self._detects_playback_barge_in(playback_barge_in_detector, chunk, state):
                    state.response_create_pending = True
                    await self._interrupt_active_response(websocket, state, source="playback_barge_in")
                    playback_barge_in_detector.reset()
                    barge_in_detector.reset()
                elif (
                    self.local_barge_in_enabled
                    and barge_in_detector.detects_barge_in(chunk)
                ):
                    await self._interrupt_active_response(websocket, state, source="local_barge_in")
                    barge_in_detector.reset()
                    playback_barge_in_detector.reset()
            elif not state.speaker_active:
                barge_in_detector.reset()
                playback_barge_in_detector.reset()
            converted = converter.convert(chunk)
            if not converted:
                continue
            state.input_audio_chunks += 1
            state.input_audio_bytes += len(converted)
            await websocket.send(
                json.dumps(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(converted).decode("ascii"),
                    }
                )
            )

    def _detects_playback_barge_in(
        self,
        detector: "_LocalBargeInDetector",
        chunk: bytes,
        state: "_RealtimeEventState",
    ) -> bool:
        if not self.playback_barge_in_enabled or not state.pending_server_barge_in:
            detector.reset()
            return False
        if state.playback_started_at is None:
            detector.reset()
            return False
        loop_time = asyncio.get_running_loop().time()
        elapsed_ms = max(0, int((loop_time - state.playback_started_at) * 1000))
        server_vad_age_ms = None
        if state.last_server_barge_in_at is not None:
            server_vad_age_ms = max(0, int((loop_time - state.last_server_barge_in_at) * 1000))
        detected = detector.detects_barge_in(chunk)
        state.playback_barge_peak_energy = max(state.playback_barge_peak_energy, detector.last_energy)
        state.playback_barge_max_active_ms = max(state.playback_barge_max_active_ms, detector.active_ms)
        if elapsed_ms < self.playback_barge_in_grace_ms:
            if detector.active_ms > 0:
                logger.info(
                    "realtime playback_barge_ignored reason=grace elapsed_ms=%s energy=%.1f threshold=%.1f active_ms=%.1f server_vad_age_ms=%s",
                    elapsed_ms,
                    detector.last_energy,
                    detector.threshold,
                    detector.active_ms,
                    server_vad_age_ms if server_vad_age_ms is not None else "--",
                )
            detector.reset()
            return False
        fresh_server_vad = (
            server_vad_age_ms is not None
            and server_vad_age_ms <= self.playback_barge_in_recent_vad_ms
        )
        recently_detected = (
            fresh_server_vad
            and detector.active_ms >= self.playback_barge_in_recent_required_ms
        )
        if detected or recently_detected:
            logger.info(
                "realtime playback_barge_confirmed elapsed_ms=%s energy=%.1f threshold=%.1f active_ms=%.1f required_ms=%s server_vad_age_ms=%s",
                elapsed_ms,
                detector.last_energy,
                detector.threshold,
                detector.active_ms,
                (
                    self.playback_barge_in_recent_required_ms
                    if recently_detected
                    else self.playback_barge_in_required_ms
                ),
                server_vad_age_ms if server_vad_age_ms is not None else "--",
            )
            state.pending_server_barge_in = False
            return True
        if detector.last_energy >= detector.threshold:
            logger.info(
                "realtime playback_barge_ignored reason=duration elapsed_ms=%s energy=%.1f threshold=%.1f active_ms=%.1f server_vad_age_ms=%s",
                elapsed_ms,
                detector.last_energy,
                detector.threshold,
                detector.active_ms,
                server_vad_age_ms if server_vad_age_ms is not None else "--",
            )
        return False

    async def _receive_events_loop(
        self,
        websocket: RealtimeWebSocket,
        stop_event: asyncio.Event,
        state: "_RealtimeEventState",
    ) -> None:
        while not stop_event.is_set():
            try:
                raw_message = await asyncio.wait_for(websocket.recv(), timeout=0.2)
            except asyncio.TimeoutError:
                await self._complete_playback_if_idle(state)
                if state.follow_up_deadline is not None and asyncio.get_running_loop().time() >= state.follow_up_deadline:
                    logger.info("realtime follow_up_idle_timeout %s", state.stats_summary())
                    stop_event.set()
                continue
            event = json.loads(raw_message)
            if not isinstance(event, dict):
                continue
            await self._handle_server_event(websocket, event, state, stop_event)

    async def _handle_server_event(
        self,
        websocket: RealtimeWebSocket,
        event: dict[str, Any],
        state: "_RealtimeEventState",
        stop_event: asyncio.Event,
    ) -> None:
        event_type = str(event.get("type", ""))
        if event_type == "error":
            error = event.get("error") if isinstance(event.get("error"), dict) else {}
            event_id = str(error.get("event_id") or event.get("event_id") or "").strip()
            if event_id and event_id in state.nonfatal_client_event_ids:
                logger.info("realtime nonfatal_client_event_error event_id=%s error=%s", event_id, error)
                state.nonfatal_client_event_ids.discard(event_id)
                return
            raise RuntimeError(f"OpenAI Realtime error: {error}")

        if event_type == "response.output_item.created":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "message":
                state.response_item_id = str(item.get("id", "")).strip() or None
            if isinstance(item, dict) and item.get("type") == "function_call":
                item_id = str(item.get("id", "")).strip()
                name = str(item.get("name", "")).strip()
                if item_id and name:
                    state.function_names[item_id] = name
            return

        if event_type == "input_audio_buffer.speech_started":
            state.follow_up_deadline = None
            state.user_speech_active = True
            state.user_speech_started_count += 1
            if state.speaker_active or state.audio_started:
                state.pending_server_barge_in = True
                state.last_server_barge_in_at = asyncio.get_running_loop().time()
                state.playback_barge_peak_energy = 0.0
                state.playback_barge_max_active_ms = 0.0
                elapsed_ms = None
                if state.playback_started_at is not None:
                    elapsed_ms = max(0, int((state.last_server_barge_in_at - state.playback_started_at) * 1000))
                logger.info(
                    "realtime playback_barge_candidate speaker_active=%s streaming=%s elapsed_ms=%s interrupts=%s",
                    state.speaker_active,
                    state.audio_started,
                    elapsed_ms if elapsed_ms is not None else "--",
                    state.interrupt_count,
                )
                return
            state.response_create_pending = True
            logger.info(
                "realtime user_speech_started speaker_active=%s streaming=%s interrupts=%s",
                state.speaker_active,
                state.audio_started,
                state.interrupt_count,
            )
            return

        if event_type == "input_audio_buffer.speech_stopped":
            state.user_speech_stopped = True
            state.user_speech_active = False
            state.follow_up_deadline = None
            if state.pending_server_barge_in:
                server_vad_age_ms = None
                if state.last_server_barge_in_at is not None:
                    server_vad_age_ms = max(
                        0,
                        int((asyncio.get_running_loop().time() - state.last_server_barge_in_at) * 1000),
                    )
                logger.info(
                    "realtime playback_barge_ignored reason=speech_stopped energy=%.1f threshold=%.1f active_ms=%.1f required_ms=%s recent_required_ms=%s server_vad_age_ms=%s",
                    state.playback_barge_peak_energy,
                    self.playback_barge_in_threshold,
                    state.playback_barge_max_active_ms,
                    self.playback_barge_in_required_ms,
                    self.playback_barge_in_recent_required_ms,
                    server_vad_age_ms if server_vad_age_ms is not None else "--",
                )
            state.pending_server_barge_in = False
            state.last_server_barge_in_at = None
            logger.info("realtime user_speech_stopped input=%sB/%sch", state.input_audio_bytes, state.input_audio_chunks)
            response_created = await self._create_response_if_pending(websocket, state, source="speech_stopped")
            if (
                not response_created
                and not state.speaker_active
                and not state.audio_started
                and not state.response_done_waiting_for_playback
            ):
                state.follow_up_deadline = asyncio.get_running_loop().time() + self.follow_up_idle_timeout_seconds
                logger.info("realtime follow_up_idle_armed source=speech_stopped %s", state.stats_summary())
            return

        if event_type in {"response.output_audio.delta", "response.audio.delta"}:
            await self._handle_audio_delta(event, state)
            return

        if event_type in {"response.output_audio.done", "response.audio.done"}:
            await self._finish_audio_output(state)
            return

        if event_type == "response.function_call_arguments.done":
            await self._handle_function_call_done(websocket, event, state)
            return

        if event_type == "response.done":
            if state.audio_started:
                await self._finish_audio_output(state)
            await self._complete_playback_if_idle(state)
            if state.ignore_next_response_done_deadline:
                state.ignore_next_response_done_deadline = False
                return
            if state.user_speech_active:
                return
            if state.speaker_active:
                state.response_done_waiting_for_playback = True
            else:
                state.follow_up_deadline = asyncio.get_running_loop().time() + self.follow_up_idle_timeout_seconds
            return

        if event_type == "conversation.item.truncated":
            logger.info(
                "realtime assistant_audio_truncated item_id=%s audio_end_ms=%s",
                event.get("item_id", "--"),
                event.get("audio_end_ms", "--"),
            )
            return

    async def _handle_audio_delta(self, event: dict[str, Any], state: "_RealtimeEventState") -> None:
        encoded = event.get("delta")
        if not isinstance(encoded, str) or not encoded:
            return
        response_id = str(event.get("response_id", "realtime")) or "realtime"
        if response_id in state.interrupted_response_ids:
            return
        state.follow_up_deadline = None
        pcm_frames = base64.b64decode(encoded)
        state.output_audio_chunks += 1
        state.output_audio_bytes += len(pcm_frames)
        state.current_response_audio_bytes += len(pcm_frames)
        if not state.audio_started:
            state.audio_started = True
            state.speaker_active = True
            state.pending_server_barge_in = False
            state.last_server_barge_in_at = None
            state.response_id = response_id
            state.playback_started_at = asyncio.get_running_loop().time()
            state.response_count += 1
            logger.info("realtime response_audio_started response_id=%s", response_id)
            await self._emit_audio_event(
                EventName.AUDIO_PLAYBACK_STARTED,
                {
                    "job_id": state.response_id,
                    "text": "",
                    "voice_id": self.voice,
                    "input_audio_bytes": state.input_audio_bytes,
                    "input_audio_chunks": state.input_audio_chunks,
                    "output_audio_bytes": state.output_audio_bytes,
                    "output_audio_chunks": state.output_audio_chunks,
                    "response_count": state.response_count,
                    "interrupt_count": state.interrupt_count,
                },
            )
        await self.audio_output.write(pcm_frames)

    async def _interrupt_active_response(
        self,
        websocket: RealtimeWebSocket,
        state: "_RealtimeEventState",
        *,
        source: str,
    ) -> None:
        if state.interrupt_in_progress:
            return
        interrupted_response_id = state.response_id or "realtime"
        response_was_streaming = state.audio_started
        state.interrupt_in_progress = True
        state.interrupt_count += 1
        state.interrupted_response_ids.add(interrupted_response_id)
        state.follow_up_deadline = None
        state.response_done_waiting_for_playback = False
        logger.info("realtime interrupt_detected source=%s response_id=%s %s", source, interrupted_response_id, state.stats_summary())
        if response_was_streaming:
            state.ignore_next_response_done_deadline = True
            cancel_event_id = _client_event_id("cancel")
            state.nonfatal_client_event_ids.add(cancel_event_id)
            await websocket.send(
                json.dumps(
                    {
                        "event_id": cancel_event_id,
                        "type": "response.cancel",
                        "response_id": interrupted_response_id,
                    }
                )
            )
        else:
            logger.info("realtime interrupt_skip_response_cancel reason=response_already_done response_id=%s", interrupted_response_id)
        await self.audio_output.interrupt()
        await self._truncate_interrupted_assistant_audio(websocket, state)
        await self._emit_audio_event(
            EventName.AUDIO_INTERRUPTED,
            {
                "job_id": interrupted_response_id,
                "source": source,
                "input_audio_bytes": state.input_audio_bytes,
                "input_audio_chunks": state.input_audio_chunks,
                "output_audio_bytes": state.output_audio_bytes,
                "output_audio_chunks": state.output_audio_chunks,
                "response_count": state.response_count,
                "interrupt_count": state.interrupt_count,
            },
        )
        state.audio_started = False
        state.speaker_active = False
        state.pending_server_barge_in = False
        state.last_server_barge_in_at = None
        state.response_id = None
        state.response_item_id = None
        state.pending_playback_job_id = None
        state.current_response_audio_bytes = 0
        state.playback_started_at = None
        state.interrupt_in_progress = False

    async def _truncate_interrupted_assistant_audio(
        self,
        websocket: RealtimeWebSocket,
        state: "_RealtimeEventState",
    ) -> None:
        if not state.response_item_id:
            logger.info("realtime truncate_skip reason=no_response_item")
            return
        audio_end_ms = self._heard_assistant_audio_ms(state)
        if audio_end_ms <= 0:
            logger.info("realtime truncate_skip reason=no_heard_audio item_id=%s", state.response_item_id)
            return
        truncate_event_id = _client_event_id("truncate")
        state.nonfatal_client_event_ids.add(truncate_event_id)
        logger.info(
            "realtime truncate_assistant_audio item_id=%s audio_end_ms=%s generated_bytes=%s",
            state.response_item_id,
            audio_end_ms,
            state.current_response_audio_bytes,
        )
        await websocket.send(
            json.dumps(
                {
                    "event_id": truncate_event_id,
                    "type": "conversation.item.truncate",
                    "item_id": state.response_item_id,
                    "content_index": 0,
                    "audio_end_ms": audio_end_ms,
                }
            )
        )

    def _heard_assistant_audio_ms(self, state: "_RealtimeEventState") -> int:
        generated_ms = int(
            (state.current_response_audio_bytes / 2 / max(1, self.realtime_sample_rate_hz)) * 1000
        )
        if state.playback_started_at is None:
            return generated_ms
        lead_in_ms = int(getattr(self.audio_output, "lead_in_silence_ms", 0) or 0)
        elapsed_ms = int((asyncio.get_running_loop().time() - state.playback_started_at) * 1000) - lead_in_ms
        heard_ms = max(0, min(generated_ms, elapsed_ms))
        if generated_ms > 0 and state.speaker_active:
            return max(1, heard_ms)
        return heard_ms

    async def _finish_audio_output(self, state: "_RealtimeEventState") -> None:
        if not state.audio_started:
            return
        job_id = state.response_id or "realtime"
        state.audio_started = False
        state.pending_playback_job_id = job_id
        finish = getattr(self.audio_output, "finish", None)
        if callable(finish):
            await finish()
        await self._complete_playback_if_idle(state)

    async def _complete_playback_if_idle(self, state: "_RealtimeEventState") -> None:
        if not state.speaker_active:
            return
        is_active = getattr(self.audio_output, "is_active", None)
        if callable(is_active) and bool(is_active()):
            return
        job_id = state.pending_playback_job_id or state.response_id or "realtime"
        state.speaker_active = False
        state.pending_server_barge_in = False
        state.last_server_barge_in_at = None
        state.pending_playback_job_id = None
        logger.info("realtime response_audio_finished response_id=%s %s", job_id, state.stats_summary())
        await self._emit_audio_event(
            EventName.AUDIO_PLAYBACK_FINISHED,
            {
                "job_id": job_id,
                "text": "",
                "voice_id": self.voice,
                "duration_ms": None,
                "input_audio_bytes": state.input_audio_bytes,
                "input_audio_chunks": state.input_audio_chunks,
                "output_audio_bytes": state.output_audio_bytes,
                "output_audio_chunks": state.output_audio_chunks,
                "response_count": state.response_count,
                "interrupt_count": state.interrupt_count,
            },
        )
        await self._emit_audio_event(
            EventName.AUDIO_FINISHED,
            {"job_id": job_id, "text": "", "duration_ms": None},
        )
        if state.response_done_waiting_for_playback:
            state.response_done_waiting_for_playback = False
            state.follow_up_deadline = asyncio.get_running_loop().time() + self.follow_up_idle_timeout_seconds

    async def _handle_function_call_done(
        self,
        websocket: RealtimeWebSocket,
        event: dict[str, Any],
        state: "_RealtimeEventState",
    ) -> None:
        if self.tool_handler is None:
            return
        call_id = str(event.get("call_id", "")).strip()
        item_id = str(event.get("item_id", "")).strip()
        tool_name = str(event.get("name") or state.function_names.get(item_id, "")).strip()
        arguments = _parse_json_object(event.get("arguments"))
        if not call_id or not tool_name:
            return
        result = await self.tool_handler(
            RealtimeToolCall(call_id=call_id, tool_name=tool_name, arguments=arguments)
        )
        await websocket.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result.output_text,
                    },
                }
            )
        )
        if result.image_url:
            await websocket.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_image",
                                    "image_url": result.image_url,
                                }
                            ],
                        },
                    }
                )
            )
        await websocket.send(json.dumps({"type": "response.create"}))

    async def _create_response_if_pending(
        self,
        websocket: RealtimeWebSocket,
        state: "_RealtimeEventState",
        *,
        source: str,
    ) -> bool:
        if not state.response_create_pending:
            return False
        if state.speaker_active or state.audio_started or state.response_done_waiting_for_playback:
            state.response_create_pending = False
            logger.info(
                "realtime response_create_skipped reason=assistant_active source=%s speaker_active=%s streaming=%s",
                source,
                state.speaker_active,
                state.audio_started,
            )
            return False
        state.response_create_pending = False
        logger.info("realtime response_create_sent source=%s %s", source, state.stats_summary())
        await websocket.send(json.dumps({"type": "response.create"}))
        return True

    async def _emit_audio_event(self, name: EventName, payload: Mapping[str, object]) -> None:
        if self.event_handler is None:
            return
        await self.event_handler(Event(name=name, source=ComponentName.AUDIO, payload=payload))

    def _session_update_event(self) -> dict[str, Any]:
        turn_detection = None
        if self.turn_detection == "server_vad":
            turn_detection = {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 250,
                "create_response": False,
                "interrupt_response": self.interrupt_response,
            }
        elif self.turn_detection == "semantic_vad":
            turn_detection = {
                "type": "semantic_vad",
                "eagerness": self.turn_eagerness,
                "create_response": False,
                "interrupt_response": self.interrupt_response,
            }
        return {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": self.instructions,
                "tools": list(self.tools),
                "tool_choice": "auto",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": self.realtime_sample_rate_hz},
                        "turn_detection": turn_detection,
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": self.realtime_sample_rate_hz},
                        "voice": self.voice,
                    },
                },
            },
        }


@dataclass(slots=True)
class _RealtimeEventState:
    audio_started: bool = False
    speaker_active: bool = False
    user_speech_active: bool = False
    user_speech_stopped: bool = False
    response_id: str | None = None
    response_item_id: str | None = None
    pending_playback_job_id: str | None = None
    playback_started_at: float | None = None
    current_response_audio_bytes: int = 0
    interrupted_response_ids: set[str] = field(default_factory=set)
    interrupt_in_progress: bool = False
    function_names: dict[str, str] = field(default_factory=dict)
    nonfatal_client_event_ids: set[str] = field(default_factory=set)
    follow_up_deadline: float | None = None
    response_done_waiting_for_playback: bool = False
    ignore_next_response_done_deadline: bool = False
    pending_server_barge_in: bool = False
    last_server_barge_in_at: float | None = None
    playback_barge_peak_energy: float = 0.0
    playback_barge_max_active_ms: float = 0.0
    response_create_pending: bool = False
    input_audio_chunks: int = 0
    input_audio_bytes: int = 0
    output_audio_chunks: int = 0
    output_audio_bytes: int = 0
    response_count: int = 0
    interrupt_count: int = 0
    user_speech_started_count: int = 0

    def stats_summary(self) -> str:
        return (
            f"in={self.input_audio_bytes}B/{self.input_audio_chunks}ch "
            f"out={self.output_audio_bytes}B/{self.output_audio_chunks}ch "
            f"responses={self.response_count} speech={self.user_speech_started_count} "
            f"interrupts={self.interrupt_count}"
        )


@dataclass(slots=True)
class _LocalBargeInDetector:
    """Conservative near-field energy detector used only while model audio plays."""

    sample_rate_hz: int
    sample_width_bytes: int = 2
    threshold: float = 900.0
    required_speech_ms: int = 180
    _active_ms: float = 0.0
    _last_energy: float = 0.0

    def detects_barge_in(self, pcm_frames: bytes) -> bool:
        if not pcm_frames:
            self._last_energy = 0.0
            return False
        duration_ms = (len(pcm_frames) / max(1, self.sample_width_bytes) / max(1, self.sample_rate_hz)) * 1000.0
        self._last_energy = _pcm_abs_energy(pcm_frames, sample_width_bytes=self.sample_width_bytes)
        if self._last_energy >= self.threshold:
            self._active_ms += duration_ms
        else:
            self._active_ms = max(0.0, self._active_ms - duration_ms)
        return self._active_ms >= self.required_speech_ms

    def reset(self) -> None:
        self._active_ms = 0.0
        self._last_energy = 0.0

    @property
    def active_ms(self) -> float:
        return self._active_ms

    @property
    def last_energy(self) -> float:
        return self._last_energy


def _pcm_abs_energy(pcm_frames: bytes, *, sample_width_bytes: int) -> float:
    if sample_width_bytes != 2 or len(pcm_frames) < 2:
        return 0.0
    sample_count = len(pcm_frames) // sample_width_bytes
    samples = struct.unpack("<" + "h" * sample_count, pcm_frames[: sample_count * sample_width_bytes])
    return sum(abs(sample) for sample in samples) / sample_count


def build_realtime_tool_definitions(
    capabilities: tuple[CapabilityDefinition, ...],
) -> tuple[dict[str, Any], ...]:
    """Translate local capabilities into OpenAI Realtime function tools."""

    tools: list[dict[str, Any]] = []
    for capability in capabilities:
        if capability.kind is CapabilityKind.RESPONSE:
            continue
        parameters = _capability_parameters_schema(capability)
        tools.append(
            {
                "type": "function",
                "name": capability.capability_id,
                "description": capability.description,
                "parameters": parameters,
            }
        )
    return tuple(tools)


def _capability_parameters_schema(capability: CapabilityDefinition) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, spec in capability.argument_schema.items():
        prop: dict[str, Any] = {"type": spec.get("type", "string")}
        if "enum" in spec:
            prop["enum"] = list(spec["enum"])
        properties[name] = prop
        if spec.get("required"):
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        "required": required,
    }


def _parse_json_object(raw_value: object) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return dict(raw_value)
    if not isinstance(raw_value, str) or not raw_value.strip():
        return {}
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _realtime_url(base_url: str, model: str) -> str:
    parsed = parse.urlparse(base_url)
    query = dict(parse.parse_qsl(parsed.query, keep_blank_values=True))
    query["model"] = model
    return parse.urlunparse(parsed._replace(query=parse.urlencode(query)))


def _format_command_input_path(command_template: tuple[str, ...], input_path: str) -> tuple[str, ...]:
    resolved = tuple(part.replace("{input_path}", input_path) for part in command_template)
    if not any(input_path in part for part in resolved):
        return (*resolved, input_path)
    return resolved


async def _connect_websocket(url: str, headers: Mapping[str, str]) -> RealtimeWebSocket:
    import websockets

    try:
        return await websockets.connect(url, additional_headers=dict(headers))
    except TypeError:
        return await websockets.connect(url, extra_headers=dict(headers))
