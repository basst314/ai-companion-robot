"""Vision service interface and mock implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from shared.models import VisionDetection


class VisionService(Protocol):
    """Interface for current perception state."""

    async def get_current_detections(self) -> tuple[VisionDetection, ...]:
        """Return the latest known detections."""


@dataclass(slots=True)
class MockVisionService:
    """Mock vision adapter exposing deterministic detections."""

    detections: list[VisionDetection] = field(default_factory=list)
    should_fail: bool = False

    async def get_current_detections(self) -> tuple[VisionDetection, ...]:
        if self.should_fail:
            raise RuntimeError("mock vision failure")

        return tuple(self.detections)
