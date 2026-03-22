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
from shared.console import configure_console_log
from shared.config import AppConfig, load_app_config
from shared.models import UserIdentity, VisionDetection
from stt.service import MockSttService, ShellAudioCaptureService, SttService, WhisperCppSttService
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
    stt = _build_stt_service(app_config)
    return OrchestratorService(
        config=app_config,
        state=OrchestratorState.initial(),
        router=RuleBasedIntentRouter(),
        memory=memory,
        vision=vision,
        ui=MockUiService(),
        hardware=MockHardwareService(),
        local_ai=MockLocalAiService(),
        cloud_ai=MockCloudAiService(),
        stt=stt,
        tts=MockTtsService(),
    )


def _build_stt_service(config: AppConfig) -> SttService:
    runtime = config.runtime
    if runtime.stt_backend == "whisper_cpp":
        if runtime.whisper_model_path is None:
            raise RuntimeError("runtime.whisper_model_path must be configured for whisper.cpp STT")

        audio_capture = ShellAudioCaptureService(
            command_template=runtime.audio_record_command,
            output_dir=_resolve_runtime_path(config.paths.data_dir / "audio"),
        )
        return WhisperCppSttService(
            audio_capture=audio_capture,
            model_path=runtime.whisper_model_path,
            binary_path=runtime.whisper_binary_path,
            language_mode=runtime.language_mode,
            speech_silence_seconds=runtime.speech_silence_seconds,
        )

    return MockSttService(utterances=runtime.manual_inputs)


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
        asyncio.run(service.run())
    return 0


if __name__ == "__main__":
    runtime_config = load_app_config()
    runtime_config.runtime.auto_run = True
    runtime_config.runtime.interactive_console = True
    raise SystemExit(main(runtime_config))
