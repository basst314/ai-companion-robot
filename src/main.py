"""Application entry point for the AI companion robot."""

from __future__ import annotations

import asyncio

from ai.cloud import MockCloudAiService
from ai.local import MockLocalAiService
from hardware.service import MockHardwareService
from memory.service import InMemoryMemoryService
from orchestrator.router import RuleBasedIntentRouter
from orchestrator.service import OrchestratorService
from orchestrator.state import OrchestratorState
from shared.config import AppConfig
from shared.models import UserIdentity, VisionDetection
from tts.service import MockTtsService
from ui.service import MockUiService
from vision.service import MockVisionService


def build_application(config: AppConfig | None = None) -> OrchestratorService:
    """Assemble the default mock runtime used during early development."""

    app_config = config or AppConfig()
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
        tts=MockTtsService(),
    )


def main(config: AppConfig | None = None) -> int:
    """Create the application and optionally run the async manual loop."""

    app_config = config or AppConfig()
    service = build_application(app_config)
    if app_config.runtime.auto_run:
        asyncio.run(service.run())
    return 0


if __name__ == "__main__":
    runtime_config = AppConfig()
    runtime_config.runtime.auto_run = True
    runtime_config.runtime.interactive_console = True
    raise SystemExit(main(runtime_config))
