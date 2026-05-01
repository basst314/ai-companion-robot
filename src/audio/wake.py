"""Wake-word detection for the realtime audio path."""

from __future__ import annotations

import asyncio
import logging
import platform
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from audio.capture import AudioCaptureService, AudioWindow, SharedLiveSpeechState
from shared.console import TerminalDebugSink

logger = logging.getLogger(__name__)


class WakeWordService(Protocol):
    """Interface for bounded wake-word detection."""

    async def wait_for_wake_word(self) -> "WakeDetectionResult":
        """Block until a wake phrase is detected and return the matched audio context."""


@dataclass(slots=True, frozen=True)
class WakeDetectionResult:
    """Wake-word detection outcome used to seed the next realtime session."""

    detected: bool
    matched_transcript: str = ""
    prefilled_command_text: str = ""
    audio_window: AudioWindow | None = None
    utterance_stream_start_offset: int | None = None
    utterance_start_offset_seconds: float = 0.0


class WakeWordModelAdapter(Protocol):
    """Minimal wake-word scoring interface used by the streaming detector."""

    def score_frame(self, pcm_frame: bytes) -> float:
        """Return a normalized confidence score for a fixed PCM frame."""

    def reset(self) -> None:
        """Reset any internal streaming state before a fresh listen loop."""


@dataclass(slots=True)
class OpenWakeWordModelAdapter:
    """Small adapter that bridges fixed PCM frames into OpenWakeWord."""

    wake_word_model: str
    inference_framework: str | None = None
    _model: object = field(init=False, repr=False)
    _numpy: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            import numpy as np  # type: ignore[import-not-found]
            from openwakeword.model import Model  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "OpenWakeWord wake detection requires the 'openwakeword' package and its runtime dependencies"
            ) from exc
        inference_framework = self.inference_framework or _select_openwakeword_inference_framework(self.wake_word_model)
        try:
            self._model = Model(
                wakeword_models=[self.wake_word_model],
                inference_framework=inference_framework,
            )
        except Exception as exc:  # pragma: no cover - exercised with dependency installed
            raise RuntimeError(f"unable to initialize OpenWakeWord model '{self.wake_word_model}': {exc}") from exc
        self._numpy = np

    def score_frame(self, pcm_frame: bytes) -> float:
        pcm_samples = self._numpy.frombuffer(pcm_frame, dtype=self._numpy.int16)
        predictions = self._model.predict(pcm_samples)
        if not isinstance(predictions, dict) or not predictions:
            return 0.0
        return max(float(score) for score in predictions.values())

    def reset(self) -> None:
        reset = getattr(self._model, "reset", None)
        if callable(reset):
            reset()


def _default_openwakeword_model_factory(wake_word_model: str) -> WakeWordModelAdapter:
    return OpenWakeWordModelAdapter(wake_word_model=wake_word_model)


def _select_openwakeword_inference_framework(wake_word_model: str) -> str:
    normalized = wake_word_model.strip().lower()
    if normalized.endswith(".onnx"):
        return "onnx"
    if normalized.endswith(".tflite"):
        return "tflite"
    if platform.system() == "Darwin":
        return "onnx"
    if _module_available("ai_edge_litert") or _module_available("tflite_runtime"):
        return "tflite"
    return "onnx"


def _module_available(module_name: str) -> bool:
    try:
        __import__(module_name)
    except ImportError:
        return False
    return True


@dataclass(slots=True)
class StreamingWakeWordDetector:
    """Translate a raw PCM stream into frame-by-frame wake detections."""

    model: WakeWordModelAdapter
    threshold: float
    sample_rate: int
    channels: int
    sample_width: int
    frame_duration_seconds: float = 0.08
    patience_frames: int = 1
    debounce_seconds: float = 1.0
    _frame_buffer: bytearray = field(default_factory=bytearray, init=False, repr=False)
    _frame_buffer_start_offset: int | None = field(default=None, init=False, repr=False)
    _consecutive_hits: int = field(default=0, init=False, repr=False)
    _debounce_until_offset: int = field(default=0, init=False, repr=False)

    @property
    def frame_byte_count(self) -> int:
        return _seconds_to_byte_offset(
            seconds=self.frame_duration_seconds,
            channels=self.channels,
            sample_width=self.sample_width,
            sample_rate=self.sample_rate,
        )

    @property
    def debounce_byte_count(self) -> int:
        return _seconds_to_byte_offset(
            seconds=self.debounce_seconds,
            channels=self.channels,
            sample_width=self.sample_width,
            sample_rate=self.sample_rate,
        )

    def process_chunk(self, chunk: bytes, stream_start_offset: int) -> int | None:
        if not chunk:
            return None

        expected_start = None
        if self._frame_buffer_start_offset is not None:
            expected_start = self._frame_buffer_start_offset + len(self._frame_buffer)
        if expected_start is None or stream_start_offset != expected_start:
            self._frame_buffer.clear()
            self._frame_buffer_start_offset = stream_start_offset

        self._frame_buffer.extend(chunk)
        frame_byte_count = self.frame_byte_count
        if frame_byte_count <= 0:
            return None

        while len(self._frame_buffer) >= frame_byte_count:
            frame = bytes(self._frame_buffer[:frame_byte_count])
            del self._frame_buffer[:frame_byte_count]
            frame_end_offset = (self._frame_buffer_start_offset or stream_start_offset) + frame_byte_count
            self._frame_buffer_start_offset = frame_end_offset if self._frame_buffer else None
            score = self.model.score_frame(frame)
            if score >= self.threshold:
                self._consecutive_hits += 1
            else:
                self._consecutive_hits = 0
            if self._consecutive_hits < max(1, self.patience_frames):
                continue
            if frame_end_offset < self._debounce_until_offset:
                continue
            self._debounce_until_offset = frame_end_offset + self.debounce_byte_count
            self._consecutive_hits = 0
            return frame_end_offset
        return None


@dataclass(slots=True)
class OpenWakeWordWakeWordService:
    """Wake-word detector backed by OpenWakeWord on the shared live stream."""

    audio_capture: AudioCaptureService
    wake_phrase: str = ""
    wake_word_model: str = ""
    wake_threshold: float = 0.5
    wake_lookback_seconds: float = 0.8
    poll_interval_seconds: float = 0.08
    speech_energy_threshold: int = 60
    frame_duration_seconds: float = 0.08
    trigger_patience_frames: int = 1
    trigger_debounce_seconds: float = 1.0
    terminal_debug: TerminalDebugSink | None = None
    shared_live_state: SharedLiveSpeechState | None = None
    model_factory: Callable[[str], WakeWordModelAdapter] = _default_openwakeword_model_factory
    _model: WakeWordModelAdapter = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.shared_live_state is None:
            raise RuntimeError("OpenWakeWord wake detection requires a shared live audio state")
        if not self.wake_word_model.strip():
            raise RuntimeError("OpenWakeWord wake detection requires a configured wake word model")
        self._model = self.model_factory(self.wake_word_model)

    async def wait_for_wake_word(self) -> WakeDetectionResult:
        wake_phrase = self.wake_phrase.strip()
        if not wake_phrase:
            return WakeDetectionResult(detected=True)
        if self.shared_live_state is None:
            raise RuntimeError("shared wake detection requires shared live audio state")

        self._model.reset()
        detector = StreamingWakeWordDetector(
            model=self._model,
            threshold=self.wake_threshold,
            sample_rate=self.shared_live_state.sample_rate,
            channels=self.shared_live_state.channels,
            sample_width=self.shared_live_state.sample_width,
            frame_duration_seconds=self.frame_duration_seconds,
            patience_frames=self.trigger_patience_frames,
            debounce_seconds=self.trigger_debounce_seconds,
        )
        wake_event = asyncio.Event()
        detection_stream_offset: int | None = None

        def on_chunk(chunk: bytes, stream_start_offset: int) -> None:
            nonlocal detection_stream_offset
            frame_detection_offset = detector.process_chunk(chunk, stream_start_offset)
            if frame_detection_offset is None or detection_stream_offset is not None:
                return
            detection_stream_offset = frame_detection_offset
            wake_event.set()

        self.shared_live_state.add_chunk_listener(on_chunk)
        self._publish_wake_status("listening", wake_phrase)
        try:
            await self.shared_live_state.ensure_session()
            self._publish_ring_buffer_state(self.wake_lookback_seconds)
            while True:
                if detection_stream_offset is not None:
                    break
                try:
                    await asyncio.wait_for(wake_event.wait(), timeout=self.poll_interval_seconds)
                except asyncio.TimeoutError:
                    pass
                wake_event.clear()
                await self.shared_live_state.sync()
                self._publish_ring_buffer_state(self.wake_lookback_seconds)
                self._publish_wake_audio()

            lookback_offset = _seconds_to_byte_offset(
                seconds=self.wake_lookback_seconds,
                channels=self.shared_live_state.channels,
                sample_width=self.shared_live_state.sample_width,
                sample_rate=self.shared_live_state.sample_rate,
            )
            utterance_start_stream_offset = max(
                self.shared_live_state.wake_buffer_start_offset,
                detection_stream_offset - lookback_offset,
            )
            self.shared_live_state.start_utterance(stream_start_offset=utterance_start_stream_offset)
            self._publish_ring_buffer_state(self.wake_lookback_seconds)
            self._publish_wake_status("awake", wake_phrase)
            logger.info(
                "turn_trace wake_word_detected phrase=%s stream_offset=%s lookback_seconds=%.2f",
                wake_phrase,
                utterance_start_stream_offset,
                self.wake_lookback_seconds,
            )
            return WakeDetectionResult(
                detected=True,
                audio_window=self.shared_live_state.current_wake_window(
                    duration_seconds=self.wake_lookback_seconds,
                    threshold=self.speech_energy_threshold,
                ),
                utterance_stream_start_offset=utterance_start_stream_offset,
            )
        except asyncio.CancelledError:
            self._publish_wake_status("listening", wake_phrase)
            raise
        finally:
            self.shared_live_state.remove_chunk_listener(on_chunk)

    def _publish_wake_status(self, status: str, detail: str | None = None) -> None:
        if self.terminal_debug is not None:
            self.terminal_debug.update_wake_status(status, detail)

    def _publish_ring_buffer_state(self, wake_window_seconds: float) -> None:
        if self.terminal_debug is None or self.shared_live_state is None:
            return
        capacity_seconds, filled_seconds, wake_window, utterance_start, write_head = (
            self.shared_live_state.ring_buffer_debug_state(wake_window_seconds=wake_window_seconds)
        )
        self.terminal_debug.update_ring_buffer(
            capacity_seconds=capacity_seconds,
            filled_seconds=filled_seconds,
            wake_window_seconds=wake_window,
            utterance_start_seconds=utterance_start,
            write_head_seconds=write_head,
        )

    def _publish_wake_audio(self) -> None:
        if self.shared_live_state is None or self.terminal_debug is None:
            return
        audio_window = self.shared_live_state.current_wake_window(
            duration_seconds=self.wake_lookback_seconds,
            threshold=self.speech_energy_threshold,
        )
        if audio_window is None:
            self.terminal_debug.update_audio(
                current_noise=0.0,
                peak_energy=0.0,
                trailing_silence_seconds=self.wake_lookback_seconds,
                speech_started=False,
                vad_active=False,
                partial_pending=False,
            )
            return
        self.terminal_debug.update_audio(
            current_noise=audio_window.current_energy,
            peak_energy=audio_window.peak_energy,
            trailing_silence_seconds=audio_window.trailing_silence_seconds,
            speech_started=False,
            vad_active=False,
            partial_pending=False,
        )


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


def _seconds_to_byte_offset(*, seconds: float, channels: int, sample_width: int, sample_rate: int) -> int:
    return max(1, int(seconds * channels * sample_width * sample_rate))
