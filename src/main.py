"""Application entry point for the AI companion robot."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import sys

from ai.cloud import (
    MockCloudResponseService,
    OpenAiCloudResponseService,
    OpenAiResponsesClient,
)
from ai.realtime import (
    AlsaRealtimePcmOutput,
    CommandRealtimePcmOutput,
    RealtimeConversationService,
    build_realtime_tool_definitions,
)
from audio.capture import SharedLiveSpeechState, ShellAudioCaptureService
from audio.wake import OpenWakeWordWakeWordService, WakeWordService
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
from shared.models import ComponentName, UserIdentity, VisionDetection
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
    shared_live_state = None
    realtime_conversation = None
    if app_config.runtime.interaction_backend == "openai_realtime":
        wake_word, shared_live_state = _build_realtime_speech_services(
            app_config,
            terminal_debug=terminal_debug,
        )
        realtime_conversation = _build_realtime_conversation_service(app_config, capability_registry)
    else:
        wake_word = None
    cloud_response = _build_cloud_services(app_config)
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
        wake_word=wake_word,
        realtime_conversation=realtime_conversation,
        shared_live_speech_state=shared_live_state,
        terminal_debug=terminal_debug,
    )
    if realtime_conversation is not None:
        realtime_conversation.event_handler = service.handle_event
        realtime_conversation.tool_handler = service.handle_realtime_tool_request
    if hasattr(ui, "handle_event"):
        event_bus.subscribe(ui.handle_event)
    return service


def _build_cloud_services(app_config: AppConfig):
    if (
        app_config.runtime.use_mock_ai
        or not app_config.cloud.enabled
        or app_config.runtime.interaction_backend == "openai_realtime"
    ):
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


def _build_realtime_conversation_service(
    app_config: AppConfig,
    capability_registry,
) -> RealtimeConversationService:
    if app_config.runtime.use_mock_ai or not app_config.cloud.enabled:
        raise RuntimeError("openai_realtime interaction backend requires real OpenAI cloud configuration")
    if (app_config.cloud.provider_name or "").strip().lower() != "openai":
        raise RuntimeError("only the OpenAI cloud provider is currently implemented")

    if sys.platform == "darwin":
        audio_output = CommandRealtimePcmOutput(
            command_template=app_config.runtime.audio_play_command or ("afplay", "{input_path}"),
            sample_rate_hz=app_config.cloud.openai_realtime_audio_sample_rate,
        )
    else:
        audio_output = AlsaRealtimePcmOutput(
            device=app_config.runtime.audio_alsa_device,
            sample_rate_hz=app_config.cloud.openai_realtime_audio_sample_rate,
            period_frames=app_config.runtime.audio_alsa_period_frames,
        )
    tools = build_realtime_tool_definitions(
        capability_registry.list_available(
            {
                ComponentName.ORCHESTRATOR,
                ComponentName.UI,
                ComponentName.MEMORY,
                ComponentName.HARDWARE,
                ComponentName.VISION,
            }
        )
    )
    return RealtimeConversationService(
        api_key=app_config.cloud.openai_api_key,
        base_url=app_config.cloud.openai_realtime_base_url,
        model=app_config.cloud.openai_realtime_model,
        voice=app_config.cloud.openai_realtime_voice,
        turn_detection=app_config.cloud.openai_realtime_turn_detection,
        turn_eagerness=app_config.cloud.openai_realtime_turn_eagerness,
        audio_capture_sample_rate_hz=16000,
        realtime_sample_rate_hz=app_config.cloud.openai_realtime_audio_sample_rate,
        audio_output=audio_output,
        tools=tools,
        follow_up_idle_timeout_seconds=app_config.runtime.follow_up_listen_timeout_seconds,
        local_barge_in_enabled=app_config.cloud.openai_realtime_local_barge_in_enabled,
        interrupt_response=app_config.cloud.openai_realtime_interrupt_response,
        playback_barge_in_enabled=app_config.cloud.openai_realtime_playback_barge_in_enabled,
        playback_barge_in_threshold=app_config.cloud.openai_realtime_playback_barge_in_threshold,
        playback_barge_in_required_ms=app_config.cloud.openai_realtime_playback_barge_in_required_ms,
        playback_barge_in_grace_ms=app_config.cloud.openai_realtime_playback_barge_in_grace_ms,
        playback_barge_in_recent_vad_ms=app_config.cloud.openai_realtime_playback_barge_in_recent_vad_ms,
        playback_barge_in_recent_required_ms=app_config.cloud.openai_realtime_playback_barge_in_recent_required_ms,
    )


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


def _build_realtime_speech_services(
    config: AppConfig,
    *,
    terminal_debug: TerminalDebugScreen | None = None,
) -> tuple[WakeWordService | None, SharedLiveSpeechState]:
    runtime = config.runtime
    if runtime.input_mode != "speech":
        raise RuntimeError("openai_realtime interaction backend requires speech input mode")
    audio_capture = ShellAudioCaptureService(
        command_template=runtime.audio_record_command,
        init_command=runtime.audio_init_command,
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
        session_recording_enabled=runtime.audio_save_session_recording,
        session_recording_dir=_resolve_runtime_path(runtime.audio_session_recording_dir),
    )
    wake_word = _build_wake_word_service(
        config,
        terminal_debug=terminal_debug,
        audio_capture=audio_capture,
        shared_live_state=shared_live_state,
    )
    return wake_word, shared_live_state


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
    if runtime.input_mode != "speech" or runtime.interaction_backend != "openai_realtime":
        raise RuntimeError("wake word support requires speech input mode with openai_realtime")
    if not runtime.wake_word_model.strip():
        raise RuntimeError("wake word support requires runtime.wake_word_model to be configured")

    capture_service = audio_capture or ShellAudioCaptureService(
        command_template=runtime.audio_record_command,
        init_command=runtime.audio_init_command,
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
