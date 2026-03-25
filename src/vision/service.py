"""Vision service interface and mock implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from shared.models import VisionDetection, VisionSnapshot


class VisionService(Protocol):
    """Interface for current perception state."""

    async def get_current_detections(self) -> tuple[VisionDetection, ...]:
        """Return the latest known detections."""

    async def capture_snapshot(self) -> VisionSnapshot:
        """Capture a current camera snapshot for multimodal cloud turns."""


@dataclass(slots=True)
class MockVisionService:
    """Mock vision adapter exposing deterministic detections."""

    detections: list[VisionDetection] = field(default_factory=list)
    snapshot: VisionSnapshot | None = None
    should_fail: bool = False

    async def get_current_detections(self) -> tuple[VisionDetection, ...]:
        if self.should_fail:
            raise RuntimeError("mock vision failure")

        return tuple(self.detections)

    async def capture_snapshot(self) -> VisionSnapshot:
        if self.should_fail:
            raise RuntimeError("mock vision failure")

        if self.snapshot is not None:
            return self.snapshot

        labels = ", ".join(detection.label for detection in self.detections) or "nothing obvious"
        return VisionSnapshot(
            image_url=(
                "data:image/gif;base64,"
                "R0lGODlhAQABAIABAP///wAAACwAAAAAAQABAAACAkQBADs="
            ),
            mime_type="image/gif",
            summary=f"Mock camera snapshot with {labels}.",
        )
