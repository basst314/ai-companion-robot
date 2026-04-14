"""Tests for main runtime helper branches."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

import main as main_mod
from ai.cloud import MockCloudResponseService, OpenAiCloudResponseService
from hardware.service import MockHardwareService
from memory.service import InMemoryMemoryService
from shared.config import AppConfig
from shared.models import Language, UserIdentity, VisionDetection
from stt.service import MockSttService
from tts.service import MockTtsService
from ui.browser_service import BrowserFaceUiService
from ui.service import MockUiService
from vision.service import MockVisionService


class _FakeWhisperStt:
    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.kwargs = kwargs
        self.ensure_endpoint_vad_ready_calls = 0

    def ensure_endpoint_vad_ready(self) -> None:
        self.ensure_endpoint_vad_ready_calls += 1


class _FakeWakeWordService:
    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.kwargs = kwargs


class _FakeTts:
    def __init__(self) -> None:
        self.bound_handler = None

    def bind_event_handler(self, handler) -> None:  # type: ignore[no-untyped-def]
        self.bound_handler = handler


class _FakeUi:
    def __init__(self) -> None:
        self.events: list[object] = []

    def handle_event(self, event) -> None:  # type: ignore[no-untyped-def]
        self.events.append(event)


class _FakeService:
    def __init__(self) -> None:
        self.terminal_debug = object()
        self.run_calls = 0

    async def run(self) -> None:
        self.run_calls += 1

    def handle_event(self, event) -> None:  # type: ignore[no-untyped-def]
        del event


def _base_config() -> AppConfig:
    config = AppConfig()
    config.mocks.active_user_id = "sebastian"
    config.mocks.active_user_name = "Sebastian"
    config.mocks.active_user_summary = "You are Sebastian."
    config.mocks.visible_people = ("Sebastian", "Ari")
    config.runtime.manual_inputs = ("hello",)
    return config


def test_build_cloud_services_handles_mock_and_openai_modes() -> None:
    mock_config = _base_config()
    mock_config.runtime.use_mock_ai = True
    assert isinstance(main_mod._build_cloud_services(mock_config), MockCloudResponseService)

    openai_config = _base_config()
    openai_config.runtime.use_mock_ai = False
    openai_config.cloud.enabled = True
    openai_config.cloud.provider_name = "openai"
    openai_config.cloud.openai_api_key = "test-key"
    openai_config.cloud.openai_response_model = "gpt-5.2"
    service = main_mod._build_cloud_services(openai_config)

    assert isinstance(service, OpenAiCloudResponseService)
    assert service.model == "gpt-5.2"

    invalid_config = _base_config()
    invalid_config.runtime.use_mock_ai = False
    invalid_config.cloud.enabled = True
    invalid_config.cloud.provider_name = "anthropic"
    with pytest.raises(RuntimeError, match="only the OpenAI cloud provider"):
        main_mod._build_cloud_services(invalid_config)


def test_build_tts_service_and_ui_service_cover_backend_branches(monkeypatch, tmp_path: Path) -> None:
    config = _base_config()
    config.paths.data_dir = tmp_path / "data"
    config.tts.backend = "mock"
    config.ui.backend = "mock"

    tts = main_mod._build_tts_service(config)
    ui = main_mod._build_ui_service(config)

    assert isinstance(tts, MockTtsService)
    assert isinstance(ui, MockUiService)
    assert ui.echo_state_to_console is True
    assert ui.echo_text_to_console is True

    config.tts.backend = "piper"
    config.tts.piper_base_url = "http://127.0.0.1:5001"
    config.tts.piper_data_dir = tmp_path / "voices"
    config.tts.default_voice_en = "en_US-hfc_female-medium"
    config.tts.default_voice_de = "de_DE-thorsten-medium"
    config.tts.default_voice_id = "id_ID-news_tts-medium"
    config.tts.audio_backend = "command"
    config.tts.audio_play_command = ("aplay", "{input_path}")
    captured: dict[str, object] = {}

    def fake_build_piper_tts_service(tts_config, *, audio_output_dir, terminal_debug=None):  # type: ignore[no-untyped-def]
        captured["tts_config"] = tts_config
        captured["audio_output_dir"] = audio_output_dir
        captured["terminal_debug"] = terminal_debug
        return "piper-service"

    monkeypatch.setattr(main_mod, "build_piper_tts_service", fake_build_piper_tts_service)
    assert main_mod._build_tts_service(config, terminal_debug=object()) == "piper-service"
    assert captured["audio_output_dir"] == tmp_path / "data" / "audio" / "tts"

    config.tts.backend = "cloud"
    with pytest.raises(RuntimeError, match="cloud TTS is not implemented yet"):
        main_mod._build_tts_service(config)

    config.ui.backend = "browser"
    browser_ui = main_mod._build_ui_service(config)
    assert isinstance(browser_ui, BrowserFaceUiService)


def test_build_speech_and_wake_word_services_cover_error_and_success_paths(monkeypatch, tmp_path: Path) -> None:
    config = _base_config()
    config.paths.data_dir = tmp_path / "data"
    config.runtime.stt_backend = "mock"
    config.runtime.wake_word_enabled = False

    stt, wake_word = main_mod._build_speech_services(config)
    assert isinstance(stt, MockSttService)
    assert wake_word is None

    config.runtime.stt_backend = "whisper_cpp"
    config.runtime.whisper_model_path = None
    with pytest.raises(RuntimeError, match="whisper.model_path"):
        main_mod._build_speech_services(config)

    config.runtime.whisper_model_path = tmp_path / "model.bin"
    config.runtime.input_mode = "speech"
    config.runtime.audio_record_command = ("rec", "{output_path}")
    config.runtime.wake_word_enabled = True
    config.runtime.wake_word_phrase = "Oreo"
    config.runtime.wake_word_model = "wake.tflite"
    config.runtime.use_mock_ai = True

    real_build_wake_word_service = main_mod._build_wake_word_service
    monkeypatch.setattr(main_mod, "ShellAudioCaptureService", lambda **kwargs: type("Audio", (), {"sample_rate": 16000, "channels": 1, "sample_width": 2})())
    monkeypatch.setattr(main_mod, "SharedLiveSpeechState", lambda **kwargs: object())
    monkeypatch.setattr(main_mod, "WhisperCppSttService", _FakeWhisperStt)
    monkeypatch.setattr(main_mod, "_build_wake_word_service", lambda *args, **kwargs: _FakeWakeWordService())

    stt, wake_word = main_mod._build_speech_services(config)
    assert isinstance(stt, _FakeWhisperStt)
    assert isinstance(wake_word, _FakeWakeWordService)
    assert stt.ensure_endpoint_vad_ready_calls == 1

    config.runtime.input_mode = "manual"
    with pytest.raises(RuntimeError, match="wake word support requires speech input mode"):
        real_build_wake_word_service(config, shared_live_state=object())


def test_speech_latency_kwargs_and_wake_word_helper_branches() -> None:
    runtime = _base_config().runtime
    assert main_mod._speech_latency_kwargs(runtime)["poll_interval_seconds"] == 0.10
    runtime.speech_latency_profile = "balanced"
    assert main_mod._speech_latency_kwargs(runtime)["poll_interval_seconds"] == 0.35

    config = _base_config()
    config.runtime.wake_word_enabled = False
    terminal = type(
        "_Terminal",
        (),
        {
            "updates": [],
            "update_wake_status": lambda self, status, detail=None: self.updates.append((status, detail)),
        },
    )()
    assert main_mod._build_wake_word_service(config, terminal_debug=terminal) is None
    assert terminal.updates == [("off", "--")]

    config.runtime.wake_word_enabled = True
    config.runtime.wake_word_phrase = "Oreo"
    config.runtime.input_mode = "manual"
    config.runtime.stt_backend = "mock"
    with pytest.raises(RuntimeError, match="speech input mode"):
        main_mod._build_wake_word_service(config, shared_live_state=object())


def test_resolve_runtime_path_and_runtime_logging(monkeypatch, tmp_path: Path) -> None:
    absolute_path = tmp_path / "absolute"
    relative_path = Path("relative")
    assert main_mod._resolve_runtime_path(absolute_path) == absolute_path
    assert main_mod._resolve_runtime_path(relative_path) == Path.cwd() / relative_path

    log_path = tmp_path / "logs" / "runtime.log"
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    try:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)

        main_mod._configure_runtime_logging(log_path)
        first_handlers = [handler for handler in root_logger.handlers if isinstance(handler, logging.FileHandler)]
        main_mod._configure_runtime_logging(log_path)
        second_handlers = [handler for handler in root_logger.handlers if isinstance(handler, logging.FileHandler)]

        assert len(first_handlers) == 1
        assert len(second_handlers) == 1
        assert log_path.parent.exists()
    finally:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
        for handler in original_handlers:
            root_logger.addHandler(handler)


def test_main_auto_run_invokes_runtime_branch(monkeypatch, tmp_path: Path) -> None:
    config = _base_config()
    config.runtime.auto_run = True
    config.paths.logs_dir = tmp_path / "logs"
    service = _FakeService()
    calls: list[tuple[str, object | None]] = []

    monkeypatch.setattr(main_mod, "build_application", lambda cfg=None: service)
    monkeypatch.setattr(main_mod, "configure_console_log", lambda path: calls.append(("log", path)))
    monkeypatch.setattr(main_mod, "configure_terminal_debug_screen", lambda screen: calls.append(("debug", screen)))

    result = main_mod.main(config)

    assert result == 0
    assert service.run_calls == 1
    assert calls[0][0] == "log"
    assert calls[1] == ("debug", service.terminal_debug)
    assert calls[-1] == ("debug", None)
