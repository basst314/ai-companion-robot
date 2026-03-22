"""Application entry point for the AI companion robot."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ai.cloud import MockCloudAiService
from ai.local import MockLocalAiService
from hardware.service import MockHardwareService
from memory.service import InMemoryMemoryService
from orchestrator.router import RuleBasedIntentRouter
from orchestrator.service import OrchestratorService
from orchestrator.state import OrchestratorState
from shared.console import TerminalDebugScreen, configure_console_log, configure_terminal_debug_screen
from shared.config import AppConfig, load_app_config
from shared.models import UserIdentity, VisionDetection
from stt.service import (
    MockSttService,
    SharedLiveSpeechState,
    ShellAudioCaptureService,
    SttService,
    WakeWordService,
    WhisperCppSttService,
    WhisperCppWakeWordService,
)
from tts.service import MockTtsService
from ui.service import MockUiService
from vision.service import MockVisionService


def build_application(config: AppConfig | None = None) -> OrchestratorService:
    """Assemble the default mock runtime used during early development."""

    app_config = config or load_app_config()
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
    terminal_debug = TerminalDebugScreen() if app_config.runtime.interactive_console else None
    stt, wake_word = _build_speech_services(app_config, terminal_debug=terminal_debug)
    return OrchestratorService(
        config=app_config,
        state=OrchestratorState.initial(),
        router=RuleBasedIntentRouter(),
        memory=memory,
        vision=vision,
        ui=MockUiService(
            echo_state_to_console=terminal_debug is None,
            echo_text_to_console=True,
        ),
        hardware=MockHardwareService(),
        local_ai=MockLocalAiService(),
        cloud_ai=MockCloudAiService(),
        stt=stt,
        wake_word=wake_word,
        tts=MockTtsService(),
        terminal_debug=terminal_debug,
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
        )
        shared_live_state = SharedLiveSpeechState(
            audio_capture=audio_capture,
            wake_buffer_seconds=max(runtime.wake_window_seconds * 2.0, runtime.wake_window_seconds + runtime.wake_stride_seconds),
            sample_rate=audio_capture.sample_rate,
            channels=audio_capture.channels,
            sample_width=audio_capture.sample_width,
        )
        stt = WhisperCppSttService(
            audio_capture=audio_capture,
            model_path=runtime.whisper_model_path,
            binary_path=runtime.whisper_binary_path,
            language_mode=runtime.language_mode,
            speech_silence_seconds=runtime.speech_silence_seconds,
            utterance_finalize_timeout_seconds=runtime.utterance_finalize_timeout_seconds,
            utterance_tail_stable_polls=runtime.utterance_tail_stable_polls,
            ring_debug_wake_window_seconds=runtime.wake_window_seconds,
            ring_debug_stride_seconds=runtime.wake_stride_seconds,
            terminal_debug=terminal_debug,
            shared_live_state=shared_live_state,
        )
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
    if runtime.stt_backend != "whisper_cpp" or runtime.whisper_model_path is None:
        raise RuntimeError("wake word support requires whisper_cpp STT and a configured model path")

    capture_service = audio_capture or ShellAudioCaptureService(
        command_template=runtime.audio_record_command,
        output_dir=_resolve_runtime_path(config.paths.data_dir / "audio"),
    )
    if terminal_debug is not None:
        terminal_debug.update_wake_status("listening", runtime.wake_word_phrase)
    return WhisperCppWakeWordService(
        audio_capture=capture_service,
        model_path=runtime.whisper_model_path,
        binary_path=runtime.whisper_binary_path,
        language_mode=runtime.language_mode,
        wake_phrase=runtime.wake_word_phrase,
        wake_window_seconds=runtime.wake_window_seconds,
        wake_stride_seconds=runtime.wake_stride_seconds,
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
    runtime_config.runtime.interactive_console = True
    raise SystemExit(main(runtime_config))
