"""UI service interface and mock implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from shared.console import ConsoleFormatter


class UiService(Protocol):
    """Interface for rendering robot-facing state and text."""

    async def render_state(self, lifecycle: str, emotion: str, preview_text: str | None = None) -> None:
        """Render current state to the face display or debug UI."""

    async def show_text(self, text: str) -> None:
        """Display text for debugging or transcript visibility."""


@dataclass(slots=True)
class MockUiService:
    """Simple console-backed UI adapter used by tests and manual development."""

    rendered_states: list[tuple[str, str, str | None]] = field(default_factory=list)
    visible_text: list[str] = field(default_factory=list)
    echo_state_to_console: bool = True
    echo_text_to_console: bool = True

    async def render_state(self, lifecycle: str, emotion: str, preview_text: str | None = None) -> None:
        self.rendered_states.append((lifecycle, emotion, preview_text))
        if not self.echo_state_to_console:
            return
        formatter = ConsoleFormatter()
        plain = f"[UI] lifecycle={lifecycle} emotion={emotion} preview={preview_text or ''}"
        formatter.emit(
            formatter.stamp(
                f"{formatter.ui_label('[UI]')} lifecycle={lifecycle} emotion={emotion} preview={preview_text or ''}"
            ),
            plain_text=formatter.stamp(plain),
        )

    async def show_text(self, text: str) -> None:
        self.visible_text.append(text)
        if not self.echo_text_to_console:
            return
        formatter = ConsoleFormatter()
        plain = formatter.stamp(f"[UI] text={text}")
        formatter.emit(
            formatter.stamp(f"{formatter.ui_label('[UI]')} text={formatter.response(text)}"),
            plain_text=plain,
        )
