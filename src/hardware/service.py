"""Hardware service interface and mock implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from shared.models import ActionRequest, ActionResult


class HardwareService(Protocol):
    """Interface for robot actions such as eye and head movement."""

    async def execute_action(self, request: ActionRequest) -> ActionResult:
        """Execute a hardware-facing action."""


@dataclass(slots=True)
class MockHardwareService:
    """Mock hardware adapter with a small internal robot state."""

    eyes_open: bool = False
    head_direction: str = "center"
    executed_actions: list[ActionRequest] = field(default_factory=list)
    should_fail: bool = False

    async def execute_action(self, request: ActionRequest) -> ActionResult:
        if self.should_fail:
            raise RuntimeError("mock hardware failure")

        self.executed_actions.append(request)

        if request.name == "open_eyes":
            self.eyes_open = True
            return ActionResult(True, "Opening my eyes now.", {"eyes_open": True})
        if request.name == "close_eyes":
            self.eyes_open = False
            return ActionResult(True, "Closing my eyes now.", {"eyes_open": False})
        if request.name == "look_at_user":
            self.head_direction = "user"
            return ActionResult(True, "I am looking at you now.", {"head_direction": "user"})
        if request.name == "turn_head":
            direction = str(request.arguments.get("direction", "center"))
            self.head_direction = direction
            return ActionResult(
                True,
                f"Turning my head {direction}.",
                {"head_direction": direction},
            )

        return ActionResult(False, f"I do not know how to {request.name}.")
