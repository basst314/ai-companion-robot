"""Tests for the real STT adapter and speech-mode runtime."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from main import build_application
from shared.config import AppConfig
from shared.console import TerminalDebugSink
from shared.events import EventName
from shared.models import Language, Transcript
from stt.service import (
    AudioWindow,
    CommandResult,
    OpenWakeWordWakeWordService,
    SharedLiveSpeechState,
    StreamingWakeWordDetector,
    WhisperCppSttService,
    _select_openwakeword_inference_framework,
    _default_run_command,
    _slice_audio_window,
    strip_wake_phrase,
)


@dataclass(slots=True)
class FakeAudioCaptureService:
    """Return a stable WAV path without touching the microphone."""

    output_path: Path

    async def capture_wav(self) -> Path:
        return self.output_path


@dataclass(slots=True)
class FakeRecordingSession:
    """In-memory recording session for streaming tests."""

    output_path: Path
    returncode: int | None = None
    stop_requested: bool = False

    def mark_stop_requested(self) -> None:
        self.stop_requested = True

    async def stop(self) -> None:
        self.stop_requested = True
        self.returncode = 0

    async def wait(self) -> int:
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode


@dataclass(slots=True)
class FakeStreamingAudioCaptureService:
    """Provide a stable recording session without launching a subprocess."""

    output_path: Path
    session: FakeRecordingSession | None = None

    async def start_capture(self, on_chunk=None) -> FakeRecordingSession:  # type: ignore[no-untyped-def]
        del on_chunk
        self.session = FakeRecordingSession(output_path=self.output_path)
        return self.session

    async def capture_wav(self) -> Path:
        return self.output_path


@dataclass(slots=True)
class FailingSttService:
    """Raise a deterministic failure for speech-loop tests."""

    async def listen_once(self):  # type: ignore[no-untyped-def]
        raise RuntimeError("mock stt failure")

    async def stream_transcripts(self):  # type: ignore[no-untyped-def]
        raise RuntimeError("mock stt failure")
        if False:
            yield None


@dataclass(slots=True)
class ScriptedStreamingWhisperService(WhisperCppSttService):
    """Whisper service with deterministic audio windows and transcript text."""

    windows: list[AudioWindow | None] = field(default_factory=list)
    transcript_texts: list[str] = field(default_factory=list)
    _window_index: int = 0
    _transcript_index: int = 0
    captured_is_final: list[bool] = field(default_factory=list)

    def _read_audio_window(self, audio_path: Path) -> AudioWindow | None:
        if self._window_index >= len(self.windows):
            return self.windows[-1] if self.windows else None
        window = self.windows[self._window_index]
        self._window_index += 1
        return window

    async def _transcribe_snapshot(  # type: ignore[override]
        self,
        audio_window: AudioWindow,
        started_at,
        *,
        is_final: bool,
    ) -> Transcript:
        text = self.transcript_texts[min(self._transcript_index, len(self.transcript_texts) - 1)]
        self._transcript_index += 1
        self.captured_is_final.append(is_final)
        return Transcript(
            text=text,
            language=Language.ENGLISH,
            confidence=1.0,
            is_final=is_final,
            started_at=started_at,
            ended_at=None,
        )


@dataclass(slots=True)
class FakeWakeWordModel:
    scores: list[float] = field(default_factory=list)
    calls: int = 0
    reset_calls: int = 0

    def score_frame(self, pcm_frame: bytes) -> float:
        del pcm_frame
        if self.calls >= len(self.scores):
            score = self.scores[-1] if self.scores else 0.0
        else:
            score = self.scores[self.calls]
        self.calls += 1
        return score

    def reset(self) -> None:
        self.calls = 0
        self.reset_calls += 1


@dataclass(slots=True)
class RecordingTerminalDebugSink(TerminalDebugSink):
    runtime_updates: list[dict[str, object]] = field(default_factory=list)
    audio_updates: list[dict[str, object]] = field(default_factory=list)
    transcript_updates: list[dict[str, object]] = field(default_factory=list)
    whisper_updates: list[str | None] = field(default_factory=list)
    wake_updates: list[dict[str, str | None]] = field(default_factory=list)
    ring_updates: list[dict[str, float | None]] = field(default_factory=list)

    def activate(self) -> None:
        return

    def close(self) -> None:
        return

    def update_runtime(
        self,
        *,
        lifecycle: str,
        emotion: str,
        language: str | None = None,
        route_summary: str | None = None,
        last_error: str | None = None,
    ) -> None:
        self.runtime_updates.append(
            {
                "lifecycle": lifecycle,
                "emotion": emotion,
                "language": language,
                "route_summary": route_summary,
                "last_error": last_error,
            }
        )

    def update_audio(
        self,
        *,
        current_noise: float | None = None,
        peak_energy: float | None = None,
        trailing_silence_seconds: float | None = None,
        speech_started: bool | None = None,
        partial_pending: bool | None = None,
    ) -> None:
        self.audio_updates.append(
            {
                "current_noise": current_noise,
                "peak_energy": peak_energy,
                "trailing_silence_seconds": trailing_silence_seconds,
                "speech_started": speech_started,
                "partial_pending": partial_pending,
            }
        )

    def update_transcript(
        self,
        text: str,
        *,
        language: str | None = None,
        is_final: bool = False,
    ) -> None:
        self.transcript_updates.append({"text": text, "language": language, "is_final": is_final})

    def update_whisper_status(self, status: str | None) -> None:
        self.whisper_updates.append(status)

    def update_wake_status(self, status: str, detail: str | None = None) -> None:
        self.wake_updates.append({"status": status, "detail": detail})

    def update_ring_buffer(
        self,
        *,
        capacity_seconds: float | None = None,
        filled_seconds: float | None = None,
        wake_window_seconds: float | None = None,
        stride_seconds: float | None = None,
        utterance_start_seconds: float | None = None,
        write_head_seconds: float | None = None,
    ) -> None:
        self.ring_updates.append(
            {
                "capacity_seconds": capacity_seconds,
                "filled_seconds": filled_seconds,
                "wake_window_seconds": wake_window_seconds,
                "stride_seconds": stride_seconds,
                "utterance_start_seconds": utterance_start_seconds,
                "write_head_seconds": write_head_seconds,
            }
        )


def _audio_window(
    wav_path: Path,
    *,
    duration_seconds: float,
    trailing_silence_seconds: float,
    has_speech: bool,
    current_energy: float = 120.0,
    peak_energy: float = 200.0,
) -> AudioWindow:
    return AudioWindow(
        source_path=wav_path,
        channels=1,
        sample_width=2,
        sample_rate=16000,
        pcm_data=b"\x00\x00" * max(1, int(16000 * duration_seconds)),
        duration_seconds=duration_seconds,
        trailing_silence_seconds=trailing_silence_seconds,
        has_speech=has_speech,
        current_energy=current_energy,
        peak_energy=peak_energy,
    )


def test_whisper_cpp_stt_service_parses_json_output() -> None:
    """The CLI adapter should convert whisper.cpp JSON into a final transcript."""

    async def fake_runner(command: tuple[str, ...]) -> CommandResult:
        assert command == (
            "/usr/local/bin/whisper-cli",
            "-m",
            "/models/ggml-base.en.bin",
            "-f",
            "/tmp/input.wav",
            "--output-json",
            "--output-file",
            "/tmp/input",
            "-l",
            "de",
        )
        return CommandResult(
            args=command,
            returncode=0,
            stdout='{"result":{"language":"de","text":"Hallo Sebastian"}}',
        )

    service = WhisperCppSttService(
        audio_capture=FakeAudioCaptureService(Path("/tmp/input.wav")),
        model_path=Path("/models/ggml-base.en.bin"),
        binary_path=Path("/usr/local/bin/whisper-cli"),
        language_mode="de",
        runner=fake_runner,
    )

    transcript = asyncio.run(service.listen_once())

    assert transcript.text == "Hallo Sebastian"
    assert transcript.language is Language.GERMAN
    assert transcript.is_final is True
    assert transcript.confidence == 1.0


def test_default_run_command_tolerates_non_utf8_output(tmp_path: Path) -> None:
    script = tmp_path / "emit_non_utf8.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(b'prefix' + bytes([0xFF, 0xFE]))\n"
        "sys.stderr.buffer.write(b'warn' + bytes([0xFF]))\n"
    )

    result = asyncio.run(_default_run_command(("python3", str(script))))

    assert result.returncode == 0
    assert "prefix" in result.stdout
    assert "\ufffd" in result.stdout
    assert "warn" in result.stderr


def test_whisper_cpp_stt_service_surfaces_subprocess_failures() -> None:
    """Transcription command failures should become clean runtime errors."""

    async def failing_runner(command: tuple[str, ...]) -> CommandResult:
        return CommandResult(args=command, returncode=1, stderr="whisper crashed")

    service = WhisperCppSttService(
        audio_capture=FakeAudioCaptureService(Path("/tmp/input.wav")),
        model_path=Path("/models/ggml-base.en.bin"),
        binary_path=Path("/usr/local/bin/whisper-cli"),
        runner=failing_runner,
    )

    try:
        asyncio.run(service.listen_once())
    except RuntimeError as exc:
        assert str(exc) == "whisper crashed"
    else:
        raise AssertionError("expected WhisperCppSttService to raise RuntimeError")


def test_whisper_cpp_stt_service_uses_auto_language_mode_by_default() -> None:
    """Auto language mode should be passed explicitly to whisper.cpp."""

    captured_command: tuple[str, ...] | None = None

    async def fake_runner(command: tuple[str, ...]) -> CommandResult:
        nonlocal captured_command
        captured_command = command
        return CommandResult(
            args=command,
            returncode=0,
            stdout='{"result":{"language":"id","text":"Halo Sebastian"}}',
        )

    service = WhisperCppSttService(
        audio_capture=FakeAudioCaptureService(Path("/tmp/input.wav")),
        model_path=Path("/models/ggml-base.bin"),
        binary_path=Path("/usr/local/bin/whisper-cli"),
        runner=fake_runner,
    )

    transcript = asyncio.run(service.listen_once())

    assert captured_command is not None
    assert "-l" in captured_command
    assert "auto" in captured_command
    assert transcript.language is Language.INDONESIAN


def test_whisper_cpp_stt_service_parses_transcription_array_shape() -> None:
    """The adapter should handle the JSON file shape produced by whisper.cpp CLI."""

    async def fake_runner(command: tuple[str, ...]) -> CommandResult:
        return CommandResult(
            args=command,
            returncode=0,
            stdout=(
                '{"result":{"language":"en"},'
                '"transcription":[{"text":" hello"},{"text":" world"}]}'
            ),
        )

    service = WhisperCppSttService(
        audio_capture=FakeAudioCaptureService(Path("/tmp/input.wav")),
        model_path=Path("/models/ggml-base.bin"),
        binary_path=Path("/usr/local/bin/whisper-cli"),
        runner=fake_runner,
    )

    transcript = asyncio.run(service.listen_once())

    assert transcript.text == "hello world"
    assert transcript.language is Language.ENGLISH


def test_whisper_cpp_stt_service_returns_empty_transcript_for_silence() -> None:
    """A silent recording should not be treated as an STT failure."""

    async def fake_runner(command: tuple[str, ...]) -> CommandResult:
        return CommandResult(
            args=command,
            returncode=0,
            stdout='{"result":{"language":"en"},"transcription":[]}',
        )

    service = WhisperCppSttService(
        audio_capture=FakeAudioCaptureService(Path("/tmp/input.wav")),
        model_path=Path("/models/ggml-base.bin"),
        binary_path=Path("/usr/local/bin/whisper-cli"),
        runner=fake_runner,
    )

    transcript = asyncio.run(service.listen_once())

    assert transcript.text == ""
    assert transcript.language is Language.ENGLISH
    assert transcript.is_final is True


def test_whisper_cpp_stt_service_keeps_last_five_recordings(tmp_path: Path) -> None:
    """Successful transcriptions should retain only a small rolling artifact history."""

    recent_audio = []
    for index in range(7):
        audio_path = tmp_path / f"ai-companion-recording-{index}.wav"
        audio_path.write_text("wav")
        (tmp_path / f"{audio_path.name}.json").write_text('{"result":{"language":"en"},"transcription":[{"text":"test"}]}')
        recent_audio.append(audio_path)

    async def fake_runner(command: tuple[str, ...]) -> CommandResult:
        return CommandResult(
            args=command,
            returncode=0,
            stdout='{"result":{"language":"en"},"transcription":[{"text":"hello"}]}',
        )

    service = WhisperCppSttService(
        audio_capture=FakeAudioCaptureService(recent_audio[-1]),
        model_path=Path("/models/ggml-base.bin"),
        binary_path=Path("/usr/local/bin/whisper-cli"),
        runner=fake_runner,
    )

    asyncio.run(service.listen_once())

    remaining_wavs = sorted(path.name for path in tmp_path.glob("ai-companion-recording-*.wav"))
    remaining_json = sorted(path.name for path in tmp_path.glob("ai-companion-recording-*.wav.json"))

    assert remaining_wavs == [
        "ai-companion-recording-2.wav",
        "ai-companion-recording-3.wav",
        "ai-companion-recording-4.wav",
        "ai-companion-recording-5.wav",
        "ai-companion-recording-6.wav",
    ]
    assert remaining_json == [
        "ai-companion-recording-2.wav.json",
        "ai-companion-recording-3.wav.json",
        "ai-companion-recording-4.wav.json",
        "ai-companion-recording-5.wav.json",
        "ai-companion-recording-6.wav.json",
    ]


def test_whisper_cpp_stt_service_streams_partials_then_final(tmp_path: Path) -> None:
    """Streaming mode should emit incremental transcript updates before the final result."""

    wav_path = tmp_path / "ai-companion-recording-stream.wav"
    wav_path.write_bytes(b"fake")
    audio_capture = FakeStreamingAudioCaptureService(output_path=wav_path)
    service = ScriptedStreamingWhisperService(
        audio_capture=audio_capture,
        model_path=Path("/models/ggml-base.bin"),
        binary_path=Path("/usr/local/bin/whisper-cli"),
        windows=[
            _audio_window(wav_path, duration_seconds=0.6, trailing_silence_seconds=0.0, has_speech=True),
            _audio_window(wav_path, duration_seconds=1.1, trailing_silence_seconds=0.0, has_speech=True),
            _audio_window(wav_path, duration_seconds=1.8, trailing_silence_seconds=1.3, has_speech=True),
            _audio_window(wav_path, duration_seconds=2.2, trailing_silence_seconds=1.3, has_speech=True),
            _audio_window(wav_path, duration_seconds=2.6, trailing_silence_seconds=1.3, has_speech=True),
            _audio_window(wav_path, duration_seconds=3.0, trailing_silence_seconds=1.3, has_speech=True),
        ],
        transcript_texts=["hello", "hello there", "hello there friend"],
        poll_interval_seconds=0.0,
        partial_update_interval_seconds=0.0,
        speech_silence_seconds=1.2,
        minimum_utterance_seconds=2.0,
        silence_confirmation_polls=1,
    )

    transcripts = asyncio.run(_collect_transcripts(service.stream_transcripts()))

    assert [transcript.text for transcript in transcripts[:-1]] == [
        "hello",
        "hello there",
    ]
    assert transcripts[-1].text == "hello there friend"
    assert [transcript.is_final for transcript in transcripts[:-1]] == [False, False]
    assert transcripts[-1].is_final is True
    assert audio_capture.session is not None
    assert audio_capture.session.stop_requested is True


def test_whisper_cpp_stt_service_requires_confirmed_silence_before_stopping(tmp_path: Path) -> None:
    """A single silence-like poll should not end the recording immediately."""

    wav_path = tmp_path / "ai-companion-recording-confirmed.wav"
    wav_path.write_bytes(b"fake")
    service = ScriptedStreamingWhisperService(
        audio_capture=FakeStreamingAudioCaptureService(output_path=wav_path),
        model_path=Path("/models/ggml-base.bin"),
        binary_path=Path("/usr/local/bin/whisper-cli"),
        windows=[
            _audio_window(wav_path, duration_seconds=1.0, trailing_silence_seconds=0.0, has_speech=True),
            _audio_window(wav_path, duration_seconds=2.1, trailing_silence_seconds=1.3, has_speech=True),
            _audio_window(wav_path, duration_seconds=2.4, trailing_silence_seconds=0.0, has_speech=True),
            _audio_window(wav_path, duration_seconds=3.0, trailing_silence_seconds=1.3, has_speech=True),
            _audio_window(wav_path, duration_seconds=3.4, trailing_silence_seconds=1.3, has_speech=True),
            _audio_window(wav_path, duration_seconds=3.8, trailing_silence_seconds=1.3, has_speech=True),
            _audio_window(wav_path, duration_seconds=3.8, trailing_silence_seconds=1.3, has_speech=True),
        ],
        transcript_texts=["hello", "hello there", "hello there friend"],
        poll_interval_seconds=0.0,
        partial_update_interval_seconds=0.0,
        speech_silence_seconds=1.2,
        minimum_utterance_seconds=2.0,
        silence_confirmation_polls=3,
    )

    transcripts = asyncio.run(_collect_transcripts(service.stream_transcripts()))

    assert transcripts[-1].is_final is True
    assert transcripts[-1].text == "hello there friend"


def test_whisper_cpp_stt_service_returns_empty_final_transcript_when_user_never_speaks(tmp_path: Path) -> None:
    """The streaming adapter should stop after a guard timeout when no speech starts."""

    wav_path = tmp_path / "ai-companion-recording-silent.wav"
    wav_path.write_bytes(b"fake")
    service = ScriptedStreamingWhisperService(
        audio_capture=FakeStreamingAudioCaptureService(output_path=wav_path),
        model_path=Path("/models/ggml-base.bin"),
        binary_path=Path("/usr/local/bin/whisper-cli"),
        windows=[None, None, None],
        transcript_texts=[""],
        poll_interval_seconds=0.0,
        no_speech_timeout_seconds=0.0,
    )

    transcripts = asyncio.run(_collect_transcripts(service.stream_transcripts()))

    assert len(transcripts) == 1
    assert transcripts[0].text == ""
    assert transcripts[0].is_final is True


def test_whisper_cpp_stt_service_publishes_initial_silence_progress(tmp_path: Path) -> None:
    """Terminal debug should show silence growing even before speech starts."""

    wav_path = tmp_path / "ai-companion-recording-initial-silence.wav"
    wav_path.write_bytes(b"fake")
    terminal_debug = RecordingTerminalDebugSink()
    service = ScriptedStreamingWhisperService(
        audio_capture=FakeStreamingAudioCaptureService(output_path=wav_path),
        model_path=Path("/models/ggml-base.bin"),
        binary_path=Path("/usr/local/bin/whisper-cli"),
        terminal_debug=terminal_debug,
        windows=[
            _audio_window(wav_path, duration_seconds=0.2, trailing_silence_seconds=0.0, has_speech=False, peak_energy=0.0),
            _audio_window(wav_path, duration_seconds=0.4, trailing_silence_seconds=0.0, has_speech=False, peak_energy=0.0),
            _audio_window(wav_path, duration_seconds=0.6, trailing_silence_seconds=0.0, has_speech=False, peak_energy=0.0),
        ],
        transcript_texts=[""],
        poll_interval_seconds=0.02,
        quiet_abort_seconds=99.0,
        no_speech_timeout_seconds=0.05,
    )

    transcripts = asyncio.run(_collect_transcripts(service.stream_transcripts()))

    assert transcripts[-1].is_final is True
    silence_updates = [
        update["trailing_silence_seconds"]
        for update in terminal_debug.audio_updates
        if update["trailing_silence_seconds"] is not None
    ]
    assert silence_updates
    assert max(float(value) for value in silence_updates) > 0.0


def test_whisper_cpp_stt_service_skips_partials_for_low_energy_quiet_audio(tmp_path: Path) -> None:
    """Quiet input should not trigger partial Whisper calls before aborting."""

    wav_path = tmp_path / "ai-companion-recording-quiet.wav"
    wav_path.write_bytes(b"fake")
    service = ScriptedStreamingWhisperService(
        audio_capture=FakeStreamingAudioCaptureService(output_path=wav_path),
        model_path=Path("/models/ggml-base.bin"),
        binary_path=Path("/usr/local/bin/whisper-cli"),
        windows=[
            _audio_window(wav_path, duration_seconds=0.8, trailing_silence_seconds=0.0, has_speech=False, peak_energy=20.0),
            _audio_window(wav_path, duration_seconds=1.6, trailing_silence_seconds=0.0, has_speech=False, peak_energy=22.0),
            _audio_window(wav_path, duration_seconds=2.6, trailing_silence_seconds=0.0, has_speech=False, peak_energy=24.0),
            _audio_window(wav_path, duration_seconds=2.6, trailing_silence_seconds=0.0, has_speech=False, peak_energy=24.0),
        ],
        transcript_texts=[""],
        poll_interval_seconds=0.0,
        partial_update_interval_seconds=0.0,
        quiet_abort_seconds=0.0,
    )

    transcripts = asyncio.run(_collect_transcripts(service.stream_transcripts()))

    assert [transcript.is_final for transcript in transcripts] == [True]
    assert transcripts[0].text == ""
    assert service.captured_is_final == [True]


def test_whisper_cpp_stt_service_stops_at_max_recording_length(tmp_path: Path) -> None:
    """A hard recording cap should end streaming even when silence never arrives."""

    wav_path = tmp_path / "ai-companion-recording-max.wav"
    wav_path.write_bytes(b"fake")
    audio_capture = FakeStreamingAudioCaptureService(output_path=wav_path)
    service = ScriptedStreamingWhisperService(
        audio_capture=audio_capture,
        model_path=Path("/models/ggml-base.bin"),
        binary_path=Path("/usr/local/bin/whisper-cli"),
        windows=[
            _audio_window(wav_path, duration_seconds=1.0, trailing_silence_seconds=0.0, has_speech=True),
            _audio_window(wav_path, duration_seconds=2.1, trailing_silence_seconds=0.0, has_speech=True),
            _audio_window(wav_path, duration_seconds=3.3, trailing_silence_seconds=0.0, has_speech=True),
            _audio_window(wav_path, duration_seconds=3.3, trailing_silence_seconds=0.0, has_speech=True),
        ],
        transcript_texts=["still listening"],
        poll_interval_seconds=0.0,
        partial_update_interval_seconds=999.0,
        minimum_utterance_seconds=99.0,
        max_recording_seconds=3.0,
        speech_silence_seconds=1.2,
    )

    transcripts = asyncio.run(_collect_transcripts(service.stream_transcripts()))

    assert [transcript.text for transcript in transcripts] == ["still listening"]
    assert transcripts[0].is_final is True
    assert audio_capture.session is not None
    assert audio_capture.session.stop_requested is True
    assert service.captured_is_final == [True]


def test_strip_wake_phrase_removes_detected_phrase_case_insensitively() -> None:
    assert strip_wake_phrase("Oreo open your eyes", "oreo") == "open your eyes"
    assert strip_wake_phrase("please Oreo look at me", "oreo") == "look at me"
    assert strip_wake_phrase("Oreo, open your eyes", "oreo") == "open your eyes"
    assert strip_wake_phrase("hello there", "oreo") is None


def test_select_openwakeword_inference_framework_uses_model_suffix_when_present() -> None:
    assert _select_openwakeword_inference_framework("/tmp/hey_jarvis.onnx") == "onnx"
    assert _select_openwakeword_inference_framework("/tmp/hey_jarvis.tflite") == "tflite"


def test_streaming_wake_word_detector_debounces_repeated_hits() -> None:
    frame = b"\x00\x00" * 1280
    detector = StreamingWakeWordDetector(
        model=FakeWakeWordModel(scores=[0.2, 0.9, 0.95, 0.92]),
        threshold=0.5,
        sample_rate=16000,
        channels=1,
        sample_width=2,
        debounce_seconds=0.3,
    )

    first_detection = detector.process_chunk(frame, 0)
    second_detection = detector.process_chunk(frame, len(frame))
    third_detection = detector.process_chunk(frame, len(frame) * 2)
    fourth_detection = detector.process_chunk(frame, len(frame) * 3)

    assert first_detection is None
    assert second_detection == len(frame) * 2
    assert third_detection is None
    assert fourth_detection is None


def test_open_wake_word_service_detects_from_shared_stream_and_sets_handoff_offset(tmp_path: Path) -> None:
    wav_path = tmp_path / "wake-shared.pcm"
    wav_path.write_bytes(b"")
    shared_state = SharedLiveSpeechState(
        audio_capture=FakeStreamingAudioCaptureService(output_path=wav_path),
        wake_buffer_seconds=2.0,
        sample_rate=16000,
        channels=1,
        sample_width=2,
    )
    fake_model = FakeWakeWordModel(scores=[0.1] * 11 + [0.9])
    service = OpenWakeWordWakeWordService(
        audio_capture=shared_state.audio_capture,
        wake_phrase="Hey Jarvis",
        wake_word_model="hey jarvis",
        wake_lookback_seconds=0.8,
        shared_live_state=shared_state,
        model_factory=lambda _model: fake_model,
    )
    frame = b"\x01\x00" * 1280

    async def run_detection() -> WakeDetectionResult:
        wait_task = asyncio.create_task(service.wait_for_wake_word())
        await asyncio.sleep(0)
        for _ in range(12):
            shared_state._handle_chunk(frame)
            await asyncio.sleep(0)
        return await asyncio.wait_for(wait_task, timeout=1.0)

    detection = asyncio.run(run_detection())

    assert detection.detected is True
    assert detection.prefilled_command_text == ""
    assert detection.utterance_stream_start_offset == 5120
    assert detection.audio_window is not None
    assert 0.75 <= detection.audio_window.duration_seconds <= 0.85
    assert shared_state.utterance_active is True
    assert fake_model.reset_calls == 1


def test_open_wake_word_service_emits_debug_updates(tmp_path: Path) -> None:
    wav_path = tmp_path / "wake-debug.pcm"
    wav_path.write_bytes(b"")
    terminal_debug = RecordingTerminalDebugSink()
    shared_state = SharedLiveSpeechState(
        audio_capture=FakeStreamingAudioCaptureService(output_path=wav_path),
        wake_buffer_seconds=2.0,
        sample_rate=16000,
        channels=1,
        sample_width=2,
    )
    service = OpenWakeWordWakeWordService(
        audio_capture=shared_state.audio_capture,
        wake_phrase="Hey Jarvis",
        wake_word_model="hey jarvis",
        terminal_debug=terminal_debug,
        shared_live_state=shared_state,
        model_factory=lambda _model: FakeWakeWordModel(scores=[0.1, 0.9]),
    )
    frame = b"\x01\x00" * 1280

    async def run_detection() -> WakeDetectionResult:
        wait_task = asyncio.create_task(service.wait_for_wake_word())
        await asyncio.sleep(0)
        shared_state._handle_chunk(frame)
        await asyncio.sleep(0)
        shared_state._handle_chunk(frame)
        return await asyncio.wait_for(wait_task, timeout=1.0)

    detection = asyncio.run(run_detection())

    assert detection.detected is True
    assert terminal_debug.wake_updates[0] == {"status": "listening", "detail": "Hey Jarvis"}
    assert terminal_debug.wake_updates[-1]["status"] == "awake"
    assert terminal_debug.audio_updates
    assert terminal_debug.ring_updates


def test_shared_live_speech_state_keeps_idle_wake_buffer_bounded(tmp_path: Path) -> None:
    wav_path = tmp_path / "shared-wake.wav"
    shared_state = SharedLiveSpeechState(
        audio_capture=FakeStreamingAudioCaptureService(output_path=wav_path),
        wake_buffer_seconds=1.0,
        sample_rate=16000,
        channels=1,
        sample_width=2,
    )

    shared_state._handle_chunk(b"\x00\x00" * int(16000 * 3.0))
    wake_window = shared_state.current_wake_window(duration_seconds=1.0, threshold=60, source_path=wav_path)

    assert wake_window is not None
    assert 0.9 <= wake_window.duration_seconds <= 1.1


def test_shared_live_speech_state_hands_wake_slice_into_active_utterance(tmp_path: Path) -> None:
    wav_path = tmp_path / "shared-utterance.wav"
    shared_state = SharedLiveSpeechState(
        audio_capture=FakeStreamingAudioCaptureService(output_path=wav_path),
        wake_buffer_seconds=2.0,
        sample_rate=16000,
        channels=1,
        sample_width=2,
    )
    wake_window = _audio_window(wav_path, duration_seconds=1.5, trailing_silence_seconds=0.0, has_speech=True)
    initial_window = _slice_audio_window(wake_window, 0.5, threshold=60)

    shared_state.start_utterance(initial_window=initial_window)
    shared_state._handle_chunk(b"\x00\x00" * int(16000 * 0.5))
    utterance_window = shared_state.current_utterance_window(threshold=60, source_path=wav_path)

    assert utterance_window is not None
    assert 1.4 <= utterance_window.duration_seconds <= 1.6


def test_shared_live_speech_state_uses_stream_offset_for_handoff_growth(tmp_path: Path) -> None:
    wav_path = tmp_path / "shared-offset.wav"
    shared_state = SharedLiveSpeechState(
        audio_capture=FakeStreamingAudioCaptureService(output_path=wav_path),
        wake_buffer_seconds=3.0,
        sample_rate=16000,
        channels=1,
        sample_width=2,
    )
    first_second = b"\x00\x00" * 16000
    second_second = b"\x01\x00" * 16000
    half_second = b"\x02\x00" * 8000

    shared_state._handle_chunk(first_second)
    shared_state._handle_chunk(second_second)

    wake_window = shared_state.current_wake_window(duration_seconds=1.5, threshold=60, source_path=wav_path)
    assert wake_window is not None

    handoff_offset = wake_window.stream_start_offset + len(wake_window.pcm_data) // 2
    shared_state._handle_chunk(half_second)
    shared_state.start_utterance(stream_start_offset=handoff_offset)

    utterance_window = shared_state.current_utterance_window(threshold=60, source_path=wav_path)

    assert utterance_window is not None
    assert 1.2 <= utterance_window.duration_seconds <= 1.3


def test_shared_live_mode_persists_unique_wav_per_utterance(tmp_path: Path) -> None:
    wav_path = tmp_path / "shared-session.wav"
    service = WhisperCppSttService(
        audio_capture=FakeStreamingAudioCaptureService(output_path=wav_path),
        model_path=Path("/models/ggml-base.bin"),
        binary_path=Path("/usr/local/bin/whisper-cli"),
        shared_live_state=SharedLiveSpeechState(
            audio_capture=FakeStreamingAudioCaptureService(output_path=wav_path),
            wake_buffer_seconds=2.0,
            sample_rate=16000,
            channels=1,
            sample_width=2,
        ),
    )

    first_path = service._persist_final_audio(
        _audio_window(wav_path, duration_seconds=1.0, trailing_silence_seconds=0.2, has_speech=True)
    )
    second_path = service._persist_final_audio(
        _audio_window(wav_path, duration_seconds=1.0, trailing_silence_seconds=0.2, has_speech=True)
    )

    assert first_path != second_path
    assert first_path.exists()
    assert second_path.exists()


def test_shared_live_mode_stops_at_max_recording_length(tmp_path: Path) -> None:
    wav_path = tmp_path / "shared-max.wav"
    audio_capture = FakeStreamingAudioCaptureService(output_path=wav_path)
    shared_state = SharedLiveSpeechState(
        audio_capture=audio_capture,
        wake_buffer_seconds=2.0,
        sample_rate=16000,
        channels=1,
        sample_width=2,
    )
    shared_state.start_utterance()
    shared_state._handle_chunk(b"\xff\x7f" * int(16000 * 3.2))

    service = ScriptedStreamingWhisperService(
        audio_capture=audio_capture,
        model_path=Path("/models/ggml-base.bin"),
        binary_path=Path("/usr/local/bin/whisper-cli"),
        transcript_texts=["shared cutoff"],
        poll_interval_seconds=0.0,
        partial_update_interval_seconds=999.0,
        minimum_utterance_seconds=99.0,
        max_recording_seconds=3.0,
        shared_live_state=shared_state,
    )

    transcripts = asyncio.run(_collect_transcripts(service.stream_transcripts()))

    assert [transcript.text for transcript in transcripts] == ["shared cutoff"]
    assert transcripts[0].is_final is True
    assert shared_state.utterance_active is False
    assert service.captured_is_final == [True]


def test_speech_mode_runtime_uses_stt_transcript_for_full_turn() -> None:
    """Speech mode should transcribe one utterance and execute a normal turn."""

    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.stt_backend = "mock"
    config.runtime.manual_inputs = ("open your eyes",)
    service = build_application(config)

    asyncio.run(service.run())

    assert service.state.lifecycle.value == "idle"
    assert service.state.eyes_open is True
    assert service.state.current_response == "Opening my eyes now."
    assert any(event.name is EventName.TRANSCRIPT_FINAL for event in service.event_history)


def test_speech_mode_stt_failure_returns_to_idle() -> None:
    """Speech mode should tolerate STT failures and keep the orchestrator alive."""

    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.stt_backend = "mock"
    service = build_application(config)
    service.stt = FailingSttService()

    asyncio.run(service.run())

    assert service.state.lifecycle.value == "idle"
    assert service.state.last_error == "mock stt failure"
    assert any(event.name is EventName.ERROR_OCCURRED for event in service.event_history)


def test_speech_mode_silent_transcript_returns_to_idle_without_error() -> None:
    """Speech mode should ignore silence instead of routing it as a failure."""

    config = AppConfig()
    config.runtime.input_mode = "speech"
    config.runtime.stt_backend = "mock"
    service = build_application(config)
    service.stt = WhisperCppSttService(
        audio_capture=FakeAudioCaptureService(Path("/tmp/input.wav")),
        model_path=Path("/models/ggml-base.bin"),
        binary_path=Path("/usr/local/bin/whisper-cli"),
        runner=lambda command: asyncio.sleep(
            0,
            result=CommandResult(
                args=command,
                returncode=0,
                stdout='{"result":{"language":"en"},"transcription":[]}',
            ),
        ),
    )

    asyncio.run(service.run())

    assert service.state.lifecycle.value == "idle"
    assert service.state.last_error is None
    assert service.state.current_transcript is not None
    assert service.state.current_transcript.text == ""
    assert not any(event.name is EventName.ERROR_OCCURRED for event in service.event_history)
    assert not any(event.name is EventName.TRANSCRIPT_FINAL for event in service.event_history)


async def _collect_transcripts(stream: AsyncIterator[Transcript]) -> list[Transcript]:
    return [transcript async for transcript in stream]
