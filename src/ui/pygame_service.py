"""Pygame-backed fullscreen face UI for Raspberry Pi deployments."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import os
from dataclasses import dataclass, field
from typing import Any

from shared.config import UiConfig
from shared.events import Event
from ui.face import FaceController, FaceFrame, FaceTheme, build_face_theme


_WINDOWED_FACE_SIZE = (800, 480)


def _require_pygame():
    pygame = importlib.import_module("pygame")
    return pygame


@dataclass(slots=True)
class PygameFaceUiService:
    """Render a procedural robot face on a fullscreen pygame surface."""

    config: UiConfig
    theme: FaceTheme = field(default_factory=build_face_theme)
    controller: FaceController = field(init=False)
    _render_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _stop_requested: bool = field(default=False, init=False, repr=False)
    _display_blanked: bool = field(default=False, init=False, repr=False)
    _content_mode: str = field(default="face", init=False, repr=False)
    _content_payload: dict[str, object] | None = field(default=None, init=False, repr=False)
    _pygame: Any | None = field(default=None, init=False, repr=False)
    _screen: Any | None = field(default=None, init=False, repr=False)
    _frame_surface: Any | None = field(default=None, init=False, repr=False)
    _clock: Any | None = field(default=None, init=False, repr=False)
    _font: Any | None = field(default=None, init=False, repr=False)
    _sleep_command_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _wake_command_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.controller = FaceController(
            theme=self.theme,
            idle_sleep_seconds=self.config.idle_sleep_seconds,
            sleeping_eyes_grace_seconds=self.config.sleeping_eyes_grace_seconds,
        )

    async def start(self) -> None:
        if self._render_task is not None:
            return
        if self.config.sdl_videodriver:
            os.environ.setdefault("SDL_VIDEODRIVER", self.config.sdl_videodriver)
        pygame = _require_pygame()
        pygame.init()
        pygame.font.init()
        flags = pygame.FULLSCREEN if self.config.fullscreen else 0
        size = (0, 0) if self.config.fullscreen else _WINDOWED_FACE_SIZE
        self._screen = pygame.display.set_mode(size, flags)
        pygame.display.set_caption("AI Companion Robot Face")
        pygame.mouse.set_visible(not self.config.fullscreen)
        self._frame_surface = pygame.Surface(self._screen.get_size()).convert()
        self._clock = pygame.time.Clock()
        self._font = pygame.font.Font(None, 28)
        self._pygame = pygame
        self._stop_requested = False
        # Draw one frame synchronously so fullscreen targets show eyes immediately.
        self._draw_frame(self.controller.update())
        self._render_task = asyncio.create_task(self._render_loop())

    async def shutdown(self) -> None:
        self._stop_requested = True
        if self._render_task is not None:
            await self._render_task
            self._render_task = None
        await self._wait_for_command_task(self._sleep_command_task)
        await self._wait_for_command_task(self._wake_command_task)
        if self._pygame is not None:
            self._pygame.quit()
        self._pygame = None
        self._screen = None
        self._frame_surface = None
        self._clock = None
        self._font = None

    async def render_state(self, lifecycle: str, emotion: str, preview_text: str | None = None) -> None:
        self.controller.render_state(lifecycle, emotion, preview_text)

    async def show_text(self, text: str) -> None:
        if self.config.show_text_overlay:
            self.controller.show_text(text)
        else:
            self.controller.clear_text()

    async def show_content(self, mode: str, payload: dict[str, object] | None = None) -> None:
        self._content_mode = mode
        self._content_payload = payload or {}
        self.controller.show_content(mode)

    async def clear_content(self) -> None:
        self._content_mode = "face"
        self._content_payload = None
        self.controller.clear_content()

    async def handle_event(self, event: Event) -> None:
        self.controller.handle_event(event)

    async def _render_loop(self) -> None:
        assert self._pygame is not None
        while not self._stop_requested:
            frame = self.controller.update()
            await self._sync_display_power(frame)
            self._pump_pygame_events()
            self._draw_frame(frame)
            fps = self._target_fps(frame)
            await asyncio.sleep(max(0.0, 1.0 / fps))

    def _pump_pygame_events(self) -> None:
        assert self._pygame is not None
        for event in self._pygame.event.get():
            if event.type == self._pygame.QUIT:
                self._stop_requested = True

    async def _sync_display_power(self, frame: FaceFrame) -> None:
        if frame.display_sleep_requested and not self._display_blanked:
            self._display_blanked = True
            if self.config.sleep_command:
                self._sleep_command_task = asyncio.create_task(self._run_command(self.config.sleep_command))
        elif not frame.display_sleep_requested and self._display_blanked:
            self._display_blanked = False
            if self.config.wake_command:
                self._wake_command_task = asyncio.create_task(self._run_command(self.config.wake_command))

    async def _run_command(self, command: tuple[str, ...]) -> None:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.wait()

    async def _wait_for_command_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None:
            return
        with contextlib.suppress(Exception):
            await task

    def _target_fps(self, frame: FaceFrame) -> int:
        if frame.scene == "face" and not frame.display_sleep_requested and self.controller.state.lifecycle != "idle":
            return self.config.active_fps
        if self.controller.state.speech_active:
            return self.config.active_fps
        return self.config.idle_fps

    def _draw_frame(self, frame: FaceFrame) -> None:
        assert self._pygame is not None
        assert self._screen is not None
        assert self._frame_surface is not None
        palette = self.theme.palette
        frame_surface = self._frame_surface
        frame_surface.fill(palette.background)

        if frame.display_sleep_requested:
            self._screen.blit(frame_surface, (0, 0))
            self._pygame.display.update()
            return

        real_screen = self._screen
        self._screen = frame_surface
        try:
            if frame.scene in {"face", "sleep"}:
                self._draw_face(frame)
            else:
                self._draw_placeholder_mode(frame)

            if self.config.show_text_overlay and frame.overlay_text:
                self._draw_text_overlay(frame.overlay_text)
        finally:
            self._screen = real_screen
        real_screen.blit(frame_surface, (0, 0))
        self._pygame.display.update()

    def _draw_face(self, frame: FaceFrame) -> None:
        assert self._pygame is not None
        assert self._screen is not None
        width, height = self._screen.get_size()
        geometry = self.theme.geometry
        bob_offset_y = int(frame.pose.bob_y * height)
        eye_width = int(width * geometry.eye_width_ratio * frame.pose.eye_scale_x)
        eye_height = int(height * geometry.eye_height_ratio * frame.pose.eye_scale_y)
        spacing = int(width * geometry.eye_spacing_ratio)
        center_y = (height // 2) + bob_offset_y
        left_center_x = (width // 2) - (spacing // 2) - (eye_width // 2)
        right_center_x = (width // 2) + (spacing // 2) + (eye_width // 2)

        self._draw_eye(
            center_x=left_center_x,
            center_y=center_y,
            eye_width=eye_width,
            eye_height=eye_height,
            openness=frame.pose.openness_left,
            pupil_scale=frame.pose.pupil_scale_left,
            pupil_x=frame.pose.pupil_x_left,
            pupil_y=frame.pose.pupil_y_left,
            brow=frame.pose.brow_left,
            lid_tilt=frame.pose.lid_tilt_left,
            accent_strength=frame.pose.accent_strength,
            highlight_strength=frame.pose.highlight_strength,
        )
        self._draw_eye(
            center_x=right_center_x,
            center_y=center_y,
            eye_width=eye_width,
            eye_height=eye_height,
            openness=frame.pose.openness_right,
            pupil_scale=frame.pose.pupil_scale_right,
            pupil_x=frame.pose.pupil_x_right,
            pupil_y=frame.pose.pupil_y_right,
            brow=frame.pose.brow_right,
            lid_tilt=frame.pose.lid_tilt_right,
            accent_strength=frame.pose.accent_strength,
            highlight_strength=frame.pose.highlight_strength,
        )

    def _draw_eye(
        self,
        *,
        center_x: int,
        center_y: int,
        eye_width: int,
        eye_height: int,
        openness: float,
        pupil_scale: float,
        pupil_x: float,
        pupil_y: float,
        brow: float,
        lid_tilt: float,
        accent_strength: float,
        highlight_strength: float,
    ) -> None:
        assert self._pygame is not None
        assert self._screen is not None
        palette = self.theme.palette
        geometry = self.theme.geometry

        visible_height = max(4, int(eye_height * openness))
        eye_rect = self._pygame.Rect(
            center_x - (eye_width // 2),
            center_y - (visible_height // 2),
            eye_width,
            visible_height,
        )

        if visible_height <= max(6, eye_height // 7):
            start_x = eye_rect.left
            end_x = eye_rect.right
            line_y_left = center_y + int(lid_tilt * eye_height * 0.25)
            line_y_right = center_y - int(lid_tilt * eye_height * 0.25)
            self._pygame.draw.line(
                self._screen,
                palette.eye_outline,
                (start_x, line_y_left),
                (end_x, line_y_right),
                max(2, geometry.outline_width_px - 1),
            )
            return

        self._pygame.draw.ellipse(self._screen, palette.eye_fill, eye_rect)
        self._pygame.draw.ellipse(
            self._screen,
            palette.eye_outline,
            eye_rect,
            geometry.outline_width_px,
        )

        pupil_radius = max(4, int(eye_width * geometry.pupil_radius_ratio * pupil_scale))
        pupil_center_x = center_x + int(pupil_x * (eye_width * 0.22))
        pupil_center_y = center_y + int(pupil_y * (eye_height * 0.22))
        pupil_center_y = max(eye_rect.top + pupil_radius, min(eye_rect.bottom - pupil_radius, pupil_center_y))
        self._pygame.draw.circle(
            self._screen,
            palette.pupil,
            (pupil_center_x, pupil_center_y),
            pupil_radius,
        )

        if highlight_strength > 0.05:
            highlight_radius = max(2, int(eye_width * geometry.highlight_radius_ratio * highlight_strength))
            self._pygame.draw.circle(
                self._screen,
                palette.highlight,
                (
                    pupil_center_x - max(2, pupil_radius // 3),
                    pupil_center_y - max(2, pupil_radius // 3),
                ),
                highlight_radius,
            )

        eyebrow_y = eye_rect.top - max(6, int(eye_height * 0.22))
        eyebrow_delta = int((brow + lid_tilt) * eye_height * 0.28)
        eyebrow_left = (eye_rect.left + 4, eyebrow_y + eyebrow_delta)
        eyebrow_right = (eye_rect.right - 4, eyebrow_y - eyebrow_delta)
        self._pygame.draw.line(
            self._screen,
            palette.accent,
            eyebrow_left,
            eyebrow_right,
            max(2, int(geometry.accent_width_px * max(0.4, accent_strength))),
        )

    def _draw_text_overlay(self, text: str) -> None:
        assert self._pygame is not None
        assert self._screen is not None
        assert self._font is not None
        surface = self._font.render(text, True, self.theme.palette.text)
        padding = 16
        width, height = self._screen.get_size()
        bg_rect = self._pygame.Rect(
            padding,
            height - surface.get_height() - (padding * 2),
            min(width - (padding * 2), surface.get_width() + (padding * 2)),
            surface.get_height() + padding,
        )
        overlay = self._pygame.Surface((bg_rect.width, bg_rect.height), self._pygame.SRCALPHA)
        overlay.fill((4, 12, 14, 190))
        self._screen.blit(overlay, bg_rect.topleft)
        self._screen.blit(surface, (bg_rect.left + padding, bg_rect.top + (padding // 2)))

    def _draw_placeholder_mode(self, frame: FaceFrame) -> None:
        assert self._pygame is not None
        assert self._screen is not None
        assert self._font is not None
        label = f"{frame.scene.title()} mode placeholder"
        surface = self._font.render(label, True, self.theme.palette.text)
        width, height = self._screen.get_size()
        self._screen.blit(
            surface,
            ((width - surface.get_width()) // 2, (height - surface.get_height()) // 2),
        )
