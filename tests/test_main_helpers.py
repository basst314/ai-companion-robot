"""Tests for main runtime helper branches."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

import main as main_mod
from ai.cloud import MockCloudResponseService, OpenAiCloudResponseService
from ai.realtime import RealtimeConversationService
from orchestrator.capabilities import build_default_capability_registry
from orchestrator.state import LifecycleStage
from shared.config import AppConfig
from ui.browser_service import BrowserFaceUiService
from ui.service import MockUiService


class _FakeWakeWordService:
    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.kwargs = kwargs


class _StartableCloud:
    def __init__(self) -> None:
        self.start_calls = 0
        self.shutdown_calls = 0

    async def start(self) -> None:
        self.start_calls += 1

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


class _FakeService:
    def __init__(self) -> None:
        self.terminal_debug = object()
        self.run_calls = 0

    async def run(self) -> None:
        self.run_calls += 1


class _CloseTrackingSharedLiveState:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class _MicLevelSharedLiveState(_CloseTrackingSharedLiveState):
    sample_rate = 16000
    channels = 1
    sample_width = 2

    def __init__(self) -> None:
        super().__init__()
        self.listeners = []

    def add_chunk_listener(self, listener):  # type: ignore[no-untyped-def]
        self.listeners.append(listener)

    def remove_chunk_listener(self, listener):  # type: ignore[no-untyped-def]
        if listener in self.listeners:
            self.listeners.remove(listener)


class _ShutdownTrackingWakeWord:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


def _base_config() -> AppConfig:
    config = AppConfig()
    config.runtime.manual_inputs = ("hello",)
    return config


def test_build_cloud_services_handles_mock_openai_and_realtime_modes() -> None:
    mock_config = _base_config()
    mock_config.runtime.use_mock_ai = True
    assert isinstance(main_mod._build_cloud_services(mock_config), MockCloudResponseService)

    realtime_config = _base_config()
    realtime_config.runtime.interaction_backend = "openai_realtime"
    realtime_config.runtime.use_mock_ai = False
    realtime_config.cloud.enabled = True
    realtime_config.cloud.provider_name = "openai"
    realtime_config.cloud.openai_api_key = "test-key"
    assert isinstance(main_mod._build_cloud_services(realtime_config), MockCloudResponseService)

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
    invalid_config.cloud.provider_name = "unsupported"
    with pytest.raises(RuntimeError, match="only the OpenAI cloud provider"):
        main_mod._build_cloud_services(invalid_config)


def test_build_realtime_conversation_service_uses_realtime_config() -> None:
    config = _base_config()
    config.runtime.interaction_backend = "openai_realtime"
    config.runtime.input_mode = "speech"
    config.runtime.use_mock_ai = False
    config.cloud.enabled = True
    config.cloud.provider_name = "openai"
    config.cloud.openai_api_key = "test-key"
    config.cloud.openai_response_model = ""
    config.cloud.openai_realtime_model = "gpt-realtime-test"
    config.cloud.openai_realtime_voice = "echo"
    config.cloud.openai_realtime_audio_sample_rate = 24000
    config.runtime.initial_speech_timeout_seconds = 2.5
    config.runtime.audio_alsa_device = "default"

    service = main_mod._build_realtime_conversation_service(config, build_default_capability_registry())

    assert isinstance(service, RealtimeConversationService)
    assert service.model == "gpt-realtime-test"
    assert service.voice == "echo"
    assert service.turn_detection == "semantic_vad"
    assert service.turn_eagerness == "auto"
    assert service.local_barge_in_enabled is False
    assert service.interrupt_response is False
    assert service.playback_barge_in_enabled is True
    assert service.playback_barge_in_threshold == 1800
    assert service.playback_barge_in_required_ms == 160
    assert service.playback_barge_in_grace_ms == 450
    assert service.playback_barge_in_recent_vad_ms == 1800
    assert service.playback_barge_in_recent_required_ms == 40
    assert service.initial_speech_timeout_seconds == 2.5
    assert service.realtime_sample_rate_hz == 24000
    tool_names = {tool["name"] for tool in service.tools}
    assert {"turn_head", "camera_snapshot"}.issubset(tool_names)


def test_build_ui_service_cover_backend_branches() -> None:
    config = _base_config()
    config.ui.backend = "mock"

    ui = main_mod._build_ui_service(config)

    assert isinstance(ui, MockUiService)
    assert ui.echo_state_to_console is True
    assert ui.echo_text_to_console is True

    config.ui.backend = "browser"
    browser_ui = main_mod._build_ui_service(config)
    assert isinstance(browser_ui, BrowserFaceUiService)


def test_realtime_speech_and_wake_services_cover_error_and_success_paths(monkeypatch, tmp_path: Path) -> None:
    config = _base_config()
    config.paths.data_dir = tmp_path / "data"
    config.runtime.interaction_backend = "openai_realtime"
    config.runtime.input_mode = "speech"
    config.runtime.audio_record_command = ("rec", "{output_path}")
    config.runtime.wake_word_enabled = True
    config.runtime.wake_word_phrase = "Robot"
    config.runtime.wake_word_model = "wake.tflite"

    capture_kwargs = {}
    monkeypatch.setattr(
        main_mod,
        "ShellAudioCaptureService",
        lambda **kwargs: capture_kwargs.update(kwargs)
        or type("Audio", (), {"sample_rate": 16000, "channels": 1, "sample_width": 2})(),
    )
    monkeypatch.setattr(main_mod, "SharedLiveSpeechState", lambda **kwargs: object())
    monkeypatch.setattr(main_mod, "_build_wake_word_service", lambda *args, **kwargs: _FakeWakeWordService())

    wake_word, shared_state = main_mod._build_realtime_speech_services(config)

    assert isinstance(wake_word, _FakeWakeWordService)
    assert shared_state is not None
    assert capture_kwargs["command_template"] == ("rec", "{output_path}")

    config.runtime.input_mode = "manual"
    with pytest.raises(RuntimeError, match="requires speech input mode"):
        main_mod._build_realtime_speech_services(config)


def test_build_realtime_speech_services_passes_session_recording_config(monkeypatch, tmp_path: Path) -> None:
    config = _base_config()
    config.paths.data_dir = tmp_path / "data"
    config.runtime.interaction_backend = "openai_realtime"
    config.runtime.input_mode = "speech"
    config.runtime.audio_record_command = ("rec", "{output_path}")
    config.runtime.audio_save_session_recording = True
    config.runtime.audio_session_recording_dir = Path("recordings")

    shared_kwargs = {}
    monkeypatch.setattr(
        main_mod,
        "ShellAudioCaptureService",
        lambda **kwargs: type("Audio", (), {"sample_rate": 16000, "channels": 1, "sample_width": 2})(),
    )
    monkeypatch.setattr(
        main_mod,
        "SharedLiveSpeechState",
        lambda **kwargs: shared_kwargs.update(kwargs) or object(),
    )
    monkeypatch.setattr(main_mod, "_build_wake_word_service", lambda *args, **kwargs: None)

    _, shared_state = main_mod._build_realtime_speech_services(config)

    assert shared_state is not None
    assert shared_kwargs["session_recording_enabled"] is True
    assert shared_kwargs["session_recording_dir"] == Path.cwd() / "recordings"


def test_wake_word_helper_branches() -> None:
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
    config.runtime.wake_word_phrase = "Robot"
    config.runtime.input_mode = "manual"
    with pytest.raises(RuntimeError, match="speech input mode"):
        main_mod._build_wake_word_service(config, shared_live_state=object())


def test_resolve_runtime_path_and_runtime_logging(tmp_path: Path) -> None:
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


def test_build_application_start_and_stop_warm_cloud(monkeypatch) -> None:
    config = _base_config()
    config.runtime.use_mock_ai = False
    config.cloud.enabled = True
    config.cloud.provider_name = "openai"
    config.cloud.openai_api_key = "test-key"
    config.cloud.openai_response_model = "gpt-5.2"

    fake_cloud = _StartableCloud()

    monkeypatch.setattr(main_mod, "_build_cloud_services", lambda *args, **kwargs: fake_cloud)
    monkeypatch.setattr(main_mod, "_build_ui_service", lambda *args, **kwargs: MockUiService())

    service = main_mod.build_application(config)

    asyncio.run(service.start())
    asyncio.run(service.stop())

    assert fake_cloud.start_calls == 1
    assert fake_cloud.shutdown_calls == 1


def test_orchestrator_stop_closes_shared_live_state_with_wake_word() -> None:
    config = _base_config()
    service = main_mod.build_application(config)
    wake_word = _ShutdownTrackingWakeWord()
    shared_state = _CloseTrackingSharedLiveState()
    service.wake_word = wake_word
    service.shared_live_speech_state = shared_state

    asyncio.run(service.stop())

    assert wake_word.shutdown_calls == 1
    assert shared_state.close_calls == 1


def test_orchestrator_registers_mic_level_updates_without_events(monkeypatch) -> None:
    config = _base_config()
    ui = MockUiService()
    monkeypatch.setattr(main_mod, "_build_ui_service", lambda *args, **kwargs: ui)
    service = main_mod.build_application(config)
    shared_state = _MicLevelSharedLiveState()
    service.shared_live_speech_state = shared_state

    async def run() -> None:
        await service.start()
        assert len(shared_state.listeners) == 1
        shared_state.listeners[0](b"\x00\x30\x00\xd0", 0)
        await asyncio.sleep(0)
        assert ui.mic_levels == []
        service.state.lifecycle = LifecycleStage.LISTENING
        shared_state.listeners[0](b"\x00\x30\x00\xd0", 0)
        await asyncio.sleep(0)
        await service.stop()

    asyncio.run(run())

    assert ui.mic_levels
    assert service.event_history == []
    assert shared_state.listeners == []


def test_main_auto_run_invokes_runtime_branch(monkeypatch, tmp_path: Path) -> None:
    config = _base_config()
    config.runtime.auto_run = True
    config.paths.logs_dir = tmp_path / "logs"
    service = _FakeService()
    calls: list[tuple[str, object | None]] = []

    monkeypatch.setattr(main_mod, "build_application", lambda cfg=None: service)
    monkeypatch.setattr(main_mod, "configure_console_log", lambda path: calls.append(("log", path)))
    monkeypatch.setattr(main_mod, "_configure_runtime_logging", lambda path: calls.append(("runtime_log", path)))
    monkeypatch.setattr(main_mod, "configure_terminal_debug_screen", lambda screen: calls.append(("debug", screen)))

    result = main_mod.main(config)

    assert result == 0
    assert service.run_calls == 1
    assert calls[0][0] == "log"
    assert calls[1][0] == "runtime_log"
    assert calls[2] == ("debug", service.terminal_debug)
    assert calls[-1] == ("debug", None)
