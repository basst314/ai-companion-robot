"""Tests for the real STT adapter and speech-mode runtime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from main import build_application
from shared.config import AppConfig
from shared.events import EventName
from shared.models import Language
from stt.service import CommandResult, WhisperCppSttService


@dataclass(slots=True)
class FakeAudioCaptureService:
    """Return a stable WAV path without touching the microphone."""

    output_path: Path

    async def capture_wav(self) -> Path:
        return self.output_path


@dataclass(slots=True)
class FailingSttService:
    """Raise a deterministic failure for speech-loop tests."""

    async def listen_once(self):  # type: ignore[no-untyped-def]
        raise RuntimeError("mock stt failure")

    async def stream_transcripts(self):  # type: ignore[no-untyped-def]
        if False:
            yield None


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
        runner=lambda command: asyncio.sleep(0, result=CommandResult(
            args=command,
            returncode=0,
            stdout='{"result":{"language":"en"},"transcription":[]}',
        )),
    )

    asyncio.run(service.run())

    assert service.state.lifecycle.value == "idle"
    assert service.state.last_error is None
    assert service.state.current_transcript is not None
    assert service.state.current_transcript.text == ""
    assert not any(event.name is EventName.ERROR_OCCURRED for event in service.event_history)
    assert not any(event.name is EventName.TRANSCRIPT_FINAL for event in service.event_history)
