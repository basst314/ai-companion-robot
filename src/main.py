"""Application entry point for the AI companion robot."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ai.cloud import (
    MockCloudResponseService,
    OpenAiCloudResponseService,
    OpenAiResponsesClient,
)
from hardware.service import MockHardwareService
from memory.service import InMemoryMemoryService
from orchestrator.capabilities import build_default_capability_registry
from orchestrator.reactive import ReactivePolicyEngine
from orchestrator.router import LocalTurnDirector
from orchestrator.service import OrchestratorService
from orchestrator.state import OrchestratorState
from shared.console import TerminalDebugScreen, configure_console_log, configure_terminal_debug_screen
from shared.config import AppConfig, load_app_config
from shared.events import EventBus
from shared.models import UserIdentity, VisionDetection
from stt.service import (
    MockSttService,
    OpenWakeWordWakeWordService,
    SharedLiveSpeechState,
    ShellAudioCaptureService,
    SttService,
    WakeWordService,
    WhisperCppSttService,
)
from tts.service import MockTtsService, TtsService, build_piper_tts_service
from ui.browser_service import BrowserFaceUiService
from ui.service import MockUiService, UiService
from vision.service import MockVisionService


def build_application(config: AppConfig | None = None) -> OrchestratorService:
    """Assemble the application runtime from the current configuration."""

    app_config = config or load_app_config()
    capability_registry = build_default_capability_registry()
    memory = InMemoryMemoryService(
        active_user=UserIdentity(
            user_id=app_config.mocks.active_user_id,
            display_name=app_config.mocks.active_user_name,
            preferred_language=app_config.default_language,
            summary=app_config.mocks.active_user_summary,
        )
    )
    vision = MockVisionService(
        detections=[
            VisionDetection(label=name, confidence=0.95, user_id=app_config.mocks.active_user_id)
            for name in app_config.mocks.visible_people
        ]
    )
    event_bus = EventBus()
    terminal_debug = TerminalDebugScreen() if app_config.runtime.interactive_console else None
    stt, wake_word = _build_speech_services(app_config, terminal_debug=terminal_debug)
    cloud_response = _build_cloud_services(app_config)
    tts = _build_tts_service(app_config, terminal_debug=terminal_debug)
    ui = _build_ui_service(app_config, terminal_debug=terminal_debug)
    service = OrchestratorService(
        config=app_config,
        state=OrchestratorState.initial(),
        turn_director=LocalTurnDirector(),
        capability_registry=capability_registry,
        reactive_policy=ReactivePolicyEngine(),
        event_bus=event_bus,
        memory=memory,
        vision=vision,
        ui=ui,
        hardware=MockHardwareService(),
        cloud_response=cloud_response,
        stt=stt,
        wake_word=wake_word,
        tts=tts,
        terminal_debug=terminal_debug,
    )
    if hasattr(tts, "bind_event_handler"):
        tts.bind_event_handler(service.handle_event)
    if hasattr(ui, "handle_event"):
        event_bus.subscribe(ui.handle_event)
    return service


def _build_cloud_services(app_config: AppConfig):
    if app_config.runtime.use_mock_ai or not app_config.cloud.enabled:
        return MockCloudResponseService()

    if (app_config.cloud.provider_name or "").strip().lower() != "openai":
        raise RuntimeError("only the OpenAI cloud provider is currently implemented")

    client = OpenAiResponsesClient(
        api_key=app_config.cloud.openai_api_key,
        base_url=app_config.cloud.openai_base_url,
        timeout_seconds=app_config.cloud.openai_timeout_seconds,
    )
    return OpenAiCloudResponseService(
        client=client,
        model=app_config.cloud.openai_response_model,
        max_output_tokens=app_config.cloud.openai_reply_max_output_tokens,
        wake_word_phrase=app_config.runtime.wake_word_phrase,
    )


def _build_tts_service(
    app_config: AppConfig,
    *,
    terminal_debug: TerminalDebugScreen | None = None,
) -> TtsService:
    if app_config.tts.backend == "mock":
        return MockTtsService(terminal_debug=terminal_debug)
    if app_config.tts.backend == "piper":
        return build_piper_tts_service(
            app_config.tts,
            audio_output_dir=_resolve_runtime_path(app_config.paths.data_dir / "audio" / "tts"),
            terminal_debug=terminal_debug,
        )
    raise RuntimeError("cloud TTS is not implemented yet")


def _build_ui_service(
    app_config: AppConfig,
    *,
    terminal_debug: TerminalDebugScreen | None = None,
) -> UiService:
    if app_config.ui.backend == "browser":
        return BrowserFaceUiService(config=app_config.ui)
    return MockUiService(
        echo_state_to_console=terminal_debug is None,
        echo_text_to_console=True,
    )


def _build_speech_services(
    config: AppConfig,
    *,
    terminal_debug: TerminalDebugScreen | None = None,
) -> tuple[SttService, WakeWordService | None]:
    runtime = config.runtime
    if runtime.stt_backend == "whisper_cpp":
        if runtime.whisper_model_path is None:
            raise RuntimeError("runtime.whisper_model_path must be configured for whisper.cpp STT")

        audio_capture = ShellAudioCaptureService(
            command_template=runtime.audio_record_command,
            output_dir=_resolve_runtime_path(config.paths.data_dir / "audio"),
            input_channels=runtime.audio_input_channels,
            channel_index=runtime.audio_channel_index,
        )
        shared_live_state = SharedLiveSpeechState(
            audio_capture=audio_capture,
            wake_buffer_seconds=max(2.0, runtime.wake_lookback_seconds * 2.0),
            sample_rate=audio_capture.sample_rate,
            channels=audio_capture.channels,
            sample_width=audio_capture.sample_width,
        )
        stt = WhisperCppSttService(
            audio_capture=audio_capture,
            model_path=runtime.whisper_model_path,
            binary_path=runtime.whisper_binary_path,
            whisper_transport=runtime.whisper_transport,
            whisper_server_base_url=runtime.whisper_server_base_url,
            whisper_server_mode=runtime.whisper_server_mode,
            command_extra_args=runtime.whisper_command_extra_args,
            language_mode=runtime.language_mode,
            partial_transcripts_enabled=runtime.partial_transcripts_enabled,
            speech_silence_seconds=runtime.speech_silence_seconds,
            vad_threshold=runtime.vad_threshold,
            vad_frame_ms=runtime.vad_frame_ms,
            vad_start_trigger_frames=runtime.vad_start_trigger_frames,
            vad_end_trigger_frames=runtime.vad_end_trigger_frames,
            max_recording_seconds=runtime.max_recording_seconds,
            utterance_finalize_timeout_seconds=runtime.utterance_finalize_timeout_seconds,
            utterance_tail_stable_polls=runtime.utterance_tail_stable_polls,
            follow_up_listen_timeout_seconds=runtime.follow_up_listen_timeout_seconds,
            ring_debug_wake_window_seconds=runtime.wake_lookback_seconds,
            terminal_debug=terminal_debug,
            shared_live_state=shared_live_state,
            **_speech_latency_kwargs(runtime),
        )
        stt.ensure_endpoint_vad_ready()
        wake_word = _build_wake_word_service(
            config,
            terminal_debug=terminal_debug,
            audio_capture=audio_capture,
            shared_live_state=shared_live_state,
        )
        return stt, wake_word

    return MockSttService(utterances=runtime.manual_inputs), _build_wake_word_service(
        config,
        terminal_debug=terminal_debug,
    )


def _speech_latency_kwargs(runtime) -> dict[str, float]:
    if runtime.speech_latency_profile == "balanced":
        return {
            "poll_interval_seconds": 0.35,
            "minimum_transcribe_seconds": 0.45,
            "partial_update_interval_seconds": 1.0,
            "minimum_utterance_seconds": 2.0,
            "partial_snapshot_max_seconds": 4.0,
            "utterance_end_grace_seconds": 0.25,
        }
    return {
        "poll_interval_seconds": 0.10,
        "minimum_transcribe_seconds": 0.20,
        "partial_update_interval_seconds": 0.20,
        "minimum_utterance_seconds": 0.80,
        "partial_snapshot_max_seconds": 3.0,
        "utterance_end_grace_seconds": 0.05,
    }


def _build_wake_word_service(
    config: AppConfig,
    *,
    terminal_debug: TerminalDebugScreen | None = None,
    audio_capture: ShellAudioCaptureService | None = None,
    shared_live_state: SharedLiveSpeechState | None = None,
) -> WakeWordService | None:
    runtime = config.runtime
    if not runtime.wake_word_enabled or not runtime.wake_word_phrase.strip():
        if terminal_debug is not None:
            terminal_debug.update_wake_status("off", "--")
        return None
    if runtime.input_mode != "speech" or runtime.stt_backend != "whisper_cpp":
        raise RuntimeError("wake word support requires speech input mode with whisper_cpp STT")
    if not runtime.wake_word_model.strip():
        raise RuntimeError("wake word support requires runtime.wake_word_model to be configured")

    capture_service = audio_capture or ShellAudioCaptureService(
        command_template=runtime.audio_record_command,
        output_dir=_resolve_runtime_path(config.paths.data_dir / "audio"),
        input_channels=runtime.audio_input_channels,
        channel_index=runtime.audio_channel_index,
    )
    if shared_live_state is None:
        raise RuntimeError("wake word support requires a shared live speech state")
    if terminal_debug is not None:
        terminal_debug.update_wake_status("listening", runtime.wake_word_phrase)
    return OpenWakeWordWakeWordService(
        audio_capture=capture_service,
        wake_phrase=runtime.wake_word_phrase,
        wake_word_model=runtime.wake_word_model,
        wake_threshold=runtime.wake_word_threshold,
        wake_lookback_seconds=runtime.wake_lookback_seconds,
        terminal_debug=terminal_debug,
        shared_live_state=shared_live_state,
    )


def _configure_runtime_logging(log_path: Path) -> None:
    """Mirror runtime logs to a file for manual debugging sessions."""

    root_logger = logging.getLogger()
    if any(
        isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == log_path
        for handler in root_logger.handlers
    ):
        return

    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root_logger.addHandler(file_handler)
    root_logger.setLevel(logging.INFO)


def _resolve_runtime_path(path: Path) -> Path:
    """Resolve app-relative paths from the current working directory."""

    return path if path.is_absolute() else Path.cwd() / path


def main(config: AppConfig | None = None) -> int:
    """Create the application and optionally run the async manual loop."""

    app_config = config or load_app_config()
    service = build_application(app_config)
    if app_config.runtime.auto_run:
        log_path = _resolve_runtime_path(app_config.paths.logs_dir / "interactive-console.log")
        configure_console_log(log_path)
        _configure_runtime_logging(log_path)
        try:
            configure_terminal_debug_screen(service.terminal_debug)
            asyncio.run(service.run())
        finally:
            configure_terminal_debug_screen(None)
    return 0


if __name__ == "__main__":
    runtime_config = load_app_config()
    runtime_config.runtime.auto_run = True
    raise SystemExit(main(runtime_config))
