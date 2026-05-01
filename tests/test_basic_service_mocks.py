"""Tests for the small mock adapter services."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ai.local import MockLocalAiService
from hardware.service import MockHardwareService
from memory.service import InMemoryMemoryService
from shared.models import (
    ActionRequest,
    EmotionState,
    InteractionContext,
    InteractionRecord,
    Language,
    RouteKind,
    RobotStateSnapshot,
    Transcript,
    UserIdentity,
    VisionDetection,
    VisionSnapshot,
)
from vision.service import MockVisionService


def test_mock_hardware_service_updates_state_for_supported_actions() -> None:
    service = MockHardwareService()

    async def run() -> None:
        assert (await service.execute_action(ActionRequest(name="open_eyes"))).success is True
        assert (await service.execute_action(ActionRequest(name="close_eyes"))).success is True
        assert (await service.execute_action(ActionRequest(name="look_at_user"))).success is True
        assert (
            await service.execute_action(ActionRequest(name="turn_head", arguments={"direction": "left"}))
        ).message == "Turning my head left."
        result = await service.execute_action(ActionRequest(name="dance"))
        assert result.success is False
        assert service.executed_actions[-1].name == "dance"

    asyncio.run(run())


def test_mock_hardware_service_can_fail() -> None:
    service = MockHardwareService(should_fail=True)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="mock hardware failure"):
            await service.execute_action(ActionRequest(name="open_eyes"))

    asyncio.run(run())


def test_in_memory_memory_service_tracks_history_and_user_summary() -> None:
    user = UserIdentity(user_id="u1", display_name="Builder", summary="You are the robot builder.")
    service = InMemoryMemoryService(active_user=user)

    async def run() -> None:
        assert await service.get_active_user() == user
        assert await service.get_user_summary("u1") == "You are the robot builder."
        assert await service.get_user_summary("someone-else") == "I do not know much about you yet."
        await service.save_interaction(
            InteractionRecord(
                user_text="hello",
                assistant_text="hi",
                language=Language.ENGLISH,
                timestamp=datetime.now(UTC),
                route_kind=RouteKind.LOCAL_QUERY,
            )
        )
        await service.save_interaction(
            InteractionRecord(
                user_text="how are you?",
                assistant_text="good",
                language=Language.ENGLISH,
                timestamp=datetime.now(UTC),
                route_kind=RouteKind.LOCAL_QUERY,
            )
        )
        assert len(await service.get_recent_history(limit=1)) == 1
        assert len(await service.get_recent_history(limit=5)) == 2

    asyncio.run(run())


def test_mock_vision_service_returns_detections_and_snapshot() -> None:
    detection = VisionDetection(label="Builder", confidence=0.98, user_id="u1")
    service = MockVisionService(detections=[detection])

    async def run() -> None:
        assert await service.get_current_detections() == (detection,)
        snapshot = await service.capture_snapshot()
        assert snapshot.mime_type == "image/gif"
        assert "Builder" in snapshot.summary
        service.snapshot = VisionSnapshot(image_url="data:image/png;base64,AAA", mime_type="image/png", summary="custom")
        assert await service.capture_snapshot() == service.snapshot

    asyncio.run(run())


def test_mock_vision_service_can_fail() -> None:
    service = MockVisionService(should_fail=True)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="mock vision failure"):
            await service.get_current_detections()

    asyncio.run(run())


def test_mock_local_ai_service_uses_active_user_context() -> None:
    service = MockLocalAiService()

    async def run() -> None:
        with_user = await service.generate_reply(
            Transcript(text="hello", language=Language.ENGLISH, confidence=1.0, is_final=True),
            InteractionContext(
                active_user=UserIdentity(user_id="u1", display_name="Builder"),
                recent_history=(),
                current_detections=(),
                robot_state=RobotStateSnapshot(
                    lifecycle="idle",
                    emotion=EmotionState.NEUTRAL,
                    eyes_open=True,
                    head_direction="center",
                ),
            ),
        )
        without_user = await service.generate_reply(
            Transcript(text="hello", language=Language.ENGLISH, confidence=1.0, is_final=True),
            InteractionContext(
                active_user=None,
                recent_history=(),
                current_detections=(),
                robot_state=RobotStateSnapshot(
                    lifecycle="idle",
                    emotion=EmotionState.NEUTRAL,
                    eyes_open=True,
                    head_direction="center",
                ),
            ),
        )

        assert with_user.intent == "local_reasoning"
        assert "Builder" in with_user.text
        assert "friend" in without_user.text
        assert with_user.emotion is EmotionState.CURIOUS

    asyncio.run(run())
