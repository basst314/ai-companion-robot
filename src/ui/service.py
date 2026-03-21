"""UI service interface and mock implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class UiService(Protocol):
    """Interface for rendering robot state and text."""

    async def render_state(self, lifecycle: str, emotion: str, preview_text: str | None = None) -> None:
        """Render current state to the face display or debug UI."""

    async def show_text(self, text: str) -> None:
        """Display text for debugging or transcript visibility."""


@dataclass(slots=True)
class MockUiService:
    """Simple console-backed UI adapter used by tests and manual development."""

    rendered_states: list[tuple[str, str, str | None]] = field(default_factory=list)
    visible_text: list[str] = field(default_factory=list)

    async def render_state(self, lifecycle: str, emotion: str, preview_text: str | None = None) -> None:
        self.rendered_states.append((lifecycle, emotion, preview_text))
        print(f"[UI] lifecycle={lifecycle} emotion={emotion} preview={preview_text or ''}")

    async def show_text(self, text: str) -> None:
        self.visible_text.append(text)
        print(f"[UI] text={text}")
